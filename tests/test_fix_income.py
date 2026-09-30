"""Partition Phase C (area income): the owner's decisions D3, D4, D5, D6
and D8 on income character and dating, each run as ONE synthetic book
under both countries (lib/income_dating is the one place the rules
live; tax-logic states them).

- D3  CA-INC-03: a payment in lieu on a Canadian issuer's share paid by
      a Canadian dealer is a taxable dividend (ITA s.260); US-INC-01:
      a substitute payment is ordinary income.
- D4  CA-INC-DATE-ROC-TRUST: a Canadian trust's return of capital lowers
      the ACB on its record date (s.53(2)(h)); corporations, foreign
      issuers and the US: the pay date. IB (no record date): a warning.
- D5  CA-INC-DATE-TRUST: a Canadian trust's distribution is income of
      the record-date year (s.104(13)); split-share corporations and
      foreign funds: the pay date. US: the pay date.
- D6  CA-DIST-02/03, US-DIST-02/03: RBC notional distributions and DRIP.
- D8  US-INC-DATE-RIC: a January fund/REIT dividend with an Oct-Dec
      ex/record date is warned about; ric_january_dividends moves it to
      Dec 31. Canada: never.

All data is synthetic (fake account ids, invented tickers).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import country as C
from taxjson.lib.income_dating import IncomeRules, SPLIT_SHARE_ROOTS
from tax_rules import rule, rule_absent
from tax_rules.dual import cli, cli_both, gains_both, projects_both, tx

ACCT = "55500001"  # pii-ok (synthetic)


def gains_one(book, country, **req):
    """One country's run_gains (for a test tagged with that country's
    rules only: the other engine must not run)."""
    import copy
    import contextlib
    import io
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)), [], [],
                        req=GainsRequest(country=country, taxable=True,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _income(res, action=None):
    return [t for t in res["transactions"]
            if t.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU")
            and (action is None or t["action"] == action)]


def _gains(res):
    return [round(t["gain"], 2) for t in res["transactions"]
            if t.get("action") not in ("DIVIDEND", "DIVIDEND_IN_LIEU")
            and "gain" in t]


# ------------------------------------------------------------ D3 PIL
class TestPaymentInLieu(unittest.TestCase):

    def _book(self, sym="ZQX.TO", dealer="CA", issuer="", cur="CAD"):
        return [tx("BUYSELL", "2025-02-03", sym, 100, 1000, currency=cur),
                tx("DIVIDEND_IN_LIEU", "2025-06-30", sym, 0, 40.0,
                   gross_amount=40.0, type="dividend_in_lieu",
                   currency=cur, dealer_country=dealer,
                   issuer_country=issuer)]

    @rule("CA-INC-03")
    @rule_absent("CA-INC-03", country="usa")
    @rule("US-INC-01")
    def test_canadian_dealer_canadian_issuer_is_a_dividend_in_canada(self):
        r = gains_both(self._book(), year=2025)
        (ca,) = _income(r["canada"])
        self.assertEqual(ca["action"], "DIVIDEND_IN_LIEU")
        self.assertAlmostEqual(ca["dividend"], 40.0)
        self.assertAlmostEqual(ca["pil"], 0.0)
        self.assertEqual(ca["deemed_dividend"], "ITA s.260")
        (us,) = _income(r["usa"])
        self.assertAlmostEqual(us["pil"], 40.0)
        self.assertNotIn("dividend", us)
        self.assertNotIn("deemed_dividend", us)
        # The aggregates the estimate / sum / close-year read.
        from taxjson.bin.taxjson_sum_gains import summarize_gains
        from taxjson.bin.taxjson_filed import aggregates_from_gains
        s_ca = summarize_gains(r["canada"])["ticker_stats"]["ZQX.TO"]["CAD"]
        s_us = summarize_gains(r["usa"])["ticker_stats"]["ZQX.TO"]["CAD"]
        self.assertEqual((s_ca["div"], s_ca["pil"]), (40.0, 0.0))
        self.assertEqual((s_us["div"], s_us["pil"]), (0.0, 40.0))
        a_ca, a_us = (aggregates_from_gains(r[c]) for c in C.COUNTRIES)
        self.assertEqual((a_ca["dividend"], a_ca["pil"]), (40.0, 0.0))
        self.assertEqual((a_us["dividend"], a_us["pil"]), (0.0, 40.0))
        # by_ticker (rebuilt after the year filter) agrees.
        self.assertAlmostEqual(
            r["canada"]["by_ticker"]["ZQX.TO"]["total_div"], 40.0)

    @rule("CA-INC-03")
    def test_other_payments_in_lieu_stay_ordinary_in_canada(self):
        for label, book in (
                ("foreign dealer", self._book(dealer="US")),
                ("dealer unknown", self._book(dealer="")),
                ("foreign issuer", self._book(sym="ZQX.US", cur="USD")),
                ("US ISIN on a .TO listing",
                 self._book(issuer="US"))):
            with self.subTest(label):
                (ca,) = _income(gains_one(book, "canada", year=2025))
                self.assertAlmostEqual(ca["pil"], 40.0)
                self.assertNotIn("deemed_dividend", ca)
        # A CA ISIN on a US listing is a Canadian issuer.
        (ca,) = _income(gains_one(self._book(sym="ZQX.US", cur="USD",
                                             issuer="CA"),
                                  "canada", year=2025))
        self.assertEqual(ca.get("deemed_dividend"), "ITA s.260")

    @rule("CA-INC-03")
    @rule_absent("CA-INC-03", country="usa")
    @rule("US-INC-01")
    def test_views_and_sum_income(self):
        base = {"transactions": [t.to_dict() for t in self._book()]}
        files = {"work/margin_raw.json": json.dumps(base),
                 "work/margin_base.json": json.dumps(base)}
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, files=files)
            divs = cli_both(p, "divs-sum", "--json")
            dil = cli_both(p, "dil-sum", "--json")
        for c in C.COUNTRIES:
            self.assertEqual(divs[c].returncode, 0, divs[c].stderr)
            self.assertEqual(dil[c].returncode, 0, dil[c].stderr)
        d_ca, d_us = (json.loads(divs[c].stdout) for c in C.COUNTRIES)
        self.assertEqual(d_ca["totals"], {"CAD": 40.0})
        self.assertEqual(d_ca["payments_in_lieu_as_dividends"], 1)
        self.assertEqual(d_us["totals"], {})
        l_ca, l_us = (json.loads(dil[c].stdout) for c in C.COUNTRIES)
        self.assertEqual(l_ca["rows"][0]["treatment"], "dividend")
        self.assertEqual(l_ca["totals_ordinary"], {})
        self.assertEqual(l_us["rows"][0]["treatment"], "ordinary")
        self.assertEqual(l_us["totals_ordinary"], {"CAD": 40.0})
        from taxjson.bin.taxjson_sum_income import summarize_income
        rows = base["transactions"]
        s = {c: summarize_income(rows, 2025, IncomeRules(c))["ticker_stats"]
             ["ZQX.TO"]["CAD"] for c in C.COUNTRIES}
        self.assertEqual((s["canada"]["div"], s["canada"]["pil"]),
                         (40.0, 0.0))
        self.assertEqual((s["usa"]["div"], s["usa"]["pil"]), (0.0, 40.0))
        # No rules (standalone, no --country): the neutral column.
        s0 = summarize_income(rows, 2025)["ticker_stats"]["ZQX.TO"]["CAD"]
        self.assertEqual((s0["div"], s0["pil"]), (0.0, 40.0))

    @rule("CA-INC-03")
    @rule("US-INC-01")
    def test_ib_statement_names_the_dealer_and_issuer(self):
        """The IB parser records the neutral facts (the statement's
        BrokerName, the ISIN's country); it decides nothing."""
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        body = ('Dividends,Header,Currency,Account,Date,Description,'
                'Amount\n'
                'Dividends,Data,CAD,U5550001,2025-06-30,'  # pii-ok
                'ZQX(CA0000000019) Payment in Lieu of Dividend '
                '(Ordinary Dividend),40.00\n')  # pii-ok
        for broker, want in (("Interactive Brokers Canada Inc.", "CA"),
                             ("Interactive Brokers LLC", "US"),
                             ("", None)):
            stmt = ('Statement,Header,Field Name,Field Value\n'
                    + (f'Statement,Data,BrokerName,"{broker}"\n'
                       if broker else ''))
            with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                             delete=False) as f:
                f.write(stmt + body)
            try:
                import contextlib
                import io
                with contextlib.redirect_stderr(io.StringIO()):
                    (t,) = IbBrokerage().parse_file(Path(f.name))
            finally:
                os.remove(f.name)
            with self.subTest(broker=broker):
                self.assertEqual(t["action"], "DIVIDEND_IN_LIEU")
                self.assertEqual(t.get("dealer_country"), want)
                self.assertEqual(t.get("issuer_country"), "CA")


