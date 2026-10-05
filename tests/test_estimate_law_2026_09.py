"""Estimate / instalments corrections against current law (2026-09
audit). Every expected value is hand arithmetic shown in the comments;
sources are cited in lib/tax_estimate.py and bin/taxjson_instalments.py.

2026 federal constants used below: brackets 58,523 / 117,045 / 181,440 /
258,482; lowest rate 14%; BPA 16,452 phasing to 14,829 over
181,440-258,482 (1,623 over 77,042); AMT 20.5% over 181,440.
"""

import os
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import tax_estimate as te
from taxjson.lib.tax_estimate import (apply_vintage, ca_fed_bpa,
                                      estimate_canada,
                                      ontario_health_premium)
from tax_rules import rule


REPO_ROOT = Path(__file__).resolve().parent.parent


def _ca(**kw):
    base = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0,
                pil=0.0, other_income=0.0, other_losses=0.0,
                province="ON", year=2026)
    base.update(kw)
    return estimate_canada(**base)


class TestOntarioAmt(unittest.TestCase):
    """ON additional tax for minimum tax = 24.63% of the federal excess
    (5006-D line 72 worksheet, 2024+), with the ON surtax recomputed on
    basic ON tax plus that additional tax."""

    def test_gains_only_400k_2026(self):
        # Regular: taxable 200,000 (400k x 50%), net income the same.
        #   fed gross 8,193.22 + 11,997.01 + 16,742.70 + 18,560 x .29
        #     (5,382.40) = 42,315.33
        #   BPA 16,452 - 1,623 x 18,560/77,042 = 16,061.0083;
        #     x .14 = 2,248.54 -> regular fed 40,066.79
        # AMT: (400,000 - 181,440) x .205 = 44,804.80
        #   - 50% BPA credit 16,061.0083 x .14 x .5 = 1,124.27
        #   = 43,680.53 minimum; excess 43,680.53 - 40,066.79 = 3,613.74
        # ON basic: 2,721.50 + 4,931.30 + 4,711.19 + 50,000 x .1216
        #   (6,080) - 12,989 x .0505 (655.94) = 17,788.05 (both surtax
        #   thresholds already exceeded)
        # ON add 3,613.74 x .2463 = 890.06; surtax on it .56 x 890.06
        #   = 498.43 -> ON AMT 1,388.50 (the old flat .3367 factor  # pii-ok
        #   gave a lower figure and no surtax)
        r = _ca(realized=400000.0)
        amt = r["amt"]
        self.assertTrue(amt["binding"])
        self.assertAlmostEqual(amt["bpa_credit"], 1124.27, delta=0.01)
        self.assertAlmostEqual(amt["minimum_fed"], 43680.53, delta=0.01)
        self.assertAlmostEqual(amt["excess_fed"], 3613.74, delta=0.01)
        self.assertEqual(amt["provincial_factor"], .2463)
        self.assertAlmostEqual(amt["provincial_amt_basic"], 890.06,
                               delta=0.01)
        self.assertAlmostEqual(amt["provincial_amt_surtax"], 498.43,
                               delta=0.01)
        self.assertAlmostEqual(amt["provincial_amt"], 1388.50, delta=0.01)
        self.assertAlmostEqual(amt["topup"], 5002.24, delta=0.01)
        # 2026 factor is assumed until the 2026 form is published.
        self.assertTrue(amt["provincial_factor_assumed"])
        self.assertTrue(any("ASSUMED" in n for n in r["notes"]))
        self.assertTrue(any("24.63%" in n and "surtax" in n
                            for n in r["notes"]))

    def test_surtax_threshold_crossed_by_the_additional_tax(self):
        # Direct: basic 5,700 (below 5,818) + add 1,000 = 6,700:
        #   surtax (6,700 - 5,818) x .20 = 176.40 (36% tier not hit).
        prov = te._VINTAGES["2026"]["CA_PROVINCES"]["ON"]
        apply_vintage(2026)
        # regular_fed chosen so excess = 1,000 / .2463 = 4,060.089...
        excess = 1000.0 / .2463
        amt = te._amt_canada(realized=0.0, eligible_div=0.0,
                             foreign_div=0.0, pil=0.0,
                             other_income=181440.0 + 100000.0,
                             other_losses=0.0, ftc=0.0,
                             regular_fed=(100000 * .205
                                          - 16452 * .14 * .5 - excess),
                             prov=prov, fed_bpa_amount=16452.0,
                             prov_basic=5700.0)
        self.assertAlmostEqual(amt["provincial_amt_basic"], 1000.0,
                               delta=0.01)
        self.assertAlmostEqual(amt["provincial_amt_surtax"], 176.40,
                               delta=0.01)

    def test_vintage_factors(self):
        v = te._VINTAGES
        for y in ("2024", "2025", "2026"):
            self.assertEqual(v[y]["CA_PROVINCES"]["ON"]["amt_factor"],
                             .2463)
            self.assertTrue(v[y]["CA_PROVINCES"]["ON"]["health_premium"])


