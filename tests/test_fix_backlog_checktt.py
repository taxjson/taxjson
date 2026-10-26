"""Backlog group `checktt`: `taxjson check-tt` and the .tt readers
(lib/check_tt, bin/taxjson_convert_tt, cmd_check_tt). Each test names
its backlog id."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_convert_tt as CV
from taxjson.lib import check_tt as CT


GOOD = "BUYSELL 2024-01-15 09:30:00 ZZQA.TO 10 CAD 10 101 1\n"
TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2024\n'
        'country = "canada"\nbase_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\n'
        '[accounts.tfsa]\ntype = "sheltered"\ntransfers = true\n'
        '[accounts.coins]\ntype = "taxable"\ncrypto = true\n')
ACCOUNTS = {"margin": {"type": "taxable"},
            "tfsa": {"type": "sheltered", "transfers": True},
            "coins": {"type": "taxable", "crypto": True}}


def _project(tmp, files, toml=TOML):
    """A one-folder project: taxjson.toml and inputs/<rel> files."""
    root = Path(tmp)
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(toml)
    for rel, text in files.items():
        p = root / "inputs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _years_project(tmp, files, toml=TOML):
    """The default layout: <tmp>/2024/taxjson.toml reading the shared
    <tmp>/inputs/ (inputs_dir = "../inputs")."""
    base = Path(tmp)
    year = base / "2024"
    year.mkdir(parents=True, exist_ok=True)
    (year / "taxjson.toml").write_text(
        toml.replace("[settings]\n", '[settings]\ninputs_dir = "../inputs"\n',
                     1))
    for rel, text in files.items():
        p = base / "inputs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return base, year


def _run(argv, cwd=None):
    """cmd_check_tt in-process: (stdout, stderr, exit code); `cwd`: the
    shell's folder."""
    import os
    from taxjson.bin.taxjson_run import _build_parser
    p, _sub = _build_parser()
    args = p.parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    here = os.getcwd()
    try:
        if cwd is not None:
            os.chdir(cwd)
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                args.func(args)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                if isinstance(e.code, str):
                    err.write(e.code)
    finally:
        os.chdir(here)
    return out.getvalue(), err.getvalue(), code


def _raises(line, source="hand.tt:1"):
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            CV.parse_tt_line(line, account_name="margin", source=source)
        except ValueError as e:
            return str(e)
    raise AssertionError(f"accepted: {line!r}")


class TestShortLine(unittest.TestCase):
    """TJ-0128: a .tt line with too few fields said `(IndexError: list
    index out of range)` and showed the line as a repr with its `\\n`."""

    def test_buysell_names_the_missing_fields(self):
        msg = _raises("BUYSELL 2024-03-01 10:00:00 ZZQA.US 10 USD\n")
        self.assertTrue(msg.startswith(
            "hand.tt:1: BUYSELL needs PRICE and TOTAL after CUR "
            "(BUYSELL DATE TIME SYMBOL QTY CUR PRICE TOTAL [FEE] [xSIZE])"),
            msg)
        self.assertNotIn("IndexError", msg)
        self.assertNotIn("\\n", msg)
        self.assertIn("'BUYSELL 2024-03-01 10:00:00 ZZQA.US 10 USD'", msg)

    def test_every_row_action(self):
        cases = {
            "TRANSFER 2024-03-01 10:00:00 ZZQA.US 10 USD 1":
                "TRANSFER needs TOTAL after PRICE",
            "SPLIT 2024-03-01 10:00:00 ZZQA.US":
                "SPLIT needs NEW and RATIO after OLD",
            "DIVIDEND 2024-03-01 10:00:00 ZZQA.US 10 USD record=2024-02-20":
                "DIVIDEND needs PRICE and GROSS after CUR",
            "TAX 2024-03-01 10:00:00 ZZQA.US 10 USD 0":
                "TAX needs GROSS after PRICE",
            "INTEREST 2024-03-01 10:00:00":
                "INTEREST needs CUR and AMOUNT after TIME",
            "FEE 2024-03-01 10:00:00 CAD": "FEE needs AMOUNT after CUR",
            "ADJUST 2024-03-01 10:00:00 ZZQA.US CAD":
                "ADJUST needs AMOUNT after CUR",
            "ASSIGN 2024-03-01 10:00:00":
                "ASSIGN needs SYMBOL, QTY, CUR, PRICE and TOTAL after TIME",
        }
        for line, want in cases.items():
            with self.subTest(line=line):
                msg = _raises(line + "\n")
                self.assertIn(want, msg)
                self.assertNotIn("Error:", msg.replace("hand.tt:1: ", ""))

    def test_a_bad_number_says_why_without_the_exception_name(self):
        msg = _raises("BUYSELL 2024-03-01 10:00:00 ZZQA.US abc USD 1 1\n")
        self.assertIn("malformed .tt line (number 'abc'", msg)
        self.assertNotIn("BrokerageParseError", msg)
        self.assertNotIn("\\n", msg)

    def test_check_tt_shows_the_same_message(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "hand.tt")
            p.write_text("BUYSELL 2024-03-01 10:00:00 ZZQA.US 10 USD\n")
            rep = CT.check_file(p, file="hand.tt", account="margin",
                                crypto=False, equity=True)
        self.assertEqual(rep.errors, 1)
        self.assertTrue(rep.problems[0].message.startswith(
            "BUYSELL needs PRICE and TOTAL"), rep.problems[0].message)


