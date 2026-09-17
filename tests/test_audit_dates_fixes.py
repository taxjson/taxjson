"""Regression tests for the date-semantics audit fixes:

D1  Canada engine: execution-order phase at a shared sort-date — a trade
    executed pre-split but SETTLING on split day is processed before the
    split; same-day executions stay post-split.
D2  Webull computes real settlement (options T+1; equities T+2 pre-cutover,
    T+1 after) instead of fabricating settle = trade date.
D3  Country-aware tax_date: usa defaults to TRADE-date year attribution
    (IRS); canada keeps settle (CRA).
D4  US wash_sales records carry date_settle, so the --year filter puts the
    records and their gain rows in the same year.
D5  Radar / safe_to_sell / superficial-loss warnings measure windows on the
    settle basis, matching the engine.
D6  US FIFO consumes lots in acquisition (trade) order even when settlement
    lags differ.
D10 IB uses the Canadian T+1 cutover (2024-05-27) for CAD trades.
D12 Income rows are year-filtered on the pay date even under settle basis.
"""
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


class TestCanadaSplitSettlePhase(unittest.TestCase):
    def test_presplit_trade_settling_on_split_day_processed_first(self):
        # Buy 100 executed T-1, SETTLING on the split date (RBC-style 09:30
        # stamps). True result of buy→2:1 split→sell 200 at the split-
        # adjusted price is a flat round trip. Pre-fix the split was applied
        # to an empty pool first → phantom short + fabricated loss.
        from taxjson.lib.core import CanadaTaxRules
        txs = [
            _tt("BUYSELL", "2026-02-02", "PH.TO", 100, 10.0, 1000.0,
                date_settle="2026-02-03"),                    # settles split day
            _tt("SPLIT", "2026-02-03", "PH.TO", 2.0),         # settle==date
            _tt("BUYSELL", "2026-03-02", "PH.TO", -200, 5.0, 1000.0,
                date_settle="2026-03-03"),
        ]
        res = CanadaTaxRules().compute_gains(txs)
        gain = sum(float(g.get("gain") or 0) for g in res["transactions"]
                   if g.get("qty"))
        self.assertAlmostEqual(gain, 0.0, places=2)
        inv = [h for h in res["inventory"] if h["symbol"] == "PH.TO"]
        self.assertTrue(all(abs(h["qty"]) < 1e-6 for h in inv),
                        f"phantom position left: {inv}")

    def test_same_day_execution_stays_post_split(self):
        # A T+0 row (crypto/.tt style, date==settle==split date) executes on
        # the split's effective date → post-split; the split must scale the
        # pre-existing pool BEFORE that sale drains it.
        from taxjson.lib.core import CanadaTaxRules
        txs = [
            _tt("BUYSELL", "2026-01-05", "SD.TO", 100, 10.0, 1000.0),
            _tt("SPLIT", "2026-02-03", "SD.TO", 2.0),
            _tt("BUYSELL", "2026-02-03", "SD.TO", -200, 5.0, 1000.0),
        ]
        res = CanadaTaxRules().compute_gains(txs)
        inv = [h for h in res["inventory"] if h["symbol"] == "SD.TO"]
        self.assertTrue(all(abs(h["qty"]) < 1e-6 for h in inv), inv)


class TestWebullSettlement(unittest.TestCase):
    _HDR = "Currency,Date,Action Code,Symbol,Name,X,Quantity,Price,Y,Proceeds\n"

    def _parse(self, rows):
        from taxjson.lib.brokerages.webull import WebullBrokerage
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False) as f:
            f.write(self._HDR + rows)
            name = f.name
        try:
            return WebullBrokerage().parse_file(Path(name))
        finally:
            os.remove(name)

    # Webull's CSV Date column IS the settlement date (user-verified against
    # real Webull statements): it stays date_settle verbatim; the TRADE date
    # is back-computed (settle − T+N business days).

    def test_equity_settle_kept_trade_backcomputed_t1(self):
        txs = self._parse("USD,31-12-2024,BUY,AAPL,APPLE,,10,100.00,,1000.00\n")
        t = next(t for t in txs if t["action"] == "BUYSELL")
        self.assertEqual(t["date_settle"], "2024-12-31")   # CSV date verbatim
        # Post-cutover T+1 back: Tue Dec 31 settles a Mon Dec 30 trade.
        self.assertEqual(t["date"], "2024-12-30")
        self.assertNotEqual(t["date"], t["date_settle"],
                            "trade date must not be the settle date")

    def test_equity_settle_kept_trade_backcomputed_t2(self):
        txs = self._parse("USD,21-05-2024,BUY,AAPL,APPLE,,10,100.00,,1000.00\n")
        t = next(t for t in txs if t["action"] == "BUYSELL")
        self.assertEqual(t["date_settle"], "2024-05-21")   # CSV date verbatim
        # Pre-cutover T+2 back: Tue May 21 ← Mon May 20 ← Fri May 17.
        self.assertEqual(t["date"], "2024-05-17")


