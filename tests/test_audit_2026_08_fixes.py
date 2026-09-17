"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestWashUnitFactorSurvivesLaterRename(unittest.TestCase):
    """Audit 2026-08 #1: `_rep_units_factor` queried the split schedule
    under the loss transaction's raw symbol, but SplitTimeline migrates
    a symbol's schedule onto the rename target at BUILD time — so any
    rename anywhere in the input emptied the raw symbol's schedule and
    the factor collapsed to 1.0, mis-scaling §1091 matches for losses
    evaluated under the pre-rename ticker."""

    def _txs(self, with_later_rename: bool):
        txs = [
            # Lot A — fully consumed by the sale (its rep is excluded
            # by quantity accounting, not by this test's target path).
            TaxTransaction(action='BUYSELL', date='2025-01-10',
                           symbol='OLD.US', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            # Lot B — retained; the replacement (100 pre-split shares).
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='OLD.US', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            # 2:1 split between the replacement buy and the loss sale.
            TaxTransaction(action='SPLIT', date='2025-02-10',
                           symbol='OLD.US', quantity=2.0, currency='USD',
                           account='M'),
            # Sell 200 post-split shares (all of lot A) at a $600 loss.
            TaxTransaction(action='BUYSELL', date='2025-02-15',
                           symbol='OLD.US', quantity=-200.0, price=2.0,
                           net_amount=400.0, currency='USD', account='M'),
        ]
        if with_later_rename:
            # Rename months AFTER the wash sale — must not change it.
            txs.append(TaxTransaction(
                action='SPLIT', date='2025-06-01', symbol='OLD.US',
                quantity=1.0, symbol_new='NEW.US', currency='USD',
                account='M'))
        return txs

    def _disallowed(self, txs):
        result = USATaxRules().compute_gains(txs)
        return sum(g.get('disallowed_amount', 0.0)
                   for g in result['transactions']
                   if g.get('raw_gain', 0.0) < 0)

    def test_later_rename_does_not_change_disallowance(self):
        without = self._disallowed(self._txs(with_later_rename=False))
        with_rename = self._disallowed(self._txs(with_later_rename=True))
        # 100 retained pre-split shares × factor 2.0 cover the full
        # 200-share loss: $600 disallowed either way.
        self.assertAlmostEqual(without, 600.0, places=2)
        self.assertAlmostEqual(
            with_rename, 600.0, places=2,
            msg="a rename AFTER the wash sale emptied the raw symbol's "
                "split schedule and halved the disallowance")


class TestMultiAccountSplitBalanceWalk(unittest.TestCase):
    """Audit 2026-08 #2: `_dedupe_corporate_splits` keeps one SPLIT row
    per corporate event (survivor account arbitrary), but the Canada
    engine's per-(account, alias) running-balance walk scaled only the
    survivor's account — every other account holding the symbol kept a
    pre-split balance, so a post-split sale there was misread as
    part short-opening and the rebuy as part short-covering, shrinking
    disallowed_qty."""

    def _fixture(self, two_accounts: bool):
        """Account Y: buy 100 @ 40, 4:1 split, sell 400 at a $600 loss,
        rebuy 400 within 30 days (held at window end → fully
        superficial). When `two_accounts`, account X also holds the
        symbol through the split and X's SPLIT row is the dedupe
        survivor (listed first)."""
        txs = []
        if two_accounts:
            txs += [
                TaxTransaction(action='BUYSELL', date='2025-01-05',
                               symbol='AAA.TO', quantity=100.0, price=40.0,
                               net_amount=4000.0, currency='CAD',
                               account='X'),
                # X's copy of the split row survives dedupe.
                TaxTransaction(action='SPLIT', date='2025-02-01',
                               symbol='AAA.TO', quantity=4.0,
                               currency='CAD', account='X'),
            ]
        txs += [
            TaxTransaction(action='BUYSELL', date='2025-01-06',
                           symbol='AAA.TO', quantity=100.0, price=40.0,
                           net_amount=4000.0, currency='CAD', account='Y'),
            # Y's copy — deduped away in the two-account fixture.
            TaxTransaction(action='SPLIT', date='2025-02-01',
                           symbol='AAA.TO', quantity=4.0, currency='CAD',
                           account='Y'),
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAA.TO', quantity=-400.0, price=8.5,
                           net_amount=3400.0, currency='CAD', account='Y'),
            TaxTransaction(action='BUYSELL', date='2025-02-20',
                           symbol='AAA.TO', quantity=400.0, price=8.5,
                           net_amount=3400.0, currency='CAD', account='Y'),
        ]
        return txs

    def test_disallowance_matches_single_account_control(self):
        control = CanadaTaxRules().compute_gains(
            self._fixture(two_accounts=False))
        multi = CanadaTaxRules().compute_gains(
            self._fixture(two_accounts=True))
        self.assertAlmostEqual(
            control['summary']['total_disallowed'], 600.0, places=2)
        self.assertAlmostEqual(
            multi['summary']['total_disallowed'], 600.0, places=2,
            msg="account Y's balance stayed in pre-split units because the "
                "dedupe-surviving SPLIT row was applied only in account X")


class TestQuestradeDividendSignPreserved(unittest.TestCase):
    """Audit 2026-08 #3: `_parse_dividend` abs()'d Net Amount, so
    negative Dividends-activity rows — withholding debits, dividend
    reversals, ROC reversals — were booked as positive income (and a
    ROC reversal as a SECOND ACB reduction instead of a restoration)."""

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
        from test_parser_activity_coverage import _parse_csv
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
    mirrors it."""

    HEADER = ('Date,Activity,Symbol,Description,Quantity,Price,'
              'Settlement Date,Currency,Value,Amount\n')

    def _parse(self, rows):
        from test_parser_activity_coverage import _parse_csv
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        return _parse_csv(RbcBrokerage, self.HEADER + rows)

    def test_dividend_reversal_nets_to_zero(self):
        txs = self._parse(
            '03/01/2024,Dividends,RY,ROYAL BANK CASH DIV ON 100 SHS,'
            '0,0.00,03/01/2024,CAD,0.00,138.00\n'
            '03/05/2024,Dividends,RY,ROYAL BANK CASH DIV REVERSAL ON 100 '
            'SHS,0,0.00,03/05/2024,CAD,0.00,-138.00\n')
        divs = [t for t in txs if t['action'] == 'DIVIDEND']
        self.assertEqual(len(divs), 2)
        self.assertEqual(sorted(t['net_amount'] for t in divs),
                         [-138.0, 138.0])
        self.assertAlmostEqual(sum(t['net_amount'] for t in divs), 0.0,
                               places=2,
                               msg="abs() reported $276 income for a fully "
                                   "reversed dividend")

    def test_withholding_charge_positive_refund_negative(self):
        txs = self._parse(
            # Charge: cash out, negative Amount → +4.05 tax withheld.
            '04/01/2024,Taxes,AAPL,NON-RESIDENT TAX PAID,'
            '0,0.00,04/01/2024,USD,0.00,-4.05\n'
            # Refund: cash in, positive Amount → NETS NEGATIVE.
            '05/01/2024,Taxes,AAPL,NON-RESIDENT TAX ADJUSTMENT REFUND,'
            '0,0.00,05/01/2024,USD,0.00,4.05\n')
        taxes = [t for t in txs if t['action'] == 'TAX']
        self.assertEqual(len(taxes), 2)
        self.assertEqual(sorted(t['net_amount'] for t in taxes),
                         [-4.05, 4.05])
        self.assertAlmostEqual(sum(t['net_amount'] for t in taxes), 0.0,
                               places=2)

    def test_net_withholding_reversal_scales_gross_up(self):
        """A reversed net-of-withholding dividend must reverse both the
        grossed-up DIVIDEND and its implied TAX row."""
        row = ('06/01/2024,Dividends,MSFT,MICROSOFT NON-RES TAX WITHHELD '
               'CASH DIV ON 100 SHS,0,0.00,06/01/2024,USD,0.00,{amt}\n')
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


class TestUsaCarryoverGapYears(unittest.TestCase):
    """Audit 2026-08 #5: `build_usa_ledger` iterated only disposition
    years, so in a no-trade gap year neither the assumed $3,000
    ordinary-income offset nor a claimed_losses.txt entry was applied —
    the final carryover was overstated by up to $3k per gap year
    (the Canada ledger got this fix in REVIEW #23; USA did not)."""

    def _nets(self):
        return {
            2020: {'net': -10000.0, 'st': -10000.0, 'lt': 0.0,
                   'dispositions': 1},
            2024: {'net': 1000.0, 'st': 1000.0, 'lt': 0.0,
                   'dispositions': 1},
        }

    def test_assumed_offset_applies_in_gap_years(self):
        from taxjson.bin.taxjson_carryover import build_usa_ledger
        ledger = build_usa_ledger(self._nets(), {})
        by_year = {r['year']: r for r in ledger['rows']}
        # 2020 absorbs 3k → 7k carry; 2021-2023 returns absorb 3k, 3k,
        # then the final 1k; 2024's gain sees no remaining carryover.
        self.assertAlmostEqual(by_year[2021]['ordinary_income_offset'],
                               3000.0, places=2)
        self.assertAlmostEqual(by_year[2022]['ordinary_income_offset'],
                               3000.0, places=2)
        self.assertAlmostEqual(by_year[2023]['ordinary_income_offset'],
                               1000.0, places=2)
        self.assertAlmostEqual(
            ledger['final_carryforward'], 0.0, places=2,
            msg="gap years 2021-2023 were skipped: the buggy final "
                "carryover was $3,000")

    def test_claimed_zero_in_gap_year_preserves_carry(self):
        from taxjson.bin.taxjson_carryover import build_usa_ledger
        ledger = build_usa_ledger(
            self._nets(), {2021: 0.0, 2022: 0.0, 2023: 0.0})
        by_year = {r['year']: r for r in ledger['rows']}
        # No returns filed 2021-2023: the full 7k carry survives into
        # 2024, nets against the 1k gain (→ -6k), 2024 assumes 3k.
        self.assertAlmostEqual(by_year[2023]['st_carryover'], 7000.0,
                               places=2)
        self.assertAlmostEqual(ledger['final_carryforward'], 3000.0,
                               places=2)


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

_CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
              "Price Currency,Price at Transaction,Subtotal,"
              "Total (inclusive of Fees and/or Spread),"
              "Fees and/or Spread,Notes\n")


