"""Deferred round, engine-t1135 area: the T1135 cost walk carries every
s.53(1)(f) addition (S008-07, S009-01, S051-21)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO = Path(__file__).resolve().parent.parent


def _t(i, d, sym, q, p, account="margin", time="10:00:00", settle=None):
    amt = abs(q * p)
    return dict(id=i, date=d, date_settle=settle or d, time=time,
                action="BUYSELL", symbol=sym, quantity=q, price=p,
                gross_amount=amt, net_amount=-amt if q > 0 else amt,
                commission=0.0, fee=0.0, currency="CAD", account=account)


def _gains(base: Path, year: int, out: Path, *extra) -> dict:
    g = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_gains", "--country",
         "canada", "--year", str(year), "--taxable", *extra, str(base)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env=dict(os.environ, PYTHONPATH=str(REPO / "src")))
    assert g.returncode == 0, g.stderr[-2000:]
    out.write_text(g.stdout)
    return json.loads(g.stdout)


class TestT1135EarlierYearDenial(unittest.TestCase):
    """A superficial loss denied in an EARLIER year stays in the
    replacement's ACB, so it stays in its T1135 cost amount."""

    def _report(self, rows, year, sheltered=None, **kw):
        from taxjson.bin.taxjson_t1135 import build_report
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": rows}))
            extra = []
            sh_paths = []
            if sheltered is not None:
                sh = Path(tmp) / "sheltered_base.json"
                sh.write_text(json.dumps({"transactions": sheltered}))
                extra = ["--sheltered", str(sh)]
                sh_paths = [sh]
            gains = Path(tmp) / "margin_gains_wash.json"
            doc = _gains(base, year, gains, *extra)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rep = build_report([base], [gains], year, {}, "CAD",
                                   sheltered_paths=sh_paths, **kw)
        return rep, doc

    @rule("CA-RPT-12")
    def test_s008_07_denial_in_prior_year_lifts_the_threshold(self):
        """S008-07: buy 1000 @120, sell @90, rebuy @90 within 30 days,
        all in 2025. The 2026 cost amount is 120,000 — over 100,000."""
        rows = [_t("b1", "2025-01-06", "XYZ.US", 1000, 120),
                _t("s1", "2025-06-02", "XYZ.US", -1000, 90),
                _t("b2", "2025-06-09", "XYZ.US", 1000, 90)]
        rep, doc = self._report(rows, 2026)
        inv = {i["symbol"]: i for i in doc["inventory"]}
        self.assertAlmostEqual(inv["XYZ.US"]["total_cost"], 120000.0, 2)
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 120000.0)
        self.assertEqual(p["max_cost"], 120000.0)
        self.assertEqual(rep["max_total_cost"], 120000.0)
        self.assertTrue(rep["filing_required"])
        self.assertEqual(rep["deferred_wash_not_in_cost"], {})

    @rule("CA-RPT-12")
    def test_s009_01_partial_sale_and_later_year(self):
        """S009-01 / S051-21: the addition leaves with the shares sold
        (average cost), the rest stays in the Dec-31 cost of a later
        year — equal to the engine's inventory ACB."""
        rows = [_t("b1", "2024-03-01", "AAPL.US", 100, 200),
                _t("s1", "2024-11-20", "AAPL.US", -100, 100),
                _t("b2", "2024-12-02", "AAPL.US", 100, 100),
                _t("s2", "2025-04-01", "AAPL.US", -40, 150)]
        rep, doc = self._report(rows, 2025)
        inv = {i["symbol"]: i for i in doc["inventory"]}
        (p,) = [r for r in rep["properties"] if r["symbol"] == "AAPL.US"]
        self.assertAlmostEqual(inv["AAPL.US"]["total_cost"], 12000.0, 2)
        self.assertEqual(p["year_end_cost"], 12000.0)      # 60 x 200
        self.assertEqual(p["max_cost"], 20000.0)           # Jan 1: 100 x 200

    @rule("CA-RPT-12")
    def test_replacement_bought_before_the_loss_in_prior_year(self):
        """The engine applies a pre-loss replacement's bump right after
        the losing sale; the walk replays it there."""
        rows = [_t("b1", "2024-03-01", "XYZ.US", 100, 10),
                _t("b2", "2024-11-01", "XYZ.US", 100, 5),
                _t("s1", "2024-11-08", "XYZ.US", -100, 5)]
        rep, doc = self._report(rows, 2025)
        inv = {i["symbol"]: i for i in doc["inventory"]}
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertGreater(inv["XYZ.US"]["deferred_wash"], 0)
        self.assertEqual(p["year_end_cost"],
                         round(inv["XYZ.US"]["total_cost"], 2))

    @rule("CA-RPT-12")
    def test_sheltered_replacement_adds_nothing(self):
        """A replacement bought in a registered account makes the loss
        permanently denied: no addition to the taxable cost."""
        rows = [_t("b1", "2024-03-01", "XYZ.US", 100, 10),
                _t("s1", "2024-11-08", "XYZ.US", -50, 5)]
        sh = [_t("r1", "2024-11-15", "XYZ.US", 50, 5, account="tfsa")]
        rep, doc = self._report(rows, 2025, sheltered=sh)
        inv = {i["symbol"]: i for i in doc["inventory"]}
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 500.0)
        self.assertAlmostEqual(inv["XYZ.US"]["total_cost"], 500.0, 2)


class TestT1135WrapperFullHistory(unittest.TestCase):
    """`taxjson t1135` in a project: the 2025 denial reaches the 2026
    cost columns."""

    @rule("CA-RPT-12")
    def test_project_year_after_the_denial(self):
        book = ("BUYSELL 2025-01-06 10:00:00 XYZ.US 1000 CAD 120 120000 0\n"
                "BUYSELL 2025-06-02 10:00:00 XYZ.US -1000 CAD 90 90000 0\n"
                "BUYSELL 2025-06-09 10:00:00 XYZ.US 1000 CAD 90 90000 0\n")
        env = dict(os.environ, TAXJSON_OFFLINE="1",
                   PYTHONPATH=str(REPO / "src"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "book.tt").write_text(book)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')

            def cli(*args):
                return subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                     str(root), *args], capture_output=True, text=True,
                    env=env, stdin=subprocess.DEVNULL)
            r = cli("run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = cli("t1135", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            rep = json.loads(r.stdout)
        (p,) = [x for x in rep["properties"] if x["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 120000.0)
        self.assertTrue(rep["filing_required"])


if __name__ == "__main__":
    unittest.main()
