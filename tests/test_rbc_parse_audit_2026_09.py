"""RBC Direct parse audit (2026-09): reorganizations, income-vs-name
classification, strict reading, option identity, row accounting.

Every fixture is synthetic (made-up codes, quantities and amounts in the
shape of RBC exports; no Account column — it is optional)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from _tmpfiles import private_tmpfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.rbc_direct import (
    RbcBrokerage, RbcFormatError, read_rbc_rows, rbc_number)
from taxjson.lib.corp_actions import (
    parse_rbc_corporate_actions, pair_rbc_reorganizations)
from tax_rules import rule

REPO = Path(__file__).resolve().parent.parent
HDR = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
       '"Settlement Date","Value","Currency","Description"\n')


def row(date, activity, symbol, symdesc, qty, price, value, cur, desc,
        settle=None):
    cells = [date, activity, symbol, symdesc, qty, price, settle or date,
             value, cur, desc]
    return ','.join('"%s"' % c for c in cells) + '\n'


def _write(text):
    f = private_tmpfile(mode='w', suffix='.csv', delete=False,
                        encoding='utf-8')
    f.write(text)
    f.close()
    return Path(f.name)


def parse(body, header=HDR, country=None):
    """(transactions, stderr, parser) for a CSV body (newest-first);
    `country` sets the notes' wording, as taxjson-brokerage does."""
    p = _write(header + body)
    err = io.StringIO()
    par = RbcBrokerage()
    par.country = country
    try:
        with contextlib.redirect_stderr(err):
            txs = par.parse_file(p)
    finally:
        os.remove(p)
    return txs, err.getvalue(), par


def of(txs, **kw):
    return [t for t in txs if all(t.get(k) == v for k, v in kw.items())]


def position(txs, symbol):
    """Share count of `symbol` replaying BUYSELL / SPLIT rows in date order."""
    q = 0.0
    for t in sorted(txs, key=lambda t: (t['date'], t['time'])):
        if t['action'] == 'BUYSELL' and t['symbol'] == symbol:
            q += t['quantity']
        elif t['action'] == 'SPLIT' and t['symbol'] == symbol:
            q *= t['quantity']
    return q


# ---------------------------------------------------------------- H1 reorgs

