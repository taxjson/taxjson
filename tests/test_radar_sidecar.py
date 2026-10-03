"""Radar JSON sidecar.

The .rpt keeps its exact bytes; --json-out adds a structured twin with
ABSOLUTE clears_at dates, so a reader (harvest --radar) can compute
"clears in Nd" at its own time instead of the generation-day countdown.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
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
    cmd = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar", "--country", "canada",
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


def _project(tmp):
    root = Path(tmp)
    (root / "work").mkdir(exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\nbase_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\n')
    return root


if __name__ == "__main__":
    unittest.main()
