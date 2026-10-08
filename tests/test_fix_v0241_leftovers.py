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
- 5: `format-map --write` homes a moved dated RENAME line where every
  account's late= resolution is kept (an account relying on the map
  line's event-level late=); when no home keeps it, the refusal names
  the account and the line to add. An account that never held OLD is
  not "changed" by the move.
- 8: a Questrade (or RBC) DRIP row whose REINV@ price is in the other
  currency (REINV@C$ on a USD row, U$ on a CAD row) is booked at the
  cash per unit, with no row-check Warning; a same-currency mismatch
  still warns.

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
                                                    distinct_spellings)
        tm, problems, _notes = _parse_map_text(
            "DISTINCT ZZX ZZX.TO\nDISTINCT QZB.B.TO QZB.B\n"
            "DISTINCT QZC QZD\nDISTINCT ZZY.US ZZY\n")
        self.assertEqual(problems, [])
        # The pair as written (a bare symbol may be a coin or a broker's
        # code) and the US listing's.
        for pair in (("ZZX.US", "ZZX.TO"), ("ZZX", "ZZX.TO"),
                     ("QZB.B.TO", "QZB.B.US"), ("QZB.B.TO", "QZB.B")):
            self.assertIn(frozenset(pair), tm.distinct)
        # Two bare symbols are two coins; a bare symbol whose US
        # spelling is the other side is left as written.
        self.assertIn(frozenset(("QZC", "QZD")), tm.distinct)
        self.assertNotIn(frozenset(("QZC.US", "QZD")), tm.distinct)
        self.assertIn(frozenset(("ZZY.US", "ZZY")), tm.distinct)
        self.assertEqual(len(tm.distinct), 6)
        # An option contract is no bare ticker.
        self.assertEqual(distinct_spellings("ZZX250117C00010000",
                                            "ZZX.TO"),
                         (frozenset(("ZZX250117C00010000", "ZZX.TO")),))

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
                          "names ZZX, a symbol the books do not hold", text)
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

    def test_coins_and_codes_are_quiet(self):
        from taxjson.bin.taxjson_ticker_map import listing_spelling_notes
        self.assertEqual(listing_spelling_notes(
            "GLOBAL QZCOIN QZC\nDISTINCT QZC QZD\nTOBASE ZZX.US ZZX.TO\n"),
            ([], []))
        # A broker's code or raw spelling the books hold is a symbol of
        # its own: its GLOBAL line is live, nothing said.
        text = ("GLOBAL QZ056068 QZDW.US\nGLOBAL QZF.PR QZF.PR.A.TO\n"
                "TOBASE ZZX ZZX.TO\n")
        books = {"QZ056068", "QZDW.US", "QZF.PR", "QZF.PR.A.TO", "ZZX.TO"}
        self.assertEqual(listing_spelling_notes(text, "ticker.map", books),
                         ([], []))
        # The US listing in the books, the bare ticker not: warned.
        _i, warns = listing_spelling_notes(text, "ticker.map",
                                           books | {"ZZX.US"})
        self.assertEqual(len(warns), 1)
        self.assertIn("ticker.map:3: `TOBASE ZZX ZZX.TO`", warns[0])



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



# ------------------------------------------------------------------ 5

_HOME = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}


def _bt(day, sym, qty, cur, price):
    return (f"BUYSELL {day} 10:00:00 {sym} {qty} {cur} {price:.2f} "
            f"{abs(qty) * price:.2f} 0.00\n")