class TestSplitRatio(unittest.TestCase):
    """TJ-0006 (SPLIT part): a SPLIT ratio of 0 or below parsed, so
    check-tt said `Every line parses.`; the run stopped later naming
    the converted work file, not the line."""

    def test_refused_at_its_line(self):
        for ratio in ("0", "-2", "0.0"):
            with self.subTest(ratio=ratio):
                msg = _raises(f"SPLIT 2024-01-05 09:30:00 ZZQA.TO ZZQA.TO "
                              f"{ratio}\n")
                self.assertTrue(msg.startswith(
                    f"hand.tt:1: SPLIT ratio must be > 0 (got {ratio})"),
                    msg)

    def test_a_real_ratio_parses(self):
        tx = CV.parse_tt_line("SPLIT 2024-01-05 09:30:00 ZZQA.TO ZZQA.TO "
                              "0.1\n", account_name="margin")
        self.assertEqual(tx["quantity"], 0.1)

    def test_check_tt_names_the_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "case.tt")
            p.write_text("# split\nSPLIT 2024-01-05 09:30:00 ZZQA.TO "
                         "ZZQA.TO 0\n")
            rep = CT.check_file(p, file="case.tt", account="margin",
                                crypto=False, equity=True)
        self.assertEqual([(x.line, x.severity) for x in rep.problems],
                         [(2, CT.ERROR)])
        self.assertIn("SPLIT ratio must be > 0", rep.problems[0].message)


class TestLineNumbers(unittest.TestCase):
    """TJ-0029: check-tt split lines with str.splitlines, which also
    breaks at a form feed, U+2028 and the like inside a line: every
    later line was numbered differently from the run (which splits at a
    line feed only) and a comment's tail became a false ERROR."""

    TEXT = ("# hand entries\n"
            "BUYSELL  2024-02-07 09:41:27 ZZQA.US  10 USD 40 401.00 1.00\n"
            "BUYSELL  2024-02-08 09:41:27 ZZQA.US  10 USD 40 401.00 1.00 "
            "# note\u2028second\n"
            "BUYSEL   2024-02-09 09:41:27 ZZQA.US  10 USD 40 401.00 1.00\n"
            "BUYSELL  2024-02-10 09:41:27 ZZQA.US  10 USD 40 401.00 "
            "1.00\x0c\n"
            "BUYSELL  2024-13-11 09:41:27 ZZQA.US  10 USD 40 401.00 1.00\n")

    def test_numbered_as_the_run_numbers_them(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "hand.tt")
            p.write_text(self.TEXT, encoding="utf-8")
            rep = CT.check_file(p, file="hand.tt", account="margin",
                                crypto=False, equity=True)
            with contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(ValueError) as cm:
                CV.tt_to_json(p, "margin", equity=True)
        self.assertEqual(rep.lines, 6)
        errs = [(x.line, x.message.split(".")[0]) for x in rep.problems
                if x.severity == CT.ERROR]
        self.assertEqual([n for n, _m in errs], [4, 6], errs)
        self.assertNotIn("'second'", " ".join(m for _n, m in errs))
        # The run stops at the first, under the same number.
        self.assertTrue(str(cm.exception).startswith(
            "hand.tt:4: unknown .tt action 'BUYSEL'"), str(cm.exception))


