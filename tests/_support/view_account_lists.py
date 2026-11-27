"""Shared test helpers: the projects the ACCOUNT-list tests read.

The row listings' and the totals' tests of an ACCOUNT list (`tjs divs-sum
alpha,beta`, lib/account_list) read these projects, built by `tjs run`.

Shared from: features/listings/test_account_lists_in_row_listings,
features/totals/test_account_lists_in_totals
"""
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple

from _support.cli_command_runs import _tjs
import _paths


# A Canada project of .tt books (made-up tickers): alpha and beta
# (taxable: trades, a dividend, a payment in lieu, a return of capital, a
# LEAPS bought and sold, a covered call written and bought back, USD cash
# moved in; both trade ZZQ.TO, its dividend, ROC and calls: a ticker of
# two listed accounts), alpha_us (taxable, alpha's prefix: fees-sum's
# sibling rule), rrsp and tfsa (sheltered, their transfers kept in the
# books; rrsp a dividend, a payment in lieu and a ROC), idle (declared, no
# inputs: no books).
TT_TOML = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = ["USD"]
option_grant_timing_since = 2025

[accounts.alpha]
type = "taxable"

[accounts.alpha_us]
type = "taxable"

[accounts.beta]
type = "taxable"

[accounts.idle]
type = "taxable"

[accounts.rrsp]
type = "sheltered"
transfers = true

[accounts.tfsa]
type = "sheltered"
transfers = true
"""

TT_ROWS = {
    "alpha": """\
CASHOPEN 2025-01-01 USD 0 0
CASHMOVE 2025-01-15 USD 1000 cost=1300
BUYSELL 2025-01-06 10:00:00 ZZQ.TO 10 CAD 100 1004.95 4.95
BUYSELL 2025-01-10 10:00:00 ZZQ270115C00050000.TO 1 CAD 5 500 0
BUYSELL 2025-02-03 10:00:00 ZZQ250418C00150000.TO -1 CAD 2 200 0
BUYSELL 2025-03-06 10:00:00 ZZQ.TO -5 CAD 120 595.05 4.95
DIVIDEND 2025-04-01 10:00:00 ZZQ.TO 0 CAD 0 12.50 0
BUYSELL 2025-04-01 10:00:00 ZZQ250418C00150000.TO 1 CAD 0.5 50 0
DIVIDEND_IN_LIEU 2025-04-20 10:00:00 ZZQ.TO 5 CAD 0 1.25
BUYSELL 2025-06-02 10:00:00 ZZQ270115C00050000.TO -1 CAD 7 700 0
FEE 2025-06-30 16:00:00 CAD 10.00
ADJUST 2025-09-30 16:00:00 ZZQ.TO CAD -3.45 type=roc
""",
    "beta": """\
CASHOPEN 2025-01-01 USD 0 0
CASHMOVE 2025-02-01 USD 500 cost=650
BUYSELL 2025-01-12 10:00:00 YYR270115C00040000.TO 2 CAD 3 600 0
BUYSELL 2025-02-06 10:00:00 YYR.TO 20 CAD 50 1000 0
BUYSELL 2025-03-01 10:00:00 YYR250620C00060000.TO -1 CAD 1 100 0
DIVIDEND 2025-04-15 10:00:00 YYR.TO 0 CAD 0 7.50 0
BUYSELL 2025-05-01 10:00:00 YYR250620C00060000.TO 1 CAD 0.2 20 0
BUYSELL 2025-05-06 10:00:00 YYR.TO -10 CAD 40 395 5
BUYSELL 2025-07-02 10:00:00 YYR270115C00040000.TO -2 CAD 4 800 0
ADJUST 2025-10-15 16:00:00 YYR.TO CAD -2.00 type=roc
DIVIDEND_IN_LIEU 2025-04-25 10:00:00 YYR.TO 10 CAD 0 0.80
BUYSELL 2025-02-20 10:00:00 ZZQ.TO 4 CAD 110 440 0
BUYSELL 2025-03-03 10:00:00 ZZQ250418C00150000.TO -1 CAD 1.5 150 0
DIVIDEND 2025-04-01 10:00:00 ZZQ.TO 0 CAD 0 2.00 0
BUYSELL 2025-04-10 10:00:00 ZZQ250418C00150000.TO 1 CAD 0.3 30 0
ADJUST 2025-06-30 16:00:00 ZZQ.TO CAD -0.40 type=roc
BUYSELL 2025-08-20 10:00:00 ZZQ.TO -4 CAD 115 460 0
""",
    "alpha_us": """\
BUYSELL 2025-03-10 10:00:00 XXT.TO 5 CAD 20 109.95 9.95
BUYSELL 2025-08-10 10:00:00 XXT.TO -5 CAD 25 115.05 9.95
DIVIDEND 2025-06-01 10:00:00 XXT.TO 0 CAD 0 3.00 0
""",
    "rrsp": """\
BUYSELL 2025-02-10 10:00:00 XXS.TO 5 CAD 20 100 0
DIVIDEND 2025-05-01 10:00:00 XXS.TO 0 CAD 0 2.00 0
DIVIDEND_IN_LIEU 2025-05-05 10:00:00 XXS.TO 5 CAD 0 0.40
ADJUST 2025-06-30 16:00:00 XXS.TO CAD -0.60 type=roc
BUYSELL 2025-08-10 10:00:00 XXS.TO -5 CAD 30 150 0
TRANSFER 2025-09-01 10:00:00 XXS.TO 3 CAD 20 60
""",
    "tfsa": """\
