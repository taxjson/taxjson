#!/usr/bin/env python3
"""check_architecture: the refactor's guard rails, a ratchet on the counts below.

Nothing here asks for today's code to be different. It records what is
there (scripts/architecture-baseline.json) and fails the gate when it gets
worse, so the code can only move one way:

  layers    the new packages import downward only, in the order of LAYERS
            below; a package below `pipeline` never imports taxjson.bin, and
            taxjson.lib never imports `pipeline`, `cli` or taxjson.bin (step 8:
            so a package importing lib reaches bin through it no more than
            directly; the in-process run of a tool, which imports one by
            name, is pipeline/dispatch's). A string naming a
            module counts as importing it, and a top-level name that is neither
            a layer nor OLD_PACKAGES fails. A leaf (LEAVES) may be imported by
            every layer and by lib, and imports nothing from taxjson itself.
            No baseline: these start clean.
            A plugin (packages/*/src) never imports or names taxjson.bin: it
            reads the core through taxjson.pipeline.plugin_api (step 8).
  imports   every import edge inside an import cycle and every string
            naming a taxjson.bin module outside lib (how a child process
            or importlib reaches it) is on the allow-list; lib reaching
            bin is a layer violation, never on it. Function-local imports
            count: they are how the cycles run today. An import under a
            module-level `if TYPE_CHECKING:` (typing's) is no edge, for
            these rules or the layers: it never runs (its else branch does).
  ruff      the [tool.ruff.lint] rules in pyproject.toml (ruff's defaults
            plus BLE001): the count per rule and file with suppressions ignored
            (--ignore-noqa), so hiding a hit cannot read as removing it; and the
            rule set itself, so narrowing it cannot either.
  noqa      the suppression comments, per code and file, a whole file's
            (`# ruff: noqa`) apart as `file:CODE` and a range's
            (`# ruff: disable[CODE]`) as `range:CODE`: a new one is a decision.
  length    a function over MAX_LINES is on the list, per file and qualified
            name, at no more than its recorded length.
  print     the raw `print(` calls in src, per file: report text can be printed
            raw for now (messages can't: tests/features/cli/test_cli_out_of_the_entry_point_and_run_state.py), and the
            count only falls, to under 100 once reports have one renderer.
  money     step 6's float money, per file in src: `round` the bare round( calls
            (not a method's), and `float` the float( calls on a money name
            (MONEY_WORDS: a word of a name, string key or attribute in the
            argument, such as row["Proceeds"] or t.cost_cad). Both only fall:
            Decimal arithmetic rounds where it is presented, not as it goes.

The baseline must match exactly. When a count falls the gate fails too, so
that the lower number is recorded and cannot be spent again:

  scripts/check_architecture.py                  check (ci.sh's `architecture` stage)
  scripts/check_architecture.py --shrink         record improvements, and a long
                                                 function's move at the same length;
                                                 nothing at all while anything got
                                                 worse: the one may have bought the other
  scripts/check_architecture.py --accept-growth=imports,ruff
                                                 record those kinds as they are now:
                                                 imports, ruff, noqa, length, print,
                                                 money or all

Use --accept-growth only when the growth is the point of the change (a tax
fix inside a long function: `length`; code moved to a new module: `imports`,
and `ruff` or `noqa` if hits moved with it). It forgives only the kinds named;
the baseline's diff shows what it forgave in review. A new ruff version can
move the counts without a code change: after a lock regeneration,
--accept-growth=ruff records them (docs/releasing.md).

Standard library only; ruff comes from the dev lock (requirements/dev.txt).
"""
import argparse
import ast
import functools
import io
import json
import re
import subprocess
import sys
import tokenize
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().with_name("architecture-baseline.json")
PACKAGE = "taxjson"
BIN = f"{PACKAGE}.bin"
LIB = f"{PACKAGE}.lib"
# What sits under taxjson/ besides the layers. Anything else needs a place first.
OLD_PACKAGES = ("bin", "data", "lib")

# Top layer first: a package may import the ones after it, never before it.
LAYERS = ("cli", "pipeline", "project", "reports", "engine", "rules",
          "characterize", "parsers", "model")
# The layers still allowed to reach the old command code while it moves.
# taxjson.lib sits below them: they call it, it never calls them. The one
# exception: `lib.brokerages`, the parsers moving out in step 5, may reach
# `parsers` (its toolbox) while it moves; the rest of lib may not.
MAY_IMPORT_BIN = ("cli", "pipeline")
# Leaves: modules every layer, and lib, may import whatever their package's
# place in LAYERS, because they import nothing from taxjson. The rule ids
# (step 7): the architecture review's layer table has characterize import
# "model, rules.ids", below the rest of `rules`. The country resolver (step
# 10 B1): the parse and characterize read a project's country through it.
LEAVES = (f"{PACKAGE}.rules.ids", f"{PACKAGE}.rules.country")
# The packages above a leaf, below taxjson itself: importing the leaf runs
# their __init__, so they are held to the leaf rule too.
LEAF_PARENTS = tuple(sorted({".".join(leaf.split(".")[:i]) for leaf in LEAVES
                             for i in range(2, leaf.count(".") + 1)}))
