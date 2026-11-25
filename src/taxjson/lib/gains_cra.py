"""`tjs gains-cra`: the tax year's dispositions one row per sale, in the
CRA's column layout: Sale date, Settle date, Description, Qty, Proceeds in
the trade's currency, FX, then Proceeds, ACB, Commissions and Gain/Loss in
the base currency (CAD).

A row's four CAD cells are Schedule 3's cells for that one disposition:
lib/tax_forms `schedule3_disposition` (the part of `build_schedule3` each
disposition adds: the sale commission split out, a grant-timing write's
premium gross, a short sale's proceeds, a futures contract's settled P/L, a
rebate kept netted, the allowed gain) footed alone by `_foot_cells`. The
line totals and the grand total are build_schedule3's own, the rows `tjs
sum` shows FOR THE RETURN (with an ACCOUNT, `tjs sum ACCOUNT`'s); Schedule
3 rounds each security's total, the rows each sale, so the rows can add to
a few cents apart: the difference is said per line, never hidden.

The native proceeds are the same function's over the native records of
the same trade, found by its id (the native gains row, or a long sale's
parsed transaction): never the CAD cell divided by a rate. The FX is the
rate the conversion read for the trade the proceeds came from (the row's
own: a sale, a grant-timing write), looked up by the command in the run's
rate table on the trade's rate date (its settlement date), the day of the
rate used said beside it; for proceeds that came from other trades (a
short's opening sales, a write closed under close timing, a futures P/L)
it is CAD ÷ native at RATE_PLACES, noted. Every foreign row is checked:
native × FX against the CAD cell, within a cent for a rate-table row and
within what RATE_PLACES allow for a weighted one (native × 0.00005 + a
cent); a larger difference is a note with both figures. A missing native
figure or FX shows "—" with a note, and the currency's subtotal says how
many rows its native total leaves out.

The command (cli/gains_filing.py `cmd_gains_cra`) reads the files and the rates;
this module only computes and lays out (`report`, `render`, `csv_rows`).
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from taxjson.lib import out as _out
from taxjson.lib import tax_forms as TF
from taxjson.lib.futures import is_plain_future
from taxjson.lib.futures_prefixes import FUTURES_PREFIXES
from taxjson.lib.markets import strip_listing_suffix
from taxjson.lib.option_symbols import (is_option_symbol, parse_option_expiry,
                                        parse_option_right, parse_option_strike,
                                        parse_option_underlying)
from taxjson.lib.report_fmt import fmt_money, fmt_qty
from taxjson.model import money as M
from taxjson.model.result import GainRow

RATE_PLACES = 4
# The CSV's columns and the console's (a currency's table names it in its
# two native columns; the base currency's table has neither).
CSV_COLUMNS = ("Sale date", "Settle date", "Description", "Symbol", "Qty", "Currency",
               "Proceeds (native)", "FX", "Proceeds (CAD)", "ACB (CAD)",
               "Commissions (CAD)", "Gain/Loss (CAD)", "Denied (CAD)", "Account", "Notes")
_MIN_DESC = len("Description")
_CELLS = ("proceeds", "acb", "outlays", "gain")
# A row's denied losses (`denied` holds `permanently_denied`; an in-kind
# contribution's apart), added per table and for every row in --json.
_DENIED = ("denied", "permanently_denied", "denied_contribution")
_NONE = "—"
# The tolerances of the native × FX check: a rate-table row's product is
# the conversion's own (one cent for the cell's rounding); a weighted rate
# is cut to RATE_PLACES, so the product can be off by native × half a unit
# of its last place, plus the cent.
_CENT = Decimal("0.01")
_HALF_UNIT = Decimal(5).scaleb(-(RATE_PLACES + 1))


@dataclass(frozen=True)
class Native:
    """What the command found for one disposition in the native books:
    the trade's currency, a gains-shaped row of the same trade in that
    currency (`source`: where it came from; `why`: why there is none),
    the rate table's rate of the row's own trade (`rate`, for its rate
    date `rate_date`, the table's rate of `used_date`: the same day, or
    the last one before it the table has; None: no rate)."""
    currency: str = ""
    row: Optional[Dict[str, Any]] = None
    source: str = ""
    why: str = ""
    rate: Optional[Decimal] = None
    rate_date: str = ""
    used_date: str = ""


def native_from_tx(tx: Dict[str, Any], e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A long sale's native gains-shaped row from its parsed native
    transaction (the same id): its net proceeds, commission and fee, for
    the units the gains row sold; None when the transaction is not that
    sale (another quantity, a buy)."""
    try:
        q = M.of(tx.get("quantity") or 0)
        sold = M.of(GainRow(e).quantity or 0)
    except (TypeError, ValueError, InvalidOperation):
        return None
    if q >= 0 or abs(q) != abs(sold):
        return None
    return {"symbol": e.get("symbol"), "qty": float(abs(sold)), "direction": "LONG",
            "proceeds": tx.get("net_amount") or 0.0, "cost": 0.0, "gain": 0.0,
            "disallowed_amount": 0.0, "permanently_disallowed": 0.0,
            "commission": tx.get("commission") or 0.0,
            "fee": tx.get("fee") or 0.0, "_crypto": e.get("_crypto")}


