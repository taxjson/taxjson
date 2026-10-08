"""New-user guidance (owner-approved gaps from the getting-started study).

1. A closing summary at the end of `taxjson run`: sales with no purchase
   not covered by missing_history.json, $0-cost positions (sold or still
   held), transfer-ins kept out with no cost, accounts with open
   positions and no holdings check, income on a security the books do
   not hold — each with its command; silent when clean; the counts in
   reports/run_summary.json. The short-position NOTE of a taxable account
   is on the console.
2. `taxjson sum` warns about uncovered sales with no purchase; "tainted"
   is "unknown cost" in every message (JSON keys kept, aliases added).
3. A transfer-in from outside the books takes the broker's STATED book
   value as its cost (never a market value), with an ATTENTION that a
   covering .tt line silences (CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).
4. Messages: the crypto time-zone refusal, the sanity mismatch hint, the
   stage-failure line, a stale missing_history.json entry, the inputs/
   README per broker.
5. A US scaffold fetches no CAD rates.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, gains_both, settings_for, tx
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


# A Canadian margin account with one of each first-run finding.
_CA_QT = _QH + (
    _qrow("2025-02-03", "Buy", "ABC.TO", "ABC CORP", 10, "20.00",
          "-200.00", "-200.00")
    + _qrow("2025-03-03", "TF6", "XYZ.TO",
            "XYZ CORP TRANSFER BOOK VALUE 5000.00", 100, "0.00", "0.00",
            "0.00", act="Transfers")
    + _qrow("2025-03-04", "TF6", "QRS.TO", "QRS CORP TRANSFER", 50, "0.00",
            "0.00", "0.00", act="Transfers")
    + _qrow("2025-04-01", "Sell", "XYZ.TO", "XYZ CORP", -100, "60.00",
            "6000.00", "6000.00")
    + _qrow("2025-05-01", "Sell", "SMA.TO", "SMA CORP", -20, "40.00",
            "800.00", "800.00"))
_CA_TT = ("BUYSELL  2025-01-10  09:30:00  ZRO.TO  5  CAD  0  0  0\n"
          "DIVIDEND 2025-06-30  09:30:00  DIV.TO  100  CAD  0.25  25.00\n")
_CLEAN_TT = ("BUYSELL  2025-01-10  09:30:00  ABC.TO  10  CAD  20  200  0\n"
             "BUYSELL  2025-03-10  09:30:00  ABC.TO  -10  CAD  25  250  0\n")


class TestFirstRunSummary(unittest.TestCase):
    """Item 1: the closing summary of `taxjson run`."""

    def test_every_finding_is_counted_with_its_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, files={
                "inputs/margin/questrade.csv": _CA_QT,
                "inputs/margin/extra.tt": _CA_TT})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            out = r.stdout
            tail = out[out.index("Done. Reports"):]
            self.assertIn("Before you trust these numbers", tail)
            self.assertIn("1 position sold in 2025 with no purchase in "
                          "your files, not in missing_history.json", tail)
            self.assertIn("SMA.TO (margin)", tail)
            self.assertIn("`taxjson find-missing-history`", tail)
            # $0 cost: still held, which the detector used to ignore.
            self.assertIn("1 position at a $0 cost (1 still held): "
                          "ZRO.TO (margin)", tail)
            self.assertIn("1 transfer-in from outside your books kept "
                          "out with no cost: QRS.TO (margin)", tail)
            self.assertIn("`taxjson transfers`", tail)
            self.assertIn("no holdings file to check them against: "
                          "margin", tail)
            self.assertIn("paid income in 2025 that the books do not "
                          "hold", tail)
            self.assertIn("DIV.TO (margin)", tail)
            self.assertIn("`taxjson checklist`", tail)
            self.assertIn("docs/getting-started.md", tail)
            # Short: a heading, one line per finding, one closing line.
            block = tail[tail.index("==> Before"):].strip().splitlines()
            self.assertLessEqual(len(block), 8, block)
            doc = json.loads((root / "reports" / "run_summary.json")
                             .read_text())
            self.assertEqual([x["symbol"] for x in doc["no_purchase"]],
                             ["SMA.TO"])
            self.assertEqual([x["symbol"] for x in doc["zero_cost_held"]],
                             ["ZRO.TO"])
            self.assertEqual(
                [x["symbol"] for x in doc["transfer_in_no_cost"]],
                ["QRS.TO"])
            self.assertEqual(
                [x["symbol"] for x in doc["transfer_in_book_value"]],
                ["XYZ.TO"])
            self.assertEqual(doc["unchecked_accounts"][0]["account"],
                             "margin")
            self.assertEqual([x["symbol"] for x in doc["income_not_held"]],
                             ["DIV.TO"])
            # The taxable account's short-position note is on the console.
            self.assertIn("note: 1 position(s) go short in margin's data "
                          "(SMA.TO) Sales with no purchase in your files",
                          " ".join(out.split()))

    def test_clean_project_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            hold = Path(td) / "h.toml"
            hold.write_text('[[holding]]\nsymbol = "XXX.TO"\nquantity = 0\n')
            root = _project(td, accounts=(
                '[accounts.margin]\ntype = "taxable"\n'
                f'holdings = ["{hold}"]\n'),
                files={"inputs/margin/start.tt": _CLEAN_TT})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("before you trust these numbers", r.stdout)
            doc = json.loads((root / "reports" / "run_summary.json")
                             .read_text())
            self.assertFalse(doc["no_purchase"] or doc["zero_cost_held"])

    def test_missing_history_covers_the_sale(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, files={
                "inputs/margin/questrade.csv": _CA_QT,
                "missing_history.json": json.dumps(
                    [{"symbol": "SMA.TO", "account": "margin"}])})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("sold in 2025 with no purchase", r.stdout)
            # ... and `sum` names it as an unknown cost, not "tainted".
            s = cli(root, "sum")
            self.assertIn("1 disposition(s) with an unknown cost (no "
                          "purchase in your files) were routed to manual "
                          "reporting", s.stderr)
            self.assertNotIn("tainted", s.stderr + s.stdout)
            j = json.loads(cli(root, "sum", "--json").stdout)
            self.assertEqual(j["unknown_cost_routed"], 1)
            self.assertEqual(j["tainted_routed"], 1)      # kept for readers
            self.assertEqual(j["no_purchase_uncovered"], [])


class TestFirstRunLib(unittest.TestCase):
    """Item 1, the detectors under the summary."""

    def test_income_on_a_security_never_held(self):
        from taxjson.lib.first_run import income_without_position
        rows = [tx("BUYSELL", "2025-01-02", "HLD.TO", 10, 100,
                   currency="CAD"),
                tx("DIVIDEND", "2025-03-31", "HLD.TO", 0, 5,
                   currency="CAD"),
                tx("DIVIDEND", "2025-03-31", "NOT.TO", 0, 5,
                   currency="CAD")]
        found = income_without_position(rows, 2025)
        self.assertEqual([(h["symbol"], h["rows"]) for h in found],
                         [("NOT.TO", 1)])
        # A holding declared in missing_history.json is not a surprise.
        self.assertEqual(income_without_position(
            rows, 2025, declared={("NOT.TO", "margin")}), [])

    def test_income_paid_just_after_a_sale_is_not_flagged(self):
        # Sold after the record date, paid after the sale: a late
        # payment on shares just held, not a missing holding.
        from taxjson.lib.first_run import income_without_position
        rows = [tx("BUYSELL", "2025-01-02", "LTE.TO", 10, 100,
                   currency="CAD"),
                tx("BUYSELL", "2025-03-20", "LTE.TO", -10, 120,
                   currency="CAD"),
                tx("DIVIDEND", "2025-04-15", "LTE.TO", 0, 5,
                   currency="CAD"),
                tx("DIVIDEND", "2025-09-15", "LTE.TO", 0, 5,
                   currency="CAD")]
        found = income_without_position(rows, 2025)
        self.assertEqual([(h["symbol"], h["first"]) for h in found],
                         [("LTE.TO", "2025-09-15")])

    def test_income_beside_its_shares_or_at_the_data_start_is_not_flagged(self):
        # A spin-off's deemed dividend is booked beside the shares it
        # delivers (same day, the dividend row first); a payment in the
        # first 60 days of the data is for shares sold just before it.
        from taxjson.lib.first_run import income_without_position
        rows = [tx("DIVIDEND", "2025-01-03", "OLD.TO", 0, 5,
                   currency="CAD"),
                tx("BUYSELL", "2025-01-05", "AAA.TO", 10, 100,
                   currency="CAD"),
                tx("DIVIDEND", "2025-10-22", "SPN.US", 0, 1.15),
                tx("BUYSELL", "2025-10-22", "SPN.US", 60, 1.15,
                   time="16:00:00")]
        self.assertEqual(income_without_position(rows, 2025), [])

    def test_zero_cost_still_held_is_reported(self):
        from taxjson.lib.missing_history import (
            detect_zero_basis_acquisitions)
        rows = [tx("BUYSELL", "2025-01-02", "FRE.TO", 5, 0, price=0,
                   currency="CAD")]
        self.assertEqual(detect_zero_basis_acquisitions(rows, 2025), [])
        held = detect_zero_basis_acquisitions(rows, 2025,
                                              include_held=True)
        self.assertEqual([(r.symbol, r.still_held_qty, r.sold,
                           r.affects_year) for r in held],
                         [("FRE.TO", 5.0, False, False)])

    @rule("CA-STKDIV-01")
    def test_canada_stock_dividend_shares_are_zero_cost(self):
        from taxjson.lib.first_run import zero_cost_positions
        rows = [tx("BUYSELL", "2025-01-02", "STK.TO", 100, 1000,
                   currency="CAD"),
                tx("BUYSELL", "2025-03-02", "STK.TO", 5, 0, price=0,
                   currency="CAD", type="stock_dividend")]
        sold, held = zero_cost_positions(rows, 2025, sheltered={},
                                         country="canada")
        self.assertEqual([r.symbol for r in held], ["STK.TO"])

    @rule("US-STKDIV-01")
    def test_us_stock_dividend_shares_share_the_basis(self):
        # §307: the basis is spread over old and new shares — the new
        # shares are not missing a cost (no $0-cost finding).
        from taxjson.lib.first_run import zero_cost_positions
        rows = [tx("BUYSELL", "2025-01-02", "STK.US", 100, 1000),
                tx("BUYSELL", "2025-03-02", "STK.US", 5, 0, price=0,
                   type="stock_dividend")]
        sold, held = zero_cost_positions(rows, 2025, sheltered={},
                                         country="usa")
        self.assertEqual((sold, held), ([], []))


class TestSumWarnsUncoveredSales(unittest.TestCase):
    """Item 2: a Questrade / RBC / Webull sale with no purchase was
    silent in `sum`."""

    def test_sum_names_the_sale_and_the_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, files={
                "inputs/margin/questrade.csv": _CA_QT})
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            r = cli(root, "sum")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("taxjson sum: warning: 1 position(s) sold in "
                          "2025 with no purchase in your files: their gain "
                          "is NOT in these totals", r.stderr)
            self.assertIn("Not in missing_history.json: SMA.TO (margin)",
                          r.stderr)
            self.assertIn("taxjson find-missing-history", r.stderr)
            j = json.loads(cli(root, "sum", "--json").stdout)
            self.assertEqual(j["no_purchase_uncovered"],
                             [{"symbol": "SMA.TO", "account": "margin",
                               "sales": 1, "proceeds": 800.0}])

    def test_no_user_facing_tainted_left(self):
        # Every message string of the package says "unknown cost".
        import re
        import tokenize
        root = Path(__file__).resolve().parents[1] / "src" / "taxjson"
        msg = re.compile(r"\btainted (disposition|row|sale)|"
                         r"pool TAINTED|tainted \(unknown", re.I)
        hits = []
        for p in root.rglob("*.py"):
            with open(p, encoding="utf-8") as fh:
                for t in tokenize.generate_tokens(fh.readline):
                    if t.type == tokenize.STRING or t.type == getattr(
                            tokenize, "FSTRING_MIDDLE", -1):
                        if (msg.search(t.string)
                                and not t.string.lstrip("rbuf").startswith(
                                    ('"""', "'''"))):
                            hits.append(f"{p.name}:{t.start[0]}")
        self.assertEqual(hits, [])


