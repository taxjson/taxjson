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
    """A2-1552 once summed each account's share of a blended pool's
    deferral for --year-wash-only. The cost amounts are now the engine's
    own pools (backlog TJ-0417): a loss denied before the year is in two
    accounts' shared pool, and nothing is named as left out."""

    @rule("CA-RPT-12")
    def test_two_accounts_shares_are_summed(self):
        base = [_r("BUYSELL", "2024-03-11", "XYZ.US", 900, -63000.0,
                   account="b", id="b1"),
                _r("BUYSELL", "2024-11-08", "XYZ.US", -900, 18000.0,
                   account="b", id="s1"),
                _r("BUYSELL", "2024-11-15", "XYZ.US", 900, -18000.0,
                   account="a", id="b2")]
        for full in (True, False):
            rep = _report(base, {"a_gains_wash.json": {"transactions": []}},
                          full_history=full)
            self.assertEqual(rep["deferred_wash_not_in_cost"], {})
            (p,) = rep["properties"]
            self.assertEqual(p["year_end_cost"], 63000.0)



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
    def test_zero_cost_shares_and_a_sold_unknown_cost_stay_listed(self):
        w = _walk([_r("BUYSELL", "2025-03-03", "FREE.US", 10, 0.0),
                   _r("OPENING_BALANCE", "2024-01-02", "TNT.US", 5, 0.0),
                   _r("BUYSELL", "2025-04-03", "TNT.US", -5, 500.0)])
        # Free shares held at a known zero cost are still property; the
        # opening of unknown cost held until April leaves the year's
        # maximum unknown after the sale (it once dropped out with it).
        self.assertEqual(w["per_symbol"]["FREE.US"]["max_cost"], 0)
        self.assertFalse(w["per_symbol"]["FREE.US"]["unknown_acb"])
        self.assertTrue(w["per_symbol"]["TNT.US"]["unknown_acb"])
        self.assertEqual(w["per_symbol"]["TNT.US"]["year_end_cost"], 0)

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
    CALL = "QZY250718C00060000.US"
    PUT = "QZY250718P00050000.US"

    @rule("CA-RPT-12")
    def test_exercised_calls_fold_per_unit_cost(self):
        w = _walk([_r("BUYSELL", "2025-03-03", self.CALL, 3, -1200.0),
                   _r("BUYSELL", "2025-03-04", self.CALL, -1, 500.0),
                   _r("ASSIGN", "2025-07-18", self.CALL, -2, 0.0),
                   _r("BUYSELL", "2025-07-18", "QZY.US", 200, -12000.0)])
        self.assertEqual(w["per_symbol"]["QZY.US"]["year_end_cost"],
                         12800.0)

    @rule("CA-RPT-12")
    def test_assigned_written_puts_lower_the_cost(self):
        w = _walk([_r("BUYSELL", "2025-03-03", self.PUT, -2, 600.0),
                   _r("BUYSELL", "2025-07-18", "QZY.US", 200, -10000.0,
                      time="16:00:00"),
                   _r("ASSIGN", "2025-07-18", self.PUT, 2, 0.0,
                      time="16:00:00")])
        self.assertEqual(w["per_symbol"]["QZY.US"]["year_end_cost"],
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
    (wash_after: after its last fill — one sale, one formula, CA-SL-08)
    — the walk's Dec-31 cost is the engine's ACB. Each sale is sized on
    its own with min(S, P, B) (A2-0167)."""

    @rule("CA-RPT-12")
    def test_pre_loss_replacement_then_same_second_fill(self):
        rows = [_t("b1", "2025-01-02", "AAA.US", 300, 100.0),
                _t("b2", "2025-01-20", "AAA.US", 100, 90.0),
                _t("s1", "2025-02-03", "AAA.US", -100, 80.0),
                _t("s2", "2025-02-03", "AAA.US", -100, 80.0)]
        _rep, props, inv = _engine_report(rows)
        # One sale of 200: min(200, 100, 200) = 100 units, all carried
        # by the 200 still held (19500 + 1750).
        self.assertEqual(props["AAA.US"]["year_end_cost"], 21250.0)
        self.assertEqual(props["AAA.US"]["year_end_cost"],
                         round(inv["AAA.US"]["total_cost"], 2))

    @rule("CA-RPT-12")
    def test_two_same_second_sells(self):
        rows = [_t("b1", "2024-03-01", "XYZ.US", 100, 10),
                _t("b2", "2024-11-01", "XYZ.US", 100, 5),
                _t("s1", "2024-11-08", "XYZ.US", -100, 5),
                _t("s2", "2024-11-08", "XYZ.US", -50, 5)]
        _rep, props, inv = _engine_report(rows)
        # One sale of 150: min(150, 100, 50) = 50 units (125 denied) on
        # the 50 held at 7.50.
        self.assertAlmostEqual(inv["XYZ.US"]["total_cost"], 500.0, 2)
        self.assertEqual(props["XYZ.US"]["year_end_cost"], 500.0)

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
        # s1+s2 are one sale: 362.50 x min(100, 60, 30)/100 = 108.75;
        # s3 is a sale of its own and the 11-01 rebuy, still held, backs
        # its whole loss too (CA-SL-08 per sale).
        self.assertEqual(props["XYZ.US"]["year_end_cost"], 416.25)

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
    """A2-0916, A2-0923, A2-1553 pinned the replay's wash landings (the
    ADJUST rows it rebuilt from gains records). The walk now reads the
    engine's pools (backlog TJ-0417): a loss's addition is where the
    engine applied it — on a replacement bought before the loss (AAA)
    or after it (BBB)."""
    BASE = [_r("BUYSELL", "2025-01-02", "AAA.US", 100, -10000.0, id="b1"),
            _r("BUYSELL", "2025-01-20", "AAA.US", 100, -9000.0, id="b2"),
            _r("BUYSELL", "2025-02-03", "AAA.US", -100, 8000.0, id="s1"),
            _r("BUYSELL", "2025-03-03", "BBB.US", 10, -1000.0, id="b3"),
            _r("BUYSELL", "2025-03-10", "BBB.US", -10, 800.0, id="s2"),
            _r("BUYSELL", "2025-03-20", "BBB.US", 10, -700.0, id="b4")]

    @rule("CA-RPT-12")
    def test_every_addition_is_where_the_engine_put_it(self):
        walk = _walk(list(self.BASE))
        # AAA: 200 shares for 19000, 100 sold for 8000 (a 1500 loss,
        # denied: the 100 left are its replacement) — 9500 + 1500.
        self.assertEqual(walk["per_symbol"]["AAA.US"]["year_end_cost"],
                         11000.0)
        # BBB: the 200 loss is in the 700 replacement.
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
    """A2-0916: --year-wash-only once left an earlier year's denial out
    of the cost and named it. The cost amounts are the engine's pools
    now (backlog TJ-0417), the gains files' records unread: a denial
    from the year before and one inside the year are both in it."""

    @rule("CA-RPT-12")
    def test_deferral_beyond_the_years_own(self):
        base = [_r("BUYSELL", "2024-01-10", "XYZ.US", 100, -10000.0,
                   id="b1"),
                _r("BUYSELL", "2024-12-02", "XYZ.US", -100, 8000.0,
                   id="s0"),
                _r("BUYSELL", "2024-12-09", "XYZ.US", 100, -8000.0,
                   id="b2"),
                _r("BUYSELL", "2025-02-03", "XYZ.US", -100, 7000.0,
                   id="s1"),
                _r("BUYSELL", "2025-02-10", "XYZ.US", 100, -7000.0,
                   id="b3")]
        gains = {"transactions": [], "wash_sales": [],
                 "inventory": [{"account": "margin", "symbol": "XYZ.US",
                                "deferred_wash": 3000.0}]}
        rep = _report(base, {"margin_gains_wash.json": gains},
                      full_history=False)
        self.assertEqual(rep["deferred_wash_not_in_cost"], {})
        (p,) = rep["properties"]
        # 7000 paid, the 2000 denied in 2024 and the 3000 denied in 2025.
        self.assertEqual(p["year_end_cost"], 10000.0)



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
        from taxjson.lib.missing_history import (assess_tax_year_relevance,
                                                  detect_missing_history)
        txs = [TaxTransaction(action="BUYSELL", date="2025-12-31",
                              date_settle="2026-01-02", time="10:00:00",
                              symbol="QZP.US", quantity=-10,
                              net_amount=2000.0, account="margin",
                              currency="USD")]
        cands = detect_missing_history(txs)
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
