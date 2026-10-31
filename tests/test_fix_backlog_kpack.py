"""Knowledge-pack and docs drift (backlog group kpack).

Each test pins one backlog item: a doc that said something the code or
the release history no longer does.

- TJ-0031: settings.md's filed/YEAR.json section names every key of the
  `fx_cash_v2` close that lib/fx_cash_v2.build writes.
- TJ-0058: docs/upgrading.md's v0.35.0 notes say the T1135 cost of an
  assigned put's shares changes on a re-run (audit D02).
- TJ-0059: the line counts AGENTS.md and the architecture map give for
  the large files, and scripts/ci.sh's own --quick usage line.
- TJ-0129: where an upgrade-only fix is recorded (troubleshooting.md's
  "Fixed in"), said the same way by both pages.
- TJ-0170: the taxjson-diff --by "unhashable type" crash has its
  troubleshooting entry.
- TJ-0171: a figure change is under "Changed: tax figures" with its rule.
- TJ-0172: no audit finding cited as "#N" (a GitHub issue link in the
  release notes) from v0.31.0 on.
- TJ-0181: README's command rows link to their own commands.md section.
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(doc, heading, level="## "):
    """The text under `heading` (a line starting with it) up to the next
    heading of the same level or above."""
    start = doc.index("\n" + level + heading) + 1
    stop = re.compile(r"^#{1,%d} " % len(level.strip()), re.M)
    m = stop.search(doc, start + len(level) + len(heading))
    return doc[start:m.start() if m else len(doc)]


def _version(v):
    return tuple(int(x) for x in v.split("."))


def _changelog_sections():
    """[(name, text)] of CHANGELOG.md: 'Unreleased' and 'vX.Y.Z'."""
    doc = _read("CHANGELOG.md")
    parts = re.split(r"^## ", doc, flags=re.M)[1:]
    return [(p.split(None, 1)[0], p) for p in parts]


class TestFxCashCloseKeys(unittest.TestCase):
    """TJ-0031."""

    def _close_keys(self):
        tree = ast.parse(_read("src/taxjson/lib/fx_cash_v2.py"))
        build = next(n for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name == "build")
        for node in ast.walk(build):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "close" \
                            and isinstance(v, ast.Dict):
                        return [x.value for x in v.keys
                                if isinstance(x, ast.Constant)]
        self.fail("lib/fx_cash_v2.build writes no \"close\" table")

    def test_filed_section_names_every_close_key(self):
        keys = self._close_keys()
        self.assertIn("accounts", keys)
        text = _section(_read("docs/settings.md"),
                        "filed/YEAR.json: close-year locks")
        missing = [k for k in keys if f"`{k}`" not in text]
        self.assertEqual(missing, [], "docs/settings.md 'filed/YEAR.json' "
                         "does not name these keys of fx_cash_v2's close")

    def test_filed_section_says_what_the_carry_refuses(self):
        text = _section(_read("docs/settings.md"),
                        "filed/YEAR.json: close-year locks")
        self.assertIn("another base currency", text)
        self.assertIn("other country", text)


class TestT1135UpgradeNote(unittest.TestCase):
    """TJ-0058: the D02 change moved a filing figure on re-run."""

    def test_v0350_notes_name_the_t1135_change(self):
        text = _section(_read("docs/upgrading.md"), "v0.35.0", "### ")
        self.assertIn("tjs t1135", text)
        self.assertIn("D02", text)
        self.assertIn("CA-RPT-12", text)


class TestDeveloperDocCounts(unittest.TestCase):
    """TJ-0059."""

    # `path` (31k lines) / `path` (about 8.3k): a count for one file.
    _COUNT = re.compile(r"`([\w/.-]+\.py)` \((?:about )?(\d+(?:\.\d+)?)k"
                        r"(?: lines)?\)")

    def test_line_counts_are_within_ten_percent(self):
        bad, seen = [], 0
        for doc in ("AGENTS.md", "docs/architecture-map.md"):
            text = " ".join(_read(doc).split())
            for path, k in self._COUNT.findall(text):
                seen += 1
                said = float(k) * 1000
                n = _read(path).count("\n")
                if abs(n - said) > said * 0.10:
                    bad.append(f"{doc}: {path} is {n} lines, not about "
                               f"{k}k")
        self.assertGreaterEqual(seen, 3)
        self.assertEqual(bad, [], "update the figure the doc gives")

    # `a`, `b` and `c` (about 4.4k to 5.2k each): one range for the files
    # named since the paragraph's previous closing parenthesis.
    _RANGE = re.compile(r"\((?:about )?(\d+(?:\.\d+)?)k? to (\d+(?:\.\d+)?)k"
                        r" each\)")

    def test_range_counts_cover_each_file(self):
        """TJ-0228: the map's "about 4.4k to 5.2k each" was not checked."""
        bad, seen = [], 0
        for doc in ("AGENTS.md", "docs/architecture-map.md"):
            for para in re.split(r"\n\s*\n", _read(doc)):
                text = " ".join(para.split())
                for m in self._RANGE.finditer(text):
                    named = text[:m.start()]
                    named = named[named.rfind(")") + 1:]
                    paths = re.findall(r"`([\w/.-]+\.py)`", named)
                    self.assertGreaterEqual(len(paths), 2, named)
                    lo, hi = float(m.group(1)) * 1000, float(m.group(2)) * 1000
                    for path in paths:
                        seen += 1
                        n = _read(path).count("\n")
                        if not lo * 0.90 <= n <= hi * 1.10:
                            bad.append(f"{doc}: {path} is {n} lines, not "
                                       f"about {m.group(1)}k to "
                                       f"{m.group(2)}k")
        self.assertGreaterEqual(seen, 4)
        self.assertEqual(bad, [], "update the range the doc gives")

    def test_ci_quick_usage_line(self):
        head = _read("scripts/ci.sh").split("\nset -u", 1)[0]
        line = next(ln for ln in head.splitlines() if "--quick" in ln)
        # ci.sh skips only the fuzz stage under --quick (`!= quick`).
        self.assertIn("fuzz", line)
        self.assertNotIn("only", line)


