"""Elections UX audit fixes (2026-07-27): --set hint validation, honest
warnings, pending-status annotation, filing reminder, GUI guardrails."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PENDING = {"accounts": {"margin": {"pending": [
    {"event_id": "20251022-ssl-rgld-51d7",
     "summary": "2025-10-22 merger: SSL.TO → RGLD.US (1-for-16)",
     "qty_disposed": 1600.0, "qty_received": 100.0,
     "options": [
         {"election": "taxable_disposition", "description": "sale now",
          "hints": []},
         {"election": "rollover_s_85_1_5", "description": "defer",
          "hints": [{"key": "fmv_per_share", "prompt": "FMV/share"}]}]}
]}}}


def _proj(tmp):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "pending_elections.json").write_text(
        json.dumps(_PENDING))
    return root


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
         str(root), *args], cwd=REPO_ROOT, capture_output=True,
        text=True)


class TestElectSetHints(unittest.TestCase):
    EV = "20251022-ssl-rgld-51d7"

    def test_missing_required_hint_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_proj(tmp), "elect", "margin", "--set",
                     f"{self.EV}=rollover_s_85_1_5")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs fmv_per_share", r.stderr)

    def test_unknown_hint_key_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_proj(tmp), "elect", "margin", "--set",
                     f"{self.EV}=taxable_disposition",
                     "--hint", "fmv_pershare=12.5")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not use hint", r.stderr)

    def test_valid_set_uses_pending_summary_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _proj(tmp)
            r = _run(root, "elect", "margin", "--set",
                     f"{self.EV}=taxable_disposition")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("not in the manifest", r.stderr)
            self.assertNotIn("matches no pending", r.stderr)
            rec = json.loads((root / "inputs" / "margin" /
                              "manifest.json").read_text())
            self.assertIn("merger: SSL.TO",
                          rec["elections"][self.EV]["summary"])
            # --pending now marks it as already elected.
            r2 = _run(root, "elect", "--pending")
            self.assertIn("already elected: taxable_disposition",
                          r2.stdout)

    def test_bogus_id_warns_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(_proj(tmp), "elect", "margin", "--set",
                     "deadbeef=taxable_disposition")
        self.assertIn("matches no pending event", r.stderr)


class TestFilingReminder(unittest.TestCase):
    def test_rollover_reminder_after_run(self):
        try:
            from tests.test_pending_elections import _SSL_RGLD_CSV
        except ImportError:
            from test_pending_elections import _SSL_RGLD_CSV
        with tempfile.TemporaryDirectory() as tmp:
            root = _proj(tmp)
            (root / "inputs" / "margin" / "ib.csv").write_text(
                _SSL_RGLD_CSV)
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3)
            pend = json.loads((root / "work" /
                               "pending_elections.json").read_text())
            evs = pend["accounts"]["margin"]["pending"]
            for ev in evs:
                opts = {o["election"] for o in ev["options"]}
                choice = ("rollover_s_85_1_5"
                          if "rollover_s_85_1_5" in opts else "ignore")
                declared = next((o.get("hints") or [] for o in
                                 ev["options"]
                                 if o["election"] == choice), [])
                hint = [x for h in declared
                        for x in ("--hint", f"{h['key']}=16.15")]
                r = _run(root, "elect", "margin", "--set",
                         f"{ev['event_id']}={choice}", *hint)
                self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FILING REQUIRED", r.stderr)
        self.assertIn("s. 85.1(5)", r.stderr)

    def test_no_reminder_for_sheltered_account(self):
        # A rollover inside a registered plan has no gain to defer, so
        # there is nothing to file with CRA — the reminder must stay
        # quiet (2026-08-02: it fired for an rrsp account).
        try:
            from tests.test_pending_elections import _SSL_RGLD_CSV
        except ImportError:
            from test_pending_elections import _SSL_RGLD_CSV
        with tempfile.TemporaryDirectory() as tmp:
            root = _proj(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "sheltered"\n')
            (root / "inputs" / "margin" / "ib.csv").write_text(
                _SSL_RGLD_CSV)
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3)
            pend = json.loads((root / "work" /
                               "pending_elections.json").read_text())
            evs = pend["accounts"]["margin"]["pending"]
            for ev in evs:
                opts = {o["election"] for o in ev["options"]}
                choice = ("rollover_s_85_1_5"
                          if "rollover_s_85_1_5" in opts else "ignore")
                declared = next((o.get("hints") or [] for o in
                                 ev["options"]
                                 if o["election"] == choice), [])
                hint = [x for h in declared
                        for x in ("--hint", f"{h['key']}=16.15")]
                r = _run(root, "elect", "margin", "--set",
                         f"{ev['event_id']}={choice}", *hint)
                self.assertEqual(r.returncode, 0, r.stderr)
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("FILING REQUIRED", r.stderr)


if __name__ == "__main__":
    unittest.main()
