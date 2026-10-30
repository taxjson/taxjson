"""Fix round 7 (wave E) of 2026-10-30, slip-audit, the CRA slip importer
and reconcile-slips: backlog TJ-1181, TJ-1208 to TJ-1216, TJ-1252,
TJ-1253. Synthetic data only: made-up tickers (SYN, FND, ZZA, ZZQ),
issuers and broker accounts (99900001)."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _cra_pdf as CRA
from _qa_project import tj
from tax_rules import rule
from taxjson.bin.taxjson_reconcile_slips import load_computed, load_slip
from taxjson.lib import cra_slips as CS
from taxjson.lib import slip_audit as SA
from test_fix_1029_r7c_slips import (
    CFG, QT, _brow, _cra_t5, _div, _interest, _line, _project, _t5)

HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None
CA_HEAD = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
           'country = "canada"\nbase_currency = "CAD"\n'
           'source_currencies = ["USD"]\n')
QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")


def _qt(acct, *rows):
    return QT_HEAD + "".join(f"{r},{acct},Margin\n" for r in rows)


def _qt_buy(sym, day="2025-01-06"):
    return (f"{day},{day},Buy,{sym},{sym} CORP,10,10.00,-100.00,0,-100.00,"
            f"CAD,Trades")


def _qt_div(sym, amt, day="2025-03-14"):
    return (f"{day},{day},DIV,{sym},{sym} CASH DIV ON 10 SHS REC 03/01/25 "
            f"PAY 03/14/25,0,0,{amt:.2f},0,{amt:.2f},CAD,Dividends")


def _cli_project(td, files):
    root = Path(td) / "p"
    for rel, text in files.items():
        p = root / "inputs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / "taxjson.toml").write_text(
        CA_HEAD + '[accounts.margin]\ntype = "taxable"\n', encoding="utf-8")
    tj(root, "run", "--no-input")
    return root


def _slip_tables(root):
    p = root / "inputs" / "slips" / "slips.toml"
    if not p.is_file():
        return []
    return [ln for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() == "[[slip]]"]


def _groups(a_rows, b_rows):
    return [CS.Group("margin", ("a" * 10,), "questrade", ("a.csv",), a_rows),
            CS.Group("margin", ("b" * 10,), "questrade", ("b.csv",), b_rows)]


def _place(boxes, groups, account=None):
    (pl,) = CS.place([CS.CraSlip("t5.pdf", 2025, "T5", "original", QT,
                                 boxes=boxes)], groups, 2025, {},
                     account=account)
    return pl


def _existing(index=1, status="original", boxes=None, keys=("k" * 10,),
              issuer=QT, source="", imported=True):
    return CS.Existing(index, "T5", "margin", "", issuer, status, "CAD",
                       boxes if boxes is not None else {"24": 100.0},
                       frozenset(keys), imported, source=source)


class ReconcileNilCloseWithCost(unittest.TestCase):
    """TJ-1181: a slip with a cost column against a contract whose only
    trace this year is the nil close of an earlier year's write."""

    OPT = "ZZQ250620C00010000.TO"
    TT = (f"BUYSELL 2024-11-04 10:00:00 {OPT} -1 CAD 0.1 10 0\n"
          f"BUYSELL 2025-06-20 16:00:00 {OPT} 1 CAD 0 0 0\n")

    def _reconcile(self, slip):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "t.tt").write_text(
                self.TT, encoding="utf-8")
            (root / "taxjson.toml").write_text(
                CA_HEAD + "option_grant_timing_since = 2024\n"
                '[accounts.margin]\ntype = "taxable"\n', encoding="utf-8")
            tj(root, "run", "--no-input")
            (root / "slip.csv").write_text(slip, encoding="utf-8")
            r = tj(root, "reconcile-slips", str(root / "slip.csv"), "--details",
                   check=False)
        return r

    def test_cost_column_reconciles_with_the_premium_note(self):
        r = self._reconcile("symbol,quantity,proceeds,cost,currency\n"
                            f"{self.OPT},1,10,0,CAD\n")
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("slip proceeds include 10.00 premium", r.stdout)

    def test_bare_symbol_and_a_wrong_cost(self):
        r = self._reconcile("symbol,quantity,proceeds,cost,currency\n"
                            f"{self.OPT.split('.')[0]},1,10,5,CAD\n")
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("slip cost differs by +5.00", r.stdout)

    def test_a_listing_records_a_cost_of_nil(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [], "grant_nil_closes": [
                {"date": "2025-06-20", "symbol": self.OPT, "account": "m",
                 "grant_closed": {"2024": {"units": 1.0, "premium": 10.0}}}]}),
                encoding="utf-8")
            rec = load_computed([g], 2025)["ZZQ250620C00010000"]
        self.assertEqual(rec["listings"]["TO"]["cost"], 0.0)


