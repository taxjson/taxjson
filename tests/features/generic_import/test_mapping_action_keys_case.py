"""Backlog fixes 2026-10-29, group `parsers`: the broker parsers.

TJ-0376 (one account's Webull downloads pasted into one file lost each
download's row order), TJ-0237 (two accounts' Webull downloads pasted
into one file were booked under the first account), TJ-0378 (a Kraken
fiat reward with its fee in another currency booked the USD net as its
own currency), TJ-0379 (a US Kraken stablecoin reward's separately paid
fee came off the coins' cost), TJ-0383 (a negative Coinbase reward was
booked as positive income), TJ-0391 (Coinbase Send/Receive rows lost
their IDs, and two exports' distinct sends folded into one), TJ-0382
(generic [actions] keys alike but for case silently replaced one
another).
Synthetic data only; account ids are fake (pii-ok).

Formerly: test_fix_1029_parsers
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.generic import GenericBrokerage


def setUpModule():
    # Crypto UTC stamps need a named zone outside a project.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_GEN_TOML = """\
[columns]
date = "Date"
action = "Transaction type"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Amount"
currency = "Currency"

[formats]
date = "%m/%d/%Y"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"""
_GEN_CSV = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
            "01/15/2025,BUY,ZZG,10,10.00,100.00,CAD\n")


def _generic(csv_text, toml_text):
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_test.csv"
        c.write_text(csv_text)
        c.with_name(c.name + ".toml").write_text(toml_text)
        with contextlib.redirect_stderr(io.StringIO()):
            return GenericBrokerage().parse_file(c)


class TestGenericActionKeysIgnoreCase(unittest.TestCase):
    """TJ-0382: the importer matches a row's action ignoring case, so
    [actions] "BUY" = "buy" and "Buy" = "sell" are one label with two
    meanings: the last one silently won and a purchase of 10 was booked
    as a sale. Such a mapping is refused; keys alike but for case or
    spaces that agree still read.

    Formerly: test_fix_1029_parsers.TestGenericActionKeysIgnoreCase"""

    def test_contradictory_keys_are_refused(self):
        with self.assertRaises(ValueError) as cm:
            _generic(_GEN_CSV, _GEN_TOML + '"Buy" = "sell"\n')
        msg = str(cm.exception)
        self.assertIn("generic_test.csv.toml", msg)
        self.assertIn("'BUY'", msg)
        self.assertIn("'Buy'", msg)
        self.assertIn("ignoring case", msg)

    def test_keys_with_spaces_are_one_label(self):
        with self.assertRaises(ValueError):
            _generic(_GEN_CSV, _GEN_TOML + '" buy " = "sell"\n')

    def test_agreeing_duplicates_and_padded_keys_read(self):
        toml_text = _GEN_TOML.replace('"BUY" = "buy"',
                                      '" Buy " = "buy"\n"BUY" = "buy"')
        rows = _generic(_GEN_CSV, toml_text)
        self.assertEqual([(r["action"], r["quantity"]) for r in rows],
                         [("BUYSELL", 10.0)])


if __name__ == "__main__":
    unittest.main()
