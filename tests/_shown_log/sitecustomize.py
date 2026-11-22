"""The shown log and the gains files' places (tests/_views_ab.py, step 6h).

On PYTHONPATH it loads in every Python process of a run (`-P`, the children's
flag, drops the script's folder, not PYTHONPATH). Idle unless one of these is
set:

  TAXJSON_TEST_SHOWN_LOG=DIR     wraps taxjson.model.money.shown and logs
      every call (step 6h(4a)): its tool context, its site (the caller's
      module in the package and line), the operand's repr, the places and
      the result's repr, one compact line each (`["s", ctx, site, repr,
      places, result]`), in call order, to the process's own file
      DIR/<pid>.<start>.jsonl. A call's ordinal at its site is its rank
      among the file's calls of that context and site, so two trees that
      differ only in shown's body log calls that pair one to one
      (tests/_views_ab compares them). What shown returns is not changed.
      A context is a stable path, the same in either tree: a step's first
      process is "0"; each tool a process runs through pipeline/dispatch
      `run_cmd` is its caller's context, ">", the tool and its rank among
      that caller's runs of it ("0>taxjson_gains#2"); a child process is
      the context that started it (TAXJSON_TEST_SHOWN_CTX), "|", its module
      and its rank among that context's children of that module
      (`_claim`: a file made once per rank in DIR/claims). Also written
      there: each tool context ("ctx": its id, its parent's, the tool, the
      inode of the file its stdout went to), each file under
      TAXJSON_TEST_SHOWN_ROOT a process opened for writing or renamed into
      place ("write", "rename"), each naming its context, so tests/_views_ab
      can tell which process wrote each output; the format log's records
      (tests/_fmt_log.py: "fmt", a fixed-point format that printed an exact
      half). Each process it installs in writes a "proc" record (its
      context, and whether its tree has the format log's sink; flushed at
      once, so a process that dies later leaves it: TJ-3396) and, at
      exit, an "exit" record with its count of calls: tests/_views_ab
      refuses a book a process of which left fewer calls or no exit record.
      One whose taxjson is on the path but where the shim cannot install
      says so on stderr and writes an "error" record: tests/_views_ab
      refuses a book whose run left no proc record, or an error (TJ-3364).
  TAXJSON_TEST_GAINS_PLACES=N    the money of the files a 4-place writer
      makes written at N places, for telling a tie of the engine's own value
      from one such a file made (`ties --deep`): model/money round_floats'
      default (the gains JSON's one writer), lib/cost_timeline's 4-place
      money (its `round_half_up(x, 4)` calls, lib/numeric's, which it
      imports when it writes) and the writers WIDENED names, each by the
      source of its call (TJ-3370): its columns (`co_positions`); a call it
      cannot tell is an "error" record (tests/_views_ab refuses the book),
      never a guess.

A record names the book (TAXJSON_TEST_SHOWN_BOOK, which _views_ab sets for
each book) and the view: the `tjs` command line of the outermost tjs process,
which this sets as TAXJSON_TEST_SHOWN_VIEW for the processes it starts.

The next sitecustomize on sys.path (the system's: Ubuntu ships one; another
taxjson test shim) is loaded first, by tests/_shimchain.py: two shims chain
without recursing (TJ-2772)."""
import os
import sys