# ---------------------------------------------------------------- item 3

class TestTransferInLib(unittest.TestCase):
    """Item 3: which transfer-ins came from outside the books."""

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
    """Item 3 end to end: the stated book value is the incoming cost."""

    def _gain(self, root):
        j = json.loads(cli(root, "sum", "--json").stdout)
        return j["filing"]["totals"]["gain"]

    @rule("CA-ACB-TRANSFER-BV")
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
            # `taxjson transfers` says what the books did with the row.
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

    @rule("US-BASIS-TRANSFER-BV")
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

    @rule("CA-ACB-TRANSFER-BV")
    def test_canada_missing_history_entry_keeps_the_manual_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = _bv_project(td, "canada")
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            self.assertTrue((root / "work" / "margin_transfer_costs.json")
                            .exists())
            (root / "missing_history.json").write_text(json.dumps(
                [{"symbol": "XYZ.TO", "account": "margin"}]))
            # --fast too: the bookings going away must re-merge the books.
            r = cli(root, "run", "--no-input", "--fast")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("transfer-in:", r.stdout)
            j = json.loads(cli(root, "sum", "--json").stdout)
            self.assertEqual(j["unknown_cost_routed"], 1)
            t = json.loads(cli(root, "transfers", "--json").stdout)
            self.assertEqual([x["arrival"] for x in t["transfers"]],
                             ["missing history"])

    @rule("CA-ACB-TRANSFER-BV")
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
    a loss superficial / a wash sale (in both countries)."""

    @rule("CA-ACB-TRANSFER-BV")
    @rule("US-BASIS-TRANSFER-BV")
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


# ---------------------------------------------------------------- item 4

class TestMessages(unittest.TestCase):

    def test_crypto_timezone_refusal_offers_deleting_the_account(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            env_tz = os.environ.pop("TAXJSON_LOCAL_TZ", None)
            try:
                r = cli(root, "run", "--no-input")
            finally:
                if env_tz:
                    os.environ["TAXJSON_LOCAL_TZ"] = env_tz
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("local_timezone", r.stderr)
            self.assertIn("If you have no crypto, delete the "
                          "[accounts.crypto] section", r.stderr)

    def test_stage_failure_names_the_step_not_the_argv(self):
        from taxjson.bin.taxjson_run import _cmd, _stage_description
        d = _stage_description(
            _cmd("taxjson-brokerage")
            + ["--account", "margin", "--brokerage", "ib", "--strict",
               "--account-type", "taxable", "--rates", "/w/to_base.csv",
               "/p/inputs/margin/a.csv", "/p/inputs/margin/b.csv"])
        self.assertEqual(d, "reading the broker files for account margin "
                            "(a.csv, b.csv)")
        self.assertNotIn("-m", d)
        self.assertEqual(
            _stage_description(_cmd("taxjson-gains") + [
                "--country", "canada", "--year", "2025",
                "/w/margin_base.json"]),
            "computing the gains (margin_base.json)")

    def test_stale_missing_history_entry_is_reported(self):
        from taxjson.lib.missing_history import (
            report_missing_history_log, stale_missing_history_entries,
            synthesize_openings)
        rows = [tx("BUYSELL", "2024-01-02", "CMP.TO", 10, 100,
                   currency="CAD"),
                tx("BUYSELL", "2025-02-10", "CMP.TO", -10, 120,
                   currency="CAD")]
        st = stale_missing_history_entries(rows, {("CMP.TO", "margin")},
                                           complete=True)
        self.assertEqual([(e.symbol, e.reason) for e in st],
                         [("CMP.TO", "complete")])
        _ops, log = synthesize_openings(rows, {("CMP.TO", "margin")})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            report_missing_history_log([log], {"margin"})
        self.assertIn("warning: ATTENTION: missing_history.json lists "
                      "CMP.TO / margin, but its rows never go short any "
                      "more — the purchase is in the books now",
                      err.getvalue())

    def test_several_stale_entries_are_one_line(self):
        from taxjson.lib.missing_history import (
            report_missing_history_log, synthesize_openings)
        rows = [tx("BUYSELL", "2024-01-02", s, 10, 100, currency="CAD")
                for s in ("CMA.TO", "CMB.TO")]
        _ops, log = synthesize_openings(
            rows, {("CMA.TO", "margin"), ("CMB.TO", "margin")})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            report_missing_history_log([log], {"margin"})
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("lists 2 entries whose rows never go short any more "
                      "(CMA.TO / margin, CMB.TO / margin)", lines[0])

    def test_stale_entry_listed_by_find_missing_history_and_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, files={
                "inputs/margin/start.tt": _CLEAN_TT,
                "missing_history.json": json.dumps(
                    [{"symbol": "ABC.TO", "account": "margin"}])})
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("ATTENTION: missing_history.json lists ABC.TO / "
                          "margin, but its rows never go short", r.stdout)
            f = cli(root, "find-missing-history")
            self.assertIn("STALE in missing_history.json", f.stdout)
            self.assertIn("ABC.TO margin", f.stdout)

    def test_init_readme_says_what_to_download(self):
        from taxjson.lib.config_template import input_readme
        m = input_readme("canada", "margin")
        for want in ("Interactive Brokers", "Activity Statement",
                     "Questrade", "RBC Direct Investing", "Webull",
                     "ALL the history", "positions report", "T5008"):
            self.assertIn(want, m)
        c = input_readme("usa", "crypto")
        self.assertIn("Trades AND Ledgers", c)
        self.assertNotIn("Webull", c)
        r = input_readme("usa", "roth")
        self.assertIn("wash-sale rule", r)
        self.assertTrue(all(len(ln) <= 72 for ln in
                            (m + c + r).splitlines()))

    def test_init_writes_the_per_account_readme(self):
        import argparse
        from unittest import mock
        from taxjson.bin.taxjson_run import cmd_init
        with tempfile.TemporaryDirectory() as td:
            with contextlib.redirect_stdout(io.StringIO()), \
                    mock.patch("taxjson.lib.config_template.system_timezone",
                               return_value="America/Toronto"):
                cmd_init(argparse.Namespace(path=td, dir=".", force=False,
                                            country="canada", year=2025))
            text = (Path(td) / "inputs" / "tfsa" / "README.txt").read_text()
            self.assertIn("superficial-loss rule", text)
            self.assertIn("Activity Statement", text)

    def test_sanity_mismatch_names_missing_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, files={
                "work/margin_gains.json": json.dumps(
                    {"summary": {"year": "2025"}, "transactions": [],
                     "inventory": [{"symbol": "MIS.TO", "qty": 5,
                                    "total_cost": 1.0}]}),
                "h.toml": '[[holding]]\nsymbol = "MIS.TO"\nquantity = 20\n'})
            r = cli(root, "sanity", f"margin={root / 'h.toml'}")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("usually means missing history", r.stdout)
            self.assertIn("taxjson find-missing-history", r.stdout)


if __name__ == "__main__":
    unittest.main()
