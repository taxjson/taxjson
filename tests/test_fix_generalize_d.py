"""Law constants stated in `taxjson tax-logic`, rendered from the same
constants the code computes with (owner, 2026-10-04: tax-law constants
stay in code, stated in tax-logic). A changed constant changes the
statement; the estimate's printed rates follow the constants too."""
import contextlib
import copy
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tax_rules import rule  # noqa: E402
from taxjson.bin import taxjson_instalments as INST  # noqa: E402
from taxjson.lib import corp_actions as CORP  # noqa: E402
from taxjson.lib import tax_estimate as TE  # noqa: E402
from taxjson.lib import tax_logic as TL  # noqa: E402
from taxjson.lib.tax_estimate import fmt_dollars, fmt_pct  # noqa: E402


def _text(country: str, rule_id: str) -> str:
    """The rendered statement of `rule_id` (fresh: not the catalog)."""
    for _t, rules in TL.rule_sections(country, {"country": country,
                                                "year": 2026}):
        for r in rules:
            if r.id == rule_id:
                return r.text
    raise AssertionError(f"{rule_id} not rendered for {country}")


def _all_text(country: str) -> str:
    return TL.render(country, {"country": country, "year": 2026})


class TestFormatters(unittest.TestCase):
    def test_fmt_pct_has_no_float_noise(self):
        self.assertEqual(fmt_pct(.150198), "15.0198%")
        self.assertEqual(fmt_pct(.5), "50%")
        self.assertEqual(fmt_pct(.038), "3.8%")
        self.assertEqual(fmt_pct(1.38 - 1), "38%")
        self.assertEqual(fmt_pct(.0812), "8.12%")
        self.assertEqual(fmt_dollars(55867.0), "$55,867")