class TestUpgradeFixRecord(unittest.TestCase):
    """TJ-0129: since v0.28.0 an upgrade-only fix is recorded by its
    troubleshooting entry's Fixed in, not in upgrading.md's list."""

    def test_both_pages_say_where_upgrade_fixes_are(self):
        up = _read("docs/upgrading.md")
        listed = [_version(v) for v in
                  re.findall(r"^#### Fixed in v(\d+\.\d+\.\d+)$", up, re.M)]
        ts = _read("docs/troubleshooting.md")
        fixed = []
        for e in re.split(r"^### ", ts, flags=re.M)[1:]:
            fx = re.search(r"^- \*\*Fix:\*\*\s*(.*)$", e, re.M)
            fi = re.search(r"^- \*\*Fixed in:\*\*\s*`v(\d+\.\d+\.\d+)`", e,
                           re.M)
            if fx and fi and fx.group(1).lower().startswith("upgrade"):
                fixed.append(_version(fi.group(1)))
        self.assertTrue(listed and fixed)
        if max(fixed) <= max(listed):
            return              # the list is current: either form is true
        stop = "v" + ".".join(map(str, max(listed)))
        header = ts.split("\n## ", 1)[0]
        self.assertNotIn("are listed by release in `docs/upgrading.md`",
                         header)
        self.assertIn("stays listed here", header)
        self.assertIn(stop, header)
        intro = _section(up, "Problems an upgrade fixes").split("####", 1)[0]
        self.assertIn("troubleshooting.md", intro)
        self.assertIn("Fixed in", intro)
        self.assertIn(f"stops at {stop}", intro)
        agents = " ".join(_read("AGENTS.md").split())
        self.assertNotIn("one only an upgrade fixes gets a line", agents)
        readme = _read("README.md")
        self.assertNotIn("upgrading.md#problems-an-upgrade-fixes", readme)


class TestDiffUnhashableEntry(unittest.TestCase):
    """TJ-0170."""

    def test_entry_carries_the_message(self):
        ts = _read("docs/troubleshooting.md")
        hits = [e for e in re.split(r"^### ", ts, flags=re.M)[1:]
                if "unhashable type" in e.split("\n", 1)[0]
                and "taxjson-diff" in e.split("\n", 1)[0]]
        self.assertEqual(len(hits), 1)
        e = hits[0]
        self.assertRegex(e, r"(?m)^- \*\*Fix:\*\* upgrade")
        self.assertRegex(e, r"(?m)^- \*\*Fixed in:\*\* `v0\.31\.1`$")
        self.assertRegex(e, r"(?m)^- \*\*Code:\*\* "
                         r"`src/taxjson/bin/taxjson_diff\.py` — `make_key`")

    def test_what_the_entry_says_holds(self):
        from taxjson.bin.taxjson_diff import make_key
        a = make_key({"symbol": "QZT.TO", "wash_replacements":
                      [{"date": "2025-03-10", "quantity": 10.0}]},
                     ["symbol", "wash_replacements"])
        hash(a)
        b = make_key({"w": {"x": 1, "y": 2}}, ["w"])
        c = make_key({"w": {"y": 2, "x": 1}}, ["w"])
        self.assertEqual(b, c)


