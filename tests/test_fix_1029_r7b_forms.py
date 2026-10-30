"""Backlog fix round 7 (wave B) of 2026-10-29, group `forms`: form
export, carryover and the cash view.

TJ-0895  a commission-only disposal of unknown-cost units: its manual
         row keeps net proceeds -2, never "proceeds 2".
TJ-0896  US carryover: a loss still carried after the last year with a
         sale takes each later year's 3,000 up to the project year.
TJ-0898  a closed year's T5 box 18 capital-gains dividends stay in the
         carry-back room the next year's carryover offers.
TJ-0901  the standalone Form 8949 takes its year from the gains files
         (or the trade date), not a December trade's January settlement.
TJ-0904  taxjson-carryover --filed-lock YEAR=PATH refuses a lock of
         another year than YEAR.
TJ-0905  the standalone form export reads a gains file named twice once.
TJ-0889  `tjs cash`: a copied line keeps its book (at=alpha), so the
         copy reads into the book the original did.
TJ-0693  `tjs cash`: a stock-lending receipt is a #CREDIT line and an
         other credit, never a fee paid.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*), account names (margin), amounts under
1,000 (or round test figures).
"""
import csv
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


def _env():
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _tjs(root, *args):
    """`tjs -C ROOT ARGS` in a child process (stdin closed, unwrapped)."""
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=_env(),
                          stdin=subprocess.DEVNULL, timeout=600)


def _module(mod, *args, cwd=None):
    """A standalone tool (`taxjson-form-export` ...) in a child process."""
    return subprocess.run(python_module_argv(mod) + [str(a) for a in args],
                          capture_output=True, text=True, env=_env(),
                          cwd=cwd, stdin=subprocess.DEVNULL, timeout=600)


def _project(root, country="canada", year=2025, tt=None, settings="",
             accounts=("margin",)):
    root = Path(root)
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        f'source_currencies = []\n{settings}\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    if tt is not None:
        (root / "inputs" / accounts[0] / "trades.tt").write_text(tt)
    return root


def _run(test, root):
    r = _tjs(root, "run", "--no-input")
    test.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
    return r


class TestManualRowKeepsCommissionDebit(unittest.TestCase):
    """TJ-0895: 10 SYN held before the data at an unknown cost, sold
    for nothing with a 2 commission (net proceeds -2). The manual
    reporting row read abs(proceeds) and told the filer to report
    proceeds 2 received, in the text and the CSV of both countries."""

    def test_unit_long_sign_kept_short_cover_magnitude(self):
        from taxjson.bin.taxjson_form_export import manual_figures
        self.assertEqual(manual_figures({"proceeds": -2.0,
                                         "direction": "LONG"}),
                         (-2.0, None))
        self.assertEqual(manual_figures({"proceeds": 50.0}), (50.0, None))
        # A short cover keeps its cover cost as a magnitude (TJ-0497).
        self.assertEqual(manual_figures({"proceeds": -50.0,
                                         "direction": "SHORT"}),
                         (None, 50.0))

    def test_both_countries_text_and_csv(self):
        for country, cur, sfx in (("canada", "CAD", "TO"),
                                  ("usa", "USD", "US")):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as d:
                root = _project(
                    Path(d) / "p", country=country,
                    tt=(f"OPENING 2024-12-02 SYN.{sfx} 10 cost=unknown\n"
                        f"BUYSELL 2025-03-03 10:00:00 SYN.{sfx} -10 {cur} "
                        f"0 -2 2\n"))
                _run(self, root)
                r = _tjs(root, "form-export", "--details")
                self.assertEqual(r.returncode, 0, r.stderr)
                out = r.stdout + r.stderr
                self.assertIn(f"proceeds -2.00 {cur}", out)
                self.assertNotIn(f"proceeds 2.00 {cur}", out)
                self.assertIn("(-2.00)", out)
                f = Path(d) / "form.csv"
                r = _tjs(root, "form-export", "--csv", str(f))
                self.assertEqual(r.returncode, 0, r.stderr)
                rows = [row for row in
                        csv.reader(f.read_text().splitlines())
                        if row and row[0] == "MANUAL"]
                self.assertEqual(len(rows), 1)
                self.assertIn("-2.0", rows[0])
                self.assertNotIn("2.0", rows[0])


