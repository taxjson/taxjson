"""No owner-specific defaults in the flow (owner, 2026-10-04): the LEAPS
cut-off is a setting (A1), crypto UTC stamps need a named zone (A2) and
close-year never stands in a province (A3). Synthetic data only."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
