"""RBC Direct parse audit (2026-09): reorganizations, income-vs-name
classification, strict reading, option identity, row accounting.

Every fixture is synthetic (made-up codes, quantities and amounts in the
shape of real RBC exports; no Account column — it is optional). The same
behaviours were proven on the 13 real export versions (see CHANGELOG).
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

from taxjson.lib.brokerages.rbc_direct import (
    RbcBrokerage, RbcFormatError, read_rbc_rows, rbc_number)
from taxjson.lib.corp_actions import (
    parse_rbc_corporate_actions, pair_rbc_reorganizations)

REPO = Path(__file__).resolve().parent.parent
HDR = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
       '"Settlement Date","Value","Currency","Description"\n')


def row(date, activity, symbol, symdesc, qty, price, value, cur, desc,
        settle=None):
    cells = [date, activity, symbol, symdesc, qty, price, settle or date,
             value, cur, desc]
    return ','.join('"%s"' % c for c in cells) + '\n'


def _write(text):
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False,
                                    encoding='utf-8')
    f.write(text)
    f.close()
    return Path(f.name)


def parse(body, header=HDR, country=None):
    """(transactions, stderr, parser) for a CSV body (newest-first).
    `country`: the project country taxjson-brokerage passes (it only
    picks which law a message cites)."""
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
            row("April 18, 2023", "Reorganization", "ZZZ", "ZED RAIL CO COM",
                "41", "", "0", "CAD", "NAC - ZED RAIL CO COM RESULT OF NAME CHANGE")
            + row("April 18, 2023", "Reorganization", "Z100001", "ZED RAILWAY LTD",
                  "-41", "", "0", "CAD",
                  "NAC - ZED RAILWAY LTD NAME CHANGE TO ZED RAIL CO")
            + row("April 10, 2023", "Buy", "ZZZ", "ZED RAILWAY LTD", "41",
                  "86.5", "-3556.45", "CAD", "ZED RAILWAY LTD UNSOLICITED DA"))
        # Only the real buy: no $0 BUY of the receipt, no $0 SELL of the
        # temporary code (the old phantom pair +41 ZZZ / -41 Z100001).
        self.assertEqual([t['symbol'] for t in txs], ['ZZZ.TO'])
        self.assertEqual(par._rows_consumed, 3)
        self.assertIn('1-for-1 name change', err)

    def test_name_change_to_new_ticker_renames_the_pool(self):
        txs, err, _ = parse(
            row("June 27, 2024", "Reorganization", "NEWM", "NEWCO MINING CORP COM",
                "500", "", "0", "CAD", "NAC - NEWCO MINING CORP COM RESULT OF NAME CHANGE")
            + row("June 27, 2024", "Reorganization", "F100009", "**OLDCO SILVER MINES INC",
                  "-500", "", "0", "CAD",
                  "NAC - **OLDCO SILVER MINES INC NAME CHG TO NEWCO MINING CORP")
            + row("May 1, 2024", "Buy", "OLDS", "**OLDCO SILVER MINES INC", "500",
                  "5", "-2509.95", "CAD", "OLDCO SILVER MINES INC UNSOLICITED DA"))
        split = of(txs, action='SPLIT')
        self.assertEqual(len(split), 1)
        self.assertEqual(split[0]['symbol'], 'OLDS.TO')
        self.assertEqual(split[0]['symbol_new'], 'NEWM.TO')
        self.assertAlmostEqual(split[0]['quantity'], 1.0)
        self.assertEqual(of(txs, action='BUYSELL', symbol='NEWM.TO'), [])

    def test_reverse_split_is_one_split(self):
        txs, err, _ = parse(
            row("December 20, 2023", "Reorganization", "C099001",
                "CANNA GROWTH CORPORATION COMMON SHARES", "-9000", "", "0", "CAD",
                "REV - CANNA GROWTH CORPORATION COMMON SHARES REV SPLIT TO "
                "CANNA GROWTH CORP NEW; 1 FOR 10")
            + row("December 20, 2023", "Reorganization", "CANN",
                  "CANNA GROWTH CORPORATION COM", "900", "", "0", "CAD",
                  "REV - CANNA GROWTH CORPORATION COM RESULT OF REVERSE SPLIT")
            + row("March 1, 2023", "Buy", "CANN", "CANNA GROWTH CORPORATION COMMON SHARES",
                  "9000", "0.5", "-4509.95", "CAD", "CANNA UNSOLICITED DA"))
        split = of(txs, action='SPLIT')
        self.assertEqual(len(split), 1)
        self.assertEqual(split[0]['symbol'], 'CANN.TO')
        self.assertEqual(split[0]['symbol_new'], '')
        self.assertAlmostEqual(split[0]['quantity'], 0.1)
        self.assertEqual(len(of(txs, action='BUYSELL')), 1)
        self.assertAlmostEqual(position(txs, 'CANN.TO'), 900.0)
        # The description keeps both legs' text (a user's description-keyed
        # override of "RESULT OF REVERSE SPLIT" still catches the row).
        self.assertIn('RESULT OF REVERSE SPLIT', split[0]['description'])

    def test_blank_mgr_forward_split(self):
        # RBC booked a 4-for-1 split as two "MGR -" rows with no text.
        txs, _, _ = parse(
            row("December 4, 2024", "Reorganization", "A099001",
                "ACME NETWORKS INC COM", "-15", "", "0", "USD", "MGR -")
            + row("December 4, 2024", "Reorganization", "ACMN",
                  "ACME NETWORKS INC COMMON STOCK", "60", "", "0", "USD", "MGR -")
            + row("November 1, 2024", "Buy", "ACMN", "ACME NETWORKS INC COM", "15",
                  "400", "-6009.95", "USD", "ACME NETWORKS UNSOLICITED DA"))
        split = of(txs, action='SPLIT')
        self.assertEqual([(s['symbol'], s['quantity']) for s in split],
                         [('ACMN.US', 4.0)])
        self.assertAlmostEqual(position(txs, 'ACMN.US'), 60.0)

    def test_one_for_one_exchange_receipt_is_not_lost_nor_a_merger(self):
        body = (
            row("October 2, 2024", "Reorganization", "B099002",
                "BLACKSTONE ROCK INC", "-10", "", "0", "USD",
                "MGR - BLACKSTONE ROCK INC TO BLACKSTONE ROCK INC COMMON STOCK 1 FOR 1")
            + row("October 2, 2024", "Reorganization", "BRKX",
                  "BLACKSTONE ROCK INC COMMON STOCK", "10", "", "0", "USD",
                  "MGR - BLACKSTONE ROCK INC COMMON STOCK SHRS RECEIVED THRU MERGER")
            + row("March 1, 2024", "Buy", "BRKX", "BLACKSTONE ROCK INC", "10",
                  "800", "-8009.95", "USD", "BLACKSTONE UNSOLICITED DA"))
        txs, err, par = parse(body)
        self.assertEqual(of(txs, action='SPLIT'), [])
        self.assertAlmostEqual(position(txs, 'BRKX.US'), 10.0)
        self.assertEqual(par._rows_consumed, 3)
        p = _write(HDR + body)
        try:
            self.assertEqual(parse_rbc_corporate_actions(p), [])
        finally:
            os.remove(p)

    def test_mer_roc_consolidation_and_cash_in_lieu(self):
        # 50 old -> 50 x .963957 = 48.19785 new: 48 delivered + CIL for
        # the .19785 fraction; C$6.1585/sh return of capital = 307.93.
        txs, err, _ = parse(
            row("July 12, 2023", "Reorganization", "TRX",
                "TRIX REUTERS CORP COM NO PAR", "", "", "30.00", "CAD",
                "CIL - TRIX REUTERS CORP COM NO PAR CASH IN LIEU OF FRAC SHARES")
            + row("June 28, 2023", "Reorganization", "T099003",
                  "TRIX REUTERS CORP COM NEW", "-50", "", "307.93", "CAD",
                  "MER - TRIX REUTERS CORP COM NEW DEFAULT: ROC OF C$6.1585 + "
                  ".963957 NEW SHS PER 1 OLD")
            + row("June 28, 2023", "Reorganization", "TRX",
                  "TRIX REUTERS CORP COM NO PAR", "48", "", "0", "CAD",
                  "MGR - TRIX REUTERS CORP COM NO PAR SHRS RECEIVED THRU MERGER")
            + row("May 3, 2023", "Buy", "TRX", "TRIX REUTERS CORP COM NEW", "50",
                  "170", "-8509.95", "CAD", "TRIX UNSOLICITED DA"))
        roc = of(txs, action='ADJUST')
        self.assertEqual(len(roc), 1)
        self.assertEqual(roc[0]['symbol'], 'TRX.TO')
        self.assertAlmostEqual(roc[0]['net_amount'], -307.93)
        self.assertEqual(roc[0]['type'], 'roc')
        split = of(txs, action='SPLIT')
        self.assertAlmostEqual(split[0]['quantity'], 0.963957)
        frac = [t for t in of(txs, action='BUYSELL') if t['quantity'] < 0]
        self.assertEqual(len(frac), 1)
        self.assertAlmostEqual(frac[0]['quantity'], -(50 * 0.963957 - 48))
        self.assertAlmostEqual(frac[0]['net_amount'], 30.00)
        self.assertEqual(frac[0]['date'], '2023-07-12')
        self.assertAlmostEqual(position(txs, 'TRX.TO'), 48.0, places=6)

    def test_reverse_entry_cancels(self):
        txs, err, par = parse(
            row("November 20, 2024", "Reorganization", "G099004", "", "-700", "",
                "0", "USD", "REV - GLOBEX DATA CENTER ETF USD AS OF 11/20/24 "
                "REVERSE ENTRY", settle="November 21, 2024")
            + row("November 20, 2024", "Reorganization", "G099004", "", "700", "",
                  "0", "USD", "REV - GLOBEX DATA CENTER ETF USD RESULT OF "
                  "REVERSE SPLIT"))
        self.assertEqual(txs, [])
        self.assertEqual(par.lint_findings, [])
        self.assertIn('nets to zero', err)

    def test_unmatched_legs_warn_and_lint(self):
        txs, err, par = parse(
            row("December 30, 2024", "Reorganization", "BIPX",
                "BRIGHTFIELD INFRA CORP NEW", "200", "", "0", "CAD",
                "MGR - BRIGHTFIELD INFRA CORP NEW SHRS RECEIVED THRU MERGER")
            + row("January 3, 2024", "Reorganization", "Q099005", "QUUX LTD",
                  "-10", "", "0", "CAD", "NAC - QUUX LTD NAME CHANGE TO QUUX CORP"))
        self.assertEqual(txs, [])
        self.assertEqual(err.count('UNMATCHED reorganization leg'), 2)
        self.assertEqual(len(par.lint_findings), 2)

    def test_hess_style_merger_still_goes_to_corp_actions(self):
        txs, err, _ = parse(
            row("2025-07-21 00:00:00", "Reorganization", "CVXX",
                "CHEVRO CORPORATION", "15", "", "0", "USD",
                "MGR - CHEVRO CORPORATION SHRS RECEIVED THRU MERGER")
            + row("2025-07-21 00:00:00", "Reorganization", "H099006",
                  "HESSO CORPORATION", "-15", "", "0", "USD",
                  "MGR - HESSO CORPORATION MERGER TO CHEVRO CORPORATION "
                  "1.025 NEW = 1 OLD"))
        self.assertEqual(txs, [])
        self.assertIn('resolved by taxjson-corp-actions', err)


class TestOptionAdjustments(unittest.TestCase):
    OLD = ("CALL .TUX   03/21/25    64 TUXEDO OIL CORP ADJ: SPCL CASH DIVD "
           "CAD $0.50")

    def test_xch_keeps_the_contract_and_the_later_close_matches(self):
        txs, err, par = parse(
            row("January 31, 2025", "Sell", "8ZZTUX2", "", "-8", "4.3",
                "3423.05", "CAD", self.OLD + " UNSOLICITED CA")
            + row("November 15, 2024", "Reorganization", "8ZZTUX2", "", "8", "",
                  "0", "CAD", "XCH - CALL .TUX   03/21/25    63.50 TUXEDO OIL CORP "
                  "ADJ: SPCL CASH DIVD CAD $0.50 ADJ FOR SPECIAL CASH DIV")
            + row("November 15, 2024", "Reorganization", "8ZZTUX1", "", "-8", "",
                  "0", "CAD", "XCH - CALL .TUX   03/21/25    64 TUXEDO OIL CORP "
                  "ADJ: SPCL CASH DIVD CAD $0.50 ADJ FOR SPECIAL CASH DIV")
            + row("November 1, 2024", "Buy", "8ZZTUX1", "", "8", "4.3",
                  "-3456.95", "CAD", self.OLD + " UNSOLICITED DA"))
        occ = 'TUX250321C00064000.TO'
        self.assertEqual({t['symbol'] for t in txs}, {occ})
        self.assertAlmostEqual(position(txs, occ), 0.0)
        self.assertEqual(of(txs, action='SPLIT'), [])
        self.assertEqual(par.lint_findings, [])
        self.assertIn('same contract', err)

    def test_xch_renames_when_the_new_code_trades_under_new_terms(self):
        txs, err, _ = parse(
            row("January 31, 2025", "Sell", "8ZZTUX2", "", "-8", "4.3",
                "3423.05", "CAD", "CALL .TUX   03/21/25    63.50 TUXEDO OIL CA")
            + row("November 15, 2024", "Reorganization", "8ZZTUX2", "", "8", "",
                  "0", "CAD", "XCH - CALL .TUX   03/21/25    63.50 TUXEDO OIL CORP")
            + row("November 15, 2024", "Reorganization", "8ZZTUX1", "", "-8", "",
                  "0", "CAD", "XCH - CALL .TUX   03/21/25    64 TUXEDO OIL CORP")
            + row("November 1, 2024", "Buy", "8ZZTUX1", "", "8", "4.3",
                  "-3456.95", "CAD", self.OLD + " UNSOLICITED DA"))
        split = of(txs, action='SPLIT')
        self.assertEqual(len(split), 1)
        self.assertEqual(split[0]['symbol'], 'TUX250321C00064000.TO')
        self.assertEqual(split[0]['symbol_new'], 'TUX250321C00063500.TO')
        self.assertAlmostEqual(split[0]['quantity'], 1.0)


# ------------------------------------------------- H2 names are not income

class TestNamesAreNotIncome(unittest.TestCase):
    def test_in_kind_transfer_of_dividend_named_fund(self):
        txs, _, _ = parse(
            row("May 27, 2022", "Transfers", "DVX", "DIVIDEND 15 SPLIT CORP CL-A SHS",
                "2000", "", "0", "CAD", "TFI - DIVIDEND 15 SPLIT CORP CL-A SHS "
                "ACCOUNT TRANSFER BOOK VALUE           16506.95 FROM ACCOUNT"))
        self.assertEqual(of(txs, action='DIVIDEND'), [])
        tr = of(txs, action='TRANSFER')
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]['quantity'], 2000.0)
        self.assertAlmostEqual(tr[0]['book_value'], 16506.95)     # M11
        self.assertEqual(tr[0]['net_amount'], 0.0)                # unchanged

    def test_tender_worded_row_is_a_disposition_not_a_dividend(self):
        # Mutation of a real retraction: RETRACTION -> TENDERED. The old
        # parser booked a 143k DIVIDEND and kept the shares.
        txs, _, _ = parse(
            row("December 15, 2023", "Other", "DVX", "DIVIDEND 15 SPLIT CORP CL-A SHS",
                "-32000", "", "143203.2", "CAD",
                "TEN - DIVIDEND 15 SPLIT CORP CL-A SHS TENDERED AT C$4.4751 PER SHARE"))
        self.assertEqual(of(txs, action='DIVIDEND'), [])
        (t,) = txs
        self.assertEqual((t['action'], t['quantity']), ('BUYSELL', -32000.0))
        self.assertAlmostEqual(t['net_amount'], 143203.2)

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
    def test_reinvestment_is_a_purchase_and_the_distribution_stays(self):
        body = (row("4/18/2022", "Dividends", "SRX.UN", "SMARTX REIT UNIT", "2", "",
                    "-64.48", "CAD", "REI - SMARTX REIT UNIT REINV@C$32.2399 REC "
                    "03/31/22 PAY 04/18/22", settle="18-Apr-22")
                + row("4/18/2022", "Dividends", "SRX.UN", "SMARTX REIT UNIT", "",
                      "0.15", "77.09", "CAD", "SMARTX REIT UNIT DIST      ON     "
                      "500 SHS REC 03/31/22 PAY 04/18/22", settle="18-Apr-22")
                + row("3/14/2022", "Buy", "SRX.UN", "SMARTX REIT UNIT", "500", "30",
                      "-15009.95", "CAD", "SMARTX UNSOLICITED DA", settle="16-Mar-22"))
        txs, _, _ = parse(body)
        rei = [t for t in of(txs, action='BUYSELL') if t['date'] == '2022-04-18']
        self.assertEqual(len(rei), 1)
        self.assertEqual(rei[0]['quantity'], 2.0)
        self.assertAlmostEqual(rei[0]['net_amount'], 64.48)
        self.assertAlmostEqual(rei[0]['price'], 32.2399)
        div = of(txs, action='DIVIDEND')
        self.assertEqual([d['net_amount'] for d in div], [77.09])
        self.assertAlmostEqual(position(txs, 'SRX.UN.TO'), 502.0)

    def test_book_cost_adjustments_are_signed_adjusts(self):
        txs, err, _ = parse(
            row("December 31, 2022", "Return of Capital", "VDX", "VANGX HIGH DIVID ETF",
                "", "", "0", "CAD", "RTC - VANGX HIGH DIVID ETF 2022 RETURN OF "
                "CAPITAL ADJUSTMENT TO BOOK COST $1.16", settle="May 5, 2023")
            + row("December 31, 2022", "Dividends", "VDX", "VANGX HIGH DIVID ETF",
                  "", "", "0", "CAD", "ADJ - VANGX HIGH DIVID ETF 2022 NOTIONAL "
                  "DISTRIBUTION ADJUSTMENT TO BOOK COST $5293.06",
                  settle="May 5, 2023"))
        adj = sorted((t['net_amount'], t['type']) for t in of(txs, action='ADJUST'))
        self.assertEqual(adj, [(-1.16, 'roc'), (5293.06, 'dist')])
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
            '"December 22, 2023","Interest","","","","","December 22, 2023",'
            '"-198.39","USD","INT FR 11/22 THRU12/21@ 9 3/4% BAL   31","498-  '
            'AVBAL   24","756"\n')
        self.assertEqual(txs[0]['description'],
                         'INT FR 11/22 THRU12/21@ 9 3/4% BAL   31,498-  AVBAL   24,756')

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
            row("December 3, 2024", "Buy", "8ZZBNS1", "CALL .BNX   02/21/25    84 "
                "BANK OF NOVA", "25", "0.226", "-606.2", "CAD", ""))
        self.assertEqual(txs[0]['symbol'], 'BNX250221C00084000.TO')
        self.assertAlmostEqual(txs[0]['fee'], 41.2, places=2)

    def test_one_code_two_descriptions_keeps_the_first(self):
        txs, err, _ = parse(
            row("December 29, 2025", "Sell", "8ZZRCI1", "", "-3", "7.25", "2161.3",
                "CAD", "CALL .RCX.B   01/15/27    46 ROGERX CA CLOSE CONTRACT")
            + row("December 23, 2024", "Buy", "8ZZRCI1", "", "3", "3.55", "-1075.7",
                  "CAD", "CALL .RCX   01/15/27    46 ROGERX DA OPEN CONTRACT"))
        self.assertEqual({t['symbol'] for t in txs}, {'RCX270115C00046000.TO'})
        self.assertIn('more than one contract', err)

    def test_emitted_rbc_code_warns(self):
        _, err, _ = parse(row("March 15, 2024", "Buy", "8ZZNONE", "", "1", "1",
                              "-109.95", "USD", "SOMETHING UNSOLICITED DA"))
        self.assertIn('RBC internal code', err)


# --------------------------------------------- M7 accounting, M10 spinoffs

# RBC writes a rights issue's expiry as "EXP <mm/dd/yyyy>" (built here so
# the literal doesn't read as a payment-card expiry to secret scanners).
RTS_EXP = "EXP " + "/".join(("09", "29", "2023"))


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
        p = _write(HDR + row("May 27, 2022", "Transfers", "DVX", "DVX FUND", "10",
                             "", "0", "CAD", "TFI - DVX FUND ACCOUNT TRANSFER BOOK "
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
        body = (row("April 4, 2024", "Reorganization", "GVX", "GENCO VERNA LLC COMMON STOCK",
                    "30", "", "0", "USD", "DIS - GENCO VERNA LLC COMMON STOCK SPINOFF   "
                    "ON     120 SHS FROM SEC# G099007 GENCO AERO REC 04/01/24 PAY 04/02/24")
                + row("February 8, 2024", "Buy", "GNX", "GENCO AERO COMMON STOCK", "120",
                      "100", "-12009.95", "USD", "GENCO UNSOLICITED DA"))
        txs, err, _ = parse(body)
        self.assertEqual(of(txs, symbol='GVX.US'), [])
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
                         ('spinoff', 'GNX.US', 'GVX.US', 30.0, 120.0))

    def test_rights_are_a_noted_nil_cost_acquisition_and_expire(self):
        txs, err, _ = parse(
            row("October 13, 2023", "Reorganization", "C099008", "", "-1", "", "0",
                "CAD", f"EXP - RTS CONSTELLO SOFTWARE INC {RTS_EXP} {RTS_EXP} "
                "AS OF 10/13/23 EXPIRED", settle="October 16, 2023")
            + row("September 8, 2023", "Reorganization", "CSX.RT", "", "1", "", "0",
                  "CAD", f"DIS - RTS CONSTELLO SOFTWARE INC {RTS_EXP} {RTS_EXP} "
                  "RTS DIST  ON       1 SHS REC 09/01/23 PAY 09/08/23"),
            country="canada")
        self.assertEqual({t['symbol'] for t in txs}, {'CSX.RT.TO'})
        self.assertAlmostEqual(position(txs, 'CSX.RT.TO'), 0.0)
        self.assertIn('15(1)(c)', err)


# ------------------------------------------------------ LOW + FEE + misc

class TestLowItems(unittest.TestCase):
    def test_horizons_and_global_x_usd_dlr_line(self):
        txs, _, _ = parse(
            row("March 15, 2024", "Buy", "DLR", "HORIZONS U S DLR CURRENCY ETF UNIT",
                "100", "10", "-1009.95", "USD", "HORIZONS U S DLR CURRENCY ETF DA")
            + row("March 14, 2024", "Buy", "DLR", "GLOBAL X US DLR CURRENCY ETF UNIT CL A",
                  "100", "10", "-1009.95", "USD", "GLOBAL X US DLR CURRENCY ETF DA")
            + row("March 13, 2024", "Sell", "DLR", "GLOBAL X US DLR CURRENCY ETF UNIT CL A",
                  "-100", "13.5", "1340.05", "CAD", "GLOBAL X US DLR CURRENCY ETF CA"))
        self.assertEqual([t['symbol'] for t in txs],
                         ['DLR.U.TO', 'DLR.U.TO', 'DLR.TO'])

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
        # premium fold (seen on real 2025 books: gains moved by ~1,950).
        txs, _, _ = parse(
            row("May 16, 2025", "Other", "9ZZCOI1", "", "1", "", "0", "USD",
                "ASN - CALL COIX   05/16/25   197.50 COIX GLOBAL INC ASSIGNMENT "
                "OF OPTION", settle="May 20, 2025")
            + row("May 16, 2025", "Sell", "COIX", "COIX GLOBAL INC", "-100",
                  "197.5", "19707", "USD", "COIX GLOBAL INC ASSIGNMENT OF OPTION "
                  "AS OF 05/16/25", settle="May 20, 2025")
            + row("May 16, 2025", "Buy", "ZZZ", "ZZZ CORP", "10", "5", "-59.95",
                  "USD", "ZZZ UNSOLICITED DA", settle="May 19, 2025"))
        t_opt = of(txs, action='ASSIGN')[0]['time']
        t_stock = of(txs, symbol='COIX.US')[0]['time']
        self.assertEqual(t_opt, t_stock)

    def test_adr_fee_is_a_fee_row(self):
        txs, _, _ = parse(row("October 8, 2024", "Fees", "SEX", "SEX LTD ADS", "", "",
                              "-8", "USD", "FCH - SEX LTD ADS DTCC ADR FEE 0.02"))
        self.assertEqual([(t['action'], t['symbol'], t['net_amount']) for t in txs],
                         [('FEE', 'SEX.US', 8.0)])


class TestDuplicateSplitWarning(unittest.TestCase):
    def test_parser_split_plus_manual_line_warns(self):
        from taxjson.bin.taxjson_merge2 import warn_duplicate_splits
        from taxjson.lib.core import TaxTransaction
        a = TaxTransaction(action='SPLIT', date='2023-12-20', symbol='CANN.TO',
                           quantity=0.1, account='margin', time='09:30:11',
                           description='RBC REV reorganization')
        b = TaxTransaction(action='SPLIT', date='2023-12-20', symbol='CANN.TO',
                           symbol_new='CANN.TO', quantity=0.1, account='margin')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(warn_duplicate_splits([a, b]), 1)
        self.assertIn('duplicate split', err.getvalue())
        c = TaxTransaction(action='SPLIT', date='2023-12-20', symbol='CANN.TO',
                           quantity=0.2, account='margin')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            warn_duplicate_splits([a, c])
        self.assertIn('conflicting splits', err.getvalue())


class TestPairingApi(unittest.TestCase):
    def test_every_leg_is_accounted_for(self):
        p = _write(HDR
                   + row("June 24, 2024", "Reorganization", "9ZZMRA1", "", "58", "", "0",
                         "USD", "XCH - CALL MARX   01/17/25    24 MARX DIGITAL "
                         "SECURITY CODE ADJUSTMENT")
                   + row("June 24, 2024", "Reorganization", "8ZZMRA1", "", "-58", "",
                         "0", "USD", "XCH - CALL MARX   01/17/25    24 MARX DIGITAL "
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
