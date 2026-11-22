"""Shared test helpers: a stock and a call, their rows, a denied loss's book, a written call bought back and an RRSP purchase of it, and the edge-case analysis of a Canadian project of books.

Shared from: test_fix_edge_cases_1026
"""
import contextlib
import io
import json
import tempfile
from pathlib import Path

from taxjson.bin.taxjson_split_gains import split_for_account
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.edge_cases import analyze, render_text
from taxjson.lib.pipeline import GainsRequest, run_gains


STOCK = "ZZY.US"
CALL = "ZZZ280121C00010000.US"


def _t(acct, day, settle, sym, qty, net):
    return TaxTransaction(account=acct, action="BUYSELL", date=day,
                          date_settle=settle, time="10:00:00", symbol=sym,
                          quantity=qty, net_amount=net, currency="CAD",
                          id=f"{acct}-{day}-{sym}-{qty}")


# A 250.00 loss on 2025-11-17 (settled 11-18), denied: the other 5 units
# are held on day 30. 5 units and 250.00 deferred at the end of 2025.
DENIED = [_t("margin", "2025-11-03", "2025-11-04", STOCK, 10, -1000),
          _t("margin", "2025-11-17", "2025-11-18", STOCK, -5, 250)]


def _edge(books, sheltered=(), *, tax_date="settle", crypto=(),
          settings=None, **req):
    """analyze() and the --details text of a project whose taxable
    accounts are `books` ({account: rows}) and sheltered account rrsp
    `sheltered`; more than one taxable account is the blended pass split
    per account (taxjson-split-gains), as the run does."""
    settings = dict({"year": 2025, "country": "canada",
                     "tax_date": tax_date}, **(settings or {}))
    accounts = {n: dict({"type": "taxable"},
                        **({"crypto": True} if n in crypto else {}))
                for n in books}
    if sheltered:
        accounts["rrsp"] = {"type": "sheltered"}
    taxable = [t for rows in books.values() for t in rows]
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        doc = run_gains(taxable, list(sheltered),
                        req=GainsRequest(country="canada", year=2025,
                                         taxable=True, tax_date=tax_date,
                                         **req))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        bases = dict(books, **({"rrsp": list(sheltered)} if sheltered
                               else {}))
        for n, rows in bases.items():
            (work / f"{n}_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in rows]}))
        for n, rows in books.items():
            out = doc if len(books) == 1 else split_for_account(
                doc, n, [t.to_dict() for t in rows])
            (work / f"{n}_gains_wash.json").write_text(json.dumps(out))
        cfg = {"settings": settings, "accounts": accounts}
        if crypto:
            cfg["settings"]["local_timezone"] = "America/Toronto"
        edge = analyze(root, cfg)
        text = "\n".join(render_text(edge, details=True, width_=200))
    return doc, edge, text


# A written call bought back at a loss on 2025-12-15 (settled 12-16),
# 2 contracts bought: one closes the short, one opens a long. rrsp
# buys the same series on 2026-01-02. On day 30 (2026-01-15) both
# longs are held.
_CALL_BOOK = [_t("margin", "2025-12-10", "2025-12-11", CALL, -1, 100),
              _t("margin", "2025-12-15", "2025-12-16", CALL, 2, -300)]
_CALL_RRSP = [_t("rrsp", "2026-01-02", "2026-01-05", CALL, 1, -150)]
