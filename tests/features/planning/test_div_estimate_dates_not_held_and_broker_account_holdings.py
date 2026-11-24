"""Backlogs TJ-5050 and TJ-5051 (older TJ-4976 (c), (g)): what the books
say of the shares held on a payer's date.

TJ-5050: a payer whose schedule the books settle (four quarterly
payments) and whose shares were sold and bought back read as stopped,
"2 expected payments not in the books", on the quarterly dates of the
months it was not held. The assumed quarterly schedule already excused
such dates; a settled one does now too (lib/div_estimate `_not_held`),
named in a flag that says what was read, each date read on the payer's
own grid (the lead, the tolerance and the grace of its period: a monthly
payer's are a month's), a move between accounts under way on the date
held. Only where the walk of the books' trades can say so
(`_walk_agrees`): it ends at the shares held now, and shows them held
before each payment. Shares that came by a row it does not walk (a
spin-off) read as base read them.

TJ-5051: a combined account's broker accounts were netted before the
shares held on a date were read: one broker account short the symbol
(99900002, its sale marked as a short by the broker) erased another's
long (99900001), so a quarterly date with the shares held and no payment
read as not held, and a single payment was projected quarterly on
assumption (four payments, 200.00) instead of the pro rata reading
(50.00). Each broker account's balance counts on its own now, through a
rename too; a move between them still counts as held. A sale with no
short marker that puts a broker account short (shares that left under
another broker account id than they came in) leaves the account's net
walk in place, as before.

The walk is checked against the shares every account holds now, whatever
account `tjs div-estimate` is asked for: a row reads the same filtered or
not (the review's div_acct).

Synthetic books only (made-up tickers, 999000xx broker accounts)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.dispatch import python_module_argv
from _support.view_div_estimate import _div, _est, _pos, _trade
import _paths

A, B = "99900001", "99900002"
# The broker's marker of a short sale (IB's Code O on a sale: lib/missing_history
# `broker_short_marker`).
MARKED = {"open_close": "O"}


def _row(res, sym):
    return next(r for r in res["rows"] if r["symbol"] == sym)


class SettledPayerDatesNotHeld(unittest.TestCase):

    def _books(self, *, sold=True):
        rows = [_trade("2024-01-02", "QZRX.TO", 100)]
        rows += [("margin", _div(d, "QZRX.TO", 100, 0.5))
                 for d in ("2024-03-15", "2024-06-14", "2024-09-13", "2024-12-13")]
        if sold:
            rows += [_trade("2024-12-20", "QZRX.TO", -100),
                     _trade("2025-06-20", "QZRX.TO", 100)]
        return rows + [_trade("2025-07-15", "QZZZ.TO", 1)]  # the books reach July

    @rule(R.CA_PLAN_06)
    def test_sold_and_bought_back_is_not_a_stopped_payer(self):
        # The entry's repro: "stopped", 0 projected.
        row = _row(_est(self._books(), [_pos("QZRX.TO", 100)], as_of="2025-07-20",
                        year=2025), "QZRX.TO")
        self.assertEqual((row["frequency"], row["frequency_assumed"], row["status"]),
                         ("quarterly", False, "ok"))
        self.assertAlmostEqual(row["window_amount"], 200.0, places=6)
        self.assertIn("2 quarterly date(s) not counted as missed payments: the books "
                      "show no shares held through the 45 days before each "
                      "(2025-03-13, 2025-06-13)", row["flags"])
        self.assertNotIn("2 expected payments not in the books", row["flags"])

    @rule(R.CA_PLAN_06)
    def test_a_flag_never_says_no_payment_was_due(self):
        # Bought back May 12: held 32 days before the June date, not the
        # 45 of the lead. The flag says what was read, not that nothing
        # was due (the record date may have been after May 12).
        rows = [r for r in self._books() if r[1].get("date") != "2025-06-20"]
        rows.append(_trade("2025-05-12", "QZRX.TO", 100))
        row = _row(_est(rows, [_pos("QZRX.TO", 100)], as_of="2025-07-20", year=2025),
                   "QZRX.TO")
        self.assertIn("2 quarterly date(s) not counted as missed payments: the books "
                      "show no shares held through the 45 days before each "
                      "(2025-03-13, 2025-06-13)", row["flags"])
        self.assertFalse(any("was due" in f for f in row["flags"]), row["flags"])

    @rule(R.CA_PLAN_06)
    def test_shares_the_walk_does_not_see_come_in_keep_the_reading(self):
        # D1: an old round trip, then the 100 shares arrive by a spin-off
        # row (no trade row: the walk ends at 0, not the 100 held, and
        # shows none held when 2024's payments were made). Nothing it
        # reads as not held is excused: stopped, as before.
        rows = [_trade("2023-02-01", "QZRX.TO", 10), _trade("2023-03-01", "QZRX.TO", -10),
                ("margin", {"action": "SPINOFF", "date": "2023-12-01", "symbol": "QZRX.TO",
                            "quantity": 100, "account": "margin"})]
        rows += [("margin", _div(d, "QZRX.TO", 100, 0.5))
                 for d in ("2024-03-15", "2024-06-14", "2024-09-13", "2024-12-13")]
        rows += [_trade("2025-07-15", "QZZZ.TO", 1)]
        row = _row(_est(rows, [_pos("QZRX.TO", 100)], as_of="2025-07-20", year=2025),
                   "QZRX.TO")
        self.assertEqual((row["status"], row["window_amount"]), ("stopped", 0))
        self.assertIn("2 expected payments not in the books", row["flags"])
        self.assertFalse(any("not counted" in f for f in row["flags"]), row["flags"])

    @rule(R.CA_PLAN_06)
    def test_a_walk_that_ends_elsewhere_than_the_holding_keeps_the_reading(self):
        # The walk ends at 100 and the holding is 60 (a row it does not
        # walk took 40): stopped, as before.
        row = _row(_est(self._books(), [_pos("QZRX.TO", 60)], as_of="2025-07-20",
                        year=2025), "QZRX.TO")
        self.assertEqual(row["status"], "stopped")
        self.assertFalse(any("not counted" in f for f in row["flags"]), row["flags"])

    @rule(R.CA_PLAN_06)
    def test_a_move_between_accounts_under_way_on_a_date_is_held(self):
        # Out of 99900001 on January 31, into 99900002 on February 4: the
        # February 2 date fell in a move (TRANSIT_DAYS), the shares held.
        # Nothing paid on it or after: stopped.
        rows = [_trade("2024-01-02", "QZRX.TO", 100, source_account=A)]
        rows += [("margin", _div(d, "QZRX.TO", 100, 0.5, source_account=A))
                 for d in ("2024-02-02", "2024-05-03", "2024-08-02", "2024-11-01")]
        rows += [_trade("2025-01-31", "QZRX.TO", -100, source_account=A),
                 _trade("2025-02-04", "QZRX.TO", 100, source_account=B),
                 _trade("2025-07-15", "QZZZ.TO", 1, source_account=A)]
        row = _row(_est(rows, [_pos("QZRX.TO", 100)], as_of="2025-07-20", year=2025),
                   "QZRX.TO")
        self.assertEqual(row["status"], "stopped")
        self.assertIn("2 expected payments not in the books", row["flags"])
        self.assertFalse(any("not counted" in f for f in row["flags"]), row["flags"])

    @rule(R.CA_PLAN_06)
    def test_held_through_with_no_payment_is_still_stopped(self):
        # Control: the shares held on those dates, nothing paid.
        row = _row(_est(self._books(sold=False), [_pos("QZRX.TO", 100)],
                        as_of="2025-07-20", year=2025), "QZRX.TO")
        self.assertEqual(row["status"], "stopped")
        self.assertIn("2 expected payments not in the books", row["flags"])
        self.assertFalse(any("not held" in f for f in row["flags"]))

    def _monthly(self, rebought):
        """Monthly, sold in June, bought back on `rebought`, read on
        2024-11-20 (the November date not yet past its 7 days' grace)."""
        rows = [_trade("2023-12-01", "QZRM.TO", 100)]
        rows += [("margin", _div(f"2024-0{m}-15", "QZRM.TO", 100, 0.1))
                 for m in range(1, 7)]
        rows += [_trade("2024-06-20", "QZRM.TO", -100),
                 _trade(rebought, "QZRM.TO", 100),
                 _trade("2024-11-19", "QZZZ.TO", 1)]
        return _row(_est(rows, [_pos("QZRM.TO", 100)], as_of="2024-11-20", year=2024),
                    "QZRM.TO")

    @rule(R.CA_PLAN_06)
    def test_a_monthly_payer_reads_its_dates_on_its_own_period(self):
        # Bought back in October: July to October not held.
        row = self._monthly("2024-10-20")
        self.assertEqual((row["frequency"], row["status"]), ("monthly", "ok"))
        self.assertIn("4 monthly date(s) not counted as missed payments: the books "
                      "show no shares held through the 15 days before each "
                      "(2024-07-15, 2024-08-15, 2024-09-15 ...)", row["flags"])

    @rule(R.CA_PLAN_06)
    def test_a_monthly_payer_held_half_a_month_before_a_date_was_entitled(self):
        # Bought back on September 25: held from half a month before the
        # October date (a quarter's lead would have read it not held),
        # and none paid on it.
        row = self._monthly("2024-09-25")
        self.assertEqual(row["status"], "late")
        self.assertIn("3 monthly date(s) not counted as missed payments: the books "
                      "show no shares held through the 15 days before each "
                      "(2024-07-15, 2024-08-15, 2024-09-15)", row["flags"])
        self.assertIn("1 expected payment not in the books", row["flags"])


class BrokerAccountsHeldOnTheirOwn(unittest.TestCase):

    def _books(self, *, short=True, sym="QZRY.TO", marked=MARKED):
        rows = [_trade("2026-01-05", "QZRY.TO", 100, source_account=A),
                ("margin", _div("2026-02-10", "QZRY.TO", 100, 0.5, source_account=A)),
                _trade("2026-06-30", "QZZZ.TO", 1, source_account=A)]
        if short:
            rows += [_trade("2026-04-01", sym, -100, source_account=B, **marked),
                     _trade("2026-06-01", sym, 100, source_account=B)]
        return rows

    @rule(R.CA_PLAN_06)
    def test_one_broker_accounts_short_does_not_erase_anothers_long(self):
        # The entry's repro: quarterly, assumed, 200.00 projected.
        row = _row(_est(self._books(), [_pos("QZRY.TO", 100)]), "QZRY.TO")
        self.assertFalse(row["frequency_assumed"])
        self.assertEqual(row["status"], "irregular")
        self.assertAlmostEqual(row["window_amount"], 50.0, places=6)
        self.assertIn("single payment", row["flags"])

    @rule(R.CA_PLAN_06)
    def test_with_no_short_the_reading_is_the_same(self):
        # Control: B's short removed (as before on every tree).
        row = _row(_est(self._books(short=False), [_pos("QZRY.TO", 100)]), "QZRY.TO")
        self.assertFalse(row["frequency_assumed"])
        self.assertAlmostEqual(row["window_amount"], 50.0, places=6)

    @rule(R.CA_PLAN_06)
    def test_through_a_rename_too(self):
        # A's shares renamed QZRA.TO -> QZRB.TO; B short the new symbol.
        rows = [_trade("2026-01-05", "QZRA.TO", 100, source_account=A),
                ("margin", _div("2026-02-10", "QZRA.TO", 100, 0.5, source_account=A)),
                ("margin", {"action": "SPLIT", "date": "2026-03-01", "symbol": "QZRA.TO",
                            "symbol_new": "QZRB.TO", "quantity": 1.0,
                            "account": "margin"}),
                _trade("2026-04-01", "QZRB.TO", -100, source_account=B, **MARKED),
                _trade("2026-06-01", "QZRB.TO", 100, source_account=B),
                _trade("2026-06-30", "QZZZ.TO", 1, source_account=A)]
        row = _row(_est(rows, [_pos("QZRB.TO", 100)]), "QZRB.TO")
        self.assertFalse(row["frequency_assumed"])
        self.assertAlmostEqual(row["window_amount"], 50.0, places=6)

    @rule(R.CA_PLAN_06)
    def test_an_rbc_short_marker_counts_too(self):
        row = _row(_est(self._books(marked={"description": "QZRY CORP SHORT. "
                                            "UNSOLICITED"}),
                        [_pos("QZRY.TO", 100)]), "QZRY.TO")
        self.assertFalse(row["frequency_assumed"])
        self.assertAlmostEqual(row["window_amount"], 50.0, places=6)

    @rule(R.CA_PLAN_06)
    def test_shares_sold_under_another_broker_account_id_read_as_before(self):
        # D2: bought under 99900001, sold and bought back under 99900002
        # (an account renumbered), no short marker: 99900002's own walk
        # would read short, 99900001's long through May. The account's net
        # walk stands, as before: not held on May 10, assumed quarterly.
        rows = [_trade("2026-01-05", "QZRY.TO", 100, source_account=A),
                ("margin", _div("2026-02-10", "QZRY.TO", 100, 0.5, source_account=A)),
                _trade("2026-03-01", "QZRY.TO", -100, source_account=B),
                _trade("2026-06-20", "QZRY.TO", 100, source_account=B),
                _trade("2026-06-30", "QZZZ.TO", 1, source_account=A)]
        row = _row(_est(rows, [_pos("QZRY.TO", 100)]), "QZRY.TO")
        self.assertEqual((row["frequency"], row["frequency_assumed"], row["status"]),
                         ("quarterly", True, "ok"))
        self.assertAlmostEqual(row["window_amount"], 200.0, places=6)
        # The entry's case unmarked reads so too.
        row = _row(_est(self._books(marked={}), [_pos("QZRY.TO", 100)]), "QZRY.TO")
        self.assertTrue(row["frequency_assumed"])

    @rule(R.CA_PLAN_06)
    def test_a_move_between_broker_accounts_still_counts_as_held(self):
        # Control: A's shares leave on April 20 and reach B on the 23rd;
        # held on May 10 with no payment: the assumption is contradicted.
        rows = [_trade("2026-01-05", "QZRY.TO", 100, source_account=A),
                ("margin", _div("2026-02-10", "QZRY.TO", 100, 0.5, source_account=A)),
                ("margin", {"action": "TRANSFER", "date": "2026-04-20",
                            "symbol": "QZRY.TO", "quantity": -100, "currency": "CAD",
                            "account": "margin", "source_account": A}),
                ("margin", {"action": "TRANSFER", "date": "2026-04-23",
                            "symbol": "QZRY.TO", "quantity": 100, "currency": "CAD",
                            "account": "margin", "source_account": B}),
                _trade("2026-06-30", "QZZZ.TO", 1, source_account=A)]
        row = _row(_est(rows, [_pos("QZRY.TO", 100)]), "QZRY.TO")
        self.assertFalse(row["frequency_assumed"])
        self.assertAlmostEqual(row["window_amount"], 50.0, places=6)


class AnAccountFilterReadsTheSame(unittest.TestCase):
    """`tjs div-estimate margin` and `tjs div-estimate`: margin's row the
    same (the walk's check reads every account's shares held now)."""

    Q = ("2024-03-15", "2024-06-14", "2024-09-13", "2024-12-13")

    def _project(self, root: Path) -> None:
        (root / "work").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.tfsa]\ntype = "sheltered"\n', encoding="utf-8")
        for acct, q in (("margin", 100), ("tfsa", 50)):
            tx = [_trade("2024-01-02", "QZRX.TO", q, acct=acct)[1]]
            tx += [_div(d, "QZRX.TO", q, 0.5, acct=acct) for d in self.Q]
            tx += [_trade(d, s, n, acct=acct)[1] for d, s, n in (
                ("2024-12-20", "QZRX.TO", -q), ("2025-06-20", "QZRX.TO", q),
                ("2025-07-15", "QZZZ.TO", 1))]
            (root / "work" / f"{acct}_raw.json").write_text(
                json.dumps({"transactions": tx}), encoding="utf-8")
            (root / "work" / f"{acct}_raw_gains.json").write_text(json.dumps(
                {"inventory": [{"symbol": "QZRX.TO", "qty": q, "total_cost": 50.0 * q,
                                "currency": "CAD"}]}), encoding="utf-8")

    def _margin_row(self, root: Path, *account: str):
        env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1", TAXJSON_WIDTH="0",
                   PYTHONPATH=str(_paths.REPO / "src") + os.pathsep
                   + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run",
                               ["-C", str(root), "div-estimate", *account,
                                "--date", "2025-07-20", "--json"]),
            capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
            check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [x for x in json.loads(r.stdout)["rows"] if x["account"] == "margin"]
        self.assertEqual(len(rows), 1, r.stdout)
        return rows[0]

    @rule(R.CA_PLAN_06)
    def test_margin_filtered_and_not_reads_the_same(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            self._project(root)
            every, margin = self._margin_row(root), self._margin_row(root, "margin")
        for row in (every, margin):
            self.assertEqual(row["status"], "ok", row)
            self.assertIn("2 quarterly date(s) not counted as missed payments: the books "
                          "show no shares held through the 45 days before each "
                          "(2025-03-13, 2025-06-13)", row["flags"])
        self.assertEqual([(r["status"], r["window_amount"], r["flags"]) for r in (margin,)],
                         [(every["status"], every["window_amount"], every["flags"])])


if __name__ == "__main__":
    unittest.main()
