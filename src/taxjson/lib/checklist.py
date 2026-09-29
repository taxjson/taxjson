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
`--reset`. A mark never hides a detector's finding: a step marked DONE
whose detector later says `attention` counts as attention (shown `[!]`
with the mark and its note beside the finding), so a stale mark cannot
turn the list green. `--skip` is the deliberate "reviewed, accepted"
mark — the finding stays visible beside it.

Country: the steps are written for a Canadian return. A US project gets
the US names where an equivalent exists (1099-B for the T5008, Form
8949 / Schedule D for Schedule 3, ...) and `n/a` for the Canada-only
steps; a project with no taxable account gets `n/a` for every step that
only concerns taxable accounts.
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
    ("handoff", 2, "Last year's closing positions carried in exactly once",
     "taxjson handoff",
     "A Dec 31 trade settling in January, a dropped lot, or a correction applied to one year only makes a gain vanish or count twice."),
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

# US projects: (title, command, why) replacements for steps with a US
# equivalent, or a plain string = the reason the step is n/a.
US_STEPS: Dict[str, Any] = {
    "sheltered-inputs": ("Retirement accounts' activity present", "inputs/<sheltered>/",
                         "An IRA/401(k) purchase within 30 days of a loss is a wash sale "
                         "(Rev. Rul. 2008-5) — the loss is gone for good."),
    "roc-entered": ("Nondividend distributions (1099-DIV box 3) entered before trusting any basis",
                    "ADJUST lines / distributions.map",
                    "Return of capital reduces basis; funds often reclassify after year end."),
    "wash-reviewed": ("Every wash-sale disallowance reviewed", "taxjson wash-sales",
                      "A wash sale triggered by an IRA purchase is permanently disallowed."),
    "option-boundary": "US: written-option premiums are netted at the close (§1234) — no s.49 boundary",
    "t5008": ("1099-B slips reconcile to the computed dispositions",
              "taxjson reconcile-slips inputs/slips/*.csv",
              "The IRS matches Form 8949 / Schedule D to the 1099-Bs — this step prevents a CP2000."),
    "t5-t3": ("1099-DIV / 1099-INT slips agree with the dividend and ROC totals",
              "taxjson divs-sum, taxjson roc-sum",
              "Qualified vs ordinary dividends and nondividend distributions come from the slips."),
    "foreign-tax": ("Foreign tax paid taken from the 1099-DIV (box 7) for the credit (Form 1116)",
                    "1099-DIV box 7",
                    "The credit is limited to what the slips show."),
    "form-export": ("Form 8949 rows exported and their totals equal the report",
                    "taxjson form-export",
                    "The export is what goes on Form 8949 / Schedule D; the .sum is what the engine computed — they must agree."),
    "t1135": "US project (T1135 is a Canadian form)",
    "carryover": ("Capital loss carryover applied and recorded (Schedule D lines 6 / 14)",
                  "taxjson carryover, claimed_losses.txt",
                  "The ledger only knows what was claimed if you write it down."),
    "fx-cash": ("FX gain on foreign cash reviewed (§988)", "taxjson fx-cash",
                "Foreign currency gains on personal cash above $200 per transaction are income."),
    "fees": ("Margin interest collected (Form 4952, if itemizing)", "taxjson fees",
             "Investment interest is deductible only when itemizing; the tool reports it."),
    "estimate": ("Tax estimate and estimated payments checked", "taxjson estimate",
                 "A sanity check on the tax owed."),
    "filed-lock": ("Return filed and the year locked", "taxjson close-year",
                   "The lock is what check-filed uses to detect drift after filing."),
    "noa": "US project (no Notice of Assessment)",
}

# Steps that only concern TAXABLE accounts: n/a for a sheltered-only
# project instead of blocked by artifacts that can never exist.
TAXABLE_ONLY = {"inputs-frozen", "roc-entered", "missing-history", "audit",
                "wash-reviewed", "option-boundary", "handoff", "t5008", "t5-t3",
                "foreign-tax", "form-export", "t1135", "carryover",
                "fx-cash", "fees", "filed-lock", "lock-committed"}


