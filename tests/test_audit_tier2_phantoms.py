"""Regression tests for the 2026-07 audit tier-2 fixes — the phantom
(--incomplete-history) workflow cluster:

P1. synthesize_openings sizes the opening in OPENING-DATE units, so SPLITs
    inside the data window no longer over/under-size it (forward split made
    the opening 2x too big → pool never drained → taint never cleared and
    later dispositions silently vanished from gains; reverse split left the
    short unfixed).
P2. synthesize_openings counts TRANSFER rows (detect and the engine both do;
    excluding them mis-sized openings exactly on registered accounts).
P3. stage_wash_pass passes --incomplete-history, so the wash-adjusted books
    (preferred by `taxjson wash-sales`) match the main gains books.
P4. Tainted (phantom-pool) losses never feed the superficial-loss solver —
    fabricated DISALLOW/ADJUST rows no longer land on clean pools.
E5. OPENING_BALANCE counts in the ±30-day "still held" balance walks, so a
    clean loss after a phantom drain can't dodge a real wash sale.
"""
import json
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


class TestSynthesizeOpeningsSplits(unittest.TestCase):
    def _synth(self, txs, pairs):
        from taxjson.lib.phantom_holdings import synthesize_openings
        return synthesize_openings(txs, pairs)

    def _replay_qty(self, txs, symbol):
        """Run the Canada engine and return the symbol's ending inventory."""
        from taxjson.lib.core import CanadaTaxRules
        res = CanadaTaxRules().compute_gains(txs)
        return sum(h["qty"] for h in res["inventory"] if h["symbol"] == symbol)

    def test_forward_split_opening_in_predate_units(self):
        # SELL -100, SPLIT x2, SELL -100: true pre-window holding is 150
        # (150 -100 = 50, x2 = 100, -100 = 0). Mixed-unit accounting said 300.
        txs = [
            _tt("BUYSELL", "2026-01-10", "FWD.TO", -100, 10.0, 1000.0),
            _tt("SPLIT", "2026-02-01", "FWD.TO", 2.0),
            _tt("BUYSELL", "2026-03-01", "FWD.TO", -100, 5.0, 500.0),
        ]
        out, applied = self._synth(txs, {("FWD.TO", "acct")})
        entry = next(a for a in applied if a["symbol"] == "FWD.TO")
        self.assertTrue(entry["inserted"])
        self.assertAlmostEqual(entry["opening_qty"], 150.0, places=6)
        # Engine replay drains exactly to zero → taint clears.
        self.assertAlmostEqual(self._replay_qty(out, "FWD.TO"), 0.0, places=6)

    def test_reverse_split_opening_covers_the_short(self):
        # SELL -100, SPLIT x0.5, SELL -100: correct opening is 300
        # (300 -100 = 200, x0.5 = 100, -100 = 0). Mixed units said 150,
        # leaving the position short (-75) — the phantom "fix" fixed nothing.
        txs = [
            _tt("BUYSELL", "2026-01-10", "REV.TO", -100, 10.0, 1000.0),
            _tt("SPLIT", "2026-02-01", "REV.TO", 0.5),
            _tt("BUYSELL", "2026-03-01", "REV.TO", -100, 20.0, 2000.0),
        ]
        out, applied = self._synth(txs, {("REV.TO", "acct")})
        entry = next(a for a in applied if a["symbol"] == "REV.TO")
        self.assertAlmostEqual(entry["opening_qty"], 300.0, places=6)
        self.assertAlmostEqual(self._replay_qty(out, "REV.TO"), 0.0, places=6)

    def test_transfer_counts_toward_opening(self):
        # TRANSFER +100 then SELL -150: the deficit is 50, not 150. (The
        # sheltered CLI rewrites TRANSFER→BUYSELL before the engine, so
        # transfers do move engine position.)
        txs = [
            _tt("TRANSFER", "2026-01-10", "TRF.TO", 100, 10.0, 1000.0),
            _tt("BUYSELL", "2026-02-01", "TRF.TO", -150, 10.0, 1500.0),
        ]
        out, applied = self._synth(txs, {("TRF.TO", "acct")})
        entry = next(a for a in applied if a["symbol"] == "TRF.TO")
        self.assertTrue(entry["inserted"])
        self.assertAlmostEqual(entry["opening_qty"], 50.0, places=6)

    def test_transfer_out_deficit_detected(self):
        # TRANSFER-out creating the deficit used to yield "no opening needed"
        # while the engine still went short.
        txs = [
            _tt("BUYSELL", "2026-01-10", "TRO.TO", 100, 10.0, 1000.0),
            _tt("TRANSFER", "2026-02-01", "TRO.TO", -150, 10.0, 1500.0),
        ]
        out, applied = self._synth(txs, {("TRO.TO", "acct")})
        entry = next(a for a in applied if a["symbol"] == "TRO.TO")
        self.assertTrue(entry["inserted"])
        self.assertAlmostEqual(entry["opening_qty"], 50.0, places=6)


