"""Audit batch A (2026-10): the crypto and cash books.

74: a Coinbase Buy / Sell of a stablecoin priced in CAD with a blank
Total read the Total as 0 in the FX-on-cash ledger v2 reader
(`coinbase_cash_events`, now the parse's) and the crypto-sends pool
(`coinbase_pool_flows`): a zero-cost acquisition. Both now derive it as
the parser does (Subtotal +/- fee, else quantity x price +/- fee) and
refuse, naming the row, when nothing is there to derive it from.

79: `crypto_sends.build_report` added up each export's stablecoin pool
flows, so two overlapping Kraken ledgers (or Coinbase exports) counted
the shared entries twice. Each flow now carries its identity (the
Kraken refid, the Coinbase ID or row) and overlapping files are read
once; genuine same-content rows in one file still both count.

Finding 75 (Questrade cash conversions in a newest-first export) is
pinned in
rules.ca.fx.test_foreign_cash_inputs_read_or_refused.TestQuestradeCashEvents.

Every fixture is SYNTHETIC: invented ids, amounts and dates.

Formerly: test_fix_audit_batch_a
"""
import tempfile
import unittest
from pathlib import Path

from _support.crypto_send_flows import _UTC, _kr

from taxjson.lib import crypto_sends as cs


def _write(tmp, name, text):
    p = Path(tmp) / name
    p.write_text(text)
    return p


# ------------------------------------- 79: overlapping exports, read once

def _f(units, key):
    return {"units": units, "key": key}


class TestMergePoolFlows(unittest.TestCase):
    """Formerly: test_fix_audit_batch_a.TestMergePoolFlows"""
    def _units(self, *files):
        return [f["units"] for f in cs.merge_pool_flows(list(files))]

    def test_a_repeated_entry_counts_once(self):
        a = [_f(100, ("kraken", "R1"))]
        b = [_f(100, ("kraken", "R1")), _f(50, ("kraken", "R2"))]
        self.assertEqual(self._units(a, b), [100, 50])

    def test_genuine_duplicates_in_one_file_both_count(self):
        k = ("coinbase", "row", ("same", "cells"))
        self.assertEqual(self._units([_f(5, k), _f(5, k)]), [5, 5])
        # ...and an overlapping copy of that file adds nothing,
        self.assertEqual(self._units([_f(5, k), _f(5, k)],
                                     [_f(5, k), _f(5, k)]), [5, 5])
        # while a later file with a third copy adds the third only.
        self.assertEqual(self._units([_f(5, k)], [_f(5, k), _f(5, k)]),
                         [5, 5])

    def test_a_flow_with_no_key_always_counts(self):
        self.assertEqual(self._units([_f(1, None)], [_f(1, None)]), [1, 1])


class TestKrakenRepeatedTxid(_UTC):
    """Formerly: test_fix_audit_batch_a.TestKrakenRepeatedTxid"""
    def test_a_pasted_twice_entry_is_read_once(self):
        rows = [("R1", "2025-01-10 12:00:00", "spend", "", "ZCAD", -140, 0),
                ("R1", "2025-01-10 12:00:00", "receive", "", "USDC", 100, 0)]
        text = _kr(rows)
        text += text.split("\n", 1)[1]            # the rows again
        with tempfile.TemporaryDirectory() as tmp:
            flows = cs.kraken_pool_flows(_write(tmp, "ledgers.csv", text))
        self.assertEqual([(f["units"], f["cad"], f["key"]) for f in flows],
                         [(100.0, -140.0, ("kraken", "R1"))])

    def test_one_txid_with_different_content_is_refused(self):
        text = _kr([("R1", "2025-01-10 12:00:00", "receive", "", "USDC",
                     100, 0)])
        text += _kr([("R1", "2025-01-10 12:00:00", "receive", "", "USDC",
                      90, 0)]).split("\n", 1)[1]
        with tempfile.TemporaryDirectory() as tmp, \
                self.assertRaises(ValueError) as cm:
            cs.kraken_pool_flows(_write(tmp, "ledgers.csv", text))
        self.assertIn("DIFFERENT content", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
