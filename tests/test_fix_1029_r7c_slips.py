"""Fix round 7 (wave C) of 2026-10-29, slips: backlog TJ-0996 to
TJ-1016 (the third worktree audit's final continuation).

Synthetic data only: made-up tickers (SYN, FND), issuers and broker
accounts.
"""
import json
import unittest
from pathlib import Path

from _tmpfiles import private_dir
from taxjson.lib import slip_audit as SA
from tax_rules import rule

CFG = {"settings": {"year": 2025, "country": "canada",
                    "base_currency": "CAD"},
       "accounts": {"margin": {"type": "taxable"}}}


def _project(book_rows, slips_toml="year = 2025\n"):
    """A project folder with work/margin_base.json holding `book_rows`
    (transaction dicts) and inputs/slips/slips.toml."""
    root = Path(private_dir("r7c-slip-"))
    (root / "inputs" / "margin").mkdir(parents=True)
    sd = root / "inputs" / "slips"
    sd.mkdir()
    (root / "work").mkdir()
    tx = []
    for i, r in enumerate(book_rows):
        t = {"id": f"t{i}", "account": "margin", "currency": "CAD",
             "source": "trades.tt", "description": ""}
        t.update(r)
        tx.append(t)
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": tx, "metadata": {}}), encoding="utf-8")
    (sd / "slips.toml").write_text(slips_toml, encoding="utf-8")
    return root


def _div(symbol, day, amt, **kw):
    t = {"action": "DIVIDEND", "symbol": symbol, "date": day,
         "gross_amount": amt, "net_amount": amt}
    t.update(kw)
    return t


def _interest(day, amt, **kw):
    t = {"action": "INTEREST", "symbol": "", "date": day,
         "gross_amount": amt, "net_amount": amt}
    t.update(kw)
    return t


def _t5(boxes, extra=""):
    return ('year = 2025\n[[slip]]\ntype = "T5"\naccount = "margin"\n'
            'issuer = "Synthetic Broker"\n' + extra + "boxes = { "
            + ", ".join(f'"{k}" = {v}' for k, v in boxes.items()) + " }\n")


def _line(rep, cat):
    for a in rep["accounts"]:
        for g in a["groups"]:
            for b in g["buckets"]:
                for ln in b["lines"]:
                    if ln["category"] == cat:
                        return ln
    return None


class TestInterestUnderFiftyOnAnIssuedT5(unittest.TestCase):
    """TJ-0996: interest under 50 is excused only when the payer paid
    under 50 in all and its T5 shows nothing else; a T5 showing 100 of
    dividends must carry the 40 of interest too."""

    def _audit(self, boxes, rows=None):
        rows = rows or [_div("SYN.TO", "2025-06-02", 100.0),
                        _interest("2025-06-02", 40.0)]
        return SA.audit(_project(rows, _t5(boxes)), CFG)

    @rule("CA-SLIP-01")
    def test_missing_interest_on_an_issued_t5_differs(self):
        rep = self._audit({"24": 100})
        ln = _line(rep, "interest")
        self.assertEqual(ln["status"], "differs", ln)
        self.assertNotIn("note", ln)
        self.assertTrue(any("interest" in i["text"]
                            for i in rep["issues"]), rep["issues"])

    @rule("CA-SLIP-01")
    def test_complete_t5_is_ok(self):
        rep = self._audit({"24": 100, "13": 40})
        self.assertEqual(_line(rep, "interest")["status"], "ok")
        self.assertEqual(rep["issues"], [])

    @rule("CA-SLIP-01")
    def test_payer_total_from_the_books_counts(self):
        # The typed T5 has no box yet, but the books hold 30 of the
        # payer's dividends beside 40 of interest: 70 in all.
        rep = self._audit({"24": 0},
                          rows=[_div("SYN.TO", "2025-06-02", 30.0),
                                _interest("2025-06-02", 40.0)])
        self.assertEqual(_line(rep, "interest")["status"], "differs")

    @rule("CA-SLIP-01")
    def test_small_interest_alone_is_still_excused(self):
        rep = self._audit({"24": 0}, rows=[_interest("2025-06-02", 40.0)])
        ln = _line(rep, "interest")
        self.assertEqual(ln["status"], "under-50", ln)


