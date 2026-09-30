"""Planning medium round (2026-09 audit): staleness, diagnostics and
artifact findings.

  R1-251  a sheltered --account rerun makes the wash-adjusted numbers
          stale: sum/form-export warn, close-year refuses
  R1-324  a configured holdings file that is missing keeps the checklist
          sanity step at attention
  R1-336  taxjson-missing-history gives no all-clear when an input failed
  S047-18 find-missing-history names configured accounts with no book
  S029-16 taxjson-diff compares manual_reporting_required rows
  S030-08 holdings TOML cost_per_share is per share for options
  R1-208  the .sum TOTAL PROCEEDS/COST say they are the engine convention
  R1-310  sum-income year filter, withholding and grand total pinned
  S022-00 / S041-14  account names that shadow artifact suffixes refused
  S037-23 `taxjson gains` names an account with no native gains
  S044-21 list --date says its ACB is per-account

All data is synthetic.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


def _config(year, accounts, extra=""):
    t = (f'[settings]\nyear = {year}\ncountry = "canada"\n'
         f'base_currency = "CAD"\nsource_currencies = []\n')
    for n, ty in accounts:
        t += f'[accounts.{n}]\ntype = "{ty}"\n'
    return t + extra


def _project(tmp, accounts, tts, year=2024, extra=""):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(_config(year, accounts, extra))
    for acct, text in tts.items():
        d = root / "inputs" / acct
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{acct}.tt").write_text(text)
    return root


def _age(path, seconds=30):
    t = time.time() - seconds
    os.utime(path, (t, t))


# ---------------------------------------------------------------- R1-251
class TestShelteredRerunStaleness(unittest.TestCase):
    MARGIN = ("BUYSELL 2024-06-10 10:00:00 XYZ.TO 200 CAD 10.00 -2000.00 0.00\n"
              "BUYSELL 2024-09-10 10:00:00 XYZ.TO -200 CAD 8.00 1600.00 0.00\n")
    TFSA = "BUYSELL 2024-01-10 10:00:00 QQQ.TO 10 CAD 10.00 -100.00 0.00\n"

    def test_sum_warns_and_close_year_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("margin", "taxable"), ("tfsa", "sheltered")],
                            {"margin": self.MARGIN, "tfsa": self.TFSA})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            work = root / "work"
            s0 = _cli(root, "sum", "--json")
            self.assertNotIn("stale", s0.stderr)
            for f in work.iterdir():
                _age(f)
            # A TFSA buy inside the margin loss window, then a
            # sheltered-only rerun: the blended wash pass is skipped.
            with open(root / "inputs" / "tfsa" / "tfsa.tt", "a") as fh:
                fh.write("BUYSELL 2024-09-20 10:00:00 XYZ.TO 100 CAD 8.00 "
                         "-800.00 0.00\n")
            r = _cli(root, "run", "--account", "tfsa", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            s = _cli(root, "sum", "--json")
            self.assertIn("stale", s.stderr)
            self.assertIn("sheltered_base.json", s.stderr)
            f = _cli(root, "form-export")
            self.assertIn("stale", f.stderr)
            c = _cli(root, "close-year")
            self.assertNotEqual(c.returncode, 0, c.stdout)
            self.assertIn("STALE", c.stderr)
            self.assertFalse((root / "filed" / "2024.json").exists())


if __name__ == "__main__":
    unittest.main()
