"""`taxjson amt`: the year's Canadian minimum tax (AMT) line by line.

Every figure comes from the estimate's own result (`taxjson estimate
--json`, lib/tax_estimate._amt_canada and ca_amt_carryover) — this
module only arranges and words it, so `amt` and the estimate's AMT
section agree to the cent (tax-logic CA-AMT-01).

Law: ITA s.127.5 (minimum tax), s.127.51 (minimum amount and rate),
s.127.52 (adjusted taxable income), s.127.53 (basic exemption),
s.127.531 (basic minimum tax credit), s.127.54 (special foreign tax
credit), s.127.55 (when it does not apply); s.120.2 (the carryover).
Form T691; the carryover is claimed on federal Schedule 1 line 40427.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from taxjson.lib import out as _out
from taxjson.lib.report_model import fmt_money as m

LAW = ("ITA s.127.5-127.55 (form T691); carryover s.120.2 (T691 Part 8, "
       "federal Schedule 1 line 40427)")


def build(r: Dict[str, Any], year: Any, currency: str) -> Dict[str, Any]:
    """The `amt --json` document from an estimate result `r`."""
    amt = dict(r.get("amt") or {})
    tw = r.get("trace_with") or {}
    return {
        "year": year, "province": r.get("province"),
        "vintage": r.get("vintage"), "currency": currency,
        "law": LAW,
        "taxable_income": round(float(tw.get("ti") or 0.0), 2),
        "tax_regular": r.get("tax_with"),
        "amt": amt,
        "estimated_tax": r.get("estimated_tax"),
        "estimated_tax_with_amt": r.get("estimated_tax_with_amt"),
        "carry_sources": r.get("carry_sources") or {},
        "notes": [n for n in (r.get("notes") or [])
                  if _amt_note(n)],
    }


def _amt_note(n: str) -> bool:
    low = n.lower()
    return any(w in low for w in ("minimum tax", "amt", "close-year",
                                  "rate vintage", "tax year", "tables"))


def _row_line(label: str, amount: Optional[float], note: str = "",
              details: bool = True) -> str:
    """`  label  amount  [note]`; a note too long for the width wraps
    under itself (the result may hold several lines). Not `details`
    (the default view): a note that does not fit the line is left out
    (--details shows it)."""
    amt = "" if amount is None else m(amount)
    head = f"  {label:<46}{amt:>14}"
    if note and not details:
        w = _out.width()
        if w and len(head) + 2 + len(note) > w:
            note = ""
    if not note:
        return head.rstrip()
    return "\n".join(_out.wrap(note, None, head + "  ",
                               " " * (len(head) + 2)))


_row = _row_line


def _notes(notes: List[str]) -> List[str]:
    """The NOTES section: one `- ` item per note, wrapped."""
    L: List[str] = ["", "  Notes"]
    for n in notes:
        L += _out.wrap(n, None, "  - ", "    ")
    return L


def render(doc: Dict[str, Any], details: bool = True) -> List[str]:
    """The `taxjson amt` report. Not `details` (the default view, docs/
    output-style.md, Essentials first): the law line, notes that do
    not fit a row and the NOTES move to --details."""
    import functools
    _row = functools.partial(_row_line, details=details)
    a = doc["amt"]
    prov = doc.get("province") or "?"
    cur = doc.get("currency") or "CAD"
    L = [f"MINIMUM TAX (AMT) — canada/{prov}, tax year {doc['year']}, "
         f"{cur}"]
    if details:
        L += _out.wrap(f"ESTIMATE ONLY, not filing numbers: the taxable "
                       f"accounts plus your other income, rates vintage "
                       f"{doc.get('vintage')}.")
        L += _out.wrap(f"Law: {LAW}.")
    else:
        L.append(f"ESTIMATE ONLY, not filing numbers: rates vintage "
                 f"{doc.get('vintage')}; [ ] the law or the source.")
    L.append("")
    reg = doc.get("tax_regular") or {}
    L.append("REGULAR TAX")
    L.append(_row("Taxable income (line 26000)", doc["taxable_income"]))
    L.append(_row("Federal tax after credits", a["regular_fed"],
                  "[BPA, dividend and foreign tax credits]"))
    if reg:
        L.append(_row(f"{prov} tax", reg.get("provincial")))
    L.append("")
    L.append("ADJUSTED TAXABLE INCOME (s.127.52)" if details
             else "ADJUSTED TAXABLE INCOME — s.127.52")
    for ln in a.get("ati_lines") or []:
        if abs(float(ln["amount"])) < 0.005 and "Other income" not in \
                ln["label"] and "Capital gains" not in ln["label"]:
            continue
        L.append(_row(ln["label"], ln["amount"], ln.get("ref") or ""))
    if details:
        L.append(_row("Donations, stock-option deduction", None,
                      "not modelled (the estimate has no input for "
                      "them)"))
    shown = sum(float(ln["amount"]) for ln in a.get("ati_lines") or [])
    L.append(_row("= Adjusted taxable income", a["adjusted_income"],
                  "[floored at zero]" if shown < -0.005 else ""))
    L.append(_row("- Basic exemption (s.127.53)", -a["exemption"],
                  "[the start of the 29% bracket]"))
    L.append(_row("= Subject to minimum tax", a.get("subject")))
    L.append(_row(f"x {a['rate'] * 100:.1f}% (s.127.51)",
                  a.get("gross_minimum")))
    L.append(_row("- Basic minimum tax credit (s.127.531)",
                  -a["bpa_credit"],
                  f"[basic personal amount {m(a.get('bpa_amount', 0.0))}"
                  f": its credit at 50%]"))
    # s.127.54(2): the greater of the regular credit and the lesser of
    # 2/3 of the foreign tax and 20.5% of the foreign income (an older
    # result without the parts prints no note).
    _sftc_note = ""
    if "ftc_regular" in a:
        from taxjson.lib.tax_estimate import CA_AMT_SFTC_RATE, fmt_pct
        _sftc_note = (f"[the greater of the regular credit "
                      f"{m(a['ftc_regular'])} and the lesser of 2/3 of "
                      f"the foreign tax "
                      f"({m(a.get('ftc_foreign_taxes', 0.0))}) and "
                      f"{fmt_pct(CA_AMT_SFTC_RATE)} of the foreign "
                      f"income ({m(a.get('ftc_income_limit', 0.0))})]")
    L.append(_row("- Special foreign tax credit (s.127.54)",
                  -float(a.get("ftc") or 0.0), _sftc_note))
    L.append(_row("= Federal minimum tax", a["minimum_fed"]))
    L.append(_row("  Federal regular tax", a["regular_fed"]))
    if a["binding"]:
        # What carries forward is s.120.2(3)'s additional tax, which is
        # the excess only when the special foreign tax credit is the
        # regular one (backlog TJ-0626).
        _cf = float(a.get("carryforward", a["excess_fed"]))
        L.append(_row("=> AMT BINDS: additional federal tax",
                      a["excess_fed"],
                      "[s.120.2(3): carries forward]"
                      if abs(_cf - a["excess_fed"]) <= 0.005
                      else f"[s.120.2(3): carries forward {m(_cf)}]"))
        from taxjson.lib.tax_estimate import ca_prov_amt_base_text
        txt = (f"{a['provincial_factor'] * 100:.2f}% of "
               f"{ca_prov_amt_base_text(a)}"
               + (f" {m(a['provincial_amt_base'])}"
                  if a.get("provincial_amt_base_kind", "excess")
                  != "excess" else "")
               + (" + surtax on it"
                  if a.get("provincial_amt_surtax", 0) > 0.005 else "")
               + (", factor ASSUMED" if a.get("provincial_factor_assumed")
                  else ""))
        L.append(_row(f"   {prov} minimum tax", a["provincial_amt"],
                      f"[{txt}]"))
        # The provincial foreign tax credit of the minimum-tax year
        # (form T2036) less the regular one (backlog TJ-0591).
        _pfc = float(a.get("provincial_ftc_change") or 0.0)
        if abs(_pfc) > 0.005:
            L.append(_row(f"   {prov} foreign tax credit (T2036)", _pfc,
                          f"[credit {m(a['provincial_ftc_min_tax'])} "
                          f"with AMT, {m(a['provincial_ftc_regular'])} "
                          f"in regular tax]"))
        L.append(_row("   Top-up over regular tax", a["topup"]))
    else:
        L.append(_row("=> AMT does not bind: regular tax over minimum",
                      a["headroom"],
                      "[the room a carryover can use]"))
    L.append("")
    c = a.get("carryover") or {}
    L.append("MINIMUM TAX CARRYOVER (s.120.2)" if details
             else "MINIMUM TAX CARRYOVER — s.120.2")
    src = (doc.get("carry_sources") or {}).get("amt_carryover")
    L += _out.wrap(src or "nothing entered ([estimate] amt_carryover) "
                          "and no earlier close-year record", None,
                   "  From: ", "        ")
    avail = c.get("available_by_year") or {}
    if avail:
        L.append(f"  {'ORIGIN':<8}{'AVAILABLE':>14}  {'USABLE THROUGH':<15}"
                 f"{'RECOVERED':>14}{'LEFT':>14}")
        rec = c.get("recovered_by_year") or {}
        last = c.get("last_year_by_origin") or {}
        for o, v in avail.items():
            left = float(v) - float(rec.get(o, 0.0))
            L.append(f"  {o:<8}{m(v):>14}  {str(last.get(o, '')):<15}"
                     f"{m(rec.get(o, 0.0)):>14}{m(left):>14}")
    for o, v in (c.get("expired_by_year") or {}).items():
        L.append(f"  expired: {o} {m(v)} (usable through {int(o) + 7}; "
                 f"dropped)")
    if avail:
        L.append(_row("Recovery limit (regular - minimum, federal)",
                      c.get("limit")))
        L.append(_row("Recovered: federal (line 40427)",
                      c.get("recovered_federal")))
        # The share deducted from basic provincial tax, what the
        # provincial form's carryover line takes; the surtax it saves
        # apart (backlog TJ-0016).
        _sur = float(c.get("recovered_provincial_surtax") or 0.0)
        L.append(_row(f"Recovered: {prov} share",
                      c.get("recovered_provincial_credit",
                            c.get("recovered_provincial")),
                      f"[{a['provincial_factor'] * 100:.2f}% of the "
                      f"federal amount, off basic {prov} tax]"))
        if abs(_sur) > 0.005:
            L.append(_row(f"  {prov} surtax it saves", _sur,
                          f"[the {prov} tax drop: "
                          f"{m(c.get('recovered_provincial', 0.0))}]"))
        if abs(float(c.get("attributed", 0.0))
               - float(c.get("recovered", 0.0))) > 0.005:
            L.append(_row("  change due to the investment income",
                          c.get("attributed"),
                          f"[the other income alone would recover "
                          f"{m(c.get('base_recovered', 0.0))}]"))
    for o, v in (c.get("lapsing_by_year") or {}).items():
        L.append(f"  {o}: {m(v)} left unrecovered expires after "
                 f"{doc['year']}")
    _created = c.get("created", a.get("carryforward",
                                      a.get("excess_fed", 0.0)))
    L.append(_row("Created this year (additional tax)", _created,
                  "[s.120.2(3); T691 Part 7 line 14]"
                  if float(_created or 0.0) > 0.005 else ""))
    closing = c.get("closing_by_year") or {}
    try:
        nxt = int(doc["year"]) + 1
    except (TypeError, ValueError):
        nxt = "next year"
    L.append(_row(f"Carried forward to {nxt}", c.get("closing", 0.0),
                  ("[" + ", ".join(f"{o} {m(v)}"
                                   for o, v in closing.items()) + "]")
                  if closing else ""))
    L.append("")
    L.append(_row("Estimated tax on investment income",
                  doc.get("estimated_tax")))
    # Incremental: the top-up the other income owes on its own is not
    # counted (backlog TJ-0489).
    _base_top = float(a.get("base_topup") or 0.0)
    L.append(_row("  with AMT and carryover", doc.get(
        "estimated_tax_with_amt"),
        f"[less the other income's own top-up {m(_base_top)}]"
        if _base_top > 0.005 else ""))
    notes = doc.get("notes") or []
    if notes and details:
        L += _notes(notes)
    elif not details:
        L.append(f"Not modelled: donations, the stock-option deduction"
                 + (f"; {len(notes)} note(s)" if notes else "")
                 + " — tjs amt --details")
    return L


def render_recorded(year: Any, label: str, mt: Dict[str, Any]
                    ) -> List[str]:
    """A closed year's minimum tax as its close-year lock recorded it."""
    L = [f"MINIMUM TAX (AMT) — tax year {year}, as recorded by close-year"]
    L += _out.wrap(f"Lock: {label}.")
    L += _out.wrap(f"Law: {LAW}.")
    L.append("")
    L += _out.wrap(str(mt.get('opening_source') or '?'), None,
                   "  Opening carryover from: ", "    ")
    for o, v in (mt.get("opening_by_year") or {}).items():
        L.append(f"    {o}: {m(v)} (usable through {int(o) + 7})")
    for o, v in (mt.get("expired_by_year") or {}).items():
        L.append(f"    expired: {o} {m(v)}")
    L.append(_row("Recovered: federal (line 40427)",
                  mt.get("recovered_federal")))
    if mt.get("recovered_provincial_credit") is not None:
        L.append(_row("Recovered: provincial share",
                      mt.get("recovered_provincial_credit")))
        _sur = (float(mt.get("recovered_provincial") or 0.0)
                - float(mt.get("recovered_provincial_credit") or 0.0))
        if abs(_sur) > 0.005:
            L.append(_row("  provincial surtax it saves", _sur))
    else:
        # A lock written before the share was recorded apart: its figure
        # is the whole provincial drop (backlog TJ-0016).
        L.append(_row("Recovered: provincial tax (surtax included)",
                      mt.get("recovered_provincial")))
    L.append(_row("Created (additional tax)", mt.get("created")))
    L.append(_row(f"Carried forward to {int(year) + 1}", mt.get("closing")))
    for o, v in (mt.get("closing_by_year") or {}).items():
        L.append(f"    {o}: {m(v)}")
    notes = []
    if not mt.get("other_income_entered"):
        notes.append("The record was computed with no other income "
                     "entered ([estimate] other_income) — compare it "
                     "with the T691 you filed.")
    notes.append(f"The full computation is in the {year} project "
                 f"(`taxjson amt` there).")
    L += _notes(notes)
    return L
