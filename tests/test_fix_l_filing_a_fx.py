"""Low-round fixes and pins, `taxjson fx-cash` (bin/taxjson_fx_cash.py):
S033-09 (a net-debit sale pays currency), S033-05 (same-day clock
order), S033-06 (only the tax year's disposals count)."""
import unittest

from taxjson.bin.taxjson_fx_cash import _flows, build_ledger


def _tx(action, date, net, qty=0.0, time="10:00:00", **kw):
    return dict(action=action, date=date, date_settle=date, time=time,
                symbol=kw.pop("symbol", "AAA.US"), quantity=qty,
                currency="USD", net_amount=net, account="margin", **kw)


class TestNetDebitSale(unittest.TestCase):
    """S033-09"""

    def test_flow_keeps_the_sign(self):
        self.assertEqual(_flows(_tx("BUYSELL", "2025-01-02", -0.25,
                                    qty=-1)), -0.25)
        self.assertEqual(_flows(_tx("BUYSELL", "2025-01-02", 100.0,
                                    qty=-1)), 100.0)
        self.assertEqual(_flows(_tx("BUYSELL", "2025-01-02", 100.0,
                                    qty=1)), -100.0)

    def test_ledger(self):
        rates = {"2025-01-02": 1.30, "2025-02-03": 1.45}
        txs = [_tx("DIVIDEND", "2025-01-02", 100.0, gross_amount=100.0),
               _tx("BUYSELL", "2025-02-03", -0.25, qty=-1,
                   symbol="XYZ250221C00010000.US"),
               _tx("BUYSELL", "2025-02-03", 99.75, qty=10, time="11:00:00")]
        doc = build_ledger(txs, "CAD", {}, 2025,
                           rate_of=lambda c, d: rates[d])
        usd = doc["per_currency"]["USD"]
        self.assertEqual((usd["acquired"], usd["disposed"], usd["gain"]),
                         (100.0, 100.0, 15.0))
        self.assertEqual(doc["pools"], {})


class TestSameDayClockOrder(unittest.TestCase):
    """S033-05: a 10:00 sale raises the USD a 14:00 buy spends, even when
    the file lists the buy first."""

    def test_order_by_time(self):
        rates = {"2025-01-02": 1.30, "2025-03-03": 1.40}
        txs = [_tx("BUYSELL", "2025-01-02", 1000.0, qty=-10),
               _tx("BUYSELL", "2025-03-03", 1500.0, qty=15, time="14:00:00"),
               _tx("BUYSELL", "2025-03-03", 1000.0, qty=-10, time="10:00:00")]
        doc = build_ledger(txs, "CAD", {}, 2025,
                           rate_of=lambda c, d: rates[d])
        self.assertEqual(doc["per_currency"]["USD"]["gain"], 75.0)
        self.assertEqual(doc["overdrafts"], {})


class TestTaxYearOnly(unittest.TestCase):
    """S033-06: disposals of other years walk the pool but are not the
    year's gain."""

    def test_multi_year(self):
        rates = {"2024-03-01": 1.30, "2024-09-03": 1.35,
                 "2025-04-01": 1.40}
        txs = [_tx("BUYSELL", "2024-03-01", 1000.0, qty=-10),
               _tx("BUYSELL", "2024-09-03", 400.0, qty=4),
               _tx("BUYSELL", "2025-04-01", 400.0, qty=4)]
        doc = build_ledger(txs, "CAD", {}, 2025,
                           rate_of=lambda c, d: rates[d])
        usd = doc["per_currency"]["USD"]
        self.assertEqual((usd["disposed"], usd["gain"]), (400.0, 40.0))
        self.assertEqual(doc["net_gain"], 40.0)
        self.assertEqual([e["date"] for e in doc["events"]], ["2025-04-01"])


if __name__ == "__main__":
    unittest.main()
