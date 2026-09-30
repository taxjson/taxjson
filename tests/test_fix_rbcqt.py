"""Questrade and RBC Direct parser fixes from the 2026-09 audit, medium
round (area rbcqt). Every fixture is SYNTHETIC: fake account ids
(55500001), invented tickers and codes.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.questrade import QuestradeBrokerage

REPO = Path(__file__).resolve().parent.parent
QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
ACCT = '55500001'  # pii-ok (synthetic)


def q(td='2025-02-03', sd=None, action='Buy', sym='QZA',
      desc='QZA CORP WE ACTED AS AGENT', qty='10', price='50', gross='-500',
      comm='-4.95', net='-504.95', cur='USD', act='Trades',
      atype='Individual margin'):
    """One Questrade export row (dates as YYYY-MM-DD; '' = blank)."""
    def dt(x):
        return f"{x} 12:00:00 AM" if x else ''
    sd = td if sd is None else sd
    cells = [dt(td), dt(sd), action, sym, desc, qty, price, gross, comm, net,
             cur, ACCT, act, atype]
    return ','.join(f'"{c}"' if ',' in c else c for c in cells) + '\n'


def qdiv(sym, desc, net, td='2025-06-02', cur='USD', qty='0',
         action='DIV'):
    return q(td=td, action=action, sym=sym, desc=desc, qty=qty, price='0',
             gross='0', comm='0', net=net, cur=cur, act='Dividends')


def qt_parse(*bodies, taxable=None):
    """Parse one or more Questrade files as ONE account (the way
    taxjson-brokerage does). Returns (transactions, stderr, parsers)."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, body in enumerate(bodies):
            p = Path(d) / f"questrade_{2025 + i}.csv"
            p.write_text(QH + body, encoding='utf-8')
            paths.append(p)
        err = io.StringIO()
        txs, pars = [], []
        with contextlib.redirect_stderr(err):
            prep = getattr(QuestradeBrokerage, 'prepare_files', None)
            ctx = prep(paths) if prep else None
            for p in paths:
                par = QuestradeBrokerage()
                par.account_context = ctx
                if taxable is not None:
                    par.account_taxable = taxable
                txs.extend(par.parse_file(p))
                pars.append(par)
    return txs, err.getvalue(), pars


def of(txs, **kw):
    return [t for t in txs if all(t.get(k) == v for k, v in kw.items())]


# ------------------------------------------------------------------ Questrade

class TestQtListingSuffix(unittest.TestCase):
    """A .TO listing keeps .TO whatever the row currency (R1-68 and the
    DLR.U.TO item found in round 1)."""

    def test_dlr_u_to_traded_in_usd_keeps_to(self):
        txs, _, _ = qt_parse(
            q(sym='DLR.U.TO', desc='GLOBAL X US DLR CURRENCY ETF WE ACTED '
              'AS AGENT', qty='100', price='10', gross='-1000', comm='0',
              net='-1000'))
        self.assertEqual(txs[0]['symbol'], 'DLR.U.TO')

    def test_xus_u_and_its_code_dividend(self):
        txs, _, _ = qt_parse(
            q(sym='XUS.U.TO', desc='ISHARES CORE S&P 500 INDEX ETF WE ACTED '
              'AS AGENT', qty='10', price='40', gross='-400', comm='0',
              net='-400')
            + qdiv('.XUS', 'ISHARES CORE S&P 500 INDEX ETF DIST ON 10 SHS '
                   'REC 06/01/25 PAY 06/05/25', '1.20'))
        self.assertEqual([t['symbol'] for t in txs],
                         ['XUS.U.TO', 'XUS.U.TO'])

    def test_usd_dividend_on_a_tsx_listing_same_for_both_spellings(self):
        buy = q(sym='FNV.TO', desc='FRANCO NEVADA CORP WE ACTED AS AGENT',
                qty='10', price='200', gross='-2000', comm='0', net='-2000',
                cur='CAD')
        desc = 'FRANCO NEVADA CORP CASH DIV ON 10 SHS REC 06/01/25 PAY 06/05/25'
        api, _, _ = qt_parse(buy + qdiv('FNV.TO', desc, '3.80'))
        web, _, _ = qt_parse(buy + qdiv('.FNV', desc, '3.80'))
        self.assertEqual(api[-1]['symbol'], 'FNV.TO')
        self.assertEqual(web[-1]['symbol'], 'FNV.TO')
        self.assertEqual(api[-1]['currency'], 'USD')   # paid in USD

    def test_cad_income_after_a_cad_settled_us_buy_hits_the_us_pool(self):
        buy = q(sym='XYZ', desc='XYZ CORP WE ACTED AS AGENT EXCHANGE RATE '
                '1.40', qty='10', price='100', gross='-1000', comm='0',
                net='-1400', cur='CAD')
        txs, _, _ = qt_parse(
            buy
            + qdiv('XYZ', 'XYZ CORP CASH DIV ON 10 SHS REC 06/01/25 PAY '
                   '06/05/25', '7.00', cur='CAD')
            + qdiv('XYZ', 'XYZ CORP RETURN OF CAPITAL ON 10 SHS REC '
                   '06/01/25 PAY 06/05/25', '70.00', cur='CAD'))
        self.assertEqual({t['symbol'] for t in txs}, {'XYZ.US'})
        self.assertAlmostEqual(txs[0]['net_amount'], 1400.0)


