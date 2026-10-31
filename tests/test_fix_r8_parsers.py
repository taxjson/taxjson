"""Fix round 8, group parsers: broker and generic importers, slip reading
and symbol matching.

Every fixture is SYNTHETIC: made-up tickers (SYN, QZX1), invented amounts.
Each test's files live in a folder of their own.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _qa_project import gains, project, tj
from test_fix_1029_r7c_ibparse import _IDS_H, _OPEN, _fill
from test_fix_ibparse import HEAD as IB_HEAD, _brokerage_cli

from taxjson.bin import taxjson_convert_tt as CT
from taxjson.bin.taxjson_reconcile_slips import load_slip
from taxjson.lib.option_yield import one_letter_listings

_AMOUNT = 'amount = "Amount"'
_AMOUNT_FEE = 'amount = "Amount"\nfee = "Fee"'
_CHARGED = 'fee_sign = "charged"'


class ShiftedAmountWithCentsOrASign(unittest.TestCase):
    """TJ-1307: an unquoted 1,000.50 or -1,000 is not read as two figures
    (TJ-1213 caught only `1` then `000`)."""  # pii-ok: synthetic values

    H4 = "symbol,quantity,proceeds,cost"

    def _load(self, head, row):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "slip.csv"
            f.write_text(head + "\n" + row + "\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                out = load_slip(f)
        return out, " ".join(err.getvalue().split())

    def test_the_split_is_not_reconciled(self):
        for head, row in (
                (self.H4, "SYN.TO,1,1,000.50"),  # pii-ok: synthetic
                (self.H4, "SYN.TO,1,1,000.50,"),  # pii-ok: synthetic
                (self.H4 + ",description",
                 "SYN.TO,1,1,000.00,10,"),  # pii-ok: synthetic
                (self.H4, "SYN.TO,1,1,050.25"),  # pii-ok: synthetic
                (self.H4, "SYN.TO,1,-1,000"),
                (self.H4, "SYN.TO,1,-1,000.50"),  # pii-ok: synthetic
                (self.H4, "SYN.TO,1,$-1,000")):
            with self.subTest(row=row):
                out, err = self._load(head, row)
                self.assertEqual(out.get("__dropped_rows__"), 1, out)
                self.assertNotIn("SYN", out)
                self.assertIn("NOT reconciled", err)
                self.assertIn("written with a comma", err)

    def test_ordinary_figures_are_read(self):
        for row, proceeds, cost in (
                ("SYN.TO,1,1,0.50", 1.0, 0.5),
                ("SYN.TO,1,-1,0.50", -1.0, 0.5),
                ("SYN.TO,1,1,50.25", 1.0, 50.25),
                ("SYN.TO,1,-5,10", -5.0, 10.0),
                ("SYN.TO,1,1.50,050.25", 1.5, 50.25),
                ('SYN.TO,1,"1,000.50",10', 1000.5, 10.0),  # pii-ok: synthetic
                ("SYN.TO,1,1000.50,0.00", 1000.5, 0.0)):
            with self.subTest(row=row):
                out, err = self._load(self.H4, row)
                self.assertNotIn("__dropped_rows__", out)
                self.assertAlmostEqual(out["SYN"]["proceeds"], proceeds)
                self.assertAlmostEqual(out["SYN"]["cost"], cost)
                self.assertEqual(err, "")






class TtTotalOnAnAdjustedSeries(unittest.TestCase):
    """TJ-1147: under MULT 50 a .tt option line's total at x100 is the
    premium, not a typo; at x50 it stays accepted; a mini's x100 stays a
    typo (TJ-0864)."""

    OPT = "QZX1251219C00005000.TO"

    def _warned(self, mult_line, tail, qty="1"):
        with tempfile.TemporaryDirectory() as td:
            tm = Path(td) / "ticker.map"
            tm.write_text(mult_line + "\n")
            err = io.StringIO()
            with mock.patch.dict(os.environ,
                                 {"TAXJSON_TICKER_MAP": str(tm)}), \
                    contextlib.redirect_stderr(err):
                CT.parse_tt_line(f"BUYSELL 2025-01-06 09:30:00 {self.OPT} "
                                 f"{qty} CAD 2 {tail}", "margin")
            return " ".join(err.getvalue().split())

    def test_a_total_at_100_passes(self):
        self.assertEqual(self._warned("MULT QZX1 50", "200 0"), "")
        self.assertEqual(self._warned("MULT QZX1 50", "201 1"), "")
        self.assertEqual(self._warned("MULT QZX1 50", "199 1", qty="-1"), "")

    def test_a_total_at_the_declared_size_passes(self):
        self.assertEqual(self._warned("MULT QZX1 50", "100 0"), "")

    def test_a_total_at_neither_is_a_typo(self):
        self.assertIn("differs from qty*price*50+fee",
                      self._warned("MULT QZX1 50", "20 0"))

    def test_a_minis_100_stays_a_typo(self):
        self.assertIn("qty*price*10+fee = 20.00",
                      self._warned("MULT QZX1 10", "200 0"))
        self.assertEqual(self._warned("MULT QZX1 10", "20 0"), "")

    def test_no_mult_line_is_the_standard_100(self):
        self.assertEqual(self._warned("# none", "200 0"), "")
        self.assertIn("qty*price*100+fee = 200.00",
                      self._warned("# none", "100 0"))


class IbAlikeRowWithoutAnExecutionId(unittest.TestCase):
    """TJ-1269: a fill with no id beside two with ids in one statement is a
    third fill, not a copy of the first id's row."""

    def _sales(self, *bodies):
        files = {f"ib_{n}.csv": IB_HEAD + b for n, b in enumerate(bodies)}
        rc, out, err, _ = _brokerage_cli(files, "--strict")
        self.assertEqual(rc, 0, err)
        return sorted(t["description"] for t in out["transactions"]
                      if t["quantity"] < 0)

    def _sale(self, xid):
        return _fill(-10, 20, "2025-06-02, 10:00:00", xid)

    def test_three_fills_get_three_marks(self):
        want = ["SYN", "SYN [fill #2]", "SYN [fill #3]"]
        for ids in (("", "X", "Y"), ("X", "", "Y"), ("X", "Y", "")):
            with self.subTest(ids=ids):
                self.assertEqual(self._sales(
                    _IDS_H + _OPEN + "".join(self._sale(i) for i in ids)),
                    want)

    def test_two_rows_without_ids_stay_apart(self):
        self.assertEqual(
            self._sales(_IDS_H + _OPEN + "".join(
                self._sale(i) for i in ("", "", "X", "Y"))),
            ["SYN", "SYN [fill #2]", "SYN [fill #3]", "SYN [fill #4]"])

    def test_the_same_statement_exported_twice_stays_three(self):
        body = "".join(self._sale(i) for i in ("", "X", "Y"))
        self.assertEqual(
            sorted(set(self._sales(_IDS_H + _OPEN + body, _IDS_H + body))),
            ["SYN", "SYN [fill #2]", "SYN [fill #3]"])

    def test_a_full_run_books_all_three(self):
        body = (_IDS_H + _fill(30, 10, "2024-01-08, 10:00:00", "OPEN")
                + "".join(_fill(-10, 20, "2024-06-03, 10:00:00", i)
                          for i in ("", "X", "Y")))
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/ib.csv": IB_HEAD + body})
            tj(root, "run", "--no-input")
            self.assertAlmostEqual(sum(t["proceeds"] for t in gains(root)),
                                   600.0)

    # The neighbours, unchanged (TJ-0966).
    def test_ids_alone(self):
        self.assertEqual(self._sales(_IDS_H + _OPEN + self._sale("X")
                                     + self._sale("Y")),
                         ["SYN", "SYN [fill #2]"])

    def test_one_id_beside_a_blank(self):
        self.assertEqual(self._sales(_IDS_H + _OPEN + self._sale("")
                                     + self._sale("X")),
                         ["SYN", "SYN [fill #2]"])

    def test_a_statement_with_no_ids_keeps_its_marks(self):
        # Beside another statement's ids: its row is the overlapping copy.
        self.assertEqual(self._sales(_IDS_H + _OPEN + self._sale("X")
                                     + self._sale("Y"),
                                     _IDS_H + self._sale("")),
                         ["SYN", "SYN", "SYN [fill #2]"])


