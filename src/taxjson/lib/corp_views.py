"""`taxjson spinoffs` and `taxjson splits`: every spin-off and every split
or consolidation in the books, with how it was booked and what to check.

Spin-offs (Canada): by default a spin-off is a dividend equal to the fair
market value of the new shares, and that value is also the new shares'
cost (`taxable_deemed_dividend`). The s.86.1 election (`rollover_s_86_1`)
instead splits the parent's cost between the two with no income; it is
only available for spin-offs on CRA's list and must be filed with the
return. The command shows the election, the value per share used, what
was booked (income and the new shares' cost, in the base currency), the
broker's own value when it reported one, and flags a zero value.

Splits: the ratio, holdings just before and after, and checks for a
split applied twice (two SPLIT rows for one security close together),
a no-op row, and a ratio that leaves a fractional share.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib.corp_actions import (ALLOCATED_BASIS_HINT, SHELTERED_DEFAULT,
                                      declares_zero_value)
from taxjson.lib.country import (CANADA, USA, display_name, home_currency,
                                 settings_country)
from taxjson.lib import project_layout as _PL

_FMV_RE = re.compile(r"FMV\s+([\d,]+(?:\.\d+)?)\s+([A-Z]{3})")


class ViewError(ValueError):
    """An input the view cannot read: the command refuses with this one
    line rather than reporting an empty (and wrongly clean) view."""


def _rows(path: Path) -> List[Dict[str, Any]]:
    """The rows of a work/<acct>_base.json. A missing file is an account
    with no books yet; an unreadable or malformed one is refused (it
    used to read as 'No splits in the books', A2-0969)."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ViewError(f"cannot read {path} ({e}) — re-run `taxjson run` "
                        f"to rebuild it") from None
    rows = doc.get("transactions", []) if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise ViewError(f"{path} holds no transaction list — re-run "
                        f"`taxjson run` to rebuild it")
    return [r for r in rows if isinstance(r, dict)]


def _manifest(root: Path, acct: str) -> Dict[str, Any]:
    """The account's saved elections, read the way `taxjson elect` reads
    them (Manifest.load, canonical inputs/<acct>/manifest.json first,
    else the legacy work/ copy). A malformed or unreadable manifest is
    refused with elect's one-line error (A2-0968): it crashed on a
    wrong-shape file and read an unreadable one as 'no elections'."""
    from taxjson.lib.corp_actions import Manifest, ManifestError
    for p in (_PL.inputs_dir(Path(root)) / acct / "manifest.json",
              Path(root) / "work" / f"{acct}_manifest.json"):
        if not p.exists() and not p.is_symlink():
            continue
        try:
            man = Manifest.load(p)
        except ManifestError as e:
            raise ViewError(str(e)) from None
        except OSError as e:
            raise ViewError(f"cannot read the elections manifest {p} "
                            f"({e.strerror or e})") from None
        return {eid: {"election": r.election, "hints": dict(r.hints or {}),
                      "summary": r.summary}
                for eid, r in man.records.items()}
    return {}


def _held(rows: List[Dict[str, Any]], sym: str, until: str,
          inclusive: bool) -> float:
    q = 0.0
    for r in sorted(rows, key=lambda r: (r.get("date") or "",
                                         r.get("time") or "")):
        d = r.get("date") or ""
        if d > until or (d == until and not inclusive):
            continue
        if r.get("action") in ("BUYSELL", "ASSIGN", "TRANSFER",
                               "OPENING_BALANCE") and r.get("symbol") == sym:
            q += float(r.get("quantity") or 0.0)
        elif r.get("action") == "SPLIT" and r.get("symbol") == sym:
            q *= float(r.get("quantity") or 1.0)
    return q


_SPIN_BOOKED_RE = re.compile(r"Spinoff\s+(\S+?)\s*\u2192\s*(\S+)")
# The spin-off elections of both countries (corp_actions.RULES_BY_COUNTRY
# keys; each key belongs to one country, so the wording below never mixes
# Canadian and US law).
_FMV_ELECTIONS = {
    "taxable_deemed_dividend": (
        "default treatment: a dividend equal to the new shares' fair "
        "market value, which is also their cost"),
    "taxable_distribution_301": (
        "§301 distribution: income equal to the new shares' fair market "
        "value, which is also their cost"),
}
_ALLOC_ELECTIONS = {
    "rollover_s_86_1": (
        "s.86.1 election: no income; the parent's cost is split between "
        "the two. File the election with the return; the spin-off must "
        "be on CRA's list"),
    "tax_free_355": (
        "§355 tax-free spin-off: no income; the basis moved to the new "
        "shares is the amount from the company's Form 8937 (§358(b))"),
}
_SPIN_ELECTIONS = tuple(_FMV_ELECTIONS) + tuple(_ALLOC_ELECTIONS)
_SHELTER_WORD = {CANADA: "registered account",
                 USA: "tax-advantaged account (IRA)"}


