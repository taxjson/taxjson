"""The money A/B (step 6f): two trees' gains engines on the same books.

    PYTHONPATH=DIR TAXJSON_WIDTH=0 python3 tests/_money_oracle.py dump OUT
        [--src DIR] [--books N] [--suite MODULE ...]
    python3 tests/_money_oracle.py diff BASE NEW [--strict] [--show N]
        [--json OUT]

(--suite runs the modules as the suite does: from tests/.)

`dump` runs each book through compute_gains with the taxjson of DIR, a
tree's src first on the path (the base: `git archive <commit> | tar -x
-C <dir>`, then PYTHONPATH=<dir>/src; --src DIR refuses to run if the
taxjson imported is not DIR's), and writes each answer, its stderr and
its exception to OUT (a pickle). With --suite, also each compute_gains call the named test
modules make, as it returns to the test. The books and the test modules
are this tests/ folder's, the same for both trees: only the engines
differ. `diff` compares two dumps book by book, and each difference
falls in one class:

  noise      a figure off by at most 1e-9 of its size (1e-9 at least)
             and by less than half a unit of the 4th place, written the
             same in the gains JSON (round_floats), or a quantity
             (written unrounded); a per-share figure (DERIVED) whose
             cost and quantity are noise and which is, in each tree,
             its own cost over its quantity;
  tie        noise written differently: a figure at a half-unit of the
             4th place (round_floats' rounding), or one the engine cuts
             itself (CUT_KEYS) one unit apart at its places (the cut at
             a half): a flip's own moves, listed;
  text       a number in a message or trace one unit apart in its last
             printed place: a printed half-unit, or a real move of that
             unit (text alone cannot tell them apart): each is read;
  form       the same value written differently (an int 0 against 0.0);
  typeerror  the new tree stopped at a money site (counted by its
             innermost src line);
  bug        anything else: a figure, a text or a shape that moved, or
             an exception one tree raised and the other did not.

The books: the engine golden books (tests/golden, kind "engine") and the
three fuzzers' books (test_engine_invariants, test_transfer_fuzz,
test_settle_straddle_fuzz) with trace, cost-timeline and year-end
variants, through both engines. The fuzz books end mostly flat: a change
to the inventory or a cut the books rarely reach needs --suite, and a
book of its own.

A change meant to move nothing expects `same` alone: `diff --strict`
exits 1 on any other class, `diff` on a bug.
"""
import argparse
import copy
import functools
import io
import json
import math
import pickle
import re
import sys
import time
import traceback
import unittest
from collections import Counter
from collections.abc import Sequence
from contextlib import redirect_stderr
from decimal import Decimal
from pathlib import Path

import _hermetic  # noqa: F401  (a synthetic HOME, offline)
import _paths
from taxjson.lib import once
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.engine.by_country import get_tax_rules
from taxjson.model.transaction import TaxTransaction
from taxjson.model import money as M
from taxjson.model.money import _QTY_FIELDS, round_half_up

HERE = _paths.TESTS
SRC = Path(once.__file__).resolve().parents[2]
NOISE = Decimal("1e-9")
# Half a unit of the gains JSON's 4th place: a difference this large is
# never float noise, whatever the figure's size (TJ-3065).
HALF_4TH = Decimal("0.00005")
CLASSES = ("same", "noise", "tie", "text", "form", "typeerror", "bug")
# A figure the engine cuts itself one unit apart at its places is that
# cut at a half (or a real move of one unit at the cut, which the figures
# around it would show).
# Each key the engine cuts itself, by its places (the cost timeline's
# rows at 4; tests/test_model_money_switch keeps the list whole).
CUT_KEYS = {
    2: ("at_risk_amount", "loss_amount"),
    4: ("rows", "capitalized_263h", "deferred", "deferred_wash"),
    6: ("deferred_loss_cut", "deferred_loss_recovered", "denied_contribution",
        "permanently_disallowed", "premium", "premium_after_cut"),
    9: ("units",),
    10: ("units",),
}
_WS = re.compile(r"\s+")
_RANK = {c: i for i, c in enumerate(CLASSES)}
_NUM = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?")


