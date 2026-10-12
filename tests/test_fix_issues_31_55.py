"""GitHub issues #31, #32, #49, #53, #54, #55 (synthetic data only).

- #31: rows of two accounts at one moment follow the accounts' NAMES,
  not their order in taxjson.toml: reordering the file changes no gain,
  and so the books stay current (CA-DATE-14 / US-DATE-13).
- #32: the filed-year locks a US gains run reads (US-WASH-22) are part
  of the run's fingerprint; Canada's gains read none.
- #49: `years` names a damaged filed/<year>.json instead of calling the
  year filed.
- #53: option-boundary on a base book without its rows: one error line.
- #54: wash-radar / safe-to-sell refuse a --date whose window leaves
  the calendar.
- #55: carry-forwards read from a lock taken before its year ended say
  they are provisional.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import gains_both, tx

from taxjson.lib import checklist as CL
from taxjson.lib import project_layout as PL

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _env():
    return dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                TAXJSON_WIDTH="0", NO_COLOR="1")


def _cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
         str(root), *args], capture_output=True, text=True, env=_env(),
        stdin=subprocess.DEVNULL, timeout=300)


def _tool(module, *args):
    return subprocess.run(
        [sys.executable, "-m", module, *args], capture_output=True,
        text=True, env=_env(), stdin=subprocess.DEVNULL, timeout=300)


def _config(year, accounts, country="canada"):
    cur = "CAD" if country == "canada" else "USD"
    return (f'[settings]\ncountry = "{country}"\nyear = {year}\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n'
            + "".join(f'\n[accounts.{a}]\ntype = "taxable"\n'
                      for a in accounts))


def _project(root, year, accounts, books, country="canada"):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(_config(year, accounts, country))
    (root / "ticker.map").write_text("")
    for a, text in books.items():
        d = root / "inputs" / a
        d.mkdir(parents=True, exist_ok=True)
        (d / "trades.tt").write_text(text)
    return root


def _gain(root, account):
    doc = json.loads((Path(root) / "work" / f"{account}_gains_wash.json")
                     .read_text())
    return doc["summary"]["total_gain"]


# ------------------------------------------------------------------ #31
# b sells 5 of its 10 (cost 10) at the same moment a buys 10 @30. By
# name a's buy comes first: the pool averages 20 and the sale gains 0.
# Before #31 the toml order decided (b first: the sale gained 50).
_ORDER_BOOKS = {
    "b": ("BUYSELL 2024-01-10 09:30:00 QZZQ.TO 10 CAD 10 100 0\n"
          "BUYSELL 2024-05-10 09:30:00 QZZQ.TO -5 CAD 20 100 0\n"),
    "a": "BUYSELL 2024-05-10 09:30:00 QZZQ.TO 10 CAD 30 300 0\n",
}


class TestAccountOrderIsNotAnInput(unittest.TestCase):
    """#31: the order of the accounts in taxjson.toml decides nothing."""

    @rule("CA-DATE-14")
    def test_reordered_toml_changes_no_gain_and_books_stay_current(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2024, ["b", "a"], _ORDER_BOOKS)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertAlmostEqual(_gain(root, "b"), 0.0, places=2)
            (root / "taxjson.toml").write_text(_config(2024, ["a", "b"]))
            self.assertEqual(
                CL.inputs_changed(root, PL.read_config(root)), "")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertAlmostEqual(_gain(root, "b"), 0.0, places=2)

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_engines_ignore_the_merge_order(self):
        # z sells at a loss; a and z rebuy at one moment. Either merge
        # order gives the same result in each country.
        def book(order):
            rows = {
                "z": [tx("BUYSELL", "2025-01-02", "XYZ.US", 100, 5000,
                         account="z"),
                      tx("BUYSELL", "2025-03-03", "XYZ.US", -100, 4000,
                         account="z"),
                      tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                         account="z", time="11:00:00")],
                "a": [tx("BUYSELL", "2025-03-10", "XYZ.US", 100, 4000,
                         account="a", time="11:00:00")]}
            return [t for n in order for t in rows[n]]

        def shape(res):
            return (round(res["summary"]["total_gain"], 2),
                    round(res["summary"]["total_disallowed"], 2),
                    sorted((str(i.get("account")),
                            round(i["total_cost"], 2))
                           for i in res["inventory"]))
        one = gains_both(book(["z", "a"]), year=2025)
        two = gains_both(book(["a", "z"]), year=2025)
        for c in ("canada", "usa"):
            self.assertEqual(shape(one[c]), shape(two[c]), c)

    @rule("CA-DATE-14")
    def test_one_accounts_rows_keep_their_order(self):
        # A write listed before its same-moment buy-back in ONE account
        # stays a write and a buy-back whatever the other account's
        # name (CA-DATE-14: the export's row order within an account).
        from taxjson.lib.core import _by_account_name
        rows = [tx("BUYSELL", "2025-03-10", "XYZ.TO", -1, 10, account="m"),
                tx("BUYSELL", "2025-03-10", "XYZ.TO", 1, 10, account="m"),
                tx("BUYSELL", "2025-03-10", "XYZ.TO", 1, 10, account="a")]
        (out,) = _by_account_name(rows)
        self.assertEqual([t.account for t in out], ["a", "m", "m"])
        self.assertEqual([t.quantity for t in out[1:]], [-1.0, 1.0])


