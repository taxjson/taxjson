"""Broker-parser audit (2026-09, round b). Every fixture is synthetic:
invented tickers, fake account ids, invented quantities and prices.

1. Option expiries carry no settlement cycle (IB/Webull/Questrade/RBC):
   settle == the expiry date, same-day opening trades clamp to it.
2. Kraken ledger fees are in coin units: rewards/instant trades book
   the coins that actually moved (amount − fee).
3. IB "(Return of Capital)": payment in lieu → income; non-Canadian
   issuer → dividend (ITA s.90(2)) unless foreign_return_of_capital=acb.
4. .tt option lines check net against qty*price*100.
5. IB accruals: a revised pay date no longer splits a Po/Re pair;
   futures shorts are not "truncated history" candidates.
"""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from taxjson.lib.brokerages.kraken import KrakenBrokerage
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from taxjson.lib.brokerages.webull import WebullBrokerage
from taxjson.lib.core import CanadaTaxRules, TaxTransaction
from tax_rules import rule


def _parse(parser, content, suffix='.csv', prefix='tmp'):
    with tempfile.NamedTemporaryFile('w', suffix=suffix, prefix=prefix,
                                     delete=False,
                                     encoding='utf-8') as f:
        f.write(content)
        name = f.name
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            txs = parser.parse_file(Path(name))
    finally:
        os.remove(name)
    return txs, err.getvalue()


def _gains(rows):
    """Canada engine over parser dicts → realized-gain entries."""
    fields = TaxTransaction.__dataclass_fields__
    txs = [TaxTransaction(**{k: v for k, v in r.items() if k in fields})
           for r in rows]
    with contextlib.redirect_stderr(io.StringIO()):
        res = CanadaTaxRules().compute_gains(txs)
    return [g for g in res['transactions']
            if 'gain' in g and g.get('action') is None]


IB_TRADES_HDR = (
    'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
    'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
    'Realized P/L,MTM P/L,Code\n')


def _ib_fii_futures(sym, mult):
    """Financial Instrument Information for a synthetic future. Added
    in the 2026-09 parse hardening: a futures contract size can no
    longer be guessed (it used to be taken as 1), so a fixture must
    state it the way a real statement does."""
    return ('Financial Instrument Information,Header,Asset Category,'
            'Symbol,Description,Conid,Underlying,Listing Exch,Multiplier,'
            'Expiry,Delivery Month,Code\n'
            f'Financial Instrument Information,Data,Futures,{sym},'
            f'{sym} TEST,999000001,{sym[:3]},CME,{mult},2026-06-26,'
            f'2026-06,\n')


def _ib_trade(cat, sym, when, qty, price, proceeds, comm, code):
    return (f'Trades,Data,Order,{cat},USD,{sym},"{when}",{qty},{price},0,'
            f'{proceeds},{comm},0,0,0,{code}\n')


