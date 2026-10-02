"""GNU-style CLI diagnostics for taxjson bin tools.

Convention (AUDIT-2026-07-ui §1C): diagnostics go to stderr as
``<prog>: warning: ...`` / ``<prog>: error: ...`` / ``<prog>: note: ...``
with a lowercase severity word, where ``<prog>`` is the installed
console-script name (e.g. ``taxjson-merge``). Report content stays on
stdout. Exit codes: 0 = success (including "no data"), 1 = the tool's
finding (mismatch/violation/lint problem), 2 = usage/environment error.

These helpers cover only bin/ CLI diagnostics. Parser-layer warnings in
lib/brokerages/ keep their bare ``warning:`` shape — they feed the
.diag/DIAGNOSTICS machinery in taxjson_run.py, whose marker matcher
accepts both the bare and the prog-prefixed form.
"""

import sys


def warn(prog: str, msg: str) -> None:
    print(f"{prog}: warning: {msg}", file=sys.stderr)


def error(prog: str, msg: str) -> None:
    print(f"{prog}: error: {msg}", file=sys.stderr)


def note(prog: str, msg: str) -> None:
    print(f"{prog}: note: {msg}", file=sys.stderr)



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
    """Write `text` to `path` through `<path>.part` in the same folder
    (the old contents stay until the new ones are complete). A failure
    raises OutputWriteError naming `path` and leaves no .part behind."""
    import os
    from pathlib import Path
    path = Path(path)
    if path.is_dir():
        raise OutputWriteError(f"cannot write {path}: is a directory")
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, path)
    except OutputWriteError:
        raise
    except OSError as e:
        raise OutputWriteError(
            f"cannot write {path}: {e.strerror or e}") from None
    finally:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass


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


def silence_stdout() -> None:
    """Point the stdout file descriptor at /dev/null after a broken pipe,
    so the interpreter's final flush does not print 'Exception ignored
    ... BrokenPipeError' (and exit 120)."""
    import os
    try:
        fd = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return                              # a StringIO (in-process)
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, fd)
        os.close(devnull)
    except OSError:
        pass


def tolerant_stdout() -> None:
    """Replace, rather than crash on, characters the terminal's encoding
    cannot show: under an ASCII locale (PYTHONIOENCODING=ascii, LANG=C on
    an old system) a '—' or '→' in a report raised UnicodeEncodeError
    and exit 1 (re-audit A2-1427)."""
    out = sys.stdout
    enc = (getattr(out, "encoding", None) or "").lower().replace("-", "")
    if enc in ("utf8", "utf8sig") or not hasattr(out, "reconfigure"):
        return
    try:
        out.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


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


def _flush_stdout() -> None:
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
                    _flush_stdout()
                    raise
                _flush_stdout()     # a broken pipe surfaces here, not
                return r            # at interpreter exit (exit 120)
            except BrokenPipeError:
                silence_stdout()
                raise SystemExit(BROKEN_PIPE_EXIT)
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