class ReimportNamingTheAccount(unittest.TestCase):
    """TJ-1208: a slip already in slips.toml under a key, imported again
    naming its account (no broker account known), is the same slip."""

    def _pl(self, boxes=None, status="original", shown="t5.pdf"):
        s = CS.CraSlip(shown, 2025, "T5", status, QT,
                       boxes=boxes or {"24": 100.0})
        return CS.Placement(s, account="margin")

    @rule("CA-SLIP-04")
    def test_keyed_table_is_the_same_slip(self):
        plan = CS.plan_import([(self._pl(), "")], [_existing()])
        self.assertEqual(plan.add, [])
        self.assertIn("already in slips.toml", plan.notes[0])

    @rule("CA-SLIP-04")
    def test_a_placed_slip_still_compares_its_key(self):
        pl = self._pl()
        plan = CS.plan_import([(pl, "m" * 10)], [_existing()])
        self.assertEqual([p for p, _k in plan.add], [pl])

    @rule("CA-SLIP-04")
    def test_other_figures_and_other_issuer_are_added(self):
        plan = CS.plan_import([(self._pl({"24": 90.0}), "")], [_existing()])
        self.assertEqual(len(plan.add), 1)
        other = _existing(issuer="ANOTHER BROKER")
        plan = CS.plan_import([(self._pl(), "")], [other])
        self.assertEqual(len(plan.add), 1)

    @rule("CA-SLIP-04")
    def test_each_table_stands_for_one_slip(self):
        plan = CS.plan_import([(self._pl(), ""), (self._pl(shown="b.pdf"), "")],
                              [_existing()])
        self.assertEqual([p.slip.shown for p, _k in plan.add], ["b.pdf"])

    @rule("CA-SLIP-04")
    def test_amended_slip_replaces_the_keyed_original_keeping_its_key(self):
        plan = CS.plan_import([(self._pl({"24": 90.0}, "amended"), "")],
                              [_existing()])
        self.assertEqual(plan.add, [])
        self.assertEqual(plan.replace[0][2], "k" * 10)

    @rule("CA-SLIP-04")
    def test_amended_slip_with_two_keyed_originals_is_listed(self):
        two = [_existing(1), _existing(2, keys=("m" * 10,))]
        plan = CS.plan_import([(self._pl({"24": 90.0}, "amended"), "")], two)
        self.assertEqual((plan.add, plan.replace), ([], []))
        self.assertIn("several", plan.notes[0])

    @rule("CA-SLIP-04")
    @unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext is not installed")
    def test_the_command_the_message_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = _cli_project(td, {
                "margin/a.csv": _qt("99900001", _qt_buy("ZZA.TO"),
                                    _qt_div("ZZA.TO", 100.0)),
                "margin/b.csv": _qt("99900002", _qt_buy("ZZB.TO"),
                                    _qt_div("ZZB.TO", 200.0))})
            pdf = CRA.t5(Path(td) / "t5.pdf", ["QUESTRADE, INC."],
                         {"13": "900.00"})
            sd = root / "inputs" / "slips"
            sd.mkdir(parents=True, exist_ok=True)
            salt = SA.key_salt(root, create=True)
            books = json.loads((root / "work" / "margin_base.json")
                               .read_text(encoding="utf-8"))
            h = next(iter(SA._file_hashes(books)["a.csv"]))
            (sd / "slips.toml").write_text(
                'year = 2025\n[[slip]]\ntype = "T5"\n'
                'issuer = "QUESTRADE, INC."\naccount = "margin"\n'
                'currency = "CAD"\nstatus = "original"\n'
                f'broker_key = "{SA.broker_key(salt, h)}"\n'
                'source = "cra:t5.pdf"\nboxes = { 13 = 900.00 }\n',
                encoding="utf-8")
            r = tj(root, "slip-audit", "margin", "--import-cra", str(pdf),
                   "--write")
            self.assertIn("already in slips.toml", r.stdout)
            self.assertEqual(len(_slip_tables(root)), 1)


class UnderFiftyBesideATrust(unittest.TestCase):
    """TJ-1209: a T3 typed by its lines or naming no fund: its income is
    no T5 income of the payer."""

    ROWS = [_div("FND.TO", "2025-06-02", 100.0),
            _interest("2025-06-02", 40.0)]

    def _audit(self, table, rows=None):
        return SA.audit(_project(rows or self.ROWS, (
            'year = 2025\n[[slip]]\ntype = "T3"\naccount = "margin"\n'
            'issuer = "Synthetic Trust"\n' + table)), CFG)

    @rule("CA-SLIP-01")
    def test_typed_by_its_lines(self):
        rep = self._audit('[[slip.line]]\nsymbol = "FND.TO"\nbox = "49"\n'
                          'amount = 100\n')
        ln = _line(rep, "interest")
        self.assertEqual(ln["status"], "under-50", ln)
        self.assertEqual(rep["issues"], [])

    @rule("CA-SLIP-01")
    def test_no_security_named(self):
        rep = self._audit('boxes = { 49 = 100 }\n')
        self.assertEqual(_line(rep, "interest")["status"], "under-50")
        self.assertEqual(rep["issues"], [])

    @rule("CA-SLIP-01")
    def test_a_named_fund_still_excused(self):
        rep = self._audit('security = "FND.TO"\nboxes = { 49 = 100 }\n')
        self.assertEqual(_line(rep, "interest")["status"], "under-50")

    @rule("CA-SLIP-01")
    def test_return_of_capital_inside_the_cash(self):
        rep = self._audit('boxes = { 49 = 60, 42 = 40 }\n')
        self.assertEqual(_line(rep, "interest")["status"], "under-50")

    @rule("CA-SLIP-01")
    def test_other_income_beyond_the_trust_still_counts(self):
        rows = self.ROWS + [_div("SYN.TO", "2025-06-02", 30.0)]
        for table in ('boxes = { 49 = 100 }\n',
                      '[[slip.line]]\nsymbol = "FND.TO"\nbox = "49"\n'
                      'amount = 100\n'):
            rep = self._audit(table, rows)
            self.assertEqual(_line(rep, "interest")["status"], "differs",
                             table)


