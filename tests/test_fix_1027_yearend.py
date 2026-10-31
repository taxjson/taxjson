"""The tjs command audit of 2026-10-27, year-end records group: close-year,
handoff, carryover and check-filed agree with the run.

- TJ-0296 US `carryover` reads the filed locks before its gains run: a
  wash-sale basis add whose replacement was sold in a locked year is
  booked in the loss's year, as the pipeline books it (US-WASH-22);
- TJ-0323 US `carryover` names a crypto account's unknown-cost sales
  (warning, --json `excluded_unknown_cost` / `incomplete_years`) and
  counts its rows in `first_transaction_year`;
- TJ-0297 a US crypto account's close-year record (handoff.snapshot) is
  computed without the wash-sale rule, as the run computes it;
- TJ-0285 (TJ-0324) Canada: the record's Dec 31 `deferred` is what the
  sales before Dec 31 left of each superficial-loss addition;
- TJ-0298 close-year refuses a --filed-dispositions row of another year,
  and a replaced lock's kept row of another year, before any write;
- TJ-0274 (TJ-0336) a lock's `return_row` is checked for its shape like
  every other locked figure; check-filed's OK says it is not recomputed.

Synthetic data only (made-up tickers and accounts, amounts under a
thousand).
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib.core import TaxTransaction
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


def _cli(root, *args):
    """`tjs -C ROOT ARGS` as a child of THIS tree's code."""
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *map(str, args)])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env=_env(), timeout=900)


def _tx(acct, date, qty, net, action="BUYSELL", sym="ZZQ.US",
        currency="USD"):
    return TaxTransaction(action=action, date=date, date_settle=date,
                          symbol=sym, quantity=qty,
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=net, currency=currency, account=acct)


def _book(path: Path, rows) -> Path:
    path.write_text(json.dumps(
        {"transactions": [r.to_dict() for r in rows]}))
    return path


def _carryover(argv):
    """(rc, stdout, stderr) of taxjson-carryover in process."""
    from taxjson.bin.taxjson_carryover import main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


def _quiet_gains(rows, **req):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return run_gains(list(rows), [], [], req=GainsRequest(
            country="usa", taxable=True, **req))


# ------------------------------------------------------------- TJ-0296
# Another account buys and sells the replacement in December 2025; the
# loss is sold in January 2026 (US-WASH-22's case).
_CROSS_YEAR = [_tx("margin", "2025-01-02", 10, 500),
               _tx("other", "2025-12-15", 10, 300),
               _tx("other", "2025-12-17", -10, 310),
               _tx("margin", "2026-01-05", -10, 300)]


