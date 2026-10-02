"""Re-audit-2 fixes, filing-reports list (helper B): option-boundary,
reconcile-slips, Schedule 3 option units, the .sum per-asset block and
the fees report.

All data is synthetic (fake account ids, invented tickers).
"""
import contextlib
import io
import unittest
from datetime import date

from taxjson.lib.core import TaxTransaction
from tax_rules import rule

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
    with the engine's resolver instead of calling it cash-settled."""

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

    @rule("CA-OPT-07")
    def test_a2_0114_class_share_put_is_an_assignment(self):
        from taxjson.lib.option_boundary import straddling
        rows, _ = _quiet(straddling, self._book("RCI.B.TO"), 2026, "grant",
                         2025, filed_years={2025}, filed_timing=GRANT25)
        self.assertEqual([r["close_kind"] for r in rows], ["assignment"])
        self.assertIn("T1-ADJ 2025: remove the 199.00 premium",
                      rows[0]["action"])

    @rule("CA-OPT-07")
    def test_a2_0114_exact_root_control_unchanged(self):
        from taxjson.lib.option_boundary import straddling
        rows, _ = _quiet(straddling, self._book("RCI.TO"), 2026, "grant",
                         2025, filed_years={2025}, filed_timing=GRANT25)
        self.assertEqual([r["close_kind"] for r in rows], ["assignment"])

    @rule("CA-OPT-07")
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

    @rule("CA-OPT-07")
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
    (money out); the cost of the cover is its magnitude."""

    @rule("CA-OPT-07")
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


class TestOptionBoundaryExpiryDay(unittest.TestCase):
    """A2-1112: on its own expiry day a contract is still open (the
    broker posts the expiry row afterwards), as the run's own
    expired-options warning already treats it."""

    S = "Q261001C00050000.TO"

    def _book(self):
        return [T(date="2026-09-01", symbol=self.S, quantity=-1, price=4,
                  net_amount=399.0),
                T(date="2026-09-02", symbol="Z261001C00010000.TO",
                  quantity=1, price=1, net_amount=101.0)]

    @rule("CA-OPT-07")
    def test_a2_1112_expiry_day_is_open(self):
        from taxjson.lib.option_boundary import expired_open, straddling
        today = date(2026, 10, 1)
        self.assertEqual(expired_open(self._book(), 2026, today=today), [])
        rows = straddling(self._book(), 2026, "grant", 2026, today=today)
        self.assertEqual([r["close_kind"] for r in rows], ["open"])

    @rule("CA-OPT-07")
    def test_a2_1112_day_after_expiry_is_missing_row(self):
        from taxjson.lib.option_boundary import expired_open, straddling
        today = date(2026, 10, 2)
        self.assertEqual(len(expired_open(self._book(), 2026,
                                          today=today)), 2)
        rows = straddling(self._book(), 2026, "grant", 2026, today=today)
        self.assertEqual([r["close_kind"] for r in rows], ["expired?"])

    @rule("CA-OPT-07")
    def test_a2_1112_dec31_expiry_of_a_past_year_still_flagged(self):
        from taxjson.lib.option_boundary import expired_open
        book = [T(date="2025-09-01", symbol="Q251231C00050000.TO",
                  quantity=-1, price=4, net_amount=399.0)]
        rows = expired_open(book, 2025, today=date(2026, 3, 1))
        self.assertEqual([r["symbol"] for r in rows],
                         ["Q251231C00050000.TO"])


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
    from taxjson.bin.taxjson_reconcile_slips import load_computed
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "margin_gains_wash.json"
        p.write_text(json.dumps(res, default=str))
        return load_computed([p], year)


def _slip(tmpdir, text):
    from pathlib import Path
    from taxjson.bin.taxjson_reconcile_slips import load_slip
    p = Path(tmpdir) / "t5008.csv"
    p.write_text(text)
    return load_slip(p)


class TestOptionUnitsGrantTiming(unittest.TestCase):
    """A2-0320, A2-0650, A2-0651: a buy-back nets against a grant write
    only when it closes a write of the SAME year; a buy-back of an
    earlier year's write (grant or close timing) is its own disposition."""

    S = "XYZ250620C00050000.TO"

    def _t(self, i, d, q, net):
        return T(id=i, date=d, symbol=self.S, quantity=q,
                 price=abs(net / q / 100), net_amount=net)

    def _units(self, book, since):
        from taxjson.bin.taxjson_form_export import build_schedule3
        res = _gains(book, 2025, since)
        s3 = build_schedule3(_rows(res), 2025)
        units = [r["units"] for r in s3["rows"] if r["symbol"] == self.S]
        comp = _computed(res, 2025)
        return units[0], comp["XYZ250620C00050000"]["qty"]

    @rule("CA-OPT-01")
    def test_a2_0651_pre_since_buyback_plus_new_grant_write(self):
        book = [self._t("w1", "2024-11-01", -1, 300.0),
                self._t("c1", "2025-02-03", 1, 100.0),
                self._t("w2", "2025-03-03", -1, 250.0)]
        self.assertEqual(self._units(book, 2025), (2.0, 2.0))

    @rule("CA-OPT-01")
    def test_a2_0320_prior_year_grant_buyback_plus_new_write(self):
        book = [self._t("w1", "2024-11-01", -1, 300.0),
                self._t("c1", "2025-02-03", 1, 100.0),
                self._t("w2", "2025-03-03", -1, 250.0)]
        self.assertEqual(self._units(book, 2024), (2.0, 2.0))

    @rule("CA-OPT-01")
    def test_a2_0650_two_contracts_each(self):
        book = [self._t("w1", "2024-12-02", -2, 200.0),
                self._t("c1", "2025-03-03", 2, 102.0),
                self._t("w2", "2025-04-01", -2, 240.0)]
        for since in (2024, 2025):
            self.assertEqual(self._units(book, since), (4.0, 4.0))

    @rule("CA-OPT-01")
    def test_same_year_write_and_buyback_still_count_once(self):
        book = [self._t("w1", "2025-01-10", -1, 400.0),
                self._t("c1", "2025-01-20", 1, 50.0)]
        self.assertEqual(self._units(book, 2025), (1.0, 1.0))


