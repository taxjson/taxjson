"""`tjs gains-base`: the dispositions of a window in the base currency, as
filed: each account's wash-adjusted base-currency book (`<account>_gains_
wash.json`, else `<account>_gains.json`: lib/report_model
resolve_gains_files, the books `tjs sum` and `tjs form-export` read), one
row per disposition, its cents the filed form's own for that one sale, so
a sale has one set of cents in every report.

Canada: lib/tax_forms `disposition_cells`, the cells `tjs gains-cra` shows
(schedule3_disposition footed alone), in `tjs sum`'s columns with their
meanings (lib/gain_columns, TJ-5336): PROCEEDS gross, as Schedule 3 shows
them (a futures contract's gain as proceeds and loss as cost, a written
option's premium gross with its commission an outlay: never negative), COST(ACB) the ACB as filed (a
denied loss taken out: Schedule 3's footed ACB, JSON `acb`), OUTLAYS,
TOTAL COST (`total_cost`), GAIN the gain filed (`allowed`) and DENIED
(INFO), every loss denied (`denied_all`, TJ-5341): the superficial
losses (`denied`; `*` one denied for good) and an in-kind contribution's
(s.40(2)(g)(iv), `denied_contribution`, R.CA_INKIND_03; its row marked
`†`, the legend saying so), information only: COST(ACB) is already
reduced by them. Each row foots: PROCEEDS − COST(ACB) − OUTLAYS = GAIN.
The JSON keeps the older keys beside them: `cost` before any
superficial-loss denial (`acb` + `denied`; an in-kind contribution's
denied loss is still out of it) and `gain` before it (`allowed` −
`denied`).

USA: lib/tax_forms `form8949_row`, Form 8949's cells for the sale: (d)
PROCEEDS, (e) COST, (g) ADJUSTMENT (a disallowed wash-sale loss, code W)
and (h) GAIN = (d) − (e) + (g); a section 1256 contract is Form 6781's,
its P/L as GAIN and no (d), (e) or (g).

The totals add the rows' cents: each account's, the taxable and the
sheltered accounts' apart; then THE RETURN, `tjs sum`'s own RETURN row
(cli/estimate `_return_figures`, which the command passes in), in
Canada sum's row cell for cell (lib/gain_columns lays the table out as
sum's, each money column at sum's offset). Schedule 3 rounds each
security's total, the rows each sale, so
the taxable rows can add to a few cents apart from it: the difference is
said per column, as is the return's rounding against the engine's
unrounded total. The command (cli/gains_filing.py `cmd_gains_base`) reads the
files and windows the rows; this module computes and lays out.
"""
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from taxjson.lib import gain_columns as GCOL
from taxjson.lib import out as _out
from taxjson.lib import tax_forms as TF
from taxjson.lib.report_fmt import fmt_money, fmt_qty
from taxjson.model import money as M
from taxjson.model.result import GainRow

