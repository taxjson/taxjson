"""`tjs parser-check` (step 5g): the package's conformance checks on a
registered parser's fixture folder."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _tmpfiles import isolate_layouts
from taxjson.lib.dispatch import python_module_argv
from taxjson.parsers import registry
from taxjson.parsers.conformance import check, read_fixture_meta
import _paths

REPO_ROOT = _paths.REPO
FIXTURES = REPO_ROOT / "tests" / "fixtures"
isolate_layouts()       # the box's own layouts never route a fixture (the command inherits it)


def _tjs(*args, env=None):
    e = dict(os.environ)
    e["TAXJSON_DEV_DIR"] = str(REPO_ROOT)
    e["TAXJSON_LOCAL_TZ"] = _ZONE
    e.update(env or {})
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run", list(args)),
                          cwd=REPO_ROOT, capture_output=True, text=True, env=e)


_ZONE = "America/Toronto"       # the zone the crypto goldens were made in


class _Zone(unittest.TestCase):
    def setUp(self):
        self._tz = os.environ.get("TAXJSON_LOCAL_TZ")
        os.environ["TAXJSON_LOCAL_TZ"] = _ZONE

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TAXJSON_LOCAL_TZ", None)
        else:
            os.environ["TAXJSON_LOCAL_TZ"] = self._tz


class TestConformanceChecks(_Zone):

    def test_every_shipped_parser_passes_its_fixture(self):
        for s in registry.SPECS:
            found, rows, _skipped = check(registry.parser_class(s), FIXTURES / s.id)
            self.assertEqual(found, [], s.id)
            self.assertGreater(rows, 0, s.id)

    def test_a_diverged_golden_is_a_finding(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "generic"
            shutil.copytree(FIXTURES / "generic", folder)
            golden = folder / "expected.json"
            rows = json.loads(golden.read_text())
            golden.write_text(json.dumps(rows[:-1], indent=1, sort_keys=True))
            found, _rows, _skipped = check(registry.parser_class("generic"), folder)
            self.assertEqual(len(found), 1)
            self.assertIn("diverged from the golden", found[0])
            found, _rows, _skipped = check(registry.parser_class("generic"), folder,
                                           update_golden=True)
            self.assertEqual(found, [])
            self.assertEqual(json.loads(golden.read_text()), rows)
            empty = Path(td) / "empty"
            empty.mkdir()
            found, _rows, _skipped = check(registry.parser_class("generic"), empty)
            self.assertIn("no fixture", found[0])
            golden.unlink()
            found, _rows, _skipped = check(registry.parser_class("generic"), folder,
                                           hint="--update-golden")
            self.assertIn("--update-golden", found[0])
            golden.write_text("[1, 2,")
            found, _rows, _skipped = check(registry.parser_class("generic"), folder)
            self.assertEqual(len(found), 1)
            self.assertIn("cannot be read", found[0])
            # A foreign file the parser refuses is a finding, not a traceback.
            (folder / "sample.csv").write_text('"unterminated,1\n')
            found, _rows, _skipped = check(registry.parser_class("generic"), folder)
            self.assertIn("refused the fixture", found[0])

    def test_a_diverged_cash_golden_is_a_finding(self):
        # The cash events the broker's reader finds are the folder's cash
        # golden, as its rows are expected.json (PLAN step 16).
        from taxjson.parsers.conformance import CASH_GOLDEN
        qt = registry.parser_class("questrade")
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "questrade"
            shutil.copytree(FIXTURES / "questrade", folder)
            golden = folder / CASH_GOLDEN
            events = json.loads(golden.read_text(encoding="utf-8"))
            golden.write_text(json.dumps(events[:-1], indent=1, sort_keys=True),
                              encoding="utf-8")
            found, _rows, _skipped = check(qt, folder)
            self.assertEqual([f.partition("\n")[0] for f in found],
                             ["the cash events diverged from the cash golden expected_cash.json"])
            self.assertIn(f"{len(events)} event(s) now, {len(events) - 1} expected", found[0])
            found, _rows, _skipped = check(qt, folder, update_golden=True)
            self.assertEqual(found, [])
            self.assertEqual(json.loads(golden.read_text(encoding="utf-8")), events)
            golden.unlink()
            found, _rows, _skipped = check(qt, folder, hint="--update-golden")
            self.assertEqual([f.partition("\n")[0] for f in found],
                             ["missing cash golden expected_cash.json"])
            self.assertIn("write it from the parse's cash events (--update-golden)", found[0])
            # A folder whose reader finds no events needs none (a folder of one's own).
            mine = Path(td) / "generic"
            shutil.copytree(FIXTURES / "generic", mine)
            (mine / CASH_GOLDEN).unlink()
            found, _rows, _skipped = check(registry.parser_class("generic"), mine)
            self.assertEqual(found, [])

    def test_the_cash_reader_reads_only_a_folder_that_asks_for_its_events(self):
        # A folder that claims no cash kinds and holds no cash golden is not
        # held to the cash reader: a deposit's Net Amount it cannot read
        # passed before the cash golden came, and passes still (review F5).
        # A folder that claims cash kinds is, and the reader's refusal is a
        # finding: since PLAN step 16 commit 9 the parse's (its cash_problem,
        # the reader's words), the parse itself standing.
        from taxjson.parsers.conformance import CASH_GOLDEN
        qt = registry.parser_class("questrade")
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "questrade"
            shutil.copytree(FIXTURES / "questrade", folder)
            sample = folder / "sample.csv"
            deposit = ",DEPOSIT,0,0.00,0.00,0.00,500.00,CAD,"
            text = sample.read_text(encoding="utf-8")
            self.assertEqual(text.count(deposit), 1)
            sample.write_text(text.replace(deposit, deposit.replace("500.00", "5x0.00")),
                              encoding="utf-8")
            toml = folder / "fixture.toml"
            claims = toml.read_text(encoding="utf-8")
            toml.write_text("".join(line for line in claims.splitlines(keepends=True)
                                    if not line.startswith(("kinds =", "cash_kinds ="))),
                            encoding="utf-8")
            (folder / CASH_GOLDEN).unlink()
            found, _rows, _skipped = check(qt, folder)
            self.assertEqual(found, [])
            toml.write_text(toml.read_text(encoding="utf-8") + 'cash_kinds = "FXCONV"\n',
                            encoding="utf-8")
            found, _rows, _skipped = check(qt, folder)
            self.assertEqual([f.partition("\n")[0] for f in found],
                             ["the parse could not build the fixture's cash events",
                              "the folder claims FXCONV, which the parse's cash events have "
                              "none of"])
            self.assertIn("Net Amount '5x0.00'", found[0])
            self.assertIn("a DEP cash row the FX-on-cash ledger reads", found[0])

    def test_a_cash_cell_the_parse_could_not_read_is_a_finding(self):
        # PLAN step 16 commit 8: Coinbase's parse builds its cash events; a
        # Deposit's quantity it never reads (a non-event) that cannot be read
        # leaves the parse standing and the fixture's cash problem a finding
        # (the parser's cash_problem), as a cash reader's refusal is.
        cb = registry.parser_class("coinbase")
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "coinbase"
            shutil.copytree(FIXTURES / "coinbase", folder)
            sample = folder / "sample.csv"
            deposit = ",Deposit,USD,1000,"
            text = sample.read_text(encoding="utf-8")
            self.assertEqual(text.count(deposit), 1)
            sample.write_text(text.replace(deposit, ",Deposit,USD,1x00,"), encoding="utf-8")
            found, _rows, _skipped = check(cb, folder)
        problem = [f for f in found
                   if f.startswith("the parse could not build the fixture's cash events")]
        self.assertEqual(len(problem), 1, found)
        self.assertIn("ValueError: unparseable quantity transacted '1x00'", problem[0])

    def test_the_kit_takes_the_parses_cash_events(self):
        # PLAN step 16 commit 12: no reader is left. The kit takes the
        # parse's events as they are; its problem is a finding; a broker the
        # registry marks builds_cash whose parse built none is a finding,
        # never an empty list read as no cash; another parser has none.
        from types import SimpleNamespace
        from taxjson.parsers.conformance import cash_events_of
        ev = {"kind": "CASHMOVE"}
        self.assertEqual(cash_events_of("coinbase", SimpleNamespace(
            cash_events=[ev], cash_problem="")), ([ev], []))
        self.assertEqual(cash_events_of("coinbase", SimpleNamespace(
            cash_events=[], cash_problem="ValueError: x")),
            ([], ["the parse could not build the fixture's cash events\nValueError: x"]))
        for pid in sorted(registry.cash_event_brokers()):
            cash, found = cash_events_of(pid, SimpleNamespace(cash_events=None,
                                                              cash_problem=""))
            self.assertEqual(cash, [], pid)
            self.assertEqual([f.partition("\n")[0] for f in found],
                             ["the parse built no cash events of the fixture"], pid)
        for pid in ("webull", "generic", "your_broker"):
            self.assertEqual(cash_events_of(pid, SimpleNamespace(cash_events=None,
                                                                 cash_problem="")),
                             ([], []), pid)

    def test_an_expected_skip_count_needs_accounting(self):
        from taxjson.parsers.conformance import accounting_findings
        from taxjson.parsers.protocol import RowCounts

        class Counted:              # rows seen, consumed and skipped
            def __init__(self, seen, consumed, skipped):
                self._counts = RowCounts(seen, consumed, skipped)

            def row_counts(self):
                return self._counts
        class NoCounts:              # a parser off the template (TJ-2415)
            pass
        self.assertIn("no row-level accounting", accounting_findings(NoCounts())[0])
        # One text with or without an expected count (TJ-2419).
        self.assertEqual(accounting_findings(NoCounts(), 2), accounting_findings(NoCounts()))
        self.assertEqual(accounting_findings(Counted(None, 0, 2), 2), [])
        self.assertIn("1 row(s) counted as skipped, 2 expected",
                      accounting_findings(Counted(None, 0, 1), 2)[0])
        self.assertEqual(accounting_findings(Counted(4, 3, 1), 1), [])
        self.assertIn("neither consumed nor counted", accounting_findings(Counted(5, 3, 1))[0])
        # A row counted twice names the double count, not a dropped row (TJ-2392).
        self.assertTrue(accounting_findings(Counted(4, 4, 2))[0].startswith(
            "2 fixture row(s) counted twice"))
        # A folder without fixture.toml has no metadata; one with it says the
        # count (every shipped fixture carries one since TJ-2328).
        from taxjson.parsers.conformance import read_fixture_meta
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(read_fixture_meta(Path(td)), {})
            (Path(td) / "fixture.toml").write_text("# a comment\nexpected_skips = 2\n",
                                                   encoding="utf-8")
            self.assertEqual(read_fixture_meta(Path(td))["expected_skips"], 2)
        for folder in FIXTURES.iterdir():
            if (folder / "sample.csv").is_file():
                self.assertIn("expected_skips", read_fixture_meta(folder), folder.name)


class TestAccountPhase(unittest.TestCase):
    """Each stage of the account phase takes what taxjson-brokerage passes
    it (TJ-2412): an override that takes less is a finding, not a TypeError
    at the run."""

    def test_the_shipped_parsers_take_the_account_keywords(self):
        from taxjson.parsers.conformance import account_phase_findings
        from taxjson.parsers.registry import SPECS, parser_class
        for spec in SPECS:
            with self.subTest(parser=spec.id):
                self.assertEqual(account_phase_findings(parser_class(spec)), [])

    def test_a_narrower_override_is_a_finding(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        from taxjson.parsers.conformance import account_phase_findings

        class Narrow(BaseBrokerage):
            @classmethod
            def prepare_files(cls, paths):
                return None

            @staticmethod
            def coverage_messages(context, year):
                return []
        found = account_phase_findings(Narrow)
        self.assertEqual([f.split("\n")[0] for f in found],
                         ["prepare_files does not take what taxjson-brokerage passes it",
                          "coverage_messages does not take what taxjson-brokerage passes it"])
        self.assertIn("tax_year, combined, taxable, symbol_codes, project", found[0])
        self.assertEqual(account_phase_findings(object), [
            f"the parser has no {n}\na parser derives from BaseBrokerage"
            for n in ("prepare_files", "coverage_messages", "reconcile_files")])

    def test_the_tool_passes_the_protocols_keywords(self):
        # The tool's calls and the kit's check read one list (TJ-2424): the
        # call's keywords, read from the tool's source, are the protocol's.
        import ast
        import taxjson
        from taxjson.parsers.protocol import COVERAGE_KEYWORDS, PREPARE_KEYWORDS
        tool = Path(taxjson.__file__).parent / "bin" / "taxjson_brokerage.py"
        calls = {}
        for node in ast.walk(ast.parse(tool.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("prepare_files", "coverage_messages")):
                calls.setdefault(node.func.attr, []).append([k.arg for k in node.keywords])
        self.assertEqual(calls, {"prepare_files": [list(PREPARE_KEYWORDS)],
                                 "coverage_messages": [list(COVERAGE_KEYWORDS)]})

    def test_a_parser_off_the_template_is_findings_not_a_traceback(self):
        # Neither configure nor row_counts (TJ-2418): each named once.
        from taxjson.parsers.conformance import check

        class Off:
            def parse_file(self, path):
                return []
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "sample.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            found, rows, skipped = check(Off, Path(td), expected_skips=1)
            # A parser's second folder: its account phase is not checked again (TJ-2425).
            again, _, _ = check(Off, Path(td), expected_skips=1, account_phase=False)
        heads = [f.split("\n")[0] for f in found]
        self.assertEqual((rows, skipped), (0, 0))
        for head in ("the parser does no row-level accounting", "the parser has no configure",
                     "the parser has no prepare_files"):
            self.assertEqual(heads.count(head), 1, (head, heads))
        self.assertNotIn("the parser has no prepare_files", [f.split("\n")[0] for f in again])


class TestDetectionAndCountries(_Zone):
    """The detection check asks the router; the countries check runs the
    schema and the accounting under each country (TJ-2390, TJ-2393)."""

    def test_detection_through_the_router(self):
        from taxjson.lib.brokerages.detect import AmbiguousBroker, Detection
        from taxjson.parsers.conformance import detection_findings
        sample = FIXTURES / "questrade" / "sample.csv"
        self.assertEqual(detection_findings(
            "questrade", sample, route=lambda p: Detection(p, "questrade", "content")), [])
        found = detection_findings("questrade", sample,
                                   route=lambda p: Detection(p, "generic", "mapping", "mapping x.toml"))
        self.assertIn("would not read the fixture with questrade", found[0])
        self.assertIn("generic (mapping x.toml)", found[0])

        def ambiguous(p):
            raise AmbiguousBroker("two exports match")
        self.assertIn("would refuse the fixture", detection_findings(
            "questrade", sample, route=ambiguous)[0])

    def test_a_decoding_error_and_a_malformed_folder(self):
        from taxjson.lib.brokerages.detect import Detection
        from taxjson.parsers.conformance import (country_findings, detection_findings,
                                                 fixture_dirs)
        sample = FIXTURES / "questrade" / "sample.csv"
        found = detection_findings("questrade", sample, route=lambda p: Detection(
            p, None, error="cannot decode the file as UTF-8"))
        self.assertIn("cannot decode the file as UTF-8", found[0])     # TJ-2403
        # One folder's malformed fixture.toml is that folder's finding, never
        # another parser's traceback (TJ-2402).
        with tempfile.TemporaryDirectory() as td:
            shutil.copytree(FIXTURES / "generic", Path(td) / "generic")
            bad = Path(td) / "newbroker"
            shutil.copytree(FIXTURES / "generic", bad)
            (bad / "fixture.toml").write_text("expected_skips = 2.5\n", encoding="utf-8")
            self.assertEqual([d.name for d in fixture_dirs(Path(td), "generic")], ["generic"])
            found, _rows, _skipped = check(registry.parser_class("generic"), bad)
            self.assertIn("fixture.toml cannot be read", found[0])
            # A folder whose fixture.toml names another parser is that parser's (TJ-2405).
            (bad / "fixture.toml").write_text('parser = "generic"\n', encoding="utf-8")
            self.assertEqual([d.name for d in fixture_dirs(Path(td), "generic")],
                             ["generic", "newbroker"])
            (Path(td) / "generic" / "fixture.toml").write_text('parser = "other"\n',
                                                              encoding="utf-8")
            self.assertEqual([d.name for d in fixture_dirs(Path(td), "generic")], ["newbroker"])
        # A finding the default parse has is not repeated per country (TJ-2406).
        from taxjson.lib.brokerages.base import BaseBrokerage

        class Drops(BaseBrokerage):
            def parse_file(self, path):
                self._rows_seen, self._rows_consumed, self._skip_counts = 2, 1, {}
                return []
        self.assertEqual(len(country_findings(Drops, sample)), 2)
        self.assertEqual(country_findings(Drops, sample, known={
            "1 fixture row(s) neither consumed nor counted as skipped\na silent-drop code path"}), [])

    def test_a_folder_names_its_parser(self):
        from taxjson.parsers.conformance import fixture_dirs, fixture_parser_id
        self.assertEqual(fixture_parser_id(FIXTURES / "kraken_ledgers"), "kraken")
        self.assertEqual(fixture_parser_id(FIXTURES / "questrade"), "questrade")
        self.assertEqual([d.name for d in fixture_dirs(FIXTURES, "kraken")],
                         ["kraken", "kraken_ledgers"])
        self.assertEqual([d.name for d in fixture_dirs(FIXTURES, "ib")], ["ib"])

    def test_the_countries_run_the_schema_and_the_accounting(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        from taxjson.lib.country import COUNTRIES
        from taxjson.parsers.conformance import country_findings

        class ByCountry(BaseBrokerage):
            # Drops a row uncounted under one country alone.
            def parse_file(self, path):
                self._rows_seen, self._rows_consumed, self._skip_counts = 2, 1, {}
                if getattr(self, "_country", None) == COUNTRIES[-1]:
                    return []
                self._rows_consumed = 2
                return []

            def configure(self, settings, *, account_context=None):
                self._country = settings.country
        found = country_findings(ByCountry, FIXTURES / "questrade" / "sample.csv")
        self.assertEqual(len(found), 2)
        self.assertTrue(found[0].startswith(f"under {COUNTRIES[-1]}: 1 fixture row(s) "
                                            f"neither consumed"), found)
        # Its counts differ from no country's: the identical rows say so too.
        self.assertTrue(found[1].startswith(f"the rows under {COUNTRIES[-1]} differ from "
                                            f"those under no country"), found)

    def test_each_country_is_parsed_on_its_own_basis(self):
        # Not only given a basis (a forgotten one is a TypeError): its
        # own, so a basis swapped or shared between the countries fails.
        from taxjson.lib.brokerages.base import BaseBrokerage
        from taxjson.lib.country import CANADA, COUNTRIES, parse_basis
        from taxjson.parsers.conformance import bases, country_findings
        seen = []

        class Basis(BaseBrokerage):
            def parse_file(self, path):
                self._rows_seen, self._rows_consumed, self._skip_counts = 0, 0, {}
                return []

            def configure(self, settings, *, account_context=None):
                seen.append((settings.country, settings.stablecoins_as_cash,
                             settings.plans_taxed))
        self.assertEqual(
            country_findings(Basis, FIXTURES / "questrade" / "sample.csv"), [])
        # The schema and the accounting first, on each country's own basis.
        own = seen[:len(COUNTRIES)]
        self.assertEqual(own, [(c, parse_basis(c)["stablecoins_as_cash"],
                                parse_basis(c)["plans_taxed"]) for c in COUNTRIES])
        self.assertEqual([cash for _c, cash, _p in own],
                         [c == CANADA for c in COUNTRIES])
        # Then the identical rows: every country and none at every basis,
        # each parsed once (a country's own basis is not parsed again).
        every = {(c, b["stablecoins_as_cash"], b["plans_taxed"])
                 for c in (None,) + tuple(COUNTRIES) for b in bases()}
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(set(seen), every)
        self.assertEqual(len(bases()), 4)       # cash or property, no plan or the US's


class TestIdenticalRows(_Zone):
    """At a fixed basis a parse gives the same rows and counts under each
    country and under none; under every basis but in the fields its
    `BASIS_INPUTS` declares (PLAN step 14)."""

    SAMPLE = FIXTURES / "questrade" / "sample.csv"

    @staticmethod
    def _parser(reads, declares=()):
        # Golden rows (they pass the schema), two or one by what `reads` sees.
        from taxjson.lib.brokerages.base import BaseBrokerage
        rows = json.loads((FIXTURES / "generic" / "expected.json").read_text())[:2]

        class Reads(BaseBrokerage):
            BASIS_INPUTS = tuple(declares)

            def parse_file(self, path):
                self._rows_seen, self._rows_consumed, self._skip_counts = 2, 2, {}
                return [dict(r) for r in (rows[:1] if reads(self) else rows)]
        return Reads

    @staticmethod
    def _heads(found):
        return [f.split("\n")[0] for f in found]

    def test_every_fixture_folder_gives_identical_rows(self):
        from taxjson.parsers.conformance import country_findings, fixture_parser_id
        folders = [d for d in sorted(FIXTURES.iterdir()) if (d / "sample.csv").is_file()]
        self.assertGreaterEqual(len(folders), 9)
        for d in folders:
            with self.subTest(folder=d.name):
                self.assertEqual(country_findings(
                    registry.parser_class(fixture_parser_id(d)), d / "sample.csv"), [])

    def test_a_stablecoin_basis_read_undeclared_fails(self):
        from taxjson.parsers.conformance import country_findings
        undeclared = self._parser(lambda p: p.stablecoins_as_cash)
        found = country_findings(undeclared, self.SAMPLE)
        self.assertEqual(self._heads(found), [
            "the rows depend on stablecoins_as_cash, which the parser does not declare"])
        self.assertIn("between stablecoins_as_cash = False and True", found[0])
        self.assertIn("BASIS_INPUTS", found[0])
        # Declared, the same parse passes.
        declared = self._parser(lambda p: p.stablecoins_as_cash, ("stablecoins_as_cash",))
        self.assertEqual(country_findings(declared, self.SAMPLE), [])
        # Any basis field: the plans taxed as ordinary read undeclared fails too.
        plans = self._parser(lambda p: bool(p.plans_taxed))
        self.assertEqual(self._heads(country_findings(plans, self.SAMPLE)), [
            "the rows depend on plans_taxed, which the parser does not declare"])

    def test_the_shipped_declarations_hold_the_fixtures(self):
        # Kraken's and Coinbase's fixtures book a stablecoin leg the two
        # models book apart: each declaration is needed on every one of the
        # parser's folders (Kraken's ledgers export too, TJ-4200), and no
        # other parser declares one.
        from taxjson.parsers.conformance import country_findings, fixture_dirs
        for spec in registry.SPECS:
            cls = registry.parser_class(spec)
            with self.subTest(parser=spec.id):
                self.assertEqual(cls.BASIS_INPUTS, ("stablecoins_as_cash",) if spec.crypto
                                 else ())
        folders = {pid: fixture_dirs(FIXTURES, pid) for pid in ("kraken", "coinbase")}
        self.assertEqual({pid: [d.name for d in ds] for pid, ds in folders.items()},
                         {"kraken": ["kraken", "kraken_ledgers"], "coinbase": ["coinbase"]})
        for pid, dirs in folders.items():
            class Undeclared(registry.parser_class(pid)):
                BASIS_INPUTS = ()
            for d in dirs:
                with self.subTest(parser=pid, folder=d.name):
                    self.assertEqual(
                        self._heads(country_findings(Undeclared, d / "sample.csv")),
                        ["the rows depend on stablecoins_as_cash, which the parser does "
                         "not declare"])

    @staticmethod
    def _stale(cls, dirs):
        # The fields the class declares that no fixture of its needs.
        from taxjson.parsers.conformance import basis_effects
        needed = {name for d in dirs for name in basis_effects(cls, d / "sample.csv")}
        return sorted(set(cls.BASIS_INPUTS) - needed)

    def test_every_declared_field_changes_a_fixture(self):
        # A declaration no fixture needs is stale: the check could no
        # longer tell it from a field the parser reads (TJ-4200).
        from taxjson.parsers.conformance import fixture_dirs
        for spec in registry.SPECS:
            cls = registry.parser_class(spec)
            with self.subTest(parser=spec.id):
                self.assertEqual(self._stale(cls, fixture_dirs(FIXTURES, spec.id)), [])
        # A stale one fails: declared and read by no parse, or read and
        # declared beside a field the parse does not read.
        never = self._parser(lambda p: False, ("stablecoins_as_cash",))
        self.assertEqual(self._stale(never, [self.SAMPLE.parent]), ["stablecoins_as_cash"])
        one = self._parser(lambda p: p.stablecoins_as_cash,
                           ("stablecoins_as_cash", "plans_taxed"))
        self.assertEqual(self._stale(one, [self.SAMPLE.parent]), ["plans_taxed"])

    def test_skips_by_class_are_compared(self):
        # As many rows skipped under each setting, for a reason that
        # follows the country or an undeclared basis field: a difference,
        # not only a changed total (TJ-4201).
        from taxjson.lib.brokerages.base import BaseBrokerage
        from taxjson.lib.country import COUNTRIES
        from taxjson.parsers.conformance import country_findings

        def skipping(label):
            class Skips(BaseBrokerage):
                def parse_file(self, path):
                    self._rows_seen, self._rows_consumed = 1, 0
                    self._skip_counts = {label(self): 1}
                    return []
            return Skips
        by_country = skipping(lambda p: f"action X ({p.country == COUNTRIES[-1]})")
        self.assertEqual(self._heads(country_findings(by_country, self.SAMPLE)), [
            f"the rows under {COUNTRIES[-1]} differ from those under no country at the "
            f"same basis"])
        by_basis = skipping(lambda p: f"action X ({p.stablecoins_as_cash})")
        self.assertEqual(self._heads(country_findings(by_basis, self.SAMPLE)), [
            "the rows depend on stablecoins_as_cash, which the parser does not declare"])
        self.assertEqual(country_findings(skipping(lambda p: "action X"), self.SAMPLE), [])

    def test_rows_read_from_the_country_fail(self):
        from taxjson.lib.country import COUNTRIES
        from taxjson.parsers.conformance import country_findings
        by_country = self._parser(lambda p: p.country == COUNTRIES[-1])
        found = country_findings(by_country, self.SAMPLE)
        self.assertEqual(self._heads(found), [
            f"the rows under {COUNTRIES[-1]} differ from those under no country at the "
            f"same basis"])
        # Each basis where they differ is named: here every one.
        self.assertIn("stablecoins_as_cash = True, plans_taxed = {", found[0])
        self.assertIn("stablecoins_as_cash = False, plans_taxed = none", found[0])
        # A declaration does not excuse it: the country is no basis field.
        by_country = self._parser(lambda p: p.country == COUNTRIES[-1],
                                  ("stablecoins_as_cash", "plans_taxed"))
        self.assertEqual(len(country_findings(by_country, self.SAMPLE)), 1)

    def test_a_declaration_names_a_basis_field(self):
        from taxjson.parsers.conformance import country_findings
        odd = self._parser(lambda p: False, ("country",))
        self.assertEqual(self._heads(country_findings(odd, self.SAMPLE)), [
            "BASIS_INPUTS names 'country', which is no basis field"])

    def test_a_refusal_under_one_basis_is_a_difference(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        from taxjson.parsers.conformance import country_findings

        class Refuses(BaseBrokerage):
            def parse_file(self, path):
                if self.stablecoins_as_cash:
                    raise ValueError("no cash model here")
                self._rows_seen, self._rows_consumed, self._skip_counts = 0, 0, {}
                return []
        heads = self._heads(country_findings(Refuses, self.SAMPLE))
        # Refused under Canada's own basis (a finding of its own), and a
        # basis the rows depend on.
        self.assertIn("the parser refused the fixture under canada", heads)
        self.assertIn("the rows depend on stablecoins_as_cash, which the parser does "
                      "not declare", heads)


class TestFixtureSettings(_Zone):
    """A folder's fixture.toml may tell the parse an account setting
    (conformance FIXTURE_SETTINGS: Webull's exercise_fee, with which it
    infers an assignment); every parse of the sample is told it."""

    def test_the_setting_reaches_every_parse(self):
        from taxjson.parsers.conformance import (country_findings, fixture_settings,
                                                 parse_fixture)
        self.assertEqual(fixture_settings(FIXTURES / "webull"), {"exercise_fee": 5.0})
        self.assertEqual(fixture_settings(FIXTURES / "ib"), {})
        cls = registry.parser_class("webull")
        _, txs = parse_fixture(cls, FIXTURES / "webull" / "sample.csv")
        self.assertEqual([t["symbol"] for t in txs if t["action"] == "ASSIGN"],
                         ["QZWP250815P00020000.US", "QZWP.US"])
        self.assertEqual(country_findings(cls, FIXTURES / "webull" / "sample.csv"), [])
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "webull"
            shutil.copytree(FIXTURES / "webull", folder)
            toml = folder / "fixture.toml"
            text = toml.read_text(encoding="utf-8")
            # Without it no pair is inferred: the claimed assign has no row.
            toml.write_text(text.replace("exercise_fee = 5.00\n", ""), encoding="utf-8")
            found, _rows, _skipped = check(cls, folder)
            self.assertIn("the folder claims assign, which no row has",
                          [f.partition("\n")[0] for f in found])
            toml.write_text(text.replace("exercise_fee = 5.00", 'exercise_fee = "5"'),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exercise_fee is a number"):
                read_fixture_meta(folder)
            toml.write_text(text.replace("expected_skips = 6", "expected_skips = 6.5"),
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected_skips is a count of rows"):
                read_fixture_meta(folder)


def _set_claims(folder: Path, **claims) -> None:
    """Rewrite the folder's fixture.toml claims: a value None drops the key."""
    p = folder / "fixture.toml"
    keep = [ln for ln in p.read_text(encoding="utf-8").splitlines()
            if not ln.startswith(("kinds =", "cash_kinds ="))]
    keep += [f'{k} = "{v}"' for k, v in claims.items() if v is not None]
    p.write_text("\n".join(keep) + "\n", encoding="utf-8")


