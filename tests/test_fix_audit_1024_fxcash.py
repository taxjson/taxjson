"""Fixes for the 2026-10-24 external audit, group fxcash: the FX-on-cash
ledger v2 (lib/fx_cash_v2, bin/taxjson_run._fx_cash_carry, the Questrade
cash reader), the rates window, carry-forwards and the filed-year lock's
shape check. One test per finding, each named for it. Synthetic data
only."""
import json
import math
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as FX
from taxjson.lib.dispatch import python_module_argv

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent

# A Questrade activity export of one cash-only row (a deposit).
_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Quantity,"
            "Net Amount,Currency,Account #,Activity Type,Description,Price,"
            "Gross Amount,Commission,Account Type\n")
_QT_DEP = ("2026-01-02,2026-01-02,DEP,,0,{net},USD,{acct},Deposits,"
           "synthetic,0,0,0,Margin\n")


def _build(cash=None, carry=None, base="CAD", country="canada"):
    return FX.build([], cash or {"events": [], "lines": []}, base, 2026,
                    lambda c, d: 1.0, country=country, carry=carry)


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *args])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, env=e, stdin=subprocess.DEVNULL,
                          timeout=600)


def _project(root: Path, country="canada", rows=()):
    """A 2026 project opted into ledger v2 whose books are already built
    (an empty work/margin_sorted.json): `fx-cash --ledger v2 --json`
    then reads only the cash events and the prior-year record."""
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "filed").mkdir(exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2026\ncountry = "{country}"\n'
        f'fx_cash_ledger = "v2"\n[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_sorted.json").write_text(
        json.dumps({"transactions": list(rows)}))


def _fx_json(root):
    r = _cli(root, "fx-cash", "--ledger", "v2", "--json")
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


class TestOwnTransferOrder(unittest.TestCase):
    @rule("CA-FX-07")
    def test_a20_departure_walked_before_same_settlement_day_arrival(self):
        """A20: FX ledger sorts an own-transfer arrival before its
        same-settlement-day departure. The receiving broker books the
        request day (02-02), the sending one the day it moved (02-03);
        both settle 02-03. Ordered by trade date first, the arrival was
        walked first and refused ("only 0.00 USD had left"), then the
        departure ("never arrived"). The departure goes first."""
        cash = {"events": [], "lines": []}
        for book, units in (("margin/a", 100), ("margin/b", 0)):
            cash["lines"].append(dict(
                kind="CASHOPEN", book=book, date="2026-01-01",
                currency="USD", units=units, cost=units, origin="tt"))
            cash["events"].append(dict(
                CE.balance("2026-02-03", "USD", 0 if book == "margin/a"
                           else 100), book=book))
        for book, amount, td in (("margin/a", -100, "2026-02-03"),
                                 ("margin/b", 100, "2026-02-02")):
            cash["events"].append(dict(
                CE.move(td, "USD", amount, settle="2026-02-03"),
                book=book, decl={"how": "own"}))
        doc = _build(cash)
        self.assertEqual([p["text"] for p in doc["problems"]], [])
        self.assertEqual(doc["status"], "computed")
        # Not a disposition: the 100 USD and their cost just moved.
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 100.0, "acb": 100.0}})
        self.assertEqual(doc["close"]["books"],
                         {"margin/b": {"USD": {"units": 100.0,
                                               "cost": 100.0}}})
        self.assertEqual(doc["per_currency"]["USD"]["gain"], 0.0)


