"""2026-10-24 audit, group "divest" (`tjs div-estimate`, lib/div_estimate):
A09, A10, A29, A30, A50, B15. Synthetic payers and projects only; the
probes are the audit's, as unit tests. Planning numbers only (CA-PLAN-06
/ US-PLAN-07): no filed figure is involved."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import div_estimate as DE
from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]
SYM = "ZZQ.US"


def _div(d, q=10, amount=10, act="DIVIDEND", cur="USD", acct="margin",
         **kw):
    return (acct, dict(date=d, account=acct, symbol=SYM, quantity=q,
                       net_amount=amount, action=act, currency=cur, **kw))


def _tx(d, q, act="BUYSELL", acct="margin"):
    return (acct, dict(date=d, account=acct, symbol=SYM, quantity=q,
                       action=act, currency="USD", net_amount=0.0))


def _estimate(rows, qty=10, as_of="2026-03-02", months=3, acct="margin"):
    doc = DE.estimate([dict(account=acct, symbol=SYM, qty=qty,
                            group="taxable")], DE.payments(rows),
                      as_of=as_of, months=months, tax_year=2026,
                      base="USD", fx=lambda c: 1)
    return doc["rows"][0]


_MONTHS = ["2026-01-01", "2026-02-01", "2026-03-01"]


class TestDivEstimateAudit1024(unittest.TestCase):

    def test_a09_split_scales_the_history_into_todays_shares(self):
        """A09: Dividend projection does not adjust historical per-share
        payments after a split. Three monthly payments of 1/share on 10
        shares, then a 2-for-1 SPLIT: 20 shares held now pay 10 next
        month (0.5/share), not 20."""
        rows = [_div(d) for d in _MONTHS] + [_div("2026-03-02", 2, 0,
                                                  "SPLIT")]
        r = _estimate(rows, qty=20)
        self.assertAlmostEqual(r["last_per_share"], 0.5)
        self.assertAlmostEqual(r["next_amount"], 10.0)
        self.assertEqual(r["status"], "ok")
        # The split is one corporate event: a second account's row of
        # it (its own book) does not scale twice.
        twice = rows + [_div("2026-03-02", 2, 0, "SPLIT", acct="rrsp")]
        self.assertAlmostEqual(_estimate(twice, qty=20)["last_per_share"],
                               0.5)
        # As of the day before the split, the history is still read in
        # the denomination of the books' current holding, the shares the
        # command multiplies by (2026-10-25 audit planning-us#2: reading
        # it in that day's shares doubled the forecast).
        r = _estimate(rows, qty=20, as_of="2026-03-01")
        self.assertAlmostEqual(r["last_per_share"], 0.5)
        self.assertAlmostEqual(r["next_amount"], 10.0)
        # A ratio of 0 or less is no split (the engine refuses it).
        bad = [_div(d) for d in _MONTHS] + [_div("2026-03-02", 0, 0,
                                                 "SPLIT")]
        self.assertAlmostEqual(_estimate(bad)["last_per_share"], 1.0)

    def test_a10_unlike_currencies_are_never_summed(self):
        """A10: Dividend history sums unlike currencies for the same
        symbol and date. A USD 10 and a CAD 100 posting of one date are
        two payments; the estimate uses the account's latest payment's
        currency and flags the other."""
        rows = [_div(d) for d in _MONTHS] + [_div("2026-03-01", 10, 100,
                                                  cur="CAD")]
        ps = DE.payments(rows)[SYM]
        by = sorted((p.currency, p.per_share) for p in ps
                    if p.pay_date == "2026-03-01")
        self.assertEqual(by, [("CAD", 10.0), ("USD", 1.0)])
        r = _estimate(rows)
        self.assertEqual(r["currency"], "USD")
        self.assertAlmostEqual(r["last_per_share"], 1.0)
        self.assertEqual(r["flags"],
                         ["payments in CAD too: estimated from the USD ones"])
        # The flag puts the payer in the default view's "to check" line.
        doc = DE.estimate([dict(account="margin", symbol=SYM, qty=10,
                                group="taxable")], DE.payments(rows),
                          as_of="2026-03-02", months=3, tax_year=2026,
                          base="USD", fx=lambda c: 1)
        self.assertIn("payer(s) to check", DE.render(doc).text())
        # Another account paid in CAD by its broker: its estimate is
        # in CAD, from the CAD postings, the USD ones flagged.
        rows = [_div(d) for d in _MONTHS]
        rows += [_div(d, 10, 14, cur="CAD", acct="other") for d in _MONTHS]
        r = _estimate(rows, acct="other")
        self.assertEqual((r["currency"], r["last_per_share"]), ("CAD", 1.4))
        self.assertIn("payments in USD too", r["flags"][0])
        # A reversal nets only within its currency.
        rows = [_div(d) for d in _MONTHS] + [_div("2026-03-01", 10, 100,
                                                  cur="CAD"),
                                             _div("2026-03-03", 10, -100,
                                                  cur="CAD")]
        ps = DE.payments(rows)[SYM]
        self.assertEqual(sorted((p.pay_date, p.currency) for p in ps),
                         [(d, "USD") for d in _MONTHS])

    def test_a29_withholding_weighted_over_the_broker_accounts(self):
        """A29: Dividend estimate applies the smallest broker subaccount
        withholding rate to all holdings. 90 shares in one broker
        account, 10 in another with 2 of tax on its 10: the rate is
        2/100, so 6 on a 300 window, not 60."""
        rows = []
        for d in _MONTHS:
            rows += [_div(d, 90, 90, source_account="source-a"),
                     _div(d, 10, 10, source_account="source-b"),
                     _div(d, 0, 2, "TAX", source_account="source-b")]
        r = _estimate(rows, qty=100)
        self.assertAlmostEqual(r["window_amount"], 300.0)
        self.assertAlmostEqual(r["withholding_rate"], 0.02)
        self.assertAlmostEqual(r["withholding_window_base"], 6.0)
        # One broker account: the latest payment's own rate, as before.
        rows = [_div(d) for d in _MONTHS] + [_div("2026-03-01", 0, 1.5,
                                                  "TAX")]
        self.assertAlmostEqual(_estimate(rows)["withholding_rate"], 0.15)

    def test_a30_window_past_the_calendar_is_an_input_error(self):
        """A30: Dividend estimate accepts a date/window combination
        outside the calendar range. `--date 9999-09-01 --months 120`
        raised "year 10009 is out of range" from the arithmetic; it is
        refused as an input error, exit 2, before any estimate."""
        msg = DE.horizon_problem("9999-09-01", 120, 2026)
        self.assertIn("reaches past 9999-12-31", msg or "")
        self.assertIn("--date 9999-09-01", msg)
        self.assertIn("120-month", msg)
        self.assertIn("tax year 9999",
                      DE.horizon_problem("2026-01-01", 12, 9999) or "")
        self.assertIn("look-back", DE.horizon_problem("0001-03-01", 1, 1)
                      or "")
        self.assertIsNone(DE.horizon_problem("2026-03-02", 120, 2026))
        with self.assertRaises(ValueError) as cm:
            _estimate([_div("2026-01-01")], as_of="9999-09-01", months=120)
        self.assertIn("reaches past 9999-12-31", str(cm.exception))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            (root / "work").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2026\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [_div("2026-01-01")[1]]}))
            (root / "work" / "margin_raw_gains.json").write_text(json.dumps(
                {"inventory": [dict(symbol=SYM, qty=10, total_cost=100,
                                    currency="USD")]}))
            env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
                       TAXJSON_WIDTH="0",
                       PYTHONPATH=str(REPO / "src") + os.pathsep
                       + os.environ.get("PYTHONPATH", ""))
            r = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_run",
                                   ["-C", str(root), "div-estimate",
                                    "--date", "9999-09-01", "--months",
                                    "120", "--json"]),
                capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL, check=False)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("--date 9999-09-01 with a 120-month window "
                          "reaches past 9999-12-31", r.stderr)
            self.assertIn("shorter window", r.stderr)
            # The same date with a window that fits is estimated.
            r = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_run",
                                   ["-C", str(root), "div-estimate",
                                    "--date", "9999-09-01", "--months",
                                    "1", "--json"]),
                capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["months"], 1)

    def test_a50_short_position_expense_is_not_a_reversal(self):
        """A50: Short-position dividend expenses cancel earlier
        long-position dividend history. A -10 row on -10 shares does
        not erase the +10 earned while long: a short's expense when the
        account's trades put it short (or it has none, the row's own
        sign deciding, with a note); a reversal when they put it long
        (IB back-computes a negative count on every negative row)."""
        long_pay = _div("2026-01-01", 10, 10)
        expense = _div("2026-02-01", -10, -10)
        # No trade of the symbol in the books: the row's sign decides.
        pays = DE.payments([long_pay, expense])
        self.assertEqual([(p.pay_date, p.gross) for p in pays[SYM]],
                         [("2026-01-01", 10.0)])
        notes = pays.notes[("margin", SYM)]
        self.assertEqual(len(notes), 1)
        self.assertIn("short position's dividend expense", notes[0])
        self.assertIn("2026-02-01", notes[0])
        r = _estimate([long_pay, expense], as_of="2026-02-15")
        self.assertEqual(r["flags"][0], notes[0])
        # The books show the short: the expense is left out, no note.
        trades = [_tx("2025-12-01", 10), _tx("2026-01-10", -20)]
        pays = DE.payments(trades + [long_pay, expense])
        self.assertEqual([(p.pay_date, p.gross) for p in pays[SYM]],
                         [("2026-01-01", 10.0)])
        self.assertEqual(pays.notes, {})
        # The position was short on the record date and covered before
        # the pay date: still the short's expense.
        trades = [_tx("2026-01-10", -10), _tx("2026-01-25", 10)]
        dated = ("margin", dict(expense[1], record_date="2026-01-20"))
        pays = DE.payments(trades + [long_pay, dated])
        self.assertEqual([p.pay_date for p in pays[SYM]], ["2026-01-01"])
        # Long by the books: a reversal (IB's back-computed negative
        # count), netting the earlier payment as before.
        pays = DE.payments([_tx("2025-12-01", 10), long_pay, expense])
        self.assertNotIn(SYM, pays)
        self.assertEqual(pays.notes, {})
        # A reversal on a positive count nets when the books hold no
        # trade of the symbol (short by the books, it is the short's
        # expense: 2026-10-25 audit planning-us#4).
        pays = DE.payments([long_pay, _div("2026-02-01", 10, -10)])
        self.assertNotIn(SYM, pays)

    def test_b15_partial_lending_is_one_payment_on_all_the_shares(self):
        """B15: Partial stock lending doubles projected dividends per
        share. A dividend on 5 shares and a payment in lieu on the 5
        lent ones, 5 each, is 1/share on 10, not 2/share on 5."""
        rows = [("margin", dict(date="2026-01-05", account="margin",
                                symbol=SYM, quantity=5, net_amount=5,
                                action=act, currency="USD"))
                for act in ("DIVIDEND", "DIVIDEND_IN_LIEU")]
        p = DE.payments(rows)[SYM][0]
        self.assertEqual((p.qty, p.per_share, p.gross), (10.0, 1.0, 10.0))
        # Two DIVIDEND rows of one date (a special beside the regular
        # payment) are still two components on the same shares.
        rows = [_div("2026-01-05", 5, 5), _div("2026-01-05", 5, 2.5)]
        p = DE.payments(rows)[SYM][0]
        self.assertEqual((p.qty, p.per_share), (5.0, 1.5))


if __name__ == "__main__":
    unittest.main()
