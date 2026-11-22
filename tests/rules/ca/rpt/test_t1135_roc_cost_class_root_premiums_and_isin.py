"""Re-audit-2 fixes, filing-reports list A (A2-...): taxjson-audit and
taxjson-explain recompute the books the way run_gains does (income
re-dating, option grant basis, US per-account FIFO, deemed gains,
phantom-basis manual rows, declared contract size), and the T1135 cost
walk follows the engine.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_a
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from _support.filing_report_books import ACCT, _quiet


# ------------------------------------------------------------------ T1135

def _t(action="BUYSELL", date="2025-01-15", symbol="QZA.US", qty=0.0,
       net=0.0, settle=None, currency="CAD", acct=ACCT, **extra):
    d = {"action": action, "date": date, "date_settle": settle or date,
         "time": "09:30:00", "symbol": symbol, "quantity": qty,
         "net_amount": net, "symbol_new": "", "currency": currency,
         "account": acct}
    d.update(extra)
    return d


def _t1135(argv):
    from taxjson.bin.taxjson_t1135 import main
    return _quiet(main, argv)


class TestT1135CostWalk(unittest.TestCase):
    """Formerly: test_fix_a2_filrep_a.TestT1135CostWalk"""

    def walk(self, rows, year=2025, today=None):
        from taxjson.bin.taxjson_t1135 import walk_costs
        return walk_costs(rows, year, {}, "settle", today=today)

    @rule(R.CA_RPT_12)
    def test_a2_0034_empty_pool_roc_does_not_reach_the_rebuy(self):
        rows = [_t(date="2024-06-03", qty=1000, net=-100000.0),
                _t(date="2025-02-03", qty=-1000, net=100000.0),
                _t("ADJUST", "2025-03-31", qty=0, net=-8000.0, type="roc"),
                _t(date="2025-05-01", qty=1000, net=-105000.0)]
        w = self.walk(rows)
        self.assertEqual(w["max_total_cost"], 105000.0)
        self.assertEqual(w["per_symbol"]["QZA.US"]["year_end_cost"],
                         105000.0)

    @rule(R.CA_RPT_12)
    def test_a2_0115_roc_beyond_acb_leaves_cost_nil(self):
        rows = [_t(date="2025-01-03", qty=1000, net=-1000.0),
                _t("ADJUST", "2025-02-03", qty=0, net=-1500.0, type="roc"),
                _t(date="2025-03-03", qty=10050, net=-100500.0)]
        w = self.walk(rows)
        self.assertEqual(w["max_total_cost"], 100500.0)

    @rule(R.CA_RPT_12)
    def test_a2_0115_cli_verdict_flips_to_required(self):
        rows = [_t(date="2025-01-03", qty=1000, net=-1000.0),
                _t("ADJUST", "2025-02-03", qty=0, net=-1500.0, type="roc"),
                _t(date="2025-03-03", qty=10050, net=-100500.0)]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin_base.json"
            p.write_text(json.dumps({"transactions": rows}))
            rc, out, err = _t1135([str(p), "--year", "2025", "--json",
                                   "--year-wash-only"])
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["filing_required"])

    @rule(R.CA_RPT_12)
    def test_a2_0321_basis_increase_and_wash_still_apply(self):
        # A positive ADJUST (a notional distribution) on an empty pool
        # joins the next purchase (CA-ACB-13), as in the engine.
        rows = [_t(date="2025-01-03", qty=10, net=-1000.0),
                _t(date="2025-02-03", qty=-10, net=1100.0),
                _t("ADJUST", "2025-03-03", qty=0, net=50.0),
                _t(date="2025-04-03", qty=10, net=-1000.0)]
        w = self.walk(rows)
        self.assertEqual(w["per_symbol"]["QZA.US"]["year_end_cost"],
                         1050.0)

    @rule(R.CA_RPT_12)
    def test_a2_1106_class_root_call_premium_folds(self):
        rows = [_t(date="2025-01-03", symbol="BRKB250620C00050000.US",
                   qty=1, net=-500.0),
                _t("ASSIGN", "2025-06-20", symbol="BRKB250620C00050000.US",
                   qty=-1, net=0.0),
                _t("ASSIGN", "2025-06-20", symbol="BRK.B.US", qty=100,
                   net=-5000.0)]
        w = self.walk(rows)
        self.assertEqual(w["per_symbol"]["BRK.B.US"]["year_end_cost"],
                         5500.0)

    @rule(R.CA_RPT_12)
    def test_a2_0328_class_root_put_premium_folds(self):
        # A written class-root put (QZB) assigned into the class share
        # QZB.B, on a US listing here so the walk reports it: premium
        # lowers the shares' cost.
        rows = [_t(date="2025-01-03", symbol="QZB250620P00050000.US",
                   qty=-1, net=300.0),
                _t("ASSIGN", "2025-06-20", symbol="QZB250620P00050000.US",
                   qty=1, net=0.0),
                _t("ASSIGN", "2025-06-20", symbol="QZB.B.US", qty=100,
                   net=-5000.0)]
        w = self.walk(rows)
        self.assertEqual(w["per_symbol"]["QZB.B.US"]["year_end_cost"],
                         4700.0)

    def test_a2_1121_dec31_expiry_is_named(self):
        rows = [_t(date="2025-03-03", symbol="QZQ251231C00010000.US",
                   qty=1, net=-293.52),
                _t(date="2025-03-03", symbol="QZQ251219C00010000.US",
                   qty=1, net=-100.0)]
        w = self.walk(rows, today="2026-10-01")
        self.assertEqual(w["expired_options_held"],
                         ["QZQ251219C00010000.US", "QZQ251231C00010000.US"])
        # In an unfinished year a contract is not expired on its day.
        w = self.walk(rows, today="2025-12-19")
        self.assertEqual(w["expired_options_held"], [])


class TestT1135Report(unittest.TestCase):
    """Formerly: test_fix_a2_filrep_a.TestT1135Report"""

    @rule(R.CA_RPT_02)
    def test_a2_0332_canadian_isin_on_us_listing_is_named(self):
        rows = [_t(date="2025-01-03", symbol="QZBT.US", qty=1500,
                   net=-202501.35),
                _t("DIVIDEND", "2025-03-03", symbol="QZBT.US", qty=0,
                   net=100.0, issuer_country="CA")]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin_base.json"
            p.write_text(json.dumps({"transactions": rows}))
            rc, out, err = _t1135([str(p), "--year", "2025",
                                   "--year-wash-only"])
            self.assertEqual(rc, 0, err)
            self.assertIn("Canadian ISIN", err)
            self.assertIn("QZBT.US", out.split("Canadian ISIN")[-1])
            m = Path(d) / "ticker.map"
            m.write_text("T1135 QZBT.US CA\n")
            rc, out, err = _t1135([str(p), "--year", "2025", "--map",
                                   str(m), "--year-wash-only"])
            self.assertNotIn("Canadian ISIN", err + out)
            self.assertIn("no T1135 required", out)

    def test_a2_0660_refusal_does_not_advise_a_relabel(self):
        rows = [_t(date="2025-02-03", symbol="MSFT.US", qty=200,
                   net=-80000.0, currency="USD")]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin_base.json"
            p.write_text(json.dumps({"transactions": rows}))
            rc, out, err = _t1135([str(p), "--year", "2025"])
            self.assertEqual(rc, 2)
            self.assertNotIn("--base-currency matching", err)
            rc, out, err = _t1135([str(p), "--year", "2025",
                                   "--base-currency", "USD"])
            self.assertEqual(rc, 2)
            self.assertNotIn("no T1135 required", out)

    @rule(R.CA_RPT_12)
    def test_a2_0661_s260_pil_counts_as_income(self):
        from taxjson.bin.taxjson_t1135 import join_income_gains
        doc = {"transactions": [
            {"action": "DIVIDEND_IN_LIEU", "date": "2025-03-03",
             "date_settle": "2025-03-03", "symbol": "SHOP.US",
             "dividend": 25.0, "pil": 0.0, "deemed_dividend": "ITA s.260"},
            {"action": "DIVIDEND_IN_LIEU", "date": "2025-04-03",
             "date_settle": "2025-04-03", "symbol": "SHOP.US",
             "pil": 5.0}]}
        with tempfile.TemporaryDirectory() as d:
            g = Path(d) / "margin_gains.json"
            g.write_text(json.dumps(doc))
            inc = join_income_gains([g], 2025)
        self.assertAlmostEqual(inc["SHOP.US"]["income"], 30.0)


if __name__ == "__main__":
    unittest.main()
