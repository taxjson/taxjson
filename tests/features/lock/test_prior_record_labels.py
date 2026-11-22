"""The 2026-10-29 fix round, group "edgecases": `tjs edge-cases`
and `tjs option-boundary` on synthetic books.

- TJ-0396: a contract on explicit close timing (option_premium_timing =
  "close") whose write year's lock records grant timing, or reports the
  premium, is a double count, flagged ATTENTION like a transition
  contract's (CA-OPT-07).
- TJ-0225: edge-cases names a lock reached through [settings]
  prior_year_record by its path, as option-boundary does, not as
  filed/<year>.json.

Made-up symbols (ZZQ, Q) and amounts only.

Formerly: test_fix_1029_edgecases
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.option_boundary import filed_locks


class TestFiledLocksLabels(unittest.TestCase):
    """Formerly: test_fix_1029_edgecases.TestFiledLocksLabels"""
    def test_labels_name_the_prior_record_by_its_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p26"
            (root / "filed").mkdir(parents=True)
            prev = Path(td) / "p25" / "filed"
            prev.mkdir(parents=True)
            (prev / "2025.json").write_text(json.dumps({"year": 2025}))
            (root / "filed" / "2024.json").write_text(
                json.dumps({"year": 2024}))
            labels = {}
            years, _timing = filed_locks(
                root, {"country": "canada", "year": 2026,
                       "prior_year_record": "../p25/filed/2025.json"},
                labels=labels)
        self.assertEqual(years, {2024, 2025})
        self.assertEqual(labels, {2024: "filed/2024.json",
                                  2025: "../p25/filed/2025.json"})


if __name__ == "__main__":
    unittest.main()
