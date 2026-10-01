"""Low-round engine pins (fixl/engine-tests): end-to-end assertions on
engine output fields and boundaries that a mutant could change with
every other test still passing. Each class names the audit finding.

Synthetic data only; account labels are fake.
"""
import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from taxjson.lib.core import TaxTransaction, held_more_than_one_year
from taxjson.lib.numeric import round_floats
from taxjson.lib.pipeline import GainsRequest, run_gains
from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def T(action, date, sym, qty, net, *, time='10:00:00', settle=None,
      cur='CAD', acct='a', price=None, **kw):
    """One synthetic row; `net` is the fee-inclusive money magnitude."""
    if price is None:
        price = abs(net / qty) if qty else 0.0
    return TaxTransaction(action=action, date=date, time=time,
                          date_settle=settle or date, symbol=sym,
                          quantity=float(qty), price=float(price),
                          net_amount=float(net), currency=cur,
                          account=acct, **kw)


def U(*a, **kw):
    return T(*a, cur='USD', **kw)


def run(book, year, country='canada', sheltered=(), **kw):
    """pipeline.run_gains (what `taxjson run` uses) on a taxable book."""
    req = GainsRequest(country=country, year=year, taxable=True, **kw)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(copy.deepcopy(list(book)),
                        copy.deepcopy(list(sheltered)), [], req=req)
    return res


def sales(res):
    return [t for t in res['transactions'] if 'gain' in t]


def schedule3(res, year):
    from taxjson.bin.taxjson_form_export import build_schedule3
    rep = build_schedule3(res['transactions'], year)
    return {r['symbol']: r for r in rep['rows']}


class TestCanadaFeeApportionmentEndToEnd(unittest.TestCase):
    """R1-302: the per-record commission/fee (fee_share = closing_qty /
    |qty|) feeds Schedule 3 outlays and proceeds; nothing pinned it."""

    @rule("CA-ACB-02", "CA-DISP-01")
    def test_record_fees_and_schedule3_outlays(self):
        book = [
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 5010, price=50,
              commission=10),
            T('BUYSELL', '2025-03-03', 'XYZ.TO', -60, 3570, price=60,
              commission=30),
            # A sale that crosses zero: 100 close the long, 50 open a
            # short, so the closing record carries 100/150 of the fees.
            T('BUYSELL', '2025-01-06', 'ABC.TO', 100, 1005, price=10,
              commission=5),
            T('BUYSELL', '2025-03-03', 'ABC.TO', -150, 1482, price=10,
              commission=15, fee=3),
            T('BUYSELL', '2025-04-01', 'ABC.TO', 50, 405, price=8,
              commission=5),
        ]
        res = run(book, 2025)
        recs = {(s['symbol'], s['date']): s for s in sales(res)}
        xyz = recs[('XYZ.TO', '2025-03-03')]
        self.assertEqual(xyz['commission'], 30.0)
        self.assertEqual(xyz['fee'], 0.0)
        self.assertEqual(xyz['proceeds'], 3570.0)
        self.assertEqual(xyz['cost'], 3006.0)
        self.assertEqual(xyz['gain'], 564.0)
        abc = recs[('ABC.TO', '2025-03-03')]
        self.assertAlmostEqual(abc['commission'], 10.0, places=9)
        self.assertAlmostEqual(abc['fee'], 2.0, places=9)
        row = schedule3(res, 2025)['XYZ.TO']
        self.assertEqual((row['proceeds'], row['acb'], row['outlays'],
                          row['gain']), (3600.0, 3006.0, 30.0, 564.0))


