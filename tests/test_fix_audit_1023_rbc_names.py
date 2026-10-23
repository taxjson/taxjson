"""Workflow audit 2026-10-23, item 3: RBC company-name lookups in an
export with no 'Symbol Description' column (the demo's layout). Every
lookup goes through `corp_actions.rbc_row_company`, which falls back to
the row's own Description; reading the optional column alone left a
name change under a temporary code unresolved.

Every fixture is SYNTHETIC: account 99900001, made-up tickers YY* / ZZ*.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

ACCT = '99900001'
RH_DEMO = ('Date,Activity,Symbol,Description,Quantity,Price,'
           'Settlement Date,Currency,Value,Amount\n')
RH_SD = ('Date,Activity,Symbol,Symbol Description,Description,Quantity,'
         'Price,Settlement Date,Currency,Value,Amount\n')


def _row(day, act, sym, desc, qty, price, value, symdesc=None):
    cells = [day, act, sym]
    if symdesc is not None:
        cells.append(symdesc)
    cells += [desc, qty, price, day, 'CAD', value, value]
    return ','.join(cells) + '\n'


def _nac(symdesc=False):
    """The audit's rows: a buy, an NAC whose removal is under the
    temporary code X000006, and a sale under the new ticker."""
    sd = (lambda s: s) if symdesc else (lambda s: None)
    return (_row('02/15/2024', 'Buy', 'YYK', 'YYK INC - Buy', '50', '20.00',
                 '-1009.95', sd('YYK INC'))
            + _row('05/15/2024', 'Reorganization', 'X000006',
                   'NAC - YYK INC NAME CHANGE TO YYL INC', '-50', '0', '0',
                   sd('YYK INC'))
            + _row('05/15/2024', 'Reorganization', 'YYL',
                   'NAC - YYL INC RESULT OF NAME CHANGE', '50', '0', '0',
                   sd('YYL INC'))
            + _row('08/01/2024', 'Sell', 'YYL', 'YYL INC - Sell', '20',
                   '25.00', '490.05', sd('YYL INC')))


def _write(d, body, header):
    p = Path(d) / 'rbc_2024.csv'
    p.write_text('Account Activity Detail Report\n'
                 f'"Account Number: {ACCT}"\n\n' + header + body)
    return p


def rbc_parse(body, header=RH_DEMO):
    from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, body, header)
        with contextlib.redirect_stderr(err):
            ctx = RbcBrokerage.prepare_files([p])
            par = RbcBrokerage()
            par.account_context = ctx
            txs = par.parse_file(p)
            ctx.emit()
    return txs, err.getvalue()


def rbc_events(body, header=RH_DEMO):
    from taxjson.lib.corp_actions import parse_rbc_corporate_actions
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, body, header)
        with contextlib.redirect_stderr(err):
            evs = parse_rbc_corporate_actions(p, 'margin', context_files=[p])
    return evs, err.getvalue()


def _splits(txs):
    return [(t['symbol'], t['symbol_new'], t['quantity'])
            for t in txs if t['action'] == 'SPLIT']


@rule("CA-ACB-RENAME")
class TestRbcNameChangeWithoutSymbolDescription(unittest.TestCase):

    def test_temporary_code_resolved_from_the_buy_description(self):
        txs, err = rbc_parse(_nac())
        self.assertEqual(_splits(txs), [('YYK.TO', 'YYL.TO', 1.0)], err)
        self.assertNotIn('ASSUMED', err)

    def test_same_as_the_symbol_description_layout(self):
        demo, _ = rbc_parse(_nac())
        control, err = rbc_parse(_nac(symdesc=True), RH_SD)
        self.assertEqual(_splits(control), [('YYK.TO', 'YYL.TO', 1.0)], err)
        self.assertEqual(_splits(demo), _splits(control))

    def test_unrelated_buy_still_warns(self):
        body = _nac().replace('YYK INC - Buy', 'YYQ OTHER HOLDINGS - Buy')
        txs, err = rbc_parse(body)
        self.assertIn('ASSUMED', err)
        self.assertNotIn(('YYK.TO', 'YYL.TO', 1.0), _splits(txs))


class TestRbcTradeUnderTemporaryCode(unittest.TestCase):

    def test_sale_under_temporary_code_takes_the_ticker_of_its_name(self):
        body = (_row('02/15/2024', 'Buy', 'YYK', 'YYK INC - Buy', '50',
                     '20.00', '-1009.95')
                + _row('08/01/2024', 'Sell', 'X000007', 'YYK INC - Sell',
                       '20', '25.00', '490.05'))
        txs, err = rbc_parse(body)
        sells = [t['symbol'] for t in txs
                 if t['action'] == 'BUYSELL' and t['quantity'] < 0]
        self.assertEqual(sells, ['YYK.TO'], err)


@rule("CA-ACB-RENAME")
class TestRbcTickerChangeWithoutSymbolDescription(unittest.TestCase):

    def test_same_name_under_a_new_symbol_is_flagged(self):
        body = (_row('04/15/2024', 'Buy', 'YYA', 'YYA CAPITAL CORP - Buy',
                     '50', '10.00', '-509.95')
                + _row('09/16/2024', 'Sell', 'YYB', 'YYA CAPITAL CORP - Sell',
                       '50', '12.00', '590.05'))
        _, err = rbc_parse(body)
        self.assertIn('RENAME 2024-09-16 YYA.TO YYB.TO', err)
        self.assertIn("same security name 'YYA CAPITAL CORP'", err)


class TestRbcSpinoffCashInLieuWithoutSymbolDescription(unittest.TestCase):

    def test_cash_in_lieu_under_a_temporary_code_joins_its_spinoff(self):
        body = (_row('01/05/2024', 'Buy', 'ZZP', 'ZZP PARENT CORP - Buy',
                     '105', '10.00', '-1050.00')
                + _row('06/15/2024', 'Reorganization', 'ZZS',
                       'DIS - ZZS SPINCO INC SPINOFF ON 105 SHS FROM SEC# '
                       'X123456 ZZP PARENT CORP REC 06/01/24 PAY 06/15/24',
                       '10', '0', '0')
                + _row('06/20/2024', 'Reorganization', 'X000009',
                       'CIL - ZZS SPINCO INC CASH IN LIEU OF 0.5 SHS', '0',
                       '0', '7.50'))
        evs, err = rbc_events(body)
        spins = [e for e in evs if e.action_type == 'spinoff']
        self.assertEqual(len(spins), 1, err)
        self.assertEqual(spins[0].source_symbol, 'ZZP.TO')
        self.assertAlmostEqual(spins[0].cash_in_lieu, 7.5, msg=err)


if __name__ == '__main__':
    unittest.main()
