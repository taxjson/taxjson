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

from _radar_text import radar_rows
from _support.planning_radar_run import _radar, _row


class TestRadarMultiLossAndDirection(unittest.TestCase):
    """Formerly: test_audit_round2_fixes.TestRadarMultiLossAndDirection"""
    def test_newer_loss_does_not_hide_active_violation(self):
        # Trigger buy May 20, loss Jun 1 (VIOLATION, rescue by Jul 1 =
        # loss+30, the engine's held-at-end boundary — the earlier +31
        # pin printed a deadline one day past the rescue window,
        # 2026-08 deep-audit #8), second small loss Jun 20 with no
        # trigger. On Jun 25 the VIOLATION (and its deadline) must
        # still be reported — it used to flip to BLOCKED.
        txs = [
            _row("BUYSELL", "2024-01-05", "ML.TO", 100, 10000.0),
            _row("BUYSELL", "2026-05-20", "ML.TO", 10, 1000.0),    # trigger
            _row("BUYSELL", "2026-06-01", "ML.TO", -50, 2000.0),   # loss #1
            _row("BUYSELL", "2026-06-20", "ML.TO", -10, 400.0),    # loss #2
        ]
        out = _radar(txs, "2026-06-25")
        line = next(l for l in radar_rows(out) if l.startswith("ML.TO"))
        self.assertIn("VIOLATION", line)
        # loss #1's rescue deadline: settle bound 07-01, and the last
        # T+1 TRADE date that settles inside it is what the user is told
        # to act on. 07-01 is Canada Day, so a 06-30 trade settles 07-02:
        # the last safe trade date is 06-29.
        self.assertIn("SETTLE by 2026-07-01", line)
        self.assertIn("by 2026-06-29", line)

    def test_short_position_loss_not_reported_as_sell_violation(self):
        # Short 100, cover 40 at a loss: the short-OPENING sale must not
        # trigger a VIOLATION telling the user to sell/increase the short.
        txs = [
            _row("BUYSELL", "2026-06-01", "SHT.TO", -100, 1000.0),  # short open
            _row("BUYSELL", "2026-06-10", "SHT.TO", 40, 800.0),     # cover @loss
        ]
        out = _radar(txs, "2026-06-15")
        line = next(l for l in radar_rows(out) if l.startswith("SHT.TO"))
        self.assertNotIn("VIOLATION: Sell", line)

    def test_reshort_in_window_is_cover_violation(self):
        # Cover at a loss then RE-SHORT in the window while still short.
        # US s.1091(e): direction-matched trigger -> the US engine
        # washes it (WASHED; no rescue, partition COMMANDS-01). Canada
        # s.54: a new short acquires nothing, so
        # the loss stands (medium audit S054-00; this test used to pin
        # the US rule for every project).
        txs = [
            _row("BUYSELL", "2026-05-01", "RSH.TO", -100, 1000.0),  # short open
            _row("BUYSELL", "2026-06-10", "RSH.TO", 40, 800.0),     # cover @loss
            _row("BUYSELL", "2026-06-12", "RSH.TO", -40, 400.0),    # re-short
        ]
        out = _radar(txs, "2026-06-15", "--country", "usa")
        line = next(l for l in radar_rows(out) if l.startswith("RSH.TO"))
        self.assertIn("WASHED:", line)
        out = _radar(txs, "2026-06-15")
        line = next(l for l in radar_rows(out) if l.startswith("RSH.TO"))
        self.assertNotIn("VIOLATION", line)

    def test_duplicate_same_account_split_rows_apply_once(self):
        # One account fed by two brokers → the same split arrives twice.
        txs = [
            _row("BUYSELL", "2026-01-05", "DUP.TO", 100, 1000.0),
            _row("SPLIT", "2026-02-01", "DUP.TO", 2.0, 0.0),
            _row("SPLIT", "2026-02-01", "DUP.TO", 2.0, 0.0),
            _row("BUYSELL", "2026-03-01", "DUP.TO", -200, 1000.0),  # closes all
        ]
        out = _radar(txs, "2026-03-05")
        line = next((l for l in radar_rows(out) if l.startswith("DUP.TO")), "")
        if line:      # position fully closed → qty column must be 0
            cells = [c.strip() for c in line.split("|")]
            self.assertAlmostEqual(float(cells[1]), 0.0)


if __name__ == "__main__":
    unittest.main()
