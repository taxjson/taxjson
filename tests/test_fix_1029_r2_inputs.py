"""Backlog fixes 2026-10-29, round 2, group `inputs`: how the run finds
and skips inputs, the holdings readers, the XLSX converter and the
parser-output schema check.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*), account names (margin, ib), ids like
99900001, amounts under 1,000 (pii-ok).
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


def _tjs(root, *args, cwd=None, width="0"):
    """The CLI in a child process (stdin closed; unwrapped output, or
    as a person is shown it at `width`)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH=width)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


_TRADES = ("BUYSELL 2025-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
           "BUYSELL 2025-02-03 10:00:00 SYN.TO -10 CAD 20 200 0\n")
_MORE = ("BUYSELL 2025-01-03 10:00:00 ZZT.TO 10 CAD 10 100 0\n"
         "BUYSELL 2025-02-03 10:00:00 ZZT.TO -10 CAD 15 150 0\n")


def _project(top, shared, accounts=("margin",)):
    """A 2025 Canadian project in `top`/2025: its own inputs/, or the
    exports every year shares in `top`/inputs (`inputs_dir`). Returns
    (project folder, the margin account's inputs folder)."""
    p = Path(top) / "2025"
    p.mkdir()
    (p / "taxjson.toml").write_text(
        "[settings]\nlocal_timezone = \"America/Toronto\"\nyear = 2025\n"
        "country = \"canada\"\nbase_currency = \"CAD\"\n"
        "source_currencies = []\n"
        + ("inputs_dir = \"../inputs\"\n" if shared else "")
        + "".join(f"[accounts.{a}]\ntype = \"taxable\"\n"
                  for a in accounts))
    (p / "ticker.map").write_text("")
    base = Path(top) / "inputs" if shared else p / "inputs"
    for a in accounts:
        (base / a).mkdir(parents=True)
    (base / "margin" / "trades.tt").write_text(_TRADES)
    return p, base / "margin"


def _write_xlsx_stub(path):
    """A spreadsheet export: the run refuses it by its suffix alone."""
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")


