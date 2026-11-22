"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R
import _paths


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

REPO_ROOT = _paths.REPO


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestQuestradeDividendSignPreserved(unittest.TestCase):
    """Audit 2026-08 #3: `_parse_dividend` abs()'d Net Amount, so
    negative Dividends-activity rows — withholding debits, dividend
    reversals, ROC reversals — were booked as positive income (and a
    ROC reversal as a SECOND ACB reduction instead of a restoration).

    Formerly: test_audit_2026_08_fixes.TestQuestradeDividendSignPreserved"""

    HEADER = (
        'Transaction Date,Settlement Date,Action,Symbol,Description,'
        'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
        'Account #,Activity Type,Account Type\n'
    )

    def _row(self, desc, net):
        return (f'2025-06-02 12:00:00 AM,2025-06-02 12:00:00 AM,DIV,AAPL,'
                f'{desc},0,0.00,0.00,0.00,{net},USD,12345,Dividends,'
                f'Individual\n')

    def _parse(self, csv_body):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        return _parse_csv(QuestradeBrokerage, self.HEADER + csv_body)

    def test_reversal_and_withholding_net_out(self):
        txs = self._parse(
            self._row('APPLE INC CASH DIV ON 100 SHS', '18.00') +
            self._row('APPLE INC NON-RES TAX WITHHELD ON 100 SHS', '-2.70') +
            self._row('APPLE INC CASH DIV REVERSAL ON 100 SHS', '-18.00'))
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 3)
        amounts = sorted(t['net_amount'] for t in divs)
        self.assertEqual(amounts, [-18.0, -2.7, 18.0])
        self.assertAlmostEqual(sum(amounts), -2.7, places=2,
                               msg="abs() turned debits into income: the "
                                   "buggy sum was +38.70")

    def test_roc_reversal_restores_acb(self):
        txs = self._parse(
            self._row('XYZ FUND RETURN OF CAPITAL ON 100 SHS', '50.00') +
            self._row('XYZ FUND RETURN OF CAPITAL REVERSAL ON 100 SHS',
                      '-50.00'))
        adjusts = [t for t in txs if t['action'] == 'ADJUST']
        self.assertEqual(len(adjusts), 2)
        # tx_roc_adjust: net_amount = -amount. Posting reduces ACB
        # (negative ADJUST); the reversal must RESTORE it (positive).
        self.assertEqual(sorted(t['net_amount'] for t in adjusts),
                         [-50.0, 50.0])


class TestRbcSignPreserved(unittest.TestCase):
    """Audit 2026-08 #4: RBC `_build_dividend` abs()'d the Amount so a
    dividend reversal booked as MORE income, and `_build_tax` abs()'d
    so a withholding refund counted as more tax withheld. The IB parser
    was deliberately fixed for both (sign-flip, not abs); RBC now
    mirrors it.

    Formerly: test_audit_2026_08_fixes.TestRbcSignPreserved"""

    HEADER = ('Date,Activity,Symbol,Description,Quantity,Price,'
              'Settlement Date,Currency,Value,Amount\n')

    def _parse(self, rows):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        return _parse_csv(RbcBrokerage, self.HEADER + rows)

    def test_dividend_reversal_nets_to_zero(self):
        txs = self._parse(
            '03/13/2024,Dividends,RY,ROYAL BANK CASH DIV ON 100 SHS,'
            '0,0.00,03/13/2024,CAD,0.00,138.00\n'
            '03/15/2024,Dividends,RY,ROYAL BANK CASH DIV REVERSAL ON 100 '
            'SHS,0,0.00,03/15/2024,CAD,0.00,-138.00\n')
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 2)
        self.assertEqual(sorted(t['net_amount'] for t in divs),
                         [-138.0, 138.0])
        self.assertAlmostEqual(sum(t['net_amount'] for t in divs), 0.0,
                               places=2,
                               msg="abs() reported $276 income for a fully "
                                   "reversed dividend")

    @rule(R.CA_INC_05)
    @rule(R.US_INC_03)
    def test_withholding_charge_positive_refund_negative(self):
        txs = self._parse(
            # Charge: cash out, negative Amount → +4.05 tax withheld.
            '04/15/2024,Taxes,AAPL,NON-RESIDENT TAX PAID,'
            '0,0.00,04/15/2024,USD,0.00,-4.05\n'
            # Refund: cash in, positive Amount → NETS NEGATIVE.
            '05/15/2024,Taxes,AAPL,NON-RESIDENT TAX ADJUSTMENT REFUND,'
            '0,0.00,05/15/2024,USD,0.00,4.05\n')
        taxes = [t for t in txs if t['action'] == 'TAX']
        self.assertEqual(len(taxes), 2)
        self.assertEqual(sorted(t['net_amount'] for t in taxes),
                         [-4.05, 4.05])
        self.assertAlmostEqual(sum(t['net_amount'] for t in taxes), 0.0,
                               places=2)

    def test_net_withholding_reversal_scales_gross_up(self):
        """A reversed net-of-withholding dividend must reverse both the
        grossed-up DIVIDEND and its implied TAX row."""
        row = ('06/14/2024,Dividends,MSFT,MICROSOFT NON-RES TAX WITHHELD '
               'CASH DIV ON 100 SHS,0,0.00,06/14/2024,USD,0.00,{amt}\n')
        txs = self._parse(row.format(amt='85.00') +
                          row.format(amt='-85.00'))
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        taxes = [t for t in txs if t['action'] == 'TAX']
        self.assertAlmostEqual(sum(t['net_amount'] for t in divs), 0.0,
                               places=2)
        self.assertAlmostEqual(sum(t['net_amount'] for t in taxes), 0.0,
                               places=2)
        self.assertAlmostEqual(sum(t['gross_amount'] for t in divs), 0.0,
                               places=2)


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

_CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
              "Price Currency,Price at Transaction,Subtotal,"
              "Total (inclusive of Fees and/or Spread),"
              "Fees and/or Spread,Notes\n")


_CB_OLD_HEADER = ("Timestamp,Transaction Type,Asset,Quantity Transacted,"
                  "Spot Price Currency,Spot Price at Transaction,Subtotal,"
                  "Total (inclusive of fees),Notes\n")


class TestCoinbaseHeaderVariantsAndDedup(unittest.TestCase):
    """Audit 2026-08 #7: `_col` silently returned '' for missing
    columns, so the older retail layout (Spot Price ...) parsed with
    price=0.0/USD — and `taxjson-fill-crypto` then overwrote the
    broker's exact totals with daily-close estimates. Old exports also
    lack an ID column, and with no `disambiguate_split_fills` guard,
    byte-identical fills were dedup-deleted.

    Formerly: test_audit_2026_08_fixes.TestCoinbaseHeaderVariantsAndDedup"""

    def _parse(self, content):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        return _parse_csv(CoinbaseBrokerage, content)

    def test_old_spot_price_layout_parses_price_and_currency(self):
        txs = self._parse(
            _CB_OLD_HEADER +
            "2021-03-05 09:48:33 UTC,Buy,BTC,0.01000000,CAD,9149.00,"
            "91.49,92.99,Bought BTC\n")
        self.assertEqual(len(txs), 1)
        t = txs[0]
        self.assertAlmostEqual(t['price'], 9149.00, places=2)
        self.assertEqual(t['currency'], 'CAD')
        self.assertAlmostEqual(
            t['net_amount'], 92.99, places=2,
            msg="price=0 rows get their exact broker total replaced by a "
                "fill-crypto estimate downstream")

    def test_unknown_layout_fails_loudly(self):
        bad = ("Timestamp,Transaction Type,Asset,Quantity Transacted,"
               "Mystery Column\n"
               "2021-03-05 09:48:33 UTC,Buy,BTC,0.01,42\n")
        with self.assertRaises(ValueError) as cm:
            self._parse(bad)
        self.assertIn("unrecognized export layout", str(cm.exception))

    def test_identical_fills_survive_without_id_column(self):
        txs = self._parse(
            _CB_OLD_HEADER +
            "2021-03-05 09:48:33 UTC,Buy,BTC,0.01000000,USD,50000.00,"
            "500.00,505.00,Bought BTC\n"
            "2021-03-05 09:48:33 UTC,Buy,BTC,0.01000000,USD,50000.00,"
            "500.00,505.00,Bought BTC\n")
        self.assertEqual(len(txs), 2)
        descs = [t.get('description', '') for t in txs]
        self.assertNotEqual(
            descs[0], descs[1],
            msg="byte-identical fills must be disambiguated or "
                "taxjson-sort --dedup deletes half the cost basis")

    def test_current_layout_with_id_still_parses(self):
        txs = self._parse(
            _CB_HEADER +
            "CB-X-1,2024-01-18 16:24:11 UTC,Buy,BTC,0.05000000,USD,"
            "43000.00,2150.00,2167.20,17.20,Bought BTC\n")
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['id'], 'CB-X-1')
        self.assertAlmostEqual(txs[0]['price'], 43000.00, places=2)


