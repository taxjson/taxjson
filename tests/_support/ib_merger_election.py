"""Shared test helpers: an IB statement of a buy and a share merger awaiting an election.

Shared from: test_pending_elections
"""


# An IB-shaped ABG->ABH merger corp-action section, wrapped in a
# minimal IB statement (Statement header + one trade) so
# taxjson-detect-brokerage recognizes the file as IB.
_SSL_RGLD_CSV = '''\
Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,CAD,ABG,"2025-02-05, 09:31:00",1600,15.90,0,-25440.0,-1,0,0,0,O
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","ABG(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (ABH.CAD, ABH GOLD INC, US0000000002)",100.0026,0,25840.67184,0,
Corporate Actions,Data,Stocks,CAD,2025-10-27,"2025-10-22, 20:25:00","ABG(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 (ABG, ABG MINING LTD, CA0000000001)",-1600.0416,0,-25920.67392,0,
'''
