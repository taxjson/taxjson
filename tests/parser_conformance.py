"""Conformance harness every brokerage parser subclasses (see
test_parser_conformance.py for the six registrations, and CONTRIBUTING.md
for the new-broker checklist).

A parser passes conformance when, against its committed synthetic fixture
(tests/fixtures/<broker>/sample.csv):

  * its output satisfies the transaction schema (zero errors AND zero
    warnings — fixtures are curated, so even warn-level findings mean
    either the fixture or the parser drifted);
  * its output equals the committed golden (expected.json) exactly;
  * parsing twice with fresh instances is byte-identical (determinism);
  * when the parser does row-level accounting, every fixture row is
    either consumed or counted as a skip — nothing vanishes.

Regenerate goldens after an INTENTIONAL parser-behavior change with:

    UPDATE_GOLDEN=1 python -m unittest tests/test_parser_conformance.py

then review the diff like any other code change — the golden IS the
parser's contract.

Fixtures must be SYNTHETIC (invented rows shaped like the broker's CSV) —
never real account data.
"""

import json
import os
from pathlib import Path

from taxjson.lib.brokerages.schema import validate_transactions

FIXTURES = Path(__file__).parent / "fixtures"


class ParserConformance:
    """Mixin — subclass alongside unittest.TestCase with:
        parser_cls    = the BaseBrokerage subclass
        fixture_dir   = fixture folder name under tests/fixtures/
        expected_skips = number of fixture rows the parser must COUNT as
                         skipped (None = parser does no row accounting)
    """

    parser_cls = None
    fixture_dir = None
    expected_skips = None

    # ------------------------------------------------------------ helpers

    def _paths(self):
        d = FIXTURES / self.fixture_dir
        return d / "sample.csv", d / "expected.json"

    def _parse(self):
        sample, _ = self._paths()
        parser = self.parser_cls()
        import io
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()):        # skip-summary notes
            txs = parser.parse_file(sample)
        return parser, txs

    # ------------------------------------------------------------- tests

    def test_schema(self):
        _, txs = self._parse()
        errors, warnings = validate_transactions(txs)
        self.assertEqual(errors, [], f"schema errors: {errors}")
        self.assertEqual(warnings, [], f"schema warnings: {warnings}")

    def test_golden(self):
        sample, golden = self._paths()
        _, txs = self._parse()
        if os.environ.get("UPDATE_GOLDEN"):
            golden.write_text(json.dumps(txs, indent=1, sort_keys=True)
                              + "\n")
        self.assertTrue(golden.exists(),
                        f"missing golden {golden} — run with UPDATE_GOLDEN=1")
        expected = json.loads(golden.read_text())
        self.assertEqual(
            txs, expected,
            f"{self.fixture_dir}: parser output diverged from the golden. "
            f"If the change is intentional, regenerate with UPDATE_GOLDEN=1 "
            f"and review the diff.")

    def test_determinism(self):
        _, first = self._parse()
        _, second = self._parse()
        self.assertEqual(first, second,
                         "parse_file is not deterministic across fresh "
                         "instances")

    def test_skip_accounting(self):
        parser, txs = self._parse()
        if self.expected_skips is None:
            self.skipTest(f"{self.fixture_dir}: parser does no row-level "
                          f"accounting")
        skipped = sum(parser._skip_counts.values())
        self.assertEqual(skipped, self.expected_skips)
        if parser._rows_seen is not None:
            unaccounted = (parser._rows_seen - parser._rows_consumed
                           - skipped)
            self.assertEqual(
                unaccounted, 0,
                f"{unaccounted} fixture row(s) neither consumed nor "
                f"counted as skipped — a silent-drop code path")
