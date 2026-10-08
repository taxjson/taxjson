"""`taxjson quick-start`: every step from install to filing, in order.

Outside a project it prints the whole workflow: numbered steps, each
with the exact command(s) and a one-line why. Inside a project (a
taxjson.toml in the folder, or -C DIR) it marks each step from the
project's own state:

  done       the evidence is clean
  attention  the evidence says something is wrong
  todo       not done yet
  review     yours to run and read; taxjson cannot tell whether you did
  n/a        does not apply to this project

and the first `todo` / `attention` step is the NEXT one, with its
command. The marks come from what is already on disk: the checks of
lib/checklist that only read files (run-clean, journals, renames,
crypto-sends, wash-reviewed and the checklist.json marks), and
reports/run_summary.json, the counts `taxjson run` closes with
(lib/first_run). It never runs a command, writes a file or opens a
connection: `taxjson checklist` runs the slow checks.

Every user-facing `taxjson` command is named by some step or listed in
EXCLUDED with its reason; tests/test_quick_start.py fails when a new
command is in neither, or a step names a command that does not exist.

`--json` (schema_version 1; docs/settings.md, "taxjson quick-start
--json"):

    {"schema_version": 1, "in_project": bool,
     "year": int|null, "country": "canada"|"usa"|null,
     "counts": {"done": n, "attention": n, "todo": n, "review": n,
                "n/a": n} | null,
     "next": {"step": n, "id": str, "title": str, "do": str} | null,
     "steps": [{"step": n, "id": str, "section": str, "title": str,
                "how": str, "why": str,
                "commands": [{"command": str, "note": str,
                              "country": "canada"|"usa"|null}],
                "status": str|null, "detail": str, "next": bool}]}

Outside a project `status` is null and `counts` / `next` are null;
inside one the other country's commands are left out.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1
GUIDE = "docs/getting-started.md"
INSTALL = 'bash -c "$(curl -fsSL https://taxjson.com/install.sh)"'

# Commands no step names, each with the reason (the coverage test reads
# this): every other command in taxjson_run._COMMAND_GROUPS must be in
# a step.
EXCLUDED: Dict[str, str] = {
    "quick-start": "this guide itself",
    "deploy": "development machine only: puts a release on this "
              "machine's production copy",
    "promote": "development machine only: moves a release channel",
}

STATUSES = ("done", "attention", "todo", "review", "n/a")
MARK = {"done": "[x]", "attention": "[!]", "todo": "[ ]", "review": "[?]",
        "n/a": "[-]"}
NEXT_MARK = "[>]"


@dataclass(frozen=True)
class Cmd:
    command: str
    note: str = ""
    country: Optional[str] = None       # one country's projects only


@dataclass(frozen=True)
class Step:
    id: str
    section: str
    title: str
    why: str
    cmds: Tuple[Cmd, ...] = ()
    how: str = ""
    do: str = ""            # the closing line's action; default: cmds[0]


def steps(year: int, country: Optional[str] = None) -> List[Step]:
    """The workflow, in order. `year` fills the example commands;
    `country` (None outside a project) picks the init example."""
    ctry = country or "canada"
    nxt = year + 1
    return [
        # ------------------------------------------------------- set up
        Step("install", "Set up", "Install or upgrade taxjson",
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
        Step("init", "Set up", "Create the year's project",
             "One folder per tax year: taxjson.toml, a ticker.map and an "
             "inputs/<account>/ folder per account, each with a "
             "README.txt naming the export to download.",
             (Cmd(f"mkdir -p ~/taxes/{year} && cd ~/taxes/{year}",
                  "the year you file"),
              Cmd(f"tjs init --country {ctry} --year {year}",
                  "or --country " + ("usa" if ctry == "canada"
                                     else "canada")))),
        Step("configure", "Set up", "Make taxjson.toml match your accounts",
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
        # ---------------------------------------------------- your files
        Step("inputs", "Get your files",
             "Put each account's broker exports in inputs/<account>/",
             "A sale's cost comes from its purchase, which may be years "
             "back.",
             (Cmd("tjs fetch", "with the taxjson-fetch plugin (Questrade, "
                  "IBKR Flex); `tjs fetch --list` names the fetchers"),),
             how="Download all the history each broker gives, not just "
                 "the tax year, and keep a positions report with book "
                 "cost from the start of that history and from today. "
                 f"Which export per broker: {GUIDE}, step 3, and each "
                 "folder's README.txt.",
             do="download the exports into inputs/<account>/ (or "
                "`tjs fetch`)"),
        # ------------------------------------------------------- build
        Step("run", "Build the books", "Build the books",
             "Its closing list names what the books show is still "
             "incomplete, each with the command that fixes it.",
             (Cmd("tjs run", "every file read, reports/ written; asks "
                  "about mergers and spin-offs"),
              Cmd("tjs run --no-input", "never asks: an open election "
                  "exits 3 (next step)"))),
        Step("elections", "Build the books",
             "Decide the merger and spin-off elections",
             "An account with an open election is left out of the run; a "
             "spin-off valued at $0 books no income and a $0 cost.",
             (Cmd("tjs elect --pending", "what is open, with ready "
                  "--set lines"),
              Cmd("tjs elect ACCOUNT --set ID=ELECTION", "record one"),
              Cmd("tjs spinoffs", "each spin-off's election, value and "
                  "cost; `tjs splits`: splits and consolidations"))),
        # ------------------------------------------------------ the gaps
        Step("missing-history", "Fill the gaps",
             "Fill in missing purchase history",
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
        Step("transfers", "Fill the gaps",
             "Shares moved in from another broker",
             "Their cost is the original purchase, not the day they "
             "arrived.",
             (Cmd("tjs transfers", "the custody moves the books leave "
                  "out"),),
             how="A transfer-in with no cost is kept out of the books: "
                 f"enter its purchase as a .tt line ({GUIDE}, 5c)."),
        Step("ticker-map", "Fill the gaps",
             "Symbol spellings and listings (ticker.map)",
             "Two spellings or listings of one security must be one "
             "position for its cost to be right.",
             (Cmd("tjs ticker-map --suggest", "the lines the last run "
                  "suggested, each with its reason"),
              Cmd("tjs ticker-map --suggest --write", "adds them, one by "
                  "one (--all: every one)"))),
        Step("journals", "Fill the gaps",
             "Broker journals between two listings",
             "A journal the books do not pool leaves a long on one "
             "listing and a short on the other.",
             (Cmd("tjs journals --pending", "the journals not joined or "
                  "settled"),)),
        Step("renames", "Fill the gaps", "Ticker changes, as dated events",
             "A rename carries the position and its cost on its date; a "
             "later trade in the old ticker is another security unless "
             "ticker.map says otherwise.",
             (Cmd("tjs renames --pending", "the renames to declare or "
                  "resolve"),
              Cmd("tjs renames", "every rename with its date and "
                  "source"))),
        Step("crypto-sends", "Fill the gaps",
             "Crypto sends that left your accounts",
             "A send that left your ownership is a disposition at fair "
             "value; only you know which did.",
             (Cmd("tjs crypto-sends", "the sends that did not arrive in "
                  "another of your accounts"),
              Cmd("tjs crypto-sends ACCOUNT --set ID=self", "or payment "
                  "(Canada: also gift)"))),
        Step("tt-lines", "Fill the gaps",
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
        # ------------------------------------------------------- tidy
        Step("format", "Tidy the config", "Lay out the config files",
             "The same layout every year, so a diff of two years' "
             "projects shows only what changed; no number changes.",
             (Cmd("tjs format", "taxjson.toml as the template lays it "
                  "out: shows the diff"),
              Cmd("tjs format --write", "applies it (keeps "
                  "taxjson.toml.bak)"),
              Cmd("tjs format-map --write", "ticker.map in keyword "
                  "groups; dated events move to .tt lines (`tjs "
                  "format-map` shows the diff)"))),
        # ------------------------------------------------------- check
        Step("scan", "Check", "Look for tax-efficiency mistakes",
             "Advice on where you hold what; it changes no number of "
             "this year.",
             (Cmd("tjs scan", "e.g. a dividend payer held where its "
                  "withholding is lost, a listing pair ticker.map does "
                  "not join"),)),
        Step("sanity", "Check", "Check positions against the broker",
             "Missing history in a position you still hold shows only "
             "here: the books cannot see a purchase that is not in the "
             "files.",
             (Cmd("tjs sanity", "against the holdings files named in "
                  "taxjson.toml (holdings = [...])"),
              Cmd("tjs sanity ACCOUNT=FILE", "against one positions "
                  "report: an IB statement, an RBC Holdings Export or a "
                  "[[holding]] .toml"))),
        Step("edge-cases", "Check", "Trades on a boundary",
             "A trade a day either side of Dec 31, or of a loss's 30-day "
             "window, changes the year or the denial.",
             (Cmd("tjs edge-cases", "each one, with where it lands and "
                  "why"),)),
        Step("option-timing", "Check",
             "Written options carried in from an earlier year",
             "A contract written before option_grant_timing_since and "
             "closed this year is taxed at the close: right only if the "
             "year it was written did not report its premium (ITA "
             "s.49(1)), which only you know.",
             (Cmd("tjs option-boundary", "each contract, where its premium "
                  "lands and what to do", "canada"),),
             how="If last year's return reported the premiums when "
                 "written, set option_grant_timing_since = <that year> in "
                 "[settings]; if it did not, confirm with `tjs checklist "
                 "--done option-boundary`.",
             do="answer the question (`tjs option-boundary`)"),
        Step("wash-sales", "Check",
             "Review each denied loss (superficial loss / wash sale)",
             "A loss denied by a registered-account (US: IRA) repurchase "
             "is gone for good: make sure each is real.",
             (Cmd("tjs wash-sales", "each loss denied this year, and "
                  "why"),
              Cmd("tjs wash-sales --explain", "with the matching "
                  "purchases"))),
        Step("check-dates", "Check", "Check the trade and settlement dates",
             "A date on a closed day, or a settlement before the trade, "
             "moves a sale to the wrong rate or year.",
             (Cmd("tjs check-dates", "every date against its market's "
                  "calendar"),)),
        Step("checklist", "Check", "Walk the filing checklist",
             "It runs the slow checks this guide does not (sanity, the "
             "audit, the slips); quick-start only reads files.",
             (Cmd("tjs checklist", "every filing step, checked by the "
                  "command that proves it"),
              Cmd("tjs checklist --walk", "the open steps one at a "
                  "time"))),
        # ------------------------------------------------------ results
        Step("sum", "Results", "Read the year's numbers",
             "`Done.` does not mean right: read them once the gaps are "
             "filled.",
             (Cmd("tjs sum", "the year's gains, ending with the lines for "
                  "your return"),
              Cmd("tjs list", "what the books hold, with book cost; `tjs "
                  "shares`: per symbol across accounts"))),
        Step("explore", "Results", "Look closer at any number",
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
        Step("slips", "Results", "Reconcile with your tax slips",
             "The tax authority matches your return to the slips: a "
             "difference explained now is not a letter later.",
             (Cmd("tjs reconcile-slips inputs/slips/*.csv", "T5008 (US: "
                  "1099-B / 1099-DA) CSVs against the dispositions"),
              Cmd("tjs roc-sum", "with `tjs divs-sum`: compare with the "
                  "T5 / T3 (US: 1099-DIV) by hand"))),
        Step("filing", "Results", "Produce the filing numbers",
             "The export is what goes on the return; its totals must "
             "equal `tjs sum`.",
             (Cmd("tjs form-export", "Schedule 3 rows (US: Form 8949 "
                  "and Schedule D)"),
              Cmd("tjs form-export --form txf --out gains.txf",
                  "a TurboTax import", "usa"),
              Cmd("tjs t1135", "foreign property test and tables",
                  "canada"),
              Cmd("tjs carryover", "capital-loss carryforward across "
                  "years"),
              Cmd("tjs fx-cash", "currency gains on foreign cash"),
              Cmd("tjs option-boundary", "written options across a year "
                  "end", "canada"))),
        Step("estimate", "Results", "Estimate the tax",
             "A check on what you owe before you file.",
             (Cmd("tjs estimate", "tax on the year's investment income "
                  "([estimate] other_income in taxjson.toml)"),
              Cmd("tjs amt", "minimum tax, line by line", "canada"),
              Cmd("tjs instalments", "instalments due, paid and "
                  "interest", "canada"))),
        # --------------------------------------------- through the year
        Step("trading", "Through the year", "Before you trade",
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
        # ------------------------------------------------------ sharing
        Step("redact", "Share safely", "Share a sample, never the exports",
             "For a bug report or a broker taxjson does not read yet; "
             "the redactor works from patterns, so read the copy first.",
             (Cmd("tjs redact", "copies inputs/ to inputs_redact/ with "
                  "account numbers and names replaced"),)),
        # ----------------------------------------------------- year end
        Step("close-year", "Year end", "Lock the year you filed",
             "The lock is what the drift check and next year's handoff "
             "compare with.",
             (Cmd("tjs close-year", "after filing: filed/<year>.json "
                  "(commit it)"),
              Cmd("tjs check-filed", "later: recomputes filed years and "
                  "shows any drift"))),
        Step("next-year", "Year end", "Start next year's project",
             "Next year's cost comes from this year's books.",
             (Cmd(f"tjs init --country {ctry} --year {nxt} ../{nxt}",
                  "a folder beside this one"),),
             how=f"Copy inputs/ and ticker.map into the new folder, and "
                 f"set [settings] prior_year_record = "
                 f"\"../{year}/filed/{year}.json\" in its taxjson.toml."),
        Step("handoff", "Year end", "Check the new year starts where this "
             "one ended",
             "A Dec 31 trade settling in January, or a dropped lot, makes "
             "a gain vanish or count twice.",
             (Cmd("tjs handoff", "in the new project"),)),
    ]


_TJS = re.compile(r"(?<![\w-])tjs ([a-z][a-z0-9-]*)")


def named_commands(year: int = 2025) -> Dict[str, List[str]]:
    """{command: [step ids naming it]} over every command, note, how and
    why text of the guide (`tjs NAME`)."""
    out: Dict[str, List[str]] = {}
    for st in steps(year):
        texts = [st.why, st.how, st.do] + [t for c in st.cmds
                                           for t in (c.command, c.note)]
        for t in texts:
            for m in _TJS.finditer(t):
                ids = out.setdefault(m.group(1), [])
                if st.id not in ids:
                    ids.append(st.id)
    return out


# ------------------------------------------------------------ state
@dataclass
class State:
    status: str
    detail: str = ""


@dataclass
class Guide:
    in_project: bool
    year: Optional[int]
    country: Optional[str]
    steps: List[Step]
    states: Dict[str, State] = field(default_factory=dict)

    @property
    def next_id(self) -> Optional[str]:
        for st in self.steps:
            s = self.states.get(st.id)
            if s and s.status in ("todo", "attention"):
                return st.id
        return None


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("taxjson")
    except Exception:                                   # noqa: BLE001
        return "unknown"


def _refuse_sub(argv, timeout=0):
    """lib/checklist's run_sub: quick-start runs nothing."""
    raise RuntimeError("quick-start runs no command")


