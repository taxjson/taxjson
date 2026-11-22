"""The characterize step (taxjson.characterize, the re-extraction's
recommendation 8): a parse gives the same rows under every country, and
the step applies the country's rule. The first slice, a foreign issuer's
return of capital (IB): CA-ACB-08 (s.90(1), a dividend), CA-ACB-09 (the
"acb" setting) and US-ROC-01 (a cost reduction). The books each mode
gives are recorded in tests/fixtures/characterize/ib_foreign_roc.json,
checked against main's parse before the move (TJ-2594): byte for byte
for the cost-reduction modes; the dividend modes but two rows, which
TJ-2594 changed on purpose (see the test below)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taxjson.characterize import characterize_rows, foreign_roc_treatment
from taxjson.lib.tax_logic import ENGINE_RULES
from _support.engine_rules import assert_noted
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from taxjson.lib import once
from taxjson.lib.country import CountryError, parse_basis, stablecoin_model
from taxjson.parsers import registry
from taxjson.lib.dispatch import python_module_argv
from taxjson.parsers.settings import ParseSettings
from taxjson.parsers.toolbox.descriptions import ISSUER_ROC_KEY
from tax_rules import rule
from taxjson.rules import ids as R
from _siblings import with_siblings
import _paths

REPO_ROOT = _paths.REPO
EXPECTED = REPO_ROOT / "tests" / "fixtures" / "characterize" / "ib_foreign_roc.json"

US1, US2, CA1, IE1 = "US0000000010", "US0000000028", "CA0000000012", "IE0000000012"
STATEMENT = (
    'Statement,Header,Field Name,Field Value\n'
    'Statement,Data,BrokerName,Interactive Brokers\n'
    'Statement,Data,Period,"January 1, 2026 - December 31, 2026"\n'
    'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
    'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
    'Realized P/L,MTM P/L,Code\n'
    'Trades,Data,Order,Stocks,USD,QZRX,"2026-01-05, 09:30:00",100,10.00,0,-1000,-1,0,0,0,O\n'
    'Trades,Data,Order,Stocks,USD,QZIE,"2026-01-05, 09:30:00",100,10.00,0,-1000,-1,0,0,0,O\n'
    'Trades,Data,Order,Stocks,USD,QZRY,"2026-01-05, 09:30:00",100,10.00,0,-1000,-1,0,0,0,O\n'
    'Dividends,Header,Currency,Account,Date,Description,Amount\n'
    # A foreign issuer's return of capital, an ordinary dividend alike in
    # every number on its day, two alike reversals.
    f'Dividends,Data,USD,U1,2026-06-30,QZRX({US1}) Return of Capital USD 0.12 per Share,12.00\n'
    f'Dividends,Data,USD,U1,2026-06-30,QZRX({US1}) Cash Dividend USD 0.12 per Share (Ordinary Dividend),12.00\n'
    f'Dividends,Data,USD,U1,2026-07-02,QZRX({US1}) Return of Capital USD 0.12 per Share,-12.00\n'
    f'Dividends,Data,USD,U1,2026-07-02,QZRX({US1}) Return of Capital USD 0.12 per Share,-12.00\n'
    # Payments in lieu (income whatever the issuer), a Canadian issuer's
    # return of capital, one of no ISIN (the issuer unknown).
    f'Dividends,Data,USD,U1,2026-06-30,QZRX({US1}) Payment in Lieu of Dividend (Return of Capital),4.00\n'
    f'Dividends,Data,CAD,U1,2026-06-30,QZRT({CA1}) Return of Capital CAD 0.12 per Share,24.00\n'
    f'Dividends,Data,CAD,U1,2026-06-30,QZRT({CA1}) Payment in Lieu of Dividend (Return of Capital),3.00\n'
    'Dividends,Data,USD,U1,2026-06-30,QZNO Return of Capital USD 0.20 per Share,20.00\n'
    # No rate in the description: the accrual gives it; a record date;
    # an ordinary dividend beside, its accrual in another currency.
    f'Dividends,Data,USD,U1,2026-06-30,QZIE({IE1}) Return of Capital,20.00\n'
    f'Dividends,Data,CAD,U1,2026-06-30,QZRY({US2}) Return of Capital REC 06/15/26,30.00\n'
    f'Dividends,Data,CAD,U1,2026-06-30,QZRY({US2}) Cash Dividend (Ordinary Dividend),10.00\n'
    'Change in Dividend Accruals,Header,Asset Category,Currency,Account,'
    'Symbol,Date,Ex Date,Pay Date,Quantity,Tax,Fee,Gross Rate,'
    'Gross Amount,Net Amount,Code\n'
    'Change in Dividend Accruals,Data,Stocks,USD,U1,QZIE,2026-06-15,2026-06-15,2026-06-30,100,0,0,0.20,20,20,Po\n'
    'Change in Dividend Accruals,Data,Stocks,USD,U1,QZIE,2026-06-30,2026-06-15,2026-06-30,100,0,0,0.20,-20,-20,Re\n'
    'Change in Dividend Accruals,Data,Stocks,USD,U1,QZRY,2026-06-15,2026-06-15,2026-06-30,100,0,0,0.40,40,40,Po\n'
    'Change in Dividend Accruals,Data,Stocks,USD,U1,QZRY,2026-06-30,2026-06-15,2026-06-30,100,0,0,0.40,-40,-40,Re\n'
    'Change in Dividend Accruals,Data,Stocks,USD,U1,QZRX,2026-06-15,2026-06-14,2026-06-30,100,0,0,0.12,12,12,Po\n'
    'Change in Dividend Accruals,Data,Stocks,USD,U1,QZRX,2026-06-30,2026-06-14,2026-06-30,100,0,0,0.12,-12,-12,Re\n')
# (mode, country, [settings] foreign_return_of_capital)
MODES = (("none", None, None), ("none-dividend", None, "dividend"),
         ("canada", "canada", None), ("canada-acb", "canada", "acb"),
         ("usa", "usa", None))


def _parse(country, characterized=True, froc=None, statement=STATEMENT, note_rule=None):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "U1234567.csv"
        path.write_text(statement)
        ib = IbBrokerage()
        ib.configure(ParseSettings(country=country, **parse_basis(country)))
        with contextlib.redirect_stderr(io.StringIO()):
            rows = ib.parse_file(path)
    if characterized:
        characterize_rows(rows, country=country, foreign_return_of_capital=froc,
                          note_rule=note_rule)
    return [r for r in rows if r["action"] != "BUYSELL"]


class TestForeignRoc(unittest.TestCase):

    def test_the_parse_is_the_same_under_every_country(self):
        # The design's test (4.5): the neutral fact, whatever the country.
        rows = {name: _parse(country, characterized=False) for name, country, _ in MODES}
        first = json.dumps(rows["none"])
        for name, got in rows.items():
            self.assertEqual(json.dumps(got), first, name)
        self.assertEqual(sum(1 for r in rows["none"] if r.get(ISSUER_ROC_KEY)), 5)

    @rule(R.CA_ACB_08)
    @rule(R.CA_ACB_09)
    @rule(R.US_ROC_01)
    def test_each_mode_books_the_recorded_rows(self):
        expected = json.loads(EXPECTED.read_text())
        for name, country, froc in MODES:
            got = _parse(country, froc=froc)
            # Key order too: a parse's rows are dicts the tools read in order.
            self.assertEqual(json.dumps(got), json.dumps(expected[name]), name)
            self.assertFalse([r for r in got if ISSUER_ROC_KEY in r], name)

    @rule(R.CA_ACB_08)
    @rule(R.CA_ACB_09)
    @rule(R.US_ROC_01)
    def test_each_booked_row_names_its_rule(self):
        # PLAN step 11: the callback hears each row booked and the rule it
        # was booked by (no country: none); the rows are the same with it.
        want = {"none": None, "none-dividend": R.CA_ACB_08, "canada": R.CA_ACB_08,
                "canada-acb": R.CA_ACB_09, "usa": R.US_ROC_01}
        for name, country, froc in MODES:
            heard = []
            got = _parse(country, froc=froc,
                         note_rule=lambda row, rid: heard.append((row, rid)))
            self.assertEqual(json.dumps(got), json.dumps(_parse(country, froc=froc)), name)
            self.assertEqual([rid for _, rid in heard], [want[name]] * 5 if want[name] else [],
                             name)
            self.assertTrue(all(any(row is r for r in got) for row, _ in heard), name)
            self.assertLessEqual({rid for _, rid in heard}, ENGINE_RULES, name)
        assert_noted(self, self._heard("canada"), R.CA_ACB_08)
        assert_noted(self, self._heard("canada", "acb"), R.CA_ACB_09)
        assert_noted(self, self._heard("usa"), R.US_ROC_01)

    @staticmethod
    def _heard(country, froc=None):
        """The (row, rule id) pairs the callback hears over the statement."""
        heard = []
        _parse(country, froc=froc, note_rule=lambda row, rid: heard.append((row, rid)))
        return heard

    @rule(R.CA_ACB_08)
    def test_a_canadian_project_books_a_foreign_roc_as_a_dividend(self):
        got = [r for r in _parse("canada") if r["symbol"] == "QZRX.US"]
        roc = [r for r in got if "Return of Capital USD" in r["description"]]
        self.assertEqual([r["action"] for r in roc], ["DIVIDEND"] * 3)
        self.assertTrue(all("treated as a foreign dividend (ITA s.90(1))" in r["description"]
                            for r in roc))
        # The note goes before a split-fill mark, as the parse wrote them.
        self.assertTrue(roc[-1]["description"].endswith("s.53(2)(b)(ii)] [fill #2]"))

    @rule(R.US_ROC_01)
    def test_a_us_project_lowers_the_basis(self):
        got = [r for r in _parse("usa") if r["symbol"] == "QZRX.US"]
        adj = [r for r in got if r["action"] == "ADJUST"]
        self.assertEqual([r["net_amount"] for r in adj], [-12.0, 12.0, 12.0])
        self.assertEqual({r["type"] for r in adj}, {"roc"})
        self.assertEqual({r["issuer_country"] for r in adj}, {"US"})
        qzry = next(r for r in _parse("usa") if r["symbol"] == "QZRY.US" and r["action"] == "ADJUST")
        self.assertEqual(qzry["record_date"], "2026-06-15")

    def test_a_foreign_roc_beside_an_ordinary_dividend(self):
        # TJ-2594: in a Canadian project the return of capital, a dividend
        # there, no longer marks the ordinary dividend alike in every
        # number as its split fill, nor takes the ordinary dividend's
        # share count from the accrual away: both as a US project's.
        for country in ("canada", "usa"):
            got = _parse(country)
            ordinary = next(r for r in got if r["symbol"] == "QZRX.US"
                            and "Ordinary Dividend" in r["description"])
            self.assertNotIn("[fill", ordinary["description"], country)
            qzry = next(r for r in got if r["symbol"] == "QZRY.US"
                        and "Ordinary Dividend" in r["description"])
            self.assertEqual((qzry["quantity"], qzry["price"]), (100.0, 0.1), country)

    def test_either_order_of_the_two_alike_rows(self):
        # TJ-2594: listed after an ordinary dividend alike in every
        # number, the return of capital was the one main marked.
        head, _, _ = STATEMENT.partition("Dividends,Header")
        stmt = (head + "Dividends,Header,Currency,Account,Date,Description,Amount\n"
                f"Dividends,Data,USD,U1,2026-06-30,QZRX({US1}) Cash Dividend USD 0.12 per Share (Ordinary Dividend),12.00\n"
                f"Dividends,Data,USD,U1,2026-06-30,QZRX({US1}) Return of Capital USD 0.12 per Share,12.00\n")
        for country in ("canada", "usa"):
            got = _parse(country, statement=stmt)
            self.assertEqual(len(got), 2, country)
            self.assertFalse([r["description"] for r in got if "[fill" in r["description"]], country)

    def test_the_treatment_of_each_country(self):
        self.assertEqual(foreign_roc_treatment("canada"), "dividend")
        self.assertEqual(foreign_roc_treatment("Canada", "acb"), "acb")
        self.assertEqual(foreign_roc_treatment("usa"), "acb")
        self.assertEqual(foreign_roc_treatment("usa", "dividend"), "acb")
        self.assertEqual(foreign_roc_treatment(None), "acb")
        self.assertEqual(foreign_roc_treatment(None, "dividend"), "dividend")
        self.assertEqual(foreign_roc_treatment("  "), "acb")
        # A value the rule does not know is refused, with a country or without.
        for country in (None, "canada"):
            with self.assertRaises(CountryError):
                foreign_roc_treatment(country, "nope")


class TestTheTool(unittest.TestCase):
    """taxjson-brokerage applies the step to what it writes."""

    def _run(self, country):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "U1234567.csv"
            path.write_text(STATEMENT)
            r = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_brokerage",
                                   ["--brokerage", "ib", "--account", "x",
                                    "--country", country, str(path)]),
                cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0"), timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [t for t in json.loads(r.stdout)["transactions"]
                if t.get("symbol") == "QZRX.US" and "Return of Capital USD" in (t.get("description") or "")]

    def test_the_written_rows_are_characterized(self):
        ca = self._run("canada")
        self.assertEqual({t["action"] for t in ca}, {"DIVIDEND"})
        self.assertTrue(all("ITA s.90(1)" in t["description"] for t in ca))
        us = self._run("usa")
        self.assertEqual({t["action"] for t in us}, {"ADJUST"})
        for t in ca + us:
            self.assertNotIn(ISSUER_ROC_KEY, t)


CRYPTO_ENV = {"TAXJSON_LOCAL_TZ": "America/Toronto", "TAXJSON_OFFLINE": "1", "TAXJSON_WIDTH": "0"}
FIXTURES = REPO_ROOT / "tests" / "fixtures"


class TestStablecoinBasis(unittest.TestCase):
    """The second slice (recommendation 8): USD stablecoins are booked on
    a basis the country decides, US-dollar cash (CA-CRYPTO-02) or
    property (US-CRYPTO-02), and that basis is the parse's input
    (lib/country `parse_basis`), not a step after it: the two bases book
    different rows, count a row differently and refuse different shapes,
    so no neutral row set exists to fold (the evidence: PLAN.md's
    `characterize/` bullet). What holds instead: the parse reads the
    country only through its basis."""

    @rule(R.CA_CRYPTO_02)
    @rule(R.US_CRYPTO_02)
    def test_the_country_decides_the_basis(self):
        self.assertEqual([stablecoin_model(c) for c in ("canada", "CA", "usa")],
                         ["cash", "cash", "property"])
        self.assertTrue(parse_basis("canada")["stablecoins_as_cash"])
        self.assertFalse(parse_basis("usa")["stablecoins_as_cash"])
        # No country: the parse's neutral basis (parse_basis) alone; the
        # model itself refuses, as every step past the parse does.
        self.assertFalse(parse_basis(None)["stablecoins_as_cash"])
        for odd in ("mars", None, "", " "):
            with self.assertRaises(CountryError):
                stablecoin_model(odd)

    def _parse(self, pid, folder, country, cash):
        # What is said once per process (a market hint, the de-peg check's
        # unchecked currency) is said again, as in each run's first parse.
        once.forget_all()
        basis = {"stablecoins_as_cash": cash, "plans_taxed": frozenset()}
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, CRYPTO_ENV):
            for f in folder.glob("*.csv"):
                shutil.copy(f, td)
            parser = with_siblings(registry.parser_class(pid)())
            parser.configure(ParseSettings(country=country, **basis))
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
                rows = parser.parse_file(Path(td) / "sample.csv")
            c = parser.row_counts()
            return (json.dumps(rows), err.getvalue().replace(td, "<dir>"),
                    (c.seen, c.consumed, c.skipped, c.unaccounted))

    def test_the_parse_reads_the_country_only_through_its_basis(self):
        for pid in ("kraken", "coinbase"):
            for cash in (True, False):
                ca = self._parse(pid, FIXTURES / pid, "canada", cash)
                us = self._parse(pid, FIXTURES / pid, "usa", cash)
                self.assertEqual(ca, us, (pid, cash))
            if pid == "kraken":
                # The trades fixture reads its ledger beside it (the run's
                # sibling finder): the ledger-priced fills are compared too.
                self.assertIn("per ledger", ca[0])
                self.assertNotIn("no Kraken ledgers export", ca[1])
            # Each fixture holds a stablecoin leg the two bases book apart.
            self.assertNotEqual(self._parse(pid, FIXTURES / pid, "canada", True)[0],
                                self._parse(pid, FIXTURES / pid, "canada", False)[0], pid)

    def test_the_ledger_records_the_model_it_used(self):
        # A cash input without the flag: the ledger derives the model
        # from the country and its document says the one it used (the
        # report's policy line and `tjs file` read it).
        from taxjson.lib import fx_cash_v2
        for country, cash in (("canada", True), ("usa", False)):
            doc = fx_cash_v2.build([], {}, "CAD", 2025, lambda *a: 1.0,
                                   country=country)
            self.assertIs(doc["stablecoins_as_cash"], cash, country)

    def test_past_the_parse_a_missing_country_is_refused(self):
        # The no-country answer (property) is the parse's alone: the
        # ledger refuses a missing country, as every step past the parse.
        from taxjson.lib import cash_events, fx_cash_v2
        for blank in (None, "", " "):
            with self.assertRaises(CountryError):
                cash_events.collect(Path("."), {}, blank, {})
            with self.assertRaises(CountryError):
                fx_cash_v2.build([], {}, "CAD", 2025, lambda *a: 1.0,
                                 country=blank)

    def test_the_tool_books_the_countrys_basis(self):
        def symbols(country):
            with tempfile.TemporaryDirectory() as td:
                for f in (FIXTURES / "kraken").glob("*.csv"):
                    shutil.copy(f, td)
                r = subprocess.run(
                    python_module_argv("taxjson.bin.taxjson_brokerage",
                                       ["--brokerage", "kraken", "--account", "x",
                                        "--country", country, str(Path(td) / "sample.csv")]),
                    cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                    env=dict(os.environ, **CRYPTO_ENV), timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
            return {t.get("symbol") for t in json.loads(r.stdout)["transactions"]}
        self.assertNotIn("USDC", symbols("canada"))      # US-dollar cash
        self.assertIn("USDC", symbols("usa"))            # a coin's leg


if __name__ == "__main__":
    unittest.main()
