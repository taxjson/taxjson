"""Fixes from the 2026-08 flow-consistency audit (cross-tool pass)."""
import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_GAINS = {
    "summary": {"year": 2026, "total_gain": -1500.0},
    "transactions": [
        {"symbol": "AAPL.US", "date": "2026-05-02", "qty": -100,
         "currency": "CAD", "proceeds": 9000.0, "cost": 10500.0,
         "gain": -1500.0, "days_held": 200, "commission": 0.0, "fee": 0.0},
    ],
    "inventory": [],
    "wash_sales": [],
}


def _project(td, sheltered=False):
    root = Path(td)
    accounts = '[accounts.margin]\ntype = "taxable"\n'
    if sheltered:
        accounts += '[accounts.rrsp]\ntype = "sheltered"\n'
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\n' + accounts)
    work = root / "work"
    work.mkdir()
    (work / "margin_gains.json").write_text(json.dumps(_GAINS))
    if sheltered:
        (work / "rrsp_gains.json").write_text(json.dumps(_GAINS))
    return root


def _run_cmd(cmd, root, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        cmd(argparse.Namespace(dir=str(root), **kw))
    return out.getvalue(), err.getvalue()


class TestSettingsKeyWhitelist(unittest.TestCase):
    def test_sectors_file_is_now_unknown(self):
        # `sectors_file` fed only `taxjson timeline`, removed with the
        # portfolio-analytics commands — a leftover key in an old
        # config must WARN, not silently pass as known.
        from taxjson.bin.taxjson_run import validate_config
        warnings = validate_config(
            {"settings": {"year": 2026, "sectors_file": "sectors.txt"},
             "accounts": {"m": {"type": "taxable"}}})
        self.assertTrue([w for w in warnings if "sectors_file" in w],
                        "removed setting should warn as unknown")

    def test_literal_year_resolves_with_root(self):
        from datetime import date
        from taxjson.bin.taxjson_run import _tx_period_cutoff
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            self.assertEqual(_tx_period_cutoff("2025", root),
                             date(2025, 1, 1))

    def test_tax_year_resolves_from_config(self):
        from datetime import date
        from taxjson.bin.taxjson_run import _tx_period_cutoff
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)          # [settings] year = 2026
            self.assertEqual(_tx_period_cutoff("tax_year", root),
                             date(2026, 1, 1))
            self.assertEqual(_tx_period_cutoff("ty", root),
                             date(2026, 1, 1))

    def test_without_root_year_tokens_still_reject(self):
        from taxjson.bin.taxjson_run import _tx_period_cutoff
        with self.assertRaises(SystemExit) as cm:
            _tx_period_cutoff("2025")
        # ... and the error must not advertise the unsupported forms.
        self.assertNotIn("tax_year", str(cm.exception))


class TestBasisLabelsFollowResolution(unittest.TestCase):
    def test_winners_names_pre_wash_when_no_wash_files(self):
        # The banner hard-coded "wash-adjusted basis" even when the
        # resolver fell back to the pre-wash gains files.
        from taxjson.bin.taxjson_run import cmd_winners
        with tempfile.TemporaryDirectory() as td:
            out, _ = _run_cmd(cmd_winners, _project(td),
                              period=None, account=None, top=None,
                              json=False)
        self.assertIn("basis: pre-wash", out)
        self.assertNotIn("wash-adjusted", out)

    def test_winners_json_carries_basis(self):
        from taxjson.bin.taxjson_run import cmd_winners
        with tempfile.TemporaryDirectory() as td:
            out, _ = _run_cmd(cmd_winners, _project(td),
                              period=None, account=None, top=None,
                              json=True)
        self.assertEqual(json.loads(out)["basis"], "pre-wash")


class TestWashSalesExplainJson(unittest.TestCase):
    def test_explain_plus_json_refuses(self):
        # --json was silently ignored under --explain (prose came out).
        from taxjson.bin.taxjson_run import cmd_wash_sales
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            with self.assertRaises(SystemExit) as cm:
                _run_cmd(cmd_wash_sales, root, account=None,
                         explain=True, json=True)
        self.assertIn("no JSON form", str(cm.exception))


