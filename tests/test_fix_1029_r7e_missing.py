"""Missing history, readiness and opening-import fixes of 2026-10-30,
round 7 wave E (group missing). Every fixture is SYNTHETIC: made-up
tickers, ids and amounts.
"""
import json
import tempfile
import unittest

from tax_rules import rule

from _planted_rates import plant
from _qa_project import gains, project, tj, toml
from test_fix_1029_r7c_missingopen import IB_HEAD, _ca_project, ib_fill

OTHER = '[accounts.other]\ntype = "taxable"\n'


def _sales(root, sym):
    return [g for g in gains(root) if g.get("qty") and g.get("symbol") == sym]


def _gain(root, sym):
    return round(sum(float(g["gain"]) for g in _sales(root, sym)), 4)


class TestDeliveryAfterASnapshotKeepsItsPremium(unittest.TestCase):
    """TJ-1167: an option exercised on or before an OPENING snapshot of
    other shares of its underlying, its shares delivered after it: the
    snapshot does not hold that delivery, so the premium goes on it."""

    OPT = "SYNQ251231C00010000.US"
    PUT = "SYNQ251231P00010000.US"

    def _book(self, cur, snapshot, exercise="2025-12-31"):
        b = (f"BUYSELL 2024-06-03 10:00:00 {self.OPT} 1 {cur} 1 100 0\n"
             f"BUYSELL 2024-06-03 10:00:00 SYNQ.US 50 {cur} 8 400 0\n")
        if snapshot:
            b += f"OPENING 2025-12-31 SYNQ.US 50 {cur} 400 2024-06-03\n"
        return b + (
            f"ASSIGN {exercise} 16:00:00 {self.OPT} -1 {cur} 0 0 0\n"
            f"ASSIGN 2026-01-02 09:30:00 SYNQ.US 100 {cur} 10 1000 0\n"
            f"BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 {cur} 12 1200 0\n")

    def _run(self, country, book):
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {"margin/book.tt": book})
            (root / "taxjson.toml").write_text(toml(country, year=2026))
            tj(root, "run", "--no-input")
            return _sales(root, "SYNQ.US")

    @rule("CA-OPEN-03")
    def test_canada_call_premium_goes_on_the_later_delivery(self):
        for exercise in ("2025-12-31", "2025-12-30"):
            rows = self._run("canada", self._book("CAD", True, exercise))
            self.assertAlmostEqual(float(rows[0]["gain"]), 200.0, places=2)

    @rule("US-OPEN-03")
    def test_us_call_premium_goes_on_the_later_delivery(self):
        rows = self._run("usa", self._book("USD", True))
        # First lot 50 at 8; the delivery's 50 at (1000 + 100) / 2.
        self.assertAlmostEqual(sum(float(g["gain"]) for g in rows), 250.0,
                               places=2)
        self.assertAlmostEqual(float(rows[-1]["gain"]), 50.0, places=2)

    @rule("CA-OPEN-03")
    def test_canada_written_put_premium_goes_on_the_later_delivery(self):
        book = (f"BUYSELL 2024-06-03 10:00:00 {self.PUT} -1 CAD 0.5 50 0\n"
                "BUYSELL 2024-06-03 10:00:00 SYNQ.US 50 CAD 8 400 0\n"
                "OPENING 2025-12-31 SYNQ.US 50 CAD 400 2024-06-03\n"
                f"ASSIGN 2025-12-31 16:00:00 {self.PUT} 1 CAD 0 0 0\n"
                "ASSIGN 2026-01-02 09:30:00 SYNQ.US 100 CAD 10 1000 0\n"
                "BUYSELL 2026-02-10 10:00:00 SYNQ.US -150 CAD 11 1650 0\n")
        rows = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 300.0, places=2)

    @rule("CA-OPEN-03")
    def test_canada_written_call_premium_goes_in_the_later_proceeds(self):
        call = "SYNQ251231C00010000.US"
        book = (f"BUYSELL 2024-06-03 10:00:00 {call} -1 CAD 1 100 0\n"
                "BUYSELL 2024-06-03 10:00:00 SYNQ.US 150 CAD 8 1200 0\n"
                "OPENING 2025-12-31 SYNQ.US 150 CAD 1200 2024-06-03\n"
                f"ASSIGN 2025-12-31 16:00:00 {call} 1 CAD 0 0 0\n"
                "ASSIGN 2026-01-02 09:30:00 SYNQ.US -100 CAD 10 1000 0\n")
        rows = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["proceeds"]), 1100.0, places=2)
        self.assertAlmostEqual(float(rows[0]["gain"]), 300.0, places=2)

    @rule("CA-OPEN-03")
    def test_a_plain_leg_at_the_strike_is_the_delivery(self):
        book = self._book("CAD", True).replace(
            "ASSIGN 2026-01-02 09:30:00 SYNQ.US 100",
            "BUYSELL 2026-01-02 09:30:00 SYNQ.US 100")
        rows = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 200.0, places=2)

    @rule("CA-OPEN-03")
    def test_a_later_plain_purchase_still_takes_no_premium(self):
        # The delivery itself is in the snapshot (TJ-0962): a purchase
        # just after it, 100 shares above the strike, is not the delivery.
        book = (f"BUYSELL 2024-06-03 10:00:00 {self.OPT} 1 CAD 1 100 0\n"
                f"ASSIGN 2025-12-30 16:00:00 {self.OPT} -1 CAD 0 0 0\n"
                "ASSIGN 2025-12-30 16:01:00 SYNQ.US 100 CAD 10 1000 0\n"
                "OPENING 2025-12-31 SYNQ.US 100 CAD 1100 2025-12-30\n"
                "BUYSELL 2026-01-02 10:00:00 SYNQ.US 100 CAD 12 1200 0\n"
                "BUYSELL 2026-02-10 10:00:00 SYNQ.US -200 CAD 13 2600 0\n")
        rows = self._run("canada", book)
        self.assertAlmostEqual(float(rows[0]["gain"]), 300.0, places=2)

    @rule("CA-OPEN-03")
    def test_a_second_accounts_delivery_is_not_this_ones(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {
                "margin/book.tt":
                    f"BUYSELL 2024-06-03 10:00:00 {self.OPT} 1 CAD 1 100 0\n"
                    f"ASSIGN 2025-12-30 16:00:00 {self.OPT} -1 CAD 0 0 0\n"
                    "ASSIGN 2025-12-30 16:01:00 SYNQ.US 100 CAD 10 1000 0\n"
                    "OPENING 2025-12-31 SYNQ.US 100 CAD 1100 2025-12-30\n"
                    "BUYSELL 2026-02-10 10:00:00 SYNQ.US -100 CAD 13 1300 0\n",
                "other/book.tt":
                    "ASSIGN 2026-01-02 09:30:00 SYNQ.US 100 CAD 10 1000 0\n"})
            (root / "taxjson.toml").write_text(toml("canada", year=2026,
                                                    extra_accounts=OTHER))
            tj(root, "run", "--no-input")
            # One pool of 200 at 2100: the premium in the cost once.
            self.assertAlmostEqual(_gain(root, "SYNQ.US"), 250.0, places=2)


