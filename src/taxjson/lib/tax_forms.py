"""The form rows a gains document's dispositions land on: Schedule 3's lines
and periods (schedule3_line, schedule3_period, build_schedule3), Form
8949's parts and boxes (build_8949), each disposition's property class
(property_class, mark_crypto), and the counts the forms and the slip check
share (grant_buyback_units, short_carried_loss). bin/taxjson_form_export.py
writes the forms; close-year's lock, the carryover and the slip check read
them from here.
"""
from taxjson.lib.out import exit_text
from datetime import datetime, timedelta
from pathlib import Path
from taxjson.lib.option_symbols import is_option_symbol
from taxjson.model.result import GainRow
from taxjson.model import money as M
from taxjson.lib.futures import is_plain_future, section_1256_kind
from taxjson.lib.numeric import round_half_up
from typing import Any, Dict, List, Optional, Tuple
from taxjson.lib.futures_prefixes import FUTURES_PREFIXES
from taxjson.lib.report_fmt import fmt_qty as _qty_str


_EPS = 0.005


# Thin alias so existing importers (tests, taxjson_reconcile_slips)
# keep working; the implementation is the shared report-layer loader.
def load_json(path: Path) -> Any:
    # The shared input contract (lib/json_input): the message names the
    # file (R1-277), a bare array is a transaction list and any other
    # non-object is refused instead of an AttributeError traceback
    # (audit S079-11). InputFileError is a ValueError.
    from taxjson.lib.json_input import read_json_doc, require_gains_doc
    # A document with no 'transactions' list, or a pre-gains stage file
    # (work/<acct>_base.json), rendered a $0 Schedule 3 at exit 0
    # (S033-01).
    return require_gains_doc(read_json_doc(path), path)


def _acquired_date(e: Dict[str, Any]) -> str:
    """The lot's ACTUAL purchase date when the engine emitted one
    (matches the broker's 1099-B date-acquired; the derived fallback
    below backdates wash-replacement lots by the §1223(3) tacking and
    won't reconcile). Fallback: sale date minus days_held; sale date
    when days_held is missing/zero (same-day round trip)."""
    if e.get("acquired_date"):
        return str(e["acquired_date"])
    date = e.get("date") or ""
    days = int(e.get("days_held") or 0)
    if not date or days <= 0:
        return date
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return date
    return (dt - timedelta(days=days)).strftime("%Y-%m-%d")


def _column_b(e: Dict[str, Any], direction: str, sold: str) -> str:
    """Form 8949's date acquired: a short sale shows the cover date (as
    its date sold), but one closed by delivering a lot held (a move
    between your accounts: the engine's `closed_by_delivery`) the date
    that lot was acquired, as the form's instructions for short sales
    say (backlog TJ-3451)."""
    if direction == "SHORT" and not e.get("closed_by_delivery"):
        return sold
    return _acquired_date(e)


# ---------------------------------------------------------------- 8949

def _cents(x: float) -> float:
    """One money cell: half-up to the cent on the stored value (model/
    money round_half_up — the project's presentation rounding). The
    binary round() put a half-cent denial on (g) as 530.42 while the
    allowed gain elsewhere read -530.42 (A2-1105). `+ 0.0` kills -0.00."""
    return round_half_up(float(x or 0.0), 2) + 0.0


# Form 8949 checkboxes by part: securities on A/B/C (short-term) and
# D/E/F (long-term); from tax year 2025 digital assets on their own
# G/H/I and J/K/L (2025 Instructions for Form 8949: "Use box G, H, or I
# to report short-term digital asset transactions. Do not use box C";
# A2-0482, A2-0829). Digital assets are the dispositions of `crypto =
# true` accounts (flagged `_crypto` by mark_crypto).
DIGITAL_ASSET_BOXES_FROM = 2025
_BOXES = {("I", False): "A/B/C", ("II", False): "D/E/F",
          ("I", True): "G/H/I", ("II", True): "J/K/L"}


