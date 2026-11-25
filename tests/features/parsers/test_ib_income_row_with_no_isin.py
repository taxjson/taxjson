"""Interactive Brokers: an income row whose Description carries no ISIN is
booked on a listing only on evidence of it (backlog TJ-5250).

An older IB statement names a dividend's security by its ticker alone
("QZNO Cash Dividend EUR 0.10 per Share"). With no ISIN the row was put
on `QZNO.US` and, where the account held no listing of the ticker, booked
there with nothing said: the guess TJ-5016 removed for an ISIN whose
country names no market. The row now follows that rule. It is booked on
the account's own holding of the symbol, on a ticker.map line naming the
symbol (the user's declaration; a line naming another listing of the
ticker declares nothing of the row), on the one listing the account
holds the ticker under where its suffix is a currency's code (said in an
ATTENTION line, and never for a return of capital), or on the one venue
the statement's Financial Instrument Information gives the ticker. Else
the parse stops and names the ticker.map line to add. Never `.US` by
default.

`tjs tax-logic` states no rule for the listing of an income row, so no
rule is cited.

All data synthetic (the made-up ticker QZNO, made-up ISINs).
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages import base as _base
from taxjson.lib.brokerages.base import BrokerageParseError
from _support.ib_statement_rows import (CA_H, DIV_H, FII_H, HEAD, WHT_H, _ca,
                                        _fii_stock, _parse_account, _parse_ib)

_T_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
        'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
        'Realized P/L,MTM P/L,Code\n')
CASH = 'Cash Dividend EUR 0.10 per Share (Ordinary Dividend)'
ROC = 'Return of Capital EUR 0.10 per Share'


def _div(what=CASH, name='QZNO'):
    return f'Dividends,Data,EUR,2026-06-30,{name} {what},10.00\n'


def _tax(name='QZNO'):
    return (f'Withholding Tax,Data,EUR,2026-06-30,{name} Cash Dividend EUR 0.10 '
            f'per Share - IE Tax,-1.50\n')


def _buy(cur, when='2026-01-05'):
    return (f'Trades,Data,Order,Stocks,{cur},QZNO,"{when}, 10:00:00",100,10,10,'
            f'-1000,0,1000,0,0,O\n')


def _income(extra=''):
    return HEAD + extra + DIV_H + _div() + WHT_H + _tax()


def _symbols(text):
    """[(action, symbol)] of the statement's rows, and what the parse said."""
    _, rows, err = _parse_ib(text)
    return [(r['action'], r['symbol']) for r in rows], err


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

    def test_the_rows_are_refused_naming_the_line_to_add(self):
        # Base: [('DIVIDEND', 'QZNO.US'), ('TAX', 'QZNO.US')], nothing said.
        self.assertEqual(
            _refused(self, _income()),
            "ib.csv lines 5, 7: QZNO: the Description carries no ISIN, so the row "
            "does not say where QZNO is listed, and nothing says where QZNO is "
            "listed: no trade, transfer or Open Positions row of the account's "
            "statements holds it, and no Financial Instrument Information row gives "
            "QZNO a listing exchange taxjson knows — refusing to book 2 rows (1 "
            "dividend, 1 withholding tax) on QZNO.US, a guess. Name its listing in "
            "the project's ticker.map: if it is QZNO.US, any line naming that symbol "
            "(`QUOTE QZNO.US QZNO`); if it is listed elsewhere, `GLOBAL QZNO.US "
            "QZNO.SUFFIX` with the symbol the account's trades of it carry (a line "
            "IB trades in euros is QZNO.EUR). Or add the statement that holds its "
            "trades or its Open Positions row.")

    def test_a_withholding_row_alone_is_refused_too(self):
        msg = _refused(self, HEAD + WHT_H + _tax())
        self.assertIn("refusing to book 1 row (1 withholding tax) on QZNO.US, a guess",
                      msg)

    def test_a_return_of_capital_is_refused_as_one(self):
        msg = _refused(self, HEAD + DIV_H + _div(ROC))
        self.assertIn("ib.csv line 5: QZNO: the Description carries no ISIN", msg)
        self.assertIn("refusing to book 1 row (1 return of capital) on QZNO.US", msg)

    def test_another_listings_map_line_declares_nothing(self):
        for line in ("QUOTE QZNO.L QZNO.L", "DELETE QZNO.TO", "QUOTE QZNT.US QZNT"):
            with self.subTest(line=line):
                self.load(line + "\n")
                self.assertIn("on QZNO.US, a guess", _refused(self, _income()))

    def test_a_venue_the_market_data_does_not_know_is_no_evidence(self):
        text = _income(FII_H + _fii_stock('QZNO', 'US0000000010', exch='ZZEX'))
        self.assertIn("nothing says where QZNO is listed", _refused(self, text))

    def test_the_parse_command_exits_1(self):
        from _support.ib_statement_rows import _brokerage_cli
        rc, out, err, _ = _brokerage_cli({'ib.csv': _income()})
        self.assertEqual((rc, out), (1, None))
        self.assertIn("refusing to book 2 rows", " ".join(err.split()))


