"""Interactive Brokers: an income row whose ISIN names a domicile and no
market is booked on a listing only on evidence of it (backlog TJ-5016).

An IB Dividends or Withholding Tax row names its security by ISIN. The
ISIN's country was read as the market: every Irish ISIN went to London
(`QZRX.L`), though an Irish fund lists in Amsterdam or Frankfurt as well
and an Irish company in New York, and a country outside the table (LU,
DE ...) went to `.US` with a warning. Where the account holds the ticker
the row is moved to the held listing, as before. A row nothing places is
now booked on evidence alone: the account's own listing of that symbol,
a ticker.map line naming that symbol (the user's declaration; a line
naming another listing of the ticker declares nothing of the row), the
one listing the account holds the ticker under where its suffix is a
currency's code (a line IB trades in euros is ROOT.EUR; said with a
Warning, and never for a return of capital), or the one venue the
statement's Financial Instrument Information gives the ISIN. Else the
parse stops, says the true cause (the listings the account holds, the
venues the instrument list names, or that nothing names one) and names
the ticker.map line to add. A country with a home market in the table
(CA, US, GB, AU) is read as before.

The listing is the symbol the row joins its holding by (a return of
capital's pool, the T1135 row, the dividend estimates); the country a
report gives it comes from the ISIN itself. `tjs tax-logic` states no
rule for the listing of an income row, so no rule is cited.

All data synthetic (the made-up ticker QZRX, made-up ISINs).
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages import base as _base
from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from _support.ib_statement_rows import (DIV_H, FII_H, HEAD, WHT_H, _fii_stock,
                                        _parse_account, _parse_ib)

IE, LU = "IE0000000010", "LU0000000010"
_OP_H = ('Open Positions,Header,DataDiscriminator,Asset Category,Currency,'
         'Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,'
         'Unrealized P/L,Code\n')
_T_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
        'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
        'Realized P/L,MTM P/L,Code\n')


def _div(isin=IE, cur='EUR', what='Cash Dividend EUR 0.10 per Share (Ordinary Dividend)',
         amount='10.00'):
    return f'Dividends,Data,{cur},2026-06-30,QZRX({isin}) {what},{amount}\n'


def _tax(isin=IE, cur='EUR'):
    return (f'Withholding Tax,Data,{cur},2026-06-30,QZRX({isin}) Cash Dividend EUR 0.10 '
            f'per Share - IE Tax,-1.50\n')


def _buy(cur, when='2026-01-05'):
    return (f'Trades,Data,Order,Stocks,{cur},QZRX,"{when}, 10:00:00",100,10,10,'
            f'-1000,0,1000,0,0,O\n')


ROC = 'Return of Capital EUR 0.10 per Share'
KEEP = ("if it is QZRX.L, any line naming that symbol (`QUOTE QZRX.L QZRX.L`)")
IE_IS = ("QZRX (ISIN IE0000000010): the ISIN's country (IE) is where the issuer is "
         "domiciled, not a market, ")


def _held(*buys, income=None):
    """A statement that trades QZRX (`buys`) and is paid the IE income."""
    return HEAD + _T_H + ''.join(buys) + (income or DIV_H + _div() + WHT_H + _tax())


def _income(isin=IE, extra=''):
    return HEAD + extra + DIV_H + _div(isin) + WHT_H + _tax(isin)


def _symbols(text):
    """{action: symbol} of the statement's rows, and what the parse said."""
    _, rows, err = _parse_ib(text)
    return {r['action']: r['symbol'] for r in rows}, err


def _refused(test, text):
    with test.assertRaises(BrokerageParseError) as cm:
        _parse_ib(text)
    return str(cm.exception)


class _Mapped(unittest.TestCase):
    """A ticker.map loaded as `tjs run` loads it (`--ticker-map`)."""

    def tearDown(self):
        _base.set_ticker_map(None)

    def load(self, tmap):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text(tmap)
            _base.set_ticker_map(p)


