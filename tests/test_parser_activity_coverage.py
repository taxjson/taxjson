"""Regression coverage for brokerage parsers.

Each broker has a canonical set of activities the parser MUST handle
without dropping rows on the floor. This file pins one test per
(broker, activity) pair against representative CSV data drawn from
real exports.

Goal: if functionality regresses (e.g. dividend parsing silently
dropped, as happened with Questrade), one of these tests fails loudly
instead of the user noticing a discrepancy weeks later.

Activity matrix (where applicable per broker):
  - buy_stock              long open
  - sell_stock             long close
  - sell_call_open         covered call write (short, +cash)
  - buy_call_close         close short call (-cash)
  - buy_call_open          long call open
  - sell_call_close        close long call
  - get_assigned           option-leg ASSIGN notification
  - option_expires         worthless expiry
  - dividend               cash dividend
  - transfer               position move in/out (broker-specific)
"""
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from taxjson.lib.brokerages.webull import WebullBrokerage
from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.brokerages.kraken import KrakenBrokerage


def _parse_csv(parser_cls, content):
    """Run a parser against an in-memory CSV string."""
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    f.write(content)
    f.close()
    try:
        return parser_cls().parse_file(Path(f.name))
    finally:
        os.remove(f.name)


def _find(txs, **fields):
    """First transaction matching all given key/value pairs (or None)."""
    for t in txs:
        if all(t.get(k) == v for k, v in fields.items()):
            return t
    return None


# ============================================================================
# Questrade
# ============================================================================
QUESTRADE_HEADER = (
    'Transaction Date,Settlement Date,Action,Symbol,Description,'
    'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
    'Account #,Activity Type,Account Type\n'
)


