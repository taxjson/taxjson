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


class TestUsSplitWindowUnits(unittest.TestCase):
    """Formerly: test_audit_round2_fixes.TestUsSplitWindowUnits"""
    def _run(self, txs):
        from taxjson.engine.us.taxrules import USATaxRules
        return USATaxRules().compute_gains(txs)

    def _tx(self, date, qty, price, net, sym="AAPL"):
        return _tt("BUYSELL", date, sym, qty, price, net)

    def test_post_split_replacement_not_double_scaled(self):
        # BUY 100@10 → 2:1 SPLIT → SELL 200@2 (loss 600) → BUY 50@2.
        # Correct: 50 of 200 loss shares replaced → disallow $150 (was $300).
        txs = [
            self._tx("2026-01-05", 100, 10.0, 1000.0),
            _tt("SPLIT", "2026-02-01", "AAPL", 2.0),
            self._tx("2026-02-10", -200, 2.0, 400.0),
            self._tx("2026-02-15", 50, 2.0, 100.0),
        ]
        res = self._run(txs)
        dis = sum(float(g.get("disallowed_amount") or 0)
                  for g in res["transactions"] if g.get("qty"))
        self.assertAlmostEqual(dis, 150.0, places=2)

    def test_reverse_split_replacement_converted_up(self):
        # BUY 100@10 → 1-for-10 SPLIT → SELL 10@40 (loss 600) → BUY 4@40.
        # 4 post-split reps cover 4 of the 10 loss shares → 40% of the loss.
        txs = [
            self._tx("2026-01-05", 100, 10.0, 1000.0),
            _tt("SPLIT", "2026-02-01", "AAPL", 0.1),
            self._tx("2026-02-10", -10, 40.0, 400.0),
            self._tx("2026-02-15", 4, 40.0, 160.0),
        ]
        res = self._run(txs)
        dis = sum(float(g.get("disallowed_amount") or 0)
                  for g in res["transactions"] if g.get("qty"))
        self.assertAlmostEqual(dis, 240.0, places=2)   # 600 * 4/10

    def test_split_between_loss_and_replacement(self):
        # BUY 100@10 → SELL 100@4 (loss 600, pre-split units) → 2:1 SPLIT →
        # BUY 100@2 (post-split = 50 pre-split) → half the loss disallowed.
        txs = [
            self._tx("2026-01-05", 100, 10.0, 1000.0),
            self._tx("2026-02-01", -100, 4.0, 400.0),
            _tt("SPLIT", "2026-02-05", "AAPL", 2.0),
            self._tx("2026-02-10", 100, 2.0, 200.0),
        ]
        res = self._run(txs)
        dis = sum(float(g.get("disallowed_amount") or 0)
                  for g in res["transactions"] if g.get("qty"))
        self.assertAlmostEqual(dis, 300.0, places=2)   # 600 * 50/100

    def test_no_split_regression(self):
        txs = [
            self._tx("2026-01-05", 100, 10.0, 1000.0),
            self._tx("2026-02-01", -100, 5.0, 500.0),   # loss 500
            self._tx("2026-02-10", 100, 5.0, 500.0),    # full replacement
        ]
        res = self._run(txs)
        dis = sum(float(g.get("disallowed_amount") or 0)
                  for g in res["transactions"] if g.get("qty"))
        self.assertAlmostEqual(dis, 500.0, places=2)


class TestUsTaintedShortGate(unittest.TestCase):
    """Formerly: test_audit_round2_fixes.TestUsTaintedShortGate"""
    def test_phantom_short_cover_does_not_corrupt_clean_lots(self):
        from taxjson.engine.us.taxrules import USATaxRules
        # Phantom short OB −100 (proceeds 0). Covering BUY books a bogus
        # tainted loss; a clean short round-trip in the window must stay $0
        # and no wash records may be fabricated.
        txs = [
            _tt("OPENING_BALANCE", "2026-01-02", "TNT", -100, 0.0, 0.0,
                time="00:00:00"),
            _tt("BUYSELL", "2026-02-01", "TNT", 100, 5.0, 500.0),   # cover
            _tt("BUYSELL", "2026-02-05", "TNT", -100, 5.0, 500.0),  # clean short
            _tt("BUYSELL", "2026-02-20", "TNT", 100, 5.0, 500.0),   # clean cover
        ]
        res = USATaxRules().compute_gains(txs)
        clean = [g for g in res["transactions"]
                 if g.get("qty") and not g.get("tainted")]
        for g in clean:
            self.assertAlmostEqual(float(g.get("gain") or 0), 0.0, places=2,
                                   msg=f"clean row corrupted: {g}")
        self.assertFalse(res.get("wash_sales"),
                         f"fabricated wash records: {res.get('wash_sales')}")


if __name__ == "__main__":
    unittest.main()
