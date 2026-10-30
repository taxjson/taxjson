"""Fixes of 2026-10-30, round 7, wave E, group cash: T1135 cash in
transit, export coverage, Kraken swap fees, Coinbase's blank Total and
repeated hour across exports, and the crypto-sends migration on read-only
inputs. One test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented books and accounts, made-up tickers
and ids, small amounts. Each test's files live in a folder of their own.
"""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from test_fix_1029_r7c_coverage import (_GENERIC_MAP, _cfg, _checklist,
                                        _ib, _project)
from test_fix_1029_r7c_crypto import _HDR as _CB_HDR
from test_fix_1029_r7c_crypto import _brokerage, _fill
from test_fix_1029_r7d_cashcrypto import _at, build, tt
from test_fix_ibparse import _trade

from taxjson.lib import cash_events as CE
from taxjson.lib import checklist as cl
from taxjson.lib import export_coverage as EC
from taxjson.lib import t1135_cash as TC
from taxjson.lib.dispatch import python_module_argv

A, B, C, D = "a/manual", "b/manual", "c/manual", "d/manual"

_Q_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
          "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
          "Activity Type,Account #,Account Type\n")


def _qt(td, sd, action, net, acct):
    kind = "Withdrawals" if action == "WDR" else "Deposits"
    what = "WITHDRAWAL" if action == "WDR" else "DEPOSIT"
    return (f"{td},{sd},{action},,{what},0,0,0,0,{net:.2f},USD,{kind},"
            f"{acct},Margin\n")


def _t1135_project(root, accounts, files, tax_date=None):
    """`accounts`: {name: country or None}; `files`: {account: {file:
    text}}; every .tt also gets a zero CAD opening and balance."""
    root.mkdir()
    head = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = ["USD"]\nfx_cash_ledger = "v2"\n')
    if tax_date:
        head += f'tax_date = "{tax_date}"\n'
    for a, cc in accounts.items():
        head += f'[accounts.{a}]\ntype = "taxable"\n'
        if cc:
            head += f'custodian_country = "{cc}"\n'
    (root / "taxjson.toml").write_text(head)
    (root / "ticker.map").write_text("")
    (root / "tobase.map").write_text("")
    for a, fs in files.items():
        (root / "inputs" / a).mkdir(parents=True)
        for name, text in fs.items():
            if name.endswith(".tt"):
                text += ("CASHOPEN 2025-01-01 CAD 0 0\n"
                         "CASHBAL 2025-12-31 CAD 0\n")
            (root / "inputs" / a / name).write_text(text)


def _t1135(root):
    tj(root, "run", "--no-input")
    return json.loads(tj(root, "t1135", "--json").stdout)


def _year_end_cost(rep):
    return round(sum(p["year_end_cost"] or 0 for p in rep["properties"]
                     if p.get("cash")), 2)


def _crypto_project(td, country="canada"):
    root = Path(td) / "c"
    (root / "inputs" / "coin").mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    src = '[]' if country == "canada" else '["CAD"]'
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        f'country = "{country}"\nbase_currency = "{base}"\n'
        f'source_currencies = {src}\n'
        '[accounts.coin]\ntype = "taxable"\ncrypto = true\n')
    (root / "ticker.map").write_text("")
    return root


def _tt(open_units, open_cost, move=None, move_units=0, bal=0):
    t = f"CASHOPEN 2025-01-01 USD {open_units} {open_cost}\n"
    if move:
        t += f"CASHMOVE {move} USD {move_units} own\n"
    return t + f"CASHBAL 2025-12-31 USD {bal}\n"


