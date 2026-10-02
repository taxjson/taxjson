"""Regression tests for the second re-audit's corporate-action findings
(fix lists corp-actions-01 / corp-actions-02, A2-NNNN ids). All data is
synthetic: fake tickers, fake ISINs, fake broker account ids."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent

QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
             "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
             "Account #,Activity Type,Account Type")


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


def _env(home):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["TAXJSON_OFFLINE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=str(REPO_ROOT), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=_env(root.parent / "home"))


def _qt_project(td, csv_text, *, year=2026, rate=1.40):
    """A one-account (margin, Questrade) Canada project with a flat
    USD->CAD to_base.csv pre-seeded, so no FX download happens."""
    root = Path(td) / "proj"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "work").mkdir()
    (Path(td) / "home").mkdir()
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\nprovince = "ON"\n'
        'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
        f'option_grant_timing_since = {year}\n'
        '[accounts.margin]\ntype = "taxable"\n')
    d, lines = date(year - 1, 12, 1), []
    while d <= max(date.today(), date(year, 12, 31)):
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD {rate:.2f} yahoo\n")
        d += timedelta(days=1)
    (root / "work" / "to_base.csv").write_text("".join(lines))
    (root / "inputs" / "margin" / f"qt_{year}.csv").write_text(
        QT_HEADER + "\n" + csv_text.strip() + "\n")
    return root


def _event(**kw):
    from taxjson.lib.corp_actions import CorporateAction
    base = dict(date='2026-06-15', time='09:30:00', action_type='spinoff',
                source_symbol='PARX.US', source_isin='',
                target_symbol='SPNC.US', target_isin='',
                ratio_new=1, ratio_old=5, qty_disposed=0, qty_received=20,
                fmv=0.0, currency='USD', target_currency='USD',
                account='margin')
    base.update(kw)
    return CorporateAction(**base)


def _flat_fx(rate):
    """fx(amount, from, to, date) with USD->CAD = rate."""
    to_cad = {'CAD': 1.0, 'USD': rate}

    def fx(amount, a, b, _d):
        return amount * to_cad[a.upper()] / to_cad[b.upper()]
    return fx


# ================================================ s.86.1 on a USD parent
SPIN_CSV = """\
2026-01-05 12:00:00 AM,2026-01-06 12:00:00 AM,Buy,PARX,PARENTCO INC WE ACTED AS AGENT,100,50,-5000,0,-5000,USD,55500001,Trades,Individual margin
2026-06-15 12:00:00 AM,2026-06-15 12:00:00 AM,DIS,SPNC,SPINCO INC SPINOFF FROM SEC# X123456 PARENTCO INC REC 06/01/26 PAY 06/15/26 ON 100 SHS,20,0,0,0,0,USD,55500001,Dividends,Individual margin
"""  # pii-ok (synthetic account id)


class TestS861UsdParent(unittest.TestCase):
    """A2-0002 (regression of S072-15), A2-0215, A2-0967."""

    @rule("CA-CORP-06")
    def test_cad_allocation_books_in_the_listing_currency(self):
        # The CAD figure is expressed in the parent's listing currency at
        # the spin-off date's rate, so the conversion stage gives back
        # exactly the CAD amount and the native view stays one currency.
        from taxjson.lib.corp_actions import resolve_event
        rows = resolve_event(_event(), 'rollover_s_86_1',
                             hints={'allocated_acb_cad': 1400.0},
                             fx=_flat_fx(1.40))
        buy = next(r for r in rows if r['action'] == 'BUYSELL')
        adj = next(r for r in rows if r['action'] == 'ADJUST')
        self.assertEqual(buy['currency'], 'USD')
        self.assertAlmostEqual(buy['net_amount'], 1000.0, places=9)
        self.assertAlmostEqual(buy['price'], 50.0, places=9)
        self.assertEqual((adj['symbol'], adj['currency']),
                         ('PARX.US', 'USD'))
        self.assertAlmostEqual(adj['net_amount'], -1000.0, places=9)
        self.assertIn('1400.00 CAD', buy['description'])

    def test_no_rate_keeps_cad_rows(self):
        from taxjson.lib.corp_actions import resolve_event
        rows, err = _quiet(resolve_event, _event(), 'rollover_s_86_1',
                           hints={'allocated_acb_cad': 1400.0},
                           fx=lambda *a: None)
        self.assertEqual({r['currency'] for r in rows}, {'CAD'})
        self.assertIn('no CAD->USD rate', err)

    def test_mixed_currency_guard_counts_adjust_rows(self):
        from taxjson.bin.taxjson_run import _raw_mixed_currency_symbols
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "raw.json"
            p.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "symbol": "PARX.US",
                 "currency": "USD", "quantity": 100, "net_amount": 5000},
                {"action": "ADJUST", "symbol": "PARX.US",
                 "currency": "CAD", "net_amount": -1000},
            ]}))
            self.assertEqual(_raw_mixed_currency_symbols(p), ["PARX.US"])

    @rule("CA-CORP-06")
    def test_run_builds_the_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = _qt_project(td, SPIN_CSV)
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, r.stderr[-2000:])
            pend = json.loads((root / "work" / "pending_elections.json")
                              .read_text())
            eid = pend["accounts"]["margin"]["pending"][0]["event_id"]
            r = _run_cli(root, "elect", "margin", "--set",
                         f"{eid}=rollover_s_86_1", "--hint",
                         "allocated_acb_cad=1400")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertNotIn("raw holdings skipped", r.stderr)
            self.assertTrue((root / "reports" /
                             "margin_holdings.toml").exists())
            g = json.loads((root / "work" / "margin_gains.json").read_text())
            inv = {h["symbol"]: h for h in g["inventory"]}
            par = inv.get("PARX.US") or {}
            spn = inv.get("SPNC.US") or {}
            self.assertAlmostEqual(spn["total_cost"], 1400.0, places=2)
            self.assertAlmostEqual(par["total_cost"], 7000.0 - 1400.0,
                                   places=2)


if __name__ == "__main__":
    unittest.main()
