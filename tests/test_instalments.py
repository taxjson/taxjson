"""Canadian tax instalments: schedule, offset interest, s.163.1."""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.bin.taxjson_instalments import (build, due_dates,
                                             interest_and_penalty,
                                             required_schedule)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDueDates(unittest.TestCase):
    def test_four_dates_with_weekend_rollover(self):
        # 2026: Mar 15 = Sunday -> Mar 16; Jun 15 = Monday;
        # Sep 15 = Tuesday; Dec 15 = Tuesday.
        d = due_dates(2026)
        self.assertEqual([x.isoformat() for x in d],
                         ["2026-03-16", "2026-06-15", "2026-09-15",
                          "2026-12-15"])
        for x in d:
            self.assertLess(x.weekday(), 5, "never a weekend")


class TestSchedule(unittest.TestCase):
    def test_current_year_splits_in_four(self):
        rows = required_schedule(year=2026, basis="current_year",
                                 current_net_tax=80000.0)
        self.assertEqual([r["amount"] for r in rows], [20000.0] * 4)

    def test_prior_year_uses_last_years_tax(self):
        rows = required_schedule(year=2026, basis="prior_year",
                                 current_net_tax=80000.0,
                                 prior_net_tax=40000.0)
        self.assertEqual([r["amount"] for r in rows], [10000.0] * 4)

    def test_cra_reminder_front_loads_the_second_prior_year(self):
        # First two: 1/4 of the second preceding year (40,000 -> 10,000
        # each). Last two: the rest of the prior year (60,000 - 20,000
        # = 40,000) split -> 20,000 each.
        rows = required_schedule(year=2026, basis="cra_reminder",
                                 current_net_tax=99999.0,
                                 prior_net_tax=60000.0,
                                 second_prior_net_tax=40000.0)
        self.assertEqual([r["amount"] for r in rows],
                         [10000.0, 10000.0, 20000.0, 20000.0])

    def test_missing_inputs_refuse(self):
        with self.assertRaises(ValueError):
            required_schedule(year=2026, basis="prior_year",
                              current_net_tax=1.0)
        with self.assertRaises(ValueError):
            required_schedule(year=2026, basis="nonsense",
                              current_net_tax=1.0)


class TestInterest(unittest.TestCase):
    _REQ = [{"date": "2026-03-16", "amount": 10000.0},
            {"date": "2026-06-15", "amount": 10000.0},
            {"date": "2026-09-15", "amount": 10000.0},
            {"date": "2026-12-15", "amount": 10000.0}]

    def test_paying_on_time_costs_nothing(self):
        paid = [{"date": r["date"], "amount": r["amount"]}
                for r in self._REQ]
        ip = interest_and_penalty(required=self._REQ, payments=paid,
                                  annual_rate=0.08,
                                  end=date(2027, 4, 30))
        self.assertAlmostEqual(ip["net_interest"], 0.0, places=2)
        self.assertAlmostEqual(ip["penalty"], 0.0, places=2)

    def test_paying_nothing_accrues_and_matches_the_unpaid_baseline(self):
        ip = interest_and_penalty(required=self._REQ, payments=[],
                                  annual_rate=0.08,
                                  end=date(2027, 4, 30))
        self.assertGreater(ip["net_interest"], 0.0)
        self.assertAlmostEqual(ip["net_interest"],
                               ip["interest_if_unpaid"], places=2)

    def test_early_overpayment_credits_offset_a_later_shortfall(self):
        # Everything prepaid on the first date: no charge survives.
        early = [{"date": "2026-03-16", "amount": 40000.0}]
        ip = interest_and_penalty(required=self._REQ, payments=early,
                                  annual_rate=0.08,
                                  end=date(2027, 4, 30))
        self.assertAlmostEqual(ip["net_interest"], 0.0, places=2)
        self.assertGreater(ip["credit_interest"], 0.0)

    def test_credit_never_becomes_a_refund(self):
        ip = interest_and_penalty(
            required=self._REQ,
            payments=[{"date": "2026-03-16", "amount": 400000.0}],
            annual_rate=0.08, end=date(2027, 4, 30))
        self.assertGreaterEqual(ip["net_interest"], 0.0)

    def test_penalty_is_half_the_excess_over_the_floor(self):
        # A big enough shortfall to clear the $1,000 floor. With NO
        # payments the floor is 25% of the same interest, so the
        # penalty is half of the remaining 75%.
        big = [{"date": "2026-03-16", "amount": 500000.0},
               {"date": "2026-06-15", "amount": 500000.0},
               {"date": "2026-09-15", "amount": 500000.0},
               {"date": "2026-12-15", "amount": 500000.0}]
        ip = interest_and_penalty(required=big, payments=[],
                                  annual_rate=0.08,
                                  end=date(2027, 4, 30))
        self.assertGreater(ip["net_interest"], 1000.0)
        expected_floor = 0.25 * ip["interest_if_unpaid"]
        # Cent-level tolerance: the doc rounds each figure once.
        self.assertAlmostEqual(ip["penalty_floor"], expected_floor,
                               delta=0.01)
        self.assertAlmostEqual(
            ip["penalty"],
            0.5 * (ip["net_interest"] - expected_floor), delta=0.01)

    def test_small_interest_is_never_penalized(self):
        ip = interest_and_penalty(
            required=self._REQ,
            payments=[{"date": "2026-12-20", "amount": 40000.0}],
            annual_rate=0.02, end=date(2027, 4, 30))
        self.assertGreater(ip["net_interest"], 0.0)
        self.assertLess(ip["net_interest"], 1000.0)
        self.assertAlmostEqual(ip["penalty"], 0.0, places=2)


