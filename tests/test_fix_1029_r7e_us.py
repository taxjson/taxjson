"""Regression tests for fix round 7 (wave E), group us: the US engine and
US output.

TJ-1261 the silent planning walk used up the engine's said-once notes.
TJ-1262 one `+?` cost turned `tjs list`'s money columns to text.
TJ-1263 `tjs events` sized its PRICE and TOTAL columns to corp= tokens.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import re
import tempfile
import unittest

from tax_rules.dual import tx
from test_fix_1029_r7d_us import _gains, _paid, _project, _tjs

import _qa_project as qa


def _short(date, cover, *, sym="ZQA.US", qty=100, proceeds=1000.0, cost=900.0,
           account="margin"):
    return [tx("BUYSELL", date, sym, -qty, proceeds, account=account),
            tx("BUYSELL", cover, sym, qty, cost, account=account)]


# --------------------------------------------------------------- TJ-1261
class TestSaidOnceNotesSurviveThePlan(unittest.TestCase):
    OPTION = ["BUYSELL 2025-01-20 10:00:00 ZOPT250321C00010000.US -2 USD 1 200 0",
              "BUYSELL 2025-02-03 10:00:00 ZOPT.US 200 USD 11 2200 0",
              "ASSIGN 2025-03-21 16:00:00 ZOPT250321C00010000.US 2 USD 0 0 0",
              "ASSIGN 2025-03-21 16:00:01 ZOPT.US -200 USD 10 2000 0"]
    PAID = ["BUYSELL 2025-01-06 10:00:00 ZQSH.US -100 USD 10 1000 0",
            "DIVIDEND_IN_LIEU 2025-01-15 16:00:00 ZQSH.US 0 USD 0 -20.00",
            "BUYSELL 2025-01-21 10:00:00 ZQSH.US 100 USD 9 900 0"]

    def _run(self, rows):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, rows, year=2025)
            _tjs(root, "run", "--no-input")
            diag = (root / "work" / "margin_gains.json.diag").read_text()
            carry = _tjs(root, "carryover").stderr
            return diag, carry

    def test_the_contract_size_note_is_said_once(self):
        for rows in (self.OPTION, self.OPTION + self.PAID):
            diag, carry = self._run(rows)
            self.assertEqual(diag.count("ZOPT options"), 1, rows)
            self.assertEqual(carry.count("ZOPT options"), 1, rows)

    def test_a_split_date_note_is_said_once(self):
        from taxjson.lib import corporate_timeline as ct
        from taxjson.lib import markets
        rows = [tx("BUYSELL", "2025-01-02", "ZQS.US", 100, 900.0),
                tx("SPLIT", "2025-02-03", "ZQS.US", 2.0, 0.0,
                   symbol_new="ZQS.US", account="margin"),
                tx("SPLIT", "2025-02-04", "ZQS.US", 2.0, 0.0,
                   symbol_new="ZQS.US"),
                *_short("2025-05-01", "2025-06-12", sym="ZQT.US"),
                _paid("2025-05-15", "ZQT.US", -20.0)]
        ct._SPLIT_DATE_NOTES.clear()
        markets.reset_notes()
        err = _gains(rows)["_stderr"]
        self.assertEqual(err.count("is booked on two dates"), 1, err)

    def test_notes_said_before_the_plan_stay_said(self):
        from taxjson.lib import markets
        from taxjson.lib.core import USATaxRules
        markets.reset_notes()
        self.addCleanup(markets.reset_notes)
        markets._NOTED.add(("earlier", "note", None))
        USATaxRules()._plan_paid_compensation(
            [*_short("2025-05-01", "2025-06-12"),
             _paid("2025-05-15", "ZQA.US", -20.0)], [], [])
        self.assertIn(("earlier", "note", None), markets._NOTED)
        self.assertEqual(len(markets._NOTED), 1)


# --------------------------------------------------------------- TJ-1262
class TestListKeepsMoneyRightAligned(unittest.TestCase):
    ROWS = ['OPENING 2024-12-31 ZZOP.US 10 cost=unknown reason="before the files"',
            "BUYSELL 2025-01-10 10:00:00 ZZC.US 100 USD 30 3000 0",
            "BUYSELL 2025-01-10 10:00:00 ZZD.US 33 USD 12.5 412.5 0"]

    def _list_lines(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = qa.project(td, "p", {"margin/trades.tt":
                                        "\n".join(self.ROWS) + "\n"},
                              country=country)
            qa.tj(root, "run", "--no-input")
            out = qa.tj(root, "list").stdout
        return [ln for ln in out.splitlines()
                if ln.startswith(("ACCOUNT", "margin"))]

    def test_both_countries(self):
        for country in ("usa", "canada"):
            lines = self._list_lines(country)
            head = lines[0]
            end = head.index("COST/SH") + len("COST/SH")
            cost_end = head.index("COST/SH") - 2
            for ln in lines[1:]:
                self.assertIn(ln[cost_end - 1], "0123456789?", (country, ln))
                self.assertIn(ln[end - 1], "0123456789?", (country, ln))
                self.assertEqual(ln[end:end + 1], " ", (country, ln))
            self.assertTrue(any("+?" in ln for ln in lines), lines)

    def test_a_cell_of_text_still_makes_a_text_column(self):
        from taxjson.lib.report_model import auto_aligns
        self.assertEqual(auto_aligns(["A"], [["0.00+?"], ["1,234.50"]]), [">"])
        self.assertEqual(auto_aligns(["A"], [["+?"], ["1,234.50"]]), ["<"])
        self.assertEqual(auto_aligns(["A"], [["0.00?"], ["1,234.50"]]), ["<"])
        self.assertEqual(auto_aligns(["A"], [["ZZ+?"], ["1,234.50"]]), ["<"])


# --------------------------------------------------------------- TJ-1263
SPIN_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Statement,Data,Title,Activity Statement
Statement,Data,Period,"January 1, 2025 - December 31, 2025"
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,USD,2025-02-03,"2025-02-03, 10:00:00","ZZQQ(US0000000921) Spinoff 1 for 1 (ZZKK, ZZKK INC, US0000000922)",100,0,100,0,
"""


