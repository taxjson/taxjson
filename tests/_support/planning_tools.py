"""Shared test helpers: the planning tools.

Shared from: test_fix_planning
"""
import json
import subprocess
import sys
from pathlib import Path
import _paths


REPO_ROOT = _paths.REPO


def _row(date_, symbol, qty, net, settle=None, account="margin",
         action="BUYSELL", rid=None, price=None, currency="CAD"):
    r = dict(action=action, date=date_, date_settle=settle or date_,
             time="10:00:00", symbol=symbol, quantity=qty,
             net_amount=net, currency=currency, account=account,
             price=price if price is not None else (abs(net / qty)
                                                    if qty else 0.0))
    if rid:
        r["id"] = rid
    return r


def _radar(tmp, taxable, as_of, sheltered=None, gains=None,
           phantoms=None):
    tmp = Path(tmp)
    t = tmp / "margin_base.json"
    t.write_text(json.dumps({"transactions": taxable}))
    cmd = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar", "--country", "canada",
           "--taxable", str(t), "--date", as_of, "--all", "--json"]
    if sheltered is not None:
        s = tmp / "sheltered_base.json"
        s.write_text(json.dumps({"transactions": sheltered}))
        cmd += ["--sheltered", str(s)]
    for i, g in enumerate(gains or []):
        gp = tmp / f"g{i}_gains_wash.json"
        gp.write_text(json.dumps(g))
        cmd += ["--gains", str(gp)]
    if phantoms is not None:
        pp = tmp / "missing_history.json"
        pp.write_text(json.dumps(phantoms))
        from _mh import from_json
        pp = from_json(pp, taxable)
        cmd += ["--incomplete-history", str(pp)]
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    return {row["ticker"]: row for sec in doc["sections"]
            for row in sec["rows"]}


def _gains(rows, year=2026, **summary):
    return {"summary": {"year": year, "tax_date_basis": "settle",
                        **summary},
            "metadata": {"account": "margin"},
            "transactions": rows}


def _disp(rid, date_, symbol, qty, raw_gain, cost=1.0, direction="LONG",
          is_option=False, account="margin", **kw):
    return dict(id=rid, date=date_, date_settle=date_, symbol=symbol,
                qty=qty, raw_gain=raw_gain, gain=raw_gain, cost=cost,
                direction=direction, is_option=is_option,
                account=account, **kw)


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price):
    gross = abs(qty) * price
    net = -gross if qty > 0 else gross
    return (f"{trade} 10:00:00 AM,{settle} 12:00:00 AM,{action},{sym},"
            f"{sym} DESC,{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,"
            f"55500001,Trades,Individual\n")  # pii-ok


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


def _config(year, accounts):
    t = (f'[settings]\nyear = {year}\ncountry = "canada"\n'
         f'base_currency = "CAD"\nsource_currencies = []\n'
         f'option_grant_timing_since = {year}\n')
    for n, ty in accounts:
        t += f'[accounts.{n}]\ntype = "{ty}"\n'
    return t
