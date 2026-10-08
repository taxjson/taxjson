"""v0.24.1 pre-release review (area a): symbol codes, the Questrade REI
binding and the cross-listing evidence.

- H1: "UNITS" in a Questrade code's descriptions (a trust's "UNITS DIST
  ON ...") is no security-kind designator: the code resolves to the
  trust's listing and its sale counts.
- M5: a spun-off code's designators are its OWN name's, never the
  parent's class letter.
- M3: a USD REI row bound to the account's CAD listing is booked in the
  listing's currency: the raw holdings report is written, scan runs.
- M2: a .tt JOURNAL between two listings on one receipt venue (a NEO
  ETF's CAD and USD units) is no receipt evidence.
- L1, L2, L7: shown_apart and the TOBASE pair edge cases.

Every fixture is SYNTHETIC: invented QZ* tickers, names and codes, fake
account ids (pii-ok: 55500001, U5550001).
"""
import json
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from tax_rules import rule
from tax_rules.dual import cli, projects_both
from taxjson.lib import symbol_codes as SC

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _flat(text):
    return " ".join(text.split())


QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')


def qt(td, action, sym, desc, qty, net='0', act='Trades', price='0',
       gross='0', comm='0', cur='CAD', kind='Individual margin'):
    return (f'{td} 12:00:00 AM,{td} 12:00:00 AM,{action},{sym},"{desc}",'
            f'{qty},{price},{gross},{comm},{net},{cur},55500001,'  # pii-ok
            f'{act},{kind}\n')


IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,BrokerName,Interactive Brokers\n'
           'Statement,Data,Title,Activity Statement\n'
           'Statement,Data,Period,"January 1, 2026 - December 31, 2026"\n'
           'Financial Instrument Information,Header,Asset Category,Symbol,'
           'Description,Conid,Security ID,Underlying,Listing Exch,'
           'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n')
IB_TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
               'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
               'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
IB_XFER_H = ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
             'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
             'Market Value,Realized P/L,Cash Amount,Code\n')


def _sum(root):
    r = cli(root, 'sum', '--json')
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


# ------------------------------------------------------------------ H1

TRUST = 'QZR REAL ESTATE INVESTMENT TRUST'
H1_CODE = 'X000009'


def h1_ib():
    fii = (f'Financial Instrument Information,Data,Stocks,QZR.UN,"{TRUST}",'
           f'999000301,,,TSE,1,,,COMMON,,\n')
    trades = ('Trades,Data,Order,Stocks,CAD,U5550001,QZR.UN,'  # pii-ok
              '"2026-03-02, 10:00:00",100,10,0,-1000,-4.95,1004.95,0,0,O\n')
    xfers = ('Transfers,Data,Stocks,CAD,QZR.UN,2026-09-01,ACATS,Out,'
             'Other Broker,5550009,-100,0,-1100,0,0,\n')  # pii-ok
    return IB_HEAD + fii + IB_TRADES_H + trades + IB_XFER_H + xfers


def h1_qt(dist=True):
    return (QH
            + qt('2026-09-03', 'TFI', H1_CODE,
                 f'{TRUST} TRANSFER IN INTERACTIVE BROKER', '100',
                 act='Transfers')
            + (qt('2026-09-30', 'DIS', H1_CODE,
                  f'{TRUST} UNITS DIST ON 100 SHS REC 09/20/26 PAY 09/30/26',
                  '0', net='12.00', act='Dividends') if dist else '')
            + qt('2026-10-05', 'Sell', H1_CODE, f'{TRUST} WE ACTED AS AGENT',
                 '-100', net='1300.00', price='13', gross='1300'))


H1_ACCTS = ('[accounts.margin]\ntype = "taxable"\n\n'
            '[accounts.qt]\ntype = "taxable"\n')


