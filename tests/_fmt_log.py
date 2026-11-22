"""The format log (step 6h, TJ-3338): a copy of a tree's taxjson whose every
fixed-point format of a value logs each print of an exact half.

    python3 tests/_fmt_log.py make SRC OUT

`make` copies SRC's taxjson (a tree, or its src) to OUT/taxjson and compiles
each module that formats a non-literal value at fixed point (`f`/`F`, a
precision or the default 6: an f-string's `{x:,.2f}`, `format(x, ".2f")`, a
string constant's `%` (`"%.2f" % x`) and `.format()` (`"{:.2f}".format(x)`))
from its tree rewritten so each such value passes through a no-op of
OUT/taxjson/_fmtlog.py, which returns it as it was: the print is the same.
The bytecode is the module's cached pyc, unchecked (PEP 552), so the import
loads it and never the source, which is copied as it is: a traceback names
the tree's lines. It loads on the Python that made it (`make` runs on the
one the dump's processes run on); another compiles the source, unlogged.

Where the value is a float (or a Decimal) whose repr is an exact half at the
format's places (places+1 places, ending in 5), the no-op writes a "fmt"
record to the shown log (tests/_shown_log, which installs the sink): the
site (the module's path in the package and the line, as the shown log names
a site), how it was formatted, the formatted expression and whether it is
money (its names: scripts/check_architecture.py `_money_name`, the metric's
money words; or it is in a function named a money formatter: report_fmt's
`fmt_money`), the repr, the places, what the format printed and what a
half-up cut prints (`moves`: they differ). A value cut by `M.shown` first
has at most its places, so it is never one: a record is a figure printed
from an uncut float at a tie, a print the reports' flip (step 6h(4)) cannot
move.

A module whose rewrite does not compile is left to its source and named in
`failed`; a format whose spec is all dynamic (`{x:{spec}}`) is counted in
`dynamic`, not logged. The views A/B dumps through such a copy with `dump
--fmt-log` (tests/_views_ab.py), and its `fmt` and `ties` read the records:
they fail on any tie a half-up cut prints otherwise, money by name or not,
but at a site listed in NOT_MONEY (each with its reason).

What it cannot see (TJ-3369, for step 6h(4)'s review): a figure cut by
`M.units` or round() before it is printed has its places, so a money tie
routed through `M.units(x, 2)` logs nothing, though the flip leaves it
(money through `units` today: lib/crypto_sends 2 sites, lib/in_kind 4,
lib/fx_cash_v2 3; no tie at them in the six books); a Decimal whose
exponent is past places+1 (`Decimal('305.775000')` at 2: trailing zeros)
is not logged (11 lib/core.py sites print such values, half up inside the
engine's decimal context, half even outside it). 6h(4)'s review reads the
`units` money sites and core.py's Decimal formats.

How the copy differs and what it misses (TJ-3373; none occurs in src
today): a `%`, `format()` or `str.format` that raises has one more
traceback frame, the hook's; `f"{v:.{expr}f}"` evaluates `expr` twice; a
rewritten module has two more globals (_TJ_FMTLOG, _TJ_FMTLOG_SITES); not
logged: `%` or `.format` of a template held in a variable, `.format_map`,
a nested spec inside a `.format` template, the `g` and `%` presentation
types; `%` of a Decimal is logged as format() prints it, not as `%`
(through float) does. In tests/_views_ab: a file inside a folder renamed
into place has no writer (its moves read unexplained); a call's site is
its module and line, so a tree whose shown calls moved a line does not
pair with another's (refused: compare trees that differ in shown alone).
"""
import argparse
import ast
import copy
import importlib.util
import json
import marshal
import re
import shutil
import string
import sys
from collections import Counter
from pathlib import Path

import _paths

HERE = _paths.TESTS
# Sites reviewed as not money, by module and expression, each with its
# reason: `fmt` and `ties` fail on any other tie a half-up cut prints
# otherwise, money by name or not (TJ-3368). Keep it short.
NOT_MONEY = {
    ("bin/taxjson_sum_gains.py", "avg_hold"): "an average of holding days",
    ("bin/taxjson_sum_gains.py", "grand_avg"): "an average of holding days, all tickers",
}
HOOK = "_fmtlog"                  # OUT/taxjson/_fmtlog.py
NAME = "_TJ_FMTLOG"               # the hook, as each rewritten module imports it
TABLE = "_TJ_FMTLOG_SITES"        # each rewritten module's sites
# A fixed-point format spec: [[fill]align][sign][z][#][0][width][grouping][.precision](f|F).
SPEC = re.compile(r"(?:.?[<>=^])?[+\- ]?z?#?0?\d*[,_]?(?:\.(\d+))?([fF])\Z")
# A printf-style conversion: %[(name)][flags][width][.precision][length]type.
PCT = re.compile(r"%(?:\(([^)]*)\))?[-+ #0]*(\*|\d+)?(?:\.(\*|\d+))?[hlL]?([diouxXeEfFgGcrsa%])")

