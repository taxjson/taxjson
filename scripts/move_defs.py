#!/usr/bin/env python3
"""move_defs: move a module's top-level definitions into new modules,
whole, the module left re-exporting them (PLAN.md step 10: lib/core.py
into the engine package).

Usage: scripts/move_defs.py TABLE [--dry-run] [--repo=PATH]
       scripts/move_defs.py --check [BASE [HEAD]] [--allow=PATH,...] [--table=PATH]
                            [--trees] [--repo=PATH]

TABLE (tracked at scripts/engine-table.txt, grown batch by batch) has a
row per new module, `SOURCE TARGET DESCRIPTION` from the first column,
then the names it takes on the indented lines below it; a line whose first
character but blanks is `#` is a comment:

    src/taxjson/lib/core.py src/taxjson/engine/__init__.py The gains engines
    src/taxjson/lib/core.py src/taxjson/engine/consts.py The engines' money constants
        _ZERO _ONE _HALF
        _shown_if_float

A row whose TARGET is a package's `__init__.py` takes no names: it makes
the package, the description its docstring. A row whose TARGET exists is
an earlier batch's, and is skipped once it holds (no name of it is still
defined in SOURCE, which imports each from TARGET); the rows not done are
the batch, and follow every row done. For each module row of the batch:
  1. each named top-level statement (a def, a class, an assignment) is cut
     from SOURCE with its decorators, the comment lines directly above it
     (not a section's banner, `# -----`, which stays with its section) and
     each store into it at module scope (`NAME.__doc__ = ...`);
  2. TARGET holds a docstring (the description, then "Moved from
     lib/core.py (PLAN step 10)."), SOURCE's `from __future__` imports,
     each import of SOURCE the moved code reads (a name in a string
     annotation counts), cut to those names, in SOURCE's order and
     spelling (`as _LO` kept), then the imports from the batch's earlier
     rows' modules, in table order, then the statements in SOURCE's order,
     their text byte for byte;
  3. SOURCE imports the names back (`from MODULE import ...` per row, in
     table order, after its imports), lists them in `__all__` (the shim
     keeps every name, private ones too: ruff reads them as re-exports),
     and drops each import only the moved code read (its noqa with it).
Then scripts/mutation_audit.py's TARGET_FUNCS rows of a moved name name its
module; the knowledge pack's code references to SOURCE (`` `path` —
`name` `` in docs/*.md, AGENTS.md and KNOWN_ISSUES.md) follow: a name its
definition, a method or fragment the name before it, each placed name
found in its new file; a reference the move splits becomes a `path —
names` group per module, `; ` between. What it cannot place is listed for a
hand edit, and left. Last, the repository's own check_architecture
records the baseline with `--accept-growth=ruff,money,length,noqa,print`
(the hits, long functions, float money, noqa markers and print calls that
moved with their code; --check proves no total grew). A failure after the
first write undoes every change; a dry run plans the batch, prints it, and
writes nothing. The seams --check's seams stage will fail are listed
(read after the move's own TARGET_FUNCS rewrites), with what is left to
B0b, and so is each comment the move leaves with no statement after it (a
section whose statements all moved), to settle in a commit apart from the
batch.

Refused, before anything is written (each by hand, in a commit of its own):
  - a table line that is no row or name, a name or a TARGET twice, a
    SOURCE that is a TARGET, a module row without names or a package row
    with some, a row done after one that is not;
  - a name that is no top-level def, class or assignment of SOURCE, or one
    SOURCE binds more than once (twice, by an import too, in a compound
    statement, `del` or `global`); a statement binding a name that moves
    with one that stays or moves elsewhere; one that shares a line; a star
    import in SOURCE;
  - moved code reading a name that stays in SOURCE (TARGET would import it
    back from SOURCE, which imports TARGET: a cycle), one a later row takes
    (a target importing a module later in the table), or an import SOURCE
    binds more than once or below the moved code;
  - a cycle (one rule, the move's and --check's, over every row of the
    table): a TARGET importing a SOURCE module that imports the TARGET
    back, directly or through the table's other modules (a shim of an
    earlier row that does not is no cycle);
  - a module of a country package (`ca` or `us` below the layer,
    src/taxjson/engine/ca/) reading the other's, or a shared module of the
    layer reading either (JOINS apart: by_country picks an engine);
  - moved code that reads its own module or where it runs (`__file__`,
    `__name__`, `globals()`, `vars()`, `sys.modules[__name__]`, inspect's
    and traceback's location readers, a frame, `__module__`, a class body's
    bare `__module__`), declares `global`, or needs a relative import;
  - SOURCE code at module level that runs more than imports, definitions,
    assignments, stores into a name and a main guard;
  - an import SOURCE drops that another module imports from SOURCE, or
    one that loses a name and carries a comment; one marked `noqa: F401`
    or a bare `noqa` (a re-export; on any of its lines, in any case) only
    the moved code reads: left, it is an F401 hit check_architecture counts
    (it reads past noqa), and dropped, an importer may lose it;
  - a TARGET whose folder is no package and no row makes one, a package
    row's folder that exists, a new path git ignores.

--check: a batch's proof, run as BASE's copy (from git, isolated, never
the checkout's: a batch cannot change its own proof):

    T=$(mktemp -d) && git --no-replace-objects archive BASE \\
      scripts/move_defs.py scripts/extract_support.py scripts/move_tests.py \\
      scripts/verify_test_moves.py scripts/moved.py |
      tar -x -C "$T" && python3 -I -S "$T/scripts/move_defs.py" \\
      --check BASE HEAD --repo=.

  1. text: BASE's tool, run on BASE's files with HEAD's table (--table,
     default scripts/engine-table.txt), writes HEAD's tree: every path,
     mode and blob as git holds them (an entry twice, a symlink or a
     submodule refused), but the .md docs --allow names, outside tests/,
     which are listed. A batch that fails it has none of its code run;
  2. pins: no file below a tests/ folder (tests/golden among them)
     changed; each TARGET_FUNCS row resolves to the function BASE's did;
     baseline: in the architecture baseline BASE's tool recorded, no total
     per kind and code (ruff, noqa, print, money) grows, one may shrink (an
     import the move drops takes its hit or its noqa with it); each file the
     batch does not write keeps BASE's counts; the long functions are
     BASE's, re-keyed to the modules they moved to; every other key is
     BASE's (what --accept-growth recorded only moved);
  3. AST: each moved statement's `ast.dump` and its comment lines are
     BASE's, in SOURCE's order; a new module holds its docstring, imports
     and the moved statements, a new package's `__init__.py` its docstring;
     SOURCE keeps BASE's other statements, in order, and gains only imports
     and `__all__`;
  4. bindings: every global each moved statement and each one SOURCE keeps
     reads (its functions' and classes' by symtable, at module level and in
     string annotations by the AST) is bound to BASE's origin: the same
     `(module, name)` import, or the module the table moves it to;
     seams: in HEAD's tests/, scripts/ and packages/*/tests, and the code
     in their strings (dedented; an f-string's, a `+` join's and a `%` or
     `.format` template's values read as one name, FIELD, and a target
     naming it as computed), no form it reads patches or stores through a
     SOURCE a name SOURCE no longer defines (every row's of the table,
     earlier batches' too) or an import the new modules read, or reads
     SOURCE's file or text. The forms it reads: `mock.patch` and
     `monkeypatch.setattr` strings ("SOURCE.X"; an f-string's, a `+`
     join's, a `%` or `.format` template's "SOURCE.{...}", computed),
     `patch.object` (positional or keyword), `patch.multiple`,
     `setattr`/`delattr`, a store or `del` through the module by any alias
     (`import ... as`, a dotted import, `from PARENT import`, a name bound
     to `import_module(SOURCE)` or `sys.modules[SOURCE]`, or to another
     such name: followed, though such a binding fails the stage by itself,
     the dotted name it hands over being a target below, TJ-3927), `vars()`
     or `__dict__` stores and `patch.dict`;
     `importlib.reload` of the module by any alias (it runs the shim again,
     not the modules its names moved to); SOURCE's dotted name itself (a
     target it cannot follow) as a patch target, joined (`+`, `%`,
     `.format`), bound to a name (an assignment, annotated or not, a
     walrus), handed to `import_module` (by keyword too), `__import__`,
     `reload` or any method of `sys.modules` (get, pop, ...), a
     `sys.modules[...]` key (a swap, `del`, or a read), a dict's key
     (`patch.dict(sys.modules, {SOURCE: ...})` among them) or a key stored
     into another mapping; a script in a string (more than one line, no
     docstring) naming SOURCE (its dotted name, its path, `from PARENT
     import`) that does not parse, as a file that does not;
     `inspect.getsource(SOURCE)` and its kin, `SOURCE.__spec__` and the
     like, `SOURCE.__file__` (but for its folder or a parent:
     `Path(...).parent`, `.parents[k]`, `os.path.dirname`, through
     `.resolve()`, `.absolute()` or realpath, abspath and normpath, the
     shim staying at SOURCE's path), a path ending in SOURCE's, its folder
     joined back to its file's name (by `/`, os.path.join, joinpath, `+`
     or an f-string; the folder by any name an assignment, annotated or
     not, a tuple's item by item, a walrus, a loop over a display or a
     default argument binds), a listing of its folder (Path's glob, rglob,
     iterdir and walk, os.listdir, scandir and walk, glob.glob and iglob;
     a parent's too, but a glob of another folder below it or a listing of
     every module below it: walk, or rglob or a `**` pattern with `**`
     alone before its last part, `*` or `*.py`, the new modules being
     there too), where a moved name lives (`__module__`, `__code__`,
     getsourcefile). The forms it does not read (none on the tree,
     TJ-2323): a target built by `".".join`, `str.format(T, n)`,
     `format_map` or from `__name__`, a `patch.object` under another name
     or through `functools.partial`, a helper handed the module,
     `object.__setattr__`, `getattr(PARENT, "LAST")`, `inspect.getmodule`,
     `find_spec(...).origin`, `sys.modules` patched from pairs
     (`patch.dict(sys.modules, [(SOURCE, m)])`, `sys.modules.update([...])`),
     a file name held by a name, `with_name` on a path below its folder
     (`(F / "x.py").with_name(...)`; on `__file__` it is a read), a folder
     held by an attribute (`self.lib = ...`) or handed by keyword
     (`glob.glob(p, root_dir=F)`, `os.walk(top=F)`).
     B0b's helper, tests/_engine.py, the one file that may name SOURCE's
     path (B0B_HELPER: it patches through, reads and searches the source
     and the engine together by design; the WIDENED rule, the planted
     paths and CUT_KEYS go through it), passes by its repo path at the
     count of seams B0b's review records, exactly (0: it reaches lib.core
     and the engine's modules through module objects, which the stage does
     not follow; tests/test_engine_seams.py proves it reaches each); a
     whole-module read passes only by repo path at its B0B_WHOLE_READS
     entry's count, exactly (fewer fail as well, asking for the entry to
     be lowered): none since B0b made tests/test_engine_run.py's eleven
     `inspect.getsource(core)` read the engine's whole text through the
     helper; each counted one named in the stage's line. A test or script
     that patches a moved name through lib/core.py, or reads its file or
     text, fails here, naming each site: it goes through the helper;
  5. runtime: SOURCE imported in a fresh interpreter of each tree, with a
     HOME of its own: each name BASE's SOURCE defines or re-exports is in
     HEAD's, `is` its defining module's object (a function's globals that
     module's), and has BASE's shape (qualname, module, doc, defaults,
     annotations, dataclass params and fields, slots, MRO, members,
     lru_cache parameters; the new modules' names read as SOURCE's); HEAD
     loads BASE's modules of the package and the new ones, no more, no
     fewer; each new module imports alone;
  6. layers: HEAD's check_architecture passes without --accept-growth, no
     module of a batch's layer breaks the country rule above, and no
     table's TARGET makes a cycle (the rule above).
Each stage's line says it passed only when it did. B0b put the seams
themselves through tests/_engine.py (the patch sites, the getsource sites,
CUT_KEYS, Tripwire, the site regexes, WIDENED, mutation_triage's paths).
What stays the gate's: the forms the seams stage does not read, and any
outcome a move changes in a test (an expected message naming where code
lives among them): the golden ledgers, the pins, the suite.
--trees lets BASE and HEAD be trees (the tests' synthetic repositories).
Exit 0 with a line per check; 1 with each difference; 2 when the arguments
name no batch to check.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import symtable
import sys
import tempfile
import textwrap
import warnings
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TABLE = "scripts/engine-table.txt"
MUTATION = "scripts/mutation_audit.py"
ARCH = "scripts/check_architecture.py"
# What check_architecture records anew after a move: the hits, long
# functions, float money, noqa markers and print calls that moved with
# their code. --check's baseline stage proves no total grew.
ACCEPT = "--accept-growth=ruff,money,length,noqa,print"
BASELINE = "scripts/architecture-baseline.json"
_COUNTS = ("ruff", "noqa", "print_calls", "money")
PACK = ("docs/*.md", "AGENTS.md", "KNOWN_ISSUES.md")
MARK = "Moved from {} (PLAN step 10)."
ALL_NOTE = "# What moved out of this module, re-exported by name (PLAN step 10)."
# The country packages below a layer, and the one shared module that may
# read both (it picks an engine; design section 1).
COUNTRIES = ("ca", "us")
JOINS = ("by_country",)
TOOLS = ("scripts/move_defs.py", "scripts/extract_support.py", "scripts/move_tests.py",
         "scripts/verify_test_moves.py", "scripts/moved.py")
_PY = re.compile(r"src(?:/[A-Za-z_][A-Za-z0-9_]*)+\.py")
_CODING = re.compile(r"^[ \t\f]*#.*?coding[:=]")
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)
# Names that read the module they are written in, wherever they are (a
# class body's `__module__` is its module's name).
OWN_NAMES = frozenset({"__module__"})


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_move_defs_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ES = _load("extract_support")
MT = ES.MT


class Refused(Exception):
    pass


# ------------------------------------------------------------------ table

class Row:
    def __init__(self, source: str, target: str, about: str, line: int):
        self.source, self.target, self.about, self.line = source, target, about, line
        self.names: List[str] = []

    @property
    def package(self) -> bool:
        return self.target.endswith("/__init__.py")

    @property
    def module(self) -> str:
        return module_name(self.target)

    def __repr__(self) -> str:
        return f"Row({self.target})"


def module_name(rel: str) -> str:
    """`src/a/b/c.py` -> `a.b.c`; `src/a/b/__init__.py` -> `a.b`."""
    parts = rel[len("src/"):-len(".py")].split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def short(rel: str) -> str:
    """SOURCE below its top package: `src/taxjson/lib/core.py` -> `lib/core.py`."""
    return "/".join(rel.split("/")[2:]) or rel


def read_table(text: str) -> List[Row]:
    rows: List[Row] = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0] in " \t":
            if not rows:
                raise Refused(f"the table's line {n}: names before any row")
            for name in raw.split():
                if not name.isidentifier() or (name.startswith("__") and name.endswith("__")):
                    raise Refused(f"the table's line {n}: {name!r} is no name a row can take")
                rows[-1].names.append(name)
            continue
        parts = raw.split(None, 2)
        if len(parts) < 3:
            raise Refused(f"the table's line {n}: want `SOURCE TARGET DESCRIPTION`: {raw!r}")
        source, target, about = parts[0], parts[1], parts[2].strip().rstrip(".")
        for p in (source, target):
            if not _PY.fullmatch(p) or p.count("/") < 2:
                raise Refused(f"the table's line {n}: {p!r} is no path to a module below "
                              f"src/<package>/")
        if '"""' in about or "\\" in about or not about:
            raise Refused(f"the table's line {n}: a description without `\"\"\"` or `\\`")
        rows.append(Row(source, target, about, n))
    _table_problems(rows)
    return rows


