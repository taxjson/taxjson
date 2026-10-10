"""The tobase.map review fixes (F1-F10) and the v0.27.0 additions (G1,
G2): reused tickers, retractions, edits and opt-outs, the T1135 country
of a foreign issuer, sightings, ETF currency lines, covered ticker.map
lines.

Every fixture is SYNTHETIC: invented tickers (QZ*), invented FIGIs
(BBG0000000xx), invented names, amounts under 1,000 a unit.
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, projects_both

from taxjson.lib import tobase_map as TB
from test_tobase_map import F1, F2, F3, SEC, _master, _project

F5, F6 = "BBG000000Q05", "BBG000000Q06"
_ACCTS = '[accounts.margin]\ntype = "taxable"\n'


def _line(rule_text, figi, extra=""):
    return f"{rule_text}  # master:{figi}:{TB.line_hash(rule_text)}{extra}"


# ------------------------------------------------------------ F1 reused

class TestReusedTicker(unittest.TestCase):

    def test_the_line_names_the_reuse(self):
        sec = {F3: dict(SEC[F3], history=[{"listing": "QZG.US", "kind": "us",
                                           "until": "2025-06-30",
                                           "reused_by": "QZ OTHER"}])}
        text = TB.render(_master(sec))
        self.assertIn('reused_by="QZ OTHER"', text)
        tob = TB.parse_tobase(text)
        self.assertEqual(tob.lines[0].reused, "QZ OTHER")

    @rule("CA-XLIST-06")
    def test_a_reused_ticker_warns_on_every_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase=_line(
                "TOBASE QZG.US QZG.TO", F3,
                ' until=2025-06-30 ended=QZG.US reused_by="QZ OTHER"') + "\n")
            found = TB.until_findings(root, {"QZG.US": ["2024-03-01",
                                                        "2025-08-15"]})
            self.assertEqual([(f.symbol, f.dates) for f in found],
                             [("QZG.US", ["2024-03-01", "2025-08-15"])])
            head, details = TB.until_message(found[0])
            self.assertIn("names QZ OTHER", head)
            self.assertIn("DISTINCT QZG.US QZG.TO", details[0])


# ------------------------------------------------------------ F2 retracted

SAND = {F3: {"name": "QZGAMMA", "kind": "share", "share_class_figi": F3,
             "ca": ["QZG.TO"], "us": [], "us_exchange": [],
             "until": "2025-06-30", "first_seen": "2026-01-01",
             "history": [{"listing": "QZGS.US", "kind": "us",
                          "until": "2025-06-30"}],
             "added": [{"listing": "QZGS.US", "since": "2026-02-01"}],
             "retracted": [{"listing": "QZG.US", "reason": "another "
                            "company's ADR", "since": "2026-02-01"}]}}


class TestRetracted(unittest.TestCase):

    def test_unedited_line_removed_edited_flagged(self):
        m = _master(SAND, generated="2026-02-01")
        for old in (
                # This version's marker, and an older one without check.
                _line("TOBASE QZG.US QZG.TO", F3, " until=2025-06-30"),
                f"TOBASE QZG.US QZG.TO  # master:{F3} until=2025-06-30"):
            tob = TB.parse_tobase(f"{TB.STAMP} 2026-01-01\n{old}\n")
            plan = TB.plan_update(m, tob, set(), "")
            self.assertEqual([(ln.rule, w) for ln, w in plan.retracted],
                             [("TOBASE QZG.US QZG.TO",
                               "retracted (QZG.US: another company's ADR)")])
            self.assertNotIn("TOBASE QZG.US", plan.new_text)
            self.assertEqual([g.rule for g in plan.added],
                             ["TOBASE QZGS.US QZG.TO"])
        # The user edited the line (another base): kept, flagged.
        tob = TB.parse_tobase(f"{TB.STAMP} 2026-01-01\n" + _line(
            "TOBASE QZG.US QZG.TO", F3).replace("TOBASE QZG.US QZG.TO",
                                                "TOBASE QZG.US QZGX.TO")
            + "\n")
        plan = TB.plan_update(m, tob, set(), "")
        self.assertEqual(plan.retracted, [])
        self.assertEqual([ln.rule for ln, _w in plan.retracted_edited],
                         ["TOBASE QZG.US QZGX.TO"])
        self.assertIn("TOBASE QZG.US QZGX.TO", plan.new_text)

    def test_cli_removes_a_shipped_retraction(self):
        # Any listing the shipped master retracts: an older line naming
        # it (no check in its marker) is removed by --write.
        m = TB.load_master()
        hit = next(((sc, e, lst) for sc, e in m.security.items()
                    for lst in TB.retracted_listings(e)
                    if TB.home_listing(e) != lst), None)
        if hit is None:
            self.skipTest("the shipped master retracts nothing")
        sc, e, lst = hit
        old = f"TOBASE {lst} {TB.home_listing(e)}"
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase=(
                f"{TB.STAMP} {m.generated}\n{old}  # master:{sc}\n"))
            r = cli(root, "update-tobase-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Retracted", r.stdout)
            self.assertNotIn(old, (root / "tobase.map").read_text())


# ------------------------------------------------------------ F4 edits

class TestUserEdits(unittest.TestCase):

    def test_reversed_edit_is_kept_and_suppresses_the_master_line(self):
        # (a) + (b): the user turned the master's line around.
        rev = _line("TOBASE QZAB.US QZA.TO", F1).replace(
            "TOBASE QZAB.US QZA.TO", "TOBASE QZA.TO QZAB.US")
        tob = TB.parse_tobase(f"{TB.STAMP} 2026-10-01\n{rev}\n")
        plan = TB.plan_update(_master(SEC), tob, set(), "")
        self.assertEqual(plan.retracted, [])
        self.assertEqual([ln.rule for ln in plan.edited],
                         ["TOBASE QZA.TO QZAB.US"])
        self.assertNotIn("TOBASE QZAB.US QZA.TO", [g.rule for g in
                                                   plan.added])
        self.assertNotIn("TOBASE QZAB.US QZA.TO", plan.new_text)
        self.assertIn("TOBASE QZA.TO QZAB.US", plan.new_text)
        # Same FIGI and FROM, another base: no duplicate either.
        ed = _line("TOBASE QZAB.US QZA.TO", F1).replace(
            "TOBASE QZAB.US QZA.TO", "TOBASE QZAB.US QZZ.TO")
        plan = TB.plan_update(_master(SEC), TB.parse_tobase(ed + "\n"),
                              set(), "")
        self.assertEqual(plan.added and [g.rule for g in plan.added
                                         if g.a == "QZAB.US"], [])

    def test_internal_conflict_does_not_blame_ticker_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase=(
                _line("TOBASE QZAB.US QZA.TO", F1) + "\n"
                + "TOBASE QZAB.US QZZ.TO\n"))
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n' + _ACCTS)
            (root / "inputs" / "margin" / "m.tt").write_text(
                "BUYSELL 2025-02-03 10:00:00 QZA.TO 10 CAD 5.00 50.00 0\n")
            r = cli(root, "run", "--no-input")
            out = " ".join((r.stdout + r.stderr).split())
            self.assertIn("another tobase.map line", out)
            self.assertNotIn("ticker.map decides", out)

    def test_comments_and_other_lines_are_kept(self):
        text = (f"{TB.STAMP} 2026-10-01\n"
                f"# my note on alpha\n"
                + _line("TOBASE QZAB.US QZA.TO", F1) + " checked 2026\n"
                "QUOTE QZA.TO QZA.TO\n"
                "# a closing note\n")
        tob = TB.parse_tobase(text)
        plan = TB.plan_update(_master(SEC), tob, set(), "")
        new = plan.new_text
        self.assertIn("# my note on alpha\nTOBASE QZAB.US QZA.TO", new)
        self.assertRegex(new, r"TOBASE QZAB.US QZA.TO  # master:\S+ "
                              r"checked 2026\n")
        self.assertIn("QUOTE QZA.TO QZA.TO", new)
        self.assertIn("# a closing note", new)
        self.assertEqual(plan.others, ["QUOTE QZA.TO QZA.TO"])
        # Idempotent: a second update changes nothing.
        again = TB.plan_update(_master(SEC), TB.parse_tobase(new), set(), "")
        self.assertEqual(again.new_text, new)

    def test_a_deleted_master_line_stays_deleted(self):
        sec = {k: dict(v, first_seen="2026-01-01") for k, v in SEC.items()}
        m = _master(sec, generated="2026-10-01")
        full = TB.render(m)
        # The user deletes the QZBBF line.
        kept = "\n".join(ln for ln in full.splitlines()
                         if "QZBBF" not in ln) + "\n"
        plan = TB.plan_update(m, TB.parse_tobase(kept), set(), "")
        self.assertEqual([g.rule for g in plan.removed_now],
                         ["TOBASE QZBBF.US QZB.TO"])
        self.assertEqual(plan.added, [])
        self.assertIn(f"# removed: master:{F2} TOBASE QZBBF.US QZB.TO",
                      plan.new_text)
        # The next update (a newer master) keeps it out.
        m2 = _master(sec, generated="2026-12-01")
        plan2 = TB.plan_update(m2, TB.parse_tobase(plan.new_text), set(), "")
        self.assertEqual(plan2.added, [])
        self.assertEqual([g.rule for g in plan2.opted_out],
                         ["TOBASE QZBBF.US QZB.TO"])
        self.assertNotIn("\nTOBASE QZBBF.US", plan2.new_text)
        # Re-add: delete the `# removed:` line, and the line is new
        # again only for a listing added since; so comment-free files
        # made by an older master see it as deleted again: re-adding is
        # copying the line back (documented). A commented-out line is an
        # opt-out too.
        com = full.replace("TOBASE QZBBF.US", "# TOBASE QZBBF.US")
        plan3 = TB.plan_update(m2, TB.parse_tobase(com), set(), "")
        self.assertEqual([g.rule for g in plan3.opted_out],
                         ["TOBASE QZBBF.US QZB.TO"])
        # A listing new since the file's master is added, never taken
        # for a deletion.
        sec2 = dict(sec)
        sec2[F2] = dict(sec[F2], us_otc=["QZBBF.US", "QZBBX.US"],
                        added=[{"listing": "QZBBX.US",
                                "since": "2026-11-01"}])
        plan4 = TB.plan_update(_master(sec2, generated="2026-11-01"),
                               TB.parse_tobase(full), set(), "")
        self.assertEqual([g.rule for g in plan4.added],
                         ["TOBASE QZBBX.US QZB.TO"])


# ------------------------------------------------------------ F5 T1135

class TestT1135Country(unittest.TestCase):

    @rule("CA-RPT-02")
    @rule("CA-XLIST-06")
    def test_domicile_country_and_override_keys_follow_tobase(self):
        sec = {F5: {"name": "QZ BERMUDA", "kind": "unit",
                    "share_class_figi": F5, "domicile": "BM",
                    "ca": ["QZBM.UN.TO"], "us": ["QZBM.US"],
                    "us_exchange": ["NYSE"]}}
        lines = TB.render(_master(sec))
        self.assertIn("TOBASE QZBM.UN.TO QZBM.US", lines)
        self.assertIn("country=BMU", lines)
        tt = ("BUYSELL 2025-02-03 10:00:00 QZBM.UN.TO 200 CAD 600.00 "
              "120000.00 0\n"
              "BUYSELL 2025-02-03 10:00:00 QZGB.TO 100 CAD 400.00 "
              "40000.00 0\n")
        files = {"inputs/margin/m.tt": tt,
                 "tobase.map": lines + _line("TOBASE QZGB.TO QZGB.US", F6)
                 + "\n",
                 "ticker.map": "T1135 QZGB.TO GBR\nT1135 QZNONE.TO GBR\n"}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(Path(td), accounts=_ACCTS,
                                 files=files)["canada"]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            t = cli(root, "t1135", "--json")
        self.assertEqual(t.returncode, 0, t.stderr[-1500:])
        prop = {p["symbol"]: p for p in json.loads(t.stdout)["properties"]}
        self.assertEqual(prop["QZBM.US"]["country"], "BMU")
        # The user's line for QZGB.TO follows the TOBASE to QZGB.US.
        self.assertEqual(prop["QZGB.US"]["country"], "GBR")
        err = " ".join(t.stderr.split())
        self.assertIn("`T1135 QZNONE.TO` matches no symbol", err)
        self.assertNotIn("`T1135 QZGB.TO`", err)


# ------------------------------------------------------------ F3 sightings

class TestSightings(unittest.TestCase):

    def test_a_tobase_target_is_no_sighting(self):
        # The books hold QZOR.US (a US company); tobase.map pools an OTC
        # listing under a TSX Venture junior QZOR.TO: no pair to verify.
        files = {"inputs/margin/m.tt": (
            "BUYSELL 2025-02-03 10:00:00 QZOR.US 10 USD 50.00 500.00 0\n"),
            "tobase.map": _line("TOBASE QZORF.US QZOR.TO", F1) + "\n",
            "ticker.map": ""}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(Path(td), accounts=_ACCTS,
                                 files=files)["canada"]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            s = cli(root, "ticker-map", "--suggest", "--json")
            self.assertEqual(s.returncode, 0, s.stderr[-800:])
            doc = json.loads(s.stdout)
            self.assertEqual(doc["verify"], [])
            self.assertEqual(doc["suggestions"], [])
            from taxjson.lib import map_hygiene as MH
            self.assertEqual(MH.map_gaps(root)[0], [])


# ------------------------------------------------------------ F6 events

class TestRenameAttribution(unittest.TestCase):

    def test_tobase_lines_are_tobase_maps_and_same_base_is_quiet(self):
        from taxjson.bin.taxjson_ticker_map import _parse_map_file
        from taxjson.lib import dated_events as DE
        from taxjson.lib.renames import DatedRename, SOURCE_TT
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase=(
                _line("TOBASE QZOLD.US QZA.TO", F1) + "\n"
                + _line("TOBASE QZNEW.US QZA.TO", F1) + "\n"
                + _line("TOBASE QZX.US QZX.TO", F2) + "\n"))
            tmap = _parse_map_file(root / "ticker.map")[0]

            def warns(old, new):
                decl = DE.Declarations()
                decl.tt_declared = [DatedRename(
                    old, new, "2025-05-01", where="m.tt:1",
                    line=f"RENAME 2025-05-01 {old} {new}",
                    source=SOURCE_TT, account="margin")]
                DE.check_against_map(decl, tmap)
                return decl.warnings
            self.assertEqual(warns("QZOLD.US", "QZNEW.US"), [])
            w = " ".join(warns("QZX.US", "QZY.US"))
            self.assertIn("tobase.map (the interlisted master's pair) also "
                          "joins QZX.US", w)
            self.assertNotIn("ticker.map also joins", w)


# ------------------------------------------------------------ F7, F8

class TestSmall(unittest.TestCase):

    def test_redact_carries_tobase_map(self):
        from taxjson.bin import taxjson_redact as R
        self.assertIn("tobase.map", R._PROJECT_FILES)

    def test_write_makes_ticker_map_when_tobase_map_is_current(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            r = cli(root, "update-tobase-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            (root / "ticker.map").unlink()
            r = cli(root, "update-tobase-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((root / "ticker.map").is_file())
            self.assertIn("Wrote an empty ticker.map", r.stdout)


# ------------------------------------------------------------ G1 ETF lines

class TestEtfCurrencyLines(unittest.TestCase):

    def test_usd_line_renders_in_its_section(self):
        sec = {"BBG000000F01": {"name": "QZ FUND", "kind": "fund",
                                "share_class_figi": "BBG000000F01",
                                "ca": ["QZF.TO", "QZF.U.TO"], "us": [],
                                "us_exchange": []}}
        g = TB.master_lines(_master(sec))
        self.assertEqual([(x.rule, x.section) for x in g],
                         [("TOBASE QZF.U.TO QZF.TO", TB.SECTION_CURRENCY)])

if __name__ == "__main__":
    unittest.main()
