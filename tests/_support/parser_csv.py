"""Shared test helpers: a parser over an in-memory CSV.

Shared from: test_parser_activity_coverage
"""
import os
from _tmpfiles import private_tmpfile
from pathlib import Path


def _parse_csv(parser_cls, content):
    """Run a parser against an in-memory CSV string."""
    f = private_tmpfile(mode='w', suffix='.csv', delete=False)
    f.write(content)
    f.close()
    try:
        return parser_cls().parse_file(Path(f.name))
    finally:
        os.remove(f.name)


QUESTRADE_HEADER = (
    'Transaction Date,Settlement Date,Action,Symbol,Description,'
    'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
    'Account #,Activity Type,Account Type\n'
)