def _filing_position(e: Dict[str, Any], country: str
                     ) -> Optional[Dict[str, Any]]:
    """The row's `loss_override` (a .tt ALLOWLOSS filing position,
    lib/loss_overrides: CA-SL-18 / US-WASH-25) with its sentence, or
    None. The gain on the row is the claimed (allowed) loss; the note
    says what the rule would have denied."""
    n = e.get("loss_override")
    if not isinstance(n, dict) or not n.get("where"):
        return None
    from taxjson.lib.loss_override_note import note_text
    return {"where": n.get("where"), "reason": n.get("reason") or "",
            "would_disallow": _cents(n.get("would_disallow") or 0.0),
            "would_permanent": _cents(n.get("would_permanent") or 0.0),
            "note": note_text(n, country)}


def _merge_positions(rows_fp: List[Dict[str, Any]], country: str
                     ) -> List[Dict[str, Any]]:
    """Several rows' positions summed per ALLOWLOSS line (a Schedule 3
    row aggregates a symbol's sales), each with its sentence."""
    from taxjson.lib.loss_override_note import note_text
    by: Dict[str, Dict[str, Any]] = {}
    for fp in rows_fp:
        cur = by.setdefault(fp["where"], {"where": fp["where"],
                                          "reason": fp["reason"],
                                          "would_disallow": 0.0,
                                          "would_permanent": 0.0})
        cur["would_disallow"] += fp["would_disallow"]
        cur["would_permanent"] += fp["would_permanent"]
    out = []
    for cur in by.values():
        cur["would_disallow"] = _cents(cur["would_disallow"])
        cur["would_permanent"] = _cents(cur["would_permanent"])
        cur["note"] = note_text(cur, country)
        out.append(cur)
    return out