# ------------------------------------------------------------------ #32
class TestLocksInTheFingerprint(unittest.TestCase):
    """#32: a new or removed filed-year lock of a US project makes the
    books stale; a Canadian project's gains read no lock."""

    def _lock(self, root, year, country):
        (root / "filed").mkdir(exist_ok=True)
        (root / "filed" / f"{year}.json").write_text(json.dumps({
            "schema_version": 1, "year": year, "country": country,
            "accounts": {}, "totals": {},
            "closed_at": f"{year + 1}-03-01T12:00:00"}))

    def test_canada_lock_is_not_a_run_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, ["margin"], {
                "margin": "BUYSELL 2025-01-02 09:30:00 QZZQ.TO 1 CAD 1 1 0\n"})
            cfg = PL.read_config(root)
            before = CL.input_fingerprint(root, cfg)
            self._lock(root, 2024, "canada")
            self.assertEqual(CL.input_fingerprint(root, cfg), before)

    @rule("US-WASH-22")
    def test_us_lock_makes_the_books_stale(self):
        # margin's 2026-01-05 loss is washed by b's 2025-12-15 buy, whose
        # sale on 12-17 is in 2025: with 2025 locked, the basis add is
        # booked in the loss's year instead (b's 2025 gain moves).
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2026, ["margin", "b"], {
                "margin": ("BUYSELL 2025-01-02 09:30:00 QZZQ.US 10 USD 50 "
                           "500 0\n"
                           "BUYSELL 2026-01-05 09:30:00 QZZQ.US -10 USD 30 "
                           "300 0\n"),
                "b": ("BUYSELL 2025-12-15 09:30:00 QZZQ.US 10 USD 30 300 0\n"
                      "BUYSELL 2025-12-17 09:30:00 QZZQ.US -10 USD 31 310 "
                      "0\n")}, country="usa")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            before = _gain(root, "b")
            self._lock(root, 2025, "usa")
            why = CL.inputs_changed(root, PL.read_config(root))
            self.assertIn(CL.LOCKED_YEARS_KEY, why or "")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotAlmostEqual(_gain(root, "b"), before, places=2)
            self.assertEqual(
                CL.inputs_changed(root, PL.read_config(root)), "")
            (root / "filed" / "2025.json").unlink()
            self.assertIn(CL.LOCKED_YEARS_KEY, CL.inputs_changed(
                root, PL.read_config(root)) or "")


# ------------------------------------------------------------------ #49
class TestYearsNamesADamagedLock(unittest.TestCase):

    def test_shapes(self):
        from taxjson.bin.taxjson_filed import lock_shape_problem
        good = {"year": 2024, "accounts": {"m": {}}, "totals": {}}
        self.assertIsNone(lock_shape_problem(good, 2024))
        self.assertIsNone(lock_shape_problem({}, 2024))
        for bad in ([], None, "x", 3, {"accounts": []},
                    {"accounts": {"m": 1}}, {"totals": []},
                    {"year": 2023}, {"year": "soon"}):
            self.assertTrue(lock_shape_problem(bad, 2024), bad)

    def test_years_reports_the_problem(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            for y, body in ((2024, "[]"), (2025, json.dumps(
                    {"year": 2025, "accounts": {}, "totals": {},
                     "closed_at": "2026-03-01T12:00:00"}))):
                d = top / str(y)
                d.mkdir()
                (d / "taxjson.toml").write_text(_config(y, ["margin"]))
                (d / "filed").mkdir()
                (d / "filed" / f"{y}.json").write_text(body)
            rep = {r["year"]: r for r in PL.years_report(top)["years"]}
            self.assertIn("filed/2024.json is not a close-year lock",
                          rep[2024]["problem"] or "")
            self.assertIsNone(rep[2025]["problem"])
            r = _cli(top, "years")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            row = [ln for ln in r.stdout.splitlines()
                   if ln.startswith("2024")]
            self.assertIn("lock damaged", row[0])
            self.assertIn("! 2024: filed/2024.json is not a close-year",
                          r.stdout)


# ------------------------------------------------------------------ #53
class TestOptionBoundaryDamagedBook(unittest.TestCase):

    def test_book_without_rows_is_one_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2024, ["margin"], {})
            (root / "work").mkdir()
            (root / "work" / "margin_base.json").write_text(
                '{"accounts": []}')
            r = _cli(root, "option-boundary")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn('work/margin_base.json has no "transactions" list',
                      r.stderr)


