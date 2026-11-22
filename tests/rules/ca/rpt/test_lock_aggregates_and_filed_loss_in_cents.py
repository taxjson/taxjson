"""Low-round fixes, carryover ledger and filed-year lock (filing-a):
S001-04, S027-18, S027-19, S027-23 (claimed_losses.txt), S027-10 /
S028-00 (per-row rounding of the filed loss), S027-11 / S027-09 (pins),
S028-02 (row currency vs --base-currency); R1-205 / R1-281 (form lines
locked), S031-19 (close-year errors), S031-20 (totals rounded once),
S032-11 (OK says what is not locked). Synthetic data only.

Formerly: test_fix_l_filing_a_co"""
import unittest
from taxjson.lib import filed_locks
from taxjson.bin.taxjson_carryover import build_canada_ledger, yearly_nets


class TestFiledLossRounding(unittest.TestCase):
    """S027-10 / S028-00: the claim is the per-row-rounded Schedule 3
    loss; the ledger's unrounded net differs by cents.

    Formerly: test_fix_l_filing_a_co.TestFiledLossRounding"""

    def test_claim_a_few_cents_under_leaves_no_carryforward(self):
        nets = {2024: {"net": -10000.40, "dispositions": 100},
                2025: {"net": 20000.0, "dispositions": 10}}
        led = build_canada_ledger(nets, {2025: 10000.00})
        self.assertEqual(led["final_carryforward"], 0.0)
        self.assertEqual({r["year"]: r["available_to_apply"]
                          for r in led["rows"]}[2025], 0.0)

    def test_claim_a_few_cents_over_is_not_unmatched(self):
        nets = {2024: {"net": -9999.60, "dispositions": 100},
                2025: {"net": 20000.0, "dispositions": 10}}
        led = build_canada_ledger(nets, {2025: 10000.00})
        self.assertEqual(led["final_carryforward"], 0.0)
        self.assertNotIn("unmatched_claims", led)

    def test_a_real_excess_is_still_unmatched(self):
        nets = {2024: {"net": -9999.60, "dispositions": 2},
                2025: {"net": 20000.0, "dispositions": 10}}
        led = build_canada_ledger(nets, {2025: 10000.00})
        self.assertAlmostEqual(led["unmatched_claims"], 0.40)

    def test_a_partial_claim_keeps_its_balance(self):
        nets = {2024: {"net": -10000.40, "dispositions": 100},
                2025: {"net": 20000.0, "dispositions": 10}}
        led = build_canada_ledger(nets, {2025: 6000.00})
        self.assertAlmostEqual(led["final_carryforward"], 4000.40)


class TestAggregatePins(unittest.TestCase):
    """S027-09: the lock's accumulators and the ledger's disposition
    counts on a small book.

    Formerly: test_fix_l_filing_a_co.TestAggregatePins"""

    DOC = {"transactions": [
        {"action": "BUYSELL", "symbol": "A.TO", "qty": 10, "gain": 100.0,
         "proceeds": 1000.0, "cost": 900.0, "date": "2025-02-03",
         "date_settle": "2025-02-04", "disallowed_amount": 0.0},
        {"action": "BUYSELL", "symbol": "B.TO", "qty": 5, "gain": -40.0,
         "proceeds": 460.0, "cost": 500.0, "date": "2025-03-03",
         "date_settle": "2025-03-04", "disallowed_amount": 15.0},
        {"action": "BUYSELL", "symbol": "C.TO", "qty": 1, "gain": 7.5,
         "proceeds": 57.5, "cost": 50.0, "date": "2025-04-01",
         "date_settle": "2025-04-02"},
        {"action": "DIVIDEND", "date": "2025-05-01", "dividend": 30.0,
         "pil": 0.0},
        {"action": "DIVIDEND_IN_LIEU", "date": "2025-05-01", "dividend": 0.0,
         "pil": 12.0}]}

    def test_aggregates(self):
        a = filed_locks.aggregates_from_gains(self.DOC, year=2025)
        self.assertEqual((a["realized"], a["disallowed"], a["proceeds"],
                          a["income"], a["dividend"], a["pil"],
                          a["dispositions"]),
                         (67.5, 15.0, 1517.5, 42.0, 30.0, 12.0, 3))
        self.assertEqual(a["form_lines"], {"13199": 1517.5, "13200": 67.5})

    def test_yearly_nets_dispositions(self):
        nets = yearly_nets({"transactions": self.DOC["transactions"] + [
            {"action": "BUYSELL", "qty": 1, "gain": 5.0,
             "date": "2024-01-02", "date_settle": "2024-01-03"}]}, "settle")
        self.assertEqual({y: v["dispositions"] for y, v in nets.items()},
                         {2024: 1, 2025: 3})
        self.assertAlmostEqual(nets[2025]["net"], 67.5)


if __name__ == "__main__":
    unittest.main()
