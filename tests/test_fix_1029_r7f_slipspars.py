"""Fix round 7 (wave F) of 2026-10-30: regressions of wave E's slip and
reconcile-slips repairs: backlog TJ-1299, TJ-1304, TJ-1305, TJ-1306.
Synthetic data only: made-up tickers (SYN, FND, ZZA, ZZB), issuers and
broker accounts (99900001)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _cra_pdf as CRA
from _qa_project import tj
from tax_rules import rule
from taxjson.bin import taxjson_reconcile_slips as RS
from taxjson.lib import cra_slips as CS
from taxjson.lib import slip_audit as SA
from test_fix_1029_r7c_slips import (
    CFG, QT, _div, _interest, _line, _project)
from test_fix_1029_r7e_slips import (
    CA_HEAD, _cli_project, _existing, _qt, _qt_buy, _qt_div, _slip_tables)

HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None


class BareSlipAgainstLondonPreferred(unittest.TestCase):
    """TJ-1299: a bare slip symbol meets the one London listing of that
    root in the books; a series on a Canadian listing stays a series
    (TJ-1203)."""

    @staticmethod
    def _run(slip_rows, *book_rows):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "gains.json"
            g.write_text(json.dumps({"transactions": [
                {"symbol": sym, "qty": 5, "proceeds": 150, "cost": 50,
                 "gain": 100, "date_settle": "2025-06-03"}
                for sym in book_rows]}), encoding="utf-8")
            s = Path(td) / "slip.csv"
            s.write_text("symbol,quantity,proceeds,cost\n" + slip_rows,
                         encoding="utf-8")
            slip = RS.load_slip(s, None, base_currency="CAD",
                                country="canada")
            return RS.reconcile(slip, RS.load_computed([g], 2025), 1.0)

    @staticmethod
    def _status(rep):
        return {r["symbol"]: r["status"] for r in rep["rows"]}

    def test_bare_slip_matches_the_london_listing(self):
        for spelled in ("SYN.PF", "SYN PF"):
            with self.subTest(spelled=spelled):
                rep = self._run(f"{spelled},5,150,50\n", "SYN.PF.L")
                self.assertEqual(self._status(rep), {"SYN.PF": "OK"})
                self.assertTrue(rep["clean"])

    def test_a_us_project_and_a_short_read_the_same(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "gains.json"
            g.write_text(json.dumps({"transactions": [
                {"symbol": "SYN.PF.L", "qty": 5, "proceeds": -50, "cost": -150,
                 "gain": 100, "direction": "SHORT", "date": "2025-06-03",
                 "date_settle": "2025-06-04"}]}), encoding="utf-8")
            s = Path(td) / "slip.csv"
            s.write_text("symbol,quantity,proceeds,cost\nSYN.PF,5,150,50\n",
                         encoding="utf-8")
            slip = RS.load_slip(s, None, base_currency="USD", country="usa")
            comp = RS.load_computed([g], 2025, "trade", "usa")
            rep = RS.reconcile(slip, comp, 1.0, "usa")
        self.assertEqual(self._status(rep), {"SYN.PF": "OK"})

    def test_figures_are_still_compared(self):
        rep = self._run("SYN.PF,5,900,50\n", "SYN.PF.L")
        self.assertEqual(self._status(rep), {"SYN.PF": "MISMATCH"})

    def test_the_same_spelling_stays_ok(self):
        rep = self._run("SYN.PF.L,5,150,50\n", "SYN.PF.L")
        self.assertEqual(self._status(rep), {"SYN.PF.L": "OK"})

    def test_a_series_on_a_canadian_listing_is_no_london_listing(self):
        rep = self._run("SYN.PR,5,150,50\n", "SYN.PR.L.TO")
        self.assertEqual(self._status(rep),
                         {"SYN.PR": "MISSING_FROM_COMPUTED",
                          "SYN.PR.L.TO": "MISSING_FROM_SLIP"})

    def test_the_slips_other_listing_is_no_match(self):
        rep = self._run("SYN.PF.TO,5,150,50\n", "SYN.PF.L")
        self.assertFalse(rep["clean"])
        self.assertNotEqual(self._status(rep).get("SYN.PF.TO"), "OK")

    def test_two_candidates_are_not_guessed(self):
        rep = self._run("SYN.PR,10,300,100\n", "SYN.PR.L", "SYN.PR.V")
        self.assertFalse(rep["clean"])
        self.assertNotIn("OK", self._status(rep).values())

    def test_a_row_naming_it_keeps_it(self):
        rep = self._run("SYN.PF,5,150,50\nSYN.PF.L,5,150,50\n", "SYN.PF.L")
        st = self._status(rep)
        self.assertEqual(st["SYN.PF.L"], "OK")
        self.assertEqual(st["SYN.PF"], "MISSING_FROM_COMPUTED")

    def test_end_to_end_through_the_generic_importer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            acct = root / "inputs" / "margin"
            acct.mkdir(parents=True)
            (acct / "generic.csv").write_text(
                "Date,Action,Symbol,Qty,Price,Amount\n"
                "2025-01-02,B,SYN.PF,10,10,-100\n"
                "2025-06-02,S,SYN.PF,-5,30,150\n", encoding="utf-8")
            (acct / "generic.csv.toml").write_text(
                '[columns]\ndate = "Date"\naction = "Action"\n'
                'symbol = "Symbol"\nquantity = "Qty"\nprice = "Price"\n'
                'amount = "Amount"\n[defaults]\ncurrency = "GBP"\n'
                '[actions]\nB = "buy"\nS = "sell"\n', encoding="utf-8")
            (root / "taxjson.toml").write_text(
                CA_HEAD.replace('["USD"]', '["USD", "GBP"]')
                + '[accounts.margin]\ntype = "taxable"\n', encoding="utf-8")
            tj(root, "run", "--no-input")
            gains = json.loads((root / "work" / "margin_gains.json")
                               .read_text(encoding="utf-8"))
            row = gains["transactions"][0]
            self.assertEqual(row["symbol"], "SYN.PF.L")
            slip = root / "slip.csv"
            slip.write_text("symbol,quantity,proceeds,cost\n"
                            f"SYN.PF,5,{row['proceeds']},{row['cost']}\n",
                            encoding="utf-8")
            r = tj(root, "reconcile-slips", str(slip), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class AmendedSlipOfAnotherBrokerAccount(unittest.TestCase):
    """TJ-1304: a slip of no key is no twin of a keyed table it shares no
    box with: another broker account's slip, never moved under its key.
    TJ-1208's cases (same figures; a key an older release guessed) stay."""

    @staticmethod
    def _pl(boxes, status="original", shown="b.pdf"):
        return CS.Placement(CS.CraSlip(shown, 2025, "T5", status, QT,
                                       boxes=boxes), account="margin")

    @rule("CA-SLIP-04")
    def test_amended_of_other_boxes_is_added_without_a_key(self):
        pl = self._pl({"13": 900.0}, "amended")
        plan = CS.plan_import([(pl, "")], [_existing()])
        self.assertEqual(plan.replace, [])
        self.assertEqual(plan.add, [(pl, "")])

    @rule("CA-SLIP-04")
    def test_original_beside_an_amended_table_of_other_boxes_is_added(self):
        pl = self._pl({"13": 900.0})
        plan = CS.plan_import([(pl, "")], [_existing(status="amended")])
        self.assertEqual(plan.add, [(pl, "")])
        self.assertEqual(plan.notes, [])

    @rule("CA-SLIP-04")
    def test_a_box_held_at_nil_is_not_shared(self):
        pl = self._pl({"13": 900.0}, "amended")
        plan = CS.plan_import(
            [(pl, "")], [_existing(boxes={"24": 100.0, "13": 0.0})])
        self.assertEqual(plan.replace, [])

    @rule("CA-SLIP-04")
    def test_a_shared_box_still_replaces_and_keeps_the_key(self):
        plan = CS.plan_import(
            [(self._pl({"24": 90.0, "13": 5.0}, "amended"), "")],
            [_existing()])
        self.assertEqual(plan.add, [])
        self.assertEqual(plan.replace[0][2], "k" * 10)

    @rule("CA-SLIP-04")
    def test_a_shared_box_still_refuses_the_original(self):
        plan = CS.plan_import([(self._pl({"24": 90.0}), "")],
                              [_existing(status="amended")])
        self.assertEqual(plan.add, [])
        self.assertIn("holds its amended slip", plan.notes[0])

    @rule("CA-SLIP-04")
    def test_a_table_of_no_key_is_still_replaced(self):
        plan = CS.plan_import([(self._pl({"13": 900.0}, "amended"), "")],
                              [_existing(keys=())])
        self.assertEqual(plan.add, [])
        self.assertEqual(plan.replace[0][2], "")

    @rule("CA-SLIP-04")
    def test_a_keyed_slip_compares_its_key_as_before(self):
        pl = self._pl({"24": 90.0}, "amended")
        plan = CS.plan_import([(pl, "m" * 10)], [_existing()])
        self.assertEqual(plan.add, [(pl, "m" * 10)])
        plan = CS.plan_import([(pl, "k" * 10)], [
            _existing(boxes={"13": 5.0})])
        self.assertEqual(plan.replace[0][2], "k" * 10)

    @rule("CA-SLIP-04")
    @unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext is not installed")
    def test_the_command_the_message_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = _cli_project(td, {
                "margin/a.csv": _qt("99900001", _qt_buy("ZZA.TO"),
                                    _qt_div("ZZA.TO", 100.0)),
                "margin/b.csv": _qt("99900002", _qt_buy("ZZB.TO"),
                                    _qt_div("ZZB.TO", 200.0))})
            a = CRA.t5(Path(td) / "a.pdf", ["QUESTRADE, INC."],
                       {"24": "100.00"})
            b = CRA.t5(Path(td) / "b.pdf", ["QUESTRADE, INC."],
                       {"13": "900.00"}, status="amended")
            tj(root, "slip-audit", "--import-cra", str(a), "--write")
            sf = root / "inputs" / "slips" / "slips.toml"
            self.assertIn("broker_key", sf.read_text(encoding="utf-8"))
            r = tj(root, "slip-audit", "margin", "--import-cra", str(b),
                   "--write")
            self.assertNotIn("replaced by", r.stdout)
            self.assertEqual(len(_slip_tables(root)), 2)
            self.assertEqual(
                sf.read_text(encoding="utf-8").count("broker_key"), 1)
            # Read twice, it is one slip.
            r = tj(root, "slip-audit", "margin", "--import-cra", str(b),
                   "--write")
            self.assertIn("already in slips.toml", r.stdout)
            self.assertEqual(len(_slip_tables(root)), 2)


