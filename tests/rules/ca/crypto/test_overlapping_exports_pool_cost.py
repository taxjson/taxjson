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

from tax_rules import rule
from taxjson.rules import ids as R
from _support.crypto_send_flows import CB_HEADER, SIDE, USDC_ID, _UTC, _kr, _project

from taxjson.lib import crypto_sends as cs


def _write(tmp, name, text):
    p = Path(tmp) / name
    p.write_text(text)
    return p


_R1 = [("R1", "2025-01-10 12:00:00", "spend", "", "ZCAD", -140, 0),
       ("R1", "2025-01-10 12:00:00", "receive", "", "USDC", 100, 0)]
_R2 = [("R2", "2025-01-20 12:00:00", "spend", "", "ZCAD", -150, 0),
       ("R2", "2025-01-20 12:00:00", "receive", "", "USDC", 100, 0)]


class TestBuildReportOverlap(_UTC):
    """USDC 100 for CAD 140 (R1), then 100 for CAD 150 (R2); 100 sent
    on 02-01 at 1.40: ACB 100 x 290/200 = 145, 100 left. Counting R1
    twice gave 300 units at CAD 430: ACB 143.33, 200 left.

    Formerly: test_fix_audit_batch_a.TestBuildReportOverlap"""

    def _report(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root, cfg = _project(tmp, sidecar=SIDE)
            bf = [(broker, _write(tmp, name, text))
                  for broker, name, text in files]
            doc = cs.build_report(root, cfg, broker_files={"a": bf})
        sends = {s["id"]: s for s in doc["accounts"]["a"]["sends"]}
        return doc["pool"]["units_now"], sends[USDC_ID]["fx"]["acb"]

    @rule(R.CA_CRYPTO_08)
    def test_overlapping_kraken_ledgers_are_read_once(self):
        self.assertEqual(self._report([
            ("kraken", "ledgers_jan.csv", _kr(_R1)),
            ("kraken", "ledgers_all.csv", _kr(_R1 + _R2))]),
            (100.0, 145.0))

    @rule(R.CA_CRYPTO_08)
    def test_overlapping_coinbase_exports_are_read_once(self):
        r1 = ("c1,2025-01-10 12:00:00 UTC,Buy,USDC,100,CAD,1.4,140,140,0,"
              "Bought 100 USDC\n")
        r2 = ("c2,2025-01-20 12:00:00 UTC,Buy,USDC,100,CAD,1.5,150,150,0,"
              "Bought 100 USDC\n")
        self.assertEqual(self._report([
            ("coinbase", "cb_jan.csv", CB_HEADER + r1),
            ("coinbase", "cb_all.csv", CB_HEADER + r1 + r2)]),
            (100.0, 145.0))

    @rule(R.CA_CRYPTO_08)
    def test_distinct_entries_of_the_same_amount_both_count(self):
        # Two real 100-USDC buys for CAD 140 (distinct refids): 200 units
        # at CAD 280, ACB 140; a second file repeating them adds nothing.
        r1b = [(r[0].replace("R1", "R3"),) + r[1:] for r in _R1]
        self.assertEqual(self._report([
            ("kraken", "ledgers_a.csv", _kr(_R1 + r1b)),
            ("kraken", "ledgers_b.csv", _kr(_R1 + r1b))]),
            (100.0, 140.0))


if __name__ == "__main__":
    unittest.main()
