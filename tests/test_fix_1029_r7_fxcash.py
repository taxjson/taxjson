"""Fixes of 2026-10-29, round 7, group fxcash: the FX-on-cash ledger v2
(lib/fx_cash_v2, lib/cash_events, bin/taxjson_fx_cash). One test class
per backlog item, each named for it.

Every fixture is SYNTHETIC: invented books (a/manual, b/manual),
invented hashed accounts, small amounts.
"""
import unittest

from tax_rules import rule

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2

A, B = "a/manual", "b/manual"


def tt(line, book=A, n=1):
    """A .tt cash line as lib/cash_events reads it, in `book`."""
    label = book.split("/", 1)[0]
    ev = CE.parse_line(line, f"inputs/{label}/book.tt:{n}")
    ev.update(label=label, file="book.tt", book=book, book_problem="")
    return ev


def cash(events=(), lines=()):
    return {"events": list(events), "lines": list(lines), "problems": [],
            "stablecoins_as_cash": True}


def build(lines, events=(), native=(), rate=1.0, year=2025, **kw):
    kw.setdefault("country", "canada")
    rate_of = rate if callable(rate) else (lambda c, d: rate)
    return V2.build(list(native), cash(events, lines), "CAD", year,
                    rate_of, **kw)


def max_total_cost(doc, cur="USD"):
    """The largest total cost of `cur` the books hold at the end of any
    day (the T1135 simultaneous maximum of the foreign cash)."""
    series = {(r["book"], r["currency"]): r["points"]
              for r in doc["holdings"]["foreign"] if r["currency"] == cur}
    dates = sorted({p[0] for pts in series.values() for p in pts})
    best = 0.0
    for d in dates:
        tot = 0.0
        for pts in series.values():
            v = 0.0
            for p in pts:
                if p[0] <= d:
                    v = p[2]
            tot += v
        best = max(best, tot)
    return round(best, 2)


# ============================================== TJ-0717 an early own
# arrival owns the units once
class TestTJ0717EarlyOwnArrivalOwnedOnce(unittest.TestCase):
    """TJ-0717: b's broker credited an own move on 06-02, a's booked
    it leaving on 06-04. Both accounts held the units in between: the
    T1135 holdings doubled, and when the arrival repaid b's debt from
    the pool the later departure put a's units back at no cost — a
    phantom gain on the next round trip."""

    @rule("CA-FX-07")
    def test_the_t1135_holdings_count_the_units_once(self):
        doc = build([tt("CASHOPEN 2025-01-01 USD 100 125"),
                     tt("CASHMOVE 2025-06-04 USD -100 own"),
                     tt(CLOSE0),
                     tt("CASHOPEN 2025-01-01 USD 0 0", B),
                     tt("CASHMOVE 2025-06-02 USD 100 own", B),
                     tt("CASHBAL 2025-12-31 USD 100", B)],
                    holdings=True)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(max_total_cost(doc), 125.0)
        self.assertEqual(doc["close"]["books"][B]["USD"],
                         {"units": 100.0, "cost": 125.0})

    @rule("CA-FX-07")
    def test_a_debt_the_early_arrival_repays_leaves_no_phantom_pool(self):
        doc = build([tt("CASHOPEN 2025-01-01 USD 300 300"),
                     tt("CASHMOVE 2025-06-04 USD -300 own"),
                     tt("CASHMOVE 2025-06-05 USD 300 cost=600"),
                     tt("CASHMOVE 2025-06-06 USD -300 proceeds=600"),
                     tt(CLOSE0),
                     tt("CASHOPEN 2025-01-01 USD -300 300", B),
                     tt("CASHMOVE 2025-06-02 USD 300 own", B),
                     tt("CASHBAL 2025-12-31 USD 0", B)])
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 0.0)
        self.assertEqual(doc["pools_year_end"], {})

    @rule("CA-FX-07")
    def test_the_sender_reconciles_between_arrival_and_departure(self):
        # a's statement on 06-03 still shows the 100 (it leaves 06-04).
        doc = build([tt("CASHOPEN 2025-01-01 USD 100 125"),
                     tt("CASHBAL 2025-06-03 USD 100"),
                     tt("CASHMOVE 2025-06-04 USD -100 own"),
                     tt(CLOSE0),
                     tt("CASHOPEN 2025-01-01 USD 0 0", B),
                     tt("CASHMOVE 2025-06-02 USD 100 own", B),
                     tt("CASHBAL 2025-12-31 USD 100", B)])
        self.assertEqual(doc["status"], "computed", doc["problems"])

    @rule("CA-FX-07")
    def test_same_day_control_unchanged(self):
        doc = build([tt("CASHOPEN 2025-01-01 USD 300 300"),
                     tt("CASHMOVE 2025-06-02 USD -300 own"),
                     tt("CASHMOVE 2025-06-05 USD 300 cost=600"),
                     tt("CASHMOVE 2025-06-06 USD -300 proceeds=600"),
                     tt(CLOSE0),
                     tt("CASHOPEN 2025-01-01 USD -300 300", B),
                     tt("CASHMOVE 2025-06-02 USD 300 own", B),
                     tt("CASHBAL 2025-12-31 USD 0", B)])
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 0.0)
        self.assertEqual(doc["pools_year_end"], {})


