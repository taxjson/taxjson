"""What lib/checklist's steps run and read of the run's own code, built
here because lib may not import the commands, the pipeline or bin (step 8
of the architecture plan): the run's readers (checklist.RunHelpers, on the
Ctx and to question_answers) and `default_run_sub` (Ctx.run_sub: `tjs`
itself as a child process). cli/setup and cli/file pass them.

Here too, the parts of `tjs checklist` (cli/setup `cmd_checklist`) that
run the checks and read their verdicts: the --section read, the verdicts
checked or kept, the lenient taxjson.toml read, the --walk, and the stop
on a checklist.json that cannot be read or written (moved out of
cli/setup, which had grown past a command module's 3,000 lines)."""
import argparse
import os
import re
import subprocess
import sys
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from taxjson.cli.common import _double_roc_warnings, _year_keep
from taxjson.cli.estimate import _fx_cash_status
from taxjson.lib import out as _out
from taxjson.lib.checklist import RunHelpers
from taxjson.lib.dispatch import python_module_argv
from taxjson.pipeline.config import _country, _refuse_outside_dir_links, load_config
from taxjson.pipeline.messages import _die, _die_input
from taxjson.pipeline.stages import _crypto_broker_files
from taxjson.project.toml_io import read_toml


def _double_roc(root, accounts, year):
    return _double_roc_warnings(root, accounts, _year_keep(year))


def _broker_files(root, cfg):
    return _crypto_broker_files(root, cfg)


def _fx_cash(root, work):
    return _fx_cash_status(root, work)


def checklist_run_helpers() -> RunHelpers:
    """The roc-entered, crypto-sends and fx-cash readers. Each calls this
    module's binding when the step runs, so a test's patch_run of the
    name reaches the step."""
    return RunHelpers(double_roc=_double_roc, crypto_broker_files=_broker_files,
                      fx_cash_status=_fx_cash)


def default_run_sub(root: Path) -> Callable[..., Tuple[int, str, str]]:
    """Run `taxjson <argv>` on this project as a subprocess (the sub-
    commands sys.exit and print; a subprocess keeps that contained): the
    checklist's Ctx.run_sub. Here, not in lib/checklist: it names the
    entry point, a bin module (step 8)."""
    def run(argv: List[str], timeout: int = 900) -> Tuple[int, str, str]:
        # Never with the project folder on sys.path (security review H1).
        cmd = python_module_argv("taxjson.bin.taxjson_run",
                                 ["-C", str(root), *argv])
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


def _die_state(e: Exception) -> None:
    """Stop on a checklist.json that cannot be read or written: the
    message, then its fix (`chmod u+w` the folder; backlog TJ-0218)."""
    fix = getattr(e, "fix", "")
    _die(str(e), *([fix] if fix else []))


def _checklist_sections(args: argparse.Namespace) -> List[str]:
    """tjs checklist: the command line's first checks, before the
    project is looked for: the old --skip refused (naming --done
    --force), and the sections --section names, each known (none:
    []), with --only refused beside it. Returns the sections."""
    from taxjson.lib import checklist as cl
    if getattr(args, "skip", None):
        _named = ",".join(t.strip() for tok in args.skip
                          for t in str(tok).split(",") if t.strip())
        _die_input(f"--skip was replaced by --done --force: `tjs "
                   f"checklist --done {_named or 'ID'} --force`",
                   "It marks the step done over the last check's finding "
                   "(shown [f], the finding beside it). Nothing was "
                   "written.")
    section: List[str] = []
    if args.section:
        try:
            section = cl.resolve_sections(args.section)
        except KeyError as e:
            _die(f"unknown section {e.args[0]!r}",
                 "Sections: " + "; ".join(cl.section_label(sec)
                                          for sec in cl.SECTIONS)
                 + ". Several: `--section 6,7`.")
        if not section:
            _die("--section names no section")
        if args.only:
            _die("--section and --only: one or the other")
    return section


