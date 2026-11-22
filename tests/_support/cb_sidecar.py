"""Shared test helpers: taxjson-brokerage over Coinbase files with a sidecar.

Shared from: test_fix_1029_r7b_coinbase
"""
import os


def _parse_sidecar(sidecar, *csvs):
    """taxjson-brokerage over `csvs` (Coinbase, Canada) writing the
    transfer sidecar `sidecar`."""
    import subprocess
    from taxjson.lib.dispatch import python_module_argv
    env = dict(os.environ, TAXJSON_WIDTH="0", TAXJSON_OFFLINE="1")
    r = subprocess.run(python_module_argv(
        "taxjson.bin.taxjson_brokerage",
        ["--brokerage", "coinbase", "--country", "canada", "--account",
         "coin", "--transfers-out", str(sidecar), *map(str, csvs)]),
        capture_output=True, text=True, env=env)
    if r.returncode:
        raise AssertionError(r.stderr)
