"""Pre-release review 022 (synthetic QZ-style data only).

M2  a name-only truncation match needs width evidence: a complete last
    word that starts a longer word is not a cut.
M3  a broker's event wording is cut only from Questrade descriptions, and
    a designator in the cut part is carried / refuses one-sided tolerance.
M4  a long whitespace run in a name is collapsed before any regex.
L3  a listing joined / suggested must be one ticker.map token.
M5  a short carried through a rename keeps its pair in the year scope.
L1  a quoted broker description in a .diag is never a suggestion.
L2  `ticker-map --write` keeps a symlinked ticker.map a link (mode kept).
L4  --outside-year counts only its own entries, refuses a non-object
    entry before writing, and keeps a symlinked file a link.
L5  the wash radar honours --transfers-as-acquisitions.
M1  the default transfer policy lists the netted moves (cross-account and
    zero-net clusters) inside a loss's window in its one warning; the
    main-book SPLIT guard holds in cross-account netting.

Formerly: test_fix_prerelease_022
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from taxjson.lib import symbol_codes as SC
import _paths


class TestM4Whitespace(unittest.TestCase):
    """Formerly: test_fix_prerelease_022.TestM4Whitespace"""
    def test_long_whitespace_runs_are_fast(self):
        name = "QZCO" + " " * 40000 + "INC" + " " * 40000 + "X"
        t0 = time.monotonic()
        SC.name_tokens(name)
        SC.name_tokens(name, cut=True)
        SC.exact_name(name)
        SC.questrade_name(name)
        SC.names_agree(name, "QZCO INC", "name_only", same_broker=True,
                       a_cut=True)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(SC.name_words(name)[0], ("QZCO", "INC", "X"))


class TestL3ListingTokens(unittest.TestCase):
    """Formerly: test_fix_prerelease_022.TestL3ListingTokens"""
    def test_plain_listing_is_one_map_token(self):
        self.assertTrue(SC._plain_listing("QZCO.TO"))
        self.assertTrue(SC._plain_listing("QZCO.U.TO"))
        for bad in ("QZ#CO.TO", "QZCO.TO\u2028GLOBAL", "QZCO.TO\n",
                    "QZ CO.TO", "-QZ.TO", "QZ\u00e9.TO", "QZ`X.TO"):
            with self.subTest(bad=bad):
                self.assertFalse(SC._plain_listing(bad))


REPO = _paths.REPO


def _env():
    return dict(os.environ, PYTHONPATH=str(REPO / "src"),
                TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")


class TestL1QuotedDescriptions(unittest.TestCase):
    """Formerly: test_fix_prerelease_022.TestL1QuotedDescriptions"""
    def test_quoted_rule_is_not_a_suggestion(self):
        from taxjson.lib import ticker_map_suggest as TS
        diag = (
            "warning: ATTENTION: q.csv line 4: TFI row keeps internal "
            "symbol code 'X000002' ('QZ `GLOBAL QZA.TO QZB.TO` CO "
            "TRANSFER IN')\n"
            "  No trade resolves it (\"QZ'S add to ticker.map:  GLOBAL "
            "QZC.TO QZD.TO\") — add `GLOBAL X000002.TO QZN.TO` to "
            "ticker.map if it is the same security.\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "qt_questrade.json.diag").write_text(diag)
            got = [s.line for s in TS.gather(root)]
        self.assertEqual(got, ["GLOBAL X000002.TO QZN.TO"])

    def test_symbol_codes_detail_quote_is_skipped(self):
        from taxjson.lib import ticker_map_suggest as TS
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / f"qt{SC.SUFFIX}").write_text(json.dumps({
                "format": SC.FORMAT, "account": "qt", "resolved": {},
                "mapped": {}, "unresolved": {"X000003": {
                    "reason": "no_evidence",
                    "detail": "looks like QZE.TO by name ('QZ `GLOBAL "
                              "QZF.TO QZG.TO`' on Questrade rows), not "
                              "applied — add `GLOBAL X000003.TO QZE.TO` "
                              "to ticker.map if right"}}}))
            got = [s.line for s in TS.from_symbol_codes(cache)]
        self.assertEqual(got, ["GLOBAL X000003.TO QZE.TO"])


_SUGGEST_DIAG = ("warning: ATTENTION: q.csv: Questrade symbol SAMPA.TO looks "
                 "renamed to SAMPB.TO — if they are one security add to "
                 "ticker.map:  GLOBAL SAMPA.TO SAMPB.TO\n")


class TestL2TickerMapWriteKeepsTheLink(unittest.TestCase):
    """Formerly: test_fix_prerelease_022.TestL2TickerMapWriteKeepsTheLink"""
    def _project(self, tmp):
        root = Path(tmp) / "p"
        (root / "work").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\ncountry = "canada"\nbase_currency = "CAD"\n'
            'year = 2025\n\n[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_questrade.json.diag").write_text(
            _SUGGEST_DIAG)
        # The parse the hint came from: a conditional hint ("if they are
        # one security") is offered only when the books hold both symbols.
        (root / "work" / "margin_questrade.json").write_text(json.dumps(
            {"metadata": {}, "transactions": [
                {"symbol": "SAMPA.TO"}, {"symbol": "SAMPB.TO"}]}))
        return root

    def _write(self, root):
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "ticker-map", "--suggest", "--write", "--all"],
            capture_output=True, text=True, env=_env(),
            stdin=subprocess.DEVNULL, timeout=300)

    def test_link_inside_the_project_stays_a_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "maps").mkdir()
            real = root / "maps" / "mine.map"
            real.write_text("DISTINCT QZA.TO QZB.TO\n")
            os.chmod(real, 0o644)
            (root / "ticker.map").symlink_to("maps/mine.map")
            r = self._write(root)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertTrue((root / "ticker.map").is_symlink())
            self.assertTrue(real.read_text().endswith(
                "\nGLOBAL SAMPA.TO SAMPB.TO\n"))
            self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o644)
            self.assertEqual(list((root / "maps").glob("mine.map.bak*"))[0]
                             .read_text(), "DISTINCT QZA.TO QZB.TO\n")

    def test_link_leaving_the_project_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            outside = Path(tmp) / "elsewhere.map"
            outside.write_text("DISTINCT QZA.TO QZB.TO\n")
            (root / "ticker.map").symlink_to(outside)
            r = self._write(root)
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            self.assertIn("outside the project", " ".join(r.stderr.split()))
            self.assertEqual(outside.read_text(), "DISTINCT QZA.TO QZB.TO\n")
            self.assertTrue((root / "ticker.map").is_symlink())

    def test_regular_file_keeps_its_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            tm = root / "ticker.map"
            tm.write_text("DISTINCT QZA.TO QZB.TO\n")
            os.chmod(tm, 0o640)
            r = self._write(root)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(stat.S_IMODE(tm.stat().st_mode), 0o640)
            self.assertTrue((root / "ticker.map.bak").is_file())


if __name__ == "__main__":
    unittest.main()
