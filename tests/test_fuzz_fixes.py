"""Regressions for FUZZ-2026-07 confirmed findings (minimal repros)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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
    position realizes its economic loss in BOTH engines."""

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
    final exit — everything flat, economic P&L -2,000."""

    FIXTURE = Path(__file__).parent / "fixtures" / \
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


class TestFuzzM_ValidationErrorsSurface(unittest.TestCase):
    """#M: merge2 --validate's hard ERRORs lived only in the raw .diag
    — the DIAGNOSTICS banner regex dropped the `validation:` header and
    the console said nothing (validation theater). Now: the header (and
    its indented TX lines) reaches the .sum banner AND the run prints a
    loud console pointer."""

    def test_bad_quantity_row_is_loudly_reported(self):
        qt = ("Transaction Date,Settlement Date,Action,Symbol,"
              "Description,Quantity,Price,Gross Amount,Commission,"
              "Net Amount,Currency,Account #,Activity Type,Account Type\n"
              "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
              "ISHARES,NOTANUM,10.00,1000.00,9.95,-1009.95,CAD,12345,"
              "Trades,Individual\n"
              "2025-02-15 09:30:00 AM,2025-02-16 12:00:00 AM,Buy,XEI.TO,"
              "ISHARES,100,10.00,1000.00,9.95,-1009.95,CAD,12345,"
              "Trades,Individual\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "questrade.csv").write_text(qt)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "run", "--no-input"],
                cwd=REPO_ROOT, capture_output=True, text=True)
            sum_txt = (root / "reports" / "margin.sum").read_text()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("validation ERROR(s)", r.stderr)     # console
        self.assertIn("validation:", sum_txt)              # banner header
        self.assertIn("Quantity is 0", sum_txt)            # detail line


class TestFuzzC_WashAcrossRename(unittest.TestCase):
    """#C: USA missed §1091 when the loss preceded a SPLIT rename and
    the rebuy was under the new symbol (replacement records were keyed
    by raw symbol; the SPLIT-time migration ran after the loss had
    already looked up the old key). Records are now keyed by the
    rename-chain canonical symbol — the USA twin of Canada's alias_of."""

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
    hardcoded permanently_disallowed = 0.0 (the #16 audit hole)."""

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


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,"
              "Description,Quantity,Price,Gross Amount,Commission,"
              "Net Amount,Currency,Account #,Activity Type,Account Type\n")


def _qt_row(d, action, sym, qty, price, comm, net):
    return (f"{d} 09:30:00 AM,{d} 12:00:00 AM,{action},{sym},DESC,"
            f"{qty},{price},{abs(qty)*price},{comm},{net},CAD,1,"
            f"Trades,Individual\n")


def _pipeline_project(root, margin_rows, rrsp_rows):
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "rrsp").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n'
        '[accounts.rrsp]\ntype = "sheltered"\n')
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT_HEADER + "".join(margin_rows))
    (root / "inputs" / "rrsp" / "questrade.csv").write_text(
        _QT_HEADER + "".join(rrsp_rows))


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


class TestFuzzI_ShelteredFeesNotFolded(unittest.TestCase):
    """#I: the wash pass fed sheltered_base into each taxable account's
    gains run and the fee tally summed over ALL books — an RRSP's fee
    appeared in every taxable wash file and `taxjson sum` multi-counted
    it. Fee tallies are now taxable-book-only."""

    def test_sum_fees_counts_each_fee_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _pipeline_project(
                root,
                [_qt_row("2025-01-15", "Buy", "XEI.TO", 100, 10.0,
                         9.90, -1009.90)],
                [_qt_row("2025-02-03", "Buy", "ZAG.TO", 50, 14.0,
                         14.95, -714.95)])
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            s = _run_cli(root, "sum", "--json")
            doc = json.loads(s.stdout)
        by = {a["account"]: a for a in doc["accounts"]}
        self.assertAlmostEqual(by["margin"]["fees"], 9.90, places=2)
        self.assertAlmostEqual(by["rrsp"]["fees"], 14.95, places=2)
        self.assertAlmostEqual(doc["totals"]["fees"], 24.85, places=2)


