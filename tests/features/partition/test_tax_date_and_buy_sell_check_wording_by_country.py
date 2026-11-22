"""Re-audit-2 tests-pins (pins3, group g6b): fixes in taxjson_run.py's
reports, guards, wording gates and settings that held but that no test
failed on when reverted. Each TestCase names the A2 finding and the code
it pins; every test was checked to fail with that code reverted.

All data is synthetic (invented tickers, round amounts).

Formerly: test_fix_a2_pins3_g6b
"""
import json
import tempfile
import unittest

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, projects_both
from taxjson.rules import ids as R
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------------------------------- A2-1533
class TestTaxDateDepartureWording(unittest.TestCase):
    """partition INPUTS-05 (validate_config): a tax_date against the
    country's practice is said in that country's own terms — a US
    project is never told 'CRA practice ... the Canadian default'.

    Formerly: test_fix_a2_pins3_g6b.TestTaxDateDepartureWording"""

    def _warn(self, country, tax_date):
        from taxjson.pipeline import config as pipeline_config
        cur = "CAD" if country == "canada" else "USD"
        w = pipeline_config.validate_config({
            "settings": {"year": 2025, "country": country,
                         "base_currency": cur, "tax_date": tax_date},
            "accounts": {"margin": {"type": "taxable"}}})
        return " ".join(x for x in w if "tax_date" in x)

    @rule(R.US_DATE_02)
    @rule_absent(R.CA_DATE_01, country="usa")
    def test_usa_settle_names_the_irs_trade_date(self):
        w = self._warn("usa", "settle")
        self.assertIn("the IRS dates a disposition by its TRADE date (the "
                      "US default)", w)
        self.assertNotIn("CRA", w)
        self.assertNotIn("Canadian", w)

    @rule(R.CA_DATE_02)
    def test_canada_trade_names_cra_settlement_practice(self):
        w = self._warn("canada", "trade")
        self.assertIn("CRA practice dates a disposition by its SETTLEMENT "
                      "date (the Canadian default)", w)
        self.assertNotIn("IRS", w)


# ------------------------------------------------------------- A2-1577
def _ago(n):
    from datetime import date, timedelta
    return (date.today() - timedelta(days=n)).isoformat()


def _trade_row(day, sym, qty, net, acct="margin"):
    return {"action": "BUYSELL", "date": day, "date_settle": day,
            "time": "10:00:00", "symbol": sym, "quantity": qty,
            "net_amount": net, "currency": "USD", "account": acct,
            "price": abs(net / qty)}


class TestBuySellCheckWordingByCountry(unittest.TestCase):
    """buy-check / sell-check say each country's own rule (the `_usa`
    ternaries of cmd_buy_check (an open window), _buy_check_row_verdict
    (a recent loss) and _sell_check_row_verdict): Canada's s.54 still-
    held-at-day-30 condition and 'sheltered'/'registered' never reach a
    US project, whose text says IRA / tax-deferred and the §1091
    partial-sale rule. The radar's dates are relative to today.

    Formerly: test_fix_a2_pins3_g6b.TestBuySellCheckWordingByCountry"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        acc = ('[accounts.margin]\ntype = "taxable"\n'
               '[accounts.ira]\ntype = "sheltered"\n')
        from datetime import date
        taxable = [
            _trade_row(_ago(5), "OPN.US", 100, 1000.0),       # window open
            _trade_row(_ago(100), "LSS.US", 100, 1500.0),     # recent loss
            _trade_row(_ago(5), "LSS.US", -100, 1200.0),
            _trade_row(_ago(200), "OLD.US", 100, 1500.0)]     # quiet
        sheltered = [_trade_row(_ago(100), "IRA.US", 10, 100.0, "ira")]
        p = projects_both(cls._td.name, year=date.today().year,
                          accounts=acc, files={
                              "work/margin_base.json": json.dumps(
                                  {"transactions": taxable}),
                              "work/sheltered_base.json": json.dumps(
                                  {"transactions": sheltered})})
        cls.out = {}
        for c, root in p.items():
            for cmd, sym in (("buy-check", "OPN.US"),
                             ("buy-check", "LSS.US"),
                             ("sell-check", "IRA.US"),
                             ("sell-check", "OLD.US")):
                cls.out[(c, cmd, sym)] = cli(root, cmd, sym).stdout

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @rule(R.CA_PLAN_01)
    @rule_absent(R.CA_PLAN_01, country="usa")
    @rule(R.US_PLAN_01)
    @rule_absent(R.US_PLAN_01, country="canada")
    def test_buy_check_open_window(self):
        ca = self.out[("canada", "buy-check", "OPN.US")]
        us = self.out[("usa", "buy-check", "OPN.US")]
        self.assertIn("would be superficial if you still hold the bought "
                      "shares 30 days after the sale — a full exit is not",
                      ca)
        self.assertIn("a PARTIAL loss sale of this name within 30 days of "
                      "a buy would be a wash sale; selling the full "
                      "position is not", us)
        self.assertNotIn("30 days after the sale", us)

    @rule(R.CA_PLAN_01)
    @rule_absent(R.CA_PLAN_01, country="usa")
    @rule(R.US_PLAN_01)
    @rule_absent(R.US_PLAN_01, country="canada")
    def test_buy_check_recent_loss(self):
        ca = self.out[("canada", "buy-check", "LSS.US")]
        us = self.out[("usa", "buy-check", "LSS.US")]
        self.assertIn("if you still hold the shares 30 days after that "
                      "sale (DEFERRED if bought taxable, PERMANENT if "
                      "bought sheltered)", ca)
        self.assertIn("(DEFERRED if bought taxable, PERMANENT if bought in "
                      "an IRA)", us)
        self.assertNotIn("still hold", us)
        self.assertNotIn("sheltered", us)

    @rule(R.CA_PLAN_01)
    @rule_absent(R.CA_PLAN_01, country="usa")
    @rule(R.US_PLAN_01)
    @rule_absent(R.US_PLAN_01, country="canada")
    def test_sell_check_sheltered_only_and_clear(self):
        ca = self.out[("canada", "sell-check", "IRA.US")]
        us = self.out[("usa", "sell-check", "IRA.US")]
        self.assertIn("held only in sheltered account(s) — nothing to sell "
                      "at a loss (a registered disposition", ca)
        self.assertIn("held only in IRA(s) — nothing to sell at a loss (a "
                      "tax-deferred disposition", us)
        ca = self.out[("canada", "sell-check", "OLD.US")]
        us = self.out[("usa", "sell-check", "OLD.US")]
        self.assertIn("(taxable or sheltered) for 30 days", ca)
        self.assertIn("(taxable or IRA) for 30 days", us)
        for c in ("IRA.US", "OLD.US"):
            u = self.out[("usa", "sell-check", c)]
            self.assertNotIn("sheltered", u)
            self.assertNotIn("registered", u)


if __name__ == "__main__":
    unittest.main()