class TestReorganizationPairs(unittest.TestCase):
    def test_name_change_same_ticker_books_nothing(self):
        txs, err, par = parse(
            row("September 12, 2023", "Reorganization", "ZZZ", "ZED RAIL CO COM",
                "37", "", "0", "CAD", "NAC - ZED RAIL CO COM RESULT OF NAME CHANGE")
            + row("September 12, 2023", "Reorganization", "Z100001", "ZED RAILWAY LTD",
                  "-37", "", "0", "CAD",
                  "NAC - ZED RAILWAY LTD NAME CHANGE TO ZED RAIL CO")
            + row("September 5, 2023", "Buy", "ZZZ", "ZED RAILWAY LTD", "37",
                  "52.25", "-1943.20", "CAD", "ZED RAILWAY LTD UNSOLICITED DA"))
        # Only the real buy: no $0 BUY of the receipt, no $0 SELL of the
        # temporary code (the old phantom pair +37 ZZZ / -37 Z100001).
        self.assertEqual([t['symbol'] for t in txs], ['ZZZ.TO'])
        self.assertEqual(par._rows_consumed, 3)
        self.assertIn('1-for-1 name change', err)

    def test_name_change_to_new_ticker_renames_the_pool(self):
        txs, err, _ = parse(
            row("August 19, 2024", "Reorganization", "NEWM", "NEWCO MINING CORP COM",
                "650", "", "0", "CAD", "NAC - NEWCO MINING CORP COM RESULT OF NAME CHANGE")
            + row("August 19, 2024", "Reorganization", "F100009", "**OLDCO SILVER MINES INC",
                  "-650", "", "0", "CAD",
                  "NAC - **OLDCO SILVER MINES INC NAME CHG TO NEWCO MINING CORP")
            + row("July 2, 2024", "Buy", "OLDS", "**OLDCO SILVER MINES INC", "650",
                  "5", "-3259.95", "CAD", "OLDCO SILVER MINES INC UNSOLICITED DA"))
        split = of(txs, action='SPLIT')
        self.assertEqual(len(split), 1)
        self.assertEqual(split[0]['symbol'], 'OLDS.TO')
        self.assertEqual(split[0]['symbol_new'], 'NEWM.TO')
        self.assertAlmostEqual(split[0]['quantity'], 1.0)
        self.assertEqual(of(txs, action='BUYSELL', symbol='NEWM.TO'), [])

    def test_reverse_split_is_one_split(self):
        txs, err, _ = parse(
            row("February 14, 2024", "Reorganization", "C099001",
                "CANNA GROWTH CORPORATION COMMON SHARES", "-6000", "", "0", "CAD",
                "REV - CANNA GROWTH CORPORATION COMMON SHARES REV SPLIT TO "
                "CANNA GROWTH CORP NEW; 1 FOR 10")
            + row("February 14, 2024", "Reorganization", "CANN",
                  "CANNA GROWTH CORPORATION COM", "600", "", "0", "CAD",
                  "REV - CANNA GROWTH CORPORATION COM RESULT OF REVERSE SPLIT")
            + row("November 1, 2023", "Buy", "CANN", "CANNA GROWTH CORPORATION COMMON SHARES",
                  "6000", "0.5", "-3009.95", "CAD", "CANNA UNSOLICITED DA"))
        split = of(txs, action='SPLIT')
        self.assertEqual(len(split), 1)
        self.assertEqual(split[0]['symbol'], 'CANN.TO')
        self.assertEqual(split[0]['symbol_new'], '')
        self.assertAlmostEqual(split[0]['quantity'], 0.1)
        self.assertEqual(len(of(txs, action='BUYSELL')), 1)
        self.assertAlmostEqual(position(txs, 'CANN.TO'), 600.0)
        # The description keeps both legs' text (a user's description-keyed
        # override of "RESULT OF REVERSE SPLIT" still catches the row).
        self.assertIn('RESULT OF REVERSE SPLIT', split[0]['description'])

    def test_blank_mgr_forward_split(self):
        # RBC booked a 4-for-1 split as two "MGR -" rows with no text.
        txs, _, _ = parse(
            row("September 9, 2024", "Reorganization", "A099001",
                "ACME NETWORKS INC COM", "-12", "", "0", "USD", "MGR -")
            + row("September 9, 2024", "Reorganization", "ACMN",
                  "ACME NETWORKS INC COMMON STOCK", "48", "", "0", "USD", "MGR -")
            + row("August 1, 2024", "Buy", "ACMN", "ACME NETWORKS INC COM", "12",
                  "400", "-4809.95", "USD", "ACME NETWORKS UNSOLICITED DA"))
        split = of(txs, action='SPLIT')
        self.assertEqual([(s['symbol'], s['quantity']) for s in split],
                         [('ACMN.US', 4.0)])
        self.assertAlmostEqual(position(txs, 'ACMN.US'), 48.0)

    def test_one_for_one_exchange_receipt_is_not_lost_nor_a_merger(self):
        body = (
            row("June 3, 2024", "Reorganization", "P099002",
                "PEBBLE STONE INC", "-7", "", "0", "USD",
                "MGR - PEBBLE STONE INC TO PEBBLE STONE INC COMMON STOCK 1 FOR 1")
            + row("June 3, 2024", "Reorganization", "PBLX",
                  "PEBBLE STONE INC COMMON STOCK", "7", "", "0", "USD",
                  "MGR - PEBBLE STONE INC COMMON STOCK SHRS RECEIVED THRU MERGER")
            + row("January 8, 2024", "Buy", "PBLX", "PEBBLE STONE INC", "7",
                  "800", "-5609.95", "USD", "PEBBLE STONE UNSOLICITED DA"))
        txs, err, par = parse(body)
        self.assertEqual(of(txs, action='SPLIT'), [])
        self.assertAlmostEqual(position(txs, 'PBLX.US'), 7.0)
        self.assertEqual(par._rows_consumed, 3)
        p = _write(HDR + body)
        try:
            self.assertEqual(parse_rbc_corporate_actions(p), [])
        finally:
            os.remove(p)

    def test_mer_roc_consolidation_and_cash_in_lieu(self):
        # 70 old -> 70 x .95 = 66.5 new: 66 delivered + CIL for the .5
        # fraction; C$4.20/sh return of capital = 294.00.
        txs, err, _ = parse(
            row("October 2, 2023", "Reorganization", "TDN",
                "TRIDENT NEWS CORP COM NO PAR", "", "", "25.00", "CAD",
                "CIL - TRIDENT NEWS CORP COM NO PAR CASH IN LIEU OF FRAC SHARES")
            + row("September 18, 2023", "Reorganization", "T099003",
                  "TRIDENT NEWS CORP COM NEW", "-70", "", "294.00", "CAD",
                  "MER - TRIDENT NEWS CORP COM NEW DEFAULT: ROC OF C$4.20 + "
                  ".95 NEW SHS PER 1 OLD")
            + row("September 18, 2023", "Reorganization", "TDN",
                  "TRIDENT NEWS CORP COM NO PAR", "66", "", "0", "CAD",
                  "MGR - TRIDENT NEWS CORP COM NO PAR SHRS RECEIVED THRU MERGER")
            + row("August 1, 2023", "Buy", "TDN", "TRIDENT NEWS CORP COM NEW", "70",
                  "120", "-8409.95", "CAD", "TRIDENT UNSOLICITED DA"))
        roc = of(txs, action='ADJUST')
        self.assertEqual(len(roc), 1)
        self.assertEqual(roc[0]['symbol'], 'TDN.TO')
        self.assertAlmostEqual(roc[0]['net_amount'], -294.00)
        self.assertEqual(roc[0]['type'], 'roc')
        split = of(txs, action='SPLIT')
        self.assertAlmostEqual(split[0]['quantity'], 0.95)
        frac = [t for t in of(txs, action='BUYSELL') if t['quantity'] < 0]
        self.assertEqual(len(frac), 1)
        self.assertAlmostEqual(frac[0]['quantity'], -(70 * 0.95 - 66))
        self.assertAlmostEqual(frac[0]['net_amount'], 25.00)
        self.assertEqual(frac[0]['date'], '2023-10-02')
        self.assertAlmostEqual(position(txs, 'TDN.TO'), 66.0, places=6)

    def test_reverse_entry_cancels(self):
        txs, err, par = parse(
            row("March 11, 2024", "Reorganization", "G099004", "", "-450", "",
                "0", "USD", "REV - GLOBEX DATA CENTER ETF USD AS OF 03/11/24 "
                "REVERSE ENTRY", settle="March 12, 2024")
            + row("March 11, 2024", "Reorganization", "G099004", "", "450", "",
                  "0", "USD", "REV - GLOBEX DATA CENTER ETF USD RESULT OF "
                  "REVERSE SPLIT"))
        self.assertEqual(txs, [])
        self.assertEqual(par.lint_findings, [])
        self.assertIn('nets to zero', err)

    def test_unmatched_legs_warn_and_lint(self):
        txs, err, par = parse(
            row("October 15, 2024", "Reorganization", "BFIX",
                "BRAMBLE INFRA CORP NEW", "130", "", "0", "CAD",
                "MGR - BRAMBLE INFRA CORP NEW SHRS RECEIVED THRU MERGER")
            + row("February 6, 2024", "Reorganization", "Q099005", "QUUX LTD",
                  "-14", "", "0", "CAD", "NAC - QUUX LTD NAME CHANGE TO QUUX CORP"))
        self.assertEqual(txs, [])
        self.assertEqual(err.count('UNMATCHED reorganization leg'), 2)
        self.assertEqual(len(par.lint_findings), 2)

    def test_share_for_share_merger_still_goes_to_corp_actions(self):
        txs, err, _ = parse(
            row("2025-03-17 00:00:00", "Reorganization", "ZPHX",
                "ZEPHYR CORPORATION", "22", "", "0", "USD",
                "MGR - ZEPHYR CORPORATION SHRS RECEIVED THRU MERGER")
            + row("2025-03-17 00:00:00", "Reorganization", "W099006",
                  "WINDCO CORPORATION", "-20", "", "0", "USD",
                  "MGR - WINDCO CORPORATION MERGER TO ZEPHYR CORPORATION "
                  "1.1 NEW = 1 OLD"))
        self.assertEqual(txs, [])
        self.assertIn('resolved by taxjson-corp-actions', err)


