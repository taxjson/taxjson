"""`taxjson checklist`: every step from install to filing, in order, each
checked for you.

One ordered list in ten sections (SECTIONS: Set up, Get your files, Build
the books, Fill the gaps, Tidy the config, Check, Results, Through the
year, Share safely, Year end). Each item has its command(s) and a
one-line why (items()). An item is either

- a CHECK (an id in STEPS / DETECTORS): a detector runs the command that
  proves it, or reads the project's files, and reports
    done       the evidence is clean
    attention  the evidence says something is wrong (fix, then re-check)
    todo       the evidence is missing (the step has not been started)
    manual     no evidence can prove it — the user confirms with
               `taxjson checklist --done ID`
    blocked    a prerequisite (usually `taxjson run`) is missing
    n/a        the step does not apply to this project
- or a STEP of the workflow no command can prove (STEP_RULES: install,
  init, the config's layout, the views to read ...), marked from the
  project's files the same way, or `review`: yours to run and read;
  taxjson cannot tell whether you did. A review item never keeps the list
  open.

Overrides live in `checklist.json` at the project root (commit it): an
item the user marks done or skipped keeps that mark until `--undo` or
`--reset`. A mark never hides a detector's finding: a step marked DONE
whose detector later says `attention` counts as attention (shown `[!]`
with the mark and its note beside the finding), so a stale mark cannot
turn the list green. `--skip` is the deliberate "reviewed, accepted"
mark — the finding stays visible beside it. The first item to do (or
needing attention) is the NEXT one, named with its command on the last
line.

Outside a project (no taxjson.toml) the same items are printed as a
step-by-step guide, without marks (render_guide).

Every user-facing `taxjson` command is named by some item or listed in
EXCLUDED with its reason; tests/test_checklist_items.py fails when a new
command is in neither, an item names a command that does not exist, or
two items share an id.

Country: the checks are written for a Canadian return. A US project gets
the US names where an equivalent exists (1099-B for the T5008, Form
8949 / Schedule D for Schedule 3, ...) and `n/a` for the Canada-only
steps; a project with no taxable account gets `n/a` for every step that
only concerns taxable accounts. The other country's commands are left
out of a project's list.

`--json`: schema_version 2 (docs/settings.md, "taxjson checklist
--json"; to_json, guide_json).
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

# The checks. (id, section, title, proves-it command, why); their place
# in the list is items()'s.
STEPS: List[Tuple[str, str, str, str, str]] = [
    ("inputs-frozen", "Get your files", "Each account's broker exports, through January of the next year",
     "taxjson fetch / broker exports",
     "December trades settle in January and option closes after year end change the year."),
    ("export-coverage", "Get your files", "Each broker's exports reach the year end wherever it still holds positions",
     "taxjson run (its export-coverage warnings)",
     "A broker whose exports stop while it holds positions leaves its later sales, option "
     "expiries and income out of the books — nothing else says so."),
    ("sheltered-inputs", "Get your files", "Sheltered accounts' activity present",
     "inputs/<sheltered>/",
     "RRSP/LIRA/TFSA/RESP purchases decide the superficial-loss rule for the taxable accounts."),
    ("crypto-inputs", "Get your files", "Crypto ledgers and trades for the full year",
     "inputs/<crypto>/",
     "Every disposition of a coin, including swaps and fees, is a capital event."),
    ("roc-entered", "Fill the gaps", "Return of capital (T3 box 42) entered before trusting any ACB",
     "ADJUST lines / [[distributions]] in taxjson.toml",
     "Some funds publish ROC factors only after year end; without them the ACB is overstated."),
    ("inputs-committed", "Tidy the config", "Inputs, config, maps and manifests committed",
     "git status",
     "The filed books must be rebuildable years later."),
    ("run-clean", "Build the books", "Full run with zero validation errors and nothing pending",
     "taxjson run",
     "A validation error means a row the engine could not book; a pending election means an account was skipped."),
    ("check-dates", "Check", "Every trade and settlement date is possible for its market",
     "taxjson check-dates",
     "A date on a closed day, or a settlement before the trade, moves a sale to the wrong day's rate or the wrong year."),
    ("sanity", "Check", "Positions tie to the broker holdings",
     "taxjson sanity",
     "The only acceptable differences are trades after the last export."),
    ("missing-history", "Fill the gaps", "No position with missing cost basis affects the year",
     "taxjson find-missing-history",
     "Missing basis distorts the year either way: a sale with no earlier buy in the data "
     "(truncated history) is booked as a short and left out of the year, understating the "
     "proceeds and gain; shares acquired at $0 cost (an undeclared corporate action) "
     "overstate the gain by the missing basis."),
    ("renames", "Fill the gaps", "Every ticker change dated; no undeclared trade in an old ticker after its rename",
     "taxjson renames",
     "A rename carries the position and cost on its date; a later trade in the old ticker is "
     "another security unless ticker.map folds it (`late=fold` / `late=separate`)."),
    ("journals", "Fill the gaps", "Every journal between two listings joined or settled",
     "taxjson journals --pending",
     "A broker journal moves a position from one listing of a security to another; one the books do "
     "not pool leaves a long on one listing and a short on the other, and a sale's cost wrong."),
    ("elections", "Build the books", "No unresolved merger or spin-off election",
     "taxjson elect --pending",
     "A deferred election leaves the account out of the run."),
    ("crypto-sends", "Fill the gaps", "Crypto sends classified (own wallet, gift or payment)",
     "taxjson crypto-sends",
     "A crypto send that left your ownership is a disposition at fair value; only you know which sends did."),
    ("audit", "Check", "Every disposition traced and tied",
     "taxjson audit",
     "The audit walks each sale from the broker row to the reported gain."),
    ("wash-reviewed", "Check", "Every superficial-loss denial reviewed",
     "taxjson wash-sales",
     "A permanently denied loss (registered-account or affiliated-person repurchase) is gone from "
     "your return; make sure each is real (an affiliated person adds it to their own ACB)."),
    ("filing-positions", "Check", "Every filing position against the superficial-loss rule confirmed",
     "taxjson sum (FILING POSITIONS)",
     "An ALLOWLOSS line claims a loss the rule would deny: it is your position, not the rule's "
     "test. Keep its reason and be ready to support it; delete the line to apply the rule."),
    ("option-boundary", "Check", "Year-straddling written options need no prior-year amendment",
     "taxjson option-boundary",
     "Under ITA s.49 an assignment in a later year moves the premium; a filed year may need a T1-ADJ."),
    ("handoff", "Check", "Last year's closing positions carried in exactly once",
     "taxjson handoff",
     "A Dec 31 trade settling in January, a dropped lot, or a correction applied to one year only makes a gain vanish or count twice."),
    ("t5008", "Results", "T5008 slips reconcile to the computed dispositions",
     "taxjson reconcile-slips inputs/slips/*.csv",
     "The CRA matches Schedule 3 proceeds to the T5008s — this step prevents the review letter."),
    ("t5-t3", "Results", "T5 / T3 / NR4 slips agree with the books' income (dividends, box 18, ROC, foreign tax, "
     "interest)",
     "taxjson slip-audit",
     "Trust units report on a T3, often weeks after the T5s; split-share and mutual-fund "
     "corporations report on a T5, where box 18 capital-gains dividends go on line 17400 "
     "(taxjson books them as dividends until [[capital_gains_dividends]] names them). "
     "`taxjson slip-audit` compares each slip box with the books (the slips typed into "
     "inputs/slips/slips.toml or read from CRA My Account's PDFs with `taxjson slip-audit "
     "--import-cra`, IB's dividends reports) and lists the lines that bring the "
     "books to the slips: payments in lieu, trust distributions dated by their record "
     "year, a T3's split (capital gains box 21, return of capital box 42). A difference "
     "you accept is answered per account by `taxjson checklist --done t5-t3`; NR4 slips "
     "are compared by hand."),
    ("foreign-tax", "Results", "Foreign tax withheld taken from the slips (line 40500 / T2209)",
     "T5 box 15/16, T3 box 33/34",
     "The credit is limited to what the slips show, not what the broker rows imply."),
    ("form-export", "Results", "Schedule 3 rows exported and their total equals the report",
     "taxjson form-export",
     "The export is what goes on the return; the .sum is what the engine computed — they must agree."),
    ("t1135", "Results", "T1135 filed when foreign property cost exceeded CAD 100,000",
     "taxjson t1135",
     "ITA 233.3: the test is on cost at any time in the year, not year-end value."),
    ("carryover", "Results", "Net capital losses of other years applied and recorded",
     "taxjson carryover, [carryover] claimed in taxjson.toml",
     "Line 25300; the ledger only knows what was claimed if you write it down — "
     "record the 100% loss applied (line 25300 divided by the inclusion rate)."),
    ("fx-cash", "Results", "FX gain on foreign cash reviewed (ITA s.39(1.1), $200 de minimis)",
     "taxjson fx-cash",
     "Foreign currency is property; the net gain above $200 is a capital gain. The default "
     "ledger is NOT RELIABLE (it reads no conversions, deposits or margin balances); the "
     "opt-in ledger v2 (fx_cash_ledger = \"v2\") is under audit."),
    ("fees", "Results", "Carrying charges (margin interest) for line 22100 taken from the statements",
     "broker statements (`taxjson events` lists the INTEREST rows)",
     "Interest on money borrowed to invest is deductible on line 22100; trade "
     "commissions are not (they are already in the ACB and proceeds). The "
     "account .sum's CASH INTEREST line nets credit against debit interest, "
     "so it is not the interest paid."),
    ("estimate", "Results", "Tax estimate and instalment position checked",
     "taxjson estimate, taxjson instalments",
     "A sanity check on the tax owed and on what was already paid."),
    ("amt", "Results", "Minimum tax (AMT) and its carryover checked",
     "taxjson amt, [estimate] amt_carryover",
     "Minimum tax paid in the 7 preceding years is recovered against regular tax above "
     "the minimum (ITA s.120.2, line 40427), and a year where AMT binds starts a new "
     "carryover; the estimate applies one only when [estimate] amt_carryover (your notice of "
     "assessment / T691) or last year's close-year lock carries it."),
    ("filed-lock", "Year end", "Return filed and the year locked",
     "taxjson close-year",
     "The lock is what check-filed and option-boundary use to detect drift and to word a T1-ADJ."),
    ("lock-committed", "Year end", "The filed/<year>.json lock committed",
     "git status filed/",
     "The lock is the record of what was filed."),
    ("noa", "Year end", "Notice of Assessment compared; net tax owing carried into next year's [instalments]",
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
    "filing-positions": ("Every filing position against the wash-sale rule confirmed",
                         "taxjson sum (FILING POSITIONS)",
                         "An ALLOWLOSS line claims a loss §1091 would disallow: it is your "
                         "position, not the rule's test. Keep its reason and be ready to "
                         "support it; delete the line to apply the rule."),
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
                "`taxjson fx-cash`'s default ledger is NOT RELIABLE and the "
                "opt-in ledger v2 is under audit (the §988(e) "
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
                "wash-reviewed", "filing-positions", "option-boundary",
                "handoff", "t5008", "t5-t3",
                "foreign-tax", "form-export", "t1135", "carryover",
                "fx-cash", "fees", "amt", "filed-lock", "lock-committed"}


def is_us(country: str) -> bool:
    """Strict (lib/country): an unknown or missing country raises."""
    from taxjson.lib.country import is_usa
    return is_usa(country)


def step_meta(sid: str, country: str) -> Tuple[str, str, str, str, str]:
    """(id, section, title, command, why) of a check for this project's
    country."""
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
          "blocked": "[b]", "n/a": "[-]", "skipped": "[~]", "review": "[?]"}
NEXT_MARK = "[>]"
# Every effective status, in the order the counts are shown.
STATUSES = ("done", "skipped", "attention", "todo", "blocked", "manual",
            "review", "n/a")
# Statuses that leave nothing to do: the exit is 0 when every item has
# one. `review` (yours to run and read) never keeps the list open.
PASSED = ("done", "n/a", "skipped", "review")


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
    # The questions asked, one key each (export-coverage: account|broker|
    # end; option-boundary: account|contract|write date): a DONE mark
    # answers the keys it recorded (question_answers) and no others — a
    # later gap or contract is a new question (apply_override).
    answers: List[str] = field(default_factory=list)
    # False for a `todo` nothing can be done about yet (the year is still
    # open, a check --quick skipped): never the NEXT item, still open.
    actionable: bool = True

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
        return self.effective in PASSED


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


# A captured message's program name and label: a detail shows the
# message only (docs/output-style.md: no label behind a program name).
_MSG_PREFIX = re.compile(r"^(?:(?:taxjson|tjs)[\w -]*:\s+)?"
                         r"(?:(?:error|warning|note):\s+)?")


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return _MSG_PREFIX.sub("", lines[-1]) if lines else ""


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
                          f"download the rest of {ctx.year} after it ends",
                          actionable=False)
        return Result("inputs-frozen", "attention",
                      f"{', '.join(ib_short)} — {ctx.year} activity after "
                      f"it is not in the books; download the statement "
                      f"that covers the rest of the year")
    if early:
        if ctx.today <= cutoff:
            return Result("inputs-frozen", "todo",
                          f"year still open — {', '.join(early)}; "
                          f"re-export after {cutoff.isoformat()}",
                          actionable=False)
        return Result("inputs-frozen", "attention",
                      f"{', '.join(early)} was taken before "
                      f"{cutoff.isoformat()} — activity after it is not in "
                      f"the books; re-export the account")
    if latest_d and latest_d >= cutoff:
        return Result("inputs-frozen", "done", f"latest activity {latest[:10]}")
    if ctx.today <= cutoff:
        return Result("inputs-frozen", "todo",
                      f"year still open — latest activity {latest[:10] or '?'}; "
                      f"re-export after {cutoff.isoformat()}",
                      actionable=False)
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
    gaps = [g for g in EC.find_gaps(ctx.root, ctx.cfg, today=ctx.today)
            if not g.info]
    if gaps:
        # A question whatever the end was read from: only the user knows
        # the broker had no later activity (an account not used after an
        # RBC as-of date). A DONE mark answers the gaps it recorded.
        return Result("export-coverage", "attention", EC.detail(gaps),
                      question=True, answers=sorted({g.key for g in gaps}))
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


def d_filing_positions(ctx: Ctx) -> Result:
    """The .tt ALLOWLOSS lines (lib/loss_overrides): n/a without one; each
    position taken is listed for the user to confirm (manual); a line the
    final books did not resolve to one denied sale is attention."""
    from taxjson.lib import loss_overrides as LO
    expected = LO.read_state(ctx.cache)
    if not expected:
        return Result("filing-positions", "n/a",
                      f"no {LO.KEYWORD} line in the accounts' .tt files")
    files: Dict[str, Path] = {}
    for n in sorted({o["account"] for o in expected}):
        f = ctx.cache / f"{n}_gains_wash.json"
        if not f.is_file():
            f = ctx.cache / f"{n}_gains.json"
        if f.is_file():
            files[n] = f
    items = LO.gather(files)
    probs = LO.problems(items, expected)
    if probs:
        return Result("filing-positions", "attention",
                      f"{len(probs)} {LO.KEYWORD} line(s) name no single "
                      f"denied loss — {probs[0]}"
                      + (" ..." if len(probs) > 1 else "")
                      + " (`taxjson run` stops on it)")
    verb = "disallow" if is_us(ctx.settings.get("country")) else "deny"
    shown = []
    for it in items:
        for s in it.get("sales") or []:
            shown.append(f"{s['account']} {s['date']} {s['symbol']} "
                         f"({s['would_disallow']:,.2f} the rule would "
                         f"{verb}; reason \"{it.get('reason')}\")")
    return Result("filing-positions", "manual",
                  f"{len(shown)} filing position(s) taken against the "
                  f"rule: {'; '.join(shown[:3])}"
                  f"{' ...' if len(shown) > 3 else ''} — confirm each "
                  f"(`taxjson sum` lists them under FILING POSITIONS)")


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
        from taxjson.lib.option_boundary import (project_question_rows,
                                                 question_detail)
        # Keyed as the mark records them (project_question_rows names
        # each row's account).
        keys = option_keys(project_question_rows(ctx.root, ctx.cfg,
                                                 today=ctx.today))
        return Result("option-boundary", "attention",
                      question_detail(asked, ctx.year), question=True,
                      answers=keys or option_keys(asked))
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
    # IB's dividends report is a T5/T3 source (`taxjson slip-audit`,
    # lib/ib_dividends), not a T5008 CSV.
    from taxjson.lib.ib_dividends import is_dividends_report
    return sorted((p for p in slips.iterdir()
                   if p.is_file() and not _skipped_input_name(p.name)
                   and p.suffix.lower() == ".csv"
                   and not is_dividends_report(p)),
                  key=lambda p: p.name.lower())


def _unread_slip_files(root: Path) -> List[Path]:
    slips = root / "inputs" / "slips"
    if not slips.is_dir():
        return []
    # slips.toml holds the T5/T3 slips `taxjson slip-audit` reads.
    from taxjson.lib.slip_audit import SLIPS_FILE
    return sorted(p for p in slips.iterdir()
                  if p.is_file() and not _skipped_input_name(p.name)
                  and p.suffix.lower() != ".csv"
                  and p.name.lower() != SLIPS_FILE)


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


def d_t5_t3(ctx: Optional[Ctx]) -> Result:
    """Canada: `taxjson slip-audit` (lib/slip_audit) — done when every
    taxable account with income has a slip and every box agrees within
    the tolerance; a finding is a question answered per account by a
    DONE mark (QUESTION_STEPS). US: compared by hand."""
    if ctx is None or is_us(ctx.settings.get("country")):
        return Result("t5-t3", "manual", "compare the slips with the TAXABLE "
                      "line of `taxjson divs-sum` / `roc-sum`"
                      + ("" if ctx is not None and is_us(
                          ctx.settings.get("country")) else
                         "; NR4 slips by hand"))
    from taxjson.lib import slip_audit as SA
    if not ctx.cache.is_dir():
        return Result("t5-t3", "blocked", "no work/ — run `taxjson run`")
    import contextlib
    import io
    try:
        # In process: the market list's notes ("... is treated as a
        # split-share corporation") are the views' to print, not the
        # checklist's.
        with contextlib.redirect_stderr(io.StringIO()):
            rep = SA.audit(ctx.root, ctx.cfg)
    except Exception as e:                              # noqa: BLE001
        return Result("t5-t3", "blocked", str(e))
    issues = rep.get("issues") or []
    if not rep.get("sources"):
        if not issues:
            return Result("t5-t3", "done", "no taxable income that a T5 or "
                          "T3 reports")
        return Result("t5-t3", "todo",
                      "no T5/T3 slips in inputs/slips/ — read CRA My "
                      "Account's slip PDFs (`taxjson slip-audit "
                      "--import-cra <folder> --write`), type them into "
                      "inputs/slips/slips.toml (`taxjson slip-audit "
                      "--template` prints one) or add IB's dividends "
                      "report (U*.YYYY.dividends.csv); NR4 slips by hand")
    if issues:
        by: Dict[str, List[str]] = {}
        for i in issues:
            by.setdefault(i["account"], []).append(i["text"])
        parts = [f"{a}: {len(t)} finding(s), e.g. {t[0]}"
                 for a, t in sorted(by.items())]
        return Result("t5-t3", "attention",
                      "; ".join(parts) + " — `taxjson slip-audit`",
                      question=True, answers=SA.question_keys(rep))
    n = sum(len(g["slips"]) for a in rep["accounts"] for g in a["groups"])
    return Result("t5-t3", "done", f"{n} slip(s) agree with the books "
                  f"within {rep['tolerance']:.2f}")


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
                      f"foreign bank account, cash) is not counted",
                      actionable=False)
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
    """`taxjson fx-cash --json` (tax-logic CA-FX-07 / US-FX-03). The
    default ledger (v1) is NOT RELIABLE: attention whenever it saw
    foreign cash move (a `--skip` mark is the deliberate accept). The
    opt-in v2: attention with its problems when it refused; when it
    computed, attention until the user marks THAT figure reviewed (a
    DONE mark answers the year and reportable amount it saw — a changed
    figure asks again)."""
    code, out, err = ctx.sub("fx-cash", "--json")
    if code != 0 or not out.strip():
        return Result("fx-cash", "blocked", _last_line(err) or f"exit {code}")
    try:
        doc = json.loads(out)
    except ValueError:
        return Result("fx-cash", "blocked", "unreadable `taxjson fx-cash "
                                            "--json`")
    head = str(doc.get("headline") or "")
    if doc.get("ledger") == "v2":
        if doc.get("status") == "computed":
            key = fx_review_key(doc)
            return Result("fx-cash", "attention",
                          f"{head} — review `taxjson fx-cash` and mark it "
                          f"reviewed: `taxjson checklist --done fx-cash`",
                          question=True, answers=[key] if key else [])
        return Result("fx-cash", "attention", head)
    if not doc.get("active", True) and not doc.get("overdrafts_in_year"):
        return Result("fx-cash", "manual", head)
    return Result("fx-cash", "attention", head)


def fx_review_key(doc: Dict[str, Any]) -> str:
    """The question a DONE mark on fx-cash answers: this year's v2
    figure, as computed."""
    rep = doc.get("reportable")
    if doc.get("ledger") != "v2" or doc.get("status") != "computed" \
            or rep is None:
        return ""
    return f"v2|{doc.get('year')}|{float(rep):.2f}"


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
    "filing-positions": d_filing_positions,
    "option-boundary": d_option_boundary,
    "handoff": d_handoff,
    "t5008": d_t5008,
    "t5-t3": d_t5_t3,
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
    for old, new in ID_ALIASES.items():
        # A mark saved under an item's former id is the item's mark (a
        # mark under the new id wins).
        if old in doc["overrides"]:
            ov = doc["overrides"].pop(old)
            doc["overrides"].setdefault(new, ov)
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
                 note: str = "", today: Optional[date] = None,
                 answers: Optional[List[str]] = None) -> bool:
    """Record (or with mark=None remove) a manual mark. Returns False when
    removing a mark that was not there (nothing changed). `answers`: the
    question keys a DONE mark answers (QUESTION_STEPS, question_answers),
    stored with it."""
    step = canonical_id(step)
    if step not in item_ids():
        raise KeyError(step)
    with _StateLock(root):
        return _set_override_locked(root, year, step, mark, note, today,
                                    answers)


# The steps whose attention is a question a DONE mark answers — for the
# questions asked when it was made (Result.answers).
QUESTION_STEPS = ("export-coverage", "option-boundary", "t5-t3",
                  "fx-cash")


def question_answers(root: Path, cfg: Dict[str, Any], step: str,
                     today: Optional[date] = None) -> Optional[List[str]]:
    """The keys of the questions `step` asks now (Result.answers), which
    a `--done` mark records as answered; None for a step that asks
    none. Reads files only (the books of the last run)."""
    if step == "export-coverage":
        from taxjson.lib import export_coverage as EC
        return sorted({g.key for g in EC.find_gaps(root, cfg, today=today)
                       if not g.info})
    if step == "option-boundary":
        from taxjson.lib import option_boundary as OB
        return option_keys(OB.project_question_rows(root, cfg, today=today))
    if step == "t5-t3":
        from taxjson.lib import slip_audit as SA
        if is_us((cfg.get("settings") or {}).get("country")):
            return None
        import contextlib
        import io
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                return SA.question_keys(SA.audit(root, cfg))
        except Exception:                               # noqa: BLE001
            return []
    if step == "fx-cash":
        # The v2 figure the mark reviews (none for the default ledger:
        # its NOT RELIABLE finding is never answered by a mark).
        from taxjson.bin.taxjson_run import _fx_cash_status
        try:
            st = _fx_cash_status(Path(root), Path(root) / "work")
        except (SystemExit, Exception):                 # noqa: BLE001
            return []
        k = fx_review_key(st)
        return [k] if k else []
    return None


def option_keys(rows: List[Dict[str, Any]]) -> List[str]:
    """The transition question's keys: one per contract written (account,
    contract, write date)."""
    return sorted({f"{r.get('account') or ''}|{r.get('symbol') or ''}|"
                   f"{str(r.get('written') or '')[:10]}" for r in rows})


def unanswered(r: Result, ov: Dict[str, Any]) -> List[str]:
    """The question keys of `r` its DONE mark `ov` did not record (a
    mark with none recorded answers none)."""
    if not r.question or ov.get("status") != "done":
        return []
    done = {str(k) for k in (ov.get("answers") or [])
            if isinstance(k, str)}
    return [k for k in r.answers if k not in done]


def key_text(sid: str, key: str) -> str:
    """A question key as a person reads it."""
    if sid == "export-coverage":
        from taxjson.lib.export_coverage import key_text
        return key_text(key)
    if sid == "t5-t3":
        from taxjson.lib.slip_audit import key_text as _kt
        return _kt(key)
    parts = key.split("|")
    if sid == "fx-cash" and len(parts) == 3:
        return f"the {parts[1]} ledger v2 figure {parts[2]}"
    return f"{parts[1]} written {parts[2]}" if len(parts) == 3 else key


def apply_override(r: Result, ov: Dict[str, Any]) -> Result:
    """Put a manual mark on a detector result: a DONE mark answers the
    question keys it recorded only — a key it did not record leaves the
    step needing attention, named in the detail."""
    if ov.get("status") not in ("done", "skipped"):
        return r
    r.override = ov["status"]
    r.note = ov.get("note") or ""
    new = unanswered(r, ov)
    if new:
        r.question = False      # the mark does not answer these
        shown = ", ".join(key_text(r.id, k) for k in new[:3]) + (
            f" +{len(new) - 3} more" if len(new) > 3 else "")
        r.detail = (f"{r.detail} — not answered by the done mark of "
                    f"{ov.get('date') or '?'}: {shown}")
    if r.status in ("attention", "blocked"):
        r.finding = r.detail
    return r


def _set_override_locked(root: Path, year: int, step: str,
                         mark: Optional[str], note: str,
                         today: Optional[date],
                         answers: Optional[List[str]] = None) -> bool:
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
        if mark == "done" and answers is not None:
            state["overrides"][step]["answers"] = sorted(set(answers))
    save_state(root, state, year)
    return True


# ------------------------------------------------------------------- items
SCHEMA_VERSION = 2
GUIDE = "docs/getting-started.md"
INSTALL = 'bash -c "$(curl -fsSL https://taxjson.com/install.sh)"'

SECTIONS = ("Set up", "Get your files", "Build the books", "Fill the gaps",
            "Tidy the config", "Check", "Results", "Through the year",
            "Share safely", "Year end")

# Commands no item names, each with the reason (tests/test_checklist_items
# reads this): every other command in taxjson_run._COMMAND_GROUPS must be
# named by an item.
EXCLUDED: Dict[str, str] = {
    "deploy": "development machine only: puts a release on this "
              "machine's production copy",
    "promote": "development machine only: moves a release channel",
}


@dataclass(frozen=True)
class Cmd:
    command: str
    note: str = ""
    country: Optional[str] = None       # one country's projects only


@dataclass(frozen=True)
class Item:
    """One line of the list. A check (its id in STEPS) takes its title
    from step_meta (the US wording in a US project) and, when `why` is
    empty, its why too; `section` is then STEPS' own."""
    id: str
    section: str
    title: str
    why: str
    cmds: Tuple[Cmd, ...] = ()
    how: str = ""
    do: str = ""            # the closing line's action; default: cmds[0]
    us_how: Optional[str] = None

    @property
    def check(self) -> bool:
        return self.id in DETECTORS


