"""`tjs gains-base`: the dispositions of a window in the base currency, as
filed: each account's wash-adjusted base-currency book (`<account>_gains_
wash.json`, else `<account>_gains.json`: lib/report_model
resolve_gains_files, the books `tjs sum` and `tjs form-export` read), one
row per disposition, its cents the filed form's own for that one sale, so
a sale has one set of cents in every report.

Canada: lib/tax_forms `disposition_cells`, the cells `tjs gains-cra` shows
(schedule3_disposition footed alone): PROCEEDS gross, as Schedule 3 shows
them (a futures contract's gain as proceeds and loss as cost, a write for
a net debit as an outlay: never negative), COST(ACB) before any denial
(Schedule 3's footed ACB plus DENIED), OUTLAYS, GAIN before the denial,
DENIED and ALLOWED (Schedule 3's gain). Each row foots twice: PROCEEDS −
COST(ACB) − OUTLAYS = GAIN, and GAIN + DENIED = ALLOWED.

USA: lib/tax_forms `form8949_row`, Form 8949's cells for the sale: (d)
PROCEEDS, (e) COST, (g) ADJUSTMENT (a disallowed wash-sale loss, code W)
and (h) GAIN = (d) − (e) + (g); a section 1256 contract is Form 6781's,
its P/L as GAIN and no (d), (e) or (g).

The totals add the rows' cents: each account's, the taxable and the
sheltered accounts' apart; then THE RETURN, `tjs sum`'s own RETURN row
(cli/estimate `_return_figures`, which the command passes in) in the same
columns (Canada: COST(ACB) is sum's COST(ACB) + DENIED, GAIN its gain −
DENIED). Schedule 3 rounds each security's total, the rows each sale, so
the taxable rows can add to a few cents apart from it: the difference is
said per column, as is the return's rounding against the engine's
unrounded total. The command (cli/gains_filing.py `cmd_gains_base`) reads the
files and windows the rows; this module computes and lays out.
"""
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from taxjson.lib import out as _out
from taxjson.lib import tax_forms as TF
from taxjson.lib.report_fmt import fmt_money, fmt_qty
from taxjson.model import money as M
from taxjson.model.result import GainRow

_NONE = "—"
# The money columns, the JSON keys of a row and of a total, in order.
CA_SUMS = ("proceeds", "cost", "outlays", "gain", "denied", "allowed")
US_SUMS = ("proceeds", "cost", "adjustment", "gain")
_CA_EXTRA = ("permanently_denied", "denied_contribution")
_CA_HEADS = ("PROCEEDS", "COST(ACB)", "OUTLAYS", "GAIN", "DENIED", "ALLOWED")
_US_HEADS = ("PROCEEDS\n(d)", "COST\n(e)", "ADJUSTMENT\n(g)", "GAIN\n(h)")


def _base(acct: str, date: str, g: Dict[str, Any], taxable: bool) -> Dict[str, Any]:
    v = GainRow(g)
    return {"account": acct, "taxable": taxable, "date": date,
            "date_settle": str(g.get("date_settle") or ""), "id": str(g.get("id") or ""),
            "symbol": str(g.get("symbol") or ""), "direction": g.get("direction") or "LONG",
            "qty": M.units(abs(v.quantity or 0.0), 8)}


def row_ca(acct: str, date: str, g: Dict[str, Any], taxable: bool, year: Optional[int],
           date_key: str) -> Dict[str, Any]:
    """One Canadian disposition's row: Schedule 3's cells for it (lib/tax_
    forms disposition_cells), COST(ACB) before the denial and GAIN before
    it, the engine's unrounded allowed gain beside them."""
    c = TF.disposition_cells(g, year, date_key)
    denied, allowed = M.of(c["denied"]), M.of(c["gain"])
    return {**_base(acct, date, g, taxable), "form": "schedule3",
            "proceeds": M.of(c["proceeds"]), "cost": M.of(c["acb"]) + denied,
            "outlays": M.of(c["outlays"]), "gain": allowed - denied, "denied": denied,
            "allowed": allowed, "permanently_denied": M.of(c["perm_denied"]),
            "denied_contribution": M.of(c["denied_contrib"]),
            "gain_unrounded": M.of(GainRow(g).gain or 0),
            "days_held": g.get("days_held")}


