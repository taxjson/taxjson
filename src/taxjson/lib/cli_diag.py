"""CLI diagnostics for taxjson bin tools.

Convention (docs/output-style.md, Messages): diagnostics go to stderr.
Shown to a person they start with their label — ``Warning: ...`` /
``Error: ...`` / ``Info: ...``; captured for a program (width 0: a
stage under ``taxjson run``, a command the checklist reads) they keep
the GNU ``<prog>: warning: ...`` / ``<prog>: error: ...`` /
``<prog>: note: ...`` bytes, where ``<prog>`` is the installed
console-script name (e.g. ``taxjson-merge``). lib/out.message makes the
choice. Report content stays on stdout. Exit codes: 0 = success
(including "no data"), 1 = the tool's finding (mismatch/violation/lint
problem), 2 = usage/environment error.

These helpers cover only bin/ CLI diagnostics. Parser-layer warnings in
lib/brokerages/ keep their bare ``warning:`` shape — they feed the
.diag/DIAGNOSTICS machinery in taxjson_run.py, whose marker matcher
accepts both the bare and the prog-prefixed form.
"""

import sys


def warn(prog: str, msg: str, details=()) -> None:
    """`Warning: <msg>` (+ indented `details`), wrapped to the house
    width by lib/out; captured by a program (width 0), the unwrapped
    `<prog>: warning: <msg>`."""
    from taxjson.lib.out import emit
    emit("warning", msg, prog=prog, details=details, file=sys.stderr)


def error(prog: str, msg: str, details=()) -> None:
    from taxjson.lib.out import emit
    emit("error", msg, prog=prog, details=details, file=sys.stderr)


def note(prog: str, msg: str, details=()) -> None:
    from taxjson.lib.out import emit
    emit("note", msg, prog=prog, details=details, file=sys.stderr)



# ---------------------------------------------------------------------------
# Unreadable inputs and engine refusals: one line, exit 2 (audit S070-23 /
# S079-10 / S029-20). A missing path, a directory, a non-UTF-8 file or bad
# JSON used to surface as a traceback (exit 1) in a dozen tools while their
# siblings printed `no such file` and exited 2.

class InputReadError(OSError):
    """An input file that exists but cannot be read as text (not UTF-8).
    An OSError, not a ValueError: the tools' data-error handlers (exit 1)
    must not swallow it — it is an environment error, exit 2."""


def not_utf8(path, exc: UnicodeDecodeError) -> InputReadError:
    """The error for a text input that is not UTF-8, naming the file —
    the bare UnicodeDecodeError carries no file name, so a latin-1
    ticker.map or a cp1252 re-saved broker export printed a traceback
    (or a one-liner) that never said WHICH file (audits S024-03 /
    S053-06)."""
    return InputReadError(
        f"{path}: not UTF-8 text (byte 0x{exc.object[exc.start]:02x} at "
        f"offset {exc.start}) — re-export it, or save it as UTF-8 in "
        f"your editor")


def read_text_utf8(path, encoding: str = "utf-8-sig") -> str:
    """A text input's contents; a non-UTF-8 file raises InputReadError
    naming it (see not_utf8)."""
    from pathlib import Path
    try:
        return Path(path).read_text(encoding=encoding)
    except UnicodeDecodeError as e:
        raise not_utf8(path, e) from None


class OutputWriteError(OSError):
    """An output path that cannot be written (a directory, no permission,
    a missing folder): 'cannot write <path>: ...', exit 2. The input
    wording ('cannot read <out>.part: is a directory') named the wrong
    file and the wrong direction (re-audit A2-0707)."""


