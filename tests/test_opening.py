"""Opening balances (OPENING rows): a position and its cost from a broker's
positions report, which is NOT a purchase (tax-logic CA-OPEN-01..03,
US-OPEN-01..03). Synthetic data only."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx

from taxjson.bin.taxjson_convert_tt import parse_tt_line, tx_to_tt_line
from taxjson.lib.core import OPENING_TYPE, TaxTransaction, is_opening_row
from taxjson.lib.opening import (ATTENTION_OPENING, OpeningError,
                                 apply_opening_cutoff)

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def opening(day, sym, qty, total, *, lot="", account="margin",
            currency="USD"):
    """The row a .tt OPENING line becomes."""
    line = f"OPENING {day} {sym} {qty} {currency} {total}" + (
        f" {lot}" if lot else "")
    return TaxTransaction(**parse_tt_line(line, account))


def _sale_gains(res):
    return [g for g in res["transactions"] if g.get("qty")]


class TestTtLine(unittest.TestCase):
    def test_parse_fields(self):
        t = parse_tt_line("OPENING 2024-12-31 SAMPB.TO 20 CAD 204.95",
                          "margin")
        self.assertEqual(t["action"], "BUYSELL")
        self.assertEqual(t["type"], OPENING_TYPE)
        self.assertEqual((t["date"], t["date_settle"], t["time"]),
                         ("2024-12-31", "2024-12-31", "00:00:00"))
        self.assertEqual((t["quantity"], t["net_amount"]), (20.0, 204.95))
        self.assertAlmostEqual(t["price"], 10.2475)
        self.assertNotIn("lot_date", t)
        self.assertTrue(is_opening_row(t))

    def test_round_trip_and_lot_date_in_id(self):
        a = parse_tt_line("OPENING 2024-12-31 SAMPU.US 10 USD 1000 "
                          "2019-03-04", "margin")
        b = parse_tt_line("OPENING 2024-12-31 SAMPU.US 10 USD 1000 "
                          "2020-03-04", "margin")
        self.assertEqual(a["lot_date"], "2019-03-04")
        self.assertNotEqual(a["id"], b["id"])
        line = tx_to_tt_line(a)
        self.assertEqual(line, "OPENING 2024-12-31 SAMPU.US 10.00000000 "
                               "USD 1000.00000 2019-03-04")
        self.assertEqual(parse_tt_line(line, "margin")["id"], a["id"])
        # The engine row keeps the lot date (an evidence field).
        row = TaxTransaction(**a).to_dict()
        self.assertEqual(row["lot_date"], "2019-03-04")

    def test_option_price_per_share(self):
        t = parse_tt_line("OPENING 2024-12-31 SAMPU250117C00010000.US 2 "
                          "USD 300", "margin")
        self.assertAlmostEqual(t["price"], 1.5)

    def test_refusals(self):
        bad = {
            "OPENING 2024-12-31 SAMPU.US -10 USD 1000": "must be positive",
            "OPENING 2024-12-31 SAMPU.US 10 USD -5": "negative",
            "OPENING 2024-12-31 SAMPU.US 10 USD 1000 2025-01-02":
                "after the snapshot",
            "OPENING 2024-12-31 SAMPU.US 10 USD": "malformed OPENING",
            "OPENING 2024-12-31 09:30:00 SAMPU.US 10 USD 1000":
                "no time column",
            "OPENING 2024-13-31 SAMPU.US 10 USD 1000": "not a valid",
            "OPENING 2024-12-31 F:SAMPCL 1 USD 1000": "futures",
        }
        for line, msg in bad.items():
            with self.assertRaises(ValueError, msg=line) as cm:
                parse_tt_line(line, "margin", source="x.tt:1")
            self.assertIn(msg, str(cm.exception), line)


class TestNotAPurchase(unittest.TestCase):
    """The false superficial-loss / wash-sale denial the new-user study
    reproduced: an opening balance written as a BUYSELL on the
    statement day is a purchase inside the window of a loss sold days
    later; the OPENING line is not."""

    def _book(self, as_opening):
        first = (opening("2025-01-31", "SAMPX.US", 200, 2000,
                         lot="2020-05-01") if as_opening else
                 tx("BUYSELL", "2025-01-31", "SAMPX.US", 200, 2000))
        return [first, tx("BUYSELL", "2025-02-10", "SAMPX.US", -100, 800)]

    @rule("CA-OPEN-01")
    @rule("US-OPEN-01")
    def test_opening_never_replaces_a_loss(self):
        bought = gains_both(self._book(False), year=2025)
        opened = gains_both(self._book(True), year=2025)
        for c in ("canada", "usa"):
            # The control: a purchase on the snapshot day IS a
            # replacement (100 still held at day 30).
            self.assertAlmostEqual(
                bought[c]["summary"]["total_disallowed"], 200.0, places=2)
            self.assertAlmostEqual(
                opened[c]["summary"]["total_disallowed"], 0.0, places=2)
            g = _sale_gains(opened[c])[0]
            self.assertAlmostEqual(g["gain"], -200.0, places=2)
            # No "recent acquisition" either: the snapshot day is not a
            # purchase date for the planning views.
            inv = {i["symbol"]: i for i in opened[c]["inventory"]}
            self.assertNotEqual(inv["SAMPX.US"].get("last_acq_date"),
                                "2025-01-31")

    @rule("CA-OPEN-01")
    @rule_absent("CA-OPEN-01", country="usa")
    @rule("US-OPEN-01")
    def test_canada_pools_the_opening_us_keeps_its_lot(self):
        book = [opening("2025-01-31", "SAMPX.US", 10, 1000,
                        lot="2020-05-01"),
                tx("BUYSELL", "2025-03-03", "SAMPX.US", 10, 2000),
                tx("BUYSELL", "2025-04-01", "SAMPX.US", -10, 1700)]
        r = gains_both(book, year=2025)
        ca = _sale_gains(r["canada"])[0]
        us = _sale_gains(r["usa"])[0]
        # Canada: s.47 average cost of the opening and the purchase.
        self.assertAlmostEqual(ca["gain"], 1700 - 1500, places=2)
        # USA: FIFO sells the opening lot, held since its own date.
        self.assertAlmostEqual(us["gain"], 1700 - 1000, places=2)
        self.assertEqual(us["term"], "LONG_TERM")
        self.assertEqual(us["acquired_date"], "2020-05-01")
        self.assertEqual(ca["days_held"] > 365, False)  # last purchase

    @rule("US-OPEN-01")
    @rule_absent("US-OPEN-01", country="canada")
    @rule("CA-OPEN-01")
    def test_us_lot_needs_its_date_canada_does_not(self):
        book = [opening("2025-01-31", "SAMPX.US", 10, 1000),
                tx("BUYSELL", "2025-04-01", "SAMPX.US", -10, 1700)]
        from taxjson.lib.pipeline import GainsRequest, run_gains
        ca = run_gains(list(book), [], [],
                       req=GainsRequest(country="canada", taxable=True,
                                        year=2025))
        self.assertAlmostEqual(_sale_gains(ca)[0]["gain"], 700.0, places=2)
        with self.assertRaises(ValueError) as cm:
            run_gains(list(book), [], [],
                      req=GainsRequest(country="usa", taxable=True,
                                       year=2025))
        self.assertIn("no lot date", str(cm.exception))

    @rule("CA-OPEN-01")
    @rule("US-OPEN-01")
    def test_a_lot_bought_inside_the_window_is_flagged_not_denied(self):
        book = [opening("2025-01-31", "SAMPX.US", 100, 2000,
                        lot="2020-05-01"),
                opening("2025-01-31", "SAMPX.US", 100, 2000,
                        lot="2025-01-20"),
                tx("BUYSELL", "2025-02-10", "SAMPX.US", -100, 800)]
        r = gains_both(book, year=2025)
        for c in ("canada", "usa"):
            self.assertAlmostEqual(r[c]["summary"]["total_disallowed"],
                                   0.0, places=2)
            flags = [w for w in r[c]["option_replacement_warnings"]
                     if w["rule"] == "opening_lot_vs_loss"]
            self.assertEqual(len(flags), 1, c)
            self.assertEqual(flags[0]["option_acquired"], "2025-01-20")
            self.assertIn("review it by hand", r[c]["_stderr"])

    @rule("CA-OPEN-01")
    def test_an_opening_call_does_not_replace_the_shares(self):
        # A long call held at the snapshot is not a call bought in the
        # window (CA-SL-05 would deny the share loss).
        book = [tx("BUYSELL", "2024-06-03", "SAMPX.TO", 100, 2000,
                   currency="CAD"),
                opening("2025-01-31", "SAMPX250620C00020000.TO", 1, 150,
                        currency="CAD"),
                tx("BUYSELL", "2025-02-10", "SAMPX.TO", -100, 1500,
                   currency="CAD")]
        from taxjson.lib.pipeline import GainsRequest, run_gains
        res = run_gains(book, [], [], req=GainsRequest(
            country="canada", taxable=True, year=2025))
        self.assertAlmostEqual(res["summary"]["total_disallowed"], 0.0,
                               places=2)


class TestCutoff(unittest.TestCase):
    def _rows(self):
        return [
            tx("BUYSELL", "2023-03-01", "SAMPB.TO", 10, 100, currency="CAD"),
            tx("BUYSELL", "2023-06-01", "SAMPB.TO", -5, 70, currency="CAD"),
            tx("DIVIDEND", "2024-06-30", "SAMPB.TO", 0, 3, currency="CAD"),
            tx("BUYSELL", "2023-03-01", "SAMPC.TO", 10, 100, currency="CAD"),
            tx("BUYSELL", "2023-03-01", "SAMPB.TO", 4, 40, currency="CAD",
               account="tfsa"),
            opening("2024-12-31", "SAMPB.TO", 5, 50, currency="CAD"),
            tx("BUYSELL", "2025-02-03", "SAMPB.TO", 1, 12, currency="CAD"),
        ]

    def _apply(self, rows, **kw):
        import io
        err = io.StringIO()
        out = apply_opening_cutoff(rows, report=err, **kw)
        return out, err.getvalue()

    @rule("CA-OPEN-03")
    def test_earlier_rows_of_snapshot_symbols_left_out(self):
        rows = self._rows()
        out, err = self._apply(rows, year=2025, country="canada")
        kept = [(t.action, t.date, t.symbol, t.account) for t in out]
        # SAMPB.TO trades before the snapshot in margin: out.
        self.assertNotIn(("BUYSELL", "2023-03-01", "SAMPB.TO", "margin"),
                         kept)
        self.assertNotIn(("BUYSELL", "2023-06-01", "SAMPB.TO", "margin"),
                         kept)
        # Income, another symbol, another account, later rows: kept.
        for k in [("DIVIDEND", "2024-06-30", "SAMPB.TO", "margin"),
                  ("BUYSELL", "2023-03-01", "SAMPC.TO", "margin"),
                  ("BUYSELL", "2023-03-01", "SAMPB.TO", "tfsa"),
                  ("BUYSELL", "2025-02-03", "SAMPB.TO", "margin"),
                  ("BUYSELL", "2024-12-31", "SAMPB.TO", "margin")]:
            self.assertIn(k, kept)
        self.assertIn(ATTENTION_OPENING + "margin", err)
        self.assertIn("2 earlier row(s) of SAMPB.TO", err)

    @rule("US-OPEN-03")
    def test_us_cutoff_is_the_same(self):
        rows = [r for r in self._rows() if not is_opening_row(r)] + [
            opening("2024-12-31", "SAMPB.TO", 5, 50, lot="2023-03-01",
                    currency="USD")]
        out, _err = self._apply(rows, year=2025, country="usa",
                                base_currency="USD")
        self.assertEqual(len(out), len(rows) - 2)

    @rule("CA-OPEN-03")
    def test_rename_into_the_symbol_is_left_out_too(self):
        rows = [tx("BUYSELL", "2023-03-01", "SAMPOLD.TO", 10, 100,
                   currency="CAD"),
                tx("SPLIT", "2024-02-01", "SAMPOLD.TO", 1, 0, currency="",
                   symbol_new="SAMPB.TO"),
                opening("2024-12-31", "SAMPB.TO", 10, 100, currency="CAD")]
        out, _err = self._apply(rows, year=2025)
        self.assertEqual([t.action for t in out], ["BUYSELL"])
        self.assertTrue(is_opening_row(out[0]))

    @rule("CA-OPEN-03")
    def test_sale_of_the_tax_year_refused(self):
        rows = [tx("BUYSELL", "2025-01-10", "SAMPB.TO", 10, 100,
                   currency="CAD"),
                tx("BUYSELL", "2025-02-10", "SAMPB.TO", -5, 70,
                   currency="CAD"),
                opening("2025-03-31", "SAMPB.TO", 5, 50, currency="CAD")]
        with self.assertRaises(OpeningError) as cm:
            self._apply(rows, year=2025)
        self.assertIn("1 sale(s) of the 2025 tax year", str(cm.exception))
        # A sale of an earlier year is history the snapshot replaces.
        out, _ = self._apply(rows, year=2026)
        self.assertEqual(len(out), 1)

    @rule("CA-OPEN-03")
    def test_two_snapshot_dates_for_one_symbol_refused(self):
        rows = [opening("2024-12-31", "SAMPB.TO", 5, 50, currency="CAD"),
                opening("2025-06-30", "SAMPB.TO", 5, 50, currency="CAD")]
        with self.assertRaises(OpeningError) as cm:
            self._apply(rows)
        self.assertIn("one snapshot date per symbol", str(cm.exception))

    @rule("US-OPEN-01")
    def test_us_opening_without_lot_date_refused_before_the_engine(self):
        with self.assertRaises(OpeningError) as cm:
            self._apply([opening("2024-12-31", "SAMPB.US", 5, 50)],
                        country="usa", base_currency="USD")
        self.assertIn("no lot date", str(cm.exception))

    def test_no_opening_rows_is_a_no_op(self):
        rows = [r for r in self._rows() if not is_opening_row(r)]
        out, err = self._apply(rows, year=2025)
        self.assertIs(out, rows)
        self.assertEqual(err, "")


def _merge2(rows, *args):
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.json"
        src.write_text(json.dumps({"transactions": [
            r.to_dict() if hasattr(r, "to_dict") else r for r in rows]}))
        rates = Path(td) / "to_base.csv"
        rates.write_text("2019-03-04 00:00:00 USD CAD 1.33\n"
                         "2024-12-31 00:00:00 USD CAD 1.44\n"
                         "2024-12-31 00:00:00 CAD USD 0.70\n")
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_merge2", "--sort",
             "--dedup", "--rates", str(rates), *args, str(src)],
            capture_output=True, text=True,
            env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin",
                 "TAXJSON_OFFLINE": "1"})
        return r


class TestCurrency(unittest.TestCase):
    @rule("CA-OPEN-02")
    @rule_absent("CA-OPEN-02", country="usa")
    @rule("US-OPEN-02")
    def test_foreign_cost_at_the_snapshot_rate_in_canada_only(self):
        row = opening("2024-12-31", "SAMPU.US", 10, 1000,
                      lot="2019-03-04")
        r = _merge2([row], "--to", "CAD", "--country", "canada")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["transactions"][0]
        # The snapshot day's rate (1.44), not the lot date's (1.33).
        self.assertAlmostEqual(out["net_amount"], 1440.0, places=2)
        self.assertEqual(out["currency"], "CAD")
        # USA: a lot in another currency than USD stops the books.
        cad = opening("2024-12-31", "SAMPU.TO", 10, 1000,
                      lot="2019-03-04", currency="CAD")
        r = _merge2([cad], "--to", "USD", "--country", "usa")
        self.assertEqual(r.returncode, 1)
        self.assertIn("US-OPEN-02", r.stderr)


if __name__ == "__main__":
    unittest.main()