class TestUsCarryoverRunsToProjectYear(unittest.TestCase):
    """TJ-0896: a 2025 US project whose only sale is a 2023 short-term
    loss of 9000 (and a 2025 dividend). The ledger stopped at 2023 with
    6000 carried, while recording the default 3000 for 2025 produced
    the 2024 and 2025 rows and nothing carried."""

    TT = ("BUYSELL 2023-01-03 10:00:00 SYN.US 100 USD 100 10000 0\n"
          "BUYSELL 2023-06-01 10:00:00 SYN.US -100 USD 10 1000 0\n"
          "DIVIDEND 2025-03-03 10:00:00 NEW.US 10 USD 1 10 0\n")

    def _ledger(self, root):
        r = _tjs(root, "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
        return json.loads(r.stdout)

    def test_trailing_years_take_their_deduction(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d) / "p", country="usa", tt=self.TT)
            _run(self, root)
            led = self._ledger(root)
            self.assertEqual([r["year"] for r in led["rows"]],
                             [2023, 2024, 2025])
            self.assertEqual([r["ordinary_income_offset"]
                              for r in led["rows"]], [3000.0] * 3)
            self.assertEqual(led["final_carryforward"], 0.0)
            # Recording the default for 2025 changes nothing.
            with (root / "taxjson.toml").open("a") as f:
                f.write("\n[carryover]\nclaimed = {2025 = 3000}\n")
            led2 = self._ledger(root)
            self.assertEqual(led2["final_carryforward"], 0.0)
            self.assertEqual([r["year"] for r in led2["rows"]],
                             [2023, 2024, 2025])

    def test_unit_through_year(self):
        from taxjson.bin.taxjson_carryover import build_usa_ledger
        nets = {2023: {"net": -9000.0, "st": -9000.0, "lt": 0.0,
                       "dispositions": 1}}
        led = build_usa_ledger(nets, {}, through_year=2025)
        self.assertEqual([r["year"] for r in led["rows"]],
                         [2023, 2024, 2025])
        self.assertEqual(led["final_carryforward"], 0.0)
        # Without it (no project year), as before.
        self.assertEqual(build_usa_ledger(nets, {})["final_carryforward"],
                         6000.0)
        # No carry left: no empty trailing row.
        gain = {2023: {"net": 50.0, "st": 50.0, "lt": 0.0,
                       "dispositions": 1}}
        self.assertEqual([r["year"] for r in build_usa_ledger(
            gain, {}, through_year=2025)["rows"]], [2023])


