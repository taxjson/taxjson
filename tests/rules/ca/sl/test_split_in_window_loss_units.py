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


class TestCanadaSplitWindowUnits(unittest.TestCase):
    """Formerly: test_audit_round2_fixes.TestCanadaSplitWindowUnits"""
    def _run(self, txs):
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        return CanadaTaxRules().compute_gains(txs)

    def test_reverse_split_in_window_full_disallowance(self):
        # BUY 100@10, SELL 100@5 (loss 500), rebuy 100@5 (+5d), 1-for-10
        # reverse split (+15d). bal_at_end is 10 post-split shares = 100
        # loss-date shares → FULL $500 disallowed (was $50).
        txs = [
            _tt("BUYSELL", "2026-01-05", "REV.TO", 100, 10.0, 1000.0),
            _tt("BUYSELL", "2026-02-01", "REV.TO", -100, 5.0, 500.0),
            _tt("BUYSELL", "2026-02-06", "REV.TO", 100, 5.0, 500.0),
            _tt("SPLIT", "2026-02-16", "REV.TO", 0.1),
        ]
        res = self._run(txs)
        dis = sum(float(g.get("disallowed_amount") or 0)
                  for g in res["transactions"] if g.get("qty"))
        self.assertAlmostEqual(dis, 500.0, places=2)

    def test_post_split_trigger_converted_to_loss_units(self):
        # Loss of 100 shares; 2:1 split (+5d); trigger buy of 100 POST-split
        # shares (+10d) = 50 loss-date shares → half the loss disallowed.
        txs = [
            _tt("BUYSELL", "2026-01-05", "TRG.TO", 100, 10.0, 1000.0),
            _tt("BUYSELL", "2026-02-01", "TRG.TO", -100, 5.0, 500.0),
            _tt("SPLIT", "2026-02-06", "TRG.TO", 2.0),
            _tt("BUYSELL", "2026-02-11", "TRG.TO", 100, 2.5, 250.0),
        ]
        res = self._run(txs)
        dis = sum(float(g.get("disallowed_amount") or 0)
                  for g in res["transactions"] if g.get("qty"))
        self.assertAlmostEqual(dis, 250.0, places=2)   # 500 * 50/100


if __name__ == "__main__":
    unittest.main()