def row_us(acct: str, date: str, g: Dict[str, Any], taxable: bool,
           da_boxes: bool) -> Dict[str, Any]:
    """One US disposition's row: Form 8949's cells for it (lib/tax_forms
    form8949_row), or a section 1256 contract's Form 6781 P/L."""
    r = TF.form8949_row(g, da_boxes)
    f = r["row"]
    on_8949 = r["part"] is not None
    return {**_base(acct, date, g, taxable),
            "form": "8949" if on_8949 else "6781",
            "part": r["part"], "boxes": f.get("boxes"), "code": f.get("code") or "",
            **{k: (M.of(f[k]) if on_8949 else None) for k in ("proceeds", "cost", "adjustment")},
            "gain": M.of(f["gain"]),
            "gain_unrounded": M.of(GainRow(g).gain or 0),
            "days_held": g.get("days_held")}


def _keys(us: bool) -> Tuple[str, ...]:
    return US_SUMS + ("section_1256_gain",) if us else CA_SUMS + _CA_EXTRA


def _add(rows: Sequence[Dict[str, Any]], us: bool) -> Dict[str, Any]:
    if not us:
        return {k: M.exact_sum(r[k] for r in rows) for k in _keys(us)}
    f8949 = [r for r in rows if r["form"] == "8949"]
    out = {k: M.exact_sum(r[k] for r in f8949) for k in US_SUMS}
    out["section_1256_gain"] = M.exact_sum(r["gain"] for r in rows if r["form"] == "6781")
    return out


def totals(rows: List[Dict[str, Any]], accounts: Sequence[Tuple[str, bool]],
           us: bool = False) -> Dict[str, Any]:
    """Each account's rows' cells added ({account, type, dispositions, the
    sums}), then the taxable and the sheltered accounts'."""
    out: Dict[str, Any] = {"accounts": []}
    for acct, taxable in accounts:
        rs = [r for r in rows if r["account"] == acct]
        out["accounts"].append({"account": acct,
                                "type": "taxable" if taxable else "sheltered",
                                "dispositions": len(rs), **_add(rs, us)})
    for kind, flag in (("taxable", True), ("sheltered", False)):
        rs = [r for r in rows if r["taxable"] is flag]
        out[kind] = {"dispositions": len(rs), **_add(rs, us)}
    return out


def return_row(filing: Optional[Dict[str, Any]], us: bool) -> Optional[Dict[str, Any]]:
    """`tjs sum`'s RETURN row in this report's columns (Canada: COST(ACB)
    = sum's COST(ACB) + DENIED, GAIN = its gain − DENIED, ALLOWED its
    gain), exact; None when there is no filing figure."""
    if filing is None:
        return None
    t = filing["totals"]
    if us:
        out = {k: M.of(t.get(k) or 0) for k in US_SUMS}
        out["section_1256_gain"] = M.of(t.get("section_1256_gain") or 0)
        return out
    denied, gain = M.of(t.get("denied") or 0), M.of(t.get("gain") or 0)
    return {"proceeds": M.of(t.get("proceeds") or 0),
            "cost": M.of(t.get("acb") or 0) + denied,
            "outlays": M.of(t.get("outlays") or 0), "gain": gain - denied,
            "denied": denied, "allowed": gain,
            "permanently_denied": M.of(t.get("permanently_denied") or 0),
            "denied_contribution": M.of(t.get("denied_contribution") or 0)}


def against_return(tot: Dict[str, Any], ret: Optional[Dict[str, Any]],
                   us: bool) -> Dict[str, float]:
    """The taxable rows' totals less THE RETURN's, per column, where they
    are a cent or more apart ({} when they agree or there is no return)."""
    if ret is None:
        return {}
    keys = (US_SUMS + ("section_1256_gain",)) if us else CA_SUMS
    return {k: M.json_money(tot["taxable"][k] - ret[k], 2) for k in keys
            if M.cents_apart(tot["taxable"][k], ret[k]) >= 1}


def _json_sums(t: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (M.json_money(v, 2) if isinstance(v, Decimal) else v) for k, v in t.items()}