HOOK_SRC = '''"""Written by tests/_fmt_log.py into a copy of taxjson (never the tree): the
format log's no-ops. Each returns what the format it stands in for prints
from, unchanged, and writes a record to the sink tests/_shown_log installs
(sys._taxjson_fmt_log) when the value's repr is an exact half at the
format's places."""
import builtins
import math
import re
import string
import sys
from decimal import ROUND_HALF_UP, Context, Decimal

_SPEC = re.compile(%(spec)r)
_PCT = re.compile(%(pct)r)
_WIDE = Context(prec=400)


def _entry(site, key):
    for k, text, money in site[2]:
        if k == key:
            return text, money
    for k, text, money in site[2]:
        if k is None:
            return text, money
    return "?", False


def _note(value, places, site, key):
    sink = getattr(sys, "_taxjson_fmt_log", None)
    if sink is None or type(places) is not int or places < 0:
        return
    try:
        if type(value) is float:
            if not math.isfinite(value):
                return
            d = Decimal(repr(value))
        elif isinstance(value, Decimal):
            if not value.is_finite():
                return
            d = value
        else:
            return
        _sign, digits, exponent = d.as_tuple()
        if exponent != -(places + 1) or digits[-1] != 5:
            return
        spec = "." + str(places) + "f"
        printed = builtins.format(value, spec)
        half_up = builtins.format(d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP,
                                             context=_WIDE), spec)
        text, money = _entry(site, key)
        sink({"site": site[0], "how": site[1], "expr": text, "money": money,
              "repr": repr(value) if type(value) is float else str(value), "places": places,
              "printed": printed, "half_up": half_up, "moves": printed != half_up})
    except Exception:            # noqa: BLE001 - the log never changes a print
        pass


def fv(value, places, site):
    """An f-string's {value:...f}."""
    _note(value, places, site, 0)
    return value


def fmt(site):
    """format(value, spec)."""
    def format(value, format_spec=""):
        if isinstance(format_spec, str):
            m = _SPEC.match(format_spec)
            if m:
                _note(value, int(m[1]) if m[1] else 6, site, 0)
        return builtins.format(value, format_spec)
    return format


def pct(site):
    """A constant's %% args."""
    def mod(text, args):
        try:
            _pct(text, args, site)
        except Exception:        # noqa: BLE001
            pass
        return text %% args
    return mod


def _pct(text, args, site):
    convs = [m.groups() for m in _PCT.finditer(text)]
    named = any(c[0] is not None for c in convs)
    seq = list(args) if isinstance(args, tuple) else None if named else [args]
    i = 0
    for name, width, prec, conv in convs:
        if conv == "%%":
            continue
        if named:
            value, key = args[name], name
            places = int(prec) if prec and prec != "*" else 6
        else:
            if width == "*":
                i += 1
            if prec == "*":
                places = seq[i]
                i += 1
            else:
                places = int(prec) if prec else 6
            value, key = seq[i], i
            i += 1
        if conv in "fF":
            _note(value, places, site, key)


def sfmt(text, site):
    """A constant's .format(...)."""
    def call(*args, **kwargs):
        try:
            _sfmt(text, args, kwargs, site)
        except Exception:        # noqa: BLE001
            pass
        return text.format(*args, **kwargs)
    return call


def _sfmt(text, args, kwargs, site):
    f = string.Formatter()
    auto = 0
    for _literal, field, spec, conv in f.parse(text):
        if field is None:
            continue
        if field == "":
            field, key = str(auto), auto
            auto += 1
        else:
            first = re.split(r"[.\\[]", field, maxsplit=1)[0]
            key = int(first) if first.isdigit() else first
        m = _SPEC.match(spec or "") if not conv and spec and "{" not in spec else None
        if m:
            value, _used = f.get_field(field, args, kwargs)
            _note(value, int(m[1]) if m[1] else 6, site, key)
''' % {"spec": SPEC.pattern, "pct": PCT.pattern}


def src_folder(arg) -> Path:
    """The src folder of DIR: DIR/src, or DIR itself if it holds taxjson."""
    p = Path(arg).resolve()
    for cand in (p / "src", p):
        if (cand / "taxjson" / "__init__.py").is_file():
            return cand
    raise ValueError(f"{arg}: no taxjson package there (a tree, or its src)")