class TestBookedOnEvidence(_Mapped):

    def test_the_accounts_own_holding_of_the_symbol(self):
        # (a control: as before, with nothing said of the listing)
        got, err = _symbols(HEAD + _T_H + _buy('USD') + DIV_H + _div() + WHT_H + _tax())
        self.assertEqual(got, [('BUYSELL', 'QZNO.US'), ('DIVIDEND', 'QZNO.US'),
                               ('TAX', 'QZNO.US')])
        self.assertNotIn("QZNO income", err)

    def test_a_holding_in_another_statement_of_the_account(self):
        rows, _ = _parse_account({'a.csv': HEAD + _T_H + _buy('USD'),
                                  'b.csv': _income()})
        self.assertEqual(sorted((r['action'], r['symbol']) for r in rows),
                         [('BUYSELL', 'QZNO.US'), ('DIVIDEND', 'QZNO.US'),
                          ('TAX', 'QZNO.US')])

    def test_shares_a_spin_off_credited_are_a_holding(self):
        # The statement has no Open Positions section and no trade of
        # QZNO: its Corporate Actions row credited the shares (the row is
        # taxjson-corp-actions' to book). Base: QZNO.US, as here.
        spin = _ca('PARN(US0000000401) Spinoff  1 for 4 (QZNO, QZNO CORP, '
                   'US0000000402)', 25)
        usd = ('Dividends,Data,USD,2026-06-30,QZNO Cash Dividend USD 0.10 per '
               'Share (Ordinary Dividend),10.00\n')
        got, _ = _symbols(HEAD + CA_H + spin + DIV_H + usd)
        self.assertEqual(got, [('DIVIDEND', 'QZNO.US')])
        # (a debit on a short parent credits nothing)
        debit = _ca('PARN(US0000000401) Spinoff  1 for 4 (QZNO, QZNO CORP, '
                    'US0000000402)', -25)
        self.assertIn("on QZNO.US, a guess", _refused(self, HEAD + CA_H + debit
                                                      + DIV_H + usd))

    def test_a_map_line_naming_the_symbol(self):
        for line in ("QUOTE QZNO.US QZNO", "GLOBAL QZNO.US QZNO.EUR",
                     "quote qzno.us qzno"):
            with self.subTest(line=line):
                self.load(line + "\n")
                got, err = _symbols(_income())
                self.assertEqual(got, [('DIVIDEND', 'QZNO.US'), ('TAX', 'QZNO.US')])
                self.assertNotIn("QZNO income", err)

    def test_the_one_venue_the_instrument_list_gives_the_ticker(self):
        # Base: QZNO.US whatever the venue.
        for exch, want in (('NYSE', 'QZNO.US'), ('LSEETF', 'QZNO.L')):
            with self.subTest(exch=exch):
                got, _ = _symbols(_income(
                    FII_H + _fii_stock('QZNO', 'IE0000000010', exch=exch)))
                self.assertEqual(got, [('DIVIDEND', want), ('TAX', want)])

    def test_two_venues_are_refused_naming_them(self):
        text = _income(FII_H + _fii_stock('QZNO', 'IE0000000010', exch='LSEETF')
                       + _fii_stock('QZNO', 'IE0000000010', conid='990000002',
                                    exch='NYSE'))
        msg = _refused(self, text)
        self.assertIn("the statement's Financial Instrument Information gives QZNO "
                      "more than one listing exchange (QZNO.L; QZNO.US)", msg)
        self.assertIn("refusing to book 2 rows", msg)

    def test_another_tickers_venue_is_no_evidence(self):
        text = _income(FII_H + _fii_stock('QZNT', 'US0000000010', exch='NYSE'))
        self.assertIn("nothing says where QZNO is listed", _refused(self, text))