class TestPreLossTriggerBumpTime(unittest.TestCase):
    """R1-305: the pre-loss-trigger ADJUST lands at loss + 1 s (23:59:59
    when the loss is at 23:59:59), so a later same-day sale of the pool
    averages the bump in and the loss itself never absorbs it."""

    def _book(self, loss_time, second_date):
        return [
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000),
            T('BUYSELL', '2025-02-01', 'XYZ.TO', 100, 1200),  # pre-loss rebuy
            T('BUYSELL', '2025-02-03', 'XYZ.TO', -100, 1000, time=loss_time),
            T('BUYSELL', second_date, 'XYZ.TO', -50, 1500, time='14:00:00'),
        ]

    def _check(self, res, adjust_time):
        # The bump is placed right after its loss row (_wash_after) and
        # its ADJUST line names loss + 1 s, wrapping to 23:59:59.
        cmds = [w['adjust_cmd'] for w in res['wash_sales']]
        self.assertEqual(cmds, [f'ADJUST 2025-02-03 {adjust_time} XYZ.TO '
                                f'CAD 300.0000'])
        loss, later = sorted(sales(res), key=lambda s: (s['date'],
                                                        s['proceeds']))
        self.assertEqual((loss['gain'], loss['disallowed_amount']),
                         (-300.0, 300.0))
        self.assertEqual((later['cost'], later['gain']), (950.0, 550.0))
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertEqual((inv['XYZ.TO']['qty'], inv['XYZ.TO']['total_cost']),
                         (50.0, 950.0))

    @rule("CA-SL-09")
    def test_later_same_day_sale_absorbs_the_bump(self):
        self._check(run(self._book('10:00:00', '2025-02-03'), 2025),
                    '10:00:01')

    @rule("CA-SL-09")
    def test_loss_at_235959_keeps_its_own_denial(self):
        self._check(run(self._book('23:59:59', '2025-02-04'), 2025),
                    '23:59:59')


class TestUsDecemberMonthEndHoldingPeriod(unittest.TestCase):
    """R1-315: the Dec-31 acquisition branch of held_more_than_one_year
    (Pub 550 / Rev. Rul. 66-7) had no test."""

    @rule("US-HOLD-01")
    def test_dec31_acquisition(self):
        h = held_more_than_one_year
        self.assertFalse(h('2023-12-31', '2024-06-01'))
        self.assertFalse(h('2023-12-31', '2024-12-31'))
        self.assertTrue(h('2023-12-31', '2025-01-01'))
        self.assertTrue(h('2023-12-31', '2025-01-31'))
        self.assertTrue(h('2024-11-30', '2025-12-01'))
        self.assertFalse(h('2024-11-30', '2025-11-30'))


class TestGainsJsonRounding(unittest.TestCase):
    """R1-317 (part 2): gains JSON money is rounded to 4 dp, ROUND_HALF_UP;
    quantity keys pass through. The old test compared with places=4,
    which the unrounded value also passes."""

    def test_four_dp_half_up_exact(self):
        out = round_floats({'gain': 100.123456789, 'cost': 1.23465,
                            'proceeds': 2.00005, 'fee': -0.00005,
                            'qty': 100.123456789,
                            'nested': [{'price': 0.00001234}]})
        self.assertEqual(out['gain'], 100.1235)
        self.assertEqual(out['cost'], 1.2347)       # half-even: 1.2346
        self.assertEqual(out['proceeds'], 2.0001)   # half-even: 2.0000
        self.assertEqual(out['fee'], -0.0001)
        self.assertEqual(out['qty'], 100.123456789)
        self.assertEqual(out['nested'][0]['price'], 0.0)


class TestConvertCurrencySettleDateKey(unittest.TestCase):
    """S028-12: convert-currency prices a trade at its SETTLE date's rate."""

    @rule("CA-FX-01")
    def test_dec31_trade_settling_january_uses_the_settle_rate(self):
        from taxjson.bin.taxjson_convert_currency import convert_transaction
        history = {'USD': {'2025-12-31': Decimal('1.37'),
                           '2026-01-02': Decimal('1.40')}}
        tx = T('BUYSELL', '2025-12-31', 'XYZ.US', -100, 10000, price=100,
               settle='2026-01-02', cur='USD')
        out = convert_transaction(tx, 'CAD', history, Decimal('1.0'))
        self.assertEqual(out.currency, 'CAD')
        self.assertAlmostEqual(out.net_amount, 14000.0, places=6)
        self.assertAlmostEqual(out.price, 140.0, places=6)


