"""Fix round 7 (2026-10-29, wave D), group corp: corporate actions (backlog
TJ-1022, TJ-1025, TJ-1026, TJ-1117, TJ-1132). Synthetic data only: made-up
tickers and account ids.

Formerly: test_fix_1029_r7d_corp"""
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from taxjson.model.transaction import TaxTransaction as T


class TestTJ1132DatedRenameExerciseTarget(unittest.TestCase):
    """TJ-1132: a dated RENAME left a later warrant exercise's exercise_of
    on the old symbol: read as settled in cash, a loss of the warrants'
    cost. TJ-0978 fixed the undated GLOBAL map.

    Formerly: test_fix_1029_r7d_corp.TestTJ1132DatedRenameExerciseTarget"""

    HEAD = ("BUYSELL 2025-01-02 10:00:00 SYN.WT.TO 10 CAD 1 10 0\n"
            "BUYSELL 2025-01-03 10:00:00 SYN.TO 5 CAD 4 20 0\n"
            "RENAME 2025-03-01 SYN.TO NEW.TO\n")

    def _project(self, d, tt):
        root = Path(d) / "p"
        (root / "inputs/margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\n'
            'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\noption_grant_timing_since = 2025\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "ticker.map").write_text("")
        (root / "inputs/margin/book.tt").write_text(tt)
        return root

    def _gain(self, root):
        import json
        tj(root, "run", "--no-input", check=False)
        return json.loads(tj(root, "sum", "--json").stdout)[
            "filing"]["totals"]

    def test_share_leg_on_the_new_symbol(self):
        tt = (self.HEAD
              + "ASSIGN 2025-06-02 10:00:00 SYN.WT.TO -10 CAD 0 0 "
                "exercise_of=SYN.TO\n"
              + "BUYSELL 2025-06-02 10:00:00 NEW.TO 10 CAD 5 50 0\n")
        with tempfile.TemporaryDirectory() as d:
            t = self._gain(self._project(d, tt))
        self.assertEqual((t["proceeds"], t["acb"], t["gain"]), (0, 0, 0))

    def test_no_shares_before_the_rename(self):
        # The account holds only the warrants: no rename row of its own.
        tt = ("BUYSELL 2025-01-02 10:00:00 SYN.WT.TO 10 CAD 1 10 0\n"
              "RENAME 2025-03-01 SYN.TO NEW.TO\n"
              "ASSIGN 2025-06-02 10:00:00 SYN.WT.TO -10 CAD 0 0 "
              "exercise_of=SYN.TO\n"
              "BUYSELL 2025-06-02 10:00:00 NEW.TO 10 CAD 5 50 0\n")
        with tempfile.TemporaryDirectory() as d:
            t = self._gain(self._project(d, tt))
        self.assertEqual((t["proceeds"], t["acb"], t["gain"]), (0, 0, 0))

    def test_library_rules(self):
        from taxjson.lib.renames import apply_dated_renames
        from taxjson.lib.rename_decl import DatedRename

        def rows(day):
            return [T(action="BUYSELL", date="2025-01-03", symbol="SYN.TO",
                      quantity=5, currency="CAD", account="m", id="b"),
                    T(action="ASSIGN", date=day, symbol="SYN.WT.TO",
                      quantity=-10, currency="CAD", account="m", id="x",
                      exercise_of="SYN.TO")]

        def run(day, **kw):
            import io
            dr = DatedRename(old="SYN.TO", new="NEW.TO", date="2025-03-01",
                             **kw)
            out = apply_dated_renames(rows(day), [dr], stream=io.StringIO())
            return next(t.exercise_of for t in out if t.id == "x")
        self.assertEqual(run("2025-06-02"), "NEW.TO")
        self.assertEqual(run("2025-03-01"), "NEW.TO")
        # Before the rename its shares were the old symbol's.
        self.assertEqual(run("2025-02-03"), "SYN.TO")

    def test_share_leg_kept_on_the_old_symbol_keeps_the_target(self):
        # late=separate: another company uses the ticker now, and the
        # exercise delivers its shares.
        import io
        from taxjson.lib.renames import apply_dated_renames
        from taxjson.lib.rename_decl import DatedRename
        rows = [T(action="BUYSELL", date="2025-01-03", symbol="SYN.TO",
                  quantity=5, currency="CAD", account="m", id="b"),
                T(action="ASSIGN", date="2025-06-02", symbol="SYN.WT.TO",
                  quantity=-10, currency="CAD", account="m", id="x",
                  exercise_of="SYN.TO"),
                T(action="BUYSELL", date="2025-06-02", symbol="SYN.TO",
                  quantity=10, currency="CAD", account="m", id="l")]
        dr = DatedRename(old="SYN.TO", new="NEW.TO", date="2025-03-01",
                         late="separate")
        out = apply_dated_renames(rows, [dr], stream=io.StringIO())
        self.assertEqual(next(t.exercise_of for t in out if t.id == "x"),
                         "SYN.TO")


if __name__ == "__main__":
    unittest.main()
