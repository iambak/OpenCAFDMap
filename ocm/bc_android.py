r"""BimmerCode Android backups (<VIN>_<ECU>.dat): Java serialization of BimmerCode's coding session.

The app's class and field names are obfuscated (de.appomotive.bimmercode.k.b, fields b..f) and can change between
app versions, so the session is recognised by the types of its fields, not their names:
  String (17 chars)         VIN
  String                    BimmerCode's ECU id (the <ECU> part of the file name)
  List of objects, 4 ints   CAFDs: id, main, sub, patch version (first = BimmerCode's primary CAFD)
  object with a List of     coding blocks: (Integer block address, byte[] data)
  java.util.Date            when the backup was made

Java writes a byte[] as a length followed by the raw bytes, so a block's coding data sits in the file as is.
Edits overwrite those bytes in place (same length); everything else, the SYSTEM (signature) block included,
stays byte-identical to the source backup.
"""
import datetime
import os
import re
import struct

MAGIC = b"\xac\xed\x00\x05"
BASE = 0x7E0000
PRIM = {"B": (">b", 1), "C": (">H", 2), "D": (">d", 8), "F": (">f", 4), "I": (">i", 4), "J": (">q", 8),
        "S": (">h", 2), "Z": (">?", 1)}
FN_RE = re.compile(r"^(?P<vin>[A-Z0-9]{17})_(?P<ecu>[0-9A-F]+)\.dat$", re.I)
VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")


class JObject:
    def __init__(self, cls):
        self.cls, self.fields, self.extra = cls, {}, []   # extra: data written by a custom writeObject


class JBytes(bytes):
    """byte[] with its file offset."""
    off = 0


class JDate:
    def __init__(self, ms):
        self.ms = ms


class Reader:
    """ObjectInputStream for the subset Java's own classes and BimmerCode's session use."""

    def __init__(self, raw):
        if raw[:4] != MAGIC:
            raise ValueError("not a Java serialization stream")
        self.b, self.p, self.handles = raw, 4, []

    def take(self, n):
        if self.p + n > len(self.b):
            raise ValueError("truncated stream")
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def num(self, fmt, n):
        return struct.unpack(fmt, self.take(n))[0]

    def utf(self):
        return self.take(self.num(">H", 2)).decode("utf-8", "replace")

    def handle(self, o):
        self.handles.append(o)
        return len(self.handles) - 1

    def ref(self):
        return self.handles[self.num(">I", 4) - BASE]

    def content(self):
        t = self.num("B", 1)
        if t == 0x70:                       # TC_NULL
            return None
        if t == 0x71:                       # TC_REFERENCE
            return self.ref()
        if t == 0x74:                       # TC_STRING
            s = self.utf()
            self.handle(s)
            return s
        if t == 0x7C:                       # TC_LONGSTRING
            s = self.take(self.num(">Q", 8)).decode("utf-8", "replace")
            self.handle(s)
            return s
        if t == 0x73:                       # TC_OBJECT
            return self.obj()
        if t == 0x75:                       # TC_ARRAY
            cd = self.classdesc()
            h = self.handle(None)
            n = self.num(">i", 4)
            et = cd["name"][1]
            if et == "B":
                off = self.p
                v = JBytes(self.take(n))
                v.off = off
            elif et in PRIM:
                f, s = PRIM[et]
                v = [self.num(f, s) for _ in range(n)]
            else:
                v = [self.content() for _ in range(n)]
            self.handles[h] = v
            return v
        if t == 0x7E:                       # TC_ENUM
            cd = self.classdesc()
            h = self.handle(None)
            self.handles[h] = (cd["name"], self.content())
            return self.handles[h]
        if t == 0x76:                       # TC_CLASS
            cd = self.classdesc()
            self.handle(cd)
            return cd
        if t == 0x77:                       # TC_BLOCKDATA
            return ("block", self.take(self.num("B", 1)))
        if t == 0x7A:                       # TC_BLOCKDATALONG
            return ("block", self.take(self.num(">I", 4)))
        raise ValueError(f"unsupported tag 0x{t:02X} at offset {self.p - 1}")

    def classdesc(self):
        t = self.num("B", 1)
        if t == 0x70:
            return None
        if t == 0x71:
            return self.ref()
        if t != 0x72:
            raise ValueError(f"expected a class description at offset {self.p - 1}")
        cd = {"name": self.utf(), "uid": self.num(">q", 8)}
        self.handle(cd)
        cd["flags"] = self.num("B", 1)
        cd["fields"] = []
        for _ in range(self.num(">H", 2)):
            tc = chr(self.num("B", 1))
            name = self.utf()
            cd["fields"].append((tc, name, self.content() if tc in "L[" else tc))
        self.annotations()
        cd["super"] = self.classdesc()
        return cd

    def annotations(self):
        out = []
        while self.b[self.p] != 0x78:      # TC_ENDBLOCKDATA
            out.append(self.content())
        self.p += 1
        return out

    def obj(self):
        cd = self.classdesc()
        o = JObject(cd["name"])
        self.handle(o)
        chain = []
        while cd:
            chain.append(cd)
            cd = cd["super"]
        for c in reversed(chain):
            for tc, name, _sig in c["fields"]:
                if tc in PRIM:
                    f, s = PRIM[tc]
                    o.fields[name] = self.num(f, s)
                else:
                    o.fields[name] = self.content()
            if c["flags"] & 0x01:           # SC_WRITE_METHOD
                o.extra += self.annotations()
        if o.cls == "java.util.Date":
            blk = next((x[1] for x in o.extra if isinstance(x, tuple) and x[0] == "block" and len(x[1]) == 8), None)
            return JDate(struct.unpack(">q", blk)[0]) if blk else o
        if o.cls == "java.lang.Integer":
            return o.fields.get("value")
        return o


