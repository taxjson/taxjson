"""Re-audit-2 fixes, filing-reports list (helper B): option-boundary,
reconcile-slips, Schedule 3 option units, the .sum per-asset block and
the fees report.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_b
"""
import unittest


if __name__ == "__main__":
    unittest.main()


class TestDedupOrderIndependent(unittest.TestCase):
    """A2-1100: the books (.tt files first) and the fees report (sorted
    glob) feed plan_dedup the same rows in different orders; the plan
    keeps the same rows either way.

    Formerly: test_fix_a2_filrep_b.TestDedupOrderIndependent"""

    def test_a2_1100_same_verdict_in_both_orders(self):
        from taxjson.bin.taxjson_sort import plan_dedup

        def row(src):
            return {"id": "same", "action": "BUYSELL", "date": "2025-03-03",
                    "symbol": "XYZ.US", "quantity": 100, "source": src}
        acc = {"ib.csv": ["aaaaaaaaaa"], "ib.csv#2": ["bbbbbbbbbb"]}
        books = [row("m.tt"), row("ib.csv"), row("ib.csv#2")]
        fees = [row("ib.csv"), row("ib.csv#2"), row("m.tt")]
        kb = sorted(books[i]["source"] for i in plan_dedup(books, acc).keep)
        kf = sorted(fees[i]["source"] for i in plan_dedup(fees, acc).keep)
        self.assertEqual(kb, kf)