# ============================================== TJ-1157, TJ-1185 the trade
# basis of a year-end transit
class TestTJ1157ArrivalTradedInTheYear(unittest.TestCase):
    """TJ-1157: on trade dates the arrival that trades in the year and
    settles after it is already in the receiver's row: the units were
    counted there and again in the sender's transit."""

    def _rep(self, accounts, rate_cost=125, dep="2025-12-29", arr_td="2025-12-31"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, accounts, {
                "a": {"q.csv": _Q_HDR + _qt(dep, dep, "WDR", -100, 99900011),
                      "c.tt": _tt(100, rate_cost, dep, -100, 0)},
                "b": {"q.csv": _Q_HDR + _qt(arr_td, "2026-01-02", "DEP", 100,
                                            99900012),
                      "c.tt": _tt(0, 0, arr_td, 100, 100)}},
                tax_date="trade")
            return _t1135(root)

    @rule("CA-RPT-20")
    def test_counted_once(self):
        rep = self._rep({"a": "USA", "b": "USA"})
        self.assertEqual(_year_end_cost(rep), 125.0)
        self.assertEqual(rep["max_total_cost"], 125.0)
        self.assertTrue(rep["total_settled"])
        self.assertEqual(rep["cash_not_counted"], [])

    @rule("CA-RPT-20")
    def test_the_days_before_the_trade_stay_counted(self):
        # Left December 20, traded in on December 31: in the maximum on
        # the days between, through the sender's row.
        rep = self._rep({"a": "USA", "b": "USA"}, dep="2025-12-20")
        self.assertEqual(rep["max_total_cost"], 125.0)
        self.assertEqual(_year_end_cost(rep), 125.0)

    @rule("CA-RPT-20")
    def test_the_receiver_in_canada_is_not_an_unknown_year_end(self):
        # On trade dates b holds them on December 31; only the days
        # before are named.
        rep = self._rep({"a": "USA", "b": None})
        self.assertEqual(_year_end_cost(rep), 0.0)
        self.assertTrue(rep["total_settled"])
        n, = rep["cash_not_counted"]
        self.assertIn("during the year", n["why"])

    def test_settlement_basis_still_carries_it(self):
        # Control: on settlement dates b does not hold it at the year end.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"a": "USA", "b": "USA"}, {
                "a": {"q.csv": _Q_HDR + _qt("2025-12-29", "2025-12-29", "WDR",
                                            -100, 99900011),
                      "c.tt": _tt(100, 125, "2025-12-29", -100, 0)},
                "b": {"q.csv": _Q_HDR + _qt("2025-12-31", "2026-01-02", "DEP",
                                            100, 99900012),
                      "c.tt": _tt(0, 0, "2025-12-31", 100, 0)}},
                tax_date="settle")
            rep = _t1135(root)
        self.assertEqual(_year_end_cost(rep), 125.0)
        self.assertEqual(rep["max_total_cost"], 125.0)

    def test_the_ledgers_own_close_is_unchanged(self):
        # The close still carries it: only the T1135's view moves.
        lines = [tt("CASHOPEN 2025-01-01 USD 100 125", A, 1),
                 tt("CASHMOVE 2025-12-29 USD -100 own", A, 2),
                 tt("CASHBAL 2025-12-31 USD 0", A, 3),
                 tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                 tt("CASHMOVE 2026-01-02 USD 100 own", B, 2),
                 tt("CASHBAL 2025-12-31 USD 0", B, 3)]
        doc = build(lines, rate=1.5, holdings=True)
        self.assertEqual(doc["close"]["transit"]["USD"]["units"], 100.0)


class TestTJ1185DepartureTradedInTheYear(unittest.TestCase):
    """TJ-1185: a departure that trades in the year and settles after it
    left the row on its trade date and reached no other: a settled zero."""

    def _rep(self, accounts, arrival=True):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            b = {"c.tt": _tt(0, 0, "2026-01-03" if arrival else None, 100, 0)}
            if arrival:
                b["qt.csv"] = _Q_HDR + _qt("2026-01-03", "2026-01-03", "DEP",
                                           100, 99900002)
            _t1135_project(root, accounts, {
                "a": {"qt.csv": _Q_HDR + _qt("2025-12-30", "2026-01-02", "WDR",
                                             -100, 99900001),
                      "c.tt": _tt(100, 125, "2025-12-30", -100, 0)},
                "b": b}, tax_date="trade")
            return _t1135(root)

    @rule("CA-RPT-20")
    def test_counted_in_the_senders_row(self):
        rep = self._rep({"a": "USA", "b": "USA"})
        self.assertEqual(_year_end_cost(rep), 125.0)
        self.assertEqual(rep["max_total_cost"], 125.0)
        self.assertTrue(rep["total_settled"])

    @rule("CA-RPT-20")
    def test_a_canadian_side_is_named_and_not_settled(self):
        rep = self._rep({"a": "USA", "b": None})
        self.assertFalse(rep["total_settled"])
        self.assertEqual(
            [(n["account"], n["currency"], n["unknown"])
             for n in rep["cash_not_counted"]], [("a", "USD", True)])

    @rule("CA-RPT-20")
    def test_no_arrival_in_the_books_is_named_and_not_settled(self):
        rep = self._rep({"a": "USA", "b": "USA"}, arrival=False)
        self.assertFalse(rep["total_settled"])
        n, = rep["cash_not_counted"]
        self.assertIn("from a to ?", n["why"])

    def test_neither_side_counted_is_no_foreign_property(self):
        rep = self._rep({"a": None, "b": None})
        self.assertEqual(rep["cash_not_counted"], [])
        self.assertTrue(rep["total_settled"])


# ============================================== TJ-1186, TJ-1187 one entry
# per departure and arrival
def _two_senders(accounts=None):
    return [tt("CASHOPEN 2025-01-01 USD 100 125", A, 1),
            tt("CASHMOVE 2025-12-20 USD -100 own", A, 2),
            tt("CASHBAL 2025-12-31 USD 0", A, 3),
            tt("CASHOPEN 2025-01-01 USD 100 125", C, 1),
            tt("CASHMOVE 2025-12-30 USD -100 own", C, 2),
            tt("CASHBAL 2025-12-31 USD 0", C, 3),
            tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
            tt("CASHMOVE 2026-01-02 USD 200 own", B, 2),
            tt("CASHBAL 2025-12-31 USD 0", B, 3)]


