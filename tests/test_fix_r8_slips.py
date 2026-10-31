"""Fix round 8, slips: backlog TJ-1151, TJ-1275, TJ-1276, TJ-1277, TJ-1278,
TJ-1335, TJ-1336, TJ-1339. Synthetic data only: made-up tickers (SYN, FND,
ZZA, ZZB), issuers and broker accounts."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _cra_pdf as CRA
from _qa_project import tj
from tax_rules import rule
from taxjson.lib import cra_slips as CS
from taxjson.lib import slip_audit as SA
import test_fix_1029_r7b_slipaudit as B
from test_fix_1029_r7c_slips import (
    CFG, QT, _brow, _div, _interest, _line, _project)
from test_fix_1029_r7e_slips import (
    _cli_project, _existing, _groups, _place, _qt, _qt_buy, _qt_div)


class UnnamedT3NegativePayerBesideAPositiveOne(unittest.TestCase):
    """TJ-1339: each other payer holds its own income of a T3 of no named
    fund; a net negative one does not net against a positive one."""

    T3 = ('year = 2025\n[[slip]]\ntype = "T3"\naccount = "margin"\n'
          'issuer = "Synthetic Fund"\nboxes = { 49 = 100 }\n')

    def _status(self, *others, div=40.0):
        rows = [_div("SYN.TO", "2025-06-02", div, source="y.csv"),
                _interest("2025-06-02", 30.0, source="y.csv")]
        for i, amt in enumerate(others):
            rows.append(_div(f"ZZ{i}.TO", "2025-06-02", amt,
                             source=f"x{i}.csv"))
        return _line(SA.audit(_project(rows, self.T3), CFG),
                     "interest")["status"]

    @rule("CA-SLIP-01")
    def test_the_positive_payer_alone_holds_it(self):
        # z holds the T3's 100; y keeps 40 + 30.
        self.assertEqual(self._status(-50.0, 100.0), "differs")

    @rule("CA-SLIP-01")
    def test_without_the_negative_payer_the_same(self):
        self.assertEqual(self._status(100.0), "differs")

    @rule("CA-SLIP-01")
    def test_two_positive_payers_still_add_up(self):
        # 60 + 40 hold the T3's 100; one of them alone would not.
        self.assertEqual(self._status(60.0, 40.0), "differs")
        self.assertEqual(self._status(60.0, 5.0, div=45.0), "under-50")

    @rule("CA-SLIP-01")
    def test_what_no_payer_holds_still_comes_off(self):
        # Only -50 elsewhere: all 100 off y's 110 leaves 10 + 30.
        self.assertEqual(self._status(-50.0, div=110.0), "under-50")


def _sec(slips, rows):
    cgd, tt, notes = [], {}, []
    SA._securities("margin", "margin", slips, rows, 2025, 1.0, cgd, tt, notes,
                   rows, sales={})
    return cgd, tt.get("margin", []), notes


class Box18OfTwoSameDayPayments(unittest.TestCase):
    """TJ-1151: IB's payments of one book date are compared with that date's
    entry as one total, and draft one entry."""

    @staticmethod
    def _p(ex, day="2025-07-02", cg=20.0):
        return B._pay(symbol="SYN.TO", day=day, ex=ex,
                      parts=(("T5", "cg_div", "T5: Capital Gains", cg),
                             ("T5", "eligible", "T5: Eligible", 50.0 - cg)))

    def _two(self, booked=0.0):
        rows = [B._row("SYN.TO", "2025-07-02", 50.0),
                B._row("SYN.TO", "2025-07-02", 51.0)]
        rows[0].cg_part = booked
        return _sec([B._ib([self._p("2025-06-15"), self._p("2025-06-25")])],
                    rows)

    @rule("CA-SLIP-03")
    def test_an_entry_of_one_of_them_is_said(self):
        cgd, _tt, notes = self._two(booked=20.0)
        self.assertEqual(cgd, [])
        self.assertTrue(any("to amount = 40.00" in n for n in notes), notes)

    @rule("CA-SLIP-03")
    def test_with_no_entry_one_entry_of_the_total(self):
        cgd, _tt, _notes = self._two()
        self.assertEqual([(c["date"], c["amount"]) for c in cgd],
                         [("2025-07-02", 40.0)])

    @rule("CA-SLIP-03")
    def test_an_entry_of_the_total_is_quiet(self):
        cgd, _tt, notes = self._two(booked=40.0)
        self.assertEqual((cgd, notes), ([], []))

    @rule("CA-SLIP-03")
    def test_two_pay_dates_keep_two_entries(self):
        rows = [B._row("SYN.TO", "2025-03-03", 50.0),
                B._row("SYN.TO", "2025-07-02", 50.0)]
        rep = [self._p("2025-02-15", day="2025-03-03"), self._p("2025-06-15")]
        cgd, _tt, _notes = _sec([B._ib(rep)], rows)
        self.assertEqual([(c["date"], c["amount"]) for c in cgd],
                         [("2025-03-03", 20.0), ("2025-07-02", 20.0)])

    @rule("CA-SLIP-03")
    def test_one_payment_as_before(self):
        rows = [B._row("SYN.TO", "2025-07-02", 50.0)]
        rows[0].cg_part = 10.0
        cgd, _tt, notes = _sec([B._ib([self._p("2025-06-15")])], rows)
        self.assertEqual(cgd, [])
        self.assertTrue(any("to amount = 20.00" in n for n in notes), notes)

    @rule("CA-SLIP-03")
    def test_a_payment_in_lieu_stays_out(self):
        pil = B._pay(symbol="SYN.TO", ex="2025-06-25", pil=True,
                     parts=(("T5", "cg_div", "T5: Capital Gains", 7.0),))
        cgd, _tt, notes = _sec(
            [B._ib([self._p("2025-06-15"), pil])],
            [B._row("SYN.TO", "2025-07-02", 50.0)])
        self.assertEqual([c["amount"] for c in cgd], [20.0])
        self.assertTrue(any("payment in lieu" in n for n in notes), notes)


class Box18BareRowBesideAListedOne(unittest.TestCase):
    """TJ-1335: a bare row beside a Canadian listing's drafts one entry, the
    bare entry taking both; slip-audit still runs once it is applied."""

    @staticmethod
    def _slips(*pairs):
        return "year = 2025\n" + "".join(
            f'[[slip]]\ntype = "T5"\naccount = "margin"\nissuer = "P{i}"\n'
            f'currency = "CAD"\nsecurity = "{sym}"\nboxes = {{ 18 = {amt} }}\n'
            for i, (sym, amt) in enumerate(pairs))

    def _draft(self, rows, *pairs):
        rep = SA.audit(_project(rows, self._slips(*pairs)), CFG)
        return sorted((c["symbol"], c["amount"]) for c in
                      rep["suggestions"]["capital_gains_dividends"])

    @rule("CA-SLIP-03")
    def test_either_order_of_the_slips(self):
        rows = [_div("SYN.TO", "2025-06-02", 200.0),
                _div("SYN", "2025-06-03", 200.0)]
        for pairs in ((("SYN.TO", 20), ("SYN.US", 5)),
                      (("SYN.US", 5), ("SYN.TO", 20))):
            self.assertEqual(self._draft(rows, *pairs), [("SYN", 25.0)])

    @rule("CA-SLIP-03")
    def test_a_bare_row_beside_a_foreign_listing_stays_apart(self):
        got = self._draft([_div("SYN", "2025-06-02", 200.0),
                           _div("SYN.US", "2025-06-03", 200.0)],
                          ("SYN.TO", 20), ("SYN.US", 5))
        self.assertEqual(got, [("SYN", 20.0), ("SYN.US", 5.0)])

    @rule("CA-SLIP-03")
    def test_the_draft_applies(self):
        tt = ("BUYSELL 2025-01-06 10:00:00 SYN 100 CAD 10 1000 0\n"
              "BUYSELL 2025-01-06 10:00:00 SYN.TO 100 CAD 10 1000 0\n"
              "DIVIDEND 2025-06-02 09:30:00 SYN 100 CAD 2 200.00\n"
              "DIVIDEND 2025-06-03 09:30:00 SYN.TO 100 CAD 2 200.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = _cli_project(td, {
                "margin/m.tt": tt,
                "slips/slips.toml": self._slips(("SYN.TO", 20),
                                                ("SYN.US", 5))})
            rep = json.loads(tj(root, "slip-audit", "--json",
                                check=False).stdout)
            cgd = rep["suggestions"]["capital_gains_dividends"]
            self.assertEqual([(c["symbol"], c["amount"]) for c in cgd],
                             [("SYN", 25.0)])
            with (root / "taxjson.toml").open("a", encoding="utf-8") as f:
                f.write("".join(c["toml"] + "\n" for c in cgd))
            tj(root, "run", "--no-input")
            r = tj(root, "slip-audit", "--json", check=False)
            self.assertNotEqual(r.returncode, 2, r.stdout + r.stderr)
            rep = json.loads(r.stdout)
            self.assertEqual(rep["suggestions"]["capital_gains_dividends"],
                             [])


class RocOfTwoPayDatesOnOneRow(unittest.TestCase):
    """TJ-1275: IB's return of capital of two pay dates that resolve to one
    dividend row adds up, on that row."""

    @staticmethod
    def _p(day, ex, roc=20.0, inc=30.0):
        return B._pay(day=day, ex=ex,
                      parts=(("T3", "roc", "T3: Return of Capital", roc),
                             ("T3", "eligible", "T3: Eligible", inc)))

    def _lines(self, rows, *pays):
        return _sec([B._ib(pays)], rows)[1]

    @rule("CA-SLIP-03")
    def test_two_pay_dates_one_row(self):
        got = self._lines([B._row(day="2025-07-03", amt=100.0)],
                          self._p("2025-07-02", "2025-06-15"),
                          self._p("2025-07-04", "2025-06-20"))
        # The row holds both payments: the return of capital comes out of it.
        self.assertEqual(got, [
            "ADJUST 2025-07-03 09:30:00 SYN.UN.TO CAD -40.00 type=roc",
            "DIVIDEND 2025-07-03 09:30:00 SYN.UN.TO 0 CAD 0 -40.00"])

    @rule("CA-SLIP-03")
    def test_a_booked_part_leaves_the_rest(self):
        got = self._lines([B._row(day="2025-07-03", amt=60.0),
                           B._row(day="2025-07-03", amt=20.0, action="ADJUST",
                                  category="roc")],
                          self._p("2025-07-02", "2025-06-15"),
                          self._p("2025-07-04", "2025-06-20"))
        self.assertIn("ADJUST 2025-07-03 09:30:00 SYN.UN.TO CAD -20.00 "
                      "type=roc", got)

    @rule("CA-SLIP-03")
    def test_each_pay_dates_own_row_keeps_its_line(self):
        got = self._lines([B._row(day="2025-07-02", amt=50.0),
                           B._row(day="2025-07-04", amt=50.0)],
                          self._p("2025-07-02", "2025-06-15"),
                          self._p("2025-07-04", "2025-06-20"))
        self.assertEqual(got, [
            "ADJUST 2025-07-02 09:30:00 SYN.UN.TO CAD -20.00 type=roc",
            "DIVIDEND 2025-07-02 09:30:00 SYN.UN.TO 0 CAD 0 -20.00",
            "ADJUST 2025-07-04 09:30:00 SYN.UN.TO CAD -20.00 type=roc",
            "DIVIDEND 2025-07-04 09:30:00 SYN.UN.TO 0 CAD 0 -20.00"])

    @rule("CA-SLIP-03")
    def test_no_row_keeps_two_lines(self):
        got = self._lines([], self._p("2025-07-02", "2025-06-15"),
                          self._p("2025-07-04", "2025-06-20"))
        self.assertEqual(len(got), 2)


class RocOfT3sOfTwoListings(unittest.TestCase):
    """TJ-1276: each listing's T3 drafts its own return of capital."""

    SLIPS = ('year = 2025\n'
             '[[slip]]\ntype = "T3"\naccount = "margin"\nissuer = "A"\n'
             'currency = "CAD"\nsecurity = "SYN.TO"\n'
             'boxes = { 49 = 60, 42 = 40 }\n'
             '[[slip]]\ntype = "T3"\naccount = "margin"\nissuer = "B"\n'
             'currency = "CAD"\nsecurity = "SYN.US"\n'
             'boxes = { 25 = 90, 42 = 10 }\n')

    def _lines(self, rows, slips=None):
        rep = SA.audit(_project(rows, slips or self.SLIPS), CFG)
        return [ln for f in rep["suggestions"]["tt_lines"]
                for ln in f["lines"]]

    @rule("CA-SLIP-03")
    def test_each_listing_its_own(self):
        got = self._lines([_div("SYN.TO", "2025-06-02", 100.0),
                           _div("SYN.US", "2025-06-03", 100.0)])
        self.assertEqual(got, [
            "ADJUST 2025-06-02 16:00:00 SYN.TO CAD -40.00 type=roc",
            "DIVIDEND 2025-06-02 16:00:00 SYN.TO 0 CAD 0 -40.00",
            "ADJUST 2025-06-03 16:00:00 SYN.US CAD -10.00 type=roc",
            "DIVIDEND 2025-06-03 16:00:00 SYN.US 0 CAD 0 -10.00"])

    @rule("CA-SLIP-03")
    def test_the_other_listings_equal_row_is_not_this_ones(self):
        # SYN.TO's booked 10 is against its own T3, not "already held" for
        # SYN.US's 10.
        slips = [B._slip({"type": "T3", "security": "SYN.TO", "issuer": "A",
                          "boxes": {"49": 60, "42": 10}}),
                 B._slip({"type": "T3", "security": "SYN.US", "issuer": "B",
                          "boxes": {"25": 90, "42": 10}}, 2)]
        rows = [B._row("SYN.TO", "2025-06-02", 60.0),
                B._row("SYN.TO", "2025-06-02", 10.0, action="ADJUST",
                       category="roc"),
                B._row("SYN.US", "2025-06-03", 100.0)]
        _cgd, got, _notes = _sec(slips, rows)
        self.assertIn("ADJUST 2025-06-03 16:00:00 SYN.US CAD -10.00 type=roc",
                      got)
        self.assertFalse(any("SYN.TO" in ln for ln in got), got)

    @rule("CA-SLIP-03")
    def test_rows_of_no_listing_stay_one_draft(self):
        got = self._lines([_div("SYN", "2025-06-02", 200.0)])
        self.assertEqual(sum(float(ln.split()[5]) for ln in got
                             if ln.startswith("ADJUST")), -50.0)
        self.assertEqual(len([ln for ln in got if ln.startswith("ADJUST")]), 1)

    @rule("CA-SLIP-03")
    def test_a_listing_with_no_row_stays_one_draft(self):
        # Nothing to place SYN.US's on: not a line on a security not held.
        got = self._lines([_div("SYN.TO", "2025-06-02", 100.0)])
        self.assertEqual(got[0],
                         "ADJUST 2025-06-02 16:00:00 SYN.TO CAD -50.00 "
                         "type=roc")

    @rule("CA-SLIP-03")
    def test_one_listings_t3_alone_as_before(self):
        one = self.SLIPS.split('[[slip]]\ntype = "T3"\naccount = "margin"\n'
                               'issuer = "B"')[0]
        got = self._lines([_div("SYN.TO", "2025-06-02", 100.0),
                           _div("SYN.US", "2025-06-03", 100.0)], one)
        self.assertEqual(got, [
            "ADJUST 2025-06-02 16:00:00 SYN.TO CAD -40.00 type=roc",
            "DIVIDEND 2025-06-02 16:00:00 SYN.TO 0 CAD 0 -40.00"])


