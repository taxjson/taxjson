"""Missing history, readiness and opening-import fixes of 2026-10-29,
round 7 wave C (group missingopen). Every fixture is SYNTHETIC: made-up
tickers, ids and amounts.

Formerly: test_fix_1029_r7c_missingopen
"""
import io
import tempfile
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R

from taxjson.lib.tt_format import parse_tt_line
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.opening import OpeningError, apply_opening_cutoff

from _qa_project import gains, project, tj, toml
from _run_patch import patch_run
from _support.ib_missing_open import IB_HEAD, ib_fill, _ca_project


def flat(text):
    """Whitespace-collapsed: where a message wraps depends on the width."""
    return " ".join(text.split())


def opening(day, sym, qty, total, *, lot="", account="margin",
            currency="USD"):
    """The row a .tt OPENING line becomes."""
    line = f"OPENING {day} {sym} {qty} {currency} {total}" + (
        f" {lot}" if lot else "")
    return TaxTransaction(**parse_tt_line(line, account))


class TestUsSnapshotPriorYearSaleSettlingInJanuary(unittest.TestCase):
    """TJ-0961: a long sale traded Dec 31 that settles in January is the
    earlier year's on trade dates (the US default): the Dec 31 snapshot
    replaces it without refusing the run."""

    def _rows(self, sale_day, settle):
        return [
            tx("BUYSELL", "2024-03-04", "SYNQ", 20, 1000),
            tx("BUYSELL", sale_day, "SYNQ", -10, 700, settle=settle),
            opening("2025-12-31", "SYNQ", 10, 500, lot="2024-03-04"),
            tx("BUYSELL", "2026-02-02", "SYNQ", -10, 560,
               settle="2026-02-03"),
        ]

    def _apply(self, rows, **kw):
        return apply_opening_cutoff(rows, report=io.StringIO(), **kw)

    @rule(R.US_OPEN_03)
    def test_us_trade_date_sale_settling_in_january_is_replaced(self):
        rows = self._rows("2025-12-31", "2026-01-02")
        out = self._apply(rows, year=2026, country="usa",
                          base_currency="USD")
        self.assertEqual([(t.date, t.quantity) for t in out
                          if t.action == "BUYSELL"],
                         [("2025-12-31", 10.0), ("2026-02-02", -10.0)])

    @rule(R.US_OPEN_03)
    def test_us_settle_basis_still_refuses_it(self):
        rows = self._rows("2025-12-31", "2026-01-02")
        with self.assertRaises(OpeningError):
            self._apply(rows, year=2026, country="usa",
                        base_currency="USD", tax_date="settle")

    @rule(R.CA_OPEN_03)
    def test_canada_settlement_date_still_refuses_it(self):
        rows = self._rows("2025-12-31", "2026-01-02")
        with self.assertRaises(OpeningError) as cm:
            self._apply(rows, year=2026, country="canada")
        self.assertIn("would leave out 1 sale(s) of the 2026",
                      flat(str(cm.exception)))

    @rule(R.US_OPEN_03)
    def test_us_sale_traded_in_the_year_is_still_refused(self):
        rows = [
            tx("BUYSELL", "2024-03-04", "SYNQ", 20, 1000),
            tx("BUYSELL", "2026-01-05", "SYNQ", -10, 700,
               settle="2026-01-06"),
            opening("2026-01-06", "SYNQ", 10, 500, lot="2024-03-04"),
        ]
        with self.assertRaises(OpeningError):
            self._apply(rows, year=2026, country="usa",
                        base_currency="USD")

    @rule(R.US_OPEN_03)
    def test_us_run_accepts_the_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {
                "margin/ib.csv": IB_HEAD
                + ib_fill("2024-06-03", 20, 10, "O")
                + ib_fill("2025-12-31", -10, 15, "C")
                + ib_fill("2026-02-02", -10, 16, "C"),
                "margin/book.tt":
                    "OPENING 2025-12-31 SYNQ.US 10 USD 100 2024-06-03\n"})
            (root / "taxjson.toml").write_text(toml("usa", year=2026))
            tj(root, "run", "--no-input")
            sales = [g for g in gains(root) if g.get("qty")
                     and str(g.get("date", "")).startswith("2026")]
            self.assertEqual(len(sales), 1)
            self.assertAlmostEqual(float(sales[0]["gain"]), 60.0, places=2)


def _readiness(root):
    import json
    tj(root, "run", "--no-input")
    doc = json.loads(tj(root, "form-export", "--json").stdout)
    return doc, {i["kind"] for i in doc["readiness"]["issues"]}


class TestExhaustedUnknownOpeningKeepsALaterSaleUnbacked(unittest.TestCase):
    """TJ-0958: an OPENING cost=unknown of 5 used up by a 2024 sale hid
    the 2025 closing sale of 10 (no purchase) from the closing summary
    and from form-export's readiness."""

    CSV = (ib_fill("2024-06-03", -5, 10, "C", cur="CAD")
           + ib_fill("2025-06-02", -10, 15, "C", cur="CAD"))

    def test_used_up_opening_leaves_the_sale_with_no_purchase(self):
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, self.CSV,
                               "OPENING 2024-01-01 SYNQ.TO 5 cost=unknown\n")
            doc, kinds = _readiness(root)
            self.assertFalse(doc["readiness"]["complete"])
            # The broker codes the 2025 sale closing (C): the run reports
            # it by hand, at an unknown cost (CA-ACB-11, TJ-5349; it was a
            # sale with no purchase, an open short).
            self.assertIn("unknown-cost", kinds)
            self.assertNotIn("no-purchase", kinds)

    def test_control_an_uncoded_sale_past_the_opening_has_no_purchase(self):
        csv = (ib_fill("2024-06-03", -5, 10, "C", cur="CAD")
               + ib_fill("2025-06-02", -10, 15, "", cur="CAD"))
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, csv,
                               "OPENING 2024-01-01 SYNQ.TO 5 cost=unknown\n")
            doc, kinds = _readiness(root)
            self.assertFalse(doc["readiness"]["complete"])
            self.assertIn("no-purchase", kinds)

    def test_an_opening_that_covers_both_is_still_covered(self):
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, self.CSV,
                               "OPENING 2024-01-01 SYNQ.TO 15 cost=unknown\n")
            doc, kinds = _readiness(root)
            self.assertNotIn("no-purchase", kinds)
            self.assertFalse(doc["readiness"]["complete"])


