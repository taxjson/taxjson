"""TJ-5281: a stock dividend received while short, read in the views as the engines book it.

The engines book such a row as the short's to make good: the short owes
the lender the new shares and grows by them, nothing is acquired
(CA-STKDIV-02 / US-STKDIV-06). The books were right (a gain of 60,
nothing held), but the views that replay the rows read the row as
shares received: a Canadian `tjs run` closed with "1 position at a $0
cost (1 still held)", `tjs find-missing-history` listed "$0-COST SHARES
STILL HELD ... HeldQty 20 ZeroQty 10" and a PeakShort of 100, and the
wash radar held 20 shares (both countries).

The engines' answer is now in the gains file (`stock_dividends_owed`,
the rows' ids; the split per account keeps each account's), and every
view that replays the rows reads those rows from it (lib/missing_history
`owed_stock_dividends`, `owed_reading`): a short grown by the shares.
Only where the account's books agree: every gains file of the account
holds, of the security (its renames followed), the units its rows give
with those rows counted so. Where a file holds others, the $0-cost
lines, then the only ones pointing at the books, stay as they were.

TJ-5321 (its part in one account): Canada's split of the pooled books
per account counted the row as shares received, so an account that
bought 50 after the cover was given 70 at 280.00 against the pool's 50
at 200.00 ("20 MORE than the pool"). It counts the row as the engine
books it (bin/taxjson_split_gains `_owed_as_booked`).

Every security and account is made up (SYQ, SYR; accounts m and o).
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from taxjson.rules import ids as R
from tax_rules import rule
from tax_rules.dual import cli, settings_for
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_CUR = {"canada": "CAD", "usa": "USD"}
_SYM = {"canada": "SYQ.TO", "usa": "SYQ"}
_NEW = {"canada": "SYR.TO", "usa": "SYR"}

# The entry's repro: short 100 on March 3, a stock dividend of 10 on
# April 1, 110 bought back on May 5.
_SHORT = ("BUYSELL 2025-03-03 10:00:00 {s} -100 {c} 5 500 0\n"
          "BUYSELL 2025-04-01 10:00:00 {s} 10 {c} 0 0 0 type=stock_dividend\n"
          "BUYSELL 2025-05-05 10:00:00 {s} 110 {c} 4 440 0\n")
# The control: the same dividend on 100 held.
_LONG = ("BUYSELL 2025-03-03 10:00:00 {s} 100 {c} 5 500 0\n"
         "BUYSELL 2025-04-01 10:00:00 {s} 10 {c} 0 0 0 type=stock_dividend\n")
# The pooled case: another taxable account holds 200 (Canada pools them).
_OTHER = "BUYSELL 2025-01-10 10:00:00 {s} 200 {c} 3 600 0\n"
# After the cover: 50 bought (TJ-5321's case in one account), then a
# stock dividend of 5 on them, shares received.
_BUY = "BUYSELL 2025-06-02 10:00:00 {s} 50 {c} 4 200 0\n"
_HELD = "BUYSELL 2025-06-20 10:00:00 {s} 5 {c} 0 0 0 type=stock_dividend\n"
# A rename in both accounts after the cover.
_RENAME = "SPLIT 2025-07-01 09:30:00 {s} {r} 1\n"


class _ViewsCases:
    COUNTRY = ""
    # The account's share of the pool across accounts after the cover:
    # Canada's pooled books (backlog TJ-5285), the US account's own.
    POOLED_COST = 0.0

    def _project(self, tt, other=""):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        books = {"m": tt, **({"o": other} if other else {})}
        (root / "taxjson.toml").write_text(
            settings_for(self.COUNTRY, year=2025, source_currencies=[])
            + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in books))
        for a, rows in books.items():
            acct = root / "inputs" / a
            acct.mkdir(parents=True)
            (acct / "hand.tt").write_text(rows.format(s=_SYM[self.COUNTRY],
                                                      c=_CUR[self.COUNTRY],
                                                      r=_NEW[self.COUNTRY]))
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return root, r.stdout + r.stderr

    def _dividend_id(self, root):
        doc = json.loads((root / "work" / "m_base.json").read_text())
        rows = doc["transactions"] if isinstance(doc, dict) else doc
        return [r["id"] for r in rows if r.get("type") == "stock_dividend"]

    def test_the_gains_files_name_the_row_the_engine_booked_on_the_short(self):
        root, _out = self._project(_SHORT)
        want = self._dividend_id(root)
        self.assertEqual(len(want), 1)
        for name in ("m_gains.json", "m_gains_wash.json"):
            with self.subTest(file=name):
                doc = json.loads((root / "work" / name).read_text())
                self.assertEqual(doc.get("stock_dividends_owed"), want)

    def test_find_missing_history_lists_no_zero_cost_shares(self):
        root, out = self._project(_SHORT)
        self.assertNotIn("$0 cost", out)
        r = cli(root, "find-missing-history")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertNotIn("$0-COST", r.stdout)
        # The short the engine books: 100, grown to 110 by the dividend.
        self.assertIn("-110.0000", r.stdout)

    def test_the_wash_radar_holds_nothing(self):
        root, _out = self._project(_SHORT)
        rpt = (root / "reports" / "wash_radar_m.rpt").read_text()
        self.assertNotIn(_SYM[self.COUNTRY] + " ", rpt)

    def _inventory(self, root, name):
        doc = json.loads((root / "work" / name).read_text())
        return {h["symbol"]: (round(h["qty"], 6), round(h["total_cost"], 2))
                for h in doc["inventory"]}

    def _radar(self, root, account, sym):
        rpt = (root / "reports" / f"wash_radar_{account}.rpt").read_text()
        m = re.search(rf"^\s*{re.escape(sym)}\s+(\S+)", rpt, re.M)
        return m and m.group(1)

    def _no_count_lost(self, out):
        self.assertNotIn("$0 cost", out)
        self.assertNotIn("than the pool", out)

    def test_a_purchase_after_the_cover_is_the_only_holding(self):
        # Backlog TJ-5321: the split gave m 70 at 280.00, the radar 70.
        root, out = self._project(_SHORT + _BUY)
        self._no_count_lost(out)
        sym = _SYM[self.COUNTRY]
        for name in ("m_gains.json", "m_gains_wash.json"):
            with self.subTest(file=name):
                self.assertEqual(self._inventory(root, name),
                                 {sym: (50.0, 200.0)})
        r = cli(root, "list")
        # SINCE is the day the 50 were bought, as in the plain file, not
        # the cover's (ACCOUNT SYMBOL QTY COST COST/SH DEFERRED SINCE).
        self.assertRegex(r.stdout, rf"(?m)^m\s+{re.escape(sym)}\s+50\s+200\.00\s+"
                         r"4\.00\s+\S+\s+2025-06-02\s*$")
        doc = json.loads((root / "work" / "m_gains_wash.json").read_text())
        self.assertEqual([h.get("position_start_date") for h in doc["inventory"]],
                         ["2025-06-02"])
        self.assertEqual(self._radar(root, "m", sym), "50")

    def test_a_dividend_on_the_shares_bought_after_is_shares_received(self):
        # The reviewer's case: the radar held 75, the books 55.
        root, out = self._project(_SHORT + _BUY + _HELD)
        self.assertNotIn("than the pool", out)
        sym = _SYM[self.COUNTRY]
        self.assertEqual(self._inventory(root, "m_gains_wash.json"),
                         {sym: (55.0, 200.0)})
        self.assertEqual(self._radar(root, "m", sym), "55")
        r = cli(root, "find-missing-history")
        # No row of the 10 at $0 (Symbol Account Cur HeldQty ZeroQty).
        self.assertNotRegex(r.stdout, rf"(?m)^{re.escape(sym)}\s+m\s+\S+\s+\S+\s+"
                            r"10\.0000\s")

    def test_a_short_still_open_has_grown_by_the_dividend(self):
        root, out = self._project(_SHORT.splitlines(True)[0]
                                  + _SHORT.splitlines(True)[1])
        self._no_count_lost(out)
        for name in ("m_gains.json", "m_gains_wash.json"):
            with self.subTest(file=name):
                self.assertEqual(self._inventory(root, name),
                                 {_SYM[self.COUNTRY]: (-110.0, -500.0)})

    def test_another_accounts_holding_leaves_the_reading(self):
        # m holds nothing after the cover, o its 200, and no view lists
        # shares at $0.
        root, out = self._project(_SHORT, other=_OTHER)
        self._no_count_lost(out)
        sym = _SYM[self.COUNTRY]
        for a, want in (("m", {}), ("o", {sym: (200.0, self.POOLED_COST)})):
            with self.subTest(account=a):
                self.assertEqual(self._inventory(root, f"{a}_gains_wash.json"),
                                 want)
        r = cli(root, "find-missing-history")
        self.assertNotIn("$0-COST", r.stdout)
        self.assertEqual(self._radar(root, "COMBINED", sym), "200")

    def test_a_rename_in_both_accounts_leaves_the_reading(self):
        # The reviewer's case: the inventory names the new symbol, the
        # dividend's row the old one.
        root, out = self._project(_SHORT + _RENAME,
                                  other=_OTHER + _RENAME)
        self._no_count_lost(out)
        new = _NEW[self.COUNTRY]
        self.assertEqual(self._inventory(root, "m_gains_wash.json"), {})
        self.assertEqual(self._inventory(root, "o_gains_wash.json"),
                         {new: (200.0, self.POOLED_COST)})
        self.assertEqual(self._radar(root, "COMBINED", new), "200")

    def test_a_dividend_on_shares_held_is_not_named(self):
        # The control: received while long, the row is shares received.
        root, _out = self._project(_LONG)
        doc = json.loads((root / "work" / "m_gains.json").read_text())
        self.assertNotIn("stock_dividends_owed", doc)
        self.assertEqual({h["symbol"]: round(h["qty"], 6)
                          for h in doc["inventory"]},
                         {_SYM[self.COUNTRY]: 110.0})


@rule(R.CA_STKDIV_02)
class TestCanada(_ViewsCases, unittest.TestCase):
    COUNTRY = "canada"
    # The pool's 740.00: o's 600.00 and m's cover of 440.00, less the
    # 300.00 m's sale took from the pool (backlog TJ-5285).
    POOLED_COST = 740.0

    def test_the_run_ends_with_no_zero_cost_position(self):
        _root, out = self._project(_SHORT)
        self.assertNotIn("position at a $0 cost", out)

    def test_the_pooled_books_book_the_dividend_on_the_short(self):
        # The pooled pass books the row as m's short's too, and the split
        # per account counts it so: m's share is nothing, not 20 at 74.00
        # (backlog TJ-5321).
        root, _out = self._project(_SHORT, other=_OTHER)
        doc = json.loads((root / "work" / ".blend_gains_wash.json").read_text())
        self.assertEqual(doc.get("stock_dividends_owed"),
                         self._dividend_id(root))
        r = cli(root, "list")
        self.assertNotRegex(r.stdout, r"(?m)^m\s+SYQ\.TO\s")

    def test_a_dividend_on_the_shares_bought_after_still_asks_for_its_cost(self):
        # CA-STKDIV-01: the 5 received while long are at the row's $0 and
        # listed; the 10 the short owed are not.
        root, _out = self._project(_SHORT + _BUY + _HELD)
        r = cli(root, "find-missing-history")
        self.assertRegex(r.stdout, r"(?m)^SYQ\.TO\s+m\s+CAD\s+55\.0000\s+"
                         r"5\.0000\s+2025-06-20\s")

    def test_a_dividend_on_shares_held_still_asks_for_its_cost(self):
        # The control (CA-STKDIV-01): shares received while long are at
        # the cost the row gives, $0 here, and still listed.
        root, _out = self._project(_LONG)
        r = cli(root, "find-missing-history")
        self.assertIn("$0-COST", r.stdout)


@rule(R.US_STKDIV_06)
class TestUs(_ViewsCases, unittest.TestCase):
    COUNTRY = "usa"
    POOLED_COST = 600.0     # US lots are the account's own


@rule(R.CA_STKDIV_02)
class TestTheScope(unittest.TestCase):
    """lib/missing_history owed_stock_dividends: the engine's reading where
    the account's gains files hold the units its rows give so counted."""

    def _row(self, rid, qty, day, **kw):
        return {"account": "m", "action": "BUYSELL", "currency": "CAD",
                "date": day, "date_settle": day, "id": rid, "price": 0.0,
                "quantity": qty, "symbol": "SYQ.TO", "symbol_new": "",
                "time": "10:00:00", "type": "", **kw}

    def _owed(self, extra=(), plain=(), wash=()):
        from taxjson.lib.missing_history import owed_stock_dividends
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        work = Path(td.name) / "work"
        work.mkdir()
        rows = [self._row("s1", -100.0, "2025-03-03"),
                self._row("d1", 10.0, "2025-04-01", type="stock_dividend"),
                self._row("c1", 110.0, "2025-05-05"), *extra]
        (work / "m_base.json").write_text(json.dumps({"transactions": rows}))
        for name, inv in (("m_gains.json", plain), ("m_gains_wash.json", wash)):
            (work / name).write_text(json.dumps(
                {"inventory": [dict(symbol=s, qty=q) for s, q in inv],
                 "stock_dividends_owed": ["d1"]}))
        return owed_stock_dividends(work)

    def test_books_that_agree_take_the_reading(self):
        self.assertEqual(self._owed(), {"d1"})

    def test_a_file_holding_units_the_rows_do_not_give_keeps_the_old_lines(self):
        # The pooled split before TJ-5321's fix: 20 where the rows give 0.
        self.assertEqual(self._owed(wash=[("SYQ.TO", 20.0)]), set())

    def test_a_holding_the_rows_give_takes_the_reading(self):
        # 50 bought after the cover: the security is held, and the books
        # agree (a scope by names dropped the reading: the radar held 70).
        buy = self._row("b1", 50.0, "2025-06-02")
        self.assertEqual(self._owed([buy], plain=[("SYQ.TO", 50.0)],
                                    wash=[("SYQ.TO", 50.0)]), {"d1"})

    def test_a_rename_is_followed(self):
        # The inventory names SYR.TO, the dividend's row SYQ.TO.
        rename = self._row("r1", 1.0, "2025-07-01", action="SPLIT",
                           symbol_new="SYR.TO")
        self.assertEqual(self._owed([rename], wash=[("SYR.TO", 20.0)]), set())
        self.assertEqual(self._owed([rename]), {"d1"})


