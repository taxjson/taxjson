"""Shared test helpers: a Questrade spin-off over two accounts in the US engine.

Shared from: test_fix_1029_r7f_useng
"""
import json
import re
import tempfile
from pathlib import Path

from _qa_project import tj


_HEAD = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
         'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
         'Activity Type,Account #,Account Type\n')
_PARENT = 'ZZP PARENT CORP'
_ONE, _TWO = '99900001', '99900002'


def _trade(date, action, sym, desc, qty, net, acct=_ONE):
    return (f'{date},{date},{action},{sym},{desc},{qty},'
            f'{abs(net / qty):.4f},{net},0,{net},USD,Trades,{acct},Margin\n')


def _dis(on, qty, acct=_ONE):
    return (f'2025-06-16,2025-06-16,DIS,ZZS,ZZS SPINCO INC SPINOFF FROM SEC# '
            f'X123456 {_PARENT} REC 06/02/25 PAY 06/16/25 ON {on} SHS,{qty},'
            f'0,0,0,0,USD,Other,{acct},Margin\n')


def _buy(date, qty, net, acct=_ONE):
    return _trade(date, 'Buy', 'ZZP', _PARENT, qty, -net, acct)


def _sell_spun(qty, net, acct=_ONE):
    return _trade('2025-07-03', 'Sell', 'ZZS', 'ZZS SPINCO', -qty, net, acct)


def _run_355(files, alloc=40):
    """`tjs run`, the election, `tjs run`: (gains doc, output, event ids)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'p'
        for name, rows in files.items():
            p = root / 'inputs' / 'margin' / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_HEAD + rows)
        (root / 'taxjson.toml').write_text(
            '[settings]\nyear = 2025\ncountry = "usa"\n'
            'base_currency = "USD"\nsource_currencies = ["USD"]\n'
            'combined_broker_accounts = true\n'
            '[accounts.margin]\ntype = "taxable"\n')
        first = tj(root, 'run', '--no-input', check=False)
        ids = sorted(set(re.findall(r'\b(\d{8}-zzp-zzs-[0-9a-f]{4})\b',
                                    first.stdout + first.stderr)))
        for i in ids:
            tj(root, 'elections', 'margin', '--set', f'{i}=tax_free_355', '--hint',
               f'allocated_acb={alloc}')
        again = tj(root, 'run', '--no-input', '--details', check=False)
        doc = json.loads((root / 'work/margin_gains.json').read_text())
    return doc, again.stdout + again.stderr, ids


def _blocks(doc, sym='ZZS.US'):
    return sorted((t['term'], round(t['qty'], 4), round(t['cost'], 4))
                  for t in doc['transactions'] if t['symbol'] == sym)


def _held(doc, sym='ZZP.US'):
    return next(round(i['total_cost'], 4) for i in doc['inventory']
                if i['symbol'] == sym)