def _current_events(root: Path, acct: str) -> Dict[str, Any]:
    """The corporate-action events the broker extractors find in this
    account's inputs NOW, by event id (the same events `taxjson run`
    books). Elections saved under ids no current event has are stale."""
    import contextlib
    import io
    from taxjson.bin.taxjson_corp_actions import EXTRACTORS, extract_events
    from taxjson.lib.corp_actions import combine_broker_copies
    out: Dict[str, Any] = {}
    try:
        lines = (root / "work" / f"{acct}_sources.list").read_text(
            encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):       # re-audit A2-0795
        return out
    from taxjson.lib.symbol_codes import SUFFIX
    _codes = root / "work" / f"{acct}{SUFFIX}"
    _codes = _codes if _codes.is_file() else None
    groups: Dict[str, List[Path]] = {}
    for ln in lines:
        kind, _, name = ln.partition("/")
        path = _PL.inputs_dir(root) / acct / name
        if EXTRACTORS.get(kind) is None or not path.exists():
            continue
        groups.setdefault(kind, []).append(path)
    for kind, paths in groups.items():
        # The run's own extraction (every file of the group as context,
        # broker-account copies combined), so the ids match its books.
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                evs = combine_broker_copies(
                    extract_events(EXTRACTORS[kind], paths, acct,
                                   symbol_codes=_codes),
                    stream=io.StringIO())
        except Exception:
            continue
        for ev in evs:
            out[ev.event_id] = ev
    return out


