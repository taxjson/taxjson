"""One mapping file and the year data in taxjson.toml (owner request).

- ticker.map carries three lookup keywords besides the renames: QUOTE
  (was yf_ticker.map), CRYPTO (crypto_ticker.map) and EXTRACT
  (ticker_extraction_overrides.txt). tv_exchange.map (the removed
  TradingView export) is only renamed by migrate (test_fix_notv).
- taxjson.toml carries [estimate] amt_carryover (amt_carryover.txt),
  [carryover] claimed (claimed_losses.txt), [[capital_gains_dividends]]
  (capital_gains_dividends.map) and [[distributions]] (distributions.map),
  checked by every config reader.
- `taxjson migrate` converts an old project (appending, never rewriting;
  old files renamed *.migrated); while an old file is present every other
  command stops (exit 2).

Round trips: a project written directly in the new format and the same
project written in the old files and migrated build identical books and
views. Synthetic data only.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli

ACCT = "55500001"  # pii-ok

_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
       "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
       "Activity Type,Account Type\n")

_ROWS = [
    # XAW.TO: bought, a reinvested distribution (ACB up), sold.
    "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XAW.TO,XAW ETF,"
    "100,10.00,-1000.00,0.00,-1000.00,CAD,{a},Trades,Individual margin",
    "2025-11-20 09:30:00 AM,2025-11-21 12:00:00 AM,Sell,XAW.TO,XAW ETF,"
    "-100,12.00,1200.00,0.00,1200.00,CAD,{a},Trades,Individual margin",
    # ZZS.TO: two dividends, one of them a box 18 capital-gains dividend.
    "2025-01-03 09:30:00 AM,2025-01-06 12:00:00 AM,Buy,ZZS.TO,"
    "ZZS SPLIT CORP CL A WE ACTED AS AGENT,1000,10.00,-10000.00,0.00,"
    "-10000.00,CAD,{a},Trades,Individual margin",
    "2025-03-10 12:00:00 AM,2025-03-10 12:00:00 AM,DIV,ZZS.TO,"
    "ZZS SPLIT CORP CL A DIV ON 1000 SHS REC 02/28/25 PAY 03/10/25,0,"
    "0.00,0.00,0.00,100.00,CAD,{a},Dividends,Individual margin",
    "2025-04-10 12:00:00 AM,2025-04-10 12:00:00 AM,DIV,ZZS.TO,"
    "ZZS SPLIT CORP CL A DIV ON 1000 SHS REC 03/31/25 PAY 04/10/25,0,"
    "0.00,0.00,0.00,100.00,CAD,{a},Dividends,Individual margin",
    # A trust unit the parser would call ZZQ.TO: EXTRACT says ZZQ.UN.TO.
    "2025-05-01 09:30:00 AM,2025-05-02 12:00:00 AM,Buy,ZZQ,"
    "ZZQ REIT TRUST UNITS,10,10.00,-100.00,0.00,-100.00,CAD,{a},"
    "Trades,Individual margin",
]

_BASE_TOML = (
    '[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
    'source_currencies = []\nprovince = "ON"\n\n'
    '# my own notes stay\n[estimate]\nother_income = 60000  # salary\n\n'
    '[accounts.margin]\ntype = "taxable"\n')

# The old files.
_OLD = {
    "distributions.map": "# reinvested\nXAW.TO 2025-06-30 0.50\n",
    "capital_gains_dividends.map": "ZZS.TO 2025-03-10 all\n",
    "amt_carryover.txt": "2023 1,200.50\n",
    "claimed_losses.txt": "2024 2500\n2024 100\n",
    "ticker_extraction_overrides.txt":
        "# the trust units\nZZQ REIT Trust Units | cad | ZZQ.UN.TO\n",
    "tv_exchange.map": "XAW.TO TSX\nZZS.TO NEO\n",
    "yf_ticker.map": "ZZS.TO ZZS.TO\nOLD.TO NEWCO 0.25 # merger\n"
                     "ZZS.TO ZZS-A.TO\n",
    "crypto_ticker.map": "foo FOO123\n",
}

# The same content written directly in the new form.
_NEW_MAP = ("TOBASE AEM.US AEM.TO\n"
            "QUOTE ZZS.TO ZZS-A.TO\nQUOTE OLD.TO NEWCO 0.25\n"
            "CRYPTO FOO FOO123\n"
            "EXTRACT ZZQ REIT Trust Units | CAD | ZZQ.UN.TO\n")
_NEW_TOML = (
    '[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
    'source_currencies = []\nprovince = "ON"\n\n'
    '[estimate]\nother_income = 60000\n'
    'amt_carryover = { 2023 = 1200.50 }\n\n'
    '[accounts.margin]\ntype = "taxable"\n\n'
    '[carryover]\nclaimed = { 2024 = 2600 }\n\n'
    '[[capital_gains_dividends]]\nsymbol = "ZZS.TO"\ndate = 2025-03-10\n'
    'amount = "all"\n\n'
    '[[distributions]]\nsymbol = "XAW.TO"\nrecord_date = 2025-06-30\n'
    'per_share = 0.5\n')


def _inputs(root: Path) -> None:
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT + "\n".join(r.format(a=ACCT) for r in _ROWS) + "\n")


def _old_project(root: Path) -> Path:
    _inputs(root)
    (root / "taxjson.toml").write_text(_BASE_TOML)
    (root / "ticker.map").write_text("TOBASE AEM.US AEM.TO  # keep me\n")
    for name, text in _OLD.items():
        (root / name).write_text(text)
    return root


def _new_project(root: Path) -> Path:
    _inputs(root)
    (root / "taxjson.toml").write_text(_NEW_TOML)
    (root / "ticker.map").write_text(_NEW_MAP)
    return root


def _book(path: Path):
    doc = json.loads(path.read_text())

    def strip(v):
        if isinstance(v, dict):
            return {k: strip(x) for k, x in v.items()
                    if k not in ("merged_at", "generated_at", "created",
                                 "run_at", "source_files", "sources")}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v
    return strip(doc)


class TestTickerMapLookups(unittest.TestCase):
    """The three lookup keywords: parsed by lib/ticker_map, accepted by the
    rename parser, read by each old reader's replacement."""

    def _map(self, td, text):
        p = Path(td) / "ticker.map"
        p.write_text(text)
        return p

    def test_parse_and_readers(self):
        from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                    merge_renames)
        from taxjson.lib.price_chain import load_crypto_overrides, load_yf_map
        from taxjson.bin.fill_crypto_prices import load_symbol_overrides
        from taxjson.bin.taxjson_brokerage import load_security_overrides
        from taxjson.lib.ticker_map import read_side_rules
        with tempfile.TemporaryDirectory() as td:
            p = self._map(td, _NEW_MAP + "GLOBAL FB.US META.US\n")
            tmap, problems, _n = _parse_map_file(p)
            self.assertEqual(problems, [])
            # The renames are unchanged by the lookup lines.
            self.assertEqual(merge_renames(tmap, True),
                             {"AEM.US": "AEM.TO", "FB.US": "META.US"})
            self.assertEqual(load_yf_map([td]),
                             {"ZZS.TO": ("ZZS-A.TO", 1.0),
                              "OLD.TO": ("NEWCO", 0.25)})
            self.assertEqual(load_crypto_overrides([td])["FOO"], "FOO123")
            self.assertEqual(load_symbol_overrides([td])["FOO"], "FOO123")
            self.assertEqual(load_security_overrides(p),
                             [("zzq reit trust units", "CAD", "ZZQ.UN.TO")])
            self.assertEqual(read_side_rules(p).retired, [])

    def test_malformed_lookup_lines_are_map_problems(self):
        from taxjson.bin.taxjson_ticker_map import map_file_problems
        with tempfile.TemporaryDirectory() as td:
            p = self._map(td, "QUOTE ZZS.TO\n"
                              "QUOTE A.TO A 0\n"
                              "CRYPTO FOO\n"
                              "EXTRACT words | US | X.TO\n"
                              "EXTRACT words only\n"
                              "QUOTE B.TO B\nQUOTE B.TO C\n"
                              "ZZS.TO ZZS-A.TO\n")
            probs = map_file_problems(p)
        self.assertEqual(len(probs), 7, probs)
        self.assertTrue(any("two" in m.lower() or "twice" in m
                            for m in probs), probs)
        self.assertTrue(any("no ticker.map keyword" in m for m in probs))

    def test_extract_line_with_old_format_is_refused_by_the_parser(self):
        from taxjson.bin.taxjson_brokerage import (SecurityOverrideError,
                                                   load_security_overrides)
        with tempfile.TemporaryDirectory() as td:
            p = self._map(td, "iShares Gold | USD | IGLD.TO\n")
            with self.assertRaises(SecurityOverrideError) as cm:
                load_security_overrides(p)
        self.assertIn("keyword", str(cm.exception))

    def test_a_folder_with_an_old_file_is_refused_by_the_tools(self):
        from taxjson.lib.price_chain import load_yf_map
        from taxjson.lib.ticker_map import LegacyMapFileError
        from taxjson.bin.fill_crypto_prices import load_symbol_overrides
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "yf_ticker.map").write_text("A.TO B\n")
            with self.assertRaises(LegacyMapFileError) as cm:
                load_yf_map([td])
            self.assertIn("taxjson migrate", str(cm.exception))
            (Path(td) / "yf_ticker.map").unlink()
            (Path(td) / "crypto_ticker.map").write_text("FOO F1\n")
            with self.assertRaises(LegacyMapFileError):
                load_symbol_overrides([td])


