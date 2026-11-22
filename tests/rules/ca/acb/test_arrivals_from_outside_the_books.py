"""New-user guidance (owner-approved gaps from the getting-started study).

1. A closing summary at the end of `tjs run`: sales with no purchase
   not covered by missing history, $0-cost positions (sold or still
   held), transfer-ins kept out with no cost, accounts with open
   positions and no holdings check, income on a security the books do
   not hold — each with its command; silent when clean; the counts in
   reports/run_summary.json. The short-position NOTE of a taxable account
   is on the console.
2. `tjs sum` warns about uncovered sales with no purchase; "tainted"
   is "unknown cost" in every message (JSON keys kept, aliases added).
3. A transfer-in from outside the books takes the broker's STATED book
   value as its cost (never a market value), with an ATTENTION that a
   covering .tt line silences (CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).
4. Messages: the crypto time-zone refusal, the sanity mismatch hint, the
   stage-failure line, a stale missing-history line, the inputs/
   README per broker.
5. A US scaffold fetches no CAD rates.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_newuser_guidance
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, gains_both, settings_for, tx
from taxjson.rules import ids as R
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _qrow(td, action, sym, desc, qty, price, gross, net, cur="CAD",
          act="Trades", acct="55500001"):                      # pii-ok
    return (f"{td},{td},{action},{sym},{desc},{qty},{price},{gross},0.00,"
            f"{net},{cur},{act},{acct},Margin\n")


def _project(tmp, *, country="canada", year=2025, accounts=None,
             files=None, extra_settings=""):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, source_currencies=[])
        + extra_settings
        + (accounts or '[accounts.margin]\ntype = "taxable"\n'))
    for rel, text in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


# ---------------------------------------------------------------- item 3

class TestTransferInLib(unittest.TestCase):
    """Item 3: which transfer-ins came from outside the books.

    Formerly: test_fix_newuser_guidance.TestTransferInLib"""

    def _rows(self, *rows):
        return [(a, b, dict(t, action="TRANSFER")) for a, b, t in rows]

    def test_own_moves_and_journals_cancel(self):
        from taxjson.lib.transfer_in import arrivals
        rows = self._rows(
            # a broker's internal shuffle and the move to another broker
            ("margin", "rbc_direct", {"symbol": "AAA.TO", "quantity": -10,
                                      "date": "2025-06-30",
                                      "book_value": 100}),
            ("margin", "rbc_direct", {"symbol": "AAA.TO", "quantity": 10,
                                      "date": "2025-06-30",
                                      "book_value": 100}),
            ("margin", "rbc_direct", {"symbol": "AAA.TO", "quantity": -10,
                                      "date": "2025-07-02",
                                      "book_value": 100}),
            ("margin", "ib", {"symbol": "AAA.TO", "quantity": 10,
                              "date": "2025-07-08"}),
            # a move between two of your taxable accounts
            ("cash", "questrade", {"symbol": "BBB.TO", "quantity": -5,
                                   "date": "2025-02-01"}),
            ("margin", "questrade", {"symbol": "BBB.TO", "quantity": 5,
                                     "date": "2025-02-03",
                                     "book_value": 50}),
            # a listing journal with no ticker.map rule
            ("margin", "questrade", {"symbol": "CCC.U.TO", "quantity": -7,
                                     "date": "2025-04-01"}),
            ("margin", "questrade", {"symbol": "CCC.TO", "quantity": 7,
                                     "date": "2025-04-01",
                                     "book_value": 70}),
            # shares from outside: the only arrival
            ("margin", "questrade", {"symbol": "DDD.TO", "quantity": 20,
                                     "date": "2025-05-01",
                                     "book_value": 400}))
        found = arrivals(rows)
        self.assertEqual([(a.symbol, a.quantity, a.book_value, a.status)
                          for a in found],
                         [("DDD.TO", 20.0, 400.0, "book_value")])

    def test_market_value_is_never_a_cost(self):
        # IB's transfer VALUE (net_amount) is the market value: no
        # stated book value, so the arrival has no cost.
        from taxjson.lib.transfer_in import arrivals, booked_rows
        found = arrivals(self._rows(
            ("margin", "ib", {"symbol": "EEE.US", "quantity": 30,
                              "date": "2025-05-01", "net_amount": 9000.0,
                              "currency": "USD"})))
        self.assertEqual([(a.status, a.book_value) for a in found],
                         [("no_cost", None)])
        self.assertEqual(booked_rows(found), [])

    def test_partial_arrival_takes_its_share_of_the_book_value(self):
        from taxjson.lib.transfer_in import arrivals
        found = arrivals(self._rows(
            ("margin", "questrade", {"symbol": "FFF.TO", "quantity": -40,
                                     "date": "2025-05-01"}),
            ("margin", "questrade", {"symbol": "FFF.TO", "quantity": 100,
                                     "date": "2025-05-02",
                                     "book_value": 1000})))
        self.assertEqual([(a.quantity, a.book_value) for a in found],
                         [(60.0, 600.0)])

    def test_a_tt_purchase_covers_the_arrival(self):
        from taxjson.lib.transfer_in import arrivals, mark_covered
        found = arrivals(self._rows(
            ("margin", "questrade", {"symbol": "GGG.TO", "quantity": 10,
                                     "date": "2025-05-02",
                                     "book_value": 100}),
            ("margin", "questrade", {"symbol": "HHH.TO", "quantity": 10,
                                     "date": "2025-05-02"})))
        mark_covered(found, {"margin": [
            {"action": "BUYSELL", "symbol": "GGG.TO", "quantity": 10,
             "date": "2021-03-15"},
            # dated AFTER the arrival: not the shares that arrived
            {"action": "BUYSELL", "symbol": "HHH.TO", "quantity": 10,
             "date": "2025-06-01"}]})
        self.assertEqual({a.symbol: a.status for a in found},
                         {"GGG.TO": "covered", "HHH.TO": "no_cost"})

    def test_missing_history_entry_covers_the_arrival(self):
        # The user's own answer (cost unknown, reported by hand) wins
        # over the broker's book value.
        from taxjson.lib.transfer_in import (arrivals, booked_rows,
                                             mark_missing_history)
        found = arrivals(self._rows(
            ("margin", "rbc_direct", {"symbol": "JJJ.US", "quantity": 10,
                                      "date": "2022-04-22",
                                      "book_value": 900})))
        mark_missing_history(found, {("JJJ.US", "margin")})
        self.assertEqual([a.status for a in found], ["missing_history"])
        self.assertEqual(booked_rows(found), [])


def _bv_project(td, country):
    cur, sym = (("CAD", "XYZ.TO") if country == "canada"
                else ("USD", "XYZ"))
    return _project(td, country=country, files={
        "inputs/margin/questrade.csv": _QH
        + _qrow("2025-03-03", "TF6", sym,
                "XYZ CORP TRANSFER BOOK VALUE 5000.00", 100, "0.00",
                "0.00", "0.00", cur=cur, act="Transfers")
        + _qrow("2025-04-01", "Sell", sym, "XYZ CORP", -100, "60.00",
                "6000.00", "6000.00", cur=cur)})


class TestTransferInBookValue(unittest.TestCase):
    """Item 3 end to end: the stated book value is the incoming cost.

    Formerly: test_fix_newuser_guidance.TestTransferInBookValue"""

    def _gain(self, root):
        j = json.loads(cli(root, "sum", "--json").stdout)
        return j["filing"]["totals"]["gain"]

    @rule(R.CA_ACB_TRANSFER_BV)
    def test_canada_book_value_is_the_acb(self):
        with tempfile.TemporaryDirectory() as td:
            root = _bv_project(td, "canada")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("ATTENTION: transfer-in: margin: 1 transfer-in(s) "
                          "from outside your books booked at the ACB the "
                          "broker states on the row (Questrade: 100 XYZ.TO "
                          "(2025-03-03))", r.stdout)
            self.assertIn("add the original purchase as a .tt BUYSELL",
                          r.stdout)
            self.assertNotIn("holding period", r.stdout)
            # The parser's own no-book-value caveat stays quiet: the run
            # says it (or not) once.
            self.assertNotIn("carry no TRANSFER BOOK VALUE", r.stderr)
            self.assertEqual(self._gain(root), 1000.0)
            # `tjs transfers` says what the books did with the row.
            j = json.loads(cli(root, "transfers", "--json").stdout)
            self.assertEqual([t["arrival"] for t in j["transfers"]],
                             ["book value"])
            # The original purchase as a .tt line overrides it, and the
            # ATTENTION stops.
            (root / "inputs" / "margin" / "start.tt").write_text(
                "BUYSELL 2021-03-15 09:30:00 XYZ.TO 100 CAD 40 4000 0\n")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("transfer-in:", r.stdout)
            self.assertFalse((root / "work" /
                              "margin_transfer_costs.json").exists())
            self.assertEqual(self._gain(root), 2000.0)
            j = json.loads(cli(root, "transfers", "--json").stdout)
            self.assertEqual([t["arrival"] for t in j["transfers"]],
                             [".tt covers"])

    @rule(R.US_BASIS_TRANSFER_BV)
    def test_us_book_value_is_the_basis_with_the_holding_period_caveat(self):
        with tempfile.TemporaryDirectory() as td:
            root = _bv_project(td, "usa")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("booked at the basis the broker states on the "
                          "row (Questrade: 100 XYZ.US (2025-03-03))",
                          r.stdout)
            self.assertIn("holding period starts on the arrival date",
                          r.stdout)
            self.assertEqual(self._gain(root), 1000.0)

    @rule(R.CA_ACB_TRANSFER_BV)
    def test_canada_missing_history_entry_keeps_the_manual_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = _bv_project(td, "canada")
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            self.assertTrue((root / "work" / "margin_transfer_costs.json")
                            .exists())
            (root / "inputs" / "margin" / "missing_history.tt").write_text(
                "OPENING 2025-03-02 XYZ.TO 100 cost=unknown\n")
            # The bookings going away re-merge the books.
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("transfer-in:", r.stdout)
            j = json.loads(cli(root, "sum", "--json").stdout)
            self.assertEqual(j["unknown_cost_routed"], 1)
            t = json.loads(cli(root, "transfers", "--json").stdout)
            self.assertEqual([x["arrival"] for x in t["transfers"]],
                             ["missing history"])

    @rule(R.CA_ACB_11)
    def test_migrate_keeps_the_entry_that_covers_a_booked_arrival(self):
        # Books built without missing_history.json (the run refuses it:
        # moved aside) booked the arrival at its book value, so its sale
        # never goes short there; the entry covered that arrival —
        # migrate sizes it as the run WITH the file did (100 units),
        # never "no opening needed".
        with tempfile.TemporaryDirectory() as td:
            root = _bv_project(td, "canada")
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            (root / "missing_history.json").write_text(json.dumps(
                [{"symbol": "XYZ.TO", "account": "margin"}]))
            r = cli(root, "migrate")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            tt = (root / "inputs" / "margin" /
                  "missing_history.tt").read_text()
            self.assertIn(" XYZ.TO 100 cost=unknown", tt)
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            j = json.loads(cli(root, "sum", "--json").stdout)
            self.assertEqual(j["unknown_cost_routed"], 1)

    @rule(R.CA_ACB_TRANSFER_BV)
    def test_canada_own_account_move_is_not_booked(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, accounts=(
                '[accounts.qa]\ntype = "taxable"\n'
                '[accounts.qb]\ntype = "taxable"\n'), files={
                "inputs/qa/questrade.csv": _QH
                + _qrow("2025-02-02", "Buy", "XYZ.TO", "XYZ CORP", 100,
                        "50.00", "-5000.00", "-5000.00")
                + _qrow("2025-03-02", "TF6", "XYZ.TO", "XYZ CORP TRANSFER "
                        "OUT", -100, "0.00", "0.00", "0.00",
                        act="Transfers"),
                "inputs/qb/questrade.csv": _QH
                + _qrow("2025-03-03", "TF6", "XYZ.TO", "XYZ CORP TRANSFER "
                        "BOOK VALUE 5000.00", 100, "0.00", "0.00", "0.00",
                        act="Transfers", acct="55500002")})       # pii-ok
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("transfer-in:", r.stdout)
            self.assertFalse((root / "work" / "qb_transfer_costs.json")
                             .exists())
            j = json.loads(cli(root, "transfers", "--json").stdout)
            self.assertEqual({(t["account"], t["arrival"])
                              for t in j["transfers"]},
                             {("qa", "-"), ("qb", "own move")})


class TestTransferArrivalIsNotAReplacement(unittest.TestCase):
    """CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV: the arrival is dated by
    its custody move, not an acquisition — never the purchase that makes
    a loss superficial / a wash sale (in both countries).

    Formerly: test_fix_newuser_guidance.TestTransferArrivalIsNotAReplacement"""

    @rule(R.CA_ACB_TRANSFER_BV)
    @rule(R.US_BASIS_TRANSFER_BV)
    def test_arrival_inside_the_window_denies_nothing(self):
        book = [tx("BUYSELL", "2025-01-02", "LSS.US", 100, 5000),
                tx("BUYSELL", "2025-03-03", "LSS.US", -100, 3000),
                tx("BUYSELL", "2025-03-10", "LSS.US", 100, 3100,
                   type="transfer_book_value")]
        r = gains_both(book, year=2025)
        for c in ("canada", "usa"):
            self.assertEqual(r[c]["summary"]["total_disallowed"], 0.0, c)
        # The same row as an ordinary purchase is denied in both.
        book[-1] = tx("BUYSELL", "2025-03-10", "LSS.US", 100, 3100)
        r = gains_both(book, year=2025)
        for c in ("canada", "usa"):
            self.assertGreater(r[c]["summary"]["total_disallowed"], 0.0, c)


if __name__ == "__main__":
    unittest.main()
