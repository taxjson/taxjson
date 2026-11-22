"""A futures contract closed at a stated price of 0 in a generic CSV
(backlog TJ-4558): the close's only cash is its commission, so the
settled P/L is the whole opening notional lost plus that commission.
The price-sign reading booked the commission as proceeds received and
put the loss 2 x the commission too low."""
import unittest

from _tmpfiles import isolate_layouts
from _support.generic_parse import _parse
from tax_rules import rule
from taxjson.lib.futures import settle_futures_dicts
from taxjson.rules import ids as R

isolate_layouts()

TOML = """\
[columns]
date = "Date"
action = "Type"
symbol = "Ticker"
quantity = "Qty"
price = "Price"
amount = "Amount"
currency = "Currency"
[actions]
"BUY" = "buy"
"SELL" = "sell"
[defaults]
currency = "USD"
"""
ROWS = ("Date,Type,Ticker,Qty,Price,Amount,Currency\n"
        "2025-06-02,BUY,F:ESM25,1,10.00,-500.00,USD\n"
        "2025-06-20,SELL,F:ESM25,-1,0,-2.50,USD\n")


@rule(R.CA_FX_04)
class TestFutureClosedAtPriceZero(unittest.TestCase):

    def test_the_commission_is_part_of_the_loss(self):
        txs, _, _ = _parse(ROWS, TOML)
        for t in txs:
            t["account"] = "margin"
        rows = settle_futures_dicts(txs, "average")
        self.assertEqual([r["net_amount"] for r in rows], [0.0, -502.5])


if __name__ == "__main__":
    unittest.main()
