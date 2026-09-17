"""Tests for the keyword-prefixed ticker-map and the DELETE rule.

A ticker-map line is `KEYWORD from [to]` — GLOBAL (rename everywhere),
TOBASE (consolidate when converting to base), JOURNAL (consolidate +
net in the holdings view), DELETE (nuke a ticker's transactions). The
DELETE is audited (a NOTE with net qty / amount) and a guardrail warns
when the dropped ticker's net quantity looks like a real position.
"""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.bin.taxjson_ticker_map import (
    load_map_file, apply_drops, merge_renames,
)


def _map(content):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / 'ticker.map'
        p.write_text(content)
        return load_map_file(p)


def _tx(symbol, qty, net):
    return {'action': 'BUYSELL', 'date': '2025-10-22', 'symbol': symbol,
            'quantity': qty, 'net_amount': net}


def _drops(txs, drops):
    buf = io.StringIO()
    with patch.object(sys, 'stderr', buf):
        kept = apply_drops(txs, drops)
    return kept, buf.getvalue()


class TestLoadMapFile(unittest.TestCase):
    def test_keywords_sort_into_buckets(self):
        tmap = _map(
            "# a comment\n"
            "GLOBAL  DFDV1.US  DFDV.US\n"
            "TOBASE  AEM.US    AEM.TO\n"
            "journal DLR.U.TO  DLR.TO\n"     # keyword is case-insensitive
            "DELETE  RGLD.CAD.TO\n"
        )
        self.assertEqual(tmap.glob, {'DFDV1.US': 'DFDV.US'})
        self.assertEqual(tmap.tobase, {'AEM.US': 'AEM.TO'})
        self.assertEqual(tmap.journal, {'DLR.U.TO': 'DLR.TO'})
        self.assertEqual(tmap.delete, {'RGLD.CAD.TO'})

    def test_unkeyworded_line_is_skipped(self):
        # A bare `from to` line (no keyword) is malformed and ignored —
        # so a typo can't silently become a rename.
        tmap = _map("AEM.US AEM.TO\n")
        self.assertEqual((tmap.glob, tmap.tobase, tmap.journal, tmap.delete),
                         ({}, {}, {}, set()))

    def test_merge_renames_scopes_by_to_base(self):
        tmap = _map("GLOBAL G.US G.TO\nTOBASE T.US T.TO\nJOURNAL J.US J.TO\n")
        # Raw merge (no conversion): GLOBAL only.
        self.assertEqual(merge_renames(tmap, to_base=False), {'G.US': 'G.TO'})
        # Main merge (to base): GLOBAL + TOBASE + JOURNAL.
        self.assertEqual(merge_renames(tmap, to_base=True),
                         {'G.US': 'G.TO', 'T.US': 'T.TO', 'J.US': 'J.TO'})


class TestApplyDrops(unittest.TestCase):
    def test_removes_matching_symbol_and_audits(self):
        txs = [_tx('RGLD.CAD.TO', 0.0, 0.0),
               _tx('RGLD.CAD.TO', -0.0026, 0.64),
               _tx('NVDA.US', 100, 50000)]
        kept, err = _drops(txs, {'RGLD.CAD.TO'})
        self.assertEqual([t['symbol'] for t in kept], ['NVDA.US'])
        # The deletion is audited, not silent.
        self.assertIn('DROP removed 2 RGLD.CAD.TO', err)
        self.assertIn('net qty -0.0026', err)

    def test_fractional_artifact_no_warning(self):
        _, err = _drops([_tx('RGLD.CAD.TO', -0.0026, 0.64)], {'RGLD.CAD.TO'})
        self.assertIn('NOTE:', err)
        self.assertNotIn('warning:', err)

    def test_real_position_triggers_guardrail_warning(self):
        kept, err = _drops([_tx('NVDA.US', 100, 50000)], {'NVDA.US'})
        self.assertEqual(kept, [])          # still dropped...
        self.assertIn('warning:', err)      # ...but loudly flagged
        self.assertIn('real position', err)

    def test_empty_drops_is_noop(self):
        txs = [_tx('AEM.US', 10, 100)]
        kept, err = _drops(txs, set())
        self.assertEqual(kept, txs)
        self.assertEqual(err, '')


if __name__ == '__main__':
    unittest.main()
