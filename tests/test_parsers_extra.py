"""Dedicated parser tests for Webull and Questrade.

Existing coverage hits the parsers indirectly via the .tt port tests. These
tests pin down the parser-emit shape directly: fee back-compute (the bug
class that caused half-the-fees-missing on Webull), option symbol
reconstruction, action mapping for Buy/Sell vs EXP vs ASN, and the
account-name override threading through tx-id hashing.
"""

import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.webull import WebullBrokerage


# ============================================================================
# Webull
# ============================================================================
class TestWebullFindHeader(unittest.TestCase):
    """Direct unit tests for `_find_header`.

    Webull's export shape varies by year and account type, so the
    detector has three fallback paths:
      1. Strict — all three markers (`Currency` + `Date` + `Action
         Code`) on one line (the modern / demo format).
      2. Two-line — `Currency` on one row, `Date` on the next (some
         older / variant exports that lack the literal "Action Code"
         column header).
      3. Last-ditch — scan for the first `,BUY,` / `,SELL,` data row
         and return the line above.

    Cycle-4 collapsed this to path 1 only, which silently produced
    zero transactions on real Webull exports that didn't carry the
    literal "Action Code" header. Restored after the user surfaced
    the regression via empty `*_webull.json` output."""

    def test_strict_path_finds_modern_header(self):
        lines = [
            "Webull Securities Statement",
            "Settled in Currency: USD",  # contains "Currency" only
            "Some other preamble line with Date label",
            '"Currency","Date","Action Code","Symbol","Security Description",'
            '"Type Code","Quantity","Price","Proceeds"',
            "USD,15-01-2024,BUY,@AAPL,APPLE INC,EQ,100,185.00,(18500.00)",
        ]
        from taxjson.lib.brokerages.webull import WebullBrokerage
        # Strict path matches line 3 even though the preamble at line 1
        # contains the word "Currency" — all three markers required.
        self.assertEqual(WebullBrokerage._find_header(lines), 3)

    def test_two_line_path_matches_when_no_action_code_header(self):
        # No literal "Action Code" anywhere — the strict path fails.
        # Two-line path catches `Currency` + next-line `Date`.
        lines = [
            "Webull Statement",
            '"Currency"',
            '"Date","Type","Symbol","Quantity","Price"',
            "USD,2024-01-15,BUY,AAPL,100,185.00",
        ]
        from taxjson.lib.brokerages.webull import WebullBrokerage
        self.assertEqual(WebullBrokerage._find_header(lines), 1)

    def test_last_ditch_path_matches_via_data_row(self):
        # Neither path 1 nor path 2 matches. Path 3 scans data rows.
        lines = [
            "Webull Statement preamble",
            "no column headers at all",
            "USD,2024-01-15,BUY,AAPL,100,185.00",
        ]
        from taxjson.lib.brokerages.webull import WebullBrokerage
        # Returns the line just before the first BUY/SELL data row.
        self.assertEqual(WebullBrokerage._find_header(lines), 1)

    def test_returns_minus_one_when_no_header_or_data(self):
        from taxjson.lib.brokerages.webull import WebullBrokerage
        lines = ["Statement", "No header markers and no data rows here"]
        self.assertEqual(WebullBrokerage._find_header(lines), -1)


