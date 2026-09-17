import unittest
import tempfile
import os
from pathlib import Path
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage

class TestRbcParser(unittest.TestCase):
    def test_option_parsing(self):
        content = """\"Date\",\"Activity\",\"Symbol\",\"Symbol Description\",\"Quantity\",\"Price\",\"Settlement Date\",\"Account\",\"Value\",\"Currency\",\"Description\"
\"January 30, 2025\",\"Sell\",\"8ABCDE1\",\"\",\"-25\",\"1.10\",\"January 31, 2025\",\"12345678\",\"2739.05\",\"CAD\",\"CALL .QQZ   06/20/25    30 QQZ HOLDINGS INC UNSOLICITED PROSPECTUS ENCLOSED CA CLOSE CONTRACT\"
\"January 15, 2025\",\"Buy\",\"8ABCDE1\",\"\",\"20\",\"0.35\",\"January 16, 2025\",\"12345678\",\"-710.95\",\"CAD\",\"CALL .QQZ   06/20/25    30 QQZ HOLDINGS INC UNSOLICITED PROSPECTUS ENCLOSED DA OPEN CONTRACT\"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(content)
            fname = f.name
            
        try:
            parser = RbcBrokerage()
            txs = parser.parse_file(Path(fname))
            self.assertEqual(len(txs), 2)
            self.assertEqual(txs[0]['symbol'], 'QQZ250620C00030000.TO')
            self.assertEqual(txs[1]['symbol'], 'QQZ250620C00030000.TO')
            self.assertEqual(txs[0]['quantity'], -25.0)
            self.assertEqual(txs[1]['quantity'], 20.0)
        finally:
            os.remove(fname)

    def test_expiry_worthless_stays_buysell(self):
        """Option EXPIRY-worthless must NOT become ASSIGN.

        ASSIGN means stock changes hands (premium rolls into the underlying);
        EXPIRY-worthless means the option just disappears and the premium
        gain/loss must be realized in place. RBC marks expiries as
        Activity='Reorganization' with desc 'EXP - CALL ... OPTION
        EXPIRATION - EXPIRED'. Earlier the rbc_direct fix accidentally
        grouped EXP with ASN, which silently dropped premium gains.
        """
        content = """\"Date\",\"Activity\",\"Symbol\",\"Symbol Description\",\"Quantity\",\"Price\",\"Settlement Date\",\"Account\",\"Value\",\"Currency\",\"Description\"
