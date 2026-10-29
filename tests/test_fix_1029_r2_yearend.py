"""The 2026-10-29 fix round 2, year-end group: the Canadian year end
after close-year.

- TJ-0421 a filed year's own rows added to a shared export after the
  close (a re-download with the missing late-December trades), or put
  there in a new file, are named by check-filed, the run and `years`;
- TJ-0587 one of two identical rows removed from a shared export is a
  lost row (the rows are counted, not a set);
- TJ-0574 a Webull row's DD-MM-YYYY date (and each date form a
  supported export writes) dates the row for that check;
- TJ-0434 fees.rpt does not count work/'s files that are no broker's
  book (checklist_results.json), so `compare filed` after a
  `checklist --run` is no difference;
- TJ-0567 a saved snapshot whose figure is NaN (infinite, text, a
  boolean) is refused, and a NaN is a difference;
- TJ-0433 `carryover` in a closed year shows what its own lock applied
  and carried out;
- TJ-0432 a [carryover] claimed amount above its year's net gain is
  named;
- TJ-0430 an amended loss year re-closed names the later year filed on
  its old balance (close-year, that year's check-filed, `run --strict`,
  its carryover);
- TJ-0431 a first year closed with no [estimate] other_losses and no
  record names the books' earlier loss the lock does not take in;
- TJ-0435 a year frozen as a single folder, then moved by `migrate
  --to-years`, counts its copy's later years' rows in check-dates;
- TJ-0475 `init` of an earlier year beside a year folder names no .bak
  it did not write, makes no template account folder the shared
  inputs/ lacks, and points at the other year's account tables;

Synthetic data only (made-up tickers and accounts, amounts under a
thousand).
"""
import json
import re
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_run as run
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPATH = os.pathsep.join(str(REPO_ROOT / p) for p in (
    "src", "packages/taxjson-fetch/src"))


def _env(**extra):
    env = dict(os.environ, TAXJSON_WIDTH="0", NO_COLOR="1",
               TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = _PYPATH + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra)
    return env


def _cli(root, *args, cwd=None):
    """`taxjson -C ROOT ARGS` as a child of THIS tree's code (`root`
    None: no -C, run in `cwd`)."""
    pre = [] if root is None else ["-C", str(root)]
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             [*pre, *map(str, args)])
    return subprocess.run(cmd, cwd=cwd or REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env=_env(), timeout=900)


def _tmp(test):
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    return Path(td.name)


def _said(r):
    return " ".join((r.stdout + r.stderr).split())


# ------------------------------------------------- TJ-0421, 0587, 0574
_ROW_A = "BUYSELL 2024-03-04 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 0.00"
_ROW_B = "BUYSELL 2024-07-02 10:00:00 ZZQ.TO -5 CAD 20.00 100.00 0.00"
_ROW_25 = "BUYSELL 2025-02-03 10:00:00 ZZQ.TO -5 CAD 21.00 105.00 0.00"
_ROW_DEC = ("BUYSELL 2024-12-16 10:00:00 ZZY.TO 10 CAD 10.00 -100.00 0.00",
            "BUYSELL 2024-12-19 10:00:00 ZZY.TO -10 CAD 15.00 150.00 0.00")


