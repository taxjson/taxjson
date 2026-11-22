"""Backlog fix round 2026-10-29 (round 2), group `income`: dividend
forecasts (`tjs div-estimate`), dividend corrections and refunded
withholding, tips, slip-audit and roc-sum (T3 return of capital) and
the final-gains required-field contract.

TJ-0547  div-estimate: a dated rename carries the dividend history to
         the new symbol.
TJ-0548  div-estimate: withholding posted in another currency than its
         dividend is converted at the pay date's rate before the rate is
         read.
TJ-0549  div-estimate: a dividend paid after a split on shares counted
         on a record date before it is read in today's shares.
TJ-0552  div-estimate: a correction's refunded withholding moves with
         the gross it reverses.
TJ-0558  div-estimate: one broker account's short charge in a combined
         account is an expense, not a reversal of its own dividend,
         beside another broker account's larger long.
TJ-0429  slip-audit: the suggested .tt lines name the folder the year
         reads (../inputs/<account>/ in the shared-exports layout).
TJ-0420  slip-audit: a typed T3's box 42 is spread over the year's
         distributions when units were sold between them, not all put
         after the sale.
TJ-0427  roc-sum: a hand-typed .tt ADJUST type=roc line is a manual row.
TJ-0562  tips: TFSA-US-DIV follows the issuer's domicile, not the US
         listing alone.
TJ-0495  a gains document's income row without its amount or date is
         refused, an explicit 0 kept.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN.*, ZZ*), account names (margin), broker account
ids (99900001), amounts under 1,000. Planning numbers (CA-PLAN-06 /
US-PLAN-07) unless a test says otherwise.

Formerly: test_fix_1029_r2_income
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import div_estimate as DE
from taxjson.lib.dispatch import python_module_argv
import _paths

REPO = _paths.REPO


def _tjs(root, *args):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
               NO_COLOR="1")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args),
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(root, rows, settings="", country="canada", year=2025,
             name="trades.tt"):
    """A one-account project whose books are `rows` (a .tt file, or
    another export named `name`)."""
    root = Path(root)
    (root / "inputs" / "margin").mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{base}"\n'
        f'source_currencies = []\n{settings}\n'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "ticker.map").write_text("")
    (root / "inputs" / "margin" / name).write_text(rows)
    return root


def _row(sym, d, act, q, amount=0.0, cur="CAD", acct="margin", **kw):
    return (acct, dict(date=d, account=acct, symbol=sym, quantity=q,
                       net_amount=amount, action=act, currency=cur, **kw))


def _one(rows, sym, qty, as_of, months=3, base="CAD", fx=None,
         fx_on=None):
    doc = DE.estimate([dict(account="margin", symbol=sym, qty=qty,
                            group="taxable")], DE.payments(rows),
                      as_of=as_of, months=months, tax_year=2025,
                      base=base, fx=fx or (lambda c: 1.0), fx_on=fx_on)
    return doc


_MONTHS = ("2025-04-02", "2025-05-02", "2025-06-02", "2025-07-02")


class TestDivEstimateWithholdingCurrency(unittest.TestCase):
    """TJ-0548: a USD 10 dividend whose same-day TAX row is CAD 2.70.
    The rate read 2.70 / 10 = 27% and CAD 10.94 over three payments; at
    USD/CAD 1.35, CAD 2.70 is USD 2.00, 20%, and the three payments'
    withholding is CAD 8.10.

    Formerly: test_fix_1029_r2_income.TestDivEstimateWithholdingCurrency"""

    def _rows(self):
        rows = [_row("SYN.US", "2025-01-06", "BUYSELL", 100, 100,
                     cur="USD")]
        for d in _MONTHS:
            rows.append(_row("SYN.US", d, "DIVIDEND", 100, 10, cur="USD",
                             gross_amount=10))
            rows.append(_row("SYN.US", d, "TAX", 0, 2.70, type="tax"))
        return rows

    def test_converted_at_the_pay_dates_rate(self):
        doc = _one(self._rows(), "SYN.US", 100, "2025-07-04",
                   fx=lambda c: 1.35, fx_on=lambda c, d: 1.35)
        r = doc["rows"][0]
        self.assertAlmostEqual(r["withholding_rate"], 0.20)
        self.assertEqual(r["flags"], [])
        self.assertAlmostEqual(doc["totals"]["withholding_window"]
                               ["taxable"], 8.10)
        # The pay date's rate, not the latest: 1.35 then, 1.50 now.
        doc = _one(self._rows(), "SYN.US", 100, "2025-07-04",
                   fx=lambda c: 1.50, fx_on=lambda c, d: 1.35)
        self.assertAlmostEqual(doc["rows"][0]["withholding_rate"], 0.20)

    def test_no_rate_no_withholding_rate(self):
        doc = _one(self._rows(), "SYN.US", 100, "2025-07-04",
                   fx=lambda c: None, fx_on=lambda c, d: None)
        r = doc["rows"][0]
        self.assertIsNone(r["withholding_rate"])
        self.assertTrue(any("withholding in CAD on USD payments" in f
                            for f in r["flags"]), r["flags"])

    def test_the_command_after_a_run(self):
        rows = "BUYSELL 2025-01-06 10:00:00 SYN.US 100 USD 1 100 0\n"
        for d in _MONTHS:
            rows += (f"DIVIDEND {d} 10:00:00 SYN.US 100 USD 0.1 10\n"
                     f"TAX {d} 10:00:00 SYN.US 0 CAD 0 2.7\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", rows)
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "source_currencies = []", 'source_currencies = ["USD"]'))
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs(root, "div-estimate", "--date", "2025-07-04",
                     "--months", "3", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            row = json.loads(r.stdout)["rows"][0]
            rate = row["fx_rate"]
            self.assertTrue(rate and rate > 1, row)
            # CAD 2.70 over USD 10 at the books' rate on the pay date.
            self.assertAlmostEqual(row["withholding_rate"],
                                   2.70 / (10 * rate), places=3)


class TestDivEstimateCorrectionRefund(unittest.TestCase):
    """TJ-0552: a monthly dividend of 10 with TAX 1.50; the next day a
    correction of -5 and a TAX refund of -0.75. The gross was netted to
    5, but the refund stayed on the correction's date: 1.50 over 5, 30%,
    4.50 over three payments. Net: 0.75 over 5, 15%, 2.25.

    Formerly: test_fix_1029_r2_income.TestDivEstimateCorrectionRefund"""

    def test_the_refund_moves_with_the_gross(self):
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        for d in _MONTHS:
            rows.append(_row("SYN.TO", d, "DIVIDEND", 100, 8.5,
                             gross_amount=10))
            rows.append(_row("SYN.TO", d, "TAX", 100, 1.5, type="tax"))
        rows.append(_row("SYN.TO", "2025-07-03", "DIVIDEND", 100, -5,
                         gross_amount=-5))
        rows.append(_row("SYN.TO", "2025-07-03", "TAX", 0, -0.75,
                         type="tax"))
        doc = _one(rows, "SYN.TO", 100, "2025-07-04")
        r = doc["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 0.05)
        self.assertAlmostEqual(r["withholding_rate"], 0.15)
        self.assertAlmostEqual(doc["totals"]["withholding_window"]
                               ["taxable"], 2.25)

    def test_a_refund_across_two_postings(self):
        """A reversal of 15 cancels the latest 10 and 5 of an earlier
        posting; its refund of -2.25 moves in the same proportion."""
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        for d in _MONTHS:
            rows.append(_row("SYN.TO", d, "DIVIDEND", 100, 8.5,
                             gross_amount=10))
            rows.append(_row("SYN.TO", d, "TAX", 100, 1.5, type="tax"))
        rows.append(_row("SYN.TO", "2025-07-03", "DIVIDEND", 100, -15,
                         gross_amount=-15))
        rows.append(_row("SYN.TO", "2025-07-03", "TAX", 0, -2.25,
                         type="tax"))
        pays = DE.payments(rows)
        by = {p.pay_date: p for p in pays["SYN.TO"]}
        self.assertNotIn("2025-07-02", by)
        self.assertAlmostEqual(by["2025-06-02"].gross, 5.0)
        self.assertAlmostEqual(by["2025-06-02"].tax, 0.75)


if __name__ == "__main__":
    unittest.main()