CLOSE0 = "CASHBAL 2025-12-31 USD 0"
IB = "margin/ib"


def ib_ev(ev, file):
    """A broker event of the IB book `margin/ib`, read from `file`."""
    ev.update(label="margin", book=IB, broker="ib", file=file)
    return ev


def fill(file, line, fee):
    """One IB Forex fill buying 100 USD for 100 CAD on 06-02 with a
    commission of `fee` CAD, from line `line` of `file`."""
    where = f"{file}:{line}"
    cv = ib_ev(CE.conv("2025-06-02", "CAD", 100.0, "USD", 100.0,
                       where=where, desc="Forex USD.CAD"), file)
    fe = ib_ev(CE.flow("2025-06-02", "CAD", -fee, where=where,
                       desc="Forex USD.CAD commission"), file)
    fe["_fee_of"] = where
    return [cv, fe]


# ============================================== TJ-0860 a fill and its
# commission deduplicated together
class TestTJ0860OverlapKeepsEachFillsOwnCommission(unittest.TestCase):
    """TJ-0860: a.csv holds two equal fills (commissions 1 and 2 CAD)
    and z.csv only the first. The conversions kept were a.csv's (it
    holds two), the 1 CAD fee z.csv's (the file tie-break): it matched
    no kept conversion and its CAD was dropped — the gain 1 too high."""

    def _doc(self, files):
        evs = []
        for f in files:
            evs += fill(f, 5, 1.0)
            if f == "a.csv":
                evs += fill(f, 6, 2.0)
                evs.append(ib_ev(CE.conv("2025-06-03", "USD", 200.0, "CAD",
                                         600.0, where=f"{f}:7"), f))
        evs = CE.dedup_across_files(evs)
        lines = [tt("CASHOPEN 2025-01-01 USD 0 0", IB),
                 tt(CLOSE0, IB)]
        return build(lines, evs), evs

    @rule("CA-FX-07")
    def test_an_overlapping_file_changes_no_figure(self):
        one, _ = self._doc(["a.csv"])
        both, evs = self._doc(["a.csv", "z.csv"])
        self.assertEqual(one["status"], "computed", one["problems"])
        self.assertEqual(both["status"], "computed", both["problems"])
        # 200 USD cost 203 CAD (the fills and their commissions), sold
        # for 600: a gain of 397.
        self.assertEqual(one["net_gain"], 397.0)
        self.assertEqual(both["net_gain"], 397.0)
        self.assertEqual(sorted((e["file"], e["kind"], e["where"])
                                for e in evs),
                         [("a.csv", "FLOW", "a.csv:5"),
                          ("a.csv", "FLOW", "a.csv:6"),
                          ("a.csv", "FXCONV", "a.csv:5"),
                          ("a.csv", "FXCONV", "a.csv:6"),
                          ("a.csv", "FXCONV", "a.csv:7")])

    def test_the_overlap_alone_is_still_kept_once(self):
        evs = CE.dedup_across_files(fill("a.csv", 5, 1.0)
                                    + fill("z.csv", 5, 1.0))
        self.assertEqual(sorted((e["file"], e["kind"]) for e in evs),
                         [("z.csv", "FLOW"), ("z.csv", "FXCONV")])


