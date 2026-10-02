"""Re-audit-2 conformance-spec fixes: the tax estimate and instalments
(A2-0481, A2-0809, A2-0821, A2-0824, A2-1463, A2-1464, A2-0828, A2-0813,
A2-0825). Every rule the estimate applies is a tax-logic statement with
an id; each test carries the id it pins. Synthetic figures only."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, settings_for

from taxjson.lib import tax_estimate as TE
from taxjson.lib import tax_logic as TL


def _text(country, rid, **settings):
    st = dict(settings, country=country, year=2025)
    for _t, rules in TL.rule_sections(country, st):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated for {country}")


def _ca(**kw):
    args = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
                other_income=0.0, other_losses=0.0, province="ON",
                year=2025)
    args.update(kw)
    return TE.estimate_canada(**args)


def _us(**kw):
    args = dict(st=0.0, lt=0.0, qualified_div=0.0, pil=0.0,
                other_income=0.0, other_losses=0.0, year=2025)
    args.update(kw)
    return TE.estimate_usa(**args)


# ------------------------------------------------------------ USA
class TestUsCarryoverByTerm(unittest.TestCase):
    """A2-0481 / A2-0809: a long-term carryover offsets long-term gains
    first (Schedule D line 14); --other-losses is the short-term one."""

    @rule("US-EST-CARRY-TERM")
    def test_long_term_carryover_offsets_long_term_first(self):
        st_first = _us(st=10000, lt=10000, other_income=120000,
                       other_losses=10000)
        lt_first = _us(st=10000, lt=10000, other_income=120000,
                       lt_losses=10000)
        self.assertEqual((st_first["st_net"], st_first["lt_net"]),
                         (0.0, 10000.0))
        self.assertEqual((lt_first["st_net"], lt_first["lt_net"]),
                         (10000.0, 0.0))
        self.assertAlmostEqual(st_first["estimated_tax"], 1500.0, places=2)
        self.assertAlmostEqual(lt_first["estimated_tax"], 2400.0, places=2)
        # An excess of either character crosses over, then $3,000.
        r = _us(st=1000, lt=0, other_income=120000, lt_losses=6000)
        self.assertEqual((r["st_net"], r["lt_net"]), (0.0, 0.0))
        self.assertEqual(r["ordinary_offset"], 3000.0)
        self.assertEqual(r["losses_unused"], 2000.0)
        self.assertIn("long-term", _text("usa", "US-EST-CARRY-TERM"))

    @rule("US-EST-CARRY-TERM")
    def test_cli_flag_and_help(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "us"
            root.mkdir()
            (root / "taxjson.toml").write_text(
                settings_for("usa", year=2025)
                + '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "m.tt").write_text(
                "BUYSELL 2025-01-02 10:00:00 XYZ.US 10 USD 100 1000 0\n"
                "BUYSELL 2025-03-03 10:00:00 XYZ.US -10 USD 200 2000 0\n")
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            r = cli(root, "estimate", "--json", "--other-income", "120000",
                    "--long-term-losses", "500")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            est = json.loads(r.stdout)["estimate"]
            self.assertEqual(est["st_net"], 500.0)
            h = cli(root, "estimate", "--help")
        self.assertNotIn("netted before the 50%", h.stdout.replace("\n", " ")
                         .replace("  ", " "))

    @rule("US-EST-CARRY-TERM")
    @rule_absent("US-EST-CARRY-TERM", country="canada")
    def test_canada_refuses_the_long_term_flag(self):
        from taxjson.lib.country import flag_country_problems
        self.assertTrue(flag_country_problems(
            "canada", {"--long-term-losses": 100.0}))
        self.assertFalse(flag_country_problems(
            "usa", {"--long-term-losses": 100.0}))
        with self.assertRaises(TypeError):
            TE.estimate_canada(realized=0.0, eligible_div=0.0,
                               foreign_div=0.0, pil=0.0, other_income=0.0,
                               other_losses=0.0, province="ON",
                               lt_losses=1.0)


class TestUsVintage(unittest.TestCase):
    """A2-1464 (US): which year's tables the estimate uses."""

    @rule("US-EST-VINTAGE")
    def test_year_without_a_table(self):
        newest = max(TE._VINTAGES, key=int)
        earliest = min(TE._VINTAGES, key=int)
        r = _us(st=1000, year=int(newest) + 3)
        self.assertEqual(r["vintage"], newest)
        self.assertTrue(any("no built-in rate vintage" in n
                            for n in r["notes"]))
        r = _us(st=1000, year=int(earliest) - 5)
        self.assertEqual(r["vintage"], earliest)
        self.assertEqual(_us(st=1000, year=int(earliest))["notes"], [])
        self.assertIn("newest", _text("usa", "US-EST-VINTAGE"))


