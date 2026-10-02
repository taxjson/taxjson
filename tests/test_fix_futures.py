"""Fix round (futures area): futures booked on their settlement P/L, T1135
futures cost and phantoms, and a transient Bank of Canada 404 that must
not switch a currency to Yahoo for good.

Findings: R1-0 / R1-52 / R1-204 (futures notional FX), S008-04 (T1135
futures notional), R1-321 (T1135 ignores phantoms.json), R1-145 (sticky
Valet 404). Every fetcher is stubbed — no network."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from taxjson.bin import to_base_curr as T
from tax_rules import rule

TODAY = "2026-09-28"                                   # a Monday


def _weekdays(a, b):
    from datetime import date
    out, d = [], a
    while d <= b:
        if date.fromisoformat(d).weekday() < 5:
            out.append(d)
        d = T._shift(d, 1)
    return out


# ------------------------------------------------------------ R1-145

class _FxCase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        p = mock.patch.object(T, "CACHE_FILE",
                              str(Path(self.td.name) / "fx.json"))
        p.start()
        self.addCleanup(p.stop)
        self.boc_calls = []
        self.boc_fail = None

    def boc(self, cur, a, b):
        self.boc_calls.append((cur, a, b))
        if self.boc_fail:
            raise self.boc_fail
        return {d: "1.3500" for d in _weekdays(a, b) if d < self.today}

    def yahoo(self, ticker, a, b):
        return {d: 1.2 for d in _weekdays(a, b) if d < self.today}

    today = TODAY

    def build(self, start="2025-01-01", end="2025-01-10", offline=False):
        return T.build_rates("USD", "CAD", start, end, today=self.today,
                             offline=offline, fetch_boc_fn=self.boc,
                             fetch_yahoo_fn=self.yahoo)


def _http_error(code, body, ctype="text/html"):
    return urllib.error.HTTPError(
        "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json",
        code, "Not Found", {"Content-Type": ctype}, io.BytesIO(body))


class TestValet404IsNotSticky(_FxCase):
    def test_html_404_is_a_failed_fetch_not_series_not_found(self):
        err = _http_error(404, b"<html><body>Maintenance</body></html>")
        with mock.patch.object(T.urllib.request, "urlopen",
                               side_effect=err):
            with self.assertRaises(Exception) as cm:
                T.fetch_boc("USD", "2025-01-01", "2025-01-10")
        self.assertNotIsInstance(cm.exception, T.SeriesNotFound)

    def test_valet_json_not_found_is_definitive(self):
        body = json.dumps({"message": "Series FXUSDCAD not found.",
                           "docs": "https://www.bankofcanada.ca/valet"
                           }).encode()
        err = _http_error(404, body, "application/json")
        with mock.patch.object(T.urllib.request, "urlopen",
                               side_effect=err):
            with self.assertRaises(T.SeriesNotFound):
                T.fetch_boc("USD", "2025-01-01", "2025-01-10")

    def test_transient_failure_never_switches_to_yahoo(self):
        # Run 1 healthy, run 2 one transient failure, run 3 healthy.
        rows, _, _ = self.build()
        self.assertTrue(rows and all(s == "boc" for _d, _v, s in rows))
        self.boc_fail = OSError("HTTP Error 404: Not Found (proxy)")
        rows, errors, _ = self.build(end="2025-01-20")
        self.assertTrue(errors)
        self.assertTrue(all(s == "boc" for _d, _v, s in rows))
        self.boc_fail = None
        rows, errors, _ = self.build(end="2025-01-20")
        self.assertEqual(errors, [])
        self.assertTrue(rows and all(s == "boc" for _d, _v, s in rows))
        blk = json.loads(Path(T.CACHE_FILE).read_text())["_boc"]["USDCAD"]
        self.assertNotIn("not_published", blk)

    def test_definitive_not_found_is_rechecked_after_a_while(self):
        self.boc_fail = T.SeriesNotFound("FXUSDCAD")
        self.build()
        blk = json.loads(Path(T.CACHE_FILE).read_text())["_boc"]["USDCAD"]
        self.assertTrue(blk.get("not_published"))
        self.assertEqual(blk.get("not_published_checked"), TODAY)
        # Same day: honoured, not re-asked.
        self.boc_calls.clear()
        self.build()
        self.assertEqual(self.boc_calls, [])
        # A week later the series is back: re-probed, marker cleared,
        # the Bank's rates are used again.
        self.boc_fail = None
        self.today = T._shift(TODAY, T.NOT_PUBLISHED_RECHECK_DAYS)
        rows, _, notes = self.build()
        self.assertTrue(self.boc_calls)
        self.assertTrue(rows and all(s == "boc" for _d, _v, s in rows))
        blk = json.loads(Path(T.CACHE_FILE).read_text())["_boc"]["USDCAD"]
        self.assertNotIn("not_published", blk)

    def test_legacy_undated_marker_is_reprobed(self):
        # A cache written by the old code: a transient 404 marked the
        # series unpublished forever, with 319 good observations kept.
        self.build()                               # healthy → obs cached
        cache = json.loads(Path(T.CACHE_FILE).read_text())
        cache["_boc"]["USDCAD"]["not_published"] = True
        Path(T.CACHE_FILE).write_text(json.dumps(cache))
        self.boc_calls.clear()
        rows, _, _ = self.build(end="2025-01-20")
        self.assertTrue(self.boc_calls)
        self.assertTrue(rows and all(s == "boc" for _d, _v, s in rows))

    def test_cached_bank_rates_keep_their_source_under_a_marker(self):
        self.build()                               # obs for 2025-01-01..10
        cache = json.loads(Path(T.CACHE_FILE).read_text())
        cache["_boc"]["USDCAD"]["not_published"] = True
        cache["_boc"]["USDCAD"]["not_published_checked"] = TODAY
        Path(T.CACHE_FILE).write_text(json.dumps(cache))
        rows, _, notes = self.build(end="2025-01-20", offline=True)
        src = {d: s for d, _v, s in rows}
        self.assertEqual(src["2025-01-06"], "boc")
        self.assertTrue(any("FXUSDCAD" in n and "not found" in n
                            for n in notes), notes)


# ------------------------------------------------------------ T1135

def _tx(action="BUYSELL", date="2025-01-15", symbol="AAA.US", qty=0.0,
        net=0.0, account="IB", **extra):
    d = {"action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": symbol, "quantity": qty,
         "net_amount": net, "currency": "CAD", "account": account}
    d.update(extra)
    return d


@rule("CA-RPT-12")
class TestT1135Futures(unittest.TestCase):
    """S008-04: a plain futures contract has a nil cost amount."""

    def test_long_future_notional_is_not_cost(self):
        from taxjson.bin.taxjson_t1135 import walk_costs
        txs = [_tx(date="2025-01-10", qty=100, net=30000.0),
               _tx(date="2025-10-20", symbol="F:CLZ5.US", qty=1,
                   net=80569.97, price=80.56)]
        w = walk_costs(txs, 2025, {})
        self.assertAlmostEqual(w["max_total_cost"], 30000.0, places=2)
        self.assertEqual(w["per_symbol"].get("F:CLZ5.US", {}).get(
            "max_cost", 0.0), 0.0)

    def test_futures_option_premium_still_counts(self):
        from taxjson.bin.taxjson_t1135 import walk_costs
        txs = [_tx(date="2025-03-01", symbol="F:CL251117C00070000.US",
                   qty=1, net=2500.0)]
        w = walk_costs(txs, 2025, {})
        self.assertAlmostEqual(w["max_total_cost"], 2500.0, places=2)

    def test_report_lists_future_with_nil_cost_and_note(self):
        from taxjson.bin.taxjson_t1135 import build_report, render_report
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                _tx(date="2025-01-10", qty=100, net=30000.0),
                _tx(date="2025-10-20", symbol="F:CLZ5.US", qty=1,
                    net=80569.97)]}))
            gains = Path(td) / "margin_gains.json"
            gains.write_text(json.dumps({"transactions": [
                {"symbol": "F:CLZ5.US", "date": "2025-10-23",
                 "gain": 6052.34}]}))
            rep = build_report([base], [gains], 2025, {}, "CAD")
        self.assertFalse(rep["filing_required"])
        row = {r["symbol"]: r for r in rep["properties"]}["F:CLZ5.US"]
        self.assertEqual(row["max_cost"], 0.0)
        self.assertTrue(row["futures"])
        self.assertIn("futures", render_report(rep))


@rule("CA-RPT-12", "CA-ACB-11")
class TestT1135Phantoms(unittest.TestCase):
    """R1-321: `taxjson t1135` applies phantoms.json like the gains pass."""

    def _books(self, td):
        base = Path(td) / "margin_base.json"
        # A sale with no history (cut-off books), then a real purchase.
        base.write_text(json.dumps({"transactions": [
            _tx(date="2023-05-01", qty=-100, net=900.0),
            _tx(date="2024-03-01", qty=150, net=1500.0)]}))
        phantoms = Path(td) / "phantoms.json"
        phantoms.write_text(json.dumps([
            {"symbol": "AAA.US", "account": "IB"}]))
        return base, phantoms

    def test_phantom_opening_restores_real_purchase_cost(self):
        from taxjson.bin.taxjson_t1135 import build_report
        with tempfile.TemporaryDirectory() as td:
            base, phantoms = self._books(td)
            without = build_report([base], [], 2024, {}, "CAD")
            with_ph = build_report([base], [], 2024, {}, "CAD",
                                   phantoms=phantoms)
        row0 = {r["symbol"]: r for r in without["properties"]}["AAA.US"]
        row1 = {r["symbol"]: r for r in with_ph["properties"]}["AAA.US"]
        # The bug: without the phantom the 2024 buy covers a fake short
        # and only 50 shares stay (their 500, plus the 50 the engine
        # denies on the short cover and adds back — S008-07: the walk
        # carries the engine's s.53(1)(f) additions).
        self.assertAlmostEqual(row0["max_cost"], 550.0, places=2)
        self.assertAlmostEqual(row1["max_cost"], 1500.0, places=2)
        self.assertAlmostEqual(row1["year_end_cost"], 1500.0, places=2)
        self.assertFalse(row1["unknown_acb"])     # the phantom drained

    def test_phantom_still_held_is_flagged(self):
        from taxjson.bin.taxjson_t1135 import build_report
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                _tx(date="2023-01-10", qty=10, net=100.0),
                _tx(date="2024-05-01", qty=-100, net=900.0)]}))
            phantoms = Path(td) / "phantoms.json"
            phantoms.write_text(json.dumps([
                {"symbol": "AAA.US", "account": "IB"}]))
            rep = build_report([base], [], 2023, {}, "CAD",
                               phantoms=phantoms)
        self.assertIn("AAA.US", rep["unknown_acb_symbols"])

    def test_cli_and_wrapper_pass_phantoms(self):
        import argparse
        from contextlib import redirect_stderr, redirect_stdout
        from taxjson.bin.taxjson_run import cmd_t1135
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work").mkdir()
            base, _ph = self._books(root / "work")
            (root / "work" / "phantoms.json").rename(root / "phantoms.json")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    cmd_t1135(argparse.Namespace(dir=str(root), json=True))
            self.assertEqual(cm.exception.code, 0, err.getvalue())
            rep = json.loads(out.getvalue())
        row = {r["symbol"]: r for r in rep["properties"]}["AAA.US"]
        self.assertAlmostEqual(row["max_cost"], 1500.0, places=2)


# ------------------------------------------------------------ R1-0/52/204

from decimal import Decimal  # noqa: E402

from taxjson.lib.core import TaxTransaction, get_tax_rules  # noqa: E402
from taxjson.lib.futures import (  # noqa: E402
    FUTURES_SETTLEMENT, settle_futures, is_plain_future)


def _fut(date, qty, net, price, symbol="F:CLZ5.US", fee=2.00,
         cur="USD", time="10:00:00", **kw):
    return TaxTransaction(
        action="BUYSELL", date=date, date_settle=date, time=time,
        symbol=symbol, quantity=qty, currency=cur, price=price, fee=fee,
        net_amount=net, gross_amount=abs(qty) * price * 1000,
        account="IB", **kw)


# A synthetic CL round trip: 1 CL bought (notional 50,000 + 2.00
# commission), sold (54,000 - 2.00). The broker's realized P/L is
# 3,996.00 USD; a T5008 would show the P/L as proceeds, cost 0.
CL_OPEN = ("2025-10-06", 1.0, 50002.00, 50.00)
CL_CLOSE = ("2025-10-09", -1.0, 53998.00, 54.00)
RATES = {"USD": {"2025-10-06": Decimal("1.3900"),
                 "2025-10-09": Decimal("1.3800"),
                 "2025-11-03": Decimal("1.4000"),
                 "2025-11-10": Decimal("1.3500")}}


def _convert(rows, to="CAD", country="canada"):
    from taxjson.bin.taxjson_convert_currency import (
        process_transactions, reset_fallback_tally)
    reset_fallback_tally()
    return process_transactions(rows, to, RATES, Decimal("1.35"),
                                country=country)


def _gains(rows):
    res = get_tax_rules("canada").compute_gains(rows)
    return [g for g in res["transactions"]
            if g.get("qty") and "gain" in g]


class TestSettleFutures(unittest.TestCase):
    @rule("CA-FX-06")
    def test_plain_future_vs_option_on_future(self):
        self.assertTrue(is_plain_future("F:CLZ5.US"))
        self.assertTrue(is_plain_future("/ESZ5"))
        self.assertFalse(is_plain_future("F:CL251117C00070000.US"))
        self.assertFalse(is_plain_future("AAPL.US"))

    @rule("CA-FX-04")
    def test_open_carries_nothing_close_carries_native_pl(self):
        rows, st = settle_futures([_fut(*CL_OPEN), _fut(*CL_CLOSE)], "average")
        self.assertEqual([r.type for r in rows], [FUTURES_SETTLEMENT] * 2)
        self.assertEqual(rows[0].net_amount, 0.0)
        self.assertAlmostEqual(rows[1].net_amount, 3996.00, places=6)
        self.assertEqual(st["closes"], 1)

    def test_crossing_fill_is_split_and_short_close_is_signed(self):
        f = lambda d, q, n, t: _fut(d, q, n, n / abs(q) / 1000,  # noqa
                                    symbol="F:XXZ5.US", fee=0.0, time=t)
        rows, st = settle_futures([
            f("2025-11-03", 1.0, 100.0, "10:00:00"),
            f("2025-11-03", -2.0, 220.0, "11:00:00"),   # close 1, short 1
            f("2025-11-10", 1.0, 105.0, "10:00:00")],  # cover at a gain
            "average")
        self.assertEqual(st["split"], 1)
        self.assertEqual([r.quantity for r in rows], [1.0, -1.0, -1.0, 1.0])
        self.assertEqual([round(r.net_amount, 6) for r in rows],
                         [0.0, 10.0, 0.0, 5.0])
        # The closing part keeps the fill's id; the opening part is new.
        self.assertNotEqual(rows[1].id, rows[2].id)

    def test_other_actions_on_a_future_are_refused(self):
        bad = TaxTransaction(action="OPENING_BALANCE", date="2025-01-02",
                             symbol="F:CLZ5.US", quantity=1.0,
                             currency="USD", net_amount=57000.0)
        with self.assertRaises(ValueError) as cm:
            settle_futures([bad], "average")
        self.assertIn("F:CLZ5.US", str(cm.exception))

    def test_idempotent(self):
        once, _ = settle_futures([_fut(*CL_OPEN), _fut(*CL_CLOSE)], "average")
        twice, _ = settle_futures(once, "average")
        self.assertEqual([r.to_dict() for r in once],
                         [r.to_dict() for r in twice])


class TestFuturesGainAtCloseRate(unittest.TestCase):
    @rule("CA-FX-04")
    def test_long_gain_is_native_pl_at_the_closing_rate(self):
        conv = _convert([_fut(*CL_OPEN), _fut(*CL_CLOSE)])
        g = _gains(conv)
        self.assertEqual(len(g), 1)
        # 3,996.00 x 1.38 — not 53,998.00 x 1.38 - 50,002.00 x
        # 1.39 = 5,014.46 (FX on a notional never paid).
        self.assertAlmostEqual(g[0]["gain"], 3996.00 * 1.38, places=2)

    def test_short_gain_is_native_pl_at_the_closing_rate(self):
        es = lambda d, q, n: _fut(d, q, n, n / 50, symbol="F:ESZ5.US",  # noqa
                                  fee=2.0)
        conv = _convert([es("2025-11-03", -1.0, 299998.0),
                         es("2025-11-10", 1.0, 295002.0)])
        g = _gains(conv)
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0]["gain"], 4996.0 * 1.35, places=2)

    def test_loss_and_crossing_totals(self):
        f = lambda d, q, n, t: _fut(d, q, n, n / abs(q) / 1000,  # noqa
                                    symbol="F:XXZ5.US", fee=0.0, time=t)
        conv = _convert([f("2025-11-03", 1.0, 100.0, "10:00:00"),
                         f("2025-11-03", -2.0, 220.0, "11:00:00"),
                         f("2025-11-10", 1.0, 115.0, "10:00:00")])
        g = sorted(_gains(conv), key=lambda e: e["date"])
        self.assertAlmostEqual(g[0]["gain"], 10 * 1.40, places=6)
        self.assertAlmostEqual(g[1]["gain"], -5 * 1.35, places=6)

    def test_futures_options_untouched_and_no_country_refused(self):
        fop = _fut("2025-10-06", 1.0, 2500.0, 2.5,
                   symbol="F:CL251117C00070000.US")
        conv = _convert([fop])
        self.assertEqual(conv[0].type, "")
        self.assertAlmostEqual(conv[0].net_amount, 2500.0 * 1.39, places=6)
        # The settlement basis no longer depends on a CAD target
        # (partition ENGINE-02): a book with futures needs the country.
        with self.assertRaises(ValueError) as cm:
            _convert([_fut(*CL_OPEN), _fut(*CL_CLOSE)], to="USD",
                     country=None)
        self.assertIn("--country", str(cm.exception))


class TestFuturesScheduleThree(unittest.TestCase):
    @rule("CA-FX-05")
    def test_line6_shows_pl_not_notional(self):
        from taxjson.bin.taxjson_form_export import build_schedule3
        entries = _gains(_convert([_fut(*CL_OPEN), _fut(*CL_CLOSE)]))
        loss = _gains(_convert([
            _fut("2025-11-03", 1.0, 60000.0, 60.0, symbol="F:CLF6.US"),
            _fut("2025-11-10", -1.0, 59000.0, 59.0, symbol="F:CLF6.US")]))
        rep = build_schedule3(entries + loss, 2025)
        rows = {r["symbol"]: r for r in rep["rows"]}
        cl = rows["F:CLZ5.US"]
        self.assertAlmostEqual(cl["proceeds"], 3996.00 * 1.38, places=2)
        self.assertEqual(cl["acb"], 0.0)
        self.assertEqual(cl["outlays"], 0.0)
        self.assertAlmostEqual(cl["gain"], 3996.00 * 1.38, places=2)
        lo = rows["F:CLF6.US"]
        self.assertEqual(lo["proceeds"], 0.0)
        self.assertAlmostEqual(lo["acb"], 1350.0, places=2)
        self.assertAlmostEqual(lo["gain"], -1350.0, places=2)
        self.assertEqual(cl["line"], "6")
        self.assertIn("settled P/L", cl["notes"])


class TestFuturesAudit(unittest.TestCase):
    def test_settled_pl_is_rederived_from_the_broker_rows(self):
        from taxjson.bin.taxjson_audit import futures_native_nets
        raw = [_fut(*CL_OPEN), _fut(*CL_CLOSE)]
        base = _convert(raw)
        source_index = {r.id: [{"label": "ib.csv", "row": r.to_dict()}]
                        for r in raw}
        base_index = {r.id: r.to_dict() for r in base}
        nets = futures_native_nets(source_index, base_index, "canada")
        self.assertAlmostEqual(nets[raw[1].id], 3996.00, places=6)
        self.assertAlmostEqual(nets[raw[1].id] * 1.38,
                               base_index[raw[1].id]["net_amount"],
                               places=6)


class TestFuturesFxCash(unittest.TestCase):
    def test_only_the_settled_pl_moves_usd(self):
        from taxjson.bin.taxjson_fx_cash import build_ledger
        rows = [r.to_dict() for r in (_fut(*CL_OPEN), _fut(*CL_CLOSE))]
        rates = {"2025-10-06": 1.39, "2025-10-09": 1.38}
        led = build_ledger(rows, "CAD", {}, 2025,
                           rate_of=lambda c, d: rates.get(d),
                           country="canada")
        usd = led["per_currency"]["USD"]
        self.assertAlmostEqual(usd["acquired"], 3996.00, places=2)
        self.assertAlmostEqual(usd["disposed"], 0.0, places=2)


if __name__ == "__main__":
    unittest.main()
