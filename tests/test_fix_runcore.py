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

    def test_implausible_year_is_refused_by_sum_on_built_books(self):
        # Re-audit A2-1481: with no built books `sum` exits 1 ('no gains
        # files') for any year, so the check above alone proved nothing
        # for `sum`. Build valid books, then type the year wrong.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_run_cli(root, "sum").returncode, 0)
            _set_config(root, _CONFIG.replace("year = 2025", "year = 2204"))
            r = _run_cli(root, "sum")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("year = 2204 is not a plausible tax year",
                          r.stderr)

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
        # The refusal itself, not the helper project's 'no gains files'
        # (re-audit A2-0811: any config exits 1 there).
        self.assertIn("prescribed_rates[1].rate", r.stderr)

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
        from taxjson.lib.config_template import render_init
        template = render_init("canada", 2025)[0]
        texts = [template,
                 (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
                 Path(I.__file__).read_text(encoding="utf-8")]
        for t in texts:
            self.assertNotIn("48500 minus", t)
        self.assertIn("43700", template)
        del R


class TestInitForceKeepsEveryBackup(unittest.TestCase):
    """R1-255: a second `init --force` overwrote taxjson.toml.bak with
    the first template, losing the user's config."""

    def test_second_force_keeps_the_first_backup(self):
        from taxjson.bin.taxjson_run import cmd_init

        def _init(path, country):
            with redirect_stdout(io.StringIO()) as out:
                cmd_init(argparse.Namespace(single=True, path=str(path), dir=".",
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

    def _detect(self, src, name, mapping=False):
        from taxjson.bin.taxjson_run import detect_broker
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / name
            p.write_bytes((REPO_ROOT / "examples" / src).read_bytes())
            if mapping:
                p.with_name(p.name + ".toml").write_text("[columns]\n")
            return detect_broker(p)

    def test_generic_mapping_beats_a_venue_word(self):
        # The generic mapping (a sidecar) is configuration and wins; a
        # generic_ NAME without a mapping is only a name, and the
        # content (a Questrade export) wins over it (owner request
        # 2026-10-04: detection never depends on the file name first).
        for name in ("generic_kraken_export.csv", "generic_coinbase_pro.csv"):
            self.assertEqual(self._detect("questrade_demo.csv", name,
                                          mapping=True), "generic", name)
            self.assertEqual(self._detect("questrade_demo.csv", name),
                             "questrade", name)

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
                {"settings": {"year": 2025, "country": "canada"},
                 "accounts": {"margin": {"type": "taxable"}}}, inputs)
        self.assertTrue(any("inputs/margin/2025/" in w for w in warnings),
                        warnings)


def _tt_project(tmp, accounts, year=None):
    """accounts: {name: (type, [.tt lines])} — a current-year project."""
    from datetime import date
    year = year or date.today().year
    root = Path(tmp)
    cfg = (f"[settings]\nyear = {year}\ncountry = \"canada\"\n"
           f"base_currency = \"CAD\"\nsource_currencies = []\n"
           f"option_grant_timing_since = {year}\n")
    for name, (atype, lines) in accounts.items():
        cfg += f"\n[accounts.{name}]\ntype = \"{atype}\"\n"
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "books.tt").write_text(
            "\n".join(lines) + "\n")
    (root / "taxjson.toml").write_text(cfg)
    return root


def _ago(days):
    from datetime import date, timedelta
    return (date.today() - timedelta(days=days)).isoformat()


class TestBuyCheckSymbolSpellings(unittest.TestCase):
    """S007-02: RCI-B / 'RCI B' / RCI/B / RCI-B.TO answered SAFE while a
    loss on RCI.B.TO sat inside the window. S047-01: an RCI-rooted
    (Montreal) call is a right to acquire RCI.B.TO shares."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _tt_project(cls._td.name, {"margin": ("taxable", [
            f"BUYSELL {_ago(120)} 10:00:00 RCI.B.TO 100 CAD 50.00 "
            f"5000.00 0.00",
            f"BUYSELL {_ago(8)} 10:00:00 RCI.B.TO -100 CAD 40.00 "
            f"4000.00 0.00"])})
        r = _run_cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_control_dotted_spelling_is_unsafe(self):
        r = _run_cli(self.root, "buy-check", "RCI.B.TO")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_broker_and_yahoo_spellings_are_unsafe(self):
        for sym in ("RCI-B", "RCI B", "RCI/B", "RCI-B.TO", "rci-b"):
            with self.subTest(sym=sym):
                r = _run_cli(self.root, "buy-check", sym)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_montreal_root_call_is_in_the_class_share_class(self):
        from datetime import date
        exp = f"{(date.today().year + 1) % 100:02d}0115"
        r = _run_cli(self.root, "buy-check", f"RCI{exp}C00046000.TO")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


class TestRadarNamesAccountsWithoutBooks(unittest.TestCase):
    """S046-11: a taxable account whose base book is missing vanished
    from sell-check / wash-radar with no word, turning a live wash
    exposure into SAFE."""

    def test_missing_sibling_book_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _tt_project(tmp, {
                "margin": ("taxable", [
                    f"BUYSELL {_ago(200)} 10:00:00 XYZ.TO 100 CAD 20.00 "
                    f"2000.00 0.00"]),
                "margin2": ("taxable", [
                    f"BUYSELL {_ago(5)} 10:00:00 XYZ.TO 100 CAD 10.00 "
                    f"1000.00 0.00"])})
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            (root / "work" / "margin2_base.json").unlink()
            for cmd in (("sell-check", "XYZ.TO"), ("wash-radar",)):
                with self.subTest(cmd=cmd[0]):
                    r = _run_cli(root, *cmd)
                    self.assertIn("margin2", r.stderr)


class TestRadarSidecarNames(unittest.TestCase):
    """S038-10: the radar sidecar name stripped EVERY '_base' from the
    file stem, and an account named COMBINED collided with the cross-
    account radar."""

    def test_stem_keeps_inner_base(self):
        from taxjson.bin import taxjson_run as R
        outs = []
        saved = R.run_to_file
        R.run_to_file = lambda cmd, out, **k: outs.append(Path(out).name)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp)
                with redirect_stdout(io.StringIO()):
                    R.stage_cross_reports(
                        [d / "x_gains.json"],
                        [d / "a_base_x_base.json", d / "a_x_base.json"],
                        None, d, country="canada")
        finally:
            R.run_to_file = saved
        self.assertIn("wash_radar_a_base_x.rpt", outs)
        self.assertIn("wash_radar_a_x.rpt", outs)

    def test_account_named_combined_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG + "\n[accounts.combined]\n"
                            "type = \"taxable\"\n")
            r = _run_cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("COMBINED", r.stderr)


class TestListAsOf(unittest.TestCase):
    """R1-4: `list --date` cut by TRADE date in a settle-basis project.
    R1-187: it ignored missing_history.json (phantom-backed positions showed as
    large shorts). R1-282: the headers mislabelled both views."""

    _CSV = _QT_HEADER + (
        "2025-03-03 09:30:00 AM,2025-03-04 12:00:00 AM,Buy,SELLX.TO,S,"
        "50,10.00,500.00,0.00,-500.00,CAD,55500001,Trades,Individual\n"
        "2025-03-03 09:30:00 AM,2025-03-04 12:00:00 AM,Buy,BUYX.TO,B,"
        "10,10.00,100.00,0.00,-100.00,CAD,55500001,Trades,Individual\n"
        "2025-12-31 09:30:00 AM,2026-01-02 12:00:00 AM,Sell,SELLX.TO,S,"
        "-50,12.00,600.00,0.00,600.00,CAD,55500001,Trades,Individual\n"
        "2025-12-31 09:30:00 AM,2026-01-02 12:00:00 AM,Buy,BUYX.TO,B,"
        "30,10.00,300.00,0.00,-300.00,CAD,55500001,Trades,Individual\n")

    def _rows(self, root, *args):
        r = _run_cli(root, "list", *args, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        return doc, {x["symbol"]: x["qty"] for x in doc["rows"]}

    def test_settle_basis_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, csv=self._CSV)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            doc, rows = self._rows(root, "--date", "2025-12-31")
            self.assertEqual(rows, {"SELLX.TO": 50.0, "BUYX.TO": 10.0})
            self.assertIn("settlement", doc["basis"])
            self.assertNotIn("pre-ticker.map", doc["basis"])
            # A trade-basis project keeps the trade-date cutoff.
            _set_config(root, _CONFIG.replace(
                'source_currencies = []',
                'source_currencies = []\ntax_date = "trade"'))
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            doc, rows = self._rows(root, "--date", "2025-12-31")
            self.assertEqual(rows, {"BUYX.TO": 40.0})
            self.assertIn("trade", doc["basis"])

    def test_phantoms_are_applied(self):
        csv = _QT_HEADER + (
            "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,ZZZ.TO,Z,"
            "-100,15.00,1500.00,0.00,1500.00,CAD,55500001,Trades,"
            "Individual\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, csv=csv)
            (root / "missing_history.json").write_text(
                '[{"symbol": "ZZZ.TO", "account": "margin"}]')
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            _doc, rows = self._rows(root, "--date", "2025-12-31")
            self.assertNotIn("ZZZ.TO", rows)

    def test_plain_list_is_labelled_by_the_data_horizon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, csv=self._CSV)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            r = _run_cli(root, "list")
            self.assertNotIn("as of tax year", r.stdout)
            # A settle-basis book's horizon is its last SETTLEMENT date:
            # the Dec 31 trades settle Jan 2 (re-audit A2-0698).
            self.assertIn("(2026-01-02)", r.stdout)
            r = _run_cli(root, "list", "--date", "2025-06-30")
            self.assertNotIn("as of tax year", r.stdout)
            self.assertIn("as of 2025-06-30", r.stdout)


def _otx(date, symbol, qty, net, currency="CAD"):
    return {"action": "BUYSELL", "date": date, "date_settle": date,
            "time": "09:30:00", "symbol": symbol, "quantity": qty,
            "net_amount": net, "currency": currency}


def _gain(symbol, date, gain, direction="LONG", account="margin", **kw):
    return dict({"symbol": symbol, "date": date, "date_settle": date,
                 "qty": -1, "proceeds": 0.0, "cost": 0.0, "gain": gain,
                 "raw_gain": gain, "disallowed_amount": 0.0,
                 "days_held": 30, "direction": direction,
                 "account": account}, **kw)


class TestLeapsViews(unittest.TestCase):
    """R1-172 / R1-237: leaps / leaps-sum counted the SHORT write and
    buy-back of a qualifying contract (also in ccd-sum), and dropped
    LEAPS whose root ticker.map TOBASE-renames. R1-182: leaps-sum /
    ccd-sum totals mixed registered accounts into the headline."""

    LEAP = "ABC280121C00033000.TO"
    MAPPED_RAW = "QCX280121C00027000.US"
    MAPPED = "QCX280121C00027000.TO"
    TFSA_LEAP = "KVX280121C00009000.TO"

    def _project(self, tmp):
        root = Path(tmp)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n'
            '[accounts.tfsa]\ntype = "sheltered"\n')
        (root / "ticker.map").write_text("TOBASE QCX.US QCX.TO\n")
        work = root / "work"
        work.mkdir()
        (work / "margin_raw.json").write_text(json.dumps({"transactions": [
            _otx("2025-02-03", self.LEAP, 1, 200.0),
            _otx("2025-03-03", self.LEAP, -1, 350.0),
            _otx("2025-04-01", self.LEAP, -3, 2700.0),
            _otx("2025-05-01", self.LEAP, 3, 900.0),
            _otx("2025-02-03", self.MAPPED_RAW, 1, 100.0),
            _otx("2025-06-03", self.MAPPED_RAW, -1, 180.0)]}))
        (work / "margin_gains_wash.json").write_text(json.dumps(
            {"transactions": [
                _gain(self.LEAP, "2025-03-03", 150.0),
                _gain(self.LEAP, "2025-04-01", 2700.0, "SHORT",
                      grant=True, cost=-2700.0),
                _gain(self.LEAP, "2025-05-01", -900.0, "SHORT",
                      proceeds=-900.0),
                _gain(self.MAPPED, "2025-06-03", 80.0)]}))
        (work / "tfsa_raw.json").write_text(json.dumps({"transactions": [
            _otx("2025-02-03", self.TFSA_LEAP, 1, 100.0),
            _otx("2025-03-03", self.TFSA_LEAP, -1, 60.0)]}))
        (work / "tfsa_gains.json").write_text(json.dumps(
            {"transactions": [_gain(self.TFSA_LEAP, "2025-03-03", -40.0,
                                    account="tfsa")]}))
        return root

    def _json(self, root, *cmd):
        r = _run_cli(root, *cmd, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_leaps_sum_is_long_only_and_follows_tobase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            doc = self._json(root, "leaps-sum", "margin")
            rows = {r["contract"]: r for r in doc["rows"]}
            self.assertAlmostEqual(rows[self.LEAP]["gain"], 150.0)
            self.assertAlmostEqual(rows[self.LEAP]["qty"], 1.0)
            self.assertIn(self.MAPPED, rows)
            self.assertAlmostEqual(doc["total_gain"], 230.0)
            rows = self._json(root, "leaps", "margin")["rows"]
            self.assertFalse([r for r in rows
                              if r.get("direction") == "SHORT"])

    def test_totals_split_taxable_and_sheltered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            doc = self._json(root, "leaps-sum")
            self.assertAlmostEqual(doc["total_gain"], 190.0)
            self.assertAlmostEqual(doc["taxable_gain"], 230.0)
            self.assertAlmostEqual(doc["sheltered_gain"], -40.0)
            r = _run_cli(root, "leaps-sum")
            self.assertIn("SHELTERED", r.stdout)
            doc = self._json(root, "ccd-sum")
            self.assertAlmostEqual(doc["taxable_gain"], doc["total_gain"])
            self.assertAlmostEqual(doc["sheltered_gain"], 0.0)


class TestAuditUncoveredAccount(unittest.TestCase):
    """S047-16: audit noted an account with no books but exited 0, and
    the checklist marked every disposition traced and tied."""

    def test_missing_books_fail_the_audit(self):
        cfg = _CONFIG + "\n[accounts.cash]\ntype = \"taxable\"\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg)
            (root / "inputs" / "cash").mkdir()
            (root / "inputs" / "cash" / "q.csv").write_text(
                _MARGIN_CSV.replace("XEI.TO", "XIU.TO"))
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            self.assertEqual(_run_cli(root, "audit").returncode, 0)
            (root / "work" / "cash_base.json").unlink()
            r = _run_cli(root, "audit")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not audited", r.stderr)
        self.assertIn("cash", r.stderr)


class TestUnblendedBooksAreNotRunClean(unittest.TestCase):
    """S004-07: with the blended pass skipped (`run --account` on each
    account) the checklist marked 'Full run' done and the filing views
    served per-account ACB as Schedule 3 figures."""

    def test_per_account_runs_leave_run_clean_attention(self):
        from datetime import date
        from taxjson.lib.checklist import Ctx, d_run_clean
        from taxjson.lib.tomlcompat import tomllib
        cfg_text = _CONFIG + "\n[accounts.cash]\ntype = \"taxable\"\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg_text)
            (root / "inputs" / "cash").mkdir()
            (root / "inputs" / "cash" / "q.csv").write_text(
                _MARGIN_CSV.replace("55500001", "55500004"))
            for a in ("margin", "cash"):
                r = _run_cli(root, "run", "--no-input", "--account", a)
                self.assertEqual(r.returncode, 0, r.stderr)
            cfg = tomllib.loads(cfg_text)
            res = d_run_clean(Ctx(root, cfg, 2025, date.today(),
                                  lambda *a, **k: (0, "", "")))
            self.assertEqual(res.status, "attention", res.detail)
            self.assertIn("blended", res.detail)
            # ... and the filing commands say so (run-state banner).
            self.assertIn("blended", _run_cli(root, "sum").stderr)
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            res = d_run_clean(Ctx(root, cfg, 2025, date.today(),
                                  lambda *a, **k: (0, "", "")))
            self.assertNotIn("blended", res.detail)


class TestElectionHintsAreAmounts(unittest.TestCase):
    """S039-00: `elect --hint allocated_acb=-2000` (and the interactive
    prompt) saved a negative hint — basis created from nothing, negative
    dividend income — and nan/inf failed only on the next run."""

    def test_cli_refuses_negative_and_non_finite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            for v in ("-2000", "nan", "inf"):
                with self.subTest(v=v):
                    r = _run_cli(root, "elect", "margin", "--set",
                                 "ev1=rollover_s_86_1", "--hint",
                                 f"allocated_acb_cad={v}")
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("allocated_acb_cad", r.stderr)
            self.assertFalse((root / "inputs" / "margin" / "manifest.json")
                             .exists() and "-2000" in (
                root / "inputs" / "margin" / "manifest.json").read_text())

    def test_hand_edited_negative_hint_is_refused_on_load(self):
        from taxjson.lib.corp_actions import Manifest
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "manifest.json"
            p.write_text(json.dumps({"elections": {"ev1": {
                "summary": "x", "election": "taxable_deemed_dividend",
                "hints": {"fmv_per_share": -3}}}}))
            with self.assertRaises(ValueError) as cm:
                Manifest.load(p)
        self.assertIn("fmv_per_share", str(cm.exception))


_IB_MERGER = '''\
Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,CAD,SSL,"2025-02-05, 09:31:00",1600,15.90,0,-25440.0,-1,0,0,0,O
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (RGLD.CAD, ROYAL GOLD INC, US0000000002)",100.0026,0,25840.67184,0,
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","SSL(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (SSL, SANDSTORM GOLD LTD, CA0000000001)",-1600.0416,0,-25920.67392,0,
'''


class TestCorpActionsFollowSecurityOverrides(unittest.TestCase):
    """S004-00: an EXTRACT override (once ticker_extraction_overrides.txt)
    renamed the trades but
    not the corporate-action rows, so a merger consumed an empty
    un-overridden pool and the real position stayed put."""

    def test_merger_consumes_the_overridden_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            _set_config(root, _CONFIG)
            (root / "inputs" / "margin" / "ib.csv").write_text(_IB_MERGER)
            (root / "ticker.map").write_text(
                "EXTRACT SSL | CAD | SSLX.TO\n")
            r = _run_cli(root, "run", "--no-input")
            if r.returncode == 3:
                pend = json.loads((root / "work" / "pending_elections.json")
                                  .read_text())
                for ev in pend["accounts"]["margin"]["pending"]:
                    _run_cli(root, "elect", "margin", "--set",
                             f"{ev['event_id']}=taxable_disposition")
                r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            base = json.loads((root / "work" / "margin_base.json")
                              .read_text())["transactions"]
            syms = {t["symbol"] for t in base}
            self.assertNotIn("SSL.TO", syms)
            doc = json.loads(_run_cli(root, "list", "--json").stdout)
            held = {x["symbol"]: x["qty"] for x in doc["rows"]}
            # The fixture's event removes 1600.0416 shares (fractional
            # rounding in IB's own numbers); what matters is that the
            # 1600 overridden shares were consumed, not left in place.
            self.assertLess(abs(held.get("SSLX.TO", 0.0)), 1.0)


class TestHoldingsTomlStatesItsCostBasis(unittest.TestCase):
    """S037-24: holdings.toml's base_total_cost comes from a no-wash,
    per-account pass (no denied-loss bump, no s.47 blend) and never said
    so."""

    def test_meta_names_the_basis(self):
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, csv=_QT_HEADER + _MARGIN_CSV.splitlines(
                True)[1])
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            doc = tomllib.loads((root / "reports" / "margin_holdings.toml")
                                .read_text())
        self.assertIn("superficial-loss", doc["meta"]["base_cost_basis"])
        self.assertIn("taxjson list", doc["meta"]["base_cost_basis"])
        # A2-0226: the map adjustments are not in either cost.
        self.assertIn("[[distributions]]", doc["meta"]["base_cost_basis"])


if __name__ == "__main__":
    unittest.main()
