"""Canada engine fixes from the 2026-09 engine audit (repros r04, r08,
r08b, r11 of that audit), each pinned by a test that failed before.

Formerly: test_ca_engine_audit_fixes"""
import io
import unittest
from contextlib import redirect_stderr

from taxjson.engine.by_country import get_tax_rules
from taxjson.model.transaction import TaxTransaction


def T(action="BUYSELL", date="", symbol="", quantity=0.0, net_amount=0.0,
      currency="USD", time="10:00:00", settle="", account="55500001", **kw):  # pii-ok (synthetic id)
    return TaxTransaction(action=action, date=date, symbol=symbol,
                          quantity=quantity, net_amount=net_amount,
                          currency=currency, time=time,
                          date_settle=settle or date, account=account, **kw)


def ca(book, **kw):
    err = io.StringIO()
    with redirect_stderr(err):
        r = get_tax_rules("canada").compute_gains(book, **kw)
    r["_stderr"] = err.getvalue()
    return r


def records(r):
    return [g for g in r["transactions"] if "gain" in g]


class TestMergerPerAccountRatio(unittest.TestCase):
    """corp_actions emits a rename-SPLIT per account at the ratio the
    broker delivered; one merger can carry two ratios in a blended book
    (audit r10). The symbol-wide pool must land on the shares actually
    delivered in total.

    Formerly: test_ca_engine_audit_fixes.TestMergerPerAccountRatio"""

    def _book(self):
        from taxjson.lib.corp_actions import CorporateAction, _emit_basis_carryover_rename
        rows = []
        for acct, disp, recv in (("55500001", 15, 15), ("55500002", 40, 41)):
            ev = CorporateAction(date="2025-07-18", time="00:00:01", action_type="merger",
                                 source_symbol="HHH.US", source_isin="", target_symbol="CCC.US",
                                 target_isin="", ratio_new=1.025, ratio_old=1.0, qty_disposed=disp,
                                 qty_received=recv, fmv=0.0, currency="USD", target_currency="USD",
                                 account=acct)
            for r in _emit_basis_carryover_rename(ev, {}, statute_note="s.85.1", cil_note="x"):
                rows.append(TaxTransaction(**r))
        self.assertEqual(sorted(r.quantity for r in rows), [1.0, 1.025])
        return ([T(date="2025-01-06", symbol="HHH.US", quantity=15, net_amount=1500.0, account="55500001"),  # pii-ok (synthetic id)
                 T(date="2025-01-06", symbol="HHH.US", quantity=40, net_amount=4000.0, account="55500002")]  # pii-ok (synthetic id)
                + rows
                + [T(date="2025-09-02", symbol="CCC.US", quantity=-15, net_amount=1800.0, account="55500001"),  # pii-ok (synthetic id)
                   T(date="2025-09-02", symbol="CCC.US", quantity=-41, net_amount=4920.0, account="55500002")])  # pii-ok (synthetic id)

    def test_blended_pool_scales_by_the_delivered_total(self):
        r = ca(self._book())
        recs = records(r)
        # 56 CCC delivered for 55 HHH at 5,500; both sold for 6,720.
        self.assertAlmostEqual(sum(g["gain"] for g in recs), 1220.0, places=6)
        self.assertAlmostEqual(sum(g["qty"] for g in recs), 56.0, places=9)
        self.assertEqual(r["inventory"], [])
        self.assertNotIn("conservation", r["_stderr"])


if __name__ == "__main__":
    unittest.main()
