"""Pre-release review 022 (synthetic QZ-style data only).

M2  a name-only truncation match needs width evidence: a complete last
    word that starts a longer word is not a cut.
M3  a broker's event wording is cut only from Questrade descriptions, and
    a designator in the cut part is carried / refuses one-sided tolerance.
M4  a long whitespace run in a name is collapsed before any regex.
L3  a listing joined / suggested must be one ticker.map token.
M5  a short carried through a rename keeps its pair in the year scope.
L1  a quoted broker description in a .diag is never a suggestion.
L2  `ticker-map --write` keeps a symlinked ticker.map a link (mode kept).
L4  --outside-year counts only its own entries, refuses a non-object
    entry before writing, and keeps a symlinked file a link.
L5  the wash radar honours --transfers-as-acquisitions.
M1  the default transfer policy lists the netted moves (cross-account and
    zero-net clusters) inside a loss's window in its one warning; the
    main-book SPLIT guard holds in cross-account netting.
"""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import redirect_stderr

from taxjson.lib import symbol_codes as SC
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from tax_rules import rule
from tax_rules.dual import gains_both, tx


def _ok_listing(listing, cur):
    return QuestradeBrokerage().apply_currency_suffix(listing, cur) == listing


def _use(code, desc, cut=False):
    return SC.CodeUse(code=code, name=SC.questrade_name(desc),
                      currencies=["CAD"], arrivals=[], rows=1, name_cut=cut)


def _entry(sym, name, cut=False):
    return SC.NameEntry(sym, SC.name_tokens(name), name, "tfsa",
                        "questrade", cut=cut)


def _res(uses, names=(), outs=()):
    return SC.resolve(uses, list(outs), list(names), listing_ok=_ok_listing)


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestM2TruncationNeedsWidthEvidence(unittest.TestCase):
    def test_complete_word_prefix_is_not_a_cut(self):
        for short, full in (
                ("QZCO HEALTH CARE PARTNERS",
                 "QZCO HEALTH CARE PARTNERSHIP INCOME FUND"),
                ("QZCO GLOBAL GROWTH EQUITY",
                 "QZCO GLOBAL GROWTH EQUITYPLUS FUND")):
            with self.subTest(short=short):
                self.assertFalse(SC.names_agree(
                    short, full, "name_only", same_broker=True)[0])
                r = _res([_use("X000021", short)],
                         names=[_entry("QZHC.TO", full)])
                self.assertEqual(r["resolved"], {})

    def test_mid_word_cut_without_width_evidence_is_not_a_cut(self):
        short = "QZCO HEALTH CARE PARTNERSH"
        full = "QZCO HEALTH CARE PARTNERSHIP INCOME FUND"
        self.assertFalse(SC.names_agree(short, full, "name_only",
                                        same_broker=True)[0])
        # With the export's width as evidence it is the cut name.
        self.assertTrue(SC.names_agree(short, full, "name_only",
                                       same_broker=True, a_cut=True)[0])

    def test_desc_cut_is_the_width_evidence(self):
        d60 = ("QZCO HEALTH CARE PARTNERSHIP INCOME FUND SERIES QZ "
               "TRUST UNI")
        self.assertEqual(len(d60), SC.QT_DESC_WIDTH)
        self.assertTrue(SC.desc_cut(d60, SC.questrade_name(d60)))
        self.assertFalse(SC.desc_cut("QZCO HEALTH CARE PARTNERS",
                                     "QZCO HEALTH CARE PARTNERS"))