def _checklist_results(args: argparse.Namespace, ctx: Any, only: Optional[List[str]],
                       show_all: bool, jobs: int, _warn: Callable[..., None],
                       _note: Callable[..., None]) -> Tuple[List[Any], Any]:
    """tjs checklist: the verdicts to show. With --run each step is
    checked and the verdicts kept in work/ (shown once, not kept, when
    they cannot be stored); without it, the kept ones. Returns the
    verdicts and what the view says about them (when made, what
    changed since)."""
    from taxjson.lib import checklist as cl
    try:
        if args.run:
            results = cl.evaluate(ctx, only=only, quick=args.quick,
                                  progress=(cl.stderr_progress
                                            if show_all or args.json
                                            else cl.once_progress()),
                                  jobs=jobs)
            try:
                doc = cl.save_results(ctx, results)
            except OSError as e:
                # A read-only project, a full disk: the verdicts are
                # shown this once and not kept.
                _warn(f"the verdicts could not be stored in "
                      f"work/{cl.RESULTS_FILE} ({e.strerror or e}): "
                      f"shown now, not kept", prog="tjs checklist")
                info = cl.fresh_info()
            else:
                results, info = cl.load_results(ctx, only=only, doc=doc)
        else:
            if args.quick or args.jobs is not None:
                _note("--quick and -j go with --run; showing the stored "
                      "verdicts.", prog="tjs checklist")
            results, info = cl.load_results(ctx, only=only)
    except cl.StateFileError as e:
        _die_state(e)
    return results, info


def _checklist_whole(ctx, cl, only, results) -> Optional[List[Any]]:
    """Every step's kept verdict, the scoped view's `results` over them,
    for a scoped view (`--section`, `--only`) with nothing left in it:
    its last line names the next step elsewhere (backlog TJ-0048). None
    for the whole list, or while the scope has something left."""
    if not only or not all(r.passed for r in results):
        return None
    try:
        rest, _info = cl.load_results(ctx)
    except (cl.StateFileError, OSError):
        return None
    mine = {r.id: r for r in results}
    return [mine.get(r.id, r) for r in rest]


def _checklist_config(root: Path) -> Tuple[Dict[str, Any], str]:
    """(taxjson.toml, "" or why every other command refuses it) for the
    checklist. A config the strict loader refuses — a crypto account and
    no local_timezone (what `init` writes on a UTC machine), an old map
    file `tjs migrate` moves — is the configure item's finding, not
    an error instead of the list: the TOML is
    read leniently and the checks that need the strict config are
    blocked. Only a file with no readable (or plausible) year, country
    or account names still stops here, with the loader's own message."""
    import contextlib
    import io
    from taxjson.lib.country import CountryError, settings_country
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            return load_config(root), ""
    except SystemExit as e:
        strict = e
        text = e.code if isinstance(e.code, str) else buf.getvalue()
    try:
        cfg = read_toml(root / "taxjson.toml")
        table = cfg.get("settings")
        accounts = cfg.get("accounts") or {}
        year = table.get("year") if isinstance(table, dict) else None
        # The loader's plausible range (_normalize_settings): a typo'd
        # year still stops here, never a list for tax year 1850.
        # An account that is no table (`margin = 42`) died later on
        # a.get in the detectors (audit 2026-10-24 A16): the loader's
        # own message instead.
        ok = (isinstance(accounts, dict)
              and isinstance(year, int) and not isinstance(year, bool)
              and 1900 <= year <= date_cls.today().year + 1
              and all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", str(n))
                      and str(n).upper() != "COMBINED"
                      and isinstance(a, dict) for n, a in accounts.items()))
        if ok:
            table["country"] = settings_country(table)
    except (OSError, UnicodeError, ValueError, TypeError, CountryError):
        ok = False
    if not ok:
        if not isinstance(strict.code, str) and buf.getvalue():
            sys.stderr.write(buf.getvalue())
        raise strict
    # A folder taxjson writes into that is a link leaving the project
    # is refused as every command refuses it (load_config), lenient
    # read or not: the checklist wrote work/checklist_results.json
    # through such a work/ link (audit 2026-10-24 A17).
    _refuse_outside_dir_links(root, accounts)
    # The loader's message without its `tjs checklist: error:`
    # headline prefix, on one line.
    msg = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())
    msg = re.sub(r"^(?:Error:\s*|.*?: error:\s*)", "", msg, count=1)
    return cfg, msg or "taxjson.toml is refused by every other command"


def _walk_interrupted(sid: str, ctx) -> None:
    from taxjson.lib.out import error
    _out.put("", sys.stderr)
    error(f"interrupted at {sid} — it is not recorded",
          prog="tjs checklist",
          details=[f"The marks made so far are kept in "
                   f"{ctx.root / 'checklist.json'}."])
    sys.exit(1)