def _usd_project(slip_box15):
    """100 USD of a foreign dividend, 135 CAD in the books (1.35), and a
    USD T5 with box 15."""
    root = _project([_div("SYN", "2025-06-02", 135.0)],
                    _t5({"15": slip_box15}, extra='currency = "USD"\n'))
    raw = [{"id": "t0", "account": "margin", "action": "DIVIDEND",
            "symbol": "SYN", "date": "2025-06-02", "currency": "USD",
            "gross_amount": 100.0, "net_amount": 100.0,
            "source": "trades.tt"}]
    (root / "work" / "margin_raw.json").write_text(
        json.dumps({"transactions": raw, "metadata": {}}), encoding="utf-8")
    return root


FX = {"_boc": {"USDCAD": {"obs": {"2025-03-03": 1.35,
                                  "2025-06-02": 1.35}}}}


class TestToleranceIsCad(unittest.TestCase):
    """TJ-1001: the tolerance is CAD: a USD slip's box is compared with
    its CAD worth in USD (1 CAD = 0.74 USD at 1.35), not with 1 USD."""

    def _foreign(self, rep):
        return _line(rep, "foreign")

    @rule("CA-SLIP-02")
    def test_usd_difference_over_one_cad_differs(self):
        rep = SA.audit(_usd_project(99.10), CFG, tolerance=1.0, fx_cache=FX)
        ln = self._foreign(rep)
        self.assertEqual(ln["status"], "differs", ln)
        self.assertAlmostEqual(ln["tolerance"], 0.74, places=2)
        self.assertTrue(rep["issues"])

    @rule("CA-SLIP-02")
    def test_usd_difference_under_one_cad_is_ok(self):
        rep = SA.audit(_usd_project(99.40), CFG, tolerance=1.0, fx_cache=FX)
        self.assertEqual(self._foreign(rep)["status"], "ok")
        self.assertEqual(rep["issues"], [])

    @rule("CA-SLIP-02")
    def test_no_average_rate_uses_the_books_rate(self):
        rep = SA.audit(_usd_project(99.10), CFG, tolerance=1.0,
                       fx_cache={})
        ln = self._foreign(rep)
        self.assertEqual(ln["status"], "differs", ln)
        self.assertAlmostEqual(ln["tolerance"], 0.74, places=2)


class TestBoxTypedTwice(unittest.TestCase):
    """TJ-1004: "024" and "24" are one box; typed both, the slip is
    refused rather than shown as 100 and compared as 200."""

    def test_alias_is_refused(self):
        root = _project([_div("SYN.TO", "2025-06-02", 200.0)],
                        _t5({"024": 100, "24": 100}))
        with self.assertRaises(SA.SlipsError) as cm:
            SA.audit(root, CFG)
        msg = str(cm.exception)
        self.assertIn("box 24", msg)
        self.assertIn("typed twice", msg)

    def test_one_spelling_is_compared(self):
        root = _project([_div("SYN.TO", "2025-06-02", 200.0)],
                        _t5({"024": 100}))
        rep = SA.audit(root, CFG)
        ln = _line(rep, "ca_div")
        self.assertEqual((ln["slip"], ln["status"]), (100.0, "differs"))


class TestExplicitListingIsKept(unittest.TestCase):
    """TJ-1016: a T5 naming SYN.TO drafts its box-18 entry for SYN.TO,
    not for SYN.US (another listing of the same root, paid first)."""

    SLIPS = ('year = 2025\n'
             '[[slip]]\ntype = "T5"\naccount = "margin"\n'
             'issuer = "Synthetic Canadian Fund"\nsecurity = "{ca}"\n'
             'boxes = {{ 24 = 80, 18 = 20 }}\n'
             '[[slip]]\ntype = "T5"\naccount = "margin"\n'
             'issuer = "Synthetic Foreign Payer"\nsecurity = "{us}"\n'
             'boxes = {{ 15 = 100 }}\n')

    def _audit(self, ca="SYN.TO", us="SYN.US", books_ca=None):
        rows = [_div(us, "2025-06-02", 100.0),
                _div(books_ca or ca, "2025-06-03", 100.0)]
        return SA.audit(_project(rows, self.SLIPS.format(ca=ca, us=us)),
                        CFG)

    @rule("CA-SLIP-03")
    def test_draft_names_the_slips_listing(self):
        rep = self._audit()
        cgd = rep["suggestions"]["capital_gains_dividends"]
        self.assertEqual([(c["symbol"], c["amount"]) for c in cgd],
                         [("SYN.TO", 20.0)], cgd)

    @rule("CA-SLIP-03")
    def test_books_without_a_suffix_still_match(self):
        # The books spell the Canadian listing bare: SYN.US is still
        # another market's.
        rep = self._audit(books_ca="SYN")
        cgd = rep["suggestions"]["capital_gains_dividends"]
        self.assertEqual([c["symbol"] for c in cgd], ["SYN"], cgd)

    def test_other_root_control(self):
        rep = self._audit(us="OTHER.US")
        cgd = rep["suggestions"]["capital_gains_dividends"]
        self.assertEqual([c["symbol"] for c in cgd], ["SYN.TO"], cgd)


