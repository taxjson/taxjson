"""Fix round 7 (wave B) of 2026-10-29, the sanity group: `tjs sanity`
and the holdings checks (lib/positions_check, cmd_sanity, lib/first_run).
Synthetic data only."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import _hermetic  # noqa: F401  (a synthetic HOME, offline)

from taxjson.lib import positions_check as PC
from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
           PYTHONPATH=str(REPO / "src"), NO_COLOR="1")


def _buy(day, settle, sym, q, cur="CAD", time="10:00:00", **kw):
    return dict(action="BUYSELL", date=day, date_settle=settle, time=time,
                symbol=sym, quantity=q, currency=cur, account="margin", **kw)


def _div(pay, sym, q, rec, cur="CAD"):
    return dict(action="DIVIDEND", date=pay, date_settle=pay, symbol=sym,
                quantity=q, record_date=rec, currency=cur,
                account="margin", net_amount=1.0,
                description=f"SYN CASH DIV ON {q:g} SHS")


class TestT3EraEntitlement(unittest.TestCase):
    """TJ-0815, TJ-0826: the eve of the ex-date was T+2 for every date
    before the 2024 T+1 move, so a T+3-era purchase two trading days
    before the record date counted as entitled and hid a dividend on
    shares the books never held on the record date."""

    def test_last_entitled_day_follows_the_era(self):
        # Canada and the US, T+3 until 2017-09-05.
        self.assertEqual(PC.last_entitled_trade_day("2016-05-17", "CAD"),
                         "2016-05-12")
        self.assertEqual(PC.last_entitled_trade_day("2016-11-09", "CAD"),
                         "2016-11-04")
        self.assertEqual(PC.last_entitled_trade_day("2016-11-09", "USD"),
                         "2016-11-04")
        # T+2 after, T+1 from May 2024 (unchanged).
        self.assertEqual(PC.last_entitled_trade_day("2022-08-01", "USD"),
                         "2022-07-28")
        self.assertEqual(PC.last_entitled_trade_day("2025-08-01", "USD"),
                         "2025-07-31")
        self.assertIsNone(PC.last_entitled_trade_day("2016-02-30", "CAD"))

    def test_a_late_t3_purchase_is_listed(self):
        # Monday 2016-11-07, settling Thursday 11-10: not a holder of
        # record on Wednesday 11-09.
        rows = [_buy("2016-11-07", "2016-11-10", "SYN.TO", 10),
                _div("2016-11-30", "SYN.TO", 10, "2016-11-09")]
        out = PC.income_share_mismatches(rows, "margin")
        self.assertEqual([(o["stated_shares"], o["books_shares"])
                          for o in out], [(10.0, 0.0)])
        # The entitled control: Friday 11-04, settling 11-09.
        rows[0] = _buy("2016-11-04", "2016-11-09", "SYN.TO", 10)
        self.assertEqual(PC.income_share_mismatches(rows, "margin"), [])

    def test_may_2016_record_date(self):
        rows = [_buy("2016-05-13", "2016-05-18", "SYN.TO", 10),
                _div("2016-05-31", "SYN.TO", 10, "2016-05-17")]
        out = PC.income_share_mismatches(rows, "margin")
        self.assertEqual([(o["on"], o["books_shares"]) for o in out],
                         [("2016-05-17", 0.0)])


def _project(td, accounts=("margin",), year=2025, country="canada",
             tt=None, ticker_map=""):
    """A project folder in `td` with .tt inputs: `tt` {account: text}."""
    root = Path(td) / "p"
    root.mkdir()
    cur = "USD" if country == "usa" else "CAD"
    tz = "America/New_York" if country == "usa" else "America/Toronto"
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "{tz}"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        f'source_currencies = []\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text(ticker_map)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
        if (tt or {}).get(a):
            (root / "inputs" / a / "trades.tt").write_text(tt[a])
    return root


def _tjs(root, *args):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=ENV, stdin=subprocess.DEVNULL)


def _run(root):
    r = _tjs(root, "run", "--no-input")
    if r.returncode != 0:
        raise AssertionError(f"run: rc {r.returncode}\n{r.stdout[-2000:]}"
                             f"\n{r.stderr[-2000:]}")


def _sanity_json(root, *args):
    r = _tjs(root, "sanity", *args, "--json")
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        doc = None
    return r.returncode, doc, r.stderr


class TestDatedSplitOrder(unittest.TestCase):
    """TJ-0812: the dated holdings check replayed a day's rows by clock
    time, so a split stamped at noon scaled a 09:30 purchase of the same
    day that the books (Canada: a split is effective at the open) hold
    as post-split shares."""

    ROWS = [
        dict(action="BUYSELL", date="2025-01-02", time="09:30:00",
             symbol="SYN.TO", quantity=10.0),
        dict(action="SPLIT", date="2025-06-02", time="12:00:00",
             symbol="SYN.TO", symbol_new="SYN.TO", quantity=2.0),
        dict(action="BUYSELL", date="2025-06-02", time="09:30:00",
             symbol="SYN.TO", quantity=5.0),
    ]

    def test_split_first_at_its_date(self):
        self.assertEqual(PC.positions_on(self.ROWS, "2025-06-30"),
                         {"SYN.TO": 25.0})
        # The US engine's order (clock time first): the split after.
        self.assertEqual(PC.positions_on(self.ROWS, "2025-06-30",
                                         clock=True), {"SYN.TO": 30.0})

    def test_sanity_dated_snapshot(self):
        tt = ("BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
              "SPLIT 2025-06-02 12:00:00 SYN.TO SYN.TO 2\n"
              "BUYSELL 2025-06-02 09:30:00 SYN.TO 5 CAD 6 30 0\n"
              "BUYSELL 2025-07-02 09:30:00 ZZE.TO 1 CAD 1 1 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tt={"margin": tt})
            _run(root)
            (root / "hold.toml").write_text(
                '[meta]\nas_of = "2025-06-30"\n[[holding]]\n'
                'symbol = "SYN.TO"\nquantity = 25\ncurrency = "CAD"\n')
            rc, doc, err = _sanity_json(root, "margin=hold.toml")
            self.assertEqual(rc, 0, err)
            self.assertEqual(doc["groups"][0]["as_of"], "2025-06-30")
            self.assertEqual(doc["discrepancies"], [])

    def test_us_books_keep_the_clock_order(self):
        # The US engine takes a noon split after a 09:30 purchase
        # (corporate_timeline 'us_main'): its books hold 30, and the
        # dated check replays them the same way.
        tt = ("BUYSELL 2025-01-02 09:30:00 SYN.US 10 USD 10 100 0\n"
              "SPLIT 2025-06-02 12:00:00 SYN.US SYN.US 2\n"
              "BUYSELL 2025-06-02 09:30:00 SYN.US 5 USD 6 30 0\n"
              "BUYSELL 2025-07-02 09:30:00 ZZE.US 1 USD 1 1 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tt={"margin": tt}, country="usa")
            _run(root)
            (root / "hold.toml").write_text(
                '[meta]\nas_of = "2025-06-30"\n[[holding]]\n'
                'symbol = "SYN.US"\nquantity = 30\ncurrency = "USD"\n')
            rc, doc, err = _sanity_json(root, "margin=hold.toml")
            self.assertEqual(rc, 0, err)
            self.assertEqual(doc["groups"][0]["as_of"], "2025-06-30")


class TestUnknownCostIsNotCompared(unittest.TestCase):
    """TJ-0818: units an OPENING declares with cost=unknown carry a 0
    placeholder in the books; sanity summed it as a known cost and
    certified a broker's 0 book value as a match."""

    def _check(self, opening, expect_status):
        tt = (opening + "BUYSELL 2025-05-02 09:30:00 SYN.TO -5 CAD 10 50 0\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tt={"margin": tt})
            _run(root)
            (root / "hold.toml").write_text(
                '[meta]\nas_of = "2025-12-31"\n[[holding]]\n'
                'symbol = "SYN.TO"\nquantity = 5\ncurrency = "CAD"\n'
                'total_cost = 0\n')
            rc, doc, err = _sanity_json(root, "margin=hold.toml")
            self.assertEqual(rc, 0, err)
            rows = doc["cost"]["rows"]
            self.assertEqual([r["status"] for r in rows], [expect_status])
            return doc

    def test_unknown_cost_is_not_a_match(self):
        doc = self._check('OPENING 2024-12-31 SYN.TO 10 cost=unknown '
                          'reason="bought before the data"\n', "n/a")
        self.assertEqual(doc["cost"]["matched"], 0)
        self.assertIn("cost is unknown", doc["cost"]["rows"][0]["note"])

    def test_a_known_zero_cost_still_matches(self):
        doc = self._check("OPENING 2024-12-31 SYN.TO 10 CAD 0\n", "match")
        self.assertEqual(doc["cost"]["matched"], 1)

    def test_load_inventory_keeps_the_flag(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "g.json"
            f.write_text(json.dumps({"inventory": [
                {"symbol": "SYN.TO", "qty": 5, "total_cost": 0,
                 "unknown_cost": True},
                {"symbol": "SYN.TO", "qty": 5, "total_cost": 30},
                {"symbol": "ZZE.TO", "qty": 1, "total_cost": 0}]}))
            inv = PC.load_inventory(f)
        self.assertTrue(inv["SYN.TO"]["unknown_cost"])
        self.assertFalse(inv["ZZE.TO"]["unknown_cost"])


class TestDatedCostAfterAnotherAccountsTrade(unittest.TestCase):
    """TJ-0817: a dated snapshot's cost was compared with the latest
    pooled ACB, which another taxable account's purchase after that
    date had moved (books 200 against the June 30 cost of 100)."""

    HOLD = ('[meta]\nas_of = "2025-06-30"\n[[holding]]\n'
            'symbol = "SYN.TO"\nquantity = 10\ncurrency = "CAD"\n'
            'total_cost = 100\n')

    def test_pool_moved_after_the_date(self):
        tt = {"a": "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n",
              "b": "BUYSELL 2025-07-02 09:30:00 SYN.TO 10 CAD 30 300 0\n"}
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, accounts=("a", "b"), tt=tt)
            _run(root)
            (root / "hold.toml").write_text(self.HOLD)
            rc, doc, err = _sanity_json(root, "a=hold.toml")
            self.assertEqual(rc, 0, err)
            self.assertEqual(doc["cost_differences"], [])
            row = doc["cost"]["rows"][0]
            self.assertEqual(row["status"], "n/a")
            self.assertIn("pooled ACB changed after the report's date "
                          "2025-06-30 (account b traded it)", row["note"])

    def test_a_trade_before_the_date_still_compares(self):
        tt = {"a": "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
                   "BUYSELL 2025-07-02 09:30:00 ZZE.TO 1 CAD 1 1 0\n",
              "b": "BUYSELL 2025-03-02 09:30:00 SYN.TO 10 CAD 30 300 0\n"}
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, accounts=("a", "b"), tt=tt)
            _run(root)
            (root / "hold.toml").write_text(self.HOLD)
            rc, doc, err = _sanity_json(root, "a=hold.toml")
            self.assertEqual(rc, 0, err)
            row = doc["cost"]["rows"][0]
            self.assertEqual(row["status"], "differs")
            self.assertIn("pooled", row["reasons"])


