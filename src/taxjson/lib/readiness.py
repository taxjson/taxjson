"""Whether a filing figure is complete: one record the reports carry
(`sum --json`, `form-export --json`) instead of a reader rebuilding it
from scattered keys and stderr (the 2026-10 critique: a command can
finish with plausible figures while something is left out).

`complete` is False while anything the totals do not account for is
open: the books are not the clean result of the current inputs, a sale
with an unknown cost is left out of the totals, a sale with no purchase
is in no total, the engine's conservation check found a cost or a
quantity the books lost track of, or an option assignment's premium
found no stock leg to fold into, or dividend compensation paid on a
short still inside its 45 days has no settled treatment. Each is an `issues` entry with its
`kind` and what to do. The figures themselves are unchanged."""
from pathlib import Path
from typing import Any, Dict, Iterable, List

CONSERVATION_PREFIX = "warning: conservation: "
# The engine's warning that an ASSIGN option leg's premium found no
# stock leg to fold into: that premium is in no gain
# (core._warn_undrained_adjustments; backlog TJ-0824).
UNCONSUMED_PREFIX = "warning: "
UNCONSUMED_MARK = "unconsumed option-assignment adjustment(s)"
# The US engine's warning that dividend compensation paid on a short still
# open at the last row, inside its 45 days, has no settled treatment: the
# year's income moves if the short is covered in time (§263(h); backlog
# TJ-1065).
UNSETTLED_COMPENSATION_MARK = "ATTENTION: dividend compensation not settled"


def _diag_lines(gains_paths: Iterable[Any], keep) -> List[str]:
    """The lines `keep` returns a message for, captured beside each gains
    file (its `.diag`, and the account's pre-wash
    `<acct>_gains.json.diag` for a wash file), each once."""
    out: List[str] = []
    seen = set()
    for p in gains_paths:
        p = Path(p)
        cands = [p.with_name(p.name + ".diag")]
        if p.name.endswith("_gains_wash.json"):
            base = p.name[:-len("_gains_wash.json")] + "_gains.json.diag"
            cands.append(p.with_name(base))
        for d in cands:
            try:
                text = d.read_text(errors="replace")
            except OSError:
                continue
            for ln in text.splitlines():
                msg = keep(ln)
                if msg and msg not in seen:
                    seen.add(msg)
                    out.append(msg)
    return out


def conservation_lines(gains_paths: Iterable[Any]) -> List[str]:
    """The engine's conservation warnings (core._warn_stranded_basis,
    _verify_share_conservation) captured beside each gains file, each
    once."""
    def keep(ln: str) -> str:
        return (ln[len(CONSERVATION_PREFIX):].strip()
                if ln.startswith(CONSERVATION_PREFIX) else "")
    return _diag_lines(gains_paths, keep)


def unconsumed_premium_lines(gains_paths: Iterable[Any]) -> List[str]:
    """The engine's warnings that an assignment's premium is in no gain
    (core._warn_undrained_adjustments) captured beside each gains file,
    each once."""
    def keep(ln: str) -> str:
        if not ln.startswith(UNCONSUMED_PREFIX) or UNCONSUMED_MARK not in ln:
            return ""
        return ln[len(UNCONSUMED_PREFIX):].strip()
    return _diag_lines(gains_paths, keep)


def unsettled_compensation_lines(gains_paths: Iterable[Any]) -> List[str]:
    """The US engine's warnings that dividend compensation paid on a
    short has no settled treatment yet (backlog TJ-1065), each once."""
    prefix = f"warning: {UNSETTLED_COMPENSATION_MARK}: "

    def keep(ln: str) -> str:
        return ln[len(prefix):].strip() if ln.startswith(prefix) else ""
    return _diag_lines(gains_paths, keep)


def build(*, run_state: Iterable[str] = (), unknown_cost: int = 0,
          no_purchase: int = 0,
          conservation: Iterable[str] = (),
          in_kind_unbooked: int = 0,
          unconsumed_premium: Iterable[str] = (),
          unsettled_compensation: Iterable[str] = ()) -> Dict[str, Any]:
    """{"complete": bool, "issues": [{"kind", "detail", "count"?}]}."""
    issues: List[Dict[str, Any]] = []
    for t in run_state:
        issues.append({"kind": "run-state", "detail": str(t),
                       "fix": "taxjson run, then taxjson checklist --run"})
    if unknown_cost:
        issues.append({
            "kind": "unknown-cost", "count": int(unknown_cost),
            "detail": f"{int(unknown_cost)} sale(s) with an unknown cost "
                      f"are not in the totals",
            "fix": "taxjson find-missing-history; report them by hand "
                   "once their cost is known"})
    if no_purchase:
        issues.append({
            "kind": "no-purchase", "count": int(no_purchase),
            "detail": f"{int(no_purchase)} sale(s) with no purchase in "
                      f"the files are in no total",
            "fix": "taxjson find-missing-history"})
    for ln in conservation:
        issues.append({"kind": "conservation", "detail": str(ln),
                       "fix": "taxjson audit; check the account's rows "
                              "for the symbol"})
    for ln in unconsumed_premium:
        # An assignment's premium no stock leg took: in no gain or
        # cost (backlog TJ-0824).
        issues.append({"kind": "unconsumed-premium", "detail": str(ln),
                       "fix": "add (or correct) the assignment's stock "
                              "delivery rows, then taxjson run"})
    for ln in unsettled_compensation:
        # The year's income moves if the short is covered in time.
        issues.append({"kind": "compensation-not-settled", "detail": str(ln),
                       "fix": "add the later rows (the cover, or the 45th "
                              "day passed), then taxjson run"})
    if in_kind_unbooked:
        # A contribution's deemed sale (a withdrawal's purchase) the run
        # could not value: missing from the books (backlog TJ-0095).
        issues.append({
            "kind": "in-kind-not-booked", "count": int(in_kind_unbooked),
            "detail": f"{int(in_kind_unbooked)} in-kind move(s) with a "
                      f"registered account are NOT booked",
            "fix": "taxjson transfers; value each with a .tt INKIND line, "
                   "then taxjson run"})
    return {"complete": not issues, "issues": issues}
