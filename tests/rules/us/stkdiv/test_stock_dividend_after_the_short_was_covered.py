"""US: a stock dividend posted after the short that owes it was covered
books no shares (backlog TJ-5282).

A short open on the ex-dividend date owes the dividend's shares. Where
it was covered before the pay date the row arrived with nothing short
and was booked as shares held at $0 (the warning read the short sale as
a sale of shares held: "the §307 basis allocation reaches the SOLD
lots"), so a later sale of those ten shares was all gain. The row is the
short's obligation, which its cover did not include: no lot, in the
walk, the wash-sale pre-pass and the result's key every reader of the
rows follows (`stock_dividends_after_cover`): one decision, the
pre-pass's, with shares entitled to the dividend first. What delivering
the shares cost is the row's `delivered_cost`, booked as a cost of that
short's cover; without it an ATTENTION line names the one line that
carries it. With no ex-dividend or record date on the row nothing tells:
it is booked as before, and its warning says a short covered in the 90
days before may owe it.

Synthetic rows only.
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.model.transaction import TaxTransaction
from _support.engine_rules import assert_rule, rules_of
from _support.us_corp_engine import _engine


def _t(i, date, qty, net, account='m', **kw):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                          symbol='SYQ', quantity=qty, currency='USD',
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=net, account=account, id=i, **kw)


def _div(**kw):
    return _t('d', '2025-04-01', 10, 0, type='stock_dividend', **kw)


def _short(sold='2025-03-03', covered='2025-03-25'):
    """100 sold short for 500 and bought back for 440: a gain of 60."""
    return [_t('s', sold, -100, 500), _t('c', covered, 100, -440)]


def _rows(r):
    return [(e['id'], e['qty'], round(e['gain'], 2)) for e in r['transactions']]


def _lots(r):
    return [(lot['qty'], round(lot['basis'], 6), lot['date']) for lot in r['lots_at']]


NO_SHARES = ("warning: ATTENTION: SYQ: stock dividend of 10 share(s) on "
             "2025-04-01: booked as no shares.")


class TestStockDividendAfterTheShortWasCovered(unittest.TestCase):

    @rule(R.US_STKDIV_06)
    def test_the_row_books_no_shares_and_names_the_short_and_the_cover(self):
        r = _engine(_short() + [_div(ex_date='2025-03-20')])
        self.assertEqual(_rows(r), [('c', 100, 60.0)])
        # Base: [(10.0, 0.0, '2025-04-01')], ten shares held at $0.
        self.assertEqual(_lots(r), [])
        self.assertEqual(r['inventory'], [])
        from taxjson.engine.rows import after_cover_record
        assert_rule(self, r, 'd', R.US_STKDIV_06, record=after_cover_record('d'))
        self.assertNotIn('d', rules_of(r, 'd'))     # (not a short that grew)
        self.assertEqual(r['stock_dividends_after_cover'], ['d'])
        self.assertIn(
            NO_SHARES + " The account m was short 100 share(s) on the row's "
            "entitlement date 2025-03-20 and covered them on 2025-03-25, before "
            "the row was posted: the new shares are what that short owed the "
            "lender, which its cover did not include, not shares held at $0 "
            "(US-STKDIV-06, an assumption: check the broker's statement). What "
            "delivering them cost is in no row: put it on the row itself, as "
            "`delivered_cost=COST` on its .tt line (`BUYSELL 2025-04-01 09:30:00 "
            "SYQ 10 USD 0 0 type=stock_dividend ex=2025-03-20 "
            "delivered_cost=COST`, in place of the broker's row): it is booked "
            "as a cost of the cover on 2025-03-25.", r['_stderr'])
        self.assertNotIn('conservation', r['_stderr'])
        self.assertNotIn('$0 purchase', r['_stderr'])
        self.assertNotIn('SOLD lots', r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_a_record_date_alone_is_the_entitlement_date(self):
        # (the assumption US-STKDIV-01 makes of a row with only a record
        # date; a short sold the day before it counts as owing)
        r = _engine(_short() + [_div(record_date='2025-03-20')])
        self.assertEqual(_lots(r), [])
        self.assertIn(NO_SHARES, r['_stderr'])
        self.assertIn("type=stock_dividend record=2025-03-20 delivered_cost=COST",
                      r['_stderr'])
        r = _engine(_short(sold='2025-03-19') + [_div(record_date='2025-03-20')])
        self.assertEqual(_lots(r), [])

    @rule(R.US_STKDIV_06)
    def test_a_cover_on_the_ex_date_owes_and_one_before_it_does_not(self):
        # Bought back on the ex-dividend date, the short was open at its
        # start: it owes. Bought back the day before, it does not, and
        # the row is the $0 purchase with missing history it was.
        on = _engine(_short(covered='2025-03-20') + [_div(ex_date='2025-03-20')])
        self.assertEqual(_lots(on), [])
        before = _engine(_short(covered='2025-03-19') + [_div(ex_date='2025-03-20')])
        self.assertEqual(_lots(before), [(10.0, 0.0, '2025-04-01')])
        self.assertNotIn('booked as no shares', before['_stderr'])
        self.assertIn("booked as a $0 purchase (not a wash-sale replacement); add "
                      "the missing purchase history", before['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_a_short_sold_on_or_after_the_ex_date_does_not_owe(self):
        r = _engine(_short(sold='2025-03-20') + [_div(ex_date='2025-03-20')])
        self.assertEqual(_lots(r), [(10.0, 0.0, '2025-04-01')])
        self.assertNotIn('booked as no shares', r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_the_books_after_it_are_the_books_without_the_row(self):
        # Ten sold on 06-02 open a short, bought back for 55: a gain of
        # 5. Base: the sale is of the ten $0 shares, a gain of 60, and
        # the purchase is ten shares held.
        later = [_t('x', '2025-06-02', -10, 60), _t('b2', '2025-06-10', 10, -55)]
        with_row = _engine(_short() + [_div(ex_date='2025-03-20')] + later)
        without = _engine(_short() + later)
        self.assertEqual(_rows(with_row), [('c', 100, 60.0), ('b2', 10, 5.0)])
        self.assertEqual(_rows(with_row), _rows(without))
        self.assertEqual(_lots(with_row), _lots(without))
        self.assertNotIn('conservation', with_row['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_shares_bought_after_the_cover_do_not_make_the_row_theirs(self):
        # Bought on 03-28, after the ex-dividend date: not entitled. Base:
        # "none of the 5 share(s) held was bought before its entitlement
        # date", and 10 more at $0.
        r = _engine(_short() + [_t('b', '2025-03-28', 5, -25),
                                _div(ex_date='2025-03-20')])
        self.assertEqual(_lots(r), [(5.0, 25.0, '2025-03-28')])
        self.assertIn(NO_SHARES, r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_another_accounts_short_is_not_this_accounts(self):
        r = _engine([_t('s', '2025-03-03', -100, 500, 'L'),
                     _t('c', '2025-03-25', 100, -440, 'L'),
                     _div(ex_date='2025-03-20')])
        self.assertEqual(_lots(r), [(10.0, 0.0, '2025-04-01')])
        self.assertNotIn('booked as no shares', r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_lots_pooled_across_accounts_read_the_pooled_short(self):
        # Without per-account lots the position is one: L's short, sold
        # before the ex date and covered, is the short the row in m owes
        # for, and the pre-pass reads it so too (the books after it are
        # the books without the row).
        import contextlib
        import io
        from taxjson.engine.us.taxrules import USATaxRules

        def pooled(book):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                r = USATaxRules().compute_gains(list(book), per_account_basis=False,
                                                lots_at='2025-12-31')
            return _rows(r), _lots(r), ' '.join(err.getvalue().split())
        short = [_t('s', '2025-03-03', -100, 500, 'L'),
                 _t('c', '2025-03-25', 100, -440, 'L')]
        later = [_t('x', '2025-06-02', -10, 60), _t('b2', '2025-06-10', 10, -55)]
        rows, lots, said = pooled(short + [_div(ex_date='2025-03-20')] + later)
        self.assertEqual(rows, [('c', 100, 60.0), ('b2', 10, 5.0)])
        self.assertEqual((rows, lots), pooled(short + later)[:2])
        self.assertIn(NO_SHARES + " The accounts pooled here were short 100 "
                      "share(s)", said)
        self.assertNotIn('conservation', said)

    @rule(R.US_STKDIV_06)
    def test_a_short_still_open_grows_as_before(self):
        # (a control: 40 of the 100 still short at the pay date)
        r = _engine([_t('s', '2025-03-03', -100, 500),
                     _t('c', '2025-03-25', 60, -264),
                     _div(ex_date='2025-03-20')])
        self.assertEqual(_lots(r), [(-50.0, -200.0, '2025-03-03')])
        assert_rule(self, r, 'd', R.US_STKDIV_06, record='d')
        self.assertNotIn('booked as no shares', r['_stderr'])

    @rule(R.US_STKDIV_01)
    def test_shares_held_take_the_dividend_as_before(self):
        # (a control: the lot bought before the ex-dividend date)
        r = _engine([_t('b', '2025-01-06', 100, -500), _div(ex_date='2025-03-20')])
        self.assertEqual([(round(q, 6), b, d) for q, b, d in _lots(r)],
                         [(110.0, 500.0, '2025-01-06')])
        self.assertNotIn('ATTENTION', r['_stderr'])


class TestTheDeliveredCost(unittest.TestCase):
    """The row's `delivered_cost`: a cost of closing the short, booked on
    the cover. The two-line hand entry it replaces sold long shares at
    no proceeds wherever the account was long by the end of the cover's
    day."""

    def _d(self, cost=45.0):
        return _div(ex_date='2025-03-20', delivered_cost=cost)

    @rule(R.US_STKDIV_06)
    def test_flat_after_the_cover_the_gain_falls_by_the_cost(self):
        r = _engine(_short() + [self._d()])
        self.assertEqual(_rows(r), [('c', 100, 15.0)])      # 60 - 45
        self.assertEqual(r['transactions'][0]['stock_dividend_delivery'], 45.0)
        self.assertEqual(r['transactions'][0]['date'], '2025-03-25')
        self.assertEqual(_lots(r), [])
        # A note, no ATTENTION: the entry is made.
        self.assertIn("note: SYQ: stock dividend of 10 share(s) on 2025-04-01: "
                      "booked as no shares.", r['_stderr'])
        self.assertIn("Its delivered_cost of 45.00 is booked as a cost of the cover "
                      "on 2025-03-25.", r['_stderr'])
        self.assertNotIn('ATTENTION', r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_a_flip_buy_keeps_the_cost_off_the_shares_it_opens(self):
        # One buy of 150 covers the 100 and goes long 50 at 4.40.
        r = _engine([_t('s', '2025-03-03', -100, 500),
                     _t('c', '2025-03-25', 150, -660), self._d()])
        self.assertEqual(_rows(r), [('c', 100, 15.0)])
        self.assertEqual(_lots(r), [(50.0, 220.0, '2025-03-25')])
        self.assertNotIn('ATTENTION', r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_a_later_buy_that_day_is_not_touched(self):
        r = _engine(_short() + [_t('b', '2025-03-25', 50, -200, time='14:00:00'),
                                self._d()])
        self.assertEqual(_rows(r), [('c', 100, 15.0)])
        self.assertEqual(_lots(r), [(50.0, 200.0, '2025-03-25')])

    @rule(R.US_STKDIV_06)
    def test_two_short_lots_share_it_by_their_units(self):
        r = _engine([_t('s1', '2025-03-03', -60, 300), _t('s2', '2025-03-10', -40, 200),
                     _t('c', '2025-03-25', 100, -440), self._d()])
        self.assertEqual([(e['qty'], round(e['gain'], 2),
                           e['stock_dividend_delivery']) for e in r['transactions']],
                         [(60, 9.0, 27.0), (40, 6.0, 18.0)])

    @rule(R.US_STKDIV_06)
    def test_the_cover_with_it_is_what_the_wash_sale_rule_reads(self):
        # The cost (80) makes the close a loss of 20. A cover's loss is
        # disallowed where the stock is sold short again within 30 days
        # (§1091(e)), and this one is, as the same loss without the row
        # is (a cover at 520); a purchase within 30 days is no such
        # replacement of a short, with the row or without.
        again = [_t('s2', '2025-04-10', -100, 500)]
        buy = [_t('b2', '2025-04-10', 100, -500)]
        plain = [_t('s', '2025-03-03', -100, 500), _t('c', '2025-03-25', 100, -520)]

        def figures(book):
            r = _engine(book)
            return [(e['id'], e['qty'], round(e['gain'], 2),
                     round(e.get('disallowed_amount') or 0.0, 2))
                    for e in r['transactions']], _lots(r)
        self.assertEqual(figures(_short() + [self._d(80.0)] + again),
                         ([('c', 100, 0.0, 20.0)], [(-100.0, -480.0, '2025-04-10')]))
        self.assertEqual(figures(_short() + [self._d(80.0)] + again),
                         figures(plain + again))
        self.assertEqual(figures(_short() + [self._d(80.0)] + buy),
                         ([('c', 100, -20.0, 0.0)], [(100.0, 500.0, '2025-04-10')]))
        self.assertEqual(figures(_short() + [self._d(80.0)] + buy),
                         figures(plain + buy))

    @rule(R.US_STKDIV_06)
    def test_a_cover_in_an_earlier_tax_year_is_an_attention_naming_both_years(self):
        r = _engine([_t('s', '2024-12-02', -100, 500), _t('c', '2024-12-20', 100, -440),
                     _t('d', '2025-01-06', 10, 0, type='stock_dividend',
                        ex_date='2024-12-18', delivered_cost=45.0)])
        self.assertEqual(_rows(r), [('c', 100, 15.0)])
        self.assertIn("warning: ATTENTION: SYQ: stock dividend of 10 share(s) on "
                      "2025-01-06: booked as no shares.", r['_stderr'])
        self.assertIn("Its delivered_cost of 45.00 is booked as a cost of the cover "
                      "on 2024-12-20, in tax year 2024: the row is dated 2025, and "
                      "2024's gain is lower by it than without the row. If 2024 is "
                      "filed, `tjs check-filed` shows the difference.", r['_stderr'])
        self.assertNotIn('note: SYQ', r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_a_cost_on_a_row_that_is_no_covered_shorts_is_said_not_booked(self):
        # No date on the row: nothing tells, so nothing is booked of it.
        r = _engine(_short() + [_div(delivered_cost=45.0)])
        self.assertEqual(_rows(r), [('c', 100, 60.0)])
        self.assertIn("warning: ATTENTION: SYQ: stock dividend of 10 share(s) on "
                      "2025-04-01: its delivered_cost of 45.00 is not booked.",
                      r['_stderr'])
        # A JSON book's cost below 0 (the .tt reader refuses one) is never
        # booked: the cover's gain stays 60, and the run says so.
        r = _engine(_short() + [self._d(-45.0)])
        self.assertEqual(_rows(r), [('c', 100, 60.0)])
        self.assertIn("its delivered_cost of -45.00 is below 0 and is not booked",
                      r['_stderr'])
        # Shares held take the dividend: the cost is no part of it.
        r = _engine([_t('b', '2025-01-06', 100, -500),
                     _div(ex_date='2025-03-20', delivered_cost=45.0)])
        self.assertIn("its delivered_cost of 45.00 is not booked", r['_stderr'])


class TestOneDecisionForThePrePassAndTheWalk(unittest.TestCase):
    """Shares entitled to the dividend come first, in the pre-pass's
    decision, which the walk follows. A second test in the walk booked
    the row on a lot the pre-pass did not see: the pre-pass left the ten
    units out, and a loss disallowed in full (450) was allowed in part
    with no word."""

    LATER = [('x', '2025-06-02', -110, 550), ('rb', '2025-06-10', 110, -500)]

    def _figures(self, book):
        r = _engine(book)
        return ([(e['id'], round(e['qty'], 4), round(e['gain'], 2),
                  round(e.get('disallowed_amount') or 0.0, 2))
                 for e in r['transactions']],
                [(round(q, 4), b, d) for q, b, d in _lots(r)],
                r.get('stock_dividends_after_cover'), r['_stderr'])

    @rule(R.US_STKDIV_06)
    def test_an_opening_lot_dated_after_the_cover_is_entitled(self):
        from taxjson.lib.opening import opening_row
        op = TaxTransaction(**opening_row(
            symbol='SYQ', quantity=100.0, total_cost=1000.0, day='2025-03-28',
            currency='USD', account='m', lot_date='2024-01-08'))
        later = [_t(*r) for r in self.LATER]
        rows, lots, key, said = self._figures(
            _short() + [op, _div(ex_date='2025-03-20')] + later)
        # The lot bought in 2024 takes the dividend; the 110 sold at a
        # loss of 450 and bought back are a wash sale in full.
        self.assertEqual(rows, [('c', 100, 60.0, 0.0), ('x', 110.0, -0.0, 450.0)])
        self.assertEqual(lots, [(110.0, 950.0, '2025-06-10')])
        self.assertIsNone(key)
        self.assertNotIn('booked as no shares', said)
        self.assertNotIn('conservation', said)
        # ... as in the book with no short at all.
        control = self._figures([op, _div(ex_date='2025-03-20')] + later)
        self.assertEqual((rows[1:], lots), control[:2])

    @rule(R.US_STKDIV_06)
    def test_a_lot_moved_in_after_the_cover_is_entitled(self):
        def move(i, qty, account):
            return TaxTransaction(
                action='TRANSFER', type='lot_move', date='2025-03-28',
                date_settle='2025-03-28', time='00:00:00', symbol='SYQ',
                quantity=qty, currency='USD', price=0.0, net_amount=0.0,
                account=account, id=i, description=(
                    'own-account move #1: L -> m (basis and purchase dates '
                    'carried)'))
        rest = ([_t('bl', '2024-01-08', 100, -1000, 'L'), move('mo', -100, 'L'),
                 move('mi', 100, 'm'), _div(ex_date='2025-03-20')]
                + [_t(*r) for r in self.LATER])
        book = sorted(_short() + rest, key=lambda r: (r.date, r.time))
        rows, lots, key, said = self._figures(book)
        self.assertEqual(rows, [('c', 100, 60.0, 0.0), ('x', 110.0, -0.0, 450.0)])
        self.assertEqual(lots, [(110.0, 950.0, '2025-06-10')])
        self.assertIsNone(key)
        self.assertNotIn('conservation', said)
        self.assertEqual((rows[1:], lots), self._figures(rest)[:2])

    @rule(R.US_STKDIV_06)
    def test_an_opening_lot_bought_after_the_ex_date_is_not(self):
        from taxjson.lib.opening import opening_row
        op = TaxTransaction(**opening_row(
            symbol='SYQ', quantity=100.0, total_cost=1000.0, day='2025-03-28',
            currency='USD', account='m', lot_date='2025-03-26'))
        rows, lots, key, said = self._figures(
            _short() + [op, _div(ex_date='2025-03-20')])
        self.assertEqual(key, ['d'])
        self.assertEqual(lots, [(100.0, 1000.0, '2025-03-26')])
        self.assertIn(NO_SHARES, said)

    @rule(R.US_STKDIV_06)
    def test_the_walk_follows_the_pre_pass(self):
        # The decision is lib/stock_dividend_cover's, from the rows alone,
        # read once before the pre-pass (the run's `stkdiv_after_cover`):
        # the result's key is its rows, the rule table names each (never
        # one without the other), and the lib function gives the same rows.
        from taxjson.lib.stock_dividend_cover import no_share_rows
        for book in (_short() + [_div(ex_date='2025-03-20')],
                     _short() + [_t('b', '2025-03-28', 5, -25),
                                 _div(ex_date='2025-03-20')],
                     _short(covered='2025-03-19') + [_div(ex_date='2025-03-20')],
                     [_t('b', '2025-01-06', 100, -500), _div(ex_date='2025-03-20')]):
            r = _engine(book)
            from taxjson.engine.rows import after_cover_record
            named = after_cover_record('d') in rules_of(r, 'd')
            self.assertEqual(named, r.get('stock_dividends_after_cover') == ['d'])
            self.assertEqual(sorted(no_share_rows(book, entitled_lots=True)[0]),
                             r.get('stock_dividends_after_cover') or [])
            self.assertNotIn('conservation', r['_stderr'])


class TestARowWithNoEntitlementDate(unittest.TestCase):
    """Nothing tells whether a short owed it: booked as before."""

    @rule(R.US_STKDIV_03)
    def test_it_stays_a_zero_cost_purchase_and_the_warning_names_the_cover(self):
        r = _engine(_short() + [_div()])
        self.assertEqual(_lots(r), [(10.0, 0.0, '2025-04-01')])
        # Base: "... the shares were disposed of before the pay date, so
        # the §307 basis allocation reaches the SOLD lots", of a short.
        self.assertIn(
            "warning: SYQ: stock dividend of 10 share(s) on 2025-04-01 with no "
            "shares held — booked as a $0 purchase (not a wash-sale "
            "replacement); add the missing purchase history so it can share "
            "their basis (§307). ATTENTION: m covered a short of SYQ on "
            "2025-03-25, within the 90 days before the row, which carries no "
            "ex-dividend or record date: a short open on the ex-dividend date "
            "owes the new shares, and the row is then that short's obligation, "
            "not shares held at $0 (US-STKDIV-06).", r['_stderr'])
        self.assertNotIn('SOLD lots', r['_stderr'])

    @rule(R.US_STKDIV_03)
    def test_a_cover_more_than_90_days_before_is_not_named(self):
        r = _engine(_short(sold='2024-11-04', covered='2024-12-02') + [_div()])
        self.assertEqual(_lots(r), [(10.0, 0.0, '2025-04-01')])
        self.assertNotIn('covered a short', r['_stderr'])

    @rule(R.US_STKDIV_03)
    def test_shares_sold_out_before_the_pay_date_are_said_as_before(self):
        # (a control: a sale of shares held, where the SOLD lots do
        # carry the §307 share)
        r = _engine([_t('b', '2025-01-06', 100, -500),
                     _t('x', '2025-03-25', -100, 600), _div()])
        self.assertIn("the shares were disposed of before the pay date, so the "
                      "§307 basis allocation reaches the SOLD lots",
                      r['_stderr'])
        self.assertNotIn('covered a short', r['_stderr'])


class TestTheRun(unittest.TestCase):
    """`tjs run` over a US .tt book: the lots are the account's own, so
    its holdings were the engine's already; the gains files name the row
    and the one line books the cost."""

    @rule(R.US_STKDIV_06)
    def test_a_flip_buy_holds_its_own_fifty_and_the_one_line_books_the_cost(self):
        import json
        import tempfile
        from pathlib import Path
        from _qa_project import console, flat, tj, toml
        lines = ["BUYSELL 2025-03-03 10:00:00 SYQ.US -100 USD 5 500 0",
                 "BUYSELL 2025-03-25 10:00:00 SYQ.US 150 USD 4.4 660 0",
                 "BUYSELL 2025-04-01 10:00:00 SYQ.US 10 USD 0 0 type=stock_dividend "
                 "ex=2025-03-20"]
        for token, gain in (("", "60.00"), (" delivered_cost=45", "15.00")):
            with self.subTest(token=token), tempfile.TemporaryDirectory() as d:
                root = Path(d) / "p"
                (root / "inputs/margin").mkdir(parents=True)
                (root / "taxjson.toml").write_text(toml(country="usa", year=2025))
                (root / "ticker.map").write_text("")
                (root / "inputs/margin/book.tt").write_text(
                    "\n".join(lines[:2] + [lines[2] + token]) + "\n")
                run = tj(root, "run", "--no-input", "--details", check=False)
                self.assertEqual(run.returncode, 0, console(run))
                self.assertIn("margin SYQ.US 50 220.00 4.40",
                              flat(tj(root, "list", check=False).stdout))
                self.assertIn(f"margin {gain} 0.00 {gain}",
                              flat(tj(root, "sum", check=False).stdout))
                keys = [json.loads(p.read_text()).get('stock_dividends_after_cover')
                        for p in sorted((root / "work").glob("margin*_gains*.json"))]
                self.assertTrue(keys and all(k and len(k) == 1 for k in keys), keys)
                self.assertEqual("What delivering them cost is in no row"
                                 in flat(console(run)), not token)


if __name__ == "__main__":
    unittest.main()
