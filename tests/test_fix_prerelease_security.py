"""v0.17.0 pre-release security review pins (core side).

H1  install hints never name a PyPI package (taxjson / taxjson-fetch are
    not published there: a name-squatter's package would be installed).
M1  write-then-rename writers never write through a symlink planted at
    their temp name (`<out>.part`, `.migrate.part`) or at the final name.
L5  fill-crypto quotes the symbol into the Yahoo URL and closes the
    response.
I1  the [fx] extra's comment dates the Yahoo fallback like the code.

(L6, TAXJSON_OFFLINE stopping `taxjson fetch`: test_fetch_dispatch.py.)
Synthetic data only; nothing here touches the network.
"""
import io
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

VICTIM = "victim: must never change\n"


# ----------------------------------------------------------------- H1

_PYPI_INSTALL = re.compile(
    r"pip3? install (?:-U |--upgrade |--user |--pre )*['\"]?taxjson")
_QUOTED_EXTRA = re.compile(r"['\"`]taxjson\[")


def _hint_files():
    """Every shipped text a user reads an install line in (tests aside;
    install.sh, docs/releasing.md and scripts/release.sh belong to the
    release tooling and carry no PyPI hint)."""
    skip_dirs = {".git", "venv", ".venv", "tests", "__pycache__", ".ci",
                 "node_modules", "build", "dist"}
    skip = {"install.sh", "releasing.md", "release.sh"}
    for top in ("src", "packages", "docs", "scripts"):
        for dp, dns, fns in os.walk(REPO_ROOT / top):
            dns[:] = [d for d in dns if d not in skip_dirs
                      and not d.endswith(".egg-info")]
            for fn in fns:
                if fn in skip or not fn.endswith(
                        (".py", ".md", ".sh", ".html", ".toml", ".txt")):
                    continue
                yield Path(dp) / fn
    for fn in ("README.md", "CONTRIBUTING.md", "SECURITY.md",
               "KNOWN_ISSUES.md", "pyproject.toml", "setup.sh"):
        if (REPO_ROOT / fn).is_file():
            yield REPO_ROOT / fn


def _unreleased_changelog():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    head, _sep, _rest = text.partition("\n## v")
    return head