@rule("US-RPT-08", "US-WASH-22")
class TestUsCarryoverLockedYearWash(unittest.TestCase):
    """TJ-0296: the ledger ran its gains before reading the locks, so the
    add went to the locked 2025 sale, whose filed figure then replaced
    the year, and the 200.00 loss was in no year (2026 net 0.00)."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.p = Path(td.name)
        self.base = _book(self.p / "margin_base.json", _CROSS_YEAR)

    def test_locked_year_books_the_add_in_the_loss_year(self):
        from taxjson.bin.taxjson_filed import (aggregates_from_gains,
                                               write_snapshot)
        filed = _quiet_gains(_CROSS_YEAR, year=2025, locked_years=(2025,))
        run_2026 = _quiet_gains(_CROSS_YEAR, year=2026,
                                locked_years=(2025,))
        lock = write_snapshot(self.p, 2025, "usa", "wash-adjusted",
                              {"margin": aggregates_from_gains(
                                  filed, year=2025)}, force=False)
        rc, out, err = _carryover([
            str(self.base), "--country", "usa", "--project-year", "2026",
            "--filed-lock", f"2025={lock}", "--claimed-year", "2026=0",
            "--json"])
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        rows = {r["year"]: r for r in doc["rows"]}
        self.assertEqual(run_2026["summary"]["total_gain"], -200.0)
        self.assertAlmostEqual(rows[2026]["net_gain"], -200.0, places=2)
        self.assertAlmostEqual(doc["final_carryforward"], 200.0, places=2)
        # The locked year: its filed figure, and the books agree with it.
        self.assertTrue(rows[2025]["from_lock"])
        self.assertAlmostEqual(rows[2025]["net_gain"],
                               filed["summary"]["total_gain"], places=2)
        self.assertAlmostEqual(rows[2025]["rebuilt_net_gain"],
                               rows[2025]["net_gain"], places=2)
        self.assertIn("wash sale reaches a filed year", err)

    def test_unlocked_year_keeps_the_add_on_the_earlier_sale(self):
        """Parity without a lock: each year's net is the run's."""
        rc, out, err = _carryover([
            str(self.base), "--country", "usa", "--project-year", "2026",
            "--claimed-year", "2026=0", "--json"])
        self.assertEqual(rc, 0, err)
        rows = {r["year"]: r for r in json.loads(out)["rows"]}
        for y in (2025, 2026):
            want = _quiet_gains(_CROSS_YEAR, year=y)["summary"]["total_gain"]
            self.assertAlmostEqual(rows[y]["net_gain"], want, places=2,
                                   msg=str(y))
        self.assertAlmostEqual(rows[2025]["net_gain"], -190.0, places=2)


# ------------------------------------------------------------- TJ-0323
@rule("US-RPT-08")
class TestUsCarryoverCryptoUnknownCost(unittest.TestCase):
    """TJ-0323: the crypto pass's unknown-cost sale vanished: rows [],
    first_transaction_year null, nothing on stderr."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.book = _book(Path(td.name) / "coin_base.json", [
            _tx("coin", "2025-01-01", 2, 0, "OPENING_BALANCE", "ZZQC"),
            _tx("coin", "2025-02-01", -1, 100, sym="ZZQC")])

    def test_the_sale_is_named_and_the_year_incomplete(self):
        rc, out, err = _carryover([
            "--crypto", str(self.book), "--country", "usa",
            "--project-year", "2025", "--json"])
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual(doc["first_transaction_year"], 2025)
        self.assertEqual(doc["incomplete_years"], [2025])
        (ex,) = doc["excluded_unknown_cost"]
        self.assertEqual((ex["year"], ex["symbol"], ex["account"],
                          ex["qty"], ex["proceeds"]),
                         (2025, "ZZQC", "coin", 1.0, 100.0))
        self.assertIn("1 disposition(s) with an unknown cost", err)
        self.assertIn("EXCLUDED", err)

    def test_the_text_view_does_not_say_no_dispositions(self):
        rc, out, err = _carryover([
            "--crypto", str(self.book), "--country", "usa",
            "--project-year", "2025", "--brief"])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("No dispositions found", out)
        self.assertIn("No disposition with a known cost", out)

    def test_a_known_cost_year_is_not_marked(self):
        """A row of a complete year carries no `incomplete` mark."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        book = _book(Path(td.name) / "coin_base.json", [
            _tx("coin", "2024-03-01", 1, 50, sym="ZZQC"),
            _tx("coin", "2024-04-01", -1, 80, sym="ZZQC"),
            _tx("coin", "2025-01-01", 2, 0, "OPENING_BALANCE", "ZZQD"),
            _tx("coin", "2025-02-01", -1, 100, sym="ZZQD")])
        rc, out, err = _carryover([
            "--crypto", str(book), "--country", "usa",
            "--project-year", "2025", "--json"])
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        rows = {r["year"]: r for r in doc["rows"]}
        self.assertNotIn("incomplete", rows[2024])
        self.assertEqual(doc["incomplete_years"], [2025])
        self.assertEqual(doc["first_transaction_year"], 2024)


# ------------------------------------------------- snapshots (handoff)
def _snapshot(rows, country, crypto, acct):
    """handoff.snapshot of one taxable account at 2025-12-31, as
    close-year records it."""
    from taxjson.bin import taxjson_run as R
    from taxjson.lib import handoff
    us = country == "usa"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "work").mkdir()
        cfg = {"settings": {"year": 2025, "country": country,
                            "base_currency": "USD" if us else "CAD",
                            "tax_date": "trade" if us else "settle"},
               "accounts": {acct: {"type": "taxable", "crypto": crypto}}}
        _book(p / "work" / f"{acct}_base.json", rows)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return handoff.snapshot(p / "work", cfg, "2025-12-31",
                                    R._filed_run_gains,
                                    R._handoff_gains_flags(cfg["settings"]))


def _loss_and_repurchase(acct, sym):
    return [_tx(acct, "2025-10-01", 10, 500, sym=sym),
            _tx(acct, "2025-11-01", -10, 300, sym=sym),
            _tx(acct, "2025-11-10", 10, 300, sym=sym)]


@rule("US-WASH-13", "US-RPT-06")
class TestUsCryptoYearEndRecord(unittest.TestCase):
    """TJ-0297: the US snapshot ran every group WITH the wash rule, so a
    crypto loss and repurchase was recorded at 500.00 with 200.00
    deferred; the run (and the next year) hold it at 300.00."""

    def test_crypto_snapshot_has_no_wash_sale(self):
        snap = _snapshot(_loss_and_repurchase("coin", "ZZQC"), "usa",
                         True, "coin")
        self.assertEqual(snap["crypto"]["ZZQC"],
                         {"qty": 10.0, "acb": 300.0, "deferred": 0.0})

    def test_a_security_keeps_the_wash_sale(self):
        snap = _snapshot(_loss_and_repurchase("margin", "ZZQ.US"), "usa",
                         False, "margin")
        self.assertEqual(snap["equity"]["ZZQ.US"],
                         {"qty": 10.0, "acb": 500.0, "deferred": 200.0})


@rule("CA-RPT-08")
class TestCaDeferredAtYearEnd(unittest.TestCase):
    """TJ-0285 / TJ-0324: the record's `deferred` summed every addition
    placed by Dec 31; a sale before Dec 31 that recovered part of it did
    not reduce it (250.00 for the 100.00 the engine's pool still holds)."""

    def test_deferred_is_what_the_later_sale_left(self):
        rows = [_tx("margin", "2025-03-10", 10, 1000, sym="ZZQ.US",
                    currency="CAD"),
                _tx("margin", "2025-04-01", -5, 250, sym="ZZQ.US",
                    currency="CAD"),
                _tx("margin", "2025-10-01", -3, 300, sym="ZZQ.US",
                    currency="CAD")]
        snap = _snapshot(rows, "canada", False, "margin")
        # 5 held at day 30 deny the 250.00 loss; 3 of those 5 sold in
        # October take 3/5 of it: 2 units, ACB 300.00, 100.00 deferred.
        self.assertEqual(snap["equity"]["ZZQ.US"],
                         {"qty": 2.0, "acb": 300.0, "deferred": 100.0})

    def test_engine_year_end_agrees(self):
        """The same figures as the engine's own year-end pools."""
        from taxjson.lib.pipeline import GainsRequest, run_gains
        rows = [_tx("margin", "2025-03-10", 10, 1000, sym="ZZQ.US",
                    currency="CAD"),
                _tx("margin", "2025-04-01", -5, 250, sym="ZZQ.US",
                    currency="CAD"),
                _tx("margin", "2025-10-01", -3, 300, sym="ZZQ.US",
                    currency="CAD")]
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            res = run_gains(list(rows), [], [], req=GainsRequest(
                country="canada", year=2025, taxable=True))
        (h,) = res["inventory_year_end"]
        snap = _snapshot(rows, "canada", False, "margin")["equity"]["ZZQ.US"]
        self.assertAlmostEqual(snap["acb"], h["total_cost"], places=2)
        self.assertAlmostEqual(snap["deferred"], h["deferred_wash"],
                               places=2)


_US_TOML = ('[settings]\nyear = {year}\ncountry = "usa"\n'
            'base_currency = "USD"\nsource_currencies = []\n'
            'local_timezone = "America/New_York"\n{prior}'
            '[accounts.coin]\ntype = "taxable"\ncrypto = true\n')
_US_2025 = ("# made-up trades\n"
            "BUYSELL 2025-10-01 10:00:00 ZZQC 10 USD 50.00 -500.00 0.00\n"
            "BUYSELL 2025-11-01 10:00:00 ZZQC -10 USD 30.00 300.00 0.00\n"
            "BUYSELL 2025-11-10 10:00:00 ZZQC 10 USD 30.00 -300.00 0.00\n")


@rule("US-WASH-13", "US-RPT-06")
class TestUsCryptoCloseYearHandoff(unittest.TestCase):
    """TJ-0297 end to end: the 2025 lock records the repurchase at its
    300.00 cost, and a 2026 project that opens with that lot has no
    position difference to choose about."""

    def test_close_year_and_handoff(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        top = Path(td.name)
        old = top / "2025"
        (old / "inputs" / "coin").mkdir(parents=True)
        (old / "inputs" / "coin" / "trades.tt").write_text(_US_2025)
        (old / "taxjson.toml").write_text(
            _US_TOML.format(year=2025, prior=""))
        for args in (("run", "--no-input"), ("close-year", "--yes")):
            r = _cli(old, *args)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:]
                             + r.stderr[-2000:])
        lock = json.loads((old / "filed" / "2025.json").read_text())
        self.assertEqual(lock["year_end"]["crypto"]["ZZQC"],
                         {"qty": 10.0, "acb": 300.0, "deferred": 0.0})
        new = top / "2026"
        (new / "inputs" / "coin").mkdir(parents=True)
        (new / "inputs" / "coin" / "trades.tt").write_text(
            "# made-up trades\n"
            "BUYSELL 2025-11-10 10:00:00 ZZQC 10 USD 30.00 -300.00 0.00\n"
            "BUYSELL 2026-06-01 10:00:00 ZZQC -10 USD 40.00 400.00 0.00\n")
        (new / "taxjson.toml").write_text(_US_TOML.format(
            year=2026,
            prior=f'prior_year_record = "{old / "filed" / "2025.json"}"\n'))
        r = _cli(new, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        r = _cli(new, "handoff", "--json")
        rep = json.loads(r.stdout)
        self.assertEqual(rep["positions"], [], r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])


