"""TJ-4930: money in a `--json` document is printed as taxjson holds it, with
no binary float's error. A float sum or quotient printed its error there
(`tjs fees-sum --json` total 80.72999999999999 for 80.73, `form-export --json`
gain_unrounded 1418.3301000000001 for v0.41.0's four places), and the golden
views compare numbers to float noise, so nothing failed.

- Every recorded golden --json view, and every one the golden views test
  produces (tests/_golden.py `money_tails`, in `compare`), carries no money
  number with more places than its field's: model/money's DERIVED_PLACES
  (6), a price 8, what a view adds up from the gains files their
  GAINS_PLACES (4). A quantity is not money.
- The check catches a binary tail and leaves an exact figure alone.
- model/money's `exact_sum` and `json_money`, and the writers that print
  with them: form-export's unrounded sums, fees-sum, the audit's events,
  taxjson-sum-gains' summary, winners / losers / ccd-sum rows, the
  estimate's traces.
"""
import json
import unittest
from decimal import Decimal

import _golden
from taxjson.model import money as M


def _views():
    for book in _golden.books():
        expected = _golden.GOLDEN / book / "expected" / "_views"
        for p in sorted(expected.glob("*.stdout.json")):
            yield book, p


class TestTheGoldenViews(unittest.TestCase):
    def test_no_recorded_view_prints_a_binary_tail(self):
        seen = 0
        for book, p in _views():
            seen += 1
            tails = _golden.money_tails(p.name, p.read_text(encoding="utf-8"))
            self.assertEqual(tails, [], f"{book}: {p.name}")
        self.assertGreater(seen, 100)

    def test_the_views_that_printed_one_are_recorded(self):
        """The outputs the v1.0.0 check found (fees-sum, trades-sum,
        winners, audit, form-export) are among the views held."""
        names = {p.name for _b, p in _views()}
        for n in ("fees-sum_json.stdout.json", "trades-sum_json.stdout.json",
                  "winners_json.stdout.json", "audit_json.stdout.json",
                  "form-export_json.stdout.json"):
            self.assertIn(n, names)


class TestTheCheck(unittest.TestCase):
    def tails(self, name, doc):
        return _golden.money_tails(name, json.dumps(doc))

    def test_a_float_sum_is_caught(self):
        self.assertEqual(len(self.tails("fees-sum_json.stdout.json",
                                        {"total": {"total": 0.1 + 0.2}})), 1)
        self.assertEqual(len(self.tails("audit_json.stdout.json",
                                        {"events": [{"gain": 167.46750000000085}]})), 1)

    def test_an_exact_figure_passes(self):
        self.assertEqual(self.tails("fees-sum_json.stdout.json",
                                    {"total": {"total": 80.73, "mean": 20.1825,
                                               "per_share": 94.976471}}), [])

    def test_the_gains_figures_have_four_places(self):
        self.assertEqual(self.tails("form-export_json.stdout.json", {"gain_unrounded": 1418.3301}), [])
        self.assertEqual(len(self.tails("form-export_json.stdout.json",
                                        {"gain_unrounded": 1418.33011})), 1)
        self.assertEqual(len(self.tails("winners_json_w120.stdout.json",
                                        {"rows": [{"gain": -42.52499}]})), 1)

    def test_a_price_has_eight_places_and_a_quantity_is_not_money(self):
        self.assertEqual(self.tails("staking_all_json.stdout.json",
                                    {"rewards": [{"book_price": 3051.61290323,
                                                  "qty": 0.05000000000000002,
                                                  "taxable_qty": 1.4051999999999998}]}), [])
        self.assertEqual(len(self.tails("staking_all_json.stdout.json",
                                        {"rewards": [{"price": 3051.612903225807}]})), 1)

    def test_text_is_not_json(self):
        self.assertEqual(_golden.money_tails("sum_json.stdout.json", "not json"), [])


class TestMoneyHelpers(unittest.TestCase):
    def test_exact_sum(self):
        self.assertNotEqual(repr(0.1 + 0.2), "0.3")
        self.assertEqual(M.exact_sum([0.1, 0.2]), Decimal("0.3"))
        self.assertEqual(repr(float(M.exact_sum([0.1, 0.2, None, 1, Decimal("0.05")]))), "1.35")
        self.assertEqual(M.exact_sum([]), Decimal(0))

    def test_json_money(self):
        self.assertEqual(M.json_money(94.97647058823527, M.DERIVED_PLACES), 94.976471)
        self.assertEqual(M.json_money(Decimal("20.1825"), 6), 20.1825)
        self.assertEqual(M.json_money(-42.52499999999995, M.GAINS_PLACES), -42.525)
        z = M.json_money(-1e-9, 6)
        self.assertEqual(json.dumps(z), "0.0")
        self.assertIs(type(M.json_money(5, 6)), int)
        self.assertIsNone(M.json_money(None, 6))


