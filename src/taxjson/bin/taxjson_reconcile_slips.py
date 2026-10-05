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
refused (a USD Webull T5008: convert boxes 20/21 to CAD first).
An unreadable proceeds, quantity or cost cell (a decimal comma '1234,56',
'nan', '1 500.00 CAD') is reported and fails the check.

Exit codes: 0 = everything reconciled; 1 = at least one mismatch or missing
symbol; 2 = usage error.

Usage:
    taxjson-reconcile-slips t5008.csv --country canada \
        --gains margin_gains.json [--gains ...]
        [--year 2025] [--tolerance 1.00] [--json]

Or through the project wrapper: `taxjson reconcile-slips t5008.csv`.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.bin.taxjson_form_export import grant_buyback_units, load_json
from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.country import CANADA, USA


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
from taxjson.lib.numeric import nonneg_float_arg

# Every known listing suffix (lib/markets; .VN was missing here).
from taxjson.lib.markets import listing_suffix_re  # noqa: E402
_SUFFIX_RE = listing_suffix_re()

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

# Broker option descriptions -> OCC (R1-209): IB prints
# "SAMPLE 21MAR25 50 C", Webull "CALL SAMPLE03/21/25 50"; the books carry
# SAMPLE250321C00050000.
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
     "NOV", "DEC"), 1)}
# A strike with or without thousands separators ('5,000.00', R1-170 /
# A2-1113 — the parsers' OPTION_STRIKE_RE accepts it); a decimal comma
# ('2,50') does not match and the cell stays a plain (unmatched) symbol.
_STRIKE = r"((?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d+)?)"
_IB_OPT_RE = re.compile(
    r"^([A-Z][A-Z0-9.]*)\s+(\d{1,2})([A-Z]{3})(\d{2})\s+"
    + _STRIKE + r"\s+([CP])$")
_WB_OPT_RE = re.compile(
    r"^(CALL|PUT)\s+([A-Z][A-Z0-9.]*?)\s*(\d{2})/(\d{2})/(\d{2})\s+"
    + _STRIKE + r"$")
_PADDED_OCC_RE = re.compile(r"^([A-Z][A-Z0-9.]*)\s+(\d{6}[CP]\d{8})$")


def _occ(root: str, yy: str, mm: int, dd: int, cp: str,
         strike: str) -> str:
    return (f"{root}{yy}{mm:02d}{dd:02d}{cp}"
            f"{int(round(float(strike.replace(',', '')) * 1000)):08d}")


def split_listing(sym: str) -> Tuple[str, str]:
    """(root, market suffix or '') of a normalized symbol."""
    m = _SUFFIX_RE.search(sym or "")
    if not m:
        return sym, ""
    return sym[:m.start()], m.group(1).upper()


def slip_symbol(raw: str) -> str:
    """A slip cell as a book-style symbol, KEEPING any listing suffix
    the slip wrote (SAMPLB.TO stays apart from SAMPLB.US — R1-292): upper-
    cased, broker option descriptions rewritten to OCC and share-class
    separators ('SAMPLC B', 'SAMPLC/B', 'SAMPLC-B') to the books' dot form."""
    s = (raw or "").replace("\ufeff", "").strip().upper().lstrip(".")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return ""
    m = _IB_OPT_RE.match(s)
    if m and m.group(3) in _MONTHS:
        return _occ(m.group(1), m.group(4), _MONTHS[m.group(3)],
                    int(m.group(2)), m.group(6), m.group(5))
    m = _WB_OPT_RE.match(s)
    if m:
        return _occ(m.group(2), m.group(5), int(m.group(3)),
                    int(m.group(4)), m.group(1)[0], m.group(6))
    m = _PADDED_OCC_RE.match(s)
    if m:
        return m.group(1) + m.group(2)
    root, sfx = split_listing(s)
    if re.fullmatch(r"[A-Z0-9]+(?:[ /\-.][A-Z0-9]+)+", root):
        root = re.sub(r"[ /\-.]", ".", root)
    return f"{root}.{sfx}" if sfx else root


def norm_symbol(sym: str) -> str:
    return _SUFFIX_RE.sub("", (sym or "").strip().upper().lstrip("."))


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
    claimed: set = set()
    where = f"{path}: " if path else ""
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
            mapping[key] = exact[0]
            claimed.add(exact[0])
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
                mapping[key] = hits[0]
                claimed.add(hits[0])
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
    from taxjson.bin.taxjson_ticker_map import map_symbol

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


