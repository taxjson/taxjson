"""Backlog group `views` (2026-10-25): the stats, win-rates,
winners / losers, `wash-sales --explain`, audit and Schedule 3 views.

TJ-0025  win-rates SYMBOL: the denied total and the unknown-cost count
         cover the symbols named, not every ticker.
TJ-0061  stats / win-rates: a denied loss on an option whose
         replacement option is exercised counts once (the deferral
         rides the premium into the shares' tally).
TJ-0062  stats: an s.86.1 spin-off moves its share of the parent's
         deferral tally to the new shares, as it moves the ACB.
TJ-0096  wash-sales --explain: a denial part permanent names the
         taxable replacement's date, not the registered one's.
TJ-0111  wash-sales --explain: the window's ACB/SH after the trigger
         row includes the deferred loss added on that row.
TJ-0113  audit's pool history: an ADJUST's amount is not a quantity.
TJ-0119  sum / form-export notes: a short sale's opening commission
         stays netted in its proceeds (the filed cells unchanged).
TJ-0120  winners / losers: a DENIED column, so PROCEEDS - COST + DENIED
         is the GAIN.
TJ-0183  winners / losers scoped to an account name it on the total.
TJ-0196  audit with an account deferred at a corp-action election:
         not audited (the blended accounts with it too), never
         MISMATCHED / FAILED with a stale-work/ diagnosis.

Synthetic books only: made-up tickers (ZZA.TO, ZZB.TO ...), account
names (margin, margin2, rrsp), amounts under 1,000.

Formerly: test_fix_backlog_views
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`tjs -C root args...` from this tree's sources, captured
    (width 0)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


def _project(tmp: str, country: str, year: int, books: dict,
             types: dict) -> Path:
    """A project with one .tt book per account ({account: lines})."""
    root = Path(tmp) / "p"
    base = "USD" if country == "usa" else "CAD"
    toml = (f'[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = []\n')
    for acct, typ in types.items():
        toml += f'[accounts.{acct}]\ntype = "{typ}"\n'
    (root / "inputs").mkdir(parents=True)
    (root / "taxjson.toml").write_text(toml)
    for acct, lines in books.items():
        d = root / "inputs" / acct
        d.mkdir()
        (d / f"{acct}.tt").write_text("\n".join(lines) + "\n")
    return root


def _flat(text: str) -> str:
    return " ".join(text.split())


class TestUnitedStatesViews(unittest.TestCase):
    """Formerly: test_fix_backlog_views.TestUnitedStatesViews"""

    @rule(R.US_RPT_12)
    def test_tj0061_tj0120_us_exercised_replacement_and_denied_column(self):
        """TJ-0061 (the US engine's lots): a call sold at a 300 loss, its
        replacement exercised into ZZQ.US shares sold at their basis:
        `stats` nets 0, the shares' sale recovering the 300 the wash
        sale added. TJ-0120: `losers` shows the 300 disallowed in a
        DENIED column, so its row adds up."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "usa", 2026, {"margin": [
                "BUYSELL 2026-02-02 09:30:00 ZZQ270115C00002000.US 1 USD 5.00 500.00 0",
                "BUYSELL 2026-03-02 09:30:00 ZZQ270115C00002000.US -1 USD 2.00 200.00 0",
                "BUYSELL 2026-03-10 09:30:00 ZZQ270115C00002000.US 1 USD 2.50 250.00 0",
                "ASSIGN 2026-05-01 16:00:00 ZZQ270115C00002000.US -1 USD 0 0",
                "ASSIGN 2026-05-01 16:00:01 ZZQ.US 100 USD 2.00 200.00",
                "BUYSELL 2026-06-01 09:30:00 ZZQ.US -100 USD 7.50 750.00 0",
                "BUYSELL 2026-02-02 09:30:00 ZZW.US 10 USD 30.00 300.00 0",
                "BUYSELL 2026-06-01 09:30:00 ZZW.US -10 USD 20.00 200.00 0",
                "BUYSELL 2026-06-15 09:30:00 ZZW.US 6 USD 20.00 120.00 0",
            ]}, {"margin": "taxable"})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            st = json.loads(_cli(root, "stats", "--json").stdout)
            self.assertEqual(st["classes"]["long-options"]["net_pl"], -300.0)
            # ZZQ's shares +300, ZZW's loss -100 (its 60 disallowed
            # counted in it once).
            self.assertEqual(st["classes"]["long-shares"]["net_pl"], 200.0)
            self.assertEqual(st["total"]["net_pl"], -100.0)
            text = _flat(_cli(root, "losers").stdout)
            self.assertIn("ZZW.US 1 200.00 300.00 60.00 -40.00", text)
            self.assertIn("the loss the wash-sale rule denied", text)


if __name__ == "__main__":
    unittest.main()