class NoAccountHasItsKindSaidOnlyWhenTrue(unittest.TestCase):
    """TJ-1278: the slip stays unplaced, and the message no longer says no
    account has its kind when another one does."""

    A = [_brow("a" * 10, "ZZA.TO", 5.0, src="a.csv")]
    B = [_brow("b" * 10, "ZZB.TO", 1000.0, src="b.csv"),
         _brow("b" * 10, "", 900.0, cat="interest", src="b.csv")]

    @rule("CA-SLIP-04")
    def test_the_other_account_holds_it(self):
        pl = _place({"13": 900.0}, _groups(self.A, self.B))
        self.assertIsNone(pl.account)
        self.assertNotIn("no Questrade broker account", pl.how)
        self.assertIn("cannot be told", pl.how)
        self.assertIn("another has some", pl.how)

    @rule("CA-SLIP-04")
    def test_naming_the_account(self):
        pl = _place({"13": 900.0}, _groups(self.A, self.B), account="margin")
        self.assertEqual((pl.account, pl.key), ("margin", ""))
        self.assertNotIn("no Questrade broker account", pl.how)

    @rule("CA-SLIP-04")
    def test_no_account_holds_it_is_still_said(self):
        pl = _place({"13": 900.0},
                    _groups(self.A, [_brow("b" * 10, "ZZB.TO", 1000.0,
                                           src="b.csv")]))
        self.assertIsNone(pl.account)
        self.assertIn("no Questrade broker account in the books has its "
                      "interest in 2025", pl.how)

    @rule("CA-SLIP-04")
    def test_the_account_of_its_kind_is_still_placed(self):
        pl = _place({"24": 1000.0, "13": 900.0}, _groups(self.A, self.B))
        self.assertEqual(pl.group.hashes, ("b" * 10,))


