"""TJ-5011: an IB return of capital on a listing no dated row of its
statement shows held, while the account's exports name another listing.

An IB income row names its security by ISIN, and the parse books it on
the listing its statement shows held that day. Where the statement does
not show that (the income exported apart from the trades; a statement
that trades one listing only later; a statement with no Open Positions
whose purchase is in another year's), the row is on the listing its ISIN
names, which may hold nothing that day: the engine then books the
payment as a capital gain (ITA s.40(3)) while the listing that was held
keeps its cost.

What the run does (the owner's rule: nothing silent that can be wrong):

- the parser's note says what is known (the account "may have held" the
  other listing);
- a cost row is never moved off its ISIN listing onto a listing the
  statement's own rows show not held that day (a figure fix: partial2);
- `tjs run` lists an ATTENTION for each such cost row wherever it landed:
  the payment and the listing it may belong to first, then what the books
  did with it (the cost reduced, the part booked as a gain), the remedy
  last; `tjs run --strict` stops, naming the row; the closing list and
  the checklist's run step repeat it; `tjs run --account` checks it too;
- one payment two overlapping statements put on two listings is booked
  once, or refused where nothing says which listing is right;
- nothing is said where one listing only ever appears, where the
  statement's dated rows place the row, or where ticker.map books the
  listings as one pool.

The split exports' figures are as they were (carrying the other exports'
dates is TJ-0669, still open).

Made-up ticker (QZRX), ISIN and account id; small made-up amounts.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from tax_rules import rule
from tax_rules.dual import SRC, cli, projects_both
from taxjson.lib.stage_msg import (UNDATED_LISTING, UNSHOWN_LISTING,
                                   parse_undated_listing_note,
                                   undated_listing_note)
from taxjson.rules import ids as R

_ISIN = 'CA9990000001'


def _head(period='January 1, 2025 - December 31, 2025'):
    return ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Statement,Data,Title,Activity Statement\n'
            f'Statement,Data,Period,"{period}"\n'
            'Account Information,Header,Field Name,Field Value\n'
            'Account Information,Data,Account,U1234567\n')


_HEAD = _head()
_T_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,'
        'Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,'
        'Realized P/L,MTM P/L,Code\n')
_OP_H = ('Open Positions,Header,DataDiscriminator,Asset Category,Currency,'
         'Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,'
         'Unrealized P/L,Code\n')
_DIV_H = 'Dividends,Header,Currency,Date,Description,Amount\n'


def _roc(amt=5, isin=_ISIN, what='Return of Capital'):
    return (f'Dividends,Data,USD,2025-01-10,"QZRX({isin}) {what} '
            f'USD 1 per Share",{amt}\n')


def _op(cur, q):
    return f'Open Positions,Data,Summary,Stocks,{cur},QZRX,{q},1,10,{q * 10},10,{q * 10},0,\n'


def _tr(day, q, cur, code, px=10):
    return (f'Trades,Data,Order,Stocks,{cur},QZRX,"{day}, 10:00:00",{q},{px},'
            f'{px},{-px * q},0,{px * q},0,0,{code}\n')


_ROC = _roc()
_BUY_US = _tr('2025-01-02', 5, 'USD', 'O')
_FEB_TO = _tr('2025-02-03', 5, 'CAD', 'O') + _tr('2025-02-20', -5, 'CAD', 'C')
_FEB_US = _tr('2025-02-03', 5, 'USD', 'O') + _tr('2025-02-20', -5, 'USD', 'C')
# Three exports: the January 2 purchase, the February trades, the income.
_SPLIT = {'a_buys.csv': _HEAD + _T_H + _BUY_US,
          'b_feb.csv': _HEAD + _T_H + _FEB_TO,
          'c_income.csv': _HEAD + _DIV_H + _ROC}
_COMBINED = {'all.csv': _HEAD + _T_H + _BUY_US + _FEB_TO + _DIV_H + _ROC}
_ONE_LISTING = dict(_SPLIT, **{'b_feb.csv': _HEAD + _T_H + _FEB_US})
# Both listings held on the payment date: QZRX.TO bought January 3, kept.
_BOTH_HELD = dict(_SPLIT, **{
    'b_feb.csv': _HEAD + _T_H + _tr('2025-01-03', 5, 'CAD', 'O')})
# The income's statement trades QZRX.TO too, opened February 3; the
# QZRX.US purchase is in the other export.
_PARTIAL = {'a_buys.csv': _HEAD + _T_H + _BUY_US,
            'c_income.csv': _HEAD + _T_H + _FEB_TO + _DIV_H + _ROC}
# The other way: the income's statement trades QZRX.US, opened February
# 3; QZRX.TO is held from January 3 in the other export.
_PARTIAL2 = {'a_buys.csv': _HEAD + _T_H + _tr('2025-01-03', 5, 'CAD', 'O'),
             'c_income.csv': _HEAD + _T_H + _FEB_US + _DIV_H + _ROC}
# The purchase in the 2024 statement; one 2025 statement holding the
# payment and the year's trades, with no Open Positions section.
_Y2024 = (_head('January 1, 2024 - December 31, 2024') + _T_H
          + _tr('2024-12-02', 5, 'USD', 'O'))
_REM_AFTER = {'y2024.csv': _Y2024,
              'y2025.csv': _HEAD + _T_H + _FEB_TO + _DIV_H + _ROC}
_REM_AFTER_OP = {'y2024.csv': _Y2024,
                 'y2025.csv': (_HEAD + _T_H + _FEB_TO + _OP_H + _op('USD', 5)
                               + _DIV_H + _ROC)}
_FIX = ("Download a statement that holds the account's Open Positions "
        "section, or every trade of the ticker from before the holding's "
        "purchase, and keep it in place of the split exports; or, if the "
        "listings are one security, add `TOBASE QZRX.US QZRX.TO` to "
        "ticker.map (one cost pool).")
_SPLIT_WHY = ("The statement has no position in the ticker, and the "
              "account's other exports name its listings with no dates the "
              "statement can use, so the row kept the listing its ISIN "
              "names.")
_HEADLINE = ("warning: ATTENTION: undated listing: return of capital "
             "2025-01-10 on QZRX.TO may belong to QZRX.US (margin: "
             "c_income.csv line 8).")
_IF_US = ("If QZRX.US was the listing held, its cost is too high by 6.75 "
          "CAD and what is booked on QZRX.TO is not real.")
_GAIN = (f"{_HEADLINE} The books took it as a capital gain of 6.75 CAD: "
         f"QZRX.TO had no cost to reduce that day. {_SPLIT_WHY} {_IF_US} "
         f"{_FIX}")
_HELD = (f"{_HEADLINE} The books reduced the cost of QZRX.TO by 6.75 CAD. "
         f"{_SPLIT_WHY} {_IF_US} {_FIX}")
_STRICT = ("--strict: 1 cost row(s) on a listing no dated row shows held, "
           "where the account's exports name another (c_income.csv line 8: "
           "return of capital of 2025-01-10 on QZRX.TO) — aborting")
_CHECKLIST = ("1 cost row(s) on a listing no dated row shows held, where the "
              "account's exports name another (return of capital 2025-01-10 "
              "on QZRX.TO may belong to QZRX.US (margin: c_income.csv line "
              "8))")


def _flat(text):
    return " ".join(text.split())


class _Case(unittest.TestCase):
    def _project(self, files, extra=None, **kw):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return projects_both(td.name, files={
            **{f"inputs/margin/{n}": t for n, t in files.items()},
            **(extra or {})}, **kw)["canada"]

    def _run(self, files, extra=None, **kw):
        root = self._project(files, extra, **kw)
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        doc = json.loads((root / "work" / "margin_gains_wash.json").read_text())
        return root, _flat(r.stdout + r.stderr), doc

    @staticmethod
    def _deemed(doc):
        return [(g["symbol"], g["date"], g["gain"])
                for g in doc["transactions"] if g.get("deemed")]

    @staticmethod
    def _held(doc):
        return sorted((h["symbol"], h["qty"], h["total_cost"])
                      for h in doc["inventory"])

    @staticmethod
    def _roc_listing(root, book="ib"):
        doc = json.loads((root / "work" / f"margin_{book}.json").read_text())
        return [t["symbol"] for t in doc["transactions"]
                if t["action"] == "ADJUST"]

    @staticmethod
    def _said(text):
        return text.count("ATTENTION: undated listing:")


class TestSplitExports(_Case):
    @rule(R.CA_ACB_07)
    def test_the_gain_on_the_empty_listing_is_listed(self):
        root, said, doc = self._run(_SPLIT)
        # The figures are the split exports' as before: the payment a
        # gain on QZRX.TO (5 USD at the synthetic 1.35), QZRX.US at cost.
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._deemed(doc), [("QZRX.TO", "2025-01-10", 6.75)])
        self.assertEqual(self._held(doc), [("QZRX.US", 5.0, 67.5)])
        # After the split, and again in the closing list.
        self.assertEqual(self._said(said), 1, said)
        self.assertIn(_GAIN, said)
        self.assertIn("Warning: [margin] Undated listing: return of capital "
                      "2025-01-10 on QZRX.TO may belong to QZRX.US", said)
        # The wash twin's banner keeps it, with the parser's note: what
        # is known is that the account may have held the other listing.
        wash = _flat((root / "reports" / "margin_wash.sum").read_text())
        self.assertIn(_GAIN, wash)
        self.assertIn(
            "note: c_income.csv line 8: return of capital on QZRX.TO of "
            "2025-01-10 kept its ISIN listing: the account's other exports "
            "name more than one listing of the ticker (QZRX.TO, QZRX.US) "
            "with no dates this statement can use, so the account may have "
            "held QZRX.US that day instead", wash)
        self.assertNotIn("the account held more than one listing", wash)
        self.assertEqual(
            (root / "work" / "margin_listing_evidence.diag").read_text(),
            _GAIN + "\n")

    @rule(R.CA_ACB_07)
    def test_the_checklist_lists_it_and_strict_stops_naming_the_row(self):
        root, _said, _doc = self._run(_SPLIT)
        ck = _flat((lambda r: r.stdout + r.stderr)(cli(root, "checklist", "--run")))
        self.assertIn(_CHECKLIST, ck)
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 1, (r.stdout + r.stderr)[-2000:])
        self.assertIn("tjs run: error: " + _STRICT, _flat(r.stdout + r.stderr))

    @rule(R.CA_ACB_07)
    def test_a_tobase_line_is_the_remedy_the_attention_names(self):
        # One security on two listings: one pool, the cost reduced there.
        root, said, doc = self._run(
            _SPLIT, {"ticker.map": "TOBASE QZRX.US QZRX.TO\n"})
        self.assertNotIn("undated listing", said)
        self.assertEqual(self._deemed(doc), [])
        self.assertFalse((root / "work" / "margin_listing_evidence.diag").exists())

    @rule(R.CA_ACB_06)
    def test_a_listing_that_holds_units_that_day_is_listed_too(self):
        # Both listings held on the payment date: the cost reduced is
        # QZRX.TO's (50.00 less 6.75), and it may be QZRX.US's (TJ-5201).
        root, said, doc = self._run(_BOTH_HELD)
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._deemed(doc), [])
        self.assertEqual(self._held(doc), [("QZRX.TO", 5.0, 43.25),
                                           ("QZRX.US", 5.0, 67.5)])
        self.assertEqual(self._said(said), 1, said)
        self.assertIn(_HELD, said)
        self.assertIn(_HELD, _flat((root / "reports" / "margin_wash.sum").read_text()))
        ck = _flat((lambda r: r.stdout + r.stderr)(cli(root, "checklist", "--run")))
        self.assertIn(_CHECKLIST, ck)
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 1, (r.stdout + r.stderr)[-2000:])
        self.assertIn("tjs run: error: " + _STRICT, _flat(r.stdout + r.stderr))
        # One pool by the map: nothing to say.
        (root / "ticker.map").write_text("TOBASE QZRX.US QZRX.TO\n")
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        self.assertNotIn("undated listing", r.stdout + r.stderr)

    @rule(R.CA_ACB_07)
    def test_run_account_checks_it_and_the_checklist_reads_it(self):
        # No pass across accounts under --account (TJ-5202); the
        # checklist's run step reads the account's own record, so it is
        # not clean while --strict would stop.
        for files, line in ((_SPLIT, _GAIN), (_BOTH_HELD, _HELD)):
            with self.subTest(line=line[150:200]):
                root = self._project(files)
                r = cli(root, "run", "--no-input", "--account", "margin")
                self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
                said = _flat(r.stdout + r.stderr)
                self.assertEqual(self._said(said), 1, said)
                self.assertIn(line, said)
                self.assertEqual(
                    (root / "work" / "margin_listing_evidence.diag").read_text(),
                    line + "\n")
                ck = _flat((lambda r: r.stdout + r.stderr)(
                    cli(root, "checklist", "--run")))
                self.assertIn(_CHECKLIST, ck)
                r = cli(root, "run", "--no-input", "--account", "margin",
                        "--strict")
                self.assertEqual(r.returncode, 1, (r.stdout + r.stderr)[-2000:])
                self.assertIn("tjs run: error: " + _STRICT,
                              _flat(r.stdout + r.stderr))
        root = self._project(_ONE_LISTING)
        r = cli(root, "run", "--no-input", "--account", "margin", "--strict")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        self.assertNotIn("undated listing", r.stdout + r.stderr)

    def test_run_account_beside_another_taxable_account_claims_no_figure(self):
        # Canada pools cost across the taxable accounts: the account's own
        # books are not the filed ones, so --account says the row and not
        # what its own books did; the full run says what the pooled books
        # did (the other account holds QZRX.TO that day).
        root = self._project(
            _SPLIT, {"inputs/other/h.tt":
                     "BUYSELL 2025-01-03 10:00:00 QZRX.TO 5 CAD 10 50 0\n"},
            accounts='[accounts.margin]\ntype = "taxable"\n'
                     '[accounts.other]\ntype = "taxable"\n')
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        self.assertIn(_HELD, _flat(r.stdout + r.stderr))
        r = cli(root, "run", "--no-input", "--account", "margin")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        said = _flat(r.stdout + r.stderr)
        self.assertIn(f"{_HEADLINE} The books that pool the taxable accounts "
                      f"say what was done with it: a full `tjs run`. "
                      f"{_SPLIT_WHY}", said)
        self.assertNotIn("capital gain of", said)

    def test_a_later_run_without_the_row_removes_the_record(self):
        root, _said, _doc = self._run(_SPLIT)
        diag = root / "work" / "margin_listing_evidence.diag"
        self.assertTrue(diag.exists())
        (root / "ticker.map").write_text("TOBASE QZRX.US QZRX.TO\n")
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        self.assertFalse(diag.exists())

    @rule(R.CA_ACB_07)
    def test_a_later_year_says_what_that_years_books_did(self):
        # The 2026 project's gains hold no 2025 row: the words come from
        # the engine's cost timeline, which holds every year.
        root, said, doc = self._run(_SPLIT, year=2026)
        self.assertEqual(self._deemed(doc), [])
        self.assertEqual(self._said(said), 1, said)
        self.assertIn(_GAIN, said)

    @rule(R.CA_ACB_07)
    def test_two_rows_of_one_day_are_two_attentions(self):
        files = dict(_SPLIT, **{'c_income.csv': (
            _HEAD + _DIV_H + _ROC + _roc(amt=3)
            + _roc(amt=2, what='Cash Dividend'))})
        root, said, doc = self._run(files)
        self.assertEqual(self._deemed(doc), [("QZRX.TO", "2025-01-10", 6.75),
                                             ("QZRX.TO", "2025-01-10", 4.05)])
        self.assertEqual(self._said(said), 2, said)
        self.assertIn("(margin: c_income.csv line 8). The books took it as a "
                      "capital gain of 6.75 CAD", said)
        self.assertIn("(margin: c_income.csv line 9). The books took it as a "
                      "capital gain of 4.05 CAD", said)
        # (the dividend of line 10 changes no cost: the note only)
        self.assertNotIn("c_income.csv line 10).", said)
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 1, (r.stdout + r.stderr)[-2000:])
        self.assertIn(
            "--strict: 2 cost row(s) on a listing no dated row shows held, "
            "where the account's exports name another (c_income.csv line 8: "
            "return of capital of 2025-01-10 on QZRX.TO; c_income.csv line "
            "9: return of capital of 2025-01-10 on QZRX.TO) — aborting",
            _flat(r.stdout + r.stderr))

    @rule(R.CA_ACB_07)
    def test_a_payment_beyond_the_cost_says_both_parts(self):
        # One QZRX.TO unit at 1.00 CAD held: 1.00 off the cost, the rest
        # (6.75 less 1.00) a gain.
        files = dict(_SPLIT, **{
            'b_feb.csv': _HEAD + _T_H + _tr('2025-01-03', 1, 'CAD', 'O', px=1)})
        _root, said, doc = self._run(files)
        self.assertEqual(self._deemed(doc), [("QZRX.TO", "2025-01-10", 5.75)])
        self.assertIn(f"{_HEADLINE} The books reduced the cost of QZRX.TO by "
                      f"1.00 CAD, all it had, and took the other 5.75 CAD as "
                      f"a capital gain. {_SPLIT_WHY}", said)

    @rule(R.CA_ACB_07)
    def test_a_renamed_listing_is_named_as_the_books_hold_it(self):
        _root, said, doc = self._run(
            _SPLIT, {"ticker.map": "GLOBAL QZRX.TO QZNEW.TO\n"})
        self.assertEqual(self._deemed(doc), [("QZNEW.TO", "2025-01-10", 6.75)])
        self.assertIn(
            "warning: ATTENTION: undated listing: return of capital "
            "2025-01-10 on QZNEW.TO (QZRX.TO in the statement) may belong to "
            "QZRX.US (margin: c_income.csv line 8). The books took it as a "
            "capital gain of 6.75 CAD: QZNEW.TO had no cost to reduce that "
            "day.", said)

    @rule(R.CA_ACB_08)
    def test_a_foreign_issuers_return_of_capital_is_named_as_one(self):
        # A US ISIN, booked against the cost (foreign_return_of_capital =
        # "acb"): QZRX.TO held from January, QZRX.US traded in February.
        files = {'a_buys.csv': _HEAD + _T_H + _tr('2025-01-02', 5, 'CAD', 'O'),
                 'b_feb.csv': _HEAD + _T_H + _FEB_US,
                 'c_income.csv': _HEAD + _DIV_H + _roc(isin='US9990000001')}
        root, said, _doc = self._run(
            files, canada={"foreign_return_of_capital": "acb"})
        self.assertIn(
            "warning: ATTENTION: undated listing: return of capital "
            "2025-01-10 on QZRX.US may belong to QZRX.TO (margin: "
            "c_income.csv line 8).", said)
        self.assertIn("note: c_income.csv line 8: return of capital on "
                      "QZRX.US of 2025-01-10 kept its ISIN listing",
                      _flat((root / "reports" / "margin_wash.sum").read_text()))


class TestAUsProject(unittest.TestCase):
    @rule(R.US_ROC_03)
    def test_the_row_the_us_engine_did_not_apply_is_said_as_such(self):
        # No lots on QZRX.TO that day: the US engine books nothing (its
        # own Unapplied basis adjustment warning); the row is listed with
        # the listing it may belong to, in USD.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = projects_both(
            td.name, usa={"source_currencies": ["CAD"]},
            files={f"inputs/margin/{n}": t for n, t in _SPLIT.items()})["usa"]
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        said = _flat(r.stdout + r.stderr)
        self.assertIn(
            f"{_HEADLINE} The books did not apply it: QZRX.TO held nothing "
            f"that day (the Unapplied basis adjustment warning). "
            f"{_SPLIT_WHY} If QZRX.US was the listing held, its cost is too "
            f"high by 5.00 USD and what is booked on QZRX.TO is not real.",
            said)
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 1, (r.stdout + r.stderr)[-2000:])
        self.assertIn("tjs run: error: " + _STRICT, _flat(r.stdout + r.stderr))


    @rule(R.US_ROC_01)
    def test_the_row_the_us_engine_applied_says_the_basis_it_took(self):
        # QZRX.TO held from January 3 (50 CAD at the synthetic 0.7407:
        # 37.035 USD): the lots' basis less 5.00.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = projects_both(
            td.name, usa={"source_currencies": ["CAD"]},
            files={f"inputs/margin/{n}": t for n, t in _BOTH_HELD.items()})["usa"]
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        self.assertIn(
            f"{_HEADLINE} The books reduced the cost of QZRX.TO by 5.00 USD. "
            f"{_SPLIT_WHY}", _flat(r.stdout + r.stderr))
        doc = json.loads((root / "work" / "margin_gains_wash.json").read_text())
        self.assertIn(("QZRX.TO", 5.0, 32.035),
                      [(h["symbol"], h["qty"], round(h["total_cost"], 3))
                       for h in doc["inventory"]])


class TestAStatementThatDoesNotShowTheListingHeld(_Case):
    """The reviewer's shapes beside the split one (P1-1, P1-2)."""

    @rule(R.CA_ACB_07)
    def test_one_statement_with_no_open_positions_the_purchase_a_year_back(self):
        # The split exports' remedy half followed: silent before.
        root, said, doc = self._run(_REM_AFTER)
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._deemed(doc), [("QZRX.TO", "2025-01-10", 6.75)])
        self.assertEqual(self._said(said), 1, said)
        self.assertIn(
            "warning: ATTENTION: undated listing: return of capital "
            "2025-01-10 on QZRX.TO may belong to QZRX.US (margin: y2025.csv "
            "line 11). The books took it as a capital gain of 6.75 CAD: "
            "QZRX.TO had no cost to reduce that day. No dated row of the "
            "statement shows QZRX.TO held that day, and the account's "
            f"exports also name QZRX.US. {_IF_US} {_FIX}", said)
        self.assertIn(
            "note: y2025.csv line 11: return of capital on QZRX.TO of "
            "2025-01-10 is on a listing no dated row of this statement shows "
            "held that day, and the account's exports name another listing "
            "of the ticker (QZRX.TO, QZRX.US), so the account may have held "
            "QZRX.US that day instead (a statement with the account's Open "
            "Positions section, or every trade of the ticker since before "
            "the purchase, places it).",
            _flat((root / "reports" / "margin_wash.sum").read_text()))

    @rule(R.CA_ACB_06)
    def test_the_same_statement_with_its_open_positions_places_it(self):
        root, said, doc = self._run(_REM_AFTER_OP)
        self.assertEqual(self._roc_listing(root), ["QZRX.US"])
        self.assertNotIn("undated listing", said)
        self.assertEqual(self._deemed(doc), [])
        self.assertEqual(self._held(doc), [("QZRX.US", 5.0, 60.75)])

    @rule(R.CA_ACB_07)
    def test_the_incomes_statement_trades_its_listing_only_later(self):
        root, said, doc = self._run(_PARTIAL)
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._deemed(doc), [("QZRX.TO", "2025-01-10", 6.75)])
        self.assertEqual(self._said(said), 1, said)
        self.assertIn(
            "warning: ATTENTION: undated listing: return of capital "
            "2025-01-10 on QZRX.TO may belong to QZRX.US (margin: "
            "c_income.csv line 11). The books took it as a capital gain of "
            "6.75 CAD", said)

    @rule(R.CA_ACB_06)
    def test_a_cost_row_is_not_moved_onto_a_listing_opened_later(self):
        # The figure fix: the CA-ISIN row went onto QZRX.US, which the
        # statement's own rows open on February 3 (a 6.75 gain there);
        # it stays on QZRX.TO, held from January 3 by the other export:
        # 50.00 less 6.75, no gain. Said, as no dated row shows it.
        root, said, doc = self._run(_PARTIAL2)
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._deemed(doc), [])
        self.assertEqual(self._held(doc), [("QZRX.TO", 5.0, 43.25)])
        self.assertEqual(self._said(said), 1, said)
        self.assertIn(
            "warning: ATTENTION: undated listing: return of capital "
            "2025-01-10 on QZRX.TO that no dated row shows held (margin: "
            "c_income.csv line 11). The books reduced the cost of QZRX.TO by "
            "6.75 CAD. No dated row of the statement shows QZRX.TO held that "
            "day, and the account's exports also name QZRX.US (the "
            "statement's own rows show QZRX.US not held that day). If "
            "QZRX.TO was not held that day, what is booked on it is not "
            f"real. {_FIX}", said)

    @rule(R.CA_ACB_06)
    def test_a_listing_another_years_statement_holds_still_takes_the_row(self):
        # QZRX.US bought in the 2024 statement and added to on February 3
        # in a 2025 statement with no Open Positions: its rows start with
        # a purchase, but the other statement names the listing, so they
        # do not show it flat in January. The row moves there as before:
        # 10 units, 135.00 less 6.75; nothing to say.
        files = {'y2024.csv': _Y2024,
                 'y2025.csv': (_HEAD + _T_H + _tr('2025-02-03', 5, 'USD', 'O')
                               + _DIV_H + _ROC)}
        root, said, doc = self._run(files)
        self.assertEqual(self._roc_listing(root), ["QZRX.US"])
        self.assertEqual(self._deemed(doc), [])
        self.assertEqual(self._held(doc), [("QZRX.US", 10.0, 128.25)])
        self.assertNotIn("undated listing", said)

    @rule(R.CA_ACB_06)
    def test_another_statement_that_opens_the_listing_later_does_not_take_it(self):
        # As partial2, and a third statement first buys QZRX.US in March,
        # coded an opening: no room for a January holding. The row stays
        # on QZRX.TO (50.00 less 6.75), said.
        files = dict(_PARTIAL2, **{'d_mar.csv': (
            _HEAD + _T_H + _tr('2025-03-03', 5, 'USD', 'O')
            + _tr('2025-03-20', -5, 'USD', 'C'))})
        root, said, doc = self._run(files)
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._deemed(doc), [])
        self.assertEqual(self._held(doc), [("QZRX.TO", 5.0, 43.25)])
        self.assertEqual(self._said(said), 1, said)
        self.assertIn("return of capital 2025-01-10 on QZRX.TO that no dated "
                      "row shows held (margin: c_income.csv line 11). The "
                      "books reduced the cost of QZRX.TO by 6.75 CAD.", said)

    @rule(R.CA_ACB_06)
    def test_another_broker_accounts_statement_does_not_take_it(self):
        # The March QZRX.US trades are another broker account's, in the
        # same folder: they say nothing of this account's holdings.
        other = (_HEAD.replace('U1234567', 'U' + '9' * 7) + _T_H   # (made up)
                 + _tr('2025-02-01', 5, 'USD', '') + _tr('2025-03-20', -5, 'USD', 'C'))
        root, said, doc = self._run(dict(_PARTIAL2, **{'e_other.csv': other}))
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._deemed(doc), [])
        self.assertIn(("QZRX.TO", 5.0, 43.25), self._held(doc))
        self.assertEqual(self._said(said), 1, said)

    @rule(R.CA_ACB_07)
    def test_no_export_naming_the_isins_listing_is_not_silent(self):
        # No export names QZRX.TO; the income's statement opens QZRX.US on
        # February 3 and another only trades it in March. The row went
        # onto QZRX.US, a 6.75 gain on a pool its statement opens later,
        # with nothing said; it stays on its ISIN's listing, said.
        files = {'c_income.csv': _PARTIAL2['c_income.csv'],
                 'd_mar.csv': (_HEAD + _T_H + _tr('2025-03-03', 5, 'USD', 'O')
                               + _tr('2025-03-20', -5, 'USD', 'C'))}
        root, said, doc = self._run(files)
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._deemed(doc), [("QZRX.TO", "2025-01-10", 6.75)])
        self.assertEqual(self._said(said), 1, said)
        self.assertIn(
            "warning: ATTENTION: undated listing: return of capital "
            "2025-01-10 on QZRX.TO that no dated row shows held (margin: "
            "c_income.csv line 11). The books took it as a capital gain of "
            "6.75 CAD: QZRX.TO had no cost to reduce that day. No dated row "
            "of the statement shows QZRX.TO held that day, and the account's "
            "exports also name QZRX.US (the statement's own rows show "
            "QZRX.US not held that day).", said)

    @rule(R.CA_ACB_07)
    def test_a_moved_row_the_books_find_nothing_under_is_listed(self):
        # No export names QZRX.TO; another statement's first QZRX.US row
        # is an uncoded purchase on March 3 (units may be older: room), so
        # the row moves to QZRX.US, where the books hold nothing on
        # January 10: a 6.75 gain, said with the ISIN's listing as where
        # it may belong.
        files = {'c_income.csv': _HEAD + _DIV_H + _ROC,
                 'd_mar.csv': _HEAD + _T_H + _tr('2025-03-03', 5, 'USD', '')}
        root, said, doc = self._run(files)
        self.assertEqual(self._roc_listing(root), ["QZRX.US"])
        self.assertEqual(self._deemed(doc), [("QZRX.US", "2025-01-10", 6.75)])
        self.assertEqual(self._said(said), 1, said)
        self.assertIn(
            "warning: ATTENTION: undated listing: return of capital "
            "2025-01-10 on QZRX.US may belong to QZRX.TO (margin: "
            "c_income.csv line 8). The books took it as a capital gain of "
            "6.75 CAD: QZRX.US had no cost to reduce that day. The row was "
            "moved off its ISIN's listing QZRX.TO: the account's statements "
            "hold or trade QZRX.US, and no dated row shows it held that day. "
            "If QZRX.TO was the listing held, its cost is too high by 6.75 "
            "CAD and what is booked on QZRX.US is not real.", said)

    @rule(R.CA_ACB_07)
    def test_a_transfer_in_after_the_payment_is_no_room(self):
        # The units were not in the account on January 10: the row stays
        # on its ISIN's listing, said.
        xfer = (_HEAD + 'Transfers,Header,Asset Category,Currency,Symbol,Date,'
                'Type,Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
                'Market Value,Realized P/L,Cash Amount,Code\n'
                'Transfers,Data,Stocks,USD,QZRX,2025-03-03,ACATS,In,OTHER,'
                '99900099,5,10,50,0,0,\n')
        root, said, doc = self._run({'c_income.csv': _HEAD + _DIV_H + _ROC,
                                     'd_mar.csv': xfer})
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._deemed(doc), [("QZRX.TO", "2025-01-10", 6.75)])
        self.assertEqual(self._said(said), 1, said)
        self.assertIn("return of capital 2025-01-10 on QZRX.TO may belong to "
                      "QZRX.US (margin: c_income.csv line 8). The books took "
                      "it as a capital gain of 6.75 CAD", said)

    @rule(R.CA_ACB_07)
    def test_open_positions_above_the_rows_net_are_room(self):
        # Five bought February 3, coded O (IB codes an addition so), and
        # ten in the Open Positions: older units, proven. The row moves
        # to QZRX.US; the books hold none of the older units, so it is a
        # gain there, said.
        trades = (_HEAD + _T_H + _tr('2025-02-03', 5, 'USD', 'O')
                  + _OP_H + _op('USD', 10))
        root, said, doc = self._run({'c_income.csv': _HEAD + _DIV_H + _ROC,
                                     'd_trades.csv': trades})
        self.assertEqual(self._roc_listing(root), ["QZRX.US"])
        self.assertEqual(self._deemed(doc), [("QZRX.US", "2025-01-10", 6.75)])
        self.assertIn("return of capital 2025-01-10 on QZRX.US may belong to "
                      "QZRX.TO (margin: c_income.csv line 8).", said)
        # Without the Open Positions the coded opening is no room.
        root, said, doc = self._run({
            'c_income.csv': _HEAD + _DIV_H + _ROC,
            'd_trades.csv': _HEAD + _T_H + _tr('2025-02-03', 5, 'USD', 'O')})
        self.assertEqual(self._roc_listing(root), ["QZRX.TO"])
        self.assertEqual(self._said(said), 1, said)

    def test_a_dividend_is_still_moved_to_the_statements_listing(self):
        # Only a cost row stays: a dividend changes no cost.
        files = dict(_PARTIAL2, **{'c_income.csv': (
            _HEAD + _T_H + _FEB_US + _DIV_H + _roc(what='Cash Dividend'))})
        root, said, _doc = self._run(files)
        doc = json.loads((root / "work" / "margin_ib.json").read_text())
        self.assertEqual([t["symbol"] for t in doc["transactions"]
                          if t["action"] == "DIVIDEND"], ["QZRX.US"])
        self.assertNotIn("undated listing", said)


