"""TJ-5036: a .tt round trip keeps the foreign cash a row moves.

`taxjson-convert-tt book.json out.tt` wrote a parser's row without what
the FX-on-cash ledgers read of it (its wording, its source file, its
commission refunds), so the line read back moved other cash: a
crypto-for-crypto leg and an in-kind reward became cash trades, a
broker's cash return of capital no cash, a refunded commission a
smaller payment on the trade's date. The writer now adds `cash_leg=`
and `refunds=`, the reader books them, and a row whose line cannot say
it is refused, naming the row.

Every row is synthetic: made-up tickers, amounts and file names.
"""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_fx_cash import build_ledger
from taxjson.lib import check_tt as CT
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib.cash_legs import _cash_leg_dates
from taxjson.lib.cli_diag import InputContentError
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.tt_format import (json_to_tt_lines, parse_tt_line, tt_to_json,
                                   tx_to_tt_line)
from taxjson.model.transaction import TaxTransaction
from taxjson.parsers.toolbox.staking import ledger_reward_legs
from taxjson.rules import ids as R
from _support.fx_cash_build import tt as cash_line
from tax_rules import rule

BOOK = "margin/manual"
RATE = 1.35


def _row(**kw):
    kw.setdefault("time", "10:00:00")
    kw.setdefault("account", "margin")
    kw.setdefault("date_settle", kw["date"])
    kw.setdefault("source", "export_2025.csv")
    return kw


def swap_rows():
    """A crypto-for-crypto fill as the Kraken parser words its legs: 1
    SOL sold for 0.05 ETH, each leg valued at USD 100."""
    return [
        _row(action="BUYSELL", date="2025-03-03", symbol="SOL",
             quantity=-1.0, currency="USD", price=100.0, net_amount=100.0,
             fee=0.0, description="Trade (sell leg of crypto-to-crypto)"),
        _row(action="BUYSELL", date="2025-03-03", symbol="ETH",
             quantity=0.05, currency="USD", price=2000.0, net_amount=100.0,
             fee=0.0, description="Trade (counter leg of crypto-to-crypto)"),
    ]


def reward_rows():
    """An in-kind reward as the ledger parser books it: 0.1 SOL valued
    at USD 10, its income row and its acquisition."""
    legs = ledger_reward_legs("SOL", "2025-04-04", "10:00:00", 0.1,
                              usd_value=10.0, stablecoins_as_cash=True,
                              account="margin")
    return [dict(leg, source="export_2025.csv") for leg in legs]


def roc_rows():
    """A return of capital a broker paid in cash: USD 10."""
    return [_row(action="ADJUST", date="2025-05-05", symbol="SAMPL.US",
                 currency="USD", net_amount=-10.0, type="roc",
                 description="Return of capital")]


def refund_rows():
    """A January purchase whose USD 1 commission was refunded in
    February: the cost is net of it (10 x 10 + 1 - 1)."""
    return [_row(action="BUYSELL", date="2025-01-10",
                 date_settle="2025-01-13", symbol="SAMPL.US", quantity=10.0,
                 currency="USD", price=10.0, net_amount=100.0, fee=0.0,
                 commission=0.0, commission_refunds="2025-02-14 1.0",
                 description="BUY 10 SAMPL")]


def stable_reward_rows():
    """5 USDC of rewards the export values in CAD (6.85)."""
    return [_row(action="DIVIDEND", date="2025-06-06", symbol="USDC",
                 quantity=5.0, currency="CAD", price=1.37, net_amount=6.85,
                 gross_amount=6.85, type="dividend",
                 description="Staking Reward: Rewards Income")]


def _lines(rows, folder):
    """taxjson-convert-tt's lines of a JSON book of `rows`."""
    src = Path(folder) / "book.json"
    src.write_text(json.dumps({"transactions": rows}))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        return list(json_to_tt_lines(src, date_basis="settle"))


def _read(lines, folder, name="book.tt"):
    """The rows `tjs run` reads of a .tt file holding `lines`."""
    tt = Path(folder) / name
    tt.write_text("".join(ln + "\n" for ln in lines))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rows = tt_to_json(tt, "margin", equity=False)["transactions"]
    for r in rows:
        TaxTransaction(**r)         # a row of the books, every field known
    return rows


def _legs(rows, stable_cash=True):
    """[(day, currency, amount)] the rows move, each on its own day."""
    out = []
    for r in rows:
        for cur, amt, paid in _cash_leg_dates(r, stable_cash):
            out.append((paid or r["date_settle"], cur, round(amt, 2)))
    return sorted(out)


def _v1(rows):
    """Ledger v1's units of each currency: (acquired, disposed)."""
    doc = build_ledger([dict(r) for r in rows], "CAD", {}, 2025,
                       rate_of=lambda c, d: RATE, country="canada")
    return {c: (v["acquired"], v["disposed"])
            for c, v in doc["per_currency"].items()}


def _v2(rows):
    """Ledger v2's units of each currency and its dated spends, from an
    opening of USD 1,000 in the book."""
    native = [dict(r, _book=BOOK, id=r.get("id") or f"r{i}")
              for i, r in enumerate(rows)]
    cash = {"events": [], "lines": [cash_line(
        "CASHOPEN 2025-01-01 USD 1000 1350", book=BOOK)],
        "problems": [], "stablecoins_as_cash": True}
    doc = V2.build(native, cash, "CAD", 2025, lambda c, d: RATE,
                   country="canada")
    return ({c: (v["acquired"], v["disposed"])
             for c, v in doc["per_currency"].items()},
            [(e["date"], e["currency"], e["units"]) for e in doc["events"]])