class KeylessSlipBesideAKeyedTableOfMoreBoxes(unittest.TestCase):
    """TJ-1336: a slip of no key sharing a box with a keyed table that has
    boxes it lacks is listed: never moved under the key, nor called the
    original of that table's amended slip."""

    TABLE = {"24": 100.0, "13": 5.0}

    @staticmethod
    def _pl(boxes, status="original"):
        return CS.Placement(CS.CraSlip("b.pdf", 2025, "T5", status, QT,
                                       boxes=boxes), account="margin")

    @rule("CA-SLIP-04")
    def test_amended_is_listed_not_moved_under_the_key(self):
        plan = CS.plan_import([(self._pl({"13": 900.0}, "amended"), "")],
                              [_existing(boxes=self.TABLE)])
        self.assertEqual((plan.add, plan.replace), ([], []))
        self.assertIn("cannot be told", plan.notes[0])
        self.assertIn("#1", plan.notes[0])

    @rule("CA-SLIP-04")
    def test_original_beside_an_amended_table_is_listed(self):
        plan = CS.plan_import(
            [(self._pl({"13": 900.0}), "")],
            [_existing(boxes=self.TABLE, status="amended")])
        self.assertEqual(plan.add, [])
        self.assertNotIn("holds its amended slip", plan.notes[0])
        self.assertIn("cannot be told", plan.notes[0])

    @rule("CA-SLIP-04")
    def test_original_beside_an_original_table_is_added_as_before(self):
        pl = self._pl({"13": 900.0})
        plan = CS.plan_import([(pl, "")], [_existing(boxes=self.TABLE)])
        self.assertEqual(plan.add, [(pl, "")])
        self.assertEqual(plan.notes, [])

    @rule("CA-SLIP-04")
    def test_every_box_on_the_slip_still_replaces_and_keeps_the_key(self):
        plan = CS.plan_import(
            [(self._pl({"24": 90.0, "13": 5.0}, "amended"), "")],
            [_existing(boxes=self.TABLE)])
        self.assertEqual(plan.replace[0][2], "k" * 10)
        self.assertEqual(plan.notes, [])

    @rule("CA-SLIP-04")
    def test_a_table_it_fits_beside_it_is_still_the_one(self):
        fits = _existing(index=2, boxes={"13": 5.0}, keys=("m" * 10,))
        plan = CS.plan_import([(self._pl({"13": 900.0}, "amended"), "")],
                              [_existing(boxes=self.TABLE), fits])
        self.assertEqual([e.index for e, _pl, _k in plan.replace], [2])

    @rule("CA-SLIP-04")
    def test_a_keyed_slip_and_a_table_of_no_key_as_before(self):
        pl = self._pl({"13": 900.0}, "amended")
        plan = CS.plan_import([(pl, "k" * 10)], [_existing(boxes=self.TABLE)])
        self.assertEqual(plan.replace[0][2], "k" * 10)
        plan = CS.plan_import([(pl, "")],
                              [_existing(boxes=self.TABLE, keys=())])
        self.assertEqual(plan.replace[0][2], "")

    @rule("CA-SLIP-04")
    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
    def test_the_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = _cli_project(td, {
                "margin/a.csv": _qt("99900001", _qt_buy("ZZA.TO"),
                                    _qt_div("ZZA.TO", 100.0)),
                "margin/b.csv": _qt("99900002", _qt_buy("ZZB.TO"),
                                    _qt_div("ZZB.TO", 200.0))})
            a = CRA.t5(Path(td) / "a.pdf", ["QUESTRADE, INC."],
                       {"24": "100.00", "13": "5.00"})
            b = CRA.t5(Path(td) / "b.pdf", ["QUESTRADE, INC."],
                       {"13": "900.00"}, status="amended")
            tj(root, "slip-audit", "--import-cra", str(a), "--write")
            sf = root / "inputs" / "slips" / "slips.toml"
            before = sf.read_text(encoding="utf-8")
            r = tj(root, "slip-audit", "margin", "--import-cra", str(b),
                   "--write", check=False)
            self.assertIn("cannot be told", r.stdout + r.stderr)
            self.assertEqual(sf.read_text(encoding="utf-8"), before)


