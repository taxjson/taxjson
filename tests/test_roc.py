"""Return-of-capital reclassification: broker rows labeled ROC become
ADJUST records that REDUCE ACB (net_amount = -amount, type='roc') instead
of dividend income — across Questrade, RBC, and IB — plus the shared
description matcher and the IB held-listing rebind for ROC ADJUSTs.
"""

import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import is_roc_description
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage


def _parse_with(parser, content):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                     delete=False) as f:
        f.write(content)
        fname = f.name
    try:
        return parser.parse_file(Path(fname))
    finally:
        os.remove(fname)


class TestRocDescription(unittest.TestCase):
    def test_matches_common_phrasings(self):
        self.assertTrue(is_roc_description("RETURN OF CAPITAL ON 300 SHS"))
        self.assertTrue(is_roc_description("Return of Capital USD 0.12/sh"))
        self.assertTrue(is_roc_description("RET OF CAPITAL"))

    def test_word_boundary_negatives(self):
        self.assertFalse(is_roc_description("CAPITAL GAINS DISTRIBUTION"))
        self.assertFalse(is_roc_description("RETURNED CAPITAL LOAN"))
        self.assertFalse(is_roc_description(""))
        self.assertFalse(is_roc_description(None))


class TestQuestradeRoc(unittest.TestCase):
    HDR = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
           'Quantity,Price,Gross Amount,Commission,Net Amount,Currency\n')

    def test_roc_row_becomes_negative_adjust(self):
        csv = (self.HDR +
               '2025-03-31 09:30:00 AM,2025-03-31 12:00:00 AM,DIV,XEI.TO,'
               'ISHARES S&P/TSX COMP HIGH DIV RETURN OF CAPITAL ON 500 SHS,'
               '0,0.00,0.00,0.00,42.50,CAD\n')
        txs = _parse_with(QuestradeBrokerage(), csv)
        self.assertEqual(len(txs), 1)
        t = txs[0]
        self.assertEqual(t['action'], 'ADJUST')
        self.assertEqual(t['type'], 'roc')
        self.assertAlmostEqual(t['net_amount'], -42.50, places=2)
        self.assertEqual(t['quantity'], 0.0)
        self.assertTrue(t['symbol'].startswith('XEI'))

    def test_plain_dividend_still_dividend(self):
        csv = (self.HDR +
               '2025-03-31 09:30:00 AM,2025-03-31 12:00:00 AM,DIV,XEI.TO,'
               'ISHARES S&P/TSX COMP HIGH DIV CASH DIV ON 500 SHS,'
               '0,0.00,0.00,0.00,42.50,CAD\n')
        txs = _parse_with(QuestradeBrokerage(), csv)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['action'], 'DIVIDEND')
        self.assertAlmostEqual(txs[0]['net_amount'], 42.50, places=2)


class TestRbcRoc(unittest.TestCase):
    HDR = ("Date,Activity,Symbol,Description,Quantity,Price,"
           "Settlement Date,Currency,Value,Amount\n")

    def test_roc_row_becomes_negative_adjust_with_market_suffix(self):
        content = (self.HDR +
                   # Trade row teaches the symbol's market currency (CAD →
                   # .TO) so the ROC lands on the same pool identity even
                   # though this ROC is paid in CAD anyway.
                   "01/12/2024,Buy,XRE,ISHARES REIT ETF - Buy,100,15.00,"
                   "01/15/2024,CAD,-1500.00,-1509.95\n"
                   "03/28/2024,Distributions,XRE,ISHARES REIT ETF "
                   "RETURN OF CAPITAL ON 100 SHS,0,0.00,"
                   "03/28/2024,CAD,0.00,12.34\n")
        txs = _parse_with(RbcBrokerage(), content)
        adjusts = [t for t in txs if t['action'] == 'ADJUST']
        self.assertEqual(len(adjusts), 1)
        t = adjusts[0]
        self.assertEqual(t['type'], 'roc')
        self.assertAlmostEqual(t['net_amount'], -12.34, places=2)
        self.assertEqual(t['symbol'], 'XRE.TO')
        # And no dividend income was booked for the ROC row.
        self.assertFalse([t for t in txs if t['action'] == 'DIVIDEND'])

    def test_plain_distribution_still_dividend(self):
        content = (self.HDR +
                   "03/28/2024,Distributions,XRE,ISHARES REIT ETF "
                   "CASH DIST ON 100 SHS,0,0.00,"
                   "03/28/2024,CAD,0.00,12.34\n")
        txs = _parse_with(RbcBrokerage(), content)
        self.assertEqual([t['action'] for t in txs], ['DIVIDEND'])


