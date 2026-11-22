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

from tax_rules import rule
from taxjson.rules import ids as R
from _support.ca_edge_analysis import _t, _edge


class TestWriteAcrossYearEndUnderGrant(unittest.TestCase):
    """TJ-1067: the premium's gain row carries the write's own id; that
    is no closing disposition. Keeps TJ-0690 (a pooled share sale).

    Formerly: test_fix_1029_r7d_edgeplan.TestWriteAcrossYearEndUnderGrant"""
    CALL = "ZZG260320C00001000.TO"

    def _row(self, year, rows):
        _doc, edge, _text = _edge({"margin": rows}, year=year, timing="grant")
        return edge["year_boundary"]["straddles"]

    @rule(R.CA_RPT_07)
    def test_both_project_years_call_it_a_write(self):
        rows = [_t("margin", "BUYSELL", "2025-12-31", self.CALL, -1, 50.0,
                   settle="2026-01-02")]
        for year in (2025, 2026):
            (r,) = self._row(year, rows)
            self.assertIn("2026 option write; under grant timing the "
                          "premium is a gain in 2026", r["why"])
            self.assertNotIn("option sale", r["why"])
        self.assertEqual(self._row(2026, rows)[0]["gain"], 50.0)

    @rule(R.CA_RPT_07)
    def test_sale_of_a_long_option_stays_an_option_sale(self):
        rows = [_t("margin", "BUYSELL", "2025-06-02", self.CALL, 1, -20.0),
                _t("margin", "BUYSELL", "2025-12-31", self.CALL, -1, 50.0,
                   settle="2026-01-02")]
        (r,) = self._row(2026, rows)
        self.assertIn("2026 option sale: a disposition", r["why"])

    @rule(R.CA_RPT_07)
    def test_buy_to_close_stays_a_buy_to_close(self):
        rows = [_t("margin", "BUYSELL", "2025-06-02", self.CALL, -1, 50.0),
                _t("margin", "BUYSELL", "2025-12-31", self.CALL, 1, -20.0,
                   settle="2026-01-02")]
        (r,) = self._row(2026, rows)
        self.assertIn("buy-to-close of a written option", r["why"])


class TestExpiryReviewOppositeLeg(unittest.TestCase):
    """TJ-1068: the engine's ledger gives an unpaired premium to an
    opposite-direction trade when no leg of its own direction is to come.
    Keeps TJ-0674 (the leg's own settlement year; no leg: the warning).

    Formerly: test_fix_1029_r7d_edgeplan.TestExpiryReviewOppositeLeg"""
    S = "ZZX.TO"
    CALL = "ZZX251231C00001000.TO"

    def _rows(self):
        return [_t("margin", "BUYSELL", "2025-01-02", self.S, 100, -50.0),
                _t("margin", "BUYSELL", "2025-11-03", self.CALL, -1, 10.0),
                _t("margin", "ASSIGN", "2025-12-31", self.CALL, 1, 0.0,
                   price=0.0)]

    @rule(R.CA_RPT_07, R.CA_OPT_08)
    def test_names_the_leg_the_engine_folded_into(self):
        rows = self._rows() + [_t("margin", "ASSIGN", "2025-12-31", self.S,
                                  100, -100.0, settle="2026-01-02")]
        for year in (2025, 2026):
            doc, edge, _text = _edge({"margin": rows}, year=year)
            (r,) = edge["year_boundary"]["option_expiries"]
            self.assertEqual(r["lands_in"], 2026)
            self.assertIn("folds into the ZZX.TO leg", r["why"])
            self.assertNotIn("add the missing share row", r["why"])
        inv = [(h["symbol"], h["qty"], h["total_cost"])
               for h in doc["inventory_year_end"]]
        self.assertEqual(inv, [(self.S, 200.0, 140.0)])

    @rule(R.CA_RPT_07, R.CA_OPT_08)
    def test_same_direction_leg_is_preferred(self):
        # The sale (own direction) comes after the buy: the ledger skips
        # the buy, and so does the review.
        rows = self._rows() + [
            _t("margin", "BUYSELL", "2025-12-31", self.S, 10, -10.0,
               settle="2026-01-02", time="11:00:00"),
            _t("margin", "BUYSELL", "2026-01-05", self.S, -100, 200.0,
               time="11:00:00")]
        _doc, edge, _text = _edge({"margin": rows}, year=2026)
        (r,) = edge["year_boundary"]["option_expiries"]
        self.assertIn("leg of 2026-01-05", r["why"])

    @rule(R.CA_RPT_07, R.CA_OPT_08)
    def test_no_trade_in_the_window_still_asks_for_the_row(self):
        _doc, edge, _text = _edge({"margin": self._rows()})
        (r,) = edge["year_boundary"]["option_expiries"]
        self.assertIsNone(r["lands_in"])
        self.assertIn("add the missing share row", r["why"])


