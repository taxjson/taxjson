"""Fix round 7 (wave B), group "coinbase": the Coinbase readers and the
crypto send / price helpers (backlog TJ-0865, TJ-0870, TJ-0916,
TJ-0747, TJ-0652, TJ-0663, TJ-0667, TJ-0671, TJ-0744).

All rows, ids and amounts are synthetic.

Formerly: test_fix_1029_r7b_coinbase
"""
import os
import tempfile
import unittest
from pathlib import Path

from _support.cb_sidecar import _parse_sidecar


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of Fees and/or Spread),Fees and/or Spread,Notes\n")


class TestRemovedExportIsStale(unittest.TestCase):
    """TJ-0671: an export removed since the last parse makes the
    transfer evidence stale, as an edited one does.

    Formerly: test_fix_1029_r7b_coinbase.TestRemovedExportIsStale"""

    def test_removed_export_is_named(self):
        from taxjson.lib.crypto_sends import stale_evidence
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            work.mkdir()
            d = root / "inputs" / "coin"
            d.mkdir(parents=True)
            a, b = d / "history.csv", d / "sends.csv"
            a.write_text(_HDR + "BUY901,2025-01-02 17:00:00 UTC,Buy,ZZE,2,"
                         "CAD,10,20,20,0,Bought 2 ZZE\n")
            b.write_text(_HDR + "SEND901,2025-03-03 17:00:00 UTC,Send,ZZE,"
                         "1,CAD,20,20,20,0,Sent 1 ZZE\n")
            side = work / "coin_coinbase_transfers.json"
            _parse_sidecar(side, a, b)
            both = {"coin": [("coinbase", a), ("coinbase", b)]}
            self.assertEqual(stale_evidence(work, both), [])
            b.unlink()
            only_a = {"coin": [("coinbase", a)]}
            self.assertEqual(stale_evidence(work, only_a),
                             ["coin (sends.csv removed since the last "
                              "parse)"])
            _parse_sidecar(side, a)
            self.assertEqual(stale_evidence(work, only_a), [])
            # An export added with an old mtime (a copy keeping its
            # stamp) is new evidence too.
            b.write_text(_HDR)
            os.utime(b, (1, 1))
            self.assertEqual(stale_evidence(work, both),
                             ["coin (sends.csv added since the last "
                              "parse)"])

    def test_an_older_sidecar_without_the_list_is_read_as_before(self):
        import json
        from taxjson.lib.crypto_sends import stale_evidence
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            a = work / "a.csv"
            a.write_text(_HDR)
            os.utime(a, (1, 1))
            (work / "coin_coinbase_transfers.json").write_text(json.dumps(
                {"transactions": [],
                 "metadata": {"kind": "transfer_sidecar"}}))
            self.assertEqual(stale_evidence(work,
                                            {"coin": [("coinbase", a)]}), [])


class TestSendIdsFollowBrokerIds(unittest.TestCase):
    """TJ-0663: two sends alike to the second keep their send ids (and
    so their saved decisions) when the export lists them in the other
    order.

    Formerly: test_fix_1029_r7b_coinbase.TestSendIdsFollowBrokerIds"""

    _ROWS = ["AAA-SEND-901,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,10,10,"
             "10,0,Sent 1 ZZE\n",
             "BBB-SEND-902,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,11,11,"
             "11,0,Sent 1 ZZE\n"]

    def _sids(self, rows):
        from taxjson.lib.crypto_sends import (assign_send_ids,
                                              load_transfer_rows)
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "work"
            work.mkdir()
            f = Path(td) / "coinbase.csv"
            f.write_text(_HDR + "".join(rows))
            _parse_sidecar(work / "coin_coinbase_transfers.json", f)
            out = load_transfer_rows(work, ["coin"])
        assign_send_ids(out)
        return {r["ref"]: (r["sid"], r["price"]) for r in out}

    def test_reversed_export_keeps_each_send_id(self):
        fwd = self._sids(self._ROWS)
        rev = self._sids(list(reversed(self._ROWS)))
        self.assertEqual(fwd, rev)
        self.assertEqual(sorted(sid for sid, _p in fwd.values()),
                         ["cb-20250303T120000-ZZE-1",
                          "cb-20250303T120000-ZZE-1-2"])

    def test_the_raw_id_is_not_kept(self):
        from taxjson.lib.crypto_sends import _id_key
        self.assertNotIn("SEND", _id_key("AAA-SEND-901-xfer"))
        self.assertEqual(_id_key(""), "")


if __name__ == "__main__":
    unittest.main()
