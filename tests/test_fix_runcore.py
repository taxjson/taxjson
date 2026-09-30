"""Regression pins for the run-core medium findings (2026-09 audit).

Each test drives the real CLI (or the unit that owns the bug) over a
synthetic project: fake account numbers, all-CAD data, no FX fetch.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"
"""

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

# Buy 100 @ 10.00 (comm 9.95) -> ACB 1009.95; sell 100 @ 15.00 (comm
# 9.95) -> proceeds 1490.05; gain 480.10.
_MARGIN_CSV = _QT_HEADER + (
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,ISHARES COMP,"
    "100,10.00,1000.00,9.95,-1009.95,CAD,55500001,Trades,Individual\n"
    "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XEI.TO,ISHARES COMP,"
    "-100,15.00,1500.00,9.95,1490.05,CAD,55500001,Trades,Individual\n")


def _run_cli(root, *args, env=None):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(tmp, config=_CONFIG, csv=_MARGIN_CSV):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(csv)
    return root


def _set_config(root, text):
    (root / "taxjson.toml").write_text(text)


class TestSettingsValidatedEverywhere(unittest.TestCase):
    """R1-153 (base_currency) and R1-256 (year): checked by every config
    reader, not left to fail later as a misleading stage error."""

    def test_base_currency_is_trimmed_and_upper_cased(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG.replace('"CAD"', '" cad "'))
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertNotIn("non-CAD", r.stderr)
            doc = json.loads(_run_cli(root, "sum", "--json").stdout)
            self.assertEqual(doc.get("currency", "CAD"), "CAD")

    def test_base_currency_not_a_code_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            _set_config(root, _CONFIG.replace('"CAD"', '"C A D"'))
            r = _run_cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("base_currency", r.stderr)
            r = _run_cli(root, "sum")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("base_currency", r.stderr)

    def test_implausible_year_is_refused_by_run_and_sum(self):
        for bad in ("2204", "1850", "0", "true"):
            with self.subTest(year=bad), \
                    tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp, _CONFIG.replace("year = 2025",
                                                     f"year = {bad}"))
                r = _run_cli(root, "run", "--no-input")
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("year", r.stderr)
                r = _run_cli(root, "sum")
                self.assertNotEqual(r.returncode, 0)

    def test_year_with_no_activity_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG.replace("year = 2025",
                                                 "year = 2015"))
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no transaction", r.stderr)
            self.assertIn("2015", r.stderr)


