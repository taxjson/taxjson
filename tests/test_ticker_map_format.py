"""`taxjson format-map`: ticker.map laid out in keyword groups
(lib/ticker_map_format). Lossless (the parsed map and every comment line
kept), idempotent, a comment directly above a line moved with it, a line
taxjson cannot use kept as written under Unrecognized, the CLI modes and
the init template. Synthetic symbols only."""
import os
import random
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from taxjson.lib import ticker_map_format as F
from taxjson.lib.ticker_map import (RENAME_KEYWORDS, RETIRED_KEYWORDS,
                                    SIDE_KEYWORDS)

REPO_ROOT = Path(__file__).resolve().parents[1]

MESSY = """\
# My notes for this project: every line here was checked by hand.

# --- renames ---
global  zzq.us   ZZR.US   # odd spelling at the broker
# Fractional residue under a non-real merger ticker; drop it.
DELETE ZZJUNK.TO
TOBASE  ZZA.US    ZZA.TO
TOBASE ZZB.US ZZB.TO
# a note about the TOBASE line above
# (two lines long)

QUOTE ZZK.TO ZZK.V
EXTRACT   Sample US Dollar Fund |USD|   ZZD.U.TO   # the TSX-only USD unit
# CRYPTO ZZC ZZC123
RENAME ZZOLD.US ZZNEW.US 2025-04-01 late=fold
RENAME ZZX.US ZZY.US
JOURNAL ZZD.U.TO ZZD.TO
DISTINCT ZZE.US ZZE.TO
TOBASE ZZB.US ZZB.TO

# --- section heading of my own ---

MULT ZZQ1 50
TRADINGVIEW ZZQ NYSE
BOGUS line without a keyword
TOBASE ZZA.US ZZOTHER.TO
# GLOBAL ZZV.US ZZW.US
"""


def _comments(text):
    """Every comment line (stripped) and inline `#...` of `text`."""
    out = Counter()
    for ln in text.splitlines():
        st = ln.strip()
        if "#" in st:
            out[st if st.startswith("#") else st[st.index("#"):]] += 1
    return out


def _cli(root, *args, **env):
    e = dict(os.environ, TAXJSON_OFFLINE="1")
    e.setdefault("TAXJSON_WIDTH", "0")
    e.update(env)
    e["PYTHONPATH"] = (str(REPO_ROOT / "src") + os.pathsep
                       + e.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=str(root), capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL, timeout=300)


def _group_of(text, line):
    """The group heading the line `line` sits under."""
    group = None
    for ln in text.splitlines():
        if ln.startswith("## --- "):
            group = ln[len("## --- "):-len(" ---")]
        if ln == line:
            return group
    raise AssertionError(f"{line!r} not in the output")


