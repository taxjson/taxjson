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


class TestDivEstimateRename(unittest.TestCase):
    """TJ-0547: 100 SYN.TO paid 10 a month April to July, then RENAME
    July 3 SYN.TO NEW.TO. As of July 4, NEW.TO was omitted with no
    history and the forecast was 0; the four payments are the same
    security's, so it is 10 a month.

    Formerly: test_fix_1029_r2_income.TestDivEstimateRename"""

    def _rows(self):
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        rows += [_row("SYN.TO", d, "DIVIDEND", 100, 10, gross_amount=10)
                 for d in _MONTHS]
        rows.append(_row("SYN.TO", "2025-07-03", "SPLIT", 1.0,
                         symbol_new="NEW.TO"))
        return rows

    def test_the_history_follows_the_rename(self):
        doc = _one(self._rows(), "NEW.TO", 100, "2025-07-04")
        self.assertEqual(doc["omitted_no_history"], [])
        r = doc["rows"][0]
        self.assertEqual(r["frequency"], "monthly")
        self.assertEqual(r["payments"], 4)
        self.assertAlmostEqual(r["last_per_share"], 0.10)
        self.assertEqual(r["next_pay_date"], "2025-08-02")
        self.assertAlmostEqual(r["window_amount"], 30.0)
        # As of a date before the rename the shares held now are still
        # NEW.TO's: the same history.
        r = _one(self._rows(), "NEW.TO", 100, "2025-07-02")["rows"][0]
        self.assertAlmostEqual(r["window_amount"], 30.0)

    def test_a_later_payment_under_the_old_ticker_stays_its_own(self):
        """A row of the old ticker paid and entitled after the rename is
        another security's (the ticker reused): it is not NEW.TO's."""
        rows = self._rows() + [_row("SYN.TO", "2025-09-02", "DIVIDEND",
                                    50, 99, gross_amount=99)]
        pays = DE.payments(rows)
        self.assertEqual([p.pay_date for p in pays["NEW.TO"]], list(_MONTHS))
        self.assertEqual([p.pay_date for p in pays["SYN.TO"]],
                         ["2025-09-02"])

    def test_a_rename_with_a_ratio_and_a_later_split(self):
        """A rename that consolidates 2-for-1 (ratio 0.5), then a 3-for-1
        split of the new ticker: the old payments are read in today's
        shares (100 old = 50 new = 150 now; 10 / 150 a share)."""
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        rows += [_row("SYN.TO", d, "DIVIDEND", 100, 10, gross_amount=10)
                 for d in _MONTHS]
        rows.append(_row("SYN.TO", "2025-07-03", "SPLIT", 0.5,
                         symbol_new="NEW.TO"))
        rows.append(_row("NEW.TO", "2025-07-10", "SPLIT", 3.0,
                         symbol_new="NEW.TO"))
        r = _one(rows, "NEW.TO", 150, "2025-07-12")["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 10 / 150, places=5)
        self.assertAlmostEqual(r["window_amount"], 30.0)

    def test_the_command_after_a_run(self):
        rows = ("BUYSELL 2025-01-06 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                + "".join(f"DIVIDEND {d} 10:00:00 SYN.TO 100 CAD 0.1 10\n"
                          for d in _MONTHS)
                + "RENAME 2025-07-03 SYN.TO NEW.TO\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", rows)
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs(root, "div-estimate", "--date", "2025-07-04",
                     "--months", "3", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["omitted_no_history"], [])
            self.assertEqual([x["symbol"] for x in doc["rows"]], ["NEW.TO"])
            self.assertAlmostEqual(doc["totals"]["window"]["taxable"], 30.0)


