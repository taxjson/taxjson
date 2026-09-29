"""Options as replacement property.

User-decided policy:
  1. share loss (LONG) + long CALL on the same underlying in the ±30d
     window, still held at day 30 → replacement. ENFORCED by the Canada
     engine (ITA s.54 para (i), 'a right to acquire'; 2026-09-29), 100
     shares per contract, the deferral added to the call's ACB; the US
     engine only warns (cross_asset);
  2. a PUT is never replacement property (a right to sell);
  3. option losses wash ONLY against the identical contract (existing
     symbol matching — pinned here);
  4. shares are NEVER replacement property for an option's loss.

The cross_asset scan is warn-only for everything else: it only adds
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

    def test_call_denies_share_loss_canada(self):
        res, err = gains(CanadaTaxRules(), self._txs())
        self.assertAlmostEqual(res['summary']['total_gain'], 0.0, places=2)
        self.assertEqual(len(res['wash_sales']), 1)
        self.assertAlmostEqual(res['wash_sales'][0]['amount'], 1000.0,
                               places=2)
        # The deferral rides on the call (the substituted property).
        inv = {h['symbol']: h for h in res['inventory']}
        self.assertAlmostEqual(inv[CALL]['total_cost'], 1300.0, places=2)
        # Enforced, so no warn-only note for it.
        self.assertEqual(res['option_replacement_warnings'], [])
        self.assertNotIn('option-replacement (warn-only', err)

    def test_call_enforced_without_cross_asset(self):
        res, _ = gains(CanadaTaxRules(), self._txs(), cross_asset=False)
        self.assertAlmostEqual(res['summary']['total_gain'], 0.0, places=2)

    def test_partial_denial_one_contract_per_100_shares(self):
        txs = [
            tx(date='2025-01-06', symbol='AAPL.US', qty=400, price=20.0,
               net=8000.0),
            tx(date='2025-03-03', symbol='AAPL.US', qty=-400, price=10.0,
               net=4000.0),
            tx(date='2025-03-20', symbol=CALL, qty=1, price=3.0, net=300.0)]
        res, _ = gains(CanadaTaxRules(), txs)
        self.assertAlmostEqual(res['wash_sales'][0]['amount'], 1000.0,
                               places=2)
        self.assertAlmostEqual(res['summary']['total_gain'], -3000.0,
                               places=2)

    def test_registered_call_makes_denial_permanent(self):
        rrsp_call = TaxTransaction(
            action='BUYSELL', date='2025-03-20', date_settle='2025-03-20',
            time='10:00:00', symbol=CALL, quantity=1, price=3.0,
            net_amount=300.0, currency='USD', account='RRSP')
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            res = CanadaTaxRules().compute_gains(
                long_loss(), sheltered_transactions=[rrsp_call])
        loss = next(t for t in res['transactions'] if t.get('is_wash_sale'))
        self.assertAlmostEqual(loss['permanently_disallowed'], 1000.0,
                               places=2)

    def test_buy_to_close_is_not_an_acquisition(self):
        txs = long_loss() + [
            tx(date='2025-02-20', symbol=CALL, qty=-1, price=3.0, net=300.0),
            tx(date='2025-03-10', symbol=CALL, qty=1, price=1.0, net=100.0)]
        res, _ = gains(CanadaTaxRules(), txs)
        self.assertEqual(res['wash_sales'], [])

    def test_ca_not_held_at_window_end(self):
        txs = self._txs() + [
            tx(date='2025-03-25', symbol=CALL, qty=-1, price=4.0, net=400.0)]
        res, _ = gains(CanadaTaxRules(), txs)
        self.assertEqual(res['wash_sales'], [])
        self.assertAlmostEqual(res['summary']['total_gain'], -900.0,
                               places=2)

    def test_fires_usa_no_held_at_end_concept(self):
        res, _ = gains(USATaxRules(), self._txs())
        warns = res['option_replacement_warnings']
        self.assertEqual(len(warns), 1)
        self.assertIsNone(warns[0]['held_at_window_end'])
        self.assertIn('1091', warns[0]['statute'])

    def test_us_numbers_never_change(self):
        for rules_cls in (USATaxRules,):
            on, _ = gains(rules_cls(), self._txs(), cross_asset=True)
            off, _ = gains(rules_cls(), self._txs(), cross_asset=False)
            self.assertAlmostEqual(on['summary']['total_gain'],
                                   off['summary']['total_gain'], places=4)
            self.assertEqual(len(on['transactions']),
                             len(off['transactions']))

    def test_us_warns_without_any_setting(self):
        res, err = gains(USATaxRules(), self._txs(), cross_asset=False)
        self.assertEqual(len(res['option_replacement_warnings']), 1)
        self.assertIn('option-replacement', err)

    def test_outside_window_allowed(self):
        res, _ = gains(CanadaTaxRules(),
                       self._txs(opt_date='2025-05-15'))   # +73d
        self.assertEqual(res['wash_sales'], [])
        self.assertEqual(res['option_replacement_warnings'], [])

    def test_put_does_not_trigger_long_loss(self):
        res, _ = gains(CanadaTaxRules(), self._txs(option=PUT))
        self.assertEqual(res['wash_sales'], [])
        self.assertEqual(res['option_replacement_warnings'], [])

    def test_different_underlying_allowed(self):
        res, _ = gains(CanadaTaxRules(),
                       self._txs(option='MSFT250620C00300000.US'))
        self.assertEqual(res['wash_sales'], [])
        self.assertEqual(res['option_replacement_warnings'], [])

    def test_other_listing_is_not_identical(self):
        # A call on the .TO listing never replaces .US shares unless
        # ticker.map joins the listings.
        res, _ = gains(CanadaTaxRules(),
                       self._txs(option='AAPL250620C00150000.TO'))
        self.assertEqual(res['wash_sales'], [])

class TestReplacementCapacity(unittest.TestCase):
    """One replacement unit backs at most one denied unit, across the
    fills of a sale and across separate losses."""

    def test_one_call_ten_fills(self):
        txs = [tx(date='2025-01-06', qty=1000, price=20.0, net=20000.0)]
        for i in range(10):
            txs.append(tx(date='2025-03-03', qty=-100, price=10.0,
                          net=1000.0, time=f'10:{i:02d}:00'))
        txs.append(tx(date='2025-03-20', symbol=CALL, qty=1, price=3.0,
                      net=300.0))
        res, _ = gains(CanadaTaxRules(), txs)
        denied = sum(w['amount'] for w in res['wash_sales'])
        self.assertAlmostEqual(denied, 1000.0, places=2)
        self.assertAlmostEqual(res['summary']['total_gain'], -9000.0,
                               places=2)

    def test_one_call_two_losses(self):
        txs = [
            tx(date='2025-01-06', qty=200, price=20.0, net=4000.0),
            tx(date='2025-03-03', qty=-100, price=10.0, net=1000.0),
            tx(date='2025-03-05', qty=-100, price=10.0, net=1000.0),
            tx(date='2025-03-20', symbol=CALL, qty=1, price=3.0, net=300.0)]
        res, _ = gains(CanadaTaxRules(), txs)
        denied = sum(w['amount'] for w in res['wash_sales'])
        self.assertAlmostEqual(denied, 1000.0, places=2)

    def test_one_share_rebuy_two_fills(self):
        txs = [
            tx(date='2025-01-06', qty=200, price=20.0, net=4000.0),
            tx(date='2025-03-03', qty=-100, price=10.0, net=1000.0),
            tx(date='2025-03-03', qty=-100, price=10.0, net=1000.0,
               time='10:01:00'),
            tx(date='2025-03-20', qty=100, price=10.0, net=1000.0)]
        res, _ = gains(CanadaTaxRules(), txs)
        denied = sum(w['amount'] for w in res['wash_sales'])
        self.assertAlmostEqual(denied, 1000.0, places=2)

    def test_call_expiring_before_day_30_is_not_held(self):
        short = 'AAPL250321C00150000.US'          # expires 03-21
        res, _ = gains(CanadaTaxRules(), long_loss() + [
            tx(date='2025-03-10', symbol=short, qty=1, price=3.0,
               net=300.0)])
        self.assertEqual(res['wash_sales'], [])


class TestPutsNeverReplace(unittest.TestCase):
    """A put is a right to SELL: never replacement property, for shares
    or for a short-cover loss."""

    def test_put_after_short_cover_loss(self):
        txs = short_loss() + [
            tx(date='2025-03-20', symbol=PUT, qty=1, price=3.0, net=300.0)]
        for rules_cls in (CanadaTaxRules, USATaxRules):
            res, _ = gains(rules_cls(), txs)
            self.assertEqual(res['option_replacement_warnings'], [],
                             rules_cls.__name__)
        res, _ = gains(CanadaTaxRules(), txs)
        self.assertEqual(res['wash_sales'], [])

    def test_call_does_not_trigger_short_loss(self):
        res, _ = gains(CanadaTaxRules(), short_loss() + [
            tx(date='2025-03-20', symbol=CALL, qty=1, price=3.0, net=300.0)])
        self.assertEqual(res['wash_sales'], [])
        self.assertEqual(res['option_replacement_warnings'], [])


class TestAsymmetry(unittest.TestCase):
    def test_different_series_never_replaces_an_option(self):
        # A loss on one call series is not deferred by buying another
        # series (strike or expiry) on the same shares.
        other = 'AAPL250718C00160000.US'
        txs = [
            tx(date='2025-01-06', symbol=CALL, qty=1, price=5.0, net=500.0),
            tx(date='2025-03-03', symbol=CALL, qty=-1, price=1.0, net=100.0),
            tx(date='2025-03-10', symbol=other, qty=1, price=2.0, net=200.0),
        ]
        res, _ = gains(CanadaTaxRules(), txs)
        self.assertEqual(res['wash_sales'], [])

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
        self.assertEqual(len(res['wash_sales']), 1)
        self.assertAlmostEqual(res['wash_sales'][0]['amount'], 1000.0,
                               places=2)
        res, _ = gains(USATaxRules(), txs)
        warns = res['option_replacement_warnings']
        self.assertEqual(len(warns), 1)
        self.assertEqual(warns[0]['loss_symbol'], 'OLD.US')
        self.assertEqual(warns[0]['option_symbol'],
                         'NEW250620C00150000.US')


class TestCrossAssetRetired(unittest.TestCase):
    def test_gains_cli_accepts_retired_flag(self):
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
            self.assertEqual(res['option_replacement_warnings'], [])
            self.assertAlmostEqual(res['summary']['total_gain'], 0.0,
                                   places=2)

    def test_setting_warns_retired(self):
        from taxjson.bin import taxjson_run as tr
        w = tr.validate_config({'settings': {'year': 2025,
                                             'cross_asset': True},
                                'accounts': {'m': {'type': 'taxable'}}})
        self.assertTrue(any('cross_asset is retired' in x for x in w))


class TestRadarAndPlanningTools(unittest.TestCase):
    """The radar and buy-check/sell-check follow the same one-way rule."""

    def _radar(self, txs, date):
        from test_audit_round2_fixes import _radar
        return _radar(txs, date)

    def test_radar_call_after_share_loss_is_violation(self):
        from test_audit_round2_fixes import _row
        txs = [
            _row("BUYSELL", "2026-01-05", "ZZZ.TO", 100, 2000.0),
            _row("BUYSELL", "2026-06-01", "ZZZ.TO", -100, 1000.0),
            _row("BUYSELL", "2026-06-10", "ZZZ270115C00010000.TO", 1, 300.0),
        ]
        out = self._radar(txs, "2026-06-15")
        line = next(l for l in out.splitlines() if l.startswith("ZZZ.TO"))
        self.assertIn("VIOLATION", line)
        self.assertIn("long call contract", line)

    def test_radar_share_buy_after_option_loss_is_not_a_trigger(self):
        from test_audit_round2_fixes import _row
        opt = "ZZZ270115C00010000.TO"
        txs = [
            _row("BUYSELL", "2026-01-05", opt, 1, 500.0),
            _row("BUYSELL", "2026-06-01", opt, -1, 100.0),
            _row("BUYSELL", "2026-06-10", "ZZZ.TO", 100, 1000.0),
        ]
        out = self._radar(txs, "2026-06-15")
        line = next(l for l in out.splitlines() if l.startswith(opt))
        self.assertNotIn("VIOLATION", line)

    def test_replacement_rows_filter(self):
        from taxjson.bin.taxjson_run import _replacement_rows
        rows = {"ZZZ.TO": {"category": "COOLING"},
                "ZZZ270115C00010000.TO": {"category": "COOLING"},
                "ZZZ270115C00012000.TO": {"category": "COOLING"},
                "ZZZ270115P00008000.TO": {"category": "COOLING"}}
        self.assertEqual(set(_replacement_rows("ZZZ.TO", rows, "buy")),
                         {"ZZZ.TO"})
        self.assertEqual(
            set(_replacement_rows("ZZZ270115C00010000.TO", rows, "buy")),
            {"ZZZ.TO", "ZZZ270115C00010000.TO"})
        self.assertEqual(
            set(_replacement_rows("ZZZ270115C00010000.TO", rows, "sell")),
            {"ZZZ270115C00010000.TO"})
        self.assertEqual(
            set(_replacement_rows("ZZZ270115P00008000.TO", rows, "buy")),
            {"ZZZ270115P00008000.TO"})


if __name__ == '__main__':
    unittest.main()
