"""Fix round 8, group stats: the performance views and an option's premium
around deliveries, returns of capital and snapshots. Every fixture is
SYNTHETIC: made-up tickers, ids and amounts.

Formerly: test_fix_r8_stats
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from _qa_project import tj


def _project(td, books, *, country="canada", year=2025):
    root = Path(td) / "p"
    for acct, text in books.items():
        (root / "inputs" / acct).mkdir(parents=True)
        (root / "inputs" / acct / "trades.tt").write_text(text)
    (root / "ticker.map").write_text("")
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{"USD" if country == "usa" else "CAD"}"\n'
        'source_currencies = []\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in books))
    tj(root, "run", "--no-input")
    return root


def _stats(rows, *, country="canada", year=2025):
    with tempfile.TemporaryDirectory() as td:
        root = _project(td, {"m": rows}, country=country, year=year)
        return json.loads(tj(root, "stats", str(year), "--json").stdout)


def _pl(doc, cls=None):
    if cls is None:
        return doc["total"]["net_pl"]
    return (doc["classes"].get(cls) or {"net_pl": 0.0})["net_pl"]


class TestAReversalKeepsThePremiumDeliveredAfterTheCut(unittest.TestCase):
    """TJ-1333: a partial reversal of the return of capital returned none
    of a premium delivered since the cut, yet shrank `premium_after_cut`.

    Formerly: test_fix_r8_stats.TestAReversalKeepsThePremiumDeliveredAfterTheCut"""

    HEAD = ("BUYSELL 2025-01-06 10:00:00 SYN.TO 100 CAD 2 200 0\n"
            "BUYSELL 2025-01-10 10:00:00 SYN.TO -100 CAD .8 80 0\n"
            "BUYSELL 2025-01-13 10:00:00 SYN.TO 100 CAD 1 100 0\n"
            "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -220 type=roc\n")
    PUT = ("BUYSELL 2025-04-01 10:00:00 SYN250516P00000200.TO -1 CAD .2 20 0\n"
           "ASSIGN 2025-05-16 09:30:00 SYN250516P00000200.TO 1 CAD 0 0 0\n"
           "ASSIGN 2025-05-16 10:00:00 SYN.TO 100 CAD .2 20 0\n")
    SALE = "BUYSELL 2025-07-02 10:00:00 SYN.TO -200 CAD .5 100 0\n"

    @rule(R.CA_RPT_16)
    def test_a_reversal_after_the_delivery(self):
        for back in (10, 110):
            with self.subTest(back=back):
                doc = _stats(self.HEAD + self.PUT + (
                    f"ADJUST 2025-06-01 10:00:00 SYN.TO CAD {back} type=roc\n")
                    + self.SALE)
                self.assertEqual(_pl(doc, "long-shares"), -40.0)
                self.assertEqual(_pl(doc), -20.0)

    @rule(R.CA_RPT_16)
    def test_controls_no_reversal_and_one_before_the_delivery(self):
        for rev in ("", "ADJUST 2025-03-10 10:00:00 SYN.TO CAD 10 type=roc\n",
                    "ADJUST 2025-03-10 10:00:00 SYN.TO CAD 110 type=roc\n"):
            with self.subTest(rev=rev):
                doc = _stats(self.HEAD + rev + self.PUT + self.SALE)
                self.assertEqual(_pl(doc, "long-shares"), -40.0)
                self.assertEqual(_pl(doc), -20.0)

    @rule(R.CA_RPT_16)
    def test_control_a_premium_in_the_acb_at_the_cut_stays_covered(self):
        # TJ-1217: the put was delivered before the cut, so a reversal has
        # no late premium to keep and the cut still covers it.
        rows = ("BUYSELL 2025-01-02 10:00:00 SYN250117P00000200.TO -1 CAD .2 "
                "20 0\n"
                "BUYSELL 2025-01-06 10:00:00 SYN.TO 100 CAD 2 200 0\n"
                "BUYSELL 2025-01-10 10:00:00 SYN.TO -100 CAD .8 80 0\n"
                "ASSIGN 2025-01-17 09:30:00 SYN250117P00000200.TO 1 CAD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 SYN.TO 100 CAD 2 200 0\n"
                "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -300 type=roc\n"
                "ADJUST 2025-06-01 10:00:00 SYN.TO CAD 10 type=roc\n"
                "BUYSELL 2025-07-02 10:00:00 SYN.TO -100 CAD .5 50 0\n")
        # Cash -200 +80 +20 -200 +300 -10 +50 = 40, less the return of
        # capital over the gross cost, which the view leaves out (300 - 10
        # against 200): -50. The sale: 50 - 10 cost + 10 tally; the loss -120.
        doc = _stats(rows)
        self.assertEqual(_pl(doc, "written-options"), 20.0)
        self.assertEqual(_pl(doc), -50.0)


SPIN = ("Statement,Header,Field Name,Field Value\n"
        "Statement,Data,BrokerName,Interactive Brokers\n"
        "Corporate Actions,Header,Asset Category,Currency,"
        "Report Date,Date/Time,Description,Quantity,Proceeds,"
        "Value,Realized P/L,Code\n"
        "Corporate Actions,Data,Stocks,CAD,2025-06-02,"
        '"2025-06-02, 10:00:00","ZZQ(CA0000000921) Spinoff 1 for 1 '
        '(ZZK, ZZK INC, CA0000000922)",100,0,100,0,\n')


def _spin_stats(rows, alloc):
    """`rows` and an s.86.1 spin-off of 2025-06-02 moving `alloc` of ACB."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "p"
        inp = root / "inputs" / "m"
        inp.mkdir(parents=True)
        (inp / "trades.tt").write_text(rows)
        (inp / "spin.csv").write_text(SPIN)
        (root / "ticker.map").write_text("")
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.m]\ntype = "taxable"\n')
        tj(root, "run", "--no-input", check=False)
        pend = (root / "work" / "pending_elections.json").read_text()
        ev = re.search(r'"(\d{8}-zzq-zzk-[0-9a-f]+)"', pend)[1]
        tj(root, "elections", "m", "--set", f"{ev}=rollover_s_86_1", "--hint",
           f"allocated_acb_cad={alloc}")
        tj(root, "run", "--no-input")
        return json.loads(tj(root, "stats", "2025", "--json").stdout)


