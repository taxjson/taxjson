"""The golden ledgers (tests/_golden.py): whole synthetic books, every output
but the stages' intermediate files, against what they produced when recorded.
A refactor leaves them alone. One test per book, so a failure names it. The
run's part here; a book's views are tests/test_golden_views.py, the same books
a thousand days later tests/test_golden_clock.py."""
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _golden
import _style

# The books the refactor plan relies on. A book cannot quietly go missing.
REQUIRED = {"demo", "style-canada", "style-usa", "options", "rbc-options", "crypto", "evidence",
            "fuzz-conservation", "fuzz-transfer", "fuzz-straddle"}


class TestGoldenLedgers(unittest.TestCase):
    def test_every_book_is_there(self):
        self.assertEqual(set(_golden.books()), REQUIRED)


def _case(book):
    def test(self):
        problems = _golden.compare(book, only="run")
        self.assertFalse(problems, _golden.report(book, problems))
    return test


for _book in _golden.books():
    setattr(TestGoldenLedgers, "test_" + _book.replace("-", "_"), _case(_book))


class TestTheComparison(unittest.TestCase):
    """Loose enough for a float's last digits, and for nothing else."""

    def test_float_noise_and_nothing_more(self):
        self.assertEqual(_golden.diff({"a": 1.1 + 2.2}, {"a": 3.3}), [])
        self.assertEqual(_golden.diff({"a": 1}, {"a": 1.0}), [])
        self.assertEqual(_golden.diff(5e-14, 0.0), [])
        self.assertTrue(_golden.diff({"a": 1.0}, {"a": 1.001}))

    def test_the_same_move_gets_the_same_answer_at_any_size(self):
        for a, b in ((1.35, 1.3501), (0.0891, 0.0892), (1000000.25, 1000000.2501)):
            self.assertTrue(_golden.diff(a, b), (a, b))

    def test_a_tiny_value_is_compared(self):
        self.assertTrue(_golden.diff(0.00005, 0.00009))
        self.assertTrue(_golden.diff(0.00000001, 0.00000002))   # a satoshi

    def test_a_satoshi_counts_on_every_figure_the_books_hold(self):
        for a, b in ((58704.9525, 58704.95250001), (20000.5, 20000.50000001),
                     (9000.5, 9000.50000001)):
            self.assertTrue(_golden.diff(a, b), (a, b))

    def test_inf_and_nan_equal_themselves(self):
        inf, nan = float("inf"), float("nan")
        self.assertEqual(_golden.diff(inf, inf), [])
        self.assertEqual(_golden.diff(-inf, -inf), [])
        self.assertEqual(_golden.diff(nan, nan), [])
        self.assertTrue(_golden.diff(inf, -inf))
        self.assertTrue(_golden.diff(inf, 1.0))
        self.assertTrue(_golden.diff(nan, 1.0))
        self.assertEqual(_golden.diff("Win/Loss: inf", "Win/Loss: inf"), [])

    def test_a_string_that_is_only_a_number_compares_by_value(self):
        self.assertEqual(_golden.diff("12.50", "12.5000"), [])
        self.assertTrue(_golden.diff("12.50", "12.60"))

    def _text(self, was, now):
        return _golden.diff(_golden.parsed("x.txt", was), _golden.parsed("x.txt", now))

    def test_a_line_of_text_compares_exactly(self):
        for was, now in (("gain 1.50 CAD", "loss 1.50 CAD"), ("gain 1.50 CAD", "gain 1.60 CAD"),
                         ("100      x", "     100 x"), ("ids 1,2", "ids 12")):
            self.assertTrue(self._text(was, now), (was, now))
        self.assertEqual(self._text("gain 1.50 CAD\n", "gain 1.50 CAD\n"), [])

    def test_how_a_number_is_printed_counts(self):
        for was, now in (("acct 007", "acct 7"), ("x +5.00", "x 5.00"), ("gain 1.50", "gain 1.5"),
                         ("on 2024-01-15", "on 2024-1-15"), ("12.50", "12.5"),
                         ("gain 1,234.50 CAD", "gain 1234.50 CAD")):
            self.assertTrue(self._text(was, now), (was, now))
        # In a JSON string with words around it too.
        self.assertTrue(_golden.diff({"a": "gain 1.50"}, {"a": "gain 1.5"}))

    def test_a_chain_of_comma_separated_fields_is_not_one_number(self):
        for was, now in (("100,200,300,400,500,600", "100,200,300,400,500,601"),
                         ("100,200,300,400.50", "100,200,300,400.55"),
                         ("total 12,345,678,901.23", "total 12,345,678,901.24")):
            self.assertTrue(self._text(was, now), (was, now))
            self.assertTrue(_golden.diff({"a": was}, {"a": now}), (was, now))

    def test_blank_lines_and_trailing_spaces_count(self):
        self.assertTrue(self._text("a\n\nb\n", "a\nb\n"))
        self.assertTrue(self._text("a  \n", "a\n"))

    def test_a_text_difference_names_the_line(self):
        self.assertEqual(self._text("a\nb\n", "a\nc\n"), ["[1]: 'b', now 'c'"])
        self.assertIn("2 lines, now 3", self._text("a\nb\n", "a\nb\nc\n")[0])

    def test_structure_counts(self):
        self.assertTrue(_golden.diff({"a": 1}, {"a": 1, "b": 2}))
        self.assertTrue(_golden.diff({"a": 1, "b": 2}, {"a": 1}))
        self.assertTrue(_golden.diff([1, 2], [1, 2, 3]))
        self.assertTrue(_golden.diff([1, 2], [2, 1]))
        self.assertTrue(_golden.diff(True, 1))
        self.assertTrue(_golden.diff(None, 0))

    def test_only_the_clock_is_masked(self):
        for was, now in (
                ("generated_at = 2000-01-01T00:00:00Z\n", "generated_at = 2026-11-03T13:26:31Z\n"),
                ('{"generated_at": "2026-11-03T11:43:08"}', '{"generated_at": "2027-12-08T11:43:08"}'),
                ('{"as_of_date": "2026-11-03"}', '{"as_of_date": "2027-12-08"}'),
                ("Generated:  2026-11-03T16:43:09Z\n", "Generated:  2027-12-08T16:43:09Z\n"),
                ("# generated     2026-11-03 11:43\n", "# generated     2027-12-08 11:43\n"),
                ("WASH RADAR — x (s.54), as of 2026-11-03, positions\n",
                 "WASH RADAR — x (s.54), as of 2027-12-08, positions\n"),
                ("FX USD→CAD: Bank of Canada Valet for 1052 dates\n",
                 "FX USD→CAD: Bank of Canada Valet for 1452 dates\n"),
                ('{"closed_at": "2026-11-10T05:26:07"}', '{"closed_at": "2029-08-06T05:26:07"}'),
                ("V042\nAtaxjson\nD11/10/2026\n^\n", "V042\nAtaxjson\nD08/06/2029\n^\n")):
            self.assertEqual(_golden._masked(was), _golden._masked(now), was)
        for was, now in (('{"total_gain": "1.00"}', '{"total_gain": "2.00"}'),
                         ("as of 2026-11-03\n", "as of 2027-12-08\n"),
                         # A TXF record's dates are the trade's, not the clock's.
                         ("V042\nAtaxjson\nD<stamp>\n^\nTD\nD02/12/2024\n",
                          "V042\nAtaxjson\nD<stamp>\n^\nTD\nD02/13/2024\n"),
                         ("TD\nD02/12/2024\n", "TD\nD02/13/2024\n")):
            self.assertNotEqual(_golden._masked(was), _golden._masked(now), was)

    def test_a_locks_build_is_masked_and_its_figures_are_not(self):
        lock = ('{\n  "taxjson": {\n    "commit": "%s",\n    "modified": %s,\n    "release": null,\n'
                '    "version": "%s"\n  },\n  "totals": {\n    "gain": %s\n  }\n}\n')
        self.assertEqual(_golden._masked(lock % ("eb072d21", "true", "0.41.0", "1.5")),
                         _golden._masked(lock % ("085141f2", "false", "0.42.0", "1.5")))
        self.assertNotEqual(_golden._masked(lock % ("eb072d21", "true", "0.41.0", "1.5")),
                            _golden._masked(lock % ("eb072d21", "true", "0.41.0", "2.5")))
        # The record's keys still compare (TJ-2776).
        self.assertEqual(json.loads(_golden._masked(lock % ("eb072d21", "true", "0.41.0", "1.5")))["taxjson"],
                         {"commit": "<build>", "modified": "<build>", "release": "<build>", "version": "<build>"})

    def test_a_locks_input_digest_is_masked_and_its_file_name_is_not(self):
        lock = '{"inputs": {"inputs/crypto/%s.csv": "%s"}}'
        self.assertEqual(_golden._masked(lock % ("coinbase_demo", "ab" * 32)),
                         _golden._masked(lock % ("coinbase_demo", "cd" * 32)))
        self.assertNotEqual(_golden._masked(lock % ("coinbase_demo", "ab" * 32)),
                            _golden._masked(lock % ("kraken_demo", "ab" * 32)))
        self.assertEqual(_golden._masked('{"id": "abc123"}'), '{"id": "abc123"}')

    def test_a_traces_row_hash_is_masked_and_a_broker_id_is_not(self):
        self.assertEqual(_golden._masked("account=margin   id=0123456789abcdef"),
                         "account=margin   id=<hash>")
        self.assertEqual(_golden._masked("id=CB-DEMO-1234"), "id=CB-DEMO-1234")

    def test_the_longer_spelling_of_a_folder_goes_first(self):
        for order in (("/var/t/p", "/private/var/t/p"), ("/private/var/t/p", "/var/t/p")):
            self.assertEqual(_golden._masked("/private/var/t/p/x", *order), "<project>/x", order)

    def test_the_folder_is_masked(self):
        self.assertEqual(_golden._masked("at /tmp/x/p/work", "/tmp/x/p"), "at <project>/work")


