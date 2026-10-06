"""`taxjson ticker-map --suggest [--write]`: every ticker.map line the
last run suggested, with its reason; --write appends the chosen ones
(--all, or one by one on a terminal), each under a comment, never one
the map already answers, keeping a backup. Synthetic data only.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import ticker_map_suggest as TS

import test_fix_cross_listings as XT

REPO = Path(__file__).resolve().parents[1]


def _tjs(root, *args, stdin=None):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL if stdin is None else stdin, timeout=300)


_DIAG = """\
warning: ATTENTION: margin_ib.csv: IB lists one stock (contract id 1) under \
several symbols: OLDX, NEWX — a ticker change. Each symbol is booked as its \
own security until you join them in ticker.map, e.g. `GLOBAL OLDX.US NEWX.US` \
(old symbol first, as first traded).
warning: ATTENTION: q.csv: Questrade symbol SAMPA.TO looks renamed to \
SAMPB.TO — if they are one security add to ticker.map:  GLOBAL SAMPA.TO \
SAMPB.TO  — SAMPA.TO stops on 2025-02-03 with 5 share(s) still open.
warning: ATTENTION: crypto id: SAMPC has no CRYPTO line in ticker.map, so it \
is priced as Yahoo SAMPC-USD. If the numbered id is your coin, add to \
ticker.map:
    CRYPTO SAMPC SAMPC12345
note: a template is never a suggestion: add `CRYPTO SAMPD SAMPD<number>` or \
`GLOBAL OLD NEW` or `RENAME OLD NEW YYYY-MM-DD late=fold|separate`.
"""


class TestGather(unittest.TestCase):
    def test_diag_lines_are_read_templates_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "margin_ib.json.diag").write_text(_DIAG)
            got = [s.line for s in TS.gather(root)]
        self.assertEqual(got, ["GLOBAL OLDX.US NEWX.US",
                               "GLOBAL SAMPA.TO SAMPB.TO",
                               "CRYPTO SAMPC SAMPC12345"])

    def test_the_map_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "margin_ib.json.diag").write_text(_DIAG)
            (root / "ticker.map").write_text(
                "GLOBAL OLDX.US OTHER.US\nDISTINCT SAMPA.TO SAMPB.TO\n"
                "CRYPTO SAMPC SAMPC999\n")
            offer, skipped = TS.pending(root)
        self.assertEqual(offer, [])
        self.assertEqual(sorted(w for _s, w in skipped), [
            "ticker.map already maps OLDX.US",
            "ticker.map has a CRYPTO line for it",
            "ticker.map keeps the two apart (DISTINCT)"])

    def test_two_targets_for_one_symbol_offer_the_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "a.diag").write_text(
                "warning: x: add `GLOBAL OLDX.US NEWX.US` here\n"
                "warning: y: add `GLOBAL OLDX.US NEWY.US` here\n")
            offer, skipped = TS.pending(root)
        self.assertEqual([s.line for s in offer], ["GLOBAL OLDX.US NEWX.US"])
        self.assertEqual(len(skipped), 1)

    def test_appended_text(self):
        s = TS.Suggestion("TOBASE A.US A.TO", "rrsp: a # journal", "x")
        self.assertEqual(
            TS.appended_text("GLOBAL B.US C.US", [s], today="2025-01-02"),
            "GLOBAL B.US C.US\n\n# taxjson ticker-map --suggest "
            "2025-01-02: rrsp: a journal\nTOBASE A.US A.TO\n")


class TestCommand(unittest.TestCase):
    """A run whose listing journal is NOT joined (the names differ in the
    share class): the run suggests the TOBASE line; the command lists it,
    and --write --all appends it once."""

    def test_suggest_and_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = XT._projects(
                tmp, tail="GLOBAL SAMPZ.TO SAMPY.TO\n",
                in_desc="SAMPQ ENERGY INC PFD SER 2 TRANSFER")["canada"]
            self.assertEqual(_tjs(root, "run", "--no-input").returncode, 0)
            r = _tjs(root, "ticker-map", "--suggest")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("TICKER.MAP SUGGESTIONS — 1 from the last run",
                          r.stdout)
            self.assertIn("\nTOBASE SAMPQ.TO SAMPR.TO\n", r.stdout)
            self.assertIn("another name of the listings states another share",
                          " ".join(r.stdout.split()))
            js = json.loads(_tjs(root, "ticker-map", "--suggest",
                                 "--json").stdout)
            self.assertEqual([s["line"] for s in js["suggestions"]],
                             ["TOBASE SAMPQ.TO SAMPR.TO"])
            # Not a terminal: nothing can be asked.
            r = _tjs(root, "ticker-map", "--suggest", "--write")
            self.assertEqual(r.returncode, 2)
            self.assertIn("--all", r.stderr)
            # --all appends it, with a comment, keeping a backup.
            r = _tjs(root, "ticker-map", "--suggest", "--write", "--all")
            self.assertEqual(r.returncode, 0, r.stderr)
            text = (root / "ticker.map").read_text()
            self.assertTrue(text.startswith("GLOBAL SAMPZ.TO SAMPY.TO\n"))
            self.assertIn("# taxjson ticker-map --suggest ", text)
            self.assertTrue(text.endswith("\nTOBASE SAMPQ.TO SAMPR.TO\n"))
            self.assertEqual((root / "ticker.map.bak").read_text(),
                             "GLOBAL SAMPZ.TO SAMPY.TO\n")
            # Idempotent: the line is now answered by the map.
            r = _tjs(root, "ticker-map", "--suggest", "--write", "--all")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((root / "ticker.map").read_text(), text)
            self.assertEqual(text.count("TOBASE SAMPQ.TO SAMPR.TO"), 1)
            r = _tjs(root, "ticker-map", "--suggest")
            self.assertIn("— 0 from the last run", r.stdout)
            self.assertIn("already in ticker.map", r.stdout)

    def test_needs_suggest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = XT._projects(tmp)["canada"]
            r = _tjs(root, "ticker-map")
            self.assertEqual(r.returncode, 2)
            self.assertIn("--suggest", r.stderr)


if __name__ == "__main__":
    unittest.main()
