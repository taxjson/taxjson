"""Backlog fix round 7 (wave D) of 2026-10-29, group `edgeplan`: edge
cases, option-boundary, the estimate and the carryover.

TJ-1046  option-boundary's already-amended test left out the same-day
         buy-back in the lock's figure for a series and day, so a lock
         still reporting the assigned premium read as amended.
TJ-1067  under grant timing a write across Dec 31 is an option write in
         both project years, not an "option sale" in the new one.
TJ-1068  the expiry review names the share leg the engine's premium
         ledger used, also one in the opposite direction.
TJ-1113  the window review's units held on day 30 are the holders' the
         engine tests, not every account's.
TJ-1030  a minimum tax carryover is claimed only while the claim lowers
         tax (or the origin would lapse anyway).
TJ-1075  a damaged prior-year lock (its year field is not its file's)
         fails `tjs carryover` as damaged, not with --filed-lock advice.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*, LOSS*), account names (margin), small
amounts (or round test figures).

Formerly: test_fix_1029_r7d_edgeplan
"""
import unittest
from datetime import date

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.lib.option_boundary import straddling
from _support.ca_edge_analysis import _PUT, _b, _assigned_2025, _engine_lock


class TestAmendedTestCountsTheDaysBuyBack(unittest.TestCase):
    """TJ-1046: a lock's figure for a series and day also holds the loss
    of a buy-back that day. Keeps TJ-0679 (rules.ca.plan.test_radar_forward_views_option_amendments_and_watch_wording):
    an amended lock is still recognised, for the whole day.

    Formerly: test_fix_1029_r7d_edgeplan.TestAmendedTestCountsTheDaysBuyBack"""

    def _action(self, y2024, lock):
        rows = straddling(y2024 + _assigned_2025(), 2025, "grant", 2024,
                          filed_years={2024}, today=date(2026, 9, 29),
                          filed_premiums={2024: lock})
        (r,) = [r for r in rows if r["close_kind"] == "assignment"]
        return r["action"]

    def _hours(self, *rows):
        for n, t in enumerate(rows):
            t.time = f"{9 + n:02d}:00:00"
        return list(rows)

    @rule(R.CA_OPT_07)
    def test_same_day_write_bought_back_that_day(self):
        y2024 = self._hours(_b("2024-12-02", -1, 20.0),
                            _b("2024-12-02", 1, -25.0),
                            _b("2024-12-02", -1, 10.0))
        lock = _engine_lock(y2024)
        self.assertEqual(lock[(_PUT, "2024-12-02")], 5.0)
        self.assertTrue(self._action(y2024, lock).startswith(
            "T1-ADJ 2024: remove the 10.00 premium"))
        # Amended: the lock reports B's write and buy-back only.
        amended = {(_PUT, "2024-12-02"): -5.0}
        self.assertTrue(self._action(y2024, amended).startswith(
            "no amendment"))

    @rule(R.CA_OPT_07)
    def test_older_write_bought_back_on_the_assigned_writes_day(self):
        y2024 = self._hours(_b("2024-11-01", -1, 20.0),
                            _b("2024-12-02", 1, -25.0),
                            _b("2024-12-02", -1, 10.0))
        lock = _engine_lock(y2024)
        self.assertEqual(lock[(_PUT, "2024-12-02")], -15.0)
        self.assertTrue(self._action(y2024, lock).startswith("T1-ADJ 2024"))
        self.assertTrue(self._action(
            y2024, {(_PUT, "2024-12-02"): -25.0}).startswith("no amendment"))

    @rule(R.CA_OPT_07)
    def test_buy_back_traded_on_the_writes_settlement_day(self):
        # The lock keys a row by its trade and settlement dates both.
        y2024 = self._hours(
            _b("2024-11-01", -1, 20.0, settle="2024-11-04"),
            _b("2024-12-03", 1, -25.0, settle="2024-12-04"),
            _b("2024-12-02", -1, 10.0, settle="2024-12-03"))
        y2024 = [y2024[0], y2024[2], y2024[1]]
        lock = _engine_lock(y2024)
        self.assertEqual(lock[(_PUT, "2024-12-03")], -15.0)
        self.assertTrue(self._action(y2024, lock).startswith("T1-ADJ 2024"))

    @rule(R.CA_OPT_07)
    def test_writes_on_consecutive_days_share_a_key(self):
        # X settles the day Y trades: the lock's Dec 3 figure holds both.
        y2024 = self._hours(_b("2024-12-02", -1, 10.0, settle="2024-12-03"),
                            _b("2024-12-03", -1, 20.0, settle="2024-12-04"))
        lock = {(_PUT, "2024-12-02"): 0.0, (_PUT, "2024-12-03"): 20.0,
                (_PUT, "2024-12-04"): 20.0}
        rows = straddling(y2024 + _assigned_2025(), 2025, "grant", 2024,
                          filed_years={2024}, today=date(2026, 9, 29),
                          filed_premiums={2024: lock})
        (r,) = [r for r in rows if r["close_kind"] == "assignment"]
        self.assertTrue(r["action"].startswith("no amendment"), r["action"])


if __name__ == "__main__":
    unittest.main()
