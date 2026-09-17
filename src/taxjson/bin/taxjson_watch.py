"""taxjson watch — cron-able change detector over the wash radar (and,
optionally, the harvest schedule).

The radar and harvest answer "what is the state NOW"; watch answers
"what CHANGED since I last looked". It compares the live combined
radar against the state saved by the previous watch run
(work/.watch_state.json) and reports only transitions:

  - a new actionable advisory (VIOLATION appearing, a LOCKED position)
  - a category change (LOCKED -> CLEAR: the loss became claimable)
  - a clear date pushed out (a new buy extended the window)
  - an advisory disappearing (position closed / window passed)
  - with --harvest: the harvestable-now loss total moving by more
    than --threshold

Quiet run (no changes) prints NOTHING and exits 0 — under cron, no
output means no mail. `--exit-code` makes changes exit 1 for shell
scripting; the default stays 0 so `taxjson run watch` chains never
break on a mere change report. The first run records a baseline and
says so. State updates on every run (atomic write).
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_VERSION = 1

# Categories worth announcing on first sight. CLEAR is tracked in
# state (so LOCKED->CLEAR transitions can be reported) but a NEW
# ticker that is already CLEAR is noise.
_ACTIONABLE = ("VIOLATION", "BLOCKED", "LOCKED", "EXITABLE",
               "CAUTION", "COOLING", "RISK")


def flatten_radar(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """{ticker: {category, advisory, clears_at}} from a radar JSON
    document (the --json / --json-out payload)."""
    out: Dict[str, Dict[str, Any]] = {}
    for sec in doc.get("sections") or []:
        for r in sec.get("rows") or []:
            t = r.get("ticker")
            if t and t not in out:
                out[t] = {"category": r.get("category") or "",
                          "advisory": r.get("advisory") or "",
                          "clears_at": r.get("clears_at"),
                          # buy-check/sell-check need the scope: a
                          # sheltered-ONLY holding must not be told
                          # to "sell at a loss", and a sheltered
                          # in-window buy makes a violation
                          # permanent rather than rescueable.
                          "taxable_qty": r.get("taxable_qty"),
                          "sheltered_qty": r.get("sheltered_qty")}
    return out


def diff_radar(prev: Dict[str, Dict[str, Any]],
               cur: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Transition records, stable order (severity of the CURRENT state,
    then ticker). Each: {kind, ticker, ...} with a human `line`."""
    changes: List[Dict[str, Any]] = []

    def _known(rec):
        # The --all radar also emits rows with an EMPTY category (no
        # advisory — e.g. a position fully exited at a gain). Treating
        # those as states produced garbled transitions ("CLEAR -> ")
        # and false "safe to sell at a loss" lines; normalize them to
        # absent.
        if rec is None:
            return None
        cat = rec.get("category") or ""
        return rec if cat in _ACTIONABLE or cat == "CLEAR" else None

    for t in sorted(set(prev) | set(cur)):
        p, c = _known(prev.get(t)), _known(cur.get(t))
        if c is None:
            if p and p.get("category") in _ACTIONABLE:
                changes.append({
                    "kind": "gone", "ticker": t,
                    "was": p.get("category"),
                    "line": f"{t}: {p.get('category')} advisory gone "
                            f"(position closed or window passed)"})
            continue
        ccat = c.get("category") or ""
        if p is None:
            if ccat in _ACTIONABLE:
                changes.append({
                    "kind": "new", "ticker": t, "category": ccat,
                    "line": f"{t}: NEW {ccat} — {c.get('advisory')}"})
            continue
        pcat = p.get("category") or ""
        if ccat != pcat:
            if ccat == "CLEAR":
                changes.append({
                    "kind": "cleared", "ticker": t, "was": pcat,
                    "line": f"{t}: {pcat} -> CLEAR — safe to sell at a "
                            f"loss (don't buy back for 30 days)"})
            else:
                # Advisory text starts with "<CAT>: ..." — strip the
                # prefix so the line doesn't say the category twice.
                adv = (c.get("advisory") or "")
                if adv.startswith(f"{ccat}:"):
                    adv = adv[len(ccat) + 1:].strip()
                changes.append({
                    "kind": "changed", "ticker": t, "was": pcat,
                    "category": ccat,
                    "line": f"{t}: {pcat} -> {ccat} — {adv}"})
            continue
        pc, cc = p.get("clears_at"), c.get("clears_at")
        if pc != cc and (pc or cc):
            # Explain the move only when it actually moved LATER on a
            # window category — a VIOLATION's date is a deadline, and
            # an earlier date isn't an extension (2026-09 audit).
            _why = (" (a new buy extended the window)"
                    if (pc and cc and cc > pc
                        and ccat != "VIOLATION") else "")
            changes.append({
                "kind": "clears_moved", "ticker": t, "category": ccat,
                "was": pc, "clears_at": cc,
                "line": f"{t}: clear date moved {pc or '?'} -> "
                        f"{cc or '?'}{_why}"})
            continue
        # Same category, same clears — but a materially different
        # ADVISORY (e.g. a VIOLATION's required sell quantity doubled
        # after a further buy) is still news; keying the diff on
        # category+clears alone swallowed it (2026-09 audit).
        pa, ca = (p.get("advisory") or ""), (c.get("advisory") or "")
        if pa != ca and ca and ccat not in ("", "CLEAR"):
            adv = ca
            if adv.startswith(f"{ccat}:"):
                adv = adv[len(ccat) + 1:].strip()
            changes.append({
                "kind": "advisory_changed", "ticker": t,
                "category": ccat,
                "line": f"{t}: {ccat} advisory changed — {adv}"})
    _rank = {"new": 0, "changed": 1, "advisory_changed": 2,
             "cleared": 3, "clears_moved": 4, "gone": 5}
    changes.sort(key=lambda ch: (_rank.get(ch["kind"], 9),
                                 ch["ticker"]))
    return changes


def diff_harvest(prev_now: Optional[float], cur_now: float,
                 threshold: float) -> Optional[Dict[str, Any]]:
    """A change record when the harvestable-NOW loss total moved by
    more than `threshold` (absolute, base currency)."""
    if prev_now is None:
        return None
    delta = cur_now - prev_now
    if abs(delta) <= threshold:
        return None
    return {"kind": "harvest_now", "was": round(prev_now, 2),
            "now": round(cur_now, 2),
            "line": f"harvestable-now losses: {prev_now:,.2f} -> "
                    f"{cur_now:,.2f} ({delta:+,.2f})"}


def load_state(path: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if doc.get("schema_version") != STATE_VERSION:
        # Older/newer state: treat as no baseline rather than diffing
        # across incompatible shapes.
        return None
    return doc


def save_state(path: Path, radar: Dict[str, Dict[str, Any]],
               harvest_now: Optional[float],
               as_of: str) -> None:
    doc: Dict[str, Any] = {"schema_version": STATE_VERSION,
                           "as_of": as_of, "radar": radar}
    if harvest_now is not None:
        doc["harvest_now"] = round(harvest_now, 2)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def render_report(changes: List[Dict[str, Any]], as_of: str,
                  since: Optional[str] = None) -> str:
    prev = f" (previous baseline {since})" if since else ""
    lines = [f"WATCH — {len(changes)} change(s) since the last run"
             f"{prev} (as of {as_of})", ""]
    lines += [f"  {ch['line']}" for ch in changes]
    return "\n".join(lines)


if __name__ == "__main__":                       # pragma: no cover
    sys.exit("taxjson-watch is not a standalone tool — use "
             "`taxjson watch` (it needs the project's radar inputs).")
