"""`taxjson tips` — tax-efficiency advice for next year (all offline).

Checks pinned: US-LISTING (cross-listed Canadian dividend payer held via
its US line in taxable/TFSA), TFSA-US-DIV (US-domiciled payer in a
TFSA), MAP-GAP (both listings seen, no ticker.map consolidation), plan
inference, exit codes (0 with or without tips: advice for next year
never fails; 2 when the project cannot be read). `taxjson scan` is gone
(no alias); its unused-rule note is `ticker-map --suggest`'s.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "tips", *args],
        cwd=REPO_ROOT, capture_output=True, text=True)


def _suggest_json(root):
    """`taxjson ticker-map --suggest --json` of the project."""
    r = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "ticker-map", "--suggest", "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    return r, json.loads(r.stdout)


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


class TestTips(unittest.TestCase):
    @rule("CA-SCAN-02")
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
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("US-LISTING", r.stdout)
        self.assertIn("ENB.US", r.stdout)
        self.assertIn("ENB.TO", r.stdout)           # the recommendation

    @rule("CA-SCAN-02")
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
        self.assertEqual(r.returncode, 0)
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
        # ...and US-LISTING too: DISTINCT says AEM.TO is another
        # instrument, so "hold AEM.TO instead" would be wrong advice
        # (audit S042-06; this line used to assert the opposite).
        self.assertNotIn("US-LISTING", r.stdout)

    @rule("CA-SCAN-01")
    def test_us_domiciled_payer_in_tfsa(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("tfsa", "sheltered")],
                holdings={"tfsa": _holdings_toml("KO.US")},
                raws={"tfsa": _raw_json("KO.US")})
            r = _run(root)
        self.assertEqual(r.returncode, 0)
        self.assertIn("TFSA-US-DIV", r.stdout)
        self.assertIn("unrecoverable", r.stdout)

    @rule("CA-SCAN-01")
    @rule_absent("CA-SCAN-01", country="usa")
    def test_us_project_has_no_tfsa_or_listing_finding(self):
        # A2-1500: the same books in a US project: no TFSA, no treaty
        # advice, no Canadian-listing advice (the checks are Canada's).
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("tfsa", "sheltered"), ("margin", "taxable")],
                holdings={"tfsa": _holdings_toml("KO.US"),
                          "margin": _holdings_toml("ENB.US")},
                raws={"tfsa": _raw_json("KO.US"),
                      "margin": _raw_json("ENB.US")},
                ticker_map="TOBASE ENB.US ENB.TO\n")
            toml = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                toml.replace('country = "canada"', 'country = "usa"')
                .replace('base_currency = "CAD"', 'base_currency = "USD"'))
            r = _run(root)
        self.assertNotIn("TFSA-US-DIV", r.stdout)
        self.assertNotIn("US-LISTING", r.stdout)
        self.assertNotIn("treaty", r.stdout)

    @rule("CA-SCAN-01")
    def test_rrsp_is_exempt_no_finding(self):
        # Same US payer inside an RRSP: treaty-exempt — no tip.
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("rrsp", "sheltered")],
                holdings={"rrsp": _holdings_toml("KO.US")},
                raws={"rrsp": _raw_json("KO.US")})
            r = _run(root)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("No tips", r.stdout)

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
        self.assertEqual(r.returncode, 0)
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
        self.assertEqual(r.returncode, 0)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["findings"][0]["check"], "US-LISTING")
        self.assertEqual(doc["findings"][0]["account"], "margin")


class TestIssuerNameMatching(unittest.TestCase):
    """The pure helpers behind --online MAP-BAD?/different-root
    MAP-GAP? findings (network calls stay in cmd_tips; these are the
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
        # tips must never suggest mapping one (the receipt ratio
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
    """`ticker-map --suggest` lists the rules no symbol reaches ("Unused
    rules, delete?"). A rule with no STOCK rows is still live when OPTION
    trades carry its root (a root-blind dead-rule check would prune live
    TOBASE rules); an unused rule is listed, never written, and never
    changes the exit code."""

    def test_option_root_keeps_rule_live_and_note_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp,
                accounts=[("margin", "taxable")],
                holdings={"margin": _holdings_toml("XIU.TO")},
                raws={"margin": _raw_json("XIU.TO")},
                ticker_map="TOBASE BCE.US BCE.TO\nTOBASE ZZZ.US ZZZ.TO\n"
                           "GLOBAL X000007 DFDVW.US\n")
            # A parsed-source file (what the dead-rule check reads):
            # a BCE OPTION under the .US root, and the warrant code
            # with a currency suffix.
            (root / "work" / "margin_qt.json").write_text(json.dumps({
                "transactions": [
                    {"action": "BUYSELL", "date": "2026-03-01",
                     "symbol": "BCE251121C00050000.US", "quantity": -1,
                     "currency": "USD", "net_amount": 120.0},
                    {"action": "BUYSELL", "date": "2026-03-02",
                     "symbol": "X000007.US", "quantity": 100,
                     "currency": "USD", "net_amount": 0.0}]}))
            r, doc = _suggest_json(root)
            tips = _run(root, "--json")
        rules = [u["rule"] for u in doc["unused"]]
        self.assertTrue(all(u["kind"] == "unused-rule"
                            and u["certainty"] == "verify"
                            for u in doc["unused"]), doc["unused"])
        # The suffix-less `GLOBAL X000007 DFDVW.US` is NOT live against
        # X000007.US: the engine matches a rule's FROM exactly, so it is
        # listed with a hint to write the suffixed form (S053-12 — this
        # test used to pin scan calling it live).
        self.assertEqual(len(rules), 2, rules)
        self.assertTrue(rules[0].startswith("X000007 -> DFDVW.US"), rules)
        self.assertIn("X000007.US", doc["unused"][0]["hint"])
        self.assertEqual(rules[1], "ZZZ.US -> ZZZ.TO")
        self.assertEqual(doc["unused"][1]["line"], "TOBASE ZZZ.US ZZZ.TO")
        self.assertEqual(r.returncode, 0, "an unused rule never fails")
        # Tips no longer carries the map's notes.
        self.assertNotIn("notes", json.loads(tips.stdout))
        self.assertEqual(json.loads(tips.stdout)["schema_version"], 2)


class TestScanIsGone(unittest.TestCase):
    """`taxjson scan` was renamed `tips` and removed with no alias; tips
    exits 0 with tips, 2 when it cannot read the project."""

    def test_scan_is_an_invalid_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, accounts=[("margin", "taxable")],
                            holdings={"margin": _holdings_toml("XIU.TO")},
                            raws={"margin": _raw_json("XIU.TO")})
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "scan"],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("invalid choice: 'scan'", r.stderr)

    def test_no_holdings_reports_is_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, accounts=[("margin", "taxable")],
                            holdings={}, raws={})
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "x.csv").write_text("a,b\n")
            r = _run(root)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("holdings", r.stderr)

    def test_tips_listing_ends_with_what_it_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp, accounts=[("tfsa", "sheltered")],
                holdings={"tfsa": _holdings_toml("KO.US")},
                raws={"tfsa": _raw_json("KO.US")})
            r = _run(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith("TIPS — "), lines[0])
        self.assertEqual(lines[-1], "1 tip(s) for next year — none "
                                    "changes a number of this year.")
