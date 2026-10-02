"""Re-audit-2 test pins for the T1135 cost walk and report and for
split-gains (fix lists tests-pins-05: A2-0172, A2-0183, A2-0184,
A2-0185, A2-0186, A2-0883, A2-0914, A2-0915, A2-0916, A2-0923,
A2-1537, A2-1552, A2-1553, A2-1589). Each test fails under the mutant
its finding names. All data is synthetic."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO = Path(__file__).resolve().parent.parent


def _r(action, date, symbol, qty, net, settle=None, time="10:00:00",
       **kw):
    """A base-book row; net is signed as the parsers write it (a buy
    negative, a sale positive)."""
    row = {"action": action, "date": date, "date_settle": settle or date,
           "time": time, "symbol": symbol, "quantity": qty,
           "net_amount": net, "currency": "CAD", "account": "margin",
           "id": kw.pop("id", f"{action}-{date}-{symbol}-{qty}-{time}")}
    row.update(kw)
    return row


def _t(i, d, sym, q, p, account="margin", time="10:00:00"):
    amt = abs(q * p)
    return dict(id=i, date=d, date_settle=d, time=time, action="BUYSELL",
                symbol=sym, quantity=q, price=p, gross_amount=amt,
                net_amount=-amt if q > 0 else amt, commission=0.0,
                fee=0.0, currency="CAD", account=account)


def _walk(rows, overrides=None, **kw):
    from taxjson.bin.taxjson_t1135 import walk_costs
    with contextlib.redirect_stderr(io.StringIO()):
        return walk_costs(rows, 2025, dict(overrides or {}),
                          today="2026-06-01", **kw)


def _report(base_rows, gains_doc_by_name=None, year=2025, **kw):
    from taxjson.bin.taxjson_t1135 import build_report
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "margin_base.json"
        base.write_text(json.dumps({"transactions": base_rows}))
        gains = []
        for name, doc in (gains_doc_by_name or {}).items():
            g = Path(td) / name
            g.write_text(json.dumps(doc))
            gains.append(g)
        kw.setdefault("today", "2026-06-01")
        with contextlib.redirect_stderr(io.StringIO()):
            return build_report([base], gains, year, {}, "CAD", **kw)


def _engine_gains(base: Path, year: int, out: Path, *extra) -> dict:
    g = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_gains", "--country",
         "canada", "--year", str(year), "--taxable", *extra, str(base)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env=dict(os.environ, PYTHONPATH=str(REPO / "src")))
    assert g.returncode == 0, g.stderr[-2000:]
    out.write_text(g.stdout)
    return json.loads(g.stdout)


# ============================================== --year-wash-only deferral
class TestYearOnlyDeferralSumsAccounts(unittest.TestCase):
    """A2-1552: each account's split gains file carries its share of a
    blended pool's deferral, so the excluded amount is their sum."""

    @rule("CA-RPT-12")
    def test_two_accounts_shares_are_summed(self):
        base = [_r("BUYSELL", "2024-03-11", "XYZ.US", 900, -63000.0,
                   account="b"),
                _r("BUYSELL", "2024-03-12", "XYZ.US", 100, -7000.0,
                   account="a")]
        inv = lambda acct, q, c, d: {"inventory": [
            {"account": acct, "symbol": "XYZ.US", "qty": q,
             "total_cost": c, "deferred_wash": d}], "transactions": []}
        rep = _report(base, {"a_gains_wash.json": inv("a", 100, 12000, 5000),
                             "b_gains_wash.json": inv("b", 900, 108000,
                                                      45000)},
                      full_history=False)
        self.assertEqual(rep["deferred_wash_not_in_cost"],
                         {"XYZ.US": 50000.0})

    def test_same_account_twice_counts_once(self):
        from taxjson.bin.taxjson_t1135 import _deferred_wash
        with tempfile.TemporaryDirectory() as td:
            doc = {"inventory": [{"account": "a", "symbol": "XYZ.US",
                                  "deferred_wash": 5000.0}]}
            ps = []
            for n in ("a_gains.json", "a_gains_wash.json"):
                p = Path(td) / n
                p.write_text(json.dumps(doc))
                ps.append(p)
            self.assertEqual(_deferred_wash(ps, {}), {"XYZ.US": 5000.0})


if __name__ == "__main__":
    unittest.main()