class TestLowSeverityBatch(unittest.TestCase):
    """Audit 2026-08 low-severity fixes (#16-#24), one test each.

    Formerly: test_audit_2026_08_fixes.TestLowSeverityBatch"""

    def setUp(self):
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))

    def test_export_filter_treats_venture_listings_as_cad(self):
        import argparse
        from taxjson.bin.taxjson_export import _passes_filters
        args = argparse.Namespace(
            long=False, short=False, no_options=False, no_futures=False,
            no_equities=False, no_cad=True, no_usd=False)
        for sym in ("ABC.V", "DEF.CN", "GHI.NE", "XEI.TO"):
            self.assertFalse(
                _passes_filters({"symbol": sym, "qty": 10}, args),
                f"{sym} is a CAD listing and must be dropped by --no-cad")
        self.assertTrue(
            _passes_filters({"symbol": "AAPL.US", "qty": 10}, args))

    def test_questrade_option_gross_amount_has_multiplier(self):
        from _support.parser_csv import _parse_csv, QUESTRADE_HEADER
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        csv = QUESTRADE_HEADER + (
            '2025-04-10 09:30:00 AM,2025-04-11 12:00:00 AM,Sell,AAPL.OPT,'
            'CALL AAPL 06/20/25 150.00,2,1.50,300.00,2.50,297.50,USD,'
            '12345,Trades,Individual\n')
        t = _parse_csv(QuestradeBrokerage, csv)[0]
        self.assertAlmostEqual(
            t['gross_amount'], 300.0, places=2,
            msg="2 contracts @ 1.50 is a $300 notional (×100), not $3")

    def test_kraken_unknown_trade_type_counted_not_silent(self):
        import io
        from contextlib import redirect_stderr
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        csv = ('"txid","ordertxid","pair","time","type","ordertype",'
               '"price","cost","fee","vol","margin","misc","ledgers"\n'
               '"T1","O1","XBT/USD","2024-02-10 14:25:18","buy","limit",'
               '"42000.00","4200.00","8.40","0.10","0.00","",""\n'
               '"T2","O2","XBT/USD","2024-02-11 14:25:18","liquidation",'
               '"limit","41000.00","4100.00","8.20","0.10","0.00","",""\n')
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = _parse_csv(KrakenBrokerage, csv)
        self.assertEqual(len(txs), 1)
        self.assertIn("liquidation", buf.getvalue(),
                      "unhandled trade types must be summarized, not "
                      "silently continued past")

    def test_kraken_bad_timestamp_raises(self):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        csv = ('"txid","ordertxid","pair","time","type","ordertype",'
               '"price","cost","fee","vol","margin","misc","ledgers"\n'
               '"T1","O1","XBT/USD","2024-02-10T14:25:18Z","buy","limit",'
               '"42000.00","4200.00","8.40","0.10","0.00","",""\n')
        with self.assertRaises(ValueError) as cm:
            _parse_csv(KrakenBrokerage, csv)
        self.assertIn("unparseable", str(cm.exception))

    def test_stdin_loader_applies_fuzz_k_guards(self):
        import io
        import json as _json
        from taxjson.lib.pipeline import load_stdin_transactions
        base = {"action": "BUYSELL", "date": "2025-01-05",
                "symbol": "A.TO", "quantity": 1.0, "price": 1.0,
                "net_amount": 1.0, "currency": "CAD", "account": "m"}
        # Non-dict row: silently dropped before, now refused.
        with self.assertRaises(ValueError):
            load_stdin_transactions(io.StringIO(
                _json.dumps({"transactions": [base, "corrupted"]})))
        # NaN: reached the engine as decimal.InvalidOperation before.
        with self.assertRaises(ValueError) as cm:
            load_stdin_transactions(io.StringIO(
                '{"transactions": [{"action": "BUYSELL", '
                '"date": "2025-01-05", "symbol": "A.TO", '
                '"quantity": NaN, "price": 1.0, "net_amount": 1.0, '
                '"currency": "CAD", "account": "m"}]}'))
        self.assertIn("non-finite", str(cm.exception))

    def test_diagnostics_banner_skips_sibling_prefix_accounts(self):
        from taxjson.pipeline.diagnostics import collect_diagnostics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.margin_us]\ntype = "taxable"\n')
            cache = root / "work"
            cache.mkdir()
            (cache / "margin_validate.diag").write_text(
                "error: margin problem\n")
            (cache / "margin_us_validate.diag").write_text(
                "error: margin_us oversold position\n")
            diag = collect_diagnostics(cache, "margin")
        self.assertIn("margin problem", diag)
        self.assertNotIn(
            "oversold", diag,
            "account margin's banner absorbed margin_us's diagnostics")

    def test_zero_basis_walk_dedupes_broker_split_copies(self):
        from taxjson.lib.missing_history import (
            detect_zero_basis_acquisitions)
        txs = [
            # $0-cost acquisition (unhandled corp action).
            TaxTransaction(action='BUYSELL', date='2024-01-10',
                           symbol='AAA.TO', quantity=10.0, price=0.0,
                           net_amount=0.0, currency='CAD', account='m'),
            # The same 2:1 split, once per broker feed (distinct ids).
            TaxTransaction(action='SPLIT', date='2024-02-01',
                           symbol='AAA.TO', quantity=2.0, currency='CAD',
                           account='m', id='split-broker-a'),
            TaxTransaction(action='SPLIT', date='2024-02-01',
                           symbol='AAA.TO', quantity=2.0, currency='CAD',
                           account='m', id='split-broker-b'),
            # Sell the whole REAL position (20 post-split shares).
            TaxTransaction(action='BUYSELL', date='2024-03-01',
                           symbol='AAA.TO', quantity=-20.0, price=5.0,
                           net_amount=100.0, currency='CAD', account='m'),
            # Clean 2025 round trip — must NOT be flagged.
            TaxTransaction(action='BUYSELL', date='2025-01-10',
                           symbol='AAA.TO', quantity=5.0, price=5.0,
                           net_amount=25.0, currency='CAD', account='m'),
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAA.TO', quantity=-5.0, price=6.0,
                           net_amount=30.0, currency='CAD', account='m'),
        ]
        rows = detect_zero_basis_acquisitions(txs, year=2025)
        self.assertEqual(len(rows), 1)
        self.assertFalse(
            rows[0].affects_year,
            "duplicate split rows double-scaled `running`, the pool "
            "never read as drained, and the clean 2025 sale stayed "
            "flagged as contaminated")

    def test_crypto_cache_save_is_atomic(self):
        import json as _json
        import os as _os
        from taxjson.lib import crypto_prices as fcp
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "cache.json")
            old_cache, fcp.CACHE_FILE = fcp.CACHE_FILE, target
            try:
                fcp.save_cache({"BTC": {"2024-01-01": 42000.0}})
                self.assertTrue(_os.path.exists(target))
                self.assertFalse(_os.path.exists(target + ".part"),
                                 "tmp sidecar must be renamed away")
                self.assertEqual(
                    _json.loads(Path(target).read_text())["BTC"]
                    ["2024-01-01"], 42000.0)
                # Unreadable cache degrades to {} instead of raising.
                _os.chmod(target, 0)
                if not _os.access(target, _os.R_OK):   # skip as root
                    self.assertEqual(fcp.load_cache(), {})
            finally:
                _os.chmod(target, 0o600)
                fcp.CACHE_FILE = old_cache

    def test_setup_sh_fails_loudly_without_venv(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "elsewhere" / "taxjson-clone"
            clone.mkdir(parents=True)
            shutil.copy(REPO_ROOT / "setup.sh", clone / "setup.sh")
            r = subprocess.run(
                ["bash", "-c", f'source "{clone}/setup.sh"'],
                capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0,
                            "no venv → sourcing must fail, not print ✅")
        self.assertNotIn("✅", r.stdout)


_KR_TRADES_HEADER = ('"txid","ordertxid","pair","time","type","ordertype",'
                     '"price","cost","fee","vol","margin","misc","ledgers"\n')


class TestKnownIssuesGraduated(unittest.TestCase):
    """KNOWN_ISSUES items implemented per their own fix templates
    (2026-08): Kraken trades crypto/crypto two-leg emission, legacy
    concatenated pairs, the X-prefix asset enumeration, Coinbase
    Convert rows, and the Webull description carry-over guard.

    Formerly: test_audit_2026_08_fixes.TestKnownIssuesGraduated"""

    def _kraken(self, rows):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        return _parse_csv(KrakenBrokerage, _KR_TRADES_HEADER + rows)

    def test_kraken_crypto_crypto_trade_emits_two_usd_legs(self):
        txs = self._kraken(
            '"T1","O1","XBT/ETH","2024-02-10 14:25:18","buy","limit",'
            '"18.50","1.85","0.003","0.10","0.00","",""\n')
        self.assertEqual(len(txs), 2)
        by_sym = {t['symbol']: t for t in txs}
        self.assertAlmostEqual(by_sym['BTC']['quantity'], 0.10)
        # cost 1.85 ETH + the 0.003 ETH fee (trades-CSV fee is in QUOTE
        # units): the fee coins left the balance too.
        self.assertAlmostEqual(by_sym['ETH']['quantity'], -1.853)
        for t in txs:
            self.assertEqual(t['currency'], 'USD')
            self.assertEqual(t['price'], 0.0)   # fill-crypto backfills

    def test_kraken_legacy_concatenated_pairs(self):
        txs = self._kraken(
            '"T1","O1","XXBTZUSD","2024-02-10 14:25:18","buy","limit",'
            '"42000.00","4200.00","8.40","0.10","0.00","",""\n'
            '"T2","O2","ADAUSD","2024-02-11 14:25:18","buy","limit",'
            '"0.50","50.00","0.10","100","0.00","",""\n')
        by_sym = {t['symbol']: t for t in txs}
        self.assertEqual(by_sym['BTC']['currency'], 'USD')
        self.assertEqual(by_sym['ADA']['currency'], 'USD')
        # XETHXXBT: legacy crypto/crypto concatenation → two legs.
        legs = self._kraken(
            '"T3","O3","XETHXXBT","2024-02-12 14:25:18","sell","limit",'
            '"0.055","0.11","0.0002","2.0","0.00","",""\n')
        self.assertEqual(sorted(t['symbol'] for t in legs),
                         ['BTC', 'ETH'])

    def test_kraken_unknown_pair_format_refused(self):
        with self.assertRaises(ValueError) as cm:
            self._kraken(
                '"T1","O1","FOOBARQQQ","2024-02-10 14:25:18","buy",'
                '"limit","1.0","1.0","0.0","1.0","0.00","",""\n')
        self.assertIn("unrecognized pair format", str(cm.exception))

    def test_kraken_x_prefix_assets_normalized(self):
        from taxjson.lib.brokerages.kraken import _normalize_asset
        self.assertEqual(_normalize_asset('XXLM'), 'XLM')
        self.assertEqual(_normalize_asset('XXMR'), 'XMR')
        self.assertEqual(_normalize_asset('XZEC'), 'ZEC')
        self.assertEqual(_normalize_asset('XXDG'), 'DOGE')
        self.assertEqual(_normalize_asset('XETC'), 'ETC')
        self.assertEqual(_normalize_asset('XXBT'), 'BTC')

    def test_coinbase_convert_emits_two_legs_with_fmv(self):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        txs = _parse_csv(CoinbaseBrokerage, _CB_HEADER + (
            'CB-C-1,2024-05-01 10:00:00 UTC,Convert,BTC,0.05000000,USD,'
            '43000.00,2150.00,2160.00,10.00,'
            'Converted 0.05000000 BTC to 1.20000000 ETH\n'))
        self.assertEqual(len(txs), 2)
        by_sym = {t['symbol']: t for t in txs}
        self.assertAlmostEqual(by_sym['BTC']['quantity'], -0.05)
        self.assertAlmostEqual(by_sym['ETH']['quantity'], 1.20)
        # 2026-09 audit #2: the conversion fee must reach exactly ONE
        # leg's net_amount (it reached neither — subtotal on both — so
        # every Convert overstated the round-trip gain by the fee).
        # Convention: acquired-side fee-inclusive basis (buy net =
        # Subtotal + Fees), mirroring Kraken's instant-trade buy; the
        # disposed leg keeps proceeds = Subtotal (the stated FMV).
        self.assertAlmostEqual(by_sym['BTC']['net_amount'], 2150.0,
                               places=2)
        self.assertAlmostEqual(by_sym['ETH']['net_amount'], 2160.0,
                               places=2)
        self.assertAlmostEqual(by_sym['ETH']['fee'], 10.0, places=2)
        self.assertAlmostEqual(by_sym['BTC']['fee'], 0.0, places=2)
        self.assertEqual(by_sym['BTC']['id'], 'CB-C-1-sell')

    def test_coinbase_convert_unknown_notes_still_fails_hard(self):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        with self.assertRaises(ValueError) as cm:
            _parse_csv(CoinbaseBrokerage, _CB_HEADER + (
                'CB-C-2,2024-05-01 10:00:00 UTC,Convert,BTC,0.05,USD,'
                '43000.00,2150.00,2160.00,10.00,Some new wording\n'))
        self.assertIn("refusing to guess", str(cm.exception))

    def test_crypto_broker_in_equity_account_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2024\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')   # no crypto flag
            (root / "inputs" / "margin" / "kr_trades.csv").write_text(
                _KR_TRADES_HEADER +
                '"T1","O1","XBT/USD","2024-02-10 14:25:18","buy","limit",'
                '"42000.00","4200.00","8.40","0.10","0.00","",""\n')
            r = _run_cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("wrong pipeline", r.stderr)

    def test_webull_new_symbol_does_not_inherit_option_description(self):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.webull import WebullBrokerage
        header = ('"Currency","Date","Action Code","Symbol",'
                  '"Security Description","Type Code","Quantity",'
                  '"Price","Proceeds"\n')
        csv = header + (
            'USD,02-12-2024,SELL,@QRS,CALL QRS03/26/25 63.5,OPC,10,'
            '1.55,"1540.08"\n'
            # New EQUITY symbol, blank Description: must NOT re-parse
            # as the QRS call.
            'USD,03-12-2024,BUY,AAPL,,STK,100,150.00,"(15000.00)"\n')
        txs = _parse_csv(WebullBrokerage, csv)
        syms = sorted(t['symbol'] for t in txs)
        self.assertIn('AAPL.US', syms)
        self.assertEqual(
            sum(1 for s in syms if 'QRS' in s), 1,
            "the blank-Description AAPL row inherited the option "
            "description and re-parsed as a second QRS call")


class TestFillCryptoSemantics(unittest.TestCase):
    """Deep-audit 2026-08 findings #5/#6: fill-crypto stamped Yahoo USD
    closes without fixing the row's currency (CAD-labeled rows booked
    USD FMV as CAD), and overwrote broker-exact net_amounts with
    daily-close estimates whenever price≈0.

    Formerly: test_audit_2026_08_fixes.TestFillCryptoSemantics"""

    def _fill(self, txs, cache):
        import io
        import json as _json
        import sys as _sys
        from unittest.mock import patch
        from taxjson.bin import fill_crypto_prices as fcp
        from taxjson.lib import crypto_prices
        stdin = io.StringIO(_json.dumps({"transactions": txs}))
        stdout = io.StringIO()
        with patch.object(crypto_prices, "load_cache", lambda: dict(cache)), \
                patch.object(crypto_prices, "save_cache", lambda c: None), \
                patch.object(_sys, "argv", ["taxjson-fill-crypto"]), \
                patch.object(_sys, "stdin", stdin), \
                patch.object(_sys, "stdout", stdout):
            fcp.main()
        return _json.loads(stdout.getvalue())["transactions"]

    def _row(self, **kw):
        base = {"action": "BUYSELL", "date": "2025-06-02",
                "time": "10:00:00", "symbol": "ETH", "quantity": 1.0,
                "price": 0.0, "net_amount": 0.0, "currency": "CAD",
                "account": "wallet"}
        base.update(kw)
        return base

    def test_yahoo_fill_stamps_usd_currency(self):
        out = self._fill([self._row()], {"ETH-2025-06-02": 2500.0})
        t = out[0]
        self.assertAlmostEqual(t["price"], 2500.0, places=2)
        self.assertEqual(
            t["currency"], "USD",
            "a Yahoo *-USD close stamped onto a CAD-labeled row makes "
            "the FX stage skip it — USD FMV booked as CAD")

    def test_broker_exact_total_survives(self):
        out = self._fill(
            [self._row(quantity=0.2, net_amount=480.0)],
            {"ETH-2025-06-02": 2500.0})
        t = out[0]
        self.assertAlmostEqual(
            t["net_amount"], 480.0, places=2,
            msg="the broker's exact total was replaced by the "
                "daily-close estimate (500.0)")
        self.assertAlmostEqual(t["price"], 2400.0, places=2)  # 480/0.2
        self.assertEqual(t["currency"], "CAD",
                         "a broker total stays in its labeled currency")


class TestKrakenLegacyZQuotePairs(unittest.TestCase):
    """Deep-audit 2026-08: the generic concatenation regex greedily ate
    the Z of a Z-prefixed fiat quote — USDTZUSD parsed as garbage
    symbol 'USDTZ' that fill-crypto could never price.

    Formerly: test_audit_2026_08_fixes.TestKrakenLegacyZQuotePairs"""

    def test_stablecoin_and_fiat_z_quotes(self):
        from taxjson.lib.brokerages.kraken import _split_pair
        self.assertEqual(_split_pair('USDTZUSD'), ('USDT', 'USD'))
        self.assertEqual(_split_pair('ZUSDZCAD'), ('USD', 'CAD'))

    def test_tezos_not_misparsed(self):
        from taxjson.lib.brokerages.kraken import _split_pair
        # XTZ ends in Z — the explicit-base restriction keeps it out of
        # the Z-quote branch.
        self.assertEqual(_split_pair('XTZUSD'), ('XTZ', 'USD'))


class TestLowSeverityDeepAuditTail(unittest.TestCase):
    """Deep-audit 2026-08 low-severity tail: Kraken duplicate-refid leg
    accumulation and negative-fee netting in sum_gains.

    Formerly: test_audit_2026_08_fixes.TestLowSeverityDeepAuditTail"""

    def test_kraken_split_settlement_legs_accumulate(self):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        csv = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
               "L1,REF1,2025-01-15 10:05:00,spend,,currency,ZUSD,,-60,0.5,0\n"
               "L2,REF1,2025-01-15 10:05:01,spend,,currency,ZUSD,,-40,0.5,0\n"
               "L3,REF1,2025-01-15 10:05:02,receive,,currency,XXBT,,0.001,0,0\n")
        txs = _parse_csv(KrakenBrokerage, csv)
        trade = next(t for t in txs
                     if t.get('description', '').startswith('Instant'))
        # Both spend rows count: 100 spent + 1.0 fee (fee-inclusive).
        self.assertAlmostEqual(trade['net_amount'], 101.0, places=2,
                               msg="the second spend row overwrote the "
                                   "first — 40 of the spend vanished")

    def test_sum_gains_nets_fee_rebates(self):
        from taxjson.lib.gain_summary import summarize_gains
        res = summarize_gains({"transactions": [
            {"date": "2025-03-01", "symbol": "A.TO", "qty": -10,
             "gain": 100.0, "proceeds": 1000.0, "cost": 900.0,
             "currency": "CAD", "fee": 9.99, "commission": 0.0,
             "account": "m"},
            {"date": "2025-04-01", "symbol": "A.TO", "qty": -10,
             "gain": 100.0, "proceeds": 1000.0, "cost": 900.0,
             "currency": "CAD", "fee": -4.99, "commission": 0.0,
             "account": "m"},   # rebate
        ]})
        self.assertAlmostEqual(
            res["total_fees"]["CAD"], 5.0, places=2,
            msg="the -4.99 rebate was dropped; fees overstated at 9.99")


