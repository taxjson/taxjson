"""The parsers' SAY rulings in `tjs run`'s end-of-run list (the owner's
rulings of 2026-11-19, backlog TJ-4559 (1) and TJ-4569 (3)): a booking
the owner ruled is said, not refused, is listed under "Before you trust
these numbers", not only printed mid-run where it scrolls away. The
parser marks the warning in its captured bytes (lib/out.END_OF_RUN_MARK),
a person is shown it without the mark, and lib/first_run.parser_warnings
reads it back from work/*.json.diag. Only the two SAYs are marked: the
parsers' other ATTENTION lines are TJ-4730.

Every fixture is synthetic: invented tickers, amounts and account ids."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from _support.generic_parse import _parse
from _support.ib_statement_rows import CA_H, HEAD, TRADES_H, _ca, _trade
from tax_rules.dual import cli, settings_for
from taxjson.lib import first_run as FR
from taxjson.lib import out

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


MARK = "warning: ATTENTION: " + out.END_OF_RUN_MARK
GEN_CSV = ("Date,Type,Ticker,Qty,Price,Amount,Currency\n"
           "2025-01-02,BUY,XEI,10,{p},-500.00,CAD\n"
           "2025-02-03,SELL,XEI,-10,{p},600.00,CAD\n")
GEN_TOML = ('[columns]\ndate = "Date"\naction = "Type"\nsymbol = "Ticker"\n'
            'quantity = "Qty"\nprice = "Price"\namount = "Amount"\n'
            'currency = "Currency"\n[actions]\n"BUY" = "buy"\n"SELL" = "sell"\n'
            '[defaults]\ncurrency = "CAD"\n')
GEN_SAID = ("Generic importer: generic_2025.csv: 2 trade rows state a price of "
            "0 with cash (the first: line 2)")
IB_STATEMENT = (
    HEAD + 'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    + TRADES_H + _trade("QZM", "2025-01-10, 10:00:00", 100, 10, -1000,
                        comm=-1, acct="U1234567")
    + CA_H + _ca("QZM(US9990000092) Merged(Acquisition) FOR USD 30.00 PER "
                 "SHARE (QZM, QZM CORP, US9990000092)", -100, proceeds=0))
IB_SAID = ("ib_2025.csv line 8 (Corporate Actions): QZM cash takeover: "
           "Proceeds is 0; booked at the description's 30.00 x 100 = "
           "3000.00 USD; check it against the cash received.")


def _project(td, files, currencies=(), accounts=("margin",)):
    root = Path(td)
    (root / "taxjson.toml").write_text(
        settings_for("canada", year=2025, source_currencies=list(currencies))
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    for acct in accounts:
        for name, text in files.items():
            p = root / "inputs" / acct / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
    return root


class TestTheMark(unittest.TestCase):

    def test_the_generic_price_zero_warning_is_marked(self):
        _, err, _ = _parse(GEN_CSV.format(p="0.00"), GEN_TOML)
        said = [ln for ln in err.splitlines() if "price of 0" in ln]
        self.assertEqual(len(said), 1, err)
        self.assertTrue(said[0].startswith(MARK + "generic importer: "))

    def test_a_person_is_shown_the_warning_without_it(self):
        line = MARK + "generic importer: g.csv: 2 trade rows state a price of 0"
        self.assertEqual(out.relabel(line),
                         "Warning: Generic importer: g.csv: 2 trade rows "
                         "state a price of 0")
        self.assertEqual(out.shown_topic("ATTENTION: " + out.END_OF_RUN_MARK
                                         + "ib.csv line 8: x"),
                         "ib.csv line 8: x")


class TestParserWarnings(unittest.TestCase):

    def test_read_back_from_the_diags_once_marked_lines_only(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "margin_ib.json.diag").write_text(
                "warning: ATTENTION: ib.csv: the statement has no Cash "
                "Report\n" + MARK + "ib.csv line 8: said\n"
                + MARK + "ib.csv line 8: said\n")
            (work / "tfsa_generic.json.diag").write_text(
                MARK + "generic importer: g.csv: said\n")
            (work / "margin_gains.traces").write_text(MARK + "not a diag\n")
            # An account named margin_us is not margin's; one not
            # configured is no one's.
            (work / "margin_us_generic.json.diag").write_text(
                MARK + "generic importer: g.csv: said\n")
            (work / "gone_generic.json.diag").write_text(MARK + "old\n")
            self.assertEqual(
                FR.parser_warnings(work, ["margin", "margin_us", "tfsa"]),
                [{"account": "margin", "text": "ib.csv line 8: said"},
                 {"account": "margin_us", "text": "generic importer: g.csv: said"},
                 {"account": "tfsa", "text": "generic importer: g.csv: said"}])
            self.assertEqual(FR.parser_warnings(work / "none", ["margin"]), [])

    def test_listed_in_both_views(self):
        doc = {"year": 2025, "parser_warnings": [
            {"account": "margin",
             "text": "generic importer: g.csv: 2 trade rows state a price of 0"}]}
        self.assertFalse(FR.is_clean(doc))
        want = ("Warning: [margin] Generic importer: g.csv: 2 trade rows state a "
                "price of 0")
        for concise in (False, True):
            lines = out.join_blocks(FR.render_blocks(doc, width_=0,
                                                     concise=concise))
            self.assertIn(want, lines)
        self.assertTrue(FR.is_clean({"year": 2025, "parser_warnings": []}))


class TestTheRunsList(unittest.TestCase):
    """`tjs run` on made-up projects: the warning under the closing
    heading, and in reports/run_summary.json; a run without one writes
    no key."""

    def _run(self, root):
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        # Captured (width 0: _style.CapturedWidth), the mark is in no
        # console line and no report (review round 1: it reached the
        # .sum DIAGNOSTICS and the width-0 console).
        self.assertNotIn(out.END_OF_RUN_MARK, r.stdout + r.stderr)
        for rep in (root / "reports").rglob("*"):
            if rep.is_file():
                self.assertNotIn(out.END_OF_RUN_MARK, rep.read_text(errors="replace"),
                                 rep.name)
        tail = r.stdout[r.stdout.index("Done. Reports"):]
        doc = json.loads((root / "reports" / FR.SUMMARY_FILE).read_text())
        return tail, doc

    def test_generic(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {
                "generic_2025.csv": GEN_CSV.format(p="0.00"),
                "generic_2025.csv.toml": GEN_TOML})
            tail, doc = self._run(root)
            block = tail[tail.index("==> Before you trust"):]
            self.assertIn("Warning: [margin] " + GEN_SAID, block)
            self.assertEqual(len(doc["parser_warnings"]), 1)
            self.assertEqual(doc["parser_warnings"][0]["account"], "margin")
            self.assertTrue(doc["parser_warnings"][0]["text"].startswith(
                "generic importer: generic_2025.csv: 2 trade rows"))
            # The .sum keeps the warning, unmarked.
            sums = (root / "reports" / "margin.sum").read_text()
            self.assertIn("warning: ATTENTION: generic importer: generic_2025.csv: "
                          "2 trade rows state a price of 0", sums)

    def test_two_accounts_one_file_name(self):
        # Review round 1: the same export name and line in two accounts
        # said one warning naming neither.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {
                "generic_2025.csv": GEN_CSV.format(p="0.00"),
                "generic_2025.csv.toml": GEN_TOML}, accounts=("margin", "cash"))
            tail, doc = self._run(root)
            block = tail[tail.index("==> Before you trust"):]
            for acct in ("margin", "cash"):
                self.assertIn(f"Warning: [{acct}] " + GEN_SAID, block)
            self.assertEqual([w["account"] for w in doc["parser_warnings"]],
                             ["margin", "cash"])

    def test_generic_with_nothing_to_say(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {
                "generic_2025.csv": GEN_CSV.format(p=""),
                "generic_2025.csv.toml": GEN_TOML})
            tail, doc = self._run(root)
            self.assertNotIn("price of 0", tail)
            self.assertNotIn("parser_warnings", doc)

    def test_ib_cash_takeover(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"ib_2025.csv": IB_STATEMENT},
                            currencies=("USD",))
            tail, doc = self._run(root)
            block = tail[tail.index("==> Before you trust"):]
            self.assertIn("Warning: [margin] " + IB_SAID, block)
            self.assertEqual(doc["parser_warnings"],
                             [{"account": "margin", "text": IB_SAID}])
            # The statement's other ATTENTION line is not listed (TJ-4730).
            self.assertNotIn("Cash Report", block)


if __name__ == "__main__":
    unittest.main()