class TestOptionMatchedByItsUnderlyingCost(unittest.TestCase):
    """TJ-0813: a class-share option the file spells by the exchange's
    root (SYN271217C...) was matched to the books' SYN.B271217C... by
    its `underlying`, and then its cost was "not compared"."""

    def test_cost_compared_through_the_match(self):
        for sym in ("SYN271217C00030000.TO", "SYN.B271217C00030000.TO"):
            with self.subTest(sym=sym), \
                    tempfile.TemporaryDirectory() as td:
                root = _project(td, tt={"margin": (
                    "BUYSELL 2025-01-02 09:30:00 SYN.B271217C00030000.TO "
                    "1 CAD 1 100 0\n")})
                _run(root)
                (root / "hold.toml").write_text(
                    f'[meta]\nas_of = "2025-12-31"\n[[holding]]\n'
                    f'symbol = "{sym}"\nunderlying = "SYN.B.TO"\n'
                    f'quantity = 1\ncurrency = "CAD"\ntotal_cost = 200\n'
                    f'asset_type = "option"\nmultiplier = 100\n')
                rc, doc, err = _sanity_json(root, "margin=hold.toml")
                self.assertEqual(rc, 0, err)
                self.assertEqual(doc["cost"]["compared"], 1, doc["cost"])
                d = doc["cost_differences"]
                self.assertEqual([(c["symbol"], c["books_cost"],
                                   c["broker_cost"]) for c in d],
                                 [("SYN.B271217C00030000.TO", 100.0,
                                   200.0)])


