"""Synthetic BimmerCode backups and a decrypted CAFD for the regression tests. Everything is made up (VIN, ECU,
coding bytes): no real backup or psdzdata file is ever committed."""
import gzip
import plistlib
import struct

VIN = "WBATEST0000000001"          # 17 chars, not a real car


# ------------------------------------------------------------------ Android .dat (Java serialization)
class _J:
    """Minimal ObjectOutputStream writer for the shapes BimmerCode for Android writes."""

    def __init__(self):
        self.b = bytearray(b"\xac\xed\x00\x05")

    def u(self, fmt, *v):
        self.b += struct.pack(fmt, *v)

    def utf(self, s):
        e = s.encode("utf-8")
        self.u(">H", len(e))
        self.b += e

    def string(self, s):
        self.u("B", 0x74)
        self.utf(s)

    def classdesc(self, name, uid, flags, fields=(), sup=None):
        """fields: [(typecode, name, class signature for L/[)]; sup: callable writing the super classdesc."""
        self.u("B", 0x72)
        self.utf(name)
        self.u(">qB", uid, flags)
        self.u(">H", len(fields))
        for tc, fname, sig in fields:
            self.u("B", ord(tc))
            self.utf(fname)
            if tc in "L[":
                self.string(sig)
        self.u("B", 0x78)                       # no class annotations
        if sup:
            sup()
        else:
            self.u("B", 0x70)

    def integer(self, v):
        self.u("B", 0x73)
        self.classdesc("java.lang.Integer", 1360826667806852920, 0x02, [("I", "value", None)],
                       sup=lambda: self.classdesc("java.lang.Number", -8742448824652078965, 0x02))
        self.u(">i", v)

    def bytes_(self, data):
        self.u("B", 0x75)
        self.classdesc("[B", -5984413125824719648, 0x02)
        self.u(">i", len(data))
        self.b += data

    def arraylist(self, write_items):
        items = list(write_items)
        self.u("B", 0x73)
        self.classdesc("java.util.ArrayList", 8683452581122892189, 0x03, [("I", "size", None)])
        self.u(">i", len(items))
        self.u("BB", 0x77, 4)
        self.u(">i", len(items))
        for w in items:
            w()
        self.u("B", 0x78)

    def date(self, ms):
        self.u("B", 0x73)
        self.classdesc("java.util.Date", 7523967970034938905, 0x03)
        self.u("BB", 0x77, 8)
        self.u(">q", ms)
        self.u("B", 0x78)


def write_dat(path, ecu="0F2330", cafds=(("00001EF6", 6, 64, 13),), blocks=None, ms=1786000000000,
              field_names="bcdef"):
    """A BimmerCode for Android backup. field_names: the app's obfuscated field names (they change between app
    versions; the reader must not depend on them)."""
    blocks = blocks or {0x3000: bytes([0x00, 0x11, 0x22, 0x33]), 0x3001: bytes([0x44, 0x55]),
                        0x3002: bytes(range(0x80, 0x80 + 32))}
    fb, fc, fd, fe, ff = field_names
    j = _J()
    j.u("B", 0x73)
    j.classdesc("de.appomotive.bimmercode.k.b", 0x6eaa2c273a250040, 0x02, [
        ("L", fb, "Ljava/lang/String;"), ("L", fc, "Ljava/lang/String;"), ("L", fd, "Ljava/util/ArrayList;"),
        ("L", fe, "Lde/appomotive/bimmercode/k/a;"), ("L", ff, "Ljava/util/Date;")])
    j.string(VIN)
    j.string(ecu)

    def cafd(c):
        def w():
            j.u("B", 0x73)
            j.classdesc("de.appomotive.bimmercode.k.v", 0x12, 0x02, [("I", "a", None), ("I", "b", None),
                                                                     ("I", "c", None), ("I", "d", None)])
            j.u(">iiii", int(c[0], 16), c[1], c[2], c[3])
        return w

    j.arraylist(cafd(c) for c in cafds)
    j.u("B", 0x73)
    j.classdesc("de.appomotive.bimmercode.k.a", 0x13, 0x02, [("L", "b", "Ljava/util/ArrayList;")])

    def block(addr, data):
        def w():
            j.u("B", 0x73)
            j.classdesc("de.appomotive.bimmercode.k.j", 0x14, 0x02, [("L", "a", "Ljava/lang/Integer;"),
                                                                     ("[", "b", "[B")])
            j.integer(addr)
            j.bytes_(data)
        return w

    j.arraylist(block(a, d) for a, d in sorted(blocks.items()))
    j.date(ms)
    with open(path, "wb") as f:
        f.write(bytes(j.b))
    return path


