"""Backlog fix round 2 of 2026-10-29, group `review`: the review and
filing-output commands (audit --year, form-export, the wash review, the
stale-book warning, check-tt, the checklist's run-clean, sanity's
pointer, the inputs/ permission warning).

TJ-0550  audit --year refuses a lock closed under the other country
         before applying its date basis or option timing.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (ZZ*, SYN*), account names (margin, tfsa), amounts
under 1,000, broker account numbers like 99900001.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


def _tjs(root, *args, cwd=None, env_extra=None):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env.update(env_extra or {})
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args), capture_output=True,
                          text=True, env=env, cwd=cwd,
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(root, *, country="canada", year=2025, accounts=None,
             files=None, settings=""):
    """A project at `root`: taxjson.toml, an empty ticker.map and one
    inputs/<account>/ per account; `files` maps "account/name" to its
    text."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    accounts = accounts or {"margin": "taxable"}
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{settings}\n'
        + "".join(f'[accounts.{a}]\ntype = "{t}"\n'
                  for a, t in accounts.items()))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        acct, _, fn = name.partition("/")
        (root / "inputs" / acct / fn).write_text(text)
    return root


_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Activity Type,Account #,Account Type\n")


class TestAuditRefusesAForeignLock(unittest.TestCase):
    """TJ-0550: a Canadian 2026 project whose filed/2025.json was
    closed under country = "usa" with date_basis = "trade". `audit
    --year 2025` adopted the US lock's trade date basis (a sale traded
    Dec 31 2025, settled Jan 2 2026 moved into 2025: gain 0 became 50)
    and exited 0. It now refuses the lock, as check-filed does, before
    applying any of its settings."""

    def test_a_us_lock_in_a_canadian_project_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p", year=2026, files={
                "margin/q.csv": _QT_HEAD
                + "2025-01-06,2025-01-07,Buy,SYN.TO,SYNTHETIC,10,10,-100,"
                  "0,-100,CAD,Trades,99900001,Margin\n"
                + "2025-12-31,2026-01-02,Sell,SYN.TO,SYNTHETIC,-10,15,150,"
                  "0,150,CAD,Trades,99900001,Margin\n"})
            r = _tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            ctl = _tjs(p, "audit", "--year", "2025", "--json")
            self.assertEqual(ctl.returncode, 0, ctl.stderr)
            self.assertEqual(json.loads(ctl.stdout)["total_gain"], 0.0)
            (p / "filed").mkdir()
            (p / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "usa", "date_basis": "trade",
                 "accounts": {"margin": {}}}))
            r = _tjs(p, "audit", "--year", "2025", "--json")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("filed/2025.json", r.stderr)
            self.assertIn('country = "usa"', r.stderr)
            self.assertNotIn("trade date basis its lock recorded",
                             r.stderr)
            self.assertNotIn('"total_gain": 50', r.stdout)

    def test_a_lock_of_this_country_still_sets_the_date_basis(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p", year=2026, files={
                "margin/q.csv": _QT_HEAD
                + "2025-01-06,2025-01-07,Buy,SYN.TO,SYNTHETIC,10,10,-100,"
                  "0,-100,CAD,Trades,99900001,Margin\n"
                + "2025-12-31,2026-01-02,Sell,SYN.TO,SYNTHETIC,-10,15,150,"
                  "0,150,CAD,Trades,99900001,Margin\n"},
                settings='tax_date = "settle"\n')
            r = _tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            (p / "filed").mkdir()
            (p / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "canada", "date_basis": "trade",
                 "accounts": {"margin": {}}}))
            r = _tjs(p, "audit", "--year", "2025", "--json")
            self.assertEqual(json.loads(r.stdout)["total_gain"], 50.0,
                             r.stderr)
            self.assertIn("2025 is locked", r.stderr)


def _form_main(argv):
    """form-export's main in-process: (rc, stdout, stderr)."""
    import contextlib
    import io
    from taxjson.bin import taxjson_form_export as FE
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = FE.main(argv)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def _manual_long(currency, proceeds=100.0):
    return {"date": "2025-06-02", "date_settle": "2025-06-02",
            "symbol": "SYN.US", "qty": 10, "proceeds": proceeds,
            "currency": currency, "direction": "LONG", "account": "margin"}


