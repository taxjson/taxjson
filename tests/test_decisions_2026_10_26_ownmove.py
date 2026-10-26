"""Backlog TJ-0093 (decided 2026-10-26, option A): a Canadian project's
books carry a move of your own between two taxable accounts per account.

A transfer-out of one taxable account that the run pairs with a
transfer-in of another (transfer_in.arrivals: the same security and
units within 10 days, or a slower move) is one move of your own. The ACB
is one pool across the accounts (s.47), so the engine moved nothing —
and every per-account view read the receiving account short: `tjs list`
showed it at -50 "missing history?", `tjs sanity` a QTY_MISMATCH and a
MISSING_IN_HOLDINGS, `tjs find-missing-history` "AFFECTS 2024 ... fix
before filing" for a correct sale, and the checklist's steps 10 and 22
needed attention. Now the run writes the move's two lot-move legs to
work/<account>_own_moves.json (as a US project's books carry them), each
account's books move the units, and the blended pass books no leg: the
pooled ACB, every Schedule 3 figure and the superficial-loss result are
what they were (tax-logic CA-ACB-OWN-MOVE).

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import settings_for
from _style import CapturedWidth

from taxjson.lib import transfer_in as TI
from taxjson.lib.core import LOT_MOVE_TYPE, TaxTransaction, _own_move_view
from taxjson.lib.corporate_timeline import event_sort_key
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _row(d, ds, act, q, price, gross, comm, net, acct, desc="ZZB MINES LTD",
         kind="Trades"):
    return (f"{d},{ds},{act},ZZB,{desc},{q},{price},{gross},{comm},{net},"
            f"CAD,{kind},{acct},Margin\n")


def _move(d, q, acct, desc):
    return _row(d, d, "TF6", q, "0.00", "0.00", "0.00", "0.00", acct,
                desc=f"ZZB MINES LTD {desc}", kind="Transfers")


def _project(root: Path, variant: str = "base") -> Path:
    """margin buys 100 ZZB at 10.00 on 2024-03-04 and moves 50 to margin2
    on 2024-05-01 (Questrade TF6 rows; the in row states a book value of
    500.00); margin2 bought 100 at 20.00 on 2024-03-05 and sells 150 at
    30.00 on 2024-10-01. Variants: "slow" (the in row 19 days later),
    "sameday" (margin2 holds nothing else and sells the 50 the day they
    arrive), "loss" (margin sells 50 at a loss 6 days before it moves
    the other 50; margin2 still holds them at the year end)."""
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for("canada", year=2024, source_currencies=[])
        + '[accounts.margin]\ntype = "taxable"\n'
        + '[accounts.margin2]\ntype = "taxable"\n')
    for a in ("margin", "margin2"):
        (root / "inputs" / a).mkdir(parents=True)
    (root / "holdings").mkdir()
    in_d = "2024-05-20" if variant == "slow" else "2024-05-01"
    a1 = 99900001
    a2 = 99900003
    m1 = _row("2024-03-04", "2024-03-06", "Buy", 100, "10.00", "-1000.00",
              "-4.95", "-1004.95", a1)
    m2 = _row("2024-03-05", "2024-03-07", "Buy", 100, "20.00", "-2000.00",
              "-4.95", "-2004.95", a2)
    sell2 = _row("2024-10-01", "2024-10-03", "Sell", -150, "30.00",
                 "4500.00", "-4.95", "4495.05", a2)
    out1 = _move("2024-05-01", -50, a1, "TFER TO 99900003")
    in2 = _move(in_d, 50, a2, "TRANSFER BOOK VALUE 500.00")
    held = {"margin": 50, "margin2": 0}
    if variant == "sameday":
        m2 = ""
        sell2 = _row(in_d, "2024-05-03", "Sell", -50, "25.00", "1250.00",
                     "-4.95", "1245.05", a2)
    if variant == "loss":
        m1 = (_row("2024-01-10", "2024-01-12", "Buy", 100, "10.00",
                   "-1000.00", "-4.95", "-1004.95", a1)
              + _row("2024-04-25", "2024-04-29", "Sell", -50, "5.00",
                     "250.00", "-4.95", "245.05", a1))
        m2 = sell2 = ""
        held = {"margin": 0, "margin2": 50}
    (root / "inputs/margin/questrade_2024.csv").write_text(_QH + m1 + out1)
    (root / "inputs/margin2/questrade_2024.csv").write_text(
        _QH + m2 + in2 + sell2)
    for a, q in held.items():
        (root / "holdings" / f"{a}_holdings.toml").write_text(
            '[meta]\nas_of = "2024-12-31"\n'
            + (f'\n[[holding]]\nsymbol = "ZZB.TO"\nquantity = {q}\n'
               if q else ""))
    return root


def _filed(root: Path):
    """(acb, gain, denied) of the project's tax year, the figures filed."""
    r = _cli(root, "sum", "--json")
    f = json.loads(r.stdout)["filing"]
    acb = sum(a.get("acb", 0.0) for a in f["accounts"])
    return (round(acb, 2), round(f["engine_gain_unrounded"], 2),
            round(f["engine_denied_unrounded"], 2))