def _check(sid: str, why: str = "", cmds: Tuple[Cmd, ...] = (),
           how: str = "", do: str = "",
           us_how: Optional[str] = None) -> Item:
    base = next(s for s in STEPS if s[0] == sid)
    return Item(sid, base[1], "", why, cmds, how, do, us_how)


def _spec(year: int, country: Optional[str]) -> List[Item]:
    """Every item, in order, before the country's commands are picked.
    `year` fills the example commands; `country` (None outside a project)
    picks the init example."""
    ctry = country or "canada"
    nxt = year + 1
    return [
        # -------------------------------------------------------- set up
        Item("install", "Set up", "Install or upgrade taxjson",
             "The same line upgrades: the fix for a problem you hit "
             "starts with the newest release.",
             (Cmd(INSTALL, "installs, or upgrades in place"),
              Cmd(INSTALL + " _ --channel beta",
                  "stable (the default), beta, latest or dev"),
              Cmd(INSTALL + " _ --without-fetch",
                  "leave out the taxjson-fetch plugin"),
              Cmd("tjs --version", "what is installed; `tjs channels`: "
                  "where each channel points"),
              Cmd("tjs help", "every command by group; `tjs COMMAND -h` "
                  "explains one"))),
        Item("init", "Set up", "Create the year's project",
             "One folder per tax year: taxjson.toml, a ticker.map and an "
             "inputs/<account>/ folder per account, each with a "
             "README.txt naming the export to download.",
             (Cmd(f"mkdir -p ~/taxes/{year} && cd ~/taxes/{year}",
                  "the year you file"),
              Cmd(f"tjs init --country {ctry} --year {year}",
                  "or --country " + ("usa" if ctry == "canada"
                                     else "canada")))),
        Item("configure", "Set up", "Make taxjson.toml match your accounts",
             "Sheltered accounts count too: a purchase there can deny a "
             "loss in a taxable account. Crypto rows are dated in "
             "local_timezone.",
             (Cmd("tjs migrate", "an older project: moves its old map "
                  "files into ticker.map / taxjson.toml"),),
             how="One [accounts.NAME] section per account you have "
                 "(type = \"taxable\" or \"sheltered\"; crypto = true for "
                 "Coinbase or Kraken), the others deleted; with a crypto "
                 "account, [settings] local_timezone (e.g. "
                 "\"America/Toronto\").",
             do="edit taxjson.toml"),
        # ------------------------------------------------- get your files
        _check("inputs-frozen",
               "A sale's cost comes from its purchase, which may be years "
               "back; December trades settle in January.",
               (Cmd("tjs fetch", "with the taxjson-fetch plugin "
                    "(Questrade, IBKR Flex); `tjs fetch --list` names the "
                    "fetchers"),),
               how="Download all the history each broker gives, through "
                   "January of the next year, not just the tax year, and "
                   "keep a positions report with book cost from the start "
                   f"of that history and from today. Which export per "
                   f"broker: {GUIDE}, step 3, and each folder's "
                   "README.txt.",
               do="download the exports into inputs/<account>/ (or "
                  "`tjs fetch`)"),
        _check("export-coverage",
               cmds=(Cmd("tjs run", "its Warning names a broker whose "
                         "exports stop while it holds positions"),
                     Cmd("tjs checklist --done export-coverage",
                         "the broker had no later activity: answers the "
                         "gaps shown")),
               do="download the later export (or confirm with `tjs "
                  "checklist --done export-coverage`)"),
        _check("sheltered-inputs",
               how="Each RRSP / TFSA / LIRA / RESP account's exports in "
                   "its inputs/<account>/ folder too.",
               us_how="Each IRA / Roth / 401(k) account's exports in its "
                      "inputs/<account>/ folder too.",
               do="download the sheltered accounts' exports into "
                  "inputs/<account>/"),
        _check("crypto-inputs",
               how="The Coinbase / Kraken ledgers and trade files for the "
                   "whole year in the crypto account's inputs/<account>/.",
               do="download the crypto ledgers into inputs/<account>/"),
        # ------------------------------------------------ build the books
        _check("run-clean",
               "Its closing list names what the books show is still "
               "incomplete, each with the command that fixes it.",
               (Cmd("tjs run", "every file read, reports/ written; asks "
                    "about mergers and spin-offs"),
                Cmd("tjs run --no-input", "never asks: an open election "
                    "exits 3 (next step)"))),
        _check("elections",
               "An account with an open election is left out of the run; "
               "a spin-off valued at $0 books no income and a $0 cost.",
               (Cmd("tjs elect --pending", "what is open, with ready "
                    "--set lines"),
                Cmd("tjs elect ACCOUNT --set ID=ELECTION", "record one"),
                Cmd("tjs spinoffs", "each spin-off's election, value and "
                    "cost; `tjs splits`: splits and consolidations"))),
        # -------------------------------------------------- fill the gaps
        _check("missing-history",
               "Exports rarely reach back to every purchase: a sale with "
               "no purchase is left out of the year, and a $0 cost "
               "overstates the gain.",
               (Cmd("tjs find-missing-history", "sales with no purchase, "
                    "$0-cost shares, and the fixes in order"),
                Cmd("tjs opening ACCOUNT FILE", "an opening balance from "
                    "a positions report at the start of your history"),
                Cmd("tjs find-missing-history --write-purchases",
                    "drafts IB purchases from the broker's cost, as .tt "
                    "lines to review"),
                Cmd("tjs find-missing-history --write-missing-history",
                    "only for what cannot be recovered"),
                Cmd("tjs find-missing-history --write-missing-history "
                    "--outside-year", "positions short in an earlier "
                    "year that do not touch this one"),
                Cmd("tjs list --negative", "the same positions in the "
                    "holdings view"))),
        Item("transfers", "Fill the gaps",
             "Shares moved in from another broker",
             "Their cost is the original purchase, not the day they "
             "arrived.",
             (Cmd("tjs transfers", "the custody moves the books leave "
                  "out"),),
             how="A transfer-in with no cost is kept out of the books: "
                 f"enter its purchase as a .tt line ({GUIDE}, 5c)."),
        Item("ticker-map", "Fill the gaps",
             "Symbol spellings and listings (ticker.map)",
             "Two spellings or listings of one security must be one "
             "position for its cost to be right.",
             (Cmd("tjs ticker-map --suggest", "the lines the last run "
                  "suggested, each with its reason, and the listing pairs "
                  "to verify (one security or two)"),
              Cmd("tjs ticker-map --suggest --write", "adds them, one by "
                  "one (--all: every line the evidence names; a pair to "
                  "verify is asked on a terminal)"))),
        _check("journals",
               "A journal the books do not pool leaves a long on one "
               "listing and a short on the other.",
               (Cmd("tjs journals --pending", "the journals not joined or "
                    "settled"),)),
        _check("renames",
               "A rename carries the position and its cost on its date; a "
               "later trade in the old ticker is another security unless "
               "ticker.map says otherwise.",
               (Cmd("tjs renames --pending", "the renames to declare or "
                    "resolve"),
                Cmd("tjs renames", "every rename with its date and "
                    "source"))),
        _check("crypto-sends",
               cmds=(Cmd("tjs crypto-sends", "the sends that did not "
                         "arrive in another of your accounts"),
                     Cmd("tjs crypto-sends ACCOUNT --set ID=self",
                         "or payment, or gift", "canada"),
                     Cmd("tjs crypto-sends ACCOUNT --set ID=self",
                         "or payment", "usa"))),
        Item("tt-lines", "Fill the gaps",
             "Anything no export holds: .tt lines",
             "The run reads a .tt file like a broker file; `tjs run "
             "--strict` refuses a line whose total is not quantity x "
             "price +/- fee.",
             (Cmd("tjs events", "the books' rows in the same .tt format, "
                  "to copy from"),),
             how="A purchase from before your downloads, a dividend from "
                 "a slip (Webull), a return of capital (ADJUST): a line "
                 "in a .tt file in inputs/<account>/ (README, "
                 "\"Importing manual cost basis\"). Non-cash fund "
                 "distributions go in [[distributions]] in "
                 "taxjson.toml."),
        _check("roc-entered",
               cmds=(Cmd("tjs roc-sum", "the year's return of capital "
                         "per security, as the books hold it"),),
               how="An ADJUST line in a .tt file, or [[distributions]] in "
                   "taxjson.toml, per fund that published one (README, "
                   "\"Non-cash distributions\"); then confirm with `tjs "
                   "checklist --done roc-entered`."),
        # ------------------------------------------------ tidy the config
        Item("format", "Tidy the config", "Lay out the config files",
             "The same layout every year, so a diff of two years' "
             "projects shows only what changed; no number changes.",
             (Cmd("tjs format", "taxjson.toml as the template lays it "
                  "out: shows the diff"),
              Cmd("tjs format --write", "applies it (keeps "
                  "taxjson.toml.bak)"),
              Cmd("tjs format-map --write", "ticker.map in keyword "
                  "groups; dated events move to .tt lines (`tjs "
                  "format-map` shows the diff)"))),
        _check("inputs-committed",
               cmds=(Cmd("git status", "what is not committed yet"),),
               do="commit inputs/, taxjson.toml and ticker.map"),
        # ---------------------------------------------------------- check
        Item("tips", "Check", "Tax-efficiency tips for next year",
             "Advice on where you hold what; it changes no number of "
             "this year.",
             (Cmd("tjs tips", "e.g. a dividend payer held where its "
                  "withholding is lost"),)),
        _check("sanity",
               "Missing history in a position you still hold shows only "
               "here: the books cannot see a purchase that is not in the "
               "files.",
               (Cmd("tjs sanity", "against the holdings files named in "
                    "taxjson.toml (holdings = [...])"),
                Cmd("tjs sanity ACCOUNT=FILE", "against one positions "
                    "report: an IB statement, an RBC Holdings Export or a "
                    "[[holding]] .toml"))),
        Item("edge-cases", "Check", "Trades on a boundary",
             "A trade a day either side of Dec 31, or of a loss's 30-day "
             "window, changes the year or the denial.",
             (Cmd("tjs edge-cases", "each one, with where it lands and "
                  "why"),)),
        _check("option-boundary",
               "A contract written before option_grant_timing_since and "
               "closed this year is taxed at the close: right only if the "
               "year it was written did not report its premium (ITA "
               "s.49(1)), which only you know.",
               (Cmd("tjs option-boundary", "each contract, where its "
                    "premium lands and what to do", "canada"),
                Cmd("tjs list", "the option positions: one past its "
                    "expiry needs its expiry, exercise or assignment row",
                    "usa")),
               how="If last year's return reported the premiums when "
                   "written, set option_grant_timing_since = <that year> "
                   "in [settings]; if it did not, confirm with `tjs "
                   "checklist --done option-boundary`.",
               us_how=""),
        _check("wash-reviewed",
               "A loss denied by a registered-account (US: IRA) "
               "repurchase is gone for good: make sure each is real.",
               (Cmd("tjs wash-sales", "each loss denied this year, and "
                    "why"),
                Cmd("tjs wash-sales --explain", "with the matching "
                    "purchases"))),
        _check("filing-positions",
               cmds=(Cmd("tjs sum", "FILING POSITIONS lists each "
                         "ALLOWLOSS line with the loss it claims"),)),
        _check("check-dates",
               "A date on a closed day, or a settlement before the trade, "
               "moves a sale to the wrong rate or year.",
               (Cmd("tjs check-dates", "every date against its market's "
                    "calendar"),)),
        _check("audit",
               "The audit walks each sale from the broker row to the "
               "reported gain.",
               (Cmd("tjs audit", "every disposition traced and tied out; "
                    "`tjs audit SYMBOL`: one security"),)),
        _check("handoff",
               "A Dec 31 trade settling in January, or a dropped lot, "
               "makes a gain vanish or count twice.",
               (Cmd("tjs handoff", "this year's opening against last "
                    "year's close-year record"),),
               how="Set [settings] prior_year_record to last year's "
                   "filed/<year>.json (your first year: confirm with `tjs "
                   "checklist --done handoff`)."),
        # -------------------------------------------------------- results
        Item("sum", "Results", "Read the year's numbers",
             "`Done.` does not mean right: read them once the gaps are "
             "filled.",
             (Cmd("tjs sum", "the year's gains, ending with the lines for "
                  "your return"),
              Cmd("tjs list", "what the books hold, with book cost (`tjs "
                  "list margin 2026-04-28`: as of a date); `tjs shares`: "
                  "per symbol across accounts"))),
        Item("explore", "Results", "Look closer at any number",
             "Every figure traces back to a broker row and a stated "
             "rule.",
             (Cmd("tjs audit SYMBOL", "each gain traced to its broker "
                  "row"),
              Cmd("tjs tax-logic", "every rule taxjson applies, one line "
                  "each"),
              Cmd("tjs gains", "rows; also `tjs trades`, `tjs events`, "
                  "`tjs divs`, `tjs dil`, `tjs roc`, `tjs fees`, "
                  "`tjs leaps`"),
              Cmd("tjs divs-sum", "totals; also `tjs dil-sum`, `tjs "
                  "roc-sum`, `tjs fees-sum`, `tjs leaps-sum`, `tjs "
                  "ccd-sum`, `tjs trades-sum`, `tjs winners`, `tjs "
                  "stats`"))),
        _check("t5008",
               "The tax authority matches your return to the slips: a "
               "difference explained now is not a letter later.",
               (Cmd("tjs reconcile-slips inputs/slips/*.csv", "the T5008 "
                    "CSVs against the dispositions", "canada"),
                Cmd("tjs reconcile-slips inputs/slips/*.csv", "the 1099-B "
                    "/ 1099-DA CSVs against the dispositions", "usa"))),
        _check("t5-t3",
               "The slip is what the return reports: a box-18 "
               "capital-gains dividend, a T3's return of capital or a "
               "payment the exports missed shows up here, with the lines "
               "that fix the books.",
               (Cmd("tjs slip-audit", "the slips typed into inputs/slips/"
                    "slips.toml (`tjs slip-audit --template`) and IB's "
                    "dividends reports, box by box", "canada"),
                Cmd("tjs divs-sum", "with `tjs roc-sum`: compare the "
                    "TAXABLE lines with the 1099-DIV by hand", "usa")),
               how="Type each T5 / T3 into inputs/slips/slips.toml (or "
                   "drop IB's U*.YYYY.dividends.csv there), then run it.",
               us_how=""),
        _check("foreign-tax",
               how="Line 40500 / T2209 from the slips: T5 box 15/16, T3 "
                   "box 33/34.",
               us_how="Form 1116 from the 1099-DIV, box 7.",
               do="take the foreign tax from the slips, then `tjs "
                  "checklist --done foreign-tax`"),
        _check("form-export",
               "The export is what goes on the return; its totals must "
               "equal `tjs sum`.",
               (Cmd("tjs form-export", "the Schedule 3 rows", "canada"),
                Cmd("tjs form-export", "the Form 8949 rows and Schedule D",
                    "usa"),
                Cmd("tjs form-export --form txf --out gains.txf",
                    "a TurboTax import", "usa"))),
        _check("t1135",
               cmds=(Cmd("tjs t1135", "foreign property test and tables",
                         "canada"),)),
        _check("carryover",
               cmds=(Cmd("tjs carryover", "capital-loss carryforward "
                         "across years"),)),
        _check("fx-cash",
               cmds=(Cmd("tjs fx-cash", "currency gains on foreign "
                         "cash"),)),
        _check("fees",
               cmds=(Cmd("tjs events", "lists the INTEREST rows"),),
               do="take the interest paid from the statements, then `tjs "
                  "checklist --done fees`"),
        _check("estimate",
               "A check on what you owe before you file.",
               (Cmd("tjs estimate", "tax on the year's investment income "
                    "([estimate] other_income in taxjson.toml)"),
                Cmd("tjs instalments", "instalments due, paid and "
                    "interest", "canada"))),
        _check("amt",
               cmds=(Cmd("tjs amt", "minimum tax, line by line",
                         "canada"),)),
        # ----------------------------------------------- through the year
        Item("trading", "Through the year", "Before you trade",
             "The loss rules look 30 days both ways: check before the "
             "trade, not after.",
             (Cmd("tjs wash-radar", "the loss windows open today"),
              Cmd("tjs buy-check SYMBOL", "would buying today cancel a "
                  "recent loss?"),
              Cmd("tjs sell-check SYMBOL", "would selling at a loss "
                  "today keep the loss?"),
              Cmd("tjs harvest", "unrealized gains and losses at current "
                  "prices (looks them up)"),
              Cmd("tjs watch", "what changed since the last watch "
                  "(cron)"))),
        # --------------------------------------------------- share safely
        Item("redact", "Share safely", "Share a sample, never the exports",
             "For a bug report or a broker taxjson does not read yet; "
             "the redactor works from patterns, so read the copy first.",
             (Cmd("tjs redact", "copies inputs/ to inputs_redact/ with "
                  "account numbers and names replaced"),)),
        # ------------------------------------------------------- year end
        _check("filed-lock",
               "The lock is what the drift check and next year's handoff "
               "compare with.",
               (Cmd("tjs close-year", "after filing: filed/<year>.json "
                    "(commit it)"),
                Cmd("tjs check-filed", "later: recomputes filed years and "
                    "shows any drift"))),
        _check("lock-committed",
               cmds=(Cmd("git status filed/", "the lock not committed "
                         "yet"),),
               do="commit filed/<year>.json"),
        Item("next-year", "Year end", "Start next year's project",
             "Next year's cost comes from this year's books.",
             (Cmd(f"tjs init --country {ctry} --year {nxt} ../{nxt}",
                  "a folder beside this one"),),
             how=f"Copy inputs/ and ticker.map into the new folder, set "
                 f"[settings] prior_year_record = "
                 f"\"../{year}/filed/{year}.json\" in its taxjson.toml, "
                 f"then `tjs handoff` there."),
        _check("noa",
               how="Compare the Notice of Assessment with the return; "
                   "carry its net tax owing into next year's "
                   "[instalments] prior_year_net_tax.",
               us_how="",
               do="compare the NOA, then `tjs checklist --done noa`"),
    ]


