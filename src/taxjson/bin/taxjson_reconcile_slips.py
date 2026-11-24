#!/usr/bin/env python3
"""
taxjson_reconcile_slips.py

Reconcile the broker's official tax slips — CRA T5008 or IRS 1099-B — against
taxjson's computed dispositions, BEFORE filing. CRA/IRS receive copies of
these slips and machine-match returns against them; this report finds every
divergence and makes you decide, per symbol, whether it's a tool-side problem
(dropped rows, missing statement months) or a legitimate difference to
document (per-broker box-20 cost vs blended ACB, broker lot method vs FIFO,
per-account vs cross-account wash adjustments).

Slip input is a CSV with one row per disposition (or per symbol, already
aggregated) — export it from your broker or hand-build it from the slips.
Headers are matched loosely, so common broker/T5008 spellings work as-is:

    symbol:    symbol | ticker | security | sym
    quantity:  quantity | qty | shares | number of shares | box 16
    proceeds:  proceeds | proceeds of disposition | gross proceeds | box 21
    cost:      cost | cost or other basis | book value | acb | box 20
               (optional — omit the column to skip basis comparison)
    currency:  currency | currency code | box 13 | devise
               (optional — blank means the project's base currency)

Comparison is per symbol. A slip symbol without a market suffix matches the
computed listing of that root (slip `SAMPLG` matches computed `SAMPLG.US`); when
the books hold two listings of one root (a CDR `SAMPLB.TO` and `SAMPLB.US`) the
row is AMBIGUOUS_LISTING until the slip CSV names the suffix. Broker option
descriptions (`SAMPLE 21MAR25 50 C`, `CALL SAMPLE03/21/25 50`) and share classes
(`SAMPLC B`, `SAMPLC/B`) are read as the books spell them, and the project's
ticker.map renames (SAMPLK -> SAMPLJ) are applied to slip symbols. A blank proceeds
cell beside a cost is nil proceeds (an option that expired worthless);
computed worthless expiries with no slip row are NO_SLIP_EXPECTED and do not
fail the check. Slips aggregated per type code (SHS/OPC/FUT, 'Various')
cannot be compared: transcribe a per-security CSV.
taxjson proceeds are net of sell-side commissions;
slips are usually gross — the tool compares against gross first and falls
back to net, telling you which one matched. Amounts must be in the same
currency as the project's base currency; the tool cannot convert slips, so
a slip whose currency column (T5008 Box 13) names another currency is
refused (a USD Webull T5008: convert boxes 20/21 to CAD first), and so is
a gains file whose dispositions or metadata.target_currency name one (the
native *_raw_gains.json).
An unreadable proceeds, quantity or cost cell (a decimal comma '1234,56',
'nan', '1 500.00 CAD') is reported and fails the check.

Exit codes: 0 = everything reconciled; 1 = at least one mismatch or missing
symbol; 2 = usage error.

Usage:
    taxjson-reconcile-slips t5008.csv --country canada \
        --gains margin_gains.json [--gains ...]
        [--year 2025] [--tolerance 1.00] [--json]

Or through the project wrapper: `tjs reconcile-slips t5008.csv`.
"""
from taxjson.lib.out import exit_text
from taxjson.lib.stage_msg import emit_line
import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib.tax_forms import load_json
from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.futures_prefixes import FuturesSymbolError
from taxjson.lib.country import CANADA, USA
from taxjson.model import money as M
from taxjson.lib.numeric import nonneg_float_arg
from taxjson.lib.brokerages.base import is_ca_pref_series
from taxjson.lib.slip_symbols import (
    UnrepresentableStrike,
    _SUFFIX_RE,
    _bump,
    load_computed,
    slip_symbol,
    split_listing,
)


# The slip wording each country's run prints (re-audit A2-0423, A2-0744,
# A2-0753, A2-1295, A2-1337, A2-1348, A2-1351): a US run never cites the
# T5008, its boxes, the Bank of Canada or a blended ACB; the basis is
# FIFO per account (US-BASIS-01) and the rates are the project's own
# (US-FX-02).
_WORDS = {
    CANADA: {
        "amount_boxes": "boxes 20/21",
        "rate": "the Bank of Canada rate for each row's settlement date "
                "(the rate taxjson used)",
        "cost_note": "per-broker book value vs blended ACB / lot method",
    },
    USA: {
        "amount_boxes": "the 1099-B proceeds and cost (boxes 1d/1e)",
        "rate": "the rate taxjson used for each row (the project's rates "
                "file, work/to_base.csv)",
        "cost_note": "the broker's basis for the lots it sold vs "
                     "taxjson's FIFO basis per account — a wash sale "
                     "across accounts or a transfer-in",
    },
}


_HEADER_SYNONYMS = {
    "symbol": ("symbol", "ticker", "security symbol", "security", "sym",
               # T5008 box 17 heading (R1-1) and French slips (R1-209).
               "identification of securities", "box 17", "symbole",
               "identification des titres", "désignation des titres"),
    "quantity": ("quantity", "qty", "shares", "number of shares", "box 16",
                 "quantity of securities", "quantité",
                 "quantité de titres", "nombre de titres"),
    "proceeds": ("proceeds of disposition", "gross proceeds", "proceeds",
                 "box 21", "proceeds of disposition or settlement amount",
                 "produit de disposition",
                 "produit de disposition ou montant de règlement"),
    "cost": ("cost or other basis", "cost basis", "book value",
             "cost/book value", "adjusted cost base", "acb", "box 20",
             "cost", "cost or book value", "coût ou valeur comptable",
             "valeur comptable", "prix de base rajusté"),
    # T5008 Box 13 (R1-20): a USD slip compared against CAD books gave
    # one MISMATCH per symbol, every amount off by the FX rate.
    "currency": ("currency", "currency code", "box 13",
                 "currency of report", "devise", "code de devise"),
}

# Spellings of a currency cell -> ISO code.
_CURRENCY_ALIASES = {"CDN": "CAD", "C$": "CAD", "CA$": "CAD", "CAN": "CAD",
                     "US$": "USD", "US": "USD", "U.S.": "USD"}


