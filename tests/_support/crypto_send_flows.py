"""Shared test helpers: a send's row, the Kraken and Coinbase headers, a USDC sidecar, daily rates and a project.

Shared from: test_fix_x_mut_crypto_sends
"""
import json
import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from taxjson.lib import base_rates as BR


def _s(exchange="coinbase", account="c", date="2025-08-24",
       time="09:41:52", symbol="SOL", quantity=-1.0, **kw):
    r = {"exchange": exchange, "account": account, "date": date,
         "time": time, "symbol": symbol, "quantity": quantity,
         "price": 0.0, "currency": "", "fee": 0.0, "kind": "Send",
         "ref": ""}
    r.update(kw)
    return r


def _daily(table, until="2025-12-31"):
    """A sparse {cur: {date: (rate, src)}} forward-filled to a row per
    day, as the real rates file is (its 7-day carry and the stage's
    5-day lookback, A2-0414): a sparse fixture row is the day's rate
    until the next one."""
    from datetime import date, timedelta
    out = {}
    for cur, days in table.items():
        keys = sorted(days)
        filled = {}
        for i, k in enumerate(keys):
            d = date.fromisoformat(k)
            end = (date.fromisoformat(keys[i + 1]) if i + 1 < len(keys)
                   else date.fromisoformat(until) + timedelta(days=1))
            while d < end:
                filled[d.isoformat()] = days[k]
                d += timedelta(days=1)
        out[cur] = filled
    return out


def _rates(table=None, base="CAD"):
    return BR.Rates(_daily(table if table is not None else
                           {"USD": {"2025-01-01": (1.35, "boc"),
                                    "2025-03-01": (1.45, "boc")}}), base)


KR_HEADER = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,"
             "fee,balance\n")


def _kr(rows):
    return KR_HEADER + "".join(
        f"L{i},{ref},{t},{typ},{sub},currency,{asset},spot,{amt},{fee},0\n"
        for i, (ref, t, typ, sub, asset, amt, fee) in enumerate(rows))


class _UTC(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"TAXJSON_LOCAL_TZ": "UTC"})
        p.start()
        self.addCleanup(p.stop)


CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")


def _flow(day, units, cad=0.0):
    return {"dt": datetime.strptime(day + " 12:00:00", "%Y-%m-%d %H:%M:%S"),
            "units": units, "cad": cad, "what": "test"}


def _project(tmp, *, country="canada", base="CAD", sidecar=None,
             decisions=None, rates=None):
    root = Path(tmp)
    (root / "work").mkdir(parents=True, exist_ok=True)
    for a in ("a", "b"):
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    (root / "work" / "a_kraken_transfers.json").write_text(json.dumps({
        "metadata": {"kind": "transfer_sidecar", "brokerage": "kraken"},
        "transactions": sidecar or []}))
    (root / "work" / "to_base.csv").write_text(
        rates or ("2025-01-01 12:00:00 USD CAD 1.40 boc\n"
                  "2025-01-31 12:00:00 USD CAD 1.40 boc\n"))
    if decisions is not None:
        (root / "inputs" / "a" / "sends.json").write_text(
            json.dumps({"sends": decisions}))
    settings = {"country": country}
    if base:
        settings["base_currency"] = base
    return root, {"settings": settings,
                  "accounts": {"a": {"crypto": True}, "b": {"crypto": True},
                               "c": {}}}


SIDE = [{"date": "2025-02-01", "time": "10:00:00", "symbol": "USDC",
         "quantity": -100, "description": "withdrawal"},
        {"date": "2025-02-02", "time": "10:00:00", "symbol": "SOL",
         "quantity": -2, "price": 150, "currency": "CAD",
         "description": "withdrawal"}]
USDC_ID = "kr-20250201T100000-USDC-100"
