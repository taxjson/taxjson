"""Tests for `taxjson events <period> [account]` and sibling query commands."""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _runsub(root, sub, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         sub, *args],
        cwd=REPO_ROOT, capture_output=True, text=True)


def _run(root, *args):
    return _runsub(root, "events", *args)


def _tx(action, dt, symbol, qty, price, net, currency="CAD", time="09:30:00"):
    return dict(action=action, date=dt, time=time, symbol=symbol, quantity=qty,
                price=price, net_amount=net, currency=currency)


def _project(tmp, files):
    root = Path(tmp)
    (root / "work").mkdir()
    for name, txs in files.items():
        (root / "work" / name).write_text(json.dumps({"transactions": txs}))
    return root


class TestTransactionsCmd(unittest.TestCase):
    def test_single_account_pure_taxtext_and_period_filter(self):
        recent = (date.today() - timedelta(days=3)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin_raw.json": [
                _tx("BUYSELL", "2000-01-01", "OLD.TO", 100, 1.0, 100.0),  # too old
                _tx("BUYSELL", recent, "AAA.TO", 10, 5.0, 50.0),
            ]})
            r = _run(root, "30d", "margin")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [l for l in r.stdout.splitlines()
                 if l.strip() and not l.startswith("TOTAL")]
        self.assertEqual(len(lines), 1)                     # OLD.TO filtered out
        self.assertTrue(lines[0].startswith("BUYSELL"))     # no account prefix
        self.assertIn("AAA.TO", lines[0])
        self.assertNotIn("OLD.TO", r.stdout)

    def test_all_accounts_prefixed_and_chronological(self):
        d1 = (date.today() - timedelta(days=3)).isoformat()
        d2 = (date.today() - timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {
                "margin_raw.json": [_tx("BUYSELL", d1, "MMM.TO", 1, 1.0, 1.0)],
                "rrsp_raw.json": [_tx("BUYSELL", d2, "RRR.TO", 1, 1.0, 1.0)],
            })
            r = _run(root, "1y")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [l for l in r.stdout.splitlines()
                 if l.strip() and not l.startswith("TOTAL")]
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("margin"))      # earlier date first
        self.assertIn("MMM.TO", lines[0])
        self.assertTrue(lines[1].startswith("rrsp"))
        self.assertIn("RRR.TO", lines[1])

    def test_crypto_uses_filled_when_no_raw(self):
        recent = (date.today() - timedelta(days=2)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"crypto_filled.json": [
                _tx("BUYSELL", recent, "BTC", 0.5, 60000.0, 30000.0, "USD")]})
            r = _run(root, "30d", "crypto")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BTC", r.stdout)
        self.assertIn("USD", r.stdout)                      # native currency

    def test_money_two_decimals_price_and_qty_precise(self):
        recent = (date.today() - timedelta(days=2)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin_raw.json": [
                _tx("BUYSELL", recent, "AAA.TO", 100, 0.25, 25.0),
                _tx("BUYSELL", recent, "BBB.TO", 100, 53.365, 5337.5003),
            ]})
            out = _run(root, "30d", "margin").stdout
        self.assertNotIn("0000000", out)      # no .tt padding zeros
        # money → exactly 2 decimals
        self.assertIn("25.00", out)
        self.assertIn("5,337.50", out)
        self.assertNotIn("5,337.5003", out)   # amount rounded to cents
        # price / qty keep their significant digits (not forced to 2dp)
        self.assertIn("53.365", out)
        self.assertIn("0.25", out)

    def test_bad_date_row_warns_and_excluded(self):
        recent = (date.today() - timedelta(days=2)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin_raw.json": [
                _tx("BUYSELL", recent, "GOOD.TO", 10, 5.0, 50.0),
                _tx("BUYSELL", "not-a-date", "BAD.TO", 10, 5.0, 50.0),
            ]})
            r = _runsub(root, "events", "1y", "margin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("GOOD.TO", r.stdout)
        self.assertNotIn("BAD.TO", r.stdout)               # excluded, not silent
        self.assertIn("missing/unparseable date", r.stderr)

    def test_invalid_period_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin_raw.json": []})
            r = _run(root, "banana", "margin")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid time period", r.stderr)

    def test_month_window_calendar(self):
        # A row ~2 months old is excluded by a 1m window, included by 3m.
        old = (date.today() - timedelta(days=62)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin_raw.json": [
                _tx("BUYSELL", old, "ZZZ.TO", 1, 1.0, 1.0)]})
            self.assertNotIn("ZZZ.TO", _run(root, "1m", "margin").stdout)
            self.assertIn("ZZZ.TO", _run(root, "3m", "margin").stdout)


