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
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
import _paths

REPO = _paths.REPO
ACCT = "99900001"

QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")
# 40 bought, 50 sold: 10 units go short with no purchase in the files.
QT_OVER = (QT_HEAD
           + "2025-01-10,2025-01-13,Buy,ZZOV.TO,ZZOV CORP,40,15.00,-600.00,"
             f"0,-600.00,CAD,Trades,{ACCT},Margin\n"
           + "2025-05-12,2025-05-13,Sell,ZZOV.TO,ZZOV CORP,-50,12.00,600.00,"
             f"-4.95,595.05,CAD,Trades,{ACCT},Margin\n")


def _tjs(root, *args, cwd=None):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    argv = [sys.executable, "-m", "taxjson.bin.taxjson_run"]
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _single(tmp, keep=("margin", "crypto")):
    """`init --single` (Canada, 2025), its accounts cut to `keep`."""
    root = Path(tmp) / "proj"
    r = _tjs(None, "init", "--single", "--country", "canada", "--year",
             "2025", str(root))
    assert r.returncode == 0, r.stderr
    cfg = root / "taxjson.toml"
    text = cfg.read_text()
    for name in ("margin", "tfsa", "rrsp", "crypto"):
        if name in keep:
            continue
        text = re.sub(r"\n\[accounts\.%s\]\n(?:[^\n\[]+\n)*" % name, "\n",
                      text)
        d = root / "inputs" / name
        if d.is_dir():
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
    cfg.write_text(text)
    return root


class TestSaleBeyondHeldIsNotInTheTotals(unittest.TestCase):
    """Item 5 (and items 10 and 12 on the same scaffold project: margin
    with stock trades only, inputs/crypto left empty).

    Formerly: test_fix_audit_1022_b.TestSaleBeyondHeldIsNotInTheTotals"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _single(cls._td.name)
        (cls.root / "inputs" / "margin" / "activity.csv").write_text(QT_OVER)
        cls.ran = _tjs(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_run_warns_not_in_the_totals(self):
        self.assertEqual(self.ran.returncode, 0, self.ran.stderr[-2000:])
        out = self.ran.stdout + self.ran.stderr
        self.assertRegex(out, r"NOT in (the totals|`tjs sum`)")
        self.assertNotIn("is in the totals", out)

    def test_sum_readiness_is_incomplete(self):
        r = _tjs(self.root, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertFalse(doc["readiness"]["complete"])
        self.assertEqual([i["symbol"] for i in doc["no_purchase_uncovered"]],
                         ["ZZOV.TO"])
        self.assertEqual(doc["no_purchase_in_totals"], [])

    def test_option_boundary_skips_the_empty_crypto_account(self):
        r = _tjs(self.root, "option-boundary", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("crypto_base.json", r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["missing_books"], [])
        # No written option in the books: no grant-timing warning (item 10)
        self.assertFalse(doc["since_warning"])

    def test_checklist_names_the_empty_crypto_folder(self):
        from taxjson.lib import checklist as cl
        import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        ctx = cl.Ctx(root=self.root, cfg=cfg, year=2025,
                     today=date(2026, 9, 1),
                     run_sub=lambda argv, timeout=900: (0, "", ""))
        res = cl.d_crypto_sends(ctx)
        self.assertEqual(res.status, "blocked")
        self.assertIn("inputs/crypto is empty", res.detail)
        self.assertIn("[accounts.crypto]", res.detail)
        self.assertNotIn("tjs run", res.detail)
        # Its `! ` line names no command (the detail is the fix).
        text = cl.render_summary([res], 2025, "canada", width_=120)
        acts = [ln for ln in text.splitlines()
                if ln.startswith("! ") and "crypto-sends" in ln]
        self.assertEqual(len(acts), 1, text)
        self.assertTrue(acts[0].endswith("[accounts.crypto]"), acts[0])
        self.assertIn("Next (step 16, crypto-sends): inputs/crypto is empty",
                      " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
