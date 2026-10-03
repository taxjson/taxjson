"""Re-audit-2 planning fixes: `taxjson harvest` and the price chain.

Offline throughout: prices come from injected fetchers (the price
chain's `fetchers` test hook) or a seeded price cache, never the
network. Synthetic books only.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from tax_rules import rule, rule_absent

from taxjson.bin.taxjson_harvest import _recovery_schedule
from taxjson.bin.taxjson_harvest import main as harvest_main

TODAY = date.today()


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _write(path: Path, doc) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _gains(inventory, year=None):
    return {"summary": {"year": year or TODAY.year}, "transactions": [],
            "inventory": inventory}


def _run(argv, fetchers=None, option_fetchers=None, env=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env or {}), \
            redirect_stdout(out), redirect_stderr(err):
        rc = harvest_main(argv, fetchers=fetchers,
                          option_fetchers=option_fetchers)
    return rc, out.getvalue(), err.getvalue()


def _json_rows(out):
    return json.loads(out)


class TestDeclaredContractSize(unittest.TestCase):
    """A2-0367 / A2-1177: harvest --options values an option at the
    contract size its rows declared (the inventory's `multiplier`), as
    `taxjson list` and the holdings export do; 100 only when none is
    declared."""

    def _book(self, td, inv):
        return _write(Path(td) / "margin_gains_wash.json", _gains(inv))

    def test_declared_x10_contract(self):
        sym = "XYZ261218C00050000.TO"
        with tempfile.TemporaryDirectory() as td:
            g = self._book(td, [{"symbol": sym, "qty": 5,
                                 "total_cost": 100.0, "multiplier": 10.0,
                                 "position_start_date": _iso(-20)}])
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--options",
                 "--json"],
                fetchers=[lambda rem: {}],
                option_fetchers=[lambda rem: {s: (2.0, "ibkr")
                                              for s in rem}])
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["multiplier"], 10.0)
        self.assertAlmostEqual(row["value"], 100.0)
        self.assertAlmostEqual(row["unrealized"], 0.0)
        self.assertEqual(row["verdict"], "FLAT")

    def test_text_cost_per_share_uses_declared_size(self):
        sym = "XYZ261218C00050000.TO"
        with tempfile.TemporaryDirectory() as td:
            g = self._book(td, [{"symbol": sym, "qty": 2,
                                 "total_cost": 30.0, "multiplier": 10.0,
                                 "position_start_date": _iso(-20)}])
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--options"],
                fetchers=[lambda rem: {}],
                option_fetchers=[lambda rem: {s: (2.10, "ibkr")
                                              for s in rem}])
        self.assertEqual(rc, 0, err)
        line = next(ln for ln in out.splitlines() if sym in ln)
        self.assertIn("1.5000", line)          # 30 / (2 x 10)
        self.assertIn("12.00", line)           # 2 x 2.10 x 10 - 30
        self.assertNotIn("390.00", line)

    def test_undeclared_equity_option_stays_x100(self):
        sym = "XYZ261218C00050000.TO"
        with tempfile.TemporaryDirectory() as td:
            g = self._book(td, [{"symbol": sym, "qty": 1,
                                 "total_cost": 200.0,
                                 "position_start_date": _iso(-20)}])
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--options",
                 "--json"],
                fetchers=[lambda rem: {}],
                option_fetchers=[lambda rem: {s: (2.0, "ibkr")
                                              for s in rem}])
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["multiplier"], 100.0)
        self.assertAlmostEqual(row["value"], 200.0)


class TestPastDeadlineViolation(unittest.TestCase):
    """A2-0365: a VIOLATION whose rescue deadline has passed is not
    'harvestable now' — a registered buy still inside the 30 days
    before a sale today denies it; it is claimable once that buy ages
    out (the radar's own LOCKED rule: last add + 31 days)."""

    def _row(self, clears, sh_add=None, sh_known=True):
        return {"verdict": "LOSS", "unrealized": -900.0,
                "radar": {"category": "VIOLATION", "clears_at": clears},
                "sheltered_last_add": sh_add,
                "sheltered_known": sh_known}

    def test_deadline_passed_with_recent_sheltered_buy_waits(self):
        # Registered buy 29 days ago: clear 2 days from now.
        s = _recovery_schedule([self._row(_iso(-1), _iso(-29))])
        self.assertEqual(s["now"], 0.0)
        self.assertEqual(s["7d"], 900.0)

    def test_deadline_passed_buy_aged_out_is_now(self):
        s = _recovery_schedule([self._row(_iso(-5), _iso(-40))])
        self.assertEqual(s["now"], 900.0)

    def test_deadline_passed_unknown_sheltered_side_is_no_clear(self):
        s = _recovery_schedule([self._row(_iso(-1), None, sh_known=False)])
        self.assertEqual(s["now"], 0.0)
        self.assertEqual(s["no_clear"], 900.0)

    def test_open_deadline_still_now(self):
        # Before the deadline a full exit rescues it: claimable now.
        s = _recovery_schedule([self._row(_iso(3), _iso(-10))])
        self.assertEqual(s["now"], 900.0)

    def test_cli_footer_not_now(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _write(root / "margin_gains_wash.json", _gains([
                {"symbol": "XYZ.TO", "qty": 100, "total_cost": 2000.0,
                 "position_start_date": _iso(-120),
                 "last_acq_date": _iso(-120)}]))
            sh = _write(root / "tfsa_gains_wash.json", _gains([
                {"symbol": "XYZ.TO", "qty": 100, "total_cost": 1100.0,
                 "position_start_date": _iso(-29),
                 "last_acq_date": _iso(-29),
                 "last_acq_settle": _iso(-29)}]))
            radar = _write(root / "wash_radar_margin.json", {"sections": [
                {"title": "act", "rows": [{
                    "ticker": "XYZ.TO", "category": "VIOLATION",
                    "advisory": "VIOLATION: Sell ...",
                    "clears_at": _iso(-1)}]}]})
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--radar", str(radar), "--sheltered", str(sh)],
                fetchers=[lambda rem: {s: (11.0, "fake", "CAD")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        h = _json_rows(out)["totals"]["harvestable"]
        self.assertEqual(h["now"], 0.0)
        self.assertEqual(h["7d"], 900.0)


class TestCryptoTickerMap(unittest.TestCase):
    """A2-0364: harvest quotes a coin under the project's
    ticker.map CRYPTO spelling — the one the books were priced with."""

    def test_project_map_spelling_is_asked(self):
        asked = {}

        def fetch(rem):
            asked.update(rem)
            return {s: (100.0, "fake", "USD") for s in rem}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "ticker.map").write_text("CRYPTO FOO FOO123\n")
            g = _write(root / "work" / "crypto_gains_wash.json", _gains([
                {"symbol": "FOO", "qty": 1, "total_cost": 135.0,
                 "position_start_date": _iso(-50)}]))
            (root / "work" / "to_base.csv").write_text(
                f"{TODAY.isoformat()} 12:00:00 USD CAD 1.35\n")
            rc, out, err = _run([str(g), "--no-ibkr", "--country",
                                 "canada", "--json"], fetchers=[fetch])
        self.assertEqual(rc, 0, err)
        self.assertEqual(asked, {"FOO": "FOO123-USD"})

    def test_builtin_override_still_applies(self):
        from taxjson.lib.price_chain import yf_symbol_for
        self.assertEqual(yf_symbol_for("UNI"), "UNI7083-USD")
        self.assertEqual(yf_symbol_for("FOO", {"FOO": "FOO123"}),
                         "FOO123-USD")


class TestMinorUnitQuotes(unittest.TestCase):
    """A2-0379 / A2-0692: a quote for an LSE line (.L, quoted in pence
    or pounds) that does not state its unit is never valued as pounds:
    a live tier's unit-less hit is passed over, and a unit-less cache
    entry (written before S077-04) leaves the row out loudly."""

    def _book(self, root):
        (root / "to_base.csv").write_text(
            f"{TODAY.isoformat()} 12:00:00 GBP CAD 1.85\n")
        return _write(root / "margin_gains_wash.json", _gains([
            {"symbol": "VOD.L", "qty": 1000, "total_cost": 1295.0,
             "position_start_date": _iso(-200)}]))

    def test_unit_less_live_quote_is_not_used(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._book(Path(td))
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                fetchers=[lambda rem: {s: (70.0, "fake-yf")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        self.assertEqual(_json_rows(out)["rows"], [])
        self.assertIn("VOD.L", err)
        self.assertIn("pence", err)
        # Never cached as if it were a verified quote.
        self.assertFalse((Path(td) / ".price_cache.json").exists()
                         and "VOD.L" in (Path(td) / ".price_cache.json")
                         .read_text())

    def test_unit_less_live_quote_falls_through_to_next_tier(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._book(Path(td))
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                fetchers=[lambda rem: {s: (70.0, "ibkr") for s in rem},
                          lambda rem: {s: (0.70, "yfinance", "GBP")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["price_source"], "yfinance")
        self.assertAlmostEqual(row["value"], 1295.0)

    def test_pre_fix_cache_entry_without_currency_is_omitted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = self._book(root)
            _write(root / ".price_cache.json", {"VOD.L": {
                "price": 70.0, "asof": TODAY.isoformat(),
                "source": "yfinance"}})
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                env={"TAXJSON_OFFLINE": "1"})
        self.assertEqual(rc, 0, err)
        self.assertEqual(_json_rows(out)["rows"], [])
        self.assertIn("VOD.L", err)

    def test_cache_entry_with_currency_is_used(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = self._book(root)
            _write(root / ".price_cache.json", {"VOD.L": {
                "price": 0.70, "asof": TODAY.isoformat(),
                "source": "yfinance", "currency": "GBP"}})
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json"],
                env={"TAXJSON_OFFLINE": "1"})
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertAlmostEqual(row["value"], 1295.0)


class TestCachedCurrencyValidated(unittest.TestCase):
    """A2-1170: a cached quote's currency is checked like its price:
    stripped and upper-cased; a non-string is ignored with a warning
    (the symbol's own spelling then decides)."""

    def _go(self, cur):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "to_base.csv").write_text(
                f"{TODAY.isoformat()} 12:00:00 USD CAD 1.25\n")
            g = _write(root / "margin_gains_wash.json", _gains([
                {"symbol": "AAPL.US", "qty": 10, "total_cost": 3000.0,
                 "position_start_date": _iso(-200)}]))
            _write(root / ".price_cache.json", {"AAPL.US": {
                "price": 200.0, "asof": TODAY.isoformat(),
                "source": "yfinance", "currency": cur}})
            return _run([str(g), "--no-ibkr", "--country", "canada",
                         "--json"], env={"TAXJSON_OFFLINE": "1"})

    def test_lower_case_currency_is_normalised(self):
        rc, out, err = self._go(" usd ")
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["price_currency"], "USD")
        self.assertAlmostEqual(row["value"], 2500.0)

    def test_non_string_currency_does_not_crash(self):
        for bad in (["x"], {"a": 1}, 5):
            rc, out, err = self._go(bad)
            self.assertEqual(rc, 0, err)
            self.assertNotIn("Traceback", err)
            self.assertIn("currency", err)
            row = _json_rows(out)["rows"][0]
            self.assertEqual(row["price_currency"], "USD")


class TestBaseCurrencyFlagTrimmed(unittest.TestCase):
    """A2-1169: --base-currency is stripped and upper-cased on entry in
    harvest, t1135, form-export and carryover, as the config path is."""

    def test_harvest(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "margin_gains_wash.json", _gains([
                {"symbol": "XYZ.TO", "qty": 10, "total_cost": 100.0,
                 "position_start_date": _iso(-200)}]))
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "canada", "--json",
                 "--base-currency", " cad"],
                fetchers=[lambda rem: {s: (9.0, "fake", "CAD")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        doc = _json_rows(out)
        self.assertEqual(len(doc["rows"]), 1)
        self.assertEqual(doc["totals"]["currency"], "CAD")

    def _base(self, td):
        return _write(Path(td) / "margin_base.json", {
            "metadata": {"target_currency": "CAD"},
            "transactions": [{
                "date": f"{TODAY.year}-01-05",
                "date_settle": f"{TODAY.year}-01-06",
                "symbol": "XYZ.TO", "action": "BUY", "quantity": 10,
                "price": 10.0, "net_amount": -100.0, "currency": "CAD",
                "fees": 0.0}]})

    def test_t1135(self):
        from taxjson.bin.taxjson_t1135 import main as t1135_main
        with tempfile.TemporaryDirectory() as td:
            b = self._base(td)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = t1135_main(["--year", str(TODAY.year),
                                 "--base-currency", " CAD", str(b)])
        self.assertNotIn("not  CAD", err.getvalue())
        self.assertNotIn("--base-currency", err.getvalue())
        self.assertEqual(rc, 0, err.getvalue())

    def test_form_export(self):
        from taxjson.bin.taxjson_form_export import main as fe_main
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "margin_gains_wash.json", {
                "summary": {"year": TODAY.year},
                "metadata": {"target_currency": "CAD"},
                "transactions": [], "inventory": []})
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = fe_main(["--form", "schedule3", "--country", "ca",
                              "--year", str(TODAY.year),
                              "--base-currency", " cad", str(g)])
        self.assertNotIn("another currency", err.getvalue())
        self.assertEqual(rc, 0, err.getvalue())
        self.assertNotIn(" cad", out.getvalue())


class TestHarvestJsonScopeNote(unittest.TestCase):
    """A2-1171: harvest --json carries the CA-PLAN-04 / US-PLAN-04 scope
    disclosure, like the radar, buy-check and sell-check JSON."""

    def test_scope_note_in_json(self):
        from taxjson.lib.wash_scope import scope_note
        for country in ("canada", "usa"):
            with tempfile.TemporaryDirectory() as td:
                g = _write(Path(td) / "margin_gains_wash.json", _gains([
                    {"symbol": "XYZ.US", "qty": 10, "total_cost": 100.0,
                     "position_start_date": _iso(-200)}]))
                (Path(td) / "to_base.csv").write_text(
                    f"{TODAY.isoformat()} 12:00:00 USD CAD 1.25\n")
                rc, out, err = _run(
                    [str(g), "--no-ibkr", "--country", country, "--json",
                     "--base-currency",
                     "USD" if country == "usa" else "CAD"],
                    fetchers=[lambda rem: {s: (9.0, "fake", "USD")
                                           for s in rem}])
            self.assertEqual(rc, 0, err)
            self.assertEqual(_json_rows(out)["scope_note"],
                             scope_note(country))

    def test_scope_note_when_no_positions(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "margin_gains_wash.json", _gains([]))
            rc, out, err = _run([str(g), "--no-ibkr", "--country",
                                 "canada", "--json"],
                                fetchers=[lambda rem: {}])
        self.assertEqual(rc, 0, err)
        self.assertIn("scope_note", _json_rows(out))


class TestUsCryptoOutsideWashRule(unittest.TestCase):
    """A2-1173: in a US project a crypto account's losses are outside
    §1091 (US-WASH-13), so harvest counts them claimable now with no
    wash-sale wording; a Canadian crypto loss stays under s.54."""

    def _go(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = _write(root / "crypto_gains_wash.json", _gains([
                {"symbol": "BTC", "qty": 1, "total_cost": 81000.0,
                 "position_start_date": _iso(-100),
                 "last_acq_date": _iso(-5)}]))
            base = "USD" if country == "usa" else "CAD"
            return _run([str(g), "--no-ibkr", "--country", country,
                         "--base-currency", base,
                         "--crypto-account", "crypto"],
                        fetchers=[lambda rem: {s: (60000.0, "fake", base)
                                               for s in rem}])

    @rule("US-PLAN-05")
    @rule_absent("US-PLAN-05", country="canada")
    def test_us_crypto_loss_is_harvestable_now(self):
        rc, out, err = self._go("usa")
        self.assertEqual(rc, 0, err)
        self.assertIn("now 21,000.00", out)
        row = next(ln for ln in out.splitlines() if ln.startswith("crypto"))
        self.assertIn("no-wash-rule", row)
        self.assertNotIn("no-radar-data", row)
        self.assertIn("US-WASH-13", out)
        # Canada: the same book stays under the superficial-loss rule —
        # no radar data means no clear date, never "now".
        rc, out, err = self._go("canada")
        self.assertEqual(rc, 0, err)
        self.assertIn("now 0.00", out)
        self.assertNotIn("no-wash-rule", out)

    @rule("US-PLAN-05")
    def test_wrapper_names_crypto_accounts(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                f'[settings]\nyear = {TODAY.year}\ncountry = "usa"\n'
                'base_currency = "USD"\n'
                '[accounts.coins]\ntype = "taxable"\ncrypto = true\n')
            _write(root / "work" / "coins_gains_wash.json", _gains([
                {"symbol": "ETH", "qty": 10, "total_cost": 31000.0,
                 "position_start_date": _iso(-100)}]))
            _write(root / "work" / ".price_cache.json", {"ETH": {
                "price": 2000.0, "asof": TODAY.isoformat(),
                "source": "yfinance", "currency": "USD"}})
            env = dict(os.environ, TAXJSON_OFFLINE="1")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "harvest", "--no-ibkr", "--crypto", "--json"],
                capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertTrue(doc["rows"][0]["wash_exempt"])
        self.assertEqual(doc["totals"]["harvestable"]["now"], 11000.0)

    def test_us_crypto_json_flag(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "crypto_gains_wash.json", _gains([
                {"symbol": "ETH", "qty": 10, "total_cost": 31000.0,
                 "position_start_date": _iso(-100)}]))
            rc, out, err = _run(
                [str(g), "--no-ibkr", "--country", "usa",
                 "--base-currency", "USD", "--crypto-account", "crypto",
                 "--json"],
                fetchers=[lambda rem: {s: (2000.0, "fake", "USD")
                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        doc = _json_rows(out)
        self.assertTrue(doc["rows"][0]["wash_exempt"])
        self.assertEqual(doc["totals"]["harvestable"]["now"], 11000.0)


class TestUsShortNeverLongTerm(unittest.TestCase):
    """A2-1176: a stand-alone short sale is short-term (US-HOLD-03) — the
    LT_IN cell never says LT for an open short."""

    def test_short_shows_st(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "brk_gains_wash.json", _gains([
                {"symbol": "SHT.US", "qty": -10, "total_cost": -1000.0,
                 "position_start_date": _iso(-500)},
                {"symbol": "LNG.US", "qty": 10, "total_cost": 1000.0,
                 "position_start_date": _iso(-500)}]))
            argv = [str(g), "--no-ibkr", "--country", "usa",
                    "--base-currency", "USD"]
            fx = [lambda rem: {s: (90.0, "fake", "USD") for s in rem}]
            rc, out, err = _run(argv, fetchers=fx)
            rc2, jout, _ = _run(argv + ["--json"], fetchers=fx)
        self.assertEqual(rc, 0, err)
        sht = next(ln for ln in out.splitlines() if "SHT.US" in ln)
        lng = next(ln for ln in out.splitlines() if "LNG.US" in ln)
        self.assertNotIn(" LT ", f" {sht} ")
        self.assertIn(" ST ", f" {sht} ")
        self.assertIn(" LT ", f" {lng} ")
        rows = {r["symbol"]: r for r in _json_rows(jout)["rows"]}
        self.assertIsNone(rows["SHT.US"]["days_to_long_term"])
        self.assertEqual(rows["LNG.US"]["days_to_long_term"], 0)


if __name__ == "__main__":
    unittest.main()