class TestPriorYearCarry(unittest.TestCase):
    @rule("CA-FX-07")
    def test_c02_unreadable_prior_year_record_is_a_blocking_problem(self):
        """C02: Unreadable prior-year cash record produces a reliable
        empty ledger. filed/2025.json exists but is not JSON: it read as
        no record, and `fx-cash --ledger v2` certified a computed, reliable
        zero ledger. An existing record that cannot be read is a problem
        the ledger lists; an absent one is still no carry."""
        from taxjson.bin.taxjson_run import _fx_cash_carry
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            # Absent: no carry, as before.
            self.assertIsNone(_fx_cash_carry(root, {}, 2026,
                                             country="canada"))
            (root / "filed" / "2025.json").write_text("{broken")
            carry = _fx_cash_carry(root, {}, 2026, country="canada")
            self.assertEqual(carry["books"], {})
            self.assertIn("filed/2025.json", carry["problem"])
            self.assertIn("cannot be read", carry["problem"])
            doc = _build(carry=carry)
            self.assertEqual(doc["status"], "not_computed")
            self.assertFalse(doc["reliable"])
            self.assertEqual([p["kind"] for p in doc["problems"]],
                             ["carry"])
            # The CLI: NOT COMPUTED, the record named.
            out = _fx_json(root)
            self.assertEqual(out["status"], "not_computed")
            self.assertFalse(out["reliable"])
            self.assertIn("filed/2025.json", out["problems"][0]["text"])
            self.assertIn("NOT COMPUTED", out["headline"])
            # A record of the wrong year's close, or a close that is not
            # a table of books: damaged, not absent.
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "canada",
                 "fx_cash_v2": {"year": 2024, "books": {}}}))
            self.assertIn("2024", _fx_cash_carry(root, {}, 2026,
                                                 country="canada")["problem"])
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "canada",
                 "fx_cash_v2": {"year": 2025, "books": [1]}}))
            self.assertIn("not a table of books", _fx_cash_carry(
                root, {}, 2026, country="canada")["problem"])
            # A record closed under the default ledger carries no close:
            # no carry, no problem.
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "canada"}))
            self.assertIsNone(_fx_cash_carry(root, {}, 2026,
                                             country="canada"))

    @rule("CA-FX-07")
    def test_c03_nonfinite_or_missing_carried_cost_refused(self):
        """C03: Nonfinite carried cash basis passes ledger validation. A
        prior-year pool of 100 USD at a NaN cost opened the year as
        computed and reliable (the JSON showed the cost as null). Units
        and cost must be finite numbers, both present."""
        for bad in ({"units": 100, "cost": float("nan")},
                    {"units": float("inf"), "cost": 140},
                    {"units": 100},
                    {"units": 100, "cost": None},
                    {"units": 100, "cost": "140"},
                    {"units": 100, "cost": True},
                    7):
            carry = {"year": 2025, "books": {"margin/ib": {"USD": bad}}}
            doc = _build(carry=carry)
            self.assertEqual(doc["status"], "not_computed", bad)
            self.assertEqual(doc["opening"], {}, bad)
            self.assertEqual(doc["pools_year_end"], {}, bad)
            self.assertEqual(doc["carried_in"], 0, bad)
            texts = [p["text"] for p in doc["problems"]
                     if p["kind"] == "carry"]
            self.assertEqual(len(texts), 1, doc["problems"])
            self.assertIn("margin/ib USD", texts[0])
            self.assertIn("finite numbers", texts[0])
            self.assertIn("filed/2025.json", texts[0])
        # A sound close still opens the year.
        doc = _build(carry={"year": 2025, "books": {
            "margin/ib": {"USD": {"units": 100, "cost": 140}}}})
        self.assertEqual(doc["status"], "computed")
        self.assertEqual(doc["carried_in"], 1)
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 100.0, "acb": 140.0}})

    def test_c04_record_of_the_other_country_or_base_currency_refused(self):
        """C04: Cash carryover ignores the prior record country and
        currency context. A USA project read a Canadian record's USD 100
        at CAD cost 140 as its own basis (and USD as foreign cash). The
        record's country is checked against the project's, and the close
        now names its base currency, checked against the project's."""
        from taxjson.bin.taxjson_run import _fx_cash_carry
        close = {"year": 2025, "books": {
            "margin/ib": {"USD": {"units": 100, "cost": 140}}}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root, country="usa")
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "canada", "fx_cash_v2": close}))
            carry = _fx_cash_carry(root, {}, 2026, country="usa")
            self.assertEqual(carry["books"], {})
            self.assertIn('country = "canada"', carry["problem"])
            self.assertIn('country = "usa"', carry["problem"])
            out = _fx_json(root)
            self.assertEqual(out["status"], "not_computed")
            self.assertEqual(out["pools_year_end"], {})
            self.assertIn("Canada law", out["problems"][0]["text"])
            # The same country: carried.
            self.assertEqual(_fx_cash_carry(root, {}, 2026,
                                            country="canada"), close)
            # A record without a country (hand-written) is not judged.
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "fx_cash_v2": close}))
            self.assertEqual(_fx_cash_carry(root, {}, 2026,
                                            country="usa"), close)
        # The close records its base currency; a carry in another one
        # cannot open the year.
        doc = _build(carry=dict(close, currency="CAD"), base="USD",
                     country="usa")
        self.assertEqual(doc["status"], "not_computed")
        self.assertEqual(doc["pools_year_end"], {})
        self.assertIn("costs are in CAD", doc["problems"][0]["text"])
        self.assertIn("base currency is USD", doc["problems"][0]["text"])
        doc = _build(carry=dict(close, currency="CAD"))
        self.assertEqual(doc["status"], "computed")
        self.assertEqual(doc["close"]["currency"], "CAD")

    @rule("CA-FX-07")
    def test_c05_renamed_broker_book_matched_or_refused(self):
        """C05: Broker book renaming allows an opening declaration to
        duplicate carried cash. Books.name drops the account suffix while
        a broker has one account in the folder and adds it when a second
        appears: last year's `margin/questrade` matched none of this
        year's `margin/questrade:aaaa` / `:bbbb`, and the CASHOPEN for the
        right one was added to the carried 100 USD (200 units, cost 280).
        Now a CASHOPEN for one of the broker's books replaces the carried
        balance; without one the ambiguous carry is refused with the line
        to add; a carry whose close recorded its account (written since)
        or whose name carries the account prefix is placed for certain."""
        books = CE.Books()
        books.add("margin", "questrade", "aaaa1111")
        self.assertEqual(books.name("margin", "questrade", "aaaa1111"),
                         "margin/questrade")
        books.add("margin", "questrade", "bbbb2222")
        self.assertEqual(books.name("margin", "questrade", "aaaa1111"),
                         "margin/questrade:aaaa")
        carry = {"year": 2025, "books": {
            "margin/questrade": {"USD": {"units": 100, "cost": 140}}}}
        ln = CE.parse_line("CASHOPEN 2026-01-01 USD 100 140", "open.tt:1")
        ln["book"] = "margin/questrade:bbbb"
        cash = {"events": [], "lines": [ln], "books": books}
        # (a) The CASHOPEN replaces the carried balance: 100, not 200.
        doc = _build(cash=cash, carry=carry)
        self.assertEqual(doc["status"], "computed")
        self.assertEqual(doc["pools_year_end"],
                         {"USD": {"units": 100.0, "acb": 140.0}})
        self.assertEqual(list(doc["opening"]),
                         ["margin/questrade:bbbb USD"])
        self.assertEqual(doc["carried_in"], 0)
        self.assertTrue(any("replaces the prior year's carried balance of "
                            "margin/questrade" in n for n in doc["notes"]),
                        doc["notes"])
        # (b) No CASHOPEN: refused, the books and the line named.
        doc = _build(cash={"events": [], "lines": [], "books": books},
                     carry=carry)
        self.assertEqual(doc["status"], "not_computed")
        self.assertEqual(doc["pools_year_end"], {})
        t = [p for p in doc["problems"] if p["kind"] == "carry"][0]
        self.assertEqual((t["book"], t["currency"], t["amount"]),
                         ("margin/questrade", "USD", 100.0))
        self.assertIn("margin/questrade:aaaa", t["text"])
        self.assertIn("margin/questrade:bbbb", t["text"])
        self.assertIn("CASHOPEN 2026-01-01 USD 100.00 140.00 at=questrade:",
                      t["text"])
        # (c) The close recorded the account: placed under its new name.
        doc = _build(cash={"events": [], "lines": [], "books": books},
                     carry=dict(carry, accounts={"margin/questrade":
                                                 "aaaa1111"}))
        self.assertEqual(doc["status"], "computed")
        self.assertEqual(doc["carried_in"], 1)
        self.assertEqual(list(doc["opening"]),
                         ["margin/questrade:aaaa USD"])
        self.assertTrue(any("margin/questrade is margin/questrade:aaaa "
                            "this year" in n for n in doc["notes"]),
                        doc["notes"])
        # (d) The reverse: the second account went, the carried
        # `margin/questrade:bbbb` is `margin/questrade` now.
        one = CE.Books()
        one.add("margin", "questrade", "bbbb2222")
        doc = _build(cash={"events": [], "lines": [], "books": one},
                     carry={"year": 2025, "books": {"margin/questrade:bbbb":
                                                   {"USD": {"units": 100,
                                                            "cost": 140}}}})
        self.assertEqual(doc["status"], "computed")
        self.assertEqual(list(doc["opening"]), ["margin/questrade USD"])
        # The close names each book's account for next year.
        self.assertEqual(doc["close"]["accounts"],
                         {"margin/questrade": "bbbb2222"})
        # (e) A broker with no file this year: carried as recorded.
        doc = _build(cash={"events": [], "lines": [], "books": CE.Books()},
                     carry=carry)
        self.assertEqual(doc["status"], "computed")
        self.assertEqual(list(doc["opening"]), ["margin/questrade USD"])

    @rule("CA-FX-07")
    def test_c05_cli_cashopen_for_the_renamed_book_replaces_the_carry(self):
        """C05 end to end: two Questrade exports in one account folder,
        last year's record carrying `margin/questrade`, a CASHOPEN
        `at=questrade:<prefix>` for the first account: 100 USD at 140,
        not 200 at 280."""
        from taxjson.bin.taxjson_brokerage import hash_broker_account
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = []
            for name, acct in (("a.csv", "99900001"), ("b.csv", "99900002")):
                (root / "inputs" / "margin").mkdir(parents=True,
                                                   exist_ok=True)
                (root / "inputs" / "margin" / name).write_text(
                    _QT_HEAD + _QT_DEP.format(net="0", acct=acct))
                rows.append(dict(
                    date="2026-01-02", date_settle="2026-01-02",
                    action="DIVIDEND", symbol="ZZQ.US", quantity=0,
                    net_amount=0, currency="USD", account="margin",
                    source=name, source_account=hash_broker_account(acct)))
            _project(root, rows=rows)
            pre = hash_broker_account("99900001")[:4]
            (root / "inputs" / "margin" / "open.tt").write_text(
                f"CASHOPEN 2026-01-01 USD 100 140 at=questrade:{pre}\n")
            (root / "filed" / "2025.json").write_text(json.dumps(
                {"year": 2025, "country": "canada", "fx_cash_v2": {
                    "year": 2025, "books": {"margin/questrade": {
                        "USD": {"units": 100, "cost": 140}}}}}))
            out = _fx_json(root)
            self.assertEqual(out["problems"], [])
            self.assertEqual(out["status"], "computed")
            self.assertEqual(out["pools_year_end"],
                             {"USD": {"units": 100.0, "acb": 140.0}})
            self.assertEqual(list(out["opening"]),
                             [f"margin/questrade:{pre} USD"])
            self.assertEqual(out["close"]["accounts"],
                             {f"margin/questrade:{pre}":
                              hash_broker_account("99900001")})
            self.assertEqual(out["close"]["currency"], "CAD")


