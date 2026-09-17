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
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tt(action, date, symbol, qty, price=0.0, net=0.0, account="acct",
        time="09:30:00", **kw):
    from taxjson.lib.core import TaxTransaction
    return TaxTransaction(action=action, date=date, time=time, symbol=symbol,
                          quantity=qty, price=price, net_amount=net,
                          currency="CAD", account=account, **kw)


class TestSplitDedupSymbolNewNormalization(unittest.TestCase):
    def test_ib_vs_rbc_symbol_new_spelling_collapses(self):
        from taxjson.lib.core import CanadaTaxRules
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


class TestUsSplitWindowUnits(unittest.TestCase):
    def _run(self, txs):
        from taxjson.lib.core import USATaxRules
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


class TestCanadaSplitWindowUnits(unittest.TestCase):
    def _run(self, txs):
        from taxjson.lib.core import CanadaTaxRules
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


class TestUsTaintedShortGate(unittest.TestCase):
    def test_phantom_short_cover_does_not_corrupt_clean_lots(self):
        from taxjson.lib.core import USATaxRules
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


class TestSynthesizeRenameChain(unittest.TestCase):
    def test_deficit_behind_a_rename_sized_correctly(self):
        from taxjson.lib.phantom_holdings import synthesize_openings
        from taxjson.lib.core import CanadaTaxRules
        # BUY 100 OLD → rename OLD→NEW (1:1) → SELL 150 NEW. True deficit 50.
        # phantoms.json lists (NEW, acct) — detect reports under NEW.
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
        from taxjson.lib.phantom_holdings import synthesize_openings
        from taxjson.lib.core import CanadaTaxRules
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


def _radar(taxable_txs, date):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "t.json"
        f.write_text(json.dumps({"transactions": taxable_txs}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
             "--taxable", str(f), "--date", date],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout


def _row(action, date, sym, qty, net, account="margin", time="09:30:00", **kw):
    d = {"action": action, "date": date, "time": time, "symbol": sym,
         "quantity": qty, "price": abs(net / qty) if qty else 0,
         "net_amount": abs(net), "currency": "CAD", "account": account}
    d.update(kw)
    return d


class TestRadarMultiLossAndDirection(unittest.TestCase):
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
        line = next(l for l in out.splitlines() if l.startswith("ML.TO"))
        self.assertIn("VIOLATION", line)
        # loss #1's rescue deadline: settle bound 07-01, and the last
        # T+1 TRADE date that settles inside it (06-30) is what the
        # user is told to act on.
        self.assertIn("SETTLE by 2026-07-01", line)
        self.assertIn("by 2026-06-30", line)

    def test_short_position_loss_not_reported_as_sell_violation(self):
        # Short 100, cover 40 at a loss: the short-OPENING sale must not
        # trigger a VIOLATION telling the user to sell/increase the short.
        txs = [
            _row("BUYSELL", "2026-06-01", "SHT.TO", -100, 1000.0),  # short open
            _row("BUYSELL", "2026-06-10", "SHT.TO", 40, 800.0),     # cover @loss
        ]
        out = _radar(txs, "2026-06-15")
        line = next(l for l in out.splitlines() if l.startswith("SHT.TO"))
        self.assertNotIn("VIOLATION: Sell", line)

    def test_reshort_in_window_is_cover_violation(self):
        # Cover at a loss then RE-SHORT in the window while still short:
        # direction-matched trigger → VIOLATION with "Cover" wording.
        txs = [
            _row("BUYSELL", "2026-05-01", "RSH.TO", -100, 1000.0),  # short open
            _row("BUYSELL", "2026-06-10", "RSH.TO", 40, 800.0),     # cover @loss
            _row("BUYSELL", "2026-06-12", "RSH.TO", -40, 400.0),    # re-short
        ]
        out = _radar(txs, "2026-06-15")
        line = next(l for l in out.splitlines() if l.startswith("RSH.TO"))
        self.assertIn("VIOLATION: Cover", line)

    def test_duplicate_same_account_split_rows_apply_once(self):
        # One account fed by two brokers → the same split arrives twice.
        txs = [
            _row("BUYSELL", "2026-01-05", "DUP.TO", 100, 1000.0),
            _row("SPLIT", "2026-02-01", "DUP.TO", 2.0, 0.0),
            _row("SPLIT", "2026-02-01", "DUP.TO", 2.0, 0.0),
            _row("BUYSELL", "2026-03-01", "DUP.TO", -200, 1000.0),  # closes all
        ]
        out = _radar(txs, "2026-03-05")
        line = next((l for l in out.splitlines() if l.startswith("DUP.TO")), "")
        if line:      # position fully closed → qty column must be 0
            cells = [c.strip() for c in line.split("|")]
            self.assertAlmostEqual(float(cells[1]), 0.0)


class TestTtExportSigned(unittest.TestCase):
    def test_reversal_dividend_and_tax_round_trip(self):
        from taxjson.bin.taxjson_convert_tt import tx_to_tt_line, parse_tt_line
        div = {"action": "DIVIDEND", "date": "2026-03-20", "time": "09:30:00",
               "symbol": "AAA.US", "quantity": -1000.0, "price": 0.25,
               "currency": "USD", "net_amount": -250.0, "gross_amount": -250.0}
        tax = {"action": "TAX", "date": "2026-03-20", "time": "09:30:00",
               "symbol": "AAA.US", "quantity": 0.0, "price": 0.0,
               "currency": "USD", "net_amount": -37.5, "gross_amount": 0.0}
        for tx, expected in ((div, -250.0), (tax, -37.5)):
            line = tx_to_tt_line(tx)
            self.assertIsNotNone(line)
            back = parse_tt_line(line, account_name="m")
            self.assertAlmostEqual(back["net_amount"], expected, places=2,
                                   msg=f"sign lost round-tripping: {line}")


class TestMerge2ReconcileSigned(unittest.TestCase):
    def test_charge_plus_refund_nets(self):
        from taxjson.bin.taxjson_merge2 import reconcile_dividend_tax
        from taxjson.lib.core import TaxTransaction
        desc = "AAA(US1) Cash Dividend USD 2.50 per Share (Ordinary Dividend)"
        div = TaxTransaction(action="DIVIDEND", date="2026-03-15",
                             time="09:30:00", symbol="AAA.US", quantity=100,
                             price=2.5, currency="USD", net_amount=250.0,
                             gross_amount=250.0, account="m", description=desc)
        charge = TaxTransaction(action="TAX", date="2026-03-15",
                                time="09:30:00", symbol="AAA.US", quantity=0,
                                price=0.0, currency="USD", net_amount=37.5,
                                account="m", description=desc + " - US Tax")
        refund = TaxTransaction(action="TAX", date="2026-03-15",
                                time="09:30:00", symbol="AAA.US", quantity=0,
                                price=0.0, currency="USD", net_amount=-30.0,
                                account="m", description=desc + " - US Tax")
        reconcile_dividend_tax([div, charge, refund])
        # Net tax = 7.50 → dividend net 242.50 (abs() said 182.50).
        self.assertAlmostEqual(div.net_amount, 242.50, places=2)


class TestGenPhantomsYearScope(unittest.TestCase):
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
                 "--suggest-phantoms", str(out), str(base)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            entries = json.loads(out.read_text())
        self.assertTrue(any(e["symbol"] == "CVR.TO" for e in entries),
                        f"cover-only pair dropped from year scope: {entries}")


if __name__ == "__main__":
    unittest.main()