\"February 24, 2025\",\"Reorganization\",\"8DZNZG9\",\"\",\"-30\",\"\",\"February 24, 2025\",\"12345678\",\"0\",\"CAD\",\"EXP - CALL .BNS   02/21/25    82 BANK OF NOVA SCOTIA OPTION EXPIRATION - EXPIRED\"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(content)
            fname = f.name
        try:
            parser = RbcBrokerage()
            txs = parser.parse_file(Path(fname))
            self.assertEqual(len(txs), 1)
            self.assertEqual(txs[0]['symbol'], 'BNS250221C00082000.TO')
            self.assertEqual(txs[0]['action'], 'BUYSELL',
                             "EXP rows must stay BUYSELL — the engine then "
                             "realizes the premium gain/loss in place")
            self.assertEqual(txs[0]['quantity'], -30.0)
        finally:
            os.remove(fname)

    def test_assignment_emits_assign_action(self):
        """RBC option-leg assignment rows must produce action='ASSIGN'.

        RBC records an option that gets assigned via two rows:
          (a) Activity 'Other' with desc 'ASN - CALL ...  ASSIGNMENT OF OPTION'
              — the option-leg notification (qty 1, price 0, value 0).
          (b) Activity 'Sell'/'Buy' with desc '... ASSIGNMENT OF OPTION AS OF ...'
              — the stock-leg sale/purchase at strike.
        Only (a) should become action='ASSIGN'. (b) stays BUYSELL because the
        rolled premium is folded in via the engine's pending_adjustments.
        """
        content = """\"Date\",\"Activity\",\"Symbol\",\"Symbol Description\",\"Quantity\",\"Price\",\"Settlement Date\",\"Account\",\"Value\",\"Currency\",\"Description\"
\"May 16, 2025\",\"Other\",\"9BZYDS1\",\"\",\"1\",\"\",\"May 20, 2025\",\"12345678\",\"0\",\"USD\",\"ASN - CALL COIN   05/16/25   197.50 COINBASE GLOBAL INC ASSIGNMENT OF OPTION\"
\"May 16, 2025\",\"Sell\",\"COIN\",\"COINBASE GLOBAL INC CLASS A\",\"-100\",\"197.5\",\"May 20, 2025\",\"12345678\",\"19707\",\"USD\",\"COINBASE GLOBAL INC ASSIGNMENT OF OPTION AS OF 05/16/25\"
\"May 6, 2025\",\"Sell\",\"9BZYDS1\",\"\",\"-1\",\"9.2\",\"May 7, 2025\",\"12345678\",\"911.77\",\"USD\",\"CALL COIN   05/16/25   197.50 COINBASE GLOBAL INC UNSOLICITED CA OPEN CONTRACT\"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(content)
            fname = f.name

        try:
            parser = RbcBrokerage()
            txs = parser.parse_file(Path(fname))
            by_key = {(t['date'], t['symbol'], t['quantity']): t for t in txs}
            # Option assignment notification → ASSIGN
            opt_assign = by_key[('2025-05-16', 'COIN250516C00197500.US', 1.0)]
            self.assertEqual(opt_assign['action'], 'ASSIGN')
            # Stock-leg at strike → BUYSELL (rolled premium handled downstream)
            stock_sale = by_key[('2025-05-16', 'COIN.US', -100.0)]
            self.assertEqual(stock_sale['action'], 'BUYSELL')
            # Original sell-to-open → BUYSELL
            opt_open = by_key[('2025-05-06', 'COIN250516C00197500.US', -1.0)]
            self.assertEqual(opt_open['action'], 'BUYSELL')
        finally:
            os.remove(fname)


    def test_commission_recovered_from_amount_column(self):
        """RBC exports carry both `Value` (gross qty*price) and `Amount`
        (net of commission). The parser must read `Amount` so
        back_compute_fee can recover the ~$9.95 commission — reading
        `Value` instead silently drops every RBC trade's fee,
        understating buy cost basis and overstating sell proceeds."""
        content = (
            "Date,Activity,Symbol,Description,Quantity,Price,"
            "Settlement Date,Currency,Value,Amount\n"
            "01/12/2024,Buy,RY.TO,ROYAL BANK OF CANADA - Buy,100,130.00,"
            "01/15/2024,CAD,-13000.00,-13009.95\n"
            "09/10/2024,Sell,RY.TO,ROYAL BANK OF CANADA - Sell,100,165.00,"
            "09/12/2024,CAD,16500.00,16490.05\n"
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                          delete=False) as f:
            f.write(content)
            fname = f.name
        try:
            txs = RbcBrokerage().parse_file(Path(fname))
            by_qty = {t['quantity']: t for t in txs}
            buy, sell = by_qty[100.0], by_qty[-100.0]
            self.assertAlmostEqual(buy['fee'], 9.95, places=2)
            self.assertAlmostEqual(sell['fee'], 9.95, places=2)
            # net_amount is the commission-inclusive cash figure.
            self.assertAlmostEqual(buy['net_amount'], 13009.95, places=2)
            self.assertAlmostEqual(sell['net_amount'], 16490.05, places=2)
        finally:
            os.remove(fname)

    def test_falls_back_to_value_when_amount_absent(self):
        """Older RBC export variants ship only a `Value` column. The
        parser must still parse them (no commission recovery possible,
        but the trade is not lost)."""
        content = (
            '"Date","Activity","Symbol","Symbol Description","Quantity",'
            '"Price","Settlement Date","Account","Value","Currency",'
            '"Description"\n'
            '"January 30, 2025","Sell","SHOP","SHOPIFY INC","-100",'
            '"50.00","January 31, 2025","12345","5000.00","CAD","Sell"\n'
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                          delete=False) as f:
            f.write(content)
            fname = f.name
        try:
            txs = RbcBrokerage().parse_file(Path(fname))
            self.assertEqual(len(txs), 1)
            self.assertAlmostEqual(txs[0]['net_amount'], 5000.00, places=2)
        finally:
            os.remove(fname)


class TestRbcDividendClassifier(unittest.TestCase):
    """`_is_dividend` keys on whole-word matches of Dividend /
    Distribution. The earlier bare-substring match (`'Dist' in desc`)
    treated "Redistribution" / "Redistributed" as a dividend row,
    routing unrelated rows through the dividend builder."""

    def test_recognizes_dividend_and_distribution_words(self):
        rbc = RbcBrokerage()
        self.assertTrue(rbc._is_dividend('Other', 'CASH DIVIDEND ON 100 SHS'))
        self.assertTrue(rbc._is_dividend('Other', 'ETF DISTRIBUTION REINVESTED'))
        self.assertTrue(rbc._is_dividend('Other', 'Year-end Dist. payment'))

    def test_rejects_redistribution(self):
        rbc = RbcBrokerage()
        self.assertFalse(rbc._is_dividend('Other', 'Redistribution of units'))
        self.assertFalse(rbc._is_dividend('Other', 'Misdistributed entry'))
        self.assertFalse(rbc._is_dividend('Other', 'Interest accrual entry'))


class TestRbcDateHelpers(unittest.TestCase):
    """Module-level `parse_rbc_date` used to silently return
    `datetime.now()` on unparseable input, which caused fee/dividend
    rows with malformed dates to land in whatever tax year the
    pipeline happened to be run in. It now raises ValueError so the
    user sees the row that needs fixing rather than chasing a
    mysterious total later."""

    def test_parse_rbc_date_raises_on_garbage(self):
        from taxjson.lib.brokerages.rbc_direct import parse_rbc_date
        with self.assertRaises(ValueError) as cm:
            parse_rbc_date('not a date')
        self.assertIn('not a date', str(cm.exception))

    def test_parse_rbc_date_accepts_known_formats(self):
        from taxjson.lib.brokerages.rbc_direct import parse_rbc_date
        d1 = parse_rbc_date('January 30, 2025')
        self.assertEqual((d1.year, d1.month, d1.day), (2025, 1, 30))
        d2 = parse_rbc_date('1/30/2025')
        self.assertEqual((d2.year, d2.month, d2.day), (2025, 1, 30))


class TestRbcIsoDatetimeDates(unittest.TestCase):
    """Some RBC exports (round-tripped through Excel/pandas) ship the Date /
    Settlement Date columns as ISO datetimes like '2026-05-28 00:00:00'.
    Earlier these failed every known format, so the parser fell back to the
    RAW string and leaked a ' 00:00:00' suffix into `date` — which the gains
    engine's strptime(date, '%Y-%m-%d') then crashed on. Emitted dates must
    normalize to bare YYYY-MM-DD."""

    def test_iso_datetime_dates_normalize_to_date(self):
        content = (
            '"Date","Activity","Symbol","Symbol Description","Quantity",'
            '"Price","Settlement Date","Account","Value","Currency",'
            '"Description"\n'
            '"2026-02-25 00:00:00","Buy","SHOP","SHOPIFY INC","100","50.00",'
            '"2026-02-27 00:00:00","12345","-5000.00","CAD","Buy"\n'
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                          delete=False) as f:
            f.write(content)
            fname = f.name
        try:
            txs = RbcBrokerage().parse_file(Path(fname))
            self.assertEqual(len(txs), 1)
            self.assertEqual(txs[0]['date'], '2026-02-25')
            self.assertEqual(txs[0]['date_settle'], '2026-02-27')
        finally:
            os.remove(fname)


class TestRbcBomTolerance(unittest.TestCase):
    """The parser file-open now uses `encoding='utf-8-sig'` so a
    BOM-prefixed CSV (rare but real for some Windows-exported broker
    files) doesn't break first-column matching. Pins the contract."""

    def test_bom_prefixed_csv_still_parses(self):
        content = '﻿"Date","Activity","Symbol","Symbol Description",' \
                  '"Quantity","Price","Settlement Date","Account","Value",' \
                  '"Currency","Description"\n' \
                  '"January 30, 2025","Sell","SHOP","SHOPIFY INC",' \
                  '"-100","50.00","January 31, 2025","12345","5000.00",' \
                  '"CAD","Sell"\n'
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                          encoding='utf-8', delete=False) as f:
            f.write(content)
            fname = f.name
        try:
            txs = RbcBrokerage().parse_file(Path(fname))
            # BOM must be silently consumed — header still found, row
            # still parsed as a Sell of SHOP.
            self.assertEqual(len(txs), 1, f"expected 1 tx, got: {txs}")
            self.assertEqual(txs[0]['symbol'], 'SHOP.TO')
            self.assertLess(txs[0]['quantity'], 0)
        finally:
            os.remove(fname)


