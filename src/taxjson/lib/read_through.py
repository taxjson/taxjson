"""A module that reads some names through another refuses a copy of them.

bin/fill_crypto_prices and bin/to_base_curr read the cache path, the
loaders and the lookups a test fakes through lib/crypto_prices and
lib/rate_cache, and bind no copy (step 8h). A copy set on the bin module
would be read by nothing: `fc.CACHE_FILE = tmp` would leave the moved
loader on the process's cache. So the module refuses it, whatever reaches
the module (an assignment, setattr, `mock.patch.object(..., create=True)`),
and names the module that holds it (backlog TJ-3529). A write to the
module's dict itself (`vars(m)[name] = x`, `mock.patch.dict(m.__dict__)`)
does not pass through the class: tests/test_cache_paths.py refuses a
test's (TJ-3535)."""
import sys
import types
from typing import Iterable


def refuse_copies(module_name: str, home: str, names: Iterable[str]) -> None:
    """Make the imported module `module_name` refuse an assignment of any
    of `names`, which `home` binds and it reads from there."""
    refused = frozenset(names)

    class _ReadThrough(types.ModuleType):
        def __setattr__(self, name, value):
            if name in refused:
                raise AttributeError(
                    f"{module_name}.{name}: {home} holds it and {module_name} reads "
                    f"it from there, so a copy here is read by nothing: set "
                    f"{home}.{name}")
            super().__setattr__(name, value)

    sys.modules[module_name].__class__ = _ReadThrough