class TestOnePaymentInTwoExports(_Case):
    """The split exports beside the statement holding all of it (P1-3)."""

    @rule(R.CA_ACB_06)
    def test_it_is_booked_once_on_the_listing_the_dated_rows_place(self):
        root, said, doc = self._run(dict(_SPLIT, **_COMBINED))
        self.assertEqual(self._roc_listing(root, "base"), ["QZRX.US"])
        self.assertEqual(self._deemed(doc), [])
        self.assertEqual(self._held(doc), [("QZRX.US", 5.0, 60.75)])
        self.assertNotIn("undated listing", said)
        self.assertIn(
            "warning: ATTENTION: one payment in two exports: all.csv line 12 "
            "and c_income.csv line 8 are one return of capital row of "
            "2025-01-10. It is booked once, on QZRX.US, where the dated rows "
            "of all.csv place it; c_income.csv alone had left it on QZRX.TO, "
            "a listing none of its rows shows held that day. One statement "
            "of the account that holds the payment and the ticker's trades, "
            "in place of the two, books it the same way.", said)
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2000:])

    def test_two_statements_placing_it_alike_on_two_listings_are_refused(self):
        # Each by its own dated rows: the trades, and an Open Positions
        # section holding the other listing. Never booked twice.
        root = self._project({
            'all.csv': _COMBINED['all.csv'],
            'c_income.csv': _HEAD + _OP_H + _op('CAD', 5) + _DIV_H + _ROC})
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 1, (r.stdout + r.stderr)[-2000:])
        self.assertIn(
            "taxjson-brokerage: error: all.csv line 12 and c_income.csv line "
            "10 are one return of capital row of 2025-01-10 that the two "
            "statements put on two listings (QZRX.US, QZRX.TO), each by its "
            "own dated evidence: it would be booked twice. Replace both "
            "exports with one statement of the account that holds the trades "
            "behind both placings: its Open Positions section, or every "
            "trade of the ticker from before the holdings' purchases.",
            _flat(r.stdout + r.stderr))
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertFalse((root / "work" / "margin_gains.json").exists())