class TestIbExpirySettlement(unittest.TestCase):
    OPT = 'Equity and Index Options'

    def test_dec31_expiry_stays_in_its_tax_year(self):
        body = (IB_TRADES_HDR
                + _ib_trade(self.OPT, 'QZX 31DEC26 70 C',
                            '2026-12-01, 10:00:00', 1, 5.00, -500, -1, 'O')
                + _ib_trade(self.OPT, 'QZX 31DEC26 70 C',
                            '2026-12-31, 16:20:00', -1, 0, 0, 0, 'C;Ep'))
        txs, _ = _parse(IbBrokerage(), body)
        exp = next(t for t in txs if t['quantity'] < 0)
        self.assertEqual(exp['date_settle'], '2026-12-31')
        # The opening buy keeps its ordinary T+1.
        buy = next(t for t in txs if t['quantity'] > 0)
        self.assertEqual(buy['date_settle'], '2026-12-02')
        gains = _gains(txs)
        self.assertEqual(len(gains), 1)
        self.assertEqual(gains[0]['date_settle'], '2026-12-31')
        self.assertAlmostEqual(gains[0]['gain'], -501.0)

    def test_zero_price_close_without_ep_code_is_an_expiry(self):
        body = (IB_TRADES_HDR
                + _ib_trade(self.OPT, 'QZX 18DEC26 70 P',
                            '2026-12-18, 16:20:00', 2, 0, 0, 0, 'C'))
        txs, _ = _parse(IbBrokerage(), body)
        self.assertEqual(txs[0]['date_settle'], '2026-12-18')

    def test_options_expirations_section_row(self):
        body = ('Options Expirations,Header,Currency,Symbol,Date/Time,'
                'Quantity,T. Price,Proceeds,Comm/Fee,Code\n'
                'Options Expirations,Data,USD,QZX 31DEC26 70 C,'
                '"2026-12-31, 16:20:00",-1,0,0,0,C;Ep\n')
        txs, _ = _parse(IbBrokerage(), body)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['date_settle'], '2026-12-31')

    def test_0dte_long_clamps_to_expiry_and_closes_long(self):
        # Bought and expired on Dec 31: the buy's T+1 settle (next year)
        # would sort AFTER the expiry on the settle basis.
        body = (IB_TRADES_HDR
                + _ib_trade(self.OPT, 'QZX 31DEC26 70 C',
                            '2026-12-31, 10:00:00', 2, 1.25, -250, -1.3, 'O')
                + _ib_trade(self.OPT, 'QZX 31DEC26 70 C',
                            '2026-12-31, 16:20:00', -2, 0, 0, 0, 'C;Ep'))
        txs, _ = _parse(IbBrokerage(), body)
        self.assertEqual({t['date_settle'] for t in txs}, {'2026-12-31'})
        gains = _gains(txs)
        self.assertEqual(len(gains), 1)
        self.assertEqual(gains[0]['direction'], 'LONG')
        self.assertAlmostEqual(gains[0]['gain'], -251.3)

    def test_0dte_short_expiry_closes_short(self):
        body = (IB_TRADES_HDR
                + _ib_trade(self.OPT, 'QZX 31DEC26 70 P',
                            '2026-12-31, 10:00:00', -1, 0.80, 80, -1, 'O')
                + _ib_trade(self.OPT, 'QZX 31DEC26 70 P',
                            '2026-12-31, 16:20:00', 1, 0, 0, 0, 'C;Ep'))
        txs, _ = _parse(IbBrokerage(), body)
        gains = _gains(txs)
        self.assertEqual(len(gains), 1)
        self.assertEqual(gains[0]['direction'], 'SHORT')
        self.assertAlmostEqual(gains[0]['gain'], 79.0)
        self.assertEqual(gains[0]['date_settle'], '2026-12-31')

    def test_assignment_option_leg_keeps_stock_leg_settle(self):
        # The premium rolls into the stock leg, which settles T+1: the
        # pair must share a settle date (pinned deliberately).
        body = (IB_TRADES_HDR
                + _ib_trade(self.OPT, 'QZX 16JAN26 56 P',
                            '2026-01-14, 16:20:00', 1, 0, 0, 0, 'A;C')
                + _ib_trade('Stocks', 'QZX', '2026-01-14, 16:20:00',
                            100, 56, -5600, 0, 'A;O'))
        txs, _ = _parse(IbBrokerage(), body)
        leg = next(t for t in txs if t['action'] == 'ASSIGN')
        stock = next(t for t in txs if t['action'] == 'BUYSELL')
        self.assertEqual(leg['date_settle'], stock['date_settle'])
        self.assertEqual(leg['date_settle'], '2026-01-15')

    def test_futures_final_settlement_is_same_day(self):
        body = (IB_TRADES_HDR
                + _ib_trade('Futures', 'QZFM6', '2026-06-26, 16:20:00',
                            -1, 5000, 500, -2, 'C;Ep')
                + _ib_fii_futures('QZFM6', '0.1'))
        txs, _ = _parse(IbBrokerage(), body)
        self.assertEqual(txs[0]['date_settle'], '2026-06-26')

    def test_ordinary_option_trade_still_t_plus_1(self):
        body = (IB_TRADES_HDR
                + _ib_trade(self.OPT, 'QZX 15JAN27 70 C',
                            '2026-12-31, 10:00:00', 1, 5.00, -500, -1, 'O'))
        txs, _ = _parse(IbBrokerage(), body)
        # 2027-01-01 is New Year's Day (a Friday): next settlement Monday.
        self.assertEqual(txs[0]['date_settle'], '2027-01-04')


WB_HEAD = (',,,,,,,,,\n'
           '"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\n'
           'Symbole","Security Description\nDescription des titres",'
           'Type Code of Securities Code de genre de titres,"Quantity of '
           'Securities Quantité\nde titres","Price\nPrix",,Proceeds of '
           'Disposition or Settlement Amount Produits de disposition\n'
           ',,,,,,,,,\n')