class TestQtSymbolResolution(unittest.TestCase):
    TRADE = q(sym='ACM.TO', desc='ACME CORP WE ACTED AS AGENT', qty='100',
              price='100', gross='-10000', comm='0', net='-10000', cur='CAD')

    def test_code_rows_in_a_later_file_resolve_account_wide(self):
        """R1-67: the trade in last year's export, the ROC in this one."""
        income = (qdiv('A020626', 'ACME CORP CASH DIV ON 100 SHS REC '
                       '01/15/26 PAY 02/01/26', '40.00', td='2026-02-02',
                       cur='CAD')
                  + qdiv('A020626', 'ACME CORP RETURN OF CAPITAL ON 100 SHS '
                         'REC 01/15/26 PAY 02/01/26', '700.00',
                         td='2026-02-02', cur='CAD'))
        txs, err, _ = qt_parse(self.TRADE, income)
        self.assertEqual({t['symbol'] for t in txs}, {'ACM.TO'})
        self.assertNotIn('internal symbol code', err)

    def test_unresolved_code_is_loud_and_a_lint_finding(self):
        txs, err, pars = qt_parse(
            qdiv('A020626', 'OTHERCO INC RETURN OF CAPITAL ON 100 SHS REC '
                 '01/15/26 PAY 02/01/26', '700.00', cur='CAD'))
        self.assertIn("keeps internal symbol code 'A020626'", err)
        self.assertIn('GLOBAL A020626.TO', err)
        self.assertTrue(pars[0].lint_findings)

    def test_event_rows_under_a_code_bind_to_the_trade(self):
        """S063-03 / S062-24: STK DIV, REI, CIL and split wording."""
        trade = q(sym='TDB.TO', desc='TDB SPLIT CORP SHS CL A NEW WE ACTED AS '
                  'AGENT', qty='1000', price='5', gross='-5000', comm='0',
                  net='-5000', cur='CAD')
        stk = q(td='2025-07-29', action='DIS', sym='T012345',
                desc='TDB SPLIT CORP SHS CL A NEW STK DIV ON 1000 SHS REC '
                     '07/24/25 PAY 07/29/25', qty='20', price='0', gross='0',
                comm='0', net='0', cur='CAD', act='Dividends')
        rei = q(td='2025-08-29', action='REI', sym='T012345',
                desc='TDB SPLIT CORP SHS CL A NEW REINV@C$5.12345', qty='3',
                price='0', gross='0', comm='0', net='-15.37', cur='CAD',
                act='Dividend reinvestment')
        cil = q(td='2025-07-30', action='CIL', sym='T012345',
                desc='TDB SPLIT CORP SHS CL A NEW CASH IN LIEU OF .50000',
                qty='0', price='0', gross='0', comm='0', net='2.50',
                cur='CAD', act='Other')
        split = q(td='2025-09-15', action='DIS', sym='T012345',
                  desc='TDB SPLIT CORP SHS CL A NEW STK SPLIT ON 1023 SHS',
                  qty='1023', price='0', gross='0', comm='0', net='0',
                  cur='CAD', act='Dividends')
        txs, err, _ = qt_parse(trade + stk + rei + cil + split)
        self.assertEqual({t['symbol'] for t in txs}, {'TDB.TO'}, err)
        self.assertNotIn('internal symbol code', err)

    def test_ib_transfer_wording_teaches_the_map(self):
        """S063-09."""
        for wording in ('AGNICO EAGLE MINES LIMITED TRANSFER IN INTERACTIVE '
                        'BROKER',
                        'AGNICO EAGLE MINES LIMITED INTERACTIVE BROKERS LLC '
                        '146.16 TRANSFER'):
            tfi = q(action='TF6', sym='AEM', desc=wording, qty='10',
                    price='0', gross='0', comm='0', net='0', act='Transfers')
            div = qdiv('A012345', 'AGNICO EAGLE MINES LIMITED CASH DIV ON 10 '
                       'SHS REC 06/01/25 PAY 06/15/25', '4.00')
            txs, err, _ = qt_parse(tfi + div)
            self.assertEqual(of(txs, action='DIVIDEND')[0]['symbol'],
                             'AEM.US', wording)

    def test_ticker_change_without_a_reorg_row_is_flagged(self):
        """S062-06 / S063-12."""
        body = (q(sym='QQOL', desc='QQ HOLDINGS CORP WE ACTED AS AGENT',
                  qty='500', price='10', gross='-5000', comm='0',
                  net='-5000')
                + q(td='2025-09-10', action='Sell', sym='QQNW',
                    desc='QQ HOLDINGS CORP WE ACTED AS AGENT', qty='-500',
                    price='20', gross='10000', comm='0', net='10000'))
        _, err, _ = qt_parse(body)
        self.assertIn('ticker change', err)
        self.assertIn('GLOBAL QQOL.US QQNW.US', err)

    def test_two_buys_under_one_description_are_not_a_ticker_change(self):
        body = (q(sym='QQOL', desc='QQ HOLDINGS CORP WE ACTED AS AGENT')
                + q(sym='QQNW', desc='QQ HOLDINGS CORP WE ACTED AS AGENT'))
        _, err, _ = qt_parse(body)
        self.assertNotIn('ticker change', err)