class TestQuestradeCashReader(unittest.TestCase):
    @rule("CA-FX-07")
    def test_c01_malformed_cash_amount_refused_with_its_location(self):
        """C01: Questrade cash reader silently drops malformed cash
        amounts. A deposit row whose Net Amount is `broken` was skipped
        with a comment saying the position parser would name it; that
        parser counts a deposit as a non-event without reading the
        amount, so `fx-cash --ledger v2` computed a reliable ledger with
        the deposit gone. The reader refuses the row by file and line;
        collect lists it and the ledger computes nothing."""
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.brokerages.questrade import questrade_cash_events
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cash.csv"
            p.write_text(_QT_HEAD + _QT_DEP.format(net="broken",
                                                   acct="99900001"))
            with self.assertRaises(BrokerageParseError) as cm:
                questrade_cash_events(p)
            msg = str(cm.exception)
            self.assertIn("cash.csv line 2", msg)
            self.assertIn("Net Amount 'broken'", msg)
            self.assertIn("DEP cash row", msg)
            # A sound row reads as before.
            p.write_text(_QT_HEAD + _QT_DEP.format(net="100",
                                                   acct="99900001"))
            self.assertEqual([(e["kind"], e["currency"], e["amount"])
                              for e in questrade_cash_events(p)],
                             [("CASHMOVE", "USD", 100.0)])
            # A row the ledger does not read (a trade) with an unreadable
            # amount is the position parser's to refuse: skipped here.
            p.write_text(_QT_HEAD + "2026-01-02,2026-01-05,Buy,ZZQ,10,"
                         "broken,USD,99900001,Trades,ZZQ CORP,10,-100,0,"
                         "Margin\n")
            self.assertEqual(questrade_cash_events(p), [])
        # The CLI: NOT COMPUTED, the file and line named.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            (root / "inputs" / "margin" / "cash.csv").write_text(
                _QT_HEAD + _QT_DEP.format(net="broken", acct="99900001"))
            out = _fx_json(root)
            self.assertEqual(out["status"], "not_computed")
            self.assertFalse(out["reliable"])
            self.assertEqual(out["problems"][0]["kind"], "input")
            self.assertIn("margin/cash.csv", out["problems"][0]["text"])
            self.assertIn("line 2", out["problems"][0]["text"])


