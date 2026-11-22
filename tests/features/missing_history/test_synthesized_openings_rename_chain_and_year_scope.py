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
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support.planning_radar_run import REPO_ROOT, _row


def _tt(action, date, symbol, qty, price=0.0, net=0.0, account="acct",
        time="09:30:00", **kw):
    from taxjson.model.transaction import TaxTransaction
    return TaxTransaction(action=action, date=date, time=time, symbol=symbol,
                          quantity=qty, price=price, net_amount=net,
                          currency="CAD", account=account, **kw)


class TestSynthesizeRenameChain(unittest.TestCase):
    """Formerly: test_audit_round2_fixes.TestSynthesizeRenameChain"""
    def test_deficit_behind_a_rename_sized_correctly(self):
        from taxjson.lib.missing_history import synthesize_openings
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        # BUY 100 OLD → rename OLD→NEW (1:1) → SELL 150 NEW. True deficit 50.
        # missing_history.json lists (NEW, acct) — detect reports under NEW.
        txs = [
            _tt("BUYSELL", "2026-01-05", "OLD.TO", 100, 10.0, 1000.0),
            _tt("SPLIT", "2026-02-01", "OLD.TO", 1.0, symbol_new="NEW.TO"),
            _tt("BUYSELL", "2026-03-01", "NEW.TO", -150, 10.0, 1500.0),
        ]
        out, applied = synthesize_openings(txs, {("NEW.TO", "acct")})
        entry = next(a for a in applied if a["inserted"])
        self.assertAlmostEqual(entry["opening_qty"], 50.0, places=6)
        # Anchored on the CHAIN's earliest symbol so replay flows through
        # the rename.
        self.assertEqual(entry.get("anchor_symbol"), "OLD.TO")
        res = CanadaTaxRules().compute_gains(out)
        qty = sum(h["qty"] for h in res["inventory"]
                  if h["symbol"] in ("OLD.TO", "NEW.TO"))
        self.assertAlmostEqual(qty, 0.0, places=6)

    def test_midnight_split_cannot_precede_opening(self):
        from taxjson.lib.missing_history import synthesize_openings
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        # The SPLIT is the pair's earliest row AND stamped 00:00:00 (real
        # parsers do this). The opening must still be scaled by it.
        txs = [
            _tt("SPLIT", "2026-02-01", "MID.TO", 2.0, time="00:00:00"),
            _tt("BUYSELL", "2026-02-10", "MID.TO", -100, 5.0, 500.0),
        ]
        out, applied = synthesize_openings(txs, {("MID.TO", "acct")})
        entry = next(a for a in applied if a["inserted"])
        self.assertAlmostEqual(entry["opening_qty"], 50.0, places=6)  # pre-split units
        res = CanadaTaxRules().compute_gains(out)
        qty = sum(h["qty"] for h in res["inventory"] if h["symbol"] == "MID.TO")
        self.assertAlmostEqual(qty, 0.0, places=6)


class TestGenPhantomsYearScope(unittest.TestCase):
    """Formerly: test_audit_round2_fixes.TestGenPhantomsYearScope"""
    def test_cover_only_year_keeps_the_pair(self):
        # 2025 phantom SELL, 2026 covering BUY (no 2026 sell). A 2026-scoped
        # suggest run must KEEP the pair — dropping it let the engine book
        # the cover as a clean in-year short-close gain.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "m_base.json"
            base.write_text(json.dumps({"transactions": [
                _row("BUYSELL", "2025-06-01", "CVR.TO", -100, 4000.0),
                _row("BUYSELL", "2026-02-01", "CVR.TO", 100, 3400.0),
            ]}))
            out = Path(tmp) / "ph.json"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--year", "2026",
                 "--suggest-missing-history", str(out), str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            entries = json.loads(out.read_text())
        self.assertTrue(any(e["symbol"] == "CVR.TO" for e in entries),
                        f"cover-only pair dropped from year scope: {entries}")


if __name__ == "__main__":
    unittest.main()
