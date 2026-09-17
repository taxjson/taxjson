"""Tests for the `taxjson run` per-account holdings diff.

After regenerating `<account>_holdings.toml`, the wrapper compares the
new file to the previous snapshot (kept in `work/`) and
emits a one-line-per-changed-symbol summary so the user can spot
unexpected trades at a glance. Silent when nothing changed.
"""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_run import (
    _load_holdings_summary, print_holdings_diff, _fmt_qty,
    maybe_print_holdings_diff,
)


def _toml(holdings):
    """Build a minimal holdings.toml string with one [[holding]] per
    entry (symbol, qty, total_cost)."""
    lines = []
    for sym, qty, cost in holdings:
        lines.append("[[holding]]")
        lines.append(f'symbol = "{sym}"')
        lines.append(f"quantity = {qty}")
        lines.append(f"total_cost = {cost}")
        lines.append("")
    return "\n".join(lines) + "\n"


class TestFmtQty(unittest.TestCase):
    def test_integer_qty_no_trailing_zero(self):
        self.assertEqual(_fmt_qty(100.0), "100")
        self.assertEqual(_fmt_qty(-50.0), "-50")
        self.assertEqual(_fmt_qty(0.0), "0")

    def test_fractional_keeps_meaningful_digits(self):
        self.assertEqual(_fmt_qty(0.5), "0.5")
        self.assertEqual(_fmt_qty(0.123456), "0.123456")
        # Trailing zeros after the meaningful part get trimmed.
        self.assertEqual(_fmt_qty(1.500000), "1.5")


class TestLoadHoldingsSummary(unittest.TestCase):
    def test_loads_symbol_qty_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "h.toml"
            p.write_text(_toml([
                ("AAPL.US", 100.0, 18500.0),
                ("MSFT.US", 50.0, 25000.0),
            ]))
            summary = _load_holdings_summary(p)
            self.assertEqual(summary["AAPL.US"], (100.0, 18500.0))
            self.assertEqual(summary["MSFT.US"], (50.0, 25000.0))

    def test_missing_file_returns_empty(self):
        self.assertEqual(_load_holdings_summary(Path("/nonexistent.toml")), {})


class TestPrintHoldingsDiff(unittest.TestCase):
    def _capture(self, prev, curr):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_holdings_diff(prev, curr)
        return buf.getvalue()

    def test_silent_when_no_changes(self):
        # Identical prev/curr → no output (silent on no news).
        out = self._capture(
            {"AAPL.US": (100.0, 18500.0)},
            {"AAPL.US": (100.0, 18500.0)},
        )
        self.assertEqual(out, "")

    def test_cost_basis_only_change_is_silent(self):
        """A wash-sale ACB bump that keeps qty constant but bumps the
        basis must NOT emit a diff line — basis-only changes aren't
        trade activity and would be noise in this high-level "what
        moved" summary. Wash-sale basis impacts surface in `_wash.sum`
        via the disallowance ledger; this diff is for trades, not
        accounting adjustments."""
        out = self._capture(
            {"AAPL.US": (100.0, 18000.0)},  # cost was 18000
            {"AAPL.US": (100.0, 18500.0)},  # bumped by wash basis add
        )
        self.assertEqual(out, "")

    def test_new_position_marker(self):
        out = self._capture({}, {"NVDA.US": (50.0, 50000.0)})
        self.assertIn("+ NVDA.US: 50 (new position)", out)

    def test_closed_position_marker(self):
        out = self._capture({"MSFT.US": (75.0, 30000.0)}, {})
        self.assertIn("- MSFT.US: was 75 (closed)", out)

    def test_quantity_increase(self):
        out = self._capture(
            {"AAPL.US": (100.0, 18500.0)},
            {"AAPL.US": (150.0, 27500.0)},
        )
        self.assertIn("~ AAPL.US: 100 → 150 (+50)", out)

    def test_quantity_decrease(self):
        out = self._capture(
            {"ENB.TO": (200.0, 10000.0)},
            {"ENB.TO": (150.0, 7500.0)},
        )
        self.assertIn("~ ENB.TO: 200 → 150 (-50)", out)

    def test_fractional_quantity_change(self):
        # Crypto quantities round-trip without spurious precision.
        out = self._capture(
            {"BTC": (0.5, 30000.0)},
            {"BTC": (0.6, 36000.0)},
        )
        self.assertIn("~ BTC: 0.5 → 0.6 (+0.1)", out)

    def test_mixed_changes_count_in_header(self):
        out = self._capture(
            {"AAPL.US": (100.0, 18500.0), "MSFT.US": (75.0, 30000.0)},
            {"AAPL.US": (150.0, 27500.0), "NVDA.US": (50.0, 50000.0)},
        )
        # 3 changes: AAPL up, MSFT closed, NVDA new.
        self.assertIn("holdings changes vs prior run (3):", out)
        self.assertIn("+ NVDA.US", out)
        self.assertIn("- MSFT.US", out)
        self.assertIn("~ AAPL.US", out)

    def test_changes_are_sorted_by_symbol(self):
        # Sorted order makes the output diffable across runs.
        out = self._capture(
            {},
            {"ZZZ.US": (1.0, 1.0), "AAA.US": (1.0, 1.0), "MMM.US": (1.0, 1.0)},
        )
        idx_aaa = out.index("AAA.US")
        idx_mmm = out.index("MMM.US")
        idx_zzz = out.index("ZZZ.US")
        self.assertLess(idx_aaa, idx_mmm)
        self.assertLess(idx_mmm, idx_zzz)


class TestMaybePrintHoldingsDiffFirstRunSilent(unittest.TestCase):
    """`maybe_print_holdings_diff` is the wrapper-level gate that
    makes the FIRST `taxjson run` silent (no `+ AAPL: 100 (new
    position)` flood for every existing holding). It runs the diff
    only when a previous snapshot exists in the cache; otherwise
    the user sees just the normal `→ holdings.toml` line."""

    def test_silent_when_baseline_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prev = tmp / 'absent.toml'          # does not exist
            curr = tmp / 'curr.toml'
            curr.write_text(_toml([("AAPL.US", 100.0, 18500.0)]))
            buf = io.StringIO()
            with redirect_stdout(buf):
                maybe_print_holdings_diff(prev, curr)
            self.assertEqual(buf.getvalue(), "",
                             "First run (no baseline) must be silent")

    def test_emits_diff_when_baseline_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prev = tmp / 'prev.toml'
            curr = tmp / 'curr.toml'
            prev.write_text(_toml([("AAPL.US", 100.0, 18500.0)]))
            curr.write_text(_toml([("AAPL.US", 150.0, 27500.0)]))
            buf = io.StringIO()
            with redirect_stdout(buf):
                maybe_print_holdings_diff(prev, curr)
            self.assertIn("~ AAPL.US: 100 → 150 (+50)", buf.getvalue())


if __name__ == '__main__':
    unittest.main()
