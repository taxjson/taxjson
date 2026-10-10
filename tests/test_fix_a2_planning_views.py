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


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = Path(__file__).resolve().parent.parent


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


# ---------------------------------------------------------------- A2-0366
class TestSameExportInTwoAccounts(unittest.TestCase):
    TT = ("BUYSELL 2024-02-05 09:31:00 MSFT.TO 50 CAD 40.00 -2001.00 1.00\n"
          "BUYSELL 2024-08-05 09:31:00 MSFT.TO -50 CAD 42.00 2099.00 1.00\n")

    def test_attention_and_strict_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("a1", "taxable"), ("a2", "taxable")],
                            {"a1": self.TT})
            (root / "inputs" / "a2").mkdir()
            (root / "inputs" / "a2" / "copy.tt").write_text(self.TT)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            out = r.stdout + r.stderr
            self.assertIn("ATTENTION", out)
            self.assertIn("a1/a1.tt", out)
            self.assertIn("a2/copy.tt", out)
            r = _cli(root, "run", "--strict", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout[-1500:])
            self.assertIn("a2/copy.tt", r.stdout + r.stderr)

    def test_distinct_files_are_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("a1", "taxable"), ("a2", "taxable")],
                            {"a1": self.TT,
                             "a2": self.TT.replace("2024-02-05", "2024-02-06")})
            r = _cli(root, "run", "--strict", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("same export", r.stdout + r.stderr)


# ------------------------------------------------------- A2-0380 / A2-0381
class TestStaleBooksBannerEverywhere(unittest.TestCase):
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
            # Commands with their own banner print it once.
            s = _cli(root, "sum")
            self.assertEqual(s.stderr.count("not the clean result"), 1)


# ------------------------------------------------------- A2-0684 / A2-1167
class TestUnknownAccountRefused(unittest.TestCase):
    MARGIN = ("BUYSELL 2024-06-10 10:00:00 XYZ.TO 200 CAD 10.00 -2000.00 0.00\n"
              "BUYSELL 2024-09-10 10:00:00 XYZ.TO -100 CAD 12.00 1200.00 0.00\n")

    def test_mistyped_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("margin", "taxable")],
                            {"margin": self.MARGIN})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            for c in ("edge-cases", "spinoffs", "splits", "check-dates",
                      "winners", "leaps", "leaps-sum"):
                r = _cli(root, c, "margn")
                self.assertNotEqual(r.returncode, 0, c)
                self.assertIn("margn", r.stderr, c)
                self.assertIn("check the name", r.stderr, c)
                self.assertNotIn("run `taxjson run` first", r.stderr, c)
                self.assertNotIn("Traceback", r.stderr, c)
            ok = _cli(root, "winners", "margin")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            # harvest takes symbols: one that matches no open position
            # is named, and nothing matching at all is an error.
            h = _cli(root, "harvest", "--no-ibkr", "margn")
            self.assertNotEqual(h.returncode, 0, h.stdout)
            self.assertIn("margn", h.stdout + h.stderr)
            self.assertIn("symbol", h.stdout + h.stderr)


# ---------------------------------------------------------------- A2-1182
class TestViewsNameAccountsWithoutBooks(unittest.TestCase):
    AAA = ("BUYSELL 2024-06-10 10:00:00 AAA.TO 10 CAD 10.00 -100.00 0.00\n"
           "BUYSELL 2024-09-10 10:00:00 AAA.TO -5 CAD 12.00 60.00 0.00\n")
    BBB = ("BUYSELL 2024-06-10 10:00:00 BBB.TO 10 CAD 10.00 -100.00 0.00\n"
           "BUYSELL 2024-09-10 10:00:00 BBB.TO -10 CAD 12.00 120.00 0.00\n"
           "BUYSELL 2024-09-11 10:00:00 CCC.TO 10 CAD 12.00 -120.00 0.00\n")

    def test_missing_books_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("aaa", "taxable"), ("bbb", "taxable")],
                            {"aaa": self.AAA, "bbb": self.BBB})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            for f in (root / "work").glob("bbb*"):
                f.unlink()
            for c in ("trades", "divs", "events", "roc", "gains", "shares",
                      "fees-sum", "check-dates"):
                r = _cli(root, c)
                self.assertIn("bbb", r.stderr, c)
                self.assertIn("NOT in this report", r.stderr, c)


# ---------------------------------------------------------------- A2-0685
class TestHoldingsTomlAppliesPhantoms(unittest.TestCase):
    TT = ("BUYSELL 2024-03-10 10:00:00 ZZZ.TO -100 CAD 30.00 3000.00 0.00\n"
          "BUYSELL 2024-04-10 10:00:00 XEI.TO 10 CAD 10.00 -100.00 0.00\n")

    def test_no_negative_phantom_row(self):
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("margin", "taxable")], {"margin": self.TT})
            (root / "missing_history.json").write_text(json.dumps(
                [{"symbol": "ZZZ.TO", "account": "margin"}]))
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


