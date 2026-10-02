"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _rates(path: Path, src: str, dst: str, rate: str):
    d = date(2025, 1, 1)
    lines = []
    while d <= date.today():
        lines.append(f"{d.isoformat()} 12:00:00 {src} {dst} {rate} boc")
        d += timedelta(days=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _env(home, **kw):
    e = {**os.environ, "HOME": str(home), "TAXJSON_OFFLINE": "1",
         "NO_COLOR": "1", "PYTHONPATH": str(REPO_ROOT / "src")}
    e.update(kw)
    return e


def _cli(root, home, *a):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=_env(home))


def _tt_project(td, lines, *, country="canada", year=2026, accounts=None,
                extra_settings=""):
    """One taxable account `m` holding a.tt (or `accounts`: {name: (type,
    lines)}); FX rates for the project's foreign currency in work/."""
    root = Path(td) / f"proj-{country}"
    base, src = (("CAD", "USD") if country == "canada" else ("USD", "CAD"))
    accounts = accounts or {"m": ("taxable", lines)}
    cfg = (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
           + ('province = "ON"\n' if country == "canada" else "")
           + f'base_currency = "{base}"\nsource_currencies = ["{src}"]\n'
           + (f'option_grant_timing_since = {year}\n'
              if country == "canada" else "") + extra_settings)
    for name, (atype, ls) in accounts.items():
        cfg += f'\n[accounts.{name}]\ntype = "{atype}"\n'
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "a.tt").write_text("\n".join(ls) + "\n")
    (root / "taxjson.toml").write_text(cfg)
    _rates(root / "work" / "to_base.csv", src, base,
           "1.3500" if country == "canada" else "0.7400")
    home = Path(td) / "home"
    home.mkdir(exist_ok=True)
    return root, home


# ---------------------------------------------- A2-0010 (R1-126 regression)
class TestTtUsdSplitRuns(unittest.TestCase):
    """A .tt SPLIT is stamped CAD whatever the listing (kept so row ids
    stay stable). The native gains pass then refused a USD pool's split
    or rename with a currency mismatch and `run` exited 1."""

    def _check(self, country, lines, held_sym, held_qty):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, lines, country=country)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertNotIn("Currency mismatch", r.stderr)
            self.assertNotIn("raw holdings skipped", r.stderr)
            hold = (root / "reports" / "m_holdings.toml").read_text()
            self.assertIn(f'symbol = "{held_sym}"', hold)
            self.assertIn(f"quantity = {held_qty}", hold)
            self.assertIn('currency = "USD"', hold)

    def test_usd_split_canada(self):
        self._check("canada", [
            "BUYSELL 2026-01-05 10:00:00 OLDQ.US 10 USD 10 -100 0",
            "SPLIT 2026-06-11 20:25:00 OLDQ.US OLDQ.US 2.0",
            "BUYSELL 2026-08-03 10:00:00 OLDQ.US -5 USD 15 75 0"],
            "OLDQ.US", "15.0")

    def test_usd_rename_canada(self):
        self._check("canada", [
            "BUYSELL 2026-01-05 10:00:00 OLDQ.US 10 USD 10 -100 0",
            "SPLIT 2026-06-11 20:25:00 OLDQ.US NEWQ.US 1.0",
            "BUYSELL 2026-08-03 10:00:00 NEWQ.US -5 USD 15 75 0"],
            "NEWQ.US", "5.0")

    def test_usd_split_usa(self):
        self._check("usa", [
            "BUYSELL 2026-01-05 10:00:00 OLDQ 10 USD 10 -100 0",
            "SPLIT 2026-06-11 20:25:00 OLDQ OLDQ 2.0",
            "BUYSELL 2026-08-03 10:00:00 OLDQ -5 USD 15 75 0"],
            "OLDQ", "15.0")


if __name__ == "__main__":
    unittest.main()
