"""`taxjson format-map` and the headers earlier taxjson versions wrote
into ticker.map (lib/ticker_map_legacy, lib/ticker_map_format).

Every variant in tests/fixtures/ticker_map/legacy_headers.txt (each
`taxjson init` template, the earliest projects' header, `taxjson
migrate`'s line, the first grouped layout) is template text: replaced by
the current header, whole, re-wrapped, re-cased, split into pieces, or
with the user's notes directly below its last line. A copy the user
edited is kept, with an Info line; a comment that still documents JOURNAL
or a dated RENAME as a map rule is kept, with an Info line. The user's
own prose and commented-out examples are kept; formatting is idempotent.
Synthetic symbols only."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from _style import CapturedWidth

from taxjson.lib import ticker_map_format as F
from taxjson.lib import ticker_map_legacy as L

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "ticker_map"
           / "legacy_headers.txt")

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def variants():
    """[(name, text)] of the fixture, each text newline-terminated."""
    parts = FIXTURE.read_text(encoding="utf-8").split("\n=== ")[1:]
    out = []
    for p in parts:
        name, _nl, body = p.partition("\n")
        out.append((name, body.rstrip("\n") + "\n"))
    return out


def prose(text):
    """The variant's own explanatory lines (its examples left out)."""
    return [ln for para in L.corpus_paragraphs(text) for ln in para]


# The user's own lines: a note above a rule, a free-standing note, a
# commented-out rule of theirs.
USER = textwrap.dedent("""\
    # --- my renames ---
    # the broker spells this one oddly
    GLOBAL ZZA.US ZZB.US

    # A free note of mine about this file.

    # GLOBAL ZZC.US ZZD.US
    TOBASE ZZE.US ZZE.TO
    """)


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    e["PYTHONPATH"] = (str(REPO_ROOT / "src") + os.pathsep
                       + e.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=str(root), capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=300)


class TestTables(unittest.TestCase):
    def test_tables_are_the_fixtures(self):
        lines, paras, prints = L.corpus_tables([t for _n, t in variants()])
        msg = ("lib/ticker_map_legacy's tables differ from the fixture: "
               "recompute them with corpus_tables")
        self.assertEqual(set(L.LINE_HASHES), lines, msg)
        self.assertEqual(set(L.PARAGRAPH_HASHES), paras, msg)
        self.assertEqual(set(L.FINGERPRINTS), prints, msg)

    def test_fixture_has_every_variant(self):
        names = [n for n, _t in variants()]
        self.assertEqual(len(names), 12, names)
        self.assertTrue(any("earliest" in n for n in names))
        self.assertTrue(any("migrate" in n for n in names))

    def test_a_listing_symbol_is_masked(self):
        # The fixture's made-up symbols hash like the templates' own.
        self.assertEqual(L.norm_line("# (e.g. ZZQ.U.TO / ZZQ.TO)"),
                         L.norm_line("#  (e.g.  QQX.US / QQX-B.V)"))


class TestEachVariant(unittest.TestCase):
    def test_each_variant_is_replaced(self):
        for name, text in variants():
            with self.subTest(name):
                src = text + "\n" + USER
                r = F.format_map(src, migrate=True)
                self.assertTrue(r.changed)
                self.assertGreater(r.regenerated, 0)
                self.assertEqual(r.edited_headers, [])
                out = r.text.splitlines()
                for ln in prose(text):
                    if ln.strip() not in F._OWN_LINES:
                        self.assertNotIn(ln, out)
                self.assertTrue(r.text.startswith(F.header_text()))
                # The user's lines, as written.
                for ln in USER.splitlines():
                    if ln.startswith("#"):
                        self.assertIn(ln, out)
                self.assertIn("# the broker spells this one oddly\n"
                              "GLOBAL ZZA.US ZZB.US", r.text)
                # The variant's own commented-out examples are kept (a
                # dated RENAME example is dropped by the migration, a
                # JOURNAL one becomes TOBASE).
                for ln in text.splitlines():
                    kw = F.commented_rule(ln)
                    if kw and not L.is_legacy_line(ln) and kw not in (
                            "RENAME", "JOURNAL"):
                        self.assertIn(ln, out)
                # Idempotent.
                again = F.format_map(r.text, migrate=True)
                self.assertFalse(again.changed, name)
                self.assertEqual(again.regenerated, 0)

    def test_rewrapped_and_recased_paragraphs_are_recognised(self):
        for name, text in variants():
            with self.subTest(name):
                paras = L.corpus_paragraphs(text)
                body = []
                for p in paras:
                    words = " ".join(L.norm_line(ln) for ln in p).lower()
                    body += ["# " + w for w in textwrap.wrap(
                        words, 50, break_long_words=False,
                        break_on_hyphens=False)]
                    body.append("#")
                r = F.format_map("\n".join(body) + "\n\n" + USER)
                self.assertEqual(r.regenerated, len(body) - len(paras))
                self.assertEqual(r.edited_headers, [])
                for ln in body:
                    if ln != "#":
                        self.assertNotIn(ln, r.text.splitlines())

    def test_a_header_split_into_pieces_is_recognised(self):
        for name, text in variants():
            with self.subTest(name):
                # A blank line after every second line of the header.
                lines = [ln for p in L.corpus_paragraphs(text) for ln in p]
                body = []
                for i, ln in enumerate(lines):
                    body.append(ln)
                    if i % 2:
                        body.append("")
                r = F.format_map("\n".join(body) + "\n\n" + USER)
                # (a `## ` line of the current layout is its own text)
                self.assertEqual(r.regenerated, sum(
                    1 for ln in lines if ln.strip() not in F._OWN_LINES))
                self.assertEqual(r.edited_headers, [])