class TestTJ1186EachSendersUnitsFromItsOwnDeparture(unittest.TestCase):
    """TJ-1186: two senders' transit merged into one entry at the earlier
    departure: the later sender's units were in its own row and in the
    transit for the days between, overstating the maximum."""

    @rule("CA-RPT-20")
    def test_two_senders(self):
        led = build(_two_senders(), rate=1.5, holdings=True)
        self.assertEqual(led["status"], "computed", led["problems"])
        doc = TC.cash_doc(led, {"a": "USA", "b": "USA", "c": "USA"},
                          2025, "CAD")
        rows = {s["account"]: s["points"] for s in doc["series"]}
        for d, want in (("2025-12-19", 250.0), ("2025-12-20", 250.0),
                        ("2025-12-29", 250.0), ("2025-12-30", 250.0),
                        ("2025-12-31", 250.0)):
            self.assertEqual(sum(_at(p, d)[1] for p in rows.values()), want, d)
        self.assertEqual(_at(rows["a"], "2025-12-31"), (100.0, 125.0))
        self.assertEqual(_at(rows["c"], "2025-12-31"), (100.0, 125.0))

    @rule("CA-RPT-20")
    def test_one_sender_two_departures(self):
        lines = [tt("CASHOPEN 2025-01-01 USD 200 250", A, 1),
                 tt("CASHMOVE 2025-12-20 USD -100 own", A, 2),
                 tt("CASHMOVE 2025-12-30 USD -100 own", A, 3),
                 tt("CASHBAL 2025-12-31 USD 0", A, 4),
                 tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                 tt("CASHMOVE 2026-01-02 USD 200 own", B, 2),
                 tt("CASHBAL 2025-12-31 USD 0", B, 3)]
        doc = TC.cash_doc(build(lines, rate=1.5, holdings=True),
                          {"a": "USA", "b": "USA"}, 2025, "CAD")
        a_row, = [s["points"] for s in doc["series"] if s["account"] == "a"]
        self.assertEqual(_at(a_row, "2025-12-25"), (200.0, 250.0))
        self.assertEqual(max(p[2] for p in a_row), 250.0)
        self.assertEqual(_at(a_row, "2025-12-31"), (200.0, 250.0))

    @rule("CA-RPT-20")
    def test_one_sender_in_canada_is_named_alone(self):
        doc = TC.cash_doc(build(_two_senders(), rate=1.5, holdings=True),
                          {"a": "USA", "b": "USA"}, 2025, "CAD")
        rows = {s["account"]: s["points"] for s in doc["series"]}
        self.assertEqual(_at(rows["a"], "2025-12-31"), (100.0, 125.0))
        n, = doc["not_counted"]
        self.assertTrue(n["unknown"])
        self.assertIn("100.00 USD", n["why"])
        self.assertIn("from c to b", n["why"])

    def test_a_named_sender_of_two_departures_is_one_entry(self):
        lines = [tt("CASHOPEN 2025-01-01 USD 200 250", A, 1),
                 tt("CASHMOVE 2025-12-20 USD -100 own", A, 2),
                 tt("CASHMOVE 2025-12-30 USD -100 own", A, 3),
                 tt("CASHBAL 2025-12-31 USD 0", A, 4),
                 tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                 tt("CASHMOVE 2026-01-02 USD 200 own", B, 2),
                 tt("CASHBAL 2025-12-31 USD 0", B, 3)]
        doc = TC.cash_doc(build(lines, rate=1.5, holdings=True),
                          {"a": "USA"}, 2025, "CAD")
        n, = doc["not_counted"]
        self.assertIn("200.00 USD", n["why"])
        self.assertIn("250.00 CAD", n["why"])