class TestHeldOnDay30PerHolder(unittest.TestCase):
    """TJ-1113: a holder that acquired nothing in the window backs no
    denial (CA-SL-08), so its old units are not the loss's day-30 count.
    Keeps TJ-0689 (calls_in_windows per holder).

    Formerly: test_fix_1029_r7d_edgeplan.TestHeldOnDay30PerHolder"""
    S = "ZZH.TO"

    def _books(self, loss="2025-11-03", rrsp_buy="2025-10-04",
               rrsp_sell="2025-10-20"):
        margin = [_t("margin", "BUYSELL", "2025-01-06", self.S, 100, -2000.0),
                  _t("margin", "BUYSELL", loss, self.S, -100, 1000.0)]
        tfsa = [_t("tfsa", "BUYSELL", "2025-01-10", self.S, 50, -500.0)]
        rrsp = [_t("rrsp", "BUYSELL", rrsp_buy, self.S, 10, -100.0)]
        if rrsp_sell:
            rrsp.append(_t("rrsp", "BUYSELL", rrsp_sell, self.S, -10, 90.0))
        return {"margin": margin}, {"tfsa": tfsa, "rrsp": rrsp}

    @rule(R.CA_RPT_07, R.CA_SL_08)
    def test_another_holders_old_units_do_not_count(self):
        books, shel = self._books()
        doc, edge, text = _edge(books, shel)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 0.0)
        self.assertIn("none of it was still held on day 30",
                      w["items"][0]["why"])

    @rule(R.CA_RPT_07, R.CA_SL_08)
    def test_a_long_call_is_not_judged_by_the_shares_held(self):
        books, shel = self._books()
        shel["rrsp"] = [_t("rrsp", "BUYSELL", "2025-10-04",
                           "ZZH260116C00001000.TO", 1, -5.0)]
        _doc, edge, _text = _edge(books, shel)
        (w,) = edge["window_edges"]
        (it,) = [i for i in w["items"] if i["kind"] == "long call"]
        self.assertNotIn("none of it was still held", it["why"])

    @rule(R.CA_RPT_07, R.CA_SL_08)
    def test_a_holder_that_acquired_in_the_window_counts(self):
        books, shel = self._books(rrsp_sell=None)
        doc, edge, text = _edge(books, shel)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 10.0)
        self.assertNotIn("none of it was still held", w["items"][0]["why"])

    @rule(R.CA_RPT_07, R.CA_SL_08)
    def test_allowed_reason_across_the_year_end(self):
        books, shel = self._books(loss="2025-12-10", rrsp_buy="2026-01-05",
                                  rrsp_sell="2026-01-07")
        doc, edge, _text = _edge(books, shel)
        (r,) = edge["year_boundary"]["loss_windows"]
        self.assertEqual(r["denied"], 0.0)
        self.assertEqual(r["allowed_by"], "not_held_day30")
        self.assertIn("no units were held at the end of day 30", r["why"])
        self.assertIn("50 held by another holder", r["why"])

    @rule(R.CA_RPT_07, R.CA_SL_08)
    def test_no_other_holder_keeps_the_plain_reason(self):
        books, shel = self._books(loss="2025-12-10", rrsp_buy="2026-01-05",
                                  rrsp_sell="2026-01-07")
        del shel["tfsa"]
        _doc, edge, _text = _edge(books, shel)
        (r,) = edge["year_boundary"]["loss_windows"]
        self.assertEqual(r["allowed_by"], "not_held_day30")
        self.assertNotIn("another holder", r["why"])


if __name__ == "__main__":
    unittest.main()