@rule("CA-ACB-CODES")
@rule("US-BASIS-CODES")
class TestM3WordingIsQuestradeOnly(unittest.TestCase):
    def test_other_brokers_names_keep_their_class(self):
        toks = SC.name_tokens("QZCO INC COMMON STOCK CLASS C")
        self.assertIn("~C", toks)
        self.assertNotEqual(toks, SC.name_tokens("QZCO INC"))
        # IB's wording is never cut as Questrade's event wording.
        self.assertIn("~C", SC.name_tokens(
            "QZCO INC CASH DIV ON CLASS C"))

    def test_questrade_common_stock_tail_carries_the_class(self):
        self.assertEqual(SC.questrade_name("QZCO INC COMMON STOCK CLASS C"),
                         "QZCO INC CL C")
        self.assertEqual(SC.questrade_name(
            "QZCO INC COMMON STOCK CLASS C CASH DIV ON 49 SHS REC "
            "09/21/26 PAY 09/23/26"), "QZCO INC CL C")
        # Event wording carries nothing (NON-RES TAX is not a designator).
        self.assertEqual(SC.questrade_name(
            "QZCO INC COMMON STOCK NON-RES TAX WITHHELD ON 49 SHS"),
            "QZCO INC")
        self.assertEqual(SC.questrade_name(
            "QZCO INC SUBST PAY ON 41 SHS IN LIEU OF DIVIDEND"), "QZCO INC")

    def test_class_c_never_pairs_class_a_through_the_cut(self):
        leg = SC.OutLeg("ibm", "ib", "QZCA.TO", "2026-09-02", 12, "CAD",
                        [SC.name_tokens("QZCO INC CL A")])
        u = SC.CodeUse(code="X000031",
                       name=SC.questrade_name("QZCO INC COMMON STOCK CLASS "
                                              "C TRANSFER IN"),
                       currencies=["CAD"], arrivals=[("2026-09-05", 12.0)],
                       rows=1)
        r = _res([u], outs=[leg])
        self.assertEqual(r["resolved"], {})
        self.assertEqual(r["unresolved"]["X000031"]["reason"],
                         "class_differs")

    def test_designator_in_cut_boilerplate_refuses_one_sided(self):
        # The cut "CL A ORD" named the share: the other side's lack of a
        # designator is no longer tolerated.
        a = "QZX HLDGS ADS EACH RPRSNTNG ONE CL A ORD"
        b = "QZX HOLDINGS"
        self.assertFalse(SC.names_agree(a, b, "pairing")[0])
        self.assertTrue(SC.names_agree("QZX HLDGS SPONSORED ADR REPSTG 5 "
                                       "COM", b, "pairing")[0])

    def test_one_sided_tolerance_only_for_a_unique_pairing(self):
        def legs(*names):
            return [SC.OutLeg("ibm", "ib", sym, "2026-09-02", 12, "CAD",
                              [SC.name_tokens(n)]) for sym, n in names]
        u = SC.CodeUse(code="X000032", name=SC.questrade_name(
            "QZNU HOLDINGS LTD CLASS A ORDINARY SHARES TRANSFER IN"),
            currencies=["CAD"], arrivals=[("2026-09-05", 12.0)], rows=1)
        # Unique: the one-sided ORDINARY / class letter is tolerated.
        r = _res([u], outs=legs(("QZNU.TO", "QZNU HOLDINGS LTD")))
        self.assertEqual(r["resolved"]["X000032"]["symbol"], "QZNU.TO")
        # Another leg pairs by quantity and date: not inferred.
        r = _res([u], outs=legs(("QZNU.TO", "QZNU HOLDINGS LTD"),
                                ("QZOT.TO", "QZOTHER MINING LTD")))
        self.assertEqual(r["resolved"], {})


class TestM4Whitespace(unittest.TestCase):
    def test_long_whitespace_runs_are_fast(self):
        name = "QZCO" + " " * 40000 + "INC" + " " * 40000 + "X"
        t0 = time.monotonic()
        SC.name_tokens(name)
        SC.name_tokens(name, cut=True)
        SC.exact_name(name)
        SC.questrade_name(name)
        SC.names_agree(name, "QZCO INC", "name_only", same_broker=True,
                       a_cut=True)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(SC.name_words(name)[0], ("QZCO", "INC", "X"))


class TestL3ListingTokens(unittest.TestCase):
    def test_plain_listing_is_one_map_token(self):
        self.assertTrue(SC._plain_listing("QZCO.TO"))
        self.assertTrue(SC._plain_listing("QZCO.U.TO"))
        for bad in ("QZ#CO.TO", "QZCO.TO\u2028GLOBAL", "QZCO.TO\n",
                    "QZ CO.TO", "-QZ.TO", "QZ\u00e9.TO", "QZ`X.TO"):
            with self.subTest(bad=bad):
                self.assertFalse(SC._plain_listing(bad))


