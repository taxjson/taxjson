"""Fixes for the 2026-10-25 workflow audit, group locks: the one check
of a close-year lock (bin/taxjson_filed.read_lock, lock_damage) that
every reader applies — check-filed and the run's filed-year check,
`taxjson years`, and the next year's carryover, estimate, amt,
close-year and handoff — so a lock one of them refuses is never taken
as sound by another. One test per finding, each named for it.
Synthetic data only."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env():
    return dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")


def _cli(root, *args):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=REPO_ROOT, capture_output=True, text=True, env=_env(),
        stdin=subprocess.DEVNULL, timeout=300)


def _row(i, date, sym, qty, net, cur):
    return {"id": i, "action": "BUYSELL", "date": date,
            "date_settle": date, "time": "10:00:00", "symbol": sym,
            "quantity": qty, "price": abs(net / qty), "net_amount": net,
            "currency": cur, "account": "margin"}


def _project(root, year, country, cur, extra=""):
    """A project whose books are built (work/margin_base.json: one
    round trip of `year`, a 100.00 gain): `taxjson carryover` reads
    them and the filed-year locks it answers to."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "work").mkdir(exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{extra}'
        f'[accounts.margin]\ntype = "taxable"\n')
    rows = [_row("b1", f"{year}-02-03", "ZZQ.TO", 10, -100.0, cur),
            _row("s1", f"{year}-06-02", "ZZQ.TO", -10, 200.0, cur)]
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": rows}))
    return root


def _lock(year, country, realized, block):
    """A close-year lock as close-year writes it (the fields the
    readers here use)."""
    return {"schema_version": 2, "year": year, "country": country,
            "basis": "wash-adjusted", "closed_at": f"{year + 1}-03-01T12:00:00",
            "date_basis": "settle", "year_end": {}, "dispositions": [],
            "accounts": {"margin": {"realized": realized, "disallowed": 0.0,
                                    "dispositions": 1, "income": 0.0}},
            "totals": {"realized": realized, "disallowed": 0.0,
                       "dispositions": 1, "income": 0.0},
            "carryforwards": dict({"version": 1}, **block)}


_NCL = {"net_capital_loss": {"opening": 0.0, "created": 900.0,
                             "applied": 0.0, "closing": 900.0}}
_CL = {"capital_loss": {"st_opening": 0.0, "lt_opening": 0.0,
                        "st_closing": 900.0, "lt_closing": 0.0}}


class TestOtherCountryPriorLock(unittest.TestCase):
    @rule("CA-RPT-09")
    @rule("US-RPT-06")
    def test_year_end_1_other_countrys_prior_lock_refused_by_every_reader(
            self):
        """year-end#1: carryover, estimate and amt read a prior-year lock
        closed under the other country's law; handoff, check-filed and
        fx-cash refuse the same lock. A Canadian 2025 project whose
        prior_year_record named a US 2024 lock took it as the filed
        figure 0.00 (carryover), dropped the carried loss (estimate) and
        said "no earlier close-year record" (amt), at exit 0. Every
        reader now refuses it by name (taxjson_filed.read_lock), as
        handoff does, and points at prior_year_record."""
        from taxjson.bin import taxjson_carryover as TC
        from taxjson.bin import taxjson_filed as F
        from taxjson.lib import carryforward as CF
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            us_lock = top / "us" / "filed" / "2024.json"
            us_lock.parent.mkdir(parents=True)
            us_lock.write_text(json.dumps(_lock(2024, "usa", -900.0, _CL)))
            ca = _project(top / "ca", 2025, "canada", "CAD",
                          f'prior_year_record = "{us_lock}"\n')
            settings = {"year": 2025, "country": "canada",
                        "prior_year_record": str(us_lock)}
            # The estimate's two readers (and so amt and close-year).
            for call in (lambda: CF.resolve_losses(ca, settings, "canada",
                                                   False),
                         lambda: CF.resolve_amt(ca, settings, {})):
                with self.assertRaises(CF.CarryInputError) as cm:
                    call()
                msg = str(cm.exception)
                self.assertIn('closed under country = "usa"', msg)
                self.assertIn('this project is country = "canada"', msg)
                self.assertIn("prior_year_record", msg)
            # The carryover ledger: refused, no 0.00 carryforward.
            with self.assertRaises(TC.LockUnreadable):
                TC.lock_figure(us_lock, "canada")
            r = _cli(ca, "carryover")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn('closed under country = "usa"', r.stderr)
            self.assertNotIn("carryforward after", r.stdout)
            # amt for the closed year: the lock is refused by name, not
            # "records no minimum tax".
            r = _cli(ca, "amt", "2024")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn('closed under country = "usa"', r.stderr)
            self.assertNotIn("records no minimum tax", r.stderr)
            # The reverse: a Canadian lock opening a US year.
            ca_lock = top / "ca24" / "filed" / "2024.json"
            ca_lock.parent.mkdir(parents=True)
            ca_lock.write_text(json.dumps(_lock(2024, "canada", -900.0,
                                                _NCL)))
            us = _project(top / "usp", 2025, "usa", "USD",
                          f'prior_year_record = "{ca_lock}"\n')
            with self.assertRaises(CF.CarryInputError) as cm:
                CF.resolve_losses(us, {"year": 2025, "country": "usa",
                                       "prior_year_record": str(ca_lock)},
                                  "usa", False)
            self.assertIn('closed under country = "canada"',
                          str(cm.exception))
            # The project's own country: read as before.
            with self.assertRaises(F.LockProblem):
                F.read_lock(us_lock, {"country": "canada"}, prior=True)
            self.assertEqual(F.read_lock(us_lock, {"country": "usa"},
                                         prior=True)["country"], "usa")
            r = CF.resolve_losses(ca, dict(settings,
                                           prior_year_record=str(ca_lock)),
                                  "canada", False)
            self.assertEqual(r["other_losses"], 900.0)


