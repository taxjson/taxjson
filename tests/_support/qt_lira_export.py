"""Shared test helpers: a Questrade LIRA export.

Shared from: test_fix_v0241_leftovers
"""


# The issue's synthetic export (GitHub issue #3), account id pii-ok.
_ISSUE3 = (
    "Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
    "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
    "Activity Type,Account Type\n"
    "2026-07-15 12:00:00 AM,2026-07-16 12:00:00 AM,Buy,QZP.TO,QZPIPE CORP "
    "WE ACTED AS AGENT,150,40.00,-6000.00,0,-6000.00,CAD,99900001,Trades,"
    "Individual LIRA\n"
    "2026-09-28 12:00:00 AM,2026-09-29 12:00:00 AM,Sell,QZP.TO,QZPIPE CORP "
    "WE ACTED AS AGENT,-150,41.00,6150.00,0,6150.00,CAD,99900001,Trades,"
    "Individual LIRA\n"
    "2026-09-29 12:00:00 AM,2026-09-29 12:00:00 AM,DIV,.QZP,QZPIPE CORP "
    "CASH DIV ON 150 SHS REC 09/15/26 PAY 09/29/26,0.0,0.0,0.0,0.0,50.0,"
    "USD,99900001,Dividends,Individual LIRA\n"
    "2026-09-29 12:00:00 AM,2026-09-29 12:00:00 AM,REI,QZP,QZPIPE CORP "
    "REINV@C$41.00 REC 09/15/26 PAY 09/29/26,1.0,0.0,0.0,0.0,-29.50,USD,"
    "99900001,Dividend reinvestment,Individual LIRA\n"
    "2026-10-01 12:00:00 AM,2026-10-02 12:00:00 AM,Sell,QZP.TO,QZPIPE CORP "
    "WE ACTED AS AGENT,-1,41.50,41.50,0,41.50,CAD,99900001,Trades,"
    "Individual LIRA\n")