class TestAnEarlierCoverDoesNotCoverALaterUnbackedSale(unittest.TestCase):
    """TJ-0957: a short opened in December and covered in January made
    the June closing sale with no purchase read as 'a short closed by a
    later purchase': in the totals, and form-export complete."""

    def test_june_sale_stays_with_no_purchase(self):
        # (the June sale with no code: one the broker codes closing is
        # reported by hand, kind unknown-cost: CA-ACB-11, TJ-5349)
        for code, kind in (("", "no-purchase"), ("C", "unknown-cost")):
            csv = (ib_fill("2024-12-02", -10, 10, "O", cur="CAD")
                   + ib_fill("2025-01-06", 10, 8, "C", cur="CAD")
                   + ib_fill("2025-06-02", -10, 15, code, cur="CAD"))
            with self.subTest(code=code), tempfile.TemporaryDirectory() as d:
                root = _ca_project(d, csv)
                doc, kinds = _readiness(root)
                self.assertFalse(doc["readiness"]["complete"])
                self.assertIn(kind, kinds)

    def test_a_later_cover_still_closes_the_sale(self):
        from taxjson.lib.first_run import BOOKED_SHORT_COVER, engine_booking
        from taxjson.lib.missing_history import (MissingHistoryCandidate,
                                                 MissingHistoryRow)
        import json
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            recs = [{"symbol": "SYNQ.TO", "direction": "SHORT", "qty": 10,
                     "date": "2025-01-06", "date_settle": "2025-01-07",
                     "id": "c1"},
                    {"symbol": "SYNQ.TO", "direction": "SHORT", "qty": 10,
                     "date": "2025-07-06", "date_settle": "2025-07-07",
                     "id": "c2"}]
            (cache / "margin_gains.json").write_text(
                json.dumps({"transactions": recs}))
            cand = MissingHistoryCandidate(
                symbol="SYNQ.TO", account="margin", currency="CAD",
                first_negative_date="2025-06-03", peak_short=-10.0,
                end_position=0.0, disposition_count=1, registered=False)

            def row(day):
                return MissingHistoryRow(
                    candidate=cand, affects_year=True,
                    in_year_dispositions=1, in_year_proceeds=150.0,
                    last_in_year_date=day,
                    in_year_short_sales=(("s1", 10.0, 10.0, day),))
            # June sale, July cover: closed by the later purchase.
            out = engine_booking(cache, [row("2025-06-03")], 2025,
                                 prefer_wash=False)
            self.assertEqual(out[("SYNQ.TO", "margin")], BOOKED_SHORT_COVER)


class TestARealShortDoesNotSizeTheMissingOpening(unittest.TestCase):
    """TJ-0960: a January closing sale of 5 with no purchase, then a
    broker-marked (IB code O) short of 10 covered in July: the written
    opening was 15, the real short counted as missing long shares."""

    CSV = (ib_fill("2025-01-06", -5, 10, "C", cur="CAD")
           + ib_fill("2025-02-03", 5, 20, "O", cur="CAD")
           + ib_fill("2025-05-05", -5, 30, "C", cur="CAD")
           + ib_fill("2025-06-02", -10, 40, "O", cur="CAD")
           + ib_fill("2025-07-02", 10, 30, "C", cur="CAD"))

    def test_opening_is_sized_on_the_missing_long_shares_only(self):
        import re
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, self.CSV)
            tj(root, "run", "--no-input")
            tj(root, "find-missing-history", "--write-missing-history")
            text = (root / "inputs" / "margin" /
                    "missing_history.tt").read_text()
            m = re.search(r"^OPENING \S+ SYNQ\.TO (\S+) cost=unknown",
                          text, re.M)
            self.assertIsNotNone(m, text)
            self.assertAlmostEqual(float(m.group(1)), 5.0)
            tj(root, "run", "--no-input")
            gain = sum(float(g.get("gain") or 0) for g in gains(root)
                       if g.get("qty") and not g.get("unknown_cost"))
            # The known May sale (50) and the short's cover (100).
            self.assertAlmostEqual(gain, 150.0, places=2)

    def test_sizing_walk_leaves_a_marked_short_out(self):
        from taxjson.lib.missing_history import synthesize_openings
        rows = [tx("BUYSELL", "2025-01-06", "SYNQ.TO", -5, 50,
                   currency="CAD", open_close="C"),
                tx("BUYSELL", "2025-06-02", "SYNQ.TO", -10, 400,
                   currency="CAD", open_close="O"),
                tx("BUYSELL", "2025-07-02", "SYNQ.TO", 10, 300,
                   currency="CAD", open_close="C")]
        _out, log = synthesize_openings(rows, {("SYNQ.TO", "margin")},
                                        flag_stale=False)
        self.assertEqual([e["opening_qty"] for e in log], [5.0])
        self.assertNotIn("short_again", log[0])


