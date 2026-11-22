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

from taxjson.engine.us.taxrules import USATaxRules
from taxjson.model.transaction import TaxTransaction
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


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


class TestReconcileSlipsDateBasis(unittest.TestCase):
    """Audit 2026-08 #9: `load_computed` year-filtered on settlement
    date unconditionally, but USA gains files and Form 8949 are scoped
    by TRADE date — a Dec-31 sale settling Jan-2 was on the 1099-B but
    missing from the computed side, producing a spurious mismatch.

    Formerly: test_audit_2026_08_fixes.TestReconcileSlipsDateBasis"""

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
        from taxjson.lib.slip_symbols import load_computed
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


class TestShortSaleFormRendering(unittest.TestCase):
    """Deep-audit 2026-08 findings #1/#2: the engine's signed short
    convention (cost = -opening proceeds, proceeds = -cover cost) was
    rendered RAW on both filing forms — 8949 rows negated and
    column-swapped (Schedule D totals corrupted; column (h) survived
    only because the double error cancels), Schedule 3 rows swapped.

    Formerly: test_audit_2026_08_fixes.TestShortSaleFormRendering"""

    def _short_entry(self, **kw):
        # Short 100 @ $120 (opening proceeds 12,000), cover @ $100
        # (cost 10,000) → +2,000 gain. Engine convention: signed.
        from _support.us_form_entries import us_entry
        base = dict(symbol="TSLA.US", qty=100, proceeds=-10000.0,
                    cost=-12000.0, gain=2000.0, direction="SHORT",
                    term="SHORT_TERM")
        base.update(kw)
        return us_entry(**base)

    def test_8949_short_row_real_world_columns(self):
        from taxjson.lib.tax_forms import build_8949
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

    def test_8949_short_closed_by_delivery_shows_the_lots_date(self):
        # Column (b): a plain short sale shows the cover date in both
        # date columns; one closed by delivering a lot held (a move
        # between your accounts: the engine's `closed_by_delivery`)
        # shows the date that lot was acquired (backlog TJ-3451).
        from taxjson.lib.tax_forms import build_8949
        plain = self._short_entry(date="2025-03-03",
                                  acquired_date="2025-01-03")
        moved = self._short_entry(date="2025-03-03", term="LONG_TERM",
                                  acquired_date="2023-06-01",
                                  days_held=641, closed_by_delivery=True)
        rep = build_8949([plain, moved])
        self.assertEqual((rep["part_I"][0]["date_acquired"],
                          rep["part_I"][0]["date_sold"]),
                         ("2025-03-03", "2025-03-03"))
        self.assertEqual((rep["part_II"][0]["date_acquired"],
                          rep["part_II"][0]["date_sold"]),
                         ("2023-06-01", "2025-03-03"))

    def test_manual_row_of_a_delivery_cover_shows_the_shorts_proceeds(self):
        # A short closed by moved shares of unknown cost: what the short
        # sale brought in is known (the run's `short_sale_proceeds`), the
        # cost of the shares is not. The manual row shows the proceeds
        # and an unknown cost; a short covered with no short sale in the
        # files still shows its cover cost and unknown proceeds (backlog
        # TJ-3465: the delivery row read PROCEEDS unknown, COVER COST 0).
        from taxjson.bin.taxjson_form_export import (_manual_table,
                                                     manual_figures)
        moved = {"symbol": "ZZQ.US", "date": "2025-03-03", "qty": 5.0,
                 "proceeds": 0.0, "direction": "SHORT", "account": "qb",
                 "closed_by_delivery": True, "short_sale_proceeds": 60.0}
        plain = {"symbol": "ZZQ.US", "date": "2025-03-04", "qty": 5.0,
                 "proceeds": -45.0, "direction": "SHORT", "account": "qb"}
        self.assertEqual(manual_figures(moved), (60.0, None))
        self.assertEqual(manual_figures(plain), (None, 45.0))
        table = "\n".join(_manual_table([moved, plain]))
        self.assertRegex(table, r"2025-03-03\s+5\s+60\.00\s+unknown\s+qb")
        self.assertRegex(table, r"2025-03-04\s+5\s+unknown\s+45\.00\s+qb")

    def test_8949_schedule_d_totals_reconcile_with_broker(self):
        from taxjson.lib.tax_forms import build_8949
        from _support.us_form_entries import us_entry
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


class TestDeferredLowTail(unittest.TestCase):
    """Final deferred items from the 2026-08 deep audit.

    Formerly: test_audit_2026_08_fixes.TestDeferredLowTail"""

    def test_8949_date_acquired_is_real_purchase_date(self):
        from taxjson.lib.tax_forms import build_8949
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
        from taxjson.lib.tt_format import parse_tt_line, tx_to_tt_line
        # The fee sign (a rebate) must survive json->tt->json.
        tx = {'action': 'BUYSELL', 'date': '2025-03-01',
              'time': '09:30:00', 'symbol': 'A.TO', 'quantity': -10.0,
              'currency': 'CAD', 'price': 5.0, 'net_amount': 51.0,
              'fee': -1.0}
        back = parse_tt_line(tx_to_tt_line(tx))
        self.assertAlmostEqual(back['net_amount'], 51.0, places=2)
        self.assertAlmostEqual(back['fee'], -1.0, places=2)
        # A NEGATIVE sell total used to round-trip as negative proceeds
        # (audit R1-117: a +1,000 gain became a -3,000 loss). The .tt
        # reader takes a negative sell total only when the line's
        # commission explains it (a commission-over-gross close,
        # A2-0622); any other one is refused loudly on the way back in.
        # (The schema accepts a negative SELL net since S017-00; the
        # refusal is the reader's — re-audit A2-1483.)
        tx['net_amount'] = -50.0
        with self.assertRaises(ValueError):
            parse_tt_line(tx_to_tt_line(tx))
        # ...while a commission-over-gross penny close round-trips.
        penny = {'action': 'BUYSELL', 'date': '2025-06-16',
                 'time': '09:30:00', 'symbol': 'ZZZ250620C00050000.US',
                 'quantity': -1.0, 'currency': 'USD', 'price': 0.01,
                 'net_amount': -8.95, 'commission': 9.95}
        back = parse_tt_line(tx_to_tt_line(penny))
        self.assertAlmostEqual(back['net_amount'], -8.95, places=2)
        self.assertAlmostEqual(back['fee'], 9.95, places=2)

    def test_full_run_clears_orphaned_wash_artifacts(self):
        import shutil
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
            # Sheltered inputs vanish by DATA (config untouched).
            shutil.rmtree(root / "inputs" / "rrsp")
            r2 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertFalse(
            wash.exists(),
            "the orphaned wash artifact kept shadowing fresh plain "
            "gains (its sheltered context no longer exists)")


if __name__ == '__main__':
    unittest.main()
