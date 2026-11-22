"""Backlog fix round 7 (wave C) of 2026-10-29, group `forms`: form
export, carryover, the estimate and the per-account reports.

TJ-0993  the standalone form export refuses gains files of more than
         one tax year without --year.
TJ-1002  taxjson-carryover --claimed-year refuses an implausible year
         (2205), as the claimed file does.
TJ-1018  a Canadian partial claim (99.98 of 100.00 of losses in whole
         cents) leaves its 0.02 carried forward.
TJ-1020  the estimate takes a mapped capital-gains dividend and its
         reversal out of the dividends as one net amount.
TJ-1008  the long-option report (reports/leaps.rpt, taxjson-leaps-gains)
         counts an unknown-cost option sale the pipeline moved to
         manual_reporting_required.
TJ-1011  taxjson-sum-gains FILE groups an option on a held class share
         (SYN.B.TO) under that share, as stdin does.
TJ-1013  a per-account position reopened after a sale that settled
         across a split starts on its own buy's date.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*), account names (margin), amounts under
1,000 (or round test figures).

Formerly: test_fix_1029_r7c_forms
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO


def _env():
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _tjs(root, *args):
    """`tjs -C ROOT ARGS` in a child process (stdin closed, unwrapped)."""
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=_env(),
                          stdin=subprocess.DEVNULL, timeout=600)


def _module(mod, *args, cwd=None, stdin=None):
    """A standalone tool (`taxjson-form-export` ...) in a child process."""
    return subprocess.run(python_module_argv(mod) + [str(a) for a in args],
                          capture_output=True, text=True, env=_env(),
                          cwd=cwd, input=stdin,
                          stdin=None if stdin is not None
                          else subprocess.DEVNULL, timeout=600)


def _project(root, country="canada", year=2025, tt=None, settings="",
             accounts=("margin",), ticker_map=""):
    root = Path(root)
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        f'source_currencies = []\n{settings}\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text(ticker_map)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    if tt is not None:
        (root / "inputs" / accounts[0] / "trades.tt").write_text(tt)
    return root


def _run(test, root):
    r = _tjs(root, "run", "--no-input")
    test.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
    return r


class TestStandaloneFormMixedYears(unittest.TestCase):
    """TJ-0993: a 2024 gains file (gain 20) and a 2025 one (gain 30)
    passed together to taxjson-form-export without --year made one
    return labelled 2025, total gain 50, readiness complete.

    Formerly: test_fix_1029_r7c_forms.TestStandaloneFormMixedYears"""

    def test_mixed_years_refused_explicit_year_kept(self):
        for country, cur, sfx, form in (
                ("canada", "CAD", "TO", "schedule3"),
                ("usa", "USD", "US", "8949")):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as d:
                files = []
                for year, proceeds in ((2024, 120), (2025, 130)):
                    p = _project(
                        Path(d) / str(year), country=country, year=year,
                        tt=(f"BUYSELL {year}-01-03 10:00:00 SYN.{sfx} 10 "
                            f"{cur} 10 100 0\n"
                            f"BUYSELL {year}-06-03 10:00:00 SYN.{sfx} -10 "
                            f"{cur} {proceeds / 10} {proceeds} 0\n"),
                        settings=(f"option_grant_timing_since = {year}\n"
                                  if country == "canada" else ""))
                    _run(self, p)
                    files.append(p / "work" / "margin_gains.json")
                args = ("--country", country, "--form", form, "--json")
                r = _module("taxjson.bin.taxjson_form_export", *files,
                            *args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertIn("more than one tax year", r.stderr)
                self.assertIn("2024", r.stderr)
                self.assertIn("--year", r.stderr)
                r = _module("taxjson.bin.taxjson_form_export", *files,
                            *args, "--year", "2025")
                self.assertEqual(r.returncode, 0, r.stderr)
                doc = json.loads(r.stdout)
                self.assertEqual(doc["year"], 2025)
                self.assertEqual(doc["gain_unrounded"], 30.0)
                # One year's file alone: no --year needed.
                r = _module("taxjson.bin.taxjson_form_export", files[1],
                            *args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(json.loads(r.stdout)["year"], 2025)

    def test_unit_source_years_without_recorded_year(self):
        from taxjson.bin.taxjson_form_export import _source_years
        with tempfile.TemporaryDirectory() as d:
            row = {"symbol": "SYN.TO", "qty": 10.0, "gain": 1.0,
                   "proceeds": 11.0, "cost": 10.0, "currency": "CAD"}
            g = Path(d) / "gains.json"
            g.write_text(json.dumps({"transactions": [
                dict(row, date="2024-06-03", date_settle="2024-06-04"),
                dict(row, date="2025-06-03", date_settle="2025-06-04")]}))
            self.assertEqual(sorted(_source_years([g], "date_settle")),
                             [2024, 2025])
            h = Path(d) / "one.json"
            h.write_text(json.dumps({"summary": {"year": "2025"},
                                     "transactions": []}))
            self.assertEqual(_source_years([h], "date"), {2025: [str(h)]})


class TestCarryoverClaimedYearFlag(unittest.TestCase):
    """TJ-1002: a US 2025 loss of 10000; `--claimed-year 2205=0` (a
    typo) was taken at exit 0, the ledger ran to 2205 and its assumed
    yearly deductions used the whole loss: carryforward 0, not 7000.

    Formerly: test_fix_1029_r7c_forms.TestCarryoverClaimedYearFlag"""

    def test_implausible_flag_year_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(
                Path(d) / "p", country="usa",
                tt=("BUYSELL 2025-01-02 10:00:00 SYN.US 100 USD 101 10100 0\n"
                    "BUYSELL 2025-06-02 10:00:00 SYN.US -100 USD 1 100 0\n"))
            _run(self, p)
            base = p / "work" / "margin_base.json"

            def carry(*extra):
                return _module("taxjson.bin.taxjson_carryover", base,
                               "--country", "usa", "--json", *extra)

            ok = carry()
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(json.loads(ok.stdout)["final_carryforward"],
                             7000.0)
            for year in ("2205", "1899", "0"):
                with self.subTest(year=year):
                    bad = carry("--claimed-year", f"{year}=0")
                    self.assertEqual(bad.returncode, 2,
                                     bad.stdout + bad.stderr)
                    self.assertEqual(bad.stdout, "")
                    self.assertIn(f"--claimed-year {int(year)}", bad.stderr)
                    self.assertIn("not a plausible tax year", bad.stderr)
            good = carry("--claimed-year", "2025=0")
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertEqual(
                json.loads(good.stdout)["final_carryforward"], 10000.0)


class TestCarryoverSlackKeepsPartialClaim(unittest.TestCase):
    """TJ-1018: ten 2024 losses of exactly 10.00 (100.00), a 2025 gain
    and [carryover] claimed = {2025 = 99.98}: the ledger applied 99.98
    and showed carryforward 0.00, not 0.02 — its rounding slack (0.005
    a loss row) zeroed the remainder of an exact partial claim.

    Formerly: test_fix_1029_r7c_forms.TestCarryoverSlackKeepsPartialClaim"""

    def test_unit_slack_only_for_subcent_rows(self):
        from taxjson.bin.taxjson_carryover import build_canada_ledger

        def final(claim, subcent):
            nets = {2024: {"net": -100.0, "dispositions": 10,
                           "subcent": subcent},
                    2025: {"net": 200.0, "dispositions": 1, "subcent": 0}}
            return build_canada_ledger(nets, {2025: claim})[
                "final_carryforward"]

        self.assertAlmostEqual(final(99.98, 0), 0.02, places=6)
        self.assertAlmostEqual(final(99.90, 0), 0.10, places=6)
        self.assertAlmostEqual(final(100.0, 0), 0.0, places=6)
        # Sub-cent rows: a claim of the filed (per-row-rounded) loss a
        # few cents from the ledger's still uses it all.
        self.assertAlmostEqual(final(99.98, 10), 0.0, places=6)

    def test_tjs_carryover(self):
        rows = "".join(
            f"BUYSELL 2024-01-02 10:00:00 SYN{i}.TO 1 CAD 20 20 0\n"
            f"BUYSELL 2024-03-01 10:00:00 SYN{i}.TO -1 CAD 10 10 0\n"
            for i in range(10))
        rows += ("BUYSELL 2025-01-02 10:00:00 ZZOTHER.TO 1 CAD 10 10 0\n"
                 "BUYSELL 2025-03-03 10:00:00 ZZOTHER.TO -1 CAD 210 210 0\n")
        for claim, left in (("99.98", 0.02), ("99.90", 0.10),
                            ("100", 0.0)):
            with self.subTest(claim=claim), \
                    tempfile.TemporaryDirectory() as d:
                p = _project(Path(d) / "p", tt=rows,
                             settings=("option_grant_timing_since = 2024\n"
                                       f"\n[carryover]\n"
                                       f"claimed = {{2025 = {claim}}}\n"))
                _run(self, p)
                r = _tjs(p, "carryover", "--json")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                doc = json.loads(r.stdout)
                self.assertAlmostEqual(doc["final_carryforward"], left,
                                       places=2)
                row = [r for r in doc["rows"] if r["year"] == 2025][0]
                self.assertAlmostEqual(row["claimed_applied"],
                                       float(claim), places=2)
                self.assertAlmostEqual(row["carryforward_balance"], left,
                                       places=2)
                self.assertAlmostEqual(row["available_to_apply"], left,
                                       places=2)


if __name__ == "__main__":
    unittest.main()
