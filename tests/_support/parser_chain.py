"""Shared test helpers: the parse chain and what it gives.

Shared from: test_fix_1029_r7e_parsers
"""
import contextlib
import io
import tempfile
from pathlib import Path

from _support.qt_disposals import QT_BUY
from _support.parser_qt_rbc_rows import QH

from taxjson.lib import corp_actions as CA


def _chain(*rows):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "qt.csv"
        f.write_text(QH + QT_BUY + "".join(rows), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            evs = CA.parse_questrade_corporate_actions(f, "margin")
    return evs, " ".join(err.getvalue().split())


def _got(evs):
    return sorted((e.target_symbol, e.qty_received) for e in evs)