class _RoundTrip(unittest.TestCase):

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = td.name

    def _trip(self, rows):
        lines = _lines(rows, self.dir)
        return lines, _read(lines, self.dir)

    def _same_movements(self, rows, back):
        self.assertEqual(_legs(back), _legs(rows))
        self.assertEqual(_legs(back, False), _legs(rows, False))
        self.assertEqual(_v1(back), _v1(rows))
        self.assertEqual(_v2(back), _v2(rows))
        from taxjson.lib.cash_legs import cash_meaning
        self.assertEqual([cash_meaning(r) for r in back],
                         [cash_meaning(r) for r in rows])


class TestFourCasesRoundTrip(_RoundTrip):
    """Each of the entry's cases moves the same currency on the same
    days before and after `json -> .tt -> json`."""

    @rule(R.CA_FX_07)
    def test_a_crypto_for_crypto_sale_moves_no_dollars(self):
        rows = swap_rows()
        lines, back = self._trip(rows)
        self.assertTrue(all(ln.endswith(" cash_leg=none") for ln in lines),
                        lines)
        self.assertEqual(_legs(rows), [])
        self.assertEqual(_legs(back), [])       # was +100 and -100 USD
        self.assertEqual(_v1(back), {})
        self.assertEqual(_v2(back), ({}, []))
        self._same_movements(rows, back)

    @rule(R.CA_FX_07)
    def test_an_in_kind_reward_moves_no_dollars(self):
        rows = reward_rows()
        self.assertEqual([r["action"] for r in rows], ["DIVIDEND", "BUYSELL"])
        lines, back = self._trip(rows)
        self.assertTrue(all(ln.endswith(" cash_leg=reward") for ln in lines),
                        lines)
        self.assertEqual(_legs(back), [])       # was +10 and -10 USD
        self.assertEqual(_v1(back), {})
        self._same_movements(rows, back)

    @rule(R.CA_FX_07)
    def test_a_brokers_cash_return_of_capital_is_received(self):
        rows = roc_rows()
        lines, back = self._trip(rows)
        self.assertEqual(lines, ["ADJUST 2025-05-05 10:00:00 SAMPL.US USD "
                                 "-10.00000 type=roc cash_leg=cash"])
        self.assertEqual(_legs(back), [("2025-05-05", "USD", 10.0)])
        self.assertEqual(_v1(back), {"USD": (10.0, 0.0)})
        self.assertEqual(_v2(back)[0], {"USD": (10.0, 0.0)})
        self._same_movements(rows, back)

    @rule(R.CA_FX_07, R.CA_ACB_COMMREFUND)
    def test_a_refunded_commission_comes_back_on_its_own_day(self):
        rows = refund_rows()
        lines, back = self._trip(rows)
        self.assertEqual(lines, [
            "BUYSELL 2025-01-13 10:00:00 SAMPL.US 10.00000000 USD "
            "10.00000000 100.00000 0.00000 refunds=2025-02-14:1.0 "
            "traded=2025-01-10"])
        self.assertEqual(back[0]["commission_refunds"], "2025-02-14 1.0")
        # 101 paid on the trade's day, 1 received in February: read at
        # the folded net it was 100 paid in January and nothing after.
        units, spends = _v2(back)
        self.assertEqual(units, {"USD": (1.0, 101.0)})
        self.assertEqual(spends, [("2025-01-13", "USD", 101.0)])
        self._same_movements(rows, back)

    @rule(R.CA_FX_07, R.CA_CRYPTO_02)
    def test_a_stablecoin_reward_priced_in_cad_brings_in_its_dollars(self):
        rows = stable_reward_rows()
        lines, back = self._trip(rows)
        self.assertTrue(lines[0].endswith(" cash_leg=reward"), lines)
        self.assertEqual(_v1(back), {"USD": (5.0, 0.0)})    # was nothing
        self.assertEqual(_v2(back)[0], {"USD": (5.0, 0.0)})
        self._same_movements(rows, back)

    def test_a_coin_fee_and_a_legacy_cash_in_lieu_leg(self):
        fee = [_row(action="BUYSELL", date="2025-07-07", symbol="SOL",
                    quantity=-0.001, currency="USD", price=100.0,
                    net_amount=0.1, fee=0.0,
                    description="Withdrawal fee 0.001 SOL (disposed at FMV)")]
        cil = [_row(action="BUYSELL", date="2025-09-09", symbol="SAMPL.US",
                    quantity=-0.5, currency="USD", price=15.0, net_amount=7.5,
                    fee=0.0, corp_event_id="merger-2025-09-09-SAMPL",
                    description="Merger SAMPL: cash-in-lieu for 0.5 "
                                "fractional share(s)")]
        for rows, token in ((fee, "cash_leg=none"), (cil, "cash_leg=cash")):
            with self.subTest(token=token):
                lines, back = self._trip(rows)
                self.assertTrue(lines[0].endswith(" " + token), lines)
                self._same_movements(rows, back)

    def test_the_ids_are_the_ones_the_lines_had_without_the_tokens(self):
        """Neither token is part of a row's id: the line with it and
        the line without it are one row to the dedup."""
        for rows in (swap_rows(), reward_rows(), roc_rows(), refund_rows()):
            lines, back = self._trip(rows)
            bare = [" ".join(t for t in ln.split()
                             if not t.startswith(("cash_leg=", "refunds=")))
                    for ln in lines]
            self.assertNotEqual(bare, lines)
            self.assertEqual([r["id"] for r in _read(bare, self.dir, "b.tt")],
                             [r["id"] for r in back])

    def test_the_events_view_writes_the_same_tokens(self):
        from taxjson.cli.listings import _tx_display_line
        self.assertTrue(_tx_display_line(roc_rows()[0], settle=True)
                        .endswith(" type=roc cash_leg=cash"))
        self.assertTrue(_tx_display_line(refund_rows()[0], settle=True)
                        .endswith(" refunds=2025-02-14:1.0"))