class TestNothingNamesTheListing(_Mapped):

    def test_an_irish_isin_alone_is_refused(self):
        # Before the fix: booked on QZRX.L, nothing said.
        self.assertEqual(
            _refused(self, _income()),
            "ib.csv lines 5, 7: " + IE_IS + "and nothing says where QZRX is "
            "listed: no trade, transfer or Open Positions row of the account's "
            "statements holds it, and no Financial Instrument Information row gives the "
            "ISIN a listing exchange taxjson knows — refusing to book 2 rows (1 "
            "dividend, 1 withholding tax) on QZRX.L, a guess. Name its listing in the "
            "project's ticker.map: " + KEEP + "; if it is "
            "listed elsewhere, `GLOBAL QZRX.L QZRX.SUFFIX` with the symbol the "
            "account's trades of it carry (a line IB trades in euros is QZRX.EUR). Or "
            "add the statement that holds its trades or its Open Positions row.")

    def test_a_country_outside_the_table_is_refused(self):
        # Before the fix: booked on QZRX.US with a warning. The sentence
        # about a domicile is said of the countries the data calls one.
        msg = _refused(self, _income(LU))
        self.assertIn("QZRX (ISIN LU0000000010): the ISIN's country (LU) has no home "
                      "market in taxjson's table (CA, US, GB, AU), so the ISIN does not "
                      "say where it is listed, and nothing says where QZRX is listed",
                      msg)
        self.assertIn("on QZRX.US, a guess.", msg)
        self.assertIn("(`QUOTE QZRX.US QZRX`)", msg)

    def test_a_return_of_capital_is_named_as_one(self):
        text = HEAD + DIV_H + _div(what=ROC) + _div()
        self.assertIn("refusing to book 2 rows (1 return of capital, 1 dividend) on "
                      "QZRX.L, a guess.", _refused(self, text))
        self.assertIn("(2 dividends)", _refused(self, HEAD + DIV_H + _div() + _div(
            amount='-10.00')))

    def test_an_exchange_the_market_data_does_not_know_is_no_evidence(self):
        text = _income(extra=FII_H + _fii_stock('QZRX', IE, exch='ZZEX'))
        self.assertIn("nothing says where QZRX is listed", _refused(self, text))

    def test_another_tickers_line_is_no_evidence(self):
        self.load("QUOTE QZRT.L QZRT.L\nQUOTE QZRX QZRX\n")
        self.assertIn("refusing to book", _refused(self, _income()))


class TestAMapLineOfAnotherListingDeclaresNothing(_Mapped):
    """Only a line naming the row's own symbol (QZRX.L) is the user's word
    on it. A line naming another listing of the ticker was read as the
    listing (the first build of TJ-5016): a `DELETE QZRX.US` line then
    took the income and deleted it, and a `QUOTE QZRX.TO` line written for
    another security put a return of capital on that security's cost."""

    def refused_with(self, line, text=None):
        self.load(line + "\n")
        msg = _refused(self, text or _income())
        self.assertIn("nothing says where QZRX is listed", msg, line)
        self.assertIn("on QZRX.L, a guess", msg, line)

    def test_a_delete_line_of_another_listing(self):
        self.refused_with("DELETE QZRX.US")

    def test_a_quote_line_of_another_securitys_listing(self):
        self.refused_with("QUOTE QZRX.TO QZRX.TO")
        self.refused_with("QUOTE QZRX.TO QZRX.TO", HEAD + DIV_H + _div(what=ROC))

    def test_a_distinct_a_t1135_and_a_global_line_of_another_listing(self):
        for line in ("DISTINCT QZRX.TO QZRY.US", "T1135 QZRX.TO CAN",
                     "GLOBAL QZRX.V QZNEW.TO", "TOBASE QZRX.US QZRX.TO"):
            with self.subTest(line=line):
                self.refused_with(line)

    def test_a_return_of_capital_never_reaches_another_accounts_holding(self):
        # `tjs run`: QZRX.TO is another security, held in a second account;
        # the map quotes it. The Irish payer's return of capital went onto
        # that holding's cost.
        import _qa_project as Q
        period = 'Statement,Data,Period,"January 1, 2024 - December 31, 2024"\n'
        paid = (HEAD + period + DIV_H
                + _div(what='Return of Capital EUR 0.20 per Share', amount='20.00')
                .replace('2026-06-30', '2024-06-28'))
        held = HEAD + period + _T_H + _buy('CAD', when='2024-01-05')
        with tempfile.TemporaryDirectory() as td:
            root = Q.project(td, 'p', {'margin/ib.csv': paid, 'second/ib2.csv': held},
                             ticker_map='QUOTE QZRX.TO QZRX.TO\n',
                             extra_accounts='[accounts.second]\ntype = "taxable"\n')
            r = Q.tj(root, 'run', check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing to book 1 row (1 return of capital) on QZRX.L, a guess",
                      Q.console(r))