class TestDividendsBuysell(unittest.TestCase):
    def _mixed(self, tmp):
        recent = (date.today() - timedelta(days=2)).isoformat()
        return _project(tmp, {"margin_raw.json": [
            _tx("BUYSELL", recent, "AAA.TO", 10, 5.0, 50.0),
            dict(action="DIVIDEND", date=recent, time="09:30:00", symbol="AAA.TO",
                 quantity=10, price=0.5, currency="CAD", net_amount=5.0,
                 gross_amount=5.0),
            dict(action="FEE", date=recent, time="09:30:00", symbol="", currency="CAD",
                 net_amount=-1.0),
        ]}), recent

    def test_dividends_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self._mixed(tmp)
            out = _runsub(root, "divs", "30d", "margin").stdout
        self.assertIn("DIVIDEND", out)
        self.assertNotIn("BUYSELL", out)
        self.assertNotIn("FEE", out)

    def test_buysell_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self._mixed(tmp)
            out = _runsub(root, "trades", "30d", "margin").stdout
        self.assertIn("BUYSELL", out)
        self.assertNotIn("DIVIDEND", out)
        self.assertNotIn("FEE", out)

    def test_dividends_total_footer(self):
        # dividends → only the dividend total (no buy/sell totals).
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self._mixed(tmp)
            out = _runsub(root, "divs", "30d", "margin").stdout
        self.assertIn("TOTAL DIVIDEND: 5.00 CAD", out)
        self.assertNotIn("TOTAL BUY", out)
        self.assertNotIn("TOTAL SELL", out)

    def test_buysell_totals_footer(self):
        recent = (date.today() - timedelta(days=2)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin_raw.json": [
                _tx("BUYSELL", recent, "AAA.TO", 10, 5.0, 50.0),    # buy 50
                _tx("BUYSELL", recent, "BBB.TO", -3, 20.0, 60.0),   # sell 60
            ]})
            out = _runsub(root, "trades", "30d", "margin").stdout
        self.assertIn("TOTAL BUY:", out)
        self.assertIn("50.00 CAD", out)
        self.assertIn("TOTAL SELL:", out)
        self.assertIn("60.00 CAD", out)
        self.assertNotIn("TOTAL DIVIDEND", out)

    def test_transactions_totals_footer(self):
        recent = (date.today() - timedelta(days=2)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin_raw.json": [
                _tx("BUYSELL", recent, "AAA.TO", 10, 5.0, 50.0),     # buy 50
                _tx("BUYSELL", recent, "BBB.TO", -3, 20.0, 60.0),    # sell 60
                dict(action="DIVIDEND", date=recent, time="09:30:00",
                     symbol="AAA.TO", quantity=10, price=0.5, currency="CAD",
                     net_amount=5.0, gross_amount=5.0),
            ]})
            out = _runsub(root, "events", "30d", "margin").stdout
        self.assertIn("TOTAL BUY:", out)
        self.assertIn("50.00 CAD", out)
        self.assertIn("TOTAL SELL:", out)
        self.assertIn("60.00 CAD", out)
        self.assertIn("TOTAL DIVIDEND:", out)
        self.assertIn("5.00 CAD", out)


