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
import re
import sys
import unittest
from pathlib import Path
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))


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
    """Formerly: test_fix_prerelease_security.TestNoPyPIInstallHints"""
    def test_fetch_install_hint_names_the_installer_and_the_checkout(self):
        from taxjson.lib.fetchers import INSTALL_HINT
        self.assertNotRegex(INSTALL_HINT, _PYPI_INSTALL)
        # The installer installs taxjson-fetch by default; an install
        # that opted out takes it back with --with-fetch.
        self.assertIn("installs taxjson-fetch by default", INSTALL_HINT)
        self.assertIn("--with-fetch", INSTALL_HINT)
        self.assertIn("https://taxjson.com/install.sh", INSTALL_HINT)
        self.assertIn("pip install --no-deps -e packages/taxjson-fetch", INSTALL_HINT)
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
            # The installer's default includes the plugin; the docs
            # name the opt-out.
            self.assertIn("--without-fetch", text, rel)


# ----------------------------------------------------------------- I1

class TestFxExtraComment(unittest.TestCase):
    """Formerly: test_fix_prerelease_security.TestFxExtraComment"""
    def test_pyproject_dates_the_yahoo_fallback_like_the_code(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        block = text[text.index("[project.optional-dependencies]"):
                     text.index("fx = [")]
        self.assertIn("2007-05-01", block)
        self.assertNotIn("2017-01-03", block)


if __name__ == "__main__":
    unittest.main()
