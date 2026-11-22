"""Fix round 7 (wave D) of 2026-10-29, slip-audit and reconcile-slips:
backlog TJ-1049, TJ-1050, TJ-1051, TJ-1124, TJ-1125, TJ-1091, TJ-1092,
TJ-1093. Synthetic data only: made-up tickers (SYN, ALFA, BETA, ZZQ) and
broker accounts (U1234567).

Formerly: test_fix_1029_r7d_slips"""
import unittest
from taxjson.lib import slip_audit as SA
from _support.ib_dividend_slips import (
    CFG, _div, _ib, _pay, _project, _report, _row, _securities, _slip,
)

ROC = ("T3", "roc", "T3: Return of Capital", 20.0)
ELIG = ("T3", "eligible", "T3: Eligible Dividend Income", 30.0)


def _adjusts(lines):
    return [ln for ln in lines if ln.startswith("ADJUST")]


def _adjust_total(lines):
    return round(sum(-float(ln.split()[5]) for ln in _adjusts(lines)), 2)


class PriorYearIbRoc(unittest.TestCase):
    """TJ-1049, TJ-1124: the per-fund figures take only the components
    of this year's slip (slip_year), as ib_slips does.

    Formerly: test_fix_1029_r7d_slips.PriorYearIbRoc"""

    def _books(self):
        return [
            _div("SYN.UN.TO", "2025-01-15", 60.0, record_date="2024-12-31",
                 income_label="distribution", issuer_country="CA"),
            {"action": "ADJUST", "symbol": "SYN.UN.TO", "date": "2025-01-15",
             "net_amount": -40.0, "type": "roc", "record_date": "2024-12-31",
             "issuer_country": "CA"},
            _div("SYN.UN.TO", "2025-06-16", 60.0, record_date="2025-05-30",
                 income_label="distribution", issuer_country="CA"),
            {"action": "ADJUST", "symbol": "SYN.UN.TO", "date": "2025-06-16",
             "net_amount": -40.0, "type": "roc", "record_date": "2025-05-30",
             "issuer_country": "CA"}]

    def _report(self, sale_roc=40):
        rows = []
        for pay, ex in (("20250115", "20241230"), ("20250616", "20250529")):
            rows += [("Summary", "SYN.UN", pay, ex, "", 100),
                     ("RevenueComponent", "SYN.UN", pay, ex,
                      "T3: Return of Capital", sale_roc),
                     ("RevenueComponent", "SYN.UN", pay, ex,
                      "T3: Eligible Dividend Income", 100 - sale_roc)]
        return _report(rows)

    def test_january_paid_distribution_is_no_finding(self):
        rep = SA.audit(_project(self._books(), self._report()), CFG)
        self.assertEqual(rep["status"], "ok", rep["issues"])
        rec = rep["accounts"][0]["groups"][0]["securities"][0]
        self.assertEqual(rec["slip"]["roc"], 40.0)
        self.assertEqual(rec["books"]["roc"], 40.0)

    def test_this_years_difference_is_still_a_finding(self):
        """TJ-0806 stands: another fund offsets this year's ROC."""
        books = [
            _div("ALFA.UN.TO", "2025-06-16", 60.0, issuer_country="CA"),
            {"action": "ADJUST", "symbol": "ALFA.UN.TO", "date": "2025-06-16",
             "net_amount": -30.0, "type": "roc", "issuer_country": "CA"},
            _div("BETA.UN.TO", "2025-06-16", 60.0, issuer_country="CA"),
            {"action": "ADJUST", "symbol": "BETA.UN.TO", "date": "2025-06-16",
             "net_amount": -50.0, "type": "roc", "issuer_country": "CA"}]
        rows = []
        for sym in ("ALFA.UN", "BETA.UN"):
            rows += [("Summary", sym, "20250616", "20250529", "", 100),
                     ("RevenueComponent", sym, "20250616", "20250529",
                      "T3: Return of Capital", 40),
                     ("RevenueComponent", sym, "20250616", "20250529",
                      "T3: Eligible Dividend Income", 60)]
        rep = SA.audit(_project(books, _report(rows)), CFG)
        self.assertIn("security-differs",
                      [i["kind"] for i in rep["issues"]])

    def test_prior_year_payment_suggests_nothing(self):
        rows = [_row(day="2024-05-02", amt=50.0),
                _row(day="2025-07-02", amt=50.0)]
        slip = _ib([_pay(day="2024-05-02", ex="2024-04-15",
                         parts=(ROC, ELIG)),
                    _pay(day="2025-07-02", ex="2025-06-15",
                         parts=(ROC, ELIG))])
        recs, lines, _ = _securities([slip], rows)
        self.assertEqual(recs[0]["slip"]["roc"], 20.0)
        self.assertEqual(lines, [
            "ADJUST 2025-07-02 09:30:00 SYN.UN.TO CAD -20.00 type=roc",
            "DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 -20.00"])

    def test_prior_year_box_18_is_left_out(self):
        cg = ("T5", "cg_div", "Capital Gain", 20.0)
        rows = [_row(day="2025-07-02", amt=50.0)]
        slip = _ib([_pay(day="2024-05-02", ex="2024-04-15", parts=(cg,)),
                    _pay(day="2025-07-02", ex="2025-06-15", parts=(cg,))])
        recs, _lines, _ = _securities([slip], rows)
        self.assertEqual(recs[0]["slip"]["cg_div"], 20.0)

    def test_december_distribution_paid_next_january_counts(self):
        """The other side of slip_year: paid in 2026, this year's T3."""
        rows = [_row(day="2026-01-15", amt=50.0, tax="2025-12-31",
                     record="2025-12-31")]
        slip = _ib([_pay(day="2026-01-15", ex="2025-12-30",
                         parts=(ROC, ELIG))])
        recs, lines, _ = _securities([slip], rows)
        self.assertEqual(recs[0]["slip"]["roc"], 20.0)
        self.assertIn("ADJUST 2026-01-15 09:30:00 SYN.UN.TO CAD -20.00 "
                      "type=roc record=2025-12-31", lines)


