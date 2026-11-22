"""Regressions for FUZZ-2026-07 confirmed findings (minimal repros).

Formerly: test_fuzz_fixes"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule
from taxjson.rules import ids as R
import _paths

REPO_ROOT = _paths.REPO


def _tx(d, q, p, sym="NNN.TO", acct="margin"):
    return {"action": "BUYSELL", "date": d, "date_settle": d,
            "time": "09:30:00", "symbol": sym, "quantity": q, "price": p,
            "net_amount": abs(q) * p, "currency": "CAD", "account": acct}


def _gains(country, txs, extra=()):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "b.json"
        f.write_text(json.dumps({"transactions": txs}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_gains",
             "--country", country, "--year", "2025", "--taxable",
             *extra, str(f)],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)


class TestFuzzA_ConsumedReplacement(unittest.TestCase):
    """#A (critical): serial loss-sales in one window must not match
    already-sold lots as replacements — a fully-closed, never-reopened
    position realizes its economic loss in BOTH engines.

    Formerly: test_fuzz_fixes.TestFuzzA_ConsumedReplacement"""

    CHAIN = [_tx("2025-01-02", 100, 100.0), _tx("2025-01-10", -100, 98.0),
             _tx("2025-01-15", 100, 97.5), _tx("2025-01-20", -100, 95.5)]

    def test_full_exit_realizes_economic_loss(self):
        for country in ("usa", "canada"):
            out = _gains(country, self.CHAIN)
            self.assertAlmostEqual(out["summary"]["total_gain"], -400.0,
                                   places=2, msg=country)
        usa = _gains("usa", self.CHAIN)
        # First loss washed (live rebuy 01-15), second allowed.
        washes = usa.get("wash_sales", [])
        self.assertEqual(len(washes), 1)
        self.assertEqual(washes[0]["date"], "2025-01-10")
        perm = sum(float(t.get("permanently_disallowed") or 0)
                   for t in usa["transactions"])
        self.assertAlmostEqual(perm, 0.0, places=2)

    def test_loss_ladder_conserves(self):
        from datetime import date, timedelta
        txs, price, day = [], 100.0, 0
        d0 = date(2025, 1, 6)
        for _ in range(20):
            txs.append(_tx(str(d0 + timedelta(days=day)), 100, price,
                           sym="L.TO")); day += 2
            price -= 2.0
            txs.append(_tx(str(d0 + timedelta(days=day)), -100, price,
                           sym="L.TO")); day += 2
        economic = sum((1 if t["quantity"] < 0 else -1) * t["net_amount"]
                       for t in txs)
        for country in ("usa", "canada"):
            out = _gains(country, txs)
            self.assertAlmostEqual(out["summary"]["total_gain"], economic,
                                   places=2, msg=country)


class TestFuzzB_CoverIsNotReplacement(unittest.TestCase):
    """#B (critical, canada): a short-COVERING buy is not replacement
    property. The old trigger test (quantity > 0) accepted it, and the
    LONG-signed ADJUST landed on a SHORT pool — inflating its opening
    proceeds and flipping a deferred -2,000 loss into a phantom +2,000
    gain with zero warnings. Fixture: buy 100; sell 200 (close + open
    short); partial cover; full cover; rebuy 100 (the REAL trigger);
    final exit — everything flat, economic P&L -2,000.

    Formerly: test_fuzz_fixes.TestFuzzB_CoverIsNotReplacement"""

    FIXTURE = _paths.TESTS / "fixtures" / \
        "fuzz_b_short_cover_trigger.json"

    def test_both_engines_agree_on_economic_loss(self):
        txs = json.loads(self.FIXTURE.read_text())["transactions"]
        for country in ("canada", "usa"):
            out = _gains(country, txs)
            self.assertAlmostEqual(out["summary"]["total_gain"], -2000.0,
                                   places=2, msg=country)

    def test_canada_defers_into_the_real_replacement(self):
        txs = json.loads(self.FIXTURE.read_text())["transactions"]
        out = _gains("canada", txs)
        # The 02-03 long loss IS superficial (100 sh held at window end
        # via the later rebuy) — deferred, then recovered on the final
        # exit; nothing permanent, solver converged.
        self.assertTrue(out["summary"]["wash_solver_converged"])
        perm = sum(float(t.get("permanently_disallowed") or 0)
                   for t in out["transactions"])
        self.assertAlmostEqual(perm, 0.0, places=2)


class TestFuzzC_WashAcrossRename(unittest.TestCase):
    """#C: USA missed §1091 when the loss preceded a SPLIT rename and
    the rebuy was under the new symbol (replacement records were keyed
    by raw symbol; the SPLIT-time migration ran after the loss had
    already looked up the old key). Records are now keyed by the
    rename-chain canonical symbol — the USA twin of Canada's alias_of.

    Formerly: test_fuzz_fixes.TestFuzzC_WashAcrossRename"""

    RENAME = [_tx("2025-01-05", 100, 50.0, sym="OLD.TO"),
              _tx("2025-03-10", -100, 40.0, sym="OLD.TO"),
              {"action": "SPLIT", "date": "2025-03-20",
               "date_settle": "2025-03-20", "time": "09:30:00",
               "symbol": "OLD.TO", "symbol_new": "NEW.TO",
               "quantity": 1, "currency": "CAD", "account": "margin"},
              _tx("2025-03-25", 100, 40.0, sym="NEW.TO")]

    def test_both_engines_wash_across_the_rename(self):
        for country in ("usa", "canada"):
            out = _gains(country, self.RENAME)
            self.assertAlmostEqual(out["summary"]["total_gain"], 0.0,
                                   places=2, msg=country)
            self.assertEqual(len(out.get("wash_sales", [])), 1, country)
            inv = {r["symbol"]: r for r in out["inventory"]}
            self.assertAlmostEqual(inv["NEW.TO"]["total_cost"], 5000.0,
                                   places=2, msg=country)
            self.assertAlmostEqual(inv["NEW.TO"]["deferred_wash"],
                                   1000.0, places=2, msg=country)


def _gains_mixed(country, taxable_txs, sheltered_txs):
    with tempfile.TemporaryDirectory() as tmp:
        tf = Path(tmp) / "t.json"
        sf = Path(tmp) / "s.json"
        tf.write_text(json.dumps({"transactions": taxable_txs}))
        sf.write_text(json.dumps({"transactions": sheltered_txs}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_gains",
             "--country", country, "--year", "2025", "--taxable",
             "--sheltered", str(sf), str(tf)],
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)


class TestFuzzD_AllocationSplit(unittest.TestCase):
    """#D: a window with BOTH taxable and sheltered rebuys must split
    the denial pro-rata by opening quantity — taxable portion deferred
    (recoverable), sheltered portion permanent — and the answer must
    not depend on which rebuy came first. Previously 100% attached to
    the earliest trigger: one ordering understated tax, the other
    permanently destroyed the taxable deferral. Also fixes Canada's
    hardcoded permanently_disallowed = 0.0 (the #16 audit hole).

    Formerly: test_fuzz_fixes.TestFuzzD_AllocationSplit"""

    @staticmethod
    def _tx(d, q, p, acct, tid):
        return {"action": "BUYSELL", "date": d, "date_settle": d,
                "time": "09:30:00", "symbol": "PPP.TO", "quantity": q,
                "price": p, "net_amount": abs(q) * p, "currency": "CAD",
                "account": acct, "id": tid}

    def _scenario(self, tfsa_date):
        # buy 100@50; sell 100@43 (-700); taxable rebuy 40 + TFSA rebuy
        # 30 in-window (bal at window end 70 -> 490 denied, 210 allowed);
        # December flat exit of the taxable 40 recovers its 280.
        taxable = [self._tx("2025-01-05", 100, 50.0, "margin", "B1"),
                   self._tx("2025-03-10", -100, 43.0, "margin", "S1"),
                   self._tx("2025-03-20", 40, 43.9, "margin", "B2"),
                   self._tx("2025-12-10", -40, 43.9, "margin", "S2")]
        sheltered = [self._tx(tfsa_date, 30, 43.5, "tfsa", "B3")]
        return taxable, sheltered

    def test_order_independent_and_classified(self):
        for label, tfsa_date in (("tfsa-first", "2025-03-15"),
                                 ("taxable-first", "2025-03-25")):
            out = _gains_mixed("canada", *self._scenario(tfsa_date))
            self.assertAlmostEqual(out["summary"]["total_gain"], -490.0,
                                   places=2, msg=label)
            wash = next(t for t in out["transactions"]
                        if t.get("is_wash_sale"))
            self.assertAlmostEqual(wash["disallowed_amount"], 490.0,
                                   places=2, msg=label)
            # 30 TFSA shares x 7/sh = 210 permanently denied; the 280
            # taxable-allocated portion deferred and later recovered.
            self.assertAlmostEqual(wash["permanently_disallowed"], 210.0,
                                   places=2, msg=label)
            self.assertEqual(len(wash["replacement_lot_ids"]), 2, label)

    def test_usa_agrees(self):
        for tfsa_date in ("2025-03-15", "2025-03-25"):
            out = _gains_mixed("usa", *self._scenario(tfsa_date))
            self.assertAlmostEqual(out["summary"]["total_gain"], -490.0,
                                   places=2)

    @rule(R.CA_SL_03, R.CA_SL_09)
    def test_pure_sheltered_replacement_is_permanent(self):
        # #16: Canada reported permanently_disallowed = 0.0 even when
        # the ONLY replacement was in an RRSP — the denied dollars were
        # classified nowhere (wash-sales/form-export printed a deferral
        # that never existed).
        taxable = [self._tx("2025-01-05", 100, 8.0, "cash", "A1"),
                   self._tx("2025-05-20", -100, 6.0, "cash", "A2")]
        sheltered = [self._tx("2025-05-25", 100, 6.1, "rrsp", "A3")]
        out = _gains_mixed("canada", taxable, sheltered)
        wash = next(t for t in out["transactions"]
                    if t.get("is_wash_sale"))
        self.assertAlmostEqual(wash["disallowed_amount"], 200.0, places=2)
        self.assertAlmostEqual(wash["permanently_disallowed"], 200.0,
                               places=2)


def _sp(d, old, new):
    return {"action": "SPLIT", "date": d, "date_settle": d,
            "time": "09:30:00", "symbol": old, "symbol_new": new,
            "quantity": 1, "net_amount": 0, "currency": "CAD",
            "account": "margin"}


class TestFuzzFG_RenameFamilyAndFifo(unittest.TestCase):
    """#F8 stranded bump, #F10 opposite-sign merge, #F11 signed short
    basis, #G FIFO order after rename-merge.

    Formerly: test_fuzz_fixes.TestFuzzFG_RenameFamilyAndFifo"""

    def test_f8_adjust_follows_rename_to_live_pool(self):
        # buy GGG in-window; rename; sell half of HHH at a loss — the
        # deferral must land on the LIVE HHH pool, not a dead GGG one.
        out = _gains("canada", [
            _tx("2025-01-20", 100, 10.0, sym="GGG.TO"),
            _sp("2025-02-01", "GGG.TO", "HHH.TO"),
            _tx("2025-02-10", -50, 5.0, sym="HHH.TO")])
        inv = {r["symbol"]: r for r in out["inventory"]}
        self.assertNotIn("GGG.TO", inv)
        self.assertAlmostEqual(inv["HHH.TO"]["total_cost"], 750.0,
                               places=2)          # 500 + 250 deferred
        self.assertAlmostEqual(inv["HHH.TO"]["deferred_wash"], 250.0,
                               places=2)
        self.assertAlmostEqual(out["summary"]["total_gain"], 0.0,
                               places=2)

    def test_f11_over_deferred_short_recovers_signed(self):
        # Deferred loss (4,500) exceeds the re-short's premium (500):
        # pool cost legitimately goes negative; the final cover must
        # recover -4,500, not book +3,500 via abs().
        out = _gains("canada", [
            _tx("2025-01-05", -100, 50.0, sym="XYZ.TO"),
            _tx("2025-02-03", 100, 95.0, sym="XYZ.TO"),
            _tx("2025-02-10", -100, 5.0, sym="XYZ.TO"),
            _tx("2025-06-01", 100, 5.0, sym="XYZ.TO")])
        self.assertAlmostEqual(out["summary"]["total_gain"], -4500.0,
                               places=2)

    def test_f10_opposite_sign_rename_merge_refused_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "b.json"
            f.write_text(json.dumps({"transactions": [
                _tx("2025-01-05", 100, 10.0, sym="AAA.TO"),
                _tx("2025-01-06", -50, 10.0, sym="BBB.TO"),
                _sp("2025-02-01", "AAA.TO", "BBB.TO")]}))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", "canada", "--year", "2025", "--taxable",
                 str(f)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("close-out", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_g_fifo_by_date_after_rename_merge(self):
        # 2024 lot migrates onto a symbol holding a 2025 lot: the sale
        # must consume the OLDER migrated lot first (LONG_TERM).
        out = _gains("usa", [
            _tx("2024-01-05", 10, 100.0, sym="OLD.US"),
            _tx("2025-02-01", 10, 200.0, sym="NEW.US"),
            _sp("2025-03-01", "OLD.US", "NEW.US"),
            _tx("2025-04-01", -10, 300.0, sym="NEW.US")])
        sold = next(t for t in out["transactions"] if t.get("qty"))
        self.assertEqual(sold["term"], "LONG_TERM")
        self.assertAlmostEqual(sold["gain"], 2000.0, places=2)


if __name__ == "__main__":
    unittest.main()
