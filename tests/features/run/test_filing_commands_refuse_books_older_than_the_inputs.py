"""close-year, check-filed and form-export refuse books a full `tjs run` of
the inputs as they are now would change: inputs changed since the last full
run, an `--account` run that left the pass across accounts behind, no record
of the last full run or one another taxjson wrote (TJ-4305, TJ-4309,
TJ-4310), and in a US project a filed-year lock removed, replaced or closed
from other books since (TJ-4308, TJ-4311). Synthetic data only."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
from _support.filing_run_cli import _cli
from _support.tt_cash_project import _tt_project


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestFilingCommandsRefuseBooksOlderThanTheInputs(unittest.TestCase):
    """TJ-4305: close-year, check-filed and form-export read the books as
    the last full run left them, so each refuses books older than the
    inputs (the last full run's record of them, the rule run-clean reads),
    exit 1, naming `tjs run`, before anything is printed or written; and
    books an `--account` run left behind the pass across accounts (the
    wash-adjusted gains older than a book they read), which close-year
    refused already (its STALE refusal, pinned here). `--force` passes
    neither. Before, check-filed and form-export only warned, and
    close-year --force locked changed inputs' books."""

    _EDIT = ("BUYSELL 2025-04-10 10:00:00 QQQ.TO 10 CAD 1.00 -10.00 "
             "0.00\n")

    def _closed(self, td):
        root = Path(td)
        _tt_project(root)
        self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
        r = _cli(root, "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr[-1000:])
        return root, (root / "filed" / "2025.json").read_bytes()

    def _refused(self, root, lock, said, close_said=None):
        for argv in (("form-export",), ("check-filed",),
                     ("close-year", "--yes", "--force")):
            with self.subTest(argv=argv):
                r = _cli(root, *argv)
                self.assertEqual(r.returncode, 1, r.stdout[-500:])
                self.assertNotIn("SCHEDULE 3", r.stdout)
                self.assertNotIn("FILED-YEAR LOCK CHECK", r.stdout)
                self.assertIn(close_said if argv[0] == "close-year" and close_said
                              else said, r.stderr)
                self.assertIn("`tjs run`", r.stderr)
        self.assertEqual((root / "filed" / "2025.json").read_bytes(), lock)

    def test_an_input_changed_since_the_last_full_run(self):
        with tempfile.TemporaryDirectory() as td:
            root, lock = self._closed(td)
            (root / "inputs" / "margin" / "m2.tt").write_text(self._EDIT)
            self._refused(root, lock, "the books in work/ are older than the "
                                      "inputs (added: inputs/margin/m2.tt)")
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            for cmd in ("form-export", "check-filed"):     # a buy: no drift
                r = _cli(root, cmd)
                self.assertEqual(r.returncode, 0, (cmd, r.stderr[-500:]))

    def test_an_account_run_left_the_pass_across_accounts_behind(self):
        with tempfile.TemporaryDirectory() as td:
            root, lock = self._closed(td)
            m = root / "inputs" / "margin" / "m.tt"
            m.write_text(m.read_text() + self._EDIT)
            self.assertEqual(_cli(root, "run", "--account", "margin",
                                  "--no-input").returncode, 0)
            # The scoped run dropped the last full run's record (TJ-4356):
            # close-year names the stale pass first (its --force never passed
            # it), the others the record (TJ-4309).
            self.assertFalse((root / "work" / ".inputs_fingerprint.json").exists())
            self._refused(root, lock, _NO_RECORD, close_said="are STALE")
            # The edit taken back, the inputs are the last full run's again:
            # still refused, the books are the scoped run's (TJ-4356: before,
            # the record vouched for them).
            m.write_text(m.read_text().replace(self._EDIT, ""))
            self._refused(root, lock, _NO_RECORD, close_said="are STALE")

    def test_no_record_of_the_last_full_run(self):
        # TJ-4309: every full run writes the record; without it nothing says
        # what the books were built from. Before, the three passed.
        with tempfile.TemporaryDirectory() as td:
            root, lock = self._closed(td)
            (root / "work" / ".inputs_fingerprint.json").unlink()
            self._refused(root, lock, _NO_RECORD)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            self.assertEqual(_cli(root, "form-export").returncode, 0)

    def test_books_another_taxjson_built(self):
        # TJ-4310: the record names the code that built the books (its
        # package fingerprint); another's, or none (an older taxjson wrote the
        # record), is refused until a run builds them with this one.
        with tempfile.TemporaryDirectory() as td:
            root, lock = self._closed(td)
            rec = root / "work" / ".inputs_fingerprint.json"
            doc = json.loads(rec.read_text(encoding="utf-8"))
            self.assertEqual(len(doc["code"]), 64)
            for code in ("0" * 64, None):
                with self.subTest(code=code):
                    rec.write_text(json.dumps(dict(
                        {k: v for k, v in doc.items() if k != "code"},
                        **({"code": code} if code else {}))), encoding="utf-8")
                    self._refused(root, lock, "the books in work/ were built by another "
                                              "taxjson (the last full run's record names "
                                              "other code)")
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            self.assertEqual(_cli(root, "form-export").returncode, 0)


_NO_RECORD = ("no full `tjs run` recorded what the books in work/ were built from "
              "(work/.inputs_fingerprint.json)")


def _us_project(root):
    """margin buys 100 WSHQ.US in 2024-11; margin2 buys 100 on 2024-12-16 and
    sells them on 12-20 for a gain of 100 (synthetic)."""
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\ncountry = "usa"\nyear = 2024\nbase_currency = "USD"\n'
        'source_currencies = []\n\n[accounts.margin]\ntype = "taxable"\n\n'
        '[accounts.margin2]\ntype = "taxable"\n')
    (root / "ticker.map").write_text("")
    for a, text in (("margin", "BUYSELL 2024-11-01 09:30:00 WSHQ.US 100 USD 50.00 5000.00 0\n"),
                    ("margin2", "BUYSELL 2024-12-16 09:30:00 WSHQ.US 100 USD 40.00 4000.00 0\n"
                                "BUYSELL 2024-12-20 09:30:00 WSHQ.US -100 USD 41.00 4100.00 0\n")):
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "t.tt").write_text(text)
    return root


