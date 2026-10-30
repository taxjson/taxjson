"""Fix round 7 wave B (2026-10-29), group qtrbc: Questrade and RBC
Direct parsers (backlog TJ-0761, TJ-0664, TJ-0665, TJ-0672, TJ-0741).
Synthetic data only: made-up tickers and synthetic accounts."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from test_fix_rbcqt import QH, RH, of, q, qt_parse, rbc_parse, rrow

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage

from taxjson.lib.income_dating import IncomeRules


class RbcInKindReinvestFacts(unittest.TestCase):
    """TJ-0741: an in-kind reinvested distribution keeps the record
    date and distribution label a cash distribution gets."""

    @rule("CA-INC-DATE-TRUST")
    def test_in_kind_reinvestment_keeps_record_date(self):
        desc = ('DIV - SYN TRUST DIST ON 100 SHS REC 12/31/24 PAY 01/15/25'
                ' Reinvest @ $10.00')
        line = rrow('January 15, 2025', 'Dividends', 'SYN.UN', 'SYN TRUST',
                    '2', '', '', 'CAD', desc)
        txs, _err, _ = rbc_parse(line)
        div = of(txs, action='DIVIDEND')
        self.assertEqual(len(div), 1)
        self.assertEqual(div[0].get('record_date'), '2024-12-31')
        self.assertEqual(div[0].get('income_label'), 'distribution')
        self.assertEqual(IncomeRules('canada').income_date(div[0]),
                         '2024-12-31')
        buy = of(txs, action='BUYSELL')
        self.assertEqual(len(buy), 1)
        self.assertEqual(buy[0]['date'], '2025-01-15')
        self.assertNotIn('record_date', buy[0])


# ------------------------------------------------------------- Questrade

def qtrade(day, n, sym='SYN.TO', acct=None):
    row = q(td=day, action='Buy' if n > 0 else 'Sell', sym=sym,
            desc='SYN CORP', qty=str(n), price='10', gross=str(-n * 10),
            comm='0', net=str(-n * 10), cur='CAD')
    return row.replace('55500001', acct) if acct else row


def qsplit(day, held, delta, sym='SYN.TO', acct=None):
    row = q(td=day, action='DIS', sym=sym,
            desc=f'SYN CORP STK SPLIT ON {held} SHS', qty=str(delta),
            price='0', gross='0', comm='0', net='0', cur='CAD',
            act='Dividends')
    return row.replace('55500001', acct) if acct else row


def qt_combined(*bodies):
    """qt_parse for an account label with combined_broker_accounts."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, body in enumerate(bodies):
            p = Path(d) / f"questrade_{2025 + i}.csv"
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        err = io.StringIO()
        txs = []
        with contextlib.redirect_stderr(err):
            ctx = QuestradeBrokerage.prepare_files(paths)
            for p in paths:
                par = QuestradeBrokerage()
                par.account_context = ctx
                par.account_taxable = True
                par.combined_broker_accounts = True
                txs.extend(par.parse_file(p))
    return txs, err.getvalue()


def ratios(txs):
    return [round(t['quantity'], 9) for t in of(txs, action='SPLIT')]


class QtSplitDirection(unittest.TestCase):
    """A split on a short debits shares; on a long, a negative quantity
    is a reverse split. The position the parser reads must be the
    broker account's real one."""

    def test_overlapping_export_does_not_count_a_sale_twice(self):
        """TJ-0664: the sale of 75 in a second download is one sale."""
        sell = qtrade('2025-02-03', -75)
        body = (qtrade('2025-01-02', 100) + sell
                + qsplit('2025-03-03', 25, 5) + qtrade('2025-04-03', -30))
        txs, _err, _ = qt_parse(body, sell, taxable=True)
        self.assertEqual(ratios(txs), [1.2])

    def test_earlier_split_counts_in_the_position(self):
        """TJ-0665: short 100, a 2-for-1 debits 100, cover 150: still
        short 50, so a split debiting 10 is a 6-for-5."""
        body = (qtrade('2025-01-02', -100) + qsplit('2025-02-03', 100, -100)
                + qtrade('2025-03-03', 150) + qsplit('2025-04-03', 50, -10)
                + qtrade('2025-05-05', 60))
        txs, _err, _ = qt_parse(body, taxable=True)
        self.assertEqual(ratios(txs), [2.0, 1.2])

    def test_each_broker_account_splits_its_own_position(self):
        """TJ-0672: account A long 100, account B short 50."""
        a, b = '99900001', '99900002'
        body = (qtrade('2025-01-02', 100, acct=a)
                + qtrade('2025-01-06', -50, acct=b)
                + qsplit('2025-03-03', 100, 50, acct=a)
                + qsplit('2025-03-03', 50, -25, acct=b)
                + qtrade('2025-04-03', -150, acct=a)
                + qtrade('2025-05-05', 75, acct=b))
        txs, _err = qt_combined(body)
        self.assertEqual(ratios(txs), [1.5, 1.5])

    def test_reverse_split_on_a_long_unchanged(self):
        body = qtrade('2025-01-02', 100) + qsplit('2025-03-03', 100, -90)
        txs, _err, _ = qt_parse(body, taxable=True)
        self.assertEqual(ratios(txs), [0.1])