def _money_name():
    """scripts/check_architecture.py's `_money_name`: the metric's money words."""
    path = HERE.parent / "scripts" / "check_architecture.py"
    spec = importlib.util.spec_from_file_location("_tj_check_architecture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._money_name


def _fixed_fields(text: str) -> bool:
    try:
        return any(spec and not conv and SPEC.match(spec)
                   for _lit, field, spec, conv in string.Formatter().parse(text) if field is not None)
    except ValueError:
        return False


def _binds_format(tree) -> bool:
    """Whether a module rebinds the name `format` (then format() is not the builtin)."""
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id == "format" and not isinstance(n.ctx, ast.Load):
            return True
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == "format":
            return True
        if isinstance(n, ast.arg) and n.arg == "format":
            return True
        if isinstance(n, ast.alias) and (n.asname or n.name) == "format":
            return True
    return False


class _Rewrite(ast.NodeTransformer):
    def __init__(self, rel: str, money, rebinds_format: bool):
        self.rel, self.money, self.rebinds = rel, money, rebinds_format
        self.sites, self.dynamic = [], 0
        self.how = Counter()
        self.money_fn = [False]          # in a money formatter: fmt_money, _format_amount

    def _function(self, node):
        words = {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", node.name)}
        self.money_fn.append(bool(words & {"fmt", "format"}) and bool(self.money(ast.Name(node.name, ast.Load()))))
        self.generic_visit(node)
        self.money_fn.pop()
        return node

    visit_FunctionDef = visit_AsyncFunctionDef = _function

    def _site(self, line: int, how: str, entries) -> ast.expr:
        self.sites.append((f"{self.rel}:{line}", how, tuple(entries)))
        self.how[how] += 1
        return ast.Subscript(value=ast.Name(TABLE, ast.Load()), slice=ast.Constant(len(self.sites) - 1),
                             ctx=ast.Load())

    def _entry(self, key, node):
        return (key, ast.unparse(node)[:100], self.money_fn[-1] or bool(self.money(node)))

    @staticmethod
    def _hook(fn: str, *args) -> ast.expr:
        return ast.Call(func=ast.Attribute(value=ast.Name(NAME, ast.Load()), attr=fn, ctx=ast.Load()),
                        args=list(args), keywords=[])

    def _places(self, spec):
        """The places of an f-string's spec (a Constant, or the precision's
        expression), or None: not fixed point, or all dynamic."""
        vals = spec.values
        if all(isinstance(v, ast.Constant) for v in vals):
            m = SPEC.match("".join(v.value for v in vals))
            return ast.Constant(int(m[1]) if m[1] else 6) if m else None
        last = vals[-1]
        if isinstance(last, ast.Constant) and isinstance(last.value, str):
            m = re.search(r"\.(\d+)[fF]\Z", last.value)
            if m:
                return ast.Constant(int(m[1]))
            if (last.value in ("f", "F") and len(vals) >= 3 and isinstance(vals[-2], ast.FormattedValue)
                    and isinstance(vals[-3], ast.Constant) and str(vals[-3].value).endswith(".")):
                return copy.deepcopy(vals[-2].value)
        self.dynamic += 1
        return None

    def visit_JoinedStr(self, node):
        for v in node.values:
            if (isinstance(v, ast.FormattedValue) and v.format_spec is not None and v.conversion == -1
                    and not isinstance(v.value, ast.Constant)):
                places = self._places(v.format_spec)
                if places is not None:
                    entry = self._entry(0, v.value)
                    v.value = self._hook("fv", v.value, places, self._site(node.lineno, "f-string", [entry]))
        self.generic_visit(node)
        return node

    def visit_Call(self, node):
        f = node.func
        starred = any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords)
        hook = None
        if (isinstance(f, ast.Name) and f.id == "format" and not self.rebinds and len(node.args) == 2
                and not node.keywords and not starred and not isinstance(node.args[0], ast.Constant)):
            hook = self._hook("fmt", self._site(node.lineno, "format()", [self._entry(0, node.args[0])]))
        elif (isinstance(f, ast.Attribute) and f.attr == "format" and isinstance(f.value, ast.Constant)
              and isinstance(f.value.value, str) and _fixed_fields(f.value.value)):
            if starred:
                entries = [(None, ", ".join(ast.unparse(a) for a in node.args)[:100],
                            self.money_fn[-1]
                            or any(self.money(a) for a in [*node.args, *(k.value for k in node.keywords)]))]
            else:
                entries = ([self._entry(i, a) for i, a in enumerate(node.args)]
                           + [self._entry(k.arg, k.value) for k in node.keywords])
            hook = self._hook("sfmt", f.value, self._site(node.lineno, "str.format", entries))
        self.generic_visit(node)
        if hook is not None:
            node.func = hook
        return node

    def visit_BinOp(self, node):
        site = None
        if (isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, str)
                and any(m[3] in "fF" for m in PCT.findall(node.left.value))):
            r = node.right
            if isinstance(r, ast.Tuple) and not any(isinstance(e, ast.Starred) for e in r.elts):
                entries = [self._entry(i, e) for i, e in enumerate(r.elts)]
            elif isinstance(r, ast.Dict) and all(isinstance(k, ast.Constant) for k in r.keys):
                entries = [self._entry(k.value, v) for k, v in zip(r.keys, r.values)]
            else:
                entries = [self._entry(None, r)]
            site = self._site(node.lineno, "%", entries)
        self.generic_visit(node)
        if site is None:
            return node
        call = ast.Call(func=self._hook("pct", site), args=[node.left, node.right], keywords=[])
        return ast.copy_location(call, node)


