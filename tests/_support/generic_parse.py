"""Shared test helpers: the generic importer's mapping file and parse.

Shared from: test_fix_a2_generic
"""
import io
import tempfile
from contextlib import redirect_stderr
from pathlib import Path


_BASE_COLS = """\
[columns]
date = "Date"
action = "Type"
symbol = "Ticker"
quantity = "Qty"
price = "Price"
"""

_TOML = _BASE_COLS + """\
amount = "Amount"
fee = "Fee"
currency = "Currency"
{extra_cols}
[actions]
"BUY" = "buy"
"SELL" = "sell"
{extra_actions}
{extra}
[defaults]
currency = "CAD"
"""


def _toml(extra_cols="", extra_actions="", extra=""):
    return _TOML.format(extra_cols=extra_cols, extra_actions=extra_actions,
                        extra=extra)


def _parse(csv_text, toml_text, *, raw=None, futures_settle=None,
           name="generic_x.csv"):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / name
        if raw is not None:
            c.write_bytes(raw)
        else:
            c.write_text(csv_text, encoding="utf-8")
        c.with_name(c.name + ".toml").write_text(toml_text)
        g = GenericBrokerage()
        if futures_settle is not None:
            g.futures_settle = futures_settle
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = g.parse_file(c)
        return txs, buf.getvalue(), g


_HDR = "Date,Type,Ticker,Qty,Price,Amount,Fee,Currency\n"
