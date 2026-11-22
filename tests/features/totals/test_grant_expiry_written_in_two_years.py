"""Fix round 2026-10-29 (round 7, wave E), group stats: trading
statistics and option reports (lib/trade_stats, lib/option_yield,
ccd-sum, winners), review output only.

TJ-1217  the denied-loss cap ignored an assigned put's premium in the
         same ACB.
TJ-1218  a US deemed gain on one lot took the premium of another lot of
         the same date.
TJ-1219  a return of capital reversed by a positive ADJUST left the
         denied-loss tally cut.
TJ-1220  a Canadian year view missed the deemed gain of an earlier
         year's return of capital on a nil ACB.
TJ-1221  a US view of one account missed a deemed gain booked in the
         other account before an own-account move.
TJ-1222  a Canadian year view missed a short cover of an earlier year.
TJ-1223  option-yield ignored a MULT line of 100 on a series renamed
         into it.
TJ-1254  a US spin-off's part of the premium when the parent's timeline
         row is not next to the new shares' row.
TJ-1255  a US year view after an earlier year's deemed gain of the
         account the shares moved to.
TJ-1256  a grant-timing series written in two years, one contract
         bought back: the same-year expiry.

Every fixture is synthetic: invented tickers (SYN*, ZZ*), account names
and amounts.

Formerly: test_fix_1029_r7e_stats
"""
import json
import os
import subprocess
import tempfile
import unittest
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


def _project(td: str, country: str, trades: dict, tmap: str = "",
             files: dict = None, year: int = 2025, extra: str = "",
             run: bool = True) -> Path:
    root = Path(td) / "p"
    cur = "CAD" if country == "canada" else "USD"
    for a in trades:
        (root / "inputs" / a).mkdir(parents=True)
        if trades[a]:
            (root / "inputs" / a / "trades.tt").write_text(trades[a])
    for rel, text in (files or {}).items():
        (root / rel).write_text(text)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n' + extra
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in trades))
    (root / "ticker.map").write_text(tmap)
    if run:
        _run(root)
    return root


def _run(root: Path) -> None:
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def _json(root: Path, *args: str) -> dict:
    r = _cli(root, *args, "--json")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads(r.stdout)


class TestGrantExpiryWrittenInTwoYears(unittest.TestCase):
    """TJ-1256: one series written in 2025 and 2026, one contract bought
    back in 2026, the other closed by the expiry in 2026: two closes in
    the 2026 views, the gain as it is.

    Formerly: test_fix_1029_r7e_stats.TestGrantExpiryWrittenInTwoYears"""

    OPT = "SYN260320C00001000.TO"
    TOML = ('option_premium_timing = "grant"\n'
            'option_grant_timing_since = 2025\n')
    HEAD = ("BUYSELL 2025-11-03 10:00:00 SYN.TO 200 CAD 1 200 0\n"
            f"BUYSELL 2025-12-01 10:00:00 {OPT} -1 CAD 0.1 10 0\n"
            f"BUYSELL 2026-01-05 10:00:00 {OPT} -1 CAD 0.1 10 0\n")
    END = f"BUYSELL 2026-03-20 10:00:00 {OPT} 1 CAD 0 0 0\n"
    BACK = f"BUYSELL 2026-02-02 10:00:00 {OPT} 1 CAD 0.05 5 0\n"
    BACK2 = f"BUYSELL 2026-02-03 10:00:00 {OPT} 1 CAD 0.05 5 0\n"

    def _views(self, rows: str):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": rows}, year=2026,
                            extra=self.TOML)
            return (_json(root, "ccd-sum"), _json(root, "ccd-sum", "all"),
                    _json(root, "winners"), _json(root, "winners", "all"))

    @staticmethod
    def _ccd(doc) -> tuple:
        return (sum(r["contracts"] for r in doc["rows"]),
                sum(r["qty"] for r in doc["rows"]), doc["total_gain"])

    @staticmethod
    def _closes(doc) -> int:
        return sum(r["closes"] for r in doc["rows"])

    def test_one_bought_back(self):
        ccd, ccd_all, win, win_all = self._views(
            self.HEAD + self.BACK + self.END)
        self.assertEqual(self._ccd(ccd_all)[:2], (2, 2.0))
        self.assertEqual(self._ccd(ccd), (2, 2.0, 5.0))
        self.assertEqual(self._closes(win_all), 2)
        self.assertEqual(self._closes(win), 2)

    def test_one_closed_at_nil_one_still_open(self):
        # The row at nil closes one; the books hold the other: no more
        # than `all` says.
        ccd, ccd_all, win, win_all = self._views(self.HEAD + self.END)
        self.assertEqual(self._ccd(ccd)[0], self._ccd(ccd_all)[0])
        self.assertEqual(self._closes(win), self._closes(win_all))
        self.assertEqual(self._ccd(ccd)[0], 1)

    def test_both_bought_back(self):
        ccd, _all, win, _wall = self._views(
            self.HEAD + self.BACK + self.BACK2 + self.END)
        self.assertEqual(self._ccd(ccd)[0], 2)
        self.assertEqual(self._closes(win), 2)


if __name__ == "__main__":
    unittest.main()
