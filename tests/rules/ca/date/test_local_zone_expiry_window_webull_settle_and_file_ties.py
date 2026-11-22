"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.

Formerly: test_fix_a2_spec
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tax_rules import rule
from tax_rules.dual import cli, settings_for
from taxjson.rules import ids as R

from taxjson.lib import tax_logic as TL


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _text(country, rid, **settings):
    """The rendered text of one rule under `settings`."""
    st = dict(settings, country=country, year=2025)
    for _t, rules in TL.rule_sections(country, st):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated for {country}")


def _project(td, country, files, *, accounts=("margin",), year=2025,
             **settings):
    root = Path(td) / country
    root.mkdir(parents=True, exist_ok=True)
    if not isinstance(accounts, dict):
        accounts = {a: "taxable" for a in accounts}
    acc = "".join(f'[accounts.{a}]\ntype = "{t}"\n'
                  for a, t in accounts.items())
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, **settings) + acc)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _gains(root, account="margin"):
    p = root / "work" / f"{account}_gains.json"
    return json.loads(p.read_text())


# ------------------------------------------------------------ dates
class TestLocalZoneInsideAProject(unittest.TestCase):
    """A2-0165: inside a project with no local_timezone the environment
    variable never re-dates crypto. Since the 2026-10 generalisation
    there is no default zone either: a crypto account without the
    setting stops every command, naming the key; the setting wins.

    Formerly: test_fix_a2_spec.TestLocalZoneInsideAProject"""

    def _check(self, country, rid):
        from taxjson.pipeline import config as pipeline_config
        from taxjson.lib.timezones import LocalTimezoneMissing, local_tz_name
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ,
                                {"TAXJSON_LOCAL_TZ": "Asia/Tokyo"}):
            root = _project(td, country, {}, local_timezone=None)
            pipeline_config.load_config(root)
            with self.assertRaises(LocalTimezoneMissing):
                local_tz_name()            # the outer variable is dropped
            self.assertIn("no default", _text(country, rid))
            croot = _project(Path(td) / "c", country, {},
                             accounts={"kr": "taxable"},
                             local_timezone=None)
            (croot / "taxjson.toml").write_text(
                (croot / "taxjson.toml").read_text()
                + "crypto = true\n")
            with self.assertRaises(SystemExit) as cm:
                pipeline_config.load_config(croot)
            self.assertIn("local_timezone", str(cm.exception.code))
            # The setting wins.
            root2 = _project(Path(td) / "b", country, {},
                             local_timezone="America/Vancouver")
            pipeline_config.load_config(root2)
            self.assertEqual(local_tz_name(), "America/Vancouver")
            self.assertIn("America/Vancouver",
                          _text(country, rid,
                                local_timezone="America/Vancouver"))

    @rule(R.CA_DATE_12)
    def test_canada(self):
        self._check("canada", "CA-DATE-12")

    @rule(R.US_DATE_11)
    def test_usa(self):
        self._check("usa", "US-DATE-11")


