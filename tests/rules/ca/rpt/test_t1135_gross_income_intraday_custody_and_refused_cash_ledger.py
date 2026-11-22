"""Fixes of 2026-10-29, round 7 (wave B), group taxtext: the .tt
conversion keeps a bond coupon's property (backlog TJ-0856), an income
row's paid date on a settle-basis export (TJ-0862), the futures
settlement facts (TJ-0662), and sizes a .tt option line by the
project's ticker.map MULT (TJ-0864); the T1135 counts the gross income
of a foreign property, not a short's compensation paid (TJ-0837), an
intraday foreign custody holding in the threshold test (TJ-0646), a
cash ledger refused with no row to hold the refusal as unsettled
(TJ-0657), and the standalone tool refuses a cash document of another
year or base currency (TJ-0772). One test class per backlog item, each
named for it.

Every fixture is SYNTHETIC: invented tickers (SYN*, ZZ*), invented
account names, small amounts.

Formerly: test_fix_1029_r7b_taxtext
"""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.bin import taxjson_t1135 as T
from taxjson.lib import t1135_cash as TC
from taxjson.model.transaction import TaxTransaction


def _row(i, date, sym, q, net, action="BUYSELL", time="10:00:00",
         acct="a"):
    return TaxTransaction(id=i, action=action, date=date, date_settle=date,
                          time=time, symbol=sym, quantity=q,
                          net_amount=net, gross_amount=0.0,
                          price=abs(net / q) if q else 0.0,
                          currency="CAD", account=acct)


