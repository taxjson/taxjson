"""The 2026-10-24 audit's option-view findings (A05, A06, A22-A28,
A47, B45): `taxjson option-yield` (lib/option_yield), `taxjson
option-boundary` and the expired-options checker (lib/option_boundary;
handoff's timing check reads its lots), and the option close guard
(lib/option_close_check). Reports, not the gains engine. Synthetic rows
only."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import option_boundary as OB
from taxjson.lib import option_yield as OY
from taxjson.lib.core import TaxTransaction
from taxjson.lib.option_close_check import unbacked_option_closes

CALL = "ZZQ260918C00010000.US"      # strike 10, expiry 2026-09-18
NEW = "ZZQ260918C00020000.US"       # strike 20, the same expiry
OLD = "ZZQ260116C00010000.US"       # strike 10, expiry 2026-01-16


def row(d, q, net=0.0, action="BUYSELL", sym=CALL, time="10:00:00", **kw):
    r = dict(date=d, time=time, account="margin", symbol=sym, action=action,
             quantity=float(q), net_amount=float(net),
             price=1.0 if net else 0.0, currency="USD")
    r.update(kw)
    return r


def lots(rows):
    return OY.contracts([(r["account"], r) for r in rows])


def tx(r):
    r = dict(r, date_settle=r.get("date_settle", r["date"]))
    return TaxTransaction(**{k: v for k, v in r.items()
                             if k in TaxTransaction.__dataclass_fields__})


def doc(rows, start, end):
    return OY.analyze(lots(rows), start=start, end=end, scope="test")


class OptionYieldTest(unittest.TestCase):

    def test_a05_notional_exposure_is_priced_per_stretch(self):
        """A05: option yield rewrites pre-adjustment notional exposure at
        new strike."""
        rows = [row("2026-01-01", -1, 100),
                row("2026-01-11", 1, action="SPLIT", symbol_new=NEW)]
        # Ten days at strike 10 (1000), ten at strike 20 (2000).
        r, = doc(rows, "2026-01-01", "2026-01-20")["rows"]
        self.assertEqual(r["yield_basis"], "notional")
        self.assertAlmostEqual(r["avg_notional"], 1500.0)
        # A window before the adjustment is at the old strike, one
        # after it at the new.
        self.assertAlmostEqual(
            doc(rows, "2026-01-01", "2026-01-10")["rows"][0]["avg_notional"],
            1000.0)
        self.assertAlmostEqual(
            doc(rows, "2026-01-11", "2026-01-20")["rows"][0]["avg_notional"],
            2000.0)
        # A plain 2-for-1 split leaves the notional alone.
        rows = [row("2026-01-01", -1, 100),
                row("2026-01-11", 2, action="SPLIT")]
        self.assertAlmostEqual(
            doc(rows, "2026-01-01", "2026-01-20")["rows"][0]["avg_notional"],
            1000.0)

    def test_a06_transferred_out_contracts_leave_the_account(self):
        """A06: option yield ignores transferred-out contracts."""
        rows = [row("2026-01-01", -1, 100),
                row("2026-02-01", 1, action="TRANSFER")]
        d = doc(rows, "2026-01-01", "2026-02-28")
        r, = d["rows"]
        self.assertEqual(r["open_contracts"], 0)
        self.assertEqual(r["closed_contracts"], 1)
        self.assertEqual(r["outcomes"], {OY.TRANSFERRED: 1.0})
        self.assertEqual(r["transferred_out"], 1.0)
        # Nothing was realized: no P/L, no win, no yield on it.
        self.assertEqual(r["realized"], 0.0)
        self.assertIsNone(r["win_rate"])
        self.assertIsNone(r["yield"])
        c, = d["closed"]
        self.assertEqual((c["how"], c["premium"], c["close_cash"], c["pnl"]),
                         (OY.TRANSFERRED, 100.0, None, None))
        self.assertEqual(d["open"], [])
        text = OY.render(d).text()
        self.assertIn("1 transferred out (no P/L)", text)
        self.assertNotIn("past expiry", text)

    def test_a06_transferred_in_contract_has_no_premium_here(self):
        """A06: a contract arriving by TRANSFER opens with its premium
        unknown (it is in the books it came from), said so, and is not
        missing history."""
        rows = [row("2026-01-05", -1, action="TRANSFER"),
                row("2026-02-01", 1, 50)]
        lot, = lots(rows)
        self.assertTrue(lot.transfer_in and lot.unknown_cost)
        self.assertEqual(lot.side, OY.WRITTEN)
        d = doc(rows, "2026-01-01", "2026-02-28")
        r, = d["rows"]
        self.assertEqual((r["written_contracts"], r["transferred_in"]),
                         (0.0, 1.0))
        self.assertIsNone(r["realized"])
        self.assertEqual(r["unknown_cost"], 0)      # not missing history
        c, = d["closed"]
        self.assertEqual((c["how"], c["premium"], c["pnl"]),
                         (OY.BOUGHT_BACK, None, None))
        text = OY.render(d).text()
        self.assertIn("1 contract(s) received by transfer (premium "
                      "unknown", text)
        self.assertNotIn("before the data", text)

    def test_a06_zero_net_transfer_cluster_is_a_no_op(self):
        """A06: a custody move out and back (a cancel and rebook) is a
        no-op, as the gains run reads it: the contract stays open."""
        rows = [row("2026-01-01", -1, 100),
                row("2026-01-10", 1, action="TRANSFER"),
                row("2026-01-12", -1, action="TRANSFER")]
        lot, = lots(rows)
        self.assertEqual((lot.open_units, lot.closes), (1.0, []))
        r, = doc(rows, "2026-01-01", "2026-02-28")["rows"]
        self.assertEqual((r["open_contracts"], r["transferred_out"],
                          r["transferred_in"]), (1.0, 0.0, 0.0))

    def test_a22_opening_balance_comes_first_in_its_day(self):
        """A22: option yield sorts a midnight close before its synthetic
        opening."""
        close = row("2026-01-01", 1, 100, time="00:00:00")
        opening = row("2026-01-01", -1, action="OPENING_BALANCE",
                      time="00:00:00")
        for stamp in ("00:00:00", "23:00:00"):
            with self.subTest(opening_time=stamp):
                ls = lots([close, dict(opening, time=stamp)])
                self.assertEqual(len(ls), 1)
                r, = doc([close, dict(opening, time=stamp)],
                         "2026-01-01", "2026-01-02")["rows"]
                self.assertEqual((r["open_contracts"], r["closed_contracts"]),
                                 (0.0, 1.0))
                self.assertEqual(r["outcomes"], {OY.BOUGHT_BACK: 1.0})

    def test_a28_rename_keeps_the_intraday_fifo_order(self):
        """A28: renaming an option series loses intraday FIFO ordering of
        open lots."""
        rows = [row("2026-01-01", -1, 100, time="09:00:00"),
                row("2026-01-01", -1, 200, sym=NEW, time="10:00:00"),
                row("2026-01-02", 1, action="SPLIT", symbol_new=NEW),
                row("2026-01-03", 1, 50, sym=NEW)]
        ls = lots(rows)
        self.assertEqual([l.cash_open for l in ls if l.closes], [100.0])
        self.assertEqual([l.seq for l in ls], [0, 1])


class OptionBoundaryTest(unittest.TestCase):
    TODAY = date(2026, 10, 24)

    def straddling(self, rows, today=TODAY, year=2026):
        return OB.straddling([tx(t) for t in rows], year, "close", None,
                             today=today)

    def test_a23_premium_is_per_post_split_contract(self):
        """A23: option boundary multiplies the original premium after a
        contract split."""
        rows = [row("2025-12-01", -1, 100),
                row("2025-12-15", 2, action="SPLIT", symbol_new=NEW),
                row("2026-01-05", 2, 50, sym=NEW)]
        r, = self.straddling(rows)
        self.assertEqual((r["close_kind"], r["units"], r["premium"],
                          r["paid"]), ("buy-back", 2.0, 100.0, 50.0))
        self.assertIn("net 50.00 in 2026", r["where"])
        # Half the post-split contracts bought back: half the premium
        # each side.
        rows[-1] = row("2026-01-05", 1, 25, sym=NEW)
        self.assertEqual({(x["close_kind"], x["premium"])
                          for x in self.straddling(rows,
                                                   today=date(2026, 2, 1))},
                         {("buy-back", 50.0), ("open", 50.0)})
        lot, = OB.write_lots([tx(t) for t in rows])
        self.assertEqual((lot.units, lot.factor, lot.per_unit,
                          lot.closes[0].premium), (1.0, 2.0, 50.0, 50.0))

    def test_a24_renamed_lot_is_the_new_series(self):
        """A24: option boundary checks the old expiry after a series
        rename."""
        rows = [row("2025-12-01", -1, 100, sym=OLD),
                row("2026-01-02", 1, action="SPLIT", sym=OLD, symbol_new=CALL)]
        r, = self.straddling(rows, today=date(2026, 2, 1))
        self.assertEqual((r["symbol"], r["close_kind"], r["attention"]),
                         (CALL, "open", False))
        # ...and its expiry is the new series': past it, the row is
        # missing.
        r, = self.straddling(rows, today=date(2026, 10, 24))
        self.assertEqual((r["symbol"], r["close_kind"], r["attention"]),
                         (CALL, "expired?", True))

    def test_a25_rename_merges_queues_in_write_order(self):
        """A25: option boundary appends a renamed older lot after newer
        lots."""
        rows = [row("2025-11-01", -1, 100),
                row("2025-12-01", -1, 200, sym=NEW),
                row("2025-12-15", 1, action="SPLIT", symbol_new=NEW),
                row("2026-01-05", 1, 50, sym=NEW)]
        ls = OB.write_lots([tx(t) for t in rows])
        self.assertEqual([(l.premium, len(l.closes)) for l in ls],
                         [(100.0, 1), (200.0, 0)])

    def test_a26_short_opening_balance_is_the_first_lot_closed(self):
        """A26: option boundary closes a known write before an older
        unknown opening."""
        rows = [row("2025-11-01", -1, action="OPENING_BALANCE"),
                row("2025-12-01", -1, 100),
                row("2026-01-05", 1, 50)]
        ls = OB.write_lots([tx(t) for t in rows])
        self.assertEqual([(l.origin, l.unknown_premium, l.premium,
                           l.open_units, len(l.closes)) for l in ls],
                         [(OB.ORIGIN_BEFORE_DATA, True, 0.0, 0.0, 1),
                          (OB.ORIGIN_WRITE, False, 100.0, 1.0, 0)])
        by = {x["close_kind"]: x
              for x in self.straddling(rows, today=date(2026, 2, 1))}
        self.assertEqual(set(by), {"buy-back", "open"})
        # The unknown contract's close: no amount placed, said so.
        u = by["buy-back"]
        self.assertIsNone(u["premium"])
        self.assertTrue(u["attention"])
        self.assertIn("held before the data (2025-11-01)", u["where"])
        self.assertIn("buy-back in 2026", u["where"])
        self.assertIn("find-missing-history", u["action"])
        self.assertIn("premium's amount and year are unknown", u["action"])
        # The known write stays open with its premium.
        self.assertEqual((by["open"]["premium"], by["open"]["attention"]),
                         (100.0, False))
        # A long opening balance is still not a write (S044-09).
        rows = [row("2025-11-01", 1, action="OPENING_BALANCE"),
                row("2025-12-01", -1, 100)]
        self.assertEqual(OB.write_lots([tx(t) for t in rows]), [])

    def test_a26_unknown_open_lot_past_expiry_asks_for_both_rows(self):
        """A26: a before-data written contract still open past its
        expiry names the missing write and the missing expiry row."""
        rows = [row("2025-11-01", -1, action="OPENING_BALANCE")]
        r, = self.straddling(rows)
        self.assertEqual((r["close_kind"], r["premium"], r["attention"]),
                         ("expired?", None, True))
        self.assertIn("expired 2026-09-18", r["where"])
        self.assertIn("import the expiry, assignment or buy-back row",
                      r["action"])

    def test_a27_transferred_out_contract_is_not_open_past_expiry(self):
        """A27: expired-options checker reports transferred-out contracts
        as still open."""
        rows = [row("2025-12-01", -1, 100),
                row("2026-01-05", 1, action="TRANSFER")]
        self.assertEqual(OB.expired_open([tx(t) for t in rows], 2026,
                                         today=self.TODAY), [])
        # The boundary review says where it went instead of asking for
        # an expiry row.
        r, = self.straddling(rows)
        self.assertEqual((r["close_kind"], r["premium"], r["paid"]),
                         (OB.TRANSFERRED, 100.0, 0.0))
        self.assertIn("transferred out in 2026", r["where"])
        self.assertTrue(r["attention"])
        self.assertIn("receiving account's books", r["action"])
        # A custody move out and back is a no-op: the contract is still
        # held, and still open past its expiry.
        rows.append(row("2026-01-07", -1, action="TRANSFER"))
        self.assertEqual([(x["symbol"], x["quantity"]) for x in
                          OB.expired_open([tx(t) for t in rows], 2026,
                                          today=self.TODAY)],
                         [(CALL, -1.0)])

    def test_a27_short_arriving_by_transfer_has_no_premium_here(self):
        """A27: a written contract received by TRANSFER is a lot whose
        premium is in the sending account's books."""
        rows = [row("2026-01-05", -1, action="TRANSFER"),
                row("2026-02-01", 1, 50)]
        lot, = OB.write_lots([tx(t) for t in rows])
        self.assertEqual((lot.origin, lot.unknown_premium, lot.open_units),
                         (OB.ORIGIN_TRANSFER, True, 0.0))
        r, = OB.straddling([tx(t) for t in rows], 2026, "close", None,
                           today=self.TODAY) or [None]
        # Written and closed in 2026: no straddle, no row.
        self.assertIsNone(r)
        rows = [row("2025-12-05", -1, action="TRANSFER"),
                row("2026-02-01", 1, 50)]
        r, = self.straddling(rows)
        self.assertIsNone(r["premium"])
        self.assertIn("received by transfer on 2025-12-05", r["where"])
        self.assertIn("books of the account it came from", r["action"])

    def test_a47_one_accounts_purchase_never_closes_anothers_write(self):
        """A47: handoff option matching closes one account's write with
        another account's purchase."""
        rows = [row("2025-12-01", -1, 100),
                dict(row("2025-12-02", 1, 50), account="other")]
        ls = OB.write_lots([tx(t) for t in rows])
        self.assertEqual([(l.account, l.open_units, len(l.closes))
                          for l in ls], [("margin", 1.0, 0)])
        # Two accounts' same split is one event each.
        rows = [row("2025-12-01", -1, 100),
                dict(row("2025-12-01", -1, 100), account="other"),
                row("2025-12-15", 2, action="SPLIT"),
                dict(row("2025-12-15", 2, action="SPLIT"), account="other")]
        self.assertEqual([(l.account, l.open_units)
                          for l in OB.write_lots([tx(t) for t in rows])],
                         [("margin", 2.0), ("other", 2.0)])

    def test_a47_handoff_keeps_the_carried_write_of_each_account(self):
        """A47: handoff._timing_issues hands every taxable account's
        book to write_lots at once; another account's purchase used to
        close the carried write and the timing warning vanished."""
        from taxjson.lib.handoff import _timing_issues
        cfg = {"settings": {"year": 2026, "country": "canada",
                            "base_currency": "CAD",
                            "option_premium_timing": "grant",
                            "option_grant_timing_since": 2026},
               "accounts": {"margin": {"type": "taxable"},
                            "other": {"type": "taxable"}}}
        record = {"year": 2025,
                  "option_timing": {"option_premium_timing": "grant",
                                    "option_grant_since": 2025}}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            for name, rows in (("margin", [row("2025-12-01", -1, 100)]),
                               ("other", [dict(row("2025-12-02", 1, 50),
                                               account="other")])):
                (cache / f"{name}_base.json").write_text(json.dumps(
                    {"transactions": [dict(r, date_settle=r["date"])
                                      for r in rows]}))
            issues = _timing_issues(cache, cfg, record)
        self.assertEqual([(i["account"], i["symbol"], i["write_year"],
                           i["premium"]) for i in issues],
                         [("margin", CALL, 2025, 100.0)])
        self.assertIn("taxes it again when it closes", issues[0]["why"])