# ------------------------------------------------------------ D4 ROC dating
class TestReturnOfCapitalDating(unittest.TestCase):
    """S056-04: buy 1000 at 10 in 2024, sell all 2024-12-30 (settles
    12-31), a 0.50/unit ROC with record date 2024-12-30 paid
    2025-01-08."""

    def _book(self, sym="ZZR.TO", record="2024-12-30", **kw):
        roc = tx("ADJUST", "2025-01-08", sym, 0, -500.0, type="roc",
                 currency="CAD", record_date=record,
                 description=f"RETURN OF CAPITAL REC {record}", **kw)
        return [tx("BUYSELL", "2024-06-03", sym, 1000, 10000,
                   currency="CAD", settle="2024-06-04"),
                tx("BUYSELL", "2024-12-30", sym, -1000, 10000,
                   currency="CAD", settle="2024-12-31"),
                roc]

    @rule("CA-INC-DATE-ROC-TRUST")
    @rule_absent("CA-INC-DATE-ROC-TRUST", country="usa")
    @rule("US-INC-DATE-ROC")
    def test_trust_roc_lowers_the_acb_on_its_record_date(self):
        r24 = gains_both(self._book(), year=2024)
        r25 = gains_both(self._book(), year=2025)
        # Canada: ACB 9,500 at the sale — a 500 gain in 2024, nothing in
        # 2025 (was: 0 in 2024 and an s.40(3) deemed gain of 500 in 2025).
        self.assertEqual(_gains(r24["canada"]), [500.0])
        self.assertEqual(_gains(r25["canada"]), [])
        # US: the pay date — the sale is at full basis; the ROC arrives
        # with no shares held (US-ROC-03: not booked, warned).
        self.assertEqual(_gains(r24["usa"]), [0.0])
        self.assertEqual(_gains(r25["usa"]), [])

    @rule("CA-INC-DATE-ROC")
    def test_corporations_and_foreign_issuers_use_the_pay_date(self):
        for sym, kw, canada in (
                ("FTN.TO", {}, {}),                     # split-share corp
                ("ZZR.TO", {}, {"corporate_distributions": ("ZZR.TO",)}),
                ("ZZR.TO", {"issuer_country": "US"}, {}),
                ("ZZR.US", {}, {})):
            with self.subTest(sym=sym, kw=kw, canada=canada):
                r = gains_one(self._book(sym, **kw), "canada", year=2024,
                              **canada)
                self.assertEqual(_gains(r), [0.0])

    @rule("CA-INC-DATE-ROC-TRUST")
    @rule_absent("CA-INC-DATE-ROC-TRUST", country="usa")
    def test_ib_january_roc_on_a_trust_is_warned_about(self):
        book = self._book(record="")
        book[1] = tx("BUYSELL", "2025-03-03", "ZZR.TO", -1000, 10000,
                     currency="CAD")
        r = gains_both(book, year=2025)
        self.assertIn("ZZR.TO: return of capital 500.00 CAD paid "
                      "2025-01-08 with no record date", r["canada"]["_stderr"])
        self.assertIn("T3 box 42", r["canada"]["_stderr"])
        self.assertIn("ADJUST 2024-12-31 12:00:00 ZZR.TO CAD -500.00",
                      r["canada"]["_stderr"])
        self.assertNotIn("no record date", r["usa"]["_stderr"])
        # Pay date in both (the ROC lowers the cost before the sale).
        self.assertEqual(_gains(r["canada"]), [500.0])

    @rule("CA-INC-DATE-ROC-TRUST")
    @rule("US-INC-DATE-ROC")
    def test_questrade_and_rbc_record_the_record_date(self):
        from test_fix_rbcqt import qdiv, qt_parse, rrow, rbc_parse
        txs, _, _ = qt_parse(qdiv(
            "ZZR.TO", "ZZR TRUST RETURN OF CAPITAL ON 1000 SHS REC 12/30/24 "
            "PAY 01/08/25", "500.00", td="2025-01-08", cur="CAD"))
        (t,) = [t for t in txs if t["action"] == "ADJUST"]
        self.assertEqual((t["date"], t["record_date"]),
                         ("2025-01-08", "2024-12-30"))
        txs, _, _ = rbc_parse(rrow(
            "January 8, 2025", "Return of Capital", "ZZR", "ZZR TRUST",
            "", "", "500", "CAD",
            "ZZR TRUST RETURN OF CAPITAL ON 1000 SHS REC 12/30/24 "
            "PAY 01/08/25"))
        (t,) = [t for t in txs if t["action"] == "ADJUST"]
        self.assertEqual(t["record_date"], "2024-12-30")

    @rule("CA-INC-DATE-ROC-TRUST")
    @rule_absent("CA-INC-DATE-ROC-TRUST", country="usa")
    def test_roc_sum_counts_the_record_year(self):
        roc = self._book()[2].to_dict()
        base = {"transactions": [roc]}
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2024, files={
                "work/margin_raw.json": json.dumps(base)})
            r = cli_both(p, "roc-sum", "--json")
        got = {c: json.loads(r[c].stdout) for c in C.COUNTRIES}
        self.assertEqual(got["canada"]["totals"], {"CAD": 500.0})
        self.assertEqual(got["usa"]["totals"], {})