class TestTheMapNamesTheSymbol(_Mapped):

    def test_a_line_naming_the_symbol_keeps_it(self):
        for line in ("QUOTE QZRX.L QZRX.L", "T1135 QZRX.L IRL", "quote qzrx.l qzrx.l",
                     # (the run then renames it)
                     "GLOBAL QZRX.L QZRX.EUR"):
            with self.subTest(line=line):
                self.load(line + "\n")
                got, err = _symbols(_income())
                self.assertEqual(got, {'DIVIDEND': 'QZRX.L', 'TAX': 'QZRX.L'})
                self.assertNotIn('guess', err)

    def test_a_country_outside_the_table(self):
        self.load("QUOTE QZRX.US QZRX\n")
        got, err = _symbols(_income(LU))
        self.assertEqual(got, {'DIVIDEND': 'QZRX.US', 'TAX': 'QZRX.US'})
        self.assertNotIn('QZRX', err)

    def test_it_decides_before_a_holding_or_the_instrument_list(self):
        self.load("QUOTE QZRX.L QZRX.L\n")
        got, err = _symbols(_held(_buy('EUR')))
        self.assertEqual(got, {'BUYSELL': 'QZRX.EUR', 'DIVIDEND': 'QZRX.L',
                               'TAX': 'QZRX.L'})
        self.assertNotIn('booked on', err)
        got, _ = _symbols(_income(extra=FII_H + _fii_stock('QZRX', IE, exch='NYSE')))
        self.assertEqual(got, {'DIVIDEND': 'QZRX.L', 'TAX': 'QZRX.L'})

    def test_the_projects_map_is_read_without_being_loaded(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            acct = Path(td) / "inputs" / "margin"
            acct.mkdir(parents=True)
            (acct / "ib.csv").write_text(_income())
            (Path(td) / "taxjson.toml").write_text('[settings]\ncountry = "canada"\n')
            (Path(td) / "ticker.map").write_text("QUOTE QZRX.L QZRX.L\n")
            with contextlib.redirect_stderr(err):
                rows = IbBrokerage().parse_file(acct / "ib.csv")
        self.assertEqual({r['symbol'] for r in rows}, {'QZRX.L'})


class TestTheInstrumentListNamesOneVenue(_Mapped):

    def test_the_isins_listing_exchange(self):
        # LSEETF is London; NYSE a US line (an Irish company's).
        for exch, sym in (('LSEETF', 'QZRX.L'), ('NYSE', 'QZRX.US'), ('TSE', 'QZRX.TO')):
            text = _income(extra=FII_H + _fii_stock('QZRX', IE, exch=exch))
            got, err = _symbols(text)
            self.assertEqual(got, {'DIVIDEND': sym, 'TAX': sym}, exch)
            self.assertNotIn('guess', err)

    def test_another_isins_listing_exchange_is_no_evidence(self):
        text = _income(extra=FII_H + _fii_stock('QZRX', 'IE0000000028', exch='LSEETF'))
        self.assertIn("nothing says where QZRX is listed", _refused(self, text))

    def test_two_venues_are_refused_naming_both(self):
        for second, said in (('NYSE', "QZRX.L; QZRX.US"),
                             # (one the market data does not know: before
                             # this it was passed over and London taken)
                             ('ZZEX', "QZRX.L; ZZEX, a venue not in taxjson's market "
                                      "data")):
            text = _income(extra=FII_H + _fii_stock('QZRX', IE, exch='LSEETF')
                           + _fii_stock('QZRX', IE, conid='990000002', exch=second))
            self.assertEqual(
                _refused(self, text),
                "ib.csv lines 8, 10: " + IE_IS + "and the statement's Financial "
                "Instrument Information gives the ISIN more than one listing exchange "
                f"({said}), which does not say which line paid — refusing to book 2 "
                "rows (1 dividend, 1 withholding tax) on QZRX.L, a guess. Say which in "
                "the project's ticker.map: " + KEEP + "; else `GLOBAL QZRX.L "
                "QZRX.SUFFIX` with the symbol the account's trades of it carry (a line "
                "IB trades in euros is QZRX.EUR).", second)


class TestTheTickerHeldUnderACurrencysCode(_Mapped):
    """A line IB trades in euros is QZRX.EUR, a suffix the income rebind
    does not read. Before the fix the income stayed on QZRX.L."""

    # An ATTENTION line: `tjs run` echoes those to the console (a plain
    # parser warning reaches the .sum and the .diag alone).
    SAID = ("warning: ATTENTION: ib.csv lines 7, 9: QZRX income booked on QZRX.EUR, "
            "the one listing of QZRX the account holds, by its name alone. The 2 rows "
            "(1 dividend, 1 withholding tax) name ISIN IE0000000010, whose country "
            "names no market, and the rows of QZRX.EUR (first row 2026-01-05) carry "
            "no ISIN. If it is this security, add "
            "`GLOBAL QZRX.L QZRX.EUR` to the project's ticker.map: the line says so, "
            "books the rows the same way and this is not said again. If the rows "
            "belong elsewhere, `GLOBAL QZRX.L QZRX.SUFFIX` moves them, and any other "
            "line naming QZRX.L keeps them on QZRX.L.\n")

    def test_the_income_is_booked_there_and_said(self):
        got, err = _symbols(_held(_buy('EUR')))
        self.assertEqual(got, {'BUYSELL': 'QZRX.EUR', 'DIVIDEND': 'QZRX.EUR',
                               'TAX': 'QZRX.EUR'})
        self.assertIn(self.SAID, err)
        self.assertEqual(err.count('income booked on QZRX.EUR'), 1)   # once a security

    def test_the_line_it_names_confirms_it_and_is_not_said_again(self):
        # The map then names QZRX.L: the user's declaration. The rows wait
        # on QZRX.L for the run's rename to QZRX.EUR.
        self.load("GLOBAL QZRX.L QZRX.EUR\n")
        got, err = _symbols(_held(_buy('EUR')))
        self.assertEqual(got, {'BUYSELL': 'QZRX.EUR', 'DIVIDEND': 'QZRX.L',
                               'TAX': 'QZRX.L'})
        self.assertNotIn('booked on', err)

    def test_the_run_shows_it_on_the_console(self):
        import _qa_project as Q
        text = (HEAD + 'Statement,Data,Period,"January 1, 2024 - December 31, 2024"\n'
                + _T_H + _buy('EUR', when='2024-01-05') + DIV_H
                + _div().replace('2026-06-30', '2024-06-28'))
        with tempfile.TemporaryDirectory() as td:
            root = Q.project(td, 'p', {'margin/ib.csv': text})
            toml = root / 'taxjson.toml'
            toml.write_text(toml.read_text().replace(
                'source_currencies = ["USD"]', 'source_currencies = ["USD", "EUR"]'))
            r = Q.tj(root, 'run', check=False)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Warning: ib.csv line 8: QZRX income booked on QZRX.EUR, the one "
                      "listing of QZRX the account holds, by its name alone.",
                      Q.console(r))

    def test_the_holding_decides_before_the_instrument_list(self):
        # The list calls the contract's primary exchange NYSE; the books
        # hold it as QZRX.EUR, and that is where its income joins it.
        got, err = _symbols(HEAD + FII_H + _fii_stock('QZRX', IE, exch='NYSE')
                            + _T_H + _buy('EUR') + DIV_H + _div() + WHT_H + _tax())
        self.assertEqual(got, {'BUYSELL': 'QZRX.EUR', 'DIVIDEND': 'QZRX.EUR',
                               'TAX': 'QZRX.EUR'})
        # The list gives the holding's ticker the rows' ISIN: the
        # statement's own word, nothing to say.
        self.assertNotIn('booked on', err)

    def test_an_open_position_or_another_statement_of_the_account(self):
        text = (HEAD + _OP_H + 'Open Positions,Data,Summary,Stocks,EUR,QZRX,100,1,10,'
                '1000,10,1000,0,\n' + DIV_H + _div() + WHT_H + _tax())
        got, err = _symbols(text)
        self.assertEqual(got, {'DIVIDEND': 'QZRX.EUR', 'TAX': 'QZRX.EUR'})
        self.assertIn("the rows of QZRX.EUR (Open Positions) carry no ISIN", err)
        rows, err = _parse_account({'a.csv': HEAD + _T_H + _buy('EUR'),
                                    'b.csv': _income()})
        self.assertEqual({r['action']: r['symbol'] for r in rows},
                         {'BUYSELL': 'QZRX.EUR', 'DIVIDEND': 'QZRX.EUR',
                          'TAX': 'QZRX.EUR'})
        self.assertIn("the rows of QZRX.EUR (another statement of the account) carry "
                      "no ISIN", err)

    def test_a_holding_the_instrument_list_gives_another_isin_is_refused(self):
        text = (HEAD + FII_H + _fii_stock('QZRX', 'DE0000000011', exch='ZZEX')
                + _T_H + _buy('EUR') + DIV_H + _div() + WHT_H + _tax())
        self.assertEqual(
            _refused(self, text),
            "ib.csv lines 9, 11: " + IE_IS + "and the account holds QZRX as QZRX.EUR "
            "(first row 2026-01-05), which the statement's instrument list gives "
            "another ISIN (DE0000000011): the statement does not show a holding of "
            "this security — refusing to book 2 rows (1 dividend, 1 withholding tax) "
            "on QZRX.L, a guess. Say which in the project's ticker.map: `GLOBAL QZRX.L "
            "QZRX.EUR` for the listing held; " + KEEP + ".")

    def test_a_return_of_capital_is_not_moved_to_it_unasked(self):
        # The holding under a currency's code carries no dates: a row
        # that lowers a cost is moved there by the map's line alone.
        text = _held(_buy('EUR'), income=DIV_H + _div() + _div(what=ROC))
        self.assertEqual(
            _refused(self, text),
            "ib.csv line 8: " + IE_IS + "and the account holds QZRX as QZRX.EUR "
            "(first row 2026-01-05), a listing whose rows carry no ISIN: its "
            "dividends and the tax withheld are booked there, but a return of capital "
            "lowers a cost, and no dated row shows QZRX.EUR is the holding it was "
            "paid on — refusing to book 1 row (1 return of capital) on QZRX.L, a "
            "guess. Say which in the project's ticker.map: `GLOBAL QZRX.L QZRX.EUR` "
            "for the listing held; " + KEEP + ".")
        self.load("GLOBAL QZRX.L QZRX.EUR\n")
        _, rows, _ = _parse_ib(text)
        # (the map names QZRX.L: the rows wait there for the run's rename)
        self.assertEqual([r['symbol'] for r in rows],
                         ['QZRX.EUR', 'QZRX.L', 'QZRX.L'])


