"""Owner decisions, round 3 (income): A2-1465 and A2-0828.

- A2-1465 (CA-INC-03 / CA-INC-07): s.260(5) deems a dividend on a SHARE
  only. A payment in lieu on a unit the books show to be a Canadian
  trust's (the distribution test that dates trust income) is ordinary
  income; a Canadian corporation's share keeps the deemed dividend.
- A2-0828 (CA-EST-TRUST): the estimate keeps grossing up a Canadian
  trust's distributions as eligible dividends, and its printed
  assumptions say so.

All data is synthetic (fake account ids, invented tickers).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import country as C
from taxjson.lib.income_dating import IncomeRules
from tax_rules import rule, rule_absent
from tax_rules.dual import cli_both, gains_both, projects_both, tx


def _pil(sym="ZQU.TO", **kw):
    return tx("DIVIDEND_IN_LIEU", "2025-06-30", sym, 0, 40.0,
              gross_amount=40.0, type="dividend_in_lieu", currency="CAD",
              dealer_country="CA", **kw)


def _dist(sym="ZQU.TO"):
    return tx("DIVIDEND", "2025-03-31", sym, 0, 25.0, gross_amount=25.0,
              currency="CAD", income_label="distribution",
              record_date="2025-03-24")


def _book(sym="ZQU.TO", with_dist=True):
    rows = [tx("BUYSELL", "2025-02-03", sym, 100, 1000, currency="CAD")]
    if with_dist:
        rows.append(_dist(sym))
    rows.append(_pil(sym))
    return rows


def _pil_rows(res):
    return [t for t in res["transactions"]
            if t.get("action") == "DIVIDEND_IN_LIEU"]


class TestPilOnATrustUnitIsOrdinary(unittest.TestCase):
    """A2-1465."""

    @rule("CA-INC-07", "CA-INC-03")
    def test_rules_trust_test(self):
        r = IncomeRules("canada")
        rows = [_dist().to_dict(), _pil().to_dict()]
        trusts = r.trust_units(rows)
        self.assertEqual(trusts, frozenset({"ZQU.TO"}))
        self.assertFalse(r.pil_is_dividend(_pil().to_dict(), trusts))
        # A row the broker itself calls a distribution is a trust's.
        self.assertFalse(r.pil_is_dividend(
            dict(_pil().to_dict(), income_label="distribution")))
        # No distribution in the books: the unit cannot be told from a
        # share, so it stays deemed (CA-INC-07's limitation).
        self.assertTrue(r.pil_is_dividend(_pil().to_dict(), frozenset()))
        # A split-share or listed corporation's "distribution" is a
        # corporation's payout: its payment in lieu is still deemed.
        for sym, rr in (("FTN.TO", r),
                        ("ZQC.TO", IncomeRules(
                            "canada", corporate_distributions=("ZQC.TO",)))):
            rows = [_dist(sym).to_dict(), _pil(sym).to_dict()]
            self.assertTrue(rr.pil_is_dividend(_pil(sym).to_dict(),
                                               rr.trust_units(rows)), sym)

    @rule("CA-INC-07", "CA-INC-03")
    @rule_absent("CA-INC-03", country="usa")
    @rule("US-INC-01")
    def test_gains_trust_unit_vs_corporate_share(self):
        book = _book("ZQU.TO") + _book("ZQS.TO", with_dist=False)
        r = gains_both(book, year=2025)
        ca = {t["symbol"]: t for t in _pil_rows(r["canada"])}
        # The trust's unit: ordinary income, no deemed dividend.
        self.assertAlmostEqual(ca["ZQU.TO"]["pil"], 40.0)
        self.assertFalse(ca["ZQU.TO"].get("dividend"))
        self.assertNotIn("deemed_dividend", ca["ZQU.TO"])
        # A share (no distribution in the books): s.260 still deems it.
        self.assertEqual(ca["ZQS.TO"]["deemed_dividend"], "ITA s.260")
        self.assertAlmostEqual(ca["ZQS.TO"]["dividend"], 40.0)
        # The US never deems a payment in lieu a dividend.
        for t in _pil_rows(r["usa"]):
            self.assertAlmostEqual(t["pil"], 40.0)
            self.assertNotIn("deemed_dividend", t)

    @rule("CA-INC-07", "CA-INC-03")
    @rule_absent("CA-INC-03", country="usa")
    @rule("US-INC-01")
    def test_views_and_sum_income(self):
        from taxjson.bin.taxjson_sum_income import summarize_income
        rows = [t.to_dict() for t in _book("ZQU.TO")]
        base = {"transactions": rows}
        files = {"work/margin_raw.json": json.dumps(base),
                 "work/margin_base.json": json.dumps(base)}
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, files=files)
            divs = cli_both(p, "divs-sum", "--json")
            dil = cli_both(p, "dil-sum", "--json")
        for c in C.COUNTRIES:
            self.assertEqual(divs[c].returncode, 0, divs[c].stderr)
            self.assertEqual(dil[c].returncode, 0, dil[c].stderr)
            d = json.loads(divs[c].stdout)
            self.assertEqual(d["totals"], {"CAD": 25.0}, c)
            self.assertEqual(d["payments_in_lieu_as_dividends"], 0, c)
            l_ = json.loads(dil[c].stdout)
            self.assertEqual(l_["rows"][0]["treatment"], "ordinary", c)
            self.assertEqual(l_["totals_ordinary"], {"CAD": 40.0}, c)
            s = summarize_income(rows, 2025, IncomeRules(c))[
                "ticker_stats"]["ZQU.TO"]["CAD"]
            self.assertEqual((s["div"], s["pil"]), (25.0, 40.0), c)

    @rule("CA-INC-07")
    def test_tax_logic_states_it(self):
        from taxjson.lib import tax_logic as TL
        rules = {r.id: r.text for _t, rs in TL.rule_sections(
            "canada", {"country": "canada", "year": 2025}) for r in rs}
        self.assertIn("covers shares only", rules["CA-INC-07"])
        self.assertIn("CA-INC-DATE-TRUST", rules["CA-INC-07"])
        self.assertIn("corporation's share", rules["CA-INC-03"])


class TestEstimatePrintsTheTrustAssumption(unittest.TestCase):
    """A2-0828: the printed estimate says a Canadian trust's
    distribution is grossed up as an eligible dividend (CA-EST-TRUST)."""

    @rule("CA-EST-TRUST", "CA-RPT-04")
    def test_printed(self):
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nprovince = "ON"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_gains.json").write_text(json.dumps({
                "summary": {"year": "2025"}, "transactions": [
                    {"action": "DIVIDEND", "symbol": "ZQU.TO",
                     "dividend": 1000.0, "currency": "CAD"}]}))
            env = dict(os.environ, HOME=tmp)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "estimate", "--other-income", "80000"],
                cwd=repo, capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = " ".join(r.stdout.split())
        self.assertIn("trust distributions included", out)
        self.assertIn("a Canadian trust's distribution (ETF, REIT or fund "
                      "units) is grossed up as an eligible dividend", out)


class TestTraceIdsAreUnmaskedOnPurpose(unittest.TestCase):
    """A2-1379 (owner decision): the gains trace prints a row's own id
    unmasked — the handle `--id` takes (a synthetic Kraken-style txid)."""

    def test_trace_line_carries_the_id(self):
        from taxjson.lib.trace_format import render_gain_block
        g = {"symbol": "ZQC", "date": "2025-03-03", "qty": 0.5,
             "gain": 10.0, "raw_gain": 10.0, "disallowed_amount": 0.0,
             "account": "crypto", "id": "LZZZZZ-AAAAA-BBBBBB-fee",
             "trace": ["# x"]}
        out = "\n".join(render_gain_block(g))
        self.assertIn("id=LZZZZZ-AAAAA-BBB", out)


if __name__ == "__main__":
    unittest.main()
