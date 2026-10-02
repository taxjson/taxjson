"""Low-round fixes, carryover ledger and filed-year lock (filing-a):
S001-04, S027-18, S027-19, S027-23 (claimed_losses.txt), S027-10 /
S028-00 (per-row rounding of the filed loss), S027-11 / S027-09 (pins),
S028-02 (row currency vs --base-currency); R1-205 / R1-281 (form lines
locked), S031-19 (close-year errors), S031-20 (totals rounded once),
S032-11 (OK says what is not locked). Synthetic data only."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from taxjson.bin import taxjson_filed as F
from taxjson.bin.taxjson_carryover import (build_canada_ledger, load_claimed,
                                           main, render, yearly_nets)
from taxjson.lib import checklist as cl
from tax_rules import rule


def tx(date_, qty, net, price, symbol="XEI.TO", currency="CAD"):
    return {"action": "BUYSELL", "date": date_, "date_settle": date_,
            "time": "09:30:00", "symbol": symbol, "quantity": qty,
            "price": price, "net_amount": net, "currency": currency}


# 2023 loss of 5,000; 2024 gain of 3,000.
BOOK = [tx("2022-05-02", 200, 20000.0, 100.0),
        tx("2023-06-01", -100, 5000.0, 50.0),
        tx("2024-06-03", -100, 13000.0, 130.0)]


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestClaimedFile(unittest.TestCase):

    def _ledger(self, text, *extra, encoding="utf-8"):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({"transactions": BOOK}))
            c = Path(td) / "claimed_losses.txt"
            c.write_bytes(text.encode(encoding))
            return _run([str(b), "--country", "canada", "--claimed", str(c),
                         "--json", *extra])

    def test_bom_line_is_read(self):
        # S027-18: Notepad's BOM is not part of the year.
        rc, out, err = self._ledger("2024 1500.00\n", encoding="utf-8-sig")
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertAlmostEqual(doc["final_carryforward"], 3500.0)
        self.assertNotIn("line ignored", err)
        self.assertNotIn("claimed_ignored", doc)

    def test_implausible_year_is_not_a_phantom_row(self):
        # S027-23: '2205' and '0' are refused by name, not booked.
        rc, out, err = self._ledger("2205 1500\n0 100\n")
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual([r["year"] for r in doc["rows"]], [2023, 2024])
        self.assertIn("2205 is not a plausible tax year", err)
        self.assertEqual(len(doc["claimed_ignored"]), 2)

    def test_ignored_line_reaches_the_checklist(self):
        # S001-04: the carryover step is attention, not 'present'.
        rc, out, err = self._ledger("2024 1,50.00\n")
        doc = json.loads(out)
        self.assertEqual(doc["claimed_ignored"],
                         ["claimed_losses.txt:1: 2024 1,50.00"])

        class Sub:
            def __call__(self, argv, timeout=900):
                return 0, out, err
        with tempfile.TemporaryDirectory() as td:
            ctx = cl.Ctx(root=Path(td), cfg={"settings": {"year": 2024,
                                                          "country": "canada"},
                                             "accounts": {}},
                         year=2024, today=date(2025, 5, 1), run_sub=Sub())
            r = cl.d_carryover(ctx)
        self.assertEqual(r.status, "attention")
        self.assertIn("1 claimed_losses.txt line(s) ignored", r.detail)

    def test_ignored_line_is_in_the_text_report(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({"transactions": BOOK}))
            c = Path(td) / "claimed_losses.txt"
            c.write_text("2024 abc\n")
            rc, out, err = _run([str(b), "--country", "canada",
                                 "--claimed", str(c)])
        self.assertIn("1 claimed line(s) could not be read and are NOT "
                      "applied", out)

    def test_directory_is_a_one_line_error(self):
        # S027-19
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            b.write_text(json.dumps({"transactions": BOOK}))
            rc, out, err = _run([str(b), "--country", "canada",
                                 "--claimed", td])
        self.assertEqual(rc, 2)
        self.assertIn("cannot read claimed file", err)
        self.assertNotIn("Traceback", err)


class TestRowCurrency(unittest.TestCase):
    """S028-02: a native USD book is refused under --base-currency CAD."""

    def test_usd_book_refused(self):
        book = [tx("2024-01-02", 100, 10000.0, 100.0, "XYZ.US", "USD"),
                tx("2025-01-02", -100, 5000.0, 50.0, "XYZ.US", "USD")]
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "raw.json"
            b.write_text(json.dumps({"transactions": book}))
            rc, out, err = _run([str(b), "--country", "canada"])
            self.assertEqual(rc, 2)
            self.assertIn("rows in USD but --base-currency is CAD", err)
            rc, out, err = _run([str(b), "--country", "usa",
                                 "--base-currency", "USD", "--json"])
        self.assertEqual(rc, 0, err)


class TestFiledLossRounding(unittest.TestCase):
    """S027-10 / S028-00: the claim is the per-row-rounded Schedule 3
    loss; the ledger's unrounded net differs by cents."""

    def test_claim_a_few_cents_under_leaves_no_carryforward(self):
        nets = {2024: {"net": -10000.40, "dispositions": 100},
                2025: {"net": 20000.0, "dispositions": 10}}
        led = build_canada_ledger(nets, {2025: 10000.00})
        self.assertEqual(led["final_carryforward"], 0.0)
        self.assertEqual({r["year"]: r["available_to_apply"]
                          for r in led["rows"]}[2025], 0.0)

    def test_claim_a_few_cents_over_is_not_unmatched(self):
        nets = {2024: {"net": -9999.60, "dispositions": 100},
                2025: {"net": 20000.0, "dispositions": 10}}
        led = build_canada_ledger(nets, {2025: 10000.00})
        self.assertEqual(led["final_carryforward"], 0.0)
        self.assertNotIn("unmatched_claims", led)

    def test_a_real_excess_is_still_unmatched(self):
        nets = {2024: {"net": -9999.60, "dispositions": 2},
                2025: {"net": 20000.0, "dispositions": 10}}
        led = build_canada_ledger(nets, {2025: 10000.00})
        self.assertAlmostEqual(led["unmatched_claims"], 0.40)

    def test_a_partial_claim_keeps_its_balance(self):
        nets = {2024: {"net": -10000.40, "dispositions": 100},
                2025: {"net": 20000.0, "dispositions": 10}}
        led = build_canada_ledger(nets, {2025: 6000.00})
        self.assertAlmostEqual(led["final_carryforward"], 4000.40)


