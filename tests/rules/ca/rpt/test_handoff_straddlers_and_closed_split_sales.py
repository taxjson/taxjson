"""Fix round 7 (wave B) of 2026-10-29, the handoff group: the year
handoff's straddler and double checks, the deleted prior-year lock, the
all-history selection recompute and the journals report (synthetic
data only).

Formerly: test_fix_1029_r7b_handoff"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price, acct="99900001"):
    gross = abs(qty) * price
    net = -gross if action == "Buy" else gross
    return (f"{trade} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},D,"
            f"{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,{acct},"
            f"Trades,Individual\n")


def _project(root, year, accounts=("margin",), prior=None):
    root.mkdir(parents=True, exist_ok=True)
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    if prior:
        head += f'prior_year_record = "{prior}"\n'
    (root / "taxjson.toml").write_text(head + "".join(
        f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


class TestStraddlerAccount(unittest.TestCase):
    """TJ-0807: a same-size sale in ANOTHER account does not stand in
    for the closed year's straddling sale that this project lost.

    Formerly: test_fix_1029_r7b_handoff.TestStraddlerAccount"""

    def _run(self, mode):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            old = _project(top / "2024", 2024, ("a", "b"))
            buys_a = _QT_HEADER + _qt("2024-03-01", "2024-03-04", "Buy",
                                      "SYN.TO", 10, 10)
            buys_b = _QT_HEADER + _qt("2024-03-01", "2024-03-04", "Buy",
                                      "SYN.TO", 10, 10, "99900002")
            cross = _qt("2024-12-31", "2025-01-02", "Sell", "SYN.TO", -5,
                        20)
            (old / "inputs/a/q.csv").write_text(buys_a + cross)
            (old / "inputs/b/q.csv").write_text(buys_b)
            tj(old, "run", "--no-input")
            tj(old, "close-year", "--yes", "--force")
            p = _project(top / "2025", 2025, ("a", "b"),
                         "../2024/filed/2024.json")
            (p / "inputs/a/q.csv").write_text(
                buys_a + (cross if mode == "complete" else ""))
            (p / "inputs/b/q.csv").write_text(
                buys_b + _qt("2025-01-02", "2025-01-03", "Sell", "SYN.TO",
                             -5, 20, "99900002"))
            tj(p, "run", "--no-input")
            r = tj(p, "handoff", "--json", check=False)
            return r.returncode, json.loads(r.stdout)

    def test_other_accounts_sale_does_not_clear_the_missing_one(self):
        rc, rep = self._run("missing_a")
        self.assertEqual(rc, 1, rep)
        self.assertEqual([(m["account"], m["symbol"]) for m in rep["missed"]],
                         [("a", "SYN.TO")])

    def test_complete_books_pass(self):
        rc, rep = self._run("complete")
        self.assertEqual((rc, rep["missed"], rep["double"]), (0, [], []),
                         rep)


class TestOwnClosedSplit(unittest.TestCase):
    """TJ-0808: the closed year's sale out of split-created units is this
    project's own row, not a double of a same-size January sale.

    Formerly: test_fix_1029_r7b_handoff.TestOwnClosedSplit"""

    def _run(self, start):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            old = _project(top / "2024", 2024)
            hist = (start
                    + "BUYSELL 2024-12-30 09:30:00 SYN.TO -2 CAD 15 30 0\n"
                    "BUYSELL 2024-12-31 09:30:00 SYN.TO 2 CAD 10 20 0\n")
            (old / "inputs/margin/trades.tt").write_text(hist)
            tj(old, "run", "--no-input")
            tj(old, "close-year", "--yes", "--force")
            p = _project(top / "2025", 2025,
                         prior="../2024/filed/2024.json")
            (p / "inputs/margin/trades.tt").write_text(
                hist + "BUYSELL 2025-01-02 09:30:00 SYN.TO -2 CAD 15 30 0\n")
            tj(p, "run", "--no-input")
            r = tj(p, "handoff", "--json", check=False)
            return r.returncode, json.loads(r.stdout)

    def test_split_history_passes(self):
        rc, rep = self._run(
            "BUYSELL 2024-01-02 09:30:00 SYN.TO 1 CAD 10 10 0\n"
            "SPLIT 2024-12-02 09:30:00 SYN.TO SYN.TO 2\n")
        self.assertEqual((rc, rep["double"]), (0, []), rep)

    def test_no_split_control_passes(self):
        rc, rep = self._run(
            "BUYSELL 2024-01-02 09:30:00 SYN.TO 2 CAD 5 10 0\n")
        self.assertEqual((rc, rep["double"]), (0, []), rep)


if __name__ == "__main__":
    unittest.main()
