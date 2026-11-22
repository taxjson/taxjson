"""Fix round 7 (wave B), 2026-10-29: harvest, watch's harvest baseline,
tips, div-estimate and fees (backlog TJ-0908, TJ-0709, TJ-0710,
TJ-0911, TJ-0912, TJ-0913, TJ-0914, TJ-0900, TJ-0897, TJ-0776).

Synthetic data only; offline throughout (injected fetchers or a cache).

Formerly: test_fix_1029_r7b_harvest
"""

import json
import tempfile
import unittest
from tax_rules import rule
from taxjson.rules import ids as R



class TestTipsForeignIssuerWithCanadianLine(unittest.TestCase):
    """TJ-0913: an issuer the project declares US-domiciled stays a US
    issuer when its Canadian listing is joined (TOBASE): no "Canadian
    issuer ... eligible-dividend" advice, and TFSA-US-DIV in a TFSA.

    Formerly: test_fix_1029_r7b_harvest.TestTipsForeignIssuerWithCanadianLine"""

    def _findings(self, td, plan, tmap):
        from _support.planning_tips import _holdings_toml, _project, _raw_json, _run
        root = _project(td, accounts=[(plan, "sheltered" if plan == "tfsa"
                                       else "taxable")],
                        holdings={plan: _holdings_toml("ZZSY.US")},
                        raws={plan: _raw_json("ZZSY.US")},
                        ticker_map=tmap)
        r = _run(root, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return [(f["check"], f["message"])
                for f in json.loads(r.stdout)["findings"]]

    @rule(R.CA_SCAN_01, R.CA_SCAN_02)
    def test_declared_us_issuer_in_tfsa_with_a_canadian_line(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._findings(td, "tfsa", "T1135 ZZSY.US USA\n"
                                           "TOBASE ZZSY.US ZZSY.TO\n")
        self.assertEqual([c for c, _ in f], ["TFSA-US-DIV"])
        self.assertNotIn("Canadian issuer", f[0][1])

    @rule(R.CA_SCAN_02)
    def test_declared_us_issuer_in_taxable_no_listing_advice(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._findings(td, "margin", "T1135 ZZSY.US USA\n"
                                             "TOBASE ZZSY.US ZZSY.TO\n")
        self.assertEqual(f, [])

    @rule(R.CA_SCAN_02)
    def test_declared_canadian_issuer_keeps_listing_advice(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._findings(td, "tfsa", "T1135 ZZSY.US CAN\n"
                                           "TOBASE ZZSY.US ZZSY.TO\n")
        self.assertEqual([c for c, _ in f], ["US-LISTING"])

    @rule(R.CA_SCAN_02)
    def test_no_domicile_on_record_keeps_listing_advice(self):
        with tempfile.TemporaryDirectory() as td:
            f = self._findings(td, "margin", "TOBASE ZZSY.US ZZSY.TO\n")
        self.assertEqual([c for c, _ in f], ["US-LISTING"])



class TestTipsShortSeller(unittest.TestCase):
    """TJ-0914: a short position that paid a dividend in lieu receives
    no dividend: no US-LISTING (eligible-dividend) advice for it.

    Formerly: test_fix_1029_r7b_harvest.TestTipsShortSeller"""

    def _findings(self, td, qty, action, amount):
        from _support.planning_tips import _project, _run
        holdings = ('schema_version = "1.2"\n[[holding]]\n'
                    f'symbol = "ZZSY.US"\nquantity = {qty}\n'
                    'currency = "CAD"\ntotal_cost = -100.0\n'
                    'cost_per_share = 10.0\n')
        raw = json.dumps({"transactions": [
            {"action": action, "date": "2026-03-01", "symbol": "ZZSY.US",
             "quantity": 0, "gross_amount": amount, "net_amount": 0.0,
             "currency": "CAD"}]})
        root = _project(td, accounts=[("margin", "taxable")],
                        holdings={"margin": holdings},
                        raws={"margin": raw},
                        ticker_map="T1135 ZZSY.US CAN\n"
                                   "TOBASE ZZSY.US ZZSY.TO\n")
        r = _run(root, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return [f["check"] for f in json.loads(r.stdout)["findings"]]

    @rule(R.CA_SCAN_02)
    def test_short_payer_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                self._findings(td, -10.0, "DIVIDEND_IN_LIEU", -10.0), [])

    @rule(R.CA_SCAN_02)
    def test_paid_in_lieu_is_not_a_dividend_received(self):
        # Long now, but the only payment on record was a charge.
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                self._findings(td, 10.0, "DIVIDEND_IN_LIEU", -10.0), [])

    @rule(R.CA_SCAN_02)
    def test_long_recipient_still_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                self._findings(td, 10.0, "DIVIDEND", 10.0), ["US-LISTING"])
        with tempfile.TemporaryDirectory() as td:
            # A payment in lieu RECEIVED (shares lent out) counts.
            self.assertEqual(
                self._findings(td, 10.0, "DIVIDEND_IN_LIEU", 10.0),
                ["US-LISTING"])



if __name__ == "__main__":
    unittest.main()
