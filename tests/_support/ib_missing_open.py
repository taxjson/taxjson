"""Shared test helpers: IB fills and Canadian projects for missing history.

Shared from: test_fix_1029_r7c_missingopen
"""
from _qa_project import project, toml


IB_HEAD = ("Statement,Header,Field Name,Field Value\n"
           "Statement,Data,BrokerName,Interactive Brokers\n"
           "Account Information,Header,Field Name,Field Value\n"
           "Account Information,Data,Account,U1234567\n"
           "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
           "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
           "Realized P/L,MTM P/L,Code\n")


def ib_fill(day, qty, px, code, sym="SYNQ", cur="USD"):
    """One IB stock trade row (no commission)."""
    return (f'Trades,Data,Order,Stocks,{cur},{sym},"{day}, 10:00:00",'
            f"{qty},{px},{px},{-qty * px},0,{-qty * px},0,0,{code}\n")


def _ca_project(d, csv, tt=None, year=2025):
    files = {"margin/ib.csv": IB_HEAD + csv}
    if tt:
        files["margin/book.tt"] = tt
    root = project(d, "p", files)
    (root / "taxjson.toml").write_text(toml("canada", year=year))
    return root
