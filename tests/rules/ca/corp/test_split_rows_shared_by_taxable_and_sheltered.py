"""Regression tests for the 2026-07 audit tier-1 fixes:

1. run_to_file: a failed stage must not leave a truncated output file that the
   mtime cache then treats as fresh (previous good output must survive).
2. `tjs fees` counts standalone FEE-action rows (amount in net_amount).
3. IB dividend/withholding reversal rows keep their sign and net out instead
   of being abs()'d into extra income/tax.
4. Wash radar: 30-day windows are DST-immune (UTC-noon epochs) — day 30
   across the November fall-back is still inside the window.
5. Wash radar: the VIOLATION (substituted-property) test uses the CRA window
   around the LOSS SALE, not the last 30 days from today; fully-exited losses
   route to COOLING.
6. Engines apply one corporate SPLIT once even when merged multi-account
   input carries one SPLIT row per account.

Formerly: test_audit_tier1_fixes
"""
import json
import tempfile
import unittest
from pathlib import Path


def _tx(date, sym, qty, net, account="margin"):
    return {"action": "BUYSELL", "date": date, "time": "09:30:00",
            "symbol": sym, "quantity": qty, "price": abs(net / qty) if qty else 0,
            "net_amount": abs(net), "currency": "CAD", "account": account}


class TestSplitDedup(unittest.TestCase):
    """Formerly: test_audit_tier1_fixes.TestSplitDedup"""
    def _split(self, date, sym, ratio, account):
        return {"action": "SPLIT", "date": date, "time": "09:30:00",
                "symbol": sym, "quantity": ratio, "net_amount": 0.0,
                "currency": "CAD", "account": account}

    def test_canada_duplicate_split_rows_apply_once(self):
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        from taxjson.lib.tx_load import load_transactions
        # 100 shares in each of two accounts; each broker emits its own SPLIT
        # row for the same 2:1 event; sell all 400 real post-split shares at
        # the (post-split) cost price → true gain 0.
        rows = [
            _tx("2026-01-05", "SPL.TO", 100, 1000.0, account="acct1"),
            _tx("2026-01-05", "SPL.TO", 100, 1000.0, account="acct2"),
            self._split("2026-02-01", "SPL.TO", 2.0, "acct1"),
            self._split("2026-02-01", "SPL.TO", 2.0, "acct2"),
            _tx("2026-03-01", "SPL.TO", -400, 2000.0, account="acct1"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "in.json"
            p.write_text(json.dumps({"transactions": rows}))
            txs = load_transactions(p)
        res = CanadaTaxRules().compute_gains(txs)
        gains = [g for g in res["transactions"]
                 if g.get("symbol") == "SPL.TO" and g.get("qty")]
        total_gain = sum(float(g.get("gain") or 0) for g in gains)
        self.assertAlmostEqual(total_gain, 0.0, places=2,
                               msg="double-applied split inflates the gain")
        # No ghost shares left in inventory.
        inv = [h for h in res["inventory"] if h["symbol"] == "SPL.TO"]
        self.assertTrue(all(abs(h["qty"]) < 1e-6 for h in inv), inv)

    def test_dedupe_shared_across_taxable_and_sheltered(self):
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        from taxjson.model.transaction import TaxTransaction
        # The same split arriving via BOTH the taxable list and the sheltered
        # context must still be applied exactly once to the taxable pool.
        def T(**kw):
            base = dict(action="BUYSELL", date="2026-01-05", time="09:30:00",
                        symbol="SPL2.TO", quantity=0.0, price=0.0,
                        net_amount=0.0, currency="CAD", account="m")
            base.update(kw)
            return TaxTransaction(**base)
        taxable = [
            T(quantity=100, price=10.0, net_amount=1000.0),
            T(action="SPLIT", date="2026-02-01", quantity=2.0),
            T(date="2026-03-01", quantity=-200, price=5.0, net_amount=1000.0),
        ]
        sheltered = [
            T(action="SPLIT", date="2026-02-01", quantity=2.0, account="rrsp"),
        ]
        res = CanadaTaxRules().compute_gains(taxable,
                                             sheltered_transactions=sheltered)
        inv = [h for h in res["inventory"] if h["symbol"] == "SPL2.TO"]
        self.assertTrue(all(abs(h["qty"]) < 1e-6 for h in inv),
                        f"sheltered copy of the split re-scaled the pool: {inv}")


if __name__ == "__main__":
    unittest.main()