class TestReAuditRegressions(unittest.TestCase):
    """Fixes for regressions the 2026-08 verification re-audit found in
    the fix-session diff itself.

    Formerly: test_audit_2026_08_fixes.TestReAuditRegressions"""

    def test_sheltered_marked_leg_does_not_gate_taxable_premium(self):
        # Canada: covered-call assignment with a PLAIN-convention stock
        # leg; the sheltered book has its own unrelated marked ASSIGN
        # stock row on the same symbol — it must not strand the taxable
        # premium.
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='SPY.US', quantity=100.0, price=50.0,
                           net_amount=5000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='SPY250620C00100000.US', quantity=-1.0,
                           price=5.0, net_amount=500.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-06-20',
                           time='16:00:00',
                           symbol='SPY250620C00100000.US', quantity=1.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            # PLAIN BUYSELL stock leg (no marked taxable leg exists).
            TaxTransaction(action='BUYSELL', date='2025-06-20',
                           time='16:00:01', symbol='SPY.US',
                           quantity=-100.0, price=100.0,
                           net_amount=10000.0, currency='USD',
                           account='M'),
        ]
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2024-06-01',
                           symbol='SPY.US', quantity=10.0, price=50.0,
                           net_amount=500.0, currency='USD', account='R'),
            TaxTransaction(action='ASSIGN', date='2025-03-01',
                           symbol='SPY.US', quantity=-10.0, price=90.0,
                           net_amount=900.0, currency='USD', account='R'),
        ]
        result = CanadaTaxRules().compute_gains(
            taxable, sheltered_transactions=sheltered)
        self.assertAlmostEqual(
            result['summary']['total_gain'], 5500.0, places=2,
            msg="the sheltered marked leg gated the taxable pop and the "
                "500 premium vanished (buggy total: 5000)")

    def test_passed_marked_leg_does_not_gate_later_plain_assignment(self):
        # US: a marked-leg pair in Feb must not strand a JUNE
        # plain-convention assignment's premium.
        txs = [
            # Feb marked pair.
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='QZQ250620P00050000.US', quantity=-1.0,
                           price=1.0, net_amount=100.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-02-01',
                           time='09:00:00',
                           symbol='QZQ250620P00050000.US', quantity=1.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-02-01',
                           time='16:00:00', symbol='QZQ.US',
                           quantity=100.0, price=50.0, net_amount=5000.0,
                           currency='USD', account='M'),
            # June plain-convention assignment (second broker).
            TaxTransaction(action='BUYSELL', date='2025-05-05',
                           symbol='QZQ250820P00060000.US', quantity=-1.0,
                           price=6.0, net_amount=600.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-06-15',
                           time='09:00:00',
                           symbol='QZQ250820P00060000.US', quantity=1.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            TaxTransaction(action='BUYSELL', date='2025-06-15',
                           time='16:00:00', symbol='QZQ.US',
                           quantity=100.0, price=60.0, net_amount=6000.0,
                           currency='USD', account='M'),
            # Liquidate everything at cost-neutral prices; the premiums
            # are the only P&L.
            TaxTransaction(action='BUYSELL', date='2025-09-01',
                           symbol='QZQ.US', quantity=-200.0, price=55.0,
                           net_amount=11000.0, currency='USD',
                           account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        total = sum(g['raw_gain'] for g in result['transactions']
                    if g.get('raw_gain') is not None)
        # Basis: 5000-100 + 6000-600 = 10,300; proceeds 11,000 → +700.
        self.assertAlmostEqual(
            total, 700.0, places=2,
            msg="the long-gone Feb marked leg gated the June premium "
                "(buggy total: 100)")

    def test_adjust_cmd_excludes_permanent_share(self):
        # Mixed triggers: 100 taxable-deferred + 100 sheltered-denied.
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='XEI.TO', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='CAD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='XEI.TO', quantity=-100.0, price=8.0,
                           net_amount=800.0, currency='CAD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-20',
                           symbol='XEI.TO', quantity=50.0, price=8.0,
                           net_amount=400.0, currency='CAD', account='M'),
        ]
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-02-21',
                           symbol='XEI.TO', quantity=50.0, price=8.0,
                           net_amount=400.0, currency='CAD', account='R'),
        ]
        result = CanadaTaxRules().compute_gains(
            txs, sheltered_transactions=sheltered)
        ws = result['wash_sales'][0]
        # disallow carries the full 200; adjust only the poolable 100.
        self.assertIn("200.0000", ws['disallow_cmd'])
        self.assertIn("100.0000", ws['adjust_cmd'])
        self.assertNotIn("200.0000", ws['adjust_cmd'])

    def test_fill_crypto_qty_zero_real_net_untouched(self):
        T = TestFillCryptoSemantics
        helper = T('test_yahoo_fill_stamps_usd_currency')
        out = helper._fill(
            [helper._row(action='DIVIDEND', quantity=0.0,
                         net_amount=55.0)],
            {"ETH-2025-06-02": 2500.0})
        t = out[0]
        self.assertAlmostEqual(
            t['net_amount'], 55.0, places=2,
            msg="qty=0 fell through to the Yahoo path and booked one "
                "full ETH (3,688) over the broker's $55")
        self.assertEqual(t['currency'], 'CAD')

    def test_claim_consumed_loss_not_resuggested(self):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        ledger = build_canada_ledger(
            {2021: {"net": 1000.0, "dispositions": 1},
             2023: {"net": -600.0, "dispositions": 1}},
            {2021: 600.0})
        row = next(r for r in ledger["rows"] if r["year"] == 2023)
        self.assertEqual(
            row["carryback_candidates"], [],
            "the fully-claimed 600 loss generated fresh carryback "
            "suggestions (buggy: 400 more to 2021)")

    def test_kraken_instant_fee_is_fiat_side_only(self):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        csv = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
               "L1,R1,2025-01-15 10:05:00,spend,,currency,ZUSD,,-100,1.50,0\n"
               "L2,R1,2025-01-15 10:05:01,receive,,currency,XETH,,0.05,0.0002,0\n")
        txs = _parse_csv(KrakenBrokerage, csv)
        t = next(x for x in txs if x['description'] == 'Instant Trade')
        # The 0.0002 receive fee is denominated in ETH — it must not be
        # summed into the fiat net as $0.0002.
        self.assertAlmostEqual(t['net_amount'], 101.50, places=4)
        self.assertAlmostEqual(t['fee'], 1.50, places=4)

    def test_kraken_different_asset_refid_legs_warn(self):
        import io
        from contextlib import redirect_stderr
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        csv = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
               "L1,R3,2025-01-15 10:05:00,spend,,currency,ZUSD,,-50,0,0\n"
               "L2,R3,2025-01-15 10:05:01,spend,,currency,ZEUR,,-20,0.30,0\n"
               "L3,R3,2025-01-15 10:05:02,receive,,currency,XXBT,,0.001,0,0\n")
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = _parse_csv(KrakenBrokerage, csv)
        # Re-premised (crypto hardening, dust-sweep fix): the legs are
        # no longer overwritten-with-a-warning — EVERY spend leg is
        # booked (the receipt split across them, equally here since the
        # export has no amountusd), and the split is announced.
        self.assertIn("2 spend legs", buf.getvalue())
        buys = [t for t in txs if t['action'] == 'BUYSELL']
        self.assertEqual(sorted(t['currency'] for t in buys),
                         ['EUR', 'USD'],
                         "the USD leg was silently overwritten by the "
                         "EUR leg")
        self.assertAlmostEqual(sum(t['quantity'] for t in buys), 0.001)

    def test_pending_elections_preserve_wash_artifacts(self):
        # A full run whose SHELTERED account defers on corp-action
        # elections must NOT destroy the taxable account's last-good
        # wash artifacts (the run exits 3; context is transient).
        from _support.ib_merger_election import _SSL_RGLD_CSV
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for acct in ("margin", "rrsp"):
                (root / "inputs" / acct).mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                # asked: by default a sheltered account's merger is
                # booked without asking (CA-CORP-11)
                'sheltered_elections = "ask"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n')
            row = ("2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,"
                   "XEI.TO,D,100,10.00,1000.00,0.00,-1000.00,CAD,1,"
                   "Trades,Individual\n")
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER + row)
            (root / "inputs" / "rrsp" / "questrade.csv").write_text(
                _QT_HEADER + row)
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            wash = root / "work" / "margin_gains_wash.json"
            self.assertTrue(wash.exists())
            # Election-requiring corp action lands in the RRSP inputs.
            (root / "inputs" / "rrsp" / "ib_corp.csv").write_text(
                _SSL_RGLD_CSV)
            r2 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r2.returncode, 3, r2.stderr)
            self.assertTrue(
                wash.exists(),
                "the pending-elections deferral destroyed the last "
                "full run's wash output before aborting")


