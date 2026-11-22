"""The Canadian pools' owner (PLAN step 13): `AcbPool` and `PoolBook`
(engine/ca/pool.py), and the step's done-when: the class has none of a
dict's writes, its fields are read-only, no other file names a private
slot or builds a pool, its public methods are the listed operations and
the dict alias is gone.

The walk's pools are the class's objects from their first row, and stay so
across the forward denial pass's checkpoints: `_WalkFold` copies the book
(`PoolBook.save`: the pools and the spin-off children that are the same
objects) inside its one deep copy, so a restore must put back pools of the
class, the book in place, and each child still the book's own pool, not a
copy beside it.
Synthetic data only.
"""
import ast
import contextlib
import copy
import io
import unittest
from decimal import Decimal
from unittest import mock

from taxjson.engine.ca import fold as fold_module
from taxjson.engine.ca.pool import AcbPool, PoolBook
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from _support.engine_ca_pool_books import pool_fields
import _paths

ENGINE = _paths.SRC / 'taxjson' / 'engine'
POOL_PY = ENGINE / 'ca' / 'pool.py'
SLOTS = set(AcbPool.__slots__)

# The pool's operations: every public name of the class that is not a field.
POOL_OPERATIONS = {
    'new', 'open', 'take_pending', 'close', 'leftover', 'drain',
    'add_cost', 'park', 'tally_denied', 'give_up', 'cut_for_roc', 'restore_roc',
    'reset_negative', 'late_premium', 'add_wash_cut',
    'move_in', 'move_beyond', 'move_out', 'move_out_lots', 'move_in_from',
    'split', 'merge', 'stamp_currency',
    'taint', 'taint_from', 'untaint', 'add_sources',
    'open_lot', 'close_lots', 'retag_lots', 'record_grant', 'record_grant_loss',
    'snapshot', 'part', 'netted'}
BOOK_OPERATIONS = {'ensure', 'rename', 'is_spinoff', 'give_up_to_child', 'child_opens',
                   'save', 'restore'}


def _trees(folder):
    for path in sorted(folder.rglob('*.py')):
        yield path, ast.parse(path.read_text(encoding='utf-8'))


class TestThePoolIsWrittenByItsOperationsAlone(unittest.TestCase):
    """PLAN step 13's done-when."""

    def test_the_class_is_no_dict_and_has_none_of_a_dicts_writes(self):
        self.assertFalse(issubclass(AcbPool, dict))
        for name in ('__setitem__', '__getitem__', 'pop', 'setdefault', 'update', 'get'):
            self.assertFalse(hasattr(AcbPool, name), name)

    def test_every_field_is_read_only(self):
        pool = AcbPool.new('CAD')
        for slot in sorted(SLOTS):
            prop = vars(AcbPool)[slot[1:]]
            self.assertIsInstance(prop, property, slot)
            self.assertIsNone(prop.fset, slot)
            self.assertIsNone(prop.fdel, slot)
            with self.assertRaises(AttributeError, msg=slot):
                setattr(pool, slot[1:], 1)
        with self.assertRaises(AttributeError):     # and no field can be added
            pool.anything_else = 1

    def test_the_type_checker_sees_properties_with_no_setter(self):
        # mypy strict (the gate's types stage) refuses `pool.qty = ...`
        # because the class declares each field as a property and nothing
        # else: no annotation of the name, no setter.
        (cls,) = [n for n in ast.parse(POOL_PY.read_text(encoding='utf-8')).body
                  if isinstance(n, ast.ClassDef) and n.name == 'AcbPool']
        declared = {}
        for node in ast.walk(cls):
            if isinstance(node, ast.FunctionDef) and node.name in {s[1:] for s in SLOTS}:
                declared.setdefault(node.name, []).append(
                    [ast.unparse(d) for d in node.decorator_list])
            self.assertFalse(isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                             and '_' + node.target.id in SLOTS, ast.unparse(node))
        self.assertEqual(declared, {s[1:]: [['property']] for s in SLOTS})

    def test_the_public_methods_are_the_listed_operations(self):
        def public(cls):
            return {n for n, v in vars(cls).items()
                    if not n.startswith('_') and not isinstance(v, property)}
        self.assertEqual(public(AcbPool), POOL_OPERATIONS)
        self.assertEqual(public(PoolBook), BOOK_OPERATIONS)

    def test_no_other_file_names_a_private_slot(self):
        named = [f'{path.relative_to(ENGINE)}:{node.lineno}: {node.attr}'
                 for path, tree in _trees(ENGINE) if path != POOL_PY
                 for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute) and node.attr in SLOTS]
        self.assertEqual(named, [])

    def test_no_other_file_builds_a_pool_or_writes_a_written_lot(self):
        # A dict with both `qty` and `position_start_date` is a pool's
        # shape: the one such dict outside the class is the report's
        # inventory row, which names its symbol and is read from a pool.
        shaped, lot_writes = [], []
        for path, tree in _trees(ENGINE / 'ca'):
            if path == POOL_PY:
                continue
            for node in ast.walk(tree):
                keys = set()
                if isinstance(node, ast.Dict):
                    keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
                elif isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'dict':
                    keys = {k.arg for k in node.keywords}
                if {'qty', 'position_start_date'} <= keys:
                    shaped.append((path.name, 'symbol' in keys))
                if (isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del))
                        and isinstance(node.slice, ast.Constant)
                        and node.slice.value in ('rec_ref', 'loss_ref', 'per_unit', 'seq')):
                    lot_writes.append(f'{path.name}:{node.lineno}')
        self.assertEqual(shaped, [('taxrules.py', True)])
        self.assertEqual(lot_writes, [])

    def test_the_dict_alias_is_gone(self):
        import _engine
        from taxjson.engine.ca import pools
        self.assertFalse(hasattr(pools, '_Pool'))
        self.assertFalse(any(hasattr(m, '_Pool') for m in _engine.modules()))
        named = [str(path.relative_to(ENGINE)) for path, tree in _trees(ENGINE)
                 for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == '_Pool']
        self.assertEqual(named, [])