# ------------------------------------------------------------ D5 trusts
class TestTrustDistributionYear(unittest.TestCase):

    def _book(self, sym="XIC.TO", label="distribution", record="2024-12-30",
              pay="2025-01-06", **kw):
        return [tx("BUYSELL", "2024-06-03", sym, 500, 15000,
                   currency="CAD"),
                tx("DIVIDEND", pay, sym, 0, 143.87, gross_amount=143.87,
                   type="dividend", currency="CAD", record_date=record,
                   income_label=label,
                   description=f"DIST ON 500 SHS REC {record} PAY {pay}",
                   **kw)]

    def _years(self, book, **kw):
        out = {}
        for y in (2024, 2025):
            r = gains_both(book, year=y, **kw)
            for c in C.COUNTRIES:
                if _income(r[c]):
                    out.setdefault(c, []).append(y)
        return out

    @rule("CA-INC-DATE-TRUST")
    @rule_absent("CA-INC-DATE-TRUST", country="usa")
    @rule("US-INC-DATE-DIV")
    def test_trust_distribution_is_income_of_the_record_year(self):
        self.assertEqual(self._years(self._book()),
                         {"canada": [2024], "usa": [2025]})
        # A CA ISIN on a US listing is a Canadian trust too.
        self.assertEqual(
            self._years(self._book(sym="ZZU.US", issuer_country="CA")),
            {"canada": [2024], "usa": [2025]})

    @rule("CA-INC-DATE-TRUST")
    @rule("CA-INC-DATE-DIV")
    def test_pay_date_for_corporations_foreign_funds_and_no_record(self):
        for label, book, canada in (
                ("split-share corp", self._book(sym="FTN.PR.A.TO"), {}),
                ("project list", self._book(),
                 {"corporate_distributions": ("XIC.TO",)}),
                ("project list, root", self._book(),
                 {"corporate_distributions": ("XIC",)}),
                ("corporate dividend label", self._book(label=""), {}),
                ("US fund 'DIST'", self._book(sym="IWM.US"), {}),
                ("no record date (IB)", self._book(record=""), {})):
            with self.subTest(label):
                got = [y for y in (2024, 2025) if _income(
                    gains_one(book, "canada", year=y, **canada))]
                self.assertEqual(got, [2025])

    @rule("CA-INC-DATE-TRUST")
    @rule_absent("CA-INC-DATE-TRUST", country="usa")
    def test_divs_sum_and_the_sum_income_section(self):
        base = {"transactions": [t.to_dict() for t in self._book()]}
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2024, files={
                "work/margin_raw.json": json.dumps(base)})
            r24 = cli_both(p, "divs-sum", "--json")
        got = {c: json.loads(r24[c].stdout)["totals"] for c in C.COUNTRIES}
        self.assertEqual(got, {"canada": {"CAD": 143.87}, "usa": {}})
        from taxjson.bin.taxjson_sum_income import summarize_income
        rows = base["transactions"]
        for c, y in (("canada", 2024), ("usa", 2025)):
            with self.subTest(country=c):
                s = summarize_income(rows, y, IncomeRules(c))
                self.assertIn("XIC.TO", s["ticker_stats"])

    @rule("CA-INC-DATE-TRUST")
    def test_parsers_record_the_facts(self):
        from test_fix_rbcqt import qdiv, qt_parse, rrow, rbc_parse
        txs, _, _ = qt_parse(qdiv(
            "XIC.TO", "ISHARES CORE S&P/TSX DIST ON 512 SHS REC 12/30/24 "
            "PAY 01/06/25", "143.87", td="2025-01-06", cur="CAD"))
        (t,) = txs
        self.assertEqual((t["action"], t["date"], t["record_date"],
                          t["income_label"]),
                         ("DIVIDEND", "2025-01-06", "2024-12-30",
                          "distribution"))
        txs, _, _ = qt_parse(qdiv(
            "CSU.TO", "CONSTELLATION CASH DIV ON 10 SHS REC 12/19/24 "
            "PAY 01/10/25", "10.00", td="2025-01-10", cur="CAD"))
        self.assertEqual(txs[0]["record_date"], "2024-12-19")
        self.assertNotIn("income_label", txs[0])
        txs, _, _ = rbc_parse(rrow(
            "January 8, 2025", "Distribution", "HDIV", "HAMILTON ETF", "",
            "0.17", "513", "CAD",
            "HAMILTON ETF DIST      ON    3000 SHS REC 12/31/24 "
            "PAY 01/08/25"))
        (t,) = [t for t in txs if t["action"] == "DIVIDEND"]
        self.assertEqual((t["record_date"], t["income_label"]),
                         ("2024-12-31", "distribution"))

    def test_split_share_list_is_small_and_documented(self):
        from taxjson.lib import tax_logic as TL
        text = TL.render("canada", {})
        for root in SPLIT_SHARE_ROOTS:
            self.assertIn(root, text)
        self.assertLessEqual(len(SPLIT_SHARE_ROOTS), 20)


