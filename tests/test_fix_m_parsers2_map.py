"""Medium-round fixes: ticker.map and taxjson-merge (parsers2-map; the
`taxjson fetch` ones moved to packages/taxjson-fetch with the fetcher).
Synthetic data only."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_ticker_map import (
    _parse_map_file, load_map_file, map_file_problems, map_symbol,
    merge_renames)
from tax_rules import rule
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _map(text: str, bom: bool = False) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "ticker.map"
    p.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode())
    return p


class TestTickerMapCase(unittest.TestCase):
    """S009-05 / S053-02: a lower- or mixed-case rule used to be a
    silent no-op in the pipeline while scan (which upper-cases) called
    it live."""

    def test_lowercase_rule_symbols_are_upper_cased(self):
        tm = load_map_file(_map("TOBASE xyz.us xyz.to\nGLOBAL OldX.TO "
                                "newx.to\nDELETE junk.to\n"
                                "DISTINCT unh.us UNH.to\n"))
        ren = merge_renames(tm, to_base=True)
        self.assertEqual(map_symbol("XYZ.US", ren), "XYZ.TO")
        self.assertEqual(map_symbol("OLDX.TO", ren), "NEWX.TO")
        self.assertEqual(tm.delete, {"JUNK.TO"})
        self.assertEqual(tm.distinct, {frozenset(("UNH.US", "UNH.TO"))})


class TestTickerMapChains(unittest.TestCase):
    """R1-139: renames were one hop — OLD.US stopped at NEW.US while
    NEW.US went on to NEW.TO, splitting one ACB pool."""

    def test_chain_resolves_to_fixed_point(self):
        tm = load_map_file(_map("GLOBAL OLD.US NEW.US\n"
                                "TOBASE NEW.US NEW.TO\n"))
        base = merge_renames(tm, to_base=True)
        self.assertEqual(map_symbol("OLD.US", base), "NEW.TO")
        self.assertEqual(map_symbol("NEW.US", base), "NEW.TO")
        # options follow the underlying's resolved target
        self.assertEqual(map_symbol("OLD260116C00150000.US", base),
                         "NEW260116C00150000.TO")
        # the GLOBAL-only (holdings) view stops where GLOBAL stops
        self.assertEqual(merge_renames(tm, to_base=False),
                         {"OLD.US": "NEW.US"})
        self.assertEqual(map_file_problems(_map("GLOBAL OLD.US NEW.US\n"
                                                "TOBASE NEW.US NEW.TO\n")),
                         [])

    def test_cycle_is_a_problem(self):
        probs = map_file_problems(_map("GLOBAL A.US B.US\n"
                                       "GLOBAL B.US A.US\n"))
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("cycle", probs[0])

    def test_conflicting_targets_for_one_from_are_a_problem(self):
        probs = map_file_problems(_map("TOBASE A.US A.TO\n"
                                       "TOBASE A.US A.NE\n"))
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("ticker.map:1", probs[0])
        self.assertIn("ticker.map:2", probs[0])
        probs = map_file_problems(_map("GLOBAL A.US X.US\n"
                                       "TOBASE A.US A.TO\n"))
        self.assertEqual(len(probs), 1, probs)
        # the same rule twice is harmless
        self.assertEqual(map_file_problems(_map("TOBASE A.US A.TO\n"
                                                "JOURNAL A.US A.TO\n")),
                         [])

    def test_inline_comment_and_bom(self):
        tm = load_map_file(_map("GLOBAL AEM.US AEM.TO # moved listing\n",
                                bom=True))
        self.assertEqual(merge_renames(tm, to_base=False),
                         {"AEM.US": "AEM.TO"})
        tm = load_map_file(_map("GLOBAL AEM.US AEM.TO#x\n"))
        self.assertEqual(tm.glob, {"AEM.US": "AEM.TO"})

    def test_extra_token_is_a_problem(self):
        probs = map_file_problems(_map("GLOBAL A.US B.US C.US\n"))
        self.assertEqual(len(probs), 1, probs)


class TestDistinctConflict(unittest.TestCase):
    """S053-03: DISTINCT UNH.US UNH.TO next to TOBASE UNH.US UNH.TO —
    the engine pooled them while sell-check said they stay separate."""

    @rule("CA-ACB-04")
    @rule("US-BASIS-06")
    def test_distinct_pair_joined_by_rename_is_a_problem(self):
        probs = map_file_problems(_map("TOBASE UNH.US UNH.TO\n"
                                       "DISTINCT UNH.US UNH.TO\n"))
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("DISTINCT", probs[0])
        self.assertIn("ticker.map:1", probs[0])
        self.assertIn("ticker.map:2", probs[0])

    def test_distinct_pair_joined_through_a_chain(self):
        probs = map_file_problems(_map("GLOBAL A.US B.US\n"
                                       "TOBASE B.US C.TO\n"
                                       "DISTINCT A.US C.TO\n"))
        self.assertEqual(len(probs), 1, probs)

    def test_plain_distinct_is_fine(self):
        self.assertEqual(map_file_problems(_map("DISTINCT UNH.US UNH.TO\n"
                                                "TOBASE QBC.US QBC.TO\n")),
                         [])


class TestScanMapUnused(unittest.TestCase):
    """R1-139: scan called a rule reached through a chain unused.
    S053-12: scan called a bare `GLOBAL QQOL QQNW` live (it matched
    QQOL.US by root) while the engine, which matches FROM exactly,
    applied nothing."""

    def _scan(self, ticker_map, symbols):
        from test_scan import _project, _raw_json, _holdings_toml, _run
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, accounts=[("margin", "taxable")],
                            holdings={"margin": _holdings_toml("XIU.TO")},
                            raws={"margin": _raw_json("XIU.TO")},
                            ticker_map=ticker_map)
            (root / "work" / "margin_qt.json").write_text(json.dumps({
                "transactions": [
                    {"action": "BUYSELL", "date": "2026-03-01",
                     "symbol": s, "quantity": 1, "currency": "USD",
                     "net_amount": 10.0} for s in symbols]}))
            r = _run(root, "--json")
        return [n["rule"] for n in json.loads(r.stdout)["notes"]]

    def test_rule_reached_through_a_chain_is_live(self):
        rules = self._scan("GLOBAL OLD.US NEW.US\nTOBASE NEW.US NEW.TO\n",
                           ["OLD.US"])
        self.assertEqual(rules, [], rules)

    def test_bare_rule_matching_only_suffixed_symbols_is_unused(self):
        rules = self._scan("GLOBAL QQOL QQNW\n", ["QQOL.US"])
        self.assertEqual(len(rules), 1, rules)
        self.assertTrue(rules[0].startswith("QQOL -> QQNW"), rules)
        self.assertIn("QQOL.US", rules[0])

    def test_bare_rule_matching_a_bare_symbol_is_live(self):
        # crypto books carry bare symbols; `GLOBAL ETH2 ETH` works
        self.assertEqual(self._scan("GLOBAL ETH2 ETH\n", ["ETH2"]), [])

    def test_option_root_keeps_rule_live(self):
        self.assertEqual(self._scan("TOBASE QBC.US QBC.TO\n",
                                    ["QBC251121C00050000.US"]), [])


def _run_project(files):
    import os
    tmp = Path(tempfile.mkdtemp())
    (tmp / "inputs" / "margin").mkdir(parents=True)
    (tmp / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\noption_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (tmp / "inputs" / "margin" / "x.tt").write_text(
        "BUYSELL 2025-01-02 09:30:00 A.US 1 USD 1 1 0\n")
    for name, text in files.items():
        (tmp / name).write_text(text)
    env = dict(os.environ, TAXJSON_OFFLINE="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(tmp),
         "run", "--no-input"], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


class TestRunRefusesContradictoryMaps(unittest.TestCase):
    def test_cycle_refused_up_front(self):
        r = _run_project({"ticker.map": "GLOBAL A.US B.US\n"
                                        "GLOBAL B.US A.US\n"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("rename cycle", r.stderr + r.stdout)

    def test_malformed_security_override_refused(self):
        # S053-04 (second half): an EXTRACT line (once
        # ticker_extraction_overrides.txt) that cannot be read.
        r = _run_project({"ticker.map":
                          "EXTRACT SOME FUND, USD, FUND.TO\n"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ticker.map:1: EXTRACT needs",
                      r.stderr + r.stdout)


class TestLegacyMergeRefusesPartial(unittest.TestCase):
    """R1-260 / R1-295: taxjson-merge (crypto books, blended base,
    sheltered_base, audit tie-out) printed 'cannot read' and exited 0
    with the unreadable file's rows missing — `run --fast` over a
    truncated cached Coinbase book dropped half the crypto gains."""

    def _merge(self, *paths):
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_merge",
             *map(str, paths)], capture_output=True, text=True)

    def _good(self, d):
        p = Path(d) / "a.json"
        p.write_text(json.dumps({"transactions": [
            {"action": "BUYSELL", "date": "2025-01-15", "symbol": "X",
             "quantity": 1, "currency": "CAD", "net_amount": 1.0}]}))
        return p

    def test_unreadable_input_is_fatal_and_emits_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.json"
            bad.write_text('{"transactions": [{"action": "BUY')
            r = self._merge(self._good(d), bad)
        self.assertEqual(r.returncode, 2)       # A2-0164
        self.assertEqual(r.stdout, "")
        self.assertIn("taxjson-merge: error: cannot read", r.stderr)
        self.assertIn("partial merge", " ".join(r.stderr.split()))

    def test_missing_input_is_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._merge(self._good(d), Path(d) / "ghost.json")
        self.assertEqual(r.returncode, 2)       # A2-0164
        self.assertEqual(r.stdout, "")
        self.assertIn("not found", r.stderr)

    def test_good_inputs_still_merge(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._merge(self._good(d))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(r.stdout)["transactions"]), 1)


if __name__ == "__main__":
    unittest.main()
