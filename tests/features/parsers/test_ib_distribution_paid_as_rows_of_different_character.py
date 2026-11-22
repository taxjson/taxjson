"""Interactive Brokers: a distribution paid as rows of different character
(an ordinary dividend beside a return of capital or a capital gain), not
every one stating a per-share rate, beside a Change in Dividend Accruals
row in the payment's own currency (backlog TJ-2598).

The accrual's gross rate is the rows' rates added, so it is no row's own:
each row was booked at its cash over the gross rate (25 and 75 shares of
the 100 held). The rows are paid on the same shares, each at its own
rate. A payment in lieu beside a dividend (shares lent) splits the
shares, not the rate, and keeps the rule; with both, a side's shares are
its net cash over the gross rate, which the accrual must bear out (the
two sides add up to it, or the issuer's rows alone do), and a
distribution no reading explains, or two do, is refused (the owner's
rule: a refusal naming the rows and the fix beats a guess).

The rows are those one accrual pays, of one account, whatever day IB
posted each. No booked amount moves: the share count and rate a row
carries do (`tjs divs`, `tjs div-estimate`).

All data synthetic (made-up tickers QZRX, QZRT; accounts U1234567 and
F1234567).
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.characterize import characterize_rows
from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from taxjson.model.transaction import TaxTransaction
from _support.ib_income import _ACC_H, _DIV_H
from _support.ib_statement_rows import HEAD, TRADES_H, _parse_ib, _trade

US, CA = "US0000000010", "CA0000000012"
_ACCT, _ACCT2 = "U1234567", "F1234567"
_DAY = "2026-06-30"
ROC, ORD = "Return of Capital", "Cash Dividend (Ordinary Dividend)"
ROC_30, ORD_10 = ("Return of Capital USD 0.30 per Share",
                  "Cash Dividend USD 0.10 per Share (Ordinary Dividend)")
LTCG = "Cash Dividend (Long-Term Capital Gain)"
PIL, PIL_ROC = ("Payment in Lieu of Dividend (Ordinary Dividend)",
                "Payment in Lieu of Dividend (Return of Capital)")


def _row(what, amount, sym="QZRX", isin=US, cur="USD", date=_DAY, acct=_ACCT):
    return f'Dividends,Data,{cur},{acct},{date},{sym}({isin}) {what},{amount}\n'


def _accrual(qty, rate, gross, sym="QZRX", cur="USD", acct=_ACCT, ex='2026-06-15',
             pay=_DAY, code='Po'):
    return (f'Change in Dividend Accruals,Data,Stocks,{cur},{acct},{sym},2026-06-15,'
            f'{ex},{pay},{qty},0,0,{rate},{gross},{gross},{code}\n')


def _statement(*rows, accrual=None, sym="QZRX", cur="USD", trades=None):
    return (HEAD + TRADES_H
            + (trades or _trade(sym, '2026-01-05, 09:30:00', 100, 10, -1000, cur=cur,
                                acct=_ACCT))
            + _DIV_H + ''.join(rows) + _ACC_H
            + (_accrual(100, 0.40, 40, sym=sym, cur=cur) if accrual is None else accrual))


def _booked(text, country=None):
    """(action, shares, rate, cash) of each Dividends row, in the statement's order."""
    _, rows, _ = _parse_ib(text)
    if country:
        characterize_rows(rows, country=country, foreign_return_of_capital=None)
    for r in rows:
        assert not [k for k in r if k in ('_line', '_acct', '_where')], r
    return [(r['action'], r['quantity'], r.get('price', 0.0), r['net_amount'])
            for r in rows if r['action'] != 'BUYSELL']


def _shares(text):
    return [(g[1], g[2]) for g in _booked(text)]


def _refused(test, text):
    with test.assertRaises(BrokerageParseError) as cm:
        _parse_ib(text)
    return str(cm.exception)


