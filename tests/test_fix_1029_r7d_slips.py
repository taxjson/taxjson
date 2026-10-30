"""Fix round 7 (wave D) of 2026-10-29, slip-audit and reconcile-slips:
backlog TJ-1049, TJ-1050, TJ-1051, TJ-1124, TJ-1125, TJ-1091, TJ-1092,
TJ-1093. Synthetic data only: made-up tickers (SYN, ALFA, BETA, ZZQ) and
broker accounts (U1234567)."""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import project, tj, toml
from taxjson.bin.taxjson_reconcile_slips import load_computed
from taxjson.lib import slip_audit as SA
from test_fix_1029_r7b_slipaudit import (
    CFG, _div, _ib, _pay, _project, _report, _row, _securities, _slip)
from test_fix_1029_r7_reconcile import _buyback, _run, _short, _write

ROC = ("T3", "roc", "T3: Return of Capital", 20.0)
ELIG = ("T3", "eligible", "T3: Eligible Dividend Income", 30.0)


def _adjusts(lines):
    return [ln for ln in lines if ln.startswith("ADJUST")]


def _adjust_total(lines):
    return round(sum(-float(ln.split()[5]) for ln in _adjusts(lines)), 2)


class PriorYearIbRoc(unittest.TestCase):
    """TJ-1049, TJ-1124: the per-fund figures take only the components
    of this year's slip (slip_year), as ib_slips does."""

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
    """TJ-1125: two payments of one fund paid the same day."""

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
    """TJ-1051: dated box-42 lines suggest only what the books lack."""

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
    own tests (test_fix_1029_r7b_slipaudit) keep the T5 and a named
    fund's replacement."""

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


class GrantProject(unittest.TestCase):
    """A Canadian project through `tjs run`, then reconcile-slips."""

    SINCE = 2025

    def _project(self, td, files, extra=""):
        root = project(td, "p", files)
        (root / "taxjson.toml").write_text(toml(year=2025).replace(
            "option_grant_timing_since = 2025",
            f"option_grant_timing_since = {self.SINCE}") + extra)
        tj(root, "run", "--no-input")
        return root

    def _reconcile(self, root, text):
        slip = root / "slip.csv"
        slip.write_text(text)
        r = tj(root, "reconcile-slips", str(slip), "--json", check=False)
        return r.returncode, json.loads(r.stdout)


class PriorYearGrantNilExpiry(GrantProject):
    """TJ-1091: written in 2024 for 10, expired at nil in 2025."""

    SINCE = 2024
    OPT = "ZZQ250620C00010000.TO"
    TT = (f"BUYSELL 2024-11-04 10:00:00 {OPT} -1 CAD 0.1 10 0\n"
          f"BUYSELL 2025-06-20 16:00:00 {OPT} 1 CAD 0 0 0\n")

    def test_correct_slip_reconciles_and_empty_slip_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, f"symbol,quantity,proceeds,currency\n{self.OPT},1,10,"
                      "CAD\n")
            self.assertEqual(rc, 0, rep)
            self.assertEqual(rep["rows"][0]["status"], "OK")
            self.assertIn("earlier-year write", rep["rows"][0]["detail"])
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,currency\n")
            self.assertEqual(rc, 1, rep)
            row = rep["rows"][0]
            self.assertEqual(row["status"], "MISSING_FROM_SLIP")
            self.assertIn("would report 10.00", row["detail"])
            self.assertIn("buy-back or expiry", row["detail"])

    def test_this_years_write_keeps_its_own_reading(self):
        """TJ-0781 through the engine: no earlier-year premium."""
        tt = (f"BUYSELL 2025-01-06 10:00:00 {self.OPT} -1 CAD 0.1 10 0\n"
              f"BUYSELL 2025-06-20 16:00:00 {self.OPT} 1 CAD 0 0 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": tt})
            rc, rep = self._reconcile(
                root, f"symbol,quantity,proceeds,currency\n{self.OPT},1,10,"
                      "CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"][0]["detail"], "")

    def test_wrong_quantity_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, f"symbol,quantity,proceeds,currency\n{self.OPT},2,10,"
                      "CAD\n")
        self.assertEqual(rc, 1, rep)
        self.assertIn("quantity off by +1.0000", rep["rows"][0]["detail"])

    def test_gains_stay_nil_and_no_close_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            doc = json.loads((root / "work" / "margin_gains.json")
                             .read_text())
        self.assertEqual(doc["transactions"], [])
        self.assertEqual(doc["grant_nil_closes"][0]["grant_closed"],
                         {"2024": {"units": 1.0, "premium": 10.0}})

    def test_no_key_when_nothing_expired_at_nil(self):
        tt = (f"BUYSELL 2024-11-04 10:00:00 {self.OPT} -1 CAD 0.1 10 0\n"
              f"BUYSELL 2025-03-03 10:00:00 {self.OPT} 1 CAD 0.01 1 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": tt})
            doc = json.loads((root / "work" / "margin_gains.json")
                             .read_text())
        self.assertNotIn("grant_nil_closes", doc)

    def test_two_accounts_each_keep_their_own(self):
        tt = (f"BUYSELL 2025-02-03 10:00:00 ZZQ260116C00010000.TO -1 CAD "
              f"0.3 30 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(
                td, {"margin/t.tt": self.TT,
                     "m2/t.tt": tt},
                extra='[accounts.m2]\ntype = "taxable"\n')
            one = json.loads((root / "work" / "margin_gains_wash.json")
                             .read_text())
            two = json.loads((root / "work" / "m2_gains_wash.json")
                             .read_text())
        self.assertEqual(len(one["grant_nil_closes"]), 1)
        self.assertNotIn("grant_nil_closes", two)