class TestNotePins(unittest.TestCase):
    """S027-11: the rendered note amounts."""

    def test_carryback_and_apply_amounts(self):
        nets = {2022: {"net": 10000.0, "dispositions": 1},
                2023: {"net": -4000.0, "dispositions": 1},
                2024: {"net": 2500.0, "dispositions": 1}}
        text = render(build_canada_ledger(nets, {}), "CAD", 2021, False)
        self.assertIn("4,000.00 can be carried back to 2022 via T1A", text)
        self.assertIn("carryforward available: apply up to 2,500.00", text)


class TestAggregatePins(unittest.TestCase):
    """S027-09: the lock's accumulators and the ledger's disposition
    counts on a small book."""

    DOC = {"transactions": [
        {"action": "BUYSELL", "symbol": "A.TO", "qty": 10, "gain": 100.0,
         "proceeds": 1000.0, "cost": 900.0, "date": "2025-02-03",
         "date_settle": "2025-02-04", "disallowed_amount": 0.0},
        {"action": "BUYSELL", "symbol": "B.TO", "qty": 5, "gain": -40.0,
         "proceeds": 460.0, "cost": 500.0, "date": "2025-03-03",
         "date_settle": "2025-03-04", "disallowed_amount": 15.0},
        {"action": "BUYSELL", "symbol": "C.TO", "qty": 1, "gain": 7.5,
         "proceeds": 57.5, "cost": 50.0, "date": "2025-04-01",
         "date_settle": "2025-04-02"},
        {"action": "DIVIDEND", "dividend": 30.0, "pil": 0.0},
        {"action": "DIVIDEND_IN_LIEU", "dividend": 0.0, "pil": 12.0}]}

    def test_aggregates(self):
        a = F.aggregates_from_gains(self.DOC, year=2025)
        self.assertEqual((a["realized"], a["disallowed"], a["proceeds"],
                          a["income"], a["dividend"], a["pil"],
                          a["dispositions"]),
                         (67.5, 15.0, 1517.5, 42.0, 30.0, 12.0, 3))
        self.assertEqual(a["form_lines"], {"13199": 1517.5, "13200": 67.5})

    def test_yearly_nets_dispositions(self):
        nets = yearly_nets({"transactions": self.DOC["transactions"] + [
            {"action": "BUYSELL", "qty": 1, "gain": 5.0,
             "date": "2024-01-02", "date_settle": "2024-01-03"}]}, "settle")
        self.assertEqual({y: v["dispositions"] for y, v in nets.items()},
                         {2024: 1, 2025: 3})
        self.assertAlmostEqual(nets[2025]["net"], 67.5)


