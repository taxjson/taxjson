"""Shared test helpers: two IB account ids, a statement over a period, its books after taxjson-brokerage and the prepared project.

Shared from: test_fix_a2_ib
"""
import contextlib
import io
import tempfile
from pathlib import Path

from taxjson.bin.taxjson_sort import plan_dedup
from taxjson.lib.brokerages.ib_extractor import IbBrokerage

from _support.ib_statement_rows import HEAD, _brokerage_cli, _period


A1 = 'U5550001'   # pii-ok
A2 = 'U5550002'   # pii-ok


def _acct(*ids):
    return ('Account Information,Header,Field Name,Field Value\n'
            + ''.join(f'Account Information,Data,Account,{a}\n'
                      for a in ids))


def _stmt(start, end, *sections, accts=(A1,)):
    return HEAD + _period(start, end) + _acct(*accts) + ''.join(sections)


def _booked(files, *extra):
    """taxjson-brokerage over {name: text}, then the books' cross-file
    dedup (taxjson-sort): the rows the books keep, and stderr."""
    rc, out, err, side = _brokerage_cli(files, *extra)
    assert rc == 0, err
    rows = out['transactions']
    plan = plan_dedup(rows, (out.get('metadata') or {}).get(
        'source_accounts'))
    return [rows[i] for i in plan.keep], err


def _prepare(files, tax_year=None):
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for name, text in files.items():
            p = Path(td) / name
            p.write_text(text, encoding='utf-8')
            paths.append(p)
        with contextlib.redirect_stderr(err):
            IbBrokerage.prepare_files(paths, tax_year=tax_year)
    return err.getvalue()