# ------------------------------------------------------------ Canada
class TestCanadaEstimateRules(unittest.TestCase):
    """A2-0821 / A2-0824 / A2-1463 / A2-1464 (Canada)."""

    @rule("CA-EST-LOSSES")
    def test_losses_capped_at_the_years_gains(self):
        r = _ca(realized=1000.0, other_losses=50000.0, other_income=80000)
        self.assertEqual(r["losses_applied"], 1000.0)
        self.assertEqual(r["losses_unused"], 49000.0)
        self.assertEqual(r["taxable_gain"], 0.0)
        # Netted before the inclusion, deducted below net income: the BPA
        # phase-down reads net income with the gain in it.
        r = _ca(realized=10000.0, other_losses=4000.0, other_income=80000)
        self.assertEqual(r["taxable_gain"], 3000.0)
        self.assertAlmostEqual(r["trace_with"]["net_income"],
                               80000 + 5000.0, places=2)

    @rule("CA-EST-DEDUCT")
    def test_deductions_and_carrying_charges(self):
        plain = _ca(realized=200000.0, other_income=0.0)
        ded = _ca(realized=200000.0, other_income=0.0, deductions=10000.0)
        cc = _ca(realized=200000.0, other_income=0.0,
                 carrying_charges=10000.0)
        # Regular tax: both lower taxable income in full.
        self.assertAlmostEqual(ded["trace_with"]["ti"],
                               plain["trace_with"]["ti"] - 10000, places=2)
        self.assertAlmostEqual(cc["trace_with"]["ti"],
                               plain["trace_with"]["ti"] - 10000, places=2)
        # AMT base: deductions in full, carrying charges at 50%.
        a0 = plain["amt"]["adjusted_income"]
        self.assertAlmostEqual(ded["amt"]["adjusted_income"], a0 - 10000,
                               places=2)
        self.assertAlmostEqual(cc["amt"]["adjusted_income"], a0 - 5000,
                               places=2)

    @rule("CA-EST-BPA")
    def test_bpa_phase_down(self):
        low = _ca(realized=10000.0, other_income=50000)
        high = _ca(realized=600000.0)
        self.assertAlmostEqual(low["trace_with"]["fed_bpa_amount"],
                               TE.CA_FED_BPA, places=2)
        self.assertAlmostEqual(high["trace_with"]["fed_bpa_amount"],
                               TE.CA_FED_BPA_MIN, places=2)
        mid_net = (TE.CA_FED_BRACKETS[2][0] + TE.CA_FED_BRACKETS[3][0]) / 2
        self.assertAlmostEqual(TE.ca_fed_bpa(mid_net),
                               (TE.CA_FED_BPA + TE.CA_FED_BPA_MIN) / 2,
                               places=2)

    @rule("CA-EST-PROV")
    def test_ontario_surtax_health_premium_and_no_quebec(self):
        r = _ca(realized=200000.0, other_income=100000)
        self.assertGreater(r["trace_with"]["prov_surtax"], 0.0)
        self.assertEqual(TE.ontario_health_premium(250000.0), 900.0)
        self.assertEqual(TE.ontario_health_premium(20000.0), 0.0)
        bc = _ca(realized=200000.0, other_income=100000, province="BC")
        self.assertEqual(bc["trace_with"]["prov_ohp"], 0.0)
        with self.assertRaises(ValueError):
            _ca(realized=1000.0, province="QC")
        self.assertIn("Quebec", _text("canada", "CA-EST-PROV"))

    @rule("CA-EST-FTC")
    def test_provincial_foreign_tax_credit(self):
        r = _ca(foreign_div=20000.0, actual_withheld=3000.0)
        t = r["trace_with"]
        self.assertGreater(t["fed_ftc_unused"], 0.0)
        self.assertGreater(t["prov_ftc"], 0.0)
        self.assertLessEqual(t["prov_ftc"], t["fed_ftc_unused"])
        # Credited withholding never exceeds 15% of the foreign dividends.
        r = _ca(foreign_div=1000.0, actual_withheld=300.0,
                other_income=100000)
        self.assertEqual(r["ftc_assumed"], 150.0)

    @rule("CA-EST-AMT")
    def test_amt_base(self):
        r = _ca(realized=1_000_000.0, other_losses=100000.0)
        amt = r["amt"]
        # Gains at 100%, the claimable carryforward at 50%.
        self.assertAlmostEqual(amt["adjusted_income"],
                               1_000_000 - 50000, places=2)
        self.assertEqual(amt["rate"], TE.CA_AMT_RATE)
        self.assertAlmostEqual(amt["exemption"], TE.CA_FED_BRACKETS[2][0],
                               places=2)
        self.assertTrue(amt["binding"])
        # Dividends at their actual amount (no gross-up) in the AMT base.
        d = _ca(eligible_div=100000.0)["amt"]
        self.assertAlmostEqual(d["adjusted_income"], 100000.0, places=2)

    @rule("CA-EST-VINTAGE")
    def test_year_without_a_table(self):
        newest = max(TE._VINTAGES, key=int)
        earliest = min(TE._VINTAGES, key=int)
        r = _ca(realized=1000.0, year=int(newest) + 2)
        self.assertEqual(r["vintage"], newest)
        r = _ca(realized=1000.0, year=int(earliest) - 1)
        self.assertEqual(r["vintage"], earliest)
        self.assertTrue(any("post-2024 regime" in n for n in r["notes"]))
        self.assertIn("newest", _text("canada", "CA-EST-VINTAGE"))


