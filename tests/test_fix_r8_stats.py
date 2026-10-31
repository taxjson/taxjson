"""Fix round 8, group stats: the performance views and an option's premium
around deliveries, returns of capital and snapshots. Every fixture is
SYNTHETIC: made-up tickers, ids and amounts.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

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
    of a premium delivered since the cut, yet shrank `premium_after_cut`."""

    HEAD = ("BUYSELL 2025-01-06 10:00:00 SYN.TO 100 CAD 2 200 0\n"
            "BUYSELL 2025-01-10 10:00:00 SYN.TO -100 CAD .8 80 0\n"
            "BUYSELL 2025-01-13 10:00:00 SYN.TO 100 CAD 1 100 0\n"
            "ADJUST 2025-03-03 10:00:00 SYN.TO CAD -220 type=roc\n")
    PUT = ("BUYSELL 2025-04-01 10:00:00 SYN250516P00000200.TO -1 CAD .2 20 0\n"
           "ASSIGN 2025-05-16 09:30:00 SYN250516P00000200.TO 1 CAD 0 0 0\n"
           "ASSIGN 2025-05-16 10:00:00 SYN.TO 100 CAD .2 20 0\n")
    SALE = "BUYSELL 2025-07-02 10:00:00 SYN.TO -200 CAD .5 100 0\n"

    @rule("CA-RPT-16")
    def test_a_reversal_after_the_delivery(self):
        for back in (10, 110):
            with self.subTest(back=back):
                doc = _stats(self.HEAD + self.PUT + (
                    f"ADJUST 2025-06-01 10:00:00 SYN.TO CAD {back} type=roc\n")
                    + self.SALE)
                self.assertEqual(_pl(doc, "long-shares"), -40.0)
                self.assertEqual(_pl(doc), -20.0)

    @rule("CA-RPT-16")
    def test_controls_no_reversal_and_one_before_the_delivery(self):
        for rev in ("", "ADJUST 2025-03-10 10:00:00 SYN.TO CAD 10 type=roc\n",
                    "ADJUST 2025-03-10 10:00:00 SYN.TO CAD 110 type=roc\n"):
            with self.subTest(rev=rev):
                doc = _stats(self.HEAD + rev + self.PUT + self.SALE)
                self.assertEqual(_pl(doc, "long-shares"), -40.0)
                self.assertEqual(_pl(doc), -20.0)

    @rule("CA-RPT-16")
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


