"""Backlog TJ-0075 (decided 2026-10-26): a .tt NOTOWN line undoes one
own-move pairing.

The run pairs a transfer-out and a transfer-in of the same security and
quantity in your taxable accounts as one move of your own — within 10
days (exact), or 11 to transfer_in.SLOW_MOVE_DAYS (kept at 45) days
apart when neither leg has another candidate (slow) — so an unrelated
departure and arrival paired too: the arrival took the departure's cost
and nothing let the user undo it. Now

    NOTOWN <date> <symbol> <qty> <from-account> <from-date>

in the receiving account's .tt files undoes that one pairing, exact or
slow, and never makes one: the transfer-in is then an arrival from
outside the books (booked at the broker's stated book value, or kept
out with no cost) and the transfer-out an ordinary one; in a US project
no lots move. Every other leg pairs as without the line. The slow
pairing's Info line names the line to add; a line that undoes no
pairing is a Warning naming it; `tjs check-tt` knows the keyword.
Figures change only when a user writes the line.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_decisions_2026_10_26_notown
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import settings_for
from taxjson.rules import ids as R
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# --------------------------------------------------------------- the run

def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _project(root: Path, country: str, in_date: str,
             notown: str = "") -> Path:
    """Account qa buys 100 ZZQ at 2.00 and transfers them out on
    2024-03-01; account qb receives 100 (TRANSFER BOOK VALUE 250.00) on
    `in_date` and sells them at 3.00 on 2024-06-03. `notown`: qb's
    notown.tt."""
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2024, source_currencies=[])
        + '[accounts.qa]\ntype = "taxable"\n'
        + '[accounts.qb]\ntype = "taxable"\n')
    for acct in ("qa", "qb"):
        (root / "inputs" / acct).mkdir(parents=True)
    (root / "inputs" / "qa" / "questrade_2024.csv").write_text(
        _QH + f"2024-01-10,2024-01-11,Buy,ZZQ,ZZQ CORP,100,2.00,-200.00,"
        f"0.00,-200.00,{cur},Trades,99900001,Margin\n"
        f"2024-03-01,2024-03-01,TF6,ZZQ,ZZQ CORP TRANSFER OUT,-100,0.00,"
        f"0.00,0.00,0.00,{cur},Transfers,99900001,Margin\n")
    (root / "inputs" / "qb" / "questrade_2024.csv").write_text(
        _QH + f"{in_date},{in_date},TF6,ZZQ,ZZQ CORP TRANSFER BOOK VALUE "
        f"250.00,100,0.00,0.00,0.00,0.00,{cur},Transfers,99900002,Margin\n"
        f"2024-06-03,2024-06-04,Sell,ZZQ,ZZQ CORP,-100,3.00,300.00,0.00,"
        f"300.00,{cur},Trades,99900002,Margin\n")
    if notown:
        (root / "inputs" / "qb" / "notown.tt").write_text(notown)
    return root


def _filed(root: Path):
    """(cost, gain) of the project's tax year."""
    s = json.loads(_cli(root, "sum", "--json").stdout)
    accts = s["filing"]["accounts"]
    cost = sum(a.get("acb", a.get("cost", 0.0)) for a in accts)
    return round(cost, 2), round(s["filing"]["engine_gain_unrounded"], 2)


def _arrival(root: Path) -> str:
    j = json.loads(_cli(root, "transfers", "--json").stdout)
    (got,) = [t["arrival"] for t in j["transfers"] if t["quantity"] > 0]
    return got


class TestNotOwnRunUSA(unittest.TestCase):
    """TJ-0075 end to end, US: no lots move for an undone pairing.

    Formerly: test_decisions_2026_10_26_notown.TestNotOwnRunUSA"""

    @rule(R.US_BASIS_05, R.US_BASIS_TRANSFER_BV)
    def test_tj0075_us_a_notown_line_moves_no_lots(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            plain = _project(td / "plain", "usa", "2024-03-16")
            r = _cli(plain, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_filed(plain), (200.0, 100.0))
            self.assertIn("go to qb. If they are not one move, add `NOTOWN "
                          "2024-03-16 ZZQ.US 100 qa 2024-03-01` to a .tt "
                          "file in inputs/qb/", r.stdout)
            for name, day in (("slow", "2024-03-16"), ("exact", "2024-03-06")):
                root = _project(
                    td / name, "usa", day,
                    f"NOTOWN {day} ZZQ.US 100 qa 2024-03-01\n")
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                # qb's lot is the arrival at its stated basis 250; qa
                # keeps its own lot.
                self.assertEqual(_filed(root), (250.0, 50.0), name)
                self.assertNotIn("own-account move", r.stdout)
                for acct in ("qa", "qb"):
                    self.assertFalse((root / "work" /
                                      f"{acct}_own_moves.json").exists())
                self.assertEqual(_arrival(root), "book value")


if __name__ == "__main__":
    unittest.main()