class TestIbRoc(unittest.TestCase):
    STMT = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n')
    TRADES_HDR = (
        'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
        'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
        'Realized P/L,MTM P/L,Code\n')
    DIV_HDR = 'Dividends,Header,Currency,Account,Date,Description,Amount\n'

    def _parse(self, body):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                         delete=False) as f:
            f.write(self.STMT + body)
            fname = f.name
        try:
            return IbBrokerage().parse_file(Path(fname))
        finally:
            os.remove(fname)

    def test_roc_row_becomes_negative_adjust(self):
        body = (self.DIV_HDR +
                'Dividends,Data,USD,U1,2026-06-30,'
                'HR(US1234567890) Return of Capital USD 0.12 per Share,'
                '24.00\n')
        txs = self._parse(body)
        adjusts = [t for t in txs if t['action'] == 'ADJUST']
        self.assertEqual(len(adjusts), 1)
        t = adjusts[0]
        self.assertEqual(t['type'], 'roc')
        self.assertAlmostEqual(t['net_amount'], -24.00, places=2)
        self.assertEqual(t['symbol'], 'HR.US')
        self.assertFalse([x for x in txs if x['action'] == 'DIVIDEND'])

    def test_roc_reversal_row_nets_positive_adjust(self):
        # IB posts re-characterizations as a negative reversal plus a
        # corrected row; the reversal must become a POSITIVE adjust so
        # the pair nets out.
        body = (self.DIV_HDR +
                'Dividends,Data,USD,U1,2026-06-30,'
                'HR(US1234567890) Return of Capital USD 0.12 per Share,'
                '-24.00\n')
        txs = self._parse(body)
        adjusts = [t for t in txs if t['action'] == 'ADJUST']
        self.assertEqual(len(adjusts), 1)
        self.assertAlmostEqual(adjusts[0]['net_amount'], 24.00, places=2)

    def test_strc_style_split_posting_both_legs_reclassified(self):
        # Real-data shape: a distribution split between "Cash Dividend ...
        # (Return of Capital)" and "Payment in Lieu of Dividend (Return of
        # Capital)" because part of the position was lent out. IB's ROC
        # marker on BOTH legs means the whole distribution is capital
        # returned — both become ADJUSTs, no income is booked.
        body = (self.DIV_HDR +
                'Dividends,Data,USD,U1,2026-06-30,'
                'STRC(US5949728530) Cash Dividend USD 0.958333 per Share '
                '(Return of Capital),886.46\n'
                'Dividends,Data,USD,U1,2026-06-30,'
                'STRC(US5949728530) Payment in Lieu of Dividend '
                '(Return of Capital),551.04\n')
        txs = self._parse(body)
        adjusts = [t for t in txs if t['action'] == 'ADJUST']
        self.assertEqual(len(adjusts), 2)
        self.assertAlmostEqual(sum(t['net_amount'] for t in adjusts),
                               -1437.50, places=2)
        self.assertFalse([t for t in txs
                          if t['action'] in ('DIVIDEND',
                                             'DIVIDEND_IN_LIEU')])

    def test_roc_adjust_rebinds_to_held_listing(self):
        # Irish-domiciled name held as .US: the ISIN stamps the ROC .L,
        # the reattribution pass must rebind it to the held listing —
        # otherwise the ADJUST would reduce the ACB of a phantom symbol.
        body = (self.TRADES_HDR +
                'Trades,Data,Order,Stocks,USD,STX,"2026-01-05, 09:30:00",'
                '100,10.00,0,1000,1,0,0,0,O\n' +
                self.DIV_HDR +
                'Dividends,Data,USD,U1,2026-06-30,'
                'STX(IE00B58JVZ52) Return of Capital USD 0.20 per Share,'
                '20.00\n')
        txs = self._parse(body)
        adjusts = [t for t in txs if t['action'] == 'ADJUST']
        self.assertEqual(len(adjusts), 1)
        self.assertEqual(adjusts[0]['symbol'], 'STX.US')


