"""Shared test helpers: rows and saved books for the filing reports.

Shared from: test_fix_a2_filrep_a
"""
import contextlib
import io
import json
from pathlib import Path

from taxjson.model.transaction import TaxTransaction


ACCT = "55500001"  # pii-ok (synthetic)


def _row(action, date, sym, qty=0.0, net=0.0, *, settle=None,
         acct=ACCT, currency="CAD", price=None, **kw):
    if price is None:
        price = abs(net / qty) if qty else 0.0
    return TaxTransaction(action=action, date=date, time="10:00:00",
                          date_settle=settle or date, symbol=sym,
                          quantity=float(qty), price=float(price),
                          net_amount=float(net), account=acct,
                          currency=currency, **kw).to_dict()


def _quiet(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    rc = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fn(*a, **kw)
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class _Books:
    """base.json (the engine input) + the pipeline's saved gains file,
    written by the real run_gains with the given request."""

    def __init__(self, tmp: Path, rows, year, *, phantoms=None, **req):
        from taxjson.lib.tx_load import load_transactions
        from taxjson.lib.pipeline import GainsRequest, run_gains
        self.tmp = tmp
        self.base = tmp / "base.json"
        self.base.write_text(json.dumps({"transactions": rows}),
                             encoding="utf-8")
        self.phantoms = None
        if phantoms is not None:
            self.phantoms = tmp / "missing_history.json"
            self.phantoms.write_text(json.dumps(phantoms), encoding="utf-8")
            from _mh import from_json
            self.phantoms = from_json(self.phantoms, rows)
        greq = GainsRequest(year=year, taxable=True,
                            incomplete_history=self.phantoms, **req)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = run_gains(load_transactions(self.base), req=greq)
        self.results = res
        self.gains = tmp / "gains.json"
        self.gains.write_text(json.dumps(res, default=str),
                              encoding="utf-8")

    def audit(self, *extra):
        from taxjson.bin.taxjson_audit import main
        args = ["--base", str(self.base), "--check", str(self.gains),
                "--json", "--no-color"] + list(extra)
        if self.phantoms is not None:
            args += ["--incomplete-history", str(self.phantoms)]
        rc, out, err = _quiet(main, args)
        doc = json.loads(out) if out.strip() else {}
        return rc, doc, err