class TestPipelineFeeTotals(unittest.TestCase):
    """G1-10 / S076-11 (pipeline builder): summary.total_fees_by_currency
    on a settle-date project — commission AND fee, the settle-date year
    window (a Dec-30 trade settling in January counts in January's year,
    a Dec-31 one moves out), stocks/options buckets, no empty bucket for
    a fee-free currency — and the `sum` figure it feeds."""

    def test_settle_year_fee_map_and_sum(self):
        book = [
            T('BUYSELL', '2024-06-03', 'ABC.TO', 10, 107, commission=7),
            T('BUYSELL', '2024-12-30', 'XYZ.TO', 100, 1009.99,
              settle='2025-01-02', commission=9.99),
            T('BUYSELL', '2025-06-02', 'XYZ.TO', -50, 594.0,
              commission=4.95, fee=1.05),
            T('BUYSELL', '2025-03-03', 'XYZ250620C00012000.TO', 1, 101.5,
              commission=1.25, fee=0.25),
            T('BUYSELL', '2025-04-01', 'XYZ250620C00012000.TO', -1, 148.5,
              commission=1.25, fee=0.25),
            T('BUYSELL', '2025-12-31', 'XYZ.TO', -10, 117.0,
              settle='2026-01-02', commission=2.50, fee=0.50),
            U('BUYSELL', '2025-05-05', 'QQQ.US', 1, 400.0),
        ]
        res = run(book, 2025)
        fees = res['summary']['total_fees_by_currency']
        self.assertEqual(set(fees), {'CAD'})
        cad = fees['CAD']
        self.assertAlmostEqual(cad['stocks'], 15.99, places=9)
        self.assertAlmostEqual(cad['options'], 3.00, places=9)
        self.assertAlmostEqual(cad['total'], 18.99, places=9)
        from taxjson.bin.taxjson_sum_gains import summarize_gains
        s = summarize_gains(json.loads(json.dumps(res)))
        self.assertAlmostEqual(s['total_fees']['CAD'], 18.99, places=9)
        self.assertAlmostEqual(s['option_fees']['CAD'], 3.00, places=9)


def _split(combined, account, base):
    from taxjson.bin.taxjson_split_gains import split_for_account
    return split_for_account(combined, account, base)


def _brow(date, sym, qty, commission=0.0, fee=0.0, settle=None,
          action='BUYSELL'):
    return {'action': action, 'date': date, 'date_settle': settle or date,
            'time': '10:00:00', 'symbol': sym, 'quantity': qty,
            'price': 10.0, 'net_amount': abs(qty) * 10.0,
            'commission': commission, 'fee': fee, 'currency': 'CAD',
            'account': 'a'}


class TestSplitGainsFeeMap(unittest.TestCase):
    """S050-10 / S076-11 (split builder): a blended account's fee map is
    rebuilt from its base book for the target year only, commission plus
    fee, on the tax_date basis."""

    def test_year_scoped_commission_plus_fee(self):
        combined = {'transactions': [], 'inventory': [],
                    'summary': {'year': '2025',
                                'tax_date_basis': 'settle'}}
        base = [
            _brow('2024-05-01', 'XYZ.TO', 10, commission=9.99),
            _brow('2025-02-03', 'XYZ.TO', 10, commission=3.95, fee=1.00),
            _brow('2025-03-03', 'XYZ.TO', -10, commission=3.95, fee=1.00),
            # Traded 2024-12-31, settles 2025-01-02: 2025 on this basis.
            _brow('2024-12-31', 'XYZ250620C00012000.TO', 1,
                  commission=1.25, settle='2025-01-02'),
            # Traded 2025-12-31, settles in 2026: not 2025.
            _brow('2025-12-31', 'XYZ.TO', 5, commission=4.00,
                  settle='2026-01-02'),
        ]
        fees = _split(combined, 'a', base)['summary']['total_fees_by_currency']
        self.assertEqual(set(fees), {'CAD'})
        self.assertAlmostEqual(fees['CAD']['stocks'], 9.90, places=9)
        self.assertAlmostEqual(fees['CAD']['options'], 1.25, places=9)
        self.assertAlmostEqual(fees['CAD']['total'], 11.15, places=9)


class TestSplitGainsBlendedInventoryApportioned(unittest.TestCase):
    """S050-22: an account-less (s.47 blended) inventory row is split by
    each account's own units at the blended ACB per share."""

    def test_two_accounts_share_one_pool(self):
        combined = {'transactions': [], 'summary': {'year': '2025'},
                    'inventory': [{'symbol': 'XYZ.TO', 'qty': 150.0,
                                   'total_cost': 1500.0, 'currency': 'CAD'}]}
        base_a = [_brow('2025-02-03', 'XYZ.TO', 100)]
        base_b = [dict(_brow('2025-02-04', 'XYZ.TO', 50), account='b')]
        inv_a = _split(combined, 'a', base_a)['inventory']
        inv_b = _split(combined, 'b', base_b)['inventory']
        self.assertEqual(len(inv_a), 1)
        self.assertEqual((inv_a[0]['qty'], inv_a[0]['total_cost']),
                         (100.0, 1000.0))
        self.assertEqual((inv_b[0]['qty'], inv_b[0]['total_cost']),
                         (50.0, 500.0))
        self.assertEqual(inv_a[0]['account'], 'a')