class TestNegativeAcbWarning(unittest.TestCase):
    """ITA s.40(3): ROC pushing ACB below zero is a deemed gain — the
    engine flags it (does not compute it)."""

    def _run(self, adjust_amount):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from taxjson.lib.core import CanadaTaxRules, TaxTransaction
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-10',
                           symbol='XEI.TO', quantity=100, currency='CAD',
                           price=10.0, net_amount=1000.0),
            TaxTransaction(action='ADJUST', date='2025-06-30',
                           symbol='XEI.TO', quantity=0, currency='CAD',
                           net_amount=adjust_amount, type='roc'),
        ]
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            CanadaTaxRules().compute_gains(txs)
        return err.getvalue()

    def test_negative_acb_flags_s40_3(self):
        err = self._run(-1200.0)          # ROC exceeds the $1000 ACB
        # 2026-09: booked, not just flagged — a deemed gain of the excess
        # in the distribution year, ACB reset to nil.
        self.assertIn("exceeded the ACB by 200.00", err)
        self.assertIn("s.40(3)", err)

    def test_normal_roc_is_quiet(self):
        err = self._run(-200.0)
        self.assertNotIn("s.40(3)", err)


class TestRocSumView(unittest.TestCase):
    """cmd_roc_sum over a scaffolded work/ dir: broker-classified vs manual
    rows, sign presentation (capital returned = positive), currency totals."""

    def test_aggregation(self):
        import argparse
        import io
        import json
        from contextlib import redirect_stdout
        from taxjson.bin.taxjson_run import cmd_roc_sum

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n')
            work = root / "work"
            work.mkdir()
            (work / "margin_raw.json").write_text(json.dumps({
                "transactions": [
                    {"action": "ADJUST", "date": "2025-03-31",
                     "symbol": "XEI.TO", "currency": "CAD",
                     "net_amount": -42.50, "type": "roc", "quantity": 0},
                    {"action": "ADJUST", "date": "2025-06-30",
                     "symbol": "XEI.TO", "currency": "CAD",
                     "net_amount": -10.00, "quantity": 0},   # manual .tt
                    {"action": "DIVIDEND", "date": "2025-06-30",
                     "symbol": "XEI.TO", "currency": "CAD",
                     "net_amount": 99.0, "quantity": 0},     # excluded
                    {"action": "ADJUST", "date": "2024-03-31",
                     "symbol": "XEI.TO", "currency": "CAD",
                     "net_amount": -5.00, "type": "roc",
                     "quantity": 0},                          # wrong year
                ]}))
            args = argparse.Namespace(dir=str(root), period=None,
                                      account=None)
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_roc_sum(args)
            text = out.getvalue()
        self.assertIn("XEI.TO", text)
        self.assertIn("52.50", text)          # 42.50 + 10.00, 2024 excluded
        self.assertIn("TOTAL CAPITAL RETURNED", text)


