"""Audit 2026-10-25, group transfers: lib/transfer_in decides what a
taxable account's books take for shares that arrive by transfer, so a
move of your own read as an arrival from outside changes filed figures.

diff-engine#4  B50: an own move between two taxable accounts whose legs
               post more than 10 days apart booked the arrival as an
               outside purchase (at the broker's stated book value) and
               silently kept the shares in the sending account: the
               pooled ACB blended 200 units, the gain changed and
               nothing named the departure. Now an out leg and an in leg
               of the same security and quantity in two of your
               accounts, the in leg 11 to SLOW_MOVE_DAYS (45) days after
               the out leg, neither leg with another candidate, are one
               move of your own (transfer_in.slow_pairs): nothing is
               booked for the arrival, the run says it as Info naming
               both dates, and a US project carries the lots. Legs that
               are ambiguous (another candidate), partly paired, in one
               account or further apart stay an arrival and a departure
               of their own, and the arrival's ATTENTION line names the
               departure. A departure after the arrival is still never
               its source (2026-10-24 B50).

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import settings_for
from _style import CapturedWidth

from taxjson.lib import transfer_in as TI
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _leg(acct, d, q, sym="ZZQ.TO", **kw):
    """One transfer sidecar row as transfer_in.arrivals takes it."""
    return (acct, "questrade",
            dict(action="TRANSFER", symbol=sym, date=d, date_settle=d,
                 time="09:30:00", quantity=q, net_amount=0,
                 currency="CAD", account=acct, **kw))


def _arrivals(rows):
    slow = []
    return TI.arrivals(rows, slow=slow), slow


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


def _project(root: Path, country: str, in_date: str) -> Path:
    """Account qa buys 100 ZZQ at 2.00 and transfers them out on
    2024-03-01; account qb receives 100 (TRANSFER BOOK VALUE 250.00) on
    `in_date` and sells them at 3.00 on 2024-06-03. One move of your
    own: the sale's cost is the purchase's 200, its gain 100."""
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
    return root


def _filed(root: Path):
    """(cost, gain, open positions) of the project's tax year."""
    s = json.loads(_cli(root, "sum", "--json").stdout)
    accts = s["filing"]["accounts"]
    cost = sum(a.get("acb", a.get("cost", 0.0)) for a in accts)
    gain = s["filing"]["engine_gain_unrounded"]
    held = json.loads(_cli(root, "list", "--json").stdout)
    return round(cost, 2), round(gain, 2), held["totals"]["positions"]