class TestTJ1187AnUnrelatedMoveNextYear(unittest.TestCase):
    """TJ-1187: every own arrival of the currency after the year was an
    end of the year-end transit, so an unrelated move next year changed
    its classification."""

    @staticmethod
    def _lines(transit=(A, B), feb=(C, D)):
        """100 USD in transit from one pair at the year end; 50 USD from
        the other in February."""
        t0, t1 = transit
        f0, f1 = feb
        return [tt("CASHOPEN 2025-01-01 USD 100 125", t0, 1),
                tt("CASHMOVE 2025-12-31 USD -100 own", t0, 2),
                tt("CASHBAL 2025-12-31 USD 0", t0, 3),
                tt("CASHOPEN 2025-01-01 USD 0 0", t1, 1),
                tt("CASHMOVE 2026-01-02 USD 100 own", t1, 2),
                tt("CASHBAL 2025-12-31 USD 0", t1, 3),
                tt("CASHOPEN 2025-01-01 USD 50 60", f0, 1),
                tt("CASHMOVE 2026-02-10 USD -50 own", f0, 2),
                tt("CASHBAL 2025-12-31 USD 50", f0, 3),
                tt("CASHOPEN 2025-01-01 USD 0 0", f1, 1),
                tt("CASHMOVE 2026-02-11 USD 50 own", f1, 2),
                tt("CASHBAL 2025-12-31 USD 0", f1, 3)]

    @rule("CA-RPT-20")
    def test_a_later_move_between_canadian_custodians(self):
        doc = TC.cash_doc(build(self._lines(), rate=1.5, holdings=True),
                          {"a": "USA", "b": "USA"}, 2025, "CAD")
        self.assertEqual(doc["not_counted"], [])
        a_row, = [s["points"] for s in doc["series"] if s["account"] == "a"]
        self.assertEqual(_at(a_row, "2025-12-31"), (100.0, 123.33))

    @rule("CA-RPT-20")
    def test_a_transit_between_canadian_custodians(self):
        doc = TC.cash_doc(
            build(self._lines(transit=(C, D), feb=(A, B)), rate=1.5,
                  holdings=True),
            {"a": "USA", "b": "USA"}, 2025, "CAD")
        self.assertEqual(doc["not_counted"], [])

    def test_the_transit_says_its_own_arrival(self):
        led = build(self._lines(), rate=1.5, holdings=True)
        tr, = led["holdings"]["transit"]
        self.assertEqual((tr["from"], tr["to"], tr["since"], tr["arrives"]),
                         ([A], [B], "2025-12-31", "2026-01-02"))

    def test_a_rounding_stays_with_its_departure(self):
        lines = [tt("CASHOPEN 2025-01-01 USD 100 125", A, 1),
                 tt("CASHMOVE 2025-12-31 USD -100 own", A, 2),
                 tt("CASHBAL 2025-12-31 USD 0", A, 3),
                 tt("CASHOPEN 2025-01-01 USD 0 0", B, 1),
                 tt("CASHMOVE 2026-01-02 USD 99.5 own", B, 2),
                 tt("CASHBAL 2025-12-31 USD 0", B, 3)]
        tr, = build(lines, rate=1.5, holdings=True)["holdings"]["transit"]
        self.assertEqual((tr["units"], tr["cost"], tr["to"]),
                         (100.0, 125.0, [B]))

    def test_two_arrivals_of_one_departure(self):
        lines = [tt("CASHOPEN 2025-01-01 USD 100 125", A, 1),
                 tt("CASHMOVE 2025-12-31 USD -100 own", A, 2),
                 tt("CASHBAL 2025-12-31 USD 0", A, 3),
                 tt("CASHMOVE 2026-01-02 USD 60 own", B, 1),
                 tt("CASHBAL 2025-12-31 USD 0", B, 2),
                 tt("CASHMOVE 2026-01-03 USD 40 own", C, 1),
                 tt("CASHBAL 2025-12-31 USD 0", C, 2)]
        led = build(lines, rate=1.5, holdings=True,
                    carry={"year": 2024, "currency": "CAD", "books": {
                        B: {"USD": {"units": 0.0, "cost": 0.0}},
                        C: {"USD": {"units": 0.0, "cost": 0.0}}}})
        self.assertEqual([(t["units"], t["to"]) for t in
                          led["holdings"]["transit"]],
                         [(60.0, [B]), (40.0, [C])])
        doc = TC.cash_doc(led, {"a": "USA", "b": "USA"}, 2025, "CAD")
        a_row, = [s["points"] for s in doc["series"] if s["account"] == "a"]
        self.assertEqual(_at(a_row, "2025-12-31"), (60.0, 75.0))
        n, = doc["not_counted"]
        self.assertIn("40.00 USD", n["why"])


# ============================================== TJ-1174 a .tt cash line
# with no book
class TestTJ1174ALineWithNoBook(unittest.TestCase):
    """TJ-1174: TJ-1098's exemption dropped the only signal of a .tt line
    the ledger could not place: T1135 said no filing required while the
    ledger refused the account's foreign cash."""

    def _ledger(self, label="us"):
        ev = CE.parse_line("CASHOPEN 2025-01-01 USD 200 260",
                           f"inputs/{label}/cash.tt:1")
        ev.update(label=label, file="cash.tt", book=None,
                  book_problem="its account folder holds several books")
        return build([ev], holdings=True)

    @rule("CA-RPT-20")
    def test_the_account_is_unknown(self):
        led = self._ledger()
        self.assertEqual(led["status"], "not_computed")
        doc = TC.cash_doc(led, {"us": "USA"}, 2025, "CAD")
        self.assertIn(("us", "USD", True),
                      [(n["account"], n["currency"], n["unknown"])
                       for n in doc["not_counted"]])

    @rule("CA-RPT-20")
    def test_another_account_stays_settled(self):
        # TJ-1098's point holds: the line is in `us`'s file, not `x`'s.
        doc = TC.cash_doc(self._ledger(), {"us": "USA", "x": "USA"}, 2025,
                          "CAD")
        self.assertEqual({n["account"] for n in doc["not_counted"]
                          if n["unknown"]}, {"us"})
        doc = TC.cash_doc(self._ledger("dom"), {"x": "USA"}, 2025, "CAD")
        self.assertEqual([n for n in doc["not_counted"] if n["unknown"]], [])

    def test_the_command(self):
        # The item's repro: two brokers' exports and a .tt line of neither.
        qt = _Q_HDR + ("2025-03-03,2025-03-05,Buy,SYN.TO,SYN,1,10,-10,0,-10,"
                       "CAD,Trades,99900090,Margin\n")
        rbc = ("Date,Activity,Symbol,Symbol Description,Quantity,Price,"
               "Settlement Date,Account,Value,Currency,Description\n"
               "2025-03-03,Buy,ZZQ.TO,ZZQ,1,10,2025-03-05,99900099,-10,CAD,"
               "ZZQ\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            _t1135_project(root, {"us": "USA"}, {"us": {
                "qt.csv": qt, "rbc.csv": rbc,
                "cash.tt": "CASHOPEN 2025-01-01 USD 200 260\n"
                           "CASHBAL 2025-12-31 USD 200\n"}})
            rep = _t1135(root)
        self.assertFalse(rep["total_settled"], rep["cash_not_counted"])


