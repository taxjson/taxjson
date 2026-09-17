"""Regressions for REVIEW-2026-07-ui confirmed findings (minimal repros)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

_QT_ROWS = (
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XIU.TO,ISHARES,"
    "100,10.00,1000.00,0.00,-1000.00,CAD,12345,Trades,Individual\n"
    "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,XIU.TO,ISHARES,"
    "-100,19.90,1990.00,0.00,1990.00,CAD,12345,Trades,Individual\n")


def _proj(tmp, acct="qt"):
    root = Path(tmp)
    (root / "inputs" / acct).mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        f'[accounts.{acct}]\ntype = "taxable"\n')
    return root


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestR1_UppercaseInputsConsumed(unittest.TestCase):
    """#1 (critical): TRADES.CSV / START.TT imported by the GUI were
    invisible to the run's case-sensitive globs — exit 0, wrong totals."""

    def test_uppercase_csv_processed_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _proj(tmp)
            (root / "inputs" / "qt" / "TRADES.CSV").write_text(
                _QT_HEADER + _QT_ROWS)
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            gains = json.loads(
                (root / "work" / "qt_gains.json").read_text())
        self.assertAlmostEqual(
            float(gains["summary"]["total_gain"]), 990.0, places=2)

    def test_input_files_case_insensitive_both_suffixes(self):
        from taxjson.bin.taxjson_run import input_files
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for n in ("a.csv", "B.CSV", "c.Csv", "s.tt", "T.TT", "x.txt"):
                (d / n).write_text("x")
            self.assertEqual(
                [p.name for p in input_files(d, ".csv")],
                ["B.CSV", "a.csv", "c.Csv"])
            self.assertEqual(
                [p.name for p in input_files(d, ".tt")], ["T.TT", "s.tt"])

    def test_unconfigured_dir_warning_sees_uppercase(self):
        from taxjson.bin.taxjson_run import load_config, validate_config
        with tempfile.TemporaryDirectory() as tmp:
            root = _proj(tmp)
            stray = root / "inputs" / "orphan"
            stray.mkdir()
            (stray / "DATA.CSV").write_text(_QT_HEADER)
            warnings = validate_config(load_config(root),
                                       root / "inputs")
        self.assertTrue(any("orphan" in w for w in warnings), warnings)


class TestR1_BrokerHintPrefixOnly(unittest.TestCase):
    """#3: 'kr_' matched ANYWHERE in the name — ibkr_statement.csv went
    to the Kraken parser (0 transactions, exit 0). Underscore hints are
    prefix-only now; word hints (kraken/coinbase) stay substrings."""

    _IB = ("Statement,Header,Field Name,Field Value\n"
           "Statement,Data,BrokerName,Interactive Brokers\n")

    def test_mid_name_kr_no_longer_hijacks(self):
        from taxjson.bin.taxjson_run import detect_broker
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ibkr_statement.csv"
            p.write_text(self._IB)
            self.assertEqual(detect_broker(p), "ib")

    def test_prefix_and_word_hints_still_route(self):
        from taxjson.bin.taxjson_run import detect_broker
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            cases = {"kr_ledger.csv": "kraken",
                     "my_kraken_export.csv": "kraken",
                     "cb_fills.csv": "coinbase",
                     "coinbase-2025.csv": "coinbase"}
            for name, want in cases.items():
                f = d / name
                f.write_text("anything\n")
                self.assertEqual(detect_broker(f), want, name)