def _report(rows, gains=True, **kw):
    """build_report over `rows` (one base file of account `a`), with the
    gains file `tjs run` writes for 2025 (the INCOME column)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a_base.json"
        p.write_text(json.dumps({"transactions": [r.to_dict()
                                                  for r in rows]}))
        gpaths = []
        with contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(T, "_DETAILS", True):
            if gains:
                g = Path(td) / "a_gains.json"
                g.write_text(json.dumps(run_gains(
                    copy.deepcopy(list(rows)), [], [], GainsRequest(
                        country="canada", taxable=True, year=2025,
                        phantom_hint=False))))
                gpaths.append(g)
            return T.build_report([p], gpaths, 2025, {}, "CAD",
                                  today="2026-10-29", **kw)


def _prop(rep, sym):
    return next(r for r in rep["properties"] if r["symbol"] == sym)


# ===================== TJ-0837 compensation paid on a short is not income
class TestTJ0837ShortCompensationIsNotGrossIncome(unittest.TestCase):
    """TJ-0837: 100 of dividends on SYN.US held long, the position sold,
    then shorted and 30 of dividend compensation paid: the T1135
    INCOME of SYN.US read 70, where the gross income the property
    earned is 100.

    Formerly: test_fix_1029_r7b_taxtext.TestTJ0837ShortCompensationIsNotGrossIncome"""

    def _rows(self, pil):
        return [
            _row("o1", "2025-01-02", "OTHER.US", 1, 100001.0,
                 time="09:00:00"),
            _row("s1", "2025-01-02", "SYN.US", 100, 1000.0),
            _row("d1", "2025-02-03", "SYN.US", 0, 100.0, "DIVIDEND",
                 "12:00:00"),
            _row("s2", "2025-03-03", "SYN.US", -100, 1000.0),
            _row("s3", "2025-05-01", "SYN.US", -100, 1000.0),
            _row("p1", "2025-06-02", "SYN.US", 0, pil, "DIVIDEND_IN_LIEU",
                 "12:00:00"),
            _row("s4", "2025-09-02", "SYN.US", 100, 1000.0)]

    @rule(R.CA_RPT_14)
    def test_the_gross_income_is_the_dividends_received(self):
        rep = _report(self._rows(-30.0))
        self.assertEqual(_prop(rep, "SYN.US")["income"], 100.0)
        self.assertTrue(rep["filing_required"])
        control = _report(self._rows(0.0))
        self.assertEqual(_prop(control, "SYN.US")["income"], 100.0)

    @rule(R.CA_RPT_14)
    def test_a_reversal_while_held_long_still_nets(self):
        rows = [
            _row("s1", "2025-01-02", "SYN.US", 100, 1000.0),
            _row("p1", "2025-02-03", "SYN.US", 0, 30.0, "DIVIDEND_IN_LIEU"),
            _row("p2", "2025-02-10", "SYN.US", 0, -30.0, "DIVIDEND_IN_LIEU"),
            _row("p3", "2025-02-11", "SYN.US", 0, 30.0, "DIVIDEND_IN_LIEU")]
        self.assertEqual(_prop(_report(rows), "SYN.US")["income"], 30.0)

    def test_paid_on_short_reads_the_window(self):
        tl = {"version": 1, "fields": ["seq", "date", "account", "symbol",
                                       "qty"],
              "rows": [[0, "2025-01-02", "", "SYN.US", 100.0],
                       [1, "2025-03-03", "", "SYN.US", 0.0],
                       [2, "2025-05-01", "", "SYN.US", -100.0],
                       [3, "2025-05-20", "", "SYN.US", 0.0]]}
        f = T.paid_on_short(tl)
        self.assertFalse(f("SYN.US", "2025-02-03"))
        self.assertTrue(f("SYN.US", "2025-05-01"))
        # covered before the pay date, within the window
        self.assertTrue(f("SYN.US", "2025-06-10"))
        self.assertFalse(f("SYN.US", "2025-07-01"))
        self.assertFalse(f("ZZB.US", "2025-05-01"))


# ================ TJ-0646 an intraday foreign holding in the threshold test
class TestTJ0646IntradayCustodyHolding(unittest.TestCase):
    """TJ-0646: 100 SYN.TO bought for 500 at 10:00 and sold for 600 at
    12:00 the same day in an account held with a US custodian: the
    threshold test's maximum was 0 (the day-end custody units, none,
    applied to the step that held them), where the overnight control
    read 500.

    Formerly: test_fix_1029_r7b_taxtext.TestTJ0646IntradayCustodyHolding"""

    def _rep(self, sell_date):
        rows = [_row("b1", "2025-06-02", "SYN.TO", 100, 500.0),
                _row("s1", sell_date, "SYN.TO", -100, 600.0,
                     time="12:00:00")]
        return _report(rows, custody={"a": "USA"})

    @rule(R.CA_RPT_01, R.CA_RPT_02)
    def test_a_same_day_round_trip_counts_its_cost(self):
        same = self._rep("2025-06-02")
        self.assertEqual(same["max_total_cost"], 500.0)
        self.assertEqual(self._rep("2025-06-03")["max_total_cost"], 500.0)
        row = _prop(same, "SYN.TO")
        self.assertEqual(row["year_end_cost"], 0.0)
        self.assertEqual(row["gain"], 100.0)

    @rule(R.CA_RPT_02)
    def test_a_canadian_account_beside_it_keeps_its_part(self):
        # The Canadian account holds 100 all day; the custody account
        # buys 100 and sells them by noon: 500 of the 1,000 pooled cost
        # is foreign between the two (s.47 average 5 a share).
        rows = [_row("c1", "2025-05-01", "SYN.TO", 100, 500.0, acct="home"),
                _row("b1", "2025-06-02", "SYN.TO", 100, 500.0),
                _row("s1", "2025-06-02", "SYN.TO", -100, 600.0,
                     time="12:00:00")]
        rep = _report(rows, gains=False, custody={"a": "USA"})
        self.assertEqual(rep["max_total_cost"], 500.0)

    def test_the_timeline_names_each_steps_row(self):
        from taxjson.lib import cost_timeline as CT
        from taxjson.lib.pipeline import GainsRequest, run_gains
        rows = [_row("b1", "2025-06-02", "SYN.TO", 100, 500.0),
                _row("s1", "2025-06-02", "SYN.TO", -100, 600.0,
                     time="12:00:00")]
        with contextlib.redirect_stderr(io.StringIO()):
            doc = run_gains(rows, [], [], GainsRequest(
                country="canada", taxable=True, year=None,
                phantom_hint=False, cost_timeline=True))["cost_timeline"]
        self.assertEqual(CT.row_seqs(doc), {"b1": 0, "s1": 1})


# ================= TJ-0657 a refusal with no row to hold it is unsettled
class TestTJ0657UnplacedLedgerRefusalIsUnsettled(unittest.TestCase):
    """TJ-0657: the FX-on-cash ledger v2 refused (a .tt USD purchase in
    a folder of two broker accounts with no CASHBOOK line) before it
    walked any cash, so no currency row carried the refusal: the T1135
    said total_settled with no unknown cash.

    Formerly: test_fix_1029_r7b_taxtext.TestTJ0657UnplacedLedgerRefusalIsUnsettled"""

    @rule(R.CA_RPT_20)
    def test_the_refusal_leaves_the_accounts_cash_unknown(self):
        from taxjson.lib import cash_events as CE
        from taxjson.lib import fx_cash_v2 as FX
        books = CE.Books()
        books.add("a", "interactive_brokers", "U1234567")
        books.add("a", "interactive_brokers", "U7654321")  # pii-ok
        native = [{"action": "BUYSELL", "date": "2025-06-02",
                   "date_settle": "2025-06-02", "time": "09:30:00",
                   "symbol": "SYN.US", "quantity": 1, "currency": "USD",
                   "net_amount": -100.0, "price": 100.0, "account": "a",
                   "id": "b", "source": "manual.tt",
                   "_book": books.for_tt("a", "manual.tt")}]
        with contextlib.redirect_stderr(io.StringIO()):
            ledger = FX.build(native, {"events": [], "lines": [],
                                       "problems": []}, "CAD", 2025,
                              lambda c, d: 1.5, country="canada",
                              holdings=True)
        self.assertEqual(ledger["status"], "not_computed")
        doc = TC.cash_doc(ledger, {"a": "USA"}, 2025, "CAD")
        self.assertEqual([(n["account"], n["unknown"])
                          for n in doc["not_counted"]], [("a", True)])
        rep = _report([_row("b", "2025-06-02", "SYN.US", 1, 150.0)],
                      gains=False, custody={"a": "USA"}, cash=doc)
        self.assertFalse(rep["total_settled"])
        brief = T.render_brief(rep)
        self.assertIn("a: cash not counted (fx-cash v2 refuses)", brief)

    def test_a_row_already_unknown_is_not_named_twice(self):
        ledger = {"status": "not_computed",
                  "problems": [{"kind": "book", "book": "", "currency": "",
                                "text": "several broker accounts"}],
                  "holdings": {"foreign": [{"book": "a/ib",
                                            "currency": "USD",
                                            "points": [["2025-01-01", 1.0,
                                                        1.4]]}],
                               "base": []}}
        doc = TC.cash_doc(ledger, {"a": "USA", "b": "CRYPTO"}, 2025, "CAD")
        self.assertTrue(doc["series"][0]["unknown"])
        # account b has no row: its cash is unknown too (the pools are
        # one per currency over every account)
        self.assertEqual([n["account"] for n in doc["not_counted"]], ["b"])

    def test_a_computed_ledger_adds_nothing(self):
        ledger = {"status": "computed", "problems": [],
                  "holdings": {"foreign": [], "base": []}}
        self.assertEqual(TC.cash_doc(ledger, {"a": "USA"}, 2025,
                                     "CAD")["not_counted"], [])


if __name__ == "__main__":
    unittest.main()
