"""Regression tests for ocm/dtc_tables.py (the DTC tool's shipped fault texts): codes a newer ISTA drops are kept
from the existing tables, and the ISTA database is optional (no apsw needed without a key).

    python -m unittest discover -s tests -v

Synthetic rows and a tiny SQLite file only: no ISTA database needed."""
import contextlib
import gzip
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ocm"))
import dtc_tables as D  # noqa: E402


def mini_ista(path, faults, version="9.99.99"):
    """A DiagDocDb with just the tables dtc_tables.build reads. faults: [(address, code, en, de)]."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE XEP_ECUGROUPS(ID INTEGER PRIMARY KEY, DIAGNOSTIC_ADDRESS INT);
        CREATE TABLE XEP_ECUVARIANTS(ID INTEGER PRIMARY KEY, ECUGROUPID INT, TITLE_ENGB TEXT, TITLE_DEDE TEXT);
        CREATE TABLE XEP_FAULTCODES(ID INTEGER PRIMARY KEY, ECUVARIANTID INT, CODE TEXT);
        CREATE TABLE XEP_REFFAULTLABELS(ID INT, LABELID INT);
        CREATE TABLE XEP_FAULTLABELS(ID INTEGER PRIMARY KEY, TITLE_ENGB TEXT, TITLE_DEDE TEXT);
        CREATE TABLE RG_VERSION(SUBSYSTEM TEXT, VERSION TEXT);""")
    con.execute("INSERT INTO RG_VERSION VALUES('DATABASE', ?)", (version,))
    for i, (addr, code, en, de) in enumerate(faults, 1):
        con.execute("INSERT OR IGNORE INTO XEP_ECUGROUPS VALUES(?, ?)", (addr, addr))
        con.execute("INSERT OR IGNORE INTO XEP_ECUVARIANTS VALUES(?, ?, ?, ?)", (addr, addr, f"Module {addr:02X}",
                                                                              f"Modul {addr:02X}"))
        con.execute("INSERT INTO XEP_FAULTCODES VALUES(?, ?, ?)", (i, addr, str(code)))
        con.execute("INSERT INTO XEP_REFFAULTLABELS VALUES(?, ?)", (i, i))
        con.execute("INSERT INTO XEP_FAULTLABELS VALUES(?, ?, ?)", (i, en, de))
    con.commit()
    con.close()


def rows_of(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f][1:]


class KeepDropped(unittest.TestCase):
    def test_old_rows_fill_only_missing_keys_in_order(self):
        new = ["10\t000002\tnew two|neu zwei", "40\t000001\tB|B"]
        old = ["10\t000001\told one|alt eins", "10\t000002\told two|alt zwei", "12\t000005\tE|E"]
        rows, kept = D.keep_dropped(new, old, 9)
        self.assertEqual(kept, 2)
        self.assertEqual(rows, ["10\t000001\told one|alt eins", "10\t000002\tnew two|neu zwei",
                                "12\t000005\tE|E", "40\t000001\tB|B"])   # the new text wins where both exist

    def test_nothing_to_keep_leaves_rows_unchanged(self):
        new = ["10\t000001\tA|A", "12\t000002\tB|B"]
        self.assertEqual(D.keep_dropped(new, [], 9), (new, 0))
        self.assertEqual(D.keep_dropped(["10\tBDC|BDC"], ["10\tOld|Alt"], 2), (["10\tBDC|BDC"], 0))


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocm-dtc-")
        self.db, self.out = os.path.join(self.tmp, "DiagDocDb.sqlite"), os.path.join(self.tmp, "dtc")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *args):
        argv, sys.argv = sys.argv, ["dtc_tables.py", "--ista", self.db, "--out", self.out, *args]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                D.main()
        finally:
            sys.argv = argv

    def test_rebuild_keeps_codes_the_newer_ista_dropped(self):
        mini_ista(self.db, [(0x10, 0x021081, "BDC: Invalid certificate", "BDC: Zertifikat ungültig"),
                            (0x12, 0x000001, "DME: one", "DME: eins")], "4.50.40")
        self.run_main()
        os.remove(self.db)
        mini_ista(self.db, [(0x12, 0x000001, "DME: one (new)", "DME: eins (neu)"),
                            (0x40, 0x8048B5, "Washer pump", "Waschpumpe")], "4.57.12")
        self.run_main()
        self.assertEqual(rows_of(os.path.join(self.out, "faults.tsv.gz")),
                         ["10\t021081\tBDC: Invalid certificate|BDC: Zertifikat ungültig",
                          "12\t000001\tDME: one (new)|DME: eins (neu)", "40\t8048B5\tWasher pump|Waschpumpe"])
        self.assertEqual(rows_of(os.path.join(self.out, "modules.tsv.gz")),
                         ["10\tModule 10|Modul 10", "12\tModule 12|Modul 12", "40\tModule 40|Modul 40"])
        with open(os.path.join(self.out, "SOURCES.txt"), encoding="utf-8") as f:
            sources = f.read()
        self.assertIn("ISTA database 4.57.12", sources)
        self.assertIn("plus 1 fault codes and 1 module titles kept from the previous tables", sources)
        # --no-keep: this ISTA alone
        self.run_main("--no-keep")
        self.assertEqual(len(rows_of(os.path.join(self.out, "faults.tsv.gz"))), 2)

    def test_decrypted_db_needs_no_apsw(self):
        mini_ista(self.db, [(0x10, 0x000001, "A", "A")])
        saved = sys.modules.get("apsw")
        sys.modules["apsw"] = None          # import apsw would fail
        try:
            rows, modules, ver = D.build(self.db)
        finally:
            if saved is None:
                del sys.modules["apsw"]
            else:
                sys.modules["apsw"] = saved
        self.assertEqual((rows, ver), (["10\t000001\tA|A"], "9.99.99"))


if __name__ == "__main__":
    unittest.main()
