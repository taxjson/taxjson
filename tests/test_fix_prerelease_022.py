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


if __name__ == "__main__":
    unittest.main()