class TestLockKeepsBox18Gain(unittest.TestCase):
    """TJ-0898: 2024 (Canada) has a T5 box 18 dividend of 100 mapped by
    [[capital_gains_dividends]] and no sale; 2025 has a loss of 100 and
    points prior_year_record at 2024's lock. After close-year the 2025
    ledger read 2024's net as the RETURN row alone (0) and offered no
    carry-back."""

    def test_carry_back_room_after_handoff(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(
                Path(d) / "y2024", year=2024,
                tt=("BUYSELL 2024-01-03 10:00:00 FUND.TO 10 CAD 10 100 0\n"
                    "DIVIDEND 2024-06-03 10:00:00 FUND.TO 10 CAD 10 100 0\n"),
                settings="option_grant_timing_since = 2024\n")
            with (p / "taxjson.toml").open("a") as f:
                f.write('\n[[capital_gains_dividends]]\nsymbol = "FUND.TO"'
                        '\nyear = 2024\namount = "all"\n')
            _run(self, p)
            r = _tjs(p, "close-year", "--yes")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lock = json.loads((p / "filed" / "2024.json").read_text())
            self.assertEqual(lock.get("slip_gains"), 100.0)
            q = _project(
                Path(d) / "y2025", year=2025,
                tt=("BUYSELL 2025-01-03 10:00:00 LOSS.TO 10 CAD 20 200 0\n"
                    "BUYSELL 2025-06-03 10:00:00 LOSS.TO -10 CAD 10 100 0\n"),
                settings=('prior_year_record = "../y2024/filed/2024.json"\n'
                          "option_grant_timing_since = 2025\n"))
            _run(self, q)
            r = _tjs(q, "carryover", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            led = json.loads(r.stdout)
            rows = {row["year"]: row for row in led["rows"]}
            self.assertEqual(rows[2024]["net_gain"], 100.0)
            self.assertEqual(rows[2024]["slip_gains"], 100.0)
            self.assertTrue(rows[2024]["from_lock"])
            self.assertNotIn("differs_from_filed", rows[2024])
            cands = rows[2025]["carryback_candidates"]
            self.assertEqual([(c["year"], c["amount"]) for c in cands],
                             [(2024, 100.0)])

    def test_damaged_slip_gains_is_a_damaged_lock(self):
        from taxjson.bin.taxjson_filed import lock_shape_problem
        self.assertIn("slip_gains", lock_shape_problem(
            {"year": 2024, "slip_gains": float("nan")}, 2024) or "")


# A synthetic Interactive Brokers activity statement: 10 SYN bought
# 2024-01-03, sold 2024-12-31 (settling 2025-01-02).
IB_DEC31 = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers\n"
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U1234567\n"
    "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
    "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
    "Realized P/L,MTM P/L,Code\n"
    'Trades,Data,Order,Stocks,USD,SYN,"2024-01-03, 10:00:00",10,10,10,'
    "-100,0,100,0,0,O\n"
    'Trades,Data,Order,Stocks,USD,SYN,"2024-12-31, 10:00:00",-10,12,12,'
    "120,0,-100,20,0,C\n")


class TestStandaloneFormYear(unittest.TestCase):
    """TJ-0901: a US 2024 gains file whose only sale was traded
    2024-12-31 and settled 2025-01-02. `taxjson-form-export FILE
    --country usa --form 8949 --json` without --year labelled it 2025
    (the settlement's year)."""

    def test_unit_infer_year_on_the_files_basis(self):
        from taxjson.bin.taxjson_form_export import _infer_year
        rows = [{"date": "2024-12-31", "date_settle": "2025-01-02"}]
        self.assertEqual(_infer_year(rows, "date"), 2024)
        self.assertEqual(_infer_year(rows, "date_settle"), 2025)
        self.assertEqual(_infer_year(rows), 2025)

    def test_standalone_8949_year(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d) / "p", country="usa", year=2024)
            (root / "inputs" / "margin" / "ib.csv").write_text(IB_DEC31)
            _run(self, root)
            f = root / "work" / "margin_gains_wash.json"
            if not f.exists():
                f = root / "work" / "margin_gains.json"
            sale = [t for t in json.loads(f.read_text())["transactions"]
                    if t.get("gain") is not None]
            self.assertEqual(sale[0]["date"], "2024-12-31")
            self.assertTrue(sale[0]["date_settle"].startswith("2025"))
            r = _module("taxjson.bin.taxjson_form_export", f, "--country",
                        "usa", "--form", "8949", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["year"], 2024)
            r = _module("taxjson.bin.taxjson_form_export", f, "--country",
                        "usa", "--form", "8949")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("tax year 2024", r.stdout)
            # A file that records no year: the rows' trade dates.
            doc = json.loads(f.read_text())
            doc["summary"].pop("year", None)
            g = Path(d) / "noyear" / "gains.json"
            g.parent.mkdir()
            g.write_text(json.dumps(doc))
            r = _module("taxjson.bin.taxjson_form_export", g, "--country",
                        "usa", "--form", "8949", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["year"], 2024)