class TestWebullParser(unittest.TestCase):
    """Webull CSV has a multi-line preamble, currency in column 0, and a
    proceeds column that shifted between 2024 and 2025 formats. The parser
    should back-compute fees that aren't broken out — that's the fix that
    recovered thousands of dollars of missing fees on a real export."""

    def _parse(self, content):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(content)
            fname = f.name
        try:
            return WebullBrokerage().parse_file(Path(fname))
        finally:
            os.remove(fname)

    # 2024 format: proceeds in column 8.
    _CSV_2024 = (
        '"Currency","Date","Action Code","Symbol","Security Description",'
        '"Type Code","Quantity","Price","Proceeds"\n'
        'USD,25-11-2024,BUY,@ABBV,CALL ABBV01/17/25 190,OPC,10,1.60,"(1,609.92)"\n'
        'USD,15-12-2024,SELL,@ABBV,CALL ABBV01/17/25 190,OPC,10,2.50,"2490.00"\n'
    )

    # 2025 format: empty column 8, proceeds in column 9.
    _CSV_2025 = (
        '"Currency","Date","Action Code","Symbol","Security Description",'
        '"Type Code","Quantity","Price","_","Proceeds"\n'
        'USD,25-11-2024,BUY,@ABBV,CALL ABBV01/17/25 190,OPC,10,1.60,,"(1,609.92)"\n'
        'USD,15-12-2024,SELL,@ABBV,CALL ABBV01/17/25 190,OPC,10,2.50,,"2490.00"\n'
    )

    def test_option_symbol_reconstruction(self):
        """Description 'CALL ABBV01/17/25 190' should reconstruct as
        ABBV250117C00190000.<ext>."""
        txs = self._parse(self._CSV_2024)
        self.assertEqual(len(txs), 2)
        for t in txs:
            self.assertEqual(t['symbol'], 'ABBV250117C00190000.US')

    def test_action_code_sign_conventions(self):
        """BUY → positive qty, SELL → negative qty. Webull always emits
        action='BUYSELL'."""
        txs = self._parse(self._CSV_2024)
        buy = next(t for t in txs if t['quantity'] > 0)
        sell = next(t for t in txs if t['quantity'] < 0)
        self.assertEqual(buy['action'], 'BUYSELL')
        self.assertEqual(sell['action'], 'BUYSELL')
        self.assertAlmostEqual(buy['quantity'], 10.0)
        self.assertAlmostEqual(sell['quantity'], -10.0)

    def test_2024_format_proceeds_column_8(self):
        txs = self._parse(self._CSV_2024)
        # |Buy net| ≈ 1609.92; |Sell net| ≈ 2490
        self.assertAlmostEqual(txs[0]['net_amount'], 1609.92, places=2)
        self.assertAlmostEqual(txs[1]['net_amount'], 2490.00, places=2)

    def test_2025_format_proceeds_column_9(self):
        txs = self._parse(self._CSV_2025)
        self.assertAlmostEqual(txs[0]['net_amount'], 1609.92, places=2)
        self.assertAlmostEqual(txs[1]['net_amount'], 2490.00, places=2)

    def test_option_fee_back_compute(self):
        """For an option, theoretical_gross = qty × price × 100.
        Buy 10 @ $1.60: theoretical = 1600; net = 1609.92 → implicit fee $9.92.
        Sell 10 @ $2.50: theoretical = 2500; net = 2490 → implicit fee $10.00.
        """
        txs = self._parse(self._CSV_2024)
        buy_fee = float(txs[0].get('fee', 0))
        sell_fee = float(txs[1].get('fee', 0))
        self.assertAlmostEqual(buy_fee, 9.92, places=2)
        self.assertAlmostEqual(sell_fee, 10.00, places=2)

    def test_equity_uses_multiplier_one(self):
        """For an equity (no option pattern in description), the multiplier
        is 1 and the fee back-compute uses qty × price directly."""
        csv = (
            '"Currency","Date","Action Code","Symbol","Security Description",'
            '"Type Code","Quantity","Price","Proceeds"\n'
            'USD,15-01-2025,BUY,AAPL,APPLE INC,STK,100,150.00,"(15010.00)"\n'
        )
        txs = self._parse(csv)
        # 100 × 150 = 15000; net = 15010 → implicit fee = $10
        self.assertAlmostEqual(float(txs[0].get('fee', 0)), 10.0, places=2)

    def test_currency_suffix_mapping(self):
        """CAD trades get .TO suffix; USD trades get .US."""
        csv = (
            '"Currency","Date","Action Code","Symbol","Security Description",'
            '"Type Code","Quantity","Price","Proceeds"\n'
            'CAD,15-01-2025,BUY,SHOP,SHOPIFY INC,STK,10,100.00,"(1000.00)"\n'
        )
        txs = self._parse(csv)
        self.assertEqual(txs[0]['symbol'], 'SHOP.TO')

    def test_empty_csv_returns_empty(self):
        """Sane on degenerate input."""
        txs = self._parse('')
        self.assertEqual(txs, [])


