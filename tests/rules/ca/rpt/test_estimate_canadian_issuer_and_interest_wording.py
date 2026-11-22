"""Re-audit-2 fixes, filing-reports list (helper D): the run pipeline
(crypto-sends stage, raw pass phantoms, blended .sum diagnostics, run
state banner), `sum`/`estimate` account handling and flag guards,
instalment dates, and the estimate's Canadian-issuer test and wording.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_d
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
import _paths
from _support.filing_run_record import record_full_run

REPO_ROOT = _paths.REPO


def _taxjson(root, *a, env=None):
    e = {**os.environ, "TAXJSON_OFFLINE": "1", "NO_COLOR": "1",
         "PYTHONPATH": str(REPO_ROOT / "src")}
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=e)


def _estimate_project(tmp, rows, base_rows=None, *, country="canada"):
    """A project with a hand-written gains file (and base book) for one
    taxable account; `tjs estimate --json` reads them."""
    root = Path(tmp)
    (root / "work").mkdir()
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2025\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n'
        + ('province = "ON"\n' if country == "canada" else "")
        + '[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_gains.json").write_text(json.dumps(
        {"summary": {"year": 2025}, "transactions": rows,
         "inventory": [], "wash_sales": []}))
    if base_rows is not None:
        (root / "work" / "margin_base.json").write_text(json.dumps(
            {"transactions": base_rows}))
    record_full_run(root)       # (the estimate reads books a full run recorded)
    return root


def _div_rows(sym, issuer, action="DIVIDEND"):
    g = {"date": "2025-06-30", "date_settle": "2025-06-30",
         "symbol": sym, "qty": 0.0, "currency": "CAD", "gain": 0.0,
         "cost": 0.0, "proceeds": 0.0, "dividend": 4000.0,
         "account": "margin", "id": "d1", "action": action}
    if action == "DIVIDEND_IN_LIEU":
        g.update(pil=0.0, deemed_dividend="ITA s.260")
    b = {"action": action, "date": "2025-06-30", "time": "10:00:00",
         "date_settle": "2025-06-30", "symbol": sym, "quantity": 0.0,
         "price": 0.0, "net_amount": 4000.0, "gross_amount": 4000.0,
         "currency": "CAD", "account": "margin", "id": "d1",
         "dealer_country": "CA"}
    if issuer:
        b["issuer_country"] = issuer
    return [g], [b]


class TestEstimateCanadianIssuer(unittest.TestCase):
    """A2-0319, A2-0662: the Canada estimate decides a dividend's
    issuer like the engine (income_dating.is_canadian_issuer: the CA
    ISIN, else the listing) — a Canadian issuer on a US listing is an
    eligible dividend with no assumed foreign tax credit.

    Formerly: test_fix_a2_filrep_d.TestEstimateCanadianIssuer"""

    def _est(self, sym, issuer, action="DIVIDEND"):
        g, b = _div_rows(sym, issuer, action)
        with tempfile.TemporaryDirectory() as td:
            root = _estimate_project(td, g, b)
            r = _taxjson(root, "estimate", "--other-income", "100000",
                         "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            return json.loads(r.stdout)["estimate"]

    @rule(R.CA_RPT_04)
    def test_a2_0319_ca_isin_on_us_listing_is_eligible(self):
        to = self._est("ZQX.TO", "")
        us_ca = self._est("ZQX.US", "CA")
        us_us = self._est("ZQX.US", "US")
        self.assertEqual(us_ca["ftc_assumed"], 0.0)
        self.assertAlmostEqual(us_ca["grossed_eligible"],
                               to["grossed_eligible"], places=2)
        self.assertAlmostEqual(us_ca["estimated_tax"], to["estimated_tax"],
                               places=2)
        # A US issuer stays foreign (the control).
        self.assertGreater(us_us["ftc_assumed"], 0.0)
        self.assertEqual(us_us["grossed_eligible"], 0.0)

    @rule(R.CA_INC_03)
    def test_a2_0662_s260_pil_ca_isin_us_listing_is_eligible(self):
        to = self._est("ZQX.TO", "", "DIVIDEND_IN_LIEU")
        us_ca = self._est("ZQX.US", "CA", "DIVIDEND_IN_LIEU")
        self.assertEqual(us_ca["ftc_assumed"], 0.0)
        self.assertAlmostEqual(us_ca["grossed_eligible"],
                               to["grossed_eligible"], places=2)


class TestInterestAndSlipWording(unittest.TestCase):
    """A2-0644, A2-1153, A2-1101: the estimate, the checklist and
    docs/filing.md no longer say no taxjson output totals interest (each
    .sum prints a NET 'CASH INTEREST' line), and the Canada Assumes line
    says mapped T5 box 18 amounts are included.

    Formerly: test_fix_a2_filrep_d.TestInterestAndSlipWording"""

    @rule(R.CA_RPT_06)
    def test_ca_assumptions_point_at_cash_interest_and_box18(self):
        from taxjson.lib.tax_estimate import CA_ASSUMPTIONS
        self.assertNotIn("no taxjson view totals", CA_ASSUMPTIONS)
        self.assertIn("CASH INTEREST", CA_ASSUMPTIONS)
        self.assertNotIn("capital gains on T3/T5 slips are not included",
                         CA_ASSUMPTIONS)
        self.assertIn("T5 box 18 amounts not named in "
                      "[[capital_gains_dividends]]", CA_ASSUMPTIONS)

    def test_checklist_and_filing_doc_wording(self):
        import inspect
        from taxjson.lib import checklist
        src = inspect.getsource(checklist)
        self.assertNotIn("command totals the interest", src)
        self.assertNotIn("command totals it", src)
        self.assertEqual(src.count("CASH INTEREST"), 2)
        doc = (REPO_ROOT / "docs" / "filing.md").read_text()
        self.assertNotIn("no taxjson command totals", doc)
        self.assertIn("CASH INTEREST", doc)


if __name__ == "__main__":
    unittest.main()
