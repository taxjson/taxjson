"""Re-audit-2 error paths (errors lists, crypto / FX caches, crypto-sends,
rates files). Synthetic data only; caches live under temporary paths and
nothing touches the network.

A2-0772 / A2-0773 / A2-1403 / A2-1446 / A2-0464: a damaged entry in
  ~/.crypto_price_cache.json (null, "abc", true, Infinity) or a cache
  that is not a JSON object is a cache miss, never a traceback or a
  price of 1.0 / inf.
A2-0474 / A2-0800 / A2-1446: a damaged block of
  ~/.currency_price_cache.json (_coverage / _boc / _boc_noon of the
  wrong shape) is dropped with a warning naming the cache.
A2-0465 / A2-0467 / A2-0775 / A2-1407: duplicate_lines reads a .tt with
  a BOM like convert-tt does.
A2-1405: a generated crypto_sends.tt re-saved with a BOM is still ours.
A2-1406: a wrong-shape transfer sidecar is one line naming the file.
A2-1404 / A2-1415 / A2-0776 / A2-1449: sends.json as a directory, a
  wrong-type note, a BOM.
A2-0790 / A2-1434 / A2-1411: an unreadable or BOM'd rates file.
A2-1437: convert-currency --rates naming a missing file.
A2-1423: fees-sum with a NaN rate.
A2-1424: fx-cash on a book a futures row refuses.

Formerly: test_fix_a2_errA_c
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _hermetic  # noqa: F401  (the synthetic HOME before taxjson's caches read it: TJ-3857)
from taxjson.lib import crypto_sends as cs
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestDuplicateLinesAndGeneratedTt(unittest.TestCase):
    """Formerly: test_fix_a2_errA_c.TestDuplicateLinesAndGeneratedTt"""
    def _acct(self, td):
        acct = Path(td) / "inputs" / "a"
        acct.mkdir(parents=True)
        return acct

    def test_bom_first_line_is_checked(self):
        # A2-0465 / A2-0467 / A2-0775 / A2-1407.
        e = {"id": "kr-x", "date": "2026-05-04", "time": "22:00:00",
             "symbol": "SOL", "quantity": 1000.0}
        with tempfile.TemporaryDirectory() as td:
            acct = self._acct(td)
            (acct / "hand.tt").write_text(
                "﻿BUYSELL 2026-05-04 22:00:00 SOL -1,000 CAD 1 1 0\n",
                encoding="utf-8")
            hits = cs.duplicate_lines(acct, [e])
        self.assertEqual([h["line"] for h in hits], [1])

    def test_generated_file_with_a_bom_is_still_generated(self):
        # A2-1405.
        with tempfile.TemporaryDirectory() as td:
            tt = self._acct(td) / cs.TT_NAME
            body = (cs.GENERATED_MARK + "\n# kr-20260504T220000-SOL-1: x\n"
                    "BUYSELL 2026-05-04 22:00:00 SOL -1 CAD 1 1 0\n")
            tt.write_text("﻿" + body, encoding="utf-8")
            self.assertEqual(cs.read_tt(tt), body)
            self.assertEqual(cs.tt_ids(tt), {"kr-20260504T220000-SOL-1"})
            self.assertEqual(cs.write_tt(tt, body), "unchanged")
            self.assertEqual(cs.write_tt(tt, body + "\n"), "written")
            self.assertEqual(cs.write_tt(tt, None), "removed")
            # A hand file is still refused.
            tt.write_text("﻿BUYSELL 2026-05-04 22:00:00 SOL -1 CAD "
                          "1 1 0\n")
            with self.assertRaises(ValueError):
                cs.write_tt(tt, body)

    def test_write_failure_is_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            tt = self._acct(td) / cs.TT_NAME
            tt.mkdir()
            with self.assertRaises(ValueError) as cm:
                cs.write_tt(tt, cs.GENERATED_MARK + "\n")
            self.assertIn(str(tt), str(cm.exception))


class TestTransferSidecarShape(unittest.TestCase):
    """Formerly: test_fix_a2_errA_c.TestTransferSidecarShape"""
    def test_wrong_shapes_are_one_line(self):
        # A2-1406: [] / ['x'] were AttributeError tracebacks.
        good = {"metadata": {"kind": "transfer_sidecar"},
                "transactions": [{"date": "2024-01-01", "quantity": -1.0,
                                  "symbol": "BTC"}]}
        with tempfile.TemporaryDirectory() as td:
            w = Path(td)
            f = w / "kr_kraken_transfers.json"
            for doc in ([], {"metadata": {"kind": "transfer_sidecar"},
                             "transactions": ["x"]},
                        {"metadata": {"kind": "transfer_sidecar"},
                         "transactions": 5},
                        {"metadata": {"kind": "transfer_sidecar"},
                         "transactions": [{"date": "2024-01-01",
                                           "quantity": "x"}]}):
                f.write_text(json.dumps(doc))
                with self.assertRaises(ValueError, msg=doc) as cm:
                    cs.load_transfer_rows(w, ["kr"])
                self.assertIn(f"could not read {f.name}", str(cm.exception))
            f.write_text(json.dumps(good))
            self.assertEqual(len(cs.load_transfer_rows(w, ["kr"])), 1)


class TestDecisionsFile(unittest.TestCase):
    """Formerly: test_fix_a2_errA_c.TestDecisionsFile"""
    def test_directory_is_refused_on_read_and_write(self):
        # A2-1404: read as "no decisions"; --set left sends.json.part.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sends.json"
            p.mkdir()
            with self.assertRaises(ValueError) as cm:
                cs.load_decisions(p)
            self.assertIn("not a regular file", str(cm.exception))
            with self.assertRaises(ValueError):
                cs.record_decision(p, "k", "gift")
            self.assertFalse((Path(td) / "sends.json.part").exists())

    def test_unwritable_save_is_one_line_and_leaves_no_part(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sends.json"
            from taxjson.lib import safe_write as _sw
            with mock.patch.object(_sw.os, "open",
                                   side_effect=PermissionError(
                                       13, "Permission denied")):
                # An OSError: one 'cannot write' line, exit 2 (A2-1416).
                with self.assertRaises(OSError) as cm:
                    cs.save_decisions(p, {"sends": {}})
            self.assertIn("cannot write", str(cm.exception))
            self.assertFalse((Path(td) / "sends.json.part").exists())

    def test_bom_is_accepted_and_note_is_checked(self):
        # A2-0776 / A2-1449: a BOM was 'not valid JSON ... delete it'.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sends.json"
            p.write_text("﻿" + json.dumps(
                {"sends": {"k": {"decision": "gift", "note": "hi"}}}),
                encoding="utf-8")
            self.assertEqual(cs.load_decisions(p)["sends"]["k"]["note"],
                             "hi")
            # A2-1415: a wrong-type field is one line naming file and id.
            for rec in ({"decision": "gift", "note": [1]},
                        {"decision": "gift", "price": [1]}):
                p.write_text(json.dumps({"sends": {"k": rec}}))
                with self.assertRaises(ValueError) as cm:
                    cs.load_decisions(p)
                self.assertIn("'k'", str(cm.exception))
                self.assertIn(str(p), str(cm.exception))
            p.write_text("{")
            with self.assertRaises(ValueError) as cm:
                cs.load_decisions(p)
            self.assertNotIn("delete", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