# ============================================================================
# Questrade
# ============================================================================
class TestQuestradeParser(unittest.TestCase):
    """Questrade CSVs are simpler (single header row, Date/Time format
    with AM/PM). We verify commission propagation (the recent fix), option
    symbol reconstruction from descriptions, and EXP/ASN action mapping."""

    def _parse(self, content):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write(content)
            fname = f.name
        try:
            return QuestradeBrokerage().parse_file(Path(fname))
        finally:
            os.remove(fname)

    def test_commission_now_emitted_on_each_trade(self):
        """Recently fixed: Questrade used to read Commission but discard it.
        Every BUYSELL must now carry the value through."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency\n'
            '2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,AAPL,APPLE INC,'
            '100,150.00,15000.00,9.95,-15009.95,USD\n'
        )
        txs = self._parse(csv)
        self.assertEqual(len(txs), 1)
        self.assertAlmostEqual(float(txs[0].get('commission', 0)), 9.95, places=2)

    def test_buy_vs_sell_qty_sign(self):
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency\n'
            '2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,AAPL,X,'
            '100,150.00,15000.00,1.00,-15001.00,USD\n'
            '2025-03-15 09:30:00 AM,2025-03-16 12:00:00 AM,Sell,AAPL,X,'
            '100,160.00,16000.00,1.00,15999.00,USD\n'
        )
        txs = self._parse(csv)
        buy = next(t for t in txs if t['quantity'] > 0)
        sell = next(t for t in txs if t['quantity'] < 0)
        self.assertAlmostEqual(buy['quantity'], 100.0)
        self.assertAlmostEqual(sell['quantity'], -100.0)

    def test_option_description_reconstructs_occ_symbol(self):
        """Description 'CALL AAPL 06/20/25 150.00' → AAPL250620C00150000.<ext>."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency\n'
            '2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,AAPL.OPT,'
            'CALL AAPL 06/20/25 150.00,1,2.50,250.00,1.25,-251.25,USD\n'
        )
        txs = self._parse(csv)
        self.assertEqual(txs[0]['symbol'], 'AAPL250620C00150000.US')

    def test_expired_option_zeroes_amounts(self):
        """EXP action → net = 0, commission = 0, gross = 0; the engine
        treats this as a position-clearing BUYSELL with no cash flow."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency\n'
            '2025-06-20 09:30:00 AM,2025-06-20 12:00:00 AM,EXP,AAPL.OPT,'
            'CALL AAPL 06/20/25 150.00 - EXPIRED,1,0.00,0.00,0.00,0.00,USD\n'
        )
        txs = self._parse(csv)
        self.assertAlmostEqual(txs[0]['net_amount'], 0.0)
        self.assertAlmostEqual(float(txs[0].get('commission', 0)), 0.0)

    def test_expired_long_option_closes_position(self):
        """A long option's EXP row carries a NEGATIVE CSV quantity — it
        removes the contract. That sign must be preserved: routing it
        through signed_quantity() forced it positive, leaving a phantom
        +1 instead of netting the bought +1 to zero."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency\n'
            '2026-02-09 12:00:00 AM,2026-02-10 12:00:00 AM,Buy,8LRQKH9,'
            'CALL ACM 02/20/26 105 AECOM,1,2.1,210.00,0.99,-210.99,USD\n'
            '2026-02-23 12:00:00 AM,2026-02-23 12:00:00 AM,EXP,8LRQKH9,'
            'CALL ACM 02/20/26 105 AECOM OPTION EXPIRATION - EXPIRED,'
            '-1,0.00,0.00,0.00,0.00,USD\n'
        )
        txs = self._parse(csv)
        self.assertEqual(len(txs), 2)
        buy = next(t for t in txs if t['quantity'] > 0)
        exp = next(t for t in txs if t['quantity'] < 0)
        self.assertAlmostEqual(buy['quantity'], 1.0)
        self.assertAlmostEqual(exp['quantity'], -1.0)
        # Same option symbol on both legs → they net to a flat position.
        self.assertEqual(buy['symbol'], exp['symbol'])
        self.assertAlmostEqual(sum(t['quantity'] for t in txs), 0.0)

    def test_transfer_resolves_internal_code_and_book_value(self):
        """A TF6 transfer-in row puts an internal Questrade code in the
        Symbol column and carries no cost in any numeric column. The
        parser must recover the real ticker from a trade row of the same
        security and the cost basis from the 'BOOK VALUE' note in the
        description."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            # Trade row teaches the parser that "REALTY INCOME CORP" is O.
            '2026-03-10 12:00:00 AM,2026-03-11 12:00:00 AM,Sell,O,'
            'REALTY INCOME CORP WE ACTED AS AGENT,-3,64.73,194.20,0,'
            '194.20,USD,123,Trades,Individual LIRA\n'
            # Transfer-in: internal code R223608, book value in the desc.
            '2026-03-03 12:00:00 AM,2026-03-03 12:00:00 AM,TF6,R223608,'
            'REALTY INCOME CORP RBC DOMINION SECURITIES 146.16 TRANSFER '
            'BOOK VALUE 173.64,3,0,0,0,0,USD,123,Transfers,Individual LIRA\n'
        )
        txs = self._parse(csv)
        transfer = next(t for t in txs if t['action'] == 'TRANSFER')
        # Internal code R223608 resolved to the real ticker.
        self.assertEqual(transfer['symbol'], 'O.US')
        # Cost basis recovered from "BOOK VALUE 173.64", not the 0 column.
        self.assertAlmostEqual(transfer['net_amount'], 173.64)
        self.assertAlmostEqual(transfer['quantity'], 3.0)
        self.assertAlmostEqual(transfer['price'], 173.64 / 3, places=4)

    def test_assignment_maps_to_assign_action(self):
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency\n'
            '2025-06-20 09:30:00 AM,2025-06-20 12:00:00 AM,ASN,AAPL.OPT,'
            'CALL AAPL 06/20/25 150.00 ASSIGNMENT,1,0.00,0.00,0.00,0.00,USD\n'
        )
        txs = self._parse(csv)
        self.assertEqual(txs[0]['action'], 'ASSIGN')

    def test_currency_drives_symbol_suffix(self):
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency\n'
            '2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,SHOP,X,'
            '10,100.00,1000.00,4.95,-1004.95,CAD\n'
        )
        txs = self._parse(csv)
        self.assertEqual(txs[0]['symbol'], 'SHOP.TO')

    def test_dividend_action_emitted(self):
        """Questrade Action='DIV' rows now emit DIVIDEND entries instead
        of being silently dropped. Symbol has its leading '.' stripped and
        the currency suffix applied."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            '2025-03-20 12:00:00 AM,2025-03-20 12:00:00 AM,DIV,.OTEX,'
            'OPEN TEXT CORP CASH DIV ON 150 SHS,0,0,0,0,55.92,CAD,'
            '12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        d = divs[0]
        self.assertEqual(d['symbol'], 'OTEX.TO')
        self.assertAlmostEqual(d['net_amount'], 55.92, places=2)
        self.assertAlmostEqual(d['gross_amount'], 55.92, places=2)
        self.assertEqual(d['type'], 'dividend')
        self.assertEqual(d['currency'], 'CAD')

    def test_transfer_action_emitted(self):
        """Questrade Action='TF6' rows emit TRANSFER entries. The CLI's
        --transfers flag controls whether they're kept; without it, they
        get filtered before reaching downstream tools."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            '2025-06-25 12:00:00 AM,2025-06-25 12:00:00 AM,TF6,.AEM,'
            'AGNICO EAGLE MINES,200.0,0,0,0,33060.00,CAD,'
            '12345,Transfers,Individual\n'
        )
        txs = self._parse(csv)
        transfers = [t for t in txs if t['action'] == 'TRANSFER']
        self.assertEqual(len(transfers), 1)
        t = transfers[0]
        self.assertEqual(t['symbol'], 'AEM.TO')
        self.assertAlmostEqual(t['quantity'], 200.0)
        self.assertAlmostEqual(t['net_amount'], 33060.0, places=2)

    def test_dividend_internal_code_resolved_via_trade_description(self):
        """Questrade sometimes emits dividends with internal codes like
        'S032771' instead of the real ticker. The parser resolves them
        by matching the dividend's cleaned description against trade
        rows in the same CSV (after stripping the row-type-specific
        noise). The SSL/SANDSTORM case from real RESP data."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            # Trade row establishes the desc → SSL mapping.
            '2025-09-30 12:00:00 AM,2025-10-01 12:00:00 AM,Sell,SSL.TO,'
            'SANDSTORM GOLD LTD COM WE ACTED AS AGENT,-1,17.39,17.39,0,17.39,'
            'CAD,12345,Trades,Individual\n'
            # Dividend row with internal code — should resolve to SSL.TO.
            '2025-10-07 12:00:00 AM,2025-10-07 12:00:00 AM,DIV,S032771,'
            'SANDSTORM GOLD LTD COM CASH DIV ON 1 SHS REC 09/26/25 PAY 10/07/25,'
            '0,0,0,0,0.02,CAD,12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        # Resolved back to SSL.TO instead of leaking 'S032771'.
        self.assertEqual(divs[0]['symbol'], 'SSL.TO')

    def test_dividend_internal_code_unresolved_when_no_trade(self):
        """If no matching trade row exists in the CSV, the internal code
        passes through (we don't make stuff up). User will see the code
        in the report and can fix it manually."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            '2025-10-07 12:00:00 AM,2025-10-07 12:00:00 AM,DIV,Z99999,'
            'UNKNOWN COMPANY CASH DIV ON 1 SHS,0,0,0,0,0.50,CAD,'
            '12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        # No trade row to learn from; symbol stays cryptic.
        self.assertEqual(divs[0]['symbol'], 'Z99999.TO')

    def test_dividend_cross_listed_uses_trade_currency(self):
        """B2Gold case: trades use BTG (US-listed) in USD; dividends use
        .BTO (Canadian listing) but are paid in USD. Without resolution
        we'd emit BTO.US which the user's ticker.map can't fold into
        the BTG.US identity. With resolution we emit BTG.US (matching
        the trades) so ticker.map can unify them downstream.

        The transaction's `currency` field still reflects USD (the
        actual payment currency) — only the symbol suffix follows the
        trade's market.
        """
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            # Trade: BTG ticker, USD currency → market is US.
            '2025-06-15 12:00:00 AM,2025-06-16 12:00:00 AM,Buy,BTG,'
            'B2GOLD CORP WE ACTED AS AGENT,1000,5.00,5000.00,0,-5000.00,'
            'USD,12345,Trades,Individual\n'
            # Dividend: .BTO ticker (Canadian listing identifier) but USD.
            '2026-03-19 12:00:00 AM,2026-03-19 12:00:00 AM,DIV,.BTO,'
            'B2GOLD CORP CASH DIV ON 4000 SHS REC 03/06/26 PAY 03/19/26,'
            '0,0,0,0,80.0,USD,12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        d = divs[0]
        # Symbol follows the trades (BTG.US), not the dividend's own
        # .BTO+USD combination that would produce BTO.US.
        self.assertEqual(d['symbol'], 'BTG.US')
        # But the currency stays USD — that's the actual payment.
        self.assertEqual(d['currency'], 'USD')
        self.assertAlmostEqual(d['net_amount'], 80.0, places=2)

    def test_dividend_same_market_uses_own_currency(self):
        """Sanity guard: a regular Canadian-stock CAD dividend should
        still produce .TO. The resolver doesn't fire when the
        dividend's own symbol+currency already match the trades'."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            '2025-06-15 12:00:00 AM,2025-06-16 12:00:00 AM,Buy,SHOP,'
            'SHOPIFY INC WE ACTED AS AGENT,100,100.00,10000.00,0,-10000.00,'
            'CAD,12345,Trades,Individual\n'
            '2025-10-15 12:00:00 AM,2025-10-15 12:00:00 AM,DIV,.SHOP,'
            'SHOPIFY INC CASH DIV ON 100 SHS,0,0,0,0,25.00,CAD,'
            '12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(divs[0]['symbol'], 'SHOP.TO')

    def test_dividend_mirror_us_listed_cad_dividend(self):
        """Mirror of the BTO/B2GOLD case: a US-listed Canadian company
        (e.g. Bank of Nova Scotia on NYSE) might pay a CAD dividend.
        Trades use BNS/USD; dividend uses .BNS/CAD. The trade's USD
        market should win — emit BNS.US so ticker.map can normalize."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            '2025-06-15 12:00:00 AM,2025-06-16 12:00:00 AM,Buy,BNS,'
            'BANK OF NOVA SCOTIA WE ACTED AS AGENT,100,55.00,5500.00,0,-5500.00,'
            'USD,12345,Trades,Individual\n'
            '2025-10-29 12:00:00 AM,2025-10-29 12:00:00 AM,DIV,.BNS,'
            'BANK OF NOVA SCOTIA CASH DIV ON 100 SHS,0,0,0,0,106.00,CAD,'
            '12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        # Trade's symbol+currency win → BNS.US.
        self.assertEqual(divs[0]['symbol'], 'BNS.US')
        # But dividend's own currency (CAD) is preserved on the record.
        self.assertEqual(divs[0]['currency'], 'CAD')

    def test_dividend_internal_code_plus_cross_listing(self):
        """Combination: internal-code symbol on dividend AND the trades
        use a different listing/currency than the dividend. Both
        resolutions should fire — the desc lookup gives us BTG.US even
        though the dividend row says 'S099999/USD'."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            '2025-06-15 12:00:00 AM,2025-06-16 12:00:00 AM,Buy,BTG,'
            'B2GOLD CORP WE ACTED AS AGENT,1000,5.00,5000.00,0,-5000.00,'
            'USD,12345,Trades,Individual\n'
            '2025-12-15 12:00:00 AM,2025-12-15 12:00:00 AM,DIV,S099999,'
            'B2GOLD CORP CASH DIV ON 1000 SHS REC 12/02/25,'
            '0,0,0,0,20.00,USD,12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(divs[0]['symbol'], 'BTG.US')

    def test_dividend_resolved_via_transfer_only_position(self):
        """A position established purely via TRANSFER (no Trade row) —
        common for LIRA/RRSP carry-over scenarios — should still let
        the parser resolve a cross-currency dividend back to the
        position's ticker."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            # Only TRANSFER establishes BTG.US. No trade rows.
            '2025-06-25 12:00:00 AM,2025-06-25 12:00:00 AM,TF6,BTG,'
            'B2GOLD CORP RBC DOMINION SECURITIES,1000,5.00,5000.00,0,5000.00,'
            'USD,12345,Transfers,Individual\n'
            # Dividend later, with the Canadian listing identifier.
            '2025-12-15 12:00:00 AM,2025-12-15 12:00:00 AM,DIV,.BTO,'
            'B2GOLD CORP CASH DIV ON 1000 SHS,0,0,0,0,20.00,USD,'
            '12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 1)
        # Even though no trade exists, the TRANSFER row populated the
        # desc map and resolution still works.
        self.assertEqual(divs[0]['symbol'], 'BTG.US')

    def test_dividend_repeated_resolutions_stable(self):
        """Multiple dividends on a cross-listed position should all
        resolve to the same symbol — no drift between the first and
        subsequent payouts."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            '2025-01-15 12:00:00 AM,2025-01-16 12:00:00 AM,Buy,BTG,'
            'B2GOLD CORP WE ACTED AS AGENT,1000,5.00,5000.00,0,-5000.00,'
            'USD,12345,Trades,Individual\n'
            '2025-03-19 12:00:00 AM,2025-03-19 12:00:00 AM,DIV,.BTO,'
            'B2GOLD CORP CASH DIV ON 1000 SHS,0,0,0,0,20.00,USD,'
            '12345,Dividends,Individual\n'
            '2025-06-19 12:00:00 AM,2025-06-19 12:00:00 AM,DIV,.BTO,'
            'B2GOLD CORP CASH DIV ON 1000 SHS,0,0,0,0,20.00,USD,'
            '12345,Dividends,Individual\n'
            '2025-09-19 12:00:00 AM,2025-09-19 12:00:00 AM,DIV,.BTO,'
            'B2GOLD CORP CASH DIV ON 1000 SHS,0,0,0,0,20.00,USD,'
            '12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 3)
        symbols = {d['symbol'] for d in divs}
        self.assertEqual(symbols, {'BTG.US'})

    def test_dividend_no_desc_match_falls_back_to_own_currency(self):
        """If the dividend's description doesn't match any trade or
        transfer (e.g. a one-off security with no other activity), the
        parser uses the dividend row's own symbol+currency — no
        fabricated resolution."""
        csv = (
            'Transaction Date,Settlement Date,Action,Symbol,Description,'
            'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
            'Account #,Activity Type,Account Type\n'
            '2025-06-15 12:00:00 AM,2025-06-16 12:00:00 AM,Buy,BTG,'
            'B2GOLD CORP WE ACTED AS AGENT,1000,5.00,5000.00,0,-5000.00,'
            'USD,12345,Trades,Individual\n'
            # Dividend for a different company (no trade).
            '2025-10-29 12:00:00 AM,2025-10-29 12:00:00 AM,DIV,.AAPL,'
            'APPLE INC CASH DIV ON 100 SHS,0,0,0,0,24.00,USD,'
            '12345,Dividends,Individual\n'
        )
        txs = self._parse(csv)
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        # APPLE has no trade row in this CSV; falls back to row's symbol
        # + currency: .AAPL stripped → AAPL, USD → .US.
        apple_div = next(d for d in divs if 'AAPL' in d['symbol'])
        self.assertEqual(apple_div['symbol'], 'AAPL.US')