class OptionCloseCheckTest(unittest.TestCase):

    @staticmethod
    def t(q, time, code, d="2026-01-02"):
        return TaxTransaction(action="BUYSELL", date=d, date_settle=d,
                              time=time, account="margin", symbol=CALL,
                              quantity=q, net_amount=0.0, currency="USD",
                              open_close=code)

    def test_b45_a_later_clocked_opening_does_not_excuse_a_close(self):
        """B45: option close guard uses a later timestamped opening to
        hide missing history."""
        found = unbacked_option_closes([self.t(-1, "09:00:00", "C"),
                                        self.t(1, "15:00:00", "O")])
        self.assertEqual([(f["side"], f["date"], f["quantity"])
                          for f in found], [("sale", "2026-01-02", -1.0)])
        # Rows with no clock (RBC), midnight stamps (Questrade) or one
        # stamp can go either way: the day's opening still backs it.
        for close_t, open_t in (("", ""), ("00:00:00", "00:00:00"),
                                ("09:00:00", "09:00:00"), ("", "15:00:00")):
            with self.subTest(close=close_t, opening=open_t):
                self.assertEqual(unbacked_option_closes(
                    [self.t(-1, close_t, "C"), self.t(1, open_t, "O")]), [])


if __name__ == "__main__":
    unittest.main()
