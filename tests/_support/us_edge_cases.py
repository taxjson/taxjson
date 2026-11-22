"""Shared test helpers: US rows and the edge-cases run.

Shared from: test_fix_us_year_end_1027
"""
import contextlib
import copy
import io
import json
import tempfile
from pathlib import Path

from taxjson.bin.taxjson_split_gains import split_for_account
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.edge_cases import analyze, render_text
from taxjson.lib.pipeline import GainsRequest, run_gains


STOCK = "ZZQ.US"


def _t(acct, day, qty, net, sym=STOCK, settle=None):
    return TaxTransaction(account=acct, action="BUYSELL", date=day,
                          date_settle=settle or day, time="10:00:00",
                          symbol=sym, quantity=qty, net_amount=net,
                          currency="USD", id=f"{acct}-{day}-{sym}-{qty}")


def _gains(rows, tax_date="trade", **req):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return run_gains(copy.deepcopy(rows), [],
                         req=GainsRequest(country="usa", year=2025,
                                          taxable=True, tax_date=tax_date,
                                          **req))


def _edge(books, *, tax_date="trade", **req):
    """The gains run, analyze() and the --details text of a US project
    whose taxable accounts are `books` ({account: rows}); more than one
    account is the blended pass split per account (taxjson-split-gains),
    as the run does."""
    rows = [t for b in books.values() for t in b]
    doc = _gains(rows, tax_date, **req)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        for n, b in books.items():
            base = [t.to_dict() for t in b]
            (work / f"{n}_base.json").write_text(
                json.dumps({"transactions": base}))
            out = doc if len(books) == 1 else split_for_account(doc, n,
                                                                 base)
            (work / f"{n}_gains_wash.json").write_text(json.dumps(out))
        cfg = {"settings": {"year": 2025, "country": "usa",
                            "tax_date": tax_date},
               "accounts": {n: {"type": "taxable"} for n in books}}
        edge = analyze(root, cfg)
        text = "\n".join(render_text(edge, details=True, width_=200))
    return doc, edge, text