def _option_text(symbol: str) -> str:
    """"QZRX $30 call exp 2025-02-21" from an OCC symbol (a futures
    option's root without its prefix)."""
    under = strip_listing_suffix(_unprefixed(parse_option_underlying(symbol) or symbol))
    strike = parse_option_strike(symbol)
    right = {"C": "call", "P": "put"}.get(parse_option_right(symbol) or "", "option")
    if strike is None:
        price = ""
    else:
        price = f"${strike:,.2f}"
        if price.endswith(".00"):
            price = price[:-3]
    return " ".join(p for p in (under, price, right, "exp",
                                parse_option_expiry(symbol) or "") if p)


def _unprefixed(symbol: str) -> str:
    for p in FUTURES_PREFIXES:
        if symbol.startswith(p):
            return symbol[len(p):]
    return symbol


def describe(e: Dict[str, Any]) -> str:
    """The Description cell: shares and fund units their TICKER, the
    symbol without its listing suffix (lib/markets strip_listing_suffix);
    an option from its contract, " (written)" on a written option's row;
    a future "QZM26 future"; a coin "QZC (crypto)"."""
    symbol = str(e.get("symbol") or "?")
    pclass = TF.property_class(e)
    if pclass == "crypto":
        return f"{strip_listing_suffix(symbol)} (crypto)"
    if is_option_symbol(symbol) or e.get("is_option"):
        text = _option_text(symbol) if is_option_symbol(symbol) else symbol
        if pclass == "futures":
            text += " (option on a future)"
        if (e.get("direction") or "LONG") == "SHORT":
            text += " (written)"
        return text
    if pclass == "futures":
        return f"{strip_listing_suffix(_unprefixed(symbol))} future"
    return strip_listing_suffix(symbol)


def _money_text(x: Any) -> str:
    return fmt_money(x)


def _rate_text(rate: Optional[Decimal]) -> str:
    return "" if rate is None else f"{rate:.{RATE_PLACES}f}"


def own_trade(e: Dict[str, Any]) -> bool:
    """True when the row's proceeds came from its own trade: a sale, or a
    write under grant timing. A short cover's (and a close-timing write's)
    came from the opening sales; a future's P/L from both legs. Only such a
    row's parsed native transaction is its native record (cli/gains_filing.py
    `_gains_cra_native`), and only its FX is the rate table's."""
    if TF.property_class(e) == "futures" and is_plain_future(str(e.get("symbol") or "")):
        return False
    return (e.get("direction") or "LONG") != "SHORT" or bool(e.get("grant"))


def _weighted(e: Dict[str, Any], d: Dict[str, Any], native: Dict[str, Any],
              nd: Dict[str, Any]) -> Optional[Decimal]:
    """CAD ÷ native at RATE_PLACES: of the proceeds, or of a future's P/L
    (both legs' P/L, before any denial); None when nothing was received."""
    if d["property"] == "futures" and is_plain_future(d["symbol"]):
        cad, nat = (GainRow(e).raw_gain if "raw_gain" in e else GainRow(e).gain,
                    GainRow(native).raw_gain if "raw_gain" in native
                    else GainRow(native).gain)
    else:
        cad, nat = d["proceeds"], nd["proceeds"]
    try:
        c, n = M.of(cad or 0), M.of(nat or 0)
    except (TypeError, ValueError, InvalidOperation):
        return None
    if not c or not n or (c > 0) != (n > 0):
        return None
    return M.present(c / n, RATE_PLACES)


