"""House output style (docs/output-style.md) for the position, period and
roll-up views, stats and crypto-sends: their diagnostics are headline +
indented detail with the `taxjson <cmd>:` prefix, their tables fit the
width (dropping the least important columns first), and the re-importable
`events PERIOD ACCOUNT` taxtext keeps its bytes. Synthetic style projects
(tests/_style.py) only."""
import unittest

from _style import assert_console, assert_styled, project

# The commands converted here (their stdout is in
# test_style_smoke.TestConvertedCommands.CASES).
_CMDS = [("list",), ("shares", "--options"), ("events",), ("trades",),
         ("divs",), ("dil",), ("fees",), ("gains",), ("leaps",), ("roc",),
         ("transfers",), ("ccd-sum",), ("dil-sum",), ("divs-sum",),
         ("fees-sum",), ("leaps-sum",), ("roc-sum",), ("trades-sum",),
         ("winners", "all"), ("stats",), ("stats", "--all-history"),
         ("crypto-sends",)]


class TestDiagnostics(unittest.TestCase):
    def test_stderr_is_styled(self):
        for country in ("canada", "usa"):
            p = project(country)
            for args in _CMDS:
                with self.subTest(country=country, args=args):
                    r = p.run(*args)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    assert_styled(self, r.stderr)
                    # The label starts each message, no program name;
                    # its later lines continue it flush-left.
                    assert_console(self, r.stderr)

    def test_note_is_a_headline_with_detail(self):
        r = project("canada").run("gains")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stderr.splitlines()
        i = next(i for i, ln in enumerate(lines)
                 if ln.startswith("Info: crypto account"))
        self.assertTrue(lines[i + 1].startswith("Its gains are in "))

    def test_errors_start_with_the_label(self):
        p = project("usa")
        r = p.run("list", "--date", "2024-13-01")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Error: --date 2024-13-01 is not a "
                      "real calendar date", r.stderr)
        # Captured (width 0, as the checklist reads it): the GNU bytes.
        r = p.run("list", "--date", "2024-13-01", TAXJSON_WIDTH=0)
        self.assertIn("taxjson list: error: --date 2024-13-01 is not a "
                      "real calendar date", r.stderr)
        r = p.run("fees-sum", "nosuch")
        self.assertEqual(r.returncode, 1)
        lines = r.stderr.strip().splitlines()
        self.assertTrue(lines[0].startswith(
            "Error: no files for account 'nosuch' in "
            "work/ — run"), lines)
        self.assertTrue(all(ln.startswith("  ") for ln in lines[1:]), lines)
        self.assertIn("run `taxjson run` first, or check the name",
                      " ".join(r.stderr.split()))
        r = p.run("winners", "--top", "0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Error: --top must be >= 1", r.stderr)


class TestFitted(unittest.TestCase):
    def test_transfers_drops_columns_on_a_narrow_terminal(self):
        r = project("canada").run("transfers", TAXJSON_WIDTH=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_styled(self, r.stdout, width=60)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("DATE"))
        self.assertEqual(head.split(), ["DATE", "ACCOUNT", "SYMBOL", "QTY",
                                        "VALUE", "CUR", "IN_BOOKS"])

    def test_stats_drops_the_largest_columns_first(self):
        r = project("canada").run("stats", TAXJSON_WIDTH=100)
        self.assertEqual(r.returncode, 0, r.stderr)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("CLASS"))
        self.assertNotIn("LARGEST_WIN", head)
        self.assertIn("PROFIT_FACTOR", head)
        # The counting notes: --details (Essentials first).
        self.assertIn("How the trades are counted",
                      project("canada").run("stats", "--details").stdout)
        # Unwrapped (captured), every column stays.
        r = project("canada").run("stats", TAXJSON_WIDTH=0)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("CLASS"))
        self.assertIn("LARGEST_WIN", head)

    def test_fees_sum_sections(self):
        # The Definitions: --details (Essentials first).
        r = project("canada").run("fees-sum", "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith(
            "TRADING FEES BY ACCOUNT / BROKERAGE — tax year 2024"))
        self.assertIn("Non-option vs option fees", lines)
        self.assertIn("Definitions", lines)
        self.assertTrue(lines[-1].startswith("- $/UNIT")
                        or lines[-2].startswith("- $/UNIT"))

    def test_crypto_sends_keeps_the_tt_line_whole(self):
        r = project("usa").run("crypto-sends", TAXJSON_WIDTH=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        tt = [ln for ln in r.stdout.splitlines() if ".tt if payment:" in ln]
        self.assertEqual(len(tt), 1)
        self.assertTrue(tt[0].rstrip().endswith(" 0"), tt[0])
        self.assertTrue(r.stdout.splitlines()[-1].startswith(
            "Decide: taxjson crypto-sends crypto --set ID=self|payment"))


class TestTaxtextUnchanged(unittest.TestCase):
    def test_events_for_one_account_is_pure_taxtext(self):
        a = project("canada").run("events", "all", "margin")
        b = project("canada").run("events", "all", "margin",
                                  TAXJSON_WIDTH=40)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, b.stdout)
        first = a.stdout.splitlines()[0]
        self.assertRegex(first, r"^[A-Z_]+ +\d{4}-\d{2}-\d{2} ")