# ============================================== TJ-0722 a commission that
# takes all the currency bought
class TestTJ0722CommissionTakesTheWholeReceipt(unittest.TestCase):
    """TJ-0722: an IB Forex order bought 2 USD (or 1) with a 2 USD
    minimum commission. Folded into the units bought (TJ-0621), the
    net was 0 — a ZeroDivisionError on repaying the account's debt —
    or -1: held as nothing bought, the account kept the dollar the
    commission took (100 where the statement says 99)."""

    def _doc(self, bought, opening, closing):
        evs = fill("a.csv", 5, 0.0)[:1]
        evs[0].update(from_amt=round(bought * 1.35, 2), to_amt=bought)
        fe = ib_ev(CE.flow("2025-06-02", "USD", -2.0, where="a.csv:5",
                           desc="Forex USD.CAD commission"), "a.csv")
        fe["_fee_of"] = "a.csv:5"
        lines = [tt(f"CASHOPEN 2025-01-01 USD {opening}", IB),
                 tt(f"CASHBAL 2025-12-31 USD {closing}", IB)]
        return build(lines, evs + [fe], rate=1.35)

    @rule("CA-FX-07")
    def test_a_commission_equal_to_the_receipt_leaves_the_debt(self):
        doc = self._doc(2.0, "-100 130", "-100")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["close"]["books"][IB]["USD"],
                         {"units": -100.0, "cost": 130.0})
        self.assertEqual(doc["net_gain"], 0.0)
        self.assertTrue(any("took all the USD it bought" in n
                            for n in doc["notes"]), doc["notes"])

    @rule("CA-FX-07")
    def test_a_commission_beyond_the_receipt_takes_the_rest(self):
        # 100 USD cost 125; the commission takes the 1 bought and 1
        # more, spent at 1.35: a gain of 1.35 - 1.25 = 0.10.
        doc = self._doc(1.0, "100 125", "99")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["close"]["books"][IB]["USD"],
                         {"units": 99.0, "cost": 123.75})
        self.assertEqual(doc["net_gain"], 0.1)
        # The statement's 99 is the ledger's too (not within the 1.00
        # tolerance of 100).
        self.assertEqual([r["ledger"] for r in doc["reconciliation"]],
                         [99.0])

    @rule("CA-FX-07")
    def test_between_two_foreign_currencies(self):
        # 10 EUR (cost 13) for 2 USD, the commission 2 USD: the euros
        # are disposed of at the day's value (14: a gain of 1), and no
        # US dollars are acquired.
        cv = ib_ev(CE.conv("2025-06-02", "EUR", 10.0, "USD", 2.0,
                           where="a.csv:5"), "a.csv")
        fe = ib_ev(CE.flow("2025-06-02", "USD", -2.0, where="a.csv:5"),
                   "a.csv")
        fe["_fee_of"] = "a.csv:5"
        rates = {"EUR": 1.4, "USD": 1.35}
        doc = build([tt("CASHOPEN 2025-01-01 EUR 10 13", IB),
                     tt("CASHOPEN 2025-01-01 USD 0 0", IB),
                     tt("CASHBAL 2025-12-31 EUR 0", IB),
                     tt("CASHBAL 2025-12-31 USD 0", IB)], [cv, fe],
                    rate=lambda c, d: rates.get(c))
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["net_gain"], 1.0)
        self.assertEqual(doc["pools_year_end"], {})

    @rule("CA-FX-07")
    def test_a_larger_receipt_still_folds_the_commission(self):
        doc = self._doc(10.0, "0 0", "8")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["close"]["books"][IB]["USD"],
                         {"units": 8.0, "cost": 13.5})