class TestRbcTransfers(unittest.TestCase):
    """RBC in-kind security transfers (Activity 'Transfers', e.g. a DTC
    transfer-in) must emit a TRANSFER row. Dropping them made the engine see
    only later sells and report a phantom short (the QCOM case). Cash moves
    ('Deposits & Contributions' / 'Withdrawals') have no symbol and stay out."""
    _HDR = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
            '"Settlement Date","Account","Value","Currency","Description"\n')

    def _parse(self, body):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(self._HDR + body)
            fname = f.name
        try:
            return RbcBrokerage().parse_file(Path(fname))
        finally:
            os.remove(fname)

    def test_in_kind_transfer_in_emits_transfer(self):
        txs = self._parse(
            '"2026-03-27 00:00:00","Transfers","QCOM","QUALCOMM INC","147","",'
            '"2026-03-27 00:00:00","1","0","USD","TFI - QUALCOMM INC DTC 8396"\n')
        tr = [t for t in txs if t['action'] == 'TRANSFER']
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]['symbol'], 'QCOM.US')
        self.assertEqual(tr[0]['quantity'], 147.0)

    def test_cash_transfer_is_not_a_security_transfer(self):
        # "Deposits & Contributions" with no symbol must not become a TRANSFER.
        txs = self._parse(
            '"2026-05-28 00:00:00","Deposits & Contributions","","","","",'
            '"2026-05-28 00:00:00","1","5649","USD","DEP - TRANSFER FUNDS FROM RBC"\n')
        self.assertEqual([t for t in txs if t['action'] == 'TRANSFER'], [])

    def test_out_transfer_signed_negative(self):
        txs = self._parse(
            '"2026-03-27 00:00:00","Transfers","QCOM","QUALCOMM INC","147","",'
            '"2026-03-27 00:00:00","1","0","USD","TFO - QUALCOMM INC DTC 8396"\n')
        tr = [t for t in txs if t['action'] == 'TRANSFER']
        self.assertEqual(tr[0]['quantity'], -147.0)

    def test_forward_stock_split_becomes_split_not_buy(self):
        # 14 shares + 70 received = 84 → a 6-for-1 forward split. Must be a
        # SPLIT (factor 6.0) that scales the pool, NOT a $0-cost BUYSELL of
        # 70 shares (which would strand them at zero basis).
        txs = self._parse(
            '"2026-04-20 00:00:00","Reorganization","VUG","VANGUARD GROWTH ETF",'
            '"70","","2026-04-22 00:00:00","1","0","USD","DIS - VANGUARD '
            'GROWTH ETF STK SPLIT ON      14 SHS REC 04/17/26 PAY 04/20/26"\n')
        splits = [t for t in txs if t['action'] == 'SPLIT']
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]['symbol'], 'VUG.US')
        self.assertEqual(splits[0].get('symbol_new', ''), '')
        self.assertAlmostEqual(splits[0]['quantity'], 6.0)
        # No $0-cost buy leaked through.
        self.assertEqual([t for t in txs if t['action'] == 'BUYSELL'], [])

    def test_reverse_stock_split_factor_below_one(self):
        # 100 shares, 80 removed = 20 → a 1-for-5 reverse split (factor 0.2).
        txs = self._parse(
            '"2026-04-20 00:00:00","Reorganization","ABC","SOME FUND","-80","",'
            '"2026-04-22 00:00:00","1","0","USD","REVERSE SPLIT ON 100 SHS"\n')
        splits = [t for t in txs if t['action'] == 'SPLIT']
        self.assertEqual(len(splits), 1)
        self.assertAlmostEqual(splits[0]['quantity'], 0.2)


if __name__ == '__main__':
    unittest.main()
