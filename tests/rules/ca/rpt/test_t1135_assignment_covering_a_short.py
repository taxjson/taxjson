"""Audit 2026-10-24, group "reports-state": the recorded wash inputs
and a new account (D01), the T1135 cost walk and an assignment that
covers a short (D02), a damaged work/cross_listings.state read as "no
pending journals" (D05), an incomplete work/<acct>_report.json served
by `sum` (D07), and `migrate` sharing a tobase.map that is not UTF-8
text (A42). One test per finding, each the audit's probe.

Every fixture is synthetic: made-up tickers (ZZQ.US), account names
(a, b, c, margin, tfsa), no ids, no amounts of 1,000 or more with cents.

Formerly: test_fix_audit_1024_reports
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule
from taxjson.rules import ids as R


def _row(d, sym, q, amount, action="BUYSELL", code=None):
    """`code`: the broker's open/close code of the row (the delivery of an
    assignment's shares is coded for what it does to the share position:
    C;O past a short, O with none, C within one; a purchase coded C alone
    past the short would be the cover of a short sold before the files,
    reported by hand: CA-ACB-11)."""
    return dict(date=d, date_settle=d, action=action, symbol=sym,
                quantity=q, net_amount=amount, currency="CAD",
                account="margin",
                open_close=code or ("O" if d < "2025-01-03" else "C"))


_PUT = "ZZQ250117P00010000.US"


def _t1135_vs_engine(rows, with_report=False):
    """(the T1135 property row, the engine's inventory) for `rows`, and
    the whole report `with_report`."""
    from taxjson.bin.taxjson_t1135 import build_report
    from taxjson.engine.ca.taxrules import CanadaTaxRules
    from taxjson.model.transaction import TaxTransaction
    with contextlib.redirect_stderr(io.StringIO()):
        engine = CanadaTaxRules().compute_gains(
            [TaxTransaction(**r) for r in rows], detect_wash_sales=False)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        base, gains = root / "base.json", root / "gains.json"
        base.write_text(json.dumps({"transactions": rows, "metadata": {
            "target_currency": "CAD"}}))
        gains.write_text(json.dumps(engine))
        with contextlib.redirect_stderr(io.StringIO()):
            report = build_report([base], [gains], 2025, {}, "CAD")
    props = {p["symbol"]: p for p in report["properties"]}
    if with_report:
        return props, engine["inventory"], report
    return props, engine["inventory"]


class TestD02AssignmentCoveringAShort(unittest.TestCase):
    """Formerly: test_fix_audit_1024_reports.TestD02AssignmentCoveringAShort"""

    @rule(R.CA_RPT_12, R.CA_RPT_19)
    def test_d02_premium_share_of_the_kept_shares_stays_in_their_cost(self):
        """D02: T1135 loses assignment premium allocated to shares
        remaining after covering a short. Short 50 shares for 500,
        write a put for 100, take assignment buying 100 shares for
        1000: the engine leaves 50 shares at cost 450 (the premium
        apportioned by quantity between the cover and the shares
        kept); the T1135 walk folded the premium into the short pool,
        the cover drained it, and the kept shares opened at 500. The
        walk now stages the premium for the delivery leg and
        apportions it as the engine does: both say 450."""
        rows = [_row("2025-01-01", "ZZQ.US", -50, 500),
                _row("2025-01-02", _PUT, -1, 100),
                _row("2025-01-03", _PUT, 1, 0, "ASSIGN"),
                _row("2025-01-03", "ZZQ.US", 100, 1000, code="C;O")]
        props, inventory = _t1135_vs_engine(rows)
        (held,) = [i for i in inventory if i["symbol"] == "ZZQ.US"]
        self.assertEqual(held["qty"], 50)
        self.assertAlmostEqual(held["total_cost"], 450.0, places=2)
        self.assertAlmostEqual(props["ZZQ.US"]["year_end_cost"], 450.0,
                               places=2)
        self.assertAlmostEqual(props["ZZQ.US"]["max_cost"], 450.0, places=2)
        # No short to cover: the whole premium lowers the shares' cost,
        # as before the change (the staged amount lands with the buy).
        rows = [_row("2025-01-02", _PUT, -1, 100),
                _row("2025-01-03", _PUT, 1, 0, "ASSIGN"),
                _row("2025-01-03", "ZZQ.US", 100, 1000, code="O")]
        props, inventory = _t1135_vs_engine(rows)
        (held,) = [i for i in inventory if i["symbol"] == "ZZQ.US"]
        self.assertAlmostEqual(held["total_cost"], 900.0, places=2)
        self.assertAlmostEqual(props["ZZQ.US"]["year_end_cost"], 900.0,
                               places=2)
        # The delivery only covers part of the short: no shares are
        # held, so no cost amount (a short is not property held; the
        # premium's whole share went to the cover's gain). Held only
        # short in the year, it is named, not a row (CA-RPT-19).
        rows = [_row("2025-01-01", "ZZQ.US", -200, 2000),
                _row("2025-01-02", _PUT, -1, 100),
                _row("2025-01-03", _PUT, 1, 0, "ASSIGN"),
                _row("2025-01-03", "ZZQ.US", 100, 1000)]
        props, inventory, report = _t1135_vs_engine(rows, with_report=True)
        self.assertEqual([i["qty"] for i in inventory
                          if i["symbol"] == "ZZQ.US"], [-100])
        self.assertNotIn("ZZQ.US", props)
        self.assertEqual(report["short_only_symbols"], ["ZZQ.US"])


if __name__ == "__main__":
    unittest.main()
