"""Shared test helpers: an account's shares transferred out and in.

Shared from: test_fix_audit_1025_transfers
"""
import os
import subprocess
from pathlib import Path

from tax_rules.dual import settings_for

from taxjson.lib.dispatch import python_module_argv
import _paths


REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _project(root: Path, country: str, in_date: str) -> Path:
    """Account qa buys 100 ZZQ at 2.00 and transfers them out on
    2024-03-01; account qb receives 100 (TRANSFER BOOK VALUE 250.00) on
    `in_date` and sells them at 3.00 on 2024-06-03. One move of your
    own: the sale's cost is the purchase's 200, its gain 100."""
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2024, source_currencies=[])
        + '[accounts.qa]\ntype = "taxable"\n'
        + '[accounts.qb]\ntype = "taxable"\n')
    for acct in ("qa", "qb"):
        (root / "inputs" / acct).mkdir(parents=True)
    (root / "inputs" / "qa" / "questrade_2024.csv").write_text(
        _QH + f"2024-01-10,2024-01-11,Buy,ZZQ,ZZQ CORP,100,2.00,-200.00,"
        f"0.00,-200.00,{cur},Trades,99900001,Margin\n"
        f"2024-03-01,2024-03-01,TF6,ZZQ,ZZQ CORP TRANSFER OUT,-100,0.00,"
        f"0.00,0.00,0.00,{cur},Transfers,99900001,Margin\n")
    (root / "inputs" / "qb" / "questrade_2024.csv").write_text(
        _QH + f"{in_date},{in_date},TF6,ZZQ,ZZQ CORP TRANSFER BOOK VALUE "
        f"250.00,100,0.00,0.00,0.00,0.00,{cur},Transfers,99900002,Margin\n"
        f"2024-06-03,2024-06-04,Sell,ZZQ,ZZQ CORP,-100,3.00,300.00,0.00,"
        f"300.00,{cur},Trades,99900002,Margin\n")
    return root
