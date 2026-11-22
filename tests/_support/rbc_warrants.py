"""Shared test helpers: RBC Direct warrant rows (a buy and an exercise's two legs) and their parse.

Shared from: test_fix_a2_warrants
"""
import contextlib
import io
import tempfile
from pathlib import Path


_RBC_H = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
          '"Price","Settlement Date","Account","Value","Currency",'
          '"Description"\n')


def _rbc(date, act, sym, symdesc, qty, value, desc, price=''):
    return (f'"{date} 00:00:00","{act}","{sym}","{symdesc}","{qty}",'
            f'"{price}","{date} 00:00:00","55500001","{value}","CAD",'  # pii-ok
            f'"{desc}"\n')


def _parse_rbc(rows):
    """An RBC Direct export of ROWS, parsed: (txs, stderr)."""
    from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "rbc.csv"
        p.write_text(_RBC_H + ''.join(rows))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = RbcBrokerage().parse_file(p)
    return txs, err.getvalue()


# A warrant bought, its exercise leg and the share leg it delivers.
_RBC_BUY = _rbc('2025-02-03', 'Buy', 'QZW.WT', 'QZ CORP WTS', '100',
                '-100.00', 'QZ CORP WTS BUY', '1.00')
_RBC_WLEG = _rbc('2025-05-05', 'Exercise', 'QZW.WT', 'QZ CORP WTS', '-100',
                 '0', 'EXERCISE QZ CORP WTS', '0')
_RBC_SLEG = _rbc('2025-05-05', 'Exercise', 'QZ', 'QZ CORP', '100',
                 '-500.00', 'EXERCISE QZ CORP WTS', '5.00')