# A note is (kind, text). The JSON keeps every row's notes and their kinds;
# the text shares one mark among the rows of a table whose note has the same
# meaning and no figure of its own (_SHARED its one sentence; _SHARED_AS_IS
# the row's own, the same on every row of a table: one currency), and
# leaves out what a cell already says (_UNSHOWN: "—" in FX is "nothing was
# received").
Note = Tuple[str, str]
_SHARED = {
    "counted_at_write": "a buy-back of an option written this year: Schedule 3 counts its "
                        "units once, at the write",
    "future": "a future: its settled P/L (a gain as proceeds, a loss as ACB), not the "
              "notional",
    "short_sale": "a short sale: Proceeds is what it brought in, ACB what covering it cost",
}
_SHARED_AS_IS = frozenset(("weighted_fx", "futures_fx"))
_UNSHOWN = frozenset(("nothing_received",))
# A denial: one mark per kind in a table, its sentence naming the table's
# rows and total (`_denial_sentence`); each row's own amount is in
# --details under it, the CSV's Denied column and the JSON.
_DENIAL_KINDS = ("denied", "denied_permanent", "denied_contribution")


def _denial_notes(d: Dict[str, Any], base: str) -> List[Note]:
    notes: List[Note] = []
    perm = M.of(d["perm_denied"])
    defer = M.of(d["denied"]) - perm
    if defer > M.of(TF._EPS):
        notes.append(("denied", f"superficial loss {fmt_money(defer)} {base} denied: the "
                     f"ACB is reduced by it, and it is added to the replacement's ACB"))
    if perm > M.of(TF._EPS):
        notes.append(("denied_permanent", f"superficial loss {fmt_money(perm)} {base} "
                     f"denied for good (a registered-account or affiliated buy-back): the "
                     f"ACB is reduced by it"))
    if M.of(d["denied_contrib"]) > M.of(TF._EPS):
        notes.append(("denied_contribution", f"loss {fmt_money(d['denied_contrib'])} "
                     f"{base} on an in-kind contribution to a registered plan, denied for "
                     f"good: the ACB is reduced by it"))
    return notes


def _fx_cells(e: Dict[str, Any], d: Dict[str, Any], nat: Native, proceeds: float,
              base: str, year: Optional[int], date_key: str
              ) -> Tuple[Optional[float], Optional[Decimal], str, List[Note], str]:
    """(native proceeds, FX, how the FX was found, notes, why there is no
    FX: '' when there is one) of a foreign row."""
    cur = nat.currency or "?"
    if nat.row is None:
        return None, None, "", [("no_native_record",
                                 f"no {cur} record of this trade in work/ "
                                 f"({nat.why or 'none found by its id'}): no native "
                                 f"proceeds or FX")], f"no {cur} record in work/"
    nd = TF.schedule3_disposition(nat.row, year, date_key)
    native = TF._cents(nd["proceeds"])
    notes: List[Note] = []
    why = ""
    if own_trade(e):
        rate, how = nat.rate, "rate_table"
        if rate is None:
            why = f"no {cur} rate in work/to_base.csv"
            notes.append(("no_rate", f"no {cur} rate for {nat.rate_date or '?'} in "
                         f"work/to_base.csv: no FX"))
    else:
        rate, how = _weighted(e, d, nat.row, nd), "weighted"
        if rate is None:
            why = "nothing received"
            notes.append(("nothing_received", f"no FX: nothing was received ({cur} "
                         f"proceeds {fmt_money(native)})"))
        elif d["property"] == "futures":
            notes.append(("futures_fx", f"FX: the P/L's rate ({base} P/L ÷ {cur} P/L, "
                         f"{RATE_PLACES} places)"))
        else:
            notes.append(("weighted_fx", f"FX: the rate of the opening sale(s) or write, "
                         f"proceeds-weighted ({base} ÷ {cur}, {RATE_PLACES} places)"))
    if rate is not None:
        exact = M.of(nd["proceeds"]) * rate
        allow = _CENT + (abs(M.of(nd["proceeds"])) * _HALF_UNIT if how == "weighted"
                         else Decimal(0))
        if abs(exact - M.of(proceeds)) > allow:
            notes.append(("fx_check", f"{fmt_money(native)} {cur} × {_rate_text(rate)} = "
                         f"{fmt_money(M.present(exact, 2))}, not the Proceeds ({base}) "
                         f"{fmt_money(proceeds)}"))
    return native, rate, how if rate is not None else "", notes, why