# ----------------------------------------------------------------- classes
def _number(x):
    """x as an exact Decimal when it is a finite number (not a bool)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, Decimal):
        return x if x.is_finite() else None
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, float):        # from_float: no FloatOperation signal
        return Decimal.from_float(x) if math.isfinite(x) else None
    return None


def _is_number(x) -> bool:
    return isinstance(x, (int, float, Decimal)) and not isinstance(x, bool)


def _written(x, key):
    """The figure as the gains JSON writes it (round_floats: a quantity
    as its float, other money at 4 places half up)."""
    if isinstance(x, (float, Decimal)):
        x = float(x) if key in _QTY_FIELDS else round_half_up(x, 4)
    return json.dumps(x)


def figures(a, b, key=None) -> str:
    """The class of two figures under `key`."""
    da, db = _number(a), _number(b)
    if da is None or db is None:        # a NaN or an infinity
        return "same" if repr(float(a)) == repr(float(b)) else "bug"
    if da == db:
        # A Decimal against a float or an int: one tree's answer kept a
        # Decimal its `leave` should have made a float (TJ-3325).
        if (isinstance(a, Decimal) or isinstance(b, Decimal)) and type(a) is not type(b):
            return "form"
        return "same" if _written(a, key) == _written(b, key) else "form"
    if abs(da - db) <= NOISE * max(Decimal(1), abs(da)) and abs(da - db) < HALF_4TH:
        # A quantity is written unrounded: its noise shows, and the golden
        # ledgers compare it within their tolerance.
        if key in _QTY_FIELDS or _written(a, key) == _written(b, key):
            return "noise"
        return "tie"
    # A float cut at k places reads back as its shortest repr.
    ra = Decimal(repr(a)) if isinstance(a, float) else da
    rb = Decimal(repr(b)) if isinstance(b, float) else db
    for k in [k for k, keys in CUT_KEYS.items() if key in keys]:
        unit = Decimal(1).scaleb(-k)
        if (abs(ra - rb) == unit and ra == ra.quantize(unit)
                and rb == rb.quantize(unit)):
            return "tie"
    return "bug"


def texts(a: str, b: str) -> str:
    """The class of two texts: "text" when only numbers differ, each by
    at most one unit in its last printed place."""
    if a == b:
        return "same"
    # A column's padding shifts with its number's width (-0.00 against
    # 0.00, 99.99 against 100.00): whitespace runs compare as one.
    if ([_WS.sub(" ", p) for p in _NUM.split(a)]
            != [_WS.sub(" ", p) for p in _NUM.split(b)]):
        return "bug"
    moved = False
    for x, y in zip(_NUM.findall(a), _NUM.findall(b)):
        if x == y:
            continue
        dx, dy = Decimal(x.replace(",", "")), Decimal(y.replace(",", ""))
        places = max(-dx.as_tuple().exponent, -dy.as_tuple().exponent, 0)
        if abs(dx - dy) > Decimal(1).scaleb(-places):
            return "bug"
        moved = True
    # Only the spacing differs, every number the same: a layout move.
    return "text" if moved else "bug"


def _is_rows(x) -> bool:
    return isinstance(x, Sequence) and not isinstance(x, (str, bytes))


# A per-share figure and the cost and quantity it is divided from: on a
# dust pool its cost's float noise, divided by the dust quantity, moves
# the per-share figure's 4th place.
DERIVED = {"acb_per_share_after": ("pool_acb_after", "pool_qty_after")}


def _derived_noise(a: dict, b: dict, k) -> bool:
    """Is a's and b's `k` a figure derived from inputs that differ by
    noise alone, each tree's figure its own inputs' quotient (the same
    contract size dividing both)?"""
    if k not in DERIVED or a[k] == b[k]:
        return False
    cost, qty = DERIVED[k]
    if any(x not in a or x not in b for x in (cost, qty)):
        return False
    if figures(a[cost], b[cost], cost) not in ("same", "noise"):
        return False
    if figures(a[qty], b[qty], qty) not in ("same", "noise"):
        return False
    ratios = []
    for d in (a, b):
        ps, c, q = _number(d[k]), _number(d[cost]), _number(d[qty])
        # A flat pool's per-share is no quotient: judged as it is.
        if ps is None or c is None or q is None or c == 0 or q == 0 or ps == 0:
            return False
        ratios.append(ps * q / c)
    return abs(ratios[0] - ratios[1]) <= NOISE * abs(ratios[0])


def compare(a, b, path: str = "", key=None, out=None) -> list:
    """[(class, path, a, b)] for each leaf of two results that differs."""
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) ^ set(b), key=str):
            out.append(("bug", f"{path}.{k}", a.get(k, "<absent>"), b.get(k, "<absent>")))
        for k in sorted(set(a) & set(b), key=str):
            if _derived_noise(a, b, k):
                out.append(("noise", f"{path}.{k}", a[k], b[k]))
                continue
            compare(a[k], b[k], f"{path}.{k}", k, out)
    elif _is_rows(a) and _is_rows(b):     # a SharedTrace is a Sequence, not a list
        if len(a) != len(b):
            out.append(("bug", path, f"{len(a)} items", f"{len(b)} items"))
        for i, (x, y) in enumerate(zip(a, b)):
            compare(x, y, f"{path}[{i}]", key, out)
    elif isinstance(a, bool) or isinstance(b, bool):
        if a is not b:
            out.append(("bug", path, a, b))
    elif _is_number(a) and _is_number(b):
        c = figures(a, b, key)
        if c != "same":
            out.append((c, path, a, b))
    elif isinstance(a, str) and isinstance(b, str):
        c = texts(a, b)
        if c != "same":
            out.append((c, path, a, b))
    elif a != b and str(a) != str(b):
        out.append(("bug", path, a, b))
    return out


def lines(a: str, b: str) -> list:
    """[(class, line, a, b)] for each line of two stderr texts that differs."""
    la, lb = a.splitlines(), b.splitlines()
    if len(la) != len(lb):
        return [("bug", "stderr", f"{len(la)} lines", f"{len(lb)} lines")]
    out = []
    for i, (x, y) in enumerate(zip(la, lb)):
        c = texts(x, y)
        if c != "same":
            out.append((c, f"stderr[{i}]", x, y))
    return out


def _gist(a, b) -> str:
    """Two leaves as the report shows them: two texts by their first
    differing numbers (or words), with what comes before."""
    if isinstance(a, str) and isinstance(b, str):
        ta, tb = re.split(r"(\s+)", a), re.split(r"(\s+)", b)
        for i, (x, y) in enumerate(zip(ta, tb)):
            if x != y:
                return f"...{''.join(ta[max(0, i - 6):i])[-60:]}[{x} | {y}]"
        return f"{a[:120]} | {b[:120]}"
    return f"{str(a)[:120]} | {str(b)[:120]}"


def _site(exc: BaseException) -> str:
    """The innermost src line of an exception's traceback."""
    where = "?"
    for fr in traceback.extract_tb(exc.__traceback__):
        try:
            rel = Path(fr.filename).resolve().relative_to(SRC)
        except ValueError:
            continue
        where = f"src/{rel.as_posix()}:{fr.lineno}"
    return where


