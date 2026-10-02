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
    return str(exc)


_INPUT_ERRORS = (InputReadError, OutputWriteError, FileNotFoundError, IsADirectoryError, NotADirectoryError,
                 PermissionError, UnicodeDecodeError)


def guard_main(prog: str, *, value_errors: bool = False):
    """Decorator for a bin tool's main(): an unreadable input (and, with
    `value_errors`, any engine/loader ValueError — the refusals
    taxjson-gains already reports this way) becomes
    ``<prog>: error: <one line>`` with exit 2 instead of a traceback."""
    import functools
    import json

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except _INPUT_ERRORS + (json.JSONDecodeError,) as e:
                error(prog, describe_input_error(e))
                raise SystemExit(2)
            except ValueError as e:
                if not value_errors:
                    raise
                error(prog, str(e))
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