class TestConfigTablesChecked(unittest.TestCase):
    """R1-216 / R1-257 / S038-13: a misspelled top-level table or key
    in [estimate] / [instalments] is reported by the commands that read
    them, not only (or never) by `run`."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = _project(self._td.name)
        r = _run_cli(self.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr

    def tearDown(self):
        self._td.cleanup()

    def test_misspelled_estimate_table_warns_in_estimate_and_run(self):
        _set_config(self.root, _CONFIG + "\n[estimates]\nother_income = "
                    "200000\n")
        r = _run_cli(self.root, "estimate", "--province", "ON", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[estimates]", r.stderr)
        self.assertIn("did you mean", r.stderr)
        r = _run_cli(self.root, "run", "--no-input")
        self.assertIn("[estimates]", r.stderr)

    def test_misspelled_key_warns_in_estimate(self):
        _set_config(self.root, _CONFIG + "\n[estimate]\nother_incme = "
                    "120000\n")
        r = _run_cli(self.root, "estimate", "--province", "ON")
        self.assertIn("other_incme", r.stderr)
        self.assertIn("other_income", r.stderr)

    def test_misspelled_instalments_key_warns_in_instalments(self):
        _set_config(self.root, _CONFIG + (
            "\n[instalments]\nbasis = \"prior_year\"\n"
            "prior_year_net_tax = 8000\nsecond_prior_net_tax = 8000\n"
            "witheld = 2000\n"))
        r = _run_cli(self.root, "instalments")
        self.assertIn("witheld", r.stderr)
        self.assertIn("withheld", r.stderr)


class TestInstalmentMoneyInputs(unittest.TestCase):
    """R1-217: [instalments] money inputs are sign-, finiteness- and
    type-checked like paid[].amount already was; [estimate] rejects a
    TOML boolean."""

    def _instalments(self, extra):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + "\n[instalments]\nbasis = "
                            "\"prior_year\"\nprior_year_net_tax = 8000\n"
                            "second_prior_net_tax = 8000\n" + extra)
            return _run_cli(root, "instalments")

    def test_negative_withheld_is_refused(self):
        r = self._instalments("withheld = -5000\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("withheld", r.stderr)

    def test_nan_and_bool_withheld_are_refused(self):
        for v in ("nan", "true", "inf"):
            with self.subTest(v=v):
                r = self._instalments(f"withheld = {v}\n")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("withheld", r.stderr)

    def test_negative_prior_year_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + "\n[instalments]\nbasis = "
                            "\"prior_year\"\nprior_year_net_tax = -60000\n")
            r = _run_cli(root, "instalments")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("prior_year_net_tax", r.stderr)

    def test_rate_must_be_a_fraction_below_one(self):
        for v in ("-0.08", "1.0"):
            with self.subTest(rate=v):
                r = self._instalments(f"prescribed_rate = {v}\n")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("prescribed_rate", r.stderr)
        r = self._instalments(
            'prescribed_rates = [{ from = "2025-01-01", rate = -0.07 }]\n')
        self.assertNotEqual(r.returncode, 0)

    def test_estimate_boolean_other_income_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + "\n[estimate]\nother_income = "
                            "true\n")
            _run_cli(root, "run", "--no-input")
            r = _run_cli(root, "estimate", "--province", "ON")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("other_income", r.stderr)


class TestNetTaxOwingGuidance(unittest.TestCase):
    """R1-215: the guidance for the prior years' net tax owing named
    line 48500 minus withholding — 48500 already subtracts withholding
    AND the instalments paid, so following it reads 'no instalments
    required' for a typical instalment payer."""

    def test_no_guidance_names_line_48500(self):
        from taxjson.bin import taxjson_run as R
        from taxjson.bin import taxjson_instalments as I
        texts = [R._TEMPLATE_INSTALMENTS,
                 (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
                 Path(I.__file__).read_text(encoding="utf-8")]
        for t in texts:
            self.assertNotIn("48500 minus", t)
        self.assertIn("43700", R._TEMPLATE_INSTALMENTS)


class TestInitForceKeepsEveryBackup(unittest.TestCase):
    """R1-255: a second `init --force` overwrote taxjson.toml.bak with
    the first template, losing the user's config."""

    def test_second_force_keeps_the_first_backup(self):
        from taxjson.bin.taxjson_run import cmd_init

        def _init(path, country):
            with redirect_stdout(io.StringIO()) as out:
                cmd_init(argparse.Namespace(path=str(path), dir=".",
                                            force=True, country=country,
                                            year=None))
            return out.getvalue()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            _init(root, "ca")
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text() + "\n# MARKER-55500001\n")
            _init(root, "us")
            out = _init(root, "ca")
            hits = [p.name for p in root.iterdir()
                    if p.name.startswith("taxjson.toml.bak")
                    and "MARKER-55500001" in p.read_text()]
            self.assertEqual(len(hits), 1, sorted(p.name
                                                  for p in root.iterdir()))
            self.assertIn("taxjson.toml.bak", out)


class TestBrokerDetectionPrecedence(unittest.TestCase):
    """R1-127, S044-01, S044-02: a word in the file name ('coinbase',
    'kraken') beat both the documented prefixes and a positive content
    match, so a generic_/kr_ file went to the wrong crypto parser (0
    rows, rc 0) and an IB/Questrade export named after a holding was
    refused as crypto."""

    def _detect(self, src, name):
        from taxjson.bin.taxjson_run import detect_broker
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / name
            p.write_bytes((REPO_ROOT / "examples" / src).read_bytes())
            return detect_broker(p)

    def test_generic_prefix_beats_a_venue_word(self):
        self.assertEqual(self._detect("questrade_demo.csv",
                                      "generic_kraken_export.csv"),
                         "generic")
        self.assertEqual(self._detect("questrade_demo.csv",
                                      "generic_coinbase_pro.csv"),
                         "generic")

    def test_kr_prefix_beats_the_coinbase_word(self):
        self.assertEqual(self._detect("kraken_demo.csv",
                                      "kr_trades_moved_from_coinbase.csv"),
                         "kraken")

    def test_content_match_beats_a_venue_word(self):
        self.assertEqual(self._detect("questrade_demo.csv",
                                      "questrade_COIN_coinbase_stock.csv"),
                         "questrade")
        self.assertEqual(self._detect("ib_demo.csv",
                                      "ib_kraken_robotics_KRKNF.csv"),
                         "ib")

    def test_word_hint_still_routes_real_exchange_exports(self):
        self.assertEqual(self._detect("kraken_demo.csv",
                                      "my_kraken_ledger.csv"), "kraken")
        self.assertEqual(self._detect("coinbase_demo.csv",
                                      "coinbase_2025.csv"), "coinbase")


