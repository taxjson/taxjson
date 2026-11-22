"""Shared test helpers: rows for the planning tools.

Shared from: test_fixl_planning
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from _support.planning_tools import REPO_ROOT


RADAR = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar"]


def _run_radar(taxable, as_of, sheltered=None, country="canada",
               json_out=True, raw_taxable=None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        t = tmp / "margin_base.json"
        t.write_text(raw_taxable if raw_taxable is not None
                     else json.dumps({"transactions": taxable}))
        cmd = RADAR + ["--country", country, "--taxable", str(t),
                       "--date", as_of, "--all"]
        if json_out:
            cmd.append("--json")
        if sheltered is not None:
            s = tmp / "sheltered_base.json"
            s.write_text(json.dumps({"transactions": sheltered}))
            cmd += ["--sheltered", str(s)]
        return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                              text=True)


def _rows(taxable, as_of, sheltered=None, country="canada"):
    r = _run_radar(taxable, as_of, sheltered, country)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    return {row["ticker"]: row for sec in doc["sections"]
            for row in sec["rows"]}
