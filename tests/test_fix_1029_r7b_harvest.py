"""Fix round 7 (wave B), 2026-10-29: harvest, watch's harvest baseline,
tips, div-estimate and fees (backlog TJ-0908, TJ-0709, TJ-0710,
TJ-0911, TJ-0912, TJ-0913, TJ-0914, TJ-0900, TJ-0897, TJ-0776).

Synthetic data only; offline throughout (injected fetchers or a cache).
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

from taxjson.bin.taxjson_harvest import main as harvest_main
from tax_rules import rule


def _run_harvest(argv, prices):
    """harvest_main with a fake live tier keyed by the QUOTE spelling."""
    def _fetch(remaining):
        return {s: (prices[y], "fake") for s, y in remaining.items()
                if y in prices}
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = harvest_main(argv, fetchers=[_fetch])
    return rc, out.getvalue(), err.getvalue()


def _gains(root: Path, inventory, name="margin_gains_wash.json"):
    p = root / name
    p.write_text(json.dumps({"summary": {"year": 2026}, "transactions": [],
                             "inventory": inventory}))
    return p


class TestQuoteRatio(unittest.TestCase):
    """TJ-0709: a QUOTE line's QTY_RATIO converts the held quantity into
    the quoted ticker's units — 100 ZZOLD.TO quoted as ZZNEW.TO at 0.25
    is 25 ZZNEW.TO shares."""

    def test_ratio_applied_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZOLD.TO", "qty": 100,
                               "total_cost": 500.0,
                               "last_acq_date": "2026-01-02"}])
            (root / "ticker.map").write_text(
                "QUOTE ZZOLD.TO ZZNEW.TO 0.25\n")
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                {"ZZNEW.TO": 20.0})
        self.assertEqual(rc, 0, err)
        row = json.loads(out)["rows"][0]
        self.assertAlmostEqual(row["value"], 500.0)
        self.assertAlmostEqual(row["unrealized"], 0.0)
        self.assertEqual(row["verdict"], "FLAT")
        self.assertAlmostEqual(row["quote_ratio"], 0.25)

    def test_ratio_in_breakeven_and_price_per_held_share(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZOLD.TO", "qty": 100,
                               "total_cost": 600.0,
                               "last_acq_date": "2026-01-02"}])
            (root / "ticker.map").write_text(
                "QUOTE ZZOLD.TO ZZNEW.TO 0.25\n")
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                {"ZZNEW.TO": 20.0})
        self.assertEqual(rc, 0, err)
        row = json.loads(out)["rows"][0]
        self.assertAlmostEqual(row["unrealized"], -100.0)
        # PRICE per BOOK share (beside COST/SH): 20 x 0.25.
        self.assertAlmostEqual(row["price_base"], 5.0)
        # EXIT@ in the quoted listing's units: 600 / 25 x 1.02.
        self.assertAlmostEqual(row["breakeven_exit_native"], 24.48)


class TestExplicitTickerMap(unittest.TestCase):
    """TJ-0911: --ticker-map's QUOTE (and CRYPTO) lines pick the quote
    identity, not a ticker.map found beside the inputs."""

    def test_explicit_map_quote_used(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 10,
                               "total_cost": 150.0,
                               "last_acq_date": "2026-01-02"}])
            custom = root / "custom_rules.map"
            custom.write_text("QUOTE ZZSYN.TO ZZALT.TO\n")
            prices = {"ZZSYN.TO": 10.0, "ZZALT.TO": 20.0}
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--ticker-map", str(custom)], prices)
        self.assertEqual(rc, 0, err)
        row = json.loads(out)["rows"][0]
        self.assertAlmostEqual(row["value"], 200.0)
        self.assertEqual(row["verdict"], "GAIN")

    def test_explicit_map_wins_over_the_found_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 10,
                               "total_cost": 150.0,
                               "last_acq_date": "2026-01-02"}])
            (root / "ticker.map").write_text("QUOTE ZZSYN.TO ZZOTH.TO\n")
            custom = root / "custom_rules.map"
            custom.write_text("QUOTE ZZSYN.TO ZZALT.TO\n")
            prices = {"ZZOTH.TO": 1.0, "ZZALT.TO": 20.0}
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--ticker-map", str(custom)], prices)
        self.assertEqual(rc, 0, err)
        self.assertAlmostEqual(json.loads(out)["rows"][0]["value"], 200.0)

    def test_explicit_map_unpriced_alt_is_omitted_not_old_quote(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 10,
                               "total_cost": 150.0,
                               "last_acq_date": "2026-01-02"}])
            custom = root / "custom_rules.map"
            custom.write_text("QUOTE ZZSYN.TO ZZALT.TO\n")
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--ticker-map", str(custom)], {"ZZSYN.TO": 10.0})
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual(doc["rows"], [])
        self.assertEqual(doc["omitted"][0]["symbol"], "ZZSYN.TO")


class TestUnknownBasis(unittest.TestCase):
    """TJ-0710: a holding whose basis is partly unknown has no definite
    unrealized result: it is omitted from the quantified rows with the
    reason, never a GAIN on the known fraction's cost."""

    def test_unknown_cost_omitted_with_reason(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [
                {"symbol": "ZZSYN.TO", "qty": 101, "total_cost": 5.0,
                 "unknown_cost": True, "last_acq_date": "2026-01-02"},
                {"symbol": "ZZOK.TO", "qty": 10, "total_cost": 100.0,
                 "last_acq_date": "2026-01-02"}])
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                {"ZZSYN.TO": 5.0, "ZZOK.TO": 9.0})
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual([r["symbol"] for r in doc["rows"]], ["ZZOK.TO"])
        self.assertEqual(doc["omitted"][0]["symbol"], "ZZSYN.TO")
        self.assertIn("unknown", doc["omitted"][0]["reason"])
        self.assertAlmostEqual(doc["totals"]["unrealized"], -10.0)
        self.assertIn("ZZSYN.TO", err)

    def test_unknown_cost_text_names_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [
                {"symbol": "ZZSYN.TO", "qty": 101, "total_cost": 5.0,
                 "unknown_cost": True, "last_acq_date": "2026-01-02"}])
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada"],
                {"ZZSYN.TO": 5.0})
        self.assertEqual(rc, 0, err)
        self.assertNotIn("GAIN", out)
        self.assertIn("unknown", err)


