"""Re-audit-2 fixes in the US engine (lib/core.USATaxRules).

All data is synthetic.
"""
import contextlib
import io
import time
import unittest

from taxjson.lib.core import TaxTransaction, USATaxRules
from tax_rules import rule


def _t(d, q, p, *, sym='XYZ.US', acct='T1', tm='10:00:00', action='BUYSELL',
       **kw):
    return TaxTransaction(action=action, date=d, date_settle=d, time=tm,
                          symbol=sym, quantity=q, price=p,
                          net_amount=round(abs(q * p), 6), currency='USD',
                          account=acct, **kw)


def _us(rows, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return USATaxRules().compute_gains(rows, **kw)


class TestSameSaleNoCascade(unittest.TestCase):
    """A2-0001 / A2-0017 / A2-0553: shares (or shorts) closed by the SAME
    sale (cover) are never replacements for each other's losses — no
    cascade, no fragmentation, no compounding of the tacked holding
    period."""

    @rule("US-WASH-17")
    def test_tiny_old_lot_whole_position_sold(self):
        # A2-0001: A 1 sh @100 (2024), B 100 @50 (2025-02-20), sell 101 @40.
        for a in (1.0, 0.001):
            rows = [_t('2024-06-03', a, 100), _t('2025-02-20', 100, 50),
                    _t('2025-03-03', -(100 + a), 40)]
            t0 = time.time()
            r = _us(rows)
            self.assertLess(time.time() - t0, 2.0)
            tr = r['transactions']
            self.assertEqual(len(tr), 2, a)
            self.assertEqual(r['wash_sales'], [])
            self.assertAlmostEqual(r['summary']['total_disallowed'], 0.0)
            self.assertAlmostEqual(r['summary']['total_gain'],
                                   -(a * 60 + 1000), places=4)
            self.assertTrue(all(x['term'] == 'SHORT_TERM' for x in tr))

    @rule("US-WASH-17")
    def test_buy_time_split_seed(self):
        # A2-0017: a washed fraction seeds the buy-time sub-lot; the whole
        # replacement is then sold in one sale: 2 rows, no new wash sale.
        for a in (1.0, 0.001):
            rows = [_t('2024-06-03', a, 60), _t('2025-03-03', -a, 40),
                    _t('2025-03-10', 100, 45), _t('2025-03-20', -100, 40)]
            r = _us(rows)
            w = [x for x in r['transactions'] if x['date'] == '2025-03-20']
            self.assertEqual(len(w), 2, a)
            self.assertTrue(all(x['disallowed_amount'] == 0 for x in w))
            self.assertEqual(len(r['wash_sales']), 1)
            self.assertAlmostEqual(r['summary']['total_gain'],
                                   -(a * 20 + 500), places=4)

    @rule("US-WASH-17")
    def test_one_cover_closes_two_shorts(self):
        # A2-0553 short side: one buy-to-cover of 200 closes an old short
        # and one opened 4 days earlier; neither replaces the other.
        rows = [_t('2026-01-02', -100, 50), _t('2026-03-02', -100, 45),
                _t('2026-03-06', 200, 60)]
        r = _us(rows)
        self.assertEqual(r['wash_sales'], [])
        self.assertAlmostEqual(r['summary']['total_disallowed'], 0.0)
        self.assertAlmostEqual(r['summary']['total_gain'], -2500.0)

    @rule("US-WASH-06", "US-WASH-17")
    def test_retained_shares_of_same_purchase_still_replace(self):
        # US-WASH-06 is kept: shares KEPT after the sale (bought within 30
        # days before it) are replacements. A 100 old, B 100 recent; sell
        # A and 50 of B: A's loss washes into B's 50 retained shares.
        rows = [_t('2024-06-03', 100, 100), _t('2025-02-20', 100, 50),
                _t('2025-03-03', -150, 40)]
        r = _us(rows)
        self.assertAlmostEqual(r['summary']['total_disallowed'], 3000.0,
                               places=4)
        self.assertEqual(len(r['transactions']), 2)

    @rule("US-WASH-17")
    def test_same_second_fills_of_one_order(self):
        # A full exit split into two fills at one timestamp is one
        # disposition: no wash between the fills.
        rows = [_t('2024-06-03', 100, 100), _t('2026-03-02', 100, 55),
                _t('2026-03-06', -150, 40, tm='10:31:07'),
                _t('2026-03-06', -50, 40, tm='10:31:07')]
        r = _us(rows)
        self.assertAlmostEqual(r['summary']['total_disallowed'], 0.0)
        self.assertEqual(r['wash_sales'], [])


class TestFullExitPlanningAgrees(unittest.TestCase):
    """A2-0553: the radar's US EXITABLE advice ("a full exit is fine")
    now matches the engine, and says the exit must be one order."""

    @rule("US-WASH-17", "US-PLAN-01")
    def test_engine_full_exit_one_sale_not_washed(self):
        rows = [_t('2024-06-03', 100, 100), _t('2026-03-02', 100, 55),
                _t('2026-03-06', -200, 40)]
        r = _us(rows)
        self.assertEqual(r['wash_sales'], [])
        st = [x for x in r['transactions'] if x['acquired_date']
              == '2026-03-02']
        self.assertEqual(st[0]['term'], 'SHORT_TERM')

    @rule("US-PLAN-01")
    def test_radar_us_exitable_says_one_order(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from test_fix_planning import REPO_ROOT, _row
        tax = [_row("2024-06-03", "XYZ.US", 100, 10000.0, currency="USD"),
               _row("2026-09-28", "XYZ.US", 100, 5500.0, currency="USD")]
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "margin_base.json"
            t.write_text(json.dumps({"transactions": tax}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
                 "--country", "usa", "--taxable", str(t), "--date",
                 "2026-10-01", "--all", "--json"], cwd=REPO_ROOT,
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {row["ticker"]: row for sec in json.loads(r.stdout)["sections"]
                for row in sec["rows"]}
        adv = rows["XYZ.US"]["advisory"]
        self.assertIn("EXITABLE", adv)
        self.assertIn("in one order at a loss is fine now", adv)


if __name__ == '__main__':
    unittest.main()
