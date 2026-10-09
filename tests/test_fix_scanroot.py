"""`taxjson tips` (scan before) stops asking about pairs that are evidently not one
security (owner, 2026-10-07).

Interlisted shares usually keep their letters (QZX.TO / QZX.US), so a
same-root US and Canadian pair with no ticker.map line stays a MAP-GAP
candidate, and US-LISTING may still suggest the Canadian line — but not
when the exports show the two apart: the Canadian line is a depositary
receipt (a receipt word of markets.toml [lists] receipt_words in its
name, or a receipt venue), or the names name different companies
(cross_listings.companies_differ). Such a pair needs no DISTINCT line.
The MAP-GAP message says whether the names agree ("carry the same
name"), differ in form, or were not compared (verify first). DISTINCT
still silences a pair. Synthetic books.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "tips", *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env=dict(os.environ, TAXJSON_OFFLINE="1"))


def _holdings(*symbols):
    out = ['schema_version = "1.2"']
    for sym in symbols:
        out.append(f'[[holding]]\nsymbol = "{sym}"\nquantity = 100.0\n'
                   f'currency = "USD"\ntotal_cost = 1000.0\n'
                   f'cost_per_share = 10.0')
    return "\n".join(out) + "\n"


def _divs(*symbols):
    return json.dumps({"transactions": [
        {"action": "DIVIDEND", "date": "2026-03-01", "symbol": s,
         "quantity": 0, "gross_amount": 10.0, "net_amount": 10.0,
         "currency": "USD"} for s in symbols]})


def _parsed(names):
    """An IB parsed export whose buys carry each listing's name."""
    return json.dumps({"transactions": [
        {"action": "BUYSELL", "date": "2026-02-02", "symbol": s,
         "quantity": 10.0, "price": 10.0, "currency":
         "CAD" if s.endswith(".TO") else "USD", "security_name": n}
        for s, n in names.items()]})


def _project(tmp, *, names=None, ticker_map=None, pays=("QZX.US",)):
    """margin (taxable) holds QZX.US, which pays; the rrsp holds QZX.TO.
    `names`: {account: {symbol: name}} written as IB parsed exports."""
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "reports").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\n[accounts.margin]\ntype = "taxable"\n'
        '[accounts.rrsp]\ntype = "sheltered"\n')
    (root / "reports" / "margin_holdings.toml").write_text(
        _holdings("QZX.US"))
    (root / "reports" / "rrsp_holdings.toml").write_text(
        _holdings("QZX.TO"))
    (root / "work" / "margin_raw.json").write_text(_divs(*pays))
    (root / "work" / "rrsp_raw.json").write_text(_divs())
    for acct, nm in (names or {}).items():
        (root / "work" / f"{acct}_ib.json").write_text(_parsed(nm))
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map)
    return root


_SAME = {"margin": {"QZX.US": "QZX ENERGY CORP"},
         "rrsp": {"QZX.TO": "QZX ENERGY CORP"}}


class TestMapGapEvidence(unittest.TestCase):

    def test_cdr_is_not_a_twin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, names={
                "margin": {"QZX.US": "QZX ENERGY CORP"},
                "rrsp": {"QZX.TO": "QZX ENERGY CDR (CAD HEDGED)"}})
            r = _run(root)
        self.assertNotIn("MAP-GAP", r.stdout, r.stdout)
        self.assertNotIn("US-LISTING", r.stdout, r.stdout)

    def test_receipt_word_alone_is_not_a_twin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, names={
                "rrsp": {"QZX.TO": "QZX ENERGY CORP CDR"}})
            r = _run(root)
        self.assertNotIn("MAP-GAP", r.stdout, r.stdout)
        self.assertNotIn("US-LISTING", r.stdout, r.stdout)

    def test_another_issuer_is_not_a_twin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, names={
                "margin": {"QZX.US": "QZX REALTY TRUST INC"},
                "rrsp": {"QZX.TO": "SAMPLEX US DOLLAR CURRENCY ETF"}})
            r = _run(root)
        self.assertNotIn("MAP-GAP", r.stdout, r.stdout)
        self.assertNotIn("US-LISTING", r.stdout, r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_receipt_venue_is_not_a_twin(self):
        # A Cboe Canada (.NE) line under the US ticker is a receipt.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "reports" / "rrsp_holdings.toml").write_text(
                _holdings("QZX.NE"))
            r = _run(root)
        self.assertNotIn("MAP-GAP", r.stdout, r.stdout)
        self.assertNotIn("US-LISTING", r.stdout, r.stdout)

    @rule("CA-SCAN-02")
    def test_equal_names_are_flagged_with_the_tobase_line(self):
        # An interlisted share that keeps its letters: still flagged.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, names=_SAME)
            r = _run(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MAP-GAP", r.stdout)
        self.assertIn("QZX.US and QZX.TO carry the same name "
                      "('QZX ENERGY CORP')", r.stdout)
        self.assertIn("ticker.map does not join them", r.stdout)
        self.assertIn("`TOBASE QZX.US QZX.TO`", r.stdout)
        self.assertIn("`DISTINCT QZX.US QZX.TO`", r.stdout)
        self.assertNotIn("both listings appear", r.stdout)
        self.assertIn("US-LISTING", r.stdout)
        self.assertIn("hold QZX.TO instead", r.stdout)
        self.assertNotIn("verify QZX.TO", r.stdout)

    @rule("CA-SCAN-02")
    def test_names_unknown_is_a_candidate_to_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            r = _run(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MAP-GAP", r.stdout)
        self.assertIn("names not compared (no security name for either "
                      "listing) — verify", r.stdout)
        self.assertIn("`TOBASE QZX.US QZX.TO`", r.stdout)
        self.assertIn("`DISTINCT QZX.US QZX.TO`", r.stdout)
        self.assertIn("hold QZX.TO instead", r.stdout)
        self.assertIn("verify QZX.TO is the same security", r.stdout)

    def test_name_on_one_side_only_is_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, names={
                "margin": {"QZX.US": "QZX ENERGY CORP"}})
            r = _run(root)
        self.assertIn("no security name for QZX.TO", r.stdout)

    def test_names_unequal_in_form_is_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, names={
                "margin": {"QZX.US": "QZX ENERGY CORP"},
                "rrsp": {"QZX.TO": "QZX ENERGY LTD"}})
            r = _run(root)
        self.assertIn("MAP-GAP", r.stdout)
        self.assertIn("share their letters but the names are not equal",
                      r.stdout)

    def test_distinct_still_silences(self):
        for names in (_SAME, None):
            with tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp, names=names,
                                ticker_map="DISTINCT QZX.US QZX.TO\n")
                r = _run(root)
            self.assertNotIn("MAP-GAP", r.stdout)
            self.assertNotIn("US-LISTING", r.stdout)

    @rule("CA-SCAN-02")
    def test_a_map_ruling_names_the_twin_without_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, ticker_map="TOBASE QZX.US QZX.TO\n")
            r = _run(root)
        self.assertIn("hold QZX.TO instead", r.stdout)
        self.assertNotIn("verify", r.stdout)
        self.assertNotIn("MAP-GAP", r.stdout)


