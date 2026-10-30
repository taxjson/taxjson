"""Fix round 7 (wave D), 2026-10-29: harvest, watch's harvest baseline,
the wash radar's calendars, div-estimate and fees (backlog TJ-1038,
TJ-1039, TJ-1040, TJ-1047, TJ-1088, TJ-1078, TJ-1079).

Synthetic data only; offline throughout (injected fetchers or a cache).
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from test_div_estimate import _div, _est, _pos
from test_fix_backlog_radar import _radar, _row

from taxjson.bin.taxjson_harvest import main as harvest_main

# A dividend row as the base-currency books write it.
_DIV = {"action": "DIVIDEND", "date": "2026-04-15",
        "date_settle": "2026-04-15", "time": "00:00:00", "symbol": "QZD.TO",
        "quantity": 0, "price": 0, "net_amount": 14.0, "currency": "CAD",
        "account": "margin", "commission": 0.0, "id": "div1"}


def _gains(root: Path, inventory, name="margin_gains_wash.json"):
    p = root / name
    p.write_text(json.dumps({"summary": {"year": 2026}, "transactions": [],
                             "inventory": inventory}))
    return p


def _harvest(argv, fetcher):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = harvest_main(argv, fetchers=[fetcher])
    return rc, out.getvalue(), err.getvalue()


class TestQuoteRatioOwnListing(unittest.TestCase):
    """TJ-1038: an IBKR hit prices the book symbol's own listing (that
    tier never sees a QUOTE remap): it is already per book share, so a
    QUOTE line's ratio is not applied to it. TJ-0709's case (a quote of
    the QUOTE target) keeps the ratio (test_fix_1029_r7b_harvest)."""

    def _row(self, fetcher):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZOLD.TO", "qty": 100,
                               "total_cost": 500.0,
                               "last_acq_date": "2026-01-02"}])
            (root / "ticker.map").write_text(
                "QUOTE ZZOLD.TO ZZNEW.TO 0.25\n")
            rc, out, err = _harvest(
                [str(g), "--country", "canada", "--json"], fetcher)
        self.assertEqual(rc, 0, err)
        return json.loads(out)["rows"][0]

    def test_ibkr_own_listing_quote_not_scaled(self):
        row = self._row(lambda rem: {s: (5.0, "ibkr", "CAD", s)
                                     for s in rem if s == "ZZOLD.TO"})
        self.assertAlmostEqual(row["value"], 500.0)
        self.assertAlmostEqual(row["unrealized"], 0.0)
        self.assertEqual(row["verdict"], "FLAT")
        self.assertAlmostEqual(row["price_base"], 5.0)
        self.assertAlmostEqual(row["quote_ratio"], 1.0)

    def test_target_quote_still_scaled(self):
        row = self._row(lambda rem: {s: (20.0, "yfinance", "CAD")
                                     for s, y in rem.items()
                                     if y == "ZZNEW.TO"})
        self.assertAlmostEqual(row["value"], 500.0)
        self.assertAlmostEqual(row["price_base"], 5.0)
        self.assertAlmostEqual(row["quote_ratio"], 0.25)


def _project(tmp, accounts, country="canada", ticker_map=""):
    """A project of `accounts` ({name: type}) dated this year."""
    root = Path(tmp) / "p"
    base = "CAD" if country == "canada" else "USD"
    lines = [f'[settings]\nyear = {date.today().year}\n'
             f'country = "{country}"\nbase_currency = "{base}"\n'
             f'source_currencies = []\n']
    for name, kind in accounts.items():
        lines.append(f'[accounts.{name}]\ntype = "{kind}"\n')
        (root / "inputs" / name).mkdir(parents=True)
    (root / "taxjson.toml").write_text("".join(lines))
    (root / "ticker.map").write_text(ticker_map)
    return root


def _quotes(root, prices):
    today = date.today().isoformat()
    (root / "work" / ".price_cache.json").write_text(json.dumps(
        {s: {"price": p, "currency": "CAD", "quote_symbol": s, "asof": today}
         for s, p in prices.items()}))


class TestViolationRegisteredWindowOnly(unittest.TestCase):
    """TJ-1039: a sale today of an open VIOLATION loses only the units a
    registered account bought in THAT sale's window and still holds, not
    its whole holding (the rescue's quantity). TJ-0908's case, every
    registered unit bought in the window, still waits in full (below,
    and test_fix_1029_r7b_harvest, which reads a sidecar without the
    field)."""

    def _books(self, tmp, reg_old, reg_new, sold_today=False):
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        root = _project(tmp, {"margin": "taxable", "registered": "sheltered"})
        margin = (f"BUYSELL {d(150)} 10:00:00 ZZSYN.TO 20 CAD 20 400 0\n"
                  f"BUYSELL {d(10)} 10:00:00 ZZSYN.TO -10 CAD 10 100 0\n")
        if sold_today:
            margin += f"BUYSELL {d(0)} 10:00:00 ZZSYN.TO -10 CAD 10 100 0\n"
        reg = (f"BUYSELL {d(5)} 10:00:00 ZZSYN.TO {reg_new} CAD 10 "
               f"{10 * reg_new} 0\n")
        if reg_old:
            reg = (f"BUYSELL {d(150)} 11:00:00 ZZSYN.TO {reg_old} CAD 20 "
                   f"{20 * reg_old} 0\n") + reg
        (root / "inputs" / "margin" / "trades.tt").write_text(margin)
        (root / "inputs" / "registered" / "trades.tt").write_text(reg)
        tj(root, "run", "--no-input")
        return root

    def _harvest(self, tmp, reg_old, reg_new):
        root = self._books(tmp, reg_old, reg_new)
        rad = json.loads(tj(root, "wash-radar", "--json").stdout)
        row = next(r for sec in rad["sections"] for r in sec["rows"]
                   if r["ticker"] == "ZZSYN.TO")
        self.assertEqual(row["category"], "VIOLATION", row["advisory"])
        self.assertAlmostEqual(row["rescue"][0]["qty"], reg_old + reg_new)
        _quotes(root, {"ZZSYN.TO": 10.0})
        doc = json.loads(tj(root, "harvest", "--no-ibkr", "--json").stdout)
        return row, doc["totals"]["harvestable"]

    def test_only_the_in_window_units_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            row, h = self._harvest(tmp, 18, 2)
        self.assertAlmostEqual(row["registered_window_qty"], 2.0)
        self.assertAlmostEqual(h["now"], 80.0)
        self.assertAlmostEqual(h["14d"], 80.0)
        self.assertAlmostEqual(h["30d"], 100.0)

    def test_the_engine_agrees_on_the_actual_sale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._books(tmp, 18, 2, sold_today=True)
            doc = json.loads((root / "work" / "margin_gains_wash.json")
                             .read_text())
        sale = [t for t in doc["transactions"]
                if t.get("date") == date.today().isoformat()]
        self.assertEqual(len(sale), 1, sale)
        self.assertAlmostEqual(sale[0]["permanently_disallowed"], 20.0)
        self.assertAlmostEqual(sale[0]["gain"], -80.0)

    def test_all_in_window_still_waits_in_full(self):
        # TJ-0908: nothing of the loss is claimable today.
        with tempfile.TemporaryDirectory() as tmp:
            row, h = self._harvest(tmp, 0, 20)
        self.assertAlmostEqual(row["registered_window_qty"], 10.0)
        self.assertAlmostEqual(h["now"], 0.0)
        self.assertAlmostEqual(h["30d"], 100.0)

    def test_sidecar_field_beats_the_rescue_quantity(self):
        from taxjson.bin.taxjson_harvest import _registered_backed_fraction
        rec = {"taxable_qty": 10.0, "registered_window_qty": 0.0,
               "rescue": [{"holder": "sheltered", "qty": 20.0}]}
        self.assertEqual(_registered_backed_fraction(rec), 0.0)
        del rec["registered_window_qty"]
        self.assertEqual(_registered_backed_fraction(rec), 1.0)


class TestWatchNewPositionLeftOut(unittest.TestCase):
    """TJ-1040: a position left out that the baseline never valued is in
    neither total: watch still compares and re-baselines. TJ-0912's case
    (one the baseline valued) keeps the baseline (below, and
    test_fix_1029_r7b_harvest)."""

    def test_coverage_rule(self):
        from taxjson.bin.taxjson_watch import harvest_coverage as c
        self.assertEqual(c([], ["m|NEW"], ["m|OLD"]), "same")
        self.assertEqual(c([], ["m|OLD"], ["m|OLD"]), "worse")
        self.assertEqual(c(["m|NEW"], ["m|NEW"], ["m|OLD"]), "same")
        self.assertEqual(c(["m|X"], ["m|NEW"], ["m|OLD"]), "better")
        self.assertEqual(c(["m|X"], ["m|OLD"], ["m|OLD"]), "worse")
        # A baseline without the field: as TJ-0912 had it.
        self.assertEqual(c([], ["m|NEW"]), "worse")

    def _watch(self, root, state):
        return tj(root, "watch", "--harvest", "--threshold", "0",
                  "--no-ibkr", "--state", str(state), "--json")

    def test_new_unknown_cost_position_does_not_stop_the_comparison(self):
        y = date.today().year
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": "taxable"})
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL {y}-01-03 10:00:00 ZZALP.TO 10 CAD 20 200 0\n"
                f"BUYSELL {y}-01-03 11:00:00 ZZBET.TO 10 CAD 20 200 0\n")
            tj(root, "run", "--no-input")
            _quotes(root, {"ZZALP.TO": 10.0, "ZZBET.TO": 10.0})
            state = root / "work" / "w_state.json"
            self._watch(root, state)
            saved = json.loads(state.read_text())
            self.assertEqual(saved["harvest_now"], 200.0)
            self.assertEqual(saved["harvest_valued"],
                             ["margin|ZZALP.TO", "margin|ZZBET.TO"])
            (root / "inputs" / "margin" / "missing_history.tt").write_text(
                f"OPENING {y - 1}-12-31 ZZGAM.TO 10 cost=unknown\n")
            tj(root, "run", "--no-input")
            _quotes(root, {"ZZALP.TO": 5.0, "ZZBET.TO": 10.0,
                           "ZZGAM.TO": 10.0})
            r = self._watch(root, state)
            doc = json.loads(r.stdout)
            self.assertEqual([c["kind"] for c in doc["changes"]],
                             ["harvest_now"], r.stderr)
            self.assertEqual(doc["incomplete"], [])
            self.assertIn("not in the harvestable total: ZZGAM.TO", r.stderr)
            self.assertNotIn("baseline valued", r.stderr)
            saved = json.loads(state.read_text())
            self.assertEqual(saved["harvest_now"], 250.0)
            self.assertEqual(saved["harvest_omitted"], ["margin|ZZGAM.TO"])
            # It stays left out: later runs compare, without the note.
            _quotes(root, {"ZZALP.TO": 4.0, "ZZBET.TO": 10.0,
                           "ZZGAM.TO": 10.0})
            r = self._watch(root, state)
            doc = json.loads(r.stdout)
            self.assertEqual([c["kind"] for c in doc["changes"]],
                             ["harvest_now"])
            self.assertNotIn("not in the harvestable total", r.stderr)
            # A position the baseline valued, left out: kept, as TJ-0912.
            (root / "inputs" / "margin" / "missing_history.tt").write_text(
                f"OPENING {y - 1}-12-31 ZZGAM.TO 10 cost=unknown\n"
                f"OPENING {y - 1}-12-31 ZZBET.TO 10 cost=unknown\n")
            tj(root, "run", "--no-input")
            _quotes(root, {"ZZALP.TO": 4.0, "ZZBET.TO": 10.0,
                           "ZZGAM.TO": 10.0})
            r = self._watch(root, state)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["changes"], [])
            self.assertTrue(any("ZZBET.TO" in x for x in doc["incomplete"]),
                            doc)
            self.assertIn("baseline valued", r.stderr)
            self.assertEqual(json.loads(state.read_text())["harvest_now"],
                             260.0)


class TestRadarCalendarFromTheSharesTrades(unittest.TestCase):
    """TJ-1047, TJ-1088: the radar's calendar comes from the rows that move
    the shares. A rename target with no trade of its own has the old
    listing's; a dividend's currency says nothing. TJ-0713's US-dollar
    unit stays on 'CAD+USD' (test_fix_1029_r7b_planning)."""

    def _rename(self, **kw):
        rows = [_row("2026-01-05", "OLD.TO", 100, 10.0, settle="2026-01-06"),
                _row("2026-05-19", "OLD.TO", 100, 9.0, settle="2026-05-20"),
                {"action": "SPLIT", "date": "2026-05-25", "time": "00:00:00",
                 "date_settle": "2026-05-25", "symbol": "OLD.TO",
                 "symbol_new": "NEW.TO", "quantity": 1.0, "currency": "CAD",
                 "account": "margin", "description": "rename", "id": "rn1"}]
        for r in rows:
            r.update(kw)
        return rows

    @rule("CA-PLAN-01")
    def test_tj1047_rename_target_on_the_canadian_calendar(self):
        r = _radar("canada", self._rename(), as_of="2026-06-01")["NEW.TO"]
        self.assertEqual(r["category"], "EXITABLE", r["advisory"])
        self.assertEqual(r["clears_at"], "2026-06-19")
        self.assertIn("before 2026-06-19", r["advisory"])

    def test_tj1047_registered_buy_variant(self):
        rows = [self._rename()[0], self._rename()[2]]
        tfsa = [_row("2026-05-19", "OLD.TO", 100, 9.0, acct="tfsa",
                     settle="2026-05-20")]
        r = _radar("canada", rows, tfsa, as_of="2026-06-01")["NEW.TO"]
        self.assertEqual(r["category"], "LOCKED", r["advisory"])
        self.assertEqual(r["clears_at"], "2026-06-19")

    def test_tj1047_us_dollar_unit_renamed_keeps_the_joint_calendar(self):
        r = _radar("canada", self._rename(trade_currency="USD"),
                   as_of="2026-06-01")["NEW.TO"]
        self.assertEqual(r["clears_at"], "2026-06-18")

    def test_dividend_alone_does_not_name_the_new_listing_currency(self):
        rows = self._rename()
        rows.insert(0, dict(_DIV, symbol="NEW.TO", trade_currency="USD"))
        r = _radar("canada", rows, as_of="2026-06-01")["NEW.TO"]
        self.assertEqual(r["clears_at"], "2026-06-19")

    def _dividend(self, **kw):
        return [_row("2026-04-01", "QZD.TO", 100, 10.0, settle="2026-04-02"),
                _row("2026-05-19", "QZD.TO", -100, 9.0, settle="2026-05-20"),
                _row("2026-05-26", "QZD.TO", 50, 9.0, settle="2026-05-27"),
                dict(_DIV, **kw)]

    @rule("CA-PLAN-01")
    def test_tj1088_usd_dividend_does_not_move_the_sell_by(self):
        r = _radar("canada", self._dividend(trade_currency="USD"),
                   as_of="2026-06-02")["QZD.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["clears_at"], "2026-06-18")
        self.assertNotIn("listing", r["advisory"])
        plain = _radar("canada", self._dividend(),
                       as_of="2026-06-02")["QZD.TO"]
        self.assertEqual(r["advisory"], plain["advisory"])

    def test_tj1088_traded_in_both_currencies_names_no_second_listing(self):
        rows = self._dividend()[:3]
        rows[2]["trade_currency"] = "USD"
        r = _radar("canada", rows, as_of="2026-06-02")["QZD.TO"]
        self.assertEqual(r["clears_at"], "2026-06-17")
        self.assertNotIn("listing", r["advisory"])


class TestDivEstimateScheduleChange(unittest.TestCase):
    """TJ-1078: two payments after the winning grid's latest that fit a
    grid of their own are a change of schedule, not specials. TJ-0900's
    single special paid last stays a special (below, and
    test_fix_1029_r7b_harvest)."""

    @staticmethod
    def _est(pays, as_of, year=2025):
        rows = [("margin", _div(d, "ZZD.TO", 10, per)) for d, per in pays]
        return _est(rows, [_pos("ZZD.TO", 10)], as_of=as_of, months=6,
                    year=year)["rows"][0]

    @rule("CA-PLAN-06")
    def test_monthly_pay_day_moved_is_not_stopped(self):
        r = self._est([(d, 1.0) for d in (
            "2025-01-15", "2025-02-14", "2025-03-14", "2025-04-15",
            "2025-05-30", "2025-06-30")], "2025-07-02")
        self.assertEqual(r["frequency"], "monthly")
        self.assertEqual(r["status"], "ok", r["flags"])
        self.assertEqual(r["next_pay_date"], "2025-07-30")
        self.assertAlmostEqual(r["window_amount"], 60.0)
        self.assertFalse([f for f in r["flags"] if "special" in f])

    def test_quarterly_gone_monthly(self):
        r = self._est([("2025-01-15", 3.0), ("2025-04-15", 3.0),
                       ("2025-07-15", 3.0), ("2025-10-15", 3.0),
                       ("2025-11-14", 1.0), ("2025-12-15", 1.0)],
                      "2025-12-20")
        self.assertEqual(r["frequency"], "monthly")
        self.assertEqual(r["next_pay_date"], "2026-01-15")
        self.assertAlmostEqual(r["next_amount"], 10.0)
        self.assertAlmostEqual(r["window_amount"], 60.0)

    def test_specials_stay_specials(self):
        from taxjson.lib.div_estimate import detect_schedule
        monthly = ["2025-01-15", "2025-02-15", "2025-03-15", "2025-04-15"]
        # One paid last (TJ-0900), and two days apart: no grid of their own.
        self.assertEqual(detect_schedule(monthly + ["2025-05-01"])[0::3],
                         ("monthly", "2025-04-15"))
        self.assertEqual(
            detect_schedule(monthly + ["2025-05-01", "2025-05-03"])[0::3],
            ("monthly", "2025-04-15"))
        # Two a week apart are not a weekly payer.
        self.assertEqual(
            detect_schedule(monthly + ["2025-04-25", "2025-05-02"])[0::3],
            ("monthly", "2025-04-15"))


class TestFeesOptionContractSizeFromTheMap(unittest.TestCase):
    """TJ-1079: a .tt option's trade value uses the ticker.map MULT line, as
    the .tt total check and the engine do. TJ-0776's trade value without
    a line is unchanged (test_fix_1029_r7b_harvest)."""

    TX = {"symbol": "ZZQ261218C00005000.US", "price": 2.0, "quantity": -1,
          "gross_amount": 0.0, "net_amount": 19.0}

    def test_trade_value(self):
        from taxjson.bin.taxjson_fees import trade_value
        from taxjson.lib import markets
        self.assertAlmostEqual(trade_value(dict(self.TX), True, False), 200.0)
        with tempfile.TemporaryDirectory() as td:
            m = Path(td) / "ticker.map"
            m.write_text("MULT ZZQ 10\n")
            markets.use_ticker_map(m)
            try:
                self.assertAlmostEqual(
                    trade_value(dict(self.TX), True, False), 20.0)
                # The line wins over the row's, as in the engine.
                self.assertAlmostEqual(
                    trade_value(dict(self.TX, multiplier=100), True, False),
                    20.0)
                # A broker's gross is the broker's.
                self.assertAlmostEqual(
                    trade_value(dict(self.TX, gross_amount=50.0), True, False),
                    50.0)
                # Not an option: the line says nothing.
                self.assertAlmostEqual(trade_value(
                    dict(self.TX, symbol="ZZQ.US"), False, False), 2.0)
            finally:
                markets.use_ticker_map(None)

    def test_fees_sum_reads_the_project_map(self):
        y = date.today().year
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": "taxable"}, country="usa",
                            ticker_map="MULT ZZQ 10\n")
            (root / "inputs" / "margin" / "trades.tt").write_text(
                f"BUYSELL {y}-01-05 10:00:00 ZZQ.US 10 USD 50 500 0\n"
                f"BUYSELL {y}-01-06 10:00:00 ZZQ{y % 100}1218C00005000.US "
                f"-1 USD 2.00 19.00 1.00\n")
            tj(root, "run", "--no-input")
            doc = json.loads(tj(root, "fees-sum", "--json").stdout)
        self.assertAlmostEqual(doc["total"]["pct_notional"], 5.0, places=3)


if __name__ == "__main__":
    unittest.main()
