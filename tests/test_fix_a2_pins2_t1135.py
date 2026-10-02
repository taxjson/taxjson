"""Re-audit-2 test pins for the T1135 cost walk and report and for
split-gains (fix lists tests-pins-05: A2-0172, A2-0183, A2-0184,
A2-0185, A2-0186, A2-0883, A2-0914, A2-0915, A2-0916, A2-0923,
A2-1537, A2-1552, A2-1553, A2-1589). Each test fails under the mutant
its finding names. All data is synthetic."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO = Path(__file__).resolve().parent.parent


def _r(action, date, symbol, qty, net, settle=None, time="10:00:00",
       **kw):
    """A base-book row; net is signed as the parsers write it (a buy
    negative, a sale positive)."""
    row = {"action": action, "date": date, "date_settle": settle or date,
           "time": time, "symbol": symbol, "quantity": qty,
           "net_amount": net, "currency": "CAD", "account": "margin",
           "id": kw.pop("id", f"{action}-{date}-{symbol}-{qty}-{time}")}
    row.update(kw)
    return row


def _t(i, d, sym, q, p, account="margin", time="10:00:00"):
    amt = abs(q * p)
    return dict(id=i, date=d, date_settle=d, time=time, action="BUYSELL",
                symbol=sym, quantity=q, price=p, gross_amount=amt,
                net_amount=-amt if q > 0 else amt, commission=0.0,
                fee=0.0, currency="CAD", account=account)


def _walk(rows, overrides=None, **kw):
    from taxjson.bin.taxjson_t1135 import walk_costs
    with contextlib.redirect_stderr(io.StringIO()):
        return walk_costs(rows, 2025, dict(overrides or {}),
                          today="2026-06-01", **kw)


def _report(base_rows, gains_doc_by_name=None, year=2025, **kw):
    from taxjson.bin.taxjson_t1135 import build_report
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "margin_base.json"
        base.write_text(json.dumps({"transactions": base_rows}))
        gains = []
        for name, doc in (gains_doc_by_name or {}).items():
            g = Path(td) / name
            g.write_text(json.dumps(doc))
            gains.append(g)
        kw.setdefault("today", "2026-06-01")
        with contextlib.redirect_stderr(io.StringIO()):
            return build_report([base], gains, year, {}, "CAD", **kw)


def _engine_gains(base: Path, year: int, out: Path, *extra) -> dict:
    g = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_gains", "--country",
         "canada", "--year", str(year), "--taxable", *extra, str(base)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env=dict(os.environ, PYTHONPATH=str(REPO / "src")))
    assert g.returncode == 0, g.stderr[-2000:]
    out.write_text(g.stdout)
    return json.loads(g.stdout)


# ============================================== --year-wash-only deferral
class TestYearOnlyDeferralSumsAccounts(unittest.TestCase):
    """A2-1552: each account's split gains file carries its share of a
    blended pool's deferral, so the excluded amount is their sum."""

    @rule("CA-RPT-12")
    def test_two_accounts_shares_are_summed(self):
        base = [_r("BUYSELL", "2024-03-11", "XYZ.US", 900, -63000.0,
                   account="b"),
                _r("BUYSELL", "2024-03-12", "XYZ.US", 100, -7000.0,
                   account="a")]
        inv = lambda acct, q, c, d: {"inventory": [
            {"account": acct, "symbol": "XYZ.US", "qty": q,
             "total_cost": c, "deferred_wash": d}], "transactions": []}
        rep = _report(base, {"a_gains_wash.json": inv("a", 100, 12000, 5000),
                             "b_gains_wash.json": inv("b", 900, 108000,
                                                      45000)},
                      full_history=False)
        self.assertEqual(rep["deferred_wash_not_in_cost"],
                         {"XYZ.US": 50000.0})

    def test_same_account_twice_counts_once(self):
        from taxjson.bin.taxjson_t1135 import _deferred_wash
        with tempfile.TemporaryDirectory() as td:
            doc = {"inventory": [{"account": "a", "symbol": "XYZ.US",
                                  "deferred_wash": 5000.0}]}
            ps = []
            for n in ("a_gains.json", "a_gains_wash.json"):
                p = Path(td) / n
                p.write_text(json.dumps(doc))
                ps.append(p)
            self.assertEqual(_deferred_wash(ps, {}), {"XYZ.US": 5000.0})



