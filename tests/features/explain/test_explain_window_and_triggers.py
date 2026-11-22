"""The 2026-10-29 fix round, planning commands: buy-check, sell-check,
rebuy, wash-radar, harvest and wash-sales (backlog TJ-0350, TJ-0351,
TJ-0352, TJ-0243, TJ-0245, TJ-0247, TJ-0244, TJ-0246, TJ-0258,
TJ-0259, TJ-0413, TJ-0157). Synthetic data only: made-up tickers
(SYN.TO, ONE.TO, TWO.TO, ZZU.US) in throwaway projects.

Formerly: test_fix_1029_planning"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_toml import _toml


class TestExplainWindowCountsSettlement(unittest.TestCase):
    """TJ-0258: a Canadian loss sale on Monday 2024-08-05 (settled the
    6th) and a buy 14 days later (settled the 20th). The window counted
    each row's TRADE date from the loss's SETTLEMENT date: the loss sale
    read T-1 and the buy T+13.

    Formerly: test_fix_1029_planning.TestExplainWindowCountsSettlement"""

    def _gain(self):
        from taxjson.engine.ca.taxrules import CanadaTaxRules
        from taxjson.model.transaction import TaxTransaction

        def tx(d, settle, q, px):
            return TaxTransaction(
                action='BUYSELL', date=d, date_settle=settle,
                symbol='ZZI.US', quantity=q, price=px,
                net_amount=-q * px, currency='CAD', account='margin')
        res = CanadaTaxRules().compute_gains([
            tx('2024-02-01', '2024-02-05', 10, 10.0),
            tx('2024-08-05', '2024-08-06', -10, 5.0),
            tx('2024-08-19', '2024-08-20', 10, 5.0)])
        return next(g for g in res['transactions'] if g.get('is_wash_sale'))

    @rule(R.CA_SL_01)
    def test_tj0258_day_counts_settlement_dates(self):
        g = self._gain()
        ww = g['wash_window']
        self.assertEqual((ww['loss_date'], ww['loss_settle']),
                         ('2024-08-05', '2024-08-06'))
        self.assertEqual([(t['days_from_loss'], t['role'])
                          for t in ww['transactions']],
                         [(0, 'loss_sale'), (14, 'trigger')])

    @rule(R.CA_SL_01)
    def test_tj0258_the_views_name_the_settlement_date(self):
        from taxjson.lib import trace_format as TF
        g = self._gain()
        text = "\n".join(TF._report_window_lines(g, 120, "  "))
        self.assertIn("(loss 2024-08-05, settled 2024-08-06)", text)
        self.assertIn("DAY: days from the loss sale's settlement", text)
        rows = [" ".join(ln.split()) for ln in text.splitlines()]
        self.assertTrue(any(r.startswith("T+0 2024-08-05 2024-08-06 margin "
                                         "SELL") for r in rows), text)
        self.assertTrue(any(r.startswith("T+14 2024-08-19 2024-08-20 "
                                         "margin BUY") for r in rows), text)
        trace = "\n".join(TF._render_wash_window(g))
        self.assertIn("LOSS  = 2024-08-05, settled 2024-08-06", trace)
        self.assertNotIn("T-1 ", trace)


class TestExplainAccountAndTriggers(unittest.TestCase):
    """A Canadian 2024 project: margin sells 100 ZZU.TO at a loss on
    2024-08-01, the RRSP buys 60 on 08-15 and margin2 buys 20 on 08-20;
    margin2 has a denied TWO.TO loss of its own.
    TJ-0259: --explain named only the RRSP buy as the trigger.
    TJ-0413: `wash-sales margin --explain` also printed margin2's TWO.TO
    denial, which `wash-sales margin` does not list.

    Formerly: test_fix_1029_planning.TestExplainAccountAndTriggers"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(
            _toml(sheltered=True, year=2024,
                  extra='[accounts.margin2]\ntype = "taxable"\n'))
        books = {
            "margin": ("BUYSELL 2024-03-01 10:00:00 ZZU.TO 100 CAD 10 1000 0\n"
                       "BUYSELL 2024-08-01 10:00:00 ZZU.TO -100 CAD 8 800 0\n"),
            "margin2": ("BUYSELL 2024-08-20 10:00:00 ZZU.TO 20 CAD 8 160 0\n"
                        "BUYSELL 2024-02-01 10:00:00 TWO.TO 10 CAD 10 100 0\n"
                        "BUYSELL 2024-03-04 10:00:00 TWO.TO -10 CAD 5 50 0\n"
                        "BUYSELL 2024-03-11 10:00:00 TWO.TO 10 CAD 5 50 0\n"),
            "rrsp": "BUYSELL 2024-08-15 10:00:00 ZZU.TO 60 CAD 8 480 0\n",
        }
        for acct, text in books.items():
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "m.tt").write_text(text)
        tj(root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _explain(self, *account):
        r = tj(self.root, "wash-sales", *account, "--explain", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_tj0259_every_trigger_is_named(self):
        out = " ".join(self._explain().split())
        self.assertIn("triggered by 2 acquisitions: - 2024-08-15 BUY "
                      "+60.0000 @ 8.0000 account=rrsp [sheltered] - "
                      "2024-08-20 BUY +20.0000 @ 8.0000 account=margin2",
                      out)
        # One trigger keeps its one line.
        self.assertIn("triggered by 2024-03-11 BUY +10.0000 @ 5.0000 "
                      "account=margin2", out)

    def test_tj0413_an_account_shows_its_own_dispositions(self):
        out = self._explain("margin")
        self.assertIn("ZZU.TO — 2024-08-01 — margin", out)
        self.assertNotIn("TWO.TO", out)
        # Still the pooled computation: margin2's buy backs the denial.
        self.assertIn("account=margin2", out)
        out2 = self._explain("margin2")
        self.assertIn("TWO.TO — 2024-03-04 — margin2", out2)
        self.assertNotIn("ZZU.TO — 2024-08-01", out2)
        table = json.loads(tj(self.root, "wash-sales", "margin",
                              "--json").stdout)
        self.assertNotIn("TWO.TO", json.dumps(table))


if __name__ == "__main__":
    unittest.main()
