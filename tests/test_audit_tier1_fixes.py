"""Regression tests for the 2026-07 audit tier-1 fixes:

1. run_to_file: a failed stage must not leave a truncated output file that the
   mtime cache then treats as fresh (previous good output must survive).
2. `taxjson fees` counts standalone FEE-action rows (amount in net_amount).
3. IB dividend/withholding reversal rows keep their sign and net out instead
   of being abs()'d into extra income/tax.
4. Wash radar: 30-day windows are DST-immune (UTC-noon epochs) — day 30
   across the November fall-back is still inside the window.
5. Wash radar: the VIOLATION (substituted-property) test uses the CRA window
   around the LOSS SALE, not the last 30 days from today; fully-exited losses
   route to COOLING.
6. Engines apply one corporate SPLIT once even when merged multi-account
   input carries one SPLIT row per account.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _runsub(root, sub, *args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         sub, *args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=e)


class TestRunToFileAtomic(unittest.TestCase):
    def test_failed_stage_preserves_previous_output(self):
        from taxjson.bin.taxjson_run import run_to_file
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stage.json"
            out.write_text('{"good": true}')
            # A command that writes a partial payload then fails.
            cmd = [sys.executable, "-c",
                   "import sys; sys.stdout.write('{\"transactions\": ['); sys.exit(1)"]
            with self.assertRaises(subprocess.CalledProcessError):
                run_to_file(cmd, out, capture_diag=False)
            # The old good output survives; no .part litter.
            self.assertEqual(out.read_text(), '{"good": true}')
            self.assertFalse((Path(tmp) / "stage.json.part").exists())

    def test_failed_stage_leaves_no_first_time_output(self):
        from taxjson.bin.taxjson_run import run_to_file
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stage.json"
            cmd = [sys.executable, "-c",
                   "import sys; sys.stdout.write('partial'); sys.exit(1)"]
            with self.assertRaises(subprocess.CalledProcessError):
                run_to_file(cmd, out, capture_diag=False)
            self.assertFalse(out.exists(),
                             "a failed first run must not leave a fresh-mtime "
                             "partial file for needs_rebuild to trust")

    def test_success_writes_output(self):
        from taxjson.bin.taxjson_run import run_to_file
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "stage.json"
            run_to_file([sys.executable, "-c", "print('{}')"], out,
                        capture_diag=False)
            self.assertEqual(out.read_text().strip(), "{}")


class TestFeesIncludesFeeRows(unittest.TestCase):
    def test_fee_action_rows_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_raw.json").write_text(json.dumps({
                "transactions": [
                    {"action": "BUYSELL", "date": "2026-03-01",
                     "time": "09:30:00", "symbol": "AAA.TO", "quantity": 100,
                     "price": 10.0, "net_amount": 1009.99, "fee": 9.99,
                     "currency": "CAD"},
                    # Standalone fee (e.g. IB market-data): amount lives in
                    # net_amount, negative = charged.
                    {"action": "FEE", "date": "2026-03-05",
                     "time": "09:30:00", "symbol": "CASH", "quantity": 0.0,
                     "net_amount": -25.00, "currency": "CAD"}]}))
            r = _runsub(root, "fees")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FEE", r.stdout)
        self.assertIn("TOTAL FEES: 34.99 CAD", r.stdout)


class TestIbReversalSigns(unittest.TestCase):
    _CSV = (
        'Statement,Header,Field Name,Field Value\n'
        'Statement,Data,BrokerName,Interactive Brokers\n'
        'Dividends,Header,Currency,Account,Date,Description,Amount\n'
        'Dividends,Data,USD,U1,2026-03-15,'
        'AAA(US0000000001) Cash Dividend USD 2.50 per Share (Ordinary Dividend),250\n'
        'Dividends,Data,USD,U1,2026-03-20,'
        'AAA(US0000000001) Cash Dividend USD 2.50 per Share - Reversal (Ordinary Dividend),-250\n'
        'Dividends,Data,USD,U1,2026-03-20,'
        'AAA(US0000000001) Cash Dividend USD 2.40 per Share (Ordinary Dividend),240\n'
        'Withholding Tax,Header,Currency,Account,Date,Description,Amount\n'
        'Withholding Tax,Data,USD,U1,2026-03-15,'
        'AAA(US0000000001) Cash Dividend USD 2.50 per Share - US Tax,-37.50\n'
        'Withholding Tax,Data,USD,U1,2026-03-20,'
        'AAA(US0000000001) Cash Dividend USD 2.50 per Share - US Tax - Reversal,37.50\n'
        'Withholding Tax,Data,USD,U1,2026-03-20,'
        'AAA(US0000000001) Cash Dividend USD 2.40 per Share - US Tax,-36.00\n'
    )

    def _parse(self):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False) as f:
            f.write(self._CSV)
            name = f.name
        try:
            return IbBrokerage().parse_file(Path(name))
        finally:
            os.remove(name)

    def test_dividend_reversal_nets_out(self):
        txs = self._parse()
        divs = [t for t in txs if t["action"] == "DIVIDEND"]
        self.assertEqual(len(divs), 3)
        total = sum(t["net_amount"] for t in divs)
        # +250 −250 +240: the correction nets to 240, not abs-summed 740.
        self.assertAlmostEqual(total, 240.0, places=2)

    def test_wht_refund_nets_out(self):
        txs = self._parse()
        taxes = [t for t in txs if t["action"] == "TAX"]
        self.assertEqual(len(taxes), 3)
        # Convention: positive = tax withheld. Charge 37.50, refund −37.50,
        # charge 36.00 → 36.00 net (was 111.00 with abs()).
        total = sum(t["net_amount"] for t in taxes)
        self.assertAlmostEqual(total, 36.0, places=2)
        self.assertAlmostEqual(max(t["net_amount"] for t in taxes), 37.5, places=2)
        self.assertAlmostEqual(min(t["net_amount"] for t in taxes), -37.5, places=2)


def _radar(taxable_txs, date, extra_env=None):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "t.json"
        f.write_text(json.dumps({"transactions": taxable_txs}))
        env = dict(os.environ)
        env["TZ"] = "America/Toronto"      # DST-observing zone
        if extra_env:
            env.update(extra_env)
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
             "--taxable", str(f), "--date", date],
            cwd=REPO_ROOT, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout


def _tx(date, sym, qty, net, account="margin"):
    return {"action": "BUYSELL", "date": date, "time": "09:30:00",
            "symbol": sym, "quantity": qty, "price": abs(net / qty) if qty else 0,
            "net_amount": abs(net), "currency": "CAD", "account": account}


class TestWashRadarDst(unittest.TestCase):
    def test_day30_across_fallback_still_in_window(self):
        # Loss sale 2026-10-04; 2026-11-03 is day 30 and the window spans the
        # Nov 1 DST fall-back. Pre-fix the local-epoch math aged it out a day
        # early and the row vanished.
        txs = [_tx("2026-09-01", "DST.TO", 100, 1000.0),
               _tx("2026-10-04", "DST.TO", -100, 500.0)]     # loss, fully out
        out = _radar(txs, "2026-11-03")
        line = next((l for l in out.splitlines() if l.startswith("DST.TO")), "")
        self.assertTrue(line, "day-30 loss must still be reported:\n" + out)
        self.assertIn("COOLING", line)

    def test_locked_buy_day30_across_fallback_not_clear(self):
        # Buy 2026-10-04 still held on 2026-11-03 (day 30): selling at a loss
        # is still a superficial loss — must be LOCKED, not CLEAR.
        txs = [_tx("2026-10-04", "LCK.TO", 100, 1000.0)]
        out = _radar(txs, "2026-11-03")
        line = next(l for l in out.splitlines() if l.startswith("LCK.TO"))
        self.assertIn("EXITABLE", line)
        self.assertNotIn("CLEAR:", line)


class TestWashRadarViolationWindow(unittest.TestCase):
    def test_violation_persists_as_today_advances(self):
        # Substitute lot bought 4 days BEFORE the loss sale and still held:
        # inside [sale−30, sale+30] forever, so the advisory must stay
        # VIOLATION (sell-by deadline) even once the buy is >30 days before
        # "today" — pre-fix it silently downgraded to BLOCKED.
        txs = [_tx("2024-01-05", "VIO.TO", 100, 10000.0),
               _tx("2026-06-01", "VIO.TO", 10, 1000.0),      # trigger buy
               _tx("2026-06-05", "VIO.TO", -50, 2000.0)]     # loss sale
        out_early = _radar(txs, "2026-06-15")
        out_late = _radar(txs, "2026-07-03")                  # buy >30d ago
        for out in (out_early, out_late):
            line = next(l for l in out.splitlines() if l.startswith("VIO.TO"))
            self.assertIn("VIOLATION", line, out)

    def test_no_trigger_held_position_is_blocked(self):
        # Loss with shares still held but NO acquisition in the CRA window:
        # loss is allowed so far — BLOCKED (don't re-buy), not VIOLATION.
        txs = [_tx("2024-01-05", "BLK.TO", 100, 10000.0),
               _tx("2026-06-05", "BLK.TO", -50, 2000.0)]      # loss, 50 left
        out = _radar(txs, "2026-06-15")
        line = next(l for l in out.splitlines() if l.startswith("BLK.TO"))
        self.assertIn("BLOCKED", line)

    def test_fully_exited_loss_is_cooling(self):
        # Sold out completely at a loss, nothing held → COOLING (the category
        # was previously unreachable dead code).
        txs = [_tx("2026-05-01", "COO.TO", 100, 2000.0),
               _tx("2026-06-05", "COO.TO", -100, 1000.0)]
        out = _radar(txs, "2026-06-15")
        line = next(l for l in out.splitlines() if l.startswith("COO.TO"))
        self.assertIn("COOLING", line)


class TestSplitDedup(unittest.TestCase):
    def _split(self, date, sym, ratio, account):
        return {"action": "SPLIT", "date": date, "time": "09:30:00",
                "symbol": sym, "quantity": ratio, "net_amount": 0.0,
                "currency": "CAD", "account": account}

    def test_canada_duplicate_split_rows_apply_once(self):
        from taxjson.lib.core import CanadaTaxRules, load_transactions
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
        from taxjson.lib.core import (CanadaTaxRules, TaxTransaction)
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