def _insert(tree, sites) -> None:
    """The hook's import and the module's site table, after its docstring
    and any `from __future__` import."""
    body, i = tree.body, 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        i = 1
    while i < len(body) and isinstance(body[i], ast.ImportFrom) and body[i].module == "__future__":
        i += 1
    imp = ast.Import(names=[ast.alias(name=f"taxjson.{HOOK}", asname=NAME)])
    table = ast.Assign(targets=[ast.Name(TABLE, ast.Store())],
                       value=ast.parse(repr(tuple(sites)), mode="eval").body)
    body[i:i] = [imp, table]


def _write_pyc(path: Path, code) -> None:
    """`code` as `path`'s cached bytecode, an unchecked hash-based pyc
    (PEP 552): the import loads it and never compiles the source, which
    stays as it was (the line numbers and the source lines a traceback
    prints are the tree's)."""
    source_hash = importlib.util.source_hash(path.read_bytes())
    cache = Path(importlib.util.cache_from_source(str(path)))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(importlib.util.MAGIC_NUMBER + (0b01).to_bytes(4, "little") + source_hash
                      + marshal.dumps(code))


def make(src, out) -> tuple:
    """(OUT's src folder, stats): SRC's taxjson copied to OUT/taxjson, each
    module with a fixed-point format rewritten (module docstring)."""
    src = src_folder(src)
    out = Path(out).resolve()
    dst = out / "taxjson"
    shutil.copytree(src / "taxjson", dst, ignore=shutil.ignore_patterns("__pycache__"))
    money = _money_name()
    stats = {"src": str(src), "python": ".".join(map(str, sys.version_info[:3])),
             "modules": 0, "sites": 0, "how": Counter(), "dynamic": 0, "failed": []}
    for p in sorted(dst.rglob("*.py")):
        rel = p.relative_to(dst).as_posix()
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(p))
        rw = _Rewrite(rel, money, _binds_format(tree))
        tree = rw.visit(tree)
        stats["dynamic"] += rw.dynamic
        if not rw.sites:
            continue
        _insert(tree, rw.sites)
        ast.fix_missing_locations(tree)
        try:
            code = compile(tree, str(p), "exec", dont_inherit=True)
        except (ValueError, SyntaxError, TypeError, RecursionError) as e:
            stats["failed"].append(f"{rel}: {type(e).__name__}: {e}")
            continue
        _write_pyc(p, code)
        stats["modules"] += 1
        stats["sites"] += len(rw.sites)
        stats["how"].update(rw.how)
    (dst / f"{HOOK}.py").write_text(HOOK_SRC, encoding="utf-8")
    stats["how"] = dict(sorted(stats["how"].items()))
    return out, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="a copy of SRC's taxjson whose fixed-point formats log ties")
    m.add_argument("src", help="a tree, or its src")
    m.add_argument("out", help="a new folder: OUT/taxjson is written")
    a = ap.parse_args(argv)
    if Path(a.out).exists() and any(Path(a.out).iterdir()):
        print(f"refused: {a.out} is not empty", file=sys.stderr)
        return 2
    try:
        out, stats = make(a.src, a.out)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps(stats, indent=1))
    print(f"{out}: {stats['sites']} format site(s) in {stats['modules']} module(s); "
          f"{stats['dynamic']} dynamic spec(s) left; {len(stats['failed'])} module(s) not rewritten")
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
