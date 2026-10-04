"""No hard-coded data in the flow (owner, 2026-10-04), part E: no
built-in FX rate (B13), Yahoo spellings and the scan probe (B14),
Canadian twins from every venue (B15), the Questrade dividend matching
key (B18), and IB return of capital judged against the project's
country (partition)."""
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from tax_rules import rule  # noqa: E402

from taxjson.bin import taxjson_convert_currency as CC  # noqa: E402
from taxjson.lib.core import TaxTransaction  # noqa: E402


def _usd_row(date, tid="T1"):
    return TaxTransaction(id=tid, date=date, symbol="ZZQ.US",
                          action="BUYSELL", quantity=1.0, price=10.0,
                          net_amount=-10.0, proceeds=0.0, currency="USD",
                          account="A")


class TestNoBuiltInFxRate(unittest.TestCase):
    """B13: a date with no rate stops the conversion, naming the date
    and the pair; no 1.35 placeholder."""

    def setUp(self):
        CC.reset_fallback_tally()

    def test_no_placeholder_constant(self):
        self.assertFalse(hasattr(CC, "DEFAULT_RATE"))
        self.assertFalse(hasattr(CC, "IMPLICIT_PAIR_RATES"))
        self.assertIsNone(CC.default_rate_for("USD", "CAD"))
        self.assertEqual(CC.default_rate_for("USD", "CAD", 1.5),
                         Decimal("1.5"))

    def test_prior_rate_within_lookback_is_used(self):
        hist = {"USD": {"2025-03-03": Decimal("1.44")}}
        self.assertEqual(CC.get_rate_for_date("USD", "2025-03-08", hist,
                                              None), Decimal("1.44"))
        self.assertIsNone(CC.get_rate_for_date("USD", "2025-03-09", hist,
                                               None))

    def test_missing_rate_stops_naming_date_and_pair(self):
        hist = {"USD": {"2025-03-03": Decimal("1.44")}}
        with self.assertRaises(CC.MissingRateError) as cm:
            CC.process_transactions([_usd_row("2025-03-04", "T0"),
                                     _usd_row("2025-03-20")], "CAD",
                                    hist, None, country="canada")
        msg = str(cm.exception)
        self.assertIn("USD->CAD on 2025-03-20", msg)
        self.assertNotIn("2025-03-04", msg)
        self.assertNotIn("1.35", msg)

    def test_currency_with_no_rates_stops(self):
        with self.assertRaises(CC.MissingRateError) as cm:
            CC.process_transactions([_usd_row("2025-03-04")], "CAD", {},
                                    None, country="canada")
        self.assertIn("USD->CAD on 2025-03-04", str(cm.exception))

    def test_explicit_default_rate_is_an_opt_in(self):
        out = CC.process_transactions([_usd_row("2025-03-20")], "CAD",
                                      {}, Decimal("2"), country="canada")
        self.assertEqual(out[0].currency, "CAD")
        self.assertAlmostEqual(out[0].net_amount, -20.0)

    def test_audit_twin_reports_missing(self):
        from taxjson.bin.taxjson_audit import rate_with_provenance
        self.assertEqual(rate_with_provenance("USD", "2025-03-09",
                                              {"USD": {}}, None),
                         (None, "missing", None))

    def test_fees_report_stops_on_a_missing_rate(self):
        from taxjson.bin import taxjson_fees as F
        with self.assertRaises(CC.MissingRateError):
            self._fees_one(F)

    def _fees_one(self, F):
        import json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ib.json"
            p.write_text(json.dumps({
                "metadata": {"source_brokerage": "ib"},
                "transactions": [{
                    "id": "F1", "date": "2025-03-20", "symbol": "ZZQ.US",
                    "action": "BUYSELL", "quantity": 1, "price": 10,
                    "net_amount": -11, "commission": -1, "fee": 0,
                    "currency": "USD", "account": "A"}]}))
            F.aggregate([p], year=None, since=None, to_curr="CAD",
                        history={}, default_rate=None, by_account=False)

    @rule("CA-FX-02")
    def test_canada_rule_states_no_built_in_rate(self):
        from taxjson.lib import tax_logic
        text = tax_logic.catalog()["CA-FX-02"].text
        self.assertNotIn("1.35", text)
        self.assertIn("no built-in rate", text)

    @rule("US-FX-02")
    def test_usa_rule_states_no_built_in_rate(self):
        from taxjson.lib import tax_logic
        text = tax_logic.catalog()["US-FX-02"].text
        self.assertNotIn("1.35", text)
        self.assertIn("no built-in rate", text)


if __name__ == "__main__":
    unittest.main()