class TestFastSeesTtAndGroupDeletions(unittest.TestCase):
    """Audit 2026-08 #6: the FUZZ #J sources manifest listed only CSVs,
    so under `run --fast` (a) deleting a .tt file left its transactions
    in the cached books, and (b) on the crypto path — whose merge had
    no manifest dep — deleting the only CSV group did the same when a
    .tt kept the account alive."""

    def test_deleted_tt_drops_from_books_under_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
                "ISHARES COMP,100,10.00,1000.00,0.00,-1000.00,CAD,1,"
                "Trades,Individual\n")
            tt = root / "inputs" / "margin" / "start.tt"
            tt.write_text(
                "BUYSELL 2025-03-01 09:30:00 TTONLY.TO 10 CAD 5.0 -50.0\n")
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            base1 = (root / "work" / "margin_base.json").read_text()
            self.assertIn("TTONLY.TO", base1)
            tt.unlink()                          # delete the .tt
            r2 = _run_cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            base2 = (root / "work" / "margin_base.json").read_text()
        self.assertNotIn(
            "TTONLY.TO", base2,
            msg=".tt files were missing from the sources manifest — the "
                "deleted opening balances survived --fast")

    def test_fast_noop_run_serves_cache(self):
        """Companion fix: with `source_currencies = []` the empty
        to_base.csv marker was REWRITTEN every run, so its mtime always
        dirtied the merge2 dep and --fast re-merged every account on
        every invocation (which also masked the .tt deletion bug above
        in offline configs)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "questrade.csv").write_text(
                _QT_HEADER +
                "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
                "ISHARES COMP,100,10.00,1000.00,0.00,-1000.00,CAD,1,"
                "Trades,Individual\n")
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            base = root / "work" / "margin_base.json"
            mtime1 = base.stat().st_mtime_ns
            r2 = _run_cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            mtime2 = base.stat().st_mtime_ns
        self.assertEqual(
            mtime1, mtime2,
            msg="a no-change --fast run re-merged the account (the "
                "to_base.csv marker rewrite dirtied the cache every run)")

    def test_deleted_crypto_group_drops_from_books_under_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "wallet").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.wallet]\ntype = "taxable"\ncrypto = true\n')
            cb = root / "inputs" / "wallet" / "cb_wallet.csv"
            cb.write_text(
                _CB_HEADER +
                "CB-T-0001,2024-01-18 16:24:11 UTC,Buy,BTC,0.05000000,CAD,"
                "43000.00,2150.00,2167.20,17.20,Bought BTC\n")
            # The .tt keeps the account alive once the CSV group is gone.
            (root / "inputs" / "wallet" / "start.tt").write_text(
                "BUYSELL 2024-02-01 09:30:00 ETH-CAD 1.0 CAD 3000.0 "
                "-3000.0\n")
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            base1 = (root / "work" / "wallet_base.json").read_text()
            self.assertIn("BTC", base1)
            cb.unlink()                          # delete the whole group
            r2 = _run_cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            base2 = (root / "work" / "wallet_base.json").read_text()
        self.assertNotIn(
            "BTC", base2,
            msg="the crypto merge had no sources-manifest dep — the "
                "deleted broker group's trades survived --fast")
        self.assertIn("ETH-CAD", base2)


_CB_OLD_HEADER = ("Timestamp,Transaction Type,Asset,Quantity Transacted,"
                  "Spot Price Currency,Spot Price at Transaction,Subtotal,"
                  "Total (inclusive of fees),Notes\n")


class TestCoinbaseHeaderVariantsAndDedup(unittest.TestCase):
    """Audit 2026-08 #7: `_col` silently returned '' for missing
    columns, so the older retail layout (Spot Price ...) parsed with
    price=0.0/USD — and `taxjson-fill-crypto` then overwrote the
    broker's exact totals with daily-close estimates. Old exports also
    lack an ID column, and with no `disambiguate_split_fills` guard,
    byte-identical fills were dedup-deleted."""

    def _parse(self, content):
        from test_parser_activity_coverage import _parse_csv
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


class TestSumBasisMatchesBanner(unittest.TestCase):
    """Audit 2026-08 #8: `taxjson sum`'s fast path served
    work/<acct>_report.json on freshness alone. After `run --account X`
    (which skips the wash pass and rewrites the report from PRE-wash
    gains) sum printed pre-wash aggregates under a "wash-adjusted"
    banner, disagreeing with list/form-export/carryover which read the
    stale wash file. The report now records its basis and sum requires
    a match."""

    def test_sum_after_account_rerun_stays_on_wash_basis(self):
        import json as _json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for acct in ("margin", "rrsp"):
                (root / "inputs" / acct).mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n')
            margin_csv = root / "inputs" / "margin" / "questrade.csv"
            margin_csv.write_text(
                _QT_HEADER +
                "2025-01-10 09:30:00 AM,2025-01-11 12:00:00 AM,Buy,XEI.TO,"
                "ISHARES COMP,100,10.00,1000.00,0.00,-1000.00,CAD,1,"
                "Trades,Individual\n"
                "2025-03-10 09:30:00 AM,2025-03-11 12:00:00 AM,Sell,XEI.TO,"
                "ISHARES COMP,-100,5.00,500.00,0.00,500.00,CAD,1,"
                "Trades,Individual\n")
            # RRSP rebuy inside the ±30-day window, held → the margin
            # loss is (partly) superficial.
            (root / "inputs" / "rrsp" / "questrade.csv").write_text(
                _QT_HEADER +
                "2025-03-20 09:30:00 AM,2025-03-21 12:00:00 AM,Buy,XEI.TO,"
                "ISHARES COMP,50,5.00,250.00,0.00,-250.00,CAD,2,"
                "Trades,Individual\n")
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            wash_file = root / "work" / "margin_gains_wash.json"
            self.assertTrue(wash_file.exists())
            self.assertTrue(
                _json.loads(wash_file.read_text()).get("wash_sales"),
                "fixture must actually trigger a cross-account wash")
            s1 = _run_cli(root, "sum", "--json")
            doc1 = _json.loads(s1.stdout)
            self.assertEqual(doc1["basis"], "wash-adjusted")

            # New profitable round-trip OUTSIDE the wash window, then a
            # per-account rerun (wash pass skipped).
            margin_csv.write_text(
                margin_csv.read_text() +
                "2025-05-01 09:30:00 AM,2025-05-02 12:00:00 AM,Buy,XEI.TO,"
                "ISHARES COMP,10,10.00,100.00,0.00,-100.00,CAD,1,"
                "Trades,Individual\n"
                "2025-05-15 09:30:00 AM,2025-05-16 12:00:00 AM,Sell,XEI.TO,"
                "ISHARES COMP,-10,20.00,200.00,0.00,200.00,CAD,1,"
                "Trades,Individual\n")
            r2 = _run_cli(root, "run", "--account", "margin", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            s2 = _run_cli(root, "sum", "--json")
            doc2 = _json.loads(s2.stdout)
        self.assertEqual(doc2["basis"], "wash-adjusted")
        self.assertAlmostEqual(
            doc2["totals"]["realized"], doc1["totals"]["realized"],
            places=2,
            msg="sum served the freshly-rebuilt PRE-wash report under a "
                "wash-adjusted banner after `run --account`")


class TestReconcileSlipsDateBasis(unittest.TestCase):
    """Audit 2026-08 #9: `load_computed` year-filtered on settlement
    date unconditionally, but USA gains files and Form 8949 are scoped
    by TRADE date — a Dec-31 sale settling Jan-2 was on the 1099-B but
    missing from the computed side, producing a spurious mismatch."""

    def _entry(self):
        return {
            "date": "2025-12-31", "date_settle": "2026-01-02",
            "symbol": "AAPL.US", "qty": -100, "proceeds": 12000.0,
            "cost": 10000.0, "gain": 2000.0, "disallowed_amount": 0.0,
            "days_held": 100, "direction": "LONG",
            "commission": 0.0, "fee": 0.0, "account": "margin",
        }

    def test_trade_basis_includes_year_end_sale(self):
        import json as _json
        from taxjson.bin.taxjson_reconcile_slips import load_computed
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_gains.json"
            p.write_text(_json.dumps({"transactions": [self._entry()]}))
            settle = load_computed([p], 2025)             # default: settle
            trade = load_computed([p], 2025, "trade")
        self.assertNotIn("AAPL", settle,
                         "settle basis keeps CRA behavior: the sale "
                         "belongs to the 2026 T5008")
        self.assertIn("AAPL", trade)
        self.assertAlmostEqual(trade["AAPL"]["proceeds_gross"], 12000.0,
                               places=2)


class TestRetainedSharesAreReplacements(unittest.TestCase):
    """Audit 2026-08 #10: `find_replacements_in_window` excluded the
    sold lot's entire originating purchase by tx id, so the RETAINED
    shares of a partially-sold order never matched as §1091
    replacements — the disallowance depended purely on order
    granularity (one 200-share order vs two 100-share orders)."""

    def _disallowed(self, txs):
        result = USATaxRules().compute_gains(txs)
        return sum(g.get('disallowed_amount', 0.0)
                   for g in result['transactions']
                   if g.get('raw_gain', 0.0) < 0)

    def test_partial_sale_matches_retained_shares(self):
        one_order = [
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', quantity=200.0, price=100.0,
                           net_amount=20000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=-100.0, price=95.0,
                           net_amount=9500.0, currency='USD', account='M'),
        ]
        two_orders = [
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           time='09:30:00', symbol='AAPL', quantity=100.0,
                           price=100.0, net_amount=10000.0,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           time='09:31:00', symbol='AAPL', quantity=100.0,
                           price=100.0, net_amount=10000.0,
                           currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=-100.0, price=95.0,
                           net_amount=9500.0, currency='USD', account='M'),
        ]
        d1 = self._disallowed(one_order)
        d2 = self._disallowed(two_orders)
        # 100 shares retained, acquired 17 days before the loss sale —
        # a $500 wash disallowance either way.
        self.assertAlmostEqual(d2, 500.0, places=2)
        self.assertAlmostEqual(
            d1, 500.0, places=2,
            msg="identical economics must not diverge on order "
                "granularity: the buggy one-order result was $0")

    def test_stale_purchase_outside_window_still_allowed(self):
        txs = [
            TaxTransaction(action='BUYSELL', date='2024-06-01',
                           symbol='AAPL', quantity=200.0, price=100.0,
                           net_amount=20000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=-100.0, price=95.0,
                           net_amount=9500.0, currency='USD', account='M'),
        ]
        self.assertAlmostEqual(self._disallowed(txs), 0.0, places=2,
                               msg="retained shares acquired outside the "
                                   "±30-day window are not replacements")


class TestShelteredSellNotFullShortReplacement(unittest.TestCase):
    """Audit 2026-08 #11: a sheltered/affiliated SELL was registered as
    a short-side §1091 replacement at FULL quantity even when it merely
    closed a long in its own book — a routine RRSP sale permanently
    destroyed a taxable short-cover loss."""

    def test_sheltered_long_sale_does_not_kill_short_cover_loss(self):
        taxable = [
            # Short 100 @ 10, cover @ 15 → -$500 loss.
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='AAPL', quantity=-100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
        ]
        sheltered = [
            # RRSP holds a long 100 and sells it ordinarily Feb 10 —
            # closing a long, not opening a short.
            TaxTransaction(action='BUYSELL', date='2024-06-01',
                           symbol='AAPL', quantity=100.0, price=8.0,
                           net_amount=800.0, currency='USD', account='R'),
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAPL', quantity=-100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='R'),
        ]
        result = USATaxRules().compute_gains(
            taxable, sheltered_transactions=sheltered)
        loss = next(g for g in result['transactions']
                    if g.get('raw_gain', 0.0) < 0)
        self.assertAlmostEqual(loss['raw_gain'], -500.0, places=2)
        self.assertAlmostEqual(
            loss.get('disallowed_amount', 0.0), 0.0, places=2,
            msg="an ordinary sheltered long-sale was booked as a short "
                "replacement and permanently destroyed the loss")

    def test_sheltered_overdrawn_sale_warns_not_denies(self):
        """Deep-audit 2026-08 refinement: a sheltered SELL exceeding its
        recorded balance can't be a real short-open (registered
        accounts can't short) — it's missing history. It must warn and
        NOT permanently deny the taxable loss."""
        import io
        from contextlib import redirect_stderr
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='AAPL', quantity=-100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
        ]
        sheltered = [
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAPL', quantity=-100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='R'),
        ]
        buf = io.StringIO()
        with redirect_stderr(buf):
            result = USATaxRules().compute_gains(
                taxable, sheltered_transactions=sheltered)
        loss = next(g for g in result['transactions']
                    if g.get('raw_gain', 0.0) < 0)
        self.assertAlmostEqual(
            loss.get('permanently_disallowed', 0.0), 0.0, places=2,
            msg="a routine cross-IRA in-kind sale permanently "
                "destroyed a real taxable loss")
        self.assertIn("missing acquisition history", buf.getvalue())

    def test_affiliated_short_open_still_triggers(self):
        """Affiliated (spouse) accounts CAN short — a genuine
        affiliated short-open keeps the disallowance."""
        taxable = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='AAPL', quantity=-100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
        ]
        affiliated = [
            TaxTransaction(action='BUYSELL', date='2025-02-10',
                           symbol='AAPL', quantity=-100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='S'),
        ]
        result = USATaxRules().compute_gains(
            taxable, affiliated_transactions=affiliated)
        loss = next(g for g in result['transactions']
                    if g.get('raw_gain', 0.0) < 0)
        self.assertAlmostEqual(
            loss.get('disallowed_amount', 0.0), 500.0, places=2)


class TestCollapsePreservesFractionalDelivery(unittest.TestCase):
    """Audit 2026-08 #12: `_collapse_cross_listing_chains` rebuilt the
    collapsed event without `fractional_delivery` / `cash_in_lieu`, so
    an IB merger chain through a cross-listing journal reverted to
    snapping a genuinely delivered fraction to whole shares plus
    fictitious cash-in-lieu — regressing the phantom-short fix."""

    def _chain(self):
        from taxjson.lib.corp_actions import CorporateAction
        merger = CorporateAction(
            date='2025-06-01', time='09:30:00', action_type='merger',
            source_symbol='SSL.TO', source_isin='CA0000000001',
            target_symbol='RGLD.CAD', target_isin='US7771950000',
            ratio_new=1, ratio_old=16,
            qty_disposed=120.0, qty_received=7.5,
            fmv=1000.0, currency='CAD', target_currency='CAD',
            account='margin',
            fractional_delivery=True,
            cash_in_lieu=12.34, cash_in_lieu_currency='USD',
        )
        journal = CorporateAction(
            date='2025-06-01', time='09:31:00', action_type='merger',
            source_symbol='RGLD.CAD', source_isin='US7771950000',
            target_symbol='RGLD', target_isin='US7771950000',
            ratio_new=1, ratio_old=1,
            qty_disposed=7.5, qty_received=7.5,
            fmv=1000.0, currency='CAD', target_currency='USD',
            account='margin',
            fractional_delivery=True,
        )
        return [merger, journal]

    def test_flags_survive_collapse(self):
        from taxjson.lib.corp_actions import _collapse_cross_listing_chains
        out = _collapse_cross_listing_chains(self._chain())
        self.assertEqual(len(out), 1)
        collapsed = out[0]
        self.assertEqual(collapsed.target_symbol, 'RGLD')
        self.assertTrue(
            collapsed.fractional_delivery,
            "collapse dropped fractional_delivery: 7.5 delivered shares "
            "would snap to 7 + fictitious cash-in-lieu, going phantom-"
            "short 0.5 when the user sells their actual 7.5")
        self.assertAlmostEqual(collapsed.cash_in_lieu, 12.34, places=2)
        self.assertEqual(collapsed.cash_in_lieu_currency, 'USD')


class TestLowSeverityBatch(unittest.TestCase):
    """Audit 2026-08 low-severity fixes (#16-#24), one test each."""

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
        from test_parser_activity_coverage import (_parse_csv,
                                                   QUESTRADE_HEADER)
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
        from test_parser_activity_coverage import _parse_csv
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
        from test_parser_activity_coverage import _parse_csv
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
        from taxjson.bin.taxjson_run import collect_diagnostics
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
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
        from taxjson.lib.phantom_holdings import (
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
        from taxjson.bin import fill_crypto_prices as fcp
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

    def test_web_wash_radar_unreadable_rpt_is_banner_not_500(self):
        import os as _os
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        from taxjson.web.data import ReportArtifactError
        from test_web_data import _project
        if _os.geteuid() == 0:
            self.skipTest("chmod 0 is not effective as root")
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [], wash_rpt="--- X ---\n")
            rpt = Path(root) / "reports" / "wash_radar_margin.rpt"
            _os.chmod(rpt, 0)
            try:
                ctx = ProjectContext.load(root)
                with self.assertRaises(ReportArtifactError):
                    data.wash_radar_sections(ctx, "margin")
            finally:
                _os.chmod(rpt, 0o600)


class TestCanadaCryptoWashCoverage(unittest.TestCase):
    """Audit 2026-08 #14: the engine wash-checks Canadian crypto
    (s.54 covers any identical property), but the cross-account wash
    pass and wash-radar skipped crypto accounts on the US-only §1091
    rationale — losses were denied with zero advance warning from the
    advisory tooling."""

    def _project(self, tmp, country, base_currency, price_currency):
        root = Path(tmp)
        (root / "inputs" / "wallet").mkdir(parents=True)
        (root / "inputs" / "rrsp").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            f'[settings]\nyear = 2024\ncountry = "{country}"\n'
            f'base_currency = "{base_currency}"\nsource_currencies = []\n'
            f'[accounts.wallet]\ntype = "taxable"\ncrypto = true\n'
            f'[accounts.rrsp]\ntype = "sheltered"\n')
        (root / "inputs" / "wallet" / "cb_wallet.csv").write_text(
            _CB_HEADER +
            f"CB-1,2024-01-18 16:24:11 UTC,Buy,BTC,0.05,{price_currency},"
            f"43000.00,2150.00,2150.00,0.00,Bought BTC\n"
            f"CB-2,2024-03-18 16:24:11 UTC,Sell,BTC,0.05,{price_currency},"
            f"40000.00,2000.00,2000.00,0.00,Sold BTC\n")
        # Priced in the base currency: with `source_currencies = []` the
        # pipeline's rates file is empty, and a currency absent from it
        # is now a fatal merge2 error (stage-tools audit) rather than a
        # silent 1.35 conversion.
        (root / "inputs" / "rrsp" / "questrade.csv").write_text(
            _QT_HEADER +
            "2024-02-03 09:30:00 AM,2024-02-04 12:00:00 AM,Buy,ZAG.TO,"
            f"BMO AGG BOND,50,14.00,700.00,0.00,-700.00,{base_currency},2,"
            "Trades,Individual\n")
        return root

    def test_canada_crypto_gets_wash_pass_and_radar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, "canada", "CAD", "CAD")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(
                (root / "work" / "wallet_gains_wash.json").exists(),
                "Canada crypto must get the cross-account wash pass")
            self.assertTrue(
                (root / "reports" / "wash_radar_wallet.rpt").exists(),
                "Canada crypto must get a wash radar — the engine "
                "denies superficial crypto losses, so the advisory "
                "tooling can't stay silent")

    def test_usa_crypto_still_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, "usa", "USD", "USD")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(
                (root / "work" / "wallet_gains_wash.json").exists())
            self.assertFalse(
                (root / "reports" / "wash_radar_wallet.rpt").exists())


