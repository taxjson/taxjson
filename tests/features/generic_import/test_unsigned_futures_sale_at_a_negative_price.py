"""Batch C (cash books, the FX-on-cash ledger v2, the generic importer,
ALLOWLOSS): 2026-10 audit findings 51, 52, 53, 70, 87. All data
synthetic.

Formerly: test_fix_batch_c_cash"""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


_HDR = "Date,Type,Ticker,Shares,Price,Amount,Commission,Currency\n"
_TOML = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
         'quantity="Shares"\nprice="Price"\namount="Amount"\n'
         'fee="Commission"\ncurrency="Currency"\n'
         '[actions]\n"BUY"="buy"\n"SELL"="sell"\n')


def _generic(csv_text):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_t.csv"
        c.write_text(csv_text)
        c.with_name(c.name + ".toml").write_text(_TOML)
        with redirect_stderr(io.StringIO()):
            return GenericBrokerage().parse_file(c)


class TestGenericNegativeFuturesSale(unittest.TestCase):
    """#70: an unsigned-quantity export's futures sale at a negative
    price (it pays cash) was refused as 'a buy by its own signs'.

    Formerly: test_fix_batch_c_cash.TestGenericNegativeFuturesSale"""

    BUY = "2025-04-01,BUY,/ZQX,1,10.00,-10000.00,0,USD\n"

    def test_unsigned_sale_booked_like_signed(self):
        unsigned = _generic(_HDR + self.BUY +
                            "2025-04-20,SELL,/ZQX,1,-10.00,-10000.00,0,USD\n")
        signed = _generic(_HDR + self.BUY +
                          "2025-04-20,SELL,/ZQX,-1,-10.00,-10000.00,0,USD\n")
        self.assertEqual([(t["quantity"], t["net_amount"]) for t in unsigned],
                         [(t["quantity"], t["net_amount"]) for t in signed])
        self.assertEqual(unsigned[1]["quantity"], -1.0)
        self.assertAlmostEqual(unsigned[1]["net_amount"], -10000.0)

    def test_negative_price_buy_does_not_flag_the_file(self):
        txs = _generic(_HDR + self.BUY +
                       "2025-04-15,BUY,/ZQX,1,-5.00,5000.00,0,USD\n"
                       "2025-04-20,SELL,/ZQX,2,-10.00,-20000.00,0,USD\n")
        self.assertEqual([t["quantity"] for t in txs], [1.0, 1.0, -2.0])

    def test_positive_price_contradiction_still_refused(self):
        with self.assertRaises(ValueError):
            _generic(_HDR + self.BUY +
                     "2025-04-20,SELL,/ZQX,1,10.00,-10000.00,0,USD\n")


if __name__ == "__main__":
    unittest.main()
