#!/usr/bin/env python3
r"""Build the DTC tool's shipped fault texts (data\dtc\) on the maintainer's machine. Users never run this:
OpenLauncher only needs E-Sys and these tables.

    python ocm\dtc_tables.py --ista <decrypted DiagDocDb.sqlite>
    python ocm\dtc_tables.py --ista <DiagDocDb.sqlite as ISTA ships it> --key <key>   (needs: pip install apsw-sqlite3mc)

The key can also come from the environment variable OCM_ISTA_KEY (keeps it out of the shell history). It isn't
shipped. The ISTA database stays optional for Open CAFD Map: only this maintainer script reads it.

Writes (deterministic gzip, so an unchanged rebuild gives identical files):
  data\dtc\faults.tsv.gz   diagnostic address (hex), fault code (hex, as the ECU reports it), then up to 3 texts
                           "English|German", most common first. ISTA ties a code to an ECU variant; the variants are
                           grouped by diagnostic address, so address + code narrows the text down without knowing the
                           variant. Where ISTA has several texts for one address and code (different ECU generations),
                           all of them are kept, up to 3.
  data\dtc\modules.tsv.gz  diagnostic address (hex) -> module title "English|German" (the most common ECU variant
                           title at that address), shown when E-Sys has no SVT with the module's name
  data\dtc\SOURCES.txt     the ISTA version the tables were built from

Codes and module titles already in the tables that this ISTA version lacks are kept (newer ISTA versions drop some
that cars still report); --no-keep rebuilds from this ISTA alone.
"""
import argparse
import collections
import gzip
import io
import os
import sqlite3
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

OUT = os.path.join(config.DATA, "dtc")
MAX_TEXTS = 3


def clean(s):
    return " ".join((s or "").replace("|", "/").split())


def open_db(ista, key=None):
    """(connection, error classes): plain sqlite3 for a decrypted database, apsw (SQLite3 Multiple Ciphers) with the
    RC4 cipher for the encrypted one ISTA ships. apsw is only imported when a key is given."""
    if not key:
        return sqlite3.connect(f"file:{ista}?mode=ro", uri=True), (sqlite3.Error,)
    try:
        import apsw
    except ImportError:
        sys.exit("--key needs SQLite3 Multiple Ciphers for Python: pip install apsw-sqlite3mc")
    params = urllib.parse.urlencode({"cipher": "rc4", "key": key})
    con = apsw.Connection(f"file:{ista}?{params}", flags=apsw.SQLITE_OPEN_URI | apsw.SQLITE_OPEN_READONLY)
    return con, (apsw.Error,)


def build(ista, key=None):
    con, db_errors = open_db(ista, key)
    q = ("SELECT g.DIAGNOSTIC_ADDRESS, f.CODE, l.TITLE_ENGB, l.TITLE_DEDE "
         "FROM XEP_FAULTCODES f JOIN XEP_ECUVARIANTS v ON v.ID = f.ECUVARIANTID "
         "JOIN XEP_ECUGROUPS g ON g.ID = v.ECUGROUPID "
         "JOIN XEP_REFFAULTLABELS r ON r.ID = f.ID JOIN XEP_FAULTLABELS l ON l.ID = r.LABELID")
    per = collections.defaultdict(collections.Counter)
    for addr, code, en, de in con.execute(q):
        en, de = clean(en), clean(de)
        if addr is None or not str(code or "").isdigit() or not (en or de):
            continue
        a, c = int(addr), int(code)
        if not (0 <= a <= 0xFF and 0 <= c <= 0xFFFFFF):
            continue
        per[(a, c)][(en, de)] += 1
    # a module title per diagnostic address (the most common ECU variant title there), for modules without an SVT name
    mods = collections.defaultdict(collections.Counter)
    for addr, en, de in con.execute("SELECT g.DIAGNOSTIC_ADDRESS, v.TITLE_ENGB, v.TITLE_DEDE FROM XEP_ECUVARIANTS v "
                                    "JOIN XEP_ECUGROUPS g ON g.ID = v.ECUGROUPID"):
        en, de = clean(en), clean(de)
        if addr is not None and 0 <= int(addr) <= 0xFF and (en or de):
            mods[int(addr)][(en, de)] += 1
    modules = [f"{a:02X}\t{t[0]}|{t[1]}" for a, cnt in sorted(mods.items())
               for t in [sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]]]
    try:
        ver = con.execute("SELECT VERSION FROM RG_VERSION WHERE SUBSYSTEM = 'DATABASE'").fetchone()
        ver = ver[0] if ver else None
    except db_errors:
        ver = None
    con.close()
    rows = []
    for (a, c), cnt in sorted(per.items()):
        texts = [t for t, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))][:MAX_TEXTS]
        rows.append(f"{a:02X}\t{c:06X}\t" + "\t".join(f"{en}|{de}" for en, de in texts))
    return rows, modules, ver


def read_gz_rows(path):
    """Data rows (no header) of one of our .tsv.gz tables, [] if there is none yet."""
    if not os.path.isfile(path):
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f.readlines()[1:] if line.strip()]


def keep_dropped(rows, old_rows, key_len):
    """rows plus every old row whose key (the first key_len characters: "AA\tCCCCCC" or "AA") the new build lacks,
    sorted by key. A newer ISTA drops some codes that cars still report, so a rebuild never loses a text."""
    have = {r[:key_len] for r in rows}
    kept = [r for r in old_rows if r[:key_len] not in have]
    return sorted(rows + kept, key=lambda r: r[:key_len]), len(kept)


def write_gz(path, text):
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as g:
        g.write(text.encode("utf-8"))
    with open(path, "wb") as f:
        f.write(raw.getvalue())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ista", required=True, help="ISTA DiagDocDb.sqlite, decrypted or with --key (maintainer only)")
    ap.add_argument("--key", default=os.environ.get("OCM_ISTA_KEY"),
                    help="key of the encrypted DiagDocDb as ISTA ships it (default: OCM_ISTA_KEY); omit for a decrypted one")
    ap.add_argument("--out", default=OUT, help=f"output folder (default: {OUT})")
    ap.add_argument("--no-keep", action="store_true",
                    help="don't keep codes and module titles of the existing tables that this ISTA lacks")
    a = ap.parse_args()
    rows, modules, ver = build(a.ista, a.key)
    fp, mp = os.path.join(a.out, "faults.tsv.gz"), os.path.join(a.out, "modules.tsv.gz")
    kept_f = kept_m = 0
    if not a.no_keep:
        rows, kept_f = keep_dropped(rows, read_gz_rows(fp), 9)
        modules, kept_m = keep_dropped(modules, read_gz_rows(mp), 2)
    os.makedirs(a.out, exist_ok=True)
    write_gz(fp, "addr\tcode\ttexts (English|German, most common first)\n" + "\n".join(rows) + "\n")
    write_gz(mp, "addr\ttitle (English|German)\n" + "\n".join(modules) + "\n")
    with open(os.path.join(a.out, "SOURCES.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("DTC fault texts (built by ocm/dtc_tables.py)\n")
        f.write(f"ISTA database {ver or '?'}\n")
        if kept_f or kept_m:
            f.write(f"plus {kept_f} fault codes and {kept_m} module titles kept from the previous tables (other ISTA versions)\n")
    print(f"{len(rows)} address + code pairs ({kept_f} kept), {len(modules)} module titles ({kept_m} kept) -> {a.out}")


if __name__ == "__main__":
    main()
