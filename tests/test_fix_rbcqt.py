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
from tax_rules import rule

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

    BRW = (q(action='BRW', sym='DLR.TO', desc='GLOBAL X US DLR CURRENCY '
             'ETF UNIT CL A JOURNAL POSITION TO USD', qty='-300',
             price='0', gross='0', comm='0', net='0', cur='CAD',
             act='Other')
           + q(action='BRW', sym='DLR.U.TO', desc='GLOBAL X US DLR '
               'CURRENCY ETF UNIT CL A JOURNAL POSITION FROM CAD BOOK '
               'VALUE: $3039.64 CNV@ 1.4138', qty='300', price='0',
               gross='0', comm='0', net='0', act='Other'))

    def test_brw_listing_journal_is_a_transfer_pair(self):
        """QT-BRW: Norbert's gambit journal booked like RBC's TFR legs."""
        txs, err, _ = qt_parse(self.BRW)
        self.assertEqual(sorted((t['action'], t['symbol'], t['quantity'])
                                for t in txs),
                         [('TRANSFER', 'DLR.TO', -300.0),
                          ('TRANSFER', 'DLR.U.TO', 300.0)])
        self.assertAlmostEqual(of(txs, symbol='DLR.U.TO')[0]['book_value'],
                               3039.64)
        # The cost moves with the units: USD book value on the IN leg,
        # the same cost in CAD at the stated rate on the OUT leg.
        self.assertAlmostEqual(of(txs, symbol='DLR.U.TO')[0]['net_amount'],
                               3039.64)
        self.assertAlmostEqual(of(txs, symbol='DLR.TO')[0]['net_amount'],
                               round(3039.64 * 1.4138, 2))
        self.assertNotIn('UNBOOKED', err)
        self.assertIn('JOURNAL', err)

    def test_brw_journal_nets_under_a_journal_rule(self):
        """End to end through taxjson-brokerage + the transfer pre-pass:
        with JOURNAL DLR.U.TO DLR.TO the two legs are one symbol and
        cancel (a sheltered account with transfers on)."""
        from taxjson.lib.pipeline import _drop_self_cancelling_transfers
        from taxjson.lib.core import TaxTransaction
        txs, _, _ = qt_parse(self.BRW)
        mapped = [TaxTransaction(**{k: v for k, v in {
            **t, 'symbol': 'DLR.TO'}.items()
            if k not in ('book_value', 'broker_account')})
            for t in txs]
        kept, dropped = _drop_self_cancelling_transfers(mapped)
        self.assertEqual(kept, [])
        self.assertTrue(dropped)

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

    @rule("CA-DATE-08")
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
        # Named as booked (A2-0966).
        self.assertIn("INTERNAL code D056068.", err)



# ------------------------------------------------------------------ RBC

from taxjson.lib.brokerages.rbc_direct import RbcBrokerage, RbcFormatError  # noqa: E402

RH = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
      '"Settlement Date","Account","Value","Currency","Description"\n')


def rrow(date, activity, symbol, symdesc, qty, price, value, cur, desc,
         settle=None):
    cells = [date, activity, symbol, symdesc, qty, price,
             date if settle is None else settle, ACCT, value, cur, desc]
    return ','.join('"%s"' % c for c in cells) + '\n'


