"""Pre-release review of the dated-event journals (v0.24.0): a JOURNAL
moves units between two listings INSIDE one account, so

- H1: its legs (a .tt JOURNAL line's, a broker's journal pair) never
  pair with another account's transfer — not as a move of your own,
  not as the end of an arrival from outside the books, not as an
  in-kind move to or from a plan;
- H7: an option contract is never journaled or renamed into a stock;
- M2: a .tt JOURNAL joins two listings only when something shows they
  are one security (one root, names that agree, a TOBASE line);
- M5: a listing joined to two others through a hub joins them only when
  their own names agree with each other;
- M6: migrating a ticker.map JOURNAL line to TOBASE changes nothing in
  the missing-history walks where the journal has evidence (the broker's
  legs, a .tt JOURNAL line) — a TOBASE line alone is no journal (second
  pre-release review, test_fix_review2_journals);
- the lows: trailing corporate forms only, one record per currency
  journal, RBC's J~ reference on RBC's rows only, identical .tt lines,
  a partial duplicate of the broker's journal, future dates and absurd
  quantities, a malformed cross_listings.state.

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: U5550001, 55500001).
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, projects_both

_QT_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,"
           "Currency,Activity Type,Account #,Account Type\n")
_TWO = '[accounts.a]\ntype = "taxable"\n\n[accounts.b]\ntype = "taxable"\n'
_USA_CAD = {"source_currencies": ["CAD"]}


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _total(test, root):
    r = cli(root, "sum", "--json")
    test.assertEqual(r.returncode, 0, _out(r)[-2000:])
    return json.loads(r.stdout)["totals"]["total"]


def _run(test, root, *args, ok=True):
    r = cli(root, "run", "--no-input", *args)
    if ok:
        test.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


# ------------------------------------------------------------------ H1

def _arrival_files(with_journal):
    """Account a receives 100 QZG.TO from outside the books (Questrade
    states a book value of 5000) and sells them; account b bought 100 at
    2000 and journals them to QZG.U.TO the same day."""
    qa = _QT_HDR + (
        "2025-03-05,2025-03-05,TF6,QZG.TO,QZG CORP TRANSFER BOOK VALUE "
        "5000.00,100,0.00,0.00,0.00,0.00,CAD,Transfers,55500001,Margin\n"
        "2025-04-01,2025-04-02,Sell,QZG.TO,QZG CORP,-100,80.00,8000.00,"
        "0.00,8000.00,CAD,Trades,55500001,Margin\n")       # pii-ok
    tt = "BUYSELL 2025-01-03 10:00:00 QZG.TO 100 CAD 20.00 2000.00 0.00\n"
    if with_journal:
        tt += "JOURNAL 2025-03-05 QZG.TO QZG.U.TO 100\n"
    return {"inputs/a/qt.csv": qa, "inputs/b/b.tt": tt}


class TestJournalLegsStayInTheirAccount(unittest.TestCase):

    def _check_arrival(self, country):
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for wj in (False, True):
                root = projects_both(Path(td) / str(wj), accounts=_TWO,
                                     files=_arrival_files(wj),
                                     usa=_USA_CAD)[country]
                r = _run(self, root, "--strict")
                got[wj] = _total(self, root)
                if wj:
                    # a's arrival keeps its book value: b's journal is
                    # b's own move.
                    self.assertIn("booked at the", _out(r))
            self.assertAlmostEqual(got[True], got[False], delta=0.011)

    @rule("CA-XLIST-04")
    def test_canada_tt_journal_never_cancels_another_accounts_arrival(self):
        self._check_arrival("canada")

    @rule("US-XLIST-03")
    def test_usa_tt_journal_never_cancels_another_accounts_arrival(self):
        self._check_arrival("usa")

    def _inkind_files(self, with_journal):
        from test_feat_in_kind import _ib
        files = {"inputs/margin/ib.csv": _ib([("2025-01-10", 100, 10.0)],
                                             [("2025-03-14", -100, 1500)]),
                 "inputs/rrsp/r.tt": (
                     "BUYSELL 2025-02-03 10:00:00 QZK.U.TO 100 USD 7.00 "
                     "700.00 0.00\n")}
        if with_journal:
            files["inputs/rrsp/r.tt"] += \
                "JOURNAL 2025-03-17 QZK.U.TO QZK.TO 100\n"
        return files

    def _check_inkind(self, country):
        acc = ('[accounts.margin]\ntype = "taxable"\n\n'
               '[accounts.rrsp]\ntype = "sheltered"\ntransfers = true\n')
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for wj in (False, True):
                root = projects_both(Path(td) / str(wj), accounts=acc,
                                     files=self._inkind_files(wj),
                                     usa=_USA_CAD)[country]
                r = _run(self, root)
                self.assertNotIn("in-kind move", _out(r))
                got[wj] = _total(self, root)
            self.assertAlmostEqual(got[True], got[False], delta=0.011)
            self.assertAlmostEqual(got[True], 0.0, delta=0.011)

    @rule("CA-XLIST-04")
    def test_canada_plan_journal_is_not_an_in_kind_contribution(self):
        self._check_inkind("canada")

    @rule("US-XLIST-03")
    def test_usa_plan_journal_is_not_an_in_kind_move(self):
        self._check_inkind("usa")


class TestOwnJournalLegs(unittest.TestCase):
    """lib/transfer_in.own_journal_legs: the pure pairing."""

    def _row(self, sym, q, day="2025-03-05", pair="", desc=""):
        return {"action": "TRANSFER", "symbol": sym, "quantity": q,
                "date": day, "journal_pair": pair, "description": desc}

    def test_pairs_settle_in_their_account_only(self):
        from taxjson.lib import transfer_in as TI
        rows = [("b", "tt-journal", self._row("QZG.TO", -100, pair="p1")),
                ("b", "tt-journal", self._row("QZG.U.TO", 100, pair="p1")),
                ("a", "questrade", self._row("QZG.TO", 100))]
        self.assertEqual(TI.own_journal_legs(rows), ({0, 1}, set()))
        found = TI.arrivals(rows)
        self.assertEqual([(x.account, x.symbol, x.quantity) for x in found],
                         [("a", "QZG.TO", 100.0)])
        self.assertEqual(TI.movable_rows(rows), [rows[2]])

    def test_one_legged_line_settles_the_broker_leg_beside_it(self):
        from taxjson.lib import transfer_in as TI
        rows = [("m", "ib", self._row("QZD.TO", -50, day="2025-05-06")),
                ("m", "tt-journal", self._row("QZD.U.TO", 50, day="2025-05-07",
                                                pair="p")),
                ("o", "ib", self._row("QZD.TO", 50, day="2025-05-06"))]
        settled, orphans = TI.own_journal_legs(rows)
        self.assertEqual((settled, orphans), ({0, 1}, set()))
        # o's in-leg is an arrival: m's broker leg is m's journal's.
        self.assertEqual([x.account for x in TI.arrivals(rows)], ["o"])

    def test_an_orphan_leg_cancels_its_own_account_only(self):
        from taxjson.lib import transfer_in as TI
        rows = [("m", "questrade", self._row("QZD.TO", -50, pair="q1")),
                ("o", "questrade", self._row("QZD.TO", 50))]
        self.assertEqual(TI.own_journal_legs(rows), (set(), {0}))
        self.assertEqual([x.account for x in TI.arrivals(rows)], ["o"])
        self.assertEqual(TI.movable_rows(rows), [rows[1]])

    def test_rbc_reference_is_read_on_rbc_rows_only(self):
        from taxjson.lib.missing_history import journal_leg_key
        tfr = {"action": "TRANSFER", "date": "2025-05-06",
               "description": "TFR - QZD UNIT TRANSFER TO U$  J~0TFR1Q"}
        self.assertEqual(journal_leg_key(tfr),
                         ("ref", "2025-05-06", "0TFR1Q"))
        self.assertEqual(journal_leg_key(tfr, broker="rbc_direct"),
                         ("ref", "2025-05-06", "0TFR1Q"))
        # Another broker's row, or text that is no TFR row: no reference.
        self.assertIsNone(journal_leg_key(tfr, broker="questrade"))
        self.assertIsNone(journal_leg_key(dict(
            tfr, description="QZD MEMO J~0TFR1Q")))


# ------------------------------------------------------------------ H7

_OPT = {"canada": "QZK250620C00010000.TO", "usa": "QZK250620C00010000.US"}
_STK = {"canada": "QZK.TO", "usa": "QZK.US"}


class TestNoOptionJournalOrRename(unittest.TestCase):

    def test_parse_refuses_contracts(self):
        from taxjson.bin.taxjson_convert_tt import (parse_journal_line,
                                                    parse_rename_line)
        for bad in ("JOURNAL 2025-03-05 QZK250620C00010000.TO QZK.TO 1",
                    "JOURNAL 2025-03-05 QZK.TO QZK250620C00010000.TO 1",
                    "JOURNAL 2025-03-05 QZK.US F:QZK.US 1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                parse_journal_line(bad, "x.tt:4")
            self.assertIn("x.tt:4", str(cm.exception))
            self.assertIn("never journaled", str(cm.exception))
        for bad, word in (
                ("RENAME 2025-04-01 QZK250620C00010000.US QZK.US",
                 "into a share listing"),
                ("RENAME 2025-04-01 QZK.US QZK250620C00010000.US",
                 "from a share listing"),
                ("RENAME 2025-04-01 QZO250620C00010000.US "
                 "QZN250620C00010000.US", "RENAME 2025-04-01 QZO.US QZN.US"),
                ("RENAME 2025-04-01 QZO250620C00010000.US "
                 "QZO250620C00012000.US", "another contract")):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                parse_rename_line(bad, "x.tt:2")
            self.assertIn(word, str(cm.exception))
        # Two share listings still parse.
        self.assertEqual(parse_rename_line("RENAME 2025-04-01 QZO.US "
                                           "QZN.US")["new"], "QZN.US")

    def _check(self, country, line, word):
        tt = (f"BUYSELL 2025-03-03 10:00:00 {_OPT[country]} 1 "
              f"{'CAD' if country == 'canada' else 'USD'} 1.00 100.00 0.00 "
              f"x100\n" + line.format(o=_OPT[country], s=_STK[country]))
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": tt})[country]
            r = _run(self, root, ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("cannot be booked", _out(r))
            self.assertIn(word, _out(r))
            self.assertIn("inputs/margin/m.tt:2", _out(r))

    @rule("CA-XLIST-04")
    def test_canada_option_journal_stops_the_run(self):
        self._check("canada", "JOURNAL 2025-03-05 {o} {s} 1\n",
                    "never journaled")

    @rule("US-XLIST-03")
    def test_usa_option_journal_stops_the_run(self):
        self._check("usa", "JOURNAL 2025-03-05 {o} {s} 1\n",
                    "never journaled")

    @rule("CA-ACB-RENAME")
    def test_canada_option_rename_stops_the_run(self):
        self._check("canada", "RENAME 2025-04-01 {o} {s}\n",
                    "into a share listing")

    @rule("US-BASIS-RENAME")
    def test_usa_option_rename_stops_the_run(self):
        self._check("usa", "RENAME 2025-04-01 {o} {s}\n",
                    "into a share listing")


# ------------------------------------------------------------------ M2

class TestDeclaredJournalNeedsEvidence(unittest.TestCase):

    def test_verdict(self):
        from taxjson.lib import cross_listings as XL
        from taxjson.lib.symbol_codes import exact_name
        self.assertEqual(XL.listing_root("QZG.U.TO"), "QZG")
        self.assertEqual(XL.listing_root("QZG-U.TO"), "QZG")
        self.assertEqual(XL.listing_root("QZG.US"), "QZG")
        self.assertEqual(XL.listing_root("QZG.UN.TO"), "QZG.UN")
        self.assertEqual(XL.listing_root("QZG.B.TO"), "QZG.B")
        a, b = exact_name("QZALPHA MINES LTD"), exact_name("QZALPHA MINES")
        c = exact_name("QZBETA PHARMA INC")
        shown = {}
        # One root: joined, unless the names name two companies.
        self.assertEqual(XL.declared_verdict("QZG.TO", "QZG.U.TO", {},
                                             shown), "")
        self.assertEqual(XL.declared_verdict(
            "QZG.TO", "QZG.US", {"QZG.TO": {a}, "QZG.US": {c}}, shown),
            XL.DIFFERENT)
        # Two roots: the names must agree.
        self.assertEqual(XL.declared_verdict(
            "QZA.TO", "QZB.US", {"QZA.TO": {a}, "QZB.US": {b}}, shown), "")
        why = XL.declared_verdict("QZAAA.TO", "QZBBB.TO", {}, shown)
        self.assertIn("nothing shows QZAAA.TO and QZBBB.TO", why)
        self.assertIn("roots differ", why)

    def _check(self, country):
        x, cur = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}[country]
        tt = (f"BUYSELL 2025-03-03 10:00:00 QZAAA.{x} 100 {cur} 10.00 "
              f"1000.00 0.00\n"
              f"JOURNAL 2025-03-05 QZAAA.{x} QZBBB.{x} 1\n")
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": tt})[country]
            r = _run(self, root, ok=False)
            self.assertNotEqual(r.returncode, 0)
            out = _out(r)
            self.assertIn("join two listings that nothing shows are one "
                          "security", out)
            self.assertIn("inputs/margin/m.tt:2", out)
            self.assertRegex(out, rf"`TOBASE QZ(AAA|BBB)\.{x} "
                                  rf"QZ(AAA|BBB)\.{x}`")
            # `taxjson journals` lists it as pending, with the way out.
            doc = json.loads(cli(root, "journals", "--json").stdout)
            (j,) = doc["journals"]
            self.assertEqual((j["state"], j["pending"]), ("refused", True))
            self.assertIn("TOBASE", j["undo"])
            # The deliberate join: a TOBASE line, then the line books.
            (root / "ticker.map").write_text(
                f"TOBASE QZAAA.{x} QZBBB.{x}\n")
            _run(self, root)

    @rule("CA-XLIST-04")
    def test_canada_two_roots_with_no_evidence_stop_the_run(self):
        self._check("canada")

    @rule("US-XLIST-03")
    def test_usa_two_roots_with_no_evidence_stop_the_run(self):
        self._check("usa")


# ------------------------------------------------------------------ M5

class TestHubPartnersAgree(unittest.TestCase):
    """A listing two journals map onto joins its partners to each other
    only when they are one security too."""

    def _legs(self, a_name, b_name, hub_name="QZCO"):
        from test_fix_journal_pairing import _gambit, _names
        legs = (_gambit("2024-02-15", 100, a_name, frm="QZA.US",
                        to="QZX.TO")
                + _gambit("2024-06-12", 50, b_name, frm="QZB.US",
                          to="QZX.TO"))
        legs[1].name = legs[3].name = tuple(hub_name.split())
        legs[1].raw_name = legs[3].raw_name = hub_name
        names = _names(QZA_US=[a_name], QZB_US=[b_name],
                       QZX_TO=[hub_name])
        return legs, names

    def _check(self):
        from test_fix_journal_pairing import _run
        # PLC -> the hub, CORP -> the hub: each passes with the hub's
        # name (it states no form), but the PLC is not the CORP.
        r = _run(*self._legs("QZCO PLC", "QZCO CORP"))
        self.assertEqual(r["joined"], [])
        self.assertEqual({p.reason for p in r["suggested"]},
                         {"a listing pairs with two other listings"})
        # Partners that agree with each other: still a hub.
        r = _run(*self._legs("QZCO PLC", "QZCO PLC"))
        self.assertEqual(sorted((p.frm, p.to) for p in r["joined"]),
                         [("QZA.US", "QZX.TO"), ("QZB.US", "QZX.TO")])

    @rule("CA-XLIST-01")
    def test_canada_hub_partners_must_agree(self):
        self._check()

    @rule("US-XLIST-01")
    def test_usa_hub_partners_must_agree(self):
        self._check()


# ------------------------------------------------------------------ M6

class TestMigratedJournalLineWalksTheSame(unittest.TestCase):
    """M6, as the second pre-release review (finding 1) left it: a legacy
    JOURNAL line still names a journal on its days of opposite trades; a
    TOBASE line — the form format-map migrates it to — is no journal by
    itself (it hid a genuinely missing purchase), so a gambit with no
    journal evidence (no broker legs) reads as a short after the
    migration until a .tt JOURNAL line declares it. With the broker's
    legs (test_fix_journal_books.TestRbcGambitNeedsNoJournalLine) the
    migration changes nothing."""

    def test_only_journal_lines_are_read(self):
        from types import SimpleNamespace
        from taxjson.lib.missing_history import _journal_line_symbols
        tm = SimpleNamespace(journal={}, tobase={
            "QZG.U.TO": "QZG.TO",       # a fund's two currency lines
            "QZF.US": "QZF.TO"})        # an interlisted stock
        self.assertEqual(_journal_line_symbols(tm), set())
        tm.journal = {"QZG.U.TO": "QZG.TO"}
        self.assertEqual(_journal_line_symbols(tm), {"QZG.U.TO", "QZG.TO"})

    def _check(self, country):
        files = {
            "ticker.map": "JOURNAL QZG.U.TO QZG.TO  # gambit\n",
            # The gambit's day, by the broker's clock: the sale of the USD
            # line before the buy of the CAD line.
            "inputs/margin/m.tt": (
                "BUYSELL 2025-03-05 10:00:00 QZG.TO 100 CAD 10.00 1000.00 "
                "0.00\n"
                "BUYSELL 2025-03-05 09:00:00 QZG.U.TO -100 USD 7.50 750.00 "
                "0.00\n")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files, usa=_USA_CAD)[country]

            def state():
                r = _run(self, root)
                s = json.loads(cli(root, "sum", "--json").stdout)
                return ("no purchase in your files" in _out(r),
                        s.get("no_purchase_in_totals"), s["totals"])
            before = state()
            self.assertFalse(before[0])
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _out(w))
            self.assertIn("TOBASE QZG.U.TO QZG.TO",
                          (root / "ticker.map").read_text())
            self.assertTrue(state()[0])
            # The journal declared on its day: the walk reads it again.
            (root / "inputs" / "margin" / "j.tt").write_text(
                "JOURNAL 2025-03-05 QZG.TO QZG.U.TO 100\n")
            self.assertEqual(state(), before)

    @rule("CA-XLIST-04")
    def test_canada_migrated_journal_line_needs_its_evidence(self):
        self._check("canada")

    @rule("US-XLIST-03")
    def test_usa_migrated_journal_line_needs_its_evidence(self):
        self._check("usa")


# ------------------------------------------------------------------ lows

class TestTrailingFormOnly(unittest.TestCase):

    def _verdict(self, a, b):
        from test_fix_journal_pairing import _leg, _names
        from taxjson.lib import cross_listings as XL
        o = _leg("QZA.US", "2025-03-05", -10, a)
        i = _leg("QZA.TO", "2025-03-05", 10, b)
        names = _names(QZA_US=[a], QZA_TO=[b])
        shown = {t: " ".join(t) for v in names.values() for t in v}
        return XL._journal_names_verdict(o, i, names["QZA.US"],
                                         names["QZA.TO"], shown)

    def _check(self):
        self.assertEqual(self._verdict("QZALPHA MINES LTD", "QZALPHA MINES"),
                         "")
        self.assertEqual(self._verdict("THE QZCO COMPANY", "QZCO"), "")
        # A form word inside a name is a word of the name.
        self.assertIn("not equal", self._verdict("QZ SE ASIA FUND",
                                                 "QZ ASIA FUND"))
        self.assertIn("not equal", self._verdict("QZ SE ASIA FUND",
                                                 "QZ ASIA FUND INC"))
        # A partnership is never the company.
        self.assertIn("not equal", self._verdict("QZCO LP", "QZCO"))

    @rule("CA-XLIST-01")
    def test_canada_only_a_trailing_form_is_set_aside(self):
        self._check()

    @rule("US-XLIST-01")
    def test_usa_only_a_trailing_form_is_set_aside(self):
        self._check()


class TestCurrencyJournalRecordedOnce(unittest.TestCase):

    @rule("CA-XLIST-03")
    def test_brw_pair_and_its_tt_twin_are_one_record(self):
        from taxjson.lib import cross_listings as XL
        from taxjson.lib import dated_events as DE
        legs = [XL.Leg("m", "questrade", "QZD.TO", "2025-05-06", -100.0,
                       pair="2025-05-06#1", currency="CAD"),
                XL.Leg("m", "questrade", "QZD.U.TO", "2025-05-06", 100.0,
                       pair="2025-05-06#1", currency="USD")]
        j = DE.Journal("m", "2025-05-06", "QZD.TO", "QZD.U.TO", 100.0,
                       "inputs/m/j.tt:1", pair="tt:m:2025-05-06#1",
                       status=DE.STATUS_DUPLICATE, legs=())
        r = XL.analyze(legs, {}, {}, base_currency="CAD",
                       currency_journals=True, declared=[j], refused=[])
        self.assertEqual([(p.frm, p.to, p.source) for p in r["joined"]],
                         [("QZD.U.TO", "QZD.TO", "tt")])


class TestListingSuffixWindowIsBusinessDays(unittest.TestCase):

    def _check(self):
        from test_fix_qt_listing_suffix import _ev, _res, _scan
        # Out on a Friday, in the next Friday: 7 calendar days, 5
        # business days — the cross-listing pairing's window.
        r = _res(_scan(QZAX_US=("USD", "QZALPHA MINES CORP",
                                [("2026-09-11", 24.0)])),
                 _ev(outs=[("QZAA.US", "2026-09-04", 24,
                            "QZALPHA MINES CORP")]))
        self.assertEqual(r["corrected"]["QZAX.US"]["symbol"], "QZAX.TO")
        # Six business days: not the arrival.
        r = _res(_scan(QZAX_US=("USD", "QZALPHA MINES CORP",
                                [("2026-09-14", 24.0)])),
                 _ev(outs=[("QZAA.US", "2026-09-04", 24,
                            "QZALPHA MINES CORP")]))
        self.assertEqual(r["corrected"], {})

    @rule("CA-XLIST-02")
    def test_canada_window_counts_business_days(self):
        self._check()

    @rule("US-XLIST-02")
    def test_usa_window_counts_business_days(self):
        self._check()


class TestDeclaredLineChecks(unittest.TestCase):

    def test_future_dates_and_absurd_quantities(self):
        from taxjson.bin.taxjson_convert_tt import (parse_journal_line,
                                                    parse_rename_line)
        for bad, word in (
                ("JOURNAL 2099-03-05 QZG.TO QZG.U.TO 10", "in the future"),
                ("JOURNAL 2025-03-05 QZG.TO QZG.U.TO 1e308", "plausible"),
                ("JOURNAL 2025-03-05 QZG.TO QZG.U.TO inf", "not a number")):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as cm:
                parse_journal_line(bad, "x.tt:1")
            self.assertIn(word, str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            parse_rename_line("RENAME 2099-04-01 QZO.US QZN.US", "x.tt:2")
        self.assertIn("in the future", str(cm.exception))

    def _identical(self, country):
        x, cur = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}[country]
        a, b = {"TO": ("QZG.TO", "QZG.U.TO"), "US": ("QZG.US", "QZG.U.TO")}[x]
        line = f"JOURNAL 2025-03-05 {a} {b} 100\n"
        tt = (f"BUYSELL 2025-03-03 10:00:00 {a} 100 {cur} 10.00 1000.00 "
              f"0.00\n" + line + line
              + f"BUYSELL 2025-03-06 10:00:00 {b} -100 {cur} 8.00 800.00 "
              f"0.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": tt})[country]
            r = _run(self, root)
            self.assertIn("repeats inputs/margin/m.tt:2 word for word",
                          _out(r))
            from taxjson.lib import dated_events as DE
            side = json.loads(DE.sidecar_path(root / "work", "margin")
                              .read_text())["transactions"]
            self.assertEqual(len(side), 2)
            self.assertEqual(len(DE.read_state(root / "work" / DE.STATE)
                                 ["journals"]), 1)
            self.assertNotIn("go short", _out(r))
            self.assertAlmostEqual(_total(self, root), -200.0, delta=0.011)

    @rule("CA-XLIST-04")
    def test_canada_identical_lines_are_one_journal(self):
        self._identical("canada")

    @rule("US-XLIST-03")
    def test_usa_identical_lines_are_one_journal(self):
        self._identical("usa")

    def _partial(self, country):
        from test_fix_dated_events import _rbc_gambit
        rbc, tmap = _rbc_gambit(True)
        files = {"inputs/margin/rbc.csv": rbc, "ticker.map": tmap,
                 "inputs/margin/j.tt": ("JOURNAL 2025-05-06 QZD.TO "
                                        "QZD.U.TO 1500\n")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files, usa=_USA_CAD)[country]
            r = _run(self, root, ok=False)
            self.assertNotEqual(r.returncode, 0)
            out = _out(r)
            self.assertIn("already hold a journal of 1000", out)
            self.assertIn("(500)", out)
            self.assertIn("inputs/margin/j.tt:1", out)

    def _other_date(self, country):
        from test_fix_dated_events import _rbc_gambit
        rbc, tmap = _rbc_gambit(True)
        # Two days after the broker's journal: another journal, booked.
        files = {"inputs/margin/rbc.csv": rbc, "ticker.map": tmap,
                 "inputs/margin/j.tt": ("JOURNAL 2025-05-08 QZD.TO "
                                        "QZD.U.TO 1500\n")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files, usa=_USA_CAD)[country]
            r = _run(self, root)
            self.assertNotIn("already hold a journal", _out(r))
            from taxjson.lib import dated_events as DE
            rec = DE.read_state(root / "work" / DE.STATE)["journals"]
            self.assertEqual([(j["status"], j["quantity"]) for j in rec],
                             [("booked", 1500.0)])

    @rule("CA-XLIST-04")
    def test_canada_a_journal_on_another_date_is_booked(self):
        self._other_date("canada")

    @rule("US-XLIST-03")
    def test_usa_a_journal_on_another_date_is_booked(self):
        self._other_date("usa")

    @rule("CA-XLIST-04")
    def test_canada_a_bigger_restatement_of_the_brokers_journal_stops(self):
        self._partial("canada")

    @rule("US-XLIST-03")
    def test_usa_a_bigger_restatement_of_the_brokers_journal_stops(self):
        self._partial("usa")


class TestJournalsReadsADamagedState(unittest.TestCase):

    def test_no_crash_on_a_damaged_state(self):
        from taxjson.lib import cross_listings as XL
        ok = {"from": "QZD.U.TO", "to": "QZD.TO", "source": "broker",
              "out": {"account": "margin", "broker": "rbc_direct",
                      "symbol": "QZD.U.TO", "date": "2025-05-06",
                      "quantity": 10},
              "in": {"account": "margin", "broker": "rbc_direct",
                     "symbol": "QZD.TO", "date": "2025-05-06",
                     "quantity": 10}}
        docs = [{"joined": [1]}, {"joined": 1}, {"joined": {"a": 1}},
                {"joined": [{"out": 1, "in": "x"}]},
                {"joined": [dict(ok, names=7)]},
                {"suggested": [dict(ok, names=[1, None], out=[])]},
                {"refused": [dict(ok, refused=3, reason=[1])]}]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td)["canada"]
            (root / "work").mkdir()
            for d in docs:
                with self.subTest(doc=d):
                    (root / "work" / XL.STATE).write_text(json.dumps(
                        dict(d, format=XL.FORMAT)))
                    for args in (("journals", "--json"), ("journals",)):
                        r = cli(root, *args)
                        self.assertNotIn("Traceback", r.stderr)
                        self.assertIn(r.returncode, (0, 1), _out(r))


class TestRenameBetweenSpellingsOfOneListing(unittest.TestCase):
    """`RENAME <date> A.TO A.CN`: a Canadian venue folds into .TO, so the
    two are one listing in the books — a SPLIT between them would strand
    the pool. Nothing is booked; an Info line says so."""

    def test_parse(self):
        from taxjson.bin.taxjson_convert_tt import parse_rename_line
        r = parse_rename_line("RENAME 2025-04-01 QZA.TO QZA.CN", "x.tt:2")
        self.assertEqual((r["old"], r["new"]), ("QZA.TO", "QZA.TO"))
        self.assertIn("one listing", r["noop"])
        with self.assertRaises(ValueError):
            parse_rename_line("RENAME 2025-04-01 QZA.TO QZA.TO", "x.tt:2")

    def _check(self, country):
        tt = ("BUYSELL 2025-03-03 10:00:00 QZA.TO 100 CAD 10.00 1000.00 "
              "0.00\n"
              "RENAME 2025-04-01 QZA.TO QZA.CN\n"
              "BUYSELL 2025-05-06 10:00:00 QZA.TO -100 CAD 12.00 1200.00 "
              "0.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={"inputs/margin/m.tt": tt},
                                 usa=_USA_CAD)[country]
            r = _run(self, root, "--strict")
            self.assertIn("nothing to rename", _out(r))
            self.assertNotIn("go short", _out(r))
            base = json.loads((root / "work" / "margin_base.json")
                              .read_text())["transactions"]
            self.assertFalse([t for t in base if t["action"] == "SPLIT"])

    @rule("CA-ACB-RENAME")
    def test_canada_one_listing_rename_is_a_no_op(self):
        self._check("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_one_listing_rename_is_a_no_op(self):
        self._check("usa")


class TestLegacyMapRenameOfAContract(unittest.TestCase):
    """A legacy ticker.map dated RENAME naming an option or a future gets
    the .tt form's refusal."""

    def test_parse(self):
        from taxjson.bin.taxjson_ticker_map import _parse_map_text
        for line, word in (
                ("RENAME QZK250620C00010000.US QZK.US 2025-04-01",
                 "into a share listing"),
                ("RENAME QZO250620C00010000.US QZN250620C00010000.US "
                 "2025-04-01", "RENAME 2025-04-01 QZO.US QZN.US"),
                ("RENAME QZO250620C00010000.US QZO250620C00012000.US "
                 "2025-04-01", "another contract")):
            with self.subTest(line=line):
                tm, problems, _n = _parse_map_text(line + "\n",
                                                   "ticker.map")
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("ticker.map:1", problems[0])
                self.assertIn(word, problems[0])
                self.assertEqual(tuple(tm.dated), ())
        tm, problems, _n = _parse_map_text(
            "RENAME QZO.US QZN.US 2025-04-01\n", "ticker.map")
        self.assertEqual((problems, len(tm.dated)), ([], 1))

    def _check(self, country):
        x = {"canada": "TO", "usa": "US"}[country]
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": (
                    f"BUYSELL 2025-03-03 10:00:00 QZK.{x} 100 "
                    f"{'CAD' if x == 'TO' else 'USD'} 10.00 1000.00 0.00\n"),
                "ticker.map": (f"RENAME QZK250620C00010000.{x} QZK.{x} "
                               f"2025-04-01\n")})[country]
            r = _run(self, root, ok=False)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("into a share listing", _out(r))

    @rule("CA-ACB-RENAME")
    def test_canada_map_rename_of_a_contract_is_refused(self):
        self._check("canada")

    @rule("US-BASIS-RENAME")
    def test_usa_map_rename_of_a_contract_is_refused(self):
        self._check("usa")


if __name__ == "__main__":
    unittest.main()
