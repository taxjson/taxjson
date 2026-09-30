"""Medium-round fixes, instalments (filing-a): R1-42, R1-214, R1-220,
S034-16. Synthetic figures only."""
import unittest
from datetime import date, timedelta

from taxjson.bin import taxjson_instalments as INST
from taxjson.bin.taxjson_instalments import build, render


def _grow(start: date, end: date, rate_of) -> float:
    """Daily-compounded growth - 1 over [start, end] inclusive."""
    g, d = 1.0, start
    while d <= end:
        g *= 1 + rate_of(d) / 365.0
        d += timedelta(days=1)
    return g - 1.0


def _cra_a_minus_b(required, payments, year, end, rate_of):
    """The CRA interest-penalty page's method, written independently:
    A = interest on each required instalment from its due date to the
    balance-due date; B = interest on each payment from the later of
    the payment date and January 1; C = A - B."""
    a = sum(r["amount"] * _grow(date.fromisoformat(r["date"]), end, rate_of)
            for r in required if date.fromisoformat(r["date"]) <= end)
    b = sum(p["amount"] * _grow(max(date.fromisoformat(p["date"]),
                                    date(year, 1, 1)), end, rate_of)
            for p in payments if date.fromisoformat(p["date"]) <= end)
    return a, b


def _rate_2025(d):
    return 0.08 if d < date(2025, 7, 1) else 0.07


class TestR1_42_InterestIsAMinusB(unittest.TestCase):
    """Catching up before the balance-due date must not freeze the
    accrued charge interest: CRA computes A - B, compounded daily."""

    def _case(self, prior, pays):
        payments = [{"date": d, "amount": a} for d, a in pays]
        doc = build(year=2025, basis="prior_year", current_net_tax=1e7,
                    payments=payments, prior_net_tax=prior,
                    second_prior_net_tax=prior, as_of=date(2026, 9, 29))
        req = INST.required_schedule(year=2025, basis="prior_year",
                                     current_net_tax=1e7,
                                     prior_net_tax=prior)
        a, b = _cra_a_minus_b(req, payments, 2025, date(2026, 4, 30),
                              _rate_2025)
        return doc, a - b

    def test_late_lump_sum(self):
        doc, want = self._case(40000, [("2025-12-15", 40000)])
        self.assertAlmostEqual(want, 1132.93, places=1)
        self.assertAlmostEqual(doc["net_interest_computed"], want, places=1)

    def test_one_late_instalment(self):
        doc, want = self._case(40000, [("2025-06-16", 20000),
                                       ("2025-09-15", 10000),
                                       ("2025-12-15", 10000)])
        self.assertAlmostEqual(doc["net_interest_computed"], want, places=1)

    def test_small_offset_is_charged_over_25(self):
        doc, want = self._case(34000, [("2025-06-16", 17000),
                                       ("2025-09-15", 17000)])
        self.assertGreater(want, 25.0)
        self.assertAlmostEqual(doc["net_interest"], want, places=1)

    def test_never_caught_up_unchanged(self):
        doc, want = self._case(40000, [("2025-08-01", 5000)])
        self.assertAlmostEqual(doc["net_interest"], want, places=1)