class TestRatesWindow(unittest.TestCase):
    def test_a38_compact_dates_read_from_date_columns_of_a_mixed_file(self):
        """A38: FX start-date scanner ignores compact trade dates when the
        export also has an ISO date. 8-digit dates were read only in a
        file with no date of another shape, so an IB Flex export's
        generation line hid its 2006 trade dates and the window started
        at January 1 of the project year (rows then had no rate). A
        compact date in a cell of a column whose header names a date is
        read in any file; an 8-digit run elsewhere stays an id."""
        from taxjson.lib.rates_window import earliest_in_text
        self.assertEqual(earliest_in_text(
            b"Generated: 2026-01-01\nTradeDate,Symbol\n20060103,ZZQ.US\n",
            2026), date(2006, 1, 3))
        # An IB Flex export: quoted cells, a DateTime with its time, a
        # WhenGenerated line in another shape.
        flex = (b'"ClientAccountID","Symbol","DateTime","TradeDate","Qty"\n'
                b'"U1234567","ZZQ","20190104;093000","20190104","10"\n'
                b'"U1234567","ZZR","20210305;101500","20210305","5"\n'
                b'Statement,Data,WhenGenerated,"2026-01-02, 10:00:00"\n')
        self.assertEqual(earliest_in_text(flex, 2026), date(2019, 1, 4))
        # Not in a date column: an id, whatever the file's other dates.
        self.assertEqual(earliest_in_text(
            b"Date,OrderID\n2024-01-02,20010101\n", 2026), date(2024, 1, 2))
        self.assertEqual(earliest_in_text(
            b"2024-01-02 and order 20010101", 2027), date(2024, 1, 2))
        # A description mentioning a date is not a header row.
        self.assertEqual(earliest_in_text(
            b"Date,Description,Ref\n2024-01-02,PAYMENT DATE ADJUSTED FOR "
            b"THE HOLIDAY SCHEDULE OF THE EXCHANGE,20010101\n", 2026),
            date(2024, 1, 2))
        # A file of compact dates and nothing else: read anywhere still.
        self.assertEqual(earliest_in_text(b"20190104;093000", 2027),
                         date(2019, 1, 4))


