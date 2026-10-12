"""`taxjson div-estimate`: upcoming dividends projected from each held
position's own history in the books (CA-PLAN-06 / US-PLAN-07). Planning
numbers only. Synthetic payers and projects only."""
import json
import tempfile
import unittest

from tax_rules import rule

from _qa_project import project as qa_project, tj
from _style import assert_concise, assert_legend_first, assert_styled
from _style import project as style_project

_W = {"TAXJSON_WIDTH": "100"}


def _div(d, sym, qty, per, acct="margin", cur="CAD", **kw):
    r = {"action": "DIVIDEND", "date": d, "symbol": sym,
         "quantity": qty, "price": per, "gross_amount": qty * per,
         "net_amount": qty * per, "currency": cur, "account": acct}
    r.update(kw)
    return r


def _tax(d, sym, amt, acct="margin", cur="USD"):
    return {"action": "TAX", "date": d, "symbol": sym, "net_amount": amt,
            "currency": cur, "account": acct, "type": "tax"}


def _est(rows, positions, as_of="2026-07-01", months=12, year=2026,
         fx=None, rules=None):
    from taxjson.lib import div_estimate as DE
    return DE.estimate(positions, DE.payments(rows), as_of=as_of,
                       months=months, tax_year=year, base="CAD",
                       fx=fx or (lambda c: None),
                       income_date=rules.income_date if rules else None)


def _pos(sym, qty, acct="margin", group="taxable"):
    return {"account": acct, "group": group, "symbol": sym, "qty": qty}


_Q = ["2025-03-15", "2025-06-15", "2025-09-15", "2025-12-15",
      "2026-03-15", "2026-06-15"]


class TestFrequency(unittest.TestCase):
    def test_buckets(self):
        from taxjson.lib.div_estimate import add_months, detect_frequency
        for step, name in ((1, "monthly"), (3, "quarterly"),
                           (6, "semi-annual"), (12, "annual")):
            ds = [add_months("2020-01-15", step * k) for k in range(5)]
            self.assertEqual(detect_frequency(ds)[:2], (name, step))

    def test_irregular_and_single(self):
        from taxjson.lib.div_estimate import detect_frequency
        self.assertEqual(detect_frequency(["2026-01-02"]),
                         (None, None, None))
        ds = ["2025-01-10", "2025-01-22", "2025-03-08", "2025-07-26",
              "2025-08-12"]
        self.assertEqual(detect_frequency(ds)[:2], (None, None))

    def test_one_skipped_month_stays_monthly(self):
        from taxjson.lib.div_estimate import add_months, detect_frequency
        ds = [add_months("2025-01-28", k) for k in range(9) if k != 4]
        self.assertEqual(detect_frequency(ds)[0], "monthly")

    def test_gaps_while_not_held_and_postings_days_apart(self):
        from taxjson.lib.div_estimate import detect_frequency
        # Quarterly, with a stretch the position was not held.
        ds = ["2023-01-15", "2023-04-15", "2024-07-15", "2024-10-15",
              "2025-01-15"]
        self.assertEqual(detect_frequency(ds)[0], "quarterly")
        # Two brokers posting one payment a day apart: one payment.
        from taxjson.lib import div_estimate as DE
        rows = []
        for d in _Q:
            rows.append(("margin", _div(d, "ZZD.TO", 100, 0.5)))
            d2 = DE.add_months(d, 0)[:8] + "%02d" % (int(d[8:]) + 1)
            rows.append(("qt", _div(d2, "ZZD.TO", 10, 0.5)))
        sched = DE.schedule(DE.payments(rows)["ZZD.TO"])
        self.assertEqual(len(sched), len(_Q))
        self.assertEqual({p.account for p in sched}, {"margin"})
        self.assertEqual(detect_frequency([p.pay_date for p in sched])[0],
                         "quarterly")

    def test_forward_window_tokens(self):
        from taxjson.lib.div_estimate import parse_window
        self.assertEqual(parse_window("6m"), 6)
        self.assertEqual(parse_window("1y"), 12)
        self.assertEqual(parse_window("13w"), 3)
        self.assertIsNone(parse_window("margin"))


