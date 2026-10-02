"""`taxjson spinoffs` / `taxjson splits` (lib/corp_views)."""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.corp_views import (render_spinoffs, render_splits,
                                    spinoffs, splits)


def _proj(rows, elections=None, sheltered=False):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "work").mkdir()
    (root / "inputs" / "m").mkdir(parents=True)
    (root / "work" / "m_base.json").write_text(json.dumps(rows))
    (root / "inputs" / "m" / "manifest.json").write_text(
        json.dumps({"elections": elections or {}}))
    cfg = {"settings": {"base_currency": "CAD", "country": "canada"},
           "accounts": {"m": {"type": "sheltered" if sheltered
                              else "taxable"}}}
    return td, root, cfg


def _row(action, date, sym, qty, net=0.0, **kw):
    d = {"action": action, "date": date, "time": "09:30:00", "symbol": sym,
         "quantity": qty, "net_amount": net}
    d.update(kw)
    return d


EV = "20240404-ge-gev-35b9"
SPIN_DESC = "Spinoff GE.US\u2192GEV.US (deemed dividend at FMV)"
SUMMARY = ("2024-04-04 spinoff: GE.US → GEV.US (30.0-for-120.0, ratio "
           "0.25, FMV 3807.69 USD)")


class TestSpinoffs(unittest.TestCase):
    def test_zero_value_flagged_with_broker_value(self):
        rows = [_row("BUYSELL", "2024-01-02", "GE.US", 120, 20000),
                _row("DIVIDEND", "2024-04-04", "GEV.US", 0, 0.0,
                     corp_event_id=EV, description=SPIN_DESC),
                _row("BUYSELL", "2024-04-04", "GEV.US", 30, 0.0,
                     corp_event_id=EV, description=SPIN_DESC)]
        td, root, cfg = _proj(rows, {EV: {
            "election": "taxable_deemed_dividend",
            "hints": {"fmv_per_share": 0.0}, "summary": SUMMARY}})
        with td:
            doc = spinoffs(root, cfg)
        items = doc["spinoffs"]
        self.assertEqual(len(items), 1)
        s = items[0]
        self.assertEqual((s["parent"], s["child"]), ("GE.US", "GEV.US"))
        self.assertEqual(s["flags"], ["ZERO-VALUE"])
        self.assertEqual(s["broker_fmv"], 0.0)   # no current broker event
        self.assertIn("ZERO-VALUE", "\n".join(render_spinoffs(doc)))

    def test_valued_and_sheltered(self):
        rows = [_row("DIVIDEND", "2024-04-04", "GEV.US", 0, 5141.9,
                     corp_event_id=EV, description=SPIN_DESC),
                _row("BUYSELL", "2024-04-04", "GEV.US", 30, 5141.9,
                     corp_event_id=EV, description=SPIN_DESC)]
        td, root, cfg = _proj(rows, {EV: {
            "election": "taxable_deemed_dividend",
            "hints": {"fmv_per_share": 126.92}, "summary": SUMMARY}})
        with td:
            s = spinoffs(root, cfg)["spinoffs"][0]
        self.assertEqual(s["flags"], [])
        self.assertAlmostEqual(s["income"], 5141.9)
        td, root, cfg = _proj(rows, {EV: {
            "election": "taxable_deemed_dividend",
            "hints": {"fmv_per_share": 0.0}, "summary": SUMMARY}},
            sheltered=True)
        with td:
            self.assertEqual(spinoffs(root, cfg)["spinoffs"][0]["flags"], [])

    def test_stale_election_listed_separately(self):
        rows = [_row("BUYSELL", "2024-04-04", "GEV.US", 30, 0.0,
                     corp_event_id=EV,
                     description="Spinoff GE.US\u2192GEV.US (deemed)")]
        old = "20240404-x-y-0000"
        td, root, cfg = _proj(rows, {
            EV: {"election": "taxable_deemed_dividend",
                 "hints": {"fmv_per_share": 1.0}, "summary": SUMMARY},
            old: {"election": "taxable_deemed_dividend",
                  "hints": {"fmv_per_share": 0.0},
                  "summary": "2024-04-04 spinoff: X \u2192 Y (1-for-1)"}})
        with td:
            doc = spinoffs(root, cfg)
        self.assertEqual([s["event_id"] for s in doc["spinoffs"]], [EV])
        self.assertEqual(doc["spinoffs"][0]["parent"], "GE.US")
        self.assertEqual([s["event_id"] for s in doc["stale"]], [old])
        self.assertIn("--reset --event " + old,
                      "\n".join(render_spinoffs(doc)))


class TestSplits(unittest.TestCase):
    def test_before_after_and_flags(self):
        rows = [_row("BUYSELL", "2024-01-02", "NVDA.US", 40, 1000),
                _row("SPLIT", "2024-06-07", "NVDA.US", 10.0),
                _row("SPLIT", "2024-06-10", "NVDA.US", 10.0),   # twice
                _row("BUYSELL", "2023-01-02", "HON.US", 15, 1000),
                _row("SPLIT", "2026-06-26", "HON.US", 0.5)]      # fraction
        td, root, cfg = _proj(rows)
        with td:
            items = splits(root, cfg)
        nv = [i for i in items if i["symbol"] == "NVDA.US"]
        self.assertEqual(nv[0]["held_before"], 40)
        self.assertEqual(nv[0]["held_after"], 400)
        self.assertIn("TWICE?", nv[0]["flags"])
        hon = [i for i in items if i["symbol"] == "HON.US"][0]
        self.assertIn("FRACTION", hon["flags"])
        self.assertIn("consolidation", "\n".join(render_splits(items)))


if __name__ == "__main__":
    unittest.main()