class TestSharedInputsOmissionChecks(unittest.TestCase):
    """TJ-0482, TJ-0582: with the exports every year shares
    (`inputs_dir = "../inputs"`, the layout `init` makes) the run never
    looked for an account's spreadsheet export or a year subfolder of
    exports: it exited 0 with their trades missing, never naming them,
    while a single-folder project refused the spreadsheet and named the
    subfolder."""

    def test_subfolder_of_shared_inputs_is_named(self):
        for shared in (False, True):
            with self.subTest(shared=shared), \
                    tempfile.TemporaryDirectory() as td:
                p, acc = _project(td, shared)
                (acc / "2023").mkdir()
                (acc / "2023" / "more.tt").write_text(_MORE)
                r = _tjs(p, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                shown = "../inputs" if shared else "inputs"
                self.assertIn(f"{shown}/margin/2023/ holds 1 "
                              f"CSV/.tt/spreadsheet file(s) that are NOT "
                              f"read", r.stderr)
                self.assertIn(f"directly in {shown}/margin/ are processed",
                              r.stderr)

    def test_spreadsheet_in_shared_inputs_is_refused(self):
        for shared in (False, True):
            with self.subTest(shared=shared), \
                    tempfile.TemporaryDirectory() as td:
                p, acc = _project(td, shared)
                _write_xlsx_stub(acc / "activity.xlsx")
                r = _tjs(p, "run", "--no-input")
                self.assertEqual(r.returncode, 1, r.stderr[-2000:])
                shown = "../inputs" if shared else "inputs"
                self.assertIn(f"spreadsheet export(s) in {shown}/ are NOT "
                              f"read", r.stderr)
                self.assertIn(f"{shown}/margin/activity.xlsx", r.stderr)

    def test_other_years_account_folder_is_not_a_warning(self):
        """A shared folder's account another year has is the run's
        note ("not an account of 2025"), never the single-folder
        warning that it has no [accounts] section."""
        with tempfile.TemporaryDirectory() as td:
            p, acc = _project(td, True)
            other = acc.parent / "rrsp"
            other.mkdir()
            (other / "trades.tt").write_text(_MORE)
            r = _tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("has no [accounts.rrsp] section", r.stderr)
            self.assertIn("rrsp — not an account of 2025", r.stderr)


_RBC_HOLDINGS = (
    "Holdings Export as of Dec 31, 2025 at 5:00:00 pm ET\n\n"
    '"Account","Product","Symbol","Name","Quantity","Currency",'
    '"Last Price","Total Book Cost","Total Market Value"\n'
    '"99900099","Equities","ZZE","ZZE CORP","15","CAD","20.00",'
    '"150.00","300.00"\n')


class TestHoldingsFolderReadsBrokerReports(unittest.TestCase):
    """TJ-0424: an RBC Holdings Export (or IB statement) saved in the
    year's holdings/ was skipped: sanity said "no holdings file —
    nothing to compare with" (rc 0) while the same file given by name
    showed a quantity difference. A broker's report there is now read
    like a TOML snapshot (claimed by its name), and any other file
    there is named as not read."""

    def _books(self, td):
        p, acc = _project(td, False)
        (acc / "trades.tt").write_text(
            "BUYSELL 2025-03-03 10:00:00 ZZE.TO 20 CAD 10 200 0\n")
        r = _tjs(p, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        (p / "holdings").mkdir()
        return p

    def test_rbc_export_in_holdings_is_compared(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._books(td)
            (p / "holdings" / "margin_holdings.csv").write_text(
                _RBC_HOLDINGS)
            r = _tjs(p, "sanity")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("QTY_MISMATCH", r.stdout)
            self.assertIn("holdings/margin_holdings.csv", r.stdout)
            self.assertNotIn("no holdings file", r.stdout)
            # The run's own check reads it too.
            r = _tjs(p, "run", "--no-input")
            self.assertIn("==> Checking positions against the broker's "
                          "holdings files", r.stdout)
            self.assertIn("ZZE.TO  QTY_MISMATCH", r.stdout)

    def test_unclaimed_report_names_the_csv_fix(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._books(td)
            (p / "holdings" / "Holdings_Export.csv").write_text(
                _RBC_HOLDINGS)
            r = _tjs(p, "sanity")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("holdings/Holdings_Export.csv: no account claims "
                          "it — name it NAME_holdings.csv", r.stderr)

    def test_other_file_in_holdings_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._books(td)
            (p / "holdings" / "README.txt").write_text("how\n")
            (p / "holdings" / "margin_holdings.pdf").write_bytes(b"%PDF")
            (p / "holdings" / "old").mkdir()
            (p / "holdings" / "old" / "margin.toml").write_text("")
            r = _tjs(p, "sanity")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("holdings/margin_holdings.pdf is not read: it is "
                          "not a holdings TOML", r.stderr)
            self.assertIn("holdings/old/ is not read: only the files "
                          "directly in holdings/ are", r.stderr)
            self.assertNotIn("README", r.stderr)
            self.assertIn("UNCHECKED: 1 account(s)", r.stdout)
            r = _tjs(p, "sanity", "--json")
            import json
            doc = json.loads(r.stdout)
            self.assertFalse(doc["complete"])
            self.assertEqual(len(doc["notes"]), 2)
            # Beside a snapshot that is compared: counted apart.
            (p / "holdings" / "margin_holdings.csv").write_text(
                _RBC_HOLDINGS.replace('"15"', '"20"'))
            r = _tjs(p, "sanity")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("INCOMPLETE: 2 file(s) in holdings/ not read",
                          r.stdout)


class TestHoldingsCheckNamesWhyItCouldNotRun(unittest.TestCase):
    """TJ-0485: a holdings file with no [[holding]] tables ended the
    run's default view with a bare "Warning: holdings check could not
    run" — no file, no reason, no command; only --details added the
    Error. The one line now names the reason and `tjs sanity`, and
    the detailed form says what to do. Since TJ-0643 such a file is an
    input sanity cannot read (exit 2): its Error, naming the file from
    the project, comes first, then the run's warning and the command."""

    def test_reason_and_command_on_the_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            p, acc = _project(td, False)
            (p / "holdings").mkdir()
            (p / "holdings" / "margin_holdings.toml").write_text(
                '[meta]\naccount = "margin"\nas_of = 2025-12-31\n\n'
                '[positions]\n"SYN.TO" = 10\n')
            r = _tjs(p, "run", "--no-input", width="120")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            out = r.stdout + r.stderr
            self.assertIn("Error: holdings/margin_holdings.toml has no "
                          "[[holding]] array (a holdings file has "
                          "[[holding]] tables)\n"
                          "Warning: holdings check could not run — "
                          "`tjs sanity`", out)
            r = _tjs(p, "run", "--no-input")
            self.assertIn("tjs run: error: holdings/margin_holdings.toml "
                          "has no [[holding]] array (a holdings file has "
                          "[[holding]] tables)\n"
                          "  warning: holdings check could not run\n"
                          "    The Error above names the file to fix; then "
                          "`tjs sanity`.",
                          r.stdout + r.stderr)


_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Activity Type,Account #,Account Type\n")
_RBC_EMPTY = ('Account Activity Detail Report\n"Account Number: 99900099"\n'
              '"Date Range: January 1, 2025 - December 31, 2025"\n\n'
              "Date,Activity,Symbol,Description,Quantity,Price,"
              "Settlement Date,Currency,Value,Amount\n")


class TestHeaderOnlyExport(unittest.TestCase):
    """TJ-0483: a header-only export (a year with no activity) warned
    "parsed to 0 transactions — NONE of its rows are in the books" on
    every run, `tjs sum` said the books were not clean and the
    checklist's run-clean step pointed to `tjs run`, which can never
    clear it. A file with no row after its header is now an Info, as
    IB's empty statement is; a file whose rows book nothing still warns,
    and the checklist names the file as the fix, not a re-run."""

    def test_header_only_exports_are_an_info(self):
        for name, text in (("questrade_2024.csv", _QT_HEADER),
                           ("rbc_2024.csv", _RBC_EMPTY)):
            with self.subTest(name=name), \
                    tempfile.TemporaryDirectory() as td:
                p, acc = _project(td, False)
                (acc / name).write_text(text)
                r = _tjs(p, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                out = r.stdout + r.stderr
                self.assertIn(f"{name}: 0 tax objects (no row after its "
                              f"header: an export of a period with no "
                              f"activity)", out)
                self.assertNotIn("parsed to 0 transactions", out)
                summ = (p / "reports" / "margin.sum").read_text()
                self.assertNotIn("parsed to 0 transactions", summ)
                r = _tjs(p, "run", "--no-input", "--strict")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def test_rows_that_book_nothing_name_the_file_fix(self):
        with tempfile.TemporaryDirectory() as td:
            p, acc = _project(td, False)
            (acc / "q.csv").write_text(
                _QT_HEADER + "2025-03-03 12:00:00 AM,2025-03-03 12:00:00 "
                "AM,ZZZ,ZZE.TO,SOMETHING NEW,0,0,0,0,0,CAD,Other,99900001,"
                "Individual margin\n")
            r = _tjs(p, "run", "--no-input")
            self.assertIn("q.csv parsed to 0 transactions — NONE of its "
                          "rows are in the books", r.stdout + r.stderr)
            r = _tjs(p, "checklist", "--run", "--only", "run-clean")
            out = r.stdout + r.stderr
            line = next((ln for ln in out.splitlines()
                         if "run-clean:" in ln and ln.startswith("! ")), "")
            self.assertIn("q.csv parsed to 0 transactions", line, out)
            self.assertFalse(line.rstrip().endswith("tjs run"), line)
            self.assertIn("check the file's header and date range", out)


_RBC_PRE = "Holdings Export as of March 3, 2025\n"
_IB_PRE = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,Period,"January 1, 2025 - March 3, 2025"\n'
           'Account Information,Header,Field Name,Field Value\n'
           'Account Information,Data,Account,U1234567\n')


def _positions(text):
    """read_positions of `text`, alone in a folder of its own."""
    from taxjson.lib.positions_reports import read_positions
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "h.csv"
        p.write_text(text)
        return read_positions(p)


class TestHoldingsReadersRefuseWhatTheyCannotRead(unittest.TestCase):
    """TJ-0529 (a nonzero position with no symbol vanished from an RBC
    or IB report), TJ-0530 (two Quantity columns: the first or the last
    taken in silence), TJ-0531 (an RBC cost label's currency read only
    when upper-case CAD or USD), TJ-0532 (an IB Mult of 0 or below
    returned as a valid option position)."""

    def _refused(self, text, *needles):
        from taxjson.lib.positions_reports import PositionsReportError
        with self.assertRaises(PositionsReportError) as cm:
            _positions(text)
        for n in needles:
            self.assertIn(n, str(cm.exception))

    def test_tj0529_blank_symbol_with_a_quantity_is_refused(self):
        self._refused(_RBC_PRE + "Symbol,Quantity,Currency,Book Value\n"
                      ",10,CAD,100\n",
                      "h.csv:3: a position of 10 with no Symbol")
        self._refused(_IB_PRE + "Open Positions,Header,DataDiscriminator,"
                      "Asset Category,Currency,Symbol,Quantity,Cost Basis\n"
                      "Open Positions,Data,Summary,Stocks,USD,,10,100\n",
                      "h.csv:6: a position of 10 with no Symbol")

    def test_tj0529_cash_and_total_rows_are_still_left_out(self):
        rep = _positions(_RBC_PRE + "Symbol,Name,Quantity,Currency,"
                         "Book Value\nZZR,ZZR CORP,10,CAD,100\n"
                         ",Cash,500,CAD,500\n,Total,,CAD,600\n")
        self.assertEqual([r.symbol for r in rep.rows], ["ZZR.TO"])
        rep = _positions(_IB_PRE + "Open Positions,Header,"
                         "DataDiscriminator,Asset Category,Currency,Symbol,"
                         "Quantity,Cost Basis\n"
                         "Open Positions,Data,Summary,Stocks,USD,ZZQ,10,100\n"
                         "Open Positions,Data,Summary,Cash,USD,,500,500\n")
        self.assertEqual([r.symbol for r in rep.rows], ["ZZQ.US"])

    def test_tj0530_a_column_twice_is_refused(self):
        self._refused(_RBC_PRE + "Symbol,Quantity,Quantity,Currency,"
                      "Book Value\nZZR,10,100,CAD,100\n",
                      "the header has 'Quantity' 2 times")
        self._refused(_IB_PRE + "Open Positions,Header,DataDiscriminator,"
                      "Asset Category,Currency,Symbol,Quantity,Quantity,"
                      "Cost Basis\nOpen Positions,Data,Summary,Stocks,USD,"
                      "ZZQ,10,100,100\n",
                      "the header has 'Quantity' 2 times")

    def test_tj0531_cost_label_currency_in_any_case(self):
        for label, cur in (("Book Value (USD)", "USD"),
                           ("Book Value (usd)", "USD"),
                           ("Book Value USD", "USD"),
                           ("Book Value (EUR)", "EUR"),
                           ("Book Value", "CAD")):
            with self.subTest(label=label):
                rep = _positions(_RBC_PRE + f"Symbol,Quantity,Currency,"
                                 f"{label}\nZZR,10,CAD,100\n")
                self.assertEqual(rep.rows[0].cost_currency, cur)
        self._refused(_RBC_PRE + "Symbol,Quantity,Currency,"
                      "Book Value (USD/CAD)\nZZR,10,CAD,100\n",
                      "names USD and CAD")

    def test_tj0532_contract_size_must_be_positive(self):
        head = (_IB_PRE + "Open Positions,Header,DataDiscriminator,"
                "Asset Category,Currency,Symbol,Quantity,Mult,Cost Basis\n")
        for mult in ("-10", "0"):
            with self.subTest(mult=mult):
                self._refused(head + f"Open Positions,Data,Summary,Equity "
                              f"and Index Options,USD,ZZQ 19DEC25 10 C,1,"
                              f"{mult},100\n",
                              f"Mult {int(mult)} is not a positive "
                              f"contract size")
        rep = _positions(head + "Open Positions,Data,Summary,Equity and "
                         "Index Options,USD,ZZQ 19DEC25 10 C,1,100,100\n")
        self.assertEqual(rep.rows[0].multiplier, 100.0)


try:
    import openpyxl
    _XLSX = True
except ImportError:
    _XLSX = False


@unittest.skipUnless(_XLSX, "the optional xlsx extra (openpyxl) is not "
                            "installed")
class TestXlsxConversionKeepsTheSheet(unittest.TestCase):
    """TJ-0534 (pandas renamed a second Quantity label Quantity.1, so the
    converted Questrade file read 10 units where the raw one refused the
    mismatch), TJ-0535 (an id cell shown 0012345 with number
    format 0000000 came out 12345), TJ-0536 (`--sheet 2025` was forced
    to the index 2025 and could not select a sheet named 2025)."""

    _HEAD = ["Transaction Date", "Settlement Date", "Action", "Symbol",
             "Description", "Quantity", "Quantity", "Price",
             "Gross Amount", "Commission", "Net Amount", "Currency",
             "Account #", "Activity Type", "Account Type"]
    _ROW = ["2025-03-03", "2025-03-04", "Buy", "SYN.TO", "SYN CORP", 10,
            100, 10, -100, 0, -100, "CAD", 12345, "Trades",
            "Individual margin"]

    def _book(self, td, title="2025"):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = title
        ws.append(self._HEAD)
        ws.append(self._ROW)
        ws.cell(2, 13).number_format = "0000000"
        path = Path(td) / "book.xlsx"
        wb.save(path)
        return path

    def _convert(self, *args):
        import contextlib
        import io
        import sys
        from unittest import mock
        from taxjson.bin import xlsx_to_csv as X
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["taxjson-xlsx-to-csv",
                                             *args]), \
                contextlib.redirect_stderr(err):
            try:
                X.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, err.getvalue()

    def test_duplicate_labels_and_padded_ids_survive(self):
        import csv as _csv
        with tempfile.TemporaryDirectory() as td:
            book = self._book(td)
            out = Path(td) / "out.csv"
            code, err = self._convert(str(book), "-o", str(out))
            self.assertEqual(code, 0, err)
            with out.open(newline="") as fh:
                rows = list(_csv.reader(fh))
        self.assertEqual(rows[0], self._HEAD)
        self.assertEqual(rows[1][5:7], ["10", "100"])
        self.assertEqual(rows[1][12], "0012345")
        # The parser sees the original layout: the raw CSV's refusal.
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "q.csv"
            with f.open("w", newline="") as fh:
                _csv.writer(fh).writerows(rows)
            with contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(BrokerageParseError):
                QuestradeBrokerage().parse_file(f)

    def test_a_sheet_named_by_a_number(self):
        with tempfile.TemporaryDirectory() as td:
            book = self._book(td)
            out = Path(td) / "out.csv"
            code, err = self._convert(str(book), "--sheet", "2025", "-o",
                                      str(out))
            self.assertEqual(code, 0, err)
            self.assertTrue(out.read_text().startswith("Transaction Date"))
            code, err = self._convert(str(book), "--sheet", "0", "-o",
                                      str(out))
            self.assertEqual(code, 0, err)
            code, err = self._convert(str(book), "--sheet", "2024", "-o",
                                      str(out))
            self.assertEqual(code, 1)
            self.assertIn("no worksheet '2024': the workbook has 1 "
                          "sheet(s), '2025'", " ".join(err.split()))


class TestOpeningReasonQuoting(unittest.TestCase):
    """TJ-0537: the comment cut before shlex toggled its quote state on
    an escaped quote, so `reason="statement \\"#2\\""` (valid shell
    quoting) was cut at its `#` and refused as an unclosed quote."""

    def _reason(self, tail):
        from taxjson.bin.taxjson_convert_tt import parse_unknown_opening_line
        line = "OPENING 2025-01-02 SYN.TO 10 cost=unknown " + tail
        return parse_unknown_opening_line(line, "margin/x.tt")["reason"]

    def test_hash_inside_any_quoting_stays_in_the_reason(self):
        import shlex
        # (the line's tail, the reason, the tail without its comment:
        # shlex reads that the same way)
        for tail, want, body in (
                ('reason="statement \\"#2\\""', 'statement "#2"', None),
                ('reason="statement #2"', "statement #2", None),
                ("reason='statement #3'", "statement #3", None),
                ("reason=statement\\#4", "statement#4", None),
                ('reason="a # b" # a comment "with a quote', "a # b",
                 'reason="a # b"'),
                ('reason="ends in \\\\" # note', "ends in \\",
                 'reason="ends in \\\\"')):
            with self.subTest(tail=tail):
                self.assertEqual(self._reason(tail), want)
                self.assertEqual(
                    shlex.split(body or tail)[0].split("=", 1)[1], want)

    def test_a_comment_outside_quotes_is_still_cut(self):
        self.assertEqual(self._reason("# reason=\"never read\""), "")
        self.assertEqual(self._reason('reason="kept" #"x'), "kept")


class TestSchemaRefusesImpossibleValues(unittest.TestCase):
    """TJ-0522: the parser-output schema passed a February 30 date (the
    pattern alone was checked) and a NaN quantity (NaN fails every
    comparison, so the zero-trade and notional checks never fired)."""

    _ROW = dict(action="BUYSELL", date="2025-03-03",
                date_settle="2025-03-04", symbol="SYN.US", quantity=1,
                price=10, currency="USD", net_amount=10)

    def _errors(self, **change):
        from taxjson.lib.brokerages.schema import validate_transactions
        return validate_transactions([{**self._ROW, **change}])[0]

    def test_a_clean_row_passes(self):
        self.assertEqual(self._errors(), [])

    def test_a_date_that_is_no_calendar_day(self):
        for field, v in (("date", "2025-02-30"),
                         ("date_settle", "2025-13-01")):
            with self.subTest(field=field):
                errs = self._errors(**{field: v})
                self.assertEqual(len(errs), 1, errs)
                self.assertIn(f"{field}={v!r} is not a calendar date",
                              errs[0])

    def test_a_number_that_is_not_finite_or_not_a_number(self):
        for field, v, why in (("quantity", float("nan"), "not finite"),
                              ("net_amount", float("inf"), "not finite"),
                              ("gross_amount", float("-inf"),
                               "not finite"),
                              ("multiplier", float("nan"), "not finite"),
                              ("price", "ten", "not a number")):
            with self.subTest(field=field):
                errs = self._errors(**{field: v})
                self.assertEqual(len(errs), 1, errs)
                self.assertIn(f"{field}={v!r} is {why}", errs[0])
                self.assertIn("SYN.US 2025-03-03", errs[0])


if __name__ == "__main__":
    unittest.main()
