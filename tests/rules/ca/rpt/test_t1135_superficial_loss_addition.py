"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic.

Formerly: test_fix_misc"""
import unittest

import _paths


class TestT1135SuperficialLossAddition(unittest.TestCase):
    """G7-0: the T1135 cost amount includes the s.53(1)(f) addition the
    engine makes to the replacement property's ACB.

    Formerly: test_fix_misc.TestT1135SuperficialLossAddition"""

    def _report(self, rows, extra_rows=()):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from taxjson.bin.taxjson_t1135 import build_report
        repo = _paths.REPO
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": rows}))
            g = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains", "--country", "canada",
                 "--year", "2025", "--taxable", str(base)],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                env=dict(os.environ, PYTHONPATH=str(repo / "src")))
            self.assertEqual(g.returncode, 0, g.stderr[-2000:])
            gains = Path(tmp) / "margin_gains.json"
            gains.write_text(g.stdout)
            doc = json.loads(g.stdout)
            rep = build_report([base], [gains], 2025, {}, "CAD")
        return rep, doc

    @staticmethod
    def _t(i, d, sym, q, p):
        return dict(id=i, date=d, date_settle=d, time="10:00:00", action="BUYSELL",
                    symbol=sym, quantity=q, price=p, gross_amount=abs(q * p),
                    net_amount=abs(q * p), commission=0.0, fee=0.0, currency="CAD",
                    account="margin")

    def test_denied_loss_raises_the_replacement_cost(self):
        rows = [self._t("a1", "2024-06-03", "XYZ.US", 100, 10),
                self._t("a2", "2025-03-03", "XYZ.US", -100, 5),
                self._t("a3", "2025-03-10", "XYZ.US", 100, 5)]
        rep, doc = self._report(rows)
        self.assertEqual(doc["summary"]["total_disallowed"], 500.0)
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 1000.0)            # 500 paid + 500 denied
        self.assertEqual(p["max_cost"], 1000.0)
        self.assertEqual(rep["max_total_cost"], 1000.0)

    def test_partial_sale_after_the_addition(self):
        rows = [self._t("a1", "2024-06-03", "XYZ.US", 100, 10),
                self._t("a2", "2025-03-03", "XYZ.US", -100, 5),
                self._t("a3", "2025-03-10", "XYZ.US", 100, 5),
                self._t("a4", "2025-09-10", "XYZ.US", -40, 20)]
        rep, _doc = self._report(rows)
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        self.assertEqual(p["year_end_cost"], 600.0)             # 60 of 100 at 10.00

    def test_replacement_bought_before_the_losing_sale(self):
        rows = [self._t("a1", "2024-06-03", "XYZ.US", 100, 10),
                self._t("a2", "2025-03-03", "XYZ.US", 100, 5),     # replacement first
                self._t("a3", "2025-03-10", "XYZ.US", -100, 5)]    # then the loss
        rep, doc = self._report(rows)
        denied = doc["summary"]["total_disallowed"]
        self.assertGreater(denied, 0)
        (p,) = [r for r in rep["properties"] if r["symbol"] == "XYZ.US"]
        inv = {i["symbol"]: i for i in doc["inventory"]}
        self.assertEqual(p["year_end_cost"], round(inv["XYZ.US"]["total_cost"], 2))


if __name__ == "__main__":
    unittest.main()