# ------------------------------------------------------------ D8 US RIC
class TestUsJanuaryFundDividends(unittest.TestCase):

    def _book(self, ex="2024-12-20"):
        return [tx("BUYSELL", "2024-06-03", "SPY.US", 100, 50000),
                tx("DIVIDEND", "2025-01-31", "SPY.US", 0, 196.60,
                   gross_amount=196.60, type="dividend", ex_date=ex)]

    def _years(self, book, **kw):
        out = {}
        for y in (2024, 2025):
            r = gains_both(book, year=y, **kw)
            for c in C.COUNTRIES:
                if _income(r[c]):
                    out.setdefault(c, []).append(y)
        return out

    @rule("US-INC-DATE-RIC")
    @rule_absent("US-INC-DATE-RIC", country="canada")
    @rule("CA-INC-DATE-DIV")
    def test_january_dividend_with_a_december_ex_date_is_warned(self):
        r = gains_both(self._book(), year=2025)
        self.assertIn("SPY.US: dividend 196.60 USD paid 2025-01-31 with an "
                      "ex/record date of 2024-12-20", r["usa"]["_stderr"])
        self.assertIn("§852(b)(7)", r["usa"]["_stderr"])
        self.assertNotIn("§852", r["canada"]["_stderr"])
        # Pay date without the list, in both countries.
        self.assertEqual(self._years(self._book()),
                         {"canada": [2025], "usa": [2025]})
        # No warning for a January ex date.
        r = gains_both(self._book(ex="2025-01-15"), year=2025)
        self.assertNotIn("§852", r["usa"]["_stderr"])

    @rule("US-INC-DATE-RIC")
    @rule_absent("US-INC-DATE-RIC", country="canada")
    def test_listed_payments_move_to_december_31(self):
        for spec in ("SPY.US", "SPY.US 2025-01-31", "spy"):
            with self.subTest(spec=spec):
                self.assertEqual(
                    self._years(self._book(),
                                usa={"ric_january_dividends": (spec,)}),
                    {"canada": [2025], "usa": [2024]})
                r = gains_both(self._book(), year=2024,
                               usa={"ric_january_dividends": (spec,)})
                (e,) = _income(r["usa"])
                self.assertEqual(e["income_date"], "2024-12-31")
                self.assertNotIn("§852", r["usa"]["_stderr"])
        # Another pay date is not the listed payment.
        self.assertEqual(
            self._years(self._book(), usa={"ric_january_dividends": (
                "SPY.US 2025-01-30",)})["usa"], [2025])

    @rule("US-INC-DATE-RIC")
    @rule_absent("US-INC-DATE-RIC", country="canada")
    def test_the_list_is_us_only_and_checked(self):
        from taxjson.lib.pipeline import GainsRequest
        with self.assertRaises(ValueError):
            GainsRequest(country="canada",
                         ric_january_dividends=("SPY.US",))
        with self.assertRaises(ValueError):
            GainsRequest(country="usa",
                         corporate_distributions=("XIC.TO",))
        with self.assertRaises(ValueError):
            GainsRequest(country="usa",
                         ric_january_dividends=("SPY.US 2025-02-28",))
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, canada={"ric_january_dividends":
                                          ["SPY.US"]},
                              usa={"ric_january_dividends": ["SPY.US"]},
                              files={"work/margin_gains.json": json.dumps(
                                  {"summary": {"year": "2025"},
                                   "transactions": []})})
            r = cli_both(p, "sum", "--json")
        self.assertNotEqual(r["canada"].returncode, 0)
        self.assertIn("ric_january_dividends is United States-only",
                      r["canada"].stderr)
        self.assertNotIn("United States-only", r["usa"].stderr)

    @rule("US-INC-DATE-RIC")
    def test_ib_accruals_give_the_ex_date(self):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        import contextlib
        import io
        body = ('Statement,Header,Field Name,Field Value\n'
                'Statement,Data,BrokerName,Interactive Brokers LLC\n'
                'Dividends,Header,Currency,Account,Date,Description,Amount\n'
                'Dividends,Data,USD,U5550001,2025-01-31,SPY(US78462F1030) '  # pii-ok
                'Cash Dividend USD 1.966 per Share (Ordinary Dividend),'
                '196.60\n'
                'Change in Dividend Accruals,Header,Asset Category,Currency,'
                'Account,Symbol,Date,Ex Date,Pay Date,Quantity,Tax,Fee,'
                'Gross Rate,Gross Amount,Net Amount,Code\n'
                'Change in Dividend Accruals,Data,Stocks,USD,U5550001,SPY,'  # pii-ok
                '2025-01-31,2024-12-20,2025-01-31,100,0,0,1.966,-196.60,'
                '-196.60,Re\n')
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False) as f:
            f.write(body)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                txs = IbBrokerage().parse_file(Path(f.name))
        finally:
            os.remove(f.name)
        (t,) = [t for t in txs if t["action"] == "DIVIDEND"]
        self.assertEqual((t["ex_date"], t["dealer_country"]),
                         ("2024-12-20", "US"))


