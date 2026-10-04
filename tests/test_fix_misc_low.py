"""Low-round fixes in the misc area (fees report, merge2, cross-listing
lint, watchlist export, generate-parser, crypto money parsing,
the PII gate and dev scripts). Synthetic data only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PY = sys.executable


def _run(mod, *args, env=None, input=None):
    e = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")
    if env:
        e.update(env)
    return subprocess.run([PY, "-m", mod, *args], capture_output=True,
                          text=True, env=e, input=input,
                          stdin=None if input is not None else subprocess.DEVNULL)


def _write_json(path, doc):
    Path(path).write_text(json.dumps(doc), encoding="utf-8")


def _trade(i, date, fee, account="margin", symbol="XYZ.US", currency="USD"):
    return {"id": f"t{i}", "action": "BUYSELL", "date": date,
            "date_settle": date, "symbol": symbol, "quantity": 10,
            "price": 10.0, "gross_amount": 100.0, "net_amount": 100.0 + fee,
            "fee": fee, "currency": currency, "account": account}


class TestFeesSum(unittest.TestCase):
    """taxjson-fees-sum (taxjson_fees.py)."""

    def _files(self, d):
        wb = Path(d) / "margin_webull.json"
        _write_json(wb, {"metadata": {"source_brokerage": "webull"},
                         "transactions": [_trade(1, "2025-03-03", 2.0)]})
        tt = Path(d) / "margin_wb_manual.json"
        _write_json(tt, {"metadata": {"source_brokerage": "manual (.tt)"},
                         "transactions": [_trade(2, "2026-06-18", 2.0)]})
        return wb, tt

    def test_manual_tt_fees_do_not_make_a_broker_fee_free(self):
        # R1-101: Webull's 2026 trades live only in a .tt; the footer
        # said 'Brokers with NO fees in this period: webull'.
        with tempfile.TemporaryDirectory() as d:
            wb, tt = self._files(d)
            r = _run("taxjson.bin.taxjson_fees", str(wb), str(tt),
                     "--year", "2026")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Brokers with NO fees", r.stdout)
            self.assertIn("not attributed to a broker", r.stdout)
            self.assertIn("webull", r.stdout)
            j = _run("taxjson.bin.taxjson_fees", str(wb), str(tt),
                     "--year", "2026", "--json")
            meta = json.loads(j.stdout)["meta"]
            self.assertTrue(meta["manual_tt_fees_unattributed"])
            self.assertEqual(meta["zero_fee_brokers"], ["webull"])

    def test_zero_fee_claim_kept_without_manual_rows(self):
        with tempfile.TemporaryDirectory() as d:
            wb, _ = self._files(d)
            other = Path(d) / "margin_ib.json"
            _write_json(other, {"metadata": {"source_brokerage": "ib"},
                                "transactions": [_trade(3, "2026-02-02", 1.0)]})
            r = _run("taxjson.bin.taxjson_fees", str(wb), str(other),
                     "--year", "2026")
            self.assertIn("Brokers with NO fees in this period: webull",
                          r.stdout)

    def test_year_help_names_the_trade_date(self):
        # R1-154 / R1-289: the help said '(settlement) date'.
        r = _run("taxjson.bin.taxjson_fees", "--help")
        self.assertNotIn("(settlement) date", r.stdout)
        self.assertIn("TRADE date", r.stdout)

    def test_since_must_be_an_iso_date(self):
        # S031-06: '2025-6-1' compared as a string dropped every fee.
        with tempfile.TemporaryDirectory() as d:
            wb, _ = self._files(d)
            for bad in ("2025-6-1", "2025/06/01", "June-2025", "banana"):
                r = _run("taxjson.bin.taxjson_fees", str(wb), "--since", bad)
                self.assertEqual(r.returncode, 2, bad)
                self.assertIn("YYYY-MM-DD", r.stderr)
            ok = _run("taxjson.bin.taxjson_fees", str(wb),
                      "--since", "2025-01-01")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("webull", ok.stdout)

    def test_by_account_without_account_is_not_None(self):
        # S031-03
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "q.json"
            row = _trade(1, "2025-04-01", 4.95, currency="CAD")
            del row["account"]
            _write_json(p, {"metadata": {"source_brokerage": "questrade"},
                            "transactions": [row]})
            r = _run("taxjson.bin.taxjson_fees", str(p), "--by-account")
            self.assertNotIn("None", r.stdout)
            self.assertIn("questrade/?", r.stdout)
            j = _run("taxjson.bin.taxjson_fees", str(p), "--by-account",
                     "--json")
            self.assertEqual(list(json.loads(j.stdout)["brokerages"]),
                             ["questrade/?"])


class TestMerge2DefaultRateWarning(unittest.TestCase):
    def test_warning_names_the_rate_actually_used(self):
        # R1-155: the warning printed '--default-rate (None)' while the
        # rows were converted at 1.35.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tx.json"
            _write_json(p, {"transactions": [_trade(1, "2025-03-03", 1.0)]})
            r = _run("taxjson.bin.taxjson_merge2", str(p), "--to", "CAD")
            self.assertEqual(r.returncode, 0, r.stderr)
            # The built-in fallback is per direction (A2-0148).
            self.assertIn("--default-rate (1.35 for USD->CAD", r.stderr)
            self.assertNotIn("(None)", r.stderr)


def _row(sym, qty, date, action="BUYSELL", **kw):
    r = {"action": action, "symbol": sym, "quantity": qty, "date": date,
         "time": "10:00:00", "currency": "CAD" if sym.endswith(".TO") else "USD",
         "description": kw.pop("description", sym)}
    r.update(kw)
    return r


class TestLintCrosslistings(unittest.TestCase):
    MOD = "taxjson.bin.taxjson_lint_crosslistings"

    def _lint(self, d, rows, map_text=None, *extra):
        t = Path(d) / "t.json"
        _write_json(t, {"transactions": rows})
        args = ["--taxable", str(t), "--strict", *extra]
        if map_text is not None:
            m = Path(d) / "ticker.map"
            m.write_text(map_text, encoding="utf-8")
            args += ["--map", str(m)]
        return _run(self.MOD, *args)

    def test_distinct_and_lowercase_keywords_honoured(self):
        # R1-144: DISTINCT pairs and lower-case `tobase` were ignored.
        rows = [_row("ZZQ.TO", 10, "2025-01-02"), _row("ZZQ.US", 10, "2025-01-03"),
                _row("YYQ.TO", 10, "2025-01-02"), _row("YYQ.US", 10, "2025-01-03")]
        with tempfile.TemporaryDirectory() as d:
            r = self._lint(d, rows, "DISTINCT ZZQ.US ZZQ.TO\ntobase YYQ.US YYQ.TO\n")
            self.assertIn("[OK] ZZQ", r.stdout)
            self.assertIn("DISTINCT", r.stdout)
            self.assertIn("[WARN ‼] YYQ", r.stdout)
            self.assertNotIn("[REVIEW", r.stdout)

    def test_unreadable_map_fails(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "t.json"
            _write_json(t, {"transactions": [_row("ZZQ.TO", 1, "2025-01-02")]})
            r = _run(self.MOD, "--taxable", str(t), "--map",
                     str(Path(d) / "missing.map"))
            # Exit 2: an unreadable input, not a lint finding (A2-1435).
            self.assertEqual(r.returncode, 2)
            self.assertIn("cannot read map", r.stderr)
            self.assertNotIn("Clean", r.stdout)

    def test_split_position_nets_to_zero(self):
        # S035-02: buy 100, 2:1 split, sell 200 read as taxable=-100.
        rows = [_row("ZZ.TO", 100, "2025-01-02"),
                _row("ZZ.TO", 2, "2025-02-03", action="SPLIT"),
                _row("ZZ.TO", -200, "2025-03-03"),
                _row("ZZ.US", 10, "2025-01-02"), _row("ZZ.US", -10, "2025-01-05")]
        with tempfile.TemporaryDirectory() as d:
            r = self._lint(d, rows)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(".TO  taxable=+0", r.stdout)
            self.assertNotIn("‼", r.stdout.split("----")[0])

    def test_options_only_listing_is_surfaced(self):
        # S035-03: AAQ.TO shares + AAQ.US calls read as '(Clean.)'.
        rows = [_row("AAQ.TO", 100, "2025-01-02"),
                _row("AAQ250620C00010000.US", 2, "2025-01-03")]
        with tempfile.TemporaryDirectory() as d:
            r = self._lint(d, rows)
            self.assertNotIn("Clean", r.stdout)
            self.assertIn("[REVIEW ‼] AAQ", r.stdout)
            self.assertIn("options: taxable=+2", r.stdout)
            self.assertEqual(r.returncode, 1)


class TestWatchlistPlatformSuffix(unittest.TestCase):
    def test_canadian_venues_are_canadian(self):
        # S078-03: .V/.CN/.NE were exported as US tickers.
        sys.path.insert(0, str(SRC))
        from taxjson.lib.ticker_map import format_ticker_for_platform as f
        cases = {
            "QZT.TO": ("QZT:CA", "QZT:CA"),
            "QZV.V": ("QZV:CA", "QZV:CA"),
            "QZC.CN": ("QZC:CA", "QZC:CA"),
            "QZN.NE": ("QZN:CA", "QZN:CA"),
            "QZU.US": ("QZU", "QZU:US"),
            "QZA.AX": ("QZA.AX", "QZA.AX"),
        }
        for sym, (sa, fg) in cases.items():
            self.assertEqual(f(sym, "seekingalpha"), sa, sym)
            self.assertEqual(f(sym, "fastgraph"), fg, sym)


_IB_SAMPLE = """Statement,Header,Field Name,Field Value
Account Information,Header,Field Name,Field Value
Account Information,Data,Name,Pat Contributor
Account Information,Data,Account,{acct}
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,Proceeds,Comm/Fee
Trades,Data,Order,Stocks,USD,MSFT,"2024-02-12, 09:35:14",50,400.00,-20000.00,-1.00
""".format(acct="U" + "5550001")   # synthetic, assembled at run time


class TestGenerateParserPrivacy(unittest.TestCase):
    """R1-342 / S033-18: taxjson-generate-parser sends the sample to an
    LLM provider. The provider calls are stubbed: no network."""

    def _main(self, d, sample, *extra):
        import contextlib
        import io
        from unittest import mock
        sys.path.insert(0, str(SRC))
        import taxjson.bin.taxjson_generate_parser as gp
        csv = Path(d) / "sample.csv"
        csv.write_text(sample, encoding="utf-8")
        sent = []

        def fake(system_text, user_text, model_id):
            sent.append(user_text)
            return "x = 1\n"
        err = io.StringIO()
        argv = ["taxjson-generate-parser", str(csv), "-o",
                str(Path(d) / "out.py"), *extra]
        with mock.patch.object(gp, "_call_claude", fake), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch.dict(os.environ, {"TAXJSON_PII_DENYLIST": ""}), \
                contextlib.redirect_stderr(err):
            os.environ.pop("TAXJSON_PII_DENYLIST", None)
            with mock.patch("pathlib.Path.home", return_value=Path(d)):
                try:
                    gp.main()
                    rc = 0
                except SystemExit as e:
                    rc = e.code
        return rc, sent, err.getvalue()

    def test_identity_in_sample_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            rc, sent, err = self._main(d, _IB_SAMPLE)
            self.assertEqual(rc, 1)
            self.assertEqual(sent, [])
            self.assertIn("personal data", err)
            self.assertIn("taxjson redact", err)
            self.assertNotIn("U5550001", err)  # pii-ok

    def test_override_sends_with_warning(self):
        with tempfile.TemporaryDirectory() as d:
            rc, sent, err = self._main(d, _IB_SAMPLE, "--allow-unredacted")
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(sent), 1)
            self.assertIn("warning: sending a sample", err)

    def test_redacted_sample_passes(self):
        sys.path.insert(0, str(SRC))
        from taxjson.bin.taxjson_redact import redact_text
        clean, _ = redact_text(_IB_SAMPLE)
        with tempfile.TemporaryDirectory() as d:
            rc, sent, err = self._main(d, clean)
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(sent), 1)
            self.assertNotIn("Pat Contributor", sent[0])

    def test_non_positive_sample_lines_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            rc, sent, err = self._main(d, "a,b\n1,2\n", "--sample-lines", "0")
            self.assertEqual(rc, 2)
            self.assertIn("--sample-lines must be at least 1", err)

    def test_prompt_steers_required_cells_to_parse_strict_number(self):
        sys.path.insert(0, str(SRC))
        import taxjson.bin.taxjson_generate_parser as gp
        text = gp._SYSTEM_TEMPLATE
        self.assertIn("parse_strict_number", text)
        self.assertIn("ONLY for optional cells", text)


class TestStrictMoney(unittest.TestCase):
    def test_doubled_signs_and_lenient_float_syntax_refused(self):
        # R1-114: '--5' read as +5, '1_000' as 1000 (float() syntax).
        sys.path.insert(0, str(SRC))
        from taxjson.lib.brokerages._crypto_common import strict_money as m
        for bad in ("--5", "(-5)", "-$-5", "$--5", "+-5", "1_000",
                    "1_000.5", "1e", "\u0661\u0662\u0663", "nan", "0x10"):
            with self.assertRaises(ValueError, msg=bad):
                m(bad)
        for good, v in (("CA$-4.00", -4.0), ("(12.00)", -12.0),
                        ("-$12.00", -12.0), ("$-4.00", -4.0), ("+3", 3.0),
                        ("1,234.56", 1234.56), (".5", 0.5),
                        ("0.00000100", 1e-6), ("", 0.0), ("US$4", 4.0),
                        ("1e-8", 1e-8)):
            self.assertAlmostEqual(m(good), v, msg=good)


class TestMutationScripts(unittest.TestCase):
    """S025-00 / S025-04. The harness itself is never run here: only its
    restore handlers are exercised, on a scratch file."""

    def _harness(self, d, tail):
        target = Path(d) / "engine.py"
        code = (
            "import importlib.util, os, signal, sys, time\n"
            f"spec = importlib.util.spec_from_file_location('ma', "
            f"{str(REPO / 'scripts' / 'mutation_audit.py')!r})\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "m.install_restore_handlers()\n"
            f"p = {str(target)!r}\n"
            "m._ORIGINALS[p] = 'ORIGINAL\\n'\n"
            "open(p, 'w').write('MUTANT\\n')\n" + tail)
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONPATH=str(SRC)))
        return r, target.read_text()

    def test_sigterm_and_sighup_restore_the_source(self):
        import signal
        with tempfile.TemporaryDirectory() as d:
            for sig in (signal.SIGTERM, signal.SIGHUP):
                r, text = self._harness(
                    d, f"os.kill(os.getpid(), {int(sig)})\ntime.sleep(5)\n")
                self.assertEqual(text, "ORIGINAL\n", r.stderr)
                self.assertEqual(r.returncode, 128 + int(sig), r.stderr)
            r, text = self._harness(d, "sys.exit(3)\n")      # atexit
            self.assertEqual(text, "ORIGINAL\n", r.stderr)

    def test_triage_help_and_missing_report(self):
        script = str(REPO / "scripts" / "mutation_triage.py")
        r = subprocess.run([PY, script, "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage", r.stdout)
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([PY, script, str(Path(d) / "none.txt")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("Traceback", r.stderr)
            rep = Path(d) / "rep.txt"
            rep.write_text("SURVIVED: 1\n  core.py:1 [cmp]\n")
            r = subprocess.run([PY, script, str(rep)], capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("== CANDIDATE", r.stdout)


_PY_STUB = """#!/usr/bin/env bash
# python stand-in for the installer test: no venv, no pip, no network.
case "$1" in
  -c) exit 0 ;;
  --version) echo "Python 3.12.0" ;;
  -m)
    case "$2" in
      venv) mkdir -p "$3/bin"; cp "$0" "$3/bin/python"
            printf '#!/bin/sh\\necho "taxjson 0.0.0"\\n' > "$3/bin/taxjson"
            chmod +x "$3/bin/taxjson"
            cp "$3/bin/taxjson" "$3/bin/tjs" ;;
    esac ;;