# ----------------------------------------------------------------- one book
def _copy(rows):
    """The rows as they are (a deep copy: a field a row lacks stays
    lacking, which the engines' checks read)."""
    return copy.deepcopy(list(rows or []))


def _exc(e):
    """An exception as a dump keeps it: (type, text, innermost src line,
    whether it is exact money's own TypeError)."""
    if e is None:
        return None
    return (type(e).__name__, str(e), _site(e), M.money_error(e))


_ADDR = re.compile(r" at 0x[0-9a-fA-F]+")


def plain(x, seen=None):
    """An engine's answer as a dump keeps it: dicts, sequences (a
    SharedTrace too) and sets as dicts and lists, numbers and texts as
    they are, any other object as its repr (no address)."""
    if isinstance(x, (str, bytes, bool, int, float, Decimal, type(None))):
        return x
    seen = set() if seen is None else seen
    if id(x) in seen:
        return "<cycle>"
    seen = seen | {id(x)}
    if isinstance(x, dict):
        return {plain(k, seen): plain(v, seen) for k, v in x.items()}
    if isinstance(x, (set, frozenset)):
        return sorted((plain(v, seen) for v in x), key=repr)
    if _is_rows(x):
        return [plain(v, seen) for v in x]
    return _ADDR.sub("", repr(x))


