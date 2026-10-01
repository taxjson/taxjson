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


class TestDaysHeldDrivesScheduleThreeAcqYear(unittest.TestCase):
    """S069-09: the Canada record's days_held is where form-export's
    Schedule 3 year of acquisition comes from."""

    def test_lot_bought_in_an_earlier_year(self):
        res = run([T('BUYSELL', '2023-03-01', 'XYZ.TO', 100, 1000),
                   T('BUYSELL', '2025-06-02', 'XYZ.TO', -100, 1500)], 2025)
        rec, = sales(res)
        self.assertEqual(rec['days_held'], 824)
        self.assertEqual(schedule3(res, 2025)['XYZ.TO']['acq_year'], '2023')


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


if __name__ == '__main__':
    unittest.main()
