"""Low-round engine-area CLI hardening (fixl/engine-cli).

Synthetic data only; account labels are fake. Each tool is run the way
a user runs it (a subprocess of the module), so the exit code and the
stderr shape are what is pinned.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _run(module, *args, stdin=None):
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")
    return subprocess.run(
        [sys.executable, "-m", f"taxjson.bin.{module}", *map(str, args)],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        input=stdin if stdin is not None else "", timeout=180)


def _row(action, date, symbol, qty, net, *, settle=None, time="10:00:00",
         currency="CAD", account="margin", **kw):
    r = {"action": action, "date": date, "time": time,
         "date_settle": settle or date, "symbol": symbol,
         "quantity": qty, "price": abs(net / qty) if qty else 0.0,
         "net_amount": net, "currency": currency, "account": account}
    r.update(kw)
    return r


def _book(path, rows):
    path.write_text(json.dumps({"transactions": rows}))
    return path


def _one_line_error(test, proc, prog, rc=2):
    test.assertEqual(proc.returncode, rc, proc.stderr)
    test.assertNotIn("Traceback", proc.stderr)
    test.assertIn(f"{prog}: error:", proc.stderr)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()


# A plain loss/gain book: two XYZ.TO dispositions.
def _plain_rows():
    return [_row("BUYSELL", "2025-01-06", "XYZ.TO", 100, 1000.0),
            _row("BUYSELL", "2025-03-03", "XYZ.TO", -50, 600.0),
            _row("BUYSELL", "2025-04-01", "XYZ.TO", -50, 400.0)]


class TestExplainSymbolCaseInsensitive(_Tmp):
    """S029-19: explain --symbol was a case-sensitive prefix match;
    audit's filter is case-insensitive."""

    def test_lower_case_symbol_matches(self):
        b = _book(self.tmp / "b.json", _plain_rows())
        up = _run("taxjson_explain", "--country", "canada", "--list",
                  "--symbol", "XYZ", b)
        low = _run("taxjson_explain", "--country", "canada", "--list",
                   "--symbol", "xyz", b)
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertEqual(low.returncode, 0, low.stderr)
        self.assertEqual(len(up.stdout.strip().splitlines()), 2, up.stdout)
        self.assertEqual(low.stdout, up.stdout)