class TestAHoldingUnderACurrencysCode(_Mapped):
    """The one listing the account holds the ticker under, its suffix a
    currency's code (a line IB trades in euros): the income is booked
    there with the ATTENTION, as TJ-5016 does."""

    def test_the_income_goes_to_it_with_the_attention(self):
        # Base: [('BUYSELL', 'QZNO.EUR'), ('DIVIDEND', 'QZNO.US'),
        # ('TAX', 'QZNO.US')] and nothing said of QZNO.
        got, err = _symbols(HEAD + _T_H + _buy('EUR') + DIV_H + _div() + WHT_H + _tax())
        self.assertEqual(got, [('BUYSELL', 'QZNO.EUR'), ('DIVIDEND', 'QZNO.EUR'),
                               ('TAX', 'QZNO.EUR')])
        said = " ".join(err.split())
        self.assertIn(
            "ATTENTION: ib.csv lines 7, 9: QZNO income booked on QZNO.EUR, the one "
            "listing of QZNO the account holds, by its name alone. Neither the 2 "
            "rows (1 dividend, 1 withholding tax) nor the rows of QZNO.EUR (first "
            "row 2026-01-05) carry an ISIN. If it is this security, add `GLOBAL "
            "QZNO.US QZNO.EUR` to the project's ticker.map", said)
        self.assertEqual(said.count("QZNO income booked on"), 1)

    def test_the_confirming_line_books_the_same_way_with_nothing_said(self):
        self.load("GLOBAL QZNO.US QZNO.EUR\n")
        got, err = _symbols(HEAD + _T_H + _buy('EUR') + DIV_H + _div())
        # (the parse keeps the symbol; the run's GLOBAL line then moves it)
        self.assertEqual(got, [('BUYSELL', 'QZNO.EUR'), ('DIVIDEND', 'QZNO.US')])
        self.assertNotIn("QZNO income booked on", err)

    def test_a_return_of_capital_is_not_moved_there(self):
        msg = _refused(self, HEAD + _T_H + _buy('EUR') + DIV_H + _div(ROC))
        self.assertIn("the account holds QZNO as QZNO.EUR (first row 2026-01-05), a "
                      "listing whose rows carry no ISIN", msg)
        self.assertIn("refusing to book 1 row (1 return of capital) on QZNO.US", msg)


class TestATickerThatMayBeAClassShares(_Mapped):
    """'QZN B Cash Dividend ...' is QZN or QZN.B: the refusal never names
    a line that would book the rows on the wrong security."""

    def _buy(self, sym):
        return (f'Trades,Data,Order,Stocks,USD,{sym},"2026-01-05, 10:00:00",100,10,'
                f'10,-1000,0,1000,0,0,O\n')

    def test_the_held_class_share_is_named_and_no_quote_line(self):
        msg = _refused(self, HEAD + _T_H + self._buy('QZN B') + DIV_H
                       + _div(name='QZN B'))
        self.assertEqual(
            msg,
            "ib.csv line 7: the Description carries no ISIN, and its ticker cannot "
            "be read for sure: it starts QZN, which may be the first word of a "
            "class share's ticker (QZN B is QZN.B), and the account holds "
            "QZN.B.US — refusing to book 1 row (1 dividend) on QZN.US, a guess. If "
            "the rows are that holding's, say so in the project's ticker.map: "
            "`GLOBAL QZN.US QZN.B.US`. If they are another security's, add the "
            "statement that holds its trades or its Open Positions row.")
        self.assertNotIn("QUOTE", msg)

    def test_the_line_it_names_books_the_rows_on_the_holding(self):
        self.load("GLOBAL QZN.US QZN.B.US\n")
        got, _ = _symbols(HEAD + _T_H + self._buy('QZN B') + DIV_H
                          + _div(name='QZN B'))
        # (the parse keeps the symbol; the run's GLOBAL line then moves it)
        self.assertEqual(got, [('BUYSELL', 'QZN.B.US'), ('DIVIDEND', 'QZN.US')])

    def test_with_no_such_holding_the_ticker_is_said_unreadable(self):
        msg = _refused(self, HEAD + DIV_H + _div(name='QZN B'))
        self.assertIn("its ticker cannot be read for sure: it starts QZN", msg)
        self.assertIn("`GLOBAL QZN.US SYMBOL` with the symbol the account's trades "
                      "of it carry", msg)
        self.assertNotIn("QUOTE", msg)

    def test_a_plain_ticker_keeps_the_plain_advice(self):
        self.assertIn("(`QUOTE QZNO.US QZNO`)", _refused(self, _income()))


class TestWhatDoesNotChange(unittest.TestCase):

    def test_a_row_with_an_isin_of_a_home_market(self):
        text = (HEAD + DIV_H
                + 'Dividends,Data,USD,2026-06-30,QZNO(US0000000010) Cash Dividend '
                  'USD 0.10 per Share (Ordinary Dividend),10.00\n')
        self.assertEqual(_symbols(text)[0], [('DIVIDEND', 'QZNO.US')])

    def test_the_one_listing_held_under_a_market_suffix_takes_the_row(self):
        # (the rebind to the held listing, before the rule is asked)
        got, _ = _symbols(HEAD + _T_H + _buy('CAD') + DIV_H + _div())
        self.assertEqual(got, [('BUYSELL', 'QZNO.TO'), ('DIVIDEND', 'QZNO.TO')])

    def test_withholding_on_credit_interest_is_cash(self):
        text = (HEAD + WHT_H + 'Withholding Tax,Data,USD,2026-06-04,Withholding @ '
                '20% on Credit Interest for MAY-2026,-1.50\n')
        self.assertEqual(_symbols(text)[0], [('TAX', 'CASH')])


if __name__ == "__main__":
    unittest.main()
