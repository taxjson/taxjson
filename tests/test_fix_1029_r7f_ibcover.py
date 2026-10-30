"""Fix round 7 (wave F), group ibcover: a regression of TJ-0930's export
coverage and the cap it added (backlog TJ-1313, TJ-1331). Synthetic data only; account ids are fake
(pii-ok)."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import tj
from test_fix_1029_r7c_coverage import _cfg, _checklist, _ib, _project

from taxjson.lib import export_coverage as EC
from test_fix_1029_r7e_ib import _A, _B
from test_fix_ibparse import _trade


class TestCoverageOneSymbolTwoAccounts(unittest.TestCase):
    """TJ-1313: two IB accounts' gaps of one end are one line with one
    entry per symbol, the units added; TJ-0930's split per account stays."""

    def _root(self, td, a, b):
        root = _project(Path(td) / "p", 2025, {"margin": "taxable"},
                        extra="combined_broker_accounts = true\n")
        (root / "inputs/margin/a.csv").write_text(a)
        (root / "inputs/margin/b.csv").write_text(b)
        return root

    def _gaps(self, root):
        return EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))

    def _plus(self, text, acct, symbol, qty):
        return text + _trade(symbol, '2025-03-03, 10:00:00', qty, 10,
                             -10 * qty, acct=acct, cur='CAD')

    def test_one_symbol_in_both(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, _ib(_A, "March 3, 2025", "ZQP", 100),
                              _ib(_B, "March 3, 2025", "ZQP", 50))
            r = tj(root, "run", "--no-input")
            self.assertIn("with 1 position(s) open",
                          r.stdout + r.stderr)
            gaps = self._gaps(root)
            self.assertEqual([g.positions for g in gaps],
                             [[("ZQP.TO", 150.0)]])
            self.assertEqual(EC._held_text(gaps[0]), "ZQP.TO 150")
            self.assertIn("1 open position(s)", _checklist(root).detail)

    def test_two_symbols_in_both(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(
                td,
                self._plus(_ib(_A, "March 3, 2025", "ZQR", 10), _A, "ZQP", 100),
                self._plus(_ib(_B, "March 3, 2025", "ZQP", 50), _B, "ZQR", 20))
            tj(root, "run", "--no-input")
            self.assertEqual([g.positions for g in self._gaps(root)],
                             [[("ZQP.TO", 150.0), ("ZQR.TO", 30.0)]])
            self.assertIn("2 open position(s)", _checklist(root).detail)

    def test_other_symbols_stay_apart(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, _ib(_A, "March 3, 2025", "ZQP", 100),
                              _ib(_B, "March 3, 2025", "ZQR", 50))
            tj(root, "run", "--no-input")
            self.assertEqual([g.positions for g in self._gaps(root)],
                             [[("ZQP.TO", 100.0), ("ZQR.TO", 50.0)]])

    def test_other_ends_stay_two_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, _ib(_A, "March 3, 2025", "ZQP", 100),
                              _ib(_B, "June 30, 2025", "ZQP", 50))
            tj(root, "run", "--no-input")
            self.assertEqual([(g.end, g.positions) for g in self._gaps(root)],
                             [("2025-03-03", [("ZQP.TO", 100.0)]),
                              ("2025-06-30", [("ZQP.TO", 50.0)])])

    def test_at_most_what_the_account_holds(self):
        # A .tt sale of 30 before the exports' end: the books hold 120.
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, _ib(_A, "March 3, 2025", "ZQP", 100),
                              _ib(_B, "March 3, 2025", "ZQP", 50))
            (root / "inputs/margin/trades.tt").write_text(
                "BUYSELL 2025-03-03 15:00:00 ZQP.TO -30 CAD 15 450 0\n")
            tj(root, "run", "--no-input")
            self.assertEqual([g.positions for g in self._gaps(root)],
                             [[("ZQP.TO", 120.0)]])

    def test_a_later_sale_closes_part_of_the_sum(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, _ib(_A, "March 3, 2025", "ZQP", 100),
                              _ib(_B, "March 3, 2025", "ZQP", 50))
            (root / "inputs/margin/trades.tt").write_text(
                "BUYSELL 2025-06-02 09:30:00 ZQP.TO -30 CAD 15 450 0\n")
            tj(root, "run", "--no-input")
            self.assertEqual([g.positions for g in self._gaps(root)],
                             [[("ZQP.TO", 120.0)]])

    def test_a_split_past_the_end_scales_the_sum(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, _ib(_A, "March 3, 2025", "ZQP", 100),
                              _ib(_B, "March 3, 2025", "ZQP", 50))
            (root / "inputs/margin/trades.tt").write_text(
                "SPLIT 2025-06-02 09:30:00 ZQP.TO ZQP.TO 2\n")
            tj(root, "run", "--no-input")
            self.assertEqual([g.positions for g in self._gaps(root)],
                             [[("ZQP.TO", 300.0)]])

    def test_a_rename_past_the_end_keeps_the_sum(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, _ib(_A, "March 3, 2025", "ZQP", 100),
                              _ib(_B, "March 3, 2025", "ZQP", 50))
            (root / "inputs/margin/trades.tt").write_text(
                "SPLIT 2025-06-02 09:30:00 ZQP.TO ZQN.TO 1\n")
            tj(root, "run", "--no-input")
            self.assertEqual([g.positions for g in self._gaps(root)],
                             [[("ZQP.TO", 150.0)]])

    def _after(self, tt):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, _ib(_A, "March 3, 2025", "ZQP", 100),
                              _ib(_B, "March 3, 2025", "ZQP", 50))
            (root / "inputs/margin/trades.tt").write_text(tt)
            tj(root, "run", "--no-input")
            return [g.positions for g in self._gaps(root)]

    def test_a_rename_past_the_end_then_a_new_buy_of_the_old_ticker(self):
        # TJ-1331: the cutoff's 5 are the later buy, not the end's units.
        self.assertEqual(
            self._after("SPLIT 2025-06-02 09:30:00 ZQP.TO ZQN.TO 1\n"
                        "BUYSELL 2025-07-02 09:30:00 ZQP.TO 5 CAD 15 75 0\n"),
            [[("ZQP.TO", 150.0)]])

    def test_a_sale_before_the_end_then_a_rename_past_it(self):
        # TJ-1331: the cutoff holds no ZQP.TO; the end held 120.
        self.assertEqual(
            self._after("BUYSELL 2025-03-03 15:00:00 ZQP.TO -30 CAD 15 450 0\n"
                        "SPLIT 2025-06-02 09:30:00 ZQP.TO ZQN.TO 1\n"),
            [[("ZQP.TO", 120.0)]])

    def test_a_sale_before_the_end_then_a_split_past_it(self):
        # TJ-1331: the larger holding is the cutoff's, as the split scales.
        self.assertEqual(
            self._after("BUYSELL 2025-03-03 15:00:00 ZQP.TO -30 CAD 15 450 0\n"
                        "SPLIT 2025-06-02 09:30:00 ZQP.TO ZQP.TO 2\n"),
            [[("ZQP.TO", 240.0)]])

    def test_written_options_and_expired_entries_add_up(self):
        opt = "SYN250620C00010000"
        rows = [{"action": "BUYSELL", "symbol": opt, "quantity": -3,
                 "date": "2025-03-03"}]

        def gap(q):
            return EC.Gap("margin", "ib", "2025-03-03", "statement",
                          "2025-12-31", False, [(opt, q)], [],
                          [("SYN250321C00010000", q, "2025-03-21")])
        got = EC._merge([gap(-1.0), gap(-2.0)], rows, False)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].positions, [(opt, -3.0)])
        self.assertEqual(got[0].expired,
                         [("SYN250321C00010000", -3.0, "2025-03-21")])

    def test_one_gap_is_left_as_it_is(self):
        g = EC.Gap("margin", "ib", "2025-03-03", "statement", "2025-12-31",
                   False, [("ZQR.TO", 1.0), ("ZQP.TO", 2.0)])
        self.assertEqual(EC._merge([g], [], False)[0].positions,
                         [("ZQR.TO", 1.0), ("ZQP.TO", 2.0)])


if __name__ == '__main__':
    unittest.main()