def is_us(country: str) -> bool:
    return str(country or "").strip().lower() in ("us", "usa")


def step_meta(sid: str, country: str = "canada") -> Tuple[str, int, str, str, str]:
    """(id, stage, title, command, why) for this project's country."""
    base = next(s for s in STEPS if s[0] == sid)
    if is_us(country) and isinstance(US_STEPS.get(sid), tuple):
        title, cmd, why = US_STEPS[sid]
        return (sid, base[1], title, cmd, why)
    return base


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
        # A DONE mark never outranks a detector finding (README: "a mark
        # never hides a later finding"); --skip is the explicit accept.
        if self.override == "done" and self.status == "attention":
            return "attention"
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
    """git in the user's project, hardened against a hostile .git/config:
    core.fsmonitor and hooks can execute arbitrary commands, and a status
    call must not take locks (2026-09 security audit)."""
    try:
        p = subprocess.run(["git", "-c", "core.fsmonitor=false",
                            "-c", "core.hooksPath=/dev/null",
                            "-C", str(root)] + list(args),
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
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
    if code != 0:
        # Exit 1 is "no base files" / "no transactions loaded": nothing
        # was checked, so it is never "nothing affects the year".
        return Result("missing-history", "blocked", _last_line(err) or _last_line(out) or f"exit {code}")
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
    if not _accounts_of(ctx, "taxable"):
        return Result("option-boundary", "n/a",
                      "no non-crypto taxable account — no written options")
    code, out, err = ctx.sub("option-boundary", "--json")
    try:
        doc = json.loads(out) if code == 0 else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        # Exit 1 = no taxable book was checked ("NOT CHECKED").
        return Result("option-boundary", "blocked", _last_line(err) or f"exit {code}")
    rows = doc.get("rows") or []
    amend = sum(1 for r in rows if str(r.get("action", "")).startswith("T1-ADJ"))
    att = sum(1 for r in rows if r.get("attention"))
    if amend or att:
        parts = []
        if amend:
            parts.append(f"{amend} contract(s) require a T1-ADJ")
        if att:
            parts.append(f"{att} need attention (a locked year on transition close "
                         f"timing, or an expired contract with no expiry row)")
        return Result("option-boundary", "attention",
                      "; ".join(parts) + " — `taxjson option-boundary`")
    if doc.get("missing_books"):
        return Result("option-boundary", "blocked",
                      f"no books for {', '.join(doc['missing_books'])} — run `taxjson run`")
    if not doc.get("since_explicit", True) and str(doc.get("timing")) == "grant":
        return Result("option-boundary", "attention",
                      "option_grant_timing_since is not set in [settings] — the default "
                      "follows `year`; set it once and keep it")
    return Result("option-boundary", "done", "no amendment required")


def d_handoff(ctx: Ctx) -> Result:
    y = ctx.year
    configured = ctx.settings.get("prior_year_record")
    local = ctx.root / "filed" / f"{y - 1}.json"
    if not configured and not local.exists():
        return Result("handoff", "manual",
                      f"no {y - 1} record: run `taxjson close-year` in the "
                      f"{y - 1} project and set [settings] prior_year_record "
                      f"to it (mark done if {y} is the first year)")
    code, out, err = ctx.sub("handoff", "--json")
    try:
        doc = json.loads(out) if out.strip() else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        return Result("handoff", "blocked", _last_line(err) or f"exit {code}")
    if doc.get("problems"):
        parts = []
        for k, label in (("positions", "opening position(s) differ"),
                         ("missed", "trade(s) settling in January missing"),
                         ("double", "sale(s) reported in both years")):
            if doc.get(k):
                parts.append(f"{len(doc[k])} {label}")
        return Result("handoff", "attention",
                      "; ".join(parts) + " — `taxjson handoff`")
    return Result("handoff", "done", f"{y - 1} carried forward once")


def slip_files(root: Path) -> List[Path]:
    out: List[Path] = []
    slips = root / "inputs" / "slips"
    if slips.is_dir():
        out += sorted(p for p in slips.glob("*.csv") if p.is_file())
    for p in (root / "inputs").rglob("*.csv") if (root / "inputs").is_dir() else []:
        if re.search(r"t5008|1099", p.name, re.I) and p not in out:
            out.append(p)
    return out


def _slip_mismatch_summary(code: int, out: str, err: str) -> str:
    """The actual mismatch summary of a `reconcile-slips --json` run (the
    last text line was a generic note, not the finding)."""
    try:
        rep = json.loads(out)
    except ValueError:
        rep = None
    if isinstance(rep, dict):
        c = rep.get("counts") or {}
        parts = [f"{c.get('mismatch', 0)} mismatch",
                 f"{c.get('missing_from_computed', 0)} missing from computed",
                 f"{c.get('missing_from_slip', 0)} missing from slip"]
        if rep.get("unreadable_rows"):
            parts.append(f"{rep['unreadable_rows']} unreadable slip row(s)")
        bad = [f"{r.get('symbol')} {r.get('status')}"
               for r in (rep.get("rows") or []) if r.get("status") != "OK"]
        if bad:
            parts.append("e.g. " + ", ".join(bad[:3]) + (" ..." if len(bad) > 3 else ""))
        return ", ".join(parts)
    for ln in reversed(out.splitlines()):
        if re.search(r"\d+ OK, \d+ mismatch", ln) or ln.startswith("NOT RECONCILED"):
            return ln.strip()
    return _last_line(err) or _last_line(out) or f"exit {code}"


def d_t5008(ctx: Ctx) -> Result:
    slip = "1099-B" if is_us(ctx.settings.get("country", "canada")) else "T5008"
    files = slip_files(ctx.root)
    if not files:
        return Result("t5008", "todo",
                      f"no slip file — put the broker {slip} CSVs in inputs/slips/")
    bad = []
    for f in files:
        code, out, err = ctx.sub("reconcile-slips", str(f), "--json")
        if code != 0:
            bad.append(f"{f.name}: {_slip_mismatch_summary(code, out, err)}")
    if bad:
        return Result("t5008", "attention", "; ".join(bad))
    return Result("t5008", "done", f"{len(files)} slip file(s) reconcile")


def d_form_export(ctx: Ctx) -> Result:
    """The export must equal what the engine computed: form-export's totals
    against `taxjson sum`'s FOR THE RETURN block (same dispositions), and
    that block's gain against the taxable accounts' realized gain."""
    code, out, err = ctx.sub("form-export", "--json")
    if code != 0:
        return Result("form-export", "blocked", _last_line(err) or f"exit {code}")
    scode, sout, serr = ctx.sub("sum", "--json")
    try:
        rep = json.loads(out)
        summ = json.loads(sout)
    except ValueError:
        return Result("form-export", "blocked",
                      _last_line(serr) or _last_line(err) or "could not read the reports")
    if rep.get("form") == "8949":
        t = {k: round(sum((rep.get(p) or {}).get(k, 0.0)
                          for p in ("part_I_totals", "part_II_totals")), 2)
             for k in ("proceeds", "gain")}
        n = len(rep.get("part_I") or []) + len(rep.get("part_II") or [])
        label = "Form 8949"
    else:
        tot = rep.get("totals") or {}
        t = {"proceeds": round(float(tot.get("proceeds_all", 0.0)), 2),
             "gain": round(float(tot.get("gain_all", 0.0)), 2)}
        n = len(rep.get("rows") or [])
        label = "Schedule 3"
    filing = (summ.get("filing") or {}).get("totals") or {}
    problems = []
    for k in ("proceeds", "gain"):
        want = round(float(filing.get(k, 0.0)), 2)
        if abs(t[k] - want) > 0.01:
            problems.append(f"{label} {k} {t[k]:,.2f} vs FOR THE RETURN {want:,.2f}")
    taxable = {n_ for n_, a in ctx.accounts.items() if a.get("type") == "taxable"}
    realized = round(sum(float(r.get("realized") or 0.0)
                         for r in summ.get("accounts") or []
                         if r.get("account") in taxable), 2)
    if abs(t["gain"] - realized) > 0.05:
        problems.append(f"{label} gain {t['gain']:,.2f} vs realized {realized:,.2f} "
                        f"in the taxable accounts' .sum")
    if problems:
        return Result("form-export", "attention", "; ".join(problems))
    return Result("form-export", "done",
                  f"{n} row(s); gain {t['gain']:,.2f} equals FOR THE RETURN and the .sum")


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
    "handoff": d_handoff,
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
        "handoff",
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
                 note: str = "", today: Optional[date] = None) -> bool:
    """Record (or with mark=None remove) a manual mark. Returns False when
    removing a mark that was not there (nothing changed)."""
    ids = {s[0] for s in STEPS}
    if step not in ids:
        raise KeyError(step)
    state = load_state(root)
    if mark is None:
        if step not in state["overrides"]:
            return False
        state["overrides"].pop(step, None)
    else:
        state["overrides"][step] = {"status": mark,
                                    "date": (today or date.today()).isoformat(),
                                    "note": note}
    save_state(root, state, year)
    return True


