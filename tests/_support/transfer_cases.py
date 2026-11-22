"""Shared test helpers: moves of your own: three accounts and a crypto move.

Shared from: test_fix_1029_transfers
"""
from pathlib import Path

from tax_rules.dual import settings_for


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _qt(acct_no: str, cur: str, *rows) -> str:
    """A Questrade activity export: rows of (date, action, description,
    quantity, price, net amount, activity type)."""
    out = _QH
    for d, act, desc, q, px, net, kind in rows:
        out += (f"{d},{d},{act},ZZQ,{desc},{q},{px:.2f},{net:.2f},0.00,"
                f"{net:.2f},{cur},{kind},{acct_no},Margin\n")
    return out


def _write(root: Path, country: str, accounts: dict) -> Path:
    """A project: `accounts` {name: export text}, each a taxable
    account."""
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2024, source_currencies=[])
        + "".join(f'[accounts.{n}]\ntype = "taxable"\n' for n in accounts))
    for n, text in accounts.items():
        (root / "inputs" / n).mkdir(parents=True)
        (root / "inputs" / n / "questrade_2024.csv").write_text(text)
    return root


def _three_accounts(root: Path, country: str) -> Path:
    """The audit's case (TJ-0380): qa buys 100 ZZQ at 2.00 and sends
    them out on 2024-03-01; qc receives 100 on 03-02 (the closest
    date: the move's other leg) and sells them at 4.00; qb receives 100
    on 03-08 from outside the books (TRANSFER BOOK VALUE 250.00) and
    sells them at 3.00. Gains: qc 400 - 200, qb 300 - 250: 250."""
    cur = "CAD" if country == "canada" else "USD"
    return _write(root, country, {
        "qa": _qt("99900001", cur,
                  ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0, -200.0,
                   "Trades"),
                  ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT", -100,
                   0.0, 0.0, "Transfers")),
        "qb": _qt("99900002", cur,
                  ("2024-03-08", "TF6",
                   "ZZQ CORP TRANSFER BOOK VALUE 250.00", 100, 0.0, 0.0,
                   "Transfers"),
                  ("2024-06-03", "Sell", "ZZQ CORP", -100, 3.0, 300.0,
                   "Trades")),
        "qc": _qt("99900003", cur,
                  ("2024-03-02", "TF6",
                   "ZZQ CORP TRANSFER BOOK VALUE 250.00", 100, 0.0, 0.0,
                   "Transfers"),
                  ("2024-06-03", "Sell", "ZZQ CORP", -100, 4.0, 400.0,
                   "Trades"))})


_CB = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
       "Price Currency,Price at Transaction,Subtotal,"
       "Total (inclusive of fees and/or spread),Fees and/or Spread,"
       "Notes\n")


def _crypto_move(root: Path, country: str) -> Path:
    """The audit's case (TJ-0388): `sender` buys 1 ETH for 100 and
    sends it to `receiver` (another of your Coinbase accounts) on
    2025-07-01; `receiver` sells 0.5 for 100 on 07-10."""
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2025, source_currencies=[])
        + '[accounts.sender]\ntype = "taxable"\ncrypto = true\n'
        + '[accounts.receiver]\ntype = "taxable"\ncrypto = true\n')
    for a, body in (
            ("sender",
             f"x1,2025-05-01 12:00:00 UTC,Buy,ETH,1,{cur},100,100,100,0,"
             f"Bought 1 ETH\n"
             f"x2,2025-07-01 12:00:00 UTC,Send,ETH,1,{cur},150,150,150,0,"
             f"Sent 1 ETH\n"),
            ("receiver",
             f"y1,2025-07-01 13:00:00 UTC,Receive,ETH,1,{cur},150,150,150,"
             f"0,Received 1 ETH\n"
             f"y2,2025-07-10 12:00:00 UTC,Sell,ETH,0.5,{cur},200,100,100,"
             f"0,Sold 0.5 ETH\n")):
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "coinbase.csv").write_text(_CB + body)
    return root