class TestOpeningImportKeepsAMiniOptionsContractSize(unittest.TestCase):
    """TJ-0963: `tjs opening` read multiplier 10 from the positions TOML
    but wrote an OPENING line without it: the exercised premium was
    spread over 100 delivered shares, not 10."""

    POSITIONS = ('[meta]\nas_of = "2024-12-31"\n[[holding]]\n'
                 'symbol = "SYNQ250620C00010000.US"\nquantity = 1\n'
                 'currency = "USD"\ntotal_cost = 10\nmultiplier = 10\n'
                 'acquired = "2024-06-03"\n')
    BOOK = ("ASSIGN 2025-06-02 10:00:00 SYNQ250620C00010000.US -1 USD 0 0 0\n"
            "ASSIGN 2025-06-02 10:01:00 SYNQ.US 5 USD 10 50 0\n"
            "BUYSELL 2025-06-03 10:00:00 SYNQ.US -5 USD 15 75 0\n"
            "ASSIGN 2025-06-04 10:00:00 SYNQ.US 5 USD 10 50 0\n")

    def test_line_round_trips_the_size(self):
        from taxjson.lib.tt_format import tx_to_tt_line
        line = "OPENING 2024-12-31 SYNQ250620C00010000.US 1 USD 10 x10"
        t = parse_tt_line(line, "margin")
        self.assertEqual(t["multiplier"], 10.0)
        self.assertAlmostEqual(t["price"], 1.0)
        self.assertTrue(tx_to_tt_line(t).endswith(" x10"))
        with self.assertRaises(ValueError):
            parse_tt_line("OPENING 2024-12-31 SYNQ.US 1 USD 10 x10",
                          "margin")

    def test_without_its_mult_line_the_option_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/book.tt": self.BOOK})
            (root / "taxjson.toml").write_text(toml("usa", year=2025))
            pos = root / "positions.toml"
            pos.write_text(self.POSITIONS)
            r = tj(root, "opening", "margin", str(pos), "--date",
                   "2024-12-31", "--dry-run", "--details", check=False)
            out = r.stdout + r.stderr
            self.assertIn("add `MULT SYNQ 10` to ticker.map", out)
            self.assertNotIn("OPENING 2024-12-31 SYNQ250620C00010000.US",
                             out)

    def test_us_opening_import_and_split_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/book.tt": self.BOOK},
                           ticker_map="MULT SYNQ 10\n")
            (root / "taxjson.toml").write_text(toml("usa", year=2025))
            pos = root / "positions.toml"
            pos.write_text(self.POSITIONS)
            tj(root, "opening", "margin", str(pos), "--date", "2024-12-31")
            text = (root / "inputs" / "margin" /
                    "opening_2024-12-31.tt").read_text()
            self.assertIn("SYNQ250620C00010000.US 1 USD 10.00 2024-06-03 x10",
                          text)
            tj(root, "run", "--no-input")
            sales = [g for g in gains(root) if g.get("qty")
                     and g.get("symbol") == "SYNQ.US"]
            self.assertEqual(len(sales), 1)
            # 5 shares at 10 plus half the 10 premium: basis 55.
            self.assertAlmostEqual(float(sales[0]["gain"]), 20.0, places=2)


class TestOpeningImportKeepsAHoldingsFilesBaseCost(unittest.TestCase):
    """TJ-0990: a holdings file (taxjson-export) states the native cost
    (USD 100) and the books' Canadian cost at the acquisition dates
    (base_total_cost CAD 150); `tjs opening` wrote the USD cost, which
    the run converted at the snapshot day's rate."""

    HEAD = ('schema_version = "1.2"\n[meta]\naccount = "margin"\n'
            '[[holding]]\nsymbol = "SYNQ.US"\naccount = "margin"\n'
            'asset_type = "equity"\nquantity = 10.0\ncurrency = "USD"\n')

    def _opening(self, body):
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/ib.csv": IB_HEAD})
            (root / "taxjson.toml").write_text(toml("canada", year=2026))
            snap = root / "snapshot.toml"
            snap.write_text(self.HEAD + body)
            r = tj(root, "opening", "margin", str(snap), "--date",
                   "2025-12-31", "--dry-run", check=False)
            return r.stdout + r.stderr

    def test_the_base_cost_is_the_opening_cost(self):
        out = self._opening('total_cost = 100.0\nbase_currency = "CAD"\n'
                            'base_total_cost = 150.0\n')
        self.assertIn("OPENING 2025-12-31 SYNQ.US 10 CAD 150.00", out)

    def test_a_base_cost_beside_an_unknown_native_cost(self):
        out = self._opening('cost_unknown = true\ncost_source = "blended"\n'
                            'base_currency = "CAD"\nbase_total_cost = 150.0\n')
        self.assertIn("OPENING 2025-12-31 SYNQ.US 10 CAD 150.00", out)

    def test_without_a_base_cost_the_native_cost_stays(self):
        out = self._opening('total_cost = 100.0\n')
        self.assertIn("OPENING 2025-12-31 SYNQ.US 10 USD 100.00", out)

    def test_a_base_cost_in_another_currency_is_not_used(self):
        out = self._opening('total_cost = 100.0\nbase_currency = "EUR"\n'
                            'base_total_cost = 90.0\n')
        self.assertIn("OPENING 2025-12-31 SYNQ.US 10 USD 100.00", out)


class TestOpeningCutoffFollowsAnotherAccountsRename(unittest.TestCase):
    """TJ-0964: account a's snapshot of NEW replaces its OLD purchase
    too, also when only account b's export carries the OLD -> NEW
    conversion (a security's event, applied to every account)."""

    SPLIT = "SPLIT 2025-06-02 10:00:00 SYNO.TO SYNN.TO 2\n"

    def _run(self, split_in_a):
        import json
        a = ("BUYSELL 2024-06-03 10:00:00 SYNO.TO 10 CAD 10 100 0\n"
             + (self.SPLIT if split_in_a else "")
             + "BUYSELL 2025-06-10 10:00:00 SYNN.TO 4 CAD 30 120 0\n"
             "OPENING 2025-06-30 SYNN.TO 24 CAD 220\n"
             "BUYSELL 2025-07-02 10:00:00 SYNN.TO -24 CAD 20 480 0\n")
        b = ("BUYSELL 2024-06-03 10:00:00 SYNO.TO 1 CAD 10 10 0\n"
             + self.SPLIT
             + "BUYSELL 2025-06-03 10:00:00 SYNN.TO -2 CAD 10 20 0\n")
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/book.tt": a, "other/book.tt": b},
                           extra_accounts='[accounts.other]\n'
                                          'type = "taxable"\n')
            (root / "taxjson.toml").write_text(toml(
                "canada", year=2025,
                extra_accounts='[accounts.other]\ntype = "taxable"\n'))
            tj(root, "run", "--no-input")
            doc = json.loads((root / "work" / ".blend_gains_wash.json")
                             .read_text())
            sales = [g for g in doc["transactions"] if g.get("qty")]
            held = [i for i in doc.get("inventory") or []
                    if abs(float(i.get("qty") or i.get("quantity") or 0))
                    > 1e-9]
            return (round(sum(float(g["gain"]) for g in sales), 4),
                    round(sum(float(g["proceeds"]) for g in sales), 4),
                    held)

    @rule(R.CA_OPEN_03)
    def test_the_rename_in_one_account_only_matches_both(self):
        self.assertEqual(self._run(False), self._run(True))
        gain, proceeds, held = self._run(False)
        self.assertEqual((gain, proceeds, held), (270.0, 500.0, []))


