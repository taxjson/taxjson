"""The owner's rulings of 2026-11-19 on the generic importer (backlog
TJ-4559) and its futures fill at a stated price of 0 (TJ-4558).

(1) A trade at a stated price of 0.00 that carries cash is booked at its
    amount, as before, and said once per file: the count and the first
    row, with no quantity x price check.
(2) A share leg of an assignment with a blank price is kept: its amount
    is the cash paid, and the engines fold the premium into the shares.
(3) A futures row that no reading of its amount fits (neither a cost nor
    a credit, with its fee, matches the price's sign) is refused.
TJ-4558: a futures fill at a stated price of 0 has a notional of 0, so
its amount is its commission or rebate alone. The price-sign reading
booked a sale's charge as proceeds received."""
import contextlib
import io
import unittest

from _tmpfiles import isolate_layouts
from _support.generic_parse import _parse
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction

isolate_layouts()

NOFEE = """\
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
"ASGI" = "assign_buy"
[defaults]
currency = "CAD"
"""
FEE = NOFEE.replace('currency = "Currency"\n[actions]',
                    'fee = "Fee"\ncurrency = "Currency"\n[actions]') \
    + "[options]\nallow_large_fees = true\n"
H7 = "Date,Type,Ticker,Qty,Price,Amount,Currency\n"
H8 = "Date,Type,Ticker,Qty,Price,Amount,Fee,Currency\n"
FUT_BUY = "2025-06-02,BUY,F:ESM25,1,10.00,-500.00,USD\n"


def _money(txs):
    return [(t["quantity"], t["net_amount"], t["gross_amount"], t["fee"])
            for t in txs]


class TestStatedZeroPrice(unittest.TestCase):
    """Ruling (1): booked at the amount, said once per file."""

    def test_said_once_with_the_count_and_the_first_row(self):
        txs, err, _ = _parse(
            H7 + "2025-01-02,BUY,XEI,10,0.00,-500.00,CAD\n"
            "2025-02-03,SELL,XEI,-10,0.00,600.00,CAD\n", NOFEE)
        self.assertEqual([t["net_amount"] for t in txs], [500.0, 600.0])
        said = [ln for ln in err.splitlines() if "price of 0" in ln]
        self.assertEqual(len(said), 1, err)
        self.assertIn("generic_x.csv: 2 trade rows state a price of 0 with "
                      "cash (the first: line 2)", said[0])
        self.assertIn("with no quantity x price check", said[0])
        self.assertIn("blank the price cells", said[0])

    def test_not_said_for_a_blank_price_or_a_fee_alone(self):
        _, err, _ = _parse(
            H7 + "2025-01-02,BUY,XEI,10,,-500.00,CAD\n"
            "2025-02-03,SELL,XEI,-10,,600.00,CAD\n", NOFEE)
        self.assertNotIn("price of 0", err)
        # A worthless position closed at 0 whose only cash is its
        # commission (backlog TJ-0731) is no price-0 trade with cash.
        txs, err, _ = _parse(
            H8 + "2025-01-02,BUY,XEI,10,5.00,-50.00,0,CAD\n"
            "2025-02-03,SELL,XEI,-10,0.00,-1.00,1.00,CAD\n", FEE)
        self.assertNotIn("price of 0", err)
        self.assertEqual(txs[1]["net_amount"], -1.0)


class TestBlankPriceAssignmentLeg(unittest.TestCase):
    """Ruling (2): kept; the engines pair the legs."""

    ROWS = (H7 + "2025-04-10,SELL,XEJ250516P00010000,-1,0.50,50.00,CAD\n"
            "2025-05-16,ASGI,XEJ250516P00010000,1,0,0,CAD\n"
            "2025-05-16,ASGI,XEJ,100,,-1000.00,CAD\n")

    def test_booked_at_the_cash_paid(self):
        txs, _, _ = _parse(self.ROWS, NOFEE)
        self.assertEqual([(t["action"], t["symbol"], t["net_amount"])
                          for t in txs[1:]],
                         [("ASSIGN", "XEJ250516P00010000.TO", 0.0),
                          ("ASSIGN", "XEJ.TO", 1000.0)])

    def test_the_premium_folds_into_the_shares(self):
        txs, _, _ = _parse(self.ROWS, NOFEE)
        fields = TaxTransaction.__dataclass_fields__
        rows = [TaxTransaction(**{k: v for k, v in t.items() if k in fields})
                for t in txs]
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res = CanadaTaxRules().compute_gains(rows)
        inv = [i for i in res["inventory"] if i["symbol"] == "XEJ.TO"]
        self.assertAlmostEqual(inv[0]["total_cost"], 950.0, places=2)


