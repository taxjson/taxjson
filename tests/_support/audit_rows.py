"""Shared test helpers: taxjson-audit in process and its rows.

Shared from: test_fix_1029_r4_audit
"""
import contextlib
import io
import json

from taxjson.bin import taxjson_audit as AUDIT
from taxjson.model.transaction import TaxTransaction


def _audit(argv):
    """taxjson-audit in process: (rc, the --json document, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    rc = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = AUDIT.main(list(argv) + ["--json"])
        except SystemExit as e:
            rc = e.code
    text = out.getvalue()
    return rc, (json.loads(text) if text.strip() else {}), err.getvalue()


def _row(**kw) -> dict:
    """One parsed row with the id the parsers give it (its content)."""
    kw.setdefault("account", "margin")
    kw.setdefault("date_settle", kw["date"])
    return TaxTransaction(**kw).to_dict()