# ============================================== TJ-1228 a one-end-counted
# own move inside the year
class TestTJ1228TransitIsNotAMissingBalance(unittest.TestCase):
    """TJ-1228: the checklist and the brief read a span in transit as a
    balance the books do not give, and asked for a cost by hand."""

    @staticmethod
    def _rep(root):
        _t1135_project(root, {"us": "USA", "dom": None}, {
            "us": {"cash.tt": "CASHOPEN 2025-01-01 USD 100 125\n"
                              "CASHMOVE 2025-06-01 USD -100 own\n"
                              "CASHBAL 2025-12-31 USD 0\n"},
            "dom": {"cash.tt": "CASHOPEN 2025-01-01 USD 0 0\n"
                               "CASHMOVE 2025-06-05 USD 100 own\n"
                               "CASHBAL 2025-12-31 USD 100\n"}})
        return _t1135(root)

    def test_the_step_stays_done_and_the_brief_says_transit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            rep = self._rep(root)
            self.assertTrue(rep["total_settled"])
            n, = rep["cash_not_counted"]
            self.assertTrue(n["transit"])
            brief = tj(root, "t1135").stdout
            ctx = type("Ctx", (), {
                "settings": {"country": "canada"}, "year": 2025,
                "today": date(2026, 10, 30),
                "sub": lambda self, *a: (0, json.dumps(rep), "")})()
            res = cl.d_t1135(ctx)
        self.assertEqual(res.status, "done", res)
        self.assertNotIn("add it by hand", brief)
        self.assertIn("in transit during the year", brief)

    def test_a_missing_base_balance_still_asks(self):
        rep = {"total_settled": True, "cash_not_counted": [
            {"account": "us", "currency": "CAD", "unknown": False,
             "why": "no balance"}], "year_complete": True}
        ctx = type("Ctx", (), {
            "settings": {"country": "canada"}, "year": 2025,
            "today": date(2026, 10, 30),
            "sub": lambda self, *a: (0, json.dumps(rep), "")})()
        res = cl.d_t1135(ctx)
        self.assertEqual(res.status, "manual")
        self.assertIn("books give no balance", res.detail)


# ============================================== TJ-1158 a sale of another
# account's own units
def _generic(rows):
    return "Date,Action,Symbol,Quantity,Price,Amount\n" + "".join(
        f"{d},{a},{sym},{q},10,{-q * 10}\n" for d, a, sym, q in rows)


class TestTJ1158AnotherAccountsOwnSale(unittest.TestCase):
    """TJ-1158: the sale of the other account's own units closed the
    position the short account still held."""

    def _gaps(self, alpha, beta, tt_text=""):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, {"margin": "taxable"},
                            extra="combined_broker_accounts = true\n")
            for broker, acct, rows in (("alpha", "99900001", alpha),
                                       ("beta", "99900002", beta)):
                f = root / "inputs/margin" / f"generic_{broker}.csv"
                f.write_text(_generic(rows))
                f.with_name(f.name + ".toml").write_text(
                    _GENERIC_MAP.format(broker=broker, account=acct))
            if tt_text:
                (root / "inputs/margin/trades.tt").write_text(tt_text)
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            return [(g.broker, g.positions) for g in gaps if not g.info]

    BUY = ("2025-03-03", "BUY", "SYN.TO", 10)
    TAIL = ("2025-12-15", "BUY", "ZZZ.TO", 5)

    def test_the_short_broker_keeps_its_gap(self):
        got = self._gaps([self.BUY], [
            self.BUY, ("2025-05-01", "SELL", "SYN.TO", -10), self.TAIL])
        self.assertEqual(got, [("generic-alpha", [("SYN.TO", 10.0)])])

    def test_a_sale_beyond_its_own_units_closes_the_rest(self):
        got = self._gaps([self.BUY], [
            ("2025-03-03", "BUY", "SYN.TO", 4),
            ("2025-05-01", "SELL", "SYN.TO", -10), self.TAIL])
        self.assertEqual(got, [("generic-alpha", [("SYN.TO", 4.0)])])

    def test_a_broker_that_never_held_it_closes_it(self):
        # Control (a move to another broker booked as its sale).
        got = self._gaps([self.BUY], [
            ("2025-05-01", "SELL", "SYN.TO", -10), self.TAIL])
        self.assertEqual(got, [])

    def test_a_buy_and_sale_inside_the_gap_leave_the_units(self):
        got = self._gaps([self.BUY], [
            ("2025-06-01", "BUY", "SYN.TO", 7),
            ("2025-07-01", "SELL", "SYN.TO", -7), self.TAIL])
        self.assertEqual(got, [("generic-alpha", [("SYN.TO", 10.0)])])

    def test_a_tt_sale_still_closes_it(self):
        got = self._gaps([self.BUY], [self.BUY, self.TAIL],
                         "BUYSELL 2025-06-02 09:30:00 SYN.TO -10 CAD 15 150 0\n")
        # beta's 10 are open past the end of its own rows; alpha's closed.
        self.assertEqual(got, [])

    def test_two_ib_accounts_of_one_combined_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2025, {"a": "taxable"},
                            extra="combined_broker_accounts = true\n")
            (root / "inputs/a/a.csv").write_text(
                _ib("U1234567", "March 3, 2025", "SYN"))
            (root / "inputs/a/b.csv").write_text(
                _ib("U9990002", "December 31, 2025", "SYN")
                + _trade("SYN", "2025-05-01, 10:00:00", -10, 10, 100,
                         acct="U9990002", cur="CAD"))
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            step = _checklist(root)
        self.assertEqual([(g.broker, g.end, g.positions) for g in gaps],
                         [("ib", "2025-03-03", [("SYN.TO", 10.0)])])
        self.assertEqual(step.status, "attention", step)

    def test_a_split_scales_the_other_brokers_lot(self):
        rows = [{"action": "BUYSELL", "date": "2025-03-03", "symbol": "SYN",
                 "quantity": 10},
                {"action": "SPLIT", "date": "2025-04-01", "symbol": "SYN",
                 "quantity": 2},
                {"action": "BUYSELL", "date": "2025-05-01", "symbol": "SYN",
                 "quantity": -20}]
        spent = {}
        EC._spend_own_units(rows, spent, True)
        self.assertEqual(spent[id(rows[0])], 10.0)
        self.assertEqual(spent[id(rows[2])], -20.0)
        self.assertEqual(EC.closed_later([("SYN", 10.0)], [rows[2]],
                                         spent=spent),
                         ([("SYN", 10.0)], [], []))

    def test_a_written_option_is_opened_and_closed_too(self):
        rows = [{"action": "BUYSELL", "date": "2025-03-03",
                 "symbol": "SYN260116C00010000", "quantity": -1},
                {"action": "BUYSELL", "date": "2025-05-01",
                 "symbol": "SYN260116C00010000", "quantity": 1}]
        spent = {}
        EC._spend_own_units(rows, spent, False)
        self.assertEqual(spent, {id(rows[0]): -1.0, id(rows[1]): 1.0})


