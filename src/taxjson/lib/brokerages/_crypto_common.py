"""Strict helpers shared by the Kraken and Coinbase parsers, about
refusing to produce a quietly-wrong number or date. `strict_money` (a CSV
amount in the currency-prefixed forms exchanges ship, RAISING on anything
else: `BaseBrokerage.clean_number` returns 0.0 for what it can't read, so
`CA$4.00` became a $0 basis row without a word) is the toolbox's since step
5a (`taxjson.parsers.toolbox.numbers`), re-exported here, as are the fiat
currencies and the USD stablecoins (`taxjson.parsers.toolbox.assets`, PLAN
step 14).

Kraken and Coinbase stamp every row in UTC; lib/timezones' `utc_to_local`
dates it in the user's zone.
"""

from taxjson.parsers.toolbox.masking import mask_id
from taxjson.parsers.toolbox.numbers import strict_money as strict_money
# The fiat currencies and the USD stablecoins (live), the toolbox's since
# PLAN step 14 (`taxjson.parsers.toolbox.assets`), re-exported here.
from taxjson.parsers.toolbox.assets import FIAT_CURRENCIES as FIAT_CURRENCIES
from taxjson.parsers.toolbox.assets import USD_STABLECOINS as USD_STABLECOINS
from taxjson.lib.once import said_set
from taxjson.lib.stage_msg import emit_line
from taxjson.model import money as M
from typing import Dict, List


def order_within_seconds(transactions, row_line, key_of_line) -> None:
    """Reorder, in place, the rows that share one (date, time) by the
    instant their export line states (`key_of_line`: export line -> a
    sort key; `row_line`: the row's line): the books keep rows of one
    second in the order the parser gives them, so a descending export
    booked a rebuy before the sale it followed. Rows of a stamp with one
    instant, and rows of a stamp with a line of no known instant, keep
    their order. (Kraken: the fraction of a second, backlog TJ-0746. Any
    crypto parser: two UTC seconds utc_to_local compresses into one in
    the hour the clocks repeat, backlog TJ-1056.)"""
    groups: Dict[tuple, list] = {}
    for i, t in enumerate(transactions):
        groups.setdefault((t.get('date'), t.get('time')), []).append(i)
    for idx in groups.values():
        if len(idx) < 2:
            continue
        keys = {i: key_of_line.get(row_line(transactions[i])) for i in idx}
        if any(k is None for k in keys.values()):
            continue
        moved = [transactions[i] for i in sorted(idx, key=keys.get)]
        for pos, t in zip(idx, moved):
            transactions[pos] = t


def order_across_exports(rows, instants) -> List[int]:
    """The order (indexes into `rows`, the parsed rows of several exports
    in file order; each with date, time and source) that puts rows of one
    (date, time) from more than one export by `instants` (parallel: each
    row's instant, or None): order_within_seconds orders one file's, and
    the books keep rows of one second in the order they are given, so two
    UTC seconds utc_to_local compresses into one replayed in file order
    (backlog TJ-1227). A stamp with a row of no known instant, or from one
    export, keeps its order."""
    order = list(range(len(rows)))
    groups: Dict[tuple, list] = {}
    for i, t in enumerate(rows):
        groups.setdefault((t.date, t.time), []).append(i)
    for idx in groups.values():
        if len({rows[i].source for i in idx}) < 2 \
                or any(instants[i] is None for i in idx):
            continue
        for pos, i in zip(idx, sorted(idx, key=lambda n: instants[n])):
            order[pos] = i
    return order


def same_coin_hint(a: str, b: str, qty_a: float, qty_b: float,
                   where: str) -> None:
    """A swap between two codes where one is the other plus a suffix
    (ETH and ETH2) at exactly 1:1 is booked as the swap it is — a
    disposition — but it is often a staked or wrapped form of the same
    coin: say once which ticker.map line folds them (taxjson ships no
    such fold; owner, 2026-10-04)."""
    a, b = (a or '').strip().upper(), (b or '').strip().upper()
    if not a or not b or a == b:
        return
    short, long_ = sorted((a, b), key=len)
    if not long_.startswith(short):
        return
    qa, qb = abs(qty_a or 0.0), abs(qty_b or 0.0)
    if not qa or abs(qa - qb) > 1e-8 * max(qa, qb):
        return
    from taxjson.lib.markets import note_builtin
    note_builtin("same-coin-hint", f"{long_}>{short}", (
        f"{where}: {a} -> {b} at exactly 1:1 is booked as a swap (a "
        f"disposition). If {long_} is a staked or wrapped form of "
        f"{short} (the same property), add `GLOBAL {long_} {short}` to "
        f"ticker.map and re-run."))