class TestOptionAdjustments(unittest.TestCase):
    OLD = ("CALL .KRN   07/18/25    37 KESTREL OIL CORP ADJ: SPCL CASH DIVD "
           "CAD $0.50")

    def test_xch_keeps_the_contract_and_the_later_close_matches(self):
        txs, err, par = parse(
            row("April 22, 2025", "Sell", "8ZZKRN2", "", "-6", "2.35",
                "1403.05", "CAD", self.OLD + " UNSOLICITED CA")
            + row("March 10, 2025", "Reorganization", "8ZZKRN2", "", "6", "",
                  "0", "CAD", "XCH - CALL .KRN   07/18/25    36.50 KESTREL OIL CORP "
                  "ADJ: SPCL CASH DIVD CAD $0.50 ADJ FOR SPECIAL CASH DIV")
            + row("March 10, 2025", "Reorganization", "8ZZKRN1", "", "-6", "",
                  "0", "CAD", "XCH - CALL .KRN   07/18/25    37 KESTREL OIL CORP "
                  "ADJ: SPCL CASH DIVD CAD $0.50 ADJ FOR SPECIAL CASH DIV")
            + row("February 3, 2025", "Buy", "8ZZKRN1", "", "6", "2.35",
                  "-1416.95", "CAD", self.OLD + " UNSOLICITED DA"))
        occ = 'KRN250718C00037000.TO'
        self.assertEqual({t['symbol'] for t in txs}, {occ})
        self.assertAlmostEqual(position(txs, occ), 0.0)
        self.assertEqual(of(txs, action='SPLIT'), [])
        self.assertEqual(par.lint_findings, [])
        self.assertIn('same contract', err)

    def test_xch_renames_when_the_new_code_trades_under_new_terms(self):
        txs, err, _ = parse(
            row("April 22, 2025", "Sell", "8ZZKRN2", "", "-6", "2.35",
                "1403.05", "CAD", "CALL .KRN   07/18/25    36.50 KESTREL OIL CA")
            + row("March 10, 2025", "Reorganization", "8ZZKRN2", "", "6", "",
                  "0", "CAD", "XCH - CALL .KRN   07/18/25    36.50 KESTREL OIL CORP")
            + row("March 10, 2025", "Reorganization", "8ZZKRN1", "", "-6", "",
                  "0", "CAD", "XCH - CALL .KRN   07/18/25    37 KESTREL OIL CORP")
            + row("February 3, 2025", "Buy", "8ZZKRN1", "", "6", "2.35",
                  "-1416.95", "CAD", self.OLD + " UNSOLICITED DA"))
        split = of(txs, action='SPLIT')
        self.assertEqual(len(split), 1)
        self.assertEqual(split[0]['symbol'], 'KRN250718C00037000.TO')
        self.assertEqual(split[0]['symbol_new'], 'KRN250718C00036500.TO')
        self.assertAlmostEqual(split[0]['quantity'], 1.0)