# ---------------------------------------------------------------- A2-0693
class TestGrantExpiryCountsAsClose(unittest.TestCase):
    OPT = "ABC260320C00050000.TO"          # expires 2026-03-20
    LATER = "ABC270115C00050000.TO"        # expires 2027-01-15
    SHARES = "BUYSELL 2026-01-05 10:00:00 ABC.TO 500 CAD 40.00 -20000.00 0.00\n"
    WRITE = f"BUYSELL 2026-02-02 10:00:00 {OPT} -5 CAD 4.00 1995.00 5.00\n"
    EXPIRE = f"BUYSELL 2026-03-20 10:00:00 {OPT} 5 CAD 0.00 0.00 0.00\n"
    BUYBACK = f"BUYSELL 2026-03-05 10:00:00 {OPT} 5 CAD 2.00 -1005.00 5.00\n"

    def _counts(self, tail, timing="", write=None):
        write = self.WRITE if write is None else write
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [("margin", "taxable")],
                            {"margin": self.SHARES + write + tail},
                            year=2026,
                            extra=("option_grant_timing_since = 2025\n"
                                   + timing))
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            ccd = json.loads(_cli(root, "ccd-sum", "--json").stdout)
            row = [x for x in ccd["rows"] if x["underlying"] == "ABC.TO"][0]
            w = _cli(root, "winners", "--json")
            wrow = [x for x in json.loads(w.stdout)["rows"]
                    if x.get("ticker") == "ABC.TO"]
            closes = wrow[0].get("closes") if wrow else None
            trades = [ln for ln in (root / "reports" / "margin_wash.sum")
                      .read_text().splitlines()
                      if "Options" in ln and "TRADES:" in ln]
            return row["contracts"], row["qty"], closes, trades

    def test_expired_grant_write_is_a_close(self):
        c, q, closes, trades = self._counts(self.EXPIRE)
        self.assertEqual((c, q), (1, 5.0))
        self.assertEqual(closes, 1)
        self.assertTrue(trades and "TRADES: 1 " in trades[0] + " ", trades)

    def test_matches_close_timing(self):
        c, q, closes, _t = self._counts(
            self.EXPIRE, 'option_premium_timing = "close"\n')
        self.assertEqual((c, q, closes), (1, 5.0, 1))

    def test_buyback_not_double_counted(self):
        c, q, closes, _t = self._counts(self.BUYBACK)
        self.assertEqual((c, q, closes), (1, 5.0, 1))

    def test_open_write_is_not_a_close(self):
        c, q, closes, _t = self._counts(
            "", write=self.WRITE.replace(self.OPT, self.LATER))
        self.assertEqual((c, q), (0, 0.0))
        self.assertIn(closes, (0, None))

    def test_helper_partial_buyback_then_expiry(self):
        from taxjson.lib.report_model import grant_write_closes
        txs = [
            {"symbol": self.OPT, "date": "2026-02-02", "qty": 5.0,
             "grant": True, "direction": "SHORT"},
            {"symbol": self.OPT, "date": "2026-03-05", "qty": 2.0,
             "direction": "SHORT"},
        ]
        self.assertEqual(grant_write_closes(txs, [], 2026), {0: 3.0})
        # Still short 3 at the end of the data: nothing expired.
        inv = [{"symbol": self.OPT, "qty": -3.0}]
        self.assertEqual(grant_write_closes(txs, inv, 2026), {})
        # A close of a prior-year write (no grant row before it) does
        # not consume this year's write.
        txs2 = [{"symbol": self.OPT, "date": "2026-01-10", "qty": 5.0,
                 "direction": "SHORT"}] + txs[:1]
        self.assertEqual(grant_write_closes(txs2, [], 2026), {1: 5.0})
        # A series expiring after the year end is open at the year end
        # (or closed in next year's books): not a close this year.
        later = [dict(txs[0], symbol=self.LATER)]
        self.assertEqual(grant_write_closes(later, [], 2026), {})
        self.assertEqual(grant_write_closes(txs[:1], [], 2025), {})


# ---------------------------------------------------------------- A2-1190
class TestChildErrorRelayed(unittest.TestCase):
    def test_last_error_line_kept(self):
        from taxjson.bin.taxjson_run import _child_error
        err = ("Traceback (most recent call last):\n"
               + "".join(f'  File "/very/long/path/{i}.py", line {i}, in f\n'
                         f"    x = y\n" for i in range(40))
               + "ValueError: could not convert string to float: 'abc'\n")
        msg = _child_error(err)
        self.assertIn("ValueError: could not convert string to float", msg)
        self.assertLess(len(msg), 600)
        self.assertEqual(_child_error(""), "(no error output)")
        self.assertEqual(_child_error("one line\n"), "one line")


if __name__ == "__main__":
    unittest.main()
