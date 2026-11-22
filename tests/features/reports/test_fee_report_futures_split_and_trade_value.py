"""Fix round 7 (wave B), 2026-10-29: harvest, watch's harvest baseline,
tips, div-estimate and fees (backlog TJ-0908, TJ-0709, TJ-0710,
TJ-0911, TJ-0912, TJ-0913, TJ-0914, TJ-0900, TJ-0897, TJ-0776).

Synthetic data only; offline throughout (injected fetchers or a cache).

Formerly: test_fix_1029_r7b_harvest
"""

import json
import tempfile
import unittest
from pathlib import Path



def _fee_file(td, rows, name="margin_raw.json", broker="synthetic"):
    p = Path(td) / name
    p.write_text(json.dumps({"metadata": {"source_brokerage": broker},
                             "transactions": rows}))
    return p


def _fee_row(i, sym, qty, commission, **kw):
    r = {"id": str(i), "action": "BUYSELL", "date": "2025-03-03",
         "symbol": sym, "quantity": qty, "currency": "CAD",
         "commission": commission, "fee": 0.0}
    r.update(kw)
    return r


class TestFeesFuturesInTheSplit(unittest.TestCase):
    """TJ-0897: the non-option / option split accounts for the plain
    futures' commissions (NON-OPTION), and the legend no longer promises
    a per-unit futures fee.

    Formerly: test_fix_1029_r7b_harvest.TestFeesFuturesInTheSplit"""

    def test_split_adds_up_to_the_total(self):
        from taxjson.bin import taxjson_fees as F
        with tempfile.TemporaryDirectory() as td:
            f = _fee_file(td, [
                _fee_row(1, "F:ZZCLZ5", 1, 2.0, price=70.0),
                _fee_row(2, "F:ZZCLZ5", -1, 2.0, price=71.0),
                _fee_row(3, "ZZOPT250620C00010000.TO", 1, 1.0,
                         gross_amount=100.0)])
            b, g, info = F.aggregate([f], year="2025", since=None,
                                     to_curr="CAD", history={},
                                     default_rate=None, by_account=False)
            lines = F.render_text(b, g, info, to_curr="CAD",
                                  by_account=False, year="2025",
                                  default_rate=None)
        text = "\n".join(lines)
        split = text[text.index("NON-OPTION"):]
        row = [ln for ln in split.splitlines()
               if ln.startswith("synthetic")][0]
        self.assertEqual(row.split()[1:], ["4.00", "1.00"])
        self.assertNotIn("contract of a future", text)



class TestFeesTradeValue(unittest.TestCase):
    """TJ-0776: %NOTNL's trade value is the gross (or price x quantity),
    never a fee-only net cash: a zero-price assignment is out of both
    sides, and a hand-entered trade's fee-inclusive net is not its
    gross.

    Formerly: test_fix_1029_r7b_harvest.TestFeesTradeValue"""

    def _pct(self, rows):
        from taxjson.bin import taxjson_fees as F
        with tempfile.TemporaryDirectory() as td:
            f = _fee_file(td, rows)
            _b, g, _i = F.aggregate([f], year="2025", since=None,
                                    to_curr=None, history={},
                                    default_rate=None, by_account=False)
        m = F.metrics(g["base"])
        return m["total"], m["pct_notional"]

    def test_commission_only_assignment_is_out_of_the_percentage(self):
        total, pct = self._pct([
            _fee_row("a", "ZZSY.TO", 100, 1.0, price=1.0,
                     gross_amount=100.0, net_amount=101.0),
            _fee_row("b", "ZZSY250117C00001000.TO", 1, 2.0, price=0.0,
                     gross_amount=0.0, net_amount=2.0, action="ASSIGN")])
        self.assertAlmostEqual(total, 3.0)
        self.assertAlmostEqual(pct, 1.0)

    def test_hand_entered_trade_value_is_price_times_quantity(self):
        # A .tt row: gross 0 in the parsed book, net 101 with the fee.
        total, pct = self._pct([
            _fee_row("a", "ZZSY.TO", 100, 1.0, price=1.0,
                     gross_amount=0.0, net_amount=101.0)])
        self.assertAlmostEqual(pct, 1.0)

    def test_option_premium_times_contract_size(self):
        from taxjson.bin.taxjson_fees import trade_value
        tx = _fee_row("o", "ZZSY250117C00001000.TO", -1, 0.0, price=0.1,
                      gross_amount=0.0, net_amount=10.0)
        self.assertAlmostEqual(trade_value(tx, True, False), 10.0)
        self.assertAlmostEqual(
            trade_value(dict(tx, multiplier=10), True, False), 1.0)
        # A future's contract size is unknown here: no notional.
        self.assertEqual(trade_value(
            _fee_row("f", "F:ZZCLZ5", 1, 2.0, price=70.0), False, True),
            0.0)
        # No price on the row: its net cash, as before.
        self.assertEqual(trade_value({"net_amount": -50.0}, False, False),
                         50.0)


if __name__ == "__main__":
    unittest.main()
