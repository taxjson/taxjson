"""House output style (docs/output-style.md) for the planning commands:
wash-radar, harvest, buy-check, sell-check, scan, watch and fx-cash.

Each runs on the synthetic style projects (tests/_style.py) as a pipe
would (width 100) and must pass out.lint; the phrases a reader needs are
compared on flattened text. The --json documents, the radar's
reports/wash_radar_*.json and the fx-cash last line the checklist reads
are machine output and keep their shapes."""
import datetime
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _style import Project, assert_styled, project

from taxjson.lib import out


def _flat(text: str) -> str:
    return " ".join(text.split())


_SFX = {"canada": ".TO", "usa": ".US"}


def _copy(country: str) -> Project:
    """A writable copy of the built style project (watch writes state,
    harvest needs every held symbol in the price cache)."""
    src = project(country)
    td = tempfile.mkdtemp(prefix="taxjson_style_c_")
    dst = Path(td) / country
    shutil.copytree(src.root, dst)
    return Project(dst, country)


def _price_every_holding(p: Project) -> None:
    """Add a synthetic cache price for every open position harvest will
    look up (the shared cache keys the country's own suffix only)."""
    today = datetime.date.today().isoformat()
    cache = p.root / "work" / ".price_cache.json"
    doc = json.loads(cache.read_text())
    for g in (p.root / "work").glob("*_gains*.json"):
        try:
            inv = json.loads(g.read_text()).get("inventory") or []
        except (OSError, ValueError, AttributeError):
            continue
        for h in inv:
            sym = str(h.get("symbol") or "")
            if sym and sym not in doc:
                cur = "CAD" if sym.endswith(".TO") else "USD"
                doc[sym] = {"price": 0.05 if len(sym) > 12 else 20.0,
                            "asof": today, "source": "yfinance",
                            "currency": cur}
    cache.write_text(json.dumps(doc))