class TestFuzzJ_FastSeesDeletions(unittest.TestCase):
    """#J: `run --fast` served stale books when an input CSV was
    DELETED (mtime deps only cover files that exist). A sources
    manifest now bumps on membership change."""

    def test_deleted_csv_drops_from_books_under_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _pipeline_project(
                root,
                [_qt_row("2025-01-15", "Buy", "XEI.TO", 100, 10.0,
                         0.00, -1000.00)],
                [_qt_row("2025-02-03", "Buy", "ZAG.TO", 50, 14.0,
                         0.00, -700.00)])
            extra = root / "inputs" / "margin" / "questrade2.csv"
            extra.write_text(_QT_HEADER + _qt_row(
                "2025-03-01", "Buy", "OTHER.TO", 10, 5.0, 0.00, -50.00))
            r1 = _run_cli(root, "run", "--no-input")
            self.assertEqual(r1.returncode, 0, r1.stderr)
            base1 = (root / "work" / "margin_base.json").read_text()
            self.assertIn("OTHER.TO", base1)
            extra.unlink()                       # delete the CSV
            r2 = _run_cli(root, "run", "--fast", "--no-input")
            self.assertEqual(r2.returncode, 0, r2.stderr)
            base2 = (root / "work" / "margin_base.json").read_text()
        self.assertNotIn("OTHER.TO", base2)      # trades gone with it


class TestFuzzK_InputHardening(unittest.TestCase):
    """#K: garbage inputs must fail CLEANLY (message + exit 1/2), never
    a stack trace or silent corruption."""

    def _gains_raw(self, txs, country="canada"):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "b.json"
            f.write_text(json.dumps(txs))
            return subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_gains",
                 "--country", country, "--taxable", str(f)],
                cwd=REPO_ROOT, capture_output=True, text=True)

    def test_nan_rejected_cleanly_both_engines(self):
        bad = {"transactions": [{
            "action": "BUYSELL", "date": "2025-01-05",
            "time": "09:30:00", "symbol": "B.TO",
            "quantity": float("nan"), "price": 1.0, "net_amount": 1.0,
            "currency": "CAD", "account": "m"}]}
        for country in ("canada", "usa"):
            r = self._gains_raw(bad, country)
            self.assertEqual(r.returncode, 2, country)
            self.assertNotIn("Traceback", r.stderr, country)
            self.assertIn("non-finite", r.stderr, country)

    def test_split_ratio_zero_rejected(self):
        bad = {"transactions": [
            {"action": "BUYSELL", "date": "2025-01-10",
             "time": "09:30:00", "symbol": "SPL.TO", "quantity": 100,
             "price": 10.0, "net_amount": 1000.0, "currency": "CAD",
             "account": "m"},
            {"action": "SPLIT", "date": "2025-02-10", "time": "09:30:00",
             "symbol": "SPL.TO", "quantity": 0, "currency": "CAD",
             "account": "m"}]}
        r = self._gains_raw(bad)
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPLIT ratio must be > 0", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_impossible_date_rejected_and_validated(self):
        bad = {"transactions": [{
            "action": "BUYSELL", "date": "2025-02-29",
            "time": "09:30:00", "symbol": "A.TO", "quantity": 1,
            "price": 1.0, "net_amount": 1.0, "currency": "CAD",
            "account": "m"}]}
        r = self._gains_raw(bad)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Impossible", r.stderr.replace("impossible",
                                                     "Impossible"))
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "b.json"
            f.write_text(json.dumps(bad))
            v = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_validate",
                 str(f)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(v.returncode, 1)
        self.assertIn("Impossible date", v.stdout + v.stderr)


class TestFuzzL_LeapsBuyToCloseNotEntry(unittest.TestCase):
    """#L: a short call's buy-to-close (positive qty against a short
    position) was classified as a LEAPS entry buy — the same
    covered-call gain appeared in BOTH ccd-sum and leaps-sum."""

    SHORT_CALL = "BNS270115C00082000.TO"      # >3 months to expiry
    LONG_CALL = "AAPL270115C00150000.US"

    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_raw.json").write_text(json.dumps(
            {"transactions": [
                # covered call: sell to open, buy to CLOSE (qty>0 but
                # against a short — NOT a LEAPS entry)
                {"action": "BUYSELL", "date": "2025-02-01",
                 "time": "09:30:00", "symbol": self.SHORT_CALL,
                 "quantity": -2, "price": 1.5, "net_amount": 300.0,
                 "currency": "CAD"},
                {"action": "BUYSELL", "date": "2025-03-01",
                 "time": "09:30:00", "symbol": self.SHORT_CALL,
                 "quantity": 2, "price": 0.5, "net_amount": 100.0,
                 "currency": "CAD"},
                # true long LEAPS entry (control)
                {"action": "BUYSELL", "date": "2025-02-05",
                 "time": "09:30:00", "symbol": self.LONG_CALL,
                 "quantity": 1, "price": 4.0, "net_amount": 400.0,
                 "currency": "CAD"}]}))
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": "2025"}, "transactions": [
                {"symbol": self.SHORT_CALL, "date": "2025-03-01",
                 "qty": -2, "proceeds": 300.0, "cost": 100.0,
                 "gain": 200.0, "direction": "SHORT",
                 "currency": "CAD", "days_held": 28},
                {"symbol": self.LONG_CALL, "date": "2025-06-01",
                 "qty": 1, "proceeds": 500.0, "cost": 400.0,
                 "gain": 100.0, "currency": "CAD", "days_held": 116}],
             "inventory": []}))
        return root

    def test_short_call_close_not_in_leaps_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "leaps-sum", "--json"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        contracts = [row.get("contract") for row in doc["rows"]]
        self.assertIn(self.LONG_CALL, contracts)          # control
        self.assertNotIn(self.SHORT_CALL, contracts)      # was leaking