def _load_next():
    import importlib.util
    mod = sys.modules.get("_taxjson_shimchain")
    if mod is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_shimchain.py")
        spec = importlib.util.spec_from_file_location("_taxjson_shimchain", path)
        mod = sys.modules["_taxjson_shimchain"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    mod.load_next(__file__)


_load_next()

LOG = "TAXJSON_TEST_SHOWN_LOG"
PLACES = "TAXJSON_TEST_GAINS_PLACES"
BOOK = "TAXJSON_TEST_SHOWN_BOOK"
VIEW = "TAXJSON_TEST_SHOWN_VIEW"
CTX = "TAXJSON_TEST_SHOWN_CTX"
ROOT = "TAXJSON_TEST_SHOWN_ROOT"
_TJS = "taxjson.bin.taxjson_run"
_TIMELINE = os.sep + os.path.join("lib", "cost_timeline.py")
# The other writers --places widens (TJ-3370): {money's function: (the
# caller's module, or None for any module of a taxjson package, the places
# it writes, the calls, by their source)}. None for the engine, whose
# modules move (PLAN.md step 10): its calls are each one site in the
# package (tests/test_views_ab.py).
WIDENED = {
    # The US engine's §263(h) capitalized carrying cost in the gains entry.
    "present": (None, 4, ("M.present(_cap_263h, 4)", "M.present(_cap, 4)")),
    # The fx cash close book's cost, carried into the next year.
    "units": (os.path.join("lib", "fx_cash_v2.py"), 6, ("M.units(h * avg, 6)",)),
}


def _view() -> str:
    """This process's view: the one its tjs parent named, else its own `tjs`
    command line (the run's folder name first: a book runs `run` in two
    years), else its module or script."""
    named = os.environ.get(VIEW)
    if named:
        return named
    argv = list(getattr(sys, "orig_argv", sys.argv))
    if _TJS in argv:
        rest = argv[argv.index(_TJS) + 1:]
        view = f"{os.path.basename(os.getcwd())}: {' '.join(rest)}"
        os.environ[VIEW] = view
        return view
    if "-m" in argv[:-1]:
        return "?" + argv[argv.index("-m") + 1]
    return "?" + os.path.basename(argv[1] if len(argv) > 1 else argv[0])


def _module() -> str:
    """This process's module (`taxjson.bin._entry` with its tool word), the
    `-c` bootstrap's module (lib/dispatch python_module_argv), or its
    script's name: never a path, which would differ between two trees."""
    argv = [str(a) for a in getattr(sys, "orig_argv", sys.argv)]
    rest = argv[1:]
    if "-m" in rest[:-1]:
        mod, tail = rest[rest.index("-m") + 1], rest[rest.index("-m") + 2:]
    elif "-c" in rest[:-1]:
        tail = rest[rest.index("-c") + 2:]
        mod, tail = (tail[0], tail[1:]) if tail else ("-c", [])
    else:
        mod, tail = os.path.basename(rest[0]) if rest else "?", []
    return f"{mod}:{tail[0]}" if mod == "taxjson.bin._entry" and tail else mod


def _claim(folder: str, key: str) -> int:
    """The first rank of `key` not claimed yet in this log (a file made
    once per rank): a child's rank among its parent context's children of
    its module, the order they started in."""
    import hashlib
    claims = os.path.join(folder, "claims")
    os.makedirs(claims, exist_ok=True)
    digest = hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()[:24]
    n = 0
    while True:
        try:
            os.close(os.open(os.path.join(claims, f"{digest}.{n}"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            return n
        except FileExistsError:
            n += 1


def _call_text(frame, name: str):
    """The source of the call of money's `name` that `frame` is making, by
    its columns (`co_positions`); None when it cannot be told."""
    import linecache
    code = frame.f_code
    try:
        l0, l1, c0, c1 = list(code.co_positions())[frame.f_lasti // 2]
        lines = [linecache.getline(code.co_filename, n) for n in range(l0, l1 + 1)]
        return lines[0][c0:c1] if l0 == l1 else lines[0][c0:] + "".join(lines[1:-1]) + lines[-1][:c1]
    except (IndexError, TypeError, ValueError):
        return None


def _install_places(money, numeric, n: str) -> None:
    """The gains JSON (round_floats' default), the cost timeline (its
    `round_half_up(x, 4)` calls, lib/numeric's, which it imports when it
    writes) and WIDENED's writers at `n` places."""
    wider = int(n)
    defaults = money.round_floats.__defaults__
    money.round_floats.__defaults__ = (wider,) + tuple(defaults[1:])
    orig = numeric.round_half_up

    def round_half_up(value, places=4):
        if places == 4 and sys._getframe(1).f_code.co_filename.endswith(_TIMELINE):
            places = wider
        return orig(value, places)

    round_half_up.__wrapped__ = orig
    numeric.round_half_up = round_half_up

    import linecache

    def widened(name, module, at, calls):
        fn = getattr(money, name)
        suffix = os.sep + module if module else None
        package = os.sep + "taxjson" + os.sep

        def wide(x, places):
            if places == at:
                f = sys._getframe(1)
                path = f.f_code.co_filename
                if ((path.endswith(suffix) if suffix else package in path)
                        and any(c in linecache.getline(path, f.f_lineno) for c in calls)):
                    text = _call_text(f, name)
                    if text is None:
                        where = module or path[path.rfind(package) + 1:]
                        _error(f"tests/_shown_log: --places: cannot tell which call of M.{name} runs at "
                               f"{where}:{f.f_lineno} (Python {sys.version.split()[0]}): not widened",
                               "--places could not tell a call")
                    elif " ".join(text.split()) in calls:
                        places = wider
            return fn(x, places)

        wide.__wrapped__ = fn
        wide.__doc__ = fn.__doc__
        setattr(money, name, wide)

    for name, (module, at, calls) in WIDENED.items():
        widened(name, module, at, calls)


def _install_log(money, dispatch, folder: str, fmt_sink: bool = False) -> None:
    import atexit
    import json
    import threading
    import time

    pkg = os.path.dirname(os.path.dirname(os.path.abspath(money.__file__)))
    book = os.environ.get(BOOK, "?")
    view = _view()
    root = os.environ.get(ROOT)
    root = os.path.realpath(root) if root else None
    logdir = os.path.realpath(folder)
    parent = os.environ.get(CTX)
    if parent:
        mod = _module()
        base = f"{parent}|{mod}#{_claim(folder, f'{view}|{parent}|{mod}')}"
    else:
        base = "0"
    os.environ[CTX] = base
    state = {"out": None, "pid": os.getpid(), "calls": 0}
    ranks = {}                   # (parent context, tool): the runs so far
    local = threading.local()
    lock = threading.Lock()

    def stack() -> list:
        s = getattr(local, "stack", None)
        if s is None:
            s = local.stack = [base]
        return s

    def emit(line: str) -> None:
        # Under the lock: the file opened once per process, buffered,
        # flushed at exit.
        if state["out"] is None or state["pid"] != os.getpid():
            os.makedirs(folder, exist_ok=True)
            state["pid"] = os.getpid()
            state["out"] = open(os.path.join(folder, f"{os.getpid()}.{time.time_ns()}.jsonl"), "a",
                                encoding="utf-8", buffering=1 << 16)
        state["out"].write(line)

    def write(rec: dict) -> None:
        rec.update(book=book, view=view, ctx=stack()[-1])
        line = json.dumps(rec) + "\n"
        local.busy = True
        try:
            with lock:
                emit(line)
        finally:
            local.busy = False

    def site(frame) -> str:
        path = os.path.abspath(frame.f_code.co_filename)
        rel = os.path.relpath(path, pkg) if path.startswith(pkg + os.sep) else os.path.basename(path)
        return f"{rel.replace(os.sep, '/')}:{frame.f_lineno}"

    orig = money.shown

    def shown(x, places):
        out = orig(x, places)
        try:
            line = json.dumps(["s", stack()[-1], site(sys._getframe(1)), repr(x)[:80],
                               places if type(places) is int else repr(places)[:20], repr(out)[:80]]) + "\n"
            local.busy = True
            try:
                with lock:
                    emit(line)
                    state["calls"] += 1
            finally:
                local.busy = False
        except (TypeError, ValueError, OSError, RuntimeError):
            pass                 # the log never changes a call
        return out

    shown.__wrapped__ = orig
    shown.__doc__ = orig.__doc__
    money.shown = shown

    def finish():
        write({"kind": "exit", "calls": state["calls"]})
        with lock:
            if state["out"] is not None:
                state["out"].flush()

    atexit.register(finish)

    # Which process wrote which file: each run_cmd a tool context of its
    # own (its children's too, through CTX), and every file under the
    # project opened for writing or renamed into place, by the context.
    run_cmd = dispatch.run_cmd

    def tool_run_cmd(cmd, **kw):
        out = kw.get("stdout")
        ino = None
        try:
            if out is not None:
                st = os.fstat(out.fileno())
                ino = [st.st_dev, st.st_ino]
        except (AttributeError, OSError, ValueError):
            pass
        tool = dispatch.tool_module(cmd)
        name = tool[0] if tool else " ".join(str(c) for c in cmd[:3])
        short = tool[0].rsplit(".", 1)[-1] if tool else os.path.basename(str(cmd[0])) if cmd else "?"
        caller = stack()[-1]
        with lock:
            rank = ranks.get((caller, short), 0)
            ranks[(caller, short)] = rank + 1
        cid = f"{caller}>{short}#{rank}"
        write({"kind": "ctx", "id": cid, "parent": caller, "tool": name, "ino": ino,
               "capture": bool(kw.get("capture_output")), "t": time.time_ns()})
        stack().append(cid)
        before = os.environ.get(CTX)
        os.environ[CTX] = cid
        try:
            return run_cmd(cmd, **kw)
        finally:
            stack().pop()
            os.environ[CTX] = before if before is not None else base

    tool_run_cmd.__wrapped__ = run_cmd
    tool_run_cmd.__doc__ = run_cmd.__doc__
    dispatch.run_cmd = tool_run_cmd

    def proc():
        # The shim ran here: a book whose run left none had it idle.
        # Flushed now: a process that dies later leaves this, not nothing.
        write({"kind": "proc", "pctx": base, "fmt_sink": fmt_sink})
        with lock:
            state["out"].flush()

    if root is None:
        sys._taxjson_fmt_log = lambda rec: write(dict(rec, kind="fmt"))
        proc()
        return
    writes = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def under(path):
        try:
            p = os.path.realpath(os.fsdecode(os.fspath(path)))
        except (TypeError, ValueError, OSError):
            return None
        if p.startswith(logdir + os.sep):
            return None
        return p if p.startswith(root + os.sep) else None

    def audit(event, args):
        if getattr(local, "busy", False) or event not in ("open", "os.rename"):
            return
        try:
            if event == "open":
                path, mode, flags = args
                if isinstance(path, int) or path is None:
                    return
                if isinstance(mode, str):
                    if not any(c in mode for c in "wax+"):
                        return
                elif not (isinstance(flags, int) and flags & writes):
                    return
                p = under(path)
                if p:
                    write({"kind": "write", "path": os.path.relpath(p, root), "t": time.time_ns()})
            else:
                src, dst = args[0], args[1]
                d = under(dst)
                if d:
                    s = os.path.realpath(os.fsdecode(os.fspath(src)))
                    try:
                        st = os.stat(s)
                        ino = [st.st_dev, st.st_ino]
                    except OSError:
                        ino = None
                    write({"kind": "rename", "src": os.path.relpath(s, root) if s.startswith(root + os.sep) else s,
                           "path": os.path.relpath(d, root), "ino": ino, "t": time.time_ns()})
        except (TypeError, ValueError, LookupError, OSError, RuntimeError):
            pass                 # the log never stops the run

    sys.addaudithook(audit)
    sys._taxjson_fmt_log = lambda rec: write(dict(rec, kind="fmt"))
    proc()


_SAID = set()


def _error(text: str, what: str) -> None:
    """Said once on stderr, and an "error" record in the log folder, which
    tests/_views_ab refuses the book for, named by `what`."""
    import json
    if text in _SAID:
        return
    _SAID.add(text)
    print(text, file=sys.stderr)
    folder = os.environ.get(LOG)
    if folder:
        try:
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"{os.getpid()}.error.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "error", "error": text, "what": what, "book": os.environ.get(BOOK, "?"),
                                    "view": _view(), "ctx": os.environ.get(CTX, "")}) + "\n")
        except OSError:
            pass


def _not_installed(error) -> None:
    """The shim could not install in a process that imports taxjson: said
    on stderr, and as an "error" record in the log folder, which
    tests/_views_ab refuses the book for (TJ-3364)."""
    _error(f"tests/_shown_log: not installed: {type(error).__name__}: {error}", "the shown log did not install")


def _install() -> None:
    import importlib.util
    try:
        taxjson = importlib.util.find_spec("taxjson")
    except (ImportError, ValueError):
        taxjson = None
    if taxjson is None:          # not a taxjson process's path
        return
    try:
        try:
            import taxjson.pipeline.dispatch as dispatch
        except ModuleNotFoundError as e:
            # A views A/B's base from before step 8j (but after taxjson.model
            # came, 2026-11-10, which the shim reads too): run_cmd was
            # lib/dispatch's (TJ-3541, TJ-3548). A module pipeline/dispatch
            # itself misses is named as it is.
            if e.name != "taxjson.pipeline.dispatch":
                raise
            import taxjson.lib.dispatch as dispatch
        import taxjson.lib.numeric as numeric
        import taxjson.model.money as money
        fmt_sink = importlib.util.find_spec("taxjson._fmtlog") is not None
        if os.environ.get(PLACES):
            _install_places(money, numeric, os.environ[PLACES])
        if os.environ.get(LOG):
            _install_log(money, dispatch, os.environ[LOG], fmt_sink)
    except (ImportError, AttributeError, TypeError, ValueError, OSError) as e:
        _not_installed(e)


if os.environ.get(LOG) or os.environ.get(PLACES):
    _install()
