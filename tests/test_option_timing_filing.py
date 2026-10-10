"""Written-option timing across the filing commands (2026-09 filing audit):
carryover honours the project's option timing; the grant-timing `since`
is written by `init` and warned about when unset; option-boundary flags a
locked year kept on transition close timing and an expired contract with
no expiry row, and refuses the all-clear when it checked nothing."""
import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.lib.core import TaxTransaction
from taxjson.lib.option_boundary import straddling
from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent
OPT = "ZZZ260116C00050000.US"


def T(action, date, sym, qty, net, settle=None, price=None):
    return TaxTransaction(action=action, date=date, symbol=sym, quantity=qty,
                          net_amount=net, currency="CAD", time="10:00:00",
                          date_settle=settle or date, account="55500001",  # pii-ok: synthetic id
                          price=abs(net / qty) if price is None else price)


# Written 2025-12-15 for 399, bought back 2026-01-10 for 101. Grant
# timing: +399 in 2025, -101 in 2026. Plus a -1,000 share loss in 2025.
BOOK = [T("BUYSELL", "2025-02-03", "YYY.US", 100, -2000.0),
        T("BUYSELL", "2025-06-03", "YYY.US", -100, 1000.0),
        T("BUYSELL", "2025-12-15", OPT, -1, 399.0, settle="2025-12-16"),
        T("BUYSELL", "2026-01-10", OPT, 1, -101.0, settle="2026-01-12")]


def _project(td, extra_settings="", year=2025):
    root = Path(td)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\n'
        f'base_currency = "CAD"\n{extra_settings}'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "work").mkdir()
    (root / "work" / "margin_base.json").write_text(json.dumps(
        {"transactions": [t.to_dict() for t in BOOK]}))
    return root


def _cli(root, *a):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *a], cwd=REPO_ROOT,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