def _row(e: Dict[str, Any], nat: Native, base: str, year: Optional[int],
         date_key: str) -> Dict[str, Any]:
    """One disposition's row: its Schedule 3 cells footed alone, its
    native proceeds and FX, its description and notes."""
    c = TF.disposition_cells(e, year, date_key)
    d = c["d"]
    proceeds = c["proceeds"]
    spec = TF.schedule3_line(d["line_key"], year, 2)
    cur = nat.currency or "?"
    notes = _denial_notes(d, base)
    native: Optional[float] = None
    rate: Optional[Decimal] = None
    how = fx_why = ""
    if cur != base:
        native, rate, how, fx_notes, fx_why = _fx_cells(e, d, nat, proceeds, base, year,
                                                         date_key)
        notes += fx_notes
    if d["property"] == "futures" and is_plain_future(d["symbol"]):
        notes.append(("future", _SHARED["future"]))
    elif d["short"] and not TF.property_class(e) == "option":
        notes.append(("short_sale", _SHARED["short_sale"]))
    if d["position"]:
        notes.append(("filing_position", d["position"]["note"]))
    g = GainRow(e)
    return {
        "account": str(e.get("account") or ""), "id": str(e.get("id") or ""),
        "symbol": d["symbol"], "date": str(e.get("date") or ""),
        "date_settle": str(e.get("date_settle") or ""),
        "description": describe(e), "property": d["property"],
        "line_key": spec["key"], "proceeds_line": spec["proceeds_code"],
        "gain_line": spec["gain_code"],
        "qty": M.units(abs(g.quantity or 0.0), 8), "units_counted_elsewhere": 0.0,
        "short_close_units": d["short_close_units"], "grant": d["grant"],
        "written": d["short"] and TF.property_class(e) == "option",
        "currency": cur,
        "native_proceeds": native,
        "fx_rate": None if rate is None else float(rate),
        "fx_source": how or None,
        # The day of the rate used (a weekend's: the Friday before) and the
        # day the row converts on (its settlement date), for a rate-table FX.
        "fx_date": (nat.used_date or None) if how == "rate_table" else None,
        "fx_rate_date": (nat.rate_date or None) if cur != base and own_trade(e) else None,
        "native_source": nat.source,
        "proceeds": proceeds, "acb": c["acb"], "outlays": c["outlays"], "gain": c["gain"],
        "denied": c["denied"], "permanently_denied": c["perm_denied"],
        "denied_contribution": c["denied_contrib"],
        "notes": [t for _k, t in notes], "note_kinds": [k for k, _t in notes],
        "_fx_why": fx_why,
    }