class TestTheAccountHoldsListingsThatDoNotSettleIt(_Mapped):
    """The refusal says what the statement shows: the listings held, and
    why none takes the rows. It said "no trade ... holds it"."""

    def test_two_listings_held(self):
        # (the rebind's note stays: the account held more than one listing)
        self.assertEqual(
            _refused(self, _held(_buy('USD'), _buy('CAD', when='2026-02-03'))),
            "ib.csv lines 8, 10: " + IE_IS + "and the account holds QZRX as QZRX.TO "
            "(first row 2026-02-03) and QZRX.US (first row 2026-01-05), and the "
            "statement does not show which holding the rows belong to — refusing to "
            "book 2 rows (1 dividend, 1 withholding tax) on QZRX.L, a guess. Say which "
            "in the project's ticker.map: `GLOBAL QZRX.L QZRX.TO` or `GLOBAL QZRX.L "
            "QZRX.US` for the listing held; " + KEEP + ".")

    def test_two_listings_under_currency_codes(self):
        msg = _refused(self, _held(_buy('EUR'), _buy('CHF')))
        self.assertIn("the account holds QZRX as QZRX.CHF (first row 2026-01-05) and "
                      "QZRX.EUR (first row 2026-01-05), and the statement does not "
                      "show which holding the rows belong to", msg)
        self.assertIn("`GLOBAL QZRX.L QZRX.CHF` or `GLOBAL QZRX.L QZRX.EUR` for the "
                      "listing held", msg)

    def test_a_return_of_capital_and_a_listing_first_bought_after_it(self):
        # v2.0.0 (TJ-5011) kept this row on its ISIN's listing with an
        # Undated listing flag; for an ISIN that names no market that
        # listing is a guess.
        text = _held(_buy('USD', when='2026-09-01'), income=DIV_H + _div(what=ROC))
        self.assertEqual(
            _refused(self, text),
            "ib.csv line 7: " + IE_IS + "and the account holds QZRX as QZRX.US (first "
            "row 2026-09-01), which no dated row shows held on the payment's date: a "
            "return of capital is not moved onto such a listing — refusing to book 1 "
            "row (1 return of capital) on QZRX.L, a guess. Say which in the project's "
            "ticker.map: `GLOBAL QZRX.L QZRX.US` for the listing held; " + KEEP + ".")
        # (its dividend is the rebind's to move, as before)
        got, _ = _symbols(_held(_buy('USD', when='2026-09-01'), income=DIV_H + _div()))
        self.assertEqual(got, {'BUYSELL': 'QZRX.US', 'DIVIDEND': 'QZRX.US'})

    def test_a_held_listing_of_another_isin(self):
        text = (HEAD + FII_H + _fii_stock('QZRX', 'US0000000010', exch='NYSE')
                + _T_H + _buy('USD') + DIV_H + _div() + WHT_H + _tax())
        self.assertIn("the account holds QZRX as QZRX.US (first row 2026-01-05), which "
                      "the statement's instrument list gives another ISIN "
                      "(US0000000010)", _refused(self, text))