class TestCrossReportsWashBasis(unittest.TestCase):
    """Audit 2026-08 #15: reports/ccd.rpt and leaps.rpt were built from
    pre-wash gains while their query twins (ccd-sum/leaps-sum) resolve
    the wash-adjusted files — `_wash_preferred_gains` now picks the
    same basis, freshness-guarded."""

    def test_selection(self):
        import os as _os
        from taxjson.bin.taxjson_run import _wash_preferred_gains
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "margin_gains.json"
            wash = Path(tmp) / "margin_gains_wash.json"
            plain.write_text("{}")
            # No wash twin → plain.
            self.assertEqual(_wash_preferred_gains(plain), plain)
            # Fresh wash twin → wash.
            wash.write_text("{}")
            _os.utime(plain, (1000, 1000))
            _os.utime(wash, (2000, 2000))
            self.assertEqual(_wash_preferred_gains(plain), wash)
            # STALE wash twin (older run) must not shadow fresh gains.
            _os.utime(wash, (500, 500))
            self.assertEqual(_wash_preferred_gains(plain), plain)


_KR_TRADES_HEADER = ('"txid","ordertxid","pair","time","type","ordertype",'
                     '"price","cost","fee","vol","margin","misc","ledgers"\n')


class TestKnownIssuesGraduated(unittest.TestCase):
    """KNOWN_ISSUES items implemented per their own fix templates
    (2026-08): Kraken trades crypto/crypto two-leg emission, legacy
    concatenated pairs, the X-prefix asset enumeration, Coinbase
    Convert rows, and the Webull description carry-over guard."""

    def _kraken(self, rows):
        from test_parser_activity_coverage import _parse_csv
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        return _parse_csv(KrakenBrokerage, _KR_TRADES_HEADER + rows)

    def test_kraken_crypto_crypto_trade_emits_two_usd_legs(self):
        txs = self._kraken(
            '"T1","O1","XBT/ETH","2024-02-10 14:25:18","buy","limit",'
            '"18.50","1.85","0.003","0.10","0.00","",""\n')
        self.assertEqual(len(txs), 2)
        by_sym = {t['symbol']: t for t in txs}
        self.assertAlmostEqual(by_sym['BTC']['quantity'], 0.10)
        self.assertAlmostEqual(by_sym['ETH']['quantity'], -1.85)
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
        from test_parser_activity_coverage import _parse_csv
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
        from test_parser_activity_coverage import _parse_csv
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
                '[settings]\nyear = 2024\ncountry = "canada"\n'
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
        from test_parser_activity_coverage import _parse_csv
        from taxjson.lib.brokerages.webull import WebullBrokerage
        header = ('"Currency","Date","Action Code","Symbol",'
                  '"Security Description","Type Code","Quantity",'
                  '"Price","Proceeds"\n')
        csv = header + (
            'USD,25-11-2024,SELL,@ABBV,CALL ABBV01/17/25 190,OPC,10,'
            '1.60,"1590.08"\n'
            # New EQUITY symbol, blank Description: must NOT re-parse
            # as the ABBV call.
            'USD,26-11-2024,BUY,AAPL,,STK,100,150.00,"(15000.00)"\n')
        txs = _parse_csv(WebullBrokerage, csv)
        syms = sorted(t['symbol'] for t in txs)
        self.assertIn('AAPL.US', syms)
        self.assertEqual(
            sum(1 for s in syms if 'ABBV' in s), 1,
            "the blank-Description AAPL row inherited the option "
            "description and re-parsed as a second ABBV call")