@rule("CA-ACB-TRANSFER-BV")
class TestDiffEngine4SlowOwnMove(unittest.TestCase):
    """diff-engine#4: B50: an own move between two taxable accounts
    whose legs post more than 10 days apart now books the arrival as an
    outside purchase and silently keeps the shares in the sending
    account."""

    def test_diff_engine_4_legs_five_days_apart_still_cancel_in_the_window(self):
        found, slow = _arrivals([
            _leg("qa", "2024-03-01", -100),
            _leg("qb", "2024-03-06", 100, book_value=250)])
        self.assertEqual((found, slow), ([], []))

    def test_diff_engine_4_legs_fifteen_days_apart_are_one_move_of_your_own(self):
        found, slow = _arrivals([
            _leg("qa", "2024-03-01", -100),
            _leg("qb", "2024-03-16", 100, book_value=250)])
        self.assertEqual(found, [])
        self.assertEqual(TI.booked_rows(found), [])
        self.assertEqual([(m.out_account, m.out_date, m.in_account,
                           m.in_date, m.quantity, m.days) for m in slow],
                         [("qa", "2024-03-01", "qb", "2024-03-16", 100.0,
                           15)])
        self.assertEqual(slow[0].text(),
                         "ZZQ.TO 100 qa -> qb (out 2024-03-01, in "
                         "2024-03-16: 15 days apart, nothing else pairs)")
        # No stated book value: the same move, nothing kept out.
        found, slow = _arrivals([_leg("qa", "2024-03-01", -100),
                                 _leg("qb", "2024-03-16", 100)])
        self.assertEqual((found, len(slow)), ([], 1))

    def test_diff_engine_4_the_slow_window_ends_at_45_days(self):
        self.assertEqual(TI.SLOW_MOVE_DAYS, 45)
        for gap in (TI.OWN_MOVE_DAYS + 1, 30, TI.SLOW_MOVE_DAYS):
            found, slow = _arrivals([
                _leg("qa", "2024-03-01", -100),
                _leg("qb", _shift("2024-03-01", gap), 100, book_value=250)])
            self.assertEqual((found, len(slow)), ([], 1), gap)
        found, slow = _arrivals([
            _leg("qa", "2024-03-01", -100),
            _leg("qb", _shift("2024-03-01", TI.SLOW_MOVE_DAYS + 1), 100,
                 book_value=250)])
        self.assertEqual(slow, [])
        self.assertEqual([(a.account, a.status, a.departures)
                          for a in found], [("qb", "book_value", [])])

    def test_diff_engine_4_a_departure_after_the_arrival_is_never_its_source(self):
        # 2026-10-24 B50 kept: the in leg first, the out leg 15 days or
        # years later.
        for gap in (15, 2200):
            found, slow = _arrivals([
                _leg("qb", "2020-01-02", 100, book_value=250),
                _leg("qa", _shift("2020-01-02", gap), -100)])
            self.assertEqual(slow, [])
            self.assertEqual([(a.account, a.status, a.departures)
                              for a in found], [("qb", "book_value", [])])
            self.assertEqual(len(TI.booked_rows(found)), 1)

    def test_diff_engine_4_ambiguous_legs_pair_nothing_and_the_line_names_the_departures(self):
        # Two departures that could be one arrival's source.
        found, slow = _arrivals([
            _leg("qa", "2024-03-01", -100),
            _leg("qc", "2024-03-03", -100),
            _leg("qb", "2024-03-20", 100, book_value=250)])
        self.assertEqual(slow, [])
        self.assertEqual([(a.account, a.status) for a in found],
                         [("qb", "book_value")])
        self.assertEqual(found[0].departures,
                         [("qa", "2024-03-01", 100.0),
                          ("qc", "2024-03-03", 100.0)])
        (line,) = TI.attention_lines(found, "canada")
        self.assertIn("It may be the other end of a transfer-out of yours "
                      "that nothing paired (100 ZZQ.TO (2024-03-20) after "
                      "100 out of qa (2024-03-01), 100 out of qc "
                      "(2024-03-03); more than one could pair, or the "
                      "quantities differ)", line)
        self.assertIn("the sending account still holds those shares, and "
                      "they are then held twice", line)
        # The run's one-line form says it too.
        from taxjson.lib.stage_msg import console_lines
        self.assertEqual(
            console_lines(f"warning: ATTENTION: {line}", width_=120,
                          concise=True),
            ["Warning: Transfer-in: qb: 1 booked at the broker's stated "
             "cost (ZZQ.TO): maybe a move of your own — `tjs transfers`"])
        # Two arrivals that could be one departure's other end.
        found, slow = _arrivals([
            _leg("qa", "2024-03-01", -100),
            _leg("qb", "2024-03-20", 100, book_value=250),
            _leg("qc", "2024-03-25", 100, book_value=260)])
        self.assertEqual(slow, [])
        self.assertEqual([(a.account, a.departures) for a in found],
                         [("qb", [("qa", "2024-03-01", 100.0)]),
                          ("qc", [("qa", "2024-03-01", 100.0)])])
        # An arrival back in the out leg's own account competes too.
        found, slow = _arrivals([
            _leg("qa", "2024-03-01", -100),
            _leg("qa", "2024-03-20", 100, book_value=250),
            _leg("qb", "2024-03-22", 100, book_value=250)])
        self.assertEqual(slow, [])
        self.assertEqual(sorted(a.account for a in found), ["qa", "qb"])

    def test_diff_engine_4_a_partly_paired_or_other_quantity_departure_is_named_not_paired(self):
        # 60 delivered within the window, 40 three weeks later: the out
        # leg's rest is no whole leg, so nothing is guessed.
        found, slow = _arrivals([
            _leg("qa", "2024-03-01", -100),
            _leg("qb", "2024-03-05", 60, book_value=150),
            _leg("qb", "2024-03-22", 40, book_value=100)])
        self.assertEqual(slow, [])
        self.assertEqual([(a.date, a.quantity, a.departures) for a in found],
                         [("2024-03-22", 40.0, [("qa", "2024-03-01", 40.0)])])
        found, slow = _arrivals([
            _leg("qa", "2024-03-01", -100),
            _leg("qb", "2024-03-20", 60)])
        self.assertEqual(slow, [])
        (line,) = TI.attention_lines(found, "canada")
        self.assertIn("NO cost in the books", line)
        self.assertIn("after 100 out of qa (2024-03-01)", line)
        self.assertIn("a purchase added for them would then hold them "
                      "twice", line)

    def test_diff_engine_4_out_and_back_in_one_account_is_named_not_paired(self):
        found, slow = _arrivals([
            _leg("qa", "2024-03-01", -100),
            _leg("qa", "2024-03-20", 100, book_value=250)])
        self.assertEqual(slow, [])
        self.assertEqual([(a.account, a.status, a.departures)
                          for a in found],
                         [("qa", "book_value", [("qa", "2024-03-01",
                                                 100.0)])])

    def test_diff_engine_4_an_in_kind_leg_never_pairs_as_a_slow_move(self):
        # The run marks the legs of every in-kind move (no_slow): qa's
        # transfer-out went to a registered account.
        found, slow = [], []
        found = TI.arrivals([_leg("qa", "2024-03-01", -100),
                             _leg("qb", "2024-03-16", 100, book_value=250)],
                            slow=slow, no_slow=lambda r: r[0] == "qa")
        self.assertEqual(slow, [])
        self.assertEqual([(a.account, a.status, a.departures)
                          for a in found], [("qb", "book_value", [])])

    def test_diff_engine_4_a_transfer_in_from_outside_is_booked_as_before(self):
        found, slow = _arrivals([_leg("qb", "2024-03-16", 100,
                                      book_value=250)])
        self.assertEqual(slow, [])
        self.assertEqual([(a.status, a.book_value, a.departures)
                          for a in found], [("book_value", 250.0, [])])
        (row,) = TI.booked_rows(found)
        self.assertEqual((row["date"], row["quantity"], row["net_amount"]),
                         ("2024-03-16", 100.0, 250.0))
        (line,) = TI.attention_lines(found, "canada")
        self.assertNotIn("transfer-out of yours", line)