class TestSumShelteredScopeNote(unittest.TestCase):
    def test_note_names_included_sheltered_accounts(self):
        from taxjson.bin.taxjson_run import cmd_summary
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, sheltered=True)
            out, _ = _run_cmd(cmd_summary, root)
            self.assertIn("include sheltered account(s) rrsp", out)
            jout, _ = _run_cmd(cmd_summary, root, json=True)
        self.assertEqual(json.loads(jout)["sheltered_included"], ["rrsp"])

    def test_no_note_without_sheltered_accounts(self):
        from taxjson.bin.taxjson_run import cmd_summary
        with tempfile.TemporaryDirectory() as td:
            out, _ = _run_cmd(cmd_summary, _project(td))
        self.assertNotIn("sheltered account", out)


class TestRbcTaxRowSuffix(unittest.TestCase):
    def test_tax_row_follows_market_currency_like_dividend(self):
        # A .TO listing paying USD dividends: the TAX row got a phantom
        # .US symbol, so per-symbol dividend/tax pairing never matched.
        import os
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        content = (
            '"Date","Activity","Symbol","Symbol Description","Quantity",'
            '"Price","Settlement Date","Account","Value","Currency",'
            '"Description"\n'
            '"January 15, 2025","Buy","BNS","","100","60.00",'
            '"January 16, 2025","12345678","-6000.00","CAD",'
            '"BANK OF NOVA SCOTIA"\n'
            '"March 20, 2025","Taxes","BNS","","0","",'
            '"March 20, 2025","12345678","-4.50","USD",'
            '"BANK OF NOVA SCOTIA NON-RES TAX WITHHELD"\n'
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                         delete=False) as f:
            f.write(content)
            fname = f.name
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                txs = RbcBrokerage().parse_file(Path(fname))
        finally:
            os.remove(fname)
        tax = [t for t in txs if t["action"] == "TAX"]
        self.assertEqual(len(tax), 1)
        self.assertEqual(tax[0]["symbol"], "BNS.TO",
                         "TAX row must carry the traded market's "
                         "suffix, not the payment currency's")
        self.assertEqual(tax[0]["currency"], "USD")   # payment currency kept
        self.assertAlmostEqual(tax[0]["net_amount"], 4.50)  # + = withheld


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestWashRadarCli(unittest.TestCase):
    def _full_project(self, tmp):
        root = Path(tmp)
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "inputs" / "margin" / "questrade.csv").write_text(
            _QT_HEADER +
            "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,D,"
            "100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n")
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stderr
        return root

    def test_json_flag_emits_structured_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._full_project(tmp)
            r = _cli(root, "wash-radar", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertIn("sections", doc)
        self.assertEqual(doc.get("schema_version"), 1)

    def test_bad_date_is_a_usage_error_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._full_project(tmp)
            r = _cli(root, "wash-radar", "--date", "2025-15-02")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("not a real calendar date", r.stderr)


class TestFastMapDeletionInvalidates(unittest.TestCase):
    def test_deleting_distributions_map_rebuilds_under_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
                "D,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,"
                "Individual\n")
            (root / "distributions.map").write_text(
                "XEI.TO 2025-06-01 0.50\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            base = (root / "work" / "margin_base.json").read_text()
            self.assertIn('"ADJUST"', base,
                          "distributions.map should have produced an "
                          "ADJUST row")
            (root / "distributions.map").unlink()
            r = _cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            base = (root / "work" / "margin_base.json").read_text()
        self.assertNotIn(
            '"ADJUST"', base,
            "deleting distributions.map went unnoticed under --fast — "
            "its ADJUST rows survived in the cached books")




class TestCombinedRadarSidecar(unittest.TestCase):
    """A loss sold in one taxable account with a rebuy in another IS a
    superficial-loss trigger (the blended engine disallows it), but
    each per-account sidecar sees only its own book. The pipeline now
    also writes wash_radar_COMBINED.{rpt,json} from one all-taxable
    run — the sidecar harvest's ADVISORY prefers."""

    def test_cross_account_trigger_visible_only_in_combined(self):
        from datetime import date, timedelta
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for acct in ("margin", "cash"):
                (root / "inputs" / acct).mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = %d\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.cash]\ntype = "taxable"\n' % date.today().year)
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                f"{d(60)} 09:30:00 AM,{d(59)} 12:00:00 AM,Buy,XEI.TO,D,"
                f"100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n"
                f"{d(10)} 09:30:00 AM,{d(9)} 12:00:00 AM,Sell,XEI.TO,D,"
                f"-100,8.00,800.00,0.00,800.00,CAD,1,Trades,Individual\n")
            (root / "inputs" / "cash" / "questrade.csv").write_text(
                _QT_HEADER +
                f"{d(5)} 09:30:00 AM,{d(4)} 12:00:00 AM,Buy,XEI.TO,D,"
                f"100,8.00,800.00,0.00,-800.00,CAD,1,Trades,Individual\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            combined = root / "reports" / "wash_radar_COMBINED.json"
            per_acct = root / "reports" / "wash_radar_margin.json"
            self.assertTrue(combined.exists(),
                            "combined radar sidecar missing")
            self.assertTrue(per_acct.exists())

            def cats(p):
                doc = json.loads(p.read_text())
                return {r2["ticker"]: sec["category"]
                        for sec in doc.get("sections") or []
                        for r2 in sec.get("rows") or []}
            comb, marg = cats(combined), cats(per_acct)
        # Combined book: loss sold AND still held (via cash) →
        # VIOLATION. Margin alone: sold out, no rebuy visible →
        # merely COOLING.
        self.assertEqual(comb.get("XEI.TO"), "VIOLATION", comb)
        self.assertNotEqual(marg.get("XEI.TO"), "VIOLATION", marg)


class TestFeeRebateNetting(unittest.TestCase):
    def test_rebate_nets_into_summary_fees(self):
        # A commission REBATE (negative fee) was silently dropped from
        # summary.total_fees_by_currency (`fee <= 0: continue`), so
        # `.sum` FEES disagreed with `fees-sum` (which nets) by the
        # rebate amount.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "b.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2025-06-02",
                 "date_settle": "2025-06-03", "time": "09:30:00",
                 "symbol": "AAA.TO", "quantity": 100, "price": 10.0,
                 "net_amount": 1000.0, "currency": "CAD", "account": "m",
                 "commission": 9.95},
                {"action": "BUYSELL", "date": "2025-07-02",
                 "date_settle": "2025-07-03", "time": "09:30:00",
                 "symbol": "AAA.TO", "quantity": -100, "price": 12.0,
                 "net_amount": 1200.0, "currency": "CAD", "account": "m",
                 "commission": -2.00}]}))       # rebate
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--year", "2025", str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
        fees = out["summary"]["total_fees_by_currency"]["CAD"]
        self.assertAlmostEqual(fees["total"], 7.95,
                               msg="rebate must net against the total")