class TestCarryForwards(unittest.TestCase):
    @rule("CA-AMT-08")
    def test_a39_amt_carryover_year_spelled_twice_is_refused(self):
        """A39: AMT configuration silently overwrites two differently
        spelled copies of a year. `2023` and `02023` are two TOML keys of
        one year of origin; the second amount replaced the first in
        silence. Refused, both spellings named, as [carryover] claimed
        does."""
        from taxjson.lib import carryforward as CF
        with self.assertRaises(CF.CarryInputError) as cm:
            CF.amt_from_config({"2023": 100, "02023": 200})
        msg = str(cm.exception)
        self.assertIn("[estimate] amt_carryover", msg)
        self.assertIn("2023 is given twice", msg)
        self.assertIn("'2023'", msg)
        self.assertIn("'02023'", msg)
        with self.assertRaises(CF.CarryInputError):
            CF.amt_from_config({2023: 100, "2023": 100})
        self.assertEqual(CF.amt_from_config({"2023": 100, "2024": 50}),
                         {2023: 100.0, 2024: 50.0})

    @rule("CA-CARRY-02")
    def test_b39_truncated_loss_block_refused_not_read_as_zero(self):
        """B39: A truncated carryforward block silently resets the next
        year's loss balance. A `net_capital_loss` section with an
        opening and no closing passed the shape check, and next year's
        estimate read the absent closing as 0.00. A present section
        without its closing balance is refused, naming the lock."""
        from taxjson.lib import carryforward as CF
        self.assertIn("net_capital_loss has no closing", CF.block_problem(
            {"net_capital_loss": {"opening": 100}}))
        self.assertIn("capital_loss has no st_closing", CF.block_problem(
            {"capital_loss": {"st_opening": 1, "lt_closing": 0}}))
        self.assertIn("minimum_tax has no closing_by_year",
                      CF.block_problem({"minimum_tax": {"created": 70,
                                                        "closing": 70}}))
        whole = {"version": 1, "net_capital_loss": {
            "opening": 100, "created": 0, "applied": 0, "closing": 100}}
        self.assertIsNone(CF.block_problem(whole))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "filed").mkdir()
            lock = root / "filed" / "2025.json"
            lock.write_text(json.dumps({"year": 2025, "carryforwards": {
                "version": 1, "net_capital_loss": {"opening": 100}}}))
            with self.assertRaises(CF.CarryInputError) as cm:
                CF.resolve_losses(root, {"year": 2026}, "canada", False)
            msg = str(cm.exception)
            self.assertIn("2025.json", msg)
            self.assertIn("net_capital_loss has no closing", msg)
            self.assertIn("re-close", msg)
            # A whole block still carries; explicit input still wins.
            lock.write_text(json.dumps({"year": 2025,
                                        "carryforwards": whole}))
            r = CF.resolve_losses(root, {"year": 2026}, "canada", False)
            self.assertEqual(r["other_losses"], 100.0)
            self.assertEqual(CF.resolve_losses(root, {"year": 2026},
                                               "canada", True),
                             {"source": None, "notes": []})


