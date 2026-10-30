"""Regression tests for fix round 7 (wave D), group us: the US engine and
its reports.

TJ-1024 §263(h): a short's compensation fell wholly on the shares already
        covered while the rest of the short was still open.
TJ-1059 a cover §263(h) turned into a loss skipped the wash-sale rule.
TJ-1065 compensation on a short still open and inside its 45 days was
        deducted with no word; the filed year then drifted.
TJ-1060 the estimate deducted compensation paid on a short when the same
        security also received a payment in lieu that year.
TJ-1062 the rest of a sub-micro position after a partial sale lost its cost.
TJ-1061 a [[distributions]] ROC stamped before a straddling sale got a false
        "record date not plausible" warning.
TJ-1029 tjs audit and the standalone taxjson-form-export ignored a SECFUT line.
TJ-1094 sanity's income check replayed US books in the Canada order.
TJ-1066 dil / dil-sum listed capitalized compensation as ordinary income;
        reconcile-slips did not name it as the cost difference.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import tx


def _gains(book, year=2025, **req):
    """pipeline.run_gains on a US book (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), [], [],
                        req=GainsRequest(country="usa", taxable=True,
                                         year=year, phantom_hint=False,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _paid(date, sym, amount):
    return tx("DIVIDEND_IN_LIEU", date, sym, 100, amount,
              gross_amount=amount, type="dividend_in_lieu")


def _covers(res):
    return [t for t in res["transactions"]
            if t.get("qty") and t.get("direction") == "SHORT"]


SRC = Path(__file__).resolve().parent.parent / "src"


def _tjs(root, *args, ok=True, **env_more):
    from taxjson.lib.dispatch import python_module_argv
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0", **env_more)
    env.pop("TAXJSON_TICKER_MAP", None)
    env.update(env_more)
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)
    if ok and r.returncode != 0:
        raise AssertionError(f"{args}: {r.stderr[-2000:]}{r.stdout[-500:]}")
    return r


def _project(td, rows, *, year=2024, csv=None, more_toml="", ticker_map=""):
    root = Path(td)
    (root / "taxjson.toml").write_text(
        f'[settings]\ncountry = "usa"\nyear = {year}\n'
        'base_currency = "USD"\nsource_currencies = []\n'
        'local_timezone = "America/New_York"\n\n'
        '[accounts.margin]\ntype = "taxable"\n' + more_toml)
    (root / "ticker.map").write_text(ticker_map)
    d = root / "inputs" / "margin"
    d.mkdir(parents=True)
    (d / "book.tt").write_text("\n".join(rows) + "\n")
    if csv:
        (d / "book.csv").write_text(csv)
    return root


def _pil(res):
    return [t for t in res["transactions"]
            if t.get("action") == "DIVIDEND_IN_LIEU"][0]


