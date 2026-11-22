"""Shares of a rename's target bought before the rename are another security
for the superficial-loss rule (backlog TJ-2699; the owner's ruling of
2026-11-11).

A SPLIT row renames OLD.TO into NEW.TO on 2025-04-01. The identity classes
join OLD.TO's rows before the date with NEW.TO's whole epoch, so NEW.TO's
own shares bought on 02-20 counted as the identical property that denied an
OLD.TO loss of 02-03: a merger into shares already held, another issuer's
before the date (IT-387R2). Within the class the denial pass now tests each
row against the loss (`SplitTimeline.identical`, CA-ACB-RENAME): the
target's rows dated before the split window of the rename
(SPLIT_DATE_WINDOW_DAYS, 7 days) are another security; a row inside the
window is a broker booking the new ticker early, the same security as
OLD.TO's; and every row after the rename is. A call on the target's shares
is read the same way. The mirror case (a loss on NEW.TO before the date, an
OLD.TO purchase in the window) is separate by the same test. The US engine
keys its wash-sale matching by the class alone and does not move. Synthetic
books, made-up tickers.
"""
import contextlib
import io
import unittest

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.corporate_timeline import SplitTimeline
from taxjson.lib.row_types import OWN_MOVE_TYPE
from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both
from taxjson.rules import ids as R

OLD, NEW = 'OLD.TO', 'NEW.TO'
CALL = 'ZZR250620C00010000'       # a call on ZZR, the bare-ticker books


def _tr(date, sym, qty, net, acct='b', currency='CAD'):
    return TaxTransaction(action='BUYSELL', date=date, date_settle=date,
                          time='10:00:00', symbol=sym, quantity=qty,
                          price=abs(net / qty), net_amount=net,
                          currency=currency, account=acct)


def _rename(date, old, new, currency='CAD', acct='b'):
    return TaxTransaction(action='SPLIT', date=date, date_settle=date,
                          time='09:00:00', symbol=old, symbol_new=new,
                          quantity=1.0, net_amount=0.0, currency=currency,
                          account=acct)


def _move(date, sym, qty, acct):
    """One leg of a move of your own between two registered accounts."""
    return TaxTransaction(action='TRANSFER', date=date, date_settle=date,
                          time='00:00:00', symbol=sym, quantity=qty,
                          price=0.0, net_amount=0.0, currency='CAD',
                          account=acct, type=OWN_MOVE_TYPE,
                          description='own-account move #1')


def _window(res, date):
    """(bal_at_end, disallowed_qty) of the loss sale dated `date`."""
    w = next(e['wash_window'] for e in res['transactions']
             if e['date'] == date and e.get('wash_window'))
    return (round(w['bal_at_end'], 2), round(w['disallowed_qty'], 2))