def _table_problems(rows: List[Row]) -> None:
    targets = [r.target for r in rows]
    twice = sorted({t for t in targets if targets.count(t) > 1})
    if twice:
        raise Refused(f"the table names a target twice: {', '.join(twice)}")
    both = sorted({r.source for r in rows} & set(targets))
    if both:
        raise Refused(f"the table moves names into a source: {', '.join(both)}")
    names = [(r.source, n) for r in rows for n in r.names]
    twice = sorted({n for s, n in names if names.count((s, n)) > 1})
    if twice:
        raise Refused(f"the table names {', '.join(twice)} twice")
    for r in rows:
        if r.package and r.names:
            raise Refused(f"the table's line {r.line}: a package row ({r.target}) takes no names")
        if not r.package and not r.names:
            raise Refused(f"the table's line {r.line}: {r.target} takes no names")


# ------------------------------------------------------------- the layers

def country_of(module: str) -> Optional[str]:
    """`ca` or `us` for a module of a country package below its layer
    (`taxjson.engine.ca.fold`, `taxjson.engine.ca`), else None."""
    parts = module.split(".")
    return parts[2] if len(parts) >= 3 and parts[2] in COUNTRIES else None


def layer_of(module: str) -> str:
    return ".".join(module.split(".")[:2])


def layer_problem(a: str, b: str) -> Optional[str]:
    """Why module A, of a layer a batch moves into, may not import B."""
    if layer_of(a) != layer_of(b):
        return None
    ca, cb = country_of(a), country_of(b)
    if ca and cb and ca != cb:
        return f"{a} imports {b}: one country's engine never reads the other's"
    if ca is None and cb and a.split(".")[-1] not in JOINS:
        return f"{a} imports {b}: a shared module reads no country's engine ({', '.join(JOINS)} apart)"
    return None


def imported_modules(tree: ast.AST, module: str, is_pkg: bool = False) -> Set[str]:
    """Every module an import statement anywhere in TREE names (a name
    imported from a module counted as the module and as `module.name`)."""
    out: Set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                parts = module.split(".")
                drop = n.level - (1 if is_pkg else 0)
                parts = parts[:len(parts) - drop] if drop else parts
                base = ".".join(parts + ([n.module] if n.module else []))
            out.add(base)
            out |= {f"{base}.{a.name}" for a in n.names if a.name != "*"}
    return out


def layer_problems(tree_root: Path, layers: Set[str]) -> List[str]:
    """The country rule over every module of LAYERS (`taxjson.engine`) in a tree."""
    out = []
    for layer in sorted(layers):
        d = tree_root / "src" / Path(*layer.split("."))
        for p in sorted(d.rglob("*.py")) if d.is_dir() else []:
            rel = p.relative_to(tree_root).as_posix()
            mod = module_name(rel)
            tree = ast.parse(MT.read_exact(p))
            for b in sorted(imported_modules(tree, mod, p.name == "__init__.py")):
                why = layer_problem(mod, b)
                if why:
                    out.append(f"layers: {rel}: {why}")
    return out


def import_graph(root: Path, rows: List[Row], texts: Dict[str, str]) -> Dict[str, Set[str]]:
    """Each row's SOURCE and TARGET module -> the modules its imports name,
    from TEXTS (a planned module) or its file under ROOT."""
    out: Dict[str, Set[str]] = {}
    for rel in dict.fromkeys(x for r in rows for x in (r.source, r.target)):
        p = root / rel
        text = texts.get(rel) or (MT.read_exact(p) if p.is_file() else None)
        if text is not None:
            mod = module_name(rel)
            out[mod] = imported_modules(ast.parse(text), mod, rel.endswith("/__init__.py"))
    return out


def cycle_problems(graph: Dict[str, Set[str]], rows: List[Row]) -> List[str]:
    """The one cycle rule, of the move and of --check: a TARGET importing a
    SOURCE module is a cycle when that module imports the TARGET back,
    directly or through the table's other modules (a shim imports what
    moved out of it; another source's shim is a real path too)."""
    sources = {module_name(r.source) for r in rows}
    targets = {r.module for r in rows}
    out = []
    for t in sorted(targets & set(graph)):
        for s in sorted(graph[t] & sources):
            seen, todo = {s}, [s]
            while todo:
                m = todo.pop()
                if t in graph.get(m, ()):
                    out.append(f"{t} imports {s}, which imports {t} back"
                               + (f" (through {m})" if m != s else "") + ": a cycle")
                    todo = []
                    break
                for x in sorted(graph.get(m, set()) - seen):
                    seen.add(x)
                    todo.append(x)
    return out


# --------------------------------------------------------------- reading

def annotation_names(stmt: ast.AST) -> Set[str]:
    """The names the string annotations in STMT name (`"Optional[Row]"`)."""
    out: Set[str] = set()
    for n in ast.walk(stmt):
        anns: List[Optional[ast.AST]] = []
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            anns = [x.annotation for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg,
                                           a.kwarg] if x is not None] + [n.returns]
        elif isinstance(n, ast.AnnAssign):
            anns = [n.annotation]
        for ann in anns:
            for c in ast.walk(ann) if ann is not None else ():
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    try:
                        expr = ast.parse(c.value.strip(), mode="eval")
                    except SyntaxError:
                        continue
                    out |= {x.id for x in ast.walk(expr) if isinstance(x, ast.Name)}
    return out


def reads(stmt: ast.stmt) -> Set[str]:
    """The module-level names STMT reads (a function's own names are its
    own), string annotations included."""
    return ES.module_reads(stmt) | annotation_names(stmt)


def store_roots(stmt: ast.stmt) -> Optional[Set[str]]:
    """The names a module-level store writes into (`X.a = v`, `X[k] += v`,
    `del X.a`), or None when STMT is no store."""
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
        targets = [stmt.target]
    elif isinstance(stmt, ast.Delete):
        targets = stmt.targets
    else:
        return None
    roots = set()
    for t in targets:
        if not isinstance(t, (ast.Attribute, ast.Subscript)):
            return None
        root = ES._root(t)
        if root is None:
            return None
        roots.add(root)
    return roots


def _bound(a: ast.alias) -> str:
    return (a.asname or a.name).split(".")[0]


def is_reexport(a: ast.alias) -> bool:
    return a.asname is not None and a.asname == a.name


def _plain(a: ast.alias) -> ast.alias:
    """A's import in a new module: `X as X` (a re-export) written `X`."""
    return ast.alias(name=a.name, asname=None if is_reexport(a) else a.asname)


def first_line(stmt: ast.stmt) -> int:
    return min([stmt.lineno] + [d.lineno for d in getattr(stmt, "decorator_list", ())])


class Module:
    """One module's top level, as the tool reads it."""

    def __init__(self, path: Path, rel: str):
        if not path.is_file() or path.is_symlink():
            raise Refused(f"no {rel}")
        self.rel, self.path, self.module = rel, path, module_name(rel)
        self.text = MT.read_exact(path)
        self.nl = ES._line_end(self.text)
        self.lines = MT.split_lines(self.text)
        try:
            self.tree = ast.parse(self.text)
        except SyntaxError as e:
            raise Refused(f"{rel} does not parse ({e.msg}, line {e.lineno})") from e
        self.body = self.tree.body
        self.import_end = ES._import_end(self.tree)
        head = 1 if self.lines and self.lines[0].startswith("#!") else 0
        for i in range(min(2, len(self.lines))):
            if _CODING.match(self.lines[i]):
                head = i + 1
        self.floor, self.head = max(self.import_end, head), head
        self.defs: Dict[str, ast.stmt] = {}
        self.count: Dict[str, int] = {}
        self.imports: Dict[str, List[Tuple[ast.stmt, ast.alias]]] = {}
        self.other: Dict[str, int] = {}
        self.stores: List[Tuple[ast.stmt, Set[str]]] = []
        self.code: List[ast.stmt] = []
        for i, n in enumerate(self.body):
            self._read(i, n)
        self.declared = {g for x in ast.walk(self.tree) if isinstance(x, ast.Global)
                         for g in x.names}
        self.star = next((x.lineno for x in ast.walk(self.tree) if isinstance(x, ast.ImportFrom)
                          and any(a.name == "*" for a in x.names)), None)

    def _read(self, i: int, n: ast.stmt) -> None:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                self.imports.setdefault(_bound(a), []).append((n, a))
                self.count[_bound(a)] = self.count.get(_bound(a), 0) + 1
            return
        roots = store_roots(n)
        if roots is not None:
            self.stores.append((n, roots))
            return
        if isinstance(n, ast.AnnAssign) and n.value is None:
            return                  # `X: int` binds nothing
        if isinstance(n, _DEFS):
            for name in ES.module_binds(n):
                self.count[name] = self.count.get(name, 0) + 1
                self.defs.setdefault(name, n)
            return
        if (i == 0 and isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)) or isinstance(n, ast.Pass):
            return
        for name in ES.module_binds(n):
            self.other.setdefault(name, n.lineno)
        if not ES._is_main_guard(n):
            self.code.append(n)

    def bound_twice(self, name: str) -> Optional[str]:
        if self.count.get(name, 0) > 1:
            return "twice" if name not in self.imports else "also by an import"
        if name in self.other:
            return f"also at line {self.other[name]}"
        if name in self.declared:
            return "also through `global`"
        return None

    def span(self, n: ast.stmt) -> Tuple[int, int]:
        """N's 0-based first and last lines: its decorators and the comment
        lines directly above it (not a banner, nor above the imports)."""
        first = first_line(n) - 1
        while first - 1 >= self.floor and self.lines[first - 1].lstrip().startswith("#") \
                and not ES.BANNER.match(self.lines[first - 1].lstrip()):
            first -= 1
        return first, n.end_lineno - 1

    def comments(self, n: ast.stmt) -> List[str]:
        first, _last = self.span(n)
        return [ln.rstrip("\r\n") for ln in self.lines[first:first_line(n) - 1]]

    def all_names(self) -> Tuple[Optional[ast.stmt], List[str]]:
        """SOURCE's `__all__` statement and its names (None, [] without one)."""
        stmts = [n for n in self.body if isinstance(n, ast.Assign) and "__all__" in
                 ES.module_binds(n)]
        if not stmts:
            return None, []
        n = stmts[0]
        ok = (len(stmts) == 1 and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
              and isinstance(n.value, (ast.List, ast.Tuple))
              and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                      for e in n.value.elts) and self.bound_twice("__all__") is None)
        if not ok:
            raise Refused(f"{self.rel}:{n.lineno}: `__all__` is no single list of names")
        return n, [e.value for e in n.value.elts]


# ------------------------------------------------------------------ plan

def _wrap_all(names: List[str], nl: str) -> str:
    one = "__all__ = [" + ", ".join(f'"{n}"' for n in names) + "]"
    if len(one) <= ES.WIDTH:
        return one + nl
    out, line = ["__all__ = ["], ""
    for n in names:
        piece = f'"{n}",'
        if line and 4 + len(line) + 1 + len(piece) > ES.WIDTH:
            out.append("    " + line)
            line = ""
        line = f"{line} {piece}" if line else piece
    out += ["    " + line, "]"]
    return nl.join(out) + nl


def module_doc(about: str, source: str, nl: str) -> str:
    body = nl.join(textwrap.wrap(f"{about}.", width=76))
    return f'"""{body}{nl}{nl}{MARK.format(short(source))}{nl}"""{nl}'


def package_doc(about: str, nl: str) -> str:
    lines = textwrap.wrap(f"{about}.", width=72)
    if len(lines) == 1:
        return f'"""{lines[0]}"""{nl}'
    return f'"""{nl.join(lines)}{nl}"""{nl}'