# ============================================== TJ-1159 Kraken's trades
# export: an unpriced swap keeps its fee
class TestTJ1159KrakenTradesSwapFee(unittest.TestCase):
    """TJ-1159: the crypto-to-crypto branch of the trades parser folded
    the fee into the leg quantities and said nothing of it, so the filler
    valued the swap from the received units alone."""

    CACHE1 = {"SYN-2025-06-02": 30, "ZZQ-2025-06-02": 100}
    CACHE2 = {"SYN-2025-06-02": 10, "ZZQ-2025-06-02": 5}
    T_HDR = "txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,misc,ledgers\n"
    L_HDR = "txid,refid,time,type,subtype,aclass,asset,amount,fee,balance"

    def _legs(self, td, name, trade, ledger, cache):
        d = Path(td) / name
        d.mkdir()
        (d / "trades.csv").write_text(self.T_HDR + trade)
        if ledger:
            (d / "ledgers.csv").write_text(ledger)
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_brokerage",
                               ["--brokerage", "kraken", "--country", "canada",
                                str(d / "trades.csv")]),
            capture_output=True, text=True,
            env=dict(os.environ, TAXJSON_WIDTH="0",
                     TAXJSON_LOCAL_TZ="America/Toronto"))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = d / "parsed.json"
        out.write_text(r.stdout)
        return {x["symbol"]: x for x in _fill(out, cache)}

    def _ledger(self, rows, usd=False):
        hdr = self.L_HDR + (",amountusd" if usd else "") + "\n"
        return hdr + "".join(
            f"{i},T1,2025-06-02 14:00:00,trade,,currency,{a},{q},{f},0"
            + (f",{u}" if usd else "") + "\n" for i, a, q, f, u in rows)

    def _both(self, legs, want):
        for sym, leg in legs.items():
            self.assertAlmostEqual(leg["net_amount"], want, msg=sym)

    def test_a_buy_with_no_ledger_counts_its_quote_fee(self):
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", "T1,O1,SYN/ZZQ,2025-06-02 14:00:00.0000,"
                              "buy,market,0.3,3,0.03,10,0,,\n", "", self.CACHE1)
        self.assertAlmostEqual(legs["ZZQ"]["quantity"], -3.03)
        self._both(legs, 303.0)

    def test_a_sell_with_no_ledger_is_net_of_its_fee(self):
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", "T1,O1,SYN/ZZQ,2025-06-02 14:00:00.0000,"
                              "sell,market,0.3,3,0.03,10,0,,\n", "", self.CACHE1)
        self.assertAlmostEqual(legs["ZZQ"]["quantity"], 2.97)
        self._both(legs, 297.0)

    def test_no_fee_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", "T1,O1,SYN/ZZQ,2025-06-02 14:00:00.0000,"
                              "buy,market,0.3,3,0,10,0,,\n", "", self.CACHE1)
        self._both(legs, 300.0)
        self.assertNotIn("swap_fee", legs["SYN"])

    BUY2 = "T1,O1,ZZQ/SYN,2025-06-02 14:00:00.0000,buy,market,0.5,10,0,20,0,,L4 L5\n"

    def test_a_joined_fee_in_the_received_coin(self):
        rows = [("L4", "SYN", -10, 0, 100), ("L5", "ZZQ", 20, 0.2, 100)]
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", self.BUY2, self._ledger(rows),
                              self.CACHE2)
            valued = self._legs(td, "b", self.BUY2,
                                self._ledger(rows, usd=True), self.CACHE2)
        self.assertAlmostEqual(legs["ZZQ"]["quantity"], 19.8)
        self._both(legs, 100.0)
        self._both(valued, 100.0)

    def test_a_joined_fee_in_the_spent_coin_is_as_with_amountusd(self):
        # A sell's fee in the coin it spends (the base coin).
        sell = ("T1,O1,ZZQ/SYN,2025-06-02 14:00:00.0000,sell,market,0.5,10,0,"
                "20,0,,L4 L5\n")
        rows = [("L4", "ZZQ", -20, 0.2, 100), ("L5", "SYN", 10, 0, 100)]
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", sell, self._ledger(rows), self.CACHE2)
            valued = self._legs(td, "b", sell, self._ledger(rows, usd=True),
                                self.CACHE2)
        self.assertAlmostEqual(legs["ZZQ"]["quantity"], -20.2)
        self._both(legs, 100.0)
        self._both(valued, 100.0)

    def test_a_joined_sell_with_its_fee_in_the_received_coin(self):
        sell = ("T1,O1,ZZQ/SYN,2025-06-02 14:00:00.0000,sell,market,0.5,10,"
                "0.1,20,0,,L4 L5\n")
        rows = [("L4", "ZZQ", -20, 0, 100), ("L5", "SYN", 10, 0.1, 100)]
        with tempfile.TemporaryDirectory() as td:
            legs = self._legs(td, "a", sell, self._ledger(rows), self.CACHE2)
            valued = self._legs(td, "b", sell, self._ledger(rows, usd=True),
                                self.CACHE2)
        self.assertAlmostEqual(legs["SYN"]["quantity"], 9.9)
        self._both(legs, 100.0)
        self._both(valued, 100.0)