class TestR2_ElectionsCluster(unittest.TestCase):
    """#4/#5/#6/#7/#33/#34: the elect/--set/--redo/pending-file family.
    Elections are the one non-rebuildable user artifact — every path
    that could silently drop or wipe one now refuses cleanly."""

    @staticmethod
    def _merger_project(root, accounts=("margin",)):
        try:                       # discover puts tests/ on sys.path…
            from test_pending_elections import _SSL_RGLD_CSV
        except ImportError:        # …`-m unittest tests.x` does not
            from tests.test_pending_elections import _SSL_RGLD_CSV
        root.mkdir(parents=True, exist_ok=True)
        acct_sections = "".join(
            f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            + acct_sections)
        for a in accounts:
            (root / "inputs" / a).mkdir(parents=True)
            (root / "inputs" / a / "ib.csv").write_text(_SSL_RGLD_CSV)
        return root

    def test_set_family_validation_and_redo_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._merger_project(Path(tmp) / "p")
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr)
            pend = json.loads(
                (root / "work" / "pending_elections.json").read_text())
            ev = pend["accounts"]["margin"]["pending"][0]["event_id"]

            # #7: --set without an account must error, not exit 0 silently.
            r = _run(root, "elect", "--set", f"{ev}=ignore")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--set/--hint need an account", r.stderr)

            # #34: empty event id refused.
            r = _run(root, "elect", "margin", "--set", "=ignore")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("empty EVENT_ID", r.stderr)

            # #6: election validated against THIS event's options.
            r = _run(root, "elect", "margin", "--set",
                     f"{ev}=rollover_s_86_1")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("not valid for event", r.stderr)

            # #5: non-numeric hint refused at save time, not next run.
            r = _run(root, "elect", "margin", "--set",
                     f"{ev}=taxable_disposition",
                     "--hint", "fmv_per_share=12,50")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is not a number", r.stderr)

            # Valid save still works.
            r = _run(root, "elect", "margin", "--set", f"{ev}=ignore")
            self.assertEqual(r.returncode, 0, r.stderr)
            manifest = root / "inputs" / "margin" / "manifest.json"
            self.assertIn(ev, json.loads(manifest.read_text())["elections"])

            # #34: --reset --event '' must NOT widen to all elections.
            r = _run(root, "elect", "margin", "--reset", "--event", "")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(ev,
                          json.loads(manifest.read_text())["elections"])

            # #4: --redo without a TTY refuses BEFORE wiping anything.
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "elect", "margin", "--redo"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("needs a terminal", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn(ev,
                          json.loads(manifest.read_text())["elections"])

    def test_single_account_run_keeps_other_accounts_pending(self):
        # #33: resolving one account then `run --account X` used to
        # delete the aggregate pending file for ALL accounts.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._merger_project(Path(tmp) / "p",
                                        accounts=("margin", "broker2"))
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr)
            agg = root / "work" / "pending_elections.json"
            pend = json.loads(agg.read_text())
            self.assertEqual(sorted(pend["accounts"]),
                             ["broker2", "margin"])
            ev = pend["accounts"]["margin"]["pending"][0]["event_id"]
            r = _run(root, "elect", "margin", "--set", f"{ev}=ignore")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(root, "run", "--no-input", "--account", "margin")
            self.assertEqual(r.returncode, 0, r.stderr)
            # broker2's ready-to-copy --set lines survive.
            self.assertTrue(agg.exists())
            self.assertEqual(
                sorted(json.loads(agg.read_text())["accounts"]),
                ["broker2"])
            r = _run(root, "elect", "--pending")
            self.assertIn("broker2", r.stdout)


