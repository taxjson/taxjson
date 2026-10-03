"""Re-audit-2 test pins (tests-pins-03): the day-30 (and day-10) edges
of the warning detectors — A2-1549, A2-1550, A2-1591. Day 30 is inside
the window, day 31 outside. Synthetic data only."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import tx

from taxjson.lib.core import (detect_right_replacement_matches,
                              detect_unresolved_option_replacement_matches)

LOSS_DATE = "2025-03-03"
DAY30 = "2025-04-02"
DAY31 = "2025-04-03"
DAY30_BEFORE = "2025-02-01"
DAY31_BEFORE = "2025-01-31"


def _loss(symbol):
    return [{"symbol": symbol, "date": LOSS_DATE, "amount": -500.0,
             "id": "loss1", "direction": "LONG"}]


def _flagged(fn, loss_sym, acq_sym, date):
    ev = tx("BUYSELL", date, acq_sym, 1, 100)
    return [w["option_symbol"] for w in fn(_loss(loss_sym), [ev],
                                          date_of=lambda t: t.date)]


class TestRightReplacementWindowEdge(unittest.TestCase):
    """A2-1550 / A2-1591: detect_right_replacement_matches (S071-14)."""

    def _f(self, date):
        return _flagged(detect_right_replacement_matches, "SLH.US",
                        "SLH.WS.US", date)

    @rule("CA-SL-14")
    @rule("US-WASH-14")
    def test_day_30_after_and_before_are_flagged(self):
        self.assertEqual(self._f(DAY30), ["SLH.WS.US"])
        self.assertEqual(self._f(DAY30_BEFORE), ["SLH.WS.US"])

    @rule("CA-SL-14")
    @rule("US-WASH-14")
    def test_day_31_is_not(self):
        self.assertEqual(self._f(DAY31), [])
        self.assertEqual(self._f(DAY31_BEFORE), [])


class TestUnresolvedOptionWindowEdge(unittest.TestCase):
    """A2-1549 / A2-1591: detect_unresolved_option_replacement_matches
    (S069-23), an adjusted-series call."""

    def _f(self, date):
        return _flagged(detect_unresolved_option_replacement_matches,
                        "ZZS.US", "ZZS1251219C00050000.US", date)

    @rule("CA-SL-15")
    @rule("US-WASH-15")
    def test_day_30_after_and_before_are_flagged(self):
        self.assertEqual(self._f(DAY30), ["ZZS1251219C00050000.US"])
        self.assertEqual(self._f(DAY30_BEFORE), ["ZZS1251219C00050000.US"])

    @rule("CA-SL-15")
    @rule("US-WASH-15")
    def test_day_31_is_not(self):
        self.assertEqual(self._f(DAY31), [])
        self.assertEqual(self._f(DAY31_BEFORE), [])


class TestPartialTaintWarningWindowEdge(unittest.TestCase):
    """A2-1591: the pipeline's partial-taint warning (a phantom-pool loss
    with a purchase in the window) — day 30 warns, day 31 does not."""

    def _warns(self, rebuy_date):
        from taxjson.lib.pipeline import GainsRequest, run_gains
        book = [tx("BUYSELL", "2025-11-03", "PT.TO", 1000, 50000.0,
                   currency="CAD", account="m"),
                tx("BUYSELL", "2026-02-02", "PT.TO", -1010, 40400.0,
                   currency="CAD", account="m"),
                tx("BUYSELL", rebuy_date, "PT.TO", 500, 20000.0,
                   currency="CAD", account="m")]
        with tempfile.TemporaryDirectory() as tmp:
            ph = Path(tmp) / "phantoms.json"
            ph.write_text(json.dumps([{"symbol": "PT.TO", "account": "m"}]))
            with contextlib.redirect_stderr(io.StringIO()):
                res = run_gains(copy.deepcopy(book), [], [], req=GainsRequest(
                    country="canada", year=2026, taxable=True,
                    incomplete_history=ph))
        return [w for w in res.get("superficial_loss_warnings") or []
                if w.get("acquisition_date") == rebuy_date]

    @rule("CA-ACB-12")
    def test_day_30_warns_day_31_does_not(self):
        self.assertEqual(len(self._warns("2026-03-04")), 1)    # day 30
        self.assertEqual(self._warns("2026-03-05"), [])        # day 31


class TestSplitTwinWindowEdge(unittest.TestCase):
    """A2-1591: corp_views.splits flags a second identical split within
    10 days as applied twice (TWICE?); day 10 inside, day 11 outside."""

    def _flags(self, second):
        from test_corp_views import _proj, _row
        from taxjson.lib.corp_views import splits
        rows = [_row("BUYSELL", "2024-01-02", "NVDA.US", 40, 1000),
                _row("SPLIT", "2024-06-07", "NVDA.US", 10.0),
                _row("SPLIT", second, "NVDA.US", 10.0)]
        td, root, cfg = _proj(rows)
        with td:
            items = splits(root, cfg)
        return [i["flags"] for i in items if i["symbol"] == "NVDA.US"][0]

    def test_day_10_is_a_twin_day_11_is_not(self):
        self.assertIn("TWICE?", self._flags("2024-06-17"))
        self.assertNotIn("TWICE?", self._flags("2024-06-18"))


if __name__ == "__main__":
    unittest.main()