class TestBuild(unittest.TestCase):
    def test_below_threshold_needs_no_instalments(self):
        doc = build(year=2026, basis="current_year",
                    current_net_tax=2500.0, payments=[],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        self.assertFalse(doc["required_at_all"])

    def test_catch_up_split_over_remaining_dates(self):
        doc = build(year=2026, basis="current_year",
                    current_net_tax=80000.0,
                    payments=[{"date": "2026-03-16", "amount": 20000.0}],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        # Two dates left (Sep, Dec); 60,000 outstanding -> 30,000 each.
        self.assertEqual(doc["remaining_dates"],
                         ["2026-09-15", "2026-12-15"])
        self.assertAlmostEqual(doc["per_remaining_date"], 30000.0)
        # The June date is short — it was never paid.
        june = next(r for r in doc["required"]
                    if r["date"] == "2026-06-15")
        self.assertEqual(june["status"], "missed")

    def test_statuses_track_cumulative_payment(self):
        doc = build(year=2026, basis="current_year",
                    current_net_tax=40000.0,
                    payments=[{"date": "2026-03-16", "amount": 10000.0},
                              {"date": "2026-06-15", "amount": 10000.0}],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        by = {r["date"]: r["status"] for r in doc["required"]}
        self.assertEqual(by["2026-03-16"], "paid")
        self.assertEqual(by["2026-06-15"], "paid")
        self.assertEqual(by["2026-09-15"], "upcoming")


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
         str(root), *args],
        cwd=REPO_ROOT, capture_output=True, text=True)


class TestInstalmentsCli(unittest.TestCase):
    def _project(self, tmp, instalments=True):
        root = Path(tmp)
        inst = ('[instalments]\nbasis = "current_year"\n'
                'prescribed_rate = 0.08\n'
                'paid = [{ date = "2026-03-16", amount = 5000 }]\n'
                if instalments else "")
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n' + inst)
        work = root / "work"
        work.mkdir()
        (work / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": 2026, "total_gain": 300000.0},
             "transactions": [
                 {"symbol": "AAA.TO", "date": "2026-02-02", "qty": -100,
                  "currency": "CAD", "proceeds": 400000.0,
                  "cost": 100000.0, "gain": 300000.0,
                  "days_held": 200, "commission": 0.0, "fee": 0.0}],
             "inventory": [], "wash_sales": []}))
        return root

    def test_schedule_interest_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _cli(root, "instalments")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("TAX INSTALMENTS", r.stdout)
            self.assertIn("2026-03-16", r.stdout)
            self.assertIn("INTEREST (offset method", r.stdout)
            for line in r.stdout.splitlines():
                self.assertLessEqual(len(line), 78, repr(line))
            rj = _cli(root, "instalments", "--json")
        doc = json.loads(rj.stdout)
        self.assertEqual(len(doc["required"]), 4)
        self.assertGreater(doc["required_total"], 0)
        self.assertAlmostEqual(doc["paid_total"], 5000.0)
        # Underpaid all year -> interest accrues.
        self.assertGreater(doc["net_interest"], 0.0)

    def test_estimate_shows_the_compact_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _cli(root, "estimate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("INSTALMENTS", r.stdout)
        self.assertIn("taxjson instalments", r.stdout)

    def test_no_config_is_a_clean_error_with_an_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, instalments=False)
            r = _cli(root, "instalments")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("[instalments]", r.stderr)
            # ... and estimate stays quiet about instalments.
            r2 = _cli(root, "estimate")
        self.assertNotIn("INSTALMENTS", r2.stdout)

    def test_bad_payment_row_refuses_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(tmp)
            (root / "taxjson.toml").write_text(
                (root / "taxjson.toml").read_text().replace(
                    'paid = [{ date = "2026-03-16", amount = 5000 }]',
                    'paid = [{ date = "nope", amount = 5000 }]'))
            r = _cli(root, "instalments")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("YYYY-MM-DD", r.stderr)