class TestFileNameProblems(unittest.TestCase):
    """TJ-0012: a file the run refuses by its name was listed with
    STATUS `ok` (--json files[].errors 0), and the closing line counted
    the file-name problem as a line that cannot be read."""

    def test_counted_in_the_files_row(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"margin/foo_base.tt": GOOD,
                                "margin/ok.tt": GOOD})
            doc = CT.check_project(root, ACCOUNTS, only=["margin"])
        by_file = {f["file"]: f for f in doc["files"]}
        self.assertEqual(by_file["inputs/margin/foo_base.tt"]["errors"], 1)
        self.assertEqual(by_file["inputs/margin/ok.tt"]["errors"], 0)
        self.assertEqual(doc["errors"], 1)
        text = "\n".join(CT.render(doc, width_=0))
        self.assertRegex(text, r"(?m)^  inputs/margin/foo_base\.tt +margin "
                               r"+1 +1 +0 +1 error$")
        self.assertIn("! 1 problem the run refuses; fix it before "
                      "`tjs run`", text)
        self.assertNotIn("cannot be read", text)

    def test_work_file_collision_too(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"margin/start.tt": GOOD,
                                "margin/start.TT": GOOD})
            doc = CT.check_project(root, ACCOUNTS, only=["margin"])
        self.assertEqual(sum(f["errors"] for f in doc["files"]), 1)
        self.assertEqual(doc["errors"], 1)


class TestTaxableTransfers(unittest.TestCase):
    """TJ-0006: a TRANSFER or ACQUIRED line in a type = "taxable"
    account parsed (`Every line parses.`, exit 0) and `tjs run` stopped
    with "TRANSFER is not allowed in taxable accounts". check-tt now
    applies what the run nets first (an OPENING snapshot's cut-off, a
    zero-net pair) and names each line left."""

    XFER = "TRANSFER 2024-01-05 09:30:00 ZZQA.TO 10 CAD 10.00 100.00 0\n"
    ACQ = ("ACQUIRED 2019-05-10 09:30:00 ZZQA.TO 40 CAD 12.00 480.00 "
           "ARRIVED 2024-06-03\n")

    def _doc(self, files, accounts=None):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, files)
            return CT.check_project(root, accounts or ACCOUNTS)

    def _errors(self, doc):
        return [(p["where"], p["message"]) for p in doc["problems"]
                if p["severity"] == CT.ERROR]

    def test_transfer_line_in_a_taxable_account(self):
        doc = self._doc({"margin/case.tt": GOOD + self.XFER})
        (where, msg), = self._errors(doc)
        self.assertEqual(where, "inputs/margin/case.tt:2")
        self.assertIn("TRANSFER is not allowed in taxable accounts", msg)
        self.assertIn("taxable account margin", msg)
        self.assertEqual(doc["files"][0]["errors"], 1)

    def test_acquired_line_in_a_taxable_account(self):
        doc = self._doc({"margin/case.tt": self.ACQ})
        (where, msg), = self._errors(doc)
        self.assertEqual(where, "inputs/margin/case.tt:1")
        self.assertIn("an ACQUIRED line books a counter-TRANSFER", msg)
        self.assertIn("BUYSELL line dated its true day", msg)

    def test_a_crypto_account_is_taxable_too(self):
        doc = self._doc({"coins/c.tt": "TRANSFER 2024-01-05 09:30:00 ZZCOIN "
                                       "1 CAD 10.00 10.00\n"})
        self.assertEqual([w for w, _m in self._errors(doc)],
                         ["inputs/coins/c.tt:1"])

    def test_what_the_run_nets_first_is_no_error(self):
        # A zero-net DECLARED pair (the documented attestation), and a
        # transfer before an OPENING snapshot of the symbol (replaced).
        doc = self._doc({
            "margin/pair.tt":
                "TRANSFER 2024-04-22 09:30:00 ZZQA.TO 50 CAD 0.0 0.0 "
                "DECLARED\n"
                "TRANSFER 2024-04-23 09:30:00 ZZQA.TO -50 CAD 0.0 0.0 "
                "DECLARED\n",
            "margin/open.tt":
                "TRANSFER 2023-06-01 09:30:00 ZZQB.TO 10 CAD 10.00 100.00\n"
                "OPENING 2023-12-29 ZZQB.TO 10 CAD 100.00\n"})
        self.assertEqual(self._errors(doc), [])
        self.assertEqual(doc["warnings"], 0)

    def test_sheltered_account_books_it(self):
        doc = self._doc({"tfsa/t.tt": self.XFER + self.ACQ})
        self.assertEqual((doc["errors"], doc["warnings"]), (0, 0))

    def test_transfers_true_is_a_warning(self):
        accounts = dict(ACCOUNTS, margin={"type": "taxable",
                                          "transfers": True})
        doc = self._doc({"margin/case.tt": self.XFER}, accounts)
        self.assertEqual(doc["errors"], 0)
        self.assertEqual(doc["warnings"], 1)
        self.assertIn("unless it cancels a TRANSFER row of the account's "
                      "exports", doc["problems"][0]["message"])

    def test_the_command_exits_1(self):
        with tempfile.TemporaryDirectory() as d:
            _project(d, {"margin/case.tt": self.XFER})
            out, _err, code = _run(["-C", d, "check-tt"])
        self.assertEqual(code, 1, out)
        self.assertNotIn("Every line parses", out)


