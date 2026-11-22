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

Formerly: test_fix_prerelease_022
"""
import unittest

from taxjson.lib import symbol_codes as SC
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def _ok_listing(listing, cur):
    return QuestradeBrokerage().apply_currency_suffix(listing, cur) == listing


def _use(code, desc, cut=False):
    return SC.CodeUse(code=code, name=SC.questrade_name(desc),
                      currencies=["CAD"], arrivals=[], rows=1, name_cut=cut)


def _entry(sym, name, cut=False):
    return SC.NameEntry(sym, SC.name_tokens(name), name, "tfsa",
                        "questrade", cut=cut)


def _res(uses, names=(), outs=()):
    return SC.resolve(uses, list(outs), list(names), listing_ok=_ok_listing,
                      code_listing=QuestradeBrokerage().apply_currency_suffix)


@rule(R.CA_ACB_CODES)
@rule(R.US_BASIS_CODES)
class TestM2TruncationNeedsWidthEvidence(unittest.TestCase):
    """Formerly: test_fix_prerelease_022.TestM2TruncationNeedsWidthEvidence"""
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


@rule(R.CA_ACB_CODES)
@rule(R.US_BASIS_CODES)
class TestM3WordingIsQuestradeOnly(unittest.TestCase):
    """Formerly: test_fix_prerelease_022.TestM3WordingIsQuestradeOnly"""
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


@rule(R.CA_ACB_11)
@rule(R.US_BASIS_04)
class TestM5RenamedShort(unittest.TestCase):
    """Formerly: test_fix_prerelease_022.TestM5RenamedShort"""
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


if __name__ == "__main__":
    unittest.main()
