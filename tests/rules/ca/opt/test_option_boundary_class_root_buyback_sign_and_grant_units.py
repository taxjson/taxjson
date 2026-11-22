"""Re-audit-2 fixes, filing-reports list (helper B): option-boundary,
reconcile-slips, Schedule 3 option units, the .sum per-asset block and
the fees report.

All data is synthetic (fake account ids, invented tickers).

Formerly: test_fix_a2_filrep_b
"""
import contextlib
import io
import unittest

from taxjson.model.transaction import TaxTransaction
from tax_rules import rule
from taxjson.rules import ids as R

GRANT25 = {2025: {"option_premium_timing": "grant", "option_grant_since": 2025}}


def T(action="BUYSELL", **kw):
    base = {"action": action, "currency": "CAD", "account": "margin",
            "time": "10:00:00"}
    base.update(kw)
    base.setdefault("date_settle", base.get("date"))
    return TaxTransaction(**base)


def _quiet(fn, *a, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = fn(*a, **kw)
    return out, err.getvalue()


class TestOptionBoundaryClassRoot(unittest.TestCase):
    """A2-0114 (regression of S075-09), A2-0328: an assignment whose
    option root drops the share class (RCI for RCI.B.TO, BRKB for
    BRK.B.US) is physically settled — option-boundary resolves the root
    with the engine's resolver instead of calling it cash-settled.

    Formerly: test_fix_a2_filrep_b.TestOptionBoundaryClassRoot"""

    def _book(self, stock):
        return [
            T(date="2025-12-01", date_settle="2025-12-02",
              symbol="RCI260116P00050000.TO", quantity=-1, price=2,
              net_amount=199.0),
            T("ASSIGN", date="2026-01-16", symbol="RCI260116P00050000.TO",
              quantity=1, price=0, net_amount=0.0, time="16:00:00"),
            T(date="2026-01-16", date_settle="2026-01-19", symbol=stock,
              quantity=100, price=50, net_amount=5000.0, time="16:00:00"),
            T(date="2026-03-02", date_settle="2026-03-03", symbol=stock,
              quantity=-100, price=52, net_amount=5200.0)]

    @rule(R.CA_OPT_07)
    def test_a2_0114_class_share_put_is_an_assignment(self):
        from taxjson.lib.option_boundary import straddling
        rows, _ = _quiet(straddling, self._book("RCI.B.TO"), 2026, "grant",
                         2025, filed_years={2025}, filed_timing=GRANT25)
        self.assertEqual([r["close_kind"] for r in rows], ["assignment"])
        self.assertIn("T1-ADJ 2025: remove the 199.00 premium",
                      rows[0]["action"])

    @rule(R.CA_OPT_07)
    def test_a2_0114_exact_root_control_unchanged(self):
        from taxjson.lib.option_boundary import straddling
        rows, _ = _quiet(straddling, self._book("RCI.TO"), 2026, "grant",
                         2025, filed_years={2025}, filed_timing=GRANT25)
        self.assertEqual([r["close_kind"] for r in rows], ["assignment"])

    @rule(R.CA_OPT_07)
    def test_a2_0328_brkb_call_root_resolves_to_class_line(self):
        from taxjson.lib.option_boundary import write_lots
        book = [
            T(date="2024-12-02", symbol="BRKB250117C00450000.US",
              quantity=-1, price=3, net_amount=300.0, currency="USD"),
            T("ASSIGN", date="2025-01-17", symbol="BRKB250117C00450000.US",
              quantity=1, price=0, net_amount=0.0, currency="USD"),
            T("ASSIGN", date="2025-01-17", date_settle="2025-01-21",
              symbol="BRK.B.US", quantity=-100, price=450,
              net_amount=45000.0, currency="USD")]
        lots, _ = _quiet(write_lots, book)
        self.assertEqual([c.kind for c in lots[0].closes], ["assignment"])

    @rule(R.CA_OPT_07)
    def test_a2_0328_index_option_still_cash_settled(self):
        from taxjson.lib.option_boundary import write_lots
        book = [
            T(date="2025-12-10", symbol="XSP260116P00500000.US",
              quantity=-1, price=5, net_amount=500.0, currency="USD"),
            T("ASSIGN", date="2026-01-16", symbol="XSP260116P00500000.US",
              quantity=1, price=3, net_amount=300.0, currency="USD")]
        lots, _ = _quiet(write_lots, book)
        self.assertEqual([c.kind for c in lots[0].closes], ["cash-settled"])


class TestOptionBoundaryBuybackSign(unittest.TestCase):
    """A2-1111: a .tt book carries a buy-back's net_amount negative
    (money out); the cost of the cover is its magnitude.

    Formerly: test_fix_a2_filrep_b.TestOptionBoundaryBuybackSign"""

    @rule(R.CA_OPT_07)
    def test_a2_1111_negative_buyback_net_is_a_cost(self):
        from taxjson.lib.option_boundary import straddling
        S = "Q260116C00050000.TO"
        for net in (-101.0, 101.0):
            book = [T(date="2025-12-15", symbol=S, quantity=-1, price=4,
                      net_amount=399.0),
                    T(date="2026-01-10", symbol=S, quantity=1, price=1,
                      net_amount=net)]
            rows, _ = _quiet(straddling, book, 2026, "close", None)
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual(r["paid"], 101.0)
            self.assertIn("net 298.00", r["where"])
            self.assertIn("-101.00 in 2026", r["action"])
            self.assertNotIn("--", r["action"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------
# Schedule 3 / reconcile-slips option units, reconcile-slips
# ---------------------------------------------------------------------

def _gains(book, year, since, timing="grant"):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    req = GainsRequest(country="canada", year=year, taxable=True,
                       option_premium_timing=timing,
                       option_grant_since=since)
    res, _ = _quiet(run_gains, list(book), (), (), req)
    return res


def _rows(res):
    return [e for e in res["transactions"] if "gain" in e and e.get("qty")]


def _computed(res, year):
    import json
    import tempfile
    from pathlib import Path
    from taxjson.lib.slip_symbols import load_computed
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "margin_gains_wash.json"
        p.write_text(json.dumps(res, default=str))
        return load_computed([p], year)


class TestOptionUnitsGrantTiming(unittest.TestCase):
    """A2-0320, A2-0650, A2-0651: a buy-back nets against a grant write
    only when it closes a write of the SAME year; a buy-back of an
    earlier year's write (grant or close timing) is its own disposition.

    Formerly: test_fix_a2_filrep_b.TestOptionUnitsGrantTiming"""

    S = "QZY250718C00045000.TO"

    def _t(self, i, d, q, net):
        return T(id=i, date=d, symbol=self.S, quantity=q,
                 price=abs(net / q / 100), net_amount=net)

    def _units(self, book, since):
        from taxjson.lib.tax_forms import build_schedule3
        res = _gains(book, 2025, since)
        s3 = build_schedule3(_rows(res), 2025)
        units = [r["units"] for r in s3["rows"] if r["symbol"] == self.S]
        comp = _computed(res, 2025)
        return units[0], comp["QZY250718C00045000"]["qty"]

    @rule(R.CA_OPT_01)
    def test_a2_0651_pre_since_buyback_plus_new_grant_write(self):
        book = [self._t("w1", "2024-11-01", -1, 300.0),
                self._t("c1", "2025-02-03", 1, 100.0),
                self._t("w2", "2025-03-03", -1, 250.0)]
        self.assertEqual(self._units(book, 2025), (2.0, 2.0))

    @rule(R.CA_OPT_01)
    def test_a2_0320_prior_year_grant_buyback_plus_new_write(self):
        book = [self._t("w1", "2024-11-01", -1, 300.0),
                self._t("c1", "2025-02-03", 1, 100.0),
                self._t("w2", "2025-03-03", -1, 250.0)]
        self.assertEqual(self._units(book, 2024), (2.0, 2.0))

    @rule(R.CA_OPT_01)
    def test_a2_0650_two_contracts_each(self):
        book = [self._t("w1", "2024-12-02", -2, 200.0),
                self._t("c1", "2025-03-03", 2, 102.0),
                self._t("w2", "2025-04-01", -2, 240.0)]
        for since in (2024, 2025):
            self.assertEqual(self._units(book, since), (4.0, 4.0))

    @rule(R.CA_OPT_01)
    def test_same_year_write_and_buyback_still_count_once(self):
        book = [self._t("w1", "2025-01-10", -1, 400.0),
                self._t("c1", "2025-01-20", 1, 50.0)]
        self.assertEqual(self._units(book, 2025), (1.0, 1.0))