class TestACutDoesNotCoverAWrittenCallsPremium(unittest.TestCase):
    """TJ-1272: a written call's premium is in the sale's proceeds, in no
    basis, so a return of capital's cut does not cancel it. Canada had that
    from TJ-1309; the US engine now says it too."""

    def _book(self, x, c, roc, *, put=True, allow=False):
        sym = f"SYN.{x}"
        opt = f"SYN250117P00001000.{x}"
        call = f"SYN250516C00001000.{x}"
        rows = (f"BUYSELL 2025-01-06 10:00:00 {sym} 100 {c} 2 200 0\n"
                f"BUYSELL 2025-01-10 10:00:00 {sym} -100 {c} .8 80 0\n")
        if put:
            rows += (f"BUYSELL 2025-01-02 10:00:00 {opt} -1 {c} .2 20 0\n"
                     f"ASSIGN 2025-01-17 09:30:00 {opt} 1 {c} 0 0 0\n"
                     f"ASSIGN 2025-01-17 10:00:00 {sym} 100 {c} 1 100 0\n")
        else:
            rows += f"BUYSELL 2025-01-17 10:00:00 {sym} 100 {c} 1 100 0\n"
        rows += (f"ADJUST 2025-03-03 10:00:00 {sym} {c} -{roc} type=roc\n"
                 f"BUYSELL 2025-04-01 10:00:00 {call} -1 {c} .3 30 0\n"
                 f"ASSIGN 2025-05-16 09:30:00 {call} 1 {c} 0 0 0\n"
                 f"ASSIGN 2025-05-16 10:00:00 {sym} -100 {c} 1 100 0\n")
        if allow:
            rows += f'ALLOWLOSS 2025-01-10 {sym} reason="control"\n'
        return rows

    @rule("US-RPT-12")
    def test_us_a_call_and_a_put_in_one_sale(self):
        for roc in (150, 200):
            with self.subTest(roc=roc):
                doc = _stats(self._book("US", "USD", roc), country="usa")
                self.assertEqual(_pl(doc), 30.0)
                self.assertEqual(
                    _pl(doc), _pl(_stats(self._book("US", "USD", roc, allow=True),
                                         country="usa")))

    @rule("US-RPT-12")
    def test_us_only_a_call(self):
        doc = _stats(self._book("US", "USD", 150, put=False), country="usa")
        self.assertEqual(
            _pl(doc), _pl(_stats(self._book("US", "USD", 150, put=False,
                                            allow=True), country="usa")))

    @rule("US-RPT-12")
    def test_us_the_sale_across_two_lots_only_one_of_them_cut(self):
        def book(allow):
            rows = ("BUYSELL 2025-01-06 10:00:00 SYN.US 100 USD 2 200 0\n"
                    "BUYSELL 2025-01-10 10:00:00 SYN.US -100 USD .8 80 0\n"
                    "BUYSELL 2025-01-13 10:00:00 SYN.US 100 USD 1 100 0\n"
                    "BUYSELL 2025-03-01 10:00:00 SYN.US 100 USD 1 100 0\n"
                    "ADJUST 2025-03-03 10:00:00 SYN.US USD -300 type=roc\n"
                    "BUYSELL 2025-04-01 10:00:00 SYN250516C00001000.US -2 USD "
                    ".3 60 0\n"
                    "ASSIGN 2025-05-16 09:30:00 SYN250516C00001000.US 2 USD 0 "
                    "0 0\n"
                    "ASSIGN 2025-05-16 10:00:00 SYN.US -200 USD 1 200 0\n")
            return rows + ('ALLOWLOSS 2025-01-10 SYN.US reason="control"\n'
                           if allow else "")
        self.assertEqual(_pl(_stats(book(False), country="usa")),
                         _pl(_stats(book(True), country="usa")))

    @rule("US-RPT-12")
    def test_control_a_put_alone_is_still_covered(self):
        rows = ("BUYSELL 2025-01-02 10:00:00 SYN250117P00001000.US -1 USD .2 "
                "20 0\n"
                "BUYSELL 2025-01-06 10:00:00 SYN.US 100 USD 2 200 0\n"
                "BUYSELL 2025-01-10 10:00:00 SYN.US -100 USD .8 80 0\n"
                "ASSIGN 2025-01-17 09:30:00 SYN250117P00001000.US 1 USD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 SYN.US 100 USD 1 100 0\n"
                "ADJUST 2025-03-03 10:00:00 SYN.US USD -150 type=roc\n")
        sale = "BUYSELL 2025-05-16 10:00:00 SYN.US -100 USD 1 100 0\n"
        allow = 'ALLOWLOSS 2025-01-10 SYN.US reason="control"\n'
        self.assertEqual(_pl(_stats(rows + sale, country="usa")),
                         _pl(_stats(rows + sale + allow, country="usa")))

    @rule("CA-RPT-16")
    def test_canada_a_call_and_a_put_in_one_sale(self):
        for roc in (150, 200):
            with self.subTest(roc=roc):
                doc = _stats(self._book("TO", "CAD", roc))
                self.assertEqual(_pl(doc), 30.0)


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
        tj(root, "elect", "m", "--set", f"{ev}=rollover_s_86_1", "--hint",
           f"allocated_acb_cad={alloc}")
        tj(root, "run", "--no-input")
        return json.loads(tj(root, "stats", "2025", "--json").stdout)


