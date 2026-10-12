"""Pre-release review of v0.24.1, part b (synthetic data only).

H2  export coverage attributes the books' rows by the masked name the
    parse writes on each row (an IB statement saved under its account id
    shows the id masked in the books), not by the real file name.
L3  a transfer sidecar is read through the books' renames: a Questrade
    Norbert's gambit with its journal legs kept aside closes nothing open.
L4  an older Webull trading summary beside a current file whose end
    cannot be read: the rows past the summary's year date the end.
--  a broker's own rows that the account's books do not hold (a sale of
    calls an opening .tt line bought) are no position open at the broker;
    an RBC export ending early beside an IB statement names RBC's own
    positions only.
--  an option that expired after the export's end with no expiry row is
    named as such.
M4  a `checklist --done` mark on export-coverage / option-boundary
    answers the questions asked when it was made, for any end (an RBC
    as-of date too); a new gap, a later end or a new contract asks again.
L5  the cross-listing radar's "possible" pair strips only a trailing
    voting-share phrase ("NON STOP CORP" is not "STOP CORP").
L6  a sheltered account's election note: no tax in the account, its
    holdings still count for the superficial-loss (US: wash-sale) rule.
L8  the same broker's proof in another account re-reads transferred and
    reinvested units, never a purchase on a USD trade row.
--  `taxjson list [ACCOUNT] [YYYY-MM-DD]`.

Fake account ids only, pii-ok: 55500001, 55500002, U5550001.
"""
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from pathlib import Path
from unittest import mock

from _qa_project import console, tj
from _style import CapturedWidth
from tax_rules import rule

import test_fix_journal_books as JB
import test_fix_tobase_transfer_pair as TB
from test_fix_ibparse import HEAD as IB_HEAD, TRADES_H as IB_TRADES_H
from test_fix_newuser_signals import (QT_HEAD, WB_HEAD, WRITE_BUYBACK,
                                      cfg_of, ctx_of, flat, make, qt_row)
from test_fix_rbc import HDR as RBC_HDR, row as rbc_row

from taxjson.lib import checklist as cl
from taxjson.lib import export_coverage as EC
from taxjson.lib import listing_suffix as LS
from taxjson.lib import xlist_loss_radar as XR
from taxjson.lib.symbol_codes import exact_name

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def gaps(root, today):
    return [g.record() for g in EC.find_gaps(root, cfg_of(root),
                                             today=today)]


# ------------------------------------------------------------ fixtures
def ib_statement(end="June 30, 2026"):
    return (IB_HEAD + f'Statement,Data,Period,"January 1, 2026 - {end}"\n'
            + IB_TRADES_H
            + 'Trades,Data,Order,Stocks,USD,U5550001,QZN,'  # pii-ok
              '"2026-03-12, 10:00:00",250,20,0,-5000,0,0,0,0,O\n')


def rbc_export(as_of, rows):
    return f'"Activity Export as of {as_of} at 8:59:00 am ET"\n\n' \
        + RBC_HDR + rows


# QZF bought at RBC and still held; a long call an opening .tt bought
# before the data, sold at RBC.
RBC_ROWS = (
    rbc_row("March 4, 2026", "Buy", "QZF", "QZFOXTROT INC COM", "10", "20",
            "-200", "CAD", "QZFOXTROT INC COM UNSOLICITED DA")
    + rbc_row("January 29, 2026", "Sell", "8ZZZZZ2", "", "-8", "0.50",
              "400", "CAD", "CALL .QZB   01/15/27    50 QZBETA INC CA "
              "CLOSE CONTRACT", settle="January 30, 2026"))
OPENING_TT = ("BUYSELL 2025-08-06 10:00:00 QZB270115C00050000.TO 8 CAD "
              "0.25 200\n")


def qt_transfer_in(day, sym, qty, name, acct="55500001"):   # pii-ok
    return (f"{day},{day},TFI,{sym},{name} TRANSFER IN,{qty},0.00,0.00,0,"
            f"0.00,CAD,Transfers,{acct},Margin\n")