# ======================================================= walk: splits
class TestWalkSplitAndRename(unittest.TestCase):
    """A2-0172, A2-0185, A2-1537: a split re-denominates the pool, so a
    later partial sale is costed per post-split unit; a rename carries
    the year maximum and the taint; a dividend-typed ADJUST is income."""

    @rule("CA-RPT-12")
    def test_split_then_sale(self):
        w = _walk([_r("BUYSELL", "2025-01-02", "AAA.US", 1000, -150000.0),
                   _r("SPLIT", "2025-03-01", "AAA.US", 2.0, 0.0,
                      time="00:00:00"),
                   _r("BUYSELL", "2025-06-02", "AAA.US", -1000, 80000.0)])
        a = w["per_symbol"]["AAA.US"]
        self.assertEqual((a["max_cost"], a["year_end_cost"]),
                         (150000.0, 75000.0))

    @rule("CA-RPT-12")
    def test_split_booked_by_two_accounts_applies_once(self):
        w = _walk([_r("BUYSELL", "2025-01-02", "AAA.US", 1000, -150000.0),
                   _r("SPLIT", "2025-03-01", "AAA.US", 2.0, 0.0,
                      time="00:00:00", account="a"),
                   _r("SPLIT", "2025-03-01", "AAA.US", 2.0, 0.0,
                      time="00:00:00", account="b", id="sp2"),
                   _r("BUYSELL", "2025-06-02", "AAA.US", -1500, 80000.0)])
        self.assertEqual(w["per_symbol"]["AAA.US"]["year_end_cost"],
                         37500.0)

    @rule("CA-RPT-12")
    def test_rename_carries_the_year_maximum(self):
        w = _walk([_r("BUYSELL", "2025-01-10", "BBB.US", 100, -10000.0),
                   _r("BUYSELL", "2025-03-03", "BBB.US", -50, 6000.0),
                   _r("SPLIT", "2025-06-02", "BBB.US", 1.0, 0.0,
                      symbol_new="CCC.US", time="00:00:00")])
        c = w["per_symbol"]["CCC.US"]
        self.assertEqual((c["max_cost"], c["year_end_cost"]),
                         (10000.0, 5000.0))

    @rule("CA-RPT-12")
    def test_dividend_typed_adjust_is_not_cost(self):
        w = _walk([_r("BUYSELL", "2025-01-10", "DDD.US", 10, -500.0),
                   _r("ADJUST", "2025-04-01", "DDD.US", 0, -100.0,
                      type="dividend")])
        self.assertEqual(w["per_symbol"]["DDD.US"]["year_end_cost"], 500.0)

    def test_pools_start_untainted_and_taint_follows_a_rename(self):
        w = _walk([_r("BUYSELL", "2025-01-10", "DDD.US", 10, -500.0),
                   _r("OPENING_BALANCE", "2024-01-02", "OLD.US", 100, 0.0),
                   _r("BUYSELL", "2025-01-02", "OLD.US", 100, -5000.0),
                   _r("SPLIT", "2025-03-01", "OLD.US", 1.0, 0.0,
                      symbol_new="NEW.US", time="00:00:00")])
        self.assertFalse(w["per_symbol"]["DDD.US"]["unknown_acb"])
        self.assertTrue(w["per_symbol"]["NEW.US"]["unknown_acb"])