class TestCloseYearStalenessGuard(unittest.TestCase):
    def test_stale_wash_file_hard_stops(self):
        # After `run --account X` the wash file predates the rebuilt
        # plain gains (blend skipped). close-year WRITES the filing
        # lock, so it must refuse — `sum` merely notes it.
        import os
        import time
        from taxjson.bin.taxjson_run import cmd_close_year
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            work = root / "work"
            (work / "margin_gains_wash.json").write_text(
                json.dumps(_GAINS))
            time.sleep(0.02)
            # Plain gains rebuilt AFTER the wash file.
            now = time.time()
            os.utime(work / "margin_gains.json", (now + 5, now + 5))
            with self.assertRaises(SystemExit) as cm:
                _run_cmd(cmd_close_year, root, year=None, force=False)
        self.assertIn("STALER", str(cm.exception))


class TestCombinedSidecarStaleness(unittest.TestCase):
    """Post-build adversarial pass: a leftover COMBINED radar pair
    must not shadow fresh per-account sidecars."""

    def test_single_account_run_removes_stale_combined(self):
        from datetime import date, timedelta
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = %d\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                % date.today().year)
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                f"{d(30)} 09:30:00 AM,{d(29)} 12:00:00 AM,Buy,XEI.TO,D,"
                f"100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,"
                f"Individual\n")
            reports = root / "reports"
            reports.mkdir()
            (reports / "wash_radar_COMBINED.json").write_text("{}")
            (reports / "wash_radar_COMBINED.rpt").write_text("stale")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(
                (reports / "wash_radar_COMBINED.json").exists(),
                "stale COMBINED sidecar must be removed on a "
                "single-taxable-account run")
            self.assertFalse(
                (reports / "wash_radar_COMBINED.rpt").exists())

    def test_close_year_guard_skips_missing_plain_gains(self):
        # Wash file present, plain gains absent: not stale — the guard
        # falsely hard-stopped (the missing plain 'won' the freshness
        # compare).
        import os
        from taxjson.bin.taxjson_run import cmd_close_year
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            work = root / "work"
            os.rename(work / "margin_gains.json",
                      work / "margin_gains_wash.json")
            out, _ = _run_cmd(cmd_close_year, root, year=None,
                              force=False)
        self.assertIn("closed", out)




