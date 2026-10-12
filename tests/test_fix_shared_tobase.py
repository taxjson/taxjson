"""v0.27.1: one tobase.map every year of a multi-year project reads
(`[settings] tobase_map = "../tobase.map"`), and no DISTINCT line for a
depositary receipt (look-alike listings are never joined; the
interlisted master keeps a CDR apart from its US share with no line).
Tax-logic CA-XLIST-06 / US-XLIST-05.

Every fixture is SYNTHETIC: invented tickers (QZ*), invented FIGIs
(BBG0000000xx), invented names.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli

from taxjson.lib import checklist as CL
from taxjson.lib import cross_listings as XL
from taxjson.lib import project_layout as _PL
from taxjson.lib import tobase_map as TB
from taxjson.lib.symbol_codes import exact_name

F1, F4 = "BBG000000Q01", "BBG000000Q04"
SEC = {F1: {"name": "QZALPHA", "kind": "share", "share_class_figi": F1,
            "ca": ["QZA.TO"], "us": ["QZAB.US"], "us_exchange": ["NYSE"]}}
CDR = {F4: {"name": "QZDELTA INC - CDR", "kind": "cdr", "ca": "QZD.TO",
            "us": "QZD.US", "us_figi": "BBG000000Q05"}}


def _master(security=None, distinct=None, generated="2026-10-01"):
    return TB.Master({"schema_version": 1, "generated": generated,
                      "sources": [{"name": "synthetic", "as_of": generated}]},
                     dict(security or {}), dict(distinct or {}))


def _toml(year, country="canada", extra=""):
    return (f'[settings]\ncountry = "{country}"\nyear = {year}\n'
            f'inputs_dir = "../inputs"\n{extra}')


def _years(td, years=(2024, 2025), tobase=None, shared=False):
    """A multi-year project: one folder per year, the exports shared;
    `tobase` the text of each year's own tobase.map (or, `shared`, of
    one at the top every year reads)."""
    top = Path(td) / "taxes"
    (top / "inputs").mkdir(parents=True)
    for y in years:
        d = top / str(y)
        d.mkdir()
        (d / "taxjson.toml").write_text(_toml(
            y, extra='tobase_map = "../tobase.map"\n' if shared else ""))
        (d / "ticker.map").write_text("")
        if tobase is not None and not shared:
            (d / "tobase.map").write_text(tobase)
    if tobase is not None and shared:
        (top / "tobase.map").write_text(tobase)
    return top


# ------------------------------------------------------------ the setting

class TestSetting(unittest.TestCase):

    def test_resolves_beside_the_year_folders(self):
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, tobase=TB.render(_master(SEC)), shared=True)
            d = top / "2025"
            self.assertTrue(_PL.shared_tobase(d))
            self.assertEqual(_PL.tobase_map_path(d),
                             (top / "tobase.map").resolve())
            self.assertEqual(TB.tobase_path(d), _PL.tobase_map_path(d))
            self.assertEqual(_PL.tobase_shown(d),
                             "../tobase.map, shared by every year")
            self.assertEqual(_PL.setting_problems(d, _PL._settings(d)), [])
            # Read through it: the overlay of the year's ticker.map.
            ov = TB.overlay_for(d / "ticker.map", "")
            self.assertIn("TOBASE QZAB.US QZA.TO", ov.lines)
            # A single-folder project: beside ticker.map, as before.
            single = Path(td) / "single"
            single.mkdir()
            (single / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n')
            self.assertFalse(_PL.shared_tobase(single))
            self.assertEqual(_PL.tobase_map_path(single),
                             single / "tobase.map")

    def test_path_safety(self):
        with tempfile.TemporaryDirectory() as td:
            top = _years(td)
            d = top / "2025"
            outside = Path(td) / "elsewhere"
            outside.mkdir()
            (outside / "tobase.map").write_text("")
            (top / "link.map").symlink_to(outside / "tobase.map")
            (top / "2024" / "tobase.map").write_text("")
            for v, want in (("../../elsewhere/tobase.map", "outside"),
                            ("../link.map", "outside"),
                            ("tobase.map", "inside this project"),
                            ("../2024/tobase.map", "another year"),
                            ("..", "names a folder"),
                            ("../inputs", "names a folder"),
                            ("", "must be a file path")):
                with self.subTest(v=v):
                    st = {"tobase_map": v}
                    with self.assertRaises(_PL.LayoutError) as cm:
                        _PL.tobase_setting(d, st)
                    self.assertIn(want, str(cm.exception))
                    probs = _PL.setting_problems(d, st)
                    self.assertTrue(any(want in p for p in probs), probs)
                    # A reader that runs anyway: the project's own file.
            (d / "taxjson.toml").write_text(_toml(
                2025, extra='tobase_map = "../../elsewhere/tobase.map"\n'))
            self.assertEqual(_PL.tobase_map_path(d), d / "tobase.map")

    def test_own_copy_beside_the_setting_is_refused_naming_both(self):
        with tempfile.TemporaryDirectory() as td:
            text = TB.render(_master(SEC))
            top = _years(td, tobase=text, shared=True)
            d = top / "2025"
            (d / "tobase.map").write_text(text)
            why = _PL.tobase_both_problem(d, _PL._settings(d))
            self.assertIn("two tobase.map files", why)
            self.assertIn("'../tobase.map'", why)
            self.assertIn("tobase.map of its own", why)
            self.assertIn("taxjson migrate", why)
            r = cli(d, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("two tobase.map files", r.stderr)
            r = cli(d, "update-tobase-map")
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("two tobase.map files", r.stderr)
            self.assertFalse((top / "tobase.map.bak").exists())


# ------------------------------------------------------------ the commands

class TestCommands(unittest.TestCase):

    def test_single_folder_init_keeps_it_beside_ticker_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            r = cli(Path(td), "init", str(root), "--single", "--country",
                    "canada", "--year", "2025")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((root / "tobase.map").is_file())
            self.assertFalse(_PL.shared_tobase(root))
            self.assertNotIn("Shared by every year",
                             (root / "tobase.map").read_text())

    def test_new_year_from_a_per_year_copy_keeps_one_and_says_migrate(self):
        with tempfile.TemporaryDirectory() as td:
            text = TB.render(_master(SEC))
            top = _years(td, years=(2025,), tobase=text)
            r = cli(top / "2025", "new-year", "2026")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((top / "2026" / "tobase.map").read_text(), text)
            self.assertIn("migrate", r.stdout + r.stderr)

    @rule("CA-XLIST-06")
    def test_migrate_identical_copies(self):
        with tempfile.TemporaryDirectory() as td:
            text = TB.render(_master(SEC))
            top = _years(td, years=(2024, 2025, 2026), tobase=text)
            r = cli(top, "migrate", "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("identical", r.stdout)
            self.assertFalse((top / "tobase.map").exists())
            r = cli(top, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((top / "tobase.map").read_text(), text)
            for y in (2024, 2025, 2026):
                d = top / str(y)
                self.assertFalse((d / "tobase.map").exists())
                self.assertEqual((d / "tobase.map.bak").read_text(), text)
                # Beside inputs_dir, the rest of the file as it was.
                self.assertIn('inputs_dir = "../inputs"\n'
                              'tobase_map = "../tobase.map"\n',
                              (d / "taxjson.toml").read_text())
                self.assertEqual(_PL.tobase_map_path(d),
                                 (top / "tobase.map").resolve())
                self.assertEqual(_PL.setting_problems(d, _PL._settings(d)),
                                 [])
            # Again: nothing left to do.
            r = cli(top, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("nothing to migrate", r.stdout)
            # years: the shared file, compared as a setting.
            r = cli(top, "years")
            self.assertIn("tobase.map: tobase.map, shared by 2024, 2025, "
                          "2026", r.stdout)
            c = _PL.compare(top / "2024", top / "2026")
            self.assertEqual((c["tobase_only_here"], c["tobase_only_there"],
                              c["keys"]), ([], [], []))

    def test_migrate_from_a_year_folder(self):
        with tempfile.TemporaryDirectory() as td:
            text = TB.render(_master(SEC))
            top = _years(td, tobase=text)
            r = cli(top / "2025", "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((top / "tobase.map").is_file())
            self.assertFalse((top / "2024" / "tobase.map").exists())

    def test_migrate_differing_copies_needs_write(self):
        with tempfile.TemporaryDirectory() as td:
            old = TB.render(_master(SEC)) + \
                "\n## --- Your own lines (kept as written) ---\n" \
                "TOBASE QZOWN.US QZOWN.TO\n"
            new = TB.render(_master(SEC, generated="2026-10-05"))
            top = _years(td, tobase=old)
            (top / "2025" / "tobase.map").write_text(new)
            r = cli(top, "migrate")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("differ", r.stdout + r.stderr)
            self.assertIn("TOBASE QZOWN.US QZOWN.TO", r.stdout)
            self.assertIn("2025/tobase.map", r.stdout)
            self.assertFalse((top / "tobase.map").exists())
            self.assertTrue((top / "2024" / "tobase.map").is_file())
            self.assertNotIn("tobase_map", (top / "2024" / "taxjson.toml")
                             .read_text())
            r = cli(top, "migrate", "--write")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((top / "tobase.map").read_text(), new)
            self.assertEqual((top / "2024" / "tobase.map.bak").read_text(),
                             old)
            self.assertIn("TOBASE QZOWN.US QZOWN.TO", r.stdout)

    def test_migrate_to_years_keeps_it_at_the_top(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            r = cli(Path(td), "init", str(root), "--single", "--country",
                    "canada", "--year", "2025")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = cli(root, "migrate", "--to-years")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((root / "tobase.map").is_file())
            self.assertFalse((root / "2025" / "tobase.map").exists())
            self.assertEqual(_PL.tobase_map_path(root / "2025"),
                             (root / "tobase.map").resolve())

    def test_update_writes_the_shared_file_from_a_year_or_the_top(self):
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, tobase=TB.render(_master(SEC)), shared=True)
            p = top / "tobase.map"
            p.write_text(p.read_text().replace(
                TB.STAMP + " ", TB.STAMP + " 2020-01-01 # "))
            r = cli(top / "2024", "update-tobase-map")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("../tobase.map, shared by every year: read by "
                          "2024, 2025", " ".join(r.stdout.split()))
            r = cli(top / "2024", "update-tobase-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Shared by every year folder", p.read_text())
            self.assertTrue((top / "tobase.map.bak").is_file())
            self.assertFalse((top / "2024" / "tobase.map").exists())
            self.assertIn("in each year that reads it (2024, 2025)",
                          " ".join(r.stdout.split()))
            # At the top: the same file, as from the newest year.
            r = cli(top, "update-tobase-map", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(json.loads(r.stdout)["exists"])
            # Ambiguous at the top: a year keeps a copy of its own.
            (top / "2025" / "taxjson.toml").write_text(_toml(2025))
            (top / "2025" / "tobase.map").write_text(p.read_text())
            r = cli(top, "update-tobase-map")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("ambiguous", r.stderr)

    def test_checklist_and_years_name_the_shared_file_that_changed(self):
        before = {"tobase.map": "h1", "ticker.map": "t"}
        # The year's own copy made the shared one, same content: no
        # change (`taxjson migrate`).
        self.assertEqual(CL._fingerprint_diff(
            before, {"../tobase.map": "h1", "ticker.map": "t"}), "")
        self.assertEqual(CL._fingerprint_diff(
            {"../tobase.map": "h1"}, {"../tobase.map": "h2"}),
            "changed: ../tobase.map (shared by every year)")
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, tobase=TB.render(_master(SEC)), shared=True)
            d = top / "2025"
            paths = CL._input_paths(d, {})
            self.assertIn((top / "tobase.map").resolve(), paths)
            self.assertEqual(CL._fp_key(d, (top / "tobase.map").resolve()),
                             "../tobase.map")
            # The setting itself is not a run input (the file is).
            a = CL._run_config_digest(d / "taxjson.toml")
            (d / "taxjson.toml").write_text(_toml(2025))
            self.assertEqual(a, CL._run_config_digest(d / "taxjson.toml"))


# ------------------------------------------------------------ DISTINCT

class TestDistinctRetracted(unittest.TestCase):

    @rule("CA-XLIST-06")
    def test_update_retracts_unedited_distinct_lines(self):
        m = _master(SEC, CDR)
        rule_ = "DISTINCT QZD.US QZD.TO"
        old = (TB.render(m) + "\n## --- Depositary receipts the books hold "
               "(two securities) ---\n"
               f"{rule_}  # {TB.MARKER}{F4}:{TB.line_hash(rule_)}\n")
        tob = TB.parse_tobase(old)
        plan = TB.plan_update(m, tob, {"QZD.TO"}, "")
        self.assertEqual([(ln.rule, why) for ln, why in plan.retracted],
                         [(rule_, TB.DISTINCT_NOT_NEEDED)])
        self.assertEqual(TB.DISTINCT_NOT_NEEDED,
                         "not needed: look-alike listings are never joined")
        self.assertTrue(plan.changes)
        self.assertNotIn("DISTINCT", plan.new_text.split(TB.STAMP)[1])
        # An older marker without a check: the same.
        tob = TB.parse_tobase(old.replace(f":{TB.line_hash(rule_)}", ""))
        plan = TB.plan_update(m, tob, set(), "")
        self.assertEqual(len(plan.retracted), 1)

    def test_an_edited_distinct_line_is_kept_and_flagged(self):
        m = _master(SEC, CDR)
        rule_ = "DISTINCT QZD.US QZD.TO"
        old = (TB.render(m) + "\n"
               f"DISTINCT QZD.US QZD.V  # {TB.MARKER}{F4}:"
               f"{TB.line_hash(rule_)}\n"
               "DISTINCT QZOWN.US QZOWN.TO\n")
        plan = TB.plan_update(m, TB.parse_tobase(old), set(), "")
        self.assertEqual([(ln.rule, why) for ln, why in
                          plan.retracted_edited],
                         [("DISTINCT QZD.US QZD.V", TB.DISTINCT_NOT_NEEDED)])
        self.assertEqual(plan.retracted, [])
        self.assertIn("DISTINCT QZD.US QZD.V", plan.new_text)
        # The user's own DISTINCT line is kept as written.
        self.assertIn("DISTINCT QZOWN.US QZOWN.TO", plan.new_text)


# ------------------------------------------------------------ receipts

def _leg(sym, day, qty, name, *, ref="J1"):
    return XL.Leg("margin", "rbc_direct", sym, day, qty,
                  name=exact_name(name), raw_name=name,
                  journal="rbc_direct", ref=ref)


class TestReceiptsApart(unittest.TestCase):
    """The interlisted master's CDRs stay apart from their US share with
    no DISTINCT line: a journal-evidence join, a suggested pair and a
    MAP-GAP all refuse them (Canada); a US project reads no master."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        p = Path(self.td.name) / "interlisted.toml"
        p.write_text(
            '[meta]\nschema_version = 1\ngenerated = "2026-10-01"\n\n'
            f'[distinct.{F4}]\nname = "QZDELTA INC - CDR"\nkind = "cdr"\n'
            'ca = "QZD.TO"\nus = "QZD.US"\n')
        self.receipts = TB.receipt_pairs(True, path=p)
        self.us_receipts = TB.receipt_pairs(False, path=p)

    def tearDown(self):
        self.td.cleanup()

    @rule("CA-XLIST-06")
    def test_receipt_pairs(self):
        pair = frozenset(("QZD.TO", "QZD.US"))
        self.assertIn(pair, self.receipts)
        self.assertIn(frozenset(("QZD.V", "QZD.US")), self.receipts)
        self.assertIn("depositary receipt", self.receipts[pair])
        self.assertTrue(TB.receipt_pairs())         # the shipped master

    @rule("US-XLIST-05")
    def test_no_master_receipts_in_a_us_project(self):
        self.assertEqual(self.us_receipts, {})
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "taxjson.toml").write_text(
                '[settings]\ncountry = "usa"\nyear = 2025\n')
            self.assertEqual(TB.receipt_pairs_for(td), {})

    @rule("CA-XLIST-06")
    def test_journal_evidence_never_joins_a_receipt(self):
        name = "QZDELTA INC"       # the exports name both alike
        legs = [_leg("QZD.US", "2025-03-03", -10, name),
                _leg("QZD.TO", "2025-03-03", 10, name)]
        names = {"QZD.US": {exact_name(name)}, "QZD.TO": {exact_name(name)}}
        shown = {exact_name(name): name}
        res = XL.analyze(legs, names, shown, base_currency="CAD")
        self.assertEqual(len(res["joined"]), 1)     # without the master
        for g in legs:
            g.used = False
        refused = []
        res = XL.analyze(legs, names, shown, base_currency="CAD",
                         refused=refused, receipts=self.receipts)
        self.assertEqual((res["joined"], res["suggested"]), ([], []))
        self.assertEqual([p.extra.get("refused") for p in refused],
                         [XL.RECEIPT])
        self.assertIn("depositary receipt", refused[0].reason)

    @rule("CA-XLIST-06")
    def test_a_tt_journal_between_them_stops(self):
        from taxjson.lib import dated_events as DE
        j = DE.Journal("margin", "2025-03-03", "QZD.US", "QZD.TO", 10.0,
                       "inputs/margin/x.tt:1",
                       line="JOURNAL 2025-03-03 QZD.US QZD.TO 10",
                       pair="tt1")
        try:
            legs = DE.journal_legs([j])
        except Exception as e:                       # noqa: BLE001
            self.skipTest(f"journal legs need more fields: {e}")
        name = "QZDELTA INC"
        names = {"QZD.US": {exact_name(name)}, "QZD.TO": {exact_name(name)}}
        refused = []
        res = XL.analyze(legs, names, {exact_name(name): name},
                         base_currency="CAD", declared=[j], refused=refused,
                         receipts=self.receipts)
        self.assertEqual(res["joined"], [])
        self.assertEqual([p.extra.get("refused") for p in refused],
                         [XL.UNPROVEN])
        self.assertIn("depositary receipt", refused[0].reason)

    @rule("CA-XLIST-06")
    def test_suggest_and_map_gap_keep_them_apart(self):
        from taxjson.lib import map_hygiene as MH
        name = ("QZDELTA", "INC")
        names = {"QZD.US": {name}, "QZD.TO": {name}}
        self.assertEqual(XL.shown_apart("QZD.US", "QZD.TO", names), "")
        self.assertIn("depositary receipt", XL.shown_apart(
            "QZD.US", "QZD.TO", names, self.receipts))
        kind, why = MH.pair_verdict("QZD.US", "QZD.TO", names,
                                    {name: "QZDELTA INC"}, self.receipts)
        self.assertEqual(kind, "receipt")
        self.assertEqual(MH.pair_verdict("QZD.US", "QZD.TO", names,
                                         {name: "QZDELTA INC"})[0], "same")


if __name__ == "__main__":
    unittest.main()
