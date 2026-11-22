"""Shared test helpers: an IB statement header and an option symbol.

Shared from: test_fix_1029_r7f_closecash
"""


_SYM = "SYN251219C00010000.TO"
_IB_HEAD = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers Canada Inc.\n"
    "Statement,Data,Title,Activity Statement\n"
    'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U1234567\n"
    "Account Information,Data,Base Currency,CAD\n"
    "Financial Instrument Information,Header,Asset Category,Symbol,Description,"
    "Conid,Security ID,Underlying,Listing Exch,Multiplier,Expiry,Delivery Month,"
    "Type,Strike,Code\n"
    "Financial Instrument Information,Data,Equity and Index Options,"
    "SYN 19DEC25 10 C,SYN CALL,999000101,,SYN,TSE,10,2025-12-19,,C,10,\n"
    "Transfers,Header,Asset Category,Currency,Symbol,Date,Type,Direction,"
    "Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,"
    "Cash Amount,Code\n")