class TestExplainHelpWashSales(unittest.TestCase):
    """S029-24: the --help example said --wash-sales 'includes' the
    wash traces; the flag filters to wash-sale gains only."""

    def test_epilog_says_filter(self):
        p = _run("taxjson_explain", "--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("Include the wash-sale window traces too", p.stdout)
        self.assertIn("Only the wash-sale gains", p.stdout)


def _mismatch_rows():
    # Same symbol bought in CAD and sold in USD: an engine ValueError.
    return [_row("BUYSELL", "2025-01-06", "XYZ.TO", 100, 1000.0),
            _row("BUYSELL", "2025-03-03", "XYZ.TO", -100, 900.0,
                 currency="USD")]


def _straddle_rows():
    # A RENAME-split between trade and settlement of a sale: the Canada
    # engine refuses it (SplitStraddlesSettlementError).
    return [_row("BUYSELL", "2024-01-05", "QZ.TO", 100, 1000.0),
            _row("BUYSELL", "2024-06-10", "QZ.TO", -100, 900.0,
                 settle="2024-06-12"),
            _row("SPLIT", "2024-06-11", "QZ.TO", 2.0, 0.0,
                 time="12:00:00", symbol_new="QY.TO")]


class TestEngineErrorsAreOneLine(_Tmp):
    """S029-20 / S071-06: explain, audit and carryover printed a full
    traceback for the engine and loader refusals taxjson-gains reports
    in one line (exit 2)."""

    def _all(self, rows):
        b = _book(self.tmp / "b.json", rows)
        return {
            "taxjson-gains": _run("taxjson_gains", "--country", "canada",
                                  "--taxable", b),
            "taxjson-explain": _run("taxjson_explain", "--country",
                                    "canada", b),
            "taxjson-audit": _run("taxjson_audit", "--country", "canada",
                                  "--base", b),
            "taxjson-carryover": _run("taxjson_carryover", "--country",
                                      "canada", b),
        }

    def test_currency_mismatch(self):
        for prog, p in self._all(_mismatch_rows()).items():
            with self.subTest(prog=prog):
                _one_line_error(self, p, prog)

    def test_split_straddles_settlement(self):
        for prog, p in self._all(_straddle_rows()).items():
            with self.subTest(prog=prog):
                _one_line_error(self, p, prog)
                self.assertIn("QZ.TO", p.stderr)


class TestBadPhantomsJsonIsOneLine(_Tmp):
    """S076-01: a hand-edited missing_history.json with a trailing comma gave
    audit and carryover a raw JSONDecodeError traceback."""

    def test_trailing_comma(self):
        b = _book(self.tmp / "b.json", _plain_rows())
        ph = self.tmp / "missing_history.json"
        ph.write_text('[{"symbol": "XYZ.TO", "account": "margin"},]')
        runs = {
            "taxjson-explain": _run("taxjson_explain", "--country",
                                    "canada", "--incomplete-history", ph,
                                    b),
            "taxjson-audit": _run("taxjson_audit", "--country", "canada",
                                  "--base", b, "--incomplete-history", ph),
            "taxjson-carryover": _run("taxjson_carryover", "--country",
                                      "canada", "--incomplete-history",
                                      ph, b),
            "taxjson-gains": _run("taxjson_gains", "--country", "canada",
                                  "--taxable", "--incomplete-history", ph,
                                  b),
        }
        for prog, p in runs.items():
            with self.subTest(prog=prog):
                _one_line_error(self, p, prog)
                self.assertIn("missing_history.json", p.stderr)
                self.assertIn("line 1", p.stderr)


class TestYearFlagValidated(_Tmp):
    """S033-16: --year 0 meant 'all history' and a 2-digit year silently
    matched nothing (exit 0)."""

    def test_gains_refuses_implausible_years(self):
        b = _book(self.tmp / "b.json", _plain_rows())
        for y in ("0", "25", "20255", "-2025", "abc"):
            with self.subTest(year=y):
                p = _run("taxjson_gains", "--country", "canada",
                         f"--year={y}", b)
                self.assertEqual(p.returncode, 2, p.stderr)
                self.assertNotIn("Traceback", p.stderr)
                self.assertIn("--year", p.stderr)
        ok = _run("taxjson_gains", "--country", "canada", "--year=2025", b)
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_form_export_and_t1135_refuse_zero(self):
        g = self.tmp / "g.json"
        g.write_text(json.dumps({"transactions": [], "summary": {}}))
        for mod, extra in (("taxjson_form_export",
                            ["--form", "schedule3"]),
                           ("taxjson_t1135", [])):
            for y in ("0", "25"):
                with self.subTest(mod=mod, year=y):
                    p = _run(mod, *extra, f"--year={y}", g)
                    self.assertEqual(p.returncode, 2, p.stderr)
                    self.assertIn("--year", p.stderr)
                    self.assertNotIn("Traceback", p.stderr)


class TestUnreadableInputIsOneLine(_Tmp):
    """S070-23 / S079-10: a missing path, a directory or a non-UTF-8 file
    gave a FileNotFoundError / IsADirectoryError / UnicodeDecodeError
    traceback (exit 1) in a dozen tools, while their siblings print one
    line and exit 2."""

    def _bads(self):
        d = self.tmp / "adir"
        d.mkdir()
        nu = self.tmp / "latin1.json"
        nu.write_bytes(b'{"transactions": [{"description": "caf\xe9"}]}')
        return {"missing": self.tmp / "nope" / "x.json", "directory": d,
                "non-utf8": nu}

    def test_tools(self):
        good = _book(self.tmp / "good.json", _plain_rows())
        mp = self.tmp / "ticker.map"
        mp.write_text("")
        tools = {
            "taxjson-gains": lambda p: ("taxjson_gains", "--country",
                                        "canada", p),
            "taxjson-gains --sheltered": lambda p: (
                "taxjson_gains", "--country", "canada", "--sheltered", p,
                good),
            "taxjson-explain": lambda p: ("taxjson_explain", "--country",
                                          "canada", p),
            "taxjson-audit": lambda p: ("taxjson_audit", "--country",
                                        "canada", "--base", p),
            "taxjson-convert-currency": lambda p: (
                "taxjson_convert_currency", "--to", "CAD",
                "--default-rate", "1.35", p),
            "taxjson-fill-crypto": lambda p: ("fill_crypto_prices", p),
            "taxjson-merge2 --map": lambda p: ("taxjson_merge2", "--map",
                                               p, good),
            "taxjson-safe-to-sell": lambda p: (
                "taxjson_safe_to_sell", "--taxable", p, "--country",
                "canada"),
            "taxjson-sort": lambda p: ("taxjson_sort", p),
            "taxjson-sum-gains": lambda p: ("taxjson_sum_gains", p),
            "taxjson-sum-income": lambda p: ("taxjson_sum_income", p),
            "taxjson-ticker-map": lambda p: ("taxjson_ticker_map", p),
            "taxjson-ticker-map map": lambda p: ("taxjson_ticker_map",
                                                 good, p),
            "taxjson-wash-radar": lambda p: ("taxjson_wash_radar",
                                             "--taxable", p, "--country",
                                             "canada"),
        }
        for kind, bad in self._bads().items():
            for name, argv in tools.items():
                with self.subTest(tool=name, kind=kind):
                    mod, *rest = argv(bad)
                    p = _run(mod, *rest)
                    self.assertEqual(p.returncode, 2,
                                     f"{name}: {p.stderr[-800:]}")
                    self.assertNotIn("Traceback", p.stderr, name)
                    self.assertIn("error:", p.stderr, name)
                    self.assertTrue(
                        any(w in p.stderr for w in (
                            "no such file", "is a directory",
                            "not UTF-8", "cannot read")), p.stderr)

    def test_console_entry_guards_every_tool(self):
        # The console-script trampoline (taxjson.bin._entry) turns the
        # same errors into one line for any tool.
        env = dict(os.environ, PYTHONPATH=str(SRC))
        p = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv=['taxjson-sort', sys.argv[1]]; "
             "from taxjson.bin._entry import taxjson_sort; "
             "sys.exit(taxjson_sort())", str(self.tmp / "nope.json")],
            capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("no such file", p.stderr)