class KeyedSlipBesideAnUnkeyedTableOfItsFigures(unittest.TestCase):
    """TJ-1277: the same figures in a table of no broker account, the slip
    now placed under one: said, never added beside it."""

    @staticmethod
    def _pl(boxes=None, status="original"):
        return CS.Placement(CS.CraSlip("t5.pdf", 2025, "T5", status, QT,
                                       boxes=boxes or {"13": 900.0}),
                            account="margin")

    TABLE = {"13": 900.0}

    @rule("CA-SLIP-04")
    def test_it_is_said_and_not_added(self):
        plan = CS.plan_import([(self._pl(), "m" * 10)],
                              [_existing(boxes=self.TABLE, keys=())])
        self.assertEqual(plan.add, [])
        self.assertIn("names no broker account", plan.notes[0])
        self.assertIn("[[slip]] #1", plan.notes[0])

    @rule("CA-SLIP-04")
    def test_other_figures_are_added_as_before(self):
        pl = self._pl({"13": 901.0})
        plan = CS.plan_import([(pl, "m" * 10)],
                              [_existing(boxes=self.TABLE, keys=())])
        self.assertEqual(plan.add, [(pl, "m" * 10)])
        self.assertEqual(plan.notes, [])

    @rule("CA-SLIP-04")
    def test_a_table_of_its_own_key_is_already_there(self):
        plan = CS.plan_import([(self._pl(), "m" * 10)],
                              [_existing(boxes=self.TABLE, keys=("m" * 10,))])
        self.assertIn("already in slips.toml", plan.notes[0])

    @rule("CA-SLIP-04")
    def test_a_table_of_another_key_is_added_as_before(self):
        pl = self._pl()
        plan = CS.plan_import([(pl, "m" * 10)],
                              [_existing(boxes=self.TABLE)])
        self.assertEqual(plan.add, [(pl, "m" * 10)])

    @rule("CA-SLIP-04")
    def test_a_second_slip_of_equal_figures_is_added(self):
        # One table stands for one slip of the import (TJ-0997).
        a, b = self._pl(), self._pl()
        plan = CS.plan_import([(a, "m" * 10), (b, "n" * 10)],
                              [_existing(boxes=self.TABLE, keys=())])
        self.assertEqual(plan.add, [(b, "n" * 10)])
        self.assertEqual(len(plan.notes), 1)


if __name__ == "__main__":
    unittest.main()
