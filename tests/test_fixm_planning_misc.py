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


# --------------------------------------------------------- R1-324 sanity
class TestSanityMissingHoldingsFile(unittest.TestCase):
    def _root(self, tmp):
        from test_sanity import _gains, _holdings_toml
        root = Path(tmp)
        (root / "taxjson.toml").write_text(
            _config(2026, [("margin", "taxable"), ("rrsp", "sheltered")])
            .replace('[accounts.margin]\ntype = "taxable"\n',
                     '[accounts.margin]\ntype = "taxable"\n'
                     'holdings = ["ext/gone.toml"]\n')
            .replace('[accounts.rrsp]\ntype = "sheltered"\n',
                     '[accounts.rrsp]\ntype = "sheltered"\n'
                     'holdings = ["ext/r.toml"]\n'))
        # margin books a position the broker does not hold.
        _gains(root, "margin", {"FFN.TO": 500})
        _gains(root, "rrsp", {"XIU.TO": 40})
        (root / "ext").mkdir()
        _holdings_toml(root / "ext" / "r.toml", "R1", {"XIU.TO": 40})
        return root

    def test_text_says_incomplete_and_checklist_attention(self):
        from taxjson.lib import checklist as cl
        from datetime import date
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            r = _cli(root, "sanity")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("INCOMPLETE", r.stdout)
            self.assertNotIn("in every group.", r.stdout)
            j = _cli(root, "sanity", "--json")
            self.assertFalse(json.loads(j.stdout)["complete"])
            import tomllib
            cfg = tomllib.loads((root / "taxjson.toml").read_text())
            ctx = cl.Ctx(root=root, cfg=cfg, year=2026,
                         today=date(2026, 9, 29),
                         run_sub=cl.default_run_sub(root))
            res = cl.d_sanity(ctx)
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn("NOT checked", res.detail)


# ------------------------------------------- R1-336 / S047-18 missing-history
class TestMissingHistoryIncomplete(unittest.TestCase):
    CASH = ("BUYSELL 2025-02-03 10:00:00 AAA.TO 10 CAD 10.00 -100.00 0.00\n"
            "BUYSELL 2025-04-01 10:00:00 DDD.TO -10 CAD 10.00 100.00 0.00\n")
    MARGIN = "BUYSELL 2025-02-03 10:00:00 BBB.TO 10 CAD 10.00 -100.00 0.00\n"

    def _built(self, tmp):
        root = _project(tmp, [("margin", "taxable"), ("cash", "taxable")],
                        {"margin": self.MARGIN, "cash": self.CASH},
                        year=2025)
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return root

    def test_intact_books_report_the_short(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._built(tmp)
            r = _cli(root, "find-missing-history")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DDD.TO", r.stdout)

    def test_corrupt_book_is_not_an_all_clear(self):
        from taxjson.lib import checklist as cl
        from datetime import date
        with tempfile.TemporaryDirectory() as tmp:
            root = self._built(tmp)
            p = root / "work" / "cash_base.json"
            p.write_text(p.read_text()[: len(p.read_text()) // 2])
            r = _cli(root, "find-missing-history")
            import tomllib
            ctx = cl.Ctx(root=root,
                         cfg=tomllib.loads((root / "taxjson.toml").read_text()),
                         year=2025, today=date(2026, 9, 29),
                         run_sub=cl.default_run_sub(root))
            res = cl.d_missing_history(ctx)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("INCOMPLETE", r.stdout)
        self.assertNotIn("no negative holdings", r.stdout)
        self.assertNotEqual(res.status, "done", res.detail)

    def test_configured_account_without_book_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._built(tmp)
            (root / "work" / "cash_base.json").unlink()
            r = _cli(root, "find-missing-history")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("account cash", r.stdout)
        self.assertIn("cash_base.json", r.stderr)

    def test_direct_tool_bad_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._built(tmp)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_missing_history",
                 str(root / "work" / "margin_base.json"),
                 str(root / "work" / "nope_base.json")],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("INCOMPLETE", r.stdout)


# ------------------------------------------------------------ S029-16 diff
class TestDiffManualReporting(unittest.TestCase):
    ROW = {"account": "margin", "date": "2025-01-10", "symbol": "ZZZ.TO",
           "qty": 10.0, "proceeds": 500.0, "raw_gain": 500.0}
    KEEP = {"account": "margin", "date": "2025-03-10", "symbol": "AAA.TO",
            "qty": 10.0, "proceeds": 250.0, "gain": 50.0}

    def _diff(self, a, b):
        with tempfile.TemporaryDirectory() as tmp:
            pa, pb = Path(tmp) / "a.json", Path(tmp) / "b.json"
            pa.write_text(json.dumps(a))
            pb.write_text(json.dumps(b))
            return subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_diff",
                 "--no-color", str(pa), str(pb)],
                cwd=REPO_ROOT, capture_output=True, text=True)

    def test_added_and_modified_manual_rows(self):
        run1 = {"transactions": [self.KEEP],
                "manual_reporting_required": [self.ROW]}
        second = {**self.ROW, "date": "2025-02-10", "qty": 5.0,
                  "proceeds": 300.0}
        run2 = {"transactions": [self.KEEP],
                "manual_reporting_required": [self.ROW, second]}
        r = self._diff(run1, run2)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 added", r.stdout)
        self.assertIn("[manual_reporting_required]", r.stdout)
        run3 = {"transactions": [self.KEEP],
                "manual_reporting_required": [{**self.ROW,
                                               "proceeds": 600.0}]}
        r = self._diff(run1, run3)
        self.assertIn("1 modified", r.stdout)
        self.assertIn("proceeds", r.stdout)


if __name__ == "__main__":
    unittest.main()
