"""Fix round 8, group stats: the performance views and an option's premium
around deliveries, returns of capital and snapshots. Every fixture is
SYNTHETIC: made-up tickers, ids and amounts.

Formerly: test_fix_r8_stats
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from _qa_project import tj


def _project(td, books, *, country="canada", year=2025):
    root = Path(td) / "p"
    for acct, text in books.items():
        (root / "inputs" / acct).mkdir(parents=True)
        (root / "inputs" / acct / "trades.tt").write_text(text)
    (root / "ticker.map").write_text("")
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{"USD" if country == "usa" else "CAD"}"\n'
        'source_currencies = []\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in books))
    tj(root, "run", "--no-input")
    return root


class TestAManualFlagNamesARealRebuy(unittest.TestCase):
    """TJ-1317: the shares a loss's sale closed never replace it. Only a
    purchase left held after the sale, or one after it, is that rebuy.

    Formerly: test_fix_r8_stats.TestAManualFlagNamesARealRebuy"""

    def _book(self, x, c):
        opt = f"UNO250620C00010000.{x}"
        return (f'OPENING 2024-12-31 {opt} 1 cost=unknown reason="synthetic"\n'
                f"ASSIGN 2025-06-20 16:00:00 {opt} -1 {c} 0 0\n"
                f"ASSIGN 2025-06-20 16:00:01 UNO.{x} 100 {c} 10 1000\n"
                f"BUYSELL 2025-06-25 10:00:00 UNO.{x} -100 {c} 9 900 0\n")

    def _flags(self, rows, country):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"m": rows}, country=country)
            doc = json.loads(next(
                p for p in (root / "work" / "m_gains_wash.json",
                            root / "work" / "m_gains.json")
                if p.exists()).read_text())
            return [w["acquisition_date"]
                    for w in doc.get("superficial_loss_warnings") or []
                    if w.get("acquisition_date")]

    CASES = (("usa", "US", "USD"), ("canada", "TO", "CAD"))

    def test_the_delivered_lot_it_closed_is_no_rebuy(self):
        for country, x, c in self.CASES:
            with self.subTest(country):
                self.assertEqual(self._flags(self._book(x, c), country), [])

    def test_a_later_rebuy_is_named(self):
        for country, x, c in self.CASES:
            with self.subTest(country):
                self.assertEqual(self._flags(
                    self._book(x, c) + f"BUYSELL 2025-06-30 10:00:00 UNO.{x} "
                    f"100 {c} 9 900 0\n", country), ["2025-06-30"])

    def test_a_purchase_left_held_after_the_sale_is_named(self):
        for country, x, c in self.CASES:
            with self.subTest(country):
                book = self._book(x, c).replace(
                    f"BUYSELL 2025-06-25 10:00:00 UNO.{x} -100",
                    f"BUYSELL 2025-06-22 10:00:00 UNO.{x} 50 {c} 10 500 0\n"
                    f"BUYSELL 2025-06-25 10:00:00 UNO.{x} -100")
                got = self._flags(book, country)
                # (a US sale of the older lot, FIFO, at its unknown cost)
                self.assertEqual(got, ["2025-06-22"])

    @rule(R.CA_ACB_12)
    def test_control_an_older_position_with_a_purchase_kept(self):
        # 100 of unknown cost, 50 bought in the window, 100 sold at a loss:
        # 50 are held, so the purchase stays flagged.
        rows = ('OPENING 2024-12-31 UNO.TO 100 cost=unknown reason="synthetic"\n'
                "BUYSELL 2025-06-20 10:00:00 UNO.TO 50 CAD 10 500 0\n"
                "BUYSELL 2025-06-25 10:00:00 UNO.TO -100 CAD 1 100 0\n")
        self.assertEqual(self._flags(rows, "canada"), ["2025-06-20"])

    @rule(R.CA_ACB_12)
    def test_control_a_split_among_the_rows_keeps_the_flag(self):
        from taxjson.lib.pipeline import _closed_by_sale

        class Row:
            def __init__(self, action, rid, quantity):
                self.action, self.id, self.quantity = action, rid, quantity
                self.date = "2025-06-20"
        rows = [Row("BUYSELL", "b", 100), Row("SPLIT", "x", 100),
                Row("BUYSELL", "s", -100)]
        self.assertEqual(_closed_by_sale(rows, "s"), set())
        self.assertEqual(_closed_by_sale(rows[:1] + rows[2:], "s"),
                         {id(rows[0])})
        # An unknown sale id says nothing either.
        self.assertEqual(_closed_by_sale(rows[:1], None), set())


if __name__ == "__main__":
    unittest.main()