class NilCloseRecords(unittest.TestCase):
    """load_computed on a gains file's `grant_nil_closes` (TJ-1091)."""

    OPT = "SYN250620C00001000.TO"

    def _nil(self, closed, date="2025-06-20"):
        return {"date": date, "date_settle": date, "symbol": self.OPT,
                "account": "margin", "grant_closed": closed}

    def test_this_years_nil_close_is_counted_once(self):
        doc = {"transactions": [_write("2025-01-02", 10.0, symbol=self.OPT)],
               "grant_nil_closes": [self._nil(
                   {"2025": {"units": 1.0, "premium": 10.0}})]}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,currency\n"
                              f"{self.OPT},1,10,CAD\n", doc=doc)
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"][0]["detail"], "")

    def test_another_years_record_is_out(self):
        doc = {"transactions": [],
               "grant_nil_closes": [self._nil(
                   {"2023": {"units": 1.0, "premium": 10.0}},
                   date="2024-06-20")]}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,currency\n",
                              doc=doc)
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"], [])


class PremiumAcrossAccounts(unittest.TestCase):
    """TJ-1092: a buy-back closes its own account's writes first. TJ-0779's
    own tests (one account: earliest closed, latest open) still hold."""

    OPT = "ZZQ260116C00010000.TO"

    def _write(self, date, premium, account, **kw):
        return dict(_write(date, premium, symbol=self.OPT, **kw),
                    account=account)

    def _close(self, date, paid, closed, account):
        return dict(_buyback(date, paid, closed, symbol=self.OPT),
                    account=account)

    def _rep(self, ents, row):
        with tempfile.TemporaryDirectory() as td:
            return _run(td, "symbol,quantity,proceeds,cost,currency\n"
                        + row, ents)

    def test_open_write_is_the_other_accounts(self):
        ents = [self._write("2025-01-06", 10.0, "a1"),
                self._write("2025-02-03", 30.0, "a2"),
                self._close("2025-03-03", 5.0, {"2025": 30.0}, "a2")]
        rc, rep, _ = self._rep(ents, f"{self.OPT},1,30,5,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertIn("10.00 premium of written option(s) still open",
                      rep["rows"][0]["detail"])

    def test_commission_stays_with_the_open_write(self):
        ents = [self._write("2025-01-06", 10.0, "a1", commission=1.0),
                self._write("2025-02-03", 30.0, "a2", commission=2.0),
                self._close("2025-03-03", 5.0, {"2025": 28.0}, "a2")]
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": ents}))
            rec = load_computed([g], 2025)["ZZQ260116C00010000"]
        self.assertAlmostEqual(rec["open_grant"], 10.0)
        self.assertAlmostEqual(rec["open_grant_net"], 9.0)

    def test_contract_moved_closes_the_other_accounts_lot(self):
        """The closing account's lots ran out: as the engine, another's
        (TJ-0718's order)."""
        ents = [self._write("2025-01-06", 10.0, "a1"),
                self._write("2025-02-03", 30.0, "a1"),
                self._close("2025-03-03", 5.0, {"2025": 10.0}, "a2")]
        rc, rep, _ = self._rep(ents, f"{self.OPT},1,10,5,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertIn("30.00 premium of written option(s) still open",
                      rep["rows"][0]["detail"])

    def test_account_with_one_of_two_writes_closed(self):
        ents = [self._write("2025-01-06", 10.0, "a1"),
                self._write("2025-01-20", 20.0, "a1"),
                self._write("2025-02-03", 30.0, "a2"),
                self._close("2025-03-03", 5.0, {"2025": 10.0}, "a1")]
        rc, rep, _ = self._rep(ents, f"{self.OPT},1,10,5,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertIn("50.00 premium of written option(s) still open",
                      rep["rows"][0]["detail"])