class UnderFiftyOfAnotherPayer(unittest.TestCase):
    """TJ-1210: one account's unkeyed group spans two brokers' files: the
    minimum is the payer's. Two exports of one broker account stay one
    payer (TJ-0996)."""

    @staticmethod
    def _audit(rows, boxes=None):
        return SA.audit(_project(rows, _t5(boxes or {"24": 100})), CFG)

    @rule("CA-SLIP-01")
    def test_another_payers_interest_is_excused(self):
        rep = self._audit([_div("SYN.TO", "2025-06-02", 100.0,
                                source="questrade.csv"),
                           _interest("2025-06-02", 40.0, source="other.csv")])
        self.assertEqual(_line(rep, "interest")["status"], "under-50")
        self.assertEqual(rep["issues"], [])

    @rule("CA-SLIP-01")
    def test_neither_payer_reaches_fifty(self):
        rep = self._audit([_div("SYN.TO", "2025-06-02", 30.0,
                                source="questrade.csv"),
                           _interest("2025-06-02", 40.0, source="other.csv")],
                          {"24": 30})
        self.assertEqual(_line(rep, "interest")["status"], "under-50")

    @rule("CA-SLIP-01")
    def test_same_payer_with_its_dividends_differs(self):
        rep = self._audit([
            _div("SYN.TO", "2025-06-02", 100.0, source="questrade.csv"),
            _div("FND.TO", "2025-06-02", 20.0, source="other.csv"),
            _interest("2025-06-02", 40.0, source="other.csv")],
            {"24": 100})
        self.assertEqual(_line(rep, "interest")["status"], "differs")

    @rule("CA-SLIP-01")
    def test_two_exports_of_one_broker_account_are_one_payer(self):
        rep = self._audit([
            _div("SYN.TO", "2025-06-02", 100.0, source="h1.csv",
                 source_account="a" * 10),
            _interest("2025-06-02", 40.0, source="h2.csv",
                      source_account="a" * 10)])
        self.assertEqual(_line(rep, "interest")["status"], "differs")

    @rule("CA-SLIP-01")
    def test_two_broker_accounts_are_two_payers(self):
        rep = self._audit([
            _div("SYN.TO", "2025-06-02", 100.0, source="a.csv",
                 source_account="a" * 10),
            _interest("2025-06-02", 40.0, source="b.csv",
                      source_account="b" * 10)])
        self.assertEqual(_line(rep, "interest")["status"], "under-50")

    @rule("CA-SLIP-01")
    def test_a_row_of_no_source_joins_no_second_payer(self):
        rep = self._audit([
            _div("SYN.TO", "2025-06-02", 100.0, source=""),
            _interest("2025-06-02", 40.0, source="a.csv")])
        self.assertEqual(_line(rep, "interest")["status"], "differs")

    @rule("CA-SLIP-01")
    def test_questrade_and_rbc_exports(self):
        rbc = ('Account Activity Detail Report\n'
               '"Account Number: 99900099"\n'
               '"Date Range: January 1, 2025 - December 31, 2025"\n\n'
               'Date,Activity,Symbol,Description,Quantity,Price,'
               'Settlement Date,Currency,Value,Amount\n'
               '06/30/2025,Interest,,INTEREST CREDIT,0,0,06/30/2025,CAD,'
               '40.00,40.00\n')
        with tempfile.TemporaryDirectory() as td:
            root = _cli_project(td, {
                "margin/questrade.csv": _qt("99900090", _qt_buy("SYN.TO"),
                                            _qt_div("SYN.TO", 100.0)),
                "margin/rbc.csv": rbc,
                "slips/slips.toml": (
                    'year = 2025\n[[slip]]\ntype = "T5"\n'
                    'issuer = "Questrade"\naccount = "margin"\n'
                    'boxes = { 24 = 100.00 }\n')})
            r = tj(root, "slip-audit", "--json")
        rep = json.loads(r.stdout)
        self.assertEqual(_line(rep, "interest")["status"], "under-50", rep)
        self.assertEqual(rep["issues"], [])


class Box14Placed(unittest.TestCase):
    """TJ-1211: other income (T5 box 14) is a kind of income the books
    can hold."""

    def _ab(self):
        return _groups([_brow("a" * 10, "FND.TO", 100.0, cat="other")],
                       [_brow("b" * 10, "SYN.TO", 1000.0, src="b.csv")])

    @rule("CA-SLIP-04")
    def test_placed_where_the_other_income_is(self):
        pl = _place({"14": 100.0}, self._ab())
        self.assertEqual(pl.group.sources, ("a.csv",), pl.how)
        self.assertEqual(pl.account, "margin")

    @rule("CA-SLIP-04")
    def test_no_other_income_is_said_in_words(self):
        pl = _place({"14": 100.0}, _groups(
            [_brow("a" * 10, "ZZA.TO", 50.0)],
            [_brow("b" * 10, "SYN.TO", 1000.0, src="b.csv")]))
        self.assertIsNone(pl.group)
        self.assertIn("has its other income in 2025", pl.how)
        self.assertNotIn("no amounts", pl.how)

    @rule("CA-SLIP-04")
    def test_named_account_reads_the_same(self):
        pl = _place({"14": 100.0}, _groups(
            [_brow("a" * 10, "ZZA.TO", 50.0)],
            [_brow("b" * 10, "SYN.TO", 1000.0, src="b.csv")]),
            account="margin")
        self.assertEqual(pl.account, "margin")
        self.assertIsNone(pl.group)
        self.assertIn("other income", pl.how)


