"""Re-audit-2 planning lists: the views / pipeline part.

  A2-0128  run --account <sheltered> with a sibling's book deleted refuses
           (sheltered_base.json kept) instead of rebuilding without it
  A2-0366  the same export file in two accounts is ATTENTION; --strict stops
  A2-0380 / A2-0381  every command that reads work/ books prints the
           stale-books run-state banner (stderr; --json stays valid)
  A2-0684 / A2-1167  an account-taking command refuses an account that is
           not in [accounts]; harvest names a symbol filter that matches
           nothing
  A2-1182  all-accounts views name a configured account with inputs but
           no books
  A2-0685  reports/<acct>_holdings.toml applies missing_history.json openings
  A2-0693  grant timing: an expired written call counts as a close in
           ccd-sum, winners and the .sum TRADES line
  A2-1190  wrappers relay the child's error line, not a fixed prefix

All data is synthetic.

Formerly: test_fix_a2_planning_views
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = _paths.REPO


def _cli(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _config(year, accounts, extra=""):
    t = (f'[settings]\nyear = {year}\ncountry = "canada"\n'
         f'base_currency = "CAD"\nsource_currencies = []\n{extra}')
    for n, ty in accounts:
        t += f'[accounts.{n}]\ntype = "{ty}"\n'
    return t


def _project(tmp, accounts, tts, year=2024, extra=""):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(_config(year, accounts, extra))
    for acct, text in tts.items():
        d = root / "inputs" / acct
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{acct}.tt").write_text(text)
    return root


def _age(root, seconds=30):
    t = time.time() - seconds
    for f in (root / "work").rglob("*"):
        if f.is_file():
            os.utime(f, (t, t))


# ---------------------------------------------------------------- A2-0128
class TestAccountRunMissingShelteredBook(unittest.TestCase):
    """Formerly: test_fix_a2_planning_views.TestAccountRunMissingShelteredBook"""
    MARGIN = ("BUYSELL 2024-06-10 10:00:00 XYZ.TO 200 CAD 10.00 -2000.00 0.00\n"
              "BUYSELL 2024-09-10 10:00:00 XYZ.TO -100 CAD 8.00 800.00 0.00\n")
    TFSA = "BUYSELL 2024-01-10 10:00:00 DEF.TO 10 CAD 10.00 -100.00 0.00\n"
    LIRA = "BUYSELL 2024-09-12 10:00:00 XYZ.TO 50 CAD 8.00 -400.00 0.00\n"

    def test_deleted_sibling_book_refuses_and_keeps_combined_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("margin", "taxable"), ("tfsa", "sheltered"),
                                  ("lira", "sheltered"), ("rrsp", "sheltered")],
                            {"margin": self.MARGIN, "tfsa": self.TFSA,
                             "lira": self.LIRA})
            (root / "inputs" / "rrsp").mkdir()      # configured, no inputs
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            shb = root / "work" / "sheltered_base.json"
            before = shb.read_text()
            self.assertIn("XYZ.TO", before)
            (root / "work" / "lira_base.json").unlink()
            r = _cli(root, "run", "--account", "tfsa", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout[-2000:])
            out = r.stdout + r.stderr
            self.assertIn("lira", out)
            self.assertNotIn("never built", out)
            # The combined book is NOT rebuilt without lira's buy.
            self.assertEqual(shb.read_text(), before)

    def test_account_without_inputs_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("margin", "taxable"), ("tfsa", "sheltered"),
                                  ("rrsp", "sheltered")],
                            {"margin": self.MARGIN, "tfsa": self.TFSA})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, "run", "--account", "tfsa", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])


# ------------------------------------------------------- A2-0380 / A2-0381
class TestStaleBooksBannerEverywhere(unittest.TestCase):
    """Formerly: test_fix_a2_planning_views.TestStaleBooksBannerEverywhere"""
    MARGIN = ("BUYSELL 2024-06-10 10:00:00 XYZ.TO 200 CAD 10.00 -2000.00 0.00\n"
              "BUYSELL 2024-09-10 10:00:00 XYZ.TO -100 CAD 8.00 800.00 0.00\n")
    CMDS = [("gains",), ("roc-sum",), ("divs-sum",), ("wash-sales",),
            ("winners",), ("trades-sum",), ("fx-cash",), ("leaps-sum",),
            ("fees-sum",), ("shares",), ("dil-sum",), ("list",),
            ("ccd-sum",), ("wash-radar", "margin"),
            ("sell-check", "XYZ.TO"), ("buy-check", "XYZ.TO")]

    def test_banner_after_inputs_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("margin", "taxable")],
                            {"margin": self.MARGIN})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            clean = _cli(root, "divs-sum")
            self.assertNotIn("not the clean result", clean.stderr)
            _age(root)
            with open(root / "inputs" / "margin" / "margin.tt", "a") as fh:
                fh.write("BUYSELL 2024-10-10 10:00:00 XYZ.TO -50 CAD 9.00 "
                         "450.00 0.00\n")
            for c in self.CMDS:
                r = _cli(root, *c)
                self.assertIn("not the clean result", r.stderr, c)
                self.assertNotIn("not the clean result", r.stdout, c)
            j = _cli(root, "divs-sum", "--json")
            self.assertIn("not the clean result", j.stderr)
            json.loads(j.stdout)
            # sum refuses such books before any output, the banner too
            # (owner decision 2026-11-18, TJ-4347: it printed the banner once).
            s = _cli(root, "sum")
            self.assertEqual(s.returncode, 1, s.stdout)
            self.assertEqual(s.stderr.count("not the clean result"), 0)
            self.assertIn("older than the inputs", s.stderr)


# ---------------------------------------------------------------- A2-0685
class TestHoldingsTomlAppliesPhantoms(unittest.TestCase):
    """Formerly: test_fix_a2_planning_views.TestHoldingsTomlAppliesPhantoms"""
    TT = ("BUYSELL 2024-03-10 10:00:00 ZZZ.TO -100 CAD 30.00 3000.00 0.00\n"
          "BUYSELL 2024-04-10 10:00:00 XEI.TO 10 CAD 10.00 -100.00 0.00\n")

    def test_no_negative_phantom_row(self):
        import tomllib
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("margin", "taxable")], {"margin": self.TT})
            (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
            (root / "inputs" / "margin" / "missing_history.tt").write_text(
                "OPENING 2024-03-09 ZZZ.TO 100 cost=unknown\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            doc = tomllib.loads(
                (root / "reports" / "margin_holdings.toml").read_text())
            rows = []

            def walk(x):
                if isinstance(x, dict):
                    if "symbol" in x:
                        rows.append(x)
                    for v in x.values():
                        walk(v)
                elif isinstance(x, list):
                    for v in x:
                        walk(v)
            walk(doc)
            syms = {r["symbol"]: r for r in rows}
            self.assertIn("XEI.TO", syms)
            z = syms.get("ZZZ.TO")
            self.assertTrue(z is None or float(z.get("quantity", 0)) >= 0, z)


if __name__ == "__main__":
    unittest.main()