# --------------------------------------------------------------- TJ-1029
class TestSecurityFutureInAuditAndStandaloneForm(unittest.TestCase):
    """SECFUT ZQF: a security future. A loss replaced within 30 days is
    a wash sale everywhere, and both sales are on Form 8949."""

    ROWS = ["BUYSELL 2025-08-03 10:00:00 F:ZQFZ5.US 1 USD 100.00 10000.00 0 x100",
            "BUYSELL 2025-08-10 10:00:00 F:ZQFZ5.US -1 USD 99.00 9900.00 0 x100",
            "BUYSELL 2025-08-25 10:00:00 F:ZQFZ5.US 1 USD 98.00 9800.00 0 x100",
            "BUYSELL 2025-09-15 10:00:00 F:ZQFZ5.US -1 USD 99.50 9950.00 0 x100"]

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = _project(self._td.name, self.ROWS, year=2025,
                             ticker_map="SECFUT ZQF\n")
        _tjs(self.root, "run", "--no-input")

    @rule("US-FUT-02")
    def test_audit_reads_the_project_map(self):
        r = _tjs(self.root, "audit", ok=False)
        text = " ".join((r.stdout + r.stderr).split())
        self.assertEqual(r.returncode, 0, text[-1500:])
        self.assertNotIn("MISMATCH ", text.replace("0 MISMATCH", ""))
        self.assertNotIn("futures_vs_loss", text)

    @rule("US-FUT-02")
    def test_standalone_form_export_takes_the_map(self):
        from taxjson.lib.dispatch import python_module_argv
        argv = python_module_argv("taxjson.bin.taxjson_form_export") + [
            "--form", "8949", "--country", "usa", "--json",
            str(self.root / "work" / "margin_gains_wash.json")]
        env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_WIDTH="0")
        env.pop("TAXJSON_TICKER_MAP", None)

        def form(*more):
            r = subprocess.run(argv + list(more), capture_output=True,
                               text=True, env=env,
                               stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            return json.loads(r.stdout)
        without = form()
        self.assertFalse(without["part_I"])
        withmap = form("--ticker-map", str(self.root / "ticker.map"))
        self.assertEqual(len(withmap["part_I"]), 2)
        self.assertFalse((withmap.get("section_1256") or []))
        r = subprocess.run(argv + ["--ticker-map", str(self.root / "no.map")],
                           capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=300)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no such file", r.stderr)


# --------------------------------------------------------------- TJ-1062
class TestSubMicroPositionSoldInParts(unittest.TestCase):
    """A sub-micro position with a real cost sold in two halves: two
    gains of 50, nothing held after, no phantom short. Canada's pools."""

    def _book(self, consolidate):
        first = ([tx("BUYSELL", "2025-01-06", "ZQD.US", 0.0005, 500.0),
                  tx("SPLIT", "2025-06-02", "ZQD.US", 0.001)]
                 if consolidate else
                 [tx("BUYSELL", "2025-01-06", "ZQD.US", 0.0000005, 500.0)])
        return first + [
            tx("BUYSELL", "2025-06-03", "ZQD.US", -0.00000025, 300.0),
            tx("BUYSELL", "2025-07-03", "ZQD.US", -0.00000025, 300.0)]

    def _canada(self, book):
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            res = run_gains(book, [], [], req=GainsRequest(
                country="canada", taxable=True, year=2025,
                phantom_hint=False))
        return res, err.getvalue()

    @rule("CA-ACB-01")
    def test_both_halves_are_realized(self):
        for consolidate in (True, False):
            res, err = self._canada(self._book(consolidate))
            self.assertAlmostEqual(res["summary"]["total_gain"], 100.0,
                                   places=4)
            self.assertEqual(res["inventory"], [])
            self.assertNotIn("stranded", err)

    @rule("CA-ACB-01")
    def test_the_rest_is_held_between_the_sales(self):
        book = self._book(False)[:-1]
        res, _err = self._canada(book)
        held = {i["symbol"]: i for i in res["inventory"]}
        self.assertAlmostEqual(held["ZQD.US"]["total_cost"], 250.0, places=4)

    def test_a_sale_of_a_real_position_still_drains_its_residue(self):
        res, _err = self._canada(
            [tx("BUYSELL", "2025-01-06", "ZQE.US", 1.0, 100.0),
             tx("BUYSELL", "2025-06-03", "ZQE.US", -0.9999999, 120.0)])
        self.assertEqual(res["inventory"], [])


# --------------------------------------------------------------- TJ-1061
class TestDistributionRocStampedBeforeItsRecordDate(unittest.TestCase):
    """A [[distributions]] entry's ADJUST is stamped the day before a
    sale that straddles the record date: taxjson's own date, nothing to
    check in the export or the T3."""

    def _project(self, td, straddle=True):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\ncountry = "canada"\nyear = 2025\n'
            'base_currency = "CAD"\n'
            'local_timezone = "America/Toronto"\n\n'
            '[accounts.a]\ntype = "taxable"\n\n'
            '[[distributions]]\nsymbol = "QQZU.UN.TO"\n'
            'record_date = 2025-06-30\nper_share = -0.10\n')
        (root / "ticker.map").write_text("")
        d = root / "inputs" / "a"
        d.mkdir(parents=True)
        (d / "book.csv").write_text(
            "Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Account #,Activity Type,Account Type\n"
            "2025-03-03 12:00:00 AM,2025-03-04 12:00:00 AM,Buy,QQZU.UN.TO,"
            "QQZU TRUST UNITS,100.00000,10.00000,-1000.00,0.00,-1000.00,"
            "CAD,99900001,Trades,Individual margin\n"
            + ("2025-06-30 12:00:00 AM,2025-07-02 12:00:00 AM,Sell,QQZU.UN.TO,"
               "QQZU TRUST UNITS,-40.00000,10.00000,400.00,0.00,400.00,"
               "CAD,99900001,Trades,Individual margin\n" if straddle else ""))
        return root

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_no_false_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            r = _tjs(root, "run", "--no-input")
            text = " ".join((r.stdout + r.stderr).split())
            self.assertNotIn("not a plausible declaration", text)
            self.assertIn("return of capital (ACB down)", text)

    def test_a_broker_roc_with_a_far_record_date_still_warns(self):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules("canada")
        row = TaxTransaction(action="ADJUST", date="2025-06-29",
                             date_settle="2025-06-29", symbol="QQZU.UN.TO",
                             quantity=0.0, net_amount=-10.0, currency="CAD",
                             account="a", record_date="2025-06-30",
                             type="roc", id="ROW-1")
        said = rules._ca_year_warnings(row, "2025-06-29", "QQZU.UN.TO",
                                       "ADJUST", 2025)
        self.assertTrue(any("not a plausible" in w for w in said), said)
        row.type, row.id = "dist", "DIST-QQZU-2025-06-30"
        said = rules._ca_year_warnings(row, "2025-06-29", "QQZU.UN.TO",
                                       "ADJUST", 2025)
        self.assertEqual(said, [])