class TestBritishColumbia(unittest.TestCase):
    def test_amt_factor_by_year(self):
        # BC ITA s.4.8: BC lowest / federal lowest, to 0.001:
        # 5.06/15 = .337; 5.06/14.5 = .349; 5.60/14 = .400.
        v = te._VINTAGES
        self.assertEqual(v["2024"]["CA_PROVINCES"]["BC"]["amt_factor"],
                         .337)
        self.assertEqual(v["2025"]["CA_PROVINCES"]["BC"]["amt_factor"],
                         .349)
        self.assertEqual(v["2026"]["CA_PROVINCES"]["BC"]["amt_factor"],
                         .400)

    def test_bc_amt_2026_is_40_percent_of_the_federal_excess(self):
        # Federal side is province-independent: excess 3,613.74 (see
        # the ON case); BC has no surtax: 3,613.74 x .400 = 1,445.50.
        r = _ca(realized=400000.0, province="BC")
        self.assertAlmostEqual(r["amt"]["excess_fed"], 3613.74, delta=0.01)
        self.assertAlmostEqual(r["amt"]["provincial_amt"], 1445.50,
                               delta=0.01)
        self.assertEqual(r["amt"]["provincial_amt_surtax"], 0.0)

    def test_bc_2026_bpa(self):
        # 13,216 x 5.60% = 740.10
        r = _ca(other_income=50000.0, province="BC")
        self.assertAlmostEqual(r["trace_base"]["prov_bpa"], 740.096,
                               places=3)
        self.assertEqual(r["trace_base"]["prov_ohp"], 0.0)
        self.assertTrue(any("no provincial surtax" in n
                            for n in r["notes"]))


class TestAlbertaDtc(unittest.TestCase):
    def test_2026_eligible_dtc_is_8_12_percent(self):
        # AB PITA s.21(c): 227/770 of the 38% gross-up = 8.12% of the
        # grossed amount. 20,000 eligible -> 27,600 grossed;
        # 27,600 x .0812 = 2,241.12 (the old .08 gave 2,208.00).
        r = _ca(eligible_div=20000.0, other_income=150000.0,
                province="AB")
        self.assertAlmostEqual(r["trace_with"]["prov_dtc"], 2241.12,
                               delta=0.005)


class TestFederalBpaPhaseDown(unittest.TestCase):
    def test_endpoints_and_midpoint_per_vintage(self):
        for year, hi, lo, start, end in (
                (2024, 15705.0, 14156.0, 173205, 246752),
                (2025, 16129.0, 14538.0, 177882, 253414),
                (2026, 16452.0, 14829.0, 181440, 258482)):
            apply_vintage(year)
            self.assertEqual(ca_fed_bpa(start), hi)
            self.assertEqual(ca_fed_bpa(end), lo)
            self.assertEqual(ca_fed_bpa(10 ** 7), lo)
            # halfway -> halfway: 2026 (16,452 + 14,829)/2 = 15,640.50
            self.assertAlmostEqual(ca_fed_bpa((start + end) / 2),
                                   (hi + lo) / 2, places=6)

    def test_regular_credit_uses_the_phased_amount(self):
        # 2026, other income 220,000 (no investments):
        # BPA 16,452 - 1,623 x 38,560/77,042 = 16,452 - 812.32
        #   = 15,639.68; credit x .14 = 2,189.56
        r = _ca(other_income=220000.0)
        self.assertAlmostEqual(r["trace_base"]["fed_bpa_amount"],
                               15639.68, delta=0.01)
        self.assertAlmostEqual(r["trace_base"]["fed_bpa"], 2189.56,
                               delta=0.01)

    def test_carryforward_losses_do_not_lower_net_income(self):
        # 10,000 gain fully offset by a 10,000 carryforward: taxable
        # income stays 200,000, net income is 205,000 (line 25300
        # sits below line 23600): BPA 16,452 - 1,623 x 23,560/77,042
        # = 15,955.67.
        r = _ca(other_income=200000.0, realized=10000.0,
                other_losses=10000.0)
        self.assertEqual(r["taxable_gain"], 0.0)
        self.assertAlmostEqual(r["trace_with"]["net_income"], 205000.0)
        self.assertAlmostEqual(r["trace_with"]["fed_bpa_amount"],
                               15955.67, delta=0.01)


