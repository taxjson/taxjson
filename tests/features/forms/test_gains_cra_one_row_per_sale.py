"""`tjs gains-cra`: the tax year's dispositions of the taxable accounts,
one row per sale in the CRA's columns, each money cell Schedule 3's for
that one disposition (lib/tax_forms `schedule3_disposition`, footed
alone), the FX of the trade that brought the proceeds in, and the line
and grand totals `tjs form-export --form schedule3` and `tjs sum`'s FOR
THE RETURN rows print (src/taxjson/lib/gains_cra.py, src/taxjson/cli/
gains_filing.py `cmd_gains_cra`).

A project with USD and CAD sales in a HOME whose Bank of Canada rate
differs every day: a long sale, a superficial loss, a call written under
grant timing and bought back, a short sale opened in two parts, a
future; the same project with more calls and puts written and bought
back (the buy-backs' one shared note); the same project with more
superficial losses, deferred and for good (one mark per kind of denial,
the table's count and total in it, each row's own in its DENIED (INFO)
cell); the same project with its native
gains file and then its rates gone; and a recorded golden book's views
(a share's Description is its ticker, never the broker's name for it).
The rate dates and the ACCOUNT view:
test_gains_cra_rate_dates_and_account_scope.
"""
import re
import csv
import datetime
import json
import unittest
from decimal import Decimal

import _golden
from _support.filing_mixed_currency_project import MARGIN_TT, TFSA_TT, Project, cents, usd_cad_rate

from taxjson.lib import gains_cra as GC
from taxjson.lib import json_contract as J
from taxjson.model import money as M

CELLS = ("proceeds", "acb", "outlays", "gain")


def _by_symbol(doc, symbol):
    return [r for r in doc["rows"] if r["symbol"] == symbol]


def _denied_cells(lines, i):
    """For the currency table headed at `lines[i]`: the slice of a row
    that holds its DENIED (INFO) cell (after GAIN, to the end of the
    heading's DENIED / (INFO) / (CAD), a heading on three lines)."""
    end = lines[i + 1].index("DENIED") + len("DENIED")
    start = lines[i + 2].index("GAIN") + len("GAIN")
    return slice(start, end)


class TestTheMixedProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project()
        cls.p.run()
        cls.doc = cls.p.json("gains-cra")
        cls.fe = cls.p.json("form-export", "--form", "schedule3")
        cls.sum = cls.p.json("sum")

    def test_the_document_s_number(self):
        self.assertEqual(self.doc["schema_version"], J.SCHEMAS["gains-cra"])
        self.assertEqual(self.doc["currency"], "CAD")
        self.assertEqual(self.doc["year"], 2025)

    def test_every_row_foots(self):
        self.assertEqual(len(self.doc["rows"]), 7)
        for r in self.doc["rows"]:
            with self.subTest(row=r["description"], date=r["date"]):
                self.assertEqual(cents(r["proceeds"]) - cents(r["acb"]) - cents(r["outlays"]),
                                 cents(r["gain"]))

    def test_only_the_taxable_accounts_sorted_by_date(self):
        self.assertEqual({r["account"] for r in self.doc["rows"]}, {"margin"})
        keys = [(r["date"], r["account"], r["symbol"]) for r in self.doc["rows"]]
        self.assertEqual(keys, sorted(keys))

    def test_the_line_totals_are_form_export_s_and_sum_s(self):
        fe = {(ln["key"], ln["period"]): ln for ln in self.fe["lines"]}
        sm = {(ln["key"], ln["period"]): ln for ln in self.sum["filing"]["lines"]}
        self.assertEqual(len(self.doc["lines"]), 2)
        self.assertEqual(set(fe), {(ln["key"], ln["period"]) for ln in self.doc["lines"]})
        self.assertEqual(set(sm), set(fe))
        for ln in self.doc["lines"]:
            k = (ln["key"], ln["period"])
            for cell in CELLS:
                with self.subTest(line=ln["title"], cell=cell):
                    self.assertEqual(cents(ln[cell]), cents(fe[k][cell]))
                    self.assertEqual(cents(ln[cell]), cents(sm[k][cell]))

    def test_the_grand_total_is_sum_s_return_row(self):
        tot = self.sum["filing"]["totals"]
        self.assertIsNone(self.doc["scope"])
        self.assertNotIn("return_totals", self.doc)
        for cell in CELLS + ("denied", "denied_all"):
            with self.subTest(cell=cell):
                self.assertEqual(cents(self.doc["totals"][cell]), cents(tot[cell]))
        self.assertEqual(cents(self.doc["totals"]["gain"]), cents(self.fe["totals"]["gain_all"]))

    def test_the_rows_add_to_the_lines_within_a_cent_a_security(self):
        for ln in self.doc["lines"]:
            rows = [r for r in self.doc["rows"] if r["line_key"] == ln["key"]]
            for cell in CELLS:
                added = sum((cents(r[cell]) for r in rows), Decimal(0))
                self.assertEqual(added, cents(ln["rows_sum"][cell]))
                self.assertLessEqual(abs(added - cents(ln[cell])),
                                     Decimal("0.01") * len({r["symbol"] for r in rows}))

    def test_the_units_are_form_export_s_less_the_ones_noted(self):
        for ln in self.doc["lines"]:
            rows = [r for r in self.doc["rows"] if r["line_key"] == ln["key"]]
            want = sum(Decimal(str(r["units"])) for r in self.fe["rows"]
                       if r["line_key"] == ln["key"])
            got = sum(Decimal(str(r["qty"])) - Decimal(str(r["units_counted_elsewhere"]))
                      for r in rows)
            self.assertEqual(got, want, ln["title"])
        back = [r for r in _by_symbol(self.doc, "QZRX251219C00030000.US") if not r["grant"]]
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0]["units_counted_elsewhere"], 2.0)
        self.assertTrue(any("counted once, at the write" in n for n in back[0]["notes"]))

    def test_the_fx_of_a_sale_is_the_rate_table_s_on_its_day(self):
        for sym in ("QZRX.US", "QZRX251219C00030000.US"):
            r = [x for x in _by_symbol(self.doc, sym) if x["proceeds"]][0]
            with self.subTest(symbol=sym):
                self.assertEqual(r["currency"], "USD")
                self.assertEqual(r["fx_source"], "rate_table")
                self.assertEqual((r["fx_date"], r["fx_rate_date"]),
                                 (r["date_settle"], r["date_settle"]))
                day = M.of(r["fx_rate"])
                self.assertEqual(day, usd_cad_rate(_date(r["date"])))
                self.assertEqual(M.present(M.of(r["native_proceeds"]) * day, 2),
                                 cents(r["proceeds"]))
                self.assertFalse([n for n in r["notes"] if "×" in n], r["notes"])

    def test_a_short_cover_s_fx_is_weighted_and_noted(self):
        r = _by_symbol(self.doc, "QZSH.US")[0]
        self.assertEqual(r["fx_source"], "weighted")
        self.assertEqual(r["native_proceeds"], 608.0)
        self.assertEqual(M.of(r["fx_rate"]),
                         M.present(M.of(r["proceeds"]) / M.of(r["native_proceeds"]), 4))
        lo, hi = usd_cad_rate(_date("2025-07-01")), usd_cad_rate(_date("2025-07-08"))
        self.assertTrue(lo <= M.of(r["fx_rate"]) <= hi, r["fx_rate"])
        self.assertNotEqual(M.of(r["fx_rate"]), usd_cad_rate(_date(r["date"])))
        self.assertTrue(any("proceeds-weighted" in n for n in r["notes"]), r["notes"])
        self.assertTrue(any("a short sale" in n for n in r["notes"]), r["notes"])
        # Four places of a weighted rate allow native × 0.00005 + a cent: no
        # mismatch note on a correct row.
        self.assertFalse([n for n in r["notes"] if "not the Proceeds" in n], r["notes"])

    def test_a_buy_back_that_received_nothing_has_no_fx_and_says_so(self):
        r = [x for x in _by_symbol(self.doc, "QZRX251219C00030000.US") if not x["grant"]][0]
        self.assertEqual((cents(r["proceeds"]), r["fx_rate"], r["fx_source"]),
                         (Decimal("0.00"), None, None))
        # The JSON keeps the note and its kind; the text's FX cell says it ("—").
        self.assertTrue(any(n.startswith("no FX: nothing was received") for n in r["notes"]))
        self.assertEqual(r["note_kinds"], ["nothing_received", "counted_at_write"])
        self.assertEqual(len(r["note_kinds"]), len(r["notes"]))
        for args in ((), ("--details",)):
            out = self.p.tjs("gains-cra", *args, TAXJSON_WIDTH="120").stdout
            with self.subTest(args=args):
                self.assertNotIn("nothing was received", out)
                row = [ln for ln in out.splitlines() if ln.startswith(r["date"])]
                self.assertEqual(len(row), 1, out)
                self.assertIn(" — ", row[0])

    def test_a_future_shows_its_settled_p_l(self):
        r = _by_symbol(self.doc, "F:QZM26.US")[0]
        self.assertEqual(r["description"], "QZM26 future")
        self.assertEqual(r["native_proceeds"], 1000.0)
        self.assertEqual(r["fx_source"], "weighted")
        self.assertTrue(any("the P/L's rate" in n for n in r["notes"]), r["notes"])
        self.assertTrue(any("not the notional" in n for n in r["notes"]), r["notes"])
        self.assertFalse([n for n in r["notes"] if "not the Proceeds" in n], r["notes"])

    def test_a_superficial_loss_is_noted(self):
        r = _by_symbol(self.doc, "QZLS.TO")[0]
        self.assertEqual((r["currency"], r["native_proceeds"], r["fx_rate"]), ("CAD", None, None))
        self.assertEqual((r["gain"], r["denied"]), (0.0, 100.0))
        self.assertTrue(any(n.startswith("superficial loss 100.00 CAD denied") for n in r["notes"]))

    def test_the_descriptions(self):
        self.assertEqual({r["symbol"]: r["description"] for r in self.doc["rows"]
                          if r["symbol"] != "QZRX251219C00030000.US"},
                         {"QZRX.US": "QZRX", "QZNW.TO": "QZNW", "QZLS.TO": "QZLS",
                          "QZSH.US": "QZSH", "F:QZM26.US": "QZM26 future"})
        self.assertEqual({r["description"] for r in _by_symbol(self.doc, "QZRX251219C00030000.US")},
                         {"QZRX $30 call exp 2025-12-19 (written)"})

    def test_the_tables_by_currency(self):
        self.assertEqual([t["currency"] for t in self.doc["tables"]], ["CAD", "USD"])
        usd = self.doc["tables"][1]
        rows = [r for r in self.doc["rows"] if r["currency"] == "USD"]
        self.assertEqual(cents(usd["native_proceeds"]),
                         sum((cents(r["native_proceeds"]) for r in rows), Decimal(0)))
        self.assertNotIn("native_proceeds", self.doc["tables"][0])

    def test_the_readiness_record(self):
        self.assertEqual(set(self.doc["readiness"]), set(self.sum["readiness"]))

    def test_the_text_view(self):
        r = self.p.tjs("gains-cra", TAXJSON_WIDTH="120")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith("CAPITAL GAINS, ONE ROW PER SALE — CAD, tax year 2025"))
        self.assertLess(r.stdout.index("SALES IN CAD"), r.stdout.index("SALES IN USD"))
        self.assertIn("FOR THE RETURN", r.stdout)
        # Always the whole text (TJ-5337, TJ-5342): every word of the long
        # Description, wrapped within its column, nothing cut; the table
        # within the width, its headings on three lines.
        i = lines.index("SALES IN USD")
        rule = next(n for n in range(i, len(lines)) if set(lines[n]) == {"-"})
        desc = lines[i + 3].index("DESCRIPTION")
        rows = lines[rule + 1:next(n for n in range(rule + 1, len(lines))
                                   if set(lines[n]) == {"-"})]
        call = next(n for n, ln in enumerate(rows) if "QZRX $30 call" in ln)
        words = [rows[call][desc:].split("  ")[0]]
        for ln in rows[call + 1:]:
            if ln[:desc].strip():
                break                       # the next sale
            words.append(ln.strip())
        self.assertEqual(" ".join(words), "QZRX $30 call exp 2025-12-19 (written)")
        self.assertNotIn("…", r.stdout)
        self.assertEqual([ln.split() for ln in lines[i + 1:rule]],
                         [["DENIED"],
                          ["SALE", "SETTLE", "PROCEEDS", "PROCEEDS", "COST(ACB)", "OUTLAYS", "GAIN",
                           "(INFO)"],
                          ["DATE", "DATE", "DESCRIPTION", "QTY", "(USD)", "FX"] + ["(CAD)"] * 5])
        self.assertEqual([ln for ln in lines[i:rule + len(rows) + 3] if len(ln) > 120], [])
        # The prose and the summary tables stay within the width.
        summary = r.stdout[r.stdout.index("ALL SALES"):].splitlines()
        self.assertEqual([ln for ln in summary if len(ln) > 120], [])

    def test_the_csv(self):
        out = self.p.root / "cra.csv"
        r = self.p.tjs("gains-cra", "--csv", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out, newline="", encoding="utf-8") as f:
            got = list(csv.reader(f))
        self.assertEqual(got[0], list(GC.CSV_COLUMNS))
        self.assertEqual(len(got) - 1, len(self.doc["rows"]))
        col = {h: i for i, h in enumerate(got[0])}
        for line, row in zip(got[1:], self.doc["rows"]):
            self.assertEqual(line[col["Currency"]], row["currency"])
            self.assertEqual(cents(line[col["Gain/Loss (CAD)"]]), cents(row["gain"]))
            self.assertEqual(line[col["Description"]], row["description"])
            self.assertEqual(line[col["Symbol"]], row["symbol"])
        self.assertEqual(got[0].index("Symbol"), got[0].index("Description") + 1)

    def test_a_csv_to_a_folder_is_refused_as_form_export_refuses_it(self):
        folder = self.p.root / "work"
        fe = self.p.tjs("form-export", "--form", "schedule3", "--csv", str(folder))
        r = self.p.tjs("gains-cra", "--csv", str(folder))
        self.assertEqual(r.returncode, fe.returncode)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn(f"tjs gains-cra: error: cannot write --csv {folder}: it is a folder",
                      r.stderr)
        self.assertNotIn(".part", r.stderr)
        missing = self.p.root / "nodir" / "x.csv"
        r = self.p.tjs("gains-cra", "--csv", str(missing))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"cannot write --csv {missing}: No such file or directory", r.stderr)
        self.assertFalse(missing.parent.exists())


