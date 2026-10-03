"""Mutation pins for lib/market_calendar.py (audit G1-0).

The Federal Reserve list mostly coincides with the NYSE list, so a
wrong Fed date hid behind the union in is_settlement_day; the Easter
arithmetic was checked only through two Good Fridays. Here each list is
pinned on its own (published schedules), Easter against an independent
algorithm, and the era gates at their first year.
"""
import unittest
from datetime import date, timedelta

from taxjson.lib import market_calendar as mc
from tax_rules import rule


def _iso(ds):
    return sorted(d.isoformat() for d in ds)


def _oudin_easter(y):
    """Gregorian Easter by Oudin's (1940) algorithm — a different
    derivation from the anonymous-Gregorian one the module uses."""
    g = y % 19
    c = y // 100
    h = (c - c // 4 - (8 * c + 13) // 25 + 19 * g + 15) % 30
    i = h - (h // 28) * (1 - (29 // (h + 1)) * ((21 - g) // 11))
    j = (y + y // 4 + i + 2 - c + c // 4) % 7
    l = i - j
    month = 3 + (l + 40) // 44
    day = l + 28 - 31 * (month // 4)
    return date(y, month, day)


class TestEaster(unittest.TestCase):
    @rule("CA-DATE-05")
    def test_against_an_independent_algorithm(self):
        # m1677-m1737: every constant of the computus.
        for y in range(1583, 2400):
            self.assertEqual(mc._easter(y), _oudin_easter(y), y)
        for y, d in ((2000, "2000-04-23"), (2019, "2019-04-21"),
                     (2024, "2024-03-31"), (2025, "2025-04-20"),
                     (2038, "2038-04-25"), (1818, "1818-03-22")):
            self.assertEqual(mc._easter(y).isoformat(), d)


class TestUsLists(unittest.TestCase):
    @rule("US-DATE-05")
    def test_federal_reserve_holidays(self):
        # A Sunday holiday moves to Monday, a Saturday one is not
        # observed (m1701-m1703); every date of the list (m1760-m1786).
        self.assertEqual(_iso(mc.fed_holidays(2021)), [
            "2021-01-01", "2021-01-18", "2021-02-15", "2021-05-31",
            "2021-07-05", "2021-09-06", "2021-10-11", "2021-11-11",
            "2021-11-25"])
        self.assertEqual(_iso(mc.fed_holidays(2022)), [
            "2022-01-17", "2022-02-21", "2022-05-30", "2022-06-20",
            "2022-07-04", "2022-09-05", "2022-10-10", "2022-11-11",
            "2022-11-24", "2022-12-26"])
        self.assertEqual(_iso(mc.fed_holidays(2025)), [
            "2025-01-01", "2025-01-20", "2025-02-17", "2025-05-26",
            "2025-06-19", "2025-07-04", "2025-09-01", "2025-10-13",
            "2025-11-11", "2025-11-27", "2025-12-25"])
        self.assertEqual(_iso(mc.fed_holidays(2027)), [
            "2027-01-01", "2027-01-18", "2027-02-15", "2027-05-31",
            "2027-07-05", "2027-09-06", "2027-10-11", "2027-11-11",
            "2027-11-25"])
        self.assertNotIn(date(2020, 6, 19), mc.fed_holidays(2020))

    @rule("US-DATE-05")
    def test_nyse_observance_years(self):
        # m1706/m1743/m1872/m1744: New Year on a Sunday moves to Monday,
        # on a Saturday it is not observed; Memorial Day on May 31
        # (m1819); Saturday holidays move to Friday.
        self.assertEqual(_iso(mc.nyse_holidays(2021)), [
            "2021-01-01", "2021-01-18", "2021-02-15", "2021-04-02",
            "2021-05-31", "2021-07-05", "2021-09-06", "2021-11-25",
            "2021-12-24"])
        self.assertEqual(_iso(mc.nyse_holidays(2022)), [
            "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30",
            "2022-06-20", "2022-07-04", "2022-09-05", "2022-11-24",
            "2022-12-26"])
        self.assertEqual(_iso(mc.nyse_holidays(2023)), [
            "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07",
            "2023-05-29", "2023-06-19", "2023-07-04", "2023-09-04",
            "2023-11-23", "2023-12-25"])
        self.assertEqual(_iso(mc.nyse_holidays(2027)), [
            "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
            "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
            "2027-11-25", "2027-12-24"])


class TestCanadaGates(unittest.TestCase):
    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_family_day_from_2008(self):
        # m1661 / m1713.
        self.assertIn(date(2008, 2, 18), mc.tsx_holidays(2008))
        self.assertNotIn(date(2007, 2, 19), mc.tsx_holidays(2007))

    @rule("CA-DATE-05")
    @rule("US-DATE-05")
    def test_truth_and_reconciliation_from_2021(self):
        # m1663 / m1720.
        self.assertIn(date(2021, 9, 30), mc.cds_holidays(2021))
        self.assertNotIn(date(2020, 9, 30), mc.cds_holidays(2020))
        self.assertFalse(mc.is_settlement_day("2021-09-30", "CAD"))
        self.assertTrue(mc.is_trading_day("2021-09-30", "CAD"))


class TestSubSettlementDays(unittest.TestCase):
    @rule("US-DATE-04")
    def test_latest_trading_day_settling_on_the_date(self):
        # m1810: the day whose settlement lands ON the date (Mon Jul 7
        # settles T+1 on Tue Jul 8), not one settling before it.
        self.assertEqual(mc.sub_settlement_days("2025-07-08", 1, "USD"),
                         date(2025, 7, 7))
        self.assertEqual(mc.sub_settlement_days("2025-07-07", 1, "USD"),
                         date(2025, 7, 3))

    def test_fallback_past_the_search_window(self):
        # m1674: no trading day within 40 calendar days settles by the
        # date -> plain calendar arithmetic.
        self.assertEqual(mc.sub_settlement_days("2025-07-08", 40, "USD"),
                         date(2025, 7, 8) - timedelta(days=40))


if __name__ == "__main__":
    unittest.main()