class TestUnitsIsNoKind(unittest.TestCase):

    def test_units_wording_states_no_kind(self):
        for d in (f'{TRUST} UNITS DIST ON 100 SHS REC 09/20/26 PAY '
                  f'09/30/26',
                  'QZF GLOBAL FUND TRUST UNITS CASH DIV ON 5 SHS',
                  'QZL ENERGY LP UNITS DIST ON 5 SHS',
                  'QZE CANADA ETF UNITS'):
            with self.subTest(d=d):
                self.assertEqual(SC.code_designators([d]), frozenset())
        # The kinds that are one still count.
        self.assertEqual(SC.code_designators(['WTS QZD CORP WARRANT']),
                         frozenset({'~WARRANT'}))
        # A SPAC unit is told apart by its bundle wording (the warrant
        # word, the class letter), never by UNIT: it never resolves to
        # the common.
        spac = SC.code_designators(['QZS ACQUISITION CORP UNIT 1 CL A & '
                                    '1/2 WT'])
        self.assertEqual(spac, frozenset({'~WARRANT', '~A'}))
        self.assertFalse(SC.designators_agree(
            spac, SC.listing_designators(
                [SC.name_tokens('QZS ACQUISITION CORP CL A')]))[0])

    def _run(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(
                td, year=2026, accounts=H1_ACCTS,
                files={'inputs/margin/U5550001_2026.csv': h1_ib(),  # pii-ok
                       'inputs/qt/55500001.csv': h1_qt()},  # pii-ok
                canada={'source_currencies': ['USD']},
                usa={'source_currencies': ['CAD']})[country]
            r = cli(root, 'run', '--no-input')
            out = _flat(r.stdout + r.stderr)
            self.assertEqual(r.returncode, 0, out[-3000:])
            self.assertNotIn('another kind', out)
            st = json.loads((root / 'work' / 'qt_symbol_codes.state')
                            .read_text())
            self.assertEqual(st['resolved'][H1_CODE]['symbol'], 'QZR.UN.TO',
                             st)
            qt_ = {a['account']: a for a in _sum(root)['accounts']}['qt']
            # The sale counts: 1300 proceeds less the IB cost carried.
            if country == 'canada':
                self.assertAlmostEqual(qt_['stock'], 295.05, places=2)
            else:
                self.assertGreater(qt_['stock'], 0.0)

    @rule('CA-ACB-CODES')
    def test_canada_trust_units_code_resolves(self):
        self._run('canada')

    @rule('US-BASIS-CODES')
    def test_usa_trust_units_code_resolves(self):
        self._run('usa')


# ------------------------------------------------------------------ M5

M5_CODE = 'D0000002'
M5_LEG = ('QZX CORP SPINOFF ON 100 SHS FROM SEC# J0000002 QZP CORP CL A '
          'REC 10/23/25 PAY 10/27/25')


def m5_qt():
    return (QH
            + qt('2025-03-03', 'Buy', 'QZP', 'QZP CORP CL A WE ACTED AS '
                 'AGENT', '100', net='-1000.00', price='10', gross='-1000',
                 cur='USD')
            + qt('2025-10-27', 'DIS', M5_CODE, M5_LEG, '20',
                 act='Dividends', cur='USD')
            + qt('2025-11-14', 'Sell', 'QZX', 'QZX CORP WE ACTED AS AGENT',
                 '-20', net='100.00', price='5', gross='100', cur='USD'))


class TestSpinoffLegIgnoresTheParentsClass(unittest.TestCase):

    def test_designators_are_the_legs_own(self):
        self.assertEqual(SC.code_designators([M5_LEG]), frozenset())
        # A class the spun-off security's own name states still counts.
        self.assertEqual(
            SC.code_designators(['QZX CORP CL B SPINOFF ON 100 SHS FROM '
                                 'SEC# J0000002 QZP CORP CL A REC 10/23/25 '
                                 'PAY 10/27/25']), frozenset({'~B'}))

    def _run(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(
                td, year=2025,
                files={'inputs/margin/55500001.csv': m5_qt()},  # pii-ok
                canada={'source_currencies': ['USD']},
                usa={'source_currencies': ['CAD']})[country]
            first = cli(root, 'run', '--no-input')
            pending = root / 'work' / 'pending_elections.json'
            self.assertTrue(pending.exists(), first.stdout + first.stderr)
            eid = json.loads(pending.read_text())[
                'accounts']['margin']['pending'][0]['event_id']
            how = ('taxable_deemed_dividend' if country == 'canada'
                   else 'taxable_distribution_301')
            el = cli(root, 'elect', 'margin', '--set', f'{eid}={how}',
                     '--hint', 'fmv_per_share=2')
            self.assertEqual(el.returncode, 0, el.stdout + el.stderr)
            r = cli(root, 'run', '--no-input')
            out = _flat(first.stdout + first.stderr + r.stdout + r.stderr)
            self.assertEqual(r.returncode, 0, out[-3000:])
            self.assertNotIn('another share class', out)
            st = json.loads((root / 'work' / 'margin_symbol_codes.state')
                            .read_text())
            self.assertEqual(st['resolved'][M5_CODE]['symbol'], 'QZX.US',
                             st)
            self.assertNotIn('no purchase in your files', _flat(r.stdout
                                                                + r.stderr))
            m = {a['account']: a for a in _sum(root)['accounts']}['margin']
            # 20 units at the elected 2.00 sold for 100: a gain.
            self.assertGreater(m['stock'], 0.0)

    @rule('CA-ACB-CODES')
    def test_canada_spun_off_code_resolves(self):
        self._run('canada')

    @rule('US-BASIS-CODES')
    def test_usa_spun_off_code_resolves(self):
        self._run('usa')


if __name__ == '__main__':
    unittest.main()
