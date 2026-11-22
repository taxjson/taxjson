"""How the run calls a sub-tool and captures its output and .diag stderr."""
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from taxjson.lib.cli_diag import write_text_atomic

from taxjson.pipeline import run_state
from taxjson.pipeline.messages import (
    _die_input, _PROG, _run_concise, _say,
)


# ---------------------------------------------------------------- subprocess

# Two scripts whose module name doesn't follow the s/-/_/ pattern of the
# console-script name in pyproject.toml. Everything else uses the default
# `taxjson-foo-bar` → `taxjson_foo_bar` mapping.
_MODULE_OVERRIDES = {
    "taxjson-to-base-curr": "to_base_curr",
    "taxjson-fill-crypto": "fill_crypto_prices",
}


def _cmd(name: str) -> List[str]:
    """Build the invocation for one of the taxjson sub-tools. Runs the
    module `taxjson.bin.<module>` so the wrapper works whether or not
    the console scripts are on PATH; built by lib/dispatch.py
    python_module_argv, never a plain `python -m` that imports a
    planted json.py from the folder it runs in (backlog TJ-0632).
    dispatch.run_cmd runs it in process."""
    from taxjson.lib.dispatch import python_module_argv
    module = _MODULE_OVERRIDES.get(name, name.replace("-", "_"))
    return python_module_argv(f"taxjson.bin.{module}")


def run_to_file(cmd: List[str], out_path: Path, *, capture_diag: bool = True,
                interactive: bool = False) -> None:
    """Run a sub-tool, writing stdout to out_path.

    stderr is normally captured, never echoed live — the console stays
    clean. When capture_diag is set, stderr is persisted to an
    `<out_path>.diag` sidecar that the .sum report's DIAGNOSTICS section
    folds in. On a non-zero exit the stderr is surfaced so a real failure
    isn't hidden behind the wrapper's own traceback.

    stdin is /dev/null for every stage so a sub-tool can never block the
    pipeline waiting on input it will never get.

    `interactive=True` is for the one stage that legitimately prompts —
    taxjson-corp-actions' election questions. It lets stderr (the prompt)
    and stdin (your answer) pass through to the terminal while stdout still
    goes to the file. At a non-TTY (CI/cron) the tool falls back to its own
    'needs an election' error, which surfaces here as a normal failure.
    """
    from taxjson.pipeline.dispatch import run_cmd
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.is_dir():
        # one line, not an IsADirectoryError at the rename (A2-0795)
        _die_input(_work_path_problem(out_path))
    # Write to a .part sidecar and rename into place only on success. A failed
    # (or Ctrl-C'd, e.g. at the corp-actions election prompt) stage must never
    # leave a truncated output file: its fresh mtime would make needs_rebuild
    # treat the corrupt file as valid and feed it to downstream stages. On
    # failure the previous good output (if any) survives with its old mtime,
    # so the next run correctly rebuilds the stage.
    # The .part is created fresh (never through a symlink planted at its
    # name) and the rename replaces a symlink at out_path instead of
    # writing through it (lib/safe_write; security review M1).
    from taxjson.lib.safe_write import atomic_open
    if interactive:
        # The prompt owns stderr, so no fresh .diag is captured —
        # but a STALE one from a prior non-interactive run of this
        # stage would keep polluting the .sum DIAGNOSTICS banner
        # until hand-deleted from work/ (KNOWN_ISSUES). Clear it.
        out_path.with_name(out_path.name + ".diag").unlink(
            missing_ok=True)
        with atomic_open(out_path, binary=True) as f:
            result = run_cmd(cmd, stdout=f,      # stderr+stdin keep TTY
                             interactive=True)
            if result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, cmd)
        return
    from taxjson.lib.cli_diag import NO_SPACE_FIX, is_no_space, no_space_text
    try:
        with atomic_open(out_path) as f:
            result = run_cmd(cmd, stdout=f)
            stderr_text = result.stderr or ""
            if capture_diag:
                diag_path = out_path.with_name(out_path.name + ".diag")
                # (a directory in its place: one line, A2-0795)
                # Written when it changed, as every other stamp of the
                # run: a run of the same inputs captures the same text,
                # and its rewrite was a temp, an fsync and a rename per
                # stage (backlog TJ-4962). Nothing compares a .diag's
                # mtime with another file's; the parse's reuse test
                # (pipeline/inputs `_parse_outputs`) reads its inode and
                # mtime after this write, so it sees the file it keeps.
                if stderr_text:
                    _write_work_stamp(diag_path, stderr_text,
                                      changed_only=True)
                else:
                    _drop_work_file(diag_path)
            if result.returncode != 0:
                if stderr_text:
                    _echo_stage_stderr(_stage_text_shown(stderr_text, cmd,
                                                         out_path))
                err = subprocess.CalledProcessError(result.returncode, cmd,
                                                    stderr=stderr_text)
                err.out_path = out_path
                raise err
    except OSError as e:
        if not is_no_space(e):
            raise
        # The stage's output could not be finished (flushed, synced,
        # renamed into place): a full disk. Said naming the work/ file,
        # then the run's "stopped at <stage> for account <acct>" line —
        # it was a bare "[Errno 28] ..." with no file or stage (backlog
        # TJ-0106). The previous output is kept (lib/safe_write).
        what = no_space_text(e, _work_shown(out_path))
        _say("error", what, NO_SPACE_FIX[:1].upper() + NO_SPACE_FIX[1:]
             + ".", prog=_PROG)
        raise subprocess.CalledProcessError(
            2, cmd, stderr=f"{_PROG}: error: {what}") from None


