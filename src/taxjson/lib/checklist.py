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
# taxjson_run.UNBOOKED_PREFIX: a parser row that is a tax event the
# run could not book.
UNBOOKED_PREFIX = "warning: UNBOOKED:"

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
    ("export-coverage", 1, "Each broker's exports reach the year end wherever it still holds positions",
     "taxjson run (its export-coverage warnings)",
     "A broker whose exports stop while it holds positions leaves its later sales, option "
     "expiries and income out of the books — nothing else says so."),
    ("sheltered-inputs", 1, "Sheltered accounts' activity present",
     "inputs/<sheltered>/",
     "RRSP/LIRA/TFSA/RESP purchases decide the superficial-loss rule for the taxable accounts."),
    ("crypto-inputs", 1, "Crypto ledgers and trades for the full year",
     "inputs/<crypto>/",
     "Every disposition of a coin, including swaps and fees, is a capital event."),
    ("roc-entered", 1, "Return of capital (T3 box 42) entered before trusting any ACB",
     "ADJUST lines / [[distributions]] in taxjson.toml",
     "Some funds publish ROC factors only after year end; without them the ACB is overstated."),
    ("inputs-committed", 1, "Inputs, config, maps and manifests committed",
     "git status",
     "The filed books must be rebuildable years later."),
    ("run-clean", 2, "Full run with zero validation errors and nothing pending",
     "taxjson run",
     "A validation error means a row the engine could not book; a pending election means an account was skipped."),
    ("check-dates", 2, "Every trade and settlement date is possible for its market",
     "taxjson check-dates",
     "A date on a closed day, or a settlement before the trade, moves a sale to the wrong day's rate or the wrong year."),
    ("sanity", 2, "Positions tie to the broker holdings",
     "taxjson sanity",
     "The only acceptable differences are trades after the last export."),
    ("missing-history", 2, "No position with missing cost basis affects the year",
     "taxjson find-missing-history",
     "Missing basis distorts the year either way: a sale with no earlier buy in the data "
     "(truncated history) is booked as a short and left out of the year, understating the "
     "proceeds and gain; shares acquired at $0 cost (an undeclared corporate action) "
     "overstate the gain by the missing basis."),
    ("renames", 2, "Every ticker change dated; no undeclared trade in an old ticker after its rename",
     "taxjson renames",
     "A rename carries the position and cost on its date; a later trade in the old ticker is "
     "another security unless ticker.map folds it (`late=fold` / `late=separate`)."),
    ("journals", 2, "Every journal between two listings joined or settled",
     "taxjson journals --pending",
     "A broker journal moves a position from one listing of a security to another; one the books do "
     "not pool leaves a long on one listing and a short on the other, and a sale's cost wrong."),
    ("elections", 2, "No unresolved merger or spin-off election",
     "taxjson elect --pending",
     "A deferred election leaves the account out of the run."),
    ("crypto-sends", 2, "Crypto sends classified (own wallet, gift or payment)",
     "taxjson crypto-sends",
     "A crypto send that left your ownership is a disposition at fair value; only you know which sends did."),
    ("audit", 2, "Every disposition traced and tied",
     "taxjson audit",
     "The audit walks each sale from the broker row to the reported gain."),
    ("wash-reviewed", 2, "Every superficial-loss denial reviewed",
     "taxjson wash-sales",
     "A permanently denied loss (registered-account or affiliated-person repurchase) is gone from "
     "your return; make sure each is real (an affiliated person adds it to their own ACB)."),
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
     "taxjson divs-sum, taxjson roc-sum (the TAXABLE lines; compare by hand)",
     "Trust units report on a T3, often weeks after the T5s; split-share and mutual-fund "
     "corporations report on a T5, where box 18 capital-gains dividends go on line 17400 "
     "(taxjson books them as dividends). reconcile-slips reads only T5008 disposition "
     "slips, so this check is by hand; other known differences: payments in lieu "
     "(divs-sum's PIL column — T5 box 24 may include them), trust distributions an IB "
     "row dates by pay date (the T3 uses the record year), and T3 boxes the books carry "
     "as dividends (capital gains box 21, return of capital box 42)."),
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
     "taxjson carryover, [carryover] claimed in taxjson.toml",
     "Line 25300; the ledger only knows what was claimed if you write it down — "
     "record the 100% loss applied (line 25300 divided by the inclusion rate)."),
    ("fx-cash", 4, "FX gain on foreign cash reviewed (ITA s.39(1.1), $200 de minimis)",
     "taxjson fx-cash",
     "Foreign currency is property; the net gain above $200 is a capital gain."),
    ("fees", 4, "Carrying charges (margin interest) for line 22100 taken from the statements",
     "broker statements (`taxjson events` lists the INTEREST rows)",
     "Interest on money borrowed to invest is deductible on line 22100; trade "
     "commissions are not (they are already in the ACB and proceeds). The "
     "account .sum's CASH INTEREST line nets credit against debit interest, "
     "so it is not the interest paid."),
    ("estimate", 4, "Tax estimate and instalment position checked",
     "taxjson estimate, taxjson instalments",
     "A sanity check on the tax owed and on what was already paid."),
    ("amt", 4, "Minimum tax (AMT) and its carryover checked",
     "taxjson amt, [estimate] amt_carryover",
     "Minimum tax paid in the 7 preceding years is recovered against regular tax above "
     "the minimum (ITA s.120.2, line 40427), and a year where AMT binds starts a new "
     "carryover; the estimate applies one only when [estimate] amt_carryover (your notice of "
     "assessment / T691) or last year's close-year lock carries it."),
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
                    "ADJUST lines / [[distributions]] in taxjson.toml",
                    "Return of capital reduces basis; funds often reclassify after year end."),
    "crypto-sends": ("Crypto sends classified (own wallet, gift or payment)",
                     "taxjson crypto-sends",
                     "Paying with crypto is a sale at fair value; a gift is not a sale for the donor."),
    "wash-reviewed": ("Every wash-sale disallowance reviewed", "taxjson wash-sales",
                      "A wash sale triggered by an IRA purchase is permanently disallowed."),
    # No s.49 year boundary in a US project (§1234 nets at the close),
    # but a contract past its expiry with no close row keeps its premium
    # or cost out of the return — the check stays (S066-15).
    "option-boundary": ("No option left open past its expiry",
                        "taxjson list (option positions)",
                        "Under §1234 an expired written option's premium is a "
                        "short-term gain, and an expired long option's cost a "
                        "loss, in the expiry year; a missing expiry, exercise "
                        "or assignment row leaves either out of the return."),
    "t5008": ("1099-B (and, for crypto from 2025, 1099-DA) slips reconcile "
              "to the computed dispositions",
              "taxjson reconcile-slips inputs/slips/*.csv",
              "The IRS matches Form 8949 / Schedule D to the 1099-Bs and "
              "1099-DAs — this step prevents a CP2000."),
    "t5-t3": ("1099-DIV / 1099-INT slips agree with the dividend and ROC totals",
              "taxjson divs-sum, taxjson roc-sum (the TAXABLE lines)",
              "Qualified vs ordinary dividends and nondividend distributions come from the slips."),
    "foreign-tax": ("Foreign tax paid taken from the 1099-DIV (box 7) for the credit (Form 1116)",
                    "1099-DIV box 7",
                    "The credit is limited to what the slips show."),
    "form-export": ("Form 8949 rows exported and their totals equal the report",
                    "taxjson form-export",
                    "The export is what goes on Form 8949 / Schedule D; the .sum is what the engine computed — they must agree."),
    "t1135": "US project (T1135 is a Canadian form)",
    "carryover": ("Capital loss carryover applied and recorded (Schedule D line 21 deduction)",
                  "taxjson carryover, [carryover] claimed in taxjson.toml",
                  "The ledger only knows what was claimed if you write it down — "
                  "record each year's Schedule D line 21 deduction, not the "
                  "line 6 / 14 carryover."),
    "fx-cash": ("FX gain on foreign cash reviewed (§988)", "taxjson fx-cash",
                "§988 currency gains on investment cash are ordinary income; "
                "`taxjson fx-cash` estimates the year's net (the §988(e) "
                "personal-transaction exclusion is not modelled)."),
    "fees": ("Margin interest collected (Form 4952, if itemizing)",
             "broker statements (`taxjson events` lists the INTEREST rows)",
             "Investment interest is deductible only when itemizing; the account "
             ".sum's CASH INTEREST line nets credit against debit interest, so it "
             "is not the interest paid, and trade commissions are not investment "
             "interest."),
    "estimate": ("Tax estimate and estimated payments checked", "taxjson estimate",
                 "A sanity check on the tax owed."),
    "amt": "US project (the US alternative minimum tax, Form 6251, is not modelled)",
    "filed-lock": ("Return filed and the year locked", "taxjson close-year",
                   "The lock is what check-filed uses to detect drift after filing."),
    "noa": "US project (no Notice of Assessment)",
}

# Steps that only concern TAXABLE accounts: n/a for a sheltered-only
# project instead of blocked by artifacts that can never exist.
TAXABLE_ONLY = {"inputs-frozen", "roc-entered", "missing-history", "audit",
                "crypto-sends",
                "wash-reviewed", "option-boundary", "handoff", "t5008", "t5-t3",
                "foreign-tax", "form-export", "t1135", "carryover",
                "fx-cash", "fees", "amt", "filed-lock", "lock-committed"}


def is_us(country: str) -> bool:
    """Strict (lib/country): an unknown or missing country raises."""
    from taxjson.lib.country import is_usa
    return is_usa(country)


def step_meta(sid: str, country: str) -> Tuple[str, int, str, str, str]:
    """(id, stage, title, command, why) for this project's country."""
    base = next(s for s in STEPS if s[0] == sid)
    if is_us(country) and isinstance(US_STEPS.get(sid), tuple):
        title, cmd, why = US_STEPS[sid]
        return (sid, base[1], title, cmd, why)
    if is_us(country) and isinstance(US_STEPS.get(sid), str):
        # An n/a step of a US project: never the Canadian title and
        # reason (T1135's ITA 233.3, the NOA's CRA interest; A2-1261).
        return (sid, base[1], "Not applicable to a US project", "-",
                US_STEPS[sid])
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
    # The attention is a QUESTION only the user can answer (the files
    # cannot tell): a DONE mark is the answer and settles it — the
    # finding stays shown beside the mark (option-boundary's transition
    # question CA-OPT-11, export-coverage).
    question: bool = False

    @property
    def effective(self) -> str:
        # A DONE mark never outranks a detector finding (README: "a mark
        # never hides a later finding"); --skip is the explicit accept.
        # A question's DONE mark is its answer.
        if self.override == "done" and self.status == "attention" \
                and not self.question:
            return "attention"
        # ...nor a detector that could not check anything (crashed, or
        # its prerequisite is gone): a stale mark showed [x] over a
        # crash and all_passed went true (S068-13).
        if self.override == "done" and self.status == "blocked":
            return "blocked"
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
                               # Read by the detectors below, never
                               # shown as is: unwrapped (lib/out).
                               env={**os.environ, "NO_COLOR": "1",
                                    "TAXJSON_WIDTH": "0"})
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
        return p.returncode, p.stdout or "", p.stderr or ""
    return run