def items(year: int, country: Optional[str] = None) -> List[Item]:
    """The list, in order, for a project of `country` (None outside a
    project: every command, the Canadian titles). In a project the other
    country's commands are left out, and a check gets its country's
    title, why and how."""
    from dataclasses import replace
    out = []
    for it in _spec(year, country):
        if it.check:
            _id, _sec, title, _cmd, why = step_meta(it.id, country
                                                    or "canada")
            us = country is not None and is_us(country)
            # A US project: the check's US why (or its n/a reason).
            it = replace(
                it, title=title,
                why=why if (us and it.id in US_STEPS) or not it.why
                else it.why,
                how=(it.us_how if us and it.us_how is not None
                     else it.how))
        if country:
            it = replace(it, cmds=tuple(c for c in it.cmds
                                        if c.country in (None, country)))
        out.append(it)
    return out


def item_ids() -> List[str]:
    """Every item id, in order (the same for both countries)."""
    return [it.id for it in _spec(2025, None)]


# An item's former id: still accepted in checklist.json marks and on the
# command line (--done/--skip/--undo/--only), read as the new id.
ID_ALIASES: Dict[str, str] = {"scan": "tips"}


def canonical_id(sid: Optional[str]) -> Optional[str]:
    """`sid` with a former id (ID_ALIASES) read as its item's id now."""
    return ID_ALIASES.get(sid, sid) if isinstance(sid, str) else sid


