"""Shared test helpers: in-kind moves: IB and Questrade rows, projects and runs.

Shared from: test_feat_in_kind
"""
import json
from pathlib import Path

from tax_rules.dual import cli, settings_for


def _ib(trades=(), xfers=(), *, cur="CAD", exch="TSE", sym="QZK",
        acct="U5550001"):                                       # pii-ok
    """A synthetic IB activity statement: `trades` (date, qty, price),
    `xfers` (date, qty, market value)."""
    head = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Statement,Data,Title,Activity Statement\n'
            'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
            'Financial Instrument Information,Header,Asset Category,'
            'Symbol,Description,Conid,Security ID,Underlying,Listing Exch,'
            'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n'
            f'Financial Instrument Information,Data,Stocks,{sym},'
            f'"{sym} SAMPLE CORP",999000101,,,{exch},1,,,COMMON,,\n')
    out = head
    if trades:
        out += ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
                'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
                'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
        for d, q, p in trades:
            out += (f'Trades,Data,Order,Stocks,{cur},{acct},{sym},'
                    f'"{d}, 10:00:00",{q},{p},0,{-q * p},0,0,0,0,'
                    f'{"O" if q > 0 else "C"}\n')
    if xfers:
        out += ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
                'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
                'Market Value,Realized P/L,Cash Amount,Code\n')
        for d, q, mv in xfers:
            out += (f'Transfers,Data,Stocks,{cur},{sym},{d},ACATS,'
                    f'{"Out" if q < 0 else "In"},Other Broker,5550009,'  # pii-ok
                    f'{q},0,{mv if q > 0 else -mv},0,0,\n')
    return out


_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


def _qt(date, action, qty, price=0.0, *, cur="CAD", acct="55500002",
        act="Trades", sym="QZK", desc="QZK SAMPLE CORP"):    # pii-ok
    net = -qty * price
    return (f"{date} 09:30:00 AM,{date} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price:.2f},{abs(qty) * price:.2f},0.00,{net:.2f},"
            f"{cur},{acct},{act},Individual\n")


def _xfer_qt(date, qty, **kw):
    return _qt(date, "TF6", qty, act="Transfers",
               desc="QZK SAMPLE CORP TRANSFER", **kw)


def _project(td, files, *, accounts=None, country="canada", name="p"):
    root = Path(td) / name
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2025, source_currencies=[])
        + (accounts or ('[accounts.margin]\ntype = "taxable"\n'
                        '[accounts.rrsp]\ntype = "sheltered"\n'
                        'transfers = true\n')))
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _run(test, root, *args, ok=True):
    r = cli(root, "run", "--no-input", *args)
    if ok:
        test.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
    return r


def _flat(r):
    return " ".join((r.stdout + r.stderr).split())


def _filing(root):
    return json.loads(cli(root, "sum", "--json").stdout)["filing"]["totals"]