class TestQuestradeActivities(unittest.TestCase):
    def test_buy_stock(self):
        csv = QUESTRADE_HEADER + (
            '2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,AAPL,APPLE INC,'
            '100,150.00,15000.00,9.95,-15009.95,USD,12345,Trades,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        t = _find(txs, symbol='AAPL.US', action='BUYSELL')
        self.assertIsNotNone(t)
        self.assertEqual(t['quantity'], 100)
        self.assertAlmostEqual(t['price'], 150.0)
        self.assertAlmostEqual(t['commission'], 9.95, places=2)
        # Trade rows carry the raw description so a description-keyed
        # --security-overrides rule can correct a mislabeled ticker (IB/RBC/
        # Webull already did; Questrade trades were the gap).
        self.assertEqual(t.get('description'), 'APPLE INC')

    def test_sell_stock(self):
        csv = QUESTRADE_HEADER + (
            '2025-03-15 09:30:00 AM,2025-03-16 12:00:00 AM,Sell,AAPL,APPLE INC,'
            '100,160.00,16000.00,9.95,15990.05,USD,12345,Trades,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        t = _find(txs, symbol='AAPL.US', action='BUYSELL')
        self.assertIsNotNone(t)
        self.assertEqual(t['quantity'], -100)

    def test_sell_call_open(self):
        """Sell-to-open a covered call: positive Sell quantity in Questrade,
        rebuilt into OCC option symbol from the description."""
        csv = QUESTRADE_HEADER + (
            '2025-04-10 09:30:00 AM,2025-04-11 12:00:00 AM,Sell,AAPL.OPT,'
            'CALL AAPL 06/20/25 150.00,1,5.00,500.00,1.25,498.75,USD,'
            '12345,Trades,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        t = _find(txs, action='BUYSELL')
        self.assertIsNotNone(t)
        self.assertEqual(t['symbol'], 'AAPL250620C00150000.US')
        self.assertEqual(t['quantity'], -1)  # Sell → negative

    def test_buy_call_close(self):
        csv = QUESTRADE_HEADER + (
            '2025-05-20 09:30:00 AM,2025-05-21 12:00:00 AM,Buy,AAPL.OPT,'
            'CALL AAPL 06/20/25 150.00,1,3.00,300.00,1.25,-301.25,USD,'
            '12345,Trades,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        t = _find(txs, action='BUYSELL')
        self.assertEqual(t['symbol'], 'AAPL250620C00150000.US')
        self.assertEqual(t['quantity'], 1)

    def test_get_assigned(self):
        """Option-leg ASN row: emits action='ASSIGN' instead of BUYSELL."""
        csv = QUESTRADE_HEADER + (
            '2025-06-20 09:30:00 AM,2025-06-20 12:00:00 AM,ASN,AAPL.OPT,'
            'CALL AAPL 06/20/25 150.00 ASSIGNMENT,1,0.00,0.00,0.00,0.00,USD,'
            '12345,Other,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        t = _find(txs, action='ASSIGN')
        self.assertIsNotNone(t)

    def test_option_expires(self):
        csv = QUESTRADE_HEADER + (
            '2025-06-20 09:30:00 AM,2025-06-20 12:00:00 AM,EXP,AAPL.OPT,'
            'CALL AAPL 06/20/25 150.00 - EXPIRED,1,0.00,0.00,0.00,0.00,USD,'
            '12345,Other,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        t = _find(txs, action='BUYSELL')  # EXP routes to BUYSELL with zero amounts
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t['net_amount'], 0.0)
        self.assertAlmostEqual(t['commission'], 0.0)

    def test_dividend(self):
        """The bug that triggered this whole regression suite."""
        csv = QUESTRADE_HEADER + (
            '2025-03-20 12:00:00 AM,2025-03-20 12:00:00 AM,DIV,.OTEX,'
            'OPEN TEXT CORP CASH DIV ON 150 SHS,0,0,0,0,55.92,CAD,'
            '12345,Dividends,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        t = _find(txs, action='DIVIDEND')
        self.assertIsNotNone(t)
        self.assertEqual(t['symbol'], 'OTEX.TO')
        self.assertAlmostEqual(t['net_amount'], 55.92, places=2)
        self.assertEqual(t['type'], 'dividend')
        # Quantity + price extracted from "ON 150 SHS" + amount/qty —
        # makes the record self-describing: 150 × $0.3728 ≈ $55.92.
        self.assertAlmostEqual(t['quantity'], 150.0)
        self.assertAlmostEqual(t['price'], 55.92 / 150, places=4)

    def test_transfer(self):
        """TF6 row emitted as TRANSFER. taxjson-brokerage --transfers
        controls whether it survives downstream."""
        csv = QUESTRADE_HEADER + (
            '2025-06-25 12:00:00 AM,2025-06-25 12:00:00 AM,TF6,.AEM,'
            'AGNICO EAGLE MINES,200.0,0,0,0,33060.00,CAD,'
            '12345,Transfers,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        t = _find(txs, action='TRANSFER')
        self.assertEqual(t['symbol'], 'AEM.TO')
        self.assertEqual(t['quantity'], 200)
        self.assertAlmostEqual(t['net_amount'], 33060.0, places=2)

    def test_internal_code_dividend_resolves_via_trade(self):
        """Real-world case where Questrade emits dividend with internal
        code symbol. Resolution via desc-match against trade rows."""
        csv = QUESTRADE_HEADER + (
            '2025-09-30 12:00:00 AM,2025-10-01 12:00:00 AM,Sell,SSL.TO,'
            'SANDSTORM GOLD LTD COM WE ACTED AS AGENT,-1,17.39,17.39,0,17.39,'
            'CAD,12345,Trades,Individual\n'
            '2025-10-07 12:00:00 AM,2025-10-07 12:00:00 AM,DIV,S032771,'
            'SANDSTORM GOLD LTD COM CASH DIV ON 1 SHS REC 09/26/25 PAY 10/07/25,'
            '0,0,0,0,0.02,CAD,12345,Dividends,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        d = _find(txs, action='DIVIDEND')
        self.assertEqual(d['symbol'], 'SSL.TO')

    def test_split_fills_disambiguated(self):
        """Two byte-identical CSV rows from one order split into two
        fills must produce two distinct transactions, not one (i.e.
        taxjson-sort --dedup should not collapse them)."""
        csv = QUESTRADE_HEADER + (
            '2025-11-12 12:00:00 AM,2025-11-13 12:00:00 AM,Sell,WCP.TO,'
            'WHITECAP RESOURCES INC WE ACTED AS AGENT,'
            '-100,10.86,1086.00,0,1086.00,CAD,12345,Trades,Individual\n'
            '2025-11-12 12:00:00 AM,2025-11-13 12:00:00 AM,Sell,WCP.TO,'
            'WHITECAP RESOURCES INC WE ACTED AS AGENT,'
            '-100,10.86,1086.00,0,1086.00,CAD,12345,Trades,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        wcps = [t for t in txs if t['symbol'] == 'WCP.TO']
        self.assertEqual(len(wcps), 2)
        # The second instance carries a "fill #2" marker so its hashed
        # id is different. The original (first) keeps its stable id.
        descs = [t.get('description') or '' for t in wcps]
        # The first keeps its plain (now real) description; the second gets a
        # "fill #2" marker so the two hash to distinct ids and survive --dedup.
        self.assertNotIn('fill #', descs[0])
        self.assertIn('fill #2', descs[1])
        self.assertNotEqual(descs[0], descs[1])

    def test_stock_split_dis_row_becomes_split_not_dropped_dividend(self):
        """A 'STK SPLIT' DIS row (tagged Dividends) must emit a SPLIT that
        scales the held pool — not get dropped as a $0 dividend. The temp
        symbol (K012006) resolves to the traded ticker (KLAC) via the
        same-security trade, and the ratio is (held + received)/held."""
        csv = QUESTRADE_HEADER + (
            '2026-06-04 09:30:00 AM,2026-06-05 12:00:00 AM,Buy,KLAC,'
            'KLA CORPORATION COMMON STOCK WITH DUE-BILL SPLIT WE ACTED AS AGENT,'
            '1,2075.00,2075.00,0,-2075.00,USD,12345,Trades,Individual\n'
            '2026-06-15 12:00:00 AM,2026-06-15 12:00:00 AM,DIS,K012006,'
            'KLA CORPORATION COMMON STOCK STK SPLIT ON 1 SHS REC 06/12/26 PAY '
            '06/15/26,9.0,0.0,0.0,0.0,0.0,USD,12345,Dividends,Individual\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        split = _find(txs, action='SPLIT')
        self.assertIsNotNone(split, f"expected a SPLIT row, got: {txs}")
        self.assertEqual(split['symbol'], 'KLAC.US')   # resolved from K012006
        self.assertAlmostEqual(split['quantity'], 10.0)  # (1 + 9) / 1
        # And it is NOT a dividend.
        self.assertIsNone(_find(txs, action='DIVIDEND'))


# ============================================================================
# RBC Direct
# ============================================================================
RBC_HEADER = (
    '"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
    '"Settlement Date","Account","Value","Currency","Description"\n'
)


class TestRbcActivities(unittest.TestCase):
    def test_buy_stock(self):
        csv = RBC_HEADER + (
            '"January 15, 2025","Buy","AAPL","APPLE INC","100","150.00",'
            '"January 16, 2025","12345","-15010.00","USD","Buy 100 shares"\n'
        )
        txs = _parse_csv(RbcBrokerage, csv)
        t = _find(txs, symbol='AAPL.US', action='BUYSELL')
        self.assertIsNotNone(t)
        self.assertEqual(t['quantity'], 100)
        # Fee back-compute: theoretical 15000, net 15010 → ~10 fee.
        self.assertAlmostEqual(t['fee'], 10.0, places=2)

    def test_sell_stock(self):
        csv = RBC_HEADER + (
            '"March 15, 2025","Sell","AAPL","APPLE INC","100","160.00",'
            '"March 16, 2025","12345","15990.00","USD","Sell 100 shares"\n'
        )
        txs = _parse_csv(RbcBrokerage, csv)
        t = _find(txs, symbol='AAPL.US', action='BUYSELL')
        self.assertEqual(t['quantity'], -100)

    def test_sell_call_open(self):
        csv = RBC_HEADER + (
            '"January 15, 2025","Sell","8ABCDE1","","-1","5.00",'
            '"January 16, 2025","12345","498.00","CAD",'
            '"CALL .QQZ   06/20/25    30 QQZ HOLDINGS INC OPEN CONTRACT"\n'
        )
        txs = _parse_csv(RbcBrokerage, csv)
        t = _find(txs, action='BUYSELL')
        self.assertEqual(t['symbol'], 'QQZ250620C00030000.TO')
        self.assertEqual(t['quantity'], -1)

    def test_get_assigned(self):
        """RBC ASN row → action=ASSIGN."""
        csv = RBC_HEADER + (
            '"May 16, 2025","Other","9BZYDS1","","1","",'
            '"May 20, 2025","12345","0","USD",'
            '"ASN - CALL COIN   05/16/25   197.50 COINBASE GLOBAL INC ASSIGNMENT OF OPTION"\n'
        )
        txs = _parse_csv(RbcBrokerage, csv)
        t = _find(txs, action='ASSIGN')
        self.assertIsNotNone(t)

    def test_option_expires(self):
        """EXP row stays BUYSELL with cleared amounts so the engine
        realizes premium gain/loss in place."""
        csv = RBC_HEADER + (
            '"February 24, 2025","Reorganization","8DZNZG9","","-30","",'
            '"February 24, 2025","12345","0","CAD",'
            '"EXP - CALL .BNS   02/21/25    82 BANK OF NOVA SCOTIA OPTION EXPIRATION - EXPIRED"\n'
        )
        txs = _parse_csv(RbcBrokerage, csv)
        t = _find(txs, action='BUYSELL')
        self.assertIsNotNone(t)
        self.assertEqual(t['symbol'], 'BNS250221C00082000.TO')

    def test_dividend(self):
        csv = RBC_HEADER + (
            '"March 15, 2025","Dividends","AAPL","APPLE INC","0","",'
            '"March 15, 2025","12345","100.00","USD","DIV ON 100 SHS"\n'
        )
        txs = _parse_csv(RbcBrokerage, csv)
        t = _find(txs, action='DIVIDEND')
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t['net_amount'], 100.0, places=2)
        # qty extracted from "ON 100 SHS", price back-computed from
        # net/qty → $1.00/share.
        self.assertAlmostEqual(t['quantity'], 100.0)
        self.assertAlmostEqual(t['price'], 1.0, places=4)

    def test_interest(self):
        csv = RBC_HEADER + (
            '"April 01, 2025","Interest","CASH","","0","",'
            '"April 01, 2025","12345","12.34","CAD","INTEREST PAID"\n'
        )
        txs = _parse_csv(RbcBrokerage, csv)
        t = _find(txs, action='INTEREST')
        self.assertIsNotNone(t)
        self.assertEqual(t['symbol'], 'CASH')

    def test_cross_listed_dividend_uses_trade_market(self):
        """A Canadian-listed stock paying a USD dividend: trades arrive
        in CAD, dividend arrives in USD. Without resolution the parser
        would suffix the dividend with .US (since currency=USD),
        splitting the ticker identity from the trades' .TO. Same
        regression class as the Questrade BTO/B2GOLD case — pin the
        right behavior so a future refactor can't silently re-break it.
        """
        csv = RBC_HEADER + (
            # Trade: BNS in CAD → BNS.TO.
            '"January 15, 2025","Buy","BNS","BANK OF NOVA SCOTIA","100","75.00",'
            '"January 16, 2025","12345","-7510.00","CAD","Buy 100 shares"\n'
            # Dividend: BNS again, but in USD.
            '"March 15, 2025","Dividends","BNS","BANK OF NOVA SCOTIA","0","",'
            '"March 15, 2025","12345","100.00","USD","BNS DIV ON 100 SHS"\n'
        )
        txs = _parse_csv(RbcBrokerage, csv)
        divs = [t for t in txs if t.get('action') == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        # Dividend symbol follows the trade's market (BNS.TO), not the
        # dividend row's USD currency (which would yield BNS.US).
        self.assertEqual(divs[0]['symbol'], 'BNS.TO')
        # The currency field still records the actual payment currency.
        self.assertEqual(divs[0]['currency'], 'USD')


# ============================================================================
# Webull
# ============================================================================
class TestWebullActivities(unittest.TestCase):
    def _csv(self, data_rows):
        header = (
            '"Currency","Date","Action Code","Symbol","Security Description",'
            '"Type Code","Quantity","Price","Proceeds"\n'
        )
        return header + ''.join(data_rows)

    def test_buy_stock(self):
        csv = self._csv([
            'USD,15-01-2025,BUY,AAPL,APPLE INC,STK,100,150.00,"(15010.00)"\n'
        ])
        txs = _parse_csv(WebullBrokerage, csv)
        t = _find(txs, symbol='AAPL.US', action='BUYSELL')
        self.assertEqual(t['quantity'], 100)
        self.assertAlmostEqual(t['fee'], 10.0, places=2)

    def test_sell_stock(self):
        csv = self._csv([
            'USD,15-03-2025,SELL,AAPL,APPLE INC,STK,100,160.00,"15990.00"\n'
        ])
        txs = _parse_csv(WebullBrokerage, csv)
        t = _find(txs, symbol='AAPL.US', action='BUYSELL')
        self.assertEqual(t['quantity'], -100)

    def test_sell_call_open(self):
        csv = self._csv([
            'USD,25-11-2024,SELL,@ABBV,CALL ABBV01/17/25 190,OPC,10,1.60,"1590.08"\n'
        ])
        txs = _parse_csv(WebullBrokerage, csv)
        t = _find(txs, action='BUYSELL')
        self.assertEqual(t['symbol'], 'ABBV250117C00190000.US')
        self.assertEqual(t['quantity'], -10)

    def test_buy_call(self):
        csv = self._csv([
            'USD,15-12-2024,BUY,@ABBV,CALL ABBV01/17/25 190,OPC,10,2.50,"(2510.00)"\n'
        ])
        txs = _parse_csv(WebullBrokerage, csv)
        t = _find(txs, action='BUYSELL')
        self.assertEqual(t['symbol'], 'ABBV250117C00190000.US')
        self.assertEqual(t['quantity'], 10)

    def test_currency_suffix_per_row(self):
        csv = self._csv([
            'CAD,15-01-2025,BUY,SHOP,SHOPIFY INC,STK,10,100.00,"(1000.00)"\n'
        ])
        txs = _parse_csv(WebullBrokerage, csv)
        t = _find(txs, action='BUYSELL')
        self.assertEqual(t['symbol'], 'SHOP.TO')

    def test_no_dividend_handling_currently(self):
        """Documents that Webull's parser doesn't emit DIVIDEND
        transactions today. Webull users in the wild typically don't
        rely on this brokerage for dividend tracking, but if the CSV
        format starts including dividend rows this test will flag the
        gap (and the cross-listing regression should be added when
        support is built)."""
        csv = self._csv([
            'USD,15-03-2025,DIV,AAPL,APPLE INC,DIV,0,0.00,"100.00"\n'
        ])
        txs = _parse_csv(WebullBrokerage, csv)
        divs = [t for t in txs if t.get('action') == 'DIVIDEND']
        # No dividend support yet — file an issue if you need it.
        self.assertEqual(divs, [])


# ============================================================================
# Interactive Brokers — dividends use ISIN prefix for the suffix
# ============================================================================
IB_HEADER_LINES = (
    'Statement,Header,Field Name,Field Value\n'
    'Statement,Data,BrokerName,Interactive Brokers\n'
)


class TestIbActivities(unittest.TestCase):
    def test_dividend_us_listed(self):
        """A US-listed stock paying USD dividend. ISIN starts with US
        → suffix .US. Sanity baseline."""
        csv = IB_HEADER_LINES + (
            'Dividends,Header,Currency,Date,Description,Amount\n'
            'Dividends,Data,USD,2025-03-15,"AAPL (US0378331005) Cash Dividend USD 0.24 per Share (Ordinary Dividend)",24.00\n'
        )
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        import os, tempfile
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        f.write(csv); f.close()
        try:
            txs = IbBrokerage().parse_file(Path(f.name))
        finally:
            os.remove(f.name)
        divs = [t for t in txs if t.get('action') == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        self.assertEqual(divs[0]['symbol'], 'AAPL.US')
        # IB descriptions carry "USD 0.24 per Share" — extract the rate
        # and back-compute qty (24.00 / 0.24 = 100 shares).
        self.assertAlmostEqual(divs[0]['price'], 0.24, places=4)
        self.assertAlmostEqual(divs[0]['quantity'], 100.0, places=4)

    def test_cross_listed_dividend_uses_isin_market(self):
        """The Canadian-ticker-USD-dividend case for IB. IB uses the
        ISIN's country prefix to derive the market suffix, NOT the
        currency. So a CA-prefixed ISIN paying USD still produces .TO.
        Regression-guard so a future refactor that switches to
        currency-based suffix doesn't reintroduce the BTO.US bug.
        """
        csv = IB_HEADER_LINES + (
            'Dividends,Header,Currency,Date,Description,Amount\n'
            # Bank of Nova Scotia, Canadian ISIN (CA0641491075), USD payment.
            'Dividends,Data,USD,2025-03-15,"BNS (CA0641491075) Cash Dividend USD 1.00 per Share (Ordinary Dividend)",100.00\n'
        )
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        import os, tempfile
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        f.write(csv); f.close()
        try:
            txs = IbBrokerage().parse_file(Path(f.name))
        finally:
            os.remove(f.name)
        divs = [t for t in txs if t.get('action') == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        # CA-ISIN → .TO suffix, regardless of USD payment.
        self.assertEqual(divs[0]['symbol'], 'BNS.TO')
        # Currency on the record reflects the actual payment.
        self.assertEqual(divs[0]['currency'], 'USD')

    def test_withholding_tax_uses_isin_market(self):
        """Regression: the IB Withholding Tax row used to hardcode `.US`
        on the emitted TAX symbol, fragmenting it from the matching
        dividend's market suffix for non-US holdings. The fix mirrors
        the dividend handler — pull the ISIN from the description and
        use the same country→market map. A CA-domiciled ISIN paying
        non-resident withholding must produce TAX symbol `BNS.TO`,
        matching the DIVIDEND row's pool key downstream."""
        csv = IB_HEADER_LINES + (
            'Withholding Tax,Header,Currency,Date,Description,Amount\n'
            'Withholding Tax,Data,USD,2025-03-15,"BNS (CA0641491075) Cash Dividend USD 1.00 per Share - US Tax",-15.00\n'
        )
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        import os, tempfile
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        f.write(csv); f.close()
        try:
            txs = IbBrokerage().parse_file(Path(f.name))
        finally:
            os.remove(f.name)
        taxes = [t for t in txs if t.get('action') == 'TAX']
        self.assertEqual(len(taxes), 1)
        # CA-ISIN → .TO, NOT the old hardcoded .US.
        self.assertEqual(taxes[0]['symbol'], 'BNS.TO')

    def test_assign_code_requires_exact_A_token_not_substring(self):
        """IB packs space- / semicolon-separated codes into one cell.
        The ASSIGN classifier must match the bare `A` token, not any
        code containing the letter A — so `Au` (auction adjustment),
        `AEx` (auto-exercise), `ADR` (ADR fee), etc. don't get
        silently reclassified as option assignments. The earlier
        `'A' in code` substring check fired on every one of these."""
        csv = IB_HEADER_LINES + (
            'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
            'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
            'Realized P/L,MTM P/L,Code\n'
            # Code `Au` with zero price — substring match would
            # mis-classify this as ASSIGN.
            'Trades,Data,Order,Stocks,USD,AAPL,"2025-05-01, 09:30:00",'
            '100,0,0,0,0,0,0,0,Au\n'
            # Bare `A` IS the real assignment marker.
            'Trades,Data,Order,Stocks,USD,MSFT,"2025-05-02, 09:30:00",'
            '50,0,0,0,0,0,0,0,A\n'
        )
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        import os, tempfile
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        f.write(csv); f.close()
        try:
            txs = IbBrokerage().parse_file(Path(f.name))
        finally:
            os.remove(f.name)
        by_sym = {t['symbol']: t for t in txs}
        self.assertEqual(by_sym['AAPL.US']['action'], 'BUYSELL',
                         "Code='Au' must not match the 'A' ASSIGN token")
        self.assertEqual(by_sym['MSFT.US']['action'], 'ASSIGN',
                         "Code='A' (bare) must classify as ASSIGN")

    def test_malformed_trade_cell_skips_row_not_whole_parse(self):
        """A malformed numeric cell in one Trades row (e.g. 'N/A' where
        Quantity should be) must skip just that row with a stderr
        warning; the rest of the file must still parse. Was a hard
        parse-abort that lost every trade in the file."""
        csv_content = IB_HEADER_LINES + (
            'Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
            'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
            'Realized P/L,MTM P/L,Code\n'
            # Malformed Quantity — old behaviour killed the whole parse here.
            'Trades,Data,Order,Stocks,USD,AAPL,"2025-05-01, 09:30:00",'
            'N/A,150.0,0,15000,1,0,0,0,O\n'
            # Valid row that must still come through.
            'Trades,Data,Order,Stocks,USD,MSFT,"2025-05-02, 09:30:00",'
            '50,400.0,0,20000,1,0,0,0,O\n'
        )
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        import os, tempfile, io, sys as _sys
        from unittest.mock import patch
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        f.write(csv_content); f.close()
        buf = io.StringIO()
        try:
            with patch.object(_sys, 'stderr', buf):
                txs = IbBrokerage().parse_file(Path(f.name))
        finally:
            os.remove(f.name)
        syms = [t['symbol'] for t in txs]
        self.assertIn('MSFT.US', syms,
                      "Valid row after a malformed one must still parse")
        self.assertNotIn('AAPL.US', syms,
                         "Malformed row must be skipped, not silently kept")
        self.assertIn('warning', buf.getvalue().lower())
        self.assertIn('Trades', buf.getvalue())


# ============================================================================
# Coinbase (crypto — narrower activity surface)
# ============================================================================
COINBASE_HEADER = (
    'Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,'
    'Price at Transaction,Fees and/or Spread,Total (inclusive of fees and/or spread)\n'
)


class TestCoinbaseActivities(unittest.TestCase):
    def test_buy_crypto(self):
        csv = COINBASE_HEADER + (
            '2025-01-15 10:00:00 UTC,Buy,BTC,0.1,USD,60000,5,6005\n'
        )
        txs = _parse_csv(CoinbaseBrokerage, csv)
        t = _find(txs, symbol='BTC', action='BUYSELL')
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t['quantity'], 0.1)
        self.assertAlmostEqual(t['fee'], 5.0)

    def test_sell_crypto(self):
        csv = COINBASE_HEADER + (
            '2025-02-15 10:00:00 UTC,Sell,BTC,0.05,USD,65000,3,3247\n'
        )
        txs = _parse_csv(CoinbaseBrokerage, csv)
        t = _find(txs, symbol='BTC', action='BUYSELL')
        # Negative-on-sell convention.
        self.assertAlmostEqual(t['quantity'], -0.05)

    def test_advanced_trade_buy(self):
        """Coinbase Advanced uses the prefixed type 'Advanced Trade Buy'.
        The legacy cb_trades.pl matched /(Buy|Sell)/i on substring so it
        caught these too. We must do the same — otherwise Advanced trades
        silently disappear and per-ticker cost basis becomes wrong."""
        csv = COINBASE_HEADER + (
            '2025-03-15 10:00:00 UTC,Advanced Trade Buy,ETH,0.5,USD,3000,1.5,1501.5\n'
        )
        txs = _parse_csv(CoinbaseBrokerage, csv)
        t = _find(txs, symbol='ETH', action='BUYSELL')
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t['quantity'], 0.5)

    def test_advanced_trade_sell(self):
        csv = COINBASE_HEADER + (
            '2025-04-15 10:00:00 UTC,Advanced Trade Sell,ETH,0.25,USD,3500,1.0,873.0\n'
        )
        txs = _parse_csv(CoinbaseBrokerage, csv)
        t = _find(txs, symbol='ETH', action='BUYSELL')
        self.assertAlmostEqual(t['quantity'], -0.25)

    def test_staking_income(self):
        """Coinbase 'Staking Income' rows are the dominant source of
        crypto-asset dividend income for ADA/AVAX/DOT/ETH/SOL holders
        (600+ rows in real data). Old cb_dividends.pl emitted a paired
        DIVIDEND + zero-cost BUYSELL; the new parser must too, so the
        rewards land in inventory at FMV cost basis AND surface as
        per-ticker dividend income."""
        csv = COINBASE_HEADER + (
            '2025-05-15 10:00:00 UTC,Staking Income,DOT,1.5,USD,8.00,0,12.00\n'
        )
        txs = _parse_csv(CoinbaseBrokerage, csv)
        div = _find(txs, symbol='DOT', action='DIVIDEND')
        buy = _find(txs, symbol='DOT', action='BUYSELL')
        self.assertIsNotNone(div, "Staking Income must emit DIVIDEND")
        self.assertIsNotNone(buy, "Staking Income must emit BUYSELL for inventory")
        # DIVIDEND carries the reward qty so fill_crypto_prices computes
        # income = qty*FMV correctly (same convention as Kraken).
        self.assertAlmostEqual(div['quantity'], 1.5)
        self.assertAlmostEqual(div['net_amount'], 12.0)
        # BUYSELL adds the tokens to inventory at FMV (matches the income
        # event, so when sold the cap gain reflects only post-receipt price
        # change — not the entire FMV being taxed twice).
        self.assertAlmostEqual(buy['quantity'], 1.5)
        self.assertAlmostEqual(buy['net_amount'], 12.0)

    def test_staking_income_label_variants(self):
        """Coinbase uses several labels for what's economically the same
        event: `Reward Income` (singular), `Inflation Reward`, etc. The
        regex must accept all of them so the parser doesn't silently
        drop legitimate income rows that happen to use a non-canonical
        label."""
        # 'Incentives Rewards Payout' is the referral/usage-incentive
        # variant — it was falling to the counted-skip bucket, leaving
        # the rewarded coins off the books (surfaced as a real-data BTC
        # phantom-short of 0.00001365 when the account was emptied).
        for label in ('Reward Income', 'Rewards Income', 'Inflation Reward',
                      'Coinbase Earn', 'Learning Reward',
                      'Incentives Rewards Payout', 'Rewards Payout'):
            csv = COINBASE_HEADER + (
                f'2025-05-15 10:00:00 UTC,{label},DOT,1.5,USD,8.00,0,12.00\n'
            )
            txs = _parse_csv(CoinbaseBrokerage, csv)
            self.assertTrue(
                any(t.get('action') == 'DIVIDEND' for t in txs),
                f"label {label!r} should produce a DIVIDEND row; got {[t.get('action') for t in txs]}",
            )

    def test_unknown_types_ignored(self):
        """Subscription/staking-transfer/etc. shouldn't produce tax
        records. Since 2026-09, Send/Receive produce TRANSFER
        EVIDENCE rows (custody sidecar — an off-platform gift is a
        taxable disposition at FMV that deserves a trace); Deposit is
        not a Coinbase Receive and stays ignored."""
        csv = COINBASE_HEADER + (
            '2025-06-01 10:00:00 UTC,Send,BTC,0.01,USD,60000,0,600\n'
            '2025-06-02 10:00:00 UTC,Deposit,USDC,100,USD,1,0,100\n'
            '2025-06-03 10:00:00 UTC,Subscription,USD,5,USD,1,0,5\n'
            '2025-06-04 10:00:00 UTC,Retail Staking Transfer,ETH,1,USD,3000,0,3000\n'
        )
        txs = _parse_csv(CoinbaseBrokerage, csv)
        self.assertEqual([t.get('action') for t in txs], ['TRANSFER'])
        self.assertEqual(txs[0]['symbol'], 'BTC')
        self.assertEqual(txs[0]['quantity'], -0.01)

    def test_convert_row_raises_rather_than_silent_drop(self):
        """A Coinbase 'Convert from BTC to ETH' row is a taxable
        disposition; silently skipping it (the prior behavior) would
        drop a tax event. The parser must raise rather than
        masquerade-pass — the user can hand-edit the row to a paired
        Buy+Sell as a workaround, but the failure has to be visible."""
        csv = COINBASE_HEADER + (
            '2025-04-10 10:00:00 UTC,Convert,BTC,0.05,USD,65000,0,3250\n'
        )
        with self.assertRaises(ValueError) as cm:
            _parse_csv(CoinbaseBrokerage, csv)
        msg = str(cm.exception)
        self.assertIn('Convert', msg)


# ============================================================================
# Kraken (crypto — two distinct CSV formats: trades and ledgers)
# ============================================================================
class TestKrakenActivities(unittest.TestCase):
    def test_trades_buy(self):
        csv = (
            "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
            "T1,O1,BTC/USD,2025-01-15 10:00:00.1234,buy,limit,60000,6000,1,0.1,,,\n"
        )
        txs = _parse_csv(KrakenBrokerage, csv)
        self.assertEqual(len(txs), 1)
        t = txs[0]
        self.assertEqual(t['symbol'], 'BTC')
        self.assertEqual(t['currency'], 'USD')
        self.assertAlmostEqual(t['quantity'], 0.1)

    def test_trades_sell(self):
        csv = (
            "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
            "T1,O1,BTC/USD,2025-02-15 10:00:00.5678,sell,limit,65000,3250,2,0.05,,,\n"
        )
        txs = _parse_csv(KrakenBrokerage, csv)
        t = txs[0]
        self.assertAlmostEqual(t['quantity'], -0.05)

    def test_ledgers_staking_reward(self):
        """Kraken ledger format: earn/reward rows produce a DIVIDEND
        plus a zero-cost BUYSELL pair (the rewarded shares become
        new acquisitions with no basis)."""
        csv = (
            "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
            "L1,,2025-01-15 10:00:00,earn,reward,currency,ADA,,100,0,1000\n"
        )
        txs = _parse_csv(KrakenBrokerage, csv)
        actions = {t['action'] for t in txs}
        self.assertEqual(actions, {'DIVIDEND', 'BUYSELL'})

    def test_ledgers_staking_reward_dividend_carries_qty(self):
        """Regression: the DIVIDEND row must carry the reward qty so
        fill_crypto_prices computes income as qty*FMV. Without it the
        prices-filler defaults qty to 1.0, inflating e.g. a 0.001 ETH
        reward into a full ETH's worth of bogus dividend income."""
        csv = (
            "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
            "L1,,2025-01-15 10:00:00,earn,reward,currency,ETH,,0.001,0,1\n"
        )
        txs = _parse_csv(KrakenBrokerage, csv)
        div = next(t for t in txs if t['action'] == 'DIVIDEND')
        self.assertAlmostEqual(div['quantity'], 0.001)
        buy = next(t for t in txs if t['action'] == 'BUYSELL')
        self.assertAlmostEqual(buy['quantity'], 0.001)

    def test_ledgers_instant_trade(self):
        """spend + receive pair with same refid = one logical trade."""
        csv = (
            "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
            "L2,REF1,2025-01-15 10:05:00,spend,,currency,ZUSD,,-100,1,0\n"
            "L3,REF1,2025-01-15 10:05:00,receive,,currency,XXBT,,0.001,0,0\n"
        )
        txs = _parse_csv(KrakenBrokerage, csv)
        instant = [t for t in txs if t.get('description') == 'Instant Trade']
        self.assertEqual(len(instant), 1)
        self.assertEqual(instant[0]['symbol'], 'BTC')
        self.assertEqual(instant[0]['currency'], 'USD')

    def test_dai_is_normalized_to_usd(self):
        """DAI is a USD-pegged stablecoin; `_normalize_asset` folds it
        the same way as USDC/USDT. Without this, DAI is in
        `_FIAT_ASSETS` (so a `BTC/DAI` row bypasses the crypto/crypto
        raise) but stays as `DAI` in the emitted tx — downstream FX
        conversion can't anchor it and falls back to --default-rate."""
        from taxjson.lib.brokerages.kraken import _normalize_asset
        self.assertEqual(_normalize_asset('DAI'), 'USD')
        self.assertEqual(_normalize_asset('USDC'), 'USD')
        self.assertEqual(_normalize_asset('USDT'), 'USD')

    def test_btc_dai_pair_treated_as_fiat_quote(self):
        """With DAI folded to USD, a BTC/DAI trades.csv row emits a
        normal single-leg BUYSELL priced in USD (not a die-hard raise
        and not a crypto-denominated currency)."""
        csv = (
            "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
            "T1,O1,XXBT/DAI,2025-02-10 10:00:00.0,buy,limit,60000,6000,1,0.1,,,\n"
        )
        txs = _parse_csv(KrakenBrokerage, csv)
        self.assertEqual(len(txs), 1)
        t = txs[0]
        self.assertEqual(t['symbol'], 'BTC')
        self.assertEqual(t['currency'], 'USD')  # DAI normalized to USD

    def test_ledgers_crypto_to_crypto_emits_two_legs(self):
        """A crypto-to-crypto swap is a taxable disposition of the spent
        crypto at FMV (CRA s. 40(1) / IRS Notice 2014-21). The parser
        must emit BOTH a SELL of the spent asset and a BUY of the
        received asset, each priced in USD with price=0 / net=0 so
        taxjson-fill-crypto backfills FMV. Pre-fix the swap squashed
        into a single BUYSELL of the received asset denominated in
        the spent crypto, silently dropping the disposition gain."""
        csv = (
            "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
            # Spend 1 ETH (with $0.50 in ETH fee), receive 0.05 BTC.
            "L4,REF2,2025-02-10 14:30:00,spend,,currency,XETH,,-1.0,0.0005,0\n"
            "L5,REF2,2025-02-10 14:30:00,receive,,currency,XXBT,,0.05,0,0\n"
        )
        txs = _parse_csv(KrakenBrokerage, csv)
        swap_legs = [t for t in txs if 'crypto-to-crypto' in t.get('description', '')]
        self.assertEqual(len(swap_legs), 2,
                         "Crypto-to-crypto swap must emit a SELL + BUY pair")
        sell = next(t for t in swap_legs if t['quantity'] < 0)
        buy = next(t for t in swap_legs if t['quantity'] > 0)
        # Sell leg: spent 1 ETH → quantity = -1.0, symbol=ETH, USD-priced.
        self.assertEqual(sell['symbol'], 'ETH')
        self.assertAlmostEqual(sell['quantity'], -1.0)
        self.assertEqual(sell['currency'], 'USD')
        self.assertAlmostEqual(sell['price'], 0.0)
        self.assertAlmostEqual(sell['net_amount'], 0.0)
        # Buy leg: received 0.05 BTC → quantity = +0.05, symbol=BTC, USD-priced.
        self.assertEqual(buy['symbol'], 'BTC')
        self.assertAlmostEqual(buy['quantity'], 0.05)
        self.assertEqual(buy['currency'], 'USD')
        self.assertAlmostEqual(buy['price'], 0.0)
        self.assertAlmostEqual(buy['net_amount'], 0.0)
        # IDs distinguish the two legs so the sort-stage dedup keeps both.
        self.assertEqual(sell['id'], 'REF2-sell')
        self.assertEqual(buy['id'], 'REF2-buy')
        # Fees zero-out on both legs: Kraken stores the swap fee in the
        # row's asset units (e.g. fractional ETH), but the legs are
        # USD-denominated and the parser can't convert without FMV.
        # Stamping the crypto-denominated fee onto a USD leg was the
        # original bug; zero is the safe placeholder.
        self.assertAlmostEqual(sell['fee'], 0.0)
        self.assertAlmostEqual(buy['fee'], 0.0)

    def test_trades_csv_crypto_to_crypto_two_legs(self):
        """A crypto-to-crypto trades-CSV fill emits the same two-leg
        SELL+BUY as the ledgers path (both USD-denominated, price=0
        for the fill-crypto FMV backfill) — graduated 2026-08 from the
        KNOWN_ISSUES fail-hard placeholder, per its fix template."""
        csv = (
            "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
            # ETH/BTC buy: received 12.5 ETH (vol), spent 0.5 BTC (cost).
            "T1,O1,ETH/BTC,2025-02-10 10:00:00.0,buy,limit,0.04,0.5,0.0001,12.5,,,\n"
        )
        txs = _parse_csv(KrakenBrokerage, csv)
        self.assertEqual(len(txs), 2)
        by_sym = {t['symbol']: t for t in txs}
        self.assertAlmostEqual(by_sym['ETH']['quantity'], 12.5)
        self.assertAlmostEqual(by_sym['BTC']['quantity'], -0.5)
        for t in txs:
            self.assertEqual(t['currency'], 'USD')
            self.assertEqual(t['price'], 0.0)
        self.assertEqual(by_sym['ETH']['id'], 'T1-base')




class TestQuestradeStockDividend(unittest.TestCase):
    """A DIS row with 'STK DIV' delivers NEW shares in kind (split-share
    corps like TDb). The dividend branch's zero-cash guard silently
    discarded it — the position then went phantom-short by the
    delivered count at the next full sale (real XTD.TO case: 208
    shares on 1,390, sale of 2,098 vs book 1,890 → -208)."""

    def test_stk_div_row_delivers_shares(self):
        csv = QUESTRADE_HEADER + (
            '2026-01-19 09:30:00 AM,2026-01-20 12:00:00 AM,Buy,XTD.TO,'
            'TDB SPLIT CORP SHS CL A NEW WE ACTED AS AGENT,1390,6.85,'
            '9521.50,0.00,-9521.50,CAD,1,Trades,Individual\n'
            '2026-07-29 12:00:00 AM,2026-07-29 12:00:00 AM,DIS,XTD,'
            'TDB SPLIT CORP SHS CL A NEW STK DIV ON 1390 SHS REC '
            '07/24/26 PAY 07/29/26,208.0,0.0,0.0,0.0,0.0,CAD,1,'
            'Dividends,Individual\n')
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = _parse_csv(QuestradeBrokerage, csv)
        stk = [t for t in txs if t['action'] == 'BUYSELL'
               and t['quantity'] == 208.0]
        self.assertEqual(len(stk), 1,
                         "the stock-dividend shares were dropped")
        self.assertEqual(stk[0]['symbol'], 'XTD.TO')
        self.assertEqual(stk[0]['price'], 0.0)
        self.assertIn("stock dividend", buf.getvalue())
        # Total position: 1390 + 208.
        total = sum(t['quantity'] for t in txs
                    if t['action'] == 'BUYSELL')
        self.assertAlmostEqual(total, 1598.0)

    def test_cash_div_rows_still_informational(self):
        # Zero-cash DIST rows without share delivery keep the old
        # behavior (no transaction).
        csv = QUESTRADE_HEADER + (
            '2026-02-10 12:00:00 AM,2026-02-10 12:00:00 AM,,XTD,'
            'TDB SPLIT CORP SHS CL A NEW DIST ON 1000 SHS REC 01/30/26 '
            'PAY 02/10/26,0.0,0.05,0.0,0.0,0.0,CAD,1,Dividends,'
            'Individual\n')
        txs = _parse_csv(QuestradeBrokerage, csv)
        self.assertEqual(txs, [])


if __name__ == "__main__":
    unittest.main()


class TestQuestradeReinvestmentAndCashInLieu(unittest.TestCase):
    """Two Questrade rows that were counted skips (2026-09, seen on a
    real RESP export): REI (DRIP purchase) and CIL (cash in lieu of a fractional
    stock-dividend share)."""

    def test_rei_row_is_a_purchase_at_the_reinvest_price(self):
        csv = QUESTRADE_HEADER + (
            '2026-09-10 12:00:00 AM,2026-09-10 12:00:00 AM,   ,ZZQ.TO,'
            'ZZQ SPLIT CORP CL-A SHS DIST ON 1000 SHS REC 08/31/26 '
            'PAY 09/10/26,0,0.1,0,0,100,CAD,99900001,Dividends,API\n'
            '2026-09-10 12:00:00 AM,2026-09-10 12:00:00 AM,REI,ZZQ.TO,'
            'ZZQ SPLIT CORP CL-A SHS REINV@C$8.33966 REC 08/31/26 '
            'PAY 09/10/26,11,0,0,0,-91.74,CAD,99900001,'
            'Dividend reinvestment,API\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        div = _find(txs, symbol='ZZQ.TO', action='DIVIDEND')
        self.assertIsNotNone(div, "the cash dividend is still booked")
        buy = _find(txs, symbol='ZZQ.TO', action='BUYSELL')
        self.assertIsNotNone(buy, "the DRIP shares enter inventory")
        self.assertEqual(buy['quantity'], 11)
        self.assertAlmostEqual(buy['price'], 8.33966)
        self.assertAlmostEqual(buy['net_amount'], 91.74)
        self.assertEqual(buy['date_settle'], '2026-09-10')

    def test_cil_row_disposes_the_fraction_for_the_cash(self):
        csv = QUESTRADE_HEADER + (
            '2026-07-31 12:00:00 AM,2026-07-31 12:00:00 AM,CIL,XTD.TO,'
            'TDB SPLIT CORP SHS CL A NEW CASH IN LIEU OF .50000 REC '
            '07/24/26 PAY 07/29/26 87234Y308000,0,0,0,0,4.56,CAD,'
            '99900001,Corporate actions,API\n'
        )
        txs = _parse_csv(QuestradeBrokerage, csv)
        legs = [t for t in txs if t.get('symbol') == 'XTD.TO']
        self.assertEqual([t['quantity'] for t in legs], [0.5, -0.5])
        self.assertAlmostEqual(legs[0]['net_amount'], 0.0)
        self.assertAlmostEqual(legs[1]['net_amount'], 4.56)
        self.assertAlmostEqual(legs[1]['price'], 9.12)
        # Same-day, ordered: the acquisition precedes the sale.
        self.assertLess(legs[0]['time'], legs[1]['time'])

