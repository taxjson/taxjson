"""Low-round fixes in the misc area (fees report, merge2, cross-listing
lint, web UI, watchlist export, generate-parser, crypto money parsing,
the PII gate and dev scripts). Synthetic data only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PY = sys.executable


def _run(mod, *args, env=None, input=None):
    e = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")
    if env:
        e.update(env)
    return subprocess.run([PY, "-m", mod, *args], capture_output=True,
                          text=True, env=e, input=input,
                          stdin=None if input is not None else subprocess.DEVNULL)


def _write_json(path, doc):
    Path(path).write_text(json.dumps(doc), encoding="utf-8")


def _trade(i, date, fee, account="margin", symbol="XYZ.US", currency="USD"):
    return {"id": f"t{i}", "action": "BUYSELL", "date": date,
            "date_settle": date, "symbol": symbol, "quantity": 10,
            "price": 10.0, "gross_amount": 100.0, "net_amount": 100.0 + fee,
            "fee": fee, "currency": currency, "account": account}


class TestFeesSum(unittest.TestCase):
    """taxjson-fees-sum (taxjson_fees.py)."""

    def _files(self, d):
        wb = Path(d) / "margin_webull.json"
        _write_json(wb, {"metadata": {"source_brokerage": "webull"},
                         "transactions": [_trade(1, "2025-03-03", 2.0)]})
        tt = Path(d) / "margin_wb_manual.json"
        _write_json(tt, {"metadata": {"source_brokerage": "manual (.tt)"},
                         "transactions": [_trade(2, "2026-06-18", 2.0)]})
        return wb, tt

    def test_manual_tt_fees_do_not_make_a_broker_fee_free(self):
        # R1-101: Webull's 2026 trades live only in a .tt; the footer
        # said 'Brokers with NO fees in this period: webull'.
        with tempfile.TemporaryDirectory() as d:
            wb, tt = self._files(d)
            r = _run("taxjson.bin.taxjson_fees", str(wb), str(tt),
                     "--year", "2026")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Brokers with NO fees", r.stdout)
            self.assertIn("not attributed to a broker", r.stdout)
            self.assertIn("webull", r.stdout)
            j = _run("taxjson.bin.taxjson_fees", str(wb), str(tt),
                     "--year", "2026", "--json")
            meta = json.loads(j.stdout)["meta"]
            self.assertTrue(meta["manual_tt_fees_unattributed"])
            self.assertEqual(meta["zero_fee_brokers"], ["webull"])

    def test_zero_fee_claim_kept_without_manual_rows(self):
        with tempfile.TemporaryDirectory() as d:
            wb, _ = self._files(d)
            other = Path(d) / "margin_ib.json"
            _write_json(other, {"metadata": {"source_brokerage": "ib"},
                                "transactions": [_trade(3, "2026-02-02", 1.0)]})
            r = _run("taxjson.bin.taxjson_fees", str(wb), str(other),
                     "--year", "2026")
            self.assertIn("Brokers with NO fees in this period: webull",
                          r.stdout)

    def test_year_help_names_the_trade_date(self):
        # R1-154 / R1-289: the help said '(settlement) date'.
        r = _run("taxjson.bin.taxjson_fees", "--help")
        self.assertNotIn("(settlement) date", r.stdout)
        self.assertIn("TRADE date", r.stdout)

    def test_since_must_be_an_iso_date(self):
        # S031-06: '2025-6-1' compared as a string dropped every fee.
        with tempfile.TemporaryDirectory() as d:
            wb, _ = self._files(d)
            for bad in ("2025-6-1", "2025/06/01", "June-2025", "banana"):
                r = _run("taxjson.bin.taxjson_fees", str(wb), "--since", bad)
                self.assertEqual(r.returncode, 2, bad)
                self.assertIn("YYYY-MM-DD", r.stderr)
            ok = _run("taxjson.bin.taxjson_fees", str(wb),
                      "--since", "2025-01-01")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("webull", ok.stdout)

    def test_by_account_without_account_is_not_None(self):
        # S031-03
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "q.json"
            row = _trade(1, "2025-04-01", 4.95, currency="CAD")
            del row["account"]
            _write_json(p, {"metadata": {"source_brokerage": "questrade"},
                            "transactions": [row]})
            r = _run("taxjson.bin.taxjson_fees", str(p), "--by-account")
            self.assertNotIn("None", r.stdout)
            self.assertIn("questrade/?", r.stdout)
            j = _run("taxjson.bin.taxjson_fees", str(p), "--by-account",
                     "--json")
            self.assertEqual(list(json.loads(j.stdout)["brokerages"]),
                             ["questrade/?"])


class TestMerge2DefaultRateWarning(unittest.TestCase):
    def test_warning_names_the_rate_actually_used(self):
        # R1-155: the warning printed '--default-rate (None)' while the
        # rows were converted at 1.35.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tx.json"
            _write_json(p, {"transactions": [_trade(1, "2025-03-03", 1.0)]})
            r = _run("taxjson.bin.taxjson_merge2", str(p), "--to", "CAD")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("--default-rate (1.35)", r.stderr)
            self.assertNotIn("(None)", r.stderr)


if __name__ == "__main__":
    unittest.main()