class TestASpinOffCarriesTheCutToTheChild(unittest.TestCase):
    """TJ-1274: a denied loss and a put in one ACB of 200 (100 - 20 + 120),
    a return of capital that cuts the tally, a spin-off moving half of what
    is left. The child's sale cancels its part of the premium as the
    parent's does: the child had no cut, so it was short by that part.

    Formerly: test_fix_r8_stats.TestASpinOffCarriesTheCutToTheChild"""

    PUT = "ZZQ250117P00001000.TO"
    HEAD = (f"BUYSELL 2025-01-02 10:00:00 {PUT} -1 CAD .2 20 0\n"
            "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 100 CAD 2 200 0\n"
            "BUYSELL 2025-01-10 10:00:00 ZZQ.TO -100 CAD .8 80 0\n"
            f"ASSIGN 2025-01-17 09:30:00 {PUT} 1 CAD 0 0 0\n"
            "ASSIGN 2025-01-17 10:00:00 ZZQ.TO 100 CAD 1 100 0\n")
    CHILD = "BUYSELL 2025-07-02 10:00:00 ZZK.TO -100 CAD .5 50 0\n"
    PARENT = "BUYSELL 2025-07-03 10:00:00 ZZQ.TO -100 CAD .5 50 0\n"

    def _book(self, roc, sells):
        return (self.HEAD + f"ADJUST 2025-03-03 10:00:00 ZZQ.TO CAD -{roc} "
                "type=roc\n" + sells)

    @rule(R.CA_RPT_16, R.CA_CORP_06)
    def test_both_sold(self):
        # Cash 50 (-200 +80 +20 -100 +ROC +100), less the return of capital
        # over the gross cost of 100, which the view leaves out.
        for roc, left in ((150, 50), (190, 10)):
            with self.subTest(roc=roc):
                doc = _spin_stats(self._book(roc, self.CHILD + self.PARENT),
                                  left / 2)
                self.assertEqual(_pl(doc, "written-options"), 20.0)
                self.assertEqual(_pl(doc), 0.0)

    @rule(R.CA_RPT_16, R.CA_CORP_06)
    def test_child_sold_gives_what_the_parent_sold_gives(self):
        child = _spin_stats(self._book(150, self.CHILD), 25)
        parent = _spin_stats(self._book(150, self.PARENT), 25)
        self.assertEqual(_pl(child), _pl(parent))
        self.assertEqual(_pl(parent), -50.0)

    @rule(R.CA_RPT_16, R.CA_CORP_06)
    def test_control_a_premium_delivered_after_the_cut_stays_on_the_child(self):
        # TJ-1309: the put arrives after the cut, so the child's part still
        # comes off the child's sale.
        rows = ("BUYSELL 2025-01-06 10:00:00 ZZQ.TO 100 CAD 2 200 0\n"
                "BUYSELL 2025-01-10 10:00:00 ZZQ.TO -100 CAD .8 80 0\n"
                "BUYSELL 2025-01-13 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                "ADJUST 2025-03-03 10:00:00 ZZQ.TO CAD -180 type=roc\n"
                "BUYSELL 2025-04-01 10:00:00 ZZQ250516P00001000.TO -1 CAD .2 "
                "20 0\n"
                "ASSIGN 2025-05-16 09:30:00 ZZQ250516P00001000.TO 1 CAD 0 0 0\n"
                "ASSIGN 2025-05-16 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                "BUYSELL 2025-07-02 10:00:00 ZZK.TO -100 CAD .5 50 0\n"
                "BUYSELL 2025-07-03 10:00:00 ZZQ.TO -200 CAD .25 50 0\n")
        # ACB 220 - 180 + 80 = 120; 60 moved. Cash -200 +80 -100 +180 +20
        # -100 +50 +50 = -20, less the 80 of return of capital over the
        # gross 100: -100.
        doc = _spin_stats(rows, 60)
        self.assertEqual(_pl(doc), -100.0)

    @rule(R.CA_RPT_16, R.CA_CORP_06)
    def test_control_no_return_of_capital(self):
        doc = _spin_stats(self.HEAD + self.CHILD + self.PARENT, 100)
        # Cash -200 +80 +20 -100 +50 +50 = -100.
        self.assertEqual(_pl(doc), -100.0)