class TestReconcileSlipsHeaders(unittest.TestCase):
    """A2-0656: two exact spellings of one amount are ambiguous."""

    def test_a2_0656_two_exact_proceeds_columns_refused(self):
        from taxjson.bin.taxjson_reconcile_slips import (AmbiguousHeader,
                                                         _map_headers)
        for cols in (["Symbol", "Quantity", "Proceeds",
                      "Proceeds of disposition"],
                     ["Symbol", "Quantity", "Proceeds of disposition",
                      "Proceeds"],
                     ["Symbol", "Quantity", "Qty", "Proceeds"]):
            with self.assertRaises(AmbiguousHeader):
                _map_headers(cols)

    def test_a2_0656_ticker_and_security_name_still_accepted(self):
        from taxjson.bin.taxjson_reconcile_slips import _map_headers
        m = _map_headers(["Symbol", "Security", "Quantity", "Proceeds"])
        self.assertEqual(m["symbol"], "Symbol")


class TestReconcileSlipsGroupedStrike(unittest.TestCase):
    """A2-1113: a slip's option description with a grouped strike."""

    def test_a2_1113_thousands_separator_strike(self):
        from taxjson.bin.taxjson_reconcile_slips import slip_symbol
        self.assertEqual(slip_symbol("CALL SPX12/19/25 5,000.00"),
                         "SPX251219C05000000")
        self.assertEqual(slip_symbol("SPX 19DEC25 5,000 C"),
                         "SPX251219C05000000")
        self.assertEqual(slip_symbol("CALL SPX12/19/25 5000.00"),
                         "SPX251219C05000000")
        # a decimal comma is not read as a strike
        self.assertNotEqual(slip_symbol("CALL XYZ12/19/25 2,50"),
                            "XYZ251219C00002000")


