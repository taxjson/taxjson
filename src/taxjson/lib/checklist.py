"""`taxjson checklist` — the filing checklist (docs/filing.md) as a command.

Every step names the command that proves it. A detector runs that
command (or inspects the project) and reports one of:

  done       the evidence is clean
  attention  the evidence says something is wrong (fix, then re-check)
  todo       the evidence is missing (the step has not been started)
  manual     no evidence can prove it — the user confirms with
             `taxjson checklist --done ID`
  blocked    a prerequisite (usually `taxjson run`) is missing
  n/a        the step does not apply to this project

Overrides live in `checklist.json` at the project root (commit it): a
step the user marks done or skipped keeps that mark until `--undo` or
`--reset`. An override never hides a detector's finding — a step marked
done whose detector later says `attention` is shown as done with the
finding beside it, so a stale mark is visible rather than silent.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

STATE_FILE = "checklist.json"

STAGES = [
    (1, "Freeze the inputs"),
    (2, "Build and clean"),
    (3, "Reconcile to the slips"),
    (4, "Produce the filing numbers"),
    (5, "File and lock"),
    (6, "After assessment"),
]

# Order matters: it is the order of docs/filing.md.
# (id, stage, title, proves-it command, why)
STEPS: List[Tuple[str, int, str, str, str]] = [
    ("inputs-frozen", 1, "Full-year activity plus January of the next year",
     "taxjson fetch / broker exports",
     "December trades settle in January and option closes after year end change the year."),
    ("sheltered-inputs", 1, "Sheltered accounts' activity present",
     "inputs/<sheltered>/",
     "RRSP/LIRA/TFSA/RESP purchases decide the superficial-loss rule for the taxable accounts."),
    ("crypto-inputs", 1, "Crypto ledgers and trades for the full year",
     "inputs/<crypto>/",
     "Every disposition of a coin, including swaps and fees, is a capital event."),
    ("roc-entered", 1, "Return of capital (T3 box 42) entered before trusting any ACB",
     "ADJUST lines / distributions.map",
     "Some funds publish ROC factors only after year end; without them the ACB is overstated."),
    ("inputs-committed", 1, "Inputs, config, maps and manifests committed",
     "git status",
     "The filed books must be rebuildable years later."),
    ("run-clean", 2, "Full run with zero validation errors and nothing pending",
     "taxjson run",
     "A validation error means a row the engine could not book; a pending election means an account was skipped."),
    ("sanity", 2, "Positions tie to the broker holdings",
     "taxjson sanity",
     "The only acceptable differences are trades after the last export."),
    ("missing-history", 2, "No position with missing cost basis affects the year",
     "taxjson find-missing-history",
     "A sale drawing on missing basis is booked at $0 cost — the gain is overstated by the missing amount."),
    ("elections", 2, "No unresolved merger or spin-off election",
     "taxjson elect --pending",
     "A deferred election leaves the account out of the run."),
    ("audit", 2, "Every disposition traced and tied",
     "taxjson audit",
     "The audit walks each sale from the broker row to the reported gain."),
    ("wash-reviewed", 2, "Every superficial-loss denial reviewed",
     "taxjson wash-sales",
     "A permanently denied loss (registered-account repurchase) is money gone; make sure each is real."),
    ("option-boundary", 2, "Year-straddling written options need no prior-year amendment",
     "taxjson option-boundary",
     "Under ITA s.49 an assignment in a later year moves the premium; a filed year may need a T1-ADJ."),
    ("t5008", 3, "T5008 slips reconcile to the computed dispositions",
     "taxjson reconcile-slips inputs/slips/*.csv",
     "The CRA matches Schedule 3 proceeds to the T5008s — this step prevents the review letter."),
    ("t5-t3", 3, "T5 / T3 / NR4 slips agree with the dividend and ROC totals",
     "taxjson divs-sum, taxjson roc-sum",
     "Trust units and split-share corps report on a T3, often weeks after the T5s."),
    ("foreign-tax", 3, "Foreign tax withheld taken from the slips (line 40500 / T2209)",
     "T5 box 15/16, T3 box 33/34",
     "The credit is limited to what the slips show, not what the broker rows imply."),
    ("form-export", 4, "Schedule 3 rows exported and their total equals the report",
     "taxjson form-export",
     "The export is what goes on the return; the .sum is what the engine computed — they must agree."),
    ("t1135", 4, "T1135 filed when foreign property cost exceeded CAD 100,000",
     "taxjson t1135",
     "ITA 233.3: the test is on cost at any time in the year, not year-end value."),
    ("carryover", 4, "Net capital losses of other years applied and recorded",
     "taxjson carryover, claimed_losses.txt",
     "Line 25300; the ledger only knows what was claimed if you write it down."),
    ("fx-cash", 4, "FX gain on foreign cash reviewed (ITA s.39(1.1), $200 de minimis)",
     "taxjson fx-cash",
     "Foreign currency is property; the net gain above $200 is a capital gain."),
    ("fees", 4, "Carrying charges collected for line 22100",
     "taxjson fees",
     "Margin interest and data subscriptions are deductible; the tool reports, it does not deduct."),
    ("estimate", 4, "Tax estimate and instalment position checked",
     "taxjson estimate, taxjson instalments",
     "A sanity check on the tax owed and on what was already paid."),
    ("filed-lock", 5, "Return filed and the year locked",
     "taxjson close-year",
     "The lock is what check-filed and option-boundary use to detect drift and to word a T1-ADJ."),
    ("lock-committed", 5, "The filed/<year>.json lock committed",
     "git status filed/",
     "The lock is the record of what was filed."),
    ("noa", 6, "Notice of Assessment compared; net tax owing carried into next year's [instalments]",
     "prior_year_net_tax",
     "CRA charges interest on the least of the methods your figures support."),
]

SYMBOL = {"done": "[x]", "attention": "[!]", "todo": "[ ]", "manual": "[m]",
          "blocked": "[b]", "n/a": "[-]", "skipped": "[~]"}


@dataclass
class Result:
    id: str
    status: str
    detail: str = ""
    override: Optional[str] = None      # "done" | "skipped"
    note: str = ""
    finding: str = ""                   # detector status when an override hides it

    @property
    def effective(self) -> str:
        return self.override or self.status

    @property
    def passed(self) -> bool:
        return self.effective in ("done", "n/a", "skipped")


@dataclass
class Ctx:
    root: Path
    cfg: Dict[str, Any]
    year: int
    today: date
    run_sub: Callable[..., Tuple[int, str, str]]
    cache: Path = field(init=False)
    reports: Path = field(init=False)

    def __post_init__(self) -> None:
        self.cache = self.root / "work"
        self.reports = self.root / "reports"

    @property
    def settings(self) -> Dict[str, Any]:
        return self.cfg.get("settings") or {}

    @property
    def accounts(self) -> Dict[str, Dict[str, Any]]:
        return self.cfg.get("accounts") or {}

    def sub(self, *argv: str, timeout: int = 900) -> Tuple[int, str, str]:
        return self.run_sub(list(argv), timeout=timeout)


# ------------------------------------------------------------------ helpers
def default_run_sub(root: Path) -> Callable[..., Tuple[int, str, str]]:
    """Run `taxjson <argv>` on this project as a subprocess (the sub-
    commands sys.exit and print; a subprocess keeps that contained)."""
    def run(argv: List[str], timeout: int = 900) -> Tuple[int, str, str]:
        cmd = [sys.executable, "-m", "taxjson.bin.taxjson_run",
               "-C", str(root)] + argv
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=timeout,
                               env={**os.environ, "NO_COLOR": "1"})
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
        return p.returncode, p.stdout or "", p.stderr or ""
    return run


def _data_files(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and not p.name.startswith(".")
                  and p.suffix.lower() in (".csv", ".tt", ".txt", ".xlsx")
                  and p.name != "README.txt")


def _base_docs(ctx: Ctx, names: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for n in names:
        f = ctx.cache / f"{n}_base.json"
        if f.is_file():
            try:
                out[n] = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return out


def _git(root: Path, *args: str) -> Tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(root)] + list(args),
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return p.returncode, (p.stdout or "")


def _is_git_repo(root: Path) -> bool:
    code, out = _git(root, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _accounts_of(ctx: Ctx, kind: str) -> List[str]:
    out = []
    for name, a in ctx.accounts.items():
        if kind == "taxable" and a.get("type") == "taxable" and not a.get("crypto"):
            out.append(name)
        elif kind == "sheltered" and a.get("type") == "sheltered":
            out.append(name)
        elif kind == "crypto" and a.get("crypto"):
            out.append(name)
    return out


# ---------------------------------------------------------------- detectors
def d_inputs_frozen(ctx: Ctx) -> Result:
    missing = [n for n in ctx.accounts
               if not _data_files(ctx.root / "inputs" / n)
               and (ctx.accounts[n].get("type") == "taxable")]
    if missing:
        return Result("inputs-frozen", "todo",
                      f"no activity files in inputs/{', inputs/'.join(missing)}")
    docs = _base_docs(ctx, _accounts_of(ctx, "taxable") + _accounts_of(ctx, "crypto"))
    if not docs:
        return Result("inputs-frozen", "blocked", "no work/*_base.json — run `taxjson run`")
    latest = ""
    for d in docs.values():
        for t in d.get("transactions") or []:
            latest = max(latest, str(t.get("date_settle") or t.get("date") or ""))
    cutoff = date(ctx.year + 1, 1, 31)
    try:
        latest_d = date.fromisoformat(latest[:10]) if latest else None
    except ValueError:
        latest_d = None
    if latest_d and latest_d >= cutoff:
        return Result("inputs-frozen", "done", f"latest activity {latest[:10]}")
    if ctx.today <= cutoff:
        return Result("inputs-frozen", "todo",
                      f"year still open — latest activity {latest[:10] or '?'}; "
                      f"re-export after {cutoff.isoformat()}")
    return Result("inputs-frozen", "attention",
                  f"latest activity {latest[:10] or '?'} — January {ctx.year + 1} "
                  f"is not in the books yet")


def d_sheltered_inputs(ctx: Ctx) -> Result:
    names = _accounts_of(ctx, "sheltered")
    if not names:
        return Result("sheltered-inputs", "n/a", "no sheltered account configured")
    empty = [n for n in names if not _data_files(ctx.root / "inputs" / n)]
    if empty:
        return Result("sheltered-inputs", "attention",
                      f"empty: {', '.join(empty)} — affiliated purchases unseen")
    return Result("sheltered-inputs", "done", ", ".join(names))


def d_crypto_inputs(ctx: Ctx) -> Result:
    names = _accounts_of(ctx, "crypto")
    if not names:
        return Result("crypto-inputs", "n/a", "no crypto account configured")
    empty = [n for n in names if not _data_files(ctx.root / "inputs" / n)]
    if empty:
        return Result("crypto-inputs", "todo", f"empty: {', '.join(empty)}")
    return Result("crypto-inputs", "done", ", ".join(names))


def d_roc_entered(ctx: Ctx) -> Result:
    docs = _base_docs(ctx, _accounts_of(ctx, "taxable"))
    adjust = sum(1 for d in docs.values()
                 for t in (d.get("transactions") or [])
                 if t.get("action") == "ADJUST"
                 and str(t.get("date") or "").startswith(str(ctx.year)))
    dmap = (ctx.root / "distributions.map").is_file()
    return Result("roc-entered", "manual",
                  f"{adjust} ADJUST row(s) in {ctx.year}; distributions.map "
                  f"{'present' if dmap else 'absent'}")


def d_inputs_committed(ctx: Ctx) -> Result:
    if not _is_git_repo(ctx.root):
        return Result("inputs-committed", "attention", "not a git repository")
    paths = ["inputs", "taxjson.toml", "ticker.map", "phantoms.json",
             "distributions.map", "claimed_losses.txt",
             "ticker_extraction_overrides.txt"]
    paths = [p for p in paths if (ctx.root / p).exists()]
    code, out = _git(ctx.root, "status", "--porcelain", "--", *paths)
    dirty = [ln for ln in out.splitlines() if ln.strip()]
    if code != 0:
        return Result("inputs-committed", "attention", "git status failed")
    if dirty:
        return Result("inputs-committed", "todo",
                      f"{len(dirty)} uncommitted change(s) under inputs/config")
    return Result("inputs-committed", "done", "clean")


def d_run_clean(ctx: Ctx) -> Result:
    sums = sorted(ctx.reports.glob("*.sum")) if ctx.reports.is_dir() else []
    if not sums:
        return Result("run-clean", "blocked", "no reports — run `taxjson run`")
    errors = 0
    for s in sums:
        try:
            head = s.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            continue
        m = re.search(r"validation: (\d+) error", head)
        if m:
            errors += int(m.group(1))
    pend = [p for p in ctx.cache.glob("*pending_elections.json")
            if p.is_file() and p.stat().st_size > 2]
    newest_input = 0.0
    for p in [ctx.root / "taxjson.toml", ctx.root / "ticker.map"] + \
            [f for n in ctx.accounts for f in _data_files(ctx.root / "inputs" / n)]:
        if p.is_file():
            newest_input = max(newest_input, p.stat().st_mtime)
    oldest_report = min(s.stat().st_mtime for s in sums)
    problems = []
    if errors:
        problems.append(f"{errors} validation error(s) in reports/*.sum")
    if pend:
        problems.append("pending elections (`taxjson elect --pending`)")
    if newest_input > oldest_report + 1:
        problems.append("inputs changed since the last run")
    if problems:
        return Result("run-clean", "attention", "; ".join(problems))
    stamp = datetime.fromtimestamp(oldest_report).strftime("%Y-%m-%d %H:%M")
    return Result("run-clean", "done", f"last run {stamp}, no validation errors")


def d_sanity(ctx: Ctx) -> Result:
    if not any(a.get("holdings") for a in ctx.accounts.values()):
        return Result("sanity", "manual",
                      "no `holdings = [...]` in taxjson.toml — run "
                      "`taxjson sanity ACCOUNT=FILE.toml` by hand")
    code, out, err = ctx.sub("sanity")
    if code == 0:
        return Result("sanity", "done", "positions tie to the holdings files")
    return Result("sanity", "attention", _last_line(out) or _last_line(err) or f"exit {code}")


def d_missing_history(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("find-missing-history")
    if code not in (0, 1) and not out:
        return Result("missing-history", "blocked", _last_line(err) or f"exit {code}")
    syms: List[str] = []
    in_affects = False
    for ln in out.splitlines():
        if ln.startswith("AFFECTS"):
            in_affects = True
            continue
        if ln.startswith("NOT relevant") or ln.startswith("To fix"):
            in_affects = False
        m = re.match(r"^([A-Z0-9.\-]+)\s+(\S+)\s+[A-Z]{3}\s", ln)
        if in_affects and m:
            syms.append(f"{m.group(1)} ({m.group(2)})")
    if syms:
        shown = ", ".join(syms[:4]) + (" ..." if len(syms) > 4 else "")
        return Result("missing-history", "attention",
                      f"{len(syms)} position(s) with missing basis affect "
                      f"{ctx.year}: {shown}")
    return Result("missing-history", "done", "nothing affects the year")


def d_elections(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("elect", "--pending")
    if "No pending elections" in out or (code == 0 and not out.strip()):
        return Result("elections", "done", "none pending")
    if code != 0 and not out:
        return Result("elections", "blocked", _last_line(err) or f"exit {code}")
    return Result("elections", "attention", _last_line(out))


def d_audit(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("audit")
    if not out:
        return Result("audit", "blocked", _last_line(err) or f"exit {code}")
    bad = 0
    notfound = 0
    events = 0
    for m in re.finditer(r"pipeline tie-out\s+([\d,]+) tied, ([\d,]+) MISMATCHED, ([\d,]+) not found", out):
        events += int(m.group(1).replace(",", ""))
        bad += int(m.group(2).replace(",", ""))
        notfound += int(m.group(3).replace(",", ""))
    if bad:
        return Result("audit", "attention", f"{bad} disposition(s) MISMATCHED")
    if notfound:
        return Result("audit", "attention",
                      f"{notfound} disposition(s) not found in the gains file "
                      f"(phantom-backed sales show here; see KNOWN_ISSUES)")
    if code != 0:
        return Result("audit", "attention", _last_line(err) or f"exit {code}")
    return Result("audit", "done", f"{events} disposition(s) tied")


def d_wash_reviewed(ctx: Ctx) -> Result:
    names = _accounts_of(ctx, "taxable") + _accounts_of(ctx, "crypto")
    denied = perm = 0.0
    seen = False
    for n in names:
        f = ctx.cache / f"{n}_gains_wash.json"
        if not f.is_file():
            f = ctx.cache / f"{n}_gains.json"
        if not f.is_file():
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        seen = True
        for t in doc.get("transactions") or []:
            if not str(t.get("date") or "").startswith(str(ctx.year)):
                continue
            denied += float(t.get("disallowed_amount") or 0)
            perm += float(t.get("permanently_disallowed") or 0)
    if not seen:
        return Result("wash-reviewed", "blocked", "no gains files — run `taxjson run`")
    if perm > 0.005:
        return Result("wash-reviewed", "manual",
                      f"{perm:,.2f} permanently denied (registered-account repurchase) "
                      f"— confirm each with `taxjson wash-sales`")
    if denied > 0.005:
        return Result("wash-reviewed", "done",
                      f"{denied:,.2f} denied, all recoverable (added to ACB)")
    return Result("wash-reviewed", "done", "no superficial losses")


def d_option_boundary(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("option-boundary")
    if not out:
        return Result("option-boundary", "blocked", _last_line(err) or f"exit {code}")
    if "T1-ADJ" in out and "require an amended return" in out:
        m = re.search(r"(\d+) item\(s\) require an amended return", out)
        return Result("option-boundary", "attention",
                      f"{m.group(1) if m else 'some'} contract(s) require a T1-ADJ")
    return Result("option-boundary", "done", "no amendment required")


def slip_files(root: Path) -> List[Path]:
    out: List[Path] = []
    slips = root / "inputs" / "slips"
    if slips.is_dir():
        out += sorted(p for p in slips.glob("*.csv") if p.is_file())
    for p in (root / "inputs").rglob("*.csv") if (root / "inputs").is_dir() else []:
        if re.search(r"t5008|1099", p.name, re.I) and p not in out:
            out.append(p)
    return out


def d_t5008(ctx: Ctx) -> Result:
    files = slip_files(ctx.root)
    if not files:
        return Result("t5008", "todo",
                      "no slip file — put the broker T5008 CSVs in inputs/slips/")
    bad = []
    for f in files:
        code, out, err = ctx.sub("reconcile-slips", str(f))
        if code != 0:
            bad.append(f"{f.name}: {_last_line(out) or _last_line(err)}")
    if bad:
        return Result("t5008", "attention", "; ".join(bad))
    return Result("t5008", "done", f"{len(files)} slip file(s) reconcile")


def d_form_export(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("form-export")
    if code != 0:
        return Result("form-export", "blocked", _last_line(err) or f"exit {code}")
    rows = sum(1 for ln in out.splitlines() if ln.strip())
    return Result("form-export", "done", f"export renders ({rows} line(s))")


def d_t1135(ctx: Ctx) -> Result:
    if str(ctx.settings.get("country", "canada")).lower() in ("us", "usa"):
        return Result("t1135", "n/a", "US project")
    code, out, err = ctx.sub("t1135", "--json")
    try:
        rep = json.loads(out)
    except ValueError:
        return Result("t1135", "blocked", _last_line(err) or f"exit {code}")
    if rep.get("filing_required"):
        return Result("t1135", "manual",
                      "cost of foreign property exceeded the threshold — file "
                      "the T1135 (`taxjson t1135` for the tables)")
    return Result("t1135", "done", "below the CAD 100,000 threshold")


def d_carryover(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("carryover", "--json")
    claimed = (ctx.root / "claimed_losses.txt").is_file()
    if code != 0 and not out:
        return Result("carryover", "blocked", _last_line(err) or f"exit {code}")
    return Result("carryover", "manual",
                  "claimed_losses.txt " + ("present" if claimed else "absent")
                  + " — record what line 25300 actually claims")


def d_fx_cash(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("fx-cash")
    if code != 0 and not out:
        return Result("fx-cash", "blocked", _last_line(err) or f"exit {code}")
    return Result("fx-cash", "manual", _last_line(out)[:100])


def d_fees(ctx: Ctx) -> Result:
    return Result("fees", "manual", "line 22100 is entered by hand")


def d_estimate(ctx: Ctx) -> Result:
    est = (ctx.cfg.get("estimate") or {}).get("other_income")
    inst = bool(ctx.cfg.get("instalments"))
    return Result("estimate", "manual",
                  f"[estimate] other_income {'set' if est is not None else 'unset'}; "
                  f"[instalments] {'present' if inst else 'absent'}")


def d_filed_lock(ctx: Ctx) -> Result:
    lock = ctx.root / "filed" / f"{ctx.year}.json"
    if not lock.is_file():
        return Result("filed-lock", "todo", "no filed/<year>.json — `taxjson close-year` after filing")
    code, out, err = ctx.sub("check-filed")
    if code != 0:
        return Result("filed-lock", "attention",
                      "check-filed reports drift against the lock — amend or "
                      "`close-year --force` after re-filing")
    return Result("filed-lock", "done", f"locked; no drift")


def d_lock_committed(ctx: Ctx) -> Result:
    lock = ctx.root / "filed" / f"{ctx.year}.json"
    if not lock.is_file():
        return Result("lock-committed", "todo", "no lock yet")
    if not _is_git_repo(ctx.root):
        return Result("lock-committed", "attention", "not a git repository")
    code, out = _git(ctx.root, "status", "--porcelain", "--", "filed")
    if out.strip():
        return Result("lock-committed", "todo", "filed/ has uncommitted changes")
    return Result("lock-committed", "done", "committed")


def d_noa(ctx: Ctx) -> Result:
    return Result("noa", "manual", "compare the NOA, then update next year's [instalments]")


DETECTORS: Dict[str, Callable[[Ctx], Result]] = {
    "inputs-frozen": d_inputs_frozen,
    "sheltered-inputs": d_sheltered_inputs,
    "crypto-inputs": d_crypto_inputs,
    "roc-entered": d_roc_entered,
    "inputs-committed": d_inputs_committed,
    "run-clean": d_run_clean,
    "sanity": d_sanity,
    "missing-history": d_missing_history,
    "elections": d_elections,
    "audit": d_audit,
    "wash-reviewed": d_wash_reviewed,
    "option-boundary": d_option_boundary,
    "t5008": d_t5008,
    "t5-t3": lambda ctx: Result("t5-t3", "manual", "compare the slips with `taxjson divs-sum` / `roc-sum`"),
    "foreign-tax": lambda ctx: Result("foreign-tax", "manual", "from the slips"),
    "form-export": d_form_export,
    "t1135": d_t1135,
    "carryover": d_carryover,
    "fx-cash": d_fx_cash,
    "fees": d_fees,
    "estimate": d_estimate,
    "filed-lock": d_filed_lock,
    "lock-committed": d_lock_committed,
    "noa": d_noa,
}

# Detectors that shell out to a slow sub-command; `--quick` skips them.
SLOW = {"sanity", "missing-history", "elections", "audit", "option-boundary",
        "t5008", "form-export", "t1135", "carryover", "fx-cash", "filed-lock"}


# -------------------------------------------------------------------- state
def load_state(root: Path) -> Dict[str, Any]:
    p = root / STATE_FILE
    if not p.is_file():
        return {"overrides": {}}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"overrides": {}}
    doc.setdefault("overrides", {})
    return doc


def save_state(root: Path, state: Dict[str, Any], year: int) -> None:
    state["year"] = year
    (root / STATE_FILE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                                   encoding="utf-8")


def set_override(root: Path, year: int, step: str, mark: Optional[str],
                 note: str = "", today: Optional[date] = None) -> None:
    ids = {s[0] for s in STEPS}
    if step not in ids:
        raise KeyError(step)
    state = load_state(root)
    if mark is None:
        state["overrides"].pop(step, None)
    else:
        state["overrides"][step] = {"status": mark,
                                    "date": (today or date.today()).isoformat(),
                                    "note": note}
    save_state(root, state, year)


# ----------------------------------------------------------------- evaluate
def evaluate(ctx: Ctx, only: Optional[List[str]] = None,
             quick: bool = False) -> List[Result]:
    state = load_state(ctx.root)
    overrides = state.get("overrides") or {}
    if state.get("year") not in (None, ctx.year) and overrides:
        # Marks from another year's project copied along — not this year's.
        overrides = {}
    results: List[Result] = []
    for sid, stage, title, cmd, why in STEPS:
        if only and sid not in only:
            continue
        ov = overrides.get(sid) or {}
        if quick and sid in SLOW and sid not in (only or []):
            r = Result(sid, "todo", "skipped by --quick (run without it to check)")
        else:
            try:
                r = DETECTORS[sid](ctx)
            except Exception as e:      # a detector must never take the list down
                r = Result(sid, "blocked", f"detector failed: {e}")
        if ov.get("status") in ("done", "skipped"):
            r.override = ov["status"]
            r.note = ov.get("note") or ""
            if r.status == "attention":
                r.finding = r.detail
        results.append(r)
    return results


def render(results: List[Result], year: int, country: str,
           quick: bool = False) -> str:
    by_stage: Dict[int, List[Result]] = {}
    for r in results:
        stage = next(s[1] for s in STEPS if s[0] == r.id)
        by_stage.setdefault(stage, []).append(r)
    total = len(results)
    done = sum(1 for r in results if r.passed)
    att = sum(1 for r in results if r.effective == "attention")
    man = sum(1 for r in results if r.effective == "manual")
    todo = sum(1 for r in results if r.effective in ("todo", "blocked"))
    lines = [f"FILING CHECKLIST — tax year {year} ({country})"
             f"{' — quick' if quick else ''}: {done}/{total} done, "
             f"{att} need attention, {man} need your confirmation, {todo} to do",
             ""]
    meta = {s[0]: s for s in STEPS}
    for num, name in STAGES:
        rows = by_stage.get(num)
        if not rows:
            continue
        lines.append(f"{num}. {name}")
        for r in rows:
            sym = SYMBOL[r.effective]
            if r.override == "done":
                sym = "[x]"
            title = meta[r.id][2]
            tail = r.detail
            if r.override:
                tail = f"marked {r.override}" + (f": {r.note}" if r.note else "")
                if r.finding:
                    tail += f"  !! detector: {r.finding}"
            lines.append(f"  {sym} {r.id:<17} {title}")
            if tail:
                lines.append(f"      {tail}")
        lines.append("")
    lines.append("[x] done  [!] needs attention  [ ] to do  [m] confirm with "
                 "`taxjson checklist --done ID`  [b] blocked  [-] n/a  [~] skipped")
    lines.append("Walk the open steps one at a time: `taxjson checklist --walk`")
    return "\n".join(lines)


def to_json(results: List[Result], year: int, country: str) -> Dict[str, Any]:
    meta = {s[0]: s for s in STEPS}
    return {
        "year": year, "country": country,
        "all_passed": all(r.passed for r in results),
        "steps": [{"id": r.id, "stage": meta[r.id][1], "title": meta[r.id][2],
                   "command": meta[r.id][3], "status": r.status,
                   "effective": r.effective, "detail": r.detail,
                   "override": r.override, "note": r.note,
                   "finding": r.finding} for r in results],
    }
