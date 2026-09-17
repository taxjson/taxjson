"""Tax-estimate math (`taxjson sum --other-income/--other-losses`).

Marginal checks are done in FLAT bracket regions so expected values are
exact hand arithmetic, not re-derivations through the same code.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.tax_estimate import (_bracket_tax, estimate_canada,
                                      estimate_usa)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestBracketMath(unittest.TestCase):
    def test_progressive(self):
        b = [(10000, .10), (20000, .20), (float("inf"), .30)]
        self.assertEqual(_bracket_tax(0, b), 0.0)
        self.assertEqual(_bracket_tax(10000, b), 1000.0)
        self.assertEqual(_bracket_tax(15000, b), 2000.0)     # 1000 + 1000
        self.assertEqual(_bracket_tax(25000, b), 4500.0)     # +2000 +1500


class TestCanadaEstimate(unittest.TestCase):
    """other_income = 200,000 puts every increment in flat regions:
    federal 29% (177,882-253,414), ON 12.16% (150,000-220,000) with the
    full 56% surtax factor (basic tax far above both thresholds)."""

    def _run(self, **kw):
        base = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0,
                    pil=0.0, other_income=200000.0, other_losses=0.0,
                    province="ON")
        base.update(kw)
        return estimate_canada(**base)

    def test_capital_gain_marginal(self):
        # 1,000 gain -> 500 taxable. fed 500*.29 = 145;
        # ON 500*.1216*1.56 = 94.85 -> 239.85 total.
        r = self._run(realized=1000.0)
        self.assertEqual(r["taxable_gain"], 500.0)
        self.assertAlmostEqual(r["estimated_tax"], 239.85, delta=0.5)
        self.assertAlmostEqual(r["avg_rate_pct"], 23.98, delta=0.1)

    def test_eligible_dividend_marginal(self):
        # 1,000 eligible -> grossed 1,380.
        # fed: 1380*.29 - 1380*.150198 = 192.93
        # ON:  1380*.1216*1.56 - 1380*.10 = 264.79 - 138 = 123.78
        r = self._run(eligible_div=1000.0)
        self.assertEqual(r["grossed_eligible"], 1380.0)
        self.assertAlmostEqual(r["estimated_tax"], 316.71, delta=0.5)

    def test_foreign_dividend_ftc(self):
        # 1,000 foreign: fed 290 - 150 FTC; ON 121.6*1.56 = 189.70
        r = self._run(foreign_div=1000.0)
        self.assertEqual(r["ftc_assumed"], 150.0)
        self.assertAlmostEqual(r["estimated_tax"],
                               290 - 150 + 189.70, delta=0.5)

    def test_other_losses_net_before_inclusion(self):
        r = self._run(realized=10000.0, other_losses=4000.0)
        self.assertEqual(r["taxable_gain"], 3000.0)     # (10k-4k) x 50%
        self.assertEqual(r["losses_applied"], 4000.0)
        self.assertEqual(r["losses_unused"], 0.0)

    def test_losses_exceed_gains(self):
        r = self._run(realized=1000.0, other_losses=5000.0)
        self.assertEqual(r["taxable_gain"], 0.0)
        self.assertEqual(r["losses_unused"], 4000.0)
        self.assertEqual(r["estimated_tax"], 0.0)

    def test_unsupported_province(self):
        with self.assertRaises(ValueError):
            self._run(province="XX")


class TestUsaEstimate(unittest.TestCase):
    def _run(self, **kw):
        base = dict(st=0.0, lt=0.0, qualified_div=0.0, pil=0.0,
                    other_income=100000.0, other_losses=0.0)
        base.update(kw)
        return estimate_usa(**base)

    def test_st_is_ordinary_lt_is_preferential(self):
        # 100k other - 15k std = 85k taxable ordinary (22% bracket,
        # 48,475-103,350). ST 1,000 -> 220. LT stacks at 85k -> 15%.
        st = self._run(st=1000.0)
        lt = self._run(lt=1000.0)
        self.assertAlmostEqual(st["estimated_tax"], 220.0, delta=0.5)
        self.assertAlmostEqual(lt["estimated_tax"], 150.0, delta=0.5)

    def test_qualified_dividend_stacks_with_lt(self):
        r = self._run(qualified_div=1000.0)
        self.assertAlmostEqual(r["estimated_tax"], 150.0, delta=0.5)

    def test_losses_net_st_first_then_lt_then_3000_ordinary(self):
        r = self._run(st=2000.0, lt=1000.0, other_losses=6000.0)
        self.assertEqual(r["st_net"], 0.0)
        self.assertEqual(r["lt_net"], 0.0)
        self.assertEqual(r["ordinary_offset"], 3000.0)
        self.assertEqual(r["losses_unused"], 0.0)
        # The offset SAVES tax: 3,000 off the 22% bracket -> -660.
        self.assertAlmostEqual(r["estimated_tax"], -660.0, delta=0.5)

    def test_niit_over_threshold(self):
        # other 190k, LT 20k -> MAGI 210k: NIIT on 10k = 380.
        # ordinary taxable 175k; LT stacks 175k-195k at 15% = 3,000.
        r = self._run(other_income=190000.0, lt=20000.0)
        self.assertEqual(r["niit"], 380.0)
        self.assertAlmostEqual(r["estimated_tax"], 3380.0, delta=0.5)


class TestSumEstimateCli(unittest.TestCase):
    """`taxjson sum --other-income/--other-losses` wiring: taxable
    accounts only, eligible/foreign dividend split by listing suffix."""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps({
            "summary": {"year": "2026"}, "transactions": [
                {"symbol": "AAA.TO", "gain": 30000.0, "cost": 1.0,
                 "proceeds": 2.0, "currency": "CAD", "days_held": 400},
                {"action": "DIVIDEND", "symbol": "AAA.TO",
                 "dividend": 1000.0, "currency": "CAD"},
                {"action": "DIVIDEND", "symbol": "KO.US",
                 "dividend": 500.0, "currency": "CAD"}]}))
        # Sheltered gains must NOT leak into the estimate.
        (root / "work" / "rrsp_gains.json").write_text(json.dumps({
            "summary": {"year": "2026"}, "transactions": [
                {"symbol": "BBB.TO", "gain": 999999.0, "cost": 1.0,
                 "proceeds": 2.0, "currency": "CAD", "days_held": 10}]}))
        return root

    def _sum(self, root, *args):
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "sum", *args],
            cwd=REPO_ROOT, capture_output=True, text=True)

    def test_estimate_block_canada(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._sum(self._project(tmp),
                          "--other-income", "200000")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("TAX ESTIMATE — canada/ON", out)
        self.assertIn("ESTIMATE ONLY", out)
        # Taxable-account gains only: 30,000 x 50% (not the rrsp's).
        self.assertIn("15,000.00", out)
        self.assertNotIn("514,999", out)
        # Eligible split by listing: only AAA.TO grosses up.
        self.assertIn("1,380.00", out)
        self.assertRegex(out, r"Foreign dividends\s+500\.00")
        # Exact expected via the estimator (wiring test; math is pinned
        # by the unit tests above).
        from taxjson.lib.tax_estimate import estimate_canada
        exp = estimate_canada(realized=30000.0, eligible_div=1000.0,
                              foreign_div=500.0, pil=0.0,
                              other_income=200000.0, other_losses=0.0,
                              province="ON")
        from taxjson.lib.report_model import fmt_money
        self.assertIn(f"ESTIMATED TAX ON INVESTMENT INCOME: "
                      f"{fmt_money(exp['estimated_tax'])} CAD", out)

    def test_verbose_calculation_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._sum(self._project(tmp),
                          "--other-income", "200000", "--verbose")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("CALCULATION TRACE", out)
        # Bracket slices, both jurisdictions, base vs with columns.
        self.assertIn("@ 29.00%", out)                     # fed top slice
        # year=2026 project -> 2026 vintage: indexed brackets, 14%.
        self.assertIn("0-58,523 @ 14.00%", out)
        self.assertIn("150,000-216,880 @ 12.16%", out)     # ON slice
        self.assertIn("BPA credit (16,452 @ 14%)", out)
        self.assertIn("surtax 20% of basic over 5,818", out)
        self.assertIn("surtax 36% of basic over 7,446", out)
        self.assertIn("DTC 15.0198% x 1,380.00", out)
        self.assertIn("FTC 15% x 500.00", out)
        self.assertIn("= basic tax", out)
        self.assertIn("WITH - BASE = 7,677.00", out)

    def test_verbose_usa_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_gains.json").write_text(json.dumps({
                "summary": {"year": "2026"}, "transactions": [
                    {"symbol": "BBB.US", "gain": 2000.0, "cost": 1.0,
                     "proceeds": 2.0, "currency": "USD",
                     "days_held": 400, "term": "LONG_TERM"}]}))
            r = self._sum(root, "--other-income", "100000", "-v")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("CALCULATION TRACE", out)
        # year=2026 project -> Rev. Proc. 2025-32 figure.
        self.assertIn("16,100.00 std deduction", out)
        self.assertIn("@ 22.00%", out)                    # ordinary slice
        # LT stacks from 85,000 into the 15% band: 2,000 @ 15% = 300.
        self.assertIn("PREFERENTIAL", out)
        # Stack base under the 2026 std deduction (16,100).
        self.assertIn("83,900-85,900 @ 15%", out)
        self.assertIn("NIIT", out)
        self.assertIn("WITH - BASE + NIIT = 300.00", out)

    def test_no_flags_no_estimate_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._sum(self._project(tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("TAX ESTIMATE", r.stdout)

    def test_other_losses_flow_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._sum(self._project(tmp),
                          "--other-income", "200000",
                          "--other-losses", "10000")
        self.assertEqual(r.returncode, 0, r.stderr)
        # (30,000 - 10,000) x 50% = 10,000 taxable.
        self.assertIn("10,000.00", r.stdout)

    def test_missing_province_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            r = self._sum(root, "--other-income", "1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("province", r.stderr)

    def test_usa_st_lt_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_gains.json").write_text(json.dumps({
                "summary": {"year": "2026"}, "transactions": [
                    {"symbol": "AAA.US", "gain": 1000.0, "cost": 1.0,
                     "proceeds": 2.0, "currency": "USD", "days_held": 30,
                     "term": "SHORT_TERM"},
                    {"symbol": "BBB.US", "gain": 2000.0, "cost": 1.0,
                     "proceeds": 2.0, "currency": "USD", "days_held": 400,
                     "term": "LONG_TERM"}]}))
            r = self._sum(root, "--other-income", "100000")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("TAX ESTIMATE — usa", out)
        self.assertRegex(out, r"Short-term gains \(net\)\s+1,000\.00")
        self.assertRegex(out, r"Long-term gains \(net\)\s+2,000\.00")
        # 1,000 @ 22% + 2,000 @ 15% = 520.
        self.assertIn("520.00 USD", out)




class TestFtcFromActualWithholding(unittest.TestCase):
    def test_actual_withholding_capped_at_treaty_rate(self):
        from taxjson.lib.tax_estimate import estimate_canada
        kw = dict(realized=0.0, eligible_div=0.0, foreign_div=1000.0,
                  pil=0.0, other_income=100000.0, other_losses=0.0,
                  province="ON")
        # No actual figure: flat 15% assumption (back-compat).
        r = estimate_canada(**kw)
        self.assertAlmostEqual(r["ftc_assumed"], 150.0)
        self.assertIn("assumed", r["ftc_source"])
        # Actual withholding below the cap: used as-is.
        r = estimate_canada(**kw, actual_withheld=90.0)
        self.assertAlmostEqual(r["ftc_assumed"], 90.0)
        self.assertIn("actual", r["ftc_source"])
        # Actual above the 15% ceiling: capped.
        r = estimate_canada(**kw, actual_withheld=400.0)
        self.assertAlmostEqual(r["ftc_assumed"], 150.0)
        # Less credit => more estimated tax.
        lo = estimate_canada(**kw, actual_withheld=0.0)
        hi = estimate_canada(**kw)
        self.assertGreater(lo["estimated_tax"], hi["estimated_tax"])

    def test_actual_withholding_flows_from_base_books(self):
        # BASE books, not gains files: the gains engine does not carry
        # TAX rows through, so the original gains-file read always
        # found nothing and silently kept the 15% assumption.
        import json
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_run import _actual_withholding
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "m_base.json").write_text(json.dumps(
                {"transactions": [
                    {"action": "TAX", "date": "2026-03-01",
                     "net_amount": 120.0},
                    {"action": "TAX", "date": "2026-04-01",
                     "net_amount": -20.0},          # refund nets
                    {"action": "TAX", "date": "2025-03-01",
                     "net_amount": 500.0},          # other year
                    {"action": "DIVIDEND", "date": "2026-03-01",
                     "net_amount": 999.0}]}))
            self.assertAlmostEqual(
                _actual_withholding(cache, {"m"}, 2026), 100.0)
            # Sheltered-only -> None (keep the assumption).
            self.assertIsNone(_actual_withholding(cache, set(), 2026))


class TestEstimateCommand(unittest.TestCase):
    """`taxjson estimate` — sum --estimate's front door: the estimate
    block without the account table."""

    def _project(self, td):
        import json
        from pathlib import Path
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        work = root / "work"
        work.mkdir()
        (work / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": 2026, "total_gain": 10000.0},
             "transactions": [
                 {"symbol": "AAA.TO", "date": "2026-05-02", "qty": -10,
                  "currency": "CAD", "proceeds": 20000.0,
                  "cost": 10000.0, "gain": 10000.0, "days_held": 100,
                  "commission": 0.0, "fee": 0.0}],
             "inventory": [], "wash_sales": []}))
        return root

    def _cli(self, root, *args):
        import subprocess
        import sys
        from pathlib import Path
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), *args],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True)

    def test_estimate_prints_the_summary_then_the_estimate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = self._cli(self._project(td), "estimate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("REALIZED-GAINS SUMMARY", r.stdout)
        self.assertIn("TAX ESTIMATE", r.stdout)
        self.assertIn("ESTIMATE ONLY", r.stdout)
        # The AMT check renders always — non-binding shows headroom.
        self.assertIn("AMT CHECK", r.stdout)
        self.assertIn("does not bind", r.stdout)
        self.assertLess(r.stdout.index("REALIZED-GAINS SUMMARY"),
                        r.stdout.index("TAX ESTIMATE"),
                        "the summary table comes FIRST")

    def test_amt_block_fits_the_house_width(self):
        # The AMT block ran off the edge (over-wide header, unwrapped
        # prose) while every table beside it was capped at 78.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = self._cli(self._project(td), "estimate")
        block = r.stdout[r.stdout.index("AMT CHECK"):]
        for line in block.splitlines():
            self.assertLessEqual(len(line), 78, repr(line))

    def test_estimate_json_carries_summary_and_estimate(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = self._cli(self._project(td), "estimate", "--json",
                          "--other-income", "100000")
        doc = json.loads(r.stdout)
        self.assertIn("estimate", doc)
        self.assertEqual(doc["estimate"]["country"], "canada")
        self.assertIn("accounts", doc)
        self.assertIn("totals", doc)

    def test_sum_estimate_flag_was_removed(self):
        # `estimate` is the one front door; the development-era
        # `sum --estimate` spelling was purged pre-1.0.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = self._cli(self._project(td), "sum", "--estimate")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--estimate", r.stderr)

    def test_sum_still_shows_estimate_with_income_flags(self):
        # sum's estimate BLOCK is a feature (the GUI's what-if path):
        # supplying income inputs still renders it under the table.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = self._cli(self._project(td), "sum",
                          "--other-income", "0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("REALIZED-GAINS SUMMARY", r.stdout)
        self.assertIn("TAX ESTIMATE", r.stdout)


class TestCanadaAmt(unittest.TestCase):
    """Post-2024 AMT check — pure math inside the Canada estimator."""

    def _est(self, **kw):
        from taxjson.lib.tax_estimate import estimate_canada
        base = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0,
                    pil=0.0, other_income=0.0, other_losses=0.0,
                    province="ON")
        base.update(kw)
        return estimate_canada(**base)

    def test_gains_heavy_no_salary_binds(self):
        # The motivating profile: big gains, no employment income —
        # regular tax enjoys 50% inclusion, AMT includes 100%.
        r = self._est(realized=390000.0, eligible_div=40000.0)
        amt = r["amt"]
        self.assertTrue(amt["binding"])
        self.assertGreater(amt["topup"], 0)
        self.assertGreater(amt["provincial_amt"], 0)     # ON piggyback
        self.assertAlmostEqual(
            r["estimated_tax_with_amt"],
            r["estimated_tax"] + amt["topup"], places=2)
        self.assertAlmostEqual(amt["carryforward"],
                               amt["excess_fed"], places=2)

    def test_salary_heavy_does_not_bind(self):
        # Same gains on top of a big salary: regular tax is already
        # high, AMT shows headroom instead.
        r = self._est(realized=100000.0, other_income=300000.0)
        amt = r["amt"]
        self.assertFalse(amt["binding"])
        self.assertGreater(amt["headroom"], 0)
        self.assertAlmostEqual(amt["topup"], 0.0)
        self.assertAlmostEqual(r["estimated_tax_with_amt"],
                               r["estimated_tax"], places=2)

    def test_loss_carryforwards_deduct_at_half_in_amt(self):
        # $50k of applied losses shrink the AMT base by only $25k.
        with_l = self._est(realized=390000.0, other_losses=50000.0)
        without = self._est(realized=390000.0)
        self.assertAlmostEqual(
            without["amt"]["adjusted_income"]
            - with_l["amt"]["adjusted_income"], 25000.0, places=2)

    def test_exemption_tracks_the_bracket_table(self):
        from taxjson.lib.tax_estimate import (CA_FED_BRACKETS,
                                              ca_amt_exemption)
        self.assertEqual(ca_amt_exemption(), CA_FED_BRACKETS[2][0])

    def test_small_gains_far_from_amt(self):
        r = self._est(realized=20000.0)
        self.assertFalse(r["amt"]["binding"])
        self.assertAlmostEqual(r["amt"]["minimum_fed"], 0.0)


if __name__ == "__main__":
    unittest.main()


class TestAmtLossCapAndWidth(unittest.TestCase):
    """Audit fixes: the AMT base deducted the whole loss POOL, and the
    binding branch overflowed the 78-column budget."""

    def _est(self, **kw):
        from taxjson.lib.tax_estimate import estimate_canada
        base = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0,
                    pil=0.0, other_income=0.0, other_losses=0.0,
                    province="ON")
        base.update(kw)
        return estimate_canada(**base)

    def test_unused_loss_pool_cannot_erase_the_amt(self):
        # Losses beyond the year's gains change nothing in regular tax
        # (the excess simply carries forward), so they must not shrink
        # the AMT base either.
        at_cap = self._est(realized=400000.0, other_losses=400000.0)
        way_over = self._est(realized=400000.0, other_losses=800000.0)
        self.assertAlmostEqual(at_cap["amt"]["adjusted_income"],
                               way_over["amt"]["adjusted_income"],
                               places=2)
        self.assertAlmostEqual(at_cap["amt"]["topup"],
                               way_over["amt"]["topup"], places=2)
        self.assertTrue(way_over["amt"]["binding"])

    def test_claimable_losses_still_reduce_the_amt_base(self):
        none = self._est(realized=400000.0)
        half = self._est(realized=400000.0, other_losses=200000.0)
        self.assertAlmostEqual(
            none["amt"]["adjusted_income"]
            - half["amt"]["adjusted_income"], 100000.0, places=2)

    def test_binding_amt_block_fits_the_house_width(self):
        import io
        import subprocess
        import sys
        import tempfile
        import json as _json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nprovince = "ON"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            work = root / "work"
            work.mkdir()
            work.joinpath("margin_gains.json").write_text(_json.dumps(
                {"summary": {"year": 2026, "total_gain": 900000.0},
                 "transactions": [
                     {"symbol": "AAA.TO", "date": "2026-05-02",
                      "qty": -100, "currency": "CAD",
                      "proceeds": 1000000.0, "cost": 100000.0,
                      "gain": 900000.0, "days_held": 200,
                      "commission": 0.0, "fee": 0.0}],
                 "inventory": [], "wash_sales": []}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "estimate"],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        block = r.stdout[r.stdout.index("AMT CHECK"):]
        self.assertIn("AMT TOP-UP", block, "fixture must BIND")
        for line in block.splitlines():
            self.assertLessEqual(len(line), 78, repr(line))