def _row(action, date, symbol, qty, net, **kw):
    return TaxTransaction(action=action, date=date, time='10:00:00', symbol=symbol,
                          quantity=qty, price=abs(net / qty) if qty else 0.0,
                          net_amount=net, currency='CAD', account='b', **kw)


def _spinoff_book():
    """A parent (ZZP) whose loss is denied and bought back; a spin-off of it
    in two tranches (the child ZZC bought on the first; the second ADJUST
    reads the child through the book) with filler rows between, so a
    checkpoint stands after the child's row; and two denied losses of ZZL,
    the first one's addition raising the second's, so the third pass
    re-amounts the second and walks on from a checkpoint."""
    rows = [_row('BUYSELL', '2024-01-02', 'ZZP', 100, 1000.0),
            _row('BUYSELL', '2024-01-03', 'ZZL', 100, 2000.0),
            _row('BUYSELL', '2024-01-10', 'ZZP', -100, 800.0),
            _row('BUYSELL', '2024-01-11', 'ZZL', -50, 750.0),
            _row('BUYSELL', '2024-01-20', 'ZZP', 100, 850.0),
            _row('BUYSELL', '2024-01-21', 'ZZL', 50, 750.0),
            _row('ADJUST', '2024-03-01', 'ZZP', 0, -100.0, corp_event_id='E1'),
            _row('BUYSELL', '2024-03-05', 'ZZC', 50, 100.0, corp_event_id='E1')]
    for i in range(9):
        rows.append(_row('BUYSELL', f'2024-03-{10 + i:02d}', 'QZRX',
                         10 if i % 2 == 0 else -10, 50.0))
    rows += [_row('ADJUST', '2024-03-25', 'ZZP', 0, -25.0, corp_event_id='E1'),
             _row('BUYSELL', '2024-05-01', 'ZZL', -50, 750.0),
             _row('BUYSELL', '2024-05-10', 'ZZL', 50, 750.0),
             _row('BUYSELL', '2024-06-03', 'ZZL', -100, 1600.0),
             _row('BUYSELL', '2024-06-03', 'ZZP', -100, 900.0),
             _row('BUYSELL', '2024-06-03', 'ZZC', -50, 450.0)]
    return rows