# ------------------------------------------------------------ end to end
_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
       "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
       "Activity Type,Account Type\n")


class TestEndToEnd(unittest.TestCase):
    """`taxjson run` carries the facts through merge, conversion and the
    gains run, and the .sum / divs-sum / estimate agree."""

    @rule("CA-INC-DATE-TRUST")
    @rule_absent("CA-INC-DATE-TRUST", country="usa")
    @rule("CA-INC-DATE-ROC-TRUST")
    @rule_absent("CA-INC-DATE-ROC-TRUST", country="usa")
    @rule("US-INC-DATE-DIV")
    @rule("US-INC-DATE-ROC")
    def test_questrade_project_both_countries(self):
        cur = {"canada": "CAD", "usa": "USD"}
        with tempfile.TemporaryDirectory() as td:
            roots = {}
            for c in C.COUNTRIES:
                k = cur[c]
                csv = (_QT +
                       f"2024-06-03 09:30:00 AM,2024-06-04 12:00:00 AM,Buy,"
                       f"ZZT.TO,ZZT TRUST WE ACTED AS AGENT,1000,10.00,"
                       f"-10000.00,0.00,-10000.00,{k},{ACCT},Trades,"
                       f"Individual margin\n"
                       f"2024-12-30 09:30:00 AM,2024-12-31 12:00:00 AM,Sell,"
                       f"ZZT.TO,ZZT TRUST WE ACTED AS AGENT,-500,10.00,"
                       f"5000.00,0.00,5000.00,{k},{ACCT},Trades,"
                       f"Individual margin\n"
                       f"2025-01-06 12:00:00 AM,2025-01-06 12:00:00 AM,DIV,"
                       f"ZZT.TO,ZZT TRUST DIST ON 1000 SHS REC 12/30/24 PAY "
                       f"01/06/25,0,0.00,0.00,0.00,120.00,{k},{ACCT},"
                       f"Dividends,Individual margin\n"
                       f"2025-01-06 12:00:00 AM,2025-01-06 12:00:00 AM,DIV,"
                       f"ZZT.TO,ZZT TRUST RETURN OF CAPITAL ON 1000 SHS REC "
                       f"12/30/24 PAY 01/06/25,0,0.00,0.00,0.00,50.00,{k},"
                       f"{ACCT},Dividends,Individual margin\n")
                root = Path(td) / c
                (root / "inputs" / "margin").mkdir(parents=True)
                (root / "inputs" / "margin" / "questrade.csv").write_text(csv)
                (root / "taxjson.toml").write_text(
                    f'[settings]\nyear = 2024\ncountry = "{c}"\n'
                    f'base_currency = "{k}"\nsource_currencies = []\n'
                    f'[accounts.margin]\ntype = "taxable"\n')
                roots[c] = root
            runs = {c: cli(roots[c], "run", "--no-input") for c in roots}
            for c, r in runs.items():
                self.assertEqual(r.returncode, 0, f"{c}: {r.stderr[-2000:]}")
            divs = {c: json.loads(cli(roots[c], "divs-sum", "--json").stdout)
                    for c in roots}
            gains = {c: json.loads((roots[c] / "work" / "margin_gains.json")
                                   .read_text()) for c in roots}
            summ = {c: (roots[c] / "reports" / "margin.sum").read_text()
                    for c in roots}
        # Canada: the distribution is 2024 income; the ROC lowered the
        # ACB on 2024-12-30, before the sale settled.
        self.assertEqual(divs["canada"]["totals"], {"CAD": 120.0})
        self.assertEqual(divs["usa"]["totals"], {})
        self.assertEqual(_income(gains["canada"])[0]["income_date"],
                         "2024-12-30")
        self.assertEqual(_income(gains["usa"]), [])
        sale = {c: [t for t in gains[c]["transactions"] if "gain" in t
                    and t.get("action") not in ("DIVIDEND",)]
                for c in roots}
        # ACB 10,000 - 50 = 9,950 over 1,000 units: 500 sold at 10 ->
        # gain 25 in Canada; full basis (gain 0) in the US.
        self.assertAlmostEqual(sale["canada"][0]["gain"], 25.0, places=2)
        self.assertAlmostEqual(sale["usa"][0]["gain"], 0.0, places=2)
        self.assertIn("120.00", summ["canada"])


