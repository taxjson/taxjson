"""Backlog fix round 2 of 2026-10-29, group `review`: the review and
filing-output commands (audit --year, form-export, the wash review, the
stale-book warning, check-tt, the checklist's run-clean, sanity's
pointer, the inputs/ permission warning).

TJ-0550  audit --year refuses a lock closed under the other country
         before applying its date basis or option timing.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*, SYN*), account names (margin, tfsa), amounts
under 1,000, broker account numbers like 99900001.

Formerly: test_fix_1029_r2_review
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO


def _tjs(root, *args, cwd=None, env_extra=None):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(env_extra or {})
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(root, *, country="canada", year=2025, accounts=None,
             files=None, settings=""):
    """A project at `root`: taxjson.toml, an empty ticker.map and one
    inputs/<account>/ per account; `files` maps "account/name" to its
    text."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    accounts = accounts or {"margin": "taxable"}
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{settings}\n'
        + "".join(f'[accounts.{a}]\ntype = "{t}"\n'
                  for a, t in accounts.items()))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        acct, _, fn = name.partition("/")
        (root / "inputs" / acct / fn).write_text(text)
    return root


class TestCheckTtRunsTheAllowlossProjectChecks(unittest.TestCase):
    """TJ-0566: two ALLOWLOSS lines naming one sale, or an ALLOWLOSS in
    a TFSA, each pass `check-tt` (errors 0, exit 0) while `tjs run`
    refuses them before computing anything. check-tt now applies the
    run's project check (loss_overrides.read_project) and names each
    line.

    Formerly: test_fix_1029_r2_review.TestCheckTtRunsTheAllowlossProjectChecks"""

    BUY = "BUYSELL 2025-01-02 10:00:00 SYN.TO 1 CAD 1 -1 0\n"

    def _check(self, accounts, rows, *args):
        with tempfile.TemporaryDirectory() as d:
            acct = next(iter(accounts))
            p = _project(Path(d) / "p", accounts=accounts,
                         files={f"{acct}/trades.tt": rows})
            run = _tjs(p, "run", "--no-input")
            r = _tjs(p, "check-tt", *args)
            return run, r

    def test_the_same_sale_named_twice_is_an_error(self):
        run, r = self._check(
            {"margin": "taxable"},
            'ALLOWLOSS 2025-03-03 SYN.TO reason="first reason"\n'
            'ALLOWLOSS 2025-03-03 SYN.TO reason="second reason"\n'
            + self.BUY, "--json")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("the same sale is named twice", run.stdout + run.stderr)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        errs = [p for p in doc["problems"]
                if p["severity"] == "error"]
        self.assertEqual(len(errs), 1, errs)
        self.assertEqual(errs[0]["line"], 2)
        self.assertIn("the same sale is named twice", errs[0]["message"])

    def test_an_allowloss_in_a_sheltered_account_is_an_error(self):
        run, r = self._check(
            {"tfsa": "sheltered"},
            'ALLOWLOSS 2025-03-03 SYN.TO reason="a reason"\n' + self.BUY)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("inputs/tfsa/trades.tt:1", r.stdout + r.stderr)
        self.assertIn("account tfsa is sheltered", r.stdout + r.stderr)

    def test_one_line_in_a_taxable_account_passes(self):
        run, r = self._check(
            {"margin": "taxable"},
            'ALLOWLOSS 2025-03-03 SYN.TO reason="a reason"\n' + self.BUY)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