class SameDayRocPayments(unittest.TestCase):
    """TJ-1125: two payments of one fund paid the same day.

    Formerly: test_fix_1029_r7d_slips.SameDayRocPayments"""

    def _pays(self):
        return [_pay(ex="2025-06-15", parts=(ROC, ELIG)),
                _pay(ex="2025-06-25", parts=(ROC, ELIG))]

    def test_two_rows_get_the_whole_return_of_capital(self):
        rows = [_row(day="2025-07-02", amt=50.0),
                _row(day="2025-07-02", amt=50.0)]
        recs, lines, _ = _securities([_ib(self._pays())], rows)
        self.assertEqual(recs[0]["slip"]["roc"], 40.0)
        self.assertEqual(lines, [
            "ADJUST 2025-07-02 09:30:00 SYN.UN.TO CAD -40.00 type=roc",
            "DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 -40.00"])

    def test_one_combined_row(self):
        rows = [_row(day="2025-07-02", amt=100.0)]
        _recs, lines, _ = _securities([_ib(self._pays())], rows)
        self.assertEqual(_adjust_total(lines), 40.0)
        self.assertIn("DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 "
                      "-40.00", lines)

    def test_each_record_date_keeps_its_own_line(self):
        """Two trust distributions the books date by their own record
        dates: a sale between them needs the two lines apart."""
        rows = [_row(day="2025-07-02", amt=50.0, tax="2025-06-15",
                     record="2025-06-15"),
                _row(day="2025-07-02", amt=50.0, tax="2025-06-25",
                     record="2025-06-25")]
        _recs, lines, _ = _securities([_ib(self._pays())], rows)
        self.assertEqual(lines, [
            "ADJUST 2025-07-02 09:30:00 SYN.UN.TO CAD -20.00 type=roc "
            "record=2025-06-15",
            "DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 -20.00 "
            "record=2025-06-15 label=distribution",
            "ADJUST 2025-07-02 09:30:00 SYN.UN.TO CAD -20.00 type=roc "
            "record=2025-06-25",
            "DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 -20.00 "
            "record=2025-06-25 label=distribution"])

    def test_booked_roc_counts_on_its_own_record_date(self):
        rows = [_row(day="2025-07-02", amt=50.0, tax="2025-06-15",
                     record="2025-06-15"),
                _row(day="2025-07-02", amt=30.0, tax="2025-06-25",
                     record="2025-06-25"),
                _row(day="2025-07-02", amt=20.0, action="ADJUST",
                     category="roc", tax="2025-06-25", record="2025-06-25")]
        _recs, lines, _ = _securities([_ib(self._pays())], rows)
        self.assertEqual(lines, [
            "ADJUST 2025-07-02 09:30:00 SYN.UN.TO CAD -20.00 type=roc "
            "record=2025-06-15",
            "DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 -20.00 "
            "record=2025-06-15 label=distribution"])

    def test_same_day_payment_without_roc_counts_in_the_day(self):
        """A regular payment with ROC and a special without: the day's
        rows hold both, so the ROC still comes out of income."""
        rows = [_row(day="2025-07-02", amt=50.0),
                _row(day="2025-07-02", amt=50.0)]
        pays = [_pay(ex="2025-06-15", parts=(ROC, ELIG)),
                _pay(ex="2025-06-25", parts=(
                    ("T3", "eligible", "T3: Eligible Dividend Income",
                     50.0),))]
        _recs, lines, _ = _securities([_ib(pays)], rows)
        self.assertEqual(lines, [
            "ADJUST 2025-07-02 09:30:00 SYN.UN.TO CAD -20.00 type=roc",
            "DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 -20.00"])

    def test_one_held_part_tops_up(self):
        rows = [_row(day="2025-07-02", amt=30.0),
                _row(day="2025-07-02", amt=50.0),
                _row(day="2025-07-02", amt=20.0, action="ADJUST",
                     category="roc")]
        _recs, lines, notes = _securities([_ib(self._pays())], rows)
        self.assertEqual(_adjust_total(lines), 20.0)
        self.assertIn("DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 "
                      "-20.00", lines)
        self.assertTrue(any("the suggested line adds the 20.00 rest" in n
                            for n in notes), notes)


