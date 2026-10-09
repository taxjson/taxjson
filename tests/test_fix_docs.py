"""Docs that contradict each other or the code (re-audit-2 docs list).

These pin the documentation fixes: a doc that names a command that does
not exist, a known issue that is already fixed, a CHANGELOG bullet that
appears twice, a help text that hides the unit the tool reads.
"""
import io
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def _unreleased():
    """The CHANGELOG's current entry: Unreleased, or — right after a release
    promoted it and left it empty — the newest version's entry."""
    text = _read("CHANGELOG.md")
    start = text.index("## Unreleased")
    end = text.index("\n## v", start)
    if re.search(r"^[-#]", text[start:end].split("\n", 1)[1], re.M):
        return text[start:end]
    nxt = text.find("\n## v", end + 1)
    return text[end + 1:nxt if nxt != -1 else len(text)]


def _bullets(section):
    """Top-level bullets of a CHANGELOG section, whitespace-normalised."""
    out, cur = [], None
    for line in section.splitlines():
        if line.startswith("- "):
            if cur is not None:
                out.append(cur)
            cur = line[2:]
        elif cur is not None and line.startswith("  "):
            cur += " " + line.strip()
        else:
            if cur is not None:
                out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return [re.sub(r"\s+", " ", b).strip() for b in out]


def _subcommands():
    """The subcommand names `taxjson --help` lists."""
    # `help --all`: every command, whatever project the cwd is in; the
    # grouped page lists each at a 2-space indent under its heading.
    r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                        "help", "--all"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, env=dict(os.environ,
                                                          COLUMNS="78"))
    names = set(re.findall(r"^  ([a-z][a-z0-9-]+)(?:\s|$)", r.stdout,
                           re.M))
    assert {"run", "renames", "crypto-sends", "help"} <= names, r.stdout
    return names


class TestNoPhantomCommands(unittest.TestCase):
    """A doc names only commands that exist (A2-0951, A2-1619)."""

    DOCS = ["README.md", "KNOWN_ISSUES.md", "SECURITY.md", "REFERENCES.md",
            "docs/filing.md", "docs/deck/taxjson-deck.html"]

    def test_no_taxjson_filed_command(self):
        self.assertNotIn("taxjson filed", _unreleased())
        for d in self.DOCS:
            self.assertNotIn("`taxjson filed`", _read(d), d)

    def test_no_taxjson_fees_console_script(self):
        # The console script is taxjson-fees-sum; the taxjson-fees alias
        # was removed (CHANGELOG v0.15 era).
        pat = re.compile(r"taxjson-fees(?!-sum)\b")
        self.assertIsNone(pat.search(_unreleased()))
        for d in self.DOCS:
            self.assertIsNone(pat.search(_read(d)), d)

    def test_every_documented_taxjson_command_exists(self):
        subs = _subcommands()
        readme = _read("README.md")
        named = set(re.findall(r"`taxjson ([a-z][a-z0-9-]+)", readme))
        # `taxjson <tool>` passthroughs and words that are not commands.
        missing = sorted(n for n in named if n not in subs)
        self.assertEqual(missing, [], "README names unknown commands")

    def test_unreleased_names_real_commands(self):
        # Removed commands the CHANGELOG still names: "the removed taxjson
        # verify", the web UI's `taxjson serve`, and `taxjson quick-start`
        # (merged into `taxjson checklist`).
        subs = _subcommands() | {"verify", "serve", "quick-start"}
        named = set(re.findall(r"`taxjson ([a-z][a-z0-9-]+)", _unreleased()))
        self.assertEqual(sorted(named - subs), [])

    def test_every_command_is_in_the_readme(self):
        readme = _read("README.md")
        undocumented = sorted(
            c for c in _subcommands()
            if f"`taxjson {c}" not in readme and f"taxjson {c} " not in readme
            and f"`{c}`" not in readme)
        self.assertEqual(undocumented, [], "commands missing from README")


class TestKnownIssuesCurrent(unittest.TestCase):
    def test_ric_january_entry_is_the_implemented_one(self):
        # A2-0948/0952/0953/0954/1621/1625: the deferred entry went; the
        # list-based one stays.
        ki = _read("KNOWN_ISSUES.md")
        self.assertNotIn("January-paid Q4 fund dividends are dated in the "
                         "pay year", ki)
        self.assertIn("ric_january_dividends", ki)

    def test_unreleased_does_not_call_income_dating_unapplied(self):
        self.assertNotIn("not applied yet", _unreleased())  # A2-1620

    def test_rbc_rename_hint_scope_stated(self):
        # A2-1629: the hint needs the old symbol's rows in an export.
        ki = _read("KNOWN_ISSUES.md")
        self.assertIn("only when an export in the project holds the old "
                      "symbol's rows", ki)


class TestFilingChecklistDoc(unittest.TestCase):
    def test_every_checklist_command_is_in_filing_md(self):
        # docs/filing.md is the checklist's own text: a step proved by a
        # `taxjson X` command names it there (the renames step was missing).
        from taxjson.lib.checklist import STEPS
        doc = _read("docs/filing.md")
        missing = []
        for sid, _stage, _title, cmd, _why in STEPS:
            for c in re.findall(r"taxjson ([a-z][a-z0-9-]+)", cmd):
                if f"`taxjson {c}" not in doc:
                    missing.append((sid, c))
        self.assertEqual(missing, [])


