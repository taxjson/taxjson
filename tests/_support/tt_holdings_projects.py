"""Shared test helpers: a .tt book of shares and a call with dividends, a .tt project run on it, a project tips reads without a run, and a margin and TFSA project holding one listing each.

Shared from: test_fix_l_runcore_b
"""
import json
from pathlib import Path

from _support.qt_margin_views import _CONFIG, _QT_HEADER, _project, _run_cli


def _scan_project(tmp, holdings, raw_rows=(), ticker_map=None):
    """A project tips can read without a pipeline run: the per-listing
    holdings report and the raw book are all it needs."""
    root = Path(tmp) / "p"
    (root / "reports").mkdir(parents=True)
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(_CONFIG)
    body = "".join(f'[[holding]]\nsymbol = "{s}"\nquantity = {q}\n\n'
                   for s, q in holdings)
    (root / "reports" / "margin_holdings.toml").write_text(body)
    (root / "work" / "margin_raw.json").write_text(
        json.dumps({"transactions": list(raw_rows)}))
    if ticker_map is not None:
        (root / "ticker.map").write_text(ticker_map)
    return root


_RICH_TT = (
    "BUYSELL 2025-01-10 09:30:00 XEI.TO 100 CAD 10.0 -1000.0 0.0\n"
    "BUYSELL 2025-02-02 09:30:00 ABC261218C00050000.TO -1 CAD 1.6 "
    "160.004 0.0\n"
    "BUYSELL 2025-04-01 09:30:00 XEI.TO -100 CAD 11.0 1100.004 0.0\n"
    "DIVIDEND 2025-04-15 09:30:00 XEI.TO 0 CAD 0 30.00\n"
    "DIVIDEND_IN_LIEU 2025-05-01 09:30:00 XEI.TO 0 CAD 0 10.00\n")


def _tt_project(tmp, tt=_RICH_TT, config=_CONFIG, run=True):
    root = _project(tmp, config=config, csv=_QT_HEADER)
    (root / "inputs" / "margin" / "questrade_2025.csv").unlink()
    (root / "inputs" / "margin" / "rows.tt").write_text(tt)
    if run:
        r = _run_cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stderr
    return root


_TWO_ACCT_CFG = _CONFIG + '\n[accounts.tfsa]\ntype = "sheltered"\n'


def _held_project(tmp, holdings_line=""):
    """margin holds 100 XEI.TO, tfsa holds 10 XIC.TO."""
    cfg = _TWO_ACCT_CFG.replace('[accounts.margin]\ntype = "taxable"\n',
                                '[accounts.margin]\ntype = "taxable"\n'
                                + holdings_line)
    root = _tt_project(tmp, tt="BUYSELL 2025-01-10 09:30:00 XEI.TO 100 "
                       "CAD 10.0 -1000.0 0.0\n", config=cfg, run=False)
    (root / "inputs" / "tfsa").mkdir()
    (root / "inputs" / "tfsa" / "rows.tt").write_text(
        "BUYSELL 2025-01-10 09:30:00 XIC.TO 10 CAD 10.0 -100.0 0.0\n")
    r = _run_cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr
    (root / "hold").mkdir(exist_ok=True)
    return root