DUP = ("OPENING 2023-12-31 ZZQY.TO 20 cost=unknown\n"
       "OPENING 2023-12-31 ZZQY.TO 30 cost=unknown\n")


class TestFileModeGetsTheAccountsChecks(unittest.TestCase):
    """TJ-0007: `check-tt FILE.tt` ran only the per-line readers, so a
    symbol opened twice at an unknown cost, a file name the run refuses
    (and now a TRANSFER a taxable account cannot hold) said `Every line
    parses.` where `check-tt ACCOUNT` and the run refuse them."""

    def test_same_problems_as_account_mode(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"margin/dup.tt": DUP,
                                "margin/foo_base.tt": GOOD,
                                "margin/x.tt": GOOD + TestTaxableTransfers
                                .XFER})
            acct = CT.check_project(root, ACCOUNTS, only=["margin"])
            files = CT.check_paths([root / "inputs/margin/dup.tt",
                                    root / "inputs/margin/foo_base.tt",
                                    root / "inputs/margin/x.tt"],
                                   shown=["dup.tt", "foo_base.tt", "x.tt"])
        self.assertEqual(acct["errors"], 3)
        self.assertEqual(files["errors"], 3)
        wheres = sorted(p["where"] for p in files["problems"])
        self.assertEqual(wheres, ["dup.tt:2", "foo_base.tt", "x.tt:2"])
        self.assertEqual([f["errors"] for f in files["files"]], [1, 1, 1])
        self.assertEqual([f["file"] for f in files["files"]],
                         ["dup.tt", "foo_base.tt", "x.tt"])

    def test_a_clash_with_another_file_of_the_account_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {
                "margin/a.tt": DUP.splitlines(keepends=True)[0],
                "margin/b.tt": DUP.splitlines(keepends=True)[1]})
            doc_a = CT.check_paths([root / "inputs/margin/a.tt"])
            doc_b = CT.check_paths([root / "inputs/margin/b.tt"])
        # The second opening (b.tt:1) is the one refused; a.tt's check
        # names it too, since it is a.tt's line it clashes with.
        self.assertEqual(doc_b["files"][0]["errors"], 1)
        self.assertEqual(doc_a["files"][0]["errors"], 0)
        self.assertEqual(doc_a["errors"], 1)
        self.assertIn("inputs/margin/a.tt:1", doc_a["problems"][0]["message"])

    def test_read_as_another_account_or_kind_reads_the_lines_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"margin/dup.tt": DUP})
            p = root / "inputs/margin/dup.tt"
            # (each line alone parses: the double opening is a check of
            # the account's lines, made only as the account's file)
            self.assertEqual(CT.check_paths([p], account="other")["errors"],
                             0)
            self.assertEqual(CT.check_paths([p], crypto=True)["errors"], 0)
            self.assertEqual(CT.check_paths([p], crypto=False)["errors"], 1)

    def test_the_command(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"margin/dup.tt": DUP})
            out, _err, code = _run(["-C", d, "check-tt",
                                    str(root / "inputs/margin/dup.tt")])
        self.assertEqual(code, 1, out)
        self.assertIn("dup.tt:2: ZZQY.TO is already opened at unknown cost",
                      out)