def items(o):
    """Elements of a serialized java.util.ArrayList (written by its writeObject)."""
    return [x for x in o.extra if not (isinstance(x, tuple) and x and x[0] == "block")] \
        if isinstance(o, JObject) and o.cls.startswith("java.util.") else []


def parse(raw):
    r = Reader(raw)
    top = r.content()
    if r.p != len(raw):
        raise ValueError(f"{len(raw) - r.p} unexpected trailing bytes")
    return top


class Dat:
    """Same interface as bc_tool.Cod: vin, ecu_id, diag, cafd, ver, cafds, block_obj, blocks, ts, when(), save_as()."""
    platform = "Android"

    def __init__(self, path):
        self.path = path
        raw = open(path, "rb").read()
        top = parse(raw)
        if not isinstance(top, JObject) or not top.cls.startswith("de.appomotive.bimmercode."):
            raise ValueError("not a BimmerCode backup")
        strings = [v for v in top.fields.values() if isinstance(v, str)]
        vins = [s for s in strings if VIN_RE.fullmatch(s)]
        m = FN_RE.match(os.path.basename(path))
        self.vin = vins[0] if vins else (m.group("vin") if m else "")
        others = [s for s in strings if s != self.vin]
        self.ecu_id = others[0] if others else (m.group("ecu") if m else "")
        self.diag = None                    # Android backups don't store the diagnostic address
        self.cafds, self._blocks = [], {}
        dates = [v for v in top.fields.values() if isinstance(v, JDate)]
        self.ts = dates[0].ms // 1000 if dates else 0
        for v in top.fields.values():
            for x in items(v):
                ints = [y for y in x.fields.values() if isinstance(y, int) and not isinstance(y, bool)] \
                    if isinstance(x, JObject) else []
                if len(ints) == 4 and len(x.fields) == 4:
                    cv = ("%08X" % ints[0], "%03d_%03d_%03d" % tuple(ints[1:]))
                    if cv not in self.cafds:
                        self.cafds.append(cv)
            if isinstance(v, JObject):
                for lst in v.fields.values():
                    for x in items(lst):
                        if isinstance(x, JObject):
                            addr = [y for y in x.fields.values() if isinstance(y, int)]
                            data = [y for y in x.fields.values() if isinstance(y, JBytes)]
                            if len(addr) == 1 and len(data) == 1:
                                self._blocks["%04X" % addr[0]] = data[0]
        if not self.cafds or not self._blocks:
            raise ValueError("BimmerCode backup without CAFD or coding blocks (unknown app version?)")
        self.cafd, self.ver = self.cafds[0]
        self.block_obj = {g: g for g in self._blocks}

    @property
    def blocks(self):
        return {g: bytearray(b) for g, b in self._blocks.items()}

    def when(self):
        return datetime.datetime.fromtimestamp(self.ts).strftime("%Y-%m-%d %H:%M") if self.ts else "?"

    def save_as(self, path, blocks):
        """Copy the source and overwrite only the edited coding bytes in place; then re-read it and check that
        nothing but those bytes differs."""
        raw = bytearray(open(self.path, "rb").read())
        for g, data in blocks.items():
            old = self._blocks[g]
            if len(data) != len(old):
                raise ValueError(f"block {g} changed length")
            assert raw[old.off:old.off + len(old)] == old, f"block {g}: data not where expected"
            raw[old.off:old.off + len(old)] = data
        with open(path, "wb") as f:
            f.write(raw)
        new = Dat(path)
        want = {g: bytes(blocks.get(g, b)) for g, b in self._blocks.items()}
        if {g: bytes(b) for g, b in new._blocks.items()} != want or (new.vin, new.ecu_id, new.cafds, new.ts) != \
                (self.vin, self.ecu_id, self.cafds, self.ts):
            os.remove(path)
            raise RuntimeError("patched backup does not round-trip; nothing written")
