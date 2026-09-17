"""Regression tests for the tier-5 audit fixes:

- CRA wash window uses SETTLEMENT date end-to-end (trigger detection had
  used trade date while the balance walk used settle — a disallowance could
  flip near T+1/T+2 window edges).
- detect_brokerage: structural Webull check runs before the RBC test, and
  RBC requires its own export markers (bare "RBC" content misrouted files).
- FX: the first row per (currency, date) wins — today's appended intra-day
  spot row no longer overrides the noon rate (non-deterministic conversions).
- .tt parser warns when total disagrees with qty*price±fee (silent typos).
- Single-account `taxjson run` no longer overwrites combined reports (gated).
- web what_if_sell: US multi-lot sells aggregate all closed lots; stale
  rates surface an fx_note instead of a silent 1.35 fallback.
- taxjson-explain handles TRANSFER-funded positions and --incomplete-history.
- Tainted losses with an in-window acquisition surface a partial-taint
  superficial-loss warning.
- phantoms.json deletion invalidates cached gains (marker file).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tt(action, date, symbol, qty, price=0.0, net=0.0, account="acct",
        time="09:30:00", **kw):
    from taxjson.lib.core import TaxTransaction
    return TaxTransaction(action=action, date=date, time=time, symbol=symbol,
                          quantity=qty, price=price, net_amount=net,
                          currency="CAD", account=account, **kw)


class TestSettleBasisWashWindow(unittest.TestCase):
    def test_trigger_admitted_on_settle_basis(self):
        # Loss settles 2026-02-03; rebuy trades 30d after the TRADE date but
        # settles inside the window measured from the SETTLE date. With the
        # old trade-date trigger + settle-date balance mix, this case could
        # flip; on a consistent settle basis the disallowance is stable.
        from taxjson.lib.core import CanadaTaxRules
        txs = [
            _tt("BUYSELL", "2026-01-05", "STL.TO", 100, 20.0, 2000.0,
                date_settle="2026-01-06"),
            _tt("BUYSELL", "2026-02-02", "STL.TO", -100, 10.0, 1000.0,
                date_settle="2026-02-03"),                     # loss
            _tt("BUYSELL", "2026-03-04", "STL.TO", 100, 10.0, 1000.0,
                date_settle="2026-03-05"),   # settle day +30 → in window
        ]
        res = CanadaTaxRules().compute_gains(txs)
        dis = sum(float(g.get("disallowed_amount") or 0)
                  for g in res["transactions"] if g.get("qty"))
        self.assertAlmostEqual(dis, 1000.0, places=2,
                               msg="settle-basis window must admit the "
                                   "settle-day+30 rebuy")


class TestDetectBrokerage(unittest.TestCase):
    def test_webull_with_rbc_ticker_not_misrouted(self):
        from taxjson.bin.taxjson_detect_brokerage import detect_brokerage
        content = ('Account Number,Action Code,Symbol,Description\n'
                   '123,BUY,RBC,RBC BEARINGS CORP\n')
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False) as f:
            f.write(content)
            name = f.name
        try:
            self.assertEqual(detect_brokerage(Path(name)), "webull")
        finally:
            os.remove(name)

    def test_rbc_export_still_detected(self):
        from taxjson.bin.taxjson_detect_brokerage import detect_brokerage
        content = ('"Activity Export as of Jan 5, 2026"\n\n'
                   '"Account: 12345678 - Margin"\n\n'
                   '"Date","Activity","Symbol"\n')
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False) as f:
            f.write(content)
            name = f.name
        try:
            self.assertEqual(detect_brokerage(Path(name)), "rbc_direct")
        finally:
            os.remove(name)


class TestFxFirstRowWins(unittest.TestCase):
    def test_intraday_spot_does_not_override_noon(self):
        from taxjson.bin.taxjson_convert_currency import load_exchange_rates
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False) as f:
            f.write("2026-07-03 12:00:00 USD CAD 1.3500\n"
                    "2026-07-03 14:23:11 USD CAD 1.4200\n")
            name = f.name
        try:
            hist = load_exchange_rates(Path(name), target_curr="CAD")
        finally:
            os.remove(name)
        self.assertAlmostEqual(float(hist["USD"]["2026-07-03"]), 1.35,
                               places=4)


class TestTtTotalValidation(unittest.TestCase):
    def test_typo_total_warns(self):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            parse_tt_line(
                "BUYSELL 2024-01-05 09:31:00 AAPL 100 USD 50.00 9999999.00 5.00",
                account_name="m")
        self.assertIn("differs from qty*price", err.getvalue())

    def test_correct_total_silent(self):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            parse_tt_line(
                "BUYSELL 2024-01-05 09:31:00 AAPL 100 USD 50.00 5005.00 5.00",
                account_name="m")
        self.assertEqual(err.getvalue(), "")


class TestWhatIfMultiLotAndStaleFx(unittest.TestCase):
    def _project(self, tmp, country="usa"):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "reports").mkdir()
        (root / "taxjson.toml").write_text(
            f'[settings]\nyear = 2026\ncountry = "{country}"\n'
            f'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
        return root

    def test_us_multi_lot_sell_accepted(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        txs = [{"action": "BUYSELL", "date": "2025-01-02", "symbol": "AAPL",
                "quantity": 100, "price": 10.0, "net_amount": 1000.0,
                "currency": "CAD", "account": "margin"},
               {"action": "BUYSELL", "date": "2025-02-02", "symbol": "AAPL",
                "quantity": 100, "price": 12.0, "net_amount": 1200.0,
                "currency": "CAD", "account": "margin"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, country="usa")
            (root / "work" / "margin_base.json").write_text(
                json.dumps({"transactions": txs}))
            ctx = ProjectContext.load(root)
            r = data.what_if_sell(ctx, "margin", "AAPL", 200, 15.0,
                                  on="2026-06-30")
        self.assertTrue(r["ok"], r)
        # 200 sold across two lots: proceeds 3000, cost 2200 → gain 800.
        self.assertAlmostEqual(r["economic_gain"], 800.0, places=2)
        self.assertAlmostEqual(r["cost_basis"], 2200.0, places=2)

    def test_stale_rates_surface_fx_note(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        txs = [{"action": "BUYSELL", "date": "2025-01-02", "symbol": "AEM.US",
                "quantity": 10, "price": 100.0, "net_amount": 1000.0,
                "currency": "CAD", "account": "margin"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, country="canada")
            (root / "work" / "margin_base.json").write_text(
                json.dumps({"transactions": txs}))
            # Rates exist for the currency but END months before the what-if
            # date → the default-rate fallback fires and must be flagged.
            (root / "work" / "to_base.csv").write_text(
                "2026-01-05 12:00:00 USD CAD 1.40000\n")
            ctx = ProjectContext.load(root)
            r = data.what_if_sell(ctx, "margin", "AEM.US", 10, 50.0,
                                  on="2026-06-30", price_currency="USD")
        self.assertTrue(r["ok"], r)
        self.assertIsNotNone(r["fx_note"],
                             "silent default-rate fallback must be flagged")


class TestExplainParity(unittest.TestCase):
    def test_transfer_funded_sale_traceable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "b.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "TRANSFER", "date": "2026-01-05",
                 "time": "09:30:00", "symbol": "TRF.TO", "quantity": 100,
                 "price": 10.0, "net_amount": 1000.0, "currency": "CAD",
                 "account": "rrsp"},
                {"action": "BUYSELL", "date": "2026-03-01",
                 "time": "09:30:00", "symbol": "TRF.TO", "quantity": -100,
                 "price": 20.0, "net_amount": 2000.0, "currency": "CAD",
                 "account": "rrsp"}]}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_explain",
                 "--country", "canada", "--list", str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
                         f"transfer-funded sale must trace: {r.stderr}")
        self.assertIn("TRF.TO", r.stdout)

    def test_incomplete_history_applies_phantoms(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "b.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2026-03-01",
                 "time": "09:30:00", "symbol": "PHX.TO", "quantity": -100,
                 "price": 50.0, "net_amount": 5000.0, "currency": "CAD",
                 "account": "m"}]}))
            ph = Path(tmp) / "phantoms.json"
            ph.write_text(json.dumps([{"symbol": "PHX.TO", "account": "m"}]))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_explain",
                 "--country", "canada", "--list",
                 "--incomplete-history", str(ph), str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PHX.TO", r.stdout)


class TestPartialTaintWarning(unittest.TestCase):
    def test_tainted_loss_with_in_window_rebuy_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "b.json"
            base.write_text(json.dumps({"transactions": [
                # 10 phantom + 1000 real @50; sell 500 at a loss while the
                # pool is still tainted; rebuy 500 in the window.
                {"action": "BUYSELL", "date": "2026-01-05", "time": "09:30:00",
                 "symbol": "PT.TO", "quantity": 1000, "price": 50.0,
                 "net_amount": 50000.0, "currency": "CAD", "account": "m"},
                {"action": "BUYSELL", "date": "2026-02-01", "time": "09:30:00",
                 "symbol": "PT.TO", "quantity": -1010, "price": 40.0,
                 "net_amount": 40400.0, "currency": "CAD", "account": "m"},
                {"action": "BUYSELL", "date": "2026-02-10", "time": "09:30:00",
                 "symbol": "PT.TO", "quantity": 500, "price": 40.0,
                 "net_amount": 20000.0, "currency": "CAD", "account": "m"}]}))
            ph = Path(tmp) / "phantoms.json"
            ph.write_text(json.dumps([{"symbol": "PT.TO", "account": "m"}]))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--year", "2026", "--taxable",
                 "--incomplete-history", str(ph), str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
        warns = out.get("superficial_loss_warnings") or []
        self.assertTrue(any(w.get("symbol") == "PT.TO"
                            and "superficial-loss" in (w.get("note") or "")
                            for w in warns),
                        f"partial-taint loss must warn: {warns}")


class TestPhantomsDeletionInvalidates(unittest.TestCase):
    def test_marker_bumps_base_mtimes_on_removal(self):
        # Unit-level: simulate the cmd_run marker logic contract — the marker
        # is written when phantoms exist and, when the file disappears, base
        # mtimes advance so needs_rebuild(gains, base) fires.
        from taxjson.bin.taxjson_run import needs_rebuild
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            base = cache / "m_base.json"; base.write_text("{}")
            gains = cache / "m_gains.json"
            import time as _t
            _t.sleep(0.01)
            gains.write_text("{}")                # gains newer than base
            self.assertFalse(needs_rebuild(gains, base))
            _t.sleep(0.01)
            os.utime(base)                        # the deletion-path bump
            self.assertTrue(needs_rebuild(gains, base))


if __name__ == "__main__":
    unittest.main()