class TestCanadaTrustDistributionsInTheEstimate(unittest.TestCase):
    """A2-0828: the estimate counts a Canadian trust's distribution with
    the eligible dividends; tax-logic and the printed assumptions say so
    (the T3 split decides)."""

    @rule("CA-EST-TRUST")
    def test_stated(self):
        t = _text("canada", "CA-EST-TRUST")
        self.assertIn("T3", t)
        self.assertIn("eligible", t)
        self.assertIn("trust", TE.CA_ASSUMPTIONS)


# ------------------------------------------------------------ instalments
class TestInstalmentRules(unittest.TestCase):
    """A2-0825: the instalment rules CA-RPT-11 left unstated."""

    def _req(self, amount=4000.0, year=2025):
        from taxjson.bin import taxjson_instalments as I
        return I.required_schedule(year=year, basis="current_year",
                                   current_net_tax=amount)

    @rule("CA-INST-LEAST")
    def test_least_cumulative_per_date(self):
        from taxjson.bin import taxjson_instalments as I
        cands = I.candidate_schedules(year=2025, current_net_tax=40000.0,
                                      prior_net_tax=4000.0,
                                      second_prior_net_tax=80000.0)
        name, sched = I.least_cumulative_schedule(cands)
        cum = {n: [sum(r["amount"] for r in cands[n][:i + 1])
                   for i in range(4)] for n in cands}
        got = [sum(r["amount"] for r in sched[:i + 1]) for i in range(4)]
        for i in range(4):
            self.assertAlmostEqual(got[i], min(c[i] for c in cum.values()),
                                   places=2)
        self.assertIn("161(4.01)", _text("canada", "CA-INST-LEAST"))

    @rule("CA-INST-INTEREST")
    def test_credit_offsets_and_25_dollar_floor(self):
        from taxjson.bin import taxjson_instalments as I
        req = self._req(4000.0)
        early = [{"date": "2025-01-02", "amount": 4000.0}]
        r = I.interest_and_penalty(required=req, payments=early,
                                   annual_rate=0.08, end=date(2026, 4, 30))
        self.assertGreater(r["credit_interest"], r["charge_interest"])
        self.assertEqual(r["net_interest"], 0.0)       # never a refund
        small = I.interest_and_penalty(
            required=self._req(400.0), payments=[], annual_rate=0.08,
            end=date(2026, 4, 30))
        self.assertGreater(small["net_interest_computed"], 0.0)
        self.assertLessEqual(small["net_interest_computed"], 25.0)
        self.assertEqual(small["net_interest"], 0.0)

    @rule("CA-INST-PENALTY")
    def test_s163_1_penalty(self):
        from taxjson.bin import taxjson_instalments as I
        r = I.interest_and_penalty(required=self._req(400000.0),
                                   payments=[], annual_rate=0.08,
                                   end=date(2026, 4, 30))
        floor = max(1000.0, 0.25 * r["interest_if_unpaid"])
        self.assertAlmostEqual(r["penalty_floor"], floor, delta=0.01)
        self.assertAlmostEqual(r["penalty"],
                               0.5 * (r["net_interest"] - floor), delta=0.02)
        low = I.interest_and_penalty(required=self._req(4000.0),
                                     payments=[], annual_rate=0.08,
                                     end=date(2026, 4, 30))
        self.assertEqual(low["penalty"], 0.0)

    @rule("CA-INST-PRIOR")
    def test_unknown_prior_year_is_assumed_met(self):
        from taxjson.bin import taxjson_instalments as I
        kw = dict(year=2025, basis="current_year", current_net_tax=10000.0,
                  payments=[], annual_rate=0.08, as_of=date(2025, 7, 1))
        doc = I.build(prior_net_tax=None, second_prior_net_tax=None, **kw)
        self.assertEqual(doc["prior_year_test"], "unknown")
        self.assertTrue(doc["required_at_all"])
        doc = I.build(prior_net_tax=1000.0, second_prior_net_tax=None, **kw)
        self.assertTrue(doc["required_at_all"])
        doc = I.build(prior_net_tax=1000.0, second_prior_net_tax=2000.0,
                      **kw)
        self.assertEqual(doc["prior_year_test"], "not_met")
        self.assertFalse(doc["required_at_all"])


if __name__ == "__main__":
    unittest.main()