class TestLayout(unittest.TestCase):
    def setUp(self):
        self.res = F.format_map(MESSY)
        self.text = self.res.text

    def test_idempotent(self):
        again = F.format_map(self.text)
        self.assertFalse(again.changed)
        self.assertEqual(again.text, self.text)

    def test_lossless(self):
        self.assertEqual(F.meaning(self.text), F.meaning(MESSY))
        # Every comment the user wrote is still there, byte for byte
        # (the one dropped duplicate line carried none).
        self.assertEqual(_comments(MESSY) - _comments(self.text), Counter())

    def test_groups_in_order_with_header(self):
        heads = [ln for ln in self.text.splitlines()
                 if ln.startswith("## --- ")]
        self.assertEqual(heads, [F.heading(g) for g in F.GROUP_NAMES])
        self.assertTrue(self.text.startswith("## ticker.map: "))
        self.assertEqual(_group_of(self.text, "TOBASE ZZA.US ZZA.TO"),
                         F.LISTINGS)
        self.assertEqual(_group_of(self.text, "DELETE ZZJUNK.TO"),
                         F.CLEANUP)
        self.assertEqual(_group_of(
            self.text, "RENAME ZZOLD.US ZZNEW.US 2025-04-01 late=fold"),
            F.DATED)
        # An undated RENAME means GLOBAL: a spelling.
        self.assertEqual(_group_of(self.text, "RENAME ZZX.US ZZY.US"),
                         F.SPELLINGS)
        self.assertEqual(_group_of(self.text, "JOURNAL ZZD.U.TO ZZD.TO"),
                         F.DATED)
        self.assertEqual(_group_of(self.text, "MULT ZZQ1 50"), F.LOOKUPS)
        self.assertEqual(_group_of(self.text, "TRADINGVIEW ZZQ NYSE"),
                         F.RETIRED)
        self.assertEqual(self.res.counts[F.LISTINGS], 3)
        self.assertEqual(self.res.counts[F.UNRECOGNIZED], 2)

    def test_spacing_normalised_inline_comments_kept(self):
        lines = self.text.splitlines()
        self.assertIn("GLOBAL zzq.us ZZR.US  # odd spelling at the broker",
                      lines)
        self.assertIn("EXTRACT Sample US Dollar Fund | USD | ZZD.U.TO  "
                      "# the TSX-only USD unit", lines)

    def test_user_order_kept_within_a_group(self):
        lines = self.text.splitlines()
        lines = lines[:lines.index(F.heading(F.UNRECOGNIZED))]
        order = [ln for ln in lines if ln.startswith(("TOBASE", "DISTINCT"))]
        self.assertEqual(order, ["TOBASE ZZA.US ZZA.TO",
                                 "TOBASE ZZB.US ZZB.TO",
                                 "DISTINCT ZZE.US ZZE.TO"])

    def test_attached_comments_move_with_their_line(self):
        lines = self.text.splitlines()
        i = lines.index("DELETE ZZJUNK.TO")
        self.assertEqual(lines[i - 1], "# Fractional residue under a "
                         "non-real merger ticker; drop it.")
        # A block directly below a line that a blank line ends is a note
        # on that line.
        i = lines.index("TOBASE ZZB.US ZZB.TO")
        self.assertEqual(lines[i + 1:i + 3],
                         ["# a note about the TOBASE line above",
                          "# (two lines long)"])
        # A heading directly above the first rule stays above it.
        i = lines.index("GLOBAL zzq.us ZZR.US  # odd spelling at the "
                        "broker")
        self.assertEqual(lines[i - 1], "# --- renames ---")

    def test_commented_out_rules(self):
        # On its own, a switched-off rule goes to its keyword's group.
        self.assertEqual(_group_of(self.text, "# GLOBAL ZZV.US ZZW.US"),
                         F.SPELLINGS)
        # Directly above a rule, it moves with that rule like a comment.
        lines = self.text.splitlines()
        i = lines.index("RENAME ZZOLD.US ZZNEW.US 2025-04-01 late=fold")
        self.assertEqual(lines[i - 1], "# CRYPTO ZZC ZZC123")

    def test_free_standing_blocks(self):
        lines = self.text.splitlines()
        # Before every rule: kept at the top, under the header.
        first_head = lines.index(F.heading(F.SPELLINGS))
        self.assertIn("# My notes for this project: every line here was "
                      "checked by hand.", lines[:first_head])
        # Between rules: before the rule that follows it.
        i = lines.index("# --- section heading of my own ---")
        self.assertEqual(lines[i + 2], "MULT ZZQ1 50")

    def test_unrecognized_kept_verbatim_and_named(self):
        lines = self.text.splitlines()
        self.assertEqual(_group_of(self.text, "BOGUS line without a "
                                   "keyword"), F.UNRECOGNIZED)
        # A second target for ZZA.US: the first line still wins.
        self.assertEqual(_group_of(self.text, "TOBASE ZZA.US ZZOTHER.TO"),
                         F.UNRECOGNIZED)
        self.assertLess(lines.index("TOBASE ZZA.US ZZA.TO"),
                        lines.index("TOBASE ZZA.US ZZOTHER.TO"))
        self.assertEqual(len(self.res.problems), 2)
        self.assertTrue(any("BOGUS" in p for p in self.res.problems))

    def test_exact_duplicate_dropped_and_reported(self):
        self.assertEqual(self.res.duplicates, [(19, "TOBASE ZZB.US ZZB.TO")])
        self.assertEqual(self.text.splitlines().count(
            "TOBASE ZZB.US ZZB.TO"), 1)

    def test_a_duplicate_with_its_own_comment_is_kept(self):
        r = F.format_map("GLOBAL ZZA ZZB\n# why\nGLOBAL ZZA ZZB\n")
        self.assertEqual(r.duplicates, [])
        self.assertEqual(r.text.splitlines().count("GLOBAL ZZA ZZB"), 2)

    def test_a_repeated_dated_rename_is_never_dropped(self):
        # Both lines book an event (and are a problem run refuses):
        # dropping one would change the books.
        text = ("RENAME ZZO.US ZZN.US 2025-04-01\n"
                "RENAME ZZO.US ZZN.US 2025-04-01\n")
        r = F.format_map(text)
        self.assertEqual(r.duplicates, [])
        self.assertEqual(F.meaning(r.text), F.meaning(text))

    def test_every_keyword_in_one_group(self):
        kws = [k for _g, ks, _d in F.GROUPS for k in ks]
        self.assertEqual(sorted(kws), sorted(
            RENAME_KEYWORDS + SIDE_KEYWORDS + tuple(RETIRED_KEYWORDS)))
        self.assertEqual(len(kws), 17)
        settings = (REPO_ROOT / "docs" / "settings.md").read_text(
            encoding="utf-8")
        for k in kws:
            self.assertIn(f"#### `{k}`", settings)

    def test_bom_and_crlf_free_result(self):
        r = F.format_map("﻿GLOBAL ZZA ZZB\n")
        self.assertTrue(r.changed)
        self.assertFalse(r.text.startswith("﻿"))

    def test_empty_map(self):
        r = F.format_map("")
        self.assertFalse(F.format_map(r.text).changed)
        self.assertEqual(r.counts[F.SPELLINGS], 0)

    def test_doc_lines_fit_the_width(self):
        for ln in F.init_template().splitlines():
            self.assertLessEqual(len(ln), 100, ln)