class UnkeyedTrustOfTwoPayers(unittest.TestCase):
    """TJ-1305: a T3 of no named fund comes off a payer's income only as
    far as the other payers' income cannot hold it."""

    T3 = ('year = 2025\n[[slip]]\ntype = "T3"\naccount = "margin"\n'
          'issuer = "Synthetic Fund"\nboxes = { 49 = 100 }\n')

    def _interest_status(self, rows, slips=None):
        rep = SA.audit(_project(rows, slips or self.T3), CFG)
        return _line(rep, "interest")["status"], rep

    @rule("CA-SLIP-01")
    def test_a_second_payers_eighty_differs(self):
        st, rep = self._interest_status([
            _div("FND.TO", "2025-06-02", 100.0, source="x.csv"),
            _div("SYN.TO", "2025-06-02", 40.0, source="y.csv"),
            _interest("2025-06-02", 40.0, source="y.csv")])
        self.assertEqual(st, "differs")
        self.assertTrue(any("interest" in str(i) for i in rep["issues"]),
                        rep["issues"])

    @rule("CA-SLIP-01")
    def test_two_broker_accounts_the_same(self):
        st, _rep = self._interest_status([
            _div("FND.TO", "2025-06-02", 100.0, source="x.csv",
                 source_account="a" * 10),
            _div("SYN.TO", "2025-06-02", 40.0, source="y.csv",
                 source_account="b" * 10),
            _interest("2025-06-02", 40.0, source="y.csv",
                      source_account="b" * 10)])
        self.assertEqual(st, "differs")

    @rule("CA-SLIP-01")
    def test_naming_the_fund_gives_the_same_verdict(self):
        st, _rep = self._interest_status([
            _div("FND.TO", "2025-06-02", 100.0, source="x.csv"),
            _div("SYN.TO", "2025-06-02", 40.0, source="y.csv"),
            _interest("2025-06-02", 40.0, source="y.csv")],
            self.T3.replace("boxes", 'security = "FND.TO"\nboxes'))
        self.assertEqual(st, "differs")

    @rule("CA-SLIP-01")
    def test_interest_alone_at_the_second_payer_stays_excused(self):
        st, rep = self._interest_status([
            _div("FND.TO", "2025-06-02", 100.0, source="x.csv"),
            _interest("2025-06-02", 40.0, source="y.csv")])
        self.assertEqual(st, "under-50")
        self.assertEqual(rep["issues"], [])

    @rule("CA-SLIP-01")
    def test_the_trust_paid_by_the_interest_payer_stays_excused(self):
        # No other payer's income can hold the T3: it is this payer's.
        st, _rep = self._interest_status([
            _div("FND.TO", "2025-06-02", 100.0, source="y.csv"),
            _interest("2025-06-02", 40.0, source="y.csv"),
            _interest("2025-06-02", 5.0, source="x.csv")])
        self.assertEqual(st, "under-50")

    @rule("CA-SLIP-01")
    def test_the_rest_of_the_trust_comes_off_the_payer(self):
        # x can hold 30 of the T3's 100; 70 comes off y's 100: 30 + 40.
        st, _rep = self._interest_status([
            _div("FND.TO", "2025-06-02", 30.0, source="x.csv"),
            _div("SYN.TO", "2025-06-02", 100.0, source="y.csv"),
            _interest("2025-06-02", 40.0, source="y.csv")])
        self.assertEqual(st, "differs")

    @rule("CA-SLIP-01")
    def test_one_payer_is_as_before(self):
        st, _rep = self._interest_status([
            _div("FND.TO", "2025-06-02", 100.0),
            _interest("2025-06-02", 40.0)])
        self.assertEqual(st, "under-50")


