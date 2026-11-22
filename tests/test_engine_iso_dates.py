"""TJ-2702: the engines compare date strings, so every date they read must
be written YYYY-MM-DD. The loader pads an unpadded date (canonical_date),
but a row handed to compute_gains directly kept '2025-2-11': it sorted
after '2025-11-01', the replacement fell out of the loss's window and the
loss was allowed. Every engine entry now refuses such a row up front, and
the denial scans no longer carry a whole-book fallback for a date they
cannot parse. Synthetic data only; tickers and accounts are made up."""
import contextlib
import io
import unittest

from taxjson.lib.cli_diag import InputContentError
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.engine.rows import require_computable_rows
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.tx_load import coerce_transaction_row
from tax_rules import rule
from taxjson.rules import ids as R


def _tx(date, qty, net, *, sym='SYQ.TO', settle=None, account='b',
        mult=1.0, cur='CAD', **kw):
    return TaxTransaction(action='BUYSELL', date=date,
                          date_settle=settle or date, time='10:00:00',
                          symbol=sym, quantity=qty,
                          price=abs(net / qty / mult), net_amount=net,
                          currency=cur, account=account, **kw)


def _run(engine, txs, **kw):
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return engine.compute_gains(txs, **kw)


def _losses(res):
    return [(r['date'], round(r['gain'], 2),
             round(r.get('disallowed_amount') or 0.0, 2))
            for r in res['transactions'] if r.get('qty')]


def _book(settle):
    """A 400 loss on 2025-02-03 and its replacement bought a week later,
    settling `settle`."""
    return [_tx('2025-01-03', 100, -1000.0), _tx('2025-02-03', -100, 600.0),
            _tx('2025-02-10', 100, -650.0, settle=settle)]


class TestTheEnginesRefuseADateNotWrittenIso(unittest.TestCase):

    BAD = ('2025-2-11', '2025/02/11', '2025-02-30', ' 2025-02-11')

    @rule(R.CA_SL_01)
    def test_canada_refuses_and_names_the_field(self):
        # The ISO book: the replacement denies the whole loss (s.54).
        self.assertEqual(_losses(_run(CanadaTaxRules(), _book('2025-02-11'))),
                         [('2025-02-03', 0.0, 400.0)])
        for bad in self.BAD:
            with self.subTest(bad=bad), \
                    self.assertRaises(InputContentError) as cm:
                _run(CanadaTaxRules(), _book(bad))
            self.assertIn(f"date_settle={bad!r}", str(cm.exception))
            self.assertIn("YYYY-MM-DD", str(cm.exception))

    @rule(R.CA_SL_01)
    def test_a_context_book_row_is_refused_too(self):
        shel = [_tx('2025-2-10', 100, -650.0, account='tfsa',
                    settle='2025-02-11')]
        with self.assertRaises(InputContentError) as cm:
            _run(CanadaTaxRules(), _book('2025-02-11')[:2],
                 sheltered_transactions=shel)
        self.assertIn("date='2025-2-10'", str(cm.exception))

    @rule(R.US_WASH_01)
    def test_the_us_engine_refuses_too(self):
        def book(date):
            return [_tx('2025-01-03', 100, -1000.0, sym='QZRX', cur='USD'),
                    _tx('2025-02-03', -100, 600.0, sym='QZRX', cur='USD'),
                    _tx(date, 100, -650.0, sym='QZRX', cur='USD')]
        res = _run(USATaxRules(), book('2025-02-10'))
        self.assertEqual(_losses(res), [('2025-02-03', 0.0, 400.0)])
        with self.assertRaises(InputContentError) as cm:
            _run(USATaxRules(), book('2025-2-10'))
        self.assertIn("date='2025-2-10'", str(cm.exception))

    def test_every_date_field_is_held_to_it(self):
        for fld in ('lot_date', 'record_date', 'ex_date'):
            row = _tx('2025-02-10', 100, -650.0, **{fld: '2025-2-1'})
            with self.subTest(fld=fld), \
                    self.assertRaises(InputContentError) as cm:
                require_computable_rows([row])
            self.assertIn(f"{fld}='2025-2-1'", str(cm.exception))
        # A date object is refused too: the engines compare strings.
        import datetime
        row = _tx('2025-02-10', 100, -650.0)
        row.date_settle = datetime.date(2025, 2, 11)
        with self.assertRaises(InputContentError) as cm:
            require_computable_rows([row])
        self.assertIn("date_settle=datetime.date(2025, 2, 11)",
                      str(cm.exception))
        # An empty optional date stays legal.
        require_computable_rows([_tx('2025-02-10', 100, -650.0,
                                     lot_date='')])

    @rule(R.CA_SL_01)
    def test_the_loader_still_pads_a_file_row(self):
        row = coerce_transaction_row(
            {'action': 'BUYSELL', 'date': '2025-2-10', 'date_settle':
             '2025-2-11', 'time': '10:00:00', 'symbol': 'SYQ.TO',
             'quantity': 100, 'price': 6.5, 'net_amount': -650.0,
             'currency': 'CAD', 'account': 'b'}, 0, 'inputs/b.json')
        self.assertEqual((row.date, row.date_settle),
                         ('2025-02-10', '2025-02-11'))
        res = _run(CanadaTaxRules(), _book('2025-02-11')[:2] + [row])
        self.assertEqual(_losses(res), [('2025-02-03', 0.0, 400.0)])


class TestALongCallWithABadDateIsRefusedBeforeTheScan(unittest.TestCase):
    """`_sl_trigger_scan`'s long-call scan read the whole book when a call's
    date did not parse (its index then gave up) and raised on that row
    there. The row is now refused before any scan; spelled ISO, the held
    calls deny the loss (CA-SL-05)."""

    C1, C2 = 'SYQ251219C00010000.TO', 'SYQ251219C00012000.TO'

    @rule(R.CA_SL_05)
    def test_refused_up_front_and_denied_when_iso(self):
        def book(settle):
            return [_tx('2025-01-03', 100, -1000.0),
                    _tx('2025-02-03', -100, 600.0),           # loss 400
                    _tx('2025-02-07', 1, -50.0, sym=self.C1, mult=100),
                    _tx('2025-02-10', 1, -40.0, sym=self.C2, mult=100,
                        settle=settle)]
        with self.assertRaises(InputContentError) as cm:
            _run(CanadaTaxRules(), book('2025/02/11'))
        self.assertIn("date_settle='2025/02/11'", str(cm.exception))
        res = _run(CanadaTaxRules(), book('2025-02-11'))
        self.assertEqual([r for r in _losses(res) if r[0] == '2025-02-03'],
                         [('2025-02-03', 0.0, 400.0)])


if __name__ == '__main__':
    unittest.main()
