"""Re-audit-2 test pins (tests-pins-04/05/06, web and planning group):
behaviour that held at the audit but that no test failed on when it was
reverted. Each test names the finding and the mutant it kills.

  A2-0501 / A2-1618 / A2-0522 (D02)
                web what-if: a Canadian crypto sale is superficial-loss
                checked (only US crypto is outside §1091); the sheltered
                loss warning speaks the project's law
  A2-0526 / A2-1575
                web what-if (US): a sale over two FIFO lots with a sibling
                account's purchase in the window sums every lot (wash
                flag, disallowed, permanently disallowed, term) on the
                account's own basis
  A2-0945 / A2-1576
                web what-if: a futures option is priced at its declared
                size and settles per futures_settle
  A2-0498 / A2-0522
                the country gates of safe-to-sell, harvest, edge-cases,
                the checklist's wash-reviewed step, buy-check and
                sell-check, both directions

All data is synthetic.
"""
import contextlib
import io
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

COUNTRIES = ("canada", "usa")


def _row(date_, symbol, qty, net, *, settle=None, account="margin",
         currency="USD", **kw):
    r = dict(action="BUYSELL", date=date_, date_settle=settle or date_,
             time="10:00:00", symbol=symbol, quantity=qty, net_amount=net,
             currency=currency, account=account,
             price=abs(net / qty) if qty else 0.0)
    r.update(kw)
    return r


def _write_books(root, books):
    (root / "work").mkdir(exist_ok=True)
    for name, rows in books.items():
        (root / "work" / f"{name}_base.json").write_text(
            json.dumps({"transactions": rows}))


def _whatif(root, *a, **kw):
    from taxjson.web.context import ProjectContext
    from taxjson.web.data import what_if_sell
    with contextlib.redirect_stderr(io.StringIO()):
        return what_if_sell(ProjectContext.load(root), *a, **kw)


def _ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


# ------------------------------------------------- web what-if: crypto
class TestWhatIfCryptoAndShelteredByCountry(unittest.TestCase):
    """A2-0501 (data.py `and not (acct_cfg.crypto and is_usa)`) and
    A2-1618 / A2-0522 D02 (the sheltered-loss warning's `if is_usa`)."""

    ACC = ('[accounts.btc]\ntype = "taxable"\ncrypto = true\n'
           '[accounts.rrsp]\ntype = "sheltered"\n')

    def _run(self):
        out = {}
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2026, accounts=self.ACC)
            for c in COUNTRIES:
                cur = "CAD" if c == "canada" else "USD"
                _write_books(p[c], {
                    "btc": [_row("2026-01-15", "BTC", 1, 50000.0,
                                 account="btc", currency=cur),
                            _row("2026-09-24", "BTC", 0.1, 4000.0,
                                 account="btc", currency=cur)],
                    "rrsp": [_row("2026-01-15", "ZZZ.US", 100, 2000.0,
                                  account="rrsp", currency=cur)]})
                out[c] = (
                    _whatif(p[c], "btc", "BTC", 1, 40000.0,
                            on="2026-09-30"),
                    _whatif(p[c], "rrsp", "ZZZ.US", 100, 10.0,
                            on="2026-09-30"))
        return out

    @rule("CA-SL-13")
    @rule_absent("CA-SL-13", country="usa")
    @rule("US-WASH-13")
    def test_crypto_rebuy_in_window(self):
        out = self._run()
        ca, us = out["canada"][0], out["usa"][0]
        self.assertTrue(ca["ok"], ca)
        self.assertTrue(ca["is_wash_sale"], ca)
        # 0.1 of the 1 BTC sold is rebought: 10,000 loss x 0.1/1.1.
        self.assertAlmostEqual(ca["disallowed_amount"], 909.09, places=2)
        self.assertTrue(us["ok"], us)
        self.assertFalse(us["is_wash_sale"], us)
        self.assertEqual(us["disallowed_amount"], 0.0)

    @rule("CA-PLAN-03")
    @rule("US-PLAN-03")
    def test_sheltered_loss_warning_wording(self):
        out = self._run()
        ca = " ".join(out["canada"][1]["warnings"])
        us = " ".join(out["usa"][1]["warnings"])
        self.assertIn("registered/sheltered account", ca)
        self.assertIn("never a superficial-loss event", ca)
        self.assertNotIn("IRA", ca)
        self.assertNotIn("wash-sale", ca)
        self.assertIn("tax-deferred IRA", us)
        self.assertIn("never a wash-sale loss", us)
        self.assertNotIn("superficial", us)
        self.assertNotIn("registered", us)