class TestRatesRebuiltByDefaultRun(unittest.TestCase):
    """S046-06: the default (force) run reused a to_base.csv built during
    a partly failed refresh; freshness looked only at the file's LAST
    line, so a USD block cut short hid behind a fresh AUD block."""

    @staticmethod
    def _line(d, cur):
        return f"{d.isoformat()} 12:00:00 {cur} CAD 1.3500 boc\n"

    def test_coverage_is_judged_per_currency(self):
        from datetime import date, timedelta
        from taxjson.bin.taxjson_run import _rates_coverage_stale
        today = date(2026, 9, 29)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "to_base.csv"
            p.write_text(self._line(today - timedelta(days=9), "USD")
                         + self._line(today, "AUD"))
            self.assertTrue(_rates_coverage_stale(
                p, today, currencies=["USD", "AUD"]))
            self.assertFalse(_rates_coverage_stale(
                p, today, currencies=["AUD"]))

    def test_a_short_currency_block_is_refetched(self):
        from datetime import date, timedelta
        from taxjson.bin import taxjson_run as R
        calls = []
        saved = (R.run_capture, R.needs_rebuild)
        R.run_capture = lambda cmd: (calls.append(cmd)
                                     or self._line(date.today(),
                                                   cmd[-2]).encode())
        R.needs_rebuild = lambda *a, **k: False      # mtime says fresh
        try:
            with tempfile.TemporaryDirectory() as td:
                cache = Path(td)
                (cache / "to_base.csv").write_text(
                    self._line(date.today() - timedelta(days=9), "USD")
                    + self._line(date.today(), "AUD"))
                with redirect_stdout(io.StringIO()):
                    R.stage_currency_rates(
                        {"base_currency": "CAD",
                         "source_currencies": ["USD", "AUD"]}, cache)
        finally:
            R.run_capture, R.needs_rebuild = saved
        self.assertEqual(len(calls), 2)


class TestFastCacheSeesContent(unittest.TestCase):
    """R1-253 / R1-294: `run --fast` kept the parse of a CSV replaced by
    one with an OLDER mtime (cp -p, rsync -a, unzip), and ignored a
    ticker.map rule restored with an old mtime."""

    def _gain(self, root):
        doc = json.loads(_run_cli(root, "sum", "margin", "--json").stdout)
        return json.dumps(doc, sort_keys=True)

    def test_csv_with_an_old_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, csv=_QT_HEADER + _MARGIN_CSV.splitlines(
                True)[1])
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            csv = root / "inputs" / "margin" / "questrade_2025.csv"
            old = csv.stat().st_mtime - 3600
            csv.write_text(_MARGIN_CSV)
            os.utime(csv, (old, old))
            r = _run_cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            fast = self._gain(root)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            self.assertEqual(fast, self._gain(root))
            self.assertIn("480.1", fast)

    def test_ticker_map_with_an_old_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            tm = root / "ticker.map"
            tm.write_text("GLOBAL XEI.TO XEIX.TO\n")
            os.utime(tm, (1577836800, 1577836800))
            r = _run_cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            base = (root / "work" / "margin_base.json").read_text()
            self.assertIn("XEIX.TO", base)


_TWO_ACCOUNTS = _CONFIG + """
[accounts.tfsa]
type = "sheltered"
"""

