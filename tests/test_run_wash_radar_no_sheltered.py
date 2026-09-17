"""`taxjson run` must generate the wash-radar report for a taxable account even
when the project defines NO registered (sheltered) account.

Previously `stage_cross_reports` gated the radar on `taxable_equity_base AND
sheltered_base`, so a taxable-only project produced no wash_radar_<account>.rpt
at all — even though same-account superficial-loss detection is fully relevant
without any registered account. The `--sheltered` file only adds the
cross-account (registered-repurchase) column and is optional.
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_run import stage_cross_reports


class TestWashRadarWithoutSheltered(unittest.TestCase):
    def test_radar_generated_when_sheltered_base_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            base = root / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2026-01-05", "time": "09:30:00",
                 "symbol": "XYZ.TO", "quantity": 100, "price": 20,
                 "net_amount": 2000, "currency": "CAD", "account": "margin"}]}))
            gains = root / "margin_gains.json"
            gains.write_text(json.dumps(
                {"transactions": [], "summary": {"year": "2026"}}))

            # sheltered_base=None is the taxable-only project case.
            stage_cross_reports([gains], [base], None, reports)

            rpt = reports / "wash_radar_margin.rpt"
            self.assertTrue(rpt.exists(),
                            "radar must generate without a sheltered account")
            body = rpt.read_text()
            self.assertIn("XYZ.TO", body)
            self.assertIn("TICKER", body)


if __name__ == "__main__":
    unittest.main()
