"""RBC Direct: identity and de-duplication ACROSS the export files of one
account (2026-09 audit findings R1-6, R1-81, S015-07, R1-79, S016-06,
R1-80, S015-02, S016-09).

`taxjson run` hands every RBC export of an account to ONE
taxjson-brokerage call; the parser used to learn each identity map
(market currency per symbol, option code -> contract, security name ->
ticker) per FILE, so the answer depended on how the rows were split
across yearly downloads. Every fixture is synthetic (fake account id
55500001 / 55500002, made-up codes).
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

from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from tax_rules import rule

REPO = Path(__file__).resolve().parent.parent
ACCT = "55500001"  # pii-ok (synthetic)
ACCT2 = "55500002"  # pii-ok (synthetic)
HDR = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
       '"Settlement Date","Account","Value","Currency","Description"\n')
HDR_NOACCT = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
              '"Price","Settlement Date","Value","Currency","Description"\n')


def row(date, activity, symbol, symdesc, qty, price, value, cur, desc,
        settle=None, acct=ACCT):
    cells = [date, activity, symbol, symdesc, qty, price, settle or date]
    if acct is not None:
        cells.append(acct)
    cells += [value, cur, desc]
    return ','.join('"%s"' % c for c in cells) + '\n'


def parse_files(files, header=HDR):
    """Parse {name: body} as ONE account (the way taxjson-brokerage does:
    shared account context, one extractor per file, files in name order).
    Returns (transactions, stderr, [parsers])."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, body in sorted(files.items()):
            p = Path(d) / name
            p.write_text(header + body, encoding='utf-8')
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


def parse_one(body, header=HDR):
    """A single file, no shared context (the old entry point)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'rbc.csv'
        p.write_text(header + body, encoding='utf-8')
        err = io.StringIO()
        par = RbcBrokerage()
        with contextlib.redirect_stderr(err):
            txs = par.parse_file(p)
    return txs, err.getvalue(), par


def cli(files):
    """taxjson-brokerage --brokerage rbc over {name: body}: (rc, txs, err)."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, body in sorted(files.items()):
            p = Path(d) / name
            p.write_text(HDR + body, encoding='utf-8')
            paths.append(str(p))
        r = subprocess.run(
            [sys.executable, '-m', 'taxjson.bin.taxjson_brokerage',
             '--brokerage', 'rbc', '--account', 'margin'] + paths,
            capture_output=True, text=True,
            env={**os.environ, 'PYTHONPATH': str(REPO / 'src')})
    txs = json.loads(r.stdout)['transactions'] if r.returncode == 0 else []
    return r.returncode, txs, r.stderr


def of(txs, **kw):
    return [t for t in txs if all(t.get(k) == v for k, v in kw.items())]


def position(txs, symbol):
    q = 0.0
    for t in sorted(txs, key=lambda t: (t['date'], t['time'])):
        if t['action'] in ('BUYSELL', 'ASSIGN') and t['symbol'] == symbol:
            q += t['quantity']
        elif t['action'] == 'SPLIT' and t['symbol'] == symbol:
            q *= t['quantity']
    return q


def positions(txs):
    """{symbol: qty} after replaying BUYSELL and SPLIT (with renames)."""
    pos = {}
    for t in sorted(txs, key=lambda t: (t['date'], t['time'])):
        s = t['symbol']
        if t['action'] in ('BUYSELL', 'ASSIGN'):
            pos[s] = pos.get(s, 0.0) + t['quantity']
        elif t['action'] == 'SPLIT' and s in pos:
            q = pos.pop(s) * t['quantity']
            new = t.get('symbol_new') or s
            pos[new] = pos.get(new, 0.0) + q
    return {s: q for s, q in pos.items() if abs(q) > 1e-9}


# ------------------------------------------ R1-6 / R1-81: bare-ticker clash