class TestNoAttentionWhereTheListingIsKnown(_Case):
    @rule(R.CA_ACB_06)
    def test_one_statement_books_it_on_the_listing_held(self):
        root, said, doc = self._run(_COMBINED)
        self.assertEqual(self._roc_listing(root), ["QZRX.US"])
        self.assertNotIn("undated listing", said)
        self.assertEqual(self._deemed(doc), [])
        # 5 at 10 USD (67.50 CAD) less the 6.75 CAD returned.
        self.assertEqual(self._held(doc), [("QZRX.US", 5.0, 60.75)])
        both = _flat((root / "reports" / "margin.sum").read_text()
                     + (root / "reports" / "margin_wash.sum").read_text())
        self.assertNotIn("may have held", both)
        self.assertNotIn("more than one listing", both)

    def test_split_exports_of_one_listing_move_it_there_unflagged(self):
        root, said, doc = self._run(_ONE_LISTING)
        self.assertEqual(self._roc_listing(root), ["QZRX.US"])
        self.assertNotIn("undated listing", said)
        self.assertEqual(self._deemed(doc), [])
        both = _flat((root / "reports" / "margin.sum").read_text()
                     + (root / "reports" / "margin_wash.sum").read_text())
        self.assertNotIn("may have held", both)
        self.assertNotIn("more than one listing", both)

    def test_one_listing_only_traded_later_is_not_another_listing(self):
        # The statement trades QZRX.TO from February and no export names
        # another listing: the engine's own empty-pool warning, no more.
        files = {'y2025.csv': _HEAD + _T_H + _FEB_TO + _DIV_H + _ROC}
        _root, said, doc = self._run(files)
        self.assertEqual(self._deemed(doc), [("QZRX.TO", "2025-01-10", 6.75)])
        self.assertNotIn("undated listing", said)


