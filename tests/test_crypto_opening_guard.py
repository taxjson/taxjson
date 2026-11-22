"""A crypto account refuses an opening position from any converted file
(step 5b's review, TJ-2108): a .tt OPENING line and a generic mapping's
`opening` row alike, since the crypto books have no snapshot cut-off."""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.pipeline.inputs import _refuse_crypto_openings


def _json(folder, name, rows):
    p = Path(folder) / name
    p.write_text(json.dumps({"transactions": rows}), encoding="utf-8")
    return p


class TestCryptoOpeningGuard(unittest.TestCase):
    def test_an_opening_row_in_a_converted_export_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            trade = _json(td, "coins_kr_trades.json", [
                {"action": "BUYSELL", "date": "2025-01-05", "symbol": "ZQC",
                 "quantity": 1.0, "net_amount": 100.0, "currency": "CAD"}])
            opening = _json(td, "coins_generic_holdings.json", [
                {"action": "BUYSELL", "type": "opening", "date": "2025-01-02",
                 "symbol": "ZQC", "quantity": 2.0, "net_amount": 150.0,
                 "currency": "CAD"}])
            _refuse_crypto_openings("coins", [], {"kraken": trade})
            # An exchange's parsed file is never read (it carries no opening
            # row and may be large); a generic group's is.
            _refuse_crypto_openings("coins", [], {"kraken": opening})
            with self.assertRaises(SystemExit) as cm:
                _refuse_crypto_openings("coins", [], {"kraken": trade,
                                                      "generic": opening})
            self.assertNotEqual(cm.exception.code, 0)
            with self.assertRaises(SystemExit):
                _refuse_crypto_openings("coins", [opening], {})


if __name__ == "__main__":
    unittest.main()