def _work_shown(out_path: Path) -> str:
    """A stage's output file as the run's messages name it: work/<name>
    (the stages write into the project's work/ folder)."""
    p = Path(out_path)
    return f"{p.parent.name}/{p.name}" if p.parent.name else str(p)


def _stage_text_shown(text: str, cmd, out_path: Optional[Path] = None
                      ) -> str:
    """A failed stage's captured stderr as the console shows it: each
    input file the stage was given (inputs/<account>/<file>, passed by
    its absolute path) named as the run names inputs — the absolute
    path pushed the headline under a bare "Error:" (backlog TJ-0123);
    a write to its stdout a full disk refused names the work/ file the
    run redirects it to, `out_path` (TJ-0106). The .diag keeps the
    captured bytes."""
    if not text:
        return text
    for a in (str(c) for c in (cmd or [])):
        p = Path(a)
        if (p.is_absolute() and len(p.parts) > 3
                and p.parent.parent.name == "inputs" and a in text):
            text = text.replace(a, f"inputs/{p.parent.name}/{p.name}")
    if out_path is not None:
        text = text.replace("cannot write the output: ",
                            f"cannot write {_work_shown(out_path)}: ")
    return text


# Matches the per-file count lines `taxjson-brokerage` prints to
# stderr (e.g. `  margin_2025.csv: 142 tax objects`). Used by
# stage_account to echo the counts into the wrapper's console output
# so a keen eye can spot a 0-object parse of a non-empty file before
# downstream stages silently propagate the empty data.
_PARSE_COUNT_RE = re.compile(r'^\s+.+: \d+ tax objects\b')


def _echo_stage_stderr(text: str) -> None:
    """A captured stage's stderr shown on the console (a failed stage, a
    report tool's notes), each long line wrapped for the person at
    display time (lib/stage_msg.console_lines); the captured text
    itself is unchanged. A message this run already showed (another
    engine pass said it: the per-account gains, the blended pass with
    the sheltered context) is not shown again (_echo_captured once)."""
    run_state.current.echo_skip = False
    for line in text.splitlines():
        _echo_captured(line, indent="", file=sys.stderr, once=True)
    run_state.current.echo_skip = False


# A position short in the books, as an engine pass says it (lib/pipeline
# ATTENTION_SHORT): its symbol and account key the message, whichever
# pass and wording (a registered account's short is said by its own
# gains stage and again by a taxable pass that reads it as context).
_SHORT_KEY_RE = re.compile(r"(?:[\w./-]+: )?warning: ATTENTION: short: "
                           r"(?P<sym>\S+) \((?P<acct>[^()]+)\)")