class TestConvergingRenamesAreNotSizedAsOneChain(unittest.TestCase):
    """TJ-0959: SYNA -> SYNN x2 and SYNB -> SYNN x3, then a sale of 60
    SYNN with no purchase: the written opening was 10 (60 / (2 x 3)),
    which its replay under SYNA turned into 20 SYNN."""

    def _book(self, shape):
        first = "SYNM.TO" if shape == "serial" else "SYNN.TO"
        b = f"SPLIT 2025-06-02 10:00:00 SYNA.TO {first} 2\n"
        if shape != "single":
            src = "SYNM.TO" if shape == "serial" else "SYNB.TO"
            b += f"SPLIT 2025-06-03 10:00:00 {src} SYNN.TO 3\n"
        return b + "BUYSELL 2025-06-04 10:00:00 SYNN.TO -60 CAD 10 600 0\n"

    def _write(self, shape):
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, "", year=2025)
            (root / "inputs" / "margin" / "book.tt").write_text(
                self._book(shape))
            tj(root, "run", "--no-input")
            r = tj(root, "find-missing-history", "--write-missing-history")
            f = root / "inputs" / "margin" / "missing_history.tt"
            return (f.read_text() if f.exists() else ""), r.stdout + r.stderr

    def test_siblings_are_not_sized(self):
        text, out = self._write("siblings")
        self.assertNotIn("SYNN.TO", "\n".join(
            ln for ln in text.splitlines() if ln.startswith("OPENING")))
        self.assertIn("SYNA.TO -> SYNN.TO x2 and SYNB.TO -> SYNN.TO x3",
                      flat(out))

    def test_serial_chain_is_still_sized(self):
        text, _out = self._write("serial")
        self.assertIn("SYNN.TO 10 cost=unknown", text)

    def test_synthesize_logs_the_converging_renames(self):
        from taxjson.lib.missing_history import synthesize_openings
        rows = [tx("SPLIT", "2025-06-02", "SYNA.TO", 2, 0, currency="",
                   symbol_new="SYNN.TO"),
                tx("SPLIT", "2025-06-03", "SYNB.TO", 3, 0, currency="",
                   symbol_new="SYNN.TO"),
                tx("BUYSELL", "2025-06-04", "SYNN.TO", -60, 600,
                   currency="CAD")]
        _out, log = synthesize_openings(rows, {("SYNN.TO", "margin")},
                                        flag_stale=False)
        e = next(x for x in log if x["symbol"] == "SYNN.TO")
        self.assertFalse(e["inserted"])
        self.assertEqual(e["converging"], ["SYNA.TO -> SYNN.TO x2",
                                           "SYNB.TO -> SYNN.TO x3"])


class TestSnapshotHoldsTheExercisedPremiumOnce(unittest.TestCase):
    """TJ-0962: a call bought for 100 and exercised into 100 shares for
    1000; an OPENING snapshot the next day of those shares at 1100. The
    premium is in the snapshot's cost: neither the snapshot row (US) nor
    a later purchase (Canada) may take it again."""

    OPT = "SYNQ250620C00010000.US"

    def _book(self, cur, snapshot, later_buy):
        b = (f"BUYSELL 2024-06-03 10:00:00 {self.OPT} 1 {cur} 1 100 0\n"
             f"ASSIGN 2025-06-02 10:00:00 {self.OPT} -1 {cur} 0 0 0\n"
             f"ASSIGN 2025-06-02 10:01:00 SYNQ.US 100 {cur} 10 1000 0\n")
        if snapshot:
            b += f"OPENING 2025-06-03 SYNQ.US 100 {cur} 1100 2025-06-02\n"
        if later_buy:
            b += (f"BUYSELL 2025-06-04 10:00:00 SYNQ.US 100 {cur} 12 1200 0\n"
                  f"BUYSELL 2025-06-10 10:00:00 SYNQ.US -200 {cur} 13 2600 0\n")
        else:
            b += f"BUYSELL 2025-06-10 10:00:00 SYNQ.US -100 {cur} 12 1200 0\n"
        return b

    def _gain(self, country, snapshot, later_buy):
        cur = "USD" if country == "usa" else "CAD"
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/book.tt": self._book(
                cur, snapshot, later_buy)})
            (root / "taxjson.toml").write_text(toml(country, year=2025))
            tj(root, "run", "--no-input")
            sales = [g for g in gains(root) if g.get("qty")
                     and "SYNQ.US" == g.get("symbol")]
            return round(sum(float(g["gain"]) for g in sales), 4)

    @rule(R.US_OPEN_03)
    def test_us_snapshot_cost_is_not_raised_again(self):
        self.assertEqual(self._gain("usa", True, False), 100.0)
        self.assertEqual(self._gain("usa", False, False), 100.0)

    @rule(R.CA_OPEN_03)
    def test_canada_later_purchase_does_not_take_the_premium(self):
        self.assertEqual(self._gain("canada", True, True), 300.0)
        self.assertEqual(self._gain("canada", False, True), 300.0)


