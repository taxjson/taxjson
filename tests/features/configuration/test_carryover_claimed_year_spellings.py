"""Batch C regression pins (slips and misc): findings 42, 83, 98, 99 and
112 of the 2026-10 bug review (finding 82 is pinned in the fetch
plugin's own tests, packages/taxjson-fetch/tests/test_fetch_fix_batch_c.py).

Synthetic data only: made-up tickers (SAMP, ZZQ), issuers and broker
account hashes.

Formerly: test_fix_batch_c_slips
"""
import unittest
from taxjson.lib.project_tables import claimed_losses
from taxjson.lib.config_check import table_problems


class TestClaimedYearTwice(unittest.TestCase):
    """42: `2023` and `02023` are two TOML keys but one tax year.

    Formerly: test_fix_batch_c_slips.TestClaimedYearTwice"""

    def test_two_spellings_of_one_year_are_a_problem(self):
        cfg = {"carryover": {"claimed": {"2023": 100, "02023": 200,
                                         "2024": 50}}}
        out, problems = claimed_losses(cfg)
        self.assertEqual(out, {2024: 50.0})      # neither 2023 kept
        self.assertEqual(len(problems), 1)
        self.assertIn("2023 is given twice", problems[0])
        self.assertIn("'02023'", problems[0])
        self.assertIn(problems[0], table_problems(cfg))

    def test_from_toml(self):
        import tomllib
        cfg = tomllib.loads("[carryover]\nclaimed = { 2023 = 100, "
                            "02023 = 200 }\n")
        self.assertEqual(len(claimed_losses(cfg)[1]), 1)

    def test_one_spelling_unchanged(self):
        self.assertEqual(claimed_losses(
            {"carryover": {"claimed": {"2023": 100, "2024": 50}}}),
            ({2023: 100.0, 2024: 50.0}, []))


if __name__ == "__main__":
    unittest.main()