# =============================================== walk: rows to step over
class TestWalkStepsOverNonCapitalRows(unittest.TestCase):
    """A2-0183, A2-0916: income, TRANSFER, futures and domestic rows
    early in the book never end the walk or hide later properties."""

    def _book(self):
        return [
            _r("BUYSELL", "2025-01-05", "CCC.TO", 100, -5000.0),
            _r("TRANSFER", "2025-01-06", "AAA.US", 0, 0.0),
            _r("DIVIDEND", "2025-01-07", "AAA.US", 0, 50.0,
               type="dividend"),
            _r("BUYSELL", "2025-01-08", "", 0, 0.0),
            _r("BUYSELL", "2024-03-03", "F:ESZ4.US", 1, -1.0),
            _r("BUYSELL", "2025-01-09", "F:SXFZ5.TO", 1, -1.0),
            _r("BUYSELL", "2025-01-09", "F:ESZ5.US", 1, -1.0),
            _r("BUYSELL", "2025-01-15", "AAA.US", 400, -80000.0),
            _r("BUYSELL", "2025-03-04", "BBB.L", 1000, -40000.0),
            _r("BUYSELL", "2025-06-02", "BBB.L", -300, 15000.0),
            _r("BUYSELL", "2025-09-02", "DDD.US", 100, -10000.0),
            _r("BUYSELL", "2025-10-01", "DDD.US", -100, 12000.0),
        ]

    @rule("CA-RPT-01", "CA-RPT-12")
    def test_case_book(self):
        w = _walk(self._book())
        got = {s: (v["max_cost"], v["year_end_cost"])
               for s, v in w["per_symbol"].items()}
        self.assertEqual(got, {"AAA.US": (80000.0, 80000.0),
                               "BBB.L": (40000.0, 28000.0),
                               "DDD.US": (10000.0, 0.0)})
        self.assertEqual(w["max_total_cost"], 120000.0)
        # Only this year's FOREIGN futures are named.
        self.assertEqual(w["futures_symbols"], ["F:ESZ5.US"])

    @rule("CA-RPT-12")
    def test_zero_cost_shares_have_no_cost_amount(self):
        w = _walk([_r("BUYSELL", "2025-03-03", "FREE.US", 10, 0.0),
                   _r("OPENING_BALANCE", "2024-01-02", "TNT.US", 5, 0.0),
                   _r("BUYSELL", "2025-04-03", "TNT.US", -5, 500.0)])
        # Free shares untainted and costless: not a property row; the
        # drained phantom pool is not listed either.
        self.assertEqual(w["per_symbol"], {})

    def test_expired_options_list_names_only_long_options(self):
        call = "XYZ250620C00060000.US"
        w = _walk([_r("BUYSELL", "2025-03-03", call, 2, -800.0),
                   _r("BUYSELL", "2025-03-03", "AAA.US", 10, -800.0),
                   _r("BUYSELL", "2025-03-03", "BTC", 0.1, -8000.0),
                   _r("BUYSELL", "2025-03-03",
                      "QZZ251219C00060000.TO", 1, -100.0)])
        self.assertEqual(w["expired_options_held"], [call])


# ================================================= walk: option and pools
class TestWalkPoolArithmetic(unittest.TestCase):
    """A2-0184: multi-contract premium folds, crossing zero and a
    fractional pool."""
    CALL = "XYZ250620C00060000.US"
    PUT = "XYZ250620P00050000.US"

    @rule("CA-RPT-12")
    def test_exercised_calls_fold_per_unit_cost(self):
        w = _walk([_r("BUYSELL", "2025-03-03", self.CALL, 3, -1200.0),
                   _r("BUYSELL", "2025-03-04", self.CALL, -1, 500.0),
                   _r("ASSIGN", "2025-06-20", self.CALL, -2, 0.0),
                   _r("BUYSELL", "2025-06-20", "XYZ.US", 200, -12000.0)])
        self.assertEqual(w["per_symbol"]["XYZ.US"]["year_end_cost"],
                         12800.0)

    @rule("CA-RPT-12")
    def test_assigned_written_puts_lower_the_cost(self):
        w = _walk([_r("BUYSELL", "2025-03-03", self.PUT, -2, 600.0),
                   _r("BUYSELL", "2025-06-20", "XYZ.US", 200, -10000.0,
                      time="16:00:00"),
                   _r("ASSIGN", "2025-06-20", self.PUT, 2, 0.0,
                      time="16:00:00")])
        self.assertEqual(w["per_symbol"]["XYZ.US"]["year_end_cost"],
                         9400.0)

    @rule("CA-RPT-12")
    def test_crossing_zero_both_ways(self):
        up = _walk([_r("BUYSELL", "2025-02-03", "AAA.US", -100, 10000.0),
                    _r("BUYSELL", "2025-03-03", "AAA.US", 300, -27000.0),
                    _r("BUYSELL", "2025-04-03", "AAA.US", -100, 9500.0)])
        self.assertEqual(up["per_symbol"]["AAA.US"]["year_end_cost"],
                         9000.0)
        down = _walk([_r("BUYSELL", "2025-02-03", "AAA.US", 100, -10000.0),
                      _r("BUYSELL", "2025-03-03", "AAA.US", -300, 33000.0),
                      _r("BUYSELL", "2025-04-03", "AAA.US", 500, -50000.0),
                      _r("BUYSELL", "2025-05-03", "AAA.US", -150, 16000.0)])
        self.assertEqual(down["per_symbol"]["AAA.US"]["year_end_cost"],
                         15000.0)

    @rule("CA-RPT-12")
    def test_fractional_coin_pool(self):
        w = _walk([_r("BUYSELL", "2025-02-03", "BTC", 0.5, -40000.0),
                   _r("BUYSELL", "2025-03-03", "BTC", -0.2, 20000.0),
                   _r("BUYSELL", "2025-04-03", "BTC", -0.1, 9000.0)])
        self.assertEqual(w["per_symbol"]["BTC"]["year_end_cost"], 16000.0)



