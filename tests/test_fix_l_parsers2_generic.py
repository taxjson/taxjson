"""Low-round parsers2 fixes: the generic column-mapped importer.

S057-05 (an explicit 0 amount is not "no amount"), S057-00 (the fee
share is printed with the limit), R1-307 (the derived net with fees and
options), S056-24 (the [defaults] branch of the action/symbol checks).
Synthetic data only.
"""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

_TOML = """\
[columns]
date = "Date"
action = "Type"
symbol = "Ticker"
quantity = "Qty"
price = "Price"
{amount}{fee}
[actions]
"BUY" = "buy"
"SELL" = "sell"

[defaults]
currency = "CAD"
"""


def _parse(csv_text, toml_text):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_x.csv"
        c.write_text(csv_text)
        c.with_name(c.name + ".toml").write_text(toml_text)
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = GenericBrokerage().parse_file(c)
        return txs, buf.getvalue()


def _toml(amount=True, fee=True):
    return _TOML.format(amount='amount = "Amount"\n' if amount else '',
                        fee='fee = "Fee"\n' if fee else '')


class TestExplicitZeroAmount(unittest.TestCase):
    """S057-05."""

    def test_zero_amount_on_a_priced_buy_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse("Date,Type,Ticker,Qty,Price,Amount,Fee\n"
                   "2025-01-02,BUY,XYZ,10,50.00,0.00,0\n", _toml())
        self.assertIn("amount cell is 0", str(cm.exception))

    def test_zero_amount_on_a_priced_sale_refused(self):
        with self.assertRaises(ValueError):
            _parse("Date,Type,Ticker,Qty,Price,Amount,Fee\n"
                   "2025-01-02,BUY,XYZ,10,50.00,-500.00,0\n"
                   "2025-01-05,SELL,XYZ,-10,50.00,0,0\n", _toml())

    def test_blank_amount_still_derives(self):
        txs, _ = _parse("Date,Type,Ticker,Qty,Price,Amount,Fee\n"
                        "2025-01-02,BUY,XYZ,10,50.00,,0\n", _toml())
        self.assertEqual(txs[0]["net_amount"], 500.0)

    def test_commission_that_ate_the_gross_nets_zero(self):
        # A penny option close whose commission equals its gross.
        txs, _ = _parse(
            "Date,Type,Ticker,Qty,Price,Amount,Fee\n"
            "2025-01-02,BUY,AAPL250321C00200000,1,1.50,-151.00,1\n"
            "2025-01-05,SELL,AAPL250321C00200000,-1,0.01,0,1\n",
            _toml().replace("[options]", "") +
            "[options]\nallow_large_fees = true\n")
        self.assertEqual(txs[1]["net_amount"], 0.0)


class TestFeeShareMessage(unittest.TestCase):
    """S057-00: 5.25 on 100.00 read '5% of the gross'."""

    def test_share_has_two_decimals_and_names_the_limit(self):
        with self.assertRaises(ValueError) as cm:
            _parse("Date,Type,Ticker,Qty,Price,Amount,Fee\n"
                   "2025-01-02,BUY,XYZ,10,10.00,-105.25,5.25\n", _toml())
        msg = str(cm.exception)
        self.assertIn("5.25% of the gross 100.00", msg)
        self.assertIn("limit 5%", msg)


class TestDerivedNet(unittest.TestCase):
    """R1-307: no amount column — net = qty x price x mult +/- fee."""

    def test_derived_with_fee_and_option_multiplier(self):
        txs, _ = _parse(
            "Date,Type,Ticker,Qty,Price,Fee\n"
            "2025-01-02,BUY,ABC,10,20.00,5\n"
            "2025-01-03,SELL,ABC,-10,25.00,5\n"
            "2025-01-02,BUY,AAPL250321C00200000,1,1.50,1\n"
            "2025-01-03,SELL,AAPL250321C00200000,-1,2.00,1\n",
            _toml(amount=False))
        self.assertEqual([t["net_amount"] for t in txs],
                         [205.0, 245.0, 151.0, 199.0])

    def test_derived_sale_whose_fee_exceeds_gross_is_negative(self):
        txs, _ = _parse(
            "Date,Type,Ticker,Qty,Price,Fee\n"
            "2025-01-02,BUY,AAPL250321C00200000,1,0.05,1\n"
            "2025-01-03,SELL,AAPL250321C00200000,-1,0.01,1.50\n",
            _toml(amount=False) + "[options]\nallow_large_fees = true\n")
        self.assertAlmostEqual(txs[1]["net_amount"], -0.50)


class TestDefaultsBranch(unittest.TestCase):
    """S056-24: '[columns] X or [defaults] X' — both directions."""

    _CSV = "Date,Ticker,Qty,Price\n2025-01-02,XYZ,10,10.00\n"

    def test_defaults_action_accepted(self):
        toml = ('[columns]\ndate = "Date"\nsymbol = "Ticker"\n'
                'quantity = "Qty"\nprice = "Price"\n'
                '[actions]\n"BUY" = "buy"\n'
                '[defaults]\ncurrency = "CAD"\naction = "BUY"\n')
        txs, _ = _parse(self._CSV, toml)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["quantity"], 10.0)

    def test_no_action_column_and_no_default_refused(self):
        toml = ('[columns]\ndate = "Date"\nsymbol = "Ticker"\n'
                'quantity = "Qty"\nprice = "Price"\n'
                '[actions]\n"BUY" = "buy"\n[defaults]\ncurrency = "CAD"\n')
        with self.assertRaises(ValueError):
            _parse(self._CSV, toml)

    def test_defaults_symbol_accepted(self):
        toml = ('[columns]\ndate = "Date"\naction = "Type"\n'
                'quantity = "Qty"\nprice = "Price"\n'
                '[actions]\n"BUY" = "buy"\n'
                '[defaults]\ncurrency = "CAD"\nsymbol = "XYZ"\n')
        txs, _ = _parse("Date,Type,Qty,Price\n2025-01-02,BUY,10,10.00\n",
                        toml)
        self.assertEqual(txs[0]["symbol"], "XYZ.TO")

    def test_no_symbol_column_and_no_default_refused(self):
        toml = ('[columns]\ndate = "Date"\naction = "Type"\n'
                'quantity = "Qty"\nprice = "Price"\n'
                '[actions]\n"BUY" = "buy"\n[defaults]\ncurrency = "CAD"\n')
        with self.assertRaises(ValueError):
            _parse("Date,Type,Qty,Price\n2025-01-02,BUY,10,10.00\n", toml)


if __name__ == "__main__":
    unittest.main()
