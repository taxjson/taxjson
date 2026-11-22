"""Fixes of 2026-10-30, round 7, wave E, group cash: T1135 cash in
transit, export coverage, Kraken swap fees, Coinbase's blank Total and
repeated hour across exports, and the crypto-sends migration on read-only
inputs. One test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented books and accounts, made-up tickers
and ids, small amounts. Each test's files live in a folder of their own.

Formerly: test_fix_1029_r7e_cash
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from _support.cb_price_fill import _HDR as _CB_HDR


def _crypto_project(td, country="canada"):
    root = Path(td) / "c"
    (root / "inputs" / "coin").mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    src = '[]' if country == "canada" else '["CAD"]'
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        f'country = "{country}"\nbase_currency = "{base}"\n'
        f'source_currencies = {src}\n'
        '[accounts.coin]\ntype = "taxable"\ncrypto = true\n')
    (root / "ticker.map").write_text("")
    return root


# ============================================== TJ-1229 the migration on
# read-only inputs
class TestTJ1229MigrationOnReadOnlyInputs(unittest.TestCase):
    """TJ-1229: the read paths saved the moved ids, and a read-only file
    stopped a listing or a run with 'the decision was not saved'.

    Formerly: test_fix_1029_r7e_cash.TestTJ1229MigrationOnReadOnlyInputs"""

    A = ("AAA-SEND-901,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,10,10,10,0,"
         "Sent 1 ZZE\n")
    B = ("BBB-SEND-902,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,11,11,11,0,"
         "Sent 1 ZZE\n")
    BUY = "BUY-0001,2025-03-01 17:00:00 UTC,Buy,ZZE,2,CAD,5,10,10,0,Bought 2 ZZE\n"
    S1, S2 = "cb-20250303T120000-ZZE-1", "cb-20250303T120000-ZZE-1-2"

    def _project(self, td):
        root = _crypto_project(td)
        (root / "inputs/coin/coinbase.csv").write_text(
            _CB_HDR + self.BUY + self.B + self.A)
        tj(root, "run", "--no-input", check=False)
        # Saved on the old numbering: B, the first row, was ...-1.
        (root / "inputs/coin/sends.json").write_text(json.dumps({
            "schema_version": 1, "sends": {
                self.S1: {"decision": "gift", "price": 11.0,
                          "price_currency": "CAD"},
                self.S2: {"decision": "self"}}}))
        return root

    def _read_only(self, root):
        for p in (root / "inputs").rglob("*"):
            p.chmod(p.stat().st_mode & ~0o222)
        (root / "inputs").chmod(0o555)

    def _writable(self, root):
        (root / "inputs").chmod(0o755)
        for p in (root / "inputs").rglob("*"):
            p.chmod(p.stat().st_mode | 0o200)

    def test_the_listing_works_with_the_moved_ids(self):
        if os.geteuid() == 0:
            self.skipTest("root writes read-only files")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            self._read_only(root)
            try:
                before = (root / "inputs/coin/sends.json").read_text()
                r = tj(root, "crypto-sends", "coin", check=False)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("the decision was not saved", r.stderr)
                self.assertIn("hold for this run only", r.stderr)
                doc = json.loads(tj(root, "crypto-sends", "--json",
                                    check=False).stdout)
                sends = {s["id"]: s for s in doc["accounts"]["coin"]["sends"]}
                # B, the 11 send, keeps the gift under its new id (..-1-2).
                self.assertEqual(sends[self.S2]["fair_value"]["price"], 11.0)
                self.assertEqual(sends[self.S2]["decision"], "gift")
                self.assertEqual(sends[self.S1]["decision"], "self")
                self.assertEqual(
                    (root / "inputs/coin/sends.json").read_text(), before)
            finally:
                self._writable(root)
            # Writable again: the move is saved, once.
            r = tj(root, "crypto-sends", "coin", check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads((root / "inputs/coin/sends.json").read_text())
            self.assertEqual(doc["schema_version"], 2)
            self.assertEqual(doc["sends"][self.S2]["decision"], "gift")

    def test_a_decision_on_a_read_only_file_still_says_so(self):
        if os.geteuid() == 0:
            self.skipTest("root writes read-only files")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            self._read_only(root)
            try:
                r = tj(root, "crypto-sends", "coin", "--set",
                       f"{self.S1}=self", check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("the decision was not saved", r.stderr)
            finally:
                self._writable(root)

    def test_load_decisions_gives_the_moved_ids_until_the_file_changes(self):
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            man = Path(td) / "sends.json"
            old = {"a": {"decision": "gift"}}
            man.write_text(json.dumps({"schema_version": 1, "sends": old}))
            CS._UNSAVED_IDS[str(man)] = (old, {
                "schema_version": 2, "sends": {"b": {"decision": "gift"}}})
            self.addCleanup(CS._UNSAVED_IDS.clear)
            self.assertEqual(sorted(CS.load_decisions(man)["sends"]), ["b"])
            man.write_text(json.dumps({"schema_version": 2,
                                       "sends": {"c": {"decision": "self"}}}))
            self.assertEqual(sorted(CS.load_decisions(man)["sends"]), ["c"])
            self.assertNotIn(str(man), CS._UNSAVED_IDS)


if __name__ == "__main__":
    unittest.main()