class SourcePlan:
    """One SOURCE's part of the batch: what moves to each of its rows."""

    def __init__(self, repo: Path, src: Module, rows: List[Row], order: Dict[str, int]):
        self.repo, self.src, self.rows = repo, src, rows
        self.order = order
        self.moved: Dict[str, Row] = {n: r for r in rows for n in r.names}
        self.stmts: Dict[str, List[ast.stmt]] = {r.target: [] for r in rows}
        self.need: Dict[str, List[Tuple[ast.stmt, ast.alias]]] = {}
        self.engine: Dict[str, List[Tuple[Row, List[str]]]] = {}
        self._statements()
        for r in rows:
            self._reads(r)
        self._drops()
        self.orphans = sorted(set(self.orphans) | set(self._left_comments()))

    def _where(self, n: ast.AST) -> str:
        return f"{self.src.rel}:{n.lineno}"

    def _statements(self) -> None:
        s = self.src
        if s.star is not None:
            raise Refused(f"{s.rel}:{s.star}: a star import: what it binds cannot be read, nor "
                          f"so what the moved code needs from it (by hand)")
        if s.code:
            n = s.code[0]
            raise Refused(f"{self._where(n)} runs code at import ({type(n).__name__}): what "
                          f"stays and what moves would run it apart (by hand)")
        for name, row in self.moved.items():
            if name not in s.defs:
                raise Refused(f"{s.rel}: {name} is no top-level def, class or assignment of it "
                              f"(the table's line {row.line})")
            why = s.bound_twice(name)
            if why:
                raise Refused(f"{s.rel}: {name} is bound at module level {why}")
        seen: Set[int] = set()
        for name, row in sorted(self.moved.items(), key=lambda kv: s.defs[kv[0]].lineno):
            st = s.defs[name]
            if id(st) in seen:
                continue
            seen.add(id(st))
            binds = ES.module_binds(st)
            other = sorted(b for b in binds if self.moved.get(b) is not row)
            if other:
                raise Refused(f"{self._where(st)} binds {', '.join(other)} with {name}, which "
                              f"{row.target} takes: name them all in one row, or split it")
            self.stmts[row.target].append(st)
        for st, roots in s.stores:
            rows = {self.moved[r].target for r in roots if r in self.moved}
            if not rows:
                continue
            if len(rows) > 1 or roots - set(self.moved):
                raise Refused(f"{self._where(st)} stores into {', '.join(sorted(roots))}: "
                              f"one moves, and not all to one module (by hand)")
            self.stmts[rows.pop()].append(st)
        for t in self.stmts:
            self.stmts[t].sort(key=lambda x: x.lineno)
        self._lines()

    def _lines(self) -> None:
        s = self.src
        moving = {id(x) for xs in self.stmts.values() for x in xs}
        owner: Dict[int, int] = {}
        for n in s.body:
            for ln in range(first_line(n), n.end_lineno + 1):
                if ln in owner and (owner[ln] in moving or id(n) in moving):
                    raise Refused(f"{s.rel}:{ln} holds a statement that moves and another")
                owner[ln] = id(n)
        for xs in self.stmts.values():
            for x in xs:
                if x.lineno <= s.import_end:
                    raise Refused(f"{self._where(x)}: a statement that moves is among the imports")

    def _reads(self, row: Row) -> None:
        s, here = self.src, self.order[row.target]
        need: Set[str] = set()
        engine: Dict[str, Set[str]] = {}
        for st in self.stmts[row.target]:
            glob = sorted({g for x in ast.walk(st) if isinstance(x, ast.Global) for g in x.names})
            if glob:
                raise Refused(f"{self._where(st)} declares `global {', '.join(glob)}`: moved, it "
                              f"would bind {row.target}'s, not {s.rel}'s (by hand)")
            place = sorted(reads(st) & ES.PLACE) + ES._place_attrs(st) + \
                ES.own_namespace(st, s.module, ES.sys_names(s.imports)) + \
                ES.location_reads(st, s.imports) + sorted(
                    {x.id for x in ast.walk(st) if isinstance(x, ast.Name) and x.id in OWN_NAMES})
            if place:
                raise Refused(f"{self._where(st)} reads its own module or where it runs "
                              f"({', '.join(place)}): moved, it would read {row.target}'s")
            for r in sorted(reads(st)):
                other = self.moved.get(r)
                if other is row:
                    continue
                if other is not None:
                    if self.order[other.target] > here:
                        raise Refused(f"{self._where(st)}: {row.target} would import {r} from "
                                      f"{other.target}, which comes later in the table")
                    engine.setdefault(other.target, set()).add(r)
                elif r in s.defs:
                    raise Refused(f"{self._where(st)} reads {r}, which stays in {s.rel}: "
                                  f"{row.target} would import it back (a cycle)")
                elif r in s.other or r in s.declared:
                    raise Refused(f"{self._where(st)} reads {r}, which {s.rel} binds in a "
                                  f"compound statement or through `global`")
                elif r in s.imports:
                    self._one_import(st, r)
                    need.add(r)
        pairs = sorted((sa for r in need for sa in s.imports[r]),
                       key=lambda sa: (sa[0].lineno, sa[0].col_offset, sa[0].names.index(sa[1])))
        for st, _a in pairs:
            if isinstance(st, ast.ImportFrom) and st.level:
                raise Refused(f"{self._where(st)}: the moved code needs a relative import")
        self.need[row.target] = pairs
        self.engine[row.target] = [
            (o, sorted(names, key=lambda n: s.defs[n].lineno))
            for o in sorted((x for x in self.rows if x.target in engine),
                            key=lambda x: self.order[x.target])
            for names in [engine[o.target]]]
        mods = {x for st, a in pairs for x in (
            (st.module, f"{st.module}.{a.name}") if isinstance(st, ast.ImportFrom) else (a.name,))}
        mods |= {o.module for o, _n in self.engine[row.target]}
        for m in sorted(m for m in mods if m):
            why = layer_problem(row.module, m)
            if why:
                raise Refused(f"{row.target}: {why}")

    def _one_import(self, st: ast.stmt, name: str) -> None:
        """NAME, which ST reads, is bound once in SOURCE, by an import above ST."""
        s = self.src
        lines = [x.lineno for x, _a in s.imports[name]]
        if s.count.get(name, 0) > 1:
            raise Refused(f"{self._where(st)} reads {name}, which {s.rel} binds more than once "
                          f"(line {', '.join(map(str, lines))}): which one it reads is not the "
                          f"import's to say (by hand)")
        if lines[0] > first_line(st):
            raise Refused(f"{self._where(st)} reads {name}, which {s.rel} imports below it (line "
                          f"{lines[0]}): moved, it would import it first (by hand)")

    def _drops(self) -> None:
        s = self.src
        moving = {id(x) for xs in self.stmts.values() for x in xs}
        stay = [n for n in s.body if id(n) not in moving
                and not isinstance(n, (ast.Import, ast.ImportFrom))]
        left: Set[str] = set()
        for n in stay:
            left |= reads(n)
        self.all_stmt, old_all = s.all_names()
        left |= set(old_all)
        kept: Set[str] = set()
        for name, pairs in s.imports.items():
            for st, a in pairs:
                if is_reexport(a) or (isinstance(st, ast.ImportFrom) and st.module == "__future__"):
                    kept.add(name)
        needed = {_bound(a) for pairs in self.need.values() for _st, a in pairs}
        # An import marked `noqa: F401` or a bare `noqa` (a re-export), on any
        # of its lines, in any case, that only the moved code read: left, it is
        # an F401 hit check_architecture counts (it reads past noqa); dropped,
        # an importer of SOURCE may lose it.
        for name in sorted(needed - left - kept):
            for st, _a in s.imports[name]:
                if any(_F401.search(x) for x in s.lines[st.lineno - 1:st.end_lineno]):
                    raise Refused(f"{self._where(st)}: {name}, which only the moved code reads, is "
                                  f"marked `noqa: F401` (or a bare `noqa`): left, it is an F401 "
                                  f"hit check_architecture counts; dropped, an importer may lose "
                                  f"it (by hand)")
        self.dropped = sorted(needed - left - kept)
        # A comment directly above an import dropped whole stays: listed, by
        # the comment's first line (as _left_comments lists one).
        def top(i: int) -> int:
            while i > 0 and s.lines[i - 1].lstrip().startswith("#"):
                i -= 1
            return i + 1
        self.orphans = [top(n.lineno - 2) for n in s.body
                        if isinstance(n, (ast.Import, ast.ImportFrom))
                        and all(_bound(a) in self.dropped for a in n.names)
                        and n.lineno >= 2 and s.lines[n.lineno - 2].lstrip().startswith("#")]
        self.exports = [n for r in sorted(self.rows, key=lambda r: self.order[r.target])
                        for n in sorted(r.names, key=lambda n: s.defs[n].lineno)]
        self.all = old_all + [n for n in self.exports if n not in old_all]
        for name in self.dropped:
            for st, _a in s.imports[name]:
                lines = s.lines[st.lineno - 1:st.end_lineno]
                if any("#" in ln for ln in lines) and any(
                        _bound(a) not in self.dropped for a in st.names):
                    raise Refused(f"{self._where(st)}: the import loses {name} and carries a "
                                  f"comment, which the rewrite would drop (by hand)")

    def _left_comments(self) -> List[int]:
        """BASE's first line of each comment block at the top level that
        stays with no statement after it: every statement between it and
        the next block that stays moves (or is an import dropped whole)."""
        s = self.src
        gone = {id(x) for xs in self.stmts.values() for x in xs} | {
            id(n) for n in s.body if isinstance(n, (ast.Import, ast.ImportFrom))
            and all(_bound(a) in self.dropped and not is_reexport(a) for a in n.names)}
        cut = {i for xs in self.stmts.values() for x in xs
               for i in range(s.span(x)[0], s.span(x)[1] + 1)}
        starts = [(first_line(n) - 1, id(n) in gone) for n in s.body]

        def comment(i: int) -> bool:
            return i >= s.head and i not in cut and s.lines[i].startswith("#")
        blocks, i = [], 0
        while i < len(s.lines):
            if comment(i):
                j = i
                while j + 1 < len(s.lines) and comment(j + 1):
                    j += 1
                blocks.append((i, j))
                i = j
            i += 1
        out = []
        for k, (a, b) in enumerate(blocks):
            nxt = blocks[k + 1][0] if k + 1 < len(blocks) else len(s.lines)
            between = [g for st, g in starts if b < st < nxt]
            if between and all(between):
                out.append(a + 1)
        return out

    # The texts.
    def target_text(self, row: Row) -> str:
        s, nl = self.src, self.src.nl
        out = [module_doc(row.about, s.rel, nl)]
        out += [ES.from_line("", "__future__", n.names, nl) + nl for n in s.body
                if isinstance(n, ast.ImportFrom) and n.module == "__future__"]
        groups: List[Tuple[ast.stmt, List[ast.alias]]] = []
        for st, a in self.need[row.target]:
            if isinstance(st, ast.ImportFrom) and st.module == "__future__":
                continue
            if groups and groups[-1][0] is st:
                groups[-1][1].append(a)
            else:
                groups.append((st, [a]))
        prev = 0
        for st, names in groups:
            if prev and any(not ln.strip() for ln in s.lines[prev:st.lineno - 1]):
                out.append(nl)
            prev = st.end_lineno
            names = [_plain(a) for a in names]
            if isinstance(st, ast.Import):
                out += [f"import {ES._alias(a)}{nl}" for a in names]
            else:
                out.append(ES.from_line("", st.module or "", names, nl) + nl)
        engine = self.engine[row.target]
        if engine and len(out) > 1:
            out.append(nl)
        for o, names in engine:
            out.append(ES.from_line("", o.module, [ast.alias(name=n) for n in names], nl) + nl)
        for i, st in enumerate(self.stmts[row.target]):
            first, last = s.span(st)
            blanks = 0
            while blanks < first and not s.lines[first - 1 - blanks].strip():
                blanks += 1
            out.append(nl * (2 if i == 0 else min(2, blanks)))
            out.append("".join(s.lines[first:last + 1]).rstrip("\r\n") + nl)
        return "".join(out)

    def source_text(self) -> str:
        s, nl = self.src, self.src.nl
        cut = object()
        drop: Set[int] = set()
        for xs in self.stmts.values():
            for x in xs:
                first, last = s.span(x)
                drop.update(range(first, last + 1))
        marked: list = []
        for i, ln in enumerate(s.lines):
            if i in drop:
                if not marked or marked[-1] is not cut:
                    marked.append(cut)
            else:
                marked.append(ln)
        text = "".join(_keep_end(marked, cut, ES._close_cuts(marked, cut, nl)))
        tree = ast.parse(text)
        lines = MT.split_lines(text)
        edits: List[Tuple[int, int, List[str]]] = []
        for n in tree.body:
            if not isinstance(n, (ast.Import, ast.ImportFrom)):
                continue
            gone = [a for a in n.names if _bound(a) in self.dropped and not is_reexport(a)]
            if not gone:
                continue
            left = [a for a in n.names if a not in gone]
            new = [] if not left else [
                f"import {', '.join(ES._alias(a) for a in left)}" if isinstance(n, ast.Import)
                else ES.from_line("", "." * n.level + (n.module or ""), left, nl)]
            edits.append((n.lineno - 1, n.end_lineno - 1, [x + nl for x in new]))
        all_stmt = next((n for n in tree.body if isinstance(n, ast.Assign)
                         and "__all__" in ES.module_binds(n)), None)
        if all_stmt is not None:
            edits.append((all_stmt.lineno - 1, all_stmt.end_lineno - 1,
                          [_wrap_all(self.all, nl)]))
        for first, last, new in sorted(edits, reverse=True):
            lines[first:last + 1] = new
        text = "".join(lines)
        tree = ast.parse(text)
        lines = _tidy_head(MT.split_lines(text), ES._import_end(tree))
        insert = ES._import_end(ast.parse("".join(lines)))
        back = [ES.from_line("", r.module, [ast.alias(name=n) for n in names], nl) + nl
                for r in sorted(self.rows, key=lambda r: self.order[r.target])
                for names in [sorted(r.names, key=lambda n: s.defs[n].lineno)]]
        if all_stmt is None:
            back += [nl, ALL_NOTE + nl, _wrap_all(self.all, nl)]
        if insert < len(lines) and lines[insert].strip():
            back += [nl, nl]           # code right below: SOURCE had no import above it
        lines[insert:insert] = back
        return "".join(lines)


def _keep_end(marked: list, cut: object, out: List[str]) -> List[str]:
    """OUT (`_close_cuts` of MARKED) ending as the source does, its blank
    lines and a last line without a newline kept, when a line that stays
    follows the last cut; a cut at the end takes the blank lines along."""
    last = max((i for i, x in enumerate(marked) if x is cut or x.strip()), default=None)
    if last is None or marked[last] is cut:
        return out
    return out[:-1] + marked[last:]


def _tidy_head(lines: List[str], end: int) -> List[str]:
    """The lines above END (the imports) without a blank first line or two
    blank lines in a row, which dropping an import can leave."""
    head, rest = lines[:end], lines[end:]
    out: List[str] = []
    for ln in head:
        if not ln.strip() and (not out or not out[-1].strip()):
            continue
        out.append(ln)
    return out + rest


# --------------------------------------------------------------- the batch

class Plan:
    """What a table's batch does, worked out before anything is written."""

    def __init__(self, repo: Path, rows: List[Row]):
        self.repo, self.rows = repo, rows
        self.order = {r.target: i for i, r in enumerate(rows)}
        done = [(repo / r.target).exists() for r in rows]
        for i, d in enumerate(done):
            if d and not all(done[:i]):
                j = done.index(False)
                raise Refused(f"the table's line {rows[i].line} is done ({rows[i].target} "
                              f"exists) but line {rows[j].line}, above it, is not")
        self.done = [r for r, d in zip(rows, done) if d]
        self.batch = [r for r, d in zip(rows, done) if not d]
        if not self.batch:
            raise Refused("every row of the table is done: no batch")
        self._done_hold()
        self._packages()
        self.sources: Dict[str, SourcePlan] = {}
        for rel in dict.fromkeys(r.source for r in self.batch if not r.package):
            src = Module(repo / rel, rel)
            mine = [r for r in self.batch if r.source == rel and not r.package]
            self.sources[rel] = SourcePlan(repo, src, mine, self.order)
        self._importers()
        self.texts = self._texts()
        bad = cycle_problems(import_graph(self.repo, self.rows, self.texts), self.rows)
        if bad:
            raise Refused(bad[0])
        self.mutation = self._mutation()
        # The seams --check will fail: read after this move's own rewrites.
        written = {MUTATION: self.mutation[0]} if self.mutation and self.mutation[1] else {}
        _n, self.patches, self.left = seam_problems(repo, rows, self.texts, written)
        self.pack, self.hand = self._pack()

    def _done_hold(self) -> None:
        for r in self.done:
            if r.package:
                continue
            src = Module(self.repo / r.source, r.source)
            back = {a.name for n in src.body if isinstance(n, ast.ImportFrom)
                    and n.module == r.module and not n.level for a in n.names}
            bad = sorted(n for n in r.names if n in src.defs or n not in back)
            if bad:
                raise Refused(f"the table's line {r.line} is done ({r.target} exists), but "
                              f"{r.source} defines or does not import {', '.join(bad)}")

    def _packages(self) -> None:
        made = {str(Path(r.target).parent) for r in self.batch if r.package}
        for r in self.batch:
            if r.package and (self.repo / r.target).parent.exists():
                raise Refused(f"{Path(r.target).parent}/ exists: a package row makes a new folder")
            parts = Path(r.target).parent.parts
            for i in range(3, len(parts) + (0 if r.package else 1)):
                d = Path(*parts[:i])
                if not (self.repo / d / "__init__.py").is_file() and str(d) not in made:
                    raise Refused(f"{r.target}: {d}/ is no package, and no row makes it one "
                                  f"(`{r.source} {d}/__init__.py DESCRIPTION`)")
        new = [r.target for r in self.batch]
        if _git_top(self.repo):
            res = subprocess.run(["git", "check-ignore", "--stdin", "-z"], cwd=self.repo,
                                 capture_output=True, env=_genv(),
                                 input="".join(f"{x}\0" for x in new).encode())
            ignored = [x for x in res.stdout.decode().split("\0") if x]
            if res.returncode not in (0, 1):
                raise Refused(f"git check-ignore: {res.stderr.decode().strip()}")
            if ignored:
                raise Refused(f"{ignored[0]} is a path git ignores (.gitignore)")

    def _importers(self) -> None:
        """An import SOURCE drops that another module imports from it is
        refused (the seams through SOURCE are listed after the texts)."""
        plans = {sp.src.module: sp for sp in self.sources.values()}
        for p in _py_files(self.repo):
            rel = p.relative_to(self.repo).as_posix()
            if rel in TOOLS or rel in self.sources:
                continue
            try:
                tree = ast.parse(MT.read_exact(p))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for mod, sp in plans.items():
                for n in ast.walk(tree):
                    if isinstance(n, ast.ImportFrom) and n.module == mod and not n.level:
                        for a in n.names:
                            if a.name in sp.dropped:
                                raise Refused(f"{rel}:{n.lineno} imports {a.name} from {mod}, "
                                              f"which no longer reads it: it would be gone")

    def _texts(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        nl = "\n"
        for r in self.batch:
            if r.package:
                src = self.repo / r.source
                nl = ES._line_end(MT.read_exact(src)) if src.is_file() else "\n"
                out[r.target] = package_doc(r.about, nl)
        for rel, sp in self.sources.items():
            for r in sp.rows:
                out[r.target] = sp.target_text(r)
            out[rel] = sp.source_text()
        for rel, t in out.items():
            try:
                ast.parse(t)
            except SyntaxError as e:
                raise Refused(f"the new {rel} does not parse ({e.msg}, line {e.lineno}): a bug "
                              f"of the tool's") from e
        return out

    def placement(self) -> Dict[str, Dict[str, str]]:
        """SOURCE -> each moved name -> the TARGET that takes it."""
        return {rel: {n: r.target for n, r in sp.moved.items()}
                for rel, sp in self.sources.items()}

    def _mutation(self) -> Optional[Tuple[str, int]]:
        p = self.repo / MUTATION
        if not p.is_file():
            return None
        return retarget_mutation(MT.read_exact(p), self.placement())

    def _pack(self) -> Tuple[Dict[str, Tuple[str, int]], List[str]]:
        texts = dict(self.texts)

        def text_of(rel: str) -> Optional[str]:
            if rel not in texts:
                p = self.repo / rel
                texts[rel] = MT.read_exact(p) if p.is_file() else None
            return texts[rel]
        out: Dict[str, Tuple[str, int]] = {}
        hand: List[str] = []
        kept = {rel: set(sp.src.defs) - set(sp.moved) for rel, sp in self.sources.items()}
        for doc in pack_docs(self.repo):
            rel = doc.relative_to(self.repo).as_posix()
            text = MT.read_exact(doc)
            new, n, left = rewrite_pack(text, self.placement(), kept, text_of)
            hand += [f"{rel}:{x}" for x in left]
            if n:
                out[rel] = (new, n)
        return out, hand

    def summary(self) -> List[str]:
        out = []
        for rel, sp in self.sources.items():
            for r in sp.rows:
                stmts = sp.stmts[r.target]
                lines = self.texts[r.target].count("\n")
                imps = [ES._alias(_plain(a)) for _s, a in sp.need[r.target]]
                eng = [o.module for o, _n in sp.engine[r.target]]
                out.append(f"{rel} -> {r.target}: {len(r.names)} name(s) in {len(stmts)} "
                           f"statement(s), {lines} lines; imports {', '.join(imps) or 'none'}"
                           + (f"; from the batch: {', '.join(eng)}" if eng else ""))
            out.append(f"{rel}: imports back {len(sp.exports)} name(s) from {len(sp.rows)} "
                       f"module(s), `__all__` {len(sp.all)}; drops "
                       f"{', '.join(sp.dropped) or 'no import'}")
            if sp.orphans:
                out.append(f"{rel}: a comment stays with no statement after it (BASE's line(s) "
                           f"{', '.join(map(str, sp.orphans))}): settle each by hand, in a "
                           f"commit apart from the batch")
        for r in self.batch:
            if r.package:
                out.append(f"{r.target}: a new package")
        return out


def _genv() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**env, "GIT_NO_REPLACE_OBJECTS": "1"}