class TestReconcileSlipsGrantStraddle(unittest.TestCase):
    """A2-0657: a grant-timing write still open at Dec 31 needs no slip
    in the write year; the close year's slip carries the premium."""

    S = "DEF260116C00030000.TO"
    BOOK = [T(id="w", date="2025-11-03", symbol=S, quantity=-3, price=0.8,
              net_amount=240.0),
            T(id="c", date="2026-01-12", symbol=S, quantity=3, price=0.21,
              net_amount=63.0)]

    @rule("CA-OPT-01")
    def test_a2_0657_write_year_no_slip_expected(self):
        import tempfile
        from taxjson.bin.taxjson_reconcile_slips import reconcile
        comp = _computed(_gains(self.BOOK, 2025, 2025), 2025)
        with tempfile.TemporaryDirectory() as tmp:
            slip = _slip(tmp, "Symbol,Quantity,Proceeds,Cost\n"
                              "ABC.TO,10,1000.00,900.00\n")
        rep = reconcile(slip, comp, 1.0)
        row = [r for r in rep["rows"] if r["symbol"].startswith("DEF")][0]
        self.assertEqual(row["symbol"], self.S)
        self.assertEqual(row["status"], "NO_SLIP_EXPECTED")
        self.assertIn("240.00", row["detail"])

    @rule("CA-OPT-01")
    def test_a2_0657_close_year_slip_with_prior_premium_ok(self):
        import tempfile
        from taxjson.bin.taxjson_reconcile_slips import reconcile
        comp = _computed(_gains(self.BOOK, 2026, 2025), 2026)
        with tempfile.TemporaryDirectory() as tmp:
            slip = _slip(tmp, "Symbol,Quantity,Proceeds,Cost\n"
                              f"{self.S},3,240.00,63.00\n")
        rep = reconcile(slip, comp, 1.0)
        self.assertTrue(rep["clean"], rep["rows"])
        self.assertIn("earlier-year", rep["rows"][0]["detail"])

    @rule("CA-OPT-01")
    def test_a2_0657_close_year_wrong_slip_still_mismatch(self):
        import tempfile
        from taxjson.bin.taxjson_reconcile_slips import reconcile
        comp = _computed(_gains(self.BOOK, 2026, 2025), 2026)
        with tempfile.TemporaryDirectory() as tmp:
            slip = _slip(tmp, "Symbol,Quantity,Proceeds,Cost\n"
                              f"{self.S},3,400.00,63.00\n")
        rep = reconcile(slip, comp, 1.0)
        self.assertFalse(rep["clean"])


class TestSumGainsGrantWrites(unittest.TestCase):
    """A2-1114: the .sum per-asset block counts a grant-timing write as
    one trade whose result is its premium plus a same-year buy-back."""

    @staticmethod
    def _e(sym, date, qty, gain, grant=False, gc=None, days=0):
        e = {"symbol": sym, "date": date, "date_settle": date, "qty": qty,
             "gain": gain, "cost": -gain if grant else 0.0,
             "proceeds": 0.0 if grant else gain, "currency": "CAD",
             "direction": "SHORT", "grant": grant, "days_held": days}
        if gc is not None:
            e["grant_closed"] = gc
        return e

    def _dollars(self, txs):
        from taxjson.bin.taxjson_sum_gains import summarize_gains
        out = summarize_gains({"transactions": txs,
                               "summary": {"year": 2025}})
        return sorted(round(x, 2) for x in
                      out["returns_by_asset"]["Options"]["CAD"]["dollars"])

    @rule("CA-OPT-01")
    def test_a2_1114_write_premium_in_per_asset_block(self):
        A, B = "XYZ250620C00060000.TO", "XYZ250718C00065000.TO"
        txs = [self._e(A, "2025-03-04", 2, 600.0, grant=True),
               self._e(B, "2025-03-04", 1, 200.0, grant=True),
               self._e(B, "2025-04-02", 1, -50.0,
                       gc={"2025": {"units": 1.0, "premium": 200.0}},
                       days=29)]
        self.assertEqual(self._dollars(txs), [150.0, 600.0])
        # a gains file written before grant_closed: same pairing
        txs[2].pop("grant_closed")
        self.assertEqual(self._dollars(txs), [150.0, 600.0])

    @rule("CA-OPT-01")
    def test_a2_1114_prior_year_write_buyback_is_its_own_trade(self):
        B = "XYZ250718C00065000.TO"
        txs = [self._e(B, "2025-02-03", 1, -100.0,
                       gc={"2024": {"units": 1.0, "premium": 300.0}}),
               self._e(B, "2025-03-04", 1, 250.0, grant=True)]
        self.assertEqual(self._dollars(txs), [-100.0, 250.0])