class TestOpeningImportHonoursTheMultLinesTheRunDoes(unittest.TestCase):
    """TJ-1200: `tjs opening` looked up the contract and the root before
    the first dot only; the run also reads the class share and the
    underlying as written."""

    SYM = "SYN.B250620C00010000.TO"

    def _opening(self, mult_line):
        pos = ('[meta]\nas_of = "2024-12-31"\n[[holding]]\n'
               f'symbol = "{self.SYM}"\nquantity = 1\ncurrency = "CAD"\n'
               'total_cost = 10\nmultiplier = 10\nacquired = "2024-06-03"\n')
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {
                "margin/x.tt":
                    "BUYSELL 2024-02-01 10:00:00 SYNZ.TO 1 CAD 1 1 0\n"},
                ticker_map=mult_line)
            f = root / "pos.toml"
            f.write_text(pos)
            r = tj(root, "opening", "margin", str(f), "--date", "2024-12-31",
                   "--dry-run", "--details", check=False)
            return r.returncode, r.stdout + r.stderr

    def test_every_line_the_run_reads_is_accepted(self):
        for line in (f"MULT {self.SYM} 10\n", "MULT SYN.B 10\n",
                     "MULT SYN 10\n", "MULT SYN.B.TO 10\n"):
            rc, out = self._opening(line)
            self.assertEqual(rc, 0, f"{line}: {out}")
            self.assertIn(f"{self.SYM} 1 CAD 10.00 2024-06-03 x10", out)

    def test_a_closer_line_of_another_size_wins_over_the_root(self):
        rc, out = self._opening("MULT SYN 10\nMULT SYN.B 25\n")
        self.assertNotEqual(rc, 0)
        self.assertNotIn(f"{self.SYM} 1 CAD 10.00", out)

    def test_no_line_names_the_class_share(self):
        rc, out = self._opening("")
        self.assertNotEqual(rc, 0)
        self.assertIn("add `MULT SYN.B 10` to ticker.map", out)