# --------------------------------------------------------------- TJ-1094
class TestSanityIncomeCheckKeepsTheUsOrder(unittest.TestCase):
    """10 SYN.US; on one day 5 bought at 09:30 and a 1:2 split stamped
    20:25; a dividend later says ON 10 SHS. The US books hold 7.5, and
    the check says so; the Canada order (a split at the open) holds 10."""

    ROWS = [{"action": "BUYSELL", "date": "2025-01-02", "time": "09:30:00",
             "date_settle": "2025-01-03", "symbol": "SYN.US", "quantity": 10,
             "currency": "USD", "net_amount": 100},
            {"action": "SPLIT", "date": "2025-06-02", "time": "20:25:00",
             "date_settle": "2025-06-02", "symbol": "SYN.US",
             "symbol_new": "SYN.US", "quantity": 0.5},
            {"action": "BUYSELL", "date": "2025-06-02", "time": "09:30:00",
             "date_settle": "2025-06-03", "symbol": "SYN.US", "quantity": 5,
             "currency": "USD", "net_amount": 100},
            {"action": "DIVIDEND", "date": "2025-06-30", "time": "00:00:00",
             "date_settle": "2025-06-30", "symbol": "SYN.US", "quantity": 10,
             "currency": "USD", "net_amount": 1.0, "gross_amount": 1.0,
             "record_date": "2025-06-16",
             "description": "SYN CORP CASH DIV ON 10 SHS"}]

    def test_held_as_follows_the_clock(self):
        from taxjson.lib import positions_check as PC
        self.assertEqual(PC.held_as(self.ROWS, "SYN.US", "2025-06-16",
                                    settled=True, clock=True), 7.5)
        self.assertEqual(PC.held_as(self.ROWS, "SYN.US", "2025-06-16",
                                    settled=True), 10.0)

    def test_the_check_lists_the_us_books_count(self):
        from taxjson.lib import positions_check as PC
        got = PC.income_share_mismatches(self.ROWS, "margin", clock=True)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["books_shares"], 7.5)
        self.assertEqual(PC.income_share_mismatches(self.ROWS, "margin"), [])

    def test_tjs_sanity_in_a_us_project(self):
        for snapshot in (True, False):
            with tempfile.TemporaryDirectory() as td:
                root = _project(td, [
                    "BUYSELL 2025-01-02 09:30:00 SYN.US 10 USD 10 100 0",
                    "SPLIT 2025-06-02 20:25:00 SYN.US SYN.US 0.5",
                    "BUYSELL 2025-06-02 09:30:00 SYN.US 5 USD 20 100 0",
                ], year=2025, csv=None)
                (root / "inputs" / "margin" / "rbc.csv").write_text(
                    "Account Activity Detail Report\n"
                    '"Account Number: 99900099"\n\n'
                    "Date,Activity,Symbol,Description,Quantity,Price,"
                    "Settlement Date,Currency,Value,Amount\n"
                    "06/30/2025,Dividends,SYN,SYN CORP CASH DIV ON 10 SHS "
                    "REC 06/16/25 PAY 06/30/25,0,0,06/30/2025,USD,1.00,1.00\n")
                if snapshot:
                    h = root / "holdings"
                    h.mkdir()
                    (h / "margin_holdings.toml").write_text(
                        '[meta]\naccount = "margin"\n'
                        'as_of = "2025-06-30"\n\n[[holding]]\n'
                        'symbol = "SYN.US"\nquantity = 7.5\n'
                        'currency = "USD"\n')
                _tjs(root, "run", "--no-input", ok=False)
                r = _tjs(root, "sanity", "--json", ok=False)
                doc = json.loads(r.stdout)
                got = doc["income_share_mismatches"]
                self.assertEqual([(m["stated_shares"], m["books_shares"])
                                  for m in got], [(10.0, 7.5)], r.stderr[-800:])