def _clean_amount(raw: str) -> Optional[float]:
    """A slip cell as a number; None when blank or unreadable (the caller
    reports it). Parsed strictly (brokerages.base.parse_strict_number):
    a decimal comma ('1234,56') is unreadable, never stripped into a
    value 100x too large; '1,234.56', '$', '(12.00)' are fine."""
    from taxjson.lib.brokerages.base import parse_strict_number
    s = (raw or "").strip().replace("$", "")
    if not s:
        return None
    try:
        return parse_strict_number(s, field="amount")
    except ValueError:
        return None


class SlipRefused(SystemExit):
    """A slip CSV the tool will not compare (exit 2, one line)."""


class AmbiguousHeader(SlipRefused):
    pass


def _map_headers(fieldnames: List[str], path: Optional[Path] = None
                 ) -> Dict[str, str]:
    """Map our canonical keys to the CSV's actual column names. An EXACT
    label wins; otherwise the first synonym (in priority order) that
    exactly ONE unclaimed column contains. Two columns containing the
    same synonym, or two exact labels of one amount (A2-0656; the
    symbol keeps its synonym priority), is refused as ambiguous, and no
    column serves two keys
    (S036-02): first-substring-wins let a blank box-23 'Quantity of
    securities received on settlement' column silently switch the
    quantity check off, and 'Proceeds (USD)' before '(CAD)' compared
    the wrong currency."""
    mapping: Dict[str, str] = {}
    lowered = {f.strip().lower(): f for f in fieldnames if f}
    # Two columns with ONE name ('proceeds' twice): the dict above keeps
    # one of them and csv.DictReader the last cell, so the other figure
    # was dropped unseen and the slip reconciled on whichever column
    # came last (backlog TJ-0890). Counted before the collapse.
    _seen: Dict[str, List[str]] = {}
    for f in fieldnames:
        if f:
            _seen.setdefault(f.strip().lower(), []).append(f)
    claimed: set = set()
    where = f"{path}: " if path else ""

    def _claim(key: str, col: str) -> None:
        same = _seen.get(col.strip().lower()) or [col]
        if len(same) > 1:
            raise AmbiguousHeader(
                f"taxjson-reconcile-slips: {where}ambiguous header — "
                f"{len(same)} columns are named {col.strip()!r} (the "
                f"{key!r} column). Rename or delete the extra "
                f"column(s) so exactly one matches.")
        mapping[key] = col
        claimed.add(col)

    for key, synonyms in _HEADER_SYNONYMS.items():
        free = {low: orig for low, orig in lowered.items()
                if orig not in claimed}
        exact = [free[syn] for syn in synonyms if syn in free]
        if len(exact) > 1 and key != "symbol":
            # Two columns that are BOTH exact spellings of one amount
            # ('Proceeds' and 'Proceeds of disposition', 'Quantity' and
            # 'Shares') — picking one by synonym priority compared the
            # other's figure silently (A2-0656). The symbol keeps its
            # priority: a slip often has a ticker AND a security-name
            # column ('Symbol', 'Security').
            raise AmbiguousHeader(
                f"taxjson-reconcile-slips: {where}ambiguous header — "
                f"{', '.join(repr(h) for h in exact)} all look like "
                f"the {key!r} column. Rename or delete the extra "
                f"column(s) so exactly one matches.")
        if exact:
            _claim(key, exact[0])
            continue
        for syn in synonyms:
            hits = [orig for low, orig in free.items() if syn in low]
            if len(hits) > 1:
                raise AmbiguousHeader(
                    f"taxjson-reconcile-slips: {where}ambiguous header — "
                    f"{', '.join(repr(h) for h in hits)} all look like "
                    f"the {key!r} column. Rename or delete the extra "
                    f"column(s) so exactly one matches (e.g. keep only "
                    f"the CAD amounts).")
            if hits:
                _claim(key, hits[0])
                break
    return mapping


def _read_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise SlipRefused(f"taxjson-reconcile-slips: cannot read {path}: "
                          f"{e.strerror or e}")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        # Excel "Unicode text" exports (R1-209): read as UTF-16 instead
        # of reporting mojibake headers as an unrecognized column.
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        # Québec-broker T5008 exports (Desjardins, NBDB, RBC French)
        # are commonly cp1252/latin-1 — the utf-8-only open crashed
        # with a raw UnicodeDecodeError traceback (REVIEW #22).
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        # Bytes cp1252 leaves undefined (0x81/0x8D/0x8F/0x90/0x9D):
        # latin-1 decodes every byte (S036-09) — a garbled cell then
        # surfaces as an unreadable row, not a traceback.
        return raw.decode("latin-1")


def _rename_fn(renames: Optional[Dict[str, str]]):
    """Slip symbol -> the project's consolidated symbol, through the
    ticker.map GLOBAL/TOBASE/JOURNAL renames the pipeline applied to the
    books (R1-19: the slip says SAMPLK / SAMPLD, the books SAMPLJ.TO / SAMPLD.B.TO,
    options included). A bare slip symbol is tried as .US then .TO."""
    if not renames:
        return lambda sym: sym
    from taxjson.lib.ticker_map_rules import map_symbol

    def _fn(sym: str) -> str:
        root, sfx = split_listing(sym)
        if sfx:
            return map_symbol(sym, renames)
        for trial in (".US", ".TO"):
            got = map_symbol(root + trial, renames)
            if got != root + trial:
                # Keep the slip bare: the listing it names is unknown.
                return split_listing(got)[0]
        return sym
    return _fn


def _norm_currency(raw: str) -> str:
    s = (raw or "").strip().upper()
    return _CURRENCY_ALIASES.get(s, s)