class TestTheWriters(unittest.TestCase):
    def test_form_export_unrounded_sums(self):
        from taxjson.lib.tax_forms import build_8949, build_schedule3
        rows = [{"symbol": s, "qty": 1.0, "proceeds": 10.0 + g, "cost": 10.0, "gain": g,
                 "disallowed_amount": d, "direction": "LONG", "date": "2025-03-04",
                 "date_settle": "2025-03-05", "days_held": 10, "term": "SHORT_TERM"}
                for s, g, d in (("ZZA", 0.1, 0.1), ("ZZB", 0.2, 0.2))]
        f8949 = build_8949([dict(r, symbol=r["symbol"] + ".US") for r in rows])
        self.assertEqual(repr(f8949["gain_unrounded"]), "0.3")
        self.assertEqual(repr(f8949["adjustment_unrounded"]), "0.3")
        s3 = build_schedule3([dict(r, symbol=r["symbol"] + ".TO") for r in rows])
        self.assertEqual(repr(s3["gain_unrounded"]), "0.3")
        self.assertEqual(repr(s3["denied_unrounded"]), "0.3")

    def test_fees_sum(self):
        from taxjson.bin import taxjson_fees as F
        s = F.new_stats()
        for fee in (10.1, 0.2, 0.1, 1.35):
            F.add_stat(s, fee, 0.17, False, 1000.0)
        m = F.json_metrics(s)
        self.assertEqual(repr(m["total"]), "11.75")
        self.assertEqual(repr(m["stock_fee"]), "11.75")
        self.assertEqual(m["mean"], 2.9375)
        self.assertEqual(m["median"], 0.775)
        self.assertEqual(m["per_share"], 17.279412)      # 11.75 / 0.68
        self.assertEqual(m["pct_notional"], 0.29375)     # 11.75 / 4000 x 100
        # The text keeps the float sum.
        self.assertEqual(repr(F.metrics(s)["total"]), "11.749999999999998")

    def test_audit_event(self):
        from taxjson.bin.taxjson_audit import json_event
        ev = {"gain": 167.46750000000085, "cost": 488.0655394631943, "qty": 0.1,
              "base_row": {"x": 1}, "trace": ("# a line",),
              "tie_out": {"records": 1, "pipeline_gain": 0.1 + 0.2, "ties": True}}
        d = json_event(ev)
        self.assertEqual((d["gain"], d["cost"], d["qty"]), (167.4675, 488.065539, 0.1))
        self.assertNotIn("base_row", d)
        self.assertEqual(d["trace"], ["# a line"])
        self.assertEqual(d["tie_out"], {"records": 1, "pipeline_gain": 0.3, "ties": True})
        self.assertEqual(ev["gain"], 167.46750000000085)       # the totals' figure stays

    def test_sum_gains_summary(self):
        from taxjson.bin.taxjson_sum_gains import json_figures
        rep = {"ticker_stats": {"QZQ.US": {"CAD": {"cost": 8444.250000000002, "opt": 59.400000000000006,
                                                   "trade_count": 2, "hold_days": [3, 4]}}},
               "returns_by_asset": {"Options": {"CAD": {"dollars": [1 / 3], "fees": [1.35]}}},
               "total_fees": {"CAD": 0.1 + 0.2}, "tainted_count": 0}
        out = json_figures(rep)
        self.assertEqual(out["ticker_stats"]["QZQ.US"]["CAD"],
                         {"cost": 8444.25, "opt": 59.4, "trade_count": 2, "hold_days": [3, 4]})
        self.assertEqual(out["returns_by_asset"]["Options"]["CAD"]["dollars"], [0.333333])
        self.assertEqual(out["total_fees"], {"CAD": 0.3})
        self.assertEqual(rep["total_fees"], {"CAD": 0.1 + 0.2})

    def test_winners_rows(self):
        from taxjson.cli.totals import _gains_figures
        self.assertEqual(_gains_figures({"closes": 2, "qty": 0.1 + 0.2, "gain": 8101.35 + 0.1 + 0.2,
                                         "cost": 8914.050000000001}),
                         {"closes": 2, "qty": 0.1 + 0.2, "gain": 8101.65, "cost": 8914.05})

    def test_estimate_traces(self):
        from taxjson.cli.estimate import _json_estimate
        r = {"total": 12.34, "trace_with": {"ti": 58956.79750000001, "surtax_parts": [(1.0, 0.2, 0.1 + 0.2)],
                                            "prov_surtax": 0},
             "trace_base": {"ftc_share": 0.008769981103536023}}
        out = _json_estimate(r)
        self.assertEqual(out["trace_with"], {"ti": 58956.7975, "surtax_parts": [[1.0, 0.2, 0.3]],
                                             "prov_surtax": 0})
        self.assertEqual(out["trace_base"], {"ftc_share": 0.00877})
        self.assertEqual(out["total"], 12.34)


if __name__ == "__main__":
    unittest.main()