class TestInstrumentFilters(unittest.TestCase):
    """`trades`/`gains` --options/--equities/--futures/--puts/--calls."""

    EQ = "AAA.TO"
    CALL = "AAA260116C00010000.US"
    PUT = "AAA260116P00010000.US"
    FUT = "F:CL.US"                       # plain future (no OCC block)
    FUT_PUT = "F:CL251220P00053000.US"    # futures option: future AND put

    def _trades_project(self, tmp):
        recent = (date.today() - timedelta(days=2)).isoformat()
        syms = [self.EQ, self.CALL, self.PUT, self.FUT, self.FUT_PUT]
        return _project(tmp, {"margin_raw.json": [
            _tx("BUYSELL", recent, s, 1, 1.0, 1.0) for s in syms]})

    def test_options_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _runsub(self._trades_project(tmp), "trades", "30d",
                          "margin", "--options").stdout
        self.assertIn(self.CALL, out)
        self.assertIn(self.PUT, out)
        self.assertIn(self.FUT_PUT, out)   # futures option is an option too
        self.assertNotIn(self.EQ, out)
        self.assertNotIn(self.FUT + " ", out)   # plain future excluded

    def test_equities_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _runsub(self._trades_project(tmp), "trades", "30d",
                          "margin", "--equities").stdout
        self.assertIn(self.EQ, out)
        self.assertNotIn(self.CALL, out)
        self.assertNotIn(self.PUT, out)
        self.assertNotIn("F:CL", out)

    def test_futures_only_includes_futures_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _runsub(self._trades_project(tmp), "trades", "30d",
                          "margin", "--futures").stdout
        self.assertIn(self.FUT, out)
        self.assertIn(self.FUT_PUT, out)   # F:-prefixed option counts
        self.assertNotIn(self.EQ, out)
        self.assertNotIn(self.CALL, out)

    def test_calls_and_puts(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._trades_project(tmp)
            calls = _runsub(proj, "trades", "30d", "margin", "--calls").stdout
            puts = _runsub(proj, "trades", "30d", "margin", "--puts").stdout
        self.assertIn(self.CALL, calls)
        self.assertNotIn(self.PUT, calls)
        self.assertNotIn(self.FUT_PUT, calls)   # it's a put
        self.assertIn(self.PUT, puts)
        self.assertIn(self.FUT_PUT, puts)       # futures put matches --puts
        self.assertNotIn(self.CALL, puts)

    def test_flags_or_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _runsub(self._trades_project(tmp), "trades", "30d",
                          "margin", "--equities", "--calls").stdout
        self.assertIn(self.EQ, out)
        self.assertIn(self.CALL, out)
        self.assertNotIn(self.PUT, out)

    def test_no_flag_shows_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = _runsub(self._trades_project(tmp), "trades", "30d",
                          "margin").stdout
        for s in (self.EQ, self.CALL, self.PUT, self.FUT, self.FUT_PUT):
            self.assertIn(s, out)

    def test_gains_equities_only(self):
        recent = (date.today() - timedelta(days=5)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "margin_raw_gains.json").write_text(json.dumps({
                "transactions": [
                    {"date": recent, "symbol": self.EQ, "qty": 10,
                     "currency": "CAD", "proceeds": 150.0, "cost": 100.0,
                     "gain": 50.0, "days_held": 30},
                    {"date": recent, "symbol": self.CALL, "qty": 1,
                     "currency": "CAD", "proceeds": 70.0, "cost": 100.0,
                     "gain": -30.0, "days_held": 5},
                ]}))
            eq = _runsub(root, "gains", "30d", "margin", "--equities").stdout
            opt = _runsub(root, "gains", "30d", "margin", "--options").stdout
        self.assertIn(self.EQ, eq)
        self.assertNotIn(self.CALL, eq)
        self.assertIn(self.CALL, opt)
        self.assertNotIn(self.EQ, opt)


class TestDisplayHelpers(unittest.TestCase):
    def test_tx_display_line_variants(self):
        from taxjson.bin.taxjson_run import _tx_display_line as f
        d, t = "2025-01-02", "09:30:00"
        # SPLIT: symbol_new + ratio, no money
        self.assertEqual(
            f({"action": "SPLIT", "date": d, "symbol": "A.TO",
               "symbol_new": "B.TO", "quantity": 1.5}),
            "SPLIT 2025-01-02 09:30:00 A.TO B.TO 1.5")
        # TRANSFER: qty/price + one money column
        self.assertEqual(
            f({"action": "TRANSFER", "date": d, "symbol": "A.TO", "quantity": 100,
               "currency": "CAD", "price": 10.0, "net_amount": 1000.0}),
            "TRANSFER 2025-01-02 09:30:00 A.TO 100 CAD 10 1,000.00")
        # INTEREST: currency + signed amount, no symbol
        self.assertEqual(
            f({"action": "INTEREST", "date": d, "currency": "USD",
               "net_amount": -5.0}),
            "INTEREST 2025-01-02 09:30:00 USD -5.00")
        # ADJUST: symbol currency amount
        self.assertEqual(
            f({"action": "ADJUST", "date": d, "symbol": "A.TO",
               "currency": "CAD", "net_amount": 12.5}),
            "ADJUST 2025-01-02 09:30:00 A.TO CAD 12.50")
        # actions with no taxtext form
        self.assertIsNone(f({"action": "OPENING_BALANCE", "date": d}))

    def test_align_columns(self):
        from taxjson.bin.taxjson_run import _align_columns
        self.assertEqual(
            _align_columns(["A bb ccc", "aaa b c"]),
            ["A   bb ccc", "aaa b  c"])

    def test_period_cutoff_units(self):
        from taxjson.bin.taxjson_run import _tx_period_cutoff
        from datetime import date, timedelta
        today = date.today()
        self.assertEqual(_tx_period_cutoff("10d"), today - timedelta(days=10))
        self.assertEqual(_tx_period_cutoff("2w"), today - timedelta(weeks=2))
        y = _tx_period_cutoff("1y")           # calendar year back
        self.assertEqual((y.year, y.month), (today.year - 1, today.month))
        # `all` (and `max`) mean no lower bound — full history.
        self.assertEqual(_tx_period_cutoff("all"), date.min)
        self.assertEqual(_tx_period_cutoff(" ALL "), date.min)
        self.assertEqual(_tx_period_cutoff("max"), date.min)
        with self.assertRaises(SystemExit):
            _tx_period_cutoff("banana")


class TestGainsCmd(unittest.TestCase):
    def test_native_gains_dispositions_only_with_total(self):
        recent = (date.today() - timedelta(days=5)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "margin_raw_gains.json").write_text(json.dumps({
                "transactions": [
                    # disposition (no action) — should show
                    {"date": recent, "symbol": "AAA.TO", "qty": 10,
                     "currency": "CAD", "proceeds": 150.0, "cost": 100.0,
                     "gain": 50.0, "days_held": 30},
                    # dividend row — should be excluded from gains
                    {"action": "DIVIDEND", "date": recent, "symbol": "AAA.TO",
                     "dividend": 5.0, "currency": "CAD", "proceeds": 0.0,
                     "cost": 0.0, "gain": 0.0},
                ]}))
            out = _runsub(root, "gains", "30d", "margin").stdout
        self.assertIn("PROCEEDS", out)              # header present
        self.assertIn("AAA.TO", out)
        self.assertIn("150.00", out)                # money 2dp
        self.assertIn("50.00", out)
        self.assertIn("TOTAL GAIN: 50.00 CAD", out)
        # the dividend row's amount (5.0) must not appear as a gain row
        self.assertNotIn("DIVIDEND", out)

    def test_gains_missing_file_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            r = _runsub(root, "gains", "30d")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no native gains files", r.stderr)

    def test_gains_all_accounts_acct_column_and_multicur_total(self):
        recent = (date.today() - timedelta(days=5)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "margin_raw_gains.json").write_text(json.dumps({
                "transactions": [{"date": recent, "symbol": "AAA.US", "qty": 5,
                                  "currency": "USD", "proceeds": 100.0,
                                  "cost": 60.0, "gain": 40.0, "days_held": 10}]}))
            (root / "work" / "rrsp_raw_gains.json").write_text(json.dumps({
                "transactions": [{"date": recent, "symbol": "BBB.TO", "qty": 5,
                                  "currency": "CAD", "proceeds": 50.0,
                                  "cost": 70.0, "gain": -20.0, "days_held": 3}]}))
            out = _runsub(root, "gains", "30d").stdout
        self.assertIn("ACCT", out)
        self.assertRegex(out, r"margin\s+" + recent)
        self.assertRegex(out, r"rrsp\s+" + recent)
        self.assertIn("TOTAL GAIN: -20.00 CAD, 40.00 USD", out)

    def test_explicit_missing_account_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin_raw.json": []})
            r = _runsub(root, "events", "30d", "nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no native transaction file", r.stderr)


class TestFindMissingHistory(unittest.TestCase):
    def test_reports_truncated_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            # sell with no prior buy → running qty goes negative → truncated
            (root / "work" / "margin_base.json").write_text(json.dumps({
                "transactions": [{"action": "BUYSELL", "date": "2026-03-01",
                                  "time": "09:30:00", "symbol": "XYZ.US",
                                  "quantity": -100, "price": 50.0,
                                  "net_amount": 5000.0, "currency": "CAD",
                                  "account": "margin"}]}))
            r = _runsub(root, "find-missing-history", "margin")
        self.assertIn("XYZ.US", r.stdout)
        self.assertIn("AFFECTS", r.stdout)          # year defaulted from config

    def test_missing_account_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            r = _runsub(root, "find-missing-history", "nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope_base.json", r.stderr)

    def test_gen_phantoms_emits_reloadable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_base.json").write_text(json.dumps({
                "transactions": [{"action": "BUYSELL", "date": "2026-03-01",
                                  "time": "09:30:00", "symbol": "XYZ.US",
                                  "quantity": -100, "price": 50.0,
                                  "net_amount": 5000.0, "currency": "CAD",
                                  "account": "margin"}]}))
            out = root / "phantoms.json"
            r = _runsub(root, "find-missing-history", "margin",
                        "--gen-phantoms", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(out.exists())
            entries = json.loads(out.read_text())
        # Emits the (symbol, account) pair the --incomplete-history loader reads.
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["symbol"], "XYZ.US")
        self.assertEqual(entries[0]["account"], "margin")

    def test_gen_phantoms_output_is_consumed_by_gains(self):
        # The whole point of the file: what --gen-phantoms emits must be
        # loadable by `taxjson-gains --incomplete-history` (the same path
        # `taxjson run` uses), so the tainted disposition is pulled out of the
        # gains total and surfaced under manual_reporting_required.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            base = root / "work" / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2025-03-01",
                 "date_settle": "2025-03-01", "time": "09:30:00",
                 "symbol": "XYZ.TO", "quantity": -100, "price": 50.0,
                 "net_amount": 4995.0, "currency": "CAD", "account": "margin"}]}))
            ph = root / "phantoms.json"
            r = _runsub(root, "find-missing-history", "margin",
                        "--gen-phantoms", str(ph))
            self.assertEqual(r.returncode, 0, r.stderr)
            g = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--year", "2025", "--taxable",
                 "--incomplete-history", str(ph), str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(g.returncode, 0, g.stderr)
            out = json.loads(g.stdout)
        mrr = out.get("manual_reporting_required") or []
        self.assertTrue(any(row.get("symbol") == "XYZ.TO" for row in mrr),
                        "phantom disposition should be flagged for manual review")

    def test_gen_phantoms_merges_across_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n'
                '[accounts.lira]\ntype = "sheltered"\n')
            for acct, sym in (("margin", "XYZ.US"), ("lira", "ABC.TO")):
                (root / "work" / f"{acct}_base.json").write_text(json.dumps({
                    "transactions": [{"action": "BUYSELL", "date": "2026-03-01",
                                      "time": "09:30:00", "symbol": sym,
                                      "quantity": -100, "price": 50.0,
                                      "net_amount": 5000.0, "currency": "CAD",
                                      "account": acct}]}))
            out = root / "phantoms.json"
            r = _runsub(root, "find-missing-history",
                        "--gen-phantoms", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            entries = json.loads(out.read_text())
        pairs = {(e["symbol"], e["account"]) for e in entries}
        self.assertEqual(pairs, {("XYZ.US", "margin"), ("ABC.TO", "lira")})


class TestSummaryCmd(unittest.TestCase):
    def test_per_account_table_totals_and_raw_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "margin_gains.json").write_text(json.dumps({
                "summary": {"year": "2026"}, "transactions": [
                    {"symbol": "AAA.TO", "gain": 100.0, "cost": 900.0,
                     "proceeds": 1000.0, "currency": "CAD", "days_held": 30},
                    {"symbol": "BBB.TO", "gain": 50.0, "cost": 50.0,
                     "proceeds": 100.0, "currency": "CAD", "days_held": 10},
                    {"action": "DIVIDEND", "symbol": "AAA.TO", "dividend": 10.0,
                     "currency": "CAD"}]}))
            (root / "work" / "rrsp_gains.json").write_text(json.dumps({
                "summary": {"year": "2026"}, "transactions": [
                    {"symbol": "AAA260101C00010000.US", "gain": -30.0,
                     "cost": 100.0, "proceeds": 70.0, "currency": "CAD",
                     "days_held": 5}]}))
            # raw derivative must be excluded (would double-count)
            (root / "work" / "margin_raw_gains.json").write_text(json.dumps({
                "transactions": [{"symbol": "ZZZ.TO", "gain": 999999.0,
                                  "currency": "CAD"}]}))
            r = _runsub(root, "sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("ACCOUNT", out)
        self.assertRegex(out, r"margin\s+150\.00\s+0\.00\s+150\.00\s+10\.00")
        self.assertRegex(out, r"rrsp\s+0\.00\s+-30\.00\s+-30\.00")
        self.assertRegex(out, r"TOTAL\s+150\.00\s+-30\.00\s+120\.00")
        # TOTAL column = REALIZED + DIVIDEND (margin: 150 + 10 = 160).
        self.assertRegex(out, r"margin\s+150\.00\s+0\.00\s+150\.00\s+10\.00\s+0\.00\s+0\.00\s+160\.00")
        self.assertNotIn("999,999", out)          # raw_gains excluded

    def test_summary_no_files_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            r = _runsub(root, "sum")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gains files", r.stderr)


class TestPositionsCmd(unittest.TestCase):
    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n'
            '[accounts.lira]\ntype = "sheltered"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps({
            "summary": {"year": "2025"}, "inventory": [
                {"symbol": "AAPL.US", "qty": 60.0, "total_cost": 600.0,
                 "position_start_date": "2025-01-15"},
                {"symbol": "BNS.TO", "qty": 50.0, "total_cost": 3000.0,
                 "position_start_date": "2025-02-01"},
                {"symbol": "ZERO.TO", "qty": 0.0, "total_cost": 0.0}]}))
        (root / "work" / "lira_gains.json").write_text(json.dumps({
            "summary": {"year": "2025"}, "inventory": [
                {"symbol": "XEQT.TO", "qty": 100.0, "total_cost": 4200.0,
                 "position_start_date": "2024-11-03"}]}))
        # raw derivative must be excluded (native, per-listing)
        (root / "work" / "margin_raw_gains.json").write_text(json.dumps({
            "inventory": [{"symbol": "SHOULD_NOT_SHOW", "qty": 1.0,
                           "total_cost": 1.0}]}))
        return root

    def test_lists_positions_with_cost_and_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("OPEN POSITIONS", out)
        # cost/share = 600/60 = 10.00; grouped under the account
        self.assertRegex(out, r"margin\s+AAPL\.US\s+60\s+600\.00\s+10\.00")
        self.assertRegex(out, r"lira\s+XEQT\.TO\s+100\s+4,200\.00\s+42\.00")
        self.assertIn("2024-11-03", out)                # position_start_date
        # 60/600 + 50/3000 + 100/4200 = 7800 book cost, 3 open positions
        self.assertRegex(out, r"3 position\(s\), total book cost 7,800\.00 CAD")
        self.assertNotIn("ZERO.TO", out)                # fully-closed excluded
        self.assertNotIn("SHOULD_NOT_SHOW", out)        # raw derivative excluded

    def test_single_account_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "list", "lira")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("XEQT.TO", r.stdout)
        self.assertNotIn("AAPL.US", r.stdout)

    def test_missing_account_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "list", "nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gains for account 'nope'", r.stderr)

    def test_no_files_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            r = _runsub(root, "list")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gains files", r.stderr)