class TestASpinOffCarriesTheCutToTheChild(unittest.TestCase):
    """TJ-1274: a denied loss and a put in one ACB of 200 (100 - 20 + 120),
    a return of capital that cuts the tally, a spin-off moving half of what
    is left. The child's sale cancels its part of the premium as the
    parent's does: the child had no cut, so it was short by that part."""

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

    @rule("CA-RPT-16", "CA-CORP-06")
    def test_both_sold(self):
        # Cash 50 (-200 +80 +20 -100 +ROC +100), less the return of capital
        # over the gross cost of 100, which the view leaves out.
        for roc, left in ((150, 50), (190, 10)):
            with self.subTest(roc=roc):
                doc = _spin_stats(self._book(roc, self.CHILD + self.PARENT),
                                  left / 2)
                self.assertEqual(_pl(doc, "written-options"), 20.0)
                self.assertEqual(_pl(doc), 0.0)

    @rule("CA-RPT-16", "CA-CORP-06")
    def test_child_sold_gives_what_the_parent_sold_gives(self):
        child = _spin_stats(self._book(150, self.CHILD), 25)
        parent = _spin_stats(self._book(150, self.PARENT), 25)
        self.assertEqual(_pl(child), _pl(parent))
        self.assertEqual(_pl(parent), -50.0)

    @rule("CA-RPT-16", "CA-CORP-06")
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

    @rule("CA-RPT-16", "CA-CORP-06")
    def test_control_no_return_of_capital(self):
        doc = _spin_stats(self.HEAD + self.CHILD + self.PARENT, 100)
        # Cash -200 +80 +20 -100 +50 +50 = -100.
        self.assertEqual(_pl(doc), -100.0)


class TestAManualFlagNamesARealRebuy(unittest.TestCase):
    """TJ-1317: the shares a loss's sale closed never replace it. Only a
    purchase left held after the sale, or one after it, is that rebuy."""

    def _book(self, x, c):
        opt = f"UNO250620C00010000.{x}"
        return (f'OPENING 2024-12-31 {opt} 1 cost=unknown reason="synthetic"\n'
                f"ASSIGN 2025-06-20 16:00:00 {opt} -1 {c} 0 0\n"
                f"ASSIGN 2025-06-20 16:00:01 UNO.{x} 100 {c} 10 1000\n"
                f"BUYSELL 2025-06-25 10:00:00 UNO.{x} -100 {c} 9 900 0\n")

    def _flags(self, rows, country):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"m": rows}, country=country)
            doc = json.loads(next(
                p for p in (root / "work" / "m_gains_wash.json",
                            root / "work" / "m_gains.json")
                if p.exists()).read_text())
            return [w["acquisition_date"]
                    for w in doc.get("superficial_loss_warnings") or []
                    if w.get("acquisition_date")]

    CASES = (("usa", "US", "USD"), ("canada", "TO", "CAD"))

    def test_the_delivered_lot_it_closed_is_no_rebuy(self):
        for country, x, c in self.CASES:
            with self.subTest(country):
                self.assertEqual(self._flags(self._book(x, c), country), [])

    def test_a_later_rebuy_is_named(self):
        for country, x, c in self.CASES:
            with self.subTest(country):
                self.assertEqual(self._flags(
                    self._book(x, c) + f"BUYSELL 2025-06-30 10:00:00 UNO.{x} "
                    f"100 {c} 9 900 0\n", country), ["2025-06-30"])

    def test_a_purchase_left_held_after_the_sale_is_named(self):
        for country, x, c in self.CASES:
            with self.subTest(country):
                book = self._book(x, c).replace(
                    f"BUYSELL 2025-06-25 10:00:00 UNO.{x} -100",
                    f"BUYSELL 2025-06-22 10:00:00 UNO.{x} 50 {c} 10 500 0\n"
                    f"BUYSELL 2025-06-25 10:00:00 UNO.{x} -100")
                got = self._flags(book, country)
                # (a US sale of the older lot, FIFO, at its unknown cost)
                self.assertEqual(got, ["2025-06-22"])

    @rule("CA-ACB-12")
    def test_control_an_older_position_with_a_purchase_kept(self):
        # 100 of unknown cost, 50 bought in the window, 100 sold at a loss:
        # 50 are held, so the purchase stays flagged.
        rows = ('OPENING 2024-12-31 UNO.TO 100 cost=unknown reason="synthetic"\n'
                "BUYSELL 2025-06-20 10:00:00 UNO.TO 50 CAD 10 500 0\n"
                "BUYSELL 2025-06-25 10:00:00 UNO.TO -100 CAD 1 100 0\n")
        self.assertEqual(self._flags(rows, "canada"), ["2025-06-20"])

    @rule("CA-ACB-12")
    def test_control_a_split_among_the_rows_keeps_the_flag(self):
        from taxjson.lib.pipeline import _closed_by_sale

        class Row:
            def __init__(self, action, rid, quantity):
                self.action, self.id, self.quantity = action, rid, quantity
                self.date = "2025-06-20"
        rows = [Row("BUYSELL", "b", 100), Row("SPLIT", "x", 100),
                Row("BUYSELL", "s", -100)]
        self.assertEqual(_closed_by_sale(rows, "s"), set())
        self.assertEqual(_closed_by_sale(rows[:1] + rows[2:], "s"),
                         {id(rows[0])})
        # An unknown sale id says nothing either.
        self.assertEqual(_closed_by_sale(rows[:1], None), set())


