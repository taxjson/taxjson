"""Re-audit-2 fixes, corp-actions-02 helper list (views, corporate
timeline ordering, run --fast fingerprint, elect --redo, manifest
migration). Synthetic data only."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib.corp_views import (ViewError, render_spinoffs, render_splits,
                                    spinoffs, splits)


def _proj(rows, elections=None, sheltered=False, country="canada",
          manifest_text=None):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "work").mkdir()
    (root / "inputs" / "m").mkdir(parents=True)
    (root / "work" / "m_base.json").write_text(json.dumps(rows))
    (root / "inputs" / "m" / "manifest.json").write_text(
        manifest_text if manifest_text is not None
        else json.dumps({"elections": elections or {}}))
    cfg = {"settings": {"base_currency": "USD" if country == "usa"
                        else "CAD", "country": country},
           "accounts": {"m": {"type": "sheltered" if sheltered
                              else "taxable"}}}
    return td, root, cfg


def _row(action, date, sym, qty, net=0.0, **kw):
    d = {"action": action, "date": date, "time": "09:30:00", "symbol": sym,
         "quantity": qty, "net_amount": net}
    d.update(kw)
    return d


EV = "20250415-parn-spnc-1dc1"
DESC = "Spinoff PARN.US→SPNC.US (x)"


def _spin_rows(net):
    return [_row("BUYSELL", "2025-01-02", "PARN.US", 100, 5000),
            _row("DIVIDEND", "2025-04-15", "SPNC.US", 0, net,
                 corp_event_id=EV, description=DESC),
            _row("BUYSELL", "2025-04-15", "SPNC.US", 10, net,
                 corp_event_id=EV, description=DESC)]


class TestSpinoffsViewUs(unittest.TestCase):
    """A2-0063 / A2-0221: the view knew only the Canadian keys."""

    @rule("US-CORP-06")
    def test_a2_0063_us_301_zero_value_flagged(self):
        td, root, cfg = _proj(_spin_rows(0.0), {EV: {
            "election": "taxable_distribution_301",
            "hints": {"fmv_per_share": 0.0}}}, country="usa")
        with td:
            doc = spinoffs(root, cfg)
        s = doc["spinoffs"][0]
        self.assertEqual(s["flags"], ["ZERO-VALUE"])
        text = "\n".join(render_spinoffs(doc))
        self.assertIn("§301", text)
        self.assertIn("=taxable_distribution_301 --hint fmv_per_share", text)
        self.assertNotIn("Every taxable spin-off is booked", text)

    @rule("US-CORP-07")
    def test_a2_0221_us_355_no_allocation_flagged(self):
        rows = [_row("BUYSELL", "2025-01-02", "PARN.US", 100, 5000),
                _row("BUYSELL", "2025-04-15", "SPNC.US", 10, 0.0,
                     corp_event_id=EV, description=DESC)]
        for hints in ({}, {"allocated_acb": 0.0}):
            td, root, cfg = _proj(rows, {EV: {
                "election": "tax_free_355", "hints": hints}}, country="usa")
            with td:
                doc = spinoffs(root, cfg)
            s = doc["spinoffs"][0]
            self.assertEqual(s["flags"], ["NO-ALLOCATION"], hints)
            text = "\n".join(render_spinoffs(doc))
            self.assertIn("§355", text)
            self.assertIn("allocated_acb=<amount>", text)
            self.assertNotIn("allocated_acb_cad", text)
            self.assertNotIn("s.86.1", text)

    @rule("US-CORP-07")
    def test_us_355_with_allocation_clean_and_stale_detected(self):
        rows = [_row("BUYSELL", "2025-04-15", "SPNC.US", 10, 400.0,
                     corp_event_id=EV, description=DESC)]
        td, root, cfg = _proj(rows, {
            EV: {"election": "tax_free_355",
                 "hints": {"allocated_acb": 400.0}},
            "20240101-old-gone-0000": {"election":
                                       "taxable_distribution_301"}},
            country="usa")
        with td:
            doc = spinoffs(root, cfg)
        self.assertEqual(doc["spinoffs"][0]["flags"], [])
        # the real US key is recognised as a spin-off election when stale
        self.assertEqual([s["event_id"] for s in doc["stale"]],
                         ["20240101-old-gone-0000"])

    @rule("US-CORP-06")
    def test_us_ira_wording(self):
        td, root, cfg = _proj(_spin_rows(0.0), {EV: {
            "election": "taxable_distribution_301",
            "hints": {"fmv_per_share": 0.0}}}, country="usa",
            sheltered=True)
        with td:
            s = spinoffs(root, cfg)["spinoffs"][0]
        self.assertEqual(s["flags"], [])
        self.assertIn("tax-advantaged account (IRA): no tax effect",
                      s["why"])
        self.assertNotIn("registered account: no tax effect", s["why"])

    @rule("CA-CORP-06")
    def test_canada_wording_unchanged(self):
        td, root, cfg = _proj(_spin_rows(0.0), {EV: {
            "election": "rollover_s_86_1", "hints": {}}}, sheltered=True)
        with td:
            s = spinoffs(root, cfg)["spinoffs"][0]
        self.assertIn("registered account: no tax effect", s["why"])
        self.assertIn("NO-ALLOCATION", s["flags"])
        self.assertTrue(any("allocated_acb_cad" in w for w in s["why"]))


class TestViewsRefuseUnreadable(unittest.TestCase):
    """A2-0968 / A2-0969: unreadable inputs read as 'nothing here'."""

    def test_a2_0968_wrong_shape_manifest_one_line_error(self):
        for text in ("[1, 2]", '"x"', '{"elections": [1]}', '{"elec'):
            td, root, cfg = _proj(_spin_rows(10.0), manifest_text=text)
            with td, self.assertRaises(ViewError) as cm:
                spinoffs(root, cfg)
            self.assertIn("manifest", str(cm.exception), text)

    def test_a2_0968_unreadable_manifest_refused(self):
        td, root, cfg = _proj(_spin_rows(10.0))
        with td:
            p = root / "inputs" / "m" / "manifest.json"
            p.unlink()
            p.mkdir()                       # a directory, not a file
            with self.assertRaises(ViewError):
                spinoffs(root, cfg)
            p.rmdir()
            p.write_bytes(b'{"elections": {"\xff": {}}}')   # not UTF-8
            with self.assertRaises(ViewError):
                spinoffs(root, cfg)

    def test_missing_manifest_is_no_elections(self):
        td, root, cfg = _proj(_spin_rows(10.0))
        with td:
            (root / "inputs" / "m" / "manifest.json").unlink()
            s = spinoffs(root, cfg)["spinoffs"][0]
        self.assertEqual(s["flags"], ["PENDING"])   # booked, no record

    def test_a2_0969_truncated_base_refused(self):
        rows = [_row("BUYSELL", "2025-01-02", "XYZ.TO", 100, 1000),
                _row("SPLIT", "2025-03-03", "XYZ.TO", 2.0)]
        td, root, cfg = _proj(rows)
        with td:
            self.assertEqual(len(splits(root, cfg)), 1)
            base = root / "work" / "m_base.json"
            base.write_text(base.read_text()[:20])
            with self.assertRaises(ViewError) as cm:
                splits(root, cfg)
            self.assertIn("m_base.json", str(cm.exception))
            base.unlink()                   # never built: just empty
            self.assertIn("No splits in the books.",
                          render_splits(splits(root, cfg)))

    def test_a2_0979_corrupt_manifest_advice(self):
        from taxjson.lib.corp_actions import Manifest, ManifestError
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "manifest.json"
            p.write_text('{"elections": {"a": ')
            with self.assertRaises(ManifestError) as cm:
                Manifest.load(p)
        msg = str(cm.exception)
        self.assertNotIn("--manifest", msg)
        self.assertNotIn("start fresh", msg)
        self.assertIn("restore", msg)
        self.assertIn("Do not delete", msg)


if __name__ == "__main__":
    unittest.main()