class TestConfigTables(unittest.TestCase):
    """The year-data tables are checked by every config reader."""

    def _probs(self, extra, country="canada", accounts=("margin",)):
        from taxjson.lib.config_check import settings_problems
        from taxjson.lib.tomlcompat import tomllib
        cur = "CAD" if country == "canada" else "USD"
        text = (f'[settings]\ncountry = "{country}"\nbase_currency = '
                f'"{cur}"\n' + "".join(f'[accounts.{a}]\ntype = "taxable"\n'
                                       for a in accounts) + extra)
        return settings_problems(tomllib.loads(text))

    def test_valid_tables_pass(self):
        self.assertEqual(self._probs(_NEW_TOML.split(
            '[accounts.margin]\ntype = "taxable"\n', 1)[1]), [])

    def test_bad_entries_are_named(self):
        cases = {
            '[[distributions]]\nsymbol = "X.TO"\nrecord_date = '
            '"2025-13-01"\nper_share = 0.5\n': "record_date",
            '[[distributions]]\nsymbol = "X.TO"\nrecord_date = '
            '2025-12-01\nper_share = "0.5"\n': "per_share",
            '[[distributions]]\nsymbol = "X.TO"\nrecord_date = '
            '2025-12-01\nper_share = 0.5\nnote = 1\n': "unknown key",
            '[carryover]\nclaimed = { 2024 = -5 }\n': "claimed",
            '[carryover]\nclaimed = { 1850 = 5 }\n': "plausible",
            '[carryover]\nclaimd = { 2024 = 5 }\n': "unknown key",
            '[[capital_gains_dividends]]\nsymbol = "Z.TO"\nyear = 2025\n'
            'date = 2025-03-10\namount = "all"\n': "exactly one",
            '[[capital_gains_dividends]]\nsymbol = "Z.TO"\nyear = 2025\n'
            'amount = "most"\n': "amount",
            '[[capital_gains_dividends]]\nsymbol = "Z.TO"\nyear = 2025\n'
            'amount = "all"\naccount = "rrsp"\n': "not an [accounts",
            '[[capital_gains_dividends]]\nsymbol = "Z.TO"\nyear = 2025\n'
            'amount = "all"\n[[capital_gains_dividends]]\nsymbol = "z.to"\n'
            'year = 2025\namount = 5.0\n': "repeats entry #1",
            '[estimate]\namt_carryover = 1200\n': "amt_carryover",
        }
        for extra, want in cases.items():
            with self.subTest(extra=extra):
                probs = self._probs(extra)
                self.assertTrue(any(want in p for p in probs),
                                (want, probs))

    @rule_absent("CA-INC-06", country="usa")
    def test_box18_table_is_canada_only(self):
        extra = ('[[capital_gains_dividends]]\nsymbol = "Z.TO"\n'
                 'year = 2025\namount = "all"\n')
        self.assertEqual(self._probs(extra), [])
        us = self._probs(extra, country="usa")
        self.assertTrue(any("[capital_gains_dividends]" in p
                            and "Canada-only" in p for p in us), us)

    def test_claimed_and_distributions_belong_to_both_countries(self):
        extra = ('[carryover]\nclaimed = { 2024 = 3000 }\n'
                 '[[distributions]]\nsymbol = "SPY.US"\n'
                 'record_date = 2025-12-20\nper_share = 0.1\n')
        self.assertEqual(self._probs(extra, country="usa"), [])
        self.assertEqual(self._probs(extra), [])

    def test_fingerprint_tracks_ticker_map_only(self):
        from taxjson.lib import checklist as cl
        from taxjson.bin import taxjson_run as R
        # tobase.map (the interlisted pairs, read with ticker.map) is an
        # input of the books too.
        # (missing_history.json is no longer read: its .tt lines are
        # account inputs.)
        self.assertEqual(cl.PROJECT_ROOT_MAPS, ("ticker.map", "tobase.map"))
        self.assertEqual(R._PROJECT_ROOT_INPUTS, cl.PROJECT_ROOT_MAPS)


