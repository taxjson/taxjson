"""Re-audit-2 filing-lock fixes, carryover side: the income-dating
settings in carryover's (and t1135's) full-history engine pass, the
filed-year locks it reads (unreadable, non-finite, reached through
prior_year_record, seeding the rebuilt prior years), rows after the
project year, box-18 slip gains, the base-currency check and the
standalone defaults.

Formerly: test_fix_a2_locks_co"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
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


def _row(i, date, sym, qty, net, action="BUYSELL", **kw):
    r = {"id": i, "action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": sym, "quantity": qty,
         "price": abs(net / qty) if qty else 0.0, "net_amount": net,
         "currency": kw.pop("currency", "CAD"), "account": "margin"}
    r.update(kw)
    return r


def _project(root, year, rows, extra="", country="canada", cur="CAD"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "work").mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{extra}'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": rows}))
    return root


def _carryover(root, *extra):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "carryover", *extra], cwd=REPO_ROOT, capture_output=True,
        text=True, stdin=subprocess.DEVNULL, timeout=300)


def _rows_by_year(stdout):
    return {r["year"]: r for r in json.loads(stdout)["rows"]}


class TestCarryoverBox18(unittest.TestCase):
    """Formerly: test_fix_a2_locks_co.TestCarryoverBox18"""
    @rule(R.CA_INC_06)
    def test_box18_gain_nets_the_year(self):
        # A2-0678: [[capital_gains_dividends]] names 2,500 of box-18
        # gains in 2025; the ledger's 2025 net is -5,000 + 2,500.
        book = [_row("b1", "2025-02-01", "AAA.TO", 100, -10000.0),
                _row("s1", "2025-03-03", "AAA.TO", -100, 5000.0),
                _row("d1", "2025-07-15", "ZZS.TO", 0, 2500.0,
                     action="DIVIDEND")]
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p", 2025, book)
            (p / "work" / "margin_raw.json").write_text(
                json.dumps({"transactions": book}))
            with (p / "taxjson.toml").open("a") as f:
                f.write('\n[[capital_gains_dividends]]\nsymbol = "ZZS.TO"\n'
                        'year = 2025\namount = "all"\n')
            r = _carryover(p, "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            row = _rows_by_year(r.stdout)[2025]
            self.assertAlmostEqual(row["net_gain"], -2500.0)
            self.assertAlmostEqual(row["slip_gains"], 2500.0)


if __name__ == "__main__":
    unittest.main()
