"""Shared test helpers: IB futures trades and instrument rows.

Shared from: test_fix_a2_ib_dates
"""


FUT_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
         'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
         'Notional Value,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
FII_H = ('Financial Instrument Information,Header,Asset Category,Symbol,'
         'Description,Conid,Security ID,Underlying,Listing Exch,Multiplier,'
         'Expiry,Delivery Month,Type,Strike,Code\n')
FUT_FII = ('Financial Instrument Information,Data,Futures,QZCLG6,'
           'QZCL FEB26,999000011,,QZCL,NYMEX,"1,000",2026-01-20,2026-02,'
           ',,\n')


def _stock_fii(sym, exch, conid='999000031'):
    return (f'Financial Instrument Information,Data,Stocks,"{sym}",{sym} '
            f'FUND,{conid},,,{exch},1,,,ETF,,\n')
