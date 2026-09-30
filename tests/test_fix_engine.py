"""Regression tests for the engine-area audit fixes (fix/engine).

Synthetic data only; account labels are fake.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


def _project(root, accounts, inputs, phantoms=None, extra_settings=''):
    """accounts: {name: type}; inputs: {name: [tt lines]}."""
    cfg = ('[settings]\nyear = 2025\ncountry = "canada"\n'
           'base_currency = "CAD"\nsource_currencies = []\n'
           'tax_date = "settle"\n' + extra_settings)
    for a, typ in accounts.items():
        cfg += f'[accounts.{a}]\ntype = "{typ}"\n'
    (root / "taxjson.toml").write_text(cfg)
    for a, lines in inputs.items():
        d = root / "inputs" / a
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{a}_hist.tt").write_text("\n".join(lines) + "\n")
    if phantoms is not None:
        (root / "phantoms.json").write_text(json.dumps(
            [{"symbol": s, "account": a} for s, a in phantoms]))


def _tx(**kw):
    kw.setdefault('currency', 'CAD')
    kw.setdefault('account', 'margin')
    return TaxTransaction(**kw)


class TestUnmarkedLegNotBlockedByLaterMarkedLeg(unittest.TestCase):
    """R1-33: a plain (IB/RBC BUYSELL) assignment stock leg was skipped
    when ANY later marked (Webull ASSIGN) leg existed on the same
    (account, underlying): the premium jumped to that later leg,
    possibly a year later."""

    def _rows(self, sfx, cur):
        und = f'XYZ.{sfx}'
        return [
            # 2025: short put, plain-convention assignment.
            _tx(action='BUYSELL', date='2025-02-03', time='10:00:00',
                symbol=f'XYZ250321P00012000.{sfx}', quantity=-1.0,
                price=2.0, net_amount=199.0, currency=cur),
            _tx(action='ASSIGN', date='2025-03-21', time='16:20:00',
                symbol=f'XYZ250321P00012000.{sfx}', quantity=1.0,
                price=0.0, net_amount=0.0, currency=cur),
            _tx(action='BUYSELL', date='2025-03-21', time='16:20:00',
                symbol=und, quantity=100.0, price=12.0,
                net_amount=1200.0, currency=cur),
            _tx(action='BUYSELL', date='2025-06-02', time='10:00:00',
                symbol=und, quantity=-100.0, price=13.0,
                net_amount=1299.0, currency=cur),
            # 2026: long call exercised, MARKED (Webull) stock leg.
            _tx(action='BUYSELL', date='2026-01-05', time='10:00:00',
                symbol=f'XYZ260320C00012000.{sfx}', quantity=1.0,
                price=1.0, net_amount=101.0, currency=cur),
            _tx(action='ASSIGN', date='2026-03-20', time='16:00:00',
                symbol=f'XYZ260320C00012000.{sfx}', quantity=-1.0,
                price=0.0, net_amount=0.0, currency=cur),
            _tx(action='ASSIGN', date='2026-03-20', time='16:00:01',
                symbol=und, quantity=100.0, price=12.0,
                net_amount=1200.0, currency=cur),
            _tx(action='BUYSELL', date='2026-04-01', time='10:00:00',
                symbol=und, quantity=-100.0, price=14.0,
                net_amount=1399.0, currency=cur),
        ]

    def test_canada(self):
        res = CanadaTaxRules().compute_gains(self._rows('TO', 'CAD'))
        by_year = {}
        for g in res['transactions']:
            if g.get('raw_gain') is None:
                continue
            y = (g.get('date_settle') or g['date'])[:4]
            by_year[y] = by_year.get(y, 0.0) + g['gain']
        self.assertAlmostEqual(by_year.get('2025', 0.0), 298.0, places=2,
                               msg=f"premium left 2025: {by_year}")
        self.assertAlmostEqual(by_year.get('2026', 0.0), 98.0, places=2,
                               msg=f"premium landed on 2026: {by_year}")

    def test_usa(self):
        res = USATaxRules().compute_gains(self._rows('US', 'USD'))
        by_year = {}
        for g in res['transactions']:
            if g.get('raw_gain') is None:
                continue
            y = (g.get('date_sold') or g.get('date'))[:4]
            by_year[y] = by_year.get(y, 0.0) + g['raw_gain']
        self.assertAlmostEqual(by_year.get('2025', 0.0), 298.0, places=2,
                               msg=f"premium left 2025: {by_year}")
        self.assertAlmostEqual(by_year.get('2026', 0.0), 98.0, places=2,
                               msg=f"premium landed on 2026: {by_year}")


def _gains(main, sheltered, **kw):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):
        r = run_gains(main, sheltered,
                      req=GainsRequest(country='canada', year=2025,
                                       taxable=True, **kw))
    perm = round(sum(g.get('permanently_disallowed', 0.0) or 0.0
                     for g in r['transactions']), 6)
    return r['summary']['total_gain'], r['summary']['total_disallowed'], perm


def _t(action, date, qty, acct, sym='XYZ.TO', price=10.0):
    return _tx(action=action, date=date, time='10:00:00', symbol=sym,
               quantity=float(qty), price=price,
               net_amount=abs(qty) * price, account=acct)


class TestOwnRegisteredMoveKeepsHolderBalances(unittest.TestCase):
    """S018-05 / G2-0: a registered-to-registered move of the owner's
    own shares (rrspA -> rrspB) is netted out of the wash context at the
    symbol level, but the s.54 still-held test runs PER HOLDER — so the
    receiving account looked short (a permanent denial was missed) and
    the sending account looked long (a denial was invented)."""

    MARGIN = [
        _t('BUYSELL', '2025-01-10', 100, 'margin', price=20.0),
        _t('BUYSELL', '2025-06-02', -100, 'margin'),     # loss 1,000
    ]

    def test_case_a_receiving_account_rebuys_and_holds(self):
        moved = [
            _t('BUYSELL', '2024-01-10', 100, 'rrspA'),
            _t('TRANSFER', '2024-03-01', -100, 'rrspA'),
            _t('TRANSFER', '2024-03-01', 100, 'rrspB'),
            _t('BUYSELL', '2024-05-01', -100, 'rrspB'),
            _t('BUYSELL', '2025-06-10', 100, 'rrspB'),
        ]
        native = [
            _t('BUYSELL', '2024-01-10', 100, 'rrspB'),
            _t('BUYSELL', '2024-05-01', -100, 'rrspB'),
            _t('BUYSELL', '2025-06-10', 100, 'rrspB'),
        ]
        self.assertEqual(_gains(self.MARGIN, native), (0.0, 1000.0, 1000.0))
        self.assertEqual(_gains(self.MARGIN, moved), (0.0, 1000.0, 1000.0),
                         "the netted move left rrspB short, so its "
                         "in-window rebuy was not 'still held'")

    def test_case_b_sending_account_round_trips_in_window(self):
        moved = [
            _t('BUYSELL', '2024-01-10', 100, 'rrspA'),
            _t('TRANSFER', '2024-03-01', -100, 'rrspA'),
            _t('TRANSFER', '2024-03-01', 100, 'rrspB'),
            _t('BUYSELL', '2025-06-05', 100, 'rrspA'),
            _t('BUYSELL', '2025-06-20', -100, 'rrspA'),
        ]
        native = [
            _t('BUYSELL', '2024-01-10', 100, 'rrspB'),
            _t('BUYSELL', '2025-06-05', 100, 'rrspA'),
            _t('BUYSELL', '2025-06-20', -100, 'rrspA'),
        ]
        self.assertEqual(_gains(self.MARGIN, native), (-1000.0, 0, 0.0))
        self.assertEqual(_gains(self.MARGIN, moved), (-1000.0, 0, 0.0),
                         "the netted move left rrspA holding the moved "
                         "shares, inventing a permanent denial")

    def test_g2_0_partial_sale_after_move(self):
        margin = [
            _t('BUYSELL', '2024-10-01', 100, 'margin', price=20.0),
            _t('BUYSELL', '2025-06-02', -100, 'margin'),
        ]
        moved = [
            _t('BUYSELL', '2024-11-01', 200, 'rrsp'),
            _t('TRANSFER', '2025-02-03', -200, 'rrsp'),
            _t('TRANSFER', '2025-02-03', 200, 'rrsp2'),
            _t('BUYSELL', '2025-03-03', -150, 'rrsp2'),
            _t('BUYSELL', '2025-06-09', 100, 'rrsp2'),
        ]
        g, dis, perm = _gains(margin, moved)
        self.assertAlmostEqual(perm, 1000.0, places=2)
        self.assertAlmostEqual(g, 0.0, places=2)


class TestSameStampTaxableBeforeRegistered(unittest.TestCase):
    """S018-06: a taxable rebuy and a TFSA buy at the SAME timestamp were
    ordered by the rows' content-hash id, so a one-cent change in the
    TFSA price decided whether the superficial loss was deferred into
    the taxable ACB or lost for good. At a tie the taxpayer's own
    (taxable) acquisition now comes first, then registered, then
    affiliated accounts — by account label, never by hash."""

    def _run(self, engine, sfx, cur, p):
        taxable = [
            _tx(action='BUYSELL', date='2025-01-06', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=100.0, price=20.0,
                net_amount=2000.0, currency=cur),
            _tx(action='BUYSELL', date='2025-03-03', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=-100.0, price=10.0,
                net_amount=1000.0, currency=cur),
            _tx(action='BUYSELL', date='2025-03-10', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=100.0, price=10.5,
                net_amount=1050.0, currency=cur),
            _tx(action='BUYSELL', date='2025-06-02', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=-100.0, price=12.0,
                net_amount=1200.0, currency=cur),
        ]
        tfsa = [
            _tx(action='BUYSELL', date='2025-03-10', time='09:30:00',
                symbol=f'XYZ.{sfx}', quantity=100.0, price=p,
                net_amount=round(100 * p, 2), currency=cur,
                account='tfsa'),
        ]
        res = engine().compute_gains(taxable, sheltered_transactions=tfsa)
        gain = sum((g.get('gain') if g.get('gain') is not None
                    else g.get('raw_gain') or 0.0)
                   for g in res['transactions']
                   if g.get('raw_gain') is not None)
        perm = sum(g.get('permanently_disallowed') or 0.0
                   for g in res['transactions'])
        return round(gain, 2), round(perm, 2)

    def test_canada_same_stamp_is_deferred_whatever_the_price(self):
        seen = {self._run(CanadaTaxRules, 'TO', 'CAD', p / 100)
                for p in range(1045, 1065)}
        self.assertEqual(seen, {(-850.0, 0.0)},
                         "the row hash decided deferral vs permanent")

    def test_usa_same_stamp_is_deferred_whatever_the_price(self):
        seen = {self._run(USATaxRules, 'US', 'USD', p / 100)[1]
                for p in range(1045, 1065)}
        self.assertEqual(seen, {0.0},
                         "the row hash decided deferral vs permanent")


class TestPhantomsNameUnknownAccount(unittest.TestCase):
    """S021-05: renaming an account silently dropped its phantoms.json
    openings (keyed by the account label) and changed the filed gain."""

    def test_run_refuses_a_phantom_entry_for_an_unknown_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, {"taxA": "taxable"}, {"taxA": [
                "BUYSELL 2025-02-03 10:00:00 XEI.TO 50 CAD 20.00 1000.00 0",
                "BUYSELL 2025-05-01 10:00:00 XEI.TO -150 CAD 25.00 3750.00 0",
            ]}, phantoms=[("XEI.TO", "margin")])
            r = _run_cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0,
                            "a stale phantoms.json account label must "
                            "stop the run")
        self.assertIn("phantoms.json", r.stderr)
        self.assertIn("'margin'", r.stderr)
        self.assertIn("taxA", r.stderr)

    def test_known_accounts_still_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, {"margin": "taxable"}, {"margin": [
                "BUYSELL 2025-02-03 10:00:00 XEI.TO 50 CAD 20.00 1000.00 0",
                "BUYSELL 2025-05-01 10:00:00 XEI.TO -150 CAD 25.00 3750.00 0",
            ]}, phantoms=[("XEI.TO", "margin")])
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)


class TestCheckFiledSeesAccountsOutsideTheLock(unittest.TestCase):
    """diff_snapshot only walked the lock's accounts: a taxable account
    added to the books after close-year was never recompared, and
    check-filed said OK."""

    def test_new_taxable_account_with_year_activity_is_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            margin = [
                "BUYSELL 2025-02-03 10:00:00 XEI.TO 100 CAD 20.00 2000.00 0",
                "BUYSELL 2025-05-01 10:00:00 XEI.TO -100 CAD 25.00 2500.00 0",
            ]
            _project(root, {"margin": "taxable"}, {"margin": margin},
                     extra_settings="option_grant_timing_since = 2025\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run_cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run_cli(root, "check-filed")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # A second taxable account appears after filing.
            _project(root, {"margin": "taxable", "cash2": "taxable"},
                     {"margin": margin, "cash2": [
                         "BUYSELL 2025-03-03 10:00:00 ABC.TO 10 CAD 10.00 100.00 0",
                         "BUYSELL 2025-04-01 10:00:00 ABC.TO -10 CAD 30.00 300.00 0",
                     ]}, extra_settings="option_grant_timing_since = 2025\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _run_cli(root, "check-filed")
        self.assertEqual(r.returncode, 1,
                         "an account outside the lock was never compared")
        self.assertIn("cash2", r.stdout + r.stderr)
        self.assertIn("not in the filed lock", r.stdout + r.stderr)

    def test_diff_snapshot_lists_both_directions(self):
        from taxjson.bin.taxjson_filed import diff_snapshot
        agg = {"realized": 0.0, "disallowed": 0.0, "dispositions": 0,
               "income": 0.0, "tainted": 0, "proceeds": 0.0,
               "st_gain": 0.0, "lt_gain": 0.0}
        snap = {"accounts": {"a": dict(agg), "gone": dict(agg)}}
        rec = {"a": dict(agg), "gone": None,
               "new": dict(agg, realized=200.0, dispositions=1,
                           proceeds=300.0),
               "idle": dict(agg)}
        lines = diff_snapshot(snap, rec)
        self.assertTrue(any(l.startswith("gone:") for l in lines), lines)
        self.assertTrue(any(l.startswith("new:") for l in lines), lines)
        self.assertFalse(any(l.startswith("idle:") for l in lines), lines)


class TestPhantomsReachTheShelteredContext(unittest.TestCase):
    """S021-09: phantom openings were applied to the taxable book only,
    never to the sheltered/affiliated wash context — a TFSA with
    truncated history looked short, its in-window rebuy was not 'held
    at day 30', and a permanent denial was missed."""

    def test_tfsa_phantom_backs_a_permanent_denial(self):
        margin = [
            _t('BUYSELL', '2025-01-03', 100, 'margin', price=30.0),
            _t('BUYSELL', '2025-03-11', -100, 'margin', price=20.0),
        ]
        tfsa = [
            _t('BUYSELL', '2025-01-16', -100, 'tfsa', price=29.0),
            _t('BUYSELL', '2025-03-13', 50, 'tfsa', price=20.0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ph = Path(tmp) / "phantoms.json"
            ph.write_text(json.dumps([{"symbol": "XYZ.TO",
                                       "account": "tfsa"}]))
            g, dis, perm = _gains(margin, tfsa, incomplete_history=ph)
        self.assertEqual((g, dis, perm), (-500.0, 500.0, 500.0))

    def test_affiliated_context_gets_openings_too(self):
        from taxjson.lib.pipeline import prepare_books
        import contextlib
        import io
        aff = [_t('BUYSELL', '2025-01-16', -100, 'spouse', price=29.0)]
        with tempfile.TemporaryDirectory() as tmp:
            ph = Path(tmp) / "phantoms.json"
            ph.write_text(json.dumps([{"symbol": "XYZ.TO",
                                       "account": "spouse"}]))
            with contextlib.redirect_stderr(io.StringIO()):
                _m, _s, a, log = prepare_books(
                    [], [], aff, taxable=True, incomplete_history=ph)
        self.assertEqual([(t.action, t.quantity) for t in a
                          if t.action == 'OPENING_BALANCE'],
                         [('OPENING_BALANCE', 100.0)])
        self.assertEqual([(e['account'], e['inserted']) for e in log],
                         [('spouse', True)])


class TestDistributionsSizedWithPhantoms(unittest.TestCase):
    """S000-08: distributions.map ADJUSTs were sized on the record-date
    balance of the phantom-less base book, so a phantom-backed position
    got the wrong ACB change (or none: 'no shares held')."""

    BOOK = {"transactions": [
        # 100 pre-window shares (phantoms.json) sold in February.
        {"action": "BUYSELL", "date": "2025-02-03",
         "date_settle": "2025-02-04", "time": "10:00:00",
         "symbol": "XAW.TO", "quantity": -100.0, "price": 30.0,
         "net_amount": 3000.0, "currency": "CAD", "account": "margin",
         "id": "a"},
        {"action": "BUYSELL", "date": "2025-03-03",
         "date_settle": "2025-03-04", "time": "10:00:00",
         "symbol": "XAW.TO", "quantity": 200.0, "price": 31.0,
         "net_amount": 6200.0, "currency": "CAD", "account": "margin",
         "id": "b"},
    ]}

    def _apply(self, phantoms):
        import contextlib
        import copy
        import io
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        with contextlib.redirect_stderr(io.StringIO()):
            doc, n = apply_distributions(
                copy.deepcopy(self.BOOK), [("XAW.TO", "2025-12-29", 0.5)],
                "margin", "settle", phantoms=phantoms)
        return [t["net_amount"] for t in doc["transactions"]
                if t["action"] == "ADJUST"]

    def test_record_date_balance_includes_the_phantom_opening(self):
        self.assertEqual(self._apply({("XAW.TO", "margin")}), [100.0])
        # The opening rows are for sizing only — never written.
        self.assertEqual(self._apply(None), [50.0])

    def test_cli_takes_incomplete_history(self):
        from taxjson.bin.taxjson_apply_distributions import main
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps(self.BOOK))
            mp = Path(tmp) / "distributions.map"
            mp.write_text("XAW.TO 2025-12-29 0.5\n")
            ph = Path(tmp) / "phantoms.json"
            ph.write_text(json.dumps([{"symbol": "XAW.TO",
                                       "account": "margin"}]))
            with contextlib.redirect_stderr(io.StringIO()):
                rc = main([str(base), "--map", str(mp), "--account",
                           "margin", "--incomplete-history", str(ph)])
            self.assertEqual(rc, 0)
            doc = json.loads(base.read_text())
        self.assertEqual([t["net_amount"] for t in doc["transactions"]
                          if t["action"] == "ADJUST"], [100.0])
        self.assertFalse(any(t["action"] == "OPENING_BALANCE"
                             for t in doc["transactions"]))


if __name__ == '__main__':
    unittest.main()
