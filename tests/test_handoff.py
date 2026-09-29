"""close-year records the hand-off; `taxjson handoff` checks the next
year's project against it (lib/handoff)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price):
    gross = abs(qty) * price
    net = -gross if action == "Buy" else gross
    return (f"{trade} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},D,"
            f"{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,55500001,"  # pii-ok
            f"Trades,Individual\n")


# 2024: XYZ bought; 40 sold on Dec 31, settling Jan 2 2025 (a 2025
# disposition). ABC bought and sold in 2024 (a 2024 disposition).
Y2024 = (_QT_HEADER
         + _qt("2024-03-01", "2024-03-04", "Buy", "XYZ.TO", 100, 10.0)
         + _qt("2024-12-31", "2025-01-02", "Sell", "XYZ.TO", -40, 12.0)
         + _qt("2024-06-03", "2024-06-04", "Buy", "ABC.TO", 50, 20.0)
         + _qt("2024-12-27", "2024-12-30", "Sell", "ABC.TO", -50, 22.0))


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=600)


def _project(root, year, files, extra_settings=""):
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\n'
        f'base_currency = "CAD"\nsource_currencies = []\n{extra_settings}'
        f'[accounts.margin]\ntype = "taxable"\n')
    for name, text in files.items():
        (root / "inputs" / "margin" / name).write_text(text)
    return root


class TestHandoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.p24 = _project(cls.base / "p2024", 2024,
                           {"questrade.csv": Y2024})
        r = _run_cli(cls.p24, "run", "--no-input")
        assert r.returncode == 0, r.stderr
        r = _run_cli(cls.p24, "close-year")
        assert r.returncode == 0, r.stderr
        cls.record = json.loads(
            (cls.p24 / "filed" / "2024.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _p25(self, name, tt):
        rec = self.p24 / "filed" / "2024.json"
        p = _project(self.base / name, 2025, {"start.tt": tt},
                     f'prior_year_record = "{rec}"\n')
        r = _run_cli(p, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr)
        return p

    def test_record_contents(self):
        rec = self.record
        self.assertEqual(rec["schema_version"], 2)
        ye = rec["year_end"]["equity"]
        # The Dec-31 sale settles in 2025: all 100 held on Dec 31.
        self.assertAlmostEqual(ye["XYZ.TO"]["qty"], 100.0)
        self.assertAlmostEqual(ye["XYZ.TO"]["acb"], 1000.0, places=2)
        self.assertNotIn("ABC.TO", ye)
        s = rec["settle_next_year"]
        self.assertEqual([(x["symbol"], x["qty"]) for x in s],
                         [("XYZ.TO", -40.0)])
        self.assertEqual([d["symbol"] for d in rec["dispositions"]],
                         ["ABC.TO"])

    def test_clean_handoff(self):
        p = self._p25("good", (
            "BUYSELL 2024-03-04 09:30:00 XYZ.TO 100 CAD 10 1000 0\n"
            "BUYSELL 2025-01-02 09:30:00 XYZ.TO -40 CAD 12 480 0\n"))
        r = _run_cli(p, "handoff")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Everything the closed year carried forward", r.stdout)

    def test_dec31_sale_dated_in_the_closed_year_is_caught(self):
        # The opening file dates the Dec-31 sale 2024-12-31 (a .tt date
        # is the settle date): it lands in 2024, where it was never
        # reported — the RBC Dec-31 case.
        p = self._p25("dec31", (
            "BUYSELL 2024-03-04 09:30:00 XYZ.TO 100 CAD 10 1000 0\n"
            "BUYSELL 2024-12-31 09:30:00 XYZ.TO -40 CAD 12 480 0\n"))
        r = _run_cli(p, "handoff", "--json")
        self.assertEqual(r.returncode, 1, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual([m["symbol"] for m in rep["missed"]], ["XYZ.TO"])
        pos = {i["symbol"]: i for i in rep["positions"]}
        self.assertAlmostEqual(pos["XYZ.TO"]["closed_qty"], 100.0)
        self.assertAlmostEqual(pos["XYZ.TO"]["opening_qty"], 60.0)

    def test_sale_missing_entirely(self):
        p = self._p25("dropped", (
            "BUYSELL 2024-03-04 09:30:00 XYZ.TO 60 CAD 10 600 0\n"))
        r = _run_cli(p, "handoff", "--json")
        self.assertEqual(r.returncode, 1)
        rep = json.loads(r.stdout)
        self.assertEqual(len(rep["missed"]), 1)

    def test_sale_reported_twice(self):
        # ABC was sold in 2024 (settled Dec 30); this project re-books
        # the same sale on Jan 2.
        p = self._p25("double", (
            "BUYSELL 2024-03-04 09:30:00 XYZ.TO 100 CAD 10 1000 0\n"
            "BUYSELL 2025-01-02 09:30:00 XYZ.TO -40 CAD 12 480 0\n"
            "BUYSELL 2024-06-04 09:30:00 ABC.TO 50 CAD 20 1000 0\n"
            "BUYSELL 2025-01-02 09:30:00 ABC.TO -50 CAD 22 1100 0\n"))
        r = _run_cli(p, "handoff", "--json")
        self.assertEqual(r.returncode, 1)
        rep = json.loads(r.stdout)
        self.assertEqual([d["symbol"] for d in rep["double"]], ["ABC.TO"])

    def test_cost_difference_offers_the_two_choices(self):
        p = self._p25("cost", (
            "BUYSELL 2024-03-04 09:30:00 XYZ.TO 100 CAD 10.5 1050 0\n"
            "BUYSELL 2025-01-02 09:30:00 XYZ.TO -40 CAD 12 480 0\n"))
        r = _run_cli(p, "handoff")
        self.assertEqual(r.returncode, 1)
        self.assertIn("keep 2024 as filed", r.stdout)
        self.assertIn("amend 2024", r.stdout)

    def test_filed_dispositions_import(self):
        with tempfile.TemporaryDirectory() as td:
            import shutil
            p = Path(td) / "p"
            shutil.copytree(self.p24, p)
            csvp = Path(td) / "filed.csv"
            csvp.write_text("symbol,date,qty,proceeds,cost,gain\n"
                            "ABC,2024-12-27,50,1100,1000,100\n")
            r = _run_cli(p, "close-year", "--force",
                         "--filed-dispositions", str(csvp))
            self.assertEqual(r.returncode, 0, r.stderr)
            rec = json.loads((p / "filed" / "2024.json").read_text())
            self.assertEqual(rec["filed_totals"]["gain"], 100.0)
            bad = Path(td) / "bad.csv"
            bad.write_text("symbol,qty\nABC,1\n")
            r = _run_cli(p, "close-year", "--force",
                         "--filed-dispositions", str(bad))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("missing column", r.stderr)


if __name__ == "__main__":
    unittest.main()


class TestChecklistStep(unittest.TestCase):
    def _ctx(self, root, settings, out, code=0):
        from datetime import date as _date
        from taxjson.lib.checklist import Ctx
        return Ctx(root=Path(root), cfg={"settings": settings}, year=2025,
                   today=_date(2026, 3, 1),
                   run_sub=lambda argv, timeout=900: (code, out, ""))

    def test_no_record_is_manual(self):
        from taxjson.lib.checklist import d_handoff
        with tempfile.TemporaryDirectory() as td:
            r = d_handoff(self._ctx(td, {"year": 2025}, ""))
            self.assertEqual(r.status, "manual")

    def test_problems_need_attention(self):
        from taxjson.lib.checklist import d_handoff
        doc = json.dumps({"problems": 2, "positions": [{}], "missed": [{}],
                          "double": []})
        with tempfile.TemporaryDirectory() as td:
            r = d_handoff(self._ctx(td, {"prior_year_record": "x.json"},
                                    doc, code=1))
            self.assertEqual(r.status, "attention")
            self.assertIn("missing", r.detail)
            r = d_handoff(self._ctx(td, {"prior_year_record": "x.json"},
                                    json.dumps({"problems": 0})))
            self.assertEqual(r.status, "done")