_KR_LEDGER_HEADER = ('"txid","refid","time","type","subtype","aclass",'
                     '"asset","amount","fee","balance"\n')


class TestParserAudit202609Fixes(unittest.TestCase):
    """2026-09 parser audit: Kraken orphan instant-trade legs warn
    loudly (#1), the Kraken ledger `trade`-row note points at the
    trades export (#5), the Coinbase Convert fee reaches the acquired
    leg's basis (#2, pinned in TestKnownIssuesGraduated), and RBC
    NON-RES TAX WITHHELD qty/rate derive from GROSS (#4). The generic
    importer's parenthesized-negative fix (#3) is pinned in
    test_generic_importer.py.

    Formerly: test_audit_2026_08_fixes.TestParserAudit202609Fixes"""

    def _kraken_ledger(self, rows):
        import io
        from contextlib import redirect_stderr
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = _parse_csv(KrakenBrokerage, _KR_LEDGER_HEADER + rows)
        return txs, buf.getvalue()

    def test_kraken_orphan_spend_warns_loudly(self):
        # A lone `spend` leg (no matching `receive` under the refid) is
        # a real taxable disposition; it used to vanish with zero
        # signal (_build_instant_trade returned [] silently).
        txs, err = self._kraken_ledger(
            '"L1","R9","2024-03-01 10:00:00","spend","","currency",'
            '"XXBT","-0.05","0.0001","0"\n')
        self.assertEqual(txs, [])
        self.assertIn("orphan spend", err)
        self.assertIn("R9", err)
        self.assertIn("BTC", err)
        self.assertIn("0.05", err)
        self.assertIn("taxable disposition", err)

    def test_kraken_orphan_receive_warns_and_complete_pair_unaffected(self):
        txs, err = self._kraken_ledger(
            # Complete fiat instant trade under R1...
            '"L1","R1","2024-03-01 10:00:00","spend","","currency",'
            '"ZUSD","-2100.00","0.00","0"\n'
            '"L2","R1","2024-03-01 10:00:00","receive","","currency",'
            '"XXBT","0.05","0","0"\n'
            # ...plus an orphan receive under R2.
            '"L3","R2","2024-03-02 10:00:00","receive","","currency",'
            '"XETH","1.5","0","0"\n')
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['symbol'], 'BTC')
        self.assertAlmostEqual(txs[0]['quantity'], 0.05)
        self.assertIn("orphan receive", err)
        self.assertIn("R2", err)
        self.assertIn("ETH", err)

    def test_kraken_ledger_trade_rows_get_dedicated_note(self):
        # Ledger `trade` rows are NOT parsed (the trades export is the
        # per-fill record). The generic note claimed transfers "don't
        # affect gains" — actively misleading for a ledgers-only user
        # whose actual trades were dropped.
        txs, err = self._kraken_ledger(
            '"L1","R1","2024-03-01 10:00:00","trade","","currency",'
            '"XXBT","-0.05","0.0001","0"\n'
            '"L2","R1","2024-03-01 10:00:00","trade","","currency",'
            '"ZUSD","2100","0","0"\n'
            '"L3","","2024-03-02 10:00:00","deposit","","currency",'
            '"ZUSD","500","0","0"\n')
        # 2026-09 round six: a FIAT (ZUSD) deposit is neither
        # custody evidence nor a disposition — back to the counted
        # note (crypto deposits/withdrawals emit evidence rows;
        # see test_transfer_sidecar). Ledger trade rows keep their
        # dedicated note.
        self.assertEqual(txs, [])
        self.assertIn("deposit x1", err)
        # R1-104: with no trades export beside it, the unbooked trade
        # is a loud UNBOOKED warning (counted per trade, not per row).
        self.assertIn("warning: UNBOOKED:", err)
        self.assertIn("1 trade(s) of 1", err)
        self.assertIn("supply the trades export", err)
        self.assertIn("not parsed", err)
        self.assertNotIn("trade x2", err)

    def test_coinbase_convert_without_subtotal_still_backfillable(self):
        # A Convert row with no value at all ships price=0 AND net=0 on
        # both legs so taxjson-fill-crypto backfills FMV (fill derives
        # price from any nonzero net_amount).
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        txs = _parse_csv(CoinbaseBrokerage, _CB_HEADER + (
            'CB-C-3,2024-05-01 10:00:00 UTC,Convert,BTC,0.05000000,USD,'
            ',,,,Converted 0.05000000 BTC to 1.20000000 ETH\n'))
        self.assertEqual(len(txs), 2)
        for t in txs:
            self.assertEqual(t['price'], 0.0)
            self.assertEqual(t['net_amount'], 0.0)
        # With a fee but no Subtotal, Total or Price, the fee (in the
        # row's currency) could not be added to the value fill-crypto
        # finds in US dollars: it was dropped, now the row is refused
        # (backlog TJ-0512).
        with self.assertRaises(ValueError) as cm:
            _parse_csv(CoinbaseBrokerage, _CB_HEADER + (
                'CB-C-3,2024-05-01 10:00:00 UTC,Convert,BTC,0.05000000,USD,'
                ',,,10.00,Converted 0.05000000 BTC to 1.20000000 ETH\n'))
        self.assertIn("fee of 10.00 USD", str(cm.exception))

    _RBC_HEADER = ('Date,Activity,Symbol,Description,Quantity,Price,'
                   'Settlement Date,Currency,Value,Amount\n')

    def _rbc(self, rows):
        from _support.parser_csv import _parse_csv
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        return _parse_csv(RbcBrokerage, self._RBC_HEADER + rows)

    def test_rbc_net_tax_qty_derived_from_gross(self):
        # Description states the DECLARED (gross) $1.00/sh rate; the
        # $85 Amount is net of 15% withholding. Deriving qty from NET
        # booked qty 85 for a 100-share position.
        txs = self._rbc(
            '06/14/2024,Dividends,MSFT,MICROSOFT NON-RES TAX WITHHELD '
            'CASH DIV $1.00 PER SHARE,0,0.00,06/14/2024,USD,0.00,85.00\n')
        div = next(t for t in txs if t['action'] == 'DIVIDEND')
        tax = next(t for t in txs if t['action'] == 'TAX')
        self.assertAlmostEqual(div['quantity'], 100.0, places=6,
                               msg="qty derived from NET (85) instead "
                                   "of GROSS (100)")
        self.assertAlmostEqual(div['price'], 1.0, places=6)
        self.assertAlmostEqual(div['net_amount'], 85.0, places=2)
        self.assertAlmostEqual(div['gross_amount'], 100.0, places=2)
        self.assertAlmostEqual(tax['net_amount'], 15.0, places=2)

    def test_rbc_net_tax_rate_derived_from_gross(self):
        # Only "ON N SHS" in the description: the per-share rate is
        # back-computed and must be the GROSS (declared) rate, not the
        # net-of-withholding one (the old gross re-derivation branch
        # was dead: rate was already net-filled by the first call).
        txs = self._rbc(
            '06/14/2024,Dividends,MSFT,MICROSOFT NON-RES TAX WITHHELD '
            'CASH DIV ON 100 SHS,0,0.00,06/14/2024,USD,0.00,85.00\n')
        div = next(t for t in txs if t['action'] == 'DIVIDEND')
        self.assertAlmostEqual(div['quantity'], 100.0, places=6)
        self.assertAlmostEqual(div['price'], 1.0, places=6,
                               msg="per-share rate derived from NET "
                                   "(0.85) instead of GROSS (1.00)")
        self.assertAlmostEqual(div['gross_amount'], 100.0, places=2)

    def test_rbc_plain_dividend_derivation_unchanged(self):
        # No withholding: gross == net, derivation identical to before.
        txs = self._rbc(
            '03/13/2024,Dividends,RY,ROYAL BANK CASH DIV ON 100 SHS,'
            '0,0.00,03/13/2024,CAD,0.00,138.00\n')
        div = next(t for t in txs if t['action'] == 'DIVIDEND')
        self.assertAlmostEqual(div['quantity'], 100.0, places=6)
        self.assertAlmostEqual(div['price'], 1.38, places=6)
        self.assertAlmostEqual(div['gross_amount'], 138.0, places=2)


if __name__ == '__main__':
    unittest.main()
