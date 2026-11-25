"""Canada: a stock dividend posted after the short that owes it was
covered books no shares (backlog TJ-5282).

A short open on the ex-dividend date owes the dividend's shares. Where
the account bought the short back before the pay date the row arrived
with nothing short and was booked as ten shares acquired at $0: shares
held that were never received, a purchase in a loss's 30 days, and a row
the run asked a declared amount for. The row is the short's obligation,
which its cover did not include: it is left out of every pass, and the
gains files name it (`stock_dividends_after_cover`) so every reader of
the rows leaves it out too (`tjs list`, the run's "$0 cost" line, `tjs
find-missing-history`). What delivering the shares cost is the row's
`delivered_cost`, booked as a cost of that short's cover; without it an
ATTENTION line names the one line that carries it. With no ex-dividend
or record date on the row nothing tells: it is booked as before, and an
ATTENTION line says a short covered in the 90 days before may owe it.

Synthetic rows only.
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.model.transaction import TaxTransaction
from _support.ca_engine_runs import _run


def _t(i, date, qty, net, account='m', **kw):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                          symbol='SYQ', quantity=qty, currency='CAD',
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=net, account=account, id=i, **kw)


def _div(**kw):
    return _t('d', '2025-04-01', 10, 0, type='stock_dividend', **kw)


def _short(sold='2025-03-03', covered='2025-03-25', account='m'):
    """100 sold short for 500 and bought back for 440: a gain of 60."""
    return [_t('s', sold, -100, 500, account), _t('c', covered, 100, -440, account)]


def _figures(book):
    """[(row, units, gain, denied)], the units held, the rows the engine
    names as booked as no shares, what the run said."""
    res, err = _run(book)
    return ([(e['id'], e['qty'], round(e['gain'], 2),
              round(e.get('disallowed_amount') or 0.0, 2))
             for e in res['transactions']],
            [(i['qty'], round(i['total_cost'], 6)) for i in res['inventory']],
            res.get('stock_dividends_after_cover'), ' '.join(err.split()))


NO_SHARES = ("warning: ATTENTION: SYQ: stock dividend of 10 share(s) on "
             "2025-04-01: booked as no shares.")


class TestStockDividendAfterTheShortWasCovered(unittest.TestCase):

    @rule(R.CA_STKDIV_02)
    def test_the_row_books_no_shares_and_names_the_short_and_the_cover(self):
        rows, held, owed, said = _figures(_short() + [_div(ex_date='2025-03-20')])
        self.assertEqual(rows, [('c', 100, 60.0, 0.0)])
        # Base: [(10.0, 0.0)], ten shares acquired at $0, and no word.
        self.assertEqual(held, [])
        self.assertEqual(owed, ['d'])       # (the gains files' key)
        self.assertIsNone(_run(_short() + [_div(ex_date='2025-03-20')])[0]
                          .get('stock_dividends_owed'))    # (no short grew)
        self.assertIn(
            NO_SHARES + " The account m was short 100 share(s) on the row's "
            "entitlement date 2025-03-20 and covered them on 2025-03-25, before "
            "the row was posted: the new shares are what that short owed the "
            "lender, which its cover did not include, not shares acquired at $0 "
            "(CA-STKDIV-02, an assumption: check the broker's statement). It "
            "needs no declared amount. What delivering them cost is in no row: "
            "put it on the row itself, as `delivered_cost=COST` on its .tt line "
            "(`BUYSELL 2025-04-01 09:30:00 SYQ 10 CAD 0 0 type=stock_dividend "
            "ex=2025-03-20 delivered_cost=COST`, in place of the broker's row): "
            "the account's own books then book it as a cost of the cover on "
            "2025-03-25.", said)
        self.assertEqual(said.count('booked as no shares'), 1)
        self.assertNotIn('conservation', said)

    @rule(R.CA_STKDIV_02)
    def test_a_record_date_alone_is_the_entitlement_date(self):
        _, held, owed, said = _figures(_short() + [_div(record_date='2025-03-20')])
        self.assertEqual((held, owed), ([], ['d']))
        self.assertIn(NO_SHARES, said)

    @rule(R.CA_STKDIV_02)
    def test_a_cover_on_the_ex_date_owes_and_one_before_it_does_not(self):
        _, held, owed, _ = _figures(_short(covered='2025-03-20')
                                    + [_div(ex_date='2025-03-20')])
        self.assertEqual((held, owed), ([], ['d']))
        _, held, owed, said = _figures(_short(covered='2025-03-19')
                                       + [_div(ex_date='2025-03-20')])
        self.assertEqual((held, owed), ([(10.0, 0.0)], None))
        self.assertEqual(said, '')

    @rule(R.CA_STKDIV_02)
    def test_a_short_sold_on_or_after_the_ex_date_does_not_owe(self):
        _, held, owed, said = _figures(_short(sold='2025-03-20')
                                       + [_div(ex_date='2025-03-20')])
        self.assertEqual((held, owed), ([(10.0, 0.0)], None))
        self.assertNotIn('booked as no shares', said)

    @rule(R.CA_STKDIV_02)
    def test_the_books_after_it_are_the_books_without_the_row(self):
        # Ten sold on 06-02 open a short, bought back for 55: a gain of
        # 5. Base: the sale is of the ten $0 shares, a gain of 60, and
        # the purchase is ten shares held.
        later = [_t('x', '2025-06-02', -10, 60), _t('b2', '2025-06-10', 10, -55)]
        rows, held, _, said = _figures(_short() + [_div(ex_date='2025-03-20')] + later)
        self.assertEqual(rows, [('c', 100, 60.0, 0.0), ('b2', 10, 5.0, 0.0)])
        without = _figures(_short() + later)
        self.assertEqual((rows, held), without[:2])
        self.assertNotIn('conservation', said)

    @rule(R.CA_STKDIV_02)
    def test_shares_held_at_the_end_are_the_purchases_own(self):
        # One buy of 150 covers the 100 and goes long 50 at 4.40: the
        # pool is those 50 at 220, with the row or without.
        book = [_t('s', '2025-03-03', -100, 500), _t('c', '2025-03-25', 150, -660)]
        rows, held, owed, said = _figures(book + [_div(ex_date='2025-03-20')])
        self.assertEqual((held, owed), ([(50.0, 220.0)], ['d']))
        self.assertEqual((rows, held), _figures(book)[:2])
        self.assertIn(NO_SHARES, said)

    @rule(R.CA_STKDIV_02)
    def test_the_row_is_no_purchase_in_a_losss_30_days(self):
        # 200 sold on 03-03: the 100 held go at a loss of 500 and 100 are
        # sold short, bought back on 03-25. Base: the ten shares the row
        # "acquired" on 04-01, held 30 days after the loss, deny a part
        # of it.
        book = [_t('b', '2025-01-06', 100, -1000), _t('s', '2025-03-03', -200, 1000),
                _t('c', '2025-03-25', 100, -440), _div(ex_date='2025-03-20')]
        rows, held, owed, _ = _figures(book)
        self.assertEqual(rows, [('s', 100, -500.0, 0.0), ('c', 100, 60.0, 0.0)])
        self.assertEqual((held, owed), ([], ['d']))
        self.assertEqual((rows, held), _figures(book[:-1])[:2])

    @rule(R.CA_STKDIV_02)
    def test_the_receiving_accounts_own_position_decides(self):
        # L holds 100; m, short on the ex date by its own books and
        # covered, receives the row: no shares, in the books pooled
        # across the two as in m's own (the pooled books are the ones
        # without the row).
        pooled = [_t('bl', '2025-01-06', 100, -1000, 'L')] + _short()
        rows, held, owed, said = _figures(pooled + [_div(ex_date='2025-03-20')])
        self.assertEqual(owed, ['d'])
        self.assertEqual((rows, held), _figures(pooled)[:2])
        # (these books pool L's shares: the line says what they do not book)
        self.assertIn("warning: ATTENTION: pooled short: SYQ: stock dividend of 10 "
                      "share(s) on 2025-04-01: booked as no shares. The account m "
                      "was short 100 share(s)", said)
        self.assertIn("What delivering them cost, once on the row as "
                      "delivered_cost=, is booked in the account's own books and not "
                      "in the books pooled across your accounts, the figures you "
                      "file: the purchase on 2025-03-25 closes no short in them",
                      said)
        # Another account's covered short never makes a holder's receipt
        # the short's: L receives the row on its own shares.
        rows, held, owed, said = _figures(pooled + [
            _t('d', '2025-04-01', 10, 0, 'L', type='stock_dividend',
               ex_date='2025-03-20')])
        self.assertEqual(owed, None)
        self.assertEqual([q for q, _ in held], [110.0])
        self.assertNotIn('booked as no shares', said)

    @rule(R.CA_STKDIV_02)
    def test_a_short_still_open_grows_as_before(self):
        # (a control: 40 of the 100 still short at the pay date)
        _, held, owed, said = _figures([_t('s', '2025-03-03', -100, 500),
                                        _t('c', '2025-03-25', 60, -264),
                                        _div(ex_date='2025-03-20')])
        self.assertIsNone(owed)
        self.assertEqual([q for q, _ in held], [-50.0])
        self.assertIn('while m is short the security', said)
        self.assertNotIn('booked as no shares', said)

    @rule(R.CA_STKDIV_01)
    def test_shares_held_take_the_dividend_as_before(self):
        # (a control)
        _, held, owed, said = _figures([_t('b', '2025-01-06', 100, -500),
                                        _div(ex_date='2025-03-20')])
        self.assertEqual((held, owed, said), ([(110.0, 500.0)], None, ''))


class TestTheDeliveredCost(unittest.TestCase):
    """The row's `delivered_cost`: a cost of closing the short, booked on
    the cover. The two-line hand entry it replaces sold pool shares at
    no proceeds wherever the account was long by the end of the cover's
    day (a loss of their cost, denied as superficial)."""

    def _d(self, cost=45.0):
        return _div(ex_date='2025-03-20', delivered_cost=cost)

    @rule(R.CA_STKDIV_02)
    def test_flat_after_the_cover_the_gain_falls_by_the_cost(self):
        rows, held, owed, said = _figures(_short() + [self._d()])
        self.assertEqual(rows, [('c', 100, 15.0, 0.0)])     # 60 - 45
        self.assertEqual((held, owed), ([], ['d']))
        self.assertIn("note: SYQ: stock dividend of 10 share(s) on 2025-04-01: "
                      "booked as no shares.", said)
        self.assertIn("Its delivered_cost of 45.00 is booked as a cost of the cover "
                      "on 2025-03-25.", said)
        self.assertNotIn('ATTENTION', said)
        res, _ = _run(_short() + [self._d()])
        self.assertEqual(res['transactions'][0]['date'], '2025-03-25')

    @rule(R.CA_STKDIV_02)
    def test_a_flip_buy_keeps_the_cost_off_the_shares_it_opens(self):
        rows, held, _, said = _figures([_t('s', '2025-03-03', -100, 500),
                                        _t('c', '2025-03-25', 150, -660), self._d()])
        self.assertEqual(rows, [('c', 100, 15.0, 0.0)])
        self.assertEqual(held, [(50.0, 220.0)])
        self.assertNotIn('ATTENTION', said)

    @rule(R.CA_STKDIV_02)
    def test_a_later_buy_that_day_is_not_touched(self):
        rows, held, _, _ = _figures(
            _short() + [_t('b', '2025-03-25', 50, -200, time='14:00:00'), self._d()])
        self.assertEqual(rows, [('c', 100, 15.0, 0.0)])
        self.assertEqual(held, [(50.0, 200.0)])

    @rule(R.CA_STKDIV_02)
    def test_the_cover_with_it_is_what_the_superficial_loss_rule_reads(self):
        # The cost (80) makes the close a loss of 20. A cover's loss is
        # denied where the account buys the shares within 30 days and
        # holds them 30 days on, and this one is, as the same loss
        # without the row is (a cover at 520): the 20 joins the shares'
        # cost. Sold short again instead, nothing is held: allowed.
        buy = [_t('b2', '2025-04-10', 100, -500)]
        again = [_t('s2', '2025-04-10', -100, 500)]
        plain = [_t('s', '2025-03-03', -100, 500), _t('c', '2025-03-25', 100, -520)]
        with_row = _short() + [self._d(80.0)]
        self.assertEqual(_figures(with_row + buy)[:2],
                         ([('c', 100, 0.0, 20.0)], [(100.0, 520.0)]))
        self.assertEqual(_figures(with_row + buy)[:2], _figures(plain + buy)[:2])
        self.assertEqual(_figures(with_row + again)[:2],
                         ([('c', 100, -20.0, 0.0)], [(-100.0, -500.0)]))
        self.assertEqual(_figures(with_row + again)[:2], _figures(plain + again)[:2])

    @rule(R.CA_STKDIV_02)
    def test_a_cost_on_a_row_that_is_no_covered_shorts_is_said_not_booked(self):
        rows, held, owed, said = _figures(_short() + [_div(delivered_cost=45.0)])
        self.assertEqual((rows, held, owed),
                         ([('c', 100, 60.0, 0.0)], [(10.0, 0.0)], None))
        self.assertIn("warning: ATTENTION: SYQ: stock dividend of 10 share(s) on "
                      "2025-04-01: its delivered_cost of 45.00 is not booked.", said)
        # A JSON book's cost below 0 is never booked: the gain stays 60.
        rows, _, owed, said = _figures(_short() + [self._d(-45.0)])
        self.assertEqual((rows, owed), ([('c', 100, 60.0, 0.0)], ['d']))
        self.assertIn("its delivered_cost of -45.00 is below 0 and is not booked",
                      said)
        self.assertNotIn("is booked as a cost of the cover", said)

    @rule(R.CA_STKDIV_02)
    def test_books_pooled_with_a_holder_say_the_cost_is_not_booked(self):
        # L holds 100: pooled, m's short sale sold pool shares and its
        # purchase covers nothing, so the cost has no cover to join. Said.
        book = ([_t('bl', '2025-01-06', 100, -1000, 'L')] + _short() + [self._d()])
        rows, held, owed, said = _figures(book)
        self.assertEqual(owed, ['d'])
        self.assertEqual((rows, held), _figures(book[:-1])[:2])
        self.assertIn("warning: ATTENTION: pooled short: SYQ: stock dividend", said)
        self.assertIn("Its delivered_cost of 45.00 is not booked in the books pooled "
                      "across your accounts, the figures you file: the purchase on "
                      "2025-03-25 closes no short in them (another account holds the "
                      "shares, so the short sale sold pool shares).", said)
        # m's own books book it: 60 - 45.
        own, _, _, own_said = _figures(_short() + [self._d()])
        self.assertEqual(own, [('c', 100, 15.0, 0.0)])
        self.assertNotIn('ATTENTION', own_said)

    @rule(R.CA_STKDIV_02)
    def test_a_cover_in_an_earlier_tax_year_is_an_attention_naming_both_years(self):
        # The short was covered in December 2024 and the row posted in
        # January 2025: its cost lowers the 2024 gain (60 to 15). Never a
        # quiet note: both years and `tjs check-filed` are named.
        book = [_t('s', '2024-12-02', -100, 500), _t('c', '2024-12-20', 100, -440),
                _t('d', '2025-01-06', 10, 0, type='stock_dividend',
                   ex_date='2024-12-18', delivered_cost=45.0)]
        rows, _, _, said = _figures(book)
        self.assertEqual(rows, [('c', 100, 15.0, 0.0)])
        self.assertIn("warning: ATTENTION: SYQ: stock dividend of 10 share(s) on "
                      "2025-01-06: booked as no shares.", said)
        self.assertIn("Its delivered_cost of 45.00 is booked as a cost of the cover "
                      "on 2024-12-20, in tax year 2024: the row is dated 2025, and "
                      "2024's gain is lower by it than without the row. If 2024 is "
                      "filed, `tjs check-filed` shows the difference.", said)
        self.assertNotIn('note: SYQ', said)


class TestAMarkedShortBesideSharesHeld(unittest.TestCase):
    """The walk keeps a broker-marked short in a pool of its own while the
    account is long (CA-DISP-04; RBC's "SHORT." / "COVER SHORT." words, an
    IB row's O / C codes). "Short on the entitlement date" is that pool,
    never the account's net position: the decision reads the account
    through the walk's own router (engine/ca/router `MarkedPositions`),
    and the engine's figures and the decision are checked against each
    other in every book."""

    @staticmethod
    def _rbc(i, date, qty, net, words='', **kw):
        return _t(i, date, qty, net, description=words, short_marks='description',
                  **kw)

    @staticmethod
    def _ib(i, date, qty, net, code='', **kw):
        return _t(i, date, qty, net, open_close=code, **kw)

    def _both(self, book):
        """(units held and short, the engine's no-share rows, the rows that
        grew a short, what was said), the lib decision checked equal."""
        from taxjson.lib.stock_dividend_cover import no_share_rows_of
        res, err = _run(book)
        gone, near = no_share_rows_of(book, 'canada')
        self.assertEqual(sorted(gone), res.get('stock_dividends_after_cover') or [])
        return (sorted(round(i['qty'], 6) for i in res['inventory']),
                res.get('stock_dividends_after_cover'),
                res.get('stock_dividends_owed'), sorted(near), ' '.join(err.split()))

    def _books(self, row):
        """The four shapes around a marked short of 50 open across the ex
        date, in a broker's own marking (`row`: its maker; the marks of a
        short sale, its cover, a plain purchase)."""
        if row == self._rbc:
            short, cover, plain = 'SHORT. SOLD', 'COVER SHORT. BOUGHT', 'BOUGHT'
        else:
            short, cover, plain = 'O', 'C', 'O'
        div = row('d', '2025-04-01', 10, 0, type='stock_dividend',
                  ex_date='2025-03-20')
        sold = row('s', '2025-03-03', -50, 250, short)
        covered = row('c', '2025-03-25', 50, -220, cover)
        long = row('b', '2025-01-06', 100, -1000, plain)
        return {'long and covered': [long, sold, covered, div],
                'no long, covered': [sold, covered, div],
                'long, short still open': [long, sold, div],
                'long bought after the ex date, covered':
                    [sold, row('b', '2025-03-22', 100, -1000, plain), covered, div]}

    @rule(R.CA_STKDIV_02)
    def test_an_rbc_account_long_100_and_marked_short_50_across_the_ex_date(self):
        books = self._books(self._rbc)
        # The long receives the shares; the covered short's part is in no
        # row, and the line says so (the net position, long 50, said
        # nothing of the short).
        held, gone, owed, near, said = self._both(books['long and covered'])
        self.assertEqual((held, gone, owed, near), ([110.0], None, None, ['d']))
        self.assertIn("warning: ATTENTION: SYQ: stock dividend of 10 share(s) on "
                      "2025-04-01: m held 100 share(s) on its entitlement date "
                      "2025-03-20 and was also short 50 of the security (a sale the "
                      "broker marked short), covered on 2025-03-25. The row is booked "
                      "as shares received on the shares held; the part that short "
                      "owed the lender is not in it: enter that by hand (.tt), "
                      "CA-STKDIV-02.", said)
        # With no shares held the short owes it: no shares.
        held, gone, owed, near, _ = self._both(books['no long, covered'])
        self.assertEqual((held, gone, owed, near), ([], ['d'], None, []))
        # The short still open beside the shares: the engine's own line.
        held, gone, owed, near, said = self._both(books['long, short still open'])
        self.assertEqual((held, gone, owed, near), ([-50.0, 110.0], None, None, []))
        self.assertIn("holds the security and is also short 50 of it", said)
        # Shares bought after the ex date are not entitled: the short's.
        held, gone, owed, near, _ = self._both(
            books['long bought after the ex date, covered'])
        self.assertEqual((held, gone, owed, near), ([100.0], ['d'], None, []))

    @rule(R.CA_STKDIV_02)
    def test_an_rbc_purchase_with_no_cover_words_leaves_the_short_open(self):
        # RBC marks its covers: an unmarked purchase is a long purchase, so
        # the marked short is still open at the row (the engine's reading).
        # By the net position the account was flat and the row was dropped.
        from taxjson.lib.stock_dividend_cover import no_share_rows
        book = [self._rbc('s', '2025-03-03', -50, 250, 'SHORT. SOLD'),
                self._rbc('c', '2025-03-25', 50, -220, 'BOUGHT'),
                self._rbc('d', '2025-04-01', 10, 0, type='stock_dividend',
                          ex_date='2025-03-20')]
        held, gone, owed, near, said = self._both(book)
        self.assertEqual((held, gone, owed, near), ([-50.0, 60.0], None, None, []))
        self.assertIn("holds the security and is also short 50 of it", said)
        self.assertEqual(sorted(no_share_rows(book, entitled_lots=False)[0]), ['d'])

    @rule(R.CA_STKDIV_02)
    def test_an_ib_account_with_o_and_c_codes_in_the_same_shapes(self):
        books = self._books(self._ib)
        held, gone, owed, near, said = self._both(books['long and covered'])
        self.assertEqual((held, gone, owed, near), ([110.0], None, None, ['d']))
        self.assertIn("m held 100 share(s) on its entitlement date 2025-03-20 and "
                      "was also short 50 of the security", said)
        held, gone, owed, near, _ = self._both(books['no long, covered'])
        self.assertEqual((held, gone, owed, near), ([], ['d'], None, []))
        held, gone, owed, near, _ = self._both(books['long, short still open'])
        self.assertEqual((held, gone, owed, near), ([-50.0, 110.0], None, None, []))
        held, gone, owed, near, _ = self._both(
            books['long bought after the ex date, covered'])
        self.assertEqual((held, gone, owed, near), ([100.0], ['d'], None, []))

    @rule(R.CA_STKDIV_02)
    def test_an_ib_sale_coded_c_o_closes_the_long_and_opens_the_short(self):
        # 150 sold coded C;O: the 100 held go, 50 are sold short, covered
        # coded C after the ex date. No shares held on the date: the short's.
        book = [self._ib('b', '2025-01-06', 100, -1000, 'O'),
                self._ib('s', '2025-03-03', -150, 750, 'C;O'),
                self._ib('c', '2025-03-25', 50, -220, 'C'),
                self._ib('d', '2025-04-01', 10, 0, type='stock_dividend',
                         ex_date='2025-03-20')]
        held, gone, owed, near, _ = self._both(book)
        self.assertEqual((held, gone, owed, near), ([], ['d'], None, []))


class TestARowWithNoEntitlementDate(unittest.TestCase):
    """Nothing tells whether a short owed it: booked as before."""

    @rule(R.CA_STKDIV_01)
    def test_it_stays_shares_at_zero_cost_and_a_line_names_the_cover(self):
        rows, held, owed, said = _figures(_short() + [_div()])
        self.assertEqual((rows, held, owed),
                         ([('c', 100, 60.0, 0.0)], [(10.0, 0.0)], None))
        # Base: nothing said.
        self.assertEqual(
            said,
            "warning: ATTENTION: SYQ: the stock dividend of 10 share(s) on "
            "2025-04-01 carries no ex-dividend or record date, and m covered a "
            "short of the security on 2025-03-25, within the 90 days before it: "
            "a short open on the ex-dividend date owes the new shares, and the "
            "row is then that short's obligation, not shares acquired at $0 "
            "(CA-STKDIV-02). It is booked as shares received. Check the issuer's "
            "ex-dividend date and give the row its ex_date (`ex=DATE` on a .tt "
            "type=stock_dividend line).")

    @rule(R.CA_STKDIV_01)
    def test_a_cover_more_than_90_days_before_is_not_named(self):
        _, held, _, said = _figures(
            _short(sold='2024-11-04', covered='2024-12-02') + [_div()])
        self.assertEqual((held, said), ([(10.0, 0.0)], ''))


class TestTheRun(unittest.TestCase):
    """`tjs run` over a .tt book: the console, the gains, the holdings,
    the gains files' key and its readers."""

    SHORT = "BUYSELL 2025-03-03 10:00:00 SYQ.TO -100 CAD 5 500 0"
    COVER = "BUYSELL 2025-03-25 10:00:00 SYQ.TO 100 CAD 4.4 440 0"
    FLIP = "BUYSELL 2025-03-25 10:00:00 SYQ.TO 150 CAD 4.4 660 0"
    DIV = ("BUYSELL 2025-04-01 10:00:00 SYQ.TO 10 CAD 0 0 type=stock_dividend "
           "ex=2025-03-20")

    def _project(self, lines):
        """(the run's console, `tjs list`, `tjs sum`, the gains files'
        key by file, `tjs find-missing-history`)."""
        import json
        from _qa_project import console, flat, tj, toml
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            (root / "inputs/margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(toml(year=2025))
            (root / "ticker.map").write_text("")
            (root / "inputs/margin/book.tt").write_text("\n".join(lines) + "\n")
            run = tj(root, "run", "--no-input", "--details", check=False)
            self.assertEqual(run.returncode, 0, console(run))
            keys = {p.name: json.loads(p.read_text()).get(
                'stock_dividends_after_cover')
                for p in sorted((root / "work").glob("margin*_gains*.json"))}
            return (flat(console(run)), flat(tj(root, "list", check=False).stdout),
                    flat(tj(root, "sum", check=False).stdout), keys,
                    flat(console(tj(root, "find-missing-history", check=False))))

    @rule(R.CA_STKDIV_02)
    def test_the_run_says_the_line_and_holds_no_shares(self):
        said, listed, summed, keys, found = self._project(
            [self.SHORT, self.COVER, self.DIV])
        self.assertIn("SYQ.TO: stock dividend of 10 share(s) on 2025-04-01: booked "
                      "as no shares. The account margin was short 100 share(s) on "
                      "the row's entitlement date 2025-03-20 and covered them on "
                      "2025-03-25",
                      said)
        # Base: "stock dividend of 10 share(s) on 2025-04-01 entered at $0
        # cost", and `tjs list` shows 10 SYQ.TO at 0.00.
        self.assertNotIn("entered at $0 cost", said)
        self.assertIn("No open positions.", listed)
        self.assertIn("margin 60.00 0.00 60.00", summed)
        # The plain, the wash and the raw gains files name the row, and
        # the lists that add up the rows themselves leave it out.
        self.assertEqual(sorted(keys), ['margin_gains.json', 'margin_gains_wash.json',
                                        'margin_raw_base_gains.json',
                                        'margin_raw_gains.json'])
        self.assertEqual({len(v or ()) for v in keys.values()}, {1})
        self.assertEqual(len({tuple(v) for v in keys.values()}), 1)
        self.assertNotIn("$0 cost", said)
        self.assertNotIn("$0 cost", found)

    @rule(R.CA_STKDIV_02)
    def test_the_accounts_split_of_the_pool_is_the_pool(self):
        # One buy covers the 100 and goes long 50. The engine's pool is 50
        # at 220.00; the account's share of it counted the row's ten
        # shares (60 at 264.00) and a Warning blamed renames and splits.
        for cover, cost in ((self.FLIP, "220.00 4.40"),
                            (self.COVER + "|BUYSELL 2025-03-25 14:00:00 SYQ.TO 50 "
                             "CAD 4 200 0", "200.00 4.00")):
            with self.subTest(cover=cover):
                said, listed, summed, _, _ = self._project(
                    [self.SHORT] + cover.split("|") + [self.DIV])
                self.assertIn(f"margin SYQ.TO 50 {cost}", listed)
                self.assertIn("margin 60.00 0.00 60.00", summed)
                self.assertNotIn("rename", said.lower().replace(
                    "renames: tjs", ""))
                self.assertNotIn("$0 cost", said)

    @rule(R.CA_STKDIV_02)
    def test_the_one_line_books_the_cost_long_at_the_end_or_flat(self):
        # 60 - 45 = 15, flat after the cover or long 50 by the end of its
        # day; the shares held keep their own cost.
        for cover, listed_as in ((self.COVER, "No open positions."),
                                 (self.FLIP, "margin SYQ.TO 50 220.00 4.40")):
            with self.subTest(cover=cover):
                said, listed, summed, _, _ = self._project(
                    [self.SHORT, cover, self.DIV + " delivered_cost=45"])
                self.assertIn("margin 15.00 0.00 15.00", summed)
                self.assertIn(listed_as, listed)
                # (the entry is made: no Warning asks for it any more)
                self.assertNotIn("What delivering them cost is in no row", said)
                self.assertNotIn("Warning: SYQ.TO: stock dividend", said)

    @rule(R.CA_STKDIV_02)
    def test_books_pooled_with_a_holder_say_so_on_the_console(self):
        # Another account holds the shares: the pooled filing books read
        # the short's cover as a purchase. The pooled pass's line was in
        # its .diag only. With the token (T10) and without (T11).
        from _qa_project import console, flat, tj, toml
        for token in (" delivered_cost=45", ""):
            with self.subTest(token=token), tempfile.TemporaryDirectory() as d:
                root = Path(d) / "p"
                for acct, lines in (
                        ("acctl", ["BUYSELL 2025-01-06 10:00:00 SYQ.TO 100 CAD 10 "
                                   "1000 0"]),
                        ("margin", [self.SHORT, self.COVER, self.DIV + token])):
                    (root / f"inputs/{acct}").mkdir(parents=True)
                    (root / f"inputs/{acct}/book.tt").write_text(
                        "\n".join(lines) + "\n")
                (root / "taxjson.toml").write_text(toml(
                    year=2025, extra_accounts='[accounts.acctl]\ntype = "taxable"\n'))
                (root / "ticker.map").write_text("")
                run = tj(root, "run", "--no-input", "--details", check=False)
                self.assertEqual(run.returncode, 0, console(run))
                said = flat(console(run))
            self.assertIn("Warning: pooled short: SYQ.TO: stock dividend of 10 "
                          "share(s) on 2025-04-01: booked as no shares.", said)
            if token:
                self.assertIn("delivered_cost of 45.00 is not booked in the books "
                              "pooled across your accounts, the figures you file",
                              said)
            else:
                # The account's own line promises its own books only.
                self.assertIn("the account's own books then book it as a cost of "
                              "the cover on 2025-03-25", said)
                self.assertIn("is booked in the account's own books and not in the "
                              "books pooled across your accounts", said)
                self.assertNotIn("it is booked as a cost of the cover", said)

    @rule(R.CA_STKDIV_02)
    def test_a_row_with_no_such_key_reads_as_before(self):
        # (a control: no date on the row, so it stays ten shares at $0,
        # no file names it, and the $0-cost line is said)
        said, listed, _, keys, _ = self._project([self.SHORT, self.COVER,
                                                  self.DIV.replace(" ex=2025-03-20", "")])
        self.assertEqual(set(keys.values()), {None})
        self.assertIn("margin SYQ.TO 10 0.00", listed)
        self.assertIn("entered at $0 cost", said)


if __name__ == "__main__":
    unittest.main()
