"""Shared test helpers: a harvest project, its runs and a fake fetcher.

Shared from: test_harvest
"""
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

from taxjson.bin.taxjson_harvest import main as harvest_main


# One "today" for the whole module: the fixtures below are dated from it
# at import, and the harvest code reads today again later — a run that
# crossed midnight saw two different days and failed (gate 2026-10-05).
_TODAY = date.today()


# margin: AAA.TO underwater (cost 1,240 vs value 1,085), BBB.US ahead,
# one OCC option row (must be skipped — unpriceable by the chain).
# last_acq_date is dynamic (TX_ADD renders signed days from today).
_AAA_TX_ADD = (_TODAY - timedelta(days=100)).isoformat()
_BBB_TX_ADD = (_TODAY - timedelta(days=400)).isoformat()
GAINS = {
    "summary": {"year": 2026},
    "transactions": [],
    "inventory": [
        {"symbol": "AAA.TO", "qty": 100, "total_cost": 1240.0,
         "position_start_date": "2026-01-15",
         "last_acq_date": _AAA_TX_ADD},
        {"symbol": "BBB.US", "qty": 50, "total_cost": 4005.0,
         "position_start_date": "2024-03-01",
         "last_acq_date": _BBB_TX_ADD},
        {"symbol": "AAA.TO260116C00010000", "qty": 2, "total_cost": 300.0},
        {"symbol": "CLOSED.TO", "qty": 0, "total_cost": 0.0},
    ],
}

PRICES = {"AAA.TO": 10.85, "BBB.US": 95.20, "ETN.US": 380.00}

USD_CAD = 1.25


def _fake_fetcher(remaining):
    return {s: (PRICES[s], "fake") for s in remaining if s in PRICES}


def _radar_doc(clears_at):
    return {"sections": [{"title": "LOCKED", "rows": [{
        "ticker": "AAA.TO", "category": "LOCKED",
        "advisory": "LOCKED: Recent buy ...", "clears_at": clears_at}]}]}


def _project(td, *, wash=True, radar_clears=None, rates=True):
    root = Path(td)
    name = "margin_gains_wash.json" if wash else "margin_gains.json"
    (root / name).write_text(json.dumps(GAINS))
    if rates:
        (root / "to_base.csv").write_text(
            f"{_TODAY.isoformat()} 12:00:00 USD CAD {USD_CAD}\n")
    radar = None
    if radar_clears is not None:
        radar = root / "wash_radar_margin.json"
        radar.write_text(json.dumps(_radar_doc(radar_clears)))
    return root / name, radar


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = harvest_main(argv, fetchers=[_fake_fetcher])
    return rc, out.getvalue(), err.getvalue()
