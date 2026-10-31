"""`tjs check-tt` (lib/check_tt): every .tt line through the run's
readers, every problem named; the run stops at the first."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import check_tt as CT

GOOD = "BUYSELL 2025-01-15 09:30:00 ZZQ.TO 10 CAD 10 101 1\n"
TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\n'
        '[accounts.coins]\ntype = "taxable"\ncrypto = true\n')


def _project(tmp, files, toml=TOML):
    root = Path(tmp)
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(toml)
    for rel, text in files.items():
        p = root / "inputs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _run(argv):
    """cmd_check_tt in-process: (stdout, stderr, exit code)."""
    from taxjson.bin.taxjson_run import _build_parser
    p, _sub = _build_parser()
    args = p.parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args.func(args)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if isinstance(e.code, str):     # out.fail: the text travels
                err.write(e.code)
    return out.getvalue(), err.getvalue(), code


class TestParseLine(unittest.TestCase):
    def kind(self, line, **kw):
        kw.setdefault("account", "margin")
        kw.setdefault("crypto", False)
        kw.setdefault("equity", True)
        return CT.parse_line(line, "f.tt:1", **kw)

    def test_kinds(self):
        self.assertEqual(self.kind("")[0], CT.KIND_BLANK)
        self.assertEqual(self.kind("# note")[0], CT.KIND_BLANK)
        self.assertEqual(self.kind(GOOD)[0], CT.KIND_ROW)
        self.assertEqual(self.kind("RENAME 2024-02-01 OLDX.TO NEWX.TO")[0],
                         CT.KIND_EVENT)
        self.assertEqual(self.kind("JOURNAL 2024-03-01 ZZQ.TO ZZQ.US 40")[0],
                         CT.KIND_EVENT)
        self.assertEqual(self.kind("OPENING 2023-12-29 ZZB.TO 20 "
                                   "cost=unknown")[0], CT.KIND_EVENT)
        self.assertEqual(self.kind("ACQUIRED 2019-05-10 09:30:00 ZZQ.TO 40 "
                                   "CAD 12.00 480.00 ARRIVED 2024-06-03")[0],
                         CT.KIND_ROW)

    def test_refused_lines_raise_with_the_runs_message(self):
        with self.assertRaises(ValueError) as cm:
            self.kind("BUYSEL 2025-01-15 09:30:00 ZZQ.TO 10 CAD 10 101 1")
        self.assertIn("Did you mean BUYSELL", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            self.kind("BUYSELL 2025-13-15 09:30:00 ZZQ.TO 10 CAD 10 101 1")
        self.assertIn("YYYY-MM-DD", str(cm.exception))

    def test_crypto_account_refuses_opening_and_journal(self):
        with self.assertRaises(ValueError) as cm:
            self.kind("OPENING 2023-12-29 ETH 2 CAD 5000", account="coins",
                      crypto=True, equity=False)
        self.assertIn("crypto account", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            self.kind("JOURNAL 2024-03-01 BTC ETH 1", account="coins",
                      crypto=True, equity=False)
        self.assertIn("crypto account", str(cm.exception))
        # The same lines in a share account are fine.
        self.assertEqual(self.kind("OPENING 2023-12-29 ZZB.TO 2 CAD 50")[0],
                         CT.KIND_ROW)

    def test_warnings_are_captured_not_printed(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            kind, warns = self.kind("BUYSELL 2025-01-15 09:30:00 ZZQ 10 "
                                    "CAD 10 101 1")
        self.assertEqual(kind, CT.KIND_ROW)
        self.assertEqual(err.getvalue(), "")
        self.assertTrue(any("no market suffix" in w for w in warns), warns)
        self.assertFalse(any(w.startswith("warning:") for w in warns))
        # A crypto account: bare symbols are the rule, no warning.
        _k, warns = self.kind("BUYSELL 2025-01-15 09:30:00 BTC 1 CAD 10 11 1",
                              account="coins", crypto=True, equity=False)
        self.assertEqual(warns, [])


class TestCheckFile(unittest.TestCase):
    def test_goes_on_past_the_first_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "a.tt")
            p.write_text(GOOD + "BUYSEL x\n" + GOOD
                         + "BUYSELL 2025-01-15 09:30:00 ZZQ.TO 10 CAD 10 "
                         "100,5 1\n" + "# end\n" + GOOD)
            rep = CT.check_file(p, file="a.tt", account="margin",
                                crypto=False, equity=True)
        self.assertEqual(rep.lines, 6)
        self.assertEqual(rep.rows, 3)
        self.assertEqual(rep.errors, 2)
        self.assertEqual([x.line for x in rep.problems], [2, 4])
        self.assertEqual(rep.problems[0].where, "a.tt:2")
        self.assertFalse(rep.problems[0].message.startswith("a.tt:2"))
        self.assertEqual(rep.problems[0].text, "BUYSEL x")

    def test_last_line_without_line_end_is_a_warning(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "a.tt")
            p.write_text(GOOD.rstrip("\n"))
            rep = CT.check_file(p, file="a.tt", account="margin",
                                crypto=False, equity=True)
        self.assertEqual((rep.errors, rep.warnings), (0, 1))
        self.assertIn("no line end", rep.problems[0].message)

    def test_unreadable_file_is_one_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "a.tt")
            p.write_bytes(b"BUYSELL \xff\n")
            rep = CT.check_file(p, file="a.tt", account="margin",
                                crypto=False, equity=True)
        self.assertEqual(rep.errors, 1)
        self.assertEqual(rep.problems[0].line, 0)


class TestCheckProject(unittest.TestCase):
    def test_accounts_kinds_and_cross_line_checks(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {
                "margin/start.tt": GOOD
                + "OPENING 2023-12-29 ZZB.TO 20 cost=unknown\n"
                + "OPENING 2023-12-29 ZZB.TO 25 cost=unknown\n"
                + "BUYSELL 2025-01-15 09:30:00 BARE 10 CAD 10 101 1\n",
                "coins/coins.tt": "OPENING 2023-12-29 ETH 2 CAD 5000\n"
                + "BUYSELL 2025-01-15 09:30:00 BTC 1 CAD 10 11 1\n",
                "coins/notes.txt": "not a .tt file\n"})
            accounts = {"margin": {"type": "taxable"},
                        "coins": {"type": "taxable", "crypto": True}}
            doc = CT.check_project(root, accounts)
            only = CT.check_project(root, accounts, only=["coins"])
        self.assertEqual([f["file"] for f in doc["files"]],
                         ["inputs/coins/coins.tt", "inputs/margin/start.tt"])
        msgs = {(p["where"], p["severity"]) for p in doc["problems"]}
        self.assertIn(("inputs/coins/coins.tt:1", "error"), msgs)
        # The second opening of one symbol: the run's reader
        # (missing_history.read_tt_openings) names it; counted in its
        # file's row.
        self.assertIn(("inputs/margin/start.tt:3", "error"), msgs)
        self.assertIn(("inputs/margin/start.tt:4", "warning"), msgs)
        self.assertEqual(doc["errors"], 2)
        by_file = {f["file"]: f for f in doc["files"]}
        self.assertEqual(by_file["inputs/margin/start.tt"]["errors"], 1)
        self.assertEqual(by_file["inputs/margin/start.tt"]["events"], 2)
        self.assertEqual(by_file["inputs/coins/coins.tt"]["rows"], 1)
        self.assertEqual([f["file"] for f in only["files"]],
                         ["inputs/coins/coins.tt"])

    def test_refused_file_names(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"margin/x_gains.tt": GOOD,
                                "margin/start.tt": GOOD,
                                "margin/start.TT": GOOD})
            doc = CT.check_project(root, {"margin": {"type": "taxable"}})
        text = "\n".join(p["message"] for p in doc["problems"])
        self.assertIn("may not end in '_gains'", text)
        self.assertIn("same work file", text)
        self.assertEqual(doc["warnings"], 0)

    def test_file_mode_reads_the_kind_from_the_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {
                "coins/c.tt": "OPENING 2023-12-29 ETH 2 CAD 5000\n",
                "margin/m.tt": "BUYSELL 2025-01-15 09:30:00 BARE 10 CAD "
                               "10 101 1\n"})
            doc = CT.check_paths([root / "inputs/coins/c.tt",
                                  root / "inputs/margin/m.tt"])
            as_share = CT.check_paths([root / "inputs/coins/c.tt"],
                                      crypto=False)
            lone = Path(d, "lone.tt")
            lone.write_text("BUYSELL 2025-01-15 09:30:00 BARE 10 CAD 10 "
                            "101 1\n")
            outside = CT.check_paths([lone])
            equity = CT.check_paths([lone], crypto=False)
        self.assertEqual((doc["errors"], doc["warnings"]), (1, 1))
        self.assertEqual(doc["files"][0]["account"], "coins")
        self.assertEqual(as_share["errors"], 0)
        # No project: the kind is unknown, so no bare-symbol warning.
        self.assertEqual((outside["errors"], outside["warnings"]), (0, 0))
        self.assertEqual(equity["warnings"], 1)

    def test_shared_layout_year_folder_beside_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "2025").mkdir()
            (base / "2025" / "taxjson.toml").write_text(TOML)
            p = base / "inputs" / "coins" / "c.tt"
            p.parent.mkdir(parents=True)
            p.write_text("OPENING 2023-12-29 ETH 2 CAD 5000\n")
            doc = CT.check_paths([p])
        self.assertEqual(doc["errors"], 1)


class TestRender(unittest.TestCase):
    def test_clean_and_problems(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"margin/a.tt": GOOD})
            clean = CT.check_project(root, {"margin": {}})
            root2 = _project(Path(d, "p2"), {"margin/a.tt": "BUYSEL x\n"
                                                             + GOOD})
            bad = CT.check_project(root2, {"margin": {}})
        text = "\n".join(CT.render(clean))
        self.assertIn("CHECK TT — 1 line in 1 file", text)
        self.assertIn("Every line parses.", text)
        self.assertRegex(text, r"(?m)^  inputs/margin/a\.tt +margin +1 +1 +0 +ok$")
        text = "\n".join(CT.render(bad))
        self.assertIn("ERRORS — lines the run refuses (1)", text)
        self.assertIn("- inputs/margin/a.tt:1: unknown .tt action", text)
        self.assertRegex(text, r"(?m)^  inputs/margin/a\.tt +margin +2 +1 +0 +1 error$")
        self.assertIn("! 1 problem the run refuses; fix it before "
                      "`tjs run`", text)
        empty = CT.render({"files": [], "problems": [], "lines": 0,
                           "rows": 0, "events": 0, "errors": 0,
                           "warnings": 0})
        self.assertIn("No .tt file to check.", "\n".join(empty))


class TestCommand(unittest.TestCase):
    def test_project_account_and_file_items(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, {"margin/a.tt": GOOD,
                                "coins/c.tt": "BUYSEL x\n"})
            out, _err, code = _run(["-C", d, "check-tt"])
            self.assertEqual(code, 1, out)
            self.assertIn("inputs/coins/c.tt:1", out)
            out, _err, code = _run(["-C", d, "check-tt", "margin"])
            self.assertEqual(code, 0, out)
            self.assertIn("Every line parses.", out)
            out, _err, code = _run(["-C", d, "check-tt", "margin",
                                    str(root / "inputs/coins/c.tt")])
            self.assertEqual(code, 1, out)
            self.assertIn("2 files", out)
            out, _err, code = _run(["-C", d, "check-tt", "--json"])
            doc = json.loads(out)
            self.assertEqual(code, 1)
            self.assertEqual(doc["errors"], 1)
            self.assertEqual(sorted(doc), ["errors", "events", "files",
                                           "lines", "problems", "rows",
                                           "warnings"])
            _out, err, code = _run(["-C", d, "check-tt", "nope"])
            self.assertEqual(code, 2)
            self.assertIn("no account 'nope'", err)
            _out, err, code = _run(["-C", d, "check-tt", "missing.tt"])
            self.assertEqual(code, 2)
            self.assertIn("no such file", err)

    def test_a_file_needs_no_project(self):
        from taxjson.bin.taxjson_run import _check_tt_files_only
        import argparse
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "x.tt")
            p.write_text(GOOD)
            out, _err, code = _run(["-C", d, "check-tt", str(p)])
            self.assertEqual(code, 0, out)
            self.assertIn("Every line parses.", out)
            _out, err, code = _run(["-C", d, "check-tt"])
            self.assertEqual(code, 1)
            self.assertIn("not a taxjson project", err)
        self.assertTrue(_check_tt_files_only(argparse.Namespace(
            cmd="check-tt", items=["a.tt", "dir/b"])))
        self.assertFalse(_check_tt_files_only(argparse.Namespace(
            cmd="check-tt", items=["margin", "a.tt"])))
        self.assertFalse(_check_tt_files_only(argparse.Namespace(
            cmd="check-tt", items=[])))


if __name__ == "__main__":
    unittest.main()
