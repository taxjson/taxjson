"""Cross-currency rollover renames vs the raw (native-currency) stage.

A s. 85.1(5) rollover across currencies (SSL.TO [CAD] → RGLD.US [USD])
is modeled as a SPLIT rename, so the native-currency book would have a
CAD pool receiving USD trades — taxjson-gains rightly refuses, but that
refusal used to kill the WHOLE `taxjson run` with a traceback (real
rrsp data, 2026-07-25). The pipeline now detects the condition
(rename-aware) and skips the raw stage for that account with a warning;
the converted, tax-authoritative books build normally.
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_run import _raw_mixed_currency_symbols


def _tx(action, sym, cur, **kw):
    return {"action": action, "date": kw.get("date", "2025-10-22"),
            "time": "09:30:00", "symbol": sym, "currency": cur,
            "quantity": kw.get("qty", 1), "net_amount": kw.get("net", 1.0),
            "account": "rrsp", **{k: v for k, v in kw.items()
                                  if k in ("symbol_new",)}}


def _write(txs):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"transactions": txs}, f)
    f.close()
    return Path(f.name)


class TestRawMixedCurrencyDetector(unittest.TestCase):
    def test_cross_currency_rollover_rename_detected(self):
        p = _write([
            _tx("BUYSELL", "SSL.TO", "CAD", qty=5024),
            _tx("SPLIT", "SSL.TO", "CAD", qty=0.0625,
                symbol_new="RGLD.US"),
            _tx("BUYSELL", "RGLD.US", "USD", qty=-314,
                date="2025-11-03"),
        ])
        self.assertEqual(_raw_mixed_currency_symbols(p), ["RGLD.US"])

    def test_rename_chain_followed(self):
        # A→B→C where A is CAD and C's trades are USD.
        p = _write([
            _tx("BUYSELL", "A.TO", "CAD"),
            _tx("SPLIT", "A.TO", "CAD", symbol_new="B.TO"),
            _tx("SPLIT", "B.TO", "CAD", symbol_new="C.US"),
            _tx("BUYSELL", "C.US", "USD", date="2025-11-03"),
        ])
        self.assertEqual(_raw_mixed_currency_symbols(p), ["C.US"])

    def test_single_currency_book_clean(self):
        p = _write([
            _tx("BUYSELL", "XIU.TO", "CAD"),
            _tx("SPLIT", "XIU.TO", "CAD", symbol_new="XIU2.TO"),
            _tx("BUYSELL", "XIU2.TO", "CAD", date="2025-11-03"),
        ])
        self.assertEqual(_raw_mixed_currency_symbols(p), [])

    def test_distinct_listings_not_flagged(self):
        # AEM.US / AEM.TO are separate symbols in the raw view — two
        # currencies across two symbols is fine.
        p = _write([
            _tx("BUYSELL", "AEM.TO", "CAD"),
            _tx("BUYSELL", "AEM.US", "USD"),
        ])
        self.assertEqual(_raw_mixed_currency_symbols(p), [])

    def test_income_rows_do_not_flag(self):
        # A USD dividend against a CAD stock row is common broker
        # noise for the detector's purposes — only position-bearing
        # actions count.
        p = _write([
            _tx("BUYSELL", "XIU.TO", "CAD"),
            _tx("DIVIDEND", "XIU.TO", "USD"),
        ])
        self.assertEqual(_raw_mixed_currency_symbols(p), [])

    def test_unreadable_file_returns_empty(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json",
                                        delete=False)
        f.write("{oops")
        f.close()
        self.assertEqual(_raw_mixed_currency_symbols(Path(f.name)), [])


if __name__ == "__main__":
    unittest.main()