class TestQuarterlyRateResets(unittest.TestCase):
    """CRA resets the prescribed rate quarterly and charges each day at
    the rate in force THAT day, compounding across the seam — a single
    annual rate drifts as soon as a reset lands mid-period."""

    _REQ = [{"date": "2026-03-16", "amount": 10000.0},
            {"date": "2026-06-15", "amount": 10000.0},
            {"date": "2026-09-15", "amount": 10000.0},
            {"date": "2026-12-15", "amount": 10000.0}]

    def test_rate_lookup_picks_the_segment_in_force(self):
        from taxjson.bin.taxjson_instalments import (normalize_rates,
                                                     rate_on)
        sched = normalize_rates([{"from": "2026-07-01", "rate": 0.09},
                                 {"from": "2026-01-01", "rate": 0.08}])
        self.assertEqual([s[0] for s in sched],
                         ["2026-01-01", "2026-07-01"])   # sorted
        self.assertAlmostEqual(rate_on(date(2026, 6, 30), sched), 0.08)
        self.assertAlmostEqual(rate_on(date(2026, 7, 1), sched), 0.09)
        # Before the first dated segment: the earliest rate given.
        self.assertAlmostEqual(rate_on(date(2025, 1, 1), sched), 0.08)

    def test_scalar_rate_still_works(self):
        from taxjson.bin.taxjson_instalments import (normalize_rates,
                                                     rate_on)
        sched = normalize_rates(0.08)
        self.assertAlmostEqual(rate_on(date(2026, 5, 5), sched), 0.08)

    def test_a_mid_year_hike_costs_more_than_the_low_rate_alone(self):
        flat_low = interest_and_penalty(
            required=self._REQ, payments=[], annual_rate=0.08,
            end=date(2027, 4, 30))
        flat_high = interest_and_penalty(
            required=self._REQ, payments=[], annual_rate=0.10,
            end=date(2027, 4, 30))
        stepped = interest_and_penalty(
            required=self._REQ, payments=[],
            annual_rate=[{"from": "2026-01-01", "rate": 0.08},
                         {"from": "2026-10-01", "rate": 0.10}],
            end=date(2027, 4, 30))
        # Strictly between the two flat-rate bounds.
        self.assertGreater(stepped["net_interest"],
                           flat_low["net_interest"])
        self.assertLess(stepped["net_interest"],
                        flat_high["net_interest"])
        self.assertEqual(len(stepped["rate_schedule"]), 2)

    def test_report_names_every_rate_it_used(self):
        from taxjson.bin.taxjson_instalments import build, render
        doc = build(year=2026, basis="current_year",
                    current_net_tax=80000.0, payments=[],
                    annual_rate=[{"from": "2026-01-01", "rate": 0.08},
                                 {"from": "2026-10-01", "rate": 0.10}],
                    as_of=date(2026, 12, 31))
        text = render(doc, "CAD")
        self.assertIn("8.00%", text)
        self.assertIn("10.00% from 2026-10-01", text)
        for line in text.splitlines():
            self.assertLessEqual(len(line), 78, repr(line))


