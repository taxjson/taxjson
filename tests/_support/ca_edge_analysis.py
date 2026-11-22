"""Shared test helpers: edge cases of the planning commands.

Shared from: test_fix_1029_r7d_edgeplan
"""
import contextlib
import copy
import io
import json
import tempfile
from pathlib import Path

from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.edge_cases import analyze, render_text
from taxjson.lib.option_boundary import reported_premiums
from taxjson.lib.pipeline import GainsRequest, run_gains
from taxjson.bin.taxjson_split_gains import split_for_account


def _t(acct, action, day, sym, qty, net, settle=None, price=None, **kw):
    return TaxTransaction(
        account=acct, action=action, date=day, date_settle=settle or day,
        time=kw.pop("time", "10:00:00"), symbol=sym, quantity=qty,
        net_amount=net, currency="CAD",
        price=(abs(net / qty) if price is None and qty else price or 0.0),
        id=kw.pop("id", f"{acct}-{action}-{day}-{sym}-{qty}"), **kw)


def _edge(books, sheltered=None, *, year=2025, timing="close"):
    """analyze() and the --details text of a Canadian project whose
    taxable accounts are `books` and sheltered accounts `sheltered`."""
    sheltered = sheltered or {}
    taxable = [t for rows in books.values() for t in rows]
    shel = [t for rows in sheltered.values() for t in rows]
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        doc = run_gains(copy.deepcopy(taxable), copy.deepcopy(shel),
                        req=GainsRequest(country="canada", year=year,
                                         taxable=True, tax_date="settle",
                                         option_premium_timing=timing,
                                         option_grant_since=year))
    accounts = {n: {"type": "taxable"} for n in books}
    accounts.update({n: {"type": "sheltered"} for n in sheltered})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        for n, rows in dict(books, **sheltered).items():
            (work / f"{n}_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in rows]}))
        for n, rows in books.items():
            out = doc if len(books) == 1 else split_for_account(
                doc, n, [t.to_dict() for t in rows])
            (work / f"{n}_gains_wash.json").write_text(json.dumps(out))
        cfg = {"settings": {"year": year, "country": "canada",
                            "tax_date": "settle",
                            "option_premium_timing": timing},
               "accounts": accounts}
        with contextlib.redirect_stderr(io.StringIO()):
            edge = analyze(root, cfg)
        text = "\n".join(render_text(edge, details=True, width_=200))
    return doc, edge, text


_PUT = "SYN250117P00001000.TO"


def _b(day, qty, net, settle=None):
    return TaxTransaction(
        action="BUYSELL", currency="CAD", account="margin",
        date=day, time="10:00:00",
        date_settle=settle or day, symbol=_PUT, quantity=qty,
        price=abs(net) / 100, net_amount=net)


def _assigned_2025():
    return [
        TaxTransaction(action="ASSIGN", currency="CAD", account="margin",
                       date="2025-01-17", time="10:00:00",
                       date_settle="2025-01-17",
                       symbol=_PUT, quantity=1, price=0, net_amount=0.0),
        TaxTransaction(action="ASSIGN", currency="CAD", account="margin",
                       date="2025-01-17", time="10:00:00",
                       date_settle="2025-01-17",
                       symbol="SYN.TO", quantity=100, price=1,
                       net_amount=-100.0)]


def _engine_lock(rows):
    """The return as first filed: the engine's grant-timing dispositions."""
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        res = CanadaTaxRules().compute_gains(
            copy.deepcopy(rows), option_premium_timing="grant")
    return reported_premiums({"dispositions": [
        {"symbol": d.get("symbol"), "date": d.get("date"),
         "date_settle": d.get("date_settle"), "gain": d.get("gain")}
        for d in res.get("transactions", [])]})