TRANSFER 2025-03-15 10:00:00 WWU.TO 4 CAD 10 40
BUYSELL 2025-06-15 10:00:00 WWU.TO -4 CAD 12 48 0
""",
}

# A Canada project of 2024 with two crypto accounts (the Coinbase demo
# export, the second's ids and one reward changed), an equity one and
# spare (declared, no inputs: no books).
CRYPTO_TOML = """\
[settings]
local_timezone = "America/Toronto"
year = 2024
country = "canada"
base_currency = "CAD"
source_currencies = ["USD"]
option_grant_timing_since = 2024

[accounts.coins]
type = "taxable"
crypto = true

[accounts.margin]
type = "taxable"

[accounts.spare]
type = "taxable"

[accounts.wallet]
type = "taxable"
crypto = true
"""

CRYPTO_MARGIN_TT = ("BUYSELL 2024-01-06 10:00:00 ZZQ.TO 10 CAD 100 1000 0\n"
                    "BUYSELL 2024-03-06 10:00:00 ZZQ.TO -5 CAD 120 600 0\n")


def _built(root: Path) -> Path:
    for p in [root, *root.rglob("*")]:
        p.chmod(0o700 if p.is_dir() else 0o600)
    r = _tjs(root, "run", "--no-input")
    if r.returncode != 0:
        raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
    return root


def tt_project(tmp: Path) -> Path:
    """The .tt project (TT_TOML, TT_ROWS) in `tmp`/p, built."""
    root = tmp / "p"
    root.mkdir()
    (root / "taxjson.toml").write_text(TT_TOML, encoding="utf-8")
    for name, rows in TT_ROWS.items():
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "rows.tt").write_text(rows, encoding="utf-8")
    return _built(root)


def crypto_project(tmp: Path) -> Path:
    """The crypto project (CRYPTO_TOML) in `tmp`/c, built: coins holds
    the Coinbase demo export; wallet a copy with its own ids and its
    first reward doubled; margin a sale of shares."""
    root = tmp / "c"
    for name in ("coins", "wallet", "margin"):
        (root / "inputs" / name).mkdir(parents=True)
    (root / "taxjson.toml").write_text(CRYPTO_TOML, encoding="utf-8")
    demo = _paths.EXAMPLES / "coinbase_demo.csv"
    shutil.copyfile(demo, root / "inputs" / "coins" / "coinbase.csv")
    text = demo.read_text(encoding="utf-8").replace("CB-DEMO-", "CB-ALT-")
    first = "0.00250000,USD,3200.00,8.00,8.00"
    if first not in text:
        raise AssertionError("the Coinbase demo's first reward changed")
    text = text.replace(first, "0.00500000,USD,3200.00,16.00,16.00")
    (root / "inputs" / "wallet" / "coinbase.csv").write_text(text, encoding="utf-8")
    (root / "inputs" / "margin" / "rows.tt").write_text(CRYPTO_MARGIN_TT,
                                                       encoding="utf-8")
    return _built(root)


class Views:
    """`tjs` on one built project, each argv run once (the tests of a
    class ask the same view more than once)."""

    def __init__(self, root: Path):
        self.root = root
        self._seen: Dict[Tuple[str, ...], Tuple[int, str, str]] = {}

    def run(self, *args: str) -> Tuple[int, str, str]:
        """`tjs ARGS`: (exit code, stdout, stderr)."""
        if args not in self._seen:
            r = _tjs(self.root, *args)
            self._seen[args] = (r.returncode, r.stdout, r.stderr)
        return self._seen[args]

    def doc(self, *args: str) -> Any:
        """`tjs ARGS --json`: its document (exit 0 asserted)."""
        code, out, err = self.run(*args, "--json")
        if code != 0:
            raise AssertionError(f"tjs {' '.join(args)} --json: exit {code}\n{err}")
        return json.loads(out)


def canon(rows: List[Any]) -> List[str]:
    """Rows as a sorted list of their JSON: a multiset, order aside."""
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def leaves(obj: Any, path: str = "") -> Dict[str, float]:
    """The numbers in `obj` by their key path (a bool or a string is no
    figure; a null leaf is left out)."""
    if isinstance(obj, dict):
        out: Dict[str, float] = {}
        for k, v in obj.items():
            out.update(leaves(v, f"{path}/{k}"))
        return out
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return {path: float(obj)}
    return {}


def rows_added(*row_lists: List[Dict[str, Any]]) -> Dict[str, float]:
    """The figures of summary rows added row key by row key: a row's key
    its fields that are no figure (symbol, currency ...), so a ticker two
    accounts hold adds up as one row of a list's run."""
    out: Dict[str, float] = {}
    for rows in row_lists:
        for r in rows:
            key = json.dumps({k: v for k, v in r.items() if not leaves({k: v})},
                             sort_keys=True)
            for k, v in leaves(r).items():
                out[key + k] = out.get(key + k, 0.0) + v
    return out


def added(*objs: Any) -> Dict[str, float]:
    """The sum of `objs`' figures key path by key path (`leaves`)."""
    out: Dict[str, float] = {}
    for o in objs:
        for k, v in leaves(o).items():
            out[k] = out.get(k, 0.0) + v
    return out