# ===================================== report: superficial-loss additions
def _engine_report(rows, year=2025, sheltered=None, **kw):
    """build_report over a base book with the engine's own gains file
    (full-history pass by default), plus the engine inventory."""
    from taxjson.bin.taxjson_t1135 import build_report
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "margin_base.json"
        base.write_text(json.dumps({"transactions": rows}))
        extra, sh_paths = [], []
        if sheltered is not None:
            sh = Path(tmp) / "tfsa_base.json"
            sh.write_text(json.dumps({"transactions": sheltered}))
            extra, sh_paths = ["--sheltered", str(sh)], [sh]
        gains = Path(tmp) / "margin_gains_wash.json"
        doc = _engine_gains(base, year, gains, *extra)
        kw.setdefault("today", "2026-10-01")
        with contextlib.redirect_stderr(io.StringIO()):
            rep = build_report([base], [gains], year, {}, "CAD",
                               sheltered_paths=sh_paths, **kw)
    inv = {i["symbol"]: i for i in doc["inventory"]}
    props = {r["symbol"]: r for r in rep["properties"]}
    return rep, props, inv


class TestWashAdditionPlacement(unittest.TestCase):
    """A2-0186, A2-0883, A2-0915, A2-1553: a replacement bought BEFORE
    the loss takes the s.53(1)(f) addition right after the losing sale
    (wash_after), so a second fill at the same second sees it — the
    walk's Dec-31 cost is the engine's ACB."""

    @rule("CA-RPT-12")
    def test_pre_loss_replacement_then_same_second_fill(self):
        rows = [_t("b1", "2025-01-02", "AAA.US", 300, 100.0),
                _t("b2", "2025-01-20", "AAA.US", 100, 90.0),
                _t("s1", "2025-02-03", "AAA.US", -100, 80.0),
                _t("s2", "2025-02-03", "AAA.US", -100, 80.0)]
        _rep, props, inv = _engine_report(rows)
        self.assertEqual(props["AAA.US"]["year_end_cost"], 20666.67)
        self.assertEqual(props["AAA.US"]["year_end_cost"],
                         round(inv["AAA.US"]["total_cost"], 2))

    @rule("CA-RPT-12")
    def test_two_same_second_sells(self):
        rows = [_t("b1", "2024-03-01", "XYZ.US", 100, 10),
                _t("b2", "2024-11-01", "XYZ.US", 100, 5),
                _t("s1", "2024-11-08", "XYZ.US", -100, 5),
                _t("s2", "2024-11-08", "XYZ.US", -50, 5)]
        _rep, props, inv = _engine_report(rows)
        self.assertAlmostEqual(inv["XYZ.US"]["total_cost"], 625.0, 2)
        self.assertEqual(props["XYZ.US"]["year_end_cost"], 625.0)

    @rule("CA-RPT-12")
    def test_two_same_second_loss_fills_part_sold_later(self):
        rows = [_t("b1", "2024-03-01", "XYZ.US", 100, 10),
                _t("b2", "2024-11-01", "XYZ.US", 60, 5),
                _t("s1", "2024-11-08", "XYZ.US", -50, 5),
                _t("s2", "2024-11-08", "XYZ.US", -50, 4),
                _t("s3", "2024-11-20", "XYZ.US", -30, 6)]
        _rep, props, inv = _engine_report(rows)
        self.assertEqual(props["XYZ.US"]["year_end_cost"],
                         round(inv["XYZ.US"]["total_cost"], 2))
        self.assertEqual(props["XYZ.US"]["year_end_cost"], 343.98)

    @rule("CA-RPT-12")
    def test_denial_settling_dec_31_is_in_the_year_end_cost(self):
        def r(i, d, ds, q, px):
            return {"id": i, "action": "BUYSELL", "date": d,
                    "date_settle": ds, "time": "10:00:00",
                    "symbol": "AAA.US", "quantity": q, "price": px,
                    "net_amount": -q * px, "currency": "CAD",
                    "account": "margin", "commission": 0.0}
        rows = [r("b1", "2025-06-02", "2025-06-03", 100, 100.0),
                r("b2", "2025-12-15", "2025-12-16", 100, 60.0),
                r("s1", "2025-12-30", "2025-12-31", -100, 50.0)]
        rep = _report(rows, {"margin_gains.json": {"transactions": []}})
        (p,) = rep["properties"]
        self.assertEqual(p["year_end_cost"], 11000.0)

    @rule("CA-RPT-12")
    def test_sheltered_books_join_the_full_history_pass(self):
        rows = [_t("b1", "2024-03-01", "XYZ.US", 100, 10),
                _t("s1", "2024-11-08", "XYZ.US", -50, 5),
                _t("b2", "2024-11-15", "XYZ.US", 50, 5)]
        sh = [_t("r1", "2024-11-10", "XYZ.US", 50, 5, account="tfsa")]
        _rep, props, inv = _engine_report(rows, sheltered=sh)
        self.assertAlmostEqual(inv["XYZ.US"]["total_cost"], 750.0, 2)
        self.assertEqual(props["XYZ.US"]["year_end_cost"], 750.0)