class TestContractSizeNoteRollUp(unittest.TestCase):
    """lib/markets: the per-root 'export does not state the contract
    size' note is ONE note for a person (the roots listed) and stays a
    line per root when captured for a program (width 0: a stage's .diag
    and the .sum DIAGNOSTICS keep their bytes)."""

    def _emit(self, width, real_stream=True):
        """The notes for three contracts on two roots. `real_stream`:
        the buffer stands in for the process's own stderr (sys.__stderr__
        too); else it is a redirect_stderr buffer (captured)."""
        import contextlib
        import io
        import os
        import sys
        from unittest import mock
        from taxjson.lib import core, markets

        class _Opt:
            multiplier = 0.0
            contract_size_basis = "assumed"

            def __init__(self, sym):
                self.symbol = sym
        err = io.StringIO()
        real = (mock.patch.object(sys, "__stderr__", err) if real_stream
                else contextlib.nullcontext())
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": str(width)}), \
                contextlib.redirect_stderr(err), real, \
                mock.patch("atexit.register"):
            markets.reset_notes()
            for sym in ("QZB250117C00040000.US", "QZA250117C00040000.US",
                        "QZB250117P00030000.US"):
                core.equity_option_size(_Opt(sym))
            markets.flush_notes()
            markets.reset_notes()
        return err.getvalue()

    def test_one_note_for_a_person(self):
        # A few roots: one line (docs/output-style.md, Essentials
        # first); more than three: the headline and the list.
        text = self._emit(100)
        self.assertEqual(text.count("Info:"), 1, text)
        self.assertEqual(text, "Info: QZA, QZB: 100 shares per option "
                               "contract ASSUMED — `MULT <ROOT> N` in "
                               "ticker.map if not\n")
        assert_styled(self, text)

    def test_a_line_per_root_when_captured(self):
        lines = self._emit(0).splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(lines[0].startswith("note: QZB options: the export "
                                            "does not state the contract "
                                            "size"), lines)
        self.assertIn("`MULT QZA N`", lines[1])

    def test_a_line_per_root_into_a_redirected_stderr(self):
        # redirect_stderr(StringIO()) (wash_radar, t1135 silence the
        # engine so) is captured even at a display width: each root's
        # line lands in that buffer at once, nothing waits for exit.
        import contextlib
        import io
        import os
        from unittest import mock
        from taxjson.lib import core, markets

        class _Opt:
            multiplier = 0.0
            contract_size_basis = "assumed"

            def __init__(self, sym):
                self.symbol = sym
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "100"}), \
                mock.patch("atexit.register") as reg:
            markets.reset_notes()
            with contextlib.redirect_stderr(buf):
                for sym in ("QZB250117C00040000.US", "QZA250117C00040000.US"):
                    core.equity_option_size(_Opt(sym))
            self.assertEqual(markets._ROLLED, {})
            reg.assert_not_called()
            markets.reset_notes()
        text = buf.getvalue()
        self.assertEqual(text.count("Info:"), 2, text)
        self.assertIn("QZB options: the export does not state", text)
        self.assertIn("`MULT QZA N`", text)

    def test_real_stream_rolls_up_once_and_registers_once(self):
        import io
        import os
        import sys
        from unittest import mock
        from taxjson.lib import core, markets

        class _Opt:
            multiplier = 0.0
            contract_size_basis = "assumed"

            def __init__(self, sym):
                self.symbol = sym
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "100"}), \
                mock.patch.object(sys, "stderr", err), \
                mock.patch.object(sys, "__stderr__", err), \
                mock.patch.object(markets, "_AT_EXIT", False), \
                mock.patch("atexit.register") as reg:
            for _round in range(2):
                markets.reset_notes()
                before = err.getvalue()
                for sym in ("QZB250117C00040000.US",
                            "QZA250117C00040000.US"):
                    core.equity_option_size(_Opt(sym))
                self.assertEqual(err.getvalue(), before, "held until exit")
                markets.flush_notes()
            markets.reset_notes()
        reg.assert_called_once_with(markets.flush_notes)
        text = err.getvalue()
        self.assertEqual(text.count("Info: QZA, QZB: 100 shares"), 2, text)
        self.assertEqual(text.count("Info:"), 2, text)


if __name__ == "__main__":
    unittest.main()