class TestWebullExpiry(unittest.TestCase):
    # Real row shape: the expiry row (no price, no proceeds) is dated the
    # EXPIRY; the 0DTE buy is dated its SETTLEMENT (next business day).
    CSV = WB_HEAD + (
        'USD,27-02-2025,SELL,,CALL QZQ02/27/25 511,,-4,,,\n'
        'USD,28-02-2025,BUY,,CALL QZQ02/27/25 511,,4,2.35,,(943.96)\n'
        'USD,04-03-2025,BUY,@QZW,QZW CORP,,10,20.00,,(200.00)\n')

    def test_expiry_row_not_shifted(self):
        txs, _ = _parse(WebullBrokerage(), self.CSV)
        exp = next(t for t in txs if t['quantity'] == -4)
        self.assertEqual(exp['date'], '2025-02-27')
        self.assertEqual(exp['date_settle'], '2025-02-27')
        self.assertEqual(exp['time'], '16:00:00')
        buy = next(t for t in txs if t['quantity'] == 4)
        self.assertEqual(buy['date'], '2025-02-27')   # T+1 walked back
        self.assertEqual(buy['date_settle'], '2025-02-27')  # clamped
        # Non-option trades keep the settle-date semantics.
        eq = next(t for t in txs if t['symbol'] == 'QZW.US')
        self.assertEqual(eq['date_settle'], '2025-03-04')
        self.assertEqual(eq['date'], '2025-03-03')

    def test_long_expiry_is_long_loss_not_a_write(self):
        txs, _ = _parse(WebullBrokerage(), self.CSV)
        gains = [g for g in _gains(txs) if g['symbol'].startswith('QZQ')]
        self.assertEqual(len(gains), 1)
        self.assertEqual(gains[0]['direction'], 'LONG')
        self.assertAlmostEqual(gains[0]['gain'], -943.96)
        self.assertAlmostEqual(gains[0]['proceeds'], 0.0)


QT_HEAD = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
           'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
           'Account #,Activity Type,Account Type\n')


class TestQuestradeExpiry(unittest.TestCase):
    def test_exp_posted_next_business_day_books_on_expiry(self):
        # Friday 2027-12-31 expiry posted Monday 2028-01-03, settle blank.
        csv = QT_HEAD + (
            '2027-12-01 12:00:00 AM,2027-12-02 12:00:00 AM,Buy,,'
            'CALL QZD 12/31/27 35 QZD CORP,3,1.00,-300,-9.95,-309.95,'
            'USD,55500001,Trades,Individual\n'  # pii-ok (synthetic)
            '2028-01-03 12:00:00 AM,,EXP,,'
            'CALL QZD 12/31/27 35 QZD CORP OPTION EXPIRATION - EXPIRED,'
            '-3,0,0,0,0,USD,55500001,Trades,Individual\n')  # pii-ok
        txs, _ = _parse(QuestradeBrokerage(), csv)
        exp = next(t for t in txs if t['quantity'] < 0)
        self.assertEqual(exp['symbol'], 'QZD271231C00035000.US')
        self.assertEqual(exp['date'], '2027-12-31')
        self.assertEqual(exp['date_settle'], '2027-12-31')
        gains = _gains(txs)
        self.assertEqual(gains[0]['date_settle'], '2027-12-31')

    @rule("CA-DATE-08")
    def test_expiry_date_guard(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        f = BaseBrokerage.option_expiry_booking_date
        self.assertEqual(f('2025-07-21', '07/18/25'), '2025-07-18')
        # Far from the posting date / garbled: keep the posting date.
        self.assertEqual(f('2025-09-21', '07/18/25'), '2025-09-21')
        self.assertEqual(f('2025-07-21', '13/45/25'), '2025-07-21')
        self.assertEqual(f('2025-07-18', '07/18/25'), '2025-07-18')


RBC_HEAD = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
            '"Price","Settlement Date","Account","Value","Currency",'
            '"Description"\n')