class WinRatesBareTickerOfAOneLetterListing(unittest.TestCase):
    """TJ-1149: `win-rates SYNT` still does not select SYNT.T (it could be
    a class share, TJ-0692); the output says what to give instead."""

    def _project(self, td, sym):
        root = project(td, "p", {"margin/t.tt": (
            f"BUYSELL 2024-02-05 10:00:00 {sym} 10 CAD 10 100 0\n"
            f"BUYSELL 2024-03-04 10:00:00 {sym} -10 CAD 12 120 0\n")})
        tj(root, "run", "--no-input", check=False)
        return root

    def test_the_listing_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "SYNT.T")
            r = tj(root, "win-rates", "SYNT", "--all-history", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("the books hold SYNT.T", r.stdout)
        self.assertIn("give the full listing", r.stdout)
        self.assertIn("tjs win-rates SYNT.T", r.stdout)

    def test_the_full_listing_is_selected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "SYNT.T")
            doc = json.loads(tj(root, "win-rates", "SYNT.T", "--all-history",
                                "--json").stdout)
        self.assertEqual(doc["unmatched_symbols"], [])
        self.assertEqual([r["key"] for r in doc["rows"]], ["SYNT.T"])

    def test_the_json_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "SYNT.T")
            doc = json.loads(tj(root, "win-rates", "SYNT", "--all-history",
                                "--json").stdout)
        self.assertEqual(doc["rows"], [])
        self.assertEqual(doc["unmatched_symbols"], ["SYNT"])

    def test_a_ticker_with_no_such_listing_says_what_it_said(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, "SYNT.TO")
            r = tj(root, "win-rates", "NOPE", "--all-history", check=False)
        self.assertIn("NOPE: no closed trade on it in the window", r.stdout)
        self.assertNotIn("the books hold", r.stdout)

    def test_the_helper(self):
        self.assertEqual(one_letter_listings(
            "syn", ["SYN.T", "SYN.B", "SYN.TO", "SYN.B.US", "SYNX.T", "SYN"]),
            ["SYN.B", "SYN.T"])
        self.assertEqual(one_letter_listings("SYN", []), [])
        self.assertEqual(one_letter_listings("", ["SYN.T"]), [])


if __name__ == "__main__":
    unittest.main()