MAX_LINES = 150
LINT_DIRS = ("src", "tests", "scripts", "packages")
LENGTH_DIRS = ("src", "scripts", "packages")
KINDS = ("imports", "ruff", "noqa", "length", "print", "money")
# What each kind owns in the baseline.
_KIND_KEYS = {"imports": ("import_edges",),
              "ruff": ("ruff", "ruff_version", "ruff_config"),
              "noqa": ("noqa",),
              "length": ("function_length", "max_function_lines"),
              "print": ("print_calls",),
              "money": ("money",)}
# A tree with no git (an export): the folders ruff would not look in.
_SKIP_PARTS = {"__pycache__", "venv", ".venv", ".ruff_cache", "build", "dist"}
_RUFF_CONFIGS = ("ruff.toml", ".ruff.toml")
_CODE = r"[A-Za-z]{1,4}[0-9]{2,4}\b"
_CODES = rf"(?:\s*:\s*({_CODE}(?:[,\s]+{_CODE})*))?"
_NOQA = re.compile(rf"(?i:#\s*noqa\b){_CODES}")
_FILE_NOQA = re.compile(rf"(?i:#\s*(?:ruff|flake8)\s*:\s*noqa\b){_CODES}")
_RANGE_NOQA = re.compile(rf"(?i:#\s*ruff\s*:\s*disable\b)(?:\s*\[\s*({_CODE}(?:[,\s]+{_CODE})*)\s*\])?")
_NAMES_MODULE = re.compile(rf"{re.escape(PACKAGE)}\.(?:bin|{'|'.join(LAYERS)})(?:\.\w+)*\Z")
_RUFF_TABLE = re.compile(r"\[tool\.ruff[.\]]")
# A name's words that make its float( money (the `money` kind). A word is a
# run of letters in a snake_case or CamelCase name; a plural counts. Not a
# quantity, a rate or a date: those are not what Decimal money replaces.
MONEY_WORDS = frozenset((
    "acb", "adjustment", "amount", "amt", "bal", "balance", "basis", "cad", "cash", "charge", "comm", "commission", "cost", "credit", "debit", "deduction",
    "distribution", "div", "dividend", "fee", "fmv", "gain", "gross", "income", "interest",
    "loss", "losse", "money", "net", "notional", "owed", "owing", "paid", "payment", "pnl",
    "premium", "price", "proceed", "proceeds", "px", "refund", "roc", "tax", "total", "usd",
    "value", "withheld", "withholding"))
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
_EDGE = " -> "
_BY_NAME = " (by name)"
_KEY = "::"


def _in_package(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _git_files(root: Path):
    """Every file git would commit here (tracked, or untracked and not
    ignored): what ruff looks at, so any checkout counts alike. None when
    `root` is not the top of a git tree."""
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, check=False)
    try:
        top = git("rev-parse", "--show-toplevel")
        if top.returncode or Path(top.stdout.strip()).resolve() != root.resolve():
            return None
        listed = git("ls-files", "-co", "--exclude-standard", "-z")
    except OSError:
        return None
    if listed.returncode:
        return None
    return [root / rel for rel in sorted(set(listed.stdout.split("\0"))) if rel]


def _all_files(root: Path) -> list:
    listed = _git_files(root)
    if listed is None:
        listed = [p for p in sorted(root.rglob("*"))
                  if not _SKIP_PARTS & set(p.relative_to(root).parts)]
    return [p for p in listed if p.is_file()]


def _py_files(root: Path, dirs) -> list:
    return [p for p in _all_files(root)
            if p.suffix == ".py" and p.relative_to(root).parts[0] in dirs]


# ---------------------------------------------------------------- imports
def _modules(src: Path) -> dict:
    """{dotted module name: path} for the package under src/."""
    mods = {}
    root = src.parent
    for p in _py_files(root, (src.name,)):
        parts = list(p.relative_to(src).with_suffix("").parts)
        if parts[0] != PACKAGE:
            continue
        if parts[-1] == "__init__":
            parts.pop()
        mods[".".join(parts)] = p
    return mods


def _targets(node, module: str, is_pkg: bool, mods: dict) -> set:
    """The package's modules one import statement names."""
    found = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.name
            while name and name not in mods:
                name = name.rpartition(".")[0]
            if name:
                found.add(name)
        return found
    if node.level:
        base = module.split(".")
        drop = node.level - (1 if is_pkg else 0)
        base = base[:len(base) - drop] if drop else base
        prefix = ".".join(base + ([node.module] if node.module else []))
    else:
        prefix = node.module or ""
    for alias in node.names:
        full = f"{prefix}.{alias.name}"
        if full in mods:
            found.add(full)
        else:
            name = prefix
            while name and name not in mods:
                name = name.rpartition(".")[0]
            if name:
                found.add(name)
    return found