class TestRbcExpiry(unittest.TestCase):
    def test_exp_posted_monday_books_friday_expiry(self):
        csv = RBC_HEAD + (
            '"January 3, 2028","Reorganization","8QZQZQ1","","-5","",'
            '"","55500001","0","CAD","EXP - CALL .QZB   12/31/27    48 '
            'QZB INC OPTION EXPIRATION - EXPIRED"\n')  # pii-ok (synthetic)
        txs, _ = _parse(RbcBrokerage(), csv)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['date'], '2027-12-31')
        self.assertEqual(txs[0]['date_settle'], '2027-12-31')
        self.assertEqual(txs[0]['action'], 'BUYSELL')

    def test_blank_settle_option_trade_gets_iso_t_plus_1(self):
        # The fallback used to parse the raw "January 5, 2028" cell with
        # %m/%d/%Y only and return it verbatim as date_settle.
        csv = RBC_HEAD + (
            '"January 5, 2028","Buy","8QZQZQ2","","2","1.10","",'
            '"55500001","-221.00","CAD","CALL .QZB   06/16/28    30 QZB '
            'INC OPEN CONTRACT"\n')  # pii-ok (synthetic)
        txs, _ = _parse(RbcBrokerage(), csv)
        self.assertEqual(txs[0]['date_settle'], '2028-01-06')


KR_HEAD_2026 = ('"txid","refid","time","type","subtype","aclass","subclass",'
                '"asset","wallet","amount","fee","balance","amountusd",'
                '"feeusd","balanceusd","feecurrency"\n')
KR_HEAD_2025 = ('"txid","refid","time","type","subtype","aclass","subclass",'
                '"asset","wallet","amount","fee","balance"\n')


class TestKrakenCoinUnitFees(unittest.TestCase):
    def test_2026_reward_books_net_coins_and_net_usd(self):
        csv = KR_HEAD_2026 + (
            '"L1","E1","2026-01-14 10:00:00","earn","reward","currency",'
            '"","QZC","earn / bonded","0.2000000000","0.0500000000",'
            '"10.1500000000","30.0000000000","7.5000000000",'
            '"1522.50","QZC"\n')
        txs, _ = _parse(KrakenBrokerage(), csv, prefix='kr_ledgers_')
        div = next(t for t in txs if t['action'] == 'DIVIDEND')
        buy = next(t for t in txs if t['action'] == 'BUYSELL')
        for leg in (div, buy):
            self.assertAlmostEqual(leg['quantity'], 0.15)
            self.assertAlmostEqual(leg['net_amount'], 22.5)
            self.assertAlmostEqual(leg['price'], 150.0)
        self.assertAlmostEqual(div['gross_amount'], 22.5)
        # The coin fee never lands in the USD fee field.
        self.assertEqual(buy['fee'], 0.0)
        self.assertIn('net of Kraken commission', div['description'])

    def test_2026_reward_without_feeusd_scales(self):
        csv = KR_HEAD_2026 + (
            '"L1","E1","2026-01-14 10:00:00","earn","reward","currency",'
            '"","QZC","spot / main","0.2000000000","0.0500000000",'
            '"0.15","30.0000000000","","","QZC"\n')
        txs, _ = _parse(KrakenBrokerage(), csv, prefix='kr_ledgers_')
        div = next(t for t in txs if t['action'] == 'DIVIDEND')
        self.assertAlmostEqual(div['net_amount'], 22.5)

    def test_pre_2026_reward_books_net_coins_unpriced(self):
        csv = KR_HEAD_2025 + (
            '"L1","E1","2025-11-19 10:00:00","earn","reward","currency",'
            '"","QZC","spot / main","0.0600000000","0.0180000000",'
            '"5.0420000000"\n')
        txs, _ = _parse(KrakenBrokerage(), csv, prefix='kr_ledgers_')
        div = next(t for t in txs if t['action'] == 'DIVIDEND')
        buy = next(t for t in txs if t['action'] == 'BUYSELL')
        self.assertAlmostEqual(div['quantity'], 0.042)
        self.assertAlmostEqual(buy['quantity'], 0.042)
        self.assertEqual(buy['price'], 0.0)       # fill-crypto prices it
        self.assertEqual(buy['fee'], 0.0)

    def test_fee_free_reward_unchanged(self):
        csv = KR_HEAD_2026 + (
            '"L1","E1","2026-03-18 10:00:00","earn","reward","currency",'
            '"stable_coin","USDC","spot / main","0.50000000","0",'
            '"100.5","0.4999","0","100.49","USDC"\n')
        txs, _ = _parse(KrakenBrokerage(), csv, prefix='kr_ledgers_')
        self.assertEqual([t['action'] for t in txs], ['DIVIDEND'])
        self.assertAlmostEqual(txs[0]['quantity'], 0.5)
        self.assertEqual(txs[0]['description'], 'Staking Reward')

    @rule("CA-CRYPTO-04")
    def test_instant_buy_receive_fee_reduces_coins(self):
        csv = KR_HEAD_2025 + (
            '"L1","R1","2025-07-25 18:40:00","spend","","currency","",'
            '"ZCAD","spot / main","-100.0000","0","0"\n'
            '"L2","R1","2025-07-25 18:40:00","receive","","currency","",'
            '"QZC","spot / main","1.0000000000","0.0100000000",'
            '"0.9900000000"\n')
        txs, _ = _parse(KrakenBrokerage(), csv, prefix='kr_ledgers_')
        buy = next(t for t in txs if t['action'] == 'BUYSELL')
        self.assertAlmostEqual(buy['quantity'], 0.99)
        self.assertAlmostEqual(buy['net_amount'], 100.0)

    @rule("CA-CRYPTO-04")
    def test_instant_sell_spend_fee_adds_coins(self):
        csv = KR_HEAD_2025 + (
            '"L1","R1","2025-07-25 18:40:00","spend","","currency","",'
            '"QZC","spot / main","-1.0000000000","0.0100000000","0"\n'
            '"L2","R1","2025-07-25 18:40:00","receive","","currency","",'
            '"ZCAD","spot / main","100.0000","0","100"\n')
        txs, _ = _parse(KrakenBrokerage(), csv, prefix='kr_ledgers_')
        sell = next(t for t in txs if t['action'] == 'BUYSELL')
        self.assertAlmostEqual(sell['quantity'], -1.01)

    def test_hybridearnwithdrawal_is_custody_evidence(self):
        # Row shape: funding-style refid, no counter-leg in any earn
        # wallet, spot balance swept to dust — the coins left the ledger.
        csv = KR_HEAD_2026 + (
            '"L1","FTQZQZQ","2026-05-10 04:37:05","hybridearnwithdrawal",'
            '"","currency","stable_coin","USDC","spot / main",'
            '"-500.00000000","0","0.00000032","-499.9","0","0",""\n')
        txs, _ = _parse(KrakenBrokerage(), csv, prefix='kr_ledgers_')
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['action'], 'TRANSFER')
        self.assertEqual(txs[0]['symbol'], 'USDC')
        self.assertAlmostEqual(txs[0]['quantity'], -500.0)
        self.assertEqual(txs[0]['description'], 'hybridearnwithdrawal')


