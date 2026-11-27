"""Audit 2026-10-24, group "inkind": B20, B43, B05, B16, A13, B36, B37,
B14 — in-kind moves, the corporate views, the holdings folder, the
holdings TOML reader and the [[distributions]] ADJUST. Synthetic data
only.

Formerly: test_fix_audit_1024_inkind"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.bin.taxjson_corp_actions import extractor_for


def _tx(d, q, sym="ZZQ.US", action="BUYSELL", **kw):
    return dict(date=d, date_settle=d, time="10:00:00", account="margin",
                symbol=sym, action=action, quantity=q, net_amount=0,
                currency="USD", **kw)


class TestCorpViews(unittest.TestCase):
    """Formerly: test_fix_audit_1024_inkind.TestCorpViews"""

    @rule(R.CA_DATE_14)
    def test_b05_held_applies_a_clock_stamped_split_at_the_open(self):
        """B05: Corporate holdings replay applies a late-stamped split
        after same-day trades — the view replays like the balance walk
        (a noon split scales yesterday's 10, not today's 5: 25, not 30)."""
        from taxjson.lib import corp_views as cv
        from taxjson.lib.unit_balance import balance_on
        rows = [_tx("2026-01-01", 10), _tx("2026-01-02", 5),
                _tx("2026-01-02", 2, action="SPLIT")]
        rows[-1]["time"] = "12:00:00"
        self.assertEqual(cv._held(rows, "ZZQ.US", "2026-01-02", True), 25.0)
        self.assertEqual(cv._held(rows, "ZZQ.US", "2026-01-02", True),
                         balance_on(rows, "ZZQ.US", "2026-01-02"))
        self.assertEqual(cv._held(rows, "ZZQ.US", "2026-01-02", False),
                         10.0)
        rows.append(_tx("2026-01-03", 1, action="SPLIT",
                        symbol_new="ZZR.US"))
        self.assertEqual(cv._held(rows, "ZZR.US", "9999-12-31", True),
                         25.0)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "margin_base.json").write_text(
                json.dumps({"transactions": rows[:3]}))
            s = cv.splits(root, {"accounts": {"margin": {"type": "taxable"}}})
            self.assertEqual((s[0]["held_before"], s[0]["held_after"]),
                             (10.0, 20.0))

    def test_b16_saved_zero_beside_a_broker_value_is_explained(self):
        """B16: Spin-off view replaces an explicit zero-value election
        with broker FMV. The booking uses the broker's value when the
        saved hint is 0: a 0 is what a user enters when they do not
        know the value, so the broker's figure wins (the user's
        decision, 2026-10-25; the booking is pinned by
        rules.ca.corp.test_event_copies_and_values.TestSpinoffBrokerValue.test_default_uses_broker_value).
        The view showed that value with no word about the saved 0; it
        now says why the 0 is not used. It is not a flag (nothing to
        fix) and offers no command (`elections --set` takes no
        fmv_per_share while the broker reports a value)."""
        from taxjson.lib import corp_views as cv
        ev = SimpleNamespace(action_type="spinoff", source_symbol="ZZQ.US",
                             target_symbol="ZZR.US", ratio=1, ratio_new=1,
                             ratio_old=1, old_count=1, target_fmv=100, qty_received=10,
                             currency="USD", date="2026-01-02")
        cfg = {"settings": {"country": "canada"},
               "accounts": {"margin": {"type": "taxable"}}}

        def view(hints):
            man = {"event": {"election": "taxable_deemed_dividend",
                             "hints": hints}}
            with patch.object(cv, "_current_events",
                              return_value={"event": ev}), \
                    patch.object(cv, "_manifest", return_value=man), \
                    patch.object(cv, "_rows", return_value=[]):
                return cv.spinoffs(Path("/synthetic"), cfg, extractor_for=extractor_for)
        doc = view({"fmv_per_share": 0})
        row = doc["spinoffs"][0]
        self.assertEqual((row["fmv_per_share"], row["value_from_broker"],
                          row["flags"]), (10.0, True, []))
        why = "\n".join(row["why"])
        self.assertIn("the saved fmv_per_share=0 is not used", why)
        self.assertIn("100.00 USD", why)
        self.assertNotIn("tjs elections", why)
        text = "\n".join(cv.render_spinoffs(doc))
        self.assertIn("the saved fmv_per_share=0 is not used", text)
        self.assertNotIn("HINT-UNUSED", text)
        # No hint saved: the broker's value is the default, no flag.
        row = view({})["spinoffs"][0]
        self.assertEqual((row["fmv_per_share"], row["flags"]), (10.0, []))


if __name__ == "__main__":
    unittest.main()