class TestR1_214_OnePriorYearUnknown(unittest.TestCase):
    """s.156.1(1): both preceding years must be at or below $3,000 to
    waive instalments; with one unknown the limb is unknown."""

    def test_one_low_prior_and_one_unset_is_unknown(self):
        doc = build(year=2026, basis="prior_year", current_net_tax=21520.0,
                    prior_net_tax=2500.0, payments=[], annual_rate=0.07,
                    as_of=date(2026, 9, 29))
        self.assertEqual(doc["prior_year_test"], "unknown")
        self.assertTrue(doc["required_at_all"])

    def test_both_low_still_not_met(self):
        doc = build(year=2026, basis="prior_year", current_net_tax=21520.0,
                    prior_net_tax=2500.0, second_prior_net_tax=2000.0,
                    payments=[], annual_rate=0.07, as_of=date(2026, 9, 29))
        self.assertEqual(doc["prior_year_test"], "not_met")
        self.assertFalse(doc["required_at_all"])

    def test_one_high_prior_met_even_with_other_unset(self):
        doc = build(year=2026, basis="prior_year", current_net_tax=21520.0,
                    prior_net_tax=40000.0, payments=[], annual_rate=0.07,
                    as_of=date(2026, 9, 29))
        self.assertEqual(doc["prior_year_test"], "met")

    def test_render_never_prints_an_unset_year_as_zero(self):
        doc = build(year=2026, basis="prior_year", current_net_tax=21520.0,
                    prior_net_tax=2500.0, second_prior_net_tax=2000.0,
                    payments=[], annual_rate=0.07, as_of=date(2026, 9, 29))
        doc["second_prior_net_tax"] = None       # forced render path
        flat = " ".join(render(doc, "CAD").split())
        self.assertNotIn("and 0.00", flat)
        self.assertIn("not set", flat)


class TestR1_220_RatesBeforeTheTable(unittest.TestCase):
    """CRA's 2023 overdue-tax rates: Q1 8%, Q2-Q4 9% (prescribed-
    interest-rates/2023-q1..q4 pages); 10% from 2024-01-01."""

    def test_2023_year_uses_2023_rates(self):
        doc = build(year=2023, basis="prior_year", current_net_tax=40000.0,
                    prior_net_tax=40000.0, second_prior_net_tax=40000.0,
                    payments=[], as_of=date(2024, 4, 30))
        sched = [(s["from"], s["rate"]) for s in doc["rate_schedule"]]
        self.assertEqual(sched[:3], [("2023-01-01", 0.08),
                                     ("2023-04-01", 0.09),
                                     ("2024-01-01", 0.10)])
        self.assertFalse(doc["rate_extrapolated"])

    def test_year_before_the_table_is_flagged(self):
        doc = build(year=2021, basis="prior_year", current_net_tax=40000.0,
                    prior_net_tax=40000.0, second_prior_net_tax=40000.0,
                    payments=[], as_of=date(2022, 4, 30))
        self.assertTrue(doc["rate_extrapolated"])
        text = " ".join(render(doc, "CAD").split())
        self.assertIn("before", text)
        self.assertIn(INST.PUBLISHED_RATES[0][0], text)


class TestS034_16_LeastCumulativePerDate(unittest.TestCase):
    """ITA 161(4.01): on each instalment day the deemed requirement is
    the method giving the LEAST total required BY THAT DAY."""

    def test_mixed_schedule(self):
        doc = build(year=2026, basis="current_year", current_net_tax=40000.0,
                    prior_net_tax=48000.0, second_prior_net_tax=0.0,
                    payments=[], annual_rate=0.07, as_of=date(2027, 4, 30))
        self.assertEqual(doc["interest_basis"], "least_per_date")
        self.assertEqual([r["amount"] for r in doc["governing_schedule"]],
                         [0.0, 0.0, 24000.0, 16000.0])
        a, _ = _cra_a_minus_b(doc["governing_schedule"], [], 2026,
                              date(2027, 4, 30), lambda d: 0.07)
        self.assertAlmostEqual(doc["net_interest"], a, places=1)
        self.assertLess(doc["net_interest"], 1703.0)   # old whole-basis min
        self.assertAlmostEqual(doc["interest_if_unpaid"], a, places=1)
        flat = " ".join(render(doc, "CAD").split())
        self.assertIn("161(4.01)", flat)
        for line in render(doc, "CAD").splitlines():
            self.assertLessEqual(len(line), 78, repr(line))

    def test_single_method_keeps_its_name(self):
        doc = build(year=2026, basis="current_year",
                    current_net_tax=200000.0, prior_net_tax=40000.0,
                    payments=[], annual_rate=0.08, as_of=date(2027, 4, 30))
        self.assertEqual(doc["interest_basis"], "prior_year")


if __name__ == "__main__":
    unittest.main()
