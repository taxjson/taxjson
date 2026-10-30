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
"""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.bin import taxjson_convert_tt as CT
from taxjson.bin import taxjson_t1135 as T
from taxjson.lib import t1135_cash as TC
from taxjson.lib.core import TaxTransaction

REPO_ROOT = Path(__file__).resolve().parent.parent


def _parse(line, account="margin"):
    """parse_tt_line, its warnings captured: (row, stderr text)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        row = CT.parse_tt_line(line, account)
    return row, err.getvalue()


# ======================================= TJ-0856 a bond coupon's symbol
class TestTJ0856InterestKeepsItsProperty(unittest.TestCase):
    """TJ-0856: an INTEREST row naming a foreign bond (its coupon, the
    bond's T1135 income, CA-RPT-14) was written to .tt with no symbol
    and read back as CASH: the bond's T1135 income fell from 100 to
    0."""

    COUPON = {"action": "INTEREST", "date": "2025-06-02",
              "date_settle": "2025-06-02", "time": "10:00:00",
              "symbol": "SYN.US", "net_amount": 100.0, "currency": "CAD",
              "account": "margin", "type": "interest"}
    BOND = {"action": "BUYSELL", "date": "2024-11-01",
            "date_settle": "2024-11-01", "time": "10:00:00",
            "symbol": "SYN.US", "quantity": 10.0, "net_amount": 500.0,
            "price": 50.0, "fee": 0.0, "currency": "CAD",
            "account": "margin"}

    @rule("CA-RPT-14")
    def test_export_and_reimport_keep_the_coupons_property(self):
        line = CT.tx_to_tt_line(self.COUPON)
        self.assertTrue(line.endswith(" symbol=SYN.US"), line)
        row, err = _parse(line)
        self.assertEqual(row["symbol"], "SYN.US")
        self.assertEqual(err, "")
        rows = [dict(self.BOND), row]
        self.assertEqual(T.property_interest(rows, 2025), {"SYN.US": 100.0})

    def test_interest_on_cash_stays_without_a_symbol(self):
        for sym in ("CASH", ""):
            line = CT.tx_to_tt_line(dict(self.COUPON, symbol=sym))
            self.assertEqual(
                line, "INTEREST 2025-06-02 10:00:00 CAD 100.00000")
            self.assertEqual(_parse(line)[0]["symbol"], "CASH")

    def test_the_events_view_line_reads_back_with_its_symbol(self):
        from taxjson.bin.taxjson_run import _tx_display_line
        line = _tx_display_line(self.COUPON)
        self.assertIn("symbol=SYN.US", line)
        self.assertEqual(_parse(line)[0]["symbol"], "SYN.US")

    def test_the_symbol_is_upper_cased_and_other_tokens_refused(self):
        row, _ = _parse("INTEREST 2025-06-02 10:00:00 CAD 100 symbol=syn.us")
        self.assertEqual(row["symbol"], "SYN.US")
        for bad in ("INTEREST 2025-06-02 10:00:00 CAD 100 record=2025-06-01",
                    "INTEREST 2025-06-02 10:00:00 CAD 100 symbol=SYN.US "
                    "symbol=ZZB.US",
                    "INTEREST 2025-06-02 10:00:00 CAD 100 SYN.US"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _parse(bad)


# ======================================= TJ-0862 income keeps its paid date
class TestTJ0862IncomeKeepsItsPaidDate(unittest.TestCase):
    """TJ-0862: a settle-basis export wrote a dividend paid 2025-12-31
    (settling 2026-01-02) with its settlement date, moving its 100 into
    2026 on re-import."""

    DIV = {"action": "DIVIDEND", "date": "2025-12-31",
           "date_settle": "2026-01-02", "time": "10:00:00",
           "symbol": "SYN.US", "quantity": 10.0, "price": 0.0,
           "gross_amount": 100.0, "net_amount": 100.0, "currency": "CAD",
           "account": "margin", "type": "dividend"}
    SALE = {"action": "BUYSELL", "date": "2025-12-31",
            "date_settle": "2026-01-02", "time": "10:00:00",
            "symbol": "SYN.US", "quantity": -10.0, "price": 10.0,
            "net_amount": 100.0, "fee": 0.0, "currency": "CAD",
            "account": "margin"}

    @rule("CA-DATE-11")
    def test_settle_basis_writes_the_paid_date_of_income(self):
        for action in CT.PAID_DATE_ACTIONS:
            row = dict(self.DIV, action=action)
            if action in ("INTEREST", "FEE"):
                row["symbol"] = "CASH"
            with self.subTest(action=action):
                line = CT.tx_to_tt_line(row, date_basis="settle")
                self.assertEqual(line.split()[1], "2025-12-31", line)
                self.assertEqual(_parse(line)[0]["date"], "2025-12-31")

    def test_a_disposition_still_takes_its_settlement_date(self):
        line = CT.tx_to_tt_line(self.SALE, date_basis="settle")
        self.assertEqual(line.split()[1], "2026-01-02")
        line = CT.tx_to_tt_line(self.SALE, date_basis="trade")
        self.assertEqual(line.split()[1], "2025-12-31")

    def test_the_events_view_on_settle_dates_too(self):
        from taxjson.bin.taxjson_run import _tx_display_line
        self.assertEqual(_tx_display_line(self.DIV, settle=True).split()[1],
                         "2025-12-31")
        self.assertEqual(_tx_display_line(self.SALE, settle=True).split()[1],
                         "2026-01-02")

    def test_the_two_dates_note_counts_dispositions_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.json"
            p.write_text(json.dumps({"transactions": [self.DIV]}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                lines = list(CT.json_to_tt_lines(p, date_basis="settle"))
        self.assertEqual(len(lines), 1)
        self.assertNotIn("SETTLEMENT date", err.getvalue())


# ================================== TJ-0864 the project's MULT sizes a line
class TestTJ0864TickerMapMultSizesTheLine(unittest.TestCase):
    """TJ-0864: with `MULT SYN251219C00005000.TO 10` in ticker.map, a
    one-contract purchase at price 2 / total 20 was a typo at x100
    ("should be 200"), which `run --strict` stops on."""

    OPT = "SYN251219C00005000.TO"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        tm = Path(self._td.name) / "ticker.map"
        tm.write_text(f"MULT {self.OPT} 10\n")
        env = mock.patch.dict(os.environ, {"TAXJSON_TICKER_MAP": str(tm)})
        env.start()
        self.addCleanup(env.stop)

    def test_correct_mini_totals_are_not_typos(self):
        for line in (f"BUYSELL 2025-06-02 10:00:00 {self.OPT} 1 CAD 2 20 0",
                     f"BUYSELL 2025-06-04 10:00:00 {self.OPT} -1 CAD 3 30 0"):
            with self.subTest(line=line):
                row, err = _parse(line)
                self.assertEqual(err, "")

    def test_a_total_at_100_is_now_the_typo(self):
        _row, err = _parse(
            f"BUYSELL 2025-06-02 10:00:00 {self.OPT} 1 CAD 2 200 0")
        self.assertIn("qty*price*10+fee = 20.00", err)

    def test_the_root_and_the_underlying_name_the_size_too(self):
        tm = Path(self._td.name) / "ticker.map"
        tm.write_text("MULT SYN 10\n")
        os.utime(tm, (1, 1))           # a new mtime for the map's cache
        _row, err = _parse(
            f"BUYSELL 2025-06-02 10:00:00 {self.OPT} 1 CAD 2 20 0")
        self.assertEqual(err, "")

    def test_without_a_mult_line_the_standard_100(self):
        with mock.patch.dict(os.environ, {"TAXJSON_TICKER_MAP": ""}):
            _row, err = _parse(
                f"BUYSELL 2025-06-02 10:00:00 {self.OPT} 1 CAD 2 20 0")
        self.assertIn("qty*price*100+fee = 200.00", err)


# ================================ TJ-0662 futures settlement facts round-trip
class TestTJ0662FuturesSettlementFactsRoundTrip(unittest.TestCase):
    """TJ-0662: a settled futures book exported to .tt lost each part's
    type=futures_settlement and settle_native: re-imported, the parts
    were raw fills and the Canadian pooled gain changed (150 became 200;
    a long-and-short case 250 became 150)."""

    @staticmethod
    def _fill(i, acct, date, q, p):
        return TaxTransaction(id=i, action="BUYSELL", date=date,
                              date_settle=date, symbol="F:SYNH6.US",
                              quantity=q, currency="CAD", price=p,
                              net_amount=abs(q) * p, multiplier=1,
                              account=acct)

    def _gains(self, raw):
        from taxjson.lib.futures import settle_futures, stamp_rate
        from taxjson.lib.pipeline import GainsRequest, run_gains
        settled = []
        for a in dict.fromkeys(t.account for t in raw):
            ts = settle_futures([copy.deepcopy(t) for t in raw
                                 if t.account == a], "average")[0]
            for t in ts:
                stamp_rate(t, 1)
            settled.extend(ts)
        settled.sort(key=lambda t: t.date)
        lines = [CT.tx_to_tt_line(t.to_dict(), "trade") for t in settled]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            back = [TaxTransaction(**CT.parse_tt_line(s, t.account))
                    for s, t in zip(lines, settled)]

            def g(ts):
                return run_gains(copy.deepcopy(ts), req=GainsRequest(
                    country="canada", year=2025, taxable=True,
                    phantom_hint=False))["summary"]["total_gain"]
            return g(settled), g(back), lines, back, err.getvalue()

    @rule("CA-FX-04")
    def test_two_accounts_pooled_gain_survives(self):
        orig, back, lines, rows, err = self._gains([
            self._fill("A1", "a", "2025-01-06", 1, 100),
            self._fill("B1", "b", "2025-02-03", 1, 200),
            self._fill("A2", "a", "2025-03-03", -1, 300)])
        self.assertAlmostEqual(orig, 150.0)
        self.assertAlmostEqual(back, orig)
        self.assertTrue(all("type=futures_settlement" in ln
                            for ln in lines), lines)
        self.assertEqual(rows[2].settle_native, "LC 300.0 200.0 CAD 1")
        self.assertNotIn("differs from", err)

    @rule("CA-FX-04")
    def test_long_and_short_stay_apart(self):
        orig, back, _lines, _rows, _err = self._gains([
            self._fill("A1", "a", "2025-01-06", 1, 100),
            self._fill("B1", "b", "2025-02-03", -1, 200),
            self._fill("A2", "a", "2025-03-03", -1, 300),
            self._fill("B2", "b", "2025-04-03", 1, 150)])
        self.assertAlmostEqual(back, orig)

    def test_a_loss_close_reads_back_with_its_negative_total(self):
        row, err = _parse("BUYSELL 2025-03-03 09:30:00 F:SYNH6.US -1 CAD "
                          "50 -50 0 x1 type=futures_settlement "
                          "settle=LC,50.0,-50.0,CAD,1", "a")
        self.assertEqual(row["net_amount"], -50.0)
        self.assertEqual(row["type"], "futures_settlement")
        self.assertEqual(err, "")

    def test_malformed_settlement_tokens_are_refused(self):
        for bad in (
                # a share is not a future
                "BUYSELL 2025-03-03 09:30:00 SYN.US 1 CAD 3 3 0 "
                "type=futures_settlement",
                # the leg sells, the line buys
                "BUYSELL 2025-03-03 09:30:00 F:SYNH6.US 1 CAD 3 0 0 x1 "
                "type=futures_settlement settle=LC,3,0,CAD,1",
                # not a leg
                "BUYSELL 2025-03-03 09:30:00 F:SYNH6.US 1 CAD 3 0 0 x1 "
                "type=futures_settlement settle=XX,3,0,CAD",
                # settle= without the type is an unknown token
                "BUYSELL 2025-03-03 09:30:00 F:SYNH6.US 1 CAD 3 0 0 x1 "
                "settle=LO,3,0,CAD"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _parse(bad, "a")


def _row(i, date, sym, q, net, action="BUYSELL", time="10:00:00",
         acct="a"):
    return TaxTransaction(id=i, action=action, date=date, date_settle=date,
                          time=time, symbol=sym, quantity=q,
                          net_amount=net, gross_amount=0.0,
                          price=abs(net / q) if q else 0.0,
                          currency="CAD", account=acct)


def _report(rows, gains=True, **kw):
    """build_report over `rows` (one base file of account `a`), with the
    gains file `taxjson run` writes for 2025 (the INCOME column)."""
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
    earned is 100."""

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

    @rule("CA-RPT-14")
    def test_the_gross_income_is_the_dividends_received(self):
        rep = _report(self._rows(-30.0))
        self.assertEqual(_prop(rep, "SYN.US")["income"], 100.0)
        self.assertTrue(rep["filing_required"])
        control = _report(self._rows(0.0))
        self.assertEqual(_prop(control, "SYN.US")["income"], 100.0)

    @rule("CA-RPT-14")
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
    read 500."""

    def _rep(self, sell_date):
        rows = [_row("b1", "2025-06-02", "SYN.TO", 100, 500.0),
                _row("s1", sell_date, "SYN.TO", -100, 600.0,
                     time="12:00:00")]
        return _report(rows, custody={"a": "USA"})

    @rule("CA-RPT-01", "CA-RPT-02")
    def test_a_same_day_round_trip_counts_its_cost(self):
        same = self._rep("2025-06-02")
        self.assertEqual(same["max_total_cost"], 500.0)
        self.assertEqual(self._rep("2025-06-03")["max_total_cost"], 500.0)
        row = _prop(same, "SYN.TO")
        self.assertEqual(row["year_end_cost"], 0.0)
        self.assertEqual(row["gain"], 100.0)

    @rule("CA-RPT-02")
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
    said total_settled with no unknown cash."""

    @rule("CA-RPT-20")
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


# ========== TJ-0772 a cash document of another year or base currency
class TestTJ0772CashDocumentMustBeThisReports(unittest.TestCase):
    """TJ-0772: taxjson-t1135 --year 2025 --base-currency CAD read a
    cash document of 2024, or one in US dollars, as this year's Canadian
    dollars (only its date basis was checked)."""

    def _main(self, **doc_kw):
        doc = {"version": TC.VERSION, "year": 2025, "currency": "CAD",
               "basis": "settle", "ledger_status": "computed",
               "accounts": {"a": "USA"},
               "series": [{"account": "a", "currency": "EUR",
                           "country": "USA",
                           "points": [["2025-01-01", 100.0, 156.0]],
                           "unknown": False, "why": ""}],
               "not_counted": [], "stablecoins": [], "interest": {}}
        doc.update(doc_kw)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "a_base.json"
            base.write_text(json.dumps({"transactions": [
                _row("b", "2025-06-02", "SYN.US", 1, 150.0).to_dict()]}))
            cash = Path(td) / "cash.json"
            cash.write_text(json.dumps(doc))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = T.main([str(base), "--year", "2025",
                             "--base-currency", "CAD", "--json",
                             "--option-premium-timing", "close",
                             "--cash", str(cash)])
        return rc, out.getvalue(), err.getvalue()

    def test_another_years_document_is_refused(self):
        rc, _out, err = self._main(year=2024)
        self.assertEqual(rc, 2)
        self.assertIn("is for the year 2024, the report for 2025", err)

    def test_another_base_currencys_document_is_refused(self):
        rc, _out, err = self._main(currency="USD")
        self.assertEqual(rc, 2)
        self.assertIn("has its costs in USD, the report in CAD", err)

    def test_this_reports_document_is_read(self):
        rc, out, err = self._main()
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["max_total_cost"], 306.0)


if __name__ == "__main__":
    unittest.main()