# --------------------------------------------------------------- TJ-1024
class TestCompensationOnAPartlyCoveredShort(unittest.TestCase):
    """100 short for 1000, 20 paid while all 100 are short, 50 covered
    within 45 days for 450. Only those 50 carry their half."""

    def _book(self, *more):
        return [tx("BUYSELL", "2025-01-06", "ZQSH.US", -100, 1000.0),
                _paid("2025-01-15", "ZQSH.US", -20.0),
                tx("BUYSELL", "2025-01-21", "ZQSH.US", 50, 450.0),
                *more]

    @rule("US-INC-04")
    def test_the_rest_still_open_keeps_its_share(self):
        res = _gains(self._book())
        cover = _covers(res)[0]
        self.assertAlmostEqual(cover["gain"], 40.0)
        self.assertAlmostEqual(cover["proceeds"], -460.0)
        self.assertAlmostEqual(_pil(res)["pil"], -10.0)
        self.assertAlmostEqual(_pil(res)["capitalized_263h"], 10.0)

    @rule("US-INC-04")
    def test_the_rest_covered_late_is_the_same(self):
        res = _gains(self._book(
            tx("BUYSELL", "2025-04-30", "ZQSH.US", 50, 450.0)))
        first, second = _covers(res)
        self.assertAlmostEqual(first["gain"], 40.0)
        self.assertAlmostEqual(second["gain"], 50.0)
        self.assertNotIn("capitalized_263h", second)
        self.assertAlmostEqual(_pil(res)["pil"], -10.0)

    @rule("US-INC-04")
    def test_a_short_opened_after_the_payment_takes_no_share(self):
        res = _gains([*self._book(),
                      tx("BUYSELL", "2025-02-03", "ZQSH.US", -300, 3000.0)])
        self.assertAlmostEqual(_pil(res)["capitalized_263h"], 10.0)
        self.assertAlmostEqual(_covers(res)[0]["gain"], 40.0)

    @rule("US-INC-04")
    def test_a_split_after_the_cover_counts_shares_in_one_unit(self):
        """The 50 still open are 100 at the end of the books: still half
        of the short on the pay date."""
        res = _gains(self._book(tx("SPLIT", "2025-06-02", "ZQSH.US", 2)))
        self.assertAlmostEqual(_pil(res)["capitalized_263h"], 10.0)
        self.assertAlmostEqual(_covers(res)[0]["gain"], 40.0)

    @rule("US-INC-04")
    def test_a_split_between_payment_and_cover(self):
        """All 100 (200 after the split) covered within 45 days: all of
        the 20 on the cover's shares, whatever their unit."""
        res = _gains([tx("BUYSELL", "2025-01-06", "ZQSH.US", -100, 1000.0),
                      _paid("2025-01-15", "ZQSH.US", -20.0),
                      tx("SPLIT", "2025-01-20", "ZQSH.US", 2),
                      tx("BUYSELL", "2025-01-21", "ZQSH.US", 200, 900.0)])
        self.assertAlmostEqual(_covers(res)[0]["gain"], 80.0)
        self.assertAlmostEqual(_pil(res)["pil"], 0.0)

    def test_books_are_unchanged_without_a_payment(self):
        res = _gains([tx("BUYSELL", "2025-01-06", "ZQSH.US", -100, 1000.0),
                      tx("BUYSELL", "2025-01-21", "ZQSH.US", 50, 450.0)])
        self.assertAlmostEqual(_covers(res)[0]["gain"], 50.0)