class TestCanadaTables(unittest.TestCase):
    @rule("CA-EST-FED-TABLE")
    def test_federal_table_is_every_vintage_from_the_constants(self):
        txt = _text("canada", "CA-EST-FED-TABLE")
        for y, t in TE._VINTAGES.items():
            self.assertIn(f"{y}: ", txt)
            for upper, rate in t["CA_FED_BRACKETS"]:
                self.assertIn(fmt_pct(rate), txt)
                if upper != float("inf"):
                    self.assertIn(fmt_dollars(upper), txt)
            self.assertIn(fmt_dollars(t["CA_FED_BPA"]), txt)
            self.assertIn(fmt_dollars(t["CA_FED_BPA_MIN"]), txt)

    @rule("CA-EST-FED-TABLE")
    def test_a_changed_constant_changes_the_statement(self):
        v = copy.deepcopy(TE._VINTAGES)
        v["2025"]["CA_FED_BPA"] = 12345.0         # synthetic
        with mock.patch.object(TE, "_VINTAGES", v):
            txt = _text("canada", "CA-EST-FED-TABLE")
        self.assertIn("$12,345", txt)

    @rule("CA-EST-DTC")
    def test_dividend_credit_rates(self):
        txt = _text("canada", "CA-EST-DTC")
        self.assertIn(f"federal {fmt_pct(TE.CA_FED_DTC_ELIGIBLE)}", txt)
        for code, p in TE._VINTAGES["2026"]["CA_PROVINCES"].items():
            self.assertIn(f"{code} {fmt_pct(p['dtc_eligible'])}", txt)
        self.assertIn(f"x{TE.CA_ELIGIBLE_GROSSUP:g}", txt)
        with mock.patch.object(TE, "CA_FED_DTC_ELIGIBLE", .123):
            self.assertIn("federal 12.3%", _text("canada", "CA-EST-DTC"))

    @rule("CA-EST-ON-TABLE")
    def test_ontario_table_surtax_and_assumed_amt_factor(self):
        txt = _text("canada", "CA-EST-ON-TABLE")
        for y, t in TE._VINTAGES.items():
            on = t["CA_PROVINCES"]["ON"]
            self.assertIn(fmt_dollars(on["bpa"]), txt)
            for thr, rate in on["surtax"]:
                self.assertIn(f"{fmt_pct(rate)} of basic provincial tax "
                              f"over {fmt_dollars(thr)}", txt)
        self.assertIn(f"minimum tax factor "
                      f"{fmt_pct(TE._VINTAGES['2026']['CA_PROVINCES']['ON']['amt_factor'])}"
                      f" (assumed", txt)

    @rule("CA-EST-BC-TABLE")
    def test_bc_table(self):
        txt = _text("canada", "CA-EST-BC-TABLE")
        for y, t in TE._VINTAGES.items():
            bc = t["CA_PROVINCES"]["BC"]
            self.assertIn(f"minimum tax factor {fmt_pct(bc['amt_factor'])}",
                          txt)
            self.assertIn(fmt_dollars(bc["brackets"][0][0]), txt)

    @rule("CA-EST-AB-TABLE")
    def test_ab_table_marks_only_the_assumed_year(self):
        txt = _text("canada", "CA-EST-AB-TABLE")
        assumed = [y for y, t in TE._VINTAGES.items()
                   if t["CA_PROVINCES"]["AB"].get("amt_factor_assumed")]
        self.assertEqual(txt.count("(assumed"), len(assumed))

    @rule("CA-EST-OHP")
    def test_ontario_health_premium_chart(self):
        txt = _text("canada", "CA-EST-OHP")
        for floor, at, rate, cap in TE.ON_HEALTH_PREMIUM:
            self.assertIn(f"over {fmt_dollars(floor)}: {fmt_dollars(at)} "
                          f"plus {fmt_pct(rate)} of the excess, at most "
                          f"{fmt_dollars(cap)}", txt)

    @rule("CA-EST-AMT")
    def test_amt_rates_from_the_constants(self):
        txt = _text("canada", "CA-EST-AMT")
        self.assertIn(fmt_pct(TE.CA_AMT_RATE), txt)
        with mock.patch.object(TE, "CA_AMT_RATE", .222):
            self.assertIn("22.2% over an exemption",
                          _text("canada", "CA-EST-AMT"))

    @rule("CA-INST-RATES")
    def test_prescribed_rates_and_the_after_table_assumption(self):
        txt = _text("canada", "CA-INST-RATES")
        self.assertIn(f"Days after {INST.PUBLISHED_THROUGH} assume the "
                      f"last rate ({fmt_pct(INST.PUBLISHED_RATES[-1][1])})",
                      txt)
        rates = [("2030-01-01", .05), ("2030-04-01", .06)]   # synthetic
        with mock.patch.object(INST, "PUBLISHED_RATES", rates), \
                mock.patch.object(INST, "PUBLISHED_THROUGH", "2030-06-30"), \
                mock.patch.object(INST, "PUBLISHED_FROM", "2030-01-01"):
            txt = _text("canada", "CA-INST-RATES")
        self.assertIn("5% from 2030-01-01, 6% from 2030-04-01", txt)
        self.assertIn("Days after 2030-06-30 assume the last rate (6%)", txt)

    @rule("CA-INST-DUE")
    def test_balance_due_day(self):
        from datetime import date
        self.assertEqual(INST.balance_due_date(2025), date(2026, 4, 30))
        self.assertIn("April 30 of the next year",
                      _text("canada", "CA-INST-DUE"))
        with mock.patch.object(INST, "BALANCE_DUE_MONTH_DAY", (5, 1)):
            self.assertIn("May 1 of the next year",
                          _text("canada", "CA-INST-DUE"))
            self.assertEqual(INST.balance_due_date(2025), date(2026, 5, 1))

    @rule("CA-INST-PENALTY")
    def test_penalty_and_thresholds_from_the_constants(self):
        txt = _text("canada", "CA-INST-PENALTY")
        self.assertIn(fmt_dollars(INST.PENALTY_FLOOR), txt)
        self.assertIn(fmt_pct(INST.PENALTY_SHARE), txt)
        self.assertIn(fmt_dollars(INST.THRESHOLD),
                      _text("canada", "CA-RPT-11"))

    @rule("CA-EST-FED-TABLE")
    def test_canada_text_names_no_us_figure(self):
        txt = _all_text("canada")
        for us in ("NIIT", "standard deduction", "§1411",
                   "qualified dividends"):
            self.assertNotIn(us, txt)