class TestCarryoverTiming(unittest.TestCase):
    def test_wrapper_uses_project_timing(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "option_grant_timing_since = 2025\n")
            r = _cli(root, "carryover", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            nets = {row["year"]: row["net_gain"]
                    for row in json.loads(r.stdout)["rows"]}
        # Filed 2025 on grant timing: -1,000 + 399 = -601 (the ledger
        # used close timing and said -1,000 before the fix).
        self.assertAlmostEqual(nets[2025], -601.0, places=2)
        self.assertAlmostEqual(nets[2026], -101.0, places=2)

    def test_close_timing_still_available(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, 'option_premium_timing = "close"\n')
            r = _cli(root, "carryover", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            nets = {row["year"]: row["net_gain"]
                    for row in json.loads(r.stdout)["rows"]}
        self.assertAlmostEqual(nets[2025], -1000.0, places=2)
        self.assertAlmostEqual(nets[2026], 298.0, places=2)

    def test_standalone_flags(self):
        from taxjson.bin import taxjson_carryover
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "b.json"
            p.write_text(json.dumps(
                {"transactions": [t.to_dict() for t in BOOK]}))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = taxjson_carryover.main(
                    [str(p), "--country", "canada", "--json",
                     "--option-premium-timing", "grant",
                     "--option-grant-since", "2025"])
            self.assertEqual(rc, 0)
            nets = {row["year"]: row["net_gain"]
                    for row in json.loads(out.getvalue())["rows"]}
        self.assertAlmostEqual(nets[2025], -601.0, places=2)


class TestGrantSince(unittest.TestCase):
    def test_init_writes_since_commented_unless_a_year_sets_it(self):
        """New-user walkthrough (2026-10): a new taxjson.toml leaves the
        key commented with its one-line explanation (the run warns when
        the books hold a written option and it is not set); a year
        folder added beside one that sets it keeps that value."""
        from taxjson.bin.taxjson_run import cmd_init
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            with redirect_stdout(io.StringIO()):
                cmd_init(argparse.Namespace(single=True, path=str(root), dir=".",
                                            force=False, country="canada",
                                            year=2025))
            text = (root / "taxjson.toml").read_text()
            doc = tomllib.loads(text)
            self.assertNotIn("option_grant_timing_since", doc["settings"])
            self.assertIn("# option_grant_timing_since", text)
            top = Path(td) / "taxes"
            with redirect_stdout(io.StringIO()):
                cmd_init(argparse.Namespace(single=False, path=str(top),
                                            dir=".", force=False,
                                            country="canada", year=2025))
            cfg = top / "2025" / "taxjson.toml"
            import re
            cfg.write_text(re.sub(r"(?m)^# option_grant_timing_since\s+= "
                                  r"2025$", "option_grant_timing_since = 2024",
                                  cfg.read_text()))
            with redirect_stdout(io.StringIO()):
                cmd_init(argparse.Namespace(single=False, path=str(top),
                                            dir=".", force=False,
                                            country="canada", year=2026))
            doc = tomllib.loads((top / "2026" / "taxjson.toml").read_text())
            self.assertEqual(doc["settings"]["option_grant_timing_since"],
                             2024)
            root2 = Path(td) / "u"
            with redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()):
                cmd_init(argparse.Namespace(single=True, path=str(root2), dir=".",
                                            force=False, country="usa",
                                            year=2025))
            doc = tomllib.loads((root2 / "taxjson.toml").read_text())
            self.assertNotIn("option_grant_timing_since", doc["settings"])

    def test_warning_only_when_unset_on_canadian_grant_timing(self):
        from taxjson.bin.taxjson_run import _grant_since_warning
        w = _grant_since_warning({"year": 2026, "country": "canada"})
        self.assertIn("option_grant_timing_since is not set", w)
        self.assertIn("MOVES", w)
        self.assertIsNone(_grant_since_warning(
            {"year": 2026, "country": "canada",
             "option_grant_timing_since": 2025}))
        self.assertIsNone(_grant_since_warning(
            {"year": 2026, "country": "canada",
             "option_premium_timing": "close"}))
        self.assertIsNone(_grant_since_warning(
            {"year": 2026, "country": "usa"}))

    def test_option_boundary_warns_when_unset(self):
        with tempfile.TemporaryDirectory() as td:
            r = _cli(_project(td), "option-boundary")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("option_grant_timing_since is not set", r.stderr)


class TestBoundaryAttention(unittest.TestCase):
    BOOK = [T("BUYSELL", "2025-12-15", OPT, -1, 399.0, settle="2025-12-16"),
            T("BUYSELL", "2026-01-10", OPT, 1, -101.0, settle="2026-01-12")]

    def test_locked_year_on_transition_close_timing(self):
        # The r05b scenario: 2025 filed under a default 2025 project
        # (grant, since 2025); the 2026 default project (since 2026) nets
        # the contract at close -> the 399 premium is taxed twice.
        rows = straddling(self.BOOK, 2026, "grant", 2026, {2025})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["attention"])
        self.assertIn("ATTENTION: 2025 is locked", rows[0]["action"])
        self.assertIn("option_grant_timing_since = 2025", rows[0]["action"])
        # The lock records grant timing -> definite wording.
        rows = straddling(self.BOOK, 2026, "grant", 2026, set(), filed_timing={
            2025: {"option_premium_timing": "grant", "option_grant_since": 2025}})
        self.assertTrue(rows[0]["attention"])
        self.assertIn("its lock records grant timing", rows[0]["action"])
        # The lock records close timing -> the transition is right.
        rows = straddling(self.BOOK, 2026, "grant", 2026, set(), filed_timing={
            2025: {"option_premium_timing": "close", "option_grant_since": None}})
        self.assertFalse(rows[0]["attention"])
        self.assertIn("kept on close timing", rows[0]["action"])
        # No lock -> transition wording, no attention (unchanged).
        rows = straddling(self.BOOK, 2026, "grant", 2026, set())
        self.assertFalse(rows[0]["attention"])

    @rule("CA-OPT-04")
    def test_expired_without_expiry_row(self):
        book = [T("BUYSELL", "2025-03-03", "ZZZ250620P00040000.US", -2, 300.0)]
        rows = straddling(book, 2025, "grant", 2025)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close_kind"], "expired?")
        self.assertIn("expired 2025-06-20 but no expiry/assignment row",
                      rows[0]["where"])
        self.assertTrue(rows[0]["attention"])
        # Expiring after the tax year: genuinely open at year end.
        book = [T("BUYSELL", "2025-03-03", OPT, -2, 300.0)]
        self.assertEqual(straddling(book, 2025, "grant", 2025)[0]["close_kind"],
                         "open")

    def test_cli_counts_attention_and_refuses_without_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "", year=2026)
            (root / "work" / "margin_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in self.BOOK]}))
            (root / "filed").mkdir()
            (root / "filed" / "2025.json").write_text("{}")
            r = _cli(root, "option-boundary", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["attention"], 1)
            self.assertFalse(doc["since_explicit"])
            t = _cli(root, "option-boundary")
            self.assertIn("1 item(s) need ATTENTION", t.stdout)
            self.assertNotIn("No amended return is required", t.stdout)
            (root / "work" / "margin_base.json").unlink()
            r = _cli(root, "option-boundary")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("NOT CHECKED", r.stderr)
            self.assertNotIn("Nothing to amend", r.stdout)

    def test_close_year_records_the_timing(self):
        from taxjson.bin import taxjson_filed
        with tempfile.TemporaryDirectory() as td:
            p = taxjson_filed.write_snapshot(
                Path(td), 2025, "canada", "wash-adjusted", {}, force=False,
                option_timing={"option_premium_timing": "grant",
                               "option_grant_since": 2025})
            doc = json.loads(p.read_text())
        self.assertEqual(doc["option_timing"]["option_grant_since"], 2025)


if __name__ == "__main__":
    unittest.main()