# 'ZZQ' is both a US stock (USD) and a Canadian ETF (CAD) at RBC; the ETF
# is bought AFTER the US stock's dividend, and RBC lists newest first.
ZZQ_ROWS = [
    row("December 24, 2024", "Buy", "ZZQ", "ZEEQUE CANADIAN BANK ETF", "100",
        "20", "-2009.95", "CAD", "ZEEQUE CANADIAN BANK ETF UNSOLICITED DA"),
    row("November 12, 2024", "Sell", "ZZQ", "ZEEQUE HEALTHCARE INC COMMON STOCK",
        "-10", "350", "3490.05", "USD",
        "ZEEQUE HEALTHCARE INC COMMON STOCK UNSOLICITED CA"),
    row("September 30, 2024", "Dividends", "ZZQ",
        "ZEEQUE HEALTHCARE INC COMMON STOCK", "", "", "17.00", "USD",
        "DIV - ZEEQUE HEALTHCARE INC COMMON STOCK CASH DIV ON 10 SHS REC "
        "09/13/24 PAY 09/30/24 NON-RES TAX WITHHELD"),
    row("September 3, 2024", "Buy", "ZZQ", "ZEEQUE HEALTHCARE INC COMMON STOCK",
        "10", "300", "-3009.95", "USD",
        "ZEEQUE HEALTHCARE INC COMMON STOCK UNSOLICITED DA"),
]


@rule("CA-ACB-04")
class TestIncomeListingWithSharedBareTicker(unittest.TestCase):
    def _check(self, txs, err):
        div = of(txs, action='DIVIDEND')
        tax = of(txs, action='TAX')
        self.assertEqual([t['symbol'] for t in div], ['ZZQ.US'], err)
        self.assertEqual([t['symbol'] for t in tax], ['ZZQ.US'], err)
        self.assertEqual(div[0]['currency'], 'USD')

    def test_newest_first_export(self):
        txs, err, _ = parse_one(''.join(ZZQ_ROWS))
        self._check(txs, err)

    def test_oldest_first_export(self):
        txs, err, _ = parse_one(''.join(reversed(ZZQ_ROWS)))
        self._check(txs, err)

    def test_cad_distribution_of_the_etf_stays_on_the_tsx_listing(self):
        body = (row("January 31, 2025", "Dividends", "ZZQ",
                    "ZEEQUE CANADIAN BANK ETF", "", "", "8.00", "CAD",
                    "DIST - ZEEQUE CANADIAN BANK ETF DIST ON 100 SHS REC "
                    "01/24/25 PAY 01/31/25")
                + ''.join(ZZQ_ROWS))
        txs, err, _ = parse_one(body)
        self.assertEqual({t['date']: t['symbol']
                          for t in of(txs, action='DIVIDEND')},
                         {'2024-09-30': 'ZZQ.US', '2025-01-31': 'ZZQ.TO'})

    def test_split_across_files(self):
        txs, err, _ = parse_files({'rbc_2024.csv': ''.join(ZZQ_ROWS[1:]),
                                   'rbc_2025.csv': ZZQ_ROWS[0]})
        self._check(txs, err)


# -------------------------------------- S015-07: market currency per ACCOUNT

GLDX_BUY = row("March 4, 2024", "Buy", "GLDX", "GOLDX MINING CORP", "1000",
               "10.00", "-10009.95", "CAD", "GOLDX MINING CORP UNSOLICITED DA")
GLDX_DIV = row("June 20, 2025", "Dividends", "GLDX", "GOLDX MINING CORP", "",
               "", "400.00", "USD", "DIV - GOLDX MINING CORP CASH DIV ON 1000 "
               "SHS REC 06/05/25 PAY 06/20/25")
GLDX_ROC = row("July 15, 2025", "Dividends", "GLDX", "GOLDX MINING CORP", "",
               "", "2000.00", "USD", "DIV - GOLDX MINING CORP RETURN OF "
               "CAPITAL ON 1000 SHS REC 07/01/25 PAY 07/15/25")


@rule("CA-ACB-04")
class TestMarketCurrencyAcrossFiles(unittest.TestCase):
    def test_usd_income_of_a_tsx_stock_in_a_no_trade_year(self):
        split, err, _ = parse_files({'rbc_2024.csv': GLDX_BUY,
                                     'rbc_2025.csv': GLDX_ROC + GLDX_DIV})
        merged, _, _ = parse_one(GLDX_ROC + GLDX_DIV + GLDX_BUY)
        self.assertEqual({t['symbol'] for t in split}, {'GLDX.TO'}, err)
        self.assertEqual(sorted((t['action'], t['symbol']) for t in split),
                         sorted((t['action'], t['symbol']) for t in merged))

    def test_cli_passes_the_account_context(self):
        rc, txs, err = cli({'rbc_2024.csv': GLDX_BUY,
                            'rbc_2025.csv': GLDX_ROC + GLDX_DIV})
        self.assertEqual(rc, 0, err)
        self.assertEqual({t['symbol'] for t in txs}, {'GLDX.TO'}, err)

    def test_untraded_symbol_usd_roc_is_said_out_loud(self):
        # Only the income year is in the project (the position came in
        # through a hand-written .tt): the parser cannot know the
        # listing, keeps the payment currency's, and says so.
        txs, err, _ = parse_files({'rbc_2025.csv': GLDX_ROC + GLDX_DIV})
        self.assertEqual({t['symbol'] for t in txs}, {'GLDX.US'})
        self.assertIn('GLDX', err)
        self.assertIn('no trade rows', err)
        # TOBASE, not GLOBAL: a GLOBAL rename puts the USD ROC on the CAD
        # pool unconverted and the run stops (re-audit A2-0005); and it
        # is an ATTENTION line, on the run console.
        self.assertIn('TOBASE GLDX.US GLDX.TO', err)
        self.assertIn('warning: ATTENTION:', err)