class TestCrossTaxableOverlapWarning(unittest.TestCase):
    """KNOWN_ISSUES multi-account gap: the per-account fan-out
    silently computed wrong Canada ACB (and skipped US cross-taxable
    wash checks) when a symbol traded in two taxable accounts. The run
    now warns loudly, naming the symbols and the workaround."""

    def _project(self, tmp, second_symbol):
        root = Path(tmp)
        for acct in ("rbc", "ib2"):
            (root / "inputs" / acct).mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.rbc]\ntype = "taxable"\n'
            '[accounts.ib2]\ntype = "taxable"\n')
        row = ("2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,{sym},"
               "DESC,100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,"
               "Individual\n")
        (root / "inputs" / "rbc" / "questrade.csv").write_text(
            _QT_HEADER + row.format(sym="XEI.TO"))
        (root / "inputs" / "ib2" / "questrade.csv").write_text(
            _QT_HEADER + row.format(sym=second_symbol))
        return root

    def test_shared_symbol_across_taxable_accounts_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, second_symbol="XEI.TO")
            r = _run_cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        # Since the blended pass landed, the message is an informational
        # note (the canonical wash artifacts carry blended figures) —
        # not a wrong-numbers warning.
        self.assertIn("more than one TAXABLE account", r.stderr)
        self.assertIn("XEI.TO", r.stderr)
        self.assertIn("blended", r.stderr)

    def test_disjoint_symbols_stay_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, second_symbol="ZAG.TO")
            r = _run_cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("more than one TAXABLE account", r.stderr)


