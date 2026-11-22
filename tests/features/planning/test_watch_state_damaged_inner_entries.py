"""Re-audit-2 errors (errA-d): one-line errors instead of tracebacks in the
checklist / watch state, close-year / handoff, export,
some parsers and taxjson-sum-income.

Formerly: test_fix_a2_errA_d"""
import json
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestWatchStateInner(unittest.TestCase):
    """A2-1430 (watch half): wrong-shape inner entries re-baseline.

    Formerly: test_fix_a2_errA_d.TestWatchStateInner"""

    def test_bad_inner_rebaselines(self):
        from taxjson.bin import taxjson_watch as w
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".watch_state.json"
            for radar, hn in (([1], None), ({"SHOP.TO": "x"}, None),
                              ({"SHOP.TO": {"category": 5}}, None),
                              ({}, "abc"), ({}, True)):
                doc = {"schema_version": w.STATE_VERSION, "as_of": "x",
                       "radar": radar}
                if hn is not None:
                    doc["harvest_now"] = hn
                p.write_text(json.dumps(doc))
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertIsNone(w.load_state(p), (radar, hn))
                self.assertIn("NEW baseline", err.getvalue())

    def test_good_state_kept(self):
        from taxjson.bin import taxjson_watch as w
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".watch_state.json"
            doc = {"schema_version": w.STATE_VERSION, "as_of": "x",
                   "radar": {"SHOP.TO": {"category": "LOCKED",
                                         "advisory": "a",
                                         "clears_at": "2025-01-01"}},
                   "harvest_now": -12.5}
            p.write_text(json.dumps(doc))
            self.assertEqual(w.load_state(p)["harvest_now"], -12.5)


if __name__ == "__main__":
    unittest.main()
