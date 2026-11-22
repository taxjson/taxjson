"""Re-audit-2 errors (errA-d): one-line errors instead of tracebacks in the
checklist / watch state, close-year / handoff, export,
some parsers and taxjson-sum-income.

Formerly: test_fix_a2_errA_d"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth
import _hermetic  # (a synthetic HOME, offline: tests/_hermetic)
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO = _paths.REPO
ENV = _hermetic.environ(TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
                        NO_COLOR="1", TAXJSON_WIDTH="0")    # captured output

TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')


def tj(root, *args):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *args], capture_output=True,
                          text=True, env=ENV, stdin=subprocess.DEVNULL)


def no_tb(tc, r):
    tc.assertNotIn("Traceback", r.stderr, r.stderr[-2000:])


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        for p in self.root.rglob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o755)
                else:
                    p.chmod(0o644)
            except OSError:
                pass
        self._td.cleanup()


# --------------------------------------------------- handoff / close-year
GOOD_LOCK = {
    "schema_version": 2, "record_version": 2, "year": 2024,
    "country": "canada", "date_basis": "settle", "closed_at": "2025-04-01",
    "dispositions": [{"account": "margin", "symbol": "XYZ.TO",
                      "date": "2024-05-05", "date_settle": "2024-05-06",
                      "qty": 10.0, "proceeds": 120.0, "cost": 100.0,
                      "gain": 20.0, "denied": 0.0}],
    "settle_next_year": [{"group": "equity", "account": "margin",
                          "symbol": "XYZ.TO", "date": "2024-12-31",
                          "date_settle": "2025-01-02", "qty": 5.0,
                          "net": -50.0}],
    "year_end": {"equity": {"XYZ.TO": {"qty": 5.0, "acb": 50.0,
                                       "deferred": 0.0}}},
    "boundary_rows": [],
}


class TestHandoffRecord(_Tmp):
    """A2-0803: field-level damage in the prior-year lock is one line.

    Formerly: test_fix_a2_errA_d.TestHandoffRecord"""

    def test_good_lock_validates(self):
        from taxjson.lib import handoff
        handoff.validate_record(json.loads(json.dumps(GOOD_LOCK)), "f")

    def test_wrong_field_shapes_refused(self):
        from taxjson.lib import handoff
        cases = [("dispositions", "x"), ("dispositions", 7),
                 ("dispositions", {"a": 1}), ("schema_version", "x"),
                 ("schema_version", [1]), ("settle_next_year", "x"),
                 ("settle_next_year", 7), ("year_end", [1]),
                 ("year", "x")]
        for k, v in cases:
            rec = json.loads(json.dumps(GOOD_LOCK))
            rec[k] = v
            with self.assertRaises(handoff.RecordError, msg=(k, v)) as cm:
                handoff.validate_record(rec, "filed/2024.json")
            self.assertIn("filed/2024.json", str(cm.exception))
        for lst, field in (("dispositions", None),
                           ("dispositions", "gain"),
                           ("dispositions", "symbol"),
                           ("settle_next_year", None),
                           ("settle_next_year", "symbol"),
                           ("settle_next_year", "qty")):
            for v in ("x", 7, None, [1], {"a": 1}, {}):
                rec = json.loads(json.dumps(GOOD_LOCK))
                if field is None:
                    if v == {}:
                        continue
                    rec[lst][0] = v
                else:
                    if field != "symbol" and v == 7:
                        continue
                    if field == "symbol" and v == "x":
                        continue
                    rec[lst][0][field] = v
                with self.assertRaises(handoff.RecordError,
                                       msg=(lst, field, v)):
                    handoff.validate_record(rec, "f")

    def test_bom_lock_loads(self):
        from taxjson.lib import handoff
        p = self.root / "2024.json"
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps(GOOD_LOCK).encode())
        self.assertEqual(handoff.load_record(p)["year"], 2024)

    def test_handoff_cli_one_line_exit_2(self):
        (self.root / "taxjson.toml").write_text(TOML)
        (self.root / "filed").mkdir()
        rec = json.loads(json.dumps(GOOD_LOCK))
        rec["dispositions"] = "x"
        (self.root / "filed" / "2024.json").write_text(json.dumps(rec))
        r = tj(self.root, "handoff")
        no_tb(self, r)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("dispositions", r.stderr)


class TestFiledDispositionsCsv(_Tmp):
    """A2-0769, A2-1397: short rows, blank symbols, a directory.

    Formerly: test_fix_a2_errA_d.TestFiledDispositionsCsv"""

    def load(self, text):
        from taxjson.lib import handoff
        p = self.root / "filed.csv"
        p.write_text("symbol,date,qty,proceeds,cost,gain\n" + text)
        return handoff.load_filed_dispositions(p)

    def test_short_row(self):
        with self.assertRaisesRegex(ValueError, r"filed.csv:2: bad row"):
            self.load("AAPL.US,2024-05-14\n")

    def test_blank_symbol(self):
        with self.assertRaisesRegex(ValueError, r"filed.csv:2: .*blank"):
            self.load(",2024-05-14,1,2,3,4\n")

    def test_non_finite(self):
        with self.assertRaisesRegex(ValueError, r"filed.csv:2"):
            self.load("AAPL.US,2024-05-14,nan,nan,inf,-inf\n")

    def test_directory(self):
        from taxjson.lib import handoff
        (self.root / "d.csv").mkdir()
        with self.assertRaises(ValueError):
            handoff.load_filed_dispositions(self.root / "d.csv")

    def test_good_row(self):
        self.assertEqual(self.load("AAPL.US,2024-05-14,1,2,3,4\n")[0]
                         ["symbol"], "AAPL.US")


class TestHandoffGainsShape(_Tmp):
    """A2-1396 / A2-0794 (handoff part): a wrong-shape gains file.

    Formerly: test_fix_a2_errA_d.TestHandoffGainsShape"""

    def test_dispositions_and_check_refuse(self):
        from taxjson.lib import handoff
        p = self.root / "brk_gains.json"
        for bad in ("[]", '{"transactions": ["x"]}',
                    '{"transactions": 5}'):
            p.write_text(bad)
            if bad == "[]":
                # a bare list is a transaction book: no sales, no crash
                self.assertEqual(handoff.dispositions({"brk": p}, 2024), [])
                continue
            with self.assertRaises(handoff.BooksError):
                handoff.dispositions({"brk": p}, 2024)

    def test_rows_wrong_row_type(self):
        from taxjson.lib import handoff
        p = self.root / "m_base.json"
        p.write_text('{"transactions": [{"symbol": "A", "quantity": "x"}]}')
        with self.assertRaises(handoff.BooksError):
            handoff._rows(p)


if __name__ == "__main__":
    unittest.main()
