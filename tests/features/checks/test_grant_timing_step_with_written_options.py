"""Workflow audit 2026-10-22, batch 2: what the views claim
(~/workflow-audit-2026-10-22.md is the maintainer's report).

- Item 5: a sale of more shares than held, the engine closing part of it
  against a long, is NOT in the totals for the units it left short.
- Item 10: the checklist flags option_grant_timing_since only where
  option-boundary (and the run) warn: the books hold a written option.
- Item 12: an empty crypto (or other) account left from the scaffold:
  option-boundary and the checklist name the empty folder, never "run
  `tjs run`" (the run skips it, so the advice looped).
- Item 15: outside a project every command says there is none and how
  to start one or where to `cd`.

Every fixture is SYNTHETIC: account 99900001, made-up tickers ZZ*.

Formerly: test_fix_audit_1022_b
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path


class TestGrantTimingOnlyWithWrittenOptions(unittest.TestCase):
    """Item 10: the checklist follows option-boundary's own warning.

    Formerly: test_fix_audit_1022_b.TestGrantTimingOnlyWithWrittenOptions"""

    def _detect(self, doc):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ctx = cl.Ctx(root=root, cfg={"settings": {"country": "canada"},
                                         "accounts": {"margin": {
                                             "type": "taxable"}}},
                         year=2025, today=date(2026, 9, 1),
                         run_sub=lambda argv, timeout=900:
                         (0, json.dumps(doc), ""))
            return cl.d_option_boundary(ctx)

    def test_no_written_option_is_done(self):
        r = self._detect({"rows": [], "timing": "grant",
                          "since_explicit": False, "since_warning": False})
        self.assertEqual(r.status, "done", r.detail)

    def test_written_option_flags_the_setting(self):
        r = self._detect({"rows": [], "timing": "grant",
                          "since_explicit": False, "since_warning": True})
        self.assertEqual(r.status, "attention")
        self.assertIn("option_grant_timing_since", r.detail)


if __name__ == "__main__":
    unittest.main()