def _bump(rec: Dict[str, Any], sfx: str, **vals) -> None:
    """Add vals to rec and to rec['listings'][sfx]."""
    sub = rec["listings"].setdefault(
        sfx, {k: (None if k == "cost" else 0.0) for k in rec
              if k != "listings"})
    for tgt in (rec, sub):
        for k, v in vals.items():
            if k == "cost":
                if v is not None:
                    tgt["cost"] = (tgt["cost"] or 0.0) + v
            else:
                tgt[k] += v


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
            raise SystemExit(f"taxjson-reconcile-slips: {path} is empty")
        cols = _map_headers(list(reader.fieldnames), path)
        for required in ("symbol", "proceeds"):
            if required not in cols:
                raise SystemExit(
                    f"taxjson-reconcile-slips: {path} has no recognizable "
                    f"{required!r} column (headers: "
                    f"{', '.join(reader.fieldnames)}). See --help for "
                    f"accepted spellings.")
        out: Dict[str, Dict[str, Any]] = {}
        dropped = 0
        foreign: Dict[str, int] = {}

        def _drop(what: str) -> None:
            nonlocal dropped
            # A slip row the tool cannot read is a row it cannot
            # RECONCILE — silently dropping it certified a disagreeing
            # slip as fully reconciled at exit 0 (REVIEW #21, R1-201).
            dropped += 1
            print(f"taxjson-reconcile-slips: warning: {path.name}: "
                  f"{what} — row NOT reconciled; fix the slip CSV cell.",
                  file=sys.stderr)

        for lineno, row in enumerate(reader, 2):
            cells = {k: (row.get(cols[k]) or "").strip()
                     for k in ("symbol", "quantity", "proceeds", "cost",
                               "currency")
                     if k in cols}
            ccy = _norm_currency(cells.get("currency", ""))
            if ccy and base_currency and ccy != base_currency.upper():
                foreign[ccy] = foreign.get(ccy, 0) + 1
                continue
            sym = rename(slip_symbol(cells["symbol"]))
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
                                        "listings": {}})
            _bump(rec, sfx, rows=1, proceeds=proceeds,
                  qty=abs(q) if q is not None else 0.0, cost=c)
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


