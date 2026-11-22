"""Fix round 2026-10-29 (round 7), group optyield: option-yield, the
LEAPS views, ccd-sum and the report filters.

TJ-0692  `SYN.B` (a class share's bare ticker) matched no option or
         trade of SYN.B.US: the first dot came off, with the class.
TJ-0694  a futures option's x1000 stated on its close alone priced the
         open at the default 100 contract size.
TJ-0774  shares held partly at an unknown cost (an OPENING cost=unknown)
         gave a yield on the known part of their cost; list and shares
         showed that part as the whole.
TJ-0777  a closed unknown-cost long (an OPENING cost=unknown) made the
         LEAPS views read its sale as a write and a later real entry as
         its buy-back: the known LEAPS gain disappeared.
TJ-0778  ccd-sum filtered a grant-timing write's expiry by the write's
         date: an expiry inside a narrow window was not counted.
TJ-0784  option-yield of a tax year not yet begun reported today's
         closes under its label.
TJ-0785  win-rates refused a digit-leading listing (1234.HK) as an
         invalid period.
TJ-0790  an expiry carrying a fee (IB `C;Ep` with a commission) read
         as a buy-back.
TJ-0903  a written call renamed by a SPLIT and bought back in the new
         series counted an extra (inferred) expiry in ccd-sum.
TJ-0907  a series split reported by one blended Canadian account left
         another account's contracts in the old series: leaps /
         leaps-sum ACCOUNT lost the LEAPS gain, option-yield invented
         open contracts.
TJ-0909  an account named `123abc` was refused as an invalid period;
         one named `2025` was silently read as the year.
TJ-0910  option-yield did not find the class shares (SYN.B.TO) behind
         the option root SYNB.

Every fixture is synthetic: invented tickers (SYN*), account names and
amounts.

Formerly: test_fix_1029_r7_optyield
"""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _project(td: str, files, *, country="canada", year=2025,
             accounts=None, settings="") -> Path:
    """A project with each account's `.tt` book; `run` done."""
    root = Path(td) / "p"
    accounts = accounts or {"margin": "taxable"}
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
    for a, text in files.items():
        name, text = text if isinstance(text, tuple) else ("trades.tt", text)
        (root / "inputs" / a / name).write_text(text)
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        f'source_currencies = []\n{settings}\n'
        + "".join(f'[accounts."{a}"]\ntype = "{t}"\n'
                  for a, t in accounts.items()))
    (root / "ticker.map").write_text("")
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return root


def _json(root: Path, *args: str) -> dict:
    r = _cli(root, *args, "--json")
    assert r.returncode == 0, (args, r.stdout[-2000:], r.stderr[-2000:])
    return json.loads(r.stdout)


# --------------------------------------------------------------------
# A closed unknown-cost long before a real LEAPS entry (TJ-0777)
# --------------------------------------------------------------------

class TestLeapsAfterAnUnknownCostLong(unittest.TestCase):
    """TJ-0777: one long call opened cost=unknown and sold; then a new
    one bought (more than 9 months to expiry) for 10 and sold for 20:
    the LEAPS gain is 10.

    Formerly: test_fix_1029_r7_optyield.TestLeapsAfterAnUnknownCostLong"""

    def test_known_entry_after_unknown_history(self):
        sym = "SYN270115C00001000.TO"
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"margin":
                f"OPENING 2025-01-02 {sym} 1 cost=unknown\n"
                f"BUYSELL 2025-01-02 10:00:00 {sym} -1 CAD 0.1 10 0\n"
                f"BUYSELL 2025-02-03 10:00:00 {sym} 1 CAD 0.1 10 0\n"
                f"BUYSELL 2025-03-03 10:00:00 {sym} -1 CAD 0.2 20 0\n"})
            for cmd in ("leaps", "leaps-sum"):
                self.assertEqual(_json(root, cmd)["total_gain"], 10.0, cmd)


# --------------------------------------------------------------------
# ccd-sum: an expiry in a narrow window (TJ-0778), a renamed write
# bought back (TJ-0903)
# --------------------------------------------------------------------

def _occ(d: date) -> str:
    return f"SYN{d:%y%m%d}C00001000.TO"


class TestCcdGrantExpiry(unittest.TestCase):
    """Formerly: test_fix_1029_r7_optyield.TestCcdGrantExpiry"""

    def test_expiry_in_window_write_before_it(self):
        """TJ-0778: a call written about four months ago expired three
        weeks ago (grant timing): `ccd-sum 2m` counts its close, with
        no premium in that window."""
        today = date.today()
        wrote, expired = today - timedelta(days=120), today - timedelta(
            days=21)
        sym = _occ(expired)
        with tempfile.TemporaryDirectory() as td:
            root = _project(
                td, {"margin":
                     f"BUYSELL {wrote} 10:00:00 {sym} -1 CAD 0.1 10 0\n"
                     f"BUYSELL {expired} 10:00:00 {sym} 1 CAD 0 0 0\n"},
                year=wrote.year,
                settings=('option_premium_timing = "grant"\n'
                          f"option_grant_timing_since = {wrote.year}\n"))
            doc = _json(root, "ccd-sum", "2m")
            self.assertEqual([(r["contracts"], r["qty"], r["gain"])
                              for r in doc["rows"]], [(1, 1.0, 0.0)])
            self.assertEqual(doc["total_gain"], 0.0)
            out = _cli(root, "ccd-sum", "2m").stdout
            self.assertNotIn("No covered-call", out)

    def test_renamed_write_bought_back(self):
        """TJ-0903: written for 10, split 2-for-1 into the new series,
        both bought back for 4: one close of two contracts, gain 6."""
        old, new = "SYN250919C00002000.TO", "SYN250919C00001000.TO"
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"margin":
                f"BUYSELL 2025-01-03 10:00:00 {old} -1 CAD .1 10 0\n"
                f"SPLIT 2025-04-01 09:30:00 {old} {new} 2\n"
                f"BUYSELL 2025-05-02 10:00:00 {new} 2 CAD .02 4 0\n"},
                settings="option_grant_timing_since = 2025\n")
            doc = _json(root, "ccd-sum")
            self.assertEqual([(r["contracts"], r["qty"], r["gain"])
                              for r in doc["rows"]], [(1, 2.0, 6.0)])

    def test_helper_follows_the_rename(self):
        from taxjson.lib.grant_writes import grant_write_closes, grant_write_expiries
        old, new = "SYN250919C00002000.TO", "SYN250919C00001000.TO"
        txs = [{"symbol": old, "date": "2025-01-03", "qty": 1.0,
                "grant": True, "direction": "SHORT"},
               {"symbol": new, "date": "2025-05-02", "qty": 2.0,
                "direction": "SHORT"}]
        split = [{"action": "SPLIT", "symbol": old, "symbol_new": new,
                  "date": "2025-04-01", "quantity": 2.0}]
        self.assertEqual(grant_write_closes(txs, [], 2025, split), {})
        # Bought back one of the two: the other expired, in the new
        # series' units, on its expiry date.
        txs[1]["qty"] = 1.0
        self.assertEqual(grant_write_expiries(txs, [], 2025, split),
                         {0: (1.0, "2025-09-19")})
        # Without the split: the old series' write reads as expired.
        self.assertEqual(grant_write_closes(txs, [], 2025), {0: 1.0})


if __name__ == "__main__":
    unittest.main()