SNAP = ('schema_version = "1.2"\n[meta]\naccount = "margin"\n[[holding]]\n'
        'symbol = "{sym}"\naccount = "margin"\nasset_type = "equity"\n'
        'quantity = 10.0\ncurrency = "{cur}"\ntotal_cost = 100.0\n'
        'base_currency = "{base}"\nbase_total_cost = 150.0\n')


class TestABaseCostOpeningKeepsTheNativeBooks(unittest.TestCase):
    """TJ-1169: an opening in the base currency on a listing that trades
    in another currency made the account's raw merge look mixed: no
    native gains, no holdings report, for every symbol of the account."""

    def _run(self, csv, *, country="canada", sym="SYNQ.US", cur="USD",
             base="CAD", year=2026, rates=True):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = project(d.name, "p", {"margin/ib.csv": IB_HEAD + csv})
        (root / "taxjson.toml").write_text(toml(country, year=year))
        f = root / "snap.toml"
        f.write_text(SNAP.format(sym=sym, cur=cur, base=base))
        tj(root, "opening", "margin", str(f), "--date", "2025-12-31",
           "--force")
        if rates:
            (root / "work").mkdir(exist_ok=True)
            pair = "USD CAD" if base == "CAD" else "CAD USD"
            rate = "1.25" if base == "CAD" else "0.8"
            text = "".join(
                f"{y}-{m:02d}-{dd:02d} 16:00:00 {pair} {rate} BOC\n"
                for y, m in ((2025, 12), (2026, 1), (2026, 2), (2026, 3))
                for dd in range(1, 29)) + "".join(
                    f"2025-12-{dd} 16:00:00 {pair} {rate} BOC\n"
                    for dd in (29, 30, 31))
            plant(root / "work" / "to_base.csv", text, cleanup=self.addCleanup)
        return root, tj(root, "run", "--no-input")

    CSV = (ib_fill("2026-01-05", 5, 20, "O", sym="SYNR")
           + ib_fill("2026-02-02", -5, 30, "C", sym="SYNR")
           + ib_fill("2026-02-02", -4, 20, "C", sym="SYNQ"))

    def test_native_books_and_report_survive(self):
        root, r = self._run(self.CSV)
        self.assertNotIn("raw holdings skipped", r.stdout + r.stderr)
        self.assertTrue((root / "work" / "margin_raw_gains.json").exists())
        self.assertTrue((root / "reports" / "margin_holdings.toml").exists())
        # Native view: both sales, in the listing's currency.
        doc = json.loads((root / "work" / "margin_raw_gains.json").read_text())
        syms = {t["symbol"] for t in doc["transactions"] if t.get("qty")}
        self.assertEqual(syms, {"SYNQ.US", "SYNR.US"})
        self.assertIn("SYNQ.US", tj(root, "gains").stdout)
        # Filing books: the Canadian cost as stated.
        self.assertAlmostEqual(_gain(root, "SYNQ.US"), 100.0 - 60.0, places=2)

    def test_the_report_keeps_the_base_cost(self):
        from taxjson.lib.tomlcompat import tomllib
        root, _r = self._run(self.CSV)
        doc = tomllib.loads((root / "reports" / "margin_holdings.toml")
                            .read_text())
        h = next(x for x in doc["holding"] if x["symbol"] == "SYNQ.US")
        self.assertEqual(h["currency"], "USD")
        self.assertAlmostEqual(h["quantity"], 6.0)
        self.assertAlmostEqual(h["base_total_cost"], 90.0, places=1)

    def test_a_holding_with_no_trade_is_in_its_listing_currency(self):
        from taxjson.lib.tomlcompat import tomllib
        csv = (ib_fill("2026-01-05", 5, 20, "O", sym="SYNR")
               + ib_fill("2026-02-02", -5, 30, "C", sym="SYNR"))
        root, r = self._run(csv)
        self.assertNotIn("raw holdings skipped", r.stdout + r.stderr)
        doc = tomllib.loads((root / "reports" / "margin_holdings.toml")
                            .read_text())
        h = next(x for x in doc["holding"] if x["symbol"] == "SYNQ.US")
        self.assertEqual(h["currency"], "USD")
        self.assertAlmostEqual(h["total_cost"], 120.0, places=2)
        self.assertAlmostEqual(h["base_total_cost"], 150.0, places=2)

    def test_us_project_on_a_cad_listing(self):
        csv = (ib_fill("2026-01-05", 5, 20, "O", sym="SYNR", cur="CAD")
               + ib_fill("2026-02-02", -5, 30, "C", sym="SYNR", cur="CAD")
               + ib_fill("2026-02-02", -4, 20, "C", sym="SYNQ", cur="CAD"))
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = project(d.name, "p", {
            "margin/ib.csv": IB_HEAD + csv,
            "margin/book.tt":
                "OPENING 2025-12-31 SYNQ.TO 10 USD 150 2024-06-03\n"})
        (root / "taxjson.toml").write_text(toml("usa", year=2026))
        (root / "work").mkdir(exist_ok=True)
        text = "".join(f"{y}-{m:02d}-{dd:02d} 16:00:00 CAD USD 0.8 YAHOO\n"
                       for y, m in ((2025, 12), (2026, 1), (2026, 2),
                                    (2026, 3))
                       for dd in range(1, 29)) + "".join(
            f"2025-12-{dd} 16:00:00 CAD USD 0.8 YAHOO\n" for dd in (29, 30, 31))
        plant(root / "work" / "to_base.csv", text, cleanup=self.addCleanup)
        r = tj(root, "run", "--no-input")
        self.assertNotIn("raw holdings skipped", r.stdout + r.stderr)
        self.assertTrue((root / "reports" / "margin_holdings.toml").exists())

    def test_trades_in_two_currencies_still_skip_the_native_books(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = project(d.name, "p", {
            "margin/ib.csv": IB_HEAD + ib_fill("2026-01-05", 5, 20, "O",
                                               sym="SYNR"),
            "margin/book.tt":
                "OPENING 2025-12-31 SYNQ.US 10 CAD 150\n"
                "BUYSELL 2026-01-05 10:00:00 SYNQ.US 5 CAD 20 100 0\n"
                "BUYSELL 2026-02-02 10:00:00 SYNQ.US -4 USD 20 80 0\n"})
        (root / "taxjson.toml").write_text(toml("canada", year=2026))
        r = tj(root, "run", "--no-input")
        self.assertIn("raw holdings skipped", r.stdout + r.stderr)


class TestARealShortDrawsNoRaiseTheLineWarning(unittest.TestCase):
    """TJ-1171: the opening written for TJ-0960 (5 units) drew a warning
    to raise it, counting the broker-marked short as units with no
    purchase; raising it restores TJ-0960's bug."""

    CSV = (ib_fill("2025-01-06", -5, 10, "C", cur="CAD")
           + ib_fill("2025-02-03", 5, 20, "O", cur="CAD")
           + ib_fill("2025-05-05", -5, 30, "C", cur="CAD")
           + ib_fill("2025-06-02", -10, 40, "O", cur="CAD")
           + ib_fill("2025-07-02", 10, 30, "C", cur="CAD"))

    def test_no_warning_on_the_written_line(self):
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, self.CSV)
            tj(root, "run", "--no-input")
            tj(root, "find-missing-history", "--write-missing-history")
            r = tj(root, "run", "--no-input", "--details")
            out = r.stdout + r.stderr
            self.assertNotIn("goes short again", out)
            self.assertNotIn("Raise the quantity", out)
            fmh = tj(root, "find-missing-history", "--details")
            self.assertNotIn("goes short again", fmh.stdout + fmh.stderr)

    def test_a_marked_short_beyond_a_too_small_line_still_warns(self):
        # Two closing sales of 5, the line opens 3: 2 units of the
        # closing sales have no purchase, whatever the real short does.
        csv = (ib_fill("2025-01-06", -5, 10, "C", cur="CAD")
               + ib_fill("2025-06-02", -10, 40, "O", cur="CAD")
               + ib_fill("2025-07-02", 10, 30, "C", cur="CAD"))
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, csv,
                               "OPENING 2025-01-05 SYNQ.TO 3 cost=unknown\n")
            r = tj(root, "run", "--no-input", "--details")
            self.assertIn("goes short again on 2025-01-06",
                          r.stdout + r.stderr)

    def test_a_closing_sale_beyond_the_line_still_warns(self):
        # Used up by a code-C sale: not a real short (TJ-0958's shape).
        csv = (ib_fill("2024-06-03", -5, 10, "C", cur="CAD")
               + ib_fill("2025-06-02", -10, 15, "C", cur="CAD"))
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, csv,
                               "OPENING 2024-01-01 SYNQ.TO 5 cost=unknown\n")
            r = tj(root, "run", "--no-input", "--details")
            self.assertIn("goes short again on 2025-06-02",
                          r.stdout + r.stderr)

    def test_a_marked_sale_the_line_holds_units_for_stays_long(self):
        # The O sale is backed by the line's units: the later closing
        # sale past them still has no purchase.
        csv = (ib_fill("2025-06-02", -10, 40, "O", cur="CAD")
               + ib_fill("2025-07-02", -15, 30, "C", cur="CAD"))
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, csv,
                               "OPENING 2025-01-05 SYNQ.TO 20 cost=unknown\n")
            r = tj(root, "run", "--no-input", "--details")
            self.assertIn("goes short again on 2025-07-02",
                          r.stdout + r.stderr)