class TestChangelogFigureChanges(unittest.TestCase):
    """TJ-0171: docs/releasing.md — a change that alters a filed number
    names the rule and the direction."""

    def test_every_tax_figure_change_names_a_known_rule(self):
        from taxjson.lib import tax_logic
        known = set(tax_logic.catalog())
        bad, seen = [], 0
        for name, sec in _changelog_sections():
            for m in re.finditer(r"^### Changed: tax figures[^\n]*\n(.*?)"
                                 r"(?=^### |\Z)", sec, re.M | re.S):
                for b in re.split(r"^- ", m.group(1), flags=re.M)[1:]:
                    seen += 1
                    ids = re.findall(r"\b(?:CA|US)-[A-Z0-9]+-\d+\b", b)
                    if not ids or set(ids) - known:
                        bad.append(f"{name}: {b[:60]!r} names "
                                   f"{ids or 'no rule'}")
        self.assertGreaterEqual(seen, 2)
        self.assertEqual(bad, [])

    def test_v0311_futures_change_is_a_figure_change(self):
        sec = dict(_changelog_sections())["v0.31.1"]
        changed = _section("\n" + sec, "Changed: tax figures", "### ")
        self.assertIn("US futures", changed)
        self.assertIn("`US-FUT-01`", changed)
        self.assertIn("up\n  or down", changed)
        rest = sec.replace(changed, "")
        self.assertNotIn("US futures: partial closes", rest)


class TestChangelogCitations(unittest.TestCase):
    """TJ-0172: GitHub links a bare '#81' in a release body to the public
    repository's issue or pull request 81. Audit findings are cited as
    'audit 81' / 'audit D02' / 'audit 2026-10-25 year-end#2' (a '#' after
    a letter is not a link). From v0.24.1 on (decided 2026-10-29: the
    v0.24.1 to v0.30.1 sections' numbers, the earlier repository's, read
    "audit N" too); docs/troubleshooting.md cites none as "(issue #N)"."""

    _LINK = re.compile(r"(?<![\w/&#`])#\d+\b")

    def test_no_issue_shaped_citation(self):
        bad = []
        for name, sec in _changelog_sections():
            if name != "Unreleased" and _version(name[1:]) < (0, 24, 1):
                continue
            for n, line in enumerate(sec.splitlines(), 1):
                if self._LINK.search(line):
                    bad.append(f"{name} +{n}: {line.strip()[:70]}")
        self.assertEqual(bad, [])

    def test_no_issue_citation_in_troubleshooting(self):
        text = _read("docs/troubleshooting.md")
        self.assertEqual(re.findall(r"\(issue #\d+", text), [])


class TestReadmeCommandLinks(unittest.TestCase):
    """TJ-0181."""

    @staticmethod
    def _anchors(rel):
        out, seen, fence = set(), {}, False
        for line in _read(rel).splitlines():
            if line.startswith("```"):
                fence = not fence
                continue
            m = None if fence else re.match(r"^#{1,6}\s+(.*?)\s*$", line)
            if m:
                s = re.sub(r"[^\w\- ]", "", m.group(1).lower()).replace(" ",
                                                                         "-")
                k = seen.get(s, 0)
                seen[s] = k + 1
                out.add(s if k == 0 else f"{s}-{k}")
        return out

    def test_each_command_row_links_to_its_section(self):
        rows = re.findall(r"\[`tjs ([^`]+)`\]\(docs/commands\.md#"
                          r"([^)]+)\)", _read("README.md"))
        self.assertGreater(len(rows), 50)
        anchors = self._anchors("docs/commands.md")
        bad = [f"{cmd} -> #{a}" for cmd, a in rows
               if a != "tjs-" + cmd.replace(" ", "-") or a not in anchors]
        self.assertEqual(bad, [])

    def test_readme_doc_anchors_resolve(self):
        bad = []
        for rel, frag in re.findall(r"\]\((docs/[\w-]+\.md)#([^)\s]+)\)",
                                    _read("README.md")):
            if frag not in self._anchors(rel):
                bad.append(f"{rel}#{frag}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