# ------------------------------------------------------------------ iOS .cod (NSKeyedArchiver binary plist)
def write_cod(path, ecu="0F2330", diag=0x63, cafd=("00001EF6", 6, 64, 13), blocks=None):
    blocks = blocks or {0x3000: bytes([0x00, 0x11, 0x22, 0x33]), 0x3001: bytes([0x44, 0x55]),
                        0x3002: bytes(range(0x80, 0x80 + 32))}
    U = plistlib.UID
    objs = ["$null"]

    def add(o):
        objs.append(o)
        return U(len(objs) - 1)

    ident = "%s%02X%02X%02X" % (cafd[0], cafd[1], cafd[2], cafd[3])
    refs = [add({"blockAddress": a, "data": add(d)}) for a, d in sorted(blocks.items())]
    blk = add({"NS.objects": refs})
    cd = add({"CAFDIdentifier": add(ident), "mutableCodingDataBlocks": blk})
    one = add({"identifier": int(cafd[0], 16), "mainVersion": cafd[1], "subVersion": cafd[2], "patchVersion": cafd[3]})
    ids = add({"CAFD": one})
    ecu_o = add({"identifier": add(ecu), "address": diag, "identifiers": ids})
    root = add({"VIN": add(VIN), "codingECU": ecu_o, "codingData": cd})
    pl = {"$archiver": "NSKeyedArchiver", "$version": 100000, "$top": {"root": root}, "$objects": objs}
    with open(path, "wb") as f:
        plistlib.dump(pl, f, fmt=plistlib.FMT_BINARY)
    return path


# ------------------------------------------------------------------ decrypted CAFD (what decrypt.js writes)
def write_cafd(path, cafd=("00001EF6", 6, 64, 13), series="G020"):
    """A two-datagroup CAFD: 3000 USER (one byte option 00/01, one bit field) and 3002 SYSTEM (signature)."""
    def cp(hx, default=False):
        return (f'<condParam><paramName>.</paramName><bool isDefault="{"true" if default else "false"}" '
                f'mainSeries="{series}">{"" if default else "false()"}</bool>'
                f'<values><unitVal position="0">{hx}</unitVal></values></condParam>')
    fn = ('<function name="."><storageInfo><start>{s}</start><end>{e}</end><size>equal to unit</size>'
          '<mask>{m}</mask></storageInfo>{cps}</function>')
    xml = ('<?xml version="1.0" encoding="UTF-8"?><caf xmlns="http://bmw.com/2005/psdz.data.caf" isEncrypted="false" '
           'version="01.04.00"><sweHeader createdBy="." ecuName="."/><cafIdent><processClass>CAFD</processClass>'
           f'<id>{cafd[0]}</id><mainVersion>{cafd[1]:03d}</mainVersion><subVersion>{cafd[2]:03d}</subVersion>'
           f'<patchVersion>{cafd[3]:03d}</patchVersion></cafIdent><data>'
           '<datagroup address="3000" datagroupClass="USER" name="." units="04">'
           + fn.format(s=0, e=0, m="11111111b", cps=cp("00", True) + cp("01"))
           + fn.format(s=1, e=1, m="00000001b", cps=cp("00", True) + cp("01"))
           + '</datagroup><datagroup address="3002" datagroupClass="SYSTEM" name="." units="20">'
           + fn.format(s=0, e=31, m="11111111b", cps="")
           + "</datagroup></data></caf>")
    with gzip.open(path, "wb") as f:
        f.write(xml.encode("utf-8"))
    return path
