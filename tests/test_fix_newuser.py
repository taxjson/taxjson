"""Fixes from a new user's walkthrough of v0.27.1: the printed setup
recipes, the year default's January-to-April hint, a .tt symbol without
its market suffix, the same account number at two brokers, the demo
exports and `tjs init --demo`, maintainer commands in the help page,
close-year with open checklist items, the FX download window, product
text that contradicted v0.27.x, and option_grant_timing_since in a new
taxjson.toml. Synthetic data only (account ids 999000xx)."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from _style import CapturedWidth

REPO = Path(__file__).resolve().parents[1]
SRC = str(REPO / "src")
EXAMPLES = REPO / "examples"

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def tearDownModule():
    _WIDTH.stop()


def _env(**extra):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["TAXJSON_OFFLINE"] = "1"
    env.update(extra)
    return env


def _cli(*args, cwd=None, env=None, stdin=subprocess.DEVNULL):
    return subprocess.run(
        [sys.executable, "-P", "-m", "taxjson.bin.taxjson_run", *args],
        cwd=cwd, stdin=stdin, capture_output=True, text=True,
        env=env or _env())


def _tmp(test) -> Path:
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


_TOML = """[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
tax_date = "settle"
option_grant_timing_since = 2025
"""

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
       "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
       "Activity Type,Account Type")


def _qbuy(d, s, acct, sym="XEI.TO"):
    return (f"{d} 12:00:00 AM,{s} 12:00:00 AM,Buy,{sym},SAMPLE TEST FUND,"
            f"100,25,-2500,-4.95,-2504.95,CAD,{acct},Trades,"
            f"Individual margin")


def _webull(acct, rows):
    return ("Webull Securities (Canada) Ltd.\nSynthetic Statement\n"
            f"Account Number: {acct}\n"
            "Date Range: January 1 2025 - December 31 2025\n\n"
            '"Currency","Date","Action Code","Symbol","Security Description",'
            '"Type Code","Quantity","Price","Proceeds"\n' + rows)


# ------------------------------------------------ 4. one number, two brokers
class TestSameNumberAtTwoBrokers(unittest.TestCase):
    """A Questrade and a Webull export that print the same account
    number are two broker accounts: no "feeds two taxjson accounts"."""

    def _project(self, webull_rows=None, second_questrade=False):
        root = _tmp(self)
        acct = "99900021"                                  # pii-ok
        (root / "inputs" / "qt").mkdir(parents=True)
        (root / "inputs" / "qt" / "questrade.csv").write_text(
            _QH + "\n" + _qbuy("2025-03-03", "2025-03-04", acct) + "\n")
        (root / "inputs" / "wb").mkdir(parents=True)
        if second_questrade:
            (root / "inputs" / "wb" / "questrade.csv").write_text(
                _QH + "\n" + _qbuy("2025-03-03", "2025-03-04", acct) + "\n")
        else:
            (root / "inputs" / "wb" / "webull.csv").write_text(_webull(
                acct, webull_rows or
                'CAD,05-03-2025,BUY,@XEI,SAMPLE TEST FUND,EQ,10,25.00,'
                '"(250.00)"\n'))
        (root / "taxjson.toml").write_text(
            _TOML + '\n[accounts.qt]\ntype = "taxable"\n'
            '\n[accounts.wb]\ntype = "taxable"\n')
        return root

    def test_two_brokers_same_number_is_quiet(self):
        r = _cli("-C", str(self._project()), "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("feeds two taxjson accounts", r.stdout + r.stderr)
        self.assertNotIn("99900021", r.stdout + r.stderr)   # pii-ok

    def test_one_broker_same_number_still_loud(self):
        r = _cli("-C", str(self._project(second_questrade=True)), "run",
                 "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("feeds two taxjson accounts, qt and wb", r.stdout)


if __name__ == "__main__":
    unittest.main()