def _run(book, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = CanadaTaxRules().compute_gains(book, **kw)
    return res, err.getvalue()


def _records(res):
    """(date, symbol, gain, denied) of every realized row."""
    return sorted((e['date'], e['symbol'], round(e['gain'], 2),
                   round(e.get('disallowed_amount') or 0.0, 2))
                  for e in res['transactions'] if 'gain' in e)


def _denied(res):
    return [round(w['disallowed_amount'], 2) for w in res['wash_sales']]


def _symptom(currency='CAD'):
    """TJ-2698's book without the short: 100 OLD.TO bought for 2000
    (01-02), sold for 1000 (02-03, a loss of 1000), rebought for 1000
    (02-08) and sold again (02-13); 100 NEW.TO bought for 1000 (02-20);
    the rename (04-01); the NEW.TO shares sold for 1200 (06-02)."""
    return [_tr('2025-01-02', OLD, 100, 2000, currency=currency),
            _tr('2025-02-03', OLD, -100, 1000, currency=currency),
            _tr('2025-02-08', OLD, 100, 1000, currency=currency),
            _tr('2025-02-13', OLD, -100, 1000, currency=currency),
            _tr('2025-02-20', NEW, 100, 1000, currency=currency),
            _rename('2025-04-01', OLD, NEW, currency=currency),
            _tr('2025-06-02', NEW, -100, 1200, currency=currency)]


def _near(new_buy_date):
    """A loss of 1000 on OLD.TO (03-10) whose window reaches past the
    rename (day 30 is 04-09); 100 NEW.TO bought for 1000 on
    `new_buy_date`, held; sold for 1200 on 06-02."""
    return [_tr('2025-01-02', OLD, 100, 2000),
            _tr('2025-03-10', OLD, -100, 1000),
            _tr(new_buy_date, NEW, 100, 1000),
            _rename('2025-04-01', OLD, NEW),
            _tr('2025-06-02', NEW, -100, 1200)]


class TestTargetSharesBeforeTheRename(unittest.TestCase):

    @rule(R.CA_ACB_RENAME, R.CA_SL_02)
    def test_the_targets_shares_bought_before_the_rename_deny_nothing(self):
        """The symptom: the 02-08 rebuy is sold out before day 30 and the
        02-20 NEW.TO purchase is another security, so nothing identical
        is held at day 30 (03-05): the loss is allowed, -1000 on 02-03,
        and the NEW.TO shares realize +200 on 06-02 at their paid cost.
        Under the class rule the loss was denied and the sale -800."""
        res, err = _run(_symptom())
        self.assertEqual(_records(res),
                         [('2025-02-03', OLD, -1000.0, 0.0),
                          ('2025-02-13', OLD, 0.0, 0.0),
                          ('2025-06-02', NEW, 200.0, 0.0)], err)
        self.assertEqual(_denied(res), [], err)
        self.assertTrue(res['summary']['wash_solver_converged'], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02)
    def test_a_target_row_inside_the_split_window_is_the_same_security(self):
        """NEW.TO bought on 03-25, seven days before the rename (the split
        window, SPLIT_DATE_WINDOW_DAYS: a broker booking the new ticker
        early): the identical property held at day 30. The loss is
        denied and its 1000 comes back on the sale: -800 on 06-02."""
        res, err = _run(_near('2025-03-25'))
        self.assertEqual(_records(res),
                         [('2025-03-10', OLD, 0.0, 1000.0),
                          ('2025-06-02', NEW, -800.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02)
    def test_a_target_row_a_day_before_the_window_is_another_security(self):
        """The same book with the purchase on 03-24, eight days before
        the rename: outside the window, the target's own shares. The
        loss is allowed (-1000 on 03-10) and the sale realizes +200."""
        res, err = _run(_near('2025-03-24'))
        self.assertEqual(_records(res),
                         [('2025-03-10', OLD, -1000.0, 0.0),
                          ('2025-06-02', NEW, 200.0, 0.0)], err)
        self.assertEqual(_denied(res), [], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02)
    def test_the_mirror_a_loss_on_the_target_and_a_purchase_of_the_source(self):
        """100 NEW.TO bought for 2000 (01-02) and sold for 1000 (02-03, a
        loss of 1000); 100 OLD.TO bought for 1000 on 02-08 and held
        through the rename, which carries them into NEW.TO; sold for
        1200 on 06-02. The OLD.TO purchase was another issuer's shares
        when the NEW.TO shares were sold (the mirror of the symptom):
        the loss is allowed and the sale realizes +200. Under the class
        rule the loss was denied and came back on the sale (-800)."""
        res, err = _run([_tr('2025-01-02', NEW, 100, 2000),
                         _tr('2025-02-03', NEW, -100, 1000),
                         _tr('2025-02-08', OLD, 100, 1000),
                         _rename('2025-04-01', OLD, NEW),
                         _tr('2025-06-02', NEW, -100, 1200)])
        self.assertEqual(_records(res),
                         [('2025-02-03', NEW, -1000.0, 0.0),
                          ('2025-06-02', NEW, 200.0, 0.0)], err)
        self.assertEqual(_denied(res), [], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02, R.CA_SL_09)
    def test_a_merger_into_shares_already_held_the_renamed_rebuy_denies(self):
        """100 NEW.TO held since 01-20 (1000). OLD.TO: bought 01-02 for
        2000, sold 03-10 for 1000 (a loss), rebought 03-15 for 1000 and
        held: the rename carries the rebuy into NEW.TO, where it is held
        at day 30 (04-09). The rebuy is the identical property (OLD.TO
        before the date, NEW.TO after it); the 01-20 shares are another
        security and back nothing. The loss is denied; its 1000 joins
        NEW.TO's one pool (200 shares, 3000): the sale of 100 on 06-02
        realizes -300 (1200 against 1500), 100 left at 1500."""
        res, err = _run([_tr('2025-01-20', NEW, 100, 1000),
                         _tr('2025-01-02', OLD, 100, 2000),
                         _tr('2025-03-10', OLD, -100, 1000),
                         _tr('2025-03-15', OLD, 100, 1000),
                         _rename('2025-04-01', OLD, NEW),
                         _tr('2025-06-02', NEW, -100, 1200)])
        self.assertEqual(_records(res),
                         [('2025-03-10', OLD, 0.0, 1000.0),
                          ('2025-06-02', NEW, -300.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0], err)
        self.assertEqual([(e['symbol'], round(e['qty'], 2),
                           round(float(e['total_cost']), 2))
                          for e in res['inventory']],
                         [(NEW, 100.0, 1500.0)], err)

    def _call_book(self, call_date):
        """Bare tickers: 100 ZZQ bought for 2000 (01-02), sold for 1000
        (03-10, a loss of 1000); one call on ZZR bought for 100 on
        `call_date` and held past day 30 (04-09), sold 05-01; ZZQ renamed
        into ZZR on 04-01."""
        return [_tr('2025-01-02', 'ZZQ', 100, 2000),
                _tr('2025-03-10', 'ZZQ', -100, 1000),
                _tr(call_date, CALL, 1, 100),
                _rename('2025-04-01', 'ZZQ', 'ZZR'),
                _tr('2025-05-01', CALL, -1, 100)]

    @rule(R.CA_ACB_RENAME, R.CA_SL_05)
    def test_a_call_on_the_targets_shares_before_the_window_is_no_replacement(self):
        """A call on ZZR bought on 03-20, before the split window of the
        rename: a right to another issuer's shares then. The loss is
        allowed; the call's own trade is flat."""
        res, err = _run(self._call_book('2025-03-20'))
        self.assertEqual(_records(res),
                         [('2025-03-10', 'ZZQ', -1000.0, 0.0),
                          ('2025-05-01', CALL, 0.0, 0.0)], err)
        self.assertEqual(_denied(res), [], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_05)
    def test_a_call_on_the_targets_shares_inside_the_window_replaces(self):
        """The same call bought on 03-26, inside the split window: a
        right to the renamed shares, held at day 30. The loss is denied
        (one contract of 100 shares backs the 100 sold)."""
        res, err = _run(self._call_book('2025-03-26'))
        self.assertEqual(_denied(res), [1000.0], err)
        self.assertEqual([r for r in _records(res) if r[1] == 'ZZQ'],
                         [('2025-03-10', 'ZZQ', 0.0, 1000.0)], err)


class TestTheUsEngineDoesNotMove(unittest.TestCase):

    @rule(R.CA_ACB_RENAME)
    @rule_absent(R.CA_ACB_RENAME, country="usa")
    @rule(R.US_BASIS_RENAME, R.US_WASH_01)
    def test_the_same_book_under_the_us_engine_washes_the_loss(self):
        """The symptom's book in USD under both engines. Canada: the loss
        allowed (-1000) and +200 on the sale. The US engine keys its
        wash-sale matching by the class alone (`class_at`, unchanged):
        the 02-08 rebuy washes the 02-03 loss (its basis 2000), its own
        sale on 02-13 loses 1000 again, and the NEW.TO purchase of 02-20
        washes that loss too, the target's shares being the class's: the
        1000 is added to their basis and the sale realizes -800."""
        r = gains_both(_symptom(currency='USD'), year=2025)
        self.assertEqual(_records(r['canada']),
                         [('2025-02-03', OLD, -1000.0, 0.0),
                          ('2025-02-13', OLD, 0.0, 0.0),
                          ('2025-06-02', NEW, 200.0, 0.0)],
                         r['canada']['_stderr'])
        self.assertEqual(_denied(r['canada']), [])
        self.assertEqual(_records(r['usa']),
                         [('2025-02-03', OLD, 0.0, 1000.0),
                          ('2025-02-13', OLD, 0.0, 1000.0),
                          ('2025-06-02', NEW, -800.0, 0.0)],
                         r['usa']['_stderr'])
        self.assertEqual(_denied(r['usa']), [1000.0, 1000.0])


class TestTheTargetsOwnSharesAreOlderUnits(unittest.TestCase):
    """The reviews' books (TJ-3289, TJ-3290): a target's own shares bought
    before the split window are never an acquisition, but they are the
    holder's older units of that symbol, which the symbol's later sales
    draw first (FIFO, the walk's convention). So a sale of the target
    inside the window or after the rename comes off them before it comes
    off the shares the rename brought in, and after the rename the merged
    pool's balance is the identical property held: the figures are the
    class rule's. The first version of the fix dropped the purchases and
    kept the sales, which netted the sales against the renamed rebuy."""

    def _mixed(self, *tail, own='2025-01-20'):
        """100 NEW.TO held since `own` (1000); OLD.TO: 100 bought 01-02 for
        2000, sold 03-10 for 1000 (a loss), rebought 03-15 and held; the
        rename on 04-01; then `tail` (day 30 is 04-09)."""
        return [_tr(own, NEW, 100, 1000), _tr('2025-01-02', OLD, 100, 2000),
                _tr('2025-03-10', OLD, -100, 1000),
                _tr('2025-03-15', OLD, 100, 1000)] + list(tail)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02, R.CA_SL_09)
    def test_a_sale_of_the_merged_pool_before_day_30_takes_the_own_shares_first(self):
        """B1: 100 of the 200 NEW.TO sold on 04-03 (1100), the other 100 on
        06-02. The sale takes the 01-20 shares; the renamed rebuy is held
        at day 30: 1000 denied on 03-10 (the pool at 3000); the 04-03 sale
        of 100 at 1500 loses 400, denied again by the 03-15 rebuy held;
        the 06-02 sale realizes -700 (1200 against 1900)."""
        res, err = _run(self._mixed(_rename('2025-04-01', OLD, NEW),
                                    _tr('2025-04-03', NEW, -100, 1100),
                                    _tr('2025-06-02', NEW, -100, 1200)))
        self.assertEqual(_records(res),
                         [('2025-03-10', OLD, 0.0, 1000.0),
                          ('2025-04-03', NEW, 0.0, 400.0),
                          ('2025-06-02', NEW, -700.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0, 400.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02, R.CA_SL_08)
    def test_a_sale_past_the_own_shares_takes_the_rebuy_in_part(self):
        """B17: 120 of the 200 sold on 04-03 (1320): the 100 own shares
        and 20 of the renamed rebuy, 80 held at day 30. 80 units are
        denied (800, -200 allowed); the 04-03 sale at 1680 loses 360, of
        which 240 (the 80 held) is denied again; the 06-02 sale of 80 for
        960 realizes -400."""
        res, err = _run(self._mixed(_rename('2025-04-01', OLD, NEW),
                                    _tr('2025-04-03', NEW, -120, 1320),
                                    _tr('2025-06-02', NEW, -80, 960)))
        self.assertEqual(_records(res),
                         [('2025-03-10', OLD, -200.0, 800.0),
                          ('2025-04-03', NEW, -120.0, 240.0),
                          ('2025-06-02', NEW, -400.0, 0.0)], err)
        self.assertEqual(_denied(res), [800.0, 240.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02)
    def test_a_target_sale_inside_the_split_window_takes_the_own_shares(self):
        """B2 (TJ-3290): the 100 own NEW.TO sold on 03-27 (1100), inside
        the split window and before the rename: before the rename the
        target's pool holds only its own shares, so the sale cannot
        reduce the renamed rebuy's holding. 1000 denied, +100 on 03-27,
        -800 on 06-02. The same with the sale on 03-20, outside the
        window (another security's sale, the same units)."""
        for day in ('2025-03-27', '2025-03-20'):
            with self.subTest(sale=day):
                res, err = _run(self._mixed(_tr(day, NEW, -100, 1100),
                                            _rename('2025-04-01', OLD, NEW),
                                            _tr('2025-06-02', NEW, -100, 1200)))
                self.assertEqual(_records(res),
                                 [('2025-03-10', OLD, 0.0, 1000.0),
                                  (day, NEW, 100.0, 0.0),
                                  ('2025-06-02', NEW, -800.0, 0.0)], err)
                self.assertEqual(_denied(res), [1000.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02, R.CA_SL_09)
    def test_the_other_reviews_books_a_later_sale_and_a_window_sale(self):
        """The Opus review's B1 and B2. B1: the own shares bought 02-01,
        100 of the 200 sold 04-05 for 1000 (at 1500, a loss of 500 denied
        again by the rebuy held), 100 sold 06-02: 1000 denied, -800. B2:
        the loss on 03-01 and the rebuy 03-05; the own shares (01-15) sold
        03-27 for their cost: 1000 denied, 0 on 03-27, -800 on 06-02."""
        res, err = _run(self._mixed(_rename('2025-04-01', OLD, NEW),
                                    _tr('2025-04-05', NEW, -100, 1000),
                                    _tr('2025-06-02', NEW, -100, 1200),
                                    own='2025-02-01'))
        self.assertEqual(_records(res),
                         [('2025-03-10', OLD, 0.0, 1000.0),
                          ('2025-04-05', NEW, 0.0, 500.0),
                          ('2025-06-02', NEW, -800.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0, 500.0], err)
        res, err = _run([_tr('2025-01-02', OLD, 100, 2000),
                         _tr('2025-01-15', NEW, 100, 1000),
                         _tr('2025-03-01', OLD, -100, 1000),
                         _tr('2025-03-05', OLD, 100, 1000),
                         _tr('2025-03-27', NEW, -100, 1000),
                         _rename('2025-04-01', OLD, NEW),
                         _tr('2025-06-02', NEW, -100, 1200)])
        self.assertEqual(_records(res),
                         [('2025-03-01', OLD, 0.0, 1000.0),
                          ('2025-03-27', NEW, 0.0, 0.0),
                          ('2025-06-02', NEW, -800.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_03)
    def test_a_registered_accounts_own_shares_and_window_sale(self):
        """RG3: the RRSP holds 100 own NEW.TO since 01-15, rebuys 100 OLD.TO
        on 03-05 after the taxable loss of 03-01 and sells its 100 NEW.TO
        on 03-27 (the own shares): the OLD.TO rebuy is held at day 30 in
        the RRSP, so the 1000 is denied and lost for good."""
        reg = [_tr('2025-01-15', NEW, 100, 1000, acct='r'),
               _tr('2025-03-05', OLD, 100, 1000, acct='r'),
               _tr('2025-03-27', NEW, -100, 1000, acct='r')]
        res, err = _run([_tr('2025-01-02', OLD, 100, 2000),
                         _tr('2025-03-01', OLD, -100, 1000),
                         _rename('2025-04-01', OLD, NEW)],
                        sheltered_transactions=reg)
        self.assertEqual(_records(res), [('2025-03-01', OLD, 0.0, 1000.0)],
                         err)
        self.assertEqual(_denied(res), [1000.0], err)
        self.assertEqual([round(e['permanently_disallowed'], 2)
                          for e in res['transactions']
                          if e['date'] == '2025-03-01'], [1000.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02, R.CA_SL_08)
    def test_fractional_shares(self):
        """FR1, B2's shape in fractions: 10.5 OLD.TO, a loss of 105, the
        rebuy of 10.5 held; 0.5 own NEW.TO sold 03-27 at cost. 105 denied
        in full (10.5 units), 0 on 03-27, -84 on 06-02 (126 against 210)."""
        res, err = _run([_tr('2025-01-02', OLD, 10.5, 210),
                         _tr('2025-01-15', NEW, 0.5, 10),
                         _tr('2025-03-01', OLD, -10.5, 105),
                         _tr('2025-03-05', OLD, 10.5, 105),
                         _tr('2025-03-27', NEW, -0.5, 10),
                         _rename('2025-04-01', OLD, NEW),
                         _tr('2025-06-02', NEW, -10.5, 126)])
        self.assertEqual(_records(res),
                         [('2025-03-01', OLD, 0.0, 105.0),
                          ('2025-03-27', NEW, 0.0, 0.0),
                          ('2025-06-02', NEW, -84.0, 0.0)], err)
        self.assertEqual(_denied(res), [105.0], err)


class TestRenameChains(unittest.TestCase):
    """`identical` over chains: a rename and back (TJ-3291), and a source
    renamed into two targets on one date (TJ-3296)."""

    @rule(R.CA_ACB_RENAME, R.CA_SL_02)
    def test_a_rename_and_back_joins_the_symbols_own_later_epoch(self):
        """AAA.TO sold 02-25 at a loss of 1000, AAA renamed BBB on 03-01 and
        back on 03-20, 100 AAA.TO rebought 03-22 and held: the rebuy is
        the same shares' security (AAA's chain reaches AAA again), the
        loss is denied and comes back on 06-02 (-800). The first version
        stopped the chain at a symbol already seen and allowed it."""
        A, B = 'AAA.TO', 'BBB.TO'
        res, err = _run([_tr('2025-01-02', A, 100, 2000),
                         _tr('2025-02-25', A, -100, 1000),
                         _rename('2025-03-01', A, B),
                         _rename('2025-03-20', B, A),
                         _tr('2025-03-22', A, 100, 1000),
                         _tr('2025-06-02', A, -100, 1200)])
        self.assertEqual(_records(res),
                         [('2025-02-25', A, 0.0, 1000.0),
                          ('2025-06-02', A, -800.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02)
    def test_a_loss_under_the_new_name_against_a_purchase_under_the_old(self):
        """100 A.TO bought 01-02 (2000) and 100 on 03-15 (1000); A renamed B
        on 04-01; 100 B.TO sold 04-10 for 1000 (a loss of 500 at the 1500
        average); B renamed back to A on 05-01; 100 A.TO sold 06-02. The
        03-15 purchase is in the window and its shares are held at day
        30: 500 denied, -800 on 06-02. The pass asks identical(B 04-10,
        A 03-15), which the first version answered by B's chain alone
        (A's row before the window of the rename back: not identical)."""
        A, B = 'A.TO', 'B.TO'
        res, err = _run([_tr('2025-01-02', A, 100, 2000),
                         _tr('2025-03-15', A, 100, 1000),
                         _rename('2025-04-01', A, B),
                         _tr('2025-04-10', B, -100, 1000),
                         _rename('2025-05-01', B, A),
                         _tr('2025-06-02', A, -100, 1200)])
        self.assertEqual(_records(res),
                         [('2025-04-10', B, 0.0, 500.0),
                          ('2025-06-02', A, -800.0, 0.0)], err)
        self.assertEqual(_denied(res), [500.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02)
    def test_a_source_renamed_into_two_targets_on_one_date(self):
        """Two accounts' brokers spell the target differently: AAA to BBB
        in account b and AAA to CCC in account c, both 04-01; the AAA loss
        of 03-10 and 100 CCC bought 04-05 in c and held: both targets are
        followed, the loss is denied (-800 on 06-02)."""
        A, B, C = 'AAA.TO', 'BBB.TO', 'CCC.TO'
        res, err = _run([_tr('2025-01-02', A, 100, 2000),
                         _tr('2025-03-10', A, -100, 1000),
                         _rename('2025-04-01', A, B, acct='b'),
                         _rename('2025-04-01', A, C, acct='c'),
                         _tr('2025-04-05', C, 100, 1000, acct='c'),
                         _tr('2025-06-02', C, -100, 1200, acct='c')])
        self.assertEqual(_records(res),
                         [('2025-03-10', A, 0.0, 1000.0),
                          ('2025-06-02', C, -800.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0], err)

    @rule(R.CA_ACB_RENAME)
    def test_identical_answers_the_same_in_either_order(self):
        """The relation on its own: every pair both ways."""
        A, B, C = 'A.TO', 'B.TO', 'C.TO'
        cases = [
            # a rename and back: A to B 04-01, B to A 05-01
            ([_rename('2025-04-01', A, B), _rename('2025-05-01', B, A)],
             [(A, '2025-03-15', B, '2025-04-10', True),
              (A, '2025-03-10', A, '2025-05-10', True),
              (A, '2025-03-10', A, '2025-04-26', True),
              (B, '2025-04-10', A, '2025-05-10', True)]),
            # one symbol out and back: A to B 03-01, B to A 03-20
            ([_rename('2025-03-01', A, B), _rename('2025-03-20', B, A)],
             [(A, '2025-02-25', A, '2025-03-22', True),
              (A, '2025-02-25', B, '2025-03-10', True)]),
            # two targets on one date
            ([_rename('2025-04-01', A, B, acct='b'),
              _rename('2025-04-01', A, C, acct='c')],
             [(A, '2025-03-10', B, '2025-04-05', True),
              (A, '2025-03-10', C, '2025-04-05', True),
              (A, '2025-03-10', C, '2025-03-20', False)]),
            # two sources of one target: never identical to each other
            ([_rename('2025-04-01', A, C), _rename('2025-05-01', B, C)],
             [(A, '2025-03-10', B, '2025-03-15', False),
              (B, '2025-03-10', C, '2025-04-20', False),
              (B, '2025-03-10', C, '2025-04-24', True)]),
        ]
        for rows, pairs in cases:
            tl = SplitTimeline.from_transactions(rows)
            for sa, da, sb, db, want in pairs:
                with self.subTest(pair=(sa, da, sb, db)):
                    self.assertEqual(tl.identical(sa, da, sb, db), want)
                    self.assertEqual(tl.identical(sb, db, sa, da), want)


class TestTheOlderUnitsFollowTheRename(unittest.TestCase):
    """The regression review's books (TJ-3299, TJ-3300, TJ-3298): the older
    units are keyed by the pool that holds them as of each row, so a sale
    of the merged pool booked under the other symbol draws them; a kept
    purchase nets the pool's negative older units (a short in the target
    before the window); an own move's two legs show the same units."""

    @rule(R.CA_ACB_RENAME, R.CA_SL_02, R.CA_SL_09)
    def test_the_mirror_with_the_source_held_through_the_rename(self):
        """K13: 100 NEW.TO held since 01-20 (2000), 100 OLD.TO since 01-02
        (1000); the NEW.TO loss of 03-13 (before the window of the 04-10
        rename), 100 NEW.TO rebought 03-20 (the trigger); OLD.TO renamed
        into NEW.TO 04-10; 100 of the 200 sold 04-11 (1100), 100 on 06-02.
        OLD.TO's shares are another security than the 03-13 loss and are
        the pool's older units once the rename folds them into NEW.TO: the
        04-11 sale takes them, the 03-20 shares are held at day 30 (04-12).
        1000 denied; the 04-11 sale at 1500 loses 400, denied again; 06-02
        -700. With 150 sold on 04-11 (K13p): 500 denied, 75, -100."""
        head = [_tr('2025-01-20', NEW, 100, 2000), _tr('2025-01-02', OLD, 100, 1000),
                _tr('2025-03-13', NEW, -100, 1000), _tr('2025-03-20', NEW, 100, 1000),
                _rename('2025-04-10', OLD, NEW)]
        res, err = _run(head + [_tr('2025-04-11', NEW, -100, 1100),
                                _tr('2025-06-02', NEW, -100, 1200)])
        self.assertEqual(_records(res),
                         [('2025-03-13', NEW, 0.0, 1000.0),
                          ('2025-04-11', NEW, 0.0, 400.0),
                          ('2025-06-02', NEW, -700.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0, 400.0], err)
        res, err = _run(head + [_tr('2025-04-11', NEW, -150, 1650),
                                _tr('2025-06-02', NEW, -50, 600)])
        self.assertEqual(_records(res),
                         [('2025-03-13', NEW, -500.0, 500.0),
                          ('2025-04-11', NEW, -150.0, 75.0),
                          ('2025-06-02', NEW, -100.0, 0.0)], err)
        self.assertEqual(_denied(res), [500.0, 75.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02, R.CA_SL_03)
    def test_two_sources_the_other_sources_shares_are_the_older_units(self):
        """K12: A and B both renamed into C on 04-01; 100 B held since 01-02
        (another security than A); the A loss of 03-10, rebought 03-15; 100
        of the 200 C sold 04-03, 100 on 06-02. The C sale takes B's shares:
        1000 denied, 400 on 04-03, -700. K12r: B's shares and the A rebuy
        in the RRSP, which sells 100 C on 04-03: 1000 denied, lost for
        good, 100 held at day 30."""
        A, B, C = 'AAA.TO', 'BBB.TO', 'CCC.TO'
        res, err = _run([_tr('2025-01-02', B, 100, 1000), _tr('2025-01-02', A, 100, 2000),
                         _tr('2025-03-10', A, -100, 1000), _tr('2025-03-15', A, 100, 1000),
                         _rename('2025-04-01', A, C), _rename('2025-04-01', B, C),
                         _tr('2025-04-03', C, -100, 1100), _tr('2025-06-02', C, -100, 1200)])
        self.assertEqual(_records(res),
                         [('2025-03-10', A, 0.0, 1000.0),
                          ('2025-04-03', C, 0.0, 400.0),
                          ('2025-06-02', C, -700.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0, 400.0], err)
        res, err = _run([_tr('2025-01-02', A, 100, 2000), _tr('2025-03-10', A, -100, 1000),
                         _rename('2025-04-01', A, C), _rename('2025-04-01', B, C)],
                        sheltered_transactions=[
                            _tr('2025-01-02', B, 100, 1000, acct='r'),
                            _tr('2025-03-15', A, 100, 1000, acct='r'),
                            _tr('2025-04-03', C, -100, 1100, acct='r')])
        self.assertEqual(_records(res), [('2025-03-10', A, 0.0, 1000.0)], err)
        self.assertEqual([round(e['permanently_disallowed'], 2)
                          for e in res['transactions'] if e['date'] == '2025-03-10'],
                         [1000.0], err)
        self.assertEqual(_window(res, '2025-03-10'), (100.0, 100.0), err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02)
    def test_a_chain_folds_the_middle_symbols_own_shares_into_the_last(self):
        """K1: A renamed B on 04-01 and B renamed C on 04-15; 100 B held
        since 03-01 (before A's window); the A loss of 03-28, rebought
        03-30 and held; 100 of the 200 C sold 04-20 (day 30 is 04-27), 100
        on 06-02. B's own shares, folded into C, are what the 04-20 sale
        takes: 1000 denied, 400 on 04-20, -700, as its one-rename twin
        with the sale under B."""
        A, B, C = 'AAA.TO', 'BBB.TO', 'CCC.TO'
        res, err = _run([_tr('2025-01-02', A, 100, 2000), _tr('2025-03-01', B, 100, 1000),
                         _tr('2025-03-28', A, -100, 1000), _tr('2025-03-30', A, 100, 1000),
                         _rename('2025-04-01', A, B), _rename('2025-04-15', B, C),
                         _tr('2025-04-20', C, -100, 1100), _tr('2025-06-02', C, -100, 1200)])
        self.assertEqual(_records(res),
                         [('2025-03-28', A, 0.0, 1000.0),
                          ('2025-04-20', C, 0.0, 400.0),
                          ('2025-06-02', C, -700.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0, 400.0], err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02, R.CA_SL_07)
    def test_a_short_in_the_target_before_the_window_covered_inside_it(self):
        """K14: 100 NEW.TO sold short 02-01 (before the window), covered
        03-27 (inside it); the OLD.TO loss of 03-10, rebought 03-15 and
        sold 03-20. The cover nets the short, the pool's negative older
        units, and acquires nothing: the loss is allowed. K14b, the rebuy
        held: 1000 denied with 100 held at day 30, not 200."""
        head = [_tr('2025-01-02', OLD, 100, 2000), _tr('2025-02-01', NEW, -100, 1000),
                _tr('2025-03-10', OLD, -100, 1000), _tr('2025-03-15', OLD, 100, 1000)]
        res, err = _run(head + [_tr('2025-03-20', OLD, -100, 1000),
                                _tr('2025-03-27', NEW, 100, 900),
                                _rename('2025-04-01', OLD, NEW)])
        self.assertEqual(_records(res),
                         [('2025-03-10', OLD, -1000.0, 0.0),
                          ('2025-03-20', OLD, 0.0, 0.0),
                          ('2025-03-27', NEW, 100.0, 0.0)], err)
        self.assertEqual(_denied(res), [], err)
        res, err = _run(head + [_tr('2025-03-27', NEW, 100, 900),
                                _rename('2025-04-01', OLD, NEW),
                                _tr('2025-06-02', NEW, -100, 1200)])
        self.assertEqual(_records(res),
                         [('2025-03-10', OLD, 0.0, 1000.0),
                          ('2025-03-27', NEW, 100.0, 0.0),
                          ('2025-06-02', NEW, -800.0, 0.0)], err)
        self.assertEqual(_denied(res), [1000.0], err)
        self.assertEqual(_window(res, '2025-03-10'), (100.0, 100.0), err)

    @rule(R.CA_ACB_RENAME, R.CA_SL_02, R.CA_SL_03)
    def test_an_own_move_of_the_targets_own_shares_shows_the_same_units_on_both_legs(self):
        """K9b (TJ-3298): RRSP r1 holds 100 NEW.TO since 01-20 and buys 50
        OLD.TO on 03-15 after the taxable loss of 03-10; on 03-27 it moves
        the 100 NEW.TO to RRSP r2 (two balance-only TRANSFER legs inside
        the window). The out leg takes r1's older units and the in leg
        carries them as r2's: 500 denied for good (r1's 50), and the
        holding at day 30 reads 50, not 150."""
        res, err = _run([_tr('2025-01-02', OLD, 100, 2000),
                         _tr('2025-03-10', OLD, -100, 1000),
                         _rename('2025-04-01', OLD, NEW)],
                        sheltered_transactions=[
                            _tr('2025-01-20', NEW, 100, 1000, acct='r1'),
                            _tr('2025-03-15', OLD, 50, 500, acct='r1'),
                            _move('2025-03-27', NEW, -100, 'r1'),
                            _move('2025-03-27', NEW, 100, 'r2')])
        self.assertEqual(_records(res), [('2025-03-10', OLD, -500.0, 500.0)], err)
        self.assertEqual([round(e['permanently_disallowed'], 2)
                          for e in res['transactions'] if e['date'] == '2025-03-10'],
                         [500.0], err)
        self.assertEqual(_window(res, '2025-03-10'), (50.0, 50.0), err)


if __name__ == '__main__':
    unittest.main()
