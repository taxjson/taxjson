"""Shared test helpers: a TSX and NYSE listing pair of one company: Questrade rows and export, an IB statement with a transfer, rates, a project with its ticker.map, its run, and a mixin running one per class.

Shared from: test_fix_tobase_transfer_pair
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from _planted_rates import plant
import _paths


REPO = _paths.REPO


QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')


def qt(td, action, sym, desc, qty, net='0', act='Transfers', price='0',
       gross='0', comm='0', cur='USD'):
    return (f"{td} 12:00:00 AM,{td} 12:00:00 AM,{action},{sym},{desc},{qty},"
            f"{price},{gross},{comm},{net},{cur},55500001,{act},"  # pii-ok
            f"Individual margin\n")


IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,BrokerName,Interactive Brokers\n'
           'Statement,Data,Title,Activity Statement\n'
           'Statement,Data,Period,"January 1, 2026 - December 31, 2026"\n'
           'Financial Instrument Information,Header,Asset Category,Symbol,'
           'Description,Conid,Security ID,Underlying,Listing Exch,'
           'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n')
IB_TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
               'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
               'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
IB_XFER_H = ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
             'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
             'Market Value,Realized P/L,Cash Amount,Code\n')
NAME = "QZALPHA MINES CORP"


def ib_statement():
    """IB holds the company's TSX listing QZAA in CAD and its NYSE
    listing QZAB in USD (another ticker), and moves the 24 QZAB out."""
    fii = (f'Financial Instrument Information,Data,Stocks,QZAA,"{NAME}",'
           f'999000201,,,TSE,1,,,COMMON,,\n'
           f'Financial Instrument Information,Data,Stocks,QZAB,"{NAME}",'
           f'999000202,,,NYSE,1,,,COMMON,,\n')
    trades = ('Trades,Data,Order,Stocks,CAD,U5550001,QZAA,'  # pii-ok
              '"2026-04-01, 10:00:00",10,20,0,-200,0,0,0,0,O\n'
              'Trades,Data,Order,Stocks,USD,U5550001,QZAB,'  # pii-ok
              '"2026-03-02, 10:00:00",24,12.5,0,-300,0,0,0,0,O\n')
    xfers = ('Transfers,Data,Stocks,USD,QZAB,2026-09-01,ACATS,Out,'
             'Other Broker,5550009,-24,0,-300,0,0,\n')  # pii-ok
    return IB_HEAD + fii + IB_TRADES_H + trades + IB_XFER_H + xfers


def qt_export(sale=True):
    """Questrade's website export: the 24 QZAB arrive as the TSX root
    QZAA on a USD row, and are sold there later."""
    return (QH
            + qt('2026-09-03', 'TFI', 'QZAA', f'{NAME} TRANSFER IN '
                 'INTERACTIVE BROKER', '24')
            + (qt('2026-10-05', 'Sell', 'QZAA', f'{NAME} WE ACTED AS '
                  'AGENT', '-24', net='450.00', act='Trades', price='18.75',
                  gross='450') if sale else ''))


def _rates(path: Path, pair: str, rate: str, cleanup):
    """The rates file, and the same rates in the suite's HOME (put back
    by `cleanup`): a run that rebuilds the file keeps them (TJ-0635)."""
    d, lines = date(2025, 12, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 {pair} {rate} boc")
        d += timedelta(days=1)
    plant(path, "\n".join(lines) + "\n", cleanup=cleanup)


def _config(country, kind):
    base, src = ("CAD", "USD") if country == "canada" else ("USD", "CAD")
    return (f'[settings]\nyear = 2026\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{src}"]\n'
            f'local_timezone = "America/Toronto"\n'
            + ('option_grant_timing_since = 2026\n' if country == "canada"
               else '') + '\n'
            f'[accounts.acct]\ntype = "{kind}"\n')


def _run(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(root: Path, country: str, kind: str, ticker_map: str,
             sale: bool = True, *, cleanup) -> None:
    (root / "taxjson.toml").write_text(_config(country, kind))
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map)
    acct = root / "inputs" / "acct"
    acct.mkdir(parents=True)
    (acct / "U5550001_2026.csv").write_text(ib_statement())  # pii-ok
    (acct / "55500001.csv").write_text(qt_export(sale))  # pii-ok
    if country == "canada":
        _rates(root / "work" / "to_base.csv", "USD CAD", "1.2500",
               cleanup)
    else:
        _rates(root / "work" / "to_base.csv", "CAD USD", "0.8000",
               cleanup)


class _Run:
    COUNTRY = "canada"
    KIND = "taxable"
    MAP = "TOBASE QZAB.US QZAA.TO\n"
    SALE = True

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        _project(cls.root, cls.COUNTRY, cls.KIND, cls.MAP, cls.SALE,
                 cleanup=cls.addClassCleanup)
        cls.r = _run(cls.root, "run", "--no-input")
        cls.out = " ".join((cls.r.stdout + cls.r.stderr).split())

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def book_symbols(self, name):
        doc = json.loads((self.root / "work" / f"{name}.json").read_text())
        return {t["symbol"] for t in doc["transactions"]}

    def summary(self):
        r = _run(self.root, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout)