class EqualT5sAcrossRunsAndOrders(unittest.TestCase):
    """TJ-1212: in one run the result does not depend on the order; across
    two runs a slip naming an account is not called the same slip."""

    @rule("CA-SLIP-04")
    def test_every_order_keeps_both_accounts(self):
        import itertools
        for perm in itertools.permutations(("copy", "acct1", "acct2")):
            slips = [_cra_t5(n + ".pdf", {"copy": None, "acct1": "99900001",
                                          "acct2": "99900002"}[n])
                     for n in perm]
            kept, notes = CS.drop_duplicates(slips)
            self.assertEqual(sorted(s.shown for s in kept),
                             ["acct1.pdf", "acct2.pdf"], perm)
            self.assertEqual(len(notes), 1, perm)
            self.assertIn("copy.pdf", notes[0])

    @rule("CA-SLIP-04")
    def test_a_copy_and_its_tagged_page_keep_the_tag(self):
        for order in ((None, "99900001"), ("99900001", None)):
            slips = [_cra_t5(f"f{i}.pdf", n) for i, n in enumerate(order)]
            kept, notes = CS.drop_duplicates(slips)
            self.assertEqual(len(kept), 1)
            self.assertTrue(kept[0].account_tag)
            self.assertEqual(len(notes), 1)

    @rule("CA-SLIP-04")
    def test_two_copies_stay_one(self):
        kept, notes = CS.drop_duplicates([_cra_t5("a.pdf"), _cra_t5("b.pdf"),
                                          _cra_t5("c.pdf")])
        self.assertEqual([s.shown for s in kept], ["a.pdf"])
        self.assertEqual(len(notes), 2)

    def _plan(self, shown, source, acct="99900002"):
        s = _cra_t5(shown, acct)
        table = _existing(keys=(), issuer=s.issuer, source=source)
        return CS.plan_import([(CS.Placement(s, account="margin"), "")],
                              [table])

    @rule("CA-SLIP-04")
    def test_another_files_page_is_not_called_the_same_slip(self):
        plan = self._plan("acct2.pdf", "cra:acct1.pdf")
        self.assertEqual(plan.add, [])
        self.assertNotIn("already in slips.toml", plan.notes[0])
        self.assertIn("acct1.pdf", plan.notes[0])
        self.assertIn("together", plan.notes[0])

    @rule("CA-SLIP-04")
    def test_the_same_file_again_is_already_there(self):
        plan = self._plan("acct1.pdf", "cra:acct1.pdf", acct="99900001")
        self.assertIn("already in slips.toml", plan.notes[0])

    @rule("CA-SLIP-04")
    def test_a_page_with_no_account_is_already_there(self):
        plan = self._plan("copy.pdf", "cra:acct1.pdf", acct=None)
        self.assertIn("already in slips.toml", plan.notes[0])

    @unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext is not installed")
    @rule("CA-SLIP-04")
    def test_folder_with_an_unnumbered_copy_writes_two_tables(self):
        def pdf(path, acct=None):
            rows = [("24", ("Actual amount of eligible dividends",),
                     "100.00"), ("27", ("Foreign currency",), "CAD")]
            if acct:
                rows.append(("29", ("Recipient account",), acct))
            return CRA.write(path, CRA.slip_lines("T5", ["QUESTRADE, INC."],
                                                  rows))
        with tempfile.TemporaryDirectory() as td:
            root = _cli_project(td, {"margin/a.csv": _qt(
                "99900001", _qt_buy("SYN.TO"), _qt_div("SYN.TO", 100.0),
                _qt_div("SYN.TO", 100.0, day="2025-06-16"))})
            d = Path(td) / "slips"
            d.mkdir()
            pdf(d / "a_saved_copy.pdf")
            pdf(d / "b_acct1.pdf", "99900001")
            pdf(d / "c_acct2.pdf", "99900002")
            r = tj(root, "slip-audit", "--import-cra", str(d), "--write")
            self.assertEqual(len(_slip_tables(root)), 2, r.stdout + r.stderr)
            # Two runs: the second file is said, and importing both
            # together is what the note asks.
            (root / "inputs" / "slips" / "slips.toml").unlink()
            one = d / "one"
            one.mkdir()
            pdf(one / "b_acct1.pdf", "99900001")
            tj(root, "slip-audit", "--import-cra", str(one), "--write")
            r = tj(root, "slip-audit", "--import-cra",
                   str(d / "c_acct2.pdf"), "--write")
            self.assertNotIn("already in slips.toml", r.stdout)
            self.assertIn("together", r.stdout)
            self.assertEqual(len(_slip_tables(root)), 1)
            pdf(one / "c_acct2.pdf", "99900002")
            tj(root, "slip-audit", "--import-cra", str(one), "--write")
            self.assertEqual(len(_slip_tables(root)), 2)
            # Read again, all of it is already there.
            r = tj(root, "slip-audit", "--import-cra", str(one), "--write")
            self.assertEqual(len(_slip_tables(root)), 2, r.stdout)