def _data_files(folder: Path) -> List[Path]:
    """The activity files `taxjson run` actually reads: .csv and .tt
    directly in the folder. Counting .xlsx/.txt too marked an account
    whose only file was an unread spreadsheet as present (R1-248)."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and not _skipped_input_name(p.name)
                  and p.suffix.lower() in (".csv", ".tt"))


# Spreadsheet suffixes a broker export may arrive in; none is read by
# `run` (taxjson_run.SPREADSHEET_SUFFIXES is the same list, A2-1156).
SPREADSHEET_SUFFIXES = (".xlsx", ".xls", ".xlsm", ".ods", ".numbers")


def _base_docs_checked(ctx: Ctx, names: List[str]
                       ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """(readable base books by account, the work/ files that exist but
    cannot be read). A detector must not compute a count or a date from
    the readable SUBSET and call it the project's (S066-19, S067-04:
    a truncated margin_base.json made roc-entered count 0 ADJUST rows
    and inputs-frozen date the books by the other accounts)."""
    out: Dict[str, Dict[str, Any]] = {}
    bad: List[str] = []
    for n in names:
        f = ctx.cache / f"{n}_base.json"
        if not f.is_file():
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bad.append(f"work/{f.name}")
            continue
        if not isinstance(doc, dict):
            bad.append(f"work/{f.name}")
            continue
        out[n] = doc
    return out, bad


def orphan_work_accounts(root: Path, known) -> List[str]:
    """Account names with a work/<name>_base.json that taxjson.toml no
    longer configures (a renamed or removed account). Their artifacts
    keep matching the discovery globs (resolve_gains_files, fees
    --cache, the audit's --source scan), so every aggregate counts them
    a second time (2026-09 audit; A2-1165: `run --strict` and run-clean
    now stop on them)."""
    known = set(known)
    names = set()
    try:
        bases = list((root / "work").glob("*_base.json"))
    except OSError:
        return []
    for p in bases:
        if p.name.startswith(".") or p.name == "sheltered_base.json":
            continue
        nm = p.name[:-len("_base.json")]
        if nm.endswith("_raw"):
            # `S_raw_base.json` is usually account S's raw-books
            # artifact — but only skip it when that S actually exists,
            # or a REAL account named `ib_raw` would never be flagged.
            parent = nm[:-len("_raw")]
            if parent in known or (root / "work" / f"{parent}_base.json").exists():
                continue
        names.add(nm)
    return sorted(names - known)


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


def _rbc_as_of_by_account(paths: List[Path]) -> List[Tuple[str, str]]:
    """[(' of account 12***' or '', latest as-of ISO)] for the RBC
    exports among `paths`: the latest "Activity Export as of" date per
    RBC account (rows' Account column; a file without one counts for
    every account of the folder)."""
    from taxjson.lib.brokerages.rbc_direct import (
        _mask_account, _norm_account, rbc_export_as_of, read_rbc_rows)
    per: Dict[str, str] = {}
    shared: List[str] = []                 # as-of of files with no Account
    for p in paths:
        asof = rbc_export_as_of(p)
        if not asof:
            continue
        try:
            exp = read_rbc_rows(p)
            accts = {_norm_account(r.account) for r in exp.rows
                     if r.account.strip()}
        except Exception:                               # noqa: BLE001
            accts = set()
        if not accts:
            shared.append(asof)
        for a in accts:
            per[a] = max(per.get(a, ""), asof)
    if not per:
        return [("", max(shared))] if shared else []
    many = len(per) > 1
    return [((f" of account {_mask_account(a)}" if many else ""),
             max([v] + shared)) for a, v in sorted(per.items())]


# ---------------------------------------------------------------- detectors
def d_inputs_frozen(ctx: Ctx) -> Result:
    missing = [n for n in ctx.accounts
               if not _data_files(ctx.root / "inputs" / n)
               and (ctx.accounts[n].get("type") == "taxable")]
    if missing:
        return Result("inputs-frozen", "todo",
                      f"no activity files in inputs/{', inputs/'.join(missing)}")
    docs, bad = _base_docs_checked(
        ctx, _accounts_of(ctx, "taxable") + _accounts_of(ctx, "crypto"))
    if bad:
        return Result("inputs-frozen", "blocked",
                      f"cannot read {', '.join(bad)} — its latest activity "
                      f"is unknown; re-run `taxjson run`")
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
    # Per statement, not across every source (audit S063-22): an RBC
    # export carries the date it was taken ("Activity Export as of
    # ..."), and an account whose latest export predates the cutoff
    # cannot hold the rest of the year — another broker's later rows
    # used to certify it.
    early = []
    for n in _accounts_of(ctx, "taxable"):
        # Per RBC ACCOUNT (the Account column), as the parser judges it:
        # one label may hold two RBC accounts' exports, and B's later
        # export certified A's early one (A2-1147).
        for who, asof in _rbc_as_of_by_account(
                [p for p in _data_files(ctx.root / "inputs" / n)
                 if p.suffix.lower() == ".csv"]):
            if asof < cutoff.isoformat():
                early.append(f"{n} (RBC export{who} as of {asof})")
    # IB statements carry their Period: an account whose statements stop
    # before Dec 31 of the year — or hold none of it — cannot hold the
    # rest of it (audit A2-0262, the RBC twin above).
    from taxjson.lib.brokerages.ib_extractor import ib_year_coverage
    from taxjson.lib.brokerages.base import decode_broker_text
    ib_short = []
    for n in _accounts_of(ctx, "taxable"):
        _ib = []
        for p in _data_files(ctx.root / "inputs" / n):
            if p.suffix.lower() != ".csv":
                continue
            try:
                head = decode_broker_text(p.read_bytes(), p.name)[:64]
            except (OSError, UnicodeError, ValueError):
                continue
            if head.lstrip().startswith("Statement,Header"):
                _ib.append(p)
        for acct, end in ib_year_coverage(_ib, ctx.year):
            who = f" {acct}" if acct else ""
            ib_short.append(
                f"{n} (IB statements{who} end {end.isoformat()})" if end
                else f"{n} (no IB statement{who} covers {ctx.year})")
    if ib_short:
        if ctx.today <= date(ctx.year, 12, 31):
            return Result("inputs-frozen", "todo",
                          f"year still open — {', '.join(ib_short)}; "
                          f"download the rest of {ctx.year} after it ends")
        return Result("inputs-frozen", "attention",
                      f"{', '.join(ib_short)} — {ctx.year} activity after "
                      f"it is not in the books; download the statement "
                      f"that covers the rest of the year")
    if early:
        if ctx.today <= cutoff:
            return Result("inputs-frozen", "todo",
                          f"year still open — {', '.join(early)}; "
                          f"re-export after {cutoff.isoformat()}")
        return Result("inputs-frozen", "attention",
                      f"{', '.join(early)} was taken before "
                      f"{cutoff.isoformat()} — activity after it is not in "
                      f"the books; re-export the account")
    if latest_d and latest_d >= cutoff:
        return Result("inputs-frozen", "done", f"latest activity {latest[:10]}")
    if ctx.today <= cutoff:
        return Result("inputs-frozen", "todo",
                      f"year still open — latest activity {latest[:10] or '?'}; "
                      f"re-export after {cutoff.isoformat()}")
    return Result("inputs-frozen", "attention",
                  f"latest activity {latest[:10] or '?'} — January {ctx.year + 1} "
                  f"is not in the books yet")


def d_export_coverage(ctx: Ctx) -> Result:
    """Each account's exports from each broker reach the tax year's end
    (today in the running year) wherever that broker still holds
    positions in the account (lib/export_coverage). A question when the
    end is read from the last row only: a DONE mark answers it."""
    from taxjson.lib import export_coverage as EC
    names = [n for n, a in ctx.accounts.items()
             if isinstance(a, dict) and not a.get("crypto")]
    if not names:
        return Result("export-coverage", "n/a", "no broker account")
    if not any((ctx.cache / f"{n}_base.json").is_file() for n in names):
        return Result("export-coverage", "blocked",
                      "no work/*_base.json — run `taxjson run`")
    gaps = EC.find_gaps(ctx.root, ctx.cfg, today=ctx.today)
    if gaps:
        # Only an end read from the last row is a question (the broker
        # may simply have been quiet); a statement or as-of date is the
        # export's own end: `--skip` accepts it.
        return Result("export-coverage", "attention", EC.detail(gaps),
                      question=all(g.how == "last-row" for g in gaps))
    return Result("export-coverage", "done",
                  "every broker with open positions has exports to "
                  + ("today" if ctx.today <= date(ctx.year, 12, 31)
                     else f"{ctx.year}-12-31"))


def d_sheltered_inputs(ctx: Ctx) -> Result:
    names = _accounts_of(ctx, "sheltered")
    if not names:
        return Result("sheltered-inputs", "n/a", "no sheltered account configured")
    empty = [n for n in names if not _data_files(ctx.root / "inputs" / n)]
    if empty:
        # US: an IRA's repurchase (Rev. Rul. 2008-5), never Canada's
        # affiliated persons (A2-1260).
        unseen = ("IRA/retirement-account purchases unseen"
                  if is_us(ctx.settings.get("country"))
                  else "affiliated purchases unseen")
        return Result("sheltered-inputs", "attention",
                      f"empty: {', '.join(empty)} — {unseen}")
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
    docs, bad = _base_docs_checked(ctx, _accounts_of(ctx, "taxable"))
    if bad:
        return Result("roc-entered", "blocked",
                      f"cannot read {', '.join(bad)} — its ADJUST rows "
                      f"cannot be counted; re-run `taxjson run`")
    # The year an ADJUST lowers the cost in: a Canadian trust's ROC on
    # its record date (CA-INC-DATE-ROC-TRUST) — roc-sum's window, not the
    # pay date (A2-0680).
    try:
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules.from_settings(ctx.settings)
        when = rules.row_date
    except Exception:                                   # noqa: BLE001
        def when(t):
            return str(t.get("date") or "")
    adjust = sum(1 for d in docs.values()
                 for t in (d.get("transactions") or [])
                 if isinstance(t, dict) and t.get("action") == "ADJUST"
                 and str(when(t) or "").startswith(str(ctx.year)))
    dmap = bool((getattr(ctx, "cfg", None) or {}).get("distributions"))
    detail = (f"{adjust} ADJUST row(s) in {ctx.year}; [[distributions]] "
              f"{'present' if dmap else 'absent'}")
    if dmap:
        # The same ROC in the books and in the map lowers the ACB twice
        # (A2-0361): roc-sum warned, this step said nothing.
        try:
            from taxjson.bin.taxjson_run import (_double_roc_warnings,
                                                 _year_keep)
            dbl = _double_roc_warnings(ctx.root, _accounts_of(ctx, "taxable"),
                                       _year_keep(str(ctx.year)))
        except SystemExit:
            dbl = []
        if dbl:
            return Result("roc-entered", "attention",
                          f"{len(dbl)} ROC entered twice — {dbl[0]}"
                          + (" ..." if len(dbl) > 1 else "")
                          + f" ({detail})")
    return Result("roc-entered", "manual", detail)


def d_inputs_committed(ctx: Ctx) -> Result:
    if not _is_git_repo(ctx.root):
        return Result("inputs-committed", "attention", "not a git repository")
    paths = ["inputs", "taxjson.toml", "ticker.map", "missing_history.json",
             "phantoms.json"]
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
    per_account: Dict[str, int] = {}
    unbooked: Dict[str, int] = {}
    empty_parse: List[str] = []
    unreadable: List[str] = []
    for s in sums:
        try:
            head = s.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            # Its validation count is unknown, not zero (S067-04).
            unreadable.append(f"reports/{s.name}")
            continue
        m = re.search(r"validation: (\d+) error", head)
        if m:
            # <acct>.sum and <acct>_wash.sum carry the SAME account's
            # diagnostics: one error was counted twice (R1-252).
            acct = s.stem[:-len("_wash")] if s.stem.endswith("_wash") \
                else s.stem
            per_account[acct] = max(per_account.get(acct, 0),
                                    int(m.group(1)))
        # A non-empty export that parsed to nothing dropped a whole
        # file from the books (R1-247).
        for f in re.findall(r"warning: (\S+) parsed to 0 transactions",
                            head):
            if f not in empty_parse:
                empty_parse.append(f)
        # Rows the parsers (or the crypto-sends stage) know are tax
        # events but could not book — the gate `run --strict` applies
        # (A2-0357, A2-0362). Counted per account, like the errors.
        try:
            whole = s.read_text(encoding="utf-8", errors="replace")
        except OSError:
            whole = head
        acct_ = s.stem[:-len("_wash")] if s.stem.endswith("_wash") else s.stem
        n_unb = sum(1 for ln in whole.splitlines()
                    if ln.lstrip().startswith(UNBOOKED_PREFIX))
        if n_unb:
            unbooked[acct_] = max(unbooked.get(acct_, 0), n_unb)
    errors = sum(per_account.values())
    pend = [p for p in ctx.cache.glob("*pending_elections.json")
            if p.is_file() and p.stat().st_size > 2]
    # A dangling reports/*.sum symlink is already in `unreadable`; its
    # stat must not take the step down (A2-1148).
    _mtimes = []
    for s in sums:
        try:
            _mtimes.append(s.stat().st_mtime)
        except OSError:
            if f"reports/{s.name}" not in unreadable:
                unreadable.append(f"reports/{s.name}")
    oldest_report = min(_mtimes) if _mtimes else 0.0
    problems = []
    if unreadable:
        problems.append(f"cannot read {', '.join(unreadable)} — its "
                        f"validation errors are unknown")
    if errors:
        problems.append(f"{errors} validation error(s) in reports/*.sum")
    if unbooked:
        problems.append(
            f"{sum(unbooked.values())} UNBOOKED event(s) in "
            f"{', '.join(f'reports/{a}.sum' for a in sorted(unbooked))} — "
            f"rows the run could not book are not in the books "
            f"(`run --strict` refuses them)")
    if pend:
        problems.append("pending elections (`taxjson elect --pending`)")
    orphans = orphan_work_accounts(ctx.root, ctx.accounts)
    if orphans:
        problems.append(
            f"work/ carries books of account(s) not in taxjson.toml: "
            f"{', '.join(orphans)} — every total counts them again; "
            f"delete work/<name>_* and reports/<name>* (a renamed "
            f"account) or restore the account")
    # What the last FULL run was built from (content, not mtimes): a
    # deleted input or a corrected export copied with its old mtime
    # (cp -p, rsync -a, unzip) left this step done over stale reports
    # (S067-07). Older projects without the record fall back to mtimes,
    # now including the project-root inputs `run` reads (R1-249).
    diff = inputs_changed(ctx.root, ctx.cfg)
    if diff:
        problems.append("inputs changed since the last full run ("
                        + diff + ")")
    elif diff is None:
        newest_input = 0.0
        for p in _input_paths(ctx.root, ctx.cfg):
            try:
                newest_input = max(newest_input, p.stat().st_mtime)
            except OSError:
                continue
        if newest_input > oldest_report + 1:
            problems.append("inputs changed since the last run")
    # Every configured account with inputs must have its report: a run
    # that died on one account, or a single `run --account X`, left the
    # others without books while this step said done (S018-02).
    unreported = [n for n in ctx.accounts
                  if _data_files(ctx.root / "inputs" / n)
                  and not (ctx.reports / f"{n}.sum").is_file()]
    if unreported:
        problems.append(f"no reports/<account>.sum for {', '.join(unreported)} "
                        f"— the last run did not finish them (run `taxjson run` "
                        f"with no --account)")
    # Two or more taxable equity accounts are filed on ONE blended
    # (s.47 ACB / cross-account wash) pass. `run --account` and a run
    # stopped at pending elections skip it, and with no wash files at
    # all every filing view read the per-account books as Schedule 3
    # figures in silence (S004-07).
    equity = _accounts_of(ctx, "taxable")
    if len(equity) >= 2:
        unblended = [n for n in equity
                     if (ctx.reports / f"{n}.sum").is_file()
                     and not (ctx.cache / f"{n}_gains_wash.json").is_file()]
        if unblended:
            # US basis is never blended: the missing pass is the
            # cross-account §1091 wash pass (A2-1262).
            if is_us(ctx.settings.get("country")):
                _pass, _books = ("cross-account wash-sale (§1091) pass",
                                 "per-account books with no cross-account "
                                 "wash sales")
            else:
                _pass, _books = ("blended (s.47) pass",
                                 "unblended per-account books")
            problems.append(
                f"no {_pass} for {', '.join(unblended)} — "
                f"the last run was per-account (`run --account`) or "
                f"stopped at pending elections, so the filing figures "
                f"are {_books} (run `taxjson run` "
                f"with no --account)")
    if empty_parse:
        problems.append(f"{', '.join(empty_parse)} parsed to 0 "
                        f"transactions (its rows are not in the books)")
    # An unconverted spreadsheet is never read; `taxjson run` refuses
    # it (R1-248) — the checklist must not call that run clean.
    sheets = []
    for n in ctx.accounts:
        folder = ctx.root / "inputs" / n
        if not folder.is_dir():
            continue
        stems = {p.stem.lower() for p in _data_files(folder)}
        sheets += [f"inputs/{n}/{p.name}" for p in sorted(folder.iterdir())
                   if p.is_file() and not _skipped_input_name(p.name)
                   and p.suffix.lower() in SPREADSHEET_SUFFIXES
                   and p.stem.lower() not in stems]
    if sheets:
        problems.append("unread spreadsheet(s) " + ", ".join(sheets)
                        + " — convert to CSV (taxjson-xlsx-to-csv)")
    # A .tt line whose total is not its qty x price +/- fee is booked as
    # written (lib/tt_totals; `run --strict` refuses it — QA F2).
    from taxjson.lib.tt_totals import project_mismatches
    tt_off = [f"inputs/{a}/{tt}:{m.where.rsplit(':', 1)[-1]}"
              for a, tt, m in project_mismatches(ctx.root, ctx.accounts)]
    if tt_off:
        problems.append(
            f"{len(tt_off)} .tt line(s) whose total is not qty x price "
            f"+/- fee, booked as written: {', '.join(tt_off[:5])}"
            + (f" +{len(tt_off) - 5} more" if len(tt_off) > 5 else "")
            + " — fix the total or put the difference in the fee column")
    if problems:
        return Result("run-clean", "attention", "; ".join(problems))
    stamp = datetime.fromtimestamp(oldest_report).strftime("%Y-%m-%d %H:%M")
    return Result("run-clean", "done", f"last run {stamp}, no validation errors")


FINGERPRINT_FILE = ".inputs_fingerprint.json"     # in work/
# The record's format: 2 = taxjson.toml by the settings `run` reads, the
# root maps of PROJECT_ROOT_MAPS, manifest.json / sends.json. A record
# without it (1) was written by an older taxjson and is compared the
# old way until the next full run rewrites it.
FINGERPRINT_VERSION = 2
# Project-root maps `taxjson run` reads (taxjson_run._PROJECT_ROOT_INPUTS
# is the same list; a test keeps the two equal — A2-0363, A2-1158).
PROJECT_ROOT_MAPS = ("ticker.map", "missing_history.json",
                     "phantoms.json")
# phantoms.json is the old name of missing_history.json (still read): a
# fingerprint keys it by the new name, so renaming the file is not an
# input change (_canon_fingerprint).
_LEGACY_INPUT_NAMES = {"phantoms.json": "missing_history.json"}
_ROOT_INPUTS = ("taxjson.toml",) + PROJECT_ROOT_MAPS
_LEGACY_ROOT_INPUTS = ("taxjson.toml", "ticker.map", "distributions.map",
                       "phantoms.json", "missing_history.json",
                       "ticker_extraction_overrides.txt")
# Per-account files `run` reads besides the activity files: the
# corp-action elections (A2-0124, A2-0126) and a crypto account's send
# decisions, which regenerate crypto_sends.tt (A2-0358).
_ACCOUNT_SIDECARS = ("manifest.json", "sends.json")
# taxjson.toml content no `taxjson run` stage reads — planning tables,
# the estimate's province, handoff's prior-year path, `sanity`'s
# holdings files and `fetch`'s broker keys. An edit to them (or to a
# comment) does not make the books stale (A2-0681, A2-1157).
_PLANNING_TABLES = ("instalments", "estimate", "carryover",
                    "capital_gains_dividends")
_PLANNING_SETTINGS = ("province", "prior_year_record")
_PLANNING_ACCOUNT_KEYS = ("holdings", "brokerage", "account", "query_id")


def _skipped_input_name(name: str) -> bool:
    """Hidden files and Office lock files ('~$x.csv', written while a
    file is open in Excel) are never inputs — `run` skips them too
    (A2-1145, A2-1166)."""
    return name.startswith((".", "~$"))


def _input_paths(root: Path, cfg: Dict[str, Any]) -> List[Path]:
    """Every file `taxjson run` reads to build the books: the project-
    root config and maps, and each configured account's activity files,
    generic-mapping sidecars (inputs/<acct>/*.toml), its elections
    manifest and its crypto send decisions."""
    out = [root / n for n in _ROOT_INPUTS if (root / n).is_file()]
    for n in sorted((cfg.get("accounts") or {})):
        folder = root / "inputs" / n
        if not folder.is_dir():
            continue
        out += sorted(p for p in folder.iterdir()
                      if p.is_file() and not _skipped_input_name(p.name)
                      and (p.suffix.lower() in (".csv", ".tt", ".toml")
                           or p.name in _ACCOUNT_SIDECARS))
    return out


def _run_config_digest(path: Path) -> str:
    """sha256 of the part of taxjson.toml a run reads: the parsed
    document without the planning-only tables and keys, canonically
    serialised (comments and layout do not count). A file that does
    not parse is hashed as bytes — it changed, whatever it says."""
    import hashlib
    raw = path.read_bytes()
    try:
        from taxjson.lib.tomlcompat import tomllib
        doc = tomllib.loads(raw.decode("utf-8-sig"))
    except Exception:                                   # noqa: BLE001
        return hashlib.sha256(raw).hexdigest()
    doc = {k: v for k, v in doc.items() if k not in _PLANNING_TABLES}
    if isinstance(doc.get("settings"), dict):
        doc["settings"] = {k: v for k, v in doc["settings"].items()
                           if k not in _PLANNING_SETTINGS}
    if isinstance(doc.get("accounts"), dict):
        doc["accounts"] = {
            n: ({k: v for k, v in a.items()
                 if k not in _PLANNING_ACCOUNT_KEYS}
                if isinstance(a, dict) else a)
            for n, a in doc["accounts"].items()}
    blob = json.dumps(doc, sort_keys=True, default=str).encode("utf-8")
    return "run-settings:" + hashlib.sha256(blob).hexdigest()


def _empty_manifest(p: Path) -> bool:
    """The elections manifest the corp stage creates on an account's
    first run ({"elections": {}}) is the same as none."""
    try:
        return json.loads(p.read_text(encoding="utf-8")) == {"elections": {}}
    except (OSError, ValueError):
        return False


def input_fingerprint(root: Path, cfg: Dict[str, Any]) -> Dict[str, str]:
    """{project-relative path: sha256} of every run input."""
    import hashlib
    out: Dict[str, str] = {}
    for p in _input_paths(root, cfg):
        if p.name == "manifest.json" and _empty_manifest(p):
            continue
        try:
            out[p.relative_to(root).as_posix()] = (
                _run_config_digest(p) if p.parent == root
                and p.name == "taxjson.toml"
                else hashlib.sha256(p.read_bytes()).hexdigest())
        except OSError:
            continue
    return out


def _legacy_input_fingerprint(root: Path, cfg: Dict[str, Any]
                              ) -> Dict[str, str]:
    """The format-1 fingerprint (raw taxjson.toml bytes, the old root
    maps, .csv/.tt/.toml only) — to judge a record an older taxjson
    wrote, without calling every project stale after an upgrade."""
    import hashlib
    paths = [root / n for n in _LEGACY_ROOT_INPUTS if (root / n).is_file()]
    for n in sorted((cfg.get("accounts") or {})):
        folder = root / "inputs" / n
        if folder.is_dir():
            paths += sorted(p for p in folder.iterdir()
                            if p.is_file() and not p.name.startswith(".")
                            and p.suffix.lower() in (".csv", ".tt", ".toml"))
    out: Dict[str, str] = {}
    for p in paths:
        try:
            out[p.relative_to(root).as_posix()] = hashlib.sha256(
                p.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def record_input_fingerprint(root: Path, cfg: Dict[str, Any]) -> None:
    """Called by a successful FULL `taxjson run`: what the reports were
    built from, for run-clean's staleness check."""
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    doc: Dict[str, Any] = {"version": FINGERPRINT_VERSION,
                           "files": input_fingerprint(root, cfg)}
    # The country the books were built under: a report command on books
    # of the other country refuses (audit A2-0147).
    try:
        from taxjson.lib.country import settings_country
        doc["country"] = settings_country(cfg.get("settings") or {})
    except ValueError:
        pass
    from taxjson.lib.safe_write import write_atomic
    write_atomic(work / FINGERPRINT_FILE,
                 json.dumps(doc, indent=1, sort_keys=True) + "\n")


def books_country(root: Path) -> Optional[str]:
    """The country the last full run built work/ under (its fingerprint
    record), or None when the record does not say."""
    try:
        doc = _load_fingerprint_doc(root)
    except FingerprintUnreadable:
        return None
    c = (doc or {}).get("country")
    return c if isinstance(c, str) and c else None


class FingerprintUnreadable(ValueError):
    """work/.inputs_fingerprint.json exists but is not a fingerprint."""


def _load_fingerprint_doc(root: Path) -> Optional[Dict[str, Any]]:
    """The recorded fingerprint document, None when there is none.
    Raises FingerprintUnreadable for a file that exists but cannot be
    read (truncated, files not a table): never "no record", which fell
    back to mtimes and called stale books clean (A2-1155)."""
    p = root / "work" / FINGERPRINT_FILE
    if not p.exists() and not p.is_symlink():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FingerprintUnreadable(str(e)) from e
    files = doc.get("files") if isinstance(doc, dict) else None
    if not isinstance(files, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in files.items()):
        raise FingerprintUnreadable("no files table")
    return doc


def _load_fingerprint(root: Path) -> Optional[Dict[str, str]]:
    try:
        doc = _load_fingerprint_doc(root)
    except FingerprintUnreadable:
        return None
    return doc["files"] if doc else None


def inputs_changed(root: Path, cfg: Dict[str, Any]) -> Optional[str]:
    """Why the books are not the result of the current inputs according
    to the last full run's record: a diff text, "" when they are, None
    when no record exists (the caller falls back to mtimes). The one
    rule for run-clean and the filing banners."""
    try:
        doc = _load_fingerprint_doc(root)
    except FingerprintUnreadable as e:
        return (f"work/{FINGERPRINT_FILE} cannot be read ({e}) — what the "
                f"last full run was built from is unknown")
    if doc is None:
        return None
    if doc.get("version") == FINGERPRINT_VERSION:
        return _fingerprint_diff(doc["files"], input_fingerprint(root, cfg))
    # An older record: the old comparison, plus the inputs it did not
    # cover, by mtime against the record itself.
    why = _fingerprint_diff(doc["files"], _legacy_input_fingerprint(root, cfg))
    if why:
        return why
    try:
        since = (root / "work" / FINGERPRINT_FILE).stat().st_mtime
    except OSError:
        return why
    legacy = set(_canon_fingerprint(doc["files"]))
    newer = sorted(p.relative_to(root).as_posix()
                   for p in _input_paths(root, cfg)
                   if _LEGACY_INPUT_NAMES.get(p.relative_to(root).as_posix(),
                                              p.relative_to(root).as_posix())
                   not in legacy
                   and p.name != "taxjson.toml"
                   and not (p.name == "manifest.json" and _empty_manifest(p))
                   and p.stat().st_mtime > since + 1)
    if newer:
        return "changed: " + ", ".join(newer[:3]) + (
            " ..." if len(newer) > 3 else "")
    return ""


def _canon_fingerprint(files: Dict[str, str]) -> Dict[str, str]:
    """A fingerprint's files with legacy names keyed by their new name
    (phantoms.json -> missing_history.json), so a rename alone — or a
    record an older taxjson wrote — is not a change."""
    return {_LEGACY_INPUT_NAMES.get(k, k): v for k, v in files.items()}


def _fingerprint_diff(before: Dict[str, str], now: Dict[str, str]) -> str:
    before, now = _canon_fingerprint(before), _canon_fingerprint(now)
    changed = sorted(k for k in before.keys() & now.keys()
                     if before[k] != now[k])
    added = sorted(now.keys() - before.keys())
    removed = sorted(before.keys() - now.keys())
    parts = []
    for label, items in (("changed", changed), ("added", added),
                         ("removed", removed)):
        if items:
            parts.append(f"{label}: {', '.join(items[:3])}"
                         + (" ..." if len(items) > 3 else ""))
    return "; ".join(parts)


def d_sanity(ctx: Ctx) -> Result:
    if not any(a.get("holdings") for a in ctx.accounts.values()):
        return Result("sanity", "manual",
                      "no `holdings = [...]` in taxjson.toml — run "
                      "`taxjson sanity ACCOUNT=FILE.toml` by hand")
    code, out, err = ctx.sub("sanity")
    incomplete = [ln for ln in out.splitlines()
                  if ln.startswith("INCOMPLETE")]
    if code == 0 and incomplete:
        # An account whose configured holdings file is missing was
        # never compared: exit 0 covers the other groups only (2026-09
        # audit R1-324).
        return Result("sanity", "attention", incomplete[0])
    unchecked = [ln for ln in out.splitlines()
                 if ln.startswith("UNCHECKED")]
    if code == 0 and unchecked:
        # An account with positions and no holdings file at all was
        # never tied either (S044-19).
        return Result("sanity", "attention", unchecked[0])
    if code == 0:
        return Result("sanity", "done", "positions tie to the holdings files")
    return Result("sanity", "attention", _last_line(out) or _last_line(err) or f"exit {code}")


def _mh_name(ctx) -> str:
    """The project's missing-history file name as the user has it
    (missing_history.json, or the legacy phantoms.json)."""
    from taxjson.lib.missing_history import (MISSING_HISTORY_FILE,
                                              project_missing_history_file)
    root = getattr(ctx, "root", None)
    try:
        p = project_missing_history_file(root, note=False) if root else None
    except ValueError:
        p = None
    return p.name if p is not None else MISSING_HISTORY_FILE


def d_missing_history(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("find-missing-history")
    if code != 0:
        # Exit 1 is "no base files" / "no transactions loaded": nothing
        # was checked, so it is never "nothing affects the year".
        return Result("missing-history", "blocked", _last_line(err) or _last_line(out) or f"exit {code}")
    syms: List[str] = []
    remove: List[str] = []
    in_affects = False
    in_remove = False
    # Every table row printed under an AFFECTS heading counts — a strict
    # symbol/currency pattern dropped 'SAMPLC/B', a '?' currency, 'USDT'
    # and lower-case coins, and the step said "nothing affects the
    # year" (S067-10). A section ends at the blank line before the next
    # heading; the header, the rules and indented detail lines are not
    # rows.
    for ln in out.splitlines():
        if ln.startswith("AFFECTS"):
            in_affects = True
            continue
        # missing-history entries on a real short / a written option
        # (A2-0639): the run applies them, so they are work to do. (The
        # file is named as the user has it: missing_history.json or the
        # legacy phantoms.json.)
        if ln.startswith("REMOVE from "):
            in_remove = True
            continue
        if (not ln.strip() or ln.startswith(("##", "NOT relevant",
                                              "To fix", "SHELTERED",
                                              "COVERED"))):
            # A blank line ends the section; registered-account rows have
            # no reportable gain: never counted as affecting the year
            # (audit S035-08); pairs the missing-history file covers are not work
            # to do (R1-339).
            in_affects = in_remove = False
            continue
        if (not (in_affects or in_remove) or ln[:1].isspace()
                or ln.startswith("-") or ln.startswith("Symbol ")):
            continue
        parts = ln.split()
        if len(parts) >= 2:
            (remove if in_remove else syms).append(
                f"{parts[0]} ({parts[1]})")
    if remove:
        shown = ", ".join(remove[:4]) + (" ..." if len(remove) > 4 else "")
        return Result("missing-history", "attention",
                      f"{len(remove)} {_mh_name(ctx)} entr"
                      f"{'y is' if len(remove) == 1 else 'ies are'} a real "
                      f"short or a written option — remove: {shown}"
                      + (f"; {len(syms)} position(s) with missing basis "
                         f"affect {ctx.year}" if syms else ""))
    if syms:
        shown = ", ".join(syms[:4]) + (" ..." if len(syms) > 4 else "")
        return Result("missing-history", "attention",
                      f"{len(syms)} position(s) with missing basis affect "
                      f"{ctx.year}: {shown}")
    return Result("missing-history", "done", "nothing affects the year")


def _zero_value_elections(ctx: Ctx) -> int:
    """Taxable spin-offs/mergers the last run booked at $0 (the "0 to
    defer" FMV): `run` names each in <acct>_corp_spinoff_value.diag."""
    n = 0
    for p in ctx.cache.glob("*_corp_spinoff_value.diag"):
        try:
            n += sum(1 for ln in p.read_text(encoding="utf-8").splitlines()
                     if ln.startswith("warning:"))
        except OSError:
            continue
    return n


def d_elections(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("elect", "--pending")
    # A deferred FMV is an unresolved election too: booked at $0 it
    # carries no income and a $0 cost for the new shares (R1-11).
    zero = _zero_value_elections(ctx)
    if zero:
        # Only the project country's rollover (A2-1245): s.86.1 and
        # allocated_acb_cad in Canada, §355 and allocated_acb in the US.
        roll = ("a §355 spin-off's allocated_acb"
                if is_us(ctx.settings.get("country"))
                else "an s.86.1 rollover's allocated_acb_cad")
        return Result("elections", "attention",
                      f"{zero} spin-off/merger(s) booked at $0 — set "
                      f"fmv_per_share (or {roll}) "
                      f"with `taxjson elect` (see "
                      f"the .sum DIAGNOSTICS)")
    if "No pending elections" in out or (code == 0 and not out.strip()):
        return Result("elections", "done", "none pending")
    if code != 0 and not out:
        return Result("elections", "blocked", _last_line(err) or f"exit {code}")
    return Result("elections", "attention", _last_line(out))


def d_crypto_sends(ctx: Ctx) -> Result:
    """Offline and fast: the sidecars + inputs/<acct>/sends.json + the
    ids recorded in the generated crypto_sends.tt — no price lookups."""
    from taxjson.lib import crypto_sends as cs
    names = _accounts_of(ctx, "crypto")
    if not names:
        return Result("crypto-sends", "n/a", "no crypto account configured")
    if not any((ctx.cache / f"{n}_{b}_transfers.json").is_file()
               for n in names for b in ("kraken", "coinbase")):
        return Result("crypto-sends", "blocked",
                      "no crypto transfer evidence in work/ — run `taxjson run`")
    # A crypto account not parsed yet: a send to it reads as unmatched
    # and undecided (A2-0359) — the books are not ready to judge.
    # (The command's own guard, crypto_sends.stale_evidence.)
    try:
        from taxjson.bin.taxjson_run import (_crypto_broker_files,
                                             _transfers_accounts)
        unparsed = cs.stale_evidence(
            ctx.cache, _crypto_broker_files(ctx.root, ctx.cfg),
            skip=_transfers_accounts(ctx.cfg))
    except SystemExit:
        unparsed = []
    if unparsed:
        return Result("crypto-sends", "blocked",
                      f"the transfer evidence is not current for "
                      f"{'; '.join(unparsed)} — a send may look unmatched; "
                      f"run `taxjson run`")
    try:
        rep = cs.build_report(ctx.root, ctx.cfg, None, None, with_pool=False)
    except ValueError as e:
        return Result("crypto-sends", "attention", str(e))
    undecided, stale, total, refused, dups = [], [], 0, [], []
    overridden = []
    for n, a in rep["accounts"].items():
        total += len(a["sends"])
        overridden += [o["id"] for o in a.get("overridden") or []]
        if a["undecided"]:
            undecided.append(f"{n}: {a['undecided']}")
        refused += [e["id"] for e in cs.refused_entries(a)]
        if cs.tt_stale_ids(a, Path(a["tt_file"])):
            stale.append(n)
        # A hand-written .tt line selling what crypto_sends.tt sells:
        # both are booked (A2-0127).
        dups += cs.duplicate_lines(ctx.root / "inputs" / n,
                                   cs.disposing_entries(a))
    if dups:
        d0 = dups[0]
        return Result("crypto-sends", "attention",
                      f"{len(dups)} hand-written .tt line(s) sell a send "
                      f"crypto_sends.tt also sells — counted twice: "
                      f"{d0['file']} line {d0['line']} ({d0['id']})"
                      + (" ..." if len(dups) > 1 else "")
                      + " — delete the hand-written line, or record the "
                        "send as `self`")
    if refused:
        return Result("crypto-sends", "attention",
                      f"saved as `gift`, which a US project refuses (not "
                      f"booked): {', '.join(refused)} — record each as "
                      f"self or payment (`taxjson crypto-sends ACCOUNT "
                      f"--set ID=self`)")
    if overridden:
        # A saved gift/payment the pairing overrode is not booked
        # (re-audit A2-0004): never "every send arrived".
        return Result("crypto-sends", "attention",
                      f"saved gift/payment now paired with an arrival "
                      f"(booked as your own move, NOT as the decision): "
                      f"{', '.join(overridden)} — `taxjson crypto-sends "
                      f"ACCOUNT` says how to unpair or confirm it")
    if undecided:
        return Result("crypto-sends", "attention",
                      f"undecided send(s) — {', '.join(undecided)}; "
                      f"decide with `taxjson crypto-sends ACCOUNT --set ID=...`")
    if stale:
        return Result("crypto-sends", "attention",
                      f"crypto_sends.tt out of date for {', '.join(stale)} — "
                      f"`taxjson crypto-sends ACCOUNT --write`")
    return Result("crypto-sends", "done",
                  f"{total} unmatched send(s), all decided" if total
                  else "every send arrived on another exchange")


def _books_state(ctx: Ctx) -> Optional[Result]:
    """blocked/attention for the steps that compare the taxable books
    (audit, form-export) when work/ was built for another tax year or a
    wash-adjusted file is older than its inputs — otherwise their
    mismatch text blames missing history or an export bug (S068-16) or they
    say done over stale numbers (S067-12). None when the books are fine
    or cannot be judged (the command's own error is then reported)."""
    from taxjson.lib.report_model import resolve_gains_files, stale_wash_inputs
    taxable = {n for n, a in ctx.accounts.items()
               if a.get("type") == "taxable"}
    try:
        files = {a: p for a, p in resolve_gains_files(ctx.cache).items()
                 if a in taxable}
    except Exception:
        return None
    other, stale = [], []
    for a, pth in sorted(files.items()):
        try:
            doc = json.loads(Path(pth).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        summ = doc.get("summary") if isinstance(doc, dict) else None
        yr = summ.get("year") if isinstance(summ, dict) else None
        if yr is not None and str(yr) != str(ctx.year):
            other.append(f"{a}: {yr}")
        if Path(pth).name.endswith("_gains_wash.json") \
                and stale_wash_inputs(Path(pth)):
            stale.append(a)
    if other:
        return Result("", "blocked",
                      f"work/ was built for another tax year "
                      f"({', '.join(other)}; [settings] year is {ctx.year}) "
                      f"— rebuild with `taxjson run`")
    if stale:
        return Result("", "attention",
                      f"the wash-adjusted books of {', '.join(stale)} are "
                      f"older than their inputs (a `run --account` skipped "
                      f"the cross-account pass) — run `taxjson run`")
    return None


def d_audit(ctx: Ctx) -> Result:
    st = _books_state(ctx)
    if st is not None:
        st.id = "audit"
        return st
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
    # Unknown-cost sales the books route to manual reporting are tied
    # out as such by the audit (A2-1150); form-export's step asks for
    # the hand-reported rows.
    manual = sum(int(m.group(1).replace(",", "")) for m in re.finditer(
        r"([\d,]+) (?:unknown-cost|phantom-basis) disposition\(s\) "
        r"tied to MANUAL REPORTING", out))
    if bad:
        return Result("audit", "attention", f"{bad} disposition(s) MISMATCHED")
    if notfound:
        return Result("audit", "attention",
                      f"{notfound} disposition(s) not found in the gains file "
                      f"— stale books? re-run `taxjson run`")
    if code != 0:
        return Result("audit", "attention", _last_line(err) or f"exit {code}")
    return Result("audit", "done", f"{events} disposition(s) tied"
                  + (f"; {manual} sale(s) with unknown cost (no purchase "
                     f"in your files) routed to manual "
                     f"reporting (see the form-export step)" if manual else ""))


def d_wash_reviewed(ctx: Ctx) -> Result:
    names = _accounts_of(ctx, "taxable") + _accounts_of(ctx, "crypto")
    trade_basis = (str(ctx.settings.get("tax_date") or "").lower() == "trade"
                   or (not ctx.settings.get("tax_date")
                       and is_us(ctx.settings.get("country"))))
    from taxjson.lib.report_model import stale_wash_inputs
    denied = perm = 0.0
    flags: set = set()
    seen = False
    # Every taxable book must be read, current and built for this year —
    # a total over the readable subset marked the step done while a
    # denial went unreviewed (R1-338 unreadable, S067-11 missing, S067-12
    # stale wash pass, S068-16 work/ built for another year).
    unreadable: List[str] = []
    missing: List[str] = []
    stale: List[str] = []
    other_year: List[str] = []
    for n in names:
        f = ctx.cache / f"{n}_gains_wash.json"
        if not f.is_file():
            f = ctx.cache / f"{n}_gains.json"
        if not f.is_file():
            if _data_files(ctx.root / "inputs" / n):
                missing.append(n)
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(f"work/{f.name}")
            continue
        if not isinstance(doc, dict):
            unreadable.append(f"work/{f.name}")
            continue
        if f.name.endswith("_gains_wash.json") and stale_wash_inputs(f):
            stale.append(n)
        _yr = (doc.get("summary") or {}).get("year") \
            if isinstance(doc.get("summary"), dict) else None
        if _yr is not None and str(_yr) != str(ctx.year):
            other_year.append(f"{n}: {_yr}")
        seen = True
        # Same year basis as every other denial total (the gains file
        # is already year-scoped on it): a loss traded Dec 31 that
        # settles in January belongs to the settle year (R1-158).
        for t in doc.get("transactions") or []:
            if not isinstance(t, dict):
                continue
            when = (t.get("date") if trade_basis
                    else t.get("date_settle") or t.get("date"))
            if not str(when or "").startswith(str(ctx.year)):
                continue
            denied += float(t.get("disallowed_amount") or 0)
            perm += float(t.get("permanently_disallowed") or 0)
        # Warn-only manual-check flags (a warrant/right, an adjusted
        # series or a futures option bought in a loss's window:
        # CA-SL-14/15, US-WASH-14/15) deny nothing, so the totals above
        # never see them; the step said "no superficial losses" over
        # them (A2-0413).
        for w in doc.get("option_replacement_warnings") or []:
            if isinstance(w, dict) and str(w.get("loss_date") or "") \
                    .startswith(str(ctx.year)):
                flags.add(f"{w.get('loss_symbol')} {w.get('loss_date')} "
                          f"[{w.get('rule')}]")
    blockers = []
    if unreadable:
        blockers.append(f"cannot read {', '.join(unreadable)}")
    if missing:
        blockers.append(f"no gains file for {', '.join(missing)}")
    if other_year:
        blockers.append(f"work/ built for another year ({', '.join(other_year)}"
                        f"; [settings] year is {ctx.year})")
    if blockers:
        return Result("wash-reviewed", "blocked",
                      "; ".join(blockers) + " — re-run `taxjson run`")
    if not seen:
        return Result("wash-reviewed", "blocked", "no gains files — run `taxjson run`")
    if stale:
        return Result("wash-reviewed", "attention",
                      f"the wash-adjusted books of {', '.join(stale)} are "
                      f"older than their inputs (a `run --account` skipped "
                      f"the cross-account pass) — run `taxjson run`")
    # US projects in §1091's words, never CRA's (S049-14).
    _us = is_us(ctx.settings.get("country"))
    # A loss on one listing with the other listing bought in its window,
    # the pair neither joined nor ruled DISTINCT (lib/xlist_loss_radar):
    # the books deny nothing until ticker.map answers it.
    from taxjson.lib.xlist_loss_radar import open_findings
    xl = [f"{f['loss_symbol']}/{f['other_symbol']}"
          for f in open_findings(ctx.root)]
    if xl:
        return Result("wash-reviewed", "attention",
                      f"{len(xl)} possible "
                      f"{'wash sale' if _us else 'superficial loss'}(s) "
                      f"across listings: {', '.join(xl[:4])}"
                      f"{' ...' if len(xl) > 4 else ''} — add the TOBASE "
                      f"(one security) or DISTINCT (two) line to ticker.map "
                      f"(`taxjson ticker-map --suggest`)")
    flag_note = ""
    if flags:
        shown = sorted(flags)
        flag_note = (f"{len(shown)} warn-only manual-check flag(s) "
                     f"(nothing denied by the books): "
                     f"{', '.join(shown[:4])}"
                     f"{' ...' if len(shown) > 4 else ''} — decide each "
                     f"by hand (`taxjson wash-sales` lists them)")
    if perm > 0.005:
        return Result("wash-reviewed", "manual",
                      f"{perm:,.2f} permanently denied ("
                      f"{'IRA' if _us else 'registered-account or affiliated-person'}"
                      f" repurchase) "
                      f"— confirm each with `taxjson wash-sales`"
                      + (f"; {flag_note}" if flag_note else ""))
    if flag_note:
        return Result("wash-reviewed", "manual",
                      (f"{denied:,.2f} denied; " if denied > 0.005 else "")
                      + flag_note)
    if denied > 0.005:
        return Result("wash-reviewed", "done",
                      f"{denied:,.2f} denied, all recoverable "
                      + ("(added to the replacement's basis)" if _us
                         else "(added to ACB)"))
    return Result("wash-reviewed", "done",
                  "no wash sales" if _us else "no superficial losses")


def _us_expired_options(ctx: Ctx) -> Result:
    """US projects (S066-15): option-boundary is a Canadian (s.49)
    command, but the missing-expiry-row check applies to both long and
    written contracts — run it on the taxable base books directly."""
    from taxjson.lib.core import TaxTransaction
    from taxjson.lib.option_boundary import expired_open
    names = _accounts_of(ctx, "taxable")
    docs, bad = _base_docs_checked(ctx, names)
    if bad:
        return Result("option-boundary", "blocked",
                      f"unreadable: {', '.join(bad)} — re-run `taxjson run`")
    if not docs:
        return Result("option-boundary", "blocked",
                      "no work/*_base.json — run `taxjson run`")
    found = []
    fields = TaxTransaction.__dataclass_fields__
    for name, d in sorted(docs.items()):
        txs = []
        for r in (d.get("transactions") or []):
            if not isinstance(r, dict):
                continue
            try:
                txs.append(TaxTransaction(**{k: v for k, v in r.items()
                                             if k in fields}))
            except TypeError:
                continue
        found += [dict(x, account=x["account"] or name) for x in
                  expired_open(txs, ctx.year, today=ctx.today,
                               tax_date=_tax_date_of(ctx))]
    if found:
        shown = ", ".join(f"{x['symbol']} {x['quantity']:g} ({x['account']}, "
                          f"expired {x['expiry']})" for x in found[:3])
        _closing = [x for x in found if x.get("broker_closing")]
        return Result("option-boundary", "attention",
                      f"{len(found)} option position(s) still open past "
                      f"expiry: {shown}{' ...' if len(found) > 3 else ''} — "
                      f"import the expiry, exercise or assignment row"
                      + (f" ({len(_closing)} opened by a trade the broker "
                         f"codes CLOSING, IB code C: add the missing "
                         f"purchase instead — `taxjson find-missing-history`)"
                         if _closing else ""))
    return Result("option-boundary", "done",
                  "no option position open past its expiry")


def _tax_date_of(ctx: Ctx) -> str:
    from taxjson.lib.country import settings_tax_date
    return settings_tax_date(ctx.settings)


def d_option_boundary(ctx: Ctx) -> Result:
    if not _accounts_of(ctx, "taxable"):
        return Result("option-boundary", "n/a",
                      "no non-crypto taxable account — no written options")
    if is_us(ctx.settings.get("country")):
        return _us_expired_options(ctx)
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
                         f"timing, a premium no filed return reports, or an "
                         f"expired contract with no expiry row)")
        _asked = [r for r in rows if r.get("question")]
        if _asked:
            from taxjson.lib.option_boundary import question_detail
            parts.append(question_detail(_asked, ctx.year))
        return Result("option-boundary", "attention",
                      "; ".join(parts) + " — `taxjson option-boundary`")
    if doc.get("missing_books"):
        return Result("option-boundary", "blocked",
                      f"no books for {', '.join(doc['missing_books'])} — run `taxjson run`")
    if not doc.get("since_explicit", True) and str(doc.get("timing")) == "grant":
        return Result("option-boundary", "attention",
                      "option_grant_timing_since is not set in [settings] — the default "
                      "follows `year`; set it once and keep it")
    asked = [r for r in rows if r.get("question")]
    if asked:
        # Transition contracts closed this year (CA-OPT-11): the premium
        # is in this year's gain, right only if the write year's return
        # did not report it — the user's answer (a DONE mark, or the
        # setting lowered to the write year) settles it.
        from taxjson.lib.option_boundary import question_detail
        return Result("option-boundary", "attention",
                      question_detail(asked, ctx.year), question=True)
    return Result("option-boundary", "done", "no amendment required")


def d_check_dates(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("check-dates", "--json")
    try:
        doc = json.loads(out) if out.strip() else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        return Result("check-dates", "blocked", _last_line(err) or f"exit {code}")
    if doc.get("errors"):
        return Result("check-dates", "attention",
                      f"{doc['errors']} impossible date(s) — `taxjson check-dates`")
    if doc.get("warnings"):
        return Result("check-dates", "attention",
                      f"{doc['warnings']} unusual date(s) to review — "
                      f"`taxjson check-dates`")
    return Result("check-dates", "done", f"{doc.get('checked', 0)} rows checked")


def d_renames(ctx: Ctx) -> Result:
    from taxjson.lib.renames import report
    if not ctx.cache.is_dir():
        return Result("renames", "blocked", "no work/ — run `taxjson run`")
    try:
        doc = report(ctx.root, ctx.cfg)
    except ValueError as e:
        return Result("renames", "blocked", str(e))
    if doc["unresolved"]:
        return Result("renames", "attention",
                      f"{doc['unresolved']} trade(s) in an old ticker after "
                      f"its rename not declared — `taxjson renames`")
    if doc.get("suggested"):
        return Result("renames", "attention",
                      f"{len(doc['suggested'])} look-alike rename(s) not "
                      f"booked — `taxjson renames --pending`")
    if doc.get("unused"):
        return Result("renames", "attention",
                      f"{len(doc['unused'])} declared rename(s) book "
                      f"nothing — `taxjson renames --pending`")
    return Result("renames", "done",
                  f"{len(doc['renames'])} dated rename(s); no undeclared "
                  f"late trade")


def d_journals(ctx: Ctx) -> Result:
    from taxjson.lib.journals import JournalsError, report
    if not ctx.cache.is_dir():
        return Result("journals", "blocked", "no work/ — run `taxjson run`")
    try:
        doc = report(ctx.root, ctx.cfg)
    except JournalsError as e:
        return Result("journals", "blocked",
                      f"{e} — run `taxjson run`")
    c = doc["counts"]
    if doc["pending"]:
        return Result("journals", "attention",
                      f"{doc['pending']} pending journal(s) between two "
                      f"listings ({c['suggested']} suggested, "
                      f"{c['refused'] - c['decided']} refused) — "
                      f"`taxjson journals --pending`")
    return Result("journals", "done",
                  f"{c['joined']} journal(s) between two listings joined"
                  + (f", {c['decided']} kept apart by ticker.map"
                     if c["decided"] else ""))


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
                         ("double", "sale(s) reported in both years"),
                         ("timing", "written option(s) on another premium "
                                    "timing than the closed year"),
                         ("boundary", "row(s) on different sides of "
                                      "Dec 31 in the two projects"),
                         ("partial", "the closed year's record is a "
                                    "partial-year snapshot")):
            if doc.get(k):
                parts.append(f"{len(doc[k])} {label}")
        return Result("handoff", "attention",
                      "; ".join(parts) + " — `taxjson handoff`")
    return Result("handoff", "done", f"{y - 1} carried forward once")


def slip_files(root: Path) -> List[Path]:
    """The slip CSVs: every .csv (any case — `run` reads inputs case-
    insensitively, S067-21) in inputs/slips/, the documented folder. A
    file under an account folder is that account's input, never a slip
    because '1099' or 't5008' appears in its name (an account number or
    a date run matched, S067-23)."""
    slips = root / "inputs" / "slips"
    if not slips.is_dir():
        return []
    return sorted((p for p in slips.iterdir()
                   if p.is_file() and not _skipped_input_name(p.name)
                   and p.suffix.lower() == ".csv"),
                  key=lambda p: p.name.lower())


def _unread_slip_files(root: Path) -> List[Path]:
    slips = root / "inputs" / "slips"
    if not slips.is_dir():
        return []
    return sorted(p for p in slips.iterdir()
                  if p.is_file() and not _skipped_input_name(p.name)
                  and p.suffix.lower() != ".csv")


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
        if c.get("ambiguous_listing"):
            parts.append(f"{c['ambiguous_listing']} ambiguous listing")
        if rep.get("unreadable_rows"):
            parts.append(f"{rep['unreadable_rows']} unreadable slip row(s)")
        # Real mismatches first as the examples (R1-1).
        _rank = {"MISMATCH": 0, "AMBIGUOUS_LISTING": 1,
                 "MISSING_FROM_COMPUTED": 2, "MISSING_FROM_SLIP": 3}
        bad = [f"{r.get('symbol')} {r.get('status')}"
               for r in sorted((r for r in (rep.get("rows") or [])
                                if r.get("status") in _rank),
                               key=lambda r: _rank[r.get("status")])]
        if bad:
            parts.append("e.g. " + ", ".join(bad[:3]) + (" ..." if len(bad) > 3 else ""))
        return ", ".join(parts)
    for ln in reversed(out.splitlines()):
        if re.search(r"\d+ OK, \d+ mismatch", ln) or ln.startswith("NOT RECONCILED"):
            return ln.strip()
    return _last_line(err) or _last_line(out) or f"exit {code}"


def _slip_names(ctx: Ctx) -> str:
    """The slips a project's dispositions come on: T5008 in Canada; in
    the US Form 1099-B for securities and, from tax year 2025, Form
    1099-DA for a broker's digital-asset sales (US-RPT-10, A2-1149)."""
    if not is_us(ctx.settings.get("country")):
        return "T5008"
    names = []
    if _accounts_of(ctx, "taxable"):
        names.append("1099-B")
    if _accounts_of(ctx, "crypto"):
        names.append("1099-DA" if ctx.year >= 2025 else "1099-B")
    return " / ".join(dict.fromkeys(names)) or "1099-B"


def d_t5008(ctx: Ctx) -> Result:
    slip = _slip_names(ctx)
    files = slip_files(ctx.root)
    unread = _unread_slip_files(ctx.root)
    if not files:
        return Result("t5008", "todo",
                      f"no slip file — put the broker {slip} CSVs in inputs/slips/"
                      + (f" ({', '.join(p.name for p in unread)} is not a CSV)"
                         if unread else ""))
    # ONE reconciliation over every slip file: each broker sends its
    # own slip, and each file alone reported the other brokers' sales
    # as missing from the slip (R1-207).
    code, out, err = ctx.sub("reconcile-slips", *[str(f) for f in files],
                             "--json")
    problems = []
    if code != 0:
        problems.append(", ".join(f.name for f in files) + ": "
                        + _slip_mismatch_summary(code, out, err))
    if unread:
        problems.append("not reconciled (not CSV): "
                        + ", ".join(p.name for p in unread)
                        + " — transcribe to CSV or move out of inputs/slips/")
    if problems:
        return Result("t5008", "attention", "; ".join(problems))
    return Result("t5008", "done", f"{len(files)} slip file(s) reconcile together")


def d_form_export(ctx: Ctx) -> Result:
    """The export must equal what the engine computed: form-export's totals
    against `taxjson sum`'s FOR THE RETURN block (same dispositions), and
    that block's gain against the taxable accounts' realized gain."""
    st = _books_state(ctx)
    if st is not None:
        st.id = "form-export"
        return st
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
    # The rows are rounded to the cent one by one while the .sum rounds
    # once per account, so the residual grows like a random walk: each
    # rounding is uniform on +-0.005 (sd 0.0029), and 5 standard
    # deviations of the sum is 0.015 * sqrt(roundings). Never below the
    # old 0.05 (R1-280: 831 rows gave a 0.06 residual and a false
    # attention); a missing or doubled row is still far outside it.
    import math
    n_acct = max(1, len(taxable))
    raw = rep.get("gain_unrounded")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        # Unrounded rows vs the .sum's per-account rounding: at most half
        # a cent per account apart (R1-210; Form 8949 too, A2-1154).
        cmp_gain = float(raw)
        tol = 0.005 * n_acct + 0.01
    else:
        cmp_gain = t["gain"]
        tol = max(0.05, 0.015 * math.sqrt(n + n_acct))
    # US §1256 contracts stay off Form 8949 (Form 6781 by hand) but are
    # in the accounts' realized gain: add them back for this tie.
    s1256 = rep.get("section_1256_totals") if label == "Form 8949" else None
    if isinstance(s1256, dict):
        cmp_gain += float(s1256.get("gain") or 0.0)
    if abs(cmp_gain - realized) > tol:
        problems.append(f"{label} gain {t['gain']:,.2f} vs realized {realized:,.2f} "
                        f"in the taxable accounts' .sum")
    # Unknown-cost dispositions are not in the rows (their cost is
    # unknown) — the export is not complete until they are reported by
    # hand (audit R1-199: this step showed [x] while they were missing).
    manual = rep.get("manual_reporting_required") or []
    if manual:
        problems.append(
            f"{len(manual)} sale(s) with no purchase in your files "
            f"(unknown cost; proceeds "
            f"{float(rep.get('manual_proceeds') or 0.0):,.2f}) are not in "
            f"the {label} rows — report them by hand (form-export's "
            f"MANUAL REPORTING section)")
    if problems:
        return Result("form-export", "attention", "; ".join(problems))
    return Result("form-export", "done",
                  f"{n} row(s); gain {t['gain']:,.2f} equals FOR THE RETURN and the .sum")


def d_t1135(ctx: Ctx) -> Result:
    if is_us(ctx.settings.get("country")):
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
    if rep.get("year_complete") is False or ctx.today <= date(ctx.year, 12, 31):
        # ITA 233.3 counts cost at any time up to Dec 31: below the
        # threshold mid-year is not a verdict (S051-22, S052-15).
        return Result("t1135", "todo",
                      f"below the CAD 100,000 threshold so far on these "
                      f"books (through {rep.get('as_of') or '?'}) — re-check "
                      f"after Dec 31; foreign property outside them (a "
                      f"foreign bank account, cash) is not counted")
    # The books only (S052-13, A2-0682): a foreign bank account or cash
    # outside them adds to the same threshold.
    return Result("t1135", "done",
                  "below the CAD 100,000 threshold on these books — "
                  "foreign property outside them (a foreign bank account, "
                  "cash) is not counted; add it if you hold any")


def d_carryover(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("carryover", "--json")
    _cfg = getattr(ctx, "cfg", None) or {}
    claimed = bool((_cfg.get("carryover") or {}).get("claimed")) \
        if isinstance(_cfg.get("carryover"), dict) else False
    if code != 0 and not out:
        return Result("carryover", "blocked", _last_line(err) or f"exit {code}")
    try:
        doc = json.loads(out) if out.strip() else {}
    except ValueError:
        doc = {}
    ignored = (doc.get("claimed_ignored") or []) if isinstance(doc, dict) \
        else []
    if ignored:
        # A claimed line the ledger could not read is not applied: the
        # carryforward is overstated by it (S001-04).
        return Result("carryover", "attention",
                      f"{len(ignored)} claimed line(s) ignored "
                      f"(not applied): {ignored[0]}"
                      + (" ..." if len(ignored) > 1 else "")
                      + " — fix the entry")
    if is_us(ctx.settings.get("country")):
        what = ("record each year's Schedule D line 21 deduction against "
                "ordinary income as far as taxable income absorbed it "
                "(Capital Loss Carryover Worksheet line 4), not the "
                "line 6 / 14 carryover")
    else:
        what = ("record the 100% loss applied each year (line 25300 "
                "divided by the inclusion rate: x2 at 50%)")
    return Result("carryover", "manual",
                  "[carryover] claimed " + ("present" if claimed
                                            else "absent")
                  + " — " + what)


def d_fx_cash(ctx: Ctx) -> Result:
    code, out, err = ctx.sub("fx-cash")
    if code != 0 and not out:
        return Result("fx-cash", "blocked", _last_line(err) or f"exit {code}")
    return Result("fx-cash", "manual", _last_line(out)[:100])


def d_fees(ctx: Ctx) -> Result:
    if is_us(ctx.settings.get("country")):
        return Result("fees", "manual",
                      "investment interest (Form 4952) is entered by hand "
                      "from the statements' margin interest (`taxjson "
                      "fees` lists commissions and fees, which are not "
                      "investment interest)")
    return Result("fees", "manual",
                  "line 22100 is entered by hand from the statements' margin "
                  "interest (`taxjson fees` lists commissions and fees, which "
                  "are not carrying charges)")


def d_estimate(ctx: Ctx) -> Result:
    est = (ctx.cfg.get("estimate") or {}).get("other_income")
    first = f"[estimate] other_income {'set' if est is not None else 'unset'}"
    # [instalments] is Canada's table (CONFIG_COUNTRY; a US config
    # refuses it): a US project never hears of it (A2-0738).
    if is_us(ctx.settings.get("country")):
        return Result("estimate", "manual",
                      f"{first}; US estimated tax payments are not "
                      f"modelled — check them yourself")
    inst = bool(ctx.cfg.get("instalments"))
    return Result("estimate", "manual",
                  f"{first}; "
                  f"[instalments] {'present' if inst else 'absent'}")


def d_amt(ctx: Ctx) -> Result:
    """`taxjson amt --json`: whether AMT binds, what carryover was
    recovered and where it came from (Canada only)."""
    code, out, err = ctx.sub("amt", "--json")
    if code != 0 or not out.strip():
        return Result("amt", "blocked", _last_line(err) or f"exit {code}")
    try:
        doc = json.loads(out)
    except ValueError:
        return Result("amt", "blocked", "unreadable `taxjson amt --json`")
    a = doc.get("amt") or {}
    c = a.get("carryover") or {}
    src = (doc.get("carry_sources") or {}).get("amt_carryover")
    if a.get("binding"):
        first = (f"AMT binds: {a.get('excess_fed', 0.0):,.2f} federal "
                 f"additional tax carries forward 7 years")
    else:
        first = (f"AMT does not bind (regular tax over the minimum by "
                 f"{a.get('headroom', 0.0):,.2f})")
    if src:
        rest = (f"carryover {c.get('available', 0.0):,.2f} from {src}, "
                f"{c.get('recovered_federal', 0.0):,.2f} recovered "
                f"federally (line 40427)")
    else:
        rest = ("no minimum tax carryover entered — if you paid AMT in "
                "the last 7 years, put it in [estimate] amt_carryover")
    return Result("amt", "manual", f"{first}; {rest}")


def _lock_state(ctx: Ctx, sid: str) -> Optional[Result]:
    """todo when there is no lock, blocked when filed/<year>.json is
    not a readable lock (a directory, a dangling link: close-year says
    it 'already exists', A2-1146), attention when it was taken before
    the year ended (A2-0679, A2-1164). None: a lock to check."""
    lock = ctx.root / "filed" / f"{ctx.year}.json"
    if not lock.exists() and not lock.is_symlink():
        return Result(sid, "todo",
                      "no filed/<year>.json — `taxjson close-year` after filing"
                      if sid == "filed-lock" else "no lock yet")
    try:
        doc = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        why = ("a directory" if lock.is_dir() else
               getattr(e, "strerror", None) or str(e))
        return Result(sid, "blocked",
                      f"filed/{ctx.year}.json cannot be read ({why}) — "
                      f"restore the lock from git, or remove it and "
                      f"`taxjson close-year` after filing")
    from taxjson.bin.taxjson_filed import partial_year_note
    note = partial_year_note(doc, ctx.year)
    if note:
        return Result(sid, "attention", note)
    return None


def d_filed_lock(ctx: Ctx) -> Result:
    st = _lock_state(ctx, "filed-lock")
    if st is not None:
        return st
    code, out, err = ctx.sub("check-filed")
    if code != 0:
        # Only a reported DRIFT is drift; a failed recompute (bad
        # config, unreadable lock, crash) is not a reason to amend a
        # return or re-lock it (S031-21).
        if "DRIFTED" in err or "DRIFTED" in out:
            return Result("filed-lock", "attention",
                          "check-filed reports drift against the lock — amend or "
                          "`close-year --force` after re-filing")
        why = next((ln.strip() for ln in (err + "\n" + out).splitlines()
                    if "could not be checked" in ln), "")
        return Result("filed-lock", "blocked",
                      "check-filed could not check the lock (not drift): "
                      + (why or _last_line(err) or _last_line(out)
                         or f"exit {code}"))
    return Result("filed-lock", "done", f"locked; no drift")


def d_lock_committed(ctx: Ctx) -> Result:
    st = _lock_state(ctx, "lock-committed")
    if st is not None:
        return st
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
    "export-coverage": d_export_coverage,
    "sheltered-inputs": d_sheltered_inputs,
    "crypto-inputs": d_crypto_inputs,
    "roc-entered": d_roc_entered,
    "inputs-committed": d_inputs_committed,
    "run-clean": d_run_clean,
    "check-dates": d_check_dates,
    "renames": d_renames,
    "journals": d_journals,
    "sanity": d_sanity,
    "missing-history": d_missing_history,
    "elections": d_elections,
    "crypto-sends": d_crypto_sends,
    "audit": d_audit,
    "wash-reviewed": d_wash_reviewed,
    "option-boundary": d_option_boundary,
    "handoff": d_handoff,
    "t5008": d_t5008,
    "t5-t3": lambda ctx: Result("t5-t3", "manual", "compare the slips with the TAXABLE line of `taxjson divs-sum` / `roc-sum`"),
    "foreign-tax": lambda ctx: Result("foreign-tax", "manual", "from the slips"),
    "form-export": d_form_export,
    "t1135": d_t1135,
    "carryover": d_carryover,
    "fx-cash": d_fx_cash,
    "fees": d_fees,
    "estimate": d_estimate,
    "amt": d_amt,
    "filed-lock": d_filed_lock,
    "lock-committed": d_lock_committed,
    "noa": d_noa,
}

# Detectors that shell out to a slow sub-command; `--quick` skips them.
SLOW = {"sanity", "missing-history", "elections", "audit", "option-boundary",
        "handoff",
        "t5008", "form-export", "t1135", "carryover", "fx-cash", "amt",
        "filed-lock"}


# -------------------------------------------------------------------- state
class StateFileError(ValueError):
    """checklist.json exists but cannot be read — never treated as empty
    (the next mark would overwrite every recorded mark and note)."""


def load_state(root: Path) -> Dict[str, Any]:
    p = root / STATE_FILE
    if not p.exists() and not p.is_symlink():
        return {"overrides": {}}
    try:
        # A directory or a looping symlink is not "no marks": is_file()
        # read it as absent and the marks were silently lost (A2-0789).
        doc = json.loads(p.read_text(encoding="utf-8-sig"))   # A2-0776
    except (OSError, ValueError) as e:
        # S068-07: silently empty, then overwritten by the next --done.
        why = (f"{e.strerror}" if isinstance(e, OSError) and e.strerror
               else f"{e}")
        raise StateFileError(
            f"{STATE_FILE} cannot be read ({why}) — fix it or restore it "
            f"from git; nothing was written. `taxjson checklist --reset` "
            f"discards every mark.") from e
    if not isinstance(doc, dict) or not isinstance(
            doc.setdefault("overrides", {}), dict):
        raise StateFileError(f"{STATE_FILE} is not a checklist state "
                             f"file (no overrides table) — fix it or "
                             f"`taxjson checklist --reset`.")
    for sid, ov in doc["overrides"].items():
        # A wrong-shape entry ("elections": "x") died later on ov.get
        # in evaluate() with an AttributeError (A2-1393 / A2-1430).
        if not isinstance(ov, dict) or not isinstance(
                ov.get("status"), (str, type(None))) or not isinstance(
                ov.get("note", ""), (str, type(None))):
            raise StateFileError(
                f"{STATE_FILE}: overrides entry {sid!r} is {ov!r}, not a "
                f"{{\"status\": ..., \"note\": ...}} table — fix it, or "
                f"`taxjson checklist --undo {sid}` / `--reset`.")
    return doc


def save_state(root: Path, state: Dict[str, Any], year: int) -> None:
    """Atomic: a reader never sees a half-written checklist.json. A file
    that cannot be written (a directory, a read-only project, a full
    disk) is a StateFileError line — the old file is kept and no .part
    is left behind (A2-0768 / A2-1414)."""
    state["year"] = year
    path = root / STATE_FILE
    text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    if path.is_dir():
        raise StateFileError(f"cannot write {STATE_FILE}: is a directory "
                             f"— nothing was written")
    from taxjson.lib.safe_write import write_atomic
    try:
        write_atomic(path, text, suffix=f".{os.getpid()}.part")
    except OSError as e:
        raise StateFileError(f"cannot write {STATE_FILE}: "
                             f"{e.strerror or e} — nothing was "
                             f"written") from None


def reset_state(root: Path) -> bool:
    """`checklist --reset`: remove checklist.json. True when there was
    one. A directory, or a project where the file cannot be removed, is
    a StateFileError line rather than a traceback (A2-1414)."""
    path = root / STATE_FILE
    if not path.exists() and not path.is_symlink():
        return False
    if path.is_dir():
        raise StateFileError(f"cannot remove {STATE_FILE}: is a directory "
                             f"— remove it by hand")
    try:
        path.unlink()
    except OSError as e:
        raise StateFileError(f"cannot remove {STATE_FILE}: "
                             f"{e.strerror or e}") from None
    return True


class _StateLock:
    """An exclusive lock around a read-modify-write of checklist.json:
    two `checklist --done` at once lost a mark or left the file
    unparseable (A2-1160). A POSIX flock on the project directory itself
    (no lock file to commit or ignore); no lock where fcntl or a
    directory descriptor is unavailable."""

    def __init__(self, root: Path):
        self.root = root
        self.fd = None

    def __enter__(self):
        try:
            import fcntl
            self.fd = os.open(str(self.root), os.O_RDONLY)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except (ImportError, OSError):                  # pragma: no cover
            if self.fd is not None:
                os.close(self.fd)
            self.fd = None
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            import fcntl
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
        return False


def set_override(root: Path, year: int, step: str, mark: Optional[str],
                 note: str = "", today: Optional[date] = None) -> bool:
    """Record (or with mark=None remove) a manual mark. Returns False when
    removing a mark that was not there (nothing changed)."""
    ids = {s[0] for s in STEPS}
    if step not in ids:
        raise KeyError(step)
    with _StateLock(root):
        return _set_override_locked(root, year, step, mark, note, today)


def _set_override_locked(root: Path, year: int, step: str,
                         mark: Optional[str], note: str,
                         today: Optional[date]) -> bool:
    state = load_state(root)
    if state.get("year") not in (None, year):
        # Another year's marks (a copied project, or `year` bumped):
        # evaluate() already ignores them, so a new mark must not
        # re-stamp them as this year's (R1-254).
        state = {"overrides": {}}
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
    us = is_us(ctx.settings.get("country"))
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
                progress(sid, step_meta(sid, ctx.settings.get("country"))[3])
            try:
                r = DETECTORS[sid](ctx)
            except Exception as e:      # a detector must never take the list down
                r = Result(sid, "blocked", f"detector failed: {e}")
        if ov.get("status") in ("done", "skipped"):
            r.override = ov["status"]
            r.note = ov.get("note") or ""
            if r.status in ("attention", "blocked"):
                r.finding = r.detail
        results.append(r)
    return results


def stderr_progress(sid: str, cmd: str) -> None:
    """Default progress line: what is being checked, on stderr so that
    `--json` stdout stays machine-readable. Shown to a person it is a
    step (`==> Checking sanity (taxjson sanity)`, docs/output-style.md,
    The run's console); captured (width 0), the old line."""
    from taxjson.lib import out
    if out.width(sys.stderr) > 0:
        out.show(out.wrap(f"==> Checking {sid} ({cmd})", None, "", "",
                          stream=sys.stderr), sys.stderr)
        sys.stderr.flush()
        return
    print(f"  checking {sid} ({cmd}) ...", file=sys.stderr, flush=True)


def render(results: List[Result], year: int, country: str,
           quick: bool = False, width_: Optional[int] = None) -> str:
    """The checklist in the house layout (docs/output-style.md): a title
    and the counts, then one section per stage; each step is its mark,
    id and title on one line with its detail wrapped under the title,
    one blank line between steps. The last line says how to walk the
    open steps."""
    from taxjson.lib.out import Doc, wrap
    by_stage: Dict[int, List[Result]] = {}
    for r in results:
        stage = next(s[1] for s in STEPS if s[0] == r.id)
        by_stage.setdefault(stage, []).append(r)
    total = len(results)
    done = sum(1 for r in results if r.passed)
    att = sum(1 for r in results if r.effective == "attention")
    man = sum(1 for r in results if r.effective == "manual")
    todo = sum(1 for r in results if r.effective in ("todo", "blocked"))
    d = Doc(f"FILING CHECKLIST — tax year {year} ({country})"
            f"{' — quick' if quick else ''}: {done}/{total} done",
            width_=width_)
    d.para(f"{att} need attention, {man} need your confirmation, "
           f"{todo} to do")
    # The step id column: the longest id, so every title starts at the
    # same column and a detail wraps under it.
    idw = max((len(r.id) for r in results), default=0)
    lead = " " * (2 + 3 + 1 + idw + 2)
    for num, name in STAGES:
        rows = by_stage.get(num)
        if not rows:
            continue
        d.section(f"{num}. {name.upper()}")
        for i, r in enumerate(rows):
            if i:
                d.blank()
            sym = SYMBOL[r.effective]
            title = step_meta(r.id, country)[2]
            for ln in wrap(title, d.w, f"  {sym} {r.id:<{idw}}  ", lead):
                d.line(ln)
            if r.override:
                d.para(f"marked {r.override}"
                       + (f": {r.note}" if r.note else ""), lead)
                if r.finding:
                    d.para(f"the detector still says: {r.finding}", lead)
            elif r.detail:
                d.para(r.detail, lead)
    d.blank()
    d.line("[x] done  [!] needs attention  [ ] to do  [b] blocked  "
           "[-] n/a  [~] skipped")
    d.line("[m] confirm it, then `taxjson checklist --done ID`")
    d.line("Walk the open steps one at a time: `taxjson checklist --walk`")
    return d.text()


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