class DatedLinesAndBookedRoc(unittest.TestCase):
    """TJ-1051: dated box-42 lines suggest only what the books lack.

    Formerly: test_fix_1029_r7d_slips.DatedLinesAndBookedRoc"""

    def _rows(self, roc_day="2025-12-31", roc=30.0):
        return [_row(day="2025-06-02"), _row(day="2025-12-01"),
                _row(day=roc_day, amt=roc, action="ADJUST", category="roc")]

    def _slip(self, **kw):
        t = {"type": "T3", "boxes": {"49": 60, "42": 40},
             "line": [{"symbol": "SYN.UN.TO", "date": "2025-06-02",
                       "box": "42", "amount": 20},
                      {"symbol": "SYN.UN.TO", "date": "2025-12-01",
                       "box": "42", "amount": 20}]}
        t.update(kw)
        return _slip(t)

    def test_no_more_than_is_missing(self):
        for kw in ({}, {"security": "SYN.UN.TO"}):
            with self.subTest(kw=kw):
                _recs, lines, notes = _securities([self._slip(**kw)],
                                                  self._rows())
                # The row of the 31st counts against the December line:
                # the 10 left is June's.
                self.assertEqual(_adjusts(lines), [
                    "ADJUST 2025-06-02 16:00:00 SYN.UN.TO CAD -10.00 "
                    "type=roc"])
                self.assertTrue(any(
                    "is on none of the dates of the slip's box-42 lines"
                    in n and "2025-12-31" in n for n in notes), notes)

    def test_row_nearer_the_june_line_counts_against_it(self):
        _recs, lines, _ = _securities([self._slip()],
                                      self._rows(roc_day="2025-06-20"))
        self.assertEqual(_adjusts(lines), [
            "ADJUST 2025-12-01 16:00:00 SYN.UN.TO CAD -10.00 type=roc"])

    def test_undated_part_absorbs_the_row_first(self):
        s = self._slip(security="SYN.UN.TO",
                       line=[{"symbol": "SYN.UN.TO", "date": "2025-06-02",
                              "box": "42", "amount": 20}])
        _recs, lines, _ = _securities([s], self._rows())
        self.assertEqual(_adjusts(lines), [
            "ADJUST 2025-06-02 16:00:00 SYN.UN.TO CAD -10.00 type=roc"])

    def test_small_row_leaves_the_lines_whole(self):
        s = self._slip(security="SYN.UN.TO", boxes={"49": 60, "42": 50})
        _recs, lines, _ = _securities([s], self._rows(roc=10.0))
        self.assertEqual(_adjust_total(lines), 40.0)
        self.assertIn("ADJUST 2025-06-02 16:00:00 SYN.UN.TO CAD -20.00 "
                      "type=roc", lines)
        self.assertIn("ADJUST 2025-12-01 16:00:00 SYN.UN.TO CAD -20.00 "
                      "type=roc", lines)

    def test_row_on_a_line_above_its_amount_counts_too(self):
        rows = self._rows(roc_day="2025-06-02", roc=30.0)
        _recs, lines, _ = _securities([self._slip()], rows)
        self.assertEqual(_adjusts(lines), [
            "ADJUST 2025-12-01 16:00:00 SYN.UN.TO CAD -10.00 type=roc"])


