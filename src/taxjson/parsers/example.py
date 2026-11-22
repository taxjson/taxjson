"""The shape of a parser on the parse template, for the scaffolding tool's
prompt and a contributor's first read (backlog TJ-2320, TJ-2354): a made-up
broker, Sample Securities, whose export is one header and rows of BUY, SELL
and DIV closed by a totals footer. It is not registered; `tjs` never runs
it. Every parser fills the same four phases (`taxjson.parsers.protocol`):
`read` the rows, `classify` each, `build` the ones that are events, `finish`
the parse; the template (`BaseBrokerage.parse_file`) counts, marks and
reports around them. Each builder declares the kinds of row it books
(`builds`), and the fixture holds a row of each."""
import csv
from pathlib import Path
from typing import Any, Dict, List

from taxjson.lib.brokerages.base import (BaseBrokerage, parse_strict_number,
                                         read_broker_text, shown_name)
from taxjson.parsers.protocol import Outcome, Row, builds


class SampleRow(Row):
    """A row and what classify learned of it: its action, upper-cased."""
    __slots__ = ("action",)

    def __init__(self, cells: Dict[str, str], line: int):
        super().__init__(cells, line)
        self.action = (cells.get("Action") or "").strip().upper()


class SampleBrokerage(BaseBrokerage):
    """Sample Securities: `Date,Action,Symbol,Quantity,Price,Amount,Currency`,
    one row per line after the header, a `TOTAL` footer."""
    DEFAULT_ACCOUNT = "SAMPLE"

    def read(self, path: Path):
        """The rows with their export line (the header is line 1)."""
        self._shown = shown_name(path)
        reader = csv.DictReader(read_broker_text(path).splitlines())
        return [SampleRow(cells, line) for line, cells in enumerate(reader, start=2)]

    def classify(self, row: SampleRow) -> Outcome:
        """What each row is: a trade or a dividend to build, the footer a
        recognized non-event, anything else a skip the summary lists."""
        if row.action in ("BUY", "SELL"):
            return row.build(self._build_trade)
        if row.action == "DIV":
            return row.build(self._build_dividend)
        if row.action == "TOTAL":
            return Outcome.nonevent("totals footer")
        return Outcome.skip(f"action {row.action or '(blank)'}")

    def build(self, row: SampleRow) -> List[Dict[str, Any]]:
        """The builder classify kept on the row."""
        return row.builder(row)

    def finish(self, transactions: List[Dict[str, Any]], path: Path) -> List[Dict[str, Any]]:
        """Nothing to close: the rows stand alone. (A parser that builds its
        cash events builds them here through `self._cash_events_from(build)`,
        so a bad cash cell is the file's `cash_problem`, not a refused parse.)"""
        return transactions

    def _where(self, row: SampleRow) -> str:
        return f"{self._shown} line {row.line}"

    def _money(self, row: SampleRow, column: str) -> float:
        """A required cell: refused when blank or ambiguous, never read as 0."""
        return parse_strict_number(row.cells.get(column), field=column.lower(),
                                   where=self._where(row))

    @builds("trade")
    def _build_trade(self, row: SampleRow) -> List[Dict[str, Any]]:
        c = row.cells
        currency = (c.get("Currency") or "CAD").strip().upper()
        qty = self.signed_quantity(self._money(row, "Quantity"), row.action == "SELL")
        price, net = self._money(row, "Price"), self._money(row, "Amount")
        date = (c.get("Date") or "").strip()
        return [{
            'action': 'BUYSELL', 'date': date, 'time': '09:30:00',
            'date_settle': self.equity_settlement_date(date, currency),
            'symbol': self.apply_currency_suffix(c.get("Symbol") or "", currency),
            'quantity': qty, 'currency': currency, 'price': price,
            'fee': self.back_compute_fee(qty, price, net, False),
            'net_amount': net, 'gross_amount': self.theoretical_gross(qty, price, False),
            'account': self.DEFAULT_ACCOUNT, 'description': row.action,
        }]

    @builds("dividend")
    def _build_dividend(self, row: SampleRow) -> List[Dict[str, Any]]:
        c = row.cells
        currency = (c.get("Currency") or "CAD").strip().upper()
        return [{
            'action': 'DIVIDEND', 'date': (c.get("Date") or "").strip(), 'time': '09:30:00',
            'symbol': self.apply_currency_suffix(c.get("Symbol") or "", currency),
            'quantity': 0.0, 'currency': currency, 'price': 0.0,
            'net_amount': self._money(row, "Amount"),
            'account': self.DEFAULT_ACCOUNT, 'description': 'DIV',
        }]
