"""The deck (docs/deck/) cannot drift from its source or from the program.

- The PDF is rendered from the HTML by scripts/build_deck.sh, which
  records the HTML's sha256 beside the PDF: an HTML edit without a
  rebuild fails here (the PDF had drifted for releases before v0.28.0).
- The commands slide is the help page's groups (_COMMAND_GROUPS, the
  Maintainer group left out): scripts/deck_commands.py --check.
- The test count on slide 6 is the one docs/limits.md states, and that
  figure is not above the suite's size nor more than 1,000 below it.
- With pdftotext installed: the PDF's text holds every slide title and
  every command the commands slide names.
"""
import hashlib
import html
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECK = ROOT / "docs" / "deck"
HTML = DECK / "taxjson-deck.html"
PDF = DECK / "taxjson-deck.pdf"
SHA = DECK / "taxjson-deck.pdf.sha256"
SCRIPT = ROOT / "scripts" / "deck_commands.py"


def _deck_commands():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import deck_commands
    finally:
        sys.path.pop(0)
    return deck_commands


class TestDeckIsBuilt(unittest.TestCase):
    def test_pdf_was_rendered_from_this_html(self):
        recorded = SHA.read_text(encoding="utf-8").split()[0]
        current = hashlib.sha256(HTML.read_bytes()).hexdigest()
        self.assertEqual(recorded, current,
                         "docs/deck/taxjson-deck.html changed since the PDF "
                         "was rendered: run scripts/build_deck.sh and commit "
                         "the PDF and its .sha256 with the HTML")

    def test_commands_slide_is_the_help_page(self):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--check"],
                           capture_output=True, text=True, timeout=60,
                           check=False)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_commands_slide_leaves_out_the_maintainer_group(self):
        dc = _deck_commands()
        titles = [t for t, _ in dc.groups()]
        self.assertNotIn("Maintainer", titles)
        block = dc.render()
        for cmd in ("channels", "deploy", "promote"):
            self.assertNotIn(f"<code>{cmd}</code>", block)
        from taxjson.bin import taxjson_run as R
        shown = [(t, c) for t, c in R._COMMAND_GROUPS
                 if t != R._MAINTAINER_GROUP]
        self.assertEqual(dc.groups(), [(t, tuple(c)) for t, c in shown])


class TestDeckFigures(unittest.TestCase):
    def test_test_count_has_one_source(self):
        limits = (ROOT / "docs" / "limits.md").read_text(encoding="utf-8")
        deck = HTML.read_text(encoding="utf-8")
        stated = re.findall(r"\*\*([\d,]+)\+ tests\*\*", limits)
        self.assertEqual(len(stated), 1, "docs/limits.md states the test "
                         "count once, as **N+ tests**")
        on_slide = re.findall(r"([\d,]+)\+ tests", deck)
        self.assertEqual(on_slide, stated,
                         "slide 6's test count is docs/limits.md's")
        figure = int(stated[0].replace(",", ""))
        defined = sum(
            len(re.findall(r"(?m)^\s+def test_\w+", p.read_text(
                encoding="utf-8", errors="replace")))
            for p in (ROOT / "tests").rglob("*.py"))
        self.assertLessEqual(figure, defined,
                             f"docs/limits.md claims {figure}+ tests; the "
                             f"suite defines {defined}")
        self.assertLess(defined - figure, 1000,
                        f"docs/limits.md says {figure}+ tests; the suite "
                        f"defines {defined}: raise the figure (and the "
                        f"deck's, and rebuild it)")


@unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
class TestPdfText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run(["pdftotext", "-layout", str(PDF), "-"],
                           capture_output=True, text=True, timeout=60,
                           check=False)
        cls.text = " ".join(r.stdout.split())

    def test_every_slide_title(self):
        src = HTML.read_text(encoding="utf-8")
        titles = re.findall(r"<h[12]>(.*?)</h[12]>", src)
        self.assertEqual(len(titles), 12)
        for t in titles:
            plain = " ".join(html.unescape(re.sub(r"<[^>]+>", "", t))
                             .split())
            self.assertIn(plain, self.text)

    def test_every_command_on_the_commands_slide(self):
        for _, cmds in _deck_commands().groups():
            for c in cmds:
                self.assertRegex(self.text, rf"(?<![\w-]){re.escape(c)}"
                                            rf"(?![\w-])", c)


if __name__ == "__main__":
    unittest.main()
