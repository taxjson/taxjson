"""Option-as-replacement wash detection (WARN-ONLY, gated on cross_asset).

User-decided policy:
  1. share loss (LONG) + long CALL on the same underlying in the ±30d
     window → replacement (warn);
  2. short-closing loss + long PUT → replacement (warn);
  3. option losses wash ONLY against the identical contract (existing
     symbol matching — pinned here);
  4. shares are NEVER replacement property for an option's loss.

Numbers must never change; the scan only adds
`option_replacement_warnings` + stderr notes.
"""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from taxjson.lib.core import (
    CanadaTaxRules,
    TaxTransaction,
    USATaxRules,
    parse_option_right,
    parse_option_strike,
)

CALL = 'AAPL250620C00150000.US'
PUT = 'AAPL250620P00140000.US'


def tx(action='BUYSELL', date='2025-03-03', symbol='AAPL.US', qty=0.0,
       price=0.0, net=0.0, symbol_new='', time='10:00:00'):
    return TaxTransaction(action=action, date=date, date_settle=date,
                          time=time, symbol=symbol, quantity=qty,
                          price=price, net_amount=net, currency='USD',
                          symbol_new=symbol_new)


def long_loss(symbol='AAPL.US'):
    """Buy 100 @ 20, sell 100 @ 10 → -1000 loss realized 2025-03-03."""
    return [
        tx(date='2025-01-06', symbol=symbol, qty=100, price=20.0, net=2000.0),
        tx(date='2025-03-03', symbol=symbol, qty=-100, price=10.0, net=1000.0),
    ]


def short_loss(symbol='AAPL.US'):
    """Short 100 @ 10, cover @ 15 → -500 loss realized 2025-03-03."""
    return [
        tx(date='2025-01-06', symbol=symbol, qty=-100, price=10.0, net=1000.0),
        tx(date='2025-03-03', symbol=symbol, qty=100, price=15.0, net=1500.0),
    ]


def gains(rules, txs, cross_asset=True):
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        res = rules.compute_gains(txs, cross_asset=cross_asset)
    return res, err.getvalue()


class TestParseOptionRight(unittest.TestCase):
    def test_rights(self):
        self.assertEqual(parse_option_right(CALL), 'C')
        self.assertEqual(parse_option_right(PUT), 'P')
        self.assertIsNone(parse_option_right('AAPL.US'))
        self.assertIsNone(parse_option_right(''))

    def test_strike(self):
        # OCC strike block is the price x 1000.
        self.assertEqual(parse_option_strike(CALL), 150.0)
        self.assertEqual(parse_option_strike(PUT), 140.0)
        self.assertEqual(
            parse_option_strike('BNS260116C00082500.TO'), 82.5)
        self.assertIsNone(parse_option_strike('AAPL.US'))
        self.assertIsNone(parse_option_strike(''))


class TestRuleOneCallVsShareLoss(unittest.TestCase):
    def _txs(self, option=CALL, opt_date='2025-03-20', opt_qty=1):
        return long_loss() + [
            tx(date=opt_date, symbol=option, qty=opt_qty, price=3.0,
               net=300.0)]

    def test_fires_canada(self):
        res, err = gains(CanadaTaxRules(), self._txs())
        warns = res['option_replacement_warnings']
        self.assertEqual(len(warns), 1)
        w = warns[0]
        self.assertEqual(w['rule'], 'call_vs_share_loss')
        self.assertEqual(w['loss_symbol'], 'AAPL.US')
        self.assertEqual(w['option_symbol'], CALL)
        self.assertAlmostEqual(w['loss_amount'], -1000.0, places=2)
        self.assertTrue(w['held_at_window_end'])
        self.assertIn('s.54', w['statute'])
        self.assertIn('option-replacement (warn-only', err)

    def test_fires_usa_no_held_at_end_concept(self):
        res, _ = gains(USATaxRules(), self._txs())
        warns = res['option_replacement_warnings']
        self.assertEqual(len(warns), 1)
        self.assertIsNone(warns[0]['held_at_window_end'])
        self.assertIn('1091', warns[0]['statute'])

    def test_numbers_never_change(self):
        for rules_cls in (CanadaTaxRules, USATaxRules):
            on, _ = gains(rules_cls(), self._txs(), cross_asset=True)
            off, _ = gains(rules_cls(), self._txs(), cross_asset=False)
            self.assertAlmostEqual(on['summary']['total_gain'],
                                   off['summary']['total_gain'], places=4)
            self.assertEqual(len(on['transactions']),
                             len(off['transactions']))

    def test_off_by_default_and_silent(self):
        res, err = gains(CanadaTaxRules(), self._txs(), cross_asset=False)
        self.assertEqual(res['option_replacement_warnings'], [])
        self.assertNotIn('option-replacement', err)

    def test_outside_window_silent(self):
        res, _ = gains(CanadaTaxRules(),
                       self._txs(opt_date='2025-05-15'))   # +73d
        self.assertEqual(res['option_replacement_warnings'], [])

    def test_put_does_not_trigger_long_loss(self):
        res, _ = gains(CanadaTaxRules(), self._txs(option=PUT))
        self.assertEqual(res['option_replacement_warnings'], [])

    def test_different_underlying_silent(self):
        res, _ = gains(CanadaTaxRules(),
                       self._txs(option='MSFT250620C00300000.US'))
        self.assertEqual(res['option_replacement_warnings'], [])

    def test_ca_not_held_at_window_end(self):
        txs = self._txs() + [
            tx(date='2025-03-25', symbol=CALL, qty=-1, price=4.0, net=400.0)]
        res, err = gains(CanadaTaxRules(), txs)
        warns = res['option_replacement_warnings']
        self.assertEqual(len(warns), 1)
        self.assertFalse(warns[0]['held_at_window_end'])
        self.assertIn('NOT held at window end', err)