class TestLockFormLines(unittest.TestCase):
    """R1-205 / R1-281: moving an amount between return lines (or a
    commission into the price) is drift even with the gain unchanged."""

    def _doc(self, symbol, commission):
        return {"transactions": [
            {"action": "BUYSELL", "symbol": symbol, "qty": 100,
             "gain": 490.0, "proceeds": 1490.0, "cost": 1000.0,
             "commission": commission, "date": "2025-02-03",
             "date_settle": "2025-02-04"}]}

    def test_line_move_and_outlay_move_drift(self):
        filed = F.aggregates_from_gains(self._doc("XEI.TO", 10.0), year=2025)
        self.assertEqual(filed["form_lines"]["13199"], 1500.0)
        snap = {"accounts": {"margin": filed}}
        cur = F.aggregates_from_gains(self._doc("XEI.TO", 0.0), year=2025)
        self.assertEqual(cur["realized"], filed["realized"])
        lines = F.diff_snapshot(snap, {"margin": cur})
        self.assertTrue(any("form line 13199 filed 1,500.00 -> now 1,490.00"
                            in ln for ln in lines), lines)
        self.assertEqual(F.diff_snapshot(snap, {"margin": filed}), [])

    def test_crypto_account_lines(self):
        a = F.aggregates_from_gains(self._doc("BTC", 0.0), crypto=True,
                                    year=2025)
        self.assertNotIn("13199", {k for k, v in a["form_lines"].items()
                                   if v})

    def test_old_lock_without_lines_is_not_drift(self):
        filed = F.aggregates_from_gains(self._doc("XEI.TO", 10.0), year=2025)
        filed.pop("form_lines")
        cur = F.aggregates_from_gains(self._doc("XEI.TO", 0.0), year=2025)
        self.assertEqual(F.diff_snapshot({"accounts": {"margin": filed}},
                                         {"margin": cur}), [])


class TestSnapshotWrite(unittest.TestCase):

    @rule("CA-RPT-08")
    def test_totals_rounded_once(self):
        # S031-20: three accounts each denying 100.0045 -> 300.01.
        raw = {a: {"disallowed": 100.0045, "realized": 0.0}
               for a in ("a", "b", "c")}
        accts = {a: {"disallowed": 100.0, "realized": 0.0,
                     "dispositions": 1} for a in ("a", "b", "c")}
        with tempfile.TemporaryDirectory() as td:
            p = F.write_snapshot(Path(td), 2025, "canada", "wash-adjusted",
                                 accts, force=False, raw=raw)
            doc = json.loads(p.read_text())
        self.assertEqual(doc["totals"]["disallowed"], 300.01)
        self.assertEqual(doc["totals"]["dispositions"], 3)

    def test_filed_is_a_file(self):
        # S031-19: one line, no traceback.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "filed").write_text("x")
            with self.assertRaises(SystemExit) as cm:
                F.write_snapshot(Path(td), 2025, "canada", "pre-wash",
                                 {"a": {"realized": 1.0}}, force=False)
        self.assertIn("cannot write", str(cm.exception))

    def test_list_document_is_a_value_error(self):
        with self.assertRaises(ValueError):
            F.aggregates_from_gains([])

    def test_ok_line_says_what_is_not_locked(self):
        # S032-11
        self.assertIn("interest", F.NOT_LOCKED)
        self.assertIn("FX gain on foreign cash", F.NOT_LOCKED)


if __name__ == "__main__":
    unittest.main()