class TestAReturnOfCapitalBesideAnOrdinaryDividend(unittest.TestCase):

    def test_both_rows_at_the_shares_accrued_each_at_its_own_rate(self):
        # Before the fix: 75 shares and 25 shares, each at 0.4.
        text = _statement(_row(ROC, '30.00'), _row(ORD, '10.00'))
        want = [('DIVIDEND', 100.0, 0.3, 30.0), ('DIVIDEND', 100.0, 0.1, 10.0)]
        self.assertEqual(_booked(text), want)
        # A Canadian project's foreign dividend (s.90(1)) keeps them; a US
        # project's cost reduction carries no share count.
        self.assertEqual(_booked(text, 'canada'), want)
        self.assertEqual(_booked(text, 'usa'),
                         [('ADJUST', 0.0, 0.0, -30.0), ('DIVIDEND', 100.0, 0.1, 10.0)])

    def test_either_order(self):
        text = _statement(_row(ORD, '10.00'), _row(ROC, '30.00'))
        self.assertEqual(_shares(text), [(100.0, 0.1), (100.0, 0.3)])

    def test_a_canadian_issuers_return_of_capital(self):
        # The return of capital is a cost reduction from the parse (an
        # ADJUST of no shares); the ordinary dividend was at 25 shares.
        text = _statement(_row(ROC, '30.00', sym='QZRT', isin=CA, cur='CAD'),
                          _row(ORD, '10.00', sym='QZRT', isin=CA, cur='CAD'),
                          sym='QZRT', cur='CAD')
        for country in (None, 'canada', 'usa'):
            self.assertEqual(_booked(text, country),
                             [('ADJUST', 0.0, 0.0, -30.0), ('DIVIDEND', 100.0, 0.1, 10.0)],
                             country)

    def test_a_capital_gain_beside_an_ordinary_dividend(self):
        # The same rule for any two characters (they were 75 and 25 shares).
        text = _statement(_row(LTCG, '30.00'), _row(ORD, '10.00'))
        self.assertEqual(_shares(text), [(100.0, 0.3), (100.0, 0.1)])
        text = _statement(_row(LTCG, '20.00'), _row(ORD, '10.00'), _row(ROC, '10.00'))
        self.assertEqual(_shares(text), [(100.0, 0.2), (100.0, 0.1), (100.0, 0.1)])

    def test_the_return_of_capital_posted_a_day_later(self):
        # The rows one accrual pays, whatever day IB posted each.
        text = _statement(_row(ROC, '30.00', date='2026-07-01'), _row(ORD, '10.00'))
        self.assertEqual(_shares(text), [(100.0, 0.3), (100.0, 0.1)])

    def test_a_row_stating_its_rate_keeps_it(self):
        text = _statement(_row(ROC, '30.00'),
                          _row('Cash Dividend USD 0.10 per Share (Ordinary Dividend)', '10.00'))
        self.assertEqual(_shares(text), [(100.0, 0.3), (100.0, 0.1)])

    def test_a_cent_rounded_row(self):
        # 33 shares at 0.105 and 0.295: 3.47 (3.465) and 9.74 (9.735).
        text = _statement(_row(ORD, '3.47'), _row(ROC, '9.74'),
                          accrual=_accrual(33, 0.40, 13.20))
        got = _shares(text)
        self.assertEqual([g[0] for g in got], [33.0, 33.0])
        self.assertAlmostEqual(got[0][1], 3.47 / 33, places=8)
        self.assertAlmostEqual(got[1][1], 9.74 / 33, places=8)

    def test_a_recharacterisation_on_the_day(self):
        # The rows IB reverses carry the sign; the corrected row is whole.
        text = _statement(_row(ROC, '-30.00'), _row(ORD, '-10.00'), _row(ORD, '40.00'))
        self.assertEqual(_shares(text), [(-100.0, 0.3), (-100.0, 0.1), (100.0, 0.4)])
        text = _statement(_row(ORD, '40.00'), _row(ORD, '-40.00'), _row(ORD, '10.00'),
                          _row(ROC, '30.00'))
        self.assertEqual(_shares(text),
                         [(100.0, 0.4), (-100.0, 0.4), (100.0, 0.1), (100.0, 0.3)])

    def test_with_no_shares_lent_the_shares_are_the_accruals_whatever_its_rate(self):
        # IB accrued the ordinary part alone, or each character as a Po
        # row of its own (the last is the accrual's rate): the issuer paid
        # every row on the shares accrued.
        for accrual in (_accrual(100, 0.10, 10), _accrual(100, 0.25, 25),
                        _accrual(100, 0.10, 10) + _accrual(100, 0.30, 30)):
            text = _statement(_row(ORD, '10.00'), _row(ROC, '30.00'), accrual=accrual)
            self.assertEqual(_shares(text), [(100.0, 0.1), (100.0, 0.3)], accrual)

    def test_a_lone_row_is_the_whole_payment(self):
        # Kept: the accrual's rate is the row's own.
        self.assertEqual(_shares(_statement(_row(ROC, '40.00'))), [(100.0, 0.4)])
        self.assertEqual(_shares(_statement(_row(ORD, '40.00'))), [(100.0, 0.4)])
        # Two rows of one character are no split: each its cash over the rate.
        self.assertEqual(_shares(_statement(_row(ORD, '24.00'), _row(ORD, '16.00'))),
                         [(60.0, 0.4), (40.0, 0.4)])
        self.assertEqual(_shares(_statement(_row('Cash Dividend', '24.00'),
                                            _row(ORD, '16.00'))),
                         [(60.0, 0.4), (40.0, 0.4)])

    def test_no_share_count_in_the_accrual_is_refused(self):
        msg = _refused(self, _statement(_row(ROC, '30.00'), _row(ORD, '10.00'),
                                        accrual=_accrual(0, 0.40, 40)))
        self.assertTrue(msg.startswith(
            "ib.csv lines 7, 8 (Dividends): QZRX.US: IB paid the distribution of "
            "2026-06-30 as 2 rows of different character (ordinary dividend, return of "
            "capital), not every one stating a per-share rate, and the Change in "
            "Dividend Accruals for it (0.4 USD a share, no share count) does not "
            "account for their 40.00 USD — refusing to guess"), msg)


