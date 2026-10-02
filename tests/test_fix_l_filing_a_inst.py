"""Low-round fixes and pins, `taxjson instalments`
(bin/taxjson_instalments.py): R1-221 (A-B pinned), R1-222 (no interest
when instalments are waived), S034-13 (Saturday due date), S034-14
(quarters add up to the net tax), S034-15 (configured schedule starting
late is disclosed), S034-17 (render at exactly 3,000), S034-18 (rate
table edges)."""
import unittest
from datetime import date, timedelta

from taxjson.bin import taxjson_instalments as I
from tax_rules import rule


class TestWaived(unittest.TestCase):
    """R1-222: s.156.1(1) waives instalments -> no shortfall, interest
    or penalty in the document either."""

    def test_current_year_at_or_below_threshold(self):
        doc = I.build(year=2025, basis="current_year",
                      current_net_tax=2900.0, payments=[],
                      as_of=date(2026, 4, 30))
        self.assertFalse(doc["required_at_all"])
        for k in ("shortfall", "net_interest", "penalty",
                  "net_interest_computed", "remaining_total",
                  "per_remaining_date"):
            self.assertEqual(doc[k], 0.0, k)

    def test_prior_years_below_threshold(self):
        doc = I.build(year=2025, basis="current_year",
                      current_net_tax=50000.0, payments=[],
                      prior_net_tax=2500.0, second_prior_net_tax=2000.0,
                      as_of=date(2026, 4, 30))
        self.assertEqual(doc["prior_year_test"], "not_met")
        self.assertEqual((doc["net_interest"], doc["shortfall"]), (0.0, 0.0))

    def test_required_case_still_charges(self):
        doc = I.build(year=2025, basis="current_year",
                      current_net_tax=50000.0, payments=[],
                      prior_net_tax=20000.0, second_prior_net_tax=20000.0,
                      as_of=date(2026, 4, 30))
        self.assertTrue(doc["required_at_all"])
        self.assertGreater(doc["net_interest"], 0.0)


class TestQuartersAddUp(unittest.TestCase):
    """S034-14"""

    def test_exact_payer_is_paid(self):
        req = I.required_schedule(year=2025, basis="current_year",
                                  current_net_tax=10000.03)
        self.assertAlmostEqual(sum(r["amount"] for r in req), 10000.03,
                               places=6)
        pays = [{"date": r["date"], "amount": a} for r, a in
                zip(req, (2500.00, 2500.01, 2500.01, 2500.01))]
        doc = I.build(year=2025, basis="current_year",
                      current_net_tax=10000.03, payments=pays,
                      prior_net_tax=20000.0, as_of=date(2026, 4, 30))
        self.assertEqual(doc["required_total"], 10000.03)
        self.assertEqual(doc["shortfall"], 0.0)
        self.assertNotIn("missed", [r["status"] for r in doc["required"]])
        # Paying each quarter's requirement is paid on every date.
        doc = I.build(year=2025, basis="current_year",
                      current_net_tax=10000.03, payments=list(req),
                      prior_net_tax=20000.0, as_of=date(2026, 4, 30))
        self.assertEqual([r["status"] for r in doc["required"]],
                         ["paid"] * 4)


@rule("CA-RPT-11")
class TestConfiguredScheduleStartingLate(unittest.TestCase):
    """S034-15"""

    def test_disclosed(self):
        rates = [{"from": "2025-07-01", "rate": 0.07},
                 {"from": "2026-01-01", "rate": 0.07}]
        doc = I.build(year=2025, basis="current_year",
                      current_net_tax=40000.0, payments=[],
                      prior_net_tax=40000.0, second_prior_net_tax=40000.0,
                      annual_rate=rates, as_of=date(2026, 4, 30))
        self.assertEqual(doc["rate_assumed_before"], "2025-07-01")
        self.assertTrue(doc["rate_extrapolated"])
        text = " ".join(I.render(doc, "CAD").split())
        self.assertIn("days before 2025-07-01 ASSUME the first configured "
                      "rate", text)

    def test_schedule_from_jan1_is_not_flagged(self):
        doc = I.build(year=2025, basis="current_year",
                      current_net_tax=40000.0, payments=[],
                      prior_net_tax=40000.0,
                      annual_rate=[{"from": "2025-01-01", "rate": 0.08}],
                      as_of=date(2026, 4, 30))
        self.assertNotIn("rate_assumed_before", doc)
        self.assertFalse(doc["rate_extrapolated"])


class TestPins(unittest.TestCase):

    def test_offset_method_is_a_minus_b(self):
        # R1-221: four instalments paid 60 days late at 7% — CRA's A - B
        # (interest keeps compounding after the catch-up): 1,081.04.
        req = I.required_schedule(year=2026, basis="current_year",
                                  current_net_tax=89644.24)
        pays = [{"date": (date.fromisoformat(r["date"])
                          + timedelta(days=60)).isoformat(),
                 "amount": r["amount"]} for r in req]
        ip = I.interest_and_penalty(required=req, payments=pays,
                                    annual_rate=0.07,
                                    end=date(2027, 4, 30))
        self.assertEqual(ip["net_interest"], 1081.04)

    def test_saturday_due_date_rolls_to_monday(self):
        # S034-13: 2025-03-15 is a Saturday, 2025-06-15 a Sunday.
        self.assertEqual([d.isoformat() for d in I.due_dates(2025)],
                         ["2025-03-17", "2025-06-16", "2025-09-15",
                          "2025-12-15"])

    def test_render_at_exactly_the_threshold(self):
        # S034-17: the current-year limb, not the prior years, explains it.
        doc = I.build(year=2025, basis="current_year",
                      current_net_tax=3000.0, payments=[],
                      prior_net_tax=10000.0, second_prior_net_tax=10000.0,
                      as_of=date(2026, 4, 30))
        text = " ".join(I.render(doc, "CAD").split())
        self.assertIn("Estimated net tax owing 3,000.00 is at or below the "
                      "3,000.00 threshold", text)
        self.assertNotIn("both at or below", text)

    def test_rate_change_on_the_end_date(self):
        # S034-18: a change effective ON the end date is in the schedule.
        sched = I.published_rates(date(2025, 3, 17), date(2025, 7, 1))
        self.assertEqual(sched[-1], {"from": "2025-07-01", "rate": 0.07})

    def test_published_through_day_is_not_extrapolated(self):
        doc = I.build(year=2026, basis="current_year",
                      current_net_tax=40000.0, payments=[],
                      prior_net_tax=40000.0, as_of=date(2026, 12, 31))
        self.assertFalse(doc["rate_extrapolated_after"])
        doc = I.build(year=2026, basis="current_year",
                      current_net_tax=40000.0, payments=[],
                      prior_net_tax=40000.0, as_of=date(2027, 1, 1))
        self.assertTrue(doc["rate_extrapolated_after"])


if __name__ == "__main__":
    unittest.main()
