"""Questrade, RBC Direct and IB parser fixes from the 2026-10-29 audits,
round 2 (group brokerparse). Every fixture is SYNTHETIC: fake account
ids (55500001, 99900001), invented tickers.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from test_fix_rbc import row as rbc_row, parse_files as rbc_parse_files

QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
ACCT = '55500001'   # pii-ok (synthetic)
ACCT2 = '55500002'  # pii-ok (synthetic)


def qrow(td='2025-02-03', action='Buy', sym='SYN', desc='SYN CORP',
         qty='10', price='10', gross='-100', comm='0', net='-100',
         cur='CAD', acct=ACCT, act='Trades', atype='Individual margin'):
    """One Questrade export row (dates as YYYY-MM-DD)."""
    dt = f"{td} 12:00:00 AM"
    cells = [dt, dt, action, sym, desc, qty, price, gross, comm, net, cur,
             acct, act, atype]
    return ','.join(f'"{c}"' if ',' in c else c for c in cells) + '\n'


def qrei(td, cur, qty, net, acct=ACCT, sym='SYN'):
    return qrow(td=td, action='REI', sym=sym, desc='SYN CORP REINV@C$10',
                qty=str(qty), price='10', gross='0', comm='0', net=str(net),
                cur=cur, acct=acct, act='Dividend reinvestment')


def qt_parse(files, taxable=None, combined=False, country=None):
    """Parse {name: body} as ONE account, the way taxjson-brokerage does
    (one shared context, one parser per file, names in order)."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, body in sorted(files.items()):
            p = Path(d) / name
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        err = io.StringIO()
        txs = []
        with contextlib.redirect_stderr(err):
            ctx = QuestradeBrokerage.prepare_files(paths)
            for p in paths:
                par = QuestradeBrokerage()
                par.account_context = ctx
                par.country = country
                par.combined_broker_accounts = combined
                if taxable is not None:
                    par.account_taxable = taxable
                txs.extend(par.parse_file(p))
    return txs, err.getvalue()


def _buys(txs):
    return sorted((t['date'], t['symbol'], t['currency'], t['quantity'])
                  for t in txs if t.get('action') == 'BUYSELL')


class TestReversalsMatchCurrencyAndAccount(unittest.TestCase):
    """TJ-0517 / TJ-0518: a reinvestment reversal cancels only an
    original of the same currency and broker account."""

    def test_questrade_usd_reversal_leaves_the_cad_reinvestment(self):
        txs, _err = qt_parse({
            'a.csv': (qrei('2025-01-02', 'USD', 10, -100)
                      + qrei('2025-02-03', 'CAD', 10, -100)
                      + qrei('2025-03-03', 'USD', -10, 100))})
        self.assertEqual(_buys(txs),
                         [('2025-02-03', 'SYN.TO', 'CAD', 10.0)])

    def test_questrade_reversal_in_another_file_same_currency(self):
        # The control: an original in last year's export is cancelled.
        txs, _err = qt_parse({
            'a.csv': qrei('2025-01-02', 'USD', 10, -100),
            'b.csv': (qrei('2025-02-03', 'CAD', 10, -100)
                      + qrei('2025-03-03', 'USD', -10, 100))})
        self.assertEqual(_buys(txs),
                         [('2025-02-03', 'SYN.TO', 'CAD', 10.0)])

    def test_questrade_reversal_of_another_account_is_refused(self):
        body = (qrei('2025-01-02', 'CAD', 10, -100, acct=ACCT)
                + qrei('2025-03-03', 'CAD', -10, 100, acct=ACCT2))
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse({'a.csv': body}, combined=True)
        self.assertIn("reversal", str(cm.exception))

    def test_rbc_reversal_of_another_account_is_refused(self):
        def rei(day, acct, qty, value):
            return rbc_row(day, 'Dividends', 'SYN', 'SYN CORP', str(qty), '',
                           str(value), 'CAD',
                           'REI - SYN CORP REINV@C$10'
                           + (' CANCEL' if qty < 0 else ''), acct=acct)
        with self.assertRaises(ValueError) as cm:
            rbc_parse_files({'a.csv': rei('January 2, 2025', ACCT, 10, -100),
                             'b.csv': rei('March 3, 2025', ACCT2, -10, 100)})
        self.assertIn("reversed reinvestment", str(cm.exception))
        # The same account's original is still cancelled.
        txs, _err, _ = rbc_parse_files({
            'a.csv': rei('January 2, 2025', ACCT, 10, -100),
            'b.csv': rei('March 3, 2025', ACCT, -10, 100)})
        self.assertEqual([t for t in txs if t.get('action') == 'BUYSELL'],
                         [])


def _attention(err):
    return [ln for ln in err.splitlines() if 'ATTENTION' in ln]


