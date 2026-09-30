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

_FMV_RE = re.compile(r"FMV\s+([\d,]+(?:\.\d+)?)\s+([A-Z]{3})")


def _rows(path: Path) -> List[Dict[str, Any]]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("transactions", []) if isinstance(doc, dict) else doc
    return [r for r in rows if isinstance(r, dict)]


def _manifest(root: Path, acct: str) -> Dict[str, Any]:
    for p in (Path(root) / "inputs" / acct / "manifest.json",
              Path(root) / "work" / f"{acct}_manifest.json"):
        try:
            return json.loads(p.read_text(encoding="utf-8")).get(
                "elections") or {}
        except (OSError, ValueError):
            continue
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
_SPIN_ELECTIONS = ("rollover_s_86_1", "taxable_deemed_dividend",
                   "tax_free_355", "taxable_distribution")


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
    except OSError:
        return out
    groups: Dict[str, List[Path]] = {}
    for ln in lines:
        kind, _, name = ln.partition("/")
        path = root / "inputs" / acct / name
        if EXTRACTORS.get(kind) is None or not path.exists():
            continue
        groups.setdefault(kind, []).append(path)
    for kind, paths in groups.items():
        # The run's own extraction (every file of the group as context,
        # broker-account copies combined), so the ids match its books.
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                evs = combine_broker_copies(
                    extract_events(EXTRACTORS[kind], paths, acct),
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
    base_cur = (cfg.get("settings", {}) or {}).get("base_currency", "CAD")
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
            hints = rec.get("hints") or {}
            fmv_ps = hints.get("fmv_per_share")
            # The value the booking used: a positive hint, else the
            # broker's own value (taxable_deemed_dividend defaults to it).
            from_broker = False
            if not fmv_ps and broker_fmv and ev is not None \
                    and ev.qty_received:
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
            election = rec.get("election") or "(none)"
            flags: List[str] = []
            why: List[str] = []
            if sheltered:
                why.append("registered account: no tax effect")
            if election == "taxable_deemed_dividend":
                why.append("default treatment: a dividend equal to the new "
                           "shares' fair market value, which is also their "
                           "cost")
                if not fmv_ps and not sheltered:
                    flags.append("ZERO-VALUE")
                    msg = ("booked at $0: no dividend income and a $0 cost "
                           "for the new shares, so a later sale overstates "
                           "the gain by the same amount")
                    if broker_fmv:
                        msg += (f"; the broker reported {broker_fmv:,.2f} "
                                f"{broker_cur}")
                    why.append(msg + ". Set it with `taxjson elect "
                               f"{acct} --set {eid}=taxable_deemed_dividend "
                               "--hint fmv_per_share=<value>`.")
                elif not fmv_ps:
                    why.append("booked at $0" + (
                        f" (the broker reported {broker_fmv:,.2f} "
                        f"{broker_cur})" if broker_fmv else ""))
            elif election == "rollover_s_86_1":
                why.append("s.86.1 election: no income; the parent's cost "
                           "is split between the two. File the election "
                           "with the return; the spin-off must be on "
                           "CRA's list")
                if not (hints.get("allocated_acb_cad")
                        or hints.get("allocated_acb")):
                    flags.append("NO-ALLOCATION")
                    why.append("no cost allocated to the new shares "
                               "(allocated_acb_cad)")
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


def render_spinoffs(doc: Dict[str, Any]) -> List[str]:
    items, stale = doc["spinoffs"], doc["stale"]
    L = [f"SPIN-OFFS ({len(items)})", ""]
    if not items:
        L.append("No spin-offs in the books.")
    for s in items:
        tag = f" [{', '.join(s['flags'])}]" if s["flags"] else ""
        L.append(f"{s['date']}  {s['account']:<8} {s['parent']} -> "
                 f"{s['child']}  {s['ratio']}  election: "
                 f"{s['election']}{tag}")
        fmv = (f"{s['fmv_per_share']:g} per share"
               + (" (the broker's value)" if s.get("value_from_broker")
                  else "")
               if s["fmv_per_share"] else "0")
        L.append(f"      value used: {fmv}; booked: income "
                 f"{s['income']:,.2f} {s['currency']}, {s['new_qty']:g} new "
                 f"shares costing {s['new_cost']:,.2f} {s['currency']}; "
                 f"held now {s['held_now']:g}")
        for w in s["why"]:
            L.append(f"      - {w}")
    n = sum(1 for s in items if s["flags"])
    if stale:
        L.append("")
        L.append(f"{len(stale)} saved spin-off election(s) match no event "
                 f"in the current inputs (saved by an older version, "
                 f"nothing booked):")
        for st in stale:
            L.append(f"      {st['account']}: {st['event_id']}  "
                     f"({st['summary']})")
            L.append(f"        remove: taxjson elect {st['account']} "
                     f"--reset --event {st['event_id']}")
    L.append("")
    if items:
        L.append(f"{n} spin-off(s) need attention." if n else
                 "Every taxable spin-off is booked with a value.")
    return L


def render_splits(items: List[Dict[str, Any]]) -> List[str]:
    L = [f"SPLITS AND CONSOLIDATIONS ({len(items)})", ""]
    if not items:
        L.append("No splits in the books.")
        return L
    for s in items:
        tag = f" [{', '.join(s['flags'])}]" if s["flags"] else ""
        ren = f" -> {s['new_symbol']}" if s["new_symbol"] and \
            s["new_symbol"] != s["symbol"] else ""
        kind = ("rename" if abs(s["ratio"] - 1) < 1e-9 and ren else
                "split" if s["ratio"] > 1 else "consolidation")
        L.append(f"{s['date']}  {s['account']:<8} {s['symbol']}{ren}  "
                 f"{kind} x{s['ratio']:g}  held {s['held_before']:g} -> "
                 f"{s['held_after']:g}{tag}")
        for w in s["why"]:
            L.append(f"      - {w}")
    n = sum(1 for s in items if s["flags"])
    L.append("")
    L.append(f"{n} split(s) need attention." if n else
             "No split is doubled, empty or fractional.")
    return L