def _echo_key(line: str) -> Tuple[str, ...]:
    """The run-wide key of a captured message line (run_state.current.shown): a
    short position by (symbol, account), any other by its text without
    the program name."""
    m = _SHORT_KEY_RE.match(line)
    if m:
        return ("short", m["sym"], m["acct"])
    from taxjson.lib.pipeline import TRANSFER_WINDOW_HEAD
    if TRANSFER_WINDOW_HEAD in line:
        # The run's one transfer-window warning (_say_transfer_windows)
        # gathers every pass's items: one key for all of them.
        return ("xferwin",)
    from taxjson.lib.out import relabel
    return ("echo", relabel(line.strip(), source=False))


def _echo_captured(line: str, indent: str = "  ", file=None,
                   source: bool = True, once: bool = False) -> None:
    """Show one captured stage line (a .diag's ATTENTION / UNBOOKED line
    or its continuation) on the run's console, wrapped for the person:
    the marker first line keeps its prefix, the rest is an indented
    headline + detail (lib/stage_msg.console_lines). The .diag and the
    .sum DIAGNOSTICS keep the one line. `source=False`: the run's own
    line (its program name is not shown). Shown to a person, the run's
    console rule applies whatever `indent` and `source` say: the label
    at column 0, continuations flush-left, one blank line after a
    message of more than one line, no stage program name
    (docs/output-style.md, The run's console)."""
    from taxjson.lib.stage_msg import console_lines, is_continuation
    from taxjson.lib.out import show, unmarked, width as _w
    file = sys.stdout if file is None else file
    line = unmarked(line)       # the end-of-run mark is the run's alone
    if once:
        # An engine message is shown once per run (owner request): the
        # gains stage, the raw-holdings pass, the blended pass and a
        # failed stage's echo all say the same lines. A continuation
        # follows its message's fate. The captured .diag / .sum keep
        # every pass's lines.
        if line[:1] in (" ", "\t") and not _PARSE_COUNT_RE.match(line):
            if run_state.current.echo_skip:
                return
        elif line.strip():
            key = _echo_key(line)
            if key in run_state.current.shown:
                run_state.current.echo_skip = True
                return
            run_state.current.shown.add(key)
            run_state.current.echo_skip = False
    if _w(file) > 0:
        indent, source = "", False
    if _run_concise(file):
        # One line per message (Essentials first): the continuation
        # lines and the detail wait for `tjs run --details` — an
        # error keeps its detail (the fix).
        from taxjson.lib.out import relabel as _relabel
        if is_continuation(line):
            if run_state.current.echo_last_error:
                show(console_lines(line, indent, stream=file,
                                   source=source), file, cont=True)
            else:
                run_state.current.details_hidden = True
            return
        run_state.current.echo_last_error = _relabel(
            line.strip()).startswith("Error:")
        if run_state.current.echo_last_error:
            show(console_lines(line, indent, stream=file, source=source),
                 file)
            return
        lines = console_lines(line, indent, stream=file, source=source,
                              concise=True)
        full = console_lines(line, indent, stream=file, source=source)
        if lines != full:
            run_state.current.details_hidden = True
        if lines:
            show(lines, file)
        return
    show(console_lines(line, indent, stream=file, source=source), file,
         cont=is_continuation(line))


def _work_path_problem(path: Path) -> str:
    return (f"{path} is a directory, not a work file — work/ holds only "
            f"files `tjs run` rebuilds: delete it and re-run.")


def _write_work_stamp(path: Path, text: str, *,
                      changed_only: bool = False) -> None:
    """Write a work/ bookkeeping file atomically; a directory (or an
    unwritable path) in its place is one line naming it (A2-1433).
    `changed_only`: a regular file there that holds `text` already is left
    as it is, neither written nor synced (lib/safe_write `holds`)."""
    from taxjson.lib.cli_diag import OutputWriteError
    from taxjson.lib.safe_write import holds
    if path.is_dir():
        _die_input(_work_path_problem(path))
    if changed_only and holds(path, text):
        return
    try:
        write_text_atomic(path, text)
    except OutputWriteError as e:
        _die_input(f"{e} — work/ holds only files `tjs run` "
                   f"rebuilds: fix or delete it and re-run.")


def _drop_work_file(path: Path) -> None:
    """Remove a work/ bookkeeping file if present; a directory in its
    place is one line naming it, not an IsADirectoryError (A2-1433)."""
    if path.is_dir() and not path.is_symlink():
        _die_input(_work_path_problem(path))
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        _die_input(f"cannot remove {path}: {e.strerror or e} — delete it "
                   f"and re-run.")