# ------------------------------- R1-79 / S016-06: option identity per account

TRP_BUY = row("March 12, 2024", "Buy", "8ZZTRP1", "", "5", "1.80", "-938.20",
              "CAD", "CALL .TRX   01/16/26    55 TRX ENERGY INC DA OPEN CONTRACT")
TRP_XCH_OUT = row("October 2, 2024", "Reorganization", "8ZZTRP1", "", "-5", "",
                  "0", "CAD", "XCH - CALL .TRX   01/16/26    55 TRX ENERGY INC")
TRP_XCH_IN = row("October 2, 2024", "Reorganization", "8ZZTRP2", "", "5", "",
                 "0", "CAD", "XCH - CALL .TRX1   01/16/26    55 TRX ENERGY INC")
TRP_SELL = row("June 10, 2025", "Sell", "8ZZTRP2", "", "-5", "17.00", "8486.80",
               "CAD", "CALL .TRX1   01/16/26    55 TRX ENERGY INC CA CLOSE "
               "CONTRACT")

RCI_BUY = row("December 16, 2024", "Buy", "8ZZRCI1", "", "3", "3.20",
              "-970.70", "CAD",
              "CALL .RCX   01/15/27    46 ROGERX COMMUNICATIONS INC DA OPEN "
              "CONTRACT")
RCI_SELL = row("December 15, 2025", "Sell", "8ZZRCI1", "", "-3", "5.40",
               "1609.30", "CAD",
               "CALL .RCX.B   01/15/27    46 ROGERX COMMUNICATIONS INC CA "
               "CLOSE CONTRACT")


@rule("CA-ACB-RENAME")
class TestOptionIdentityAcrossFiles(unittest.TestCase):
    def test_xch_then_close_in_the_next_export(self):
        split, err, _ = parse_files({'rbc_2024.csv': TRP_XCH_IN + TRP_XCH_OUT
                                     + TRP_BUY,
                                     'rbc_2025.csv': TRP_SELL})
        one, _, _ = parse_one(TRP_SELL + TRP_XCH_IN + TRP_XCH_OUT + TRP_BUY)
        self.assertEqual(positions(split), {}, err)
        self.assertEqual(positions(one), {})
        self.assertEqual(of(split, action='SPLIT')[0]['symbol_new'],
                         'TRX1260116C00055000.TO')

    def test_xch_alone_names_the_ticker_map_line(self):
        # The project holds only the XCH year; next year's close lives in
        # another project (opened there through a .tt).
        _, err, _ = parse_one(TRP_XCH_IN + TRP_XCH_OUT + TRP_BUY)
        self.assertIn('GLOBAL TRX1260116C00055000.TO TRX260116C00055000.TO',
                      err)

    def test_same_code_redescribed_in_the_next_export(self):
        split, err, _ = parse_files({'rbc_2024.csv': RCI_BUY,
                                     'rbc_2025.csv': RCI_SELL})
        self.assertEqual({t['symbol'] for t in split},
                         {'RCX270115C00046000.TO'}, err)
        self.assertEqual(positions(split), {})
        self.assertIn('more than one contract', err)
        self.assertIn('rbc_2025.csv', err)

    def test_cli_same_code_redescribed(self):
        rc, txs, err = cli({'rbc_2024.csv': RCI_BUY, 'rbc_2025.csv': RCI_SELL})
        self.assertEqual(rc, 0, err)
        self.assertEqual({t['symbol'] for t in txs}, {'RCX270115C00046000.TO'})


# ----------------------------------- R1-80: ticker change without a reorg row

