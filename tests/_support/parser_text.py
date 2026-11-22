"""Shared test helpers: a parser over in-memory text.

Shared from: test_parser_coverage_audit
"""
import contextlib
import io
import tempfile
from pathlib import Path


def _parse(parser_cls, text, prefix="x_"):
    """(parser, transactions, stderr) for an in-memory CSV, alone in a
    directory of its own: the Kraken parser reads a sibling ledger
    export, and another test's (or another gate's) kr_ledgers_*.csv in
    the shared temp directory was read with it."""
    parser = parser_cls()
    err = io.StringIO()
    with tempfile.TemporaryDirectory() as td:
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         prefix=prefix, dir=td) as f:
            f.write(text)
            name = f.name
        with contextlib.redirect_stderr(err):
            txs = parser.parse_file(Path(name))
    return parser, txs, err.getvalue()


KR_LEDGER_H = ('"txid","refid","time","type","subtype","aclass","asset",'
               '"wallet","amount","fee","balance"\n')
