"""tobase.map: the interlisted master's pairs in a Canadian project
(lib/tobase_map, scripts/build_interlisted.py; tax-logic CA-XLIST-06,
US-XLIST-05).

Every fixture is SYNTHETIC: invented tickers (QZ*), invented FIGIs
(BBG0000000xx), invented names.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, projects_both
from taxjson.rules import ids as R

from taxjson.lib.ticker_map_rules import merge_renames
from taxjson.lib.map_file import _parse_map_file
from taxjson.lib import tobase_map as TB
from _support.symbols_interlisted_master import (
    REPO, F1, F2, F3, _build_module, _master, SEC, _project, _xlsx, _eq, _build_cache,
)

F4 = "BBG000000Q04"


# ------------------------------------------------------------ the master

class TestShippedMaster(unittest.TestCase):

    def test_master_reads_and_holds_no_isin(self):
        m = TB.load_master()
        self.assertEqual(m.meta["schema_version"], 1)
        self.assertTrue(m.generated)
        self.assertGreater(len(m.security), 100)
        text = TB.master_path().read_text(encoding="utf-8")
        self.assertNotIn("isin", text.lower())
        self.assertNotIn("cusip", text.lower())
        for sc, e in m.security.items():
            self.assertRegex(sc, r"^BBG[0-9A-Z]{9}$")
            self.assertEqual(e["share_class_figi"], sc)
            self.assertTrue(e["ca"], sc)
            for s in e["ca"]:
                self.assertTrue(s.endswith(".TO"), s)
            for s in e.get("us", []) + e.get("us_otc", []):
                self.assertTrue(s.endswith(".US"), s)

    def test_rendered_map_parses_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase=TB.render(TB.load_master()))
            tm, problems, notes = _parse_map_file(root / "ticker.map")
            self.assertEqual((problems, notes), ([], []))
            self.assertTrue(tm.tobase)
            self.assertFalse(set(tm.tobase) - set(tm.generated))


class TestRender(unittest.TestCase):

    def test_sections_markers_and_until(self):
        text = TB.render(_master(SEC))
        self.assertIn(f"{TB.STAMP} 2026-10-01", text)
        ex = text.index(TB.SECTION_EXCHANGE)
        otc = text.index(TB.SECTION_OTC)
        self.assertLess(ex, otc)
        a = text.index(f"TOBASE QZAB.US QZA.TO  # master:{F1}:"
                       f"{TB.line_hash('TOBASE QZAB.US QZA.TO')}")
        b = text.index(f"TOBASE QZBBF.US QZB.TO  # master:{F2}")
        self.assertTrue(ex < a < otc < b)
        self.assertRegex(text, rf"TOBASE QZG.US QZG.TO  # master:{F3}:"
                               rf"[0-9a-f]{{6}} until=2025-06-30\n")
        self.assertNotIn("DISTINCT", text.split(TB.STAMP)[1])

    @rule(R.CA_XLIST_06)
    def test_no_distinct_line_for_a_receipt(self):
        # v0.27.1: look-alike listings are never joined, so a receipt the
        # books hold needs no DISTINCT line (the master keeps it apart:
        # receipt_pairs).
        m = _master(SEC, {F4: {"name": "QZD CDR", "kind": "cdr",
                               "ca": "QZD.TO", "us": "QZD.US",
                               "us_figi": "BBG000000Q05"}})
        self.assertNotIn("DISTINCT", TB.render(m).split(TB.STAMP)[1])
        self.assertFalse([g for g in TB.master_lines(m)
                          if g.keyword == "DISTINCT"])

    def test_a_foreign_issuer_is_booked_under_its_us_listing(self):
        # A US company's TSX line joins its NYSE line (its dividends and
        # T1135 status stay foreign); a Canadian one the reverse.
        sec = {F1: dict(SEC[F1], domicile="US"),
               F2: dict(SEC[F2], domicile="US")}
        text = TB.render(_master(sec))
        self.assertIn(f"TOBASE QZA.TO QZAB.US  # master:{F1}", text)
        # No US exchange listing: the Canadian line stays the base.
        self.assertIn(f"TOBASE QZBBF.US QZB.TO  # master:{F2}", text)
        text = TB.render(_master({F1: dict(SEC[F1], domicile="CA")}))
        self.assertIn(f"TOBASE QZAB.US QZA.TO  # master:{F1}", text)

    def test_listing_level_end(self):
        sec = {F1: dict(SEC[F1], history=[{"listing": "QZAX.US",
                                          "kind": "us",
                                          "until": "2024-03-01"}])}
        text = TB.render(_master(sec))
        self.assertRegex(text, rf"TOBASE QZAX.US QZA.TO  # master:{F1}:"
                               rf"[0-9a-f]{{6}} until=2024-03-01 "
                               rf"ended=QZAX.US\n")


# ------------------------------------------------------------ overlay

class TestOverlay(unittest.TestCase):

    def _tob(self, *lines):
        return TB.parse_tobase("\n".join(
            f"{ln}  # master:{F1}" for ln in lines))

    @rule(R.CA_XLIST_06)
    def test_applied_and_pooled(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase=f"TOBASE QZAB.US QZA.TO  # master:{F1}\n")
            tm, problems, _n = _parse_map_file(root / "ticker.map")
            self.assertEqual(problems, [])
            self.assertEqual(merge_renames(tm, True)["QZAB.US"], "QZA.TO")
            self.assertIn("QZAB.US", tm.generated)
            # Not in the holdings (GLOBAL) view, as any TOBASE line.
            self.assertNotIn("QZAB.US", merge_renames(tm, False))

    @rule(R.CA_XLIST_06)
    def test_ticker_map_wins(self):
        ov = TB.compute_overlay("DISTINCT QZAB.US QZA.TO\n",
                                self._tob("TOBASE QZAB.US QZA.TO"), True)
        self.assertFalse([x for x in ov.lines if x.startswith("TOBASE")])
        self.assertEqual(ov.overridden[0][0], "TOBASE QZAB.US QZA.TO")
        # A different pairing in ticker.map: not applied, said.
        ov = TB.compute_overlay("TOBASE QZAB.US QZZ.TO\n",
                                self._tob("TOBASE QZAB.US QZA.TO"), True)
        self.assertIn("ticker.map: TOBASE QZAB.US QZZ.TO",
                      ov.overridden[0][1])
        # The same pairing: silent.
        ov = TB.compute_overlay("TOBASE QZAB.US QZA.TO\n",
                                self._tob("TOBASE QZAB.US QZA.TO"), True)
        self.assertEqual((ov.overridden, ov.same), ([], 1))
        # A GLOBAL / DELETE of either symbol decides it.
        for line in ("GLOBAL QZAB.US QZQ.US\n", "DELETE QZA.TO\n"):
            ov = TB.compute_overlay(line, self._tob("TOBASE QZAB.US QZA.TO"),
                                    True)
            self.assertTrue(ov.overridden, line)
        # Another listing joining the same base is no conflict.
        ov = TB.compute_overlay("TOBASE QZAX.US QZA.TO\n",
                                self._tob("TOBASE QZAB.US QZA.TO"), True)
        self.assertEqual(ov.overridden, [])
        self.assertIn("TOBASE QZAB.US QZA.TO", ov.lines)
        # A FROM that is the base of another pairing is.
        ov = TB.compute_overlay("TOBASE QZQ.US QZAB.US\n",
                                self._tob("TOBASE QZAB.US QZA.TO"), True)
        self.assertTrue(ov.overridden)
        # A rename INTO a symbol does not: the chain joins the pair.
        ov = TB.compute_overlay("GLOBAL QZOLD.US QZAB.US\n",
                                self._tob("TOBASE QZAB.US QZA.TO"), True)
        self.assertIn("TOBASE QZAB.US QZA.TO", ov.lines)

    def test_never_a_cycle_or_two_targets(self):
        ov = TB.compute_overlay("", self._tob("TOBASE QZA.US QZA.TO",
                                              "TOBASE QZA.TO QZA.US",
                                              "TOBASE QZA.US QZQ.TO"), True)
        self.assertEqual([x for x in ov.lines if x.startswith("TOBASE QZA.")
                          and "QZA.V" not in x],
                         ["TOBASE QZA.US QZA.TO"])
        # Two tobase.map lines another tobase.map line contradicts:
        # never blamed on ticker.map.
        self.assertEqual(len(ov.internal), 2)
        self.assertEqual(ov.overridden, [])

    def test_bad_line_is_a_problem_naming_tobase_map(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase="GLOBAL QZA.US QZA.TO\n")
            _tm, problems, _n = _parse_map_file(root / "ticker.map")
            self.assertTrue(any(p.startswith("tobase.map:1:")
                                for p in problems), problems)

    @rule(R.CA_XLIST_06)
    def test_venture_spelling_is_one_listing(self):
        # A ticker.map line naming X.V covers X.TO, and the reverse.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ticker="TOBASE QZVB.US QZV.V\n"
                                       "DISTINCT QZW.US QZW.V\n")
            tm, problems, _n = _parse_map_file(root / "ticker.map")
            self.assertEqual(problems, [])
            ren = merge_renames(tm, True)
            self.assertEqual(ren["QZV.TO"], "QZV.V")
            self.assertEqual(ren["QZVB.US"], "QZV.V")
            self.assertIn(frozenset(("QZW.US", "QZW.TO")), tm.distinct)
        # A line naming X.TO adds nothing: the books never hold X.V (a
        # Venture line in CAD is booked X.TO by every parser).
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ticker="TOBASE QZVB.US QZV.TO\n")
            tm = _parse_map_file(root / "ticker.map")[0]
            self.assertEqual(merge_renames(tm, True), {"QZVB.US": "QZV.TO"})
            self.assertEqual(tm.generated, frozenset())

    @rule_absent(R.CA_XLIST_06, country="usa")
    @rule(R.US_XLIST_05)
    def test_usa_reads_no_tobase_map_and_no_venture_alias(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa", ticker="TOBASE QZVB.US QZV.V\n",
                            tobase=f"TOBASE QZAB.US QZA.TO  # master:{F1}\n")
            tm, problems, _n = _parse_map_file(root / "ticker.map")
            self.assertEqual(problems, [])
            ren = merge_renames(tm, True)
            self.assertNotIn("QZAB.US", ren)
            self.assertNotIn("QZV.TO", ren)
            self.assertEqual(tm.generated, frozenset())

    def test_inferences_yield_only_to_the_users_lines(self):
        # named_symbols (what listing-suffix and Questrade-code inference
        # leave alone) counts the user's lines, not the master's.
        from taxjson.lib.ticker_map_rules import named_symbols
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ticker="TOBASE QZUU.US QZUU.TO\n",
                            tobase=f"TOBASE QZAB.US QZA.TO  # master:{F1}\n")
            tm = _parse_map_file(root / "ticker.map")[0]
            named = named_symbols(tm, lookups=True)
            self.assertIn("QZUU.US", named)
            self.assertIn("QZUU.TO", named)
            self.assertNotIn("QZAB.US", named)
            self.assertNotIn("QZA.TO", named)

    def test_unused_rule_check_skips_generated_lines(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, ticker="TOBASE QZUU.US QZUU.TO\n",
                            tobase=f"TOBASE QZAB.US QZA.TO  # master:{F1}\n")
            (root / "work").mkdir()
            from taxjson.lib import map_hygiene as MH
            from unittest import mock
            with mock.patch.object(MH, "_accounts", return_value=[]):
                unused, unread = MH.unused_rules(root)
            self.assertEqual([u.frm for u in unused], ["QZUU.US"])


# ------------------------------------------------------------ until

class TestUntil(unittest.TestCase):

    @rule(R.CA_XLIST_06)
    def test_trade_after_until_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase=(
                f"TOBASE QZG.US QZG.TO  # master:{F3} until=2025-06-30\n"
                f"TOBASE QZGX.US QZH.TO  # master:{F4} until=2025-06-30 "
                f"ended=QZGX.US\n"))
            dates = {"QZG.US": ["2025-03-01", "2025-08-15"],
                     "QZH.TO": ["2025-09-01"]}
            found = TB.until_findings(root, dates)
            self.assertEqual([(f.symbol, f.dates) for f in found],
                             [("QZG.US", ["2025-08-15"])])
            head, details = TB.until_message(found[0])
            self.assertIn("after 2025-06-30", head)
            self.assertIn("DISTINCT QZG.US QZG.TO", details[0])

    def test_unknown_until_gives_no_finding(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, tobase=(
                f"TOBASE QZG.US QZG.TO  # master:{F3} until=unknown\n"))
            self.assertEqual(TB.until_findings(root, {"QZG.US":
                                                      ["2030-01-01"]}), [])


# ------------------------------------------------------------ update plan

class TestPlan(unittest.TestCase):

    def test_added_ended_retracted_edited_and_own_lines(self):
        old = TB.parse_tobase(
            f"{TB.STAMP} 2026-01-01\n"
            f"TOBASE QZAB.US QZA.TO  # master:{F1}\n"
            f"TOBASE QZG.US QZG.TO  # master:{F3}\n"
            f"TOBASE QZA.U.TO QZA.TO  # master:{F1}\n"
            f"TOBASE QZEDIT.US QZB.TO  # master:{F2}\n"
            f"TOBASE QZOWN.US QZOWN.TO  # my own\n")
        plan = TB.plan_update(_master(SEC), old, {"QZBBF.US"}, "")
        self.assertEqual([g.rule for g in plan.added],
                         ["TOBASE QZBBF.US QZB.TO"])
        self.assertEqual([(ln.rule, g.until) for ln, g in plan.ended],
                         [("TOBASE QZG.US QZG.TO", "2025-06-30")])
        # QZA.U.TO is a listing of no entry: the user's edit, kept.
        self.assertEqual({ln.rule for ln in plan.edited},
                         {"TOBASE QZA.U.TO QZA.TO",
                          "TOBASE QZEDIT.US QZB.TO"})
        self.assertIn("TOBASE QZOWN.US QZOWN.TO  # my own", plan.new_text)
        self.assertIn(f"{TB.STAMP} 2026-10-01", plan.new_text)
        self.assertIn("until=2025-06-30", plan.new_text)
        self.assertTrue(plan.changes)

    def test_retracted_only_when_unedited(self):
        sec = {F1: dict(SEC[F1], us=[], us_exchange=[],
                        history=[{"listing": "QZAB.US", "kind": "us",
                                  "until": "2026-01-01"}])}
        # The master still gives the ended line: annotated, kept.
        old = TB.parse_tobase(f"TOBASE QZAB.US QZA.TO  # master:{F1}\n")
        plan = TB.plan_update(_master(sec), old, set(), "")
        self.assertEqual(len(plan.ended), 1)
        self.assertEqual(plan.retracted, [])
        # A pair of the entry the master no longer gives at all (an
        # older marker, no check: judged by what the master could give).
        sec = {F1: dict(SEC[F1], us=["QZAN.US"])}
        old = TB.parse_tobase(f"TOBASE QZAB.US QZA.TO  # master:{F1}\n")
        plan = TB.plan_update(_master(sec), old, set(), "")
        self.assertEqual([ln.rule for ln, _w in plan.retracted], [])
        self.assertEqual([ln.rule for ln in plan.edited],
                         ["TOBASE QZAB.US QZA.TO"])
        # The reversed direction of a marked line is the user's edit:
        # kept, never retracted.
        sec = {F1: dict(SEC[F1], ca=["QZA.TO", "QZA.U.TO"])}
        old = TB.parse_tobase(f"TOBASE QZA.TO QZA.U.TO  # master:{F1}\n")
        plan = TB.plan_update(_master(sec), old, set(), "")
        self.assertEqual(plan.retracted, [])
        self.assertEqual([ln.rule for ln in plan.edited],
                         ["TOBASE QZA.TO QZA.U.TO"])
        self.assertIn("TOBASE QZA.TO QZA.U.TO", plan.new_text)

    def test_ticker_change_keeps_the_old_line_and_suggests_rename(self):
        sec = {F1: dict(SEC[F1], us=["QZAN.US"],
                        history=[{"listing": "QZAB.US", "kind": "us",
                                  "until": "2026-02-01"}])}
        old = TB.parse_tobase(f"TOBASE QZAB.US QZA.TO  # master:{F1}\n")
        plan = TB.plan_update(_master(sec), old, set(), "")
        self.assertEqual([g.rule for g in plan.added],
                         ["TOBASE QZAN.US QZA.TO"])
        (o, n, pooled), = plan.renamed
        self.assertEqual((o.a, n.a, o.until), ("QZAB.US", "QZAN.US",
                                               "2026-02-01"))
        # Both book as QZA.TO: nothing more to write.
        self.assertTrue(pooled)
        self.assertIn("TOBASE QZAB.US QZA.TO", plan.new_text)

    def test_conflicts_reported(self):
        plan = TB.plan_update(_master(SEC), None, set(),
                              "TOBASE QZAB.US QZZ.TO\n")
        self.assertEqual(plan.conflicts[0][0], "TOBASE QZAB.US QZA.TO")

    def test_books_changes(self):
        ch = TB.books_changes(_master(SEC), {"QZAB.US", "QZA.TO", "QZBBF.US"},
                              "")
        how = {g.rule: h for g, h in ch}
        self.assertIn("joins QZAB.US and QZA.TO", how["TOBASE QZAB.US QZA.TO"])
        self.assertIn("books QZBBF.US as QZB.TO", how["TOBASE QZBBF.US QZB.TO"])
        self.assertNotIn("TOBASE QZG.US QZG.TO", how)


# ------------------------------------------------------------ the build


class TestBuild(unittest.TestCase):

    _cache = staticmethod(_build_cache)

    def test_offline_build_pairs_only_one_share_class(self):
        with tempfile.TemporaryDirectory() as td:
            B, cache = self._cache(td)
            fig = B.Figi(cache / B.FIGI_CACHE)
            doc, rep = B.build(cache, None, "2026-01-02", fig, [])
            sec = doc["security"]
            a = sec["BBG000000A01"]
            self.assertEqual(a["ca"], ["QZA.TO", "QZA.U.TO"])
            self.assertEqual(a["us"], ["QZAB.US"])
            self.assertEqual(a["us_exchange"], ["NYSE"])
            self.assertEqual(a["us_otc"], ["QZAAF.US"])     # the ADR: never
            self.assertEqual(rep["receipts_skipped_under_share_class"], 1)
            self.assertEqual(sec["BBG000000V01"]["us_otc"], ["QZVVF.US"])
            # The ISIN's country, never the ISIN.
            self.assertEqual(a["domicile"], "US")
            self.assertNotIn("domicile", sec["BBG000000V01"])
            self.assertNotIn("BBG000000M01", sec)           # two classes
            self.assertEqual(rep["figi_requests"], 0)
            self.assertEqual(doc["distinct"]["BBG000000D01"]["us"], "QZD.US")
            # The US ticker's own company, named apart from the receipt's.
            self.assertEqual(doc["distinct"]["BBG000000D01"]["us_name"],
                             "QZ CO")
            text = B.render(dict(doc, meta={"schema_version": 1,
                                             "generated": "2026-01-02",
                                             "sources": []}))
            self.assertNotIn("isin", text.lower())
            self.assertNotIn("US0000000001", text)
            self.assertNotIn("QZ Alpha", text)      # no TMX text shipped
            import tomllib
            self.assertIn("BBG000000A01", tomllib.loads(text)["security"])

    def test_append_only(self):
        with tempfile.TemporaryDirectory() as td:
            B, cache = self._cache(td)
            prev = {"security": {
                "BBG000000A01": {"name": "X", "kind": "share",
                                 "share_class_figi": "BBG000000A01",
                                 "ca": ["QZA.TO"], "us": ["QZAOLD.US"],
                                 "us_exchange": ["NYSE"],
                                 "first_seen": "2025-01-01"},
                "BBG000000Z01": {"name": "GONE", "kind": "share",
                                 "share_class_figi": "BBG000000Z01",
                                 "ca": ["QZZ.TO"], "us": ["QZZ.US"],
                                 "us_exchange": ["NYSE"],
                                 "first_seen": "2025-01-01"}},
                "distinct": {}}
            doc, _rep = B.build(cache, prev, "2026-01-02",
                                B.Figi(cache / B.FIGI_CACHE), [])
            a = doc["security"]["BBG000000A01"]
            self.assertEqual(a["first_seen"], "2025-01-01")
            self.assertEqual(a["history"], [{"listing": "QZAOLD.US",
                                             "kind": "us",
                                             "until": "2026-01-02"}])
            z = doc["security"]["BBG000000Z01"]
            self.assertEqual((z["us"], z["until"]), (["QZZ.US"], "2026-01-02"))

    def test_history_needs_a_figi(self):
        with tempfile.TemporaryDirectory() as td:
            B, cache = self._cache(td)
            hist = [{"name": "QZ ENDED", "share_class_figi": "BBG000000E01",
                     "ca": ["QZE.TO"], "us": ["QZE.US"]},
                    {"name": "NO FIGI", "ca": ["QZF.TO"], "us": ["QZF.US"]}]
            doc, rep = B.build(cache, None, "2026-01-02",
                               B.Figi(cache / B.FIGI_CACHE), hist)
            e = doc["security"]["BBG000000E01"]
            self.assertEqual(e["until"], "unknown")
            self.assertEqual(e["history"][0]["listing"], "QZE.US")
            self.assertEqual(rep["history_skipped_no_figi"],
                             ["QZF.TO / QZF.US"])

    def test_a_bare_issuer_root_resolves_by_its_class_spelling(self):
        # The TMX workbook names the issuer's root (QZK); its shares
        # trade only as a class (QZK.B) or as trust units (QZR.UN).
        with tempfile.TemporaryDirectory() as td:
            B, cache = self._cache(td)
            hdr = ["Co_ID", "Root Ticker", "Name", "SP_Type"]
            _xlsx(cache / f"{B.TMX_XLSX_PREFIX}2026-01-01.xlsx", {
                "TSX Issuers": [hdr, ["1", "QZA", "QZ Alpha", ""],
                                ["4", "QZK", "QZ Kappa", ""],
                                ["5", "QZR", "QZ REIT", "Income Trust"]],
                "TSXV Issuers": [hdr]})
            K = B.Figi.key
            figi = json.loads((cache / B.FIGI_CACHE).read_text())
            figi.update({
                K(B.ticker_job("QZK", "CN")): [],
                K(B.ticker_job("QZK/B", "CN")): [
                    _eq("QZK/B", "CN", "BBG000000K01")],
                K(B.class_job("BBG000000K01", "US")): [
                    _eq("QZK", "US", "BBG000000K01")],
                K(B.ticker_job("QZR", "CN")): [],
                K(B.ticker_job("QZR-U", "CN")): [
                    _eq("QZR-U", "CN", "BBG000000R01", typ="REIT")],
                K(B.class_job("BBG000000R01", "US")): [
                    _eq("QZRUF", "US", "BBG000000R01", typ="REIT")]})
            (cache / B.NASDAQ).write_text(
                "Symbol|Security Name|Market Category|Test Issue|Financial "
                "Status|Round Lot Size|ETF|NextShares\n"
                "QZK|QZ Kappa Class B|Q|N|N|100|N|N\n"
                "File Creation Time: 0101202612:00|||||||\n")
            (cache / B.FIGI_CACHE).write_text(json.dumps(figi))
            doc, rep = B.build(cache, None, "2026-01-02",
                               B.Figi(cache / B.FIGI_CACHE), [])
            k = doc["security"]["BBG000000K01"]
            self.assertEqual((k["ca"], k["us"]), (["QZK.B.TO"], ["QZK.US"]))
            r = doc["security"]["BBG000000R01"]
            self.assertEqual((r["ca"], r["kind"], r["us_otc"]),
                             (["QZR.UN.TO"], "unit", ["QZRUF.US"]))
            self.assertEqual(rep["roots_resolved_by_class_spelling"],
                             ["QZK.B", "QZR.UN"])

    def test_an_openfigi_error_is_not_cached_as_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            B = _build_module()
            fig = B.Figi(Path(td) / "c.json", online=True)
            fig.PAUSE = 0
            fig._post = lambda jobs: [{"error": "Invalid idValue."},
                                      {"warning": "No identifier found."}]
            jobs = [B.ticker_job("QZE", "CN"), B.ticker_job("QZN", "CN")]
            self.assertEqual(fig.map(jobs), [None, []])
            self.assertEqual(fig.errors, 1)
            self.assertNotIn(B.Figi.key(jobs[0]), fig.cache)

    def test_history_date_replaces_an_earlier_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            B, cache = self._cache(td)
            hist = [{"name": "QZ ENDED", "share_class_figi": "BBG000000E01",
                     "ca": ["QZE.TO"], "us": ["QZE.US"]}]
            fig = B.Figi(cache / B.FIGI_CACHE)
            prev, _ = B.build(cache, None, "2026-01-02", fig, hist)
            hist[0]["until"] = "2025-10-22"
            doc, _ = B.build(cache, prev, "2026-02-01", fig, hist)
            e = doc["security"]["BBG000000E01"]
            self.assertEqual(e["until"], "2025-10-22")
            self.assertEqual(e["history"], [{"listing": "QZE.US",
                                             "kind": "us",
                                             "until": "2025-10-22"}])

    def test_an_unchanged_rebuild_keeps_its_date(self):
        with tempfile.TemporaryDirectory() as td:
            B, cache = self._cache(td)
            out = Path(td) / "m.toml"
            self.assertEqual(B.main(["--cache", str(cache), "--out", str(out),
                                     "--date", "2026-01-02"]), 0)
            first = out.read_text()
            self.assertEqual(B.main(["--cache", str(cache), "--out", str(out),
                                     "--date", "2026-03-01"]), 0)
            self.assertEqual(out.read_text(), first)

    def test_cache_inside_the_repo_is_refused(self):
        B = _build_module()
        self.assertEqual(B.main(["--cache", str(REPO / "tmp-cache"),
                                 "--out", os.devnull]), 2)
        self.assertFalse((REPO / "tmp-cache").exists())


# ------------------------------------------------------------ OTC spelling

class TestOtcSpelling(unittest.TestCase):
    """A US OTC trade is ROOT.US in every parser, the spelling tobase.map
    writes (`TOBASE QZOTF.US QZO.TO`)."""

    def test_ib_otc_venues(self):
        from taxjson.lib.brokerages.ib_extractor import _ib_stock_symbol
        from taxjson.lib.markets import ib_venue_suffix
        for venue in ("PINK", "OTC", "GREY"):
            self.assertEqual(ib_venue_suffix(venue), "US", venue)
            fii = {("Stocks", "QZOTF"): {"exch": venue}}
            self.assertEqual(_ib_stock_symbol("Stocks", "QZOTF", "USD", fii),
                             "QZOTF.US")

    def test_currency_suffix_parsers(self):
        from taxjson.lib.brokerages.base import BaseBrokerage
        self.assertEqual(BaseBrokerage.apply_currency_suffix(
            BaseBrokerage.__new__(BaseBrokerage), "QZOTF", "USD"),
            "QZOTF.US")
        # A Venture listing in CAD is its .TO spelling in the books.
        self.assertEqual(BaseBrokerage.apply_currency_suffix(
            BaseBrokerage.__new__(BaseBrokerage), "QZV.V", "CAD"), "QZV.TO")


# ------------------------------------------------------------ the CLI

class TestCli(unittest.TestCase):

    def test_init_writes_it_in_canada_only_and_new_year_shares_it(self):
        # v0.27.1: in the year layout one tobase.map at the top, every
        # year reading it (`[settings] tobase_map`); no per-year copy.
        from taxjson.lib import project_layout as _PL
        with tempfile.TemporaryDirectory() as td:
            for country in ("canada", "usa"):
                top = Path(td) / country
                r = cli(Path(td), "init", str(top), "--country", country,
                        "--year", "2025")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertFalse((top / "2025" / "tobase.map").exists())
                self.assertEqual((top / "tobase.map").is_file(),
                                 country == "canada")
                self.assertEqual(_PL.shared_tobase(top / "2025"),
                                 country == "canada")
            top = Path(td) / "canada"
            text = (top / "tobase.map").read_text()
            self.assertIn("# master:BBG", text)
            self.assertIn(TB.STAMP, text)
            self.assertIn("Shared by every year folder", text)
            r = cli(top / "2025", "new-year", "2026")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((top / "2026" / "tobase.map").exists())
            self.assertEqual(_PL.tobase_map_path(top / "2026"),
                             (top / "tobase.map").resolve())

    @rule(R.US_XLIST_05)
    def test_update_refused_in_a_us_project(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "usa")
            r = cli(root, "update-tobase-map")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Canada-only", r.stdout + r.stderr)

    def test_update_dry_run_then_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            (root / "ticker.map").unlink()
            r = cli(root, "update-tobase-map")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("no tobase.map yet", r.stdout)
            self.assertFalse((root / "tobase.map").exists())
            r = cli(root, "update-tobase-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((root / "tobase.map").is_file())
            self.assertTrue((root / "ticker.map").is_file())
            r = cli(root, "update-tobase-map", "--json")
            doc = json.loads(r.stdout)
            self.assertEqual((doc["exists"], doc["added"], doc["written"]),
                             (True, [], False))
            # An older file: the checklist says so; --write backs it up.
            p = root / "tobase.map"
            p.write_text(p.read_text().replace(
                TB.STAMP + " ", TB.STAMP + " 2000-01-01 #"))
            from taxjson.lib import checklist as CL
            self.assertEqual(CL.s_tobase_map(
                CL.Ctx(root, {"settings": {"country": "canada"}}, 2025,
                       None, None), None).status, "attention")
            r = cli(root, "update-tobase-map", "--write")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((root / "tobase.map.bak").is_file())


class TestYears(unittest.TestCase):

    def test_years_compare_counts_tobase_lines_apart(self):
        from taxjson.lib import project_layout as PL
        with tempfile.TemporaryDirectory() as td:
            a = _project(Path(td) / "2024", tobase=(
                f"TOBASE QZAB.US QZA.TO  # master:{F1}\n"))
            b = _project(Path(td) / "2025", tobase=(
                f"TOBASE QZAB.US QZA.TO  # master:{F1}\n"
                f"TOBASE QZBBF.US QZB.TO  # master:{F2}\n"))
            c = PL.compare(a, b)
            self.assertEqual(c["map_only_there"], [])
            self.assertEqual(c["tobase_only_there"],
                             ["TOBASE QZBBF.US QZB.TO"])
            self.assertEqual(c["tobase_only_here"], [])


# ------------------------------------------------------------ a full run

_ACCTS = '[accounts.margin]\ntype = "taxable"\n'


def _files(country):
    sfx, cur = ("TO", "CAD") if country == "canada" else ("US", "USD")
    m = (f"BUYSELL 2025-11-03 10:00:00 QZU.{sfx} 100 {cur} 50.00 5000.00 "
         f"0.00\n"
         f"BUYSELL 2025-12-19 10:00:00 QZU.{sfx} -100 {cur} 40.00 4000.00 "
         f"0.00\n"
         f"BUYSELL 2025-12-29 10:00:00 QZT.{sfx} 100 {cur} 39.00 3900.00 "
         f"0.00\n")
    return {"inputs/margin/m.tt": m,
            "tobase.map": (f"TOBASE QZU.{sfx} QZT.{sfx}  # master:{F1} "
                           f"until=2025-12-01 ended=QZU.{sfx}\n"),
            "ticker.map": ""}


class TestRun(unittest.TestCase):

    @rule(R.CA_XLIST_06)
    @rule_absent(R.CA_XLIST_06, country="usa")
    @rule(R.US_XLIST_05)
    def test_pooled_in_canada_not_read_in_the_usa(self):
        with tempfile.TemporaryDirectory() as td:
            for country in ("canada", "usa"):
                root = projects_both(Path(td) / country, accounts=_ACCTS,
                                     files=_files(country))[country]
                r = cli(root, "run", "--no-input")
                out = " ".join((r.stdout + r.stderr).split())
                self.assertEqual(r.returncode, 0, out)
                doc = json.loads(cli(root, "sum", "--json").stdout)
                tot = doc["filing"]["totals"]
                if country == "canada":
                    # One security: the loss on QZU is superficial.
                    self.assertAlmostEqual(tot["denied"], 1000.0)
                    self.assertIn("QZU.TO has 1 row date(s) after "
                                  "2025-12-01", out)
                else:
                    # Two securities: the loss stands.
                    self.assertAlmostEqual(tot["gain"], -1000.0)
                    self.assertAlmostEqual(tot.get("denied", tot.get(
                        "disallowed", 0.0)), 0.0)
                    self.assertIn("tobase.map is not read in a US project",
                                  out)


class TestT1135Options(unittest.TestCase):

    @rule(R.CA_RPT_17)
    def test_option_on_a_pooled_listing_follows_it(self):
        # Filing position: a US-listed call on a Canadian issuer pooled
        # by tobase.map is booked under the Canadian listing's option
        # code and is not specified foreign property; a call on a US
        # listing nothing pools stays foreign.
        tt = ("BUYSELL 2025-02-03 10:00:00 QZAB270115C00010000.US 10 CAD "
              "150.00 150000.00 0\n"
              "BUYSELL 2025-02-03 10:00:00 QZZ270115C00010000.US 10 CAD "
              "120.00 120000.00 0\n")
        files = {"inputs/margin/m.tt": tt,
                 "tobase.map": f"TOBASE QZAB.US QZA.TO  # master:{F1}\n",
                 "ticker.map": ""}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(Path(td), accounts=_ACCTS,
                                 files=files)["canada"]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            t = cli(root, "t1135", "--json")
        self.assertEqual(t.returncode, 0, t.stderr[-1500:])
        prop = {p["symbol"]: p for p in json.loads(t.stdout)["properties"]}
        self.assertEqual(prop["QZZ270115C00010000.US"]["country"], "USA")
        self.assertNotIn("QZAB270115C00010000.US", prop)
        pooled = prop.get("QZA270115C00010000.TO")
        self.assertTrue(pooled is None or not pooled.get("country"), pooled)


if __name__ == "__main__":
    unittest.main()
