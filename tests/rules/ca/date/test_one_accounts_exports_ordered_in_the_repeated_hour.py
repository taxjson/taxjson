"""Fixes of 2026-10-30, round 7, wave E, group cash: T1135 cash in
transit, export coverage, Kraken swap fees, Coinbase's blank Total and
repeated hour across exports, and the crypto-sends migration on read-only
inputs. One test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented books and accounts, made-up tickers
and ids, small amounts. Each test's files live in a folder of their own.

Formerly: test_fix_1029_r7e_cash
"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from _support.cb_price_fill import _HDR as _CB_HDR


def _crypto_project(td, country="canada"):
    root = Path(td) / "c"
    (root / "inputs" / "coin").mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    src = '[]' if country == "canada" else '["CAD"]'
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        f'country = "{country}"\nbase_currency = "{base}"\n'
        f'source_currencies = {src}\n'
        '[accounts.coin]\ntype = "taxable"\ncrypto = true\n')
    (root / "ticker.map").write_text("")
    return root


# ============================================== TJ-1227 the repeated hour
# across two exports
class TestTJ1227RepeatedHourAcrossExports(unittest.TestCase):
    """TJ-1227: the sale and the rebuy compress into one local second in
    two files of one account: file order replayed the rebuy first.

    Formerly: test_fix_1029_r7e_cash.TestTJ1227RepeatedHourAcrossExports"""

    C1 = "C1,2025-01-06 15:00:00 UTC,Buy,ZZE,10,CAD,5,50,50,0,Bought 10 ZZE\n"
    C2 = "C2,2025-11-02 05:00:00 UTC,Sell,ZZE,10,CAD,10,100,100,0,Sold 10 ZZE\n"
    C3 = ("C3,2025-11-02 05:00:01 UTC,Buy,ZZE,10,CAD,10.01,100.1,100.1,0,"
          "Bought 10 ZZE\n")

    def _run(self, files, country="canada"):
        with tempfile.TemporaryDirectory() as td:
            root = _crypto_project(td, country)
            for name, body in files.items():
                (root / "inputs/coin" / name).write_text(_CB_HDR + body)
            tj(root, "run", "--no-input")
            order = [r["id"] for r in json.loads(
                (root / "work/coin_sorted.json").read_text())["transactions"]]
            doc = json.loads((root / "work/coin_gains.json").read_text()) \
                if (root / "work/coin_gains.json").exists() else {}
        return order, doc

    def test_the_sale_goes_first(self):
        order, doc = self._run({"coinbase_a.csv": self.C3,
                                "coinbase_b.csv": self.C1 + self.C2})
        self.assertEqual(order.index("C2") < order.index("C3"), True, order)
        self.assertAlmostEqual(doc["summary"]["total_gain"], 50.0, places=2)

    def test_the_other_file_order_too(self):
        order, _d = self._run({"coinbase_a.csv": self.C1 + self.C2,
                               "coinbase_b.csv": self.C3})
        self.assertEqual(order.index("C2") < order.index("C3"), True, order)

    def test_a_us_project_too(self):
        order, _d = self._run({"coinbase_a.csv": self.C3,
                               "coinbase_b.csv": self.C1 + self.C2}, "usa")
        self.assertEqual(order.index("C2") < order.index("C3"), True, order)

    def test_the_order_function(self):
        from types import SimpleNamespace as NS
        from taxjson.lib.brokerages._crypto_common import order_across_exports
        rows = [NS(date="d", time="t", source="a"),
                NS(date="d", time="t", source="b"),
                NS(date="d", time="u", source="b")]
        self.assertEqual(order_across_exports(rows, [2, 1, 9]), [1, 0, 2])
        # An unknown instant, or one export: as given.
        self.assertEqual(order_across_exports(rows, [2, None, 9]), [0, 1, 2])
        same = [NS(date="d", time="t", source="a"),
                NS(date="d", time="t", source="a")]
        self.assertEqual(order_across_exports(same, [2, 1]), [0, 1])
        # One instant: the file order.
        self.assertEqual(order_across_exports(rows, [1, 1, 9]), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
