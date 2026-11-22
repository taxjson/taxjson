"""Backlog fix round 7 (wave C) of 2026-10-29, group `forms`: form
export, carryover, the estimate and the per-account reports.

TJ-0993  the standalone form export refuses gains files of more than
         one tax year without --year.
TJ-1002  taxjson-carryover --claimed-year refuses an implausible year
         (2205), as the claimed file does.
TJ-1018  a Canadian partial claim (99.98 of 100.00 of losses in whole
         cents) leaves its 0.02 carried forward.
TJ-1020  the estimate takes a mapped capital-gains dividend and its
         reversal out of the dividends as one net amount.
TJ-1008  the long-option report (reports/leaps.rpt, taxjson-leaps-gains)
         counts an unknown-cost option sale the pipeline moved to
         manual_reporting_required.
TJ-1011  taxjson-sum-gains FILE groups an option on a held class share
         (SYN.B.TO) under that share, as stdin does.
TJ-1013  a per-account position reopened after a sale that settled
         across a split starts on its own buy's date.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*), account names (margin), amounts under
1,000 (or round test figures).

Formerly: test_fix_1029_r7c_forms
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO


def _env():
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _tjs(root, *args):
    """`tjs -C ROOT ARGS` in a child process (stdin closed, unwrapped)."""
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=_env(),
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(root, country="canada", year=2025, tt=None, settings="",
             accounts=("margin",), ticker_map=""):
    root = Path(root)
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        f'source_currencies = []\n{settings}\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text(ticker_map)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    if tt is not None:
        (root / "inputs" / accounts[0] / "trades.tt").write_text(tt)
    return root


def _run(test, root):
    r = _tjs(root, "run", "--no-input")
    test.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
    return r


GENERIC_TOML = ('[columns]\ndate = "date"\nsettle = "settle"\n'
                'action = "action"\nsymbol = "symbol"\n'
                'quantity = "quantity"\nprice = "price"\n'
                'amount = "amount"\n[actions]\nBUY = "buy"\n'
                'SELL = "sell"\n[defaults]\ncurrency = "CAD"\n')


class TestSplitGainsStartAfterLaggedSale(unittest.TestCase):
    """TJ-1013: 100 ZZOLD.TO bought in January, all sold June 3 settling
    June 6, a 2-for-1 split June 4, 10 bought June 9. The per-account
    gains file and `tjs list` gave the new position January's start:
    the account's date walk subtracted the sale's 100 pre-split shares
    from the 200 the split made.

    Formerly: test_fix_1029_r7c_forms.TestSplitGainsStartAfterLaggedSale"""

    def _since(self, d, sale_settle):
        p = _project(Path(d),
                     tt="SPLIT 2025-06-04 09:30:00 ZZOLD.TO ZZOLD.TO 2\n")
        inp = p / "inputs" / "margin"
        (inp / "generic_trades.csv").write_text(
            "date,settle,action,symbol,quantity,price,amount\n"
            "2025-01-02,2025-01-03,BUY,ZZOLD.TO,100,1,-100\n"
            f"2025-06-03,{sale_settle},SELL,ZZOLD.TO,100,1.5,150\n"
            "2025-06-09,2025-06-10,BUY,ZZOLD.TO,10,1,-10\n")
        (inp / "generic.toml").write_text(GENERIC_TOML)
        _run(self, p)
        out = {}
        for f in sorted((p / "work").glob("margin_gains*.json")):
            for row in json.loads(f.read_text()).get("inventory") or []:
                if row.get("symbol") == "ZZOLD.TO":
                    out[f.name] = (row.get("qty"),
                                   row.get("position_start_date"))
        r = _tjs(p, "list", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return out, r.stdout

    def test_reopened_position_starts_june_9(self):
        for settle in ("2025-06-06", "2025-06-03"):
            with self.subTest(settle=settle), \
                    tempfile.TemporaryDirectory() as d:
                inv, listed = self._since(Path(d) / "p", settle)
                self.assertTrue(inv)
                for name, (qty, start) in inv.items():
                    self.assertAlmostEqual(float(qty), 10.0, msg=name)
                    self.assertEqual(start, "2025-06-09", name)
                self.assertIn("2025-06-09", listed)
                self.assertNotIn("2025-01-02", listed)

    def test_unit_position_starts(self):
        from taxjson.bin.taxjson_split_gains import _position_starts
        rows = [
            {"action": "BUYSELL", "symbol": "ZZOLD.TO", "quantity": 100,
             "date": "2025-01-02", "date_settle": "2025-01-03"},
            {"action": "BUYSELL", "symbol": "ZZOLD.TO", "quantity": -100,
             "date": "2025-06-03", "date_settle": "2025-06-06",
             "time": "10:00:00"},
            {"action": "SPLIT", "symbol": "ZZOLD.TO",
             "symbol_new": "ZZOLD.TO", "quantity": 2,
             "date": "2025-06-04", "date_settle": "2025-06-04",
             "time": "09:30:00"},
            {"action": "BUYSELL", "symbol": "ZZOLD.TO", "quantity": 10,
             "date": "2025-06-09", "date_settle": "2025-06-10"},
        ]
        self.assertEqual(_position_starts(rows, "settle"),
                         {"ZZOLD.TO": "2025-06-09"})
        # On trade dates the sale is before the split: no lag to undo.
        self.assertEqual(_position_starts(rows, "trade"),
                         {"ZZOLD.TO": "2025-06-09"})


if __name__ == "__main__":
    unittest.main()
