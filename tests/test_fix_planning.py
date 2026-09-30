"""Planning-tool fixes (2026-09 audit): the wash radar and the tools
built on it (wash-radar, watch, buy-check, sell-check, the run's
reports/wash_radar_*), the web what-if, and `taxjson redact`.

  R1-225  trades made but not yet settled are in the books
  R1-226  loss detection comes from the engine's gains (s.47 blend,
          denied-loss bump), not the radar's own per-account pool
  S006-09 phantoms.json openings are applied like the gains pass does
  R1-227  web what-if applies the x100 option multiplier
  S022-02 web what-if maps the symbol like the pipeline (options
          follow their underlying's ticker.map rule)
  R1-250  redact removes holder names in Coinbase / IB Flex / IB HTML
          layouts and every IB id occurrence

All data is synthetic.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------- radar CLI
def _row(date_, symbol, qty, net, settle=None, account="margin",
         action="BUYSELL", rid=None, price=None, currency="CAD"):
    r = dict(action=action, date=date_, date_settle=settle or date_,
             time="10:00:00", symbol=symbol, quantity=qty,
             net_amount=net, currency=currency, account=account,
             price=price if price is not None else (abs(net / qty)
                                                    if qty else 0.0))
    if rid:
        r["id"] = rid
    return r


def _radar(tmp, taxable, as_of, sheltered=None, gains=None,
           phantoms=None):
    tmp = Path(tmp)
    t = tmp / "margin_base.json"
    t.write_text(json.dumps({"transactions": taxable}))
    cmd = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
           "--taxable", str(t), "--date", as_of, "--all", "--json"]
    if sheltered is not None:
        s = tmp / "sheltered_base.json"
        s.write_text(json.dumps({"transactions": sheltered}))
        cmd += ["--sheltered", str(s)]
    for i, g in enumerate(gains or []):
        gp = tmp / f"g{i}_gains_wash.json"
        gp.write_text(json.dumps(g))
        cmd += ["--gains", str(gp)]
    if phantoms is not None:
        pp = tmp / "phantoms.json"
        pp.write_text(json.dumps(phantoms))
        cmd += ["--incomplete-history", str(pp)]
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    return {row["ticker"]: row for sec in doc["sections"]
            for row in sec["rows"]}


def _gains(rows, year=2026, **summary):
    return {"summary": {"year": year, "tax_date_basis": "settle",
                        **summary},
            "metadata": {"account": "margin"},
            "transactions": rows}


def _disp(rid, date_, symbol, qty, raw_gain, cost=1.0, direction="LONG",
          is_option=False, account="margin", **kw):
    return dict(id=rid, date=date_, date_settle=date_, symbol=symbol,
                qty=qty, raw_gain=raw_gain, gain=raw_gain, cost=cost,
                direction=direction, is_option=is_option,
                account=account, **kw)


class TestUnsettledTradesAreBooked(unittest.TestCase):
    """R1-225: a trade made today settles tomorrow (T+1); it is already
    an acquisition / a loss inside its window."""

    def test_loss_sale_made_today_opens_its_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, [
                _row("2026-08-03", "FTN.TO", 100, 1000.0),
                _row("2026-09-29", "FTN.TO", -100, 800.0,
                     settle="2026-09-30"),
            ], "2026-09-29")
        self.assertEqual(rows["FTN.TO"]["taxable_qty"], 0.0)
        self.assertEqual(rows["FTN.TO"]["category"], "COOLING")

    def test_buy_made_today_locks_the_old_lot(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, [
                _row("2026-01-05", "TRP.TO", 200, 12000.0),
                _row("2026-09-29", "TRP.TO", 200, 11000.0,
                     settle="2026-09-30"),
            ], "2026-09-29")
        self.assertEqual(rows["TRP.TO"]["taxable_qty"], 400.0)
        self.assertEqual(rows["TRP.TO"]["category"], "EXITABLE")

    def test_friday_trade_is_seen_on_the_weekend(self):
        # Traded Friday 2026-09-25, settles Monday 09-28; radar Sunday.
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, [
                _row("2026-08-03", "FTN.TO", 100, 1000.0),
                _row("2026-09-25", "FTN.TO", -100, 800.0,
                     settle="2026-09-28"),
            ], "2026-09-27")
        self.assertEqual(rows["FTN.TO"]["category"], "COOLING")

    def test_trades_after_the_as_of_date_stay_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, [
                _row("2026-01-05", "TRP.TO", 200, 12000.0),
                _row("2026-10-02", "TRP.TO", 200, 11000.0,
                     settle="2026-10-05"),
            ], "2026-09-29")
        self.assertEqual(rows["TRP.TO"]["taxable_qty"], 200.0)
        self.assertEqual(rows["TRP.TO"]["category"], "CLEAR")


class TestEngineDecidesLosses(unittest.TestCase):
    """R1-226: the engine's raw_gain decides whether a sale was a loss."""

    # buy 100@10, sell @8 (denied: bump +200), rebuy @8, sell @9.
    _BUMP = [
        _row("2026-06-01", "XYZ.TO", 100, 1000.0, rid="b1"),
        _row("2026-06-10", "XYZ.TO", -100, 800.0, rid="s1"),
        _row("2026-06-15", "XYZ.TO", 100, 800.0, rid="b2"),
        _row("2026-09-15", "XYZ.TO", -100, 900.0, rid="s2"),
    ]

    def test_bumped_acb_loss_is_a_loss(self):
        g = _gains([_disp("s1", "2026-06-10", "XYZ.TO", 100, -200.0),
                    _disp("s2", "2026-09-15", "XYZ.TO", 100, -100.0)])
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, self._BUMP, "2026-09-29", gains=[g])
        self.assertEqual(rows["XYZ.TO"]["category"], "COOLING")
        self.assertIn("100.0000", rows["XYZ.TO"]["advisory"])

    def test_without_engine_gains_the_pool_fallback_is_kept(self):
        # Documents the fallback (rows the engine does not cover): the
        # radar's own pool sees a +100 gain on the 09-15 sale.
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, self._BUMP, "2026-09-29")
        self.assertEqual(rows["XYZ.TO"]["category"], "")

    def test_rows_outside_the_engine_year_fall_back_to_the_pool(self):
        g = _gains([], year=2025)
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, [
                _row("2026-06-01", "XYZ.TO", 100, 1000.0, rid="b1"),
                _row("2026-09-15", "XYZ.TO", -100, 700.0, rid="s1"),
            ], "2026-09-29", gains=[g])
        self.assertEqual(rows["XYZ.TO"]["category"], "COOLING")

    def test_engine_gain_overrides_a_pool_loss(self):
        # The engine (blended ACB) booked a GAIN: no loss window.
        g = _gains([_disp("s1", "2026-09-15", "XYZ.TO", 100, 50.0)])
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, [
                _row("2026-06-01", "XYZ.TO", 100, 1000.0, rid="b1"),
                _row("2026-09-15", "XYZ.TO", -100, 700.0, rid="s1"),
            ], "2026-09-29", gains=[g])
        self.assertEqual(rows["XYZ.TO"]["category"], "")

    def test_grant_timed_buyback_loss_is_not_a_wash_loss(self):
        opt = "XYZ270115C00010000.TO"
        base = [_row("2026-09-01", opt, -1, 100.0, rid="w1"),
                _row("2026-09-10", opt, 1, 300.0, rid="c1")]
        rows_g = [_disp("w1", "2026-09-01", opt, 1, 100.0, cost=-100.0,
                        direction="SHORT", is_option=True, grant=True),
                  _disp("c1", "2026-09-10", opt, 1, -300.0, cost=0.0,
                        direction="SHORT", is_option=True)]
        off = _gains(rows_g, option_premium_timing="grant",
                     option_buyback_loss_superficial=False)
        on = _gains(rows_g, option_premium_timing="grant",
                    option_buyback_loss_superficial=True)
        with tempfile.TemporaryDirectory() as tmp:
            r_off = _radar(tmp, base, "2026-09-29", gains=[off])
        with tempfile.TemporaryDirectory() as tmp:
            r_on = _radar(tmp, base, "2026-09-29", gains=[on])
        self.assertEqual(r_off.get(opt, {}).get("category", ""), "")
        self.assertEqual(r_on[opt]["category"], "COOLING")