class TestFuturesNoReadingFits(unittest.TestCase):
    """Ruling (3): refused, the row named."""

    def test_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(H8 + "2025-04-20,SELL,F:CLK25,-1,-1.00,2.00,5.00,USD\n",
                   FEE)
        msg = str(cm.exception)
        self.assertIn("generic_x.csv line 2: neither a cost nor a credit of "
                      "2.00 fits a sale at price -1.00 with a fee of 5.00",
                      msg)
        self.assertIn("Check the amount, the fee and the price's sign", msg)

    def test_a_reading_that_fits_is_booked(self):
        txs, _, _ = _parse(
            H8 + "2025-04-20,SELL,F:CLK25,-1,-1.00,6.00,5.00,USD\n", FEE)
        self.assertEqual(_money(txs), [(-1.0, -6.0, 1.0, 5.0)])


class TestFuturesFillAtPriceZero(unittest.TestCase):
    """TJ-4558: the amount is the commission or rebate alone."""

    def test_a_sales_charge_in_a_signed_file(self):
        txs, _, _ = _parse(H7 + FUT_BUY
                           + "2025-06-20,SELL,F:ESM25,-1,0,-2.50,USD\n", NOFEE)
        self.assertEqual(_money(txs[1:]), [(-1.0, -2.5, 0.0, 2.5)])

    def test_a_sales_rebate_in_a_signed_file(self):
        txs, _, _ = _parse(H7 + FUT_BUY
                           + "2025-06-20,SELL,F:ESM25,-1,0,2.50,USD\n", NOFEE)
        self.assertEqual(_money(txs[1:]), [(-1.0, 2.5, 0.0, -2.5)])

    def test_a_buys_charge(self):
        txs, _, _ = _parse(H7 + "2025-04-01,BUY,F:ESM25,1,0,-10.00,USD\n",
                           NOFEE)
        self.assertEqual(_money(txs), [(1.0, 10.0, 0.0, 10.0)])

    def test_the_fee_column_decides(self):
        for amount in ("-2.50", "2.50"):
            txs, _, _ = _parse(
                H8 + "2025-06-02,BUY,F:ESM25,1,10.00,-500.00,0,USD\n"
                f"2025-06-20,SELL,F:ESM25,-1,0,{amount},2.50,USD\n", FEE)
            self.assertEqual(_money(txs[1:]), [(-1.0, -2.5, 0.0, 2.5)])

    def test_an_option_on_a_future(self):
        opt = "F:ES250620C05000000"
        txs, _, _ = _parse(
            H7 + f"2025-06-02,BUY,{opt},1,10.00,-500.00,USD\n"
            f"2025-06-23,SELL,{opt},-1,0,-2.50,USD\n", NOFEE)
        self.assertEqual(_money(txs[1:]), [(-1.0, -2.5, 0.0, 2.5)])

    def test_refused_when_nothing_tells_paid_from_received(self):
        for rows in (H7 + "2025-06-02,BUY,F:ESM25,1,10.00,500.00,USD\n"
                     "2025-06-20,SELL,F:ESM25,-1,0,2.50,USD\n",
                     H7 + "2025-04-01,BUY,F:ESM25,1,0,10.00,USD\n"):
            with self.assertRaises(ValueError) as cm:
                _parse(rows, NOFEE)
            self.assertIn("at a stated price of 0", str(cm.exception))
            self.assertIn("carry no sign", str(cm.exception))

    def test_refused_when_the_amount_is_not_the_fee(self):
        with self.assertRaises(ValueError) as cm:
            _parse(H8 + "2025-06-02,BUY,F:ESM25,1,10.00,-500.00,0,USD\n"
                   "2025-06-20,SELL,F:ESM25,-1,0,-4.00,2.50,USD\n", FEE)
        self.assertIn("generic_x.csv line 3: a futures sell at a stated "
                      "price of 0 has an amount of 4.00 and a fee of 2.50",
                      str(cm.exception))


if __name__ == "__main__":
    unittest.main()
