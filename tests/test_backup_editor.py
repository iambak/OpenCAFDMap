"""Regression tests for the BimmerCode Backup Editor: iOS .cod and Android .dat backups (read, edit in place,
round-trip) and the definitions it builds from your backups.

    python -m unittest discover -s tests -v

Runs without E-Sys or psdzdata: the backups and the decrypted CAFD are synthetic (tests/fixtures.py)."""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ocm"))
sys.path.insert(0, HERE)
import bc_android  # noqa: E402
import bc_tool as bt  # noqa: E402
import fixtures as fx  # noqa: E402


def quiet(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*a)


class Workspace(unittest.TestCase):
    """Every test gets its own workspace: bc_tool's folders, DB and decrypted cache point into a temp dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocm-test-")
        self._saved = {k: getattr(bt, k) for k in ("ORIG", "DEC", "OUT", "EDITS", "DB", "BDB", "STAMP", "BCACHE",
                                                    "SRC", "INPUTS")}
        self._dec_default = bt.decode_one.__defaults__
        bt.ORIG, bt.DEC, bt.OUT, bt.EDITS = (os.path.join(self.tmp, d) for d in ("originals", "decoded", "out", "edits"))
        bt.DB = bt.BDB = os.path.join(self.tmp, "cafd_defs_backups.sqlite")
        bt.STAMP = bt.BDB + ".json"
        bt.BCACHE = os.path.join(self.tmp, "cache")
        bt.SRC = os.path.join(self.tmp, "phone")
        self.names = os.path.join(self.tmp, "names.tsv")      # stands in for the shipped names (staleness)
        open(self.names, "w").close()
        bt.INPUTS = list(self._saved["INPUTS"]) + [self.names]
        bt.decode_one.__defaults__ = (bt.DEC,)
        os.makedirs(bt.SRC)
        quiet(bt.ensure_workspace)

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(bt, k, v)
        bt.decode_one.__defaults__ = self._dec_default
        shutil.rmtree(self.tmp, ignore_errors=True)

    def backup_pair(self, folder):
        dat = fx.write_dat(os.path.join(folder, f"{fx.VIN}_0F2330.dat"))
        cod = fx.write_cod(os.path.join(folder, f"0F2330-{fx.VIN}-1786000000.cod"))
        return dat, cod


class ReadBackups(Workspace):
    def test_android_fields(self):
        d = bc_android.Dat(fx.write_dat(os.path.join(self.tmp, "a.dat"),
                                        cafds=(("00001EF6", 6, 64, 13), ("00001B29", 10, 0, 21))))
        self.assertEqual(d.platform, "Android")
        self.assertEqual((d.vin, d.ecu_id, d.diag), (fx.VIN, "0F2330", None))
        self.assertEqual(d.cafds, [("00001EF6", "006_064_013"), ("00001B29", "010_000_021")])
        self.assertEqual((d.cafd, d.ver), ("00001EF6", "006_064_013"))
        self.assertEqual(sorted(d.blocks), ["3000", "3001", "3002"])
        self.assertEqual(bytes(d.blocks["3001"]), b"\x44\x55")
        self.assertEqual(d.ts, 1786000000)

    def test_android_field_names_dont_matter(self):
        """BimmerCode's field names are obfuscated and change between app versions."""
        d = bc_android.Dat(fx.write_dat(os.path.join(self.tmp, "a.dat"), field_names="vwxyz"))
        self.assertEqual((d.vin, d.ecu_id, d.cafd), (fx.VIN, "0F2330", "00001EF6"))

    def test_android_rejects_other_files(self):
        p = os.path.join(self.tmp, "x.dat")
        open(p, "wb").write(b"not a backup")
        with self.assertRaises(ValueError):
            bc_android.Dat(p)

    def test_ios_fields(self):
        c = bt.Cod(fx.write_cod(os.path.join(self.tmp, f"0F2330-{fx.VIN}-1786000000.cod")))
        self.assertEqual(c.platform, "iOS")
        self.assertEqual((c.vin, c.ecu_id, c.diag, c.cafd, c.ver), (fx.VIN, "0F2330", 0x63, "00001EF6", "006_064_013"))
        self.assertEqual(sorted(c.blocks), ["3000", "3001", "3002"])

    def test_open_backup_and_listing(self):
        dat, cod = self.backup_pair(bt.ORIG)
        self.assertIsInstance(bt.open_backup(dat), bc_android.Dat)
        self.assertIsInstance(bt.open_backup(cod), bt.Cod)
        run = os.path.join(bt.OUT, "20260101-120000")
        os.makedirs(run)
        shutil.copy(dat, run)                     # Android edits land in a folder per run
        self.assertEqual(len(bt.backup_files(bt.ORIG)), 2)
        self.assertEqual(len(bt.backup_files(bt.OUT, out=True)), 1)
        self.assertEqual(len(bt.all_cods()), 3)
        self.assertEqual([bt.diag_txt(bt.open_backup(p)) for p in (dat, cod)], ["-", "0x63"])
        self.assertTrue(bt.is_out(bt.open_backup(os.path.join(run, os.path.basename(dat)))))