# ------------------------------------------------------------- TJ-0298
class TestFiledDispositionsYear(unittest.TestCase):
    """TJ-0298, the reader: a row dated outside the closing year."""

    def _csv(self, text):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "filed.csv"
        p.write_text(text)
        return p

    def test_another_years_row_is_refused_by_line(self):
        from taxjson.lib import handoff
        p = self._csv("symbol,date,qty,proceeds,cost,gain\n"
                      "ZZQ.TO,2025-06-02,10,200,100,100\n"
                      "ZZR.TO,2024-06-03,10,100,400,-300\n")
        with self.assertRaises(ValueError) as cm:
            handoff.load_filed_dispositions(p, 2025)
        msg = str(cm.exception)
        self.assertIn("line 3 (ZZR.TO 2024-06-03)", msg)
        self.assertIn("outside 2025", msg)
        # Without a year (a reader that is not closing one): as before.
        self.assertEqual(len(handoff.load_filed_dispositions(p)), 2)

    def test_rows_outside(self):
        from taxjson.lib import handoff
        self.assertEqual(handoff.filed_rows_outside(
            [{"symbol": "ZZQ.TO", "date": "2025-06-02"},
             {"symbol": "ZZR.TO", "date": "2024-06-03"},
             {"symbol": "ZZS.TO", "date": ""}], 2025),
            ["ZZR.TO 2024-06-03", "ZZS.TO "])