class TestAtAPersonsWidth(_Case):
    """The console at 100, 120 and 160 columns (docs/output-style.md)."""

    def _at(self, root, width, *args):
        env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                   TAXJSON_WIDTH=str(width), NO_COLOR="1")
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
             "run", "--no-input", *args], capture_output=True, text=True,
            env=env, stdin=subprocess.DEVNULL, timeout=180)

    def test_the_fact_leads_the_one_line_at_each_width(self):
        root = self._project(_SPLIT)
        lead = ("Warning: Undated listing: return of capital 2025-01-10 on "
                "QZRX.TO may belong to QZRX.US")
        for width in (100, 120, 160):
            with self.subTest(width=width):
                r = self._at(root, width)
                self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2000:])
                lines = r.stdout.splitlines()
                self.assertTrue(all(len(ln) <= width for ln in lines),
                                [ln for ln in lines if len(ln) > width])
                at = [i for i, ln in enumerate(lines) if ln.startswith(lead)]

                self.assertEqual(
                    lines[at[0] - 1], "==> Pooling cost and checking "
                    "superficial losses across taxable accounts (margin)")
                self.assertEqual(lines[at[0] + 1],
                                 "==> Writing summary reports/margin_wash.sum")
                # The closing list: the row in its short form, its file
                # and line kept, and the remedy, within the block's six
                # lines; the whole message is mid-run.
                close = next(i for i, ln in enumerate(lines)
                             if ln.startswith("==> Before you trust"))
                block = lines[close + 1:]
                self.assertLessEqual(len(block), 6, block)
                self.assertEqual(len(at), 1, lines)
                self.assertLess(at[0], close)
                self.assertEqual(
                    _flat(" ".join(block)).split(" Info: ")[0],
                    "Warning: Undated listing: return of capital 2025-01-10: "
                    "QZRX.TO or QZRX.US? (margin: c_income.csv line 8): "
                    "replace the split exports (Open Positions) or add "
                    "TOBASE: `tjs run --details`")

    def test_several_rows_are_a_line_each_and_a_count_in_the_closing_list(self):
        files = dict(_SPLIT, **{'c_income.csv': (
            _HEAD + _DIV_H + _ROC + _roc(amt=3) + _roc(amt=2))})
        root = self._project(files)
        for width in (100, 120, 160):
            with self.subTest(width=width):
                r = self._at(root, width)
                self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2000:])
                lines = r.stdout.splitlines()
                close = next(i for i, ln in enumerate(lines)
                             if ln.startswith("==> Before you trust"))
                block = lines[close + 1:]
                self.assertLessEqual(len(block), 6, block)
                self.assertEqual(block[:2], [
                    "Warning: return of capital 2025-01-10: QZRX.TO or "
                    "QZRX.US? (margin: c_income.csv line 8)",
                    "Warning: return of capital 2025-01-10: QZRX.TO or "
                    "QZRX.US? (margin: c_income.csv line 9)"])
                self.assertIn(
                    "Warning: Undated listing: 3 such rows, 1 more than shown "
                    "— the remedy: `tjs run --details`", block)

    def test_details_show_the_headline_then_flush_left_lines(self):
        root = self._project(_SPLIT)
        r = self._at(root, 100, "--details")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2000:])
        lines = (r.stdout + r.stderr).splitlines()
        lead = ("Warning: Undated listing: return of capital 2025-01-10 on "
                "QZRX.TO may belong to QZRX.US")
        i = next(k for k, ln in enumerate(lines) if ln.startswith(lead))
        j = lines.index("", i)
        # The headline, then the rest on lines of its own, flush-left,
        # a blank line after the message.
        self.assertTrue(all(ln and not ln[0].isspace() and len(ln) <= 100
                            for ln in lines[i:j]), lines[i:j])
        k = next(n for n in range(i + 1, j)
                 if lines[n].startswith("The books took it as a capital "
                                        "gain of 6.75 CAD: QZRX.TO had no "
                                        "cost to reduce that day."))
        self.assertEqual(_flat(" ".join(lines[i:k])),
                         lead + " (margin: c_income.csv line 8).")
        self.assertTrue(lines[j - 1].endswith("(one cost pool)."), lines[j - 1])
        self.assertEqual(lines[j + 1],
                         "==> Writing summary reports/margin_wash.sum")

    def test_the_strict_error_names_the_row(self):
        root = self._project(_SPLIT)
        r = self._at(root, 120, "--strict")
        self.assertEqual(r.returncode, 1, (r.stdout + r.stderr)[-2000:])
        self.assertIn(
            "Error: --strict: 1 cost row(s) on a listing no dated row shows "
            "held, where the account's exports name another (c_income.csv "
            "line 8: return of capital of 2025-01-10 on QZRX.TO) — aborting",
            _flat(r.stdout + r.stderr))