@rule(R.CA_STKDIV_02)
class TestTheReading(unittest.TestCase):
    """lib/missing_history owed_reading: the rows a view replays."""

    def _rows(self):
        return [{"id": "d1", "action": "BUYSELL", "symbol": "SYQ.TO",
                 "quantity": 10.0, "type": "stock_dividend",
                 "date": "2025-04-01", "_acct": "m"},
                {"id": "d2", "action": "BUYSELL", "symbol": "SYQ.TO",
                 "quantity": 10.0, "type": "stock_dividend",
                 "date": "2025-06-02", "_acct": "m"}]

    def test_a_named_row_grows_the_short_and_the_other_is_kept(self):
        from taxjson.lib.missing_history import (is_owed_stock_dividend,
                                                 owed_reading)
        out = owed_reading(self._rows(), {"d1"})
        self.assertEqual([(r["id"], r["quantity"], is_owed_stock_dividend(r))
                          for r in out],
                         [("d1", -10.0, True), ("d2", 10.0, False)])

    def test_no_loss_window_reads_it_as_a_purchase(self):
        from taxjson.lib.edge_cases import Book
        from taxjson.lib.missing_history import owed_reading
        book = Book.__new__(Book)
        book.country = "canada"
        book.in_wash_scope = lambda _acct: True
        out = owed_reading(self._rows(), {"d1"})
        # (The other, received while long, is one: CA-STKDIV-01.)
        self.assertEqual([book.window_row(r) for r in out], [False, True])


if __name__ == "__main__":
    unittest.main()