def load_computed(gains_paths: List[Path],
                  year: Optional[int],
                  date_basis: str = "settle") -> Dict[str, Dict[str, Any]]:
    """Aggregate computed dispositions per symbol root:
    {ROOT: {qty, proceeds_net, proceeds_gross, cost, rows, tainted_rows,
    listings}} (listings: the same per listing suffix — SAMPLB.TO and
    SAMPLB.US are different securities, R1-292).
    Tainted rows are INCLUDED in the counts here (the broker's slip will
    include those sales too) but flagged so a basis mismatch on a tainted
    symbol reads as expected, not alarming."""
    out: Dict[str, Dict[str, Any]] = {}
    ystr = str(year) if year else None
    grant_qty: Dict[Tuple[str, str], float] = {}
    grant_gross: Dict[Tuple[str, str], float] = {}
    grant_net: Dict[Tuple[str, str], float] = {}
    short_close_qty: Dict[Tuple[str, str], float] = {}
    for p in gains_paths:
        data = load_json(p)
        # Tainted (unknown-cost) dispositions live in
        # manual_reporting_required, NOT transactions — the pipeline
        # strips them there before writing the gains file, which made
        # every tainted_rows counter in this tool a permanent no-op
        # and a real broker-reported sale show as
        # MISSING_FROM_COMPUTED with the wrong diagnosis (2026-09
        # audit). Fold them back in, flagged.
        _manual = [dict(e, tainted=True)
                   for e in data.get("manual_reporting_required", [])
                   if e.get("qty")]
        for e in list(data.get("transactions", [])) + _manual:
            if e.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU"):
                continue
            # Manual (unknown-cost) rows carry qty and proceeds but no
            # `gain` (the pipeline strips it); requiring `gain` dropped
            # every one of them again (R1-206).
            if "qty" not in e or ("gain" not in e and not e.get("tainted")):
                continue
            # Year-scope on the same basis the gains files (and the
            # broker's slip) use: the IRS recognizes on TRADE date, so a
            # 1099-B includes a Dec-31 sale settling in January — the
            # unconditional settle-first read excluded it and produced
            # spurious MISMATCH rows against the tool's own form-export.
            # CRA times dispositions on SETTLEMENT date (the default).
            if date_basis == "trade":
                date = e.get("date") or e.get("date_settle") or ""
            else:
                date = e.get("date_settle") or e.get("date") or ""
            if ystr and not date.startswith(ystr):
                continue
            full = (e.get("symbol") or "").strip().upper().lstrip(".")
            root, sfx = split_listing(full)
            if not root:
                continue
            # open_grant*: this year's grant-timing writes still open at
            # the year end (premium reported now, s.49(1); the broker's
            # slip reports it at the close). prior_grant: the premium of
            # an EARLIER year's grant write this year's buy-back closed —
            # the broker's close-year slip carries it as proceeds
            # (A2-0657).
            rec = out.setdefault(root, {"qty": 0.0, "proceeds_net": 0.0,
                                        "proceeds_gross": 0.0, "cost": 0.0,
                                        "rows": 0, "tainted_rows": 0,
                                        "open_grant_qty": 0.0,
                                        "open_grant": 0.0,
                                        "open_grant_net": 0.0,
                                        "prior_grant": 0.0,
                                        "listings": {}})
            proceeds = float(e.get("proceeds") or 0.0)
            cost = float(e.get("cost") or 0.0)
            outlays = float(e.get("commission") or 0.0) + \
                float(e.get("fee") or 0.0)
            short = (e.get("direction") or "LONG") == "SHORT"
            if short:
                # Engine short convention: `cost` holds the (negated)
                # short-sale proceeds and `proceeds` the (negated)
                # cover cost — the SWAP form-export performs. Merely
                # stripping signs compared the slip's proceeds against
                # the COVER COST, a false MISMATCH equal to the gain
                # (2026-09 audit).
                proceeds, cost = abs(cost), abs(proceeds)
                if e.get("grant") and -float(e.get("cost") or 0.0) \
                        + outlays >= 0:
                    # A grant-timing WRITE: the row's commission is the
                    # write's own, so the gross premium is the net plus
                    # it (form-export's Schedule 3 shows the same gross,
                    # R1-40). A buy-back's fee is a cost, never proceeds.
                    proceeds = -float(e.get("cost") or 0.0)
                else:
                    outlays = 0.0
            q = abs(float(e.get("qty") or 0.0))
            # Grant timing books a written option twice — the WRITE
            # record and its buy-back/expiry — while the slip reports
            # ONE disposition of those contracts: quantity counts once
            # (R1-18). Settled after the loop.
            key = (root, sfx)
            if e.get("grant"):
                grant_qty[key] = grant_qty.get(key, 0.0) + q
                grant_gross[key] = (grant_gross.get(key, 0.0)
                                    + proceeds + outlays)
                grant_net[key] = grant_net.get(key, 0.0) + proceeds
                q = 0.0
            elif short:
                short_close_qty[key] = (short_close_qty.get(key, 0.0)
                                        + grant_buyback_units(e, year))
                if ystr:
                    _prior = sum(
                        float((v or {}).get("premium") or 0.0)
                        for y, v in (e.get("grant_closed") or {}).items()
                        if str(y) < ystr)
                    if _prior:
                        _bump(rec, sfx, prior_grant=_prior)
            _bump(rec, sfx, qty=q, proceeds_net=proceeds,
                  proceeds_gross=proceeds + outlays, cost=cost, rows=1,
                  tainted_rows=1 if e.get("tainted") else 0)
    for (root, sfx), gq in grant_qty.items():
        extra = max(0.0, gq - short_close_qty.get((root, sfx), 0.0))
        if extra:
            # Written this year, still open (or closed next year): the
            # write itself is the year's disposition.
            f = extra / gq if gq else 0.0
            _bump(out[root], sfx, qty=extra, open_grant_qty=extra,
                  open_grant=grant_gross.get((root, sfx), 0.0) * f,
                  open_grant_net=grant_net.get((root, sfx), 0.0) * f)
    return out


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
        grant_note.append(f"{_open:,.2f} premium of written option(s) "
                          f"still open at the year end is reported this "
                          f"year (grant timing, s.49(1)) but not on this "
                          f"year's slip")
    if abs(_prior) > 0.005:
        grant_note.append(f"slip proceeds include {_prior:,.2f} premium "
                          f"of earlier-year write(s) already reported in "
                          f"the write year (grant timing, s.49(1)) — not "
                          f"income again")
    if abs(d_gross) <= tolerance:
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
        problems.append(f"proceeds off by {closer:+,.2f} "
                        f"(slip {s['proceeds']:,.2f} vs computed "
                        f"gross {c['proceeds_gross']:,.2f} / net "
                        f"{c['proceeds_net']:,.2f})")
        if _timed:
            problems.extend(grant_note)
    _oq = float(c.get("open_grant_qty") or 0.0)
    if s["qty"] > 0 and abs(s["qty"] - c["qty"]) > 1e-4 \
            and not (_oq > 1e-9 and abs(s["qty"] - (c["qty"] - _oq)) <= 1e-4):
        problems.append(f"quantity off by {s['qty'] - c['qty']:+,.4f} "
                        f"(slip {s['qty']:,.4f} vs computed "
                        f"{c['qty']:,.4f})")
    if s.get("cost") is not None:
        d_cost = s["cost"] - c["cost"]
        if abs(d_cost) > tolerance:
            notes.append(f"slip cost differs by {d_cost:+,.2f} — often "
                         f"legitimate ({_WORDS[country]['cost_note']}); "
                         f"document the reason")
    if c.get("tainted_rows"):
        notes.append(f"{int(c['tainted_rows'])} disposition(s) with an "
                     f"unknown cost (no purchase in your files) included")
    return {"symbol": label, "status": "MISMATCH" if problems else "OK",
            "detail": "; ".join(problems + notes)}