# --------------------------------------------------------------- TJ-1059
class TestCapitalizedLossIsTestedForAWashSale(unittest.TestCase):
    """100 short for 1000; 20 paid; covered within 45 days for 990: a
    loss of 10 only because of the 20. Shorted again within 30 days."""

    def _book(self, paid=True, cover=990.0):
        return [tx("BUYSELL", "2025-12-01", "ZQW.US", -100, 1000.0),
                *([_paid("2025-12-10", "ZQW.US", -20.0)] if paid else []),
                tx("BUYSELL", "2025-12-15", "ZQW.US", 100, cover),
                tx("BUYSELL", "2025-12-22", "ZQW.US", -100, 1000.0),
                tx("BUYSELL", "2026-02-27", "ZQW.US", 100, 1000.0)]

    @rule("US-INC-04")
    def test_the_loss_is_deferred_into_the_re_short(self):
        res = _gains(self._book())
        cover = _covers(res)[0]
        self.assertAlmostEqual(cover["raw_gain"], -10.0)
        self.assertAlmostEqual(cover["disallowed_amount"], 10.0)
        self.assertAlmostEqual(cover["gain"], 0.0)
        self.assertAlmostEqual(res["summary"]["total_gain"], 0.0)
        self.assertAlmostEqual(cover["capitalized_263h"], 20.0)

    def test_same_as_a_plain_loss_of_that_size(self):
        with_paid = _gains(self._book())
        plain = _gains(self._book(paid=False, cover=1010.0))
        self.assertAlmostEqual(with_paid["summary"]["total_gain"],
                               plain["summary"]["total_gain"])
        next_year = _gains(self._book(), year=2026)
        plain_next = _gains(self._book(paid=False, cover=1010.0), year=2026)
        self.assertAlmostEqual(_covers(next_year)[-1]["gain"],
                               _covers(plain_next)[-1]["gain"])

    @rule("US-INC-04")
    def test_a_washed_cover_takes_the_payment_too(self):
        """A cover already at a loss: the whole loss, its capitalized
        part with it, goes to the re-short. No manual move is asked."""
        res = _gains(self._book(cover=1010.0))
        cover = _covers(res)[0]
        self.assertAlmostEqual(cover["raw_gain"], -30.0)
        self.assertAlmostEqual(cover["disallowed_amount"], 30.0)
        self.assertAlmostEqual(_pil(res)["pil"], 0.0)
        self.assertNotIn("by hand", res["_stderr"])

    def test_each_note_is_said_once(self):
        res = _gains(self._book())
        self.assertEqual(res["_stderr"].count("added to the cover's cost"), 1)