class TestSnapshotAfterARenameHoldsTheExercisedPremiumOnce(unittest.TestCase):
    """TJ-5005 (the branch TJ-0962 left open): a call bought for 100 and
    exercised into 100 SYNO shares for 1000; SYNO renamed SYNW the next
    day; an accurate OPENING snapshot of the 100 SYNW at 1100 the day
    after. The option staged its premium under SYNO, where no snapshot
    was found, and a later, unrelated purchase under the reused SYNO
    name took it: cost 1300 and a total gain of 100, against 200 for the
    same history without the redundant snapshot."""

    OPT = "SYNO250620C00010000.US"

    def _book(self, cur, snapshot, late="", parts=False, marked_later=False):
        delivery = (
            f"ASSIGN 2025-06-02 10:01:00 SYNO.US 60 {cur} 10 600 0\n"
            f"ASSIGN 2025-06-02 10:02:00 SYNO.US 40 {cur} 10 400 0\n"
            if parts else
            f"ASSIGN 2025-06-02 10:01:00 SYNO.US 100 {cur} 10 1000 0\n")
        b = (f"BUYSELL 2024-06-03 10:00:00 {self.OPT} 1 {cur} 1 100 0\n"
             f"ASSIGN 2025-06-02 10:00:00 {self.OPT} -1 {cur} 0 0 0\n"
             + delivery
             + f"RENAME 2025-06-03 SYNO.US SYNW.US{late}\n")
        if snapshot:
            b += f"OPENING 2025-06-04 SYNW.US 100 {cur} 1100 2025-06-02\n"
        b += (f"BUYSELL 2025-06-05 10:00:00 SYNO.US 100 {cur} 12 1200 0\n"
              f"BUYSELL 2025-06-10 10:00:00 SYNW.US -100 {cur} 12 1200 0\n"
              f"BUYSELL 2025-06-10 10:05:00 SYNO.US -100 {cur} 13 1300 0\n")
        return b

    def _sales(self, country, snapshot, **kw):
        cur = "USD" if country == "usa" else "CAD"
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/book.tt": self._book(
                cur, snapshot, **kw)})
            (root / "taxjson.toml").write_text(toml(country, year=2025))
            tj(root, "run", "--no-input")
            return sorted((g["symbol"], round(float(g["cost"]), 2),
                           round(float(g["gain"]), 2))
                          for g in gains(root) if g.get("qty")
                          and g.get("symbol") in ("SYNO.US", "SYNW.US"))

    ONCE = [("SYNO.US", 1200.0, 100.0), ("SYNW.US", 1100.0, 100.0)]

    @rule(R.CA_OPEN_03)
    def test_canada_the_reused_old_name_does_not_take_the_premium(self):
        self.assertEqual(self._sales("canada", True), self.ONCE)
        self.assertEqual(self._sales("canada", False), self.ONCE)

    @rule(R.US_OPEN_03)
    def test_us_the_reused_old_name_does_not_take_the_premium(self):
        self.assertEqual(self._sales("usa", True), self.ONCE)
        self.assertEqual(self._sales("usa", False), self.ONCE)

    @rule(R.CA_OPEN_03)
    def test_canada_declared_separate_and_a_delivery_in_two_rows(self):
        self.assertEqual(self._sales("canada", True, late=" late=separate"),
                         self.ONCE)
        self.assertEqual(self._sales("canada", True, parts=True), self.ONCE)


