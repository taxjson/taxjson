"""A day with no clock that no export proves: whose order the books read.

A broker that prints no time of day (RBC) leaves a day's rows in the
file's order. Where the account's exports prove the series' life, the
parser puts a day's OPEN row before the CLOSE row it backs, in either
country (the proof is the rows' own). Where they do not, the parser keeps
the file's order and states the neutral fact on each row of the day:
`open_close_time`, the time the row takes in the broker's open/close
order. This module applies the country's reading of it:

- Canada [CA-OPT-12]: a row the broker
  marks CLOSE never opens a position, so on such a day the day's own OPEN
  rows back a CLOSE first: the rows take `open_close_time`, and the day
  is said (an ATTENTION naming the export to add). What no OPEN backs is
  reported by hand (lib/unbacked_close; backlog TJ-5349, TJ-5363).
- United States: the books go by position, not by the broker's mark
  (backlog TJ-5369): the file's order stays and nothing is said.
- No country: the file's order stays too (no country's reading is
  guessed); taxjson-brokerage says so in a note.

The fact is consumed here in either case: no later reader sees it.
"""
from typing import Any, Callable, Dict, List, Optional, Sequence

from taxjson.rules.country import marked_close_decides

FACT = "open_close_time"


def characterize_no_clock_order(rows: List[Dict[str, Any]], *, country: Optional[str],
                                days: Sequence[str] = (),
                                attention: Optional[Callable[[str], None]] = None) -> None:
    """Take `open_close_time` off every row of `rows`; where the country's
    books go by the broker's CLOSE mark, make it the row's time and say
    each of `days` (the parser's words for a day it left in file order)
    through `attention`."""
    applies = marked_close_decides(country)
    moved = False
    for row in rows:
        at = row.pop(FACT, None)
        if at and applies:
            row["time"] = at
            moved = True
    if moved and attention is not None:
        for words in days:
            attention(words)