def _soft_config(root: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """(the parsed taxjson.toml or None, what is wrong with it) — never
    an exit: a broken config is the configure step's finding."""
    from taxjson.lib.tomlcompat import tomllib
    from taxjson.lib.config_check import settings_problems
    try:
        raw = (root / "taxjson.toml").read_bytes()
        cfg = tomllib.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as e:
        return None, [f"taxjson.toml cannot be read: {e}"]
    try:
        problems = settings_problems(cfg)
    except Exception as e:                              # noqa: BLE001
        problems = [str(e)]
    if not isinstance(cfg.get("accounts") or {}, dict):
        problems.append("[accounts] must be a table")
    return cfg, problems


def _load_json(p: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _from_checklist(res, ran: bool) -> State:
    """A lib/checklist Result (its checklist.json mark applied) as a
    quick-start state."""
    eff = res.effective
    detail = res.detail
    if res.override:
        detail = (f"marked {res.override} in checklist.json"
                  + (f": {res.note}" if res.note else ""))
        if res.finding:
            detail += f"; the check still says: {res.finding}"
    if eff in ("done", "skipped"):
        return State("done", detail)
    if eff == "attention":
        return State("attention", detail)
    if eff == "manual":
        return State("review", detail)
    if eff == "n/a":
        return State("n/a", detail)
    return State("todo", detail if ran else "after `tjs run`")


def evaluate(root: Path, today: Optional[date] = None) -> Guide:
    """The guide with each step's state for the project at `root`
    (which holds a taxjson.toml). Reads files only."""
    from taxjson.lib import checklist as cl
    from taxjson.lib import first_run as fr
    from taxjson.lib.migrate import legacy_files, legacy_message
    today = today or date.today()
    cfg, problems = _soft_config(root)
    settings = (cfg or {}).get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    year = settings.get("year")
    year = year if isinstance(year, int) and not isinstance(year, bool) \
        else None
    country = settings.get("country") if settings.get("country") in (
        "canada", "usa") else None
    accounts = (cfg or {}).get("accounts") or {}
    if not isinstance(accounts, dict):
        accounts = {}
    accounts = {str(n): a for n, a in accounts.items() if isinstance(a, dict)}
    g = Guide(True, year, country,
              steps(year or today.year - 1, country))
    S = g.states
    ok_cfg = cfg is not None and not problems and year is not None

    # Set up
    S["install"] = State("done", f"taxjson {_version()}")
    S["init"] = State("done", f"taxjson.toml for {year or '?'}"
                      + (f" ({country})" if country else ""))
    cfg_issues = list(problems)
    legacy = legacy_files(root)
    if legacy:
        cfg_issues.append(legacy_message(legacy))
    if cfg is not None and not accounts:
        cfg_issues.append("no [accounts.NAME] section")
    crypto = sorted(n for n, a in accounts.items() if a.get("crypto") is True)
    if crypto and not settings.get("local_timezone"):
        cfg_issues.append(f"[accounts.{crypto[0]}] is a crypto account but "
                          f"[settings] has no local_timezone — set it, or "
                          f"delete the section if you have no crypto")
    if cfg_issues:
        S["configure"] = State("attention", "; ".join(cfg_issues))
    else:
        S["configure"] = State("done", ", ".join(
            f"{n} ({'crypto' if a.get('crypto') else a.get('type', '?')})"
            for n, a in accounts.items()))

    # Your files
    empty = [n for n in accounts if not cl._data_files(root / "inputs" / n)]
    sheets = []
    for n in accounts:
        folder = root / "inputs" / n
        if folder.is_dir():
            stems = {p.stem.lower() for p in cl._data_files(folder)}
            sheets += [f"inputs/{n}/{p.name}" for p in sorted(folder.iterdir())
                       if p.is_file()
                       and p.suffix.lower() in cl.SPREADSHEET_SUFFIXES
                       and p.stem.lower() not in stems]
    n_files = sum(len(cl._data_files(root / "inputs" / n)) for n in accounts)
    if accounts and len(empty) == len(accounts):
        S["inputs"] = State("todo", "no broker exports yet in "
                            + ", ".join(f"inputs/{n}/" for n in empty))
    elif empty or sheets:
        bits = []
        if empty:
            bits.append(f"empty: {', '.join(f'inputs/{n}/' for n in empty)}"
                        f" — add their exports, or delete the account's "
                        f"section from taxjson.toml")
        if sheets:
            bits.append(f"not read: {', '.join(sheets)} — save it as CSV")
        S["inputs"] = State("attention", "; ".join(bits))
    elif accounts:
        S["inputs"] = State("done", f"{n_files} file(s) in "
                            f"{len(accounts)} account folder(s)")
    else:
        S["inputs"] = State("todo", "no account in taxjson.toml yet")

    reports = root / "reports"
    ran = reports.is_dir() and any(reports.glob("*.sum"))
    ctx = None
    if ok_cfg:
        ctx = cl.Ctx(root=root, cfg=cfg, year=year, today=today,
                     run_sub=_refuse_sub)
    try:
        overrides = cl.load_state(root)
        if overrides.get("year") not in (None, year):
            overrides = {}
        overrides = overrides.get("overrides") or {}
    except cl.StateFileError:
        overrides = {}

    def checked(sid: str, fn: Callable) -> State:
        if ctx is None:
            return State("todo", "after taxjson.toml loads")
        try:
            res = fn(ctx)
        except Exception as e:                          # noqa: BLE001
            return State("todo", f"could not check ({e})")
        ov = overrides.get(sid) or {}
        if ov.get("status") in ("done", "skipped"):
            res.override = ov["status"]
            res.note = ov.get("note") or ""
            if res.status in ("attention", "blocked"):
                res.finding = res.detail
        return _from_checklist(res, ran)

    def option_question(ctx_) -> Any:
        from taxjson.lib import option_boundary as OB
        rows = OB.project_question_rows(root, cfg or {}, today=today)
        if rows:
            return cl.Result("option-boundary", "attention",
                             OB.question_detail(rows, year), question=True)
        return cl.Result("option-boundary", "done",
                         "no contract written before "
                         "option_grant_timing_since closed this year")

    def marked(sid: str, st: State) -> State:
        """A review step the user marked done/skipped in checklist.json."""
        ov = overrides.get(sid) or {}
        if st.status == "review" and ov.get("status") in ("done", "skipped"):
            return State("done", f"marked {ov['status']} in checklist.json"
                         + (f": {ov['note']}" if ov.get("note") else ""))
        return st

    # A broker whose exports stop while it holds positions
    # (lib/export_coverage): only the books can tell.
    if ran and S["inputs"].status == "done":
        cov = checked("export-coverage", cl.d_export_coverage)
        if cov.status == "attention":
            S["inputs"] = cov

    # Build the books
    S["run"] = checked("run-clean", cl.d_run_clean) if ran else State(
        "todo", "no reports/ yet")
    pend = 0
    for p in sorted((root / "work").glob("*pending_elections.json")) \
            if (root / "work").is_dir() else []:
        doc = _load_json(p)
        accts = (doc or {}).get("accounts")
        if isinstance(accts, dict):
            pend += sum(len(a.get("pending") or []) for a in accts.values()
                        if isinstance(a, dict))
        elif doc is None and p.is_file():
            pend = max(pend, 1)         # unreadable: still open (checklist)
    zero = cl._zero_value_elections(ctx) if ctx is not None else 0
    if pend:
        S["elections"] = State("attention", f"{pend} election(s) pending — "
                               f"its account is left out of the run")
    elif zero:
        S["elections"] = State("attention", f"{zero} spin-off/merger(s) "
                               f"booked at $0 — set fmv_per_share with "
                               f"`tjs elect`")
    elif ran:
        S["elections"] = State("done", "none pending")
    else:
        S["elections"] = State("todo", "after `tjs run`")

    # Fill the gaps: the run's closing counts (lib/first_run)
    summ = _load_json(reports / fr.SUMMARY_FILE) if ran else None
    if summ is not None and year is not None \
            and str(summ.get("year")) != str(year):
        summ = None
    if summ is None:
        wait = State("todo", "after `tjs run`" if not ran else
                     f"no reports/{fr.SUMMARY_FILE} for {year} — re-run "
                     f"`tjs run`")
        S["missing-history"] = wait
        S["transfers"] = wait
    else:
        np_ = summ.get("no_purchase") or []
        cov = [i for i in summ.get("no_purchase_in_sum") or []
               if i.get("booked") == fr.BOOKED_SHORT_COVER]
        zs = summ.get("zero_cost_sold") or []
        zh = summ.get("zero_cost_held") or []
        bits = []
        if np_:
            bits.append(f"{len(np_)} sold in {year} with no purchase in "
                        f"your files, not in `tjs sum`: {fr._names(np_)}")
        if cov:
            bits.append(f"{len(cov)} sold in {year} with no purchase, "
                        f"booked at a later purchase's cost: "
                        f"{fr._names(cov)}")
        if zs or zh:
            bits.append(f"{len(zs) + len(zh)} at a $0 cost: "
                        f"{fr._names(zs + zh)}")
        S["missing-history"] = (State("attention", "; ".join(bits)) if bits
                                else State("done", "every sale has its "
                                           "purchase; no $0 cost"))
        ov = overrides.get("missing-history") or {}
        if bits and ov.get("status") == "skipped":
            S["missing-history"] = State(
                "done", "marked skipped in checklist.json; the run still "
                        "says: " + "; ".join(bits))
        tn = summ.get("transfer_in_no_cost") or []
        tb = summ.get("transfer_in_book_value") or []
        if tn:
            S["transfers"] = State("attention", f"{len(tn)} transfer-in(s) "
                                   f"kept out with no cost: {fr._names(tn)}")
        elif tb:
            S["transfers"] = State("review", f"{len(tb)} transfer-in(s) at "
                                   f"the broker's book value: "
                                   f"{fr._names(tb)} — is it your cost?")
        else:
            S["transfers"] = State("done", "no transfer-in without its "
                                   "cost")
    if ran and (root / "work").is_dir():
        try:
            from taxjson.lib import ticker_map_suggest as TS
            offer, _skipped = TS.pending(root)
            S["ticker-map"] = (State("todo", f"{len(offer)} line(s) the "
                                     f"last run suggested: decide each")
                               if offer else State("done", "nothing "
                                                   "suggested"))
        except Exception as e:                          # noqa: BLE001
            S["ticker-map"] = State("todo", f"could not check ({e})")
    else:
        S["ticker-map"] = State("todo", "after `tjs run`")
    S["journals"] = checked("journals", cl.d_journals)
    S["renames"] = checked("renames", cl.d_renames)
    S["crypto-sends"] = (checked("crypto-sends", cl.d_crypto_sends)
                         if crypto else State("n/a", "no crypto account"))
    tts = sorted(p for n in accounts
                 for p in cl._data_files(root / "inputs" / n)
                 if p.suffix.lower() == ".tt")
    S["tt-lines"] = State("review", f"{len(tts)} .tt file(s) in inputs/"
                          if tts else "")

    # Tidy
    S["format"] = _format_state(root)

    # Check
    S["scan"] = State("review")
    un = (summ or {}).get("unchecked_accounts") or []
    inc = (summ or {}).get("income_not_held") or []
    has_holdings = any(a.get("holdings") for a in accounts.values())
    if summ is None:
        S["sanity"] = S["missing-history"]
    elif inc:
        S["sanity"] = State("attention", f"{len(inc)} security(ies) paid "
                            f"income the books do not hold: "
                            f"{fr._names(inc)}")
    elif un:
        S["sanity"] = State("todo", "no holdings file for "
                            + fr._accounts_shown(un) + " (open positions) "
                            "— add holdings = [...] to the account, or "
                            "`tjs sanity ACCOUNT=FILE`")
    elif has_holdings:
        S["sanity"] = marked("sanity", State(
            "review", "the run compares positions with the holdings files; "
                      "`tjs sanity` shows the tables"))
    else:
        S["sanity"] = State("done", "no open position to check")
    S["edge-cases"] = State("review")
    if country == "usa":
        S["option-timing"] = State("n/a", "US project: a premium is taxed "
                                   "at the close (§1234)")
    elif ran:
        S["option-timing"] = checked("option-boundary", option_question)
    else:
        S["option-timing"] = State("todo", "after `tjs run`")
    S["wash-sales"] = checked("wash-reviewed", cl.d_wash_reviewed)
    S["check-dates"] = marked("check-dates", State("review"))
    lock = root / "filed" / f"{year}.json"
    locked = year is not None and lock.is_file()
    S["checklist"] = (State("done", f"filed/{year}.json: the year is "
                            f"locked") if locked else
                      State("todo", "" if ran else "after `tjs run`"))

    # Results
    S["sum"] = State("review")
    S["explore"] = State("review")
    slips = cl.slip_files(root)
    S["slips"] = marked("t5008", State(
        "review", f"{len(slips)} slip file(s) in inputs/slips/" if slips
        else "no slip CSV in inputs/slips/ yet"))
    S["filing"] = marked("form-export", State("review"))
    S["estimate"] = State("review")
    S["trading"] = State("review")
    S["redact"] = State("review")

    # Year end
    S["close-year"] = (State("done", f"filed/{year}.json") if locked
                       else State("todo", "after you file"))
    S["next-year"] = (State("todo", f"start {year + 1}") if locked
                      else State("review", "after you file"))
    prior = settings.get("prior_year_record")
    if year is not None and not prior \
            and not (root / "filed" / f"{year - 1}.json").exists():
        S["handoff"] = marked("handoff", State(
            "review", f"no {year - 1} record: set [settings] "
                      f"prior_year_record to last year's filed/"
                      f"{year - 1}.json (none for your first year)"))
    else:
        S["handoff"] = marked("handoff", State("review"))

    # The other country's commands are left out.
    if country:
        g.steps = [_for_country(st, country) for st in g.steps]
    return g


def _for_country(st: Step, country: str) -> Step:
    from dataclasses import replace
    return replace(st, cmds=tuple(c for c in st.cmds
                                  if c.country in (None, country)))


def _format_state(root: Path) -> State:
    from taxjson.lib.config_template import format_config
    from taxjson.lib.ticker_map_format import format_map
    loose = []
    try:
        text = (root / "taxjson.toml").read_bytes().decode("utf-8-sig")
        if format_config(text).changed:
            loose.append("taxjson.toml")
    except Exception:                                   # noqa: BLE001
        loose.append("taxjson.toml")
    tm = root / "ticker.map"
    if tm.is_file():
        try:
            res = format_map(tm.read_bytes().decode("utf-8-sig"),
                             migrate=True)
        except Exception as e:                          # noqa: BLE001
            return State("attention", f"ticker.map: {e}")
        if res.problems:
            return State("attention", f"{len(res.problems)} ticker.map "
                         f"problem(s) `tjs run` refuses — `tjs format-map` "
                         f"names them")
        if res.changed or res.migration_pending:
            loose.append("ticker.map")
    if loose:
        return State("review", " and ".join(loose)
                     + " not in the template layout")
    return State("done", "taxjson.toml and ticker.map laid out")


def outside(today: Optional[date] = None) -> Guide:
    """The guide with no project: every step, no state."""
    today = today or date.today()
    return Guide(False, None, None, steps(today.year - 1))


# ----------------------------------------------------------- output
def _cmd_lines(st: Step, w: int, lead: str, country: Optional[str]
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
    col = max((len(c.command) for c in st.cmds if len(c.command) <= 44),
              default=0)
    out: List[str] = []
    for c in st.cmds:
        note = shown(c)
        cmd = printable(c.command) if w > 0 else c.command
        line = f"{lead}{cmd.ljust(col)}  # {note}" if note else lead + cmd
        if not note or w <= 0 or len(line) <= w:
            out.append(line.rstrip())
            continue
        out.append(lead + cmd)
        out.extend(wrap(f"# {note}", w, lead + "  ", lead + "    "))
    return out


def render(g: Guide, show_all: bool = False,
           width_: Optional[int] = None) -> str:
    """The guide in the house layout (docs/output-style.md): a title,
    one section per stage, each step its mark, number and title, with
    its state, how, commands and why under the title; the last line
    says what to do next."""
    from taxjson.lib.out import Doc, wrap
    if g.in_project:
        counts = {s: sum(1 for st in g.steps if g.states[st.id].status == s)
                  for s in STATUSES}
        title = (f"QUICK START — tax year {g.year or '?'}"
                 + (f" ({g.country})" if g.country else "")
                 + f": {counts['done']}/{len(g.steps) - counts['n/a']} done")
    else:
        title = "QUICK START — from install to filing, step by step"
    d = Doc(title, width_=width_)
    nxt = g.next_id
    if g.in_project:
        d.para(f"{counts['attention']} need attention, {counts['todo']} to "
               f"do, {counts['review']} to run and read yourself, "
               f"{counts['n/a']} n/a")
    else:
        d.para("Each step: the command(s) to type and why. `tjs` is the "
               f"short name of `taxjson`. Worked examples: {GUIDE}.")
    sections: List[str] = []
    for st in g.steps:
        if st.section not in sections:
            sections.append(st.section)
    num = {st.id: i + 1 for i, st in enumerate(g.steps)}
    for k, sec in enumerate(sections, 1):
        d.section(f"{k}. {sec.upper()}")
        first = True
        for st in (s for s in g.steps if s.section == sec):
            if not first:
                d.blank()
            first = False
            state = g.states.get(st.id)
            if g.in_project:
                mark = NEXT_MARK if st.id == nxt else MARK[state.status]
                head = f"  {mark} {num[st.id]:>2}. "
            else:
                head = f"  {num[st.id]:>2}. "
            lead = " " * len(head)
            for ln in wrap(st.title, d.w, head, lead):
                d.line(ln)
            # Inside a project a done or n/a step is its state line; an
            # open one adds its commands; the next step and one that
            # needs attention also say how and why (--all: every step).
            full = (not g.in_project or show_all or st.id == nxt
                    or state.status == "attention")
            cmds = full or state.status in ("todo", "review")
            if state is not None and state.detail:
                d.para(state.detail, lead)
            if full and st.how:
                d.para(st.how, lead)
            if cmds:
                for ln in _cmd_lines(st, d.w, lead, g.country):
                    d.line(ln)
            if full:
                d.para(f"Why: {st.why}", lead)
    d.blank()
    if g.in_project:
        d.line("[x] done  [!] needs attention  [ ] to do  [?] yours to "
               "run and read  [-] n/a  [>] next")
        if nxt:
            st = next(s for s in g.steps if s.id == nxt)
            d.para(f"Next (step {num[nxt]}): {_do(st)}")
        else:
            d.para("Nothing left to do now: the [?] steps are yours to "
                   "run and read.")
    else:
        d.para("In a project folder (or `tjs -C DIR quick-start`) each "
               "step is marked done or not, and the next one named.")
    return d.text()


def _do(st: Step) -> str:
    if st.do:
        return st.do
    return f"`{st.cmds[0].command}`" if st.cmds else st.title


def to_json(g: Guide) -> Dict[str, Any]:
    nxt = g.next_id
    num = {st.id: i + 1 for i, st in enumerate(g.steps)}
    counts = None
    if g.in_project:
        counts = {s: sum(1 for st in g.steps if g.states[st.id].status == s)
                  for s in STATUSES}
    nst = next((s for s in g.steps if s.id == nxt), None)
    return {
        "schema_version": SCHEMA_VERSION,
        "in_project": g.in_project,
        "year": g.year, "country": g.country,
        "counts": counts,
        "next": ({"step": num[nst.id], "id": nst.id, "title": nst.title,
                  "do": _do(nst)} if nst else None),
        "steps": [{
            "step": num[st.id], "id": st.id, "section": st.section,
            "title": st.title, "how": st.how, "why": st.why,
            "commands": [{"command": c.command, "note": c.note,
                          "country": c.country} for c in st.cmds],
            "status": (g.states[st.id].status if g.in_project else None),
            "detail": (g.states[st.id].detail if g.in_project else ""),
            "next": st.id == nxt,
        } for st in g.steps],
    }
