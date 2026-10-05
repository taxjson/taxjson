"""No owner-specific defaults in the flow (owner, 2026-10-04): the LEAPS
cut-off is a setting (A1), crypto UTC stamps need a named zone (A2) and
close-year never stands in a province (A3). Synthetic data only."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from tax_rules import rule  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_BASE = ('[settings]\nyear = 2025\ncountry = "canada"\n'
         'base_currency = "CAD"\n')
_ACCT = '\n[accounts.margin]\ntype = "taxable"\n'

# A long call bought 2025-06-02 that expires 2025-12-19: six and a half
# months to expiry.
_MID = "ZZQ251219C00010000.US"
# One bought 2025-02-03 that expires 2026-01-16: more than eleven months.
_LONG = "ZZR260116C00020000.US"


def _buy(d, sym, qty=1):
    return {"action": "BUYSELL", "date": d, "time": "10:00:00",
            "symbol": sym, "quantity": qty, "price": 1.0,
            "net_amount": -100.0 * qty, "currency": "USD"}


class TestLeapsMonthsSetting(unittest.TestCase):
    """A1: the LEAPS cut-off is [settings] leaps_months (default 9)."""

    def _contracts(self, settings_extra=""):
        from taxjson.bin.taxjson_run import _leaps_contracts
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_BASE + settings_extra
                                               + _ACCT)
            (root / "work").mkdir()
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [_buy("2025-06-02", _MID),
                                  _buy("2025-02-03", _LONG)]}))
            return set(_leaps_contracts(root, None, "leaps"))

    def test_default_is_nine_months(self):
        self.assertEqual(self._contracts(), {_LONG})

    def test_project_value_is_used(self):
        self.assertEqual(self._contracts("leaps_months = 3\n"),
                         {_MID, _LONG})
        self.assertEqual(self._contracts("leaps_months = 12\n"), set())

    def test_bad_values_are_refused(self):
        from taxjson.lib.config_check import settings_problems
        for bad in ('"3"', "0", "-2", "2.5", "true"):
            cfg = {"settings": {"country": "canada",
                                "leaps_months": eval(bad.replace(
                                    "true", "True"))}}
            probs = settings_problems(cfg)
            self.assertTrue(any("leaps_months" in p for p in probs),
                            (bad, probs))
        ok = {"settings": {"country": "usa", "leaps_months": 6}}
        self.assertEqual([p for p in settings_problems(ok)
                          if "leaps_months" in p], [])

    def test_both_countries_own_the_key(self):
        from taxjson.lib import country as C
        self.assertEqual(C.SETTING_COUNTRY["leaps_months"], C.BOTH)

    def test_template_documents_it(self):
        from taxjson.lib.config_template import render_init
        for c in ("canada", "usa"):
            self.assertIn("leaps_months", render_init(c, 2025)[0])


REPO = Path(__file__).resolve().parent.parent


def _cli(root, *args, tz=None):
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "TAXJSON_LOCAL_TZ"}
    env.update(TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"))
    if tz:
        env["TZ"] = tz
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


def _crypto_project(td, country="canada", tz_line=""):
    root = Path(td) / country
    root.mkdir(parents=True, exist_ok=True)
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2025\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\n{tz_line}'
        '\n[accounts.kr]\ntype = "taxable"\ncrypto = true\n')
    (root / "inputs" / "kr").mkdir(parents=True)
    return root


class _NoZoneEnv(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("TAXJSON_LOCAL_TZ", None)


class TestNoDefaultZone(_NoZoneEnv):
    """A2: crypto UTC stamps are dated in the zone the user names; there
    is no default (it used to be one user's zone, silently)."""

    def _stops(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = _crypto_project(td, country)
            r = _cli(root, "run", "--no-input", tz="America/Halifax")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("local_timezone", r.stderr)
            self.assertIn("[accounts.kr]", r.stderr)
            self.assertIn('local_timezone = "America/Halifax"', r.stderr)
            # A UTC machine: no zone is suggested, an IANA name is asked.
            r = _cli(root, "run", "--no-input", tz="UTC")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("cannot be read or is UTC", " ".join(r.stderr.split()))
            # format / migrate still work (they date nothing).
            self.assertEqual(_cli(root, "format").returncode, 0)
            self.assertEqual(_cli(root, "migrate", "--dry-run").returncode,
                             0)
            # Named: the run goes ahead.
            root2 = _crypto_project(Path(td) / "b", country,
                                    'local_timezone = "America/Halifax"\n')
            r = _cli(root2, "run", "--no-input")
            self.assertNotIn("has no local_timezone", r.stderr)

    @rule("CA-DATE-12")
    def test_canada_crypto_project_stops(self):
        self._stops("canada")
        from taxjson.lib import tax_logic as TL
        txt = [r.text for _t, rs in TL.rule_sections(
            "canada", {"country": "canada", "year": 2025}) for r in rs
            if r.id == "CA-DATE-12"][0]
        self.assertIn("no default", txt)
        self.assertNotIn("America/Toronto", txt)

    @rule("US-DATE-11")
    def test_usa_crypto_project_stops(self):
        self._stops("usa")
        from taxjson.lib import tax_logic as TL
        txt = [r.text for _t, rs in TL.rule_sections(
            "usa", {"country": "usa", "year": 2025}) for r in rs
            if r.id == "US-DATE-11"][0]
        self.assertIn("no default", txt)

    def test_project_without_crypto_needs_no_zone(self):
        from taxjson.bin.taxjson_run import load_config
        from taxjson.lib.brokerages._crypto_common import (
            LocalTimezoneMissing, local_tz_name)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_BASE + _ACCT)
            os.environ["TAXJSON_LOCAL_TZ"] = "Asia/Tokyo"
            load_config(root)
            # the outer variable never dates this project's rows
            with self.assertRaises(LocalTimezoneMissing):
                local_tz_name()

    def test_parser_outside_a_project_needs_the_variable(self):
        from datetime import datetime
        from taxjson.lib.brokerages._crypto_common import (
            LocalTimezoneMissing, utc_to_local)
        with self.assertRaises(LocalTimezoneMissing) as cm:
            utc_to_local(datetime(2025, 12, 31, 3, 0))
        self.assertIn("TAXJSON_LOCAL_TZ", str(cm.exception))
        os.environ["TAXJSON_LOCAL_TZ"] = "America/Halifax"
        self.assertEqual(utc_to_local(datetime(2025, 12, 31, 3, 0)),
                         datetime(2025, 12, 30, 23, 0))

    def test_kraken_file_refused_without_a_zone(self):
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kr_ledgers.csv"
            p.write_text(
                '"txid","refid","time","type","subtype","aclass","asset",'
                '"amount","fee","balance"\n'
                '"L1","R1","2025-06-02 10:00:00","deposit","","currency",'
                '"ZUSD","100.0","0","100.0"\n')
            with self.assertRaises(Exception) as cm:
                KrakenBrokerage().parse_file(p)
            self.assertIn("TAXJSON_LOCAL_TZ", str(cm.exception))

    def test_utc_machine_scaffold_leaves_it_commented(self):
        from unittest import mock
        from taxjson.lib.config_template import render_init
        text = render_init("canada", 2025, tz=None)[0]
        self.assertIn("# local_timezone", text)
        self.assertNotIn("default America/Toronto", text)
        self.assertIn("no default", text)
        import argparse
        import io
        from contextlib import redirect_stdout
        from taxjson.bin.taxjson_run import cmd_init
        with tempfile.TemporaryDirectory() as td, \
                mock.patch("taxjson.lib.config_template.system_timezone",
                           return_value=None):
            out = io.StringIO()
            with redirect_stdout(out):
                cmd_init(argparse.Namespace(path=str(Path(td) / "p"),
                                            dir=".", force=False,
                                            country="canada", year=2025))
            self.assertIn("local_timezone", out.getvalue())


class TestCloseYearNeedsNoStandInProvince(unittest.TestCase):
    """A3: close-year's carry-forwards come from the project's province,
    or from a federal-only estimate — never another province's tables
    standing in."""

    _EST = dict(realized=400000.0, eligible_div=0.0, foreign_div=0.0,
                pil=0.0, other_income=0.0, other_losses=0.0, year=2025)

    @rule("CA-CARRY-01")
    def test_federal_only_estimate(self):
        from taxjson.lib.tax_estimate import estimate_canada
        fed = estimate_canada(province=None, **self._EST)
        bc = estimate_canada(province="BC", **self._EST)
        self.assertIsNone(fed["province"])
        self.assertTrue(fed["federal_only"])
        self.assertEqual(fed["tax_with"]["provincial"], 0.0)
        self.assertEqual(fed["tax_with"]["federal"],
                         bc["tax_with"]["federal"])
        self.assertEqual(fed["amt"]["excess_fed"], bc["amt"]["excess_fed"])
        self.assertTrue(any("federal" in n.lower() and "province" in n
                            for n in fed["notes"]), fed["notes"])

    def _lock_argv(self, settings_extra):
        import subprocess
        from unittest import mock
        from taxjson.bin import taxjson_run as R
        from taxjson.lib.tax_estimate import estimate_canada
        seen = {}
        prov = "BC" if "BC" in settings_extra else None
        r = estimate_canada(province=prov, **self._EST)

        def fake(argv, capture_output=False, **_k):
            seen["argv"] = list(argv)
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"estimate": r}), "")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_BASE + settings_extra
                                               + _ACCT)
            cfg = R.load_config(root)
            with mock.patch("taxjson.lib.dispatch.run_cmd", fake):
                block = R._carryforwards_for_lock(root, cfg)
        return seen["argv"], block

    def test_close_year_without_province_is_federal_only(self):
        argv, block = self._lock_argv("")
        self.assertIn("--federal-only", argv)
        self.assertNotIn("--province", argv)
        self.assertIsNone(block["minimum_tax"]["province"])
        self.assertNotIn("recovered_provincial", block["minimum_tax"])

    def test_close_year_uses_the_project_province(self):
        argv, block = self._lock_argv('province = "BC"\n')
        self.assertNotIn("--federal-only", argv)
        self.assertNotIn("--province", argv)       # read from [settings]
        self.assertEqual(block["minimum_tax"]["province"], "BC")


if __name__ == "__main__":
    unittest.main()