def _iso(days):
    return (date.today() + timedelta(days=days)).isoformat()


class TestRegisteredBackedViolation(unittest.TestCase):
    """TJ-0908: an OPEN VIOLATION whose replacement units a registered
    account bought in the window and still holds is not harvestable
    now: a loss sale today is superficial for those units too, and the
    denial is permanent. It waits for that buy to age out (+31 days)."""

    def _row(self, rescue, sh_add=None, sh_known=True, tq=10.0):
        return {"verdict": "LOSS", "unrealized": -100.0,
                "radar": {"category": "VIOLATION", "clears_at": _iso(19),
                          "taxable_qty": tq, "rescue": rescue},
                "sheltered_last_add": sh_add,
                "sheltered_known": sh_known}

    def test_registered_backed_waits_for_age_out(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        s = _recovery_schedule([self._row(
            [{"holder": "sheltered", "account": "registered",
              "symbol": "ZZSYN.TO", "qty": 20.0}], _iso(-5))])
        self.assertEqual(s["now"], 0.0)
        self.assertEqual(s["14d"], 0.0)
        self.assertEqual(s["30d"], 100.0)       # 26 days from now

    def test_partly_backed_splits(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        s = _recovery_schedule([self._row(
            [{"holder": "sheltered", "account": "registered",
              "symbol": "ZZSYN.TO", "qty": 4.0},
             {"holder": "taxable", "account": "",
              "symbol": "ZZSYN.TO", "qty": 6.0}], _iso(-5))])
        self.assertEqual(s["now"], 60.0)
        self.assertEqual(s["30d"], 100.0)

    def test_unknown_registered_side_is_no_clear(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        s = _recovery_schedule([self._row(
            [{"holder": "sheltered", "account": "registered",
              "symbol": "ZZSYN.TO", "qty": 20.0}], None, sh_known=False)])
        self.assertEqual(s["now"], 0.0)
        self.assertEqual(s["no_clear"], 100.0)

    def test_taxable_backed_still_now(self):
        from taxjson.bin.taxjson_harvest import _recovery_schedule
        s = _recovery_schedule([self._row(
            [{"holder": "taxable", "account": "", "symbol": "ZZSYN.TO",
              "qty": 10.0}], _iso(-5))])
        self.assertEqual(s["now"], 100.0)

    def test_cli_with_radar_and_sheltered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 10,
                               "total_cost": 200.0,
                               "position_start_date": _iso(-150),
                               "last_acq_date": _iso(-150)}])
            sh = _gains(root, [{"symbol": "ZZSYN.TO", "qty": 20,
                                "total_cost": 200.0,
                                "position_start_date": _iso(-5),
                                "last_acq_date": _iso(-5),
                                "last_acq_settle": _iso(-4)}],
                        name="registered_gains_wash.json")
            radar = root / "wash_radar_margin.json"
            radar.write_text(json.dumps({"sections": [{
                "category": "VIOLATION", "rows": [{
                    "ticker": "ZZSYN.TO", "category": "VIOLATION",
                    "advisory": "VIOLATION: Sell ...",
                    "clears_at": _iso(19), "taxable_qty": 10.0,
                    "rescue": [{"holder": "sheltered",
                                "account": "registered",
                                "symbol": "ZZSYN.TO", "qty": 20.0}]}]}]}))
            rc, out, err = _run_harvest(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--radar", str(radar), "--sheltered", str(sh)],
                {"ZZSYN.TO": 10.0})
        self.assertEqual(rc, 0, err)
        h = json.loads(out)["totals"]["harvestable"]
        self.assertEqual(h["now"], 0.0)
        self.assertEqual(h["30d"], 100.0)