class TestNoPyPIInstallHints(unittest.TestCase):
    def test_fetch_install_hint_names_the_installer_and_the_checkout(self):
        from taxjson.lib.fetchers import INSTALL_HINT
        self.assertNotRegex(INSTALL_HINT, _PYPI_INSTALL)
        self.assertIn("--with-fetch", INSTALL_HINT)
        self.assertIn("https://taxjson.com/install.sh", INSTALL_HINT)
        self.assertIn("pip install -e packages/taxjson-fetch", INSTALL_HINT)
        self.assertIn("not published on PyPI", INSTALL_HINT)
        self.assertNotIn("\n", INSTALL_HINT)          # still one line

    def test_extra_hints_never_point_at_pypi(self):
        from taxjson.lib.install_hint import extra_hint
        for extra in ("fx", "ibkr", "xlsx"):
            h = extra_hint(extra)
            self.assertNotRegex(h, _PYPI_INSTALL)
            self.assertNotRegex(h, _QUOTED_EXTRA)
            self.assertIn(f"pip install -e '.[{extra}]'", h)
            self.assertIn("not published on PyPI", h)
            self.assertIn("TAXJSON_EXTRAS=", h)

    def test_no_shipped_text_installs_taxjson_from_pypi(self):
        hits = []
        for p in _hint_files():
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if _PYPI_INSTALL.search(line) or _QUOTED_EXTRA.search(line):
                    hits.append(f"{p.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
        for n, line in enumerate(_unreleased_changelog().splitlines(), 1):
            if _PYPI_INSTALL.search(line):
                hits.append(f"CHANGELOG.md:{n}: {line.strip()}")
        self.assertEqual(hits, [])

    def test_readmes_say_taxjson_is_not_on_pypi(self):
        for rel in ("README.md", "packages/taxjson-fetch/README.md"):
            text = " ".join((REPO_ROOT / rel).read_text(
                encoding="utf-8").split())
            self.assertIn("not published on PyPI", text, rel)
            self.assertIn("--with-fetch", text, rel)


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


class TestSafeWrite(_Victims):
    def test_planted_temp_symlink_is_not_followed(self):
        from taxjson.lib.safe_write import write_atomic
        out = self.tmp / "proj" / "work" / "m_base.json"
        v = self.plant(out.with_name(out.name + ".part"))
        write_atomic(out, "new\n")
        self.assertUntouched(v)
        self.assertEqual(out.read_text(), "new\n")
        self.assertFalse(out.is_symlink())
        self.assertFalse(os.path.lexists(out.with_name(out.name + ".part")))
        self.assertEqual(stat.S_IMODE(out.stat().st_mode) & 0o077, 0)

    def test_symlink_at_the_final_name_is_replaced_not_written_through(self):
        from taxjson.lib.safe_write import write_atomic
        out = self.tmp / "proj" / "reports" / "m.sum"
        v = self.plant(out)
        write_atomic(out, b"bytes\n")
        self.assertUntouched(v)
        self.assertFalse(out.is_symlink())
        self.assertEqual(out.read_bytes(), b"bytes\n")

    def test_failed_write_keeps_the_old_file_and_leaves_no_temp(self):
        from taxjson.lib.safe_write import atomic_open
        out = self.tmp / "f.txt"
        out.write_text("old\n")
        with self.assertRaises(RuntimeError):
            with atomic_open(out) as f:
                f.write("half")
                raise RuntimeError("boom")
        self.assertEqual(out.read_text(), "old\n")
        self.assertFalse(os.path.lexists(self.tmp / "f.txt.part"))

    def test_cli_diag_write_text_atomic(self):
        from taxjson.lib.cli_diag import write_text_atomic
        out = self.tmp / "taxjson.toml"
        v = self.plant(out.with_name(out.name + ".part"))
        write_text_atomic(out, "[settings]\n")
        self.assertUntouched(v)
        self.assertEqual(out.read_text(), "[settings]\n")

    def test_run_to_file_stage_writer(self):
        from taxjson.bin import taxjson_run as R
        out = self.tmp / "proj" / "work" / "m_raw.json"
        v1 = self.plant(out.with_name(out.name + ".part"))
        v2 = self.plant(out)
        R.run_to_file([sys.executable, "-c", "print('stage output')"], out)
        self.assertUntouched(v1, v2)
        self.assertFalse(out.is_symlink())
        self.assertEqual(out.read_text(), "stage output\n")

    def test_other_part_writers(self):
        from taxjson.bin import taxjson_watch as W
        from taxjson.lib import checklist as CL
        proj = self.tmp / "proj"
        (proj / "work").mkdir(parents=True)
        vs = [self.plant(proj / "work" / "watch.json.part"),
              self.plant(proj / "work" / (CL.FINGERPRINT_FILE + ".part")),
              self.plant(proj / f"{CL.STATE_FILE}.{os.getpid()}.part")]
        W.save_state(proj / "work" / "watch.json", {}, None, "2025-01-02")
        CL.record_input_fingerprint(proj, {"settings": {
            "year": 2025, "country": "canada"}, "accounts": {}})
        CL.save_state(proj, {}, 2025)
        self.assertUntouched(*vs)
        self.assertTrue((proj / "work" / "watch.json").is_file())
        self.assertTrue((proj / "work" / CL.FINGERPRINT_FILE).is_file())
        self.assertTrue((proj / CL.STATE_FILE).is_file())

    def test_form_export_csv_writer(self):
        from taxjson.bin import taxjson_form_export as FE
        out = self.tmp / "s3.csv"
        v = self.plant(out.with_name(out.name + ".part"))
        rep = {"form": "8949", "part_I": [], "part_II": []}
        FE.write_csv(rep, out)
        self.assertUntouched(v)
        self.assertTrue(out.read_text().startswith("part,"))


class TestMigrateWriter(_Victims):
    def _project(self):
        root = self.tmp / "proj"
        root.mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            '[accounts.m]\ntype = "taxable"\n')
        (root / "yf_ticker.map").write_text("ZZS.TO ZZS-A.TO\n")
        return root

    def test_planted_migrate_part_is_not_followed(self):
        from taxjson.lib import migrate as M
        root = self._project()
        v = self.plant(root / "ticker.map.migrate.part")
        M.apply(M.plan(root))
        self.assertUntouched(v)
        self.assertIn("QUOTE ZZS.TO ZZS-A.TO",
                      (root / "ticker.map").read_text())
        self.assertFalse(os.path.lexists(root / "ticker.map.migrate.part"))

    def test_ticker_map_linked_outside_the_project_is_refused(self):
        from taxjson.lib import migrate as M
        root = self._project()
        v = self.plant(root / "ticker.map", "shared-ticker.map")
        with self.assertRaises(M.MigrateError) as cm:
            M.plan(root)
        self.assertIn("symlink", str(cm.exception))
        self.assertIn("outside the project", str(cm.exception))
        self.assertUntouched(v)
        self.assertTrue((root / "yf_ticker.map").is_file())   # nothing moved

    def test_ticker_map_linked_inside_the_project_updates_the_target(self):
        from taxjson.lib import migrate as M
        root = self._project()
        real = root / "maps" / "ticker.map"
        real.parent.mkdir()
        real.write_text("# shared\n")
        (root / "ticker.map").symlink_to(Path("maps") / "ticker.map")
        M.apply(M.plan(root))
        self.assertTrue((root / "ticker.map").is_symlink())
        self.assertIn("QUOTE ZZS.TO ZZS-A.TO", real.read_text())