class TestARowThatIsItsSidesOneCharacter(unittest.TestCase):
    """The lone-row rule (the row's cash over the accrual's rate) where
    IB accrued the distribution as a Po row for each character: the last
    row's rate was the accrual's, one part's (review round 2)."""
    _PARTS = _accrual(100, 0.10, 10) + _accrual(100, 0.30, 30)
    # A share count the rows do not bear out: no reading of the split.
    _STALE = _accrual(120, 0.10, 12) + _accrual(120, 0.30, 36)

    def test_a_lone_row_takes_the_parts_rates_added(self):
        # It was 133.33 shares at 0.3.
        text = _statement(_row(ORD, '40.00'), accrual=self._PARTS)
        self.assertEqual(_shares(text), [(100.0, 0.4)])
        text = _statement(_row(ORD, '24.00'), _row(PIL, '16.00'), accrual=self._PARTS)
        self.assertEqual(_shares(text), [(60.0, 0.4), (40.0, 0.4)])

    def test_a_lone_payment_in_lieu_beside_rows_stating_their_rates(self):
        # It was 53.33 shares at 0.3.
        text = _statement(_row(ORD_10, '6.00'), _row(ROC_30, '18.00'), _row(PIL, '16.00'),
                          accrual=self._STALE)
        self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4)])

    def test_a_lone_dividend_beside_payments_in_lieu_stating_their_rates(self):
        # It was 80 shares at 0.3.
        text = _statement(
            _row(ORD, '24.00'),
            _row('Payment in Lieu of Dividend USD 0.10 per Share (Ordinary Dividend)', '4.00'),
            _row('Payment in Lieu of Dividend USD 0.30 per Share (Return of Capital)', '12.00'),
            accrual=self._STALE)
        self.assertEqual(_shares(text), [(60.0, 0.4), (40.0, 0.1), (40.0, 0.3)])

    def test_po_rows_the_cash_bears_out_neither_way_are_refused(self):
        # 0.10 on 100 and 0.30 on 120: no one count for the rates added,
        # and the 40.00 is not the last row's 36.00.
        accrual = _accrual(100, 0.10, 10) + _accrual(120, 0.30, 36)
        msg = _refused(self, _statement(_row(ORD, '40.00'), accrual=accrual))
        self.assertEqual(
            msg,
            "ib.csv line 7 (Dividends): QZRX.US: the row states no per-share rate, and "
            "the Change in Dividend Accruals for the distribution of 2026-06-30 has Po "
            "rows of different rates (100 shares at 0.1 USD; 120 shares at 0.3 USD) "
            "that its rows' 40.00 USD bears out neither added (the parts of one rate) "
            "nor as the last (the accrual posted again) — refusing to guess the rate "
            "the row was paid at. Write the row's per-share rate into its Description "
            "as IB prints it (`USD 0.10 per Share`), from IB's dividend report or the "
            "issuer's notice. A row that states its rate is read from it.")
        # One count, and the cash is neither 100 x 0.40 nor 100 x 0.30.
        msg = _refused(self, _statement(_row(ORD, '24.00'), accrual=self._PARTS))
        self.assertIn("that its rows' 24.00 USD bears out neither added", msg)
        # The rows' own rates are the Po rows': the parts of one rate.
        text = _statement(_row(ORD_10, '6.00'), _row(ROC_30, '18.00'), _row(PIL, '16.00'),
                          accrual=accrual)
        self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4)])

    def test_a_po_row_repeated_is_one_rate(self):
        # Two alike Po rows with no Re between (it was 50 shares at 0.8).
        twice = _accrual(100, 0.40, 40) + _accrual(100, 0.40, 40)
        self.assertEqual(_shares(_statement(_row(ORD, '40.00'), accrual=twice)),
                         [(100.0, 0.4)])
        self.assertEqual(_shares(_statement(_row(ROC, '30.00'), _row(ORD, '10.00'),
                                            accrual=twice)),
                         [(100.0, 0.3), (100.0, 0.1)])
        self.assertEqual(_shares(_statement(_row(ORD, '6.00'), _row(ROC, '18.00'),
                                            _row(PIL, '16.00'), accrual=twice)),
                         [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4)])
        # The shares in two rows of one rate (it was refused).
        two = _accrual(100, 0.40, 40) + _accrual(20, 0.40, 8)
        self.assertEqual(_shares(_statement(_row(ORD, '48.00'), accrual=two)),
                         [(120.0, 0.4)])
        # Split by character, the cash says which count: both rows' 120.
        self.assertEqual(_shares(_statement(_row(ROC, '36.00'), _row(ORD, '12.00'),
                                            accrual=two)),
                         [(120.0, 0.3), (120.0, 0.1)])
        # Two characters of one rate each: the cash is the count at the sum.
        halves = _accrual(100, 0.20, 20) + _accrual(100, 0.20, 20)
        self.assertEqual(_shares(_statement(_row(ORD, '40.00'), accrual=halves)),
                         [(100.0, 0.4)])
        self.assertEqual(_shares(_statement(_row(ROC, '20.00'), _row(ORD, '20.00'),
                                            accrual=halves)),
                         [(100.0, 0.2), (100.0, 0.2)])
        # A row stating the repeated rate beside a lone payment in lieu: the one rate, not twice it.
        text = _statement(_row('Cash Dividend USD 0.40 per Share (Ordinary Dividend)', '24.00'),
                          _row(PIL, '16.00'), accrual=twice)
        self.assertEqual(_shares(text), [(60.0, 0.4), (40.0, 0.4)])
        # Posted again at another rate with no Re: the cash is the last row's.
        again = _accrual(100, 0.38, 38) + _accrual(100, 0.40, 40)
        self.assertEqual(_shares(_statement(_row(ORD, '40.00'), accrual=again)),
                         [(100.0, 0.4)])

    def test_an_accrual_posted_again_is_its_last_row(self):
        # Po, its Re, then the revised Po: one rate, the last (not 0.78).
        accrual = (_accrual(100, 0.38, 38) + _accrual(-100, 0.38, -38, code='Re')
                   + _accrual(100, 0.40, 40) + _accrual(-100, 0.40, -40, code='Re'))
        self.assertEqual(_shares(_statement(_row(ORD, '40.00'), accrual=accrual)),
                         [(100.0, 0.4)])
        # The parts' reversals at the payment do not undo them.
        accrual = (self._PARTS + _accrual(-100, 0.10, -10, code='Re')
                   + _accrual(-100, 0.30, -30, code='Re'))
        self.assertEqual(_shares(_statement(_row(ORD, '40.00'), accrual=accrual)),
                         [(100.0, 0.4)])