class TestOntarioHealthPremium(unittest.TestCase):
    def test_chart_points(self):
        # ON428 line 89 chart.
        for ti, exp in ((20000, 0), (25000, 300), (30000, 300),
                        (37000, 360),          # 300 + 6% x 1,000
                        (38500, 450), (48300, 525),   # 450 + 25% x 300
                        (60000, 600), (72400, 700),   # 600 + 25% x 400
                        (100000, 750), (200300, 825), # 750 + 25% x 300
                        (200600, 900), (1000000, 900)):
            self.assertAlmostEqual(ontario_health_premium(ti), exp,
                                   places=6, msg=str(ti))

    def test_premium_is_in_the_ontario_total(self):
        # 2026, 60,000 other income: ON gross 53,891 x .0505
        #   = 2,721.50 + 6,109 x .0915 = 558.97 -> 3,280.47;
        #   - BPA 12,989 x .0505 = 655.94 -> basic 2,624.53 (under
        #   the 5,818 surtax threshold) + OHP 600 = 3,224.52
        r = _ca(other_income=60000.0)
        self.assertAlmostEqual(r["trace_base"]["prov_ohp"], 600.0)
        self.assertAlmostEqual(r["tax_base"]["provincial"], 3224.52,
                               delta=0.01)
        self.assertTrue(any("Ontario Health Premium" in n
                            for n in r["notes"]))


class TestVintageNotes(unittest.TestCase):
    def test_matching_year_has_no_vintage_note(self):
        r = _ca(realized=1000.0, year=2026)
        self.assertFalse(any("vintage" in n for n in r["notes"]))

    def test_future_year_says_which_tables_ran(self):
        r = _ca(realized=1000.0, year=2027)
        self.assertEqual(r["vintage"], "2026")
        self.assertTrue(any("2027 has no built-in rate vintage" in n
                            and "2026 tables" in n for n in r["notes"]))

    def test_pre_earliest_year_warns_about_the_amt_regime(self):
        r = _ca(realized=1000.0, year=2023)
        self.assertEqual(r["vintage"], "2024")
        note = " ".join(r["notes"])
        self.assertIn("2023 predates", note)
        self.assertIn("AMT", note)
        self.assertIn("do not apply", note)

    def test_usa_carries_the_same_note(self):
        from taxjson.lib.tax_estimate import estimate_usa
        r = estimate_usa(st=0.0, lt=1000.0, qualified_div=0.0, pil=0.0,
                         other_income=50000.0, other_losses=0.0,
                         year=2030)
        self.assertTrue(any("2030" in n for n in r["notes"]))
        self.assertFalse(any("AMT" in n for n in r["notes"]))