class TestGrantSinceUsesTheWritesSettleYear(unittest.TestCase):
    """S068-22: option_grant_timing_since compares the write's year on the
    tax_date basis — a Dec-31 write settling in the `since` year is
    grant-timed."""

    @rule("CA-OPT-01")
    def test_dec31_write_settling_in_since_year(self):
        book = [
            T('BUYSELL', '2025-12-31', 'ABC270115C00030000.TO', -1, 300,
              settle='2026-01-02'),
            T('BUYSELL', '2027-01-05', 'ABC270115C00030000.TO', 1, 400,
              settle='2027-01-06'),
        ]
        kw = dict(option_premium_timing='grant', option_grant_since=2026)
        self.assertEqual(run(book, 2026, **kw)['summary']['total_gain'],
                         300.0)
        self.assertEqual(run(book, 2027, **kw)['summary']['total_gain'],
                         -400.0)


class TestDaysHeldDrivesScheduleThreeAcqYear(unittest.TestCase):
    """S069-09: the Canada record's days_held is where form-export's
    Schedule 3 year of acquisition comes from."""

    def test_lot_bought_in_an_earlier_year(self):
        res = run([T('BUYSELL', '2023-03-01', 'XYZ.TO', 100, 1000),
                   T('BUYSELL', '2025-06-02', 'XYZ.TO', -100, 1500)], 2025)
        rec, = sales(res)
        self.assertEqual(rec['days_held'], 824)
        self.assertEqual(schedule3(res, 2025)['XYZ.TO']['acq_year'], '2023')


class TestWashWindowBalanceAtDayThirty(unittest.TestCase):
    """S069-18: wash_window.bal_at_end counts rows dated ON day +30."""

    def _bal(self, book):
        res = run(book, 2025)
        return {s['date']: (s.get('wash_window') or {}).get('bal_at_end')
                for s in sales(res)}

    def test_sale_on_day_30_reduces_the_end_balance(self):
        bal = self._bal([
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000),
            T('BUYSELL', '2025-02-03', 'XYZ.TO', -100, 1000),
            T('BUYSELL', '2025-02-10', 'XYZ.TO', 100, 1100),
            T('BUYSELL', '2025-03-05', 'XYZ.TO', -50, 600)])
        self.assertEqual(bal['2025-02-03'], 50.0)

    def test_buy_on_day_30_adds_to_the_end_balance(self):
        bal = self._bal([
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000),
            T('BUYSELL', '2025-02-03', 'XYZ.TO', -100, 1000),
            T('BUYSELL', '2025-02-10', 'XYZ.TO', 60, 660),
            T('BUYSELL', '2025-03-05', 'XYZ.TO', 40, 440)])
        self.assertEqual(bal['2025-02-03'], 100.0)


class TestRenameOnWindowEndPoolBacking(unittest.TestCase):
    """S069-19: the end-of-window pool backing is keyed after every row of
    the window's last day, so a rename on that day routes the bump to
    the renamed pool, not to the old symbol's partial sale."""

    @rule("CA-SL-09")
    def test_rename_on_window_end_with_partial_sale(self):
        book = [
            T('BUYSELL', '2025-01-06', 'OLD.TO', 100, 2000, time='09:30:00',
              id='a0'),
            T('BUYSELL', '2025-02-03', 'OLD.TO', -100, 1000, id='a1'),
            T('BUYSELL', '2025-02-10', 'OLD.TO', 100, 1100, id='a2'),
            T('BUYSELL', '2025-02-20', 'OLD.TO', -60, 720, id='a3'),
            T('SPLIT', '2025-03-05', 'OLD.TO', 1.0, 0, time='09:30:00',
              price=0, symbol_new='NEW.TO', id='sp'),
            T('BUYSELL', '2025-03-05', 'NEW.TO', 50, 600, time='11:00:00',
              acct='b', id='a4'),
        ]
        res = run(book, 2025)
        self.assertEqual(res['summary']['total_gain'], -100.0)
        self.assertEqual(res['summary']['total_disallowed'], 1380.0)
        by = {s['date']: s for s in sales(res)}
        self.assertEqual((by['2025-02-20']['cost'], by['2025-02-20']['gain']),
                         (1200.0, 0.0))
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertEqual((inv['NEW.TO']['qty'], inv['NEW.TO']['total_cost']),
                         (90.0, 1880.0))