OWL = "BLUE OWLX CAPITAL CORPORATION COMMON STOCK"
ORCX_ROWS = [
    row("August 23, 2023", "Sell", "OBDX", OWL, "-1568", "15", "23500.05",
        "USD", "BLUE OWLX CAPITAL CORPORATION UNSOLICITED CA"),
    row("May 22, 2023", "Buy", "ORCX", OWL, "668", "13", "-8693.95", "USD",
        "OWLX ROCK CAPITAL CORPORATION UNSOLICITED DA"),
    row("April 3, 2023", "Buy", "ORCX", OWL, "900", "13", "-11709.95", "USD",
        "OWLX ROCK CAPITAL CORPORATION UNSOLICITED DA"),
]


@rule("CA-ACB-RENAME")
class TestTickerChangeWithoutReorganization(unittest.TestCase):
    def test_one_file_warns_with_the_ticker_map_line(self):
        _, err, _ = parse_one(''.join(ORCX_ROWS))
        self.assertIn('GLOBAL ORCX.US OBDX.US', err)
        self.assertIn('warning', err)

    def test_across_files(self):
        _, err, _ = parse_files({'rbc_a.csv': ''.join(ORCX_ROWS[1:]),
                                 'rbc_b.csv': ORCX_ROWS[0]})
        self.assertIn('GLOBAL ORCX.US OBDX.US', err)

    def test_second_symbol_opening_with_a_buy_is_not_flagged(self):
        body = (row("August 23, 2023", "Buy", "OBDX", OWL, "10", "15",
                    "-150.05", "USD", "BLUE OWLX UNSOLICITED DA")
                + ''.join(ORCX_ROWS[1:]))
        _, err, _ = parse_one(body)
        self.assertNotIn('GLOBAL ORCX.US OBDX.US', err)


# ------------------------------- S015-02: temporary code named in no row

ABC_BUY = row("March 3, 2025", "Buy", "ABQ", "ABQCORP INC", "100", "10",
              "-1000", "CAD", "ABQCORP INC UNSOLICITED")
ABC_REM = row("April 18, 2026", "Reorganization", "A012345", "ABQCORP INC",
              "-100", "", "0", "CAD", "NAC - ABQCORP INC NAME CHANGE TO NEWQO INC")
ABC_REC = row("April 18, 2026", "Reorganization", "NEWQ", "NEWQO INC COM", "100",
              "", "0", "CAD", "NAC - NEWQO INC COM RESULT OF NAME CHANGE")
ABC_SELL = row("August 3, 2026", "Sell", "NEWQ", "NEWQO INC COM", "-100", "12",
               "1200", "CAD", "NEWQO INC COM UNSOLICITED")


@rule("CA-ACB-RENAME")
class TestTemporaryCodeAcrossFiles(unittest.TestCase):
    def test_resolved_by_name_in_the_earlier_export(self):
        split, err, _ = parse_files({'rbc_2025.csv': ABC_BUY,
                                     'rbc_2026.csv': ABC_SELL + ABC_REC
                                     + ABC_REM})
        self.assertEqual(positions(split), {}, err)
        sp = of(split, action='SPLIT')
        self.assertEqual((sp[0]['symbol'], sp[0]['symbol_new']),
                         ('ABQ.TO', 'NEWQ.TO'))

    def test_unresolvable_code_is_a_loud_warning(self):
        txs, err, pars = parse_files({'rbc_2026.csv': ABC_SELL + ABC_REC
                                      + ABC_REM})
        self.assertIn('warning', err)
        self.assertIn('A012345', err)
        self.assertIn('ABQCORP', err)
        self.assertIn('GLOBAL', err)
        self.assertIn('NEWQ.TO', err)
        self.assertTrue(any('A012345' in f for f in pars[0].lint_findings))

    def test_same_company_reverse_split_under_temp_code_stays_quiet(self):
        body = (
            row("November 20, 2024", "Reorganization", "ETQ",
                "GRAYSCALE ETHEREQ MINI TR ETF", "700", "", "0", "USD",
                "REV - GRAYSCALE ETHEREQ MINI TR ETF AS OF 11/20/24")
            + row("November 20, 2024", "Reorganization", "G012345", "", "-7000",
                  "", "0", "USD", "REV - GRAYSCALE ETHEREQ MINI TR ETF SHARES "
                  "REV SPLIT TO GRAYSCALE ETHEREQ MINI TR ETF; 1 FOR 10"))
        txs, err, par = parse_one(body)
        self.assertNotIn('warning', err)
        self.assertEqual(par.lint_findings, [])
        self.assertEqual(of(txs, action='SPLIT')[0]['symbol'], 'ETQ.US')


