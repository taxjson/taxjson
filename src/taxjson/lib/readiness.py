"""Whether a filing figure is complete: one record the reports carry
(`sum --json`, `form-export --json`) instead of a reader rebuilding it
from scattered keys and stderr (the 2026-10 critique: a command can
finish with plausible figures while something is left out).

`complete` is False while anything the totals do not account for is
open: the books are not the clean result of the current inputs, a sale
with an unknown cost is left out of the totals, a sale with no purchase
is in no total, or the engine's conservation check found a cost or a
quantity the books lost track of. Each is an `issues` entry with its
`kind` and what to do. The figures themselves are unchanged."""
from pathlib import Path
from typing import Any, Dict, Iterable, List

CONSERVATION_PREFIX = "warning: conservation: "


def conservation_lines(gains_paths: Iterable[Any]) -> List[str]:
    """The engine's conservation warnings (core._warn_stranded_basis,
    _verify_share_conservation) captured beside each gains file (its
    `.diag`, and the account's pre-wash `<acct>_gains.json.diag` for a
    wash file), each once."""
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
                if ln.startswith(CONSERVATION_PREFIX):
                    msg = ln[len(CONSERVATION_PREFIX):].strip()
                    if msg not in seen:
                        seen.add(msg)
                        out.append(msg)
    return out


def build(*, run_state: Iterable[str] = (), unknown_cost: int = 0,
          no_purchase: int = 0,
          conservation: Iterable[str] = ()) -> Dict[str, Any]:
    """{"complete": bool, "issues": [{"kind", "detail", "count"?}]}."""
    issues: List[Dict[str, Any]] = []
    for t in run_state:
        issues.append({"kind": "run-state", "detail": str(t),
                       "fix": "taxjson run, then taxjson checklist"})
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
    return {"complete": not issues, "issues": issues}
