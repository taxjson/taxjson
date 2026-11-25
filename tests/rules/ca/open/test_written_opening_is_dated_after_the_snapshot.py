"""TJ-5243: `--write-missing-history` beside an opening snapshot.

An opening snapshot replaces a `.tt` `OPENING ... cost=unknown` line
dated on or before it (CA-OPEN-03 / US-OPEN-03): the line opens nothing.
With such a line and a later sale beyond the snapshot's units (25 sold
of 10), the writer compared its sizing (15) with the line there and
said "inputs/margin/trades.tt:1 opens 10, the books size 15 — left as it
is (edit the line to change it)" and "nothing to add": an edit of that
line changes nothing, and no line was written for the 15 units. With no
line there, it wrote one dated the day before the account's first row,
which the snapshot replaced in turn.

Now a written line is dated the day after the snapshot (the snapshot is
the position at its day's end), and a line there that the run says the
snapshot replaced is written over in its place: an account has one
OPENING cost=unknown line per symbol. The writer names the line, the
snapshot, and where it wrote, in one line. Every line to write over, in
every file, is checked before any is written.

Every fixture is synthetic: invented tickers, amounts and dates.
"""
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, settings_for
from taxjson.rules import ids as R
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_CUR = {"canada": "CAD", "usa": "USD"}
_SFX = {"canada": ".TO", "usa": ".US"}

# The entry's repro: a line the snapshot replaces, 25 sold of its 10.
_REPRO = ("OPENING 2024-12-01 SYQ{s} 10 cost=unknown\n"
          "OPENING 2024-12-31 SYQ{s} 10 {c} 100{lot}\n"
          "BUYSELL 2025-02-03 10:00:00 SYQ{s} -25 {c} 12 300 0\n")
# No line there: the snapshot and the sale.
_NO_LINE = ("OPENING 2024-12-31 SYQ{s} 10 {c} 100{lot}\n"
            "BUYSELL 2025-02-03 10:00:00 SYQ{s} -25 {c} 12 300 0\n")
# The control: no snapshot; the line is dated before the first row.
_NO_SNAPSHOT = ("BUYSELL 2025-01-06 10:00:00 SYQ{s} 10 {c} 10 100 0\n"
                "BUYSELL 2025-02-03 10:00:00 SYQ{s} -25 {c} 12 300 0\n")
# Two replaced lines of one CRLF file, one indented, one with a comment.
_TWO = ("# made up\r\n"
        "OPENING 2024-12-01 SYQ{s} 10 cost=unknown   # my note; keep\r\n"
        "  OPENING 2024-11-15 QZB{s} 7 cost=unknown\r\n"
        "OPENING 2024-12-31 SYQ{s} 10 {c} 100{lot}\r\n"
        "OPENING 2024-12-31 QZB{s} 5 {c} 50{lot}\r\n"
        "BUYSELL 2025-01-02 10:00:00 SYQ{s} -25 {c} 12 300 0\r\n"
        "BUYSELL 2025-02-03 10:00:00 QZB{s} -9 {c} 12 108 0\r\n")