class TestUsEngineRocAdjust(unittest.TestCase):
    """USATaxRules must apply ADJUST rows to open-lot basis (IRC
    §301(c)(2) nondividend distribution). Before the fix these rows fell
    through the qty-epsilon skip and were silently dropped, understating
    gains at sale."""

    @staticmethod
    def _tx(**kw):
        from taxjson.lib.core import TaxTransaction
        return TaxTransaction(**kw)

    def _run(self, txs):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from taxjson.lib.core import USATaxRules
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            res = USATaxRules().compute_gains(txs)
        return res, err.getvalue()

    def test_roc_reduces_basis_before_sale(self):
        res, err = self._run([
            self._tx(action='BUYSELL', date='2025-01-10', symbol='STRC.US',
                     quantity=100, currency='USD', price=10.0,
                     net_amount=1000.0),
            self._tx(action='ADJUST', date='2025-06-30', symbol='STRC.US',
                     quantity=0, currency='USD', net_amount=-500.0,
                     type='roc'),
            self._tx(action='BUYSELL', date='2025-12-01', symbol='STRC.US',
                     quantity=-100, currency='USD', price=10.0,
                     net_amount=1000.0),
        ])
        gains = [g for g in res['transactions'] if g.get('qty')]
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['cost'], 500.0, places=2)
        self.assertAlmostEqual(gains[0]['gain'], 500.0, places=2)
        self.assertNotIn("NEGATIVE", err)

    def test_roc_apportions_per_share_across_lots(self):
        # 100 sh @ $10 and 300 sh @ $20: a -$400 ROC is $1/sh, so the
        # lots carry $900 and $5700. FIFO sale of 100 must consume $900.
        res, _ = self._run([
            self._tx(action='BUYSELL', date='2025-01-10', symbol='O.US',
                     quantity=100, currency='USD', price=10.0,
                     net_amount=1000.0),
            self._tx(action='BUYSELL', date='2025-02-10', symbol='O.US',
                     quantity=300, currency='USD', price=20.0,
                     net_amount=6000.0),
            self._tx(action='ADJUST', date='2025-06-30', symbol='O.US',
                     quantity=0, currency='USD', net_amount=-400.0,
                     type='roc'),
            self._tx(action='BUYSELL', date='2025-12-01', symbol='O.US',
                     quantity=-100, currency='USD', price=15.0,
                     net_amount=1500.0),
        ])
        gains = [g for g in res['transactions'] if g.get('qty')]
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0]['cost'], 900.0, places=2)
        # Remaining inventory keeps the other $5700 of basis.
        inv = [i for i in res['inventory'] if i['symbol'] == 'O.US']
        self.assertEqual(len(inv), 1)
        self.assertAlmostEqual(inv[0]['total_cost'], 5700.0, places=2)

    def test_negative_lot_basis_flags_301c3(self):
        _, err = self._run([
            self._tx(action='BUYSELL', date='2025-01-10', symbol='STRC.US',
                     quantity=100, currency='USD', price=10.0,
                     net_amount=1000.0),
            self._tx(action='ADJUST', date='2025-06-30', symbol='STRC.US',
                     quantity=0, currency='USD', net_amount=-1200.0,
                     type='roc'),
        ])
        self.assertIn("NEGATIVE", err)
        self.assertIn("301(c)(3)", err)

    def test_roc_after_full_exit_warns_and_skips(self):
        res, err = self._run([
            self._tx(action='BUYSELL', date='2025-01-10', symbol='STRC.US',
                     quantity=100, currency='USD', price=10.0,
                     net_amount=1000.0),
            self._tx(action='BUYSELL', date='2025-03-10', symbol='STRC.US',
                     quantity=-100, currency='USD', price=12.0,
                     net_amount=1200.0),
            self._tx(action='ADJUST', date='2025-06-30', symbol='STRC.US',
                     quantity=0, currency='USD', net_amount=-50.0,
                     type='roc'),
        ])
        self.assertIn("no open long lots", err)
        gains = [g for g in res['transactions'] if g.get('qty')]
        self.assertAlmostEqual(gains[0]['gain'], 200.0, places=2)


if __name__ == "__main__":
    unittest.main()