def item_meta(sid: str, country: Optional[str]) -> Tuple[str, str, str,
                                                         str, str]:
    """(id, section, title, command, why) of any item: a check's
    step_meta, a step's own text (its first command)."""
    if sid in DETECTORS:
        return step_meta(sid, country or "canada")
    it = next(i for i in items(date.today().year - 1, country)
              if i.id == sid)
    return (it.id, it.section, it.title,
            it.cmds[0].command if it.cmds else "", it.why)


_TJS = re.compile(r"(?<![\w-])tjs ([a-z][a-z0-9-]*)")


def named_commands(year: int = 2025) -> Dict[str, List[str]]:
    """{command: [item ids naming it]} over every command, note, how and
    why text of the list (`tjs NAME`), both countries."""
    out: Dict[str, List[str]] = {}
    for it in _spec(year, None):
        texts = [it.why, it.how, it.do, it.us_how or ""] \
            + [t for c in it.cmds for t in (c.command, c.note)]
        for t in texts:
            for m in _TJS.finditer(t):
                ids = out.setdefault(m.group(1), [])
                if it.id not in ids:
                    ids.append(it.id)
    return out


# ------------------------------------------------------------ step rules
def _version() -> str:
    try:
        from importlib.metadata import version
        return version("taxjson")
    except Exception:                                   # noqa: BLE001
        return "unknown"