class TestWashRadarCmd(unittest.TestCase):
    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')
        # margin: a recent buy → LOCKED row
        (root / "work" / "margin_base.json").write_text(json.dumps({
            "transactions": [{"action": "BUYSELL", "date": "2026-06-28",
                              "time": "09:30:00", "symbol": "LCK.TO",
                              "quantity": 100, "price": 10.0,
                              "net_amount": 1000.0, "currency": "CAD",
                              "account": "margin"}]}))
        (root / "work" / "sheltered_base.json").write_text(
            json.dumps({"transactions": []}))
        # a sheltered per-account base that must NOT be treated as taxable
        (root / "work" / "rrsp_base.json").write_text(
            json.dumps({"transactions": []}))
        return root

    def test_all_taxable_only_excludes_sheltered(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "wash-radar",
                        "--date", "2026-07-02")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("LCK.TO", r.stdout)
        # One combined invocation → the Definitions legend prints exactly once
        # (the old per-account loop duplicated it).
        self.assertEqual(r.stdout.count("Definitions:"), 1)

    def test_single_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "wash-radar", "margin",
                        "--date", "2026-07-02")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("LOCKED", r.stdout)

    def test_missing_account_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "wash-radar", "nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope_base.json", r.stderr)


class TestWashSalesCmd(unittest.TestCase):
    def _gains(self, wash=True, denied=100.0, perm=0.0):
        # One disallowed loss + one ordinary disposition (must not appear).
        return {"summary": {"year": "2025"}, "transactions": [
            {"symbol": "ZZA.US", "date": "2025-10-08", "qty": 100,
             "proceeds": 900.0, "cost": 1000.0, "raw_gain": -100.0,
             "gain": -100.0 + denied, "disallowed_amount": denied,
             "permanently_disallowed": perm,
             "is_wash_sale": wash, "currency": "CAD"},
            {"symbol": "AAA.TO", "date": "2025-05-01", "qty": 10,
             "proceeds": 500.0, "cost": 400.0, "raw_gain": 100.0,
             "gain": 100.0, "is_wash_sale": False, "currency": "CAD"}]}

    def _project(self, tmp, **kw):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(
            json.dumps(self._gains(**kw)))
        return root

    def test_lists_denied_loss_and_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "wash-sales")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("ZZA.US", out)
        self.assertNotIn("AAA.TO", out)               # ordinary gain excluded
        # GAIN -100, DENIED 100, ALLOWED 0
        self.assertRegex(out, r"ZZA\.US\s+100\s+900\.00\s+1,000\.00\s+-100\.00\s+100\.00\s+0\.00")
        self.assertRegex(out, r"1 wash sale\(s\); 100\.00 CAD")

    def test_prefers_gains_wash_file(self):
        # When the cross-account wash pass ran, its _gains_wash.json is used.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "work" / "margin_gains_wash.json").write_text(json.dumps(
                {"summary": {"year": "2025"}, "transactions": [
                    {"symbol": "ZZZ.US", "date": "2025-11-01", "qty": 5,
                     "proceeds": 50.0, "cost": 80.0, "raw_gain": -30.0,
                     "disallowed_amount": 30.0, "permanently_disallowed": 30.0,
                     "is_wash_sale": True, "currency": "CAD"}]}))
            r = _runsub(root, "wash-sales")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ZZZ.US", r.stdout)             # from the _wash file
        self.assertNotIn("ZZA.US", r.stdout)         # main gains not used
        self.assertIn("30.00 permanently denied", r.stdout)

    def test_no_wash_sales_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp, wash=False), "wash-sales")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No wash sales", r.stdout)

    def test_missing_account_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "wash-sales", "nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gains for account", r.stderr)