class ShiftedRowLeavesNoSurplus(unittest.TestCase):
    """TJ-1213: an unquoted 1,000 whose surplus cell is blank, or that
    exactly fills the header, is not read as proceeds 1, cost 000."""

    def _load(self, head, row):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "slip.csv"
            f.write_text(head + "\n" + row + "\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                out = load_slip(f)
        return out, " ".join(err.getvalue().split())

    def test_shift_signatures_are_not_reconciled(self):
        h4 = "symbol,quantity,proceeds,cost"
        h5 = h4 + ",description"
        for head, row in ((h4, "SYN.TO,1,1,000,"), (h5, "SYN.TO,1,1,000,10,"),
                          (h4, "SYN.TO,1,1,000"), (h4, "SYN.TO,1,1,050"),
                          (h4, "SYN.TO,1,000,5000"),
                          ("symbol,quantity,cost,proceeds", "SYN.TO,1,1,000")):
            out, err = self._load(head, row)
            self.assertEqual(out.get("__dropped_rows__"), 1, (row, out))
            self.assertNotIn("SYN", out, row)
            self.assertIn("NOT reconciled", err)
            self.assertIn("written with a comma", err)

    def test_figures_that_are_no_shift_are_read(self):
        h4 = "symbol,quantity,proceeds,cost"
        for row, proceeds, cost in (
                ("SYN.TO,1,1,10,", 1.0, 10.0), ("SYN.TO,100,250,300", 250.0,
                                                300.0),
                ('SYN.TO,1,"1,000",10', 1000.0, 10.0),
                ("SYN.TO,1,1000.00,0", 1000.0, 0.0),
                ("SYN.TO,1,1,0", 1.0, 0.0), ("SYN.TO,1,1,0.00", 1.0, 0.0),
                ("SYN.TO,1,1,00", 1.0, 0.0),
                ("SYN.TO,1,1.50,050", 1.5, 50.0)):
            out, err = self._load(h4, row)
            self.assertNotIn("__dropped_rows__", out, row)
            self.assertAlmostEqual(out["SYN"]["proceeds"], proceeds, msg=row)
            self.assertAlmostEqual(out["SYN"]["cost"], cost, msg=row)
            self.assertEqual(err, "")

    def test_a_ticker_and_a_quantity_of_zeros_are_no_shift(self):
        # The cell before the quantity is the symbol, not a figure.
        out, _ = self._load("symbol,quantity,proceeds", "123,000,10")
        self.assertNotIn("__dropped_rows__", out)


class UnmatchedT5BesideABareAccount(unittest.TestCase):
    """TJ-1214: the broker's second account, with no income rows, still
    counts when no account has the slip's kind of income."""

    def _ab(self):
        return [CS.Group("margin", ("a" * 10,), "questrade", ("a.csv",),
                         [_brow("a" * 10, "ZZA.TO", 100.0)]),
                CS.Group("margin", ("b" * 10,), "questrade", ("b.csv",), [])]

    @rule("CA-SLIP-04")
    def test_interest_slip_is_listed(self):
        pl = _place({"13": 900.0}, self._ab())
        self.assertIsNone(pl.group)
        self.assertIn("has its interest in 2025", pl.how)

    @rule("CA-SLIP-04")
    def test_named_account_writes_no_broker_account(self):
        pl = _place({"13": 900.0}, self._ab(), account="margin")
        self.assertEqual((pl.account, pl.group, pl.key), ("margin", None, ""))

    @rule("CA-SLIP-04")
    def test_a_matching_slip_goes_to_the_paid_account(self):
        pl = _place({"24": 100.0}, self._ab())
        self.assertEqual(pl.group.sources, ("a.csv",))

    @rule("CA-SLIP-04")
    def test_the_only_broker_account_keeps_its_slip(self):
        pl = _place({"13": 900.0}, self._ab()[:1])
        self.assertEqual(pl.group.sources, ("a.csv",))

    @rule("CA-SLIP-04")
    def test_a_bare_account_alone_keeps_its_slip(self):
        pl = _place({"13": 900.0}, self._ab()[1:])
        self.assertEqual(pl.group.sources, ("b.csv",))


class Box18OfBothListings(unittest.TestCase):
    """TJ-1215: each listing's slip drafts its own box-18 entry."""

    ROWS = [_div("SYN.US", "2025-06-02", 100.0),
            _div("SYN.TO", "2025-06-03", 100.0)]

    def _cgd(self, a="SYN.TO", b="SYN.US", rows=None):
        toml = ('year = 2025\n'
                f'[[slip]]\ntype = "T5"\naccount = "margin"\nissuer = "P1"\n'
                f'security = "{a}"\nboxes = {{ 24 = 80, 18 = 20 }}\n'
                f'[[slip]]\ntype = "T5"\naccount = "margin"\nissuer = "P2"\n'
                f'security = "{b}"\nboxes = {{ 15 = 95, 18 = 5 }}\n')
        rep = SA.audit(_project(rows or self.ROWS, toml), CFG)
        return rep, [(c["symbol"], c["amount"])
                     for c in rep["suggestions"]["capital_gains_dividends"]]

    @rule("CA-SLIP-03")
    def test_each_slip_names_its_listing(self):
        _rep, got = self._cgd()
        self.assertEqual(sorted(got), [("SYN.TO", 20.0), ("SYN.US", 5.0)])

    @rule("CA-SLIP-03")
    def test_applying_the_draft_leaves_the_audit_ok(self):
        tt = ("BUYSELL 2025-01-06 10:00:00 SYN.US 100 CAD 10 1000 0\n"
              "BUYSELL 2025-01-06 10:00:00 SYN.TO 100 CAD 10 1000 0\n"
              "DIVIDEND 2025-06-02 09:30:00 SYN.US 100 CAD 1 100.00\n"
              "DIVIDEND 2025-06-03 09:30:00 SYN.TO 100 CAD 1 100.00\n")
        slips = ('year = 2025\n'
                 '[[slip]]\ntype = "T5"\naccount = "margin"\nissuer = "P1"\n'
                 'currency = "CAD"\nsecurity = "SYN.TO"\n'
                 'boxes = { 24 = 80, 18 = 20 }\n'
                 '[[slip]]\ntype = "T5"\naccount = "margin"\nissuer = "P2"\n'
                 'currency = "CAD"\nsecurity = "SYN.US"\n'
                 'boxes = { 15 = 95, 18 = 5 }\n')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            for rel, text in (("margin/margin.tt", tt),
                              ("slips/slips.toml", slips)):
                f = root / "inputs" / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
            (root / "ticker.map").write_text("DISTINCT SYN.TO SYN.US\n",
                                             encoding="utf-8")
            (root / "taxjson.toml").write_text(
                CA_HEAD + '[accounts.margin]\ntype = "taxable"\n',
                encoding="utf-8")
            tj(root, "run", "--no-input")
            rep = json.loads(tj(root, "slip-audit", "--json",
                                check=False).stdout)
            cgd = rep["suggestions"]["capital_gains_dividends"]
            self.assertEqual(sorted((c["symbol"], c["amount"]) for c in cgd),
                             [("SYN.TO", 20.0), ("SYN.US", 5.0)])
            with (root / "taxjson.toml").open("a", encoding="utf-8") as f:
                f.write("".join(c["toml"] + "\n" for c in cgd))
            tj(root, "run", "--no-input")
            rep = json.loads(tj(root, "slip-audit", "--json",
                                check=False).stdout)
        self.assertEqual(rep["status"], "ok", rep["issues"])

    @rule("CA-SLIP-03")
    def test_one_listing_without_a_dividend_is_said(self):
        rep, got = self._cgd(rows=[_div("SYN.TO", "2025-06-03", 100.0)])
        self.assertEqual(got, [("SYN.TO", 20.0)])
        self.assertTrue(any("SYN.US" in n and "no CAD dividend" in n
                            for n in rep["suggestions"]["notes"]),
                        rep["suggestions"]["notes"])

    @rule("CA-SLIP-03")
    def test_two_exchanges_of_one_country_stay_one(self):
        _rep, got = self._cgd(a="SYN.TO", b="SYN.V",
                              rows=[_div("SYN.TO", "2025-06-03", 200.0)])
        self.assertEqual(got, [("SYN.TO", 25.0)])

    @rule("CA-SLIP-03")
    def test_a_bare_slip_stays_merged(self):
        _rep, got = self._cgd(a="SYN", b="SYN.US")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1], 25.0)

    @rule("CA-SLIP-03")
    def test_box_18_of_one_listing_beside_the_others_return_of_capital(self):
        toml = ('year = 2025\n'
                '[[slip]]\ntype = "T3"\naccount = "margin"\nissuer = "P1"\n'
                'security = "SYN.TO"\nboxes = { 49 = 60, 42 = 40 }\n'
                '[[slip]]\ntype = "T5"\naccount = "margin"\nissuer = "P2"\n'
                'security = "SYN.US"\nboxes = { 15 = 95, 18 = 5 }\n')
        rep = SA.audit(_project(self.ROWS, toml), CFG)
        got = [(c["symbol"], c["amount"])
               for c in rep["suggestions"]["capital_gains_dividends"]]
        self.assertEqual(got, [("SYN.US", 5.0)])

    @rule("CA-SLIP-03")
    def test_slip_lines_naming_each_listing(self):
        toml = ('year = 2025\n[[slip]]\ntype = "T5"\naccount = "margin"\n'
                'issuer = "P1"\nboxes = { 24 = 180, 15 = 0, 18 = 20 }\n'
                '[[slip.line]]\nsymbol = "SYN.TO"\nbox = "18"\namount = 15\n'
                '[[slip.line]]\nsymbol = "SYN.US"\nbox = "18"\namount = 5\n')
        rep = SA.audit(_project(self.ROWS, toml), CFG)
        # One slip's lines naming two listings: its sum is not split.
        got = [c["amount"] for c in rep["suggestions"][
            "capital_gains_dividends"]]
        self.assertEqual(got, [20.0])


