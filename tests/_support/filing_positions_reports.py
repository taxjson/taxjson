"""Shared test helpers: IB and RBC Direct positions reports.

Shared from: test_positions_reports
"""
from pathlib import Path


_IB_HEAD = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers Canada Inc.\n"
    "Statement,Data,Title,Activity Statement\n"
    'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U5550001\n"  # pii-ok
)
_OP_H = ("Open Positions,Header,DataDiscriminator,Asset Category,Currency,"
         "Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,"
         "Unrealized P/L,Code\n")
_IB_POS = (
    _OP_H
    + "Open Positions,Data,Summary,Stocks,CAD,ZZA,100,1,10.5,1050,12,"
      "1200,150,\n"
    + "Open Positions,Data,Summary,Stocks,USD,ZZB,-20,1,30,-600,25,"
      "-500,100,\n"
    + "Open Positions,Total,,Stocks,CAD,,,,,1050,,1200,150,\n"
    + _OP_H
    + "Open Positions,Data,Summary,Equity and Index Options,USD,"
      '"ZZB 16JAN26 30 C",2,100,1.5,300,1,200,-100,\n'
    + "Open Positions,Data,Lot,Stocks,CAD,ZZA,100,1,10.5,1050,12,1200,"
      "150,\n"
    + "Open Positions,Total,,Equity and Index Options,USD,,,,,300,,200,"
      "-100,\n"
)
_IB_STATEMENT = _IB_HEAD + _IB_POS + (
    "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
    "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
    "Realized P/L,MTM P/L,Code\n"
    'Trades,Data,Order,Stocks,CAD,ZZA,"2025-02-12, 09:35:14",100,10.50,'
    "10.50,-1050.00,0,1050.00,0,0,O\n")
_RBC_HOLD = (
    "Holdings Export as of Jan 5, 2026 at 9:00:28 am ET\n\n"
    '"Account","Product","Symbol","Name","Quantity","Currency",'
    '"Last Price","Total Book Cost","Total Market Value"\n'
    '"55500001","Equities","ZZR","ZZR CORP","10","CAD","6.00",'  # pii-ok
    '"$50.00","60.00"\n'
    '"55500001","Equities","ZZU","ZZU INC","5","USD","20.00",'  # pii-ok
    '"80.00","100.00"\n'
    '"55500001","Cash","","CASH","","CAD","","",""\n'  # pii-ok
)


def _write(td, name, text, encoding="utf-8"):
    p = Path(td) / name
    p.write_bytes(text.encode(encoding))
    return p
