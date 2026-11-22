"""Regressions for FUZZ-2026-07 confirmed findings (minimal repros).

Formerly: test_fuzz_fixes"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import _paths

REPO_ROOT = _paths.REPO


def _tx(d, q, p, sym="NNN.TO", acct="margin"):
    return {"action": "BUYSELL", "date": d, "date_settle": d,
            "time": "09:30:00", "symbol": sym, "quantity": q, "price": p,
            "net_amount": abs(q) * p, "currency": "CAD", "account": acct}


def _gains(country, txs, extra=()):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "b.json"
        f.write_text(json.dumps({"transactions": txs}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_gains",
             "--country", country, "--year", "2025", "--taxable",
             *extra, str(f)],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)


class TestFuzzK_InputHardening(unittest.TestCase):
    """#K: garbage inputs must fail CLEANLY (message + exit 1/2), never
    a stack trace or silent corruption.

    Formerly: test_fuzz_fixes.TestFuzzK_InputHardening"""

    def _gains_raw(self, txs, country="canada"):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "b.json"
            f.write_text(json.dumps(txs))
            return subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", country, "--taxable", str(f)],
                cwd=REPO_ROOT, capture_output=True, text=True)

    def test_nan_rejected_cleanly_both_engines(self):
        bad = {"transactions": [{
            "action": "BUYSELL", "date": "2025-01-05",
            "time": "09:30:00", "symbol": "B.TO",
            "quantity": float("nan"), "price": 1.0, "net_amount": 1.0,
            "currency": "CAD", "account": "m"}]}
        for country in ("canada", "usa"):
            r = self._gains_raw(bad, country)
            self.assertEqual(r.returncode, 2, country)
            self.assertNotIn("Traceback", r.stderr, country)
            self.assertIn("non-finite", r.stderr, country)

    def test_split_ratio_zero_rejected(self):
        bad = {"transactions": [
            {"action": "BUYSELL", "date": "2025-01-10",
             "time": "09:30:00", "symbol": "SPL.TO", "quantity": 100,
             "price": 10.0, "net_amount": 1000.0, "currency": "CAD",
             "account": "m"},
            {"action": "SPLIT", "date": "2025-02-10", "time": "09:30:00",
             "symbol": "SPL.TO", "quantity": 0, "currency": "CAD",
             "account": "m"}]}
        r = self._gains_raw(bad)
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPLIT ratio must be > 0", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_impossible_date_rejected_and_validated(self):
        bad = {"transactions": [{
            "action": "BUYSELL", "date": "2025-02-29",
            "time": "09:30:00", "symbol": "A.TO", "quantity": 1,
            "price": 1.0, "net_amount": 1.0, "currency": "CAD",
            "account": "m"}]}
        r = self._gains_raw(bad)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Impossible", r.stderr.replace("impossible",
                                                     "Impossible"))
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "b.json"
            f.write_text(json.dumps(bad))
            v = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_validate",
                 str(f)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(v.returncode, 1)
        self.assertIn("Impossible date", v.stdout + v.stderr)


class TestFuzzE_ShortConventionCanonical(unittest.TestCase):
    """#5/#6/#21: ONE cross-engine convention for shorts.

    Disposition rows: cost = -opening premium, proceeds = -close cost,
    so gain == proceeds - cost direction-free (the documented signed
    cash-flow convention — USA had the legs swapped). Inventory:
    total_cost NEGATIVE (proceeds credited; cost/share positive — the
    holdings-export convention; Canada leaked its internal positive
    sign through). ccd-sum's PREMIUM/BUYBACK identity follows.

    Formerly: test_fuzz_fixes.TestFuzzE_ShortConventionCanonical
    """

    CALL = "QZX250321C00012500.TO"

    @classmethod
    def _open_close(cls):
        sto = _tx("2025-01-05", -1, 1.0, sym=cls.CALL)   # premium 100 in
        sto["net_amount"] = 100.0
        btc = _tx("2025-02-10", 1, 0.4, sym=cls.CALL)    # buyback 40 out
        btc["net_amount"] = 40.0
        return [sto, btc]

    @staticmethod
    def _project(tmp, country, gains):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            f'[settings]\nyear = 2025\ncountry = "{country}"\n'
            f'base_currency = "{"USD" if country == "usa" else "CAD"}"\n'
            'source_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps(gains))
        return root

    def test_rows_identical_and_identity_holds_both_engines(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                t = _gains(country, self._open_close())["transactions"][0]
                self.assertEqual(t["direction"], "SHORT")
                self.assertAlmostEqual(t["cost"], -100.0, places=2)
                self.assertAlmostEqual(t["proceeds"], -40.0, places=2)
                self.assertAlmostEqual(t["gain"], 60.0, places=2)
                self.assertAlmostEqual(
                    t["gain"], t["proceeds"] - t["cost"], places=2)

    def test_short_inventory_negative_cost_both_engines(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                inv = _gains(country,
                             self._open_close()[:1])["inventory"][0]
                self.assertAlmostEqual(inv["qty"], -1.0)
                self.assertAlmostEqual(inv["total_cost"], -100.0,
                                       places=2)   # credit, not cost

    def test_ccd_sum_premium_buyback_identity_both_engines(self):
        # End-to-end: engine gains file → ccd-sum. PREMIUM/BUYBACK were
        # negated-and-swapped (canada) or identity-breaking (usa).
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as tmp:
                root = self._project(tmp, country,
                                     _gains(country, self._open_close()))
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run",
                     "-C", str(root), "ccd-sum", "--json"],
                    cwd=REPO_ROOT, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                row = json.loads(r.stdout)["rows"][0]
                self.assertAlmostEqual(row["proceeds"], 100.0, places=2)
                self.assertAlmostEqual(row["cost"], 40.0, places=2)
                self.assertAlmostEqual(row["gain"], 60.0, places=2)

    def test_list_cost_and_cost_per_share_convention(self):
        # Short row: COST -100 (credit) with COST/SH positive — signed
        # division, matching the holdings export convention. COST/SH of
        # an equity option is per SHARE (100 a contract), like harvest
        # and the holdings report (S045-03): 1 contract, 100 -> 1.00.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                tmp, "canada", _gains("canada", self._open_close()[:1]))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run",
                 "-C", str(root), "list", "--json"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        row = json.loads(r.stdout)["rows"][0]
        self.assertAlmostEqual(row["cost"], -100.0, places=2)
        self.assertAlmostEqual(row["cost_per_share"], 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