# ------------------------------------ S016-09: overlapping re-downloads

RY_SELL = row("May 12, 2025", "Sell", "RYX", "ROYAL BANK X", "-50", "120.00",
              "5990.05", "CAD", "ROYAL BANK X")
BNS_BUY = row("May 12, 2025", "Buy", "BNX", "BANK OF NOVA X", "100", "70.00",
              "-7009.95", "CAD", "BANK OF NOVA X")
TD_SELL = row("May 12, 2025", "Sell", "TDX", "TORONTO-DOMINION X", "-40",
              "80.00", "3190.05", "CAD", "TORONTO-DOMINION X")
RY_BUY = row("March 3, 2025", "Buy", "RYX", "ROYAL BANK X", "100", "100.00",
             "-10009.95", "CAD", "ROYAL BANK X")
TD_BUY = row("March 3, 2025", "Buy", "TDX", "TORONTO-DOMINION X", "100",
             "75.00", "-7509.95", "CAD", "TORONTO-DOMINION X")


def _trades(txs):
    return sorted((t['date'], t['symbol'], t['quantity'])
                  for t in txs if t['action'] == 'BUYSELL')


class TestFormatErrorsAreParseErrors(unittest.TestCase):
    def test_rbc_format_error_is_a_brokerage_parse_error(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.brokerages.rbc_direct import RbcFormatError
        self.assertTrue(issubclass(RbcFormatError, BrokerageParseError))


class TestOverlappingDownloads(unittest.TestCase):
    ONE = [RY_SELL, BNS_BUY, TD_SELL, RY_BUY, TD_BUY]

    def test_reordered_same_day_rows_dedupe(self):
        a = ''.join(self.ONE)
        b = TD_SELL + RY_SELL + BNS_BUY + RY_BUY + TD_BUY
        txs, err, _ = parse_files({'rbc_a.csv': a, 'rbc_b.csv': b})
        single, _, _ = parse_one(a)
        self.assertEqual(_trades(txs), _trades(single), err)
        self.assertIn('overlap', err)

    def test_cli_output_has_no_duplicates(self):
        a = ''.join(self.ONE)
        b = TD_SELL + RY_SELL + BNS_BUY + RY_BUY + TD_BUY
        rc, txs, err = cli({'rbc_a.csv': a, 'rbc_b.csv': b})
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(txs), 5, err)
        self.assertEqual(len({t['id'] for t in txs}), 5)

    def test_identical_trades_on_one_day_are_kept_per_count(self):
        twice = RY_BUY + RY_BUY
        txs, err, _ = parse_files({'rbc_a.csv': twice, 'rbc_b.csv': twice})
        self.assertEqual(position(txs, 'RYX.TO'), 200.0, err)
        # The later download carries a third identical fill: keep it.
        txs, err, _ = parse_files({'rbc_a.csv': twice,
                                   'rbc_b.csv': twice + RY_BUY})
        self.assertEqual(position(txs, 'RYX.TO'), 300.0, err)
        rc, out, err = cli({'rbc_a.csv': twice, 'rbc_b.csv': twice + RY_BUY})
        self.assertEqual(rc, 0, err)
        self.assertEqual(len({t['id'] for t in out}), 3)

    def test_other_account_is_never_deduped(self):
        other = RY_BUY.replace(ACCT, ACCT2)
        txs, err, _ = parse_files({'rbc_a.csv': RY_BUY, 'rbc_b.csv': other})
        self.assertEqual(position(txs, 'RYX.TO'), 200.0, err)

    def test_no_account_column_is_not_guessed(self):
        body = ''.join(row(*a) for a in [
            ("March 3, 2025", "Buy", "RYX", "ROYAL BANK X", "100", "100.00",
             "-10009.95", "CAD", "ROYAL BANK X", None, None)])
        txs, err, _ = parse_files({'rbc_a.csv': body, 'rbc_b.csv': body},
                                  header=HDR_NOACCT)
        self.assertEqual(position(txs, 'RYX.TO'), 200.0)
        self.assertIn('Account', err)
        # A note now: the run's cross-file dedup decides and says so
        # (re-audit A2-1051 — the warning contradicted its line).
        self.assertIn('de-duplication decides', err)


if __name__ == '__main__':
    unittest.main()