# --------------------------------------------------------------- TJ-1065
class TestCompensationOnAShortStillOpen(unittest.TestCase):
    MARK = "dividend compensation not settled"

    def _book(self, *more):
        return [tx("BUYSELL", "2024-12-16", "ZRS.US", -30, 2999.0),
                _paid("2024-12-20", "ZRS.US", -15.0), *more]

    @rule("US-INC-04")
    def test_inside_the_45_days_the_run_says_so(self):
        res = _gains(self._book(), year=2024)
        self.assertIn(self.MARK, res["_stderr"])
        for word in ("ZRS.US", "2024-12-20", "2024-12-16", "2025-01-30"):
            self.assertIn(word, res["_stderr"])
        self.assertAlmostEqual(_pil(res)["pil"], -15.0)

    @rule("US-INC-04")
    def test_past_the_45th_day_it_is_settled(self):
        res = _gains(self._book(
            tx("BUYSELL", "2025-02-03", "ZOTHER.US", 1, 10.0)), year=2024)
        self.assertNotIn(self.MARK, res["_stderr"])
        self.assertAlmostEqual(_pil(res)["pil"], -15.0)

    @rule("US-INC-04")
    def test_covered_in_time_it_is_settled(self):
        res = _gains(self._book(
            tx("BUYSELL", "2025-01-10", "ZRS.US", 30, 2851.0)), year=2024)
        self.assertNotIn(self.MARK, res["_stderr"])
        self.assertAlmostEqual(_pil(res)["pil"], 0.0)

    @rule("US-INC-04")
    def test_readiness_and_the_checklist_say_so(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, [
                "BUYSELL 2024-12-16 10:00:00 ZRS.US -30 USD 100.00 2999.00 1",
                "DIVIDEND_IN_LIEU 2024-12-20 09:30:00 ZRS.US 0 USD 0 -15.00",
            ])
            _tjs(root, "run", "--no-input")
            doc = json.loads(_tjs(root, "sum", "--json").stdout)
            kinds = [i["kind"] for i in doc["readiness"]["issues"]]
            self.assertIn("compensation-not-settled", kinds)
            self.assertFalse(doc["readiness"]["complete"])
            shown = _tjs(root, "sum")
            self.assertIn("not settled", " ".join(
                (shown.stdout + shown.stderr).split()))
            doc = json.loads(_tjs(root, "form-export", "--json").stdout)
            kinds = [i["kind"] for i in doc["readiness"]["issues"]]
            self.assertIn("compensation-not-settled", kinds)
            from taxjson.lib import checklist as cl
            from taxjson.bin.taxjson_run import load_config
            import datetime
            cwd = os.getcwd()
            env = {k: os.environ.get(k) for k in
                   ("PYTHONPATH", "TAXJSON_OFFLINE", "TAXJSON_WIDTH")}
            os.environ.update(PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                              TAXJSON_WIDTH="0")
            try:
                cfg = load_config(root)
                ctx = cl.Ctx(root=root, cfg=cfg, year=2024,
                             today=datetime.date(2025, 1, 2),
                             run_sub=cl.default_run_sub(root))
                r = cl.evaluate(ctx, only=["form-export"])[0]
            finally:
                os.chdir(cwd)
                for k, v in env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(r.status, "attention", r.detail)
            self.assertIn("2024-12-20", r.detail)
            r = _tjs(root, "close-year", "--yes", ok=False)
            self.assertIn("not settled", " ".join(
                (r.stdout + r.stderr).split()))


# --------------------------------------------------------------- TJ-1060
class TestEstimateWithReceivedAndPaidCompensation(unittest.TestCase):
    """+30 received on a long, -20 paid on a later short held past 45
    days: only the 30 is income, as on the received-only estimate."""

    ROWS = ["BUYSELL 2025-01-02 10:00:00 ZQPN.US 100 USD 10 1000 0",
            "BUYSELL 2025-03-03 10:00:00 ZQPN.US -100 USD 11 1100 0",
            "BUYSELL 2025-05-01 10:00:00 ZQPN.US -100 USD 10 1000 0",
            "BUYSELL 2025-09-02 10:00:00 ZQPN.US 100 USD 9 900 0"]
    HEAD = ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers LLC\n"
            "Dividends,Header,Currency,Account,Date,Description,Amount\n")
    LINE = ("Dividends,Data,USD,U1234567,{},ZQPN(US9990000002) Payment in "
            "Lieu of Dividend USD 0.2 per Share,{}\n")

    def _estimate(self, *payments):
        with tempfile.TemporaryDirectory() as td:
            root = _project(
                td, self.ROWS, year=2025,
                csv=self.HEAD + "".join(self.LINE.format(d, a)
                                        for d, a in payments),
                more_toml="\n[estimate]\nother_income = 100000\n")
            _tjs(root, "run", "--no-input")
            return json.loads(_tjs(root, "estimate", "--json").stdout)[
                "estimate"]

    @rule("US-INC-04")
    def test_paid_is_not_netted_against_received(self):
        both = self._estimate(("2025-02-14", 30), ("2025-06-16", -20))
        received = self._estimate(("2025-02-14", 30))
        self.assertEqual(both["estimated_tax"], received["estimated_tax"])
        self.assertEqual(both["pil_paid_not_deducted"], 20.0)

    def test_a_reversal_of_a_received_payment_still_nets(self):
        """Long the shares on both rows: nothing paid on a short."""
        both = self._estimate(("2025-02-14", 30), ("2025-02-20", -30))
        none = self._estimate(("2025-02-14", 0.0))
        self.assertEqual(both["estimated_tax"], none["estimated_tax"])
        self.assertNotIn("pil_paid_not_deducted", both)


