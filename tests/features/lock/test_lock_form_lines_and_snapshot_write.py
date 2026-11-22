"""Low-round fixes, carryover ledger and filed-year lock (filing-a):
S001-04, S027-18, S027-19, S027-23 (claimed_losses.txt), S027-10 /
S028-00 (per-row rounding of the filed loss), S027-11 / S027-09 (pins),
S028-02 (row currency vs --base-currency); R1-205 / R1-281 (form lines
locked), S031-19 (close-year errors), S031-20 (totals rounded once),
S032-11 (OK says what is not locked). Synthetic data only.

Formerly: test_fix_l_filing_a_co"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_filed as F
from taxjson.lib import filed_locks
from tax_rules import rule
from taxjson.rules import ids as R


class TestLockFormLines(unittest.TestCase):
    """R1-205 / R1-281: moving an amount between return lines (or a
    commission into the price) is drift even with the gain unchanged.

    Formerly: test_fix_l_filing_a_co.TestLockFormLines"""

    def _doc(self, symbol, commission):
        return {"transactions": [
            {"action": "BUYSELL", "symbol": symbol, "qty": 100,
             "gain": 490.0, "proceeds": 1490.0, "cost": 1000.0,
             "commission": commission, "date": "2025-02-03",
             "date_settle": "2025-02-04"}]}

    def test_line_move_and_outlay_move_drift(self):
        filed = filed_locks.aggregates_from_gains(self._doc("XEI.TO", 10.0), year=2025)
        self.assertEqual(filed["form_lines"]["13199"], 1500.0)
        snap = {"accounts": {"margin": filed}}
        cur = filed_locks.aggregates_from_gains(self._doc("XEI.TO", 0.0), year=2025)
        self.assertEqual(cur["realized"], filed["realized"])
        lines = F.diff_snapshot(snap, {"margin": cur})
        self.assertTrue(any("form line 13199 filed 1,500.00 -> now 1,490.00"
                            in ln for ln in lines), lines)
        self.assertEqual(F.diff_snapshot(snap, {"margin": filed}), [])

    def test_crypto_account_lines(self):
        a = filed_locks.aggregates_from_gains(self._doc("BTC", 0.0), crypto=True,
                                    year=2025)
        self.assertNotIn("13199", {k for k, v in a["form_lines"].items()
                                   if v})

    def test_old_lock_without_lines_is_not_drift(self):
        filed = filed_locks.aggregates_from_gains(self._doc("XEI.TO", 10.0), year=2025)
        filed.pop("form_lines")
        cur = filed_locks.aggregates_from_gains(self._doc("XEI.TO", 0.0), year=2025)
        self.assertEqual(F.diff_snapshot({"accounts": {"margin": filed}},
                                         {"margin": cur}), [])


class TestSnapshotWrite(unittest.TestCase):
    """Formerly: test_fix_l_filing_a_co.TestSnapshotWrite"""

    @rule(R.CA_RPT_08)
    def test_totals_rounded_once(self):
        # S031-20: three accounts each denying 100.0045 -> 300.01.
        raw = {a: {"disallowed": 100.0045, "realized": 0.0}
               for a in ("a", "b", "c")}
        accts = {a: {"disallowed": 100.0, "realized": 0.0,
                     "dispositions": 1} for a in ("a", "b", "c")}
        with tempfile.TemporaryDirectory() as td:
            p = F.write_snapshot(Path(td), 2025, "canada", "wash-adjusted",
                                 accts, force=False, raw=raw)
            doc = json.loads(p.read_text())
        self.assertEqual(doc["totals"]["disallowed"], 300.01)
        self.assertEqual(doc["totals"]["dispositions"], 3)

    def test_filed_is_a_file(self):
        # S031-19: one line, no traceback; exit 2, an output it cannot
        # write (backlog TJ-4896: it was 1).
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "filed").write_text("x")
            with self.assertRaises(SystemExit) as cm, \
                    contextlib.redirect_stderr(err):
                F.write_snapshot(Path(td), 2025, "canada", "pre-wash",
                                 {"a": {"realized": 1.0}}, force=False)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("cannot write filed/2025.json", err.getvalue())
        self.assertIn("Nothing was written.", err.getvalue())

    def test_list_document_is_a_value_error(self):
        with self.assertRaises(ValueError):
            filed_locks.aggregates_from_gains([])

    def test_ok_line_says_what_is_not_locked(self):
        # S032-11
        self.assertIn("interest", F.NOT_LOCKED)
        self.assertIn("FX gain on foreign cash", F.NOT_LOCKED)


if __name__ == "__main__":
    unittest.main()