class TestViewsAgreeWithTheWalkOfTheOpenings(unittest.TestCase):
    """TJ-1170, TJ-1199: find-missing-history and checklist step 10 read
    a declared pair by its name alone: a used-up opening read as covered
    (the run says NOT in the totals), and the old holdings' lines the
    converging-rename advice asks for read as not covering the new
    symbol (the run says covered)."""

    def _ib(self, tt):
        csv = (ib_fill("2024-06-03", -5, 10, "C", cur="CAD")
               + ib_fill("2025-06-02", -10, 15, "C", cur="CAD"))
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = _ca_project(d.name, csv, tt)
        tj(root, "run", "--no-input")
        return root

    def _affects(self, root):
        out = tj(root, "find-missing-history").stdout
        return out, "AFFECTS 2025" in out, "COVERED by" in out

    def _step10(self, root):
        out = tj(root, "checklist", "--run", check=False).stdout
        return next(ln for ln in out.splitlines() if "missing-history" in ln)

    def test_used_up_opening_is_unresolved_in_every_view(self):
        root = self._ib("OPENING 2024-01-01 SYNQ.TO 5 cost=unknown\n")
        out, affects, covered = self._affects(root)
        self.assertTrue(affects, out)
        self.assertFalse(covered, out)
        self.assertIn("[!]", self._step10(root))
        self.assertIn("affect 2025: SYNQ.TO (margin)",
                      tj(root, "checklist", "--run", check=False).stdout)

    def test_used_up_opening_in_a_us_project(self):
        csv = (ib_fill("2024-06-03", -5, 10, "C")
               + ib_fill("2025-06-02", -10, 15, "C"))
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {
                "margin/ib.csv": IB_HEAD + csv,
                "margin/book.tt": "OPENING 2024-01-01 SYNQ.US 5 cost=unknown\n"})
            (root / "taxjson.toml").write_text(toml("usa", year=2025))
            tj(root, "run", "--no-input")
            out = tj(root, "find-missing-history").stdout
            self.assertIn("AFFECTS 2025", out)
            self.assertNotIn("COVERED by", out)

    def test_a_declared_pair_with_no_booked_opening_beside_a_used_up_one(self):
        # A line on a symbol with no sale reads as before; another pair's
        # used-up line does not uncover a pair the run opens as a whole.
        csv = (ib_fill("2024-06-03", -5, 10, "C", cur="CAD")
               + ib_fill("2025-06-02", -10, 15, "C", cur="CAD")
               + ib_fill("2025-06-02", -3, 15, "C", sym="SYNR", cur="CAD"))
        with tempfile.TemporaryDirectory() as d:
            root = _ca_project(d, csv,
                               "OPENING 2024-01-01 SYNQ.TO 5 cost=unknown\n"
                               "OPENING 2024-01-01 SYNR.TO 3 cost=unknown\n")
            tj(root, "run", "--no-input")
            out = tj(root, "find-missing-history").stdout
            aff = out.split("AFFECTS 2025")[1].split("COVERED by")[0]
            self.assertIn("SYNQ.TO", aff)
            self.assertNotIn("SYNR.TO", aff)
            self.assertIn("SYNR.TO", out.split("COVERED by")[1])

    def test_used_up_opening_says_how_far_it_reaches(self):
        root = self._ib("OPENING 2024-01-01 SYNQ.TO 5 cost=unknown\n")
        r = tj(root, "find-missing-history", "--details")
        self.assertIn("goes short again on 2025-06-02", r.stdout)

    def test_used_up_opening_write_does_not_deny_the_sale(self):
        root = self._ib("OPENING 2024-01-01 SYNQ.TO 5 cost=unknown\n")
        r = tj(root, "find-missing-history", "--write-missing-history")
        out = r.stdout + r.stderr
        self.assertIn("opens 5, the books size 15", out)
        self.assertNotIn("no sale with no purchase in your files", out)

    def test_an_opening_that_reaches_every_sale_stays_covered(self):
        root = self._ib("OPENING 2024-01-01 SYNQ.TO 15 cost=unknown\n")
        out, affects, covered = self._affects(root)
        self.assertFalse(affects, out)
        self.assertTrue(covered, out)
        self.assertIn("[x]", self._step10(root))

    def test_no_opening_is_unchanged(self):
        root = self._ib(None)
        out, affects, covered = self._affects(root)
        self.assertTrue(affects, out)
        self.assertFalse(covered, out)

    SPLITS = ("SPLIT 2025-06-02 10:00:00 SYNA.TO SYNN.TO 2\n"
              "SPLIT 2025-06-03 10:00:00 SYNB.TO SYNN.TO 3\n"
              "BUYSELL 2025-06-04 10:00:00 SYNN.TO -60 CAD 10 600 0\n")

    def _renames(self, lines):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = _ca_project(d.name, "", year=2025)
        (root / "inputs" / "margin" / "book.tt").write_text(
            self.SPLITS + lines)
        tj(root, "run", "--no-input")
        return root

    def test_the_advised_old_symbol_lines_cover_the_new_symbol(self):
        root = self._renames("OPENING 2025-06-01 SYNA.TO 15 cost=unknown\n"
                             "OPENING 2025-06-01 SYNB.TO 10 cost=unknown\n")
        out, affects, covered = self._affects(root)
        self.assertFalse(affects, out)
        self.assertTrue(covered, out)
        self.assertIn("[x]", self._step10(root))

    def test_too_small_old_symbol_line_stays_unresolved(self):
        root = self._renames("OPENING 2025-06-01 SYNA.TO 5 cost=unknown\n")
        out, affects, covered = self._affects(root)
        self.assertTrue(affects, out)
        self.assertFalse(covered, out)
        self.assertIn("[!]", self._step10(root))

    def test_a_line_on_the_new_symbol_is_still_covered(self):
        root = self._renames("OPENING 2025-06-01 SYNN.TO 30 cost=unknown\n")
        out, affects, covered = self._affects(root)
        self.assertFalse(affects, out)
        self.assertTrue(covered, out)

    def test_another_accounts_line_does_not_cover_this_one(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "p", {
                "margin/book.tt":
                    "BUYSELL 2025-06-04 10:00:00 SYNN.TO -5 CAD 10 50 0\n",
                "other/book.tt":
                    "BUYSELL 2025-06-04 10:00:00 SYNN.TO -5 CAD 10 50 0\n"
                    "OPENING 2025-06-01 SYNN.TO 5 cost=unknown\n"})
            (root / "taxjson.toml").write_text(toml("canada", year=2025,
                                                    extra_accounts=OTHER))
            tj(root, "run", "--no-input")
            out = tj(root, "find-missing-history").stdout
            rows = [ln.split()[:2] for ln in out.split("AFFECTS 2025")[1]
                    .split("COVERED")[0].splitlines()
                    if ln.startswith("SYNN.TO")]
            self.assertEqual(rows, [["SYNN.TO", "margin"]], out)
            self.assertIn("COVERED by", out)