class TestDefaultRateValidated(_Tmp):
    """S028-15 / S028-17: --default-rate took -1.35, 0, nan, inf and
    '1_35' (float() syntax) with exit 0 — a sign slip inverted every
    converted amount; the rates file refuses the same values."""

    BAD = ("-1.35", "0", "nan", "inf", "1_35")

    def test_refused_at_parse_in_every_tool(self):
        b = _book(self.tmp / "b.json", [
            _row("BUYSELL", "2025-01-06", "ABC.US", 10, 1000.0,
                 currency="USD", fee=-1.0)])
        cmds = {
            "convert-currency": lambda r: (
                "taxjson_convert_currency", "--to", "CAD",
                "--default-rate", r, b),
            "merge2": lambda r: ("taxjson_merge2", "--to", "CAD",
                                 "--default-rate", r, b),
            "fees-sum": lambda r: ("taxjson_fees", "--default-rate", r,
                                   b),
            "audit": lambda r: ("taxjson_audit", "--country", "canada",
                                "--base", b, "--default-rate", r),
        }
        for name, argv in cmds.items():
            for r in self.BAD:
                with self.subTest(tool=name, rate=r):
                    p = _run(*argv(r))
                    self.assertEqual(p.returncode, 2, p.stderr[-500:])
                    self.assertIn("--default-rate", p.stderr)
                    self.assertNotIn("Traceback", p.stderr)
        ok = _run(*cmds["convert-currency"]("1.35"))
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_rates_file_underscore_is_malformed(self):
        from taxjson.bin.taxjson_convert_currency import load_exchange_rates
        rf = self.tmp / "rates.txt"
        rf.write_text("2025-03-03 12:00:00 USD CAD 1_35\n"
                      "2025-03-04 12:00:00 USD CAD 1.36\n")
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            h = load_exchange_rates(rf, "CAD")
        self.assertNotIn("2025-03-03", h.get("USD", {}))
        self.assertEqual(str(h["USD"]["2025-03-04"]), "1.36")
        self.assertIn("malformed", err.getvalue())


if __name__ == "__main__":
    unittest.main()
