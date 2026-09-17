"""CLI end-to-end for taxjson-missing-history (the report glue, no IBKR).

The lib detectors are covered by test_missing_history.py; this exercises
main()'s load → detect → section-printing path, which had zero coverage.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_missing_history import main


def _tx(action, date, symbol, qty, net=0.0, desc=""):
    return dict(action=action, date=date, time="09:30:00", symbol=symbol,
                quantity=qty, net_amount=net, currency="USD", account="margin",
                description=desc)


_TXS = [
    # A truncated-history short: sells with no prior buy.
    _tx("BUYSELL", "2025-06-01", "FOO.US", -10, 2000.0),
    # A merger fragmented across a temp symbol + the acquirer, then sold.
    _tx("BUYSELL", "2025-07-21", "H015283.US", -15, 0.0,
        "MGR - HESS CORPORATION MERGER TO CHEVRON CORPORATION 1.025 NEW = 1 OLD"),
    _tx("BUYSELL", "2025-07-21", "CVX.US", 15, 0.0,
        "MGR - CHEVRON CORPORATION SHRS RECEIVED THRU MERGER"),
    _tx("BUYSELL", "2025-12-23", "CVX.US", -15, 3000.0, "sale"),
    # A clean, fully-known position — must NOT be flagged.
    _tx("BUYSELL", "2025-02-01", "OK.US", 100, 5000.0),
    _tx("BUYSELL", "2025-09-01", "OK.US", -100, 6000.0),
]


def _run(args):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "margin_base.json"
        f.write_text(json.dumps({"transactions": _TXS}))
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(args + [str(f)])
        return rc, out.getvalue()


class TestMissingHistoryCli(unittest.TestCase):
    def test_reports_all_three_categories(self):
        rc, out = _run(["--year", "2025"])
        self.assertEqual(rc, 0)
        # Merger reconstructed and labelled with both symbols.
        self.assertIn("Reconstructed mergers", out)
        self.assertIn("H015283.US", out)
        self.assertIn("CVX.US", out)
        # Truncated-history short surfaced.
        self.assertIn("Truncated history", out)
        self.assertIn("FOO.US", out)
        # The clean position is not flagged anywhere.
        self.assertNotIn("OK.US", out)

    def test_clean_data_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "b.json"
            f.write_text(json.dumps({"transactions": [
                _tx("BUYSELL", "2025-02-01", "OK.US", 100, 5000.0),
                _tx("BUYSELL", "2025-09-01", "OK.US", -100, 6000.0),
            ]}))
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["--year", "2025", str(f)])
            self.assertEqual(rc, 0)
            self.assertIn("No missing-cost-basis issues", out.getvalue())


if __name__ == "__main__":
    unittest.main()
