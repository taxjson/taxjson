"""Shared test helpers: a Questrade margin export of a currency journal, its config with transfers in the books or aside, rates and the run.

Shared from: test_fix_journal_books
"""
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import _paths


REPO = _paths.REPO


def _rates(path: Path, pair='USD CAD', rate='1.3500'):
    d, lines = date(2025, 1, 1), []
    while d <= date.today():
        lines.append(f"{d.isoformat()} 12:00:00 {pair} {rate} boc")
        d += timedelta(days=1)
    path.write_text("\n".join(lines) + "\n")


def _run(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
             PYTHONPATH=str(REPO / "src"))
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=600)


def _config(country='canada', transfers=None, year=2025, source='USD'):
    base = 'CAD' if country == 'canada' else 'USD'
    return (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{source}"]\n'
            + (f'option_grant_timing_since = {year}\n'
               if country == 'canada' else '')
            + '\n[accounts.margin]\ntype = "taxable"\n'
            + ('' if transfers is None
               else f'transfers = {"true" if transfers else "false"}\n'))


QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
QNAME = 'QZ US DLR CURRENCY ETF UNIT CL A'


def _q(td, action, sym, desc, qty, price='0.0', gross='0.0', net='0.0',
       cur='CAD', act='Other', sd=None):
    return (f"{td} 12:00:00 AM,{sd or td} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price},{gross},0.0,{net},{cur},55500001,{act},"  # pii-ok
            f"Individual margin\n")


QT_CSV = QH + (
    _q('2025-09-10', 'Buy', 'QZD.TO', f'{QNAME} WE ACTED AS AGENT', '300.0',
       price='14.20', gross='-4260.00', net='-4260.00', act='Trades',
       sd='2025-09-11')
    + _q('2025-09-25', 'BRW', 'QZD', f'{QNAME} JOURNAL POSITION FROM CAD '
         'BOOK VALUE: $3016.67 CNV@ 1.4138', '300.0', cur='USD')
    + _q('2025-09-25', 'BRW', 'QZD', f'{QNAME} JOURNAL POSITION TO USD',
         '-300.0')
    + _q('2025-09-30', 'Sell', 'QZD.U.TO', f'{QNAME} WE ACTED AS AGENT',
         '-300.0', price='10.00', gross='3000.00', net='3000.00', cur='USD',
         act='Trades', sd='2025-10-01'))


def _qt_project(td, transfers, country='canada'):
    root = Path(td) / f"{country}-{transfers}"
    (root / 'inputs' / 'margin').mkdir(parents=True)
    (root / 'taxjson.toml').write_text(
        _config(country, transfers,
                source='USD' if country == 'canada' else 'CAD'))
    (root / 'inputs' / 'margin' / 'questrade.csv').write_text(QT_CSV)
    (root / 'work').mkdir()
    _rates(root / 'work' / 'to_base.csv',
           *(() if country == 'canada' else ('CAD USD', '0.7400')))
    return root