class TestAsBefore(_Mapped):
    """(controls) What the fix leaves alone."""

    def test_a_held_listing_takes_the_row(self):
        for cur, sym in (('USD', 'QZRX.US'), ('GBP', 'QZRX.L'), ('CAD', 'QZRX.TO')):
            got, err = _symbols(_held(_buy(cur)))
            self.assertEqual(got, {'BUYSELL': sym, 'DIVIDEND': sym, 'TAX': sym}, cur)
            self.assertNotIn('guess', err)
            self.assertNotIn('booked on', err)

    def test_an_open_position_takes_the_row(self):
        text = (HEAD + _OP_H + 'Open Positions,Data,Summary,Stocks,USD,QZRX,100,1,10,'
                '1000,10,1000,0,\n' + DIV_H + _div() + WHT_H + _tax())
        self.assertEqual(_symbols(text)[0], {'DIVIDEND': 'QZRX.US', 'TAX': 'QZRX.US'})

    def test_a_country_with_a_home_market_is_read_from_the_isin(self):
        for isin, sym in (('CA0000000012', 'QZRX.TO'), ('US0000000010', 'QZRX.US'),
                          ('GB0000000014', 'QZRX.L'), ('AU0000000016', 'QZRX.AX')):
            got, err = _symbols(_income(isin))
            self.assertEqual(got, {'DIVIDEND': sym, 'TAX': sym}, isin)
            self.assertNotIn('guess', err)


if __name__ == '__main__':
    unittest.main()