class TestWhatIsCompared(unittest.TestCase):
    def test_work_keeps_the_answers_and_leaves_the_stages(self):
        for rel in ("work/margin_gains.json", "2024/work/margin_gains_wash.json",
                    "work/margin_report.json", "work/margin_gains.traces"):
            self.assertTrue(_golden._compared(rel), rel)
        for rel in ("work/margin_raw.json", "work/.blend_base.json", "2024/work/x.diag",
                    "work/margin_raw_gains.json.diag"):
            self.assertFalse(_golden._compared(rel), rel)

    def test_everything_outside_work_is_compared(self):
        for rel in ("reports/fees.rpt", "reports/run_summary.json", "exports/README.txt",
                    "2024/reports/wash_radar_margin.json", "inputs/crypto/crypto_sends.tt"):
            self.assertTrue(_golden._compared(rel), rel)

    def test_a_named_report_is_really_recorded(self):
        names = {p.relative_to(_golden.GOLDEN / "demo" / "expected").as_posix()
                 for p in (_golden.GOLDEN / "demo" / "expected").rglob("*") if p.is_file()}
        for rel in ("_run/stdout.txt", "2024/reports/run_summary.json", "2024/reports/fees.rpt",
                    "2024/reports/wash_radar_margin.json", "2024/work/margin_report.json"):
            self.assertIn(rel, names)