class TestTheClass(unittest.TestCase):

    def test_a_new_pool_holds_nothing(self):
        pool = AcbPool.new('CAD')
        held = {k: v for k, v in pool_fields(pool).items() if v}
        self.assertEqual(held, {'last_acq_date': '1970-01-01', 'currency': 'CAD'})
        self.assertEqual((pool.qty, pool.total_cost, pool.deferred_wash, pool.grants,
                          pool.position_start_date, pool.tainted),
                         (0.0, Decimal(0), Decimal(0), [], None, False))
        self.assertIsNot(AcbPool.new().grants, pool.grants)

    def test_the_book_makes_a_pool_once_and_keeps_its_keys_in_order(self):
        book = PoolBook()
        first = book.ensure('ZZP', 'CAD')
        self.assertIs(book.ensure('ZZP'), first)
        self.assertEqual(first.currency, 'CAD')
        book.ensure('ZZL')
        # A renamed pool is the last key, as an assignment to a new key was;
        # under its own key again (a split's short pool) it moves to the end.
        book.rename('ZZP', 'ZZN', first)
        self.assertEqual(list(book), ['ZZL', 'ZZN'])
        book.rename('ZZL', 'ZZL', book['ZZL'])
        self.assertEqual(list(book), ['ZZN', 'ZZL'])
        self.assertIs(book['ZZN'], first)

    def test_a_deep_copy_keeps_the_class_and_one_copy_per_pool(self):
        book = PoolBook()
        pool = book.ensure('ZZP')
        lot = {'units': 1.0}
        pool.grants.append(lot)
        children = {('b', 'E1'): pool}
        row = object()
        book2, children2, lot2 = copy.deepcopy((book, children, lot), {id(row): row})
        self.assertIs(type(book2), PoolBook)
        self.assertIs(type(book2['ZZP']), AcbPool)
        self.assertIsNot(book2['ZZP'], pool)
        self.assertIs(children2[('b', 'E1')], book2['ZZP'])
        self.assertIs(book2['ZZP'].grants[0], lot2)


class TestTheBooksCheckpoint(unittest.TestCase):

    def test_a_restore_leaves_no_pool_the_checkpoint_did_not_hold(self):
        book = PoolBook([('b', 'E1')])
        book.ensure('ZZP', 'CAD')
        saved = book.save({})
        book.ensure('ZZL', 'CAD')
        book.restore(saved.save({}))
        self.assertEqual(list(book), ['ZZP'])
        # The copy knows the same spin-off events as the book.
        self.assertTrue(saved.is_spinoff(('b', 'E1')))
        self.assertFalse(saved.is_spinoff(('b', 'E2')))

    def test_what_waits_for_a_spin_offs_shares_is_saved_and_put_back(self):
        # The parent gives up a fifth of 500.00 of denied losses before the
        # new shares arrive; a return of capital had taken 100.00 of them,
        # 40.00 of it a put's late premium.
        book = PoolBook([('b', 'E1')])
        parent = book.ensure('ZZP', 'CAD')
        parent.open(100.0, Decimal(1000), date='2025-01-06', settle='2025-01-06')
        parent.tally_denied(Decimal(600))
        parent.add_cost(Decimal(-500))
        parent.cut_for_roc()
        parent.late_premium(Decimal(-40))
        cut, late = parent.wash_cut, parent.cut_late
        self.assertTrue(cut > 0 and late > 0, (cut, late))
        book.give_up_to_child(('b', 'E1'), parent, Decimal('0.2'))
        saved = book.save({})
        child = book.ensure('ZZC', 'CAD')
        carried, cuts = book.child_opens(('b', 'E1'), child)
        self.assertEqual(carried, Decimal('0.2') * (parent.deferred_wash / Decimal('0.8')))
        self.assertEqual(list(cuts), [(cut * Decimal('0.2'), late)])
        # Taken once: a second arrival finds nothing; the checkpoint still
        # holds it, and a book restored from it gives it again.
        self.assertEqual(book.child_opens(('b', 'E1'), child), (Decimal(0), ()))
        book.restore(saved.save({}))
        again = book.child_opens(('b', 'E1'), book.ensure('ZZC', 'CAD'))
        self.assertEqual((again[0], list(again[1])), (carried, list(cuts)))