class TestTheNote(unittest.TestCase):
    def test_the_note_reads_back(self):
        line = undated_listing_note("c_income.csv", 8, "return of capital",
                                    "QZRX.TO", "2025-01-10",
                                    ["QZRX.US", "QZRX.TO"])
        self.assertIn(UNDATED_LISTING, line)
        self.assertEqual(parse_undated_listing_note(line), {
            "file": "c_income.csv", "line": "8", "what": "return of capital",
            "symbol": "QZRX.TO", "day": "2025-01-10", "why": "split",
            "listings": "QZRX.TO, QZRX.US", "flat": ""})

    def test_the_unshown_note_reads_back_with_its_flat_listings(self):
        line = undated_listing_note("c.csv", 11, "return of capital",
                                    "QZRX.TO", "2025-01-10",
                                    ["QZRX.US", "QZRX.L"], why="unshown",
                                    flat=["QZRX.US"])
        self.assertIn(UNSHOWN_LISTING, line)
        self.assertIn("so the account may have held QZRX.L that day instead",
                      line)
        self.assertEqual(parse_undated_listing_note(line), {
            "file": "c.csv", "line": "11", "what": "return of capital",
            "symbol": "QZRX.TO", "day": "2025-01-10", "why": "unshown",
            "listings": "QZRX.L, QZRX.TO, QZRX.US", "flat": "QZRX.US"})

    def test_a_note_without_a_line_and_other_lines(self):
        line = undated_listing_note("a: b.csv", None, "income", "QZRX.TO",
                                    "2025-01-10", ["QZRX.US"])
        got = parse_undated_listing_note(line)
        self.assertEqual((got["file"], got["line"], got["what"]),
                         ("a: b.csv", "", "income"))
        self.assertIsNone(parse_undated_listing_note(
            "note: c_income.csv: income on QZRX.TO of 2025-01-10 kept its "
            "ISIN listing: the account held more than one listing of the "
            "ticker that day (add a ticker.map rule if it belongs to the "
            "other)."))


if __name__ == "__main__":
    unittest.main()
