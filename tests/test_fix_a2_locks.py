"""Re-audit-2 filing-lock fixes (close-year, handoff, option-boundary):
the prior-year lock reached through [settings] prior_year_record, the
close-year gates, and the hand-off record/check."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=600)


def _project(root, year, tt="", extra="", country="canada", cur="CAD"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{extra}'
        f'[accounts.margin]\ntype = "taxable"\n')
    if tt:
        (root / "inputs" / "margin" / "a.tt").write_text(tt)
    return root


class TestProjectLocks(unittest.TestCase):
    """taxjson_filed.project_locks: the local locks plus the one
    prior_year_record names (A2-0036, A2-0335, A2-0338, A2-0664)."""

    def test_prior_record_is_a_lock_of_its_year(self):
        from taxjson.bin import taxjson_filed as tf
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "p25" / "filed").mkdir(parents=True)
            (base / "p25" / "filed" / "2025.json").write_text(
                json.dumps({"year": 2025, "totals": {}}))
            (base / "p26").mkdir()
            st = {"year": 2026,
                  "prior_year_record": "../p25/filed/2025.json"}
            locks = tf.project_locks(base / "p26", st)
            self.assertEqual([(y, w) for y, _p, w in locks],
                             [(2025, "prior_year_record")])
            # A local lock of the same year wins; no duplicate.
            (base / "p26" / "filed").mkdir()
            (base / "p26" / "filed" / "2025.json").write_text(
                json.dumps({"year": 2025}))
            self.assertEqual([(y, w) for y, _p, w in
                              tf.project_locks(base / "p26", st)],
                             [(2025, "local")])
            self.assertIsNone(tf.lock_for_year(base / "p26", st, 2024))
            with self.assertRaises(tf.PriorRecordError):
                tf.project_locks(base / "p26",
                                 dict(st, prior_year_record=5))


class TestHandoffPriorRecordType(unittest.TestCase):
    def test_non_string_prior_year_record_is_refused_like_run(self):
        # A2-1135: handoff read 5 as the path <root>/5.
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2026,
                         extra="prior_year_record = 5\n")
            r = _run_cli(p, "handoff")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("prior_year_record must be a path string",
                          r.stderr)
            self.assertNotIn("/5", r.stderr)


if __name__ == "__main__":
    unittest.main()