class T3OfOneListing(unittest.TestCase):
    """TJ-1216: a T3 naming SYN.TO splits SYN.TO's rows, not SYN.US's."""

    SLIPS = ('year = 2025\n'
             '[[slip]]\ntype = "T3"\naccount = "margin"\nissuer = "Trust"\n'
             'security = "{sec}"\nboxes = {{ {t3} }}\n'
             '[[slip]]\ntype = "T5"\naccount = "margin"\nissuer = "Payer"\n'
             'security = "SYN.US"\nboxes = {{ 15 = 100 }}\n')

    def _audit(self, sec="SYN.TO", t3="49 = 100", rows=None):
        rows = rows or [_div("SYN.US", "2025-06-02", 100.0),
                        _div("SYN.TO", "2025-06-03", 100.0)]
        return SA.audit(_project(rows, self.SLIPS.format(sec=sec, t3=t3)),
                        CFG)

    @rule("CA-SLIP-01")
    def test_each_slip_matches_its_listing(self):
        rep = self._audit()
        for cat in ("ca_div", "foreign"):
            ln = _line(rep, cat)
            self.assertEqual((ln["slip"], ln["books"], ln["status"]),
                             (100.0, 100.0, "ok"), ln)
        self.assertEqual(rep["issues"], [])

    @rule("CA-SLIP-01")
    def test_the_split_applies_to_its_own_listing(self):
        rep = self._audit(t3="49 = 60, 26 = 40")
        self.assertEqual(_line(rep, "ca_div")["books"], 60.0)
        self.assertEqual(_line(rep, "other")["books"], 40.0)
        self.assertEqual(_line(rep, "foreign")["books"], 100.0)
        self.assertEqual(rep["issues"], [])

    @rule("CA-SLIP-01")
    def test_a_bare_security_still_takes_every_listing(self):
        rep = self._audit(sec="SYN", t3="49 = 200",
                          rows=[_div("SYN.US", "2025-06-02", 100.0),
                                _div("SYN.TO", "2025-06-03", 100.0)])
        self.assertEqual(_line(rep, "ca_div")["books"], 200.0)

    @rule("CA-SLIP-01")
    def test_a_listing_the_books_do_not_hold_keeps_the_root(self):
        rep = self._audit(sec="SYN.TO", t3="49 = 100",
                          rows=[_div("SYN.US", "2025-06-02", 100.0)])
        self.assertEqual(_line(rep, "ca_div")["books"], 100.0)

    @rule("CA-SLIP-01")
    def test_another_buckets_listing_is_left_alone(self):
        """SYN.US paid in USD with a USD T5: the CAD T3's split never
        reaches the USD bucket."""
        toml = ('year = 2025\n'
                '[[slip]]\ntype = "T3"\naccount = "margin"\nissuer = "Trust"\n'
                'security = "SYN.TO"\nboxes = { 49 = 100 }\n'
                '[[slip]]\ntype = "T5"\naccount = "margin"\nissuer = "Payer"\n'
                'currency = "USD"\nsecurity = "SYN.US"\n'
                'boxes = { 15 = 100 }\n')
        root = _project([_div("SYN.TO", "2025-06-03", 100.0),
                         _div("SYN.US", "2025-06-02", 135.0)], toml)
        raw = [{"id": "t1", "account": "margin", "action": "DIVIDEND",
                "symbol": "SYN.US", "date": "2025-06-02", "currency": "USD",
                "gross_amount": 100.0, "net_amount": 100.0,
                "source": "trades.tt"}]
        (root / "work" / "margin_raw.json").write_text(
            json.dumps({"transactions": raw, "metadata": {}}),
            encoding="utf-8")
        rep = SA.audit(root, CFG, fx_cache={
            "_boc": {"USDCAD": {"obs": {"2025-06-02": 1.35}}}})
        self.assertEqual(rep["issues"], [])