class TestWashPassGetsPhantoms(unittest.TestCase):
    def test_wash_books_match_main_books(self):
        # stage_wash_pass must apply the same phantoms as the main gains pass:
        # the phantom-tainted disposition shows up under
        # manual_reporting_required in the WASH gains file too.
        from taxjson.bin.taxjson_run import stage_wash_pass
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "work"; cache.mkdir()
            reports = root / "reports"; reports.mkdir()
            (cache / "margin_base.json").write_text(json.dumps({
                "transactions": [
                    {"action": "BUYSELL", "date": "2026-03-01",
                     "time": "09:30:00", "symbol": "PHM.TO",
                     "quantity": -100, "price": 50.0, "net_amount": 5000.0,
                     "currency": "CAD", "account": "margin"}]}))
            (cache / "sheltered_base.json").write_text(
                json.dumps({"transactions": []}))
            phantoms = root / "phantoms.json"
            phantoms.write_text(json.dumps(
                [{"symbol": "PHM.TO", "account": "margin"}]))
            settings = {"year": 2026, "country": "canada",
                        "tax_date": "settle", "base_currency": "CAD"}
            # In-process stage: capture its progress lines (they name
            # the fixture's `margin` account and read like a live run).
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                stage_wash_pass("margin", settings, cache, reports,
                                cache / "sheltered_base.json",
                                incomplete_history=phantoms)
            wash = json.loads((cache / "margin_gains_wash.json").read_text())
        mrr = wash.get("manual_reporting_required") or []
        self.assertTrue(any(r.get("symbol") == "PHM.TO" for r in mrr),
                        "wash books must apply phantoms like the main books")


class TestTaintedLossesSkipSolver(unittest.TestCase):
    def _run(self, txs):
        from taxjson.lib.core import CanadaTaxRules
        return CanadaTaxRules().compute_gains(txs)

    def test_fabricated_loss_does_not_adjust_clean_pool(self):
        # Phantom pool: OB 100 @$0 + BUY 100 @$50 → ACB $25/sh. Sell all 200
        # @$20 → fabricated tainted loss. Clean rebuys 100+50 @$20 inside the
        # 30-day window, clean sell 150 @$20 → TRUE gain $0. Pre-fix the
        # tainted loss spawned an ADJUST that landed on the clean pool and
        # the clean sale reported a fabricated loss.
        txs = [
            _tt("OPENING_BALANCE", "2026-01-02", "TNT.TO", 100, 0.0, 0.0,
                time="00:00:00"),
            _tt("BUYSELL", "2026-01-05", "TNT.TO", 100, 50.0, 5000.0),
            _tt("BUYSELL", "2026-02-01", "TNT.TO", -200, 20.0, 4000.0),
            _tt("BUYSELL", "2026-02-10", "TNT.TO", 100, 20.0, 2000.0),
            _tt("BUYSELL", "2026-02-12", "TNT.TO", 50, 20.0, 1000.0),
            _tt("BUYSELL", "2026-02-20", "TNT.TO", -150, 20.0, 3000.0),
        ]
        res = self._run(txs)
        clean = [g for g in res["transactions"]
                 if g.get("symbol") == "TNT.TO" and g.get("qty")
                 and not g.get("tainted") and g.get("date") == "2026-02-20"]
        self.assertTrue(clean, "clean disposition must be reported")
        for g in clean:
            self.assertAlmostEqual(float(g.get("gain") or 0), 0.0, places=2,
                                   msg=f"clean sale corrupted: {g}")
        # And no fabricated wash-sale records from the tainted loss.
        self.assertFalse(res.get("wash_sales"),
                         f"tainted loss must not spawn wash records: "
                         f"{res.get('wash_sales')}")

    def test_opening_balance_counts_in_still_held_walk(self):
        # Clean loss AFTER a phantom drain: the drain sells net against the
        # (previously invisible) OPENING_BALANCE, so the +30-day balance used
        # to read 0 and a genuinely-held rebuy dodged the wash sale.
        txs = [
            _tt("OPENING_BALANCE", "2026-01-02", "OBW.TO", 100, 0.0, 0.0,
                time="00:00:00"),
            _tt("BUYSELL", "2026-01-10", "OBW.TO", -100, 20.0, 2000.0),  # drain
            _tt("BUYSELL", "2026-02-01", "OBW.TO", 100, 20.0, 2000.0),   # clean buy
            _tt("BUYSELL", "2026-02-10", "OBW.TO", -100, 10.0, 1000.0),  # clean loss
            _tt("BUYSELL", "2026-02-15", "OBW.TO", 100, 10.0, 1000.0),   # rebuy, held
        ]
        res = self._run(txs)
        loss_rows = [g for g in res["transactions"]
                     if g.get("symbol") == "OBW.TO"
                     and g.get("date") == "2026-02-10"]
        self.assertTrue(loss_rows)
        self.assertTrue(any(float(g.get("disallowed_amount") or 0) > 1
                            for g in loss_rows),
                        f"held rebuy must disallow the clean loss: {loss_rows}")


if __name__ == "__main__":
    unittest.main()
