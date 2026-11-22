"""Kraken on the parse template (step 5h (7)): the row accounting of a
ledger through the template, the zero-transactions safety net for a
ledger whose trade rows no trades export covers, a file that is not a
Kraken export, the export's assets restored. Synthetic data only."""
import os
import unittest
from unittest import mock

from _support.crypto_brokerage import _KL_H2, _brokerage
from _support.kraken_ledger import _ledger, _parse

from taxjson.lib.brokerages import kraken


def setUpModule():
    # The ledger stamps are UTC; the local date needs a zone. The a2
    # module, whose helpers these tests use, sets one in its own
    # setUpModule, which a run calls for that module's tests, not these
    # (and never puts it back: TJ-3960). Restored after this module's
    # tests, not left for every later test of the process (TJ-3952).
    zone = mock.patch.dict(os.environ, {"TAXJSON_LOCAL_TZ": os.environ.get("TAXJSON_LOCAL_TZ", "America/Toronto")})
    zone.start()
    unittest.addModuleCleanup(zone.stop)


def _reconciled(k):
    return (k._rows_consumed >= 0
            and k._rows_seen == k._rows_consumed + sum(k._skip_counts.values()))


class TestKrakenOnTheTemplate(unittest.TestCase):
    def test_trade_rows_without_a_trades_export_keep_the_safety_net(self):
        body = _ledger([
            "L1,TX1,2025-06-02 16:00:00,trade,,currency,XXBT,spot,0.1,0,0",
            "L2,TX1,2025-06-02 16:00:00,trade,,currency,ZUSD,spot,-4000,0,0",
        ])
        txs, err, k = _parse({"kr_ledgers.csv": body}, "kr_ledgers.csv", want_extractor=True)
        self.assertEqual(txs, [])
        self.assertIn("UNBOOKED", err)
        self.assertIsNone(k.zero_tx_reason)          # the tool's loud path
        self.assertNotIn("recognized non-event", err)
        self.assertEqual((k._rows_consumed, sum(k._skip_counts.values())), (0, 2))
        self.assertTrue(_reconciled(k), (k._rows_seen, k._rows_consumed, k._skip_counts))

    def test_a_trade_joined_across_two_exports_counts_each_files_own_row(self):
        # One leg per file; the pairing joins them through the account
        # context. Each file counts its own row once, moved to the
        # non-event the pairing found; nothing goes negative.
        files = {"kr_a_2026.csv": _KL_H2 + "LA1,R1,2026-03-02 00:00:00,spend,,currency,ZUSD,spot,-5,0,0,5,0,USD\n",
                 "kr_b_2026.csv": _KL_H2 + "LB1,R1,2026-03-02 00:00:00,receive,,currency,ZCAD,spot,7,0,7,5,0,USD\n"}
        r, _side = _brokerage(files, "kraken", "--lint")
        lint = [ln for ln in r.stderr.splitlines() if ln.startswith("lint: ")]
        self.assertEqual(len(lint), 2, r.stderr)
        for line in lint:
            self.assertIn("rows=1 consumed=0 skipped=1 unaccounted=0", line)
        self.assertNotIn("2 recognized non-event", r.stderr)

    def test_a_relabel_swap_counts_its_two_rows_once(self):
        body = _ledger([
            "L1,R1,2025-03-01 12:00:00,spend,,currency,DOT28.S,spot,-5,0,0",
            "L2,R1,2025-03-01 12:00:00,receive,,currency,DOT,spot,5,0,0",
        ])
        txs, err, k = _parse({"kr_ledgers.csv": body}, "kr_ledgers.csv", want_extractor=True)
        self.assertEqual(txs, [])
        self.assertEqual((k._rows_seen, k._rows_consumed), (2, 0))
        self.assertTrue(_reconciled(k))
        self.assertIn("relabel swap", err)

    def test_fiat_and_repeated_rows_are_reported_by_the_parsers_own_notes(self):
        row = "L1,R1,2025-03-01 12:00:00,deposit,,currency,ZUSD,spot,100,0,0"
        body = _ledger([row, row])
        txs, err, k = _parse({"kr_ledgers.csv": body}, "kr_ledgers.csv", want_extractor=True)
        self.assertEqual(txs, [])
        self.assertIn("repeated ledger row(s)", err)
        self.assertIn("fiat-cash or zero-amount", err)
        self.assertNotIn("recognized non-event", err)
        self.assertNotIn("unclassified", err)
        self.assertTrue(_reconciled(k))

    def test_a_file_that_is_not_a_kraken_export_keeps_no_row_count(self):
        txs, _err, k = _parse({"kr_x.csv": "Date,Something\n2025-01-01,x\n"}, "kr_x.csv",
                              want_extractor=True)
        self.assertEqual((txs, k._rows_seen), ([], None))

    def test_release_restores_the_row_readers_assets(self):
        before = kraken._KNOWN_ASSETS
        _parse({"kr_ledgers.csv": _ledger([])}, "kr_ledgers.csv")
        self.assertIs(kraken._KNOWN_ASSETS, before)


if __name__ == "__main__":
    unittest.main()
