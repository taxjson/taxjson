"""A later clock for the golden books (tests/_golden.py, `clock_days`), and
a clock that stands still for the byte proof (scripts/style/byte_proof.py).

On PYTHONPATH it loads in every Python process of a run (`-P`, the children's
flag, drops the script's folder, not PYTHONPATH). It is idle unless
TAXJSON_TEST_CLOCK_DAYS names a whole number of days, or TAXJSON_TEST_CLOCK_AT
an instant (whole seconds since the epoch). With DAYS the process's clock runs
that many days ahead; with AT it reads that instant, always (DAYS is then
ignored): two runs of one tree see one time, so their bytes agree without a
mask. Either moves time.time / time_ns and what reads them without a time
given (localtime, gmtime, ctime, strftime), and datetime's today / now /
utcnow. A file's mtime is not moved: a cache written by the run looks that much
older, as it would to a person running the book later (the byte proof sets the
project's file times to its instant itself).

datetime.date and datetime.datetime are replaced by subclasses. isinstance and
issubclass against those two accept the real classes (a date the C code makes,
such as datetime.date(), stays a date); a class derived from them keeps its
own checks; a real date pickles as the replacement. pandas is loaded with the
real classes in place, when something imports it: its compiled types subclass
them as they load, and crash on these (taxjson never asks pandas the time).

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

_DAYS = os.environ.get("TAXJSON_TEST_CLOCK_DAYS", "").strip()
_AT = os.environ.get("TAXJSON_TEST_CLOCK_AT", "").strip()
if _AT or (_DAYS and _DAYS != "0"):
    import copyreg
    import datetime as _dt
    import importlib.abc
    import time as _time

    _real_time, _real_time_ns = _time.time, _time.time_ns
    _real_localtime, _real_gmtime = _time.localtime, _time.gmtime
    _real_ctime, _real_strftime = _time.ctime, _time.strftime

    if _AT:
        _FIXED = int(_AT)

        def _now() -> float:
            return float(_FIXED)

        _time.time_ns = lambda: _FIXED * 1_000_000_000
    else:
        _SHIFT = int(_DAYS) * 86400

        def _now() -> float:
            return _real_time() + _SHIFT

        _time.time_ns = lambda: _real_time_ns() + _SHIFT * 1_000_000_000
    _time.time = _now
    _time.localtime = lambda secs=None: _real_localtime(_now() if secs is None else secs)
    _time.gmtime = lambda secs=None: _real_gmtime(_now() if secs is None else secs)
    _time.ctime = lambda secs=None: _real_ctime(_now() if secs is None else secs)
    _time.strftime = lambda fmt, t=None: _real_strftime(fmt, _time.localtime() if t is None else t)

    _RealDate, _RealDatetime = _dt.date, _dt.datetime
    _REAL = {}      # a replacement class -> the real class its checks accept

    class _DateMeta(type):
        def __instancecheck__(cls, obj):
            real = _REAL.get(cls)
            return isinstance(obj, real) if real else type.__instancecheck__(cls, obj)

        def __subclasscheck__(cls, sub):
            real = _REAL.get(cls)
            return issubclass(sub, real) if real else type.__subclasscheck__(cls, sub)

    class date(_RealDate, metaclass=_DateMeta):
        @classmethod
        def today(cls):
            return cls.fromtimestamp(_now())

    class datetime(_RealDatetime, metaclass=_DateMeta):
        @classmethod
        def today(cls):
            return cls.fromtimestamp(_now())

        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(_now(), tz)

        @classmethod
        def utcnow(cls):
            return cls.fromtimestamp(_now(), _dt.timezone.utc).replace(tzinfo=None)

    _REAL.update({date: _RealDate, datetime: _RealDatetime})
    date.__module__ = datetime.__module__ = "datetime"
    # pickle finds a class by its module's name for it: datetime.date is now
    # the replacement, so a real one is written as one. Protocol 4's state
    # keeps a datetime's fold (TJ-2783). copy and deepcopy read copyreg too:
    # a real date or datetime is immutable, so a copy is the same object, of
    # its own class, as it would be without the shim.
    copyreg.pickle(_RealDate, lambda d: (date, d.__reduce_ex__(4)[1]))
    copyreg.pickle(_RealDatetime, lambda d: (datetime, d.__reduce_ex__(4)[1]))
    import copy as _copy
    for _table, _same in (("_copy_dispatch", lambda d: d), ("_deepcopy_dispatch", lambda d, memo: d)):
        for _cls in (_RealDate, _RealDatetime):
            getattr(_copy, _table, {})[_cls] = _same   # 3.12 and 3.13 have both tables

    class _RealClassesFor(importlib.abc.MetaPathFinder):
        """pandas's own spec and loader, its exec_module run with the real
        classes in place: the loader's resource reader, get_data and the rest
        stay as they are (TJ-2785)."""

        def find_spec(self, name, path, target=None):
            if name != "pandas":
                return None
            for finder in sys.meta_path:
                if finder is self or not hasattr(finder, "find_spec"):
                    continue
                spec = finder.find_spec(name, path, target)
                if spec is not None:
                    run = spec.loader.exec_module

                    def exec_module(module, _run=run):
                        _dt.date, _dt.datetime = _RealDate, _RealDatetime
                        try:
                            _run(module)
                        finally:
                            _dt.date, _dt.datetime = date, datetime
                    spec.loader.exec_module = exec_module
                    return spec
            return None

    sys.meta_path.insert(0, _RealClassesFor())
    _dt.date, _dt.datetime = date, datetime