def _positions(root: Path):
    """[(account, symbol, qty)] as `tjs list` shows them."""
    r = _cli(root, "list")
    out = []
    for ln in r.stdout.splitlines():
        m = re.match(r"(margin2?)\s+(ZZB\.TO)\s+(-?\d+)\s", ln)
        if m:
            out.append((m.group(1), m.group(2), int(m.group(3))))
    return out, r.stdout


def _leg(acct, d, q, sym="ZZB.TO"):
    """One transfer sidecar row as transfer_in.arrivals takes it."""
    return (acct, "questrade",
            dict(action="TRANSFER", symbol=sym, date=d, date_settle=d,
                 time="09:30:00", quantity=q, net_amount=0,
                 currency="CAD", account=acct))


class TestPairingNamesTheMoves(unittest.TestCase):
    """TJ-0093: transfer_in.arrivals returns the moves it cancelled
    between two accounts (OwnMove), the legs the books carry."""

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_an_exact_pair_is_one_move(self):
        moves = []
        found = TI.arrivals([_leg("qa", "2024-05-01", -50),
                             _leg("qb", "2024-05-03", 50)], moves=moves)
        self.assertEqual(found, [])
        self.assertEqual(
            [(m.key, m.quantity, m.out_account, m.out_date, m.in_account,
              m.in_date, m.out_symbol, m.in_symbol, m.in_currency)
             for m in moves],
            [("ZZB.TO", 50.0, "qa", "2024-05-01", "qb", "2024-05-03",
              "ZZB.TO", "ZZB.TO", "CAD")])

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_a_slow_move_and_what_a_pairing_leaves(self):
        # 19 days apart, nothing else pairs: a slower move of your own.
        moves = []
        TI.arrivals([_leg("qa", "2024-05-01", -50),
                     _leg("qb", "2024-05-20", 50)], moves=moves)
        self.assertEqual([(m.quantity, m.in_date) for m in moves],
                         [(50.0, "2024-05-20")])
        # 80 in against 50 out: the 50 move, the other 30 arrived from
        # outside the books (an Arrival, as before).
        moves = []
        found = TI.arrivals([_leg("qa", "2024-05-01", -50),
                             _leg("qb", "2024-05-02", 80)], moves=moves)
        self.assertEqual([m.quantity for m in moves], [50.0])
        self.assertEqual([(a.account, a.quantity) for a in found],
                         [("qb", 30.0)])

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_a_journal_inside_one_account_is_no_move(self):
        moves = []
        TI.arrivals([_leg("qa", "2024-05-01", -50),
                     _leg("qa", "2024-05-01", 50)], moves=moves)
        self.assertEqual(moves, [])


def _tx(acct, d, action, q, typ="", desc=""):
    return TaxTransaction(date=d, action=action, symbol="ZZB.TO",
                          quantity=q, currency="CAD", time="00:00:00",
                          date_settle=d, account=acct, type=typ,
                          description=desc)


class TestEngineView(unittest.TestCase):
    """TJ-0093: the engine books the legs in one account's own view
    only; the blended pass of several accounts books none."""

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_the_blended_book_drops_the_legs(self):
        desc = "own-account move #1: qa -> qb (not a sale)"
        rows = [_tx("qa", "2024-03-04", "BUYSELL", 100),
                _tx("qa", "2024-05-01", "TRANSFER", -50, LOT_MOVE_TYPE, desc),
                _tx("qb", "2024-05-01", "TRANSFER", 50, LOT_MOVE_TYPE, desc),
                _tx("qb", "2024-10-01", "BUYSELL", -50)]
        self.assertEqual([t.action for t in _own_move_view(rows)],
                         ["BUYSELL", "BUYSELL"])
        own = [t for t in rows if t.account == "qb"]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIs(_own_move_view(own), own)
        self.assertIn("note: ZZB.TO: 50 moved from qa on 2024-05-01 (your "
                      "own account)", err.getvalue())

    def test_tj0093_the_history_walk_reads_an_in_leg_at_the_day_start(self):
        # A sale the day the units arrive, settling later, reads them
        # held; the out leg stays after the day's trades.
        sale = _tx("qb", "2024-05-01", "BUYSELL", -50)
        sale.date_settle = "2024-05-03"
        inn = _tx("qb", "2024-05-01", "TRANSFER", 50, LOT_MOVE_TYPE)
        out = _tx("qb", "2024-05-01", "TRANSFER", -50, LOT_MOVE_TYPE)
        k = lambda t: event_sort_key(t, profile="missing_history_walk")  # noqa: E731
        self.assertLess(k(inn), k(sale))
        self.assertLess(k(sale), k(out))