class TestARowPostedAfterThePayDate(unittest.TestCase):

    def test_the_next_weeks_row_is_no_part_of_this_accruals_distribution(self):
        # A weekly payer: the return of capital of 07-06 (50 shares then)
        # with its own accrual in no statement. The first accrual's 40.00
        # is the ordinary dividend's, so the later row is not its: no
        # share count is known for it (it was shown at this accrual's 100).
        text = _statement(_row(ORD, '40.00'), _row(ROC, '15.00', date='2026-07-06'))
        self.assertEqual(_booked(text), [('DIVIDEND', 100.0, 0.4, 40.0),
                                         ('DIVIDEND', 0.0, 0.0, 15.0)])
        # The row says its count is not known (0 alone reads as a row
        # whose description names none); no other row carries the fact.
        _, rows, _ = _parse_ib(text)
        characterize_rows(rows, country='canada', foreign_return_of_capital=None)
        self.assertEqual([r.get('no_share_count') for r in rows if r['action'] != 'BUYSELL'],
                         [None, 'no-accrual'])
        self.assertEqual(TaxTransaction(**rows[-1]).to_dict()['no_share_count'], 'no-accrual')
        self.assertNotIn('no_share_count', TaxTransaction(**rows[-2]).to_dict())
        # A row that states its rate keeps its own count, unmarked.
        text = _statement(_row(ORD, '40.00'), _row(ROC_30, '15.00', date='2026-07-06'))
        _, rows, _ = _parse_ib(text)
        self.assertEqual([(r['quantity'], r.get('no_share_count')) for r in rows[1:]],
                         [(100.0, None), (50.0, None)])
        # Its own accrual in the statement: its shares.
        text = _statement(
            _row(ORD, '40.00'), _row(ROC, '15.00', date='2026-07-06'),
            accrual=(_accrual(100, 0.40, 40)
                     + _accrual(50, 0.30, 15, ex='2026-07-01', pay='2026-07-06')))
        self.assertEqual(_shares(text), [(100.0, 0.4), (50.0, 0.3)])
        # The same of a row of the same character (it was 37.5 shares at 0.4).
        text = _statement(_row(ORD, '40.00'), _row(ORD, '15.00', date='2026-07-06'))
        self.assertEqual(_shares(text), [(100.0, 0.4), (0.0, 0.0)])

    def test_a_later_row_the_accrual_accounts_for_is_part_of_it(self):
        text = _statement(_row(ROC, '30.00', date='2026-07-01'), _row(ORD, '10.00'))
        self.assertEqual(_shares(text), [(100.0, 0.3), (100.0, 0.1)])
        text = _statement(_row(ROC, '30.00', date='2026-07-01'),
                          _row(ORD, '10.00', date='2026-07-01'))
        self.assertEqual(_shares(text), [(100.0, 0.3), (100.0, 0.1)])
        # The accrual in a Po row for each character: the two rows are
        # its 40.00, though the first alone is its last row's 30.00.
        text = _statement(_row(ORD, '30.00'), _row(ROC, '10.00', date='2026-07-01'),
                          accrual=_accrual(100, 0.10, 10) + _accrual(100, 0.30, 30))
        self.assertEqual(_shares(text), [(100.0, 0.3), (100.0, 0.1)])

    def test_a_later_reversal_or_reclassification_is_part_of_it(self):
        text = _statement(_row(ORD, '10.00'), _row(ROC, '30.00'),
                          _row(ROC, '-30.00', date='2026-07-02'))
        self.assertEqual(_shares(text), [(100.0, 0.1), (100.0, 0.3), (-100.0, 0.3)])
        text = _statement(_row(ORD, '40.00'), _row(ORD, '-30.00', date='2026-07-02'),
                          _row(ROC, '30.00', date='2026-07-02'))
        self.assertEqual(_shares(text), [(100.0, 0.4), (-100.0, 0.3), (100.0, 0.3)])

    def test_a_later_row_nothing_accounts_for_is_refused(self):
        # 10.00 on the pay date and 20.00 a day later: neither the two
        # nor the first alone is the accrual's 40.00.
        msg = _refused(self, _statement(_row(ORD, '10.00'),
                                        _row(ROC, '20.00', date='2026-07-01')))
        self.assertIn("ib.csv lines 7, 8 (Dividends): QZRX.US: IB paid the distribution "
                      "of 2026-06-30 as 2 rows of different character", msg)
        self.assertIn("does not account for their 30.00 USD", msg)


