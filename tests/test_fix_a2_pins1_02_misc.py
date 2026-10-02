"""Re-audit-2 test pins (tests-pins-02), outside the wash radar: each test
fails when the code it guards is reverted (a surviving mutant).

  A2-0537 / A2-0938  the tax_date variant in each consumer: the standalone
          audit's US default, check-filed's recorded date basis, the
          handoff snapshot, --suggest-phantoms, find-missing-history, the
          T1135 walk order, the checklist wash step, split-gains position
          starts, the trades view (window and .tt dates) and the web
          what-if grant-year basis
  A2-0892 / A2-1608  expiry cutoffs: expired_open's today cap and its
          last-data-date extension, the run warning's today clamp, the
          T1135 expired-option note's today clamp
  A2-0917 the CA-RPT-10 carryback reaches the third year back
  A2-1530 carryover sums several short- and long-term rows per year
  A2-1595 the US carryover's crypto no-wash pass; the 8949 CSV MANUAL rows
  A2-0920 the CA-CRYPTO-02 de-peg 2% and the October US-INC-DATE-RIC month
  A2-0925 taxjson-audit / -explain / -form-export refuse an implausible
          --year
  A2-1579 edge-cases names a permanent (registered) denial
  A2-0859 audit exits 1 when an account has no books at all

All data is synthetic (fake accounts only).
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import tx as dtx

REPO_ROOT = Path(__file__).resolve().parent.parent


def _call(main, argv, *, use_sys_argv=False):
    """(rc, stdout, stderr) of an in-process console-script main()."""
    out, err = io.StringIO(), io.StringIO()
    old = sys.argv
    rc = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if use_sys_argv:
                sys.argv = ["prog", *argv]
                r = main()
            else:
                r = main(argv)
            rc = r or 0
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (1 if e.code else 0)
    finally:
        sys.argv = old
    return rc, out.getvalue(), err.getvalue()


# ------------------------------------------- tax_date variant per consumer
def _trade_basis_project(tmp):
    """Canada, tax_date = "trade": 100 XEI.TO bought and sold Dec 31
    (settles Jan 2); a ZZZ.TO short sale Dec 31 with no buy in the books."""
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\ntax_date = "trade"\n\n'
        '[accounts.margin]\ntype = "taxable"\n')
    book = [dtx("BUYSELL", "2025-06-02", "XEI.TO", 100, -1000,
                currency="CAD", settle="2025-06-03", id="b1"),
            dtx("BUYSELL", "2025-12-31", "XEI.TO", -100, 1500,
                currency="CAD", settle="2026-01-02", id="s1"),
            dtx("BUYSELL", "2025-12-31", "ZZZ.TO", -50, 500,
                currency="CAD", settle="2026-01-02", id="z1")]
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": [t.to_dict() for t in book]}))
    return root


@rule("CA-DATE-02")
class TestCaTradeBasisConsumers(unittest.TestCase):
    """A2-0537 / A2-0938: on a trade-basis Canadian project a Dec-31
    trade that settles in January belongs to the year it was traded, in
    every consumer."""

    def test_handoff_snapshot(self):
        from taxjson.bin import taxjson_run as R
        from taxjson.lib import handoff
        with tempfile.TemporaryDirectory() as tmp:
            root = _trade_basis_project(tmp)
            cfg = R.load_config(root)
            self.assertEqual(handoff._basis(cfg["settings"]), "trade")
            with contextlib.redirect_stderr(io.StringIO()):
                snap = handoff.snapshot(
                    root / "work", cfg, "2025-12-31", R._filed_run_gains,
                    R._handoff_gains_flags(cfg["settings"]), None)
        # Sold on Dec 31 (trade date): not held at the year end.
        self.assertNotIn("XEI.TO", snap["equity"])
        self.assertEqual(snap["equity"]["ZZZ.TO"]["qty"], -50.0)

    def test_suggest_phantoms(self):
        from taxjson.bin import taxjson_gains as G
        with tempfile.TemporaryDirectory() as tmp:
            root = _trade_basis_project(tmp)
            out = root / "sugg.json"
            rc, _o, err = _call(G.main, [
                "--country", "canada", "--tax-date", "trade", "--year",
                "2025", "--suggest-phantoms", str(out),
                str(root / "work" / "margin_base.json")], use_sys_argv=True)
            self.assertEqual(rc, 0, err)
            sugg = json.loads(out.read_text())
        self.assertEqual([s["symbol"] for s in sugg], ["ZZZ.TO"])

    def test_find_missing_history(self):
        from taxjson.bin import taxjson_missing_history as M
        with tempfile.TemporaryDirectory() as tmp:
            root = _trade_basis_project(tmp)
            rc, out, err = _call(M.main, [
                "--year", "2025", str(root / "work" / "margin_base.json")])
        self.assertEqual(rc, 0, err)
        zzz = [ln.split() for ln in out.splitlines() if "ZZZ.TO" in ln]
        self.assertEqual(len(zzz), 1, out)
        # in-year dispositions 1, proceeds 500.00 (trade date 2025-12-31)
        self.assertEqual(zzz[0][-2:], ["1", "500.00"])

    def test_lock_keeps_its_recorded_basis(self):
        from taxjson.bin.taxjson_filed import lock_settings
        self.assertEqual(lock_settings({"date_basis": "trade"},
                                       {"tax_date": "settle"})["tax_date"],
                         "trade")
        self.assertEqual(lock_settings({"date_basis": "settle"},
                                       {"tax_date": "trade"})["tax_date"],
                         "settle")
        self.assertEqual(lock_settings({}, {"tax_date": "trade"}),
                         {"tax_date": "trade"})

    @rule("CA-RPT-01")
    def test_t1135_walk_order(self):
        # 60k of BBB held; 50k of AAA bought Dec 30 (settles Jan 2); BBB
        # sold Dec 31: on trade dates both are held on Dec 30.
        from taxjson.bin import taxjson_t1135 as T1

        def t(d, s, sym, q, net):
            return {"action": "BUYSELL", "date": d, "date_settle": s,
                    "time": "09:30:00", "symbol": sym, "quantity": q,
                    "net_amount": net, "symbol_new": "", "currency": "CAD"}
        rows = [t("2025-03-03", "2025-03-04", "BBB.US", 100, -60000.0),
                t("2025-12-30", "2026-01-02", "AAA.US", 100, -50000.0),
                t("2025-12-31", "2025-12-31", "BBB.US", -100, 61000.0)]
        self.assertEqual(T1.walk_costs(rows, 2025, {}, "trade")
                         ["max_total_cost"], 110000.0)
        self.assertEqual(T1.walk_costs(rows, 2025, {}, "settle")
                         ["max_total_cost"], 60000.0)

    @rule("CA-RPT-01")
    def test_t1135_trade_order_needs_no_split_re_denomination(self):
        # A split (dated Jun 2, settling Jun 3) and a buy of 10 made later
        # that day (settling Jun 4): on trade dates the buy is after the
        # split, already in post-split units — never scaled again.
        from taxjson.bin import taxjson_t1135 as T1

        def t(a, d, s, q, net, time="10:00:00"):
            return {"action": a, "date": d, "date_settle": s, "time": time,
                    "symbol": "ZZ.US", "quantity": q, "net_amount": net,
                    "symbol_new": "", "currency": "CAD"}
        rows = [t("BUYSELL", "2025-03-03", "2025-03-04", 100, -10000.0),
                t("SPLIT", "2025-06-02", "2025-06-03", 2, 0.0,
                  time="00:00:00"),
                t("BUYSELL", "2025-06-02", "2025-06-04", 10, -1000.0),
                t("BUYSELL", "2025-08-01", "2025-08-04", -100, 6000.0)]
        w = T1.walk_costs(rows, 2025, {}, "trade")
        # 210 units at 11,000; 100 sold -> 110/210 of the cost remains.
        self.assertAlmostEqual(w["per_symbol"]["ZZ.US"]["year_end_cost"],
                               5761.90, places=2)

    def test_checklist_wash_step(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "m_gains_wash.json").write_text(json.dumps({
                "summary": {"year": 2025},
                "transactions": [{"date": "2025-12-31",
                                  "date_settle": "2026-01-02",
                                  "disallowed_amount": 200.0,
                                  "permanently_disallowed": 0.0}]}))
            cfg = {"settings": {"year": 2025, "country": "canada",
                                "tax_date": "trade"},
                   "accounts": {"m": {"type": "taxable"}}}
            ctx = cl.Ctx(root=root, cfg=cfg, year=2025,
                         today=date(2026, 3, 1), run_sub=None)
            r = cl.d_wash_reviewed(ctx)
        self.assertIn("200.00 denied", r.detail)

    @rule("CA-OPT-01")
    def test_web_what_if_grant_year_on_trade_dates(self):
        # A call written 2024-12-31 (settles 2025-01-02), grant timing
        # from 2025: on trade dates it is a 2024 write (close timing), so
        # buying it back books the premium: 50 - 20 = 30.
        from test_fix_a2_planning_web import _project, _row, _whatif
        opt = "ABC250321C00050000.TO"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [_row(
                opt, -1, 50.0, "2024-12-31", settle="2025-01-02",
                price=0.5)]}, year=2025)
            p = root / "taxjson.toml"
            p.write_text(p.read_text().replace(
                'base_currency = "CAD"\n',
                'base_currency = "CAD"\ntax_date = "trade"\n'
                'option_grant_timing_since = 2025\n'))
            r = _whatif(root, "margin", opt, -1, 0.2, on="2025-03-03")
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["allowed_gain"], 30.0)
        self.assertAlmostEqual(r["cost_basis"], -50.0)


@rule("CA-DATE-01", "CA-DATE-02")
class TestSplitGainsPositionStarts(unittest.TestCase):
    """A2-0938: split-gains orders an account's rows on the tax_date
    basis. A sale traded Jun 2 that settles Jun 5 after a buy traded
    Jun 3 (settles Jun 4): on settle dates the position never closed."""

    def test_both_bases(self):
        from taxjson.bin.taxjson_split_gains import _position_starts

        def r(d, s, q):
            return {"action": "BUYSELL", "date": d, "date_settle": s,
                    "symbol": "XYZ.TO", "quantity": q, "time": "10:00:00",
                    "account": "margin"}
        rows = [r("2025-01-02", "2025-01-03", 100),
                r("2025-06-02", "2025-06-05", -100),
                r("2025-06-03", "2025-06-04", 100)]
        self.assertEqual(_position_starts(rows, "settle"),
                         {"XYZ.TO": "2025-01-02"})
        self.assertEqual(_position_starts(rows, "trade"),
                         {"XYZ.TO": "2025-06-03"})


def _trades_project(tmp, country, tax_date, sym, cur):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        f'[settings]\ncountry = "{country}"\nyear = 2025\n'
        + (f'tax_date = "{tax_date}"\n' if tax_date else ""))

    def t(d, s, q, net):
        return dict(action="BUYSELL", date=d, date_settle=s,
                    time="10:00:00", symbol=sym, quantity=q,
                    price=abs(net / q), net_amount=net, currency=cur)
    (root / "work" / "margin_raw.json").write_text(json.dumps(
        {"transactions": [t("2025-06-02", "2025-06-03", 10, -100.0),
                          t("2025-12-31", "2026-01-02", -10, 120.0)]}))
    return root


def _trade_lines(root, period):
    r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                        "-C", str(root), "trades", period, "margin"],
                       cwd=REPO_ROOT, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return [ln.split()[1] for ln in r.stdout.splitlines()
            if ln.startswith("BUYSELL")]


class TestTradesViewDateBasis(unittest.TestCase):
    """A2-0938 (run trades view): the tax-year window and the dates the
    single-account .tt view writes follow the project's tax_date."""

    @rule("CA-DATE-01", "CA-DATE-02")
    def test_canada(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _trades_project(tmp, "canada", None, "XYZ.TO", "CAD")
            self.assertEqual(_trade_lines(root, "ty"), ["2025-06-03"])
            self.assertEqual(_trade_lines(root, "2026"), ["2026-01-02"])
        with tempfile.TemporaryDirectory() as tmp:
            root = _trades_project(tmp, "canada", "trade", "XYZ.TO", "CAD")
            self.assertEqual(_trade_lines(root, "ty"),
                             ["2025-06-02", "2025-12-31"])

    @rule("US-DATE-01", "US-DATE-02")
    def test_usa(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _trades_project(tmp, "usa", None, "XYZ.US", "USD")
            self.assertEqual(_trade_lines(root, "ty"),
                             ["2025-06-02", "2025-12-31"])
        with tempfile.TemporaryDirectory() as tmp:
            root = _trades_project(tmp, "usa", "settle", "XYZ.US", "USD")
            self.assertEqual(_trade_lines(root, "ty"), ["2025-06-03"])
            self.assertEqual(_trade_lines(root, "2026"), ["2026-01-02"])


@rule("US-DATE-01")
class TestStandaloneAuditUsBasis(unittest.TestCase):
    """A2-0537: taxjson-audit defaults a US book to trade dates: a Dec-31
    sale that settles Jan 2 is a 2025 event."""

    def test_dec31_sale_is_audited_in_its_trade_year(self):
        from taxjson.bin import taxjson_audit as A

        def r(d, s, q, net, rid):
            return dict(id=rid, action="BUYSELL", date=d, date_settle=s,
                        time="10:00:00", symbol="XYZ.US", quantity=q,
                        net_amount=net, price=abs(net / q), currency="USD",
                        account="margin", commission=0.0)
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp) / "margin_base.json"
            b.write_text(json.dumps({"transactions": [
                r("2025-06-02", "2025-06-03", 10, -1000.0, "b1"),
                r("2025-12-31", "2026-01-02", -10, 1200.0, "s1")]}))
            rc, out, err = _call(A.main, [
                "--country", "usa", "--base", str(b), "--year", "2025",
                "--json", "--no-trace"])
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual(len(doc["events"]), 1)
        self.assertAlmostEqual(doc["total_gain"], 200.0)


# ------------------------------------------------------- expiry cutoffs
class TestExpiryCutoffs(unittest.TestCase):
    """A2-0892 / A2-1608 (R1-174 twins)."""

    @staticmethod
    def _t(d, sym, q, net=100.0):
        from taxjson.lib.core import TaxTransaction
        return TaxTransaction(action="BUYSELL", date=d, date_settle=d,
                              time="10:00:00", symbol=sym, quantity=q,
                              net_amount=net, price=1.0, currency="CAD",
                              account="margin")

    def test_expired_open_today_cap(self):
        from taxjson.lib.option_boundary import expired_open
        opt = "Q261120C00050000.TO"            # expires 2026-11-20
        book = [self._t("2026-08-10", opt, -1)]
        self.assertEqual(expired_open(book, 2026, today=date(2026, 9, 30)),
                         [])
        self.assertEqual([o["symbol"] for o in expired_open(
            book, 2026, today=date(2026, 11, 21))], [opt])

    def test_expired_open_reaches_the_last_data_date(self):
        # Books of 2025 that run into February 2026: a January 2026
        # expiry inside them is past.
        from taxjson.lib.option_boundary import expired_open
        opt = "Q260116C00050000.TO"            # expires 2026-01-16
        book = [self._t("2025-10-10", opt, -1),
                self._t("2026-02-02", "Q.TO", 10, -100.0)]
        self.assertEqual([o["symbol"] for o in expired_open(
            book, 2025, today=date(2026, 9, 30))], [opt])

    def test_run_warning_skips_a_future_expiry_of_this_year(self):
        from taxjson.bin import taxjson_run as R
        today = date.today()
        future = today + timedelta(days=3)
        if future.year != today.year:
            self.skipTest("too close to the year end")
        past = today - timedelta(days=3)
        fut_opt = f"ABC{future:%y%m%d}C00010000.US"
        past_opt = f"ABC{past:%y%m%d}P00010000.US"
        inv = [{"symbol": fut_opt, "qty": 1, "total_cost": 100.0},
               {"symbol": past_opt, "qty": 1, "total_cost": 100.0}]
        for with_base in (True, False):
            with self.subTest(with_base=with_base), \
                    tempfile.TemporaryDirectory() as td:
                cache = Path(td)
                if with_base:
                    rows = [dict(action="BUYSELL", date=f"{today.year}-01-05",
                                 date_settle=f"{today.year}-01-05",
                                 time="10:00:00", symbol=s, quantity=1,
                                 net_amount=-100.0, price=1.0,
                                 currency="USD", account="m")
                            for s in (fut_opt, past_opt)]
                    (cache / "m_base.json").write_text(
                        json.dumps({"transactions": rows}))
                g = cache / "m_gains.json"
                g.write_text(json.dumps({"inventory": inv}))
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    R._warn_expired_open_options("m", g, cache, today.year)
                self.assertIn(past_opt, err.getvalue())
                self.assertNotIn(fut_opt, err.getvalue())

    @rule("CA-RPT-01")
    def test_t1135_expired_option_note_waits_for_the_expiry(self):
        from taxjson.bin import taxjson_t1135 as T1
        rows = [{"action": "BUYSELL", "date": "2026-03-02",
                 "date_settle": "2026-03-03", "time": "09:30:00",
                 "symbol": "ZZQ260918C00015000.US", "quantity": 10,
                 "net_amount": -5000.0, "symbol_new": "", "currency": "CAD"}]
        self.assertEqual(T1.walk_costs(rows, 2026, {}, "settle",
                                       today="2026-06-01")
                         ["expired_options_held"], [])
        self.assertEqual(T1.walk_costs(rows, 2026, {}, "settle",
                                       today="2026-09-30")
                         ["expired_options_held"],
                         ["ZZQ260918C00015000.US"])


# -------------------------------------------------------------- carryover
class TestCarryover(unittest.TestCase):

    @rule("CA-RPT-10")
    def test_carryback_reaches_the_third_year_back(self):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        nets = {2022: {"net": 1000.0, "st": 0, "lt": 0, "dispositions": 1},
                2025: {"net": -1000.0, "st": 0, "lt": 0, "dispositions": 1}}
        rows = {r["year"]: r for r in build_canada_ledger(nets, {})["rows"]}
        self.assertEqual(rows[2025]["carryback_candidates"],
                         [{"year": 2022, "amount": 1000.0}])

    @rule("US-RPT-08")
    def test_yearly_nets_sum_every_row_of_a_term(self):
        from taxjson.bin.taxjson_carryover import yearly_nets
        rows = [{"date": "2025-03-03", "gain": g, "qty": 1, "term": term}
                for g, term in ((100.0, "SHORT_TERM"), (200.0, "SHORT_TERM"),
                                (50.0, "LONG_TERM"), (-20.0, "LONG_TERM"))]
        n = yearly_nets({"transactions": rows}, "trade")[2025]
        self.assertEqual((n["st"], n["lt"], n["net"], n["dispositions"]),
                         (300.0, 30.0, 330.0, 4))

    @rule("US-RPT-08", "US-WASH-13")
    def test_us_crypto_books_run_without_wash_sales(self):
        # A2-1595: a taxable gain of 1000, and a crypto loss of 3000 with
        # a rebuy 4 days later — no §1091 on crypto, so 2025 nets -2000.
        from taxjson.bin import taxjson_carryover as C

        def r(d, sym, q, net, acct="margin"):
            return dict(action="BUYSELL", date=d, date_settle=d,
                        time="10:00:00", symbol=sym, quantity=q,
                        net_amount=net, price=abs(net / q), currency="USD",
                        account=acct, commission=0.0)
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "margin_base.json"
            t.write_text(json.dumps({"transactions": [
                r("2025-02-03", "XYZ.US", 10, -1000.0),
                r("2025-03-03", "XYZ.US", -10, 2000.0)]}))
            c = Path(tmp) / "cb_base.json"
            c.write_text(json.dumps({"transactions": [
                r("2025-04-01", "BTC", 1, -50000.0, "cb"),
                r("2025-05-01", "BTC", -1, 47000.0, "cb"),
                r("2025-05-05", "BTC", 1, -47500.0, "cb")]}))
            rc, out, err = _call(C.main, [str(t), "--crypto", str(c),
                                          "--country", "usa", "--json"])
        self.assertEqual(rc, 0, err)
        (row,) = json.loads(out)["rows"]
        self.assertEqual((row["year"], row["net_gain"], row["net_st"],
                          row["dispositions"]), (2025, -2000.0, -2000.0, 2))


@rule("US-BASIS-04", "US-RPT-01")
class TestForm8949CsvManualRows(unittest.TestCase):
    """A2-1595: a phantom-basis disposition is a MANUAL row of the 8949
    CSV (cost unknown), never dropped."""

    def test_manual_row(self):
        from taxjson.bin.taxjson_form_export import _write_csv
        rep = {"form": "8949", "part_I": [], "part_II": [],
               "manual_reporting_required": [{
                   "qty": -100, "symbol": "XYZ.US", "date": "2025-03-03",
                   "proceeds": 1195.0, "account": "margin"}]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "f.csv"
            _write_csv(rep, p)
            lines = p.read_text().splitlines()
        self.assertEqual(lines[-1],
                         "MANUAL,100 XYZ.US,,2025-03-03,1195.0,,,,,margin")


# -------------------------------------------------- stated thresholds
class TestStatedThresholds(unittest.TestCase):
    """A2-0920: the numbers tax-logic states, tested at their edges."""

    @rule("CA-CRYPTO-02")
    def test_depeg_warning_starts_past_two_percent(self):
        from taxjson.lib.brokerages._crypto_common import warn_depeg
        for price, warns in ((0.97, True), (0.975, True), (1.025, True),
                             (0.985, False), (1.015, False)):
            with self.subTest(price=price), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(warn_depeg("USDC", price, 1000,
                                            "2025-03-01", "cb.csv"), warns)

    @rule("US-INC-DATE-RIC")
    def test_ric_warning_covers_an_october_record_date(self):
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules(country="usa")
        for ex, rec, warns in (("2025-10-30", "2025-10-31", True),
                               ("2025-09-29", "2025-09-30", False)):
            row = dtx("DIVIDEND", "2026-01-30", "VTI.US", 0, 50.0,
                      gross_amount=50.0, type="dividend", ex_date=ex,
                      record_date=rec)
            with self.subTest(ex=ex):
                w = rules.warnings([row], 2026)
                self.assertEqual(any("§852(b)(7)" in x for x in w), warns, w)


class TestStandaloneYearFlags(unittest.TestCase):
    """A2-0925 (S047-14): the standalone console scripts refuse a year
    that is not a plausible tax year, like the `taxjson` subcommands."""

    def test_audit_explain_form_export(self):
        from taxjson.bin import taxjson_audit, taxjson_explain
        from taxjson.bin import taxjson_form_export
        for main, sys_argv in ((taxjson_audit.main, False),
                               (taxjson_explain.main, True),
                               (taxjson_form_export.main, False)):
            for year in ("2204", "0"):
                with self.subTest(tool=main.__module__, year=year):
                    rc, _o, err = _call(main, ["--year", year],
                                        use_sys_argv=sys_argv)
                    self.assertEqual(rc, 2)
                    self.assertIn("not a plausible tax year", err)


# --------------------------------------------------------- edge-cases
@rule("CA-RPT-07")
class TestEdgeCasesPermanentDenial(unittest.TestCase):
    """A2-1579: a loss whose window crosses Dec 31 states whether the
    denial is permanent (a registered replacement) or deferred."""

    def _why(self, perm):
        from test_fix_a2_dates_views import _Proj, _gain, _tx
        p = _Proj("canada", 2025, accounts={"margin": {"type": "taxable"},
                                            "rrsp": {"type": "sheltered"}})
        try:
            p.base("margin", [
                _tx("margin", "BUYSELL", "2025-06-02", "2025-06-03",
                    "XYZ.TO", 100, -2000.0),
                _tx("margin", "BUYSELL", "2025-12-10", "2025-12-11",
                    "XYZ.TO", -100, 1000.0)])
            p.base("rrsp", [_tx("rrsp", "BUYSELL", "2026-01-05",
                                "2026-01-06", "XYZ.TO", 30, -300.0)])
            g = _gain("margin", "2025-12-10", "2025-12-11", "XYZ.TO", 100,
                      -1000.0, denied=300.0)
            g["permanently_disallowed"] = perm
            p.gains("margin", [g])
            (w,) = p.analyze()["year_boundary"]["loss_windows"]
            return w["why"]
        finally:
            p.close()

    def test_permanent_and_deferred_wording(self):
        why = self._why(300.0)
        self.assertIn("denied 300.00 (300.00 permanently, registered "
                      "replacement)", why)
        self.assertNotIn("added to the replacement's cost", why)
        self.assertIn("denied 300.00; added to the replacement's cost",
                      self._why(0.0))


# --------------------------------------------------------------- audit
class TestAuditAccountWithoutBooks(unittest.TestCase):
    """A2-0859 (S047-16): an account whose books are missing entirely (a
    failed stage) fails the audit by itself — not through a stale-
    artifact check — while the other account still ties out."""

    def test_no_books_at_all_exits_1(self):
        from test_fix_runcore import _CONFIG, _MARGIN_CSV, _project, _run_cli
        cfg = _CONFIG + "\n[accounts.cash]\ntype = \"taxable\"\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg)
            (root / "inputs" / "cash").mkdir()
            (root / "inputs" / "cash" / "q.csv").write_text(
                _MARGIN_CSV.replace("XEI.TO", "XIU.TO"))
            self.assertEqual(_run_cli(root, "run", "--no-input")
                             .returncode, 0)
            for p in (root / "work").glob("cash*"):
                p.unlink()
            r = _run_cli(root, "audit")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not audited — no books for cash", r.stderr)
        self.assertNotIn("CHECKS FAILED", r.stdout + r.stderr)
        self.assertIn("1 tied, 0 MISMATCHED", r.stdout)


if __name__ == "__main__":
    unittest.main()