# ------------------------------------------------- H2 names are not income

class TestNamesAreNotIncome(unittest.TestCase):
    def test_in_kind_transfer_of_dividend_named_fund(self):
        txs, _, _ = parse(
            row("August 15, 2022", "Transfers", "DMX", "DIVIDEND MAPLE SPLIT CORP CL-A SHS",
                "1500", "", "0", "CAD", "TFI - DIVIDEND MAPLE SPLIT CORP CL-A SHS "
                "ACCOUNT TRANSFER BOOK VALUE           11250.40 FROM ACCOUNT"))
        self.assertEqual(of(txs, action='DIVIDEND'), [])
        tr = of(txs, action='TRANSFER')
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]['quantity'], 1500.0)
        self.assertAlmostEqual(tr[0]['book_value'], 11250.40)     # M11
        self.assertEqual(tr[0]['net_amount'], 0.0)                # unchanged

    def test_tender_worded_row_is_a_disposition_not_a_dividend(self):
        # A retraction row reworded RETRACTION -> TENDERED. The old
        # parser booked the proceeds as a DIVIDEND and kept the shares.
        txs, _, _ = parse(
            row("June 14, 2024", "Other", "DMX", "DIVIDEND MAPLE SPLIT CORP CL-A SHS",
                "-12000", "", "45750", "CAD",
                "TEN - DIVIDEND MAPLE SPLIT CORP CL-A SHS TENDERED AT C$3.8125 PER SHARE"))
        self.assertEqual(of(txs, action='DIVIDEND'), [])
        (t,) = txs
        self.assertEqual((t['action'], t['quantity']), ('BUYSELL', -12000.0))
        self.assertAlmostEqual(t['net_amount'], 45750.0)

    def test_income_row_with_shares_is_refused(self):
        with self.assertRaises(RbcFormatError) as cm:
            parse(row("March 15, 2024", "Dividends", "XYZ", "XYZ CORP", "100",
                      "", "50.00", "CAD", "DIV - XYZ CORP CASH DIV ON 100 SHS"))
        self.assertIn('never moves shares', str(cm.exception))

    def test_cash_in_lieu_of_dividend_is_income_not_cil(self):
        from taxjson.lib.brokerages.rbc_direct import (classify_rbc_row,
                                                       read_rbc_rows)
        for desc, want in (("XYZ CASH IN LIEU OF DIVIDEND", "dividend"),
                           ("XYZ CASH IN LIEU OF FRAC SHARES", "cil")):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "rbc.csv"
                p.write_text(HDR + row("March 15, 2024", "Dividends",
                                       "XYZ", "XYZ CORP", "", "", "5.00",
                                       "CAD", desc))
                (r,) = read_rbc_rows(p).rows
            self.assertEqual(classify_rbc_row(r), want, desc)
        txs, _, _ = parse(row("March 15, 2024", "Dividends", "XYZ", "XYZ CORP", "",
                              "", "5.00", "CAD", "XYZ CORP CASH IN LIEU OF DIVIDEND"))
        # Income (a payment in lieu since re-audit A2-0098), not a CIL.
        self.assertEqual([t['action'] for t in txs], ['DIVIDEND_IN_LIEU'])


# ------------------------------------------------ M1 / M2 income variants