def _load_json(p: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


class _Facts:
    """What several items read, read once: whether `taxjson run` wrote
    reports, and its closing counts (reports/run_summary.json,
    lib/first_run) for this year."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        rep = ctx.reports
        self.ran = rep.is_dir() and any(rep.glob("*.sum"))
        self._summ: Any = False

    @property
    def summ(self) -> Optional[Dict[str, Any]]:
        if self._summ is False:
            from taxjson.lib import first_run as fr
            doc = (_load_json(self.ctx.reports / fr.SUMMARY_FILE)
                   if self.ran else None)
            if doc is not None and str(doc.get("year")) != str(self.ctx.year):
                doc = None
            self._summ = doc
        return self._summ

    def wait(self, sid: str) -> Result:
        """A step that reads the run's summary, before there is one."""
        from taxjson.lib import first_run as fr
        if not self.ran:
            return Result(sid, "todo", "after `tjs run`")
        return Result(sid, "todo", f"no reports/{fr.SUMMARY_FILE} for "
                      f"{self.ctx.year} — re-run `tjs run`")


def s_install(ctx: Ctx, f: _Facts) -> Result:
    return Result("install", "done", f"taxjson {_version()}")


def s_init(ctx: Ctx, f: _Facts) -> Result:
    c = ctx.settings.get("country")
    return Result("init", "done", f"taxjson.toml for {ctx.year}"
                  + (f" ({c})" if c else ""))


def s_configure(ctx: Ctx, f: _Facts) -> Result:
    from taxjson.lib.migrate import legacy_files, legacy_message
    issues = []
    legacy = legacy_files(ctx.root)
    if legacy:
        issues.append(legacy_message(legacy))
    accounts = {n: a for n, a in ctx.accounts.items() if isinstance(a, dict)}
    if not accounts:
        issues.append("no [accounts.NAME] section")
    crypto = sorted(n for n, a in accounts.items() if a.get("crypto") is True)
    if crypto and not ctx.settings.get("local_timezone"):
        issues.append(f"[accounts.{crypto[0]}] is a crypto account but "
                      f"[settings] has no local_timezone — set it, or "
                      f"delete the section if you have no crypto")
    if issues:
        return Result("configure", "attention", "; ".join(issues))
    return Result("configure", "done", ", ".join(
        f"{n} ({'crypto' if a.get('crypto') else a.get('type', '?')})"
        for n, a in accounts.items()))


def s_transfers(ctx: Ctx, f: _Facts) -> Result:
    from taxjson.lib import first_run as fr
    summ = f.summ
    if summ is None:
        return f.wait("transfers")
    tn = summ.get("transfer_in_no_cost") or []
    tb = summ.get("transfer_in_book_value") or []
    if tn:
        return Result("transfers", "attention", f"{len(tn)} transfer-in(s) "
                      f"kept out with no cost: {fr._names(tn)}")
    if tb:
        return Result("transfers", "review", f"{len(tb)} transfer-in(s) at "
                      f"the broker's book value: {fr._names(tb)} — is it "
                      f"your cost?")
    return Result("transfers", "done", "no transfer-in without its cost")


def s_ticker_map(ctx: Ctx, f: _Facts) -> Result:
    if not (f.ran and ctx.cache.is_dir()):
        return Result("ticker-map", "todo", "after `tjs run`")
    from taxjson.lib import ticker_map_suggest as TS
    offer, _skipped = TS.pending(ctx.root)
    # A listing pair the map does not answer (MAP-GAP) is a question
    # only the user can answer: one security (TOBASE) or two (DISTINCT).
    verify, _unread = TS.verify(ctx.root, offer)
    if verify:
        return Result("ticker-map", "attention",
                      f"{len(verify)} listing pair(s) to verify — one "
                      f"security (TOBASE) or two (DISTINCT): "
                      + ", ".join(f"{s.symbols[0]}/{s.symbols[1]}"
                                  for s in verify[:3])
                      + (" ..." if len(verify) > 3 else "")
                      + (f"; {len(offer)} more line(s) the last run "
                         f"suggested" if offer else ""))
    if offer:
        return Result("ticker-map", "todo", f"{len(offer)} line(s) the "
                      f"last run suggested: decide each")
    return Result("ticker-map", "done", "nothing suggested")


def s_tt_lines(ctx: Ctx, f: _Facts) -> Result:
    tts = sorted(p for n in ctx.accounts
                 for p in _data_files(ctx.root / "inputs" / n)
                 if p.suffix.lower() == ".tt")
    return Result("tt-lines", "review",
                  f"{len(tts)} .tt file(s) in inputs/" if tts else "")


def s_format(ctx: Ctx, f: _Facts) -> Result:
    from taxjson.lib.config_template import format_config
    from taxjson.lib.ticker_map_format import format_map
    loose = []
    try:
        text = (ctx.root / "taxjson.toml").read_bytes().decode("utf-8-sig")
        if format_config(text).changed:
            loose.append("taxjson.toml")
    except Exception:                                   # noqa: BLE001
        loose.append("taxjson.toml")
    tm = ctx.root / "ticker.map"
    if tm.is_file():
        try:
            res = format_map(tm.read_bytes().decode("utf-8-sig"),
                             migrate=True)
        except Exception as e:                          # noqa: BLE001
            return Result("format", "attention", f"ticker.map: {e}")
        if res.problems:
            return Result("format", "attention",
                          f"{len(res.problems)} ticker.map problem(s) `tjs "
                          f"run` refuses — `tjs format-map` names them")
        if res.changed or res.migration_pending:
            loose.append("ticker.map")
    if loose:
        return Result("format", "review", " and ".join(loose)
                      + " not in the template layout")
    return Result("format", "done", "taxjson.toml and ticker.map laid out")


def _review(sid: str) -> Callable[[Ctx, _Facts], Result]:
    def rule(ctx: Ctx, f: _Facts) -> Result:
        return Result(sid, "review")
    return rule


def s_next_year(ctx: Ctx, f: _Facts) -> Result:
    if (ctx.root / "filed" / f"{ctx.year}.json").is_file():
        return Result("next-year", "review", f"start {ctx.year + 1}")
    return Result("next-year", "review", "after you file")


# The items no detector proves: each marked from the project's files.
STEP_RULES: Dict[str, Callable[[Ctx, _Facts], Result]] = {
    "install": s_install,
    "init": s_init,
    "configure": s_configure,
    "transfers": s_transfers,
    "ticker-map": s_ticker_map,
    "tt-lines": s_tt_lines,
    "format": s_format,
    "tips": _review("tips"),
    "edge-cases": _review("edge-cases"),
    "sum": _review("sum"),
    "explore": _review("explore"),
    "trading": _review("trading"),
    "redact": _review("redact"),
    "next-year": s_next_year,
}


def _before_run(sid: str, ctx: Ctx, f: _Facts) -> Optional[Result]:
    """A check's state before there is anything for its detector to
    read: no export at all, no `taxjson run` yet. None: run it."""
    if sid == "inputs-frozen":
        names = list(ctx.accounts)
        if names and not any(_data_files(ctx.root / "inputs" / n)
                             for n in names):
            return Result(sid, "todo", "no broker exports yet in "
                          + ", ".join(f"inputs/{n}/" for n in names))
    if sid == "run-clean" and not f.ran:
        return Result(sid, "todo", "no reports/ yet")
    return None


# ----------------------------------------------------------------- evaluate
def evaluate(ctx: Ctx, only: Optional[List[str]] = None,
             quick: bool = False,
             progress: Optional[Callable[[str, str], None]] = None) -> List[Result]:
    """Every item's result, in the list's order (all, or `only` these
    ids): a check's detector, a step's rule, each with its checklist.json
    mark applied. `progress(id, command)` is called before each slow
    detector so a caller can say what it is waiting on — the audit alone
    can take a minute on a big book."""
    state = load_state(ctx.root)
    overrides = state.get("overrides") or {}
    if state.get("year") not in (None, ctx.year) and overrides:
        # Marks from another year's project copied along — not this year's.
        overrides = {}
    country = ctx.settings.get("country")
    us = is_us(country)
    has_taxable = any(a.get("type") == "taxable" for a in ctx.accounts.values())
    facts = _Facts(ctx)
    results: List[Result] = []
    for sid in item_ids():
        if only and sid not in only:
            continue
        ov = overrides.get(sid) or {}
        if sid in STEP_RULES:
            try:
                r = STEP_RULES[sid](ctx, facts)
            except Exception as e:      # an item must never take the list down
                r = Result(sid, "blocked", f"could not check: {e}")
            apply_override(r, ov)
            results.append(r)
            continue
        if us and isinstance(US_STEPS.get(sid), str):
            results.append(Result(sid, "n/a", US_STEPS[sid]))
            continue
        if not has_taxable and sid in TAXABLE_ONLY:
            results.append(Result(sid, "n/a", "no taxable account — nothing to report"))
            continue
        r = _before_run(sid, ctx, facts)
        if r is None and quick and sid in SLOW:
            r = Result(sid, "todo", "skipped by --quick (run without it to check)",
                       actionable=False)
        if r is None:
            if progress and sid in SLOW:
                progress(sid, step_meta(sid, country)[3])
            try:
                r = DETECTORS[sid](ctx)
            except Exception as e:      # a detector must never take the list down
                r = Result(sid, "blocked", f"detector failed: {e}")
        apply_override(r, ov)
        results.append(r)
    return results


def next_result(results: List[Result]) -> Optional[Result]:
    """The NEXT item: the first one to do, needing attention or blocked
    that something can be done about now; else the first one to confirm
    (manual); None when nothing is left."""
    for r in results:
        if r.effective in ("attention", "todo", "blocked") and r.actionable:
            return r
    return next((r for r in results if r.effective == "manual"), None)


def _do(it: Item, r: Optional[Result] = None) -> str:
    """What the closing line tells the user to do for item `it`."""
    if r is not None and r.effective == "manual":
        return f"confirm it, then `tjs checklist --done {it.id}`"
    if it.do:
        return it.do
    return f"`{it.cmds[0].command}`" if it.cmds else it.title


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




# ------------------------------------------------------------------ output
def _cmd_lines(it: Item, w: int, lead: str, country: Optional[str]
               ) -> List[str]:
    """Each command on its own line, never wrapped, its note after
    `  # ` when it fits, else wrapped on the lines under it."""
    from taxjson.lib.out import printable, wrap

    def shown(c: Cmd) -> str:
        # Outside a project a one-country command says whose it is, as
        # the help page marks it.
        if c.country and not country:
            who = "Canada" if c.country == "canada" else "USA"
            return f"({who}) {c.note}"
        return c.note
    col = max((len(c.command) for c in it.cmds if len(c.command) <= 44),
              default=0)
    out: List[str] = []
    for c in it.cmds:
        note = shown(c)
        cmd = printable(c.command) if w > 0 else c.command
        line = f"{lead}{cmd.ljust(col)}  # {note}" if note else lead + cmd
        if not note or w <= 0 or len(line) <= w:
            out.append(line.rstrip())
            continue
        out.append(lead + cmd)
        out.extend(wrap(f"# {note}", w, lead + "  ", lead + "    "))
    return out


def counts(results: List[Result]) -> Dict[str, int]:
    """{effective status: how many items}, every status in STATUSES."""
    return {s: sum(1 for r in results if r.effective == s) for s in STATUSES}


def render(results: List[Result], year: int, country: str,
           quick: bool = False, width_: Optional[int] = None,
           show_all: bool = False) -> str:
    """The list in the house layout (docs/output-style.md): a title and
    the counts, then one section per SECTIONS entry; each item is its
    mark, number, id and title on one line, its state wrapped under the
    title, then — while it is open — its commands; the NEXT item and one
    needing attention also say how and why (`show_all`: every item). The
    legend, then the last line: the next item and what to do."""
    from taxjson.lib.out import Doc, wrap
    its = {it.id: it for it in items(year, country)}
    num = {sid: i + 1 for i, sid in enumerate(its)}
    c = counts(results)
    total = len(results) - c["n/a"]
    done = c["done"] + c["skipped"]
    d = Doc(f"CHECKLIST — tax year {year} ({country})"
            f"{' — quick' if quick else ''}: {done}/{total} done",
            width_=width_)
    d.para(f"{c['attention']} need attention, {c['todo']} to do, "
           f"{c['blocked']} blocked, {c['manual']} to confirm, "
           f"{c['review']} to run and read yourself, {c['n/a']} n/a")
    nxt = next_result(results)
    idw = max((len(r.id) for r in results), default=0)
    by_sec: Dict[str, List[Result]] = {}
    for r in results:
        by_sec.setdefault(its[r.id].section, []).append(r)
    for k, sec in enumerate(SECTIONS, 1):
        rows = by_sec.get(sec)
        if not rows:
            continue
        d.section(f"{k}. {sec.upper()}")
        for i, r in enumerate(rows):
            if i:
                d.blank()
            it = its[r.id]
            eff = r.effective
            mark = NEXT_MARK if nxt is not None and r.id == nxt.id \
                else SYMBOL[eff]
            head = f"  {mark} {num[r.id]:>2}. {r.id:<{idw}}  "
            lead = " " * len(head)
            for ln in wrap(it.title, d.w, head, lead):
                d.line(ln)
            if r.override:
                d.para(f"marked {r.override}"
                       + (f": {r.note}" if r.note else ""), lead)
                if r.finding:
                    d.para(f"the detector still says: {r.finding}", lead)
            elif r.detail:
                d.para(r.detail, lead)
            # A done, skipped or n/a item is its state line; an open one
            # adds its commands; the next one and one that needs
            # attention also say how and why (--all: every item).
            full = (show_all or (nxt is not None and r.id == nxt.id)
                    or eff == "attention")
            if full and it.how:
                d.para(it.how, lead)
            if full or eff in ("todo", "blocked", "manual", "review"):
                for ln in _cmd_lines(it, d.w, lead, country):
                    d.line(ln)
            if full and it.why:
                d.para(f"Why: {it.why}", lead)
    d.blank()
    d.line("[x] done  [!] needs attention  [ ] to do  [b] blocked  "
           "[-] n/a  [~] skipped  [>] next")
    d.para("[m] confirm it, then `tjs checklist --done ID`; [?] yours to "
           "run and read (`--done ID` ticks it off)")
    d.para("One at a time: `tjs checklist --walk`; every item's commands "
           "and why: `tjs checklist --all`")
    if nxt is not None:
        d.para(f"Next (step {num[nxt.id]}, {nxt.id}): "
               f"{_do(its[nxt.id], nxt)}")
    elif all(r.passed for r in results):
        d.para("Nothing left to do now: the [?] items are yours to run "
               "and read.")
    elif quick:
        d.para("Nothing to do now: run without --quick to check the rest.")
    else:
        d.para("Nothing to do until the year ends: re-check then.")
    return d.text()


def render_guide(year: Optional[int] = None,
                 width_: Optional[int] = None) -> str:
    """Outside a project: every item as a numbered step with its
    commands, how and why — no marks."""
    from taxjson.lib.out import Doc, wrap
    year = year or date.today().year - 1
    its = items(year, None)
    d = Doc("CHECKLIST — from install to filing, step by step",
            width_=width_)
    d.para("Each step: the command(s) to type and why. `tjs` is the "
           f"short name of `taxjson`. Worked examples: {GUIDE}.")
    for k, sec in enumerate(SECTIONS, 1):
        d.section(f"{k}. {sec.upper()}")
        first = True
        for n, it in enumerate(its, 1):
            if it.section != sec:
                continue
            if not first:
                d.blank()
            first = False
            head = f"  {n:>2}. "
            lead = " " * len(head)
            for ln in wrap(it.title, d.w, head, lead):
                d.line(ln)
            if it.how:
                d.para(it.how, lead)
            for ln in _cmd_lines(it, d.w, lead, None):
                d.line(ln)
            if it.why:
                d.para(f"Why: {it.why}", lead)
    d.blank()
    d.para("In a project folder (or `tjs -C DIR checklist`) each step is "
           "checked and marked done or not, and the next one named.")
    return d.text()


def _item_json(n: int, it: Item, r: Optional[Result], is_next: bool,
               country: Optional[str]) -> Dict[str, Any]:
    command = (step_meta(it.id, country or "canada")[3] if it.check
               else (it.cmds[0].command if it.cmds else ""))
    return {
        "step": n, "id": it.id, "section": it.section, "title": it.title,
        "check": it.check, "command": command,
        "commands": [{"command": c.command, "note": c.note,
                      "country": c.country} for c in it.cmds],
        "how": it.how, "why": it.why,
        "status": r.status if r else None,
        "effective": r.effective if r else None,
        "detail": r.detail if r else "",
        "override": r.override if r else None,
        "note": r.note if r else "",
        "finding": r.finding if r else "",
        "next": is_next,
    }


def to_json(results: List[Result], year: int, country: str) -> Dict[str, Any]:
    """The list as one document (schema_version 2, docs/settings.md)."""
    its = {it.id: it for it in items(year, country)}
    num = {sid: i + 1 for i, sid in enumerate(its)}
    nxt = next_result(results)
    nit = its[nxt.id] if nxt is not None else None
    return {
        "schema_version": SCHEMA_VERSION,
        "in_project": True,
        "year": year, "country": country,
        "all_passed": all(r.passed for r in results),
        "counts": counts(results),
        "next": ({"step": num[nit.id], "id": nit.id, "title": nit.title,
                  "do": _do(nit, nxt)} if nit is not None else None),
        "steps": [_item_json(num[r.id], its[r.id], r,
                             nxt is not None and r.id == nxt.id, country)
                  for r in results],
    }


def guide_json(year: Optional[int] = None) -> Dict[str, Any]:
    """Outside a project: the same schema, nothing evaluated."""
    year = year or date.today().year - 1
    return {
        "schema_version": SCHEMA_VERSION, "in_project": False,
        "year": None, "country": None, "all_passed": None,
        "counts": None, "next": None,
        "steps": [_item_json(n, it, None, False, None)
                  for n, it in enumerate(items(year, None), 1)],
    }
