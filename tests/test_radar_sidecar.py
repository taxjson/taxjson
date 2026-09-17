"""Radar JSON sidecar + web view-time countdowns + staleness banner data.

The .rpt keeps its exact bytes; --json-out adds a structured twin with
ABSOLUTE clears_at dates so the web UI computes "clears in Nd" at view
time instead of serving the generation-day countdown forever.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tx(action, dt, symbol, qty, net, time_="09:30:00"):
    return dict(action=action, date=dt, time=time_, symbol=symbol,
                quantity=qty, net_amount=net, currency="CAD", account="a")


# Loss on 2026-06-02, still in window on 2026-06-15 and fully exited →
# COOLING with a definite clears_at.
_TAXABLE = [
    _tx("BUYSELL", "2026-01-05", "WSP.TO", 50, 5000.0),
    _tx("BUYSELL", "2026-06-02", "WSP.TO", -50, 4000.0),
]


def _run_radar(tmp, json_out=None):
    t = Path(tmp) / "t.json"
    t.write_text(json.dumps({"transactions": _TAXABLE}))
    cmd = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
           "--taxable", str(t), "--date", "2026-06-15"]
    if json_out:
        cmd += ["--json-out", str(json_out), "--account", "margin"]
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                       env={**os.environ,
                            "PYTHONPATH": str(REPO_ROOT / "src")})
    assert r.returncode == 0, r.stderr
    return r.stdout


class TestSidecar(unittest.TestCase):
    def test_rpt_bytes_unchanged_and_json_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = _run_radar(tmp)
            sidecar = Path(tmp) / "wash_radar_margin.json"
            with_json = _run_radar(tmp, json_out=sidecar)
            self.assertEqual(plain, with_json)      # .rpt text untouched

            doc = json.loads(sidecar.read_text())
            self.assertEqual(doc["schema_version"], 1)
            self.assertEqual(doc["account"], "margin")
            self.assertEqual(doc["as_of_date"], "2026-06-15")
            cats = [s["category"] for s in doc["sections"]]
            self.assertEqual(cats[:8], ["VIOLATION", "BLOCKED", "LOCKED",
                                        "EXITABLE", "CAUTION",
                                        "COOLING", "RISK", "CLEAR"])
            rows = [r for s in doc["sections"] for r in s["rows"]]
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual(r["ticker"], "WSP.TO")
            self.assertEqual(r["category"], "COOLING")
            # Absolute date, and it matches the date quoted in the prose.
            self.assertRegex(r["clears_at"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertIn(r["clears_at"], r["advisory"])
            # "DATE (Nd)" — same shape as the report's CLEARS column.
            self.assertRegex(r["clears_in_at_generation"],
                             r"^\d{4}-\d{2}-\d{2} \(\d+d\)$")


class TestViewTimeCountdown(unittest.TestCase):
    def test_countdown_math(self):
        from taxjson.web.data import _clears_in_display
        today = date(2026, 6, 20)
        self.assertEqual(_clears_in_display("2026-06-25", today),
                         "2026-06-25 (5d)")
        self.assertEqual(_clears_in_display("2026-06-20", today), "cleared")
        self.assertEqual(_clears_in_display("2026-06-01", today), "cleared")
        self.assertEqual(_clears_in_display(None, today), "-")
        self.assertEqual(_clears_in_display("garbage", today), "-")


def _project(tmp):
    root = Path(tmp)
    (root / "work").mkdir(exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\nbase_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\n')
    return root


class TestWebSectionsPreferJson(unittest.TestCase):
    def _sidecar(self):
        return {
            "schema_version": 1, "generated_at": "2026-06-15T12:00:00",
            "as_of_date": "2026-06-15", "account": "margin",
            "include_all": False,
            "sections": [
                {"category": "COOLING", "title": "COOLING", "rows": [{
                    "ticker": "WSP.TO", "taxable_qty": 0.0,
                    "taxable_display": "0", "sheltered_qty": 0.0,
                    "sheltered_display": "0", "clears_at": "2026-07-03",
                    "clears_in_at_generation": "18d",
                    "advisory": "COOLING: ...", "category": "COOLING"}]},
                {"category": "CLEAR", "title": "No risk", "rows": []},
            ],
        }

    def test_json_preferred_with_live_countdown(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web.data import wash_radar_sections
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "reports" / "wash_radar_margin.json").write_text(
                json.dumps(self._sidecar()))
            # A conflicting stale .rpt must be ignored when JSON exists.
            (root / "reports" / "wash_radar_margin.rpt").write_text(
                "--- COOLING (1) ---\n"
                "WSP.TO | 0 | 0 | 18d | COOLING: stale text\n")
            ctx = ProjectContext.load(root)
            secs = wash_radar_sections(ctx, "margin",
                                       today=date(2026, 6, 30))
        self.assertEqual(len(secs), 1)          # empty CLEAR section dropped
        row = secs[0]["rows"][0]
        # Live view-time countdown (NOT the stale generation-day 18d),
        # in the aligned "DATE (Nd)" shape.
        self.assertEqual(row["clears_in"], "2026-07-03 (3d)")
        self.assertEqual(secs[0]["title"], "COOLING (1)")

    def test_rpt_fallback_when_no_json(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web.data import wash_radar_sections
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "reports" / "wash_radar_margin.rpt").write_text(
                "--- COOLING (1) -----\n"
                "TICKER | TAXABLE | SHELTERED | CLEARS_IN | ADVISORY\n"
                "-------+---------+-----------+-----------+---------\n"
                "WSP.TO | 0       | 0         | 18d       | COOLING: x\n")
            ctx = ProjectContext.load(root)
            secs = wash_radar_sections(ctx, "margin")
        self.assertEqual(secs[0]["rows"][0]["clears_in"], "18d")


class TestFreshness(unittest.TestCase):
    def test_states(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web.data import freshness
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            ctx = ProjectContext.load(root)
            self.assertIsNone(freshness(ctx))        # no reports yet

            (root / "inputs" / "margin").mkdir(parents=True)
            csv = root / "inputs" / "margin" / "jan.csv"
            csv.write_text("a,b\n")
            rpt = root / "reports" / "margin.sum"
            rpt.write_text("SUMMARY\n")
            now = time.time()
            os.utime(csv, (now - 100, now - 100))
            os.utime(root / "taxjson.toml", (now - 100, now - 100))
            os.utime(rpt, (now, now))
            self.assertFalse(freshness(ctx)["stale"])   # reports newest

            os.utime(csv, (now + 100, now + 100))
            self.assertTrue(freshness(ctx)["stale"])    # input changed since


if __name__ == "__main__":
    unittest.main()