# ============================================== TJ-0840 two CASHOPEN lines
# of the base currency
class TestTJ0840TwoBaseCurrencyOpenings(unittest.TestCase):
    """TJ-0840: two `CASHOPEN 2025-01-01 CAD` lines for one book (100
    and 200) were accepted, the first taken: the order of the lines
    chose the T1135 cash cost. A foreign currency's second line is
    refused; the base currency's is now too, naming both lines."""

    def _doc(self, first, second, closing):
        return build([tt(f"CASHOPEN 2025-01-01 CAD {first} 0", A, 1),
                      tt(f"CASHOPEN 2025-01-01 CAD {second} 0", A, 2),
                      tt(f"CASHBAL 2025-12-31 CAD {closing}", A, 3)],
                     holdings=True)

    def test_refused_naming_both_lines(self):
        for first, second in ((100, 200), (200, 100)):
            with self.subTest(first=first):
                doc = self._doc(first, second, first)
                self.assertEqual(doc["status"], "not_computed")
                texts = [p["text"] for p in doc["problems"]]
                self.assertEqual(len(texts), 1, texts)
                self.assertIn("inputs/a/book.tt:2: a second CASHOPEN for "
                              "a/manual CAD (the first: "
                              "inputs/a/book.tt:1)", texts[0])
                row, = doc["holdings"]["base"]
                self.assertFalse(row["ok"])
                self.assertIn("a second CASHOPEN", row["why"])

    def test_one_line_still_gives_the_balance(self):
        doc = build([tt("CASHOPEN 2025-01-01 CAD 100 0", A, 1),
                     tt("CASHBAL 2025-12-31 CAD 100", A, 3)],
                    holdings=True)
        self.assertEqual(doc["status"], "computed", doc["problems"])
        row, = doc["holdings"]["base"]
        self.assertTrue(row["ok"], row)
        self.assertEqual(row["points"], [["2024-12-31", 100.0]])


# ============================================== TJ-0834 a carried book
# whose account has no file this year
class TestTJ0834CarriedAccountKeepsItsOwnName(unittest.TestCase):
    """TJ-0834: two IB accounts whose hashes share four characters
    (books `ib:31357`, `ib:31355`) closed 2025 with 100 USD each. In
    2026 one has no file: it was named with four characters, the other
    one's book, and its balance dropped as "replaced by a CASHOPEN"."""
    HA, HB, HC = "3135769a44", "31355d479e", "7f20aa0b11"

    @staticmethod
    def _books(accts):
        b = CE.Books()
        for a in accts:
            b.add("margin", "ib", a)
        return b

    def _line(self, text, book):
        e = CE.parse_line(text, "inputs/margin/cash.tt:1")
        e.update(label="margin", book=book, file="cash.tt",
                 book_problem="")
        return e

    def _prior(self):
        old = self._books([self.HA, self.HB])
        lines = [self._line("CASHOPEN 2025-01-01 USD 100 100",
                            old.name("margin", "ib", a))
                 for a in (self.HA, self.HB)]
        doc = V2.build([], {"events": [], "lines": lines, "books": old,
                            "problems": []}, "CAD", 2025,
                       lambda c, d: 1.0, country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        return doc["close"]

    @rule("CA-FX-07")
    def test_the_absent_account_keeps_its_balance(self):
        books = self._books([self.HA, self.HC])
        name = books.name("margin", "ib", self.HA)
        lines = [self._line("FXCONV 2026-06-02 USD 100 CAD 500", name),
                 self._line("CASHBAL 2026-06-03 USD 0", name)]
        doc = V2.build([], {"events": [], "lines": lines, "books": books,
                            "problems": []}, "CAD", 2026,
                       lambda c, d: 1.0, country="canada",
                       carry=self._prior())
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 100.0, "acb": 100.0}})
        self.assertEqual(doc["close"]["books"]["margin/ib:31355"]["USD"],
                         {"units": 100.0, "cost": 100.0})
        # Its account is recorded, so the next year places it again.
        self.assertEqual(doc["close"]["accounts"]["margin/ib:31355"],
                         self.HB)
        self.assertFalse(any("replaces" in n for n in doc["notes"]),
                         doc["notes"])

    def test_two_carried_books_in_one_are_refused(self):
        # A damaged record: two carried books whose accounts both name
        # one book this year — refused, never one of them dropped.
        carry = {"year": 2025, "currency": "CAD",
                 "books": {"margin/ib:aaaa": {"USD": {"units": 1.0,
                                                      "cost": 1.0}},
                           "margin/ib:bbbb": {"USD": {"units": 2.0,
                                                      "cost": 2.0}}},
                 "accounts": {"margin/ib:aaaa": self.HA,
                              "margin/ib:bbbb": self.HA}}
        problems = []
        opening = {}
        V2._carry_openings(carry, opening,
                           self._books([self.HA, self.HC]), "CAD", 2026,
                           problems)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("carries USD for both margin/ib:aaaa and "
                      "margin/ib:bbbb", problems[0]["text"])


