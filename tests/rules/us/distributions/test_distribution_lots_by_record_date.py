"""Audit 2026-10-24, group "inkind": B20, B43, B05, B16, A13, B36, B37,
B14 — in-kind moves, the corporate views, the holdings folder, the
holdings TOML reader and the [[distributions]] ADJUST. Synthetic data
only.

Formerly: test_fix_audit_1024_inkind"""
import io
import contextlib
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


def _tx(d, q, sym="ZZQ.US", action="BUYSELL", **kw):
    return dict(date=d, date_settle=d, time="10:00:00", account="margin",
                symbol=sym, action=action, quantity=q, net_amount=0,
                currency="USD", **kw)


class TestDistributions(unittest.TestCase):
    """Formerly: test_fix_audit_1024_inkind.TestDistributions"""

    def _gains(self, rows):
        from taxjson.engine.us.taxrules import USATaxRules
        from taxjson.model.transaction import TaxTransaction
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return USATaxRules().compute_gains(
                [TaxTransaction(**r) for r in rows], detect_wash_sales=False)

    @rule(R.US_DIST_01)
    def test_b14_each_lot_settled_by_the_record_date_takes_its_part(self):
        """B14: Distribution backdating puts a later eligible lots
        adjustment on the oldest lot — three 10-share lots, the middle
        one settling after the record date: the first and third take
        10 each (cost 110), the middle one nothing (100)."""
        from taxjson.bin import taxjson_apply_distributions as dist
        rows = [_tx("2026-01-01", 10), _tx("2026-01-02", 10),
                _tx("2026-01-03", 10)]
        rows[1]["date_settle"] = "2026-01-06"
        rows[2]["date_settle"] = "2026-01-04"
        for r in rows:
            r["net_amount"], r["price"] = 100, 10
        self.assertEqual(dist._stamp_date(rows, "ZZQ.US", "2026-01-05",
                                          "margin"), "2026-01-05")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            adjusted, n = dist.apply_distributions(
                {"transactions": rows, "metadata": {"target_currency": "USD"}},
                [("ZZQ.US", "2026-01-05", 1)], "margin", country="usa")
        self.assertEqual(n, 1)
        adj = [t for t in adjusted["transactions"] if t["action"] == "ADJUST"]
        self.assertEqual([(a["date"], a["date_settle"], a["record_date"],
                           a["net_amount"]) for a in adj],
                         [("2026-01-05", "2026-01-05", "2026-01-05", 20.0)])
        self.assertNotIn("ATTENTION", err.getvalue())
        sales = [_tx("2026-01-07", -10), _tx("2026-01-08", -10),
                 _tx("2026-01-09", -10)]
        for s in sales:
            s["net_amount"], s["price"] = 200, 20
        result = self._gains(adjusted["transactions"] + sales)
        self.assertEqual([t["cost"] for t in result["transactions"]],
                         [110.0, 100.0, 110.0])

    @rule(R.US_DIST_01)
    def test_b14_straddling_sale_still_backdates_and_misses_are_said(self):
        """B14: a sale straddling the record date keeps the backdated
        stamp (its lot is the holder's); a purchase between that sale
        and the record date cannot then take its part, and the US run
        says so (ATTENTION) instead of placing it silently; Canada's
        pool needs no lot and gets no warning."""
        from taxjson.bin import taxjson_apply_distributions as dist
        rows = [_tx("2026-01-01", 10), _tx("2026-01-02", -10),
                _tx("2026-01-03", 10)]
        rows[1]["date_settle"] = "2026-01-06"
        rows[2]["date_settle"] = "2026-01-04"
        self.assertEqual(dist._stamp_date(rows, "ZZQ.US", "2026-01-05",
                                          "margin"), "2026-01-01")
        self.assertIsNone(dist._stamp_problem(rows, "ZZQ.US", "2026-01-05",
                                              "margin", "2026-01-05"))
        self.assertIn("bought 10 on 2026-01-03",
                      dist._stamp_problem(rows, "ZZQ.US", "2026-01-05",
                                          "margin", "2026-01-01"))
        for country, said in (("usa", True), ("canada", False)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                adjusted, n = dist.apply_distributions(
                    {"transactions": [dict(r) for r in rows],
                     "metadata": {"target_currency": "USD"}},
                    [("ZZQ.US", "2026-01-05", 1)], "margin",
                    country=country)
            self.assertEqual(n, 1, country)
            self.assertEqual("ATTENTION" in err.getvalue(), said, country)
            adj = [t for t in adjusted["transactions"]
                   if t["action"] == "ADJUST"][0]
            self.assertEqual((adj["date"], adj["net_amount"]),
                             ("2026-01-01", 20.0))

    @rule(R.US_DIST_01)
    def test_b14_engine_skips_lots_settling_after_the_record_date(self):
        """B14: the US engine spreads a 'dist' ADJUST with a record date
        over the open lots settled by it only; with none settled the
        row is not applied and says so."""
        rows = [_tx("2026-01-01", 10), _tx("2026-01-02", 10)]
        rows[1]["date_settle"] = "2026-01-06"
        for r in rows:
            r["net_amount"], r["price"] = 100, 10
        adj = dict(action="ADJUST", date="2026-01-05", time="23:59:58",
                   date_settle="2026-01-05", record_date="2026-01-05",
                   symbol="ZZQ.US", quantity=0.0, net_amount=10.0,
                   currency="USD", type="dist", account="margin",
                   id="DIST-ZZQ.US-2026-01-05-margin")
        sales = [_tx("2026-01-07", -10), _tx("2026-01-08", -10)]
        for s in sales:
            s["net_amount"], s["price"] = 200, 20
        result = self._gains(rows + [adj] + sales)
        self.assertEqual([t["cost"] for t in result["transactions"]],
                         [110.0, 100.0])
        # Without a record date (a .tt ADJUST, a broker's row): pro rata
        # over every open lot, as before.
        plain = dict(adj, record_date="", type="")
        result = self._gains(rows + [plain] + sales)
        self.assertEqual([t["cost"] for t in result["transactions"]],
                         [105.0, 105.0])
        # Every open lot settling after the record date: not applied.
        late = dict(adj, record_date="2025-12-31")
        result = self._gains(rows + [late] + sales)
        self.assertEqual([t["cost"] for t in result["transactions"]],
                         [100.0, 100.0])


if __name__ == "__main__":
    unittest.main()