class TestRenamedSnapshotPremiumGuards(unittest.TestCase):
    """TJ-5005, its two other ends. (a) The common case: no row of the
    old ticker is left after the cut-off, and the exercise was booked as
    cash-settled: the option's loss of the premium on top of the
    snapshot's cost. (b) What the snapshot does NOT hold stays as
    without it: the replaced row is the delivery only when it is marked
    ASSIGN or at the strike, the exercise is on or before the snapshot,
    and a replaced row is one exercise's delivery, never two."""

    OPT = "SYNO250620C00010000.US"
    OPT2 = "SYNO250620C00011000.US"
    BUY = "BUYSELL 2024-06-03 10:00:00 %s 1 CUR 1 100 0" % OPT
    EX = "ASSIGN 2025-06-02 10:00:00 %s -1 CUR 0 0 0" % OPT
    RENAME = "RENAME 2025-06-03 SYNO.US SYNW.US"
    SELL_NEW = "BUYSELL 2025-06-20 10:00:00 SYNW.US -%d CUR 12 %d 0"
    SELL_OLD = "BUYSELL 2025-06-20 10:05:00 SYNO.US -100 CUR 13 1300 0"

    def _run(self, country, lines, snapshot=True):
        """(the gains by symbol, the total, the run's text)."""
        cur = "USD" if country == "usa" else "CAD"
        book = "".join(line.replace("CUR", cur) + "\n" for line in lines
                       if snapshot or not line.startswith("OPENING"))
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/book.tt": book})
            (root / "taxjson.toml").write_text(toml(country, year=2025))
            r = tj(root, "run", "--no-input", "--details")
            rows = sorted((g["symbol"], round(float(g["cost"]), 2),
                           round(float(g["gain"]), 2))
                          for g in gains(root)
                          if g.get("qty") and "gain" in g)
            # (The engine's notes are in its stage's .diag.)
            diag = root / "work" / "margin_gains.json.diag"
            diag = diag.read_text() if diag.exists() else ""
        return rows, sum(g[2] for g in rows), flat(
            r.stdout + r.stderr + diag)

    def _same_as_without(self, country, lines):
        with_snap = self._run(country, lines)
        without = self._run(country, lines, snapshot=False)
        self.assertEqual(with_snap[:2], without[:2])
        return with_snap

    NO_OLD_TRADE = [
        BUY, EX, "ASSIGN 2025-06-02 10:01:00 SYNO.US 100 CUR 10 1000 0",
        RENAME, "OPENING 2025-06-04 SYNW.US 100 CUR 1100 2025-06-02",
        SELL_NEW % (100, 1200)]

    @rule(R.CA_OPEN_03)
    def test_canada_no_later_trade_of_the_old_ticker(self):
        rows, total, text = self._same_as_without("canada", self.NO_OLD_TRADE)
        self.assertEqual((rows, total),
                         ([("SYNW.US", 1100.0, 100.0)], 100.0))
        self.assertNotIn("cash-settled", text)

    @rule(R.US_OPEN_03)
    def test_us_no_later_trade_of_the_old_ticker(self):
        rows, total, text = self._same_as_without("usa", self.NO_OLD_TRADE)
        self.assertEqual((rows, total),
                         ([("SYNW.US", 1100.0, 100.0)], 100.0))
        self.assertNotIn("cash-settled", text)

    @rule(R.CA_OPEN_03)
    def test_a_plain_replaced_row_is_not_a_later_exercises_delivery(self):
        # A market purchase (not at the strike) is replaced; a call on
        # the reused old ticker is bought and exercised after the
        # snapshot: its premium is its own delivery's.
        rows, total, _ = self._same_as_without("canada", [
            "BUYSELL 2025-06-02 10:01:00 SYNO.US 150 CUR 9 1350 0",
            self.RENAME, "OPENING 2025-06-04 SYNW.US 150 CUR 1350 2025-06-02",
            self.BUY.replace("2024-06-03 10:00:00", "2025-06-04 11:00:00"),
            self.EX.replace("2025-06-02", "2025-06-05"),
            "BUYSELL 2025-06-05 10:01:00 SYNO.US 100 CUR 10 1000 0",
            self.SELL_NEW % (150, 1800), self.SELL_OLD])
        self.assertEqual(rows, [("SYNO.US", 1100.0, 200.0),
                                ("SYNW.US", 1350.0, 450.0)])

    @rule(R.US_OPEN_03)
    def test_us_an_exercise_after_the_snapshot_keeps_its_premium(self):
        # The replaced row is at the strike, but the exercise is dated
        # after the snapshot: the snapshot cannot hold its delivery.
        _rows, total, _ = self._same_as_without("usa", [
            "BUYSELL 2025-06-02 10:01:00 SYNO.US 100 CUR 10 1000 0",
            self.RENAME, "OPENING 2025-06-04 SYNW.US 100 CUR 1000 2025-06-02",
            self.BUY.replace("2024-06-03", "2025-06-04"),
            self.EX.replace("2025-06-02", "2025-06-05"),
            "BUYSELL 2025-06-05 10:01:00 SYNO.US 100 CUR 10 1000 0",
            self.SELL_NEW % (100, 1200), self.SELL_OLD])
        self.assertEqual(total, 400.0)

    @rule(R.CA_OPEN_03)
    def test_a_replaced_delivery_is_one_exercises_never_two(self):
        _rows, total, _ = self._same_as_without("canada", [
            self.BUY, self.EX,
            "ASSIGN 2025-06-02 10:01:00 SYNO.US 100 CUR 10 1000 0",
            self.RENAME, "OPENING 2025-06-04 SYNW.US 100 CUR 1100 2025-06-02",
            "BUYSELL 2025-06-04 11:00:00 %s 1 CUR 1 100 0" % self.OPT2,
            "ASSIGN 2025-06-05 10:00:00 %s -1 CUR 0 0 0" % self.OPT2,
            "BUYSELL 2025-06-05 10:01:00 SYNO.US 100 CUR 11 1100 0",
            self.SELL_NEW % (100, 1200), self.SELL_OLD])
        self.assertEqual(total, 200.0)

    @rule(R.CA_OPEN_03)
    def test_two_rows_that_can_each_be_the_delivery_are_said(self):
        # A plain purchase at the strike before the snapshot and another
        # after it, under the new name: the books cannot tell which the
        # call delivered. The total of the books without the snapshot
        # (the premium once, realized on the option, as before), and said.
        lines = [
            "BUYSELL 2025-06-01 10:01:00 SYNO.US 100 CUR 10 1000 0",
            self.BUY, self.EX, self.RENAME,
            "OPENING 2025-06-04 SYNW.US 100 CUR 1000 2025-06-01",
            "BUYSELL 2025-06-05 10:01:00 SYNW.US 100 CUR 10 1000 0",
            self.SELL_NEW % (200, 2400)]
        rows, total, text = self._run("canada", lines)
        self.assertEqual(rows, [(self.OPT, 100.0, -100.0),
                                ("SYNW.US", 2000.0, 400.0)])
        self.assertEqual(total, self._run("canada", lines, False)[1])
        self.assertIn(f"{self.OPT}: exercised or assigned on 2025-06-02, "
                      f"and SYNO.US was renamed before an opening snapshot "
                      f"replaced its rows: a replaced row at the strike and "
                      f"the row of 2025-06-05 after the snapshot can each "
                      f"be its delivery", text)
        self.assertIn("If a replaced row is its delivery, write that row as "
                      "ASSIGN; if the row of 2025-06-05 is, write it as "
                      "ASSIGN and write the option under the renamed ticker "
                      "(SYNW.US), and its premium rolls into that row.", text)

    @rule(R.CA_OPEN_03)
    def test_a_marked_delivery_under_the_new_name_is_said_with_what_settles_it(self):
        # The delivery is the row after the snapshot, marked ASSIGN,
        # under the new name: the premium does not follow the rename
        # (TJ-5120), so it stays on the option; said, with the remedy
        # that works (the option written under the renamed ticker).
        lines = [
            "BUYSELL 2025-06-01 10:01:00 SYNO.US 100 CUR 10 1000 0",
            self.BUY, self.EX, self.RENAME,
            "OPENING 2025-06-04 SYNW.US 100 CUR 1000 2025-06-01",
            "ASSIGN 2025-06-05 10:01:00 SYNW.US 100 CUR 10 1000 0",
            self.SELL_NEW % (200, 2400)]
        rows, total, text = self._run("canada", lines)
        self.assertEqual(rows, [(self.OPT, 100.0, -100.0),
                                ("SYNW.US", 2000.0, 400.0)])
        self.assertIn("its delivery, the ASSIGN row of 2025-06-05, follows "
                      "the snapshot under the new name SYNW.US. The option "
                      "names SYNO.US, so its premium is not rolled into "
                      "those shares: it stays on the option, as a cash "
                      "settlement would. To roll it, write the option under "
                      "the renamed ticker (SYNW.US)", text)
        rows, total, text = self._run("canada", [
            line.replace("SYNO2506", "SYNW2506") for line in lines])
        self.assertEqual(rows, [("SYNW.US", 2100.0, 300.0)])
        self.assertNotIn("was renamed before an opening snapshot", text)

    def _amount_on_the_row(self, country, amount, fee, said):
        # The exercise row itself carries an amount. Its cash cannot tell
        # a settlement from a commission or cash in lieu (owner,
        # 2026-11-23): where a snapshot holds its delivery the row is
        # booked as delivered, as it always was on the same-symbol path
        # (the snapshot's cost holds the premium: the figures of the row
        # with no amount), and an amount beyond the row's fee is SAID,
        # with both readings. A commission in the fee column is not.
        ex = "ASSIGN 2025-06-02 10:00:00 %s -1 CUR 0 %s %s" % (
            self.OPT, amount, fee)
        for old, rename in (("SYNO.US", [self.RENAME]), ("SYNW.US", [])):
            opt = self.OPT.replace("SYNO", old[:4])
            lines = ([self.BUY.replace(self.OPT, opt),
                      ex.replace(self.OPT, opt),
                      f"ASSIGN 2025-06-02 10:01:00 {old} 100 CUR 10 1000 0"]
                     + rename
                     + ["OPENING 2025-06-04 SYNW.US 100 CUR 1100 2025-06-02",
                        self.SELL_NEW % (100, 1200)])
            rows, total, text = self._run(country, lines)
            self.assertEqual(rows, [("SYNW.US", 1100.0, 100.0)], old)
            self.assertEqual(
                f"{opt}: the exercise or assignment row of 2025-06-02 "
                f"carries {said or '0'}" in text, bool(said),
                (old, text[-900:]))
            self.assertEqual("is booked as DELIVERED" in text, bool(said))
            if said:
                self.assertIn(
                    "and is booked as DELIVERED: its shares are in the "
                    "opening snapshot of SYNW.US", text)
                self.assertIn(
                    "If shares were delivered, write the row with no amount "
                    "(a charge or cash in lieu as a row of its own). If it "
                    "was settled in cash, write it as a closing BUYSELL of "
                    "the option at the settlement amount", text)

    @rule(R.CA_OPEN_03)
    def test_canada_an_amount_on_a_delivered_exercise_row_is_said(self):
        self._amount_on_the_row("canada", "0.5", "0", "0.50 CAD")
        self._amount_on_the_row("canada", "0", "24.95", "")

    @rule(R.US_OPEN_03)
    def test_us_an_amount_on_a_delivered_exercise_row_is_said(self):
        self._amount_on_the_row("usa", "300", "0", "300.00 USD")

    @rule(R.CA_OPEN_03)
    def test_the_cash_settlement_the_warning_names_is_read_as_one(self):
        # A plain purchase at the strike in the snapshot, and a row with
        # 300 of settlement cash: booked as delivered and said (main's
        # figure on this path); written as the closing BUYSELL the
        # warning names, the option's 200 is realized.
        lines = ["BUYSELL 2025-06-01 10:00:00 SYNW.US 100 CUR 10 1000 0",
                 self.BUY.replace("SYNO", "SYNW"),
                 "ASSIGN 2025-06-02 10:00:00 %s -1 CUR 3 300 0"
                 % self.OPT.replace("SYNO", "SYNW"),
                 "OPENING 2025-06-04 SYNW.US 100 CUR 1000 2025-06-01",
                 self.SELL_NEW % (100, 1200)]
        rows, total, text = self._run("canada", lines)
        self.assertEqual(rows, [("SYNW.US", 1000.0, 200.0)])
        self.assertIn("carries 300.00 CAD and is booked as DELIVERED", text)
        rows, total, text = self._run("canada", [
            line.replace("ASSIGN 2025-06-02", "BUYSELL 2025-06-02")
            for line in lines])
        self.assertEqual(total, 400.0)
        self.assertNotIn("is booked as DELIVERED", text)