class TestWhatARunDoes(unittest.TestCase):
    """A book's run, with something done to the project just after it."""

    @staticmethod
    def _compare_after(change):
        real = subprocess.run

        def run(argv, **kw):
            r = real(argv, **kw)
            change(Path(kw["cwd"]))
            return r
        with mock.patch.object(_golden.subprocess, "run", run):
            return _golden.compare("options")

    def test_control(self):
        self.assertEqual(self._compare_after(lambda cwd: None), [])

    def test_a_deleted_project_file_is_a_difference_and_named(self):
        problems = self._compare_after(lambda cwd: (cwd / "taxjson.toml").unlink())
        self.assertEqual(problems, ["_run/removed/taxjson.toml: new output, not recorded"])

    def test_a_binary_output_is_a_named_difference(self):
        problems = self._compare_after(lambda cwd: (cwd / "reports" / "x.bin").write_bytes(b"PK\xff\xfe\x00"))
        self.assertEqual(problems, ["reports/x.bin: new output, not recorded"])

    def test_a_changed_binary_is_a_difference(self):
        self.assertEqual(_golden._decoded(b"ok"), "ok")
        self.assertNotEqual(_golden._decoded(b"\xff1"), _golden._decoded(b"\xff2"))

    def test_json_that_no_longer_parses_is_a_named_difference(self):
        problems = self._compare_after(
            lambda cwd: (cwd / "reports" / "run_summary.json").write_text("{not json"))
        self.assertEqual(len(problems), 1)
        self.assertIn("reports/run_summary.json: does not parse", problems[0])