class TestDeckAndSecurity(unittest.TestCase):
    def test_webull_input_named_trading_summary(self):
        # A2-0949/0950/1623
        deck = _read("docs/deck/taxjson-deck.html")
        self.assertNotIn("Account statement CSV", deck)
        self.assertIn("Trading Summary CSV", deck)

    def test_fx_sources_name_the_noon_rate_period(self):
        # A2-1622: Yahoo only before 2007-05 (to_base_curr NOON_START).
        from taxjson.bin import to_base_curr as t
        self.assertEqual((t.NOON_START, t.NOON_BEFORE),
                         ("2007-05-01", "2017-03-01"))
        sec = _read("SECURITY.md")
        self.assertNotIn("before 2017-01-03", sec)
        self.assertIn("before 2007-05-01", sec)
        self.assertNotIn("Yahoo before 2017",
                         _read("docs/deck/taxjson-deck.html"))


class TestCrossDocWording(unittest.TestCase):
    def test_outlays_split_mentions_written_options(self):
        self.assertNotIn("outlays split (long sales only)", _unreleased())

    def test_generic_venue_suffix_documented_as_folded(self):
        # A2-1628: .V/.CN/.NE are spelled .TO by the generic importer.
        readme = _read("README.md")
        self.assertNotIn("(`.TO`, `.V`, `.CN`,\n`.NE`, `.US`, `.AX`, `.L`) "
                         "keeps it", readme)
        from taxjson.lib.brokerages.generic import GenericBrokerage
        self.assertEqual(GenericBrokerage()._listing_symbol("QZA.V", "CAD"),
                         "QZA.TO")

    def test_crypto_pairing_window_documented(self):
        # A2-1627: same exchange in another account; 10 min early.
        readme = _read("README.md")
        self.assertNotIn("on another exchange within 3 days", readme)
        self.assertIn("from 10 minutes before to 3 days after", readme)
        r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                            "crypto-sends", "--help"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
        self.assertIn("10 minutes", " ".join(r.stdout.split()))


class TestDistributionsMapCurrency(unittest.TestCase):
    """A2-0947: the per-share amount is in the project base currency."""

    def test_note_names_the_base_currency(self):
        from taxjson.bin.taxjson_apply_distributions import \
            apply_distributions
        doc = {"metadata": {"target_currency": "CAD"}, "transactions": [
            {"action": "BUYSELL", "date": "2025-02-03", "time": "09:30:00",
             "date_settle": "2025-02-04", "symbol": "VTI.US",
             "quantity": 100.0, "id": "b1"}]}
        err = io.StringIO()
        with redirect_stderr(err):
            _doc, n = apply_distributions(
                doc, [("VTI.US", "2025-12-29", 0.5)], "m", country="canada")
        self.assertEqual(n, 1)
        self.assertIn("x 0.5 CAD =", err.getvalue())

    def test_docs_and_help_name_the_unit(self):
        self.assertIn("plain decimal in the project's base currency",
                      _read("README.md"))
        r = subprocess.run([sys.executable, "-m",
                            "taxjson.bin.taxjson_apply_distributions",
                            "--help"], capture_output=True, text=True)
        self.assertIn("BASE currency", " ".join(r.stdout.split()))
        from taxjson.lib.tax_logic import catalog
        rules = catalog()
        for rid in ("CA-DIST-01", "US-DIST-01"):
            self.assertIn("base currency", rules[rid].text)


class TestChangelogUnreleased(unittest.TestCase):
    def test_no_duplicate_bullets(self):
        bullets = _bullets(_unreleased())
        dups = sorted({b[:80] for b in bullets if bullets.count(b) > 1})
        self.assertEqual(dups, [])

    def test_grouped_by_area(self):
        entry = _unreleased()
        heads = re.findall(r"^### (.+)$", entry, re.M)
        # Every bullet sits under a heading; a large entry (more than 40
        # bullets, like a big release) is split into at least 5 areas, a
        # small one may need only one or two.
        big = len(_bullets(entry)) > 40
        self.assertGreaterEqual(len(heads), 5 if big else 1)
        self.assertEqual(len(heads), len(set(heads)))


class TestChangelogReleasedSections(unittest.TestCase):
    """A change made after a release goes under ## Unreleased: the newest
    tag's own section gains no bullet and loses none after the tag (an
    erratum may reword a bullet's body; its title stays). Skipped
    without git or tags (an sdist)."""

    def _git(self, *args):
        try:
            r = subprocess.run(["git", "-C", str(REPO), *args],
                               capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    @staticmethod
    def _section(text, tag):
        start = text.find(f"## {tag} ")
        if start == -1:
            start = text.find(f"## {tag}\n")
        if start == -1:
            return None
        end = text.find("\n## ", start + 1)
        return text[start:end if end != -1 else len(text)]

    @staticmethod
    def _titles(section):
        heads = re.findall(r"^### (.+)$", section, re.M)
        return heads, [b[:60] for b in _bullets(section)]

    def test_newest_release_section_matches_its_tag(self):
        tags = self._git("tag", "--list", "v*", "--sort=-v:refname")
        tags = [t for t in (tags or "").split()
                if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
        if not tags:
            self.skipTest("no git checkout with release tags")
        tag = tags[0]
        then = self._git("show", f"{tag}:CHANGELOG.md")
        if then is None:
            self.skipTest(f"{tag} has no CHANGELOG.md")
        was = self._section(then, tag)
        if was is None:
            self.skipTest(f"{tag}'s CHANGELOG has no {tag} section")
        now = self._section(_read("CHANGELOG.md"), tag)
        self.assertIsNotNone(now, f"the {tag} section is gone")
        heads_was, bullets_was = self._titles(was)
        heads_now, bullets_now = self._titles(now)
        self.assertEqual(heads_now, heads_was)
        added = [b for b in bullets_now if b not in bullets_was]
        removed = [b for b in bullets_was if b not in bullets_now]
        self.assertEqual((added, removed), ([], []),
                         f"{tag}'s section changed after the tag: move the "
                         f"new bullets under ## Unreleased")


if __name__ == "__main__":
    unittest.main()