# ======================================================== H2: id-named
class TestIdNamedExports(unittest.TestCase):
    """A broker's default download name carries the account id; the
    books' rows carry it masked. Attributed all the same."""

    def test_ib_statement_named_with_the_account_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("activity.csv", "U5550001_2026.csv"):  # pii-ok
                root = make(tmp, name.split(".")[0], 2026,
                            {f"margin/{name}": ib_statement()})
                text = flat(console(tj(root, "run", "--no-input", "--details")))
                self.assertIn("Warning: Interactive Brokers exports for "
                              "margin end 2026-06-30 with open positions "
                              "(QZN.US 250)", text, name)
                g, = gaps(root, date(2026, 10, 8))
                self.assertEqual((g["broker"], g["how"]),
                                 ("ib", "statement"))

    def test_questrade_export_named_with_the_account_id(self):
        rows = (qt_row("2025-02-03", "2025-02-04", "Buy", "ZZD.TO", 100, 20,
                       "CAD", "ZZDELTA CORP")
                + qt_row("2025-06-02", "2025-06-03", "Buy", "ZZE.TO", 10, 20,
                         "CAD", "ZZECHO CORP"))
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "qt", 2025, {"margin/55500001.csv":  # pii-ok
                                          QT_HEAD + rows})
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertIn("Questrade exports for margin end 2025-06-02 with "
                          "open positions (ZZD.TO 100, ZZE.TO 10)", text)

    def test_only_a_sidecar_row_attributed_is_no_false_end(self):
        """The id-named file's own rows run to December; its transfer-in
        (kept aside, a taxable account's default) is the only row the old
        name lookup attributed — its date became the end."""
        rows = (qt_transfer_in("2025-03-03", "ZZT.TO", 40, "ZZTANGO CORP")
                + qt_row("2025-12-15", "2025-12-16", "Buy", "ZZU.TO", 10, 20,
                         "CAD", "ZZUNIFORM CORP"))
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "side", 2025, {"margin/55500001.csv":  # pii-ok
                                            QT_HEAD + rows})
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertNotIn("exports for margin end", text)
            self.assertEqual(gaps(root, date(2026, 3, 1)), [])

    def test_row_source_ids(self):
        name = "U5550001_2026.csv"                              # pii-ok
        sid = EC.file_source_id(name)
        self.assertTrue(sid.startswith("U5***_2026.csv#"), sid)
        row = {"source": "U5***_2026.csv", "source_key": sid.split("#")[1]}
        self.assertEqual(EC.row_source_ids(row)[0], sid)
        # The parse's `#2` for a second file showing the same name.
        row2 = dict(row, source="U5***_2026.csv#2")
        self.assertIn(sid, EC.row_source_ids(row2))
        idx = {}
        EC.add_source(idx, name, "ib")
        self.assertEqual(EC.source_broker(idx, row2), "ib")
        # A row with no key: the shown name while one broker shows it.
        self.assertEqual(EC.source_broker(idx, {"source": "U5***_2026.csv"}),
                         "ib")
        EC.add_source(idx, "U5550002_2026.csv", "questrade")   # pii-ok
        self.assertIsNone(EC.source_broker(idx,
                                           {"source": "U5***_2026.csv"}))


class TestTobaseTransferPair(unittest.TestCase):
    """The TOBASE fixture (IB moves QZAB.US out, Questrade books QZAA on
    a USD row in, sold later; both files named with the account id):
    the arrival leg is read through the map's renames, so nothing is
    open at Questrade."""

    def test_no_questrade_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            TB._project(root, "canada", "taxable", TB._Run.MAP, True)
            r = TB._run(root, "run", "--no-input", "--details")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("Questrade exports for",
                             flat(r.stdout + r.stderr))
            for today in (date(2026, 10, 8), date(2027, 3, 1)):
                self.assertEqual(
                    [g for g in gaps(root, today)
                     if g["broker"] == "questrade"], [], today)


# ================================================ L3: journal legs aside
class TestNorbertsGambitAside(unittest.TestCase):
    @rule("CA-XLIST-03")
    def test_journal_legs_meet_the_joined_listing(self):
        with tempfile.TemporaryDirectory() as td:
            root = JB._qt_project(td, None)          # transfers kept aside
            r = JB._run(root, "run", "--no-input", "--details")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("exports for margin end",
                             flat(r.stdout + r.stderr))
            self.assertEqual(gaps(root, date(2026, 3, 1)), [])