class TestQuestradePlanGuardAcrossFiles(unittest.TestCase):
    """TJ-0527 / TJ-0477: the taxable-versus-registered check holds for
    an export of one account and across the account's exports."""

    def _buy(self, acct, atype, td='2025-01-15'):
        return qrow(td=td, sym='QZEQ.TO', desc='QZEQ ETF WE ACTED AS AGENT',
                    qty='10', price='30', gross='-300', net='-300',
                    acct=acct, atype=atype)

    def test_tfsa_export_beside_a_margin_export_is_refused(self):
        files = {'margin.csv': self._buy(ACCT, 'Individual margin'),
                 'tfsa.csv': self._buy(ACCT2, 'Individual TFSA',
                                       '2025-02-18')}
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(files, taxable=True)
        msg = str(cm.exception)
        self.assertIn("registered plan", msg)
        self.assertNotIn(ACCT2, msg)                      # masked
        # The same rows in ONE export are refused as before.
        with self.assertRaises(BrokerageParseError):
            qt_parse({'both.csv': files['margin.csv'] + files['tfsa.csv']},
                     taxable=True)

    def test_registered_export_alone_in_a_taxable_account_is_said(self):
        # Two downloads of one LIRA: ONE line for the account.
        txs, err = qt_parse({'lira_2024.csv': self._buy(ACCT2,
                                                        'Individual LIRA'),
                             'lira_2025.csv': self._buy(
                                 ACCT2, 'Individual LIRA', '2025-03-03')},
                            taxable=True)
        self.assertEqual(len(txs), 2)                  # booked as declared
        att = _attention(err)
        self.assertEqual(len(att), 1, err)
        self.assertIn("registered plan (Individual LIRA)", att[0])
        self.assertIn('type = "sheltered"', att[0])
        self.assertNotIn(ACCT2, err)
        # A margin export alone in a sheltered account: the reverse.
        _txs, err = qt_parse({'m.csv': self._buy(ACCT, 'Individual margin')},
                             taxable=False)
        att = _attention(err)
        self.assertEqual(len(att), 1, err)
        self.assertIn("a taxable account (Individual margin)", att[0])
        # Each where it belongs: nothing said.
        _txs, err = qt_parse({'lira.csv': self._buy(ACCT2,
                                                    'Individual LIRA')},
                             taxable=False)
        self.assertEqual(_attention(err), [])
        _txs, err = qt_parse({'m.csv': self._buy(ACCT, 'Individual margin')},
                             taxable=True)
        self.assertEqual(_attention(err), [])

    def test_two_taxable_accounts_in_separate_exports_are_said_once(self):
        files = {'a.csv': self._buy(ACCT, 'Individual margin'),
                 'b.csv': self._buy(ACCT2, 'Joint margin', '2025-02-18')}
        txs, err = qt_parse(files, taxable=True)
        self.assertEqual(len(txs), 2)
        att = _attention(err)
        self.assertEqual(len(att), 1, err)
        self.assertIn("2 Questrade accounts", att[0])
        self.assertNotIn(ACCT, err)
        # combined_broker_accounts = true: a note instead.
        txs, err = qt_parse(files, taxable=True, combined=True)
        self.assertEqual(len(txs), 2)
        self.assertEqual(_attention(err), [])
        self.assertIn("booked together", err)

    def test_one_account_in_two_downloads_says_nothing(self):
        files = {'a.csv': self._buy(ACCT, 'Individual margin'),
                 'b.csv': self._buy(ACCT, 'Individual margin',
                                    '2025-02-18')}
        _txs, err = qt_parse(files, taxable=True)
        self.assertEqual(_attention(err), [])

    def test_two_plans_in_a_combined_sheltered_account_are_refused(self):
        files = {'a.csv': self._buy(ACCT, 'Individual TFSA'),
                 'b.csv': self._buy(ACCT2, 'Individual RRSP', '2025-02-18')}
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(files, taxable=False, combined=True)
        self.assertIn("Account Types differ", str(cm.exception))


class TestOptionContractsAreReal(unittest.TestCase):
    """TJ-0519 / TJ-0520: an option description with an impossible
    expiry, or a strike the OCC symbol cannot hold, is refused with its
    row instead of becoming a contract's symbol."""

    def _opt(self, desc):
        return qrow(action='Buy', sym='SYN19Dec25C10.00', desc=desc,
                    qty='1', price='10', gross='-1000', comm='-1',
                    net='-1001', cur='CAD')

    def test_impossible_expiry_is_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse({'a.csv': self._opt('CALL SYN 02/30/25 10 SYN CORP')})
        msg = str(cm.exception)
        self.assertIn("a.csv line 2", msg)
        self.assertIn("02/30/25 is not a calendar date", msg)

    def test_strike_finer_than_the_occ_field_is_refused(self):
        for strike in ('10.0009', '0', '100000'):
            with self.assertRaises(BrokerageParseError, msg=strike) as cm:
                qt_parse({'a.csv': self._opt(
                    f'CALL SYN 12/19/25 {strike} SYN CORP')})
            self.assertIn("a.csv line 2", str(cm.exception))
            self.assertIn("no OCC spelling", str(cm.exception))

    def test_real_contracts_still_read(self):
        txs, _err = qt_parse({'a.csv': self._opt(
            'CALL SYN 12/19/25 10.125 SYN CORP')})
        self.assertEqual([t['symbol'] for t in txs],
                         ['SYN251219C00010125.TO'])
        from taxjson.lib.brokerages.base import encode_occ_strike
        self.assertEqual(encode_occ_strike('99999.999'), '99999999')
        self.assertEqual(encode_occ_strike('0.001'), '00000001')
        self.assertEqual(encode_occ_strike(4.02), '00004020')
        for bad in ('10.0009', '0', '-5', '100000', 'nan', 'inf', 'x'):
            with self.assertRaises(BrokerageParseError, msg=bad):
                encode_occ_strike(bad)

    def test_format_occ_symbol_names_the_row(self):
        b = QuestradeBrokerage()
        with self.assertRaises(BrokerageParseError) as cm:
            b.format_occ_symbol('P', 'SYN', '13/01/25', '5', where='x.csv:7')
        self.assertTrue(str(cm.exception).startswith("x.csv:7: option PUT"),
                        str(cm.exception))
        self.assertEqual(b.format_occ_symbol('P', 'SYN', '2/28/24', '5'),
                         'SYN240228P00005000')