class TestWatchHarvestCoverage(unittest.TestCase):
    """TJ-0912: watch compares harvest totals only when they leave out
    the same positions. A run that leaves out a position the baseline
    valued (its FX rate missing) keeps the baseline and says so."""

    def test_left_out_and_coverage(self):
        from taxjson.bin.taxjson_watch import (harvest_coverage,
                                               harvest_left_out)
        left = harvest_left_out({"omitted": [
            {"account": "margin", "symbol": "ZZB.TO",
             "reason": "no USD->CAD rate"}]})
        self.assertEqual(left, [{"key": "margin|ZZB.TO",
                                 "shown": "ZZB.TO (no USD->CAD rate)"}])
        self.assertEqual(harvest_coverage([], ["margin|ZZB.TO"]), "worse")
        self.assertEqual(harvest_coverage(["margin|ZZB.TO"], []), "better")
        self.assertEqual(harvest_coverage(["a|X"], ["a|X"]), "same")
        self.assertEqual(harvest_coverage(None, ["a|X"]), "same")

    def _project(self, tmp):
        import subprocess
        import sys
        d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
        root = Path(tmp)
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = %d\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n' % date.today().year)
        (root / "inputs" / "margin" / "trades.tt").write_text(
            f"BUYSELL {d(60)} 10:00:00 ZZA.TO 10 CAD 30 300 0\n")
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "run", "--no-input"], capture_output=True,
            text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return root

    def _watch(self, root, hdoc):
        import argparse
        import subprocess
        from unittest import mock
        import taxjson.lib.dispatch as D
        from taxjson.bin import taxjson_run as R
        real = D.run_cmd

        def fake(cmd, **kw):
            if "harvest" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, json.dumps(hdoc), "")
            return real(cmd, **kw)

        args = argparse.Namespace(dir=str(root), json=True, harvest=True,
                                  no_ibkr=True, threshold=0.0, state=None,
                                  exit_code=False)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(D, "run_cmd", fake), \
                redirect_stdout(out), redirect_stderr(err):
            R.cmd_watch(args)
        return json.loads(out.getvalue()), err.getvalue()

    @staticmethod
    def _hdoc(now, omitted=()):
        # The total values ZZB.TO unless it is left out: TJ-1040 reads
        # the rows for what a baseline valued.
        return {"rows": ([] if "ZZB.TO" in omitted else
                         [{"account": "margin", "symbol": "ZZB.TO"}]),
                "totals": {"harvestable": {"now": now}},
                "omitted": [{"account": "margin", "symbol": s,
                             "reason": "no USD->CAD rate"}
                            for s in omitted]}

    def test_partial_total_keeps_the_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            doc, _ = self._watch(root, self._hdoc(200.0))
            self.assertTrue(doc["baseline"])
            doc, err = self._watch(root, self._hdoc(100.0, ["ZZB.TO"]))
            self.assertEqual(doc["changes"], [])
            self.assertEqual(doc["incomplete"],
                             ["harvest: ZZB.TO (no USD->CAD rate)"])
            self.assertIn("not compared", err)
            state = json.loads(
                (root / "work" / ".watch_state.json").read_text())
            self.assertEqual(state["harvest_now"], 200.0)
            # The rate is back: same coverage, same total — no change.
            doc, err = self._watch(root, self._hdoc(200.0))
            self.assertEqual(doc["changes"], [])
            self.assertEqual(doc["incomplete"], [])
            # A real move with the same coverage is still reported.
            doc, _ = self._watch(root, self._hdoc(500.0))
            self.assertEqual([c["kind"] for c in doc["changes"]],
                             ["harvest_now"])

    def test_fuller_total_becomes_the_baseline_uncompared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            self._watch(root, self._hdoc(100.0, ["ZZB.TO"]))
            doc, _ = self._watch(root, self._hdoc(400.0))
            self.assertEqual(doc["changes"], [])
            state = json.loads(
                (root / "work" / ".watch_state.json").read_text())
            self.assertEqual(state["harvest_now"], 400.0)
            self.assertEqual(state["harvest_omitted"], [])