class TestFormExportManualRowCurrency(unittest.TestCase):
    """TJ-0496: a gains file with no computed disposition and one
    unknown-cost (manual reporting) row in USD exported a CAD Schedule
    3 at exit 0, its manual proceeds 100 labelled CAD. The manual rows
    are held to the return's currency as the computed ones are."""

    def _doc(self, d, rows, txs=()):
        p = Path(d) / "g.json"
        p.write_text(json.dumps({"summary": {"year": 2025},
                                 "transactions": list(txs),
                                 "manual_reporting_required": rows}))
        return p

    def test_a_usd_manual_row_is_refused_in_a_cad_return(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._doc(d, [_manual_long("USD")])
            rc, out, err = _form_main([str(p), "--form", "schedule3",
                                       "--country", "canada", "--year",
                                       "2025", "--json"])
            self.assertEqual(rc, 2, out)
            self.assertIn("another currency than CAD", err)
            self.assertIn("1 USD", err)
            self.assertEqual(out, "")

    def test_a_usd_manual_row_is_refused_in_form_8949_text(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._doc(d, [dict(_manual_long("CAD"), term="SHORT_TERM")])
            rc, out, err = _form_main([str(p), "--form", "8949",
                                       "--country", "usa", "--year",
                                       "2025"])
            self.assertEqual(rc, 2, out)
            self.assertIn("another currency than USD", err)

    def test_a_manual_row_in_the_returns_currency_is_listed(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._doc(d, [_manual_long("CAD")])
            rc, out, err = _form_main([str(p), "--form", "schedule3",
                                       "--country", "canada", "--year",
                                       "2025", "--json"])
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(out)["manual_proceeds"], 100.0)

    def test_a_manual_row_with_no_currency_is_said(self):
        with tempfile.TemporaryDirectory() as d:
            row = _manual_long("CAD")
            del row["currency"]
            p = self._doc(d, [row])
            rc, out, err = _form_main([str(p), "--form", "schedule3",
                                       "--country", "canada", "--year",
                                       "2025", "--json"])
            self.assertEqual(rc, 0, err)
            self.assertIn("carry no currency", err)


def _short_cover_gains(country):
    """The engine's gains for an opening short of unknown proceeds (10
    SYN.US, before the data) covered on 2025-06-02 for 500."""
    import contextlib
    import io
    from taxjson.lib.core import TaxTransaction as T
    from taxjson.lib.pipeline import GainsRequest, run_gains
    cur = "CAD" if country == "canada" else "USD"

    def tx(i, a, d, q, n=0.0, sym="SYN.US"):
        return T(id=i, action=a, date=d, date_settle=d, symbol=sym,
                 quantity=q, net_amount=n, currency=cur, account="margin")
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(
            [tx("open", "OPENING_BALANCE", "2024-01-02", -10),
             tx("cover", "BUYSELL", "2025-06-02", 10, -500.0),
             tx("b", "BUYSELL", "2025-01-06", 5, -50.0, "ZZQ.US"),
             tx("s", "BUYSELL", "2025-02-03", -5, 60.0, "ZZQ.US")],
            req=GainsRequest(country=country, year=2025, taxable=True,
                             phantom_hint=False))


class TestFormExportManualShortCover(unittest.TestCase):
    """TJ-0497: an unknown-cost short cover (the short sale before the
    data, its proceeds unknown; covered for 500) was listed for manual
    reporting as proceeds 500 — the cover cost — in the warning, the
    MANUAL REPORTING table, the CSV and `manual_proceeds`. Its cover
    cost is now named as such, and its proceeds as unknown."""

    def _export(self, d, country, *extra):
        g = _short_cover_gains(country)
        self.assertEqual(
            [m["direction"] for m in g["manual_reporting_required"]],
            ["SHORT"])
        p = Path(d) / "g.json"
        p.write_text(json.dumps(g))
        form = "schedule3" if country == "canada" else "8949"
        return _form_main([str(p), "--form", form, "--country", country,
                           "--year", "2025", *extra])

    def test_json_separates_the_cover_cost_from_proceeds(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as d:
                rc, out, err = self._export(d, country, "--json")
                self.assertEqual(rc, 0, err)
                j = json.loads(out)
                self.assertEqual(j["manual_proceeds"], 0.0)
                self.assertEqual(j["manual_cover_cost"], 500.0)
                m = j["manual_reporting_required"][0]
                self.assertEqual(m["cover_cost"], 500.0)
                self.assertIsNone(m["short_sale_proceeds"])
                self.assertNotIn("proceeds 500.00", err)
                self.assertIn("cover cost 500.00", err)

    def test_the_text_table_says_unknown_proceeds(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as d:
                rc, out, err = self._export(d, country)
                self.assertEqual(rc, 0, err)
                sec = out[out.index("MANUAL REPORTING REQUIRED"):]
                self.assertIn("COVER COST", sec)
                row = next(ln for ln in sec.splitlines()
                           if ln.startswith("SYN.US"))
                self.assertIn("unknown", row)
                self.assertIn("500.00", row)
                self.assertIn("short", sec.splitlines()[0])

    def test_the_csv_writes_the_cover_cost_as_cost(self):
        import csv
        for country, pcol, ccol in (("canada", 7, 8), ("usa", 4, 5)):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as d:
                c = Path(d) / "out.csv"
                rc, out, err = self._export(d, country, "--csv", str(c))
                self.assertEqual(rc, 0, err)
                rows = [r for r in csv.reader(c.open())
                        if r and r[0] == "MANUAL"]
                self.assertEqual(len(rows), 1, rows)
                self.assertEqual(rows[0][pcol], "")
                self.assertEqual(float(rows[0][ccol]), 500.0)
                self.assertIn("short", ",".join(rows[0]))

    def test_a_long_row_keeps_its_proceeds(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.json"
            p.write_text(json.dumps({
                "summary": {"year": 2025}, "transactions": [],
                "manual_reporting_required": [_manual_long("CAD")]}))
            rc, out, err = _form_main([str(p), "--form", "schedule3",
                                       "--country", "canada", "--year",
                                       "2025", "--json"])
            j = json.loads(out)
            self.assertEqual(j["manual_proceeds"], 100.0)
            self.assertEqual(j["manual_cover_cost"], 0.0)
            self.assertNotIn("cover_cost",
                             j["manual_reporting_required"][0])
            self.assertIn("proceeds 100.00", err)


class TestWashReviewShowsUnknownCostWarnings(unittest.TestCase):
    """TJ-0551: a US sale of 2 SYN.US draws a lot bought for 10 (sold
    for 8: a loss of 2) and a unit with no purchase in the files. The
    engine warns that the wash-sale rule may apply and must be checked
    by hand (the gains file's superficial_loss_warnings), but `tjs
    wash-sales` listed no flag and the checklist marked wash-reviewed
    done ("no wash sales"). Both now carry the engine's warning."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.p = _project(Path(cls._td.name) / "p", country="usa", files={
            "margin/trades.tt":
                'OPENING 2024-12-31 SYN.US 1 cost=unknown '
                'reason="synthetic missing lot"\n'
                "BUYSELL 2025-01-06 10:00:00 SYN.US 1 USD 10 10 0\n"
                "BUYSELL 2025-03-03 10:00:00 SYN.US -2 USD 8 16 0\n"})
        r = _tjs(cls.p, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        g = json.loads((cls.p / "work" / "margin_gains_wash.json")
                       .read_text())
        assert g.get("superficial_loss_warnings"), g

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_wash_sales_json_lists_the_manual_check(self):
        r = _tjs(self.p, "wash-sales", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        flags = json.loads(r.stdout)["manual_check_flags"]
        self.assertEqual(len(flags), 1, flags)
        self.assertIn("SYN.US", flags[0])
        self.assertIn("verify manually", flags[0])

    def test_wash_sales_text_names_the_manual_check(self):
        r = _tjs(self.p, "wash-sales")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("MANUAL CHECK: 1 warn-only flag(s)", r.stdout)
        r = _tjs(self.p, "wash-sales", "--details")
        self.assertIn("unknown-cost disposition", r.stdout)

    def test_the_checklist_keeps_wash_reviewed_open(self):
        r = _tjs(self.p, "checklist", "--run", "--only", "wash-reviewed",
                 "--json")
        step = next(s for s in json.loads(r.stdout)["steps"]
                    if s["id"] == "wash-reviewed")
        self.assertEqual(step["status"], "manual", step)
        self.assertIn("SYN.US", step["detail"])
        self.assertNotIn("no wash sales", step["detail"])


class TestStaleBooksBannerOnReviewCommands(unittest.TestCase):
    """TJ-0557: a run over a buy for 10 and a sale for 20, then the
    purchase edited to 15 without a re-run. `list` warned that the
    inputs changed; `audit --json` still reported a gain of 10 with an
    empty stderr and exit 0, and journals, renames and cash said
    nothing either. Every review command that reads the saved books
    now prints the run-state banner (stderr, so --json stays valid)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.p = _project(Path(cls._td.name) / "p", files={
            "margin/trades.tt":
                "BUYSELL 2025-01-06 10:00:00 SYN.TO 1 CAD 10 -10 0\n"
                "BUYSELL 2025-03-03 10:00:00 SYN.TO -1 CAD 20 20 0\n"})
        r = _tjs(cls.p, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr
        cls.clean = {c: _tjs(cls.p, c, "--json")
                     for c in ("audit", "journals", "renames", "cash", "list")}
        (cls.p / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2025-01-06 10:00:00 SYN.TO 1 CAD 15 -15 0\n"
            "BUYSELL 2025-03-03 10:00:00 SYN.TO -1 CAD 20 20 0\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_each_review_command_warns_after_an_input_change(self):
        for cmd in ("audit", "journals", "renames", "cash", "list"):
            with self.subTest(cmd=cmd):
                self.assertNotIn("not the clean result",
                                 self.clean[cmd]
                                 .stderr)
                r = _tjs(self.p, cmd, "--json")
                self.assertIn("these books are not the clean result",
                              r.stderr)
                json.loads(r.stdout)

    def test_slip_audit_and_reconcile_slips_warn(self):
        r = _tjs(self.p, "slip-audit")
        self.assertIn("these books are not the clean result", r.stderr)
        r = _tjs(self.p, "slip-audit", "--template")
        self.assertNotIn("not the clean result", r.stderr)
        slip = self.p / "t5008.csv"
        slip.write_text("date,symbol,quantity,proceeds\n")
        r = _tjs(self.p, "reconcile-slips", str(slip))
        self.assertIn("these books are not the clean result", r.stderr)


class TestCheckTtRunsTheAllowlossProjectChecks(unittest.TestCase):
    """TJ-0566: two ALLOWLOSS lines naming one sale, or an ALLOWLOSS in
    a TFSA, each pass `check-tt` (errors 0, exit 0) while `tjs run`
    refuses them before computing anything. check-tt now applies the
    run's project check (loss_overrides.read_project) and names each
    line."""

    BUY = "BUYSELL 2025-01-02 10:00:00 SYN.TO 1 CAD 1 -1 0\n"

    def _check(self, accounts, rows, *args):
        with tempfile.TemporaryDirectory() as d:
            acct = next(iter(accounts))
            p = _project(Path(d) / "p", accounts=accounts,
                         files={f"{acct}/trades.tt": rows})
            run = _tjs(p, "run", "--no-input")
            r = _tjs(p, "check-tt", *args)
            return run, r

    def test_the_same_sale_named_twice_is_an_error(self):
        run, r = self._check(
            {"margin": "taxable"},
            'ALLOWLOSS 2025-03-03 SYN.TO reason="first reason"\n'
            'ALLOWLOSS 2025-03-03 SYN.TO reason="second reason"\n'
            + self.BUY, "--json")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("the same sale is named twice", run.stdout + run.stderr)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        errs = [p for p in doc["problems"]
                if p["severity"] == "error"]
        self.assertEqual(len(errs), 1, errs)
        self.assertEqual(errs[0]["line"], 2)
        self.assertIn("the same sale is named twice", errs[0]["message"])

    def test_an_allowloss_in_a_sheltered_account_is_an_error(self):
        run, r = self._check(
            {"tfsa": "sheltered"},
            'ALLOWLOSS 2025-03-03 SYN.TO reason="a reason"\n' + self.BUY)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("inputs/tfsa/trades.tt:1", r.stdout + r.stderr)
        self.assertIn("account tfsa is sheltered", r.stdout + r.stderr)

    def test_one_line_in_a_taxable_account_passes(self):
        run, r = self._check(
            {"margin": "taxable"},
            'ALLOWLOSS 2025-03-03 SYN.TO reason="a reason"\n' + self.BUY)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestOneExportInTwoAccounts(unittest.TestCase):
    """TJ-0478: one Questrade export copied into a second taxable
    account's folder. The run warned that the same export sits in two
    accounts (every row booked in both), but its closing list left it
    out, `tjs sum` showed no banner over the inflated totals and the
    checklist ticked run-clean done. The run now records what it found;
    run-clean is attention with a Next line that fixes it, every report
    banner carries it, and the closing list names it."""

    CSV = (_QT_HEAD
           + "2025-01-06,2025-01-07,Buy,SYN.TO,SYNTHETIC,10,10,-100,0,"
             "-100,CAD,Trades,99900001,Margin\n"
           + "2025-03-03,2025-03-04,Sell,SYN.TO,SYNTHETIC,-10,15,150,0,"
             "150,CAD,Trades,99900001,Margin\n")

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.p = _project(Path(cls._td.name) / "p",
                         accounts={"margin": "taxable", "other": "taxable"},
                         files={"margin/q.csv": cls.CSV,
                                "other/copy.csv": cls.CSV})
        cls.first = _tjs(cls.p, "run", "--no-input")
        assert cls.first.returncode == 0, cls.first.stdout + cls.first.stderr

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_the_run_warns_and_its_closing_list_names_it(self):
        out = self.first.stdout + self.first.stderr
        self.assertIn("the same export file sits in two accounts", out)
        close = self.first.stdout[
            self.first.stdout.index("Before you trust these numbers"):]
        self.assertIn("booked in two accounts", close)
        self.assertIn("margin/q.csv", close)

    def test_run_clean_is_attention_and_its_next_line_fixes_it(self):
        r = _tjs(self.p, "checklist", "--run", "--only", "run-clean",
                 "--json")
        doc = json.loads(r.stdout)
        step = next(s for s in doc["steps"] if s["id"] == "run-clean")
        self.assertEqual(step["status"], "attention", step)
        self.assertIn("margin/q.csv", step["detail"])
        self.assertIn("other/copy.csv", step["detail"])
        r = _tjs(self.p, "checklist")
        nxt = [ln for ln in r.stdout.splitlines()
               if ln.startswith("Next")]
        self.assertTrue(nxt, r.stdout)
        self.assertIn("one inputs/<account>/ folder", nxt[0])

    def test_sum_carries_the_run_state_banner(self):
        r = _tjs(self.p, "sum")
        self.assertIn("these books are not the clean result", r.stderr)
        r = _tjs(self.p, "sum", "--details")
        self.assertIn("booked twice: keep each export in one", r.stderr)

    def test_moving_the_copy_away_and_running_again_clears_it(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(Path(d) / "p",
                         accounts={"margin": "taxable", "other": "taxable"},
                         files={"margin/q.csv": self.CSV,
                                "other/copy.csv": self.CSV})
            self.assertEqual(_tjs(p, "run", "--no-input").returncode, 0)
            (p / "inputs" / "other" / "copy.csv").unlink()
            (p / "inputs" / "other" / "x.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 10 -10 0\n")
            r = _tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("booked in two accounts", r.stdout)
            r = _tjs(p, "checklist", "--run", "--only", "run-clean",
                     "--json")
            step = next(s for s in json.loads(r.stdout)["steps"]
                        if s["id"] == "run-clean")
            self.assertEqual(step["status"], "done", step)


class TestSanityPointsAtTheFixThatWorks(unittest.TestCase):
    """TJ-0423: `sanity` ended every difference with fewer shares in the
    books on "usually means missing history — tjs find-missing-history",
    which then said "No missing-cost-basis issues found" for a position
    bought before the data (MISSING_IN_TAXJSON, or a partial history
    with a positive book position) and for one typed under another
    spelling. Each kind of difference now names the fix that works for
    it: an opening balance, the books' symbol, find-missing-history for
    a position the books hold short."""

    TT = ("BUYSELL 2025-01-06 10:00:00 ZZA.TO 30 CAD 10 -300 0\n"
          "BUYSELL 2025-01-06 10:00:00 ZZB.TO 75 CAD 10 -750 0\n"
          "BUYSELL 2025-02-03 10:00:00 ZZS.TO -5 CAD 10 50 0\n")

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.p = _project(Path(cls._td.name) / "p",
                         files={"margin/t.tt": cls.TT})
        r = _tjs(cls.p, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _sanity(self, holdings):
        h = self.p / "h.toml"
        h.write_text("".join(
            f'[[holding]]\nsymbol = "{s}"\nquantity = {q}\n'
            for s, q in holdings))
        r = _tjs(self.p, "sanity", f"margin={h}")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        return [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]

    def test_a_position_bought_before_the_data_names_an_opening(self):
        acts = self._sanity([("ZZA.TO", 30), ("ZZB.TO", 75),
                             ("ZZQ.TO", 10), ("ZZS.TO", 0)])
        self.assertEqual(len(acts), 2, acts)
        self.assertIn("ZZQ.TO", acts[1])
        self.assertTrue(acts[1].endswith("— tjs opening margin FILE"),
                        acts)
        self.assertNotIn("find-missing-history", acts[1])
        # ZZS.TO: sold with no purchase, short in the books.
        self.assertIn("ZZS.TO", acts[0])
        self.assertTrue(acts[0].endswith("— tjs find-missing-history"),
                        acts)
        self.assertTrue(all(len(a) <= 100 for a in acts), acts)

    def test_a_partial_history_names_an_opening_too(self):
        acts = self._sanity([("ZZA.TO", 40), ("ZZB.TO", 75)])
        self.assertEqual(len(acts), 2, acts)
        self.assertIn("ZZA.TO", acts[1])
        self.assertTrue(acts[1].endswith("— tjs opening margin FILE"),
                        acts)

    def test_the_same_quantity_under_another_symbol_is_a_spelling(self):
        acts = self._sanity([("ZZA.TO", 30), ("ZZB", 75)])
        self.assertEqual(len(acts), 2, acts)
        spell = [a for a in acts if "symbol spelling" in a]
        self.assertEqual(len(spell), 1, acts)
        self.assertIn("ZZB / ZZB.TO", spell[0])
        self.assertTrue(spell[0].endswith("— tjs list"), acts)
        self.assertFalse(any("opening" in a for a in acts), acts)


@unittest.skipIf(os.name == "nt", "POSIX permissions")
class TestNoPermissionWarningUnderAnOwnerOnlyFolder(unittest.TestCase):
    """TJ-0426: `mkdir inputs/slips` (where the docs put the slips) made
    the year's inputs/ 0755 under a 0700 year folder, and every run
    warned that other users could read it. Nobody else passes through
    the 0700 folder above it: no warning, until that folder lets them."""

    def test_quiet_under_0700_warned_once_reachable(self):
        with tempfile.TemporaryDirectory() as td:
            w = Path(td) / "w"
            r = _tjs(None, "init", "--country", "canada", "--year",
                     "2024", str(w))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            year = w / "2024"
            self.assertTrue((year / "taxjson.toml").is_file())
            self.assertEqual(year.stat().st_mode & 0o077, 0)
            slips = year / "inputs" / "slips"
            slips.mkdir(parents=True)
            for d in (year / "inputs", slips):
                d.chmod(0o755)
            r = _tjs(year, "run", "--no-input")
            self.assertNotIn("other users", r.stderr + r.stdout)
            # The year folder others can pass through: inputs/ is theirs
            # to read, and said once.
            year.chmod(0o711)
            w.chmod(0o711)
            r = _tjs(year, "run", "--no-input")
            self.assertEqual(r.stderr.count("can be read by other users"),
                             1, r.stderr)
            self.assertIn("inputs/ can be read by other users", r.stderr)
            # The folder holding the year folders shut again: quiet.
            w.chmod(0o700)
            r = _tjs(year, "run", "--no-input")
            self.assertNotIn("other users", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
