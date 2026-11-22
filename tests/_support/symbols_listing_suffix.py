"""Shared test helpers: a Questrade export and an IB statement of one listing under two tickers, the scan and evidence helpers, RBC Direct rows and a run fixture.

Shared from: test_fix_qt_listing_suffix
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib import listing_suffix as LS
from taxjson.lib.symbol_codes import exact_name
from _planted_rates import plant
import _paths


REPO = _paths.REPO


def _scan(**cands):
    """Scan of a receiving group: listing=(currency, name, arrivals)."""
    s = LS.Scan()
    for k, (cur, name, arr) in cands.items():
        lst = k.replace("_", ".")
        c = s.candidate(lst, cur)
        c.add_name(name)
        c.arrivals += list(arr)
        s.saw(lst, cur, name)
    return s


def _ev(outs=(), named=(), seen=(), renames=()):
    """Evidence: outs=[(symbol, date, qty, name)], named=[(symbol, name)]
    (a broker that names its listings), seen=[(symbol, account, broker,
    currency, name)]."""
    ev = LS.Evidence()
    for sym, d, q, name in outs:
        ev.outs.append(LS.OutLeg("ibm", "ib", sym, d, q, {exact_name(name)}))
        ev.named.setdefault(sym, set()).add(exact_name(name))
        ev.seen.setdefault(sym, []).append(
            ("ibm", "ib", "USD", frozenset([exact_name(name)])))
    for sym, name in named:
        ev.named.setdefault(sym, set()).add(exact_name(name))
        ev.seen.setdefault(sym, []).append(
            ("ibm", "ib", "USD", frozenset([exact_name(name)])))
    for sym, acct, broker, cur, name in seen:
        ev.seen.setdefault(sym, []).append(
            (acct, broker, cur, frozenset([exact_name(name)])))
    ev.renames = {frozenset(p) for p in renames}
    return ev


def _res(scan, ev, **kw):
    return LS.resolve(scan, ev, account="qt", broker="questrade", **kw)


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
# (IB symbol, instrument name, shares) — bought in USD, all moved out.
IB_BOOK = [('QZAA', 'QZALPHA MINES CORP', 24),
           ('QZBB', 'QZBETA PIPELINES CORP', 30),
           ('QZGG', 'QZGAMMA SYSTEMS INC', 12),
           ('QZDD', 'QZDELTA GOLD CORP', 40)]


def ib_statement():
    fii = ''.join(f'Financial Instrument Information,Data,Stocks,{s},'
                  f'"{n}",99900020{i},,,NYSE,1,,,COMMON,,\n'
                  for i, (s, n, _q) in enumerate(IB_BOOK))
    trades = ''.join(f'Trades,Data,Order,Stocks,USD,U5550001,{s},'  # pii-ok
                     f'"2026-03-02, 10:00:00",{n},10,0,{-10 * n},0,0,0,0,O\n'
                     for s, _nm, n in IB_BOOK)
    xfers = ''.join(f'Transfers,Data,Stocks,USD,{s},2026-09-01,ACATS,Out,'
                    f'Other Broker,5550009,{-n},0,{-10 * n},0,0,\n'  # pii-ok
                    for s, _nm, n in IB_BOOK)
    return IB_HEAD + fii + IB_TRADES_H + trades + IB_XFER_H + xfers


def qt_export():
    """The Questrade website export: QZAA arrives as the TSX ticker QZAX
    on a USD row, QZBB as QZBX; QZGG under its own (US) ticker; QZDD as
    QZDX whose name differs (not one company). Later rows of QZAX."""
    return (QH
            + qt('2026-09-03', 'TFI', 'QZAX', 'QZALPHA MINES CORP TRANSFER '
                 'IN INTERACTIVE BROKER', '24')
            + qt('2026-09-03', 'TFI', 'QZBX', 'QZBETA PIPELINES CORP '
                 'TRANSFER IN INTERACTIVE BROKER', '30')
            + qt('2026-09-03', 'TFI', 'QZGG', 'QZGAMMA SYSTEMS INC TRANSFER '
                 'IN INTERACTIVE BROKER', '12')
            + qt('2026-09-03', 'TFI', 'QZDX', 'QZDELTA SILVER CORP TRANSFER '
                 'IN INTERACTIVE BROKER', '40')
            + qt('2026-09-29', 'DIV', 'QZAX', 'QZALPHA MINES CORP CASH DIV ON '
                 '24 SHS REC 09/15/26 PAY 09/29/26', '0', net='6.00',
                 act='Dividends')
            + qt('2026-10-05', 'Sell', 'QZAX', 'QZALPHA MINES CORP WE ACTED '
                 'AS AGENT', '-24', net='288.00', act='Trades', price='12',
                 gross='288'))


def _rates(path: Path, pair: str, rate: str, cleanup):
    """The rates file, and the same rates in the suite's HOME (put back
    by `cleanup`): a run that rebuilds the file keeps them (TJ-0635)."""
    d, lines = date(2025, 12, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 {pair} {rate} boc")
        d += timedelta(days=1)
    plant(path, "\n".join(lines) + "\n", cleanup=cleanup)


def _config(country):
    base, src = ("CAD", "USD") if country == "canada" else ("USD", "CAD")
    return (f'[settings]\nyear = 2026\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{src}"]\n'
            f'local_timezone = "America/Toronto"\n'
            + ('option_grant_timing_since = 2026\n' if country == "canada"
               else '') + '\n'
            f'[accounts.ibm]\ntype = "taxable"\n\n'
            f'[accounts.qt]\ntype = "taxable"\n')


def _run(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(root: Path, country: str, ticker_map: str = "", *,
             cleanup) -> None:
    (root / "taxjson.toml").write_text(_config(country))
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map)
    (root / "inputs" / "ibm").mkdir(parents=True)
    (root / "inputs" / "qt").mkdir(parents=True)
    (root / "inputs" / "ibm" / "U5550001_2026.csv").write_text(  # pii-ok
        ib_statement())
    (root / "inputs" / "qt" / "55500001.csv").write_text(  # pii-ok
        qt_export())
    if country == "canada":
        _rates(root / "work" / "to_base.csv", "USD CAD", "1.2500",
               cleanup)
    else:
        _rates(root / "work" / "to_base.csv", "CAD USD", "0.8000",
               cleanup)


class _RunBase:
    COUNTRY = "canada"
    MAP = ""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        _project(cls.root, cls.COUNTRY, cls.MAP,
                 cleanup=cls.addClassCleanup)
        cls.r = _run(cls.root, "run", "--no-input")
        cls.out = " ".join((cls.r.stdout + cls.r.stderr).split())

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def symbols(self, name):
        work = self.root / "work"
        book = json.loads((work / f"{name}.json").read_text())
        return {(t['action'], t['symbol']) for t in book['transactions']}

    def check_corrected(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        books = self.symbols("qt_questrade")
        moved = self.symbols("qt_questrade_transfers")
        # Every row of QZAX: the transfer-in, the dividend, the sale.
        self.assertIn(("TRANSFER", "QZAX.TO"), moved)
        self.assertIn(("DIVIDEND", "QZAX.TO"), books)
        self.assertIn(("BUYSELL", "QZAX.TO"), books)
        self.assertFalse({s for _a, s in books | moved} & {"QZAX.US"})
        # A genuine US ticker (the same as IB's) stays .US; a different
        # name is not one company.
        self.assertIn(("TRANSFER", "QZGG.US"), moved)
        self.assertIn(("TRANSFER", "QZDX.US"), moved)
        st = LS.read_state(LS.state_path(self.root / "work", "qt",
                                         "questrade"))
        return st


RBC_H = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
         '"Price","Settlement Date","Account","Value","Currency",'
         '"Description"\n')


def _rbc(d, act, sym, symdesc, qty, value, cur, desc, price=''):
    return (f'"{d} 00:00:00","{act}","{sym}","{symdesc}","{qty}",'
            f'"{price}","{d} 00:00:00","55500002","{value}","{cur}",'  # pii-ok
            f'"{desc}"\n')