class TestWashAdjustmentRows(unittest.TestCase):
    """A2-0916, A2-0923, A2-1553: wash_adjustments' landings, their
    de-dup, and the legacy fallback for gains records written before
    `adjusts` existed."""
    BASE = [_r("BUYSELL", "2025-01-02", "AAA.US", 100, -10000.0, id="b1"),
            _r("BUYSELL", "2025-01-20", "AAA.US", 100, -9000.0, id="b2"),
            _r("BUYSELL", "2025-02-03", "AAA.US", -100, 8000.0, id="s1"),
            _r("BUYSELL", "2025-03-03", "BBB.US", 10, -1000.0, id="b3"),
            _r("BUYSELL", "2025-03-10", "BBB.US", -10, 800.0, id="s2"),
            _r("BUYSELL", "2025-03-20", "BBB.US", 10, -700.0, id="b4")]

    def _rows(self, wash):
        from taxjson.bin.taxjson_t1135 import wash_adjustments
        return wash_adjustments(wash, self.BASE)

    def _landing(self, aid, sym, amt, date, after=None):
        return {"id": aid, "symbol": sym, "amount": amt, "date": date,
                "date_settle": date, "time": "10:00:00",
                "account": "margin", "after": after}

    def test_every_record_and_its_wash_after(self):
        w1 = {"loss_tx_id": "s1", "adjusts": [
            self._landing("a1", "AAA.US", 1500.0, "2025-02-03", "s1"),
            self._landing("a1", "AAA.US", 1500.0, "2025-02-03", "s1")]}
        w2 = {"loss_tx_id": "s2", "adjusts": [
            self._landing("a2", "BBB.US", 200.0, "2025-03-20")]}
        rows = self._rows([w1, w2])
        got = [(r["id"], r["symbol"], r["net_amount"], r.get("wash_after"))
               for r in rows]
        # One row per landing id (the duplicate is dropped), both
        # records read, the pre-loss landing placed after its sale.
        self.assertEqual(got, [("a1", "AAA.US", 1500.0, "s1"),
                               ("a2", "BBB.US", 200.0, None)])

    def test_place_after_moves_the_row_behind_its_sale(self):
        from taxjson.bin.taxjson_t1135 import _place_after
        rows = [dict(r) for r in self.BASE[:3]]
        rows.insert(0, {"id": "a1", "action": "ADJUST", "symbol": "AAA.US",
                        "wash_after": "s1"})
        out = [r["id"] for r in _place_after(rows)]
        self.assertEqual(out, ["b1", "b2", "s1", "a1"])
        plain = [dict(r) for r in self.BASE[:3]]
        self.assertEqual([r["id"] for r in _place_after(plain)],
                         ["b1", "b2", "s1"])

    def test_legacy_record_is_stamped_at_the_later_of_sale_and_lot(self):
        # The trigger lot (b2) was bought before the loss (s1): the
        # addition lands at the sale.
        w = {"loss_tx_id": "s1", "trigger_lot_id": "b2",
             "adjust_cmd": "ADJUST 2025-02-03 10:00:00 AAA.US CAD 1500"}
        (row,) = self._rows([w, dict(w)])
        self.assertEqual((row["symbol"], row["net_amount"], row["date"]),
                         ("AAA.US", 1500.0, "2025-02-03"))
        self.assertEqual(row["id"], "wash:s1:b2")
        # Replacement after the loss (b4 after s2): stamped at the lot.
        w = {"loss_tx_id": "s2", "trigger_lot_id": "b4", "amount": 200.0}
        (row,) = self._rows([w])
        self.assertEqual((row["symbol"], row["net_amount"], row["date"]),
                         ("BBB.US", 200.0, "2025-03-20"))

    @rule("CA-RPT-12")
    def test_legacy_record_reaches_the_cost(self):
        from taxjson.bin.taxjson_t1135 import walk_costs
        w = {"loss_tx_id": "s2", "trigger_lot_id": "b4", "amount": 200.0}
        txs = list(self.BASE) + self._rows([w])
        with contextlib.redirect_stderr(io.StringIO()):
            walk = walk_costs(txs, 2025, {}, today="2026-06-01")
        self.assertEqual(walk["per_symbol"]["BBB.US"]["year_end_cost"],
                         900.0)


