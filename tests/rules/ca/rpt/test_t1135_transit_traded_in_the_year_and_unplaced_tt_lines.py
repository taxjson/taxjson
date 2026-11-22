"""Fixes of 2026-10-30, round 7, wave E, group cash: T1135 cash in
transit, export coverage, Kraken swap fees, Coinbase's blank Total and
repeated hour across exports, and the crypto-sends migration on read-only
inputs. One test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented books and accounts, made-up tickers
and ids, small amounts. Each test's files live in a folder of their own.

Formerly: test_fix_1029_r7e_cash
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _support.fx_cash_build import _at, build, tt

from taxjson.lib import cash_lines as CL
from taxjson.lib import t1135_cash as TC
from _support.ca_t1135_cash import _Q_HDR, _qt, _t1135_project, _t1135, _tt

A, B, C, D = "a/manual", "b/manual", "c/manual", "d/manual"


def _year_end_cost(rep):
    return round(sum(p["year_end_cost"] or 0 for p in rep["properties"]
                     if p.get("cash")), 2)


# ============================================== TJ-1157, TJ-1185 the trade
# basis of a year-end transit
class TestTJ1157ArrivalTradedInTheYear(unittest.TestCase):
    """TJ-1157: on trade dates the arrival that trades in the year and
    settles after it is already in the receiver's row: the units were
    counted there and again in the sender's transit.

    Formerly: test_fix_1029_r7e_cash.TestTJ1157ArrivalTradedInTheYear"""

    def _rep(self, accounts, rate_cost=125, dep="2025-12-29", arr_td="2025-12-31"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, accounts, {
                "a": {"q.csv": _Q_HDR + _qt(dep, dep, "WDR", -100, 99900011),
                      "c.tt": _tt(100, rate_cost, dep, -100, 0)},
                "b": {"q.csv": _Q_HDR + _qt(arr_td, "2026-01-02", "DEP", 100,
                                            99900012),
                      "c.tt": _tt(0, 0, arr_td, 100, 100)}},
                tax_date="trade")
            return _t1135(root)

    @rule(R.CA_RPT_20)
    def test_counted_once(self):
        rep = self._rep({"a": "USA", "b": "USA"})
        self.assertEqual(_year_end_cost(rep), 125.0)
        self.assertEqual(rep["max_total_cost"], 125.0)
        self.assertTrue(rep["total_settled"])
        self.assertEqual(rep["cash_not_counted"], [])

    @rule(R.CA_RPT_20)
    def test_the_days_before_the_trade_stay_counted(self):
        # Left December 20, traded in on December 31: in the maximum on
        # the days between, through the sender's row.
        rep = self._rep({"a": "USA", "b": "USA"}, dep="2025-12-20")
        self.assertEqual(rep["max_total_cost"], 125.0)
        self.assertEqual(_year_end_cost(rep), 125.0)

    @rule(R.CA_RPT_20)
    def test_the_receiver_in_canada_is_not_an_unknown_year_end(self):
        # On trade dates b holds them on December 31; only the days
        # before are named.
        rep = self._rep({"a": "USA", "b": None})
        self.assertEqual(_year_end_cost(rep), 0.0)
        self.assertTrue(rep["total_settled"])
        n, = rep["cash_not_counted"]
        self.assertIn("during the year", n["why"])

    def test_settlement_basis_still_carries_it(self):
        # Control: on settlement dates b does not hold it at the year end.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"a": "USA", "b": "USA"}, {
                "a": {"q.csv": _Q_HDR + _qt("2025-12-29", "2025-12-29", "WDR",
                                            -100, 99900011),
                      "c.tt": _tt(100, 125, "2025-12-29", -100, 0)},
                "b": {"q.csv": _Q_HDR + _qt("2025-12-31", "2026-01-02", "DEP",
                                            100, 99900012),
                      "c.tt": _tt(0, 0, "2025-12-31", 100, 0)}},
                tax_date="settle")
            rep = _t1135(root)
        self.assertEqual(_year_end_cost(rep), 125.0)
        self.assertEqual(rep["max_total_cost"], 125.0)

    def test_the_ledgers_own_close_is_unchanged(self):
        # The close still carries it: only the T1135's view moves.
        lines = [tt("CASHOPEN 2025-01-01 USD 100 125", A, 1),
                 tt("CASHMOVE 2025-12-29 USD -100 own", A, 2),
                 tt("CASHBAL 2025-12-31 USD 0", A, 3),
                 tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                 tt("CASHMOVE 2026-01-02 USD 100 own", B, 2),
                 tt("CASHBAL 2025-12-31 USD 0", B, 3)]
        doc = build(lines, rate=1.5, holdings=True)
        self.assertEqual(doc["close"]["transit"]["USD"]["units"], 100.0)


class TestTJ1185DepartureTradedInTheYear(unittest.TestCase):
    """TJ-1185: a departure that trades in the year and settles after it
    left the row on its trade date and reached no other: a settled zero.

    Formerly: test_fix_1029_r7e_cash.TestTJ1185DepartureTradedInTheYear"""

    def _rep(self, accounts, arrival=True):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            b = {"c.tt": _tt(0, 0, "2026-01-03" if arrival else None, 100, 0)}
            if arrival:
                b["qt.csv"] = _Q_HDR + _qt("2026-01-03", "2026-01-03", "DEP",
                                           100, 99900002)
            _t1135_project(root, accounts, {
                "a": {"qt.csv": _Q_HDR + _qt("2025-12-30", "2026-01-02", "WDR",
                                             -100, 99900001),
                      "c.tt": _tt(100, 125, "2025-12-30", -100, 0)},
                "b": b}, tax_date="trade")
            return _t1135(root)

    @rule(R.CA_RPT_20)
    def test_counted_in_the_senders_row(self):
        rep = self._rep({"a": "USA", "b": "USA"})
        self.assertEqual(_year_end_cost(rep), 125.0)
        self.assertEqual(rep["max_total_cost"], 125.0)
        self.assertTrue(rep["total_settled"])

    @rule(R.CA_RPT_20)
    def test_a_canadian_side_is_named_and_not_settled(self):
        rep = self._rep({"a": "USA", "b": None})
        self.assertFalse(rep["total_settled"])
        self.assertEqual(
            [(n["account"], n["currency"], n["unknown"])
             for n in rep["cash_not_counted"]], [("a", "USD", True)])

    @rule(R.CA_RPT_20)
    def test_no_arrival_in_the_books_is_named_and_not_settled(self):
        rep = self._rep({"a": "USA", "b": "USA"}, arrival=False)
        self.assertFalse(rep["total_settled"])
        n, = rep["cash_not_counted"]
        self.assertIn("from a to ?", n["why"])

    def test_neither_side_counted_is_no_foreign_property(self):
        rep = self._rep({"a": None, "b": None})
        self.assertEqual(rep["cash_not_counted"], [])
        self.assertTrue(rep["total_settled"])


# ============================================== TJ-1186, TJ-1187 one entry
# per departure and arrival
def _two_senders(accounts=None):
    return [tt("CASHOPEN 2025-01-01 USD 100 125", A, 1),
            tt("CASHMOVE 2025-12-20 USD -100 own", A, 2),
            tt("CASHBAL 2025-12-31 USD 0", A, 3),
            tt("CASHOPEN 2025-01-01 USD 100 125", C, 1),
            tt("CASHMOVE 2025-12-30 USD -100 own", C, 2),
            tt("CASHBAL 2025-12-31 USD 0", C, 3),
            tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
            tt("CASHMOVE 2026-01-02 USD 200 own", B, 2),
            tt("CASHBAL 2025-12-31 USD 0", B, 3)]


class TestTJ1186EachSendersUnitsFromItsOwnDeparture(unittest.TestCase):
    """TJ-1186: two senders' transit merged into one entry at the earlier
    departure: the later sender's units were in its own row and in the
    transit for the days between, overstating the maximum.

    Formerly: test_fix_1029_r7e_cash.TestTJ1186EachSendersUnitsFromItsOwnDeparture"""

    @rule(R.CA_RPT_20)
    def test_two_senders(self):
        led = build(_two_senders(), rate=1.5, holdings=True)
        self.assertEqual(led["status"], "computed", led["problems"])
        doc = TC.cash_doc(led, {"a": "USA", "b": "USA", "c": "USA"},
                          2025, "CAD")
        rows = {s["account"]: s["points"] for s in doc["series"]}
        for d, want in (("2025-12-19", 250.0), ("2025-12-20", 250.0),
                        ("2025-12-29", 250.0), ("2025-12-30", 250.0),
                        ("2025-12-31", 250.0)):
            self.assertEqual(sum(_at(p, d)[1] for p in rows.values()), want, d)
        self.assertEqual(_at(rows["a"], "2025-12-31"), (100.0, 125.0))
        self.assertEqual(_at(rows["c"], "2025-12-31"), (100.0, 125.0))

    @rule(R.CA_RPT_20)
    def test_one_sender_two_departures(self):
        lines = [tt("CASHOPEN 2025-01-01 USD 200 250", A, 1),
                 tt("CASHMOVE 2025-12-20 USD -100 own", A, 2),
                 tt("CASHMOVE 2025-12-30 USD -100 own", A, 3),
                 tt("CASHBAL 2025-12-31 USD 0", A, 4),
                 tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                 tt("CASHMOVE 2026-01-02 USD 200 own", B, 2),
                 tt("CASHBAL 2025-12-31 USD 0", B, 3)]
        doc = TC.cash_doc(build(lines, rate=1.5, holdings=True),
                          {"a": "USA", "b": "USA"}, 2025, "CAD")
        a_row, = [s["points"] for s in doc["series"] if s["account"] == "a"]
        self.assertEqual(_at(a_row, "2025-12-25"), (200.0, 250.0))
        self.assertEqual(max(p[2] for p in a_row), 250.0)
        self.assertEqual(_at(a_row, "2025-12-31"), (200.0, 250.0))

    @rule(R.CA_RPT_20)
    def test_one_sender_in_canada_is_named_alone(self):
        doc = TC.cash_doc(build(_two_senders(), rate=1.5, holdings=True),
                          {"a": "USA", "b": "USA"}, 2025, "CAD")
        rows = {s["account"]: s["points"] for s in doc["series"]}
        self.assertEqual(_at(rows["a"], "2025-12-31"), (100.0, 125.0))
        n, = doc["not_counted"]
        self.assertTrue(n["unknown"])
        self.assertIn("100.00 USD", n["why"])
        self.assertIn("from c to b", n["why"])

    def test_a_named_sender_of_two_departures_is_one_entry(self):
        lines = [tt("CASHOPEN 2025-01-01 USD 200 250", A, 1),
                 tt("CASHMOVE 2025-12-20 USD -100 own", A, 2),
                 tt("CASHMOVE 2025-12-30 USD -100 own", A, 3),
                 tt("CASHBAL 2025-12-31 USD 0", A, 4),
                 tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                 tt("CASHMOVE 2026-01-02 USD 200 own", B, 2),
                 tt("CASHBAL 2025-12-31 USD 0", B, 3)]
        doc = TC.cash_doc(build(lines, rate=1.5, holdings=True),
                          {"a": "USA"}, 2025, "CAD")
        n, = doc["not_counted"]
        self.assertIn("200.00 USD", n["why"])
        self.assertIn("250.00 CAD", n["why"])


class TestTJ1187AnUnrelatedMoveNextYear(unittest.TestCase):
    """TJ-1187: every own arrival of the currency after the year was an
    end of the year-end transit, so an unrelated move next year changed
    its classification.

    Formerly: test_fix_1029_r7e_cash.TestTJ1187AnUnrelatedMoveNextYear"""

    @staticmethod
    def _lines(transit=(A, B), feb=(C, D)):
        """100 USD in transit from one pair at the year end; 50 USD from
        the other in February."""
        t0, t1 = transit
        f0, f1 = feb
        return [tt("CASHOPEN 2025-01-01 USD 100 125", t0, 1),
                tt("CASHMOVE 2025-12-31 USD -100 own", t0, 2),
                tt("CASHBAL 2025-12-31 USD 0", t0, 3),
                tt("CASHOPEN 2025-01-01 USD 0 0", t1, 1),
                tt("CASHMOVE 2026-01-02 USD 100 own", t1, 2),
                tt("CASHBAL 2025-12-31 USD 0", t1, 3),
                tt("CASHOPEN 2025-01-01 USD 50 60", f0, 1),
                tt("CASHMOVE 2026-02-10 USD -50 own", f0, 2),
                tt("CASHBAL 2025-12-31 USD 50", f0, 3),
                tt("CASHOPEN 2025-01-01 USD 0 0", f1, 1),
                tt("CASHMOVE 2026-02-11 USD 50 own", f1, 2),
                tt("CASHBAL 2025-12-31 USD 0", f1, 3)]

    @rule(R.CA_RPT_20)
    def test_a_later_move_between_canadian_custodians(self):
        doc = TC.cash_doc(build(self._lines(), rate=1.5, holdings=True),
                          {"a": "USA", "b": "USA"}, 2025, "CAD")
        self.assertEqual(doc["not_counted"], [])
        a_row, = [s["points"] for s in doc["series"] if s["account"] == "a"]
        self.assertEqual(_at(a_row, "2025-12-31"), (100.0, 123.33))

    @rule(R.CA_RPT_20)
    def test_a_transit_between_canadian_custodians(self):
        doc = TC.cash_doc(
            build(self._lines(transit=(C, D), feb=(A, B)), rate=1.5,
                  holdings=True),
            {"a": "USA", "b": "USA"}, 2025, "CAD")
        self.assertEqual(doc["not_counted"], [])

    def test_the_transit_says_its_own_arrival(self):
        led = build(self._lines(), rate=1.5, holdings=True)
        tr, = led["holdings"]["transit"]
        self.assertEqual((tr["from"], tr["to"], tr["since"], tr["arrives"]),
                         ([A], [B], "2025-12-31", "2026-01-02"))

    def test_a_rounding_stays_with_its_departure(self):
        lines = [tt("CASHOPEN 2025-01-01 USD 100 125", A, 1),
                 tt("CASHMOVE 2025-12-31 USD -100 own", A, 2),
                 tt("CASHBAL 2025-12-31 USD 0", A, 3),
                 tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                 tt("CASHMOVE 2026-01-02 USD 99.5 own", B, 2),
                 tt("CASHBAL 2025-12-31 USD 0", B, 3)]
        tr, = build(lines, rate=1.5, holdings=True)["holdings"]["transit"]
        self.assertEqual((tr["units"], tr["cost"], tr["to"]),
                         (100.0, 125.0, [B]))

    def test_two_arrivals_of_one_departure(self):
        lines = [tt("CASHOPEN 2025-01-01 USD 100 125", A, 1),
                 tt("CASHMOVE 2025-12-31 USD -100 own", A, 2),
                 tt("CASHBAL 2025-12-31 USD 0", A, 3),
                 tt("CASHMOVE 2026-01-02 USD 60 own", B, 1),
                 tt("CASHBAL 2025-12-31 USD 0", B, 2),
                 tt("CASHMOVE 2026-01-03 USD 40 own", C, 1),
                 tt("CASHBAL 2025-12-31 USD 0", C, 2)]
        led = build(lines, rate=1.5, holdings=True,
                    carry={"year": 2024, "currency": "CAD", "books": {
                        B: {"USD": {"units": 0.0, "cost": 0.0}},
                        C: {"USD": {"units": 0.0, "cost": 0.0}}}})
        self.assertEqual([(t["units"], t["to"]) for t in
                          led["holdings"]["transit"]],
                         [(60.0, [B]), (40.0, [C])])
        doc = TC.cash_doc(led, {"a": "USA", "b": "USA"}, 2025, "CAD")
        a_row, = [s["points"] for s in doc["series"] if s["account"] == "a"]
        self.assertEqual(_at(a_row, "2025-12-31"), (60.0, 75.0))
        n, = doc["not_counted"]
        self.assertIn("40.00 USD", n["why"])


# ============================================== TJ-1174 a .tt cash line
# with no book
class TestTJ1174ALineWithNoBook(unittest.TestCase):
    """TJ-1174: TJ-1098's exemption dropped the only signal of a .tt line
    the ledger could not place: T1135 said no filing required while the
    ledger refused the account's foreign cash.

    Formerly: test_fix_1029_r7e_cash.TestTJ1174ALineWithNoBook"""

    def _ledger(self, label="us"):
        ev = CL.parse_line("CASHOPEN 2025-01-01 USD 200 260",
                           f"inputs/{label}/cash.tt:1")
        ev.update(label=label, file="cash.tt", book=None,
                  book_problem="its account folder holds several books")
        return build([ev], holdings=True)

    @rule(R.CA_RPT_20)
    def test_the_account_is_unknown(self):
        led = self._ledger()
        self.assertEqual(led["status"], "not_computed")
        doc = TC.cash_doc(led, {"us": "USA"}, 2025, "CAD")
        self.assertIn(("us", "USD", True),
                      [(n["account"], n["currency"], n["unknown"])
                       for n in doc["not_counted"]])

    @rule(R.CA_RPT_20)
    def test_another_account_stays_settled(self):
        # TJ-1098's point holds: the line is in `us`'s file, not `x`'s.
        doc = TC.cash_doc(self._ledger(), {"us": "USA", "x": "USA"}, 2025,
                          "CAD")
        self.assertEqual({n["account"] for n in doc["not_counted"]
                          if n["unknown"]}, {"us"})
        doc = TC.cash_doc(self._ledger("dom"), {"x": "USA"}, 2025, "CAD")
        self.assertEqual([n for n in doc["not_counted"] if n["unknown"]], [])

    def test_the_command(self):
        # The item's repro: two brokers' exports and a .tt line of neither.
        qt = _Q_HDR + ("2025-03-03,2025-03-05,Buy,SYN.TO,SYN,1,10,-10,0,-10,"
                       "CAD,Trades,99900090,Margin\n")
        rbc = ("Date,Activity,Symbol,Symbol Description,Quantity,Price,"
               "Settlement Date,Account,Value,Currency,Description\n"
               "2025-03-03,Buy,ZZQ.TO,ZZQ,1,10,2025-03-05,99900099,-10,CAD,"
               "ZZQ\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"us": "USA"}, {"us": {
                "qt.csv": qt, "rbc.csv": rbc,
                "cash.tt": "CASHOPEN 2025-01-01 USD 200 260\n"
                           "CASHBAL 2025-12-31 USD 200\n"}})
            rep = _t1135(root)
        self.assertFalse(rep["total_settled"], rep["cash_not_counted"])


if __name__ == "__main__":
    unittest.main()