class TestKinds(_Zone):
    """The kinds (PLAN step 16): each row and cash event is of one of
    parsers.protocol's kinds, each row's one its parser declares
    (`builds`), each kind a folder's fixture.toml claims has a row, or an
    event, and each kind a parser declares is claimed."""

    def test_row_kind_reads_what_the_rows_carry(self):
        from taxjson.lib.row_types import OPENING_TYPE, STOCK_DIVIDEND
        from taxjson.lib.trade_cancel import TRADE_CANCEL_TYPE
        from taxjson.parsers.protocol import ROW_KINDS, row_kind
        expiry = {"action": "BUYSELL", "symbol": "ZQQ", "price": 0.0}
        twin = dict(expiry)
        cases = ((expiry, "expiry"), (twin, "trade"), ({"action": "ASSIGN"}, "assign"),
                 ({"action": "SPLIT", "symbol": "ZQQ", "symbol_new": ""}, "split"),
                 ({"action": "SPLIT", "symbol": "ZQQ", "symbol_new": "ZQQ"}, "split"),
                 ({"action": "SPLIT", "symbol": "ZQQ", "symbol_new": "ZQR"}, "rename"),
                 ({"action": "BUYSELL", "type": STOCK_DIVIDEND}, "stock_dividend"),
                 ({"action": "BUYSELL", "type": OPENING_TYPE}, "opening"),
                 ({"action": "BUYSELL", "type": TRADE_CANCEL_TYPE}, "trade_cancel"),
                 ({"action": "ADJUST", "type": "roc"}, "roc"),
                 ({"action": "ADJUST", "type": "dist"}, "adjust"),
                 ({"action": "DIVIDEND_IN_LIEU"}, "dividend_in_lieu"),
                 ({"action": "BUYSELL", "type": "lot_move"}, None),
                 ({"action": "OPENING_BALANCE"}, None), ({"action": "DISALLOW"}, None))
        for tx, kind in cases:
            self.assertEqual(row_kind(tx, [expiry]), kind, tx)
        self.assertEqual({row_kind(tx, [expiry]) for tx, _k in cases} - {None},
                         set(ROW_KINDS) - {"dividend", "tax", "interest", "fee", "transfer"})

    def test_a_row_or_an_event_of_no_kind_is_a_finding(self):
        from taxjson.parsers.conformance import kind_findings
        rows = [{"action": "BUYSELL"}, {"action": "BUYSELL", "type": "lot_move"},
                {"action": "OPENING_BALANCE"}]
        found = kind_findings(rows, (), ("trade",), [{"kind": "WIRE"}, {"kind": "FLOW"}],
                              ("FLOW",))
        self.assertEqual(len(found), 2, found)
        self.assertTrue(found[0].startswith("2 row(s) of no kind\nthe first: action "
                                            "'BUYSELL', type 'lot_move'"), found[0])
        self.assertTrue(found[1].startswith("1 cash event(s) of no kind\nthe first: kind "
                                            "'WIRE'"), found[1])

    def test_a_claimed_kind_with_no_row_is_a_finding(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "generic"
            shutil.copytree(FIXTURES / "generic", folder)
            claimed = read_fixture_meta(folder)["kinds"]
            _set_claims(folder, kinds=", ".join(claimed + ("trade_cancel", "expiry")),
                        cash_kinds="FXCONV")
            found, _rows, _skipped = check(registry.parser_class("generic"), folder)
            heads = [f.partition("\n")[0] for f in found]
            self.assertEqual(heads, ["the folder claims trade_cancel, expiry, which no row has",
                                     "the folder claims FXCONV, which the parse's cash events "
                                     "have none of"])
            # A name that is no kind: the file cannot be read, the name said.
            _set_claims(folder, kinds="trade, swap", cash_kinds="")
            found, _rows, _skipped = check(registry.parser_class("generic"), folder)
            self.assertIn("fixture.toml cannot be read", found[0])
            self.assertIn("kinds names 'swap', which is no kind", found[0])

    def test_a_row_its_parser_does_not_declare_is_a_finding(self):
        from taxjson.parsers.conformance import kind_findings
        rows = [{"action": "BUYSELL"}, {"action": "DIVIDEND"}, {"action": "FEE"}]
        found = kind_findings(rows, (), None, declared=("trade", "fee"))
        self.assertEqual([f.partition("\n")[0] for f in found],
                         ["the parser books dividend, which it does not declare"])
        self.assertIn("it declares trade, fee", found[0])
        self.assertEqual(kind_findings(rows, (), None), [])      # not asked

    def test_every_declared_kind_is_claimed(self):
        # Each shipped parser declares the kinds it books (`builds` on its
        # builders), and its checkout folders claim each one: a kind a parser
        # can book has a row the kit checks.
        from taxjson.parsers.conformance import declared_findings, fixture_dirs
        from taxjson.parsers.protocol import parser_kinds
        for s in registry.SPECS:
            with self.subTest(parser=s.id):
                cls = registry.parser_class(s)
                dirs = fixture_dirs(FIXTURES, s.id)
                self.assertTrue(parser_kinds(cls), s.id)
                self.assertEqual(declared_findings(cls, dirs), [])
                claimed = set()
                for d in dirs:
                    claimed.update(read_fixture_meta(d)["kinds"])
                self.assertEqual(set(parser_kinds(cls)), claimed)
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "webull"
            shutil.copytree(FIXTURES / "webull", folder)
            _set_claims(folder, kinds="trade, expiry")
            found = declared_findings(registry.parser_class("webull"), [folder])
            self.assertEqual([f.partition("\n")[0] for f in found],
                             ["the parser declares assign, which no fixture folder claims"])

    def test_builds_names_kinds(self):
        from taxjson.parsers.protocol import builds, parser_kinds
        with self.assertRaisesRegex(ValueError, "'swap' is no kind"):
            builds("trade", "swap")
        with self.assertRaisesRegex(ValueError, "no kind: the kinds are trade, "):
            builds()

        class Base:
            @builds("fee", "trade")
            def one(self):
                return []

        class Sub(Base):
            @classmethod
            @builds("rename")
            def two(cls):
                return None
        self.assertEqual(parser_kinds(Base), ("trade", "fee"))
        self.assertEqual(parser_kinds(Sub), ("trade", "rename", "fee"))

    def test_absent_claims_pass_and_say_so(self):
        from taxjson.parsers.conformance import unclaimed_note
        self.assertIsNone(unclaimed_note(FIXTURES / "generic"))
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "mine"
            shutil.copytree(FIXTURES / "generic", folder)
            _set_claims(folder, cash_kinds="")
            self.assertIn("claims no row kinds: none is asked for (kinds = \"...\" in its "
                          "fixture.toml)", unclaimed_note(folder))
            _set_claims(folder)
            found, rows, _skipped = check(registry.parser_class("generic"), folder)
            self.assertEqual(found, [])
            self.assertGreater(rows, 0)
            r = _tjs("parser-check", "generic", "--fixtures", str(folder))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(f"Info: generic: {folder} claims no row or cash kinds: none is "
                          f"asked for (kinds = \"...\" and cash_kinds = \"...\" in its "
                          f"fixture.toml)", r.stdout)
        r = _tjs("parser-check", "generic")
        self.assertNotIn("claims no", r.stdout)


class TestParserCheckCommand(_Zone):
    def test_a_parser_and_all(self):
        r = _tjs("parser-check", "generic")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("generic: ok", r.stdout)
        r = _tjs("parser-check", "qt")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("questrade: ok", r.stdout)
        r = _tjs("parser-check", "all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # One line per fixture folder: Kraken's ledgers fixture names its parser.
        folders = [d for d in FIXTURES.iterdir() if (d / "sample.csv").is_file()]
        self.assertEqual(r.stdout.count(": ok"), len(folders))
        r = _tjs("parser-check", "kraken")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("kraken: ok"), 2)
        self.assertIn("kraken_ledgers", r.stdout)

    def test_a_folder_read_with_an_unverified_layout_says_so(self):
        # TJ-2573: the Wealthsimple folder is read with the shipped layout,
        # never run on a real export: its pass is said to prove no real
        # file; the generic folder's own sidecar says nothing.
        r = _tjs("parser-check", "generic")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        said = [ln for ln in r.stdout.splitlines() if "unverified" in ln]
        self.assertEqual(said, [
            f"Info: generic: {FIXTURES / 'wealthsimple'} is read with the layout "
            f"wealthsimple, unverified against a real export: a pass checks the layout "
            f"against its own made-up sample, not a real file"])

    def test_findings_and_refusals(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "mine"
            shutil.copytree(FIXTURES / "generic", folder)
            golden = folder / "expected.json"
            golden.write_text("[]")
            r = _tjs("parser-check", "generic", "--fixtures", str(folder))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("diverged from the golden", r.stdout)
            r = _tjs("parser-check", "generic", "--fixtures", str(Path(td) / "gone"))
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("no fixture folder", r.stdout + r.stderr)
        r = _tjs("parser-check", "nobody")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown parser", r.stdout + r.stderr)
        with tempfile.TemporaryDirectory() as td:      # no checkout there
            r = _tjs("parser-check", "generic", env={"TAXJSON_DEV_DIR": td})
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("not a git checkout", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