class TestUSTables(unittest.TestCase):
    @rule("US-EST-TABLE")
    def test_us_table_is_every_vintage_from_the_constants(self):
        txt = _text("usa", "US-EST-TABLE")
        for y, t in TE._VINTAGES.items():
            self.assertIn(f"{y}: standard deduction "
                          f"{fmt_dollars(t['US_STD_DEDUCTION'])}", txt)
            for upper, _r in t["US_ORD_BRACKETS"] + t["US_LTCG_BRACKETS"]:
                if upper != float("inf"):
                    self.assertIn(fmt_dollars(upper), txt)

    @rule("US-EST-NIIT")
    def test_niit_rate_and_threshold(self):
        txt = _text("usa", "US-EST-NIIT")
        self.assertIn(fmt_pct(TE.US_NIIT_RATE), txt)
        self.assertIn(fmt_dollars(TE.US_NIIT_MAGI_THRESHOLD), txt)
        with mock.patch.object(TE, "US_NIIT_RATE", .05):
            self.assertIn("NIIT is 5% ", _text("usa", "US-EST-NIIT"))

    @rule("US-CORP-04")
    def test_significant_holder_thresholds(self):
        txt = _text("usa", "US-CORP-04")
        self.assertIn(fmt_pct(CORP.US_SIGNIFICANT_HOLDER_PUBLIC), txt)
        self.assertIn(fmt_pct(CORP.US_SIGNIFICANT_HOLDER_PRIVATE), txt)
        self.assertIn(fmt_dollars(CORP.US_SIGNIFICANT_HOLDER_BASIS), txt)
        self.assertIn(CORP.us_significant_holder_test(),
                      CORP._US_SIGNIFICANT_HOLDER.format(reg="1.368-3"))

    @rule("US-RPT-07")
    def test_loss_cap_one_constant(self):
        from taxjson.bin import taxjson_carryover
        self.assertEqual(taxjson_carryover.US_ORDINARY_OFFSET,
                         TE.US_ORDINARY_LOSS_CAP)
        self.assertIn(fmt_dollars(TE.US_ORDINARY_LOSS_CAP),
                      _text("usa", "US-RPT-07"))

    @rule("US-EST-TABLE")
    def test_us_text_names_no_canadian_figure(self):
        txt = _all_text("usa")
        for ca in ("Ontario", fmt_pct(TE.CA_FED_DTC_ELIGIBLE),
                   "basic personal amount", "prescribed"):
            self.assertNotIn(ca, txt)


def _estimate(country, verbose=True):
    from taxjson.bin.taxjson_run import _print_tax_estimate
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        _print_tax_estimate(
            {"settings": {"country": country, "year": 2025,
                          **({"province": "ON"} if country == "canada"
                             else {})}},
            {"realized": 1000.0, "st": 1000.0, "lt": 0.0,
             "div_ca": 500.0, "div_foreign": 200.0, "pil": 0.0},
            "CAD" if country == "canada" else "USD",
            other_income=250000.0, other_losses=0.0,
            province="ON" if country == "canada" else None,
            carrying_charges=100.0 if country == "canada" else 0.0,
            verbose=verbose)
    return " ".join(out.getvalue().split())


class TestEstimatePrintsTheConstants(unittest.TestCase):
    @rule("CA-EST-DTC")
    def test_canada_printed_rates_follow_the_constants(self):
        out = _estimate("canada")
        self.assertIn(f"DTC {fmt_pct(TE.CA_FED_DTC_ELIGIBLE)} x", out)
        self.assertIn(f"x{fmt_pct(TE.CA_INCLUSION)}]", out)
        self.assertIn(f"x{TE.CA_ELIGIBLE_GROSSUP:g},", out)
        with mock.patch.object(TE, "CA_FED_DTC_ELIGIBLE", .111), \
                mock.patch.object(TE, "CA_INCLUSION", .6), \
                mock.patch.object(TE, "CA_FOREIGN_WITHHOLDING", .1), \
                mock.patch.object(TE, "CA_AMT_CARRYING_CHARGE_ALLOWANCE",
                                  .4):
            out = _estimate("canada")
        self.assertIn("DTC 11.1% x", out)
        self.assertIn("x60%]", out)
        self.assertIn("FTC 10% x", out)
        self.assertIn("line 22100; 40% under AMT", out)
        self.assertNotIn("15.0198", out)

    @rule("US-EST-NIIT")
    def test_us_printed_niit_follows_the_constant(self):
        self.assertIn(f"NIIT: {fmt_pct(TE.US_NIIT_RATE)} x",
                      _estimate("usa"))
        with mock.patch.object(TE, "US_NIIT_RATE", .05):
            out = _estimate("usa")
        self.assertIn("NIIT: 5% x", out)
        self.assertNotIn("3.8%", out)


if __name__ == "__main__":
    unittest.main()
