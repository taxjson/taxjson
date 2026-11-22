"""Shared test helpers: IB margin and RRSP rows, a project in either country and the CLI, for the missing-history scope.

Shared from: test_fix_mhscope
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import _paths


REPO = _paths.REPO

_TOML = {
    "canada": ('[settings]\nbase_currency = "CAD"\ncountry = "canada"\n'
               'source_currencies = []\nyear = 2025\n'
               'option_grant_timing_since = 2025\n'),
    "usa": ('[settings]\nbase_currency = "USD"\ncountry = "usa"\n'
            'source_currencies = []\nyear = 2025\n'),
}
_CUR = {"canada": "CAD", "usa": "USD"}


def _ib(acct, rows, cur):
    head = ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers (Synthetic)\n"
            "Statement,Data,Title,Activity Statement\n"
            'Statement,Data,Period,"January 1, 2024 - December 31, 2025"\n'
            "Account Information,Header,Field Name,Field Value\n"
            f"Account Information,Data,Account,{acct}\n"
            "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
            "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
            "Realized P/L,MTM P/L,Code\n")
    body = "".join(
        f'Trades,Data,Order,Stocks,{cur},{s},"{d}, 10:00:00",{q},{p},{p},'
        f'{-q * p:.2f},-1.00,{b},0,0,{c}\n' for s, d, q, p, b, c in rows)
    return head + body


# margin: QPAS / QOPN went short in 2024 (a sale coded closing, nothing
# held) and never trade again; QABC went short in 2024 and is bought in
# 2025 (the cover draws on it); QXYZ is sold in 2025 with nothing held;
# QOK is a clean round trip. rrsp: QRGL short in 2024 only, QRGT in 2025.
MARGIN = [("QPAS", "2024-03-01", -100, 2, -150, "C"),
          ("QOPN", "2024-07-02", -30, 5, -120, "C"),
          ("QABC", "2024-06-03", -20, 4, -60, "C"),
          ("QABC", "2025-02-03", 50, 4.5, 226, "O"),
          ("QXYZ", "2025-05-01", -10, 50, -400, "C"),
          ("QOK", "2025-03-03", 10, 10, 101, "O"),
          ("QOK", "2025-04-01", -10, 12, -101, "C")]
RRSP = [("QRGL", "2024-04-01", -5, 30, -100, "C"),
        ("QRGT", "2025-04-01", -5, 30, -100, "C")]


def _env(**extra):
    e = dict(os.environ)
    e.pop("TAXJSON_WIDTH", None)            # piped: a person's width 100
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src") + os.pathsep + e.get(
        "PYTHONPATH", "")
    e.update(extra)
    return e


def _make(country, *, extra_accounts=None):
    tmp = tempfile.mkdtemp(prefix="taxjson_mhscope_")
    root = Path(tmp) / country
    cur = _CUR[country]
    accts = {"margin": ("taxable", "U5550001", MARGIN),  # pii-ok
             "rrsp": ("sheltered", "U5550002", RRSP)}  # pii-ok
    accts.update(extra_accounts or {})
    toml = _TOML[country]
    for name, (kind, acct, rows) in accts.items():
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / f"ib_{name}.csv").write_text(
            _ib(acct, rows, cur))
        toml += f'\n[accounts.{name}]\ntype = "{kind}"\n'
    (root / "taxjson.toml").write_text(toml)
    return tmp, root


def _tj(root, *args, merged=False, **env):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=root, env=_env(**env), timeout=900, text=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merged else subprocess.PIPE)