class TestTwoAccountsInOneStatement(unittest.TestCase):
    _TRADES = (_trade('QZRX', '2026-01-05, 09:30:00', 100, 10, -1000, acct=_ACCT)
               + _trade('QZRX', '2026-01-05, 09:30:00', 50, 10, -500, acct=_ACCT2))
    _ACCRUALS = _accrual(100, 0.40, 40) + _accrual(50, 0.40, 20, acct=_ACCT2)

    def test_each_accounts_rows_at_its_own_accrual(self):
        # The second account's 50 shares were shown as 100.
        text = _statement(_row(ROC, '30.00'), _row(ORD, '10.00'),
                          _row(ROC, '15.00', acct=_ACCT2), _row(ORD, '5.00', acct=_ACCT2),
                          accrual=self._ACCRUALS, trades=self._TRADES)
        self.assertEqual(_shares(text),
                         [(100.0, 0.3), (100.0, 0.1), (50.0, 0.3), (50.0, 0.1)])

    def test_one_account_lends_shares(self):
        # The other account's rows are not part of its distribution.
        text = _statement(_row(ROC, '30.00'), _row(ORD, '10.00'),
                          _row(ROC, '9.00', acct=_ACCT2), _row(ORD, '3.00', acct=_ACCT2),
                          _row(PIL, '8.00', acct=_ACCT2),
                          accrual=self._ACCRUALS, trades=self._TRADES)
        self.assertEqual(_shares(text), [(100.0, 0.3), (100.0, 0.1), (30.0, 0.3),
                                         (30.0, 0.1), (20.0, 0.4)])

    def test_a_lone_row_takes_its_own_accounts_accrual(self):
        text = _statement(_row(ORD, '5.00', acct=_ACCT2), _row(ORD, '80.00'),
                          accrual=(_accrual(100, 0.80, 80)
                                   + _accrual(50, 0.10, 5, acct=_ACCT2)),
                          trades=self._TRADES)
        self.assertEqual(_shares(text), [(50.0, 0.1), (100.0, 0.8)])


