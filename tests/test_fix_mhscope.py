"""`taxjson run`'s console scoped to the tax year (owner feedback from a
new-user run).

* Positions that go short with no purchase in the files are listed one
  by one only when they bear on the project's year — a row of the year
  draws on the missing purchase or touches the position at all, or
  (Canada) another taxable account of its ACB pool trades the symbol
  that year: the test find-missing-history sorts its sections by
  (lib/missing_history.classify_year_shorts). The rest are ONE `Info:`
  line naming `find-missing-history --write-missing-history
  --outside-year`, which records them; the .diag / .sum keep every
  line.
* An engine message is shown once per run whichever pass says it.
* Whole lines in order through a pipe; the checks before the first
  stage under a step; a failed stage's headline repeated in the run's
  last line; the Questrade codes note one code per line.

Every fixture is synthetic: invented tickers, fake account ids (each
line marked pii-ok), small amounts."""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent

_TOML = {
    "canada": ('[settings]\nbase_currency = "CAD"\ncountry = "canada"\n'
               'source_currencies = []\nyear = 2025\n'
               'option_grant_timing_since = 2025\n'),
    "usa": ('[settings]\nbase_currency = "USD"\ncountry = "usa"\n'
            'source_currencies = []\nyear = 2025\n'),
}
_CUR = {"canada": "CAD", "usa": "USD"}


def _ib(acct, rows, cur):
    head = ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers (Synthetic)\n"
            "Statement,Data,Title,Activity Statement\n"
            'Statement,Data,Period,"January 1, 2024 - December 31, 2025"\n'
            "Account Information,Header,Field Name,Field Value\n"
            f"Account Information,Data,Account,{acct}\n"
            "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
            "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
            "Realized P/L,MTM P/L,Code\n")
    body = "".join(
        f'Trades,Data,Order,Stocks,{cur},{s},"{d}, 10:00:00",{q},{p},{p},'
        f'{-q * p:.2f},-1.00,{b},0,0,{c}\n' for s, d, q, p, b, c in rows)
    return head + body


# margin: QPAS / QOPN went short in 2024 (a sale coded closing, nothing
# held) and never trade again; QABC went short in 2024 and is bought in
# 2025 (the cover draws on it); QXYZ is sold in 2025 with nothing held;
# QOK is a clean round trip. rrsp: QRGL short in 2024 only, QRGT in 2025.
MARGIN = [("QPAS", "2024-03-01", -100, 2, -150, "C"),
          ("QOPN", "2024-07-02", -30, 5, -120, "C"),
          ("QABC", "2024-06-03", -20, 4, -60, "C"),
          ("QABC", "2025-02-03", 50, 4.5, 226, "O"),
          ("QXYZ", "2025-05-01", -10, 50, -400, "C"),
          ("QOK", "2025-03-03", 10, 10, 101, "O"),
          ("QOK", "2025-04-01", -10, 12, -101, "C")]
RRSP = [("QRGL", "2024-04-01", -5, 30, -100, "C"),
        ("QRGT", "2025-04-01", -5, 30, -100, "C")]


def _env(**extra):
    e = dict(os.environ)
    e.pop("TAXJSON_WIDTH", None)            # piped: a person's width 100
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src") + os.pathsep + e.get(
        "PYTHONPATH", "")
    e.update(extra)
    return e


def _make(country, *, extra_accounts=None):
    tmp = tempfile.mkdtemp(prefix="taxjson_mhscope_")
    root = Path(tmp) / country
    cur = _CUR[country]
    accts = {"margin": ("taxable", "U5550001", MARGIN),  # pii-ok
             "rrsp": ("sheltered", "U5550002", RRSP)}  # pii-ok
    accts.update(extra_accounts or {})
    toml = _TOML[country]
    for name, (kind, acct, rows) in accts.items():
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / f"ib_{name}.csv").write_text(
            _ib(acct, rows, cur))
        toml += f'\n[accounts.{name}]\ntype = "{kind}"\n'
    (root / "taxjson.toml").write_text(toml)
    return tmp, root


def _tj(root, *args, merged=False, **env):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=root, env=_env(**env), timeout=900, text=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merged else subprocess.PIPE)