class Test2026_08_21AuditFixes(unittest.TestCase):
    """Second high-effort audit round."""

    def test_split_gains_nets_rebates_like_pipeline(self):
        from taxjson.bin.taxjson_split_gains import split_for_account
        combined = {"summary": {"year": 2026},
                    "transactions": [
                        {"action": "BUYSELL", "date": "2026-02-01",
                         "date_settle": "2026-02-02",
                         "symbol": "A.TO", "qty": 100, "gain": 0.0,
                         "account": "m", "currency": "CAD",
                         "commission": 5.0},
                    ], "inventory": [], "wash_sales": []}
        base = {"transactions": [
            {"action": "BUYSELL", "date": "2026-02-01",
             "date_settle": "2026-02-02", "time": "09:30:00",
             "symbol": "A.TO", "quantity": 100, "price": 10.0,
             "net_amount": 1000.0, "currency": "CAD", "account": "m",
             "commission": 5.0},
            {"action": "BUYSELL", "date": "2026-03-01",
             "date_settle": "2026-03-02", "time": "09:30:00",
             "symbol": "A.TO", "quantity": -100, "price": 11.0,
             "net_amount": 1100.0, "currency": "CAD", "account": "m",
             "commission": -5.0},          # rebate
        ]}
        doc = split_for_account(combined, "m", base["transactions"])
        fees = (doc.get("summary") or {}).get(
            "total_fees_by_currency") or {}
        self.assertAlmostEqual(fees.get("CAD", {}).get("total", 0.0),
                               0.0, places=2,
                               msg="rebates must net (pipeline parity)")

    def test_transfer_guard_scoped_to_account_and_counts_assign(self):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers

        def tx(action, date, acct, qty, sym="A.TO"):
            return TaxTransaction(action=action, date=date,
                                  time="09:30:00", symbol=sym,
                                  quantity=qty, currency="CAD",
                                  account=acct, net_amount=0.0)
        # (a) sibling account's BUYSELL must NOT block the drop
        txs = [tx("TRANSFER", "2026-01-10", "a", -100),
               tx("TRANSFER", "2026-01-20", "a", 100),
               tx("BUYSELL", "2026-01-15", "b", 50)]
        kept, dropped = _drop_self_cancelling_transfers(txs)
        self.assertEqual(len(dropped), 1,
                         "another account's trade falsely blocked")
        # (b) an ASSIGN in the same account MUST block it
        txs = [tx("TRANSFER", "2026-01-10", "a", -100),
               tx("TRANSFER", "2026-01-20", "a", 100),
               tx("ASSIGN", "2026-01-15", "a", -100)]
        kept, dropped = _drop_self_cancelling_transfers(txs)
        self.assertEqual(dropped, [],
                         "ASSIGN is a trade — the gap must surface")

    def test_leaps_empty_json_carries_basis(self):
        from taxjson.bin.taxjson_run import cmd_leaps
        with tempfile.TemporaryDirectory() as td:
            out, _ = _run_cmd(cmd_leaps, _project(td), period=None,
                              account=None, json=True)
        doc = json.loads(out)
        self.assertIn("basis", doc)
        self.assertEqual(doc["rows"], [])

    def test_sum_account_positional(self):
        from taxjson.bin.taxjson_run import cmd_summary
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, sheltered=True)
            out, _ = _run_cmd(cmd_summary, root, account="margin",
                              json=True)
            doc = json.loads(out)
            self.assertEqual([a["account"] for a in doc["accounts"]],
                             ["margin"])
            with self.assertRaises(SystemExit) as cm:
                _run_cmd(cmd_summary, root, account="nope")
        self.assertIn("nope", str(cm.exception))