class TestInterestUsesTheCheapestBasis(unittest.TestCase):
    """ITA 161(4.01): CRA assesses deficient-instalment interest on the
    LEAST of the current-year, prior-year and no-calculation methods —
    so following ANY one correctly is interest-free. Charging only
    against the chosen basis over-stated what CRA would assess."""

    def test_paying_the_prior_year_amounts_is_interest_free(self):
        # A blowout year (200k) after a quiet one (40k): the taxpayer
        # follows the prior-year option and pays 10k on each date.
        paid = [{"date": d, "amount": 10000.0}
                for d in ("2026-03-16", "2026-06-15", "2026-09-15",
                          "2026-12-15")]
        doc = build(year=2026, basis="current_year",
                    current_net_tax=200000.0, prior_net_tax=40000.0,
                    payments=paid, annual_rate=0.08,
                    as_of=date(2027, 4, 30))
        self.assertAlmostEqual(doc["net_interest"], 0.0, places=2)
        self.assertEqual(doc["interest_basis"], "prior_year")
        # The SCHEDULE still shows what the chosen basis calls for.
        self.assertAlmostEqual(doc["required_total"], 200000.0)

    def test_without_prior_year_figures_only_current_is_considered(self):
        paid = [{"date": d, "amount": 10000.0}
                for d in ("2026-03-16", "2026-06-15", "2026-09-15",
                          "2026-12-15")]
        doc = build(year=2026, basis="current_year",
                    current_net_tax=200000.0, payments=paid,
                    annual_rate=0.08, as_of=date(2027, 4, 30))
        self.assertEqual(doc["interest_bases_considered"],
                         ["current_year"])
        self.assertGreater(doc["net_interest"], 0.0)

    def test_report_explains_the_governing_basis(self):
        from taxjson.bin.taxjson_instalments import render
        paid = [{"date": "2026-03-16", "amount": 10000.0,
                 "note": "2025 refund transferred"}]
        doc = build(year=2026, basis="current_year",
                    current_net_tax=200000.0, prior_net_tax=40000.0,
                    second_prior_net_tax=20000.0, payments=paid,
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        text = render(doc, "CAD")
        self.assertIn("PAYMENTS APPLIED", text)
        self.assertIn("2025 refund transferr", text)   # may elide
        self.assertIn("161(4.01)", text)
        for line in text.splitlines():
            self.assertLessEqual(len(line), 78, repr(line))


class TestRequirementTest(unittest.TestCase):
    """CRA's two-limb test: net tax owing over the threshold in the
    CURRENT year AND in either of the two preceding years. A
    placeholder `prior_year_net_tax = 0` reads as 'I owed nothing',
    which silently suppressed both the obligation and all interest."""

    def test_zero_priors_mean_no_instalments_required(self):
        doc = build(year=2026, basis="current_year",
                    current_net_tax=81000.0, prior_net_tax=0.0,
                    second_prior_net_tax=0.0, payments=[],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        self.assertFalse(doc["required_at_all"])
        self.assertEqual(doc["prior_year_test"], "not_met")

    def test_one_prior_year_over_threshold_is_enough(self):
        doc = build(year=2026, basis="current_year",
                    current_net_tax=81000.0, prior_net_tax=0.0,
                    second_prior_net_tax=40000.0, payments=[],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        self.assertTrue(doc["required_at_all"])
        self.assertEqual(doc["prior_year_test"], "met")

    def test_unknown_priors_assume_required(self):
        doc = build(year=2026, basis="current_year",
                    current_net_tax=81000.0, payments=[],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        self.assertTrue(doc["required_at_all"])
        self.assertEqual(doc["prior_year_test"], "unknown")

    def test_report_names_the_failing_limb_and_the_placeholder_risk(self):
        from taxjson.bin.taxjson_instalments import render
        doc = build(year=2026, basis="current_year",
                    current_net_tax=81000.0, prior_net_tax=0.0,
                    second_prior_net_tax=0.0, payments=[],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        text = render(doc, "CAD")
        flat = " ".join(text.split())      # wrapping splits phrases
        self.assertIn("either of the two preceding years", flat)
        self.assertIn("placeholders", flat)
        for line in text.splitlines():
            self.assertLessEqual(len(line), 78, repr(line))



class TestStatusVocabulary(unittest.TestCase):
    """PAID / LATE / MISSED / UPCOMING — 'SHORT' conflated a date that
    was covered late (interest ran, nothing outstanding) with one
    still owing."""

    def test_late_payment_reclassifies_an_earlier_date(self):
        # Nothing on the March or June dates; a lump sum in August
        # covers March's requirement but not June's.
        doc = build(year=2026, basis="current_year",
                    current_net_tax=80000.0,
                    payments=[{"date": "2026-08-17",
                               "amount": 25000.0}],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        by = {r["date"]: r["status"] for r in doc["required"]}
        self.assertEqual(by["2026-03-16"], "late")    # 20k <= 25k paid
        self.assertEqual(by["2026-06-15"], "missed")  # 40k > 25k paid
        self.assertEqual(by["2026-09-15"], "upcoming")
        # LATE is not free — interest still ran from the due date.
        self.assertGreater(doc["net_interest"], 0.0)

    def test_future_payments_do_not_count_as_paid_today(self):
        doc = build(year=2026, basis="current_year",
                    current_net_tax=80000.0,
                    payments=[{"date": "2026-12-15",
                               "amount": 80000.0}],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        by = {r["date"]: r["status"] for r in doc["required"]}
        self.assertEqual(by["2026-03-16"], "missed")
        self.assertAlmostEqual(doc["paid_total"], 0.0)


class TestEstimateAndInstalmentsAgree(unittest.TestCase):
    """The documented invariant, which a 5.5x divergence slipped past:
    `taxjson instalments` shells out to `estimate`, so both must
    resolve the SAME other-income inputs. Previously the standalone
    command always assumed zero."""

    def _project(self, tmp, other_income=None):
        root = Path(tmp)
        est = (f"\n[estimate]\nother_income = {other_income}\n"
               if other_income is not None else "")
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[instalments]\nbasis = "current_year"\n'
            'prescribed_rate = 0.08\n' + est)
        work = root / "work"
        work.mkdir()
        (work / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": 2026, "total_gain": 200000.0},
             "transactions": [
                 {"symbol": "AAA.TO", "date": "2026-02-02", "qty": -100,
                  "currency": "CAD", "proceeds": 300000.0,
                  "cost": 100000.0, "gain": 200000.0,
                  "days_held": 200, "commission": 0.0, "fee": 0.0}],
             "inventory": [], "wash_sales": []}))
        return root

    def _required(self, out):
        return json.loads(out)["required_total"]

    def test_agree_with_no_other_income(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            a = json.loads(_cli(root, "instalments", "--json").stdout)
            b = json.loads(_cli(root, "estimate", "--json").stdout)
        self.assertAlmostEqual(a["required_total"],
                               b["instalments"]["required_total"],
                               places=2)

    def test_agree_when_other_income_is_configured(self):
        # [estimate] other_income feeds BOTH commands — the standalone
        # instalments run used to ignore it entirely.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, other_income=150000)
            a = json.loads(_cli(root, "instalments", "--json").stdout)
            b = json.loads(_cli(root, "estimate", "--json").stdout)
            plain = self._project(tempfile.mkdtemp())
            c = json.loads(_cli(plain, "instalments", "--json").stdout)
        self.assertAlmostEqual(a["required_total"],
                               b["instalments"]["required_total"],
                               places=2)
        self.assertGreater(a["required_total"], c["required_total"],
                           "other income must raise net tax owing")

    def test_us_project_never_gets_a_canadian_instalment_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "taxjson.toml").write_text(
                (root / "taxjson.toml").read_text()
                .replace('country = "canada"', 'country = "usa"'))
            r = _cli(root, "instalments")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("canada only", r.stderr)
            doc = json.loads(_cli(root, "sum", "--other-income", "0",
                                  "--json").stdout)
        self.assertNotIn("instalments", doc)


class TestConfigAbuse(unittest.TestCase):
    """`_instalment_config`'s promise is 'loud on bad input: these
    figures move money'. Each of these was silent or a traceback."""

    def _run(self, instalments_toml, year=2026):
        root = Path(tempfile.mkdtemp())
        (root / "taxjson.toml").write_text(
            f'[settings]\nyear = {year}\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            + instalments_toml)
        work = root / "work"
        work.mkdir()
        (work / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": year}, "transactions": [],
             "inventory": [], "wash_sales": []}))
        return _cli(root, "instalments")

    def test_payment_dated_outside_the_tax_year_refuses(self):
        # Year-rollover trap: last year's payments left in the table
        # made the schedule read "met" with zero interest.
        r = self._run('[instalments]\nbasis = "current_year"\n'
                      'paid = [{ date = "2025-03-17", amount = 20000 }]\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("outside tax year 2026", r.stderr)

    def test_unpadded_dates_refuse_instead_of_crashing_later(self):
        r = self._run('[instalments]\nbasis = "current_year"\n'
                      'paid = [{ date = "2026-3-16", amount = 5000 }]\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("YYYY-MM-DD", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_both_rate_keys_refuse(self):
        r = self._run('[instalments]\nprescribed_rate = 0.02\n'
                      'prescribed_rates = [{ from = "2026-01-01", '
                      'rate = 0.20 }]\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not both", r.stderr)

    def test_percentage_instead_of_fraction_refuses(self):
        r = self._run('[instalments]\nprescribed_rate = 8\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DECIMAL fraction", r.stderr)

    def test_scalar_where_a_list_belongs_refuses(self):
        for line in ("paid = 5", "prescribed_rates = 0.08"):
            r = self._run(f'[instalments]\n{line}\n')
            self.assertNotEqual(r.returncode, 0, line)
            self.assertIn("LIST", r.stderr, line)
            self.assertNotIn("Traceback", r.stderr, line)

    def test_negative_payment_refuses(self):
        r = self._run('[instalments]\n'
                      'paid = [{ date = "2026-03-16", amount = -500 }]\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("negative", r.stderr)

    def test_unknown_key_warns_with_a_suggestion(self):
        from taxjson.bin.taxjson_run import validate_config
        w = validate_config(
            {"settings": {"year": 2026},
             "accounts": {"m": {"type": "taxable"}},
             "instalments": {"priorr_year_net_tax": 60000}})
        self.assertTrue(any("prior_year_net_tax" in x for x in w), w)

    def test_missing_year_is_a_clean_error(self):
        root = Path(tempfile.mkdtemp())
        (root / "taxjson.toml").write_text(
            '[settings]\ncountry = "canada"\nbase_currency = "CAD"\n'
            'province = "ON"\n[accounts.margin]\ntype = "taxable"\n'
            '[instalments]\nbasis = "current_year"\n')
        (root / "work").mkdir()
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {}, "transactions": [], "inventory": [],
             "wash_sales": []}))
        r = _cli(root, "instalments")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)


class TestInterestGoldenValues(unittest.TestCase):
    """Every other interest test is a relative inequality; these pin
    the actual arithmetic (compounding base, /365, inclusive window)
    against an independently derived closed form."""

    def test_single_shortfall_matches_closed_form(self):
        start, end = date(2026, 3, 16), date(2026, 3, 26)
        req = [{"date": start.isoformat(), "amount": 10000.0}]
        ip = interest_and_penalty(required=req, payments=[],
                                  annual_rate=0.08, end=end)
        days = (end - start).days + 1          # inclusive walk
        expected = 10000.0 * ((1 + 0.08 / 365) ** days - 1)
        self.assertAlmostEqual(ip["net_interest"], round(expected, 2),
                               places=2)

    def test_prepayment_before_the_first_due_date_earns_credit(self):
        # CRA's contra interest runs from the DATE OF PAYMENT; the
        # walk used to start at the first due date and discard it.
        req = [{"date": "2026-03-16", "amount": 10000.0}]
        early = interest_and_penalty(
            required=req,
            payments=[{"date": "2026-01-02", "amount": 10000.0}],
            annual_rate=0.08, end=date(2026, 3, 26))
        on_time = interest_and_penalty(
            required=req,
            payments=[{"date": "2026-03-16", "amount": 10000.0}],
            annual_rate=0.08, end=date(2026, 3, 26))
        self.assertGreater(early["credit_interest"],
                           on_time["credit_interest"])


class TestRenderBranches(unittest.TestCase):
    """Four render paths no test had ever executed."""

    def _r(self, **kw):
        from taxjson.bin.taxjson_instalments import render
        base = dict(year=2026, basis="current_year", payments=[],
                    annual_rate=0.08, as_of=date(2026, 8, 27))
        base.update(kw)
        text = render(build(**base), "CAD")
        for line in text.splitlines():
            self.assertLessEqual(len(line), 78, repr(line))
        return text

    def test_below_threshold(self):
        flat = " ".join(self._r(current_net_tax=2500.0).split()).lower()
        self.assertIn("no instalments required", flat)

    def test_penalty_prose(self):
        t = self._r(current_net_tax=2000000.0,
                    as_of=date(2027, 4, 30))
        self.assertIn("PENALTY", t)
        self.assertIn("half the excess", t)

    def test_no_dates_left(self):
        t = self._r(current_net_tax=80000.0, as_of=date(2027, 4, 30))
        self.assertIn("balance is due April 30", t)

    def test_vacuous_governing_basis_warns(self):
        # The realistic trap: a placeholder 0 in ONE field. The
        # second-prior year clears the requirement test, so
        # instalments ARE owed — but the prior-year schedule requires
        # nothing, governs as cheapest, and would silently show zero
        # interest.
        t = self._r(current_net_tax=80000.0, prior_net_tax=0.0,
                    second_prior_net_tax=50000.0)
        flat = " ".join(t.split())
        self.assertIn("No interest can arise", flat)
        self.assertIn("placeholder", flat)


if __name__ == "__main__":
    unittest.main()