class TestTtOptionMultiplier(unittest.TestCase):
    def _warn(self, line):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            parse_tt_line(line, account_name='m')
        return err.getvalue()

    def test_option_line_checked_with_x100(self):
        # 2 contracts @ 1.50 = 300 notional; buy net 301.30 with fee.
        self.assertEqual(self._warn(
            'BUYSELL 2026-06-17 13:52:00 QZT270115C00088000.US 2 USD '
            '1.50 301.30 1.30'), '')
        self.assertEqual(self._warn(
            'BUYSELL 2026-06-17 13:52:00 QZT270115C00088000.US -2 USD '
            '1.50 298.70 1.30'), '')

    def test_option_typo_still_warns_and_names_x100(self):
        w = self._warn('BUYSELL 2026-06-17 13:52:00 QZT270115C00088000.US '
                       '2 USD 1.50 3.01 1.30')
        self.assertIn('qty*price*100', w)

    def test_equity_line_unchanged(self):
        self.assertEqual(self._warn(
            'BUYSELL 2026-06-17 13:52:00 QZT.US 100 USD 1.50 151.30 1.30'),
            '')


class TestIbAccrualRevisedPayDate(unittest.TestCase):
    ACCR_HDR = ('Change in Dividend Accruals,Header,Asset Category,Currency,'
                'Account,Symbol,Date,Ex Date,Pay Date,Quantity,Tax,Fee,'
                'Gross Rate,Gross Amount,Net Amount,Code\n')
    DIV_HDR = 'Dividends,Header,Currency,Account,Date,Description,Amount\n'

    def _accr(self, date, pay, rate, gross, code):
        return ('Change in Dividend Accruals,Data,Stocks,USD,U5550001,'  # pii-ok
                f'QZE,{date},2026-02-17,{pay},900,0,0,{rate},{gross},'
                f'{gross},{code}\n')

    def test_po_re_with_revised_pay_date_net_out(self):
        # Po carries pay 03-01 (USD estimate), Re pay 03-02; the cash
        # posts 03-02 in CAD. Pre-fix: "accrued but not booked".
        body = (self.ACCR_HDR
                + self._accr('2026-02-16', '2026-03-01', 0.7, 630, 'Po')
                + self._accr('2026-03-02', '2026-03-02', 0.97, -630, 'Re')
                + self.DIV_HDR
                + 'Dividends,Data,CAD,U5550001,2026-03-02,'  # pii-ok
                  'QZE (CA0000000011) Cash Dividend CAD 0.97 '
                  '(Ordinary Dividend),873\n')
        _, err = _parse(IbBrokerage(), body)
        self.assertNotIn('accrued but not', err)

    def test_unreversed_accrual_paid_nearby_in_other_currency(self):
        body = (self.ACCR_HDR
                + self._accr('2026-02-16', '2026-03-01', 0.7, 630, 'Po')
                + self.DIV_HDR
                + 'Dividends,Data,CAD,U5550001,2026-03-02,'  # pii-ok
                  'QZE (CA0000000011) Cash Dividend CAD 0.97 '
                  '(Ordinary Dividend),873\n')
        _, err = _parse(IbBrokerage(), body)
        self.assertNotIn('accrued but not', err)

    def test_truly_open_accrual_still_warns(self):
        body = (self.ACCR_HDR
                + self._accr('2026-02-16', '2026-03-01', 0.7, 630, 'Po'))
        _, err = _parse(IbBrokerage(), body)
        self.assertIn('accrued but not', err)


