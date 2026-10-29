"""Filing checks that reported success on missing or mismatched evidence
(the tjs command audit of 2026-10-27): TJ-0305 (slip-audit and its
checklist step passed while an account's books were not readable),
TJ-0306 (t1135 zeroed income and gains without the gains files), TJ-0307
(sanity complete after unreadable books left the coverage), TJ-0325
(reconcile-slips compared USD gains with CAD slips).
Synthetic data only: made-up tickers (ZZQ.*), amounts under 1,000."""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

import _hermetic  # noqa: F401  (a synthetic HOME, offline)
from tax_rules import rule
from taxjson.lib import checklist as cl
from taxjson.lib import out as _out
from taxjson.lib import slip_audit as SA
from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


def _env(**kw):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
               TAXJSON_WIDTH="0",
               PYTHONPATH=str(REPO / "src") + os.pathsep
               + os.environ.get("PYTHONPATH", ""))
    env.update(kw)
    return env


def _tjs(root, *argv):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *argv]),
        capture_output=True, text=True, env=_env(),
        stdin=subprocess.DEVNULL, check=False, timeout=600)


_CA = ('[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
       'source_currencies = []\n')


def _slip_project(tmp: Path) -> Path:
    """Account a: a 100.00 Canadian dividend and its T5 (box 24);
    account b: an input folder and no books (its run failed)."""
    root = tmp / "p"
    for d in ("work", "inputs/slips", "inputs/a", "inputs/b"):
        (root / d).mkdir(parents=True)
    (root / "inputs/b/trades.tt").write_text("# synthetic: its run failed\n")
    (root / "taxjson.toml").write_text(
        _CA + '[accounts.a]\ntype = "taxable"\n'
        '[accounts.b]\ntype = "taxable"\n')
    (root / "inputs/slips/slips.toml").write_text(
        'year = 2025\n[[slip]]\ntype = "T5"\naccount = "a"\n'
        'currency = "CAD"\nboxes = { 24 = 100.00 }\n')
    row = {"id": "income-a", "action": "DIVIDEND", "date": "2025-04-01",
           "symbol": "ZZQ.TO", "net_amount": 100.0, "gross_amount": 100.0,
           "currency": "CAD", "account": "a"}
    (root / "work/a_base.json").write_text(
        json.dumps({"transactions": [row]}))
    return root


def _cfg(root: Path) -> dict:
    from taxjson.bin.taxjson_run import load_config
    return load_config(root)


