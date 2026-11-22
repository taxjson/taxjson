"""Fix round 7 (wave D) of 2026-10-29, slip-audit and reconcile-slips:
backlog TJ-1049, TJ-1050, TJ-1051, TJ-1124, TJ-1125, TJ-1091, TJ-1092,
TJ-1093. Synthetic data only: made-up tickers (SYN, ALFA, BETA, ZZQ) and
broker accounts (U1234567).

Formerly: test_fix_1029_r7d_slips"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import project, tj, toml
from taxjson.lib.slip_symbols import load_computed
from _support.filing_reconcile_slips import _buyback, _run, _short, _write


class GrantProject(unittest.TestCase):
    """A Canadian project through `tjs run`, then reconcile-slips."""

    SINCE = 2025

    def _project(self, td, files, extra=""):
        root = project(td, "p", files)
        (root / "taxjson.toml").write_text(toml(year=2025).replace(
            "option_grant_timing_since = 2025",
            f"option_grant_timing_since = {self.SINCE}") + extra)
        tj(root, "run", "--no-input")
        return root

    def _reconcile(self, root, text):
        slip = root / "slip.csv"
        slip.write_text(text)
        r = tj(root, "reconcile-slips", str(slip), "--json", check=False)
        return r.returncode, json.loads(r.stdout)


class PriorYearGrantNilExpiry(GrantProject):
    """TJ-1091: written in 2024 for 10, expired at nil in 2025.

    Formerly: test_fix_1029_r7d_slips.PriorYearGrantNilExpiry"""

    SINCE = 2024
    OPT = "ZZQ250620C00010000.TO"
    TT = (f"BUYSELL 2024-11-04 10:00:00 {OPT} -1 CAD 0.1 10 0\n"
          f"BUYSELL 2025-06-20 16:00:00 {OPT} 1 CAD 0 0 0\n")

    def test_correct_slip_reconciles_and_empty_slip_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, f"symbol,quantity,proceeds,currency\n{self.OPT},1,10,"
                      "CAD\n")
            self.assertEqual(rc, 0, rep)
            self.assertEqual(rep["rows"][0]["status"], "OK")
            self.assertIn("earlier-year write", rep["rows"][0]["detail"])
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,currency\n")
            self.assertEqual(rc, 1, rep)
            row = rep["rows"][0]
            self.assertEqual(row["status"], "MISSING_FROM_SLIP")
            self.assertIn("would report 10.00", row["detail"])
            self.assertIn("buy-back or expiry", row["detail"])

    def test_this_years_write_keeps_its_own_reading(self):
        """TJ-0781 through the engine: no earlier-year premium."""
        tt = (f"BUYSELL 2025-01-06 10:00:00 {self.OPT} -1 CAD 0.1 10 0\n"
              f"BUYSELL 2025-06-20 16:00:00 {self.OPT} 1 CAD 0 0 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": tt})
            rc, rep = self._reconcile(
                root, f"symbol,quantity,proceeds,currency\n{self.OPT},1,10,"
                      "CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"][0]["detail"], "")

    def test_wrong_quantity_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, f"symbol,quantity,proceeds,currency\n{self.OPT},2,10,"
                      "CAD\n")
        self.assertEqual(rc, 1, rep)
        self.assertIn("quantity off by +1.0000", rep["rows"][0]["detail"])

    def test_gains_stay_nil_and_no_close_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            doc = json.loads((root / "work" / "margin_gains.json")
                             .read_text())
        self.assertEqual(doc["transactions"], [])
        self.assertEqual(doc["grant_nil_closes"][0]["grant_closed"],
                         {"2024": {"units": 1.0, "premium": 10.0}})

    def test_no_key_when_nothing_expired_at_nil(self):
        tt = (f"BUYSELL 2024-11-04 10:00:00 {self.OPT} -1 CAD 0.1 10 0\n"
              f"BUYSELL 2025-03-03 10:00:00 {self.OPT} 1 CAD 0.01 1 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": tt})
            doc = json.loads((root / "work" / "margin_gains.json")
                             .read_text())
        self.assertNotIn("grant_nil_closes", doc)

    def test_two_accounts_each_keep_their_own(self):
        tt = (f"BUYSELL 2025-02-03 10:00:00 ZZQ260116C00010000.TO -1 CAD "
              f"0.3 30 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(
                td, {"margin/t.tt": self.TT,
                     "m2/t.tt": tt},
                extra='[accounts.m2]\ntype = "taxable"\n')
            one = json.loads((root / "work" / "margin_gains_wash.json")
                             .read_text())
            two = json.loads((root / "work" / "m2_gains_wash.json")
                             .read_text())
        self.assertEqual(len(one["grant_nil_closes"]), 1)
        self.assertNotIn("grant_nil_closes", two)


class NilCloseRecords(unittest.TestCase):
    """load_computed on a gains file's `grant_nil_closes` (TJ-1091).

    Formerly: test_fix_1029_r7d_slips.NilCloseRecords"""

    OPT = "SYN250620C00001000.TO"

    def _nil(self, closed, date="2025-06-20"):
        return {"date": date, "date_settle": date, "symbol": self.OPT,
                "account": "margin", "grant_closed": closed}

    def test_this_years_nil_close_is_counted_once(self):
        doc = {"transactions": [_write("2025-01-02", 10.0, symbol=self.OPT)],
               "grant_nil_closes": [self._nil(
                   {"2025": {"units": 1.0, "premium": 10.0}})]}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,currency\n"
                              f"{self.OPT},1,10,CAD\n", doc=doc)
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"][0]["detail"], "")

    def test_another_years_record_is_out(self):
        doc = {"transactions": [],
               "grant_nil_closes": [self._nil(
                   {"2023": {"units": 1.0, "premium": 10.0}},
                   date="2024-06-20")]}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,currency\n",
                              doc=doc)
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"], [])


class PremiumAcrossAccounts(unittest.TestCase):
    """TJ-1092: a buy-back closes its own account's writes first. TJ-0779's
    own tests (one account: earliest closed, latest open) still hold.

    Formerly: test_fix_1029_r7d_slips.PremiumAcrossAccounts"""

    OPT = "ZZQ260116C00010000.TO"

    def _write(self, date, premium, account, **kw):
        return dict(_write(date, premium, symbol=self.OPT, **kw),
                    account=account)

    def _close(self, date, paid, closed, account):
        return dict(_buyback(date, paid, closed, symbol=self.OPT),
                    account=account)

    def _rep(self, ents, row):
        with tempfile.TemporaryDirectory() as td:
            return _run(td, "symbol,quantity,proceeds,cost,currency\n"
                        + row, ents)

    def test_open_write_is_the_other_accounts(self):
        ents = [self._write("2025-01-06", 10.0, "a1"),
                self._write("2025-02-03", 30.0, "a2"),
                self._close("2025-03-03", 5.0, {"2025": 30.0}, "a2")]
        rc, rep, _ = self._rep(ents, f"{self.OPT},1,30,5,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertIn("10.00 premium of written option(s) still open",
                      rep["rows"][0]["detail"])

    def test_commission_stays_with_the_open_write(self):
        ents = [self._write("2025-01-06", 10.0, "a1", commission=1.0),
                self._write("2025-02-03", 30.0, "a2", commission=2.0),
                self._close("2025-03-03", 5.0, {"2025": 28.0}, "a2")]
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": ents}))
            rec = load_computed([g], 2025)["ZZQ260116C00010000"]
        self.assertAlmostEqual(rec["open_grant"], 10.0)
        self.assertAlmostEqual(rec["open_grant_net"], 9.0)

    def test_contract_moved_closes_the_other_accounts_lot(self):
        """The closing account's lots ran out: as the engine, another's
        (TJ-0718's order)."""
        ents = [self._write("2025-01-06", 10.0, "a1"),
                self._write("2025-02-03", 30.0, "a1"),
                self._close("2025-03-03", 5.0, {"2025": 10.0}, "a2")]
        rc, rep, _ = self._rep(ents, f"{self.OPT},1,10,5,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertIn("30.00 premium of written option(s) still open",
                      rep["rows"][0]["detail"])

    def test_account_with_one_of_two_writes_closed(self):
        ents = [self._write("2025-01-06", 10.0, "a1"),
                self._write("2025-01-20", 20.0, "a1"),
                self._write("2025-02-03", 30.0, "a2"),
                self._close("2025-03-03", 5.0, {"2025": 10.0}, "a1")]
        rc, rep, _ = self._rep(ents, f"{self.OPT},1,10,5,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertIn("50.00 premium of written option(s) still open",
                      rep["rows"][0]["detail"])


class PremiumAcrossAccountsThroughTheEngine(GrantProject):
    """TJ-1092's repro, with the engine's own rows.

    Formerly: test_fix_1029_r7d_slips.PremiumAcrossAccountsThroughTheEngine"""

    OPT = "ZZQ260116C00010000.TO"

    def test_pooled_slip_reconciles(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(
                td,
                {"margin/t.tt":
                 f"BUYSELL 2025-01-06 10:00:00 {self.OPT} -1 CAD 0.1 10 0\n",
                 "m2/t.tt":
                 f"BUYSELL 2025-02-03 10:00:00 {self.OPT} -1 CAD 0.3 30 0\n"
                 f"BUYSELL 2025-03-03 10:00:00 {self.OPT} 1 CAD 0.05 5 0\n"},
                extra='[accounts.m2]\ntype = "taxable"\n')
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,cost,currency\n"
                      f"{self.OPT},1,30,5,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertIn("10.00 premium of written option(s) still open",
                      rep["rows"][0]["detail"])


class FuturesCloses(GrantProject):
    """TJ-1093: a plain future's close reads as form-export shows it: a
    gain as proceeds, a loss as ACB.

    Formerly: test_fix_1029_r7d_slips.FuturesCloses"""

    TT = ("BUYSELL 2025-08-11 10:00:00 F:SXFZ5.TO -1 CAD 1010 202000 0 x200\n"
          "BUYSELL 2025-09-03 10:00:00 F:SXFZ5.TO 1 CAD 1000 200000 0 x200\n"
          "BUYSELL 2025-08-11 10:00:00 F:SXFH6.TO 1 CAD 1010 202000 0 x200\n"
          "BUYSELL 2025-09-03 10:00:00 F:SXFH6.TO -1 CAD 1000 200000 0 x200"
          "\n")

    def test_the_schedule_3_shape_reconciles(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,cost,currency\n"
                      "F:SXFZ5.TO,1,2000,0,CAD\nF:SXFH6.TO,1,0,2000,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertEqual([r["status"] for r in rep["rows"]], ["OK", "OK"])
        self.assertEqual([r["detail"] for r in rep["rows"]], ["", ""])

    def test_a_wrong_figure_is_still_caught(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,cost,currency\n"
                      "F:SXFZ5.TO,1,1500,0,CAD\nF:SXFH6.TO,1,0,2000,CAD\n")
        self.assertEqual(rc, 1, rep)
        self.assertEqual({r["symbol"].split(".")[0]: r["status"]
                          for r in rep["rows"]},
                         {"F:SXFZ5": "MISMATCH", "F:SXFH6": "OK"})

    def test_a_loss_without_a_slip_row_is_no_expired_option(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"margin/t.tt": self.TT})
            rc, rep = self._reconcile(
                root, "symbol,quantity,proceeds,cost,currency\n"
                      "F:SXFZ5.TO,1,2000,0,CAD\n")
        self.assertEqual(rc, 1, rep)
        row = [r for r in rep["rows"] if r["symbol"].startswith("F:SXFH6")][0]
        self.assertEqual(row["status"], "MISSING_FROM_SLIP")

    def test_commission_is_inside_the_profit_or_loss(self):
        e = {"date": "2025-09-03", "date_settle": "2025-09-03",
             "symbol": "F:SXFH6.TO", "qty": -1, "proceeds": -2010.0,
             "cost": 0.0, "gain": -2010.0, "raw_gain": -2010.0,
             "commission": 10.0, "fee": 0.0, "direction": "LONG"}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,cost,currency\n"
                              "F:SXFH6.TO,1,0,2010,CAD\n", [e])
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"][0]["detail"], "")

    def test_a_denied_loss_shows_before_the_denial(self):
        """Schedule 3 shows the P/L before any superficial-loss denial."""
        e = {"date": "2025-09-03", "date_settle": "2025-09-03",
             "symbol": "F:SXFH6.TO", "qty": -1, "proceeds": -2000.0,
             "cost": 0.0, "gain": 0.0, "raw_gain": -2000.0,
             "disallowed_amount": 2000.0, "direction": "LONG"}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,cost,currency\n"
                              "F:SXFH6.TO,1,0,2000,CAD\n", [e])
        self.assertEqual(rc, 0, rep)

    def test_us_section_1256_future_keeps_its_reading(self):
        """It goes on Form 6781, not Form 8949: no T5008 shape."""
        e = {"date": "2025-09-03", "date_settle": "2025-09-03",
             "symbol": "F:SXFH6.US", "qty": -1, "proceeds": -2000.0,
             "cost": 0.0, "gain": -2000.0, "direction": "LONG"}
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [e]}))
            rec = load_computed([g], 2025, "trade", "usa")["F:SXFH6"]
            ca = load_computed([g], 2025)["F:SXFH6"]
        self.assertAlmostEqual(rec["proceeds_gross"], -2000.0)
        self.assertAlmostEqual(ca["cost"], 2000.0)

    def test_option_on_a_future_is_not_a_plain_future(self):
        e = _short(symbol="F:SXF251219C00001000.TO", qty=1, proceeds=-5.0,
                   cost=-12.0, date="2025-03-03", grant=False)
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,cost,currency\n"
                              "F:SXF251219C00001000.TO,1,12,5,CAD\n", [e])
        self.assertEqual(rc, 0, rep)


if __name__ == "__main__":
    unittest.main()