# ============================================== TJ-0724 the $200 is the
# currency's alone
class TestTJ0724DebtGainsCountOutsideTheExemption(unittest.TestCase):
    """TJ-0724: a USD 300 debt that cost 390 repaid with USD bought for
    300 realises 90 on the debt (s.39(2)); the $200 exemption of
    s.39(1.1) is for disposing of currency only, and it erased that 90
    too. Own currency repaying a debt was one gain at the pool's cost,
    so the currency's part and the debt's could not be told apart."""

    def _verdict(self, doc, country="canada"):
        from taxjson.bin.taxjson_fx_cash import v2_verdict
        self.assertEqual(doc["status"], "computed", doc["problems"])
        return v2_verdict(doc, country)

    @rule("CA-FX-07")
    def test_a_debt_gain_or_loss_counts_in_full(self):
        for opening, paid, gain in (("-300 390", 300, 90.0),
                                    ("-300 300", 390, -90.0)):
            with self.subTest(gain=gain):
                doc = build([tt(f"CASHOPEN 2025-01-01 USD {opening}"),
                             tt(f"FXCONV 2025-03-03 CAD {paid} USD 300"),
                             tt(CLOSE0)])
                self.assertEqual(doc["per_currency"]["USD"]["debt_gain"],
                                 gain)
                v = self._verdict(doc)
                self.assertEqual(v["reportable"], gain)
                self.assertIn("counts in full", v["note"])

    @rule("CA-FX-07")
    def test_a_currency_gain_within_200_is_still_nil(self):
        doc = build([tt("CASHOPEN 2025-01-01 USD 300 300"),
                     tt("FXCONV 2025-03-03 USD 300 CAD 390"),
                     tt(CLOSE0)])
        self.assertEqual(doc["net_gain"], 90.0)
        self.assertEqual(self._verdict(doc)["reportable"], 0.0)

    @rule("CA-FX-07")
    def test_own_currency_repaying_a_debt_is_two_realisations(self):
        # a holds 100 USD costing 100; b owes 100 USD borrowed at 130;
        # a's 100 repay it on a day of 1.50: the currency gains 50
        # (within the $200: nil), the debt loses 20 (in full).
        doc = build([tt("CASHOPEN 2025-01-01 USD 100 100"),
                     tt("CASHMOVE 2025-03-03 USD -100 own"),
                     tt(CLOSE0),
                     tt("CASHOPEN 2025-01-01 USD -100 130", B),
                     tt("CASHMOVE 2025-03-03 USD 100 own", B),
                     tt("CASHBAL 2025-12-31 USD 0", B)], rate=1.5)
        usd = doc["per_currency"]["USD"]
        self.assertEqual((usd["gain"], usd["debt_gain"]), (50.0, -20.0))
        self.assertEqual(doc["net_gain"], 30.0)
        self.assertEqual(self._verdict(doc)["reportable"], -20.0)

    @rule("US-FX-03")
    def test_us_reports_the_full_net(self):
        doc = build([tt("CASHOPEN 2025-01-01 USD -300 390"),
                     tt("FXCONV 2025-03-03 CAD 300 USD 300"),
                     tt(CLOSE0)])
        self.assertEqual(self._verdict(doc, "usa")["reportable"], 90.0)