_NONE = "—"
# The money keys of a row and of a total (`columns` in --json): Canada's
# `cost` and `gain` before any superficial-loss denial (an in-kind
# contribution's denied loss stays out of them), `allowed` the gain filed.
CA_SUMS = ("proceeds", "cost", "outlays", "gain", "denied", "allowed")
US_SUMS = ("proceeds", "cost", "adjustment", "gain")
_CA_EXTRA = ("permanently_denied", "denied_contribution")
# The filed ACB (sum's `acb`: `cost` − `denied`) and TOTAL COST (`acb` +
# `outlays`), beside them (TJ-5336); DENIED (INFO), `denied_all`: every
# kind of denied loss, `denied` + `denied_contribution` (TJ-5341).
_CA_FILED = ("acb", "total_cost", "denied_all")
# The text's money columns, sum's (lib/gain_columns), by their keys here
# (`text_columns` in --json): GAIN is `allowed`, DENIED (INFO) `denied_all`.
CA_SHOWN = ("proceeds", "acb", "outlays", "total_cost", "allowed", "denied_all")
_CA_HEADS = GCOL.HEADS
# The rows' own table: the long heads on two lines, so a sale fits on a
# line at the owner's ~117 columns as it did before TOTAL COST.
_CA_ROW_HEADS = tuple(h.replace(" ", "\n") for h in GCOL.HEADS)
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
    forms disposition_cells), the filed ACB (`acb`) and TOTAL COST, the
    older `cost` and `gain` before the superficial-loss denial (an
    in-kind contribution's, `denied_contribution`, stays out of them),
    DENIED (INFO) (`denied_all`: every kind added), the engine's
    unrounded allowed gain beside them."""
    c = TF.disposition_cells(g, year, date_key)
    denied, allowed = M.of(c["denied"]), M.of(c["gain"])
    acb, outlays = M.of(c["acb"]), M.of(c["outlays"])
    return {**_base(acct, date, g, taxable), "form": "schedule3",
            "proceeds": M.of(c["proceeds"]), "cost": acb + denied,
            "outlays": outlays, "gain": allowed - denied, "denied": denied,
            "allowed": allowed, "acb": acb, "total_cost": acb + outlays,
            "permanently_denied": M.of(c["perm_denied"]),
            "denied_contribution": M.of(c["denied_contrib"]),
            "denied_all": denied + M.of(c["denied_contrib"]),
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
    return US_SUMS + ("section_1256_gain",) if us else CA_SUMS + _CA_EXTRA + _CA_FILED


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
    """`tjs sum`'s RETURN row under this report's keys, exact (Canada:
    `acb`, `total_cost`, `allowed` and `denied_all` sum's COST(ACB), TOTAL
    COST, GAIN and DENIED (INFO); `cost` sum's COST(ACB) + `denied` and
    `gain` its gain − `denied`, the older keys' meanings); None when
    there is no filing figure."""
    if filing is None:
        return None
    t = filing["totals"]
    if us:
        out = {k: M.of(t.get(k) or 0) for k in US_SUMS}
        out["section_1256_gain"] = M.of(t.get("section_1256_gain") or 0)
        return out
    denied, gain = M.of(t.get("denied") or 0), M.of(t.get("gain") or 0)
    acb, outlays = M.of(t.get("acb") or 0), M.of(t.get("outlays") or 0)
    return {"proceeds": M.of(t.get("proceeds") or 0),
            "cost": acb + denied, "outlays": outlays, "gain": gain - denied,
            "denied": denied, "allowed": gain,
            "permanently_denied": M.of(t.get("permanently_denied") or 0),
            "denied_contribution": M.of(t.get("denied_contribution") or 0),
            "acb": acb, "total_cost": acb + outlays,
            "denied_all": M.of(t.get("denied_all") or 0)}


def against_return(tot: Dict[str, Any], ret: Optional[Dict[str, Any]],
                   us: bool) -> Dict[str, float]:
    """The taxable rows' totals less THE RETURN's, per column, where they
    are a cent or more apart ({} when they agree or there is no return)."""
    if ret is None:
        return {}
    keys = (US_SUMS + ("section_1256_gain",)) if us else CA_SUMS + _CA_FILED
    return {k: M.json_money(tot["taxable"][k] - ret[k], 2) for k in keys
            if M.cents_apart(tot["taxable"][k], ret[k]) >= 1}


def _json_sums(t: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (M.json_money(v, 2) if isinstance(v, Decimal) else v) for k, v in t.items()}


def json_doc(rows: List[Dict[str, Any]], tot: Dict[str, Any], *, currency: str,
             scope: str, basis: str, filing: Optional[Dict[str, Any]],
             unknown_cost: int, us: bool,
             account_names: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The `--json` document: `account_names` (an ACCOUNT's names, in the
    order given, lib/account_list; null for every account), `columns`
    (the money keys of a row: Canada's
    `cost` and `gain` before any superficial-loss denial, an in-kind
    contribution's denied loss still out of them), `text_columns` (the keys of the
    text's money columns, in its order: Canada's COST(ACB) `acb`, GAIN
    `allowed`, DENIED (INFO) `denied_all`), `rows` (each cell at the cent;
    `gain_unrounded` at the gains files' places) and `totals` (each account's, the taxable and the
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
            "account_names": None if account_names is None else [str(n) for n in account_names],
            "form": "8949" if us else "schedule3",
            "columns": list(US_SUMS if us else CA_SUMS),
            "text_columns": list(US_SUMS if us else CA_SHOWN),
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
        money = [_money(r[k]) for k in CA_SHOWN]
        if r["permanently_denied"] > 0:
            money[-1] += "*"
        if r["denied_contribution"] > 0:
            money[-1] += _CONTRIB
    return (([r["account"]] if prefix else [])
            + [r["date"], r["symbol"], fmt_qty(r["qty"]), *money,
               "" if days is None else str(days)])


# An in-kind contribution's denied loss on its row's DENIED (INFO) cell:
# in the cell's figure (TJ-5341), marked with its kind, s.40(2)(g)(iv), as
# `*` marks a superficial loss denied for good (TJ-5340).
_CONTRIB = "†"


def _legend(us: bool, contrib: bool) -> List["GCOL.Forms"]:
    """The legend lines, each in forms by width (lib/gain_columns
    `fit_legend`): how a row foots, and what its cells are; Canada's
    in-kind contribution line when a row is marked `†` (`contrib`)."""
    if us:
        return [(("Form 8949's cells per sale: (d) PROCEEDS − (e) COST + (g) ADJUSTMENT = (h) "
                  "GAIN, a loss negative.",),
                 ("Form 8949's cells per sale: (d) PROCEEDS − (e) COST + (g) ADJUSTMENT = (h) "
                  "GAIN.",)),
                (("(g): a wash-sale loss disallowed (code W), positive; §1256: a section 1256 "
                  "contract, Form 6781's P/L as GAIN.",),
                 ("(g): a wash-sale loss disallowed (code W), positive.",
                  "§1256: a section 1256 contract, Form 6781's P/L as GAIN."))]
    out = [(("Schedule 3's cells per sale: GAIN = PROCEEDS − TOTAL COST = PROCEEDS − COST(ACB) − "
             "OUTLAYS (a loss negative).",),
            ("Schedule 3's cells per sale: GAIN = PROCEEDS − TOTAL COST = PROCEEDS − COST(ACB) − "
             "OUTLAYS.",)),
           GCOL.denied_forms(star="*")]
    if contrib:
        # What the mark says that the cell does not: the kind (the figure
        # and that COST(ACB) is reduced by it are DENIED (INFO)'s line).
        out.append(((f"{_CONTRIB}: an in-kind contribution to a registered plan, its loss denied "
                     f"for good (s.40(2)(g)(iv)).",),))
    return out


def render(rows: List[Dict[str, Any]], tot: Dict[str, Any], *, currency: str, scope: str,
           basis: str, filing: Optional[Dict[str, Any]], why: str, prefix: bool,
           us: bool = False, details: bool = False,
           account_names: Optional[Sequence[str]] = None) -> "_out.Doc":
    """The text report: the rows, the totals per account (taxable and
    sheltered apart) and THE RETURN (`filing`, tjs sum's; `why` says what
    stands there when there is none), then how the rows and the return
    differ, if they do. With ACCOUNT (`account_names`), the return row is
    named by the `tjs sum` that prints it and the totals are said not to
    be the whole return (`_named`). A column the width leaves out is said
    in an `! ` line (nothing dropped silently)."""
    d = _out.Doc(f"REALIZED GAINS — {currency}, {scope}, the filing figures ({basis})")
    sums = US_SUMS if us else CA_SHOWN
    acts: List[Tuple[str, str]] = []
    contrib = not us and any(r["denied_contribution"] > 0 for r in rows)
    if not rows:
        d.line(f"No dispositions in {scope}.")
    else:
        for forms in _legend(us, contrib):
            GCOL.legend(d, forms, d.w)
        heads = ((["ACCT"] if prefix else []) + ["DATE", "SYMBOL", "QTY"]
                 + list(_US_HEADS if us else _CA_ROW_HEADS) + ["DAYS"])
        o = 1 if prefix else 0
        aligns = ["<"] * (o + 2) + [">"] * (len(sums) + 2)
        left_out: List[str] = []
        d.table(heads, [_cells(r, prefix, us) for r in rows], aligns=aligns,
                drop=(3 + o + len(sums), 2 + o), key=(0, 1, 2) if prefix else (0, 1),
                left_out=left_out)
        if left_out:
            acts.append((f"{' and '.join(left_out)} left out of the sales at this width",
                         "tjs gains-base --json"))
    ret = return_row(filing, us)
    cmd = _named(filing, account_names)
    if rows or ret is not None:
        _totals_heading(d, currency, account_names if cmd and ret is not None else None)
        if us:
            _totals_table(d, tot, ret, currency, cmd)
        else:
            dropped = _totals_ca(d, tot, ret, filing, currency, details, cmd)
            if dropped:
                acts.append((dropped, "tjs gains-base --details"))
    _filing_lines(d, tot, filing, ret, why, currency, details, us,
                  rows_against_filing(rows, filing), acts, cmd)
    return d


def _named(filing: Optional[Dict[str, Any]], account_names: Optional[Sequence[str]]) -> str:
    """With ACCOUNT, the `tjs sum` whose RETURN row THE RETURN is (the
    filing block's `command`: "tjs sum margin,beta"), which labels it: not
    the whole return, so never "THE RETURN (tjs sum)"; "" for every
    account or with no return row."""
    if not account_names or filing is None:
        return ""
    return str(filing.get("command") or "")


def _totals_heading(d: "_out.Doc", currency: str, names: Optional[Sequence[str]]) -> None:
    """TOTALS' heading: with ACCOUNT and a return row (`names`), the
    accounts and "(not the whole return)", wrapped to the width when
    longer."""
    if not names:
        d.section(f"TOTALS — {currency}")
        return
    head = (f"TOTALS — {currency}, {'account' if len(names) == 1 else 'accounts'} "
            f"{', '.join(names)} (not the whole return)")
    if 0 < d.w < len(head):
        d.blank()
        d.para(head)
    else:
        d.section(head)


def _totals_table(d: "_out.Doc", tot: Dict[str, Any], ret: Optional[Dict[str, Any]],
                  currency: str, cmd: str = "") -> None:
    """The US TOTALS (under `_totals_heading`): Form 8949's columns, the
    return row labelled `cmd` with ACCOUNT (`_named`)."""
    sums = US_SUMS
    body = [[a["account"], a["type"], str(a["dispositions"]),
             *(fmt_money(a[k]) for k in sums)]
            for a in tot["accounts"] if a["dispositions"]]
    foot = []
    for kind, label in (("taxable", "TAXABLE"), ("sheltered", "SHELTERED (not on the return)")):
        t = tot[kind]
        if t["dispositions"]:
            foot.append([label, "", str(t["dispositions"]), *(fmt_money(t[k]) for k in sums)])
    if ret is not None:
        foot.append([cmd or "THE RETURN (tjs sum)", "", "", *(fmt_money(ret[k]) for k in sums)])
    d.table(["ACCOUNT", "TYPE", "SALES", *(h.replace("\n", " ") for h in _US_HEADS)],
            body, aligns=["<", "<"] + [">"] * (len(sums) + 1), foot=foot, key=0)


def _filed(t: Dict[str, Any]) -> Dict[str, Any]:
    """A row or total in sum's columns (lib/gain_columns KEYS): GAIN the
    gain filed."""
    return {**{k: t[k] for k in GCOL.KEYS if k != "gain"}, "gain": t["allowed"]}


# The foot rows' labels, long and short: the short ones where the long
# would make the left block wider than sum's line labels (lib/gain_columns
# `table`), so the money columns stay at sum's offsets.
_FEET = (("taxable", "TAXABLE", "TAXABLE"),
         ("sheltered", "SHELTERED (not on the return)", "SHELTERED"))
_RETURN_LABELS = ("THE RETURN (tjs sum)", GCOL.RETURN_ROW)


def _totals_ca(d: "_out.Doc", tot: Dict[str, Any], ret: Optional[Dict[str, Any]],
               filing: Optional[Dict[str, Any]], currency: str, details: bool,
               cmd: str = "") -> Optional[str]:
    """The Canadian TOTALS (under `_totals_heading`) in sum's columns and
    layout (lib/gain_columns `table`, sum's lines and RETURN row its
    reference): each account, TAXABLE and SHELTERED, THE RETURN (with
    ACCOUNT, labelled `cmd`, `_named`, in every form: short enough for
    sum's label column); DENIED (INFO) dropped for the width only
    without --details: then the `! ` line's text saying so, its figure
    every kind of denied loss."""
    accts = [a for a in tot["accounts"] if a["dispositions"]]
    body = [[str(a["account"]), str(a["type"]), str(a["dispositions"])] for a in accts]
    # Each foot row's label in the three forms (the labels long; the others
    # short, THE RETURN (tjs sum) long; all short), its SALES and figures.
    labels: List[Tuple[str, str, str, str]] = [
        (lo, sh, sh, str(tot[k]["dispositions"])) for k, lo, sh in _FEET
        if tot[k]["dispositions"]]
    figs = [_filed(tot[k]) for k, _lo, _sh in _FEET if tot[k]["dispositions"]]
    if ret is not None:
        long_, short = (cmd, cmd) if cmd else _RETURN_LABELS
        labels.append((long_, long_, short, ""))
        figs.append(_filed(ret))
    forms = [(body, [[f[i], "", f[3]] for f in labels]) for i in (0, 1, 2)]
    lines, layout = GCOL.table(["ACCOUNT", "TYPE", "SALES"], lambda _labels: forms,
                               [_filed(a) for a in accts], figs, width=d.w,
                               lines=(filing or {}).get("lines") or (),
                               total=(filing or {}).get("totals"), keep_denied=details,
                               aligns=["<", "<", ">"])
    d.rows(lines)
    if layout != "no-denied":
        return None
    return (f"DENIED (INFO) left out at this width (losses denied: "
            f"{fmt_money((ret or tot['taxable'])['denied_all'])})")


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


_CA_NAMES = dict(zip(CA_SHOWN, _CA_HEADS))
_US_NAMES = {**{k: h.replace("\n", " ") for k, h in zip(US_SUMS, _US_HEADS)},
             "section_1256_gain": "section 1256 P/L"}


def _filing_lines(d: "_out.Doc", tot: Dict[str, Any], filing: Optional[Dict[str, Any]],
                  ret: Optional[Dict[str, Any]], why: str, currency: str, details: bool,
                  us: bool, mismatch: Optional[str], acts: Sequence[Tuple[str, str]],
                  cmd: str = "") -> None:
    """Under the totals: what stands for the return when there is none;
    else the taxable rows against THE RETURN per column (Schedule 3
    rounds per security), the return's rounding against the engine's
    unrounded total, the section 1256 contracts (US), the return's lines
    (`details`); `acts`, the columns left out for the width, `! `
    lines (text, where to see them). With ACCOUNT, THE RETURN is named
    as its row is (`cmd`, `_named`): not the whole return."""
    d.blank()
    if filing is None or ret is None:
        d.line(why)
        for text, cmd in acts:
            d.act(text, cmd)
        return
    apart = against_return(tot, ret, us)
    names = _US_NAMES if us else _CA_NAMES
    apart = {k: apart[k] for k in names if k in apart}
    if apart:
        d.para(f"The taxable rows differ from {f'the {cmd} row' if cmd else 'THE RETURN'} by "
               + ", ".join(f"{names[k]} {v:+,.2f}" for k, v in apart.items())
               + f" {currency}: Schedule 3 rounds each security's total, the rows each sale.")
    t = filing["totals"]
    gain = t.get("gain", 0.0)
    engine = filing.get("engine_gain_unrounded")
    if engine is not None and M.cents_apart(gain, engine) >= 1:
        gap = M.present(M.of(gain) - M.of(engine), 2)
        lines_of = (f"Each {'Form 8949' if us else 'Schedule 3'} line" if cmd
                    else "Each line of the return")
        d.para(f"{lines_of} is rounded to the cent, as filed: the engine's "
               f"unrounded total is {fmt_money(engine)} ({gap:+,.2f} on "
               f"{f'the {cmd} row' if cmd else 'the return'}'s "
               f"{'(h) GAIN' if us else 'GAIN'}).")
    if us and t.get("section_1256_gain"):
        d.line(f"Not on Form 8949: the section 1256 contracts, Form 6781 by hand "
               f"({fmt_money(t['section_1256_gain'])}).")
    if mismatch:
        d.act(mismatch, "tjs sum --details")
    for text, cmd in acts:
        d.act(text, cmd)
    if details:
        for ln in filing.get("lines") or filing.get("parts_8949") or []:
            # A Schedule 3 line by its one name, sum's (lib/gain_columns).
            name = (GCOL.label(ln) if "short" in ln
                    else ln.get("title") or ln.get("label") or "")
            d.line(f"  {name}: {fmt_money(ln.get('gain', 0.0))}")
        if tot["sheltered"]["dispositions"]:
            d.line("The sheltered accounts' sales are shown and totalled, never on the return.")
    else:
        d.hint("tjs gains-base --details",
               f"the {'Form 8949' if us else 'Schedule 3'} lines" if cmd else "the return's lines")
