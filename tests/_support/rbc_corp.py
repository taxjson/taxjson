"""Shared test helpers: RBC Direct corporate-action rows and their parse.

Shared from: test_fix_1029_r7_corprbc
"""
import contextlib
import csv
import io
import tempfile
from pathlib import Path

from taxjson.lib import corp_actions as CA
from taxjson.lib import rbc_corp_actions as RCA


RBC_HEAD = ['Date', 'Activity', 'Symbol', 'Symbol Description', 'Quantity',
            'Price', 'Settlement Date', 'Account', 'Value', 'Currency',
            'Description']


def rbc_row(day, act, sym, name, q, value, desc, price='', cur='CAD',
            settle=None):
    return [day, act, sym, name, q, price, settle or day, '99900001', value,
            cur, desc]


def write_rbc(folder: Path, name: str, rows) -> Path:
    s = io.StringIO()
    csv.writer(s).writerows([RBC_HEAD] + rows)
    p = folder / name
    p.write_text(s.getvalue())
    return p


def parse_all(files, quiet=True):
    """Every file parsed with the others as context, the copies combined
    (as taxjson_corp_actions does)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
        evs = CA.combine_broker_copies(
            [e for f in files
             for e in RCA.parse_rbc_corporate_actions(f, 'margin', files)])
    return evs, err.getvalue()


def parse_one(rows):
    with tempfile.TemporaryDirectory() as td:
        f = write_rbc(Path(td), 'rbc.csv', rows)
        return parse_all([f])