def _bound_names(node) -> list:
    """The names NODE binds (a star import: `*`)."""
    if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
        return [node.id]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.Import):
        return [a.asname or a.name.split(".")[0] for a in node.names]
    if isinstance(node, ast.ImportFrom):
        return [a.asname or a.name for a in node.names]
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
        return [node.name]
    return []


def type_checking_only(tree) -> set:
    """The ids of the nodes under a module-level `if TYPE_CHECKING:` or `if
    typing.TYPE_CHECKING:`, its else branch aside: code that never runs, so
    its imports are no edge. Typing's flag only: TYPE_CHECKING (or typing)
    bound by a module-level import from typing and by nothing else in the
    module, no star import, no `x.TYPE_CHECKING = ...`."""
    imports = [st for st in tree.body if isinstance(st, (ast.Import, ast.ImportFrom))]
    flags = [a.asname or a.name for st in imports if isinstance(st, ast.ImportFrom)
             and st.module == "typing" and not st.level for a in st.names if a.name == "TYPE_CHECKING"]
    mods = [a.asname or a.name for st in imports if isinstance(st, ast.Import)
            for a in st.names if a.name == "typing"]
    binds = defaultdict(int)
    for node in ast.walk(tree):
        for name in _bound_names(node):
            binds[name] += 1
        if isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING" \
                and not isinstance(node.ctx, ast.Load):
            binds["*"] += 1         # typing's flag set by hand: trust no spelling of it
    if binds["*"]:
        return set()
    flags = {f for f in flags if binds[f] == flags.count(f)}
    mods = {m for m in mods if binds[m] == mods.count(m)}

    def is_flag(test) -> bool:
        return (isinstance(test, ast.Name) and test.id in flags) or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name) and test.value.id in mods)

    found = set()

    def walk(body) -> None:
        for st in body:
            if isinstance(st, ast.If) and is_flag(st.test):
                found.update(id(n) for x in st.body for n in ast.walk(x))
                walk(st.orelse)
    walk(tree.body)
    return found


def import_graph(src: Path) -> dict:
    """{module: {modules it imports, anywhere in its body}}, those under a
    module-level `if TYPE_CHECKING:` aside."""
    mods = _modules(src)
    graph = {m: set() for m in mods}
    for mod, path in mods.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        unrun = type_checking_only(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in unrun:
                graph[mod] |= _targets(node, mod, path.name == "__init__.py", mods)
        graph[mod].discard(mod)
    return graph


def _named_in(tree) -> set:
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and _NAMES_MODULE.match(node.value)}


