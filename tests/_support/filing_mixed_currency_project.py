"""Shared test helpers: a Canadian project with USD and CAD sales, built
by `tjs run` in a HOME of its own whose Bank of Canada rates differ day
by day, and the CLI run on it.

Shared from: features/forms/test_gains_cra_one_row_per_sale,
features/forms/test_gains_cra_rate_dates_and_account_scope,
features/summaries/test_sum_total_cost_column,
features/listings/test_gains_base_filing_figures

The margin account (taxable) holds a USD long sale with commissions, a
CAD long sale, a superficial loss (sold at a loss, bought back within 30
days), a USD call written under grant timing and bought back the same
year, a USD short sale opened in two parts and covered, and a USD future
closed; the tfsa account (sheltered) one CAD sale. Made-up tickers only.
"""
import datetime
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import _hermetic
import _paths
from _planted_rates import plant
from _tmpfiles import private_dir

from taxjson.lib.dispatch import python_module_argv

TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = ["USD"]\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n[accounts.tfsa]\ntype = "sheltered"\n')

MARGIN_TT = """\
BUYSELL 2025-01-06 10:00:00 QZRX.US 100 USD 20.00 2001.00 1.00
BUYSELL 2025-03-10 10:00:00 QZRX.US -40 USD 25.00 999.00 1.00
BUYSELL 2025-01-07 10:00:00 QZNW.TO 50 CAD 10.00 504.95 4.95
BUYSELL 2025-04-14 10:00:00 QZNW.TO -50 CAD 12.00 595.05 4.95
BUYSELL 2025-02-03 10:00:00 QZLS.TO 10 CAD 50.00 500.00 0
BUYSELL 2025-05-05 10:00:00 QZLS.TO -10 CAD 40.00 400.00 0
BUYSELL 2025-05-12 10:00:00 QZLS.TO 10 CAD 41.00 410.00 0
BUYSELL 2025-06-02 10:00:00 QZRX251219C00030000.US -2 USD 1.50 298.70 1.30
BUYSELL 2025-08-04 10:00:00 QZRX251219C00030000.US 2 USD 0.50 101.30 1.30
BUYSELL 2025-07-01 10:00:00 QZSH.US -10 USD 30.00 299.00 1.00
BUYSELL 2025-07-08 10:00:00 QZSH.US -10 USD 31.00 309.00 1.00
BUYSELL 2025-09-02 10:00:00 QZSH.US 20 USD 25.00 501.00 1.00
BUYSELL 2025-08-11 10:00:00 F:QZM26.US 1 USD 300 30000 0 x100
BUYSELL 2025-09-03 10:00:00 F:QZM26.US -1 USD 310 31000 0 x100
"""

TFSA_TT = """\
BUYSELL 2025-01-06 10:00:00 QZAA.TO 10 CAD 10.00 100.00 0
BUYSELL 2025-06-06 10:00:00 QZAA.TO -10 CAD 12.00 120.00 0
"""


def usd_cad_rate(day: datetime.date) -> Decimal:
    """The planted USD/CAD rate of a day: 1.3000 plus 0.0001 a day of
    the year, so each trade's day has its own."""
    return Decimal("1.3000") + Decimal(day.timetuple().tm_yday) * Decimal("0.0001")


def rates_text() -> str:
    day, out = datetime.date(2024, 12, 20), []
    while day <= datetime.date(2026, 1, 10):
        out.append(f"{day.isoformat()} 12:00:00 USD CAD {usd_cad_rate(day):.4f} boc\n")
        day += datetime.timedelta(days=1)
    return "".join(out)


class Project:
    """A project folder, its HOME (synthetic, with the planted rates) and
    the CLI run on it."""

    def __init__(self, files=None, toml=TOML, planted=True):
        base = Path(private_dir())
        self.root = base / "p"
        self.home = str(base / "home")
        self.env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                        **_hermetic.home_env(self.home))
        self.env["PYTHONPATH"] = os.pathsep.join(
            [str(_paths.SRC)] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH")
                                 else []))
        self.root.mkdir(parents=True)
        (self.root / "taxjson.toml").write_text(toml, encoding="utf-8")
        (self.root / "ticker.map").write_text("", encoding="utf-8")
        for acct, text in (files or {"margin": MARGIN_TT, "tfsa": TFSA_TT}).items():
            (self.root / "inputs" / acct).mkdir(parents=True)
            (self.root / "inputs" / acct / "trades.tt").write_text(text, encoding="utf-8")
        if planted:
            plant(self.root / "work" / "to_base.csv", rates_text(), self.home)

    def tjs(self, *args, **extra):
        argv = python_module_argv("taxjson.bin.taxjson_run") + ["-C", str(self.root)]
        env = dict(self.env, **extra)
        return subprocess.run(argv + list(args), capture_output=True, text=True, env=env,
                              stdin=subprocess.DEVNULL, timeout=600)

    def run(self):
        """`tjs run`; an AssertionError with its output when it fails."""
        r = self.tjs("run", "--no-input")
        if r.returncode != 0:
            raise AssertionError(r.stdout[-3000:] + r.stderr[-3000:])
        return r

    def json(self, *args):
        """`tjs ARGS --json`'s document (exit 0, else an AssertionError)."""
        r = self.tjs(*args, "--json")
        if r.returncode != 0:
            raise AssertionError(f"tjs {' '.join(args)} --json: exit {r.returncode}\n"
                                 + r.stderr[-3000:])
        return json.loads(r.stdout)


def cents(x) -> Decimal:
    """A JSON money figure as an exact Decimal at the cent."""
    return Decimal(str(x)).quantize(Decimal("0.01"))