_TFSA_CSV = _QT_HEADER + (
    "2025-06-25 09:30:00 AM,2025-06-26 12:00:00 AM,Buy,XEI.TO,ISHARES COMP,"
    "10,14.00,140.00,0.00,-140.00,CAD,55500002,Trades,TFSA\n")


class TestStaleSidecarsRemovedByFullRun(unittest.TestCase):
    """S004-05: work/sheltered_base.json outlived the last sheltered
    account and kept feeding the filed-year lock and the radar.
    S038-19: an account re-typed taxable -> sheltered kept its old
    _gains_wash.json / _wash.sum, which every query preferred."""

    def test_sheltered_base_goes_with_the_last_sheltered_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _TWO_ACCOUNTS)
            (root / "inputs" / "tfsa").mkdir()
            (root / "inputs" / "tfsa" / "q.csv").write_text(_TFSA_CSV)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            sb = root / "work" / "sheltered_base.json"
            self.assertTrue(sb.exists())
            _set_config(root, _CONFIG)
            import shutil
            shutil.rmtree(root / "inputs" / "tfsa")
            for p in (root / "work").glob("tfsa_*"):
                p.unlink()
            for p in (root / "reports").glob("tfsa*"):
                p.unlink()
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(sb.exists())

    def test_retyped_account_drops_its_wash_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            wash = root / "work" / "margin_gains_wash.json"
            self.assertTrue(wash.exists())
            _set_config(root, _CONFIG.replace('"taxable"', '"sheltered"'))
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(wash.exists())
            self.assertFalse((root / "reports" / "margin_wash.sum")
                             .exists())


class TestMergeRefusesUnreadableInput(unittest.TestCase):
    """S038-18: taxjson-merge logged an unreadable book and exited 0, so
    `run --account <sheltered>` rebuilt sheltered_base.json without a
    sibling account's rows."""

    def test_unreadable_book_fails_the_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "a.json"
            good.write_text(json.dumps({"transactions": []}))
            bad = Path(tmp) / "b.json"
            bad.write_text('{"transactions": [')
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_merge",
                 str(good), str(bad)], capture_output=True, text=True,
                cwd=REPO_ROOT)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("b.json", r.stderr)


class TestFilingReminderSurvivesABadManifest(unittest.TestCase):
    """S038-23: one unreadable manifest.json silently dropped the end-
    of-run FILING REQUIRED reminder for every account."""

    def test_other_accounts_reminder_still_prints(self):
        cfg = _CONFIG + "\n[accounts.zmargin]\ntype = \"taxable\"\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg)
            (root / "inputs" / "zmargin").mkdir()
            (root / "inputs" / "zmargin" / "q.csv").write_text(
                _MARGIN_CSV.replace("55500001", "55500003"))
            (root / "inputs" / "margin" / "manifest.json").write_text(
                json.dumps({"elections": {"ev1": {
                    "summary": "2025-03-01 spin-off XEI.TO",
                    "election": "rollover_s_86_1",
                    "hints": {"allocated_acb_cad": 10.0}}}}))
            (root / "inputs" / "zmargin" / "manifest.json").write_text(
                "<<<<<<< HEAD\n{}\n")
            r = _run_cli(root, "run", "--no-input")
        self.assertIn("FILING REQUIRED", r.stderr)
        self.assertIn("zmargin", r.stderr)
        self.assertIn("manifest.json", r.stderr)


class TestSpreadsheetInSubfolderWarns(unittest.TestCase):
    """S043-13: the not-read-subfolder warning counted only .csv/.tt,
    so an .xlsx in inputs/<account>/2025/ got no word."""

    def test_xlsx_in_a_subfolder(self):
        from taxjson.bin.taxjson_run import validate_config
        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "inputs"
            (inputs / "margin" / "2025").mkdir(parents=True)
            (inputs / "margin" / "2025" / "Activity.xlsx").write_bytes(b"x")
            warnings = validate_config(
                {"settings": {"year": 2025},
                 "accounts": {"margin": {"type": "taxable"}}}, inputs)
        self.assertTrue(any("inputs/margin/2025/" in w for w in warnings),
                        warnings)


if __name__ == "__main__":
    unittest.main()
