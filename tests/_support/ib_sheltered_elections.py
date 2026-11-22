"""Shared test helpers: an IB spin-off and merger in a taxable and a sheltered account: the statement, the project, its corporate-action rows and event ids.

Shared from: test_feat_sheltered_elections
"""
import json
from pathlib import Path

from tax_rules.dual import settings_for

from taxjson.lib.country import CANADA, HOME_CURRENCY


_SPIN = ('PARNT(US0000000777) Spinoff  1 for 4 '
         '(SPNCO, SPINCO CORP, US0000000778)')
_MRG = ('ABC(CA0000000001) Merged(Acquisition) WITH CA0000000002 1 for 2 '
        '({t}, {n}, {i})')


def _ib(country, acct, *, trades=(), corp=(), xfers=()):
    """A synthetic IB activity statement in the country's currency:
    `trades` (symbol, date, qty, price), `corp` (description, date, qty,
    value) and `xfers` (symbol, date, qty, market value)."""
    cur = HOME_CURRENCY[country]
    exch = "TSE" if country == CANADA else "NYSE"
    out = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,BrokerName,Interactive Brokers\n'
           'Statement,Data,Title,Activity Statement\n'
           'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
           'Account Information,Header,Field Name,Field Value\n'
           f'Account Information,Data,Account,{acct}\n'
           f'Account Information,Data,Base Currency,{cur}\n'
           'Financial Instrument Information,Header,Asset Category,'
           'Symbol,Description,Conid,Security ID,Underlying,Listing Exch,'
           'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n')
    for i, sym in enumerate(("PARNT", "SPNCO", "ABC", "XYZ")):
        out += (f'Financial Instrument Information,Data,Stocks,{sym},'
                f'"{sym} SAMPLE CORP",99900010{i},,,{exch},1,,,COMMON,,\n')
    if trades:
        out += ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
                'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
                'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
        for sym, d, q, p in trades:
            out += (f'Trades,Data,Order,Stocks,{cur},{acct},{sym},'
                    f'"{d}, 10:00:00",{q},{p},0,{-q * p},0,0,0,0,'
                    f'{"O" if q > 0 else "C"}\n')
    if corp:
        out += ('Corporate Actions,Header,Asset Category,Currency,Report '
                'Date,Date/Time,Description,Quantity,Proceeds,Value,'
                'Realized P/L,Code\n')
        for desc, d, q, v in corp:
            out += (f'Corporate Actions,Data,Stocks,{cur},{d},'
                    f'"{d}, 20:25:00","{desc}",{q},0,{v},0,\n')
    if xfers:
        out += ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
                'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
                'Market Value,Realized P/L,Cash Amount,Code\n')
        for sym, d, q, mv in xfers:
            out += (f'Transfers,Data,Stocks,{cur},{sym},{d},ACATS,'
                    f'{"Out" if q < 0 else "In"},Other Broker,5550009,'  # pii-ok
                    f'{q},0,{mv if q > 0 else -mv},0,0,\n')
    return out


def _flat(r):
    return " ".join((r.stdout + r.stderr).split())


def _sheltered_project(td, country, plan, *, spin_in=None, mode=None,
                       merger=False, withdraw=False, margin_trades=()):
    """margin (taxable) and PLAN (sheltered), both IB, in COUNTRY. The
    spin-off (or, `merger`, an ABC -> XYZ share exchange) happens in
    `spin_in` (default: the sheltered account). `withdraw`: the 25
    spun-off shares move from the plan to margin at a market value of
    500 and margin sells them for 750."""
    c = country
    spin_in = spin_in or plan
    root = Path(td) / "p"
    root.mkdir()
    extra = {"year": 2025, "source_currencies": []}
    if mode:
        extra["sheltered_elections"] = mode
    (root / "taxjson.toml").write_text(
        settings_for(c, **extra)
        + '[accounts.margin]\ntype = "taxable"\n'
        + f'[accounts.{plan}]\ntype = "sheltered"\ntransfers = true\n')
    if merger:
        ev = dict(trades=[("ABC", "2025-01-10", 100, 10.0)],
                  corp=[(_MRG.format(t="ABC", n="ABC CORP",
                                     i="CA0000000001"),
                         "2025-03-03", -100, -2500),
                        (_MRG.format(t="XYZ", n="XYZ CORP",
                                     i="CA0000000002"),
                         "2025-03-03", 50, 2500)])
    else:
        ev = dict(trades=[("PARNT", "2025-01-10", 100, 10.0)],
                  corp=[(_SPIN, "2025-03-03", 25, 250)])
    plan_kw = dict(ev) if spin_in == plan else {}
    margin_kw = dict(ev) if spin_in == "margin" else {}
    if withdraw:
        plan_kw["xfers"] = [("SPNCO", "2025-05-01", -25, 500)]
        margin_kw["xfers"] = [("SPNCO", "2025-05-02", 25, 500)]
        margin_kw["trades"] = (list(margin_kw.get("trades", []))
                               + [("SPNCO", "2025-07-02", -25, 30.0)])
    if margin_trades:
        margin_kw["trades"] = (list(margin_kw.get("trades", []))
                               + list(margin_trades))
    for acct, kw, aid in ((plan, plan_kw, "U5550002"),     # pii-ok
                          ("margin", margin_kw, "U5550001")):  # pii-ok
        d = root / "inputs" / acct
        d.mkdir(parents=True)
        if kw:
            d.joinpath("ib.csv").write_text(_ib(c, aid, **kw))
    return root


def _corp_rows(root, acct):
    doc = json.loads((root / "work" / f"{acct}_ib_corp.json")
                     .read_text())
    return doc["transactions"]


def _event_id(root, acct):
    return _corp_rows(root, acct)[0]["corp_event_id"]


# The Canadian case's registered plan.
_CA_PLAN = "lira"