class TestFuturesShortNotPhantom(unittest.TestCase):
    def test_futures_sell_to_open_not_flagged(self):
        from taxjson.lib.phantom_holdings import detect_phantoms
        body = (IB_TRADES_HDR
                + _ib_trade('Futures', 'QZFK6', '2026-05-14, 21:19:06',
                            1, 80000, 8000, -2, 'O')
                + _ib_trade('Futures', 'QZFK6', '2026-05-29, 06:15:26',
                            -2, 73000, 14600, -4, 'C;O')
                + _ib_trade('Futures', 'QZFK6', '2026-05-29, 06:15:54',
                            1, 73100, 7310, -2, 'C')
                + _ib_trade('Stocks', 'QZS', '2026-05-29, 10:00:00',
                            -5, 10, 50, -1, 'O')
                + _ib_fii_futures('QZFK6', '0.1'))
        txs, _ = _parse(IbBrokerage(), body)
        fields = TaxTransaction.__dataclass_fields__
        tts = [TaxTransaction(**{k: v for k, v in r.items() if k in fields})
               for r in txs]
        syms = {c.symbol for c in detect_phantoms(tts)}
        self.assertNotIn('F:QZFK6.US', syms)
        self.assertIn('QZS.US', syms)          # equities still flagged
        self.assertIn('F:QZFK6.US',
                      {c.symbol for c in detect_phantoms(
                          tts, include_options=True)})


class TestForeignRocCli(unittest.TestCase):
    def test_brokerage_flag_reaches_parser(self):
        import subprocess
        import sys
        import json
        body = ('Dividends,Header,Currency,Account,Date,Description,Amount\n'
                'Dividends,Data,USD,U5550001,2026-06-30,'  # pii-ok
                'QZRX(US0000000017) Return of Capital USD 0.12 per Share,'
                '24.00\n')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'ib.csv'
            p.write_text(body, encoding='utf-8')
            out = {}
            for mode in ('dividend', 'acb'):
                r = subprocess.run(
                    [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
                     '--brokerage', 'ib', '--foreign-roc', mode, str(p)],
                    capture_output=True, text=True,
                    env={**os.environ,
                         'PYTHONPATH': str(Path(__file__).resolve()
                                           .parents[1] / 'src')})
                self.assertEqual(r.returncode, 0, r.stderr)
                data = json.loads(r.stdout)
                txs = (data['transactions'] if isinstance(data, dict)
                       else data)
                out[mode] = [t['action'] for t in txs]
        self.assertEqual(out['dividend'], ['DIVIDEND'])
        self.assertEqual(out['acb'], ['ADJUST'])

    def test_settings_key_validated(self):
        from taxjson.bin import taxjson_run as run
        self.assertIn('foreign_return_of_capital', run._SETTINGS_KEYS)
        cfg = {'settings': {'year': 2026, 'country': 'canada',
                            'base_currency': 'CAD',
                            'foreign_return_of_capital': 'acb'},
               'accounts': {}}
        warnings = run.validate_config(cfg)
        self.assertFalse([w for w in warnings
                          if 'foreign_return_of_capital' in w])
        cfg['settings']['foreign_return_of_capital'] = 'nope'
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                run.validate_config(cfg)


if __name__ == '__main__':
    unittest.main()