def _missing_from_slip(label: str, c: Dict[str, Any],
                       tolerance: float) -> Dict[str, Any]:
    _open = float(c.get("open_grant") or 0.0)
    if abs(_open) > 0.005 and \
            abs(c["proceeds_gross"] - _open) <= max(tolerance, 0.005):
        # Only this year's grant-timing writes still open at the year
        # end (A2-0657): the premium is reported this year (s.49(1));
        # the broker reports the contract on the slip of the year it
        # closes.
        return {"symbol": label, "status": "NO_SLIP_EXPECTED",
                "detail": f"written option(s) still open at the year end: "
                          f"the {_open:,.2f} premium is reported this year "
                          f"without a slip (grant timing, s.49(1)); the "
                          f"broker's slip reports it when the contract "
                          f"closes"}
    if abs(c["proceeds_gross"]) <= min(tolerance, 0.005):
        # Nil proceeds — a long option that expired worthless. IB
        # issues no T5008 row for it; that is expected, not a gap
        # (R1-1), so it does not fail the reconciliation.
        return {"symbol": label, "status": "NO_SLIP_EXPECTED",
                "detail": f"computed {c['rows']} disposition(s) with nil "
                          f"proceeds (expired option) — brokers usually "
                          f"issue no slip row"}
    return {"symbol": label, "status": "MISSING_FROM_SLIP",
            "detail": f"computed {c['rows']} disposition(s), "
                      f"proceeds {c['proceeds_gross']:,.2f} — "
                      f"no slip row (missing slip, or a "
                      f"non-slip disposition like a corp action)"}


def _label(root: str, sfx: str) -> str:
    return f"{root}.{sfx}" if sfx else root


def reconcile(slip: Dict[str, Dict[str, Any]],
              computed: Dict[str, Dict[str, Any]],
              tolerance: float, country: str = CANADA) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
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
                                   f"{s['proceeds']:,.2f} — nothing "
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
                                       f"{s_list[sfx]['proceeds']:,.2f} — "
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
                                       f"{s_list['']['proceeds']:,.2f} — "
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
            },
            "clean": not failing}


def render(rep: Dict[str, Any], tolerance: float,
           country: Optional[str] = None) -> str:
    lines = ["SLIP RECONCILIATION — computed dispositions vs broker tax "
             "slips", ""]
    if not rep["rows"]:
        lines.append("Nothing to reconcile (no symbols on either side).")
        return "\n".join(lines)
    width = max(len(r["symbol"]) for r in rep["rows"])
    swidth = max(len(r["status"]) for r in rep["rows"])
    for r in rep["rows"]:
        lines.append(f"{r['symbol'].ljust(width)}  "
                     f"{r['status'].ljust(swidth)}  {r['detail']}".rstrip())
    c = rep["counts"]
    lines.append("")
    lines.append(f"{c['ok']} OK, {c['mismatch']} mismatch, "
                 f"{c['missing_from_computed']} missing from computed, "
                 f"{c['missing_from_slip']} missing from slip"
                 + (f", {c['ambiguous_listing']} ambiguous listing"
                    if c.get("ambiguous_listing") else "")
                 + (f", {c['no_slip_expected']} with no slip row "
                    f"expected (not a failure)"
                    if c.get("no_slip_expected") else "")
                 + f" (tolerance ±{tolerance:,.2f}).")
    lines.append("")
    lines.append("Notes:")
    if country == "usa":
        lines.append("  - MISSING_FROM_SLIP can be benign: corp-action "
                     "dispositions don't always get 1099-B rows. "
                     "Worthless option expiries are listed as "
                     "NO_SLIP_EXPECTED and do not fail the check.")
    else:
        lines.append("  - MISSING_FROM_SLIP can be benign: corp-action "
                     "dispositions don't always get T5008 rows. "
                     "Worthless option expiries, and options written "
                     "this year under grant timing and still open at "
                     "the year end, are listed as NO_SLIP_EXPECTED and "
                     "do not fail the check.")
    lines.append("  - A slip symbol without a listing suffix matches every "
                 "listing of that root; when the books hold two (SAMPLB.TO "
                 "CDR and SAMPLB.US), write the suffix in the slip CSV.")
    lines.append("  - Slips aggregated per type code (IB's SHS/OPC/FUT "
                 "rows identified 'Various') cannot be compared per "
                 "security: transcribe a per-security CSV.")
    if country == "usa":
        lines.append("  - Slip cost (1099-B box 1e) is the broker's basis "
                     "for the lots it sold; it can differ from taxjson's "
                     "when a wash sale crossed accounts (box 1g covers "
                     "only the broker's own) or shares came in by "
                     "transfer — document it, don't 'fix' it.")
    else:
        lines.append("  - Slip cost (T5008 box 20) is per-broker book value; a "
                     "difference from blended ACB is expected when you hold the "
                     "security at more than one broker — document it, don't "
                     "'fix' it.")
    if country == "usa":
        lines.append("  - Amounts are compared in the project base "
                     "currency; a slip whose currency column names "
                     "another currency is refused, not converted.")
    else:
        lines.append("  - Amounts are compared in the project base "
                     "currency; a slip whose currency column (T5008 Box "
                     "13) names another currency is refused, not "
                     "converted.")
    return "\n".join(lines)