class TestTJ0305SlipAuditNotCompared(unittest.TestCase):
    """TJ-0305: an account with inputs and no readable books is not
    compared, so the audit is incomplete: never status ok, exit 0,
    "Every slip agrees" or a done checklist step."""

    def test_the_audit_says_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            root = _slip_project(Path(td))
            rep = SA.audit(root, _cfg(root))
            self.assertEqual(rep["issues"], [])
            self.assertEqual(rep["status"], "incomplete")
            self.assertEqual([(e["kind"], e["account"])
                              for e in rep["not_compared"]],
                             [("no-books", "b")])
            self.assertIn("b: no readable work/b_base.json",
                          rep["not_compared"][0]["text"])
            # (the problems list keeps every notice, as before)
            self.assertIn(rep["not_compared"][0]["text"], rep["problems"])
            text = "\n".join(SA.render(rep, width_=0))
            self.assertNotIn("Every slip agrees", text)
            self.assertIn("The slips compared agree", text)
            last = text.splitlines()[-1]
            self.assertTrue(last.startswith(_out.ACT), last)
            self.assertIn("(b) — tjs run", last)
            self.assertLessEqual(len(last), 100)

    def test_its_books_back_make_it_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = _slip_project(Path(td))
            (root / "work/b_base.json").write_text(
                json.dumps({"transactions": []}))
            rep = SA.audit(root, _cfg(root))
            self.assertEqual(rep["not_compared"], [])
            self.assertEqual(rep["status"], "ok")
            self.assertIn("Every slip agrees",
                          "\n".join(SA.render(rep, width_=0)))

    def test_the_command_exits_1(self):
        with tempfile.TemporaryDirectory() as td:
            root = _slip_project(Path(td))
            r = _tjs(root, "slip-audit", "--json")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            rep = json.loads(r.stdout)
            self.assertEqual(rep["status"], "incomplete")
            r = _tjs(root, "slip-audit")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertNotIn("Every slip agrees", r.stdout)
            self.assertIn("NOT COMPARED", r.stdout)

    def _step(self, root):
        ctx = cl.Ctx(root=root, cfg=_cfg(root), year=2025,
                     today=date(2026, 3, 1),
                     run_sub=lambda *a, **k: (0, "", ""))
        return cl.d_t5_t3(ctx)

    def test_the_checklist_step_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = _slip_project(Path(td))
            r = self._step(root)
            self.assertEqual(r.status, "blocked", r.detail)
            self.assertIn("b: no readable work/b_base.json", r.detail)
            # A DONE mark never outranks it.
            r.override = "done"
            self.assertEqual(r.effective, "blocked")

    def test_ib_inputs_left_uncompared(self):
        # An IB component taxjson does not know, with an amount, and a
        # report of an IB account no account's books carry: neither is
        # compared, so the audit is not ok and the step not done. A
        # second copy of a report stays a notice (test_fix_v0242_slips).
        from test_slip_audit import IB_ACCT, _home, _ib_project, _ib_report
        from test_slip_audit import tj as tj_home
        report = (_ib_report()
                  .replace("20250924,100,,,40,40,28.8,",
                           "20250924,100,,,45,45,32.4,")
                  .replace("T5: Mystery Income,Not Qualified,0,0,0,",
                           "T5: Mystery Income,Not Qualified,5,5,3.6,"))
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp, report=report)
            other = IB_ACCT[:-1] + str((int(IB_ACCT[-1]) + 1) % 10)
            (root / "inputs" / "slips" / f"{other}.2025.dividends.csv"
             ).write_text(_ib_report().replace(IB_ACCT, other))
            r = tj_home(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
            r = tj_home(root, home, "slip-audit", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)
            rep = json.loads(r.stdout)
            kinds = sorted(e["kind"] for e in rep["not_compared"])
            self.assertEqual(kinds, ["report-no-account",
                                     "unknown-component"])
            self.assertNotIn(IB_ACCT, r.stdout)
            self.assertNotIn(other, r.stdout)
            self.assertNotEqual(rep["status"], "ok")
            step = self._step(root)
            self.assertEqual(step.status, "attention", step.detail)
            self.assertFalse(step.question)
            self.assertIn("2 slip input(s) not compared", step.detail)

    def test_no_slips_and_an_unread_account_is_not_done(self):
        # No slip and no income in the books that were read: "no taxable
        # income that a T5 or T3 reports" was done over account b.
        with tempfile.TemporaryDirectory() as td:
            root = _slip_project(Path(td))
            (root / "inputs/slips/slips.toml").unlink()
            (root / "work/a_base.json").write_text(
                json.dumps({"transactions": []}))
            rep = SA.audit(root, _cfg(root))
            self.assertEqual(rep["status"], "incomplete")
            self.assertEqual(self._step(root).status, "blocked")


def _run_project(tmp: Path, trades: str) -> Path:
    """A Canada project of one taxable account, `margin`, built by
    `taxjson run`."""
    root = tmp / "p"
    (root / "inputs/margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        _CA + 'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "inputs/margin/trades.tt").write_text(trades)
    r = _tjs(root, "run", "--no-input")
    if r.returncode != 0:
        raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
    return root


class TestTJ0306T1135NeedsTheGains(unittest.TestCase):
    """TJ-0306: the T1135 income and gain columns come from the gains
    files; with an account's gains files gone they read 0 at exit 0."""

    _TRADES = ("BUYSELL 2025-01-02 10:00:00 ZZQ.US 10 CAD 10 100 0\n"
               "BUYSELL 2025-06-02 10:00:00 ZZQ.US -10 CAD 20 200 0\n"
               "DIVIDEND 2025-09-01 10:00:00 ZZQ.US 0 CAD 0 100 0\n")

    def test_missing_gains_files_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _run_project(Path(td), self._TRADES)
            r = _tjs(root, "t1135", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            (prop,) = json.loads(r.stdout)["properties"]
            self.assertEqual((prop["symbol"], prop["income"], prop["gain"]),
                             ("ZZQ.US", 100.0, 100.0))
            for name in ("margin_gains.json", "margin_gains_wash.json"):
                p = root / "work" / name
                if p.exists():
                    p.unlink()
            for args in (("t1135", "--json"), ("t1135",)):
                r = _tjs(root, *args)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertIn("no gains file for taxable account(s) margin",
                              r.stderr)
                self.assertIn("`taxjson run`", r.stderr)


class TestTJ0307SanityCoverage(unittest.TestCase):
    """TJ-0307: an account whose books cannot be read (or that has
    inputs and no books) is refused, never dropped from the coverage:
    with it the only unchecked account, sanity said clean and complete."""

    _TRADES = "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 100 100 0\n"

    def _gains_file(self, root):
        for name in ("margin_gains_wash.json", "margin_gains.json"):
            if (root / "work" / name).exists():
                return root / "work" / name
        raise AssertionError("no gains file")

    def _step(self, root):
        from taxjson.bin.taxjson_run import load_config
        ctx = cl.Ctx(root=root, cfg=load_config(root), year=2025,
                     today=date(2026, 3, 1),
                     run_sub=cl.default_run_sub(root))
        return cl.d_sanity(ctx)

    def test_unreadable_books_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _run_project(Path(td), self._TRADES)
            r = _tjs(root, "sanity", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual((doc["complete"], doc["uncovered_accounts"]),
                             (False, ["margin"]))
            g = self._gains_file(root)
            g.write_text("{ broken")
            for args in (("sanity", "--json"), ("sanity",)):
                r = _tjs(root, *args)
                # (exit 2, an input that cannot be read, as the refusal
                # of such an account named with a file:
                # test_fix_audit_1024_sanity_slips B19; backlog TJ-0353)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertIn(f"work/{g.name}", r.stderr)
                self.assertIn("the books of 'margin' cannot be read",
                              r.stderr)
            step = self._step(root)
            self.assertEqual(step.status, "attention", step.detail)
            self.assertIn("cannot be read", step.detail)

    def test_an_account_with_inputs_and_no_books_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _run_project(Path(td), self._TRADES)
            (root / "taxjson.toml").write_text(
                (root / "taxjson.toml").read_text()
                + '[accounts.rrsp]\ntype = "sheltered"\n')
            (root / "inputs/rrsp").mkdir()
            (root / "inputs/rrsp/trades.tt").write_text(self._TRADES)
            r = _tjs(root, "sanity", "--json")
            # (exit 2: books missing, an input; backlog TJ-0353)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertIn("no gains file in work/ for account(s) rrsp",
                          r.stderr)
            # Named accounts are checked as asked: the other one is not
            # in that check.
            h = Path(td) / "margin_positions.toml"
            h.write_text('[[holding]]\nsymbol = "ZZQ.TO"\nquantity = 1\n')
            r = _tjs(root, "sanity", f"margin={h}", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(json.loads(r.stdout)["clean"])


class TestTJ0325ReconcileCurrency(unittest.TestCase):
    """TJ-0325: reconcile-slips compared computed USD gains with a CAD
    slip as equal. Every gains file is checked against the country's
    currency (the slips') before anything is compared."""

    _ROW = {"id": "s1", "date": "2025-06-02", "date_settle": "2025-06-03",
            "symbol": "ZZQ.US", "qty": 10, "proceeds": 150, "cost": 100,
            "gain": 50, "direction": "LONG"}

    def _reconcile(self, td, country, gains, slip_ccy):
        from taxjson.bin import taxjson_reconcile_slips as RS
        import contextlib
        import io
        p = Path(td)
        (p / "gains.json").write_text(json.dumps(gains))
        (p / "slip.csv").write_text(
            "Symbol,Quantity,Proceeds,Cost,Currency\n"
            f"ZZQ.US,10,150,100,{slip_ccy}\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = RS.main([str(p / "slip.csv"), "--gains",
                          str(p / "gains.json"), "--country", country,
                          "--year", "2025", "--json"])
        return rc, out.getvalue(), err.getvalue()

    @rule("CA-CTRY-03")
    def test_canada_refuses_computed_usd(self):
        cases = {
            "row": {"transactions": [dict(self._ROW, currency="USD")]},
            "metadata": {"metadata": {"target_currency": "USD"},
                         "transactions": [dict(self._ROW)]},
            "manual row": {"transactions": [],
                           "manual_reporting_required": [
                               {"date": "2025-06-02", "symbol": "ZZQ.US",
                                "qty": 10, "proceeds": 150,
                                "currency": "USD"}]},
        }
        for name, gains in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as td:
                rc, out, err = self._reconcile(td, "canada", gains, "CAD")
                self.assertEqual(rc, 2, out + err)
                self.assertEqual(out, "")
                self.assertIn("gains.json: computed dispositions in USD",
                              err)
                self.assertIn("not CAD", err)

    @rule("CA-CTRY-03")
    def test_canada_compares_cad(self):
        for row in (dict(self._ROW, currency="CAD"), dict(self._ROW)):
            with tempfile.TemporaryDirectory() as td:
                gains = {"metadata": {"target_currency": "CAD"},
                         "transactions": [
                             row, {"action": "DIVIDEND", "symbol": "ZZQ.US",
                                   "date": "2025-04-01", "currency": "USD",
                                   "dividend": 5}]}
                rc, out, err = self._reconcile(td, "canada", gains, "CAD")
                self.assertEqual(rc, 0, out + err)
                self.assertTrue(json.loads(out)["clean"])

    @rule("US-CTRY-03")
    def test_usa_refuses_computed_cad(self):
        with tempfile.TemporaryDirectory() as td:
            gains = {"transactions": [dict(self._ROW, currency="CAD")]}
            rc, out, err = self._reconcile(td, "usa", gains, "USD")
            self.assertEqual(rc, 2, out + err)
            self.assertIn("computed dispositions in CAD", err)


if __name__ == "__main__":
    unittest.main()
