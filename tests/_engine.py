"""The gains engine's seams, for the tests (PLAN.md step 10, B0b).

Step 10 moved lib/core.py's definitions into the package taxjson.engine,
batch by batch, lib/core.py left re-exporting every name until the shim
was removed after v1.0.0. A name is bound in its own module and in each
module importing it: a test that patches one, reads the engine's text or
names one of its files goes through here, never through a single module,
which would reach that module alone, often silently (scripts/move_defs.py
`B0B_HELPER` counts this file's seams). The engine is every module below
taxjson/engine/.

  modules()           each engine module, by name
  files()             their files, in that order
  binding(name)       the engine's object for `name`
  binders(name)       the modules whose `name` is that object
  patch_engine(n, v)  a context manager: `n` is `v` in each of binders(n)
  store_engine(n, v)  the same for good (a child's planted sitecustomize)
  source()            the engine's text: each of files(), joined
  file_with(marker)   the one file holding `# cov: <marker>` (tests/_tripwire)
  site_pattern()      a regex of "taxjson/<path>" for each of files()
  loader()            the lines a planted sitecustomize loads this file with

Imports only the standard library and taxjson: a child loads it by path.
tests/test_engine_seams.py proves each reaches every engine module."""
import contextlib
import importlib
import re
from pathlib import Path

import taxjson

_PKG = Path(taxjson.__file__).resolve().parent
_MISSING = object()


def _engine_names() -> list:
    """The engine package's modules, by dotted name, sorted by file."""
    root = _PKG / "engine"
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*.py")):
        parts = p.relative_to(_PKG.parent).with_suffix("").parts
        out.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return out


def modules() -> list:
    """Each module of taxjson.engine (imported), sorted by file."""
    return [importlib.import_module(n) for n in _engine_names()]


def files() -> list:
    """The file of each of modules(), resolved."""
    return [Path(m.__file__).resolve() for m in modules()]


def _bound(name: str) -> list:
    """(module, object) for each of modules() binding `name`; refused when
    none does, or when two bind different objects under it: a patch that
    would miss a reader fails here, never silently."""
    bound = [(m, vars(m).get(name, _MISSING)) for m in modules()]
    bound = [(m, v) for m, v in bound if v is not _MISSING]
    if not bound:
        raise LookupError(f"no module of the engine binds {name!r}")
    other = [m.__name__ for m, v in bound if v is not bound[0][1]]
    if other:
        raise LookupError(f"{name!r} is another object in {', '.join(other)}: "
                          f"patch each by hand")
    return bound


def binding(name: str):
    """The engine's object for `name`: the one object the engine's modules
    bind under it."""
    return _bound(name)[0][1]


def binders(name: str) -> list:
    """The modules of the engine binding `name` to binding(name): the
    module defining it and each importing it."""
    return [m for m, _v in _bound(name)]


@contextlib.contextmanager
def patch_engine(name: str, new):
    """`name` is `new` in every module of binders(name) inside the block,
    each restored after it (unittest.mock's patch.object, one per module)."""
    from unittest import mock
    with contextlib.ExitStack() as stack:
        for m in binders(name):
            stack.enter_context(mock.patch.object(m, name, new))
        yield new


def store_engine(name: str, new) -> None:
    """`name` is `new` in every module of binders(name), for good."""
    for m in binders(name):
        setattr(m, name, new)


def source() -> str:
    """The engine's text: each of files() whole, in order, one newline
    between."""
    return "\n".join(p.read_text(encoding="utf-8") for p in files())


def file_with(marker: str) -> str:
    """The one file of the engine holding the coverage marker `# cov:
    <marker>` at a line's end, for tests/_tripwire.py's Tripwire."""
    tag = f"# cov: {marker}"
    hits = [p for p in files()
            if any(x.rstrip().endswith(tag) for x in p.read_text(encoding="utf-8").splitlines())]
    if len(hits) != 1:
        raise AssertionError(f"marker {tag!r} found in {len(hits)} files of the engine")
    return str(hits[0])


def site_pattern() -> str:
    """A regex matching the path a money stop names for a line of the
    engine ("taxjson/<module path>.py"), any of files()."""
    rels = [p.relative_to(_PKG.parent).as_posix() for p in files()]
    return "(?:" + "|".join(re.escape(r) for r in rels) + ")"


def loader(var: str = "_engine") -> str:
    """Python lines binding `var` to this module loaded by its path, for a
    sitecustomize a test plants in a child (tests/ is not on its path)."""
    from _paths import TESTS
    path = str(TESTS / "_engine.py")
    return (f"import importlib.util as _iu\n"
            f"_spec = _iu.spec_from_file_location('_engine', {path!r})\n"
            f"{var} = _iu.module_from_spec(_spec)\n"
            f"_spec.loader.exec_module({var})\n")
