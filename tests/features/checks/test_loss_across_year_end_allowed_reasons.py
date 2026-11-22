"""`tjs edge-cases` (lib/edge_cases): the 2026-10-26 report on it
(backlog TJ-0282, TJ-0283), engine-backed — run_gains computes every
gains and inventory row from synthetic trades (ZZ* tickers, made-up
amounts), as `tjs run` writes them to work/.

TJ-0282: the denied losses "still held at the end of <year>" come from
the Canadian engine's pools at the year end (`inventory_year_end`), not
from its final inventory, so a later sale or purchase leaves them alone.

TJ-0283: a loss whose window spans Dec 31 and that the engine denied
nothing of gets the reason the books show (`allowed_by`); the report
never says "the replacement was not still held on day 30" unproved.

Formerly: test_fix_edge_cases_1026
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.edge_cases import analyze
from _support.ca_edge_cases import STOCK, CALL, _t, _edge, _CALL_BOOK, _CALL_RRSP


def _window(edge, sym):
    (w,) = [w for w in edge["year_boundary"]["loss_windows"]
            if w["symbol"] == sym]
    return w


class TestAllowedReason(unittest.TestCase):
    """TJ-0283: why a loss across Dec 31 was allowed, from the books.

    Formerly: test_fix_edge_cases_1026.TestAllowedReason"""

    OPTION = _CALL_BOOK
    RRSP = _CALL_RRSP

    def _option(self, timing, buyback):
        req = dict(option_premium_timing=timing,
                   option_buyback_loss_superficial=buyback,
                   option_grant_since=2025 if timing == "grant" else None)
        settings = {"option_premium_timing": timing,
                    "option_buyback_loss_superficial": buyback,
                    "option_grant_timing_since": 2025}
        return _edge({"margin": self.OPTION}, self.RRSP,
                     settings=settings, **req)

    def test_buyback_exempt_though_held_on_day_30(self):
        for timing, raw in (("grant", -150.0), ("close", -50.0)):
            with self.subTest(timing=timing):
                _, edge, text = self._option(timing, False)
                w = _window(edge, CALL)
                self.assertEqual((w["raw_loss"], w["denied"]), (raw, 0.0))
                self.assertEqual(w["allowed_by"], "buyback_exempt")
                self.assertIn("a written option's loss is exempt from the "
                              "rule (option_buyback_loss_superficial = "
                              "false), so its window is not tested",
                              w["why"])
                self.assertNotIn("day 30", w["why"])
                self.assertIn(w["why"], " ".join(text.split()))

    def test_buyback_tested_when_the_project_opts_in(self):
        _, edge, _ = self._option("close", True)
        w = _window(edge, CALL)
        self.assertGreater(w["denied"], 0)
        self.assertIsNone(w["allowed_by"])
        self.assertNotIn("exempt", w["why"])

    # A 250.00 loss on 2025-12-19 (settled 12-22) whose window runs to
    # 2026-01-21; a rebuy on 2026-01-05 (settled 01-06).
    LOSS = [_t("margin", "2025-11-03", "2025-11-04", STOCK, 5, -1000),
            _t("margin", "2025-12-19", "2025-12-19", STOCK, -5, 750)]
    LOSS[1].date_settle = "2025-12-22"
    REBUY = _t("margin", "2026-01-05", "2026-01-06", STOCK, 5, -760)

    def test_allowloss_position_though_the_replacement_is_kept(self):
        ov = {"account": "margin", "date": "2025-12-19", "symbol": STOCK,
              "qty": None, "reason": "a synthetic filing position",
              "where": "inputs/margin/x.tt:1",
              "line": f'ALLOWLOSS 2025-12-19 {STOCK} '
                      f'reason="a synthetic filing position"'}
        _, edge, text = _edge({"margin": self.LOSS + [self.REBUY]},
                              loss_overrides=(ov,))
        w = _window(edge, STOCK)
        self.assertEqual(w["denied"], 0.0)
        self.assertEqual(w["allowed_by"], "filing_position")
        self.assertIn("allowed by your filing position: ALLOWLOSS "
                      "inputs/margin/x.tt:1", w["why"])
        self.assertIn('"a synthetic filing position"', w["why"])
        self.assertNotIn("day 30", w["why"])
        self.assertIn(w["why"], " ".join(text.split()))

    def test_replacement_sold_before_day_30(self):
        _, edge, _ = _edge({"margin": self.LOSS + [
            self.REBUY,
            _t("margin", "2026-01-12", "2026-01-13", STOCK, -5, 770)]})
        w = _window(edge, STOCK)
        self.assertEqual(w["denied"], 0.0)
        self.assertEqual(w["allowed_by"], "not_held_day30")
        self.assertIn("allowed: no units were held at the end of day 30 "
                      "(2026-01-21)", w["why"])

    def test_replacement_kept_is_denied(self):
        _, edge, _ = _edge({"margin": self.LOSS + [self.REBUY]})
        w = _window(edge, STOCK)
        self.assertEqual(w["denied"], 250.0)
        self.assertIsNone(w["allowed_by"])


class TestAllowedReasonFromTheBooks(unittest.TestCase):
    """The rows the engine writes for a loss the rule does not reach, and
    one it allowed for a reason the books do not record.

    Formerly: test_fix_edge_cases_1026.TestAllowedReasonFromTheBooks"""

    def _edge(self, gain, rows):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            (work / "margin_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in rows]}))
            (work / "margin_gains_wash.json").write_text(json.dumps(
                {"transactions": [gain], "inventory": [],
                 "inventory_year_end": []}))
            cfg = {"settings": {"year": 2025, "country": "canada",
                                "tax_date": "settle"},
                   "accounts": {"margin": {"type": "taxable"}}}
            return _window(analyze(root, cfg), STOCK)

    ROWS = [_t("margin", "2025-11-03", "2025-11-04", STOCK, 5, -1000),
            _t("margin", "2025-12-19", "2025-12-22", STOCK, -5, 750),
            _t("margin", "2026-01-05", "2026-01-06", STOCK, 5, -760)]

    def _gain(self, **extra):
        return dict({"account": "margin", "date": "2025-12-19",
                     "date_settle": "2025-12-22", "symbol": STOCK, "qty": 5,
                     "gain": -250.0, "raw_gain": -250.0,
                     "disallowed_amount": 0.0, "proceeds": 750.0,
                     "cost": 1000.0, "direction": "LONG",
                     "id": "margin-2025-12-19-ZZY.US--5"}, **extra)

    def test_in_kind_contribution(self):
        w = self._edge(self._gain(in_kind="contribution",
                                  denied_contribution=250.0, gain=0.0),
                       self.ROWS)
        self.assertEqual(w["allowed_by"], "in_kind_contribution")
        self.assertIn("not a superficial loss: an in-kind contribution",
                      w["why"])
        self.assertIn("s.40(2)(g)(iv) denies for good", w["why"])
        self.assertNotIn("allowed", w["why"])

    def test_no_reason_recorded_claims_none(self):
        w = self._edge(self._gain(), self.ROWS)
        self.assertEqual(w["allowed_by"], "unknown")
        self.assertIn("no denial applied by the engine (5 units were held "
                      "at the end of day 30, 2026-01-21; the books do not "
                      "record why)", w["why"])
        self.assertNotIn("not still held", w["why"])


if __name__ == "__main__":
    unittest.main()