class TestReinvestAndBookCost(unittest.TestCase):
    @rule("CA-DIST-03")
    @rule("US-DIST-03")
    def test_reinvestment_is_a_purchase_and_the_distribution_stays(self):
        body = (row("7/15/2022", "Dividends", "SRX.UN", "SMARTX REIT UNIT", "2", "",
                    "-57.23", "CAD", "REI - SMARTX REIT UNIT REINV@C$28.6150 REC "
                    "06/30/22 PAY 07/15/22", settle="15-Jul-22")
                + row("7/15/2022", "Dividends", "SRX.UN", "SMARTX REIT UNIT", "",
                      "0.1542", "61.68", "CAD", "SMARTX REIT UNIT DIST      ON     "
                      "400 SHS REC 06/30/22 PAY 07/15/22", settle="15-Jul-22")
                + row("6/13/2022", "Buy", "SRX.UN", "SMARTX REIT UNIT", "400", "30",
                      "-12009.95", "CAD", "SMARTX UNSOLICITED DA", settle="15-Jun-22"))
        txs, _, _ = parse(body)
        rei = [t for t in of(txs, action='BUYSELL') if t['date'] == '2022-07-15']
        self.assertEqual(len(rei), 1)
        self.assertEqual(rei[0]['quantity'], 2.0)
        self.assertAlmostEqual(rei[0]['net_amount'], 57.23)
        self.assertAlmostEqual(rei[0]['price'], 28.6150)
        div = of(txs, action='DIVIDEND')
        self.assertEqual([d['net_amount'] for d in div], [61.68])
        self.assertAlmostEqual(position(txs, 'SRX.UN.TO'), 402.0)

    def test_book_cost_adjustments_are_signed_adjusts(self):
        txs, err, _ = parse(
            row("December 31, 2023", "Return of Capital", "MHX", "MAPLEX HIGH DIVID ETF",
                "", "", "0", "CAD", "RTC - MAPLEX HIGH DIVID ETF 2023 RETURN OF "
                "CAPITAL ADJUSTMENT TO BOOK COST $0.87", settle="May 6, 2024")
            + row("December 31, 2023", "Dividends", "MHX", "MAPLEX HIGH DIVID ETF",
                  "", "", "0", "CAD", "ADJ - MAPLEX HIGH DIVID ETF 2023 NOTIONAL "
                  "DISTRIBUTION ADJUSTMENT TO BOOK COST $1204.55",
                  settle="May 6, 2024"))
        adj = sorted((t['net_amount'], t['type']) for t in of(txs, action='ADJUST'))
        self.assertEqual(adj, [(-0.87, 'roc'), (1204.55, 'dist')])
        self.assertEqual(of(txs, action='DIVIDEND'), [])

    def test_zero_dividend_warns(self):
        _, err, _ = parse(row("March 15, 2024", "Dividends", "XYZ", "XYZ CORP", "",
                              "", "0", "CAD", "DIV - XYZ CORP CASH DIV ON 100 SHS"))
        self.assertIn('$0 dividend row', err)


# ------------------------------------------------------- M5 strict reading

class TestStrictReading(unittest.TestCase):
    def test_missing_required_column_raises(self):
        hdr = HDR.replace(',"Currency"', '')
        body = '"March 15, 2024","Buy","XYZ","XYZ CORP","1","1","March 16, 2024","-1","x"\n'
        with self.assertRaises(RbcFormatError) as cm:
            parse(body, header=hdr)
        self.assertIn('Currency', str(cm.exception))

    def test_preamble_mentioning_date_is_not_the_header(self):
        txs, _, _ = parse(
            row("March 15, 2024", "Buy", "XYZ", "XYZ CORP", "10", "5", "-59.95",
                "CAD", "XYZ UNSOLICITED DA"),
            header='"Activity Export as of Date Jan 5, 2026"\n\n' + HDR)
        self.assertEqual(len(txs), 1)

    def test_lowercase_headers_and_currency(self):
        txs, _, _ = parse(
            row("March 15, 2024", "Buy", "XYZ", "XYZ CORP", "10", "5", "-59.95",
                "usd", "XYZ UNSOLICITED DA"), header=HDR.lower())
        self.assertEqual((txs[0]['symbol'], txs[0]['currency']), ('XYZ.US', 'USD'))

    def test_numbers(self):
        kw = dict(path=Path('x.csv'), line=1, column='Value')
        self.assertEqual(rbc_number('1,234.50', **kw), 1234.5)
        self.assertEqual(rbc_number('(12.50)', **kw), -12.5)
        self.assertEqual(rbc_number('', **kw), 0.0)
        for bad in ('39 043', '1,5', '12abc', '(-3)', '1.2.3'):
            with self.assertRaises(RbcFormatError, msg=bad):
                rbc_number(bad, **kw)

    def test_french_number_in_a_file_raises(self):
        with self.assertRaises(RbcFormatError):
            parse(row("March 15, 2024", "Buy", "XYZ", "XYZ CORP", "10", "5",
                      "-39 043", "CAD", "XYZ UNSOLICITED DA"))

    def test_ambiguous_day_month_raises(self):
        with self.assertRaises(RbcFormatError) as cm:
            parse(row("03/04/2024", "Dividends", "XYZ", "XYZ CORP", "", "", "5",
                      "CAD", "DIV - XYZ CASH DIV ON 10 SHS"))
        self.assertIn('ambiguous', str(cm.exception))

    def test_settlement_lag_settles_month_day(self):
        txs, _, _ = parse(row("03/04/2024", "Buy", "XYZ", "XYZ CORP", "10", "5",
                              "-59.95", "CAD", "XYZ UNSOLICITED DA",
                              settle="03/06/2024"))
        self.assertEqual((txs[0]['date'], txs[0]['date_settle']),
                         ('2024-03-04', '2024-03-06'))

    def test_day_first_file_is_read_as_day_first(self):
        txs, err, _ = parse(row("25/03/2024", "Buy", "XYZ", "XYZ CORP", "10", "5",
                                "-59.95", "CAD", "XYZ UNSOLICITED DA",
                                settle="26/03/2024"))
        self.assertEqual(txs[0]['date'], '2024-03-25')
        self.assertIn('DAY/MONTH', err)

    def test_mixed_date_formats_raise(self):
        with self.assertRaises(RbcFormatError) as cm:
            parse(row("March 15, 2024", "Buy", "XYZ", "XYZ CORP", "10", "5", "-59.95",
                      "CAD", "XYZ UNSOLICITED DA")
                  + row("2024-03-14", "Buy", "XYZ", "XYZ CORP", "10", "5", "-59.95",
                        "CAD", "XYZ UNSOLICITED DA"))
        self.assertIn('mixed date formats', str(cm.exception))

    def test_unquoted_comma_in_last_column_is_rejoined(self):
        txs, _, _ = parse(
            '"February 22, 2024","Interest","","","","","February 22, 2024",'
            '"-71.23","USD","INT FR 01/22 THRU02/21@ 8 1/4% BAL   10","203-  '
            'AVBAL   9","876"\n')
        self.assertEqual(txs[0]['description'],
                         'INT FR 01/22 THRU02/21@ 8 1/4% BAL   10,203-  AVBAL   9,876')

    def test_shifted_columns_raise(self):
        # A comma inside Symbol Description of an unquoted file shifts
        # Quantity/Price/... one cell right: refused, never guessed.
        hdr = HDR.replace('"', '')
        body = ('3/15/2024,Buy,XYZ,XYZ, INC,10,5,3/16/2024,-59.95,CAD,'
                'XYZ UNSOLICITED DA\n')
        with self.assertRaises(RbcFormatError):
            parse(body, header=hdr)