class TestDamagedPriorLockInCarryover(unittest.TestCase):
    @rule("CA-CARRY-04")
    def test_robustness_1_carryover_refuses_a_damaged_prior_year_lock(self):
        """robustness#1: incomplete fix of B39 — `tjs carryover` dropped
        a damaged prior-year close-year lock (its closing missing, the
        file cut in half, the closing NaN) with a Warning and printed a
        0.00 carryforward at exit 0, while estimate, amt, handoff and
        close-year refuse the same lock. A lock before the project year
        (the ledger takes its filed figure and carried balance) is now
        refused, naming the lock, the problem and the fix."""
        from taxjson.lib import carryforward as CF
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            lock = top / "2024" / "filed" / "2024.json"
            lock.parent.mkdir(parents=True)
            p = _project(top / "2025", 2025, "canada", "CAD",
                         'prior_year_record = "../2024/filed/2024.json"\n')
            settings = {"year": 2025, "country": "canada",
                        "prior_year_record": "../2024/filed/2024.json"}
            whole = _lock(2024, "canada", -900.0, _NCL)
            lock.write_text(json.dumps(whole))
            r = _cli(p, "carryover")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("carryforward after 2025: 900.00", r.stdout)
            no_closing = json.loads(json.dumps(whole))
            del no_closing["carryforwards"]["net_capital_loss"]["closing"]
            text = json.dumps(whole)
            for damaged, why in (
                    (json.dumps(no_closing), "net_capital_loss has no "
                                             "closing"),
                    (text.replace('"closing": 900.0', '"closing": NaN'),
                     "closing is not a number"),
                    (text[:len(text) // 2], "not valid JSON")):
                lock.write_text(damaged)
                r = _cli(p, "carryover")
                self.assertEqual(r.returncode, 1, why + r.stdout + r.stderr)
                self.assertIn("2024.json", r.stderr)
                self.assertIn(why, r.stderr)
                self.assertIn("re-close that year", r.stderr)
                self.assertNotIn("carryforward after", r.stdout)
                # The estimate refuses the same lock with the same words.
                with self.assertRaises(CF.CarryInputError) as cm:
                    CF.resolve_losses(p, settings, "canada", False)
                self.assertIn(why, str(cm.exception))


class TestMinimumTaxByYearShape(unittest.TestCase):
    @rule("CA-AMT-08")
    def test_year_end_3_lock_minimum_tax_year_twice_or_negative_refused(
            self):
        """year-end#3: a lock's minimum-tax carryover with one year
        written twice silently keeps the second amount, and a negative
        amount is dropped (the A39 fix covers only [estimate]
        amt_carryover). The lock's by-year tables now get the checks
        amt_carryover gets — one entry per year, a plausible year, 0 or
        more — the closing must be the sum of closing_by_year, a
        negative balance is refused, and a key written twice in the
        file is refused too."""
        from taxjson.bin import taxjson_filed as F
        from taxjson.lib import carryforward as CF
        from taxjson.lib import handoff as H

        def mt(by_year, closing=None, **kw):
            s = {"created": 0.0, "recovered_federal": 0.0,
                 "closing_by_year": by_year,
                 "closing": (sum(by_year.values()) if closing is None
                             else closing)}
            s.update(kw)
            return {"version": 1, "minimum_tax": s}

        self.assertIsNone(CF.block_problem(mt({"2023": 400.0,
                                               "2024": 98.51})))
        self.assertIsNone(CF.block_problem(mt({})))
        twice = CF.block_problem(mt({"2024": 900.0, "02024": 50.0}))
        self.assertIn("closing_by_year gives 2024 twice", twice)
        self.assertIn("'02024'", twice)
        self.assertIn("closing_by_year: 2024 is -900.0, a negative amount",
                      CF.block_problem(mt({"2024": -900.0}, closing=0.0)))
        self.assertIn("minimum_tax.closing is -900.0, a negative balance",
                      CF.block_problem(mt({"2024": -900.0})))
        self.assertIn("opening_by_year", CF.block_problem(
            mt({"2024": 900.0}, opening_by_year={"2023": -1.0})))
        self.assertIn("plausible", CF.block_problem(mt({"0": 900.0})))
        self.assertIn("adds up to 500.00", CF.block_problem(
            mt({"2024": 500.0}, closing=900.0)))
        self.assertIn("negative balance", CF.block_problem(
            {"net_capital_loss": {"opening": 0.0, "created": 0.0,
                                  "applied": 0.0, "closing": -900.0}}))
        # The amt_carryover input gets the same verdicts.
        with self.assertRaises(CF.CarryInputError):
            CF.amt_from_config({"2024": 900.0, "02024": 50.0})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "filed").mkdir()
            lock = root / "filed" / "2024.json"
            settings = {"year": 2025, "country": "canada"}
            doc = _lock(2024, "canada", 0.0,
                        mt({"2024": 900.0, "02024": 50.0}))
            lock.write_text(json.dumps(doc))
            with self.assertRaises(CF.CarryInputError) as cm:
                CF.resolve_amt(root, settings, {})
            self.assertIn("2024.json", str(cm.exception))
            self.assertIn("gives 2024 twice", str(cm.exception))
            with self.assertRaises(H.RecordError):
                H.load_record(lock)
            self.assertIn("gives 2024 twice",
                          F.lock_damage(doc, 2024, "filed/2024.json"))
            # One key written twice in the file: json.loads kept the
            # second; every reader of a lock refuses it.
            lock.write_text(json.dumps(_lock(2024, "canada", 0.0,
                                             mt({"2024": 900.0})))
                            .replace('"2024": 900.0',
                                     '"2024": 900.0, "2024": 50.0'))
            with self.assertRaises(F.LockProblem) as cm:
                F.read_lock(lock)
            self.assertIn("written twice", str(cm.exception))
            with self.assertRaises(CF.CarryInputError):
                CF.resolve_amt(root, settings, {})
            with self.assertRaises(H.RecordError):
                H.load_record(lock)
            # A sound lock carries its by-year amounts.
            lock.write_text(json.dumps(_lock(2024, "canada", 0.0,
                                             mt({"2023": 400.0,
                                                 "2024": 98.51}))))
            self.assertEqual(CF.resolve_amt(root, settings, {})["by_year"],
                             {2023: 400.0, 2024: 98.51})


class TestFiledYearOwnCheck(unittest.TestCase):
    def test_year_end_6_check_filed_and_years_call_a_refused_lock_damaged(
            self):
        """year-end#6: check-filed, run --strict and years call a lock OK
        that the next year's estimate, amt, handoff and close-year refuse
        as damaged (carryforwards NaN or a missing closing; a year field
        naming another year). The filed year's own check now applies the
        same lock_damage, so the year can be repaired there."""
        from taxjson.bin import taxjson_filed as F
        from taxjson.lib import carryforward as CF
        from taxjson.lib import handoff as H
        from taxjson.lib import project_layout as PL
        whole = _lock(2024, "canada", -900.0, _NCL)
        text = json.dumps(whole)
        no_closing = json.loads(text)
        del no_closing["carryforwards"]["net_capital_loss"]["closing"]
        cases = ((json.dumps(no_closing), "net_capital_loss has no closing"),
                 (text.replace('"closing": 900.0', '"closing": NaN'),
                  "net_capital_loss.closing is not a number"),
                 (json.dumps(dict(whole, year=2023)),
                  "says year 2023, not 2024"))
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            p = top / "2024"
            _project(p, 2024, "canada", "CAD")
            lock = p / "filed" / "2024.json"
            lock.parent.mkdir()
            nxt = top / "2025"
            settings = {"year": 2025, "country": "canada",
                        "prior_year_record": "../2024/filed/2024.json"}
            nxt.mkdir()
            lock.write_text(text)
            self.assertIsNone(F.lock_damage(whole, 2024, "filed/2024.json"))
            self.assertIsNone(
                {r["year"]: r for r in PL.years_report(top)["years"]}
                [2024]["problem"])
            for body, why in cases:
                lock.write_text(body)
                # The filed year's own checks.
                r = _cli(p, "check-filed")
                self.assertEqual(r.returncode, 1, why + r.stdout + r.stderr)
                self.assertIn("filed 2024 could not be checked", r.stderr)
                self.assertIn(why, r.stderr)
                self.assertNotIn("filed 2024: OK", r.stdout + r.stderr)
                rep = {x["year"]: x for x in PL.years_report(top)["years"]}
                self.assertIn(why, rep[2024]["problem"] or "", why)
                self.assertTrue(rep[2024]["problem"].startswith(
                    "filed/2024.json"))
                # ... agree with the next year's readers.
                with self.assertRaises(CF.CarryInputError) as cm:
                    CF.resolve_losses(nxt, settings, "canada", False)
                self.assertIn(why, str(cm.exception))
                with self.assertRaises(H.RecordError) as cm:
                    H.load_record(lock)
                self.assertIn(why, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
