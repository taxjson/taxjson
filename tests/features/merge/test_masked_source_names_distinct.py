"""Re-audit-2 fixes: id masking in diagnostics (fix list privacy-02).

A2-0756 / A2-0757 / A2-1381 (every Kraken refid / txid in a note, an
error or a skip summary is masked to its first 2 characters + ***),
A2-0461 (the file-name account-id mask is pinned at the IB and generic
broker diagnostics). Synthetic data only.

Formerly: test_fix_a2_privacy_mask
"""
import os
import tempfile
import unittest
from pathlib import Path

import _paths


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


# ------------------------------------------------------------ A2-0461
_REPO = _paths.REPO


def _brokerage(name, text, *extra):
    import os
    import subprocess
    import sys
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(_REPO / "src"))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_brokerage", *extra,
             str(p)], capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


# ------------------------------------------------------------ A2-0159
_TT_BUY = "BUYSELL 2025-03-04 10:00:00 XEI.TO 100 CAD 25.00 2500.00 9.96\n"
_TT_SELL = "BUYSELL 2025-06-04 10:00:00 XEI.TO -200 CAD 30.00 6000.00 9.96\n"


class TestMaskedSourceNamesStayDistinct(unittest.TestCase):
    """A2-0159: two .tt / generic files whose names differ only in an
    account-number token used to share the masked `source`
    ('manual_55***.tt'), so dedup read them as one file repeating
    itself and dropped a real trade silently.

    Formerly: test_fix_a2_privacy_mask.TestMaskedSourceNamesStayDistinct"""

    def _tt_rows(self, files):
        from taxjson.lib.tt_format import tt_to_json
        rows = []
        with tempfile.TemporaryDirectory() as td:
            for name, text in files.items():
                p = Path(td) / name
                p.write_text(text)
                rows += tt_to_json(p, "margin")["transactions"]
        return rows

    def _plan(self, rows, accts=None):
        from taxjson.bin.taxjson_sort import plan_dedup
        return plan_dedup(rows, accts)

    def test_two_tt_files_named_after_account_numbers_both_book(self):
        rows = self._tt_rows({
            "manual_55500001.tt": _TT_BUY,              # pii-ok
            "manual_55500002.tt": _TT_BUY + _TT_SELL})  # pii-ok
        self.assertEqual(rows[0]["source"], "manual_55***.tt")
        plan = self._plan(rows)
        self.assertEqual(plan.drop, [])
        self.assertEqual(len(plan.keep), 3)
        text = " ".join(plan.attention + plan.notes)
        self.assertIn("Hand-kept .tt files are separate records", text)
        self.assertIn("manual_55***.tt", text)
        self.assertNotIn("55500001", text)  # pii-ok
        self.assertNotIn("55500002", text)  # pii-ok

    def test_run_books_both_buys_and_prints_no_id(self):
        import json
        from _support.tt_fx_project import _cli, _tt_project
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [_TT_BUY.strip()], year=2025)
            d = root / "inputs" / "m"
            (d / "a.tt").rename(d / "manual_55500001.tt")   # pii-ok
            (d / "manual_55500002.tt").write_text(          # pii-ok
                _TT_BUY + _TT_SELL)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            both = r.stdout + r.stderr
            self.assertNotIn("5550000", both)
            self.assertIn("Hand-kept .tt files are separate records", both)
            s = _cli(root, home, "sum", "--json")
            acct = json.loads(s.stdout)["filing"]["accounts"][0]
            self.assertAlmostEqual(acct["acb"], 5000.0)   # both buys
            self.assertAlmostEqual(acct["gain"], 1000.0)

    def test_same_file_twice_still_collapses(self):
        rows = self._tt_rows({"manual_55500001.tt": _TT_BUY})  # pii-ok
        plan = self._plan(rows + [dict(r) for r in rows])
        self.assertEqual(len(plan.drop), 1)

    def test_unmasked_names_keep_their_old_shape(self):
        rows = self._tt_rows({"margin_start.tt": _TT_BUY})
        self.assertEqual(rows[0]["source"], "margin_start.tt")
        self.assertNotIn("source_key", rows[0])

    def test_generic_files_of_two_numbered_names_warn(self):
        # One shared row and one row each: too thin an overlap to call
        # a re-export, so the shared row is kept once WITH the ATTENTION.
        def row(i, src, key):
            return {"id": f"r{i}", "date": f"2025-01-0{i}",
                    "action": "BUYSELL", "symbol": "XEI.TO",
                    "quantity": 100, "source": src, "source_key": key}
        a = [row(1, "generic_55***.csv", "k1"), row(2, "generic_55***.csv",
                                                     "k1")]
        b = [row(1, "generic_55***.csv", "k2"), row(3, "generic_55***.csv",
                                                     "k2")]
        plan = self._plan(a + b)
        self.assertEqual(plan.drop, [2])
        self.assertTrue(any("identical row" in x for x in plan.attention),
                        plan.attention)

    def test_brokerage_stamps_a_key_for_a_masked_name(self):
        import json
        qt = (_REPO / "examples" / "questrade_demo.csv").read_text(
            encoding="utf-8")
        keys = []
        for name in ("55500001_activity.csv",   # pii-ok
                     "55500002_activity.csv"):  # pii-ok
            rc, out, err = _brokerage(name, qt, "--brokerage", "questrade")
            self.assertEqual(rc, 0, err)
            doc = json.loads(out)
            tx = doc["transactions"][0]
            self.assertEqual(tx["source"], "55***_activity.csv")
            keys.append(tx["source_key"])
            self.assertNotIn(name[:8], json.dumps(doc["transactions"]))
            self.assertNotIn(name[:8], json.dumps(
                doc["metadata"].get("source_accounts") or {}))
            for k in (doc["metadata"].get("source_accounts") or {}):
                self.assertIn(tx["source_key"], k)
        self.assertNotEqual(keys[0], keys[1])


if __name__ == "__main__":
    unittest.main()