# -------------------------------------------------- M6 option identity

class TestOptionIdentity(unittest.TestCase):
    def test_contract_only_in_symbol_description(self):
        txs, _, _ = parse(
            row("September 9, 2024", "Buy", "8ZZLNX1", "CALL .LNX   12/20/24    37 "
                "LAKEVIEW BANK", "15", "0.31", "-478.45", "CAD", ""))
        self.assertEqual(txs[0]['symbol'], 'LNX241220C00037000.TO')
        self.assertAlmostEqual(txs[0]['fee'], 13.45, places=2)

    def test_one_code_two_descriptions_keeps_the_first(self):
        txs, err, _ = parse(
            row("November 10, 2025", "Sell", "8ZZPNX1", "", "-4", "4.40", "1750.05",
                "CAD", "CALL .PNX.B   06/18/27    33 PINEX CA CLOSE CONTRACT")
            + row("March 4, 2025", "Buy", "8ZZPNX1", "", "4", "2.15", "-869.95",
                  "CAD", "CALL .PNX   06/18/27    33 PINEX DA OPEN CONTRACT"))
        self.assertEqual({t['symbol'] for t in txs}, {'PNX270618C00033000.TO'})
        self.assertIn('more than one contract', err)

    def test_emitted_rbc_code_warns(self):
        _, err, _ = parse(row("March 15, 2024", "Buy", "8ZZNONE", "", "1", "1",
                              "-109.95", "USD", "SOMETHING UNSOLICITED DA"))
        self.assertIn('RBC internal code', err)


# --------------------------------------------- M7 accounting, M10 spinoffs

# RBC writes a rights issue's expiry as "EXP <mm/dd/yyyy>" (built here so
# the literal doesn't read as a payment-card expiry to secret scanners).
RTS_EXP = "EXP " + "/".join(("11", "14", "2023"))