class TestFiledLockShape(unittest.TestCase):
    def test_b38_nonfinite_locked_totals_refused(self):
        """B38: Filed-year drift check accepts NaN locked totals as
        matching real gains. A lock whose realized total was NaN passed
        lock_shape_problem, and diff_snapshot's comparisons with it were
        all False, so real gains of 100 showed no drift. A figure that is
        not a finite number is a damaged lock in both."""
        from taxjson.bin import taxjson_filed as filed
        lock = {"year": 2025,
                "accounts": {"margin": {"realized": float("nan")}}}
        shape = filed.lock_shape_problem(lock, 2025)
        self.assertIn("accounts.margin.realized", shape)
        self.assertIn("not a finite number", shape)
        with self.assertRaises(ValueError) as cm:
            filed.diff_snapshot(lock, {"margin": {"realized": 100}})
        self.assertIn("the lock is damaged", str(cm.exception))
        for v in (float("inf"), None, True, "100"):
            self.assertIsNotNone(filed.lock_shape_problem(
                {"year": 2025, "accounts": {"margin": {"realized": v}}},
                2025), v)
        self.assertIn("totals.realized", filed.lock_shape_problem(
            {"year": 2025, "accounts": {},
             "totals": {"realized": float("nan")}}, 2025))
        self.assertIn("form_lines.13200", filed.lock_shape_problem(
            {"year": 2025, "accounts": {"margin": {
                "realized": 1, "form_lines": {"13200": float("nan")}}}},
            2025))
        with self.assertRaises(ValueError):
            filed.diff_snapshot(
                {"year": 2025, "accounts": {"margin": {
                    "realized": 1.0, "form_lines": {"13200": float("nan")}}}},
                {"margin": {"realized": 1.0, "form_lines": {"13200": 1.0}}})
        with self.assertRaises(ValueError):
            filed.diff_snapshot(
                {"year": 2025, "accounts": {"margin": {
                    "realized": 1.0, "dispositions": float("nan")}}},
                {"margin": {"realized": 1.0, "dispositions": 1}})
        # json.loads reads NaN into a float: a lock on disk is caught.
        doc = json.loads('{"year": 2025, "accounts": {"margin": '
                         '{"realized": NaN}}}')
        self.assertTrue(math.isnan(doc["accounts"]["margin"]["realized"]))
        self.assertIsNotNone(filed.lock_shape_problem(doc, 2025))
        # A sound lock passes, and drift still shows.
        good = {"year": 2025, "accounts": {"margin": {"realized": 100.0,
                                                     "dispositions": 1}}}
        self.assertIsNone(filed.lock_shape_problem(good, 2025))
        self.assertEqual(filed.diff_snapshot(
            good, {"margin": {"realized": 100.0, "dispositions": 1}}), [])
        self.assertEqual(len(filed.diff_snapshot(
            good, {"margin": {"realized": 150.0, "dispositions": 1}})), 1)


if __name__ == "__main__":
    unittest.main()