# ============================================== TJ-0846 a broker's cash
# return of capital
def roc(source, amount=100.0, d="2025-06-02"):
    """The return-of-capital ADJUST a broker parser books
    (tx_roc_adjust: net_amount = -cash received), in book a/manual."""
    return {"action": "ADJUST", "type": "roc", "date": d,
            "date_settle": d, "time": "09:30:00", "symbol": "ZZR.TO",
            "quantity": 0.0, "currency": "USD", "net_amount": -amount,
            "gross_amount": 0.0, "account": "a", "source": source,
            "description": "ZZR(CA0000000001) Return of Capital",
            "_book": A}


class TestTJ0846BrokerReturnOfCapitalIsCash(unittest.TestCase):
    """TJ-0846: IB paid 100 USD of return of capital: its parser books
    an ADJUST (the security's cost), the FX ledger skipped every
    ADJUST, and IB's cash events read no Dividends row — the receipt
    was in neither, and selling the 200 USD made up a 100 USD debt."""

    def _doc(self, row):
        rates = {"2025-06-02": 1.5, "2025-09-02": 1.6}
        return build([tt("CASHOPEN 2025-01-01 USD 100 130"),
                      tt("FXCONV 2025-09-02 USD 200 CAD 320"),
                      tt(CLOSE0)], native=[row],
                     rate=lambda c, d: rates.get(d))

    @rule("CA-FX-07")
    def test_the_cash_comes_in(self):
        for src in ("ib.csv", "ib.csv#2"):
            with self.subTest(source=src):
                doc = self._doc(roc(src))
                self.assertEqual(doc["status"], "computed",
                                 doc["problems"])
                # 200 USD costing 130 + 150 = 280, sold for 320.
                self.assertEqual(doc["net_gain"], 40.0)
                self.assertEqual(doc["borrowed"], [])

    @rule("CA-FX-07")
    def test_a_tt_adjust_moves_no_cash(self):
        from taxjson.bin.taxjson_fx_cash import _flows
        self.assertIsNone(_flows(roc("book.tt")))
        self.assertIsNone(_flows(dict(roc("ib.csv"), type="")))
        self.assertEqual(_flows(roc("ib.csv")), 100.0)
        # A broker's reversal of one takes the cash back out.
        self.assertEqual(_flows(roc("ib.csv", amount=-100.0)), -100.0)


# ============================================== TJ-0720 the trade-date
# pool's average in every account
class TestTJ0720TradeDatePoolCostInEveryAccount(unittest.TestCase):
    """TJ-0720: on the trade-date basis, b's sale of December 31
    (settling January 2) brings in 100 USD worth 200; a holds 100 USD
    costing 100. One pool: 200 USD costing 300, 150 in each account.
    The reconstruction gave b's at their own cost and left a's at the
    settled average (100): a's year-end cash cost 100, not 150."""

    def _doc(self, settle):
        sale = {"action": "BUYSELL", "date": "2025-12-31",
                "date_settle": settle, "time": "10:00:00",
                "symbol": "ZZC.TO", "quantity": -10, "net_amount": 100.0,
                "currency": "USD", "account": "b", "_book": B}
        return build([tt("CASHOPEN 2025-01-01 USD 100 100"),
                      tt("CASHBAL 2025-12-31 USD 100"),
                      tt("CASHOPEN 2025-01-01 USD 0 0", B),
                      tt("CASHBAL 2025-12-31 USD 100", B)],
                     native=[sale], rate=2.0, holdings=True,
                     holdings_basis="trade")

    @staticmethod
    def _year_end(doc):
        return {r["book"]: r["points"][-1][1:]
                for r in doc["holdings"]["foreign"]}

    @rule("CA-RPT-20")
    def test_the_pending_receipt_moves_every_accounts_average(self):
        for settle in ("2026-01-02", "2025-12-31"):
            with self.subTest(settle=settle):
                doc = self._doc(settle)
                self.assertEqual(doc["status"], "computed",
                                 doc["problems"])
                self.assertEqual(self._year_end(doc),
                                 {A: [100.0, 150.0], B: [100.0, 150.0]})
                self.assertEqual(max_total_cost(doc), 300.0)