class TestTemplates(unittest.TestCase):
    def test_init_template_is_formatted(self):
        t = F.init_template()
        r = F.format_map(t)
        self.assertFalse(r.changed)
        # A commented-out example in each standard group.
        for g in F.GROUP_NAMES:
            if g not in F.OPTIONAL_GROUPS:
                self.assertTrue(any(
                    _group_of(t, ln) == g for ln in t.splitlines()
                    if F.commented_rule(ln)), g)

    def test_an_earlier_init_header_is_regenerated(self):
        # The header paragraphs the previous `taxjson init` wrote are
        # template text (recognised by hash): replaced by the new header;
        # its examples are kept like the user's own lines.
        old = (
            "# ticker.map — symbol rules for the taxjson pipeline. "
            "OPTIONAL: the pipeline\n"
            "# runs without it. Each non-comment line is:  KEYWORD  from  "
            "[to]\n"
            "#\n"
            "# Examples — uncomment and edit:\n"
            "# GLOBAL   ABCX-B.US  ABCX.B.US\n")
        r = F.format_map(old + "# my own note\nGLOBAL ZZA ZZB\n")
        self.assertNotIn("OPTIONAL: the pipeline", r.text)
        self.assertNotIn("Examples — uncomment", r.text)
        self.assertIn("# GLOBAL   ABCX-B.US  ABCX.B.US", r.text)
        self.assertIn("# my own note\nGLOBAL ZZA ZZB", r.text)

    def test_a_paragraph_the_user_edited_is_kept_whole(self):
        old = ("# ticker.map — symbol rules for the taxjson pipeline. "
               "OPTIONAL: the pipeline\n"
               "# runs without it, and I added this line.\n"
               "\nGLOBAL ZZA ZZB\n")
        r = F.format_map(old)
        self.assertIn("OPTIONAL: the pipeline", r.text)
        self.assertIn("I added this line", r.text)


class TestFuzz(unittest.TestCase):
    """Random maps from a pool of lines (good, bad, contradictory,
    comments, switched-off rules, headings): the result parses the
    same, keeps every comment and is a fixed point."""
    POOL = [
        "GLOBAL ZZA.US ZZB.US", "global zza.us  zzb.us",
        "TOBASE ZZC.US ZZC.TO", "DISTINCT ZZD.US ZZD.TO", "DELETE ZZE.TO",
        "RENAME ZZF.US ZZG.US 2025-03-01", "RENAME ZZH.US ZZI.US",
        "JOURNAL ZZJ.U.TO ZZJ.TO", "QUOTE ZZK.TO ZZK.V",
        "QUOTE ZZK.TO ZZK.X", "EXTRACT Some  Fund | USD | ZZL.U.TO",
        "EXTRACT Some Fund | * | ZZM.TO", "CRYPTO ZZN ZZN123",
        "T1135 ZZO.US CA", "STABLE ZZP USD", "SPLITSHARE ZZQ",
        "MULT ZZR1 50", "VENUE ZZEX NO", "TRADINGVIEW ZZ NYSE", "BOGUS x y",
        "GLOBAL ZZA.US ZZX.US", "GLOBAL ZZB.US ZZA.US",
        "DISTINCT ZZA.US ZZB.US", "GLOBAL ZZS.US ZZT.US  # note",
        "TOBASE ZZU.US", "# GLOBAL ZZV ZZW", "# TOBASE ZZV.US ZZV.TO",
        "# QUOTE A B", "# a note", "## user double", "#", "", "", "",
        "## --- Lookups ---", "## --- Spellings ---",
        "   GLOBAL ZZY.US ZZZ.US", "   # indented note",
        "EXTRACT Other | CAD | ZZQ.TO  # x",
        "RENAME ZZF.US ZZG.US 2025-03-10", "RENAME ZZG.US ZZF.US 2025-06-01",
        "#   CRYPTO  SYMBOL YAHOO_ID"]

    def test_random_maps(self):
        rnd = random.Random(20261007)
        tmpl = F.init_template().splitlines()
        for _ in range(300):
            lines = [rnd.choice(self.POOL)
                     for _ in range(rnd.randint(0, 25))]
            if rnd.random() < 0.2:
                lines = tmpl + lines
            text = "\n".join(lines) + "\n"
            r = F.format_map(text)
            self.assertEqual(F.meaning(r.text), F.meaning(text), text)
            dropped = Counter(ln[ln.index("#"):] for _n, ln in r.duplicates
                              if "#" in ln)
            self.assertEqual(F._comment_lines(text) - dropped
                             - F._comment_lines(r.text), Counter(), text)
            self.assertFalse(F.format_map(r.text).changed, text)


