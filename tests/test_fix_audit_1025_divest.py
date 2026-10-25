"""2026-10-25 audit, group "divest" (`tjs div-estimate`,
lib/div_estimate): planning-us#2, #3, #4 (the 2026-10-24 fixes A09 and
A50 left incomplete). Synthetic payers and projects only. Planning
numbers only (CA-PLAN-06 / US-PLAN-07): no filed figure is involved."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import div_estimate as DE
from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


def _row(sym, d, act, q, amount=0.0, cur="CAD", acct="margin", **kw):
    return (acct, dict(date=d, account=acct, symbol=sym, quantity=q,
                       net_amount=amount, action=act, currency=cur, **kw))


def _estimate(rows, sym, qty, as_of, months=12, cur="CAD"):
    doc = DE.estimate([dict(account="margin", symbol=sym, qty=qty,
                            group="taxable")], DE.payments(rows),
                      as_of=as_of, months=months, tax_year=2024,
                      base=cur, fx=lambda c: 1)
    return doc["rows"][0]


_QUARTERS = ["2023-12-15", "2024-03-15", "2024-06-14", "2024-09-13"]


def _split_later(sym="ZZDB.TO"):
    """100 shares paid 0.50 a quarter, then a 2-for-1 split: 200 held."""
    return ([_row(sym, "2023-06-01", "BUYSELL", 100, 2000)]
            + [_row(sym, d, "DIVIDEND", 100, 50) for d in _QUARTERS]
            + [_row(sym, "2024-10-01", "SPLIT", 2, symbol_new=sym)])


class TestDivEstimateAudit1025(unittest.TestCase):

    def test_planning_us_2_date_before_a_later_split_reads_todays_shares(
            self):
        """planning-us#2: div-estimate --date before a later SPLIT
        doubles the forecast (A09 incomplete). The shares are the
        books' current holding (200, after the 2-for-1 split), so the
        history is read in today's shares at any --date: 0.25/share,
        NEXT 0.25 x 200 = 50.00, 12 months 4 x 50 = 200.00 — not
        0.50 x 200 = 100.00 and 400.00 as of a day before the split."""
        rows = _split_later()
        for as_of in ("2024-09-20", "2024-10-15"):
            r = _estimate(rows, "ZZDB.TO", 200, as_of)
            self.assertAlmostEqual(r["last_per_share"], 0.25, msg=as_of)
            self.assertEqual(r["next_pay_date"], "2024-12-13")
            self.assertAlmostEqual(r["next_amount"], 50.0, msg=as_of)
            self.assertAlmostEqual(r["window_amount"], 200.0, msg=as_of)
            self.assertAlmostEqual(r["rest_of_year_amount"], 50.0)
            self.assertAlmostEqual(r["ttm_per_share"], 1.0, msg=as_of)
        # Payments after --date are still unknown on it (audit R09): a
        # raised dividend posted after the date does not count.
        raised = rows + [_row("ZZDB.TO", "2024-12-13", "DIVIDEND", 200, 60)]
        r = _estimate(raised, "ZZDB.TO", 200, "2024-09-20")
        self.assertAlmostEqual(r["last_per_share"], 0.25)
        # One split booked by two accounts' books a day apart is one
        # event (split_copies, CA-CORP-12), not 2 x 2.
        twice = rows + [_row("ZZDB.TO", "2024-10-02", "SPLIT", 2,
                             acct="rrsp", symbol_new="ZZDB.TO")]
        r = _estimate(twice, "ZZDB.TO", 200, "2024-09-20")
        self.assertAlmostEqual(r["last_per_share"], 0.25)
        # The command: --date before the split gives the same figures
        # as after it (work/ written directly, as the run leaves it).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            (root / "work").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2024\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [t for _a, t in rows]}))
            (root / "work" / "margin_raw_gains.json").write_text(json.dumps(
                {"inventory": [dict(symbol="ZZDB.TO", qty=200,
                                    total_cost=400, currency="CAD")]}))
            env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
                       TAXJSON_WIDTH="0",
                       PYTHONPATH=str(REPO / "src") + os.pathsep
                       + os.environ.get("PYTHONPATH", ""))
            got = {}
            for as_of in ("2024-09-20", "2024-10-15"):
                r = subprocess.run(
                    python_module_argv("taxjson.bin.taxjson_run",
                                       ["-C", str(root), "div-estimate",
                                        "margin", "--date", as_of,
                                        "--json"]),
                    capture_output=True, text=True, env=env,
                    stdin=subprocess.DEVNULL, check=False)
                self.assertEqual(r.returncode, 0, r.stderr)
                row = json.loads(r.stdout)["rows"][0]
                got[as_of] = (row["shares"], row["last_per_share"],
                              row["next_amount"], row["window_amount"])
            self.assertEqual(got["2024-09-20"], (200, 0.25, 50.0, 200.0))
            self.assertEqual(got["2024-09-20"], got["2024-10-15"])

    def test_planning_us_3_reversal_on_a_long_position_after_a_split(self):
        """planning-us#3: div-estimate reads a reversal on a long
        position as a short's dividend expense after a split (A50
        incomplete). 100 shares, a 2-for-1 split (200), 150 sold: 50
        held, never short. The -5.00 reversal (IB's negative count)
        nets against the 10.00 payment: 5.00 on 50 = 0.10/share, NEXT
        5.00, 12 months 20.00 — the same as the control bought as 200
        with no split (the raw quantities read 100 - 150 = short 50)."""
        def book(sym, split):
            rows = [_row(sym, "2023-06-01", "BUYSELL", 100 if split else 200,
                         1000)]
            n = 100 if split else 200
            rows += [_row(sym, d, "DIVIDEND", n, 20)
                     for d in ("2023-12-15", "2024-03-15")]
            if split:
                rows.append(_row(sym, "2024-04-01", "SPLIT", 2,
                                 symbol_new=sym))
            rows += [_row(sym, "2024-05-01", "BUYSELL", -150, 900),
                     _row(sym, "2024-06-14", "DIVIDEND", 50, 5),
                     _row(sym, "2024-09-13", "DIVIDEND", 50, 10),
                     _row(sym, "2024-09-16", "DIVIDEND", -50, -5)]
            return rows
        for sym, split in (("ZZDG.TO", True), ("ZZDH.TO", False)):
            rows = book(sym, split)
            pays = DE.payments(rows)
            self.assertEqual(pays.notes, {})
            self.assertEqual([round(p.per_share, 6) for p in pays[sym]],
                             [0.1, 0.1, 0.1, 0.1], sym)
            r = _estimate(rows, sym, 50, "2024-10-15")
            self.assertAlmostEqual(r["last_per_share"], 0.1, msg=sym)
            self.assertAlmostEqual(r["next_amount"], 5.0, msg=sym)
            self.assertAlmostEqual(r["window_amount"], 20.0, msg=sym)
        # A split on the sale's day is effective at the open: 100 x 2
        # - 150 is long 50, so the reversal still nets.
        rows = [_row("ZZDG.TO", "2023-06-01", "BUYSELL", 100, 1000),
                _row("ZZDG.TO", "2024-09-13", "DIVIDEND", 50, 10),
                _row("ZZDG.TO", "2024-04-01", "BUYSELL", -150, 900,
                     time="09:30:00"),
                _row("ZZDG.TO", "2024-04-01", "SPLIT", 2, time="20:25:00",
                     symbol_new="ZZDG.TO"),
                _row("ZZDG.TO", "2024-09-16", "DIVIDEND", -50, -5)]
        pays = DE.payments(rows)
        self.assertEqual([p.gross for p in pays["ZZDG.TO"]], [5.0])
        # The split's ratio works the other way too: 100 x 2 - 300 is
        # short 100, and that dividend charge is an expense.
        rows = [_row("ZZDG.TO", "2023-06-01", "BUYSELL", 100, 1000),
                _row("ZZDG.TO", "2024-03-15", "DIVIDEND", 100, 20),
                _row("ZZDG.TO", "2024-04-01", "SPLIT", 2,
                     symbol_new="ZZDG.TO"),
                _row("ZZDG.TO", "2024-05-01", "BUYSELL", -300, 900),
                _row("ZZDG.TO", "2024-06-14", "DIVIDEND", -100, -10)]
        pays = DE.payments(rows)
        self.assertEqual([p.gross for p in pays["ZZDG.TO"]], [20.0])
        self.assertEqual(pays.notes, {})
        # A rename carries the shares to the new symbol: 100 renamed
        # 2-for-1 into ZZDN.TO, 150 of it sold, 50 long; a reversal of
        # ZZDN.TO's payment nets, no note (the account's trades of it
        # are the rename and the sale).
        rows = [_row("ZZDM.TO", "2023-06-01", "BUYSELL", 100, 1000),
                _row("ZZDM.TO", "2024-04-01", "SPLIT", 2,
                     symbol_new="ZZDN.TO"),
                _row("ZZDN.TO", "2024-05-01", "BUYSELL", -150, 900),
                _row("ZZDN.TO", "2024-06-14", "DIVIDEND", 50, 5),
                _row("ZZDN.TO", "2024-09-13", "DIVIDEND", 50, 10),
                _row("ZZDN.TO", "2024-09-16", "DIVIDEND", -50, -5)]
        pays = DE.payments(rows)
        self.assertEqual([p.gross for p in pays["ZZDN.TO"]], [5.0, 5.0])
        self.assertEqual(pays.notes, {})

    def test_planning_us_4_short_charge_on_a_positive_count(self):
        """planning-us#4: div-estimate reads a short's dividend charge
        as a reversal when the row's share count is positive (Questrade
        'ON N SHS', .tt), erasing a long payment (A50 incomplete). Long
        100, sold 200 (short 100), a -25.00 charge 'ON 100 SHS' while
        short, then bought 200 (long 100): the charge is an expense, the
        three long payments stay — quarterly, NEXT 2024-11-15 at 0.25 x
        100 = 25.00, 12 months 100.00 (as with a negative count)."""
        def book(sym, q, **kw):
            return [_row(sym, "2023-06-01", "BUYSELL", 100, 1000, "USD"),
                    _row(sym, "2023-11-15", "DIVIDEND", 100, 25, "USD"),
                    _row(sym, "2024-02-15", "DIVIDEND", 100, 25, "USD"),
                    _row(sym, "2024-04-01", "BUYSELL", -200, 2400,
                         "USD"),
                    _row(sym, "2024-05-15", "DIVIDEND", q, -25, "USD",
                         **kw),
                    _row(sym, "2024-06-03", "BUYSELL", 200, 2200,
                         "USD"),
                    _row(sym, "2024-08-15", "DIVIDEND", 100, 25, "USD")]
        # .tt with a positive count; the Questrade row (its record date
        # read from "REC 05/01/24"); the negative-count control.
        for sym, q, kw in (("ZZDJ.US", 100, {}),
                           ("ZZQU.US", 100, dict(record_date="2024-05-01")),
                           ("ZZDK.US", -100, {})):
            rows = book(sym, q, **kw)
            pays = DE.payments(rows)
            self.assertEqual([p.pay_date for p in pays[sym]],
                             ["2023-11-15", "2024-02-15", "2024-08-15"], sym)
            self.assertEqual(pays.notes, {})
            r = _estimate(rows, sym, 100, "2024-10-15", cur="USD")
            self.assertEqual((r["frequency"], r["next_pay_date"]),
                             ("quarterly", "2024-11-15"), sym)
            self.assertAlmostEqual(r["next_amount"], 25.0, msg=sym)
            self.assertAlmostEqual(r["window_amount"], 100.0, msg=sym)
        # A reversal on a positive count while long still nets.
        rows = book("ZZDJ.US", 100)
        rows[3] = _row("ZZDJ.US", "2024-04-01", "BUYSELL", -50, 600, "USD")
        pays = DE.payments(rows)
        self.assertEqual([(p.pay_date, p.gross) for p in pays["ZZDJ.US"]],
                         [("2023-11-15", 25.0), ("2024-08-15", 25.0)])
        # The record date decides, not the pay date: a reversal of the
        # February payment (record 2024-02-01, long) posted while short
        # nets against it.
        rows = book("ZZDJ.US", 100, record_date="2024-02-01")
        pays = DE.payments(rows)
        self.assertEqual([p.pay_date for p in pays["ZZDJ.US"]],
                         ["2023-11-15", "2024-08-15"])
        # No trade of the symbol in the books: a positive count is a
        # reversal, as before (a negative one the short's expense, with
        # a note: audit A50).
        rows = [r for r in book("ZZDJ.US", 100)
                if r[1]["action"] != "BUYSELL"]
        pays = DE.payments(rows)
        self.assertEqual([p.pay_date for p in pays["ZZDJ.US"]],
                         ["2023-11-15", "2024-08-15"])
        self.assertEqual(pays.notes, {})
        # Units bought before the data (a .tt OPENING cost=unknown
        # line) are held: the command opens them as the gains stage
        # does, so selling 50 of 100 is long 50, not short 50, and the
        # reversal nets (5.00 on 50: 0.10/share, NEXT 5.00).
        rows = [_row("ZZMH.TO", "2023-12-15", "DIVIDEND", 100, 20),
                _row("ZZMH.TO", "2024-03-15", "DIVIDEND", 100, 20),
                _row("ZZMH.TO", "2024-05-01", "BUYSELL", -50, 300),
                _row("ZZMH.TO", "2024-06-14", "DIVIDEND", 50, 10),
                _row("ZZMH.TO", "2024-09-13", "DIVIDEND", 50, 10),
                _row("ZZMH.TO", "2024-09-16", "DIVIDEND", 50, -5)]
        # The rows on disk alone read the sale as a short (no reversal):
        self.assertEqual([p.gross for p in DE.payments(rows)["ZZMH.TO"]],
                         [20.0, 20.0, 10.0, 10.0])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            (root / "work").mkdir(parents=True)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "missing_history.tt").write_text(
                "OPENING 2023-01-01 ZZMH.TO 100 cost=unknown\n")
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2024\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = ["USD"]\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [t for _a, t in rows]}))
            (root / "work" / "margin_raw_gains.json").write_text(json.dumps(
                {"inventory": [dict(symbol="ZZMH.TO", qty=50,
                                    total_cost=0, currency="CAD")]}))
            env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
                       TAXJSON_WIDTH="0",
                       PYTHONPATH=str(REPO / "src") + os.pathsep
                       + os.environ.get("PYTHONPATH", ""))
            r = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_run",
                                   ["-C", str(root), "div-estimate",
                                    "--date", "2024-10-15", "--json"]),
                capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            row = json.loads(r.stdout)["rows"][0]
            self.assertEqual((row["last_per_share"], row["next_amount"],
                              row["window_amount"], row["flags"]),
                             (0.1, 5.0, 20.0, []))


if __name__ == "__main__":
    unittest.main()