class TestCanadaOptionRecordUnits(unittest.TestCase):
    """S070-02: a Canada option record's qty is contracts (Schedule 3
    units), not shares; share rows unchanged."""

    def test_option_and_share_units(self):
        res = run([
            T('BUYSELL', '2025-01-06', 'XYZ250620C00050000.TO', 2, 400),
            T('BUYSELL', '2025-03-03', 'XYZ250620C00050000.TO', -2, 600),
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 1000),
            T('BUYSELL', '2025-03-03', 'XYZ.TO', -100, 1500)], 2025)
        q = {s['symbol']: s['qty'] for s in sales(res)}
        self.assertEqual(q, {'XYZ250620C00050000.TO': 2.0, 'XYZ.TO': 100.0})
        s3 = schedule3(res, 2025)
        self.assertEqual(s3['XYZ250620C00050000.TO']['units'], 2.0)
        self.assertEqual(s3['XYZ.TO']['units'], 100.0)


class TestInventoryCurrencyReachesHoldingsToml(unittest.TestCase):
    """S070-06: the engine inventory's currency drives holdings.toml's
    currency and base_* fields."""

    def test_usd_holding_with_cad_base(self):
        try:
            import tomllib
        except ModuleNotFoundError:          # pragma: no cover
            self.skipTest("no tomllib")
        native = run([U('BUYSELL', '2025-01-06', 'XYZ.US', 100, 1000)], 2025)
        base = run([T('BUYSELL', '2025-01-06', 'XYZ.US', 100, 1350)], 2025)
        self.assertEqual(native['inventory'][0]['currency'], 'USD')
        self.assertEqual(base['inventory'][0]['currency'], 'CAD')
        with tempfile.TemporaryDirectory() as tmp:
            n = Path(tmp) / 'native.json'
            b = Path(tmp) / 'base.json'
            n.write_text(json.dumps({'inventory': native['inventory']}))
            b.write_text(json.dumps({'inventory': base['inventory']}))
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_export',
                 '--holdings-toml', '--base-gains', str(b), str(n)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        h = tomllib.loads(r.stdout)['holding'][0]
        self.assertEqual(h['currency'], 'USD')
        self.assertEqual(h['base_currency'], 'CAD')
        self.assertEqual(h['base_total_cost'], 1350.0)


class TestUsReplacementOrderAcquiredByTime(unittest.TestCase):
    """S070-13: same-date replacements go in order acquired by clock time
    (Reg. 1.1091-1(c)), whatever their ids: the 10:00 taxable lot takes
    the wash, the 14:00 IRA lot does not make it permanent."""

    def _run(self, tax_time, ira_time):
        book = [U('BUYSELL', '2025-01-06', 'AAA.US', 100, 2000,
                  time='09:30:00', id='b0'),
                U('BUYSELL', '2025-02-03', 'AAA.US', -100, 1000, id='s0'),
                U('BUYSELL', '2025-02-10', 'AAA.US', 100, 1100,
                  time=tax_time, id='z_tax')]
        ira = [U('BUYSELL', '2025-02-10', 'AAA.US', 100, 1100,
                 time=ira_time, id='a_ira', acct='ira')]
        res = run(book, 2025, country='usa', sheltered=ira)
        inv = {i['symbol']: i for i in res['inventory']}
        return res, inv['AAA.US']['total_cost']

    @rule("US-WASH-09")
    def test_taxable_lot_first(self):
        res, basis = self._run('10:00:00', '14:00:00')
        self.assertEqual(basis, 2100.0)
        self.assertEqual(res['summary']['total_gain'], 0.0)

    @rule("US-WASH-11")
    def test_earlier_ira_lot_makes_it_permanent(self):
        # The IRA lot was acquired first (10:00): it takes the wash, the
        # denial is permanent and the 14:00 taxable lot keeps its cost.
        res, basis = self._run('14:00:00', '10:00:00')
        self.assertEqual(basis, 1100.0)
        rec, = sales(res)
        self.assertEqual(rec['permanently_disallowed'], 1000.0)


class TestUsChunkFeeApportionment(unittest.TestCase):
    """S070-15: a FIFO sale across two lots splits its commission by each
    chunk's quantity."""

    @rule("US-BASIS-03")
    def test_two_chunks(self):
        res = run([U('BUYSELL', '2025-01-06', 'XYZ.US', 100, 1000),
                   U('BUYSELL', '2025-01-07', 'XYZ.US', 100, 1100),
                   U('BUYSELL', '2025-03-03', 'XYZ.US', -120, 1470,
                     price=12.5, commission=30, fee=6)],
                  2025, country='usa')
        chunks = sorted(sales(res), key=lambda s: -s['qty'])
        self.assertEqual([c['qty'] for c in chunks], [100.0, 20.0])
        self.assertEqual([c['commission'] for c in chunks], [25.0, 5.0])
        self.assertEqual([c['fee'] for c in chunks], [5.0, 1.0])
        self.assertEqual([c['proceeds'] for c in chunks], [1225.0, 245.0])


