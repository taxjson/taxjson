"""2026-10-29 fix round, group taxtext: the two .tt writers
(taxjson-convert-tt's tx_to_tt_line and the `tjs events` view's
_tx_display_line) write what the .tt reader reads back, and the reader
refuses an ambiguous line.

- TJ-0377: a warrant or right exercise leg keeps its `exercise_of=`
  (owner decision 2026-10-29): exported and read back, the warrant's
  cost still goes into the shares, no 2025 loss.
- TJ-0385: a contract size is written in fixed-point decimals
  (`x1000000`, `x0.00001`), never `x1e+06`, which the reader refused.
- TJ-0386: a fact token given twice (`record=`, `plan=` ...) is refused,
  naming both values.
- TJ-0390: the events view writes a dividend's and an ADJUST's income
  facts (`record=`, `label=`, `type=`, `dealer=`, `issuer=`), as
  taxjson-convert-tt does.

Every fixture is synthetic: invented tickers, made-up amounts."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_convert_tt import (json_to_tt_lines,
                                            parse_inkind_line, parse_tt_line,
                                            tt_to_json, tx_to_tt_line)
from taxjson.lib.core import TaxTransaction
from tax_rules import rule
from tax_rules.dual import gains_both, tx
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _display(row, settle=True):
    from taxjson.bin.taxjson_run import _tx_display_line
    return _tx_display_line(row, settle=settle)


def _parse(line, account="margin"):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = parse_tt_line(line, account_name=account, source="h.tt:1")
    return out, err.getvalue()


def _reimport(lines, account="margin"):
    return [TaxTransaction(**_parse(ln, account)[0]) for ln in lines]


def _gains(book, year):
    r = gains_both(book, year=year)
    return {c: (round(r[c]["summary"]["total_gain"], 2),
                sorted((x["symbol"], x["date"], round(x["gain"], 2))
                       for x in r[c]["transactions"] if "gain" in x))
            for c in r}


# ------------------------------------------------- TJ-0377 warrant exercise

def _warrant_book():
    """A warrant bought for 201 in 2025, exercised into 100 shares at
    11.50 on 2025-05-02 (the ASSIGN leg the broker parser marks), the
    shares sold in 2026."""
    w = tx("ASSIGN", "2025-05-02", "QZW.US", -100, 0, time="16:20:00")
    w.exercise_of = "QZX.US"
    return [tx("BUYSELL", "2025-02-03", "QZW.US", 100, 201),
            w,
            tx("BUYSELL", "2025-05-02", "QZX.US", 100, 1150,
               time="16:20:00"),
            tx("BUYSELL", "2026-09-10", "QZX.US", -100, 1999)]


class TestWarrantExerciseRoundTrip(unittest.TestCase):
    """TJ-0377: both writers keep the exercise link, the reader reads
    it, and the re-imported book has the original's gains."""

    def _check_same_gains(self, lines):
        book = _warrant_book()
        back = _reimport(lines)
        self.assertEqual(back[1].exercise_of, "QZX.US", lines[1])
        for year in (2025, 2026):
            self.assertEqual(_gains(back, year), _gains(book, year),
                             f"{year}: {lines}")
        # No 2025 loss on the warrants; 2026 carries the warrant's cost.
        g25 = _gains(back, 2025)
        g26 = _gains(back, 2026)
        for c in ("canada", "usa"):
            self.assertEqual(g25[c], (0, []), c)
            self.assertEqual(g26[c][0], round(1999 - 1150 - 201, 2), c)

    @rule("CA-OPT-09")
    @rule("US-OPT-06")
    def test_converter_writes_and_reads_exercise_of(self):
        lines = [tx_to_tt_line(t.to_dict()) for t in _warrant_book()]
        self.assertTrue(lines[1].startswith("ASSIGN "), lines[1])
        self.assertTrue(lines[1].endswith(" exercise_of=QZX.US"), lines[1])
        self.assertNotIn("exercise_of", lines[0] + lines[2] + lines[3])
        self._check_same_gains(lines)

    @rule("CA-OPT-09")
    @rule("US-OPT-06")
    def test_events_view_writes_and_reads_exercise_of(self):
        lines = [_display(t.to_dict()) for t in _warrant_book()]
        self.assertTrue(lines[1].endswith(" exercise_of=QZX.US"), lines[1])
        self._check_same_gains(lines)

    @rule("CA-OPT-09")
    @rule("US-OPT-06")
    def test_json_to_tt_to_json_files_keep_the_link(self):
        """The file round trip `taxjson-convert-tt book.json out.tt`,
        then the run's reader (tt_to_json): the row in the books
        carries exercise_of, and its id is the original's."""
        book = _warrant_book()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "book.json"
            src.write_text(json.dumps(
                {"transactions": [t.to_dict() for t in book]}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                text = "\n".join(json_to_tt_lines(src)) + "\n"
            tt = Path(tmp) / "margin" / "hand.tt"
            tt.parent.mkdir()
            tt.write_text(text)
            with contextlib.redirect_stderr(err):
                rows = tt_to_json(tt, "margin", equity=True)["transactions"]
        w = [r for r in rows if r["action"] == "ASSIGN"]
        self.assertEqual(len(w), 1, text)
        self.assertEqual(w[0]["exercise_of"], "QZX.US")
        self.assertEqual(w[0]["id"], book[1].compute_id())
        back = [TaxTransaction(**{k: v for k, v in r.items()
                                  if k not in ("source", "source_key")})
                for r in rows]
        for year in (2025, 2026):
            self.assertEqual(_gains(back, year), _gains(book, year), year)

    def test_declared_shares_per_warrant_ride_along(self):
        """A warrant's declared shares per warrant (`multiplier`: two
        warrants buy one share) is written as x<size> on the marked
        leg, and read back."""
        w = _warrant_book()[1].to_dict()
        w["multiplier"] = 0.5
        for line in (tx_to_tt_line(w), _display(w)):
            self.assertTrue(line.endswith(" x0.5 exercise_of=QZX.US"), line)
            back, _err = _parse(line)
            self.assertEqual(back["multiplier"], 0.5, line)
            self.assertEqual(back["exercise_of"], "QZX.US", line)

    def test_token_order_after_the_fee_is_free(self):
        a, _ = _parse("ASSIGN 2025-05-02 16:20:00 QZW.US -100 USD 0 0 0 "
                      "x0.5 exercise_of=qzx.us")
        b, _ = _parse("ASSIGN 2025-05-02 16:20:00 QZW.US -100 USD 0 0 0 "
                      "exercise_of=QZX.US x0.5")
        for t in (a, b):
            self.assertEqual(t["exercise_of"], "QZX.US")
            self.assertEqual(t["multiplier"], 0.5)
        self.assertEqual(a["id"], b["id"])

    def test_a_canadian_listing_is_spelled_as_the_parsers_spell_it(self):
        t, _ = _parse("ASSIGN 2025-05-02 16:20:00 QZW.V -100 CAD 0 0 0 "
                      "exercise_of=QZX.V")
        self.assertEqual(t["exercise_of"], "QZX.TO")

    def test_refused_where_it_means_nothing(self):
        bad = {
            "BUYSELL 2025-05-02 16:20:00 QZW.US -100 USD 0 0 0 "
            "exercise_of=QZX.US": "only an ASSIGN",
            "ASSIGN 2025-06-20 16:00:00 QZX250620C00005000.US -1 USD 0 0 0 "
            "exercise_of=QZX.US": "option",
            "ASSIGN 2025-05-02 16:20:00 QZW.US 100 USD 0 0 0 "
            "exercise_of=QZX.US": "negative",
            "ASSIGN 2025-05-02 16:20:00 QZW.US -100 USD 0 0 0 "
            "exercise_of=QZW.US": "line's own symbol",
            "ASSIGN 2025-05-02 16:20:00 QZW.TO -100 CAD 0 0 0 "
            "exercise_of=QZW.V": "line's own symbol",
            "ASSIGN 2025-05-02 16:20:00 QZW.US -100 USD 0 0 0 "
            "exercise_of=QZX250620C00005000.US": "share listing",
            "ASSIGN 2025-05-02 16:20:00 QZW.US -100 USD 0 0 0 "
            "exercise_of=QZX.US exercise_of=QZY.US": "twice",
            "ASSIGN 2025-05-02 16:20:00 QZW.US -100 USD 0 0 0 "
            "deliver=QZX.US": "unknown .tt token",
        }
        for line, why in bad.items():
            with self.assertRaises(ValueError, msg=line) as cm:
                _parse(line)
            self.assertIn(why, str(cm.exception), line)
            self.assertIn("h.tt:1", str(cm.exception), line)

    def test_check_tt_reads_the_token(self):
        from taxjson.lib.check_tt import parse_line
        good = ("ASSIGN 2025-05-02 16:20:00 QZW.US -100 USD 0 0 0 "
                "exercise_of=QZX.US")
        rows = []
        kind, warns = parse_line(good, "h.tt:1", account="margin",
                                 crypto=False, equity=True, rows=rows)
        self.assertEqual(warns, [])
        self.assertEqual(rows[0]["exercise_of"], "QZX.US")
        with self.assertRaises(ValueError) as cm:
            parse_line(good.replace("ASSIGN", "BUYSELL"), "h.tt:1",
                       account="margin", crypto=False, equity=True)
        self.assertIn("exercise_of=", str(cm.exception))

    @rule("CA-OPT-09")
    @rule("US-OPT-06")
    def test_tjs_run_books_a_tt_warrant_exercise(self):
        """`tjs run` on a .tt file holding the exercise: the only
        disposition is the shares' sale, at a cost that carries the
        warrants' 201 (each country in its own currency: no rates)."""
        from taxjson.lib.country import COUNTRIES, HOME_CURRENCY
        from tax_rules.dual import cli_both, projects_both
        projects = {}
        with tempfile.TemporaryDirectory() as td:
            for c in COUNTRIES:
                cur = HOME_CURRENCY[c]
                sfx = ".TO" if cur == "CAD" else ".US"
                tt = (f"BUYSELL 2025-02-03 10:00:00 QZW{sfx} 100 {cur} 2.01 "
                      f"201.00\n"
                      f"ASSIGN 2025-05-02 16:20:00 QZW{sfx} -100 {cur} 0 0 "
                      f"exercise_of=QZX{sfx}\n"
                      f"BUYSELL 2025-05-02 16:20:00 QZX{sfx} 100 {cur} 11.50 "
                      f"1150.00\n"
                      f"BUYSELL 2025-09-10 10:00:00 QZX{sfx} -100 {cur} 19.99 "
                      f"1999.00\n")
                projects[c] = projects_both(
                    f"{td}/{c}", files={"inputs/margin/hand.tt": tt})[c]
            runs = cli_both(projects, "run", "--no-input")
            for c, r in runs.items():
                self.assertEqual(r.returncode, 0, f"{c}: {r.stderr[-2000:]}")
            for c, root in projects.items():
                g = json.loads((root / "work" / "margin_gains.json")
                               .read_text())
                sold = [(t["symbol"][:3], round(t["gain"], 2))
                        for t in g["transactions"] if "gain" in t]
                self.assertEqual(sold, [("QZX", 648.0)], c)


# ------------------------------------------------- TJ-0385 contract sizes

class TestContractSizeRoundTrip(unittest.TestCase):

    def _row(self, size, sym="F:QZF"):
        return dict(action="BUYSELL", date="2025-03-03",
                    date_settle="2025-03-03", time="10:00:00", symbol=sym,
                    quantity=1, currency="CAD", price=1, net_amount=size,
                    multiplier=size, account="margin")

    def test_both_writers_use_fixed_point_sizes(self):
        cases = {1000000.0: "x1000000", 0.00001: "x0.00001",
                 1e22: "x10000000000000000000000", 0.1: "x0.1",
                 50.0: "x50", 12.5: "x12.5"}
        for size, tok in cases.items():
            row = self._row(size)
            conv = tx_to_tt_line(row)
            # (the view shows money to the cent: its total is checked
            # by the converter's line, written in full)
            self.assertEqual(_parse(conv)[0]["net_amount"], size, conv)
            for line in (conv, _display(row)):
                self.assertEqual(line.split()[-1], tok, line)
                back, err = _parse(line)
                self.assertEqual(back["multiplier"], size, line)
                self.assertNotIn("differs", err, line)

    def test_an_option_size_other_than_100(self):
        row = self._row(1000000.0, sym="QZO250620C00005000.US")
        row["price"] = 0.001
        row["net_amount"] = 1000.0
        for line in (tx_to_tt_line(row), _display(row)):
            self.assertTrue(line.endswith(" x1000000"), line)
            self.assertEqual(_parse(line)[0]["multiplier"], 1000000.0)

    def test_a_line_an_older_release_wrote_still_reads(self):
        """v0.37.0 wrote `x1e+06`: such a line in a .tt file reads."""
        t, _ = _parse("BUYSELL 2025-03-03 10:00:00 F:QZF 1 CAD 1 1000000 0 "
                      "x1e+06")
        self.assertEqual(t["multiplier"], 1000000.0)
        t, _ = _parse("BUYSELL 2025-03-03 10:00:00 F:QZF 1 CAD 1 0.00001 0 "
                      "x1e-05")
        self.assertEqual(t["multiplier"], 0.00001)

    def test_a_size_that_is_not_a_finite_positive_number_is_refused(self):
        for tok in ("x1e999", "x0", "x0e5", "x1e-999"):
            with self.assertRaises(ValueError, msg=tok) as cm:
                _parse(f"BUYSELL 2025-03-03 10:00:00 F:QZF 1 CAD 1 1 0 {tok}")
            self.assertIn("contract size", str(cm.exception), tok)

    def test_converter_does_not_warn_it_lost_the_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "book.json"
            src.write_text(json.dumps({"transactions": [
                self._row(1000000.0), self._row(0.00001)]}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                lines = list(json_to_tt_lines(src))
        self.assertEqual([ln.split()[-1] for ln in lines],
                         ["x1000000", "x0.00001"])
        self.assertNotIn("cannot hold", err.getvalue())


# ------------------------------------------- TJ-0386 duplicate fact tokens

class TestDuplicateTokensRefused(unittest.TestCase):

    def test_income_fact_given_twice(self):
        cases = [
            "DIVIDEND 2025-01-15 10:00:00 QZT.TO 10 CAD 0 100 "
            "record=2024-12-31 record=2025-01-10 label=distribution",
            "DIVIDEND 2025-01-15 10:00:00 QZT.TO 10 CAD 0 100 "
            "record=2024-12-31 label=distribution record=2024-12-31",
            "ADJUST 2025-12-31 16:00:00 QZT.TO CAD -12.50 type=roc type=dist",
            "DIVIDEND_IN_LIEU 2025-03-03 10:00:00 QZT.TO 10 CAD 0 5 "
            "dealer=CA dealer=US",
        ]
        for line in cases:
            with self.assertRaises(ValueError, msg=line) as cm:
                _parse(line)
            msg = str(cm.exception)
            self.assertIn("twice", msg, line)
            self.assertIn("h.tt:1", msg, line)
        msg = ""
        try:
            _parse(cases[0])
        except ValueError as e:
            msg = str(e)
        self.assertIn("record=2024-12-31", msg)
        self.assertIn("record=2025-01-10", msg)

    def test_inkind_plan_given_twice(self):
        for line in ("INKIND 2025-03-03 QZT.TO -10 CAD 10 plan=own plan=rrsp",
                     "INKIND 2025-03-03 QZT.TO -10 CAD 10 plan=rrsp plan=rrsp",
                     "INKIND 2025-03-03 QZT.TO -10 plan=own plan=own"):
            with self.assertRaises(ValueError, msg=line) as cm:
                parse_inkind_line(line, "h.tt:1")
            self.assertIn("twice", str(cm.exception), line)
        try:
            parse_inkind_line(
                "INKIND 2025-03-03 QZT.TO -10 CAD 10 plan=own plan=rrsp",
                "h.tt:1")
        except ValueError as e:
            self.assertIn("plan=own", str(e))
            self.assertIn("plan=rrsp", str(e))

    def test_check_tt_names_the_line(self):
        from taxjson.lib.check_tt import check_paths
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "margin" / "hand.tt"
            p.parent.mkdir()
            p.write_text(
                "DIVIDEND 2025-01-15 10:00:00 QZT.TO 10 CAD 0 100 "
                "record=2024-12-31 record=2025-01-10\n"
                "INKIND 2025-03-03 QZT.TO -10 CAD 10 plan=own plan=rrsp\n")
            doc = check_paths([p], account="margin", crypto=False,
                              shown=["hand.tt"])
        probs = [(x["line"], x["severity"], "twice" in x["message"])
                 for x in doc["problems"]]
        self.assertEqual(probs, [(1, "error", True), (2, "error", True)],
                         doc)

    def test_one_of_each_still_reads(self):
        t, _ = _parse("DIVIDEND 2025-01-15 10:00:00 QZT.TO 10 CAD 0 100 "
                      "record=2024-12-31 label=distribution")
        self.assertEqual(t["record_date"], "2024-12-31")
        self.assertEqual(t["income_label"], "distribution")
        k = parse_inkind_line("INKIND 2025-03-03 QZT.TO -10 CAD 10 plan=rrsp")
        self.assertEqual(k["plan"], "rrsp")


# ------------------------------------------ TJ-0390 events view's facts

def _trust_distribution():
    return dict(action="DIVIDEND", date="2025-01-15",
                date_settle="2025-01-15", time="09:30:00", account="margin",
                symbol="QZT.TO", quantity=0, currency="CAD", price=0,
                net_amount=100, gross_amount=100, record_date="2024-12-31",
                income_label="distribution")


class TestEventsViewIncomeFacts(unittest.TestCase):

    @rule("CA-INC-DATE-TRUST")
    def test_trust_distribution_stays_in_the_record_year(self):
        from taxjson.lib.pipeline import GainsRequest, run_gains
        row = _trust_distribution()
        line = _display(row)
        self.assertIn("record=2024-12-31", line)
        self.assertIn("label=distribution", line)
        back, _ = _parse(line)
        self.assertEqual(back["record_date"], "2024-12-31")
        self.assertEqual(back["income_label"], "distribution")

        def divs(book, year):
            r = run_gains(book, req=GainsRequest(country="canada",
                                                 year=year))
            return {s: v["total_div"] for s, v in r["by_ticker"].items()}
        for year in (2024, 2025):
            self.assertEqual(divs([TaxTransaction(**back)], year),
                             divs([TaxTransaction(**row)], year), year)
        self.assertEqual(divs([TaxTransaction(**back)], 2024),
                         {"QZT.TO": 100.0})
        self.assertEqual(divs([TaxTransaction(**back)], 2025), {})

    def test_every_fact_rides_along_as_the_converter_writes_it(self):
        rows = [
            _trust_distribution(),
            dict(action="DIVIDEND_IN_LIEU", date="2025-03-03",
                 date_settle="2025-03-03", time="10:00:00", symbol="QZT.TO",
                 quantity=10, currency="CAD", price=0, net_amount=5,
                 gross_amount=5, dealer_country="CA", issuer_country="CA",
                 ex_date="2025-02-27"),
            dict(action="TAX", date="2025-01-15", date_settle="2025-01-15",
                 time="09:30:00", symbol="QZT.TO", quantity=0,
                 currency="CAD", price=0, net_amount=-15, gross_amount=-15,
                 record_date="2024-12-31"),
            dict(action="ADJUST", date="2025-12-31",
                 date_settle="2025-12-31", time="16:00:00", symbol="QZT.TO",
                 currency="CAD", net_amount=-12.5, type="roc",
                 record_date="2025-12-30"),
        ]
        for row in rows:
            conv = tx_to_tt_line(row)
            shown = _display(row)
            facts = [t for t in conv.split() if "=" in t]
            self.assertTrue(facts, conv)
            self.assertEqual([t for t in shown.split() if "=" in t], facts,
                             shown)
            a, _ = _parse(conv)
            b, _ = _parse(shown)
            for key in ("record_date", "ex_date", "income_label",
                        "dealer_country", "issuer_country", "type"):
                self.assertEqual(a.get(key), b.get(key), (key, shown))
                if row.get(key):
                    self.assertEqual(b.get(key), row[key], (key, shown))

    def test_events_command_prints_the_facts(self):
        """`tjs events all margin` (one account: taxtext) shows the
        facts on the dividend's line."""
        import os
        import subprocess
        from taxjson.lib.dispatch import python_module_argv
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": [_trust_distribution()]}))
            env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
            r = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_run",
                                   ["-C", str(root), "events", "all",
                                    "margin"]),
                capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = [ln for ln in r.stdout.splitlines()
                if ln.startswith("DIVIDEND")]
        self.assertEqual(len(line), 1, r.stdout)
        self.assertIn("record=2024-12-31", line[0])
        self.assertEqual(_parse(line[0])[0]["record_date"], "2024-12-31")


# ------------------------- found in passing: the events view's OPENING row

class TestEventsViewOpeningRow(unittest.TestCase):
    """A positions snapshot (`OPENING <date> <symbol> <qty> <cur>
    <total> [<lot-date>]`) is written as an OPENING line, as
    taxjson-convert-tt writes it: the view wrote a BUYSELL line, read
    back as a purchase without its lot date."""

    def test_opening_round_trips_with_its_lot_date(self):
        for text in ("OPENING 2024-12-31 QZQ.US 10 USD 1450.00 2023-05-01",
                     "OPENING 2024-12-31 QZQ.US 10 USD 450.00"):
            row, _ = _parse(text)
            line = _display(row)
            self.assertTrue(line.startswith("OPENING 2024-12-31 QZQ.US 10 "),
                            line)
            self.assertNotIn("00:00:00", line)
            back, _ = _parse(line)
            for key in ("type", "lot_date", "date", "time", "quantity",
                        "net_amount", "price", "description", "id"):
                self.assertEqual(back.get(key), row.get(key), (key, line))
            self.assertTrue(tx_to_tt_line(row).startswith("OPENING "))


if __name__ == "__main__":
    unittest.main()