class TestLegacyRefusal(unittest.TestCase):

    def test_every_old_file_stops_commands_until_migrated(self):
        from taxjson.lib.migrate import LEGACY_FILES
        from taxjson.lib.ticker_map import RETIRED_MAP_FILES
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_BASE_TOML)
            # (a retired file — tv_exchange.map — stops nothing:
            # test_fix_notv)
            for name in [n for n in LEGACY_FILES
                         if n not in RETIRED_MAP_FILES]:
                (root / name).write_text("")
                for args in (("sum",), ("divs-sum",), ("run", "--no-input")):
                    r = cli(root, *args)
                    self.assertEqual(r.returncode, 2, (name, args, r.stderr))
                    self.assertIn(name, r.stderr)
                    self.assertIn("taxjson migrate", r.stderr)
                (root / name).unlink()

    def test_migrate_with_nothing_to_do(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_BASE_TOML)
            r = cli(root, "migrate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to migrate", r.stdout)


class TestMigrate(unittest.TestCase):

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = _old_project(Path(td) / "p")
            before = {p.name: p.read_bytes() for p in root.iterdir()
                      if p.is_file()}
            r = cli(root, "migrate", "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            after = {p.name: p.read_bytes() for p in root.iterdir()
                     if p.is_file()}
        self.assertEqual(before, after)
        self.assertIn("would append to ticker.map", r.stdout)
        self.assertIn("QUOTE ZZS.TO ZZS-A.TO", r.stdout)
        self.assertIn("+[[distributions]]", r.stdout)
        self.assertIn("nothing was written", r.stdout)

    def test_appends_and_keeps_the_users_text(self):
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as td:
            root = _old_project(Path(td) / "p")
            r = cli(root, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            tm = (root / "ticker.map").read_text()
            toml = (root / "taxjson.toml").read_text()
            names = sorted(p.name for p in root.iterdir())
            cfg = tomllib.loads(toml)
        # ticker.map: appended after the user's line.
        self.assertTrue(tm.startswith("TOBASE AEM.US AEM.TO  # keep me\n"))
        self.assertNotIn("TRADINGVIEW", tm)
        for ln in ("QUOTE ZZS.TO ZZS-A.TO", "QUOTE OLD.TO NEWCO 0.25",
                   "CRYPTO FOO FOO123",
                   "EXTRACT ZZQ REIT Trust Units | CAD | ZZQ.UN.TO",
                   "# the trust units"):
            self.assertIn(ln, tm)
        # The earlier ZZS.TO line lost to the later one, as before.
        self.assertNotIn("QUOTE ZZS.TO ZZS.TO", tm)
        # taxjson.toml: every original line kept, in order.
        it = iter(toml.splitlines())
        for ln in _BASE_TOML.splitlines():
            self.assertIn(ln, it)
        self.assertEqual(cfg["estimate"]["amt_carryover"], {"2023": 1200.5})
        self.assertEqual(cfg["estimate"]["other_income"], 60000)
        self.assertEqual(cfg["carryover"]["claimed"], {"2024": 2600.0})
        self.assertEqual(len(cfg["distributions"]), 1)
        self.assertEqual(cfg["capital_gains_dividends"][0]["amount"], "all")
        # Old files renamed, never deleted.
        for name in _OLD:
            self.assertIn(name + ".migrated", names)
            self.assertNotIn(name, names)

    def test_refuses_a_conflict_and_writes_nothing(self):
        cases = (
            ("ticker.map", "QUOTE ZZS.TO OTHER.TO\n", "QUOTE"),
            ("taxjson.toml", None, "amt_carryover"),
        )
        for target, text, want in cases:
            with self.subTest(target=target), \
                    tempfile.TemporaryDirectory() as td:
                root = _old_project(Path(td) / "p")
                if target == "ticker.map":
                    (root / "ticker.map").write_text(text)
                else:
                    (root / "taxjson.toml").write_text(_BASE_TOML.replace(
                        "other_income = 60000  # salary",
                        "other_income = 60000\n"
                        "amt_carryover = { 2023 = 99.0 }"))
                before = {p.name: p.read_bytes() for p in root.iterdir()
                          if p.is_file()}
                r = cli(root, "migrate")
                after = {p.name: p.read_bytes() for p in root.iterdir()
                         if p.is_file()}
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn(want, r.stderr)
                self.assertIn("nothing was changed",
                          " ".join(r.stderr.split()).lower())
                self.assertEqual(before, after)

    def test_identical_entries_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = _old_project(Path(td) / "p")
            (root / "ticker.map").write_text(
                "QUOTE ZZS.TO ZZS-A.TO\nCRYPTO FOO FOO123\n")
            r = cli(root, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            tm = (root / "ticker.map").read_text()
        self.assertEqual(tm.count("QUOTE ZZS.TO ZZS-A.TO"), 1)
        self.assertEqual(tm.count("CRYPTO FOO FOO123"), 1)
        self.assertIn("already there", r.stdout)

    def test_an_unreadable_old_line_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            root = _old_project(Path(td) / "p")
            (root / "distributions.map").write_text("XAW.TO 2025/06/30 0.5\n")
            r = cli(root, "migrate")
            self.assertEqual(r.returncode, 2)
            self.assertIn("distributions.map:1", r.stderr)
            self.assertTrue((root / "yf_ticker.map").exists())

    @rule("CA-DIST-01")
    @rule("CA-INC-06")
    @rule("CA-AMT-08")
    @rule("CA-RPT-10")
    def test_round_trip_builds_identical_books_and_views(self):
        with tempfile.TemporaryDirectory() as td:
            old = _old_project(Path(td) / "old")
            new = _new_project(Path(td) / "new")
            r = cli(old, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            for root in (old, new):
                r = cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            # The lookups read the same from both maps.
            from taxjson.lib.price_chain import (load_crypto_overrides,
                                                 load_yf_map)
            from taxjson.lib.ticker_map import read_side_rules
            self.assertEqual(load_yf_map([old]), load_yf_map([new]))
            self.assertEqual(load_crypto_overrides([old]),
                             load_crypto_overrides([new]))
            ro_, rn_ = (read_side_rules(old / "ticker.map"),
                        read_side_rules(new / "ticker.map"))
            self.assertEqual((ro_.quote, ro_.crypto, ro_.extract,
                              ro_.problems, ro_.retired),
                             (rn_.quote, rn_.crypto, rn_.extract,
                              rn_.problems, rn_.retired))
            for f in ("margin_base.json", "margin_gains.json"):
                self.assertEqual(_book(old / "work" / f),
                                 _book(new / "work" / f), f)
            base = _book(new / "work" / "margin_base.json")
            syms = {t.get("symbol") for t in base["transactions"]}
            # EXTRACT applied; the distribution booked.
            self.assertIn("ZZQ.UN.TO", syms)
            self.assertNotIn("ZZQ.TO", syms)
            self.assertTrue(any(str(t.get("id", "")).startswith("DIST-")
                                for t in base["transactions"]))
            def _holdings(root):
                return [ln for ln in (root / "reports" /
                                      "margin_holdings.toml")
                        .read_text().splitlines()
                        if not ln.startswith("generated_at")]
            self.assertEqual(_holdings(old), _holdings(new))
            for args in (("divs-sum", "--json"), ("estimate", "--json"),
                         ("carryover", "--json"), ("amt", "--json"),
                         ("sum", "--json")):
                ro, rn = cli(old, *args), cli(new, *args)
                self.assertEqual(rn.returncode, 0, (args, rn.stderr))
                self.assertEqual(ro.stdout, rn.stdout, args)
            d = json.loads(cli(new, "divs-sum", "--json").stdout)
            self.assertEqual(d["capital_gains_dividends"]["totals"],
                             {"CAD": 100.0})
            e = json.loads(cli(new, "estimate", "--json").stdout)
            src = (e.get("carry_sources")
                   or (e.get("estimate") or {}).get("carry_sources") or {})
            self.assertEqual(src.get("amt_carryover"),
                             "[estimate] amt_carryover", e.keys())
            c = json.loads(cli(new, "carryover", "--json").stdout)
            self.assertIn("2024", json.dumps(c))

    @rule("US-RPT-08")
    def test_us_project_claimed_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "usa"\n'
                'base_currency = "USD"\n[accounts.margin]\n'
                'type = "taxable"\n')
            (root / "claimed_losses.txt").write_text("2024 3000\n")
            r = cli(root, "migrate")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("claimed = { 2024 = 3000.0 }",
                          (root / "taxjson.toml").read_text())
            # The Canada-only box 18 file is refused in a US project.
            (root / "capital_gains_dividends.map").write_text(
                "SPY.US 2025 all\n")
            r = cli(root, "migrate")
            self.assertEqual(r.returncode, 2)
            self.assertIn("Canada-only", r.stderr)


if __name__ == "__main__":
    unittest.main()