# ------------------------------------------------------------------ #54
class TestAsOfDateRange(unittest.TestCase):

    def test_problem(self):
        from taxjson.lib.dates import as_of_date_problem
        self.assertIsNone(as_of_date_problem("2025-03-03"))
        self.assertIsNone(as_of_date_problem("9999-10-31"))
        self.assertIn("out of range", as_of_date_problem("9999-12-31"))
        self.assertIn("out of range", as_of_date_problem("0001-01-01"))
        self.assertIn("0001-03-02", as_of_date_problem("0001-01-01"))
        self.assertIn("not a real calendar date",
                      as_of_date_problem("2025-02-30"))
        self.assertIn("not a YYYY-MM-DD", as_of_date_problem("2025-3-3"))

    def test_radar_and_safe_to_sell_refuse_it(self):
        with tempfile.TemporaryDirectory() as td:
            book = Path(td) / "book.json"
            book.write_text(json.dumps({"transactions": [{
                "action": "BUYSELL", "date": "2025-01-02",
                "date_settle": "2025-01-03", "time": "09:30:00",
                "symbol": "QZZQ.TO", "quantity": 10, "price": 10,
                "net_amount": 100, "currency": "CAD",
                "account": "margin"}]}))
            for mod in ("taxjson.bin.taxjson_wash_radar",
                        "taxjson.bin.taxjson_safe_to_sell"):
                r = _tool(mod, "--taxable", str(book), "--country",
                          "canada", "--date", "9999-12-31")
                self.assertNotEqual(r.returncode, 0, mod)
                self.assertNotIn("Traceback", r.stderr, mod)
                self.assertIn("--date 9999-12-31 is out of range",
                              r.stderr, mod)


# ------------------------------------------------------------------ #55
class TestPartialLockCarryforward(unittest.TestCase):

    def test_resolvers_say_provisional(self):
        from taxjson.lib import carryforward as CF
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "filed").mkdir()
            lock = {"year": 2024, "country": "canada",
                    "carryforwards": {
                        "net_capital_loss": {"closing": 50},
                        "minimum_tax": {"closing_by_year": {"2024": 20}}}}
            settings = {"year": 2025, "country": "canada"}
            for closed, partial in (("2024-06-01T12:00:00", True),
                                    ("2025-03-01T12:00:00", False)):
                (d / "filed" / "2024.json").write_text(
                    json.dumps(dict(lock, closed_at=closed)))
                loss = CF.resolve_losses(d, settings, "canada", False)
                amt = CF.resolve_amt(d, settings, {})
                self.assertEqual(loss["other_losses"], 50)
                for res in (loss, amt):
                    said = any("provisional" in n for n in res["notes"])
                    self.assertEqual(said, partial, closed)
                    self.assertEqual(res.get("partial_lock"),
                                     2024 if partial else None)

    def test_estimate_says_it_on_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, ["margin"], {
                "margin": ("BUYSELL 2025-01-10 09:30:00 QZZQ.TO 10 CAD 10 "
                           "100 0\n"
                           "BUYSELL 2025-05-10 09:30:00 QZZQ.TO -5 CAD 12 "
                           "60 0\n")})
            (root / "filed").mkdir()
            (root / "filed" / "2024.json").write_text(json.dumps({
                "year": 2024, "country": "canada", "accounts": {},
                "closed_at": "2024-06-01T12:00:00",
                "carryforwards": {"net_capital_loss": {"closing": 50}}}))
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, "estimate", "--province", "ON")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            acts = [ln for ln in r.stdout.splitlines()
                    if ln.startswith("! Carryovers come from the 2024")]
            self.assertEqual(len(acts), 1, r.stdout)
            self.assertLessEqual(len(acts[0]), 100)
            r = _cli(root, "estimate", "--province", "ON", "--json")
            doc = json.loads(r.stdout)
            self.assertEqual(
                doc["estimate"]["carry_sources"]["partial_lock"], 2024)


if __name__ == "__main__":
    unittest.main()
