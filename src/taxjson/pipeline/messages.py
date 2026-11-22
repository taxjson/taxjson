"""How the run and the commands say something and stop: _die, _say, _step."""
import argparse
import sys
from pathlib import Path
from typing import Any, List, Optional

from taxjson.pipeline import run_state


def _out_wrap(text: str, indent: str = "", hang: Optional[str] = None,
              file=None) -> List[str]:
    """lib/out.wrap for the run console (stdout by default)."""
    from taxjson.lib.out import wrap
    return wrap(text, indent=indent, hang=hang,
                stream=sys.stdout if file is None else file)


def _out_relpath(path: Path, root: Path) -> str:
    from taxjson.lib.out import relpath
    return relpath(path, root)


def _step(text: str, file=None) -> None:
    """`==> <text>`: a step the run is doing, on its console
    (docs/output-style.md, The run's console: short, plain, one verb
    form — the table there lists every step)."""
    from taxjson.lib.out import show, width, wrap
    file = sys.stdout if file is None else file
    # Shown to a person, a wrapped step continues flush-left (a blank
    # line after it, lib/out.show); captured, two spaces in.
    show(wrap("==> " + text, None, "", "  " if width(file) <= 0 else "",
              stream=file), file)


def _run_concise(file=None) -> bool:
    """True on `tjs run`'s console shown to a person without
    --details: each message is one line (docs/output-style.md,
    Essentials first); captured output (width 0) keeps every byte."""
    from taxjson.lib.out import width as _w
    return (_current_cmd() == "run" and not run_state.current_details
            and _w(sys.stdout if file is None else file) > 0)


def _say(kind: str, text: str, *details: str, prog: Optional[str] = None,
         indent: str = "", file=None, short: Optional[str] = None,
         cmd: Optional[str] = None, whole: bool = False) -> None:
    """A run-console message in the house style: `[<prog>: ]<kind>:
    <headline>` and indented `details` (stderr by default). kind: note |
    warning | attention | error. `indent` nests the captured form
    (width 0) only: shown to a person the label starts the line, its
    continuations are flush-left and a message of more than one line is
    followed by one blank line (The run's console). On the run's
    console without --details (_run_concise) a note or warning is one
    line: `short` when given, else its headline and the command its
    detail names, or `cmd` (lib/out.concise_line); an error keeps
    its detail (the fix), and so does `whole` (lib/out.emit)."""
    from taxjson.lib.stage_msg import message_lines
    from taxjson.lib.out import concise_line
    from taxjson.lib.out import label, show, width as _w
    file = sys.stderr if file is None else file
    if _w(file) > 0:
        indent = ""
    if kind != "error" and not whole and _run_concise(file):
        lab = label("warning" if kind == "attention" else kind,
                    stream=file)
        w = _w(file)
        if kind == "attention":
            from taxjson.lib.out import shown_topic
            text = shown_topic(text)
        one = (f"{lab}{short}" if short else
               concise_line(f"{lab}{text}", details, w, cmd=cmd))
        if details or short or len(one) < len(lab) + len(text):
            run_state.current.details_hidden = True
        # One line, cut at the width; the third of a kind folded.
        from taxjson.lib.out import concise_show
        concise_show(one, file)
        return
    show(message_lines(kind, text, details, prog=prog, indent=indent,
                       stream=file), file)

# The command's name in every message and usage line, however it was
# invoked (`taxjson` still runs it): one name for people to read.
_PROG = "tjs"


def _current_cmd() -> str:
    """The command running: the one main's dispatch loop set
    (run_state.current_cmd) while `_main` is on the stack; for an in-process
    call of a `cmd_*` function (tests, a GUI), the outermost one's
    command name — not the command an earlier call left set (backlog
    TJ-0354: a t1135 refusal said "tjs reconcile-slips: error");
    else run_state.current_cmd."""
    f = sys._getframe(1)
    outer = ""
    while f is not None:
        module = f.f_globals.get("__name__", "")
        if module.startswith(("taxjson.cli.", "taxjson.pipeline.")):
            name = f.f_code.co_name
            if name == "_main":
                return run_state.current_cmd
            if name.startswith("cmd_"):
                outer = name
        f = f.f_back
    return outer[4:].replace("_", "-") if outer else run_state.current_cmd


def _cmd_prog() -> str:
    """`tjs <command>` while a command runs (the prog of its
    messages), else `tjs`."""
    cmd = _current_cmd()
    return f"{_PROG} {cmd}" if cmd else _PROG


def _die(msg: str, *details: str) -> None:
    """Stop the command, exit 1: `tjs <cmd>: error: <msg>`, then each
    of `details` as an indented paragraph (docs/output-style.md). Keep
    `msg` a short headline — what was refused, about what — and put the
    why and the fix in `details`. SystemExit carries the text, as
    sys.exit(msg) did."""
    from taxjson.lib.out import fail
    fail(msg, prog=_cmd_prog(), details=details, code=1)


def _die_input(msg: str, *details: str) -> None:
    """Like _die, with exit 2: a named input (or output) that is missing
    or cannot be read or written, or a usage error. Exit 1 stays the
    command's FINDING (drift, a handoff problem, PII found, a lint hit),
    so cron and the checklist can tell 'broken input' from 'finding' —
    the cli_diag convention every console script follows (re-audit
    A2-0164)."""
    from taxjson.lib.out import fail
    fail(msg, prog=_cmd_prog(), details=details, code=2)


def _say_once(key: Any, kind: str, text: str, *details: str,
              **kw: Any) -> None:
    """_say, unless this run already showed the message keyed `key`."""
    if key in run_state.current.shown:
        return
    run_state.current.shown.add(key)
    _say(kind, text, *details, **kw)


def _details(args: argparse.Namespace) -> bool:
    """`--details` given (docs/output-style.md, Essentials first)."""
    return bool(getattr(args, "details", False))
