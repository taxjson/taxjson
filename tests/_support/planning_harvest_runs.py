"""Shared test helpers: harvest runs and their gains files.

Shared from: test_fix_a2_planning_harvest
"""
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from taxjson.bin.taxjson_harvest import main as harvest_main


TODAY = date.today()


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _write(path: Path, doc) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _gains(inventory, year=None):
    return {"summary": {"year": year or TODAY.year}, "transactions": [],
            "inventory": inventory}


def _run(argv, fetchers=None, option_fetchers=None, env=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env or {}), \
            redirect_stdout(out), redirect_stderr(err):
        rc = harvest_main(argv, fetchers=fetchers,
                          option_fetchers=option_fetchers)
    return rc, out.getvalue(), err.getvalue()


def _json_rows(out):
    return json.loads(out)