class TestQtRowShapes(unittest.TestCase):

    def test_upper_case_action_and_lower_case_symbol(self):
        """R1-71."""
        body = (q(action='BUY', sym='xyz.to', desc='XYZ CORP', qty='200',
                  price='10', gross='-2000', comm='0', net='-2000',
                  cur='CAD')
                + q(td='2025-03-03', action='sell', sym='XYZ.TO',
                    desc='XYZ CORP', qty='-100', price='12', gross='1200',
                    comm='0', net='1200', cur='CAD'))
        txs, _, _ = qt_parse(body)
        self.assertEqual([(t['symbol'], t['quantity']) for t in txs],
                         [('XYZ.TO', 200.0), ('XYZ.TO', -100.0)])

    def test_unknown_share_moving_action_is_unbooked(self):
        txs, err, _ = qt_parse(q(action='NAC', sym='QQOL', desc='QQ CORP',
                                 qty='-500', price='0', gross='0', comm='0',
                                 net='0', act='Other'))
        self.assertEqual(txs, [])
        self.assertIn('warning: UNBOOKED:', err)

    def test_brw_listing_journal_is_a_note_not_unbooked(self):
        body = (q(action='BRW', sym='DLR.TO', desc='GLOBAL X US DLR CURRENCY '
                  'ETF UNIT CL A JOURNAL POSITION TO USD', qty='-300',
                  price='0', gross='0', comm='0', net='0', cur='CAD',
                  act='Other')
                + q(action='BRW', sym='DLR.U.TO', desc='GLOBAL X US DLR '
                    'CURRENCY ETF UNIT CL A JOURNAL POSITION FROM CAD',
                    qty='300', price='0', gross='0', comm='0', net='0',
                    act='Other'))
        txs, err, pars = qt_parse(body)
        self.assertEqual(txs, [])
        self.assertNotIn('UNBOOKED', err)
        self.assertIn('JOURNAL rule', err)

    def test_transfer_carries_its_description(self):
        """R1-69."""
        desc = 'US DLR CURRENCY ETF TRANSFER FROM OTHER BROKER BOOK VALUE 1000'
        txs, _, _ = qt_parse(q(action='TF6', sym='DLR', desc=desc,
                               qty='100', price='0', gross='0', comm='0',
                               net='0', act='Transfers'))
        self.assertEqual(txs[0]['description'], desc)

    def test_option_transfer_uses_the_contract(self):
        """S062-14."""
        body = (q(action='TF6', sym='QZSL16Oct26C62.50',
                  desc='CALL QZSL 10/16/26 62.50 QZSL CORP TRANSFER BOOK '
                       'VALUE 230.00', qty='2', price='0', gross='0',
                  comm='0', net='0', act='Transfers')
                + q(td='2025-03-03', action='Sell', sym='QZSL16Oct26C62.50',
                    desc='CALL QZSL 10/16/26 62.50 QZSL CORP WE ACTED AS '
                         'AGENT', qty='-2', price='0.40', gross='80',
                    comm='0', net='80'))
        txs, _, _ = qt_parse(body)
        self.assertEqual({t['symbol'] for t in txs},
                         {'QZSL261016C00062500.US'})
        tr = of(txs, action='TRANSFER')[0]
        self.assertAlmostEqual(tr['price'], 1.15)
        self.assertEqual(tr['multiplier'], 100.0)

    def test_stock_leg_with_contract_text_is_the_equity(self):
        """S062-19."""
        txs, _, _ = qt_parse(q(action='Sell', sym='ABC',
                               desc='ABC CORP COMMON STOCK ASSIGNMENT OF '
                                    'OPTION CALL ABC 05/16/25 50',
                               qty='-100', price='50', gross='5000',
                               comm='0', net='5000'))
        self.assertEqual(txs[0]['symbol'], 'ABC.US')
        self.assertEqual(txs[0]['action'], 'BUYSELL')
        self.assertAlmostEqual(txs[0]['net_amount'], 5000.0)

    def test_blank_settle_option_is_t_plus_1(self):
        """R1-194: a Dec-28-2023 option sale stays in 2023."""
        txs, _, _ = qt_parse(q(td='2023-12-28', sd='', action='Sell',
                               sym='ABC19Jan24C50.00',
                               desc='CALL ABC 01/19/24 50 ABC CORP WE ACTED '
                                    'AS AGENT', qty='-2', price='3',
                               gross='600', comm='0', net='600', cur='CAD'))
        self.assertEqual(txs[0]['date_settle'], '2023-12-29')

    def test_blank_settle_equity_keeps_t_plus_2_pre_cutover(self):
        txs, _, _ = qt_parse(q(td='2023-12-27', sd='', cur='CAD',
                               sym='XYZ.TO', desc='XYZ CORP', qty='1',
                               price='10', gross='-10', comm='0', net='-10'))
        self.assertEqual(txs[0]['date_settle'], '2023-12-29')

    def test_warrant_expiry_books_on_its_expiry_date(self):
        """S065-04."""
        txs, _, _ = qt_parse(q(td='2028-01-03', action='EXP', sym='QZWW',
                               desc='QZW CORP WARRANT AS OF 12/31/27 - EXPIRED',
                               qty='-100', price='0', gross='0', comm='0',
                               net='0'))
        self.assertEqual((txs[0]['date'], txs[0]['date_settle']),
                         ('2027-12-31', '2027-12-31'))