class PremiumAcrossAccountsThroughTheEngine(GrantProject):
    """TJ-1092's repro, with the engine's own rows."""

    OPT = "ZZQ260116C00010000.TO"

    def test_pooled_slip_reconciles(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(
                td,
                {"margin/t.tt":
                 f"BUYSELL 2025-01-06 10:00:00 {self.OPT} -1 CAD 0.1 10 0\n",
                 "m2/t.tt":
                 f"BUYSELL 2025-02-03 10:00:00 {self.OPT} -1 CAD 0.3 30 0\n"
                 f"BUYSELL 2025-03-03 10:00:00 {self.OPT} 1 CAD 0.05 5 0\n"},
                extra='[accounts.m2]\ntype = "taxable"\n')
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,cost,currency\n"
                      f"{self.OPT},1,30,5,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertIn("10.00 premium of written option(s) still open",
                      rep["rows"][0]["detail"])


class FuturesCloses(GrantProject):
    """TJ-1093: a plain future's close reads as form-export shows it: a
    gain as proceeds, a loss as ACB."""

    TT = ("BUYSELL 2025-08-11 10:00:00 F:SXFZ5.TO -1 CAD 1010 202000 0 x200\n"
          "BUYSELL 2025-09-03 10:00:00 F:SXFZ5.TO 1 CAD 1000 200000 0 x200\n"
          "BUYSELL 2025-08-11 10:00:00 F:SXFH6.TO 1 CAD 1010 202000 0 x200\n"
          "BUYSELL 2025-09-03 10:00:00 F:SXFH6.TO -1 CAD 1000 200000 0 x200"
          "\n")

    def test_the_schedule_3_shape_reconciles(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,cost,currency\n"
                      "F:SXFZ5.TO,1,2000,0,CAD\nF:SXFH6.TO,1,0,2000,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertEqual([r["status"] for r in rep["rows"]], ["OK", "OK"])
        self.assertEqual([r["detail"] for r in rep["rows"]], ["", ""])

    def test_a_wrong_figure_is_still_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,cost,currency\n"
                      "F:SXFZ5.TO,1,1500,0,CAD\nF:SXFH6.TO,1,0,2000,CAD\n")
        self.assertEqual(rc, 1, rep)
        self.assertEqual({r["symbol"].split(".")[0]: r["status"]
                          for r in rep["rows"]},
                         {"F:SXFZ5": "MISMATCH", "F:SXFH6": "OK"})

    def test_a_loss_without_a_slip_row_is_no_expired_option(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,cost,currency\n"
                      "F:SXFZ5.TO,1,2000,0,CAD\n")
        self.assertEqual(rc, 1, rep)
        row = [r for r in rep["rows"] if r["symbol"].startswith("F:SXFH6")][0]
        self.assertEqual(row["status"], "MISSING_FROM_SLIP")

    def test_commission_is_inside_the_profit_or_loss(self):
        e = {"date": "2025-09-03", "date_settle": "2025-09-03",
             "symbol": "F:SXFH6.TO", "qty": -1, "proceeds": -2010.0,
             "cost": 0.0, "gain": -2010.0, "raw_gain": -2010.0,
             "commission": 10.0, "fee": 0.0, "direction": "LONG"}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,cost,currency\n"
                              "F:SXFH6.TO,1,0,2010,CAD\n", [e])
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"][0]["detail"], "")

    def test_a_denied_loss_shows_before_the_denial(self):
        """Schedule 3 shows the P/L before any superficial-loss denial."""
        e = {"date": "2025-09-03", "date_settle": "2025-09-03",
             "symbol": "F:SXFH6.TO", "qty": -1, "proceeds": -2000.0,
             "cost": 0.0, "gain": 0.0, "raw_gain": -2000.0,
             "disallowed_amount": 2000.0, "direction": "LONG"}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,cost,currency\n"
                              "F:SXFH6.TO,1,0,2000,CAD\n", [e])
        self.assertEqual(rc, 0, rep)

    def test_us_section_1256_future_keeps_its_reading(self):
        """It goes on Form 6781, not Form 8949: no T5008 shape."""
        e = {"date": "2025-09-03", "date_settle": "2025-09-03",
             "symbol": "F:SXFH6.US", "qty": -1, "proceeds": -2000.0,
             "cost": 0.0, "gain": -2000.0, "direction": "LONG"}
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [e]}))
            rec = load_computed([g], 2025, "trade", "usa")["F:SXFH6"]
            ca = load_computed([g], 2025)["F:SXFH6"]
        self.assertAlmostEqual(rec["proceeds_gross"], -2000.0)
        self.assertAlmostEqual(ca["cost"], 2000.0)

    def test_option_on_a_future_is_not_a_plain_future(self):
        e = _short(symbol="F:SXF251219C00001000.TO", qty=1, proceeds=-5.0,
                   cost=-12.0, date="2025-03-03", grant=False)
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,cost,currency\n"
                              "F:SXF251219C00001000.TO,1,12,5,CAD\n", [e])
        self.assertEqual(rc, 0, rep)


if __name__ == "__main__":
    unittest.main()