class TestRefusedRows(_RoundTrip):
    """A row whose .tt line cannot say what cash it moved is refused,
    naming the row; nothing is written."""

    def test_a_reward_fee_row_has_no_line(self):
        # A FEE line takes no token: read back, the coin fee would be
        # 0.50 US dollars paid.
        rows = swap_rows() + [_row(
            action="FEE", date="2025-04-04", symbol="CASH", currency="USD",
            net_amount=0.5, id="fee-row-1",
            description="Staking reward fee taken in SOL")]
        with self.assertRaises(InputContentError) as cm:
            _lines(rows, self.dir)
        msg = str(cm.exception)
        self.assertIn("transaction at index 2 (id='fee-row-1', FEE "
                      "2025-04-04 CASH, from export_2025.csv)", msg)
        self.assertIn("would move other foreign cash", msg)
        self.assertIn("cash_leg=reward", msg)
        self.assertIn("nothing was written", msg)

    def test_the_refusal_says_what_decided(self):
        """Review P3-7: "a FEE line", and the row's own `cash_leg`
        field named when it, not the wording, decided."""
        worded = [_row(action="FEE", date="2025-04-04", symbol="CASH",
                       currency="USD", net_amount=0.5,
                       description="Staking reward fee taken in SOL")]
        own = [_row(action="FEE", date="2025-04-04", symbol="CASH",
                    currency="USD", net_amount=0.5, cash_leg="none")]
        for rows, said in ((worded, "lib/cash_legs: its wording, type and "
                                    "source say so"),
                           (own, "the row's own `cash_leg` field says so")):
            with self.assertRaises(InputContentError) as cm:
                _lines(rows, self.dir)
            self.assertIn(said, str(cm.exception))
            self.assertIn("a FEE line has no place for the token",
                          str(cm.exception))
            self.assertNotIn("an FEE", str(cm.exception))

    def test_a_move_in_kind_the_run_booked_has_no_line(self):
        from taxjson.lib.row_types import (IN_KIND_CONTRIBUTION_TYPE,
                                           TRANSFER_BOOK_VALUE_TYPE)
        for typ in (TRANSFER_BOOK_VALUE_TYPE, IN_KIND_CONTRIBUTION_TYPE):
            with self.subTest(type=typ):
                rows = [_row(action="BUYSELL", date="2025-08-08",
                             symbol="SAMPL.US", quantity=10.0, currency="USD",
                             price=10.0, net_amount=100.0, fee=0.0, type=typ)]
                with self.assertRaises(InputContentError) as cm:
                    _lines(rows, self.dir)
                self.assertIn(f"a row of type {typ} is a move in kind",
                              str(cm.exception))

    def test_the_command_refuses_and_writes_nothing(self):
        src = Path(self.dir) / "book.json"
        out = Path(self.dir) / "out.tt"
        out.write_text("# kept\n")
        src.write_text(json.dumps({"transactions": swap_rows() + [_row(
            action="FEE", date="2025-04-04", symbol="CASH", currency="USD",
            net_amount=0.5, description="Staking reward fee taken in SOL")]}))
        p = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_convert_tt",
                               [str(src), str(out), "--date-basis", "settle"]),
            capture_output=True, text=True)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("taxjson-convert-tt: error:", p.stderr)
        self.assertIn("transaction at index 2", p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        self.assertEqual(out.read_text(), "# kept\n")


def _income_row(**kw):
    return _row(action="DIVIDEND", symbol="SAMPL.US", quantity=10.0,
                currency="USD", price=1.0, net_amount=10.0,
                gross_amount=10.0, type="dividend", **kw)


def _v2_year(rows, year, basis="settle"):
    """Ledger v2 of `year`: the units acquired and each book's dated
    balances (`holdings`, on `basis`), the rate 1.30 through 2025 and
    1.40 after."""
    native = [dict(r, _book=BOOK, id=r.get("id") or f"r{i}")
              for i, r in enumerate(rows)]
    cash = {"events": [], "lines": [cash_line(
        f"CASHOPEN {year}-01-01 USD 0 0", book=BOOK)],
        "problems": [], "stablecoins_as_cash": True}
    doc = V2.build(native, cash, "CAD", year,
                   lambda c, d: 1.30 if d <= "2025-12-31" else 1.40,
                   country="canada", holdings=True, holdings_basis=basis)
    return ({c: v["acquired"] for c, v in doc["per_currency"].items()},
            [pt for h in doc["holdings"]["foreign"] for pt in h["points"]])


class TestSettledDate(_RoundTrip):
    """TJ-5211: a line whose date is not the row's settlement date
    carries it (`settled=`), so the ledgers walk the row's cash on the
    same day from the file as from the book."""

    @rule(R.CA_FX_07)
    def test_a_dividend_that_settles_next_year_stays_in_that_year(self):
        rows = [_income_row(date="2025-12-31", date_settle="2026-01-02"),
                _income_row(date="2025-09-30")]
        src = Path(self.dir) / "book.json"
        src.write_text(json.dumps({"transactions": rows}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            lines = list(json_to_tt_lines(src, date_basis="settle"))
        self.assertEqual(err.getvalue(), "")        # nothing lost: no note
        self.assertTrue(lines[0].startswith("DIVIDEND 2025-12-31 "), lines)
        self.assertTrue(lines[0].endswith(" settled=2026-01-02"), lines)
        self.assertNotIn("settled=", lines[1])
        back = _read(lines, self.dir)
        self.assertEqual([(r["date"], r["date_settle"]) for r in back],
                         [("2025-12-31", "2026-01-02"),
                          ("2025-09-30", "2025-09-30")])
        # 10 US dollars on 2026-01-02 at 1.40: read on the pay date they
        # were 2025's, at 1.30.
        self.assertEqual(_v2_year(back, 2026),
                         ({"USD": 10.0}, [["2026-01-02", 10.0, 14.0]]))
        for year in (2025, 2026):
            for basis in ("settle", "trade"):
                self.assertEqual(_v2_year(back, year, basis),
                                 _v2_year(rows, year, basis))

    def test_a_trade_basis_trade_keeps_both_dates(self):
        rows = [_row(action="BUYSELL", date="2025-12-30",
                     date_settle="2025-12-31", symbol="SAMPL.US",
                     quantity=-10.0, currency="USD", price=10.0,
                     net_amount=100.0, fee=0.0)]
        src = Path(self.dir) / "book.json"
        src.write_text(json.dumps({"transactions": rows}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            lines = list(json_to_tt_lines(src, date_basis="trade"))
            settle = list(json_to_tt_lines(src, date_basis="settle"))
        self.assertTrue(lines[0].startswith("BUYSELL 2025-12-30 "), lines)
        self.assertTrue(lines[0].endswith(" settled=2025-12-31"), lines)
        back = _read(lines, self.dir)
        self.assertEqual((back[0]["date"], back[0]["date_settle"]),
                         ("2025-12-30", "2025-12-31"))
        self.assertEqual(_v2_year(back, 2025, "trade"),
                         _v2_year(rows, 2025, "trade"))
        # On the settle basis the line carries the settlement date and
        # the trade date in `traded=` (TJ-5213): the same row, the same
        # id, and nothing left for a note.
        self.assertTrue(settle[0].startswith("BUYSELL 2025-12-31 "), settle)
        self.assertTrue(settle[0].endswith(" traded=2025-12-30"), settle)
        same = _read(settle, self.dir, "s.tt")
        self.assertEqual((same[0]["date"], same[0]["date_settle"]),
                         ("2025-12-30", "2025-12-31"))
        self.assertEqual(same[0]["id"], back[0]["id"])
        self.assertEqual(err.getvalue(), "")
        # The cash is the book's on its trade date too (`tjs t1135`):
        # read with the one date it showed from Dec 31, not Dec 30.
        self.assertEqual(_v2_year(same, 2025, "trade")[1][0][0], "2025-12-30")
        for basis in ("settle", "trade"):
            self.assertEqual(_v2_year(same, 2025, basis),
                             _v2_year(rows, 2025, basis))

    def test_a_traded_date_is_checked(self):
        row = _parse_quiet("BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 "
                           "100 0 traded=2025-12-30")
        self.assertEqual((row["date"], row["date_settle"]),
                         ("2025-12-30", "2025-12-31"))
        bare = _parse_quiet("BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 "
                            "100 0")
        self.assertNotEqual(row["id"], bare["id"])      # `date` is hashed
        book = dict(action="BUYSELL", date="2025-12-30",
                    date_settle="2025-12-31", time="10:00:00",
                    account="margin", symbol="SAMPL.US", quantity=10.0,
                    currency="USD", price=10.0, net_amount=100.0)
        self.assertEqual(row["id"], TaxTransaction(**book).compute_id())
        for line, said in (
                ("BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                 "traded=2026-01-02",
                 "traded=2026-01-02 is after the line's date 2025-12-31"),
                ("BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                 "traded=2025-13-01",
                 "traded= '2025-13-01' is not a valid YYYY-MM-DD date"),
                ("BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                 "traded=2025-12-29 traded=2025-12-30",
                 "traded= is given twice"),
                ("BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                 "traded=2025-12-30 settled=2026-01-02",
                 "traded=2025-12-30 and settled=2026-01-02 on one line"),
                ("FEE 2025-12-31 16:00:00 USD 1.00 settled=2026-01-02 "
                 "traded=2025-12-30", "on one line")):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as cm:
                    _parse_quiet(line)
                self.assertIn(said, str(cm.exception))
                self.assertTrue(str(cm.exception).startswith("h.tt:1: "))

    @rule(R.CA_FX_07)
    def test_a_row_that_settles_before_it_is_paid_is_refused(self):
        """Review P2: a DIVIDEND dated 2026-01-02 that settled
        2025-12-31 was written with its pay date alone (exit 0): its 10
        US dollars moved from the 2025 ledger to the 2026 one. No line
        says a settlement before the pay date: the row is refused,
        under either basis, and nothing is written. (A trade like it is
        the loader's refusal, CA-DATE-03.)"""
        rows = [refund_rows()[0],
                _income_row(date="2026-01-02", date_settle="2025-12-31",
                            id="div-early")]
        self.assertEqual(_v2_year(rows[1:], 2025)[0], {"USD": 10.0})
        src = Path(self.dir) / "book.json"
        src.write_text(json.dumps({"transactions": rows}))
        for basis in ("settle", "trade"):
            with self.subTest(basis=basis):
                err = io.StringIO()
                with self.assertRaises(InputContentError) as cm, \
                        contextlib.redirect_stderr(err):
                    list(json_to_tt_lines(src, date_basis=basis))
                msg = str(cm.exception)
                self.assertIn("transaction at index 1 (id='div-early', "
                              "DIVIDEND 2026-01-02 SAMPL.US, from "
                              "export_2025.csv)", msg)
                self.assertIn("would not keep the row's dates (dated "
                              "2026-01-02, settled 2025-12-31; the line "
                              "reads back dated 2026-01-02, settled "
                              "2026-01-02)", msg)
                self.assertIn("nothing was written", msg)
        out = Path(self.dir) / "out.tt"
        run = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_convert_tt",
                               [str(src), str(out), "--date-basis", "settle"]),
            capture_output=True, text=True)
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertIn("would not keep the row's dates", run.stderr)
        self.assertFalse(out.exists())

    def test_the_cash_compare_reads_the_rows_own_dates(self):
        """`_unwritable_cash` compares the dates the ledgers walk the
        row on, not only its legs: a line that reads back with another
        settlement or trade date is not the row."""
        from taxjson.lib.tt_format import _unwritable_cash
        row = _income_row(date="2025-12-31", date_settle="2026-01-02")
        good = tx_to_tt_line(row)
        self.assertEqual(_unwritable_cash(row, good), ("", ""))
        key, why = _unwritable_cash(row, good.replace(
            " settled=2026-01-02", ""))
        self.assertEqual(key, "dates")
        self.assertIn("settled 2026-01-02; the line reads back dated "
                      "2025-12-31, settled 2025-12-31", why)
        trade = refund_rows()[0]
        self.assertEqual(_unwritable_cash(trade, tx_to_tt_line(trade)),
                         ("", ""))
        self.assertEqual(_unwritable_cash(trade, tx_to_tt_line(trade).replace(
            " traded=2025-01-10", ""))[0], "dates")

    def test_a_gap_of_more_than_366_days_is_a_typo(self):
        """Review P3-6: `settled=2099-01-10` and `traded=1999-01-10`
        were booked."""
        base = "BUYSELL 2025-01-13 09:30:00 SAMPL.US 10 USD 10 100 0"
        for token, said in (
                ("settled=2099-01-10", "settled=2099-01-10 is 27025 days "
                                       "from the line's date 2025-01-13"),
                ("traded=1999-01-10", "traded=1999-01-10 is 9500 days from "
                                      "the line's date 2025-01-13"),
                ("settled=2026-01-15", "is 367 days from")):
            with self.subTest(token=token):
                with self.assertRaises(ValueError) as cm:
                    _parse_quiet(f"{base} {token}")
                self.assertIn(said, str(cm.exception))
                self.assertIn("read as a typo", str(cm.exception))
                self.assertTrue(str(cm.exception).startswith("h.tt:1: "))
        self.assertEqual(_parse_quiet(f"{base} settled=2026-01-14")
                         ["date_settle"], "2026-01-14")      # 366 days
        # The writer has no line for such a row: refused, never a line
        # the reader would refuse.
        row = _income_row(date="2025-01-13", date_settle="2026-06-01")
        src = Path(self.dir) / "book.json"
        src.write_text(json.dumps({"transactions": [row]}))
        with self.assertRaises(InputContentError) as cm:
            list(json_to_tt_lines(src))
        self.assertIn("would not keep the row's dates", str(cm.exception))

    def test_traded_is_refused_on_a_line_dated_the_day_paid(self):
        """Review P3-6: an income, tax, interest or fee line's date is
        its pay date; `traded=` on one was booked as its date."""
        for line in ("DIVIDEND 2025-12-31 10:00:00 SAMPL.US 10 USD 1 10",
                     "DIVIDEND_IN_LIEU 2025-12-31 10:00:00 SAMPL.US 10 USD "
                     "1 10",
                     "TAX 2025-12-31 10:00:00 SAMPL.US 10 USD 0 1.50",
                     "INTEREST 2025-12-31 16:00:00 USD 12.40",
                     "FEE 2025-12-31 16:00:00 USD 1.00"):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as cm:
                    _parse_quiet(f"{line} traded=2025-12-30")
                self.assertIn("line's date is the day it was paid",
                              str(cm.exception))
                self.assertIn("not traded=", str(cm.exception))
        self.assertIn("traded=2025-12-30: a FEE line's date", self._said(
            "FEE 2025-12-31 16:00:00 USD 1.00 traded=2025-12-30"))
        self.assertIn("an INTEREST line's date", self._said(
            "INTEREST 2025-12-31 16:00:00 USD 12.40 traded=2025-12-30"))
        # The lines the writer writes it on take it.
        for line in ("TRANSFER 2025-12-31 09:30:00 SAMPL.US -20 USD 30 600",
                     "SPLIT 2025-12-31 09:30:00 SAMPLO.US SAMPLN.US 1",
                     "ADJUST 2025-12-31 16:00:00 SAMPL.US USD -1.00",
                     "ASSIGN 2025-12-31 16:00:00 SAMPL.US -100 USD 5 500"):
            with self.subTest(line=line):
                row = _parse_quiet(f"{line} traded=2025-12-30")
                self.assertEqual((row["date"], row["date_settle"]),
                                 ("2025-12-30", "2025-12-31"))

    def _said(self, line):
        with self.assertRaises(ValueError) as cm:
            _parse_quiet(line)
        return str(cm.exception)

    def test_an_opening_line_takes_neither(self):
        """Review P3-7: `OPENING … settled=` said "lot date … is not a
        valid date"."""
        for token in ("settled=2025-01-02", "traded=2024-12-30"):
            said = self._said(f"OPENING 2024-12-31 SAMPL.US 10 USD 100 "
                              f"{token}")
            self.assertIn(f"{token.split('=')[0]}= is not allowed on an "
                          f"OPENING line", said)

    def test_the_id_is_the_books_own_where_the_row_has_no_wording(self):
        """`date_settle` is part of a row's id: with the token the line
        reads back to the id the book's row computes (a row with no
        description; with one, the ids differ as they always did)."""
        row = _income_row(date="2025-12-31", date_settle="2026-01-02")
        row.pop("source")
        line = tx_to_tt_line(row)
        back = _parse_quiet(line)
        self.assertEqual(back["id"], TaxTransaction(**row).compute_id())
        bare = _parse_quiet(line.replace(" settled=2026-01-02", ""))
        self.assertNotEqual(bare["id"], back["id"])
        # A row whose two dates are one day gets no token and keeps the
        # id its line always had.
        same = _income_row(date="2025-09-30")
        self.assertNotIn("settled=", tx_to_tt_line(same))

    def test_every_row_line_takes_it_and_a_wrong_one_is_refused(self):
        for line in (
                "INTEREST 2025-12-31 16:00:00 USD 12.40",
                "INTEREST 2025-12-31 16:00:00 USD 12.40 symbol=SAMPLB.US",
                "FEE 2025-12-31 16:00:00 USD 1.00",
                "TAX 2025-12-31 10:00:00 SAMPL.US 10 USD 0 1.50",
                "TRANSFER 2025-12-31 09:30:00 SAMPL.US -20 USD 30 600 "
                "DECLARED",
                "SPLIT 2025-12-31 09:30:00 SAMPLO.US SAMPLN.US 1",
                "ADJUST 2025-12-31 16:00:00 SAMPL.US USD -1.00 type=roc",
                "BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0"):
            with self.subTest(line=line):
                row = _parse_quiet(f"{line} settled=2026-01-02")
                self.assertEqual((row["date"], row["date_settle"]),
                                 ("2025-12-31", "2026-01-02"))
                again = tx_to_tt_line(row, "trade")
                self.assertTrue(again.endswith(" settled=2026-01-02"), again)
        for line, said in (
                ("DIVIDEND 2025-12-31 10:00:00 SAMPL.US 10 USD 1 10 "
                 "settled=2025-12-30",
                 "settled=2025-12-30 is before the line's date 2025-12-31"),
                ("FEE 2025-12-31 16:00:00 USD 1.00 settled=2026-02-30",
                 "settled= '2026-02-30' is not a valid YYYY-MM-DD date"),
                ("FEE 2025-12-31 16:00:00 USD 1.00 settled=",
                 "is not a valid YYYY-MM-DD date"),
                ("BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                 "settled=2026-01-02 settled=2026-01-05",
                 "settled= is given twice")):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as cm:
                    _parse_quiet(line)
                self.assertIn(said, str(cm.exception))
                self.assertTrue(str(cm.exception).startswith("h.tt:1: "))


class TestEventsViewLines(unittest.TestCase):
    """TJ-5212: the `tjs events` view never prints a row no .tt line
    can say as a plain line: it ends with `not_tt=`, which the reader
    refuses if the line is pasted, saying why."""

    def _view(self, row, **kw):
        from taxjson.cli.listings import _tx_display_line
        return _tx_display_line(row, **kw)

    def test_a_move_in_kind_is_marked_and_refused_when_pasted(self):
        from taxjson.lib.row_types import IN_KIND_CONTRIBUTION_TYPE
        row = _row(action="BUYSELL", date="2025-08-08", symbol="SAMPL.US",
                   quantity=-10.0, currency="USD", price=10.0,
                   net_amount=100.0, fee=0.0, type=IN_KIND_CONTRIBUTION_TYPE)
        for kw in ({}, {"settle": True, "settled": True}):
            line = self._view(row, **kw)
            self.assertTrue(line.endswith(" not_tt=in_kind"), line)
            with self.assertRaises(ValueError) as cm:
                _parse_quiet(line)
            msg = str(cm.exception)
            self.assertTrue(msg.startswith(
                "h.tt:1: not_tt=in_kind: `tjs events` printed this line "
                "for a move in kind"), msg)
            self.assertIn("remove this line", msg)

    def test_a_rewards_fee_row_is_marked_and_refused_when_pasted(self):
        row = _row(action="FEE", date="2025-04-04", symbol="CASH",
                   currency="USD", net_amount=0.5,
                   description="Staking reward fee taken in SOL")
        line = self._view(row, settle=True, settled=True)
        self.assertEqual(line, "FEE 2025-04-04 10:00:00 USD 0.50 "
                               "not_tt=cash_leg")
        with self.assertRaises(ValueError) as cm:
            _parse_quiet(line)
        self.assertIn("not_tt=cash_leg: `tjs events` printed this line for "
                      "a row whose amount did not move as cash", str(
                          cm.exception))
        self.assertIn("INTEREST, FEE or TAX row", str(cm.exception))

    def test_a_row_that_settles_before_it_is_paid_is_marked(self):
        """Review P2: both views, and the pasted line is refused."""
        row = _income_row(date="2026-01-02", date_settle="2025-12-31")
        for kw in ({}, {"settle": True, "settled": True},
                   {"settled": True}):
            line = self._view(row, **kw)
            self.assertEqual(line, "DIVIDEND 2026-01-02 10:00:00 SAMPL.US 10 "
                                   "USD 1 10.00 not_tt=dates", kw)
        with self.assertRaises(ValueError) as cm:
            _parse_quiet(line)
        self.assertIn("not_tt=dates: `tjs events` printed this line for a "
                      "row whose two dates a line cannot say",
                      str(cm.exception))

    def test_an_unknown_marker_value_is_said(self):
        with self.assertRaises(ValueError) as cm:
            _parse_quiet("FEE 2025-04-04 10:00:00 USD 0.50 not_tt=whatever")
        self.assertIn("'whatever' is not one of its reasons (in_kind, "
                      "cash_leg, dates). The line is not a .tt row either "
                      "way: remove it", str(cm.exception))

    def test_check_tt_names_the_pasted_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin" / "pasted.tt"
            p.parent.mkdir()
            p.write_text(
                "FEE 2025-04-04 10:00:00 USD 0.50 not_tt=cash_leg\n"
                "DIVIDEND 2025-12-31 10:00:00 SAMPL.US 10 USD 1 10 "
                "settled=2026-01-02\n"
                "DIVIDEND 2025-12-31 10:00:00 SAMPL.US 10 USD 1 10 "
                "settled=2025-12-01\n"
                "BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                "traded=2025-12-30\n"
                "BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                "traded=2026-01-05\n"
                "BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                "traded=12-30\n"
                "BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                "traded=2025-12-29 traded=2025-12-30\n"
                "BUYSELL 2025-12-31 10:00:00 SAMPL.US 10 USD 10 100 0 "
                "traded=2025-12-30 settled=2026-01-02\n")
            doc = CT.check_paths([p], account="margin", crypto=False,
                                 shown=["pasted.tt"])
        errs = _errors(doc)
        self.assertEqual([e[0] for e in errs],
                         ["pasted.tt:1", "pasted.tt:3", "pasted.tt:5",
                          "pasted.tt:6", "pasted.tt:7", "pasted.tt:8"])
        self.assertIn("is after the line's date", errs[2][1])
        self.assertIn("not a valid YYYY-MM-DD date", errs[3][1])
        self.assertIn("traded= is given twice", errs[4][1])
        self.assertIn("on one line", errs[5][1])
        self.assertIn("not_tt=cash_leg", errs[0][1])
        self.assertIn("is before the line's date", errs[1][1])

    def test_an_ordinary_row_is_printed_as_before(self):
        row = _row(action="BUYSELL", date="2025-03-10",
                   date_settle="2025-03-11", symbol="SAMPL.US", quantity=10.0,
                   currency="USD", price=45.0, net_amount=459.95, fee=9.95)
        self.assertEqual(self._view(row), "BUYSELL 2025-03-10 10:00:00 "
                         "SAMPL.US 10 USD 45 459.95 9.95")
        # The one-account view (the lines to copy) on a trade-basis
        # project carries the settlement date too; on a settle-basis one
        # the line's date is it.
        self.assertTrue(self._view(row, settled=True).endswith(
            " 9.95 settled=2025-03-11"))
        self.assertEqual(self._view(row, settle=True, settled=True),
                         "BUYSELL 2025-03-11 10:00:00 SAMPL.US 10 USD 45 "
                         "459.95 9.95 traded=2025-03-10")
        # The all-accounts view (not for pasting) shows neither.
        self.assertNotIn("=", self._view(row, settle=True))
        paid = _income_row(date="2025-12-31", date_settle="2026-01-02")
        self.assertTrue(self._view(paid, settle=True, settled=True)
                        .endswith(" settled=2026-01-02"))


def _parse_quiet(line):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        return parse_tt_line(line, account_name="margin", source="h.tt:1")


def _parse(line):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        return parse_tt_line(line, account_name="margin", source="h.tt:1")


class TestHandWrittenLines(unittest.TestCase):
    """A line without the tokens reads as it did; the tokens are checked."""

    @rule(R.CA_FX_07)
    def test_a_line_without_the_keys_reads_as_before(self):
        # A trade and an income line move their amount in cash; an
        # ADJUST moves none (a T3's box 42: its cash came with the
        # distribution's own row).
        for line, legs in (
                ("BUYSELL 2025-03-03 10:00:00 SOL -1 USD 100 100 0",
                 [("USD", 100.0, "")]),
                ("BUYSELL 2025-03-03 10:00:00 ETH 0.05 USD 2000 100 0",
                 [("USD", -100.0, "")]),
                ("DIVIDEND 2025-04-04 10:00:00 SOL 0.1 USD 100 10",
                 [("USD", 10.0, "")]),
                ("ADJUST 2025-05-05 10:00:00 SAMPL.US USD -10 type=roc", []),
                ("BUYSELL 2025-09-09 10:00:00 SAMPL.US -0.5 USD 15 7.5 0 "
                 "corp=merger-2025-09-09-SAMPL", [])):
            with self.subTest(line=line):
                row = _parse(line)
                self.assertNotIn("cash_leg", row)
                self.assertNotIn("commission_refunds", row)
                self.assertEqual(_cash_leg_dates(row), legs)

    @rule(R.CA_FX_07)
    def test_the_keys_change_the_cash_and_not_the_id(self):
        for bare, token, legs in (
                ("BUYSELL 2025-03-03 10:00:00 SOL -1 USD 100 100 0",
                 "cash_leg=none", []),
                ("DIVIDEND 2025-04-04 10:00:00 SOL 0.1 USD 100 10",
                 "cash_leg=reward", []),
                ("DIVIDEND 2025-04-04 10:00:00 USD 12 USD 1 12",
                 "cash_leg=reward", [("USD", 12.0, "")]),
                ("ADJUST 2025-05-05 10:00:00 SAMPL.US USD -10 type=roc",
                 "cash_leg=cash", [("USD", 10.0, "")])):
            with self.subTest(line=bare, token=token):
                row = _parse(f"{bare} {token}")
                self.assertEqual(_cash_leg_dates(row), legs)
                self.assertEqual(row["id"], _parse(bare)["id"])

    def test_a_token_in_the_wrong_place_is_refused(self):
        for line, said in (
                ("BUYSELL 2025-03-03 10:00:00 SOL -1 USD 100 100 0 "
                 "cash_leg=maybe", "cash_leg=maybe: one of none"),
                ("BUYSELL 2025-03-03 10:00:00 SOL -1 USD 100 100 0 "
                 "cash_leg=cash", "amount is cash already"),
                ("ADJUST 2025-05-05 10:00:00 SAMPL.US USD -10 cash_leg=cash",
                 "the line needs type=roc too"),
                ("ADJUST 2025-05-05 10:00:00 SAMPL.US USD -10 type=roc "
                 "cash_leg=none", "moves no cash without it"),
                ("TAX 2025-04-04 10:00:00 SOL 0.1 USD 100 10 cash_leg=reward",
                 "only those take it"),
                ("DIVIDEND 2025-04-04 10:00:00 SOL 0.1 USD 100 10 "
                 "refunds=2025-02-14:1", "only a BUYSELL or an ASSIGN line"),
                ("BUYSELL 2025-01-13 10:00:00 SAMPL.US 10 USD 10 100 0 "
                 "refunds=2025-02-30:1", "refunds=2025-02-30:1: the "
                                         "commission refunds"),
                ("BUYSELL 2025-01-13 10:00:00 SAMPL.US 10 USD 10 100 0 "
                 "refunds=2025-02-14", "<date>:<amount>"),
                ("BUYSELL 2025-01-13 10:00:00 SAMPL.US 10 USD 10 100 0 "
                 "refunds=2025-02-14:0", "a number other than 0"),
                ("BUYSELL 2025-01-13 10:00:00 SAMPL.US 10 USD 10 100 0 "
                 "refunds=2025-02-14:1 refunds=2025-03-14:1",
                 "refunds= is given twice")):
            with self.subTest(line=line):
                with self.assertRaises(ValueError) as cm:
                    _parse(line)
                self.assertIn(said, str(cm.exception))
                self.assertTrue(str(cm.exception).startswith("h.tt:1: "))

    def test_a_corp_line_takes_cash_or_cash_leg_never_both(self):
        """Review P3-5: `corp=… cash_leg=cash cash=7.5:USD` was read,
        the cash_leg ignored (the ledgers read a corp row's `cash=`
        alone)."""
        base = ("BUYSELL 2025-09-09 10:00:00 SAMPL.US -0.5 USD 15 7.5 0 "
                "corp=merger-2025-09-09-SAMPL")
        with self.assertRaises(ValueError) as cm:
            _parse(f"{base} cash_leg=cash cash=7.5:USD")
        self.assertIn("cash_leg=cash: the line has cash= too", str(
            cm.exception))
        self.assertTrue(str(cm.exception).startswith("h.tt:1: "))
        self.assertEqual(_cash_leg_dates(_parse(f"{base} cash_leg=cash")),
                         [("USD", 7.5, "")])
        self.assertEqual(_cash_leg_dates(_parse(f"{base} cash=2.5:USD")),
                         [("USD", 2.5, "")])
        # A book row holding both is written with its cash= alone, and
        # moves the same cash.
        row = dict(_parse(f"{base} cash=2.5:USD"), cash_leg="cash")
        line = tx_to_tt_line(row)
        self.assertIn(" cash=2.5:USD", line)
        self.assertNotIn("cash_leg=", line)
        self.assertEqual(_cash_leg_dates(_parse(line)),
                         _cash_leg_dates(row))

    def test_refunds_are_read_in_date_order_with_other_tokens(self):
        row = _parse("ASSIGN 2025-01-13 10:00:00 SAMPLW.US -100 USD 0 0 0 "
                     "refunds=2025-03-14:0.25;2025-02-14:1 "
                     "exercise_of=SAMPL.US cash_leg=none")
        self.assertEqual(row["commission_refunds"],
                         "2025-02-14 1.0; 2025-03-14 0.25")
        self.assertEqual(row["exercise_of"], "SAMPL.US")
        self.assertEqual(row["cash_leg"], "none")
        again = tx_to_tt_line(row)
        self.assertTrue(again.endswith(
            " exercise_of=SAMPL.US cash_leg=none "
            "refunds=2025-02-14:1.0;2025-03-14:0.25"), again)

    def test_a_book_with_an_unknown_cash_leg_is_refused(self):
        from taxjson.lib.tx_load import coerce_transaction_row
        row = dict(roc_rows()[0], cash_leg="some")
        with self.assertRaises(ValueError) as cm:
            coerce_transaction_row(row, 0, "book.json")
        self.assertIn("cash_leg='some' is not one of none, reward, cash",
                      str(cm.exception))


class TestCheckTt(unittest.TestCase):
    """`tjs check-tt` reads the tokens as the run does."""

    def _check(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin" / "cash.tt"
            p.parent.mkdir()
            p.write_text(text)
            return CT.check_paths([p], account="margin", crypto=True,
                                  shown=["cash.tt"])

    def test_the_new_keys_are_accepted(self):
        doc = self._check(
            "BUYSELL 2025-03-03 10:00:00 SOL -1 USD 100 100 0 cash_leg=none\n"
            "DIVIDEND 2025-04-04 10:00:00 SOL 0.1 USD 100 10 "
            "cash_leg=reward\n"
            "ADJUST 2025-05-05 10:00:00 SAMPL.US USD -10 type=roc "
            "cash_leg=cash\n"
            "BUYSELL 2025-01-13 10:00:00 SAMPL.US 10 USD 10 100 0 "
            "refunds=2025-02-14:1.0\n")
        self.assertEqual(_errors(doc), [])

    def test_a_malformed_one_is_an_error_naming_its_line(self):
        doc = self._check(
            "BUYSELL 2025-03-03 10:00:00 SOL -1 USD 100 100 0 cash_leg=none\n"
            "BUYSELL 2025-03-03 10:00:00 ETH 0.05 USD 2000 100 0 "
            "cash_leg=nocash\n"
            "BUYSELL 2025-01-13 10:00:00 SAMPL.US 10 USD 10 100 0 "
            "refunds=Feb-14:1\n")
        errs = _errors(doc)
        self.assertEqual([e[0] for e in errs], ["cash.tt:2", "cash.tt:3"])
        self.assertIn("cash_leg=nocash: one of none", errs[0][1])
        self.assertIn("refunds=Feb-14:1", errs[1][1])


def _errors(doc):
    """[(where, message)] of a check-tt report's errors."""
    return [(p["where"], p["message"]) for p in doc["problems"]
            if p["severity"] == CT.ERROR]


if __name__ == "__main__":
    unittest.main()
