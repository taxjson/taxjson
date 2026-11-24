"""Interactive Brokers: two distributions of one security that share a pay
date (two Change in Dividend Accruals rows, an ex date each), their
Dividends rows matched to them by the statement's evidence (backlog
TJ-5013; the older items TJ-4903 and TJ-4902).

Nothing on a Dividends row names the accrual it pays, and every row of
the day took the first: a second dividend of 100 shares at 0.50 was
booked as 125 shares at 0.40, and a special dividend stating its rate was
counted into the regular distribution's cash, which then explained
nothing and stopped the parse. The rows are matched now by the rate a row
states, by the cash against each accrual's shares and rate (the shares
lent with it) and by the rows' characters; one accrual pays one
distribution; where the cash fits more than one way, what else a way
says decides (whole share counts, the same shares lent under each
accrual, the most accruals with rows of their own); a match that still
stays open is refused, naming the rows and the fix, and the first
accrual is never taken for it.

No booked amount moves: the share count, the rate and the ex date a row
carries do (`tjs divs`, `tjs div-estimate`). The spec (`tjs tax-logic`)
states no rule for them, so no rule is cited: a row that states no ex
date is keyed to its pay date there.

All data synthetic (the made-up ticker QZRX, the account U1234567).
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from _support.ib_income import _ACC_H, _DIV_H
from _support.ib_statement_rows import HEAD, TRADES_H, _parse_ib, _trade

US = "US0000000010"
_ACCT = "U1234567"
_DAY = "2026-06-30"
EX_A, EX_B = "2026-06-15", "2026-06-16"
ORD, ROC = "Cash Dividend (Ordinary Dividend)", "Return of Capital"
SPECIAL = "Cash Dividend (Special Dividend)"
SPECIAL_50 = "Cash Dividend USD 0.50 per Share (Special Dividend)"
ORD_40 = "Cash Dividend USD 0.40 per Share (Ordinary Dividend)"
PIL = "Payment in Lieu of Dividend (Ordinary Dividend)"


def _row(what, amount, cur="USD", date=_DAY):
    return f'Dividends,Data,{cur},{_ACCT},{date},QZRX({US}) {what},{amount}\n'


def _accrual(qty, rate, gross, ex=EX_A, pay=_DAY, cur="USD", code='Po'):
    return (f'Change in Dividend Accruals,Data,Stocks,{cur},{_ACCT},QZRX,2026-06-15,'
            f'{ex},{pay},{qty},0,0,{rate},{gross},{gross},{code}\n')


# The regular dividend (0.40) and a second one (0.50), one pay date.
TWO = _accrual(100, 0.40, 40) + _accrual(100, 0.50, 50, ex=EX_B)


def _statement(*rows, accrual=TWO):
    return (HEAD + TRADES_H
            + _trade('QZRX', '2026-01-05, 09:30:00', 100, 10, -1000, acct=_ACCT)
            + _DIV_H + ''.join(rows) + _ACC_H + accrual)


def _facts(text):
    """(shares, rate, ex date) of each Dividends row, in the statement's
    order, and what the parse said."""
    _, rows, err = _parse_ib(text)
    for r in rows:
        assert not [k for k in r if k in ('_line', '_acct', '_rated', '_ex_open')], r
    return ([(r['quantity'], r.get('price', 0.0), r.get('ex_date'))
             for r in rows if r['action'] != 'BUYSELL'], err)


def _refused(test, text):
    with test.assertRaises(BrokerageParseError) as cm:
        _parse_ib(text)
    return str(cm.exception)


class TestTwoDividendsOfOnePayDate(unittest.TestCase):
    """TJ-4903: two rate-less rows of one character, an accrual each."""

    def test_each_row_at_its_own_accruals_shares_and_rate(self):
        # Before the fix: (100, 0.4) and (125, 0.4), both ex 2026-06-15.
        got, err = _facts(_statement(_row(ORD, '40.00'), _row(ORD, '50.00')))
        self.assertEqual(got, [(100.0, 0.4, EX_A), (100.0, 0.5, EX_B)])
        self.assertNotIn('distributions share the pay date', err)

    def test_either_order_of_the_rows_or_the_accruals(self):
        got, _ = _facts(_statement(_row(ORD, '50.00'), _row(ORD, '40.00')))
        self.assertEqual(got, [(100.0, 0.5, EX_B), (100.0, 0.4, EX_A)])
        got, _ = _facts(_statement(
            _row(ORD, '40.00'), _row(ORD, '50.00'),
            accrual=_accrual(100, 0.50, 50, ex=EX_B) + _accrual(100, 0.40, 40)))
        self.assertEqual(got, [(100.0, 0.4, EX_A), (100.0, 0.5, EX_B)])

    def test_a_row_stating_its_rate_is_its_accruals(self):
        # 80 of the second dividend's 100 shares paid (no row for the
        # rest): the rate the row states is the second accrual's.
        got, _ = _facts(_statement(_row(ORD, '40.00'), _row(SPECIAL_50, '40.00')))
        self.assertEqual(got, [(100.0, 0.4, EX_A), (80.0, 0.5, EX_B)])

    def test_one_row_alone_is_the_accrual_its_cash_is(self):
        # The other dividend is paid in another statement, or not yet.
        got, _ = _facts(_statement(_row(ORD, '50.00')))
        self.assertEqual(got, [(100.0, 0.5, EX_B)])

    def test_a_payment_in_lieu_beside_each_dividend(self):
        # 60 held and 40 lent, both dividends: 24 + 16 and 30 + 20.
        got, _ = _facts(_statement(_row(ORD, '24.00'), _row(PIL, '16.00'),
                                   _row(SPECIAL, '30.00'), _row(PIL, '20.00')))
        self.assertEqual(got, [(60.0, 0.4, EX_A), (40.0, 0.4, EX_A),
                               (60.0, 0.5, EX_B), (40.0, 0.5, EX_B)])

    def test_the_accruals_in_another_statement_of_the_account(self):
        with tempfile.TemporaryDirectory() as td:
            paths = [Path(td) / 'a.csv', Path(td) / 'b.csv']
            paths[0].write_text(_statement())
            paths[1].write_text(_statement(_row(ORD, '40.00'), _row(ORD, '50.00'),
                                           accrual=''))
            with contextlib.redirect_stderr(io.StringIO()):
                ib = IbBrokerage()
                ib.account_context = IbBrokerage.prepare_files(paths)
                rows = ib.parse_file(paths[1])
        self.assertEqual([(r['quantity'], r['price'], r.get('ex_date')) for r in rows
                          if r['action'] != 'BUYSELL'],
                         [(100.0, 0.4, EX_A), (100.0, 0.5, EX_B)])

    def test_an_overlapping_statements_copy_of_an_accrual_is_no_second_one(self):
        # The same accrual in two statements is one distribution: no tie.
        with tempfile.TemporaryDirectory() as td:
            paths = [Path(td) / 'a.csv', Path(td) / 'b.csv']
            one = _accrual(100, 0.40, 40)
            paths[0].write_text(_statement(accrual=one))
            paths[1].write_text(_statement(_row(ORD, '44.00'), accrual=one))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ib = IbBrokerage()
                ib.account_context = IbBrokerage.prepare_files(paths)
                rows = ib.parse_file(paths[1])
        self.assertEqual([(r['quantity'], r['price'], r.get('ex_date')) for r in rows
                          if r['action'] != 'BUYSELL'], [(110.0, 0.4, EX_A)])
        self.assertNotIn('distributions share the pay date', err.getvalue())


class TestASpecialDividendBesideADistributionSplitByCharacter(unittest.TestCase):
    """TJ-4902: the special dividend's row is no part of the regular
    distribution's cash."""

    REGULAR = (_row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.00'))
    WANT = [(60.0, 0.1, EX_A), (60.0, 0.3, EX_A), (40.0, 0.4, EX_A), (60.0, 0.5, EX_B)]

    def test_the_special_states_its_rate(self):
        # Before the fix: refused ("as 4 rows of different character ...
        # does not account for their 70.00 USD").
        for regular in (_accrual(100, 0.10, 10) + _accrual(100, 0.30, 30),
                        _accrual(100, 0.40, 40)):
            text = _statement(*self.REGULAR, _row(SPECIAL_50, '30.00'),
                              accrual=regular + _accrual(100, 0.50, 50, ex=EX_B))
            self.assertEqual(_facts(text)[0], self.WANT, regular)

    def test_the_special_states_no_rate_and_its_cash_is_its_accruals(self):
        text = _statement(*self.REGULAR, _row(SPECIAL, '50.00'),
                          accrual=_accrual(100, 0.40, 40)
                          + _accrual(100, 0.50, 50, ex=EX_B))
        self.assertEqual(_facts(text)[0], self.WANT[:3] + [(100.0, 0.5, EX_B)])

    def test_the_regular_rows_alone_keep_their_reading(self):
        # (control) The second accrual unpaid: as before the fix.
        text = _statement(*self.REGULAR,
                          accrual=_accrual(100, 0.10, 10) + _accrual(100, 0.30, 30)
                          + _accrual(100, 0.50, 50, ex=EX_B))
        self.assertEqual(_facts(text)[0], self.WANT[:3])

    def test_one_distribution_accrued_under_two_ex_dates(self):
        # (control) A Po row for each character, an ex date each, and no
        # row of its own for either: one distribution, the first's ex date.
        text = _statement(*self.REGULAR,
                          accrual=_accrual(100, 0.10, 10)
                          + _accrual(100, 0.30, 30, ex=EX_B))
        self.assertEqual(_facts(text)[0], self.WANT[:3])


class TestAMatchTheEvidenceLeavesOpen(unittest.TestCase):

    def test_cash_that_is_neither_accruals_is_refused(self):
        # 110 shares paid, 100 accrued: 44.00 is 110 at 0.40 or 88 at
        # 0.50. Before the fix both rows took the first accrual's rate.
        msg = _refused(self, _statement(_row(ORD, '44.00'), _row(ORD, '55.00')))
        self.assertEqual(
            msg,
            "ib.csv lines 7, 8 (Dividends): QZRX.US: 2 distributions share the pay "
            "date 2026-06-30 (Change in Dividend Accruals: 100 shares at 0.4 USD, ex "
            "date 2026-06-15; 100 shares at 0.5 USD, ex date 2026-06-16), nothing on a "
            "Dividends row names the one it pays, and the rows' rates, amounts and "
            "shares match them no way — refusing to guess the distribution each row "
            "pays, and with it the row's shares, rate and ex date. Write each row's "
            "per-share rate into its Description as IB prints it (`USD 0.10 per "
            "Share`), from IB's dividend report or the issuer's notice. A row that "
            "states its rate is read from it.")

    def test_one_row_that_is_neither_accruals_cash_is_refused(self):
        msg = _refused(self, _statement(_row(ORD, '44.00')))
        self.assertIn("ib.csv line 7 (Dividends): QZRX.US: 2 distributions share", msg)

    def test_the_fix_the_refusal_names_is_read(self):
        got, err = _facts(_statement(
            _row(ORD_40, '44.00'), _row(SPECIAL_50, '55.00')))
        self.assertEqual(got, [(110.0, 0.4, EX_A), (110.0, 0.5, EX_B)])
        self.assertNotIn('distributions share the pay date', err)

    def test_two_ways_that_differ_are_refused(self):
        # 40 + 10 is the second accrual's 50.00 (10 a payment in lieu) or
        # the first's 40.00 beside a row of the second's.
        msg = _refused(self, _statement(
            _row(ORD, '40.00'), _row(PIL, '10.00'),
            accrual=_accrual(100, 0.40, 40) + _accrual(100, 0.50, 50, ex=EX_B)
            + _accrual(100, 0.10, 10, ex='2026-06-17')))
        self.assertIn("3 distributions share the pay date 2026-06-30", msg)
        self.assertIn("match them more than one way", msg)


class TestAccrualsAlikeInSharesAndRate(unittest.TestCase):
    """Two accruals of one rate and share count: no share count or rate
    hangs on the match, so nothing is refused; an ex date it leaves open
    is not recorded, and said."""

    ALIKE = _accrual(100, 0.40, 40) + _accrual(100, 0.40, 40, ex=EX_B)

    def test_two_rows_alike_take_an_accrual_each(self):
        got, err = _facts(_statement(_row(ORD, '40.00'), _row(ORD, '40.00'),
                                     accrual=self.ALIKE))
        self.assertEqual(got, [(100.0, 0.4, EX_A), (100.0, 0.4, EX_B)])
        self.assertNotIn('distributions share the pay date', err)

    def test_rows_of_two_characters_keep_their_shares_with_no_ex_date(self):
        # Before the fix both rows carried the first ex date.
        got, err = _facts(_statement(_row(ORD, '40.00'), _row(SPECIAL, '40.00'),
                                     accrual=self.ALIKE))
        self.assertEqual(got, [(100.0, 0.4, None), (100.0, 0.4, None)])
        # (an ATTENTION line: the run echoes it to the console)
        self.assertIn(
            "warning: ATTENTION: ib.csv lines 7, 8: QZRX.US: no ex date recorded for "
            "2 dividend rows — 2 "
            "distributions share the pay date 2026-06-30 (ex dates 2026-06-15, "
            "2026-06-16) and nothing in the statement says which of them these rows "
            "pay: the shares and rate are the same either way; no ex date is recorded "
            "for them, so what reads an ex date (a US project's short-sale and "
            "holding-period rules, the slip audit's T3 year) goes by the pay date. "
            "Nothing in a statement can say more; a statement in which the two are "
            "not paid on one day does.\n", err)

    def test_it_is_said_once_a_security(self):
        # Two pay dates of the kind in one statement: one line, the
        # second pay date's rows named after the first's.
        july = (_accrual(100, 0.40, 40, ex='2026-07-15', pay='2026-07-31')
                + _accrual(100, 0.40, 40, ex='2026-07-16', pay='2026-07-31'))
        _, err = _facts(_statement(
            _row(ORD, '40.00'), _row(SPECIAL, '40.00'),
            _row(ORD, '40.00', date='2026-07-31'), _row(SPECIAL, '40.00', date='2026-07-31'),
            accrual=self.ALIKE + july))
        self.assertEqual(err.count('no ex date is recorded'), 1)
        self.assertIn("goes by the pay date. Nothing in a statement can say more; a "
                      "statement in which the two are not paid on one day does. The "
                      "same holds for lines 9, 10, of 1 more pay date.", err)

    def test_cash_no_accrual_explains_keeps_the_rate_they_share(self):
        got, err = _facts(_statement(_row(ORD, '44.00'), accrual=self.ALIKE))
        self.assertEqual(got, [(110.0, 0.4, None)])
        self.assertIn("ib.csv line 7: QZRX.US: no ex date recorded for 1 dividend row",
                      err)
        self.assertIn("which of them this row pays", err)

    def test_rows_that_state_their_rates_are_never_refused(self):
        # Neither rate is an accrual's: the rows are read from their
        # descriptions, with no ex date.
        got, err = _facts(_statement(
            _row('Cash Dividend USD 0.22 per Share (Ordinary Dividend)', '22.00'),
            _row('Cash Dividend USD 0.33 per Share (Ordinary Dividend)', '33.00')))
        self.assertEqual(got, [(100.0, 0.22, None), (100.0, 0.33, None)])
        self.assertIn("no ex date is recorded for them", err)


class TestAmountsThatFitByCoincidence(unittest.TestCase):
    """Cash can fit the accruals more than one way. What else a way says
    decides before a refusal: whole share counts, the same shares lent
    under each accrual, the most accruals with rows of their own."""

    PARTS = _accrual(100, 0.10, 10) + _accrual(100, 0.30, 30, ex=EX_B)

    def test_one_distribution_in_parts_whose_payment_in_lieu_is_one_parts_cash(self):
        # 75 held and 25 lent of a distribution accrued as 0.10 and 0.30
        # under two ex dates: the payment in lieu's 10.00 is also the first
        # part's cash (all 100 lent, none held) beside the other rows as
        # the second's (none lent), and the dividends alone the second's
        # with 33.33 shares lent. As base read it: one distribution.
        got, err = _facts(_statement(_row(ORD, '7.50'), _row(ROC, '22.50'),
                                     _row(PIL, '10.00'), accrual=self.PARTS))
        self.assertEqual(got, [(75.0, 0.1, EX_A), (75.0, 0.3, EX_A), (25.0, 0.4, EX_A)])
        # The other whole-share reading gives other shares and rates: said.
        self.assertIn(
            "warning: ATTENTION: ib.csv lines 7, 8, 9: QZRX.US: the shares and rates "
            "booked are one of 2 readings the dividend rows' amounts fit — 2 "
            "distributions share the pay date 2026-06-30, nothing on a Dividends row "
            "names the one it pays, and the rows' amounts fit them more than one way "
            "in whole shares. Booked: lines 7, 8, 9 under the pay date's accruals read "
            "as one distribution of 0.4 USD a share: 75 shares held and 25 lent. Set "
            "aside: line 9 under the accrual of 100 shares at 0.1 USD, ex date "
            "2026-06-15: 0 shares held and 100 lent; lines 7, 8 under the accrual of "
            "100 shares at 0.3 USD, ex date 2026-06-16: 100 shares held. The reading "
            "booked is the one with the same shares lent under each accrual, then "
            "with the most accruals paid; no amount depends on it, the rows' share "
            "counts and rates do. To settle it, write each row's "
            "per-share rate into its Description as IB prints it (`USD 0.40 per "
            "Share`): a row that states its rate is read from it, and this is not "
            "said again.\n", err)

    def test_two_dividends_lent_unevenly_or_one_distribution(self):
        # 0.60 on 70 held and 30 lent with 0.30 on 100 held, or one
        # distribution of 0.90 on 80 held and 20 lent: the second is
        # booked (as base read it), and both are said.
        got, err = _facts(_statement(
            _row(ORD, '42.00'), _row(SPECIAL, '30.00'), _row(PIL, '18.00'),
            accrual=_accrual(100, 0.60, 60) + _accrual(100, 0.30, 30, ex=EX_B)))
        self.assertEqual([g[0] for g in got], [80.0, 80.0, 20.0])
        self.assertIn(
            "Booked: lines 7, 8, 9 under the pay date's accruals read as one "
            "distribution of 0.9 USD a share: 80 shares held and 20 lent. Set aside: "
            "lines 7, 9 under the accrual of 100 shares at 0.6 USD, ex date "
            "2026-06-15: 70 shares held and 30 lent; line 8 under the accrual of 100 "
            "shares at 0.3 USD, ex date 2026-06-16: 100 shares held.", err)
        # The fix it names: the rates of the reading set aside, stated.
        got, err = _facts(_statement(
            _row('Cash Dividend USD 0.60 per Share (Ordinary Dividend)', '42.00'),
            _row('Cash Dividend USD 0.30 per Share (Special Dividend)', '30.00'),
            _row('Payment in Lieu of Dividend USD 0.60 per Share (Ordinary Dividend)',
                 '18.00'),
            accrual=_accrual(100, 0.60, 60) + _accrual(100, 0.30, 30, ex=EX_B)))
        self.assertEqual(got, [(70.0, 0.6, EX_A), (100.0, 0.3, EX_B), (30.0, 0.6, EX_A)])
        self.assertNotIn('more than one way', err)

    def test_one_accrual_paying_both_rows_or_an_accrual_each(self):
        # The second accrual on 100 held and 25 lent, or the first all
        # lent and the second all held: the first reading, and both said.
        accrual = _accrual(100, 0.10, 10) + _accrual(100, 0.40, 40, ex=EX_B)
        got, err = _facts(_statement(_row(ORD, '40.00'), _row(PIL, '10.00'),
                                     accrual=accrual))
        self.assertEqual(got, [(100.0, 0.4, EX_B), (25.0, 0.4, EX_B)])
        self.assertIn(
            "Booked: lines 7, 8 under the accrual of 100 shares at 0.4 USD, ex date "
            "2026-06-16: 100 shares held and 25 lent. Set aside: line 8 under the "
            "accrual of 100 shares at 0.1 USD, ex date 2026-06-15: 0 shares held and "
            "100 lent; line 7 under the accrual of 100 shares at 0.4 USD, ex date "
            "2026-06-16: 100 shares held.", err)
        got, err = _facts(_statement(
            _row(ORD_40, '40.00'),
            _row('Payment in Lieu of Dividend USD 0.10 per Share (Ordinary Dividend)',
                 '10.00'), accrual=accrual))
        self.assertEqual(got, [(100.0, 0.4, EX_B), (100.0, 0.1, EX_A)])
        self.assertNotIn('more than one way', err)

    def test_a_reading_that_needs_a_fraction_of_a_share_is_not_said(self):
        for rows, accrual in (
                # (as one distribution of 0.90: 4.44 and 5.56 shares lent)
                ((_row(ORD, '36.00'), _row(SPECIAL, '45.00'), _row(PIL, '4.00'),
                  _row(PIL, '5.00')), TWO),
                # (the first accrual: 75 held, 50 lent of 100; the second 60 and 40)
                ((_row(ORD, '30.00'), _row(PIL, '20.00')), TWO)):
            _, err = _facts(_statement(*rows, accrual=accrual))
            self.assertNotIn('more than one way', err)
        # (the same shares and rates, another ex date: not said either)
        _, err = _facts(_statement(_row(ORD, '10.00'), _row(ROC, '30.00'),
                                   accrual=self.PARTS))
        self.assertNotIn('more than one way', err)

    def test_a_special_dividend_beside_a_distribution_in_parts(self):
        # Three accruals: all the rows as one distribution of 0.90 would
        # be 82.22 shares; the special's 50.00 is the third accrual's.
        got, _ = _facts(_statement(
            _row(ORD, '6.00'), _row(ROC, '18.00'), _row(PIL, '16.00'),
            _row(SPECIAL, '50.00'),
            accrual=self.PARTS + _accrual(100, 0.50, 50, ex='2026-06-17')))
        self.assertEqual(got, [(60.0, 0.1, EX_A), (60.0, 0.3, EX_A), (40.0, 0.4, EX_A),
                               (100.0, 0.5, '2026-06-17')])

    def test_rows_that_are_an_accrual_each_take_their_own_ex_date(self):
        # No shares lent: the parts' rows are an accrual each (base gave
        # both the first ex date; the shares and rates are the same).
        got, _ = _facts(_statement(_row(ORD, '10.00'), _row(ROC, '30.00'),
                                   accrual=self.PARTS))
        self.assertEqual(got, [(100.0, 0.1, EX_A), (100.0, 0.3, EX_B)])

    def test_a_fraction_of_a_share_accrued_is_no_evidence_against_a_way(self):
        got, _ = _facts(_statement(
            _row(ORD, '13.33'), _row(ORD, '16.67'),
            accrual=_accrual(33.333, 0.40, 13.33) + _accrual(33.333, 0.50, 16.67, ex=EX_B)))
        self.assertEqual(got, [(33.325, 0.4, EX_A), (33.34, 0.5, EX_B)])


class TestAReversalAndItsRepost(unittest.TestCase):

    def test_rate_less_rows_fit_either_accrual_and_are_refused(self):
        # 40.00, -40.00 and 40.00 again beside the second dividend's 50.00:
        # the reversed pair nets to nothing under either accrual (at 100
        # shares of the first, or 80 of the second). Base gave the 50.00
        # 125 shares at 0.40. Backlog TJ-5254.
        for third in (_row(ORD, '40.00'), _row(ROC, '40.00')):
            msg = _refused(self, _statement(_row(ORD, '40.00'), _row(ORD, '-40.00'),
                                            third, _row(SPECIAL, '50.00')))
            self.assertIn("ib.csv lines 7, 8, 9, 10 (Dividends)", msg)
            self.assertIn("match them more than one way", msg)

    def test_rows_that_state_their_rates_are_read(self):
        got, err = _facts(_statement(_row(ORD_40, '40.00'), _row(ORD_40, '-40.00'),
                                     _row(ORD_40, '40.00'), _row(SPECIAL_50, '50.00')))
        self.assertEqual(got, [(100.0, 0.4, EX_A), (-100.0, 0.4, EX_A),
                               (100.0, 0.4, EX_A), (100.0, 0.5, EX_B)])
        self.assertNotIn('distributions share the pay date', err)


class TestWhatTheEvidenceIs(unittest.TestCase):

    def test_a_row_that_states_a_rate_is_bound_to_the_accrual_stating_it(self):
        # 40.00 at a stated 0.50 (80 shares paid) and a rate-less 50.00:
        # were the stated rate not binding, the 40.00 would be the first
        # accrual's cash and the 50.00 the second's.
        msg = _refused(self, _statement(_row(SPECIAL_50, '40.00'), _row(ORD, '50.00')))
        self.assertIn("match them no way", msg)

    def test_more_ways_than_are_tried_are_refused_saying_so(self):
        pair = [_row(ORD, '40.00'), _row(ORD, '-40.00')]
        # 13 rate-less rows on two accruals: 8192 ways.
        msg = _refused(self, _statement(*(pair * 6), _row(ORD, '50.00')))
        self.assertIn("the rows' rates, amounts and shares were not weighed (too many "
                      "ways of matching them) — refusing", msg)
        # 12 rows, 4096 ways: weighed.
        msg = _refused(self, _statement(*(pair * 5), _row(ORD, '40.00'),
                                        _row(ORD, '50.00')))
        self.assertIn("match them more than one way", msg)

    def test_two_accounts_accruals_of_one_ex_date_are_two(self):
        # A Dividends section with no Account column: the row may be
        # either account's, and each account's accrual is its own.
        text = (HEAD + TRADES_H
                + _trade('QZRX', '2026-01-05, 09:30:00', 100, 10, -1000, acct=_ACCT)
                + 'Dividends,Header,Currency,Date,Description,Amount\n'
                + f'Dividends,Data,USD,{_DAY},QZRX({US}) {ORD},50.00\n' + _ACC_H
                + _accrual(100, 0.40, 40)
                + _accrual(100, 0.50, 50).replace(_ACCT, 'F1234567'))
        self.assertEqual(_facts(text)[0], [(100.0, 0.5, EX_A)])

    def test_an_accrual_with_no_ex_date_in_another_statement_is_the_dated_one(self):
        with tempfile.TemporaryDirectory() as td:
            paths = [Path(td) / 'a.csv', Path(td) / 'b.csv']
            paths[0].write_text(_statement(accrual=_accrual(100, 0.40, 40, ex='')))
            paths[1].write_text(_statement(_row(ORD, '44.00'),
                                           accrual=_accrual(100, 0.40, 40)))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ib = IbBrokerage()
                ib.account_context = IbBrokerage.prepare_files(paths)
                rows = ib.parse_file(paths[1])
        # (it was read as a second distribution: no ex date, a Warning)
        self.assertEqual([(r['quantity'], r['price'], r.get('ex_date')) for r in rows
                          if r['action'] != 'BUYSELL'], [(110.0, 0.4, EX_A)])
        self.assertNotIn('distributions', err.getvalue())


class TestPayDatesAsNear(unittest.TestCase):
    """Two accruals paid a day before and a day after the row's date are
    as near it: the message says so, not that they share a pay date."""

    NEAR = (_accrual(100, 0.40, 40, pay='2026-06-29')
            + _accrual(100, 0.50, 50, ex=EX_B, pay='2026-07-01'))

    def test_the_row_is_the_accrual_its_cash_is(self):
        self.assertEqual(_facts(_statement(_row(ORD, '50.00'), accrual=self.NEAR))[0],
                         [(100.0, 0.5, EX_B)])

    def test_the_refusal_names_the_pay_dates(self):
        msg = _refused(self, _statement(_row(ORD, '44.00'), accrual=self.NEAR))
        self.assertIn("ib.csv line 7 (Dividends): QZRX.US: 2 distributions are paid as "
                      "near the date of 2026-06-30 (pay dates 2026-06-29, 2026-07-01) "
                      "(Change in Dividend Accruals: 100 shares at 0.4 USD, ex date "
                      "2026-06-15; 100 shares at 0.5 USD, ex date 2026-06-16), nothing "
                      "on a Dividends row", msg)


class TestRowsInAnotherCurrencyThanTheAccruals(unittest.TestCase):
    """A CAD payment of a dividend IB accrued in USD takes the accrual's
    share count alone."""

    def test_the_share_count_both_accruals_state_is_kept_with_no_ex_date(self):
        got, err = _facts(_statement(_row(ORD, '54.00', cur='CAD')))
        self.assertEqual(got, [(100.0, 0.54, None)])
        self.assertIn("no ex date is recorded for it", err)

    def test_share_counts_that_differ_are_refused_in_the_rows_currency(self):
        msg = _refused(self, _statement(
            _row(ORD, '54.00', cur='CAD'),
            accrual=_accrual(100, 0.40, 40) + _accrual(80, 0.50, 40, ex=EX_B)))
        self.assertIn("(Change in Dividend Accruals: 100 shares at 0.4 USD, ex date "
                      "2026-06-15; 80 shares at 0.5 USD, ex date 2026-06-16)", msg)
        self.assertIn("as IB prints it (`CAD 0.10 per Share`)", msg)
        got, _ = _facts(_statement(
            _row('Cash Dividend CAD 0.54 per Share (Ordinary Dividend)', '54.00',
                 cur='CAD'),
            accrual=_accrual(100, 0.40, 40) + _accrual(80, 0.50, 40, ex=EX_B)))
        self.assertEqual(got, [(100.0, 0.54, None)])


class TestNoTie(unittest.TestCase):
    """(controls) One accrual of the pay date, or the next week's."""

    def test_one_accrual_two_rows_of_one_character(self):
        got, err = _facts(_statement(_row(ORD, '40.00'), _row(ORD, '50.00'),
                                     accrual=_accrual(100, 0.40, 40)))
        self.assertEqual(got, [(100.0, 0.4, EX_A), (125.0, 0.4, EX_A)])
        self.assertNotIn('distributions share the pay date', err)

    def test_a_weekly_payers_next_accrual_is_no_tie(self):
        got, _ = _facts(_statement(
            _row(ORD, '44.00'),
            accrual=_accrual(100, 0.40, 40)
            + _accrual(100, 0.50, 50, ex='2026-06-22', pay='2026-07-07')))
        self.assertEqual(got, [(110.0, 0.4, EX_A)])

    def test_an_accrual_in_another_currency_is_not_the_rows(self):
        got, _ = _facts(_statement(
            _row(ORD, '44.00'),
            accrual=_accrual(100, 0.40, 40) + _accrual(100, 0.50, 50, ex=EX_B, cur='CAD')))
        self.assertEqual(got, [(110.0, 0.4, EX_A)])


if __name__ == '__main__':
    unittest.main()
