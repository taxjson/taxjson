"""Python 3.11's float sum() on a later Python (tests/_golden.py, `old_sum`).

On PYTHONPATH it loads in every Python process of a run (`-P`, the children's
flag, drops the script's folder, not PYTHONPATH). It is idle unless
TAXJSON_TEST_OLD_SUM is set and not 0; then builtins.sum adds left to right,
one float addition at a time, as Python 3.10 and 3.11 do. Python 3.12's sum()
compensates a float total (Neumaier), so a figure added with sum() and rounded
to the cent could differ by Python version (TJ-2791: the T1135 maximum, 0.01
more on 3.10 and 3.11). The golden books are recorded on 3.12; the clock test
runs them again under this, so the gate sees what CI's 3.10 and 3.11 would.

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

_ON = os.environ.get("TAXJSON_TEST_OLD_SUM", "").strip()
if _ON and _ON != "0" and sys.version_info >= (3, 12):
    import builtins

    def _sum(iterable, /, start=0):
        """builtins.sum without 3.12's compensated float addition."""
        if isinstance(start, (str, bytes, bytearray)):
            raise TypeError(f"sum() can't sum {type(start).__name__}: use ''.join(seq) instead")
        total = start
        for x in iterable:
            total = total + x
        return total

    builtins.sum = _sum