def short_carried_loss(e: Dict[str, Any]) -> float:
    """The earlier washed losses a US short sale's row carries off its
    opening proceeds (the engine's `deferred_loss_recovered` on a SHORT
    row with a term): Form 8949 and the 1099-B have them in (e) cost,
    never out of (d) proceeds. 0.0 for a long row or a Canadian one."""
    if (e.get("direction") or "LONG") != "SHORT" \
            or e.get("term") not in ("SHORT_TERM", "LONG_TERM"):
        return 0.0
    try:
        v = float(GainRow(e).deferred_loss_recovered or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0.0 else 0.0


def build_8949(entries: List[Dict[str, Any]],
               year: Optional[int] = None) -> Dict[str, Any]:
    """Form 8949 rows by part. `year` (default: the latest year among
    the rows) decides whether `_crypto` rows go on the digital-asset
    boxes (2025 and later) or with the securities."""
    if year is None:
        year = _infer_year(entries)
    da_boxes = year is not None and int(year) >= DIGITAL_ASSET_BOXES_FROM
    parts: Dict[str, List[Dict[str, Any]]] = {"I": [], "II": []}
    sec1256: List[Dict[str, Any]] = []
    drift_warned = 0
    for e in entries:
        g = GainRow(e)
        term = e.get("term")
        if term not in ("SHORT_TERM", "LONG_TERM"):
            raise SystemExit(
                exit_text("taxjson-form-export: entry for "
                          f"{e.get('symbol')!r} on {e.get('date')!r} has no ST/LT "
                          "term — Form 8949 needs a country=usa gains file "
                          "(Canada has no term concept; use --form schedule3)."))
        _kind = section_1256_kind(str(e.get("symbol") or ""))
        if _kind:
            # A §1256 contract (a future, an option on one, a broad-
            # based index option) is reported on Form 6781 — 60/40,
            # marked to market at year end — never on Form 8949; Form
            # 6781 is by hand (tax-logic US-FUT-02 / US-OPT-04; the
            # estimate and carryover split its net, US-FUT-03). It
            # went on Part I as a covered short-term sale, a futures
            # loss as NEGATIVE proceeds (A2-0118, A2-0322, A2-0323): a
            # user following tax-logic reported it twice. Kept off the
            # parts and their totals, listed for Form 6781 by hand.
            sec1256.append({
                "description": str(e.get("symbol") or ""),
                "kind": _kind,
                "date_acquired": (_acquired_date(e)
                                  if (e.get("direction") or "LONG")
                                  != "SHORT" else e.get("date") or ""),
                "date_sold": e.get("date") or "",
                "gain": _cents(g.gain),
                "account": e.get("account") or "",
            })
            continue
        part = "I" if term == "SHORT_TERM" else "II"
        proceeds = float(g.proceeds or 0.0)
        cost = float(g.cost or 0.0)
        direction = e.get("direction") or "LONG"
        if direction == "SHORT":
            # Engine signed short convention (FUZZ #E): `cost` holds the
            # NEGATED opening short-sale proceeds and `proceeds` the
            # NEGATED buy-to-cover cost, so gain = proceeds - cost is
            # direction-free inside the engine. The FILED row wants the
            # real-world figures — (d) what the short sale brought in,
            # (e) what covering cost — so the fields un-negate AND swap.
            # (h) = (d) - (e) + (g) is preserved: the swap+negation
            # leaves the difference unchanged (which is also why the
            # drift check below never caught the raw rendering).
            proceeds, cost = -cost + 0.0, -proceeds + 0.0   # +0.0 kills -0.00 rendering
            # A re-short that replaced an earlier short's washed loss
            # carries that loss off its opening proceeds in the engine
            # (`deferred_loss_recovered`). The 1099-B reports what the
            # short sale brought in, and a §1091(d) add is basis: (d)
            # gets the carried amount back and (e) takes it, (h)
            # unchanged (backlog TJ-0465: (d) was below the slip by it).
            _carried = short_carried_loss(e)
            proceeds, cost = proceeds + _carried, cost + _carried
        if (is_plain_future(str(e.get("symbol") or ""))
                and (proceeds < 0 or cost < 0)):
            # A security future off §1256 (ticker.map SECFUT, TJ-0730)
            # is booked on its settled P/L (lib/futures): a loss came
            # out as negative proceeds, a short's gain as negative cost.
            # The row shows the P/L as the T5008 shape does: a gain as
            # (d), a loss as (e); (h) unchanged.
            _pl = proceeds - cost
            proceeds, cost = max(_pl, 0.0) + 0.0, max(-_pl, 0.0) + 0.0
        adj = float(g.disallowed_amount or 0.0)
        gain = float(g.gain or 0.0)
        code = "W" if adj > _EPS else ""
        if adj < -_EPS:
            # A negative disallowance is not a shape this renderer can
            # file (code W adjustments are positive); rendering would
            # silently disagree with the engine's allowed gain.
            from taxjson.lib.out import warn as _warn
            _warn(f"{e.get('symbol')} {e.get('date')}: NEGATIVE "
                  f"disallowed_amount {M.shown(adj, 2):.2f} — row rendered without an "
                  f"adjustment", details=["Inspect it before filing."])
        # (h) must equal (d) - (e) + (g) with the RENDERED adjustment;
        # the engine's allowed gain is the authority — surface drift
        # instead of silently printing either.
        rendered_adj = adj if code else 0.0
        if abs((proceeds - cost + rendered_adj) - gain) > 0.02:
            drift_warned += 1
        sold = e.get("date") or ""
        acquired = _column_b(e, direction, sold)
        if direction == "SHORT":
            sold = e.get("date") or ""
        desc = f"{_qty_str(abs(float(g.quantity or 0)))} {e.get('symbol')}"
        if e.get("deemed"):
            # §301(c)(3): no shares were sold (US-ROC-02); a row says
            # its own kind when it is another (US-WASH-22).
            desc = e.get("deemed_desc") or (
                f"{e.get('symbol')} nondividend distribution in excess "
                f"of basis (§301(c)(3))")
        if e.get("is_option"):
            desc += " (option)"
        if direction == "SHORT":
            desc += " (short sale)"
        # Every row FOOTS: (h) = (d) - (e) + (g) on the rounded cells,
        # (d) and (e) as the 1099-B reports them. Rounding the four
        # independently left rows (and the part totals, and the TXF
        # that carries only d/e/g) off by cents (S032-16); the engine
        # drift check above still compares the unrounded gain.
        _d, _e = _cents(proceeds), _cents(cost)
        _g = _cents(adj) if code else 0.0
        _da = bool(da_boxes and e.get("_crypto"))
        _fp = _filing_position(e, "usa")
        parts[part].append({
            "boxes": _BOXES[(part, _da)],
            "digital_asset": _da,
            "description": desc,
            "date_acquired": acquired,
            "date_sold": sold,
            "proceeds": _d,
            "cost": _e,
            "code": code,
            "adjustment": _g,
            "gain": M.shown(_d - _e + _g, 2) + 0.0,
            "account": e.get("account") or "",
            # The user's ALLOWLOSS position against §1091 (US-WASH-25):
            # no code W, no (g) — the row claims the loss; said here
            # and under the table (pre-release review M7).
            **({"filing_position": _fp} if _fp else {}),
        })
    if drift_warned:
        from taxjson.lib.out import warn as _warn
        _warn(f"{drift_warned} row(s) where (d)-(e)+(g) differs from the "
              f"engine's allowed gain by more than $0.02",
              details=["Inspect them before filing."])
    for rows in list(parts.values()):
        rows.sort(key=lambda r: (r["digital_asset"], r["date_sold"],
                                 r["description"]))
    sec1256.sort(key=lambda r: (r["date_sold"], r["description"]))

    def totals(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        return {
            "proceeds": M.shown(sum(r["proceeds"] for r in rows), 2),
            "cost": M.shown(sum(r["cost"] for r in rows), 2),
            "adjustment": M.shown(sum(r["adjustment"] for r in rows), 2),
            "gain": M.shown(sum(r["gain"] for r in rows), 2),
        }
    groups = []
    for part in ("I", "II"):
        for _da in (False, True):
            g_rows = [r for r in parts[part] if r["digital_asset"] == _da]
            if g_rows:
                groups.append({"part": part, "boxes": _BOXES[(part, _da)],
                               "digital_asset": _da,
                               "rows": len(g_rows),
                               "totals": totals(g_rows)})
    return {
        "form": "8949", "year": year,
        # Per checkbox group (a 2025+ digital-asset group apart): each
        # group is its own Form 8949 page(s) with its own totals.
        "groups": groups,
        "part_I": parts["I"], "part_I_totals": totals(parts["I"]),
        "part_II": parts["II"], "part_II_totals": totals(parts["II"]),
        # The engine's unrounded sums over the same rows (the export
        # rounds per row, as filed — A2-1108's note compares them), added
        # exactly: the gains files' four places, as v0.41.0 printed them
        # (a float sum printed a binary tail, TJ-4930).
        "gain_unrounded": M.exact_total(
            GainRow(e).gain for e in entries
            if not section_1256_kind(str(e.get("symbol") or ""))),
        "adjustment_unrounded": M.exact_total(
            v for v in (GainRow(e).disallowed_amount for e in entries
                        if not section_1256_kind(str(e.get("symbol") or "")))
            if float(v or 0.0) > _EPS),
        "section_1256": sec1256,
        "section_1256_totals": {
            "gain": M.shown(sum(r["gain"] for r in sec1256), 2) + 0.0,
            "dispositions": len(sec1256)},
    }


# ---------------------------------------------------------------- schedule 3

# Schedule 3 routes each disposition by PROPERTY TYPE (2025 form, Part 3;
# T4037 "Capital Gains", Chapter 2 "Completing Schedule 3", lines 4/6/7):
#   line 4  publicly traded shares, mutual fund units, ...   13199 / 13200
#   line 6  bonds, debentures, promissory notes and other     15199 / 15300
#           similar properties — T4037: "Other properties
#           include bad debts, foreign currencies and options"
#   line 7  crypto-assets (new for 2025)                      15200 / 15301
# For 2024 and earlier returns crypto-assets were reported with the
# other properties on 15199 / 15300 (there was no separate line).
# The 2024 form (5000-S3 E (24)) splits Part 3 by the date of the
# disposition (A2-0166): Period 1, January 1 to June 24, 2024, on
# 10689 / 10690 (shares) and 10693 / 10694 (bonds, crypto-assets and
# other properties), slip lines 17399 / 17599; Period 2, June 25 to
# December 31, on 13199 / 13200 and 15199 / 15300, slips 17400 / 17600.
_LINE_ORDER = ("shares_p1", "other_p1", "shares", "other", "crypto")
PERIOD_YEAR = 2024
PERIOD_1_END = "2024-06-24"
_PERIOD_TEXT = {1: "Period 1 (January 1 to June 24, 2024)",
                2: "Period 2 (June 25 to December 31, 2024)"}


def schedule3_period(e: Dict[str, Any], year: Optional[int],
                     date_key: str = "date_settle") -> Optional[int]:
    """1 or 2 for a disposition on the 2024 form (its date on the
    gains files' tax-date basis: settle by default, CA-DATE-01); None
    for every other year (one period)."""
    if year is None or int(year) != PERIOD_YEAR:
        return None
    d = str(e.get(date_key) or e.get("date_settle") or e.get("date") or "")
    return 1 if d[:10] <= PERIOD_1_END else 2


def schedule3_line(key: str, year: Optional[int],
                   period: Optional[int] = None) -> Dict[str, str]:
    """The Schedule 3 line a property class lands on for `year`.
    `key` is shares | other | crypto (or a period-1 key, shares_p1 |
    other_p1); crypto folds into `other` before 2025 (the separate
    crypto-assets line starts with the 2025 form). `period` (2024 only,
    schedule3_period) picks the 2024 form's Period 1 or Period 2
    codes."""
    new_form = year is None or int(year) >= 2025
    if key.endswith("_p1"):
        key, period = key[:-3], 1
    if key == "crypto" and not new_form:
        key = "other"
    split = (not new_form and int(year) == PERIOD_YEAR
             and period in (1, 2))
    p1 = split and period == 1
    tag = f" — {_PERIOD_TEXT[period]}" if split else ""
    if key == "shares":
        return {"key": "shares_p1" if p1 else "shares",
                "line": "4" if new_form else "",
                "label": "Publicly traded shares, mutual fund units, "
                         "deferral of eligible small business "
                         "corporation shares, and other shares" + tag,
                "short": "shares & fund units"
                         + (f" (P{period})" if split else ""),
                "period": period if split else None,
                "proceeds_code": "10689" if p1 else "13199",
                "gain_code": "10690" if p1 else "13200"}
    if key == "crypto":
        return {"key": "crypto", "line": "7",
                "label": "Crypto-assets", "short": "crypto-assets",
                "period": None,
                "proceeds_code": "15200", "gain_code": "15301"}
    return {"key": "other_p1" if p1 else "other",
            "line": "6" if new_form else "",
            "label": ("Bonds, debentures, promissory notes, and other "
                      "similar properties (options, futures, foreign "
                      "currency)" if new_form else
                      "Bonds, debentures, promissory notes, crypto-"
                      "assets, and other similar properties (options, "
                      "futures, foreign currency)") + tag,
            "short": ("options & other properties" if new_form else
                      "options, crypto & other properties")
                     + (f" (P{period})" if split else ""),
            "period": period if split else None,
            "proceeds_code": "10693" if p1 else "15199",
            "gain_code": "10694" if p1 else "15300"}


def line_title(spec: Dict[str, str]) -> str:
    """'Part 3, line 4 (13199/13200)' — or, for pre-2025 forms whose
    line numbering this tool does not pin, just the line codes."""
    codes = f"lines {spec['proceeds_code']}/{spec['gain_code']}"
    if spec.get("period"):
        return f"{_PERIOD_TEXT[spec['period']]}, {codes}"
    return (f"Part 3, line {spec['line']} ({codes})" if spec["line"]
            else codes)


def property_class(e: Dict[str, Any]) -> str:
    """shares | option | futures | crypto for one disposition. Crypto
    comes from the account (`crypto = true` books, flagged `_crypto` by
    the loader); futures carry the F:/ prefix the parsers give them
    (options on futures too); options are OCC-style symbols."""
    if e.get("_crypto"):
        return "crypto"
    sym = str(e.get("symbol") or "")
    if sym.startswith(FUTURES_PREFIXES):
        return "futures"
    if e.get("is_option") or is_option_symbol(sym):
        return "option"
    return "shares"


def _line_key(pclass: str) -> str:
    return {"shares": "shares", "crypto": "crypto"}.get(pclass, "other")


def mark_crypto(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copies of `entries` flagged as crypto-asset dispositions (the
    gains rows themselves carry no asset class; the account does)."""
    return [{**e, "_crypto": True} for e in entries]


def _infer_year(entries: List[Dict[str, Any]],
                date_key: str = "date_settle") -> Optional[int]:
    """The latest year among the rows, each dated as the gains files
    were scoped (`date_key`: date_settle, or date on a trade-date
    basis): a December trade settling in January is the trade's year
    under the IRS's trade date (backlog TJ-0901)."""
    ys = [str(e.get(date_key) or e.get("date_settle") or e.get("date")
              or "")[:4]
          for e in entries]
    ys = [int(y) for y in ys if y.isdigit()]
    return max(ys) if ys else None


def _foot_cells(proceeds_u: float, outlays_u: float,
                gain_u: float) -> Tuple[float, float, float, float]:
    """(proceeds, acb, outlays, gain) cents for one Schedule 3 row that
    FOOT exactly: PROCEEDS − ACB − OUTLAYS = GAIN. Each cell is rounded
    half-up on its own value (the .sum's convention — binary round()
    put a 1,469.575 ACB at 1,469.57 here and 1,469.58 there, A2-1104);
    the ACB is the residual. When the separately rounded cells leave the
    residual a cent below zero (a premium-only write: 27.672 / 13.836 /
    13.836 showed ACB -0.01, A2-0649), the ACB is 0.00 and the cent is
    absorbed by OUTLAYS (else by the gain) — no cell goes negative."""
    proceeds, outlays, gain = (_cents(proceeds_u), _cents(outlays_u),
                               _cents(gain_u))
    acb = M.shown(proceeds - outlays - gain, 2) + 0.0
    acb_u = proceeds_u - outlays_u - gain_u
    if acb < 0 and acb_u > -_EPS:
        acb = 0.0
        if proceeds - gain >= 0:
            outlays = M.shown(proceeds - gain, 2) + 0.0
        else:
            gain = M.shown(proceeds - outlays, 2) + 0.0
    return proceeds, acb, outlays, gain
def grant_buyback_units(e: Dict[str, Any], year: Optional[int]) -> float:
    """Units of a (non-grant) SHORT close row that buy back a grant-
    timing WRITE of the same year `year`: those contracts were disposed
    of once (the write), so Schedule 3 and reconcile-slips count them
    with the write, not again at the buy-back (R1-18, S003-04). A buy-
    back of an EARLIER year's write, or of a close-timing (pre-since)
    write, is a disposition of its own and counts (A2-0320, A2-0650,
    A2-0651). The engine names the write years a close consumed in
    `grant_closed`; a gains file written before that field nets every
    short close (the old count)."""
    gc = e.get("grant_closed")
    if isinstance(gc, dict):
        return sum(abs(float((v or {}).get("units") or 0.0))
                   for y, v in gc.items()
                   if year is None or str(y) == str(year))
    return abs(float(GainRow(e).quantity or 0.0))


def build_schedule3(entries: List[Dict[str, Any]],
                    year: Optional[int] = None,
                    date_key: str = "date_settle") -> Dict[str, Any]:
    """Per-property rows grouped by Schedule 3 line. Every row FOOTS:
    PROCEEDS − ACB − OUTLAYS = GAIN (the allowed gain), so a denied
    superficial loss shows as an ACB reduced by the denial — the denied
    amount is what gets added to the replacement property's ACB.
    `date_key`: the date the gains files were scoped on (date_settle,
    or date under tax_date = "trade"); on the 2024 form it also picks
    the Period 1 / Period 2 line codes (A2-0166), so a symbol sold in
    both periods is two rows."""
    if year is None:
        year = _infer_year(entries)
    recs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in entries:
        g = GainRow(e)
        symbol = e.get("symbol") or "?"
        pclass = property_class(e)
        lkey = schedule3_line(_line_key(pclass), year,
                              schedule3_period(e, year, date_key))["key"]
        rec = recs.setdefault((lkey, symbol), {
            "symbol": symbol, "units": 0.0, "acq_year": None,
            "proceeds": 0.0, "outlays": 0.0, "gain": 0.0,
            "denied": 0.0, "perm_denied": 0.0, "denied_contrib": 0.0,
            "short": False,
            "classes": set(), "n": 0, "grant_units": 0.0,
            "short_close_units": 0.0, "positions": [],
        })
        _fp = _filing_position(e, "canada")
        if _fp:
            rec["positions"].append(_fp)
        rec["classes"].add(pclass)
        rec["n"] += 1
        qty = abs(float(g.quantity or 0.0))
        proceeds = float(g.proceeds or 0.0)
        cost = float(g.cost or 0.0)
        gain = float(g.gain or 0.0)
        outlays = float(g.commission or 0.0) + float(g.fee or 0.0)
        if outlays < 0:
            # A net REBATE (IB, Questrade: a negative commission/fee)
            # is not an outlay or expense: it stays netted in the
            # proceeds, so the OUTLAYS cell is never negative (A2-0653,
            # A2-1053; tax software can reject a negative outlay). The
            # gain is unchanged.
            outlays = 0.0
        direction = e.get("direction") or "LONG"
        if pclass == "futures" and is_plain_future(symbol):
            # A futures contract is booked on its settled P/L
            # (lib/futures.py): nothing is paid to open one, so the
            # notional is neither proceeds nor ACB. Each closed contract
            # shows the T5008 shape — a gain as PROCEEDS, a loss as ACB
            # (the P/L before any superficial-loss denial; the footing
            # ACB below absorbs a denial the usual way). Commissions are
            # inside the P/L, so no separate outlays.
            pl = (float(e["raw_gain"]) if e.get("raw_gain") is not None
                  else gain)
            rec["proceeds"] += max(pl, 0.0)
        elif direction == "SHORT":
            # Engine signed short convention (FUZZ #E): `cost` is the
            # NEGATED opening short-sale proceeds and `proceeds` the
            # NEGATED buy-to-cover cost. Filing wants the real-world
            # mapping — disposition PROCEEDS = what the short sale
            # brought in, ACB = what covering cost — so the fields swap
            # as they un-negate (the ACB itself is derived below).
            rec["short"] = True
            _sold = -cost           # what the short sale brought in (net)
            if e.get("grant") and _sold + outlays >= 0:
                # A WRITE under grant timing (s.49(1)): the row's
                # commission/fee is the write's own, netted into the
                # premium. Show the premium GROSS with the commission
                # as an outlay, as for a sale (audit R1-40, CA-DISP-06;
                # gain unchanged). A buy-back row's fee is a cost of
                # acquiring the cover and stays in the ACB.
                rec["proceeds"] += _sold + outlays
                rec["outlays"] += outlays
            elif _sold >= 0:
                rec["proceeds"] += _sold
            else:
                # A write for a net DEBIT (commission above the premium):
                # abs() turned the debit into proceeds and invented an ACB
                # of twice it (S032-19). Nothing was received: the debit
                # is an outlay, the ACB (the cover) is what it cost.
                rec["outlays"] += -_sold
        else:
            # taxjson proceeds are net of sell-side costs; Schedule 3 wants
            # them split back out. gain = (net + outlays) - acb - outlays
            # stays identical.
            rec["proceeds"] += proceeds + outlays
            rec["outlays"] += outlays
        # Grant timing books a written option twice — the WRITE and its
        # buy-back — while the contracts were disposed of once: count
        # them once (S003-04, S033-02; as reconcile-slips, R1-18). A
        # write still open (or closed next year) counts at the write.
        if e.get("grant"):
            rec["grant_units"] += qty
        else:
            if direction == "SHORT":
                rec["short_close_units"] += grant_buyback_units(e, year)
            rec["units"] += qty
        rec["gain"] += gain
        rec["denied"] += float(g.disallowed_amount or 0.0)
        rec["perm_denied"] += float(g.permanently_disallowed or 0.0)
        # An in-kind contribution's loss (s.40(2)(g)(iv), CA-INKIND-03):
        # nil, like a denied superficial loss the footing ACB absorbs it
        # — but its own figure, never in DENIED.
        rec["denied_contrib"] += float(g.denied_contribution or 0.0)
        acq = _acquired_date(e)
        if acq:
            y = acq[:4]
            if rec["acq_year"] is None or y < rec["acq_year"]:
                rec["acq_year"] = y

    rows: List[Dict[str, Any]] = []
    for (lkey, symbol) in sorted(recs, key=lambda k: (
            _LINE_ORDER.index(k[0]), k[1])):
        r = recs[(lkey, symbol)]
        r["units"] += max(0.0, r["grant_units"] - r["short_close_units"])
        spec = schedule3_line(lkey, year, 2)
        notes = []
        _perm = r["perm_denied"]
        _defer = r["denied"] - _perm
        if _defer > _EPS:
            notes.append(f"superficial loss {M.shown(_defer, 2):,.2f} denied: ACB "
                         f"shown reduced by it; add it to the ACB of "
                         f"the replacement property")
        if _perm > _EPS:
            # A registered-account acquisition denies for GOOD — there
            # is no ACB anywhere to bump; the old single note told
            # users to add it to a future rebuy's ACB (2026-09 audit).
            # An affiliated person's purchase is permanent for THIS
            # return too, but that person adds it to their own ACB
            # (s.53(1)(f)) — the note no longer says nobody does
            # (S033-03).
            notes.append(f"superficial loss {M.shown(_perm, 2):,.2f} PERMANENTLY "
                         f"denied (replacement in a registered account "
                         f"or bought by an affiliated person — no ACB "
                         f"addition on this return; an affiliated "
                         f"person adds it to their own ACB)")
        if r["denied_contrib"] > _EPS:
            notes.append(f"loss {M.shown(r['denied_contrib'], 2):,.2f} on an in-kind "
                         f"contribution to a registered plan DENIED for "
                         f"good (s.40(2)(g)(iv)): ACB shown reduced by "
                         f"it; no ACB addition anywhere")
        if "futures" in r["classes"]:
            notes.append("futures / option on futures — reported with "
                         "the other properties (options, T4037)"
                         + ("; futures: settled P/L (gain as proceeds, "
                            "loss as ACB), not the notional"
                            if is_plain_future(symbol) else ""))
        if r["short"]:
            notes.append("includes short position(s) — PROCEEDS is the "
                         "short sale or write, ACB the cover")
        # The user's ALLOWLOSS position (CA-SL-18): the loss is claimed
        # although the rule would deny it — the row never showed it
        # (pre-release review M7).
        _positions = _merge_positions(r["positions"], "canada")
        notes += [fp["note"] for fp in _positions]
        proceeds, acb, outlays, gain = _foot_cells(
            r["proceeds"], r["outlays"], r["gain"])
        pclass = sorted(r["classes"])[0] if len(r["classes"]) == 1 \
            else "mixed"
        rows.append({
            "symbol": symbol,
            "line": spec["line"], "line_key": spec["key"],
            "proceeds_line": spec["proceeds_code"],
            "gain_line": spec["gain_code"],
            "property": pclass,
            # Full precision (a crypto unit count is not money): 4 dp
            # showed a 0.00003 BTC sale as 0 units (S032-21).
            "units": M.units(r["units"], 8),
            "acq_year": r["acq_year"] or "",
            "proceeds": proceeds,
            # The footing ACB: proceeds − outlays − allowed gain. For a
            # plain sale this IS the ACB; a superficial-loss row shows
            # it reduced by the denied amount.
            "acb": acb,
            "outlays": outlays,
            "gain": gain,
            "denied": _cents(r["denied"]),
            "denied_contribution": _cents(r["denied_contrib"]),
            "dispositions": r["n"],
            "notes": "; ".join(notes),
            **({"filing_positions": _positions} if _positions else {}),
        })

    lines: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {}
    for lkey in _LINE_ORDER:
        lrows = [r for r in rows if r["line_key"] == lkey]
        if not lrows:
            continue
        spec = schedule3_line(lkey, year, 2)
        agg = {k: M.shown(sum(r[k] for r in lrows), 2) + 0.0
               for k in ("proceeds", "acb", "outlays", "gain", "denied",
                         "denied_contribution")}
        lines.append({**spec, "title": line_title(spec), **agg,
                      "dispositions": sum(r["dispositions"] for r in lrows),
                      "rows": len(lrows)})
        totals[f"proceeds_{spec['proceeds_code']}"] = agg["proceeds"]
        totals[f"gain_{spec['gain_code']}"] = agg["gain"]
    # Line 4's pair is always present (0.00 when nothing was sold), so a
    # consumer keyed on 13199/13200 never KeyErrors.
    totals.setdefault("proceeds_13199", 0.0)
    totals.setdefault("gain_13200", 0.0)
    totals["proceeds_all"] = M.shown(sum(r["proceeds"] for r in rows), 2)
    totals["gain_all"] = M.shown(sum(r["gain"] for r in rows), 2)
    # The same rows' gain summed BEFORE the per-row rounding, exactly (a
    # float sum's binary tail, TJ-4930): what the engine (and the .sum)
    # totals. The rows must add up to the line, so the form rounds per row;
    # a checker comparing with the engine uses this, not a growing tolerance
    # (R1-210: 100 rows of 100.004 export 10,000.00 against 10,000.40).  # pii-ok
    return {"form": "schedule3", "year": year, "rows": rows,
            "lines": lines, "totals": totals,
            "gain_unrounded": M.exact_total(GainRow(e).gain for e in entries),
            "denied_unrounded": M.exact_total(GainRow(e).disallowed_amount for e in entries)}