class TestIbIncomeListingThroughSplits(unittest.TestCase):
    """TJ-0521: the position replay that dates which listing an income
    row belongs to applies splits and ticker changes."""

    def _statement(self, split):
        from test_fix_ibparse import HEAD, TRADES_H, CA_H, DIV_H, _trade, _ca
        s = (HEAD + TRADES_H
             + _trade('SYN', '2025-01-02, 10:00:00', 10, 10, -100)
             + _trade('SYN', '2025-01-15, 10:00:00', -10, 11, 110)
             + _trade('SYN', '2025-02-03, 10:00:00', 10, 10, -100, cur='CAD')
             + _trade('SYN', '2025-02-05, 10:00:00', -20 if split else -10,
                      10, 200 if split else 100, cur='CAD'))
        if split:
            s += CA_H + _ca('SYN(CA9990000001) Split 2 for 1 (SYN, SYN CORP, '
                            'CA9990000001)', 10, cur='CAD',
                            when='2025-02-04, 10:00:00')
        return s + (DIV_H + 'Dividends,Data,USD,2025-01-10,"SYN(CA9990000001)'
                    ' Return of Capital USD 1 per Share",10\n')

    def test_january_roc_stays_on_the_listing_held_then(self):
        from test_fix_ibparse import _parse_ib
        for split in (False, True):
            _p, txs, _err = _parse_ib(self._statement(split))
            roc = [t['symbol'] for t in txs if t.get('type') == 'roc']
            self.assertEqual(roc, ['SYN.US'], f"split={split}")

    def test_replay(self):
        from taxjson.lib.brokerages.ib_extractor import _listing_replay

        def tx(action, day, sym, qty, new=None):
            t = {'action': action, 'date': day, 'symbol': sym,
                 'quantity': qty}
            if new:
                t['symbol_new'] = new
            return t
        # Bought 10, split 2-for-1, sold 20: nothing held before.
        held = _listing_replay([tx('BUYSELL', '2025-02-03', 'SYN.TO', 10),
                                tx('SPLIT', '2025-02-04', 'SYN.TO', 2.0,
                                   'SYN.TO'),
                                tx('BUYSELL', '2025-02-05', 'SYN.TO', -20)])
        self.assertFalse(held('SYN', 'TO', '2025-01-10'))
        self.assertTrue(held('SYN', 'TO', '2025-02-04'))
        self.assertFalse(held('SYN', 'TO', '2025-02-06'))
        # The Open Positions quantity counts in post-split units.
        held = _listing_replay([tx('BUYSELL', '2025-02-03', 'SYN.TO', 10),
                                tx('SPLIT', '2025-02-04', 'SYN.TO', 2.0,
                                   'SYN.TO')], {'SYN.TO': 20.0})
        self.assertFalse(held('SYN', 'TO', '2025-01-10'))
        held = _listing_replay([tx('BUYSELL', '2025-02-03', 'SYN.TO', 10),
                                tx('SPLIT', '2025-02-04', 'SYN.TO', 2.0,
                                   'SYN.TO')], {'SYN.TO': 30.0})
        self.assertTrue(held('SYN', 'TO', '2025-01-10'))     # 5 before
        # A ticker change: the units leave QZO.US and arrive on QZN.US;
        # the sale on QZN needs no earlier holding of it.
        held = _listing_replay([tx('BUYSELL', '2025-01-02', 'QZO.US', 10),
                                tx('SPLIT', '2025-03-03', 'QZO.US', 1.0,
                                   'QZN.US'),
                                tx('BUYSELL', '2025-04-01', 'QZN.US', -10)])
        self.assertFalse(held('QZN', 'US', '2025-02-01'))
        self.assertTrue(held('QZN', 'US', '2025-03-15'))
        self.assertTrue(held('QZO', 'US', '2025-02-01'))
        self.assertFalse(held('QZO', 'US', '2025-03-15'))
        # No trade of a listing: no dated evidence, never excluded.
        self.assertTrue(held('QZX', 'US', '2025-02-01'))


if __name__ == '__main__':
    unittest.main()