def _run(country, txs, shel, kw):
    """(answer, stderr, exception) of one book's compute_gains."""
    err = io.StringIO()
    with once.nested(), redirect_stderr(err):
        try:
            res = get_tax_rules(country).compute_gains(
                _copy(txs), sheltered_transactions=_copy(shel), **kw)
            return plain(res), err.getvalue(), None
        except Exception as e:  # noqa: BLE001  (both trees' exceptions are compared)
            return None, err.getvalue(), _exc(e)


def judge(a, b) -> dict:
    """One book's record from the base tree's run and the new one's, each
    (answer, stderr, exception)."""
    (ar, ae, ax), (br, be, bx) = a, b
    diffs = []
    site = None
    # An exception is the same by its type, text and kind: its src line
    # moves with any edit above it.
    if (ax and (ax[0], ax[1], ax[3])) != (bx and (bx[0], bx[1], bx[3])):
        if bx is not None and bx[3]:
            # The new tree stopped at a money site where the base did not.
            site = bx[2]
            diffs.append(("typeerror", site, None, bx[1]))
        else:
            diffs.append(("bug", "exception", repr(ax), repr(bx)))
    elif bx is not None and bx[3]:
        # Both trees stopped at one money site: a site both missed (TJ-3331).
        site = bx[2]
        diffs.append(("typeerror", site, None, bx[1]))
    else:
        if ar is not None:
            compare(ar, br, out=diffs)
        diffs += lines(ae, be)
    cls = max((c for c, *_ in diffs), key=_RANK.get, default="same")
    return {"class": cls, "site": site, "diffs": diffs,
            "counts": dict(Counter(c for c, *_ in diffs))}


# ----------------------------------------------------------------- the books
_VARIANTS = (
    {}, {"trace": True}, {"cost_timeline": "settle"},
    {"year_end": "2025-06-30"}, {"detect_wash_sales": False},
    {"year_end": "2025-12-31", "year_end_basis": "trade"},
    {"trace": True, "cost_timeline": "trade"},
)


def books(n: int):
    """(name, country, txs, shel, kwargs) for each book: the engine
    goldens, then n of each fuzzer's books (variants by seed)."""
    from _support.engine_fuzz_books import _with_dust, make_book
    from _support.engine_settle_fuzz_books import build_book, clear_settle
    from _support.engine_transfer_fuzz_books import make_transfer_book, prep
    from taxjson.lib.pipeline import prepare_books
    gold = HERE / "golden"
    for b in sorted(p.name for p in gold.iterdir() if (p / "book.json").exists()):
        m = json.loads((gold / b / "book.json").read_text(encoding="utf-8"))
        if m.get("kind") != "engine":
            continue
        doc = json.loads((gold / b / "inputs.json").read_text(encoding="utf-8"))
        txs = [TaxTransaction(**r) for r in doc["taxable"]]
        shel = [TaxTransaction(**r) for r in doc["sheltered"]]
        if m.get("prepare"):
            with redirect_stderr(io.StringIO()):
                txs, shel, _a, _l = prepare_books(txs, shel, taxable=True, phantom_hint=False)
        for kw in ({}, {"trace": True}, {"cost_timeline": "settle", "year_end": "2025-06-30"}):
            yield f"golden/{b}", "canada", txs, shel, kw
            yield f"golden/{b}", "usa", txs, shel, {"per_account_basis": True, **kw}
    for seed in range(n):
        kw = _VARIANTS[seed % len(_VARIANTS)]
        txs, shel = make_book(seed)
        yield f"inv/{seed}", "canada", txs, shel, kw
        yield f"inv/{seed}", "usa", txs, shel, {"per_account_basis": seed % 2 == 0, **kw}
        if seed < n // 5:
            t2, _s = make_book(seed, sheltered_sells=False)
            yield f"inv/{seed}/dust", "canada", _with_dust(t2, seed), [], {}
        try:
            with redirect_stderr(io.StringIO()):
                m, s = prep(*make_transfer_book(seed))
        except Exception:  # noqa: BLE001  (a book prepare refuses: none to run)
            m = None
        if m is not None:
            yield f"tf/{seed}", "canada", m, s, kw
            yield f"tf/{seed}", "usa", m, s, {"per_account_basis": True, **kw}
        txs, shel, _meta = build_book(seed)
        yield f"ss/{seed}", "canada", txs, shel, kw
        yield f"ss/{seed}/cleared", "canada", clear_settle(txs), shel, {}
        yield f"ss/{seed}", "usa", txs, shel, {"per_account_basis": True}


