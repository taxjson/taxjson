"""Shared test helpers: buy-check and sell-check against a synthetic radar.

Shared from: test_fix_a2_planning
"""
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from _run_patch import patch_run
import _paths


REPO_ROOT = _paths.REPO
RADAR = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar"]


def _row(date_, symbol, qty, net, settle=None, account="margin",
         action="BUYSELL", rid=None, currency="CAD", **kw):
    r = dict(action=action, date=date_, date_settle=settle or date_,
             time="10:00:00", symbol=symbol, quantity=qty,
             net_amount=net, currency=currency, account=account,
             price=abs(net / qty) if qty else 0.0, **kw)
    if rid:
        r["id"] = rid
    return r


def _radar_doc(taxable, as_of, sheltered=None, country="canada",
               gains=None, extra=()):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        t = tmp / "margin_base.json"
        t.write_text(json.dumps({"transactions": taxable}))
        cmd = RADAR + ["--country", country, "--taxable", str(t),
                       "--date", as_of, "--all", "--json", *extra]
        if sheltered is not None:
            s = tmp / "sheltered_base.json"
            s.write_text(json.dumps({"transactions": sheltered}))
            cmd += ["--sheltered", str(s)]
        for i, g in enumerate(gains or []):
            gp = tmp / f"g{i}_gains_wash.json"
            gp.write_text(json.dumps(g))
            cmd += ["--gains", str(gp)]
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                           text=True)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)


def _rows(*a, **kw):
    doc = _radar_doc(*a, **kw)
    return {row["ticker"]: row for sec in doc["sections"]
            for row in sec["rows"]}


def _check(cmd_name, radar, symbols, last_loss=None, usa=False,
           as_json=False):
    """Drive buy-check / sell-check against a synthetic radar."""
    from argparse import Namespace
    from contextlib import redirect_stdout
    from taxjson.cli import plan
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_underlying

    def canon(t):
        t = t.strip().upper()
        if is_option_symbol(t):
            return parse_option_underlying(t) or t
        return t
    ctx = (radar, canon, last_loss or {})
    buf = io.StringIO()
    code = 0
    with patch_run("_wash_class_context", return_value=ctx), \
            patch_run("_radar_country_is_usa", return_value=usa):
        try:
            with redirect_stdout(buf):
                getattr(plan, cmd_name)(Namespace(dir=".", symbol=list(symbols),
                                               json=as_json))
        except SystemExit as e:
            code = e.code or 0
    out = buf.getvalue()
    return (json.loads(out) if as_json else out), code
