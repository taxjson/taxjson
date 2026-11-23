"""Shared test helpers: a run's work folder for ticker-map hints.

Shared from: test_fix_suggest_conditional_hints
"""
import json
from pathlib import Path

from _support.rbc_rows import parse_files, row as rbc_row


DIV = rbc_row("June 2, 2025", "Dividend", "QZLR", "QZLR RESEARCH CORP", "",
              "", "12.00", "USD", "QZLR RESEARCH CORP CASH DIV")


def _book(work: Path, name: str, rows) -> None:
    (work / name).write_text(json.dumps(
        {"metadata": {"source_brokerage": "x"}, "transactions": rows}))


def _project(td: str, other_rows=None, holdings=None) -> Path:
    """work/ as `tjs run` leaves it: the RBC account's parse and its
    .diag (the parser's own output for a dividend-only USD symbol), plus
    another account's parse when given."""
    root = Path(td)
    work = root / "work"
    work.mkdir()
    txs, err, _ = parse_files({"rbc.csv": DIV})
    assert {t["symbol"] for t in txs} == {"QZLR.US"}, txs
    assert "TOBASE QZLR.US QZLR.TO" in err, err
    _book(work, "margin_rbc_direct.json", txs)
    (work / "margin_rbc_direct.json.diag").write_text(err)
    if other_rows is not None:
        _book(work, "tfsa_ib.json", other_rows)
    # The account whose parse work/ holds: a book is an account's
    # (lib/clean holds_books).
    cfg = ('[settings]\nyear = 2025\ncountry = "canada"\n\n'
           '[accounts.margin]\ntype = "taxable"\n')
    if holdings:
        (root / "h.toml").write_text(holdings)
        cfg += '\n[accounts.tfsa]\nholdings = ["h.toml"]\n'
    (root / "taxjson.toml").write_text(cfg)
    return root


def _tx(sym, qty=10, action="BUYSELL"):
    return {"date": "2025-03-03", "time": "10:00:00", "action": action,
            "symbol": sym, "quantity": qty, "price": 10.0,
            "currency": "CAD"}