# ============================================== TJ-1188 a stated Subtotal 0
class TestTJ1188BlankTotalWithSubtotalZero(unittest.TestCase):
    """TJ-1188: a stated Subtotal of 0 was read as a blank cell: the row
    was booked at quantity x price, while the same row with its Total
    filled in is refused."""

    def _parse(self, td, name, price, subtotal, total):
        row = (f"A1,2025-06-02 14:00:00 UTC,Buy,SYN,10,CAD,{price},{subtotal},"
               f"{total},0,Bought\n")
        return _brokerage(td, name, "coinbase", _CB_HDR + row)

    def test_refused_either_way(self):
        with tempfile.TemporaryDirectory() as td:
            for name, total in (("blank", ""), ("given", "100")):
                rc, _path, err = self._parse(td, name, 10, 0, total)
                self.assertNotEqual(rc, 0, name)
                self.assertIn("Subtotal 0.00 does not fit", err, name)

    def test_a_blank_subtotal_still_derives(self):
        with tempfile.TemporaryDirectory() as td:
            rc, path, err = self._parse(td, "blank", 10, "", "")
            self.assertEqual(rc, 0, err)
            row = json.loads(path.read_text())["transactions"][0]
            self.assertAlmostEqual(row["net_amount"], 100.0)

    def test_nothing_to_derive_from_is_the_old_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            rc, _path, err = self._parse(td, "zero", "", 0, "")
            self.assertNotEqual(rc, 0)
            self.assertIn("the Total cell is blank", err)


# ============================================== TJ-1227 the repeated hour
# across two exports
class TestTJ1227RepeatedHourAcrossExports(unittest.TestCase):
    """TJ-1227: the sale and the rebuy compress into one local second in
    two files of one account: file order replayed the rebuy first."""

    C1 = "C1,2025-01-06 15:00:00 UTC,Buy,ZZE,10,CAD,5,50,50,0,Bought 10 ZZE\n"
    C2 = "C2,2025-11-02 05:00:00 UTC,Sell,ZZE,10,CAD,10,100,100,0,Sold 10 ZZE\n"
    C3 = ("C3,2025-11-02 05:00:01 UTC,Buy,ZZE,10,CAD,10.01,100.1,100.1,0,"
          "Bought 10 ZZE\n")

    def _run(self, files, country="canada"):
        with tempfile.TemporaryDirectory() as td:
            root = _crypto_project(td, country)
            for name, body in files.items():
                (root / "inputs/coin" / name).write_text(_CB_HDR + body)
            tj(root, "run", "--no-input")
            order = [r["id"] for r in json.loads(
                (root / "work/coin_sorted.json").read_text())["transactions"]]
            doc = json.loads((root / "work/coin_gains.json").read_text()) \
                if (root / "work/coin_gains.json").exists() else {}
        return order, doc

    def test_the_sale_goes_first(self):
        order, doc = self._run({"coinbase_a.csv": self.C3,
                                "coinbase_b.csv": self.C1 + self.C2})
        self.assertEqual(order.index("C2") < order.index("C3"), True, order)
        self.assertAlmostEqual(doc["summary"]["total_gain"], 50.0, places=2)

    def test_the_other_file_order_too(self):
        order, _d = self._run({"coinbase_a.csv": self.C1 + self.C2,
                               "coinbase_b.csv": self.C3})
        self.assertEqual(order.index("C2") < order.index("C3"), True, order)

    def test_a_us_project_too(self):
        order, _d = self._run({"coinbase_a.csv": self.C3,
                               "coinbase_b.csv": self.C1 + self.C2}, "usa")
        self.assertEqual(order.index("C2") < order.index("C3"), True, order)

    def test_the_order_function(self):
        from types import SimpleNamespace as NS
        from taxjson.lib.brokerages._crypto_common import order_across_exports
        rows = [NS(date="d", time="t", source="a"),
                NS(date="d", time="t", source="b"),
                NS(date="d", time="u", source="b")]
        self.assertEqual(order_across_exports(rows, [2, 1, 9]), [1, 0, 2])
        # An unknown instant, or one export: as given.
        self.assertEqual(order_across_exports(rows, [2, None, 9]), [0, 1, 2])
        same = [NS(date="d", time="t", source="a"),
                NS(date="d", time="t", source="a")]
        self.assertEqual(order_across_exports(same, [2, 1]), [0, 1])
        # One instant: the file order.
        self.assertEqual(order_across_exports(rows, [1, 1, 9]), [0, 1, 2])