def spinoffs(root: Path, cfg: Dict[str, Any],
             account: Optional[str] = None) -> Dict[str, Any]:
    root = Path(root)
    cache = root / "work"
    country = settings_country(cfg.get("settings"))
    # The country's currency when base_currency is unset, as run's
    # _base() reads it (re-audit A2-1275: a US view said CAD).
    base_cur = (cfg.get("settings", {}) or {}).get("base_currency") \
        or home_currency(country)
    shelter_word = _SHELTER_WORD[country]
    from taxjson.lib.corp_actions import RULES_BY_COUNTRY, election_keys
    own_keys = election_keys(country)
    spin_options = [k for k, _ in RULES_BY_COUNTRY[country]["spinoff"].options]
    out: List[Dict[str, Any]] = []
    stale: List[Dict[str, Any]] = []
    for acct, acfg in sorted((cfg.get("accounts") or {}).items()):
        if account and acct != account:
            continue
        if (acfg or {}).get("crypto"):
            continue
        sheltered = (acfg or {}).get("type") != "taxable"
        rows = _rows(cache / f"{acct}_base.json")
        man = _manifest(root, acct)
        events = {k: v for k, v in _current_events(root, acct).items()
                  if v.action_type == "spinoff"}
        by_ev: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            if r.get("corp_event_id") and str(
                    r.get("description") or "").startswith("Spinoff"):
                by_ev.setdefault(r["corp_event_id"], []).append(r)
        live = sorted(set(events) | set(by_ev))
        for eid, rec in sorted(man.items()):
            is_spin = (" spinoff:" in (rec.get("summary") or "")
                       or rec.get("election") in _SPIN_ELECTIONS)
            if is_spin and eid not in live:
                stale.append({"account": acct, "event_id": eid,
                              "summary": rec.get("summary") or ""})
        for eid in live:
            ev = events.get(eid)
            rec = man.get(eid) or {}
            booked = by_ev.get(eid, [])
            parent = child = "?"
            for r in booked:
                m = _SPIN_BOOKED_RE.search(str(r.get("description") or ""))
                if m:
                    parent, child = m.group(1), m.group(2)
                    break
            if ev is not None:
                if parent == "?":
                    parent = ev.source_symbol
                if child == "?":
                    child = ev.target_symbol
            ratio = (f"{ev.ratio:.6g} new per share ({ev.ratio_new:g} for "
                     f"{ev.ratio_old:g})" if ev else "")
            broker_fmv = float(getattr(ev, "target_fmv", 0) or 0) or \
                float(getattr(ev, "fmv", 0) or 0) if ev else 0.0
            broker_cur = ((getattr(ev, "target_currency", "") or
                           getattr(ev, "currency", "")) if ev else "")
            # A sheltered account's spin-off booked without asking
            # (CA-CORP-11 / US-CORP-12): no value used, not pending.
            defaulted = not rec and any(
                r.get("corp_election") == SHELTERED_DEFAULT for r in booked)
            hints = rec.get("hints") or {}
            fmv_ps = hints.get("fmv_per_share")
            # The value the booking used: a positive hint, else the
            # broker's own value (taxable_deemed_dividend defaults to it).
            from_broker = False
            if not fmv_ps and broker_fmv and ev is not None \
                    and ev.qty_received and not defaulted:
                fmv_ps = broker_fmv / ev.qty_received
                from_broker = True
            income = sum(float(r.get("net_amount") or 0) for r in booked
                         if r.get("action") == "DIVIDEND")
            buys = [r for r in booked if r.get("action") == "BUYSELL"
                    and float(r.get("quantity") or 0) > 0]
            new_qty = sum(float(r.get("quantity") or 0) for r in buys)
            new_cost = sum(float(r.get("net_amount") or 0) for r in buys)
            date = booked[0].get("date") if booked else (
                ev.date[:10] if ev else eid[:8])
            now = _held(rows, child, "9999-12-31", True) \
                if child != "?" else 0.0
            election = (SHELTERED_DEFAULT if defaulted
                        else rec.get("election") or "(none)")
            flags: List[str] = []
            why: List[str] = []
            if sheltered:
                why.append(f"{shelter_word}: no tax effect")
            if election == SHELTERED_DEFAULT:
                why.append("booked without asking: the new shares at $0 "
                           "cost, the parent keeps its cost (the holdings "
                           "view only). Set a value with `taxjson elect "
                           f"{acct} --set {eid}=<"
                           f"{'|'.join(spin_options)}>`.")
            elif election != "(none)" and election not in own_keys:
                # Another country's election (a project switched from
                # canada to usa keeps its rollover_s_86_1): `taxjson run`
                # refuses it, so it is described as invalid here — never
                # in the other country's law (re-audit A2-0725).
                flags.append("WRONG-COUNTRY")
                why.append(f"{election} is not an election of a "
                           f"{display_name(country)} project — `taxjson "
                           f"run` refuses it. Choose one: `taxjson elect "
                           f"{acct} --set {eid}="
                           f"<{'|'.join(spin_options)}>`.")
            elif election in _FMV_ELECTIONS:
                why.append(_FMV_ELECTIONS[election])
                if not fmv_ps and declares_zero_value(election, hints):
                    # The user's own $0 (a warrant distributed at no
                    # value): answered, not a flag.
                    why.append("booked at the $0 value you declared "
                               "(fmv_per_share=0): no dividend income and "
                               "a $0 cost for the new shares. To change "
                               f"it: `taxjson elect {acct} --redo --event "
                               f"{eid}`.")
                elif not fmv_ps and not sheltered:
                    flags.append("ZERO-VALUE")
                    msg = ("booked at $0: no dividend income and a $0 cost "
                           "for the new shares, so a later sale overstates "
                           "the gain by the same amount")
                    if broker_fmv:
                        msg += (f"; the broker reported {broker_fmv:,.2f} "
                                f"{broker_cur}")
                    why.append(msg + ". Set it with `taxjson elect "
                               f"{acct} --set {eid}={election} "
                               "--hint fmv_per_share=<value>`.")
                elif not fmv_ps:
                    why.append("booked at $0" + (
                        f" (the broker reported {broker_fmv:,.2f} "
                        f"{broker_cur})" if broker_fmv else ""))
            elif election in _ALLOC_ELECTIONS:
                why.append(_ALLOC_ELECTIONS[election])
                hint = ALLOCATED_BASIS_HINT[election]
                # The legacy `allocated_acb` key still books an s.86.1.
                given = [hints.get(hint)] + (
                    [hints.get("allocated_acb")]
                    if election == "rollover_s_86_1" else [])
                if not any(given):
                    flags.append("NO-ALLOCATION")
                    why.append("no cost allocated to the new shares "
                               f"({hint}): the parent keeps its whole "
                               "cost and the spin-off's sale books the "
                               "gain. Set it with `taxjson elect "
                               f"{acct} --set {eid}={election} --hint "
                               f"{hint}=<amount>`.")
            elif election == "ignore":
                flags.append("IGNORED")
                why.append("ignored: nothing booked; correct only for "
                           "broker noise")
            elif election == "(none)":
                flags.append("PENDING")
                why.append("no election yet: `taxjson elect --pending`")
            out.append({
                "account": acct, "sheltered": sheltered, "event_id": eid,
                "date": date, "parent": parent, "child": child,
                "ratio": ratio, "election": election,
                "fmv_per_share": fmv_ps, "value_from_broker": from_broker,
                "broker_fmv": broker_fmv,
                "broker_currency": broker_cur, "income": round(income, 2),
                "new_qty": new_qty, "new_cost": round(new_cost, 2),
                "held_now": round(now, 6), "currency": base_cur,
                "flags": flags, "why": why})
    out.sort(key=lambda x: (x["date"] or "", x["account"]))
    return {"spinoffs": out, "stale": stale}