class TestWashRadarStyle(unittest.TestCase):
    def test_layout_both_countries(self):
        for country in ("canada", "usa"):
            for args in (("wash-radar",),
                         ("wash-radar", "--date", "2024-11-25", "--all")):
                with self.subTest(country=country, args=args):
                    r = project(country).run(*args)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    assert_styled(self, r.stdout)
                    lines = r.stdout.splitlines()
                    self.assertTrue(lines[0].startswith("WASH RADAR — "),
                                    lines[0])
                    self.assertIn("DEFINITIONS", lines)
                    # The scope paragraph closes the report.
                    self.assertIn("these verdicts cover this project's "
                                  "accounts only", _flat(r.stdout))
                    # No pipe table, no retired NOTE: inside advisories.
                    self.assertNotIn(" | ", r.stdout)
                    self.assertNotIn("NOTE:", r.stdout)

    def test_sections_show_counts_and_rows(self):
        r = project("canada").run("wash-radar", "--date", "2024-11-25",
                                  "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        flat = _flat(r.stdout)
        self.assertIn("VIOLATION — superficial loss; act to rescue the "
                      "loss (", flat)
        self.assertIn("TICKER TAXABLE SHELTERED CLEARS", flat)
        self.assertIn("(none)", r.stdout)

    def test_json_keeps_the_advisory_text(self):
        # The --json document is the radar's machine output: the
        # advisory keeps its "<CATEGORY>: " lead (the text view strips
        # it under the section heading).
        r = project("canada").run("wash-radar", "--json", "--all",
                                  "--date", "2024-11-25")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        rows = [row for s in doc["sections"] for row in s["rows"]
                if row["category"]]
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(row["advisory"].startswith(
                row["category"] + ":"), row["advisory"])

    def test_captured_report_is_unwrapped(self):
        # reports/wash_radar_*.rpt is captured by the run: never wrapped,
        # so each advisory stays one line there.
        p = project("canada")
        rpts = sorted((p.root / "reports").glob("wash_radar_*.rpt"))
        self.assertTrue(rpts)
        text = rpts[0].read_text()
        self.assertTrue(text.startswith("WASH RADAR — "), text[:80])
        self.assertTrue(any(len(ln) > 100 for ln in text.splitlines()))


class TestHarvestStyle(unittest.TestCase):
    def test_layout_both_countries(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                p = _copy(country)
                self.addCleanup(shutil.rmtree, p.root.parent, True)
                _price_every_holding(p)
                r = p.run("harvest", "--no-ibkr", "--options")
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_styled(self, r.stdout)
                assert_styled(self, r.stderr)
                lines = r.stdout.splitlines()
                self.assertTrue(lines[0].startswith("HARVEST — "), lines)
                self.assertIn("COLUMNS", lines)
                flat = _flat(r.stdout)
                self.assertIn("HARVESTABLE LOSSES", flat)
                # Too wide as one table: split into tables that fit,
                # each led by ACCOUNT and SYMBOL — no column is lost.
                hdrs = " ".join(ln for ln in lines
                                if ln.startswith("ACCOUNT"))
                for col in ("COST/SH", "EXIT@", "TX_ADD", "UNREALIZED",
                            "ADVISORY"):
                    self.assertIn(col, hdrs)
                # The progress line is a note on stderr.
                self.assertIn("Info: pricing ", r.stderr)

    def test_unwrapped_keeps_every_column(self):
        p = _copy("usa")
        self.addCleanup(shutil.rmtree, p.root.parent, True)
        _price_every_holding(p)
        r = p.run("harvest", "--no-ibkr", "--options", TAXJSON_WIDTH="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        hdr = next(ln for ln in r.stdout.splitlines()
                   if ln.startswith("ACCOUNT"))
        for col in ("COST/SH", "EXIT@", "PCT", "TX_ADD", "SH_ADD",
                    "ADVISORY"):
            self.assertIn(col, hdr)
        self.assertEqual(sum(1 for ln in r.stdout.splitlines()
                             if ln.startswith("ACCOUNT")), 1)


class TestBuySellCheckStyle(unittest.TestCase):
    def test_layout_both_countries(self):
        for country in ("canada", "usa"):
            sfx = _SFX[country]
            for args in (("buy-check", f"QZQ{sfx}", f"SAMPA{sfx}"),
                         ("sell-check", f"SAMPA{sfx}", f"QZQ{sfx}")):
                with self.subTest(country=country, args=args):
                    r = project(country).run(*args)
                    self.assertIn(r.returncode, (0, 1), r.stderr)
                    assert_styled(self, r.stdout)
                    lines = r.stdout.splitlines()
                    # SYMBOL: VERDICT, its details as `- ` items, a
                    # blank line between symbols, the scope last.
                    self.assertRegex(lines[0], r"^\S+: [A-Z*]+$")
                    self.assertTrue(lines[1].startswith("- "), lines[1])
                    self.assertEqual(
                        sum(1 for ln in lines if not ln.strip()), 2)
                    self.assertIn("these verdicts cover this project's "
                                  "accounts only", _flat(r.stdout))

    def test_json_unchanged_shape(self):
        r = project("canada").run("sell-check", "SAMPA.TO", "--json")
        doc = json.loads(r.stdout)
        self.assertEqual(sorted(doc), ["results", "scope_note"])
        self.assertTrue(doc["scope_note"].startswith("Scope: "))


class TestScanStyle(unittest.TestCase):
    def test_layout_both_countries(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = project(country).run("scan")
                self.assertIn(r.returncode, (0, 1), r.stderr)
                assert_styled(self, r.stdout)
                lines = r.stdout.splitlines()
                self.assertTrue(lines[0].startswith("SCAN — "), lines[0])
                if r.returncode:
                    self.assertIn("FINDINGS", lines)
                    self.assertRegex(lines[-1], r"^\d+ finding\(s\)\.$")
                    self.assertTrue(any(ln.startswith("1. ")
                                        for ln in lines))
                else:
                    self.assertEqual(lines[-1], "No findings — clean scan.")


class TestWatchStyle(unittest.TestCase):
    def test_baseline_then_changes(self):
        p = _copy("canada")
        self.addCleanup(shutil.rmtree, p.root.parent, True)
        state = p.root / "w.json"
        r = p.run("watch", "--no-ibkr", "--state", str(state))
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_styled(self, r.stdout)
        self.assertTrue(r.stdout.startswith("WATCH — baseline recorded"))
        # Pretend every tracked ticker was LOCKED last time: the report
        # lists each change as a `- ` item.
        doc = json.loads(state.read_text())
        for rec in doc["radar"].values():
            rec["category"] = "LOCKED"
            rec["advisory"] = "LOCKED: " + "a long synthetic advisory " * 8
        state.write_text(json.dumps(doc))
        r = p.run("watch", "--no-ibkr", "--state", str(state))
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_styled(self, r.stdout)
        lines = r.stdout.splitlines()
        self.assertIn("change(s) since the last run", lines[0])
        self.assertTrue(lines[2].startswith("- "), lines[:4])

    def test_render_report_wraps_items(self):
        from taxjson.bin.taxjson_watch import render_report
        text = render_report(
            [{"line": "AAA.TO: NEW LOCKED — " + "word " * 40
              + "NOTE: a long call on these shares was bought."}],
            "2026-10-01", since="2026-09-01", scope="Scope: x " * 30,
            width_=100)
        self.assertEqual(out.lint(text), [])
        self.assertIn("- Info: a long call", text)
        self.assertNotIn("NOTE:", text)
        with out.unwrapped():
            one = render_report([{"line": "AAA.TO: " + "word " * 40}],
                                "2026-10-01")
        self.assertEqual(len(one.splitlines()), 3)


class TestFxCashStyle(unittest.TestCase):
    def test_layout_both_countries(self):
        for country in ("canada", "usa"):
            for args in (("fx-cash",), ("fx-cash", "--events")):
                with self.subTest(country=country, args=args):
                    r = project(country).run(*args)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    assert_styled(self, r.stdout)
                    # The NOT RELIABLE line first (CA-FX-07 / US-FX-03).
                    self.assertTrue(r.stdout.startswith(
                        "FX on foreign cash: "))
                    self.assertIn("\n\nFX GAINS ON CASH — ", r.stdout)
                    self.assertNotIn("WARNING:", r.stdout)

    def test_first_line_is_the_caveat(self):
        # `taxjson fx-cash` says first, unwrapped, that the default
        # ledger's figure is not for filing (the checklist reads --json).
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                r = project(country).run("fx-cash", TAXJSON_WIDTH="0")
                self.assertEqual(r.returncode, 0, r.stderr)
                first = r.stdout.splitlines()[0]
                self.assertTrue(first.startswith(
                    "FX on foreign cash: "), first)
                self.assertTrue(
                    "NOT RELIABLE for " in first
                    or "no foreign-currency cash flow" in first, first)


class TestWashScopeHelpers(unittest.TestCase):
    def test_scope_lines_wrap_and_keep_the_lead(self):
        from taxjson.lib.wash_scope import scope_lines, scope_note
        for c in ("canada", "usa"):
            lines = scope_lines(c, 100)
            self.assertTrue(lines[0].startswith("Scope: "))
            self.assertTrue(all(len(ln) <= 100 for ln in lines))
            self.assertEqual(" ".join(lines), scope_note(c))
            self.assertEqual(scope_lines(c, 0), [scope_note(c)])

    def test_advisory_parts_split_notes(self):
        from taxjson.lib.wash_scope import advisory_lines, advisory_parts
        body, notes = advisory_parts(
            "Sell 10 by 2026-01-02. NOTE: a call was bought. NOTE: two.")
        self.assertEqual(body, "Sell 10 by 2026-01-02.")
        self.assertEqual(notes, ["a call was bought.", "two."])
        body, notes = advisory_parts("AAA.TO: NOTE: a call was bought.")
        self.assertEqual((body, notes), ("AAA.TO: note: a call was bought.",
                                         []))
        lines = advisory_lines("x " * 80 + "NOTE: y", 100, "- ", "  ")
        self.assertTrue(lines[0].startswith("- x"))
        self.assertTrue(lines[1].startswith("  x"))
        self.assertEqual(lines[-1], "- Info: y")
        # Nothing wraps (captured): the lower-case captured label.
        self.assertEqual(advisory_lines("x NOTE: y", 0, "- ", "  ")[-1],
                         "- note: y")


class TestHarvestOfflineRefusal(unittest.TestCase):
    def test_headline_and_details(self):
        from taxjson.lib import price_chain
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "100"}), \
                self.assertRaises(SystemExit) as cm:
            price_chain.fetch_prices({"ZZQ.US": "ZZQ"},
                                     cache_path=Path(td) / "c.json",
                                     use_ibkr=False, offline=True)
        text = str(cm.exception)
        self.assertTrue(text.startswith("Error: TAXJSON_OFFLINE "
                                        "is set"), text)
        self.assertEqual(out.lint(text), [])


if __name__ == "__main__":
    unittest.main()