def qrei(day, sym, n, desc='SYN CORP REINV@C$10'):
    return q(td=day, action='REI', sym=sym, desc=desc, qty=str(n),
             price='10', gross=str(-10 * n), comm='0', net=str(-10 * n),
             cur='CAD', act='Dividend reinvestment', atype='Cash')


class QtReversalSymbolSpellings(unittest.TestCase):
    """TJ-0761: a reinvestment and its reversal pair on the security
    they are booked on, however the Symbol cell spells it."""

    BUY = q(td='2025-01-06', action='Buy', sym='SYN',
            desc='SYN CORP WE ACTED AS AGENT', qty='10', price='10',
            gross='-100', comm='0', net='-100', cur='CAD', atype='Cash')

    def booked(self, *bodies):
        txs, _err, _ = qt_parse(*bodies, taxable=True)
        return [(t['symbol'], t['quantity']) for t in of(txs,
                                                         action='BUYSELL')]

    def test_bare_original_explicit_reversal(self):
        body = (self.BUY + qrei('2025-06-02', 'SYN', 2)
                + qrei('2025-06-03', 'SYN.TO', -2))
        self.assertEqual(self.booked(body), [('SYN.TO', 10.0)])

    def test_across_two_exports(self):
        self.assertEqual(
            self.booked(self.BUY + qrei('2025-06-02', 'SYN', 2),
                        qrei('2025-06-03', 'SYN.TO', -2)),
            [('SYN.TO', 10.0)])

    def test_internal_code_original(self):
        body = (self.BUY + qrei('2025-06-02', 'S000001', 2)
                + qrei('2025-06-03', 'SYN', -2))
        self.assertEqual(self.booked(body), [('SYN.TO', 10.0)])

    def test_controls_still_pair(self):
        for orig, rev in (('SYN', 'SYN'), ('SYN.TO', 'SYN.TO')):
            body = (self.BUY + qrei('2025-06-02', orig, 2)
                    + qrei('2025-06-03', rev, -2))
            self.assertEqual(self.booked(body), [('SYN.TO', 10.0)])

    def test_another_security_is_still_refused(self):
        body = (self.BUY + qrei('2025-06-02', 'SYN', 2)
                + qrei('2025-06-03', 'OTH.TO', -2, desc='OTH CORP REINV@C$10'))
        with self.assertRaises(BrokerageParseError):
            qt_parse(body, taxable=True)


# ------------------------------------------------------------- RBC Direct

def rtrade(day, n, acct=None):
    row = rrow(day, 'Buy' if n > 0 else 'Sell', 'SYN', 'SYN CORP', str(n),
               '10', str(-10 * n), 'CAD', 'SYN CORP UNSOLICITED DA')
    return row.replace('55500001', acct) if acct else row


def rsplit(day, held, delta, acct=None):
    row = rrow(day, 'Reorganization', 'SYN', 'SYN CORP', str(delta), '',
               '0', 'CAD', f'DIS - SYN CORP STK SPLIT ON {held} SHS')
    return row.replace('55500001', acct) if acct else row


class RbcSplitDirection(unittest.TestCase):

    def test_earlier_split_counts_in_the_position(self):
        """TJ-0665 (RBC)."""
        body = (rtrade('January 2, 2025', -100)
                + rsplit('February 3, 2025', 100, -100)
                + rtrade('March 3, 2025', 150)
                + rsplit('April 3, 2025', 50, -10)
                + rtrade('May 5, 2025', 60))
        txs, _err, _ = rbc_parse(body)
        self.assertEqual(ratios(txs), [2.0, 1.2])

    def test_each_broker_account_splits_its_own_position(self):
        """TJ-0672 (RBC)."""
        a, b = '99900001', '99900002'
        body = (rtrade('January 2, 2025', 100, acct=a)
                + rtrade('January 6, 2025', -50, acct=b)
                + rsplit('March 3, 2025', 100, 50, acct=a)
                + rsplit('March 3, 2025', 50, -25, acct=b)
                + rtrade('April 3, 2025', -150, acct=a)
                + rtrade('May 5, 2025', 75, acct=b))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'rbc_2025.csv'
            p.write_text(RH + body, encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                ctx = RbcBrokerage.prepare_files([p], combined=True,
                                                 taxable=True)
                par = RbcBrokerage()
                par.combined_broker_accounts = True
                par.account_context = ctx
                txs = par.parse_file(p)
        self.assertEqual(ratios(txs), [1.5, 1.5])


if __name__ == '__main__':
    unittest.main()