# ----------------------------------------------- the commands, end to end
_CA_TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            'province = "ON"\n\n[accounts.margin]\ntype = "taxable"\n')
_CA_TRADES = ("# made-up trades\n"
              "BUYSELL 2025-01-02 10:00:00 ZZQ.TO 10 CAD 10.00 -100.00 0.00\n"
              "BUYSELL 2025-06-02 10:00:00 ZZQ.TO -10 CAD 20.00 200.00 0.00\n")


class _CaProject(unittest.TestCase):
    """A Canadian 2025 project, run once; each test works on a copy."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = Path(cls._td.name) / "base"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "trades.tt").write_text(_CA_TRADES)
        (root / "taxjson.toml").write_text(_CA_TOML)
        r = _cli(root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.base = root

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        root = Path(t.name) / "p"
        shutil.copytree(self.base, root, symlinks=True)
        return root

    def ok(self, r):
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return r


@rule("CA-CARRY-01")
class TestCloseYearFiledDispositionsYear(_CaProject):
    """TJ-0298: close-year stored a 2024 row of the CSV in the 2025 lock,
    filed_totals.gain -200.00 and a 200.00 net capital loss carried
    into 2026, although the 2025 return's gain was 100.00."""

    def test_another_years_row_is_refused_before_any_write(self):
        root = self.copy()
        csv = root / "filed.csv"
        csv.write_text("symbol,date,qty,proceeds,cost,gain\n"
                       "ZZQ.TO,2025-06-02,10,200,100,100\n"
                       "ZZR.TO,2024-06-03,10,100,400,-300\n")
        toml = (root / "taxjson.toml").read_bytes()
        r = _cli(root, "close-year", "--yes", "--filed-dispositions", csv)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("line 3 (ZZR.TO 2024-06-03)", err)
        self.assertIn("Nothing was written.", err)
        self.assertFalse((root / "filed" / "2025.json").exists())
        self.assertEqual((root / "taxjson.toml").read_bytes(), toml)

    def test_the_years_own_rows_are_kept(self):
        root = self.copy()
        csv = root / "filed.csv"
        csv.write_text("symbol,date,qty,proceeds,cost,gain\n"
                       "ZZQ.TO,2025-06-02,10,200,100,100\n")
        self.ok(_cli(root, "close-year", "--yes", "--filed-dispositions",
                     csv))
        lock = json.loads((root / "filed" / "2025.json").read_text())
        self.assertEqual(lock["filed_totals"]["gain"], 100.0)
        self.assertEqual(
            lock["carryforwards"]["net_capital_loss"]["closing"], 0.0)

    def test_force_refuses_a_kept_row_of_another_year(self):
        """--force keeps the replaced lock's filed dispositions: one of
        another year is refused, naming it, and nothing is rewritten."""
        root = self.copy()
        csv = root / "filed.csv"
        csv.write_text("symbol,date,qty,proceeds,cost,gain\n"
                       "ZZQ.TO,2025-06-02,10,200,100,100\n")
        self.ok(_cli(root, "close-year", "--yes", "--filed-dispositions",
                     csv))
        lp = root / "filed" / "2025.json"
        lock = json.loads(lp.read_text())
        lock["filed_dispositions"].append(
            {"account": "", "symbol": "ZZR.TO", "date": "2024-06-03",
             "qty": 10.0, "proceeds": 100.0, "cost": 400.0,
             "gain": -300.0})
        lp.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
        before = lp.read_bytes()
        r = _cli(root, "close-year", "--yes", "--force")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        err = " ".join(r.stderr.split())
        self.assertIn("ZZR.TO 2024-06-03", err)
        self.assertIn("--filed-dispositions", err)
        self.assertEqual(lp.read_bytes(), before)