class TestCountryAwareTaxDate(unittest.TestCase):
    def _run_gains(self, country, extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "b.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2025-06-02",
                 "date_settle": "2025-06-03", "time": "09:30:00",
                 "symbol": "YR", "quantity": 100, "price": 10.0,
                 "net_amount": 1000.0, "currency": "CAD", "account": "m"},
                {"action": "BUYSELL", "date": "2025-12-30",
                 "date_settle": "2026-01-02", "time": "09:30:00",
                 "symbol": "YR", "quantity": -100, "price": 20.0,
                 "net_amount": 2000.0, "currency": "CAD", "account": "m"}]}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", country, "--year", "2025", *extra, str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            return json.loads(r.stdout)

    def test_usa_defaults_to_trade_year(self):
        out = self._run_gains("usa")
        gains = [t for t in out["transactions"] if t.get("qty")]
        self.assertTrue(gains, "IRS recognizes on the TRADE date → 2025")
        self.assertEqual(out["summary"].get("tax_date_basis"), "trade")

    def test_canada_defaults_to_settle_year(self):
        out = self._run_gains("canada")
        gains = [t for t in out["transactions"] if t.get("qty")]
        self.assertFalse(gains, "CRA settle basis → the sale lands in 2026")
        self.assertEqual(out["summary"].get("tax_date_basis"), "settle")


class TestUsWashRecordYearCoherence(unittest.TestCase):
    def test_records_and_rows_share_the_year(self):
        # Loss trade Dec-30/settle Jan-2 with a replacement: under an
        # explicit settle basis, the wash record must follow the gain rows
        # into 2026 (it used to fall back to trade date → split-brain).
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "b.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2025-06-02",
                 "date_settle": "2025-06-03", "time": "09:30:00",
                 "symbol": "WR", "quantity": 100, "price": 20.0,
                 "net_amount": 2000.0, "currency": "USD", "account": "m"},
                {"action": "BUYSELL", "date": "2025-12-30",
                 "date_settle": "2026-01-02", "time": "09:30:00",
                 "symbol": "WR", "quantity": -100, "price": 10.0,
                 "net_amount": 1000.0, "currency": "USD", "account": "m"},
                {"action": "BUYSELL", "date": "2025-12-20",
                 "date_settle": "2025-12-23", "time": "09:30:00",
                 "symbol": "WR", "quantity": 100, "price": 11.0,
                 "net_amount": 1100.0, "currency": "USD", "account": "m"}]}))
            def run(year):
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                     "--country", "usa", "--taxable", "--tax-date", "settle",
                     "--year", year, str(base)],
                    cwd=REPO_ROOT, capture_output=True, text=True)
                assert r.returncode == 0, r.stderr
                return json.loads(r.stdout)
            y25, y26 = run("2025"), run("2026")
        # 2026 holds BOTH the disposition rows and the disallowed total;
        # 2025 holds NEITHER.
        self.assertFalse([t for t in y25["transactions"] if t.get("qty")])
        self.assertAlmostEqual(y25["summary"].get("total_disallowed", 0), 0.0,
                               places=2)
        self.assertTrue([t for t in y26["transactions"] if t.get("qty")])
        self.assertGreater(y26["summary"].get("total_disallowed", 0), 1.0)


