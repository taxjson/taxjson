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


def spinoffs(root: Path, cfg: Dict[str, Any],
             account: Optional[str] = None) -> List[Dict[str, Any]]:
    root = Path(root)
    cache = root / "work"
    base_cur = (cfg.get("settings", {}) or {}).get("base_currency", "CAD")
    out = []
    for acct, acfg in sorted((cfg.get("accounts") or {}).items()):
        if account and acct != account:
            continue
        if (acfg or {}).get("crypto"):
            continue
        sheltered = (acfg or {}).get("type") != "taxable"
        rows = _rows(cache / f"{acct}_base.json")
        man = _manifest(root, acct)
        by_ev: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            if r.get("corp_event_id"):
                by_ev.setdefault(r["corp_event_id"], []).append(r)
        for eid, rec in sorted(man.items()):
            summary = rec.get("summary") or ""
            if " spinoff:" not in summary and "spinoff" not in eid \
                    and not str(rec.get("election", "")).startswith(
                        ("rollover_s_86_1", "taxable_deemed_dividend",
                         "tax_free_355")):
                continue
            m = re.search(r"spinoff:\s*(\S+)\s*→\s*([^\s(]+)", summary)
            parent, child = (m.group(1), m.group(2)) if m else ("?", "?")
            ratio = re.search(r"\(([\d.]+)-for-([\d.]+)", summary)
            fm = _FMV_RE.search(summary)
            broker_fmv = float(fm.group(1).replace(",", "")) if fm else 0.0
            broker_cur = fm.group(2) if fm else ""
            hints = rec.get("hints") or {}
            fmv_ps = hints.get("fmv_per_share")
            booked = by_ev.get(eid, [])
            income = sum(float(r.get("net_amount") or 0) for r in booked
                         if r.get("action") == "DIVIDEND")
            buys = [r for r in booked if r.get("action") == "BUYSELL"
                    and float(r.get("quantity") or 0) > 0]
            new_qty = sum(float(r.get("quantity") or 0) for r in buys)
            new_cost = sum(float(r.get("net_amount") or 0) for r in buys)
            date = (booked[0].get("date") if booked else eid[:8])
            if date and len(date) == 8 and date.isdigit():
                date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            now = _held(rows, child, "9999-12-31", True) if child != "?" \
                else 0.0
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
                    why.append("booked at $0 (no tax effect in a registered "
                               "account)")
            elif election == "rollover_s_86_1":
                why.append("s.86.1 election: no income; the parent's cost "
                           "is split between the two. File the election "
                           "with the return; the spin-off must be on "
                           "CRA's list")
                if not hints.get("allocated_acb"):
                    flags.append("NO-ALLOCATION")
                    why.append("no cost allocated to the new shares "
                               "(allocated_acb)")
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
                "ratio": (f"{ratio.group(1)}-for-{ratio.group(2)}"
                          if ratio else ""),
                "election": election, "fmv_per_share": fmv_ps,
                "broker_fmv": broker_fmv, "broker_currency": broker_cur,
                "income": round(income, 2), "new_qty": new_qty,
                "new_cost": round(new_cost, 2), "held_now": round(now, 6),
                "currency": base_cur, "flags": flags, "why": why})
    out.sort(key=lambda x: (x["date"] or "", x["account"]))
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


def render_spinoffs(items: List[Dict[str, Any]]) -> List[str]:
    L = [f"SPIN-OFFS ({len(items)})", ""]
    if not items:
        L.append("No spin-offs in the books.")
        return L
    for s in items:
        tag = f" [{', '.join(s['flags'])}]" if s["flags"] else ""
        L.append(f"{s['date']}  {s['account']:<8} {s['parent']} -> "
                 f"{s['child']}  {s['ratio']}  election: "
                 f"{s['election']}{tag}")
        fmv = (f"{s['fmv_per_share']:g} per share"
               if s["fmv_per_share"] else "0")
        L.append(f"      value used: {fmv}; booked: income "
                 f"{s['income']:,.2f} {s['currency']}, {s['new_qty']:g} new "
                 f"shares costing {s['new_cost']:,.2f} {s['currency']}; "
                 f"held now {s['held_now']:g}")
        for w in s["why"]:
            L.append(f"      - {w}")
    n = sum(1 for s in items if s["flags"])
    L.append("")
    L.append(f"{n} spin-off(s) need attention." if n else
             "Every spin-off is booked with a value.")
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
