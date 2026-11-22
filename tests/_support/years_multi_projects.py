"""Shared test helpers: year projects sharing a folder of exports, a single-year project, the CLI and a run that must pass.

Shared from: test_fix_multi_year
"""
import subprocess
import sys
from pathlib import Path

from _style import env
from _tmpfiles import private_dir


YEARS = (2023, 2024, 2025)

_TT = {
    "canada": {
        "margin": """\
# synthetic
BUYSELL 2023-02-01 10:00:00 QZQ.TO 100 CAD 20.00 2009.95 9.95
BUYSELL 2023-06-01 10:00:00 QZQ.TO -50 CAD 25.00 1240.05 9.95
DIVIDEND 2023-09-28 16:00:00 QZQ.TO 50 CAD 0.40 20.00
BUYSELL 2024-03-01 10:00:00 ZZB.TO 40 CAD 10.00 409.95 9.95
BUYSELL 2024-12-16 10:00:00 ZZB.TO -40 CAD 8.00 310.05 9.95
BUYSELL 2025-01-08 10:00:00 ZZB.TO 40 CAD 8.50 349.95 9.95
BUYSELL 2025-05-01 10:00:00 QZQ.TO -50 CAD 30.00 1490.05 9.95
BUYSELL 2025-07-01 10:00:00 ZZB.TO -40 CAD 9.00 350.05 9.95
DIVIDEND 2025-03-28 16:00:00 QZQ.TO 50 CAD 0.40 20.00
""",
        "tfsa": "BUYSELL 2024-05-01 10:00:00 QZQ.TO 10 CAD 22.00 229.95 9.95\n",
    },
    "usa": {
        "margin": """\
# synthetic
BUYSELL 2023-02-01 10:00:00 QZQ.US 100 USD 20.00 2001.00 1.00
BUYSELL 2023-06-01 10:00:00 QZQ.US -50 USD 25.00 1249.00 1.00
DIVIDEND 2023-09-28 16:00:00 QZQ.US 50 USD 0.40 20.00
BUYSELL 2024-03-01 10:00:00 ZZB.US 40 USD 10.00 401.00 1.00
BUYSELL 2024-12-16 10:00:00 ZZB.US -40 USD 8.00 319.00 1.00
BUYSELL 2025-01-08 10:00:00 ZZB.US 40 USD 8.50 341.00 1.00
BUYSELL 2025-05-01 10:00:00 QZQ.US -50 USD 30.00 1499.00 1.00
BUYSELL 2025-07-01 10:00:00 ZZB.US -40 USD 9.00 359.00 1.00
""",
        "roth": "BUYSELL 2024-05-01 10:00:00 QZQ.US 10 USD 22.00 221.00 1.00\n",
    },
}


def _toml(country: str, year: int, shared: bool) -> str:
    sheltered = "tfsa" if country == "canada" else "roth"
    lines = ["[settings]", f"year = {year}", f'country = "{country}"']
    if shared:
        lines += ['inputs_dir = "../inputs"', 'exports_dir = "../exports"']
    if country == "canada":
        lines += ['tax_date = "settle"', 'base_currency = "CAD"',
                  'source_currencies = ["USD"]',
                  "option_grant_timing_since = 2023"]
    else:
        lines += ['tax_date = "trade"', 'base_currency = "USD"']
    lines += ["", "[accounts.margin]", 'type = "taxable"', "",
              f"[accounts.{sheltered}]", 'type = "sheltered"',
              "transfers = true", ""]
    return "\n".join(lines)


def _inputs(country: str, where: Path) -> None:
    for acct, text in _TT[country].items():
        d = where / acct
        d.mkdir(parents=True, exist_ok=True)
        (d / "trades.tt").write_text(text)


def tjs(*args, cwd=None, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", *args],
        cwd=cwd, capture_output=True, text=True, env=env(TAXJSON_WIDTH=0),
        timeout=900,
        stdin=subprocess.DEVNULL if stdin is None else stdin)


def multi(country: str, years=YEARS) -> Path:
    """A folder of exports shared by the year projects."""
    top = Path(private_dir()) / "taxes"
    _inputs(country, top / "inputs")
    for y in years:
        (top / str(y)).mkdir(parents=True)
        (top / str(y) / "taxjson.toml").write_text(_toml(country, y, True))
    return top


def single(country: str, year: int) -> Path:
    root = Path(private_dir()) / "p"
    _inputs(country, root / "inputs")
    root.mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(_toml(country, year, False))
    return root


def run_ok(tc, folder: Path):
    r = tjs("-C", str(folder), "run", "--no-input")
    tc.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
    return r
