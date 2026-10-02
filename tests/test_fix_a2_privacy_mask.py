"""Re-audit-2 fixes: id masking in diagnostics (fix list privacy-02).

A2-0756 / A2-0757 / A2-1381 (every Kraken refid / txid in a note, an
error or a skip summary is masked to its first 2 characters + ***),
A2-0461 (the file-name account-id mask is pinned at the IB and generic
broker diagnostics). Synthetic data only.
"""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.brokerages.kraken import KrakenBrokerage

_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_KL_H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
         "balance\n")

# Synthetic ids, long enough that a leak is unmistakable.
_REF = "TSECRETREFAAAA"
_TX = "LSECRETTXIDBBBB"


def _kraken(files, which):
    """Parse; return (txs or exception, stderr, extractor)."""
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text)
        k = KrakenBrokerage()
        k.stablecoins_as_cash = True
        buf = io.StringIO()
        out = None
        with redirect_stderr(buf):
            try:
                out = k.parse_file(Path(td) / which)
            except ValueError as e:
                out = e
        return out, buf.getvalue(), k


class TestKrakenIdsMasked(unittest.TestCase):
    """A2-0756 / A2-0757 / A2-1381."""

    def _assert_masked(self, text):
        self.assertNotIn(_REF, text)
        self.assertNotIn(_TX, text)
        self.assertNotIn(_REF[2:6], text)
        self.assertNotIn(_TX[2:6], text)

    def _everything(self, out, err, k):
        skips = " ".join(getattr(k, "_skip_counts", {}) or {})
        return f"{err}\n{out if isinstance(out, Exception) else ''}\n{skips}"

    def test_multi_leg_note_masks_the_refid(self):
        led = _KL_H + "".join(r + "\n" for r in [
            f"L1,{_REF},2025-03-01 12:00:00,spend,,currency,ADA,spot,-10,0,0",
            f"L2,{_REF},2025-03-01 12:00:00,spend,,currency,DOT,spot,-1,0,0",
            f"L3,{_REF},2025-03-01 12:00:00,receive,,currency,XETH,spot,"
            "0.004,0,0.004"])
        out, err, k = _kraken({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIsInstance(out, list)
        self.assertIn("TS***", err)
        self._assert_masked(self._everything(out, err, k))
        # The refid stays the work-JSON id (data, not a message).
        self.assertTrue(any(_REF in (t.get("id") or "") for t in out))

    def test_both_sides_many_legs_error_masks_the_refid(self):
        led = _KL_H + "".join(r + "\n" for r in [
            f"L1,{_REF},2025-03-01 12:00:00,spend,,currency,ADA,spot,-10,0,0",
            f"L2,{_REF},2025-03-01 12:00:00,spend,,currency,DOT,spot,-1,0,0",
            f"L3,{_REF},2025-03-01 12:00:00,receive,,currency,XETH,spot,"
            "0.004,0,0.004",
            f"L4,{_REF},2025-03-01 12:00:00,receive,,currency,SOL,spot,"
            "0.1,0,0.1"])
        out, err, k = _kraken({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIsInstance(out, ValueError)
        self.assertIn("TS***", str(out))
        self._assert_masked(self._everything(out, err, k))

    def test_orphan_leg_skip_category_masks_the_refid(self):
        led = _KL_H + (f"L1,{_REF},2025-03-01 12:00:00,spend,,currency,"
                       "ADA,spot,-10,0,0\n")
        out, err, k = _kraken({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIn("orphan spend (refid TS***)", k._skip_counts)
        self._assert_masked(self._everything(out, err, k))

    def test_ledger_parse_error_masks_the_txid(self):
        led = _KL_H + (f"{_TX},{_REF},2025-03-01 12:00:00,deposit,,currency,"
                       "ADA,spot,abc,0,0\n")
        out, err, k = _kraken({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIsInstance(out, ValueError)
        self.assertIn("LS***", str(out))
        self._assert_masked(self._everything(out, err, k))

    def test_trades_parse_error_masks_the_txid(self):
        t = _KT_H + (f"{_TX},O1,XBT/CAD,2025-06-02 16:00:00,buy,limit,"
                     "abc,9000,10,0.1,,,\n")
        out, err, k = _kraken({"kr_trades.csv": t}, "kr_trades.csv")
        self.assertIsInstance(out, ValueError)
        self.assertIn("LS***", str(out))
        self._assert_masked(self._everything(out, err, k))

    def test_trade_ledger_join_error_masks_the_txid(self):
        t = _KT_H + (f"{_TX},O1,XBT/CAD,2025-06-02 16:00:00,buy,limit,"
                     "90000,9000,10,0.1,,,\n")
        led = _KL_H + "".join(r + "\n" for r in [
            f"L1,{_TX},2025-06-02 16:00:00,trade,,currency,ZCAD,spot,"
            "-9010,0,0",
            f"L2,{_TX},2025-06-02 16:00:00,trade,,currency,ADA,spot,"
            "5,0,5"])
        out, err, k = _kraken({"kr_trades.csv": t, "kr_ledgers.csv": led},
                              "kr_trades.csv")
        self.assertIsInstance(out, ValueError)
        self.assertIn("LS***", str(out))
        self._assert_masked(self._everything(out, err, k))


if __name__ == "__main__":
    unittest.main()
