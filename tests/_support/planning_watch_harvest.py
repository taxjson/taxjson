"""Shared test helpers: a project with a margin buy 60 days back, run, and `tjs watch --harvest` against a given harvest document, and such a document.

Shared from: test_fix_1029_r7b_harvest
"""
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path


def _watch_project(case, tmp):
    """A Canadian project in TMP with a margin buy 60 days back, run;
    CASE (a TestCase) asserts the run passed."""
    import subprocess
    import sys
    d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731
    root = Path(tmp)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = %d\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n' % date.today().year)
    (root / "inputs" / "margin" / "trades.tt").write_text(
        f"BUYSELL {d(60)} 10:00:00 ZZA.TO 10 CAD 30 300 0\n")
    r = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
         str(root), "run", "--no-input"], capture_output=True,
        text=True)
    case.assertEqual(r.returncode, 0, r.stderr)
    return root


def _watch_harvest(root, hdoc):
    """`tjs watch --harvest --json` of ROOT, the harvest answering
    HDOC: (its document, stderr)."""
    import argparse
    import subprocess
    from unittest import mock
    import taxjson.pipeline.dispatch as D
    from taxjson.cli import plan as cli_plan
    real = D.run_cmd

    def fake(cmd, **kw):
        if "harvest" in cmd:
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps(hdoc), "")
        return real(cmd, **kw)

    args = argparse.Namespace(dir=str(root), json=True, harvest=True,
                              no_ibkr=True, threshold=0.0, state=None,
                              exit_code=False)
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(D, "run_cmd", fake), \
            redirect_stdout(out), redirect_stderr(err):
        cli_plan.cmd_watch(args)
    return json.loads(out.getvalue()), err.getvalue()


def _harvest_doc(now, omitted=()):
    """A harvest document: the total NOW, leaving out OMITTED."""
    # The total values ZZB.TO unless it is left out: TJ-1040 reads
    # the rows for what a baseline valued.
    return {"rows": ([] if "ZZB.TO" in omitted else
                     [{"account": "margin", "symbol": "ZZB.TO"}]),
            "totals": {"harvestable": {"now": now}},
            "omitted": [{"account": "margin", "symbol": s,
                         "reason": "no USD->CAD rate"}
                        for s in omitted]}