class TestSharedExportsMoved(unittest.TestCase):
    """_shared_exports_moved on a hand-made frozen 2024 year: its copy
    in 2024/inputs/<account>/, the shared folder ../inputs/."""

    def setUp(self):
        top = _tmp(self)
        self.root = top / "2024"
        self.shared = top / "inputs"
        (self.root / "inputs" / "margin").mkdir(parents=True)
        (self.shared / "margin").mkdir(parents=True)
        (self.root / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "canada"\n\n'
            '[accounts.margin]\ntype = "taxable"\n\n'
            '[accounts.extra]\ntype = "taxable"\n')
        self.fz = {"inputs_dir": "../inputs", "inputs": {}}

    def frozen(self, name, rows, acct="margin"):
        text = "".join(r + "\n" for r in rows)
        (self.root / "inputs" / acct / name).write_text(text)
        (self.shared / acct).mkdir(exist_ok=True)
        (self.shared / acct / name).write_text(text)
        self.fz["inputs"][f"inputs/{acct}/{name}"] = "made-up-hash"

    def shared_file(self, name, rows, acct="margin"):
        (self.shared / acct).mkdir(exist_ok=True)
        (self.shared / acct / name).write_text(
            "".join(r + "\n" for r in rows))

    def moved(self):
        return run._shared_exports_moved(self.root, self.fz, 2024)

    def test_rows_of_the_year_added_are_named(self):
        """TJ-0421: the re-download has the late-December round trip."""
        self.frozen("trades.tt", [_ROW_A, _ROW_B])
        self.shared_file("trades.tt", [_ROW_A, _ROW_B, *_ROW_DEC])
        self.assertEqual(self.moved(), ["../inputs/margin/trades.tt"])

    def test_rows_of_a_later_year_added_are_no_change(self):
        self.frozen("trades.tt", [_ROW_A, _ROW_B])
        self.shared_file("trades.tt", [_ROW_A, _ROW_B, _ROW_25])
        self.assertEqual(self.moved(), [])

    def test_a_new_file_with_rows_of_the_year_is_named(self):
        """TJ-0421: the late rows in a file of their own."""
        self.frozen("trades.tt", [_ROW_A, _ROW_B])
        self.shared_file("late_dec.tt", ["# made-up", *_ROW_DEC])
        self.shared_file("y2025.tt", [_ROW_25])
        self.assertEqual(self.moved(), ["../inputs/margin/late_dec.tt"])

    def test_a_new_file_overlapping_the_copy_is_no_change(self):
        """A later year's export that repeats rows the copy has (an
        overlapping date range) adds nothing to the filed year."""
        self.frozen("trades.tt", [_ROW_A, _ROW_B])
        self.shared_file("y2025.tt", [_ROW_B, _ROW_25])
        self.assertEqual(self.moved(), [])

    def test_a_row_moved_between_files_is_no_change(self):
        self.frozen("a.tt", [_ROW_A])
        self.frozen("b.tt", [_ROW_B])
        self.shared_file("a.tt", [_ROW_A, _ROW_B, _ROW_25])
        (self.shared / "margin" / "b.tt").unlink()
        self.assertEqual(self.moved(), [])

    def test_an_account_with_no_export_at_the_close(self):
        """An account the year names whose shared folder was empty at
        the close: a file of the year put there later is named."""
        self.frozen("trades.tt", [_ROW_A, _ROW_B])
        self.shared_file("late.tt", [_ROW_DEC[0]], acct="extra")
        self.assertEqual(self.moved(), ["../inputs/extra/late.tt"])

    def test_skipped_and_generated_names_are_not_judged(self):
        self.frozen("trades.tt", [_ROW_A, _ROW_B])
        self.shared_file("~$trades.tt", [*_ROW_DEC])
        self.shared_file(".hidden.csv", [*_ROW_DEC])
        self.shared_file("crypto_sends.tt", [*_ROW_DEC])
        self.assertEqual(self.moved(), [])

    def test_one_of_two_identical_rows_removed(self):
        """TJ-0587: the rows were a set, so the second occurrence of a
        row counted as there while one remained."""
        self.frozen("trades.tt", [_ROW_A, _ROW_A])
        self.shared_file("trades.tt", [_ROW_A])
        self.assertEqual(self.moved(), ["../inputs/margin/trades.tt"])
        self.shared_file("trades.tt", [_ROW_A, _ROW_A, _ROW_25])
        self.assertEqual(self.moved(), [])

    def test_a_webull_row_corrected(self):
        """TJ-0574: a Webull row dated 03-03-2025 had no year, so a
        corrected purchase amount showed nowhere."""
        head = "Currency,Date,Action Code,Symbol,Quantity,Price,Amount"
        self.frozen("webull.csv", [head, "CAD,03-03-2024,BUY,ZZW,10,10,(100)"])
        self.shared_file("webull.csv",
                         [head, "CAD,03-03-2024,BUY,ZZW,10,12,(120)"])
        self.assertEqual(self.moved(), ["../inputs/margin/webull.csv"])
        # A row of 2025 changed: no change of 2024's.
        self.frozen("webull.csv", [head, "CAD,03-03-2025,BUY,ZZW,10,10,(100)"])
        self.shared_file("webull.csv",
                         [head, "CAD,03-03-2025,BUY,ZZW,10,12,(120)"])
        self.assertEqual(self.moved(), [])

    def test_line_year_reads_every_export_date_form(self):
        """TJ-0574: each date form a supported export writes."""
        for line, year in (
                ("CAD,03-03-2025,BUY,ZZW,SYN CORP,SHS,10,10,(100)", 2025),
                ('"January 30, 2025","Feb 3, 2025",Buy,ZZR', 2025),
                ("10-Jan-22,12-Jan-22,Buy,ZZR", 2022),
                ("2025/03/04,2025/03/06,Buy,ZZR", 2025),
                ("03.03.2025,ZZG,Buy", 2025),
                ("2024-12-16 12:00:00 AM,2024-12-17 12:00:00 AM", 2024),
                ("2025-03-03T10:00:00Z,ZZC", 2025),
                ("U1234567,20240105,20240102,X", 2024),
                ("12/30/2023,01/03/2024,BUY", 2023)):
            self.assertEqual(run._line_year(line), year, line)
        for line in ("Date,Symbol,Quantity", "99900001,20241399,X",
                     "Year / Année:,,,,,,,2024,", "x 1.5 2.25 2025",
                     "ZZR Nov 2025 call"):
            self.assertIsNone(run._line_year(line), line)