class TestEstimate(unittest.TestCase):
    def test_quarterly_projection_and_totals(self):
        rows = [("margin", _div(d, "ZZD.TO", 100, 0.40 if d < "2026"
                                else 0.50)) for d in _Q]
        doc = _est(rows, [_pos("ZZD.TO", 200)])
        r = doc["rows"][0]
        self.assertEqual(r["frequency"], "quarterly")
        self.assertAlmostEqual(r["last_per_share"], 0.5)
        # 12 months back from 2026-07-01: 2025-09, 2025-12, 2026-03,
        # 2026-06.
        self.assertAlmostEqual(r["ttm_per_share"], 0.4 + 0.4 + 0.5 + 0.5)
        self.assertEqual(r["next_pay_date"], "2026-09-15")
        self.assertAlmostEqual(r["next_amount_base"], 100.0)
        self.assertEqual([p["pay_date"] for p in r["projected"]],
                         ["2026-09-15", "2026-12-15", "2027-03-15",
                          "2027-06-15"])
        self.assertAlmostEqual(doc["totals"]["window"]["taxable"], 400.0)
        self.assertAlmostEqual(doc["totals"]["rest_of_year"]["taxable"],
                               200.0)
        self.assertTrue(doc["planning_only"])

    def test_months_window(self):
        rows = [("margin", _div(d, "ZZD.TO", 100, 0.5)) for d in _Q]
        doc = _est(rows, [_pos("ZZD.TO", 100)], months=3)
        self.assertAlmostEqual(doc["totals"]["window"]["taxable"], 50.0)

    def test_fx_and_withholding(self):
        rows = []
        for d in _Q:
            rows.append(("margin", _div(d, "ZZU.US", 10, 1.0, cur="USD")))
            rows.append(("margin", _tax(d, "ZZU.US", 1.5)))
        rows.append(("rrsp", _div("2026-06-15", "ZZU.US", 10, 1.0,
                                  acct="rrsp", cur="USD")))
        doc = _est(rows, [_pos("ZZU.US", 10),
                          _pos("ZZU.US", 10, "rrsp", "sheltered")],
                   fx=lambda c: 1.25 if c == "USD" else None)
        by = {r["account"]: r for r in doc["rows"]}
        self.assertAlmostEqual(by["margin"]["next_amount_base"], 12.5)
        self.assertAlmostEqual(by["margin"]["withholding_rate"], 0.15)
        self.assertIsNone(by["rrsp"]["withholding_rate"])
        self.assertAlmostEqual(doc["totals"]["window"]["taxable"], 50.0)
        self.assertAlmostEqual(doc["totals"]["window"]["sheltered"], 50.0)
        self.assertAlmostEqual(
            doc["totals"]["withholding_window"]["taxable"], 7.5)

    def test_no_rate_leaves_totals(self):
        rows = [("margin", _div(d, "ZZU.US", 10, 1.0, cur="USD"))
                for d in _Q]
        doc = _est(rows, [_pos("ZZU.US", 10)])
        self.assertIsNone(doc["rows"][0]["next_amount_base"])
        self.assertEqual(doc["totals"]["window"]["taxable"], 0.0)
        self.assertEqual(doc["no_fx"], ["USD"])

    def test_omitted_irregular_stopped_and_late(self):
        rows = [("margin", _div("2026-02-01", "ZZS.TO", 10, 2.0))]
        rows += [("margin", _div(d, "ZZL.TO", 10, 1.0))
                 for d in ("2025-06-15", "2025-09-15", "2025-12-15",
                           "2026-03-15")]                  # 06-15 missed
        rows += [("margin", _div(d, "ZZP.TO", 10, 1.0))
                 for d in ("2025-03-15", "2025-06-15", "2025-09-15",
                           "2025-12-15")]                  # 2 missed
        doc = _est(rows, [_pos("ZZS.TO", 10), _pos("ZZL.TO", 10),
                          _pos("ZZP.TO", 10), _pos("ZZN.TO", 5)])
        by = {r["symbol"]: r for r in doc["rows"]}
        self.assertEqual(by["ZZS.TO"]["status"], "irregular")
        # Pro rata: 2.00/share x 10 over 12 months.
        self.assertAlmostEqual(by["ZZS.TO"]["window_amount_base"], 20.0)
        self.assertEqual(by["ZZL.TO"]["status"], "late")
        self.assertEqual(by["ZZL.TO"]["next_pay_date"], "2026-09-15")
        self.assertEqual(by["ZZP.TO"]["status"], "stopped")
        self.assertEqual(by["ZZP.TO"]["window_amount_base"], 0.0)
        self.assertEqual([o["symbol"] for o in doc["omitted_no_history"]],
                         ["ZZN.TO"])

    def test_reversal_nets_and_deemed_dividend_is_not_a_payment(self):
        rows = [("margin", _div(d, "ZZD.TO", 100, 0.5)) for d in _Q]
        rows.append(("margin", _div("2026-06-15", "ZZD.TO", 100, -0.5)))
        rows.append(("margin", _div("2026-06-15", "ZZD.TO", 100, 0.6)))
        rows.append(("margin", _div("2026-05-01", "ZZD.TO", 0, 0,
                                    gross_amount=99.0,
                                    corp_event_id="x")))
        doc = _est(rows, [_pos("ZZD.TO", 100)])
        r = doc["rows"][0]
        self.assertEqual(r["payments"], 6)
        self.assertAlmostEqual(r["last_per_share"], 0.6)

    def test_history_after_the_date_is_unknown(self):
        rows = [("margin", _div(d, "ZZD.TO", 100, 0.5)) for d in _Q]
        doc = _est(rows, [_pos("ZZD.TO", 100)], as_of="2026-04-01")
        self.assertEqual(doc["rows"][0]["last_pay_date"], "2026-03-15")
        self.assertEqual(doc["rows"][0]["next_pay_date"], "2026-06-15")

    @rule("CA-PLAN-06")
    def test_canadian_trust_dated_by_record_date(self):
        """A trust's distribution paid in January with a December record
        date counts in the earlier tax year (CA-INC-DATE-TRUST), so the
        projected January payment is in this year's estimate."""
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules.from_settings({"country": "canada"})
        rows = []
        from taxjson.lib.div_estimate import add_months
        for k in range(10):
            pay = add_months("2026-02-07", k)
            rec = add_months("2026-01-30", k)
            rows.append(("margin", _div(pay, "ZZT.TO", 100, 0.1,
                                        record_date=rec,
                                        income_label="distribution")))
        doc = _est(rows, [_pos("ZZT.TO", 100)], as_of="2026-12-10",
                   months=2, rules=rules)
        r = doc["rows"][0]
        jan = [p for p in r["projected"] if p["pay_date"] == "2027-01-07"]
        self.assertEqual(jan[0]["tax_date"], "2026-12-30")
        # The Dec and Jan payments both fall in 2026.
        self.assertAlmostEqual(doc["totals"]["rest_of_year"]["taxable"],
                               20.0)

    @rule("US-PLAN-07")
    def test_us_pay_date_and_listed_ric(self):
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules.from_settings({
            "country": "usa", "ric_january_dividends": ["ZZF.US"]})
        rows = [("margin", _div(d, "ZZF.US", 10, 1.0, cur="USD"))
                for d in ("2025-01-10", "2025-04-10", "2025-07-10",
                          "2025-10-10")]
        doc = _est(rows, [_pos("ZZF.US", 10)], as_of="2025-12-01",
                   year=2025, rules=rules, fx=lambda c: 1.0)
        p = doc["rows"][0]["projected"][0]
        self.assertEqual(p["pay_date"], "2026-01-10")
        self.assertEqual(p["tax_date"], "2025-12-31")
        self.assertAlmostEqual(doc["totals"]["rest_of_year"]["taxable"],
                               10.0)

    def test_render_budget_and_details(self):
        from taxjson.lib import div_estimate as DE
        from taxjson.lib import out
        rows = [("margin", _div(d, "ZZD.TO", 100, 0.5)) for d in _Q]
        rows.append(("margin", _div("2026-02-01", "ZZS.TO", 10, 2.0)))
        doc = _est(rows, [_pos("ZZD.TO", 100), _pos("ZZS.TO", 10),
                          _pos("ZZN.TO", 1)])
        text = DE.render(doc).text()
        self.assertLessEqual(len(out.prose_lines(text)), 6, text)
        assert_legend_first(self, text, "Planning numbers, not filing")
        self.assertIn("1 held position(s) with no dividend history", text)
        self.assertIn("! 1 payer(s) to check: ZZS.TO", text)
        full = DE.render(doc, details=True).text()
        self.assertIn("SCHEDULES AND PROJECTED PAYMENTS", full)
        self.assertIn("ZZN.TO (margin)", full)


