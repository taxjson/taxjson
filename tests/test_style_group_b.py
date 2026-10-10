"""House output style (docs/output-style.md) for the filing and planning
commands: sum / estimate, amt, instalments, form-export, t1135,
carryover, reconcile-slips, option-boundary, close-year, check-filed
and handoff — on the synthetic style projects (tests/_style.py), at
width 100 as a pipe would show them. The commands both countries run
are in test_style_smoke.TestConvertedCommands.CASES; this file holds
the Canada-only ones, the commands that write (on a copy) and the
diagnostics."""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from _style import Project, assert_console, assert_styled, project


def _copy(country: str, extra_toml: str = "") -> Project:
    """A writable copy of the built style project (`extra_toml`
    appended to its taxjson.toml)."""
    src = project(country)
    td = tempfile.mkdtemp(prefix="taxjson_style_b_")
    root = Path(td) / country
    shutil.copytree(src.root, root)
    if extra_toml:
        p = root / "taxjson.toml"
        p.write_text(p.read_text() + "\n" + extra_toml)
    p = Project(root, country)
    p._td = td
    return p


def _flat(text: str) -> str:
    return " ".join(text.split())


class TestCanadaOnlyViews(unittest.TestCase):
    def test_t1135_option_boundary_amt(self):
        p = project("canada")
        for args in (("t1135",), ("option-boundary",),
                     ("amt", "--province", "ON"),
                     ("estimate", "--province", "ON", "--verbose")):
            with self.subTest(args=args):
                r = p.run(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_styled(self, r.stdout)
                assert_styled(self, r.stderr)

    def test_t1135_lists_property_notes_under_the_table(self):
        out = project("canada").run("t1135").stdout
        self.assertNotIn(" | ", out)
        self.assertIn("- BTC, ETH: crypto — check where held", out)
        self.assertTrue(out.splitlines()[-1].startswith("- Not tax advice"))

    def test_instalments(self):
        toml = project("canada").root.joinpath("taxjson.toml").read_text()
        if re.search(r"^\[(instalments|estimate)\]", toml, re.M):
            self.skipTest("the style project configures these tables")
        p = _copy("canada", '[instalments]\nbasis = "current_year"\n'
                  'paid = [{ date = "2024-03-15", amount = 500 }]\n'
                  '\n[estimate]\nother_income = 250000\n')
        self.addCleanup(shutil.rmtree, p._td, True)
        if not re.search(r"^province\s*=", toml, re.M):
            t = p.root / "taxjson.toml"
            t.write_text(re.sub(r'^(country\s*=.*)$', r'\1\nprovince = "ON"',
                                t.read_text(), count=1, flags=re.M))
        r = p.run("instalments", "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_styled(self, r.stdout)
        assert_styled(self, p.run("instalments").stdout)
        self.assertIn("Not modelled - CPP/EI payable", _flat(r.stdout))
        self.assertNotIn("NOTE:", r.stdout)
        a = p.run("amt")
        self.assertEqual(a.returncode, 0, a.stderr)
        assert_styled(self, a.stdout)

    def test_missing_instalments_table_is_a_headline(self):
        p = project("canada")
        if "[instalments]" in p.root.joinpath("taxjson.toml").read_text():
            self.skipTest("the style project configures [instalments]")
        r = p.run("instalments")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith(
            "Error: no [instalments] section"))
        assert_styled(self, r.stderr)


class TestBothCountries(unittest.TestCase):
    def test_estimate(self):
        # The assumptions list: --details or --verbose (Essentials first).
        for country, args in (("canada", ("estimate", "--province", "ON",
                                          "--details")),
                              ("usa", ("estimate", "--verbose"))):
            with self.subTest(country=country):
                r = project(country).run(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_styled(self, r.stdout)
                assert_styled(self, r.stderr)
                self.assertIn("  Assumes:\n  - ", r.stdout)

    def test_sum_warning_is_a_headline_with_details(self):
        r = project("canada").run("sum")
        lines = r.stderr.splitlines()
        self.assertTrue(lines[0].startswith("Warning: 2 "
                                            "position(s) sold"), r.stderr)
        # Its details flush-left (docs/output-style.md, Messages).
        self.assertTrue(all(not ln[:1].isspace() for ln in lines[1:]))
        assert_styled(self, r.stderr)
        assert_console(self, r.stderr)

    def test_sum_return_block_fits(self):
        r = project("canada").run("sum")
        block = r.stdout.split("FOR THE RETURN")[1]
        self.assertIn("RETURN ", block)
        self.assertIn("- Per-security rows: `taxjson form-export`", block)
        self.assertNotIn("NOTE:", r.stdout)

    def test_form_export_row_notes_under_the_table(self):
        out = project("canada").run("form-export").stdout
        self.assertNotIn(" | ", out)
        self.assertIn("- NVDA.US: superficial loss", out)
        # The synthetic rates of tests/_hermetic (USD→CAD 1.35).
        self.assertIn("Line 13200 (gain/loss): -351.55", _flat(out))

    def test_reconcile_slips(self):
        for country, slip in (("canada", "inputs/slips/t5008.csv"),
                              ("usa", "inputs/slips/1099b.csv")):
            with self.subTest(country=country):
                r = project(country).run("reconcile-slips", slip)
                self.assertIn(r.returncode, (0, 1), r.stderr)
                assert_styled(self, r.stdout)
                assert_styled(self, r.stderr)
                self.assertIn("\nNOTES\n- ", r.stdout)

    def test_close_year_check_filed_handoff(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                p = _copy(country)
                self.addCleanup(shutil.rmtree, p._td, True)
                c = p.run("close-year", "--force")
                self.assertEqual(c.returncode, 0, c.stderr)
                assert_styled(self, c.stdout)
                assert_styled(self, c.stderr)
                self.assertIn("CLOSED 2024 — wash-adjusted", c.stdout)
                k = p.run("check-filed")
                self.assertEqual(k.returncode, 0, k.stderr)
                assert_styled(self, k.stdout)
                self.assertIn("filed 2024: OK", k.stdout)
                h = p.run("handoff", "--prior", "filed/2024.json")
                self.assertIn(h.returncode, (0, 1), h.stderr)
                assert_styled(self, h.stdout)
                self.assertIn("Record: filed/2024.json", h.stdout)
                # A drifted lock: the DRIFTED marker stays on the
                # warning's first line, the detail lines under it.
                lock = p.root / "filed" / "2024.json"
                doc = json.loads(lock.read_text())
                acct = sorted(doc["accounts"])[-1]
                doc["accounts"][acct]["realized"] += 100.0
                lock.write_text(json.dumps(doc))
                d = p.run("check-filed")
                self.assertEqual(d.returncode, 1)
                first = d.stderr.splitlines()[0]
                self.assertEqual(first, "Warning: "
                                        "filed 2024 DRIFTED vs 2024.json")
                assert_styled(self, d.stderr)
                # As the checklist reads it (width 0): the GNU bytes.
                d = p.run("check-filed", TAXJSON_WIDTH=0)
                self.assertEqual(d.stderr.splitlines()[0],
                                 "taxjson check-filed: warning: "
                                 "filed 2024 DRIFTED vs 2024.json")


if __name__ == "__main__":
    unittest.main()