class TestR4_TaintedConsistency(unittest.TestCase):
    """#2: tainted (phantom-basis) dispositions were counted silently
    by winners/sum/ccd-sum while form-export/carryover/leaps exclude
    them — a $0-cost phantom ranked as a top winner and totals could
    not be reconciled. winners/ccd-sum now exclude + warn; sum keeps
    engine parity but warns with the count."""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": "2025"}, "transactions": [
                {"date": "2025-03-01", "symbol": "AAA.TO", "qty": 10,
                 "proceeds": 2000.0, "cost": 1000.0, "gain": 1000.0,
                 "currency": "CAD", "days_held": 30},
                {"date": "2025-06-15", "symbol": "PHANTOM.TO",
                 "qty": 10, "proceeds": 1000.0, "cost": 0.0,
                 "gain": 1000.0, "currency": "CAD", "days_held": 5,
                 "tainted": True},
                # Tainted SHORT call — the ccd-sum sibling.
                {"date": "2025-05-01",
                 "symbol": "AAA250620C00015000.TO", "qty": -1,
                 "proceeds": 0.0, "cost": -300.0, "gain": 300.0,
                 "direction": "SHORT", "currency": "CAD",
                 "days_held": 10, "tainted": True}]}))
        return root

    def test_winners_excludes_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(self._project(tmp), "winners", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        tickers = {row["ticker"] for row in doc["rows"]}
        self.assertNotIn("PHANTOM.TO", tickers)
        self.assertEqual(doc["tainted_skipped"], 2)
        self.assertAlmostEqual(doc["total_gain"], 1000.0, places=2)
        self.assertIn("tainted", r.stderr)

    def test_ccd_sum_excludes_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(self._project(tmp), "ccd-sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["rows"], [])          # phantom call gone
        self.assertEqual(doc["tainted_skipped"], 1)
        self.assertIn("tainted", r.stderr)

    def test_sum_keeps_engine_parity_but_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(self._project(tmp), "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        # Totals still mirror the engine (all three rows)…
        self.assertAlmostEqual(doc["totals"]["realized"], 2300.0,
                               places=2)
        # …but the discrepancy vs filing commands is named.
        self.assertEqual(doc["tainted_included"], 2)
        self.assertIn("filing totals will differ", r.stderr)

    def test_sum_warns_on_pipeline_routed_tainted(self):
        """Round-five audit: pipeline-written gains files carry
        tainted rows OUT-of-line (manual_reporting_required), so the
        in-line count is 0 and the old warning was dead code — totals
        silently excluded routed dispositions with no signal."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            # Rewrite the gains file the way the PIPELINE writes it:
            # tainted rows stripped to manual_reporting_required.
            for p in (root / "work").glob("*_gains*.json"):
                doc = json.loads(p.read_text())
                keep, routed = [], []
                for t in doc.get("transactions", []):
                    (routed if t.get("tainted") else keep).append(t)
                if routed:
                    for t in routed:
                        t.pop("tainted", None)
                    doc["transactions"] = keep
                    doc["manual_reporting_required"] = routed
                    p.write_text(json.dumps(doc))
            r = _run(root, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["tainted_included"], 0)
        self.assertGreater(doc["tainted_routed"], 0)
        self.assertIn("EXCLUDE", r.stderr)
        self.assertIn("manual reporting", r.stderr)


class TestR5_EstimateMath(unittest.TestCase):
    """#24/#25/#41/#42/#43: USA Schedule-D cross-netting, estimate
    input validation, strict --json, and the province settings key."""

    def test_usa_mixed_signs_cross_net(self):
        from taxjson.lib.tax_estimate import estimate_usa
        kw = dict(qualified_div=0, pil=0, other_income=200000,
                  other_losses=0)
        # ST -1,000 / LT +5,000 must tax like pure LT +4,000 …
        mixed = estimate_usa(st=-1000, lt=5000, **kw)
        ctrl = estimate_usa(st=0, lt=4000, **kw)
        self.assertAlmostEqual(mixed["estimated_tax"],
                               ctrl["estimated_tax"], places=2)
        # … and the mirror like pure ST +4,000 (residual keeps character).
        mixed2 = estimate_usa(st=5000, lt=-1000, **kw)
        ctrl2 = estimate_usa(st=4000, lt=0, **kw)
        self.assertAlmostEqual(mixed2["estimated_tax"],
                               ctrl2["estimated_tax"], places=2)
        # A net-loss year still reduces tax (the $3,000 offset).
        self.assertLess(estimate_usa(st=-6000, lt=5000,
                                     **kw)["estimated_tax"], 0)

    def _ca_project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": "2025"}, "transactions": [
                {"date": "2025-03-01", "symbol": "AAA.TO", "qty": 10,
                 "proceeds": 500.0, "cost": 1000.0, "gain": -500.0,
                 "currency": "CAD", "days_held": 30}]}))
        return root

    def test_negative_and_nonfinite_estimate_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._ca_project(tmp)
            for flags in (("--other-losses", "-10000"),
                          ("--other-income", "-50000"),
                          ("--other-income", "nan"),
                          ("--other-income", "inf"),
                          ("--other-losses", "nan")):
                r = _run(root, "sum", "--province", "ON", *flags)
                self.assertNotEqual(r.returncode, 0, flags)
                self.assertIn("non-negative finite number", r.stderr)

    def test_json_output_never_emits_nan_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._ca_project(tmp)
            # Hand-corrupted gains file with a bare Infinity token
            # (json.loads accepts it; strict consumers do not).
            (root / "work" / "margin_gains.json").write_text(
                '{"summary": {"year": "2025"}, "transactions": ['
                '{"date": "2025-03-01", "symbol": "AAA.TO", "qty": 1,'
                ' "proceeds": Infinity, "cost": 0.0, "gain": Infinity,'
                ' "currency": "CAD", "days_held": 1}]}')
            r = _run(root, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Infinity", r.stdout)
        self.assertNotIn("NaN", r.stdout)
        json.loads(r.stdout)                      # strict-parseable

    def test_province_settings_key_recognized(self):
        from taxjson.bin.taxjson_run import load_config, validate_config
        with tempfile.TemporaryDirectory() as tmp:
            root = self._ca_project(tmp)
            (root / "taxjson.toml").write_text(
                (root / "taxjson.toml").read_text().replace(
                    'source_currencies = []',
                    'source_currencies = []\nprovince = "ON"'))
            warnings = validate_config(load_config(root),
                                       root / "inputs")
            self.assertFalse([w for w in warnings if "province" in w],
                             warnings)
            # And the estimate actually uses it.
            r = _run(root, "estimate")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("canada/ON", r.stdout)


class TestR6_CliRenderingHardening(unittest.TestCase):
    """#14/#15/#17/#27/#28/#32/#35/#39/#40/#44: crash and silent-
    rewrite fixes across CLI options and rendering."""

    def _proj(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": "2025"}, "transactions": [
                {"date": "2025-03-01", "symbol": "AAA.TO", "qty": 10,
                 "proceeds": 2000.0, "cost": 1000.0, "gain": 1000.0,
                 "currency": "CAD", "days_held": 30}]}))
        return root

    def test_fees_sum_json_empty_is_success(self):
        # #14: --json with zero fee rows KeyError'd while text was fine.
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_fees",
                 "--cache", tmp, "--year", "2025", "--json"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["total"]["total"], 0.0)

    def test_align_columns_space_in_first_cell(self):
        # #15: an account named "rrsp x" shifted every column.
        from taxjson.lib.report_model import align_columns
        out = align_columns(["ACCOUNT STOCK TOTAL",
                             "rrsp x 500.00 500.00"])
        self.assertIn("rrsp x", out[1])
        self.assertTrue(out[1].rstrip().endswith("500.00"))
        # Columns line up: STOCK header over 500.00.
        self.assertEqual(out[0].index("STOCK"), out[1].index("500.00"))

    def test_huge_period_tokens_no_traceback(self):
        # #32: 99999m / 9999999999d / 2026y crashed every period view.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            for tok in ("99999m", "9999999999d", "2026y"):
                r = _run(root, "winners", tok)
                self.assertNotIn("Traceback", r.stderr, tok)
                self.assertEqual(r.returncode, 0, (tok, r.stderr))

    def test_list_date_rejects_impossible_calendar_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(self._proj(tmp), "list", "--date", "2025-15-02")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a real calendar date", r.stderr)

    def test_list_date_warns_on_skipped_account(self):
        # #35: account with gains but no base.json vanished silently
        # from the as-of view.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            r = _run(root, "list", "--date", "2025-06-01")
            # No base.json for margin at all -> global error is fine;
            # add a second account WITH a base to hit the skip path.
            (root / "taxjson.toml").write_text(
                (root / "taxjson.toml").read_text()
                + '[accounts.other]\ntype = "taxable"\n')
            (root / "work" / "other_base.json").write_text(json.dumps(
                {"transactions": [
                    {"action": "BUYSELL", "date": "2025-01-05",
                     "date_settle": "2025-01-05", "time": "09:30:00",
                     "symbol": "XIU.TO", "quantity": 10, "price": 10.0,
                     "net_amount": 100.0, "currency": "CAD",
                     "account": "other"}]}))
            r = _run(root, "list", "--date", "2025-06-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("margin skipped", r.stderr)

    def test_init_year_range_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("0", "-5", "20255"):
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run",
                     "init", str(Path(tmp) / f"p{bad}"), "--country",
                     "ca", "--year", bad],
                    cwd=REPO_ROOT, capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0, bad)
                self.assertIn("not a plausible tax year", r.stderr)

    def test_winners_top_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            for bad in ("0", "-3"):
                r = _run(root, "winners", "--top", bad)
                self.assertNotEqual(r.returncode, 0, bad)
                self.assertIn("--top must be >= 1", r.stderr)

    def test_form_export_csv_dir_clean_error(self):
        # #39: --csv DIR was a raw IsADirectoryError losing the report.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            (root / "outdir").mkdir()
            r = _run(root, "form-export", "--csv", str(root / "outdir"))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("cannot write --csv", r.stderr)

    def test_serve_wildcard_host_accepts_lan_clients(self):
        # #17: allowed_hosts=["0.0.0.0"] rejected every real Host
        # header with 400.
        try:
            from fastapi.testclient import TestClient
        except (ImportError, RuntimeError):
            self.skipTest("fastapi not installed")
        from taxjson.web.app import create_app
        from taxjson.web.context import ProjectContext
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            ctx = ProjectContext.load(root)
            app = create_app(ctx, allowed_hosts=["*"])
            client = TestClient(app)
            r = client.get("/", headers={"host": "192.168.1.5:8765"})
            self.assertNotEqual(r.status_code, 400)


class TestR7_NativeViewsRoundTrip(unittest.TestCase):
    """#18/#19/#20/#36/#37: the taxtext views' round-trip contract and
    the .tt-only account gate."""

    _TT = ("BUYSELL 2025-01-06 09:30:00 BTC 0.123456789 CAD 100000 "
           "12345.68 0.00\n"
           "BUYSELL 2025-03-06 09:30:00 BTC -0.123456789 CAD 130000 "
           "16049.38 0.00\n")

    def _proj(self, tmp):
        root = _proj(tmp, acct="margin")
        (root / "inputs" / "margin" / "questrade.csv").write_text(
            _QT_HEADER +
            "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XIU.TO,"
            "ISHARES,100,10.00,1000.00,9.99,-1009.99,CAD,12345,Trades,"
            "Individual\n")
        return root

    def test_tt_only_account_builds(self):
        # #20: a .tt-only account was skipped ("no CSVs") and its whole
        # book vanished from every report at exit 0.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            (root / "taxjson.toml").write_text(
                (root / "taxjson.toml").read_text()
                + '[accounts.manual]\ntype = "taxable"\n')
            (root / "inputs" / "manual").mkdir()
            (root / "inputs" / "manual" / "manual.tt").write_text(
                self._TT)
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            s = _run(root, "sum", "--json")
            doc = json.loads(s.stdout)
            accts = {a["account"]: a for a in doc["accounts"]}
            self.assertIn("manual", accts)
            self.assertAlmostEqual(accts["manual"]["realized"],
                                   3703.70, places=2)

    def test_fee_column_and_roundtrip_id_stability(self):
        # #36: FEE printed 0.00 while fees/trades-sum saw 9.99;
        # #19: :.8f qty broke the re-import transaction id;
        # #18: the round-tripped line must carry the fee.
        try:
            from taxjson.bin.taxjson_convert_tt import parse_tt_line
        except ImportError:
            from taxjson.bin.taxjson_convert_tt import (  # noqa
                parse_tt_line)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            (root / "inputs" / "manual").mkdir()
            (root / "inputs" / "manual" / "manual.tt").write_text(
                self._TT)
            (root / "taxjson.toml").write_text(
                (root / "taxjson.toml").read_text()
                + '[accounts.manual]\ntype = "taxable"\n')
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            t = _run(root, "trades", "margin")
            self.assertIn("9.99", t.stdout.splitlines()[0])  # FEE shown
            v = _run(root, "events", "manual")
            first = v.stdout.splitlines()[0]
            self.assertIn("0.123456789", first)   # full precision
            orig = parse_tt_line(self._TT.splitlines()[0], "manual")
            rt = parse_tt_line(first, "manual")
            self.assertEqual(orig["id"], rt["id"])

    def test_roc_dil_print_empty_state(self):
        # #37: zero bytes at rc 0 was indistinguishable from a typo'd
        # window.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._proj(tmp)
            _run(root, "run", "--no-input")
            for view in ("roc", "dil"):
                r = _run(root, view)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("no", r.stdout.lower())
                self.assertTrue(r.stdout.strip(), view)


class TestR8_FilingTools(unittest.TestCase):
    """#21/#22/#23/#38: reconcile-slips must never certify what it
    could not read; carryover claims apply on the RETURN's year."""

    def test_reconcile_unreadable_proceeds_not_clean(self):
        import contextlib
        import io
        from taxjson.bin.taxjson_reconcile_slips import load_slip
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "slip.csv"
            bad.write_text("Symbol,Quantity,Proceeds of disposition\n"
                           "BCE.TO,200,7600.00\nBCE.TO,100,N/A\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                out = load_slip(bad)
        self.assertEqual(out.get("__dropped_rows__"), 1)
        self.assertIn("NOT", err.getvalue())
        self.assertAlmostEqual(out["BCE"]["proceeds"], 7600.0)

    def test_reconcile_cp1252_slip_parses(self):
        # #22: French-broker exports (cp1252 accents/em-dash) crashed
        # with a raw UnicodeDecodeError.
        from taxjson.bin.taxjson_reconcile_slips import load_slip
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "slip.csv"
            cp.write_bytes(
                "Symbol,Description,Quantity,Proceeds of disposition\n"
                "BCE.TO,BCE Inc — Montréal Québec,200,7600.00\n"
                .encode("cp1252"))
            out = load_slip(cp)
        self.assertAlmostEqual(out["BCE"]["proceeds"], 7600.0)

    def test_carryover_claim_in_no_disposition_year_applies(self):
        # #23: a claim on a later return with no book dispositions was
        # silently ignored — carryforward overstated.
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        nets = {2025: {"net": -24.97, "dispositions": 3}}
        led = build_canada_ledger(nets, {2026: 20.00})
        self.assertAlmostEqual(led["final_carryforward"], 4.97,
                               places=2)
        self.assertNotIn("unmatched_claims", led)

    def test_carryover_nonfinite_claims_rejected(self):
        # #38: '2025 nan' poisoned the ledger (APPLIED = full balance).
        import contextlib
        import io
        from taxjson.bin.taxjson_carryover import load_claimed
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "claimed.txt"
            f.write_text("2025 10.00\n2025 nan\n2025 inf\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                claims = load_claimed(f)
        self.assertEqual(claims, {2025: 10.0})
        self.assertEqual(err.getvalue().count("line ignored"), 2)


class TestR9_WebWhatIf(unittest.TestCase):
    """#26/#29/#30/#31: what-if input validation, the synthetic-id
    collision, and live config reload."""

    @classmethod
    def setUpClass(cls):
        try:
            import fastapi  # noqa: F401
            # Gate on what the tests actually use: TestClient's own
            # transport dep varies by starlette version (httpx vs
            # httpx2) — importing `httpx` by name silently skipped
            # this whole class on envs where TestClient works fine.
            from fastapi.testclient import TestClient  # noqa: F401
        except (ImportError, RuntimeError):
            raise unittest.SkipTest("fastapi test client not installed")

    def _client(self, d):
        from fastapi.testclient import TestClient
        from taxjson.web.app import create_app
        from taxjson.web.context import ProjectContext
        return TestClient(create_app(ProjectContext.load(d)))

    def _project(self, tmp):
        from datetime import date
        d = Path(tmp)
        (d / "work").mkdir()
        (d / "reports").mkdir()
        (d / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        today = date.today().isoformat()
        (d / "work" / "margin_base.json").write_text(json.dumps(
            {"transactions": [
                {"action": "BUYSELL", "date": "2025-01-02",
                 "date_settle": "2025-01-02", "time": "09:30:00",
                 "symbol": "AAA.TO", "quantity": 100, "price": 10.0,
                 "net_amount": 1000.0, "currency": "CAD",
                 "account": "margin"},
                # REAL same-day sale with the qty/price a what-if will
                # ask about — used to content-hash to the SAME id as
                # the synthetic sell and double the aggregates.
                {"action": "BUYSELL", "date": today,
                 "date_settle": today, "time": "09:31:00",
                 "symbol": "AAA.TO", "quantity": -40, "price": 15.0,
                 "net_amount": 600.0, "currency": "CAD",
                 "account": "margin"}]}))
        return d

    def test_no_id_collision_with_real_same_day_sale(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(self._project(tmp))
            r = c.get("/api/whatif",
                      params={"account": "margin", "symbol": "AAA.TO",
                              "qty": 40, "price": 15}).json()
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["cost_basis"], 400.0)   # was 800

    def test_nonfinite_and_nonpositive_inputs_rejected_no_500(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(self._project(tmp))
            for q, p in (("nan", "15"), ("10", "inf"),
                         ("10", "-15"), ("0", "15")):
                resp = c.get("/api/whatif",
                             params={"account": "margin",
                                     "symbol": "AAA.TO",
                                     "qty": q, "price": p})
                self.assertEqual(resp.status_code, 200, (q, p))
                self.assertFalse(resp.json()["ok"], (q, p))

    def test_accounts_added_while_serving_are_visible(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            d = self._project(tmp)
            c = self._client(d)
            self.assertEqual(c.get("/healthz").json()["accounts"],
                             ["margin"])
            (d / "taxjson.toml").write_text(
                (d / "taxjson.toml").read_text()
                + '[accounts.tfsa]\ntype = "sheltered"\n')
            os.utime(d / "taxjson.toml")
            self.assertIn("tfsa",
                          c.get("/healthz").json()["accounts"])


if __name__ == "__main__":
    unittest.main()