def _margin2_2024(root):
    return json.loads((root / "work" / "margin2_gains_wash.json").read_text(
        encoding="utf-8"))["summary"]["total_gain"]


class TestALockSinceTheRunIsNoChangeOnlyFromTheseBooks(unittest.TestCase):
    """TJ-4308 (US-WASH-22, TJ-0463): a US gains run reads the filed-year
    locks, their rows too, so a lock that changed since the last full run
    moves figures a run would change. One closed from these books since (its
    frozen record holds the inputs that run read and the hashes of the books
    now: TJ-4311), of the year a filing command reads or a later one, is no
    change: check-filed after close-year, a re-close. A lock removed,
    replaced, put back after a run without it, or closed from other books
    is. Before, any lock of that year or later added or removed passed:
    with 2024's lock deleted, form-export printed margin2's 100 where a run
    gives -900 (margin's 2025-01-06 loss is washed by margin2's 12-16 buy,
    its add booked in 2025 while 2024 was locked), and close-year --force
    locked the 100."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = _us_project(Path(cls._tmp.name) / "base")
        for argv in (("run", "--no-input"), ("close-year", "--yes")):
            r = _cli(cls.base, *argv)
            assert r.returncode == 0, (argv, r.stderr[-1500:])
        with (cls.base / "inputs" / "margin" / "t.tt").open("a") as f:
            f.write("BUYSELL 2025-01-06 09:30:00 WSHQ.US -100 USD 40.00 4000.00 0\n")
        r = _cli(cls.base, "run", "--no-input")
        assert r.returncode == 0, r.stderr[-1500:]
        cls.lock = (cls.base / "filed" / "2024.json").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name) / "p"
        shutil.copytree(self.base, self.root, symlinks=True)
        self.filed = self.root / "filed" / "2024.json"

    def _remove_lock(self):
        self.filed.unlink()
        shutil.rmtree(self.root / "filed" / "2024", ignore_errors=True)

    def _refused(self, said, argvs=(("form-export",), ("close-year", "--yes", "--force"))):
        lock = self.filed.read_bytes() if self.filed.exists() else None
        for argv in argvs:
            with self.subTest(argv=argv):
                r = _cli(self.root, *argv)
                self.assertEqual(r.returncode, 1, r.stdout[-500:])
                self.assertIn(f"the books in work/ are older than the inputs ({said}: "
                              f"filed/ (locked years))", r.stderr)
                self.assertIn("`tjs run`", r.stderr)
        self.assertEqual(self.filed.read_bytes() if self.filed.exists() else None, lock)

    @rule(R.US_WASH_22)
    def test_a_lock_removed(self):
        self.assertEqual(_margin2_2024(self.root), 100.0)
        self._remove_lock()
        self._refused("removed")
        self.assertEqual(_cli(self.root, "run", "--no-input").returncode, 0)
        self.assertEqual(_margin2_2024(self.root), -900.0)       # what the refusal kept out

    def test_a_lock_replaced(self):
        doc = json.loads(self.lock)
        doc["closed_at"] = "2025-04-01T12:00:00"
        self.filed.write_text(json.dumps(doc), encoding="utf-8")
        self._refused("changed", argvs=(("form-export",), ("check-filed",),
                                        ("close-year", "--yes", "--force")))

    def test_a_lock_put_back(self):
        # Put back with no run between: the books were built with it.
        self._remove_lock()
        self.filed.write_bytes(self.lock)
        for argv in (("form-export",), ("check-filed",)):
            r = _cli(self.root, *argv)
            self.assertEqual(r.returncode, 0, (argv, r.stderr[-500:]))
        # Put back after a run without it: closed from other inputs (before
        # the 2025 sale), so a run with it moves the books.
        self._remove_lock()
        self.assertEqual(_cli(self.root, "run", "--no-input").returncode, 0)
        self.filed.write_bytes(self.lock)
        self._refused("added")

    def _re_closed(self):
        """The lock re-closed from the books built with it: the inputs of
        now, 2024's rows at 100 (2025's add booked in 2025)."""
        r = _cli(self.root, "close-year", "--yes", "--force")
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertEqual(_cli(self.root, "check-filed").returncode, 0)
        return self.filed.read_bytes()

    def _run_without_the_lock(self):
        self._remove_lock()
        self.assertEqual(_cli(self.root, "run", "--no-input").returncode, 0)
        self.assertEqual(_margin2_2024(self.root), -900.0)

    @rule(R.US_WASH_22)
    def test_a_lock_closed_from_other_books_put_back(self):
        # TJ-4311: its frozen inputs are the last run's, its books are not
        # (closed while the lock it replaced was in place). Before, both
        # commands passed and read -900 where a run gives 100.
        lock = self._re_closed()
        self._run_without_the_lock()
        self.filed.write_bytes(lock)
        self._refused("added", argvs=(("form-export",), ("check-filed",),
                                      ("close-year", "--yes", "--force")))
        self.assertEqual(_cli(self.root, "run", "--no-input").returncode, 0)
        self.assertEqual(_margin2_2024(self.root), 100.0)        # what the refusal kept out

    def test_it_replacing_a_lock_closed_with_none_present(self):
        lock = self._re_closed()
        self._run_without_the_lock()
        for argv in (("close-year", "--yes"), ("check-filed",), ("form-export",)):
            r = _cli(self.root, *argv)
            self.assertEqual(r.returncode, 0, (argv, r.stderr[-800:]))
        self.filed.write_bytes(lock)
        self._refused("added", argvs=(("form-export",), ("check-filed",)))

    def test_close_year_then_check_filed_and_a_re_close(self):
        root = _us_project(Path(self.td.name) / "fresh")
        for argv in (("run", "--no-input"), ("close-year", "--yes"), ("check-filed",),
                     ("form-export",), ("close-year", "--yes", "--force"), ("check-filed",),
                     ("run", "--no-input"), ("close-year", "--yes", "--force"),
                     ("check-filed",)):
            r = _cli(root, *argv)
            self.assertEqual(r.returncode, 0, (argv, r.stderr[-800:]))


if __name__ == "__main__":
    unittest.main()