class _WriterCases:
    COUNTRY = ""

    def _project(self, tt, other=""):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        c = self.COUNTRY
        books = {"margin": tt, **({"other": other} if other else {})}
        (root / "taxjson.toml").write_text(
            settings_for(c, year=2025, source_currencies=[])
            + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in books))
        for a, rows in books.items():
            acct = root / "inputs" / a
            acct.mkdir(parents=True)
            (acct / "trades.tt").write_bytes(rows.format(
                s=_SFX[c], c=_CUR[c],
                lot=" 2024-06-03" if c == "usa" else "").encode())
        self._ok(cli(root, "run", "--no-input"))
        return root

    def _ok(self, r):
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return r

    def _write(self, root):
        return self._ok(cli(root, "find-missing-history",
                            "--write-missing-history"))

    def _openings(self, root):
        return [ln for p in sorted((root / "inputs" / "margin").glob("*.tt"))
                for ln in p.read_text().splitlines()
                if ln.startswith("OPENING") and "cost=unknown" in ln]

    def test_the_replaced_line_is_written_over_dated_after_the_snapshot(self):
        root = self._project(_REPRO)
        r = self._write(root)
        sym = "SYQ" + _SFX[self.COUNTRY]
        self.assertNotIn("left as it is", r.stderr)
        self.assertIn(f"margin {sym}: inputs/margin/trades.tt:1 rewritten as "
                      f"`OPENING 2025-01-01 {sym} 15 cost=unknown`, dated "
                      f"after the opening snapshot of {sym} dated 2024-12-31, "
                      "which replaced the line there (OPENING 2024-12-01 "
                      f"{sym} 10 cost=unknown); the previous file kept as "
                      "inputs/margin/trades.tt.bak.", " ".join(r.stderr.split()))
        lines = (root / "inputs" / "margin" / "trades.tt").read_text() \
            .splitlines()
        self.assertTrue(lines[0].startswith(
            f"OPENING 2025-01-01 {sym} 15 cost=unknown"), lines[0])
        self.assertEqual(len(self._openings(root)), 1)
        # The previous file is kept beside it.
        self.assertTrue((root / "inputs" / "margin" / "trades.tt.bak")
                        .is_file())

    def test_the_written_line_opens_the_units_on_the_next_run(self):
        root = self._project(_REPRO)
        self._write(root)
        out = self._ok(cli(root, "run", "--no-input"))
        self.assertNotIn("ATTENTION: opening", out.stdout + out.stderr)
        r = self._ok(cli(root, "find-missing-history"))
        self.assertNotIn("REPLACED", r.stdout)
        self.assertIn("COVERED by missing_history.tt", r.stdout)

    def test_a_new_line_is_dated_after_the_snapshot(self):
        root = self._project(_NO_LINE)
        self._write(root)
        sym = "SYQ" + _SFX[self.COUNTRY]
        got = self._openings(root)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(
            f"OPENING 2025-01-01 {sym} 15 cost=unknown"), got)
        out = self._ok(cli(root, "run", "--no-input"))
        self.assertNotIn("ATTENTION: opening", out.stdout + out.stderr)

    def test_two_lines_of_a_crlf_file_keep_their_place_indent_and_comment(self):
        root = self._project(_TWO)
        tt = root / "inputs" / "margin" / "trades.tt"
        before = tt.read_bytes()
        self._write(root)
        s, c = _SFX[self.COUNTRY], _CUR[self.COUNTRY]
        lines = tt.read_bytes().split(b"\r\n")
        self.assertEqual(len(lines), 8)              # every end kept: CRLF
        self.assertNotIn(b"\n", b"".join(lines))
        self.assertTrue(lines[1].startswith(
            f"OPENING 2025-01-01 SYQ{s} 15 cost=unknown".encode()), lines[1])
        self.assertTrue(lines[1].endswith(b"  # my note; keep"), lines[1])
        self.assertTrue(lines[2].startswith(
            f"  OPENING 2025-01-01 QZB{s} 4 cost=unknown".encode()), lines[2])
        self.assertEqual([lines[0]] + lines[3:], [ln for i, ln in enumerate(
            before.split(b"\r\n")) if i not in (1, 2)])
        self.assertIn(f"{c} 100".encode(), lines[3])
        # One backup, the file as it was.
        self.assertEqual((root / "inputs" / "margin" / "trades.tt.bak")
                         .read_bytes(), before)
        out = self._ok(cli(root, "run", "--no-input"))
        self.assertNotIn("ATTENTION: opening", out.stdout + out.stderr)

    def test_the_same_symbol_in_two_accounts_is_written_in_each_file(self):
        other = _REPRO.replace("-25 {c} 12 300", "-30 {c} 12 360")
        root = self._project(_REPRO, other=other)
        self._write(root)
        sym = "SYQ" + _SFX[self.COUNTRY]
        for acct, q in (("margin", 15), ("other", 20)):
            with self.subTest(account=acct):
                first = (root / "inputs" / acct / "trades.tt").read_text() \
                    .splitlines()[0]
                self.assertTrue(first.startswith(
                    f"OPENING 2025-01-01 {sym} {q} cost=unknown"), first)
                self.assertFalse((root / "inputs" / acct /
                                  "missing_history.tt").exists())
        out = self._ok(cli(root, "run", "--no-input"))
        self.assertNotIn("ATTENTION: opening", out.stdout + out.stderr)

    def test_with_no_snapshot_the_line_is_dated_before_the_first_row(self):
        # The control: as before, the day before the account's first row.
        root = self._project(_NO_SNAPSHOT)
        self._write(root)
        sym = "SYQ" + _SFX[self.COUNTRY]
        got = self._openings(root)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(
            f"OPENING 2025-01-05 {sym} 15 cost=unknown"), got)