class TestUsFifoTradeOrder(unittest.TestCase):
    def test_lots_consumed_in_acquisition_order(self):
        from taxjson.lib.core import USATaxRules, TaxTransaction
        def T(**kw):
            base = dict(action="BUYSELL", time="09:30:00", symbol="FIFO",
                        quantity=0.0, price=0.0, net_amount=0.0,
                        currency="USD", account="m")
            base.update(kw)
            return TaxTransaction(**base)
        txs = [
            # Lot A: traded first, settles LATER (delayed settlement).
            T(date="2026-03-06", date_settle="2026-03-12", quantity=100,
              price=10.0, net_amount=1000.0),
            # Lot B: traded second, settles first.
            T(date="2026-03-07", date_settle="2026-03-10", quantity=100,
              price=20.0, net_amount=2000.0),
            T(date="2026-04-01", date_settle="2026-04-02", quantity=-100,
              price=15.0, net_amount=1500.0),
        ]
        res = USATaxRules().compute_gains(txs)
        gains = [g for g in res["transactions"] if g.get("qty")]
        total = sum(float(g.get("gain") or 0) for g in gains)
        # IRS FIFO by acquisition (trade) date consumes lot A: 1500-1000=+500.
        self.assertAlmostEqual(total, 500.0, places=2,
                               msg="settle-order FIFO consumed the wrong lot")


class TestRadarSettleBasis(unittest.TestCase):
    def test_settle_gap_inside_window_is_flagged(self):
        # Loss trade Fri 03-07 (settle Tue 03-11); re-buy trade Mon 04-07
        # (settle 04-09): 31 trade-days apart but 29 settle-days — inside
        # the CRA window. The engine disallows; the radar must agree.
        txs = [
            {"action": "BUYSELL", "date": "2024-01-05",
             "date_settle": "2024-01-08", "time": "09:30:00",
             "symbol": "SG.TO", "quantity": 100, "price": 20.0,
             "net_amount": 2000.0, "currency": "CAD", "account": "m"},
            {"action": "BUYSELL", "date": "2025-03-07",
             "date_settle": "2025-03-11", "time": "09:30:00",
             "symbol": "SG.TO", "quantity": -100, "price": 10.0,
             "net_amount": 1000.0, "currency": "CAD", "account": "m"},
            {"action": "BUYSELL", "date": "2025-04-07",
             "date_settle": "2025-04-09", "time": "09:30:00",
             "symbol": "SG.TO", "quantity": 100, "price": 10.0,
             "net_amount": 1000.0, "currency": "CAD", "account": "m"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.json"
            f.write_text(json.dumps({"transactions": txs}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
                 "--taxable", str(f), "--date", "2025-04-10"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next((l for l in r.stdout.splitlines()
                     if l.startswith("SG.TO")), "")
        self.assertIn("VIOLATION", line,
                      f"settle-basis window must flag the rebuy:\n{r.stdout}")


class TestIbCadCutover(unittest.TestCase):
    def test_cad_trade_on_0527_settles_t1(self):
        from taxjson.lib.brokerages.ib_extractor import get_ib_settlement
        # 2024-05-27 was a TSX trading day AFTER Canada's T+1 cutover but
        # BEFORE the US one.
        self.assertEqual(get_ib_settlement("2024-05-27", "Stocks", "CAD"),
                         "2024-05-28")
        self.assertEqual(get_ib_settlement("2024-05-27", "Stocks", "USD"),
                         "2024-05-29")            # still T+2 in the US


class TestIncomeYearOnPayDate(unittest.TestCase):
    def test_dividend_filtered_by_pay_date_under_settle(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "b.json"
            base.write_text(json.dumps({"transactions": [
                # Pathological row: pay date 2025-12-30 with a divergent
                # date_settle in 2026 (no parser emits this today — the fix
                # removes the latent hazard).
                {"action": "DIVIDEND", "date": "2025-12-30",
                 "date_settle": "2026-01-02", "time": "09:30:00",
                 "symbol": "DV.TO", "quantity": 100, "price": 0.5,
                 "gross_amount": 50.0, "net_amount": 50.0,
                 "currency": "CAD", "account": "m"}]}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--year", "2025",
                 "--tax-date", "settle", str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
        divs = [t for t in out["transactions"]
                if t.get("action") == "DIVIDEND"]
        self.assertTrue(divs, "income belongs to the year it was RECEIVED")


if __name__ == "__main__":
    unittest.main()
