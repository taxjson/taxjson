"""Re-audit-2 fixes: one-line errors and consistent exit codes
(errors-01 / errors-03 / errors-04 lists, errA area).

Synthetic data only.

Formerly: test_fix_a2_errA
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin._entry import console_prog
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _tool(mod, *args, env=None, cwd=None):
    """The console script `taxjson-<tool>` (its entry point,
    taxjson.bin._entry:<mod>)."""
    e = dict(os.environ, **(env or {}))
    code = (f"import sys; from taxjson.bin._entry import {mod} as m; "
            f"sys.argv[0] = {console_prog(mod)!r}; sys.exit(m())")
    return subprocess.run([sys.executable, "-c", code, *map(str, args)],
                          capture_output=True, text=True, env=e, cwd=cwd,
                          stdin=subprocess.DEVNULL)


def _gains_doc(rows=None, inv=None):
    return {"transactions": rows if rows is not None else [
        {"symbol": "ZZQ.TO", "date": "2025-03-03", "qty": 10,
         "proceeds": 120.0, "cost": 100.0, "gain": 20.0,
         "currency": "CAD", "account": "margin"}],
        "inventory": inv or []}


def _base_row(**kw):
    r = {"action": "BUYSELL", "date": "2025-01-06", "time": "10:00:00",
         "symbol": "ZZQ.TO", "quantity": 10, "price": 10.0,
         "net_amount": -100.0, "commission": 0.0, "currency": "CAD",
         "account": "margin", "id": "t1"}
    r.update(kw)
    return r


class TestBomJson(unittest.TestCase):
    """A2-0776 / A2-1412 / A2-1449: a BOM before hand-edited JSON is
    dropped by every JSON loader, as by the TOML and text readers.

    Formerly: test_fix_a2_errA.TestBomJson"""

    def _bom(self, td, name, doc):
        p = Path(td) / name
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps(doc).encode())
        return p

    def test_loaders_accept_a_bom(self):
        from taxjson.lib.tx_load import load_transactions
        from taxjson.lib.json_input import read_json_doc
        # (migrate's reader of an old missing_history.json)
        from taxjson.lib.missing_history import (
            read_legacy_entries as load_missing_history)
        from taxjson.lib.report_model import load_report_json
        with tempfile.TemporaryDirectory() as td:
            book = self._bom(td, "b.json", {"transactions": [_base_row()]})
            self.assertEqual(len(read_json_doc(book)["transactions"]), 1)
            self.assertEqual(len(load_transactions(book)), 1)
            self.assertIn("transactions", load_report_json(book))
            ph = self._bom(td, "missing_history.json",
                           [{"symbol": "ZZQ.TO", "account": "margin"}])
            self.assertTrue(load_missing_history(ph))

    def test_merge_tools_accept_a_bom(self):
        with tempfile.TemporaryDirectory() as td:
            book = self._bom(td, "b.json", {"transactions": [_base_row()]})
            for mod in ("taxjson_merge", "taxjson_merge2"):
                r = _tool(mod, book)
                self.assertEqual(r.returncode, 0, (mod, r.stderr))
                self.assertEqual(len(json.loads(r.stdout)["transactions"]),
                                 1, mod)


class TestWorkDocShapes(unittest.TestCase):
    """A2-0794 / A2-1408 / A2-0793: read views and tools read work/
    documents through read_work_doc: one line naming the file.

    Formerly: test_fix_a2_errA.TestWorkDocShapes"""

    def test_check_dates_rows(self):
        from taxjson.lib.check_dates import _rows
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_questrade.json"
            self.assertEqual(_rows(p), [])
            for doc in (None, {"transactions": ["x"]},
                        {"transactions": 5},
                        {"transactions": [_base_row(price="x")]}):
                p.write_text(json.dumps(doc))
                with self.assertRaisesRegex(ValueError,
                                            "margin_questrade.json"):
                    _rows(p)

    def test_edge_cases_rows(self):
        from taxjson.lib.edge_cases import _rows
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_base.json"
            for doc in ({"transactions": ["x"]}, {"transactions": 5},
                        {"transactions": [_base_row(quantity="x")]}):
                p.write_text(json.dumps(doc))
                with self.assertRaisesRegex(ValueError, "margin_base.json"):
                    _rows(p)
            p.write_text(json.dumps([_base_row()]))
            self.assertEqual(len(_rows(p)), 1)

    def test_tools_refuse_a_text_number(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            g = td / "margin_gains.json"
            g.write_text(json.dumps(_gains_doc(inv=[{
                "symbol": "ZZQ.TO", "qty": "x", "total_cost": 5.0,
                "currency": "CAD"}])))
            b = td / "margin_base.json"
            b.write_text(json.dumps({"transactions": [
                _base_row(quantity="x")]}))
            dmap = td / "taxjson.toml"     # no [[distributions]]
            dmap.write_text("")
            runs = {
                "taxjson_harvest": ("--no-ibkr", "--country", "canada", g),
                "taxjson_apply_distributions": ("--config", dmap, b),
                "taxjson_split_gains": ("--account", "margin", g),
            }
            for mod, args in runs.items():
                r = _tool(mod, *args, env={"TAXJSON_OFFLINE": "1"})
                self.assertNotIn("Traceback", r.stderr, mod)
                self.assertEqual(r.returncode, 2, (mod, r.stderr))
                self.assertIn("not a number", r.stderr, mod)


if __name__ == "__main__":
    unittest.main()