class TestFileArgumentFolders(unittest.TestCase):
    """TJ-0033, TJ-0041: a relative FILE was looked for from the shell's
    folder only: the names check-tt prints (inputs/margin/x.tt, in the
    default years layout under ../inputs/ of the year folder) and a
    path relative to `-C DIR` were "no such file"."""

    def test_the_names_check_tt_prints(self):
        with tempfile.TemporaryDirectory() as d:
            _base, year = _years_project(d, {"margin/bad.tt": "BUYSEL x\n"})
            out, _err, code = _run(["check-tt"], cwd=year)
            self.assertIn("inputs/margin/bad.tt", out)
            out, err, code = _run(["check-tt", "inputs/margin/bad.tt"],
                                  cwd=year)
        self.assertEqual(code, 1, err)
        self.assertIn("inputs/margin/bad.tt:1: unknown .tt action", out)

    def test_relative_to_the_project_of_dash_c(self):
        with tempfile.TemporaryDirectory() as d:
            base, _year = _years_project(d, {"margin/bad.tt": "BUYSEL x\n"})
            out, err, code = _run(["-C", "2024", "check-tt",
                                   "../inputs/margin/bad.tt"], cwd=base)
            self.assertEqual(code, 1, err)
            self.assertIn("../inputs/margin/bad.tt:1: unknown .tt action",
                          out)
            # The shell's folder first, as before.
            out, err, code = _run(["-C", "2024", "check-tt",
                                   "inputs/margin/bad.tt"], cwd=base)
            self.assertEqual(code, 1, err)
            self.assertIn("inputs/margin/bad.tt:1:", out)

    def test_a_missing_file_says_where_it_was_looked_for(self):
        with tempfile.TemporaryDirectory() as d:
            _base, year = _years_project(d, {"margin/a.tt": GOOD})
            _out, err, code = _run(["check-tt", "inputs/margin/nope.tt"],
                                   cwd=year)
        self.assertEqual(code, 2)
        self.assertIn("inputs/margin/nope.tt: no such file", err)
        self.assertIn("from the shell's folder, then from the project's",
                      err)


class TestChecklistTtLines(unittest.TestCase):
    """TJ-0032: the checklist's `.tt lines` step only counted the files:
    `[?] ... 1 .tt file(s) in inputs/` while check-tt found errors and
    the last run stopped on one."""

    def _result(self, files):
        from datetime import date
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, files)
            cfg = {"settings": {"year": 2024, "country": "canada"},
                   "accounts": ACCOUNTS}
            ctx = cl.Ctx(root=root, cfg=cfg, year=2024,
                         today=date(2025, 3, 1),
                         run_sub=lambda *a, **k: (0, "", ""))
            return cl.s_tt_lines(ctx, cl._Facts(ctx))

    def test_a_refused_line_is_attention_naming_the_first(self):
        r = self._result({"margin/bad.tt":
                          "BUYSELL 2024-13-05 09:30:00 ZZQA.TO 10 CAD 10 "
                          "101 1\nBUYSEL x\n"})
        self.assertEqual(r.status, "attention")
        self.assertIn("2 .tt problem(s)", r.detail)
        self.assertIn("inputs/margin/bad.tt:1: date '2024-13-05' is not a "
                      "valid YYYY-MM-DD date", r.detail)
        self.assertNotIn("BUYSELL 2024-13-05", r.detail)
        self.assertEqual(r.cmd, "tjs check-tt")

    def test_clean_files_stay_a_step_to_review(self):
        r = self._result({"margin/ok.tt": GOOD})
        self.assertEqual(r.status, "review")
        self.assertIn("1 .tt file(s) in inputs/, every line parses",
                      r.detail)
        self.assertEqual(self._result({}).detail, "")


LS = "\u2028"       # a Unicode line separator, pasted into a comment