class TestDistinctIsNoSpelling(unittest.TestCase):
    """TJ-0836: two listings ticker.map declares DISTINCT, one in the
    books and the other at the broker, were called one symbol spelled
    two ways, and sanity told the user to rewrite the snapshot."""

    def _pointers(self, ticker_map):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ticker_map=ticker_map, tt={"margin": (
                "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n")})
            _run(root)
            (root / "hold.toml").write_text(
                '[meta]\nas_of = "2025-12-31"\n[[holding]]\n'
                'symbol = "SYN.US"\nquantity = 10\ncurrency = "CAD"\n')
            r = _tjs(root, "sanity", "margin=hold.toml")
            self.assertEqual(r.returncode, 1, r.stderr)
            return " ".join(r.stdout.split())

    def test_declared_distinct(self):
        out = self._pointers("DISTINCT SYN.TO SYN.US\n")
        self.assertNotIn("symbol spelling", out)
        self.assertNotIn("use the books' symbol", out)
        self.assertIn("1 with fewer shares (SYN.US)", out)
        self.assertIn("1 more in the books", out)

    def test_without_the_line_it_is_a_spelling(self):
        out = self._pointers("")
        self.assertIn("1 symbol spelling (SYN.US / SYN.TO)", out)


_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Activity Type,Account #,Account Type\n")