class TestCommand(unittest.TestCase):
    def _project(self, td, text=MESSY):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\ncountry = "canada"\nyear = 2025\n', encoding="utf-8")
        (root / "ticker.map").write_text(text, encoding="utf-8")
        return root

    def test_dry_run_check_write_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            r = _cli(root, "format-map")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("+++ ticker.map (formatted)", r.stdout)
            self.assertIn("dry run: nothing written", r.stderr)
            self.assertIn("lines per group: Spellings 2, Listings of one security 3", r.stderr)
            self.assertIn("2 ticker.map problem(s)", r.stderr)
            self.assertIn("exact duplicate", r.stderr)
            self.assertEqual((root / "ticker.map").read_text(), MESSY)
            r = _cli(root, "format-map", "--check")
            self.assertEqual(r.returncode, 1)
            self.assertIn("ticker.map is not formatted", r.stderr)
            r = _cli(root, "format-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("previous version: ticker.map.bak", r.stdout)
            self.assertEqual((root / "ticker.map.bak").read_text(), MESSY)
            self.assertEqual((root / "ticker.map").read_text(),
                             F.format_map(MESSY).text)
            # Formatted, but `taxjson run` still refuses the map (its two
            # problem lines): --check fails and says so.
            r = _cli(root, "format-map", "--check")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("already formatted", r.stdout)
            self.assertIn("`taxjson run` refuses this ticker.map", r.stderr)
            # A formatted file is left alone: no second backup.
            r = _cli(root, "format-map", "--write")
            self.assertEqual(r.returncode, 0)
            self.assertFalse((root / "ticker.map.bak1").exists())

    def test_no_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "GLOBAL  ZZA  ZZB\n")
            r = _cli(root, "format-map", "--no-backup")
            self.assertEqual(r.returncode, 2)
            self.assertIn("--no-backup applies only with --write", r.stderr)
            r = _cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((root / "ticker.map.bak").exists())
            self.assertIn("\nGLOBAL ZZA ZZB\n",
                          (root / "ticker.map").read_text())

    def test_no_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            (root / "ticker.map").unlink()
            r = _cli(root, "format-map")
            self.assertEqual(r.returncode, 2)
            self.assertIn("no ticker.map in", r.stderr)

    def test_symlink_outside_the_project_is_not_written(self):
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as other:
            root = self._project(td)
            target = Path(other) / "shared.map"
            target.write_text("GLOBAL  ZZA  ZZB\n", encoding="utf-8")
            (root / "ticker.map").unlink()
            (root / "ticker.map").symlink_to(target)
            r = _cli(root, "format-map", "--write")
            self.assertEqual(r.returncode, 2)
            self.assertIn("outside the project", r.stderr)
            self.assertEqual(target.read_text(), "GLOBAL  ZZA  ZZB\n")

    def test_init_maps_are_formatted_both_countries(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as td:
                root = Path(td) / "p"
                r = _cli(Path(td), "init", "--single", "--country", country,
                         str(root))
                self.assertEqual(r.returncode, 0, r.stderr)
                r = _cli(root, "format-map", "--check")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("already formatted", r.stdout)
                # The commented examples parse to nothing: an empty map.
                # (A Canadian project's tobase.map adds the interlisted
                # pairs — every one of them `generated`, none the
                # user's: lib/tobase_map.)
                from taxjson.bin.taxjson_ticker_map import _parse_map_file
                tm, problems, notes = _parse_map_file(root / "ticker.map")
                self.assertEqual((problems, notes), ([], []))
                self.assertFalse(tm.glob or tm.delete)
                self.assertFalse(set(tm.tobase) - set(tm.generated))
                self.assertEqual(bool(tm.tobase), country == "canada")

    def test_console_style(self):
        from _style import assert_console, assert_labelled, assert_styled
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            r = _cli(root, "format-map", TAXJSON_WIDTH="120")
            self.assertEqual(r.returncode, 0, r.stderr)
            assert_console(self, r.stderr)
            assert_labelled(self, r.stdout + r.stderr)
            r = _cli(root, "format-map", "--write", TAXJSON_WIDTH="120")
            assert_styled(self, r.stdout)
            assert_console(self, r.stderr)


if __name__ == "__main__":
    unittest.main()
