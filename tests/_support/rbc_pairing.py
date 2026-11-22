"""Shared test helpers: RBC Direct rows and their pairing.

Shared from: test_fix_m_corp
"""
from pathlib import Path


_RBC_HEADER = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
               '"Price","Settlement Date","Account","Value","Currency",'
               '"Description"\n')


def _rbc_row(date, activity, sym, symdesc, qty, value, cur, desc,
             price='', acct='55500001'):
    return (f'"{date} 00:00:00","{activity}","{sym}","{symdesc}","{qty}",'
            f'"{price}","{date} 00:00:00","{acct}","{value}","{cur}",'
            f'"{desc}"\n')


def _rbc_file(tmp, name, *rows):
    p = Path(tmp) / name
    p.write_text(_RBC_HEADER + ''.join(rows))
    return p


def _rbc_pairing(tmp, *rows):
    from taxjson.lib.brokerages.rbc_direct import read_rbc_rows
    from taxjson.lib.corp_actions import pair_rbc_reorganizations
    p = _rbc_file(tmp, "rbc.csv", *rows)
    return pair_rbc_reorganizations(read_rbc_rows(p).rows)