class TestOwnerShape(unittest.TestCase):
    def test_notes_directly_below_the_last_paragraph(self):
        # The earliest header's last line, then the user's notes in the
        # same comment block, then a rule: the header goes, the notes
        # stay with their rule.
        name, text = variants()[0]
        self.assertIn("earliest", name)
        src = (text + "# --- GLOBAL renames ---\n"
               "# an internal code at the broker\nGLOBAL ZZQW ZZQ\n")
        r = F.format_map(src, migrate=True)
        self.assertEqual(r.regenerated, len(prose(text)))
        self.assertIn("\n# --- GLOBAL renames ---\n"
                      "# an internal code at the broker\nGLOBAL ZZQW ZZQ\n",
                      r.text)
        self.assertNotIn("starts a comment", r.text)
        self.assertNotIn("symbol rules for the taxjson pipeline", r.text)
        self.assertFalse(F.format_map(r.text, migrate=True).changed)

    def test_check_fails_while_a_legacy_header_is_present(self):
        _name, text = variants()[0]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n',
                encoding="utf-8")
            (root / "ticker.map").write_text(
                F.header_text() + "\n" + text + "GLOBAL ZZQW ZZQ\n",
                encoding="utf-8")
            r = _cli(root, "format-map")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("-# '#' starts a comment.", r.stdout)
            self.assertIn(f"note: {len(prose(text))} comment line(s) of an "
                          f"earlier taxjson header are replaced by the "
                          f"current header", r.stderr)
            r = _cli(root, "format-map", "--check")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("ticker.map is not formatted", r.stderr)
            r = _cli(root, "format-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            new = (root / "ticker.map").read_text(encoding="utf-8")
            self.assertEqual(new.count(F.header_text()), 1)
            self.assertNotIn("Each line is", new)
            r = _cli(root, "format-map", "--check")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("earlier taxjson header", r.stderr)


class TestEdited(unittest.TestCase):
    EDIT = ("Nuke that ticker's transactions", "Drop that ticker's rows")

    def _edited(self):
        _name, text = variants()[0]
        self.assertIn(self.EDIT[0], text)
        return text.replace(*self.EDIT)

    def test_an_edited_copy_is_kept_with_an_info(self):
        text = self._edited()
        r = F.format_map(text + "GLOBAL ZZQW ZZQ\n", migrate=True)
        self.assertEqual(len(r.edited_headers), 1)
        self.assertTrue(r.edited_headers[0].startswith("#   GLOBAL  from to"))
        # The edited paragraph is kept whole, as written.
        para = next(p for p in L.corpus_paragraphs(text)
                    if any(self.EDIT[1] in ln for ln in p))
        self.assertIn("\n".join(para), r.text)
        # Its other paragraphs are the template's: replaced.
        self.assertNotIn("starts a comment", r.text)
        # It does not move under the rule below the header.
        self.assertNotIn(para[-1] + "\nGLOBAL", r.text)
        # Not a dated-comment hint too (it is the header's text).
        self.assertEqual(r.dated_comments, [])
        again = F.format_map(r.text, migrate=True)
        self.assertFalse(again.changed)
        self.assertEqual(again.edited_headers, r.edited_headers)

    def test_a_paragraph_with_a_line_added_is_kept(self):
        # A line of the user's inside a long paragraph: the paragraph
        # (as edited) is kept whole. (A line directly below its last
        # line is a note of the user's: the paragraph goes, the note
        # stays — TestOwnerShape.)
        n = 0
        for name, text in variants():
            with self.subTest(name):
                paras = [p for p in L.corpus_paragraphs(text)
                         if len(L.words(p)) >= 30 and len(p) >= 3]
                if not paras:
                    continue
                p = paras[0]
                body = p[:2] + ["# (my own line inside the old header)"] \
                    + p[2:]
                r = F.format_map("\n".join(body) + "\n\nGLOBAL ZZA ZZB\n")
                # (a `## ` line of the current layout is its own text)
                kept = [ln for ln in body if ln.strip() not in F._OWN_LINES]
                self.assertEqual(r.edited_headers, [kept[0].strip()])
                self.assertIn("\n".join(kept), r.text)
                n += 1
        self.assertGreater(n, 8)

    def test_cli_names_the_edited_block(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n',
                encoding="utf-8")
            (root / "ticker.map").write_text(
                self._edited() + "GLOBAL ZZQW ZZQ\n", encoding="utf-8")
            r = _cli(root, "format-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("note: a comment block looks like an old taxjson "
                          "header you edited — kept; delete it if it no "
                          "longer applies", r.stderr)
            r = _cli(root, "format-map", "--check")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("header you edited", r.stderr)


class TestUserProse(unittest.TestCase):
    def test_prose_is_untouched(self):
        # Words of the templates in the user's own sentences, short
        # template phrases, a re-cased keyword table of their own.
        notes = textwrap.dedent("""\
            # The taxjson pipeline reads this file; my symbol rules below.
            # Examples:
            # GLOBAL from to   my own reminder of the order
            #
            # '#' starts a comment in my notes too, and so on.
            GLOBAL ZZA.US ZZB.US
            """)
        r = F.format_map(notes)
        self.assertEqual(r.regenerated, 0)
        self.assertEqual(r.edited_headers, [])
        for ln in notes.splitlines():
            if ln.startswith("#") and ln != "#":
                self.assertIn(ln, r.text.splitlines())

    def test_a_comment_documenting_journal_gets_a_hint(self):
        src = textwrap.dedent("""\
            # --- JOURNAL (Norbert's Gambit) ---
            JOURNAL ZZD.U.TO ZZD.TO

            # A ticker change: RENAME ZZO.US ZZN.US 2024-06-10 in the map.
            GLOBAL ZZA ZZB

            # a .tt line now: RENAME 2024-06-10 ZZO.US ZZN.US
            # a journal between two listings, lower case
            # JOURNAL ZZE.U.TO ZZE.TO
            GLOBAL ZZC ZZD
            """)
        r = F.format_map(src, migrate=True)
        self.assertCountEqual(r.dated_comments, [
            "# --- JOURNAL (Norbert's Gambit) ---",
            "# A ticker change: RENAME ZZO.US ZZN.US 2024-06-10 in the map."])
        # Kept, as written; the switched-off JOURNAL is migrated.
        self.assertIn("# --- JOURNAL (Norbert's Gambit) ---\n"
                      "TOBASE ZZD.U.TO ZZD.TO", r.text)
        self.assertIn("# TOBASE ZZE.U.TO ZZE.TO", r.text)
        # Without the migration (format_map's default) there is no hint.
        self.assertEqual(F.format_map(src).dated_comments, [])

    def test_cli_hint_points_at_the_docs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n',
                encoding="utf-8")
            (root / "ticker.map").write_text(
                "# --- JOURNAL (Norbert's Gambit) ---\n"
                "JOURNAL ZZD.U.TO ZZD.TO\n", encoding="utf-8")
            r = _cli(root, "format-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("note: 1 comment block(s) still describe JOURNAL "
                          "or a dated RENAME as a ticker.map rule", r.stderr)
            self.assertIn("- # --- JOURNAL (Norbert's Gambit) ---", r.stderr)
            self.assertIn("docs/settings.md", r.stderr)
            self.assertIn("# --- JOURNAL (Norbert's Gambit) ---\n"
                          "TOBASE ZZD.U.TO ZZD.TO",
                          (root / "ticker.map").read_text(encoding="utf-8"))


class TestMigrateHeader(unittest.TestCase):
    def test_a_map_migrate_creates_starts_with_the_current_header(self):
        from taxjson.lib import migrate as M
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n',
                encoding="utf-8")
            (root / "yf_ticker.map").write_text("ZZQ.TO ZZQ.V\n",
                                                encoding="utf-8")
            M.apply(M.plan(root))
            text = (root / "ticker.map").read_text(encoding="utf-8")
            self.assertTrue(text.startswith(F.header_text()), text)
            r = F.format_map(text, migrate=True)
            self.assertEqual(r.regenerated, 0)


if __name__ == "__main__":
    unittest.main()
