"""Backlog fix round 5 of 2026-10-29, group `commands`: messages and exit
codes of sanity, sum, estimate and wash-sales.

TJ-0639  sanity says an account of taxjson.toml with no inputs has no
         books to compare (its inputs folder named), not "not an
         account"; one with inputs and no books, that its run did not
         build them.
TJ-0640  sanity's "none of the holdings files could be checked — fix
         the paths" and "none of the snapshots could be compared" exit
         2 (an input missing, or one no account claims), not 1, the
         verdict "positions differ".
TJ-0641  sum / estimate (and plot-tax, and the estimate amt and
         instalments relay) name a damaged work/ gains file once, as
         work/<file>, and exit 2.
TJ-0642  wash-sales names work/ from the project ("no gains files in
         work/", "no base files in work/") and exits 2.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*), account names (margin, rrsp, zempty), amounts
under 1,000.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from test_fix_1029_r3_commands import _gains_file, _run_project, _tjs

_RRSP = "BUYSELL 2025-01-07 10:00:00 ZZR.TO 2 CAD 50 100 0\n"


def _flat(text: str) -> str:
    return " ".join(text.split())


# ---- TJ-0639: an account with no inputs has no books to compare ----------
class TestTJ0639SanityAccountWithNoInputs(unittest.TestCase):
    """TJ-0639: [accounts.zempty] with `holdings = [...]` and no
    inputs/zempty/ (so no books) stopped `tjs sanity` with
    "taxjson.toml: 'zempty' is not an account (have: …)". It is one:
    the refusal now says it has no inputs, naming the folder, so there
    is nothing to compare its holdings with (exit 2, an input missing)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _run_project(Path(cls._td.name))
        cls.h = Path(cls._td.name) / "pos.toml"
        cls.h.write_text('[[holding]]\nsymbol = "ZZQ.TO"\nquantity = 1\n')

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _with_zempty(self, td, holdings=True) -> Path:
        root = Path(td) / "p"
        shutil.copytree(self.root, root)
        with open(root / "taxjson.toml", "a", encoding="utf-8") as fh:
            fh.write('[accounts.zempty]\ntype = "taxable"\n'
                     + (f'holdings = ["{self.h}"]\n' if holdings else ""))
        return root

    def test_no_arguments_names_its_inputs_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._with_zempty(td)
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertEqual(r.stdout, "")
            err = _flat(r.stderr)
            self.assertIn("error: taxjson.toml: account 'zempty' has no "
                          "inputs (inputs/zempty/ is missing): nothing to "
                          "compare", err)
            self.assertIn("Its holdings are compared with the books "
                          "`taxjson run` builds from its exports. Save them "
                          "in inputs/zempty/ and run `taxjson run`, or take "
                          "out its `holdings` setting until it has some.",
                          err)
            self.assertNotIn("is not an account", err)
            # An empty folder: said so.
            (root / "inputs" / "zempty").mkdir()
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("(inputs/zempty/ holds no export)",
                          _flat(r.stderr))

    def test_named_in_the_arguments(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._with_zempty(td, holdings=False)
            for args in ((f"zempty={self.h}",), ("zempty", str(self.h))):
                with self.subTest(args=args):
                    r = _tjs(root, "sanity", *args)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    err = _flat(r.stderr)
                    self.assertIn("account 'zempty' has no inputs "
                                  "(inputs/zempty/ is missing)", err)
                    self.assertIn("or leave it out of the check.", err)
                    self.assertNotIn("is not an account", err)
                    self.assertNotIn("is neither an account", err)
            # A name taxjson.toml does not declare is still a usage
            # error of its own.
            r = _tjs(root, "sanity", f"nosuch={self.h}")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("'nosuch' is not an account", r.stderr)

    def test_inputs_and_no_books_say_the_run_did_not_build_them(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._with_zempty(td, holdings=False)
            (root / "inputs" / "zempty").mkdir()
            (root / "inputs" / "zempty" / "trades.tt").write_text(_RRSP)
            r = _tjs(root, "sanity", f"zempty={self.h}")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            err = _flat(r.stderr)
            self.assertIn("no gains file in work/ for account 'zempty', "
                          "whose inputs are there — the last `taxjson "
                          "run` did not build them", err)
            self.assertNotIn("is not an account", err)

    def test_a_holdings_snapshot_says_take_out_the_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._with_zempty(td, holdings=False)
            (root / "holdings").mkdir()
            shutil.copy(self.h, root / "holdings" / "zempty_holdings.toml")
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            err = _flat(r.stderr)
            self.assertIn("account 'zempty' has no inputs", err)
            self.assertIn("or take out its snapshot in holdings/ until it "
                          "has some.", err)

    def test_the_run_shows_it(self):
        from _qa_project import console, tj
        with tempfile.TemporaryDirectory() as td:
            root = self._with_zempty(td)
            out = console(tj(root, "run", "--no-input"))
            # As a person reads it: the headline on its one line.
            self.assertIn("Error: taxjson.toml: account 'zempty' has no "
                          "inputs (inputs/zempty/ is missing): nothing to "
                          "compare\n", out)
            self.assertIn("Warning: holdings check could not run — "
                          "`taxjson sanity`", out)
            self.assertNotIn("is not an account", out)


# ---- TJ-0640: nothing compared for a config problem is exit 2 -------------
class TestTJ0640NothingComparedExits2(unittest.TestCase):
    """TJ-0640: when every `holdings` file of taxjson.toml is missing,
    or every holdings/ snapshot is one no account claims, `tjs sanity`
    stopped with exit 1, the status of "positions differ". Nothing was
    compared: a named input missing, or one it cannot place, is exit 2
    (docs/commands.md, Exit codes), as TJ-0353 and TJ-0630 made sanity's
    other refusals."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _run_project(Path(cls._td.name))

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _copy(self, td) -> Path:
        root = Path(td) / "p"
        shutil.copytree(self.root, root)
        return root

    def test_every_holdings_path_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy(td)
            with open(root / "taxjson.toml", "a", encoding="utf-8") as fh:
                fh.write('holdings = ["nowhere.toml"]\n')    # margin's
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            err = _flat(r.stderr)
            self.assertIn("account margin: holdings file(s) missing — not "
                          "checked: nowhere.toml", err)
            self.assertIn("none of the `holdings` files in taxjson.toml "
                          "(or the holdings/ folder) could be checked (see "
                          "the notes above) — fix the paths.", err)
            # The run still names it, and goes on.
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = _flat(r.stdout + r.stderr)
            self.assertIn("error: none of the `holdings` files", out)
            self.assertIn("warning: holdings check could not run", out)

    def test_only_unclaimed_snapshots(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy(td)
            (root / "holdings").mkdir()
            (root / "holdings" / "zzz_positions.toml").write_text(
                '[[holding]]\nsymbol = "ZZQ.TO"\nquantity = 1\n')
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            err = _flat(r.stderr)
            self.assertIn("none of the snapshots in holdings/ could be "
                          "compared (see the notes above) — claim each for "
                          "its account", err)

    def test_a_difference_stays_exit_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy(td)
            (root / "pos.toml").write_text(
                '[[holding]]\nsymbol = "ZZQ.TO"\nquantity = 3\n')
            with open(root / "taxjson.toml", "a", encoding="utf-8") as fh:
                fh.write('holdings = ["pos.toml"]\n')        # margin's
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("QTY_MISMATCH", r.stdout)


# ---- TJ-0641: a damaged gains file named once, exit 2 --------------------
class TestTJ0641SumNamesADamagedGainsFileOnce(unittest.TestCase):
    """TJ-0641: with work/margin_gains_wash.json not JSON, `tjs sum` and
    `tjs estimate` said "could not read work/margin_gains_wash.json:
    /abs/…/work/margin_gains_wash.json: not valid JSON (…)" — the file
    twice, the second time by its absolute path — and exited 1. Named
    once, from the project, with the reason; exit 2, an input that
    cannot be read (docs/commands.md, Exit codes), as the views do
    (TJ-0596)."""

    def _project(self, td) -> Path:
        root = _run_project(
            Path(td), extra='[accounts.rrsp]\ntype = "sheltered"\n',
            more_inputs={"rrsp": _RRSP})
        text = (root / "taxjson.toml").read_text()
        (root / "taxjson.toml").write_text(text.replace(
            'source_currencies = []\n',
            'source_currencies = []\nprovince = "ON"\n', 1))
        return root

    def _assert_named_once(self, r, root, name, args):
        self.assertEqual(r.returncode, 2, (args, r.stdout + r.stderr))
        err = _flat(r.stderr)
        self.assertIn(f"could not read work/{name}: not valid JSON (",
                      err)
        self.assertEqual(err.count(name), 1, err)
        self.assertNotIn(str(root), err)

    def test_sum_estimate_and_plot_tax(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            g = _gains_file(root)
            g.write_text("{")
            for args in (("sum",), ("sum", "--json"), ("estimate",),
                         ("estimate", "--json"), ("plot-tax",)):
                with self.subTest(args=args):
                    r = _tjs(root, *args)
                    self._assert_named_once(r, root, g.name, args)
                    self.assertIn("A taxable account's gains cannot be "
                                  "left out", _flat(r.stderr))

    def test_a_sheltered_accounts_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            g = root / "work" / "rrsp_gains.json"
            self.assertTrue(g.is_file())
            g.write_text("{")
            r = _tjs(root, "sum")
            self._assert_named_once(r, root, g.name, ("sum",))
            self.assertIn("Account 'rrsp' (sheltered) would be left out",
                          _flat(r.stderr))

    def test_amt_and_instalments_keep_the_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            with open(root / "taxjson.toml", "a", encoding="utf-8") as fh:
                fh.write('[instalments]\nbasis = "current_year"\n'
                         'withheld = 0\n')
            g = _gains_file(root)
            g.write_text("{")
            for cmd in ("amt", "instalments"):
                with self.subTest(cmd=cmd):
                    r = _tjs(root, cmd)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    err = _flat(r.stderr)
                    self.assertIn("could not compute the estimate it "
                                  "builds on", err)
                    self.assertIn(f"could not read work/{g.name}: not "
                                  f"valid JSON", err)
                    self.assertEqual(err.count(g.name), 1, err)
                    self.assertNotIn(str(root), err)

    def test_the_reason_names_the_file_once(self):
        from taxjson.bin.taxjson_run import _work_read_why
        root = Path("/p/2025")
        p = root / "work" / "margin_gains.json"
        self.assertEqual(
            _work_read_why(p, ValueError(f"{p}: not valid JSON (x)"), root),
            "work/margin_gains.json: not valid JSON (x)")
        self.assertEqual(
            _work_read_why(p, OSError("Permission denied"), root),
            "work/margin_gains.json: Permission denied")
        # No root: a work/ file is named from the folder holding work/.
        self.assertEqual(
            _work_read_why(p, ValueError(f"{p}: bad")),
            "work/margin_gains.json: bad")


# ---- TJ-0642: wash-sales names work/, exit 2 ------------------------------
class TestTJ0642WashSalesNamesWork(unittest.TestCase):
    """TJ-0642: with no gains files in work/, `tjs wash-sales` said "no
    gains files in /abs/…/work (run `taxjson run` first)." and exited 1.
    work/ is named from the project and the input missing is exit 2, as
    sanity says it (TJ-0631); so are --explain's "no base files" and an
    account with none."""

    def test_no_gains_or_base_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = _run_project(Path(td))
            for p in (root / "work").glob("*gains*.json"):
                p.unlink()
            for args, says in (
                    (("wash-sales",), "no gains files in work/ — run "
                                      "`taxjson run` first."),
                    (("wash-sales", "--json"), "no gains files in work/"),
                    (("wash-sales", "nosuch"),
                     "no gains for account 'nosuch' in work/ — run "
                     "`taxjson run` first, or check the name.")):
                with self.subTest(args=args):
                    r = _tjs(root, *args)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertEqual(r.stdout, "")
                    err = _flat(r.stderr)
                    self.assertIn(f"taxjson wash-sales: error: {says}", err)
                    self.assertNotIn(str(root), err)
            for p in (root / "work").glob("*_base.json"):
                p.unlink()
            for args, says in (
                    (("wash-sales", "--explain"),
                     "no base files in work/ — run `taxjson run` first."),
                    (("wash-sales", "--explain", "nosuch"),
                     "no nosuch_base.json in work/ — run `taxjson run` "
                     "first, or check the name.")):
                with self.subTest(args=args):
                    r = _tjs(root, *args)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    err = _flat(r.stderr)
                    self.assertIn(f"taxjson wash-sales: error: {says}", err)
                    self.assertNotIn(str(root), err)


if __name__ == "__main__":
    unittest.main()
