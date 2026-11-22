"""Re-audit-2 test pins (lists tests-pins-07/08, fork g3): check-dates
session and range boundaries, edge-cases day-30 boundaries, the R1-28
assignment pairing, income dating (listings, split-share roots, the US
§852(b)(7) warning window) and the per-ticker income sums.

Each test kills a mutant that survived the whole suite in the second
audit (the docstrings name the A2 id and the line pinned). Every
fixture is SYNTHETIC: invented tickers (QZ*, XYZ, GHI), fake accounts.

Formerly: test_fix_a2_pins3_g3
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from taxjson.rules import ids as R


# ========================================================= edge-cases
from taxjson.lib.edge_cases import analyze as edge_analyze  # noqa: E402


def _etx(acct, action, d, settle, sym, qty, typ=""):
    return {"account": acct, "action": action, "date": d,
            "date_settle": settle, "time": "10:00:00", "symbol": sym,
            "quantity": qty, "net_amount": 0.0, "price": 0.0,
            "currency": "CAD", "type": typ, "id": f"{sym}-{d}-{qty}"}


def _egain(acct, d, settle, sym, qty, raw, denied=0.0):
    return {"account": acct, "date": d, "date_settle": settle,
            "symbol": sym, "qty": qty, "gain": raw + denied,
            "raw_gain": raw, "disallowed_amount": denied,
            "permanently_disallowed": 0.0, "proceeds": 1000.0,
            "cost": 1000.0 - raw, "id": f"g-{sym}-{d}"}


def _edge_project(rows, gains, country="canada"):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    work = root / "work"
    work.mkdir()
    (work / "margin_base.json").write_text(json.dumps(rows))
    (work / "margin_gains_wash.json").write_text(json.dumps(
        {"transactions": gains, "inventory": []}))
    cfg = {"settings": {"year": 2025, "country": country},
           "accounts": {"margin": {"type": "taxable"}}}
    return td, root, cfg


class TestEdgeCasesDay30(unittest.TestCase):
    """A2-0535: edge_cases.window_edges `inside=off <= WINDOW` (sale),
    `"inside": abs(off) <= WINDOW` (long call) and calls_in_windows
    `abs((d - ld).days) > WINDOW`: day 30 is inside, day 31 outside, for
    each item kind. Canada counts on settlement dates: the loss settles
    Tue 2025-03-04, so day 30 is 2025-04-03 and day 31 2025-04-04.

    Formerly: test_fix_a2_pins3_g3.TestEdgeCasesDay30"""

    ROWS = [
        _etx("margin", "BUYSELL", "2025-01-10", "2025-01-13", "GHI.TO", 100),
        _etx("margin", "BUYSELL", "2025-03-03", "2025-03-04", "GHI.TO", -60),
        _etx("margin", "BUYSELL", "2025-03-05", "2025-03-06", "GHI.TO", 40),
        _etx("margin", "BUYSELL", "2025-04-02", "2025-04-03", "GHI.TO", -10),
        _etx("margin", "BUYSELL", "2025-04-03", "2025-04-04", "GHI.TO", -10),
        _etx("margin", "BUYSELL", "2025-04-02", "2025-04-03",
             "GHI260116C00010000.TO", 1),
        _etx("margin", "BUYSELL", "2025-04-03", "2025-04-04",
             "GHI260116C00012000.TO", 1),
    ]
    GAINS = [_egain("margin", "2025-03-03", "2025-03-04", "GHI.TO", 60,
                    -300.0, denied=100.0)]

    def _doc(self):
        td, root, cfg = _edge_project(self.ROWS, self.GAINS)
        with td:
            return edge_analyze(root, cfg)

    @rule(R.CA_RPT_07, R.CA_SL_01, R.CA_SL_02)
    def test_sale_and_long_call_day_30_inside_day_31_outside(self):
        doc = self._doc()
        (edge,) = [r for r in doc["window_edges"] if r["symbol"] == "GHI.TO"]
        got = {(i["kind"], i["day"]): i["inside"] for i in edge["items"]}
        self.assertEqual(got[("sale", 30)], True)
        self.assertEqual(got[("sale", 31)], False)
        self.assertEqual(got[("long call", 30)], True)
        self.assertEqual(got[("long call", 31)], False)

    @rule(R.CA_RPT_07, R.CA_SL_01)
    def test_calls_in_windows_takes_day_30_not_day_31(self):
        doc = self._doc()
        (row,) = [r for r in doc["calls_in_windows"]
                  if r["symbol"] == "GHI.TO"]
        self.assertEqual([(i["day"], i["option"]) for i in row["items"]],
                         [(30, "GHI260116C00010000.TO")])


class TestEdgeCasesStockDividend(unittest.TestCase):
    """A2-1547 (a real bug, fixed): a US stock dividend is not a
    purchase (US-STKDIV-01), so edge-cases no longer lists it as an
    in-window acquisition the engine ignores; Canada still lists it
    (CA-STKDIV-01: an acquisition at $0 that counts for s.54). Gate:
    country.stock_dividend_in_loss_window.

    Formerly: test_fix_a2_pins3_g3.TestEdgeCasesStockDividend"""

    ROWS = [
        _etx("margin", "BUYSELL", "2025-01-02", "2025-01-03", "ABC.US", 200),
        _etx("margin", "BUYSELL", "2025-05-20", "2025-05-21", "ABC.US", -100),
        _etx("margin", "BUYSELL", "2025-06-18", "2025-06-18", "ABC.US", 10,
             typ="stock_dividend"),
    ]
    GAINS = [_egain("margin", "2025-05-20", "2025-05-21", "ABC.US", 100,
                    -1000.0)]

    def _items(self, country):
        td, root, cfg = _edge_project(self.ROWS, self.GAINS, country)
        with td:
            doc = edge_analyze(root, cfg)
        return [i for r in doc["window_edges"] for i in r["items"]]

    @rule(R.US_RPT_05, R.US_STKDIV_01)
    @rule_absent(R.CA_STKDIV_01, country="usa")
    @rule(R.CA_STKDIV_01, R.CA_RPT_07)
    def test_stock_dividend_window_item_by_country(self):
        self.assertEqual(self._items("usa"), [])
        ca = self._items("canada")
        self.assertEqual([(i["kind"], i["day"], i["inside"]) for i in ca],
                         [("acquisition", 28, True)])

    def test_country_table(self):
        from taxjson.lib.country import stock_dividend_in_loss_window
        self.assertTrue(stock_dividend_in_loss_window("canada"))
        self.assertFalse(stock_dividend_in_loss_window("usa"))


if __name__ == "__main__":
    unittest.main()