class TestEventsTrailingTokens(unittest.TestCase):
    ROWS = ["BUYSELL 2025-01-17 10:00:00 ZZQQ.US 100 USD 2 200 0",
            "BUYSELL 2025-03-03 10:00:00 ZZQQ.US -100 USD 1 100 0",
            "BUYSELL 2025-03-04 10:00:00 ZZKK.US -100 USD 1 100 0"]

    def _events(self, *view):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, self.ROWS, year=2025, csv=SPIN_CSV)
            first = _tjs(root, "run", "--no-input", ok=False)
            m = re.search(r"20250203-zzqq-zzkk-[0-9a-f]+",
                          first.stdout + first.stderr)
            self.assertIsNotNone(m, first.stderr[-1500:])
            _tjs(root, "elect", "margin", "--set",
                 f"{m.group(0)}=tax_free_355",
                 "--hint", "allocated_acb=90")
            _tjs(root, "run", "--no-input")
            return _tjs(root, "events", *view).stdout

    def test_the_tokens_do_not_size_the_numbers(self):
        out = self._events()
        rows = [ln for ln in out.splitlines() if " ZZ" in ln]
        self.assertTrue(any("corp=" in ln for ln in rows), out)
        self.assertTrue(any("election=tax_free_355" in ln for ln in rows), out)
        plain = next(ln for ln in rows
                     if " ZZQQ.US 100 " in ln and "corp=" not in ln)
        self.assertLess(len(plain), 80, plain)
        self.assertRegex(plain, r"USD +2 +200\.00 +0\.00$")

    def test_the_tokens_start_in_one_column(self):
        out = self._events()
        starts = {ln.index(" corp=") for ln in out.splitlines()
                  if " corp=" in ln}
        self.assertEqual(len(starts), 1, out)

    def test_view_helper_keeps_every_token(self):
        from taxjson.bin.taxjson_run import _align_tx_lines
        lines = ["BUYSELL 2025-01-17 10:00:00 ZQ.US 100 USD 2 200.00 0.00",
                 "BUYSELL 2025-02-03 10:00:00 ZK.US 100 USD 0.9 90.00 0.00 "
                 "corp=a election=b",
                 "ADJUST 2025-02-03 10:00:00 ZQ.US USD -90.00 corp=a",
                 "BUYSELL 2025-03-03 10:00:00 ES=F.US 1 USD 1 1.00 0.00 x50",
                 "ASSIGN 2025-03-03 10:00:00 ZW.US 1 USD 1 1.00 0.00 x2 "
                 "exercise_of=ZW"]
        out = _align_tx_lines(lines)
        self.assertEqual([ln.split() for ln in out],
                         [ln.split() for ln in lines])

    def test_no_tokens_is_the_old_layout(self):
        from taxjson.bin.taxjson_run import _align_tx_lines
        from taxjson.lib.report_model import align_columns
        lines = ["BUYSELL 2025-01-17 10:00:00 ZQ.US 100 USD 2 200.00 0.00",
                 "ADJUST 2025-02-03 10:00:00 ZQ.US USD -90.00",
                 "BUYSELL 2025-03-03 10:00:00 ES=F.US 1 USD 12.5 1.00 0.00"]
        self.assertEqual(_align_tx_lines(lines),
                         align_columns(lines, numeric_right=True, header=False))

    def test_a_pasted_listing_reads_back(self):
        from taxjson.bin.taxjson_run import _align_tx_lines
        from taxjson.bin import taxjson_convert_tt as C
        lines = ["BUYSELL 2025-02-03 10:00:00 ZK.US 100 USD 0.9 90.00 0.00 "
                 "corp=ev-1 election=tax_free_355",
                 "ADJUST 2025-02-03 10:00:00 ZQ.US USD -90.00 corp=ev-1 "
                 "election=tax_free_355"]
        self.assertEqual([C.parse_tt_line(ln) for ln in lines],
                         [C.parse_tt_line(ln)
                          for ln in _align_tx_lines(lines)])


if __name__ == "__main__":
    unittest.main()