def _m1_book():
    """The reviewer's case: a taxable loss on 2025-04-15; 100 shares
    leave the RRSP on 04-20 and arrive in the TFSA on 04-21."""
    main = [tx("BUYSELL", "2025-01-10", "SAMPXF.US", 100, -5000.0),
            tx("BUYSELL", "2025-04-15", "SAMPXF.US", -100, 4000.0)]
    shel = [tx("TRANSFER", "2025-04-20", "SAMPXF.US", -100, 0.0,
               account="rrsp"),
            tx("TRANSFER", "2025-04-21", "SAMPXF.US", 100, 0.0,
               account="tfsa")]
    return main, shel


class TestM1NettedMovesInLossWindows(unittest.TestCase):
    @rule("CA-SL-16")
    @rule("US-WASH-23")
    def test_cross_account_move_in_the_window_is_listed(self):
        main, shel = _m1_book()
        r = gains_both(main, sheltered=shel, year=2025)
        for c, rule_words in (("canada", "the loss may be superficial "
                               "(s.54)"),
                              ("usa", "the loss may be a wash sale")):
            with self.subTest(country=c):
                self.assertEqual(r[c]["transfers_in_loss_windows"], [{
                    "kind": "move", "account": "rrsp→tfsa",
                    "symbol": "SAMPXF.US", "from_accounts": ["rrsp"],
                    "to_accounts": ["tfsa"], "qty": 100.0,
                    "date": "2025-04-20", "date_end": "2025-04-21",
                    "loss_date": "2025-04-15", "loss_account": "margin"}])
                err = " ".join(r[c]["_stderr"].split())
                self.assertIn("1 transfer in a taxable loss's 30-day "
                              "window counted as an account move", err)
                self.assertIn("SAMPXF.US 100 moved rrsp→tfsa 2025-04-20/21 "
                              "inside the 2025-04-15 loss window in margin "
                              "— if one leg was a contribution, "
                              + rule_words, err)
                # Still a move: the loss stands (the user decides).
                self.assertAlmostEqual(r[c]["summary"]["total_gain"],
                                       -1000.0, places=2)

    @rule("CA-SL-16")
    @rule("US-WASH-23")
    def test_move_outside_the_window_is_not_listed(self):
        main, _ = _m1_book()
        shel = [tx("TRANSFER", "2025-06-20", "SAMPXF.US", -100, 0.0,
                   account="rrsp"),
                tx("TRANSFER", "2025-06-21", "SAMPXF.US", 100, 0.0,
                   account="tfsa")]
        r = gains_both(main, sheltered=shel, year=2025)
        for c in r:
            self.assertNotIn("transfers_in_loss_windows", r[c])

    @rule("CA-SL-17")
    @rule("US-WASH-24")
    def test_strict_policy_unchanged(self):
        from taxjson.lib.core import AmbiguousTransferDateError
        from taxjson.lib.pipeline import GainsRequest, run_gains
        main, shel = _m1_book()
        for c in ("canada", "usa"):
            with self.subTest(country=c), \
                    redirect_stderr(io.StringIO()), \
                    self.assertRaises(AmbiguousTransferDateError):
                run_gains(list(main), list(shel), (), GainsRequest(
                    country=c, year=2025, taxable=True,
                    transfers_as_acquisitions=True))

    def test_main_book_split_blocks_cross_account_netting(self):
        from taxjson.lib.pipeline import _net_cross_account_transfers
        shel = [tx("TRANSFER", "2025-06-01", "SAMPXF.US", -100, 0.0,
                   account="rrsp"),
                tx("TRANSFER", "2025-06-03", "SAMPXF.US", 100, 0.0,
                   account="tfsa")]
        split = [tx("SPLIT", "2025-06-02", "SAMPXF.US", 2, 0.0)]
        for guard in (False, True):
            with self.subTest(near_trade_guard=guard):
                got = _net_cross_account_transfers(
                    list(shel), main_transactions=split,
                    near_trade_guard=guard)
                self.assertEqual(len(got), 2)
        # Without the split the default policy nets it.
        self.assertEqual(_net_cross_account_transfers(
            list(shel), main_transactions=[], near_trade_guard=False), [])


