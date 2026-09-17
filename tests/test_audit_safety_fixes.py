"""Tests for the 2026-07 safety fixes:

1. to_base.csv data-coverage staleness (rates never refreshed on a stable
   install → silent default-rate conversions).
2. taxjson.toml schema validation (typo'd account type silently dropped the
   account from the run).
3. corp-actions unavailable guard (country=usa + RBC merger rows vanished
   with no owner).
4. elections manifest canonical home in inputs/ (was defaulting into the
   disposable, gitignored work/ cache) + legacy migration.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

import taxjson.bin.taxjson_run as run_mod
from taxjson.bin.taxjson_run import (
    _rates_coverage_stale,
    _resolve_manifest,
    stage_account,
    stage_currency_rates,
    validate_config,
)

TODAY = date(2026, 7, 5)


def rates_line(d, rate="1.3500"):
    return f"{d.isoformat()} 12:00:00 USD CAD {rate}\n"


class TestRatesCoverageStale(unittest.TestCase):
    def _file(self, td, content):
        p = Path(td) / "to_base.csv"
        p.write_text(content)
        return p

    def test_recent_data_is_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._file(td, rates_line(TODAY - timedelta(days=10))
                           + rates_line(TODAY - timedelta(days=1)))
            self.assertFalse(_rates_coverage_stale(p, TODAY))

    def test_weekend_gap_tolerated(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._file(td, rates_line(TODAY - timedelta(days=3)))
            self.assertFalse(_rates_coverage_stale(p, TODAY))

    def test_old_data_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._file(td, rates_line(TODAY - timedelta(days=40)))
            self.assertTrue(_rates_coverage_stale(p, TODAY))

    def test_empty_and_malformed_are_stale(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(_rates_coverage_stale(self._file(td, ""), TODAY))
            self.assertTrue(_rates_coverage_stale(
                self._file(td, "not-a-date USD CAD 1.35\n"), TODAY))

    def test_missing_file_is_stale(self):
        self.assertTrue(_rates_coverage_stale(Path("/nonexistent/x.csv"),
                                              TODAY))


class TestStageCurrencyRates(unittest.TestCase):
    """stage_currency_rates with the subprocess + mtime seams patched out."""

    SETTINGS = {"base_currency": "CAD", "source_currencies": ["USD"]}

    def setUp(self):
        self._needs_rebuild = run_mod.needs_rebuild
        self._run_capture = run_mod.run_capture
        run_mod.needs_rebuild = lambda *a, **k: False   # mtime says fresh

    def tearDown(self):
        run_mod.needs_rebuild = self._needs_rebuild
        run_mod.run_capture = self._run_capture

    def test_stale_coverage_triggers_refetch(self):
        fetched = []
        run_mod.run_capture = lambda cmd: (
            fetched.append(cmd) or rates_line(date.today()).encode())
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "to_base.csv").write_text(
                rates_line(date.today() - timedelta(days=60)))
            with redirect_stdout(io.StringIO()):
                out = stage_currency_rates(self.SETTINGS, cache)
            self.assertEqual(len(fetched), 1)
            self.assertIn(date.today().isoformat(), out.read_text())

    def test_fresh_coverage_not_refetched(self):
        def boom(cmd):
            raise AssertionError("refetched a fresh rates file")
        run_mod.run_capture = boom
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            content = rates_line(date.today())
            (cache / "to_base.csv").write_text(content)
            out = stage_currency_rates(self.SETTINGS, cache)
            self.assertEqual(out.read_text(), content)

    def test_refresh_failure_keeps_previous_file_with_warning(self):
        def offline(cmd):
            raise RuntimeError("no network")
        run_mod.run_capture = offline
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            old = rates_line(date.today() - timedelta(days=60))
            (cache / "to_base.csv").write_text(old)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                out = stage_currency_rates(self.SETTINGS, cache)
            self.assertEqual(out.read_text(), old)     # preserved
            self.assertIn("keeping the existing", err.getvalue())

    def test_refresh_failure_with_no_previous_file_raises(self):
        def offline(cmd):
            raise RuntimeError("no network")
        run_mod.run_capture = offline
        with tempfile.TemporaryDirectory() as td:
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    stage_currency_rates(self.SETTINGS, Path(td))


class TestValidateConfig(unittest.TestCase):
    def _cfg(self, **overrides):
        cfg = {"settings": {"year": 2025, "country": "canada",
                            "base_currency": "CAD"},
               "accounts": {"margin": {"type": "taxable"}}}
        cfg.update(overrides)
        return cfg

    def test_clean_config_no_warnings(self):
        self.assertEqual(validate_config(self._cfg()), [])

    def test_typo_type_is_fatal_with_suggestion(self):
        cfg = self._cfg(accounts={"margin": {"type": "Taxable"}})
        with self.assertRaises(SystemExit) as cm:
            validate_config(cfg)
        self.assertIn("silently dropped", str(cm.exception))
        self.assertIn("taxable", str(cm.exception))

    def test_bad_country_and_tax_date_fatal(self):
        with self.assertRaises(SystemExit):
            validate_config(self._cfg(settings={"year": 2025,
                                                "country": "germany",
                                                "base_currency": "EUR"}))
        with self.assertRaises(SystemExit):
            validate_config(self._cfg(settings={"year": 2025,
                                                "country": "canada",
                                                "base_currency": "CAD",
                                                "tax_date": "settled"}))

    def test_non_integer_year_fatal(self):
        with self.assertRaises(SystemExit):
            validate_config(self._cfg(settings={"year": "2025",
                                                "country": "canada",
                                                "base_currency": "CAD"}))

    def test_unknown_keys_warn_with_did_you_mean(self):
        cfg = self._cfg(settings={"year": 2025, "country": "canada",
                                  "base_currency": "CAD",
                                  "taxdate": "settle"})
        warnings = validate_config(cfg)
        self.assertEqual(len(warnings), 1)
        self.assertIn("taxdate", warnings[0])
        self.assertIn("tax_date", warnings[0])         # did-you-mean

    def test_missing_type_warns(self):
        cfg = self._cfg(accounts={"margin": {}})
        warnings = validate_config(cfg)
        self.assertTrue(any("no `type`" in w for w in warnings))

    def test_populated_unconfigured_inputs_dir_warns(self):
        with tempfile.TemporaryDirectory() as td:
            inputs = Path(td) / "inputs"
            (inputs / "cash").mkdir(parents=True)
            (inputs / "cash" / "jan.csv").write_text("a,b\n")
            (inputs / "margin").mkdir()
            warnings = validate_config(self._cfg(), inputs)
        self.assertTrue(any("inputs/cash/" in w for w in warnings))
        self.assertFalse(any("inputs/margin" in w for w in warnings))


class TestResolveManifest(unittest.TestCase):
    def test_creates_in_inputs_not_work(self):
        with tempfile.TemporaryDirectory() as td:
            acct = Path(td) / "inputs" / "margin"
            cache = Path(td) / "work"
            acct.mkdir(parents=True); cache.mkdir()
            with redirect_stdout(io.StringIO()):
                p = _resolve_manifest(acct, cache, "margin", create=True)
            self.assertEqual(p, acct / "manifest.json")
            self.assertTrue(p.exists())
            self.assertFalse((cache / "margin_manifest.json").exists())
            self.assertEqual(json.loads(p.read_text()), {"elections": {}})

    def test_migrates_legacy_work_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            acct = Path(td) / "inputs" / "margin"
            cache = Path(td) / "work"
            acct.mkdir(parents=True); cache.mkdir()
            legacy = {"elections": {"abc123": {"election": "rollover"}}}
            (cache / "margin_manifest.json").write_text(json.dumps(legacy))
            out = io.StringIO()
            with redirect_stdout(out):
                p = _resolve_manifest(acct, cache, "margin", create=False)
            self.assertEqual(p, acct / "manifest.json")
            self.assertEqual(json.loads(p.read_text()), legacy)  # content kept
            self.assertIn("migrated", out.getvalue())

    def test_user_manifest_wins_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            acct = Path(td) / "inputs" / "margin"
            cache = Path(td) / "work"
            acct.mkdir(parents=True); cache.mkdir()
            user = {"elections": {"user": {"election": "taxable"}}}
            (acct / "manifest.json").write_text(json.dumps(user))
            (cache / "margin_manifest.json").write_text('{"elections": {}}')
            p = _resolve_manifest(acct, cache, "margin", create=True)
            self.assertEqual(json.loads(p.read_text()), user)

    def test_read_only_mode_creates_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            acct = Path(td) / "inputs" / "margin"
            cache = Path(td) / "work"
            acct.mkdir(parents=True); cache.mkdir()
            p = _resolve_manifest(acct, cache, "margin", create=False)
            self.assertEqual(p, acct / "manifest.json")
            self.assertFalse(p.exists())


class TestUndrainedAssignPremiumWarning(unittest.TestCase):
    """An ASSIGN option leg stages its premium for the stock leg; when the
    stock leg never arrives the premium used to vanish silently. Both
    engines now warn at end of run."""

    OPTION_ONLY = """
    BUYSELL 2025-01-01 10:00:00 AAPL250117C00160000.US -1.0 USD 5.00 500.00 0.00
    ASSIGN 2025-01-17 16:00:00 AAPL250117C00160000.US 1.0 USD 0.00 0.00 0.00
    """
    WITH_STOCK_LEG = OPTION_ONLY.rstrip() + """
    BUYSELL 2024-12-01 10:00:00 AAPL.US 100.0 USD 150.00 15000.00 0.00
    ASSIGN 2025-01-17 16:00:01 AAPL.US -100.0 USD 160.00 16000.00 0.00
    """

    def _gains(self, rules_cls, content):
        from test_ported_tt_helper import parse_tt_lines
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            rules_cls().compute_gains(parse_tt_lines(content))
        return err.getvalue()

    def test_canada_warns_on_missing_stock_leg(self):
        # 2026-08 deep-audit: an ASSIGN whose underlying never trades
        # as stock is now REALIZED directly (cash-settled semantics —
        # the old path dropped the entire P&L with only the
        # "unconsumed premium" warning). The missing-data case stays
        # diagnosable via the cash-settled note.
        from taxjson.lib.core import CanadaTaxRules
        err = self._gains(CanadaTaxRules, self.OPTION_ONLY)
        self.assertIn("cash-settled", err)
        self.assertIn("AAPL.US", err)

    def test_canada_quiet_when_stock_leg_consumes(self):
        from taxjson.lib.core import CanadaTaxRules
        err = self._gains(CanadaTaxRules, self.WITH_STOCK_LEG)
        self.assertNotIn("unconsumed option-assignment", err)

    def test_usa_warns_on_missing_stock_leg(self):
        # See the Canada twin: realized as cash-settled, note emitted.
        from taxjson.lib.core import USATaxRules
        err = self._gains(USATaxRules, self.OPTION_ONLY)
        self.assertIn("cash-settled", err)

    def test_usa_quiet_when_stock_leg_consumes(self):
        from taxjson.lib.core import USATaxRules
        err = self._gains(USATaxRules, self.WITH_STOCK_LEG)
        self.assertNotIn("unconsumed option-assignment", err)


class _StopStage(Exception):
    """Raised by the patched run_to_file at the merge stage so stage_account
    can be driven exactly far enough to cross the corp-actions block."""


class TestCorpActionsUnavailableGuard(unittest.TestCase):
    RBC_CSV = ("RBC Direct Investing\n"
               "Activity Export\n"
               "Account:,123456789\n"
               "Date,Activity,Symbol,Description,Quantity,Price,Amount,"
               "Currency\n")

    def _run_stage(self, country):
        settings = {"base_currency": "CAD", "country": country,
                    "year": 2025, "tax_date": "settle"}

        def fake_run_to_file(cmd, out, **kwargs):
            if any("merge" in str(c) for c in cmd):
                raise _StopStage()
            Path(out).write_text('{"transactions": []}\n')

        orig = run_mod.run_to_file
        run_mod.run_to_file = fake_run_to_file
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                acct = root / "inputs" / "margin"
                acct.mkdir(parents=True)
                (acct / "rbc.csv").write_text(self.RBC_CSV)
                (root / "work").mkdir()
                rates = root / "work" / "to_base.csv"
                rates.write_text("")
                err, out_ = io.StringIO(), io.StringIO()
                try:
                    with redirect_stdout(out_), redirect_stderr(err):
                        stage_account("margin", {"type": "taxable"},
                                      settings, root / "inputs",
                                      root / "work", root / "reports",
                                      rates, None, None, False)
                except _StopStage:
                    pass
                manifest = acct / "manifest.json"
                return err.getvalue(), out_.getvalue(), manifest.exists()
        finally:
            run_mod.run_to_file = orig

    def test_ruleless_country_with_rbc_warns_loudly(self):
        # Canada AND the US both have election rules now, so the guard
        # only fires for a hypothetical rule-less jurisdiction — keep the
        # path covered, it protects the next country addition.
        err, _out, _ = self._run_stage("germany")
        self.assertIn("no corp-action election rules", err)
        self.assertIn("rbc_direct", err)
        self.assertIn("MISSING", err)

    def test_usa_now_has_rules_no_warning_and_manifest_created(self):
        # The audit's shares-vanish scenario: country=usa + RBC merger
        # rows. With RULES_BY_COUNTRY['usa'] present the corp-actions
        # stage runs (patched here) instead of being skipped.
        err, _out, manifest_in_inputs = self._run_stage("usa")
        self.assertNotIn("no corp-action election rules", err)
        self.assertTrue(manifest_in_inputs)

    def test_canada_does_not_warn_and_creates_manifest_in_inputs(self):
        err, _out, manifest_in_inputs = self._run_stage("canada")
        self.assertNotIn("no corp-action election rules", err)
        # The corp stage ran (patched), and the manifest's canonical home
        # is inputs/<account>/manifest.json — not the work/ cache.
        self.assertTrue(manifest_in_inputs)


if __name__ == "__main__":
    unittest.main()