# ============================================== TJ-0656 an own move across
# December 31
class TestTJ0656OwnMoveAcrossTheYearEnd(unittest.TestCase):
    """TJ-0656: a's 100 USD (cost 125) leave on December 31 as an own
    move and arrive in b on January 2. The year was refused ("never
    arrived"), and its close had no place for money in transit."""
    AIB, BIB = "a/ib", "b/ib"

    def _lines(self, year, arrival=True):
        a, b = self.AIB, self.BIB
        if year == 2024:
            out = [tt("CASHOPEN 2024-01-01 USD 100 125", a),
                   tt("CASHOPEN 2024-01-01 USD 0 0", b),
                   tt("CASHMOVE 2024-12-31 USD -100 own", a),
                   tt("CASHBAL 2024-12-31 USD 0", a),
                   tt("CASHBAL 2024-12-31 USD 0", b)]
        else:
            out = [tt("CASHMOVE 2024-12-31 USD -100 own", a),
                   tt("CASHBAL 2025-12-31 USD 0", a),
                   tt("CASHBAL 2025-12-31 USD 100", b)]
        if arrival:
            out.append(tt("CASHMOVE 2025-01-02 USD 100 own", b))
        return out

    @rule("CA-FX-07")
    def test_carried_in_transit_and_matched_next_year(self):
        y1 = build(self._lines(2024), year=2024, rate=1.5)
        self.assertEqual(y1["status"], "computed", y1["problems"])
        self.assertEqual(y1["close"]["transit"],
                         {"USD": {"units": 100.0, "cost": 125.0}})
        self.assertEqual(y1["pools_year_end"],
                         {"USD": {"units": 100.0, "acb": 125.0}})
        self.assertIsNone(V2.close_problem(y1["close"], 2024))
        y2 = build(self._lines(2025), year=2025, rate=1.5,
                   carry=y1["close"])
        self.assertEqual(y2["status"], "computed", y2["problems"])
        self.assertEqual(y2["net_gain"], 0.0)
        self.assertEqual(y2["close"]["books"][self.BIB]["USD"],
                         {"units": 100.0, "cost": 125.0})
        self.assertNotIn("transit", y2["close"])

    def test_an_arrival_the_books_do_not_have_is_still_refused(self):
        doc = build(self._lines(2024, arrival=False), year=2024,
                    rate=1.5)
        self.assertEqual(doc["status"], "not_computed")
        self.assertIn("never arrived", doc["problems"][0]["text"])

    def test_a_damaged_transit_is_refused(self):
        bad = {"year": 2024, "currency": "CAD", "books": {},
               "transit": {"USD": {"units": "x", "cost": 1}}}
        self.assertIn("transit.USD", V2.close_problem(bad, 2024))
        doc = build(self._lines(2025), year=2025, rate=1.5, carry=bad)
        self.assertEqual(doc["status"], "not_computed")
        self.assertTrue(any("in transit carries" in p["text"]
                            for p in doc["problems"]), doc["problems"])


if __name__ == "__main__":
    unittest.main()