# ------------------------------------------------------------ D6
class TestRbcNotionalAndDrip(unittest.TestCase):

    @rule("CA-DIST-02")
    @rule("US-DIST-02")
    def test_notional_distribution_raises_cost_with_no_income(self):
        from test_fix_rbcqt import RBUY, rrow, rbc_parse
        txs, err, _ = rbc_parse(
            RBUY + rrow("December 31, 2025", "Dividends", "XYZ", "XYZ CORP",
                        "", "", "0", "CAD", "ADJ - XYZ CORP 2025 NOTIONAL "
                        "DISTRIBUTION ADJUSTMENT TO BOOK COST $200.00"))
        self.assertIn("NOT in taxjson", err)
        (adj,) = [t for t in txs if t["action"] == "ADJUST"]
        self.assertEqual((adj["type"], adj["net_amount"]), ("dist", 200.0))
        self.assertFalse([t for t in txs if t["action"] == "DIVIDEND"])
        from taxjson.lib.core import coerce_transaction_row
        book = [coerce_transaction_row(t, i, "t") for i, t in enumerate(txs)]
        book.append(tx("BUYSELL", "2026-02-02", book[0].symbol, -100,
                       6000, currency="CAD"))
        r = gains_both(book, year=2026)
        for c in C.COUNTRIES:
            with self.subTest(country=c):
                # Cost 5009.95 + 200 notional -> gain 790.05.
                self.assertEqual(_gains(r[c]), [790.05])

    @rule("CA-DIST-03")
    @rule("US-DIST-03")
    def test_drip_is_income_plus_a_purchase(self):
        from test_fix_rbcqt import q, qdiv, qt_parse
        txs, _, _ = qt_parse(
            qdiv("XIC.TO", "ISHARES DIST ON 100 SHS REC 06/20/25 PAY "
                 "06/27/25", "30.00", td="2025-06-27", cur="CAD")
            + q(td="2025-06-27", action="REI", sym="XIC.TO",
                desc="ISHARES REINV@C$30.00", qty="1", price="0",
                gross="0", comm="0", net="-30.00", cur="CAD",
                act="Dividend reinvestment"))
        acts = sorted((t["action"], t["quantity"]) for t in txs)
        self.assertEqual(acts, [("BUYSELL", 1.0), ("DIVIDEND", 100.0)])
        buy = [t for t in txs if t["action"] == "BUYSELL"][0]
        self.assertAlmostEqual(buy["net_amount"], 30.0)


if __name__ == "__main__":
    unittest.main()