# ----------------------------------------------------------------- the suite
def _test_id() -> str:
    """The id of the test whose call this is ("?" outside one)."""
    f = sys._getframe(2)
    while f is not None:
        t = f.f_locals.get("self")
        if isinstance(t, unittest.TestCase):
            return t.id()
        f = f.f_back
    return "?"


def suite(modules) -> list:
    """Each compute_gains call the test modules make, as it returns to the
    test: (name, (answer, stderr, exception)), named by its test and its
    number there. A run with a module that did not load, or that made
    no call, is refused (SystemExit 1): its records would compare as if
    complete (TJ-3324). A test that fails is said and its calls kept:
    the other tree fails some by design, a test of this checkout or of
    the change itself (TJ-3328)."""
    records = []
    count = Counter()
    real = {cls: cls.compute_gains for cls in (CanadaTaxRules, USATaxRules)}

    def wrap(cls):
        @functools.wraps(real[cls])     # a test reads the signature
        def recording(self, *args, **kw):
            key = f"suite/{_test_id()}#{cls.__name__}"
            n = count[key]
            count[key] += 1
            buf = io.StringIO()
            res = exc = None
            orig = sys.stderr
            sys.stderr = _Tee(orig, buf)
            try:
                res = real[cls](self, *args, **kw)
                return res
            except Exception as e:  # noqa: BLE001
                exc = e
                raise
            finally:
                sys.stderr = orig
                # Kept before the test reads (or changes) its answer.
                records.append((f"{key}#{n}", (plain(res) if exc is None else None,
                                               buf.getvalue(), _exc(exc))))
        return recording

    for cls in real:
        cls.compute_gains = wrap(cls)
    try:
        prog = unittest.main(module=None, argv=["money-ab", *modules], exit=False, verbosity=0)
    finally:
        for cls, fn in real.items():
            cls.compute_gains = fn
    res = prog.result
    unloaded = [t.id() for t, _tb in res.errors if type(t).__name__ == "_FailedTest"]
    if unloaded or not records:
        print(f"--suite: {len(unloaded)} module(s) not loaded {unloaded[:3]}, "
              f"{len(records)} compute_gains call(s): no dump", file=sys.stderr)
        raise SystemExit(1)
    if not res.wasSuccessful():
        print(f"--suite: {len(res.errors)} error(s) and {len(res.failures)} failure(s) "
              f"in {res.testsRun} test(s); their calls are kept", file=sys.stderr)
    return records


class _Tee(io.TextIOBase):
    def __init__(self, a, b):
        self.a, self.b = a, b

    def write(self, s):
        self.a.write(s)
        self.b.write(s)
        return len(s)

    def flush(self):
        self.a.flush()

    def isatty(self):
        return False