class TestWhatABookHolds(unittest.TestCase):
    def test_no_project_holds_what_a_run_writes(self):
        """A file in project/ that expected/ records as a run's output would be
        copied in, and the run would start from it."""
        for book in _golden.books():
            if _golden.meta(book)["kind"] != "project":
                continue
            expected = _golden.GOLDEN / book / "expected"
            for p in expected.rglob("*"):
                rel = p.relative_to(expected).as_posix()
                if p.is_file() and not rel.startswith("_run/"):
                    self.assertFalse((_golden.GOLDEN / book / "project" / rel).exists(), (book, rel))

    def test_no_recorded_comment_holds_an_amount_the_push_gate_refuses(self):
        """check-pii's --diff reads what follows a # in an added line as a comment,
        and refuses a money amount there. Only the push gate reads the diff, so
        without this a re-record passes the whole gate and fails on landing."""
        comment = re.compile(r"(^|[ \t])(#|//|/\*|<!--)")
        amount = re.compile(r"(^|[^0-9,.])[0-9]{1,3}(,[0-9]{3})+[.][0-9]{2}([^0-9]|$)")
        hits = []
        for p in _golden.GOLDEN.rglob("*"):
            if p.is_file() and p.suffix not in (".md", ".rst", ".markdown"):
                for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    found = comment.search(line)
                    if found and amount.search(line[found.start():]):
                        hits.append(f"{p.relative_to(_golden.GOLDEN)}:{n}")
        self.assertEqual(hits, [], "lower a price in that book's inputs and re-record")

    def test_the_generated_list_covers_what_the_books_record_outside_the_folders(self):
        names = {p.name for book in _golden.books()
                 for p in (_golden.GOLDEN / book / "expected" / "inputs").rglob("*") if p.is_file()}
        self.assertLessEqual(names, set(_golden._GENERATED))

    def test_an_engine_book_records_each_countrys_warnings(self):
        expected = _golden.GOLDEN / "fuzz-transfer" / "expected"
        self.assertTrue((expected / "canada.stderr.txt").read_text())
        self.assertTrue((expected / "usa.stderr.txt").read_text())

    def test_an_engines_warning_is_compared(self):
        real = _golden.get_tax_rules

        def noisy(country):
            rules = mock.Mock()

            def run(*a, **kw):
                print("warning: a new one", file=_golden.sys.stderr)
                return real(country).compute_gains(*a, **kw)
            rules.compute_gains = run
            return rules
        with mock.patch.object(_golden, "get_tax_rules", noisy):
            problems = _golden.compare("fuzz-straddle")
        self.assertTrue(any("stderr.txt" in p for p in problems), problems)


class TestABookThatProtectsNothing(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.golden = Path(d.name)
        patcher = mock.patch.object(_golden, "GOLDEN", self.golden)
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.golden / "b" / "expected").mkdir(parents=True)
        (self.golden / "b" / "book.json").write_text('{"kind": "project"}')

    def test_an_empty_expected_fails(self):
        with mock.patch.object(_golden, "produce", return_value={}):
            self.assertIn("protects nothing", _golden.compare("b")[0])

    def test_recording_nothing_is_refused_and_leaves_expected_alone(self):
        kept = self.golden / "b" / "expected" / "work" / "x_gains.json"
        kept.parent.mkdir()
        kept.write_text("{}")
        with mock.patch.object(_golden, "produce", return_value={"reports/x.sum": "t"}):
            with self.assertRaisesRegex(SystemExit, "refusing to record"):
                _golden.update("b")
        self.assertTrue(kept.exists())

    def test_an_engine_book_needs_both_countries(self):
        (self.golden / "b" / "book.json").write_text('{"kind": "engine"}')
        self.assertEqual(_golden._ledger_missing("b", ["canada.json", "usa.json"]), "")
        self.assertIn("usa.json", _golden._ledger_missing("b", ["canada.json"]))


class TestACheckoutsModes(unittest.TestCase):
    def test_a_copied_project_is_private_at_every_level(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            (root / "inputs").mkdir(parents=True)
            (root / "inputs" / "a.csv").write_text("x")
            for p in (root, root / "inputs", root / "inputs" / "a.csv"):
                p.chmod(0o775 if p.is_dir() else 0o664)
            _golden._make_private(root)
            for p in (root, root / "inputs", root / "inputs" / "a.csv"):
                self.assertEqual(p.stat().st_mode & 0o077, 0, p)


class TestThisCheckout(unittest.TestCase):
    def test_the_engine_is_this_checkouts(self):
        import taxjson
        self.assertEqual(Path(taxjson.__file__).resolve().parents[1], _golden.REPO / "src")

    def test_no_empty_entry_on_the_child_path(self):
        with mock.patch.dict("os.environ"):
            os.environ.pop("PYTHONPATH", None)
            self.assertNotIn("", _style.env()["PYTHONPATH"].split(os.pathsep))
            os.environ["PYTHONPATH"] = os.pathsep + "/x" + os.pathsep
            self.assertEqual(_style.env()["PYTHONPATH"].split(os.pathsep)[1:], ["/x"])


if __name__ == "__main__":
    unittest.main()
