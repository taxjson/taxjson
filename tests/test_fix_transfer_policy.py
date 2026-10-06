"""Transfer policy (owner feedback, new-user run): a sheltered account's
unmatched TRANSFER row is a custody move by default — its shares count
as held, but it is never an acquisition (nor a disposition) for the
superficial-loss / wash-sale window (CA-SL-16 / US-WASH-23). The run
warns ONCE about each transfer-in inside a taxable loss's window and
completes. `[settings] transfers_as_acquisitions = true` restores the
strict treatment (CA-SL-17 / US-WASH-24): an arrival date inside a
loss's window stops the run until it is declared.

Synthetic data only.
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.core import AmbiguousTransferDateError
from taxjson.lib.country import COUNTRIES
from taxjson.lib.pipeline import GainsRequest, run_gains
from tax_rules import rule
from tax_rules.dual import cli, gains_both, projects_both, tx


def _book():
    """A taxable loss (bought 100 at 50, sold at 40, settle = trade)
    and an RRSP/IRA transfer-in of 80 identical shares 24 days later."""
    main = [tx("BUYSELL", "2025-01-10", "SAMPXF.US", 100, -5000.0),
            tx("BUYSELL", "2025-04-15", "SAMPXF.US", -100, 4000.0)]
    shel = [tx("TRANSFER", "2025-05-09", "SAMPXF.US", 80, 0.0,
               account="rrsp")]
    return main, shel


def _disallowed(res):
    return sum(float(w.get("disallowed_amount") or 0.0)
               for w in res.get("wash_sales") or [])


class TestDefaultPolicyEngine(unittest.TestCase):
    @rule("CA-SL-16")
    @rule("US-WASH-23")
    def test_transfer_in_never_replaces_and_is_listed(self):
        main, shel = _book()
        r = gains_both(main, sheltered=shel, year=2025)
        for c in COUNTRIES:
            with self.subTest(country=c):
                self.assertEqual(_disallowed(r[c]), 0.0)
                self.assertAlmostEqual(r[c]["summary"]["total_gain"],
                                       -1000.0, places=2)
                self.assertEqual(r[c]["transfers_in_loss_windows"], [{
                    "account": "rrsp", "symbol": "SAMPXF.US", "qty": 80.0,
                    "date": "2025-05-09", "loss_date": "2025-04-15",
                    "loss_account": "margin"}])
                err = r[c]["_stderr"]
                self.assertIn("warning: transfer-in inside a loss window:",
                              err)
                self.assertIn("- rrsp: SAMPXF.US +80 on 2025-05-09", err)
                self.assertIn("record it as a BUYSELL", err)
                # The DECLARED attestation is no longer asked for.
                self.assertNotIn("DECLARED", err)
        self.assertIn("superficial (s.54)", r["canada"]["_stderr"])
        self.assertIn("wash sale (§1091)", r["usa"]["_stderr"])

    @rule("CA-SL-16")
    @rule("US-WASH-23")
    def test_transfer_outside_the_window_is_not_listed(self):
        main, _ = _book()
        shel = [tx("TRANSFER", "2025-06-20", "SAMPXF.US", 80, 0.0,
                   account="rrsp")]
        r = gains_both(main, sheltered=shel, year=2025)
        for c in COUNTRIES:
            with self.subTest(country=c):
                self.assertNotIn("transfers_in_loss_windows", r[c])
                self.assertNotIn("transfer-in inside a loss window",
                                 r[c]["_stderr"])

    @rule("CA-SL-16", "CA-SL-02")
    def test_transferred_shares_still_count_as_held_canada(self):
        # Canada: a purchase in the window backs the denial only while
        # held at day 30 — shares that ARRIVED by transfer are held too
        # (the transfer is a move, its shares count).
        main, shel = _book()
        shel = shel + [tx("BUYSELL", "2025-04-20", "SAMPXF.US", 10, -400.0,
                          account="rrsp")]
        with redirect_stderr(io.StringIO()):
            r = run_gains(main, shel, (), GainsRequest(
                country="canada", year=2025, taxable=True))
        # 10 bought in the window, held at day 30: 10 of the 100 units
        # sold are denied (permanently: the replacement is in the RRSP).
        self.assertAlmostEqual(_disallowed(r), 100.0, places=2)

    @rule("CA-SL-16")
    @rule("US-WASH-23")
    def test_in_kind_contribution_recorded_as_buysell_is_counted(self):
        # The warning's advice: a genuine in-kind contribution recorded
        # as a BUYSELL is a purchase in the window.
        main, _ = _book()
        shel = [tx("BUYSELL", "2025-05-09", "SAMPXF.US", 80, -3300.0,
                   account="rrsp")]
        r = gains_both(main, sheltered=shel, year=2025)
        for c in COUNTRIES:
            with self.subTest(country=c):
                self.assertAlmostEqual(_disallowed(r[c]), 800.0, places=2)
                self.assertNotIn("transfers_in_loss_windows", r[c])

    @rule("CA-SL-16")
    @rule("US-WASH-23")
    def test_zero_net_cluster_near_a_trade_nets_quietly(self):
        main, _ = _book()
        shel = [tx("TRANSFER", "2025-04-20", "SAMPXF.US", -50, 0.0,
                   account="rrsp"),
                tx("TRANSFER", "2025-04-22", "SAMPXF.US", 50, 0.0,
                   account="rrsp")]
        r = gains_both(main, sheltered=shel, year=2025)
        for c in COUNTRIES:
            with self.subTest(country=c):
                err = r[c]["_stderr"]
                self.assertNotIn("NOT netted", err)
                self.assertNotIn("account-wide restatement", err)
                self.assertNotIn("transfers_in_loss_windows", r[c])
                self.assertEqual(_disallowed(r[c]), 0.0)


class TestStrictPolicyEngine(unittest.TestCase):
    @rule("CA-SL-17")
    @rule("US-WASH-24")
    def test_arrival_date_in_a_window_stops(self):
        main, shel = _book()
        for c in COUNTRIES:
            with self.subTest(country=c):
                req = GainsRequest(country=c, year=2025, taxable=True,
                                   transfers_as_acquisitions=True)
                with redirect_stderr(io.StringIO()), \
                        self.assertRaises(AmbiguousTransferDateError) as cm:
                    run_gains(list(main), list(shel), (), req)
                self.assertIn("ARRIVAL", str(cm.exception))


_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


def _qt(date, action, sym, desc, qty, price, net, cur, acct,
        act="Trades"):
    return (f"{date} 09:30:00 AM,{date} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price:.2f},{abs(qty) * price:.2f},0.00,{net:.2f},"
            f"{cur},{acct},{act},Individual\n")


_ACCOUNTS = ('[accounts.margin]\ntype = "taxable"\n'
             '[accounts.rrsp]\ntype = "sheltered"\ntransfers = true\n')


def _files(cur):
    return {
        "inputs/margin/questrade.csv": _QT
        + _qt("2025-01-10", "Buy", "SAMPXF", "SAMPXF CORP", 100, 50.0,
              -5000.0, cur, "55500001")
        + _qt("2025-04-15", "Sell", "SAMPXF", "SAMPXF CORP", -100, 40.0,
              4000.0, cur, "55500001"),
        "inputs/rrsp/questrade.csv": _QT
        + _qt("2025-05-09", "TF6", "SAMPXF", "SAMPXF CORP TRANSFER FROM "
              "OTHER", 80, 0.0, 0.0, cur, "55500002", act="Transfers"),
    }


class TestRunProject(unittest.TestCase):
    """The new-user run that stopped with `Error: ... ARRIVAL date ...`
    (exit 2, "stage failed: computing the gains (.blend_base.json)")."""

    def _projects(self, tmp, **extra):
        return {c: projects_both(
            Path(tmp) / c, accounts=_ACCOUNTS,
            files=_files("CAD" if c == "canada" else "USD"),
            canada=dict({"source_currencies": []}, **extra),
            usa=dict({"source_currencies": []}, **extra))[c]
            for c in COUNTRIES}

    @rule("CA-SL-16")
    @rule("US-WASH-23")
    def test_run_completes_with_one_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            for c, root in self._projects(tmp).items():
                with self.subTest(country=c):
                    r = cli(root, "run", "--no-input")
                    out = r.stdout + r.stderr
                    self.assertEqual(r.returncode, 0, out)
                    self.assertNotIn("Error:", out)
                    self.assertNotIn("DECLARED", out)
                    self.assertNotIn("zero-net", out)
                    self.assertEqual(out.count(
                        "in a taxable loss's 30-day window counted"), 1,
                        out)
                    flat = " ".join(out.split())
                    self.assertIn("- rrsp: SAMPXF", flat)
                    self.assertIn("+80 on 2025-05-09 (loss sale 2025-04-15 "
                                  "in margin)", flat)
                    wash = json.loads((root / "work" / "margin_gains_wash"
                                       ".json").read_text())
                    self.assertAlmostEqual(
                        wash["summary"]["total_gain"], -1000.0, places=2)

    @rule("CA-SL-17")
    @rule("US-WASH-24")
    def test_strict_setting_restores_the_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            for c, root in self._projects(
                    tmp, transfers_as_acquisitions=True).items():
                with self.subTest(country=c):
                    r = cli(root, "run", "--no-input")
                    out = r.stdout + r.stderr
                    self.assertNotEqual(r.returncode, 0, out)
                    self.assertIn("ARRIVAL", out)

    @rule("CA-SL-16")
    def test_quoted_setting_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._projects(
                tmp, transfers_as_acquisitions="true")["canada"]
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("transfers_as_acquisitions must be true or false",
                          " ".join((r.stdout + r.stderr).split()))


if __name__ == "__main__":
    unittest.main()
