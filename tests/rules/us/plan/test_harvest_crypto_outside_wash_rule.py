"""Re-audit-2 planning fixes: `tjs harvest` and the price chain.

Offline throughout: prices come from injected fetchers (the price
chain's `fetchers` test hook) or a seeded price cache, never the
network. Synthetic books only.

Formerly: test_fix_a2_planning_harvest
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from taxjson.rules import ids as R

from _support.planning_harvest_runs import TODAY, _iso, _write, _gains, _run, _json_rows


class TestUsCryptoOutsideWashRule(unittest.TestCase):
    """A2-1173: in a US project a crypto account's losses are outside
    §1091 (US-WASH-13), so harvest counts them claimable now with no
    wash-sale wording; a Canadian crypto loss stays under s.54.

    Formerly: test_fix_a2_planning_harvest.TestUsCryptoOutsideWashRule"""

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

    @rule(R.US_PLAN_05)
    @rule_absent(R.US_PLAN_05, country="canada")
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

    @rule(R.US_PLAN_05)
    def test_wrapper_names_crypto_accounts(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {TODAY.year}\ncountry = "usa"\n'
                'base_currency = "USD"\n'
                '[accounts.coins]\ntype = "taxable"\ncrypto = true\n')
            _write(root / "work" / "coins_gains_wash.json", _gains([
                {"symbol": "ETH", "qty": 10, "total_cost": 31000.0,
                 "position_start_date": _iso(-100)}]))
            _write(root / "work" / ".price_cache.json", {"ETH": {
                "price": 2000.0, "asof": TODAY.isoformat(),
                "source": "yfinance", "currency": "USD",
                "quote_symbol": "ETH-USD"}})
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


if __name__ == "__main__":
    unittest.main()