# ============================================== TJ-1229 the migration on
# read-only inputs
class TestTJ1229MigrationOnReadOnlyInputs(unittest.TestCase):
    """TJ-1229: the read paths saved the moved ids, and a read-only file
    stopped a listing or a run with 'the decision was not saved'."""

    A = ("AAA-SEND-901,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,10,10,10,0,"
         "Sent 1 ZZE\n")
    B = ("BBB-SEND-902,2025-03-03 17:00:00 UTC,Send,ZZE,1,CAD,11,11,11,0,"
         "Sent 1 ZZE\n")
    BUY = "BUY-0001,2025-03-01 17:00:00 UTC,Buy,ZZE,2,CAD,5,10,10,0,Bought 2 ZZE\n"
    S1, S2 = "cb-20250303T120000-ZZE-1", "cb-20250303T120000-ZZE-1-2"

    def _project(self, td):
        root = _crypto_project(td)
        (root / "inputs/coin/coinbase.csv").write_text(
            _CB_HDR + self.BUY + self.B + self.A)
        tj(root, "run", "--no-input", check=False)
        # Saved on the old numbering: B, the first row, was ...-1.
        (root / "inputs/coin/sends.json").write_text(json.dumps({
            "schema_version": 1, "sends": {
                self.S1: {"decision": "gift", "price": 11.0,
                          "price_currency": "CAD"},
                self.S2: {"decision": "self"}}}))
        return root

    def _read_only(self, root):
        for p in (root / "inputs").rglob("*"):
            p.chmod(p.stat().st_mode & ~0o222)
        (root / "inputs").chmod(0o555)

    def _writable(self, root):
        (root / "inputs").chmod(0o755)
        for p in (root / "inputs").rglob("*"):
            p.chmod(p.stat().st_mode | 0o200)

    def test_the_listing_works_with_the_moved_ids(self):
        if os.geteuid() == 0:
            self.skipTest("root writes read-only files")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            self._read_only(root)
            try:
                before = (root / "inputs/coin/sends.json").read_text()
                r = tj(root, "crypto-sends", "coin", check=False)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("the decision was not saved", r.stderr)
                self.assertIn("hold for this run only", r.stderr)
                doc = json.loads(tj(root, "crypto-sends", "--json",
                                    check=False).stdout)
                sends = {s["id"]: s for s in doc["accounts"]["coin"]["sends"]}
                # B, the 11 send, keeps the gift under its new id (..-1-2).
                self.assertEqual(sends[self.S2]["fair_value"]["price"], 11.0)
                self.assertEqual(sends[self.S2]["decision"], "gift")
                self.assertEqual(sends[self.S1]["decision"], "self")
                self.assertEqual(
                    (root / "inputs/coin/sends.json").read_text(), before)
            finally:
                self._writable(root)
            # Writable again: the move is saved, once.
            r = tj(root, "crypto-sends", "coin", check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads((root / "inputs/coin/sends.json").read_text())
            self.assertEqual(doc["schema_version"], 2)
            self.assertEqual(doc["sends"][self.S2]["decision"], "gift")

    def test_a_decision_on_a_read_only_file_still_says_so(self):
        if os.geteuid() == 0:
            self.skipTest("root writes read-only files")
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            self._read_only(root)
            try:
                r = tj(root, "crypto-sends", "coin", "--set",
                       f"{self.S1}=self", check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("the decision was not saved", r.stderr)
            finally:
                self._writable(root)

    def test_load_decisions_gives_the_moved_ids_until_the_file_changes(self):
        from taxjson.lib import crypto_sends as CS
        with tempfile.TemporaryDirectory() as td:
            man = Path(td) / "sends.json"
            old = {"a": {"decision": "gift"}}
            man.write_text(json.dumps({"schema_version": 1, "sends": old}))
            CS._UNSAVED_IDS[str(man)] = (old, {
                "schema_version": 2, "sends": {"b": {"decision": "gift"}}})
            self.addCleanup(CS._UNSAVED_IDS.clear)
            self.assertEqual(sorted(CS.load_decisions(man)["sends"]), ["b"])
            man.write_text(json.dumps({"schema_version": 2,
                                       "sends": {"c": {"decision": "self"}}}))
            self.assertEqual(sorted(CS.load_decisions(man)["sends"]), ["c"])
            self.assertNotIn(str(man), CS._UNSAVED_IDS)


if __name__ == "__main__":
    unittest.main()
