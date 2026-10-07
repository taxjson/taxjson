"""Second pre-release review of the journals (v0.24.0):

- 1: the missing-history walks read a day's buys first ONLY on a day that
  holds a journal — the journal legs' own dates (a broker pair id, RBC's
  J~ reference, a .tt JOURNAL line) and, for a pair a journal (or a
  legacy ticker.map JOURNAL line) shows, a day with opposite trades of
  the same quantity on its two DIFFERENT listings. A TOBASE line alone
  is no journal: a sale with no purchase on one listing and a buy of the
  other stays missing history, and a sale-then-rebuy of one listing is
  never a journal;
- 2: the legs of a journal inside one account (a broker pair id, RBC's
  J~ reference, a .tt JOURNAL line) pair inside that account before any
  transfer of another account cancels a leg of the same symbol;
- 7: a .tt JOURNAL line bigger than the broker's journal between the
  same listings one business day away (or on its trades' day) is said;
- 12: a hub's partners of two corporate forms are not pooled; a class,
  warrant or venue suffix is not a listing root; RBC's J~ reference is
  read on RBC's rows only in the walks.

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: 55500001).
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, projects_both

_USA_CAD = {"source_currencies": ["CAD"]}
_QT_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,"
           "Currency,Activity Type,Account #,Account Type\n")


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _run(test, root, *args, ok=True):
    r = cli(root, "run", "--no-input", *args)
    if ok:
        test.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


def _sum(test, root):
    r = cli(root, "sum", "--json")
    test.assertEqual(r.returncode, 0, _out(r)[-2000:])
    return json.loads(r.stdout)


def _short_reported(test, root, sym, said=None):
    """Whether the run, `sum` (its sales with no purchase, in a total or
    not) and find-missing-history each report the sale of `sym` as one
    with no purchase."""
    r = _run(test, root)
    if said is not None:
        said.append(_out(r))
    s = _sum(test, root)
    m = cli(root, "find-missing-history")
    return ("no purchase in your files" in _out(r),
            any(sym in json.dumps(x)
                for k in ("no_purchase_uncovered", "no_purchase_in_totals")
                for x in s.get(k) or []),
            sym in m.stdout)


# ------------------------------------------------------------------ 1

class TestTobaseAloneIsNoJournal(unittest.TestCase):

    def _check(self, country, tmap, sell, buy):
        files = {"ticker.map": tmap,
                 "inputs/margin/m.tt": (
                     f"BUYSELL 2025-03-03 09:00:00 {sell} -100 "
                     f"{'USD' if '.U.' in sell or sell.endswith('.US') else 'CAD'}"
                     f" 7.00 700.00 0.00\n"
                     f"BUYSELL 2025-03-03 10:00:00 {buy} 100 CAD 10.00 "
                     f"1000.00 0.00\n")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files, usa=_USA_CAD)[country]
            said = []
            got = _short_reported(self, root, buy, said)
            self.assertEqual(got, (True, True, True))
            # The line to add if the units were journaled.
            self.assertIn(f"`JOURNAL 2025-03-03 {buy} {sell} 100` (margin)",
                          said[0])

    @rule("CA-XLIST-04")
    def test_canada_currency_lines_tobase_hides_no_missing_purchase(self):
        self._check("canada", "TOBASE QZF.U.TO QZF.TO\n", "QZF.U.TO",
                    "QZF.TO")

    @rule("US-XLIST-03")
    def test_usa_currency_lines_tobase_hides_no_missing_purchase(self):
        self._check("usa", "TOBASE QZF.U.TO QZF.TO\n", "QZF.U.TO",
                    "QZF.TO")

    @rule("CA-XLIST-04")
    def test_canada_interlisted_tobase_hides_no_missing_purchase(self):
        self._check("canada", "TOBASE QZX.US QZX.TO\n", "QZX.US", "QZX.TO")

    @rule("US-XLIST-03")
    def test_usa_interlisted_tobase_hides_no_missing_purchase(self):
        self._check("usa", "TOBASE QZX.US QZX.TO\n", "QZX.US", "QZX.TO")


class TestJournalDaysOnly(unittest.TestCase):
    """A run-joined RBC gambit (J~ legs, no map line) is no short on its
    own days; a later sale-then-rebuy of one listing with no purchase
    before it is missing history."""

    def _files(self, later):
        from test_fix_dated_events import _rbc_gambit
        rbc, tmap = _rbc_gambit(True)
        files = {"inputs/margin/rbc.csv": rbc, "ticker.map": tmap}
        if later:
            files["inputs/margin/m.tt"] = (
                "BUYSELL 2025-06-02 09:00:00 QZD.TO -500 CAD 14.00 "
                "7000.00 0.00\n"
                "BUYSELL 2025-06-02 10:00:00 QZD.TO 500 CAD 14.10 "
                "7050.00 0.00\n")
        return files

    def _check(self, country):
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for later in (False, True):
                root = projects_both(Path(td) / str(later),
                                     files=self._files(later),
                                     usa=_USA_CAD)[country]
                got[later] = _short_reported(self, root, "QZD")
            self.assertEqual(got[False], (False, False, False))
            self.assertEqual(got[True], (True, True, True))

    @rule("CA-XLIST-04")
    def test_canada_later_sale_and_rebuy_is_missing_history(self):
        self._check("canada")

    @rule("US-XLIST-03")
    def test_usa_later_sale_and_rebuy_is_missing_history(self):
        self._check("usa")


# ------------------------------------------------------------------ 2

def _total(test, root):
    return _sum(test, root)["totals"]["total"]


_QD = "QZD US DLR CURRENCY ETF UNIT"


def _qt_arrival(acct_no, day, qty, bv, sold_day, price):
    """Questrade: `qty` QZD.TO arrive from outside the books on `day`
    (stated book value `bv`), all sold on `sold_day` at `price`."""
    return _QT_HDR + (
        f"{day},{day},TF6,QZD.TO,{_QD} TRANSFER BOOK VALUE {bv:.2f},{qty},"
        f"0.00,0.00,0.00,0.00,CAD,Transfers,{acct_no},Margin\n"
        f"{sold_day},{sold_day},Sell,QZD.TO,{_QD},-{qty},{price:.2f},"
        f"{qty * price:.2f},0.00,{qty * price:.2f},CAD,Trades,{acct_no},"
        f"Margin\n")


class TestJournalLegsPairInTheirAccountFirst(unittest.TestCase):
    """Account b's broker journal (RBC's J~ legs; Questrade's BRW pair)
    and account a's transfer-in of the same listing the same day: a's
    arrival keeps its stated book value and b's journal joins its two
    lines — the two accounts' totals add up."""

    def _check(self, country, b_files, a_csv, tmap):
        acc_a = '[accounts.a]\ntype = "taxable"\n'
        acc_b = '[accounts.b]\ntype = "taxable"\n'
        sets = {"a": (acc_a, {"inputs/a/qt.csv": a_csv}),
                "b": (acc_b, b_files),
                "ab": (acc_a + "\n" + acc_b,
                       dict(b_files, **{"inputs/a/qt.csv": a_csv}))}
        with tempfile.TemporaryDirectory() as td:
            got, out = {}, {}
            for k, (acc, files) in sets.items():
                files = dict(files, **{"ticker.map": tmap})
                root = projects_both(Path(td) / k, accounts=acc,
                                     files=files, usa=_USA_CAD)[country]
                out[k] = _out(_run(self, root))
                got[k] = _total(self, root)
            self.assertNotIn("no purchase in your files", out["ab"])
            self.assertAlmostEqual(got["ab"], got["a"] + got["b"],
                                   delta=0.011)
            self.assertNotEqual(round(got["b"], 2), 0.0)

    def _rbc(self, country):
        from test_fix_dated_events import _rbc_gambit
        rbc, tmap = _rbc_gambit(True)
        self._check(country, {"inputs/b/rbc.csv": rbc},
                    _qt_arrival("55500002", "2025-05-06", 1000, 9000.0,
                                "2025-06-02", 12.10), tmap)

    def _brw(self, country):
        from test_fix_journal_books import QT_CSV
        self._check(country, {"inputs/b/questrade.csv": QT_CSV},
                    _qt_arrival("55500002", "2025-09-25", 300, 2700.0,
                                "2025-10-02", 14.50), "")

    @rule("CA-XLIST-04")
    def test_canada_rbc_journal_is_not_cancelled_by_an_arrival(self):
        self._rbc("canada")

    @rule("US-XLIST-03")
    def test_usa_rbc_journal_is_not_cancelled_by_an_arrival(self):
        self._rbc("usa")

    @rule("CA-XLIST-04")
    def test_canada_brw_pair_is_not_cancelled_by_an_arrival(self):
        self._brw("canada")

    @rule("US-XLIST-03")
    def test_usa_brw_pair_is_not_cancelled_by_an_arrival(self):
        self._brw("usa")

    def test_analyze_pairs_referenced_legs_in_their_account(self):
        from taxjson.lib import cross_listings as XL
        nm = ("QZD", "US", "DLR", "CURRENCY", "ETF", "UNIT")

        def leg(acct, broker, sym, q, ref=""):
            return XL.Leg(acct, broker, sym, "2025-05-06", q, name=nm,
                          raw_name=" ".join(nm), ref=ref,
                          journal=broker if ref else "")
        legs = [leg("b", "rbc_direct", "QZD.TO", -1000, "ref|x|1"),
                leg("b", "rbc_direct", "QZD.U.TO", 1000, "ref|x|1"),
                leg("a", "questrade", "QZD.TO", 1000)]
        names = {"QZD.TO": {nm}, "QZD.U.TO": {nm}}
        r = XL.analyze(legs, names, {nm: " ".join(nm)},
                       base_currency="CAD")
        self.assertEqual([(p.out.account, p.out.symbol, p.into.symbol)
                          for p in r["joined"]],
                         [("b", "QZD.TO", "QZD.U.TO")])
        self.assertFalse(legs[2].used)
        # An orphan journal leg never cancels another account's leg.
        legs = [leg("b", "rbc_direct", "QZD.TO", -1000, "ref|x|1"),
                leg("a", "questrade", "QZD.TO", 1000)]
        XL.analyze(legs, names, {nm: " ".join(nm)}, base_currency="CAD")
        self.assertFalse(legs[0].used or legs[1].used)