# ======================================================= report columns
class TestReportColumns(unittest.TestCase):
    """A2-0883, A2-0914: per-country sums over two properties, the
    income column over several dividends, domestic income rows left
    out, an unknown suffix listed for review, and a stage file refused
    as --gains."""

    BASE = [_r("BUYSELL", "2025-01-10", "CCC.TO", 100, -5000.0),
            _r("BUYSELL", "2025-01-15", "AAA.US", 400, -80000.0),
            _r("BUYSELL", "2025-02-03", "DDD.US", 100, -10000.0),
            _r("BUYSELL", "2025-10-01", "DDD.US", -100, 12000.0),
            _r("BUYSELL", "2025-03-04", "BBB.L", 1000, -40000.0),
            _r("BUYSELL", "2025-06-02", "BBB.L", -300, 15000.0),
            _r("BUYSELL", "2025-04-02", "NEW.L", 100, -5000.0),
            _r("BUYSELL", "2025-04-01", "EEE.ZZ", 10, -1000.0)]
    GAINS = {"transactions": [
        {"action": "DIVIDEND", "symbol": "AAA.US", "date": "2025-02-14",
         "date_settle": "2025-02-14", "dividend": 50.0},
        {"action": "DIVIDEND", "symbol": "AAA.US", "date": "2025-05-15",
         "date_settle": "2025-05-15", "dividend": 60.0},
        {"action": "DIVIDEND", "symbol": "AAA.US", "date": "2025-08-15",
         "date_settle": "2025-08-15", "dividend": 70.0},
        {"action": "DIVIDEND", "symbol": "CCC.TO", "date": "2025-03-03",
         "date_settle": "2025-03-03", "dividend": 150.0}]}

    @rule("CA-RPT-01", "CA-RPT-02")
    def test_columns(self):
        rep = _report(self.BASE, {"margin_gains_wash.json": self.GAINS},
                      full_history=False)
        props = {r["symbol"]: r for r in rep["properties"]}
        self.assertNotIn("CCC.TO", props)
        self.assertEqual(props["AAA.US"]["income"], 180.0)
        self.assertEqual(set(rep["by_country"]), {"USA", "GBR", "??"})
        usa = rep["by_country"]["USA"]
        self.assertEqual((usa["max_cost"], usa["year_end_cost"],
                          usa["income"]), (90000.0, 80000.0, 180.0))
        gbr = rep["by_country"]["GBR"]
        self.assertEqual((gbr["max_cost"], gbr["year_end_cost"]),
                         (45000.0, 33000.0))
        self.assertEqual(rep["review_symbols"], ["EEE.ZZ"])
        self.assertTrue(rep["filing_required"])

    def test_stage_file_as_gains_is_refused(self):
        from taxjson.bin.taxjson_t1135 import UnreadableGains, build_report
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": self.BASE}))
            with contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(UnreadableGains):
                build_report([base], [base], 2025, {}, "CAD",
                             today="2026-06-01", full_history=False)