# ----------------------------------------------------------------- evaluate
def evaluate(ctx: Ctx, only: Optional[List[str]] = None,
             quick: bool = False,
             progress: Optional[Callable[[str, str], None]] = None) -> List[Result]:
    """Run the detectors (all, or `only` these ids). `progress(id, command)`
    is called before each slow detector so a caller can say what it is
    waiting on — the audit alone can take a minute on a big book."""
    state = load_state(ctx.root)
    overrides = state.get("overrides") or {}
    if state.get("year") not in (None, ctx.year) and overrides:
        # Marks from another year's project copied along — not this year's.
        overrides = {}
    us = is_us(ctx.settings.get("country", "canada"))
    has_taxable = any(a.get("type") == "taxable" for a in ctx.accounts.values())
    results: List[Result] = []
    for sid, stage, title, cmd, why in STEPS:
        if only and sid not in only:
            continue
        ov = overrides.get(sid) or {}
        if us and isinstance(US_STEPS.get(sid), str):
            results.append(Result(sid, "n/a", US_STEPS[sid]))
            continue
        if not has_taxable and sid in TAXABLE_ONLY:
            results.append(Result(sid, "n/a", "no taxable account — nothing to report"))
            continue
        if quick and sid in SLOW:
            r = Result(sid, "todo", "skipped by --quick (run without it to check)")
        else:
            if progress and sid in SLOW:
                progress(sid, step_meta(sid, ctx.settings.get("country", "canada"))[3])
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


def stderr_progress(sid: str, cmd: str) -> None:
    """Default progress line: what is being checked, on stderr so that
    `--json` stdout stays machine-readable."""
    print(f"  checking {sid} ({cmd}) ...", file=sys.stderr, flush=True)


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
    for num, name in STAGES:
        rows = by_stage.get(num)
        if not rows:
            continue
        lines.append(f"{num}. {name}")
        for r in rows:
            sym = SYMBOL[r.effective]
            title = step_meta(r.id, country)[2]
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
    meta = {s[0]: step_meta(s[0], country) for s in STEPS}
    return {
        "year": year, "country": country,
        "all_passed": all(r.passed for r in results),
        "steps": [{"id": r.id, "stage": meta[r.id][1], "title": meta[r.id][2],
                   "command": meta[r.id][3], "status": r.status,
                   "effective": r.effective, "detail": r.detail,
                   "override": r.override, "note": r.note,
                   "finding": r.finding} for r in results],
    }