def load_slip(path: Path, renames: Optional[Dict[str, str]] = None,
              base_currency: Optional[str] = None,
              country: str = CANADA) -> Dict[str, Dict[str, Any]]:
    """Aggregate the slip CSV per symbol root:
    {ROOT: {qty, proceeds, cost (or None), rows, listings}}, where
    listings splits the root by the listing suffix the slip wrote
    ('' when it wrote none). With `base_currency`, a row whose currency
    cell (T5008 Box 13) names another currency refuses the file
    (SlipRefused): the tool cannot convert slip amounts (R1-20)."""
    text = _read_text(path)
    rename = _rename_fn(renames)
    import io
    with io.StringIO(text, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit(exit_text(f"taxjson-reconcile-slips: {path} is empty"))
        cols = _map_headers(list(reader.fieldnames), path)
        for required in ("symbol", "proceeds"):
            if required not in cols:
                raise SystemExit(
                    exit_text(f"taxjson-reconcile-slips: {path} has no recognizable "
                              f"{required!r} column (headers: "
                              f"{', '.join(reader.fieldnames)}). See --help for "
                              f"accepted spellings."))
        out: Dict[str, Dict[str, Any]] = {}
        dropped = 0
        foreign: Dict[str, int] = {}

        def _drop(what: str) -> None:
            nonlocal dropped
            # A slip row the tool cannot read is a row it cannot
            # RECONCILE — silently dropping it certified a disagreeing
            # slip as fully reconciled at exit 0 (REVIEW #21, R1-201).
            dropped += 1
            from taxjson.lib.out import warn as _warn
            _warn(f"{path.name}: {what} — row NOT reconciled",
                  prog="taxjson-reconcile-slips",
                  details=["Fix the slip CSV cell."])

        width = len(reader.fieldnames)
        _amount_cols = {cols[k] for k in ("quantity", "proceeds", "cost")
                        if k in cols}
        for lineno, row in enumerate(reader, 2):
            extra = [str(x) for x in (row.get(None) or [])]
            if any(x.strip() for x in extra):
                # More cells than the header: an unquoted 1,000 split
                # into two shifts every cell after it (proceeds 1, cost
                # 000), and the surplus was dropped unseen (backlog
                # TJ-1015).
                _drop(f"line {reader.line_num}: {width + len(extra)} "
                      f"cells for {width} columns — quote an amount "
                      f"written with a comma (\"1,000\")")
                continue
            # A blank surplus cell, or none: the amount columns themselves
            # show the split, as `1` then `000` (no slip prints a figure
            # with a leading zero) (backlog TJ-1213); with cents or a minus
            # sign too (TJ-1307).
            _names = list(reader.fieldnames)
            _split = next(
                (i for i in range(1, width)
                 if _names[i] in _amount_cols
                 and re.fullmatch(r"0\d\d(\.\d*)?",
                                  (row.get(_names[i]) or "").strip())
                 and _names[i - 1] != cols["symbol"]
                 and re.fullmatch(r"-?\$?-?\d{1,3}",
                                  (row.get(_names[i - 1]) or "").strip())),
                None)
            if _split is not None:
                _a = (row.get(_names[_split - 1]) or "").strip()
                _b = (row.get(_names[_split]) or "").strip()
                _drop(f"line {reader.line_num}: {_a} then {_b} looks like "
                      f"one amount — quote an amount written with a comma "
                      f"(\"{_a},{_b}\")")
                continue
            cells = {k: (row.get(cols[k]) or "").strip()
                     for k in ("symbol", "quantity", "proceeds", "cost",
                               "currency")
                     if k in cols}
            ccy = _norm_currency(cells.get("currency", ""))
            if ccy and base_currency and ccy != base_currency.upper():
                foreign[ccy] = foreign.get(ccy, 0) + 1
                continue
            try:
                sym = rename(slip_symbol(cells["symbol"], strict=True))
            except (UnrepresentableStrike, FuturesSymbolError) as e:   # TJ-5150
                # Matching it to the nearest OCC strike certified a
                # different contract (backlog TJ-0902).
                _drop(f"line {lineno}: option {cells['symbol']!r}: an OCC symbol cannot "
                      f"hold its strike {e} (it counts in steps of 0.001, below 100000)"
                      if isinstance(e, UnrepresentableStrike) else f"line {lineno}: {e}")
                continue
            if not sym:
                if any(cells.get(k) for k in ("quantity", "proceeds",
                                              "cost")):
                    # A row with amounts but no symbol (a CUSIP-only or
                    # bond row) was skipped silently (R1-201).
                    _drop(f"line {lineno}: amounts but no symbol")
                continue
            raw_p = cells["proceeds"]
            proceeds = _clean_amount(raw_p)
            if proceeds is None and not raw_p and cells.get("cost"):
                # A blank box 21 beside a box 20 is how brokers print an
                # option that expired worthless: nil proceeds (R1-17).
                proceeds = 0.0
            if proceeds is None:
                _drop(f"unreadable proceeds for {sym} ({raw_p!r})")
                continue
            q = None
            if cells.get("quantity"):
                q = _clean_amount(cells["quantity"])
                if q is None:
                    # An unreadable quantity turned the quantity check
                    # off for the symbol and still exited 0 (R1-201).
                    _drop(f"unreadable quantity for {sym} "
                          f"({cells['quantity']!r})")
                    continue
            c = None
            if cells.get("cost"):
                c = _clean_amount(cells["cost"])
                if c is None:
                    # An unreadable cost cell was dropped silently: the
                    # cost note vanished, or came from a partial sum
                    # (S035-19, S036-07, R1-335's '50000,00').
                    _drop(f"unreadable cost for {sym} "
                          f"({cells['cost']!r})")
                    continue
            root, sfx = split_listing(sym)
            rec = out.setdefault(root, {"qty": 0.0, "proceeds": 0.0,
                                        "cost": None, "rows": 0,
                                        "qty_rows": 0, "cost_rows": 0,
                                        "listings": {}})
            # `qty_rows`: the rows that state a quantity. A blank one
            # adds nothing to `qty`, so with fewer than `rows` the
            # quantity is a partial sum, never a total to compare
            # (_compare; audit 2026-10-24 B47). `cost_rows` the same for
            # the cost (backlog TJ-0892).
            _bump(rec, sfx, rows=1, proceeds=proceeds,
                  qty=abs(q) if q is not None else 0.0,
                  qty_rows=1 if q is not None else 0,
                  cost_rows=1 if c is not None else 0, cost=c)
        if foreign:
            got = ", ".join(f"{n} row(s) in {c}"
                            for c, n in sorted(foreign.items()))
            w = _WORDS[country]
            col = ("Box 13 / currency column" if country == CANADA
                   else "currency column")
            raise SlipRefused(
                f"taxjson-reconcile-slips: {path}: the slip reports "
                f"amounts in another currency ({col}: "
                f"{got}) but the books are in {base_currency.upper()}. "
                f"reconcile-slips cannot convert slip amounts: convert "
                f"{w['amount_boxes']} to {base_currency.upper()} at "
                f"{w['rate']} and blank the currency column, or leave "
                f"this slip out.")
        if dropped:
            out["__dropped_rows__"] = dropped   # consumed (popped) in main
        return out


class ComputedCurrencyRefused(SystemExit):
    """A gains file in another currency than the slips' (exit 2, one
    line)."""


def check_computed_currency(gains_paths: List[Path],
                            base_currency: str) -> None:
    """Refuse a gains file whose dispositions (transactions and the
    unknown-cost manual_reporting_required rows) or whose
    metadata.target_currency name another currency than
    `base_currency`, the country's, which the slips are in: a USD book
    (the native work/<acct>_raw_gains.json) compared 150 USD with a
    150 CAD slip as equal (backlog TJ-0325), as form-export and t1135
    refuse it. A row with no currency is the base currency's (the
    pipeline's converted rows always carry it). Raises
    ComputedCurrencyRefused, or InputFileError for a file that cannot
    be read."""
    base = (base_currency or "").upper()
    for p in gains_paths:
        data = load_json(p)
        meta = data.get("metadata") if isinstance(data, dict) else None
        tgt = (str(meta.get("target_currency") or "").upper()
               if isinstance(meta, dict) else "")
        other: Dict[str, int] = {}
        for e in (list(data.get("transactions") or [])
                  + list(data.get("manual_reporting_required") or [])):
            if not isinstance(e, dict) or e.get("action") in (
                    "DIVIDEND", "DIVIDEND_IN_LIEU"):
                continue
            c = str(e.get("currency") or "").strip().upper()
            if c and c != base:
                other[c] = other.get(c, 0) + 1
        if tgt and tgt != base:
            other.setdefault(tgt, 0)
        if other:
            got = ", ".join(f"{c} ({n} row(s))" if n else
                            f"{c} (metadata.target_currency)"
                            for c, n in sorted(other.items()))
            raise ComputedCurrencyRefused(
                f"{p}: computed dispositions in {got}, not {base}, the "
                f"slips' currency — pass the converted "
                f"<account>_gains(_wash).json, not the native "
                f"*_raw_gains.json (`tjs reconcile-slips` does)")


def _sum_listings(recs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not recs:
        return None
    agg: Dict[str, Any] = {}
    for r in recs:
        for k, v in r.items():
            if k in ("listings", "cost"):
                continue
            agg[k] = agg.get(k, 0.0) + v
    costs = [r.get("cost") for r in recs if r.get("cost") is not None]
    agg["cost"] = sum(costs) if costs else None
    return agg


def _compare(label: str, s: Dict[str, Any], c: Dict[str, Any],
             tolerance: float, country: str = CANADA) -> Dict[str, Any]:
    problems: List[str] = []
    notes: List[str] = []
    d_gross = s["proceeds"] - c["proceeds_gross"]
    d_net = s["proceeds"] - c["proceeds_net"]
    # Grant timing (A2-0657): the broker's slip follows the CONTRACT —
    # it reports a written option's premium when the position closes,
    # while the books report it in the write year (s.49(1)). Take this
    # year's still-open writes out, and put an earlier year's premium
    # that this year's buy-back closed back in, before comparing.
    _open = float(c.get("open_grant") or 0.0)
    _open_net = float(c.get("open_grant_net") or 0.0)
    _prior = float(c.get("prior_grant") or 0.0)
    _timed = abs(_open) > 0.005 or abs(_prior) > 0.005
    d_gross_t = d_gross + _open - _prior
    d_net_t = d_net + _open_net - _prior
    grant_note = []
    if abs(_open) > 0.005:
        grant_note.append(f"{M.shown(_open, 2):,.2f} premium of written option(s) "
                          f"still open at the year end is reported this "
                          f"year (grant timing, s.49(1)) but not on this "
                          f"year's slip")
    if abs(_prior) > 0.005:
        grant_note.append(f"slip proceeds include {M.shown(_prior, 2):,.2f} premium "
                          f"of earlier-year write(s) already reported in "
                          f"the write year (grant timing, s.49(1)) — not "
                          f"income again")
    _unk = int(c.get("unknown_proceeds_rows") or 0)
    unknown_note = []
    if _unk:
        # A cover of a short sale the files do not hold: what the short
        # sale brought in is unknown, so the slip's proceeds cannot be
        # checked — never against an invented 0 (backlog TJ-0906).
        unknown_note.append(
            f"proceeds not compared: {_unk} short cover(s) whose short "
            f"sale is not in your files — the books do not know what it "
            f"brought in (slip {M.shown(s['proceeds'], 2):,.2f}; the other "
            f"disposition(s) computed {M.shown(c['proceeds_gross'], 2):,.2f})")
    if _unk:
        pass
    elif abs(d_gross) <= tolerance:
        pass
    elif abs(d_net) <= tolerance:
        notes.append("matches NET proceeds (slip appears net of "
                     "commissions)")
    elif _timed and abs(d_gross_t) <= tolerance:
        notes.extend(grant_note)
    elif _timed and abs(d_net_t) <= tolerance:
        notes.extend(grant_note)
        notes.append("matches NET proceeds (slip appears net of "
                     "commissions)")
    else:
        cands = [d_gross, d_net] + ([d_gross_t, d_net_t] if _timed else [])
        closer = min(cands, key=abs)
        problems.append(f"proceeds off by {M.shown(closer, 2):+,.2f} "
                        f"(slip {M.shown(s['proceeds'], 2):,.2f} vs computed "
                        f"gross {M.shown(c['proceeds_gross'], 2):,.2f} / net "
                        f"{M.shown(c['proceeds_net'], 2):,.2f})")
        if _timed:
            problems.extend(grant_note)
    _oq = float(c.get("open_grant_qty") or 0.0)
    _known = s.get("qty_rows")
    _blank = (int(s.get("rows") or 0) - int(_known)
              if _known is not None else 0)
    # A quantity the slip STATES — an explicit 0 included — is compared:
    # `qty > 0` alone took a written '0' for an absent cell and certified
    # a 10-share sale against it (backlog TJ-0891). A slip dict without
    # the row count (an older caller) keeps the old reading.
    _stated = s["qty"] > 0 or (_known is not None and int(_known) > 0)
    if _stated and _blank > 0:
        # Some of the slip's rows state no quantity: its share count is
        # a partial sum, not a total — a "quantity off by" line claimed
        # a discrepancy of exactly the blank rows' shares (audit
        # 2026-10-24 B47). The known part alone above the computed
        # count is a real one.
        _where = (f"{_blank} of {int(s['rows'])} slip row(s) state no "
                  f"quantity")
        if s["qty"] > c["qty"] + 1e-4:
            problems.append(f"quantity: the {s['qty']:,.4f} the slip "
                            f"rows with a quantity state exceed the "
                            f"computed {c['qty']:,.4f} ({_where})")
        else:
            notes.append(f"quantity not compared: {_where} (the others "
                         f"state {s['qty']:,.4f}; computed "
                         f"{c['qty']:,.4f})")
    elif _stated and abs(s["qty"] - c["qty"]) > 1e-4 \
            and not (_oq > 1e-9 and abs(s["qty"] - (c["qty"] - _oq)) <= 1e-4):
        problems.append(f"quantity off by {s['qty'] - c['qty']:+,.4f} "
                        f"(slip {s['qty']:,.4f} vs computed "
                        f"{c['qty']:,.4f})")
    _cost_known = s.get("cost_rows")
    _cost_blank = (int(s.get("rows") or 0) - int(_cost_known)
                   if _cost_known is not None else 0)
    if s.get("cost") is not None and _cost_blank > 0:
        # Some rows state no cost: the slip's cost is a partial sum —
        # "differs by" was the blank rows' basis, a discrepancy that
        # does not exist (backlog TJ-0892).
        notes.append(f"cost not compared: {_cost_blank} of "
                     f"{int(s['rows'])} slip row(s) state no cost (the "
                     f"others state {M.shown(s['cost'], 2):,.2f}; computed "
                     f"{M.shown(c['cost'], 2):,.2f})")
    elif s.get("cost") is not None:
        d_cost = s["cost"] - c["cost"]
        # Dividend compensation capitalized into a cover's cost is in no
        # 1099-B: the difference the broker's slip must show (§263(h);
        # US-INC-04, backlog TJ-1066).
        _cap263 = float(c.get("capitalized_263h") or 0.0)
        if _cap263 and abs(d_cost + _cap263) <= tolerance:
            notes.append(f"slip cost differs by {M.shown(d_cost, 2):+,.2f}, §263(h) "
                         f"compensation — the dividend compensation added "
                         f"to the cover's cost, which the broker's slip "
                         f"does not show")
        elif abs(d_cost) > tolerance:
            if _cap263:
                notes.append(f"{M.shown(_cap263, 2):,.2f} of the cost difference is "
                             f"dividend compensation added to the cover's "
                             f"cost (§263(h))")
            notes.append(f"slip cost differs by {M.shown(d_cost, 2):+,.2f} — often "
                         f"legitimate ({_WORDS[country]['cost_note']}); "
                         f"document the reason")
    if c.get("tainted_rows"):
        notes.append(f"{int(c['tainted_rows'])} disposition(s) with an "
                     f"unknown cost (no purchase in your files) included")
    return {"symbol": label,
            "status": ("MISMATCH" if problems else
                       "UNKNOWN_PROCEEDS" if _unk else "OK"),
            "detail": "; ".join(unknown_note + problems + notes)}


def _missing_from_slip(label: str, c: Dict[str, Any],
                       tolerance: float) -> Dict[str, Any]:
    _unk = int(c.get("unknown_proceeds_rows") or 0)
    if _unk:
        # Unknown short-sale proceeds are not nil proceeds: the broker
        # reports the short sale (backlog TJ-0906).
        return {"symbol": label, "status": "MISSING_FROM_SLIP",
                "detail": f"computed {c['rows']} disposition(s), "
                          f"{_unk} of them the cover of a short sale "
                          f"that is not in your files (its proceeds "
                          f"unknown) — no slip row (missing slip?)"}
    _open = float(c.get("open_grant") or 0.0)
    # The premium of an earlier year's write this year's buy-back
    # closed: the broker's close-year slip reports it as proceeds, as
    # _compare puts it back — so a symbol carrying one expects a slip
    # row; nil computed proceeds are not a worthless expiry then
    # (backlog TJ-0782).
    _prior = float(c.get("prior_grant") or 0.0)
    _has_prior = abs(_prior) > 0.005
    if abs(_open) > 0.005 and not _has_prior and \
            abs(c["proceeds_gross"] - _open) <= max(tolerance, 0.005):
        # Only this year's grant-timing writes still open at the year
        # end (A2-0657): the premium is reported this year (s.49(1));
        # the broker reports the contract on the slip of the year it
        # closes.
        return {"symbol": label, "status": "NO_SLIP_EXPECTED",
                "detail": f"written option(s) still open at the year end: "
                          f"the {M.shown(_open, 2):,.2f} premium is reported this year "
                          f"without a slip (grant timing, s.49(1)); the "
                          f"broker's slip reports it when the contract "
                          f"closes"}
    if not _has_prior and not c.get("future_loss_rows") and abs(
            c["proceeds_gross"]) <= min(tolerance, 0.005):
        # Nil proceeds — a long option that expired worthless. IB
        # issues no T5008 row for it; that is expected, not a gap
        # (R1-1), so it does not fail the reconciliation.
        return {"symbol": label, "status": "NO_SLIP_EXPECTED",
                "detail": f"computed {c['rows']} disposition(s) with nil "
                          f"proceeds (expired option) — brokers usually "
                          f"issue no slip row"}
    if _has_prior:
        _slip = c["proceeds_gross"] - _open + _prior
        return {"symbol": label, "status": "MISSING_FROM_SLIP",
                "detail": f"computed {c['rows']} disposition(s), "
                          f"proceeds {M.shown(c['proceeds_gross'], 2):,.2f} — no slip "
                          f"row; the broker's slip would report "
                          f"{M.shown(_slip, 2):,.2f}, with {M.shown(_prior, 2):,.2f} premium of "
                          f"earlier-year write(s) this year's buy-back or "
                          f"expiry closed (grant timing, s.49(1))"}
    return {"symbol": label, "status": "MISSING_FROM_SLIP",
            "detail": f"computed {c['rows']} disposition(s), "
                      f"proceeds {M.shown(c['proceeds_gross'], 2):,.2f} — "
                      f"no slip row (missing slip, or a "
                      f"non-slip disposition like a corp action)"}


def _label(root: str, sfx: str) -> str:
    return f"{root}.{sfx}" if sfx else root


def _london_preferreds(slip: Dict[str, Dict[str, Any]],
                       computed: Dict[str, Dict[str, Any]]
                       ) -> Dict[str, Dict[str, Any]]:
    """`computed` with a dotted preferred the books hold with no market
    suffix (SYN.PF.L) under the root of a bare slip symbol (SYN.PF) that
    nothing else computed meets: its last letter is then the listing, not
    the series split_listing keeps (backlog TJ-1299). One such symbol
    only; a series on a listing (SYN.PF.L.TO) stays a series."""
    out = dict(computed)
    for root, s in slip.items():
        if root in computed or "" not in (s.get("listings") or {"": s}):
            continue
        hits = []
        for full, rec in computed.items():
            m = _SUFFIX_RE.search(full)
            if (full not in slip and m and full[:m.start()] == root
                    and is_ca_pref_series(full)
                    and set(rec.get("listings") or {""}) == {""}):
                hits.append((full, m.group(1).upper()))
        if len(hits) == 1:
            full, sfx = hits[0]
            rec = out.pop(full)
            out[root] = dict(rec, listings={sfx: (rec.get("listings")
                                                  or {"": rec})[""]})
    return out


def reconcile(slip: Dict[str, Dict[str, Any]],
              computed: Dict[str, Dict[str, Any]],
              tolerance: float, country: str = CANADA) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    computed = _london_preferreds(slip, computed)
    for root in sorted(set(slip) | set(computed)):
        s, c = slip.get(root), computed.get(root)
        if s is None:
            # One row per listing, labelled with its suffix (A2-0657:
            # the bare root hid which listing had no slip row).
            for sfx, sub in sorted((c.get("listings") or {"": c}).items()):
                rows.append(_missing_from_slip(_label(root, sfx), sub,
                                               tolerance))
            continue
        if c is None:
            rows.append({"symbol": root, "status": "MISSING_FROM_COMPUTED",
                         "detail": f"slip has {s['rows']} row(s), proceeds "
                                   f"{M.shown(s['proceeds'], 2):,.2f} — nothing "
                                   f"computed (dropped CSV rows, a "
                                   f"missing statement, or a symbol the "
                                   f"books spell differently?)"})
            continue
        s_list = s.get("listings") or {"": s}
        c_list = c.get("listings") or {"": c}
        named = sorted(x for x in s_list if x)
        for sfx in named:
            label = _label(root, sfx)
            if sfx in c_list:
                rows.append(_compare(label, s_list[sfx], c_list[sfx],
                                     tolerance, country))
            else:
                rows.append({"symbol": label,
                             "status": "MISSING_FROM_COMPUTED",
                             "detail": f"slip has {s_list[sfx]['rows']} "
                                       f"row(s), proceeds "
                                       f"{M.shown(s_list[sfx]['proceeds'], 2):,.2f} — "
                                       f"nothing computed for this "
                                       f"listing"})
        rest = {x: v for x, v in c_list.items() if x not in named}
        if "" in s_list:
            agg = _sum_listings(list(rest.values()))
            if agg is None:
                rows.append({"symbol": root,
                             "status": "MISSING_FROM_COMPUTED",
                             "detail": f"slip has {s_list['']['rows']} "
                                       f"row(s) without a listing "
                                       f"suffix, proceeds "
                                       f"{M.shown(s_list['']['proceeds'], 2):,.2f} — "
                                       f"nothing computed"})
                continue
            row = _compare(root, s_list[""], agg, tolerance, country)
            if len(rest) > 1:
                # Two securities that share a root (a CDR and its US
                # parent, SAMPLO.TO vs SAMPLO.US) folded into one bare slip
                # symbol: offsetting errors between them cancel and
                # reconciled "OK" (R1-292). Make the user say which.
                names = ", ".join(_label(root, x) for x in sorted(rest))
                row["detail"] = "; ".join(filter(None, [
                    f"computed has {len(rest)} listings ({names}) but the "
                    f"slip names none — write the suffix in the slip CSV "
                    f"so each listing is checked on its own",
                    row["detail"]]))
                if row["status"] == "OK":
                    row["status"] = "AMBIGUOUS_LISTING"
            rows.append(row)
        else:
            for sfx in sorted(rest):
                rows.append(_missing_from_slip(_label(root, sfx),
                                               rest[sfx], tolerance))
    failing = [r for r in rows
               if r["status"] not in ("OK", "NO_SLIP_EXPECTED")]
    return {"rows": rows,
            "counts": {
                "ok": sum(1 for r in rows if r["status"] == "OK"),
                "mismatch": sum(1 for r in rows
                                if r["status"] == "MISMATCH"),
                "missing_from_computed": sum(
                    1 for r in rows
                    if r["status"] == "MISSING_FROM_COMPUTED"),
                "missing_from_slip": sum(
                    1 for r in rows if r["status"] == "MISSING_FROM_SLIP"),
                "no_slip_expected": sum(
                    1 for r in rows if r["status"] == "NO_SLIP_EXPECTED"),
                "ambiguous_listing": sum(
                    1 for r in rows if r["status"] == "AMBIGUOUS_LISTING"),
                "unknown_proceeds": sum(
                    1 for r in rows if r["status"] == "UNKNOWN_PROCEEDS"),
            },
            "clean": not failing}


def _short_detail(detail: str, cap: int = 56) -> str:
    """A row's detail cut to its first clause (the default view's
    column; --details prints it whole)."""
    cut = len(detail)
    for sep in (" (", " — ", ": ", "; "):
        i = detail.find(sep)
        if 0 < i < cut:
            cut = i
    d = detail[:cut]
    return d if len(d) <= cap else d[:cap - 3].rstrip() + "..."


def _counts_text(c: Dict[str, Any], tolerance: float) -> str:
    return (f"{c['ok']} OK, {c['mismatch']} mismatch, "
            f"{c['missing_from_computed']} missing from computed, "
            f"{c['missing_from_slip']} missing from slip"
            + (f", {c['ambiguous_listing']} ambiguous listing"
               if c.get("ambiguous_listing") else "")
            + (f", {c['unknown_proceeds']} with unknown short-sale "
               f"proceeds" if c.get("unknown_proceeds") else "")
            + (f", {c['no_slip_expected']} with no slip row "
               f"expected (not a failure)"
               if c.get("no_slip_expected") else "")
            + f" (tolerance ±{M.shown(tolerance, 2):,.2f}).")


def render_brief(rep: Dict[str, Any], tolerance: float,
                 country: Optional[str] = None) -> str:
    """The default view (docs/output-style.md, Essentials first): a
    legend, one table row per symbol with its first clause, the counts,
    and the lines to act on; the full details and the notes with
    --details (render)."""
    from taxjson.lib.out import act, fit_table
    lines = ["SLIP RECONCILIATION — computed dispositions vs broker tax "
             "slips", ""]
    if not rep["rows"]:
        lines.append("Nothing to reconcile (no symbols on either side).")
        return "\n".join(lines)
    slip = "1099-B" if country == "usa" else "T5008"
    lines.append(f"MISMATCH fails the check; MISSING_FROM_SLIP: no {slip} "
                 f"row, often a corporate action.")
    lines.extend(fit_table(
        ("SYMBOL", "STATUS", "DETAIL"),
        [(r["symbol"], r["status"], _short_detail(r["detail"] or ""))
         for r in rep["rows"]], aligns=["<", "<", "<"]))
    c = rep["counts"]
    lines.append("")
    lines.append(f"{c['ok']} OK, {c['mismatch']} mismatch, "
                 f"{c['missing_from_computed']} missing from computed, "
                 f"{c['missing_from_slip']} missing from slip"
                 + (f", {c['ambiguous_listing']} ambiguous"
                    if c.get("ambiguous_listing") else "")
                 + (f", {c['unknown_proceeds']} unknown proceeds"
                    if c.get("unknown_proceeds") else "")
                 + (f", {c['no_slip_expected']} no slip expected"
                    if c.get("no_slip_expected") else "")
                 + f" (tolerance ±{M.shown(tolerance, 2):,.2f})")
    cmd = "tjs reconcile-slips --details"
    bad = c["mismatch"] + c["missing_from_computed"] \
        + c.get("ambiguous_listing", 0) + c.get("unknown_proceeds", 0)
    if bad:
        lines.append(act(f"Explain or fix {bad} difference(s) before "
                         f"filing", cmd))
    if c["missing_from_slip"]:
        lines.append(f"Check each MISSING_FROM_SLIP against the {slip}s "
                     f"— {cmd}")
    return "\n".join(lines)


def render(rep: Dict[str, Any], tolerance: float,
           country: Optional[str] = None) -> str:
    """The console report in the house style (docs/output-style.md):
    one line per symbol — SYMBOL, STATUS and the detail wrapped under
    itself — the counts, then the notes. `--json` is the machine form."""
    from taxjson.lib.out import wrap
    lines = ["SLIP RECONCILIATION — computed dispositions vs broker tax "
             "slips", ""]
    if not rep["rows"]:
        lines.append("Nothing to reconcile (no symbols on either side).")
        return "\n".join(lines)

    def item(text: str) -> None:
        lines.extend(wrap(text, None, "- ", "  "))

    width = max(len(r["symbol"]) for r in rep["rows"])
    swidth = max(len(r["status"]) for r in rep["rows"])
    for r in rep["rows"]:
        head = f"{r['symbol'].ljust(width)}  {r['status'].ljust(swidth)}  "
        if not r["detail"]:
            lines.append(head.rstrip())
        elif len(head) <= 50:
            lines.extend(wrap(r["detail"], None, head, " " * len(head)))
        else:
            # A long symbol: the detail goes under the row, indented.
            lines.append(head.rstrip())
            lines.extend(wrap(r["detail"], None, "    ", "    "))
    c = rep["counts"]
    lines.append("")
    lines.extend(wrap(_counts_text(c, tolerance)))
    lines.append("")
    lines.append("NOTES")
    if country == "usa":
        item("MISSING_FROM_SLIP can be benign: corp-action "
             "dispositions don't always get 1099-B rows. "
             "Worthless option expiries are listed as "
             "NO_SLIP_EXPECTED and do not fail the check.")
    else:
        item("MISSING_FROM_SLIP can be benign: corp-action "
             "dispositions don't always get T5008 rows. "
             "Worthless option expiries, and options written "
             "this year under grant timing and still open at "
             "the year end, are listed as NO_SLIP_EXPECTED and "
             "do not fail the check.")
    item("A slip symbol without a listing suffix matches every "
         "listing of that root; when the books hold two (SAMPLB.TO "
         "CDR and SAMPLB.US), write the suffix in the slip CSV.")
    item("Slips aggregated per type code (IB's SHS/OPC/FUT "
         "rows identified 'Various') cannot be compared per "
         "security: transcribe a per-security CSV.")
    if country == "usa":
        item("Slip cost (1099-B box 1e) is the broker's basis "
             "for the lots it sold; it can differ from taxjson's "
             "when a wash sale crossed accounts (box 1g covers "
             "only the broker's own) or shares came in by "
             "transfer — document it, don't 'fix' it.")
    else:
        item("Slip cost (T5008 box 20) is per-broker book value; a "
             "difference from blended ACB is expected when you hold the "
             "security at more than one broker — document it, don't "
             "'fix' it.")
    if country == "usa":
        item("Amounts are compared in the project base "
             "currency; a slip whose currency column names "
             "another currency is refused, not converted.")
    else:
        item("Amounts are compared in the project base "
             "currency; a slip whose currency column (T5008 Box "
             "13) names another currency is refused, not "
             "converted.")
    return "\n".join(lines)


def _dump_json(rep: Dict[str, Any]) -> None:
    """The `--json` document on stdout: its schema_version (lib/
    json_contract), a non-finite figure as null as every `--json` writes
    it (out.clean_json; a bare NaN token is not JSON: REVIEW #41)."""
    from taxjson.lib import out as _out
    from taxjson.lib.json_contract import versioned
    json.dump(_out.clean_json(versioned(rep, "reconcile-slips")), sys.stdout,
              indent=2, sort_keys=True)


# Guarded here as well as by the console script: `tjs reconcile-slips`
# calls this main directly, and an unreadable ticker.map was a traceback
# there while `taxjson-reconcile-slips` printed one line (A2-0161).
@guard_main("taxjson-reconcile-slips")
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="taxjson-reconcile-slips", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slip_csv", type=Path, nargs="+",
                        help="Slip CSV(s) (accepted column spellings: "
                             "see above). Several files "
                             "(one per broker) are reconciled TOGETHER "
                             "against the combined dispositions.")
    parser.add_argument("--gains", action="append", type=Path, default=[],
                        required=True,
                        help="Year-scoped <account>_gains.json (repeatable; "
                             "prefer the wash-adjusted variants)")
    parser.add_argument("--year", type=tax_year, default=None,
                        help="Defensive year filter")
    parser.add_argument("--date-basis", choices=("settle", "trade"),
                        default=None,
                        help="Which date the --year filter scopes on: "
                             "'settle' (CRA/T5008) or 'trade' "
                             "(IRS/1099-B). Default: the country's "
                             "(canada: settle, usa: trade). The `tjs "
                             "reconcile-slips` wrapper passes the "
                             "project's convention automatically.")
    from taxjson.lib.country import add_country_argument
    # Required (re-audit A2-0747, A2-1294, A2-1349, A2-1350): it sets
    # the base currency the slip amounts must be in, the default
    # --date-basis and the slip wording (T5008 vs 1099-B) — a missing
    # one silently became Canada (CAD, settle dates).
    add_country_argument(
        parser, help="The project's country (required): canada | usa. "
                     "It sets the base currency slip amounts must be "
                     "in (CAD / USD), the default --date-basis and the "
                     "slip wording (T5008 vs 1099-B). The `tjs "
                     "reconcile-slips` wrapper passes it.")
    parser.add_argument("--tolerance", type=nonneg_float_arg, default=1.00,
                        help="Absolute per-symbol amount tolerance "
                             "(default: 1.00)")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    parser.add_argument("--brief", action="store_true",
                        help="The essentials only (what `tjs reconcile-slips` prints "
                             "without --details): the notes and each "
                             "message's detail are left out")
    parser.add_argument("--ticker-map", type=Path, default=None,
                        help="The project's ticker.map: slip symbols are "
                             "renamed the way the books were (SAMPLK -> SAMPLJ, "
                             "SAMPLD -> SAMPLD.B). The `tjs reconcile-slips` "
                             "wrapper passes it automatically.")
    args = parser.parse_args(argv)

    from taxjson.lib import out as _out
    for sp in args.slip_csv:
        if not sp.exists():
            if "*" in str(sp) or "?" in str(sp):
                # The documented `inputs/slips/*.csv` with no slip saved
                # yet reaches here unexpanded (workflow audit 2026-10).
                # With no slip file the checklist's t5008 step is to do,
                # not flagged: a plain --done marks it (backlog TJ-0046).
                _out.fail(f"no slip CSV matches {sp}: nothing to "
                          f"reconcile yet", prog="tjs reconcile-slips",
                          details=["Save the broker's T5008 / 1099-B slip "
                                   "CSV there (docs/filing.md); with no "
                                   "slip to reconcile, mark the step done: "
                                   "`tjs checklist --done t5008`."],
                          code=2)
            _out.fail(f"no such file: {sp}", prog="tjs reconcile-slips",
                      code=2)
        if not sp.is_file():
            # A directory raised IsADirectoryError (S036-09).
            _out.fail(f"{sp} is not a file", prog="tjs reconcile-slips",
                      code=2)
    for p in args.gains:
        if not p.exists():
            _out.fail(f"no such file: {p}", prog="tjs reconcile-slips",
                      details=["Run `tjs run` first."], code=2)

    # One slip per broker is the norm (R1-207): each file on its own
    # reported every other broker's sales as MISSING_FROM_SLIP.
    renames = None
    if args.ticker_map is not None:
        from taxjson.lib.ticker_map_rules import merge_renames
        from taxjson.lib.map_file import load_map_file
        renames = merge_renames(load_map_file(args.ticker_map),
                                to_base=True)
    from taxjson.lib.country import default_tax_date, home_currency
    base_ccy = home_currency(args.country)
    if args.date_basis is None:
        # IRS/1099-B scope by trade date, CRA/T5008 by settlement
        # (re-audit A2-1292, A2-1331).
        args.date_basis = default_tax_date(args.country)
    slip: Dict[str, Dict[str, Any]] = {}
    dropped_rows = 0
    try:
        for sp in args.slip_csv:
            one = load_slip(sp, renames, base_currency=base_ccy,
                            country=args.country)
            dropped_rows += int(one.pop("__dropped_rows__", 0) or 0)
            for root, rec in one.items():
                acc = slip.setdefault(root, {"qty": 0.0, "proceeds": 0.0,
                                             "cost": None, "rows": 0,
                                             "qty_rows": 0, "cost_rows": 0,
                                             "listings": {}})
                for sfx, sub in rec["listings"].items():
                    _bump(acc, sfx, qty=sub["qty"],
                          proceeds=sub["proceeds"], rows=sub["rows"],
                          qty_rows=sub.get("qty_rows", sub["rows"]),
                          cost_rows=sub.get("cost_rows", sub["rows"]),
                          cost=sub["cost"])
    except SlipRefused as e:
        print(e.code, file=sys.stderr)
        return 2
    from taxjson.lib.json_input import InputFileError
    try:
        # Every gains file in the slips' currency first: numbers in
        # another currency are never compared (backlog TJ-0325).
        check_computed_currency(args.gains, base_ccy)
        computed = load_computed(args.gains, args.year, args.date_basis,
                                 args.country)
    except InputFileError as e:
        # One line naming the gains file, not a traceback (S079-11).
        emit_line(f"taxjson-reconcile-slips: error: {e}", file=sys.stderr)
        return 2
    except ComputedCurrencyRefused as e:
        emit_line(f"taxjson-reconcile-slips: error: {e.code}",
                  file=sys.stderr)
        return 2
    rep = reconcile(slip, computed, args.tolerance, args.country)
    if dropped_rows:
        # Unreadable rows mean the slip was NOT fully reconciled —
        # exit 0 here certified agreement the tool never checked
        # (REVIEW #21).
        rep["clean"] = False
        rep["unreadable_rows"] = dropped_rows
    d = _out.Doc(exact=True, width_=0, live=True)  # each line written where its print() was
    if args.json:
        _dump_json(rep)
        d.blank()
    else:
        d.line(render_brief(rep, args.tolerance, args.country)
               if args.brief else render(rep, args.tolerance, args.country))
        if dropped_rows and not args.brief:
            from taxjson.lib.out import fill
            d.blank()
            d.line(fill(f"NOT RECONCILED: {dropped_rows} slip row(s) had "
                        f"an unreadable cell (see the warnings above)."))
        elif dropped_rows:
            from taxjson.lib.out import act
            d.line(act(f"NOT RECONCILED: {dropped_rows} slip row(s) had an unreadable cell (warnings above)"))
    return 0 if rep["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
