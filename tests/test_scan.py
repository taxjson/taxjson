"""`taxjson scan` — tax-efficiency lint (all offline).

Checks pinned: US-LISTING (cross-listed Canadian dividend payer held via
its US line in taxable/TFSA), TFSA-US-DIV (US-domiciled payer in a
TFSA), MAP-GAP (both listings seen, no ticker.map consolidation), plan
inference, exit codes (1 findings / 0 clean).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "scan", *args],
        cwd=REPO_ROOT, capture_output=True, text=True)


def _holdings_toml(*symbols):
    out = ['schema_version = "1.2"']
    for sym in symbols:
        out.append(f'[[holding]]\nsymbol = "{sym}"\nquantity = 100.0\n'
                   f'currency = "USD"\ntotal_cost = 1000.0\n'
                   f'cost_per_share = 10.0')
    return "\n".join(out) + "\n"


def _raw_json(*div_symbols):
    return json.dumps({"transactions": [
        {"action": "DIVIDEND", "date": "2026-03-01", "symbol": s,
         "quantity": 0, "gross_amount": 10.0, "net_amount": 10.0,
         "currency": "USD"} for s in div_symbols]})


def _project(tmp, *, accounts, holdings, raws, ticker_map=None):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "reports").mkdir()
    acct_toml = "".join(
        f'[accounts.{n}]\ntype = "{t}"\n' for n, t in accounts)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\n' + acct_toml)
    for name, text in holdings.items():
        (root / "reports" / f"{name}_holdings.toml").write_text(text)
    for name, text in raws.items():
        (root / "work" / f"{name}_raw.json").write_text(text)
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map)
    return root


class TestScan(unittest.TestCase):
    def test_us_listing_of_canadian_issuer_in_taxable(self):
        # ENB.US held in margin; the map knows ENB.US == ENB.TO; pays divs.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("margin", "taxable")],
                holdings={"margin": _holdings_toml("ENB.US")},
                raws={"margin": _raw_json("ENB.US")},
                ticker_map="TOBASE ENB.US ENB.TO\n")
            r = _run(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("US-LISTING", r.stdout)
        self.assertIn("ENB.US", r.stdout)
        self.assertIn("ENB.TO", r.stdout)           # the recommendation

    def test_twin_detected_from_data_without_map(self):
        # No map entry, but the .TO line is held in another account —
        # still a Canadian issuer via US line, AND a MAP-GAP.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("margin", "taxable"), ("rrsp", "sheltered")],
                holdings={"margin": _holdings_toml("AEM.US"),
                          "rrsp": _holdings_toml("AEM.TO")},
                raws={"margin": _raw_json("AEM.US")})
            r = _run(root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("US-LISTING", r.stdout)
        self.assertIn("MAP-GAP", r.stdout)
        self.assertIn("AEM.TO/AEM.US", r.stdout)

    def test_distinct_ruling_silences_map_gap(self):
        # Same book as the twin test, but the user has RECORDED that
        # the pair is deliberately separate (a CDR vs its underlying):
        # `DISTINCT` must silence the MAP-GAP nag while every other
        # check keeps running.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("margin", "taxable"), ("rrsp", "sheltered")],
                holdings={"margin": _holdings_toml("AEM.US"),
                          "rrsp": _holdings_toml("AEM.TO")},
                raws={"margin": _raw_json("AEM.US")},
                ticker_map="DISTINCT AEM.US AEM.TO\n")
            r = _run(root)
        self.assertNotIn("MAP-GAP", r.stdout)
        self.assertIn("US-LISTING", r.stdout)   # unrelated check lives

    def test_us_domiciled_payer_in_tfsa(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("tfsa", "sheltered")],
                holdings={"tfsa": _holdings_toml("KO.US")},
                raws={"tfsa": _raw_json("KO.US")})
            r = _run(root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("TFSA-US-DIV", r.stdout)
        self.assertIn("unrecoverable", r.stdout)

    def test_rrsp_is_exempt_no_finding(self):
        # Same US payer inside an RRSP: treaty-exempt — clean scan.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("rrsp", "sheltered")],
                holdings={"rrsp": _holdings_toml("KO.US")},
                raws={"rrsp": _raw_json("KO.US")})
            r = _run(root)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("No findings", r.stdout)

    def test_mapped_pair_is_not_a_map_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("margin", "taxable"), ("rrsp", "sheltered")],
                holdings={"margin": _holdings_toml("AEM.TO"),
                          "rrsp": _holdings_toml("AEM.US")},
                raws={},
                ticker_map="TOBASE AEM.US AEM.TO\n")
            r = _run(root)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("MAP-GAP", r.stdout)

    def test_non_dividend_payer_not_flagged(self):
        # US line of a Canadian issuer but NO dividends observed — the
        # dividend-tax check stays quiet (MAP coverage still applies).
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("margin", "taxable")],
                holdings={"margin": _holdings_toml("SHOP.US")},
                raws={"margin": _raw_json()},
                ticker_map="TOBASE SHOP.US SHOP.TO\n")
            r = _run(root)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("US-LISTING", r.stdout)

    def test_plan_override_beats_name(self):
        # An account NAMED 'usd_account' configured as plan = "tfsa" is
        # checked as a TFSA.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "reports").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.usd_account]\ntype = "sheltered"\n'
                'plan = "tfsa"\n')
            (root / "reports" / "usd_account_holdings.toml").write_text(
                _holdings_toml("KO.US"))
            (root / "work" / "usd_account_raw.json").write_text(
                _raw_json("KO.US"))
            r = _run(root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("TFSA-US-DIV", r.stdout)

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("margin", "taxable")],
                holdings={"margin": _holdings_toml("ENB.US")},
                raws={"margin": _raw_json("ENB.US")},
                ticker_map="TOBASE ENB.US ENB.TO\n")
            r = _run(root, "--json")
        self.assertEqual(r.returncode, 1)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["findings"][0]["check"], "US-LISTING")
        self.assertEqual(doc["findings"][0]["account"], "margin")


class TestIssuerNameMatching(unittest.TestCase):
    """The pure helpers behind --online MAP-BAD?/different-root
    MAP-GAP? findings (network calls stay in cmd_scan; these are the
    decision rules)."""

    def test_norm_strips_exchange_boilerplate(self):
        from taxjson.bin.taxjson_run import _norm_issuer_name
        self.assertEqual(_norm_issuer_name("B2Gold Corp."), "B2GOLD")
        self.assertEqual(_norm_issuer_name("The Toronto-Dominion Bank"),
                         "TORONTO DOMINION BANK")
        self.assertEqual(
            _norm_issuer_name("Agnico Eagle Mines Limited"),
            "AGNICO EAGLE MINES")

    def test_same_issuer_across_listing_styles(self):
        from taxjson.bin.taxjson_run import _issuer_names_match
        self.assertTrue(_issuer_names_match(
            "B2Gold Corp.", "B2GOLD CORP"))
        self.assertTrue(_issuer_names_match(
            "Agnico Eagle Mines Limited", "AGNICO EAGLE MINES LTD"))
        # Multi-token prefix: same issuer, one side fuller.
        self.assertTrue(_issuer_names_match(
            "Agnico Eagle", "Agnico Eagle Mines"))

    def test_different_issuers_do_not_match(self):
        from taxjson.bin.taxjson_run import _issuer_names_match
        self.assertFalse(_issuer_names_match(
            "B2Gold Corp.", "Barrick Gold Corporation"))
        # Shared FIRST token only is not enough — issuer families.
        self.assertFalse(_issuer_names_match(
            "Brookfield Corporation",
            "Brookfield Renewable Partners LP"))

    def test_missing_name_never_accuses(self):
        from taxjson.bin.taxjson_run import _issuer_names_match
        self.assertTrue(_issuer_names_match("", "B2Gold Corp."))
        self.assertTrue(_issuer_names_match("Inc.", "B2Gold Corp."))

    def test_cdr_names_recognized(self):
        # A CDR is the SAME issuer but NOT a listing equivalent — the
        # scan must never suggest mapping one (the receipt ratio
        # floats, so no TOBASE ratio can ever be right).
        from taxjson.bin.taxjson_run import _is_cdr_name
        self.assertTrue(_is_cdr_name(
            "UnitedHealth Group CDR (CAD Hedged)"))
        self.assertTrue(_is_cdr_name(
            "Amazon.com Canadian Depositary Receipts"))
        self.assertTrue(_is_cdr_name("Nvidia CDR (CAD-Hedged)"))
        self.assertFalse(_is_cdr_name(
            "UnitedHealth Group Incorporated"))
        self.assertFalse(_is_cdr_name("Agnico Eagle Mines Limited"))
        # 'CDR' inside a word is not the token.
        self.assertFalse(_is_cdr_name("Cedrus Holdings"))

    def test_distinct_parses_into_map(self):
        import tempfile as _tf
        from pathlib import Path as _P
        from taxjson.bin.taxjson_ticker_map import load_map_file
        with _tf.TemporaryDirectory() as td:
            p = _P(td) / "ticker.map"
            p.write_text("TOBASE AEM.US AEM.TO\n"
                         "DISTINCT UNH.US UNH.TO\n")
            tm = load_map_file(p)
        self.assertEqual(tm.tobase, {"AEM.US": "AEM.TO"})
        self.assertEqual(tm.distinct,
                         {frozenset(("UNH.US", "UNH.TO"))})


if __name__ == "__main__":
    unittest.main()


class TestMapUnusedIsRootAware(unittest.TestCase):
    """A rule with no STOCK rows is still live when OPTION trades carry
    its root (2026-09-15: a root-blind dead-rule check pruned ten live
    TOBASE rules from a real map). MAP-UNUSED is a note, never a
    finding, and never changes the exit code."""

    def test_option_root_keeps_rule_live_and_note_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("margin", "taxable")],
                holdings={"margin": _holdings_toml("XIU.TO")},
                raws={"margin": _raw_json("XIU.TO")},
                ticker_map="TOBASE BCE.US BCE.TO\nTOBASE ZZZ.US ZZZ.TO\n"
                           "GLOBAL D056068 DFDVW.US\n")
            # A parsed-source file (what the dead-rule check reads):
            # a BCE OPTION under the .US root, and the warrant code
            # with a currency suffix.
            (root / "work" / "margin_qt.json").write_text(json.dumps({
                "transactions": [
                    {"action": "BUYSELL", "date": "2026-03-01",
                     "symbol": "BCE251121C00050000.US", "quantity": -1,
                     "currency": "USD", "net_amount": 120.0},
                    {"action": "BUYSELL", "date": "2026-03-02",
                     "symbol": "D056068.US", "quantity": 100,
                     "currency": "USD", "net_amount": 0.0}]}))
            r = _run(root, "--json")
        doc = json.loads(r.stdout)
        rules = [n["rule"] for n in doc["notes"]]
        self.assertEqual(rules, ["ZZZ.US -> ZZZ.TO"], rules)
        self.assertEqual(r.returncode, 0, "notes never fail the scan")