# ----------------------------------------------------------------- report
def report(records, show: int) -> Counter:
    by_class = Counter(r["class"] for _n, r in records)
    sites = Counter(r["site"] for _n, r in records if r["site"])
    print(f"books: {len(records)}  " + "  ".join(f"{c}: {by_class.get(c, 0)}" for c in CLASSES))
    if sites:
        said = {}
        for _n, r in records:
            for c, site, _a, msg in r["diffs"]:
                if c == "typeerror":
                    said.setdefault(site, msg)
        print("\ntypeerror sites (innermost src line, books, the first's message):")
        for s, n in sites.most_common():
            print(f"  {n:6d}  {s}  {said.get(s, '')[:100]}")
    for cls in ("bug", "form", "text", "tie", "noise"):
        shown = [(n, r) for n, r in records if r["class"] == cls][:show]
        if shown:
            print(f"\n{cls} (first {len(shown)}):")
        for n, r in shown:
            for c, path, a, b in [d for d in r["diffs"] if d[0] == cls][:3]:
                print(f"  {n} {path}: {_gist(a, b)}")
    return by_class


def dump(a) -> int:
    if a.src is not None and SRC != Path(a.src).resolve():
        print(f"--src {a.src}: taxjson came from {SRC} (PYTHONPATH={a.src})", file=sys.stderr)
        return 2
    t0 = time.perf_counter()
    records = [(f"{name} {country} {json.dumps(kw, sort_keys=True)}", _run(country, txs, shel, kw))
               for name, country, txs, shel, kw in books(a.books)]
    if a.suite is not None:
        records += suite(a.suite)
    with open(a.out, "wb") as f:
        pickle.dump({"src": str(SRC), "records": records}, f)
    print(f"{len(records)} answers from {SRC} in {time.perf_counter() - t0:.1f}s")
    return 0


def diff(a) -> int:
    def load(path):
        with open(path, "rb") as f:
            recs = pickle.load(f)["records"]
        twice = sorted(n for n, k in Counter(n for n, _r in recs).items() if k > 1)
        if not recs or twice:
            # Nothing to compare, or a name whose records would collapse
            # to one (TJ-3323, TJ-3326).
            print(f"{path}: " + (f"{len(twice)} name(s) twice, the first {twice[0]!r}"
                                 if twice else "no answers"), file=sys.stderr)
            return None
        return dict(recs)
    base, new = load(a.base), load(a.new)
    if base is None or new is None:
        return 2
    records = []
    for name in sorted(set(base) | set(new)):
        if name not in base or name not in new:
            records.append((name, {"class": "bug", "site": None, "counts": {"bug": 1},
                                   "diffs": [("bug", "answer", "<absent>" if name not in base else "",
                                              "<absent>" if name not in new else "")]}))
        else:
            records.append((name, judge(base[name], new[name])))
    by_class = report(records, a.show)
    # A book that raised in both trees compares only its exception: how
    # many did (TJ-3323).
    both = set(base) & set(new)
    raised = sum(1 for n in both if base[n][2] and new[n][2])
    print(f"raised in both trees: {raised}")
    if a.strict and raised == len(both):
        # Every book compared only its exception (TJ-3331).
        print("--strict: every book compared raised in both trees", file=sys.stderr)
        return 1
    if a.json:
        Path(a.json).write_text(json.dumps(
            [{"name": n, "class": r["class"], "site": r["site"], "counts": r["counts"]}
             for n, r in records], indent=1) + "\n", encoding="utf-8")
    bad = [c for c in CLASSES if c != "same"] if a.strict else ["bug"]
    return 1 if any(by_class.get(c) for c in bad) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="each book's answer from this taxjson")
    d.add_argument("out")
    d.add_argument("--src", metavar="DIR", help="the tree's src, which PYTHONPATH puts first (checked)")
    d.add_argument("--books", type=int, default=300, help="each fuzzer's books (default 300)")
    d.add_argument("--suite", nargs="*", default=None, metavar="MODULE",
                   help="also each compute_gains call these test modules make")
    c = sub.add_parser("diff", help="two dumps compared, book by book")
    c.add_argument("base")
    c.add_argument("new")
    c.add_argument("--strict", action="store_true", help="fail on any class but same")
    c.add_argument("--show", type=int, default=10)
    c.add_argument("--json", metavar="OUT", help="write each book's class and site")
    a = ap.parse_args(argv)
    return dump(a) if a.cmd == "dump" else diff(a)


if __name__ == "__main__":
    sys.exit(main())