# A per-year project: 2024 closed with its inputs frozen from ../inputs.
_CA24_TOML = ('[settings]\nyear = 2024\ncountry = "canada"\n'
              'base_currency = "CAD"\nsource_currencies = []\n'
              'province = "ON"\n\n[accounts.margin]\ntype = "taxable"\n')
_TRADES = ("# made-up trades\n"
           f"{_ROW_A}\n{_ROW_B}\n")


class _Closed2024(unittest.TestCase):
    """Year folders sharing one inputs/; 2024 closed with its inputs
    frozen; each test works on a copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(_TRADES)
        (top / "taxjson.toml").write_text(_CA24_TOML)
        for d, args in ((top, ("migrate", "--to-years")),
                        (top / "2024", ("run", "--no-input")),
                        (top / "2024", ("close-year", "--yes"))):
            r = _cli(d, *args)
            assert r.returncode == 0, (args, r.stdout[-2000:],
                                       r.stderr[-2000:])
        cls.base = top

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy_top(self):
        top = _tmp(self) / "p"
        shutil.copytree(self.base, top, symlinks=True)
        return top


class TestLateRowsOfAFiledYear(_Closed2024):
    """TJ-0421: check-filed, the run and `years` said OK."""

    _NOTE = ("note: filed 2024: 1 shared export(s) gained, changed or lost "
             "a row of 2024 or earlier since the year was closed: ")

    def _check(self, top, shown):
        r = _cli(top / "2024", "check-filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self._NOTE + shown, _said(r))
        self.assertIn("filed 2024: OK", r.stdout)
        doc = json.loads(_cli(top, "years", "--json").stdout)
        self.assertEqual(doc["years"][0]["shared_exports_changed"],
                         [shown.replace("../", "")])

    def test_rows_appended_to_the_shared_export(self):
        top = self.copy_top()
        f = top / "inputs" / "margin" / "trades.tt"
        f.write_text(f.read_text() + "".join(r + "\n" for r in _ROW_DEC))
        self._check(top, "../inputs/margin/trades.tt")
        r = _cli(top / "2024", "run", "--no-input")
        self.assertIn(self._NOTE + "../inputs/margin/trades.tt", _said(r))

    def test_rows_in_a_new_file(self):
        top = self.copy_top()
        (top / "inputs" / "margin" / "late_dec.tt").write_text(
            "".join(r + "\n" for r in _ROW_DEC))
        self._check(top, "../inputs/margin/late_dec.tt")

    def test_a_later_years_file_is_no_change(self):
        top = self.copy_top()
        (top / "inputs" / "margin" / "y2025.tt").write_text(_ROW_25 + "\n")
        r = _cli(top / "2024", "check-filed")
        self.assertNotIn("shared export", r.stdout)


# ------------------------------------------------------------- TJ-0434
def _fees(*args):
    cmd = python_module_argv("taxjson.bin.taxjson_fees", list(map(str, args)))
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, env=_env(), timeout=300)


class TestFeesReportStable(unittest.TestCase):
    """TJ-0434: `taxjson-fees --cache work/` counted every JSON in work/
    with no brokerage tag ("12 file(s) not from a broker export ... left
    out"), so checklist_results.json moved fees.rpt."""

    def setUp(self):
        self.d = _tmp(self)
        (self.d / "margin.json").write_text(json.dumps({
            "metadata": {"source_brokerage": "questrade"},
            "transactions": [{
                "id": "t1", "action": "BUYSELL", "date": "2024-02-01",
                "symbol": "ZZB.TO", "currency": "CAD", "quantity": 10,
                "commission": -4.95, "fee": 0.0, "gross_amount": -400.0}]}))
        for i in range(8):
            (self.d / f"margin_part{i}.json").write_text('{"rows": []}')

    def test_a_cache_folders_other_files_do_not_move_it(self):
        before = _fees("--cache", self.d, "--year", "2024")
        self.assertEqual(before.returncode, 0, before.stderr)
        (self.d / "checklist_results.json").write_text('{"results": []}')
        after = _fees("--cache", self.d, "--year", "2024")
        # The report's generation time can tick between the two runs.
        stamp = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
        self.assertEqual(stamp.sub("T", before.stdout),
                         stamp.sub("T", after.stdout))
        self.assertNotIn("not from a broker", after.stdout)
        doc = json.loads(_fees("--cache", self.d, "--json").stdout)
        self.assertEqual(doc["meta"]["skipped_files"], [])

    def test_a_named_file_with_no_tag_is_still_listed(self):
        p = self.d / "merged.json"
        p.write_text(json.dumps({"metadata": {}, "transactions": []}))
        r = _fees(self.d / "margin.json", p)
        self.assertIn("Skipped 1 non-broker file(s): merged.json", r.stdout)

    def test_compare_after_a_new_work_file_is_no_difference(self):
        root = _tmp(self) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "trades.tt").write_text(_TRADES)
        (root / "taxjson.toml").write_text(_CA24_TOML)
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (root / "work" / "checklist_results.json").write_text(
            '{"results": []}')
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = _cli(root, "compare")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("No difference", r.stdout)