# --------------------------------------- web what-if (US): multi-lot sale
class TestWhatIfUsMultiLot(unittest.TestCase):
    """A2-0526 (C01 is_wash_sale any(), C02 per_account_basis=True, C03
    the account filter, C07 _term_label over every lot, C09 disallowed
    summed) and C08 / A2-1575 (permanently_disallowed and disallowed
    summed over lots)."""

    ACC = ('[accounts.margin]\ntype = "taxable"\n'
           '[accounts.margin2]\ntype = "taxable"\n')

    @rule("US-PLAN-03", "US-WASH-04")
    def test_two_lots_with_a_sibling_account_rebuy(self):
        # Sell 20: lot 1 (10 @50, long-term) gains 300; lot 2 (10 @100,
        # short-term) loses 200, washed by margin2's buy 6 days earlier.
        # margin2's own older lot @200 must stay out of margin's basis.
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2026, accounts=self.ACC)["usa"]
            _write_books(p, {
                "margin": [_row("2024-01-03", "XYZ.US", 10, 500.0),
                           _row("2026-08-03", "XYZ.US", 10, 1000.0)],
                "margin2": [_row("2025-01-03", "XYZ.US", 10, 2000.0,
                                 account="margin2"),
                            _row("2026-09-24", "XYZ.US", 10, 800.0,
                                 account="margin2")]})
            r = _whatif(p, "margin", "XYZ.US", 20, 80.0, on="2026-09-30")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["is_wash_sale"])                       # C01
        self.assertAlmostEqual(r["cost_basis"], 1500.0)          # C02/C03
        self.assertAlmostEqual(r["disallowed_amount"], 200.0)    # C02/C09
        self.assertAlmostEqual(r["economic_gain"], 100.0)
        self.assertAlmostEqual(r["allowed_gain"], 300.0)
        self.assertEqual(r["term"],                              # C07
                         "MIXED (LONG_TERM 300.00, SHORT_TERM 0.00)")
        self.assertIn("margin2", r["basis"])

    @rule("US-PLAN-03", "US-WASH-02")
    def test_two_losing_lots_disallowed_is_summed(self):
        # A2-1575: lots 100 @20 and 50 @15, a 120-unit replacement 5
        # days before; selling 150 @10 loses 1,000 + 250 and 120 units
        # wash 100 + 20 of them: disallowed 1,000 + 100.
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2026)["usa"]
            _write_books(p, {"margin": [
                _row("2026-02-12", "XYZ.US", 100, 2000.0),
                _row("2026-06-22", "XYZ.US", 50, 750.0),
                _row("2026-09-25", "XYZ.US", 120, 1200.0)]})
            r = _whatif(p, "margin", "XYZ.US", 150, 10.0, on="2026-09-30")
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["economic_gain"], -1250.0)
        self.assertAlmostEqual(r["disallowed_amount"], 1100.0)
        self.assertAlmostEqual(r["allowed_gain"], -150.0)

    @rule("US-PLAN-03", "US-WASH-11")
    def test_ira_rebuy_permanent_amount_is_summed(self):
        # C08: two losing lots, an IRA buy of 150 in the window: both
        # lots' losses are permanently disallowed.
        acc = ('[accounts.margin]\ntype = "taxable"\n'
               '[accounts.ira]\ntype = "sheltered"\n')
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2026, accounts=acc)["usa"]
            _write_books(p, {
                "margin": [_row("2026-02-12", "XYZ.US", 100, 2000.0),
                           _row("2026-06-22", "XYZ.US", 50, 750.0)],
                "ira": [_row("2026-09-25", "XYZ.US", 150, 1500.0,
                             account="ira")]})
            r = _whatif(p, "margin", "XYZ.US", 150, 10.0, on="2026-09-30")
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["economic_gain"], -1250.0)
        self.assertAlmostEqual(r["permanently_disallowed"], 1250.0)


# --------------------------------------------- web what-if: futures option
class TestWhatIfFuturesOption(unittest.TestCase):
    """A2-0945 (the declared size, not 100) and A2-1576 (the futures_settle
    branches: next_day and the trade date)."""

    OPT = "F:SXF261217C00500000.TO"

    def _run(self, futures_settle=None):
        with tempfile.TemporaryDirectory() as td:
            extra = ({"futures_settle": futures_settle}
                     if futures_settle else {})
            p = projects_both(td, year=2026, canada=extra)["canada"]
            _write_books(p, {"margin": [_row(
                "2026-06-01", self.OPT, 2, 2000.0, currency="CAD",
                price=5.0, multiplier=200.0)]})
            return _whatif(p, "margin", self.OPT, 1, 4.0, on="2026-12-31")

    @rule("CA-PLAN-03", "CA-DATE-09")
    def test_trade_date_settle_and_declared_size(self):
        r = self._run()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["multiplier"], 200)
        self.assertAlmostEqual(r["proceeds"], 800.0)
        self.assertAlmostEqual(r["cost_basis"], 1000.0)
        self.assertAlmostEqual(r["economic_gain"], -200.0)
        # Settles on the trade date: a 2026 loss, not an equity T+1
        # sale settling in January.
        self.assertEqual(r["settle_date"], "2026-12-31")
        self.assertEqual(r["tax_year"], 2026)

    @rule("CA-PLAN-03", "CA-DATE-10")
    def test_next_day_settle_moves_the_year(self):
        r = self._run("next_day")
        self.assertTrue(r["ok"], r)
        # Jan 1 is a holiday and Jan 2-3 2027 a weekend.
        self.assertEqual(r["settle_date"], "2027-01-04")
        self.assertEqual(r["tax_year"], 2027)


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