class TestSlipCsvSurplusCells(unittest.TestCase):
    """TJ-1015: a slip CSV row with more cells than the header (an
    unquoted 1,000) is not reconciled, never read as proceeds 1 and
    cost 000."""

    def _load(self, row):
        import contextlib
        import io
        from taxjson.bin.taxjson_reconcile_slips import load_slip
        d = Path(private_dir("r7c-csv-"))
        f = d / "slip.csv"
        f.write_text("symbol,quantity,proceeds,cost\n" + row,
                     encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = load_slip(f)
        return out, err.getvalue()

    def test_extra_cell_is_not_reconciled(self):
        out, err = self._load("SYN.TO,1,1,000,10\n")
        self.assertEqual(out.get("__dropped_rows__"), 1, out)
        self.assertNotIn("SYN", out)
        text = " ".join(err.split())
        self.assertIn("line 2", text)
        self.assertIn("5 cells for 4 columns", text)
        self.assertIn('"1,000"', text)

    def test_quoted_amount_is_read(self):
        out, _ = self._load('SYN.TO,1,"1,000",10\n')
        self.assertNotIn("__dropped_rows__", out)
        self.assertAlmostEqual(out["SYN"]["proceeds"], 1000.0)

    def test_empty_trailing_cell_is_fine(self):
        out, _ = self._load("SYN.TO,1,1,10,\n")
        self.assertNotIn("__dropped_rows__", out)
        self.assertAlmostEqual(out["SYN"]["proceeds"], 1.0)


_CRA_T5 = """  2025 T5 slip (original) from SYNTHETIC CARRYING BROKER

    Box                   Box name                               Box value
    number
    24                    Actual amount of eligible dividends         100.00
    27                    Foreign currency                               CAD
{acct}"""


def _cra_t5(shown, acct_no=None):
    from taxjson.lib.cra_slips import parse_text
    row = (f"    29                    Recipient account                "
           f"      {acct_no}\n" if acct_no else "")
    return parse_text(_CRA_T5.format(acct=row), shown)


class TestCraEqualSlipsOfTwoAccounts(unittest.TestCase):
    """TJ-0997: two CRA T5s of equal figures whose pages name different
    recipient accounts (box 29) are two slips, not one read twice; the
    account number itself is never kept."""

    @rule("CA-SLIP-04")
    def test_two_accounts_are_kept(self):
        from taxjson.lib.cra_slips import drop_duplicates
        a = _cra_t5("account_1.pdf", "99900001")
        b = _cra_t5("account_2.pdf", "99900002")
        kept, notes = drop_duplicates([a, b])
        self.assertEqual([s.shown for s in kept],
                         ["account_1.pdf", "account_2.pdf"])
        self.assertEqual(notes, [])
        for s in (a, b):
            self.assertNotIn("9990000", repr(s))
            self.assertNotIn("9990000", s.account_tag)
            self.assertNotIn("29", s.boxes)

    @rule("CA-SLIP-04")
    def test_same_slip_twice_is_read_once(self):
        from taxjson.lib.cra_slips import drop_duplicates
        for pair in ((_cra_t5("t5.pdf", "99900001"),
                      _cra_t5("t5 (1).pdf", "99900001")),
                     (_cra_t5("t5.pdf"), _cra_t5("t5 (1).pdf")),
                     (_cra_t5("t5.pdf", "99900001"),
                      _cra_t5("t5 (1).pdf"))):
            kept, notes = drop_duplicates(list(pair))
            self.assertEqual([s.shown for s in kept], ["t5.pdf"])
            self.assertEqual(len(notes), 1)

    @rule("CA-SLIP-04")
    def test_both_written_and_each_table_counts_once(self):
        from taxjson.lib.cra_slips import (Existing, Placement,
                                           plan_import, table)
        a = _cra_t5("account_1.pdf", "99900001")
        b = _cra_t5("account_2.pdf", "99900002")
        placed = [(Placement(s, account="margin"), "") for s in (a, b)]
        plan = plan_import(placed, [])
        self.assertEqual(len(plan.add), 2)
        for pl, k in plan.add:
            self.assertNotIn("9990000", table(pl, k))
        one = Existing(1, "T5", "margin", "", a.issuer, "original", "CAD",
                       {"24": 100.0}, frozenset(), True)
        plan = plan_import(placed, [one])
        self.assertEqual([pl.slip.shown for pl, _ in plan.add],
                         ["account_2.pdf"])
        two = Existing(2, "T5", "margin", "", a.issuer, "original", "CAD",
                       {"24": 100.0}, frozenset(), True)
        self.assertEqual(plan_import(placed, [one, two]).add, [])


def _brow(h, sym, amt, cat="ca_div", src="a.csv", desc=""):
    return SA.BookRow(account="margin", id=f"{h}{sym}{amt}{cat}",
                      action="INTEREST" if cat == "interest" else "DIVIDEND",
                      symbol=sym, root=SA._root(sym) if sym else "",
                      currency="CAD", native=amt, cad=amt,
                      pay_date="2025-03-03", tax_date="2025-03-03",
                      source=src, source_hash=h, category=cat, hashes=(h,),
                      description=desc)


QT = "QUESTRADE, INC."


class TestCraUnmatchedT5(unittest.TestCase):
    """TJ-0998: a broker's T5 that shares no income with any of the
    broker's accounts in the books is listed, never given to the least
    different one."""

    def _groups(self, a_rows, b_rows):
        from taxjson.lib.cra_slips import Group
        return [Group("margin", ("a" * 10,), "questrade", ("a.csv",),
                      a_rows),
                Group("margin", ("b" * 10,), "questrade", ("b.csv",),
                      b_rows)]

    def _place(self, boxes, groups, account=None):
        from taxjson.lib.cra_slips import CraSlip, place
        (pl,) = place([CraSlip("t5.pdf", 2025, "T5", "original", QT,
                               boxes=boxes)], groups, 2025, {},
                      account=account)
        return pl

    def _ab(self):
        return self._groups([_brow("a" * 10, "SYN.TO", 100.0)],
                            [_brow("b" * 10, "FND.TO", 200.0,
                                   src="b.csv")])

    @rule("CA-SLIP-04")
    def test_interest_slip_with_no_interest_in_the_books_is_listed(self):
        pl = self._place({"13": 900.0}, self._ab())
        self.assertIsNone(pl.group)
        self.assertIsNone(pl.account)
        self.assertIn("no Questrade broker account", pl.how)

    @rule("CA-SLIP-04")
    def test_named_account_writes_no_broker_account(self):
        pl = self._place({"13": 900.0}, self._ab(), account="margin")
        self.assertIsNone(pl.group)
        self.assertEqual(pl.account, "margin")

    @rule("CA-SLIP-04")
    def test_matching_dividends_still_placed(self):
        pl = self._place({"24": 200.0}, self._ab())
        self.assertEqual(pl.group.sources, ("b.csv",))

    @rule("CA-SLIP-04")
    def test_interest_slip_goes_where_the_interest_is(self):
        groups = self._groups([_brow("a" * 10, "SYN.TO", 100.0)],
                              [_brow("b" * 10, "", 30.0, cat="interest",
                                     src="b.csv")])
        pl = self._place({"13": 900.0}, groups)
        self.assertEqual(pl.group.sources, ("b.csv",))


class TestCraT3NameTie(unittest.TestCase):
    """TJ-0999: a T3 whose fund two broker accounts hold, each matching
    it by name and by the same 10% off, is ambiguous, as an exact tie
    is."""

    def _place(self, amount):
        from taxjson.lib.cra_slips import CraSlip, Group, place
        desc = "SYNTHETIC FND INCOME FUND DISTRIBUTION"
        groups = [Group("margin", (h * 10,), "questrade", (f"{h}.csv",),
                        [_brow(h * 10, "FND.TO", 90.0, src=f"{h}.csv",
                               desc=desc)])
                  for h in ("a", "b")]
        (pl,) = place([CraSlip("t3.pdf", 2025, "T3", "original",
                               "SYNTHETIC FND INCOME FUND",
                               boxes={"49": amount})], groups, 2025, {})
        return pl

    @rule("CA-SLIP-04")
    def test_name_band_tie_is_ambiguous(self):
        pl = self._place(100.0)
        self.assertIsNone(pl.group)
        self.assertIn("ambiguous", pl.how)

    @rule("CA-SLIP-04")
    def test_exact_tie_is_ambiguous(self):
        pl = self._place(90.0)
        self.assertIsNone(pl.group)
        self.assertIn("ambiguous", pl.how)


if __name__ == "__main__":
    unittest.main()