REPO = Path(__file__).resolve().parents[1]


def _env():
    return dict(os.environ, PYTHONPATH=str(REPO / "src"),
                TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")


@rule("CA-ACB-11")
@rule("US-BASIS-04")
class TestM5RenamedShort(unittest.TestCase):
    def _book(self, cover_year="2025"):
        return [tx("BUYSELL", "2024-03-01", "QZOLD.TO", -10, 100),
                tx("SPLIT", "2024-06-01", "QZOLD.TO", 1, 0,
                   symbol_new="QZNEW.TO"),
                tx("BUYSELL", f"{cover_year}-03-01", "QZNEW.TO", 10, 90)]

    def test_successor_cover_bears_on_the_year(self):
        from taxjson.lib.missing_history import classify_year_shorts
        for c in ("canada", "usa"):
            with self.subTest(country=c):
                rows = classify_year_shorts(
                    self._book(), 2025, country=c,
                    registered={"margin": False}, date_basis="trade")
                r = rows[("QZOLD.TO", "margin")]
                self.assertTrue(r.affects_year)
                self.assertTrue(r.in_year_activity)
                self.assertTrue(r.short_at_year_start)
                self.assertTrue(r.year_listed)
                # (the successor's cover is no sale: QA F4)
                self.assertEqual(r.in_year_dispositions, 0)
        # Covered in 2024: nothing of 2025 draws on it.
        rows = classify_year_shorts(
            self._book("2024"), 2025, country="canada",
            registered={"margin": False}, date_basis="trade")
        self.assertFalse(rows[("QZOLD.TO", "margin")].year_listed)

    def test_chain_and_pool(self):
        from taxjson.lib.missing_history import (classify_year_shorts,
                                                  rename_successors)
        book = [tx("BUYSELL", "2024-03-01", "QZA.TO", -10, 100),
                tx("SPLIT", "2024-05-01", "QZA.TO", 1, 0,
                   symbol_new="QZB.TO"),
                tx("SPLIT", "2024-07-01", "QZB.TO", 1, 0,
                   symbol_new="QZC.TO"),
                # another taxable account trades the new symbol in 2025
                tx("BUYSELL", "2025-02-01", "QZC.TO", 5, 50,
                   account="cash"),
                tx("BUYSELL", "2025-03-01", "QZC.TO", -5, 60,
                   account="cash")]
        self.assertEqual(rename_successors(book)[("QZA.TO", "margin")],
                         {"QZB.TO", "QZC.TO"})
        rows = classify_year_shorts(
            book, 2025, country="canada",
            registered={"margin": False, "cash": False},
            date_basis="trade")
        r = rows[("QZA.TO", "margin")]
        self.assertEqual(r.pooled_with, ("cash",))
        self.assertTrue(r.year_listed)
        rows = classify_year_shorts(
            book, 2025, country="usa",
            registered={"margin": False, "cash": False},
            date_basis="trade")
        self.assertFalse(rows[("QZA.TO", "margin")].year_listed)


class TestL1QuotedDescriptions(unittest.TestCase):
    def test_quoted_rule_is_not_a_suggestion(self):
        from taxjson.lib import ticker_map_suggest as TS
        diag = (
            "warning: ATTENTION: q.csv line 4: TFI row keeps internal "
            "symbol code 'X000002' ('QZ `GLOBAL QZA.TO QZB.TO` CO "
            "TRANSFER IN')\n"
            "  No trade resolves it (\"QZ'S add to ticker.map:  GLOBAL "
            "QZC.TO QZD.TO\") — add `GLOBAL X000002.TO QZN.TO` to "
            "ticker.map if it is the same security.\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "qt_questrade.json.diag").write_text(diag)
            got = [s.line for s in TS.gather(root)]
        self.assertEqual(got, ["GLOBAL X000002.TO QZN.TO"])

    def test_symbol_codes_detail_quote_is_skipped(self):
        from taxjson.lib import ticker_map_suggest as TS
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / f"qt{SC.SUFFIX}").write_text(json.dumps({
                "format": SC.FORMAT, "account": "qt", "resolved": {},
                "mapped": {}, "unresolved": {"X000003": {
                    "reason": "no_evidence",
                    "detail": "looks like QZE.TO by name ('QZ `GLOBAL "
                              "QZF.TO QZG.TO`' on Questrade rows), not "
                              "applied — add `GLOBAL X000003.TO QZE.TO` "
                              "to ticker.map if right"}}}))
            got = [s.line for s in TS.from_symbol_codes(cache)]
        self.assertEqual(got, ["GLOBAL X000003.TO QZE.TO"])


_SUGGEST_DIAG = ("warning: ATTENTION: q.csv: Questrade symbol SAMPA.TO looks "
                 "renamed to SAMPB.TO — if they are one security add to "
                 "ticker.map:  GLOBAL SAMPA.TO SAMPB.TO\n")


class TestL2TickerMapWriteKeepsTheLink(unittest.TestCase):
    def _project(self, tmp):
        root = Path(tmp) / "p"
        (root / "work").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\ncountry = "canada"\nbase_currency = "CAD"\n'
            'year = 2025\n\n[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_questrade.json.diag").write_text(
            _SUGGEST_DIAG)
        # The parse the hint came from: a conditional hint ("if they are
        # one security") is offered only when the books hold both symbols.
        (root / "work" / "margin_questrade.json").write_text(json.dumps(
            {"metadata": {}, "transactions": [
                {"symbol": "SAMPA.TO"}, {"symbol": "SAMPB.TO"}]}))
        return root

    def _write(self, root):
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "ticker-map", "--suggest", "--write", "--all"],
            capture_output=True, text=True, env=_env(),
            stdin=subprocess.DEVNULL, timeout=300)

    def test_link_inside_the_project_stays_a_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "maps").mkdir()
            real = root / "maps" / "mine.map"
            real.write_text("DISTINCT QZA.TO QZB.TO\n")
            os.chmod(real, 0o644)
            (root / "ticker.map").symlink_to("maps/mine.map")
            r = self._write(root)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertTrue((root / "ticker.map").is_symlink())
            self.assertTrue(real.read_text().endswith(
                "\nGLOBAL SAMPA.TO SAMPB.TO\n"))
            self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o644)
            self.assertEqual(list((root / "maps").glob("mine.map.bak*"))[0]
                             .read_text(), "DISTINCT QZA.TO QZB.TO\n")

    def test_link_leaving_the_project_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            outside = Path(tmp) / "elsewhere.map"
            outside.write_text("DISTINCT QZA.TO QZB.TO\n")
            (root / "ticker.map").symlink_to(outside)
            r = self._write(root)
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            self.assertIn("outside the project", " ".join(r.stderr.split()))
            self.assertEqual(outside.read_text(), "DISTINCT QZA.TO QZB.TO\n")
            self.assertTrue((root / "ticker.map").is_symlink())

    def test_regular_file_keeps_its_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            tm = root / "ticker.map"
            tm.write_text("DISTINCT QZA.TO QZB.TO\n")
            os.chmod(tm, 0o640)
            r = self._write(root)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(stat.S_IMODE(tm.stat().st_mode), 0o640)
            self.assertTrue((root / "ticker.map.bak").is_file())


