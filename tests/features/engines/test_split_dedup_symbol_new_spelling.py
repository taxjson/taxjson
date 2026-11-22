"""Regression tests for the round-2 audit fixes (holes found by the
adversarial re-audit of the tier-1/2 fixes, plus the pre-validated tier-3
engine fixes):

- Split dedup key normalizes symbol_new (IB stamps it = symbol, RBC/Questrade
  stamp '' — same event, different spelling).
- US §1091: replacement records stay in their own trade-date units, converted
  at match time (a split no longer double-scales post-split replacements; a
  split BETWEEN the loss and the replacement is handled too).
- Canada: bal_at_end / acquired_qty / loss qty are compared in loss-date
  units (a reverse split no longer shrinks the disallowance 10x).
- US engine gates tainted (phantom) losses out of §1091 like Canada does
  (phantom short lots have proceeds=0, so covering always booked bogus loss).
- synthesize_openings follows rename chains and anchors a day before first
  activity (a midnight SPLIT can no longer sort ahead of the opening).
- Radar: keeps ALL in-window losses (an older active VIOLATION survives a
  newer loss); triggers are direction-matched (no "sell more" advice on
  shorts); duplicate same-account SPLIT rows apply once.
- .tt export keeps DIVIDEND/TAX signs (reversals round-trip); merge2's
  dividend/tax reconciliation nets signed TAX legs.
- gen-phantoms year scope keeps a pair whose only in-year activity is the
  covering BUY of a phantom short.

Formerly: test_audit_round2_fixes
"""
import unittest


def _tt(action, date, symbol, qty, price=0.0, net=0.0, account="acct",
        time="09:30:00", **kw):
    from taxjson.model.transaction import TaxTransaction
    return TaxTransaction(action=action, date=date, time=time, symbol=symbol,
                          quantity=qty, price=price, net_amount=net,
                          currency="CAD", account=account, **kw)


class TestSplitDedupSymbolNewNormalization(unittest.TestCase):
    """Formerly: test_audit_round2_fixes.TestSplitDedupSymbolNewNormalization"""
    def test_ib_vs_rbc_symbol_new_spelling_collapses(self):
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        # Same 2:1 event: IB row carries symbol_new == symbol, RBC row ''.
        txs = [
            _tt("BUYSELL", "2026-01-05", "SPL.TO", 100, 10.0, 1000.0, account="a1"),
            _tt("BUYSELL", "2026-01-05", "SPL.TO", 100, 10.0, 1000.0, account="a2"),
            _tt("SPLIT", "2026-02-01", "SPL.TO", 2.0, account="a1",
                symbol_new="SPL.TO"),                       # IB spelling
            _tt("SPLIT", "2026-02-01", "SPL.TO", 2.0, account="a2",
                symbol_new=""),                             # RBC spelling
            _tt("BUYSELL", "2026-03-01", "SPL.TO", -400, 5.0, 2000.0, account="a1"),
        ]
        res = CanadaTaxRules().compute_gains(txs)
        total_gain = sum(float(g.get("gain") or 0) for g in res["transactions"]
                         if g.get("symbol") == "SPL.TO" and g.get("qty"))
        self.assertAlmostEqual(total_gain, 0.0, places=2)
        inv = [h for h in res["inventory"] if h["symbol"] == "SPL.TO"]
        self.assertTrue(all(abs(h["qty"]) < 1e-6 for h in inv), inv)


if __name__ == "__main__":
    unittest.main()
