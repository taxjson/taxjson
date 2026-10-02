"""Re-audit-2 test pins (tests-pins-03): harvest, sell-check and
safe-to-sell — A2-0885 (standalone harvest finds the project's
ticker.map), A2-1573 (EXIT@ for options uses the contract multiplier),
A2-1529 / A2-1578 (US-only long-term column, field and note; the RISK
pause wording per country), A2-1581 (the US scope note on harvest,
sell-check and safe-to-sell). Offline, synthetic data only."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, projects_both
from test_fix_a2_planning_harvest import (TODAY, _gains, _iso, _json_rows,
                                          _run, _write)


class TestStandaloneHarvestFindsTheTickerMap(unittest.TestCase):
    """A2-0885 (S034-11 twin): the console script `taxjson-harvest` run on
    <project>/work/*_gains_wash.json without --ticker-map quoted a
    TOBASE-renamed option as the Montreal contract in CAD. It now finds
    the project's ticker.map next to the inputs or one level up, as it
    finds yf_ticker.map."""

    def test_renamed_option_quoted_as_the_contract_held(self):
        exp = (TODAY + timedelta(days=200)).strftime("%y%m%d")
        held, booked = f"KGC{exp}C00012000.US", f"K{exp}C00012000.TO"
        seen = []

        def _opt(rem):
            seen.extend(rem)
            return {s: (1.00, "ibkr") for s in rem if s == held}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            g = _write(work / "margin_gains_wash.json", _gains([
                {"symbol": booked, "qty": 1, "total_cost": 136.0}]))
            _write(work / "margin_raw_gains.json", {"inventory": [
                {"symbol": held, "qty": 1, "total_cost": 100.0}]})
            (root / "ticker.map").write_text("TOBASE KGC.US K.TO\n")
            (work / "to_base.csv").write_text(
                f"{TODAY.isoformat()} 12:00:00 USD CAD 1.38\n")
            rc, out, err = _run([str(g), "--no-ibkr", "--country", "canada",
                                 "--options", "--json"],
                                fetchers=[lambda rem: {}],
                                option_fetchers=[_opt])
        self.assertEqual(rc, 0, err)
        self.assertEqual(seen, [held])
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["price_currency"], "USD")
        self.assertAlmostEqual(row["value"], 138.0)


class TestOptionBreakevenUsesTheMultiplier(unittest.TestCase):
    def test_two_calls_costing_1000(self):
        """A2-1573: 2 calls costing 1,000 break even at 1000 / (2 x 100)
        x 1.02 = 5.10 a contract unit, not 510."""
        exp = (TODAY + timedelta(days=90)).strftime("%y%m%d")
        opt = f"QZX{exp}C00050000.TO"
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "margin_gains_wash.json", _gains([
                {"symbol": opt, "qty": 2, "total_cost": 1000.0}]))
            rc, out, err = _run([str(g), "--no-ibkr", "--country", "canada",
                                 "--options", "--json"],
                                fetchers=[lambda rem: {}],
                                option_fetchers=[lambda rem: {
                                    s: (1.00, "ibkr") for s in rem}])
        self.assertEqual(rc, 0, err)
        row = _json_rows(out)["rows"][0]
        self.assertEqual(row["verdict"], "LOSS")
        self.assertAlmostEqual(row["breakeven_exit_native"], 5.10)


class TestHarvestCountryWording(unittest.TestCase):
    """A2-1529 / A2-1578: Canada has no short/long-term split, so the
    LT_IN column, the days_to_long_term field and the ST/LT note are US
    only; the RISK line names IRA buys in the US and DRIPs/sheltered
    adds in Canada."""

    def _harvest(self, country, *extra):
        sym = "QZR.US" if country == "usa" else "QZR.TO"
        cur = "USD" if country == "usa" else "CAD"
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "margin_gains_wash.json", _gains([
                {"symbol": sym, "qty": 100, "total_cost": 1000.0,
                 "position_start_date": _iso(-200),
                 "last_acq_date": _iso(-200)}]))
            radar = _write(Path(td) / "wash_radar_margin.json", {
                "sections": [{"title": "RISK", "rows": [{
                    "ticker": sym, "category": "RISK",
                    "advisory": "RISK: a sheltered buy after a loss sale "
                                "would deny it",
                    "clears_at": None}]}]})
            return _run([str(g), "--no-ibkr", "--country", country,
                         "--base-currency", cur, "--radar", str(radar),
                         *extra],
                        fetchers=[lambda rem: {s: (8.0, "fake", cur)
                                               for s in rem}])

    def test_canada(self):
        rc, out, err = self._harvest("canada")
        self.assertEqual(rc, 0, err)
        self.assertIn("pause DRIPs/sheltered adds", out)
        self.assertNotIn("IRA", out)
        self.assertNotIn("LT_IN", out)
        self.assertNotIn("ST/LT", out)
        rc, out, err = self._harvest("canada", "--json")
        self.assertEqual(rc, 0, err)
        self.assertIsNone(_json_rows(out)["rows"][0]["days_to_long_term"])

    def test_usa(self):
        rc, out, err = self._harvest("usa")
        self.assertEqual(rc, 0, err)
        self.assertIn("pause IRA buys and dividend reinvestment", out)
        self.assertNotIn("DRIPs/sheltered", out)
        self.assertIn("LT_IN", out)
        self.assertIn("per-lot ST/LT is decided by the engine", out)
        rc, out, err = self._harvest("usa", "--json")
        self.assertEqual(rc, 0, err)
        days = _json_rows(out)["rows"][0]["days_to_long_term"]
        self.assertIsInstance(days, int)
        self.assertGreater(days, 150)


class TestUsScopeNoteOnSellTools(unittest.TestCase):
    """A2-1581: a US project's harvest, sell-check and safe-to-sell cite
    IRS Pub. 550, never the Canadian s.251.1 note (US-PLAN-04)."""

    @rule("US-PLAN-04")
    def test_harvest_text(self):
        with tempfile.TemporaryDirectory() as td:
            g = _write(Path(td) / "margin_gains_wash.json", _gains([
                {"symbol": "QZR.US", "qty": 10, "total_cost": 100.0,
                 "position_start_date": _iso(-20)}]))
            rc, out, err = _run([str(g), "--no-ibkr", "--country", "usa",
                                 "--base-currency", "USD"],
                                fetchers=[lambda rem: {s: (9.0, "fake",
                                                           "USD")
                                                       for s in rem}])
        self.assertEqual(rc, 0, err)
        self.assertIn("Pub. 550", out)
        self.assertNotIn("s.251.1", out)

    @rule("US-PLAN-04")
    def test_safe_to_sell(self):
        import contextlib
        import io
        from taxjson.bin.taxjson_safe_to_sell import main as sts
        with tempfile.TemporaryDirectory() as td:
            b = _write(Path(td) / "margin.json", {"transactions": [
                {"action": "BUYSELL", "date": _iso(-60), "time": "10:00:00",
                 "symbol": "QZR.US", "quantity": 100, "price": 10.0,
                 "net_amount": 1000.0, "currency": "USD",
                 "account": "margin"}]})
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = sts(["--taxable", str(b), "--country", "usa"])
        self.assertEqual(rc, 0)
        self.assertIn("QZR.US", out.getvalue())
        self.assertIn("Pub. 550", out.getvalue())
        self.assertNotIn("s.251.1", out.getvalue())

    @rule("US-PLAN-04")
    def test_sell_check(self):
        d = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731
        tt = (f"BUYSELL {d(-60)} 10:00:00 QZR.US 200 USD 10.00 2000.00 0.00\n"
              f"BUYSELL {d(-5)} 10:00:00 QZR.US -100 USD 8.00 800.00 0.00\n")
        year = (TODAY - timedelta(days=5)).year
        with tempfile.TemporaryDirectory() as tmp:
            root = projects_both(tmp, year=year, files={
                "inputs/margin/m.tt": tt})["usa"]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            s = cli(root, "sell-check", "QZR.US", "--json")
            t = cli(root, "sell-check", "QZR.US")
        self.assertIn("Pub. 550", json.loads(s.stdout)["scope_note"])
        self.assertIn("Pub. 550", t.stdout)
        self.assertNotIn("s.251.1", t.stdout)


if __name__ == "__main__":
    unittest.main()
