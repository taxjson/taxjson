"""Regression pins for the stage-tools audit (merge2 / sort / validate /
ticker-map / convert-currency / fill-crypto / convert-tt).

One class per defect, numbered as in the audit:

  1. null numerics crashed every loader with a bare `float(None)`
     traceback while `taxjson-validate` said OK.
  2. wrong-typed but validate-clean rows (`"quantity": "10"`,
     `"symbol": 0`) crashed downstream tools; coerce_transaction_row is
     now the single type funnel.
  3. `taxjson-validate` itself crashed on wrong JSON types.
  4. merge2 / convert-currency bypassed the shared loader (no `#`
     comment stripping, no qty alias) and merge2 emitted an EMPTY merge
     at exit 0 on a commented input.
  5. ticker-map DELETE vs GLOBAL order differed between merge2 and the
     standalone tool.
  6. currency codes compared case/whitespace-sensitively, and a
     currency entirely absent from --rates converted at the implicit
     default with only a warning.
  7. rates-file parser skipped malformed lines silently and accepted
     NaN / negative / zero rates.
  8. fill-crypto cached on the raw symbol (stale after a ticker remap)
     and priced qty=0 rows at one unit of FMV.
  9. `taxjson-sort --dedup` (non-strict) silently DROPPED rows failing
     a private validator, with a stderr line the pipeline's diag
     marker regex ignored.
 10. `.tt` files had no split-fill disambiguation, so two identical
     hand-entered lines collapsed under --dedup.

Formerly: test_stage_tools_audit
"""
import tempfile
import unittest
from pathlib import Path

from taxjson.model.transaction import TaxTransaction
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestD10TtSplitFills(unittest.TestCase):
    """Formerly: test_stage_tools_audit.TestD10TtSplitFills"""
    LINE = "BUYSELL 2025-01-15 09:30:00 AAPL.US 100 USD 150.00 15000.00 5.00"

    def test_identical_tt_lines_get_distinct_ids(self):
        from taxjson.lib.tt_format import tt_to_json, parse_tt_line
        from taxjson.bin.taxjson_sort import deduplicate
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.tt'
            p.write_text(self.LINE + "\n" + self.LINE + "\n")
            txs = tt_to_json(p, 'Margin')['transactions']
        self.assertEqual(len(txs), 2)
        self.assertNotEqual(txs[0]['id'], txs[1]['id'])
        # First keeps its stable id; second carries the fill marker and
        # its id agrees with a JSON-native load of the same content.
        self.assertEqual(txs[0]['id'], parse_tt_line(self.LINE, 'Margin')['id'])
        self.assertIn('[fill #2]', txs[1]['description'])
        self.assertEqual(txs[1]['id'], TaxTransaction(
            **{k: v for k, v in txs[1].items() if k != 'id'}).id)
        self.assertEqual(len(deduplicate([TaxTransaction(**t) for t in txs])), 2)

    def test_declared_transfers_are_not_marked(self):
        from taxjson.lib.tt_format import tt_to_json
        from taxjson.lib.tt_format import MANUAL_TRANSFER_DECLARATION
        line = "TRANSFER 2025-01-20 09:30:00 AAPL.US -100 USD 150.00 15000.00 DECLARED"
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.tt'
            p.write_text(line + "\n" + line + "\n")
            txs = tt_to_json(p, 'Margin')['transactions']
        for t in txs:
            self.assertEqual(t['description'], MANUAL_TRANSFER_DECLARATION)


if __name__ == '__main__':
    unittest.main()