class TestExpiryReviewAnUnpairedAssignmentUsedTheLeg(unittest.TestCase):
    """TJ-1302: three written puts, one share leg of 200 at no put's strike:
    the first two premiums use its shares; the third stays unconsumed, as
    the run warns (TJ-1230 had this only for paired options)."""

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


class TestASnapshotAndAPlainRowAtTheStrike(unittest.TestCase):
    """TJ-1332: the snapshot replaced the delivery's row (it is small because
    delivered shares were sold first): a later plain row at the strike takes
    no premium. TJ-1325: nothing says the row is not the delivery and the
    snapshot could hold it: an ordinary trade, and the run says to mark it."""

    CALL = "SYNQ251231C00010000.US"
    PUT = "SYNQ251231P00010000.US"
    HINT = "write it as ASSIGN"

    def _run(self, country, book):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"m": book}, country=country, year=2026)
            r = tj(root, "run", "--no-input")
            doc = json.loads(next(
                p for p in (root / "work" / "m_gains_wash.json",
                            root / "work" / "m_gains.json")
                if p.exists()).read_text())
            return ([t for t in doc["transactions"]
                     if t.get("qty") and t.get("symbol") == "SYNQ.US"],
                    r.stdout + r.stderr)

    def _sold_first(self, cur, buy="BUYSELL", opt=None, q=1, cost=100):
        opt = opt or self.CALL
        back = -q
        return (f"BUYSELL 2024-06-03 10:00:00 {opt} {q} {cur} 1 100 0\n"
                f"ASSIGN 2025-12-29 16:00:00 {opt} {back} {cur} 0 0 0\n"
                f"ASSIGN 2025-12-29 16:00:00 SYNQ.US 100 {cur} 10 1000 0\n"
                f"BUYSELL 2025-12-30 10:00:00 SYNQ.US -50 {cur} 11 550 0\n"
                f"OPENING 2025-12-31 SYNQ.US 50 {cur} {cost} 2024-06-03\n"
                f"{buy} 2026-01-05 10:00:00 SYNQ.US 100 {cur} 10 1000 0\n"
                f"BUYSELL 2026-02-10 10:00:00 SYNQ.US -150 {cur} 12 1800 0\n")

    @rule("CA-OPEN-03")
    def test_a_cut_row_too_small_to_be_the_delivery(self):
        # 50 shares at the strike cannot be the delivery of 100: the premium
        # stays on the later purchase.
        opt = "SYNQ250620P00010000.TO"
        def gain(cut):
            with tempfile.TemporaryDirectory() as td:
                root = _project(td, {"m": (
                    f"BUYSELL 2024-11-04 10:00:00 {opt} -1 CAD 2 200 0\n"
                    f"ASSIGN 2024-12-30 10:00:00 {opt} 1 CAD 0 0 0\n"
                    + cut +
                    "OPENING 2024-12-31 SYNQ.TO 50 CAD 500\n"
                    "BUYSELL 2025-01-02 10:00:00 SYNQ.TO 100 CAD 10 1000 0\n"
                    "BUYSELL 2025-05-05 10:00:00 SYNQ.TO -150 CAD 12 1800 0\n"
                )}, country="canada", year=2025)
                tj(root, "run", "--no-input")
                doc = json.loads(next(
                    p for p in (root / "work" / "m_gains_wash.json",
                                root / "work" / "m_gains.json")
                    if p.exists()).read_text())
                return sum(float(t.get("gain") or 0)
                           for t in doc["transactions"] if t.get("qty"))
        self.assertAlmostEqual(float(gain(
            "BUYSELL 2024-12-29 10:00:00 SYNQ.TO 50 CAD 10 500 0\n")), 500.0,
            places=2)
        self.assertAlmostEqual(float(gain("")), 500.0, places=2)      # control

    @rule("CA-OPEN-03")
    def test_canada_delivered_shares_sold_before_the_snapshot(self):
        rows, out = self._run("canada", self._sold_first("CAD", cost=550))
        self.assertAlmostEqual(float(rows[0]["gain"]), 250.0, places=2)
        self.assertNotIn(self.HINT, out)

    @rule("US-OPEN-03")
    def test_us_delivered_shares_sold_before_the_snapshot(self):
        rows, _out = self._run("usa", self._sold_first("USD", cost=550))
        self.assertAlmostEqual(float(rows[-1]["gain"]), 200.0, places=2)
        self.assertAlmostEqual(float(rows[-1]["cost"]), 1000.0, places=2)

    @rule("CA-OPEN-03")
    def test_canada_written_put_likewise(self):
        book = self._sold_first("CAD", opt=self.PUT, q=-1, cost=450)
        rows, _out = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 350.0, places=2)

    @rule("CA-OPEN-03")
    def test_control_a_marked_row_after_it_is_the_delivery(self):
        # The cut row is another exercise's size: the mark still wins.
        book = self._sold_first("CAD", buy="ASSIGN", cost=550)
        rows, _out = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 150.0, places=2)

    @rule("CA-OPEN-03")
    def test_control_tj_1167_an_off_strike_row_cut_is_not_the_delivery(self):
        # An ordinary purchase the snapshot replaced; the delivery is dated
        # after it, at the strike, and the snapshot cannot hold it.
        book = (f"BUYSELL 2024-06-03 10:00:00 {self.CALL} 1 CAD 1 100 0\n"
                "BUYSELL 2025-12-29 10:00:00 SYNQ.US 50 CAD 8 400 0\n"
                "OPENING 2025-12-31 SYNQ.US 50 CAD 400 2024-06-03\n"
                f"ASSIGN 2025-12-31 16:00:00 {self.CALL} -1 CAD 0 0 0\n"
                "BUYSELL 2026-01-02 09:30:00 SYNQ.US 100 CAD 10 1000 0\n"
                "BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 CAD 12 1200 0\n")
        rows, out = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 200.0, places=2)
        self.assertNotIn(self.HINT, out)

    @rule("CA-OPEN-03")
    def test_tj_1325_a_snapshot_that_could_hold_it_says_to_mark_the_row(self):
        for opening, plain, name in (
                ("200 CAD 1600", "100 CAD 10 1000", "purchase"),
                ("200 CAD 1600", "-100 CAD 10 1000", "sale")):
            with self.subTest(name):
                opt = self.CALL
                back = -1
                if name == "sale":
                    back = 1
                book = (f"BUYSELL 2024-06-03 10:00:00 {opt} {-back} CAD 1 100 "
                        "0\n"
                        f"OPENING 2025-12-31 SYNQ.US {opening} 2024-06-03\n"
                        f"ASSIGN 2025-12-31 16:00:00 {opt} {back} CAD 0 0 0\n"
                        f"BUYSELL 2026-01-02 09:30:00 SYNQ.US {plain} 0\n")
                _rows, out = self._run("canada", book)
                self.assertIn(self.HINT, out)
                self.assertIn(f"{opt}: if the row of 2026-01-02", out)
                # Once, as the engine's passes repeat.
                self.assertEqual(out.count(f"{opt}: if the row of"), 1)

    @rule("CA-OPEN-03")
    def test_control_tj_1297_the_delivery_cut_needs_no_warning(self):
        book = (f"BUYSELL 2024-06-03 10:00:00 {self.CALL} 1 CAD 1 100 0\n"
                f"ASSIGN 2025-12-30 16:00:00 {self.CALL} -1 CAD 0 0 0\n"
                "ASSIGN 2025-12-30 16:00:00 SYNQ.US 100 CAD 10 1000 0\n"
                "OPENING 2025-12-31 SYNQ.US 100 CAD 1100 2024-06-03\n"
                "BUYSELL 2026-01-05 10:00:00 SYNQ.US 100 CAD 10 1000 0\n"
                "BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 CAD 12 1200 0\n")
        rows, out = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 150.0, places=2)
        self.assertNotIn(self.HINT, out)


if __name__ == "__main__":
    unittest.main()