class TestTheLedgerReadsADeliveryCutUnderItsOldName(unittest.TestCase):
    """TJ-5005, at the two ends: the cut-off names the replaced row's own
    symbol on the snapshot's row, and the premium ledger stages nothing
    for the assignment that row delivered."""

    OPT = "SYNO250620C00010000.US"

    def _rows(self, *, delivery_day="2025-06-02", qty=100.0):
        def row(action, day, sym, q, net, **kw):
            return TaxTransaction(
                action=action, date=day, date_settle=day, time="10:00:00",
                symbol=sym, quantity=q, currency="CAD",
                price=abs(net / q) if q else 0.0, net_amount=net,
                account="margin", **kw)
        return [
            row("ASSIGN", "2025-06-02", self.OPT, -1.0, 0.0, id="o1"),
            row("ASSIGN", delivery_day, "SYNO.US", qty, -10.0 * qty,
                id="d1"),
            row("SPLIT", "2025-06-03", "SYNO.US", 1.0, 0.0,
                symbol_new="SYNW.US", id="r1"),
            opening("2025-06-04", "SYNW.US", 100, 1100, lot="2025-06-02"),
            row("BUYSELL", "2025-06-05", "SYNO.US", 100.0, -1200.0, id="b1"),
        ]

    def _ledger(self, rows):
        from taxjson.engine.premium_ledger import _AssignPremiumLedger
        from taxjson.lib.option_symbols import is_option_symbol
        return _AssignPremiumLedger(
            rows, lambda t: t.action in ("BUYSELL", "ASSIGN")
            and not is_option_symbol(t.symbol))

    def _staged(self, **kw):
        from decimal import Decimal
        kept = apply_opening_cutoff(self._rows(**kw), report=io.StringIO())
        ledger = self._ledger(kept)
        opt = next(t for t in kept if t.symbol == self.OPT)
        ledger.stage(opt, "SYNO.US", Decimal("-100"))
        return kept, ledger.undrained()

    @rule(R.CA_OPEN_03)
    def test_the_snapshot_row_names_the_old_symbols_delivery(self):
        kept, staged = self._staged()
        self.assertEqual([r.id for r in kept if r.id in ("o1", "d1", "r1", "b1")],
                         ["o1", "b1"])
        snap, = [r for r in kept if r.symbol == "SYNW.US"]
        self.assertEqual(snap.opening_cut_legs,
                         "2025-06-02 100.0 10.0 ASSIGN SYNO.US")
        self.assertEqual(staged, {})

    @rule(R.CA_OPEN_03)
    def test_a_replaced_row_that_is_not_the_delivery_holds_nothing(self):
        import contextlib
        from decimal import Decimal
        # Too few shares to be what the call delivered (said), and a row
        # far from the exercise: the premium stays staged (and is named
        # undrained when no row takes it).
        for kw, said in ((dict(qty=40.0), "come to 40 shares, fewer than "
                                           "it delivers"),
                         (dict(delivery_day="2025-05-01"), "")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                _kept, staged = self._staged(**kw)
            self.assertEqual(staged, {("margin", "SYNO.US"): Decimal("-100")},
                             kw)
            self.assertIn(said, flat(err.getvalue()))
            self.assertEqual("ATTENTION" in err.getvalue(), bool(said))

    @rule(R.CA_OPEN_03)
    def test_the_verdict_is_made_once_and_a_row_claimed_once(self):
        import contextlib
        kept = apply_opening_cutoff(self._rows(), report=io.StringIO())
        twin = TaxTransaction(
            action="ASSIGN", date="2025-06-02", date_settle="2025-06-02",
            time="10:00:30", symbol="SYNO250620C00010500.US", quantity=-1.0,
            currency="CAD", price=0.0, net_amount=0.0, account="margin",
            id="o2")
        ledger = self._ledger(kept + [twin])
        first = next(t for t in kept if t.symbol == self.OPT)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ledger.renamed_delivery_underlying(first),
                             "SYNO.US")
            # Asked again (the walks do): the same answer.
            self.assertEqual(ledger.renamed_delivery_underlying(first),
                             "SYNO.US")
            # The one replaced row is the first exercise's: the second
            # exercise of that day finds none left.
            self.assertIsNone(ledger.renamed_delivery_underlying(twin))
            self.assertIsNone(ledger.renamed_delivery_underlying(twin))
        self.assertEqual(err.getvalue(), "")

    @rule(R.CA_OPEN_03)
    def test_a_symbol_that_is_not_one_word_is_said(self):
        # The leg's fifth token cannot spell `SYN O.US`: the snapshot's
        # row names no such row, and the cut-off says so.
        rows = self._rows()
        for r in rows:
            if r.symbol == "SYNO.US" and r.id != "b1":
                r.symbol = "SYN O.US"
        report = io.StringIO()
        kept = apply_opening_cutoff(rows, report=report)
        snap, = [r for r in kept if r.symbol == "SYNW.US"]
        self.assertEqual(snap.opening_cut_legs, "")
        text = flat(report.getvalue())
        self.assertIn("the opening snapshot of SYNW.US replaces rows of "
                      "'SYN O.US', renamed to it, and a symbol with a space "
                      "or a ';' cannot be named on the snapshot's row", text)
        self.assertEqual(text.count("cannot be named"), 1)