def _sp(d, old, new):
    return {"action": "SPLIT", "date": d, "date_settle": d,
            "time": "09:30:00", "symbol": old, "symbol_new": new,
            "quantity": 1, "net_amount": 0, "currency": "CAD",
            "account": "margin"}


class TestFuzzFG_RenameFamilyAndFifo(unittest.TestCase):
    """#F8 stranded bump, #F10 opposite-sign merge, #F11 signed short
    basis, #G FIFO order after rename-merge."""

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


class TestFuzzE_ShortConventionCanonical(unittest.TestCase):
    """#5/#6/#21: ONE cross-engine convention for shorts.

    Disposition rows: cost = -opening premium, proceeds = -close cost,
    so gain == proceeds - cost direction-free (the documented signed
    cash-flow convention — USA had the legs swapped). Inventory:
    total_cost NEGATIVE (proceeds credited; cost/share positive — the
    holdings-export convention; Canada leaked its internal positive
    sign through). ccd-sum's PREMIUM/BUYBACK identity follows.
    """

    CALL = "XYZ250321C00012000.TO"

    @classmethod
    def _open_close(cls):
        sto = _tx("2025-01-05", -1, 1.0, sym=cls.CALL)   # premium 100 in
        sto["net_amount"] = 100.0
        btc = _tx("2025-02-10", 1, 0.4, sym=cls.CALL)    # buyback 40 out
        btc["net_amount"] = 40.0
        return [sto, btc]

    @staticmethod
    def _project(tmp, country, gains):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            f'[settings]\nyear = 2025\ncountry = "{country}"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps(gains))
        return root

    def test_rows_identical_and_identity_holds_both_engines(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                t = _gains(country, self._open_close())["transactions"][0]
                self.assertEqual(t["direction"], "SHORT")
                self.assertAlmostEqual(t["cost"], -100.0, places=2)
                self.assertAlmostEqual(t["proceeds"], -40.0, places=2)
                self.assertAlmostEqual(t["gain"], 60.0, places=2)
                self.assertAlmostEqual(
                    t["gain"], t["proceeds"] - t["cost"], places=2)

    def test_short_inventory_negative_cost_both_engines(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                inv = _gains(country,
                             self._open_close()[:1])["inventory"][0]
                self.assertAlmostEqual(inv["qty"], -1.0)
                self.assertAlmostEqual(inv["total_cost"], -100.0,
                                       places=2)   # credit, not cost

    def test_ccd_sum_premium_buyback_identity_both_engines(self):
        # End-to-end: engine gains file → ccd-sum. PREMIUM/BUYBACK were
        # negated-and-swapped (canada) or identity-breaking (usa).
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as tmp:
                root = self._project(tmp, country,
                                     _gains(country, self._open_close()))
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run",
                     "-C", str(root), "ccd-sum", "--json"],
                    cwd=REPO_ROOT, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                row = json.loads(r.stdout)["rows"][0]
                self.assertAlmostEqual(row["proceeds"], 100.0, places=2)
                self.assertAlmostEqual(row["cost"], 40.0, places=2)
                self.assertAlmostEqual(row["gain"], 60.0, places=2)

    def test_list_cost_and_cost_per_share_convention(self):
        # Short row: COST -100 (credit) with COST/SH +100 — signed
        # division, matching the holdings export convention.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                tmp, "canada", _gains("canada", self._open_close()[:1]))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run",
                 "-C", str(root), "list", "--json"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        row = json.loads(r.stdout)["rows"][0]
        self.assertAlmostEqual(row["cost"], -100.0, places=2)
        self.assertAlmostEqual(row["cost_per_share"], 100.0, places=2)


if __name__ == "__main__":
    unittest.main()