def _rates(path: Path):
    d, lines = date(2024, 1, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.3500 boc")
        d += timedelta(days=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


class TestRunInAPlantedProject(_Victims):
    """`taxjson run` over a synthetic project whose work/ and reports/
    hold a symlink at EVERY temp name (and at the final names) pointing
    outside the project: nothing outside changes."""

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
        _rates(root / "work" / "to_base.csv")
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


# ----------------------------------------------------------------- L5

class _Resp(io.BytesIO):
    closed_by_with = False

    def __exit__(self, *a):
        type(self).closed_by_with = True
        return super().__exit__(*a)


class TestFillCryptoUrl(unittest.TestCase):
    def test_symbol_is_quoted_and_the_response_closed(self):
        from taxjson.bin import fill_crypto_prices as F
        seen = []
        body = (b'{"chart": {"result": [{"indicators": {"quote": '
                b'[{"close": [123.5]}]}}]}}')

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            return _Resp(body)

        env = {k: v for k, v in os.environ.items() if k != "TAXJSON_OFFLINE"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(F.urllib.request, "urlopen", fake_urlopen), \
                mock.patch.dict(F.SYMBOL_OVERRIDES, {"ZZQ": "A/B?x=1#y"}):
            v = F.get_crypto_price("ZZQ", "2025-01-02")
        self.assertEqual(v, 123.5)
        self.assertEqual(len(seen), 1)
        path = seen[0].split("?", 1)[0]
        self.assertTrue(path.endswith("/chart/A%2FB%3Fx%3D1%23y-USD"),
                        seen[0])
        self.assertTrue(_Resp.closed_by_with)


# ----------------------------------------------------------------- I1

class TestFxExtraComment(unittest.TestCase):
    def test_pyproject_dates_the_yahoo_fallback_like_the_code(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        block = text[text.index("[project.optional-dependencies]"):
                     text.index("fx = [")]
        self.assertIn("2007-05-01", block)
        self.assertNotIn("2017-01-03", block)


if __name__ == "__main__":
    unittest.main()
