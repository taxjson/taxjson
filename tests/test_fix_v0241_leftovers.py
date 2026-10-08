"""Leftovers of the v0.24.0 third pre-release review:

- 1: a `DISTINCT` line written with the bare US ticker (`DISTINCT QZX
  QZX.TO`) answers the `.US` pair (the books spell a bare US ticker
  QZX.US) and says how it was read; a GLOBAL / TOBASE line so written is
  not re-read (it would move pools) but warned, naming the line to write.
- 2: a CDR (written on Cboe Canada, QZG.NE, or named "... CDR" in the
  exports) is its own security: a .tt JOURNAL between it and QZG.US
  needs names that agree, not the shared root.
- 3: a .tt JOURNAL line ending `separate` is a journal of its own: the
  near-restatement Warning is silenced and the line is booked in full,
  never settled against the broker's legs near it.
- 4: one rule for a broker reference several legs share (a journal the
  broker split over rows): the cross-listing join, the transfer-in
  arrivals and the missing-history walk agree on a 3-leg group.

Every fixture is SYNTHETIC: invented QZ*/ZZX tickers and names, fake
account ids (pii-ok: 55500001).
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from _qa_project import console, tj
from tax_rules.dual import cli, projects_both
from test_fix_qa_f3_xlist_loss import f3_project, zzx_loss


def _flat(text):
    return " ".join(text.split())


# ------------------------------------------------------------------ 1

class TestDistinctBareUsTicker(unittest.TestCase):

    def test_parse(self):
        from taxjson.bin.taxjson_ticker_map import (_parse_map_text,
                                                    canonical_distinct)
        tm, problems, _notes = _parse_map_text(
            "DISTINCT ZZX ZZX.TO\nDISTINCT QZB.B.TO QZB.B\n"
            "DISTINCT QZC QZD\nDISTINCT ZZY.US ZZY\n")
        self.assertEqual(problems, [])
        self.assertIn(frozenset(("ZZX.US", "ZZX.TO")), tm.distinct)
        self.assertIn(frozenset(("QZB.B.TO", "QZB.B.US")), tm.distinct)
        # Two bare symbols are two coins; a bare symbol whose US
        # spelling is the other side is left as written.
        self.assertIn(frozenset(("QZC", "QZD")), tm.distinct)
        self.assertIn(frozenset(("ZZY.US", "ZZY")), tm.distinct)
        # An option contract is no bare ticker.
        self.assertEqual(canonical_distinct("ZZX250117C00010000",
                                            "ZZX.TO"),
                         ("ZZX250117C00010000", "ZZX.TO"))

    def _distinct(self, country, kind):
        with tempfile.TemporaryDirectory() as td:
            root = f3_project(td, "p", country=country,
                              ticker_map="DISTINCT ZZX ZZX.TO\n")
            r = tj(root, "run", "--no-input", "--strict")
            text = _flat(console(r))
            self.assertNotIn(f"possible {kind} across listings", text)
            self.assertIn("ticker.map:1: `DISTINCT ZZX ZZX.TO` is read as "
                          "`DISTINCT ZZX.US ZZX.TO`", text)
            loss, = zzx_loss(root)
            self.assertLess(loss["gain"], -100.0)     # still allowed
            self.assertEqual(tj(root, "scan", check=False).stdout.count(
                "XLIST-LOSS"), 0)
            doc = json.loads(tj(root, "ticker-map", "--suggest",
                                "--json").stdout)
            lines = [s["line"] for s in doc.get("suggestions")
                     or doc.get("offer") or []]
            self.assertFalse([x for x in lines if "ZZX" in x], doc)

    @rule("CA-XLIST-05")
    def test_canada_distinct_with_the_bare_us_ticker(self):
        self._distinct("canada", "superficial loss")

    @rule("US-XLIST-04")
    def test_usa_distinct_with_the_bare_us_ticker(self):
        self._distinct("usa", "wash sale")

    def _tobase(self, country, kind):
        with tempfile.TemporaryDirectory() as td:
            root = f3_project(td, "p", country=country,
                              ticker_map="TOBASE ZZX ZZX.TO\n")
            r = tj(root, "run", "--no-input")
            text = _flat(console(r))
            self.assertIn("Warning: ticker.map:1: `TOBASE ZZX ZZX.TO` "
                          "names ZZX without a market suffix", text)
            self.assertIn("write `TOBASE ZZX.US ZZX.TO`", text)
            # Not re-read: the loss stays allowed, the radar still asks.
            self.assertIn(f"possible {kind} across listings", text)
            loss, = zzx_loss(root)
            self.assertLess(loss["gain"], -100.0)     # still allowed

    @rule("CA-XLIST-05")
    def test_canada_tobase_with_the_bare_us_ticker_is_warned(self):
        self._tobase("canada", "superficial loss")

    @rule("US-XLIST-04")
    def test_usa_tobase_with_the_bare_us_ticker_is_warned(self):
        self._tobase("usa", "wash sale")

    def test_coin_lines_are_quiet(self):
        from taxjson.bin.taxjson_ticker_map import listing_spelling_notes
        self.assertEqual(listing_spelling_notes(
            "GLOBAL QZCOIN QZC\nDISTINCT QZC QZD\nTOBASE ZZX.US ZZX.TO\n"),
            ([], []))



# ------------------------------------------------------------------ 2

_QT_CDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n"
           "2025-03-03,2025-03-04,Buy,QZG.NE,QZGEE CORP CDR (CAD HEDGED),"
           "10,20.00,-200.00,0,-200.00,CAD,Trades,55500001,Margin\n"
           "2025-03-03,2025-03-04,Buy,QZG,QZGEE CORP,"
           "10,30.00,-300.00,0,-300.00,USD,Trades,55500001,Margin\n")


class TestCdrIsItsOwnRoot(unittest.TestCase):

    def test_verdict(self):
        from taxjson.lib import cross_listings as XL
        from taxjson.lib.markets import receipt_suffixes
        from taxjson.lib.symbol_codes import exact_name
        self.assertIn("NE", receipt_suffixes())
        self.assertEqual(XL.declared_verdict("QZG.TO", "QZG.US", {}, {}),
                         "")
        why = XL.declared_verdict("QZG.TO", "QZG.US", {}, {},
                                  ("QZG.NE", "QZG.US"))
        self.assertIn("QZG.NE is written on a venue that lists depositary "
                      "receipts", why)
        self.assertIn("no security name for either listing", why)
        cdr = exact_name("QZGEE CORP CDR (CAD HEDGED)")
        corp = exact_name("QZGEE CORP")
        why = XL.declared_verdict("QZG.TO", "QZG.US",
                                  {"QZG.TO": {cdr}, "QZG.US": {corp}},
                                  {cdr: "QZGEE CORP CDR (CAD HEDGED)",
                                   corp: "QZGEE CORP"})
        self.assertIn("QZG.TO is named as a depositary receipt (CDR)", why)
        # Two receipts named alike agree by their names.
        self.assertEqual(XL.declared_verdict(
            "QZG.TO", "QZG.U.TO", {"QZG.TO": {cdr}, "QZG.U.TO": {cdr}},
            {cdr: "QZGEE CORP CDR (CAD HEDGED)"}), "")

    def _stops(self, country, files, needle):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files,
                                 usa={"source_currencies": ["CAD"]})[country]
            r = cli(root, "run", "--no-input")
            out = _flat(r.stdout + r.stderr)
            self.assertNotEqual(r.returncode, 0, out[-2000:])
            self.assertIn("join two listings that nothing shows are one "
                          "security", out)
            self.assertIn(needle, out)
            self.assertIn("inputs/margin/m.tt:", out)

    def _tt_only(self, country):
        tt = ("BUYSELL 2025-03-03 10:00:00 QZG.NE 10 CAD 20.00 200.00 "
              "0.00\n"
              "JOURNAL 2025-03-05 QZG.NE QZG.US 10\n")
        self._stops(country, {"inputs/margin/m.tt": tt},
                    "QZG.NE is written on a venue that lists depositary "
                    "receipts")

    def _named(self, country):
        files = {"inputs/margin/q.csv": _QT_CDR,
                 "inputs/margin/m.tt": "JOURNAL 2025-03-05 QZG.TO QZG.US "
                                       "5\n"}
        self._stops(country, files,
                    "QZG.TO is named as a depositary receipt (CDR)")

    @rule("CA-XLIST-04")
    def test_canada_cdr_venue_line_needs_names(self):
        self._tt_only("canada")

    @rule("US-XLIST-03")
    def test_usa_cdr_venue_line_needs_names(self):
        self._tt_only("usa")

    @rule("CA-XLIST-04")
    def test_canada_cdr_named_in_the_export_needs_names(self):
        self._named("canada")

    @rule("US-XLIST-03")
    def test_usa_cdr_named_in_the_export_needs_names(self):
        self._named("usa")



# ------------------------------------------------------------------ 3

class TestSeparateJournalLine(unittest.TestCase):

    def test_parse(self):
        from taxjson.bin.taxjson_convert_tt import parse_journal_line
        j = parse_journal_line("JOURNAL 2025-05-07 QZD.TO QZD.U.TO 1500 "
                               "separate  # a second gambit")
        self.assertTrue(j["separate"])
        self.assertEqual(j["quantity"], 1500.0)
        self.assertFalse(parse_journal_line(
            "JOURNAL 2025-05-07 QZD.TO QZD.U.TO 1500")["separate"])
        self.assertTrue(parse_journal_line(
            "JOURNAL 2025-05-07 QZD.TO QZD.U.TO 1500 SEPARATE")["separate"])
        with self.assertRaises(ValueError) as cm:
            parse_journal_line("JOURNAL 2025-05-07 QZD.TO QZD.U.TO 1500 "
                               "apart", "j.tt:1")
        self.assertIn("`separate`", str(cm.exception))

    def _run_line(self, country, line):
        from test_fix_dated_events import _rbc_gambit
        from taxjson.lib import dated_events as DE
        rbc, tmap = _rbc_gambit(True)
        files = {"inputs/margin/rbc.csv": rbc, "ticker.map": tmap,
                 "inputs/margin/j.tt": line + "\n"}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=files,
                                 usa={"source_currencies": ["CAD"]}
                                 )[country]
            r = cli(root, "run", "--no-input")
            out = _flat(r.stdout + r.stderr)
            self.assertEqual(r.returncode, 0, out[-3000:])
            rec = DE.read_state(root / "work" / DE.STATE)["journals"]
            return out, [(j["status"], j["quantity"], j["legs"],
                          j.get("separate", False)) for j in rec]

    def _check(self, country):
        # A bigger line a business day after the broker's journal:
        # warned without the word, silent with it.
        out, rec = self._run_line(country,
                                  "JOURNAL 2025-05-07 QZD.TO QZD.U.TO 1500")
        self.assertIn("is booked in full beside the broker's journal", out)
        self.assertIn("end the line with `separate`", out)
        out, rec = self._run_line(
            country, "JOURNAL 2025-05-07 QZD.TO QZD.U.TO 1500 separate")
        self.assertNotIn("booked in full beside", out)
        self.assertEqual(rec, [("booked", 1500.0, ["out", "in"], True)])
        # The broker's own journal restated: a duplicate without the
        # word, its own journal (both legs booked) with it.
        out, rec = self._run_line(country,
                                  "JOURNAL 2025-05-06 QZD.TO QZD.U.TO 1000")
        self.assertEqual(rec, [("duplicate", 1000.0, [], False)])
        out, rec = self._run_line(
            country, "JOURNAL 2025-05-06 QZD.TO QZD.U.TO 1000 separate")
        self.assertEqual(rec, [("booked", 1000.0, ["out", "in"], True)])
        self.assertNotIn("already in the broker's rows", out)

    @rule("CA-XLIST-04")
    def test_canada_separate_line(self):
        self._check("canada")

    @rule("US-XLIST-03")
    def test_usa_separate_line(self):
        self._check("usa")



# ------------------------------------------------------------------ 4

class TestSplitLegReferenceGroup(unittest.TestCase):
    """A broker reference shared by three legs: one journal when its
    legs balance on one listing each side; never one otherwise — and the
    three readers agree."""

    NAME = "QZD US DLR CURRENCY ETF UNIT"

    def _three(self, legs):
        """(cross_listings joined pairs, transfer_in settled indices,
        missing_history detected journals) of one account's legs
        [(symbol, qty, date)] sharing one journal_pair id."""
        from taxjson.lib import cross_listings as XL
        from taxjson.lib.missing_history import (_detected_journals,
                                                 journal_leg_key)
        from taxjson.lib.symbol_codes import exact_name
        from taxjson.lib.transfer_in import own_journal_legs
        rows = [("m", "questrade",
                 {"action": "TRANSFER", "symbol": sym, "quantity": q,
                  "date": day, "journal_pair": "qz-1",
                  "description": self.NAME})
                for sym, q, day in legs]
        nm = exact_name(self.NAME)
        xl_legs = [XL.Leg("m", "questrade", r["symbol"], r["date"],
                          r["quantity"], name=nm, raw_name=self.NAME,
                          ref="|".join(journal_leg_key(r, "questrade")))
                   for _a, _b, r in rows]
        res = XL.analyze(xl_legs, {s: {nm} for s, _q, _d in legs},
                         {nm: self.NAME}, base_currency="CAD")
        joined = [(p.out.symbol, p.into.symbol, -p.out.quantity,
                   p.into.quantity) for p in res["joined"]]
        settled, _orph = own_journal_legs(rows)
        found = _detected_journals(
            [(a, r, b) for a, b, r in rows], {"QZD.U.TO": "QZD.TO"})
        return joined, settled, [(j[2], j[4], j[5]) for j in found]

    def test_split_journal_is_one_journal_in_all_three(self):
        joined, settled, found = self._three(
            [("QZD.TO", -1000, "2025-05-06"),
             ("QZD.U.TO", 600, "2025-05-06"),
             ("QZD.U.TO", 400, "2025-05-07")])
        self.assertEqual([(a, b) for a, b, _q, _r in joined],
                         [("QZD.TO", "QZD.U.TO")])
        self.assertEqual([(q, r) for _a, _b, q, r in joined],
                         [(1000.0, 1000.0)])
        self.assertEqual(settled, {0, 1, 2})
        self.assertEqual(found, [("QZD.TO", "QZD.U.TO", 1000.0)])

    def test_groups_that_are_no_journal_in_all_three(self):
        for legs in (
                # The units do not balance.
                [("QZD.TO", -1000, "2025-05-06"),
                 ("QZD.U.TO", 600, "2025-05-06"),
                 ("QZD.U.TO", 300, "2025-05-06")],
                # A third listing.
                [("QZD.TO", -1000, "2025-05-06"),
                 ("QZD.U.TO", 600, "2025-05-06"),
                 ("QZD.US", 400, "2025-05-06")],
                # Legs further apart than a journal's window.
                [("QZD.TO", -1000, "2025-05-06"),
                 ("QZD.U.TO", 600, "2025-05-06"),
                 ("QZD.U.TO", 400, "2025-05-20")]):
            joined, settled, found = self._three(legs)
            self.assertEqual(joined, [], legs)
            self.assertEqual(settled, set(), legs)
            self.assertEqual(found, [], legs)

    def test_the_rule(self):
        from taxjson.lib.missing_history import ref_group_journal
        self.assertEqual(ref_group_journal(
            [("A.TO", -10, "2025-01-02"), ("A.TO", 10, "2025-01-02")]),
            ("A.TO", "A.TO", 10.0))
        self.assertIsNone(ref_group_journal([("A.TO", -10, "2025-01-02")]))
        self.assertEqual(ref_group_journal(
            [("A.TO", -6, "2025-01-02"), ("A.TO", -4, "2025-01-02"),
             ("A.U.TO", 10, "2025-01-03")]), ("A.TO", "A.U.TO", 10.0))


if __name__ == "__main__":
    unittest.main()