class TestPhantomOpenings(unittest.TestCase):
    """S006-09: phantom-backed positions are longs, not shorts."""

    _ZZZ = [
        _row("2025-01-10", "ZZZ.TO", -100, 3000.0),
        _row("2025-03-03", "ZZZ.TO", 100, 2000.0),
        _row("2025-03-10", "ZZZ.TO", -100, 1500.0),
        _row("2025-03-20", "ZZZ.TO", 100, 1600.0),
    ]

    def test_phantom_opening_turns_the_rebuy_into_a_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, self._ZZZ, "2025-03-25",
                          phantoms=[{"symbol": "ZZZ.TO",
                                     "account": "margin"}])
        self.assertEqual(rows["ZZZ.TO"]["taxable_qty"], 100.0)
        self.assertEqual(rows["ZZZ.TO"]["category"], "VIOLATION")

    def test_phantoms_are_scoped_to_their_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = _radar(tmp, self._ZZZ, "2025-03-25",
                          phantoms=[{"symbol": "ZZZ.TO",
                                     "account": "other"}])
        self.assertEqual(rows["ZZZ.TO"]["taxable_qty"], 0.0)


# ------------------------------------------------------- `taxjson` e2e
_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price):
    gross = abs(qty) * price
    net = -gross if qty > 0 else gross
    return (f"{trade} 10:00:00 AM,{settle} 12:00:00 AM,{action},{sym},"
            f"{sym} DESC,{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,"
            f"55500001,Trades,Individual\n")  # pii-ok


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