class TestStakingIsOrdinaryIncome(unittest.TestCase):
    def test_staking_has_no_ftc(self):
        # 2026 ON, other 200,000 + 1,000 staking:
        # fed 1,000 x .29 = 290 + BPA 1,000 x .14 x 1,623/77,042 = 2.95
        # ON 1,000 x .1216 x 1.56 = 189.70; OHP 750 -> 900 = +150
        # -> 632.65, and no foreign tax credit at all.
        r = _ca(other_income=200000.0, staking=1000.0)
        self.assertEqual(r["ftc_assumed"], 0.0)
        self.assertEqual(r["staking"], 1000.0)
        self.assertAlmostEqual(r["estimated_tax"], 632.65, delta=0.01)
        # Treated as foreign dividends it would have been credited 150.
        wrong = _ca(other_income=200000.0, foreign_div=1000.0)
        self.assertAlmostEqual(wrong["estimated_tax"]
                               - r["estimated_tax"], -150.0, delta=0.01)

    @rule("CA-INC-04")
    def test_crypto_account_dividends_route_to_staking(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nprovince = "ON"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.cb_main]\ntype = "taxable"\ncrypto = true\n')
            work = root / "work"
            work.mkdir()
            (work / "margin_gains.json").write_text(json.dumps({
                "summary": {"year": "2026"}, "transactions": [
                    {"action": "DIVIDEND", "symbol": "KO.US",
                     "dividend": 500.0, "currency": "CAD"}]}))
            (work / "cb_main_gains.json").write_text(json.dumps({
                "summary": {"year": "2026"}, "transactions": [
                    {"action": "DIVIDEND", "symbol": "ETH",
                     "dividend": 1000.0, "currency": "CAD"}]}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "estimate", "--json", "--other-income",
                 "200000"], cwd=REPO_ROOT, capture_output=True,
                text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            est = json.loads(r.stdout)["estimate"]
            # Only KO's 500 is foreign; FTC assumed 15% x 500 = 75,
            # not 15% x 1,500 = 225.
            self.assertAlmostEqual(est["staking"], 1000.0)
            self.assertAlmostEqual(est["ftc_assumed"], 75.0)
            t = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "estimate", "--other-income", "200000"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertRegex(t.stdout, r"Foreign dividends\s+500\.00")
        self.assertRegex(t.stdout, r"Crypto staking \(ordinary\)\s+"
                                   r"1,000\.00")
        self.assertIn("ELIGIBLE (non-eligible", t.stdout)
        self.assertIn("- Included in the ON figure", t.stdout)


class TestInstalmentLawFixes(unittest.TestCase):
    _REQ = [{"date": "2026-03-16", "amount": 10000.0},
            {"date": "2026-06-15", "amount": 10000.0},
            {"date": "2026-09-15", "amount": 10000.0},
            {"date": "2026-12-15", "amount": 10000.0}]

    def test_credit_runs_from_january_1_at_the_earliest(self):
        from taxjson.bin.taxjson_instalments import interest_and_penalty
        kw = dict(required=self._REQ, annual_rate=0.07,
                  end=date(2027, 4, 30))
        dec = interest_and_penalty(
            payments=[{"date": "2025-12-01", "amount": 40000.0}], **kw)
        jan = interest_and_penalty(
            payments=[{"date": "2026-01-01", "amount": 40000.0}], **kw)
        self.assertAlmostEqual(dec["credit_interest"],
                               jan["credit_interest"], places=2)
        self.assertGreater(jan["credit_interest"], 0.0)

    def test_net_interest_of_25_or_less_is_not_charged(self):
        from taxjson.bin.taxjson_instalments import interest_and_penalty
        # 10,000 short for 11 days at 8%:
        # 10,000 x ((1 + .08/365)^11 - 1) = 24.14 <= 25 -> not charged.
        req = [{"date": "2026-03-16", "amount": 10000.0}]
        ip = interest_and_penalty(required=req, payments=[],
                                  annual_rate=0.08,
                                  end=date(2026, 3, 26))
        self.assertAlmostEqual(ip["net_interest_computed"], 24.14,
                               places=2)
        self.assertEqual(ip["net_interest"], 0.0)
        # 12 days: 10,000 x ((1 + .08/365)^12 - 1) = 26.33 > 25.
        ip = interest_and_penalty(required=req, payments=[],
                                  annual_rate=0.08,
                                  end=date(2026, 3, 27))
        self.assertAlmostEqual(ip["net_interest"], 26.33, places=2)

    def test_published_rates_are_the_default(self):
        from taxjson.bin.taxjson_instalments import build, render
        doc = build(year=2026, basis="current_year",
                    current_net_tax=40000.0, payments=[],
                    annual_rate=None, as_of=date(2026, 9, 27))
        self.assertEqual(doc["rate_source"], "published")
        # 2026: 7% every quarter (CRA 2026-q1..q4 pages).
        self.assertEqual(doc["rate_schedule"],
                         [{"from": "2026-01-01", "rate": 0.07}])
        self.assertFalse(doc["rate_extrapolated"])
        text = " ".join(render(doc, "CAD").split())
        self.assertIn("7.00%", text)
        self.assertIn("published quarterly rate", text)
        self.assertIn("instalment reminder", text)

    def test_published_schedule_changes_by_quarter(self):
        from taxjson.bin.taxjson_instalments import published_rates
        # 2024 Q1-Q2 10%, Q3-Q4 9%; 2025 Q1-Q2 8%, Q3-Q4 7%.
        self.assertEqual(
            published_rates(date(2024, 1, 1), date(2025, 4, 30)),
            [{"from": "2024-01-01", "rate": 0.10},
             {"from": "2024-07-01", "rate": 0.09},
             {"from": "2025-01-01", "rate": 0.08}])
        self.assertEqual(
            published_rates(date(2025, 1, 1), date(2026, 4, 30)),
            [{"from": "2025-01-01", "rate": 0.08},
             {"from": "2025-07-01", "rate": 0.07}])

    def test_past_the_table_is_disclosed(self):
        from taxjson.bin.taxjson_instalments import build, render
        doc = build(year=2026, basis="current_year",
                    current_net_tax=40000.0, payments=[],
                    annual_rate=None, as_of=date(2027, 4, 30))
        self.assertTrue(doc["rate_extrapolated"])
        self.assertIn("assume the last published rate",
                      " ".join(render(doc, "CAD").split()))

    def test_configured_rate_overrides(self):
        from taxjson.bin.taxjson_instalments import build
        doc = build(year=2026, basis="current_year",
                    current_net_tax=40000.0, payments=[],
                    annual_rate=0.05, as_of=date(2026, 9, 27))
        self.assertEqual(doc["rate_source"], "configured")
        self.assertEqual(doc["annual_rate"], 0.05)


if __name__ == "__main__":
    unittest.main()
