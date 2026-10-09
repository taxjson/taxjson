"""Stale blended gains after another account's partial run (GitHub #15).

A blended result (`<acct>_gains_wash.json`, written by the cross-account
pass of a full `taxjson run`) depends on EVERY taxable account in the
blend: Canada pools the cost of identical property across accounts
(s.47) and a buy in one account can make a loss in another superficial;
the US matches wash sales across accounts. `run --account B` rebuilds
B's books but skips the blended pass, and the stale check looked only at
A's own base/gains and sheltered_base.json, so A's report served the old
blend silently. The blended pass now records its members and a
fingerprint of each input (work/.wash_inputs.json); a changed input of
any member makes every wash file of that blend stale (the same warning,
checklist attention and close-year stop as before). All data synthetic.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _put(p: Path, text: str, t: float) -> Path:
    p.write_text(text)
    os.utime(p, (t, t))
    return p


class TestStaleWashInputsUnit(unittest.TestCase):

    def test_issue_repro_without_a_record(self):
        # The issue's synthetic steps: a wash file with no record of its
        # blend (written by an older taxjson) and B rebuilt after it.
        from taxjson.lib.report_model import (resolve_gains_files,
                                              stale_wash_inputs)
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            for name in ("a_gains_wash.json", "a_base.json", "a_gains.json",
                         "b_base.json", "b_gains.json"):
                _put(work / name, "{}", 100)
            for name in ("b_base.json", "b_gains.json"):
                os.utime(work / name, (200, 200))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                result = resolve_gains_files(work, account="a")
            self.assertEqual(result["a"].name, "a_gains_wash.json")
            self.assertIn("b_base.json",
                          stale_wash_inputs(work / "a_gains_wash.json"))
            self.assertIn("a_gains_wash.json", err.getvalue())
            self.assertIn("b_base.json", err.getvalue())

    def test_without_a_record_intermediates_are_not_accounts(self):
        from taxjson.lib.report_model import stale_wash_inputs
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            for name in ("a_gains_wash.json", "a_gains.json",
                         "b_gains.json", "b_base.json"):
                _put(work / name, "{}", 100)
            # The account's own raw intermediates and the blended pass's
            # dot-prefixed files are not other accounts.
            for name in ("a_raw_base.json", "a_raw_gains.json",
                         "a_raw_base_gains.json", ".blend_base.json"):
                _put(work / name, "{}", 200)
            self.assertEqual(stale_wash_inputs(work / "a_gains_wash.json"),
                             [])

    def _blend(self, work: Path):
        from taxjson.lib.report_model import record_wash_inputs
        files = {n: _put(work / n, json.dumps({"n": n}), 100)
                 for n in ("a_base.json", "a_gains.json", "b_base.json",
                           "b_gains.json")}
        washes = [_put(work / f"{x}_gains_wash.json", "{}", 150)
                  for x in ("a", "b")]
        record_wash_inputs(work, washes, list(files.values()),
                           blend="blend")
        return files, washes

    def test_member_changed_after_the_blend(self):
        from taxjson.lib.report_model import stale_wash_inputs
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            files, (wa, wb) = self._blend(work)
            self.assertEqual(stale_wash_inputs(wa), [])
            self.assertEqual(stale_wash_inputs(wb), [])
            # B rebuilt with different books (an --account B run).
            _put(files["b_base.json"], json.dumps({"n": "changed"}), 120)
            self.assertEqual(stale_wash_inputs(wa), ["b_base.json"])
            self.assertEqual(stale_wash_inputs(wb), ["b_base.json"])

    def test_member_removed_or_a_sheltered_book_appears(self):
        from taxjson.lib.report_model import (record_wash_inputs,
                                              stale_wash_inputs)
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            files = {n: _put(work / n, "{}", 100)
                     for n in ("a_base.json", "b_base.json")}
            wa = _put(work / "a_gains_wash.json", "{}", 150)
            record_wash_inputs(work, [wa], list(files.values())
                               + [work / "sheltered_base.json"],
                               blend="blend")
            files["b_base.json"].unlink()
            _put(work / "sheltered_base.json", "{}", 120)
            self.assertEqual(stale_wash_inputs(wa),
                             ["b_base.json", "sheltered_base.json"])

    def test_identical_rebuild_is_not_stale(self):
        # Same content rewritten later (a --fast no-op, a re-run that
        # changed nothing): the fingerprint's hash still matches.
        from taxjson.lib.report_model import stale_wash_inputs
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            files, (wa, _wb) = self._blend(work)
            text = files["b_base.json"].read_text()
            _put(files["b_base.json"], text, 120)
            self.assertEqual(stale_wash_inputs(wa), [])

    def test_a_wash_file_rewritten_outside_the_blend_ignores_it(self):
        # The record describes the wash file it fingerprinted; a wash
        # file written since (by a pass that kept no record) falls back
        # to the record-less check.
        from taxjson.lib.report_model import stale_wash_inputs
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            files, (wa, _wb) = self._blend(work)
            _put(files["b_base.json"], json.dumps({"n": "x"}), 120)
            _put(wa, '{"other": 1}', 300)
            self.assertEqual(stale_wash_inputs(wa), [])

    def test_record_is_dot_prefixed_and_owner_only(self):
        from taxjson.lib.report_model import WASH_INPUTS_FILE
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            self._blend(work)
            p = work / WASH_INPUTS_FILE
            self.assertTrue(p.name.startswith("."))
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            doc = json.loads(p.read_text())
            self.assertEqual(sorted(doc["wash"]),
                             ["a_gains_wash.json", "b_gains_wash.json"])


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


class TestPartialRunOfAnotherMember(unittest.TestCase):
    """Full run -> change B -> `run --account B` -> A's report warns."""
    MARGIN = ("BUYSELL 2024-06-10 10:00:00 XYZ.TO 200 CAD 10.00 -2000.00 0.00\n"
              "BUYSELL 2024-09-10 10:00:00 XYZ.TO -200 CAD 8.00 1600.00 0.00\n")
    CASH = "BUYSELL 2024-01-10 10:00:00 QQQ.TO 10 CAD 10.00 -100.00 0.00\n"

    def test_a_reports_stale_after_a_partial_run_of_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.cash]\ntype = "taxable"\n')
            for acct, text in (("margin", self.MARGIN), ("cash", self.CASH)):
                d = root / "inputs" / acct
                d.mkdir(parents=True)
                (d / f"{acct}.tt").write_text(text)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            work = root / "work"
            self.assertTrue((work / ".wash_inputs.json").is_file())
            s0 = _cli(root, "sum", "--json")
            self.assertEqual(s0.returncode, 0, s0.stderr[-2000:])
            self.assertNotIn("stale", s0.stderr)
            t = time.time() - 30
            for f in work.iterdir():
                os.utime(f, (t, t))
            # A buy in cash inside margin's loss window (a superficial
            # loss across accounts), then a cash-only rerun: the blended
            # pass is skipped and margin's own books are untouched.
            with open(root / "inputs" / "cash" / "cash.tt", "a") as fh:
                fh.write("BUYSELL 2024-09-20 10:00:00 XYZ.TO 100 CAD 8.00 "
                         "-800.00 0.00\n")
            r = _cli(root, "run", "--account", "cash", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            s = _cli(root, "sum", "--json")
            self.assertIn("margin_gains_wash.json", s.stderr)
            self.assertIn("cash_base.json", s.stderr)
            self.assertIn("stale", s.stderr)
            # A full run brings the blend up to date: no warning.
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            s2 = _cli(root, "sum", "--json")
            self.assertNotIn("stale", s2.stderr)


if __name__ == "__main__":
    unittest.main()