def _note_units(rows: List[Dict[str, Any]]) -> None:
    """Mark the buy-backs whose units Schedule 3 counts with the same
    year's write (grant timing: the contracts were disposed of once):
    per line and security, as build_schedule3 nets them, each buy-back's
    units up to what the writes left (in date order), so the Qty column
    less the units noted is form-export's units."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["line_key"], r["symbol"]), []).append(r)
    for grp in groups.values():
        left = sum(r["qty"] for r in grp if r["grant"])
        for r in grp:
            if r["grant"] or r["short_close_units"] <= 0 or left <= 0:
                continue
            n = min(r["short_close_units"], left, r["qty"])
            left -= n
            r["units_counted_elsewhere"] = M.units(n, 8)
            r["notes"].append(f"{fmt_qty(n)} of its units {'is' if n == 1 else 'are'} counted "
                              f"once, at the write (Schedule 3's units)")
            r["note_kinds"].append("counted_at_write")


def _sum_cells(rows: Sequence[Dict[str, Any]], keys: Sequence[str]) -> Dict[str, float]:
    """The cells added exactly (each is cents already), as JSON floats."""
    return {k: M.json_money(M.exact_sum(r[k] for r in rows), 2) for k in keys}


def _distinct_tickers(rows: List[Dict[str, Any]]) -> None:
    """Where two different symbols among the report's rows show one
    ticker (a share listed on two markets, `DISTINCT` in ticker.map), the
    Description of those rows is the full symbol ("QZDD.TO", "QZDD.US"),
    in every table: one security reads the same everywhere."""
    def ticker(r: Dict[str, Any]) -> Optional[str]:
        shown = str(r["description"])
        return shown if shown == strip_listing_suffix(str(r["symbol"])) else None
    symbols: Dict[str, Set[str]] = {}
    for r in rows:
        t = ticker(r)
        if t is not None:
            symbols.setdefault(t, set()).add(r["symbol"])
    for r in rows:
        t = ticker(r)
        if t is not None and len(symbols[t]) > 1:
            r["description"] = r["symbol"]


def report(entries: List[Dict[str, Any]], natives: List[Native], *, year: Optional[int],
           date_key: str, base: str, account: Optional[str] = None) -> Dict[str, Any]:
    """The `--json` document (before its readiness record): `scope`
    ({"account": ACCOUNT}, or None for every taxable account), `rows` (one
    per disposition, sorted by sale date, account, symbol), `tables` (one
    per native currency: its rows' cells and denied losses added, the base
    currency's first; a foreign one's native total, the rows it leaves out
    and the rows with no FX, by why), `rows_total` (every row's cells and
    denied losses added), and Schedule 3's own `lines` (build_schedule3
    over the same entries: the
    figures `tjs form-export` and `tjs sum` show, each with what its rows
    add to) with `totals` the lines added as `tjs sum`'s RETURN row adds
    them (with an ACCOUNT, `tjs sum ACCOUNT`'s)."""
    rows = [_row(e, n, base, year, date_key) for e, n in zip(entries, natives)]
    rows.sort(key=lambda r: (r["date"], r["account"], r["symbol"], r["id"]))
    _note_units(rows)
    _distinct_tickers(rows)
    tables = []
    for cur in sorted({r["currency"] for r in rows}, key=lambda c: (c != base, c)):
        trows = [r for r in rows if r["currency"] == cur]
        t = {"currency": cur, "rows": len(trows), **_sum_cells(trows, _CELLS + _DENIED)}
        if cur != base:
            t["native_proceeds"] = M.json_money(M.exact_sum(
                r["native_proceeds"] for r in trows if r["native_proceeds"] is not None), 2)
            t["native_missing"] = sum(1 for r in trows if r["native_proceeds"] is None)
            why: Dict[str, int] = {}
            for r in trows:
                if r["fx_rate"] is None:
                    k = r["_fx_why"] or "no FX"
                    why[k] = why.get(k, 0) + 1
            t["fx_missing"] = sum(why.values())
            t["fx_missing_why"] = dict(sorted(why.items()))
        tables.append(t)
    for r in rows:
        del r["short_close_units"], r["_fx_why"]
    rep = TF.build_schedule3(entries, year, date_key)
    lines = []
    for ln in rep["lines"]:
        lrows = [r for r in rows if r["line_key"] == ln["key"]]
        sums = _sum_cells(lrows, _CELLS + ("denied",))
        units = M.units(sum(r["qty"] - r["units_counted_elsewhere"] for r in lrows), 8)
        lines.append({**{k: ln[k] for k in ("key", "line", "title", "proceeds_code",
                                            "gain_code", "period", "dispositions")},
                      **{k: ln[k] for k in _CELLS + ("denied",)},
                      "units": units, "rows_sum": sums})
    totals = {k: M.shown(sum(ln[k] for ln in rep["lines"]), 2) + 0.0
              for k in _CELLS + ("denied",)}
    return {"form": "schedule3", "year": year, "currency": base, "date_basis": date_key,
            "scope": {"account": account} if account else None,
            "rows": rows, "tables": tables,
            "rows_total": {"rows": len(rows), **_sum_cells(rows, _CELLS + _DENIED)},
            "lines": lines, "totals": totals}


def _denied_amount(r: Dict[str, Any], kind: str) -> Decimal:
    """A row's denied loss of one kind (`_DENIAL_KINDS`): the deferred
    part (`denied` less `permanently_denied`), the permanent part, or the
    in-kind contribution's."""
    perm = M.of(r["permanently_denied"])
    if kind == "denied":
        return M.of(r["denied"]) - perm
    if kind == "denied_permanent":
        return perm
    return M.of(r["denied_contribution"])


def denied_total(r: Dict[str, Any]) -> Decimal:
    """A row's denied losses, every kind added (the CSV's Denied)."""
    return M.exact_sum((r["denied"], r["denied_contribution"]))


def _denial_sentence(kind: str, rows: List[Dict[str, Any]], base: str) -> str:
    """A table's one note for a kind of denial: its rows and total."""
    total = fmt_money(M.exact_sum(_denied_amount(r, kind) for r in rows))
    count = (f"1 row, {total} {base}" if len(rows) == 1
             else f"{len(rows)} rows, {total} {base} in all")
    if kind == "denied":
        return (f"superficial loss denied ({count}): each row's ACB is reduced by its "
                f"denied loss, so Gain/Loss is the allowed part; the denied loss is added "
                f"to the replacement's ACB")
    if kind == "denied_permanent":
        return (f"superficial loss denied for good ({count}; a registered-account or "
                f"affiliated buy-back): each row's ACB is reduced by it")
    return (f"loss on an in-kind contribution to a registered plan, denied for good "
            f"({count}): each row's ACB is reduced by it")


def _shown_note(r: Dict[str, Any], kind: str, text: str,
                denials: Dict[str, str]) -> Tuple[str, bool]:
    """A note as the text shows it, and whether rows share it: a denial's
    table sentence (`denials`, by kind), a shared meaning's one sentence
    (_SHARED; a buy-back's only when Schedule 3 counts all its units at
    the write, else its own count), else the row's own; ("", False) for
    a note a cell already says (_UNSHOWN)."""
    if kind in _UNSHOWN:
        return "", False
    if kind in denials:
        return denials[kind], True
    if kind in _SHARED and (kind != "counted_at_write"
                            or r["units_counted_elsewhere"] == r["qty"]):
        return _SHARED[kind], True
    return text, kind in _SHARED_AS_IS


# A note under a table: its mark, its row (None: shared), its text, and
# the lines --details lists under it (a denial's rows and amounts).
NoteLine = Tuple[int, Optional[Dict[str, Any]], str, List[str]]


def _marks(rows: List[Dict[str, Any]], start: int, base: str
           ) -> Tuple[List[str], List[NoteLine]]:
    """Each row's footnote marks ("[1,2]", in order) and the notes they
    number: a row's own note its own number; a shared one (`_shown_note`)
    the number it got at its first row in this table, its note once (no
    row); a denial's with its rows' own amounts ("2025-05-05 QZLS: 100.00
    CAD") for --details."""
    marks: List[str] = []
    notes: List[NoteLine] = []
    shared: Dict[str, int] = {}
    denials = {k: _denial_sentence(k, krows, base) for k in _DENIAL_KINDS
               for krows in [[r for r in rows if k in r["note_kinds"]]] if krows}
    n = start
    for r in rows:
        ids: List[str] = []
        for kind, text in zip(r["note_kinds"], r["notes"]):
            shown, is_shared = _shown_note(r, kind, text, denials)
            if not shown:
                continue
            if is_shared and shown in shared:
                mark = shared[shown]
            else:
                n += 1
                mark = n
                subs = ([f"{x['date']} {x['description']}: "
                         f"{fmt_money(_denied_amount(x, kind))} {base}"
                         for x in rows if kind in x["note_kinds"]]
                        if kind in denials else [])
                notes.append((n, None if is_shared else r, shown, subs))
                if is_shared:
                    shared[shown] = n
            if str(mark) not in ids:
                ids.append(str(mark))
        marks.append(f" [{','.join(sorted(ids, key=int))}]" if ids else "")
    return marks, notes


def _desc_cells(descs: List[str], marks: List[str], room: Optional[int]) -> List[str]:
    """The Description cells: each with its marks, cut with an ellipsis
    to `room` columns (None: whole)."""
    out = []
    for d, m in zip(descs, marks):
        if room is not None and len(d) + len(m) > room:
            keep = max(1, room - len(m) - 1)
            d = d[:keep].rstrip() + "…"
        out.append(d + m)
    return out


def _table_lines(rows: List[Dict[str, Any]], table: Dict[str, Any], base: str,
                 marks: List[str], width: int) -> List[str]:
    """One currency's table, its Description cut to fit `width` (0: whole):
    a money column is never dropped."""
    foreign = table["currency"] != base
    cur = table["currency"]
    heads = ["Sale date", "Settle date", "Description", "Qty"]
    if foreign:
        heads += [f"Proceeds\n({cur})", "FX"]
    heads += [f"Proceeds\n({base})", f"ACB\n({base})", f"Commissions\n({base})",
              f"Gain/Loss\n({base})"]
    aligns = ["<", "<", "<", ">"] + ([">", ">"] if foreign else []) + [">"] * 4

    def body(descs: List[str]) -> List[List[str]]:
        out = []
        for r, desc in zip(rows, descs):
            cells = [r["date"], r["date_settle"], desc, fmt_qty(r["qty"])]
            if foreign:
                cells += [_NONE if r["native_proceeds"] is None
                          else _money_text(r["native_proceeds"]),
                          _NONE if r["fx_rate"] is None else _rate_text(M.of(r["fx_rate"]))]
            cells += [_money_text(r[k]) for k in _CELLS]
            out.append(cells)
        return out

    left_out = table.get("native_missing") or 0
    # The native total adds the rows that have one: how many it leaves out.
    foot = [["Subtotal", "",
             f"({cur}: {len(rows) - left_out} of {len(rows)} rows)" if left_out else "", ""]
            + ([_money_text(table["native_proceeds"]), ""] if foreign else [])
            + [_money_text(table[k]) for k in _CELLS]]
    # A broker's name, shown to a person: its control characters escaped
    # (fit_table escapes nothing at width 0).
    descs = [_out.printable(r["description"]) for r in rows]
    whole = _out.fit_table(heads, body(_desc_cells(descs, marks, None)), aligns=aligns,
                           foot=foot, width_=0)
    over = max(map(len, whole), default=0) - width
    if width <= 0 or over <= 0:
        return whole
    desc_w = max(len(c) for c in _desc_cells(descs, marks, None))
    room = max(_MIN_DESC, desc_w - over)
    return _out.fit_table(heads, body(_desc_cells(descs, marks, room)), aligns=aligns,
                          foot=foot, width_=0)


def _note_lines(notes: List[NoteLine], details: bool, width: int) -> List[str]:
    """The notes under a table, by mark, each wrapped to `width` under its
    text (`details`: a row's own with its date and description, and a
    denial's rows with their amounts, a `- ` item each)."""
    out: List[str] = []
    for n, r, text, subs in notes:
        mark = f"[{n}]  "
        hang = " " * len(mark)
        out += _out.wrap((f"{r['date']} {r['description']}: " if details and r else "")
                         + text, width, indent=mark, hang=hang)
        for sub in subs if details else []:
            out += _out.wrap(sub, width, indent=hang + "- ", hang=hang + "  ")
    return out


def _fx_missing_line(table: Dict[str, Any]) -> str:
    """Why a foreign table's FX column is empty on every row ('' when a
    row has one)."""
    if not table.get("fx_missing") or table["fx_missing"] < table["rows"]:
        return ""
    why = table["fx_missing_why"]
    return (f"No row of this table has an FX ({_NONE}): "
            + "; ".join(f"{n} {'row' if n == 1 else 'rows'}: {w}" for w, n in why.items()) + ".")


def _lines_table(doc: Dict[str, Any], base: str) -> List[str]:
    body = [[ln["title"], _money_text(ln["proceeds"]), _money_text(ln["acb"]),
             _money_text(ln["outlays"]), _money_text(ln["gain"])] for ln in doc["lines"]]
    rt = doc["totals"]
    foot = [["Total", *(_money_text(rt[k]) for k in _CELLS)]]
    return _out.fit_table(["SCHEDULE 3 LINE", f"Proceeds\n({base})", f"ACB\n({base})",
                           f"Commissions\n({base})", f"Gain/Loss\n({base})"], body,
                          aligns=["<", ">", ">", ">", ">"], foot=foot)


_NAMES = {"proceeds": "Proceeds", "acb": "ACB", "outlays": "Commissions", "gain": "Gain/Loss"}


def _rounding_lines(doc: Dict[str, Any], base: str, details: bool) -> List[str]:
    """Each line whose rows add to other cents than Schedule 3's figure
    (it rounds each security's total; the rows round each sale): by how
    much, per column (`details`: both figures)."""
    out = []
    for ln in doc["lines"]:
        rs = ln["rows_sum"]
        ks = [k for k in _CELLS if M.cents_apart(ln[k], rs[k])]
        if not ks:
            continue
        if details:
            what = "add to " + ", ".join(f"{_NAMES[k]} {_money_text(rs[k])} (the line "
                                         f"{_money_text(ln[k])})" for k in ks)
        else:
            what = "differ from the line by " + ", ".join(
                f"{_NAMES[k]} {M.present(M.of(rs[k]) - M.of(ln[k]), 2):+,.2f}" for k in ks)
        out.append(f"{ln['title']}: the rows {what} {base}: Schedule 3 rounds each "
                   f"security's total, the rows each sale.")
    return out

def render(doc: Dict[str, Any], *, details: bool, width: int,
           tail: Sequence[str] = (), manual: Sequence[str] = ()) -> "_out.Doc":
    """The text report: a table per currency (the base currency's first),
    its notes under it, the tables' total, Schedule 3's lines and their
    total, the unknown-cost sales (`manual`: form-export's block), then
    `tail` (the `! ` lines) and the pointer to the long form."""
    base, year = doc["currency"], doc["year"]
    acct = (doc.get("scope") or {}).get("account")
    d = _out.Doc(f"CAPITAL GAINS, ONE ROW PER SALE — {base}, tax year {year}"
                 + (f", account {acct}" if acct else "")
                 + ", Schedule 3's cells per disposition")
    if not doc["rows"]:
        d.line(f"No dispositions in {f'account {acct}' if acct else 'the taxable accounts'} "
               f"in tax year {year}.")
    else:
        d.legend("Proceeds − ACB − Commissions = Gain/Loss on each row; FX: the Bank "
                 "of Canada rate that converted the proceeds.")
    start = 0
    for t in doc["tables"]:
        rows = [r for r in doc["rows"] if r["currency"] == t["currency"]]
        marks, notes = _marks(rows, start, base)
        start += len(notes)
        d.section(f"SALES IN {t['currency']}")
        d.rows(_table_lines(rows, t, base, marks, 0 if details else width))
        if _fx_missing_line(t):
            d.blank()
            d.para(_fx_missing_line(t))
        if notes:
            d.blank()
            d.rows(_note_lines(notes, details, width))
    if len(doc["tables"]) > 1:
        d.section(f"ALL SALES — {base}")
        body = [[f"Sales in {t['currency']}", str(t["rows"]),
                 *(_money_text(t[k]) for k in _CELLS)] for t in doc["tables"]]
        rt = doc["rows_total"]
        d.rows(_out.fit_table(["TABLE", "ROWS", f"Proceeds\n({base})", f"ACB\n({base})",
                               f"Commissions\n({base})", f"Gain/Loss\n({base})"], body,
                              aligns=["<", ">", ">", ">", ">", ">"],
                              foot=[["Total", str(rt["rows"]),
                                     *(_money_text(rt[k]) for k in _CELLS)]]))
    if doc["lines"]:
        d.section(f"FOR ACCOUNT {acct} — Schedule 3, as tjs sum {acct} shows it (not the "
                  f"whole return)" if acct else
                  "FOR THE RETURN — Schedule 3, as tjs form-export and tjs sum show it")
        d.rows(_lines_table(doc, base))
        for ln in _rounding_lines(doc, base, details):
            d.para(ln)
    if manual:
        d.blank()
        d.rows(list(manual))
    if tail:
        d.blank()
        d.rows(list(tail))
    if not details and doc["rows"]:
        d.hint("tjs gains-cra --details", "whole descriptions, the rounding")
    return d


def csv_rows(doc: Dict[str, Any]) -> List[List[Any]]:
    """The `--csv` rows: CSV_COLUMNS, then a row per disposition with its
    raw numbers (the currency in a column of its own; a base-currency row
    has no native proceeds or FX; Denied every kind of denied loss added,
    empty when none) and every note in full."""
    out: List[List[Any]] = [list(CSV_COLUMNS)]
    for r in doc["rows"]:
        denied = denied_total(r)
        out.append([r["date"], r["date_settle"], r["description"], r["symbol"], r["qty"],
                    r["currency"],
                    "" if r["native_proceeds"] is None else r["native_proceeds"],
                    "" if r["fx_rate"] is None else _rate_text(M.of(r["fx_rate"])),
                    r["proceeds"], r["acb"], r["outlays"], r["gain"],
                    float(denied) if denied else "", r["account"],
                    "; ".join(r["notes"])])
    return out

