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
from tax_rules.dual import cli, settings_for, tx
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
    """Item 1: the closing summary of `tjs run`.

    Formerly: test_fix_newuser_guidance.TestFirstRunSummary"""

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
                          "your files, not in missing_history.tt", tail)
            self.assertIn("SMA.TO (margin)", tail)
            self.assertIn("`tjs find-missing-history`", tail)
            # $0 cost: still held, which the detector used to ignore.
            self.assertIn("1 position at a $0 cost (1 still held): "
                          "ZRO.TO (margin)", tail)
            self.assertIn("1 transfer-in from outside your books kept "
                          "out with no cost: QRS.TO (margin)", tail)
            self.assertIn("`tjs transfers`", tail)
            self.assertIn("no holdings file to check them against: "
                          "margin", tail)
            self.assertIn("paid income in 2025 that the books do not "
                          "hold", tail)
            self.assertIn("DIV.TO (margin)", tail)
            self.assertIn("`tjs checklist --run`", tail)
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
                "inputs/margin/missing_history.tt":
                    "OPENING 2025-01-01 SMA.TO 20 cost=unknown\n"})
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
    """Item 1, the detectors under the summary.

    Formerly: test_fix_newuser_guidance.TestFirstRunLib"""

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

    @rule(R.CA_STKDIV_01)
    def test_canada_stock_dividend_shares_are_zero_cost(self):
        from taxjson.lib.first_run import zero_cost_positions
        rows = [tx("BUYSELL", "2025-01-02", "STK.TO", 100, 1000,
                   currency="CAD"),
                tx("BUYSELL", "2025-03-02", "STK.TO", 5, 0, price=0,
                   currency="CAD", type="stock_dividend")]
        sold, held = zero_cost_positions(rows, 2025, sheltered={},
                                         country="canada")
        self.assertEqual([r.symbol for r in held], ["STK.TO"])

    @rule(R.US_STKDIV_01)
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


if __name__ == "__main__":
    unittest.main()
