"""v0.19.0 pre-release review pins (core output side).

L5  Text shown to a person (width > 0) never carries a control character
    other than newline and tab: broker data (a symbol, a description)
    with an ESC sequence, a BEL or a carriage return is shown escaped
    (`\\x1b`), through lib/out (wrap, fill, message, emit, tables,
    key/value blocks, Doc) and the paths that print captured lines for a
    person (stage_msg.console_lines / emit_line, the run's echo, the
    first-run list). Captured output (width 0: work/, reports/, a .diag)
    keeps its bytes.
I3  stage_msg.split_message is linear on an unclosed backtick followed
    by many clause breaks.

(M2 / L1-L3, the installer: test_installer_fetch. L4, the contract-size
roll-up: test_style_group_d.) Synthetic data only; no network.
"""
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

ESC = "\x1b"
SYM = "AB\x1b[31mC.TO"          # an ESC sequence inside a broker symbol
SHOWN = "AB\\x1b[31mC.TO"        # how a person sees it


class TestPrintable(unittest.TestCase):
    def test_controls_are_escaped_newline_and_tab_kept(self):
        from taxjson.lib.out import printable
        self.assertEqual(printable("a\x1bb\x07c\rd\x9be\x7ff\x00"),
                         "a\\x1bb\\x07c\\x0dd\\x9be\\x7ff\\x00")
        self.assertEqual(printable("a\nb\tc é — ✓"), "a\nb\tc é — ✓")
        self.assertEqual(printable(printable(SYM)), SHOWN)

    def test_wrap_and_messages_escape_only_for_display(self):
        from taxjson.lib import out
        self.assertEqual(out.wrap(f"sold {SYM}", 100), [f"sold {SHOWN}"])
        self.assertEqual(out.fill(f"sold {SYM}", 100), f"sold {SHOWN}")
        self.assertEqual(out.wrap(f"sold {SYM}", 0), [f"sold {SYM}"])
        self.assertEqual(out.message("note", SYM, details=[SYM],
                                     width_=100),
                         [f"Info: {SHOWN}", f"  {SHOWN}"])
        self.assertEqual(out.message("note", SYM, width_=0),
                         [f"note: {SYM}"])
        for width, want in (("100", SHOWN), ("0", SYM)):
            with self.subTest(width=width), \
                    mock.patch.dict(os.environ, {"TAXJSON_WIDTH": width}):
                buf = io.StringIO()
                out.warn(f"bad row {SYM}", details=[f"row: {SYM}"],
                         file=buf)
                self.assertIn(want, buf.getvalue())
                self.assertEqual(ESC in buf.getvalue(), want == SYM)
                self.assertEqual(out.shown(SYM, buf), want)

    def test_tables_key_values_and_docs(self):
        from taxjson.lib import out
        rows = [[SYM, "10"]]
        self.assertNotIn(ESC, "\n".join(out.fit_table(["SYM", "QTY"], rows,
                                                      width_=100)))
        self.assertIn(SHOWN, "\n".join(out.fit_table(["SYM", "QTY"], rows,
                                                     width_=100)))
        self.assertIn(SYM, "\n".join(out.fit_table(["SYM", "QTY"], rows,
                                                   width_=0)))
        kv = out.kv_lines([("symbol", SYM), (SYM, out.Verbatim(SYM))],
                          width_=100)
        self.assertNotIn(ESC, "\n".join(kv))
        self.assertIn(SYM, "\n".join(out.kv_lines(
            [("symbol", out.Verbatim(SYM))], width_=0)))
        d = out.Doc(f"TITLE {SYM}", width_=100)
        d.section(SYM).line(SYM).para(SYM).item(SYM).table(["S"], [[SYM]])
        self.assertNotIn(ESC, d.text())
        self.assertEqual(d.text().count(SHOWN), 6, d.text())
        d0 = out.Doc(f"TITLE {SYM}", width_=0)
        d0.section(SYM).line(SYM).para(SYM)
        self.assertEqual(d0.text().count(SYM), 4)

    def test_stage_lines_shown_to_a_person(self):
        from taxjson.lib import stage_msg
        line = f"warning: ATTENTION: {SYM}: a sale with\rno purchase"
        self.assertEqual(stage_msg.console_lines(line, "  ", width_=100),
                         [f"  Warning: ATTENTION: {SHOWN}: a sale with"
                          f"\\x0dno purchase"])
        self.assertEqual(stage_msg.console_lines(line, "  ", width_=0),
                         ["  " + line])
        # emit_line: as is into a captured stream, escaped on the real one.
        buf = io.StringIO()
        stage_msg.emit_line(line, file=buf)
        self.assertEqual(buf.getvalue(), line + "\n")
        real = io.StringIO()
        with mock.patch.object(sys, "__stderr__", real), \
                mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "100"}):
            stage_msg.emit_line(line, file=real)
        self.assertNotIn(ESC, real.getvalue())
        self.assertIn(SHOWN, real.getvalue())

    def test_first_run_list(self):
        from taxjson.lib import first_run
        doc = {"year": 2025, "no_purchase": [{"symbol": SYM, "account": "m"}],
               "income_not_held": [{"symbol": SYM, "account": "m"}]}
        shown = "\n".join(first_run.render(doc, width_=100))
        self.assertNotIn(ESC, shown)
        self.assertIn(SHOWN, shown)