# --------------------------------------------------------------- TJ-1066
class TestCapitalizedCompensationInTheViews(unittest.TestCase):
    ROWS = ["BUYSELL 2024-02-01 10:00:00 ZRS.US -30 USD 100.00 2999.00 1",
            "DIVIDEND_IN_LIEU 2024-02-20 09:30:00 ZRS.US 0 USD 0 -15.00",
            "BUYSELL 2024-03-01 10:00:00 ZRS.US 30 USD 95.00 2851.00 1"]

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = _project(self._td.name, self.ROWS)
        _tjs(self.root, "run", "--no-input")

    def test_dil_sum_shows_the_treatment(self):
        out = " ".join(_tjs(self.root, "dil-sum").stdout.split())
        self.assertIn("capitalized(§263(h))", out)
        self.assertIn("TOTAL DIVIDEND IN LIEU: 0.00", out)
        self.assertNotIn("-15.00 1 ordinary", out)
        self.assertIn("CAPITALIZED (§263(h), in the cover's cost): -15.00",
                      out)
        doc = json.loads(_tjs(self.root, "dil-sum", "--json").stdout)
        self.assertEqual(doc["totals_capitalized"], {"USD": -15.0})
        self.assertFalse(doc["totals_ordinary"])
        self.assertEqual(doc["totals"].get("USD", 0.0), 0.0)
        self.assertEqual([r["treatment"] for r in doc["rows"]],
                         ["capitalized"])

    def test_dil_keeps_the_row_and_moves_the_total(self):
        out = " ".join(_tjs(self.root, "dil").stdout.split())
        self.assertIn("-15.00", out)
        self.assertIn("CAPITALIZED (§263(h), in the cover's cost): -15.00",
                      out)
        doc = json.loads(_tjs(self.root, "dil", "--json").stdout)
        self.assertEqual(doc["totals"]["capitalized_263h"], {"USD": -15.0})
        self.assertEqual(len(doc["rows"]), 1)

    def test_dil_sum_matches_sum(self):
        doc = json.loads(_tjs(self.root, "dil-sum", "--json").stdout)
        rows = json.loads(
            (self.root / "work" / "margin_gains_wash.json").read_text()
        )["transactions"]
        pil = sum(float(t.get("pil") or 0) for t in rows)
        self.assertAlmostEqual(doc["totals"].get("USD", 0.0), pil)

    def test_reconcile_slips_names_it(self):
        slip = self.root / "slip.csv"
        slip.write_text("Symbol,Quantity,Proceeds,Cost or other basis\n"
                        "ZRS,30,2999.00,2851.00\n")
        r = _tjs(self.root, "reconcile-slips", str(slip), ok=False)
        out = " ".join((r.stdout + r.stderr).split())
        self.assertIn("slip cost differs by -15.00, §263(h) compensation",
                      out)
        r = _tjs(self.root, "reconcile-slips", str(slip), "--details",
                 ok=False)
        out = " ".join((r.stdout + r.stderr).split())
        self.assertIn("the dividend compensation added to the cover's cost",
                      out)
        self.assertNotIn("a wash sale across accounts", out)


if __name__ == "__main__":
    unittest.main()
