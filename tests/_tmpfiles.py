"""Temp files for tests, each alone in its own private folder.

A parser that reads its folder's companions (a Kraken trades export
indexes every Kraken ledger beside it, a Webull export reads its other
years: brokerages/detect.same_broker_siblings) sees every CSV in the
same directory. A test file written straight into the shared temp root
(`tempfile.NamedTemporaryFile(...)` with no `dir=`) is therefore read by
every other test, and every other run or agent on the machine, that
parses a file there; one left behind by an interrupted run broke
unrelated tests ("Kraken ledger exports kr_ledgers_... and
kr_ledgers_... both carry ledger txid L1*** with DIFFERENT content").

    from _tmpfiles import private_tmpfile

    with private_tmpfile("w", suffix=".csv", prefix="kr_ledgers_",
                         delete=False) as f:
        f.write(text)

`private_tmpfile` takes tempfile.NamedTemporaryFile's arguments and
puts the file in a fresh `mkdtemp` folder of its own (unless the test
passes `dir=`), so no file ever has a sibling it did not create. The
folders are removed when the test process exits.
tests/test_temp_isolation.py refuses a bare NamedTemporaryFile, mkstemp
or gettempdir() in the test trees.
"""
import atexit
import shutil
import tempfile

_DIRS = []


@atexit.register
def _remove_dirs():
    for d in _DIRS:
        shutil.rmtree(d, ignore_errors=True)


def private_dir(prefix="taxjson-test-"):
    """A new empty folder of this process's own (removed at exit)."""
    d = tempfile.mkdtemp(prefix=prefix)
    _DIRS.append(d)
    return d


def private_tmpfile(*args, **kwargs):
    """tempfile.NamedTemporaryFile, alone in a fresh private folder."""
    if kwargs.get("dir") is None:
        kwargs["dir"] = private_dir()
    return tempfile.NamedTemporaryFile(*args, **kwargs)
