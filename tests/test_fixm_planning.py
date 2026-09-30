"""Planning tools, medium audit round (2026-09): the wash radar and the
checks built on it follow the engine's per-holder superficial-loss rule,
harvest and safe-to-sell agree with the radar and the books.

  R1-231  EXITABLE: shares a registered account held BEFORE the window
          never make a full taxable exit's loss superficial
  R1-232  LOCKED states the at-risk units; sell-check / harvest act on
          the fraction, not all-or-nothing
  S054-08 LOCKED needs the ACQUIRING registered account to still hold
  S055-01 LOCKED text: the sheltered buyer can still defeat the denial
  S054-03 VIOLATION rescue = the holders that back the denial
  S047-09 sell-check: a taxable-only VIOLATION is ACTION, not UNSAFE
  S054-00 a short opening is not an acquisition (Canada)
  S053-14 a long rebuy triggers a short-cover loss (not the loss row's
          own leg)
  S054-15 a dust replacement (< 0.01 units) still backs a denial
  S054-04 an exercised option is not a loss on the option
  R1-241  a pre-split sale settling after the split is re-denominated
  S053-20 own-account registered moves keep per-account balances
  S054-20 country = usa: an IRA buy in the window locks even after it
          sold
  R1-234  buy-back losses follow the project's option settings
  S048-13 an unreadable taxjson.toml stops the radar family
  S033-24 harvest: a VIOLATION's deadline day is still actionable
  R1-230  harvest: a grant-timed written option's buy-back books the
          whole buy-back cost as the loss
  S034-11 harvest prices a ticker.map-renamed option as the contract held
  S038-09 harvest never serves a radar older than the books
  R1-233 / S007-09 / S050-03  safe-to-sell reads the radar's walk

All data is synthetic.
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

from test_fix_planning import (REPO_ROOT, _QT_HEADER, _cli, _config, _disp,
                               _gains, _qt, _row)


def _radar(tmp, taxable, as_of, sheltered=None, gains=None, extra=()):
    tmp = Path(tmp)
    t = tmp / "margin_base.json"
    t.write_text(json.dumps({"transactions": taxable}))
    cmd = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
           "--taxable", str(t), "--date", as_of, "--all", "--json",
           *extra]
    if sheltered is not None:
        s = tmp / "sheltered_base.json"
        s.write_text(json.dumps({"transactions": sheltered}))
        cmd += ["--sheltered", str(s)]
    for i, g in enumerate(gains or []):
        gp = tmp / f"g{i}_gains_wash.json"
        gp.write_text(json.dumps(g))
        cmd += ["--gains", str(gp)]
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    return {row["ticker"]: row for sec in doc["sections"]
            for row in sec["rows"]}


def _cat(rows, t):
    return (rows.get(t) or {}).get("category", "")


class TestPerHolderRule(unittest.TestCase):
    """The engine's s.54 test: per holder, min(acquired in the window,
    held at its end). Units a registered account held before the window
    neither create nor back a denial."""

    def test_exitable_with_prewindow_sheltered_holding(self):   # R1-231
        tax = [_row("2026-03-02", "XYZ.TO", 100, 5000.0),
               _row("2026-09-20", "XYZ.TO", 10, 400.0)]
        shl = [_row("2024-05-01", "XYZ.TO", 100, 4000.0, account="rrsp")]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-29", sheltered=shl)
        r = rows["XYZ.TO"]
        self.assertEqual(r["category"], "EXITABLE")
        self.assertNotIn("PERMANENTLY denied", r["advisory"])
        self.assertIn("FULL position at a loss is fine now", r["advisory"])

    def test_locked_states_the_units_at_risk(self):   # R1-232, S055-01
        tax = [_row("2025-01-10", "AEM.TO", 100, 10000.0)]
        shl = [_row("2025-02-10", "AEM.TO", 49, 4900.0, account="rrsp2"),
               _row("2026-09-14", "AEM.TO", 4, 600.0, account="rrsp2")]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-29", sheltered=shl)
        r = rows["AEM.TO"]
        self.assertEqual(r["category"], "LOCKED")
        self.assertEqual(r["at_risk_qty"], 4.0)
        self.assertIn("up to 4 of your 100 shares", r["advisory"])
        self.assertNotIn("cannot be rescued", r["advisory"])
        self.assertIn("unless the registered account sells", r["advisory"])

    def test_sold_out_buyer_does_not_lock(self):   # S054-08
        tax = [_row("2025-03-03", "XYZ.TO", 100, 5000.0)]
        shl = [_row("2025-04-01", "XYZ.TO", 1000, 50000.0, account="rrsp"),
               _row("2026-09-20", "XYZ.TO", 4, 160.0, account="rrsp2"),
               _row("2026-09-24", "XYZ.TO", -4, 150.0, account="rrsp2")]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-29", sheltered=shl)
        self.assertEqual(_cat(rows, "XYZ.TO"), "CAUTION")

    def test_violation_rescue_is_the_backing_holders(self):   # S054-03
        tax = [_row("2025-06-02", "XYZ.TO", 100, 5000.0),
               _row("2026-09-10", "XYZ.TO", -100, 4000.0),
               _row("2026-09-15", "XYZ.TO", 5, 200.0)]
        shl = [_row("2025-06-02", "XYZ.TO", 1000, 50000.0, account="rrsp")]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-20", sheltered=shl)
        r = rows["XYZ.TO"]
        self.assertEqual(r["category"], "VIOLATION")
        self.assertEqual(r["rescue"], [{"holder": "taxable", "account": "",
                                        "symbol": "XYZ.TO", "qty": 5.0}])
        self.assertIn("Sell 5.0000 shares (taxable 5)", r["advisory"])
        self.assertNotIn("PERMANENTLY", r["advisory"])
        # After selling the 5 taxable shares the loss stands (the RRSP's
        # pre-window shares back nothing): no VIOLATION.
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax + [_row("2026-09-21", "XYZ.TO", -5,
                                           190.0)],
                          "2026-09-22", sheltered=shl)
        self.assertNotEqual(_cat(rows, "XYZ.TO"), "VIOLATION")

    def test_sheltered_in_window_buy_backs_the_violation(self):
        tax = [_row("2025-06-02", "XYZ.TO", 100, 5000.0),
               _row("2026-09-10", "XYZ.TO", -100, 4000.0)]
        shl = [_row("2026-09-12", "XYZ.TO", 30, 1200.0, account="tfsa")]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-20", sheltered=shl)
        r = rows["XYZ.TO"]
        self.assertEqual(r["category"], "VIOLATION")
        self.assertEqual(r["rescue"][0]["holder"], "sheltered")
        self.assertEqual(r["rescue"][0]["account"], "tfsa")
        self.assertIn("PERMANENTLY", r["advisory"])


class TestDirection(unittest.TestCase):
    """Canada: every loss uses the LONG criteria (a new short or a
    written option acquires nothing)."""

    def test_reshort_is_not_a_trigger(self):   # S054-00 (D)
        tax = [_row("2026-06-01", "XYZ.TO", -100, 5000.0),
               _row("2026-09-10", "XYZ.TO", 100, 5500.0),
               _row("2026-09-17", "XYZ.TO", -100, 5400.0)]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-20")
        self.assertNotEqual(_cat(rows, "XYZ.TO"), "VIOLATION")

    def test_written_call_is_not_a_recent_buy(self):   # S054-00 (B)
        opt = "XYZ270115C00060000.TO"
        tax = [_row("2026-03-01", "XYZ.TO", 100, 5000.0),
               _row("2026-09-20", opt, -1, 150.0)]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-29")
        self.assertNotEqual(_cat(rows, opt), "EXITABLE")
        self.assertNotEqual(_cat(rows, "XYZ.TO"), "EXITABLE")

    def test_long_rebuy_after_short_cover_loss(self):   # S054-00 (G)
        tax = [_row("2026-07-01", "XYZ.TO", -100, 5000.0),
               _row("2026-08-10", "XYZ.TO", 100, 5500.0),
               _row("2026-08-17", "XYZ.TO", 100, 5400.0)]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-08-31")
        r = rows["XYZ.TO"]
        self.assertEqual(r["category"], "VIOLATION")
        self.assertIn("Sell 100.0000 shares", r["advisory"])

    def test_cover_into_long_two_rows_vs_one_row(self):   # S053-14
        two = [_row("2026-08-03", "XYZ.TO", -100, 1000.0),
               _row("2026-09-14", "XYZ.TO", 100, 1200.0),
               _row("2026-09-14", "XYZ.TO", 50, 600.0)]
        one = [_row("2026-08-03", "XYZ.TO", -100, 1000.0),
               _row("2026-09-14", "XYZ.TO", 150, 1800.0)]
        with tempfile.TemporaryDirectory() as tmp:
            r2 = _radar(tmp, two, "2026-09-20")["XYZ.TO"]
        with tempfile.TemporaryDirectory() as tmp:
            r1 = _radar(tmp, one, "2026-09-20")["XYZ.TO"]
        self.assertEqual(r2["category"], "VIOLATION")
        self.assertIn("Sell 50.0000 shares", r2["advisory"])
        # The engine allows the one-row cover: the loss row's own long
        # leg is not a replacement for its own loss.
        self.assertNotEqual(r1["category"], "VIOLATION")

    def test_us_keeps_the_reshort_rule(self):
        tax = [_row("2026-06-01", "XYZ.US", -100, 5000.0, currency="USD"),
               _row("2026-09-10", "XYZ.US", 40, 2400.0, currency="USD"),
               _row("2026-09-12", "XYZ.US", -40, 2300.0, currency="USD")]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-15", extra=["--country", "usa"])
        self.assertEqual(_cat(rows, "XYZ.US"), "VIOLATION")
        self.assertIn("Cover", rows["XYZ.US"]["advisory"])


class TestEdgeShapes(unittest.TestCase):
    def test_dust_replacement_backs_the_denial(self):   # S054-15
        tax = [_row("2026-01-12", "BTC", 1, 150000.0),
               _row("2026-09-01", "BTC", -1, 100000.0),
               _row("2026-09-08", "BTC", 0.009, 900.0)]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-10")
        self.assertEqual(_cat(rows, "BTC"), "VIOLATION")

    def test_exercised_call_is_not_a_loss(self):   # S054-04
        opt = "JKL260320C00060000.TO"
        tax = [_row("2026-09-01", opt, 1, 400.0),
               _row("2026-09-14", opt, -1, 0.0, action="ASSIGN"),
               _row("2026-09-14", "JKL.TO", 100, 6000.0, action="ASSIGN")]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-25")
        self.assertNotIn(_cat(rows, opt), ("COOLING", "VIOLATION",
                                           "BLOCKED"))

    def test_split_inside_a_sale_settle_lag(self):   # R1-241
        shl = [_row("2026-06-16", "FFN.TO", 220, 2200.0, account="rrsp"),
               _row("2026-06-30", "FFN.TO", 150, 1500.0, account="rrsp",
                    settle="2026-07-01"),
               dict(_row("2026-07-02", "FFN.TO", -84, 840.0,
                         account="rrsp", settle="2026-07-03"),
                    time="10:54:31"),
               dict(_row("2026-07-02", "FFN.TO", 1.1, 0.0, account="rrsp",
                         action="SPLIT"), time="20:25:00"),
               _row("2026-07-21", "FFN.TO", -314.6, 3100.0, account="rrsp",
                    settle="2026-07-22")]
        tax = [_row("2026-03-02", "ABC.TO", 10, 100.0)]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2026-09-29", sheltered=shl)
        self.assertAlmostEqual(
            (rows.get("FFN.TO") or {}).get("sheltered_qty", 0.0), 0.0,
            places=6)

    def test_own_account_move_keeps_balances(self):   # S053-20 R1
        shl = [_row("2025-05-01", "XYZ.TO", 100, 1000.0, account="rrspA"),
               _row("2025-05-20", "XYZ.TO", -100, 0.0, account="rrspA",
                    action="TRANSFER"),
               _row("2025-05-20", "XYZ.TO", 100, 0.0, account="rrspB",
                    action="TRANSFER"),
               _row("2025-05-28", "XYZ.TO", -100, 900.0, account="rrspB")]
        tax = [_row("2025-05-15", "XYZ.TO", 100, 1000.0),
               _row("2025-06-02", "XYZ.TO", -100, 800.0)]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2025-06-10", sheltered=shl)
        self.assertEqual(_cat(rows, "XYZ.TO"), "COOLING")

    def test_rebuy_after_own_account_move_is_a_trigger(self):   # S053-20 R2
        shl = [_row("2024-03-01", "XYZ.TO", 100, 1000.0, account="rrspA"),
               _row("2024-04-10", "XYZ.TO", -100, 0.0, account="rrspA",
                    action="TRANSFER"),
               _row("2024-04-10", "XYZ.TO", 100, 0.0, account="rrspB",
                    action="TRANSFER"),
               _row("2024-06-01", "XYZ.TO", -100, 900.0, account="rrspB"),
               _row("2025-06-10", "XYZ.TO", 100, 800.0, account="rrspB")]
        tax = [_row("2025-05-15", "XYZ.TO", 100, 1000.0),
               _row("2025-06-02", "XYZ.TO", -100, 800.0)]
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, tax, "2025-06-15", sheltered=shl)
        self.assertEqual(_cat(rows, "XYZ.TO"), "VIOLATION")

    def test_usa_ira_buy_locks_even_after_it_sold(self):   # S054-20
        tax = [_row("2026-01-05", "XYZ.US", 100, 5000.0, currency="USD")]
        shl = [_row("2026-09-10", "XYZ.US", 10, 450.0, account="roth",
                    currency="USD"),
               _row("2026-09-15", "XYZ.US", -10, 440.0, account="roth",
                    currency="USD")]
        with tempfile.TemporaryDirectory() as tmp:
            us = _radar(tmp, tax, "2026-09-29", sheltered=shl,
                        extra=["--country", "usa"])
        with tempfile.TemporaryDirectory() as tmp:
            ca = _radar(tmp, tax, "2026-09-29", sheltered=shl)
        self.assertEqual(_cat(us, "XYZ.US"), "LOCKED")
        self.assertEqual(_cat(ca, "XYZ.US"), "CAUTION")


class TestBuybackSettings(unittest.TestCase):   # R1-234
    OPT = "XYZ261218C00050000.TO"

    def _book(self, *extra):
        return [_row("2026-09-02", self.OPT, -1, 200.0, rid="w1"),
                _row("2026-09-16", self.OPT, 1, 100.0, rid="c1"),
                *extra]

    def _g(self, strict, rows):
        return _gains(rows, option_premium_timing="grant",
                      option_buyback_loss_superficial=strict)

    def test_strict_buyback_loss_then_rebuy_is_a_violation(self):
        rows = [_disp("w1", "2026-09-02", self.OPT, 1, 200.0, cost=0.0,
                      direction="SHORT", is_option=True, grant=True),
                _disp("c1", "2026-09-16", self.OPT, 1, -100.0, cost=0.0,
                      direction="SHORT", is_option=True)]
        with tempfile.TemporaryDirectory() as tmp:
            before = _radar(tmp, self._book(), "2026-09-20",
                            gains=[self._g(True, rows)])
        with tempfile.TemporaryDirectory() as tmp:
            after = _radar(tmp, self._book(
                _row("2026-09-29", self.OPT, 1, 120.0, rid="b1")),
                "2026-09-29", gains=[self._g(True, rows)])
        self.assertEqual(_cat(before, self.OPT), "COOLING")
        self.assertEqual(_cat(after, self.OPT), "VIOLATION")

    def test_default_rewrite_after_buyback_is_not_a_violation(self):
        rows = [_disp("w1", "2026-09-02", self.OPT, 1, 200.0, cost=0.0,
                      direction="SHORT", is_option=True, grant=True),
                _disp("c1", "2026-09-16", self.OPT, 1, -300.0, cost=0.0,
                      direction="SHORT", is_option=True)]
        book = [_row("2026-09-02", self.OPT, -1, 200.0, rid="w1"),
                _row("2026-09-16", self.OPT, 1, 300.0, rid="c1"),
                _row("2026-09-22", self.OPT, -1, 150.0, rid="w2")]
        with tempfile.TemporaryDirectory() as tmp:
            rows_r = _radar(tmp, book, "2026-09-25",
                            gains=[self._g(False, rows)])
        self.assertNotIn(_cat(rows_r, self.OPT), ("VIOLATION", "COOLING",
                                                  "BLOCKED"))


# ------------------------------------------------ sell-check / wash-radar
def _work_project(tmp, taxable, sheltered=None, config=None):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": taxable}))
    if sheltered is not None:
        (root / "work" / "sheltered_base.json").write_text(
            json.dumps({"transactions": sheltered}))
    if config is not None:
        (root / "taxjson.toml").write_text(config)
    return root


def _checks(root, cmd, sym):
    r = _cli(root, cmd, sym, "--json")
    doc = json.loads(r.stdout)
    return r.returncode, doc["results"][0]


class TestSellCheck(unittest.TestCase):
    def test_taxable_only_violation_is_action(self):   # S047-09
        today = date.today()
        d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
        tax = [_row(d(-400), "XYZ.TO", 100, 5000.0),
               _row(d(-10), "XYZ.TO", -50, 2000.0),
               _row(d(-5), "XYZ.TO", 10, 380.0)]
        shl = [_row(d(-700), "XYZ.TO", 100, 4000.0, account="rrsp")]
        with tempfile.TemporaryDirectory() as tmp:
            root = _work_project(tmp, tax, shl)
            rc, res = _checks(root, "sell-check", "XYZ.TO")
        self.assertEqual(res["verdict"], "ACTION", res)
        self.assertEqual(rc, 0)
        self.assertNotIn("permanently denied", " ".join(res["detail"]))

    def test_small_locked_fraction_is_partial(self):   # R1-232
        today = date.today()
        d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
        tax = [_row(d(-400), "AEM.TO", 100, 10000.0)]
        shl = [_row(d(-300), "AEM.TO", 49, 4900.0, account="rrsp2"),
               _row(d(-10), "AEM.TO", 4, 600.0, account="rrsp2")]
        with tempfile.TemporaryDirectory() as tmp:
            root = _work_project(tmp, tax, shl)
            rc, res = _checks(root, "sell-check", "AEM.TO")
        self.assertEqual(res["verdict"], "PARTIAL", res)
        self.assertEqual(rc, 1)
        self.assertIn("up to 4 of your 100", " ".join(res["detail"]))

    def test_whole_position_at_risk_stays_unsafe(self):
        today = date.today()
        d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
        tax = [_row(d(-400), "AEM.TO", 100, 10000.0)]
        shl = [_row(d(-10), "AEM.TO", 150, 15000.0, account="tfsa")]
        with tempfile.TemporaryDirectory() as tmp:
            root = _work_project(tmp, tax, shl)
            rc, res = _checks(root, "sell-check", "AEM.TO")
        self.assertEqual(res["verdict"], "UNSAFE", res)
        self.assertEqual(rc, 1)


class TestUnreadableConfig(unittest.TestCase):   # S048-13
    def test_radar_family_refuses_a_broken_config(self):
        today = date.today().isoformat()
        tax = [_row(today, "ZZZ.TO", 50, 500.0)]
        with tempfile.TemporaryDirectory() as tmp:
            root = _work_project(tmp, tax, config="[settings\nyear = 2026\n")
            (root / "work" / "tfsa_base.json").write_text(
                json.dumps({"transactions": tax}))
            outs = [_cli(root, c, *a) for c, *a in (
                ("wash-radar",), ("wash-radar", "tfsa"),
                ("buy-check", "ZZZ.TO"), ("sell-check", "ZZZ.TO"))]
        for r in outs:
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("taxjson.toml", r.stderr)


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------- harvest
from taxjson.bin import taxjson_harvest as H  # noqa: E402


def _harvest(argv, prices=None, option_prices=None, seen=None):
    def _fetch(remaining):
        return {s: ((prices or {})[s], "fake") for s in remaining
                if s in (prices or {})}

    def _opt(remaining):
        if seen is not None:
            seen.extend(remaining)
        return {s: ((option_prices or {})[s], "ibkr") for s in remaining
                if s in (option_prices or {})}
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = H.main(argv, fetchers=[_fetch], option_fetchers=[_opt])
    return rc, out.getvalue(), err.getvalue()


class TestHarvest(unittest.TestCase):
    def test_violation_deadline_day_is_sell_by(self):   # S033-24
        today = date.today()
        rec = {"category": "VIOLATION", "clears_at": today.isoformat()}
        self.assertEqual(H._advisory_display(rec, today),
                         f"VIOLATION(sell-by:{today.isoformat()},+0d)")
        rec["clears_at"] = (today - timedelta(days=1)).isoformat()
        self.assertIn("deadline-passed", H._advisory_display(rec, today))

    def test_locked_fraction_is_claimable_now(self):   # R1-232
        clears = (date.today() + timedelta(days=15)).isoformat()
        rows = [{"verdict": "LOSS", "unrealized": -1000.0,
                 "radar": {"category": "LOCKED", "clears_at": clears,
                           "at_risk_qty": 4.0, "taxable_qty": 100.0}}]
        sched = H._recovery_schedule(rows)
        self.assertEqual(sched["now"], 960.0)
        self.assertEqual(sched["30d"], 1000.0)
        # An older sidecar without the field keeps the whole loss locked.
        rows[0]["radar"].pop("at_risk_qty")
        self.assertEqual(H._recovery_schedule(rows)["now"], 0.0)

    def test_grant_timed_short_shows_the_buyback_loss(self):   # R1-230
        exp = (date.today() + timedelta(days=80)).strftime("%y%m%d")
        opt = f"XYZ{exp}C00050000.TO"
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains_wash.json"
            g.write_text(json.dumps({"inventory": [
                {"symbol": opt, "qty": -1, "total_cost": -200.0,
                 "recognised_premium": 200.0}]}))
            rc, out, err = _harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--options",
                 "--json"], option_prices={opt: 1.00})
        self.assertEqual(rc, 0, err)
        row = json.loads(out)["rows"][0]
        # Buying back at 1.00 x 100 books a -100 loss (the premium was
        # taxed at the write), not a +100 gain.
        self.assertAlmostEqual(row["unrealized"], -100.0)
        self.assertEqual(row["verdict"], "LOSS")

    def test_renamed_option_is_quoted_as_the_contract_held(self):  # S034-11
        exp = (date.today() + timedelta(days=200)).strftime("%y%m%d")
        held, booked = f"KGC{exp}C00012000.US", f"K{exp}C00012000.TO"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = root / "margin_gains_wash.json"
            g.write_text(json.dumps({"inventory": [
                {"symbol": booked, "qty": 1, "total_cost": 136.0}]}))
            (root / "margin_raw_gains.json").write_text(json.dumps(
                {"inventory": [{"symbol": held, "qty": 1,
                                "total_cost": 100.0}]}))
            (root / "ticker.map").write_text("TOBASE KGC.US K.TO\n")
            (root / "to_base.csv").write_text(
                f"{date.today().isoformat()} 12:00:00 USD CAD 1.38\n")
            seen = []
            rc, out, err = _harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--options",
                 "--ticker-map", str(root / "ticker.map"), "--json"],
                option_prices={held: 1.00}, seen=seen)
        self.assertEqual(rc, 0, err)
        self.assertEqual(seen, [held])
        row = json.loads(out)["rows"][0]
        self.assertEqual(row["price_currency"], "USD")
        self.assertAlmostEqual(row["value"], 138.0)


class TestHarvestStaleRadar(unittest.TestCase):   # S038-09
    def test_single_account_run_does_not_serve_a_stale_radar(self):
        today = date.today()
        d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
        margin = _QT_HEADER + _qt(d(-200), d(-200), "Buy", "XYZ.TO", 200,
                                  10.0)
        tfsa = _QT_HEADER + _qt(d(-10), d(-10), "Buy", "XYZ.TO", 10, 7.0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(_config(
                today.year, [("margin", "taxable"), ("tfsa", "sheltered")]))
            for a in ("margin", "tfsa"):
                (root / "inputs" / a).mkdir(parents=True)
            (root / "inputs" / "margin" / "m.csv").write_text(margin)
            (root / "inputs" / "tfsa" / "t.csv").write_text(
                _QT_HEADER + _qt(d(-300), d(-300), "Buy", "ABC.TO", 1, 5.0))
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (root / "inputs" / "tfsa" / "t.csv").write_text(tfsa)
            r = _cli(root, "run", "--no-input", "--account", "tfsa")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (root / "work" / ".price_cache.json").write_text(json.dumps(
                {"XYZ.TO": {"price": 8.0, "asof": today.isoformat(),
                            "currency": "CAD"}}))
            h = _cli(root, "harvest", "--no-ibkr", "--json")
        self.assertEqual(h.returncode, 0, h.stderr[-2000:])
        rows = json.loads(h.stdout)["rows"]
        xyz = next(x for x in rows if x["symbol"] == "XYZ.TO")
        self.assertEqual((xyz.get("radar") or {}).get("category"), "LOCKED",
                         h.stderr)


# --------------------------------------------------------- safe-to-sell
def _sts(taxable, as_of=None, sheltered=None, extra=()):
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / "t.json"
        t.write_text(json.dumps({"transactions": taxable}))
        cmd = [sys.executable, "-m", "taxjson.bin.taxjson_safe_to_sell",
               "--taxable", str(t), *extra]
        if as_of:
            cmd += ["--date", as_of]
        if sheltered is not None:
            s = Path(tmp) / "s.json"
            s.write_text(json.dumps({"transactions": sheltered}))
            cmd += ["--sheltered", str(s)]
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                           text=True)
    return r


def _sts_rows(out):
    rows = {}
    for ln in out.splitlines():
        cells = ln.split()
        if len(cells) >= 3 and cells[1].replace(".", "").lstrip(
                "-").isdigit():
            rows[cells[0]] = (float(cells[1]), cells[2])
    return rows


class TestSafeToSell(unittest.TestCase):
    def test_buy_made_today_is_seen(self):   # S007-09
        today = date.today()
        tax = [_row("2026-01-05", "SYN.TO", 100, 5000.0),
               _row(today.isoformat(), "SYN.TO", 100, 3000.0,
                    settle=(today + timedelta(days=1)).isoformat())]
        r = _sts(tax)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_sts_rows(r.stdout)["SYN.TO"],
                         (200.0, "FULL-EXIT-ONLY"))

    def test_short_cover_is_not_a_long_lot(self):   # R1-233
        opt = "ABC261218C00050000.TO"
        tax = [_row("2026-05-01", "XYZ.TO", -100, 5000.0),
               _row("2026-05-01", "XYZ.TO", 100, 4900.0),
               _row("2026-09-15", opt, -3, 300.0)]
        r = _sts(tax, "2026-09-29")
        rows = _sts_rows(r.stdout)
        self.assertNotIn("XYZ.TO", rows)
        self.assertEqual(rows[opt][0], -3.0)

    def test_rename_is_followed(self):   # S050-03
        tax = [_row("2026-01-05", "OLD.TO", 100, 5000.0),
               dict(_row("2026-03-02", "OLD.TO", 1.0, 0.0, action="SPLIT"),
                    symbol_new="NEW.TO"),
               _row("2026-04-01", "NEW.TO", -100, 5200.0)]
        r = _sts(tax, "2026-09-29")
        self.assertEqual(_sts_rows(r.stdout), {})

    def test_bad_date_is_a_usage_error(self):
        r = _sts([_row("2026-01-05", "SYN.TO", 100, 5000.0)], "2026-13-01")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
