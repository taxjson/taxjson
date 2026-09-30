"""Regression pins for the 2026-09 IB parse hardening.

The failure class: a missing or renamed column read as 0, a rebate read
as a charge, a levy counted twice, a cancelled corporate action left
booked — each "parses fine" and files the wrong number. Every fixture
here is SYNTHETIC (fake account ids — see _trade() — made-up ISINs
and conids).
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import BrokerageParseError
from taxjson.lib.brokerages.ib_extractor import IbBrokerage

HEAD = ('Statement,Header,Field Name,Field Value\n'
        'Statement,Data,BrokerName,Interactive Brokers\n'
        'Statement,Data,Title,Activity Statement\n')
TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
            'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
            'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
FUT_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
         'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
         'Notional Value,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
CA_H = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
        'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
        'Code\n')
FII_H = ('Financial Instrument Information,Header,Asset Category,Symbol,'
         'Description,Conid,Underlying,Listing Exch,Multiplier,Expiry,'
         'Delivery Month,Type,Strike,Code\n')
CASH_H = 'Cash Report,Header,Currency Summary,Currency,Total,\n'


def _parse(text):
    with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False,
                                     encoding='utf-8') as f:
        f.write(text)
        name = f.name
    parser = IbBrokerage()
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            txs = parser.parse_file(Path(name))
    finally:
        os.remove(name)
    return parser, txs, err.getvalue()


def _trade(sym, when, qty, price, proceeds, comm, code='O',
           cat='Stocks', cur='USD', acct='U5550001'):  # pii-ok
    return (f'Trades,Data,Order,{cat},{cur},{acct},{sym},"{when}",{qty},'
            f'{price},0,{proceeds},{comm},0,0,0,{code}\n')


class TestCommissionRebateSign(unittest.TestCase):
    """Positive Comm/Fee is a REBATE (option exchange/ORF credits). The
    old abs() booked it as a charge: 2x the rebate wrong on every such
    row (245.95 USD over 65 rows in the real 2025 margin statement)."""

    def test_sell_with_rebate_nets_more_cash_and_a_negative_fee(self):
        _, txs, _ = _parse(HEAD + TRADES_H + _trade(
            'ANET 15AUG25 120 C', '2025-07-14, 10:43:52', -2, 2.9, 580,
            0.58712, cat='Equity and Index Options'))
        t = txs[0]
        self.assertAlmostEqual(t['fee'], -0.58712)
        self.assertAlmostEqual(t['net_amount'], 580.58712,
                               msg="cash received = Proceeds + Comm/Fee")

    def test_buy_with_rebate_costs_less(self):
        _, txs, _ = _parse(HEAD + TRADES_H + _trade(
            'ACM 18DEC26 70 C', '2026-05-22, 15:28:26', 2, 10.65, -2130,
            0.7035, cat='Equity and Index Options'))
        self.assertAlmostEqual(txs[0]['fee'], -0.7035)
        self.assertAlmostEqual(txs[0]['net_amount'], 2129.2965,
                               msg="cost = -(Proceeds + Comm/Fee) == Basis")

    def test_ordinary_charge_unchanged(self):
        _, txs, _ = _parse(HEAD + TRADES_H + _trade(
            'MSFT', '2026-02-05, 09:31:00', 50, 400, -20000, -1))
        self.assertAlmostEqual(txs[0]['fee'], 1.0)
        self.assertAlmostEqual(txs[0]['net_amount'], 20001.0)


class TestRequiredColumns(unittest.TestCase):
    ROW = _trade('MSFT', '2026-02-05, 09:31:00', 50, 400, -20000, -1)

    def _renamed(self, old, new):
        cells = TRADES_H.rstrip('\n').split(',')
        hdr = ','.join(new if c == old else c for c in cells) + '\n'
        self.assertNotEqual(hdr, TRADES_H)
        return HEAD + hdr + self.ROW

    def test_each_trades_money_column_is_required(self):
        for col, new in (('Comm/Fee', 'Commission'),
                         ('Proceeds', 'Amount'),
                         ('T. Price', 'Trade Price'),
                         ('Quantity', 'Qty'),
                         ('Currency', 'Devise'),
                         ('Date/Time', 'When'),
                         ('Code', 'Codes')):
            with self.subTest(col=col):
                with self.assertRaises(BrokerageParseError) as cm:
                    _parse(self._renamed(col, new))
                self.assertIn("'Trades'", str(cm.exception))
                self.assertIn(repr(col), str(cm.exception))

    def test_each_money_section_requires_its_columns(self):
        cases = {
            'Dividends': 'Dividends,Header,Currency,Date,Description,Total\n'
                         'Dividends,Data,USD,2026-01-02,X(US0000000001) '
                         'Cash Dividend,5\n',
            'Withholding Tax': 'Withholding Tax,Header,Currency,Date,'
                               'Description,Amt\nWithholding Tax,Data,USD,'
                               '2026-01-02,X Tax,-1\n',
            'Interest': 'Interest,Header,Currency,Day,Description,Amount\n'
                        'Interest,Data,USD,2026-01-02,Credit Interest,1\n',
            'Fees': 'Fees,Header,Subtitle,Currency,Date,Description\n'
                    'Fees,Data,Other Fees,USD,2026-01-02,Data fee\n',
            'Corporate Actions': CA_H.replace(',Value,', ',Val,')
            + 'Corporate Actions,Data,Stocks,USD,2026-01-02,'
              '"2026-01-02, 20:25:00","X(US0000000001) Split 2 for 1 '
              '(X, X CORP, US0000000001)",10,0,0,0,\n',
            'Transfers': 'Transfers,Header,Asset Category,Currency,Symbol,'
                         'Date,Type,Direction,Xfer Company,Xfer Account,'
                         'Qty,Xfer Price,Value,Realized P/L,Cash Amount,'
                         'Code\nTransfers,Data,Stocks,USD,X,2026-01-02,'
                         'ATON,In,Broker,000,10,5,50,0,0,\n',
        }
        for section, body in cases.items():
            with self.subTest(section=section):
                with self.assertRaises(BrokerageParseError) as cm:
                    _parse(HEAD + body)
                self.assertIn(repr(section), str(cm.exception))

    def test_non_iso_trade_date_is_refused(self):
        for when in ('20250328;093000', '03/28/2025, 09:30:00'):
            with self.subTest(when=when):
                with self.assertRaises(BrokerageParseError):
                    _parse(HEAD + TRADES_H + _trade(
                        'MSFT', when, 50, 400, -20000, -1))

    def test_decimal_comma_money_is_refused(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(HEAD + TRADES_H + _trade(
                'MSFT', '2026-02-05, 09:31:00', 50, 400, '"-20000,00"', -1))
        self.assertIn('Proceeds', str(cm.exception))

    def test_parenthesized_negative_is_accepted(self):
        _, txs, _ = _parse(HEAD + TRADES_H + _trade(
            'MSFT', '2026-02-05, 09:31:00', 50, 400, '"(20,000.00)"',
            '(1.00)'))
        self.assertAlmostEqual(txs[0]['net_amount'], 20001.0)


class TestPerRowMoneyCheck(unittest.TestCase):
    def test_header_only_swap_of_proceeds_and_comm_fails(self):
        swapped = TRADES_H.replace('Proceeds,Comm/Fee', 'Comm/Fee,Proceeds')
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(HEAD + swapped + _trade(
                'MSFT', '2026-02-05, 09:31:00', 50, 400, -20000, -1))
        self.assertIn('multiplier', str(cm.exception))

    def test_futures_multiplier_comes_from_the_instrument_list(self):
        fii = (FII_H + 'Financial Instrument Information,Data,Futures,'
               'QZCLZ5,QZCL DEC25,999000011,QZCL,NYMEX,"1,000",'
               '2025-11-20,2025-12,,,\n')
        body = (HEAD + FUT_H + _trade('QZCLZ5', '2025-10-19, 18:13:50', 1,
                                      57.4, -57400, -2.37, cat='Futures')
                + fii)
        _, txs, _ = _parse(body)
        t = txs[0]
        self.assertEqual(t['multiplier'], 1000.0)
        self.assertEqual(t['symbol'], 'F:QZCLZ5.US')
        # Without the instrument list the contract size is not guessed.
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(HEAD + FUT_H + _trade('QZCLZ5', '2025-10-19, 18:13:50',
                                         1, 57.4, -57400, -2.37,
                                         cat='Futures'))
        self.assertIn('multiplier', str(cm.exception))

    def test_micro_future_fractional_multiplier(self):
        fii = (FII_H + 'Financial Instrument Information,Data,Futures,'
               'QZMETK6,QZMET MAY26,999000012,QZMET,CME,0.1,2026-05-29,'
               '2026-05,,,\n')
        _, txs, _ = _parse(HEAD + FUT_H + _trade(
            'QZMETK6', '2026-05-14, 21:19:06', 1, 2400, -240, -1.5,
            cat='Futures') + fii)
        self.assertEqual(txs[0]['multiplier'], 0.1)


class TestFuturesOptionExpiry(unittest.TestCase):
    FOP = ('Financial Instrument Information,Data,Options On Futures,'
           'QZLOF6 P5200,QZCL JAN26 52 P,999000021,QZCLF6,NYMEX,"1,000",'
           '2025-12-16,2026-01,P,52,\n')

    def test_monthly_fop_uses_the_real_expiry(self):
        _, txs, _ = _parse(HEAD + TRADES_H + _trade(
            'QZCL JAN26 52 P', '2025-11-12, 14:40:26', 1, 0.39, -390, -2.37,
            cat='Options On Futures') + FII_H + self.FOP)
        self.assertEqual(txs[0]['symbol'], 'F:QZCL251216P00052000.US',
                         "expiry 2025-12-16 from the instrument list, not "
                         "a placeholder day 20 of the delivery month")
        self.assertEqual(txs[0]['multiplier'], 1000.0)


class TestTransactionFeesAreABreakdown(unittest.TestCase):
    def test_awe_fee_is_the_comm_fee_column_only(self):
        body = (HEAD + TRADES_H + _trade(
            'AWE', '2025-03-28, 09:06:18', '"10,000"', 0.988, -9880, -54.34,
            code='O;P', cur='GBP')
            + 'Transaction Fees,Header,Asset Category,Currency,Account,'
              'Date/Time,Symbol,Description,Quantity,Trade Price,Amount,'
              'Code\n'
              'Transaction Fees,Data,Stocks,GBP,U5550001,'  # pii-ok
              '"2025-03-28, 09:06:18",AWE,UK Stamp Tax,"8,900",0.988,'
              '-43.966,\n'
              'Transaction Fees,Data,Stocks,GBP,U5550001,'  # pii-ok
              '"2025-03-28, 09:06:18",AWE,UK Stamp Tax,"1,100",0.988,'
              '-5.434,\n'
            + CASH_H
            + 'Cash Report,Data,Commissions,GBP,-4.94,\n'
              'Cash Report,Data,Transaction Fees,GBP,-49.40,\n'
              'Cash Report,Data,Trades (Purchase),GBP,-9880,\n')
        _, txs, _ = _parse(body)
        self.assertEqual(len(txs), 1)
        self.assertAlmostEqual(txs[0]['fee'], 54.34, places=6,
                               msg="was 103.74: the levy folded twice")
        self.assertAlmostEqual(txs[0]['net_amount'], 9934.34, places=6)


class TestCashReportReconciliation(unittest.TestCase):
    BODY = (HEAD + TRADES_H
            + _trade('MSFT', '2026-02-05, 09:31:00', 50, 400, -20000, -1)
            + 'Dividends,Header,Currency,Date,Description,Amount\n'
              'Dividends,Data,USD,2026-06-12,MSFT(US9990000101) Cash '
              'Dividend USD 0.75 per Share,37.50\n')

    def _cash(self, div):
        return (CASH_H
                + 'Cash Report,Data,Starting Cash,Base Currency Summary,0,\n'
                  'Cash Report,Data,Commissions,USD,-1,\n'
                  'Cash Report,Data,Trades (Purchase),USD,-20000,\n'
                  f'Cash Report,Data,Dividends,USD,{div},\n')

    def test_reconciled_statement_parses(self):
        _, txs, _ = _parse(self.BODY + self._cash('37.50'))
        self.assertEqual(len(txs), 2)

    def test_mismatch_names_currency_and_line(self):
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(self.BODY + self._cash('75.00'))
        msg = str(cm.exception)
        self.assertIn('USD Dividends', msg)
        self.assertIn('37.50', msg)
        self.assertIn('75.00', msg)

    def test_unhandled_asset_class_cash_fails_reconciliation(self):
        body = (self.BODY + _trade('QZBOND', '2026-03-01, 10:00:00', 1000,
                                   1, -1000, -1, cat='Bonds')
                + self._cash('37.50').replace(
                    'Trades (Purchase),USD,-20000',
                    'Trades (Purchase),USD,-21000').replace(
                    'Commissions,USD,-1', 'Commissions,USD,-2'))
        # The row itself is refused now, before the reconciliation
        # (audit S060-08: without a Cash Report it was a counted skip).
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(body)
        self.assertIn("asset category 'Bonds'", str(cm.exception))


class TestUnknownSections(unittest.TestCase):
    def test_localized_money_section_is_an_error(self):
        body = (HEAD + 'Dividendes,Header,Devise,Date,Description,Amount\n'
                'Dividendes,Data,USD,2026-01-02,X Cash Dividend,5\n')
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(body)
        self.assertIn("'Dividendes'", str(cm.exception))

    def test_unknown_non_money_section_warns_loudly(self):
        parser, _, err = _parse(HEAD + 'Widget Notes,Header,Note\n'
                                'Widget Notes,Data,hello\n')
        self.assertIn("unknown IB section 'Widget Notes'", err)
        self.assertEqual(parser._skip_counts.get(
            'section Widget Notes (unknown)'), 1)

    def test_known_metadata_stays_quiet(self):
        parser, _, err = _parse(HEAD + 'Net Asset Value,Header,Asset Class,'
                                'Prior Total\nNet Asset Value,Data,Cash,5\n')
        self.assertNotIn('unknown IB section', err)


class TestStatementKind(unittest.TestCase):
    def test_realized_summary_is_refused(self):
        body = HEAD.replace('Activity Statement', 'Realized Summary') + (
            TRADES_H + _trade('MSFT', '2026-02-05, 09:31:00', 50, 400,
                              -20000, -1))
        with self.assertRaises(BrokerageParseError) as cm:
            _parse(body)
        self.assertIn('Realized Summary', str(cm.exception))


class TestConsolidatedStatementWarning(unittest.TestCase):
    def test_two_accounts_warn_masked(self):
        body = HEAD + TRADES_H + _trade(
            'MSFT', '2026-02-05, 09:31:00', 50, 400, -20000, -1,
            acct='U5550001') + _trade(  # pii-ok
            'MSFT', '2026-02-06, 09:31:00', -50, 401, 20050, -1,
            acct='U5550002')  # pii-ok
        _, _, err = _parse(body)
        self.assertIn('spans 2 accounts', err)
        self.assertIn('U5***', err)
        self.assertNotIn('U5550001', err, "account ids are masked")  # pii-ok


class TestCorporateActionCancellation(unittest.TestCase):
    DESC3 = ('"QZX(US9990000301) Split 3 for 1 (QZX, QZX CORP, '
             'US9990000301)"')
    DESC2 = ('"QZX(US9990000301) Split 2 for 1 (QZX, QZX CORP, '
             'US9990000301)"')

    def _ca(self, desc, qty, code='', when='2026-03-02, 20:25:00'):
        return (f'Corporate Actions,Data,Stocks,USD,2026-03-02,"{when}",'
                f'{desc},{qty},0,0,0,{code}\n')

    def test_split_booked_cancelled_rebooked_leaves_one_split(self):
        body = (HEAD + CA_H + self._ca(self.DESC3, 200)
                + self._ca(self.DESC3, -200, 'Ca')
                + self._ca(self.DESC2, 100))
        parser, txs, _ = _parse(body)
        splits = [t for t in txs if t['action'] == 'SPLIT']
        self.assertEqual(len(splits), 1)
        self.assertAlmostEqual(splits[0]['quantity'], 2.0)
        self.assertEqual(parser._rows_seen, parser._rows_consumed
                         + sum(parser._skip_counts.values()))

    def test_cancellation_before_its_original_still_pairs(self):
        body = (HEAD + CA_H + self._ca(self.DESC3, -200, 'Ca')
                + self._ca(self.DESC3, 200) + self._ca(self.DESC2, 100))
        _, txs, _ = _parse(body)
        self.assertEqual([t['quantity'] for t in txs
                          if t['action'] == 'SPLIT'], [2.0])

    def test_cancelled_cash_in_lieu_is_removed(self):
        cil = ('"QZX(US9990000301) Cash in Lieu of Fractional Shares '
               '(QZX, QZX CORP, US9990000301)"')
        body = (HEAD + CA_H
                + 'Corporate Actions,Data,Stocks,USD,2026-03-02,'
                  f'"2026-03-02, 20:25:00",{cil},-0.5,12.5,0,0,\n'
                  'Corporate Actions,Data,Stocks,USD,2026-03-02,'
                  f'"2026-03-02, 20:25:00",{cil},0.5,-12.5,0,0,Ca\n')
        _, txs, _ = _parse(body)
        self.assertEqual(txs, [])

    def test_cancelled_merger_leaves_the_corp_actions_note(self):
        # Merger rows are taxjson-corp-actions' (not "unhandled"); a
        # cancelled leg drops out of the count.
        m = ('"QZM(US9990000401) Merged(Acquisition) WITH US9990000402 '
             '1 for 1 (QZM, QZM CORP, US9990000401)"')
        body = (HEAD + CA_H + self._ca(m, -10) + self._ca(m, 10, 'Ca')
                + self._ca(m, -10, when='2026-03-03, 20:25:00'))
        parser, _, err = _parse(body)
        self.assertIn('note: 1 merger/spin-off Corporate Action row', err,
                      "the cancelled leg no longer counts")

    def test_unmatched_cancellation_is_loud(self):
        parser, txs, err = _parse(HEAD + CA_H
                                  + self._ca(self.DESC3, -200, 'Ca'))
        self.assertEqual(txs, [])
        # Worded for the account: taxjson-brokerage first offers the
        # row to the account's other statements (audit S059-04).
        self.assertIn("its original row is not in this account's "
                      "statements", err)
        self.assertEqual(parser._skip_counts.get(
            'Corporate Actions Ca row whose original is not in this '
            'statement (see warning)'), 1)


class TestOptionRootAlias(unittest.TestCase):
    """After a corporate action IB renames an adjusted option's root
    (DFDV -> DFDV1): the opening sale is `QZD 21NOV25 12.5 P`, the
    assignment `QZD1 21NOV25 12.5 P`, and the instrument list shows one
    conid under both roots. One canonical symbol lets the assignment
    fold the premium."""

    FII = ('Financial Instrument Information,Data,Equity and Index '
           'Options,"QZD  251121P00012500, QZD1 251121P00012500",'
           'QZD 21NOV25 12.5 P,999000031,QZD,CBOE,100,2025-11-21,2025-11,'
           'P,12.5,\n'
           'Financial Instrument Information,Data,Equity and Index '
           'Options,QZD1 251121P00015000,QZD1 21NOV25 15 P,999000032,QZD1,'
           'CBOE,100,2025-11-21,2025-11,P,15,\n')

    def test_both_legs_share_one_symbol_and_the_premium_folds(self):
        body = (HEAD + TRADES_H
                + _trade('QZD 21NOV25 12.5 P', '2025-09-22, 15:59:28', -10,
                         1.41, 1410, -7.06, cat='Equity and Index Options')
                + _trade('QZD 21NOV25 15 P', '2025-10-07, 14:32:07', -5,
                         2.35, 1175, -3, cat='Equity and Index Options')
                + _trade('QZD1 21NOV25 12.5 P', '2025-11-21, 16:20:00', 10,
                         0, 0, 0, code='A;C', cat='Equity and Index Options')
                + _trade('QZD1 21NOV25 15 P', '2025-11-21, 16:20:00', 5,
                         0, 0, 0, code='A;C', cat='Equity and Index Options')
                + _trade('QZD', '2025-11-21, 16:20:00', 1000, 12.5, -12500,
                         0, code='A;O')
                + FII_H + self.FII)
        _, txs, err = _parse(body)
        syms = sorted({t['symbol'] for t in txs})
        self.assertEqual(syms, ['QZD.US', 'QZD251121P00012500.US',
                                'QZD251121P00015000.US'])
        self.assertIn('alias of QZD', err)
        # A ticker.map GLOBAL QZD1.US QZD.US rule (the manual workaround)
        # now has nothing left to rename: a no-op.
        self.assertFalse(any(s.startswith('QZD1') for s in syms))
        from taxjson.lib.core import CanadaTaxRules, TaxTransaction
        fields = TaxTransaction.__dataclass_fields__
        tts = [TaxTransaction(**{k: v for k, v in r.items() if k in fields})
               for r in txs]
        with contextlib.redirect_stderr(io.StringIO()) as e2:
            CanadaTaxRules().compute_gains(tts)
        self.assertNotIn('QZD251121P00012500', e2.getvalue(),
                         "no undrained premium / phantom on the option")


class TestAccrualCodeTokens(unittest.TestCase):
    def test_adr_po_and_re_are_paired(self):
        acc_h = ('Change in Dividend Accruals,Header,Asset Category,'
                 'Currency,Account,Symbol,Date,Ex Date,Pay Date,Quantity,'
                 'Tax,Fee,Gross Rate,Gross Amount,Net Amount,Code\n')
        row = ('Change in Dividend Accruals,Data,Stocks,USD,U5550001,'  # pii-ok
               'QZA,2026-02-10,2026-02-09,2026-03-01,100,0,0,0.5,{g},{g},'
               '{c}\n')
        body = (HEAD + acc_h + row.format(g='50', c='ADR;Po')
                + row.format(g='-50', c='ADR;Re'))
        parser, _, err = _parse(body)
        self.assertNotIn('accrued but not', err)
        self.assertEqual(parser._rows_consumed, 2,
                         "ADR;Po / ADR;Re were read as subtotals")



class TestIsinCountryFallback(unittest.TestCase):
    DIV = ('Dividends,Header,Currency,Date,Description,Amount\n'
           'Dividends,Data,USD,2026-01-02,QZN(NL9990000501) Cash Dividend '
           'USD 0.10 per Share,10\n')

    def test_unmapped_country_without_a_position_warns(self):
        _, txs, err = _parse(HEAD + self.DIV)
        self.assertEqual(txs[0]['symbol'], 'QZN.US')
        self.assertIn('QZN.US (ISIN NL)', err)

    def test_position_on_the_listing_confirms_it_quietly(self):
        _, _, err = _parse(HEAD + TRADES_H + _trade(
            'QZN', '2025-12-01, 10:00:00', 100, 5, -500, -1) + self.DIV)
        self.assertNotIn('ISIN NL', err)



class TestRunParsesStrict(unittest.TestCase):
    """`taxjson run` passes --strict to taxjson-brokerage: a schema
    ERROR stops the run instead of scrolling past as a warning."""

    def test_brokerage_stage_is_strict(self):
        import taxjson.bin.taxjson_run as run_mod

        class _Stop(Exception):
            pass

        seen = []

        def fake_run_to_file(cmd, out, **kwargs):
            if '--brokerage' in cmd:
                seen.append(list(cmd))
                raise _Stop()
            Path(out).write_text('{"transactions": []}\n')

        orig = run_mod.run_to_file
        run_mod.run_to_file = fake_run_to_file
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                acct = root / 'inputs' / 'tfsa'
                acct.mkdir(parents=True)
                (acct / 'ib.csv').write_text(HEAD + TRADES_H + _trade(
                    'MSFT', '2026-02-05, 09:31:00', 50, 400, -20000, -1))
                (root / 'work').mkdir()
                rates = root / 'work' / 'to_base.csv'
                rates.write_text('')
                settings = {'base_currency': 'CAD', 'country': 'canada',
                            'year': 2026, 'tax_date': 'settle'}
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    try:
                        run_mod.stage_account(
                            'tfsa', {'type': 'sheltered'}, settings,
                            root / 'inputs', root / 'work',
                            root / 'reports', rates, None, None, True)
                    except _Stop:
                        pass
        finally:
            run_mod.run_to_file = orig
        self.assertEqual(len(seen), 1)
        self.assertIn('--strict', seen[0])


if __name__ == '__main__':
    unittest.main()