class TestRedoRollbackKeepsAConcurrentElectionOfTheEvent(unittest.TestCase):
    """TJ-0955: an interrupted `elections --redo --event ID` put its cleared
    record back over an election of the same event that another command
    saved while it waited at the prompt."""

    def _redo(self, root, child):
        import argparse
        import contextlib
        from unittest import mock
        import sys
        from taxjson.cli import elections as cli_elect
        args = argparse.Namespace(
            dir=str(root), account="margin", redo=True, reset=False,
            event="event-a", set=None, hint=None, pending=False,
            json=False)
        with patch_run("run_to_file", child), \
                patch_run("group_inputs",
                                  lambda _d: {"ib": [_d / "zz_ib.csv"]}), \
                patch_run("_extracted_event_ids",
                                  lambda *a, **k: {"event-a"}), \
                mock.patch.object(sys.stdin, "isatty", lambda: True), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as cm:
            cli_elect.cmd_elections(args)
        return str(cm.exception.code)

    def _setup(self, d):
        from _support.corp_elect_project import _project
        from taxjson.lib import corp_actions as ca
        root = _project(d)
        (root / "inputs" / "margin" / "zz_ib.csv").write_text("x\n")
        path = root / "inputs" / "margin" / "elections.json"
        m = ca.Manifest()
        m.set(ca.ElectionRecord(event_id="event-a", summary="A",
                                election="taxable_deemed_dividend",
                                hints={"fmv_per_share": 2.5}))
        m.save(path)
        return root, path

    def test_a_concurrent_set_of_the_same_event_is_kept(self):
        from taxjson.lib import corp_actions as ca
        with tempfile.TemporaryDirectory() as d:
            root, path = self._setup(d)

            def child(cmd, out, **kw):
                # `elections --set event-a=ignore` from another terminal,
                # then EOF at this redo's prompt.
                ca.Manifest.update(path, lambda mm: mm.set(
                    ca.ElectionRecord(event_id="event-a", summary="A",
                                      election="ignore")) or True)
                raise KeyboardInterrupt
            msg = self._redo(root, child)
            self.assertIn("--redo interrupted", msg)
            self.assertIn("except event-a", flat(msg))
            rec = ca.Manifest.load(path).records["event-a"]
            self.assertEqual((rec.election, rec.hints), ("ignore", {}))

    def test_the_redo_s_own_answer_is_still_put_back(self):
        import json
        from dataclasses import asdict
        from taxjson.lib import corp_actions as ca
        with tempfile.TemporaryDirectory() as d:
            root, path = self._setup(d)

            def child(cmd, out, **kw):
                # The child saved an answer, then was interrupted.
                rec = ca.ElectionRecord(event_id="event-a", summary="A",
                                        election="ignore")
                ca.Manifest.update(path, lambda mm: mm.set(rec) or True)
                log = cmd[cmd.index("--saved-log") + 1]
                with open(log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(rec)) + "\n")
                raise KeyboardInterrupt
            msg = self._redo(root, child)
            self.assertIn("restored unchanged", flat(msg))
            rec = ca.Manifest.load(path).records["event-a"]
            self.assertEqual((rec.election, rec.hints),
                             ("taxable_deemed_dividend",
                              {"fmv_per_share": 2.5}))

    def test_nothing_saved_meanwhile_restores_the_old_record(self):
        from taxjson.lib import corp_actions as ca
        with tempfile.TemporaryDirectory() as d:
            root, path = self._setup(d)

            def child(cmd, out, **kw):
                raise KeyboardInterrupt
            self.assertIn("restored unchanged",
                          flat(self._redo(root, child)))
            rec = ca.Manifest.load(path).records["event-a"]
            self.assertEqual(rec.election, "taxable_deemed_dividend")


if __name__ == "__main__":
    unittest.main()