class TestTipsForeignIssuerWithCanadianLine(unittest.TestCase):
    """TJ-0913: an issuer the project declares US-domiciled stays a US
    issuer when its Canadian listing is joined (TOBASE): no "Canadian
    issuer ... eligible-dividend" advice, and TFSA-US-DIV in a TFSA."""

    def _findings(self, td, plan, tmap):
        from test_tips import _holdings_toml, _project, _raw_json, _run
        root = _project(td, accounts=[(plan, "sheltered" if plan == "tfsa"
                                       else "taxable")],
                        holdings={plan: _holdings_toml("ZZSY.US")},
                        raws={plan: _raw_json("ZZSY.US")},
                        ticker_map=tmap)
        r = _run(root, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return [(f["check"], f["message"])
                for f in json.loads(r.stdout)["findings"]]

    @rule("CA-SCAN-01", "CA-SCAN-02")
    def test_declared_us_issuer_in_tfsa_with_a_canadian_line(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._findings(td, "tfsa", "T1135 ZZSY.US USA\n"
                                           "TOBASE ZZSY.US ZZSY.TO\n")
        self.assertEqual([c for c, _ in f], ["TFSA-US-DIV"])
        self.assertNotIn("Canadian issuer", f[0][1])

    @rule("CA-SCAN-02")
    def test_declared_us_issuer_in_taxable_no_listing_advice(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._findings(td, "margin", "T1135 ZZSY.US USA\n"
                                             "TOBASE ZZSY.US ZZSY.TO\n")
        self.assertEqual(f, [])

    @rule("CA-SCAN-02")
    def test_declared_canadian_issuer_keeps_listing_advice(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._findings(td, "tfsa", "T1135 ZZSY.US CAN\n"
                                           "TOBASE ZZSY.US ZZSY.TO\n")
        self.assertEqual([c for c, _ in f], ["US-LISTING"])

    @rule("CA-SCAN-02")
    def test_no_domicile_on_record_keeps_listing_advice(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._findings(td, "margin", "TOBASE ZZSY.US ZZSY.TO\n")
        self.assertEqual([c for c, _ in f], ["US-LISTING"])



class TestTipsShortSeller(unittest.TestCase):
    """TJ-0914: a short position that paid a dividend in lieu receives
    no dividend: no US-LISTING (eligible-dividend) advice for it."""

    def _findings(self, td, qty, action, amount):
        from test_tips import _project, _run
        holdings = ('schema_version = "1.2"\n[[holding]]\n'
                    f'symbol = "ZZSY.US"\nquantity = {qty}\n'
                    'currency = "CAD"\ntotal_cost = -100.0\n'
                    'cost_per_share = 10.0\n')
        raw = json.dumps({"transactions": [
            {"action": action, "date": "2026-03-01", "symbol": "ZZSY.US",
             "quantity": 0, "gross_amount": amount, "net_amount": 0.0,
             "currency": "CAD"}]})
        root = _project(td, accounts=[("margin", "taxable")],
                        holdings={"margin": holdings},
                        raws={"margin": raw},
                        ticker_map="T1135 ZZSY.US CAN\n"
                                   "TOBASE ZZSY.US ZZSY.TO\n")
        r = _run(root, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return [f["check"] for f in json.loads(r.stdout)["findings"]]

    @rule("CA-SCAN-02")
    def test_short_payer_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                self._findings(td, -10.0, "DIVIDEND_IN_LIEU", -10.0), [])

    @rule("CA-SCAN-02")
    def test_paid_in_lieu_is_not_a_dividend_received(self):
        # Long now, but the only payment on record was a charge.
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                self._findings(td, 10.0, "DIVIDEND_IN_LIEU", -10.0), [])

    @rule("CA-SCAN-02")
    def test_long_recipient_still_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                self._findings(td, 10.0, "DIVIDEND", 10.0), ["US-LISTING"])
        with tempfile.TemporaryDirectory() as td:
            # A payment in lieu RECEIVED (shares lent out) counts.
            self.assertEqual(
                self._findings(td, 10.0, "DIVIDEND_IN_LIEU", 10.0),
                ["US-LISTING"])



class TestDivEstimateSpecialPaidLast(unittest.TestCase):
    """TJ-0900: an off-cycle special paid AFTER the regular monthly
    payments does not turn the payer quarterly: the forecast keeps the
    monthly schedule from the latest regular payment, at its amount,
    and names the special."""

    @staticmethod
    def _rows(special=True):
        from test_div_estimate import _div
        rows = [("margin", _div(f"2025-0{m}-15", "ZZD.TO", 10, 1.0))
                for m in (1, 2, 3, 4)]
        if special:
            rows.append(("margin", _div("2025-05-01", "ZZD.TO", 10, 5.0)))
        return rows

    @rule("CA-PLAN-06")
    def test_special_last_keeps_the_monthly_forecast(self):
        from test_div_estimate import _est, _pos
        plain = _est(self._rows(False), [_pos("ZZD.TO", 10)],
                     as_of="2025-05-02", months=6, year=2025)["rows"][0]
        r = _est(self._rows(), [_pos("ZZD.TO", 10)], as_of="2025-05-02",
                 months=6, year=2025)["rows"][0]
        self.assertEqual(r["frequency"], "monthly")
        self.assertEqual(r["next_pay_date"], "2025-05-15")
        self.assertAlmostEqual(r["next_amount"], 10.0)
        self.assertAlmostEqual(r["window_amount"], plain["window_amount"])
        self.assertEqual(r["status"], "ok")
        self.assertTrue(any("2025-05-01" in f and "special" in f
                            for f in r["flags"]), r["flags"])
        # The latest payment is still shown as the latest.
        self.assertEqual(r["last_pay_date"], "2025-05-01")

    def test_detect_schedule_names_the_latest_regular_payment(self):
        from taxjson.lib.div_estimate import detect_schedule
        ds = ["2025-01-15", "2025-02-15", "2025-03-15", "2025-04-15",
              "2025-05-01"]
        self.assertEqual(detect_schedule(ds)[0::3], ("monthly",
                                                    "2025-04-15"))
        # Regular payments after the special: anchored at the latest.
        ds = ["2025-01-15", "2025-02-15", "2025-02-27", "2025-03-15",
              "2025-04-15"]
        self.assertEqual(detect_schedule(ds)[0::3], ("monthly",
                                                    "2025-04-15"))



def _fee_file(td, rows, name="margin_raw.json", broker="synthetic"):
    p = Path(td) / name
    p.write_text(json.dumps({"metadata": {"source_brokerage": broker},
                             "transactions": rows}))
    return p


def _fee_row(i, sym, qty, commission, **kw):
    r = {"id": str(i), "action": "BUYSELL", "date": "2025-03-03",
         "symbol": sym, "quantity": qty, "currency": "CAD",
         "commission": commission, "fee": 0.0}
    r.update(kw)
    return r


class TestFeesFuturesInTheSplit(unittest.TestCase):
    """TJ-0897: the non-option / option split accounts for the plain
    futures' commissions (NON-OPTION), and the legend no longer promises
    a per-unit futures fee."""

    def test_split_adds_up_to_the_total(self):
        from taxjson.bin import taxjson_fees as F
        with tempfile.TemporaryDirectory() as td:
            f = _fee_file(td, [
                _fee_row(1, "F:ZZCLZ5", 1, 2.0, price=70.0),
                _fee_row(2, "F:ZZCLZ5", -1, 2.0, price=71.0),
                _fee_row(3, "ZZOPT250620C00010000.TO", 1, 1.0,
                         gross_amount=100.0)])
            b, g, info = F.aggregate([f], year="2025", since=None,
                                     to_curr="CAD", history={},
                                     default_rate=None, by_account=False)
            lines = F.render_text(b, g, info, to_curr="CAD",
                                  by_account=False, year="2025",
                                  default_rate=None)
        text = "\n".join(lines)
        split = text[text.index("NON-OPTION"):]
        row = [ln for ln in split.splitlines()
               if ln.startswith("synthetic")][0]
        self.assertEqual(row.split()[1:], ["4.00", "1.00"])
        self.assertNotIn("contract of a future", text)



class TestFeesTradeValue(unittest.TestCase):
    """TJ-0776: %NOTNL's trade value is the gross (or price x quantity),
    never a fee-only net cash: a zero-price assignment is out of both
    sides, and a hand-entered trade's fee-inclusive net is not its
    gross."""

    def _pct(self, rows):
        from taxjson.bin import taxjson_fees as F
        with tempfile.TemporaryDirectory() as td:
            f = _fee_file(td, rows)
            _b, g, _i = F.aggregate([f], year="2025", since=None,
                                    to_curr=None, history={},
                                    default_rate=None, by_account=False)
        m = F.metrics(g["base"])
        return m["total"], m["pct_notional"]

    def test_commission_only_assignment_is_out_of_the_percentage(self):
        total, pct = self._pct([
            _fee_row("a", "ZZSY.TO", 100, 1.0, price=1.0,
                     gross_amount=100.0, net_amount=101.0),
            _fee_row("b", "ZZSY250117C00001000.TO", 1, 2.0, price=0.0,
                     gross_amount=0.0, net_amount=2.0, action="ASSIGN")])
        self.assertAlmostEqual(total, 3.0)
        self.assertAlmostEqual(pct, 1.0)

    def test_hand_entered_trade_value_is_price_times_quantity(self):
        # A .tt row: gross 0 in the parsed book, net 101 with the fee.
        total, pct = self._pct([
            _fee_row("a", "ZZSY.TO", 100, 1.0, price=1.0,
                     gross_amount=0.0, net_amount=101.0)])
        self.assertAlmostEqual(pct, 1.0)

    def test_option_premium_times_contract_size(self):
        from taxjson.bin.taxjson_fees import trade_value
        tx = _fee_row("o", "ZZSY250117C00001000.TO", -1, 0.0, price=0.1,
                      gross_amount=0.0, net_amount=10.0)
        self.assertAlmostEqual(trade_value(tx, True, False), 10.0)
        self.assertAlmostEqual(
            trade_value(dict(tx, multiplier=10), True, False), 1.0)
        # A future's contract size is unknown here: no notional.
        self.assertEqual(trade_value(
            _fee_row("f", "F:ZZCLZ5", 1, 2.0, price=70.0), False, True),
            0.0)
        # No price on the row: its net cash, as before.
        self.assertEqual(trade_value({"net_amount": -50.0}, False, False),
                         50.0)


if __name__ == "__main__":
    unittest.main()
