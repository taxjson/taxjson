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


# ------------------------------------------------------ 8. the FX window
class TestFxWindow(unittest.TestCase):
    """The rates stage asks only for the dates the project's files
    reach (lib/rates_window), never Yahoo for a 2024 project."""

    def test_date_shapes(self):
        from taxjson.lib.rates_window import earliest_in_text
        cases = {
            b"2024-03-05,x": date(2024, 3, 5),
            b'"05-03-2023",x': date(2023, 3, 5),       # either order:
            b"03/15/2022 x": date(2022, 3, 15),         # the valid one
            b"Date Range: January 1, 2021 - x": date(2021, 1, 1),
            b"x 1 Feb 2020 x": date(2020, 2, 1),
            b"20190104;093000": date(2019, 1, 4),       # an IB Flex date
            b"2024-01-02 and order 20010101": date(2024, 1, 2),
            b"price 2003.45 qty 1999": None,
        }
        for text, want in cases.items():
            self.assertEqual(earliest_in_text(text, 2027), want, text)

    def test_window_start(self):
        from taxjson.lib.rates_window import window_start
        d = _tmp(self)
        (d / "a.csv").write_text("2024-02-12,BUY\n2024-12-05,SELL\n")
        (d / "b.tt").write_text("OPENING 2022-06-03 QZA.TO 10 "
                                "cost=unknown\n")
        self.assertEqual(window_start(2024, [d / "a.csv"]),
                         date(2023, 12, 18))
        self.assertEqual(window_start(2024, [d / "a.csv", d / "b.tt"]),
                         date(2022, 5, 20))
        self.assertEqual(window_start(2024, []), date(2023, 12, 18))

    def test_a_2024_project_asks_the_bank_from_december_2023_only(self):
        """A stubbed fetcher counts the requests: the Bank of Canada is
        asked from the window's start, Yahoo never."""
        from taxjson.bin import to_base_curr as T
        from taxjson.lib.rates_window import window_start
        d = _tmp(self)
        shutil.copy(EXAMPLES / "questrade_demo.csv", d / "q.csv")
        start = window_start(2024, [d / "q.csv"]).isoformat()
        boc, yahoo, noon = [], [], []

        def fake_boc(cur, a, b):
            boc.append((a, b))
            out, x = {}, a
            while x <= b:
                if date.fromisoformat(x).weekday() < 5:
                    out[x] = "1.3500"
                x = T._shift(x, 1)
            return out

        with mock.patch.object(T, "CACHE_FILE", str(d / "fx.json")):
            rows, errors, _n = T.build_rates(
                "USD", "CAD", start, "2026-10-09", today="2026-10-09",
                fetch_boc_fn=fake_boc,
                fetch_yahoo_fn=lambda *a: yahoo.append(a) or {},
                fetch_noon_fn=lambda *a: noon.append(a) or {})
        self.assertEqual(errors, [])
        self.assertEqual(start, "2023-12-18")
        self.assertTrue(boc)
        self.assertGreaterEqual(min(a for a, _b in boc), start)
        self.assertEqual(yahoo, [])
        self.assertEqual(rows[0][0], start)

    def test_the_stage_passes_the_window_and_records_it(self):
        from taxjson.bin import taxjson_run as R
        d = _tmp(self)
        cache = d / "work"
        (d / "inputs" / "qt").mkdir(parents=True)
        shutil.copy(EXAMPLES / "questrade_demo.csv",
                    d / "inputs" / "qt" / "q.csv")
        seen = []

        def capture(argv, *a, **k):
            seen.append(argv)
            return b"2023-12-18 12:00:00 USD CAD 1.35 boc\n"
        settings = {"base_currency": "CAD", "source_currencies": ["USD"],
                    "year": 2024}
        files = R._rates_inputs(d, d / "inputs", {"qt": {}})
        with mock.patch.object(R, "run_capture", capture):
            R.stage_currency_rates(settings, cache, files)
        self.assertEqual(seen[0][-2:], ["--start", "2023-12-18"])
        self.assertEqual((cache / R.RATES_START_STAMP).read_text().strip(),
                         "2023-12-18")
        # An export reaching further back rebuilds from its date.
        (d / "inputs" / "qt" / "old.tt").write_text(
            "OPENING 2021-03-01 QZA.TO 10 cost=unknown\n")
        files = R._rates_inputs(d, d / "inputs", {"qt": {}})
        with mock.patch.object(R, "run_capture", capture), \
                mock.patch.object(R, "_rates_coverage_stale",
                                  lambda *a, **k: False), \
                mock.patch.object(R, "needs_rebuild", lambda *a: False):
            R.stage_currency_rates(settings, cache, files)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1][-2:], ["--start", "2021-02-15"])


if __name__ == "__main__":
    unittest.main()
