"""Shared test helpers: Questrade currency-journal rows (a CAD buy, the journal to USD, a sale under the internal code), rates and an RRSP and margin project.

Shared from: test_fix_qtjournal
"""
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import _paths


REPO = _paths.REPO
QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
NAME = 'QZ US DLR CURRENCY ETF UNIT CL A'


def q(td, action, sym, desc, qty, price='0.0', gross='0.0', comm='0.0',
      net='0.0', cur='CAD', act='Other', sd=None, acct='55500001',
      atype='Individual RRSP'):
    sd = sd or td
    return (f"{td} 12:00:00 AM,{sd} 12:00:00 AM,{action},{sym},{desc},{qty},"
            f"{price},{gross},{comm},{net},{cur},{acct},{act},{atype}\n")


def buy_cad(acct='55500001', atype='Individual RRSP', qty='300.0',
            gross='-4260.00', net='-4264.95'):
    return q('2026-09-10', 'Buy', 'QZD.TO', f'{NAME} WE ACTED AS AGENT', qty,
             price='14.20', gross=gross, comm='-4.95', net=net, act='Trades',
             sd='2026-09-11', acct=acct, atype=atype)


def journal_to_usd(qty=300, bv='3016.67', rate='1.4138', **kw):
    """The owner's two row shapes (in-leg first, as exported)."""
    return (q('2026-09-25', 'BRW', 'QZD', f'{NAME} JOURNAL POSITION FROM CAD '
              f'BOOK VALUE: ${bv} CNV@ {rate}', f'{qty:.1f}', cur='USD', **kw)
            + q('2026-09-25', 'BRW', 'QZD',
                f'{NAME} JOURNAL POSITION TO USD', f'{-qty:.1f}', cur='CAD',
                **kw))


def sell_code(qty=300, code='G000901', price='10.085', **kw):
    g = f'{qty * float(price):.2f}'
    return q('2026-09-30', 'Sell', code, f'{NAME} WE ACTED AS AGENT',
             f'{-qty:.1f}', price=price, gross=g, net=g, cur='USD',
             act='Trades', sd='2026-10-01', **kw)


def _rates_file(path: Path, pair='USD CAD', rate='1.3500'):
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


def _project(td, country='canada', tmap=''):
    root = Path(td) / country
    base_cur, src = (('CAD', 'USD') if country == 'canada'
                     else ('USD', 'CAD'))
    (root / 'inputs' / 'rrsp').mkdir(parents=True)
    (root / 'inputs' / 'marg').mkdir(parents=True)
    (root / 'taxjson.toml').write_text(
        f'[settings]\nyear = 2026\ncountry = "{country}"\n'
        f'base_currency = "{base_cur}"\nsource_currencies = ["{src}"]\n'
        + ('option_grant_timing_since = 2026\n' if country == 'canada'
           else '') + '\n'
        f'[accounts.rrsp]\ntype = "sheltered"\n\n'
        f'[accounts.marg]\ntype = "taxable"\n')
    (root / 'inputs' / 'rrsp' / '55500001.csv').write_text(  # pii-ok
        QH + buy_cad() + journal_to_usd() + sell_code())
    kw = dict(acct='55500002', atype='Individual margin')  # pii-ok
    (root / 'inputs' / 'marg' / '55500002.csv').write_text(  # pii-ok
        QH + buy_cad(**kw) + journal_to_usd(**kw) + sell_code(**kw))
    if tmap:
        (root / 'ticker.map').write_text(tmap)
    (root / 'work').mkdir()
    _rates_file(root / 'work' / 'to_base.csv',
                *(() if country == 'canada' else ('CAD USD', '0.7400')))
    return root