def _sfx(country):
    return ".TO" if country == "canada" else ".US"


class TestShortsScopedToTheYear(unittest.TestCase):
    """The owner's case in both countries: the shorts of the year are
    listed, the others are one line; the captured text keeps them all;
    `--outside-year` records the others and changes no number of the
    year."""

    def test_both_countries(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                self._check(country)

    def _check(self, country):
        tmp, root = _make(country)
        self.addCleanup(shutil.rmtree, tmp, True)
        sx = _sfx(country)
        r = _tj(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        text = r.stdout + r.stderr
        from taxjson.lib import out
        self.assertEqual(out.console_lint(r.stdout, 100), [], r.stdout)
        shown = re.findall(r"(?m)^Warning: Short position: (\S+) \((\w+)\)",
                           text)
        # The year's shorts, once each; not the ones that went short in
        # 2024 and never trade again (margin and the registered rrsp).
        self.assertEqual(sorted(shown), sorted([
            (f"QABC{sx}", "margin"), (f"QXYZ{sx}", "margin"),
            (f"QRGT{sx}", "rrsp")]), text)
        # One summary line for the three others, naming the command.
        flat = " ".join(r.stdout.split())
        self.assertEqual(flat.count("went short before 2025"), 1, text)
        self.assertIn("Info: 3 positions went short before 2025 with "
                      "missing history; no effect on 2025's numbers", flat)
        self.assertIn("`taxjson find-missing-history --write-missing-"
                      "history --outside-year`", flat)
        # Under its own step, after every account's books.
        steps = re.findall(r"(?m)^==> (.*)$", r.stdout)
        i = steps.index("Checking for missing purchase history")
        self.assertIn("Writing summary reports/margin.sum", steps[:i])
        # The captured text keeps every short (.diag and .sum).
        diag = (root / "work" / "margin_gains.json.diag").read_text()
        for s in ("QPAS", "QOPN", "QABC", "QXYZ"):
            self.assertIn(f"warning: ATTENTION: short: {s}{sx} (margin)",
                          diag)
        self.assertIn(f"QRGL{sx} (rrsp)", (root / "reports" /
                                            "rrsp.sum").read_text())

        # find-missing-history agrees: the summarised ones are its NOT
        # relevant rows, a still-short one says so.
        r = _tj(root, "find-missing-history")
        nr = r.stdout.split("NOT relevant to 2025", 1)[1]
        for s in ("QPAS", "QOPN", "QRGL"):
            self.assertIn(f"{s}{sx}", nr)
        self.assertNotIn("QABC", nr)
        self.assertIn("still short at the start of 2025, with no 2025 "
                      "activity", " ".join(nr.split()))

        before = _tj(root, "sum", "--json").stdout
        r = _tj(root, "find-missing-history", "--write-missing-history",
                "--outside-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads((root / "missing_history.json").read_text())
        self.assertEqual(sorted((e["symbol"], e["account"]) for e in doc),
                         [(f"QOPN{sx}", "margin"), (f"QPAS{sx}", "margin"),
                          (f"QRGL{sx}", "rrsp")])
        self.assertTrue(all(e["_outside_year"] == 2025 for e in doc))
        # A second call adds nothing and keeps the file (no --force).
        r = _tj(root, "find-missing-history", "--write-missing-history",
                "--outside-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to add to missing_history.json",
                      " ".join(r.stderr.split()).lower())
        r = _tj(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("went short before", r.stdout)
        self.assertEqual(len(re.findall(r"(?m)^Warning: Short position:",
                                        r.stdout)), 3, r.stdout)
        # The year's numbers do not move.
        self.assertEqual(before, _tj(root, "sum", "--json").stdout)

    def test_outside_year_keeps_a_reviewed_file(self):
        tmp, root = _make("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertEqual(_tj(root, "run", "--no-input").returncode, 0)
        mine = [{"symbol": "QHAND.TO", "account": "margin",
                 "_note": "added by hand"}]
        (root / "missing_history.json").write_text(json.dumps(mine))
        r = _tj(root, "find-missing-history", "--write-missing-history",
                "--outside-year")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads((root / "missing_history.json").read_text())
        self.assertEqual(doc[0], mine[0])
        self.assertEqual(len(doc), 4)
        self.assertTrue(list(root.glob("missing_history.json.bak*")))

    def test_outside_year_needs_write(self):
        tmp, root = _make("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertEqual(_tj(root, "run", "--no-input").returncode, 0)
        r = _tj(root, "find-missing-history", "--outside-year")
        self.assertEqual(r.returncode, 2, r.stderr)
        r = _tj(root, "find-missing-history", "--write-missing-history",
                "--outside-year", "--all-history")
        self.assertEqual(r.returncode, 2, r.stderr)


class TestListMarksMissingHistory(unittest.TestCase):
    """`taxjson list` tells a purchase missing from the files (a sale
    with nothing to close) from a real short (the broker marks it, IB
    code O), with the pairs find-missing-history reports."""

    def test_both_countries(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                self._check(country)

    def _check(self, country):
        sx = _sfx(country)
        tmp, root = _make(country)
        self.addCleanup(shutil.rmtree, tmp, True)
        csv = root / "inputs" / "margin" / "ib_margin.csv"
        csv.write_text(csv.read_text() + _ib(
            "U5550001", [("QSHO", "2025-03-10", -10, 8, 0, "O")],  # pii-ok
            _CUR[country]).split("Code\n", 1)[1])
        r = _tj(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        suspects = {f"QOPN{sx}", f"QPAS{sx}", f"QXYZ{sx}", f"QRGL{sx}",
                    f"QRGT{sx}"}
        for extra in ((), ("--date", "2025-12-31")):
            doc = json.loads(_tj(root, "list", "--json", *extra).stdout)
            got = {x["symbol"]: x["missing_history_suspect"]
                   for x in doc["rows"]}
            self.assertEqual({s for s, v in got.items() if v}, suspects,
                             (extra, got))
            self.assertFalse(got[f"QSHO{sx}"])
            # the existing keys are unchanged
            self.assertIn("deferred_wash", doc["rows"][0])
        r = _tj(root, "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next(ln for ln in r.stdout.splitlines()
                    if f"QPAS{sx}" in ln)
        self.assertTrue(line.rstrip().endswith("missing history?"), line)
        line = next(ln for ln in r.stdout.splitlines()
                    if f"QSHO{sx}" in ln)
        self.assertNotIn("missing history", line)
        r = _tj(root, "list", "--negative")
        out = r.stdout
        a = out.index("Short positions (1):")
        b = out.index("Missing history (a sale with no purchase in your "
                      "files) (5):")
        self.assertLess(a, b)
        self.assertIn(f"QSHO{sx}", out[a:b])
        self.assertNotIn(f"QSHO{sx}", out[b:])
        for sym in suspects:
            self.assertIn(sym, out[b:])
        self.assertIn("`taxjson find-missing-history --write-missing-history "
                      "--all-history`", " ".join(out.split()))
        # find-missing-history reports the same pairs, the real short
        # apart.
        fmh = _tj(root, "find-missing-history", "--year", "2025").stdout
        trunc = fmh.split("TRUNCATED HISTORY", 1)[1]
        for sym in suspects:
            self.assertIn(sym, trunc)
        self.assertNotIn(f"QSHO{sx}", trunc)
        self.assertIn(f"QSHO{sx}", fmh.split("TRUNCATED HISTORY", 1)[0])


class TestYearScopeLib(unittest.TestCase):
    """lib/missing_history: the row fields the run and the report share."""

    def _rows(self, book, year=2025, country="canada", registered=None):
        from taxjson.lib.missing_history import classify_year_shorts
        return classify_year_shorts(
            book, year, country=country,
            registered=registered or {"margin": False, "qt": False},
            date_basis="trade")

    def test_activity_and_year_start(self):
        from tax_rules.dual import tx
        book = [
            # short in 2024, a dividend in 2025: touched in the year
            tx("BUYSELL", "2024-03-01", "QDV.TO", -10, 100),
            tx("DIVIDEND", "2025-03-01", "QDV.TO", 0, 5.0,
               gross_amount=5.0),
            # short in 2024, nothing since: still short, not listed
            tx("BUYSELL", "2024-03-01", "QNO.TO", -10, 100),
            # short in 2024, covered in 2024: flat at the year's start
            tx("BUYSELL", "2024-03-01", "QFL.TO", -10, 100),
            tx("BUYSELL", "2024-04-01", "QFL.TO", 10, 90),
        ]
        rows = self._rows(book)
        dv = rows[("QDV.TO", "margin")]
        self.assertFalse(dv.affects_year)
        self.assertTrue(dv.in_year_activity)
        self.assertTrue(dv.year_listed)
        no = rows[("QNO.TO", "margin")]
        self.assertFalse(no.year_listed)
        self.assertTrue(no.short_at_year_start)
        fl = rows[("QFL.TO", "margin")]
        self.assertFalse(fl.year_listed)
        self.assertFalse(fl.short_at_year_start)

    def test_pool_differs_by_country(self):
        # One account's short of 2024 with another taxable account
        # trading the symbol in 2025: Canada pools the two accounts'
        # shares (s.47) — that account's 2025 gain moves, so the pair
        # is listed; the US keeps basis per account — it is not.
        from tax_rules.dual import tx
        book = [tx("BUYSELL", "2024-03-01", "QPL", -10, 100),
                tx("BUYSELL", "2023-03-01", "QPL", 20, 150, account="qt"),
                tx("BUYSELL", "2025-03-01", "QPL", -20, 220, account="qt")]
        ca = self._rows(book, country="canada")[("QPL", "margin")]
        us = self._rows(book, country="usa")[("QPL", "margin")]
        self.assertEqual(ca.pooled_with, ("qt",))
        self.assertTrue(ca.year_listed)
        self.assertEqual(us.pooled_with, ())
        self.assertFalse(us.year_listed)
        # A registered account is not in the pool.
        reg = self._rows(book, country="canada",
                         registered={"margin": False, "qt": True})
        self.assertFalse(reg[("QPL", "margin")].year_listed)

    def test_pool_on_the_run_console(self):
        tmp, root = _make("canada", extra_accounts={
            "qt": ("taxable", "U5550003",  # pii-ok
                   [("QPAS", "2023-02-01", 40, 2, 81, "O"),
                    ("QPAS", "2025-02-03", -40, 3, -81, "C")])})
        self.addCleanup(shutil.rmtree, tmp, True)
        r = _tj(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertRegex(r.stdout, r"(?m)^Warning: Short position: "
                                   r"QPAS\.TO \(margin\)")
        self.assertIn("Info: 2 positions went short before 2025",
                      r.stdout)
        r = _tj(root, "find-missing-history")
        aff = r.stdout.split("AFFECTS 2025", 1)[1].split("\n\n", 1)[0]
        self.assertIn("QPAS.TO", aff)
        self.assertIn("one ACB pool", " ".join(aff.split()))


class TestEngineMessagesOnce(unittest.TestCase):
    """The same engine message from two passes (the account's gains, the
    blended pass reading the sheltered context, a failed stage's echo)
    is shown once; the continuation lines follow their message."""

    def setUp(self):
        p = mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "100"})
        p.start()
        self.addCleanup(p.stop)
        from taxjson.bin import taxjson_run as R
        self.R = R
        R._SHOWN_THIS_RUN.clear()
        self.addCleanup(R._SHOWN_THIS_RUN.clear)

    def _out(self, fn, *a, **k):
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            fn(*a, **k)
        return o.getvalue() + e.getvalue()

    def test_short_keyed_on_symbol_and_account(self):
        R = self.R
        a = ("warning: ATTENTION: short: QRG.TO (rrsp): the broker codes "
             "the sale on 2024-04-01 CLOSING (IB code C), but the data "
             "holds no position to close")
        b = ("taxjson-gains: warning: ATTENTION: short: QRG.TO (rrsp): "
             "registered accounts cannot be short — it sells 5 more")
        first = self._out(R._echo_captured, a, once=True)
        self.assertIn("Short position: QRG.TO (rrsp)", first)
        self.assertEqual(self._out(R._echo_captured, b, once=True), "")
        stage = "\n".join([a, "  a continuation of it",
                           "warning: something else",
                           "  its own continuation"])
        again = self._out(R._echo_stage_stderr, stage)
        self.assertNotIn("QRG.TO", again)
        self.assertNotIn("a continuation of it", again)
        self.assertIn("omething else", again)
        self.assertIn("its own continuation", again)
        self.assertEqual(self._out(R._echo_stage_stderr, stage), "")


class TestRunConsoleLines(unittest.TestCase):

    def test_pipe_keeps_order_and_first_step(self):
        tmp, root = _make("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        # A file that parses to nothing: a stderr warning in the middle
        # of the stdout steps.
        (root / "inputs" / "margin" / "ib_empty.csv").write_text(
            "Statement,Header,Field Name,Field Value\n"
            "Trades,Header,DataDiscriminator,Asset Category,Currency,"
            "Symbol,Date/Time,Quantity,T. Price,Proceeds,Comm/Fee,Basis,"
            "Realized P/L,Code\n")
        r = _tj(root, "run", "--no-input", merged=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "==> Checking the project")
        w = next(i for i, ln in enumerate(lines)
                 if "ib_empty.csv parsed to 0 transactions — NONE" in ln)
        # (the account's parse is under its first-pass step when every
        # equity account is read first — lib/cross_listings)
        m = next(i for i, ln in enumerate(lines)
                 if ln.startswith("==> margin  (taxable"))
        self.assertGreater(w, m, r.stdout)
        from taxjson.lib import out
        self.assertEqual(out.console_lint(r.stdout, 100), [], r.stdout)

    def test_failed_stage_headline_in_the_last_line(self):
        tmp, root = _make("canada")
        self.addCleanup(shutil.rmtree, tmp, True)
        (root / "inputs" / "margin" / "bad.tt").write_text(
            "BUYSELL 2025-13-45 10:00:00 QZZ.TO 10 CAD 5 -50 0\n")
        r = _tj(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        last = " ".join(r.stderr.strip().split("\nError: ")[-1].split())
        self.assertTrue(last.startswith("stopped at converting the .tt "
                                        "file for account margin"), last)
        self.assertIn("date '2025-13-45' is not a valid YYYY-MM-DD date",
                      last)
        self.assertIn("details above", last)

    def test_failure_headline_parts(self):
        from taxjson.bin.taxjson_run import _failure_headline
        self.assertEqual(_failure_headline(
            "note: x\ntaxjson-gains: error: QZA.US: a TRANSFER-in has no "
            "cost in the books — add the original purchase as a .tt "
            "line\n"), "QZA.US: a TRANSFER-in has no cost in the books")
        self.assertEqual(_failure_headline(
            "Traceback (most recent call last):\n  File x\n"
            "ValueError: bad row 3\n"), "bad row 3")
        self.assertEqual(_failure_headline(""), "")
        self.assertEqual(_failure_headline(None), "")


class TestCodesNoteDisplay(unittest.TestCase):
    def test_one_code_per_line_captured_unchanged(self):
        from taxjson.lib.stage_msg import console_lines
        from taxjson.lib.symbol_codes import codes_note, parse_codes_note
        line = codes_note({
            "X000001": {"symbol": "QZM.TO", "evidence":
                        "paired with the Interactive Brokers transfer out "
                        "of 24 on 2026-09-01, account ibm"},
            "X000004": {"symbol": "QZB.TO", "evidence":
                        "name match: 'QZB BANK CORP' on Interactive "
                        "Brokers rows of account ibm"}})
        shown = console_lines(line, "", width_=100)
        self.assertEqual(shown[0], "Info: Questrade internal symbol codes "
                                   "resolved (2):")
        self.assertEqual(shown[1], "  X000001 → QZM.TO (Interactive "
                                   "Brokers transfer out of 24 on "
                                   "2026-09-01, ibm)")
        self.assertEqual(shown[2], "  X000004 → QZB.TO (same name as "
                                   "'QZB BANK CORP' in ibm)")
        # Captured: the one parseable line, as symbol_codes wrote it.
        self.assertEqual(console_lines(line, "", width_=0), [line])
        self.assertEqual(len(parse_codes_note(line)), 2)


if __name__ == "__main__":
    unittest.main()