def _lint(taxable, sheltered):
    with tempfile.TemporaryDirectory() as tmp:
        t, s = Path(tmp) / "t.json", Path(tmp) / "s.json"
        t.write_text(json.dumps({"transactions": taxable}))
        s.write_text(json.dumps({"transactions": sheltered}))
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_lint_crosslistings",
             "--taxable", str(t), "--sheltered", str(s)],
            cwd=REPO_ROOT, capture_output=True, text=True)


def _buy(sym, desc=None):
    r = {"action": "BUYSELL", "date": "2026-01-05", "symbol": sym,
         "quantity": 5.0, "price": 10.0, "currency": "CAD"}
    if desc:
        r["description"] = desc
    return r


class TestLintCrossListingsEvidence(unittest.TestCase):
    """reports/crosslistings.rpt (written every run): the same rule."""

    def test_different_companies_are_ok(self):
        r = _lint([], [_buy("QZX.TO", "SAMPLEX US DOLLAR CURRENCY ETF"),
                       _buy("QZX.US", "QZX REALTY TRUST INC")])
        self.assertIn("[OK] QZX", r.stdout)
        self.assertIn("different companies", r.stdout)

    def test_cdr_word_from_markets_toml_is_ok(self):
        r = _lint([], [_buy("QZX.TO", "QZX ENERGY CORP CDR"),
                       _buy("QZX.US", "QZX ENERGY CORP")])
        self.assertIn("[OK] QZX", r.stdout)

    def test_same_name_is_review(self):
        r = _lint([], [_buy("QZX.TO", "QZX ENERGY CORP"),
                       _buy("QZX.US", "QZX ENERGY CORP")])
        self.assertIn("[REVIEW] QZX", r.stdout)
        self.assertIn("same name on both listings", r.stdout)

    def test_unknown_names_stay_review_to_verify(self):
        r = _lint([], [_buy("QZX.TO"), _buy("QZX.US")])
        self.assertIn("[REVIEW] QZX", r.stdout)
        self.assertIn("names not compared", r.stdout)


class TestSuggestConditionalHintEvidence(unittest.TestCase):
    """`ticker-map --suggest` offers a parser's conditional TOBASE hint
    when the books hold both listings — but not when the exports show
    the two apart (cross_listings.shown_apart)."""

    def _root(self, td, tfsa_name):
        from test_fix_suggest_conditional_hints import _project as hp, _tx
        row = dict(_tx("QZLR.TO"), security_name=tfsa_name)
        root = hp(td, other_rows=[row])
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.tfsa]\ntype = "sheltered"\n')
        return root

    def test_a_cdr_is_not_offered(self):
        from taxjson.lib import ticker_map_suggest as TS
        with tempfile.TemporaryDirectory() as td:
            offer, _ = TS.pending(self._root(
                td, "QZLR RESEARCH CDR (CAD HEDGED)"))
        self.assertEqual(offer, [])

    def test_another_company_is_not_offered(self):
        from taxjson.lib import ticker_map_suggest as TS
        with tempfile.TemporaryDirectory() as td:
            offer, _ = TS.pending(self._root(
                td, "SAMPLEX US DOLLAR CURRENCY ETF"))
        self.assertEqual(offer, [])

    def test_the_same_name_is_offered(self):
        from taxjson.lib import ticker_map_suggest as TS
        with tempfile.TemporaryDirectory() as td:
            offer, _ = TS.pending(self._root(td, "QZLR RESEARCH CORP"))
        self.assertEqual([s.line for s in offer],
                         ["TOBASE QZLR.US QZLR.TO"])


if __name__ == "__main__":
    unittest.main()
