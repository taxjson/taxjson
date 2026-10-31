"""Fixes of 2026-10-29, round 7, wave D, group cashcrypto: FX-on-cash
ledgers and T1135 cash, Coinbase's repeated hour and re-keyed sends,
Yahoo fill days already cached. One test class per backlog item, each
named for it.

Every fixture is SYNTHETIC: invented books and accounts, made-up tickers
and ids, small amounts. Each test's files live in a folder of their own.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule
from test_fix_rbc import parse_one, row

from taxjson.bin import taxjson_fx_cash as FX
from taxjson.bin import taxjson_t1135 as T1135
from taxjson.bin import to_base_curr as TB
from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib import t1135_cash as TC
from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.brokerages.kraken import kraken_cash_events
from taxjson.lib.core import coerce_transaction_row

A, B = "a/manual", "b/manual"


def tt(line, book=A, n=1):
    """A .tt cash line as lib/cash_events reads it, in `book`."""
    label = book.split("/", 1)[0]
    ev = CE.parse_line(line, f"inputs/{label}/book.tt:{n}")
    ev.update(label=label, file="book.tt", book=book, book_problem="")
    return ev


def build(lines, native=(), rate=1.0, year=2025, **kw):
    kw.setdefault("country", "canada")
    rate_of = rate if callable(rate) else (lambda c, d: rate)
    cash = {"events": [], "lines": list(lines), "problems": [],
            "stablecoins_as_cash": True}
    return V2.build(list(native), cash, "CAD", year, rate_of, **kw)


# ============================================== TJ-1033 RBC's year-end
# ROC book-cost row carries no cash
class TestTJ1033RbcBookCostRowIsNoCash(unittest.TestCase):
    """TJ-1033: a 30 USD cash distribution in June, converted to CAD in
    September, and RBC's December row "RETURN OF CAPITAL ADJUSTMENT TO
    BOOK COST $30.00" (Value 0). TJ-0846 counted every broker ROC as a
    cash receipt, so 30 USD that never existed sat in the pool."""

    def _rows(self, source="rbc.csv"):
        body = (row("June 16, 2025", "Distribution", "XYZ", "XYZ TRUST",
                    "", "", "30.00", "USD", "XYZ TRUST DIST ON 100 SHS")
                + row("December 31, 2025", "Distribution", "XYZ",
                      "XYZ TRUST", "", "", "0", "USD",
                      "RTC - XYZ TRUST RETURN OF CAPITAL "
                      "ADJUSTMENT TO BOOK COST $30.00"))
        txs, _err, _p = parse_one(body)
        for t in txs:
            t["source"] = source
            t["_book"] = A
        return txs

    @rule("CA-FX-07")
    def test_no_cash_leg(self):
        adj = [t for t in self._rows() if t["action"] == "ADJUST"]
        self.assertEqual(len(adj), 1)
        self.assertEqual(adj[0]["type"], "roc")
        self.assertEqual(FX._cash_legs(adj[0]), [])

    @rule("CA-FX-07")
    def test_the_mark_survives_the_book(self):
        # Rows reach the ledgers through the book (lib/core): a field
        # the row type does not know would be dropped there.
        adj = [t for t in self._rows() if t["action"] == "ADJUST"][0]
        back = coerce_transaction_row(adj, 0, "t").to_dict()
        self.assertEqual(back.get("no_cash"), "book-cost")
        self.assertEqual(FX._cash_legs(back), [])

    @rule("CA-FX-07")
    def test_both_ledgers(self):
        rate = lambda c, d: 1.40  # noqa: E731
        v1 = FX.build_ledger(self._rows(), "CAD", {}, 2025, rate_of=rate,
                             country="canada")
        self.assertEqual(v1["per_currency"]["USD"]["acquired"], 30.0)
        v2 = build([tt("CASHOPEN 2025-01-01 USD 0 0", n=1),
                    tt("FXCONV 2025-09-02 USD 30 CAD 42", n=2),
                    tt("CASHBAL 2025-12-31 USD 0", n=3)],
                   native=self._rows(), rate=rate)
        self.assertEqual(v2["status"], "computed", v2["problems"])
        self.assertEqual(v2["net_gain"], 0.0)
        self.assertEqual(v2["pools_year_end"], {})

    @rule("CA-FX-07")
    def test_a_roc_row_with_a_value_still_brings_its_cash(self):
        # TJ-0846's own case on an RBC row: a Value means cash came in.
        body = row("June 16, 2025", "Return of Capital", "XYZ",
                   "XYZ TRUST", "", "", "30.00", "USD",
                   "XYZ TRUST RETURN OF CAPITAL")
        txs, _err, _p = parse_one(body)
        adj = [t for t in txs if t["action"] == "ADJUST"]
        self.assertEqual(len(adj), 1)
        adj[0]["source"] = "rbc.csv"
        self.assertEqual(FX._cash_legs(adj[0]), [("USD", 30.0)])


# ============================================== TJ-1080 a redacted copy
# beside a Kraken ledger
_KL_H2 = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
          "balance,amountusd,feeusd,feecurrency\n")


class TestTJ1080RedactedCopyIsNoKrakenSibling(unittest.TestCase):
    """TJ-1080: `tjs redact FILE` beside a Kraken ledger: the copy's
    rows, the export's own times under new ids, read as another export's
    movements, so the opening CASHBAL of the export was dropped."""

    _ROWS = ("L1AAAA-BBBBB-CCCCCC,F1AAAA-BBBBB-CCCCCC,2025-01-02 16:00:00,"
             "deposit,,currency,ZUSD,spot,100,0,100,100,0,ZUSD\n"
             "L2AAAA-BBBBB-CCCCCC,F2AAAA-BBBBB-CCCCCC,2025-06-02 16:00:00,"
             "withdrawal,,currency,ZUSD,spot,-20,0,80,20,0,ZUSD\n")
    _COPY = _ROWS.replace("L1AAAA-BBBBB-CCCCCC", "REDACTED0000000001") \
        .replace("L2AAAA-BBBBB-CCCCCC", "REDACTED0000000002")

    def setUp(self):
        self._old_tz = os.environ.get("TAXJSON_LOCAL_TZ")
        os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
        self.addCleanup(self._restore_tz)

    def _restore_tz(self):
        if self._old_tz is None:
            os.environ.pop("TAXJSON_LOCAL_TZ", None)
        else:
            os.environ["TAXJSON_LOCAL_TZ"] = self._old_tz

    def _bals(self, copy):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kraken_ledgers.csv"
            p.write_text(_KL_H2 + self._ROWS)
            if copy:
                (Path(d) / "kraken_ledgers.redacted.csv").write_text(
                    _KL_H2 + self._COPY)
            return sorted((e["date"], e["currency"], e["balance"])
                          for e in kraken_cash_events(p)
                          if e["kind"] == "CASHBAL")

    @rule("CA-FX-07")
    def test_the_opening_balance_stays(self):
        self.assertIn(("2024-12-31", "USD", 0.0), self._bals(False))
        self.assertEqual(self._bals(True), self._bals(False))

    def test_a_folder_of_only_redacted_copies_reads_each_other(self):
        # A sample project: nothing else is there, so the copies are the
        # inputs and they stay one another's siblings.
        from taxjson.lib.brokerages.detect import same_broker_siblings
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "kraken_a.redacted.csv"
            b = Path(d) / "kraken_b.redacted.csv"
            a.write_text(_KL_H2 + self._COPY)
            b.write_text(_KL_H2 + self._COPY)
            self.assertEqual([q.name for q in same_broker_siblings(
                a, "kraken")], [b.name])


# ============================================== TJ-1098 a book-less
# problem of one currency reaches only the accounts with that currency
class TestTJ1098CurrencyProblemReachesItsHolders(unittest.TestCase):
    """TJ-1098: no EUR rate for account dom's purchase: account a, which
    never held EUR, was named as holding EUR of unknown cost, and its
    total was no longer settled (a regression of TJ-0657's fix)."""

    @staticmethod
    def _native():
        def rowd(acct, sym, cur, d, net, rid):
            return {"action": "BUYSELL", "date": d, "date_settle": d,
                    "time": "09:30:00", "symbol": sym, "quantity": 1,
                    "currency": cur, "net_amount": net, "price": -net,
                    "account": acct, "id": rid, "source": "ib.csv"}
        books = CE.Books()
        books.add("a", "interactive_brokers", "U1234567")
        books.add("dom", "interactive_brokers", "U7654321")  # pii-ok
        rows = [rowd("a", "SYN.TO", "CAD", "2025-06-02", -100.0, "r1"),
                rowd("dom", "ZZQ.DE", "EUR", "2025-07-02", -50.0, "r2")]
        for r in rows:
            r["_book"] = books.for_file(r["account"], "ib.csv", "")
        return rows

    def _ledger(self, native):
        return V2.build(native, {"events": [], "lines": [], "problems": [],
                                 "stablecoins_as_cash": True}, "CAD", 2025,
                        lambda c, d: None if c == "EUR" else 1.4,
                        country="canada", holdings=True)

    @rule("CA-RPT-20")
    def test_an_account_without_the_currency_stays_settled(self):
        ledger = self._ledger(self._native())
        self.assertEqual(ledger["status"], "not_computed")
        doc = TC.cash_doc(ledger, {"a": "USA"}, 2025, "CAD")
        self.assertEqual([(n["account"], n["currency"], n["unknown"])
                          for n in doc["not_counted"]
                          if n["currency"] != "CAD"], [])

    @rule("CA-RPT-20")
    def test_the_account_whose_event_was_not_walked_is_unknown(self):
        # TJ-0657's point: dom's EUR purchase was skipped (no rate), so
        # dom has no EUR row, yet its EUR is unknown, not none.
        ledger = self._ledger(self._native())
        doc = TC.cash_doc(ledger, {"dom": "USA"}, 2025, "CAD")
        unknown = [(s["account"], s["currency"]) for s in doc["series"]
                   if s["unknown"]] + [
            (n["account"], n["currency"]) for n in doc["not_counted"]
            if n["unknown"]]
        self.assertIn(("dom", "EUR"), unknown)

    def test_a_problem_naming_no_currency_reaches_every_account(self):
        ledger = {"status": "not_computed",
                  "problems": [{"kind": "book", "book": "", "currency": "",
                                "text": "several broker accounts"}],
                  "holdings": {"foreign": [], "base": [], "currencies": []}}
        doc = TC.cash_doc(ledger, {"a": "USA"}, 2025, "CAD")
        self.assertEqual([n["account"] for n in doc["not_counted"]], ["a"])

    def test_an_unreadable_carry_of_a_currency_reaches_every_account(self):
        # The record does not say who held it.
        ledger = {"status": "not_computed",
                  "problems": [{"kind": "carry", "book": "",
                                "currency": "USD", "text": "bad record"}],
                  "holdings": {"foreign": [], "base": [], "currencies": []}}
        doc = TC.cash_doc(ledger, {"a": "USA"}, 2025, "CAD")
        self.assertEqual([n["currency"] for n in doc["not_counted"]],
                         ["USD"])


# ============================================== TJ-1099 a same-day round
# trip at a superficial loss
class TestTJ1099SuperficialLossRoundTrip(unittest.TestCase):
    """TJ-1099: account a (USA custodian) buys 100 for 500 at 10:00 and
    sells them for 400 at 12:00; account b's rebuy makes the loss
    superficial. The DISALLOW row carries the sale's id, the id was
    walked twice and could not be placed, so the at-any-time maximum
    fell back to the day's end: none (TJ-0646's bug on this one case)."""

    @staticmethod
    def _row(i, q, net, acct="a", time="10:00:00", d="2025-06-02"):
        return {"id": i, "action": "BUYSELL", "date": d, "date_settle": d,
                "time": time, "symbol": "SYN.TO", "quantity": q,
                "net_amount": net, "gross_amount": 0.0,
                "price": abs(net / q), "currency": "CAD", "account": acct}

    def _max(self, rows):
        with tempfile.TemporaryDirectory() as td, \
                contextlib.redirect_stderr(io.StringIO()):
            paths = []
            for acct in sorted({r["account"] for r in rows}):
                p = Path(td) / f"{acct}_base.json"
                p.write_text(json.dumps({"transactions": [
                    r for r in rows if r["account"] == acct]}))
                paths.append(p)
            return T1135.build_report(paths, [], 2025, {}, "CAD",
                                      tax_date="settle", today="2026-10-29",
                                      custody={"a": "USA"})["max_total_cost"]

    @rule("CA-RPT-20")
    def test_the_round_trip_stays_in_the_maximum(self):
        rows = [self._row("1", 100, 500.0),
                self._row("2", -100, 400.0, time="12:00:00")]
        self.assertEqual(self._max(rows), 500.0)      # the control
        rows.append(self._row("3", 100, 400.0, acct="b", d="2025-06-05"))
        self.assertEqual(self._max(rows), 500.0)

    def test_a_row_id_walked_twice_is_still_left_out(self):
        from taxjson.lib.cost_timeline import row_seqs
        self.assertEqual(row_seqs({"row_ids": ["1", "2", "", "2", "3"]}),
                         {"1": 0, "3": 4})


# ============================================== TJ-1076 an own move
# across December 31 that the receiving broker books first
class TestTJ1076EarlyArrivalAcrossTheYearEnd(unittest.TestCase):
    """TJ-1076: b's broker credits the own move on December 30, a's books
    it leaving on January 2: the 2024 ledger refused ("no move out ...
    in the 7 days after") and 2025's departure never arrived."""

    def _lines(self, year):
        if year == 2024:
            return [tt("CASHOPEN 2024-01-01 USD 100 125", A, 1),
                    tt("CASHMOVE 2025-01-02 USD -100 own", A, 2),
                    tt("CASHBAL 2024-12-31 USD 100", A, 3),
                    tt("CASHOPEN 2024-01-01 USD 0 0", B, 1),
                    tt("CASHMOVE 2024-12-30 USD 100 own", B, 2),
                    tt("CASHBAL 2024-12-31 USD 100", B, 3)]
        return [tt("CASHMOVE 2025-01-02 USD -100 own", A, 2),
                tt("CASHBAL 2024-12-31 USD 100", A, 3),
                tt("CASHBAL 2025-12-31 USD 0", A, 4),
                tt("CASHMOVE 2024-12-30 USD 100 own", B, 2),
                tt("CASHBAL 2024-12-31 USD 100", B, 3),
                tt("CASHBAL 2025-12-31 USD 100", B, 4)]

    @rule("CA-FX-07")
    def test_the_year_closes_owned_once(self):
        doc = build(self._lines(2024), year=2024, rate=1.5, holdings=True)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 0.0)
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 100.0, "acb": 125.0}})
        self.assertEqual(doc["close"]["books"][A]["USD"]["units"], 0.0)
        self.assertEqual(doc["close"]["books"][B]["USD"],
                         {"units": 100.0, "cost": 125.0})
        self.assertEqual(doc["close"]["early_out"],
                         {A: {"USD": {"units": 100.0}}})
        self.assertIsNone(V2.close_problem(doc["close"], 2024))
        # b's arrival and a's statement both reconcile.
        self.assertTrue(all(r["ok"] for r in doc["reconciliation"]))
        # The T1135 holdings count it once.
        last = {r["book"]: r["points"][-1][1]
                for r in doc["holdings"]["foreign"]}
        self.assertEqual(last, {A: 0.0, B: 100.0})

    @rule("CA-FX-07")
    def test_the_departure_is_matched_next_year(self):
        y1 = build(self._lines(2024), year=2024, rate=1.5)
        y2 = build(self._lines(2025), rate=1.5, year=2025,
                   carry=y1["close"])
        self.assertEqual(y2["status"], "computed", y2["problems"])
        self.assertEqual(y2["net_gain"], 0.0)
        self.assertNotIn("early_out", y2["close"])
        self.assertNotIn("transit", y2["close"])
        self.assertEqual(y2["close"]["books"][B]["USD"],
                         {"units": 100.0, "cost": 125.0})
        self.assertEqual(y2["pools_year_end"],
                         {"USD": {"units": 100.0, "acb": 125.0}})

    def test_a_carried_departure_the_books_lack_is_refused(self):
        y1 = build(self._lines(2024), year=2024, rate=1.5)
        lines = [ln for ln in self._lines(2025)
                 if not (ln.get("book") == A and ln["kind"] == "CASHMOVE")]
        y2 = build(lines, rate=1.5, year=2025, carry=y1["close"])
        self.assertEqual(y2["status"], "not_computed")
        self.assertTrue(any("is not in the books" in p["text"]
                            for p in y2["problems"]), y2["problems"])

    def test_a_departure_too_late_is_still_refused(self):
        lines = self._lines(2024)
        lines[1] = tt("CASHMOVE 2025-01-20 USD -100 own", A, 2)
        doc = build(lines, year=2024, rate=1.5)
        self.assertEqual(doc["status"], "not_computed")

    def test_a_sender_that_spends_the_units_first_is_still_refused(self):
        lines = self._lines(2024) + [tt("FXCONV 2025-01-01 USD 100 CAD 150",
                                        A, 5)]
        doc = build(lines, year=2024, rate=1.5)
        self.assertEqual(doc["status"], "not_computed")

    def test_a_damaged_early_out_is_refused(self):
        bad = {"year": 2024, "currency": "CAD", "books": {},
               "early_out": {A: {"USD": {"units": "x"}}}}
        self.assertIn("early_out", V2.close_problem(bad, 2024))
        doc = build(self._lines(2025), rate=1.5, year=2025, carry=bad)
        self.assertTrue(any("early_out" in p["text"]
                            for p in doc["problems"]), doc["problems"])