class TestQtStockDividendAndDis(unittest.TestCase):
    BUY = q(sym='XTD.TO', desc='XTD SPLIT CORP WE ACTED AS AGENT', qty='1000',
            price='10', gross='-10000', comm='0', net='-10000', cur='CAD')

    def _stk(self, qty, net='0', td='2025-07-29'):
        return q(td=td, action='DIS', sym='XTD.TO',
                 desc='XTD SPLIT CORP STK DIV ON 1000 SHS REC 07/24/25 PAY '
                      '07/29/25', qty=qty, price='0', gross='0', comm='0',
                 net=net, cur='CAD', act='Dividends')

    def test_stock_dividend_reversal_cancels_the_original(self):
        """R1-65."""
        txs, _, _ = qt_parse(self.BUY + self._stk('150')
                             + self._stk('-150', td='2025-07-30'))
        self.assertEqual(sum(t['quantity'] for t in txs), 1000.0)

    def test_orphan_stock_dividend_reversal_is_refused(self):
        with self.assertRaises(BrokerageParseError):
            qt_parse(self.BUY + self._stk('-150'))

    def test_stock_dividend_with_cash_is_refused(self):
        """R1-72 (mirror)."""
        with self.assertRaises(BrokerageParseError):
            qt_parse(self.BUY + self._stk('5', net='12.00'))

    def test_dis_with_shares_and_cash_is_refused(self):
        """R1-72."""
        with self.assertRaises(BrokerageParseError):
            qt_parse(self.BUY + q(td='2025-07-29', action='DIS',
                                  sym='XTD.TO',
                                  desc='XTD SPLIT CORP DIST ON 1000 SHS',
                                  qty='5', price='0', gross='0', comm='0',
                                  net='12.00', cur='CAD', act='Dividends'))

    def test_split_on_a_short_scales_up(self):
        """S063-08."""
        body = (q(action='Sell', sym='SPQ.TO', desc='SPQ CORP', qty='-100',
                  price='20', gross='2000', comm='0', net='2000', cur='CAD')
                + q(td='2025-03-03', action='DIS', sym='SPQ.TO',
                    desc='SPQ CORP STK SPLIT ON 100 SHS', qty='-5',
                    price='0', gross='0', comm='0', net='0', cur='CAD',
                    act='Dividends'))
        txs, _, _ = qt_parse(body)
        self.assertAlmostEqual(of(txs, action='SPLIT')[0]['quantity'], 1.05)

    def test_split_on_a_long_unchanged(self):
        body = (self.BUY
                + q(td='2025-03-03', action='DIS', sym='XTD.TO',
                    desc='XTD SPLIT CORP STK SPLIT ON 1000 SHS', qty='1000',
                    price='0', gross='0', comm='0', net='0', cur='CAD',
                    act='Dividends'))
        txs, _, _ = qt_parse(body)
        self.assertAlmostEqual(of(txs, action='SPLIT')[0]['quantity'], 2.0)


