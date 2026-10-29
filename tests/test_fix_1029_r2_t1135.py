"""T1135 fixes of 2026-10-29, round 2: a dividend paid on Dec 31 that
settles in January (backlog TJ-0488), a gains file the US engine wrote
(TJ-0503), the interest a foreign debt pays (TJ-0504), a futures
contract's superficial-loss addition (TJ-0505) and a foreign issuer's
ISIN on a Canadian listing (TJ-0455).

Every fixture is SYNTHETIC: invented tickers (QZ*), invented account
names, amounts under 1,000.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.bin import taxjson_t1135 as T
from taxjson.bin.taxjson_t1135 import (build_report, main, render_brief,
                                       render_report)
from taxjson.lib import out as _out
from taxjson.lib.core import TaxTransaction
from taxjson.lib.pipeline import GainsRequest, run_gains


def tx(a, d, s, q, n=0.0, acct="margin", settle=None, **kw):
    """A base-book row (a buy's net negative, a sale's positive)."""
    return TaxTransaction(action=a, date=d, date_settle=settle or d,
                          time="10:00:00", symbol=s, quantity=q,
                          net_amount=n, currency="CAD", account=acct,
                          id=kw.pop("id", f"{a}-{d}-{s}-{q}-{acct}"), **kw)


def gains_of(rows, year=2025):
    """The Canada engine's gains file of `rows` for `year` (None: the
    whole history), as `taxjson run` writes it."""
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(list(rows), [], [], GainsRequest(
            country="canada", taxable=True, year=year, phantom_hint=False))


def report(rows, gains, year=2025, **kw):
    """build_report over one base file per account and the gains docs
    {file name: doc}; the warnings it printed in `_stderr`."""
    with tempfile.TemporaryDirectory() as td:
        by_acct = {}
        for r in rows:
            d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
            by_acct.setdefault(d.get("account") or "book", []).append(d)
        bases = []
        for acct, rs in by_acct.items():
            p = Path(td) / f"{acct}_base.json"
            p.write_text(json.dumps({"transactions": rs}))
            bases.append(p)
        gpaths = []
        for name, doc in gains.items():
            p = Path(td) / name
            p.write_text(json.dumps(doc))
            gpaths.append(p)
        kw.setdefault("today", "2026-10-29")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                mock.patch.object(T, "_DETAILS", True):
            rep = build_report(bases, gpaths, year, kw.pop("overrides", {}),
                               "CAD", **kw)
        rep["_stderr"] = " ".join(err.getvalue().split())
        return rep


def props(rep):
    return {p["symbol"]: p for p in rep["properties"]}


# ============================================== TJ-0488 income dating
class TestIncomeDatedWhenPaid(unittest.TestCase):
    """A dividend's year is the year it is paid (its `date`, or the
    gains file's `income_date`), never its settlement date: a Dec 31
    payment the export settles on Jan 2 was dropped from the INCOME
    column while the gains file counted it."""

    BOOK = [tx("BUYSELL", "2025-01-02", "QZD.US", 10, -500.0),
            tx("DIVIDEND", "2025-12-31", "QZD.US", 0, 100.0,
               settle="2026-01-02", type="dividend")]

    @rule("CA-INC-DATE-DIV")
    def test_a_dec_31_dividend_settling_in_january(self):
        g = gains_of(self.BOOK)
        self.assertEqual([round(float(e.get("dividend") or 0), 2)
                          for e in g["transactions"]
                          if e.get("action") == "DIVIDEND"], [100.0])
        rep = report(self.BOOK, {"margin_gains.json": g})
        self.assertEqual(props(rep)["QZD.US"]["income"], 100.0)
        self.assertEqual(rep["by_country"]["USA"]["income"], 100.0)

    @rule("CA-INC-DATE-DIV")
    def test_a_whole_history_gains_file_dates_it_by_its_payment(self):
        # A hand-run gains file (no year): the tool's own year filter.
        g = gains_of(self.BOOK, year=None)
        for year, want in ((2025, 100.0), (2026, 0.0)):
            with self.subTest(year=year):
                rep = report(self.BOOK, {"margin_gains.json": g},
                             year=year)
                self.assertEqual(props(rep)["QZD.US"]["income"], want)

    @rule("CA-INC-DATE-TRUST")
    def test_the_gains_files_income_date_wins(self):
        # The run dates a Canadian trust's distribution by its record
        # date (`income_date`); the T1135 column reads that year too.
        g = {"transactions": [
            {"action": "DIVIDEND", "symbol": "QZD.US", "date": "2026-01-15",
             "date_settle": "2026-01-15", "income_date": "2025-12-31",
             "dividend": 40.0, "account": "margin"}]}
        for year, want in ((2025, 40.0), (2026, 0.0)):
            with self.subTest(year=year):
                rep = report(self.BOOK, {"margin_gains.json": g},
                             year=year)
                self.assertEqual(props(rep)["QZD.US"]["income"], want)


# ============================================== TJ-0503 a US gains file
class TestUSEngineGainsRefused(unittest.TestCase):
    """A Canadian T1135 joined a US-engine gains file (FIFO gains, a
    short/long-term term on each sale) to Canadian average-cost pools:
    it is refused, naming the file."""

    BOOK = [tx("BUYSELL", "2025-01-02", "QZU.US", 10, -100.0),
            tx("BUYSELL", "2025-03-03", "QZU.US", 10, -300.0),
            tx("BUYSELL", "2025-06-02", "QZU.US", -10, 250.0)]
    # As the US engine writes it: FIFO (the first 10 at 100.00), a term.
    US_GAINS = {"transactions": [
        {"action": "BUYSELL", "symbol": "QZU.US", "date": "2025-06-02",
         "date_settle": "2025-06-03", "qty": 10, "cost": 100.0,
         "proceeds": 250.0, "gain": 150.0, "term": "SHORT_TERM",
         "account": "margin"}],
        "summary": {"year": "2025", "tax_date_basis": "trade"}}

    @rule("CA-RPT-01")
    def test_the_report_refuses_it(self):
        with self.assertRaises(T.WrongCountryGains) as cm:
            report(self.BOOK, {"margin_gains.json": self.US_GAINS})
        msg = str(cm.exception.code)
        self.assertIn("margin_gains.json", msg)
        self.assertIn("US engine", msg)
        self.assertIn("country=canada", msg)

    @rule("CA-RPT-01")
    def test_the_tool_exits_2_naming_it(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                r.to_dict() for r in self.BOOK]}))
            gains = Path(td) / "margin_gains.json"
            gains.write_text(json.dumps(self.US_GAINS))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = main([str(base), "--gains", str(gains), "--year",
                           "2025", "--option-premium-timing", "close",
                           "--json"])
        self.assertEqual(rc, 2)
        self.assertEqual(out.getvalue(), "")
        text = " ".join(err.getvalue().split())
        self.assertIn("margin_gains.json", text)
        self.assertIn("short/long-term", text)

    @rule("CA-RPT-01")
    def test_a_canadian_gains_file_is_read(self):
        g = gains_of(self.BOOK)
        rep = report(self.BOOK, {"margin_gains.json": g})
        row = props(rep)["QZU.US"]
        # Average cost: 20 at 400.00, 10 sold at 200.00.
        self.assertEqual((row["gain"], row["year_end_cost"]), (50.0, 200.0))


# ============================================== TJ-0504 a debt's interest
class TestInterestOfAProperty(unittest.TestCase):
    """The interest a foreign property pays (an INTEREST row naming its
    symbol: a bond's coupon) is its income; interest on the account's
    cash names no property and is not a row."""

    BOOK = [tx("BUYSELL", "2024-11-01", "QZB.US", 10, -500.0),
            tx("INTEREST", "2025-06-02", "QZB.US", 0, 100.0,
               type="interest"),
            tx("INTEREST", "2024-12-02", "QZB.US", 0, 70.0,
               type="interest"),
            tx("INTEREST", "2025-06-02", "CASH", 0, 30.0, type="interest"),
            tx("INTEREST", "2025-07-02", "", 0, 20.0, type="interest")]

    @rule("CA-RPT-14")
    def test_a_category_3_debt_shows_its_interest(self):
        g = gains_of(self.BOOK)
        rep = report(self.BOOK, {"margin_gains.json": g},
                     categories={"QZB.US": 3})
        self.assertEqual(sorted(props(rep)), ["QZB.US"])
        row = props(rep)["QZB.US"]
        self.assertEqual((row["category"], row["max_cost"], row["income"]),
                         (3, 500.0, 100.0))
        self.assertEqual(rep["by_country"]["USA"]["income"], 100.0)

    @rule("CA-RPT-14")
    def test_the_interest_of_a_year_it_is_paid_in(self):
        g = gains_of(self.BOOK, year=2024)
        rep = report(self.BOOK, {"margin_gains.json": g}, year=2024)
        self.assertEqual(props(rep)["QZB.US"]["income"], 70.0)

    @rule("CA-RPT-14")
    def test_interest_with_its_dividends(self):
        rows = self.BOOK + [tx("DIVIDEND", "2025-09-02", "QZB.US", 0, 5.0,
                               type="dividend")]
        rep = report(rows, {"margin_gains.json": gains_of(rows)})
        self.assertEqual(props(rep)["QZB.US"]["income"], 105.0)

    @rule("CA-RPT-14")
    def test_a_domestic_bonds_interest_is_not_a_row(self):
        rows = [tx("BUYSELL", "2025-01-02", "QZC.TO", 10, -500.0),
                tx("INTEREST", "2025-06-02", "QZC.TO", 0, 100.0,
                   type="interest")]
        rep = report(rows, {"margin_gains.json": gains_of(rows)})
        self.assertEqual(rep["properties"], [])


# ============================================== TJ-0505 futures
class TestFuturesSuperficialLoss(unittest.TestCase):
    """A futures contract costs nil to open, but a superficial loss
    denied on it is added to the replacement's ACB (s.53(1)(f)): that
    addition is the contract's cost amount. (A fill's money as the
    parsers give it to lib/futures: a buy's cost positive.)"""

    @staticmethod
    def book(rows):
        from taxjson.lib.futures import settle_futures
        return settle_futures(rows, "average")[0]

    @rule("CA-RPT-12")
    def test_the_denied_loss_is_the_replacements_cost(self):
        rows = self.book([
            tx("BUYSELL", "2025-01-06", "F:QZH6.US", 1, 100.0, price=100),
            tx("BUYSELL", "2025-03-03", "F:QZH6.US", -1, 50.0, price=50),
            tx("BUYSELL", "2025-03-05", "F:QZH6.US", 1, 100.0,
               price=100)])
        g = gains_of(rows)
        (inv,) = g["inventory_year_end"]
        self.assertEqual((inv["total_cost"], inv["deferred_wash"]),
                         (50.0, 50.0))
        rep = report(rows, {"margin_gains.json": g})
        row = props(rep)["F:QZH6.US"]
        self.assertEqual((row["country"], row["category"], row["max_cost"],
                          row["year_end_cost"], row["futures"]),
                         ("USA", 6, 50.0, 50.0, True))
        self.assertEqual((rep["max_total_cost"], rep["max_total_date"]),
                         (50.0, "2025-03-05"))
        self.assertEqual(rep["futures_symbols"], ["F:QZH6.US"])
        text = " ".join(render_report(rep).split())
        self.assertIn("F:QZH6.US: futures — no cost of its own; its cost "
                      "amount is a superficial loss added to it", text)

    @rule("CA-RPT-12")
    def test_a_contract_with_no_addition_costs_nil(self):
        rows = self.book([
            tx("BUYSELL", "2025-01-06", "F:QZH6.US", 2, 200.0, price=100),
            tx("BUYSELL", "2025-03-03", "F:QZH6.US", -1, 120.0,
               price=120)])
        rep = report(rows, {"margin_gains.json": gains_of(rows)})
        self.assertEqual(rep["max_total_cost"], 0.0)
        row = props(rep)["F:QZH6.US"]
        self.assertEqual((row["max_cost"], row["year_end_cost"],
                          row["gain"], row["futures"]),
                         (0.0, 0.0, 20.0, True))
        self.assertIn("F:QZH6.US: futures — cost amount nil",
                      " ".join(render_report(rep).split()))

    @rule("CA-RPT-12")
    def test_a_book_still_carrying_the_notional(self):
        # A book not on the settlement basis (the standalone tool fed a
        # raw one): the notional never becomes a cost amount.
        rows = [tx("BUYSELL", "2025-01-06", "F:QZH6.US", 1, 100.0),
                tx("BUYSELL", "2025-03-03", "F:QZH6.US", -1, 50.0),
                tx("BUYSELL", "2025-03-05", "F:QZH6.US", 1, 100.0)]
        rep = report(rows, {"margin_gains.json": gains_of(rows)})
        self.assertEqual(rep["max_total_cost"], 50.0)
        self.assertEqual(props(rep)["F:QZH6.US"]["year_end_cost"], 50.0)


# ============================================== TJ-0455 a foreign ISIN
class TestForeignIsinOnACanadianListing(unittest.TestCase):
    """A foreign issuer's shares bought on a Canadian exchange are
    foreign property wherever listed; the listing alone says Canadian,
    so when the rows carry a foreign ISIN (IB stamps issuer_country) the
    symbol is named for a `T1135 SYMBOL COUNTRY` line, as the mirror
    case (a Canadian ISIN on a foreign listing) is."""

    BOOK = [tx("BUYSELL", "2025-02-01", "QZF.TO", 10, -900.0),
            tx("DIVIDEND", "2025-06-17", "QZF.TO", 0, 20.0,
               type="dividend", issuer_country="US")]

    @rule("CA-RPT-02")
    def test_it_is_named_with_the_line_to_add(self):
        rep = report(self.BOOK, {"margin_gains.json": gains_of(self.BOOK)})
        self.assertEqual(rep["properties"], [])
        self.assertEqual(rep["foreign_issuer_symbols"], ["QZF.TO"])
        self.assertEqual(rep["foreign_issuer_countries"], {"QZF.TO": "USA"})
        self.assertIn("1 symbol(s) on a Canadian listing carry a foreign "
                      "ISIN: QZF.TO (USA)", rep["_stderr"])
        self.assertIn("`T1135 QZF.TO USA`", rep["_stderr"])
        acts = _out.act_lines(render_brief(rep))
        self.assertTrue(any("QZF.TO" in a and "foreign-ISIN" in a
                            for a in acts), acts)
        self.assertIn("QZF.TO", " ".join(render_report(rep).split()))

    @rule("CA-RPT-02")
    def test_a_line_for_it_settles_it(self):
        for ov, rows in (({"QZF.TO": "USA"}, 1), ({"QZF.TO": None}, 0)):
            with self.subTest(ov=ov):
                rep = report(self.BOOK,
                             {"margin_gains.json": gains_of(self.BOOK)},
                             overrides=ov)
                self.assertEqual(rep["foreign_issuer_symbols"], [])
                self.assertEqual(len(rep["properties"]), rows)
                self.assertNotIn("foreign ISIN", rep["_stderr"])

    @rule("CA-RPT-02")
    def test_a_canadian_isin_on_a_canadian_listing_is_not_named(self):
        rows = [dict(r.to_dict(), issuer_country="CA") for r in self.BOOK]
        rep = report(rows, {"margin_gains.json": {"transactions": []}})
        self.assertEqual(rep["foreign_issuer_symbols"], [])

    @rule("CA-RPT-02")
    def test_the_issuers_country_from_tobase_map_needs_no_warning(self):
        rep = report(self.BOOK, {"margin_gains.json": gains_of(self.BOOK)},
                     issuer_countries={"QZF.TO": "USA"})
        self.assertEqual(rep["foreign_issuer_symbols"], [])
        self.assertEqual(props(rep)["QZF.TO"]["max_cost"], 900.0)


if __name__ == "__main__":
    unittest.main()