class TestCutAcrossAccountsReachesTheAccountsHoldings(unittest.TestCase):
    """TJ-1168: the second cut-off reached only the merged books: the
    account's holdings still walked its replaced OLD purchase, through
    the other account's rename."""

    def _run(self, split_in_margin, sold=4):
        split = "SPLIT 2025-03-02 10:00:00 SYNO.TO SYNN.TO 1\n"
        margin = ("BUYSELL 2024-06-03 10:00:00 SYNO.TO 10 CAD 10 100 0\n"
                  + (split if split_in_margin else "")
                  + "BUYSELL 2025-04-10 10:00:00 SYNN.TO 4 CAD 30 120 0\n"
                  "OPENING 2025-06-30 SYNN.TO 24 CAD 220\n"
                  f"BUYSELL 2025-07-02 10:00:00 SYNN.TO -{sold} CAD 20 "
                  f"{20 * sold} 0\n")
        other = ("BUYSELL 2024-06-03 10:00:00 SYNO.TO 1 CAD 10 10 0\n"
                 + split
                 + "BUYSELL 2025-04-03 10:00:00 SYNN.TO -1 CAD 10 10 0\n")
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = project(d.name, "p", {"margin/book.tt": margin,
                                     "other/book.tt": other})
        (root / "taxjson.toml").write_text(toml("canada", year=2025,
                                                extra_accounts=OTHER))
        return root, tj(root, "run", "--no-input")

    def _inv(self, root):
        doc = json.loads((root / "work" / "margin_gains_wash.json")
                         .read_text())
        return [(i["symbol"], round(float(i["qty"]), 4),
                 round(float(i["total_cost"]), 2),
                 i.get("position_start_date"))
                for i in doc["inventory"]]

    @rule("CA-OPEN-03")
    def test_matches_the_rename_in_both_accounts(self):
        root_a, ra = self._run(False)
        root_b, rb = self._run(True)
        self.assertEqual(self._inv(root_a), self._inv(root_b))
        self.assertEqual([x[:3] for x in self._inv(root_a)],
                         [("SYNN.TO", 20.0, 183.33)])
        self.assertNotIn("per-account split accounts for", ra.stdout + ra.stderr)

    @rule("CA-OPEN-03")
    def test_list_and_sanity_agree(self):
        root, _r = self._run(False)
        for view in (("list",), ("list", "--date", "2025-12-31")):
            self.assertRegex(tj(root, *view).stdout,
                             r"margin\s+SYNN\.TO\s+20\s+183\.33")
        hold = root / "holdings"
        hold.mkdir(exist_ok=True)
        (hold / "margin_holdings.toml").write_text(
            '[[holding]]\nsymbol = "SYNN.TO"\nquantity = 20\n'
            'currency = "CAD"\ntotal_cost = 183.33\n')
        r = tj(root, "sanity", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_cut_row_is_out_of_the_fee_map_too(self):
        from taxjson.bin.taxjson_split_gains import split_for_account
        base = [{"id": "a", "account": "margin", "action": "BUYSELL",
                 "symbol": "SYNO.TO", "date": "2025-02-01", "currency": "CAD",
                 "commission": 5.0, "quantity": 1.0},
                {"id": "b", "account": "margin", "action": "BUYSELL",
                 "symbol": "SYNN.TO", "date": "2025-07-01", "currency": "CAD",
                 "commission": 7.0, "quantity": 1.0}]
        combined = {"summary": {"year": 2025}, "transactions": [],
                    "inventory": []}
        pool = [dict(base[1])]
        out = split_for_account(combined, "margin", base, pool)
        self.assertEqual(
            out["summary"]["total_fees_by_currency"]["CAD"]["total"], 7.0)
        # No pool rows of the account: nothing is taken out.
        out = split_for_account(combined, "margin", base, [])
        self.assertEqual(
            out["summary"]["total_fees_by_currency"]["CAD"]["total"], 12.0)


class TestCutAcrossAccountsWalksTheRowsAlreadyCut(unittest.TestCase):
    """TJ-1201: the second cut-off walked back from the snapshot through
    the rows it drops itself: what the account's own merge had cut was
    missing, and an OLD purchase read as a short cover."""

    SPLIT = "SPLIT 2025-03-02 10:00:00 SYNO.TO SYNN.TO 1\n"
    OTHER_BOOK = ("BUYSELL 2024-06-03 10:00:00 SYNO.TO 1 CAD 10 10 0\n"
                  + SPLIT
                  + "BUYSELL 2025-04-03 10:00:00 SYNN.TO -1 CAD 10 10 0\n")

    def _run(self, margin, year=2025):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = project(d.name, "p", {"margin/book.tt": margin,
                                     "other/book.tt": self.OTHER_BOOK})
        (root / "taxjson.toml").write_text(toml("canada", year=year,
                                                extra_accounts=OTHER))
        return root, tj(root, "run", "--no-input", check=False)

    def _margin(self, middle, buy="BUYSELL 2025-01-10 10:00:00 SYNO.TO 20 "
                                  "CAD 10 200 0\n"):
        return (buy + middle + "OPENING 2025-06-30 SYNN.TO 5 CAD 50\n"
                "BUYSELL 2025-07-02 10:00:00 SYNN.TO -5 CAD 20 100 0\n")

    @rule("CA-OPEN-03")
    def test_a_consolidation_the_merge_cut_is_walked(self):
        root, r = self._run(self._margin(
            "SPLIT 2025-04-10 10:00:00 SYNN.TO SYNN.TO 0.25\n"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("short cover", r.stdout + r.stderr)

    @rule("CA-OPEN-03")
    def test_a_transfer_the_merge_cut_is_walked(self):
        root, r = self._run(self._margin(
            "TRANSFER 2025-04-10 10:00:00 SYNN.TO -15 CAD 0 0 0\n"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("short cover", r.stdout + r.stderr)

    @rule("CA-OPEN-03")
    def test_a_real_cover_is_still_refused(self):
        # Short 15 SYNO in 2024; the 2025 buy of 20 covers it, then 5
        # are held; the rename is only in `other`.
        root, r = self._run(self._margin(
            "",
            buy="BUYSELL 2024-02-01 10:00:00 SYNO.TO -15 CAD 10 150 0\n"
                "BUYSELL 2025-01-10 10:00:00 SYNO.TO 20 CAD 10 200 0\n"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("short cover", r.stdout + r.stderr)

    @rule("CA-OPEN-03")
    def test_a_sale_the_second_cut_drops_is_still_refused(self):
        root, r = self._run(self._margin(
            "SPLIT 2025-04-10 10:00:00 SYNN.TO SYNN.TO 0.25\n",
            buy="BUYSELL 2024-02-01 10:00:00 SYNO.TO 25 CAD 10 250 0\n"
                "BUYSELL 2025-01-10 10:00:00 SYNO.TO -5 CAD 10 50 0\n"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("1 sale(s) of the 2025", r.stdout + r.stderr)

    def test_a_row_the_merge_judged_is_not_judged_again(self):
        # The first pass let this prior-year sale's row through its own
        # year test; the walk only reads it.
        from taxjson.lib.opening import apply_opening_cutoff
        import io
        rows = [
            {"action": "BUYSELL", "date": "2025-01-10", "symbol": "SYNO.TO",
             "quantity": 20.0, "net_amount": 200.0, "currency": "CAD",
             "account": "margin"},
            {"action": "SPLIT", "date": "2025-03-02", "symbol": "SYNO.TO",
             "symbol_new": "SYNN.TO", "quantity": 1.0, "account": "other"},
            {"action": "BUYSELL", "type": "OPENING", "date": "2025-06-30",
             "symbol": "SYNN.TO", "quantity": 5.0, "net_amount": 50.0,
             "currency": "CAD", "account": "margin"}]
        from taxjson.lib.core import OPENING_TYPE
        rows[2]["type"] = OPENING_TYPE
        earlier = [{"action": "BUYSELL", "date": "2025-04-10",
                    "symbol": "SYNN.TO", "quantity": -15.0,
                    "net_amount": 300.0, "currency": "CAD",
                    "account": "margin"}]
        out = apply_opening_cutoff(rows, year=2025, country="canada",
                                   earlier_cut=earlier, report=io.StringIO())
        self.assertEqual(len(out), 2)

    def test_a_row_in_another_currency_leaves_the_cost_walk_alone(self):
        # The account's merge cut this row before its conversion: USD
        # beside the converted books. A return of capital the snapshot's
        # cost shows was within it stays history the snapshot replaces.
        from taxjson.lib.core import OPENING_TYPE
        from taxjson.lib.opening import apply_opening_cutoff
        import io
        rows = [
            {"action": "BUYSELL", "date": "2025-01-10", "symbol": "SYNO.TO",
             "quantity": 20.0, "net_amount": 200.0, "currency": "CAD",
             "account": "margin"},
            {"action": "ADJUST", "date": "2025-02-10", "symbol": "SYNO.TO",
             "quantity": 0.0, "net_amount": -10.0, "currency": "CAD",
             "account": "margin"},
            {"action": "SPLIT", "date": "2025-03-02", "symbol": "SYNO.TO",
             "symbol_new": "SYNN.TO", "quantity": 1.0, "account": "other"},
            {"action": "BUYSELL", "type": OPENING_TYPE, "date": "2025-06-30",
             "symbol": "SYNN.TO", "quantity": 25.0, "net_amount": 250.0,
             "currency": "CAD", "account": "margin"}]
        earlier = [{"action": "BUYSELL", "date": "2025-05-01",
                    "symbol": "SYNN.TO", "quantity": 5.0, "net_amount": 40.0,
                    "currency": "USD", "account": "margin"}]
        out = apply_opening_cutoff(rows, year=2025, country="canada",
                                   earlier_cut=earlier, report=io.StringIO())
        self.assertEqual([t["action"] for t in out], ["SPLIT", "BUYSELL"])


if __name__ == "__main__":
    unittest.main()