# ======================================================= L4: Webull year
class TestWebullSummaryBesideAnUnreadableEnd(unittest.TestCase):
    def test_rows_past_the_summary_year_date_the_end(self):
        summary = ("Account Number / Numero de compte:,,,,,,,,12345678,\n"
                   "Year / Annee:,,,,,,,,2024,\n"
                   "Report / Rapport:,,,,,,,,TRADING SUMMARY,\n\n" + WB_HEAD
                   + 'USD,22-04-2024,BUY,@ZZCC,ZZCC HOLDINGS INC,EQ,10,'
                     '20.00,"(200.00)"\n')
        current = (WB_HEAD
                   + 'USD,23-04-2025,BUY,@ZZDD,ZZDD HOLDINGS INC,EQ,10,'
                     '20.00,"(200.00)"\n'
                   + 'USD,16-12-2025,BUY,@ZZEE,ZZEE HOLDINGS INC,EQ,10,'
                     '20.00,"(200.00)"\n')
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "wb", 2025, {"margin/wb_2024.csv": summary,
                                          "margin/wb_2025.csv": current})
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertNotIn("Webull exports for margin end 2024-12-31",
                             text)
            self.assertEqual(gaps(root, date(2026, 3, 1)), [])


# ============================================ the broker's own positions
class TestBrokersOwnPositions(unittest.TestCase):
    """One account label, an RBC export as of April 28 and an IB
    statement for the year: only what RBC's own rows leave open — and
    the account's books hold — is named."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make(cls.tmp.name, "rbcib", 2026, {
            "margin/rbc.csv": rbc_export("Apr 28, 2026", RBC_ROWS),
            "margin/margin_start.tt": OPENING_TT,
            "margin/ib_2026.csv": ib_statement("December 31, 2026")})
        cls.r = tj(cls.root, "run", "--no-input", "--details")
        cls.text = flat(console(cls.r))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rbc_names_its_own_open_position_only(self):
        self.assertIn("Warning: RBC Direct Investing exports for margin end "
                      "2026-04-28 with open positions (QZF.TO 10); download "
                      "the rest of 2026", self.text)
        g, = [g for g in gaps(self.root, date(2026, 10, 8))
              if g["broker"] == "rbc_direct"]
        # IB's QZN is not RBC's; the call bought in the opening .tt and
        # sold at RBC is no written call open at RBC.
        self.assertEqual(g["positions"], [{"symbol": "QZF.TO",
                                           "quantity": 10.0}])
        self.assertEqual(g["expired"], [])

    def test_no_rbc_gap_when_its_rows_hold_nothing(self):
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "p"
        shutil.copytree(self.root, d)
        (d / "inputs" / "margin" / "rbc.csv").write_text(rbc_export(
            "Apr 28, 2026", RBC_ROWS.split("\n", 1)[1]))
        text = flat(console(tj(d, "run", "--no-input", "--details")))
        self.assertNotIn("RBC Direct Investing exports", text)
        self.assertEqual(gaps(d, date(2026, 10, 8)), [])

    def test_held_at_broker(self):
        own = [("A", -80.0), ("B", 10.0), ("C", 5.0), ("D", -2.0)]
        acct = [("B", 4.0), ("C", 9.0), ("D", -3.0)]
        self.assertEqual(EC.held_at_broker(own, acct),
                         [("B", 4.0), ("C", 5.0), ("D", -2.0)])


class TestExpiredInTheGap(unittest.TestCase):
    def test_named_as_expired_with_no_expiry_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "U_2026.csv").write_text(
                "Statement,Header,Field Name,Field Value\n"
                'Statement,Data,Period,"January 1, 2026 - April 28, '
                '2026"\n')
            (root / "work" / "margin_ib.json").write_text(json.dumps({
                "metadata": {"input_files": ["/x/U_2026.csv"]},
                "transactions": []}))
            rows = [{"action": "BUYSELL", "date": "2026-03-02",
                     "symbol": s, "quantity": q, "source": "U_2026.csv"}
                    for s, q in (("ZZF.US", 10),
                                 ("ZZG260619C00010000.US", 2),
                                 ("ZZG270115C00010000.US", -1))]
            (root / "work" / "margin_base.json").write_text(json.dumps({
                "transactions": rows}))
            cfg = {"settings": {"year": 2026},
                   "accounts": {"margin": {"type": "taxable"}}}
            g, = EC.find_gaps(root, cfg, today=date(2026, 10, 8))
            self.assertEqual(g.positions, [("ZZF.US", 10.0),
                                           ("ZZG270115C00010000.US", -1.0)])
            self.assertEqual(g.expired, [("ZZG260619C00010000.US", 2.0,
                                          "2026-06-19")])
            head, details = EC.message(g, 2026)
            self.assertIn("with open positions (ZZF.US 10, "
                          "ZZG270115C00010000.US -1) and an option that "
                          "expired after it with no expiry row "
                          "(ZZG260619C00010000.US 2, expired 2026-06-19)",
                          head)
            self.assertIn("the missing export holds the expiry, assignment "
                          "or buy-back row", " ".join(details))
            # Only expired options left: still a gap, not an Info.
            (root / "work" / "margin_base.json").write_text(json.dumps({
                "transactions": rows[1:2]}))
            g, = EC.find_gaps(root, cfg, today=date(2026, 10, 8))
            self.assertFalse(g.info)
            self.assertEqual(g.positions, [])


# ===================================================== M4: marks per key
class TestExportCoverageMarkPerGap(unittest.TestCase):
    """An explicit end (RBC's as-of date) is a question too; a DONE mark
    answers the (account, broker, end) it was made for."""

    def test_mark_answers_its_end_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "mark", 2026, {
                "margin/rbc.csv": rbc_export("Apr 28, 2026", RBC_ROWS),
                "margin/margin_start.tt": OPENING_TT})
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertIn("No RBC Direct Investing activity in margin after "
                          "2026-04-28? Mark `taxjson checklist --done "
                          "export-coverage`", text)
            ctx = ctx_of(root, 2026, date(2026, 10, 8))
            res = cl.d_export_coverage(ctx)
            self.assertTrue(res.question)
            self.assertEqual(res.answers, ["margin|rbc_direct|2026-04-28"])
            r = tj(root, "checklist", "--done", "export-coverage",
                   check=False)
            self.assertIn("it answers: RBC Direct Investing for margin to "
                          "2026-04-28", flat(r.stdout))
            st = json.loads((root / cl.STATE_FILE).read_text())
            self.assertEqual(st["overrides"]["export-coverage"]["answers"],
                             ["margin|rbc_direct|2026-04-28"])
            res, = cl.evaluate(ctx, only=["export-coverage"])
            self.assertEqual(res.effective, "done")
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertNotIn("Warning: RBC Direct Investing exports", text)
            self.assertIn("marked done in checklist.json (export-coverage)",
                          text)
            # A later export, still short: a new end, a new question.
            (root / "inputs" / "margin" / "rbc.csv").write_text(rbc_export(
                "Jun 30, 2026", RBC_ROWS))
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertIn("Warning: RBC Direct Investing exports for margin "
                          "end 2026-06-30", text)
            res, = cl.evaluate(ctx, only=["export-coverage"])
            self.assertEqual(res.effective, "attention")
            self.assertIn("not answered by the done mark", res.detail)
            self.assertIn("RBC Direct Investing for margin to 2026-06-30",
                          res.detail)

    def test_legacy_mark_without_answers_answers_nothing(self):
        r = cl.Result("export-coverage", "attention", "x", question=True,
                      answers=["a|b|2026-01-01"])
        cl.apply_override(r, {"status": "done", "date": "2026-10-01"})
        self.assertEqual(r.effective, "attention")
        r = cl.Result("export-coverage", "attention", "x", question=True,
                      answers=["a|b|2026-01-01"])
        cl.apply_override(r, {"status": "skipped"})
        self.assertEqual(r.effective, "skipped")
        r = cl.Result("export-coverage", "attention", "x", question=True,
                      answers=["a|b|2026-01-01"])
        cl.apply_override(r, {"status": "done",
                              "answers": ["a|b|2026-01-01"]})
        self.assertEqual(r.effective, "done")


class TestOptionMarkPerContract(unittest.TestCase):
    @rule("CA-OPT-11")
    def test_a_new_contract_is_asked_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make(tmp, "opt", 2026, {"margin/book.tt": WRITE_BUYBACK})
            tj(root, "run", "--no-input", "--details")
            tj(root, "checklist", "--done", "option-boundary", check=False)
            ctx = ctx_of(root, 2026, date(2026, 10, 1))
            res, = cl.evaluate(ctx, only=["option-boundary"])
            self.assertEqual(res.effective, "done")
            # A second contract written in 2025, closed in 2026.
            other = WRITE_BUYBACK.replace("ZZQ261218C00050000",
                                          "ZZR261218C00060000")
            (root / "inputs" / "margin" / "more.tt").write_text(other)
            text = flat(console(tj(root, "run", "--no-input", "--details")))
            self.assertIn("Warning: 1 option contract you wrote in 2025 and "
                          "closed in 2026 is on transition close timing",
                          text)
            self.assertIn("answered in checklist.json (option-boundary "
                          "marked done)", text)
            self.assertIn("ZZR261218C00060000.TO", text)
            res, = cl.evaluate(ctx, only=["option-boundary"])
            self.assertEqual(res.effective, "attention")
            self.assertIn("ZZR261218C00060000.TO written 2025-12-15",
                          res.detail)


# ============================================================ L5: radar
class TestRadarTrailingVotingPhrase(unittest.TestCase):
    @staticmethod
    def k(*names):
        return {exact_name(n) for n in names}

    @rule("CA-XLIST-05")
    def test_a_word_of_the_name_is_not_share_wording(self):
        a, b = self.k("NON STOP CORP"), self.k("STOP CORP")
        self.assertIsNone(XR._wording_only(a, b, a, b, "ZZS.US", "ZZS.TO"))
        a, b = self.k("RESTRICTED BRANDS INC"), self.k("BRANDS INC")
        self.assertIsNone(XR._wording_only(a, b, a, b, "ZZS.US", "ZZS.TO"))
        a, b = (self.k("MULTIPLE HOLDINGS INC COM"),
                self.k("HOLDINGS INC SUBORD VTG SHS"))
        self.assertIsNone(XR._wording_only(a, b, a, b, "ZZS.US", "ZZS.TO"))

    @rule("CA-XLIST-05")
    def test_a_trailing_phrase_is(self):
        com = self.k("ZZSTOP CORP")
        for w in ("ZZSTOP CORP SUBORD VTG SHS",
                  "ZZSTOP CORP MULTIPLE VOTING SHARES",
                  "ZZSTOP CORP RESTRICTED VOTING",
                  "ZZSTOP CORP NON-VOTING SHS"):
            b = self.k(w)
            self.assertIsNotNone(XR._wording_only(com, b, com, b, "ZZS.US",
                                                  "ZZS.TO"), w)

    @rule("US-XLIST-04")
    def test_same_test_in_a_us_project(self):
        a, b = self.k("NON STOP CORP"), self.k("STOP CORP")
        self.assertIsNone(XR._wording_only(a, b, a, b, "ZZS.US", "ZZS.TO"))


# ================================================ L6: sheltered election
class TestShelteredElectionNote(unittest.TestCase):
    def _event(self):
        from taxjson.lib.corp_actions import CorporateAction
        return CorporateAction(
            date="2025-05-01", time="00:00:00", action_type="spinoff",
            source_symbol="ZZK.TO", source_isin="", target_symbol="ZZL.TO",
            target_isin="", ratio_new=1, ratio_old=10, qty_disposed=0,
            qty_received=10, fmv=0.0, currency="CAD",
            target_currency="CAD", account="rrsp", event_id="ev1")

    def _prompt(self, country):
        from taxjson.bin import taxjson_corp_actions as CA
        err = io.StringIO()
        with mock.patch("builtins.input", side_effect=["1", "0", "y", ""]), \
                redirect_stderr(err):
            CA._prompt_election(self._event(), country, sheltered=True)
        return flat(err.getvalue())

    def test_canada_names_the_superficial_loss_rule(self):
        text = self._prompt("canada")
        self.assertIn("Sheltered account: this election sets the holdings' "
                      "cost in the books — no tax in this account; its "
                      "holdings still count for the superficial-loss rule.",
                      text)
        self.assertNotIn("wash-sale", text)
        self.assertNotIn("taxed either way", text)

    def test_usa_names_the_wash_sale_rule(self):
        text = self._prompt("usa")
        self.assertIn("its holdings still count for the wash-sale rule.",
                      text)
        self.assertNotIn("superficial", text)


# ========================================= L8: the same broker's proof
def _scan(listing, cur, name, arrivals=(), bought=False):
    s = LS.Scan()
    c = s.candidate(listing, cur)
    c.add_name(name)
    c.arrivals += list(arrivals)
    c.bought = bought
    s.saw(listing, cur, name)
    return s


class TestBrokerProofScope(unittest.TestCase):
    NAME = "QZALPHA MINES CORP"

    def _ev(self):
        ev = LS.Evidence()
        ev.proved["QZAX.US"] = [("tfsa", "questrade", "QZAX.TO",
                                 frozenset([exact_name(self.NAME)]))]
        return ev

    def _res(self, scan):
        return LS.resolve(scan, self._ev(), account="lira",
                          broker="questrade")

    @rule("CA-XLIST-02")
    def test_a_usd_purchase_keeps_the_us_listing(self):
        r = self._res(_scan("QZAX.US", "USD", self.NAME, bought=True))
        self.assertEqual(r["corrected"], {})

    @rule("CA-XLIST-02")
    def test_reinvested_units_are_read_as_the_tsx_listing(self):
        r = self._res(_scan("QZAX.US", "USD", self.NAME))
        got = r["corrected"]["QZAX.US"]
        self.assertEqual((got["symbol"], got["how"]), ("QZAX.TO", "broker"))

    @rule("US-XLIST-02")
    def test_same_scope_in_a_us_project(self):
        r = self._res(_scan("QZAX.US", "USD", self.NAME, bought=True))
        self.assertEqual(r["corrected"], {})

    def test_scan_marks_a_purchase(self):
        qh = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")

        def q(action, qty, act):
            return (f"2026-09-10 12:00:00 AM,2026-09-10 12:00:00 AM,{action},"
                    f"QZAX,{self.NAME} WE ACTED AS AGENT,{qty},10,0,0,0,USD,"
                    f"55500002,{act},Individual RRSP\n")         # pii-ok
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "q.csv"
            p.write_text(qh + q("REI", 1, "Dividend reinvestment")
                         + q("Sell", -1, "Trades"))
            self.assertFalse(LS.scan_questrade([p]).cands["QZAX.US"].bought)
            p.write_text(qh + q("Buy", 10, "Trades"))
            self.assertTrue(LS.scan_questrade([p]).cands["QZAX.US"].bought)


# ================================================ taxjson list YYYY-MM-DD
class TestListPositionalDate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = make(cls.tmp.name, "list", 2026, {
            "margin/book.tt":
                "BUYSELL 2026-01-05 10:00:00 ZZA.TO 10 CAD 20 200\n"
                "BUYSELL 2026-03-02 10:00:00 ZZA.TO -4 CAD 25 100\n"})
        tj(cls.root, "run", "--no-input", "--details")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _json(self, *args):
        return json.loads(tj(self.root, "list", *args, "--json").stdout)

    def test_a_date_word_is_the_date(self):
        want = self._json("margin", "--date", "2026-02-01")
        self.assertEqual(self._json("margin", "2026-02-01"), want)
        self.assertEqual(self._json("2026-02-01", "margin"), want)
        self.assertEqual(self._json("2026-02-01"),
                         self._json("--date", "2026-02-01"))
        self.assertNotEqual(self._json("margin"), want)

    def test_two_dates_stop(self):
        r = tj(self.root, "list", "2026-02-01", "2026-03-01", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("one account and one date", flat(r.stderr))
        r = tj(self.root, "list", "2026-02-01", "--date", "2026-03-01",
               check=False)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