class TestRuleTwoPutVsShortLoss(unittest.TestCase):
    def _txs(self, option=PUT):
        return short_loss() + [
            tx(date='2025-03-20', symbol=option, qty=1, price=3.0, net=300.0)]

    def test_fires_both_engines(self):
        for rules_cls in (CanadaTaxRules, USATaxRules):
            res, _ = gains(rules_cls(), self._txs())
            warns = res['option_replacement_warnings']
            self.assertEqual(len(warns), 1, rules_cls.__name__)
            self.assertEqual(warns[0]['rule'], 'put_vs_short_loss')

    def test_call_does_not_trigger_short_loss(self):
        res, _ = gains(CanadaTaxRules(), self._txs(option=CALL))
        self.assertEqual(res['option_replacement_warnings'], [])


class TestAsymmetry(unittest.TestCase):
    def test_share_buy_never_triggers_option_loss(self):
        # Rule 4: lose money on a call, buy the shares in-window → silent.
        txs = [
            tx(date='2025-01-06', symbol=CALL, qty=1, price=5.0, net=500.0),
            tx(date='2025-03-03', symbol=CALL, qty=-1, price=1.0, net=100.0),
            tx(date='2025-03-10', symbol='AAPL.US', qty=100, price=10.0,
               net=1000.0),
        ]
        for rules_cls in (CanadaTaxRules, USATaxRules):
            res, _ = gains(rules_cls(), txs)
            self.assertEqual(res['option_replacement_warnings'], [],
                             rules_cls.__name__)

    def test_identical_contract_wash_still_enforced(self):
        # Rule 3 pin: the EXISTING same-symbol machinery still disallows
        # an identical-contract option repurchase — unaffected by the
        # warn-only scan.
        txs = [
            tx(date='2025-01-06', symbol=CALL, qty=1, price=5.0, net=500.0),
            tx(date='2025-03-03', symbol=CALL, qty=-1, price=1.0, net=100.0),
            tx(date='2025-03-10', symbol=CALL, qty=1, price=2.0, net=200.0),
        ]
        res, _ = gains(CanadaTaxRules(), txs)
        self.assertTrue(res['wash_sales'])
        self.assertAlmostEqual(res['wash_sales'][0]['amount'], 400.0,
                               places=2)
        # And no cross-asset warning was fabricated for it.
        self.assertEqual(res['option_replacement_warnings'], [])


class TestRenameBridge(unittest.TestCase):
    def test_call_on_renamed_underlying_matches(self):
        txs = [
            tx(date='2025-01-06', symbol='OLD.US', qty=100, price=20.0,
               net=2000.0),
            tx(date='2025-03-03', symbol='OLD.US', qty=-100, price=10.0,
               net=1000.0),
            tx(action='SPLIT', date='2025-03-05', symbol='OLD.US',
               qty=1.0, symbol_new='NEW.US'),
            tx(date='2025-03-20', symbol='NEW250620C00150000.US', qty=1,
               price=3.0, net=300.0),
        ]
        res, _ = gains(CanadaTaxRules(), txs)
        warns = res['option_replacement_warnings']
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0]['loss_symbol'], 'OLD.US')
        self.assertEqual(warns[0]['option_symbol'],
                         'NEW250620C00150000.US')


class TestCliFlag(unittest.TestCase):
    def test_gains_cli_threads_cross_asset(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        txs = long_loss() + [
            tx(date='2025-03-20', symbol=CALL, qty=1, price=3.0, net=300.0)]
        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / 'base.json'
            inp.write_text(json.dumps(
                {'transactions': [t.to_dict() for t in txs]}))
            out = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_gains',
                 '--country', 'canada', '--taxable', '--cross-asset',
                 str(inp)],
                capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            res = json.loads(out.stdout)
            self.assertEqual(len(res['option_replacement_warnings']), 1)
            self.assertIn('option-replacement (warn-only', out.stderr)


if __name__ == '__main__':
    unittest.main()