class AmendedT3OfAnotherFund(unittest.TestCase):
    """TJ-1050: an amended T3 replaces only its own fund's T3; TJ-0793's
    own tests (rules.ca.slip.test_t3_slip_lines_and_ib_payment_reports)
    keep the T5 and a named fund's replacement.

    Formerly: test_fix_1029_r7d_slips.AmendedT3OfAnotherFund"""

    BOOKS = [_div("ALFA.UN.TO", "2025-06-02", 100.0, issuer_country="CA"),
             _div("BETA.UN.TO", "2025-06-02", 50.0, issuer_country="CA")]

    def _audit(self, toml):
        return SA.audit(_project(self.BOOKS, slips_toml="year = 2025\n"
                                 + toml), CFG)

    def test_two_t3s_without_security(self):
        rep = self._audit(
            '[[slip]]\ntype = "T3"\naccount = "margin"\n'
            'issuer = "Syn Broker"\nboxes = { 49 = 100 }\n'
            '[[slip]]\ntype = "T3"\naccount = "margin"\n'
            'issuer = "Syn Broker"\nstatus = "amended"\n'
            'boxes = { 49 = 50 }\n')
        self.assertEqual(rep["status"], "ok", rep["issues"])
        self.assertFalse(any("is replaced by" in p for p in rep["problems"]),
                         rep["problems"])
        self.assertTrue(any("names no fund" in p for p in rep["problems"]),
                        rep["problems"])

    def test_lines_name_other_funds(self):
        rep = self._audit(
            '[[slip]]\ntype = "T3"\naccount = "margin"\n'
            'issuer = "Syn Broker"\n'
            '[[slip.line]]\nsymbol = "ALFA.UN.TO"\nbox = "49"\namount = 100\n'
            '[[slip]]\ntype = "T3"\naccount = "margin"\n'
            'issuer = "Syn Broker"\nstatus = "amended"\n'
            '[[slip.line]]\nsymbol = "BETA.UN.TO"\nbox = "49"\namount = 50\n')
        self.assertEqual(rep["status"], "ok", rep["issues"])
        self.assertEqual(rep["problems"], [])

    def _t3(self, n, status="", **kw):
        t = {"type": "T3", "issuer": "Syn Broker", "boxes": {"49": 10 * n}}
        if status:
            t["status"] = status
        t.update(kw)
        return _slip(t, n)

    def test_named_fund_is_still_replaced(self):
        kept = SA.apply_amendments(
            [self._t3(1, security="ALFA.UN.TO"),
             self._t3(2, "amended", security="ALFA.UN.TO"),
             self._t3(3, security="BETA.UN.TO")], [], [])
        self.assertEqual([s.where[-2:] for s in kept], ["#2", "#3"])

    def test_lines_of_the_same_fund_are_the_same_slip(self):
        line = {"symbol": "ALFA.UN.TO", "box": "49", "amount": 5}
        problems = []
        kept = SA.apply_amendments(
            [self._t3(1, line=[line]), self._t3(2, "amended", line=[line])],
            problems, [])
        self.assertEqual([s.where[-2:] for s in kept], ["#2"])
        self.assertIn("is replaced by", problems[0])

    def test_security_and_lines_of_one_fund_are_the_same_slip(self):
        line = {"symbol": "ALFA.UN.TO", "box": "49", "amount": 5}
        kept = SA.apply_amendments(
            [self._t3(1, security="ALFA.UN.TO"),
             self._t3(2, "amended", line=[line])], [], [])
        self.assertEqual([s.where[-2:] for s in kept], ["#2"])

    def test_amended_of_two_originals_of_its_fund_cannot_be_told(self):
        problems, nc = [], []
        kept = SA.apply_amendments(
            [self._t3(1, security="ALFA.UN.TO"),
             self._t3(2, security="ALFA.UN.TO"),
             self._t3(3, "amended", security="ALFA.UN.TO")], problems, nc)
        self.assertEqual(len(kept), 3)
        self.assertEqual([x["kind"] for x in nc], ["amended-slip"])

    def test_t5_without_security_is_still_replaced(self):
        t5 = {"type": "T5", "issuer": "Syn Broker", "boxes": {"24": 10}}
        kept = SA.apply_amendments(
            [_slip(t5, 1), _slip(dict(t5, status="amended"), 2)], [], [])
        self.assertEqual([s.where[-2:] for s in kept], ["#2"])


if __name__ == "__main__":
    unittest.main()