class TestYearOnlyDeferralNetOfTheYearsAdditions(unittest.TestCase):
    """A2-0916: --year-wash-only names the deferral beyond the year's
    own additions (which the walk already carries)."""

    @rule("CA-RPT-12")
    def test_deferral_beyond_the_years_own(self):
        base = [_r("BUYSELL", "2024-01-10", "XYZ.US", 100, -10000.0,
                   id="b1"),
                _r("BUYSELL", "2025-02-03", "XYZ.US", 100, -9000.0,
                   id="b2")]
        gains = {"transactions": [],
                 "wash_sales": [{"loss_tx_id": "s0", "adjusts": [
                     {"id": "a1", "symbol": "XYZ.US", "amount": 1000.0,
                      "date": "2025-02-03", "date_settle": "2025-02-03",
                      "time": "10:00:00", "account": "margin"}]}],
                 "inventory": [{"account": "margin", "symbol": "XYZ.US",
                                "deferred_wash": 3000.0}]}
        rep = _report(base, {"margin_gains_wash.json": gains},
                      full_history=False)
        self.assertEqual(rep["deferred_wash_not_in_cost"],
                         {"XYZ.US": 2000.0})
        (p,) = rep["properties"]
        self.assertEqual(p["year_end_cost"], 20000.0)



# ========================================== split-gains position start
class TestSplitGainsPositionStart(unittest.TestCase):
    """A2-1589, A2-1537: the account's position start follows a split's
    ratio (a partial sale after a 2-for-1 does not cross zero) and a
    rename's new symbol."""

    def _starts(self, rows):
        from taxjson.bin.taxjson_split_gains import _position_starts
        return _position_starts(rows, "settle")

    def test_split_then_partial_sale_keeps_the_start(self):
        rows = [_r("BUYSELL", "2023-03-01", "AAA.US", 100, -1000.0),
                _r("SPLIT", "2024-06-03", "AAA.US", 2.0, 0.0,
                   time="00:00:00"),
                _r("BUYSELL", "2025-02-03", "AAA.US", -150, 2000.0)]
        self.assertEqual(self._starts(rows), {"AAA.US": "2023-03-01"})

    def test_rename_moves_the_start_to_the_new_symbol(self):
        rows = [_r("BUYSELL", "2023-03-01", "OLD.US", 100, -1000.0),
                _r("SPLIT", "2024-06-03", "OLD.US", 1.0, 0.0,
                   symbol_new="NEW.US", time="00:00:00"),
                _r("BUYSELL", "2025-02-03", "NEW.US", -50, 600.0)]
        self.assertEqual(self._starts(rows), {"NEW.US": "2023-03-01"})