def rbc_parse(*bodies, raw=False):
    """Parse RBC files as ONE account. Returns (txs, stderr, parsers)."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i, body in enumerate(bodies):
            p = Path(d) / f"rbc_{2025 + i}.csv"
            p.write_text(body if raw else RH + body, encoding='utf-8')
            paths.append(p)
        err = io.StringIO()
        txs, pars = [], []
        with contextlib.redirect_stderr(err):
            ctx = RbcBrokerage.prepare_files(paths)
            for p in paths:
                par = RbcBrokerage()
                par.account_context = ctx
                txs.extend(par.parse_file(p))
                pars.append(par)
    return txs, err.getvalue(), pars


RBUY = rrow("March 3, 2025", "Buy", "XYZ", "XYZ CORP", "100", "50",
            "-5009.95", "CAD", "XYZ CORP UNSOLICITED DA")


class TestRbcTradeRows(unittest.TestCase):

    def test_blank_value_on_a_sale_is_refused(self):
        """R1-82."""
        with self.assertRaises(RbcFormatError):
            rbc_parse(RBUY + rrow("April 1, 2025", "Sell", "XYZ", "XYZ CORP",
                                  "-100", "60", "", "CAD", "XYZ CORP"))

    def test_explicit_zero_value_is_allowed(self):
        txs, _, _ = rbc_parse(rrow("April 1, 2025", "Sell", "8QQQQQ1",
                                   "CALL .XYZ 04/17/25 90 XYZ CORP", "-1",
                                   "0.01", "0", "CAD",
                                   "CALL .XYZ 04/17/25 90 XYZ CORP"))
        self.assertAlmostEqual(txs[0]['net_amount'], 0.0)

    def test_value_off_by_a_factor_is_refused(self):
        """S023-19."""
        for value in ("-500.99", "-50099.50"):
            with self.assertRaises(RbcFormatError, msg=value):
                rbc_parse(rrow("March 3, 2025", "Buy", "XYZ", "XYZ CORP",
                               "100", "50", value, "CAD", "XYZ CORP"))

    def test_small_option_with_a_large_commission_is_fine(self):
        txs, _, _ = rbc_parse(rrow("April 8, 2024", "Buy", "9QQQQQ5",
                                   "CALL .SNQ 04/19/24 7 SNQ INC", "1",
                                   "0.32", "-43.95", "USD",
                                   "CALL .SNQ 04/19/24 7 SNQ INC"))
        self.assertAlmostEqual(txs[0]['net_amount'], 43.95)

    def test_comma_strikes_are_distinct_contracts(self):
        """S063-15."""
        txs, _, _ = rbc_parse(
            rrow("June 2, 2025", "Buy", "8QQQQQ2", "", "1", "100",
                 "-10009.95", "USD", "CALL .BKQ 06/20/25 5,000")
            + rrow("June 2, 2025", "Buy", "8QQQQQ3", "", "1", "80",
                   "-8009.95", "USD", "CALL .BKQ 06/20/25 5,025"))
        self.assertEqual(sorted(t['symbol'] for t in txs),
                         ['BKQ250620C05000000.US', 'BKQ250620C05025000.US'])

    def test_stock_leg_with_contract_text_is_the_equity(self):
        """R1-86: in the Description and in the Symbol Description."""
        for desc, symdesc in (
                ("ABC CORP ASSIGNMENT OF OPTION CALL ABC 05/16/25 50",
                 "ABC CORP"),
                ("ABC CORP ASSIGNMENT OF OPTION AS OF 05/16/25",
                 "ASSIGNMENT OF OPTION CALL ABC 05/16/25 50")):
            txs, _, _ = rbc_parse(rrow("May 16, 2025", "Sell", "ABC",
                                       symdesc, "-100", "50", "4990.05",
                                       "USD", desc))
            self.assertEqual((txs[0]['symbol'], txs[0]['quantity']),
                             ('ABC.US', -100.0), desc)

    def test_listed_symbol_with_another_contract_is_refused(self):
        with self.assertRaises(RbcFormatError):
            rbc_parse(rrow("May 16, 2025", "Sell", "ABC", "ABC CORP", "-100",
                           "50", "4990.05", "USD",
                           "CALL XYZ 05/16/25 50 XYZ CORP"))

    def test_blank_settle_assignment_legs_share_the_equity_cycle(self):
        """S065-06: pre-cutover, the ASN option leg follows its stock leg."""
        txs, _, _ = rbc_parse(
            rrow("June 16, 2023", "Other", "8QQQQQ4", "", "1", "", "0",
                 "CAD", "ASN - PUT .QZX 06/16/23 50", settle="")
            + rrow("June 16, 2023", "Buy", "QZX", "QZX CORP", "100", "50",
                   "-5000", "CAD", "QZX CORP ASSIGNMENT OF OPTION AS OF "
                   "06/16/23", settle=""))
        self.assertEqual({t['date_settle'] for t in txs}, {'2023-06-20'})

    def test_warrant_expiry_settles_on_its_date(self):
        """S065-04."""
        txs, _, _ = rbc_parse(rrow("December 31, 2027", "Reorganization",
                                   "QZW.WT", "QZW CORP WTS", "-100", "", "0",
                                   "CAD", "EXP - WTS QZW CORP AS OF 12/31/27 "
                                   "EXPIRED", settle="January 3, 2028"))
        self.assertEqual(txs[0]['date_settle'], '2027-12-31')


class TestRbcIncomeAndCorporateRows(unittest.TestCase):

    def test_reinvestment_reversal_cancels(self):
        """R1-83."""
        rei = rrow("April 18, 2022", "Dividends", "SRU.UN", "SMARTCENTRES",
                   "2", "", "-64.48", "CAD", "REI - SMARTCENTRES REINV@C$32.24")
        cxl = rrow("April 22, 2022", "Dividends", "SRU.UN", "SMARTCENTRES",
                   "-2", "", "64.48", "CAD",
                   "REI - SMARTCENTRES REINV@C$32.24 CANCEL")
        txs, _, _ = rbc_parse(rei + cxl)
        self.assertEqual(txs, [])
        with self.assertRaises(RbcFormatError):
            rbc_parse(cxl)
        with self.assertRaises(RbcFormatError):
            rbc_parse(rrow("April 18, 2022", "Dividends", "SRU.UN",
                           "SMARTCENTRES", "2", "", "64.48", "CAD",
                           "REI - SMARTCENTRES REINV@C$32.24"))

    def test_in_kind_reinvested_distribution(self):
        """R1-87."""
        txs, _, _ = rbc_parse(rrow(
            "April 7, 2022", "Dividends", "RBF8411", "RBC INTL EQUITY O",
            "5", "", "", "USD",
            "DIV - Rbc International Equity Series O U$ (8411) As Of "
            "04/07/22 Reinvest @ $20.00"))
        self.assertEqual(sorted((t['action'], t['net_amount']) for t in txs),
                         [('BUYSELL', 100.0), ('DIVIDEND', 100.0)])

    def test_stock_dividend_books_shares_at_zero(self):
        """S015-06: Reorganization and Dividends forms."""
        for activity in ("Reorganization", "Dividends"):
            txs, err, _ = rbc_parse(
                rrow("March 3, 2025", "Buy", "XTD", "XTD SPLIT CORP", "1390",
                     "5", "-6950", "CAD", "XTD SPLIT CORP")
                + rrow("July 29, 2025", activity, "XTD", "XTD SPLIT CORP",
                       "208", "", "0", "CAD",
                       "DIS - XTD SPLIT CORP STK DIV ON 1390 SHS"))
            self.assertEqual(sum(t['quantity'] for t in txs), 1598.0,
                             activity)
            self.assertNotIn('UNCLASSIFIED', err)

    def test_cash_in_lieu_of_dividend_on_a_frac_name_is_income(self):
        """S064-05."""
        txs, _, _ = rbc_parse(rrow("March 3, 2025", "Dividends", "GUTZ",
                                   "FRACTYL HEALTH INC", "", "", "25.00",
                                   "USD", "FRACTYL HEALTH INC CASH IN LIEU "
                                   "OF DIVIDEND"))
        self.assertEqual([(t['action'], t['net_amount']) for t in txs],
                         [('DIVIDEND', 25.0)])

    def test_transfer_in_of_a_deliver_named_security_stays_in(self):
        """S016-05 / S065-03."""
        txs, _, _ = rbc_parse(rrow(
            "March 10, 2025", "Transfers", "GDN", "GLOBAL DELIVERY NETWORKS",
            "100", "", "0", "CAD", "TFI - GLOBAL DELIVERY NETWORKS INC "
            "ACCOUNT TRANSFER BOOK VALUE 5000.00 FROM ACCOUNT"))
        self.assertEqual(txs[0]['quantity'], 100.0)

    def test_transfer_out_code_still_flips(self):
        txs, _, _ = rbc_parse(rrow(
            "March 10, 2025", "Transfers", "GDN", "GLOBAL NETWORKS", "100",
            "", "0", "CAD", "TFO - GLOBAL NETWORKS INC ACCOUNT TRANSFER"))
        self.assertEqual(txs[0]['quantity'], -100.0)

    def test_blank_description_falls_back_to_symbol_description(self):
        """S065-01."""
        txs, _, _ = rbc_parse(rrow("March 3, 2025", "Buy", "ZZ",
                                   "ZZ US DOLLAR CURRENCY ETF", "100", "10",
                                   "-1009.95", "USD", ""))
        self.assertEqual(txs[0]['description'], 'ZZ US DOLLAR CURRENCY ETF')

    def test_split_on_a_short_scales_up(self):
        """S065-00."""
        txs, _, _ = rbc_parse(
            rrow("March 3, 2025", "Sell", "SPC", "SPC CORP", "-100", "20",
                 "1990.05", "CAD", "SPC CORP SHORT. UNSOLICITED")
            + rrow("March 10, 2025", "Reorganization", "SPC", "SPC CORP",
                   "-100", "", "0", "CAD",
                   "DIS - SPC CORP STK SPLIT ON 100 SHS"))
        self.assertAlmostEqual(of(txs, action='SPLIT')[0]['quantity'], 2.0)

    def test_cil_reversal_nets(self):
        """S063-19."""
        mer = (rrow("June 28, 2023", "Reorganization", "T099003",
                    "TRIX REUTERS CORP COM NEW", "-50", "", "307.93", "CAD",
                    "MER - TRIX REUTERS CORP COM NEW DEFAULT: ROC OF "
                    "C$6.1585 + .963957 NEW SHS PER 1 OLD")
               + rrow("June 28, 2023", "Reorganization", "TRX",
                      "TRIX REUTERS CORP COM NO PAR", "48", "", "0", "CAD",
                      "MGR - TRIX REUTERS CORP COM NO PAR SHRS RECEIVED THRU "
                      "MERGER")
               + rrow("May 3, 2023", "Buy", "TRX", "TRIX REUTERS CORP COM NEW",
                      "50", "170", "-8509.95", "CAD", "TRIX UNSOLICITED DA"))

        def cil(date, v, extra=''):
            return rrow(date, "Reorganization", "TRX",
                        "TRIX REUTERS CORP COM NO PAR", "", "", v, "CAD",
                        "CIL - TRIX REUTERS CORP COM NO PAR CASH IN LIEU OF "
                        "FRAC SHARES" + extra)
        txs, _, _ = rbc_parse(cil("July 12, 2023", "30.00")
                              + cil("July 13, 2023", "-30.00", " CXL")
                              + cil("July 14, 2023", "30.00") + mer)
        frac = [t for t in of(txs, action='BUYSELL') if t['quantity'] < 0]
        self.assertAlmostEqual(frac[0]['net_amount'], 30.0)

    def test_reorganization_straddling_two_files_pairs(self):
        """S064-14."""
        rem = rrow("December 31, 2025", "Reorganization", "G099004",
                   "GLOBEX DATA CORP", "-700", "", "0", "CAD",
                   "REV - GLOBEX DATA CORP REV SPLIT TO GLOBEX DATA CORP "
                   "NEW; 1 FOR 10")
        rc = rrow("January 2, 2026", "Reorganization", "GLBX",
                  "GLOBEX DATA CORP NEW", "70", "", "0", "CAD",
                  "REV - GLOBEX DATA CORP NEW RESULT OF REVERSE SPLIT")
        buy = rrow("March 3, 2025", "Buy", "GLBX", "GLOBEX DATA CORP", "700",
                   "2", "-1409.95", "CAD", "GLOBEX UNSOLICITED")
        txs, err, _ = rbc_parse(rem + buy, rc)
        self.assertNotIn('UNMATCHED', err)
        self.assertAlmostEqual(of(txs, action='SPLIT')[0]['quantity'], 0.1)

    def test_notional_distribution_says_the_income_is_not_booked(self):
        """S063-17 (the income itself is an owner decision)."""
        _, err, _ = rbc_parse(
            RBUY + rrow("December 31, 2025", "Dividends", "XYZ", "XYZ CORP",
                        "", "", "0", "CAD", "ADJ - XYZ CORP 2025 NOTIONAL "
                        "DISTRIBUTION ADJUSTMENT TO BOOK COST $200.00"))
        self.assertIn('NOT in taxjson', err)


class TestRbcUnbookedRows(unittest.TestCase):
    """S016-00 / S064-17 / S063-21: rows the parser does not book reach
    the console (`taxjson run` echoes UNBOOKED lines; `run --strict`
    refuses them)."""

    def test_unclassified_share_row_is_unbooked(self):
        _, err, pars = rbc_parse(rrow("March 3, 2025", "Sold", "QQQX",
                                      "QQQX INC", "-100", "10", "990.05",
                                      "USD", "QQQX INC"))
        self.assertIn('warning: UNBOOKED:', err)
        self.assertTrue(pars[0].lint_findings)

    def test_transfer_with_blank_quantity_is_unbooked(self):
        _, err, _ = rbc_parse(rrow("February 13, 2025", "Transfers", "ZZQ",
                                   "ZZQ CORP", "", "", "0.00", "CAD",
                                   "TFI - ZZQ CORP ACCOUNT TRANSFER"))
        self.assertIn('warning: UNBOOKED:', err)

    def test_unmatched_reorganization_leg_is_unbooked(self):
        _, err, _ = rbc_parse(rrow("December 31, 2025", "Reorganization",
                                   "G099004", "GLOBEX", "-700", "", "0",
                                   "CAD", "REV - GLOBEX REV SPLIT TO GLOBEX "
                                   "NEW; 1 FOR 10"))
        self.assertIn('warning: UNBOOKED:', err)

    def test_swallowed_row_is_refused(self):
        """R1-84."""
        # The Description's only internal quote is its last character:
        # raw '...PRINCIPAL ""' then a newline keeps the field open.
        body = (RH
                + rrow("May 1, 2024", "Sell", "ABC", "ABC CORP", "-100", "10",
                       "990.05", "CAD", 'ABC CORP WE ACTED AS PRINCIPAL "')
                + rrow("May 2, 2024", "Buy", "DEF", "DEF CORP", "50", "20",
                       "-1000.00", "CAD", "DEF CORP"))
        self.assertIn('PRINCIPAL ""\n', body)
        with self.assertRaises(RbcFormatError):
            rbc_parse(body, raw=True)



class TestBrokerMarkedShorts(unittest.TestCase):
    """R1-8: an RBC 'SHORT.' sale is a real short, not truncated history."""

    def _txs(self, marker=' SHORT.'):
        from taxjson.lib.core import TaxTransaction

        def t(date, qty, desc):
            return TaxTransaction(action='BUYSELL', date=date, symbol='QQA.TO',
                                  quantity=qty, price=10.7,
                                  net_amount=abs(qty) * 10.7,
                                  account='margin', currency='CAD',
                                  description=desc)
        return [t('2024-04-15', -700, f'QQA CORP{marker} UNSOLICITED'),
                t('2024-04-16', -300, f'QQA CORP{marker} UNSOLICITED'),
                t('2024-04-19', 1000, 'QQA CORP COVER SHORT. UNSOLICITED')]

    def test_marked_short_is_not_a_phantom_candidate(self):
        from taxjson.lib.phantom_holdings import detect_phantoms
        self.assertEqual(detect_phantoms(self._txs()), [])
        marked = detect_phantoms(self._txs(), include_broker_shorts=True)
        self.assertEqual([c.broker_marked_short for c in marked], [True])

    def test_unmarked_short_still_is(self):
        from taxjson.lib.phantom_holdings import detect_phantoms
        self.assertEqual(len(detect_phantoms(self._txs(marker=''))), 1)

    def test_missing_history_reports_it_apart(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'margin_base.json'
            f.write_text(json.dumps({'transactions': [
                t.to_dict() for t in self._txs()]}))
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_missing_history',
                 '--year', '2024', str(f)], capture_output=True, text=True,
                env={**os.environ, 'PYTHONPATH': str(REPO / 'src')})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Broker-marked short sales', r.stdout)
        self.assertNotIn('AFFECTS 2024', r.stdout)


if __name__ == '__main__':
    unittest.main()