@rule(R.CA_OPEN_03)
class TestCanada(_WriterCases, unittest.TestCase):
    COUNTRY = "canada"


@rule(R.US_OPEN_03)
class TestUs(_WriterCases, unittest.TestCase):
    COUNTRY = "usa"


class TestTheSwapKeepsTheFile(unittest.TestCase):
    """lib/missing_history apply_tt_swaps: the line's own place only."""

    def test_line_ends_a_comment_and_the_other_lines_are_kept(self):
        from taxjson.lib import missing_history as MH
        from _tmpfiles import private_dir
        root = Path(private_dir())
        acct = root / "inputs" / "margin"
        acct.mkdir(parents=True)
        tt = acct / "trades.tt"
        tt.write_bytes(b"# made up\r\n"
                       b"OPENING 2024-12-01 SYQ.TO 10 cost=unknown  # old\r\n"
                       b"OPENING 2024-12-31 SYQ.TO 10 CAD 100\r\n")
        old = MH.TtOpening("margin", "2024-12-01", "SYQ.TO", 10.0,
                           "inputs/margin/trades.tt:2", path=str(tt))
        plan = MH.TtWritePlan(root)
        plan.swap.append((MH.TtLine("margin", "SYQ.TO", 15.0, "2025-01-01"),
                          old, ("SYQ.TO", "2024-12-31")))
        MH.apply_tt_swaps(plan)
        self.assertEqual(tt.read_bytes(),
                         b"# made up\r\n"
                         b"OPENING 2025-01-01 SYQ.TO 15 cost=unknown  # old\r\n"
                         b"OPENING 2024-12-31 SYQ.TO 10 CAD 100\r\n")

    def test_a_line_changed_since_it_was_read_is_refused(self):
        from taxjson.lib import missing_history as MH
        from _tmpfiles import private_dir
        root = Path(private_dir())
        tt = root / "trades.tt"
        tt.write_text("OPENING 2024-12-01 SYQ.TO 12 cost=unknown\n")
        old = MH.TtOpening("margin", "2024-12-01", "SYQ.TO", 10.0,
                           "inputs/margin/trades.tt:1", path=str(tt))
        plan = MH.TtWritePlan(root)
        plan.swap.append((MH.TtLine("margin", "SYQ.TO", 15.0, "2025-01-01"),
                          old, ("SYQ.TO", "2024-12-31")))
        with self.assertRaises(MH.TtSwapError):
            MH.apply_tt_swaps(plan)
        self.assertEqual(tt.read_text(),
                         "OPENING 2024-12-01 SYQ.TO 12 cost=unknown\n")

    def test_a_line_changed_in_a_later_file_writes_no_file(self):
        # Every file's lines are checked before any is written.
        from taxjson.lib import missing_history as MH
        from _tmpfiles import private_dir
        root = Path(private_dir())
        plan = MH.TtWritePlan(root)
        files = {}
        for name, qty in (("a.tt", 10), ("b.tt", 12)):
            files[name] = root / name
            files[name].write_text("OPENING 2024-12-01 SYQ.TO "
                                   f"{qty} cost=unknown\n")
            plan.swap.append((
                MH.TtLine("margin", "SYQ.TO", 15.0, "2025-01-01"),
                MH.TtOpening("margin", "2024-12-01", "SYQ.TO", 10.0,
                             f"inputs/margin/{name}:1", path=str(files[name])),
                ("SYQ.TO", "2024-12-31")))
        with self.assertRaisesRegex(MH.TtSwapError, "nothing written .run `tjs run`"):
            MH.apply_tt_swaps(plan)
        self.assertEqual(files["a.tt"].read_text(),
                         "OPENING 2024-12-01 SYQ.TO 10 cost=unknown\n")
        self.assertFalse((root / "a.tt.bak").exists())


if __name__ == "__main__":
    unittest.main()