class TestCanBuy(unittest.TestCase):
    """`taxjson buy-check` — buy-side wash safety."""

    def _project(self, tmp, with_recent_loss=True):
        from datetime import date, timedelta
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        root = Path(tmp)
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = %d\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n' % date.today().year)
        rows = (f"{d(60)} 09:30:00 AM,{d(59)} 12:00:00 AM,Buy,XEI.TO,D,"
                f"100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Ind\n")
        if with_recent_loss:
            rows += (f"{d(10)} 09:30:00 AM,{d(9)} 12:00:00 AM,Sell,"
                     f"XEI.TO,D,-100,8.00,800.00,0.00,800.00,CAD,1,"
                     f"Trades,Ind\n")
        (root / "inputs" / "margin" / "questrade.csv").write_text(
            _QT_HEADER + rows)
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stderr
        return root

    def test_recent_loss_sale_is_unsafe_with_clear_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, with_recent_loss=True)
            # Root form must match the .TO listing.
            r = _cli(root, "buy-check", "XEI")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("UNSAFE", r.stdout)
        self.assertIn("cancels it", r.stdout)
        self.assertIn("Safe to buy from", r.stdout)

    def test_open_position_no_recent_loss_is_safe_star(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, with_recent_loss=False)
            r = _cli(root, "buy-check", "XEI.TO")
        # Bought 60 days ago, held: no loss sale — safe; the recent-buy
        # window has passed too, so plain SAFE is also acceptable
        # depending on radar category; must NOT be UNSAFE.
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("UNSAFE", r.stdout)

    def test_unknown_symbol_is_safe_with_forward_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _cli(root, "buy-check", "ZZZT")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no wash exposure", r.stdout)
        self.assertIn("30-day window", r.stdout)

    def test_json_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _cli(root, "buy-check", "XEI", "ZZZT", "--json")
        doc = json.loads(r.stdout)
        by = {x["symbol"]: x for x in doc["results"]}
        self.assertEqual(by["XEI"]["verdict"], "UNSAFE")
        self.assertEqual(by["ZZZT"]["verdict"], "SAFE")


class TestBuyCheckClassShares(unittest.TestCase):
    def test_class_ticker_matches_with_and_without_suffix(self):
        from datetime import date, timedelta
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = %d\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                % date.today().year)
            (root / "inputs" / "margin" / "generic_x.csv").write_text(
                "Date,Transaction type,Symbol,Quantity,Price,Amount,"
                "Currency\n"
                # Base-currency rows: with `source_currencies = []` the
                # pipeline's rates file is empty, and a currency absent
                # from it is now a fatal merge2 error (stage-tools
                # audit) rather than a silent 1.35 conversion.
                f"{d(60)},BUY,BRK.B,10,400.00,-4000.00,CAD\n"
                f"{d(10)},SELL,BRK.B,10,300.00,3000.00,CAD\n")
            (root / "inputs" / "margin" / "generic.toml").write_text(
                '[columns]\ndate = "Date"\n'
                'action = "Transaction type"\nsymbol = "Symbol"\n'
                'quantity = "Quantity"\nprice = "Price"\n'
                'amount = "Amount"\ncurrency = "Currency"\n'
                '[formats]\ndate = "%Y-%m-%d"\n'
                '[actions]\n"BUY" = "buy"\n"SELL" = "sell"\n')
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            # Loss sold 10 days ago: UNSAFE under BOTH spellings —
            # the naive last-dot root turned BRK.B into "BRK" and
            # reported a false SAFE.
            for spelling in ("BRK.B", "BRK.B.US"):
                r = _cli(root, "buy-check", spelling)
                self.assertEqual(r.returncode, 1,
                                 f"{spelling}: {r.stdout}")
                self.assertIn("UNSAFE", r.stdout, spelling)