# ============================================== TJ-1129 an own move in
# transit inside the year
def _at(points, d):
    """A series' value (units, cost) at the end of day `d`."""
    v = (0.0, 0.0)
    for p in points:
        if p[0] <= d:
            v = (p[1], p[2])
    return v


class TestTJ1129OwnMoveInTransitInsideTheYear(unittest.TestCase):
    """TJ-1129: 100 USD (cost 125) leave a on June 1 and reach b on June
    5: they were in neither account's units from June 1 to June 4, so
    the T1135 maximum missed them on those days."""

    def _doc(self, a_dep="2025-06-01", b_arr="2025-06-05", **kw):
        return build([tt("CASHOPEN 2025-01-01 USD 100 125", A, 1),
                      tt(f"CASHMOVE {a_dep} USD -100 own", A, 2),
                      tt("CASHBAL 2025-12-31 USD 0", A, 3),
                      tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                      tt(f"CASHMOVE {b_arr} USD 100 own", B, 2),
                      tt("CASHBAL 2025-12-31 USD 100", B, 3)],
                     rate=1.5, holdings=True, **kw)

    @rule("CA-RPT-20")
    def test_both_accounts_counted_in_one_country(self):
        doc = TC.cash_doc(self._doc(), {"a": "USA", "b": "USA"}, 2025, "CAD")
        self.assertEqual(doc["not_counted"], [])
        rows = {s["account"]: s["points"] for s in doc["series"]}
        # Held by a, in transit (a's row), held by b: 125 at every step.
        for d in ("2025-05-31", "2025-06-01", "2025-06-04", "2025-06-05",
                  "2025-12-31"):
            total = _at(rows["a"], d)[1] + _at(rows["b"], d)[1]
            self.assertEqual(total, 125.0, d)
        self.assertEqual(_at(rows["a"], "2025-06-04"), (100.0, 125.0))
        self.assertEqual(_at(rows["a"], "2025-06-05"), (0.0, 0.0))

    def test_the_same_day_move_has_no_days_in_transit(self):
        doc = self._doc(b_arr="2025-06-01")
        self.assertEqual(doc["holdings"]["transit_spans"], [])

    def test_an_arrival_that_comes_first_has_none(self):
        doc = self._doc(a_dep="2025-06-03", b_arr="2025-06-01")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["holdings"]["transit_spans"], [])

    @rule("CA-RPT-20")
    def test_one_end_in_canada_is_named_and_leaves_the_total_settled(self):
        doc = TC.cash_doc(self._doc(), {"a": "USA"}, 2025, "CAD")
        named = doc["not_counted"]
        self.assertEqual([(n["account"], n["currency"], n["unknown"])
                          for n in named], [("a", "USD", False)])
        self.assertIn("in transit between your own accounts", named[0]["why"])
        self.assertIn("125.00 CAD", named[0]["why"])

    def test_no_end_counted_is_no_foreign_property(self):
        doc = TC.cash_doc(self._doc(), {"c": "USA"}, 2025, "CAD")
        self.assertEqual(doc["series"], [])
        self.assertEqual(doc["not_counted"], [])

    @rule("CA-RPT-20")
    def test_carried_in_transit_from_the_prior_year(self):
        # TJ-0656's move: left a on December 31, reaches b on January 5.
        lines = [tt("CASHMOVE 2024-12-31 USD -100 own", A, 1),
                 tt("CASHBAL 2025-12-31 USD 0", A, 2),
                 tt("CASHMOVE 2025-01-05 USD 100 own", B, 1),
                 tt("CASHBAL 2025-12-31 USD 100", B, 2)]
        carry = {"year": 2024, "currency": "CAD",
                 "books": {A: {"USD": {"units": 0.0, "cost": 0.0}},
                           B: {"USD": {"units": 0.0, "cost": 0.0}}},
                 "transit": {"USD": {"units": 100.0, "cost": 125.0}}}
        doc = build(lines, rate=1.5, holdings=True, carry=carry)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        spans = doc["holdings"]["transit_spans"]
        self.assertEqual([(s["from"], s["to"], s["since"], s["until"])
                          for s in spans],
                         [(A, B, "2024-12-31", "2025-01-05")])
        rows = {s["account"]: s["points"] for s in TC.cash_doc(
            doc, {"a": "USA", "b": "USA"}, 2025, "CAD")["series"]}
        self.assertEqual(_at(rows["a"], "2025-01-04"), (100.0, 125.0))
        self.assertEqual(_at(rows["a"], "2025-01-05"), (0.0, 0.0))
        self.assertEqual(_at(rows["b"], "2025-01-05"), (100.0, 125.0))

    def test_a_carried_arrival_whose_departure_came_first_has_no_days(self):
        # TJ-1076: b's arrival of 2024 took a's January departure, so
        # June's departure pairs with June's arrival, not January's.
        carry = {"year": 2024, "currency": "CAD",
                 "books": {A: {"USD": {"units": 50.0, "cost": 60.0}},
                           B: {"USD": {"units": 100.0, "cost": 120.0}}},
                 "early_out": {A: {"USD": {"units": 100.0}}}}
        lines = [tt("CASHMOVE 2025-01-02 USD -100 own", A, 1),
                 tt("CASHMOVE 2025-06-01 USD -50 own", A, 2),
                 tt("CASHBAL 2025-12-31 USD 0", A, 3),
                 tt("CASHMOVE 2025-06-05 USD 50 own", B, 1),
                 tt("CASHBAL 2025-12-31 USD 150", B, 2)]
        doc = build(lines, rate=1.5, holdings=True, carry=carry)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual([(s["units"], s["since"], s["until"]) for s in
                          doc["holdings"]["transit_spans"]],
                         [(50.0, "2025-06-01", "2025-06-05")])

    @rule("CA-RPT-20")
    def test_the_threshold_walk_counts_it_at_the_peak(self):
        # A foreign share held June 2 to 4, while the 125 are in transit.
        def rowd(i, q, net, d):
            return {"id": i, "action": "BUYSELL", "date": d,
                    "date_settle": d, "time": "10:00:00", "symbol": "SYN.US",
                    "quantity": q, "net_amount": net, "gross_amount": 0.0,
                    "price": abs(net / q), "currency": "CAD", "account": "a"}
        rows = [rowd("1", 10, 1000.0, "2025-06-02"),
                rowd("2", -10, 1000.0, "2025-06-04")]
        doc = TC.cash_doc(self._doc(), {"a": "USA", "b": "USA"}, 2025, "CAD")
        with tempfile.TemporaryDirectory() as td, \
                contextlib.redirect_stderr(io.StringIO()):
            p = Path(td) / "a_base.json"
            p.write_text(json.dumps({"transactions": rows}))
            rep = T1135.build_report([p], [], 2025, {}, "CAD",
                                     tax_date="settle", today="2026-10-29",
                                     custody={"a": "USA", "b": "USA"},
                                     cash=doc)
        self.assertEqual(rep["max_total_cost"], 1125.0)
        self.assertEqual(rep["max_total_date"], "2025-06-02")

    def test_the_cost_follows_the_pool_while_in_transit(self):
        # A purchase in between changes the average, as for held cash.
        doc = build([tt("CASHOPEN 2025-01-01 USD 100 100", A, 1),
                     tt("CASHMOVE 2025-06-01 USD -100 own", A, 2),
                     tt("CASHBAL 2025-12-31 USD 0", A, 3),
                     tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                     tt("FXCONV 2025-06-03 CAD 300 USD 100", B, 2),
                     tt("CASHMOVE 2025-06-05 USD 100 own", B, 3),
                     tt("CASHBAL 2025-12-31 USD 200", B, 4)],
                    rate=1.5, holdings=True)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        pts = doc["holdings"]["transit_spans"][0]["points"]
        self.assertEqual(pts, [["2025-06-01", 100.0, 100.0],
                               ["2025-06-03", 100.0, 200.0],
                               ["2025-06-05", 0.0, 0.0]])