esac
exit 0
"""


class TestInstallerTagSelection(unittest.TestCase):
    """S023-01: users run install.sh straight from main, so its release
    channel's tag choice is pinned here (a stub python; local git only)."""

    def _git(self, cwd, *args):
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Sam",
                   GIT_AUTHOR_EMAIL="sam@example.com",
                   GIT_COMMITTER_NAME="Sam",
                   GIT_COMMITTER_EMAIL="sam@example.com")
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_release_channel_takes_the_newest_plain_vXYZ_tag(self):
        import shutil
        if not shutil.which("git"):
            self.skipTest("git required")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "src"
            src.mkdir()
            self._git(src, "init", "-q", "-b", "main")
            for i, tag in enumerate(("v0.2.0", "v0.10.0", "v0.11.0-rc1",
                                     "v0.12.0.1", "v1.0", "release-9")):
                (src / "f.txt").write_text(f"{i}\n")
                self._git(src, "add", "f.txt")
                self._git(src, "commit", "-q", "-m", tag)
                self._git(src, "tag", "-a", tag, "-m", tag)
            stub = d / "stub"
            stub.mkdir()
            for name in ("python3", "python3.9", "python3.10", "python3.11",
                         "python3.12", "python3.13"):
                (stub / name).write_text(_PY_STUB)
                (stub / name).chmod(0o755)
            env = dict(os.environ, HOME=str(d), TAXJSON_REPO=str(src),
                       TAXJSON_DIR=str(d / "inst"), TAXJSON_BIN=str(d / "bin"),
                       PATH=f"{stub}:{os.environ['PATH']}",
                       GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

            def install():
                r = subprocess.run(["bash", str(REPO / "install.sh")], env=env,
                                   capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                return self._git(d / "inst", "describe", "--tags",
                                 "--exact-match")
            self.assertEqual(install(), "v0.10.0",
                             "an rc / four-part / two-part tag never ships")
            self.assertTrue((d / "bin" / "taxjson").is_symlink())
            # The short name `tjs` is linked beside it (owner request).
            self.assertTrue((d / "bin" / "tjs").is_symlink())
            (src / "f.txt").write_text("next\n")
            self._git(src, "commit", "-q", "-am", "next")
            self._git(src, "tag", "-a", "v0.10.1", "-m", "v0.10.1")
            self.assertEqual(install(), "v0.10.1", "re-running upgrades")


if __name__ == "__main__":
    unittest.main()