class TestUsOptionRecordReachesForm8949(unittest.TestCase):
    """S070-16: the US record's is_option marks the 8949 description."""

    def test_option_description(self):
        from taxjson.bin.taxjson_form_export import build_8949
        res = run([U('BUYSELL', '2025-01-06', 'XYZ250620C00050000', 2, 400),
                   U('BUYSELL', '2025-03-03', 'XYZ250620C00050000', -2, 600),
                   U('BUYSELL', '2025-01-06', 'XYZ.US', 10, 100),
                   U('BUYSELL', '2025-03-03', 'XYZ.US', -10, 150)],
                  2025, country='usa')
        recs = {s['symbol']: s for s in sales(res)}
        self.assertIs(recs['XYZ250620C00050000']['is_option'], True)
        self.assertIs(recs['XYZ.US']['is_option'], False)
        desc = sorted(r['description']
                      for r in build_8949(sales(res))['part_I'])
        self.assertEqual(desc, ['10 XYZ.US', '2 XYZ250620C00050000 (option)'])


class TestSplitAtTheTradesOwnTimestamp(unittest.TestCase):
    """S071-05: a trade stamped exactly at the split's (date, time) is
    already in post-split units and is not re-denominated."""

    @rule("CA-CORP-01")
    def test_ex_date_trade_at_split_time(self):
        res = run([
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000, time='09:30:00',
              id='b0'),
            T('SPLIT', '2025-06-10', 'XYZ.TO', 2.0, 0, time='09:30:00',
              price=0, id='sp'),
            T('BUYSELL', '2025-06-10', 'XYZ.TO', 100, 1200, time='09:30:00',
              settle='2025-06-11', id='b1'),
            T('BUYSELL', '2025-07-02', 'XYZ.TO', -300, 4500,
              settle='2025-07-03', id='s1')], 2025)
        rec, = sales(res)
        self.assertEqual((rec['qty'], rec['cost'], rec['gain']),
                         (300.0, 3200.0, 1300.0))
        self.assertEqual([i for i in res['inventory']
                          if abs(i.get('qty') or 0) > 1e-9], [])


class TestCanadaBalanceWalkClockOrder(unittest.TestCase):
    """S074-11: the per-account balance walk orders same-day rows by clock
    time like the ACB pass, even when the export lists them otherwise."""

    @rule("CA-SL-08")
    def test_buy_then_sell_same_day_other_account(self):
        res = run([
            T('BUYSELL', '2025-01-06', 'XYZ.TO', 100, 2000, time='09:30:00',
              id='t1'),
            T('BUYSELL', '2025-02-03', 'XYZ.TO', -100, 1000, id='t2'),
            # Listed sell-first; the clock says buy 10:00, sell 14:00.
            T('BUYSELL', '2025-02-10', 'XYZ.TO', -100, 1150, time='14:00:00',
              acct='b', id='a_sell'),
            T('BUYSELL', '2025-02-10', 'XYZ.TO', 100, 1100, time='10:00:00',
              acct='b', id='z_buy'),
            T('BUYSELL', '2025-02-11', 'XYZ.TO', 50, 550, time='09:30:00',
              acct='b', id='t5')], 2025)
        self.assertEqual(res['summary']['total_gain'], -725.0)
        self.assertEqual(res['summary']['total_disallowed'], 725.0)
        by = {s['date']: s for s in sales(res)}
        self.assertEqual(by['2025-02-10']['cost'], 1600.0)
        inv = {i['symbol']: i for i in res['inventory']}
        self.assertEqual((inv['XYZ.TO']['qty'], inv['XYZ.TO']['total_cost']),
                         (50.0, 775.0))
        # The wash window's running balance walks the same clock order
        # (the ACB pass no longer depends on this walk; its display does).
        win = {(t['date'], t['role']): t['running_bal'] for t in
               by['2025-02-03']['wash_window']['transactions']}
        self.assertEqual(win[('2025-02-10', 'trigger')], 100.0)
        self.assertEqual(win[('2025-02-10', 'other_sell')], 0.0)


if __name__ == '__main__':
    unittest.main()