def _config(year, accounts):
    t = (f'[settings]\nyear = {year}\ncountry = "canada"\n'
         f'base_currency = "CAD"\nsource_currencies = []\n'
         f'option_grant_timing_since = {year}\n')
    for n, ty in accounts:
        t += f'[accounts.{n}]\ntype = "{ty}"\n'
    return t


def _verdicts(r):
    doc = json.loads(r.stdout)
    return {x["symbol"]: x["verdict"] for x in doc["results"]}


class TestChecksEndToEnd(unittest.TestCase):
    def test_todays_trades_and_engine_losses_reach_the_checks(self):
        today = date.today()
        d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
        # Loss sale TODAY (settles tomorrow); buy TODAY on an old lot;
        # a bump-shaped loss (radar pool: gain; engine: loss) 10 days ago.
        csv = _QT_HEADER + "".join([
            _qt(d(-60), d(-60), "Buy", "FTN.TO", 100, 10.0),
            _qt(d(0), d(1), "Sell", "FTN.TO", -100, 8.0),
            _qt(d(-300), d(-300), "Buy", "TRP.TO", 200, 60.0),
            _qt(d(0), d(1), "Buy", "TRP.TO", 200, 55.0),
            _qt(d(-60), d(-60), "Buy", "BMP.TO", 100, 10.0),
            _qt(d(-50), d(-50), "Sell", "BMP.TO", -100, 8.0),
            _qt(d(-45), d(-45), "Buy", "BMP.TO", 100, 8.0),
            _qt(d(-10), d(-10), "Sell", "BMP.TO", -100, 9.0),
        ])
        # Project year = the year of the bump sale, so the engine's
        # gains file covers it (it is year-scoped).
        year = (today - timedelta(days=10)).year
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                _config(year, [("margin", "taxable")]))
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "qt.csv").write_text(csv)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            b = _cli(root, "buy-check", "FTN.TO", "BMP.TO", "--json")
            s = _cli(root, "sell-check", "TRP.TO", "--json")
            radar = json.loads(
                (root / "reports" / "wash_radar_margin.json").read_text())
        bv = _verdicts(b)
        self.assertEqual(bv["FTN.TO"], "UNSAFE", b.stdout)
        self.assertEqual(bv["BMP.TO"], "UNSAFE", b.stdout)
        self.assertNotEqual(_verdicts(s)["TRP.TO"], "SAFE", s.stdout)
        rows = {x["ticker"]: x for sec in radar["sections"]
                for x in sec["rows"]}
        self.assertEqual(rows["TRP.TO"]["taxable_qty"], 400.0)

    def test_run_reports_apply_phantoms(self):
        tt = ("BUYSELL 2025-01-10 10:00:00 ZZZ.TO -100 CAD 30.00 3000.00 0.00\n"
              "BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 20.00 2000.00 0.00\n"
              "BUYSELL 2025-03-10 10:00:00 ZZZ.TO -100 CAD 15.00 1500.00 0.00\n"
              "BUYSELL 2025-03-20 10:00:00 ZZZ.TO 100 CAD 16.00 1600.00 0.00\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                _config(2025, [("margin", "taxable")]))
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "m.tt").write_text(tt)
            (root / "phantoms.json").write_text(
                '[{"symbol": "ZZZ.TO", "account": "margin"}]')
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            radar = json.loads(
                (root / "reports" / "wash_radar_margin.json").read_text())
            w = _cli(root, "wash-radar", "--date", "2025-03-25", "--json")
        rows = {x["ticker"]: x for sec in radar["sections"]
                for x in sec["rows"]}
        self.assertEqual(rows["ZZZ.TO"]["taxable_qty"], 100.0)
        wrows = {x["ticker"]: x for sec in json.loads(w.stdout)["sections"]
                 for x in sec["rows"]}
        self.assertEqual(wrows["ZZZ.TO"]["category"], "VIOLATION")


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------ web what-if
def _web_project(tmp, txs, ticker_map=None, settings_extra=""):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "reports").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\n' + settings_extra +
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": txs}))
    if ticker_map is not None:
        (root / "ticker.map").write_text(ticker_map)
    from taxjson.web.context import ProjectContext
    return ProjectContext.load(root)