# ========================================= leaps: renamed contract ratio
class TestLeapsRenameRatio(unittest.TestCase):
    """A2-1537: a rename-SPLIT of a contract carries its quantity times
    the ratio, for the report's quantity and for the buy-to-close test."""
    OLD = "QZA270115C00150000.US"
    NEW = "QZA1270115C00150000.US"

    def _leaps(self, rows):
        from taxjson.bin.taxjson_run import _leaps_contracts
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n[accounts.margin]\n'
                'type = "taxable"\n')
            (root / "work").mkdir()
            (root / "work" / "margin_raw.json").write_text(
                json.dumps({"transactions": rows}))
            return _leaps_contracts(root, None, "leaps")

    def test_long_contract_quantity_follows_the_ratio(self):
        got = self._leaps([
            _r("BUYSELL", "2024-12-16", self.OLD, 2, -2400.0),
            _r("SPLIT", "2025-02-03", self.OLD, 2.0, 0.0,
               symbol_new=self.NEW)])
        self.assertEqual(got[self.NEW], 4.0)

    def test_buy_closing_a_renamed_short_is_not_a_leaps(self):
        got = self._leaps([
            _r("BUYSELL", "2024-12-16", self.OLD, -2, 2400.0),
            _r("SPLIT", "2025-02-03", self.OLD, 2.0, 0.0,
               symbol_new=self.NEW),
            _r("BUYSELL", "2025-03-03", self.NEW, 3, -1800.0)])
        self.assertEqual(got, {})


# ============================== code paths no test ran (A2-0923)
class TestPhantomRelevanceOnTradeDates(unittest.TestCase):
    """A2-0923: on a trade-date project a Dec-31 short sale settling in
    January draws on the phantom state in the TRADE year."""

    def test_trade_basis_dates_the_row_by_its_trade_date(self):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.phantom_holdings import (assess_tax_year_relevance,
                                                  detect_phantoms)
        txs = [TaxTransaction(action="BUYSELL", date="2025-12-31",
                              date_settle="2026-01-02", time="10:00:00",
                              symbol="QZP.US", quantity=-10,
                              net_amount=2000.0, account="margin",
                              currency="USD")]
        cands = detect_phantoms(txs)
        trade = assess_tax_year_relevance(txs, cands, 2025,
                                          date_basis="trade")[0]
        self.assertTrue(trade.affects_year)
        self.assertEqual(trade.last_in_year_date, "2025-12-31")
        settle = assess_tax_year_relevance(txs, cands, 2025,
                                           date_basis="settle")[0]
        self.assertFalse(settle.affects_year)


class TestRadarAdvisorySentences(unittest.TestCase):
    """A2-0923: the LOCKED partial-sale sentence (Canada) and the US
    pre-window IRA sentence."""

    @rule("CA-PLAN-01")
    def test_locked_with_a_taxable_buy_names_the_partial_sale(self):
        from test_fix_planning import _row
        from test_fixl_planning import _rows
        tax = [_row("2026-01-05", "ZZL.TO", 100, -2000.0),
               _row("2026-09-20", "ZZL.TO", 10, -100.0)]
        sh = [_row("2026-09-22", "ZZL.TO", 10, -100.0, account="tfsa")]
        r = _rows(tax, "2026-09-29", sheltered=sh)["ZZL.TO"]
        self.assertEqual(r["category"], "LOCKED")
        self.assertIn("A PARTIAL loss sale is also superficial for the "
                      "taxable buy on 2026-09-20", r["advisory"])

    @rule("US-PLAN-01")
    def test_us_pre_window_ira_holding_is_named(self):
        from test_fix_planning import _row
        from test_fixl_planning import _rows
        tax = [_row("2026-01-05", "ZZL.US", 100, -2000.0, currency="USD"),
               _row("2026-09-20", "ZZL.US", 10, -100.0, currency="USD")]
        sh = [_row("2026-01-05", "ZZL.US", 10, -100.0, account="ira",
                   currency="USD")]
        r = _rows(tax, "2026-09-29", sheltered=sh, country="usa")["ZZL.US"]
        self.assertEqual(r["category"], "EXITABLE")
        self.assertIn("IRAs hold 10 sh bought before the window — they do "
                      "not make this loss a wash sale", r["advisory"])
        self.assertNotIn("superficial", r["advisory"])


if __name__ == "__main__":
    unittest.main()
