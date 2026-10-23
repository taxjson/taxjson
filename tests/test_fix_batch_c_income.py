"""Bug-review batch C (income): dividend estimation, income dating and
the year hand-off (findings 38, 49, 65, 76, 77, 92 of the 2026-10
review).

All data is synthetic (made-up tickers and accounts).
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from _qa_project import tj, toml


def _div(d, sym, qty, per, acct="margin", cur="CAD", **kw):
    r = {"action": "DIVIDEND", "date": d, "date_settle": d, "symbol": sym,
         "quantity": qty, "gross_amount": qty * per,
         "net_amount": qty * per, "currency": cur, "account": acct}
    r.update(kw)
    return r


class TestDivEstimateSchedule(unittest.TestCase):

    @rule("CA-PLAN-06")
    def test_38_staggered_weekly_postings_stay_weekly(self):
        """Two accounts post a weekly payer three days apart: four weekly
        payments of 1/share, not one chained payment of 4/share."""
        from taxjson.lib import div_estimate as DE
        rows = []
        for acct, dates in (("margin", ["2025-01-06", "2025-01-13",
                                        "2025-01-20", "2025-01-27"]),
                            ("other", ["2025-01-09", "2025-01-16",
                                       "2025-01-23", "2025-01-30"])):
            rows += [(acct, _div(d, "ZZW.TO", 100, 1.0, acct=acct))
                     for d in dates]
        sched = DE.schedule(DE.payments(rows)["ZZW.TO"])
        self.assertEqual([round(p.per_share, 6) for p in sched],
                         [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(
            DE.detect_frequency([p.pay_date for p in sched])[0], "weekly")
        # One account's special beside its regular payment, two days
        # apart, is still one payment of both.
        rows = [("margin", _div("2025-03-14", "ZZS.TO", 10, 1.0)),
                ("margin", _div("2025-03-16", "ZZS.TO", 10, 0.5))]
        sched = DE.schedule(DE.payments(rows)["ZZS.TO"])
        self.assertEqual([round(p.per_share, 6) for p in sched], [1.5])

    @rule("CA-PLAN-06")
    def test_92_two_broker_accounts_in_one_account(self):
        """combined_broker_accounts: each broker account's 10 shares
        receive 10 on one day: 1/share, not 2."""
        from taxjson.lib import div_estimate as DE
        rows = [("margin", _div("2025-02-03", "ZZU.US", 10, 1.0, cur="USD",
                                source_account=src))
                for src in ("synthetic-a", "synthetic-b")]
        rows.append(("margin", {"action": "TAX", "type": "tax",
                                "date": "2025-02-03", "symbol": "ZZU.US",
                                "net_amount": 1.5, "currency": "USD",
                                "account": "margin",
                                "source_account": "synthetic-a"}))
        pays = DE.payments(rows)["ZZU.US"]
        self.assertEqual(sorted(p.per_share for p in pays), [1.0, 1.0])
        self.assertEqual(sorted(p.tax for p in pays), [0.0, 1.5])
        sched = DE.schedule(pays)
        self.assertEqual([p.per_share for p in sched], [1.0])
        # A reversal and its re-posting in one broker account net there,
        # not against the other broker account's payment.
        rows = [("margin", _div("2025-02-03", "ZZU.US", 10, 1.0, cur="USD",
                                source_account=src))
                for src in ("synthetic-a", "synthetic-b")]
        rows.append(("margin", _div("2025-02-05", "ZZU.US", 10, -1.0,
                                    cur="USD",
                                    source_account="synthetic-a")))
        pays = DE.payments(rows)["ZZU.US"]
        self.assertEqual([(p.source, p.per_share) for p in pays],
                         [("synthetic-b", 1.0)])


class TestDivEstimateWindow(unittest.TestCase):

    @rule("CA-PLAN-06")
    def test_49_positional_window_has_the_months_limit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"
            (root / "work").mkdir(parents=True)
            (root / "taxjson.toml").write_text(toml(year=2025))
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [_div("2025-01-02", "ZZD.TO", 10, 1.0)]}))
            (root / "work" / "margin_raw_gains.json").write_text(
                json.dumps({"inventory": [{"symbol": "ZZD.TO", "qty": 10,
                                           "total_cost": 100,
                                           "currency": "CAD"}]}))
            for period in ("100000y", "11y", "121m"):
                with self.subTest(period=period):
                    r = tj(root, "div-estimate", period, "--json",
                           check=False)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertIn("longer than 120 months", r.stderr)
            r = tj(root, "div-estimate", "10y", "--date", "2025-06-01",
                   "--json")
            self.assertEqual(json.loads(r.stdout)["months"], 120)


class TestRocWarningAccount(unittest.TestCase):

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_65_pair_in_another_account_or_currency_keeps_warning(self):
        from taxjson.lib.income_dating import rules_for
        r = rules_for("canada")
        roc = {"action": "ADJUST", "type": "roc", "date": "2025-01-10",
               "symbol": "ZZT.UN.TO", "account": "margin",
               "currency": "CAD", "net_amount": -10.0}

        def pair(acct, cur):
            return [{"action": "ADJUST", "date": d, "symbol": "ZZT.UN.TO",
                     "account": acct, "currency": cur, "net_amount": n}
                    for d, n in (("2024-12-31", -10.0),
                                 ("2025-01-10", 10.0))]
        self.assertEqual(len(r.warnings([roc], 2025)), 1)
        self.assertEqual(len(r.warnings([roc] + pair("other", "CAD"),
                                        2025)), 1)
        self.assertEqual(len(r.warnings([roc] + pair("margin", "USD"),
                                        2025)), 1)
        self.assertEqual(r.warnings([roc] + pair("margin", "CAD"), 2025),
                         [])
        # A pair row naming no account still counts (as before).
        self.assertEqual(r.warnings([roc] + pair("", "CAD"), 2025), [])


class TestHandoffBoundary(unittest.TestCase):
    CFG = {"settings": {"country": "canada"},
           "accounts": {"a": {"type": "taxable"},
                        "b": {"type": "taxable"}}}

    @staticmethod
    def _rec(acct, d):
        return {"account": acct, "action": "DIVIDEND", "date": d,
                "tax_date": d, "symbol": "ZZH.US", "quantity": 0,
                "net": 10}

    def _issues(self, cache, rows_b, rec_rows):
        from taxjson.lib import handoff as H
        (cache / "b_base.json").write_text(json.dumps(
            {"transactions": rows_b}))
        return H._boundary_issues(cache, self.CFG,
                                  {"year": 2024, "boundary_rows": rec_rows},
                                  "settle", set())

    @rule("CA-RPT-08")
    def test_76_another_accounts_row_is_not_this_one(self):
        """The closed record has account a's Dec 31 dividend; this
        project's account b has an identical one the closed books never
        had: it is in neither return."""
        row = {"action": "DIVIDEND", "date": "2024-12-31",
               "date_settle": "2024-12-31", "symbol": "ZZH.US",
               "quantity": 0, "net_amount": 10, "account": "b"}
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            out = self._issues(cache, [row], [self._rec("a", "2024-12-31")])
            self.assertEqual(len(out), 1, out)
            self.assertIn("neither return", out[0]["why"])
            # The same row in the record under b: matched, no issue.
            self.assertEqual(
                self._issues(cache, [row], [self._rec("b", "2024-12-31")]),
                [])
            # An older record's rows name no account: matched as before.
            old = self._rec("a", "2024-12-31")
            del old["account"]
            self.assertEqual(self._issues(cache, [row], [old]), [])

    @rule("CA-RPT-08")
    def test_77_one_row_here_answers_one_record_row(self):
        """The closed project's inputs held two identical early-January
        dividends; this project has one: one is in neither return."""
        row = {"action": "DIVIDEND", "date": "2025-01-03",
               "date_settle": "2025-01-03", "symbol": "ZZH.US",
               "quantity": 0, "net_amount": 10, "account": "b"}
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            rec = self._rec("b", "2025-01-03")
            out = self._issues(cache, [row], [rec, dict(rec)])
            self.assertEqual(len(out), 1, out)
            self.assertIn("neither return", out[0]["why"])
            self.assertEqual(
                self._issues(cache, [row, dict(row)], [rec, dict(rec)]), [])
            self.assertEqual(len(self._issues(cache, [], [rec, dict(rec)])),
                             2)


if __name__ == "__main__":
    unittest.main()