# ------------------------------------------------------------- TJ-0274
_LOCK = {"year": 2025, "country": "canada",
         "totals": {"realized": 100.0, "dispositions": 1},
         "return_row": {"realized": 100.0, "disallowed": 0.0},
         "accounts": {"margin": {"realized": 100.0, "dispositions": 1}}}


class TestReturnRowShape(unittest.TestCase):
    """TJ-0274 / TJ-0336: a malformed return_row passed check-filed, and
    `years` and the next year's carryover fell back in silence."""

    def test_shape_problem(self):
        from taxjson.bin.taxjson_filed import lock_shape_problem
        self.assertIsNone(lock_shape_problem(_LOCK, 2025))
        no_row = {k: v for k, v in _LOCK.items() if k != "return_row"}
        self.assertIsNone(lock_shape_problem(no_row, 2025))
        bad = dict(_LOCK, return_row={"realized": "broken"})
        self.assertIn("return_row.realized as 'broken'",
                      lock_shape_problem(bad, 2025))
        for v in (float("nan"), True, None):
            bad = dict(_LOCK, return_row={"realized": 1.0, "disallowed": v})
            self.assertIn("return_row.disallowed",
                          lock_shape_problem(bad, 2025), v)
        for v in ([1.0], "100", None):
            self.assertIn("return_row as",
                          lock_shape_problem(dict(_LOCK, return_row=v),
                                             2025), v)

    def test_every_reader_refuses_it(self):
        from taxjson.bin import taxjson_filed
        from taxjson.bin.taxjson_carryover import LockUnreadable, lock_figure
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "2025.json"
            p.write_text(json.dumps(dict(_LOCK, return_row={
                "realized": "broken", "disallowed": 0.0})))
            with self.assertRaises(taxjson_filed.LockProblem):
                taxjson_filed.read_lock(p, {"country": "canada"})
            with self.assertRaises(LockUnreadable):
                lock_figure(p, "canada")

    def test_ok_scope_names_it(self):
        from taxjson.bin.taxjson_filed import NOT_LOCKED
        self.assertIn("RETURN row (return_row) is not recomputed",
                      NOT_LOCKED)


class TestCheckFiledReturnRow(_CaProject):
    """TJ-0274 / TJ-0336 through check-filed."""

    def test_malformed_return_row_is_lock_damage(self):
        root = self.copy()
        self.ok(_cli(root, "close-year", "--yes"))
        ok = self.ok(_cli(root, "check-filed")).stdout
        self.assertIn("RETURN row (return_row) is not recomputed",
                      " ".join(ok.split()))
        lp = root / "filed" / "2025.json"
        lock = json.loads(lp.read_text())
        self.assertIn("return_row", lock)
        lock["return_row"]["realized"] = "broken"
        lp.write_text(json.dumps(lock))
        r = _cli(root, "check-filed")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("return_row.realized", " ".join(r.stderr.split()))


if __name__ == "__main__":
    unittest.main()