def _git_top(repo: Path) -> bool:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True,
                       text=True, env=_genv())
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == repo.resolve()


def _py_files(repo: Path) -> List[Path]:
    out = []
    for d in ("src", "tests", "scripts", "packages"):
        if (repo / d).is_dir():
            out += [p for p in sorted((repo / d).rglob("*.py")) if "__pycache__" not in p.parts]
    return out


# ------------------------------------------------------------------ seams

# The folders whose code reaches a SOURCE by name: a test patches through
# it, a script reads its file (the seams stage of --check).
SEAM_DIRS = ("tests", "scripts", "packages/*/tests")
# Whole-module reads of a SOURCE's text that pass, by repo path -> (the
# SOURCE module they read, exactly that many; an entry is removed at 0).
# Empty since B0b: tests/test_engine_run.py's eleven (audits/step10-design/
# design.md, section 6: the "11 getsource sites") read the engine's whole
# text through tests/_engine.py's `source()`. Every other reader fails, a
# file of the same name elsewhere too, and so does a count that is not an
# entry's, fewer reads too (when the text moves out of that module).
B0B_WHOLE_READS: Dict[str, Tuple[str, int]] = {}
# B0b's helper module (the design's section 2) -> its seams' count, exactly:
# it patches through, reads and searches the source and the engine together
# by design, and is the one file that may name SOURCE's path (the WIDENED
# rule, the planted paths and CUT_KEYS go through it). Any other count
# fails. B0b's review set it at 0: the helper reaches lib.core and each
# engine module through module objects (`modules()`: `mock.patch.object` on
# each, their `__file__` read from the objects), never by a dotted name, a
# path or a getsource the stage reads, and it names no engine name, so the
# stage reads no seam in it at any batch, B1's to B6's included (a variant
# with `inspect.getsource(core)`, `patch.object(core, ...)` and
# `core.__file__` reads 3). The proof that it reaches every engine module
# is tests/test_engine_seams.py (TJ-3944). An edit adding a form the stage
# reads fails the next batch's check: --check runs BASE's tool, so the
# count is raised in a commit before that batch.
B0B_HELPER: Tuple[str, int] = ("tests/_engine.py", 0)
_WHOLE = ("getsource", "getsourcelines", "getsourcefile", "getfile", "getabsfile", "findsource")
_WHERE = ("getsourcelines", "getsourcefile", "getfile", "getabsfile", "findsource")
_FILE_ATTRS = ("__file__", "__spec__", "__loader__", "__cached__", "__path__")
_LIVES = ("__module__", "__code__", "__globals__")
_NS_KEYED = ("pop", "setdefault", "__setitem__", "__delitem__")
_NS_SAFE = ("dict", "list", "sorted", "set", "len", "iter", "tuple", "frozenset", "repr", "str",
            "id", "type", "isinstance", "any", "all", "sum", "min", "max")
_JOINS = ("join", "joinpath", "Path", "PurePath", "PosixPath", "PurePosixPath")
_PATHS = ("Path", "PurePath", "PosixPath", "PurePosixPath")
# An import its `noqa` keeps where nothing reads it (a re-export): one
# naming F401, or a bare one (every code), in any case.
_F401 = re.compile(r"#\s*noqa\b(?!\s*:)|#\s*noqa\s*:[^#]*\bF401\b", re.IGNORECASE)
_REAL = ("realpath", "abspath", "normpath")
# The one-argument calls a folder's path passes through.
_UNWRAP = ("str", "fspath") + _REAL + _PATHS
# What lists a folder: Path's methods, os's and glob's functions.
_LISTS = ("glob", "rglob", "iterdir", "walk", "listdir", "scandir", "iglob")
# A field of a string (an f-string's, a `+` join's value, a `%` or
# `.format` template's), read as this name: a target naming it is computed.
FIELD = "__move_defs_field__"
_PERCENT = re.compile(r"%%|%(\([^)]*\))?[-#0 +]*(\*|\d+)?(\.(\*|\d+))?[a-zA-Z]")
_BRACES = re.compile(r"\{\{|\}\}|\{[^{}]*\}")
Seam = Tuple[int, str, str, str]     # (line, SOURCE's module, what, kind)


def _dotted(e: Optional[ast.AST]) -> Optional[str]:
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        d = _dotted(e.value)
        return f"{d}.{e.attr}" if d else None
    return None


def _const(e: Optional[ast.AST]) -> Optional[str]:
    return e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None


def _last(e: Optional[ast.AST]) -> str:
    return (_dotted(e) or "").rpartition(".")[2]


def _code_in(text: str) -> Optional[ast.AST]:
    """TEXT's syntax tree when it parses as Python, else None (a string's
    escapes warn nothing: it is read, not run)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


def _strings(tree: ast.AST, parents: Dict[int, ast.AST]):
    """Each string of TREE whole, as the code or the name it may hold, with
    its node: a constant, an f-string, a `+` join of them (a value that is
    no string counts as one), a `%` or `.format` template, each value they
    are filled with read as FIELD; a `+` join's strings each on their own
    too when it holds a FIELD (the code may be in one of them)."""
    def text(e: ast.AST) -> Optional[str]:
        if _const(e) is not None:
            return _const(e)
        if isinstance(e, ast.JoinedStr):
            return "".join(_const(v) if _const(v) is not None else FIELD for v in e.values)
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            a, b = text(e.left), text(e.right)
            if a is None and b is None:
                return None
            return (FIELD if a is None else a) + (FIELD if b is None else b)
        return None
    for n in ast.walk(tree):
        v = text(n)
        p = parents.get(id(n))
        if v is None or isinstance(p, ast.JoinedStr) or (
                isinstance(p, ast.BinOp) and isinstance(p.op, ast.Add) and text(p) is not None
                and FIELD not in text(p)):
            continue
        if isinstance(p, ast.BinOp) and isinstance(p.op, ast.Mod) and p.left is n:
            v = _PERCENT.sub(lambda m: "%" if m.group(0) == "%%" else FIELD, v)
        elif isinstance(p, ast.Attribute) and p.attr == "format" and \
                isinstance(parents.get(id(p)), ast.Call):
            v = _BRACES.sub(lambda m: m.group(0)[0] if m.group(0) in ("{{", "}}") else FIELD, v)
        yield n, v


def _docstrings(tree: ast.AST) -> Set[int]:
    return {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and _const(n.body[0].value) is not None}


def _names_module(text: str, mod: str) -> bool:
    """TEXT names MOD: its dotted name, its path, or `from PARENT import LAST`."""
    parent, _, last = mod.rpartition(".")
    return mod in text or mod.replace(".", "/") + ".py" in text or bool(re.search(
        rf"\bfrom\s+{re.escape(parent)}\s+import\b[^\n]*\b{re.escape(last)}\b", text))


def _named(mod: str, v: Optional[str], names, how: str) -> List[Tuple[str, str]]:
    """HOW ("stores into", "patches") MOD's name V: one MOD no longer
    defines, or one computed at run time (V None, or holding FIELD)."""
    if v is None or FIELD in v:
        return [(f"{how} {mod}.<computed>", "blind")]
    if v in names:
        return [(f"{how} {mod}.{v} ({names[v]} binds it)", "patch")]
    return []


def _pairs(t: ast.AST, v: ast.AST) -> List[Tuple[ast.AST, ast.AST]]:
    """Target T and value V, a tuple's or list's paired item by item."""
    if isinstance(t, (ast.Tuple, ast.List)) and isinstance(v, (ast.Tuple, ast.List)) and \
            len(t.elts) == len(v.elts) and not any(isinstance(x, ast.Starred) for x in t.elts):
        return [y for a, b in zip(t.elts, v.elts) for y in _pairs(a, b)]
    return [(t, v)]


def _bindings(n: ast.AST) -> List[Tuple[str, ast.AST]]:
    """(name, value) for each name N binds to a value: an assignment's
    (annotated too), a walrus's, a loop's target to each item of the
    display it walks, a default argument's."""
    if isinstance(n, ast.Assign):
        pairs = [x for t in n.targets for x in _pairs(t, n.value)]
    elif isinstance(n, (ast.AnnAssign, ast.NamedExpr)) and n.value is not None:
        pairs = [(n.target, n.value)]
    elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
        items = n.iter.elts if isinstance(n.iter, (ast.Tuple, ast.List, ast.Set)) else []
        pairs = [y for x in items for y in _pairs(n.target, x)]
    elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = n.args
        pos = a.posonlyargs + a.args
        pairs = list(zip(pos[len(pos) - len(a.defaults):], a.defaults)) + [
            (x, d) for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
    else:
        return []
    return [(t.id if isinstance(t, ast.Name) else t.arg, v) for t, v in pairs
            if isinstance(t, (ast.Name, ast.arg))]


_SEPS = ("os.sep", "sep", "os.path.sep")


def _path_parts(e: Optional[ast.AST]) -> Optional[List[ast.AST]]:
    """The parts a path E joins: `a / b`, os.path.join(a, b), a.joinpath(b), Path(a, b)."""
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Div):
        parts: List[ast.AST] = []
        while isinstance(e, ast.BinOp) and isinstance(e.op, ast.Div):
            parts.insert(0, e.right)
            e = e.left
        return [e] + parts
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr == "joinpath":
        return [e.func.value] + list(e.args)
    if isinstance(e, ast.Call) and _last(e.func) in _JOINS:
        return list(e.args)
    return None


def _added(e: ast.AST) -> List[ast.AST]:
    """The operands of a `+` chain, in order."""
    parts: List[ast.AST] = []
    while isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
        parts.insert(0, e.right)
        e = e.left
    return [e] + parts


def _descend(d: int, text: str, chain: List[str]) -> Tuple[int, List[str]]:
    """TEXT's leading components walked from D folders above the last of
    CHAIN (SOURCE's folder, `src/taxjson/lib`): `..` up, CHAIN's next name
    down; (the depth reached, the components left)."""
    comps = [c for c in text.split("/") if c not in ("", ".")]
    i = 0
    while i < len(comps):
        if comps[i] == "..":
            d += 1
        elif 0 < d <= len(chain) and comps[i] == chain[-d]:
            d -= 1
        else:
            break
        i += 1
    return d, comps[i:]