def json_doc(rows: List[Dict[str, Any]], tot: Dict[str, Any], *, currency: str,
             scope: str, basis: str, filing: Optional[Dict[str, Any]],
             unknown_cost: int, us: bool) -> Dict[str, Any]:
    """The `--json` document: `columns` (the money keys of a row, in the
    text's order), `rows` (each cell at the cent; `gain_unrounded` at the
    gains files' places) and `totals` (each account's, the taxable and the
    sheltered rows added, `return`: `tjs sum`'s RETURN row in these
    columns, `rows_less_return`: the taxable rows less it where a cent or
    more apart, and `filing`: `tjs sum --json`'s filing block, its
    `totals` and `engine_gain_unrounded` under sum's own key names, and
    whether the taxable rows' unrounded gains add to sum's; `return` and
    `filing` null when the window is not the tax year's, a filter narrows
    it or no account shown is taxable)."""
    def cell(k: str, v: Any) -> Any:
        if not isinstance(v, Decimal):
            return v
        return M.json_money(v, M.GAINS_PLACES if k == "gain_unrounded" else 2)
    ret = return_row(filing, us)
    return {"currency": currency, "scope": scope, "basis": basis,
            "form": "8949" if us else "schedule3",
            "columns": list(US_SUMS if us else CA_SUMS),
            "rows": [{k: cell(k, v) for k, v in r.items()} for r in rows],
            "totals": {"accounts": [_json_sums(a) for a in tot["accounts"]],
                       "taxable": _json_sums(tot["taxable"]),
                       "sheltered": _json_sums(tot["sheltered"]),
                       "return": None if ret is None else _json_sums(ret),
                       "rows_less_return": against_return(tot, ret, us),
                       "filing": None if filing is None else {
                           **filing, "rows_match": rows_against_filing(rows, filing) is None}},
            "unknown_cost_routed": unknown_cost}


def _money(v: Any) -> str:
    return _NONE if v is None else fmt_money(v)


def _cells(r: Dict[str, Any], prefix: bool, us: bool) -> List[str]:
    days = r["days_held"]
    if us:
        money = [_money(r[k]) for k in US_SUMS]
        if r["form"] == "6781":
            money[-1] += " §1256"
        elif r["code"]:
            money[2] += f" {r['code']}"
    else:
        money = [_money(r[k]) for k in CA_SUMS]
        if r["permanently_denied"] > 0:
            money[4] += "*"
    return (([r["account"]] if prefix else [])
            + [r["date"], r["symbol"], fmt_qty(r["qty"]), *money,
               "" if days is None else str(days)])


def _legend(us: bool) -> Tuple[str, str]:
    """The two legend lines: how a row foots, and what its cells are."""
    if us:
        return ("Form 8949's cells per sale: (d) PROCEEDS − (e) COST + (g) ADJUSTMENT = (h) "
                "GAIN, a loss negative.",
                "(g): a wash-sale loss disallowed (code W), positive; §1256: a section 1256 "
                "contract, Form 6781's P/L as GAIN.")
    return ("PROCEEDS − COST(ACB) − OUTLAYS = GAIN (a loss negative), before any superficial-"
            "loss denial; GAIN + DENIED = ALLOWED.",
            "Schedule 3's cells per sale; DENIED positive (* for good); THE RETURN's COST(ACB) = "
            "tjs sum's COST(ACB) + DENIED.")


def render(rows: List[Dict[str, Any]], tot: Dict[str, Any], *, currency: str, scope: str,
           basis: str, filing: Optional[Dict[str, Any]], why: str, prefix: bool,
           us: bool = False, details: bool = False) -> "_out.Doc":
    """The text report: the rows, the totals per account (taxable and
    sheltered apart) and THE RETURN (`filing`, tjs sum's; `why` says what
    stands there when there is none), then how the rows and the return
    differ, if they do."""
    d = _out.Doc(f"REALIZED GAINS — {currency}, {scope}, the filing figures ({basis})")
    sums = US_SUMS if us else CA_SUMS
    if not rows:
        d.line(f"No dispositions in {scope}.")
    else:
        for text in _legend(us):
            d.legend(text)
        heads = ((["ACCT"] if prefix else []) + ["DATE", "SYMBOL", "QTY"]
                 + list(_US_HEADS if us else _CA_HEADS) + ["DAYS"])
        o = 1 if prefix else 0
        aligns = ["<"] * (o + 2) + [">"] * (len(sums) + 2)
        d.table(heads, [_cells(r, prefix, us) for r in rows], aligns=aligns,
                drop=(3 + o + len(sums), 2 + o), key=(0, 1, 2) if prefix else (0, 1))
    ret = return_row(filing, us)
    if rows or ret is not None:
        _totals_table(d, tot, ret, currency, us)
    _filing_lines(d, tot, filing, ret, why, currency, details, us,
                  rows_against_filing(rows, filing))
    return d