class TestL4OutsideYearWrites(unittest.TestCase):
    """The mhscope project: QPAS / QOPN (margin) and QRGL (rrsp) went
    short in 2024 and never trade again."""

    @classmethod
    def setUpClass(cls):
        import test_fix_mhscope as M
        cls.M = M
        cls.tmp, cls.base = M._make("canada")
        r = M._tj(cls.base, "run", "--no-input")
        assert r.returncode == 0, r.stderr + r.stdout

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def _copy(self):
        d = tempfile.mkdtemp(prefix="taxjson_l4_")
        self.addCleanup(shutil.rmtree, d, True)
        root = Path(d) / "canada"
        shutil.copytree(self.base, root, symlinks=True)
        return root

    def _outside(self, root):
        # (with FILE: the JSON form, for review; without it the lines
        # go to the accounts' .tt files — test_fix_missing_history_tt)
        return self.M._tj(root, "find-missing-history",
                          "--write-missing-history",
                          str(root / "missing_history.json"),
                          "--outside-year")

    def test_counts_only_this_runs_entries(self):
        root = self._copy()
        prev = [{"symbol": "QPAS.TO", "account": "margin",
                 "_outside_year": 2025, "_note": "earlier run"}]
        (root / "missing_history.json").write_text(json.dumps(prev))
        r = self._outside(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        flat = " ".join(r.stderr.split())
        self.assertIn("added 2 position(s)", flat)
        self.assertIn("1 entry already there kept", flat)

    def test_non_object_entry_refused_before_writing(self):
        root = self._copy()
        text = json.dumps([{"symbol": "QHAND.TO", "account": "margin"},
                           "QPAS.TO", 7])
        (root / "missing_history.json").write_text(text)
        r = self._outside(root)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("are not JSON objects", " ".join(r.stderr.split()))
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual((root / "missing_history.json").read_text(), text)
        self.assertFalse(list(root.glob("missing_history.json.bak*")))

    def test_symlinked_file_stays_a_link(self):
        root = self._copy()
        (root / "data").mkdir()
        real = root / "data" / "mh.json"
        real.write_text(json.dumps([{"symbol": "QHAND.TO",
                                     "account": "margin"}]))
        os.chmod(real, 0o644)
        (root / "missing_history.json").symlink_to("data/mh.json")
        r = self._outside(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((root / "missing_history.json").is_symlink())
        self.assertEqual(len(json.loads(real.read_text())), 4)
        self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o644)
        self.assertTrue(list((root / "data").glob("mh.json.bak*")))

    def test_symlink_leaving_the_project_is_refused(self):
        root = self._copy()
        outside = root.parent / "elsewhere.json"
        outside.write_text("[]")
        (root / "missing_history.json").symlink_to(outside)
        r = self._outside(root)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("outside the project", " ".join(r.stderr.split()))
        self.assertEqual(outside.read_text(), "[]")


class TestL5RadarTransferPolicy(unittest.TestCase):
    def _radar(self, td, *extra):
        tax, shl, out = (Path(td) / n for n in ("tax.json", "shl.json",
                                                 "out.json"))
        tax.write_text(json.dumps({"transactions": [
            {"action": "BUYSELL", "date": "2026-01-05", "time": "09:30:00",
             "symbol": "QZXF.TO", "quantity": 100, "price": 50,
             "net_amount": -5000.0, "currency": "CAD",
             "account": "margin"},
            {"action": "BUYSELL", "date": "2026-01-20", "time": "09:30:00",
             "symbol": "QZXF.TO", "quantity": -100, "price": 40,
             "net_amount": 4000.0, "currency": "CAD",
             "account": "margin"}]}))
        shl.write_text(json.dumps({"transactions": [
            {"action": "TRANSFER", "date": "2026-01-25", "time": "09:30:00",
             "symbol": "QZXF.TO", "quantity": 100, "net_amount": 0.0,
             "currency": "CAD", "account": "rrsp"}]}))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
             "--country", "canada", "--taxable", str(tax), "--sheltered",
             str(shl), "--date", "2026-02-01", "--json-out", str(out),
             *extra], capture_output=True, text=True, env=_env(),
            timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(out.read_text())
        return {s["category"]: len(s["rows"]) for s in doc["sections"]}

    @rule("CA-SL-16", "CA-SL-17")
    def test_default_custody_move_strict_acquisition(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._radar(td).get("VIOLATION"), 0)
            self.assertEqual(self._radar(
                td, "--transfers-as-acquisitions").get("VIOLATION"), 1)

    def test_run_passes_the_project_policy(self):
        from taxjson.bin.taxjson_run import _radar_engine_args
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for val, want in (("true", True), ("false", False)):
                (root / "taxjson.toml").write_text(
                    f'[settings]\ncountry = "canada"\nyear = 2025\n'
                    f'transfers_as_acquisitions = {val}\n')
                args = _radar_engine_args([], root / "missing_history.json",
                                          "canada")
                self.assertEqual("--transfers-as-acquisitions" in args, want)


if __name__ == "__main__":
    unittest.main()