def _buy(symbol, qty, price, net, date_="2026-03-02"):
    return {"action": "BUYSELL", "date": date_, "symbol": symbol,
            "quantity": qty, "price": price, "net_amount": net,
            "currency": "CAD", "account": "margin"}


class TestWhatIfOptions(unittest.TestCase):
    def test_option_sale_applies_the_contract_multiplier(self):
        # R1-227: 1 call bought @5 (cost 500); what-if sell 1 @4.00
        # is 400 of proceeds, a 100 loss — as the engine books it.
        from taxjson.web import data
        opt = "ABC270115C00050000.TO"
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _web_project(tmp, [_buy(opt, 1, 5.0, 500.0)])
            r = data.what_if_sell(ctx, "margin", opt, 1, 4.0,
                                  on="2026-06-30")
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["proceeds"], 400.0)
        self.assertAlmostEqual(r["economic_gain"], -100.0)
        self.assertEqual(r.get("multiplier"), 100)

    def test_shares_are_unchanged(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _web_project(tmp, [_buy("ABC.TO", 2, 5.0, 10.0)])
            r = data.what_if_sell(ctx, "margin", "ABC.TO", 2, 4.0,
                                  on="2026-06-30")
        self.assertAlmostEqual(r["proceeds"], 8.0)
        self.assertEqual(r.get("multiplier"), 1)

    def test_futures_option_is_refused_not_mispriced(self):
        from taxjson.web import data
        fop = "F:CL270115C00060000.US"
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _web_project(tmp, [_buy(fop, 1, 2.0, 2000.0)])
            r = data.what_if_sell(ctx, "margin", fop, 1, 2.5,
                                  on="2026-06-30")
        self.assertFalse(r["ok"])
        self.assertIn("multiplier", r["reason"])

    def test_cross_listed_option_follows_its_underlying(self):
        # S022-02: TOBASE AEM.US -> AEM.TO; the holdings view links the
        # .US option, the book holds it as .TO.
        from taxjson.web import data
        held = "AEM270115C00150000.TO"
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _web_project(tmp, [_buy(held, 2, 8.0, 1600.0)],
                               ticker_map="TOBASE AEM.US AEM.TO\n")
            r = data.what_if_sell(ctx, "margin", "AEM270115C00150000.US",
                                  2, 6.0, on="2026-06-30")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["symbol"], held)
        self.assertAlmostEqual(r["cost_basis"], 1600.0)
        self.assertAlmostEqual(r["economic_gain"], -400.0)

    def test_unheld_option_is_refused_not_simulated_as_a_write(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _web_project(
                tmp, [_buy("AEM.TO", 100, 50.0, 5000.0)],
                settings_extra="option_premium_timing = \"grant\"\n"
                               "option_grant_timing_since = 2026\n")
            r = data.what_if_sell(ctx, "margin", "AEM270115C00150000.TO",
                                  1, 1.0, on="2026-06-30")
        self.assertFalse(r["ok"], r)