class Box18OfRowsOfNoListing(unittest.TestCase):
    """TJ-1306: slips of two markets' listings draft one entry per symbol
    and year, whatever the rows they meet."""

    @staticmethod
    def _slips(*pairs):
        return "year = 2025\n" + "".join(
            f'[[slip]]\ntype = "T5"\naccount = "margin"\nissuer = "P{i}"\n'
            f'currency = "CAD"\nsecurity = "{sym}"\n'
            f'boxes = {{ 24 = 80, 18 = {amt} }}\n'
            for i, (sym, amt) in enumerate(pairs))

    def _draft(self, rows, *pairs):
        rep = SA.audit(_project(rows, self._slips(*pairs)), CFG)
        return sorted((c["symbol"], c["amount"]) for c in
                      rep["suggestions"]["capital_gains_dividends"])

    @rule("CA-SLIP-03")
    def test_bare_rows_draft_one_entry(self):
        got = self._draft([_div("SYN", "2025-06-02", 200.0)],
                          ("SYN.TO", 20), ("SYN.US", 5))
        self.assertEqual(got, [("SYN", 25.0)])

    @rule("CA-SLIP-03")
    def test_one_row_met_by_two_exchanges_drafts_one_entry(self):
        got = self._draft([_div("SYN.TO", "2025-06-02", 100.0),
                           _div("SYN.US", "2025-06-03", 100.0)],
                          ("SYN.TO", 20), ("SYN.V", 3), ("SYN.US", 5))
        self.assertEqual(got, [("SYN.TO", 23.0), ("SYN.US", 5.0)])

    @rule("CA-SLIP-03")
    def test_each_listings_rows_stay_apart(self):
        got = self._draft([_div("SYN.TO", "2025-06-02", 100.0),
                           _div("SYN.US", "2025-06-03", 100.0)],
                          ("SYN.TO", 20), ("SYN.US", 5))
        self.assertEqual(got, [("SYN.TO", 20.0), ("SYN.US", 5.0)])

    @rule("CA-SLIP-03")
    def test_a_bare_row_beside_a_listed_one_stays_apart(self):
        got = self._draft([_div("SYN.TO", "2025-06-02", 100.0),
                           _div("SYN", "2025-06-03", 100.0)],
                          ("SYN.TO", 20), ("SYN.US", 5))
        self.assertEqual(got, [("SYN", 5.0), ("SYN.TO", 20.0)])

    @rule("CA-SLIP-03")
    def test_the_draft_applies(self):
        tt = ("BUYSELL 2025-01-06 10:00:00 SYN 100 CAD 10 1000 0\n"
              "DIVIDEND 2025-06-02 09:30:00 SYN 100 CAD 2 200.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            for rel, text in (
                    ("margin/m.tt", tt),
                    ("slips/slips.toml",
                     self._slips(("SYN.TO", 20), ("SYN.US", 5)))):
                f = root / "inputs" / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
            (root / "taxjson.toml").write_text(
                CA_HEAD + '[accounts.margin]\ntype = "taxable"\n',
                encoding="utf-8")
            tj(root, "run", "--no-input")
            rep = json.loads(tj(root, "slip-audit", "--json",
                                check=False).stdout)
            cgd = rep["suggestions"]["capital_gains_dividends"]
            self.assertEqual([(c["symbol"], c["amount"]) for c in cgd],
                             [("SYN", 25.0)])
            with (root / "taxjson.toml").open("a", encoding="utf-8") as f:
                f.write("".join(c["toml"] + "\n" for c in cgd))
            tj(root, "run", "--no-input")
            rep = json.loads(tj(root, "slip-audit", "--json",
                                check=False).stdout)
            self.assertEqual(rep["suggestions"]["capital_gains_dividends"],
                             [])


if __name__ == "__main__":
    unittest.main()