# ============================================== TJ-1056 two seconds of
# the repeated hour compressed into one
_CB_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
           "Price Currency,Price at Transaction,Subtotal,"
           "Total (inclusive of Fees and/or Spread),Fees and/or Spread,"
           "Notes\n")


class _Toronto(unittest.TestCase):
    def setUp(self):
        old = os.environ.get("TAXJSON_LOCAL_TZ")
        os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

        def restore():
            if old is None:
                os.environ.pop("TAXJSON_LOCAL_TZ", None)
            else:
                os.environ["TAXJSON_LOCAL_TZ"] = old
        self.addCleanup(restore)


class TestTJ1056RepeatedHourKeepsSecondsInOrder(_Toronto):
    """TJ-1056: the compression of the repeated hour (TJ-0747) gave a
    sale at 05:00:00 UTC and a rebuy at 05:00:01 one local second, and a
    newest-first export then replayed the rebuy first: a gain of 24.95,
    not 50.00."""

    def _gain(self, sale, rebuy, newest_first=True):
        import dataclasses
        from taxjson.lib.core import TaxTransaction as TX
        from taxjson.lib.pipeline import GainsRequest, run_gains
        rows = [f"C1,2025-01-06 15:00:00 UTC,Buy,ZZE,10,CAD,5,50,50,0,"
                f"Bought 10 ZZE\n",
                f"C2,2025-11-02 {sale} UTC,Sell,ZZE,10,CAD,10,100,100,0,"
                f"Sold 10 ZZE\n",
                f"C3,2025-11-02 {rebuy} UTC,Buy,ZZE,10,CAD,10.01,100.1,"
                f"100.1,0,Bought 10 ZZE\n"]
        if newest_first:
            rows.reverse()
        with tempfile.TemporaryDirectory() as td, \
                contextlib.redirect_stderr(io.StringIO()):
            p = Path(td) / "cb.csv"
            p.write_text(_CB_HDR + "".join(rows))
            parsed = CoinbaseBrokerage().parse_file(p)
            fields = {f.name for f in dataclasses.fields(TX)}
            res = run_gains([TX(**{k: v for k, v in r.items() if k in fields})
                             for r in parsed], req=GainsRequest(
                country="canada", year=2025, taxable=True,
                phantom_hint=False))
        return res["summary"]["total_gain"], [r["id"] for r in parsed]

    @rule("CA-DATE-12")
    def test_a_sale_and_a_rebuy_one_second_apart(self):
        for sale, rebuy in (("05:00:00", "05:00:01"),
                            ("06:00:00", "06:00:01"),
                            ("05:59:58", "05:59:59")):
            for newest_first in (True, False):
                with self.subTest(sale=sale, newest_first=newest_first):
                    gain, _ids = self._gain(sale, rebuy, newest_first)
                    self.assertAlmostEqual(gain, 50.0, places=2)

    def test_the_kraken_rows_share_the_one_ordering(self):
        from taxjson.lib.brokerages import _crypto_common as cc
        a, b, c = ({"date": "d", "time": "t", "n": n} for n in "abc")
        rows = [c, a, b]
        lines = {id(a): 3, id(b): 2, id(c): 1}
        cc.order_within_seconds(rows, lambda t: lines[id(t)],
                                {1: 5, 2: 1, 3: 3})
        self.assertEqual([r["n"] for r in rows], ["b", "a", "c"])
        # A line of no known instant: the stamp keeps its order.
        rows = [c, a, b]
        cc.order_within_seconds(rows, lambda t: lines[id(t)], {1: 5, 2: 1})
        self.assertEqual([r["n"] for r in rows], ["c", "a", "b"])