class TestEveryTtReaderSplitsAtLineFeeds(unittest.TestCase):
    """The .tt readers split at U+2028 (new; the lead gives it an id):
    the run's readers of the dated events and declarations used
    str.splitlines, which also breaks at U+2028, U+2029, U+0085, a form
    feed, a vertical tab and U+001C-U+001E. The converter (tt_to_json)
    reads such a line as one line, its tail a comment; these readers
    read the tail as a line of its own and BOOKED it — a RENAME, a
    JOURNAL, an `OPENING ... cost=unknown`, an ALLOWLOSS, an INKIND, a
    cash-ledger line — and numbered every later line one more."""

    BUY = "BUYSELL 2024-01-15 09:30:00 ZZQA.TO 10 CAD 10 101 1"

    def test_tt_lines(self):
        self.assertEqual(CV.tt_lines(""), [])
        self.assertEqual(CV.tt_lines("a\nb\n"), ["a", "b"])
        self.assertEqual(CV.tt_lines("a\nb"), ["a", "b"])
        self.assertEqual(CV.tt_lines("a\n\nb\n"), ["a", "", "b"])
        for sep in ("\u2028", "\u2029", "\x85", "\x0c", "\x0b", "\x1c"):
            self.assertEqual(CV.tt_lines(f"a{sep}b\nc\n"), [f"a{sep}b", "c"])

    def _root(self, d, text, acct="margin"):
        root = _project(d, {})
        p = root / "inputs" / acct / "a.tt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return root

    def test_a_rename_in_a_comment_is_not_booked(self):
        from taxjson.lib.dated_events import read_declarations
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, f"{self.BUY} # note{LS}RENAME 2024-02-01 "
                                 f"ZZQA.TO ZZQB.TO\n"
                                 f"# a{LS}b\n"
                                 f"JOURNAL 2024-03-01 ZZQC.TO ZZQC.US 5\n")
            decl = read_declarations(root, ACCOUNTS)
        self.assertEqual(decl.renames, [])
        # The journal on line 3 is named line 3, as the converter does.
        self.assertEqual([j.where for j in decl.journals],
                         ["inputs/margin/a.tt:3"])

    def test_an_unknown_cost_opening_in_a_comment_is_not_read(self):
        from taxjson.lib.missing_history import read_tt_openings
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, f"{self.BUY} # see{LS}OPENING 2023-12-31 "
                                 f"ZZQC.TO 5 cost=unknown\n")
            self.assertEqual(read_tt_openings(root, ["margin"]), [])

    # Each reader's keyword line, valid on a line of its own (the control
    # below proves the reader books it there).
    PAYLOAD = {
        "allowloss": 'ALLOWLOSS 2024-03-01 ZZQA.TO reason="x"',
        "cash": "CASHMOVE 2024-03-01 USD 100 own",
        "inkind": "INKIND 2024-03-01 ZZQA.TO 10 CAD 10",
        "split": "SPLIT 2024-01-05 09:30:00 ZZQA.TO ZZQB.TO 1",
        "sale": "BUYSELL 2024-02-01 09:30:00 ZZQD.TO -1 CAD 1 1",
    }

    def _read(self, text):
        from taxjson.bin.taxjson_run import _inkind_lines
        from taxjson.lib import cash_events, loss_overrides
        from taxjson.lib.brokerages.ib_extractor import _account_tt_links
        from taxjson.lib.corp_actions import read_tt_sales
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, text)
            tt = root / "inputs/margin/a.tt"
            return {
                "allowloss": [p["where"] for p in
                              loss_overrides.read_project(root, ACCOUNTS)],
                "cash": [e["where"] for e in
                         cash_events.read_project_lines(root, ACCOUNTS)[0]],
                "inkind": [ln["date"] for _, ln in
                           _inkind_lines(root, ["margin"])[0]],
                "split": sorted(_account_tt_links([tt]).values()),
                "sale": [s["source"] for s in read_tt_sales([tt])],
            }

    def test_each_reader_books_its_line_on_a_line_of_its_own(self):
        text = "".join(f"{self.BUY}\n{p}\n" for p in self.PAYLOAD.values())
        got = self._read(text)
        for k in self.PAYLOAD:
            with self.subTest(k):
                self.assertEqual(len(got[k]), 1, got[k])

    def test_each_reader_skips_its_line_in_a_comment(self):
        text = "".join(f"{self.BUY} # note{LS}{p}\n"
                       for p in self.PAYLOAD.values())
        got = self._read(text)
        for k in self.PAYLOAD:
            with self.subTest(k):
                self.assertEqual(got[k], [])


if __name__ == "__main__":
    unittest.main()
