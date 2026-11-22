"""Shared test helpers: an IB listing journal into a sheltered account.

Shared from: test_fix_transfer_pairing_1027
"""
from datetime import date, timedelta
from pathlib import Path

from _planted_rates import plant


IB = ('Statement,Header,Field Name,Field Value\n'
      'Statement,Data,BrokerName,Interactive Brokers\n'
      'Statement,Data,Title,Activity Statement\n'
      'Statement,Data,Period,"January 1, 2026 - December 31, 2026"\n'
      'Financial Instrument Information,Header,Asset Category,Symbol,'
      'Description,Conid,Security ID,Underlying,Listing Exch,'
      'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n'
      'Financial Instrument Information,Data,Stocks,QZA,'
      '"QZALPHA MINES CORP",999000201,CA9990002011,,TSE,1,,,COMMON,,\n'
      'Trades,Header,DataDiscriminator,Asset Category,Currency,'
      'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
      'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n'
      'Trades,Data,Order,Stocks,CAD,U1234567,QZA,"2026-06-19, 10:00:00",'
      '100,40.00,0,-4000,0,0,0,0,O\n'
      'Trades,Data,Order,Stocks,CAD,U1234567,QZA,"2026-06-30, 10:00:00",'
      '-10,41.00,0,410.00,0,0,0,0,C\n'
      'Trades,Data,Order,Stocks,USD,U1234567,QZA,"2026-08-26, 10:00:00",'
      '-20,30.00,0,600.00,0,0,0,0,C\n'
      'Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
      'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
      'Market Value,Realized P/L,Cash Amount,Code\n'
      'Transfers,Data,Stocks,CAD,QZA,2026-07-31,InterDepot,Out,'
      'IB,U1234567,-90,0,-3600,0,0,\n'
      'Transfers,Data,Stocks,USD,QZA,2026-07-31,InterDepot,In,'
      'IB,U1234567,90,0,2900,0,0,\n'
      'Transfers,Data,Stocks,USD,QZA,2026-09-03,ATON,Out,'
      'Other Broker,99900009,-70,0,-2100,0,0,\n')
QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')


def _qt(td, action, desc, qty, net, act, price, gross):
    return (f"{td} 12:00:00 AM,{td} 12:00:00 AM,{action},QZA,{desc},{qty},"
            f"{price},{gross},0,{net},USD,99900001,{act},Individual RRSP\n")


def _qt_export(arrival):
    return (QH + _qt(arrival, "TFI", "QZALPHA MINES CORP TRANSFER IN "
                     "INTERACTIVE BROKER", "70", "0", "Transfers", "0", "0")
            + _qt("2026-09-14", "Buy", "QZALPHA MINES CORP WE ACTED AS "
                  "AGENT", "10", "-280.00", "Trades", "28.00", "-280.00"))


COUNTRY = {
    "canada": ('base_currency = "CAD"\nsource_currencies = ["USD"]\n'
               'local_timezone = "America/Toronto"\n'
               'option_grant_timing_since = 2026\n',
               "TOBASE QZA.US QZA.TO\n", "USD CAD 1.2500"),
    "usa": ('base_currency = "USD"\nsource_currencies = ["CAD"]\n'
            'local_timezone = "America/New_York"\n',
            "TOBASE QZA.TO QZA.US\n", "CAD USD 0.8000"),
}


def _project(root: Path, country: str, *, journal: str, aton: str,
             arrival: str, cleanup) -> None:
    """The project; its rates also in the suite's HOME (put back by
    `cleanup`): a run that rebuilds them keeps them (TJ-0635)."""
    settings, tobase, pair = COUNTRY[country]
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2026\ncountry = "{country}"\n' + settings
        + '\n[accounts.qzplan]\ntype = "sheltered"\ntransfers = true\n')
    (root / "ticker.map").write_text("# synthetic\n")
    (root / "tobase.map").write_text(tobase)
    inp = root / "inputs" / "qzplan"
    inp.mkdir(parents=True)
    (inp / "U1234567_2026.csv").write_text(
        IB.replace("2026-07-31", journal).replace("2026-09-03", aton))
    (inp / "99900001.csv").write_text(_qt_export(arrival))
    d, lines = date(2025, 12, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 {pair} boc")
        d += timedelta(days=1)
    plant(root / "work" / "to_base.csv", "\n".join(lines) + "\n",
          cleanup=cleanup)