class TestBuyCheckMappedCrossListings(unittest.TestCase):
    def test_different_root_tobase_pair_matches_both_ways(self):
        # BTG.US <-> BTO.TO: the roots differ, only ticker.map knows
        # they are one security — without folding the map, buy-check
        # false-SAFEd the unmapped spelling.
        from datetime import date, timedelta
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = %d\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                % date.today().year)
            (root / "ticker.map").write_text(
                "TOBASE BTG.US  BTO.TO\n")
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                f"{d(60)} 09:30:00 AM,{d(59)} 12:00:00 AM,Buy,BTO.TO,"
                f"D,100,5.00,500.00,0.00,-500.00,CAD,1,Trades,Ind\n"
                f"{d(10)} 09:30:00 AM,{d(9)} 12:00:00 AM,Sell,BTO.TO,"
                f"D,-100,4.00,400.00,0.00,400.00,CAD,1,Trades,Ind\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            for spelling in ("BTG", "BTG.US", "BTO", "BTO.TO"):
                r = _cli(root, "buy-check", spelling)
                self.assertEqual(r.returncode, 1,
                                 f"{spelling}: {r.stdout}")
                self.assertIn("UNSAFE", r.stdout, spelling)


class TestBuyCheckLastLossLine(unittest.TestCase):
    def test_old_loss_shows_as_sanity_line(self):
        from datetime import date, timedelta
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = %d\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                % date.today().year)
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                f"{d(90)} 09:30:00 AM,{d(89)} 12:00:00 AM,Buy,XEI.TO,"
                f"D,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Ind\n"
                f"{d(60)} 09:30:00 AM,{d(59)} 12:00:00 AM,Sell,XEI.TO,"
                f"D,-100,8.00,800.00,0.00,800.00,CAD,1,Trades,Ind\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _cli(root, "buy-check", "XEI", "--json")
            doc = json.loads(r.stdout)
        res = doc["results"][0]
        # 60-day-old loss: SAFE, but the sanity line names it.
        self.assertEqual(res["verdict"], "SAFE")
        self.assertIsNotNone(res["last_loss"])
        self.assertAlmostEqual(res["last_loss"]["gain"], -200.0)
        joined = " ".join(res["detail"])
        self.assertIn("last loss sale this tax year", joined)
        self.assertIn("outside the 30-day window", joined)


class TestBuyCheckShelteredSide(unittest.TestCase):
    def test_sheltered_recent_buy_flows_into_the_verdict(self):
        # Taxable loss sold 40 days ago (outside the window) but the
        # RRSP bought the same name 10 days ago and still holds:
        # buying is safe TODAY (no recent taxable loss) but the open
        # window — driven by the SHELTERED buy — must surface as
        # SAFE*, proving sheltered acquisitions feed buy-check.
        from datetime import date, timedelta
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for acct in ("margin", "rrsp"):
                (root / "inputs" / acct).mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = %d\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n'
                % date.today().year)
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                f"{d(90)} 09:30:00 AM,{d(89)} 12:00:00 AM,Buy,XEI.TO,"
                f"D,200,10.00,2000.00,0.00,-2000.00,CAD,1,Trades,Ind\n"
                f"{d(40)} 09:30:00 AM,{d(39)} 12:00:00 AM,Sell,XEI.TO,"
                f"D,-100,8.00,800.00,0.00,800.00,CAD,1,Trades,Ind\n")
            (root / "inputs" / "rrsp" / "questrade.csv").write_text(
                _QT_HEADER +
                f"{d(10)} 09:30:00 AM,{d(9)} 12:00:00 AM,Buy,XEI.TO,"
                f"D,50,8.50,425.00,0.00,-425.00,CAD,1,Trades,Ind\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _cli(root, "buy-check", "XEI")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("SAFE*", r.stdout)
        self.assertIn("extends the wash window", r.stdout)
        # And the historic taxable loss shows as the sanity line.
        self.assertIn("last loss sale this tax year", r.stdout)

    def test_missing_sheltered_context_notes_it(self):
        from datetime import date, timedelta
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = %d\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n'
                % date.today().year)
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                f"{d(60)} 09:30:00 AM,{d(59)} 12:00:00 AM,Buy,XEI.TO,"
                f"D,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Ind\n")
            # Partial run (one account) never builds sheltered_base.
            r = _cli(root, "run", "--no-input", "--account", "margin")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _cli(root, "buy-check", "XEI")
        self.assertIn("sheltered-account activity is invisible",
                      r.stderr)