class TestWashSalesExplain(unittest.TestCase):
    def _project(self, tmp):
        # buy, sell at a loss, rebuy within 30 days → a superficial loss the
        # --explain trace can reconstruct from the base file.
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\ntax_date = "settle"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_base.json").write_text(json.dumps({
            "transactions": [
                {"action": "BUYSELL", "date": "2026-01-05", "time": "09:30:00",
                 "symbol": "XYZ.TO", "quantity": 100, "price": 20.0,
                 "net_amount": 2000.0, "currency": "CAD", "account": "margin"},
                {"action": "BUYSELL", "date": "2026-02-01", "time": "09:30:00",
                 "symbol": "XYZ.TO", "quantity": -100, "price": 10.0,
                 "net_amount": 1000.0, "currency": "CAD", "account": "margin"},
                {"action": "BUYSELL", "date": "2026-02-10", "time": "09:30:00",
                 "symbol": "XYZ.TO", "quantity": 100, "price": 11.0,
                 "net_amount": 1100.0, "currency": "CAD", "account": "margin"}]}))
        return root

    def test_explain_shows_calculation_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "wash-sales", "margin", "--explain")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("XYZ.TO", r.stdout)
        self.assertIn("CALCULATION TRACE", r.stdout)
        self.assertIn("WASH SALE", r.stdout)
        # Color is off by default → no ANSI escapes leak into the output.
        self.assertNotIn("\x1b[", r.stdout)

    def test_explain_missing_account_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "wash-sales", "nope", "--explain")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope_base.json", r.stderr)