class WriteInPlace(Workspace):
    """A new backup is a byte copy of the source with only the edited bytes changed."""

    def check(self, src, out_name):
        b = bt.open_backup(src)
        blocks = b.blocks
        out = os.path.join(self.tmp, "same" + out_name)
        b.save_as(out, blocks)
        self.assertEqual(open(out, "rb").read(), open(src, "rb").read(), "unchanged save must be byte-identical")
        blocks["3000"][1] = 0x99
        blocks["3001"][0] = 0x00
        out = os.path.join(self.tmp, "edit" + out_name)
        b.save_as(out, blocks)
        a, n = open(src, "rb").read(), open(out, "rb").read()
        self.assertEqual(len(a), len(n))
        self.assertEqual(sum(x != y for x, y in zip(a, n)), 2)
        r = bt.open_backup(out)
        self.assertEqual(bytes(r.blocks["3000"]), b"\x00\x99\x22\x33")
        self.assertEqual(bytes(r.blocks["3001"]), b"\x00\x55")
        self.assertEqual(bytes(r.blocks["3002"]), bytes(b.blocks["3002"]), "signature block must not change")
        self.assertEqual((r.vin, r.ecu_id, r.cafds), (b.vin, b.ecu_id, b.cafds))
        blocks["3000"] = bytearray(b"\x00")
        with self.assertRaises(ValueError):
            b.save_as(os.path.join(self.tmp, "short" + out_name), blocks)

    def test_android(self):
        self.check(fx.write_dat(os.path.join(self.tmp, f"{fx.VIN}_0F2330.dat")), ".dat")

    def test_ios(self):
        self.check(fx.write_cod(os.path.join(self.tmp, f"0F2330-{fx.VIN}-1786000000.cod")), ".cod")


class DefinitionsFromBackups(Workspace):
    """The editor builds its DB from exactly the CAFD versions in your backups (no full series build)."""

    def setUp(self):
        super().setUp()
        self.backup_pair(bt.SRC)
        os.makedirs(bt.BCACHE)
        # what decrypt.js would write for this version; with it cached, nothing has to be decrypted (no E-Sys)
        fx.write_cafd(os.path.join(bt.BCACHE, "cafd_00001EF6_006_064_013.xml.gz"))

    def import_(self):
        return quiet(bt.cmd_init, type("A", (), {"folder": bt.SRC})())

    def test_import_builds_and_decodes(self):
        self.import_()
        self.assertTrue(os.path.exists(bt.DB))
        self.assertEqual(bt.needed_versions(), {("00001EF6", "006_064_013")})
        self.assertEqual(bt.cached_versions(), bt.needed_versions())
        self.assertEqual(len(os.listdir(bt.DEC)), 4, "a .txt and a .json per backup")
        con = bt.db()
        for c in bt.all_cods():
            fs = bt.functions(con, c)
            self.assertEqual(sorted({f["addr"] for f in fs}), ["3000", "3002"])
        con.close()

    def test_no_rebuild_when_nothing_changed(self):
        self.import_()
        stamp = json.load(open(bt.STAMP))
        mtime = os.path.getmtime(bt.DB)
        self.assertFalse(bt.defs_status()[2])
        quiet(bt.ensure_defs)
        self.assertEqual(os.path.getmtime(bt.DB), mtime)
        self.assertEqual(json.load(open(bt.STAMP)), stamp)

    def test_rebuild_when_names_change(self):
        self.import_()
        os.utime(self.names, (time.time(), time.time() + 60))
        self.assertTrue(bt.defs_status()[2])
        quiet(bt.ensure_defs)
        self.assertFalse(bt.defs_status()[2])

    def test_version_not_in_psdzdata(self):
        self.import_()
        fx.write_dat(os.path.join(bt.ORIG, f"{fx.VIN}_0F9990.dat"), ecu="0F9990", cafds=(("00009999", 1, 2, 3),))
        missing, absent, _stale = bt.defs_status()
        self.assertEqual(missing, set())
        self.assertEqual(absent, {("00009999", "001_002_003")})
        self.assertEqual(quiet(bt.ensure_defs), absent)

    def test_edit_end_to_end(self):
        self.import_()
        con = bt.db()
        for name in (f"{fx.VIN}_0F2330.dat", f"0F2330-{fx.VIN}-1786000000.cod"):
            src = bt.open_backup(os.path.join(bt.ORIG, name))
            fs = bt.functions(con, src)
            f = next(x for x in fs if x["addr"] == "3000" and x["start"] == 0)
            self.assertEqual(bt.extract(src.blocks["3000"], f["start"], f["end"], f["mask"]), "00")
            out = quiet(bt.write_new, con, src, [(f, "01")], "test")
            self.assertTrue(bt.is_out(bt.open_backup(out)))
            if src.platform == "Android":
                self.assertEqual(os.path.basename(out), name, "Android keeps the source's file name")
            a, n = open(src.path, "rb").read(), open(out, "rb").read()
            self.assertEqual(sum(x != y for x, y in zip(a, n)), 1)
            sig = next(x for x in fs if x["dg_class"] == "SYSTEM")
            with self.assertRaises(SystemExit):
                quiet(bt.write_new, con, src, [(sig, "00" * 32)], "must refuse")
        con.close()

    def test_db_schema_is_what_the_editor_reads(self):
        self.import_()
        con = sqlite3.connect(bt.DB)
        for table in ("cafd", "datagroup", "func", "val"):
            self.assertTrue(con.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone(), table)
        con.close()


if __name__ == "__main__":
    unittest.main()