class TestSellCheck(unittest.TestCase):
    """`taxjson sell-check` — the sell-side twin."""

    def _project(self, tmp, taxable_rows, rrsp_rows=None):
        from datetime import date
        root = Path(tmp)
        accounts = '[accounts.margin]\ntype = "taxable"\n'
        (root / "inputs" / "margin").mkdir(parents=True)
        if rrsp_rows is not None:
            (root / "inputs" / "rrsp").mkdir(parents=True)
            accounts += '[accounts.rrsp]\ntype = "sheltered"\n'
            (root / "inputs" / "rrsp" / "questrade.csv").write_text(
                _QT_HEADER + rrsp_rows)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = %d\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            % date.today().year + accounts)
        (root / "inputs" / "margin" / "questrade.csv").write_text(
            _QT_HEADER + taxable_rows)
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stderr
        return root

    @staticmethod
    def _d(n):
        from datetime import date, timedelta
        return (date.today() - timedelta(days=n)).isoformat()

    def test_locked_by_sheltered_buy_is_unsafe(self):
        d = self._d
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                tmp,
                f"{d(90)} 09:30:00 AM,{d(89)} 12:00:00 AM,Buy,XEI.TO,"
                f"D,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Ind\n",
                rrsp_rows=(
                    f"{d(10)} 09:30:00 AM,{d(9)} 12:00:00 AM,Buy,"
                    f"XEI.TO,D,50,8.50,425.00,0.00,-425.00,CAD,1,"
                    f"Trades,Ind\n"))
            r = _cli(root, "sell-check", "XEI")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("UNSAFE", r.stdout)
        self.assertIn("permanently denied", r.stdout)

    def test_violation_is_action_to_rescue(self):
        d = self._d
        with tempfile.TemporaryDirectory() as tmp:
            # The buy must fall INSIDE the ±30-day window for the
            # loss to be superficial (a 90-day-old buy is BLOCKED
            # territory, not VIOLATION).
            root = self._project(
                tmp,
                f"{d(20)} 09:30:00 AM,{d(19)} 12:00:00 AM,Buy,XEI.TO,"
                f"D,200,10.00,2000.00,0.00,-2000.00,CAD,1,Trades,Ind\n"
                f"{d(10)} 09:30:00 AM,{d(9)} 12:00:00 AM,Sell,XEI.TO,"
                f"D,-100,8.00,800.00,0.00,800.00,CAD,1,Trades,Ind\n")
            r = _cli(root, "sell-check", "XEI")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ACTION", r.stdout)

    def test_clear_is_safe_with_both_sides_caveat(self):
        d = self._d
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                tmp,
                f"{d(90)} 09:30:00 AM,{d(89)} 12:00:00 AM,Buy,XEI.TO,"
                f"D,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Ind\n")
            r = _cli(root, "sell-check", "XEI", "--json")
        doc = json.loads(r.stdout)
        res = doc["results"][0]
        self.assertEqual(res["verdict"], "SAFE")
        self.assertIn("EITHER side", " ".join(res["detail"]))


if __name__ == "__main__":
    unittest.main()
