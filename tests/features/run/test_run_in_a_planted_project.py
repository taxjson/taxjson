"""v0.17.0 pre-release security review pins (core side).

H1  install hints never name a PyPI package (taxjson / taxjson-fetch are
    not published there: a name-squatter's package would be installed).
M1  write-then-rename writers never write through a symlink planted at
    their temp name (`<out>.part`, `.migrate.part`) or at the final name.
L5  fill-crypto quotes the symbol into the Yahoo URL and closes the
    response.
I1  the [fx] extra's comment dates the Yahoo fallback like the code.

(L6, TAXJSON_OFFLINE stopping `tjs fetch`: test_fetch_dispatch.py.)
Synthetic data only; nothing here touches the network.

Formerly: test_fix_prerelease_security
"""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

VICTIM = "victim: must never change\n"


# ----------------------------------------------------------------- M1

class _Victims(unittest.TestCase):
    """A temp dir holding an 'outside' folder of victim files."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()

    def victim(self, name):
        v = self.outside / name
        v.write_text(VICTIM)
        return v

    def plant(self, link: Path, name=None):
        v = self.victim(name or (link.name + ".victim"))
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(v)
        return v

    def assertUntouched(self, *victims):
        for v in victims:
            self.assertFalse(v.is_symlink(), v)
            self.assertEqual(v.read_text(), VICTIM, v)


def _rates(path: Path, home: Path):
    """The rates file, and the same rates in `home`'s currency cache: a
    run that rebuilds the file keeps them (backlog TJ-0635)."""
    from _planted_rates import plant
    d, lines = date(2024, 1, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.3500 boc")
        d += timedelta(days=1)
    plant(path, "\n".join(lines) + "\n", home)


class TestRunInAPlantedProject(_Victims):
    """`tjs run` over a synthetic project whose work/ and reports/
    hold a symlink at EVERY temp name (and at the final names) pointing
    outside the project: nothing outside changes.

    Formerly: test_fix_prerelease_security.TestRunInAPlantedProject"""

    def _cli(self, root, *a):
        env = {**os.environ, "HOME": str(self.tmp / "home"),
               "TAXJSON_OFFLINE": "1", "NO_COLOR": "1",
               "PYTHONPATH": str(SRC)}
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), *a], cwd=str(root), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, env=env)

    def test_run_never_writes_outside_the_project(self):
        (self.tmp / "home").mkdir()
        root = self.tmp / "proj"
        (root / "inputs" / "m").mkdir(parents=True)
        (root / "inputs" / "m" / "a.tt").write_text(
            "BUYSELL 2025-01-06 10:00:00 ABC.TO 100 CAD 10 -1000 0\n"
            "BUYSELL 2025-03-03 10:00:00 ABC.TO -40 CAD 12 480 0\n")
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\nprovince = "ON"\n'
            'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
            'option_grant_timing_since = 2025\n\n'
            '[accounts.m]\ntype = "taxable"\n')
        _rates(root / "work" / "to_base.csv", self.tmp / "home")
        r = self._cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        built = sorted(p for d in ("work", "reports")
                       for p in (root / d).rglob("*")
                       if p.is_file() and p.name != "to_base.csv"
                       and not p.name.endswith(".lock"))
        self.assertTrue(any(p.name == "m.sum" for p in built), built)
        victims = []
        for i, p in enumerate(built):
            victims.append(self.plant(p.with_name(p.name + ".part"),
                                      f"{i}.part.victim"))
            p.unlink()
            victims.append(self.plant(p, f"{i}.final.victim"))
        victims.append(self.plant(root / "work" / "m_holdings_prev.toml",
                                  "holdings_prev.victim"))
        # A fresh input forces every stage to rebuild.
        with open(root / "inputs" / "m" / "a.tt", "a") as f:
            f.write("BUYSELL 2025-04-01 10:00:00 ABC.TO -10 CAD 11 110 0\n")
        r = self._cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertUntouched(*victims)
        self.assertFalse((root / "reports" / "m.sum").is_symlink())
        self.assertIn("ABC.TO", (root / "reports" / "m.sum").read_text())


if __name__ == "__main__":
    unittest.main()