class Seams:
    """The seams through SOURCE modules in code: each patch or store
    through one of a name it no longer defines (or an import its new
    modules read: they read their own), and each reader of its file or
    text. MOVED: SOURCE's module -> {name: the module that binds it now};
    TAILS: SOURCE's path below src/<package>/ (`lib/core.py`)."""

    def __init__(self, moved: Dict[str, Dict[str, str]], tails: Dict[str, str]):
        self.moved, self.tails = moved, tails

    def scan(self, tree: ast.AST, at: Optional[int] = None, depth: int = 0) -> List[Seam]:
        """TREE's seams; the code in its strings too (a sitecustomize a
        test writes; dedented, its fields read as FIELD: see _strings),
        each at the line of its string. A script in a string (more than one
        line, no docstring) that names a SOURCE but does not parse is a
        seam too: what it does cannot be read, as a file that does not."""
        parents = {id(c): p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        strings = list(_strings(tree, parents))
        whole = {id(n): v for n, v in strings}
        out: List[Seam] = []
        for mod, names in self.moved.items():
            al = self._aliases(tree, mod)
            ns = {x.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                  and self._is_ns(n.value, mod, al, set()) for x in n.targets
                  if isinstance(x, ast.Name)}
            folders = self._folders(tree, mod, al)
            for n in ast.walk(tree):
                found = self._seams(n, mod, names, al, ns, folders, parents)
                if id(n) in whole:
                    found += self._target(whole[id(n)], mod, names)
                for what, kind in found:
                    out.append((at or getattr(n, "lineno", 0), mod, what, kind))
        lasts = {m.rpartition(".")[2] for m in self.moved}
        docs = _docstrings(tree)
        for n, v in strings if depth < 3 else ():
            if not any(x in v for x in lasts):
                continue
            sub = _code_in(textwrap.dedent(v))
            if sub is not None:
                out += self.scan(sub, at or n.lineno, depth + 1)
            elif "\n" in v.strip() and id(n) not in docs:
                out += [(at or n.lineno, mod, f"a script naming {mod} that does not parse: its "
                         f"seams cannot be read", "blind") for mod in self.moved
                        if _names_module(v.replace(FIELD, "\n"), mod)]
        return sorted(set(out))

    def _is_mod(self, e: Optional[ast.AST], mod: str, al: Set[str]) -> bool:
        if isinstance(e, ast.Name):
            return e.id in al
        if _dotted(e) == mod:
            return True
        if isinstance(e, ast.Call) and e.args and _const(e.args[0]) == mod:
            f = _dotted(e.func) or ""
            return f.rpartition(".")[2] == "import_module" or (f == "__import__" and (
                len(e.args) >= 4 or any(k.arg == "fromlist" for k in e.keywords)))
        return isinstance(e, ast.Subscript) and (_dotted(e.value) or "").endswith("modules") \
            and _const(e.slice) == mod

    def _aliases(self, tree: ast.AST, mod: str) -> Set[str]:
        """The names TREE binds to MOD: an import of it, or a name assigned
        it (`importlib.import_module(MOD)`, `sys.modules[MOD]`, an alias)."""
        parent, _, last = mod.rpartition(".")
        al: Set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                al |= {a.asname for a in n.names if a.name == mod and a.asname}
            elif isinstance(n, ast.ImportFrom) and not n.level and n.module == parent:
                al |= {a.asname or a.name for a in n.names if a.name == last}
        for _ in range(3):
            for n in ast.walk(tree):
                if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) \
                        and self._is_mod(n.value, mod, al):
                    targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                    al |= {x.id for x in targets if isinstance(x, ast.Name)}
        return al

    def _is_ns(self, e: Optional[ast.AST], mod: str, al: Set[str], ns: Set[str]) -> bool:
        """E is MOD's namespace: `vars(MOD)`, `MOD.__dict__`, a name for one."""
        if isinstance(e, ast.Name):
            return e.id in ns
        if isinstance(e, ast.Attribute) and e.attr == "__dict__":
            return self._is_mod(e.value, mod, al)
        if isinstance(e, ast.Call) and _dotted(e.func) in ("vars", "getattr") and e.args:
            return self._is_mod(e.args[0], mod, al) and (_dotted(e.func) == "vars" or (
                len(e.args) > 1 and _const(e.args[1]) == "__dict__"))
        return False

    def _seams(self, n, mod, names, al, ns, folders, parents) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        v = _const(n)
        if v is not None:
            out += self._constant(n, v, mod, names, parents)
        elif isinstance(n, ast.Attribute):
            if isinstance(n.ctx, (ast.Store, ast.Del)) and self._is_mod(n.value, mod, al) \
                    and (n.attr in names or FIELD in n.attr):
                out += _named(mod, n.attr, names, "stores into")
            elif n.attr in _FILE_ATTRS and self._is_mod(n.value, mod, al) and not (
                    n.attr == "__file__" and self._in_folder(n, mod, al, parents)):
                out.append((f"reads {mod}.{n.attr}", "reader"))
            elif n.attr in _LIVES and isinstance(n.value, ast.Attribute) \
                    and self._is_mod(n.value.value, mod, al) and n.value.attr in names:
                out.append((f"reads {mod}.{n.value.attr}.{n.attr} ({names[n.value.attr]} "
                            f"binds it)", "reader"))
        elif isinstance(n, ast.Call):
            out += self._call(n, mod, names, al, folders)
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            up = parents.get(id(n))
            if not (isinstance(up, ast.BinOp) and isinstance(up.op, ast.Div) and up.left is n):
                parts, x = [], n
                while isinstance(x, ast.BinOp) and isinstance(x.op, ast.Div):
                    parts.insert(0, x.right)
                    x = x.left
                out += self._path([x] + parts, mod, al, folders)
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            up = parents.get(id(n))
            if not (isinstance(up, ast.BinOp) and isinstance(up.op, ast.Add) and up.left is n):
                out += self._joined(n, mod, al, folders)
        elif isinstance(n, ast.JoinedStr):
            out += self._joined(n, mod, al, folders)
        if self._is_ns(n, mod, al, ns):
            out += self._namespace(n, mod, names, parents)
        return out

    @staticmethod
    def _target(v: str, mod: str, names) -> List[Tuple[str, str]]:
        """A string V, whole (see _strings), that names a name in MOD: one
        it no longer defines, or one computed at run time."""
        if not v.startswith(mod + "."):
            return []
        rest = v[len(mod) + 1:]
        head = rest.split(".")[0]
        shown = (v if rest else v + FIELD).replace(FIELD, "<computed>")
        if head in names:
            return [(f"names {shown} ({names[head]} binds {head})", "patch")]
        if FIELD in head or not rest:
            return [(f"names {shown}", "blind")]
        return []

    def _constant(self, n, v: str, mod: str, names, parents) -> List[Tuple[str, str]]:
        out = []
        how = self._bare(n, parents) if v == mod else None
        if how:
            out.append((f"names {mod} itself ({how})", "blind"))
        tail = self.tails[mod]
        if not any(c.isspace() for c in v) and (v == tail or v.endswith("/" + tail)):
            p = parents.get(id(n))
            planted = isinstance(p, ast.Dict) and any(
                k is n and _const(x) is not None for k, x in zip(p.keys, p.values))
            row = isinstance(p, ast.Tuple) and len(p.elts) >= 2 and p.elts[0] is n and \
                _const(p.elts[1]) is not None and _const(p.elts[1]).split(".")[0] not in names
            if not (planted or row):
                out.append((f"names its path {v!r}", "reader"))
        return out

    @staticmethod
    def _bare(n: ast.AST, parents) -> Optional[str]:
        """How a constant N, SOURCE's dotted name itself, reaches the module
        at run time, when it does: patched or imported by a name the stage
        cannot follow (`patch.multiple`'s keywords it reads), swapped or
        dropped in sys.modules (or a mapping that may be handed to it)."""
        p = parents.get(id(n))
        if isinstance(p, ast.keyword):
            p = parents.get(id(p))
        if isinstance(p, ast.Call) and p.func is not n:
            f = _dotted(p.func) or ""
            last = f.rpartition(".")[2]
            if last in ("import_module", "__import__", "reload"):
                return f"passed to {last}"
            if f.rpartition(".")[0].endswith("modules"):
                return f"passed to sys.modules.{last}"
            if last in ("patch", "object", "setattr", "delattr"):
                return "a patch target"
            if isinstance(p.func, ast.Attribute) and p.func.attr == "format":
                return "a target built at run time"
        if isinstance(p, ast.Subscript) and p.slice is n:
            if (_dotted(p.value) or "").endswith("modules"):
                return "a sys.modules key"
            if isinstance(p.ctx, (ast.Store, ast.Del)):
                return "a key stored into a mapping"
        if isinstance(p, ast.Dict) and any(k is n for k in p.keys):
            return "a dict key"
        up = parents.get(id(p))
        if isinstance(p, ast.Tuple) and isinstance(up, ast.BinOp) and isinstance(up.op, ast.Mod) \
                and up.right is p:
            p = up
        if isinstance(p, ast.BinOp) and (isinstance(p.op, ast.Add) or (
                isinstance(p.op, ast.Mod) and p.left is not n)):
            return "a target built at run time"
        if isinstance(p, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and p.value is n:
            return "bound to a name"
        return None

    def _file(self, e, mod: str, al: Set[str], path: bool = False) -> Optional[ast.AST]:
        """The `SOURCE.__file__` node E is, through realpath, abspath or
        normpath (PATH: E is `Path` of it, through `.resolve()` or
        `.absolute()`)."""
        if path:
            while isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and \
                    e.func.attr in ("resolve", "absolute") and not e.args:
                e = e.func.value
            if not (isinstance(e, ast.Call) and _last(e.func) in _PATHS and len(e.args) == 1):
                return None
            e = e.args[0]
        while isinstance(e, ast.Call) and _last(e.func) in _REAL and len(e.args) == 1:
            e = e.args[0]
        ok = isinstance(e, ast.Attribute) and e.attr == "__file__" and self._is_mod(e.value, mod, al)
        return e if ok else None

    def _folders(self, tree: ast.AST, mod: str, al: Set[str]) -> Dict[str, int]:
        """The names TREE binds to SOURCE's folder or a parent of it (see
        _bindings: an assignment, annotated too, a walrus, a loop over a
        display, a default argument, or another such name), each with how
        far above the folder it is (the nearest, bound more than once)."""
        out: Dict[str, int] = {}
        for _ in range(3):
            for n in ast.walk(tree):
                for name, v in _bindings(n):
                    f = self._folder(v, mod, al, out)
                    if f is not None and f[0] < out.get(name, f[0] + 1):
                        out[name] = f[0]
        return out

    def _folder(self, e, mod: str, al: Set[str],
                folders: Dict[str, int]) -> Optional[Tuple[int, Optional[ast.AST]]]:
        """(how far above SOURCE's folder E is, 0 for the folder itself; the
        `SOURCE.__file__` node it reads, None through a name) when E is
        SOURCE's folder or a parent, which a move keeps (the shim stays at
        SOURCE's path): `Path(S.__file__).parent` or `.parents[k]`,
        `os.path.dirname`, through `.resolve()`, `.absolute()`, `str()`,
        realpath and the like; a name FOLDERS binds to one; one joined to
        `..` or down toward SOURCE's folder."""
        while True:
            if isinstance(e, ast.NamedExpr):
                e = e.value
            elif isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and \
                    e.func.attr in ("resolve", "absolute") and not e.args:
                e = e.func.value
            elif isinstance(e, ast.Call) and len(e.args) == 1 and _last(e.func) in _UNWRAP:
                e = e.args[0]
            else:
                break
        if isinstance(e, ast.Name):
            return (folders[e.id], None) if e.id in folders else None
        up, path, k = None, True, 0
        if isinstance(e, ast.Subscript) and isinstance(e.value, ast.Attribute) and \
                e.value.attr == "parents":
            up = e.value.value
            s = e.slice
            k = s.value if isinstance(s, ast.Constant) and isinstance(s.value, int) else 0
        elif isinstance(e, ast.Attribute) and e.attr == "parent":
            up = e.value
        elif isinstance(e, ast.Call) and _last(e.func) == "dirname" and len(e.args) == 1:
            up, path = e.args[0], False
        if up is not None:
            f = self._file(up, mod, al, path=path)
            if f is not None:
                return k, f
            above = self._folder(up, mod, al, folders)
            return None if above is None else (above[0] + k + 1, above[1])
        if len(_path_parts(e) or ()) > 1:
            p = self._pattern(e, mod, al, folders)
            if p is None:
                return None
            d, rest = _descend(p[0], p[2], ["src"] + mod.split(".")[:-1])
            return None if rest else (d, p[1])
        return None

    def _pattern(self, e, mod: str, al: Set[str],
                 folders: Dict[str, int]) -> Optional[Tuple[int, Optional[ast.AST], str]]:
        """(how far above SOURCE's folder the path E starts, the
        `SOURCE.__file__` node it reads, the constant path joined below
        it) when E starts at SOURCE's folder or a parent: joined by `/`,
        os.path.join, joinpath or Path(), or by `+` or an f-string (`str()`
        of one too; `os.sep` read as `/`)."""
        while isinstance(e, ast.Call) and len(e.args) == 1 and _last(e.func) in ("str", "fspath"):
            e = e.args[0]
        parts, sep = _path_parts(e), "/"
        if parts is None or len(parts) < 2:
            sep = ""
            if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
                parts = _added(e)
            elif isinstance(e, ast.JoinedStr):
                parts = [v.value if isinstance(v, ast.FormattedValue) else v for v in e.values]
            else:
                f = self._folder(e, mod, al, folders)
                return None if f is None else (f[0], f[1], "")
        i = len(parts)
        while i > 0 and (_const(parts[i - 1]) is not None or _dotted(parts[i - 1]) in _SEPS):
            i -= 1
        f = self._folder(parts[i - 1], mod, al, folders) if i else None
        if f is None:
            return None
        text = sep.join(_const(x) if _const(x) is not None else "/" for x in parts[i:])
        return f[0], f[1], text.replace("\\", "/")

    def _in_folder(self, n: ast.AST, mod: str, al: Set[str], parents) -> bool:
        """N, a `SOURCE.__file__`, is read only for its folder or a parent."""
        a: Optional[ast.AST] = n
        for _ in range(8):
            a = parents.get(id(a))
            if a is None:
                return False
            f = self._folder(a, mod, al, {})
            if f is not None and f[1] is n:
                return True
        return False

    def _call(self, n: ast.Call, mod: str, names, al, folders) -> List[Tuple[str, str]]:
        f = _dotted(n.func) or ""
        last, a, out = f.rpartition(".")[2], n.args, []

        def named(x: Optional[ast.AST], how: str) -> None:
            out.extend(_named(mod, _const(x), names, how))
        if last in ("setattr", "delattr") and len(a) >= 2 and self._is_mod(a[0], mod, al):
            named(a[1], "stores into")
        elif last == "object" and self._is_mod(a[0] if a else next(
                (k.value for k in n.keywords if k.arg == "target"), None), mod, al):
            kw = next((k.value for k in n.keywords if k.arg == "attribute"), None)
            named(a[1] if len(a) > 1 else kw, "patches")
        elif last == "multiple" and a and (self._is_mod(a[0], mod, al) or _const(a[0]) == mod):
            for k in n.keywords:
                if k.arg is None or k.arg in names or FIELD in k.arg:
                    named(None if k.arg is None else ast.Constant(k.arg), "patches")
        elif last == "reload" and a and self._is_mod(a[0], mod, al):
            out.append((f"reloads {mod}: the shim runs again, not the modules its names "
                        f"moved to", "blind"))
        elif last in _WHOLE and a and self._is_mod(a[0], mod, al):
            out.append((f"{last}({mod})", "whole"))
        elif last in _WHERE and a and isinstance(a[0], ast.Attribute) \
                and self._is_mod(a[0].value, mod, al) and a[0].attr in names:
            out.append((f"{last}({mod}.{a[0].attr}) ({names[a[0].attr]} binds it)", "reader"))
        # A method's name, on any receiver (`Path(...).parent.glob`).
        meth = n.func.attr if isinstance(n.func, ast.Attribute) else last
        if last in _JOINS or meth == "joinpath":
            out += self._path(_path_parts(n) or [], mod, al, folders)
        if meth in _LISTS:
            out += self._listing(n, meth, mod, al, folders)
        return out

    def _listing(self, n: ast.Call, last: str, mod: str, al, folders) -> List[Tuple[str, str]]:
        """A listing of SOURCE's folder: Path's glob, rglob, iterdir and
        walk on it, os.listdir, os.scandir and os.walk of it, glob.glob and
        glob.iglob of a pattern below it; a parent's too, but a glob of
        another folder below it, or a listing of every module below it
        (walk; rglob or a `**` pattern with `**` alone before its last
        part, `*` or `*.py`): the modules a move writes are there too."""
        a = n.args
        on = n.func.value if isinstance(n.func, ast.Attribute) else None
        f = self._folder(on, mod, al, folders) if on is not None and \
            last in ("glob", "rglob", "iterdir", "walk") else None
        if f is not None:
            p: Optional[Tuple[int, Optional[str]]] = (
                f[0], (_const(a[0]) if a else None) if last in ("glob", "rglob") else "")
        elif a and last in ("listdir", "scandir", "walk"):
            f = self._folder(a[0], mod, al, folders)
            p = None if f is None else (f[0], "")
        elif a and last in ("glob", "iglob"):
            q = self._pattern(a[0], mod, al, folders)
            p = None if q is None else (q[0], q[2])
        else:
            return []
        if p is None:
            return []
        if p[1] is None:
            return [(f"lists its folder or a parent ({last}, by a pattern it cannot read)",
                     "reader")]
        d, rest = _descend(p[0], p[1], ["src"] + mod.split(".")[:-1])
        deep = last in ("rglob", "walk") or "**" in rest
        other = not deep and bool(rest) and not any(c in rest[0] for c in "*?[")
        every = deep and (not rest or (all(c == "**" for c in rest[:-1])
                                       and rest[-1] in ("*", "*.py")))
        if d > 0 and (other or every):
            return []
        return [(f"lists its folder{'' if d == 0 else ' or a parent'} ({last}): the moved "
                 f"code is not there", "reader")]

    def _path(self, parts: List[ast.AST], mod: str, al, folders) -> List[Tuple[str, str]]:
        """A path PARTS join (`/`, os.path.join, Path) that ends in SOURCE's,
        or SOURCE's folder joined back to its file's name."""
        consts: List[str] = []
        for x in reversed(parts):
            v = _const(x)
            if v is None:
                break
            consts.insert(0, v)
        joined = "/".join(c.strip("/") for c in consts)
        tail = self.tails[mod]
        if len(consts) >= 2 and (joined == tail or joined.endswith("/" + tail)):
            return [(f"names its path {joined!r}", "reader")]
        lead = parts[len(parts) - len(consts) - 1] if consts and len(consts) < len(parts) else None
        base = tail.rpartition("/")[2]
        if lead is not None and (joined == base or joined.endswith("/" + base)) and \
                self._folder(lead, mod, al, folders) is not None:
            return [(f"reads its file through its folder ({joined!r})", "reader")]
        return []

    def _joined(self, n: ast.AST, mod: str, al, folders) -> List[Tuple[str, str]]:
        """SOURCE's folder joined back to its file's name by `+` or an f-string."""
        p = self._pattern(n, mod, al, folders)
        text = p[2].lstrip("/") if p is not None else ""
        base = self.tails[mod].rpartition("/")[2]
        if p is not None and (text == base or text.endswith("/" + base)):
            return [(f"reads its file through its folder ({text!r})", "reader")]
        return []

    def _namespace(self, n, mod: str, names, parents) -> List[Tuple[str, str]]:
        """A store into MOD's namespace N, or N handed to a call."""
        p, what = parents.get(id(n)), f"{mod}'s namespace"
        keys: Optional[Set[str]] = set()
        if isinstance(p, ast.Subscript) and p.value is n:
            if not isinstance(p.ctx, (ast.Store, ast.Del)):
                return []
            keys = {_const(p.slice)} if _const(p.slice) is not None else None
        elif isinstance(p, ast.Attribute) and p.value is n:
            call = parents.get(id(p))
            if p.attr in _NS_KEYED and isinstance(call, ast.Call) and call.args:
                keys = {_const(call.args[0])} if _const(call.args[0]) is not None else None
            elif p.attr in ("update", "clear", "popitem"):
                keys = _store_keys(call, n) if p.attr == "update" else None
            else:
                return []
        elif isinstance(p, ast.Call) and any(x is n for x in p.args):
            f = _dotted(p.func) or ""
            if f in _NS_SAFE:
                return []
            keys = _store_keys(p, n) if f.endswith(".dict") else None
        elif isinstance(p, ast.keyword):
            keys = None
        else:
            return []
        if keys and any(FIELD in k for k in keys):
            keys = None
        if keys is None or keys & set(names):
            shown = ", ".join(sorted(keys & set(names))) if keys else "names it cannot read"
            return [(f"stores into {what} ({shown})", "patch" if keys else "blind")]
        return []


def _store_keys(call: Optional[ast.AST], skip: ast.AST) -> Optional[Set[str]]:
    """The names a dict store CALL writes (`ns.update({...})`, `patch.dict(ns,
    {...})`), when its dict displays and keywords say; None when not."""
    if not isinstance(call, ast.Call):
        return None
    keys: Set[str] = set()
    for x in call.args:
        if x is skip:
            continue
        if not isinstance(x, ast.Dict) or any(_const(k) is None for k in x.keys):
            return None
        keys |= {_const(k) for k in x.keys}
    for k in call.keywords:
        if k.arg is None:
            return None
        if k.arg != "clear":
            keys.add(k.arg)
    return keys


def seam_files(root: Path) -> List[Path]:
    out: List[Path] = []
    for g in SEAM_DIRS:
        for d in sorted(root.glob(g)):
            out += [p for p in sorted(d.rglob("*.py")) if d.is_dir()
                    and "__pycache__" not in p.parts and not p.is_symlink()]
    return out


def _import_names(text: str) -> Set[str]:
    return {_bound(a) for n in ast.parse(text).body if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names}


def seams_of(root: Path, rows: List[Row], texts: Dict[str, str]) -> Seams:
    """Every row's SOURCE: the names it no longer defines (every row of the
    table's, earlier batches' too) and the imports their modules read
    (from TEXTS, the planned ones, or the file), each with its module."""
    moved: Dict[str, Dict[str, str]] = {}
    tails: Dict[str, str] = {}
    for r in rows:
        if r.package:
            continue
        mod = module_name(r.source)
        tails[mod] = short(r.source)
        m = moved.setdefault(mod, {})
        m.update({n: r.target for n in r.names})
        p = root / r.target
        text = texts.get(r.target) or (MT.read_exact(p) if p.is_file() else None)
        for name in sorted(_import_names(text) if text else ()):
            m.setdefault(name, r.target)
    return Seams(moved, tails)


def scan_seams(root: Path, seams: Seams,
               written: Optional[Dict[str, str]] = None) -> Tuple[int, List[Tuple[str, Seam]]]:
    """(the files read, each seam in them with its file); WRITTEN: a file's
    text as a move will write it (its TARGET_FUNCS retargeted)."""
    files, out = 0, []
    for p in seam_files(root):
        rel = p.relative_to(root).as_posix()
        if rel in TOOLS:
            continue
        files += 1
        text = (written or {}).get(rel)
        if text is None and p.stat().st_size < 4_000_000:
            text = MT.read_exact(p)
        tree = _code_in(text) if text is not None else None
        if tree is None:
            out.append((rel, (0, "", "does not parse: its seams cannot be read", "blind")))
            continue
        out += [(rel, s) for s in seams.scan(tree)]
    return files, out


def seam_problems(head: Path, rows: List[Row], texts: Optional[Dict[str, str]] = None,
                  written: Optional[Dict[str, str]] = None) -> Tuple[int, List[str], List[str]]:
    """The seams stage: (the files read, the problems, what is left to B0b,
    by file). TEXTS: the new modules as planned; WRITTEN: see scan_seams."""
    seams = seams_of(head, rows, texts or {})
    files, found = scan_seams(head, seams, written)
    problems, notes = seam_verdicts(found, set(seams.moved))
    return files, problems, notes


def seam_verdicts(found: List[Tuple[str, Seam]],
                  sources: Set[str]) -> Tuple[List[str], List[str]]:
    """(the problems, what is left to B0b): B0b's helper passes by its path
    at B0B_HELPER's count, B0B_WHOLE_READS' files their whole-module reads
    of a module of SOURCES at the entry's count; any other count fails."""
    problems: List[str] = []
    left: Dict[Tuple[str, str], int] = {}
    helper = 0
    for rel, (line, mod, what, kind) in found:
        if rel == B0B_HELPER[0] and mod:
            helper += 1
            continue
        b0b = B0B_WHOLE_READS.get(rel)
        if kind == "whole" and b0b and b0b[0] == mod:
            n = left.get((rel, mod), 0) + 1
            if n <= b0b[1]:
                left[rel, mod] = n
                continue
        why = {"patch": f"{mod} no longer defines it: the code there reads its own binding",
               "reader": f"a reader of {mod}'s file or text, which a move changes",
               "whole": "a whole-module read B0b's design does not account for",
               "blind": "the stage cannot read what it reaches, a moved name among them"}[kind]
        problems.append(f"seams: {rel}:{line}: {what}: {why}" if mod else f"seams: {rel}: {what}")
    for rel, (mod, want) in sorted(B0B_WHOLE_READS.items()):
        got = left.get((rel, mod), 0)
        if mod in sources and got < want:
            left.pop((rel, mod), None)
            problems.append(f"seams: {rel}: {got} whole-module read(s) of {mod}, "
                            f"B0B_WHOLE_READS records {want}: lower the entry to {got} (at 0, "
                            f"remove it)")
    if helper != B0B_HELPER[1]:
        problems.append(f"seams: {B0B_HELPER[0]}: {helper} seam(s), B0B_HELPER records "
                        f"{B0B_HELPER[1]}: B0b's helper passes at its reviewed count only (set "
                        f"it with the review of its seams)")
    notes = [f"{rel}: {n} whole-module read(s) of {mod}" for (rel, mod), n in sorted(left.items())]
    notes += [f"{B0B_HELPER[0]}: {helper} seam(s), B0b's helper"] if helper and \
        helper == B0B_HELPER[1] else []
    return problems, notes


# ------------------------------------------------------------ the rewrites

def target_rows(tree: ast.AST) -> List[Tuple[ast.Constant, str]]:
    """mutation_audit's TARGET_FUNCS rows, read from its text (never run):
    each row's path constant and its `Name.attr`, in source order."""
    out = []
    for n in getattr(tree, "body", []):
        if not (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TARGET_FUNCS"
                                                  for t in n.targets)):
            continue
        for e in ast.walk(n.value):
            if isinstance(e, ast.Tuple) and len(e.elts) >= 2 and all(
                    isinstance(x, ast.Constant) and isinstance(x.value, str) for x in e.elts[:2]):
                out.append((e.elts[0], e.elts[1].value))
    return sorted(out, key=lambda x: (x[0].lineno, x[0].col_offset))