class TestIncomeCheckWithoutASnapshot(unittest.TestCase):
    """TJ-0688: with no holdings snapshot sanity stopped at "nothing to
    compare with" before its books-only check of dividends paid on
    shares the books never held."""

    def test_income_listed_with_no_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "inputs" / "margin" / "div.csv").write_text(
                _QT_HEAD + "2025-06-03,2025-06-03,DIV,SYN.TO,SYN CORP CASH "
                "DIV ON 10 SHS REC 05/30/25 PAY 06/03/25 CAD 1 PER SHARE,"
                "0,0,10,0,10,CAD,Dividends,99900001,Margin\n")
            _run(root)
            rc, doc, err = _sanity_json(root)
            self.assertEqual(rc, 0, err)
            self.assertEqual([(m["symbol"], m["stated_shares"],
                               m["books_shares"])
                              for m in doc["income_share_mismatches"]],
                             [("SYN.TO", 10.0, 0.0)])
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("INCOME: 1 dividend row(s) state shares the books "
                          "did not hold", r.stdout)
            r = _tjs(root, "sanity", "--details")
            self.assertIn("INCOME ON SHARES THE BOOKS DO NOT HOLD",
                          r.stdout)


class TestUnreadableHoldingsTomlExits2(unittest.TestCase):
    """TJ-0643: a holdings TOML sanity cannot read as a snapshot exited
    1, the status of "positions differ", and was named by its bare file
    name; it is an input that cannot be read (exit 2), named from the
    project."""

    CASES = (
        ('[meta]\naccount = "margin"\nas_of = 2025-12-31\n'
         '[positions]\n"ZZQ.TO" = 1\n', "has no [[holding]] array"),
        ('[[holding]]\nsymbol = "ZZQ.TO"\n', "a [[holding]] row has no "
                                             "quantity"),
        ('[[holding]]\nsymbol = "ZZQ.TO"\nquantity = "x"\n',
         "quantity 'x' is not a number"),
        ('[[holding]]\nsymbol = "ZZQ.TO"\nquantity = nan\n',
         "quantity is not finite"),
        ('[[holding]]\nquantity = 1\n', "row has quantity 1 but no symbol"),
        ('holding = [1]\n', "entry is not a table"),
    )

    def test_exit_2_named_from_the_project(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tt={"margin": (
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 100 100 0\n")})
            _run(root)
            (root / "holdings").mkdir()
            for text, why in self.CASES:
                with self.subTest(why=why):
                    f = root / "holdings" / "margin_holdings.toml"
                    f.write_text(text)
                    r = _tjs(root, "sanity")
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertIn("error: holdings/margin_holdings.toml",
                                  r.stderr)
                    self.assertIn(why, r.stderr)
                    r = _tjs(root, "sanity", "margin=holdings/"
                             "margin_holdings.toml")
                    self.assertEqual(r.returncode, 2, r.stderr)


class TestIncomeNotHeldUsesTheRecordDate(unittest.TestCase):
    """TJ-0811: a trust distribution paid on 2025-03-10 for a 2024-12-13
    record date, on units held then and sold on 2024-12-16, is 2024
    income; the run still warned that a security paid income in 2025
    that the books do not hold (pay date plus a 60-day grace)."""

    TT = ("BUYSELL 2024-06-03 10:00:00 SYN.UN.TO 10 CAD 10 100 0\n"
          "BUYSELL 2024-12-16 10:00:00 SYN.UN.TO -10 CAD 12 120 0\n"
          "DIVIDEND {pay} 10:00:00 SYN.UN.TO 10 CAD 1 10 "
          "record=2024-12-13 label={label} issuer=CA\n")

    def _collect(self, pay, label="distribution"):
        from taxjson.lib import first_run as F
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tt={"margin": self.TT.format(
                pay=pay, label=label)})
            _run(root)
            cfg = tomllib.loads((root / "taxjson.toml").read_text())
            return F.collect(root, cfg).get("income_not_held")

    def test_prior_year_distribution_is_held(self):
        self.assertEqual(self._collect("2025-03-10"), [])

    def test_record_date_holding_of_a_dividend(self):
        # A corporation's dividend is the pay date's income (2025), and
        # the units were held on its record date, 84 days before.
        self.assertEqual(self._collect("2025-03-07", label="dividend"), [])

    def test_never_held_is_still_listed(self):
        from taxjson.lib import first_run as F
        from taxjson.lib.core import TaxTransaction
        rows = [TaxTransaction(
            date="2025-06-03", date_settle="2025-06-03", time="10:00:00",
            account="margin", symbol="ZZQ.TO", action="DIVIDEND",
            quantity=10, currency="CAD", gross_amount=10, net_amount=10,
            record_date="2025-05-30"),
            TaxTransaction(
            date="2025-01-03", date_settle="2025-01-03", time="10:00:00",
            account="margin", symbol="ZZE.TO", action="BUYSELL",
            quantity=1, currency="CAD", gross_amount=-1, net_amount=-1)]
        found = F.income_without_position(rows, 2025)
        self.assertEqual([h["symbol"] for h in found], ["ZZQ.TO"])


if __name__ == "__main__":
    unittest.main()