class TestDivEstimateCli(unittest.TestCase):
    @rule("CA-PLAN-06")
    def test_project_end_to_end(self):
        tt = ["BUYSELL 2025-01-02 10:00:00 ZZD.TO 100 CAD 20.00 2000"]
        tt += [f"DIVIDEND {d} 16:00:00 ZZD.TO 100 CAD 0.50 50.00"
               for d in _Q if d < "2026"]
        tt += ["BUYSELL 2025-01-02 10:00:00 ZZN.TO 10 CAD 5.00 50"]
        with tempfile.TemporaryDirectory() as tmp:
            root = qa_project(
                tmp, "p", {"margin/m.tt": "\n".join(tt) + "\n",
                           "tfsa/t.tt": "BUYSELL 2025-01-02 10:00:00 "
                                        "ZZD.TO 40 CAD 20.00 800\n"},
                extra_accounts='[accounts.tfsa]\ntype = "sheltered"\n')
            cfg = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                cfg.replace("year = 2024", "year = 2025").replace(
                    "option_grant_timing_since = 2024",
                    "option_grant_timing_since = 2025"))
            tj(root, "run", "--no-input")
            doc = json.loads(tj(root, "div-estimate", "--date",
                                "2025-12-20", "--json").stdout)
            by = {(r["account"], r["symbol"]): r for r in doc["rows"]}
            self.assertEqual(by[("margin", "ZZD.TO")]["frequency"],
                             "quarterly")
            self.assertEqual(by[("margin", "ZZD.TO")]["next_pay_date"],
                             "2026-03-15")
            # The TFSA holds the same payer: estimated from its history.
            self.assertAlmostEqual(
                by[("tfsa", "ZZD.TO")]["window_amount_base"], 80.0)
            self.assertAlmostEqual(doc["totals"]["window"]["taxable"],
                                   200.0)
            self.assertEqual([o["symbol"] for o in
                              doc["omitted_no_history"]], ["ZZN.TO"])
            # An account filter and a forward window.
            one = json.loads(tj(root, "div-estimate", "6m", "tfsa",
                                "--date", "2025-12-20", "--json").stdout)
            self.assertEqual({r["account"] for r in one["rows"]}, {"tfsa"})
            self.assertEqual(one["months"], 6)
            bad = tj(root, "div-estimate", "nosuch", check=False)
            self.assertEqual(bad.returncode, 2)


class TestDivEstimateStyle(unittest.TestCase):
    def test_default_view_budget_both_countries(self):
        for c in ("canada", "usa"):
            with self.subTest(country=c):
                r = style_project(c).run("div-estimate", "--date",
                                         "2024-11-25", **_W)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr,
                               legend="Planning numbers, not filing")
                assert_styled(self, r.stdout)
                self.assertIn("Next 12 months:", r.stdout)
                self.assertIn("tjs div-estimate --details",
                              r.stdout.strip().splitlines()[-1])

    def test_details(self):
        r = style_project("canada").run("div-estimate", "--date",
                                        "2024-11-25", "--details", **_W)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("HOW IT IS ESTIMATED", r.stdout)
        self.assertIn("RY.TO (tfsa)", r.stdout)


if __name__ == "__main__":
    unittest.main()
