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


EV = "20240513-prnt-spnc-35b9"
SPIN_DESC = "Spinoff PRNT.US\u2192SPNC.US (deemed dividend at FMV)"
SUMMARY = ("2024-05-13 spinoff: PRNT.US → SPNC.US (20.0-for-80.0, ratio "
           "0.25, FMV 830.00 USD)")


class TestSpinoffs(unittest.TestCase):
    def test_zero_value_flagged_with_broker_value(self):
        rows = [_row("BUYSELL", "2024-01-02", "PRNT.US", 80, 13000),
                _row("DIVIDEND", "2024-05-13", "SPNC.US", 0, 0.0,
                     corp_event_id=EV, description=SPIN_DESC),
                _row("BUYSELL", "2024-05-13", "SPNC.US", 20, 0.0,
                     corp_event_id=EV, description=SPIN_DESC)]
        # No value saved (a fmv_per_share=0 written by the user is a
        # declared $0, not a flag: test_fix_quietdeclared).
        td, root, cfg = _proj(rows, {EV: {
            "election": "taxable_deemed_dividend",
            "hints": {}, "summary": SUMMARY}})
        with td:
            doc = spinoffs(root, cfg)
        items = doc["spinoffs"]
        self.assertEqual(len(items), 1)
        s = items[0]
        self.assertEqual((s["parent"], s["child"]), ("PRNT.US", "SPNC.US"))
        self.assertEqual(s["flags"], ["ZERO-VALUE"])
        self.assertEqual(s["broker_fmv"], 0.0)   # no current broker event
        self.assertIn("ZERO-VALUE", "\n".join(render_spinoffs(doc)))
        td, root, cfg = _proj(rows, {EV: {
            "election": "taxable_deemed_dividend",
            "hints": {"fmv_per_share": 0.0}, "summary": SUMMARY}})
        with td:
            doc = spinoffs(root, cfg)
        self.assertEqual(doc["spinoffs"][0]["flags"], [])
        self.assertIn("the $0 value you declared",
                      "\n".join(render_spinoffs(doc)))

    def test_valued_and_sheltered(self):
        rows = [_row("DIVIDEND", "2024-05-13", "SPNC.US", 0, 830.0,
                     corp_event_id=EV, description=SPIN_DESC),
                _row("BUYSELL", "2024-05-13", "SPNC.US", 20, 830.0,
                     corp_event_id=EV, description=SPIN_DESC)]
        td, root, cfg = _proj(rows, {EV: {
            "election": "taxable_deemed_dividend",
            "hints": {"fmv_per_share": 41.5}, "summary": SUMMARY}})
        with td:
            s = spinoffs(root, cfg)["spinoffs"][0]
        self.assertEqual(s["flags"], [])
        self.assertAlmostEqual(s["income"], 830.0)
        td, root, cfg = _proj(rows, {EV: {
            "election": "taxable_deemed_dividend",
            "hints": {"fmv_per_share": 0.0}, "summary": SUMMARY}},
            sheltered=True)
        with td:
            self.assertEqual(spinoffs(root, cfg)["spinoffs"][0]["flags"], [])

    def test_stale_election_listed_separately(self):
        rows = [_row("BUYSELL", "2024-05-13", "SPNC.US", 20, 0.0,
                     corp_event_id=EV,
                     description="Spinoff PRNT.US\u2192SPNC.US (deemed)")]
        old = "20240513-x-y-0000"
        td, root, cfg = _proj(rows, {
            EV: {"election": "taxable_deemed_dividend",
                 "hints": {"fmv_per_share": 1.0}, "summary": SUMMARY},
            old: {"election": "taxable_deemed_dividend",
                  "hints": {"fmv_per_share": 0.0},
                  "summary": "2024-05-13 spinoff: X \u2192 Y (1-for-1)"}})
        with td:
            doc = spinoffs(root, cfg)
        self.assertEqual([s["event_id"] for s in doc["spinoffs"]], [EV])
        self.assertEqual(doc["spinoffs"][0]["parent"], "PRNT.US")
        self.assertEqual([s["event_id"] for s in doc["stale"]], [old])
        self.assertIn("--reset --event " + old,
                      "\n".join(render_spinoffs(doc)))


class TestSplits(unittest.TestCase):
    def test_before_after_and_flags(self):
        rows = [_row("BUYSELL", "2024-01-02", "SPLT.US", 40, 1000),
                _row("SPLIT", "2024-06-17", "SPLT.US", 10.0),
                _row("SPLIT", "2024-06-20", "SPLT.US", 10.0),   # 2 sources
                _row("BUYSELL", "2023-01-02", "QHN.US", 15, 1000),
                _row("SPLIT", "2026-03-09", "QHN.US", 0.5),      # fraction
                _row("BUYSELL", "2023-01-02", "QTW.US", 10, 1000),
                _row("SPLIT", "2025-02-03", "QTW.US", 2.0),
                _row("SPLIT", "2025-02-12", "QTW.US", 2.0)]      # 9 days
        td, root, cfg = _proj(rows)
        with td:
            items = splits(root, cfg)
        nv = [i for i in items if i["symbol"] == "SPLT.US"]
        self.assertEqual(nv[0]["held_before"], 40)
        self.assertEqual(nv[0]["held_after"], 400)
        # Copies within a week are ONE event, applied once (GitHub #92,
        # CA-CORP-12): the second copy changes nothing, and the view no
        # longer says "applied twice".
        self.assertIn("TWO-SOURCES", nv[0]["flags"])
        self.assertNotIn("TWICE?", nv[0]["flags"])
        self.assertEqual((nv[1]["held_before"], nv[1]["held_after"]),
                         (400, 400))
        self.assertIn("applied ONCE, on 2024-06-17", " ".join(nv[1]["why"]))
        # Further apart they are two events: both apply, flagged.
        tw = [i for i in items if i["symbol"] == "QTW.US"]
        self.assertIn("TWICE?", tw[0]["flags"])
        self.assertEqual(tw[1]["held_after"], 40)
        qhn = [i for i in items if i["symbol"] == "QHN.US"][0]
        self.assertIn("FRACTION", qhn["flags"])
        self.assertIn("consolidation", "\n".join(render_splits(items)))


if __name__ == "__main__":
    unittest.main()