def retarget_mutation(text: str, placement: Dict[str, Dict[str, str]]) -> Tuple[str, int]:
    """mutation_audit's TARGET_FUNCS with each `(SOURCE, "Name.attr")` row
    of a moved Name naming its TARGET: (the text, the rows changed)."""
    lines = MT.split_lines(text)
    edits = []
    for path, name in target_rows(ast.parse(text)):
        target = placement.get(path.value, {}).get(name.split(".")[0])
        if target and path.lineno == path.end_lineno:
            edits.append((path.lineno - 1, path.col_offset, path.end_col_offset,
                          path.value, target))
    for ln, c0, c1, old, new in sorted(edits, reverse=True):
        raw = lines[ln].encode("utf-8")
        seg = raw[c0:c1].decode("utf-8").replace(old, new, 1)
        lines[ln] = raw[:c0].decode("utf-8") + seg + raw[c1:].decode("utf-8")
    return "".join(lines), len(edits)


def pack_docs(repo: Path) -> List[Path]:
    out: List[Path] = []
    for g in PACK:
        out += sorted(p for p in repo.glob(g) if p.is_file())
    return out


# The knowledge pack's reference forms (tests/test_knowledge_pack.py).
_REF = re.compile(r"`([^`\s]+)`(\s+(?:—|--|-)\s+)((?:`[^`]+`(?:,\s*|\s+and\s+)?)+)")
_ITEM = re.compile(r"`([^`]+)`((?:,\s*|\s+and\s+)?)")
_BARE = re.compile(r"`([^`\s]+)`\s+`([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SPAN = re.compile(r"`([^`]+)`|[()]")
_PATHISH = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*(/|\.[A-Za-z0-9]+)$")
_REPO_DIRS = ("src/", "tests/", "scripts/", "docs/", "packages/", "examples/", ".github/")
_ROOT_FILES = re.compile(r"^([A-Z][A-Z_]*\.md|install\.sh|setup\.sh|run_tests\.sh|"
                         r"pyproject\.toml|channels\.json)$")


def _is_repo_path(tok: str) -> bool:
    path = tok.partition(":")[0]
    return bool(_PATHISH.match(path)) and not path.startswith("/") and (
        path.startswith(_REPO_DIRS) or bool(_ROOT_FILES.match(path)))


def has_symbol(text: str, sym: str) -> bool:
    """SYM in TEXT as the pack test finds it: each dotted part a whole word."""
    sym = sym.strip()
    sym = sym[:-2] if sym.endswith("()") else sym
    if _IDENT.match(sym):
        return all(re.search(r"(?<![A-Za-z0-9_])" + re.escape(p) + r"(?![A-Za-z0-9_])", text)
                   for p in sym.split("."))
    return sym in text


def _trailing(line: str, pos: int) -> List[str]:
    """The identifiers a reference ending at POS goes on to name, as the
    pack test reads them (its _span_idents)."""
    out, depth, skip = [], 0, None
    for e in _SPAN.finditer(line, pos):
        tok = e.group(1)
        if tok is None:
            depth += 1 if e.group(0) == "(" else -1
            if depth < 0:
                break
            if skip is not None and depth < skip:
                skip = None
            continue
        if _is_repo_path(tok):
            if depth == 0:
                break
            skip = depth if skip is None else min(skip, depth)
        elif skip is None and _IDENT.match(tok) and "." not in tok:
            out.append(tok)
    return out


def rewrite_pack(text: str, placement: Dict[str, Dict[str, str]], kept: Dict[str, Set[str]],
                 text_of: Callable[[str], Optional[str]]) -> Tuple[str, int, List[str]]:
    """TEXT's references to a SOURCE pointed at where their names went:
    (the text, the references changed, `LINE: reference` for each one it
    could not place, left as it was)."""
    lines = MT.split_lines(text)
    changed, hand = 0, []
    for i, line in enumerate(lines):
        new, n, bad = _rewrite_line(line, placement, kept, text_of)
        lines[i] = new
        changed += n
        hand += [f"{i + 1}: {b}" for b in bad]
    return "".join(lines), changed, hand


def _place(sym: str, source: str, placement: Dict[str, Dict[str, str]],
           kept: Dict[str, Set[str]], prev: Optional[str],
           text_of: Callable[[str], Optional[str]]) -> Optional[str]:
    """Where the reference's SYM goes: a moved name's TARGET, a kept name's
    SOURCE, a method or fragment where the name before it went (PREV), or
    SOURCE when it is still there; None when it cannot be placed."""
    s = sym.strip()
    s = s[:-2] if s.endswith("()") else s
    head = s.split(".")[0] if _IDENT.match(s) else None
    if head is not None and head in placement[source]:
        return placement[source][head]
    if head is not None and head in kept[source]:
        return source
    if prev is not None:
        return prev
    return source if has_symbol(text_of(source) or "", sym) else None


def _rewrite_line(line: str, placement, kept, text_of) -> Tuple[str, int, List[str]]:
    edits: List[Tuple[int, int, str]] = []
    bad: List[str] = []
    seen: Set[int] = set()
    for m in _REF.finditer(line):
        path, _, sym0 = m.group(1).partition(":")
        seen.add(m.start(1))
        if path not in placement:
            continue
        items = [(x.group(1), x.group(2)) for x in _ITEM.finditer(m.group(3))]
        syms = ([sym0] if sym0 else []) + [s for s, _sep in items]
        places: List[Optional[str]] = []
        for s in syms:
            places.append(_place(s, path, placement, kept, places[-1] if places else None,
                                 text_of))
        tails = _trailing(line, m.end())
        tail = [_place(t, path, placement, kept, places[-1], text_of) for t in tails]
        if all(p == path for p in places + tail):
            continue
        ok = all(p is not None and has_symbol(text_of(p) or "", s)
                 for p, s in zip(places + tail, syms + tails)) \
            and all(t == places[-1] for t in tail) and (not sym0 or len(set(places)) == 1)
        if not ok:
            bad.append(m.group(0))
            continue
        if sym0:
            edits.append((m.start(1), m.end(1), f"{places[0]}:{sym0}"))
            continue
        runs: List[Tuple[str, List[Tuple[str, str]]]] = []
        for p, it in zip(places, items):
            if runs and runs[-1][0] == p:
                runs[-1][1].append(it)
            else:
                runs.append((p, [it]))
        dash = m.group(2)
        parts = []
        for p, its in runs:
            body = "".join(f"`{s}`{sep}" for s, sep in its[:-1]) + f"`{its[-1][0]}`"
            parts.append(f"`{p}`{dash}{body}")
        end = m.end(3)
        trail = items[-1][1]          # a separator the regex took after the last item
        edits.append((m.start(0), end - len(trail), "; ".join(parts)))
    for m in _BARE.finditer(line):
        if m.start(1) in seen or m.group(1) not in placement:
            continue
        seen.add(m.start(1))
        p = _place(m.group(2), m.group(1), placement, kept, None, text_of)
        if p is None:
            bad.append(m.group(0))
        elif p != m.group(1):
            edits.append((m.start(1), m.end(1), p))
    for m in re.finditer(r"`([^`\s:]+):([A-Za-z_][A-Za-z0-9_.]*)`", line):
        if m.start(1) in seen or m.group(1) not in placement:
            continue
        p = _place(m.group(2), m.group(1), placement, kept, None, text_of)
        if p is None:
            bad.append(m.group(0))
        elif p != m.group(1):
            edits.append((m.start(1), m.end(1), p))
    for s, e, new in sorted(edits, reverse=True):
        line = line[:s] + new + line[e:]
    return line, len(edits), bad


# ------------------------------------------------------------------ the run

def _arch(repo: Path, args: List[str]) -> Tuple[int, str]:
    """The repository's check_architecture, in a child with a HOME of its own."""
    with tempfile.TemporaryDirectory(prefix="move-defs-home-") as home:
        r = subprocess.run([sys.executable, "-B", str(repo / ARCH), *args], cwd=repo,
                           env=ES._child_env(repo, Path(home)), capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def move(repo: Path, table: Path, dry_run: bool = False) -> int:
    plan = Plan(repo, read_table(table.read_text(encoding="utf-8")))
    for line in plan.summary():
        print(f"move_defs: {line}")
    if dry_run:
        _report(plan)
        return 0
    undo: List[Callable[[], None]] = []

    def write(rel: str, text: str) -> None:
        p = repo / rel
        made = []
        d = p.parent
        while not d.exists():
            made.append(d)
            d = d.parent
        for x in reversed(made):
            x.mkdir()
            undo.append(x.rmdir)
        before = MT.read_exact(p) if p.exists() else None
        MT.write_exact(p, text)
        undo.append(lambda: p.unlink() if before is None else MT.write_exact(p, before))
    try:
        for rel, text in plan.texts.items():
            write(rel, text)
        if plan.mutation and plan.mutation[1]:
            write(MUTATION, plan.mutation[0])
        for rel, (text, _n) in plan.pack.items():
            write(rel, text)
        base = repo / BASELINE
        if (repo / ARCH).is_file():
            if base.is_file():
                write(base.relative_to(repo).as_posix(), MT.read_exact(base))
            rc, out = _arch(repo, [ACCEPT])
            if rc:
                raise Refused(f"check_architecture {ACCEPT} fails after the move:\n{out[-1500:]}")
    except BaseException as e:
        for step in reversed(undo):
            try:
                step()
            except OSError as e2:
                print(f"move_defs: undoing: {e2}", file=sys.stderr)
        raise Refused(f"{e}; every change undone") from e
    _report(plan)
    how = "recorded" if (repo / ARCH).is_file() else "not recorded (no check_architecture)"
    print(f"move_defs: the architecture baseline {how}")
    return 0


def _report(plan: Plan) -> None:
    if plan.mutation is not None:
        print(f"move_defs: {MUTATION}: {plan.mutation[1]} TARGET_FUNCS row(s) retargeted")
    n = sum(c for _t, c in plan.pack.values())
    print(f"move_defs: the knowledge pack: {n} reference(s) rewritten in "
          f"{len(plan.pack)} file(s); {len(plan.hand)} to edit by hand"
          + (":" if plan.hand else ""))
    for x in plan.hand:
        print(f"  {x[:200]}")
    if plan.patches:
        print(f"move_defs: {len(plan.patches)} seam(s) through a source, which --check's seams "
              f"stage will fail:")
        for x in plan.patches:
            print(f"  {x[len('seams: '):][:200]}")
    if plan.left:
        print(f"move_defs: left to B0b: {'; '.join(plan.left)}")


# ----------------------------------------------------------------- --check

def _vt():
    return _load("verify_test_moves")


def _moved_stmts(src: Module, rows: List[Row]) -> Dict[str, List[ast.stmt]]:
    """BASE's statements each row takes: those binding its names, and the
    stores into them, in SOURCE's order (read anew, not by the planner)."""
    out: Dict[str, List[ast.stmt]] = {}
    for r in rows:
        names = set(r.names)
        out[r.target] = [n for n in src.body if not isinstance(n, (ast.Import, ast.ImportFrom))
                         and (ES.module_binds(n) & names or (store_roots(n) or set()) & names)]
    return out


def _dump(n: ast.AST) -> str:
    return ast.dump(n, include_attributes=False)


def ast_problems(base: Path, head: Path, batch: List[Row]) -> List[str]:
    """Check 3: the moved statements and their comments are BASE's; a new
    module holds its docstring, imports and those; SOURCE keeps the rest."""
    out: List[str] = []
    dump = _dump
    for r in batch:
        if r.package:
            body = Module(head / r.target, r.target).body
            if len(body) != 1 or not isinstance(body[0], ast.Expr):
                out.append(f"AST: {r.target} holds more than a docstring")
    for rel in dict.fromkeys(r.source for r in batch if not r.package):
        rows = [r for r in batch if r.source == rel and not r.package]
        b, h = Module(base / rel, rel), Module(head / rel, rel)
        want = _moved_stmts(b, rows)
        for r in rows:
            t = Module(head / r.target, r.target)
            body = t.body
            if not body or not (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)):
                out.append(f"AST: {r.target} has no docstring")
                continue
            i = 1
            while i < len(body) and isinstance(body[i], (ast.Import, ast.ImportFrom)):
                i += 1
            got = body[i:]
            if [dump(x) for x in got] != [dump(x) for x in want[r.target]]:
                out.append(f"AST: {r.target}'s statements are not BASE's {rel} ones it takes "
                           f"({len(got)} against {len(want[r.target])}, or one differs)")
                continue
            for x, y in zip(want[r.target], got):
                if b.comments(x) != t.comments(y):
                    out.append(f"AST: {r.target}:{y.lineno}: the comment lines above it are not "
                               f"BASE's ({rel}:{x.lineno})")
        moving = {id(x) for xs in want.values() for x in xs}
        rest_b = [dump(n) for n in b.body if id(n) not in moving
                  and not isinstance(n, (ast.Import, ast.ImportFrom))
                  and "__all__" not in ES.module_binds(n)]
        rest_h = [dump(n) for n in h.body if not isinstance(n, (ast.Import, ast.ImportFrom))
                  and "__all__" not in ES.module_binds(n)]
        if rest_b != rest_h:
            out.append(f"AST: {rel} keeps other statements than BASE's that stay")
    return out


Origin = Tuple[str, ...]


def origins(tree: ast.Module, module: str) -> Dict[str, List[Origin]]:
    """Each name a module binds at its top level -> where from: ('def',
    MODULE, name), ('from', M, name) or ('module', M)."""
    out: Dict[str, List[Origin]] = {}
    for n in tree.body:
        if isinstance(n, ast.Import):
            for a in n.names:
                o = ("module", a.name) if a.asname else ("module", a.name.split(".")[0])
                out.setdefault(_bound(a), []).append(o)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                out.setdefault(a.asname or a.name, []).append(
                    ("from", "." * n.level + (n.module or ""), a.name))
        elif store_roots(n) is None:
            for b in ES.module_binds(n):
                out.setdefault(b, []).append(("def", module, b))
    return out


def global_reads(m: Module, stmts: List[ast.stmt]) -> Dict[int, Set[str]]:
    """The globals each statement reads: its functions' and classes' by
    symtable, at module level and in string annotations by the AST."""
    top = symtable.symtable(m.text, m.rel, "exec")
    spans = [(first_line(s), s.end_lineno, s) for s in stmts]
    out = {id(s): ES.import_time_reads(s) | annotation_names(s) for s in stmts}

    def globs(t) -> Set[str]:
        names = {s.get_name() for s in t.get_symbols() if s.is_global()}
        for c in t.get_children():
            names |= globs(c)
        return names
    for child in top.get_children():
        for first, last, s in spans:
            if first <= child.get_lineno() <= last:
                out[id(s)] |= globs(child)
    return out


def binding_problems(base: Path, head: Path, batch: List[Row]) -> List[str]:
    """Check 4: every global a moved statement, or one SOURCE keeps, reads
    is bound to BASE's origin, or to the module the table moves it to."""
    out: List[str] = []
    for rel in dict.fromkeys(r.source for r in batch if not r.package):
        rows = [r for r in batch if r.source == rel and not r.package]
        b, h = Module(base / rel, rel), Module(head / rel, rel)
        ob = origins(b.tree, b.module)
        to = {n: r for r in rows for n in r.names}

        def expect(name: str, where: Row | None) -> List[Origin]:
            o = ob.get(name, [])
            if o == [("def", b.module, name)] and name in to:
                r = to[name]
                return [("def", r.module, name)] if r is where else [("from", r.module, name)]
            return o
        mods = [(h, None)] + [(Module(head / r.target, r.target), r) for r in rows]
        for m, row in mods:
            oh = origins(m.tree, m.module)
            stmts = [n for n in m.body if not isinstance(n, (ast.Import, ast.ImportFrom))
                     and not (isinstance(n, ast.Expr) and n is m.body[0])
                     and "__all__" not in ES.module_binds(n)]
            got_reads = global_reads(m, stmts)
            for s in stmts:
                for name in sorted(got_reads[id(s)]):
                    want, got = expect(name, row), oh.get(name, [])
                    if want != got:
                        out.append(f"bindings: {m.rel}:{s.lineno} reads {name}, bound to "
                                   f"{got or 'nothing'}; in BASE {want or 'nothing'}")
    return out


# What importing SOURCE leaves, in a fresh interpreter of one tree: each
# name's shape and whether it is its home module's object; the package's
# modules loaded. The new modules' names read as SOURCE's (MAP).
_SNAP = r'''
import dataclasses, functools, importlib, inspect, json, re, sys, types
spec = json.load(open(sys.argv[1], encoding="utf-8"))
MAP = sorted(spec["map"].items(), key=lambda kv: -len(kv[0]))
ADDR = re.compile(r" at 0x[0-9a-fA-F]+")
ROOT = spec["root"]
SKIP = {"__dict__", "__weakref__", "__module__", "__doc__", "__slots__", "__dataclass_fields__",
        "__dataclass_params__", "__firstlineno__", "__static_attributes__"}
def norm(s):
    s = ADDR.sub("", s).replace(ROOT, "<tree>")
    for a, b in MAP:
        s = re.sub(r"(?<![\w.])" + re.escape(a) + r"(?![\w])", b, s)
    return s
def r(v):
    try:
        return norm(repr(v))
    except Exception as e:
        return f"<repr failed: {type(e).__name__}>"
def mod(m):
    return dict(spec["map"]).get(m, m)
def fn(f, depth=0):
    inner = vars(f).get("__wrapped__")
    return {"kind": "function", "qualname": f.__qualname__, "module": mod(f.__module__),
            "doc": f.__doc__, "defaults": r(f.__defaults__), "kwdefaults": r(f.__kwdefaults__),
            "annotations": r(getattr(f, "__annotations__", None)),
            "attrs": sorted(k for k in vars(f) if k != "__wrapped__"),
            "wrapped": fn(inner, depth + 1) if isinstance(inner, types.FunctionType)
            and depth < 5 else None}
def member(v, depth):
    if isinstance(v, functools._lru_cache_wrapper):
        return {"kind": "lru_cache", "params": v.cache_parameters(), "of": fn(v.__wrapped__)}
    if isinstance(v, types.FunctionType):
        return fn(v)
    if isinstance(v, (staticmethod, classmethod)):
        return [type(v).__name__, member(v.__func__, depth)]
    if isinstance(v, property):
        return ["property", r(v.__doc__)] + [member(x, depth) if x else None
                                             for x in (v.fget, v.fset, v.fdel)]
    if isinstance(v, type) and depth < 3:
        return cls(v, depth + 1)
    if isinstance(v, types.ModuleType):
        return ["module", mod(v.__name__)]
    return [mod(type(v).__module__) + "." + type(v).__qualname__, r(v)]
def cls(c, depth=0):
    out = {"kind": "class", "qualname": c.__qualname__, "module": mod(c.__module__),
           "doc": c.__doc__, "mro": [mod(k.__module__) + "." + k.__qualname__ for k in c.__mro__],
           "slots": r(c.__dict__.get("__slots__")),
           "dataclass": r(c.__dict__.get("__dataclass_params__")), "fields": None,
           "members": {k: member(v, depth) for k, v in sorted(vars(c).items()) if k not in SKIP}}
    if dataclasses.is_dataclass(c):
        out["fields"] = [[f.name, r(f.type), r(f.default), r(f.default_factory), f.init, f.repr,
                          r(f.hash), f.compare, r(f.kw_only), r(f.metadata)]
                         for f in dataclasses.fields(c)]
    return out
def shape(v):
    if isinstance(v, type):
        return cls(v)
    return member(v, 0)
src = importlib.import_module(spec["source"])
out = {"file": getattr(src, "__file__", None), "names": {}, "errors": []}
MISSING = object()
for name, (where, attr, here) in sorted(spec["names"].items()):
    v = getattr(src, name, MISSING)
    if v is MISSING:
        continue
    try:
        home = importlib.import_module(where)
        same = (home if attr == "" else getattr(home, attr, MISSING)) is v
    except Exception as e:
        out["errors"].append(f"{name}: {where}: {type(e).__name__}: {e}")
        same, home = False, None
    f = v
    if callable(v) and not isinstance(v, type):
        try:
            f = inspect.unwrap(v)     # the function defined there, below any wrapper
        except Exception:
            pass
    glob = None
    if here and isinstance(f, types.FunctionType) and home is not None:
        glob = f.__globals__ is vars(home)
    module = v.__module__ if isinstance(v, type) else (
        f.__module__ if isinstance(f, types.FunctionType) else None)
    out["names"][name] = {"is": same, "globals": glob, "module": module, "shape": shape(v)}
top = spec["source"].split(".")[0]
out["loaded"] = sorted(k for k in sys.modules if k == top or k.startswith(top + "."))
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), sort_keys=True, default=repr)
'''


def exported(m: Module) -> Dict[str, Tuple[str, str, bool]]:
    """Each name SOURCE defines or re-exports -> (its home module, the name
    there, defined there): a def, class or assignment, an `X as X` import,
    a name of `__all__`."""
    out: Dict[str, Tuple[str, str, bool]] = {}
    _st, listed = m.all_names()
    for name, pairs in m.imports.items():
        for st, a in pairs:
            if (is_reexport(a) or name in listed) and isinstance(st, ast.ImportFrom) \
                    and not st.level:
                out[name] = (st.module or "", a.name, False)
            elif (is_reexport(a) or name in listed) and isinstance(st, ast.Import):
                out[name] = (a.name, "", False)
    for name in m.defs:
        if name != "__all__":
            out[name] = (m.module, name, True)
    return out


def snapshot(tree: Path, home: Path, source: str, names: Dict[str, Tuple[str, str, bool]],
             mapping: Dict[str, str], td: Path, tag: str) -> dict:
    d = td / f"snap-{tag}"
    d.mkdir()
    script, spec, out = d / "snap.py", d / "spec.json", d / "out.json"
    script.write_text(_SNAP, encoding="utf-8")
    spec.write_text(json.dumps({"source": source, "names": names, "map": mapping,
                                "root": str(tree)}), encoding="utf-8")
    home.mkdir()
    r = subprocess.run([sys.executable, "-B", str(script), str(spec), str(out)], cwd=home,
                       env=ES._child_env(tree, home), capture_output=True, text=True,
                       timeout=900)
    if r.returncode or not out.is_file():
        return {"error": (r.stderr or r.stdout).strip()[-400:] or f"exit {r.returncode}"}
    return json.loads(out.read_text(encoding="utf-8"))


def first_difference(a, b, path: str = "") -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                return first_difference(a.get(k), b.get(k), f"{path}.{k}" if path else k)
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return first_difference(x, y, f"{path}[{i}]")
    return f"{path or 'it'}: {str(a)[:80]} against {str(b)[:80]}"


def runtime_problems(base: Path, head: Path, batch: List[Row], home: Path, td: Path) -> List[str]:
    """Check 5: SOURCE imported in each tree."""
    out: List[str] = []
    new = {r.module for r in batch}
    parents = {".".join(m.split(".")[:i]) for m in new for i in range(1, m.count(".") + 1)}
    # Each new module's names read as its SOURCE's, for every source (one
    # re-exports another's moved name).
    mapping = {r.module: module_name(r.source) for r in batch if not r.package}
    runs = [0]

    def snap(tree: Path, source: str, names, mapping) -> dict:
        runs[0] += 1
        return snapshot(tree, home / f"run-{runs[0]}", source, names, mapping, td, str(runs[0]))
    for rel in dict.fromkeys(r.source for r in batch if not r.package):
        rows = [r for r in batch if r.source == rel and not r.package]
        b = Module(base / rel, rel)
        names = exported(b)
        to = {n: r.module for r in rows for n in r.names}
        head_names = {n: ((to[n], n, True) if n in to else w) for n, w in names.items()}
        a = snap(base, b.module, names, {})
        h = snap(head, b.module, head_names, mapping)
        for side, s in (("BASE", a), ("HEAD", h)):
            if "error" in s:
                out.append(f"runtime: {side}'s import of {b.module} fails: {s['error']}")
            elif not str(s["file"]).startswith(str((base if side == "BASE" else head) / "src")):
                out.append(f"runtime: {side} imported {b.module} from {s['file']}, not its tree")
        if any("error" in s for s in (a, h)):
            continue
        out += [f"runtime: HEAD: {e}" for e in h["errors"]]
        for n in sorted(names):
            x, y = a["names"].get(n), h["names"].get(n)
            if x is None:
                out.append(f"runtime: BASE's {b.module} lacks {n}")
            elif y is None:
                out.append(f"runtime: HEAD's {b.module} lacks {n}")
            elif not y["is"] or y["globals"] != x["globals"]:
                w = head_names[n]
                out.append(f"runtime: HEAD's {b.module}.{n} is not {w[0]}'s object"
                           + (" (its globals are another module's)" if y["is"] else ""))
            elif n in to and y["module"] not in (None, to[n]):
                out.append(f"runtime: HEAD's {n} says its module is {y['module']}, not {to[n]}")
            elif x["shape"] != y["shape"]:
                out.append(f"runtime: {n}'s shape differs: {first_difference(x['shape'], y['shape'])}")
        extra = sorted(set(h["loaded"]) - set(a["loaded"]) - new - parents)
        fewer = sorted(set(a["loaded"]) - set(h["loaded"]))
        unloaded = sorted({r.module for r in rows} - set(h["loaded"]))
        if extra or fewer or unloaded:
            out.append(f"runtime: HEAD loads {extra or 'no more'} beyond BASE's and the new "
                       f"modules, {fewer or 'none'} fewer; new ones not loaded: {unloaded or 'none'}")
    for r in batch:
        s = snap(head, r.module, {}, {})
        if "error" in s:
            out.append(f"runtime: {r.module} does not import alone: {s['error']}")
    return out


def check(repo: Path, base_rev: str, head_rev: str, allow: List[str], table_rel: str) -> int:
    failed = "check: FAILED (the text check; nothing of HEAD's was run)"
    with tempfile.TemporaryDirectory(prefix="move-defs-check-") as tdn:
        td = Path(tdn)
        base, head, work, home = (td / n for n in ("base", "head", "work", "home"))
        for d in (base, head, home):
            d.mkdir()
        try:
            base_e = ES.materialize(repo, base_rev, base)
            head_e = ES.materialize(repo, head_rev, head)
        except ES.Refused as e:
            print(f"check: {e}\n{failed}")
            return 1
        if table_rel not in head_e:
            print(f"check: HEAD has no {table_rel}: no batch", file=sys.stderr)
            return 2
        try:
            rows = read_table((head / table_rel).read_text(encoding="utf-8"))
        except Refused as e:
            print(f"check: HEAD's table: {e}\n{failed}")
            return 1
        batch = [r for r in rows if r.target not in base_e]
        if not batch:
            print(f"check: HEAD's {table_rel} has no row whose target BASE lacks: no batch",
                  file=sys.stderr)
            return 2
        print(f"check: {len(batch)} row(s): {', '.join(r.target for r in batch)}")
        bad = text_problems(repo, base, work, head_e, base_e, table_rel, allow)
        if bad:
            for p in bad:
                print(f"check: {p}")
            print(failed)
            return 1
        changed = sorted(f for f in set(base_e) | set(head_e) if base_e.get(f) != head_e.get(f))
        problems: List[str] = []

        def stage(name: str, found: List[str], passed: str) -> None:
            problems.extend(found)
            print(f"check: {name}: " + (passed if not found else
                                        f"FAILED, {len(found)} problem(s), listed below"))
        stage("pins", pin_problems(changed) + mutation_problems(base, head),
              f"{len(changed)} file(s) changed, none below tests/; TARGET_FUNCS resolve to "
              f"BASE's functions")
        stage("baseline", baseline_problems(base, head, batch),
              "no kind's total grew, the files the batch does not write and the long "
              "functions, re-keyed, are BASE's")
        for name, fn, passed in (
                ("AST", ast_problems, "each moved statement and its comments, the new modules "
                                      "and the source"),
                ("bindings", binding_problems, "every global the moved statements and the "
                                               "source read")):
            try:
                found = fn(base, head, batch)
            except Refused as e:
                found = [f"{name}: {e}"]
            stage(name, found, passed)
        files, bad, left = seam_problems(head, rows)
        stage("seams", bad, f"{files} file(s) of {', '.join(SEAM_DIRS)}: none of the forms the "
                            f"seams stage reads patches or stores through a source a name it no "
                            f"longer defines (every row's) or an import its new modules read, "
                            f"or reads its file or text")
        if left:
            print(f"check: seams: left to B0b: {'; '.join(left)}")
        stage("runtime", runtime_problems(base, head, batch, home, td),
              "each source imported in both trees, each new module alone")
        stage("layers", _layers_check(head, batch, rows),
              "check_architecture, the country rule, the cycle rule")
    for p in problems:
        print(f"check: {p}")
    print("check: " + ("FAILED" if problems else "HEAD is BASE's tool run on BASE, and the "
                       "moves keep every name"))
    return 1 if problems else 0


def baseline_problems(base: Path, head: Path, batch: List[Row]) -> List[str]:
    """The baseline BASE's tool recorded keeps BASE's: no kind's total per
    code grows (one may shrink: an import the move drops takes its hit or
    its noqa with it), every file the batch does not write keeps its
    counts, the long functions are BASE's re-keyed to the modules they
    moved to, and every other key is BASE's."""
    bp, hp = base / BASELINE, head / BASELINE
    if not bp.is_file() or not hp.is_file():
        return [] if bp.is_file() == hp.is_file() else [f"baseline: only one tree has {BASELINE}"]
    b, h = (json.loads(MT.read_exact(x)) for x in (bp, hp))
    out = [f"baseline: {k} is not BASE's" for k in sorted(set(b) | set(h))
           if k not in _COUNTS + ("function_length",) and b.get(k) != h.get(k)]
    written = {r.source for r in batch} | {r.target for r in batch}
    for kind in _COUNTS:
        bk, hk = b.get(kind, {}), h.get(kind, {})
        for code in sorted(set(bk) | set(hk)):
            bf, hf = bk.get(code, {}), hk.get(code, {})
            if sum(hf.values()) > sum(bf.values()):
                out.append(f"baseline: {kind} {code}: {sum(hf.values())} in HEAD, "
                           f"{sum(bf.values())} in BASE (grown)")
            out += [f"baseline: {kind} {code} {f}: {hf.get(f)} in HEAD, {bf.get(f)} in BASE"
                    for f in sorted((set(bf) | set(hf)) - written) if bf.get(f) != hf.get(f)]
    moved = {(r.source, n): r.target for r in batch for n in r.names}
    want: Dict[str, List[int]] = {}
    for key, lengths in b.get("function_length", {}).items():
        path, _, qual = key.partition("::")
        new = moved.get((path, qual.split(".")[0]), path)
        want.setdefault(f"{new}::{qual}", []).extend(lengths)
    got = h.get("function_length", {})
    for k in sorted(set(want) | set(got)):
        if sorted(want.get(k, [])) != sorted(got.get(k, [])):
            out.append(f"baseline: function_length {k}: {got.get(k)} in HEAD, {want.get(k)} "
                       f"in BASE, re-keyed")
    return out


def pin_problems(changed: List[str]) -> List[str]:
    """Check 2: no file below a tests/ folder changed (tests/golden among them)."""
    return [f"pins: {f} changed (a test or golden file: a move changes none)" for f in changed
            if f.startswith("tests/") or "/tests/" in f]


def _resolve(tree: ast.AST, qual: str) -> Optional[ast.AST]:
    """mutation_audit's resolve_targets for one row: the def or class each
    part of QUAL names, a child of the one before."""
    node: Optional[ast.AST] = tree
    for part in qual.split("."):
        node = next((n for n in ast.iter_child_nodes(node) if isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part), None)
        if node is None:
            return None
    return node


def mutation_problems(base: Path, head: Path) -> List[str]:
    """Check 2's other half: each TARGET_FUNCS row of HEAD resolves, as
    mutation_audit's resolve_targets does, to the AST its row gave in BASE."""
    trees: Dict[Tuple[Path, str], Optional[ast.AST]] = {}

    def tree_of(root: Path, rel: str) -> Optional[ast.AST]:
        if (root, rel) not in trees:
            p = root / rel
            ok = not Path(rel).is_absolute() and ".." not in Path(rel).parts and p.is_file()
            trees[root, rel] = ast.parse(p.read_bytes()) if ok else None
        return trees[root, rel]
    if not (base / MUTATION).is_file():
        return []
    if not (head / MUTATION).is_file():
        return [f"pins: HEAD has no {MUTATION}"]
    rb, rh = (target_rows(tree_of(x, MUTATION)) for x in (base, head))
    if [q for _, q in rb] != [q for _, q in rh]:
        return [f"pins: {MUTATION}'s TARGET_FUNCS names other functions in HEAD"]
    out = []
    for (pb, q), (ph, _) in zip(rb, rh):
        x = _resolve(tree_of(base, pb.value), q) if tree_of(base, pb.value) else None
        y = _resolve(tree_of(head, ph.value), q) if tree_of(head, ph.value) else None
        if x is not None and (y is None or _dump(x) != _dump(y)):
            out.append(f"pins: {MUTATION}:{ph.lineno}: ({ph.value}, {q}) is not the function "
                       f"({pb.value}, {q}) gave in BASE")
    return out


def text_problems(repo: Path, base: Path, work: Path, head_e, base_e, table_rel: str,
                  allow: List[str]) -> List[str]:
    """Check 1: BASE's tool, run on BASE with HEAD's table, writes HEAD."""
    shutil.copytree(base, work, symlinks=True)
    tw = work / table_rel
    tw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(work.parent / "head" / table_rel, tw)
    tool = work / "scripts" / "move_defs.py"
    env = {k: v for k, v in _genv().items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, "-I", "-S", "-B", str(tool), str(tw), f"--repo={work}"],
                       cwd=work.parent, env=env, capture_output=True, text=True)
    if r.returncode:
        return [f"BASE's tool refused HEAD's table: {(r.stdout + r.stderr).strip()[-1500:]}"]
    work_e = ES.work_entries(repo, work)
    written = sorted(f for f in set(work_e) | set(base_e)
                     if work_e.get(f) != base_e.get(f) and f != table_rel)
    diff = sorted(f for f in set(work_e) | set(head_e) if work_e.get(f) != head_e.get(f))
    hand = [f for f in diff if f in allow and f.endswith(".md") and f in head_e
            and not (f.startswith("tests/") or "/tests/" in f)
            and (f not in work_e or work_e[f][0] == head_e[f][0])]
    print(f"check: text: the tool wrote {len(written)} file(s); beyond it, by hand: "
          f"{hand or 'none'}")
    return [f"text: {f} is not what BASE's tool writes from BASE" for f in diff if f not in hand]


def _layers_check(head: Path, batch: List[Row], rows: List[Row]) -> List[str]:
    out = []
    if (head / ARCH).is_file():
        rc, text = _arch(head, [])
        if rc:
            out.append(f"layers: HEAD's check_architecture fails: {text[-600:]}")
    out += layer_problems(head, {layer_of(r.module) for r in batch})
    out += [f"layers: {x}" for x in cycle_problems(import_graph(head, rows, {}), rows)]
    return out


def run_base_copy(repo: Path, base: str, head: str, flags: List[str]) -> int:
    """Run BASE's copy of this script: a batch cannot change its own proof."""
    VT = _vt()
    with tempfile.TemporaryDirectory(prefix="move-defs-tools-") as td:
        try:
            VT.export(base, Path(td), repo, TOOLS)
        except subprocess.CalledProcessError:
            print(f"check: BASE lacks one of {', '.join(TOOLS)}: the tool lands in a commit "
                  f"of its own before a batch", file=sys.stderr)
            return 2
        me = Path(td) / "scripts" / "move_defs.py"
        env = {k: v for k, v in _genv().items() if k != "PYTHONPATH"}
        return subprocess.run([sys.executable, "-I", "-S", "-B", str(me), "--check", base, head,
                               f"--repo={repo}", "--base-copy", *flags], env=env).returncode


def _check_main(repo: Path, args: List[str], flags: List[str], allow: List[str],
                table_rel: str) -> int:
    VT = _vt()
    bad = VT.rewritten_history(repo)
    if bad:
        print(f"check: {bad}: git would not show the objects themselves", file=sys.stderr)
        return 2
    head, hkind = VT.resolve(repo, args[1] if len(args) > 1 else "HEAD")
    base, bkind = VT.resolve(repo, args[0] if args else VT.default_base(repo, head))
    if "--trees" not in flags:
        if "tree" in (bkind, hkind):
            print("check: BASE and HEAD are commits (--trees: a synthetic repository's trees)",
                  file=sys.stderr)
            return 2
        bad = VT.ancestry_problem(repo, base, head)
        if bad:
            print(f"check: {bad}", file=sys.stderr)
            return 2
    if "--base-copy" not in flags:
        rest = [f"--allow={','.join(allow)}"] if allow else []
        rest += [f"--table={table_rel}"] + (["--trees"] if "--trees" in flags else [])
        return run_base_copy(repo, base, head, rest)
    return check(repo, base, head, allow, table_rel)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    repo, allow, args, flags, table_rel = REPO, [], [], [], TABLE
    for a in argv:
        if a.startswith("--repo="):
            repo = Path(a[len("--repo="):]).resolve()
        elif a.startswith("--allow="):
            allow += [x for x in a[len("--allow="):].split(",") if x]
        elif a.startswith("--table="):
            table_rel = a[len("--table="):]
        elif a in ("--check", "--dry-run", "--trees", "--base-copy"):
            flags.append(a)
        else:
            args.append(a)
    usage = __doc__.split("\n\n")[1]
    try:
        if "--check" in flags:
            if len(args) > 2 or any(a.startswith("--") for a in args) or "--dry-run" in flags:
                print(usage, file=sys.stderr)
                return 2
            return _check_main(repo, args, flags, allow, table_rel)
        if len(args) != 1 or set(flags) - {"--dry-run"} or args[0].startswith("--"):
            print(usage, file=sys.stderr)
            return 2
        return move(repo, Path(args[0]), "--dry-run" in flags)
    except Refused as e:
        print(f"move_defs: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
