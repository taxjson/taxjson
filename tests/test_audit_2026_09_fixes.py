"""Regression pins for the 2026-09 deep-audit fixes.

Each test is a distilled repro of a CONFIRMED wrong-number or
wrong-advice finding; the fixed behavior is asserted, so none of them
can quietly return.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.lib.core import TaxTransaction, get_tax_rules

REPO_ROOT = Path(__file__).resolve().parent.parent


def _t(**kw):
    d = dict(action='BUYSELL', date='2026-01-05', time='09:30:00',
             symbol='Q.TO', quantity=100.0, currency='CAD',
             net_amount=1000.0, account='A')
    d.update(kw)
    return TaxTransaction(**d)


class TestEngineShortAdjustSign(unittest.TestCase):
    """Blended book, one symbol: account A short, account B long. A
    short-side superficial loss's deferral ADJUST used to land on the
    (net LONG) global pool with a NEGATIVE sign — reducing long ACB,
    the opposite of a deferral — and the stale DISALLOW from a
    flipped-to-gain iteration then reported a denied 'loss' on a raw
    GAIN. Conservation must hold: realized + still-deferred = no-wash
    baseline."""

    def _book(self):
        return [
            # B: plain long position (the global pool's direction).
            _t(account='B', date='2025-01-02', quantity=200,
               net_amount=2000.0),
            # A: short round trip at a loss, replacement short opens
            # in-window.
            _t(account='A', date='2025-01-03', quantity=-100,
               net_amount=800.0),
            _t(account='A', date='2025-01-10', quantity=100,
               net_amount=1000.0),          # cover at a 200 loss
            _t(account='A', date='2025-01-20', quantity=-100,
               net_amount=700.0),           # replacement short
            _t(account='A', date='2025-02-03', quantity=100,
               net_amount=1000.0),          # cover again (loss)
            _t(account='A', date='2025-02-15', quantity=-200,
               net_amount=1200.0),
        ]

    def test_conserves_against_no_wash_baseline(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            wash = get_tax_rules('canada').compute_gains(self._book())
            base = get_tax_rules('canada').compute_gains(
                self._book(), detect_wash_sales=False)
        base_total = sum(g['gain'] for g in base['transactions']
                         if g.get('qty') and 'gain' in g)
        wash_total = sum(g['gain'] for g in wash['transactions']
                         if g.get('qty') and 'gain' in g)
        deferred = sum(float(r.get('deferred_wash') or 0.0)
                       for r in wash.get('inventory') or [])
        self.assertAlmostEqual(wash_total - deferred, base_total,
                               places=2)

    def test_no_disallowance_on_a_raw_gain(self):
        with redirect_stderr(io.StringIO()):
            wash = get_tax_rules('canada').compute_gains(self._book())
        for g in wash['transactions']:
            if not g.get('qty') or 'gain' not in g:
                continue
            raw = float(g.get('raw_gain', g['gain']) or 0)
            dis = float(g.get('disallowed_amount') or 0)
            if dis > 0.001:
                self.assertLess(raw, 0.001,
                                f"disallowance attached to a raw "
                                f"gain: {g}")


class TestEngineEmptyTimeNoCrash(unittest.TestCase):
    def test_loss_row_with_empty_time(self):
        txs = [_t(date='2026-01-05', quantity=100),
               _t(date='2026-02-01', quantity=100, net_amount=900.0),
               _t(date='2026-02-10', quantity=-100, net_amount=800.0,
                  time='')]
        with redirect_stderr(io.StringIO()):
            r = get_tax_rules('canada').compute_gains(txs)
        # Used to raise ValueError out of _mk_adjust; now the wash
        # sale computes.
        self.assertEqual(len(r.get('wash_sales') or []), 1)


class TestPairDropClustering(unittest.TestCase):
    def test_distant_events_never_cancel(self):
        # In-kind contribution OUT (Feb, a deemed disposition) and an
        # unrelated transfer IN (Dec) used to cancel silently,
        # bypassing the taxable hard error.
        from taxjson.lib.pipeline import (_handle_transfers,
                                          TransferValidationError)
        txs = [_t(date='2023-05-01', quantity=100, net_amount=5000.0,
                  account='margin'),
               _t(action='TRANSFER', date='2024-02-10', quantity=-100,
                  net_amount=8000.0, account='margin'),
               _t(action='TRANSFER', date='2024-12-05', quantity=100,
                  net_amount=2000.0, account='margin')]
        with self.assertRaises(TransferValidationError):
            with redirect_stderr(io.StringIO()):
                _handle_transfers(txs, [], taxable=True)

    def test_split_between_legs_blocks_the_drop(self):
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        txs = [_t(action='TRANSFER', date='2026-03-01', quantity=-100),
               TaxTransaction(action='SPLIT', date='2026-03-05',
                              symbol='Q.TO', quantity=2.0, account='A'),
               _t(action='TRANSFER', date='2026-03-10', quantity=100)]
        out, dropped = _drop_self_cancelling_transfers(txs)
        self.assertEqual(dropped, [])
        self.assertEqual(
            sum(1 for t in out if t.action == 'TRANSFER'), 2)

    def test_two_separate_moves_both_cancel(self):
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        txs = [_t(action='TRANSFER', date='2026-03-01', quantity=-50),
               _t(action='TRANSFER', date='2026-03-03', quantity=50),
               _t(action='TRANSFER', date='2026-07-01', quantity=-50),
               _t(action='TRANSFER', date='2026-07-02', quantity=50)]
        out, _ = _drop_self_cancelling_transfers(txs)
        self.assertEqual([t for t in out if t.action == 'TRANSFER'], [])


class TestShelteredContributionIsATrigger(unittest.TestCase):
    def _run_with_sheltered(self, spelling, shel_date):
        from taxjson.lib.pipeline import prepare_books
        main = [_t(account='margin', date='2024-05-01', quantity=100,
                   net_amount=10000.0),
                _t(account='margin', date='2024-06-10', quantity=-100,
                   net_amount=8000.0)]
        shel = [_t(action=spelling, account='rrsp', date=shel_date,
                   quantity=100, net_amount=8100.0)]
        with redirect_stderr(io.StringIO()):
            m, sh, af, _ = prepare_books(main, shel, taxable=False,
                                         phantom_hint=False)
            return get_tax_rules('canada').compute_gains(
                m, sheltered_transactions=sh,
                affiliated_transactions=af)

    def test_buysell_contribution_in_window_denies(self):
        # The canonical permanently-denied superficial loss, spelled
        # explicitly: taxable loss sale, sheltered BUYSELL acquisition
        # of identical shares inside the window.
        r = self._run_with_sheltered('BUYSELL', '2024-06-20')
        self.assertGreater(sum(
            float(w.get('disallowed_amount') or 0)
            for w in r.get('wash_sales') or []), 0)

    def test_transfer_in_window_demands_a_declaration(self):
        # A TRANSFER's date is a broker ARRIVAL date; if it lands in a
        # trigger window the engine refuses to guess whether it was a
        # contribution (a real acquisition — deny) or a custody move
        # (not an acquisition — allow). The old strip silently chose
        # "allow"; silently choosing "deny" would be wrong the other
        # way. The error names both resolutions.
        from taxjson.lib.core import AmbiguousTransferDateError
        with self.assertRaises(AmbiguousTransferDateError) as cm:
            self._run_with_sheltered('TRANSFER', '2024-06-20')
        self.assertIn('ARRIVAL', str(cm.exception))
        self.assertIn('custody', str(cm.exception))

    def test_transfer_outside_window_counts_only_as_balance(self):
        # An OLD sheltered transfer-in never triggers (s.54(a) needs an
        # in-window acquisition), but it DOES satisfy the still-held
        # limb: with an in-window taxable rebuy that is itself gone by
        # day +30, the sheltered balance is the only thing keeping the
        # denial alive — the old strip zeroed it.
        from taxjson.lib.pipeline import prepare_books
        main = [_t(account='margin', date='2024-05-01', quantity=100,
                   net_amount=10000.0),
                _t(account='margin', date='2024-06-10', quantity=-100,
                   net_amount=8000.0),          # loss 2,000 (20/sh)
                _t(account='margin', date='2024-06-25', quantity=30,
                   net_amount=2400.0),          # in-window trigger
                _t(account='margin', date='2024-07-05', quantity=-30,
                   net_amount=3200.0)]          # gone before +30, at a
                                                # gain even after the
                                                # 600 deferral lands
        shel = [_t(action='TRANSFER', account='rrsp',
                   date='2024-04-01', quantity=100,
                   net_amount=8100.0)]          # balance only
        with redirect_stderr(io.StringIO()):
            m, sh, af, _ = prepare_books(main, shel, taxable=False,
                                         phantom_hint=False)
            r = get_tax_rules('canada').compute_gains(
                m, sheltered_transactions=sh,
                affiliated_transactions=af)
        denied = sum(float(w.get('disallowed_amount') or 0)
                     for w in r.get('wash_sales') or [])
        # min(100 sold, 30 acquired in window, 100 still held) = 30
        # shares x 20/sh = 600.
        self.assertAlmostEqual(denied, 600.0, places=2)

    def test_old_sheltered_balance_alone_denies_nothing(self):
        # The user's rule, pinned: no acquisition in the +/-30 window
        # means no superficial loss, no matter what an affiliated
        # account has held for months.
        from taxjson.lib.pipeline import prepare_books
        main = [_t(account='margin', date='2024-05-01', quantity=100,
                   net_amount=10000.0),
                _t(account='margin', date='2024-06-10', quantity=-100,
                   net_amount=8000.0)]
        shel = [_t(action='TRANSFER', account='rrsp',
                   date='2024-04-01', quantity=100,
                   net_amount=8100.0)]
        with redirect_stderr(io.StringIO()):
            m, sh, af, _ = prepare_books(main, shel, taxable=False,
                                         phantom_hint=False)
            r = get_tax_rules('canada').compute_gains(
                m, sheltered_transactions=sh,
                affiliated_transactions=af)
        self.assertEqual(sum(
            float(w.get('disallowed_amount') or 0)
            for w in r.get('wash_sales') or []), 0)

    def test_registered_to_registered_move_is_not_a_trigger(self):
        from taxjson.lib.pipeline import prepare_books
        shel = [_t(action='TRANSFER', account='rrsp',
                   date='2024-06-18', quantity=-100, net_amount=8000.0),
                _t(action='TRANSFER', account='rrsp2',
                   date='2024-06-20', quantity=100, net_amount=8000.0)]
        with redirect_stderr(io.StringIO()):
            _, sh, _, _ = prepare_books([], shel, taxable=False,
                                        phantom_hint=False)
        self.assertEqual(sh, [])


class TestEstimateConfigGuard(unittest.TestCase):
    def test_negative_config_losses_die(self):
        # The config path skipped the CLI's sign/finiteness guard —
        # a "-10,000 carryover" fabricated taxable gains silently.
        from argparse import Namespace
        from taxjson.bin.taxjson_run import _estimate_inputs
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'taxjson.toml').write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.m]\ntype = "taxable"\n'
                '[estimate]\nother_losses = -10000\n')
            args = Namespace(other_income=None, other_losses=None)
            with self.assertRaises(SystemExit) as cm:
                with redirect_stderr(io.StringIO()):
                    _estimate_inputs(root, args)
        self.assertIn('non-negative', str(cm.exception))


class TestIbCancellations(unittest.TestCase):
    def _parse(self, text):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.NamedTemporaryFile('w', suffix='.csv',
                                         delete=False) as f:
            f.write(text)
            name = f.name
        try:
            with redirect_stderr(io.StringIO()):
                return IbBrokerage().parse_file(Path(name))
        finally:
            os.unlink(name)

    def test_cancelled_trade_pair_nets_to_zero(self):
        txs = self._parse(
            'Trades,Header,Asset Category,Currency,Symbol,Date/Time,'
            'Quantity,T. Price,Proceeds,Comm/Fee,Code\n'
            'Trades,Data,Stocks,USD,MSFT,"2026-01-05, 09:30:00",'
            '100,10,-1000,-1,O\n'
            'Trades,Data,Stocks,USD,MSFT,"2026-01-06, 09:30:00",'
            '-100,10,1000,1,Ca\n')
        buys = [t for t in txs if t['action'] == 'BUYSELL']
        self.assertEqual(len(buys), 2)
        # Original cost == cancellation proceeds: the phantom round
        # trip nets exactly zero (was: a 2x-commission phantom loss).
        self.assertAlmostEqual(buys[0]['net_amount'],
                               buys[1]['net_amount'], places=6)

    def test_order_and_trade_levels_emit_once(self):
        txs = self._parse(
            'Trades,Header,DataDiscriminator,Asset Category,Currency,'
            'Symbol,Date/Time,Quantity,T. Price,Proceeds,Comm/Fee,Code\n'
            'Trades,Data,Order,Stocks,USD,MSFT,"2026-01-05, 09:30:00",'
            '-50,400,20000,-1,\n'
            'Trades,Data,Trade,Stocks,USD,MSFT,"2026-01-05, 09:30:00",'
            '-50,400,20000,-1,\n'
            'Trades,Data,ClosedLot,Stocks,USD,MSFT,'
            '"2025-06-01, 09:30:00",50,300,,,\n')
        self.assertEqual(
            sum(1 for t in txs if t['action'] == 'BUYSELL'), 1)

    def test_restated_spinoff_emits_once(self):
        txs = self._parse(
            'Corporate Actions,Header,Asset Category,Currency,'
            'Report Date,Date/Time,Description,Quantity,Proceeds,'
            'Value,Realized P/L,Code\n'
            'Corporate Actions,Data,Stocks,USD,2025-06-02,'
            '"2025-06-01, 20:25:00","WBD(US9344231041) Spinoff  1 for '
            '10 (VNT, VONTIER CORP, US92917K1043)",10,0,250.0,0,\n'
            'Corporate Actions,Data,Stocks,USD,2025-06-03,'
            '"2025-06-01, 20:25:00","WBD(US9344231041) Spinoff  1 for '
            '10 (VNT, VONTIER CORP, US92917K1043)",-10,0,-250.0,0,Ca\n'
            'Corporate Actions,Data,Stocks,USD,2025-06-03,'
            '"2025-06-01, 20:25:00","WBD(US9344231041) Spinoff  1 for '
            '10 (VNT, VONTIER CORP, US92917K1043)",10,0,250.0,0,\n')
        self.assertEqual(
            sum(1 for t in txs if t['action'] == 'DIVIDEND'), 1)
        self.assertEqual(
            sum(1 for t in txs if t['action'] == 'BUYSELL'), 1)


class TestVentureSuffix(unittest.TestCase):
    def test_vn_normalizes_to_v(self):
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        b = QuestradeBrokerage()
        self.assertEqual(b.apply_currency_suffix('ABC.VN', 'CAD'),
                         'ABC.V')
        self.assertEqual(b.apply_currency_suffix('SHOP.TO', 'CAD'),
                         'SHOP.TO')


class TestHarvestScheduleSemantics(unittest.TestCase):
    def test_violation_loss_is_claimable_now(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        rows = [{'verdict': 'LOSS', 'unrealized': -1000.0,
                 'radar': {'category': 'VIOLATION',
                           'clears_at': '2099-01-01'}}]
        out = _recovery_schedule(rows)
        self.assertAlmostEqual(out['now'], 1000.0)


class TestExitableWithShelteredHolding(unittest.TestCase):
    def test_full_exit_advice_carries_the_denied_portion(self):
        # The real FFH.TO shape: taxable in-window buys + an OLD
        # sheltered holding. "Selling the FULL position at a loss is
        # fine now" was wrong — CRA's min(sold, acquired, still-held)
        # keeps up to the sheltered balance denied PERMANENTLY.
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            tax = Path(td) / 'tax.json'
            shl = Path(td) / 'shl.json'
            tax.write_text(json.dumps({'transactions': [
                {'action': 'BUYSELL', 'date': '2026-01-02',
                 'time': '09:30:00', 'symbol': 'FFH.TO',
                 'quantity': 20, 'net_amount': 47000.0,
                 'currency': 'CAD', 'account': 'margin'},
                {'action': 'BUYSELL', 'date': '2026-01-22',
                 'time': '09:30:00', 'symbol': 'FFH.TO',
                 'quantity': 20, 'net_amount': 46000.0,
                 'currency': 'CAD', 'account': 'margin'}]}))
            shl.write_text(json.dumps({'transactions': [
                {'action': 'BUYSELL', 'date': '2025-09-26',
                 'time': '09:30:00', 'symbol': 'FFH.TO',
                 'quantity': 10, 'net_amount': 23000.0,
                 'currency': 'CAD', 'account': 'resp'}]}))
            r = subprocess.run(
                [sys.executable, '-m',
                 'taxjson.bin.taxjson_wash_radar',
                 '--taxable', str(tax), '--sheltered', str(shl),
                 '--date', '2026-01-29'],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('EXITABLE', r.stdout)
        self.assertIn('PERMANENTLY denied', r.stdout)
        self.assertNotIn('loss is fine now', r.stdout)


class TestWatchAdvisoryDiff(unittest.TestCase):
    def test_same_category_advisory_change_is_news(self):
        from taxjson.bin.taxjson_watch import diff_radar
        prev = {'X.TO': {'category': 'VIOLATION',
                         'advisory': 'Sell 100 by 2026-09-20.',
                         'clears_at': '2026-09-20'}}
        cur = {'X.TO': {'category': 'VIOLATION',
                        'advisory': 'Sell 200 by 2026-09-20.',
                        'clears_at': '2026-09-20'}}
        changes = diff_radar(prev, cur)
        self.assertEqual([c['kind'] for c in changes],
                         ['advisory_changed'])


class TestBalanceOnSplitDedup(unittest.TestCase):
    def test_duplicated_broker_split_applies_once(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [{'action': 'BUYSELL', 'date': '2026-01-05',
                'symbol': 'A.TO', 'quantity': 100.0, 'account': 'm'},
               {'action': 'SPLIT', 'date': '2026-02-01',
                'symbol': 'A.TO', 'quantity': 2.0, 'account': 'm',
                'id': 'broker1'},
               {'action': 'SPLIT', 'date': '2026-02-01',
                'symbol': 'A.TO', 'quantity': 2.0, 'account': 'm',
                'id': 'broker2'}]
        self.assertAlmostEqual(
            balance_on(txs, 'A.TO', '2026-03-01'), 200.0)


if __name__ == '__main__':
    unittest.main()


class TestFullAuditRound2Fixes(unittest.TestCase):
    """Pins for the 2026-09-04 full-audit fixes (reporting, pipeline,
    contracts). Engine pins live in test_engine_invariants."""

    def test_rbc_demo_detects_as_rbc(self):
        # "Account Number" preamble routed real RBC files to the
        # Webull parser -> silently empty books.
        from taxjson.bin.taxjson_detect_brokerage import (
            detect_brokerage)
        p = REPO_ROOT / "examples" / "rbc_direct_demo.csv"
        self.assertEqual(detect_brokerage(p), "rbc_direct")

    def test_webull_demo_still_webull(self):
        from taxjson.bin.taxjson_detect_brokerage import (
            detect_brokerage)
        p = REPO_ROOT / "examples" / "webull_demo.csv"
        self.assertEqual(detect_brokerage(p), "webull")

    def test_readme_adjust_example_parses(self):
        # The documented ROC line crashed the parser (wrong field
        # order in the docs). Whatever the README shows must parse.
        import re
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        m = re.search(r"^\s*(ADJUST \S.*?)(?:#.*)?$", readme,
                      re.MULTILINE)
        self.assertIsNotNone(m, "README lost its ADJUST example")
        row = parse_tt_line(m.group(1).strip())
        self.assertEqual(row["action"], "ADJUST")
        self.assertAlmostEqual(row["net_amount"], -184.23, places=2)

    def test_reconcile_slips_short_swap(self):
        # Engine short convention needs the proceeds/cost SWAP; sign
        # stripping alone mismatched by exactly the gain.
        from taxjson.bin.taxjson_reconcile_slips import load_computed
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [
                {"symbol": "XYZ.TO", "date": "2026-02-10",
                 "date_settle": "2026-02-11", "qty": 100.0,
                 "gain": 980.0, "direction": "SHORT",
                 "proceeds": -4010.0, "cost": -4990.0}]}))
            rec = load_computed([g], 2026)["XYZ"]
        self.assertAlmostEqual(rec["proceeds_net"], 4990.0, places=2)
        self.assertAlmostEqual(rec["cost"], 4010.0, places=2)

    def test_reconcile_slips_sees_tainted_rows(self):
        from taxjson.bin.taxjson_reconcile_slips import load_computed
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({
                "transactions": [],
                "manual_reporting_required": [
                    {"symbol": "GHOST.TO", "date": "2026-03-01",
                     "date_settle": "2026-03-02", "qty": 10.0,
                     "gain": 0.0, "proceeds": 500.0, "cost": 0.0}]}))
            out = load_computed([g], 2026)
        self.assertIn("GHOST", out)
        self.assertEqual(out["GHOST"]["tainted_rows"], 1)

    def test_report_income_uses_base_book(self):
        from taxjson.lib.report_model import build_account_report
        gains_doc = {"transactions": [
            {"action": "DIVIDEND", "date": "2026-01-10",
             "symbol": "A.TO", "dividend": 100.0, "qty": 0}],
            "summary": {}}
        base_rows = [
            {"action": "DIVIDEND", "date": "2026-01-10",
             "symbol": "A.TO", "net_amount": 100.0,
             "gross_amount": 100.0, "currency": "CAD",
             "account": "m"},
            {"action": "TAX", "date": "2026-01-10", "symbol": "A.TO",
             "net_amount": 15.0, "currency": "CAD", "account": "m"}]
        rep = build_account_report(gains_doc, "m", basis="pre-wash",
                                   base_transactions=base_rows)
        inc = rep["income"]
        total = json.dumps(inc)
        self.assertIn("100", total,
                      f"income section still empty: {inc}")

    def test_filed_phantoms_read_from_root(self):
        src = (REPO_ROOT / "src/taxjson/bin/taxjson_filed.py"
               ).read_text(encoding="utf-8")
        self.assertNotIn('cache / "phantoms.json"', src)
        self.assertIn('cache.parent / "phantoms.json"', src)

    def test_ambiguous_error_handled_by_explain(self):
        src = (REPO_ROOT / "src/taxjson/bin/taxjson_explain.py"
               ).read_text(encoding="utf-8")
        self.assertIn("AmbiguousTransferDateError", src)

    def test_cross_account_netting_refuses_near_taxable_sales(self):
        # contribution into rrsp + withdrawal from rrsp2 near a
        # taxable loss must SURVIVE netting (and then the engine's
        # guard forces a declaration).
        from taxjson.lib.pipeline import _net_cross_account_transfers
        shel = [_t(action='TRANSFER', account='rrsp',
                   date='2024-03-05', quantity=100, net_amount=8000.0),
                _t(action='TRANSFER', account='rrsp2',
                   date='2024-03-20', quantity=-100,
                   net_amount=8000.0)]
        main = [_t(account='margin', date='2024-03-10', quantity=-100,
                   net_amount=8000.0)]
        out = _net_cross_account_transfers(shel, main_transactions=main)
        self.assertEqual(len(out), 2, "must not net near a sale")
        out2 = _net_cross_account_transfers(shel, main_transactions=[])
        self.assertEqual(out2, [], "far from sales it still nets")
