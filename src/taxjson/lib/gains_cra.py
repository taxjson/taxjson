"""`tjs gains-cra`: the tax year's dispositions one row per sale, in the
CRA's column layout: SALE DATE, SETTLE DATE, DESCRIPTION, QTY, PROCEEDS in
the trade's currency, FX, then PROCEEDS, COST(ACB), OUTLAYS and GAIN in
the base currency (CAD), named as `tjs sum` names them (one name for
one figure in every table: TJ-5341's P3-10), and DENIED (INFO): every
loss denied (the superficial loss, deferred or for good, and an in-kind
contribution's, as the CSV's Denied column adds them: `denied_all`,
TJ-5341), information only, COST(ACB) already reduced by it
(lib/gain_columns, one meaning in every report). The CSV keeps its own
header (CSV_COLUMNS). The text is always the whole of it (TJ-5337):
every column and every word, each row's own note with its date and
description, the rounding with both figures; a currency's table stacks
its headings and wraps a long description within its column to fit the
width (TJ-5342, `_table_lines`). The totals, ALL SALES and FOR THE
RETURN, are in `tjs sum`'s columns and layout (lib/gain_columns `table`,
TJ-5336), and line up with each other.

A row's four CAD cells are Schedule 3's cells for that one disposition:
lib/tax_forms `schedule3_disposition` (the part of `build_schedule3` each
disposition adds: the sale commission split out, a written option's
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

from taxjson.lib import gain_columns as GCOL
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
_CELLS = ("proceeds", "acb", "outlays", "gain")
# A row's denied losses (`denied` holds `permanently_denied`; an in-kind
# contribution's apart; `denied_all` every kind added, DENIED (INFO)'s
# figure), added per table and for every row in --json.
_DENIED = ("denied", "permanently_denied", "denied_contribution", "denied_all")
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
    "assigned_premium_fx": "FX: the shares' at the sale's rate; the premium of the call "
                           "assigned, in these proceeds, at its write's rate (CA-OPT-06)",
}
_SHARED_AS_IS = frozenset(("weighted_fx", "futures_fx"))
_UNSHOWN = frozenset(("nothing_received",))
# A denial: one mark per kind in a table, its sentence counting the
# table's rows and adding their amounts (`_denial_sentence`); each row's
# own amount, every kind added, is in its DENIED (INFO) cell, as in the
# CSV's Denied column (TJ-5341), each kind apart in the JSON.
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
            if how == "rate_table" and GainRow(e).write_outlay:
                # An assigned call's shares: the premium folded into the
                # proceeds was converted at the write's rate (its
                # commission, `write_outlay`, with it), the rest at the
                # sale's, as a bought-back write's FX is the write's.
                notes.append(("assigned_premium_fx", _SHARED["assigned_premium_fx"]))
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
        # DENIED (INFO): every kind added (the CSV's Denied, `denied_total`).
        "denied_all": M.json_money(M.exact_sum((c["denied"], c["denied_contrib"])), 2),
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
    """The cells added exactly (each is cents already), as JSON floats,
    with TOTAL COST (`total_cost`: COST(ACB) + OUTLAYS, the added cells)."""
    out = {k: M.json_money(M.exact_sum(r[k] for r in rows), 2) for k in keys}
    return _with_total_cost(out)


def _with_total_cost(t: Dict[str, Any]) -> Dict[str, Any]:
    """`t` with `total_cost` after its `outlays`: `acb` + `outlays` as
    shown, as `tjs sum` adds it (cli/estimate `_return_figures`)."""
    out: Dict[str, Any] = {}
    for k, v in t.items():
        out[k] = v
        if k == "outlays":
            out["total_cost"] = M.json_money(M.of(t["acb"]) + M.of(v), 2)
    return out


def _line_denied_all(ln: Dict[str, Any]) -> float:
    """A Schedule 3 line's DENIED (INFO), `denied_all`: its superficial
    losses denied and its in-kind contributions' (build_schedule3's
    `denied` and `denied_contribution`) added, as `tjs sum` adds them
    (cli/estimate `_return_figures`)."""
    return M.json_money(M.exact_sum((ln["denied"], ln.get("denied_contribution") or 0)), 2)


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
           date_key: str, base: str, account: Optional[str] = None,
           account_names: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The `--json` document (before its readiness record): `scope`
    ({"account": ACCOUNT, the text given, "account_names": its names in
    the order given, lib/account_list; `account_names` defaults to
    [ACCOUNT]}, or None for every taxable account), `rows` (one
    per disposition, sorted by sale date, account, symbol), `tables` (one
    per native currency: its rows' cells and denied losses added, the base
    currency's first; a foreign one's native total, the rows it leaves out
    and the rows with no FX, by why), `rows_total` (every row's cells and
    denied losses added), and Schedule 3's own `lines` (build_schedule3
    over the same entries: the
    figures `tjs form-export` and `tjs sum` show, each with what its rows
    add to, and its `short` words, sum's label's) with `totals` the lines
    added as `tjs sum`'s RETURN row adds them (with an ACCOUNT, `tjs sum
    ACCOUNT`'s). Every total and line has `total_cost` (`acb` +
    `outlays`, TOTAL COST), and every row, table, line (its `rows_sum`
    too) and total `denied_all`, DENIED (INFO): every kind of denied
    loss added (TJ-5341), beside `denied`, the superficial losses."""
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
        sums = _sum_cells(lrows, _CELLS + ("denied", "denied_all"))
        units = M.units(sum(r["qty"] - r["units_counted_elsewhere"] for r in lrows), 8)
        lines.append({**{k: ln[k] for k in ("key", "line", "title", "short", "proceeds_code",
                                            "gain_code", "period", "dispositions")},
                      **_with_total_cost({k: ln[k] for k in _CELLS + ("denied",)}),
                      "denied_all": _line_denied_all(ln),
                      "units": units, "rows_sum": sums})
    totals = _with_total_cost({k: M.shown(sum(ln[k] for ln in rep["lines"]), 2) + 0.0
                               for k in _CELLS + ("denied",)})
    # The lines' DENIED (INFO) added, as `tjs sum`'s RETURN row adds them.
    totals["denied_all"] = M.json_money(M.exact_sum(ln["denied_all"] for ln in lines), 2)
    scope = ({"account": account,
              "account_names": [str(n) for n in (account_names or (account,))]}
             if account else None)
    return {"form": "schedule3", "year": year, "currency": base, "date_basis": date_key,
            "scope": scope,
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
    """A row's denied losses, every kind added (the CSV's Denied; its
    DENIED (INFO) cell and `denied_all`, the same figure)."""
    return M.exact_sum((r["denied"], r["denied_contribution"]))


def _denial_sentence(kind: str, rows: List[Dict[str, Any]], base: str) -> str:
    """A table's one note for a kind of denial: its rows and total."""
    total = fmt_money(M.exact_sum(_denied_amount(r, kind) for r in rows))
    count = (f"1 row, {total} {base}" if len(rows) == 1
             else f"{len(rows)} rows, {total} {base} in all")
    if kind == "denied":
        return (f"superficial loss denied ({count}): each row's COST(ACB) is reduced by its "
                f"denied loss, so GAIN is the allowed part; the denied loss is added "
                f"to the replacement's ACB")
    if kind == "denied_permanent":
        return (f"superficial loss denied for good ({count}; a registered-account or "
                f"affiliated buy-back): each row's COST(ACB) is reduced by it")
    return (f"loss on an in-kind contribution to a registered plan, denied for good "
            f"({count}; s.40(2)(g)(iv)): each row's COST(ACB) is reduced by it")


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


# A note under a table: its mark, its row (None: shared) and its text.
NoteLine = Tuple[int, Optional[Dict[str, Any]], str]


def _marks(rows: List[Dict[str, Any]], start: int, base: str
           ) -> Tuple[List[str], List[NoteLine]]:
    """Each row's footnote marks ("[1,2]", in order) and the notes they
    number: a row's own note its own number; a shared one (`_shown_note`)
    the number it got at its first row in this table, its note once (no
    row): a denial's one per kind, its rows counted and added."""
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
                notes.append((n, None if is_shared else r, shown))
                if is_shared:
                    shared[shown] = n
            if str(mark) not in ids:
                ids.append(str(mark))
        marks.append(f" [{','.join(sorted(ids, key=int))}]" if ids else "")
    return marks, notes


# A currency table's columns (TJ-5342): each heading on up to three lines,
# bottom-aligned, and its one-line name (a record's label). The money
# columns in `tjs sum`'s names (lib/gain_columns HEADS, TOTAL COST aside):
# one name for one figure in every table (TJ-5341's P3-10).
def _heads(cur: str, base: str, foreign: bool) -> Tuple[List[str], List[str]]:
    stacked: List[Tuple[str, ...]] = [("SALE", "DATE"), ("SETTLE", "DATE"), ("DESCRIPTION",),
                                      ("QTY",)]
    if foreign:
        stacked += [("PROCEEDS", f"({cur})"), ("FX",)]
    stacked += [("PROCEEDS", f"({base})"), ("COST(ACB)", f"({base})"),
                ("OUTLAYS", f"({base})"), ("GAIN", f"({base})"),
                ("DENIED", "(INFO)", f"({base})")]
    depth = max(map(len, stacked))
    heads = ["\n".join(("",) * (depth - len(h)) + h) for h in stacked]
    names = [" ".join(h) for h in stacked]
    return heads, names


_DESC = 2                     # the DESCRIPTION column
_DESC_MIN = len("DESCRIPTION")


def _wrapped(text: str, mark: str, room: Optional[int]) -> List[str]:
    """A Description's lines within `room` (None: one line), at word
    boundaries, the marks on the first line beside the figures; a word
    longer than the room stays whole."""
    if room is None or len(text) + len(mark) <= room:
        return [text + mark]
    out: List[str] = []
    cur, limit = "", room - len(mark)
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > limit:
            out.append(cur)
            cur, limit = word, room
        else:
            cur = f"{cur} {word}" if cur else word
    out.append(cur)
    out[0] += mark
    return out


def _table_lines(rows: List[Dict[str, Any]], table: Dict[str, Any], base: str,
                 marks: List[str], width: int) -> List[str]:
    """One currency's table, every column and every word at any width
    (TJ-5342): the headings on up to three lines; too wide, each
    Description wraps at word boundaries within its column (never under
    its heading's width), its continuation lines holding it alone, its
    marks on the first line; narrower still, one record per row. Width 0:
    whole and unwrapped. DENIED (INFO) is a row's losses denied, every
    kind added (`denied_all`, lib/gain_columns: the CSV's Denied, TJ-5341),
    empty when it has none; the subtotal always shows its figure. The
    money columns in `tjs sum`'s names (`_heads`), the CRA's columns only:
    no TOTAL COST."""
    foreign = table["currency"] != base
    cur = table["currency"]
    heads, names = _heads(cur, base, foreign)
    aligns = ["<", "<", "<", ">"] + ([">", ">"] if foreign else []) + [">"] * 5
    # A broker's name, shown to a person: its control characters escaped
    # (fit_table escapes nothing at width 0).
    lead = []
    for r, mark in zip(rows, marks):
        cells = [r["date"], r["date_settle"], _out.printable(r["description"]), fmt_qty(r["qty"])]
        if foreign:
            cells += [_NONE if r["native_proceeds"] is None
                      else _money_text(r["native_proceeds"]),
                      _NONE if r["fx_rate"] is None else _rate_text(M.of(r["fx_rate"]))]
        cells += [_money_text(r[k]) for k in _CELLS]
        cells.append(_money_text(r["denied_all"]) if M.of(r["denied_all"]) else "")
        lead.append((cells, mark))
    left_out = table.get("native_missing") or 0
    # The native total adds the rows that have one: how many it leaves out.
    foot = (["SUBTOTAL", "",
             f"({cur}: {len(rows) - left_out} of {len(rows)} rows)" if left_out else "", ""]
            + ([_money_text(table["native_proceeds"]), ""] if foreign else [])
            + [_money_text(table[k]) for k in _CELLS + ("denied_all",)])

    def lines(room: Optional[int]) -> List[str]:
        def rows_of(cells: List[str], mark: str) -> List[List[str]]:
            parts = _wrapped(cells[_DESC], mark, room)
            first = cells[:_DESC] + [parts[0]] + cells[_DESC + 1:]
            return [first] + [["", "", p] + [""] * (len(cells) - 3) for p in parts[1:]]
        body = [ln for cells, mark in lead for ln in rows_of(cells, mark)]
        return _out.fit_table(heads, body, aligns=aligns, foot=rows_of(foot, ""), width_=0)

    full = lines(None)
    wide = max(map(len, full))
    if width <= 0 or wide <= width:
        return full
    desc = max([_DESC_MIN, len(foot[_DESC])] + [len(c[_DESC] + m) for c, m in lead])
    fitted = lines(max(_DESC_MIN, width - (wide - desc)))
    if max(map(len, fitted)) <= width:
        return fitted
    return _out.records(names, [c[:_DESC] + [c[_DESC] + m] + c[_DESC + 1:] for c, m in lead]
                        + [foot], width, key=(0, _DESC))


def _note_lines(notes: List[NoteLine], width: int) -> List[str]:
    """The notes under a table, by mark, each wrapped to `width` under its
    text, a row's own with its date and description."""
    out: List[str] = []
    for n, r, text in notes:
        mark = f"[{n}]  "
        out += _out.wrap((f"{r['date']} {_out.printable(r['description'])}: " if r else "")
                         + text, width, indent=mark, hang=" " * len(mark))
    return out


def _fx_missing_line(table: Dict[str, Any]) -> str:
    """Why a foreign table's FX column is empty on every row ('' when a
    row has one)."""
    if not table.get("fx_missing") or table["fx_missing"] < table["rows"]:
        return ""
    why = table["fx_missing_why"]
    return (f"No row of this table has an FX ({_NONE}): "
            + "; ".join(f"{n} {'row' if n == 1 else 'rows'}: {w}" for w, n in why.items()) + ".")


_ALL_HEADS = ("TABLE", "ROWS")


def _all_cells(doc: Dict[str, Any]) -> Tuple[List[List[str]], List[List[str]]]:
    """ALL SALES' left cells: a row per currency's table, then TOTAL."""
    return ([[f"Sales in {t['currency']}", str(t["rows"])] for t in doc["tables"]],
            [["TOTAL", str(doc["rows_total"]["rows"])]])


def _all_sales(doc: Dict[str, Any], width: int) -> List[str]:
    """ALL SALES: each currency's table added, then every row's, in `tjs
    sum`'s columns and layout (lib/gain_columns `table`), every column;
    FOR THE RETURN's figures among its references, as its among FOR THE
    RETURN's (`render`), so the two line up."""
    rt = doc["rows_total"]
    lines, _layout = GCOL.table(
        list(_ALL_HEADS), lambda _labels: [_all_cells(doc)],
        doc["tables"], [rt], width=width, lines=doc["lines"], total=doc["totals"],
        keep_denied=True, aligns=["<", ">"])
    return lines


# A line's columns by FOR THE RETURN's names (lib/gain_columns HEADS).
_NAMES = dict(zip(GCOL.KEYS, GCOL.HEADS))


def _rounding_lines(doc: Dict[str, Any], base: str) -> List[str]:
    """Each line whose rows add to other cents than Schedule 3's figure
    (it rounds each security's total; the rows round each sale): both
    figures, per column (FOR THE RETURN's: TOTAL COST and DENIED (INFO)
    too), the line by `tjs sum`'s label."""
    out = []
    for ln in doc["lines"]:
        rs = ln["rows_sum"]
        ks = [k for k in GCOL.KEYS if M.cents_apart(ln[k], rs[k])]
        if not ks:
            continue
        what = ", ".join(f"{_NAMES[k]} {_money_text(rs[k])} (the line {_money_text(ln[k])})"
                         for k in ks)
        out.append(f"{GCOL.label(ln)}: the rows add to {what} {base}: Schedule 3 rounds "
                   f"each security's total, the rows each sale.")
    return out


def _scope_names(scope: Optional[Dict[str, Any]]) -> List[str]:
    """The accounts the report is scoped to, in the order given ([]: every
    taxable account): `account_names`, else the one `account`."""
    if not scope:
        return []
    names = scope.get("account_names")
    if names:
        return [str(n) for n in names]
    return [str(scope["account"])] if scope.get("account") else []


def _scope_shown(scope: Optional[Dict[str, Any]]) -> str:
    """"account margin", "accounts margin, beta", or "" for every
    taxable account: the title's and the empty report's words."""
    names = _scope_names(scope)
    if not names:
        return ""
    return f"{'account' if len(names) == 1 else 'accounts'} {', '.join(names)}"


def _totals_heading(scope: Optional[Dict[str, Any]]) -> str:
    """The Schedule 3 totals' heading: FOR THE RETURN for every taxable
    account; with ACCOUNT, the accounts named once and said not to be the
    whole return ("FOR ACCOUNTS margin, beta — Schedule 3, as tjs sum
    shows it for these accounts (not the whole return)"), as `tjs sum`
    heads the same figures (cli/estimate `_sum_return_heading`)."""
    names = _scope_names(scope)
    if not names:
        return "FOR THE RETURN — Schedule 3, as tjs form-export and tjs sum show it"
    one = len(names) == 1
    return (f"FOR {'ACCOUNT' if one else 'ACCOUNTS'} {', '.join(names)} — Schedule 3, as "
            f"tjs sum shows it for {'this account' if one else 'these accounts'} "
            f"(not the whole return)")


def render(doc: Dict[str, Any], *, width: int, tail: Sequence[str] = (),
           manual: Sequence[str] = ()) -> "_out.Doc":
    """The text report, all of it at every width (TJ-5337): a table per
    currency (the base currency's first), its notes under it, the tables'
    total (ALL SALES), Schedule 3's lines and their total (FOR THE RETURN,
    as `tjs sum` lays it out), the unknown-cost sales (`manual`:
    form-export's block), then `tail` (the `! ` lines)."""
    base, year = doc["currency"], doc["year"]
    shown = _scope_shown(doc.get("scope"))
    d = _out.Doc(f"CAPITAL GAINS, ONE ROW PER SALE — {base}, tax year {year}"
                 + (f", {shown}" if shown else "")
                 + ", Schedule 3's cells per disposition")
    if not doc["rows"]:
        d.line(f"No dispositions in {shown or 'the taxable accounts'} "
               f"in tax year {year}.")
    else:
        for forms in _LEGEND:
            GCOL.legend(d, forms, width)
    start = 0
    for t in doc["tables"]:
        rows = [r for r in doc["rows"] if r["currency"] == t["currency"]]
        marks, notes = _marks(rows, start, base)
        start += len(notes)
        d.section(f"SALES IN {t['currency']}")
        d.rows(_table_lines(rows, t, base, marks, width))
        if _fx_missing_line(t):
            d.blank()
            d.para(_fx_missing_line(t))
        if notes:
            d.blank()
            d.rows(_note_lines(notes, width))
    refs: List[Dict[str, Any]] = []
    left_min = 0
    if len(doc["tables"]) > 1:
        d.section(f"ALL SALES — {base}")
        d.rows(_all_sales(doc, width))
        # FOR THE RETURN lines up with ALL SALES: its cells and its left
        # block among FOR THE RETURN's references (TJ-5336 review).
        refs = [*doc["tables"], doc["rows_total"]]
        body, foot = _all_cells(doc)
        left_min = GCOL.block_width(_ALL_HEADS, body + foot)
    if doc["lines"]:
        head = _totals_heading(doc.get("scope"))
        if 0 < d.w < len(head):
            # A long ACCOUNT list: wrapped to the width, as a paragraph.
            d.blank()
            d.para(head)
        else:
            d.section(head)
        d.rows(GCOL.return_table(doc["lines"], doc["totals"], width=width,
                                 keep_denied=True, refs=refs, left_min=left_min)[0])
        for ln in _rounding_lines(doc, base):
            d.para(ln)
    if manual:
        d.blank()
        d.rows(list(manual))
    if tail:
        d.blank()
        d.rows(list(tail))
    return d


# The legend, each line in forms by width (lib/gain_columns `fit_legend`):
# how every row and total foots, in `tjs sum`'s names, with what FX is,
# and what DENIED (INFO) is. The tables and the totals name a figure
# alike (TJ-5341's P3-10), so no line maps one name to another.
_LEGEND: Tuple["GCOL.Forms", ...] = (
    tuple((f"{GCOL.IDENTITY}; FX: the Bank of Canada rate{tail}.",)
          for tail in (" that converted the proceeds", " of the proceeds", "")),
    GCOL.denied_forms())


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

