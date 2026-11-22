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


# ---------------------------------------------------------------- A2-0693
class TestGrantExpiryCountsAsClose(unittest.TestCase):
    """Formerly: test_fix_a2_planning_views.TestGrantExpiryCountsAsClose"""
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
        from taxjson.lib.grant_writes import grant_write_closes
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


if __name__ == "__main__":
    unittest.main()
