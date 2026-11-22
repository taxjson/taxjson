"""Shared test helpers: Questrade symbol codes: rows, an IB statement and runs.

Shared from: test_fix_qtcodes
"""
import os
import subprocess
import sys

import _paths


REPO = _paths.REPO

QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')


def q(td, action, sym, desc, qty, net='0', act='Transfers', price='0',
      gross='0', comm='0', cur='CAD'):
    return (f"{td} 12:00:00 AM,{td} 12:00:00 AM,{action},{sym},{desc},{qty},"
            f"{price},{gross},{comm},{net},{cur},55500001,{act},"  # pii-ok
            f"Individual margin\n")


TFI_M = q('2026-09-05', 'TFI', 'X000001', 'QZM MINING CORP COMMON SHARES '
          'TRANSFER IN INTERACTIVE BROKER', '24')
DIV_M = q('2026-09-30', 'DIV', 'X000001', 'QZM MINING CORP CASH DIV ON 24 '
          'SHS REC 09/20/26 PAY 09/30/26', '0', net='12.00', act='Dividends')
SELL_M = q('2026-11-02', 'Sell', 'X000001', 'QZM MINING CORP COMMON SHARES '
           'WE ACTED AS AGENT', '-24', net='307.05', act='Trades',
           price='13', gross='312', comm='-4.95')
DIV_NAME = q('2026-10-15', 'DIV', 'X000004', 'QZB BANK CORP CASH DIV ON 50 '
             'SHS REC 10/01/26 PAY 10/15/26', '0', net='20.00',
             act='Dividends')

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
# (symbol, instrument name, shares bought, transfer-out date or None)
IB_BOOK = [('QZM', 'QZM MINING CORP', 24, '2026-09-01'),
           ('QZN', 'QZN NETWORKS INC', 10, '2026-09-01'),
           ('QZA', 'QZALPHA INC-CL A', 15, '2026-09-02'),
           ('QZC', 'QZALPHA INC-CL C', 15, '2026-09-02'),
           ('QZB', 'QZB BANK CORP', 50, None),
           ('QZD', 'QZD DATA CORP', 7, '2026-09-02')]


def ib_statement(book=IB_BOOK):
    fii = ''.join(f'Financial Instrument Information,Data,Stocks,{s},'
                  f'"{n}",99900010{i},,,TSE,1,,,COMMON,,\n'
                  for i, (s, n, _q, _d) in enumerate(book))
    trades = ''.join(f'Trades,Data,Order,Stocks,CAD,U5550001,{s},'  # pii-ok
                     f'"2026-03-02, 10:00:00",{n},10,0,{-10 * n},0,0,0,0,O\n'
                     for s, _nm, n, _d in book)
    xfers = ''.join(f'Transfers,Data,Stocks,CAD,{s},{d},ACATS,Out,'
                    f'Other Broker,5550009,{-n},0,{-10 * n},0,0,\n'  # pii-ok
                    for s, _nm, n, d in book if d)
    return IB_HEAD + fii + IB_TRADES_H + trades + IB_XFER_H + xfers


def _run(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


CONFIG = """\
[settings]
year = 2026
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2026

[accounts.ibm]
type = "taxable"

[accounts.qt]
type = "taxable"
"""