def _checklist_walk(ctx, cl, only, quick: bool = False,
                    scope: str = "") -> None:
    """Interactive pass over the open steps, one detector at a time: each
    step is shown as soon as its own check finishes (the whole list can
    take a minute on a big book), with why it matters and what was found,
    then the user's decision is recorded. Ends with the summary; exit 1
    while anything is still open (also after [q]uit)."""
    from taxjson.lib.out import kv_lines, wrap
    country = _country(ctx.settings)
    ids = [sid for sid in cl.item_ids() if not only or sid in only]
    keys = "[d]one  [f]orce done  [r]e-check  [n]ext  [q]uit  (Enter = next)"
    doc = _out.Doc(exact=True, width_=0, live=True)
    doc.line("CHECKLIST WALK")
    doc.line(f"For each open step: {keys}\n")
    seen = 0
    quit_early = False
    for sid in ids:
        r = cl.evaluate(ctx, only=[sid], quick=quick,
                        progress=cl.stderr_progress)[0]
        _walk_store(ctx, cl, r)
        if r.passed:
            for ln in wrap(f"{cl.SYMBOL[r.effective]} {sid}"
                           + (f": {r.detail}" if r.detail else "")
                           + (f" (marked {cl.mark_text(r.override)})"
                              if r.override else ""), None, "  ", "      "):
                doc.line(ln)
            continue
        seen += 1
        _, section, title, cmd, why = cl.item_meta(sid, country)
        show = True
        while True:
            if show:
                doc.line(f"\n{section.upper()} — {sid}")
                for ln in wrap(title, None, "  ", "  "):
                    doc.line(ln)
                for ln in kv_lines([
                        ("why", why), ("proves", cmd),
                        ("found", f"{cl.SYMBOL[r.effective]} {r.detail}"
                         + (f" (marked {cl.mark_text(r.override)}"
                            + (f": {r.note}" if r.note else "") + ")"
                            if r.override else ""))], "  "):
                    doc.line(ln)
            show = False
            try:
                ans = input("  > ").strip().lower()
            except EOFError:
                doc.blank()
                quit_early = True
                break
            except KeyboardInterrupt:
                _walk_interrupted(sid, ctx)
            if ans in ("d", "done", "f", "force"):
                # EOF or Ctrl-C at the note/reason prompt was a traceback
                # (re-audit A2-1394): this step is not recorded, the
                # marks made so far are kept.
                try:
                    note = input("  note (optional): " if ans[0] == "d"
                                 else "  reason (optional): ").strip()
                except (EOFError, KeyboardInterrupt):
                    _walk_interrupted(sid, ctx)
            if ans in ("q", "quit"):
                quit_early = True
                break
            if ans in ("d", "done"):
                if cl.finding_over_done(r):
                    # (as `--done` is refused: the mark would not show —
                    # a question step's finding that asks nothing too,
                    # audit 2026-10-25 first-project#1)
                    doc.line(f"  {sid} is {r.status}: a done mark would not "
                             f"show over it — [f] accepts it as it is")
                    continue
                cl.set_override(
                    ctx.root, ctx.year, sid, "done", note=note,
                    answers=(cl.question_answers(ctx.root, ctx.cfg, sid,
                                                 today=ctx.today, run=ctx.run)
                             if sid in cl.QUESTION_STEPS else None))
                doc.line(f"  recorded: {sid} done")
                break
            if ans in ("f", "force"):
                cl.set_override(ctx.root, ctx.year, sid, cl.MARK_FORCED,
                                note=note)
                doc.line(f"  recorded: {sid} done (forced)")
                break
            if ans in ("r", "recheck", "re-check"):
                r = cl.evaluate(ctx, only=[sid], progress=cl.stderr_progress)[0]
                _walk_store(ctx, cl, r)
                if r.passed:
                    for ln in wrap(f"now: {cl.SYMBOL[r.effective]} "
                                   f"{r.detail}", None, "  ", "    "):
                        doc.line(ln)
                    break
                show = True
                continue
            if ans in ("", "n", "next"):
                break
            doc.line(f"  unknown key {ans!r} — {keys}")
        if quit_early:
            break
    doc.line(f"\n{seen} open step(s) visited. Summary "
             f"(`tjs checklist --run` re-checks everything):\n")
    results, info = cl.load_results(ctx, only=only)
    doc.line(cl.render_summary(results, ctx.year, country, root=ctx.root,
                               info=info, scope=scope,
                               whole=_checklist_whole(ctx, cl, only,
                                                      results)))
    if quit_early or not all(r.passed for r in results):
        # Open steps remain: the one quit on, the ones passed over, a
        # DONE mark that did not settle the step's finding — the exit
        # follows the verdicts as kept, like `--run`'s (audit
        # 2026-10-24 A18).
        sys.exit(1)


def _walk_store(ctx, cl, r) -> None:
    """A walked step's verdict goes to the store like a --run's; a store
    that cannot be written never stops the walk."""
    try:
        cl.save_results(ctx, [r])
    except OSError:
        pass
