"""The freshness-edge audit's tracer (TJ-2324; tests/test_freshness_edges.py).

On PYTHONPATH it loads in every Python process of a traced run (`-P`, the
children's flag, drops the script's folder, not PYTHONPATH). It is idle unless
TAXJSON_TRACE_OPENS names a folder; then each process writes one JSON line per
event to <folder>/<pid>.jsonl, in the order they happen:

  {"ev": "open", "path", "write", "fresh", "at", "via"}  an `open` audit event
                                        (io.open, os.open); `fresh` when made
                                        inside the freshness API (its records,
                                        lib/records'): not a stage's read;
                                        `at` / `via` the innermost two taxjson
                                        frames (`module:function:line`)
  {"ev": "rename", "src", "dst"}        os.rename / os.replace (a safe_write)
  {"ev": "list", "path"}                os.listdir / os.scandir / glob
  {"ev": "ask", "out", "inputs", "stale", "fn"}   freshness.needs_rebuild, mark_built
  {"ev": "start", "argv"}               the process's first line

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

_DIR = os.environ.get("TAXJSON_TRACE_OPENS")
if _DIR:
    import importlib.abc
    import json

    _log = open(os.path.join(_DIR, f"{os.getpid()}.jsonl"), "a", buffering=1,
                encoding="utf-8")
    _WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

    def _emit(**rec) -> None:
        _log.write(json.dumps(rec) + "\n")

    def _path(p):
        if isinstance(p, int):
            return None
        try:
            return os.path.realpath(os.fsdecode(p))
        except (TypeError, ValueError):
            return None

    _depth = [0]
    _ME = os.path.realpath(__file__)

    def _frames(skip=2, n=2):
        """The innermost `n` frames of the taxjson package (`module:function:
        line`, the module relative to the package's own folder, so a checkout
        under a folder named taxjson reads the same), past the freshness
        module and this tracer."""
        pkg = sys.modules.get("taxjson")
        if pkg is None or not getattr(pkg, "__file__", None):
            return (None,) * n
        root = os.path.dirname(os.path.realpath(pkg.__file__)) + os.sep
        found = []
        f = sys._getframe(skip)
        while f is not None and len(found) < n:
            fn = os.path.realpath(f.f_code.co_filename)
            if (fn.startswith(root) and fn != _ME
                    and fn[len(root):] != os.path.join("pipeline", "freshness.py")):
                found.append(f"{fn[len(root):]}:{f.f_code.co_name}:{f.f_lineno}")
            f = f.f_back
        return tuple(found) + (None,) * (n - len(found))

    def _hook(event, args):
        if event == "open":
            path, mode, flags = args
            p = _path(path)
            if p is None:
                return
            if mode is not None:
                write = any(c in str(mode) for c in "wax+")
            else:
                write = bool((flags or 0) & _WRITE)
            at, via = _frames()
            _emit(ev="open", path=p, write=write, fresh=_depth[0] > 0, at=at, via=via)
        elif event == "os.rename":
            _emit(ev="rename", src=_path(args[0]), dst=_path(args[1]))
        elif event in ("os.listdir", "os.scandir", "glob.glob"):
            p = _path(args[0]) if args and args[0] is not None else os.getcwd()
            if p is not None:
                _emit(ev="list", path=p)

    _emit(ev="start", argv=list(sys.argv), cwd=os.getcwd())
    sys.addaudithook(_hook)

    _TARGET = "taxjson.pipeline.freshness"

    # The freshness API: the reads made inside a call are its records. A
    # helper a stage imports (`_read_work_stamp`, a stage's stamp) is the
    # stage's read, judged. The records' own module is the API too (a wash
    # pass keeps its field in its wash file's record: TJ-1804); its hasher
    # is not (a wash pass hashes what it read with it).
    _API = ("needs_rebuild", "mark_built", "commit_records", "record_now",
            "unmark", "package_fingerprint")
    _RECORDS = "taxjson.lib.records"
    _RECORDS_API = ("read_record", "write_record", "set_record_field")

    def _wrap(module) -> None:
        def inside(fn):
            def call(*a, **kw):
                _depth[0] += 1
                try:
                    return fn(*a, **kw)
                finally:
                    _depth[0] -= 1
            return call

        if module.__name__ == _RECORDS:
            for name in _RECORDS_API:
                setattr(module, name, inside(getattr(module, name)))
            return
        for name in _API:
            setattr(module, name, inside(getattr(module, name)))
        for name in ("needs_rebuild", "mark_built"):
            fn = getattr(module, name)

            def traced(out, *inputs, _fn=fn, _name=name, **kw):
                r = _fn(out, *inputs, **kw)
                _emit(ev="ask", fn=_name, out=_path(out),
                      inputs=[_path(i) for i in inputs],
                      stale=r if _name == "needs_rebuild" else True,
                      at=_frames(1, 1)[0])
                return r
            setattr(module, name, traced)

    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name not in (_TARGET, _RECORDS):
                return None
            for f in sys.meta_path:
                if f is self or not hasattr(f, "find_spec"):
                    continue
                spec = f.find_spec(name, path, target)
                if spec is not None:
                    break
            else:
                return None
            run = spec.loader.exec_module

            def exec_module(module, _run=run):
                _run(module)
                _wrap(module)
            spec.loader.exec_module = exec_module
            return spec

    sys.meta_path.insert(0, _Finder())