def wrong_country_elections(root: Path, cfg: Dict[str, Any]
                            ) -> List[Dict[str, str]]:
    """Saved elections (any corporate action) the project's country does
    not know — e.g. a Canadian rollover_s_86_1 left in a project switched
    to usa. `taxjson run` stops on each; `taxjson elect --pending` lists
    them so 'No pending elections' never hides one (re-audit A2-0725)."""
    from taxjson.lib.corp_actions import election_keys
    country = settings_country(cfg.get("settings"))
    keys = election_keys(country)
    out: List[Dict[str, str]] = []
    for acct in sorted(cfg.get("accounts") or {}):
        for eid, rec in sorted(_manifest(Path(root), acct).items()):
            el = rec.get("election") or ""
            if el and el not in keys:
                out.append({"account": acct, "event_id": eid,
                            "election": el,
                            "summary": rec.get("summary") or ""})
    return out


def splits(root: Path, cfg: Dict[str, Any],
           account: Optional[str] = None,
           window_days: int = 10) -> List[Dict[str, Any]]:
    root = Path(root)
    cache = root / "work"
    out = []
    for acct, acfg in sorted((cfg.get("accounts") or {}).items()):
        if account and acct != account:
            continue
        rows = _rows(cache / f"{acct}_base.json")
        sp = [r for r in rows if r.get("action") == "SPLIT"]
        for r in sorted(sp, key=lambda r: (r.get("date") or "",
                                           r.get("symbol") or "")):
            sym = r.get("symbol") or ""
            new = (r.get("symbol_new") or "").strip()
            ratio = float(r.get("quantity") or 0.0)
            d = r.get("date") or ""
            before = _held(rows, sym, d, False)
            # after: the rename target from the split on
            after_sym = new or sym
            after = before * ratio if ratio else before
            flags: List[str] = []
            why: List[str] = []
            if abs(ratio - 1.0) < 1e-9 and (not new or new == sym):
                flags.append("NO-OP")
                why.append("ratio 1 and no new symbol: changes nothing")
            if abs(ratio) < 1e-12:
                flags.append("ZERO-RATIO")
                why.append("a zero ratio would erase the position")
            if abs(before) > 1e-9 and abs(after - round(after)) > 1e-6 \
                    and abs(before - round(before)) < 1e-9:
                flags.append("FRACTION")
                why.append(f"leaves {after:g} shares: expect cash in lieu "
                           f"for the fraction (booked as a small sale)")
            try:
                dd = datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                dd = None
            twins = []
            if dd:
                for o in sp:
                    if o is r or o.get("symbol") != sym:
                        continue
                    try:
                        od = datetime.strptime(o.get("date") or "",
                                               "%Y-%m-%d")
                    except ValueError:
                        continue
                    if abs((od - dd).days) <= window_days and \
                            abs(float(o.get("quantity") or 0) - ratio) < 1e-6:
                        twins.append(o.get("date"))
            if twins:
                flags.append("TWICE?")
                why.append(f"another {ratio:g} split of {sym} on "
                           f"{', '.join(sorted(twins))}: a split reported by "
                           f"two sources is applied twice")
            if re.search(r"\bROC\b|RETURN OF CAPITAL|CASH", str(
                    r.get("description") or ""), re.IGNORECASE):
                why.append("the event also mentions a cash or return-of-"
                           "capital leg: check it is booked")
            if abs(before) < 1e-9:
                why.append("nothing held just before: the split changes "
                           "nothing here")
            out.append({"account": acct,
                        "sheltered": (acfg or {}).get("type") != "taxable",
                        "date": d, "symbol": sym, "new_symbol": new,
                        "ratio": ratio, "held_before": round(before, 6),
                        "held_after": round(after, 6),
                        "after_symbol": after_sym,
                        "description": (r.get("description") or "")[:160],
                        "flags": flags, "why": why})
    out.sort(key=lambda x: (x["date"], x["account"], x["symbol"]))
    return out