class TestOwnMoveRun(unittest.TestCase):
    """TJ-0093 end to end: the receiving account holds the moved shares
    in every per-account view; the filed figures are main's."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _project(Path(cls._tmp.name) / "p")
        cls.out = _cli(cls.root, "run", "--no-input")
        assert cls.out.returncode == 0, cls.out.stdout + cls.out.stderr

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_list_shows_the_receiver_holding_them(self):
        pos, text = _positions(self.root)
        self.assertEqual(pos, [("margin", "ZZB.TO", 50)], text)
        self.assertNotIn("missing history?", text)

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_sanity_and_find_missing_history_are_clean(self):
        s = _cli(self.root, "sanity")
        self.assertIn("OK: tickers and quantities agree in every group.",
                      s.stdout, s.stdout + s.stderr)
        f = _cli(self.root, "find-missing-history", "--details")
        self.assertIn("No missing-cost-basis issues found", f.stdout,
                      f.stdout + f.stderr)
        self.assertNotIn("go short in margin2's data",
                         self.out.stdout + self.out.stderr)

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_checklist_steps_10_and_22_are_done(self):
        c = _cli(self.root, "checklist", "--run")
        self.assertRegex(c.stdout, r"\[x\]\s+10\. missing-history", c.stdout)
        self.assertRegex(c.stdout, r"\[x\]\s+22\. sanity", c.stdout)

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_the_filed_figures_do_not_move(self):
        # One pool: the 150 sold carry 3/4 of the 200's cost, as
        # without the legs.
        self.assertEqual(_filed(self.root), (2257.42, 2237.62, 0.0))

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_the_books_carry_the_legs_and_transfers_lists_the_rows(
            self):
        legs = {}
        for a in ("margin", "margin2"):
            doc = json.loads((self.root / "work" / f"{a}_own_moves.json")
                             .read_text())
            legs[a] = [(t["type"], t["date"], t["quantity"])
                       for t in doc["transactions"]]
        self.assertEqual(legs, {
            "margin": [(LOT_MOVE_TYPE, "2024-05-01", -50.0)],
            "margin2": [(LOT_MOVE_TYPE, "2024-05-01", 50.0)]})
        # `tjs transfers` lists the broker's two rows, not the legs.
        j = json.loads(_cli(self.root, "transfers", "--json").stdout)
        self.assertEqual(sorted((t["account"], t["quantity"])
                                for t in j["transfers"]),
                         [("margin", -50.0), ("margin2", 50.0)])
        # margin's export ends with 50 still held: still said.
        self.assertIn("Questrade exports for margin end 2024-05-01 with "
                      "open positions (ZZB.TO 50)", self.out.stdout
                      + self.out.stderr)


class TestOwnMoveVariants(unittest.TestCase):
    """TJ-0093: a slower move, a sale the day the shares arrive, and a
    loss before the move (never a superficial loss)."""

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0093_variants(self):
        want = {"slow": ([("margin", "ZZB.TO", 50)], (2257.42, 2237.62, 0.0)),
                "sameday": ([("margin", "ZZB.TO", 50)], (502.47, 742.58, 0.0)),
                # The loss stands: the move is no purchase in the window.
                "loss": ([("margin2", "ZZB.TO", 50)], (502.48, -257.43, 0.0))}
        with tempfile.TemporaryDirectory() as td:
            for name, (pos, filed) in want.items():
                with self.subTest(name):
                    root = _project(Path(td) / name, name)
                    r = _cli(root, "run", "--no-input")
                    self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                    self.assertEqual(_positions(root)[0], pos)
                    self.assertEqual(_filed(root), filed)
                    f = _cli(root, "find-missing-history")
                    self.assertIn("No missing-cost-basis issues found",
                                  f.stdout, f.stdout + f.stderr)
                    s = _cli(root, "sanity")
                    self.assertIn("OK: tickers and quantities agree",
                                  s.stdout, s.stdout + s.stderr)


if __name__ == "__main__":
    unittest.main()
