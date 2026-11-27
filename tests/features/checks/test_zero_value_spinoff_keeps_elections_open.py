"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic.

Formerly: test_fix_misc"""
import unittest


class TestZeroValueSpinoffInChecklist(unittest.TestCase):
    """R1-11: a taxable spin-off booked at $0 keeps the elections step open
    (not run-clean: that step gates close-year and the report banners).

    Formerly: test_fix_misc.TestZeroValueSpinoffInChecklist"""

    def test_elections_step_needs_attention(self):
        import tempfile
        from pathlib import Path
        from taxjson.lib import checklist as cl
        from _support.filing_checklist_ctx import _cl_ctx, _cl_project
        table = {"elections": (0, "No pending elections.\n", "")}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            ctx = _cl_ctx(root, cfg, table)
            self.assertEqual(cl.d_elections(ctx).status, "done")
            (root / "work" / "margin_corp_spinoff_value.diag").write_text(
                "warning: margin: spin-off ZZQ.US on 2025-04-04 (event e1) is booked at $0\n")
            r = cl.d_elections(_cl_ctx(root, cfg, table))
            clean = cl.d_run_clean(_cl_ctx(root, cfg, table))
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("booked at $0", r.detail)
        self.assertIn("fmv_per_share", r.detail)
        self.assertNotIn("booked at $0", clean.detail)


if __name__ == "__main__":
    unittest.main()
