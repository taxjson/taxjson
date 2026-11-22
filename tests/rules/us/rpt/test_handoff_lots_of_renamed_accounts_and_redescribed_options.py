"""Fix round 7 (wave C) of 2026-10-29, the handoff and in-kind group:
handoff's lot checks with renamed accounts and re-described options,
ALLOWLOSS units, the in-kind contribution date on the registered side,
RESP withdrawal guidance, a per-share INKIND value with a contract
size, the ACQUIRED repair price for options and the SIMILAR warning
(synthetic data only).

Formerly: test_fix_1029_r7c_handoffinkind"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj


def _us_project(root, year, accounts, prior=None):
    root.mkdir(parents=True, exist_ok=True)
    head = ('[settings]\nlocal_timezone = "America/New_York"\n'
            f'year = {year}\ncountry = "usa"\nbase_currency = "USD"\n'
            'source_currencies = []\n')
    if prior:
        head += f'prior_year_record = "{prior}"\n'
    (root / "taxjson.toml").write_text(head + "".join(
        f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


def _handoff(top, prev_books, now_books):
    """Close 2024 with `prev_books` ({account: .tt text}), then run 2025
    with `now_books` and return its `handoff --json` (rc, doc)."""
    prev = _us_project(top / "2024", 2024, list(prev_books))
    for a, text in prev_books.items():
        (prev / "inputs" / a / "trades.tt").write_text(text)
    tj(prev, "run", "--no-input")
    tj(prev, "close-year", "--force", "--yes")
    now = _us_project(top / "2025", 2025, list(now_books),
                      prior="../2024/filed/2024.json")
    for a, text in now_books.items():
        (now / "inputs" / a / "trades.tt").write_text(text)
    tj(now, "run", "--no-input")
    r = tj(now, "handoff", "--json", check=False)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        doc = None
    return r, doc


class TestHandoffRenamedAccountLots(unittest.TestCase):
    """TJ-0919: renamed accounts whose lot sequences tie on date and
    units are paired by their whole lots (basis too), not by name order.

    Formerly: test_fix_1029_r7c_handoffinkind.TestHandoffRenamedAccountLots"""

    @staticmethod
    def _lots(acct, basis):
        return [{"account": acct, "date": "2024-01-02", "qty": 10.0,
                 "basis": basis}]

    def test_same_lots_any_name_order(self):
        from taxjson.lib.handoff import _same_lots
        rec = self._lots("a", 200.0) + self._lots("b", 100.0)
        for x, y in (("x", "y"), ("y", "x")):
            with self.subTest(order=(x, y)):
                # In account-name order, as the books list them.
                op = sorted(self._lots(x, 200.0) + self._lots(y, 100.0),
                            key=lambda lot: lot["account"])
                self.assertTrue(_same_lots(rec, op))
        op = self._lots("x", 200.0) + self._lots("y", 150.0)
        self.assertFalse(_same_lots(rec, op))

    def test_handoff_accepts_swapped_rename(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            buy = "BUYSELL 2024-01-02 09:30:00 SYN.US 10 USD {p} {c} 0\n"
            sell = "BUYSELL 2025-06-02 09:30:00 SYN.US -5 USD 30 150 0\n"
            r, doc = _handoff(
                top, {"a": buy.format(p=20, c=200),
                      "b": buy.format(p=10, c=100)},
                {"x": buy.format(p=10, c=100) + sell,
                 "y": buy.format(p=20, c=200) + sell})
            self.assertEqual(r.returncode, 0, r.stdout[-3000:]
                             + r.stderr[-2000:])


class TestHandoffRedescribedOptionLots(unittest.TestCase):
    """TJ-0920: an option the opening re-describes (SYN -> SYN.B) has its
    lots compared too: a changed acquisition date is not accepted.

    Formerly: test_fix_1029_r7c_handoffinkind.TestHandoffRedescribedOptionLots"""

    _OLD = "SYN260116C00010000.US"
    _NEW = "SYN.B260116C00010000.US"

    def _run(self, day):
        with tempfile.TemporaryDirectory() as td:
            return _handoff(
                Path(td),
                {"margin": f"BUYSELL 2023-12-01 09:30:00 {self._OLD} 1 USD "
                           f"1 100 0\n"},
                {"margin": f"BUYSELL {day} 09:30:00 {self._NEW} 1 USD 1 "
                           f"100 0\nBUYSELL 2025-02-03 09:30:00 "
                           f"{self._NEW} -1 USD 1.5 150 0\n"})

    def test_changed_date_is_flagged(self):
        r, _doc = self._run("2024-12-02")
        self.assertEqual(r.returncode, 1, r.stdout[-3000:])
        self.assertIn(self._NEW, r.stdout + r.stderr)
        self.assertIn("2023-12-01", r.stdout + r.stderr)

    def test_same_date_is_accepted(self):
        r, _doc = self._run("2023-12-01")
        self.assertEqual(r.returncode, 0, r.stdout[-3000:]
                         + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