# ------------------------------------------------------------------ 7

class TestBiggerLineADayFromTheBrokersJournal(unittest.TestCase):
    """RBC dates the gambit's trades May 5 and its J~ legs May 6. A .tt
    line of 1500 dated May 5 (the trade date) or May 7 (one business day
    after the legs) is booked and said as a Warning naming both; on May 6
    it stops the run (partial_overlaps); two business days away it is
    another journal, booked without a word."""

    def _run_line(self, country, day):
        from test_fix_dated_events import _rbc_gambit
        from taxjson.lib import dated_events as DE
        rbc, tmap = _rbc_gambit(True)
        files = {"inputs/margin/rbc.csv": rbc, "ticker.map": tmap,
                 "inputs/margin/j.tt": (f"JOURNAL {day} QZD.TO QZD.U.TO "
                                        f"1500\n")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files, usa=_USA_CAD)[country]
            out = _out(_run(self, root))
            rec = DE.read_state(root / "work" / DE.STATE)["journals"]
            return out, [(j["status"], j["quantity"]) for j in rec]

    def _check(self, country):
        for day in ("2025-05-05", "2025-05-07"):
            out, rec = self._run_line(country, day)
            self.assertEqual(rec, [("booked", 1500.0)])
            self.assertIn(f"inputs/margin/j.tt:1: JOURNAL {day} QZD.TO "
                          f"QZD.U.TO 1500 is booked in full beside the "
                          f"broker's journal of 1000", out)
            self.assertIn("2025-05-06, 2025-05-06", out)
            self.assertIn("date it 2025-05-06", out)
        out, rec = self._run_line(country, "2025-05-08")
        self.assertEqual(rec, [("booked", 1500.0)])
        self.assertNotIn("booked in full beside", out)

    def test_trade_day_two_business_days_before_the_legs(self):
        from types import SimpleNamespace
        from taxjson.lib import cross_listings as XL
        legs = [XL.Leg("m", "rbc_direct", "QZD.TO", "2025-05-07", -1000,
                       ref="ref|2025-05-07|1"),
                XL.Leg("m", "rbc_direct", "QZD.U.TO", "2025-05-07", 1000,
                       ref="ref|2025-05-07|1")]

        def line(day, qty=1500):
            return SimpleNamespace(account="m", date=day, frm="QZD.TO",
                                   to="QZD.U.TO", quantity=qty,
                                   where="j.tt:1", status="booked")
        trades = {("m", "2025-05-05"): {"QZD.TO": [1000.0, 0.0],
                                        "QZD.U.TO": [0.0, 1000.0]}}
        # T+2: the trades' day is two business days before the legs.
        self.assertEqual(len(XL.near_restatements(
            [line("2025-05-05")], legs, trades)), 1)
        self.assertEqual(XL.near_restatements(
            [line("2025-05-05")], legs, {}), [])
        # One business day off the legs; not a bigger line; another day.
        self.assertEqual(len(XL.near_restatements(
            [line("2025-05-08")], legs)), 1)
        self.assertEqual(XL.near_restatements(
            [line("2025-05-08", 1000)], legs), [])
        self.assertEqual(XL.near_restatements(
            [line("2025-05-12")], legs, trades), [])

    @rule("CA-XLIST-04")
    def test_canada_a_bigger_line_a_day_off_is_said(self):
        self._check("canada")

    @rule("US-XLIST-03")
    def test_usa_a_bigger_line_a_day_off_is_said(self):
        self._check("usa")