class SecurityFutureCarriedBasis(unittest.TestCase):
    """TJ-1252: a US security future compares as Form 8949 shows it: only
    a negative figure is reshaped (build_8949)."""

    def _rec(self, e, country="usa"):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [e]}), encoding="utf-8")
            with mock.patch("taxjson.lib.markets.is_security_future_root",
                            return_value=True):
                return load_computed([g], 2025, "trade" if country == "usa"
                                     else "settle", country)["F:ZQFZ5"]

    @staticmethod
    def _row(**kw):
        e = {"date": "2025-09-15", "date_settle": "2025-09-16",
             "symbol": "F:ZQFZ5.US", "qty": -1, "proceeds": 150.0,
             "cost": 100.0, "gain": 50.0, "raw_gain": 50.0,
             "commission": 0.0, "fee": 0.0, "direction": "LONG"}
        e.update(kw)
        return e

    def test_carried_basis_stays_as_form_8949_shows_it(self):
        rec = self._rec(self._row())
        self.assertAlmostEqual(rec["proceeds_gross"], 150.0)
        self.assertAlmostEqual(rec["cost"], 100.0)

    def test_a_loss_is_reshaped_as_form_8949_does(self):
        rec = self._rec(self._row(proceeds=-50.0, cost=0.0, gain=-50.0,
                                  raw_gain=-50.0))
        self.assertAlmostEqual(rec["proceeds_gross"], 0.0)
        self.assertAlmostEqual(rec["cost"], 50.0)
        self.assertEqual(rec["future_loss_rows"], 1)

    def test_a_short_gain_is_reshaped_as_form_8949_does(self):
        rec = self._rec(self._row(qty=1, proceeds=0.0, cost=-200.0, gain=200.0,
                                  raw_gain=200.0, direction="SHORT"))
        self.assertAlmostEqual(rec["proceeds_gross"], 200.0)
        self.assertAlmostEqual(rec["cost"], 0.0)

    def test_a_loss_with_positive_figures_is_a_loss_row(self):
        rec = self._rec(self._row(proceeds=0.0, cost=100.0, gain=-100.0,
                                  raw_gain=-100.0))
        self.assertAlmostEqual(rec["cost"], 100.0)
        self.assertEqual(rec["future_loss_rows"], 1)

    def test_a_commission_is_not_added_back(self):
        rec = self._rec(self._row(commission=5.0))
        self.assertAlmostEqual(rec["proceeds_gross"], 150.0)

    def test_canada_keeps_the_schedule_3_shape(self):
        rec = self._rec(self._row(), country="canada")
        self.assertAlmostEqual(rec["proceeds_gross"], 50.0)
        self.assertAlmostEqual(rec["cost"], 0.0)

    def test_the_form_8949_slip_reconciles(self):
        tt = ("BUYSELL 2024-12-03 10:00:00 F:ZQFZ5.US 1 USD 100.00 10000.00 "
              "0 x100\n"
              "BUYSELL 2024-12-10 10:00:00 F:ZQFZ5.US -1 USD 99.00 9900.00 "
              "0 x100\n"
              "BUYSELL 2024-12-20 10:00:00 F:ZQFZ5.US 1 USD 98.00 9800.00 "
              "0 x100\n"
              "BUYSELL 2025-09-15 10:00:00 F:ZQFZ5.US -1 USD 99.50 9950.00 "
              "0 x100\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "t.tt").write_text(
                tt, encoding="utf-8")
            (root / "ticker.map").write_text("SECFUT ZQF\n", encoding="utf-8")
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/New_York"\n'
                'year = 2025\ncountry = "usa"\nbase_currency = "USD"\n'
                'source_currencies = []\n[accounts.margin]\n'
                'type = "taxable"\n', encoding="utf-8")
            tj(root, "run", "--no-input")
            (root / "a.csv").write_text(
                "symbol,quantity,proceeds,cost,currency\n"
                "F:ZQFZ5.US,1,150,100,USD\n", encoding="utf-8")
            r = tj(root, "reconcile-slips", str(root / "a.csv"), "--details",
                   check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            (root / "a.csv").write_text(
                "symbol,quantity,proceeds,cost,currency\n"
                "F:ZQFZ5.US,1,50,0,USD\n", encoding="utf-8")
            r = tj(root, "reconcile-slips", str(root / "a.csv"), "--details",
                   check=False)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


def _adjusts(rep):
    return [ln for e in rep["suggestions"]["tt_lines"] for ln in e["lines"]
            if ln.startswith("ADJUST")]


class TwoBox42LinesOneText(unittest.TestCase):
    """TJ-1253: box-42 lines that come to one ADJUST text add up."""

    def _lines(self, d1, d2, day="2025-06-02", extra=""):
        toml = ('year = 2025\n[[slip]]\ntype = "T3"\naccount = "margin"\n'
                'currency = "CAD"\nissuer = "Trust"\n'
                'boxes = { 49 = 60.00, 42 = 40.00 }\n' + extra)
        for d in (d1, d2):
            toml += ('[[slip.line]]\nsymbol = "SYN.UN.TO"\n'
                     f'date = "{d}"\nbox = "42"\namount = 20.00\n')
        rep = SA.audit(_project([_div("SYN.UN.TO", day, 100.0)], toml), CFG)
        return _adjusts(rep)

    @rule("CA-SLIP-03")
    def test_one_date_two_lines(self):
        self.assertEqual(self._lines("2025-06-02", "2025-06-02"),
                         ["ADJUST 2025-06-02 16:00:00 SYN.UN.TO CAD -40.00 "
                          "type=roc"])

    @rule("CA-SLIP-03")
    def test_two_dates_one_dividend_row(self):
        self.assertEqual(self._lines("2025-06-02", "2025-06-05",
                                     day="2025-06-03"),
                         ["ADJUST 2025-06-03 16:00:00 SYN.UN.TO CAD -40.00 "
                          "type=roc"])

    @rule("CA-SLIP-03")
    def test_two_distributions_stay_two_lines(self):
        toml = ('year = 2025\n[[slip]]\ntype = "T3"\naccount = "margin"\n'
                'currency = "CAD"\nissuer = "Trust"\n'
                'boxes = { 49 = 160.00, 42 = 40.00 }\n'
                '[[slip.line]]\nsymbol = "SYN.UN.TO"\ndate = "2025-06-02"\n'
                'box = "42"\namount = 20.00\n'
                '[[slip.line]]\nsymbol = "SYN.UN.TO"\ndate = "2025-09-02"\n'
                'box = "42"\namount = 20.00\n')
        rep = SA.audit(_project([_div("SYN.UN.TO", "2025-06-02", 100.0),
                                 _div("SYN.UN.TO", "2025-09-02", 100.0)],
                                toml), CFG)
        self.assertEqual(
            _adjusts(rep),
            ["ADJUST 2025-06-02 16:00:00 SYN.UN.TO CAD -20.00 type=roc",
             "ADJUST 2025-09-02 16:00:00 SYN.UN.TO CAD -20.00 type=roc"])

    @rule("CA-SLIP-03")
    def test_unequal_lines_on_one_date(self):
        toml = ('year = 2025\n[[slip]]\ntype = "T3"\naccount = "margin"\n'
                'currency = "CAD"\nissuer = "Trust"\n'
                'boxes = { 49 = 60.00, 42 = 40.00 }\n'
                '[[slip.line]]\nsymbol = "SYN.UN.TO"\ndate = "2025-06-02"\n'
                'box = "42"\namount = 30.00\n'
                '[[slip.line]]\nsymbol = "SYN.UN.TO"\ndate = "2025-06-02"\n'
                'box = "42"\namount = 10.00\n')
        rep = SA.audit(_project([_div("SYN.UN.TO", "2025-06-02", 100.0)],
                                toml), CFG)
        self.assertEqual(
            _adjusts(rep),
            ["ADJUST 2025-06-02 16:00:00 SYN.UN.TO CAD -40.00 type=roc"])


if __name__ == "__main__":
    unittest.main()
