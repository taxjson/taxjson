"""Regression tests for the low-round filing-b fixes (fixl/filing-b):
reconcile-slips, t1135, audit and form-export.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_filing_b
"""
import json
import unittest
from taxjson.project.settings import Settings


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# t1135
# ---------------------------------------------------------------------------

from unittest import mock  # noqa: E402


class TestT1135Checklist(unittest.TestCase):
    """S051-22 / S052-15: the checklist marked t1135 done in an open
    year below the threshold.

    Formerly: test_fix_l_filing_b.TestT1135Checklist"""

    def test_open_year_below_threshold_is_not_done(self):
        from datetime import date
        from taxjson.lib import checklist as CL
        rep = {"filing_required": False, "year_complete": False,
               "as_of": "2026-09-22", "max_total_cost": 61500.0}
        ctx = mock.Mock()
        ctx.settings = Settings.from_table({"country": "canada"})
        ctx.year = 2026
        ctx.today = date(2026, 9, 29)
        ctx.sub.return_value = (0, json.dumps(rep), "")
        res = CL.d_t1135(ctx)
        self.assertNotEqual(res.status, "done")
        self.assertIn("2026-09-22", res.detail)
        ctx.today = date(2027, 2, 1)
        ctx.sub.return_value = (0, json.dumps(
            dict(rep, year_complete=True, as_of="2026-12-31")), "")
        self.assertEqual(CL.d_t1135(ctx).status, "done")
