"""Pre-release review for v0.21.0 (findings L1, L2, L4 and two small
pre-existing items). The Kraken dust finding (H1, L3) is pinned in
test_fix_kraken_dust.py. Synthetic data only."""
import io
import os
import time
import unittest
from contextlib import redirect_stderr


class TestRewordIsLinear(unittest.TestCase):
    """L1: the run console's display regexes matched a file-name group
    `\\s+(?P<f>.+?)` that could also eat the leading whitespace, so a
    long run of spaces cost quadratic time; a line over 2000 characters
    is now shown as is."""

    def test_long_whitespace_is_fast(self):
        from taxjson.lib.stage_msg import reword
        for n in (1999, 50_000, 500_000):
            line = " " * n + "x"
            t = time.perf_counter()
            self.assertEqual(reword(line), [line])
            self.assertLess(time.perf_counter() - t, 0.5, n)

    def test_long_line_is_not_reworded(self):
        from taxjson.lib.stage_msg import reword
        line = "  " + "a" * 2100 + ".csv: 5 tax objects"
        self.assertEqual(reword(line), [line])

    def test_short_lines_still_reworded(self):
        from taxjson.lib.stage_msg import reword
        self.assertEqual(reword("  kr_l.csv: 251 tax objects"),
                         ["note: kr_l.csv: 251 tax objects"])
        self.assertEqual(
            reword("  a b.csv: 3 TRANSFER row(s) kept aside (custody "
                   "evidence, not tax events — view with `taxjson "
                   "transfers`)"),
            ["note: a b.csv: 3 transfer rows kept aside (not tax events; "
             "`taxjson transfers` lists them)"])


class TestOfflineRatesStep(unittest.TestCase):
    """L2: with TAXJSON_OFFLINE=1 the rates helper reads its cache only;
    the run's step said `Downloading USD → CAD rates` all the same."""

    def _steps(self, offline):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from contextlib import redirect_stdout
        from taxjson.bin import taxjson_run as tr
        out = io.StringIO()
        env = {"TAXJSON_OFFLINE": "1" if offline else "0"}
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, env), \
                mock.patch.object(tr, "run_capture",
                                  return_value=b"2026-01-02 12:00:00 USD "
                                               b"CAD 1.40 boc\n"), \
                redirect_stdout(out):
            tr.stage_currency_rates({"base_currency": "CAD",
                                     "source_currencies": ["USD"]},
                                    Path(td))
        return out.getvalue()

    def test_offline_says_cached(self):
        got = self._steps(offline=True)
        self.assertIn("Loading cached USD → CAD rates", got)
        self.assertNotIn("Downloading", got)

    def test_online_says_downloading(self):
        self.assertIn("Downloading USD → CAD rates",
                      self._steps(offline=False))



class TestSanityConsoleUnchecked(unittest.TestCase):
    """L4: the run's holdings check said "positions match the broker's
    holdings files" while some accounts with open positions had no
    holdings file at all (their positions were never compared)."""

    def _shown(self, doc):
        import json
        from unittest import mock
        from contextlib import redirect_stdout
        from taxjson.bin import taxjson_run as tr

        def fake(_args):
            print(json.dumps(doc))
            raise SystemExit(0)
        out = io.StringIO()
        with mock.patch.object(tr, "cmd_sanity", fake), \
                mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}), \
                redirect_stdout(out):
            tr._sanity_console(__import__("pathlib").Path("."))
        return " ".join(out.getvalue().split())

    def test_unchecked_accounts_named(self):
        got = self._shown({"discrepancies": [], "notes": [],
                           "uncovered_accounts": ["crypto", "tfsa"]})
        self.assertIn("positions match the broker's holdings files "
                      "(checked accounts only; 2 unchecked: crypto, tfsa)",
                      got)

    def test_all_checked_is_plain(self):
        got = self._shown({"discrepancies": [], "notes": [],
                           "uncovered_accounts": []})
        self.assertIn("positions match the broker's holdings files", got)
        self.assertNotIn("unchecked", got)


class TestTomlStrEscapesControls(unittest.TestCase):
    """Pre-existing (review info): the holdings TOML writer escaped only
    newline, carriage return and tab, so a symbol holding another control
    character (an ESC from broker data) wrote a file TOML refuses."""

    def test_round_trip(self):
        from taxjson.lib.tomlcompat import tomllib
        from taxjson.bin.taxjson_export import _toml_str
        for raw in ("AB\x1bC", "X\x00Y\x7fZ", 'q"\\b\n\r\t\x08\x0c',
                    "".join(chr(c) for c in range(0x20)) + "\x7f"):
            with self.subTest(raw=raw):
                text = f"s = {_toml_str(raw)}\n"
                self.assertEqual(tomllib.loads(text)["s"], raw)
                self.assertFalse(any(ord(c) < 0x20 or ord(c) == 0x7f
                                     for c in text[:-1]), text)


class TestSkipSummaryShownName(unittest.TestCase):
    """Pre-existing (review info): parsers passed `path.name` to
    emit_skip_summary, so a file name with an account-id-shaped token or
    a control character reached the captured .sum/.diag unmasked."""

    def test_parsers_pass_shown_name(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / "src" / "taxjson"
        hits = [str(p.relative_to(src)) for p in src.rglob("*.py")
                if "emit_skip_summary(path.name)" in p.read_text()]
        self.assertEqual(hits, [])

    def test_kraken_summary_masks_the_name(self):
        import tempfile
        from pathlib import Path
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        h = ("txid,refid,time,type,subtype,aclass,subclass,asset,wallet,"
             "amount,fee,balance\n")
        row = ("T1,R1,2026-01-05 12:00:00,earn,allocation,currency,crypto,"
               "SOL,spot / main,-1,0,0\n")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "kr_ledgers_55500001\x1b.csv"  # pii-ok
            p.write_text(h + row)
            buf = io.StringIO()
            from unittest import mock
            with redirect_stderr(buf), mock.patch.dict(
                    os.environ, {"TAXJSON_LOCAL_TZ": "America/Toronto"}):
                KrakenBrokerage().parse_file(p)
        err = buf.getvalue()
        self.assertIn("recognized non-event", err)
        self.assertNotIn("55500001", err)  # pii-ok
        self.assertNotIn("\x1b", err.replace("\\x1b", ""))

if __name__ == "__main__":
    unittest.main()