class TestDivEstimateRecordDateBeforeSplit(unittest.TestCase):
    """TJ-0549: four monthly CAD 10 dividends on 100 shares; the July 2
    payment's record date is June 27, before the June 30 2-for-1 split.
    It names the 100 pre-split shares: 0.05 a share now, 10 a payment on
    200 shares, not 20.

    Formerly: test_fix_1029_r2_income.TestDivEstimateRecordDateBeforeSplit"""

    def test_the_record_date_decides_the_denomination(self):
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        for d in _MONTHS:
            kw = {"record_date": "2025-06-27"} if d == "2025-07-02" else {}
            rows.append(_row("SYN.TO", d, "DIVIDEND", 100, 10,
                             gross_amount=10, **kw))
        rows.append(_row("SYN.TO", "2025-06-30", "SPLIT", 2.0,
                         symbol_new="SYN.TO"))
        r = _one(rows, "SYN.TO", 200, "2025-07-04")["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 0.05)
        self.assertAlmostEqual(r["next_amount"], 10.0)
        self.assertAlmostEqual(r["window_amount"], 30.0)

    def test_a_record_date_after_the_split_is_not_scaled(self):
        """The control: a July 2 payment of record June 30 or later is on
        the post-split shares the row names (200): 0.05 a share."""
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        for d in _MONTHS[:3]:
            rows.append(_row("SYN.TO", d, "DIVIDEND", 100, 10,
                             gross_amount=10))
        rows.append(_row("SYN.TO", "2025-07-02", "DIVIDEND", 200, 10,
                         gross_amount=10, record_date="2025-06-30"))
        rows.append(_row("SYN.TO", "2025-06-30", "SPLIT", 2.0,
                         symbol_new="SYN.TO"))
        r = _one(rows, "SYN.TO", 200, "2025-07-04")["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 0.05)
        self.assertAlmostEqual(r["window_amount"], 30.0)


class TestDivEstimateCombinedShort(unittest.TestCase):
    """TJ-0558: one taxjson account combines broker accounts A and B.
    B holds 100, is paid 10, then sells 200 (short 100) while A buys
    1000; B is charged 10 on its short. The combined balance is long
    900, so the charge read as a reversal and erased B's payment: 900
    shares with no history. B's own balance is short: an expense.

    Formerly: test_fix_1029_r2_income.TestDivEstimateCombinedShort"""

    def _rows(self, opening_without_source=False):
        b, a = "99900002", "99900001"
        rows = [_row("SYN.TO", "2025-06-02", "BUYSELL", 100, 100,
                     source_account=b),
                _row("SYN.TO", "2025-07-02", "DIVIDEND", 100, 10,
                     gross_amount=10, record_date="2025-07-02",
                     source_account=b),
                _row("SYN.TO", "2025-07-03", "BUYSELL", 1000, 1000,
                     source_account=a),
                _row("SYN.TO", "2025-07-03", "BUYSELL", -200, 200,
                     source_account=b),
                _row("SYN.TO", "2025-07-04", "DIVIDEND", 100, -10,
                     gross_amount=-10, record_date="2025-07-04",
                     source_account=b)]
        if opening_without_source:
            rows.insert(0, _row("SYN.TO", "2025-01-02", "OPENING_BALANCE",
                                100, 0))
        return rows

    def test_the_broker_accounts_own_short_is_an_expense(self):
        doc = _one(self._rows(), "SYN.TO", 900, "2025-07-05")
        self.assertEqual(doc["omitted_no_history"], [])
        r = doc["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 0.10)
        # One payment, three days old: assumed quarterly, so the October
        # payment of 0.10 x 900 is in the 3-month window (it was 22.50,
        # pro rata from one payment a year).
        self.assertIs(r["frequency_assumed"], True)
        self.assertAlmostEqual(r["window_amount"], 90.0)

    def test_a_row_naming_no_broker_account_falls_back_to_the_account(
            self):
        """Units opened by a row naming no broker account (a .tt OPENING
        line) leave each broker account's own balance incomplete: the
        account's decides, as before (long 1000 here: a reversal)."""
        pays = DE.payments(self._rows(opening_without_source=True))
        self.assertNotIn("SYN.TO", pays)


if __name__ == "__main__":
    unittest.main()
