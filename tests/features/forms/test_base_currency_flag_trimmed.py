"""Re-audit-2 planning fixes: `tjs harvest` and the price chain.

Offline throughout: prices come from injected fetchers (the price
chain's `fetchers` test hook) or a seeded price cache, never the
network. Synthetic books only.

Formerly: test_fix_a2_planning_harvest
"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from _support.planning_harvest_runs import TODAY, _iso, _write, _gains, _run, _json_rows


class TestBaseCurrencyFlagTrimmed(unittest.TestCase):
    """A2-1169: --base-currency is stripped and upper-cased on entry in
    harvest, t1135, form-export and carryover, as the config path is.

    Formerly: test_fix_a2_planning_harvest.TestBaseCurrencyFlagTrimmed"""

    def test_harvest(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "margin_gains_wash.json", _gains([
                {"symbol": "XYZ.TO", "qty": 10, "total_cost": 100.0,
                 "position_start_date": _iso(-200)}]))
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--base-currency", " cad"],
                fetchers=[lambda rem: {s: (9.0, "fake", "CAD")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        doc = _json_rows(out)
        self.assertEqual(len(doc["rows"]), 1)
        self.assertEqual(doc["totals"]["currency"], "CAD")

    def _base(self, td):
        return _write(Path(td) / "margin_base.json", {
            "metadata": {"target_currency": "CAD"},
            "transactions": [{
                "date": f"{TODAY.year}-01-05",
                "date_settle": f"{TODAY.year}-01-06",
                "symbol": "XYZ.TO", "action": "BUYSELL", "quantity": 10,
                "price": 10.0, "net_amount": 100.0, "currency": "CAD",
                "fees": 0.0}]})

    def test_t1135(self):
        from taxjson.bin.taxjson_t1135 import main as t1135_main
        with tempfile.TemporaryDirectory() as td:
            b = self._base(td)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = t1135_main(["--year", str(TODAY.year),
                                 "--base-currency", " CAD", str(b)])
        self.assertNotIn("not  CAD", err.getvalue())
        self.assertNotIn("--base-currency", err.getvalue())
        self.assertEqual(rc, 0, err.getvalue())

    def test_form_export(self):
        from taxjson.bin.taxjson_form_export import main as fe_main
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "margin_gains_wash.json", {
                "summary": {"year": TODAY.year},
                "metadata": {"target_currency": "CAD"},
                "transactions": [], "inventory": []})
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = fe_main(["--form", "schedule3", "--country", "ca",
                              "--year", str(TODAY.year),
                              "--base-currency", " cad", str(g)])
        self.assertNotIn("another currency", err.getvalue())
        self.assertEqual(rc, 0, err.getvalue())
        self.assertNotIn(" cad", out.getvalue())


if __name__ == "__main__":
    unittest.main()