class TestHelpCmd(unittest.TestCase):
    def test_top_level_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(Path(tmp), "help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wash-sales", r.stdout)
        self.assertIn("events", r.stdout)

    def test_help_for_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(Path(tmp), "help", "divs")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("taxjson divs", r.stdout)      # its own usage line
        self.assertIn("period", r.stdout)

    def test_renamed_long_names_are_gone(self):
        # Clean rename: the old long names no longer resolve.
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(Path(tmp), "transactions", "5d")
        self.assertNotEqual(r.returncode, 0)

    def test_help_unknown_command_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(Path(tmp), "help", "bogus")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no such command", r.stderr)


class TestFeesCmd(unittest.TestCase):
    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "to_base.csv").write_text(
            "2026-03-01 12:00:00 USD CAD 1.35000\n")
        (root / "work" / "margin_ib.json").write_text(json.dumps({
            "metadata": {"source_brokerage": "ib"},
            "transactions": [{"action": "BUYSELL", "date": "2026-03-01",
                              "symbol": "AAA.US", "currency": "USD",
                              "quantity": 10, "commission": 0.0, "fee": 5.0,
                              "gross_amount": 1000.0}]}))
        return root

    def test_reports_fees_by_brokerage(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "fees-sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        # by-account is the default, so the key is "ib/<account>".
        self.assertTrue(any(k.split("/")[0] == "ib"
                            for k in doc.get("brokerages", {})), doc)

    def test_no_work_dir_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(Path(tmp), "fees-sum")     # no work/ dir
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("run `taxjson run`", r.stderr)

    def test_period_window_uses_since(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "fees-sum", "all", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        # `all` → no year/since scope, so every fee counts (the 2026 row).
        self.assertTrue(any(k.split("/")[0] == "ib"
                            for k in doc.get("brokerages", {})), doc)


class TestPeriodSums(unittest.TestCase):
    """`fees` / `divs-sum` / `trades-sum` — period optional, default tax year."""
    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_raw.json").write_text(json.dumps({
            "transactions": [
                {"action": "BUYSELL", "date": "2026-03-01", "time": "09:30:00",
                 "symbol": "AAA.TO", "quantity": 100, "price": 10.0,
                 "net_amount": 1000.0, "fee": 5.0, "currency": "CAD"},
                {"action": "BUYSELL", "date": "2026-04-01", "time": "09:30:00",
                 "symbol": "AAA.TO", "quantity": -40, "price": 12.0,
                 "net_amount": 480.0, "commission": 3.0, "currency": "CAD"},
                {"action": "DIVIDEND", "date": "2026-05-01", "time": "09:30:00",
                 "symbol": "AAA.TO", "quantity": 60, "price": 0.5,
                 "gross_amount": 30.0, "net_amount": 30.0, "currency": "CAD"},
                {"action": "BUYSELL", "date": "2025-06-01", "time": "09:30:00",
                 "symbol": "OLD.TO", "quantity": 10, "price": 1.0,
                 "net_amount": 10.0, "fee": 9.0, "currency": "CAD"}]}))  # prior yr
        return root

    def test_fees_default_tax_year_excludes_other_years(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "fees")     # default: tax year 2026
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("tax year 2026", r.stdout)
        self.assertNotIn("OLD.TO", r.stdout)            # 2025 fee excluded
        # fee (5.00) + commission (3.00) summed across the two 2026 trades
        self.assertIn("TOTAL FEES: 8.00 CAD", r.stdout)

    def test_divs_sum_per_ticker_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "divs-sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"AAA\.TO\s+CAD\s+30\.00")
        self.assertIn("TOTAL DIVIDEND: 30.00 CAD", r.stdout)

    def test_dil_view_and_sum(self):
        """`dil` / `dil-sum`: DIVIDEND_IN_LIEU only — plain dividends and
        trades excluded; ordinary-income caveat in the banner."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            work = root / "work"
            data = json.loads((work / "margin_raw.json").read_text())
            data["transactions"].append(
                {"action": "DIVIDEND_IN_LIEU", "date": "2026-05-15",
                 "time": "09:30:00", "symbol": "AAA.TO", "quantity": 60,
                 "price": 0.25, "gross_amount": 15.0, "net_amount": 15.0,
                 "currency": "CAD"})
            (work / "margin_raw.json").write_text(json.dumps(data))
            view = _runsub(root, "dil")
            summ = _runsub(root, "dil-sum")
            divs = _runsub(root, "divs-sum")
        self.assertEqual(view.returncode, 0, view.stderr)
        self.assertIn("2026-05-15", view.stdout)
        self.assertNotIn("2026-05-01", view.stdout)     # plain DIVIDEND out
        self.assertEqual(summ.returncode, 0, summ.stderr)
        self.assertRegex(summ.stdout, r"AAA\.TO\s+CAD\s+15\.00\s+1")
        self.assertIn("TOTAL DIVIDEND IN LIEU: 15.00 CAD", summ.stdout)
        self.assertIn("ordinary income", summ.stdout)
        self.assertNotIn("30.00", summ.stdout)          # dividend excluded
        # divs-sum still counts BOTH (dividend + in-lieu), unchanged.
        self.assertIn("TOTAL DIVIDEND: 45.00 CAD", divs.stdout)

    def test_dil_sum_empty_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "dil-sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No dividends in lieu in tax year 2026.", r.stdout)

    def test_trades_sum_counts_and_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "trades-sum")
        self.assertEqual(r.returncode, 0, r.stderr)
        # AAA.TO: 1 buy (1000), 1 sell (480), fees 5+3=8
        self.assertRegex(r.stdout,
                         r"AAA\.TO\s+CAD\s+1\s+1\s+1,000\.00\s+480\.00\s+8\.00")

    def test_period_positional_window(self):
        # A far-future window excludes everything → the empty-scope message.
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "divs-sum", "1d")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No dividends in last 1d", r.stdout)

    def test_lone_positional_is_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "trades-sum", "margin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("tax year 2026", r.stdout)        # not read as a period

    def test_typo_period_reports_invalid_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _runsub(self._project(tmp), "divs-sum", "5x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid time period", r.stderr)

    def test_mtd_and_ytd_period_tokens(self):
        # mtd/ytd are calendar month/year-to-date windows, valid everywhere a
        # period is accepted (including as the lone positional in roll-ups).
        from datetime import date
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            today = date.today()
            in_month = today.replace(day=1).isoformat()
            prior_year = f"{today.year - 1}-06-15"
            (root / "work" / "margin_raw.json").write_text(json.dumps({
                "transactions": [
                    {"action": "DIVIDEND", "date": in_month,
                     "time": "09:30:00", "symbol": "NEW.TO", "quantity": 10,
                     "price": 1.0, "gross_amount": 10.0, "net_amount": 10.0,
                     "currency": "CAD"},
                    {"action": "DIVIDEND", "date": prior_year,
                     "time": "09:30:00", "symbol": "OLD.TO", "quantity": 10,
                     "price": 1.0, "gross_amount": 99.0, "net_amount": 99.0,
                     "currency": "CAD"}]}))
            mtd = _runsub(root, "divs-sum", "mtd")
            ytd = _runsub(root, "divs", "ytd", "margin")
        self.assertEqual(mtd.returncode, 0, mtd.stderr)
        self.assertIn("MTD (since", mtd.stdout)
        self.assertIn("NEW.TO", mtd.stdout)
        self.assertNotIn("OLD.TO", mtd.stdout)          # prior year excluded
        self.assertEqual(ytd.returncode, 0, ytd.stderr)
        self.assertIn("NEW.TO", ytd.stdout)
        self.assertNotIn("OLD.TO", ytd.stdout)

    def test_tax_year_token_binds_to_config_year(self):
        # `tax_year` is a valid period everywhere and scopes to the config year.
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._project(tmp)                    # config year 2026
            ev = _runsub(proj, "events", "tax_year")     # required-period view
            fees = _runsub(proj, "fees", "tax_year")     # optional-period view
        self.assertEqual(ev.returncode, 0, ev.stderr)
        self.assertIn("AAA.TO", ev.stdout)               # 2026 included
        self.assertNotIn("OLD.TO", ev.stdout)            # 2025 excluded
        self.assertEqual(fees.returncode, 0, fees.stderr)
        self.assertIn("tax year 2026", fees.stdout)
        self.assertIn("TOTAL FEES: 8.00 CAD", fees.stdout)


if __name__ == "__main__":
    unittest.main()