class TestQtFxSettled(unittest.TestCase):
    def _row(self, net, comm='0', action='Buy', qty='10', gross='-1000'):
        return q(action=action, sym='XYZ',
                 desc='XYZ CORP WE ACTED AS AGENT EXCHANGE RATE 1.40',
                 qty=qty, price='100', gross=gross, comm=comm, net=net,
                 cur='CAD')

    def test_consistent_row_books_the_cad_cost(self):
        txs, _, _ = qt_parse(self._row('-1400.00'))
        self.assertAlmostEqual(txs[0]['net_amount'], 1400.0)

    def test_commission_in_either_currency_is_accepted(self):
        qt_parse(self._row('-1406.93', comm='-4.95'))     # USD commission
        qt_parse(self._row('-1404.95', comm='-4.95'))     # CAD commission

    def test_wrong_cad_net_is_refused(self):
        """R1-73."""
        for net in ('-14000.00', '-1426.00', '-4.95'):
            with self.assertRaises(BrokerageParseError, msg=net):
                qt_parse(self._row(net, comm='-1406.93' if net == '-4.95'
                                   else '0'))

    def test_negative_net_sale_keeps_its_sign(self):
        """S014-06: a sale whose commission exceeds its gross."""
        txs, _, _ = qt_parse(q(action='Sell', sym='PNY',
                               desc='PNY CORP WE ACTED AS AGENT EXCHANGE '
                                    'RATE 1.40', qty='-10', price='0.05',
                               gross='0.50', comm='-4.95', net='-6.23',
                               cur='CAD'))
        self.assertAlmostEqual(txs[0]['net_amount'], -6.23)


class TestQtTransferAdvice(unittest.TestCase):
    def test_no_book_value_warning_names_a_remedy_that_works(self):
        """S063-00."""
        _, err, _ = qt_parse(q(action='TF6', sym='QZG',
                               desc='QZG INC TRANSFER IN OTHER BROKER',
                               qty='12', price='0', gross='0', comm='0',
                               net='0', act='Transfers'), taxable=True)
        self.assertIn('no TRANSFER BOOK VALUE', err)
        self.assertIn('.tt BUYSELL', err)
        self.assertNotIn('OPENING_BALANCE', err)
        self.assertNotIn('booked at $0 cost', err)


class TestQtCorpActionChains(unittest.TestCase):
    def _events(self, body):
        from taxjson.lib.corp_actions import parse_questrade_corporate_actions
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'questrade_2025.csv'
            p.write_text(QH + body, encoding='utf-8')
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ev = parse_questrade_corporate_actions(p)
        return ev, err.getvalue()

    def test_negative_chain_is_unbooked_not_silent(self):
        """S062-11."""
        ev, err = self._events(
            q(sym='PARR.TO', desc='PAR CORP RIGHTS', qty='1000', price='0.10',
              gross='-100', comm='0', net='-100', cur='CAD')
            + q(td='2025-03-17', action='DIS', sym='PARR.TO',
                desc='PAR CORP RTS DIST ON 1000 SHS REC 03/14/25 PAY '
                     '03/17/25 RIGHTS LAPSED', qty='-1000', price='0',
                gross='0', comm='0', net='0', cur='CAD', act='Dividends'))
        self.assertEqual(ev, [])
        self.assertIn('UNBOOKED', err)

    def test_internal_code_target_is_flagged(self):
        """R1-3."""
        leg = ('WTS DEFI DEV CORP WT EXP RTS DIST ON 1000 SHS FROM SEC# '
               'J000001 DEFI DEVELOPMENT CORP REC 07/10/25 PAY 07/14/25')
        ev, err = self._events(q(td='2025-07-14', action='DIS',
                                 sym='D056068', desc=leg, qty='100',
                                 price='0', gross='0', comm='0', net='0',
                                 act='Dividends'))
        self.assertEqual(len(ev), 1)
        self.assertIn("INTERNAL code 'D056068'", err)


if __name__ == '__main__':
    unittest.main()
