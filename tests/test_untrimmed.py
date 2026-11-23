"""Regression tests for BMW untrimmed names (ocm/untrimmed.py, build_defdb.apply_untrimmed): names by datagroup
layout, and the field fallback for groups whose layout isn't packaged (issue #8: EGS 00002458, group 3000,
byte 4 mask 0xFE split into 0x02 + 0xFC; the other fields keep their names).

    python -m unittest discover -s tests -v

Synthetic package and DB only: no psdzdata, no decrypted CAFDs."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import contextlib
import io

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ocm"))
import build_defdb as B  # noqa: E402
import untrimmed as U  # noqa: E402

CID = "00002458"
# packaged layout of group 3000 (as in definitions/untrimmed/00002458.tsv.gz, trimmed to bytes 3-4)
PACKED = [(3, 3, "00000001b", "Sportschalter", {"00": "nicht aktiv", "01": "aktiv"}),
          (3, 3, "00000010b", "Allrad", {"00": "nicht_aktiv", "01": "aktiv"}),
          (3, 3, "11111100b", "UNBELEGT", {"00": "initwert"}),
          (4, 4, "00000001b", "AGS_US", {"00": "nicht_aktiv", "01": "aktiv"}),
          (4, 4, "11111110b", "UNBELEGT", {"00": "initwert"})]
# the reporter's layout: byte 4's 0xFE split in two
SPLIT = [(3, 3, "00000001b", ["00", "01"]), (3, 3, "00000010b", ["00", "01"]), (3, 3, "11111100b", ["00"]),
         (4, 4, "00000001b", ["01", "00"]), (4, 4, "00000010b", ["01", "00"]), (4, 4, "11111100b", ["00"])]


def pack_group(layout, names):
    h = U.layout_hash([(s, e, m) for s, e, m, *_ in layout])
    g = {"G": "Applikation", "F": {}, "V": {}}
    for st, en, mk, name, vals in names:
        g["F"][(st, en, mk)] = name
        for hx, vn in vals.items():
            g["V"][(st, en, mk, hx)] = vn
    return ("3000", h), g


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


class FieldMatch(unittest.TestCase):
    def idx(self, *groups):
        return U.field_index(dict(groups))

    def test_exact_field_borrows_name_and_values(self):
        idx = self.idx(pack_group(PACKED, PACKED))
        self.assertEqual(U.field_match(idx, "3000", 4, 4, "00000001b", {"00", "01"}),
                         ("AGS_US", {"00": "nicht_aktiv", "01": "aktiv"}))

    def test_split_field_has_no_match(self):
        idx = self.idx(pack_group(PACKED, PACKED))
        self.assertIsNone(U.field_match(idx, "3000", 4, 4, "00000010b", {"00", "01"}))
        self.assertIsNone(U.field_match(idx, "3000", 4, 4, "11111100b", {"00"}))

    def test_layouts_that_disagree_give_nothing(self):
        other = [r if r[2] != "00000010b" or r[0] != 3 else (3, 3, "00000010b", "xDrive", {}) for r in PACKED]
        lay2 = PACKED[:-1] + [(4, 4, "00000010b", "X", {}), (4, 4, "11111100b", "UNBELEGT", {})]
        idx = self.idx(pack_group(PACKED, PACKED), pack_group(lay2, other))
        self.assertIsNone(U.field_match(idx, "3000", 3, 3, "00000010b", {"00", "01"}))
        self.assertEqual(U.field_match(idx, "3000", 4, 4, "00000001b", {"00", "01"})[0], "AGS_US")

    def test_value_codes_the_function_lacks_reject_a_layout(self):
        # a packaged feature bit {00, 01} must not name a bit that only has {00} here (reserved)
        idx = self.idx(pack_group(PACKED, PACKED))
        self.assertIsNone(U.field_match(idx, "3000", 3, 3, "00000010b", {"00"}))
        # the other way round is allowed: the packaged layout names fewer codes than the function has
        self.assertEqual(U.field_match(idx, "3000", 4, 4, "11111110b", {"00", "01"})[0], "UNBELEGT")

    def test_marked_names_are_never_packed_back(self):
        self.assertTrue(U.placeholder("Allrad" + U.POS_MARK))
        self.assertFalse(U.placeholder("Allrad"))


class ApplyUntrimmed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocm-untrimmed-")
        key, g = pack_group(PACKED, PACKED)
        quiet(U.save, CID, {key: g}, {"3000": "Applikation"}, self.tmp)
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(B.SCHEMA)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_cafd(self, layout, version, named=None):
        """layout: [(start, end, mask, [value hex..])]; named: {(start, end, mask): (name, source)} pre-named."""
        c = self.con
        cpk = c.execute("INSERT INTO cafd(cafd_id, version) VALUES(?, ?)", (CID, version)).lastrowid
        dpk = c.execute("INSERT INTO datagroup(cafd_pk, address) VALUES(?, '3000')", (cpk,)).lastrowid
        for i, (st, en, mk, hexes) in enumerate(layout):
            name, src = (named or {}).get((st, en, mk), (f"3000_{st}_{en}_{int(mk[:-1], 2)}", "derived"))
            fpk = c.execute("INSERT INTO func(cafd_pk, dg_pk, address, idx, start, end, mask, name, name_source) "
                            "VALUES(?,?,?,?,?,?,?,?,?)", (cpk, dpk, "3000", i, st, en, mk, name, src)).lastrowid
            for j, hx in enumerate(hexes):
                c.execute("INSERT INTO val(func_pk, idx, hex, label, label_source) VALUES(?,?,?,?, 'derived')",
                          (fpk, j, hx, f"wert_{j + 1:02d} [n/a here]"))
        return cpk

    def names(self, cpk):
        out = {}
        for fpk, st, en, mk, name, src in self.con.execute(
                "SELECT pk, start, end, mask, name, name_source FROM func WHERE cafd_pk=? ORDER BY idx", (cpk,)):
            vals = dict(self.con.execute("SELECT hex, label FROM val WHERE func_pk=?", (fpk,)).fetchall())
            out[(st, en, mk)] = (name, src, vals)
        return out

    def test_packaged_layout_still_matches_exactly(self):
        cpk = self.add_cafd([(s, e, m, list(v)) for s, e, m, _, v in PACKED], "008_044_021")
        quiet(B.apply_untrimmed, self.con, self.tmp)
        n = self.names(cpk)
        self.assertEqual(n[(4, 4, "00000001b")][:2], ("AGS_US", "untrimmed"))
        self.assertEqual(n[(4, 4, "00000001b")][2]["01"], "wert_02 aktiv [n/a here]")

    def test_split_layout_gets_marked_names_by_field(self):
        cpk = self.add_cafd(SPLIT, "008_044_022")
        quiet(B.apply_untrimmed, self.con, self.tmp)
        n = self.names(cpk)
        self.assertEqual(n[(3, 3, "00000010b")][:2], ("Allrad" + U.POS_MARK, "untrimmed-pos"))
        self.assertEqual(n[(4, 4, "00000001b")][0], "AGS_US" + U.POS_MARK)
        self.assertEqual(n[(4, 4, "00000001b")][2], {"01": "wert_01 aktiv [n/a here]",
                                                     "00": "wert_02 nicht_aktiv [n/a here]"})
        comment = self.con.execute("SELECT comment FROM func WHERE cafd_pk=? AND start=4 AND mask='00000001b'",
                                   (cpk,)).fetchone()[0]
        self.assertEqual(comment, U.POS_NOTE)
        # the two new fields from the split stay placeholders
        self.assertEqual(n[(4, 4, "00000010b")][:2], ("3000_4_4_2", "derived"))
        self.assertEqual(n[(4, 4, "11111100b")][:2], ("3000_4_4_252", "derived"))

    def test_names_from_other_sources_win(self):
        cpk = self.add_cafd(SPLIT, "008_044_022", named={(3, 3, "00000001b"): ("SPORTSCHALTER", "cheatsheet"),
                                                          (3, 3, "00000010b"): ("4WD", "contributed")})
        quiet(B.apply_untrimmed, self.con, self.tmp)
        n = self.names(cpk)
        # same name (any case): kept, and its values get the packaged value names
        self.assertEqual(n[(3, 3, "00000001b")][:2], ("SPORTSCHALTER", "cheatsheet"))
        self.assertEqual(n[(3, 3, "00000001b")][2]["01"], "wert_02 aktiv [n/a here]")
        # a different name: untouched, values too
        self.assertEqual(n[(3, 3, "00000010b")], ("4WD", "contributed", {"00": "wert_01 [n/a here]",
                                                                          "01": "wert_02 [n/a here]"}))


if __name__ == "__main__":
    unittest.main()
