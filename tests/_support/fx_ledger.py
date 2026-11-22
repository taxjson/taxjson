"""Shared test helpers: an IB statement, books and rates for the FX ledger.

Shared from: test_fix_fx_cash_v2
"""
from _planted_rates import plant

from datetime import date as _date, timedelta as _td


IB_STATEMENT = """\
Statement,Header,Field Name,Field Value
Statement,Data,Period,"January 1, 2025 - December 31, 2025"
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm in CAD,MTM in CAD,Code
Trades,Data,Order,Forex,CAD,USD.CAD,"2025-01-10, 10:00:00","1,000",1.3,1.3,"-1,300",-2,0,
Trades,Data,Order,Forex,CAD,USD.CAD,"2025-05-10, 10:00:00",-500,1.42,1.42,710,-2,0,
Trades,Data,Order,Forex,USD,GBP.USD,"2025-04-10, 10:00:00",100,1.25,1.25,-125,-1,0,
Trades,SubTotal,,Forex,CAD,USD.CAD,,500,,,-590,-4,0,
Deposits & Withdrawals,Header,Currency,Account,Settle Date,Description,Amount
Deposits & Withdrawals,Data,USD,U55X01,2025-02-03,Electronic Fund Transfer,250
Deposits & Withdrawals,Data,USD,U55X01,2025-03-03,Internal Transfer Out To Account U55X02,-40
Deposits & Withdrawals,Data,USD,U55X02,2025-03-03,Internal Transfer In From Account U55X01,40
Deposits & Withdrawals,Data,USD,U55X01,2025-06-02,Adjustment: Deposit Advance (First 100.00 of 300.00),100
Deposits & Withdrawals,Data,USD,U55X01,2025-06-05,Cancellation (First 100.00 of 300.00),-100
Deposits & Withdrawals,Data,Total,,,,250
Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,
Cash Report,Data,Starting Cash,USD,0,0,0,
Cash Report,Data,Ending Cash,USD,625,625,0,
Cash Report,Data,Commissions,CAD,-5,-5,0,
Cash Report,Data,Trades (Sales),CAD,710,710,0,
Cash Report,Data,Trades (Purchase),CAD,-1300,-1300,0,
Cash Report,Data,Trades (Sales),USD,-625,-625,0,
Cash Report,Data,Trades (Purchase),USD,1000,1000,0,
Cash Report,Data,Trades (Purchase),GBP,100,100,0,
Cash Report,Data,Starting Cash,Base Currency Summary,0,0,0,
"""

_TOML = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = ["USD"]
option_grant_timing_since = 2025
{extra}
[accounts.margin]
type = "taxable"
"""

# Deposit US$10,000 (declared cost 13,000); buy 100 XYZ 02-12 @1.35:
# 13,500 - 13,000 = +500; sell 03-12 @1.40; year-end US$10,000 held.
_ROWS_TT = """\
CASHOPEN 2025-01-01 USD 0 0
CASHMOVE 2025-01-15 USD 10000 cost=13000
BUYSELL 2025-02-12 09:30:00 XYZ.US 100 USD 100.0 -10000.0 0
BUYSELL 2025-03-12 09:30:00 XYZ.US -100 USD 100.0 10000.0 0
CASHBAL 2025-12-31 USD 10000
"""


def _rates(path, cleanup):
    """The rates file, and the same rates in the suite's HOME (put back
    by `cleanup`): a run that rebuilds the file keeps them (TJ-0635)."""
    d, lines = _date(2025, 1, 1), []
    while d <= _date.today():
        r = ("1.3000" if d < _date(2025, 2, 10) else
             "1.3500" if d < _date(2025, 3, 10) else "1.4000")
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD {r} boc")
        d += _td(days=1)
    plant(path, "\n".join(lines) + "\n", cleanup=cleanup)