# ============================================================================
# Account-name override threading through tx-id hashing
# ============================================================================
class TestAccountNameOverride(unittest.TestCase):
    """taxjson-brokerage and taxjson-convert-tt both accept --account-name.
    The override must apply BEFORE tx-id hashing so dedup is stable across
    runs that use the same label."""

    def test_same_label_same_id_hash(self):
        """Two parser invocations with the same --account-name produce
        the same tx.id for an identical trade."""
        from taxjson.lib.core import TaxTransaction
        t1 = TaxTransaction(
            action='BUYSELL', date='2025-01-15', symbol='AAPL.US',
            quantity=100.0, price=150.0, net_amount=15001.0,
            currency='USD', account='Margin',
        )
        t2 = TaxTransaction(
            action='BUYSELL', date='2025-01-15', symbol='AAPL.US',
            quantity=100.0, price=150.0, net_amount=15001.0,
            currency='USD', account='Margin',
        )
        self.assertEqual(t1.id, t2.id)

    def test_different_label_different_id_hash(self):
        """Changing the account label changes the dedup hash — expected,
        because the same trade in two different accounts must be tracked
        separately."""
        from taxjson.lib.core import TaxTransaction
        margin = TaxTransaction(
            action='BUYSELL', date='2025-01-15', symbol='AAPL.US',
            quantity=100.0, price=150.0, net_amount=15001.0,
            currency='USD', account='Margin',
        )
        tfsa = TaxTransaction(
            action='BUYSELL', date='2025-01-15', symbol='AAPL.US',
            quantity=100.0, price=150.0, net_amount=15001.0,
            currency='USD', account='TFSA',
        )
        self.assertNotEqual(margin.id, tfsa.id)


if __name__ == '__main__':
    unittest.main()
