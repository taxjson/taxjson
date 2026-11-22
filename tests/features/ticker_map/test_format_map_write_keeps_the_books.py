"""Pre-release review of the dated renames (v0.24 dated events).

- H2: `tjs format-map --write` never changes the books: it simulates
  the rename events the run books after the move (the project's .tt lines
  plus the moved map lines) and refuses, writing nothing, unless they are
  the ones it books now; a moved line that repeats a .tt line written
  differently is reported. Every declaration of one change is one event
  whatever order the files are read in (earliest date; late= per account).
- H3: dated renames apply in date order, and an account holds OLD when an
  earlier rename moved a position into it (a chain A -> B -> C).
- H5 / M8: a .tt RENAME applies only to accounts of its declaring
  account's kind (securities or crypto).
- M3: contradictory declarations stop the run naming the lines (a swap or
  cycle, one change on two dates).
- M4: late= describes one account's rows: its own account's late rows,
  and those of accounts without a line of their own.
- Lows: --check, symlinked inputs folder, idempotent .tt append, the
  dated_events.state of a failed run, read_state guards, an OLD row on
  the event date, the bounded hint regexes, map rules beside a .tt
  RENAME, home_account and crypto accounts, the legacy JOURNAL wording.

Every fixture is SYNTHETIC: invented QZ* tickers, fake account names.

Formerly: test_fix_rename_review
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from taxjson.lib import dated_events as DE
from taxjson.lib import renames as RN
from taxjson.lib import rename_rows as RNR
from taxjson.lib.rename_decl import DatedRename
from tax_rules import rule
from tax_rules.dual import cli, projects_both
from taxjson.rules import ids as R

_HOME = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}
_TWO = ('[accounts.margin]\ntype = "taxable"\n\n'
        '[accounts.plan]\ntype = "sheltered"\n')


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _sum(root):
    r = cli(root, "sum", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _holdings(root, acct="margin"):
    p = root / "reports" / f"{acct}_holdings.toml"
    return "\n".join(ln for ln in p.read_text().splitlines()
                     if not ln.startswith("generated_at"))


def _books(root, accts=("margin",)):
    return (_sum(root),) + tuple(_holdings(root, a) for a in accts)


def _run(tc, root, *extra):
    r = cli(root, "run", "--no-input", *extra)
    tc.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


def _bt(day, sym, qty, cur, price):
    return (f"BUYSELL {day} 10:00:00 {sym} {qty} {cur} {price:.2f} "
            f"{abs(qty) * price:.2f} 0.00\n")


# ------------------------------------------------------------ H2

class TestFormatMapKeepsTheBooks(unittest.TestCase):
    """A ticker.map dated RENAME with a .tt twin in the project.

    Formerly: test_fix_rename_review.TestFormatMapKeepsTheBooks"""

    def _files(self, x, cur, tt_line, map_line):
        return {
            "ticker.map": map_line,
            "inputs/margin/a.tt": (
                _bt("2024-01-10", f"QZOLD.{x}", 10, cur, 10.0)
                + tt_line
                # a late buy in the old ticker, then the whole sale
                + _bt("2025-04-06", f"QZOLD.{x}", 5, cur, 10.0)
                + _bt("2025-06-03", f"QZNEW.{x}", -15, cur, 11.0))}

    def _same_books(self, country, tt_line, map_line, total):
        x, cur = _HOME[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=self._files(
                x, cur, tt_line.format(x=x), map_line.format(x=x)))[country]
            _run(self, root)
            before = _books(root)
            self.assertAlmostEqual(before[0]["totals"]["total"], total,
                                   delta=0.011)
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _out(w))
            self.assertIn("declare one change differently", _out(w))
            self.assertNotIn(f"RENAME QZOLD.{x}",
                             (root / "ticker.map").read_text())
            _run(self, root)
            self.assertEqual(_books(root), before)

    @rule(R.CA_ACB_RENAME)
    def test_canada_late_fold_kept_by_the_migration(self):
        # Input A: the map line holds late=fold, its .tt twin none: the
        # late buy is the renamed shares before and after (gain 15).
        self._same_books("canada",
                         "RENAME 2025-04-01 QZOLD.{x} QZNEW.{x}\n",
                         "RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=fold\n",
                         15.0)

    @rule(R.US_BASIS_RENAME)
    def test_usa_late_fold_kept_by_the_migration(self):
        self._same_books("usa",
                         "RENAME 2025-04-01 QZOLD.{x} QZNEW.{x}\n",
                         "RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=fold\n",
                         15.0)

    @rule(R.CA_ACB_RENAME)
    def test_canada_twin_on_another_date_is_one_event(self):
        # Input B: map 04-01 vs .tt 04-05 — one event on the earliest
        # date, whichever file holds which line.
        self._same_books("canada",
                         "RENAME 2025-04-05 QZOLD.{x} QZNEW.{x}\n",
                         "RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=fold\n",
                         15.0)

    @rule(R.US_BASIS_RENAME)
    def test_usa_twin_on_another_date_is_one_event(self):
        self._same_books("usa",
                         "RENAME 2025-04-05 QZOLD.{x} QZNEW.{x}\n",
                         "RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=fold\n",
                         15.0)

    def test_a_move_that_would_change_the_books_is_refused(self):
        # The map line says late=separate, the account's own .tt line
        # late=fold (its own choice): moving the map line into that
        # account would make it declare both — refused, nothing written.
        x, cur = _HOME["canada"]
        tmap = f"RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 late=separate\n"
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=self._files(
                x, cur, f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=fold\n",
                tmap))["canada"]
            _run(self, root)
            a_tt = (root / "inputs/margin/a.tt").read_text()
            w = cli(root, "format-map", "--write")
            self.assertEqual(w.returncode, 2, _out(w))
            out = _out(w)
            self.assertIn("would change the books — nothing was written",
                          out)
            self.assertIn("inputs/margin/a.tt:2", out)
            self.assertEqual((root / "ticker.map").read_text(), tmap)
            self.assertEqual((root / "inputs/margin/a.tt").read_text(),
                             a_tt)
            self.assertFalse((root / "inputs/margin/renames.tt").exists())
            self.assertFalse((root / "ticker.map.bak").exists())

    def test_resolution_ignores_the_reading_order(self):
        # The same declarations in any order give the same events.
        a = DatedRename("QZA.TO", "QZB.TO", "2025-04-05", "", "x/a.tt:1",
                        source="tt", account="margin", kind="securities")
        b = DatedRename("QZA.TO", "QZB.TO", "2025-04-01", "fold",
                        "x/renames.tt:4", source="tt", account="margin",
                        kind="securities")
        e1 = DE.resolve_renames([a, b], [])[0]
        e2 = DE.resolve_renames([b, a], [])[0]
        self.assertEqual([(e.date, e.late_for("margin"), e.late_for("tfsa"))
                          for e in e1],
                         [(e.date, e.late_for("margin"), e.late_for("tfsa"))
                          for e in e2])
        self.assertEqual([(e.date, e.late_for("margin")) for e in e1],
                         [("2025-04-01", "fold")])


# ------------------------------------------------------------ M3

def _decl(old, new, date, where, late="", acct="margin",
          kind="securities", source="tt"):
    return DatedRename(old, new, date, late, where, f"RENAME {date} {old} "
                       f"{new}", source=source, account=acct, kind=kind)


# ------------------------------------------------------------ lows

class TestFormatMapLows(unittest.TestCase):
    """Formerly: test_fix_rename_review.TestFormatMapLows"""

    def _project(self, td, accounts=_TWO, extra=None):
        x, cur = _HOME["canada"]
        files = {"ticker.map": (f"# renamed by its issuer\n"
                                f"RENAME QZOLD.{x} QZNEW.{x} 2025-04-01\n"),
                 "inputs/margin/m.tt": (
                     _bt("2024-01-10", f"QZOLD.{x}", 10, cur, 10.0)
                     + _bt("2025-06-03", f"QZNEW.{x}", -10, cur, 11.0))}
        files.update(extra or {})
        return projects_both(td, accounts=accounts, files=files)["canada"]

    def test_check_needs_no_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, accounts="")
            r = cli(root, "format-map", "--check")
            self.assertEqual(r.returncode, 1, _out(r))
            self.assertIn("dated events to migrate", _out(r))

    def test_inputs_folder_linked_outside_is_refused(self):
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as away:
            root = self._project(td)
            _run(self, root)
            (root / "inputs" / "margin").rename(Path(away) / "margin")
            os.symlink(Path(away) / "margin", root / "inputs" / "margin")
            tmap = (root / "ticker.map").read_text()
            w = cli(root, "format-map", "--write")
            self.assertEqual(w.returncode, 2, _out(w))
            # Refused before anything runs: every command stops on a
            # written folder linked outside the project (security
            # review LOW c).
            self.assertIn("symlinks to outside the project", _out(w))
            self.assertIn("inputs/margin/ ->", _out(w))
            self.assertFalse((Path(away) / "margin" / "renames.tt").exists())
            self.assertEqual((root / "ticker.map").read_text(), tmap)

    def test_restored_backup_does_not_duplicate_the_tt_lines(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            _run(self, root)
            tmap = (root / "ticker.map").read_text()
            w = cli(root, "format-map", "--write")
            self.assertEqual(w.returncode, 0, _out(w))
            tt = (root / "inputs/margin/renames.tt").read_text()
            # The user restores the old map (or a write stopped half-way).
            (root / "ticker.map").write_text(tmap)
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _out(w))
            self.assertEqual((root / "inputs/margin/renames.tt").read_text(),
                             tt)
            self.assertEqual(tt.count("# renamed by its issuer"), 1)

    def test_journal_gap_suggests_a_tt_journal_line(self):
        x, cur = _HOME["canada"]
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, extra={
                "ticker.map": f"JOURNAL QZG.{x} QZGB.{x}\n",
                "inputs/margin/m.tt": (
                    _bt("2025-03-03", f"QZG.{x}", 100, cur, 10.0)
                    + _bt("2025-03-06", f"QZGB.{x}", -100, cur, 8.0))})
            r = _run(self, root)
            self.assertIn("A JOURNAL line no longer moves units in the "
                          "holdings view", _out(r))
            d = cli(root, "format-map")
            self.assertEqual(d.returncode, 0, _out(d))
            self.assertIn(f"JOURNAL <date> QZG.{x} QZGB.{x} 100", _out(d))


class TestStateAndHints(unittest.TestCase):
    """Formerly: test_fix_rename_review.TestStateAndHints"""

    def test_state_cleared_by_a_failed_run(self):
        x, cur = _HOME["canada"]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={"inputs/margin/m.tt": (
                _bt("2024-01-10", f"QZA.{x}", 10, cur, 10.0)
                + f"RENAME 2025-03-01 QZA.{x} QZB.{x}\n")})["canada"]
            _run(self, root)
            st = root / "work" / DE.STATE
            self.assertTrue(DE.read_state(st)["renames"])
            (root / "inputs/margin/x.tt").write_text(
                f"RENAME 2025-03-01 QZA.{x} QZOTHER.{x}\n")
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(st.exists())

    def test_read_state_guards(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s"
            for doc in ({"format": DE.FORMAT, "renames": 5},
                        {"format": DE.FORMAT, "journals": "x",
                         "renames": [1, {"old": "A"}]}, [1, 2], "x"):
                p.write_text(json.dumps(doc))
                st = DE.read_state(p)
                self.assertEqual(set(st), {"journals", "renames"})
                self.assertTrue(all(isinstance(r, dict) for v in st.values()
                                    for r in v))

    def test_an_old_row_on_the_event_date_is_late(self):
        ev = RNR.rename_events([{"action": "SPLIT", "symbol": "QZA",
                                "symbol_new": "QZB", "date": "2025-04-01",
                                "time": "00:00:00", "_acct": "m"}])
        rows = [{"action": "BUYSELL", "symbol": "QZA", "date": "2025-04-01",
                 "time": "10:00:00", "_acct": "m"},
                {"action": "BUYSELL", "symbol": "QZA", "date": "2025-04-01",
                 "time": "11:00:00", "_acct": "other"},
                {"action": "BUYSELL", "symbol": "QZA", "date": "2025-03-31",
                 "time": "15:00:00", "_acct": "m"}]
        late = RN.late_rows(rows, ev)
        self.assertEqual([r["_acct"] for r, _e in late], ["m", "other"])
        # A broker's rename row later that day: an earlier trade is not
        # late in its account in the US engine's order (trade date and
        # clock); the Canada engine takes the SPLIT ahead of every
        # execution of its day, so it is late there (third pre-release
        # review, 1). Without a country the order is refused.
        ev = RNR.rename_events([{"action": "SPLIT", "symbol": "QZA",
                                "symbol_new": "QZB", "date": "2025-04-01",
                                "time": "16:00:00", "_acct": "m"}])
        self.assertEqual(RN.late_rows(rows[:1], ev, "usa"), [])
        self.assertEqual(len(RN.late_rows(rows[:1], ev, "canada")), 1)
        with self.assertRaises(RN.RenameNeedsCountry):
            RN.late_rows(rows[:1], ev)

    def test_hint_regexes_are_bounded(self):
        text = ("RBC symbol A looks renamed to B " + "x " * 5000) * 2
        t0 = time.perf_counter()
        for _ in range(5):
            self.assertIsNone(RN._LOOKS_RE.search(text))
            self.assertIsNone(RN._LOOKS_TT_RE.search(text))
        self.assertLess(time.perf_counter() - t0, 2.0)
        ok = ("warning: ATTENTION: x.csv: Questrade symbol QZA looks renamed "
              "to QZB — if they are one security, add to a .tt file of this "
              "account:  RENAME 2025-04-01 QZA.TO QZB.TO  — QZA stops")
        m = RN._LOOKS_TT_RE.search(ok)
        self.assertEqual(m.groups()[1:], ("2025-04-01", "QZA.TO", "QZB.TO"))

    def test_map_rules_beside_a_tt_rename_are_warned(self):
        from taxjson.lib.ticker_map_rules import _parse_map_text
        decl = DE.Declarations(tt_declared=[
            _decl("QZA.TO", "QZB.TO", "2025-03-01", "inputs/m/a.tt:1")])
        for line, word in (("TOBASE QZB.TO QZB.US", "TOBASE"),
                           ("DELETE QZA.TO", "DELETE QZA.TO"),
                           ("DISTINCT QZA.TO QZB.TO", "DISTINCT")):
            with self.subTest(line=line):
                d = DE.Declarations(tt_declared=list(decl.tt_declared))
                tmap = _parse_map_text(line + "\n", "ticker.map")[0]
                self.assertEqual(DE.check_against_map(d, tmap), [])
                self.assertEqual(len(d.warnings), 1, d.warnings)
                self.assertIn(word, d.warnings[0])
                self.assertIn("inputs/m/a.tt:1", d.warnings[0])


if __name__ == "__main__":
    unittest.main()