class TestTheBooksAndTheCountry(unittest.TestCase):
    def test_books_older_than_the_inputs_are_refused(self):
        p = Project(files={"margin": "BUYSELL 2025-01-06 10:00:00 QZNW.TO 10 CAD 10 100 0\n"
                                     "BUYSELL 2025-03-06 10:00:00 QZNW.TO -10 CAD 12 120 0\n"},
                    planted=False)
        p.run()
        self.assertEqual(p.tjs("gains-cra").returncode, 0)
        tt = p.root / "inputs" / "margin" / "trades.tt"
        tt.write_text(tt.read_text() + "BUYSELL 2025-04-06 10:00:00 QZNW.TO 1 CAD 10 10 0\n")
        for args in (("gains-cra",), ("gains-cra", "--json")):
            with self.subTest(args=args):
                r = p.tjs(*args)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertEqual(r.stdout, "")
                self.assertIn("tjs run", r.stderr)

    def test_a_us_project_is_refused(self):
        toml = ('[settings]\nlocal_timezone = "America/New_York"\nyear = 2025\n'
                'country = "usa"\nbase_currency = "USD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
        p = Project(files={"margin": "BUYSELL 2025-01-06 10:00:00 QZNW 10 USD 10 100 0\n"
                                     "BUYSELL 2025-03-06 10:00:00 QZNW -10 USD 12 120 0\n"},
                    toml=toml, planted=False)
        p.run()
        r = p.tjs("gains-cra", "--json")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("Canada", r.stderr)
        self.assertIn("form-export --form 8949", r.stderr)

    def test_an_unknown_cost_is_reported_by_hand(self):
        p = Project(files={"margin": "OPENING 2024-12-02 QZUK.TO 10 cost=unknown\n"
                                     "BUYSELL 2025-03-06 10:00:00 QZUK.TO -10 CAD 12 120 0\n"
                                     "BUYSELL 2025-01-06 10:00:00 QZNW.TO 10 CAD 10 100 0\n"
                                     "BUYSELL 2025-03-06 10:00:00 QZNW.TO -10 CAD 12 120 0\n"},
                    planted=False)
        p.run()
        doc = p.json("gains-cra")
        self.assertEqual([r["symbol"] for r in doc["rows"]], ["QZNW.TO"])
        self.assertEqual([r["symbol"] for r in doc["manual_reporting_required"]], ["QZUK.TO"])
        self.assertEqual(cents(doc["totals"]["proceeds"]), Decimal("120.00"))
        self.assertIn("MANUAL REPORTING REQUIRED", p.tjs("gains-cra").stdout)


class TestTheNativeBooksMissing(unittest.TestCase):
    """The mixed project with its native gains file deleted, then its rates
    too: a cell with no native figure or FX shows a dash with the note why,
    never 0.00 (a future's P/L has no parsed sale to stand in for it), the
    currency's subtotal says how many rows its native total leaves out, and
    an FX column empty on every row stays with a line naming why."""

    @classmethod
    def setUpClass(cls):
        cls.p = Project()
        cls.p.run()
        work = cls.p.root / "work"
        (work / "margin_raw_gains.json").unlink()
        cls.doc = cls.p.json("gains-cra")
        cls.text = cls.p.tjs("gains-cra").stdout
        (work / "to_base.csv").rename(work / "to_base.csv.moved")
        cls.doc2 = cls.p.json("gains-cra")
        cls.text2 = cls.p.tjs("gains-cra").stdout

    def test_no_native_record_is_a_dash_with_a_note(self):
        usd = [r for r in self.doc["rows"] if r["currency"] == "USD"]
        fut = _by_symbol(self.doc, "F:QZM26.US")[0]
        self.assertEqual((fut["native_proceeds"], fut["fx_rate"]), (None, None))
        self.assertTrue(any("no USD record of this trade in work/ (no readable "
                            "margin_raw_gains.json)" in n for n in fut["notes"]), fut["notes"])
        # The long sale's parsed native row still serves it.
        sale = _by_symbol(self.doc, "QZRX.US")[0]
        self.assertEqual(sale["native_source"], "parsed row")
        self.assertEqual(cents(sale["native_proceeds"]), Decimal("1000.00"))
        t = self.doc["tables"][1]
        self.assertEqual((t["currency"], t["rows"], t["native_missing"]), ("USD", len(usd), 4))
        self.assertEqual(cents(t["native_proceeds"]), Decimal("1000.00"))
        line = next(ln for ln in self.text.splitlines() if "QZM26 future" in ln)
        self.assertEqual(line.split().count("—"), 2, line)
        self.assertIn(f"(USD: 1 of {len(usd)} rows)", self.text)

    def test_an_fx_column_empty_on_every_row_stays_and_says_why(self):
        t = self.doc2["tables"][1]
        self.assertEqual(t["fx_missing"], t["rows"])
        self.assertEqual(t["fx_missing_why"], {"no USD rate in work/to_base.csv": 1,
                                               "no USD record in work/": 4})
        head = next(ln for ln in self.text2.splitlines()
                    if ln.startswith("DATE ") and "FX" in ln.split())
        self.assertIn("DESCRIPTION", head.split())
        self.assertIn("No row of this table has an FX (—): 1 row: no USD rate in "
                      "work/to_base.csv; 4 rows: no USD record in work/.", self.text2)
        sale = _by_symbol(self.doc2, "QZRX.US")[0]
        self.assertTrue(any(n.startswith("no USD rate for 2025-03-10 in work/to_base.csv")
                            for n in sale["notes"]), sale["notes"])


class TestTheRoundingLines(unittest.TestCase):
    """Where a line's rows add to other cents than Schedule 3's figure:
    both figures per column, by FOR THE RETURN's column names, the line by
    the label `tjs sum` gives it."""

    DOC = {"lines": [
        {"title": "Part 3, line 4 (lines 13199/13200)", "short": "shares & fund units",
         "line": "4", "proceeds_code": "13199", "gain_code": "13200", "proceeds": 500.0,
         "acb": 300.0, "outlays": 4.06, "total_cost": 304.06, "gain": 195.94,
         "denied_all": 0.0,
         "rows_sum": {"proceeds": 500.0, "acb": 300.04, "outlays": 4.1, "total_cost": 304.14,
                      "gain": 195.86, "denied_all": 0.0}},
        {"title": "Part 3, line 6 (lines 15199/15300)", "short": "options & other properties",
         "line": "6", "proceeds_code": "15199", "gain_code": "15300", "proceeds": 10.0,
         "acb": 0.0, "outlays": 0.0, "total_cost": 0.0, "gain": 10.0, "denied_all": 2.0,
         "rows_sum": {"proceeds": 10.0, "acb": 0.0, "outlays": 0.0, "total_cost": 0.0,
                      "gain": 10.0, "denied_all": 2.01}}]}

    def test_both_figures_per_column(self):
        self.assertEqual(GC._rounding_lines(self.DOC, "CAD"), [
            "Line 4 shares & fund units (13199/13200): the rows add to COST(ACB) 300.04 (the "
            "line 300.00), OUTLAYS 4.10 (the line 4.06), TOTAL COST 304.14 (the line 304.06), "
            "GAIN 195.86 (the line 195.94) CAD: Schedule 3 rounds each security's total, the "
            "rows each sale.",
            "Line 6 options & other properties (15199/15300): the rows add to DENIED (INFO) "
            "2.01 (the line 2.00) CAD: Schedule 3 rounds each security's total, the rows each "
            "sale."])


class TestTheDescriptions(unittest.TestCase):
    def test_each_form(self):
        # A share or fund unit: its ticker alone, the listing suffix dropped
        # (the broker's name for it is never looked up).
        self.assertEqual(GC.describe({"symbol": "QZRX.US", "direction": "LONG"}), "QZRX")
        self.assertEqual(GC.describe({"symbol": "QZNW.TO", "direction": "LONG",
                                      "security_name": "Quartz New Inc"}), "QZNW")
        self.assertEqual(GC.describe({"symbol": "QZC", "direction": "LONG", "_crypto": True}),
                         "QZC (crypto)")
        self.assertEqual(GC.describe({"symbol": "QZRX250221C00030000.US", "direction": "LONG"}),
                         "QZRX $30 call exp 2025-02-21")
        self.assertEqual(GC.describe({"symbol": "QZRX250221P00030000.US", "direction": "SHORT"}),
                         "QZRX $30 put exp 2025-02-21 (written)")
        self.assertEqual(GC.describe({"symbol": "F:QZM26.US", "direction": "LONG"}),
                         "QZM26 future")
        self.assertFalse(hasattr(GC, "trade_name"))


_MARKS = re.compile(r"\[(\d+(?:,\d+)*)\]")
# More calls and puts written under grant timing and bought back the same
# year: two in USD, one in CAD.
_BUY_BACKS = """\
BUYSELL 2025-06-03 10:00:00 QZRX251219P00020000.US -3 USD 1.00 298.70 1.30
BUYSELL 2025-08-05 10:00:00 QZRX251219P00020000.US 3 USD 0.20 61.30 1.30
BUYSELL 2025-06-04 10:00:00 QZNW251219C00015000.TO -1 CAD 1.00 98.70 1.30
BUYSELL 2025-08-06 10:00:00 QZNW251219C00015000.TO 1 CAD 0.20 21.30 1.30
"""


def _table_marks(text, currency):
    """{(date, description start): [marks]} of the table SALES IN `currency`,
    and its note lines by mark."""
    lines = text.splitlines()
    i = lines.index(f"SALES IN {currency}")
    end = next((k for k in range(i + 1, len(lines)) if lines[k].startswith(("SALES IN", "ALL SALES",
                                                                           "FOR THE RETURN"))),
               len(lines))
    rows, notes = {}, {}
    for ln in lines[i + 1:end]:
        m = re.match(r"\[(\d+)\]  (.*)", ln)
        if m:
            notes[m.group(1)] = m.group(2)
        elif re.match(r"\d{4}-\d\d-\d\d ", ln):
            found = _MARKS.search(ln)
            rows[ln[:10]] = found.group(1).split(",") if found else []
    return rows, notes


class TestBuyBacksShareOneNote(unittest.TestCase):
    """Every buy-back of an option written this year (grant timing) has the
    same note: one mark per table, its sentence once under it; a row's own
    figure (a superficial loss) keeps a mark of its own; the JSON keeps
    each row's notes and their kinds."""
    SHARED = GC._SHARED["counted_at_write"]

    @classmethod
    def setUpClass(cls):
        cls.p = Project(files={"margin": MARGIN_TT + _BUY_BACKS, "tfsa": TFSA_TT})
        cls.p.run()
        cls.doc = cls.p.json("gains-cra")
        cls.fe = cls.p.json("form-export", "--form", "schedule3")

    def _backs(self, currency):
        return [r for r in self.doc["rows"] if r["currency"] == currency
                and "counted_at_write" in r["note_kinds"]]

    def test_one_mark_and_one_line_per_table(self):
        for width in ("120", "0"):
            out = self.p.tjs("gains-cra", TAXJSON_WIDTH=width).stdout
            for cur, n in (("USD", 2), ("CAD", 1)):
                with self.subTest(width=width, currency=cur):
                    backs = self._backs(cur)
                    self.assertEqual(len(backs), n)
                    rows, notes = _table_marks(out, cur)
                    marks = {m for r in backs for m in rows[r["date"]]}
                    self.assertEqual(len(marks), 1, rows)
                    mark = marks.pop()
                    self.assertEqual(" ".join(notes[mark].split()), self.SHARED)
                    self.assertEqual([t for t in notes.values() if "at the write" in t],
                                     [notes[mark]])
            self.assertNotIn("nothing was received", out)
            self.assertNotIn("of its units are counted", out)

    def test_a_denial_s_mark_names_its_rows_and_total(self):
        out = self.p.tjs("gains-cra", TAXJSON_WIDTH="0").stdout
        rows, notes = _table_marks(out, "CAD")
        loss = _by_symbol(self.doc, "QZLS.TO")[0]
        self.assertEqual(len(rows[loss["date"]]), 1)
        self.assertTrue(notes[rows[loss["date"]][0]].startswith(
            "superficial loss denied (1 row, 100.00 CAD): each row's COST(ACB) is reduced"))

    def test_a_shared_note_is_once_without_a_row(self):
        out = self.p.tjs("gains-cra", TAXJSON_WIDTH="0").stdout
        _rows, notes = _table_marks(out, "USD")
        # A shared note is no one row's: no date and description before it
        # (a weighted rate's too: it has no figure of the row's own).
        self.assertEqual([t for t in notes.values() if "at the write" in t], [self.SHARED])
        self.assertTrue(any(t.startswith("FX: the rate of the opening") for t in notes.values()),
                        notes)
        # A denial's note counts and adds its rows; it lists none under it
        # (each row's amount is in its DENIED (INFO) cell).
        self.assertIn("superficial loss denied (1 row, 100.00 CAD)", out)
        self.assertNotIn("\n     - 2025-", out)

    def test_the_json_keeps_each_row_s_notes(self):
        for r in self._backs("USD") + self._backs("CAD"):
            with self.subTest(row=r["description"]):
                self.assertEqual(len(r["notes"]), len(r["note_kinds"]))
                self.assertEqual(r["units_counted_elsewhere"], r["qty"])
                self.assertTrue(any("counted once, at the write" in n for n in r["notes"]))
        self.assertEqual([r["note_kinds"] for r in self._backs("USD")],
                         [["nothing_received", "counted_at_write"]] * 2)

    def test_the_units_are_form_export_s_less_the_ones_noted(self):
        for ln in self.doc["lines"]:
            rows = [r for r in self.doc["rows"] if r["line_key"] == ln["key"]]
            want = sum(Decimal(str(r["units"])) for r in self.fe["rows"]
                       if r["line_key"] == ln["key"])
            got = sum(Decimal(str(r["qty"])) - Decimal(str(r["units_counted_elsewhere"]))
                      for r in rows)
            self.assertEqual(got, want, ln["title"])

    def test_the_marks_are_in_order_and_a_rate_note_is_shared(self):
        weighted = ("FX: the rate of the opening sale(s) or write, proceeds-weighted (CAD ÷ USD, "
                    "4 places)")
        short = {"qty": 1.0, "units_counted_elsewhere": 0.0,
                 "note_kinds": ["weighted_fx", "short_sale"], "notes": [weighted, "x"]}
        check = "1.00 USD × 1.3000 = 1.30, not the Proceeds (CAD) 1.31"
        own = {"qty": 1.0, "units_counted_elsewhere": 0.0,
               "note_kinds": ["fx_check", "weighted_fx", "short_sale"],
               "notes": [check, weighted, "x"]}
        marks, notes = GC._marks([short, own], 6, "CAD")
        # The FX check is the row's own, 9; its shared notes 7 and 8: "[7,8,9]", not "[9,7,8]".
        self.assertEqual(marks, [" [7,8]", " [7,8,9]"])
        self.assertEqual([(n, r is None, t) for n, r, t in notes],
                         [(7, True, weighted), (8, True, GC._SHARED["short_sale"]),
                          (9, False, check)])

    def test_a_buy_back_counted_in_part_keeps_its_own_count(self):
        part = {"qty": 3.0, "units_counted_elsewhere": 2.0,
                "note_kinds": ["counted_at_write"],
                "notes": ["2 of its units are counted once, at the write (Schedule 3's units)"]}
        whole = dict(part, qty=2.0)
        marks, notes = GC._marks([whole, part, dict(whole)], 0, "CAD")
        self.assertEqual(marks, [" [1]", " [2]", " [1]"])
        self.assertEqual([(n, r is None, t) for n, r, t in notes],
                         [(1, True, self.SHARED), (2, False, part["notes"][0])])


# More superficial losses in CAD: QZLB and QZLS deferred, QZLC deferred in
# part and denied for good in part (half its buy-back in the tfsa), QZLD
# denied for good.
_LOSSES = """\
BUYSELL 2025-01-10 10:00:00 QZLB.TO 10 CAD 30.00 300.00 0
BUYSELL 2025-03-10 10:00:00 QZLB.TO -10 CAD 25.00 250.00 0
BUYSELL 2025-03-20 10:00:00 QZLB.TO 10 CAD 26.00 260.00 0
BUYSELL 2025-01-15 10:00:00 QZLC.TO 20 CAD 20.00 400.00 0
BUYSELL 2025-04-01 10:00:00 QZLC.TO -20 CAD 15.00 300.00 0
BUYSELL 2025-04-05 10:00:00 QZLC.TO 10 CAD 16.00 160.00 0
BUYSELL 2025-01-20 10:00:00 QZLD.TO 10 CAD 40.00 400.00 0
BUYSELL 2025-06-02 10:00:00 QZLD.TO -10 CAD 35.00 350.00 0
"""
_TFSA_BUYS = """\
BUYSELL 2025-04-06 10:00:00 QZLC.TO 10 CAD 16.00 160.00 0
BUYSELL 2025-06-05 10:00:00 QZLD.TO 10 CAD 36.00 360.00 0
"""


def _note_block(text, currency):
    """The notes under the table SALES IN `currency`: {mark: (its text, its
    wrapping undone; the `- ` lines under it)}."""
    lines = text.splitlines()
    i = lines.index(f"SALES IN {currency}")
    out, mark = {}, None
    for ln in lines[i + 1:]:
        if ln.startswith(("SALES IN", "ALL SALES", "FOR THE RETURN")):
            break
        m = re.match(r"\[(\d+)\]  (.*)", ln)
        if m:
            mark = m.group(1)
            out[mark] = [m.group(2), []]
        elif mark and ln.startswith("     - "):
            out[mark][1].append(ln[len("     - "):])
        elif mark and ln.startswith("     "):
            out[mark][0] += " " + ln.strip()
        else:
            mark = None
    return {k: (t, subs) for k, (t, subs) in out.items()}


class TestDenialsShareOneMarkPerKind(unittest.TestCase):
    """Superficial losses denied: one mark per kind in a table, its line
    once under it with the table's count and total; a row with a deferred
    and a permanent part both marks; each row's own amount in its DENIED
    (INFO) cell, the CSV's Denied column and the JSON."""
    DEFERRED = ("superficial loss denied ({}): each row's COST(ACB) is reduced by its denied "
                "loss, so GAIN is the allowed part; the denied loss is added to the "
                "replacement's ACB")
    FOR_GOOD = ("superficial loss denied for good ({}; a registered-account or affiliated "
                "buy-back): each row's COST(ACB) is reduced by it")

    @classmethod
    def setUpClass(cls):
        cls.p = Project(files={"margin": MARGIN_TT + _LOSSES, "tfsa": TFSA_TT + _TFSA_BUYS})
        cls.p.run()
        cls.doc = cls.p.json("gains-cra")
        cls.cad = [r for r in cls.doc["rows"] if r["currency"] == "CAD"]

    def _kind(self, kind):
        return [r for r in self.cad if kind in r["note_kinds"]]

    def _marks_of(self, out):
        rows, _notes = _table_marks(out, "CAD")
        return {r["symbol"]: rows[r["date"]] for r in self.cad}

    def test_the_rows_and_their_kinds(self):
        self.assertEqual({r["symbol"]: r["note_kinds"] for r in self.cad if r["note_kinds"]},
                         {"QZLB.TO": ["denied"], "QZLC.TO": ["denied", "denied_permanent"],
                          "QZLS.TO": ["denied"], "QZLD.TO": ["denied_permanent"]})

    def test_deferred_denials_share_one_mark_and_their_total(self):
        rows = self._kind("denied")
        total = sum((cents(r["denied"]) - cents(r["permanently_denied"]) for r in rows),
                    Decimal(0))
        self.assertEqual(total, Decimal("200.00"))
        for width in ("0", "100", "80"):
            out = self.p.tjs("gains-cra", TAXJSON_WIDTH=width).stdout
            with self.subTest(width=width):
                marks = self._marks_of(out)
                notes = _note_block(out, "CAD")
                deferred = {marks[r["symbol"]][0] for r in rows}
                self.assertEqual(len(deferred), 1, marks)
                mark = deferred.pop()
                self.assertEqual(notes[mark][0],
                                 self.DEFERRED.format(f"{len(rows)} rows, {total:,.2f} CAD in all"))
                # Only the deferred rows carry it.
                self.assertEqual(sorted(s for s, m in marks.items() if mark in m),
                                 sorted(r["symbol"] for r in rows))
                self.assertFalse([ln for ln in out.splitlines() if "superficial loss 100.00" in ln])

    def test_a_permanent_denial_has_its_own_mark(self):
        out = self.p.tjs("gains-cra", TAXJSON_WIDTH="100").stdout
        marks, notes = self._marks_of(out), _note_block(out, "CAD")
        perm = self._kind("denied_permanent")
        total = sum((cents(r["permanently_denied"]) for r in perm), Decimal(0))
        mark = marks["QZLD.TO"][0]
        self.assertEqual(notes[mark][0], self.FOR_GOOD.format(f"2 rows, {total:,.2f} CAD in all"))
        self.assertNotEqual(mark, marks["QZLB.TO"][0])
        # A row with both parts carries both marks, in order.
        self.assertEqual(marks["QZLC.TO"], sorted({marks["QZLB.TO"][0], mark}, key=int))
        # Wrapped at 100 and 80: under the note's text, never past the width.
        for width in (100, 80):
            out = self.p.tjs("gains-cra", TAXJSON_WIDTH=str(width)).stdout
            block = out[out.index("SALES IN CAD"):out.index("SALES IN USD")].splitlines()
            block = block[next(i for i, ln in enumerate(block) if ln.startswith("SUBTOTAL")):]
            notes = [ln for ln in block if ln.startswith(("[", "     "))]
            with self.subTest(width=width):
                self.assertTrue(notes)
                self.assertEqual([ln for ln in notes if len(ln) > width], [])
                self.assertTrue(any(ln.startswith("     ") for ln in notes), notes)

    def test_each_row_s_amount_is_its_denied_info_cell(self):
        out = self.p.tjs("gains-cra", TAXJSON_WIDTH="0").stdout
        notes = _note_block(out, "CAD")
        self.assertEqual([s for _t, s in notes.values() if s], [])
        lines = out.splitlines()
        i = lines.index("SALES IN CAD")
        cell = _denied_cells(lines, i)
        cells = {}
        for r in self.cad:
            ln = next(x for x in lines[i:] if x.startswith(r["date"]))
            cells[r["symbol"]] = ln[cell].strip()
        self.assertEqual({k: v for k, v in cells.items() if v},
                         {"QZLB.TO": "50.00", "QZLC.TO": "100.00", "QZLS.TO": "100.00",
                          "QZLD.TO": "50.00"})
        self.assertEqual({k for k, v in cells.items() if not v}, {"QZNW.TO"})
        foot = next(x for x in lines[i:] if x.startswith("SUBTOTAL"))
        self.assertEqual(foot[cell].strip(),
                         f"{cents(self.doc['tables'][0]['denied_all']):,.2f}")

    def test_the_csv_s_denied_column(self):
        out = self.p.root / "cra.csv"
        self.assertEqual(self.p.tjs("gains-cra", "--csv", str(out)).returncode, 0)
        with open(out, newline="", encoding="utf-8") as f:
            got = list(csv.DictReader(f))
        heads = list(GC.CSV_COLUMNS)
        self.assertEqual(heads.index("Denied (CAD)"), heads.index("Gain/Loss (CAD)") + 1)
        for line, row in zip(got, self.doc["rows"]):
            with self.subTest(row=row["description"], date=row["date"]):
                want = cents(row["denied"]) + cents(row["denied_contribution"])
                # The text's DENIED (INFO) figure, `denied_all` (TJ-5341).
                self.assertEqual(cents(row["denied_all"]), want)
                if want:
                    self.assertEqual(cents(line["Denied (CAD)"]), want)
                else:
                    self.assertEqual(line["Denied (CAD)"], "")
                self.assertEqual(line["Notes"], "; ".join(row["notes"]))
        self.assertEqual({ln["Symbol"]: ln["Denied (CAD)"] for ln in got if ln["Denied (CAD)"]},
                         {"QZLB.TO": "50.0", "QZLC.TO": "100.0", "QZLS.TO": "100.0",
                          "QZLD.TO": "50.0"})

    def test_the_tables_add_the_denials(self):
        for t in self.doc["tables"]:
            rows = [r for r in self.doc["rows"] if r["currency"] == t["currency"]]
            for k in ("denied", "permanently_denied", "denied_contribution", "denied_all"):
                with self.subTest(table=t["currency"], key=k):
                    self.assertEqual(cents(t[k]), sum((cents(r[k]) for r in rows), Decimal(0)))
        cad = self.doc["tables"][0]
        self.assertEqual((cents(cad["denied"]), cents(cad["permanently_denied"])),
                         (Decimal("300.00"), Decimal("100.00")))
        self.assertEqual(cents(self.doc["rows_total"]["denied"]), Decimal("300.00"))


_TWO_LISTINGS = """\
BUYSELL 2025-01-06 10:00:00 QZDD.US 100 USD 10.00 1001.00 1.00
BUYSELL 2025-01-06 11:00:00 QZDD.TO 100 USD 20.00 2001.00 1.00
BUYSELL 2025-05-05 10:00:00 QZDD.US -50 USD 12.00 599.00 1.00
BUYSELL 2025-05-05 11:00:00 QZDD.TO -50 USD 22.00 1099.00 1.00
BUYSELL 2025-02-03 10:00:00 QZNW.TO 10 CAD 50.00 500.00 0
BUYSELL 2025-05-05 10:00:00 QZNW.TO -10 CAD 55.00 550.00 0
"""


class TestTwoListingsOfOneTicker(unittest.TestCase):
    """Two securities whose symbols strip to one ticker (`DISTINCT QZDD.US
    QZDD.TO` in ticker.map), sold the same day: each row's Description is
    its full symbol, in the text, --details and the CSV (with the Symbol
    column); with one listing alone, the ticker."""

    @classmethod
    def setUpClass(cls):
        cls.p = Project(files={"margin": _TWO_LISTINGS})
        (cls.p.root / "ticker.map").write_text("DISTINCT QZDD.US QZDD.TO\n", encoding="utf-8")
        cls.p.run()
        cls.doc = cls.p.json("gains-cra")
        cls.one = Project(files={"margin": "".join(
            ln + "\n" for ln in _TWO_LISTINGS.splitlines() if "QZDD.TO" not in ln)})
        cls.one.run()

    def _rows(self, text, day="2025-05-05"):
        return [ln for ln in text.splitlines() if ln.startswith(f"{day}  ")]

    def test_the_json(self):
        self.assertEqual({r["symbol"]: r["description"] for r in self.doc["rows"]},
                         {"QZDD.US": "QZDD.US", "QZDD.TO": "QZDD.TO", "QZNW.TO": "QZNW"})

    def test_the_text_and_details(self):
        for args in ((), ("--details",)):
            out = self.p.tjs("gains-cra", *args, TAXJSON_WIDTH="120").stdout
            with self.subTest(args=args):
                descs = sorted(ln.split()[2] for ln in self._rows(out))
                self.assertEqual(descs, ["QZDD.TO", "QZDD.US", "QZNW"])

    def test_the_csv(self):
        out = self.p.root / "cra.csv"
        self.assertEqual(self.p.tjs("gains-cra", "--csv", str(out)).returncode, 0)
        with open(out, newline="", encoding="utf-8") as f:
            got = list(csv.DictReader(f))
        self.assertEqual(sorted((r["Description"], r["Symbol"]) for r in got),
                         [("QZDD.TO", "QZDD.TO"), ("QZDD.US", "QZDD.US"), ("QZNW", "QZNW.TO")])

    def test_one_listing_reads_its_ticker(self):
        doc = self.one.json("gains-cra")
        self.assertEqual({r["symbol"]: r["description"] for r in doc["rows"]},
                         {"QZDD.US": "QZDD", "QZNW.TO": "QZNW"})
        out = self.one.tjs("gains-cra", TAXJSON_WIDTH="120").stdout
        self.assertEqual(sorted(ln.split()[2] for ln in self._rows(out)), ["QZDD", "QZNW"])


class TestAGoldenBook(unittest.TestCase):
    """style-canada's recorded views: gains-cra's totals are its sum's
    FOR THE RETURN rows and its form-export's lines."""

    def _view(self, argv):
        name = _golden.view_name({"argv": argv})
        p = _golden.GOLDEN / "style-canada" / "expected" / "_views" / f"{name}.stdout.json"
        return json.loads(p.read_text(encoding="utf-8"))

    def test_the_totals(self):
        doc, fe, sm = (self._view("gains-cra --json"), self._view("form-export --json"),
                       self._view("sum --json"))
        self.assertTrue(doc["rows"])
        for r in doc["rows"]:
            self.assertEqual(cents(r["proceeds"]) - cents(r["acb"]) - cents(r["outlays"]),
                             cents(r["gain"]))
        sml = {(ln["key"], ln["period"]): ln for ln in sm["filing"]["lines"]}
        fel = {(ln["key"], ln["period"]): ln for ln in fe["lines"]}
        for ln in doc["lines"]:
            for cell in CELLS:
                k = (ln["key"], ln["period"])
                self.assertEqual(cents(ln[cell]), cents(sml[k][cell]), (ln["title"], cell))
                self.assertEqual(cents(ln[cell]), cents(fel[k][cell]), (ln["title"], cell))
        for cell in CELLS:
            self.assertEqual(cents(doc["totals"][cell]),
                             cents(sm["filing"]["totals"][cell]), cell)

    def test_a_share_s_description_is_its_ticker(self):
        # The book's broker exports name their securities: never shown.
        from taxjson.lib.markets import strip_listing_suffix
        shares = [r for r in self._view("gains-cra --json")["rows"] if r["property"] == "shares"]
        self.assertTrue(shares)
        for r in shares:
            self.assertEqual(r["description"], strip_listing_suffix(r["symbol"]), r["symbol"])


def _date(text):
    return datetime.date.fromisoformat(text)


if __name__ == "__main__":
    unittest.main()