# ------------------------------------------------------------------ 12

class TestHubPartnersOfTwoForms(unittest.TestCase):
    """Two journals onto one hub whose name states no form, each leg
    named as the hub word for word: an LP partner and a CORP partner
    (their listings' other names) are not one security."""

    def _legs(self, a_names, b_names):
        from test_fix_journal_pairing import _gambit, _names
        legs = (_gambit("2024-02-15", 100, "QZCO", frm="QZA.US",
                        to="QZX.TO")
                + _gambit("2024-06-12", 50, "QZCO", frm="QZB.US",
                          to="QZX.TO"))
        return legs, _names(QZA_US=a_names, QZB_US=b_names,
                            QZX_TO=["QZCO"])

    def _check(self):
        from test_fix_journal_pairing import _run
        r = _run(*self._legs(["QZCO", "QZCO LP"], ["QZCO", "QZCO CORP"]))
        self.assertEqual(r["joined"], [])
        self.assertEqual({p.reason for p in r["suggested"]},
                         {"a listing pairs with two other listings"})
        # A partnership one states and the other does not: apart too.
        r = _run(*self._legs(["QZCO", "QZCO LP"], ["QZCO"]))
        self.assertEqual(r["joined"], [])
        # Partners that state no form, or the same one: still a hub.
        for a, b in ((["QZCO"], ["QZCO"]),
                     (["QZCO", "QZCO CORP"], ["QZCO CORP"])):
            r = _run(*self._legs(a, b))
            self.assertEqual(sorted((p.frm, p.to) for p in r["joined"]),
                             [("QZA.US", "QZX.TO"), ("QZB.US", "QZX.TO")])

    @rule("CA-XLIST-01")
    def test_canada_lp_and_corp_partners_are_not_pooled(self):
        self._check()

    @rule("US-XLIST-01")
    def test_usa_lp_and_corp_partners_are_not_pooled(self):
        self._check()