class TestStandaloneFormFileOnce(unittest.TestCase):
    """TJ-0905: the same gains file given twice to taxjson-form-export
    (explicit paths and a shell glob that overlap) doubled the
    Schedule 3 totals at readiness complete."""

    def test_repeated_path_read_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(
                Path(d) / "p", year=2024,
                tt=("BUYSELL 2024-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                    "BUYSELL 2024-06-03 10:00:00 SYN.TO -10 CAD 20 200 0\n"),
                settings="option_grant_timing_since = 2024\n")
            _run(self, root)
            g = root / "work" / "margin_gains.json"
            args = ("--country", "canada", "--form", "schedule3",
                    "--year", "2024", "--json")
            one = _module("taxjson.bin.taxjson_form_export", g, *args)
            self.assertEqual(one.returncode, 0, one.stderr)
            # The same file twice, once by another spelling of its path.
            two = _module("taxjson.bin.taxjson_form_export", g,
                          root / "work" / ".." / "work" / g.name, *args)
            self.assertEqual(two.returncode, 0, two.stderr)
            a, b = json.loads(one.stdout), json.loads(two.stdout)
            self.assertEqual(a["totals"], b["totals"])
            self.assertEqual(a["totals"]["gain_all"], 100.0)
            self.assertIn("given more than once", two.stderr)

    def test_unit_unique_paths(self):
        from taxjson.bin.taxjson_form_export import _unique_paths
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.json", Path(d) / "b.json"
            once, again = _unique_paths([a, b, Path(d) / "." / "a.json"])
            self.assertEqual(once, [a, b])
            self.assertEqual(len(again), 1)


class TestFiledLockYearBinding(unittest.TestCase):
    """TJ-0904: a genuine 2024 lock (gain 100) passed to the standalone
    taxjson-carryover as `--filed-lock 2021=.../2024.json` was taken as
    2021's filed return at exit 0, and the 2024 carry-back room of the
    2025 loss was gone."""

    def test_binding_checked(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(
                Path(d) / "y2024", year=2024,
                tt=("BUYSELL 2024-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                    "BUYSELL 2024-06-03 10:00:00 SYN.TO -10 CAD 20 200 0\n"),
                settings="option_grant_timing_since = 2024\n")
            _run(self, p)
            r = _tjs(p, "close-year", "--yes", "--force")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            q = _project(
                Path(d) / "y2025", year=2025,
                tt=("BUYSELL 2025-01-03 10:00:00 LOSS.TO 10 CAD 20 200 0\n"
                    "BUYSELL 2025-06-03 10:00:00 LOSS.TO -10 CAD 10 100 0\n"),
                settings="option_grant_timing_since = 2025\n")
            _run(self, q)
            lock = p / "filed" / "2024.json"

            def carry(binding):
                return _module(
                    "taxjson.bin.taxjson_carryover",
                    q / "work" / "margin_base.json", "--country", "canada",
                    "--project-year", "2025", "--filed-lock",
                    f"{binding}={lock}", "--json")

            bad = carry(2021)
            self.assertEqual(bad.returncode, 2, bad.stdout + bad.stderr)
            self.assertIn("the lock is for 2024, not 2021", bad.stderr)
            self.assertEqual(bad.stdout, "")
            # A lock named anything: its own year field decides.
            other = Path(d) / "record.json"
            other.write_text(lock.read_text())
            bad = _module(
                "taxjson.bin.taxjson_carryover",
                q / "work" / "margin_base.json", "--country", "canada",
                "--project-year", "2025", "--filed-lock",
                f"2021={other}", "--json")
            self.assertEqual(bad.returncode, 2, bad.stdout + bad.stderr)
            self.assertIn("the lock records year 2024, not 2021",
                          bad.stderr)
            good = carry(2024)
            self.assertEqual(good.returncode, 0, good.stderr)
            rows = {r["year"]: r for r in json.loads(good.stdout)["rows"]}
            self.assertEqual([(c["year"], c["amount"]) for c in
                              rows[2025]["carryback_candidates"]],
                             [(2024, 100.0)])


class TestCashLineKeepsItsBook(unittest.TestCase):
    """TJ-0889: one taxable account keeps two foreign-cash books by
    hand (at=alpha, at=beta). `tjs cash` printed their lines without
    at=, so the documented copy into a .tt file read every line into
    the folder's default book (margin/tt)."""

    TT = ("BUYSELL 2025-06-02 10:00:00 SYN.TO 1 CAD 10 10 0\n"
          "CASHOPEN 2025-01-01 USD 100 130 at=alpha\n"
          "CASHOPEN 2025-01-01 USD 200 280 at=beta\n"
          "CASHMOVE 2025-06-02 USD -10 spot at=alpha\n")

    def test_copy_keeps_book(self):
        from taxjson.lib import cash_events as CE
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d) / "p", tt=self.TT,
                            settings="option_grant_timing_since = 2025\n")
            _run(self, root)
            r = _tjs(root, "cash", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = json.loads(r.stdout)["rows"]
            self.assertEqual([(x["book"], x["tt"]) for x in rows], [
                ("margin/alpha", "CASHOPEN 2025-01-01 USD 100.00 130.00 "
                                 "at=alpha"),
                ("margin/beta", "CASHOPEN 2025-01-01 USD 200.00 280.00 "
                                "at=beta"),
                ("margin/alpha", "CASHMOVE 2025-06-02 USD -10.00 spot "
                                 "at=alpha")])
            text = _tjs(root, "cash")
            self.assertIn("at=beta", text.stdout)
            cfg = {"accounts": {"margin": {"type": "taxable"}}}
            before = [x["book"] for x in
                      CE.collect(root, cfg, "canada", {})["lines"]]
            (root / "inputs" / "margin" / "trades.tt").write_text(
                self.TT.splitlines()[0] + "\n"
                + "\n".join(x["tt"] for x in rows) + "\n")
            after = [x["book"] for x in
                     CE.collect(root, cfg, "canada", {})["lines"]]
            self.assertEqual(before, after)
            self.assertEqual(after, ["margin/alpha", "margin/beta",
                                     "margin/alpha"])

    def test_unit_default_book_needs_no_at(self):
        from taxjson.lib import cash_events as CE
        books = CE.Books()
        # A folder of .tt files only: its default book is <label>/tt.
        self.assertIsNone(CE.at_token({"label": "margin",
                                       "book": "margin/tt"}, books))
        self.assertEqual(CE.at_token({"label": "margin",
                                      "book": "margin/alpha"}, books),
                         "alpha")
        ev = {"kind": "CASHOPEN", "date": "2025-01-01", "currency": "USD",
              "units": 1.0, "cost": 2.0}
        self.assertEqual(CE.tt_line(ev), "CASHOPEN 2025-01-01 USD 1.00 2.00")
        self.assertEqual(CE.tt_line(ev, at="alpha"),
                         "CASHOPEN 2025-01-01 USD 1.00 2.00 at=alpha")


class TestCashLendingReceiptIsNoFee(unittest.TestCase):
    """TJ-0693: a Questrade LFJ row (stock lending income, +10 CAD) was
    listed by `tjs cash` as `#FEE ... +10.00` and counted in TOTAL FEES
    (fees.CAD 10)."""

    CSV = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n"
           "2025-06-03,2025-06-03,LFJ,,Stock lending income,0,0,10,0,10,"
           "CAD,Other,99900001,Margin\n")

    def test_receipt_is_a_credit(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(Path(d) / "p",
                            settings="option_grant_timing_since = 2025\n")
            (root / "inputs" / "margin" / "cash.csv").write_text(self.CSV)
            _run(self, root)
            r = _tjs(root, "cash", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["totals"]["fees"], {})
            self.assertEqual(doc["totals"]["credits"], {"CAD": 10.0})
            tts = [x["tt"] for x in doc["rows"]]
            self.assertIn("#CREDIT 2025-06-03 CAD +10.00", tts)
            text = _tjs(root, "cash").stdout
            self.assertNotIn("TOTAL FEES", text)
            self.assertIn("TOTAL OTHER CREDITS: 10.00 CAD", text)

    def test_unit_fee_paid_stays_fee(self):
        from taxjson.lib import cash_events as CE
        self.assertEqual(CE.tt_line(CE.flow("2025-03-05", "USD", -2.0)),
                         "#FEE 2025-03-05 USD -2.00")
        self.assertEqual(CE.tt_line(CE.flow("2025-03-05", "USD", 2.0)),
                         "#CREDIT 2025-03-05 USD +2.00")


if __name__ == "__main__":
    unittest.main()
