"""Re-audit-2 fixes, filing-reports list (helper B): option-boundary,
reconcile-slips, Schedule 3 option units, the .sum per-asset block and
the fees report.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_b
"""
import unittest
from tax_rules import rule
from taxjson.rules import ids as R
import _paths


if __name__ == "__main__":
    unittest.main()


class TestSumGainsGrantWrites(unittest.TestCase):
    """A2-1114: the .sum per-asset block counts a grant-timing write as
    one trade whose result is its premium plus a same-year buy-back.

    Formerly: test_fix_a2_filrep_b.TestSumGainsGrantWrites"""

    @staticmethod
    def _e(sym, date, qty, gain, grant=False, gc=None, days=0):
        e = {"symbol": sym, "date": date, "date_settle": date, "qty": qty,
             "gain": gain, "cost": -gain if grant else 0.0,
             "proceeds": 0.0 if grant else gain, "currency": "CAD",
             "direction": "SHORT", "grant": grant, "days_held": days}
        if gc is not None:
            e["grant_closed"] = gc
        return e

    def _dollars(self, txs):
        from taxjson.lib.gain_summary import summarize_gains
        out = summarize_gains({"transactions": txs,
                               "summary": {"year": 2025}})
        return sorted(round(x, 2) for x in
                      out["returns_by_asset"]["Options"]["CAD"]["dollars"])

    @rule(R.CA_OPT_01)
    def test_a2_1114_write_premium_in_per_asset_block(self):
        A, B = "XYZ250620C00060000.TO", "XYZ250718C00065000.TO"
        txs = [self._e(A, "2025-03-04", 2, 600.0, grant=True),
               self._e(B, "2025-03-04", 1, 200.0, grant=True),
               self._e(B, "2025-04-02", 1, -50.0,
                       gc={"2025": {"units": 1.0, "premium": 200.0}},
                       days=29)]
        self.assertEqual(self._dollars(txs), [150.0, 600.0])
        # a gains file written before grant_closed: same pairing
        txs[2].pop("grant_closed")
        self.assertEqual(self._dollars(txs), [150.0, 600.0])

    @rule(R.CA_OPT_01)
    def test_a2_1114_prior_year_write_buyback_is_its_own_trade(self):
        B = "XYZ250718C00065000.TO"
        txs = [self._e(B, "2025-02-03", 1, -100.0,
                       gc={"2024": {"units": 1.0, "premium": 300.0}}),
               self._e(B, "2025-03-04", 1, 250.0, grant=True)]
        self.assertEqual(self._dollars(txs), [-100.0, 250.0])


# ---------------------------------------------------------------------
# fees report
# ---------------------------------------------------------------------

def _fees_run(*args):
    import os
    import subprocess
    import sys
    src = _paths.REPO / "src"
    env = dict(os.environ, PYTHONPATH=str(src), TAXJSON_OFFLINE="1")
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_fees",
                           *args], capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL)


def _fee_trade(i, date, fee, currency="CAD", symbol="XYZ.TO"):
    return {"id": f"t{i}", "action": "BUYSELL", "date": date,
            "date_settle": date, "symbol": symbol, "quantity": 10,
            "price": 10.0, "gross_amount": 100.0, "net_amount": 100.0 + fee,
            "fee": fee, "currency": currency, "account": "margin"}


def _write(path, doc):
    import json
    path.write_text(json.dumps(doc), encoding="utf-8")


class TestFeesReport(unittest.TestCase):
    """Formerly: test_fix_a2_filrep_b.TestFeesReport"""

    def test_a2_0645_to_currency_spelling_normalised(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "margin_questrade.json"
            _write(f, {"metadata": {"source_brokerage": "questrade"},
                       "transactions": [_fee_trade(1, "2025-03-03", 9.99),
                                        _fee_trade(2, "2025-03-04", 9.99)]})
            rates = Path(d) / "to_base.csv"
            rates.write_text("2025-03-03 00:00:00 USD CAD 1.40\n")
            outs = []
            for to in ("CAD", "cad", " CAD"):
                r = _fees_run(str(f), "--year", "2025", "--to", to,
                              "--rates", str(rates))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("default-rate", r.stderr + r.stdout)
                outs.append([ln for ln in r.stdout.splitlines()
                             if ln.startswith("TOTAL")])
            self.assertEqual(outs[0], outs[1])
            self.assertEqual(outs[0], outs[2])
            self.assertIn("19.98", outs[0][0])

    def test_a2_0646_unnamed_generic_bucket_caveats_zero_fee_claim(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            q = Path(d) / "margin_questrade.json"
            _write(q, {"metadata": {"source_brokerage": "questrade"},
                       "transactions": [_fee_trade(1, "2024-03-03", 0.0)]})
            g = Path(d) / "margin_generic_x.json"
            _write(g, {"metadata": {"source_brokerage": "generic"},
                       "transactions": [_fee_trade(2, "2025-03-03", 10.0)]})
            r = _fees_run(str(q), str(g), "--year", "2025")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Brokers with NO fees", r.stdout)
            self.assertIn("questrade", r.stdout)
            self.assertIn("not attributed to a broker", r.stdout)
            self.assertIn("[broker] name", r.stdout)

    def test_a2_1102_title_names_the_trade_date_window(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "margin_questrade.json"
            _write(f, {"metadata": {"source_brokerage": "questrade"},
                       "transactions": [_fee_trade(1, "2025-03-03", 9.99)]})
            r = _fees_run(str(f), "--year", "2025")
            self.assertIn("tax year 2025, by TRADE date", r.stdout)
            j = _fees_run(str(f), "--year", "2025", "--json")
            self.assertEqual(json.loads(j.stdout)["meta"]["date_basis"],
                             "trade")
