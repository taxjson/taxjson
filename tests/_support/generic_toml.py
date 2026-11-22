"""Shared test helpers: a generic CSV mapping's generic.toml.

Shared from: test_generic_importer
"""


_TOML = """\
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
"DIV" = "dividend"
"NRT" = "tax"
"JNL" = "skip"
"""