def write_text_atomic(path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` through a temp file of this write's own
    in the same folder (`<path>.<random>.part`; the old contents stay
    until the new ones are complete). A failure raises OutputWriteError
    naming `path` and leaves no .part behind. The temp is created fresh
    and a symlink at either name is never written through
    (lib/safe_write)."""
    from pathlib import Path
    from taxjson.lib.safe_write import write_atomic
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        raise OutputWriteError(f"cannot write {path}: is a directory")
    try:
        write_atomic(path, text, encoding=encoding)
    except OSError as e:
        raise OutputWriteError(
            f"cannot write {path}: {e.strerror or e}") from None


def read_stdin_utf8() -> str:
    """Standard input as UTF-8 text (a BOM dropped), whatever the locale:
    sys.stdin decodes with the locale's codec, so a JSON document with
    'Société' piped under an ASCII locale was refused while the same file
    named on the command line was read (re-audit A2-1219). A stream with
    no byte buffer (a test's StringIO) is read as is."""
    buf = getattr(sys.stdin, "buffer", None)
    if buf is None:
        return sys.stdin.read()
    data = buf.read()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise not_utf8("<stdin>", e) from None


class InputContentError(ValueError):
    """A named input whose CONTENT a tool refuses (a ticker.map rename
    cycle, a work/ document of the wrong shape): ``str(err)`` is one line
    that names the file. A ValueError, so the orchestrator's data-error
    handlers keep catching it; every guard_main-wrapped tool reports it
    as ``<prog>: error: <line>`` with exit 2 — the stand-alone tools
    printed a traceback for the refusals `taxjson run` reports in one line
    (re-audit A2-0770 / A2-0793)."""


# Console-script names whose module name is not taxjson_<x> -> taxjson-<x>.
_PROG_ALIASES = {
    "taxjson_fees": "taxjson-fees-sum",
    "to_base_curr": "taxjson-to-base-curr",
    "fill_crypto_prices": "taxjson-fill-crypto",
    "xlsx_to_csv": "taxjson-xlsx-to-csv",
}


def console_prog(module: str) -> str:
    """The console-script name of a bin module (`taxjson.bin.taxjson_corp_
    actions` or `taxjson_corp_actions` -> `taxjson-corp-actions`)."""
    name = str(module).rsplit(".", 1)[-1]
    if name in _PROG_ALIASES:
        return _PROG_ALIASES[name]
    if name.startswith("taxjson_"):
        name = name[len("taxjson_"):]
    return "taxjson-" + name.replace("_", "-")


# Exit status of a tool whose reader went away (`taxjson-x | head`): the
# shell's 128 + SIGPIPE, with no traceback (re-audit A2-1426 / A2-1417).
BROKEN_PIPE_EXIT = 141


def tolerant_stdout() -> None:
    """Report text that the terminal's encoding cannot show degrades to
    '?' instead of a UnicodeEncodeError traceback: with
    PYTHONIOENCODING=ascii (or latin-1) every report that printed an em
    dash died mid-table (re-audit A2-0786). Top level only — a tool run
    in-process prints into its caller's buffer."""
    for stream, errors in ((sys.stdout, "replace"),
                           (sys.stderr, "backslashreplace")):
        try:
            stream.reconfigure(errors=errors)
        except (AttributeError, ValueError, OSError):
            pass


def _stdout_closed_exit() -> None:
    """The reader of our stdout went away (`taxjson trades | head -1`):
    stop quietly with the shell's SIGPIPE status, 141, and no traceback
    (re-audit A2-0785). stdout is pointed at /dev/null first so the
    interpreter's own flush at exit cannot fail again (exit 120)."""
    import os
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass
    raise SystemExit(BROKEN_PIPE_EXIT)


def _flush_stdout() -> None:
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _stdout_closed_exit()
    except (OSError, ValueError, AttributeError):
        pass


def _argparse_error(self, message: str) -> None:
    """argparse's usage error in the house style: the usage line, then
    the message as lib/out lays out an error — `Error: <message>` shown
    to a person, argparse's own `<prog>: error: <message>` bytes when
    captured (width 0) — exit 2."""
    from taxjson.lib.out import message as _message
    self.print_usage(sys.stderr)
    self.exit(2, "\n".join(_message("error", message, prog=self.prog,
                                    stream=sys.stderr)) + "\n")


def labelled_usage_errors() -> None:
    """Make every argparse parser of this process report a usage error
    with the house label (_argparse_error). Called at the top of the
    process only (run_top_level)."""
    import argparse
    argparse.ArgumentParser.error = _argparse_error


def run_top_level(prog, fn, *a, interrupt_note="", **kw):
    """Run a command's main at the top of the process (the `taxjson`
    entry point and every `taxjson-*` console script): Ctrl-C is one
    `<prog>: interrupted` line with exit 130 (re-audit A2-0782 /
    A2-1425: a 25-50 line KeyboardInterrupt traceback), and a closed
    stdout pipe exits 141 quietly (A2-0785), and a usage error carries
    the house label (labelled_usage_errors). Never used around a tool
    run in-process by another: its Ctrl-C must stop the whole run."""
    tolerant_stdout()
    labelled_usage_errors()
    from taxjson.lib import out as _out
    try:
        # A message of more than one line shown to a person owes one
        # blank line before whatever is printed next, a raw print()
        # included (lib/out.show, docs/output-style.md).
        with _out.settling_streams():
            r = fn(*a, **kw)
        _flush_stdout()
        return r
    except SystemExit as e:
        if e.code is not None and not isinstance(e.code, int):
            # The interpreter prints the exit text to stderr: after the
            # blank line a message there owes.
            _out.settle(sys.stderr)
        _flush_stdout()
        raise
    except BrokenPipeError:
        _stdout_closed_exit()
    except KeyboardInterrupt:
        try:
            sys.stdout.flush()
        except Exception:                                # noqa: BLE001
            pass
        print("", file=sys.stderr)          # end the ^C line
        # (both may be callables: `taxjson` knows its command only once
        # argv is parsed)
        prog = prog() if callable(prog) else prog
        note_ = interrupt_note() if callable(interrupt_note) \
            else interrupt_note
        error(prog, "interrupted" + (f" — {note_}" if note_ else ""))
        raise SystemExit(130)


def describe_input_error(exc: BaseException) -> str:
    """The one-line text for an input a tool could not read."""
    import json
    fn = getattr(exc, "filename", None)
    if isinstance(exc, (InputReadError, OutputWriteError)):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return f"no such file: {fn}"
    if isinstance(exc, IsADirectoryError):
        return f"cannot read {fn}: is a directory"
    if isinstance(exc, NotADirectoryError):
        return f"no such file: {fn}"
    if isinstance(exc, PermissionError):
        return f"permission denied: {fn}"
    if isinstance(exc, UnicodeDecodeError):
        return (f"cannot read input: not UTF-8 text (byte "
                f"0x{exc.object[exc.start]:02x} at offset {exc.start})")
    if isinstance(exc, json.JSONDecodeError):
        return f"cannot read input: not valid JSON ({exc})"
    if isinstance(exc, OSError) and fn:
        # ELOOP (a symlink loop), EIO, ENAMETOOLONG...: the file and the
        # reason, not a traceback (re-audit A2-0791).
        return f"cannot read {fn}: {exc.strerror or exc}"
    if isinstance(exc, RuntimeError):
        # pathlib's resolve() on a symlink loop (Python < 3.13).
        return f"cannot read input: {exc}"
    return str(exc)


_INPUT_ERRORS = (InputReadError, OutputWriteError, FileNotFoundError, IsADirectoryError, NotADirectoryError,
                 PermissionError, UnicodeDecodeError)


def _is_symlink_loop(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and "symlink loop" in str(exc).lower()


def _guard_flush() -> None:
    # A BrokenPipeError propagates to guard_main's handler.
    try:
        sys.stdout.flush()
    except (AttributeError, ValueError):
        pass


def guard_main(prog: str, *, value_errors: bool = False):
    """Decorator for a bin tool's main(): an unreadable input (any
    OSError, a symlink loop, not UTF-8, not JSON), an InputContentError
    (and, with `value_errors`, any engine/loader ValueError — the
    refusals taxjson-gains already reports this way) becomes
    ``<prog>: error: <one line>`` with exit 2 instead of a traceback.
    A reader that goes away (`| head`) is a quiet exit 141."""
    import functools
    import json

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                try:
                    r = fn(*a, **kw)
                except SystemExit:
                    _guard_flush()
                    raise
                _guard_flush()      # a broken pipe surfaces here, not
                return r            # at interpreter exit (exit 120)
            except BrokenPipeError:
                _stdout_closed_exit()       # quiet, exit 141
            except _INPUT_ERRORS + (json.JSONDecodeError, OSError) as e:
                error(prog, describe_input_error(e))
                raise SystemExit(2)
            except InputContentError as e:
                error(prog, str(e))
                raise SystemExit(2)
            except ValueError as e:
                if not value_errors:
                    raise
                error(prog, str(e))
                raise SystemExit(2)
            except RuntimeError as e:
                if not _is_symlink_loop(e):
                    raise
                error(prog, describe_input_error(e))
                raise SystemExit(2)
        return wrapper
    return deco


def tax_year(value: str) -> int:
    """argparse type for --year: an integer in 1900..next year, the range
    `taxjson init` and [settings] year enforce (audit S033-16: 0 meant
    'all history' and a 2-digit year silently matched nothing)."""
    import argparse
    from datetime import date
    try:
        y = int(str(value).strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a year")
    hi = date.today().year + 1
    if not 1900 <= y <= hi:
        raise argparse.ArgumentTypeError(
            f"{y} is not a plausible tax year (expected 1900..{hi})")
    return y
