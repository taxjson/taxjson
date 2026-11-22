"""Conformance harness every brokerage parser subclasses (see
test_parser_conformance.py for one registration per fixture folder, and CONTRIBUTING.md
for the new-broker checklist).

A parser passes conformance when, against its committed synthetic fixture
(tests/fixtures/<broker>/sample.csv):

  * its output satisfies the transaction schema (zero errors AND zero
    warnings — fixtures are curated, so even warn-level findings mean
    either the fixture or the parser drifted);
  * its output equals the committed golden (expected.json) exactly;
  * parsing twice with fresh instances is byte-identical (determinism);
  * when the parser does row-level accounting, every fixture row is
    either consumed or counted as a skip — nothing vanishes;
  * `tjs run`'s router sends the fixture to the parser (detection);
  * under each country's settings it parses, passes the schema and
    leaves no row unaccounted; at one basis the rows and counts are the
    same under each country and under none, and the same under every
    basis but in the fields its `BASIS_INPUTS` names;
  * every row and cash event is of a kind, and the folder's fixture.toml
    claims the kinds they have (`kinds`, `cash_kinds`): one of each.

Regenerate goldens after an INTENTIONAL parser-behavior change with:

    UPDATE_GOLDEN=1 python -m unittest tests/test_parser_conformance.py

then review the diff like any other code change — the golden IS the
parser's contract.

Fixtures must be SYNTHETIC (invented rows shaped like the broker's CSV) —
never real account data.
"""

import os

from taxjson.parsers.conformance import (accounting_findings,
                                         determinism_findings,
                                         golden_findings, parse_fixture,
                                         read_fixture_meta, schema_findings,
    country_findings, detection_findings, fixture_parser_id,
    CASH_GOLDEN, cash_events_of, cash_golden_findings, kind_findings)
from taxjson.parsers.protocol import parser_kinds, row_kind
import _paths
from _tmpfiles import isolate_layouts

isolate_layouts()       # the box's own layouts never route a fixture (detection)

FIXTURES = _paths.FIXTURES


class ParserConformance:
    """Mixin — subclass alongside unittest.TestCase with:
        parser_cls    = the BaseBrokerage subclass
        fixture_dir   = fixture folder name under tests/fixtures/
        (the rows the parser must count as skipped: the folder's
         fixture.toml, `expected_skips`; absent = no row accounting;
         its `parser` when the folder's name is not the parser's id)
    """

    parser_cls = None
    fixture_dir = None

    # ------------------------------------------------------------ helpers

    def _paths(self):
        d = FIXTURES / self.fixture_dir
        return d / "sample.csv", d / "expected.json"

    def _parse(self):
        sample, _ = self._paths()
        return parse_fixture(self.parser_cls, sample)

    # ------------------------------------------------------------- tests
    # Each check is the package's (taxjson.parsers.conformance), the one
    # `tjs parser-check` runs; the suite asserts it finds nothing.

    def test_schema(self):
        _, txs = self._parse()
        self.assertEqual(schema_findings(txs), [])

    def test_golden(self):
        sample, golden = self._paths()
        _, txs = self._parse()
        found = golden_findings(txs, golden,
                                update=bool(os.environ.get("UPDATE_GOLDEN")))
        self.assertEqual(found, [], f"{self.fixture_dir}: {found}")

    def test_cash_golden(self):
        # The cash events of the fixture (the parse's) are the folder's cash
        # golden (expected_cash.json, PLAN step 16): every shipped folder has
        # one, `[]` where there are none.
        parser, _txs = self._parse()
        golden = FIXTURES / self.fixture_dir / CASH_GOLDEN
        cash, refused = cash_events_of(fixture_parser_id(FIXTURES / self.fixture_dir), parser)
        self.assertEqual(refused, [])
        found = cash_golden_findings(cash, golden,
                                     update=bool(os.environ.get("UPDATE_GOLDEN")))
        self.assertEqual(found, [], f"{self.fixture_dir}: {found}")
        self.assertTrue(golden.is_file(), f"{self.fixture_dir}: no {CASH_GOLDEN} (UPDATE_GOLDEN=1 "
                                          f"writes it)")

    def test_determinism(self):
        sample, _ = self._paths()
        self.assertEqual(determinism_findings(self.parser_cls, sample), [])

    def test_detection(self):
        # `tjs run` routes the fixture to its parser (the generic importer's
        # by its sidecar). A second fixture of one parser names the parser
        # in its fixture.toml.
        from taxjson.lib.brokerages.detect import detect
        sample, _ = self._paths()
        self.assertEqual(detection_findings(fixture_parser_id(FIXTURES / self.fixture_dir),
                                            sample, route=detect), [])

    def test_countries(self):
        sample, _ = self._paths()
        self.assertEqual(country_findings(self.parser_cls, sample), [])

    def test_kinds(self):
        # Every shipped folder claims its rows' and its cash events' kinds
        # (fixture.toml), each of them had, and its parser declares each
        # row's (`builds`) (PLAN step 16).
        parser, txs = self._parse()
        meta = read_fixture_meta(FIXTURES / self.fixture_dir)
        self.assertIn("kinds", meta)
        self.assertIn("cash_kinds", meta)
        cash, refused = cash_events_of(fixture_parser_id(FIXTURES / self.fixture_dir), parser)
        self.assertEqual(refused, [])
        self.assertEqual(kind_findings(txs, parser.expiry_rows, meta["kinds"], cash,
                                       meta["cash_kinds"], parser_kinds(self.parser_cls)), [])
        # A shipped folder claims exactly the kinds it has: a row of a new kind
        # is claimed with it (the kit asks no more of a folder of your own).
        self.assertEqual(set(meta["kinds"]), {row_kind(t, parser.expiry_rows) for t in txs})
        self.assertEqual(set(meta["cash_kinds"]), {e["kind"] for e in cash})

    def test_skip_accounting(self):
        # The expected count lives beside the fixture (fixture.toml), the
        # one place `tjs parser-check` reads it from too.
        parser, txs = self._parse()
        expected = read_fixture_meta(FIXTURES / self.fixture_dir).get("expected_skips")
        if expected is None:
            self.skipTest(f"{self.fixture_dir}: parser does no row-level "
                          f"accounting")
        self.assertEqual(accounting_findings(parser, expected), [])