def named_modules(src: Path) -> dict:
    """{module outside bin: {the bin and layer modules a string in it names}}:
    what python_module_argv and importlib reach, no import statement in sight.
    A name built at run time stays out of reach of this."""
    found = {}
    for mod, path in _modules(src).items():
        if not _in_package(mod, BIN):
            names = _named_in(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            if names:
                found[mod] = names
    return found


def plugin_bin_edges(root: Path) -> set:
    """The edges from packages/*/src into taxjson.bin: a move of what they
    import breaks a plugin CI's main suite does not install, so each is
    refused (plugin_violations). They do not join the cycle graph: nothing in
    taxjson imports a plugin."""
    mods = _modules(root / "src")
    edges = set()
    for p in _py_files(root, ("packages",)):
        parts = p.relative_to(root).with_suffix("").parts
        if len(parts) < 4 or parts[2] != "src":
            continue
        mod = ".".join(parts[3:-1] if parts[-1] == "__init__" else parts[3:])
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        unrun = type_checking_only(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and not getattr(node, "level", 0) \
                    and id(node) not in unrun:
                edges |= {mod + _EDGE + t for t in _targets(node, mod, False, mods)
                          if _in_package(t, BIN)}
        edges |= {mod + _EDGE + n + _BY_NAME for n in _named_in(tree) if _in_package(n, BIN)}
    return edges


def plugin_violations(root: Path) -> list:
    out = []
    for e in sorted(plugin_bin_edges(root)):
        verb = "names" if e.endswith(_BY_NAME) else "imports"
        a, b = e.removesuffix(_BY_NAME).split(_EDGE)
        out.append(f"layers: {a} {verb} {b}: a plugin reads the core through "
                   f"{PACKAGE}.pipeline.plugin_api, never {BIN} (step 8)")
    return out


def cycles(graph: dict) -> list:
    """The strongly connected components of more than one module
    (Tarjan's, iterative: the graph is deeper than the recursion limit
    would like)."""
    index, low, on_stack, stack, sccs = {}, {}, set(), [], []
    counter = 0
    for start in sorted(graph):
        if start in index:
            continue
        work = [(start, iter(sorted(graph[start])))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(graph[nxt]))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    m = stack.pop()
                    on_stack.discard(m)
                    comp.append(m)
                    if m == node:
                        break
                if len(comp) > 1:
                    sccs.append(sorted(comp))
    return sorted(sccs, key=len, reverse=True)


def allow_listed_edges(graph: dict, comps=None, named=None) -> set:
    """The edges the allow-list covers: inside a cycle, or a string naming
    a bin module outside lib (lib reaching bin is a layer violation: step
    8, TJ-3546)."""
    comp_of = {m: i for i, comp in enumerate(cycles(graph) if comps is None else comps)
               for m in comp}
    edges = set()
    for a, targets in graph.items():
        for b in targets:
            if (a in comp_of and comp_of[a] == comp_of.get(b)
                    and not (_in_package(a, LIB) and _in_package(b, BIN))):
                edges.add(a + _EDGE + b)
    for a, names in (named or {}).items():
        if not _in_package(a, LIB):
            edges |= {a + _EDGE + n + _BY_NAME for n in names if _in_package(n, BIN)}
    return edges


def _layer(module: str):
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in LAYERS else None


# The modules of lib.brokerages every parser may import (and detect,
# which routes a file to the generic importer): a parser imports no
# other parser's module, private or not (step 5f).
PARSER_SHARED = frozenset(f"{LIB}.brokerages.{m}"
                          for m in ("base", "schema", "_crypto_common", "detect"))


def _edge_problem(a: str, verb: str, b: str):
    """Why `a` may not import or name `b`, or None."""
    if a in LEAVES or a in LEAF_PARENTS:
        what = "a leaf (LEAVES)" if a in LEAVES else "a leaf's package (LEAF_PARENTS)"
        return f"layers: {a} {verb} {b}: {what} imports nothing from {PACKAGE}"
    if b in LEAVES:
        return None
    la, lb = _layer(a), _layer(b)
    if _in_package(a, LIB):
        if lb in MAY_IMPORT_BIN:
            return f"layers: {a} {verb} {b}: `lib` sits below `{lb}`"
        if _in_package(b, BIN):
            return (f"layers: {a} {verb} {b}: `lib` never reaches {BIN}: the caller passes "
                    f"what lib needs of it (step 8)")
        if lb == "parsers" and not a.startswith(f"{LIB}.brokerages"):
            return (f"layers: {a} {verb} {b}: only `lib.brokerages`, the parsers moving out, "
                    f"may reach `parsers`")
        pkg = f"{LIB}.brokerages"
        if (_in_package(a, pkg) and a != pkg and a not in PARSER_SHARED
                and _in_package(b, pkg) and b not in PARSER_SHARED):
            # The package root too: `from taxjson.lib.brokerages import x`
            # reaches a sibling through it.
            return (f"layers: {a} {verb} {b}: a parser reaches base, schema, _crypto_common "
                    f"and the toolbox, never another parser (step 5f)")
        return None
    if la is None:
        return None
    if lb is not None and LAYERS.index(lb) < LAYERS.index(la):
        return f"layers: {a} {verb} {b}: `{la}` sits below `{lb}`"
    if _in_package(b, BIN) and la not in MAY_IMPORT_BIN:
        return f"layers: {a} {verb} {b}: only {' and '.join(MAY_IMPORT_BIN)} may reach {BIN}"
    return None


def layer_violations(graph: dict, named=None) -> list:
    problems = []
    for a in sorted(graph):
        parts = a.split(".")
        if len(parts) > 1 and parts[1] not in LAYERS + OLD_PACKAGES:
            problems.append(f"layers: {a}: `{parts[1]}` has no place: give it a layer in "
                            "LAYERS, or a package outside them can reach anything")
        for verb, targets in (("imports", graph[a]), ("names", (named or {}).get(a, ()))):
            for b in sorted(targets):
                why = _edge_problem(a, verb, b)
                if why:
                    problems.append(why)
    return problems


# ------------------------------------------------------------- ruff, noqa
def _ruff_argv(*args) -> list:
    # -I: not `python_module_argv` (this script imports nothing of taxjson's),
    # but what it is for: no module from the current directory.
    return [sys.executable, "-I", "-m", "ruff", *args]


def _ruff_failed(why: str):
    return SystemExit(f"check_architecture: {why}\n"
                      "Nothing was recorded. scripts/dev-setup.sh installs the dev lock's "
                      "ruff; run this script with that venv's Python.")


def ruff_version(root: Path, runner=None) -> str:
    """The version, or refuse: no ruff is not zero hits."""
    runner = runner or subprocess.run
    r = runner(_ruff_argv("--version"), cwd=root, capture_output=True, text=True, check=False)
    words = (r.stdout or "").split()
    if r.returncode or len(words) < 2 or words[0] != "ruff":
        raise _ruff_failed(f"no ruff in {sys.executable}: {(r.stderr or r.stdout)[-200:].strip()}")
    return words[1]


def ruff_counts(root: Path, runner=None) -> dict:
    """{rule: {path: count}} from `ruff check` with pyproject's rule set,
    suppressions ignored: hiding a hit, however it is spelled, leaves it counted.
    rc 1 means hits only when there are hits: `python -m` of a missing or broken
    module exits 1 too."""
    runner = runner or subprocess.run
    r = runner(_ruff_argv("check", "--no-cache", "--ignore-noqa", "--output-format=json", *LINT_DIRS),
               cwd=root, capture_output=True, text=True, check=False)
    try:
        hits = json.loads(r.stdout)
    except (TypeError, ValueError):
        hits = None
    if (r.returncode not in (0, 1) or not isinstance(hits, list)
            or (r.returncode == 1 and not hits)):
        raise _ruff_failed(f"ruff failed (exit {r.returncode}): "
                           f"{(r.stderr or r.stdout or '')[-400:].strip()}")
    counts = defaultdict(lambda: defaultdict(int))
    for hit in hits:
        path = Path(hit["filename"]).resolve().relative_to(root.resolve()).as_posix()
        counts[hit["code"] or "syntax"][path] += 1
    return {k: dict(v) for k, v in counts.items()}


def _header(line: str) -> str:
    """A table header with its quotes and spacing gone."""
    return re.sub(r"[\"'\s]", "", line.split("#")[0])


def misplaced_ruff_settings(root: Path) -> list:
    """Ruff settings written as dotted keys or an inline table reach ruff, but
    ruff_config reads only [tool.ruff...] tables (it reads lines, not TOML), so
    they must be written in one. No baseline: there is nothing to record."""
    problems = []
    for p in _all_files(root):
        if p.name != "pyproject.toml":
            continue
        rel, table = p.relative_to(root).as_posix(), ""
        for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if ln.lstrip().startswith("["):
                table = _header(ln)
            elif table in ("", "[tool]") and "ruff" in ln.split("#")[0].lower():
                problems.append(f"ruff: {rel}:{n}: a ruff setting outside a [tool.ruff...] "
                                "table goes unseen here: move it into one")
    return problems


def ruff_config(root: Path) -> dict:
    """{file: the lines that shape ruff's answer}: a narrower rule set or a
    per-file ignore would otherwise read as an improvement."""
    found = {}
    for p in _all_files(root):
        if p.name in _RUFF_CONFIGS:
            lines = p.read_text(encoding="utf-8").splitlines()
        elif p.name == "pyproject.toml":
            lines, inside = [], False
            for ln in p.read_text(encoding="utf-8").splitlines():
                if ln.lstrip().startswith("["):
                    inside = bool(_RUFF_TABLE.match(_header(ln)))
                if inside:
                    lines.append(ln)
        else:
            continue
        lines = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
        if lines:
            found[p.relative_to(root).as_posix()] = lines
    return found


def _comments(text: str) -> list:
    """The real comments: a marker inside a string suppresses nothing. A file
    that does not tokenize keeps what came before the error (ruff reports
    the file itself)."""
    found = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                found.append(tok.string)
    except (tokenize.TokenError, SyntaxError):
        pass
    return found


def print_counts(root: Path) -> dict:
    """{"print": {path: raw print( calls}}, by the parse: not a comment, not
    `out.print_x(`."""
    counts = {}
    for p in _py_files(root, ("src",)):
        tree = _parsed(p)
        n = sum(1 for c in ast.walk(tree) if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name) and c.func.id == "print")
        if n:
            counts[p.relative_to(root).as_posix()] = n
    return {"print": counts}


# model/result's row views (Row and its subclasses there; a test holds
# the list to that file): a read through one names its field, not the class.
ROW_VIEWS = frozenset({"Row", "GainRow", "Holding", "Lot", "WashRecord"})


def _money_name(node) -> bool:
    """Whether a float( argument names money: any name, attribute or string
    in it has a MONEY_WORDS word. A method's name is not one (d.values()),
    nor a ROW_VIEWS class a row is read through (GainRow(e).quantity is
    units); any other called name is (Money(x), net_gain(e))."""
    methods = {id(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
    for n in ast.walk(node):
        text = (n.id if isinstance(n, ast.Name)
                and not (id(n) in methods and n.id in ROW_VIEWS)
                else n.attr if isinstance(n, ast.Attribute) and id(n) not in methods
                else n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else "")
        for word in _WORD.findall(text):
            w = word.lower()
            if w in MONEY_WORDS or (w.endswith("s") and w[:-1] in MONEY_WORDS):
                return True
    return False


def money_counts(root: Path) -> dict:
    """{"round": {path: bare round( calls}, "float": {path: float( calls on
    a money name}}, in src, by the parse."""
    rounds, floats = {}, {}
    for p in _py_files(root, ("src",)):
        r = f = 0
        for c in ast.walk(_parsed(p)):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name):
                if c.func.id == "round":
                    r += 1
                elif c.func.id == "float" and c.args and _money_name(c.args[0]):
                    f += 1
        rel = p.relative_to(root).as_posix()
        if r:
            rounds[rel] = r
        if f:
            floats[rel] = f
    return {"float": floats, "round": rounds}


def noqa_in_text(text: str) -> dict:
    """{code: count} of one file's suppression comments, as noqa_counts
    counts them (scripts/move_tests.py counts a split's fragments so)."""
    counts: dict = defaultdict(int)
    for comment in _comments(text):
        for pattern, prefix in ((_FILE_NOQA, "file:"), (_RANGE_NOQA, "range:"), (_NOQA, "")):
            m = pattern.search(comment)
            if m:
                codes = re.split(r"[,\s]+", m.group(1)) if m.group(1) else ["blanket"]
                for code in codes:
                    counts[prefix + (code if code == "blanket" else code.upper())] += 1
                break
    return dict(counts)


def noqa_counts(root: Path) -> dict:
    """{code: {path: count}}; a marker with no code counts as `blanket`, a
    whole file's as `file:CODE` or `file:blanket`, a range's as `range:CODE`."""
    counts = defaultdict(lambda: defaultdict(int))
    for p in _py_files(root, LINT_DIRS):
        rel = p.relative_to(root).as_posix()
        for code, n in noqa_in_text(p.read_text(encoding="utf-8", errors="replace")).items():
            counts[code][rel] += n
    return {k: dict(v) for k, v in counts.items()}


# ----------------------------------------------------------------- length
class _Lengths(ast.NodeVisitor):
    def __init__(self):
        self.scope, self.found = [], []

    def _fn(self, node):
        name = ".".join(self.scope + [node.name])
        n = node.end_lineno - node.lineno + 1
        if n > MAX_LINES:
            self.found.append((name, n, node.lineno))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _fn

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


@functools.lru_cache(maxsize=None)
def _tree(text: str, filename: str) -> ast.AST:
    return ast.parse(text, filename=filename)


def _parsed(p: Path) -> ast.AST:
    """A file's tree, parsed once for its text: long_functions and
    print_counts each walk src."""
    return _tree(p.read_text(encoding="utf-8"), str(p))


def long_functions(root: Path) -> dict:
    """{'path::qualified name': [(length, line), ...] longest first}."""
    found = defaultdict(list)
    for p in _py_files(root, LENGTH_DIRS):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("packages/") and "/tests/" in rel:
            continue
        v = _Lengths()
        v.visit(_parsed(p))
        for name, n, line in v.found:
            found[rel + _KEY + name].append((n, line))
    return {k: sorted(v, reverse=True) for k, v in found.items()}


def _pair_lengths(cur: dict, base: dict) -> tuple:
    """(same-place pairs, new, gone, moved). A function is itself by file and
    qualified name, so one shrinking cannot pay for another growing. Two defs
    of one name in one file pair by length, longest first. A function gone
    from one file and in another under its name, at exactly its length, moved:
    anything else is a new function, and a shorter `main` elsewhere is one."""
    pairs, new, gone = [], [], []
    for key in sorted(set(cur) | set(base)):
        now, was = cur.get(key, []), sorted(base.get(key, []), reverse=True)
        pairs += [(key, n, line, w) for (n, line), w in zip(now, was)]
        new += [(key, n, line) for n, line in now[len(was):]]
        gone += [(key, w) for w in was[len(now):]]
    moved = []
    for item in list(new):
        name = item[0].split(_KEY, 1)[1]
        for old in gone:
            if old[1] == item[1] and old[0].split(_KEY, 1)[1] == name:
                moved.append((item[0], item[1], item[2], old[0]))
                new.remove(item)
                gone.remove(old)
                break
    return pairs, new, gone, moved


def _where(key: str, line: int) -> tuple:
    path, name = key.split(_KEY, 1)
    return f"{path}:{line}", name


def compare_lengths(cur: dict, base: dict) -> tuple:
    pairs, new, gone, moved = _pair_lengths(cur, base)
    grew, shrank = [], []
    for key, n, line, was in pairs:
        where, name = _where(key, line)
        if n > was:
            grew.append(f"length: {where}: {name} is {n} lines, baseline {was}")
        elif n < was:
            shrank.append(f"length: {where}: {name} is {n} lines, baseline {was}")
    for key, n, line in new:
        where, name = _where(key, line)
        grew.append(f"length: {where}: {name} is {n} lines, over {MAX_LINES} and not on the list")
    for key, was in gone:
        where, name = key.split(_KEY, 1)
        shrank.append(f"length: {where}: {name}: gone or under {MAX_LINES} lines, baseline {was}")
    for key, n, line, old in moved:
        where, name = _where(key, line)
        shrank.append(f"length: {where}: {name} moved from {old.split(_KEY, 1)[0]} at {n} lines")
    return grew, shrank


# ---------------------------------------------------------------- compare
def _totals(counts: dict) -> dict:
    return {k: sum(v.values()) for k, v in counts.items()}


def compare_counts(kind: str, cur: dict, base: dict) -> tuple:
    """(grew, shrank), per rule and file: a hit removed in one file does not
    pay for a new one in another."""
    grew, shrank = [], []
    for code in sorted(set(cur) | set(base)):
        now, was = cur.get(code, {}), base.get(code, {})
        for path in sorted(set(now) | set(was)):
            c, b = now.get(path, 0), was.get(path, 0)
            if c > b:
                grew.append(f"{kind}: {code}: {path}: {c}, baseline {b}")
            elif c < b:
                shrank.append(f"{kind}: {code}: {path}: {c}, baseline {b}")
    return grew, shrank


def compare_edges(cur: set, base: set) -> tuple:
    grew = [f"imports: new edge, not on the allow-list: {e}" for e in sorted(cur - base)]
    shrank = [f"imports: no longer in a cycle or gone: {e}" for e in sorted(base - cur)]
    return grew, shrank


def current_state(root: Path, runner=None) -> dict:
    src = root / "src"
    graph = import_graph(src)
    comps = cycles(graph)
    named = named_modules(src)
    return {
        "graph": graph,
        "cycles": comps,
        "named": named,
        "import_edges": allow_listed_edges(graph, comps, named),
        "plugin_violations": plugin_violations(root),
        "ruff_version": ruff_version(root, runner),
        "ruff": ruff_counts(root, runner),
        "ruff_config": ruff_config(root),
        "ruff_misplaced": misplaced_ruff_settings(root),
        "noqa": noqa_counts(root),
        "function_length": long_functions(root),
        "print_calls": print_counts(root),
        "money": money_counts(root),
    }


def _doc(state: dict) -> dict:
    return {
        "_": "Written by scripts/check_architecture.py; see its docstring. Do not edit by hand.",
        "max_function_lines": MAX_LINES,
        "ruff_version": state["ruff_version"],
        "ruff_config": dict(sorted(state["ruff_config"].items())),
        "import_edges": sorted(state["import_edges"]),
        "ruff": {k: dict(sorted(v.items())) for k, v in sorted(state["ruff"].items())},
        "noqa": {k: dict(sorted(v.items())) for k, v in sorted(state["noqa"].items())},
        "function_length": {k: [n for n, _ in v]
                            for k, v in sorted(state["function_length"].items())},
        "print_calls": {k: dict(sorted(v.items())) for k, v in sorted(state["print_calls"].items())},
        "money": {k: dict(sorted(v.items())) for k, v in sorted(state["money"].items())},
    }


def _accepted_doc(state: dict, base: dict, kinds) -> dict:
    """The baseline with the named kinds as they are now, the others as they were."""
    doc = dict(base)
    now = _doc(state)
    for kind in kinds:
        for key in _KIND_KEYS[kind]:
            doc[key] = now[key]
    return {**now, **doc}


def _shrunk_doc(state: dict, base: dict) -> dict:
    """The baseline with every improvement taken and nothing added. A long
    function that moved at its recorded length follows its new path."""
    doc = dict(base)
    doc["import_edges"] = sorted(state["import_edges"] & set(base["import_edges"]))
    for kind in ("ruff", "noqa", "print_calls", "money"):
        merged = {}
        for code, files in base.get(kind, {}).items():
            now = state[kind].get(code, {})
            kept = {f: min(n, now.get(f, 0)) for f, n in sorted(files.items())}
            kept = {f: n for f, n in kept.items() if n}
            if kept:
                merged[code] = kept
        doc[kind] = dict(sorted(merged.items()))
    pairs, _new, _gone, moved = _pair_lengths(state["function_length"], base["function_length"])
    lengths = defaultdict(list)
    for key, n, _line, was in pairs:
        lengths[key].append(min(n, was))
    for key, n, _line, _old in moved:
        lengths[key].append(n)
    doc["function_length"] = {k: sorted(v, reverse=True) for k, v in sorted(lengths.items())}
    return doc


def _compare(state: dict, base: dict) -> tuple:
    """({kind: what grew}, what shrank) of this state against this baseline."""
    grown, shrank = {}, []
    for kind, (g, s) in (("imports", compare_edges(state["import_edges"], set(base["import_edges"]))),
                         ("ruff", compare_counts("ruff", state["ruff"], base["ruff"])),
                         ("noqa", compare_counts("noqa", state["noqa"], base["noqa"])),
                         ("length", compare_lengths(state["function_length"], base["function_length"])),
                         ("print", compare_counts("print", state["print_calls"], base.get("print_calls", {}))),
                         ("money", compare_counts("money", state["money"], base.get("money", {})))):
        grown[kind] = list(g)
        shrank += s
    if state["ruff_config"] != base.get("ruff_config"):
        grown["ruff"].append("ruff: the rule set changed (ruff.toml, or [tool.ruff] in a "
                             "pyproject.toml): it decides what the counts mean")
    if base.get("max_function_lines") != MAX_LINES:
        grown["length"].append(f"length: MAX_LINES is {MAX_LINES}, the baseline was recorded "
                               f"at {base.get('max_function_lines')}")
    return grown, shrank


def _kinds(text: str) -> list:
    names = [k.strip() for k in text.split(",") if k.strip()]
    if names == ["all"]:
        return list(KINDS)
    bad = sorted(set(names) - set(KINDS))
    if bad or not names:
        raise argparse.ArgumentTypeError(
            f"{','.join(bad) or text!r}: name one or more of {', '.join(KINDS)}, or all")
    return names


def main(argv=None, root: Path = REPO, baseline: Path = BASELINE, runner=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--shrink", action="store_true",
                      help="record the improvements; adds nothing but a long function's move")
    mode.add_argument("--accept-growth", type=_kinds, metavar="KINDS",
                      help=f"record these as they are now: {', '.join(KINDS)} or all, comma-separated")
    args = ap.parse_args(argv)
    accept = args.accept_growth or []

    if not baseline.exists() and set(accept) != set(KINDS):
        print(f"check_architecture: {baseline.name} is missing: restore it from git "
              "(--accept-growth=all writes a new one, and forgives everything).")
        return 2
    try:
        state = current_state(root, runner)
    except SyntaxError as e:
        print(f"check_architecture: {e.filename}:{e.lineno}: {e.msg}\nNothing was recorded.")
        return 2
    base = json.loads(baseline.read_text(encoding="utf-8")) if baseline.exists() else _doc(state)
    other_ruff = base.get("ruff_version") != state["ruff_version"]
    if args.shrink and other_ruff:
        print(f"check_architecture: ruff {state['ruff_version']} here, the baseline is ruff "
              f"{base.get('ruff_version')}: its counts are not comparable, so nothing was recorded. "
              "Use the dev lock's ruff, or after a lock regeneration --accept-growth=ruff.")
        return 2
    if accept:
        base = _accepted_doc(state, base, accept)
        baseline.write_text(json.dumps(base, indent=1) + "\n", encoding="utf-8")
        other_ruff = base.get("ruff_version") != state["ruff_version"]

    problems = (layer_violations(state["graph"], state["named"]) + state["plugin_violations"]
                + state["ruff_misplaced"])
    grown, shrank = _compare(state, base)
    refused = args.shrink and (problems or any(grown.values()))
    if args.shrink and not refused:
        base = _shrunk_doc(state, base)
        baseline.write_text(json.dumps(base, indent=1) + "\n", encoding="utf-8")
        grown, shrank = _compare(state, base)

    biggest = len(state["cycles"][0]) if state["cycles"] else 0
    print(f"architecture: {len(state['graph'])} modules, largest import cycle {biggest}, "
          f"{len(state['import_edges'])} edges on the allow-list, "
          f"{sum(_totals(state['ruff']).values())} ruff hits, "
          f"{sum(_totals(state['noqa']).values())} noqa markers, "
          f"{sum(len(v) for v in state['function_length'].values())} functions over {MAX_LINES} lines, "
          f"{sum(_totals(state['print_calls']).values())} print calls in src, "
          f"{_totals(state['money']).get('round', 0)} bare round( and "
          f"{_totals(state['money']).get('float', 0)} money float( calls")
    grew_kinds = [k for k in KINDS if grown[k]]
    for line in problems + [g for k in grew_kinds for g in grown[k]] + shrank:
        print(line)
    if other_ruff and (grown["ruff"] or any(s.startswith("ruff:") for s in shrank)):
        print(f"Note: ruff {state['ruff_version']} here, the baseline is ruff "
              f"{base.get('ruff_version')}: that may be all this is. Use the dev lock's ruff; "
              "after a lock regeneration, --accept-growth=ruff records it (docs/releasing.md).")
    if problems:
        print("It got worse. A layer or placement rule has no baseline: fix it.")
    if grew_kinds:
        print("It got worse. Fix it; if the growth is the point of the change, "
              f"scripts/check_architecture.py --accept-growth={','.join(grew_kinds)} records it.")
    if refused:
        print("Nothing was recorded: --shrink takes no improvement while something got worse.")
    elif shrank:
        print("It got better. scripts/check_architecture.py --shrink records it.")
    return 1 if (problems or grew_kinds or shrank) else 0


if __name__ == "__main__":
    sys.exit(main())
