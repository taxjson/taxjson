"""Workflow audit 2026-10-23, items 2 and 4: what close-year's lock
carries forward (~/workflow-audit-2026-10-23.md is the maintainer's
report).

Every fixture is SYNTHETIC: made-up years and amounts, no accounts.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule


def _nets(**by_year):
    return {int(y[1:]): {"net": v, "st": 0.0, "lt": 0.0, "dispositions": 1}
            for y, v in by_year.items()}


# ---------------------------------------------------------------------------
# Item 2: two loss years carry back into the same gain year

class TestSecondLossYearCarryBack(unittest.TestCase):
    # 2023 gain 1,000; 2024 loss 1,500 (locked: 600 carried back, 900
    # carried out); 2025 loss 500. [carryover] claimed 2023 = 1,000 in
    # the 2025 project: the total applied on the 2023 return.
    NETS = _nets(y2023=1000.0, y2024=-1500.0, y2025=-500.0)

    def ledger(self, carried, locked_back):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        out = build_canada_ledger(dict(self.NETS), {2023: 1000.0}, carried,
                                  locked_back)
        return out, {r["year"]: r for r in out["rows"]}

    @rule("CA-CARRY-04")
    def test_the_rest_of_the_claim_waits_for_the_later_loss(self):
        out, by = self.ledger({2023: 0.0, 2024: 900.0}, {2024: 600.0})
        self.assertAlmostEqual(by[2024]["claimed_applied"], 600.0)
        self.assertAlmostEqual(by[2024]["carried_back"], 600.0)
        self.assertAlmostEqual(by[2024]["carryforward_balance"], 900.0)
        self.assertAlmostEqual(by[2025]["carried_back"], 400.0)
        # 900 + 500 - 400 (hand-computed; was 1,400).
        self.assertAlmostEqual(by[2025]["carryforward_balance"], 1000.0)
        self.assertAlmostEqual(out["final_carryforward"], 1000.0)
        self.assertNotIn("unmatched_claims", out)
        # The 2023 capacity stays used by the claim: no fresh T1A
        # suggestion for the 400 already claimed.
        self.assertEqual(by[2024]["carryback_candidates"], [])

    @rule("CA-CARRY-04")
    def test_a_lock_with_no_carry_back_leaves_the_claim_pending(self):
        # The 2024 lock carried the whole loss (closed before the claim,
        # or before close-year took carry-backs out): the claim is met
        # by the balance after it, not dropped.
        out, by = self.ledger({2023: 0.0, 2024: 1500.0}, {2024: 0.0})
        self.assertAlmostEqual(by[2024]["carried_back"], 0.0)
        self.assertAlmostEqual(by[2024]["carryforward_balance"], 1500.0)
        self.assertAlmostEqual(by[2025]["carried_back"], 1000.0)
        self.assertAlmostEqual(out["final_carryforward"], 1000.0)

    @rule("CA-CARRY-04")
    def test_the_lock_s_whole_carry_back_is_kept(self):
        out, by = self.ledger({2023: 0.0, 2024: 500.0}, {2024: 1000.0})
        self.assertAlmostEqual(by[2024]["carried_back"], 1000.0)
        self.assertAlmostEqual(by[2025]["carried_back"], 0.0)
        self.assertAlmostEqual(out["final_carryforward"], 1000.0)

    @rule("CA-CARRY-04")
    def test_lock_figure_reads_the_recorded_carry_back(self):
        from taxjson.bin.taxjson_carryover import lock_figure
        doc = {"totals": {"realized": -1500.0, "dispositions": 2},
               "carryforwards": {"net_capital_loss": {
                   "opening": 0.0, "created": 1500.0, "applied": 0.0,
                   "carried_back": 600.0, "closing": 900.0}}}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "2024.json"
            p.write_text(json.dumps(doc))
            info = lock_figure(p, "canada")
            self.assertAlmostEqual(info["carried"], 900.0)
            self.assertAlmostEqual(info["carried_back"], 600.0)
            del doc["carryforwards"]["net_capital_loss"]["carried_back"]
            p.write_text(json.dumps(doc))
            self.assertEqual(lock_figure(p, "canada")["carried_back"], 0.0)


# ---------------------------------------------------------------------------
# Item 4: close-year --filed-dispositions carries the FILED loss forward

class TestFiledDispositionsCarryForward(unittest.TestCase):

    def block(self, carried_back, filed_gain=-1200.0, books_gain=-1500.0):
        from taxjson.bin import taxjson_run as R
        est = {"losses_opening": 0.0, "losses_created": -books_gain,
               "losses_applied": 0.0, "losses_unused": -books_gain,
               "capital_gains_dividends": 0.0}
        doc = {"estimate": est, "totals": {"realized": books_gain},
               "filing": {"totals": {"gain": books_gain}}}
        ledger = {"rows": [{"year": 2023, "carried_back": 0.0},
                           {"year": 2024, "carried_back": carried_back}]}

        def fake(argv, capture_output=False, **_k):
            out = ledger if "carryover" in argv else doc
            return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'province = "ON"\n[accounts.margin]\ntype = "taxable"\n')
            cfg = R.load_config(root)
            with mock.patch("taxjson.lib.dispatch.run_cmd", fake):
                return R._carryforwards_for_lock(
                    root, cfg, filed_totals={"gain": filed_gain,
                                             "dispositions": 2})

    @rule("CA-CARRY-01")
    def test_the_lock_carries_the_filed_loss_less_the_carry_back(self):
        from taxjson.bin import taxjson_run as R
        block = self.block(1000.0)
        ncl = block["net_capital_loss"]
        self.assertAlmostEqual(ncl["created"], 1200.0)
        self.assertAlmostEqual(ncl["carried_back"], 1000.0)
        # 1,200 filed - 1,000 carried back (was 500, from the books).
        self.assertAlmostEqual(ncl["closing"], 200.0)
        self.assertAlmostEqual(ncl["filed_gain"], -1200.0)
        self.assertAlmostEqual(ncl["books_gain"], -1500.0)
        line = "\n".join(R._carryforwards_summary(block, 2024))
        self.assertIn("as filed: gain -1,200.00", line)
        self.assertIn("carried into 2025: 200.00", line)

    @rule("CA-CARRY-01")
    def test_a_filed_gain_applies_the_opening_balance(self):
        from taxjson.bin import taxjson_run as R
        ncl = {"opening": 300.0, "created": 0.0, "applied": 300.0,
               "closing": 0.0}
        doc = {"estimate": {}, "filing": {"totals": {"gain": 400.0}}}
        R._as_filed(ncl, doc, 100.0)
        self.assertEqual((ncl["created"], ncl["applied"], ncl["closing"]),
                         (0.0, 100.0, 200.0))

    @rule("CA-CARRY-01")
    def test_the_carry_back_is_at_most_the_filed_loss(self):
        ncl = self.block(1400.0)["net_capital_loss"]
        self.assertAlmostEqual(ncl["carried_back"], 1200.0)
        self.assertAlmostEqual(ncl["closing"], 0.0)

    @rule("CA-CARRY-01")
    def test_filed_equal_to_the_books_changes_nothing(self):
        ncl = self.block(1000.0, filed_gain=-1500.0)["net_capital_loss"]
        self.assertNotIn("filed_gain", ncl)
        self.assertAlmostEqual(ncl["closing"], 500.0)


if __name__ == "__main__":
    unittest.main()