class TestTheCopiesTheReportsRead(unittest.TestCase):
    """A short beside a long is reported per account, and a year end as it
    stood: copies of the pool, never the walk's own (TJ-0282, TJ-1251,
    TJ-3194)."""

    def _short(self):
        pool = AcbPool.new('USD')
        pool.open(-10.0, Decimal(100), date='2025-01-06', settle='2025-01-08')
        pool.tally_denied(Decimal('0.7'))
        return pool

    def test_the_year_end_keeps_units_cost_currency_and_denied_losses(self):
        pool = self._short()
        prem, lots = {'b': {'units': 4.0}}, {'b': {'units': 4.0, 'premium': Decimal(40)}}
        snap = pool.snapshot(prem, lots)
        pool.add_cost(Decimal(5))         # the walk goes on; the copy stands
        self.assertEqual((snap.qty, snap.total_cost, snap.currency, snap.deferred_wash),
                         (-10.0, Decimal(100), 'USD', Decimal('0.7')))
        self.assertIs(snap.account_premiums, prem)
        self.assertIs(snap.account_lots, lots)
        self.assertEqual((snap.position_start_date, snap.grants), (None, []))

    def test_an_accounts_part_is_the_pool_with_its_own_units_cost_and_lots(self):
        pool = self._short()
        lot = {'units': 4.0}
        part = pool.part(-4.0, Decimal(40), Decimal('0.28'), [lot])
        self.assertEqual((part.qty, part.total_cost, part.deferred_wash, part.grants),
                         (-4.0, Decimal(40), Decimal('0.28'), [lot]))
        # Everything else is the pool's.
        self.assertEqual((part.currency, part.last_acq_date, part.position_start_date),
                         ('USD', '2025-01-06', '2025-01-06'))
        self.assertEqual((pool.qty, pool.grants), (-10.0, []))
        self.assertEqual(pool.part(-4.0, Decimal(40), Decimal(0)).grants, [])

    def test_a_netted_pool_adds_the_shorts_units_cost_and_denied_losses(self):
        long_ = AcbPool.new('USD')
        long_.open(-1.0, Decimal(5), date='2025-01-03', settle='2025-01-07')
        long_.tally_denied(Decimal('0.1'))
        net = long_.netted(self._short())
        self.assertEqual((net.qty, net.total_cost, net.deferred_wash, net.last_acq_date),
                         (-11.0, Decimal(105), Decimal('0.8'), '2025-01-03'))
        self.assertEqual(long_.qty, -1.0)


class TestAPoolKeepsItsCurrency(unittest.TestCase):

    def test_a_rename_from_a_ticker_never_traded_leaves_the_targets_currency(self):
        # ZZO has no trade in the book (its pool is made by the rename row,
        # with no currency); ZZN was bought in CAD. One pool, one currency:
        # the merged pool is still a CAD pool.
        rename = TaxTransaction(action='SPLIT', date='2024-02-01', time='09:00:00',
                                symbol='ZZO', symbol_new='ZZN', quantity=1.0,
                                net_amount=0.0, currency='CAD', account='b')
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res = CanadaTaxRules().compute_gains(
                [_row('BUYSELL', '2024-01-02', 'ZZN', 100, 1000.0), rename])
        (held,) = res['inventory']
        self.assertEqual((held['symbol'], held['qty'], held['currency']),
                         ('ZZN', 100.0, 'CAD'))


class TestARestoredCheckpointKeepsThePoolsIdentities(unittest.TestCase):

    def test_pools_of_the_class_in_the_same_book_and_children_that_are_its_pools(self):
        seen = []
        install = fold_module._WalkFold._install

        def checked(self_, it, state):
            book = it.global_pools
            install(self_, it, state)
            own = {id(p) for p in it.global_pools.values()}
            seen.append((it.global_pools is book, type(it.global_pools) is PoolBook,
                         all(type(p) is AcbPool for p in it.global_pools.values()),
                         len(book._cev_child),
                         all(id(c) in own for c in book._cev_child.values())))

        err = io.StringIO()
        with mock.patch.object(fold_module._WalkFold, '_install', checked), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            res = CanadaTaxRules().compute_gains(_spinoff_book())
        self.assertTrue(res['summary']['wash_solver_converged'], err.getvalue())
        self.assertTrue(seen, 'no pass restored a checkpoint')
        self.assertTrue(any(n for _b, _t, _p, n, _c in seen),
                        'no restored checkpoint held a spin-off child')
        for same_book, book_class, pool_class, _n, children_own in seen:
            self.assertTrue(same_book and book_class and pool_class and children_own, seen)


if __name__ == '__main__':
    unittest.main()