class TestStrictRunFlag(unittest.TestCase):
    """KNOWN_ISSUES "per-account validation is non-fatal": a CI/cron
    `taxjson run` could publish a wrong .sum at exit 0. `--strict`
    promotes per-account validation ERRORs to fatal."""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "inputs" / "margin" / "questrade.csv").write_text(
            _QT_HEADER +
            "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
            "ISHARES COMP,100,10.00,1000.00,0.00,-1000.00,CAD,1,"
            "Trades,Individual\n")
        # Zero-quantity BUYSELL: a validation ERROR the gains engine
        # tolerates (it skips sub-epsilon rows) — so the default run
        # publishes reports at exit 0 with only a DIAGNOSTICS banner.
        (root / "inputs" / "margin" / "start.tt").write_text(
            "BUYSELL 2025-03-01 09:30:00 ZERO.TO 0 CAD 5.0 0.0\n")
        return root

    def test_strict_promotes_validation_errors_to_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r_default = _run_cli(root, "run", "--no-input")
            r_strict = _run_cli(root, "run", "--strict", "--no-input")
        self.assertEqual(r_default.returncode, 0,
                         "default stays warn-only: " + r_default.stderr)
        self.assertNotEqual(r_strict.returncode, 0)
        self.assertIn("--strict", r_strict.stderr)
        self.assertIn("validation ERROR", r_strict.stderr)

    def test_strict_not_bypassed_by_fast_cache_hit(self):
        """Deep-audit 2026-08 #7: the strict gate lived inside the
        rebuild branch, so `run --strict --fast` on unchanged-but-
        invalid books skipped merge2, never reached the gate, and
        published reports at exit 0 — in exactly the CI/cron pairing
        the flag recommends."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r1 = _run_cli(root, "run", "--no-input")   # warms the cache
            self.assertEqual(r1.returncode, 0, r1.stderr)
            r2 = _run_cli(root, "run", "--strict", "--fast", "--no-input")
        self.assertNotEqual(
            r2.returncode, 0,
            "--fast cache hit skipped the strict gate: invalid books "
            "published at exit 0")
        self.assertIn("validation ERROR", r2.stderr)


class TestCryptoTickerMap(unittest.TestCase):
    """KNOWN_ISSUES "SYMBOL_OVERRIDES is hardcoded": a user-editable
    crypto_ticker.map now extends/overrides the built-in Yahoo
    collision disambiguations."""

    def test_map_file_merges_over_builtins(self):
        from taxjson.bin.fill_crypto_prices import load_symbol_overrides
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "crypto_ticker.map").write_text(
                "# my colliding coins\n"
                "PEPE PEPE24478   # collision with the other PEPE\n"
                "TAO  TAO99999\n"
                "garbage line without two fields extra\n")
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                merged = load_symbol_overrides([tmp])
        self.assertEqual(merged["PEPE"], "PEPE24478")   # added
        self.assertEqual(merged["TAO"], "TAO99999")     # overrides builtin
        self.assertEqual(merged["UNI"], "UNI7083")      # builtin kept
        self.assertIn("skipped", buf.getvalue())        # malformed warned


class TestShortSaleFormRendering(unittest.TestCase):
    """Deep-audit 2026-08 findings #1/#2: the engine's signed short
    convention (cost = -opening proceeds, proceeds = -cover cost) was
    rendered RAW on both filing forms — 8949 rows negated and
    column-swapped (Schedule D totals corrupted; column (h) survived
    only because the double error cancels), Schedule 3 rows swapped."""

    def _short_entry(self, **kw):
        # Short 100 @ $120 (opening proceeds 12,000), cover @ $100
        # (cost 10,000) → +2,000 gain. Engine convention: signed.
        from test_form_export import us_entry
        base = dict(symbol="TSLA.US", qty=100, proceeds=-10000.0,
                    cost=-12000.0, gain=2000.0, direction="SHORT",
                    term="SHORT_TERM")
        base.update(kw)
        return us_entry(**base)

    def test_8949_short_row_real_world_columns(self):
        from taxjson.bin.taxjson_form_export import build_8949
        rep = build_8949([self._short_entry()])
        row = rep["part_I"][0]
        self.assertAlmostEqual(
            row["proceeds"], 12000.0, places=2,
            msg="(d) must be the short-sale proceeds; the raw render "
                "printed -10,000 (the negated cover cost)")
        self.assertAlmostEqual(row["cost"], 10000.0, places=2)
        self.assertAlmostEqual(row["gain"], 2000.0, places=2)
        self.assertAlmostEqual(
            row["proceeds"] - row["cost"], row["gain"], places=2)

    def test_8949_schedule_d_totals_reconcile_with_broker(self):
        from taxjson.bin.taxjson_form_export import build_8949
        from test_form_export import us_entry
        rep = build_8949([
            self._short_entry(),
            us_entry(proceeds=6000.0, cost=5000.0),   # normal long sale
        ])
        t = rep["part_I_totals"]
        # 1099-B gross proceeds: 12,000 (short sale) + 6,000 = 18,000.
        self.assertAlmostEqual(t["proceeds"], 18000.0, places=2,
                               msg="the raw render totalled -4,000")
        self.assertAlmostEqual(t["cost"], 15000.0, places=2)
        self.assertAlmostEqual(t["gain"], 3000.0, places=2)


class TestFillCryptoSemantics(unittest.TestCase):
    """Deep-audit 2026-08 findings #5/#6: fill-crypto stamped Yahoo USD
    closes without fixing the row's currency (CAD-labeled rows booked
    USD FMV as CAD), and overwrote broker-exact net_amounts with
    daily-close estimates whenever price≈0."""

    def _fill(self, txs, cache):
        import io
        import json as _json
        import sys as _sys
        from unittest.mock import patch
        from taxjson.bin import fill_crypto_prices as fcp
        stdin = io.StringIO(_json.dumps({"transactions": txs}))
        stdout = io.StringIO()
        with patch.object(fcp, "load_cache", lambda: dict(cache)), \
                patch.object(fcp, "save_cache", lambda c: None), \
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


class TestRadarViolationRescueDeadline(unittest.TestCase):
    """Deep-audit 2026-08 #8: the VIOLATION advisory printed a sell-by
    date of loss+31, one day past the engine's held-at-end boundary
    (loss+30). Following the advisory to the letter left the loss
    disallowed. This test closes the loop: the date the radar prints
    must actually rescue the loss when followed."""

    BASE = [
        {"action": "BUYSELL", "date": "2025-01-15", "time": "09:30:00",
         "symbol": "XEI.TO", "quantity": 100.0, "net_amount": 1000.0,
         "currency": "CAD", "account": "m"},
        {"action": "BUYSELL", "date": "2025-02-02", "time": "09:30:00",
         "symbol": "XEI.TO", "quantity": -100.0, "net_amount": 800.0,
         "currency": "CAD", "account": "m"},
        {"action": "BUYSELL", "date": "2025-02-10", "time": "09:30:00",
         "symbol": "XEI.TO", "quantity": 100.0, "net_amount": 810.0,
         "currency": "CAD", "account": "m"},
    ]

    def _radar_violation_date(self):
        import json as _json
        import re as _re
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "t.json"
            t.write_text(_json.dumps({"transactions": self.BASE}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
                 "--taxable", str(t), "--date", "2025-02-20"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = _re.search(r"VIOLATION: Sell [\d.]+ shares \(globally\) by "
                       r"(\d{4}-\d{2}-\d{2})", r.stdout)
        self.assertIsNotNone(m, f"no VIOLATION advisory in:\n{r.stdout}")
        return m.group(1)

    def test_following_the_advisory_actually_rescues_the_loss(self):
        safe_date = self._radar_violation_date()
        # loss 2025-02-02 + 30 days = 2025-03-04 is the engine's SETTLE
        # boundary; the printed "sell by" is the last TRADE date that
        # settles inside it (T+1 → Mon 2025-03-03). Printing the settle
        # bound itself was the 2026-09 audit's one-lag-late defect.
        self.assertEqual(safe_date, "2025-03-03")
        # Follow the advisory: full exit TRADED on safe_date, settling
        # T+1 like a real broker row.
        from taxjson.lib.dates import settlement_date
        txs = [TaxTransaction(**t) for t in self.BASE]
        txs.append(TaxTransaction(
            action="BUYSELL", date=safe_date, time="09:30:00",
            date_settle=settlement_date(safe_date, "CAD"),
            symbol="XEI.TO", quantity=-100.0, net_amount=820.0,
            currency="CAD", account="m"))
        result = CanadaTaxRules().compute_gains(txs)
        self.assertAlmostEqual(
            result["summary"].get("total_disallowed", 0.0), 0.0,
            places=2,
            msg="selling on the radar's own printed deadline must "
                "rescue the loss — the +31 date left it disallowed")


class TestRepCapacityUnitsAcrossSplits(unittest.TestCase):
    """Deep-audit 2026-08 #4: the FIFO partial-pop decremented
    replacement capacity in TODAY's units while the capacity is stored
    in the rep's own trade-date units — with a split between the buy
    and the loss sale, wash quantities skewed both directions."""

    def _disallowed(self, txs):
        result = USATaxRules().compute_gains(txs)
        return sum(g.get('disallowed_amount', 0.0)
                   for g in result['transactions']
                   if g.get('raw_gain', 0.0) < 0)

    def test_forward_split_keeps_retained_share_disallowance(self):
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-02',
                           symbol='AAPL', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='SPLIT', date='2025-01-10',
                           symbol='AAPL', quantity=2.0, currency='USD',
                           account='M'),
            # Sell HALF the post-split position at a loss; the retained
            # 100 shares (50 rep-units × factor 2) are replacements.
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', quantity=-100.0, price=4.0,
                           net_amount=400.0, currency='USD', account='M'),
        ]
        control = [
            TaxTransaction(action='BUYSELL', date='2025-01-02',
                           symbol='AAPL', quantity=200.0, price=5.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='AAPL', quantity=-100.0, price=4.0,
                           net_amount=400.0, currency='USD', account='M'),
        ]
        d_split = self._disallowed(txs)
        d_control = self._disallowed(control)
        self.assertAlmostEqual(d_control, 100.0, places=2)
        self.assertAlmostEqual(
            d_split, 100.0, places=2,
            msg="raw-unit decrement zeroed the rep across a 2:1 split "
                "and the retained-share disallowance vanished")

    def test_reverse_split_does_not_overdisallow(self):
        txs = [
            # Lot A: 100 shares @ $1 (rep capacity 100 pre-split units).
            TaxTransaction(action='BUYSELL', date='2025-01-02',
                           time='09:30:00', symbol='ZZZ', quantity=100.0,
                           price=1.0, net_amount=100.0, currency='USD',
                           account='M'),
            # Lot B: 10 shares @ $10.
            TaxTransaction(action='BUYSELL', date='2025-01-03',
                           time='09:30:00', symbol='ZZZ', quantity=10.0,
                           price=10.0, net_amount=100.0, currency='USD',
                           account='M'),
            # 1:10 reverse split → lot A = 10 sh, lot B = 1 sh.
            TaxTransaction(action='SPLIT', date='2025-01-10',
                           symbol='ZZZ', quantity=0.1, currency='USD',
                           account='M'),
            # Sell 9 of lot A's 10 post-split shares at a $45 loss;
            # retained replacements = 1 sh (lot A) + 1 sh (lot B).
            TaxTransaction(action='BUYSELL', date='2025-01-15',
                           symbol='ZZZ', quantity=-9.0, price=5.0,
                           net_amount=45.0, currency='USD', account='M'),
        ]
        d = self._disallowed(txs)
        # Loss $45 over 9 shares = $5/sh; 2 replacement shares → $10.
        self.assertAlmostEqual(
            d, 10.0, places=2,
            msg="raw-unit decrement left phantom 9.1-share capacity on "
                "lot A and disallowed the full $45")


class TestSection1223TackingExcludesGap(unittest.TestCase):
    """Deep-audit 2026-08 #9: §1223(3) tacking inherited the wash-sold
    lot's calendar acquisition date, wrongly counting the sale→rebuy
    gap toward the holding period — flipping SHORT_TERM to LONG_TERM
    (rate category) near the anniversary."""

    def test_gap_days_do_not_count_toward_term(self):
        txs = [
            # Held 2025-01-02 → 2025-12-20 = 352 days, sold at a loss.
            TaxTransaction(action='BUYSELL', date='2025-01-02',
                           symbol='AAPL', quantity=100.0, price=20.0,
                           net_amount=2000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-12-20',
                           symbol='AAPL', quantity=-100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
            # Rebuy after a 21-day gap (wash replacement), then sell.
            TaxTransaction(action='BUYSELL', date='2026-01-10',
                           symbol='AAPL', quantity=100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2026-01-20',
                           symbol='AAPL', quantity=-100.0, price=16.0,
                           net_amount=1600.0, currency='USD', account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        final = next(g for g in result['transactions']
                     if g['date'] == '2026-01-20')
        # Tacked holding = 352 (prior) + 10 (own) = 362 days.
        self.assertEqual(
            final['term'], 'SHORT_TERM',
            "the raw-date inheritance counted the 21-day gap and "
            "reported LONG_TERM at 383 days")
        self.assertEqual(final['days_held'], 362)


class TestAssignmentPremiumNotHijacked(unittest.TestCase):
    """Deep-audit 2026-08: the option-assignment premium staged by the
    ASSIGN option leg was consumed by the FIRST same-symbol trade, not
    the assignment's own stock leg — an unrelated trade sorted between
    the two legs absorbed the premium, misstating both dispositions."""

    def test_canada_unrelated_sale_between_legs(self):
        txs = [
            # Hold 100 XYZ @ 4000 (ACB 40/sh).
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='XYZ.US', quantity=100.0, price=40.0,
                           net_amount=4000.0, currency='USD', account='M'),
            # Short put, premium +200; assigned.
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           time='10:00:00',
                           symbol='XYZ250620P00050000.US', quantity=-2.0,
                           price=1.0, net_amount=200.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-03-10',
                           time='09:00:00',
                           symbol='XYZ250620P00050000.US', quantity=2.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            # UNRELATED sale of 40 held shares at ACB (gain must be 0).
            TaxTransaction(action='BUYSELL', date='2025-03-10',
                           time='09:10:00', symbol='XYZ.US',
                           quantity=-40.0, price=40.0, net_amount=1600.0,
                           currency='USD', account='M'),
            # The assignment's own stock leg: buy 200 @ 50.
            TaxTransaction(action='ASSIGN', date='2025-03-10',
                           time='09:30:00', symbol='XYZ.US',
                           quantity=200.0, price=50.0, net_amount=10000.0,
                           currency='USD', account='M'),
        ]
        result = CanadaTaxRules().compute_gains(txs)
        sale = next(g for g in result['transactions']
                    if g.get('qty') == 40.0 and g.get('action') != 'DIVIDEND')
        self.assertAlmostEqual(
            sale['gain'], 0.0, places=2,
            msg="the unrelated sale absorbed the staged put premium "
                "(buggy gain was +200)")

    def test_usa_unrelated_buy_between_legs(self):
        txs = [
            # Short put, premium +500; assigned.
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           time='10:00:00',
                           symbol='XYZ250620P00100000.US', quantity=-1.0,
                           price=5.0, net_amount=500.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-03-10',
                           time='09:00:00',
                           symbol='XYZ250620P00100000.US', quantity=1.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            # UNRELATED buy at market.
            TaxTransaction(action='BUYSELL', date='2025-03-10',
                           time='09:30:00', symbol='XYZ.US',
                           quantity=100.0, price=100.0,
                           net_amount=10000.0, currency='USD',
                           account='M'),
            # Assignment stock leg: buy 100 @ strike.
            TaxTransaction(action='ASSIGN', date='2025-03-10',
                           time='16:00:00', symbol='XYZ.US',
                           quantity=100.0, price=100.0,
                           net_amount=10000.0, currency='USD',
                           account='M'),
            # Sell the FIRST lot (FIFO) — must realize gain 1000, not
            # 1500 (basis 10000, not premium-reduced 9500).
            TaxTransaction(action='BUYSELL', date='2025-04-01',
                           symbol='XYZ.US', quantity=-100.0, price=110.0,
                           net_amount=11000.0, currency='USD',
                           account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        sale = next(g for g in result['transactions']
                    if g['date'] == '2025-04-01')
        self.assertAlmostEqual(
            sale['gain'], 1000.0, places=2,
            msg="the unrelated 09:30 buy absorbed the premium and its "
                "lot's basis dropped to 9,500 (buggy gain +1,500)")


class TestCashSettledAssignmentRealized(unittest.TestCase):
    """Deep-audit 2026-08 #3: a cash-settled index option ASSIGN
    (XSP/SPX — the underlying never trades as stock) staged its premium
    for a stock leg that structurally cannot exist; the option's entire
    P&L vanished from the results with only a stderr warning."""

    CANADA_TXS = [
        # Short 1 XSP put, premium +300.
        dict(action='BUYSELL', date='2025-01-10', time='10:00:00',
             symbol='XSP250620P00068500.US', quantity=-1.0, price=3.0,
             net_amount=300.0, currency='USD', account='M'),
        # Cash-settled assignment: pay out 450.
        dict(action='ASSIGN', date='2025-06-20', time='16:00:00',
             symbol='XSP250620P00068500.US', quantity=1.0, price=4.5,
             net_amount=450.0, currency='USD', account='M'),
    ]

    def test_canada_realizes_the_option_pl(self):
        txs = [TaxTransaction(**t) for t in self.CANADA_TXS]
        result = CanadaTaxRules().compute_gains(txs)
        gains = [g for g in result['transactions']
                 if g.get('action') != 'DIVIDEND']
        self.assertEqual(
            len(gains), 1,
            "the cash-settled close vanished from the results entirely")
        self.assertAlmostEqual(gains[0]['gain'], -150.0, places=2)

    def test_usa_realizes_the_option_pl(self):
        txs = [TaxTransaction(**t) for t in self.CANADA_TXS]
        result = USATaxRules().compute_gains(txs)
        closes = [g for g in result['transactions']
                  if g.get('raw_gain') is not None]
        self.assertEqual(len(closes), 1)
        self.assertAlmostEqual(closes[0]['raw_gain'], -150.0, places=2)

    def test_real_stock_assignment_still_rolls_premium(self):
        """Guard the fix's boundary: an assignment whose underlying DOES
        trade keeps the premium-roll behavior."""
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-10',
                           time='10:00:00',
                           symbol='AAPL250117P00140000.US', quantity=-1.0,
                           price=2.0, net_amount=200.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-01-17',
                           time='16:00:00',
                           symbol='AAPL250117P00140000.US', quantity=1.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-01-17',
                           time='16:00:01', symbol='AAPL.US',
                           quantity=100.0, price=140.0,
                           net_amount=14000.0, currency='USD',
                           account='M'),
            TaxTransaction(action='BUYSELL', date='2025-03-01',
                           symbol='AAPL.US', quantity=-100.0, price=140.0,
                           net_amount=14000.0, currency='USD',
                           account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        sale = next(g for g in result['transactions']
                    if g['date'] == '2025-03-01')
        # Basis 14,000 − 200 premium = 13,800 → gain +200.
        self.assertAlmostEqual(sale['raw_gain'], 200.0, places=2)


class TestTickerMapSymbolNew(unittest.TestCase):
    """Deep-audit 2026-08 #10: the ticker map never remapped
    symbol_new, so a merger SPLIT renamed the source pool into an
    ORPHAN identity while the acquirer's trades mapped elsewhere —
    phantom short plus stranded ACB."""

    def test_split_target_follows_the_mapping(self):
        from taxjson.bin.taxjson_ticker_map import apply_mapping
        tx = TaxTransaction(action='SPLIT', date='2025-06-01',
                            symbol='HES.US', symbol_new='CVX.US',
                            quantity=1.0, currency='USD', account='M')
        apply_mapping(tx, {'CVX.US': 'CVX.TO', 'HES.US': 'HES.TO'})
        self.assertEqual(tx.symbol, 'HES.TO')
        self.assertEqual(
            tx.symbol_new, 'CVX.TO',
            "symbol_new kept the unmapped identity: the renamed pool "
            "orphans while CVX.TO sales find no basis")

    def test_drop_removes_renames_targeting_dropped_ticker(self):
        import io
        from contextlib import redirect_stderr
        from taxjson.bin.taxjson_ticker_map import apply_drops
        txs = [
            TaxTransaction(action='SPLIT', date='2025-06-01',
                           symbol='KEEP.TO', symbol_new='GONE.TO',
                           quantity=1.0, currency='CAD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-06-02',
                           symbol='KEEP.TO', quantity=10.0, price=1.0,
                           net_amount=10.0, currency='CAD', account='M'),
        ]
        with redirect_stderr(io.StringIO()):
            kept = apply_drops(txs, {'GONE.TO'})
        self.assertEqual([t.action for t in kept], ['BUYSELL'])


class TestKrakenLegacyZQuotePairs(unittest.TestCase):
    """Deep-audit 2026-08: the generic concatenation regex greedily ate
    the Z of a Z-prefixed fiat quote — USDTZUSD parsed as garbage
    symbol 'USDTZ' that fill-crypto could never price."""

    def test_stablecoin_and_fiat_z_quotes(self):
        from taxjson.lib.brokerages.kraken import _split_pair
        self.assertEqual(_split_pair('USDTZUSD'), ('USDT', 'USD'))
        self.assertEqual(_split_pair('ZUSDZCAD'), ('USD', 'CAD'))

    def test_tezos_not_misparsed(self):
        from taxjson.lib.brokerages.kraken import _split_pair
        # XTZ ends in Z — the explicit-base restriction keeps it out of
        # the Z-quote branch.
        self.assertEqual(_split_pair('XTZUSD'), ('XTZ', 'USD'))


class TestCarrybackCapacityKeysOnTargetYear(unittest.TestCase):
    """Deep-audit 2026-08: a T1A carryback recorded via --claimed under
    its TARGET year still had that year's carryback capacity intact, so
    the ledger re-suggested the same carryback — following the report
    filed a duplicate T1A."""

    def test_recorded_carryback_not_resuggested(self):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        nets = {
            2022: {"net": 5000.0, "dispositions": 1},
            2025: {"net": -8000.0, "dispositions": 1},
        }
        ledger = build_canada_ledger(nets, {2022: 5000.0})
        row_2025 = next(r for r in ledger["rows"] if r["year"] == 2025)
        cb_to_2022 = [c for c in row_2025["carryback_candidates"]
                      if c["year"] == 2022]
        self.assertEqual(
            cb_to_2022, [],
            "the 2022 gains are already sheltered by the recorded "
            "claim — suggesting another 5,000 carryback duplicates "
            "the filed T1A")
        self.assertAlmostEqual(ledger["final_carryforward"], 3000.0,
                               places=2)


class TestMediumSeverityDeepAuditBatch(unittest.TestCase):
    """Deep-audit 2026-08 medium fixes: post-drain ROC warning, ratio-0
    rename migration, short-side wash_deferred visibility, T1135
    tainted filter."""

    def test_post_drain_roc_adjust_warns(self):
        import io
        from contextlib import redirect_stderr
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='XRE.TO', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='CAD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-05',
                           symbol='XRE.TO', quantity=-100.0, price=11.0,
                           net_amount=1100.0, currency='CAD', account='M'),
            # ROC posted AFTER the full exit.
            TaxTransaction(action='ADJUST', date='2025-03-01',
                           symbol='XRE.TO', quantity=0.0,
                           net_amount=-60.0, currency='CAD', account='M',
                           type='roc'),
        ]
        buf = io.StringIO()
        with redirect_stderr(buf):
            CanadaTaxRules().compute_gains(txs)
        self.assertIn("EMPTY pool", buf.getvalue())

    def test_ratio_zero_rename_still_migrates(self):
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='OLD.US', quantity=100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='SPLIT', date='2025-02-01',
                           symbol='OLD.US', symbol_new='NEW.US',
                           quantity=0.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-03-01',
                           symbol='NEW.US', quantity=-100.0, price=12.0,
                           net_amount=1200.0, currency='USD', account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        closes = [g for g in result['transactions']
                  if g.get('raw_gain') is not None]
        self.assertEqual(len(closes), 1,
                         "the ratio-0 rename was dropped: no gain "
                         "realized, book forked into long+phantom short")
        self.assertAlmostEqual(closes[0]['raw_gain'], 200.0, places=2)
        self.assertFalse(result.get('inventory'),
                         "no residual position may survive the rename")

    def test_short_wash_deferred_visible_in_inventory(self):
        txs = [
            # Short-open A, cover at a loss; replacement short B stays
            # open with the deferral embedded.
            TaxTransaction(action='BUYSELL', date='2025-01-05',
                           symbol='AAPL', quantity=-100.0, price=10.0,
                           net_amount=1000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-01-20',
                           time='09:30:00', symbol='AAPL',
                           quantity=-100.0, price=11.0,
                           net_amount=1100.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-02-01',
                           symbol='AAPL', quantity=100.0, price=12.0,
                           net_amount=1200.0, currency='USD', account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        inv = next(r for r in result['inventory'] if r['qty'] < 0)
        self.assertGreater(
            inv.get('deferred_wash', 0.0), 0.0,
            "the short lot's embedded §1091 deferral was invisible "
            "(long-side lots already reported theirs)")

    def test_t1135_excludes_tainted_gains(self):
        import io
        import json as _json
        from contextlib import redirect_stderr
        from taxjson.bin.taxjson_t1135 import join_income_gains
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "g.json"
            p.write_text(_json.dumps({"transactions": [
                {"date": "2025-05-01", "symbol": "AAPL.US", "qty": -10,
                 "gain": 5000.0, "tainted": True},
                {"date": "2025-06-01", "symbol": "AAPL.US", "qty": -10,
                 "gain": 100.0},
            ]}))
            buf = io.StringIO()
            with redirect_stderr(buf):
                out = join_income_gains([p], 2025)
        self.assertAlmostEqual(out["AAPL.US"]["gain"], 100.0, places=2,
                               msg="the phantom-basis 5,000 leaked into "
                                   "the T1135 gain column")
        self.assertIn("EXCLUDED", buf.getvalue())


class TestLowSeverityDeepAuditTail(unittest.TestCase):
    """Deep-audit 2026-08 low-severity tail: Kraken duplicate-refid leg
    accumulation and negative-fee netting in sum_gains."""

    def test_kraken_split_settlement_legs_accumulate(self):
        from test_parser_activity_coverage import _parse_csv
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
        from taxjson.bin.taxjson_sum_gains import summarize_gains
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


class TestDeferredLowTail(unittest.TestCase):
    """Final deferred items from the 2026-08 deep audit."""

    def test_8949_date_acquired_is_real_purchase_date(self):
        from taxjson.bin.taxjson_form_export import build_8949
        txs = [
            TaxTransaction(action='BUYSELL', date='2025-01-02',
                           symbol='AAPL', quantity=100.0, price=20.0,
                           net_amount=2000.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2025-12-20',
                           symbol='AAPL', quantity=-100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2026-01-10',
                           symbol='AAPL', quantity=100.0, price=15.0,
                           net_amount=1500.0, currency='USD', account='M'),
            TaxTransaction(action='BUYSELL', date='2026-01-20',
                           symbol='AAPL', quantity=-100.0, price=16.0,
                           net_amount=1600.0, currency='USD', account='M'),
        ]
        result = USATaxRules().compute_gains(txs)
        final = next(g for g in result['transactions']
                     if g['date'] == '2026-01-20')
        rep = build_8949([final])
        row = (rep['part_I'] + rep['part_II'])[0]
        # Column (b): the replacement lot's REAL buy date (broker
        # convention), not the §1223(3)-tacked date the derived
        # fallback produced (2025-01-23-ish).
        self.assertEqual(row['date_acquired'], '2026-01-10')
        self.assertEqual(final['term'], 'SHORT_TERM')  # tacking intact

    def test_tt_roundtrip_preserves_reversal_signs(self):
        from taxjson.bin.taxjson_convert_tt import (parse_tt_line,
                                                    tx_to_tt_line)
        tx = {'action': 'BUYSELL', 'date': '2025-03-01',
              'time': '09:30:00', 'symbol': 'A.TO', 'quantity': -10.0,
              'currency': 'CAD', 'price': 5.0, 'net_amount': -50.0,
              'fee': -1.0}
        back = parse_tt_line(tx_to_tt_line(tx))
        self.assertAlmostEqual(back['net_amount'], -50.0, places=2,
                               msg="abs() re-inflated the reversal")
        self.assertAlmostEqual(back['fee'], -1.0, places=2)

    def test_full_run_clears_orphaned_wash_artifacts(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for acct in ("margin", "rrsp"):
                (root / "inputs" / acct).mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
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
            # Sheltered inputs vanish by DATA (config untouched).
            shutil.rmtree(root / "inputs" / "rrsp")
            r2 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertFalse(
            wash.exists(),
            "the orphaned wash artifact kept shadowing fresh plain "
            "gains (its sheltered context no longer exists)")


class TestReAuditRegressions(unittest.TestCase):
    """Fixes for regressions the 2026-08 verification re-audit found in
    the fix-session diff itself."""

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
                           symbol='QQQ250620P00050000.US', quantity=-1.0,
                           price=1.0, net_amount=100.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-02-01',
                           time='09:00:00',
                           symbol='QQQ250620P00050000.US', quantity=1.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-02-01',
                           time='16:00:00', symbol='QQQ.US',
                           quantity=100.0, price=50.0, net_amount=5000.0,
                           currency='USD', account='M'),
            # June plain-convention assignment (second broker).
            TaxTransaction(action='BUYSELL', date='2025-05-05',
                           symbol='QQQ250820P00060000.US', quantity=-1.0,
                           price=6.0, net_amount=600.0, currency='USD',
                           account='M'),
            TaxTransaction(action='ASSIGN', date='2025-06-15',
                           time='09:00:00',
                           symbol='QQQ250820P00060000.US', quantity=1.0,
                           price=0.0, net_amount=0.0, currency='USD',
                           account='M'),
            TaxTransaction(action='BUYSELL', date='2025-06-15',
                           time='16:00:00', symbol='QQQ.US',
                           quantity=100.0, price=60.0, net_amount=6000.0,
                           currency='USD', account='M'),
            # Liquidate everything at cost-neutral prices; the premiums
            # are the only P&L.
            TaxTransaction(action='BUYSELL', date='2025-09-01',
                           symbol='QQQ.US', quantity=-200.0, price=55.0,
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
        from test_audit_2026_08_fixes import TestFillCryptoSemantics as T
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
        from test_parser_activity_coverage import _parse_csv
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
        from test_parser_activity_coverage import _parse_csv
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        csv = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
               "L1,R3,2025-01-15 10:05:00,spend,,currency,ZUSD,,-50,0,0\n"
               "L2,R3,2025-01-15 10:05:01,spend,,currency,ZEUR,,-20,0.30,0\n"
               "L3,R3,2025-01-15 10:05:02,receive,,currency,XXBT,,0.001,0,0\n")
        buf = io.StringIO()
        with redirect_stderr(buf):
            _parse_csv(KrakenBrokerage, csv)
        self.assertIn("TWO assets", buf.getvalue(),
                      "the USD leg was silently overwritten by the EUR "
                      "leg with no signal")

    def test_pending_elections_preserve_wash_artifacts(self):
        # A full run whose SHELTERED account defers on corp-action
        # elections must NOT destroy the taxable account's last-good
        # wash artifacts (the run exits 3; context is transient).
        import shutil
        from test_pending_elections import _SSL_RGLD_CSV
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for acct in ("margin", "rrsp"):
                (root / "inputs" / acct).mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
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
    test_generic_importer.py."""

    def _kraken_ledger(self, rows):
        import io
        from contextlib import redirect_stderr
        from test_parser_activity_coverage import _parse_csv
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
        self.assertIn("2 trade row(s) ignored", err)
        self.assertIn("supply the trades export", err)
        self.assertIn("not parsed", err)
        self.assertNotIn("trade x2", err)

    def test_coinbase_convert_without_subtotal_still_backfillable(self):
        # A Convert row with no usable Subtotal ships price=0 AND
        # net=0 on both legs so taxjson-fill-crypto backfills FMV —
        # the fee must not leak into the buy leg's net there (fill
        # derives price from any nonzero net_amount).
        from test_parser_activity_coverage import _parse_csv
        from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
        txs = _parse_csv(CoinbaseBrokerage, _CB_HEADER + (
            'CB-C-3,2024-05-01 10:00:00 UTC,Convert,BTC,0.05000000,USD,'
            ',,,10.00,Converted 0.05000000 BTC to 1.20000000 ETH\n'))
        self.assertEqual(len(txs), 2)
        for t in txs:
            self.assertEqual(t['price'], 0.0)
            self.assertEqual(t['net_amount'], 0.0)

    _RBC_HEADER = ('Date,Activity,Symbol,Description,Quantity,Price,'
                   'Settlement Date,Currency,Value,Amount\n')

    def _rbc(self, rows):
        from test_parser_activity_coverage import _parse_csv
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        return _parse_csv(RbcBrokerage, self._RBC_HEADER + rows)

    def test_rbc_net_tax_qty_derived_from_gross(self):
        # Description states the DECLARED (gross) $1.00/sh rate; the
        # $85 Amount is net of 15% withholding. Deriving qty from NET
        # booked qty 85 for a 100-share position.
        txs = self._rbc(
            '06/01/2024,Dividends,MSFT,MICROSOFT NON-RES TAX WITHHELD '
            'CASH DIV $1.00 PER SHARE,0,0.00,06/01/2024,USD,0.00,85.00\n')
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
            '06/01/2024,Dividends,MSFT,MICROSOFT NON-RES TAX WITHHELD '
            'CASH DIV ON 100 SHS,0,0.00,06/01/2024,USD,0.00,85.00\n')
        div = next(t for t in txs if t['action'] == 'DIVIDEND')
        self.assertAlmostEqual(div['quantity'], 100.0, places=6)
        self.assertAlmostEqual(div['price'], 1.0, places=6,
                               msg="per-share rate derived from NET "
                                   "(0.85) instead of GROSS (1.00)")
        self.assertAlmostEqual(div['gross_amount'], 100.0, places=2)

    def test_rbc_plain_dividend_derivation_unchanged(self):
        # No withholding: gross == net, derivation identical to before.
        txs = self._rbc(
            '03/01/2024,Dividends,RY,ROYAL BANK CASH DIV ON 100 SHS,'
            '0,0.00,03/01/2024,CAD,0.00,138.00\n')
        div = next(t for t in txs if t['action'] == 'DIVIDEND')
        self.assertAlmostEqual(div['quantity'], 100.0, places=6)
        self.assertAlmostEqual(div['price'], 1.38, places=6)
        self.assertAlmostEqual(div['gross_amount'], 138.0, places=2)


if __name__ == '__main__':
    unittest.main()
