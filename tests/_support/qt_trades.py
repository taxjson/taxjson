"""Shared test helpers: a Questrade header and trade rows.

Shared from: test_workflow_audit_fixes
"""


QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")
QT_ROWS = (QT_HEAD
           + "2024-01-02,2024-01-04,Buy,ZZQ,ZZQ CORP,100,50.00,-5000.00,0,"
             "-5000.00,USD,Trades,55500001,Margin\n"            # pii-ok
           + "2024-03-01,2024-03-05,Sell,ZZQ,ZZQ CORP,-100,60.00,6000.00,0,"
             "6000.00,USD,Trades,55500001,Margin\n")           # pii-ok