class TestListingRootIsTheVenueOnly(unittest.TestCase):

    def test_listing_root(self):
        from taxjson.lib.cross_listings import listing_root
        for sym, root in (("QZG.U.TO", "QZG"), ("QZG-U.TO", "QZG"),
                          ("QZG.TO", "QZG"), ("QZG.US", "QZG"),
                          ("QZG.L", "QZG"), ("QZG.B.TO", "QZG.B"),
                          ("QZG.UN.TO", "QZG.UN"), ("QZG.WS", "QZG.WS"),
                          ("QZG.A", "QZG.A"), ("QZG.B", "QZG.B"),
                          ("QZG", "QZG")):
            with self.subTest(sym):
                self.assertEqual(listing_root(sym), root)

    def test_a_class_or_warrant_is_no_evidence(self):
        from taxjson.lib.cross_listings import UNPROVEN, declared_verdict
        for frm, to in (("QZA.WS", "QZA.L"), ("QZB.A", "QZB.B"),
                        ("QZB.A.TO", "QZB.B.TO")):
            with self.subTest(frm=frm, to=to):
                self.assertIn("nothing shows", declared_verdict(
                    frm, to, {}, {}))
        self.assertEqual(UNPROVEN, "unproven")
        self.assertEqual(declared_verdict("QZG.U.TO", "QZG.TO", {}, {}), "")

    def _check(self, country):
        files = {"inputs/margin/m.tt": (
            "BUYSELL 2025-02-03 10:00:00 QZB.A 100 USD 10.00 1000.00 "
            "0.00\n"
            "JOURNAL 2025-03-05 QZB.A QZB.B 100\n")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files, usa=_USA_CAD)[country]
            r = _run(self, root, ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("nothing shows QZB.A and QZB.B are one "
                          "security", _out(r))

    @rule("CA-XLIST-04")
    def test_canada_two_classes_need_evidence(self):
        self._check("canada")

    @rule("US-XLIST-03")
    def test_usa_two_classes_need_evidence(self):
        self._check("usa")


if __name__ == "__main__":
    unittest.main()
