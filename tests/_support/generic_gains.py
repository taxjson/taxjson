"""Shared test helpers: the generic importer's mapping, rows and gains.

Shared from: test_fix_1029_r7b_generic
"""
import contextlib
import copy
import dataclasses
import io
import tempfile
from pathlib import Path

from taxjson.lib import markets
from taxjson.lib.brokerages.generic import GenericBrokerage


_MAP = """\
[columns]
date = "Date"
action = "Action"
symbol = "Symbol"
quantity = "Qty"
price = "Price"
currency = "Currency"
{extra}
[formats]
{formats}
[actions]
B = "buy"
S = "sell"
{options}
"""


def _mapping(extra="", formats="", options=""):
    return _MAP.format(extra=extra, formats=formats, options=options)


def _parse(csv_text, mapping_text, ticker_map=None):
    """(rows, stderr) of the generic importer over one file."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "generic.csv"
        p.write_text(csv_text)
        p.with_suffix(".csv.toml").write_text(mapping_text)
        tm = None
        if ticker_map is not None:
            tm = Path(td) / "ticker.map"
            tm.write_text(ticker_map)
            markets.use_ticker_map(tm)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rows = GenericBrokerage().parse_file(p)
        finally:
            if tm is not None:
                markets.use_ticker_map(None)
        return rows, err.getvalue()


def _gains(rows, year, ticker_map=None):
    from taxjson.model.transaction import TaxTransaction as T
    from taxjson.lib.pipeline import GainsRequest, run_gains
    fields = {f.name for f in dataclasses.fields(T)}
    txs = [T(**{k: v for k, v in r.items() if k in fields}) for r in rows]
    with tempfile.TemporaryDirectory() as td:
        if ticker_map is not None:
            tm = Path(td) / "ticker.map"
            tm.write_text(ticker_map)
            markets.use_ticker_map(tm)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                return run_gains(copy.deepcopy(txs), req=GainsRequest(
                    country="canada", year=year, taxable=True,
                    phantom_hint=False))
        finally:
            if ticker_map is not None:
                markets.use_ticker_map(None)