def _rates(path: Path):
    d, lines = date(2024, 1, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.3500 boc")
        d += timedelta(days=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


class TestRunConsole(unittest.TestCase):
    """`taxjson run` over a synthetic project whose sale names a symbol
    carrying an ESC sequence: the console shows it escaped; the .diag and
    .sum files keep the raw bytes (they are captured, never displayed —
    byte for byte what the run wrote before this change)."""

    def test_console_escaped_captured_files_raw(self):
        tmp = Path(tempfile.mkdtemp(prefix="tj-l5-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        (tmp / "home").mkdir()
        root = tmp / "proj"
        (root / "inputs" / "m").mkdir(parents=True)
        (root / "inputs" / "m" / "a.tt").write_text(
            "BUYSELL 2025-01-06 10:00:00 ABC.TO 100 CAD 10 -1000 0\n"
            f"BUYSELL 2025-03-03 10:00:00 {SYM} -40 CAD 12 480 0\n")
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\nprovince = "ON"\n'
            'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
            'option_grant_timing_since = 2025\n\n'
            '[accounts.m]\ntype = "taxable"\n')
        _rates(root / "work" / "to_base.csv")
        env = {**os.environ, "HOME": str(tmp / "home"),
               "TAXJSON_OFFLINE": "1", "NO_COLOR": "1",
               "PYTHONPATH": str(SRC)}
        env.pop("TAXJSON_WIDTH", None)
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "run", "--no-input"], cwd=str(root),
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        console = r.stdout + r.stderr
        self.assertNotIn(ESC, console)
        self.assertIn(SHOWN.upper(), console.upper())
        diag = (root / "work" / "m_gains.json.diag").read_bytes()
        summ = (root / "reports" / "m.sum").read_bytes()
        for name, data in (("diag", diag), ("sum", summ)):
            self.assertIn(b"\x1b[31", data, name)
            self.assertNotIn(b"\\x1b", data, name)


class TestSplitMessageLinear(unittest.TestCase):
    def test_unclosed_backtick_with_many_breaks(self):
        from taxjson.lib.stage_msg import split_message
        text = "x" * 50 + " `" + "a. b" * 100_000
        t0 = time.perf_counter()
        self.assertEqual(split_message(text), (text, ""))
        self.assertLess(time.perf_counter() - t0, 2.0)
        # Unchanged where it splits: a break past a closed span.
        msg = ("warning: " + "y" * 40 + " `a. b` closed. Then the fix")
        self.assertEqual(split_message(msg),
                         ("warning: " + "y" * 40 + " `a. b` closed.",
                          "Then the fix"))


if __name__ == "__main__":
    unittest.main()
