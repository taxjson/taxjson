"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.

Formerly: test_fix_a2_spec
"""
import contextlib
import io
import os
import unittest

from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx
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


def _gains_one(country, book, sheltered=(), **req):
    """One country's pipeline.run_gains on `book` (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         **dict(dict(year=2025), **req)))
    res["_stderr"] = err.getvalue()
    return res


class TestUsDust(unittest.TestCase):
    """A2-0808 / A2-0818 / A2-0820 / A2-1471 / A2-1484 / A2-1485: the
    US engine's 1e-08 zero is stated and every case is named; Canada
    keeps any amount of a coin (CA-CRYPTO-09).

    Formerly: test_fix_a2_spec.TestUsDust"""

    def _book(self, *rows):
        return [tx("BUYSELL", d, s, q, n, currency="CAD", account="crypto")
                for d, s, q, n in rows]

    @rule(R.US_CRYPTO_08)
    @rule_absent(R.US_CRYPTO_08, country="canada")
    def test_dust_row_residue_and_excess(self):
        r = gains_both(self._book(("2025-01-02", "ETH", 5e-9, 2),
                                  ("2025-03-03", "ETH", -5e-9, 3)),
                       year=2025)
        self.assertEqual(r["usa"]["transactions"], [])
        self.assertIn("smaller than 1e-08", r["usa"]["_stderr"])
        self.assertAlmostEqual(r["canada"]["transactions"][0]["gain"], 1.0)

        r = gains_both(self._book(("2025-01-02", "ETH", 1.000000005, 2000),
                                  ("2025-03-03", "ETH", -1, 3000)),
                       year=2025)
        self.assertEqual(r["usa"]["inventory"], [])
        self.assertIn("lot residue of at most 1e-08",
                      r["usa"]["_stderr"])
        self.assertEqual(len(r["canada"]["inventory"]), 1)
        self.assertNotIn("1e-08", r["canada"]["_stderr"])

        r = gains_both(self._book(("2025-01-02", "BTC", 1, 2000),
                                  ("2025-03-03", "BTC", -1.000000009, 3000)),
                       year=2025)
        self.assertEqual(r["usa"]["inventory"], [])
        self.assertIn("exceeded the units held", r["usa"]["_stderr"])
        self.assertLess(r["canada"]["inventory"][0]["qty"], 0)
        self.assertIn("1e-08", _text("usa", "US-CRYPTO-08"))

    @rule(R.US_CRYPTO_08)
    def test_float_noise_is_not_named(self):
        r = _gains_one("usa", self._book(("2025-01-02", "ETH", 0.1, 200),
                                         ("2025-01-03", "ETH", 0.2, 400),
                                         ("2025-03-03", "ETH", -0.3, 900)))
        self.assertEqual(r["inventory"], [])
        self.assertNotIn("1e-08", r["_stderr"])


if __name__ == "__main__":
    unittest.main()