# Guarded here as well as by the console script: `taxjson reconcile-slips`
# calls this main directly, and an unreadable ticker.map was a traceback
# there while `taxjson-reconcile-slips` printed one line (A2-0161).
@guard_main("taxjson-reconcile-slips")
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="taxjson-reconcile-slips",
        description=__doc__,
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
                             "(canada: settle, usa: trade). The `taxjson "
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
                     "slip wording (T5008 vs 1099-B). The `taxjson "
                     "reconcile-slips` wrapper passes it.")
    parser.add_argument("--tolerance", type=nonneg_float_arg, default=1.00,
                        help="Absolute per-symbol amount tolerance "
                             "(default: 1.00)")
    parser.add_argument("--json", action="store_true",
                        help="Emit the report as JSON instead of text")
    parser.add_argument("--ticker-map", type=Path, default=None,
                        help="The project's ticker.map: slip symbols are "
                             "renamed the way the books were (SAMPLK -> SAMPLJ, "
                             "SAMPLD -> SAMPLD.B). The `taxjson reconcile-slips` "
                             "wrapper passes it automatically.")
    args = parser.parse_args(argv)

    for sp in args.slip_csv:
        if not sp.exists():
            print(f"taxjson-reconcile-slips: no such file: {sp}",
                  file=sys.stderr)
            return 2
        if not sp.is_file():
            # A directory raised IsADirectoryError (S036-09).
            print(f"taxjson-reconcile-slips: {sp}: not a file",
                  file=sys.stderr)
            return 2
    for p in args.gains:
        if not p.exists():
            print(f"taxjson-reconcile-slips: no such file: {p}",
                  file=sys.stderr)
            return 2

    # One slip per broker is the norm (R1-207): each file on its own
    # reported every other broker's sales as MISSING_FROM_SLIP.
    renames = None
    if args.ticker_map is not None:
        from taxjson.bin.taxjson_ticker_map import (load_map_file,
                                                    merge_renames)
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
                                             "listings": {}})
                for sfx, sub in rec["listings"].items():
                    _bump(acc, sfx, qty=sub["qty"],
                          proceeds=sub["proceeds"], rows=sub["rows"],
                          cost=sub["cost"])
    except SlipRefused as e:
        print(e.code, file=sys.stderr)
        return 2
    from taxjson.lib.json_input import InputFileError
    try:
        computed = load_computed(args.gains, args.year, args.date_basis)
    except InputFileError as e:
        # One line naming the gains file, not a traceback (S079-11).
        print(f"taxjson-reconcile-slips: error: {e}", file=sys.stderr)
        return 2
    rep = reconcile(slip, computed, args.tolerance, args.country)
    if dropped_rows:
        # Unreadable rows mean the slip was NOT fully reconciled —
        # exit 0 here certified agreement the tool never checked
        # (REVIEW #21).
        rep["clean"] = False
        rep["unreadable_rows"] = dropped_rows
    if args.json:
        json.dump(rep, sys.stdout, indent=2, sort_keys=True)
        print()
    else:
        print(render(rep, args.tolerance, args.country))
        if dropped_rows:
            print(f"\nNOT RECONCILED: {dropped_rows} slip row(s) had "
                  f"an unreadable cell (see warnings above).")
    return 0 if rep["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