class TestFormatMapKeepsEveryLate(unittest.TestCase):
    """ticker.map: `RENAME QZOLD QZNEW 2025-04-01 late=fold`. aa declares
    the change late=separate in its own .tt line; bb (and cc) held QZOLD
    and have late rows, relying on the map line's late=fold."""

    def _files(self, x, cur, accts):
        f = {"ticker.map": (f"RENAME QZOLD.{x} QZNEW.{x} 2025-04-01 "
                            f"late=fold\n"),
             "inputs/aa/a.tt": (
                 _bt("2024-01-10", f"QZOLD.{x}", 10, cur, 10.0)
                 + f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=separate\n"
                 + _bt("2025-05-06", f"QZOLD.{x}", 7, cur, 3.0)
                 + _bt("2025-06-03", f"QZNEW.{x}", -10, cur, 11.0))}
        for a, n in (("bb", 20), ("cc", 30)):
            if a in accts:
                f[f"inputs/{a}/{a}.tt"] = (
                    _bt("2024-02-10", f"QZOLD.{x}", n, cur, 10.0)
                    + _bt("2025-04-07", f"QZOLD.{x}", 5, cur, 10.0)
                    + _bt("2025-06-03", f"QZNEW.{x}", -(n + 5), cur, 11.0))
        if "ee" in accts:
            # ee holds QZOLD too and says late=fold in its own line.
            f["inputs/ee/e.tt"] = (
                _bt("2024-03-10", f"QZOLD.{x}", 4, cur, 10.0)
                + f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=fold\n"
                + _bt("2025-06-03", f"QZNEW.{x}", -4, cur, 11.0))
        if "dd" in accts:
            # dd never held QZOLD: only QZNEW, bought after the change.
            f["inputs/dd/d.tt"] = _bt("2025-06-01", f"QZNEW.{x}", 3, cur,
                                      10.0)
        return f

    def _project(self, td, country, accts):
        x, cur = _HOME[country]
        accounts = "".join(f'[accounts.{a}]\ntype = "taxable"\n\n'
                           for a in accts)
        root = projects_both(td, accounts=accounts,
                             files=self._files(x, cur, accts))[country]
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 0, _flat(r.stdout + r.stderr)[-3000:])
        return root, x

    def _sum(self, root):
        r = cli(root, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _home_kept(self, country):
        with tempfile.TemporaryDirectory() as td:
            root, x = self._project(td, country, ("dd", "ee", "aa", "bb"))
            before = self._sum(root)
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _flat(w.stdout + w.stderr))
            self.assertIn(f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=fold",
                          (root / "inputs/bb/renames.tt").read_text())
            self.assertIn("its line keeps every account's late= choice",
                          _flat(w.stdout + w.stderr))
            for a in ("dd", "ee", "aa"):
                self.assertFalse((root / f"inputs/{a}/renames.tt").exists())
            r = cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, _flat(r.stdout + r.stderr))
            self.assertEqual(self._sum(root), before)

    def test_canada_a_home_that_keeps_every_late(self):
        self._home_kept("canada")

    def test_usa_a_home_that_keeps_every_late(self):
        self._home_kept("usa")

    def test_no_home_keeps_it_names_the_line_to_add(self):
        with tempfile.TemporaryDirectory() as td:
            root, x = self._project(td, "canada", ("aa", "bb", "cc"))
            before = self._sum(root)
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertNotEqual(w.returncode, 0)
            out = _flat(w.stdout + w.stderr)
            line = f"RENAME 2025-04-01 QZOLD.{x} QZNEW.{x} late=fold"
            self.assertRegex(out, rf"account (bb|cc): the ticker change "
                                  rf"QZOLD.{x} -> QZNEW.{x} is booked "
                                  rf"2025-04-01 late=fold now \(ticker.map's "
                                  rf"late=, the account having no line of "
                                  rf"its own\)")
            self.assertIn(f"add `{line}` to inputs/", out)
            # Adding the line it names to that account settles it.
            acct = "cc" if "add `" + line + "` to inputs/cc" in out else "bb"
            (root / f"inputs/{acct}/own.tt").write_text(line + "\n")
            r = cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, _flat(r.stdout + r.stderr))
            w = cli(root, "format-map", "--write", "--no-backup")
            self.assertEqual(w.returncode, 0, _flat(w.stdout + w.stderr))
            r = cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, _flat(r.stdout + r.stderr))
            self.assertEqual(self._sum(root), before)



# ------------------------------------------------------------------ 8

_QT_DRIP_H = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _drip(sym, desc, qty, net, cur):
    return (f"2026-09-29 12:00:00 AM,2026-09-29 12:00:00 AM,REI,{sym},"
            f"{desc},{qty},0,0,0,{net:.2f},{cur},55500001,"   # pii-ok
            f"Dividend reinvestment,Individual margin\n")


class TestDripPricedInTheOtherCurrency(unittest.TestCase):

    def test_row_price(self):
        from taxjson.lib.brokerages.rbc_direct import reinvest_row_price
        self.assertEqual(reinvest_row_price(1, -44.98, 62.90, "CAD", "USD"),
                         44.98)
        self.assertEqual(reinvest_row_price(5, -37.31, 5.3783, "USD",
                                            "CAD"), 7.462)
        self.assertEqual(reinvest_row_price(5, -26.9, 5.38, "CAD", "CAD"),
                         5.38)

    def _run(self, country, csv):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(
                td, year=2026, files={"inputs/margin/q.csv": csv},
                canada={"source_currencies": ["USD"]},
                usa={"source_currencies": ["CAD"]})[country]
            r = cli(root, "run", "--no-input")
            out = _flat(r.stdout + r.stderr)
            self.assertEqual(r.returncode, 0, out[-3000:])
            rows = []
            for p in (root / "work").glob("margin_questrade*.json"):
                doc = json.loads(p.read_text())
                rows += [t for t in doc.get("transactions") or []
                         if t.get("action") == "BUYSELL"]
            return out, rows

    def _check(self, country):
        csv = _QT_DRIP_H + _drip(
            "QZPIPE", "QZPIPE CORP REINV@C$62.90 REC 09/15/26 PAY 09/29/26",
            1, -44.98, "USD") + _drip(
            "QZGOLD.TO", "QZGOLD CORP REINV@U$5.3783 REC 09/15/26 PAY "
            "09/29/26", 5, -37.31, "CAD")
        out, rows = self._run(country, csv)
        self.assertNotIn("is far from qty*price", out)
        got = sorted((t["symbol"], t["quantity"], t["net_amount"],
                      round(t["price"], 4)) for t in rows)
        self.assertEqual(got, [("QZGOLD.TO", 5.0, 37.31, 7.462),
                               ("QZPIPE.US", 1.0, 44.98, 44.98)])
        # The same currency, 4% off: still said.
        csv = _QT_DRIP_H + _drip(
            "QZGOLD.TO", "QZGOLD CORP REINV@C$10.00 REC 09/15/26 PAY "
            "09/29/26", 100, -1040.00, "CAD")
        out, _rows = self._run(country, csv)
        self.assertIn("is far from qty*price", out)

    def test_canada_drip_priced_in_the_other_currency(self):
        self._check("canada")

    def test_usa_drip_priced_in_the_other_currency(self):
        self._check("usa")


if __name__ == "__main__":
    unittest.main()