# ============================================== TJ-1057 saved decisions
# stay on their sends through the re-keying
class TestTJ1057SavedDecisionsStayOnTheirSends(_Toronto):
    """TJ-1057: two sends alike to the second in an export listing B
    first. Before TJ-0663 B was ...-1 and A ...-1-2; after it A is ...-1:
    a saved gift on B silently moved to A."""

    ROW_A = ("AAA-SEND-901,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,10,10,10,0,"
             "Sent 1 ZZE\n")
    ROW_B = ("BBB-SEND-902,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,11,11,11,0,"
             "Sent 1 ZZE\n")
    S1, S2 = "cb-20250303T120000-ZZE-1", "cb-20250303T120000-ZZE-1-2"

    def _rows(self, td, body):
        from test_fix_1029_r7b_coinbase import _parse_sidecar
        from taxjson.lib.crypto_sends import (assign_send_ids,
                                              load_transfer_rows)
        work = Path(td) / "work"
        work.mkdir(exist_ok=True)
        f = Path(td) / "coinbase.csv"
        f.write_text(_CB_HDR + body)
        _parse_sidecar(work / "coin_coinbase_transfers.json", f)
        rows = load_transfer_rows(work, ["coin"])
        assign_send_ids(rows)
        return rows

    def _migrate(self, td, sends, version=1, body=None):
        from taxjson.lib import crypto_sends as CS
        rows = self._rows(td, body or self.ROW_B + self.ROW_A)
        man = Path(td) / "inputs" / "coin" / "sends.json"
        man.parent.mkdir(parents=True, exist_ok=True)
        man.write_text(json.dumps({"schema_version": version,
                                   "sends": sends}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            CS.migrate_saved_ids(Path(td) / "inputs", ["coin"], rows)
        by_price = {r["sid"]: r["price"] for r in rows}
        return json.loads(man.read_text()), by_price, err.getvalue()

    def test_the_gift_stays_on_the_send_it_was_made_for(self):
        with tempfile.TemporaryDirectory() as td:
            doc, price, err = self._migrate(td, {
                self.S1: {"decision": "gift"}, self.S2: {"decision": "self"}})
        self.assertEqual(doc["schema_version"], 2)
        gift = [k for k, v in doc["sends"].items()
                if v["decision"] == "gift"]
        self.assertEqual(len(gift), 1)
        # B, the 11 send, was the first row: the gift was made on it.
        self.assertEqual(price[gift[0]], 11.0)
        self.assertIn("saved decision", err)
        self.assertIn(self.S2, err)

    def test_one_saved_decision_and_its_fee_move(self):
        with tempfile.TemporaryDirectory() as td:
            doc, _p, _e = self._migrate(td, {
                self.S1: {"decision": "gift"},
                self.S1 + "-fee": {"decision": "fee", "price": 1.0}})
        self.assertEqual(sorted(doc["sends"]),
                         sorted([self.S2, self.S2 + "-fee"]))

    def test_it_moves_once(self):
        with tempfile.TemporaryDirectory() as td:
            sends = {self.S1: {"decision": "gift"}}
            doc, _p, err = self._migrate(td, sends, version=2)
            self.assertEqual(doc["sends"], sends)
            self.assertEqual(err, "")

    def test_an_export_in_the_new_order_moves_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            sends = {self.S1: {"decision": "gift"}}
            doc, _p, err = self._migrate(td, sends,
                                         body=self.ROW_A + self.ROW_B)
            self.assertEqual(doc, {"schema_version": 1, "sends": sends})
            self.assertEqual(err, "")

    def test_a_new_decision_stamps_the_file(self):
        # Decided under the new numbering: never moved after that.
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            man = Path(td) / "sends.json"
            CS.record_decision(man, self.S1, "gift")
            self.assertEqual(json.loads(man.read_text())["schema_version"],
                             2)


# ============================================== TJ-1090 Yahoo fill days
# already in the cache
class TestTJ1090StaleYahooFillsInTheCache(unittest.TestCase):
    """TJ-1090: v0.38.0's refresh_yahoo filled on from a cached fill, so
    the cache holds GBPUSD 2025-01-02..15 from one January 1 observation,
    with coverage: never fetched again, and written to the rates file on
    each rebuild, so January 20 had a rate 19 days old (TJ-0843's bug)."""

    OBS = {"2025-01-01": 1.25, "2025-01-22": 1.50}

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.file = Path(td.name) / "cache.json"
        patch = mock.patch.object(TB, "CACHE_FILE", str(self.file))
        patch.start()
        self.addCleanup(patch.stop)
        self.td = td.name

    def _old_cache(self):
        cache = {f"GBPUSD-2025-01-{d:02d}": 1.25 for d in range(1, 16)}
        cache["GBPUSD-2025-01-22"] = 1.5
        cache["_coverage"] = {"yahoo:GBPUSD": [["2025-01-01", "2025-01-31"]]}
        self.file.write_text(json.dumps(cache))

    def _fetch(self, _t, a, b):
        return {d: v for d, v in self.OBS.items() if a <= d <= b}

    def _build(self, offline=False, fetch=None):
        rows, errors, notes = TB.build_rates(
            "GBP", "USD", "2025-01-01", "2025-01-22", today="2025-02-01",
            offline=offline, fetch_yahoo_fn=fetch or self._fetch)
        return rows, errors, notes

    def _rate_on(self, rows, day):
        from taxjson.bin.taxjson_convert_currency import (
            get_rate_for_date, load_exchange_rates)
        p = Path(self.td) / "to_base.csv"
        p.write_text("".join(f"{d} 12:00:00 GBP USD {v} {src}\n"
                             for d, v, src in rows))
        return get_rate_for_date("GBP", day, load_exchange_rates(p, "USD"),
                                 None)

    @rule("CA-FX-02")
    @rule("US-FX-02")
    def test_the_stale_fills_go_and_the_rule_holds(self):
        self._old_cache()
        rows, errors, notes = self._build()
        self.assertEqual(errors, [])
        dates = [r[0] for r in rows]
        self.assertEqual(dates[-2:], ["2025-01-08", "2025-01-22"])
        self.assertIsNone(self._rate_on(rows, "2025-01-20"))
        self.assertEqual(str(self._rate_on(rows, "2025-01-13")), "1.25")
        self.assertTrue(any("7-day fill limit" in n for n in notes), notes)
        saved = json.loads(self.file.read_text())
        self.assertNotIn("GBPUSD-2025-01-09", saved)
        self.assertTrue(saved["_yahoo_fills_checked"]["GBPUSD"])

    def test_a_rate_that_really_repeats_comes_back_and_stays(self):
        # A pegged rate: every day is the source's own observation.
        days = {f"2025-01-{d:02d}": 1.25 for d in range(1, 23)}
        self.file.write_text(json.dumps(dict(
            {f"GBPUSD-{d}": v for d, v in days.items()},
            _coverage={"yahoo:GBPUSD": [["2025-01-01", "2025-01-31"]]})))
        fetch = (lambda _t, a, b: {d: v for d, v in days.items()
                                   if a <= d <= b})
        rows, errors, _n = self._build(fetch=fetch)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 22)
        # Checked once: the next run leaves the run of one rate alone.
        rows, _e, notes = self._build(fetch=fetch)
        self.assertEqual(len(rows), 22)
        self.assertFalse(any("fill limit" in n for n in notes), notes)

    def test_offline_drops_them_and_asks_later(self):
        self._old_cache()
        rows, _e, notes = self._build(offline=True)
        self.assertIsNone(self._rate_on(rows, "2025-01-20"))
        self.assertTrue(any("online" in n for n in notes), notes)
        saved = json.loads(self.file.read_text())
        self.assertNotIn("_yahoo_fills_checked", saved)

    def test_a_cache_this_release_built_is_untouched(self):
        rows, _e, notes = self._build()
        self.assertFalse(any("fill limit" in n for n in notes), notes)
        rows2, _e, notes = self._build()
        self.assertEqual(rows, rows2)
        self.assertFalse(any("fill limit" in n for n in notes), notes)


if __name__ == "__main__":
    unittest.main()