class TestSharesLentOnTheDay(unittest.TestCase):

    def test_a_payment_in_lieu_beside_a_dividend_keeps_the_rate_rule(self):
        # Kept: 60 shares held, 40 lent, each row at the gross rate.
        text = _statement(_row(ORD, '24.00'), _row(PIL, '16.00'))
        self.assertEqual(_booked(text), [('DIVIDEND', 60.0, 0.4, 24.0),
                                         ('DIVIDEND_IN_LIEU', 40.0, 0.4, 16.0)])
        # A return of capital and a payment in lieu of the whole: one
        # character a side.
        text = _statement(_row(ROC, '24.00'), _row(PIL, '16.00'))
        self.assertEqual(_shares(text), [(60.0, 0.4), (40.0, 0.4)])

    def test_a_split_by_character_with_shares_lent(self):
        # 60 held (0.10 ordinary, 0.30 return of capital), 40 lent.
        text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.00'))
        self.assertEqual(_booked(text), [('DIVIDEND', 60.0, 0.1, 6.0),
                                         ('DIVIDEND', 60.0, 0.3, 18.0),
                                         ('DIVIDEND_IN_LIEU', 40.0, 0.4, 16.0)])

    def test_the_payments_in_lieu_split_by_character_too(self):
        text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'),
                          _row(PIL, '4.00'), _row(PIL_ROC, '12.00'))
        self.assertEqual(_shares(text),
                         [(60.0, 0.1), (60.0, 0.3), (40.0, 0.1), (40.0, 0.3)])
        # One character among the issuer's rows, two among the payments in lieu.
        text = _statement(_row(ORD, '24.00'), _row(PIL, '4.00'), _row(PIL_ROC, '12.00'))
        self.assertEqual(_shares(text), [(60.0, 0.4), (40.0, 0.1), (40.0, 0.3)])
        # Every share lent: the payments in lieu are the accrual's 100.
        text = _statement(_row(PIL, '10.00'), _row(PIL_ROC, '30.00'))
        self.assertEqual(_shares(text), [(100.0, 0.1), (100.0, 0.3)])

    def test_a_reversed_row_and_its_repost_net_out(self):
        # A reclassification with shares lent: 24.00 reversed, 6.00 and
        # 18.00 in its place (it was refused as 64.00 against 40.00).
        text = _statement(_row(ORD, '24.00'), _row(PIL, '16.00'), _row(ORD, '-24.00'),
                          _row(ORD, '6.00'), _row(ROC, '18.00'))
        self.assertEqual(_shares(text), [(60.0, 0.4), (40.0, 0.4), (-60.0, 0.4),
                                         (60.0, 0.1), (60.0, 0.3)])
        text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.00'),
                          _row(ORD, '-6.00'), _row(ORD, '6.00'))
        self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4),
                                         (-60.0, 0.1), (60.0, 0.1)])

    def test_cent_rounded_rows(self):
        # 33 held (3.465, 9.735), 17 lent: the rows add up to 20.01, a
        # cent over the accrual, and the issuer's 13.21 is 33.025 shares
        # at 0.40: each row's cent of rounding is allowed for.
        text = _statement(_row(ORD, '3.47'), _row(ROC, '9.74'), _row(PIL, '6.80'),
                          accrual=_accrual(50, 0.40, 20))
        got = _shares(text)
        self.assertEqual([g[0] for g in got], [33.0, 33.0, 17.0])
        self.assertAlmostEqual(got[0][1], 3.47 / 33, places=8)
        self.assertEqual(got[2][1], 0.4)
        # A fraction of a share is kept.
        text = _statement(_row(ROC, '6.12'), _row(ORD, '2.04'), _row(PIL, '5.24'),
                          accrual=_accrual(33.5, 0.40, 13.40))
        self.assertEqual(_shares(text), [(20.4, 0.3), (20.4, 0.1), (13.1, 0.4)])

    def test_the_accrual_in_a_row_for_each_character(self):
        # Two Po rows under one ex date (the last was the accrual's rate),
        # or under two: the gross rate is their rates added.
        for accrual in (_accrual(100, 0.10, 10) + _accrual(100, 0.30, 30),
                        _accrual(100, 0.10, 10) + _accrual(100, 0.30, 30, ex='2026-06-16')):
            text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.00'),
                              accrual=accrual)
            self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4)],
                             accrual)

    def test_the_accrual_of_the_shares_held_alone(self):
        text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.00'),
                          accrual=_accrual(60, 0.40, 24))
        self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4)])

    def test_a_revised_accrual_is_its_last_row(self):
        # A Po row of 60 shares, then the accrual's 100 at 0.40: rows of
        # different share counts are no parts of one distribution (added,
        # 60 x 0.666667 would be a second reading of the 40.00).
        text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.00'),
                          accrual=_accrual(60, 0.266667, 16) + _accrual(100, 0.40, 40))
        self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4)])

    def test_another_distribution_of_the_pay_date_is_not_added_to_the_rates(self):
        # A second accrual of the day (0.50, another ex date) beside the
        # two Po rows of this one: their own sum, 0.40, is a reading.
        text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.00'),
                          accrual=(_accrual(100, 0.10, 10) + _accrual(100, 0.30, 30)
                                   + _accrual(100, 0.50, 50, ex='2026-06-16')))
        self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4)])

    def test_the_same_accrual_in_an_overlapping_statement_counts_once(self):
        accrual = _accrual(100, 0.10, 10) + _accrual(100, 0.30, 30, ex='2026-06-16')
        with tempfile.TemporaryDirectory() as td:
            paths = [Path(td) / 'a.csv', Path(td) / 'b.csv']
            paths[0].write_text(_statement(accrual=accrual))
            paths[1].write_text(_statement(_row(ORD, '6.00'), _row(ROC, '18.00'),
                                           _row(PIL, '16.00'), accrual=accrual))
            with contextlib.redirect_stderr(io.StringIO()):
                ib = IbBrokerage()
                ib.account_context = IbBrokerage.prepare_files(paths)
                rows = ib.parse_file(paths[1])
        self.assertEqual([(r['quantity'], r['price']) for r in rows
                          if r['action'] != 'BUYSELL'],
                         [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4)])

    def test_another_accrual_is_no_part_of_the_distributions(self):
        # An accrual of another pay date, another account or another
        # currency, of a rate that would make a second reading with this
        # one's (60 x 0.666667 is the rows' 40.00): not added to it.
        held = _accrual(60, 0.40, 24)
        for other in (_accrual(60, 0.266667, 16, ex='2026-06-22', pay='2026-07-06'),
                      _accrual(60, 0.266667, 16, ex='2026-06-16', acct=_ACCT2),
                      _accrual(60, 0.266667, 16, ex='2026-06-16', cur='CAD')):
            for accrual in (held + other, other + held):
                text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'),
                                  _row(PIL, '16.00'), accrual=accrual)
                self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4)],
                                 accrual)

    def test_a_row_in_another_currency_is_no_part_of_the_distribution(self):
        # The same security paid in CAD too (its accrual the USD one, the
        # only one): its cash is not this distribution's.
        text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.00'),
                          _row('Cash Dividend CAD 0.05 per Share (Ordinary Dividend)',
                               '5.00', cur='CAD'))
        self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (40.0, 0.4), (100.0, 0.05)])

    def test_two_payments_in_lieu_of_one_character_beside_a_split(self):
        # Each its cash over the gross rate, as two beside a lone dividend.
        text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '10.00'),
                          _row(PIL, '6.00'))
        self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (25.0, 0.4), (15.0, 0.4)])

    def test_cent_rounded_payments_in_lieu(self):
        # 17 lent at 0.105 and 0.295: 1.79 (1.785) and 5.02 (5.015).
        text = _statement(_row(ORD, '3.47'), _row(ROC, '9.74'), _row(PIL, '1.79'),
                          _row(PIL_ROC, '5.02'), accrual=_accrual(50, 0.40, 20))
        self.assertEqual([g[0] for g in _shares(text)], [33.0, 33.0, 17.0, 17.0])

    def test_the_issuers_rows_all_reversed_are_refused(self):
        # The accrual explains the payments in lieu; the issuer's rows
        # net to nothing, so their shares are not in the statement.
        msg = _refused(self, _statement(
            _row(ORD, '6.00'), _row(ROC, '18.00'), _row(ORD, '-6.00'),
            _row(ROC, '-18.00'), _row(PIL, '16.00'), accrual=_accrual(40, 0.40, 16)))
        self.assertIn("ib.csv lines 7, 8, 9, 10, 11 (Dividends): QZRX.US:", msg)

    def test_a_distribution_the_accrual_does_not_explain_is_refused(self):
        # 6 + 18 + 10 is not the 40.00 accrued, nor 6 + 18: the shares
        # held cannot be told from the shares lent.
        msg = _refused(self, _statement(_row(ORD, '6.00'), _row(ROC, '18.00'),
                                        _row(PIL, '10.00')))
        self.assertEqual(
            msg,
            "ib.csv lines 7, 8, 9 (Dividends): QZRX.US: IB paid the distribution of "
            "2026-06-30 as 3 rows of different character (ordinary dividend, return of "
            "capital; 1 of them in lieu, shares lent), not every one stating a "
            "per-share rate, and the Change in Dividend Accruals for it (100 shares at "
            "0.4 USD, 40.00 USD) does not account for their 34.00 USD — refusing to "
            "guess the shares each row was paid on. Write each row's per-share rate "
            "into its Description as IB prints it (`USD 0.10 per Share`): the rate of "
            "the row's own character, from IB's dividend report or the issuer's notice "
            "(a payment in lieu's: the rate of what it replaces). A row that states "
            "its rate is read from it.")
        # A cent and a half off over three rows is within their rounding;
        # two cents is not.
        text = _statement(_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.02'))
        self.assertIn("does not account for their 40.02 USD", _refused(self, text))

    def test_a_lone_payment_in_lieu_beside_rows_stating_their_rates(self):
        # Kept: where the accrual does not explain the distribution (a
        # reversal of twice the return of capital, two days later), a
        # payment in lieu that is its side's one character is its cash
        # over the accrual's rate, as a lone row; no row of a side split
        # by character lacks a rate, so nothing is a guess.
        text = _statement(
            _row('Return of Capital USD 0.12 per Share', '12.00'),
            _row('Cash Dividend USD 0.12 per Share (Ordinary Dividend)', '12.00'),
            _row('Return of Capital USD 0.12 per Share', '-24.00', date='2026-07-02'),
            _row(PIL_ROC, '3.00'), accrual=_accrual(100, 0.12, 12))
        self.assertEqual(_shares(text), [(100.0, 0.12), (100.0, 0.12), (-200.0, 0.12),
                                         (25.0, 0.12)])

    def test_two_readings_of_the_accrual_are_refused(self):
        # Po rows of 0.10 and 0.30 on 100 shares; 10.00 from the issuer,
        # 30.00 in lieu: 25 held and 75 lent at 0.40, or 100 held at 0.10
        # (the accrual of the shares held alone).
        msg = _refused(self, _statement(
            _row(ORD, '2.50'), _row(ROC, '7.50'), _row(PIL, '30.00'),
            accrual=_accrual(100, 0.30, 30) + _accrual(100, 0.10, 10)))
        self.assertIn("(100 shares at 0.1 USD, 10.00 USD; 100 shares at 0.4 USD, "
                      "40.00 USD) does not account for their 40.00 USD", msg)

    def test_the_fix_the_refusal_names_is_read(self):
        # Each row's rate in its Description: nothing is asked of the accrual.
        text = _statement(
            _row('Cash Dividend USD 0.10 per Share (Ordinary Dividend)', '6.00'),
            _row('Return of Capital USD 0.30 per Share', '18.00'),
            _row('Payment in Lieu of Dividend USD 0.40 per Share (Ordinary Dividend)', '10.00'))
        self.assertEqual(_shares(text), [(60.0, 0.1), (60.0, 0.3), (25.0, 0.4)])


if __name__ == '__main__':
    unittest.main()