def _hang(d, text: str, indent: str = "", hang: str = "  ") -> None:
    """A paragraph of `d` (lib/out.Doc) with a hanging indent."""
    from taxjson.lib.out import wrap
    for ln in wrap(text, d.w, indent, hang):
        d.line(ln)


def render_spinoffs(doc: Dict[str, Any],
                    width_: Optional[int] = None) -> List[str]:
    """The spin-offs in the house layout (docs/output-style.md): each
    event's headline, its figures as aligned `label:  value` lines and
    its notes as `- ` items; the last line says whether any needs
    attention."""
    from taxjson.lib.out import Doc, Verbatim
    items, stale = doc["spinoffs"], doc["stale"]
    d = Doc(f"SPIN-OFFS ({len(items)})", width_=width_)
    d.blank()
    if not items:
        d.para("No spin-offs in the books.")
    for k, s in enumerate(items):
        if k:
            d.blank()
        tag = f" [{', '.join(s['flags'])}]" if s["flags"] else ""
        _hang(d, f"{s['date']}  {s['account']}  {s['parent']} -> "
               f"{s['child']}  {s['ratio']}{tag}", "", "  ")
        fmv = (f"{s['fmv_per_share']:g} per share"
               + (" (the broker's value)" if s.get("value_from_broker")
                  else "")
               if s["fmv_per_share"] else "0")
        d.kv([("election", s["election"]), ("value used", fmv),
              ("booked", f"income {s['income']:,.2f} {s['currency']}, "
                         f"{s['new_qty']:g} new shares costing "
                         f"{s['new_cost']:,.2f} {s['currency']}"),
              ("held now", f"{s['held_now']:g}")], "  ")
        d.items(s["why"], "  ")
    n = sum(1 for s in items if s["flags"])
    if stale:
        d.blank()
        d.para(f"{len(stale)} saved spin-off election(s) match no event "
               f"in the current inputs (saved by an older version, "
               f"nothing booked):")
        for st in stale:
            d.item(f"{st['account']}: {st['event_id']}  "
                   f"({st['summary']})", "  ")
            d.kv([("remove", Verbatim(f"taxjson elect {st['account']} "
                                      f"--reset --event "
                                      f"{st['event_id']}"))], "    ")
    d.blank()
    if items:
        d.para(f"{n} spin-off(s) need attention." if n else
               "Every taxable spin-off is booked with a value.")
    return d.lines()


def render_splits(items: List[Dict[str, Any]],
                  width_: Optional[int] = None) -> List[str]:
    """The splits in the house layout: one line per split (its holdings
    before and after), its notes as `- ` items under it; the last line
    says whether any needs attention."""
    from taxjson.lib.out import Doc
    d = Doc(f"SPLITS AND CONSOLIDATIONS ({len(items)})", width_=width_)
    d.blank()
    if not items:
        d.para("No splits in the books.")
        return d.lines()
    for s in items:
        tag = f" [{', '.join(s['flags'])}]" if s["flags"] else ""
        ren = f" -> {s['new_symbol']}" if s["new_symbol"] and \
            s["new_symbol"] != s["symbol"] else ""
        kind = ("rename" if abs(s["ratio"] - 1) < 1e-9 and ren else
                "split" if s["ratio"] > 1 else "consolidation")
        _hang(d, f"{s['date']}  {s['account']:<8} {s['symbol']}{ren}  "
               f"{kind} x{s['ratio']:g}  held {s['held_before']:g} -> "
               f"{s['held_after']:g}{tag}", "", "  ")
        d.items(s["why"], "  ")
    n = sum(1 for s in items if s["flags"])
    d.blank()
    d.para(f"{n} split(s) need attention." if n else
           "No split is doubled, empty or fractional.")
    return d.lines()
