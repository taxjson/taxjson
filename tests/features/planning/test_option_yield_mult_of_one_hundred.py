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


class TestOptionYieldMultOfOneHundred(unittest.TestCase):
    """TJ-1223: a contract of 150 renamed into a series whose ticker.map
    MULT line says 100.

    Formerly: test_fix_1029_r7e_stats.TestOptionYieldMultOfOneHundred"""

    ROWS = ("BUYSELL 2025-01-02 10:00:00 SYN1251219C00002000.TO -1 CAD .2 "
            "30 0\n"
            "SPLIT 2025-04-01 09:30:00 SYN1251219C00002000.TO "
            "SYN251219C00003000.TO 1\n"
            "BUYSELL 2025-07-02 10:00:00 SYN251219C00003000.TO 1 CAD .12 12 "
            "0\n")

    def _part(self, tmap: str, rows: str = None) -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada", {"margin": rows or self.ROWS},
                            tmap=tmap)
            part, = _json(root, "option-yield")["parts"]
            return {k: part[k] for k in ("avg_notional", "annualized")}

    def test_mult_line_of_100(self):
        self.assertEqual(self._part("MULT SYN1 150\nMULT SYN 100\n"),
                         {"avg_notional": 148.77, "annualized": 0.120994})

    def test_stated_on_a_row(self):
        self.assertEqual(
            self._part("MULT SYN1 150\n",
                       self.ROWS.replace("12 0\n", "12 0 x100\n")),
            {"avg_notional": 148.77, "annualized": 0.120994})

    def test_stated_nowhere_keeps_the_opened_size(self):
        self.assertEqual(self._part("MULT SYN1 150\n")["avg_notional"],
                         186.58)


if __name__ == "__main__":
    unittest.main()