class TestAccountingAndSpinoffs(unittest.TestCase):
    def test_known_cash_codes_and_footers_are_non_events(self):
        txs, err, par = parse(
            row("March 15, 2024", "Deposits & Contributions", "", "", "", "", "6.95",
                "CAD", "ADJ - ACCOUNT ADJUSTMENT CAF - TRADE REBATES")
            + row("March 14, 2024", "Withdrawals & De-registrations", "", "", "", "",
                  "-500", "CAD", "WIR - TRANSFER FUNDS TO RBC")
            + row("March 13, 2024", "Transfers", "", "", "", "", "385.77", "USD",
                  "TFI - ACCOUNT TRANSFER FROM ACCOUNT")
            + row("March 12, 2024", "Other", "", "", "", "", "425.3", "CAD",
                  "MKT - MARK TO MARKET")
            + '\n"Disclaimers"\n"1 This exchange rate is based on ..."\n')
        self.assertEqual(txs, [])
        self.assertEqual(par._rows_seen, par._rows_consumed
                         + sum(par._skip_counts.values()))
        self.assertTrue(all(k.startswith('non-event ') for k in par._skip_counts))
        self.assertNotIn('unclassified', err)
        self.assertEqual(par.lint_findings, [])

    def test_unclassified_row_with_cash_is_loud_and_fails_lint(self):
        body = row("March 15, 2024", "Fancy New Thing", "ZZZ", "", "", "", "12.00",
                   "CAD", "ZZZ - SOMETHING NEW")
        _, err, par = parse(body)
        self.assertIn('UNCLASSIFIED row that moves cash', err)
        self.assertEqual(len(par.lint_findings), 1)
        p = _write(HDR + body)
        try:
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
                 '--brokerage', 'rbc', '--lint', str(p)],
                capture_output=True, text=True,
                env={**os.environ, 'PYTHONPATH': str(REPO / 'src')})
        finally:
            os.remove(p)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('lint:', r.stderr)

    def test_book_value_is_evidence_not_an_unknown_field(self):
        p = _write(HDR + row("August 15, 2022", "Transfers", "DMX", "DMX FUND", "10",
                             "", "0", "CAD", "TFI - DMX FUND ACCOUNT TRANSFER BOOK "
                             "VALUE 100.00 FROM ACCOUNT"))
        try:
            r = subprocess.run(
                [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
                 '--brokerage', 'rbc', '--transfers', str(p)],
                capture_output=True, text=True,
                env={**os.environ, 'PYTHONPATH': str(REPO / 'src')})
        finally:
            os.remove(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('unknown field', r.stderr)
        self.assertEqual(json.loads(r.stdout)['transactions'][0]['action'],
                         'TRANSFER')

    def test_spinoff_goes_to_the_election_machinery(self):
        body = (row("August 12, 2024", "Reorganization", "OPWX", "ORBIT POWER LLC COMMON STOCK",
                    "30", "", "0", "USD", "DIS - ORBIT POWER LLC COMMON STOCK SPINOFF   "
                    "ON     150 SHS FROM SEC# O099007 ORBIT DYNAMICS REC 08/05/24 PAY 08/06/24")
                + row("May 6, 2024", "Buy", "ORDX", "ORBIT DYNAMICS COMMON STOCK", "150",
                      "80", "-12009.95", "USD", "ORBIT UNSOLICITED DA"))
        txs, err, _ = parse(body)
        self.assertEqual(of(txs, symbol='OPWX.US'), [])
        # The note names the stage that asks for the election; the
        # election itself is per country (audit S064-20).
        self.assertIn('taxjson-corp-actions', err)
        self.assertNotIn('s.86.1', err)
        p = _write(HDR + body)
        try:
            (ev,) = parse_rbc_corporate_actions(p, 'margin')
        finally:
            os.remove(p)
        self.assertEqual((ev.action_type, ev.source_symbol, ev.target_symbol,
                          ev.qty_received, ev.ratio_old),
                         ('spinoff', 'ORDX.US', 'OPWX.US', 30.0, 150.0))

    def test_rights_are_a_noted_nil_cost_acquisition_and_expire(self):
        body = (
            row("November 17, 2023", "Reorganization", "Q099008", "", "-1", "", "0",
                "CAD", f"EXP - RTS QUILLON SOFTWARE INC {RTS_EXP} {RTS_EXP} "
                "AS OF 11/17/23 EXPIRED", settle="November 20, 2023")
            + row("October 6, 2023", "Reorganization", "QSW.RT", "", "1", "", "0",
                  "CAD", f"DIS - RTS QUILLON SOFTWARE INC {RTS_EXP} {RTS_EXP} "
                  "RTS DIST  ON       1 SHS REC 09/29/23 PAY 10/06/23"))
        txs, err, _ = parse(body, country='canada')
        self.assertEqual({t['symbol'] for t in txs}, {'QSW.RT.TO'})
        self.assertAlmostEqual(position(txs, 'QSW.RT.TO'), 0.0)
        self.assertIn('15(1)(c)', err)
        # The same rows in a US project: the same booking, US words
        # (re-audit A2-1314: the test pinned the Canadian citation).
        us_txs, us_err, _ = parse(body, country='usa')
        self.assertEqual(us_txs, txs)
        self.assertNotIn('ITA s.', us_err)
        self.assertNotIn('ACB', us_err)
        self.assertIn('§305', us_err)


# ------------------------------------------------------ LOW + FEE + misc

class TestLowItems(unittest.TestCase):
    def test_usd_class_of_a_tsx_fund_is_said_with_the_extract_line(self):
        # No security is named in the parser (owner 2026-10-04): the USD
        # rows of a TSX fund's US-dollar class read as a .US listing, said
        # out loud with the ticker.map EXTRACT line that moves them
        # (test_fix_generalize pins the line moving them).
        txs, err, _ = parse(
            row("March 15, 2024", "Buy", "ZZD", "SAMPLE U S DLR CURRENCY ETF UNIT",
                "100", "10", "-1009.95", "USD", "SAMPLE U S DLR CURRENCY ETF DA")
            + row("March 13, 2024", "Sell", "ZZD", "SAMPLE U S DLR CURRENCY ETF UNIT",
                  "-100", "13.5", "1340.05", "CAD", "SAMPLE U S DLR CURRENCY ETF CA"))
        self.assertEqual([t['symbol'] for t in txs], ['ZZD.US', 'ZZD.TO'])
        self.assertIn("EXTRACT SAMPLE U S DLR CURRENCY ETF | USD | ZZD.U.TO",
                      err)

    def test_same_day_order_follows_the_file(self):
        # Newest-first: the rebuy is listed ABOVE the sell it followed.
        txs, _, _ = parse(
            row("March 15, 2024", "Buy", "XYZ", "XYZ CORP", "10", "6", "-69.95",
                "CAD", "XYZ UNSOLICITED DA")
            + row("March 15, 2024", "Sell", "XYZ", "XYZ CORP", "-10", "5", "40.05",
                  "CAD", "XYZ UNSOLICITED CA")
            + row("March 1, 2024", "Buy", "XYZ", "XYZ CORP", "10", "7", "-79.95",
                  "CAD", "XYZ UNSOLICITED DA"))
        by = {(t['date'], t['quantity']): t['time'] for t in txs}
        self.assertLess(by[('2024-03-15', -10.0)], by[('2024-03-15', 10.0)])
        self.assertEqual(by[('2024-03-01', 10.0)], '09:30:00')

    def test_assignment_rows_share_one_time(self):
        # The ASN option leg is listed ABOVE its stock leg; file order would
        # put the stock sale first and the engine would miss the s.49(3)
        # premium fold.
        txs, _, _ = parse(
            row("October 17, 2025", "Other", "9ZZKOI1", "", "1", "", "0", "USD",
                "ASN - CALL KOIX   10/17/25   82.50 KOIX GLOBAL INC ASSIGNMENT "
                "OF OPTION", settle="October 21, 2025")
            + row("October 17, 2025", "Sell", "KOIX", "KOIX GLOBAL INC", "-100",
                  "82.5", "8207", "USD", "KOIX GLOBAL INC ASSIGNMENT OF OPTION "
                  "AS OF 10/17/25", settle="October 21, 2025")
            + row("October 17, 2025", "Buy", "ZZZ", "ZZZ CORP", "10", "5", "-59.95",
                  "USD", "ZZZ UNSOLICITED DA", settle="October 20, 2025"))
        t_opt = of(txs, action='ASSIGN')[0]['time']
        t_stock = of(txs, symbol='KOIX.US')[0]['time']
        self.assertEqual(t_opt, t_stock)

    def test_adr_fee_is_a_fee_row(self):
        txs, _, _ = parse(row("July 9, 2024", "Fees", "OCNX", "OCEANIX LTD ADS", "", "",
                              "-6", "USD", "FCH - OCEANIX LTD ADS DTCC ADR FEE 0.03"))
        self.assertEqual([(t['action'], t['symbol'], t['net_amount']) for t in txs],
                         [('FEE', 'OCNX.US', 6.0)])


class TestDuplicateSplitWarning(unittest.TestCase):
    def test_parser_split_plus_manual_line_warns(self):
        from taxjson.bin.taxjson_merge2 import warn_duplicate_splits
        from taxjson.lib.core import TaxTransaction
        a = TaxTransaction(action='SPLIT', date='2024-02-14', symbol='CANN.TO',
                           quantity=0.1, account='margin', time='09:30:11',
                           description='RBC REV reorganization')
        b = TaxTransaction(action='SPLIT', date='2024-02-14', symbol='CANN.TO',
                           symbol_new='CANN.TO', quantity=0.1, account='margin')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(warn_duplicate_splits([a, b]), 1)
        self.assertIn('duplicate split', err.getvalue())
        c = TaxTransaction(action='SPLIT', date='2024-02-14', symbol='CANN.TO',
                           quantity=0.2, account='margin')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            warn_duplicate_splits([a, c])
        self.assertIn('conflicting splits', err.getvalue())


class TestPairingApi(unittest.TestCase):
    def test_every_leg_is_accounted_for(self):
        p = _write(HDR
                   + row("August 26, 2024", "Reorganization", "9ZZVLX1", "", "35", "", "0",
                         "USD", "XCH - CALL VOLX   03/21/25    19 VOLTEX DIGITAL "
                         "SECURITY CODE ADJUSTMENT")
                   + row("August 26, 2024", "Reorganization", "8ZZVLX1", "", "-35", "",
                         "0", "USD", "XCH - CALL VOLX   03/21/25    19 VOLTEX DIGITAL "
                         "SECURITY CODE ADJUSTMENT"))
        try:
            rows = read_rbc_rows(p).rows
        finally:
            os.remove(p)
        res = pair_rbc_reorganizations(rows)
        self.assertEqual([e.kind for e in res.events], ['option_adjust'])
        self.assertEqual(res.unmatched, [])


if __name__ == '__main__':
    unittest.main()