class TestDiffEngine4Run(unittest.TestCase):
    """diff-engine#4 end to end: `tjs run` on two Questrade accounts,
    the legs 5 and 15 days apart, books the same cost and gain."""

    @rule("CA-ACB-TRANSFER-BV")
    def test_diff_engine_4_canada_run_keeps_the_acb_of_a_slow_move(self):
        with tempfile.TemporaryDirectory() as td:
            for gap in (5, 15):
                root = _project(Path(td) / f"p{gap}", "canada",
                                _shift("2024-03-01", gap))
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                # The purchase's 200, its gain 100; nothing left in qa.
                self.assertEqual(_filed(root), (200.0, 100.0, 0), gap)
                self.assertFalse((root / "work" /
                                  "qb_transfer_costs.json").exists())
                self.assertNotIn("booked at the ACB the broker states",
                                 r.stdout)
                j = json.loads(_cli(root, "transfers", "--json").stdout)
                self.assertEqual([t["arrival"] for t in j["transfers"]
                                  if t["quantity"] > 0], ["own move"])
                if gap == 5:
                    self.assertNotIn("Own-account move", r.stdout)
                    continue
                self.assertIn("Own-account move: ZZQ.TO 100 qa -> qb (out "
                              "2024-03-01, in 2024-03-16: 15 days apart, "
                              "nothing else pairs): not a purchase — the "
                              "shares keep their ACB", r.stdout)
                # Not drafted as a purchase either.
                f = _cli(root, "find-missing-history", "--write-purchases")
                self.assertIn("a move from your account qa (2024-03-01, 15 "
                              "days before) — its cost carries over",
                              " ".join(f.stdout.split()))

    @rule("US-BASIS-05", "US-BASIS-TRANSFER-BV")
    def test_diff_engine_4_us_run_carries_the_lots_of_a_slow_move(self):
        with tempfile.TemporaryDirectory() as td:
            for gap in (5, 15):
                root = _project(Path(td) / f"p{gap}", "usa",
                                _shift("2024-03-01", gap))
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                self.assertEqual(_filed(root), (200.0, 100.0, 0), gap)
                self.assertFalse((root / "work" /
                                  "qb_transfer_costs.json").exists())
                when = ("2024-03-01" if gap == 5 else
                        "out 2024-03-01, in 2024-03-16: 15 days apart, "
                        "nothing else pairs")
                self.assertIn(f"own-account move: ZZQ.US 100 qa -> qb "
                              f"({when}): not a sale — the lots' basis and "
                              f"purchase dates go to qb.", r.stdout)

    @rule("US-BASIS-05", "US-BASIS-TRANSFER-BV")
    def test_diff_engine_4_a_contribution_in_kind_is_no_slow_move(self):
        # qa's transfer-out is a contribution in kind to a retirement
        # account (its transfer-in the next day; never booked in the
        # US): qb's transfer-in 15 days later came from outside, booked
        # at its stated basis as before, and no lots move.
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", "usa", "2024-03-16")
            with open(root / "taxjson.toml", "a", encoding="utf-8") as f:
                f.write('[accounts.plan]\ntype = "sheltered"\n'
                        'transfers = true\n')
            (root / "inputs" / "plan").mkdir()
            (root / "inputs" / "plan" / "questrade_2024.csv").write_text(
                _QH + "2024-03-02,2024-03-02,TF6,ZZQ,ZZQ CORP TRANSFER,100,"
                "0.00,0.00,0.00,0.00,USD,Transfers,99900003,Margin\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("own-account move", r.stdout)
            self.assertIn("booked at the basis the broker states on the "
                          "row", r.stdout)
            self.assertNotIn("transfer-out of yours that nothing paired",
                             r.stdout)
            j = json.loads(_cli(root, "transfers", "--json").stdout)
            self.assertEqual([(t["account"], t["arrival"])
                              for t in j["transfers"]
                              if t["account"] == "qb"],
                             [("qb", "book value")])

    @rule("US-BASIS-05")
    def test_diff_engine_4_custody_pairing_pairs_only_an_unambiguous_slow_move(self):
        from taxjson.bin.taxjson_run import own_account_custody_moves

        def _moves(legs):
            with tempfile.TemporaryDirectory() as td:
                cache = Path(td)
                for acct in sorted({a for a, _d, _q in legs}):
                    (cache / f"{acct}_questrade_transfers.json").write_text(
                        json.dumps({
                            "metadata": {"account": acct,
                                         "brokerage": "questrade",
                                         "kind": "transfer_sidecar"},
                            "transactions": [
                                {"action": "TRANSFER", "symbol": "ZZQ.US",
                                 "quantity": q, "date": d}
                                for a, d, q in legs if a == acct]}))
                return own_account_custody_moves(
                    sorted({a for a, _d, _q in legs}), cache)
        moves = _moves([("qa", "2024-03-01", -100.0),
                        ("qb", "2024-03-16", 100.0)])
        self.assertEqual([(m["from"], m["to"], m["qty"], m["slow"])
                          for m in moves], [("qa", "qb", 100.0, 15)])
        # Another candidate, or an in leg before the out leg: none.
        self.assertEqual(_moves([("qa", "2024-03-01", -100.0),
                                 ("qc", "2024-03-02", -100.0),
                                 ("qb", "2024-03-16", 100.0)]), [])
        self.assertEqual(_moves([("qb", "2024-03-01", 100.0),
                                 ("qa", "2024-03-16", -100.0)]), [])
        # Within the window: the move it always was, no "slow".
        (m,) = _moves([("qa", "2024-03-01", -100.0),
                       ("qb", "2024-03-06", 100.0)])
        self.assertNotIn("slow", m)


if __name__ == "__main__":
    unittest.main()
