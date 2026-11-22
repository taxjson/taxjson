"""Shared test helpers: IB statement sections, a buy and a corporate-action row, a Canadian project of IB files, its pending elections and gains, a spin-off project and a merger's description.

Shared from: test_fix_corp
"""
import json
from pathlib import Path


_CAD_CONFIG = """\
[settings]
year = 2026
country = "canada"
province = "ON"
base_currency = "CAD"
source_currencies = []
tax_date = "settle"

[accounts.margin]
type = "taxable"
"""
_IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Account Information,Header,Field Name,Field Value\n'
            'Account Information,Data,Account,{acct}\n'
            'Account Information,Data,Base Currency,CAD\n')
_IB_TRADES = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
              'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,'
              'Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
_IB_CA = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
          'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
          'Code\n')


def _ib_buy(sym, qty, price, when='2026-01-05, 10:00:00', cur='CAD'):
    cost = qty * price
    return (f'Trades,Data,Order,Stocks,{cur},{sym},"{when}",{qty},{price},'
            f'{price},{-cost:.2f},0,{cost:.2f},0,0,O\n')


def _ib_ca(desc, qty, value, proceeds=0, when='2026-04-01, 20:25:00',
           cur='CAD', code=''):
    return (f'Corporate Actions,Data,Stocks,{cur},{when[:10]},"{when}",'
            f'"{desc}",{qty},{proceeds},{value},0,{code}\n')


def _project(tmp, files, acct_type='taxable'):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        _CAD_CONFIG.replace('type = "taxable"', f'type = "{acct_type}"'))
    d = root / "inputs" / "margin"
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_text(body)
    return root


def _pending(root):
    doc = json.loads((root / "work" / "pending_elections.json").read_text())
    return doc["accounts"]["margin"]["pending"]


def _gains(root):
    g = json.loads((root / "work" / "margin_gains_wash.json").read_text())
    inv = {i["symbol"]: (round(i["qty"], 6), round(i["total_cost"], 2))
           for i in g.get("inventory", []) if abs(i["qty"]) > 1e-9}
    return float(g["summary"]["total_gain"]), inv


_SPIN = 'PARNT(CA0000000777) Spinoff  1 for 5 (SPNCO, SPINCO CORP, CA0000000778)'


def _ib_spinoff_project(tmp, acct_type='taxable'):
    body = (_IB_HEAD.format(acct='U5550001') + _IB_TRADES  # pii-ok
            + _ib_buy('PARNT', 100, 60) + _IB_CA
            + _ib_ca(_SPIN, 20, 900))
    return _project(tmp, {'ib_2026.csv': body}, acct_type)


# IB's description of the merger: ABC into DEF, one DEF for two ABC.
_ABC_MERGER = 'ABC(CA0000000001) Merged(Acquisition) WITH CA0000000002 1 for 2'