# ------------------------------------------------------------- TJ-0567
class TestSnapshotFigureNotFinite(unittest.TestCase):
    """TJ-0567: a saved snapshot whose figure is NaN compared as
    unchanged (abs(NaN - x) > tolerance is False), exit 0."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = Path(cls._td.name) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "trades.tt").write_text(_TRADES)
        (root / "taxjson.toml").write_text(_CA24_TOML)
        for args in (("run", "--no-input"), ("compare", "--save", "base")):
            r = _cli(root, *args)
            assert r.returncode == 0, (args, r.stdout, r.stderr)
        cls.base = root

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _with(self, value):
        root = _tmp(self) / "p"
        shutil.copytree(self.base, root, symlinks=True)
        f = root / "work" / "compare" / "base" / "snapshot.json"
        doc = json.loads(f.read_text())
        doc["accounts"]["margin"]["realized"] = value
        f.write_text(json.dumps(doc))
        return root

    def test_a_nan_figure_is_refused(self):
        r = _cli(self._with(float("nan")), "compare", "base", "--json")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("work/compare/base/snapshot.json is not a snapshot "
                      "(accounts.margin.realized is NaN, not a finite "
                      "number)", _said(r))

    def test_text_or_a_boolean_is_refused(self):
        for v, what in (("50", "text"), (True, "a boolean"),
                        (float("inf"), "an infinite number")):
            r = _cli(self._with(v), "compare", "base")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn(f"(accounts.margin.realized is {what}, not a "
                          f"finite number)", _said(r))

    def test_a_good_snapshot_still_compares(self):
        r = _cli(self.base, "compare", "base", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["different"])

    def test_differs_on_a_nan(self):
        from taxjson.lib import compare as cmp
        self.assertTrue(cmp._differs(float("nan"), 50.0, "money"))
        self.assertTrue(cmp._differs(50.0, float("nan"), "money"))
        self.assertFalse(cmp._differs(50.0, 50.001, "money"))


# ------------------------------------------- TJ-0430, 0431, 0432, 0433
def _nets(**by_year):
    return {int(y[1:]): {"net": float(v), "st": 0.0, "lt": 0.0,
                         "dispositions": 1}
            for y, v in by_year.items()}


def _row(ledger, year):
    return next(r for r in ledger["rows"] if r["year"] == year)


class TestLedgerUnits(unittest.TestCase):

    def ledger(self, nets, claimed=None, **kw):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        return build_canada_ledger(nets, claimed or {}, **kw)

    def test_own_lock_applied_and_carried(self):
        """TJ-0433: the project year's own lock applied the 100 carried
        in and carried 0: its row says so, nothing is offered again."""
        led = self.ledger(_nets(y2024=-100, y2025=200),
                          carried={2024: 100.0, 2025: 0.0},
                          locked_applied={2024: 0.0, 2025: 100.0},
                          own_year=2025)
        r = _row(led, 2025)
        self.assertEqual((r["claimed_applied"], r["carryforward_balance"],
                          r["available_to_apply"]), (100.0, 0.0, 0.0))
        self.assertEqual(led["final_carryforward"], 0.0)
        self.assertEqual(r["rebuilt_balance"], 0.0)
        self.assertNotIn("applied_over_balance", r)

    def test_own_lock_keeps_the_claims_carry_back(self):
        """A T1A claim recorded after the close is the year's carry-back
        (close-year's re-close reads it) and comes off the balance its
        lock carried."""
        led = self.ledger(_nets(y2023=50, y2024=-100), {2023: 30.0},
                          carried={2024: 100.0}, locked_back={2024: 0.0},
                          locked_applied={2024: 0.0}, own_year=2024)
        r = _row(led, 2024)
        self.assertEqual(r["carried_back"], 30.0)
        self.assertEqual(r["carryforward_balance"], 70.0)
        # An earlier year's lock gives the claim back instead.
        led = self.ledger(_nets(y2023=50, y2024=-100), {2023: 30.0},
                          carried={2024: 100.0}, locked_back={2024: 0.0},
                          locked_applied={2024: 0.0})
        self.assertEqual(_row(led, 2024)["carried_back"], 0.0)

    def test_claim_above_the_years_gain(self):
        """TJ-0432: a claim of 100 under a year whose gain is 20."""
        led = self.ledger(_nets(y2024=-100, y2025=20), {2025: 100.0})
        self.assertEqual(led["claims_over_gain"],
                         {"2025": {"claimed": 100.0, "net_gain": 20.0,
                                   "excess": 80.0}})
        led = self.ledger(_nets(y2024=-100, y2025=20), {2025: 20.0})
        self.assertNotIn("claims_over_gain", led)
        # A year with no disposition in these books is not judged.
        led = self.ledger(_nets(y2024=-100), {2025: 100.0})
        self.assertNotIn("claims_over_gain", led)

    def test_claim_above_the_gain_is_named(self):
        from taxjson.bin import taxjson_carryover as C
        led = self.ledger(_nets(y2024=-100, y2025=20), {2025: 100.0})
        text = C.render_brief(led, "CAD", 2024)
        self.assertIn("! 2025: claimed 100.00, 80.00 above its net gain",
                      text)
        text = " ".join(C.render(led, "CAD", 2024, claimed_used=True,
                                 in_project=True).split())
        self.assertIn("[carryover] claimed for 2025 is 100.00, 80.00 more "
                      "than 2025's net gain 20.00", text)

    def test_lock_applied_more_than_carried_in(self):
        """TJ-0430: 2024 amended to carry 90; 2025's lock applied 100."""
        led = self.ledger(_nets(y2024=-90, y2025=200),
                          carried={2024: 90.0, 2025: 0.0},
                          locked_applied={2024: 0.0, 2025: 100.0})
        self.assertEqual(_row(led, 2025)["applied_over_balance"],
                         {"applied": 100.0, "carried_in": 90.0})
        # The year before rebuilt (no lock): not judged.
        led = self.ledger(_nets(y2024=-90, y2025=200),
                          carried={2025: 0.0},
                          locked_applied={2025: 100.0})
        self.assertNotIn("applied_over_balance", _row(led, 2025))

    def test_a_lock_balance_below_the_books(self):
        """TJ-0431: a first year closed carrying 0 while the books carry
        100 into it: the next year's ledger gives both."""
        from taxjson.bin import taxjson_carryover as C
        led = self.ledger(_nets(y2024=-100, y2025=20),
                          carried={2025: 0.0}, locked_applied={2025: 0.0})
        r = _row(led, 2025)
        self.assertEqual((r["carryforward_balance"], r["rebuilt_balance"]),
                         (0.0, 100.0))
        self.assertIn("! 2025: its lock carried out 0.00; these books, "
                      "100.00", C.render_brief(led, "CAD", 2024))

    def test_carry_in_moved(self):
        later = {"carryforwards": {"net_capital_loss": {
            "opening": 100.0, "applied": 100.0, "closing": 0.0,
            "opening_source": "../2024/filed/2024.json (the 2024 "
                              "close-year record)"}}}
        rec = {"carryforwards": {"net_capital_loss": {"closing": 90.0}}}
        self.assertEqual(run._carry_in_moved(later, rec, 2024),
                         (100.0, 100.0, 90.0))
        rec["carryforwards"]["net_capital_loss"]["closing"] = 100.0
        self.assertIsNone(run._carry_in_moved(later, rec, 2024))
        rec["carryforwards"]["net_capital_loss"]["closing"] = 90.0
        later["carryforwards"]["net_capital_loss"]["opening_source"] = (
            "[estimate] other_losses")
        self.assertIsNone(run._carry_in_moved(later, rec, 2024))


# 2024: a loss of 100 (a December sale); 2025: a gain of 200.
_LOSS_THEN_GAIN = ("# made-up trades\n"
                   "BUYSELL 2024-02-01 10:00:00 ZZB.TO 10 CAD 40.00 -400.00 "
                   "0.00\n"
                   "BUYSELL 2024-12-30 10:00:00 ZZB.TO -5 CAD 20.00 100.00 "
                   "0.00\n"
                   "BUYSELL 2025-02-03 10:00:00 ZZG.TO 10 CAD 10.00 -100.00 "
                   "0.00\n"
                   "BUYSELL 2025-05-01 10:00:00 ZZG.TO -10 CAD 30.00 300.00 "
                   "0.00\n")


class _Closed2024And2025(unittest.TestCase):
    """Year folders sharing one inputs/: 2024 (a loss of 100) and 2025
    (a gain of 200, applying it) closed; each test works on a copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        top = Path(cls._td.name) / "p"
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "trades.tt").write_text(
            _LOSS_THEN_GAIN)
        (top / "taxjson.toml").write_text(_CA24_TOML)
        for d, args in ((top, ("migrate", "--to-years")),
                        (top / "2024", ("run", "--no-input")),
                        (top / "2024", ("close-year", "--yes")),
                        (top / "2024", ("new-year", "2025")),
                        (top / "2025", ("run", "--no-input")),
                        (top / "2025", ("close-year", "--yes"))):
            r = _cli(d, *args)
            assert r.returncode == 0, (args, r.stdout[-2000:],
                                       r.stderr[-2000:])
        cls.base = top

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy_top(self):
        top = _tmp(self) / "p"
        shutil.copytree(self.base, top, symlinks=True)
        return top


class TestClosedYearLedger(_Closed2024And2025):

    def test_the_closed_year_shows_its_lock(self):
        """TJ-0433: after close-year 2025 (applied 100, carried 0), the
        2025 ledger said APPLIED 0.00, a balance of 100 after 2025 and
        "apply up to 100.00"."""
        r = _cli(self.base / "2025", "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        row = _row(doc, 2025)
        self.assertEqual((row["claimed_applied"], row["carryforward_balance"],
                          row["available_to_apply"]), (100.0, 0.0, 0.0))
        self.assertEqual(doc["final_carryforward"], 0.0)
        text = _cli(self.base / "2025", "carryover").stdout
        self.assertNotIn("apply up to", text)

    def test_an_amended_loss_year_names_the_later_filed_year(self):
        """TJ-0430: 2024's sale corrected (a loss of 90), 2024 re-closed:
        2025 had applied the 100 carried before."""
        top = self.copy_top()
        for f in (top / "inputs" / "margin" / "trades.tt",
                  top / "2024" / "inputs" / "margin" / "trades.tt"):
            f.write_text(f.read_text().replace(
                "-5 CAD 20.00 100.00", "-5 CAD 22.00 110.00"))
        r = _cli(top / "2024", "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = _cli(top / "2024", "close-year", "--force", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2025 was filed on the 100.00 net capital loss the "
                      "replaced 2024 lock carried; 2024 now carries 90.00",
                      _said(r))
        r = _cli(top / "2025", "check-filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        said = _said(r)
        self.assertIn("filed 2025 took in a 100.00 net capital loss from "
                      "2024, whose record now carries 90.00 into 2025", said)
        self.assertIn("The 2025 return applied 100.00 of the 100.00, more "
                      "than the 90.00 now carried in", said)
        self.assertIn("2025 needs a T1-ADJ too", said)
        r = _cli(top / "2025", "run", "--no-input", "--strict")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("took in a net capital loss its earlier year's "
                      "record no longer carries", _said(r))
        r = _cli(top / "2025", "carryover")
        self.assertIn("! 2025: its lock applied 100.00, more than the "
                      "90.00 carried in", r.stdout)

    def test_an_unchanged_reclose_names_nothing(self):
        top = self.copy_top()
        r = _cli(top / "2024", "close-year", "--force", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("was filed on the", _said(r))
        r = _cli(top / "2025", "check-filed")
        self.assertNotIn("took in a", _said(r))


class TestFirstYearLoss(unittest.TestCase):
    """TJ-0431: a first year with taxjson (no record, no [estimate]
    other_losses) whose books hold an earlier loss."""

    def project(self, extra=""):
        root = _tmp(self) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "trades.tt").write_text(
            _LOSS_THEN_GAIN)
        (root / "taxjson.toml").write_text(
            _CA24_TOML.replace("year = 2024", "year = 2025") + extra)
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return root

    def test_close_year_names_the_books_loss(self):
        r = _cli(self.project(), "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("carried into 2026: 0.00", r.stdout)
        self.assertIn("note: these books carry a net capital loss of "
                      "100.00 into 2025 (`taxjson carryover`) that the lock "
                      "does not take in", _said(r))
        self.assertIn("set [estimate] other_losses to it", _said(r))

    def test_other_losses_entered_is_no_note(self):
        root = self.project("\n[estimate]\nother_losses = 100\n")
        r = _cli(root, "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("carried into 2026: 0.00", r.stdout)
        self.assertNotIn("these books carry a net capital loss", _said(r))


# ------------------------------------------------------------- TJ-0435
_SINGLE_23 = ("# made-up trades\n"
              "BUYSELL 2023-02-01 10:00:00 ZZA.TO 10 CAD 5.00 -50.00 0.00\n"
              "BUYSELL 2023-05-01 10:00:00 ZZA.TO -5 CAD 8.00 40.00 0.00\n"
              "BUYSELL 2025-06-10 10:00:00 ZZA.TO -5 CAD 9.00 45.00 0.00\n")


class TestMigratedFrozenYearDates(unittest.TestCase):
    """A single-folder 2023 project, closed (its exports hold a 2025
    row), then `migrate --to-years`: 2023/ reads its own copy of the
    exports now shared in ../inputs."""

    def project(self):
        root = _tmp(self) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "trades.tt").write_text(_SINGLE_23)
        (root / "taxjson.toml").write_text(
            _CA24_TOML.replace("year = 2024", "year = 2023"))
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return root

    def test_single_folder_year_still_errs(self):
        """By the doc's letter: a single-folder project's own exports
        are no folder shared by every year."""
        r = _cli(self.project(), "check-dates")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("out-of-range", r.stdout)

    def test_migrated_year_counts_later_rows(self):
        root = self.project()
        for args in (("close-year", "--yes"), ("migrate", "--to-years")):
            r = _cli(root, *args)
            self.assertEqual(r.returncode, 0, (args, r.stdout + r.stderr))
        lock = json.loads((root / "2023" / "filed" / "2023.json")
                          .read_text())
        self.assertIsNone(lock["frozen"].get("inputs_dir"))
        r = _cli(root / "2023", "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = _cli(root / "2023", "check-dates", "--details")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("out-of-range", r.stdout)
        self.assertIn("Info: 1 row(s) dated after 2024", _said(r))
        from taxjson.lib import project_layout as PL
        self.assertTrue(PL.frozen_from_shared(root / "2023", 2023))
        # The year folder's lock, the shared inputs/ gone: no exception.
        shutil.rmtree(root / "inputs")
        self.assertFalse(PL.frozen_from_shared(root / "2023", 2023))



# ------------------------------------------------------------- TJ-0475
class TestInitEarlierYear(unittest.TestCase):
    """`init --year 2024` beside a 2025 folder whose accounts are not
    the template's."""

    def project(self):
        top = _tmp(self) / "p"
        r = _cli(None, "init", "--country", "usa", "--year", "2025",
                 str(top))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for d in (top / "inputs").iterdir():
            if d.is_dir() and d.name != "slips":
                shutil.rmtree(d)
        (top / "inputs" / "brokA").mkdir(mode=0o700)
        cfg = top / "2025" / "taxjson.toml"
        text = cfg.read_text()
        cfg.write_text(text[:text.index("\n[accounts.") + 1]
                       + '[accounts.brokA]\ntype = "taxable"\n')
        return top

    def test_no_bak_named_and_no_template_folders(self):
        top = self.project()
        r = _cli(None, "init", "--country", "usa", "--year", "2024",
                 str(top))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((top / "2024" / "taxjson.toml.bak").exists())
        self.assertNotIn(".bak", r.stdout)
        self.assertIn("note: 2024/taxjson.toml has the template's "
                      "accounts, inputs/ 2025's (brokA) — copy their "
                      "tables from 2025/taxjson.toml", r.stdout)
        self.assertEqual(sorted(d.name for d in (top / "inputs").iterdir()
                                if d.is_dir()), ["brokA"])
        r = _cli(top / "2025", "run", "--no-input")
        self.assertNotIn("not an account of 2025", _said(r))
        r = _cli(None, "init", "--country", "usa", "--year", "2024",
                 str(top), "--details", "--force")
        self.assertIn("wrote 2024/taxjson.toml.bak", r.stdout)
        self.assertIn("No folder was made in inputs/ for the template's "
                      "margin", _said(r))

    def test_a_folder_no_year_names(self):
        """A folder of no year's accounts: add a section or remove it;
        the .bak only when this init wrote one."""
        top = _tmp(self) / "p"
        (top / "inputs" / "old").mkdir(parents=True)
        r = _cli(None, "init", "--country", "usa", "--year", "2025",
                 str(top))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("note: inputs/ old: no [accounts.*] section — add "
                      "one or remove them", r.stdout)
        r = _cli(None, "init", "--country", "usa", "--year", "2025",
                 str(top), "--force")
        self.assertIn("note: inputs/ old: no [accounts.*] section — re-add "
                      "(see taxjson.toml.bak) or remove them", r.stdout)


if __name__ == "__main__":
    unittest.main()
