"""Re-audit-2 test pins (tests-pins-04/05/06, planning group):
behaviour that held at the audit but that no test failed on when it was
reverted. Each test names the finding and the mutant it kills.

  A2-0498 / A2-0522
                the country gates of safe-to-sell, harvest, edge-cases,
                the checklist's wash-reviewed step, buy-check and
                sell-check, both directions

All data is synthetic.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import SRC, cli_both, gains_both, projects_both
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

COUNTRIES = ("canada", "usa")


def _row(date_, symbol, qty, net, *, settle=None, account="margin",
         currency="USD", **kw):
    r = dict(action="BUYSELL", date=date_, date_settle=settle or date_,
             time="10:00:00", symbol=symbol, quantity=qty, net_amount=net,
             currency=currency, account=account,
             price=abs(net / qty) if qty else 0.0)
    r.update(kw)
    return r


def _ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


# ------------------------------------------------------- safe-to-sell
class TestSafeToSellWording(unittest.TestCase):
    """A2-0498 / A2-0522 D03 (usa = args.country == "usa"), D04 (the
    SAFE note) and D05 (the 'To fully avoid' line)."""

    def _run(self, country):
        rows = [_row("2026-01-05", "ASG.US", 100, 5000.0)]
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "t.json"
            t.write_text(json.dumps({"transactions": rows}))
            env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_safe_to_sell",
                 "--country", country, "--taxable", str(t),
                 "--date", "2026-09-10"],
                capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    @rule("CA-PLAN-01")
    @rule("US-PLAN-01")
    def test_header_and_notes_by_country(self):
        ca, us = self._run("canada"), self._run("usa")
        self.assertIn("CRA 30-day window", ca.splitlines()[0])
        self.assertIn("would make a loss sale superficial", ca)
        self.assertIn("To fully avoid superficial loss", ca)
        for w in ("§1091", "wash sale"):
            self.assertNotIn(w, ca)
        self.assertIn("§1091 30-day window (trade dates)", us.splitlines()[0])
        self.assertIn("would make a loss sale a wash sale", us)
        self.assertIn("To fully avoid a wash sale", us)
        for w in ("CRA", "superficial"):
            self.assertNotIn(w, us)


# ------------------------------------------------------------- harvest
class TestHarvestLongTermNote(unittest.TestCase):
    """A2-0522 D07: the LT_IN note is US-only (Canada has no holding
    period)."""

    @rule("US-HOLD-01")
    @rule_absent("US-HOLD-01", country="canada")
    def test_lt_in_note_only_in_a_us_project(self):
        from test_harvest import _project, _run
        out = {}
        for c in COUNTRIES:
            with tempfile.TemporaryDirectory() as td:
                gains, _ = _project(td)
                rc, out[c], _err = _run([str(gains), "--no-ibkr",
                                         "--country", c])
            self.assertEqual(rc, 0)
        self.assertIn("LT_IN approximates", out["usa"])
        self.assertNotIn("LT_IN", out["canada"])


# ---------------------------------------------------------- edge-cases
class TestEdgeCasesHeader(unittest.TestCase):
    """A2-0522 D09: the settle-basis header says CRA only in Canada."""

    @rule("CA-RPT-07")
    @rule("US-RPT-05")
    def test_settle_basis_header(self):
        from test_partition_commands import _gains_doc, _tx
        rows = [_row("2025-01-06", "XYZ.US", 100, 5000.0),
                _row("2025-06-02", "XYZ.US", -100, 4000.0,
                     settle="2025-06-03")]
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2025, usa={"tax_date": "settle"},
                              files={"work/margin_base.json": json.dumps(
                                  {"transactions": rows})})
            g = gains_both([_tx(x) for x in rows], year=2025,
                           usa={"tax_date": "settle"})
            for c in COUNTRIES:
                (p[c] / "work" / "margin_gains_wash.json").write_text(
                    _gains_doc(g[c]))
            r = cli_both(p, "edge-cases")
        for c in COUNTRIES:
            self.assertEqual(r[c].returncode, 0, r[c].stderr)
        ca = r["canada"].stdout.splitlines()[0]
        us = r["usa"].stdout.splitlines()[0]
        self.assertIn("date basis: settle (settlement date, CRA)", ca)
        self.assertIn("date basis: settle (settlement date);", us)
        self.assertNotIn("CRA", us)


# ------------------------------------------- checklist wash-reviewed step
class TestChecklistWashReviewedWording(unittest.TestCase):
    """A2-0522 D11 (permanently denied: IRA vs registered/affiliated)
    and D12 (denied, recoverable: basis vs ACB). D13 ('no wash sales')
    is pinned by test_fix_a2_partA.test_no_flags_still_done."""

    def _detail(self, country, row):
        from test_fix_a2_partA import _ctx
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "m_gains.json").write_text(json.dumps(
                {"summary": {"year": 2025}, "transactions": [dict(
                    {"symbol": "XYZ.US", "date": "2025-03-03",
                     "date_settle": "2025-03-04", "qty": -10}, **row)]}))
            r = cl.d_wash_reviewed(_ctx(root, country,
                                        {"m": {"type": "taxable"}}))
        return r.status, r.detail

    @rule("CA-SL-09")
    @rule("US-WASH-09")
    def test_recoverable_denial(self):
        row = {"gain": 0.0, "raw_gain": -50.0, "disallowed_amount": 50.0}
        ca = self._detail("canada", row)
        us = self._detail("usa", row)
        self.assertEqual(ca, ("done",
                              "50.00 denied, all recoverable (added to ACB)"))
        self.assertEqual(us, ("done", "50.00 denied, all recoverable "
                                      "(added to the replacement's basis)"))

    @rule("CA-SL-04")
    @rule("US-WASH-11")
    def test_permanent_denial(self):
        row = {"gain": 0.0, "raw_gain": -50.0, "disallowed_amount": 50.0,
               "permanently_disallowed": 50.0}
        ca = self._detail("canada", row)
        us = self._detail("usa", row)
        self.assertEqual(ca[0], "manual")
        self.assertIn("(registered-account or affiliated-person "
                      "repurchase)", ca[1])
        self.assertEqual(us[0], "manual")
        self.assertIn("(IRA repurchase)", us[1])
        self.assertNotIn("registered", us[1])


# --------------------------------------------------- buy-check/sell-check
class TestBuySellCheckShelteredWording(unittest.TestCase):
    """A2-0522 D21 (buy-check: PERMANENT if bought in an IRA / sheltered)
    and D25 (sell-check: a tax-deferred / registered disposition)."""

    @rule("CA-PLAN-01")
    @rule("US-PLAN-01")
    def test_ira_vs_sheltered_words(self):
        acc = ('[accounts.margin]\ntype = "taxable"\n'
               '[accounts.rrsp]\ntype = "sheltered"\n')
        files = {
            "work/margin_base.json": json.dumps({"transactions": [
                _row(_ago(100), "BND.US", 100, 1500.0),
                _row(_ago(10), "BND.US", -100, 1200.0)]}),
            "work/sheltered_base.json": json.dumps({"transactions": [
                _row(_ago(100), "SHL.US", 50, 500.0, account="rrsp")]})}
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=date.today().year, accounts=acc,
                              files=files)
            buy = cli_both(p, "buy-check", "BND.US")
            sell = cli_both(p, "sell-check", "SHL.US")
        self.assertIn("PERMANENT if bought sheltered",
                      buy["canada"].stdout, buy["canada"].stderr)
        self.assertIn("PERMANENT if bought in an IRA", buy["usa"].stdout,
                      buy["usa"].stderr)
        self.assertIn("held only in sheltered account(s)",
                      sell["canada"].stdout, sell["canada"].stderr)
        self.assertIn("a registered disposition has no tax effect",
                      sell["canada"].stdout)
        self.assertIn("held only in IRA(s)", sell["usa"].stdout,
                      sell["usa"].stderr)
        self.assertIn("a tax-deferred disposition has no tax effect",
                      sell["usa"].stdout)


if __name__ == "__main__":
    unittest.main()