def _totals_table(d: "_out.Doc", tot: Dict[str, Any], ret: Optional[Dict[str, Any]],
                  currency: str, us: bool) -> None:
    sums = US_SUMS if us else CA_SUMS
    d.section(f"TOTALS — {currency}")
    body = [[a["account"], a["type"], str(a["dispositions"]),
             *(fmt_money(a[k]) for k in sums)]
            for a in tot["accounts"] if a["dispositions"]]
    foot = []
    for kind, label in (("taxable", "TAXABLE"), ("sheltered", "SHELTERED (not on the return)")):
        t = tot[kind]
        if t["dispositions"]:
            foot.append([label, "", str(t["dispositions"]), *(fmt_money(t[k]) for k in sums)])
    if ret is not None:
        foot.append(["THE RETURN (tjs sum)", "", "", *(fmt_money(ret[k]) for k in sums)])
    d.table(["ACCOUNT", "TYPE", "SALES", *(h.replace("\n", " ") for h in
                                          (_US_HEADS if us else _CA_HEADS))],
            body, aligns=["<", "<"] + [">"] * (len(sums) + 1), foot=foot, key=0)


def rows_against_filing(rows: Sequence[Dict[str, Any]],
                        filing: Optional[Dict[str, Any]]) -> Optional[str]:
    """The check that the taxable rows are the return's: their engine
    allowed gains, added exactly (US: the section 1256 contracts, Form
    6781's, left out, as Form 8949 leaves them), against `tjs sum`'s
    unrounded total; the difference said when they are a cent or more
    apart, else None."""
    if filing is None:
        return None
    on = M.exact_sum(r["gain_unrounded"] for r in rows
                     if r["taxable"] and r["form"] != "6781")
    engine = filing.get("engine_gain_unrounded")
    if engine is None or M.cents_apart(on, engine) < 1:
        return None
    return (f"the taxable rows add to {fmt_money(on)}, tjs sum's to {fmt_money(engine)}: "
            f"they should be the same sales")


_CA_NAMES = dict(zip(CA_SUMS, _CA_HEADS))
_US_NAMES = {**{k: h.replace("\n", " ") for k, h in zip(US_SUMS, _US_HEADS)},
             "section_1256_gain": "section 1256 P/L"}


def _filing_lines(d: "_out.Doc", tot: Dict[str, Any], filing: Optional[Dict[str, Any]],
                  ret: Optional[Dict[str, Any]], why: str, currency: str, details: bool,
                  us: bool, mismatch: Optional[str]) -> None:
    """Under the totals: what stands for the return when there is none;
    else the taxable rows against THE RETURN per column (Schedule 3
    rounds per security), the return's rounding against the engine's
    unrounded total, the section 1256 contracts (US), the return's lines
    (`details`)."""
    d.blank()
    if filing is None or ret is None:
        d.line(why)
        return
    apart = against_return(tot, ret, us)
    if apart:
        names = _US_NAMES if us else _CA_NAMES
        d.para("The taxable rows differ from THE RETURN by " + ", ".join(
            f"{names[k]} {v:+,.2f}" for k, v in apart.items())
            + f" {currency}: Schedule 3 rounds each security's total, the rows each sale.")
    t = filing["totals"]
    gain = t.get("gain", 0.0)
    engine = filing.get("engine_gain_unrounded")
    if engine is not None and M.cents_apart(gain, engine) >= 1:
        gap = M.present(M.of(gain) - M.of(engine), 2)
        d.para(f"Each line of the return is rounded to the cent, as filed: the engine's "
               f"unrounded total is {fmt_money(engine)} ({gap:+,.2f} on the return's "
               f"{'(h) GAIN' if us else 'ALLOWED'}).")
    if us and t.get("section_1256_gain"):
        d.line(f"Not on Form 8949: the section 1256 contracts, Form 6781 by hand "
               f"({fmt_money(t['section_1256_gain'])}).")
    if mismatch:
        d.act(mismatch, "tjs sum --details")
    if details:
        for ln in filing.get("lines") or filing.get("parts_8949") or []:
            name = ln.get("title") or ln.get("label") or ""
            d.line(f"  {name}: {fmt_money(ln.get('gain', 0.0))}")
        if tot["sheltered"]["dispositions"]:
            d.line("The sheltered accounts' sales are shown and totalled, never on the return.")
    else:
        d.hint("tjs gains-base --details", "the return's lines")