class TestExpiryReviewAnUnpairedAssignmentUsedTheLeg(unittest.TestCase):
    """TJ-1302: three written puts, one share leg of 200 at no put's strike:
    the first two premiums use its shares; the third stays unconsumed, as
    the run warns (TJ-1230 had this only for paired options).

    Formerly: test_fix_r8_stats.TestExpiryReviewAnUnpairedAssignmentUsedTheLeg"""

    def _rows(self, shares):
        book = ("BUYSELL 2025-01-02 10:00:00 ZZX.TO 100 CAD 0.50 50 0\n"
                "BUYSELL 2025-11-03 10:00:00 ZZX251231P00010000.TO -1 CAD 0.10 "
                "10 0\n"
                "BUYSELL 2025-11-03 10:01:00 ZZX251231P00011000.TO -1 CAD 0.20 "
                "20 0\n"
                "BUYSELL 2025-11-03 10:02:00 ZZX251231P00012000.TO -1 CAD 0.70 "
                "70 0\n"
                "ASSIGN 2025-12-31 16:00:00 ZZX251231P00010000.TO 1 CAD 0 0 0\n"
                "ASSIGN 2025-12-31 16:00:01 ZZX251231P00011000.TO 1 CAD 0 0 0\n"
                "ASSIGN 2025-12-31 16:00:02 ZZX251231P00012000.TO 1 CAD 0 0 0\n"
                f"BUYSELL 2025-12-31 16:00:03 ZZX.TO {shares} CAD 10.5 "
                f"{shares * 10.5:g} 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"margin": book})
            edge = json.loads(tj(root, "edge-cases", "--json").stdout)
            diag = (root / "reports" / "margin.sum").read_text()
        return ({r["symbol"]: r
                 for r in edge["year_boundary"]["option_expiries"]},
                "unconsumed" in diag)

    def test_the_third_put_says_add_the_missing_share_row(self):
        rows, warned = self._rows(200)
        self.assertTrue(warned)
        self.assertEqual(rows["ZZX251231P00010000.TO"]["lands_in"], 2025)
        self.assertEqual(rows["ZZX251231P00011000.TO"]["lands_in"], 2025)
        third = rows["ZZX251231P00012000.TO"]
        self.assertIsNone(third["lands_in"])
        self.assertIn("add the missing share row", third["why"])

    def test_control_a_leg_of_300_folds_all_three(self):
        rows, warned = self._rows(300)
        self.assertFalse(warned)
        for r in rows.values():
            self.assertEqual(r["lands_in"], 2025)
            self.assertIn("folds into the ZZX.TO leg", r["why"])


if __name__ == "__main__":
    unittest.main()