class RowIds:
    """The broker's own row ids of one export (a Coinbase ID, a Kraken
    trades txid), which the parsers keep as the transaction id. The
    de-duplication (taxjson-sort --dedup, bin/taxjson_sort.plan_dedup)
    reads two rows of one file with one id as the file repeating itself
    and books one: right for an identical row (two downloads pasted
    together), but a second row with DIFFERENT content was a trade
    dropped from the books with no message (2026-10-25 audit
    robustness#4). `check` lets an identical repeat through and refuses
    a different one, naming the file, both lines and the id (masked: its
    first 2 characters, the privacy rule for ids)."""

    def __init__(self, label: str, id_name: str):
        # label: "Coinbase CSV cb.csv"; id_name: the column, "ID".
        self.label, self.id_name = label, id_name
        self._seen: dict = {}

    def check(self, row_id: str, line: int, content: tuple,
              brief: str) -> None:
        row_id = (row_id or '').strip()
        if not row_id:
            return
        prev = self._seen.get(row_id)
        if prev is None:
            self._seen[row_id] = (line, content, brief)
            return
        p_line, p_content, p_brief = prev
        if p_content == content:
            return
        raise ValueError(
            f"{self.label} lines {p_line} and {line}: {self.id_name} "
            f"{mask_id(row_id)} is on two rows with DIFFERENT content "
            f"(line {p_line}: {p_brief}; line {line}: {brief}). One id "
            f"names one transaction, and the books keep one row per id, "
            f"so the other trade would be dropped. Fix the file: delete "
            f"the row that is a stray or edited copy, or download the "
            f"export again; if the broker's own export holds both rows, "
            f"give one a distinct {self.id_name} (append -2 to it) so "
            f"both are booked.")


# A USD stablecoin traded this far from 1.00 USD is said: the parsers
# book stablecoins as US-dollar cash (an approximation), so a de-peg
# gain or loss never reaches the books (partition INPUTS-12; tax-logic
# CA-CRYPTO-02 / US-CRYPTO-02).
DEPEG_TOLERANCE = 0.02


# The run's rates table (work/to_base.csv, the conversion stage's own
# rates and lookback), installed by `taxjson-brokerage --rates`: a fill
# valued in CAD, EUR, ... is turned into US dollars through it before
# the peg test (re-audit A2-0590), and a parser may book with it (Kraken
# values a fiat leg of a multi-leg receipt through `usd_value`). The parse
# stage gives the rates only to a parser the registry marks `reads_rates`,
# and declares them as its input (TJ-2497, TJ-2508); tests/test_freshness_
# edges.py holds the parser modules naming these readers to that mark.
# None = no rates given.
_DEPEG_RATES = None
# Currencies already named as "not checked" (one note each per parse run).
_DEPEG_UNCHECKED = said_set()


def set_depeg_rates(path, base) -> None:
    """Load the rates file (`DATE TIME FROM TO RATE [SOURCE]`, rates to
    `base`) the de-peg test converts a non-USD fill with, and `usd_value`
    reads (see `_DEPEG_RATES`); None clears."""
    global _DEPEG_RATES
    _DEPEG_UNCHECKED.clear()
    if path is None:
        _DEPEG_RATES = None
        return
    from pathlib import Path
    from taxjson.lib.base_rates import Rates, load_rates
    if not base:
        # The file's own target currency (its TO column).
        base = 'CAD'
        for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
            parts = line.split()
            if len(parts) >= 5:
                base = parts[3]
                break
    _DEPEG_RATES = Rates(load_rates(Path(path)), str(base))


def usd_value(amount: float, currency: str, date: str):
    """`amount` of `currency` in US dollars on `date` through the loaded
    rates (each currency's rate to the base, the conversion stage's
    lookback), or None when a rate is missing."""
    cur = (currency or 'USD').strip().upper() or 'USD'
    if cur == 'USD':
        return amount
    if _DEPEG_RATES is None:
        return None
    a = _DEPEG_RATES.get(cur, str(date)[:10])
    u = _DEPEG_RATES.get('USD', str(date)[:10])
    if not a or not u:
        return None
    return amount * a[0] / u[0]


def warn_depeg(coin: str, usd_price: float, qty: float, date: str,
               where: str, currency: str = 'USD') -> bool:
    """Print a warning when a stablecoin fill's USD price is more than
    DEPEG_TOLERANCE from 1.00. `usd_price` is the fill's price in
    `currency`: a non-USD price is converted through the loaded rates
    first (re-audit A2-0590); with no rate the fill is said to be
    unchecked, never guessed. Returns whether it warned."""
    try:
        price = float(usd_price)
    except (TypeError, ValueError):
        return False
    if not price:
        return False
    cur = (currency or 'USD').strip().upper() or 'USD'
    native = price
    if cur != 'USD':
        conv = usd_value(price, cur, date)
        if conv is None:
            if cur not in _DEPEG_UNCHECKED:
                _DEPEG_UNCHECKED.add(cur)
                emit_line(f"note: {where}: a {coin} fill valued in {cur} on "
                      f"{date} is not checked for a de-peg: no {cur} and "
                      f"USD rate for that day (`tjs run` passes its "
                      f"rates; add {cur} to source_currencies). Further "
                      f"{cur} fills are not named.")
            return False
        price = conv
    if abs(price - 1.0) <= DEPEG_TOLERANCE:
        return False
    # The ATTENTION channel: `tjs run` echoes it on the console
    # (it was only in the .sum, re-audit A2-1001) — money the books
    # leave out.
    via = (f" ({M.shown(native, 4):.4f} {cur} at the day's rate)" if cur != 'USD'
           else "")
    emit_line(f"warning: ATTENTION: {where}: {coin} traded at {M.shown(price, 4):.4f} USD"
          f"{via} on {date} "
          f"— stablecoins are booked as US-dollar cash (an "
          f"approximation), so the {M.shown(abs(price - 1.0) * qty, 2):,.2f} USD "
          f"de-peg difference on {qty:g} {coin} is not in the gains; "
          f"report it by hand if it matters.")
    return True