class TestExpiryDating(unittest.TestCase):
    """A2-0483: the 7-day re-dating window and the 0DTE settle clamp.

    Formerly: test_fix_a2_spec.TestExpiryDating"""

    def _window(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        f = BaseBrokerage.option_expiry_booking_date
        self.assertEqual(f("2026-01-02", "12/31/25"), "2025-12-31")
        self.assertEqual(f("2026-01-07", "12/31/25"), "2025-12-31")
        self.assertEqual(f("2026-01-09", "12/31/25"), "2026-01-09")

    def _clamp(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        trade = {"action": "BUYSELL", "symbol": "ZZQ251231C00050000",
                 "account": "m", "date": "2025-12-31",
                 "date_settle": "2026-01-02"}
        other = dict(trade, symbol="ZZQ260116C00050000")
        exp = {"symbol": "ZZQ251231C00050000", "account": "m",
               "date": "2025-12-31", "date_settle": "2025-12-31"}
        BaseBrokerage.clamp_settlement_across([trade, other], [exp])
        self.assertEqual(trade["date_settle"], "2025-12-31")
        self.assertEqual(other["date_settle"], "2026-01-02")

    @rule(R.CA_DATE_15)
    def test_ca_window(self):
        self._window()

    @rule(R.US_DATE_14)
    def test_us_window(self):
        self._window()

    @rule(R.CA_DATE_16)
    def test_ca_clamp(self):
        self._clamp()

    @rule(R.US_DATE_15)
    def test_us_clamp(self):
        self._clamp()


class TestWebullPrintsTheSettleDate(unittest.TestCase):
    """A2-1478: Webull's Date column is the settle date; the trade date
    is walked back one cycle.

    Formerly: test_fix_a2_spec.TestWebullPrintsTheSettleDate"""

    def _parse(self):
        from _support.wb_summary_2024 import _H25, _PRE, _parse
        text = (_PRE.replace("2024", "2026") + _H25
                + 'USD,02-01-2026,SELL,ZZR,ZZR HOLDINGS INC CLASS A,SHS,'
                  '-10,50.00,,499.00\n'
                + 'USD,02-12-2025,BUY,ZZR,,,10,40.00,,"(401.00)"\n')
        return {t["quantity"]: t for t in _parse(text)}

    @rule(R.CA_DATE_17)
    def test_canada(self):
        rows = self._parse()
        self.assertEqual((rows[-10.0]["date"], rows[-10.0]["date_settle"]),
                         ("2025-12-31", "2026-01-02"))
        self.assertEqual(rows[10.0]["date"], "2025-12-01")

    @rule(R.US_DATE_16)
    def test_usa(self):
        rows = self._parse()
        self.assertEqual((rows[-10.0]["date"], rows[-10.0]["date_settle"]),
                         ("2025-12-31", "2026-01-02"))


class TestCrossFileTies(unittest.TestCase):
    """A2-0823: same-moment rows of one account from two files follow
    the files' name order.

    Formerly: test_fix_a2_spec.TestCrossFileTies"""

    _BUY = "BUYSELL 2025-02-03 10:00:00 JJJ.US 100 USD 10 1000 0\n"

    def _run(self, country, a, b, extra=""):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {
                "inputs/margin/0.tt": self._BUY + extra,
                "inputs/margin/a.tt": a, "inputs/margin/b.tt": b})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            return _gains(root)

    @rule(R.US_DATE_17)
    def test_usa_fifo_follows_file_names(self):
        sell = "BUYSELL 2025-06-02 11:00:00 JJJ.US -100 USD {p} {t} 0\n"
        lot2 = "BUYSELL 2025-03-03 10:00:00 JJJ.US 100 USD 20 2000 0\n"
        g = self._run("usa", sell.format(p=12, t=1200),
                      sell.format(p=30, t=3000), extra=lot2)
        by_p = {round(x["proceeds"]): x for x in g["transactions"]}
        # a.tt's sale (1200) takes the first lot (cost 1000).
        self.assertAlmostEqual(by_p[1200]["cost"], 1000.0, places=2)
        self.assertAlmostEqual(by_p[3000]["cost"], 2000.0, places=2)
        g2 = self._run("usa", sell.format(p=30, t=3000),
                       sell.format(p=12, t=1200), extra=lot2)
        by_p2 = {round(x["proceeds"]): x for x in g2["transactions"]}
        self.assertAlmostEqual(by_p2[3000]["cost"], 1000.0, places=2)

    @rule(R.CA_DATE_18)
    def test_canada_sale_before_rebuy_by_file_name(self):
        sell = "BUYSELL 2025-06-02 11:00:00 JJJ.US -100 USD 20 2000 0\n"
        buy = "BUYSELL 2025-06-02 11:00:00 JJJ.US 100 USD 30 3000 0\n"
        g = self._run("canada", sell, buy)
        self.assertEqual(len(g["transactions"]), 1)
        # The sale in a.tt is made from the shares held before b.tt's
        # buy: ACB 1000 (USD 10/share), not the blended 2000.
        rg = g["transactions"][0]
        self.assertLess(rg["cost"], rg["proceeds"] * 0.75)
        g2 = self._run("canada", buy, sell)
        rg2 = g2["transactions"][0]
        self.assertGreater(rg2["cost"], rg["cost"] * 1.5)


if __name__ == "__main__":
    unittest.main()
