"""The 2026-10-24 audit, the statistics views: `tjs stats` / `tjs
win-rates` (lib/trade_stats) and the statistics blocks of `tjs sum`
(bin/taxjson_sum_gains `summarize_gains`). Findings A07, A11, A12, A28
(the trade_stats half), A37, A44, A49, B34 and B35. Traders' views,
never a filing figure; the gains engine is untouched. Synthetic rows
only."""
import unittest

from taxjson.bin import taxjson_sum_gains as SG
from taxjson.lib import trade_stats as TS

CALL = "ZZQ260918C00010000.US"
CALL2 = "ZZQ260918C00020000.US"
PUT = "ZZQ260918P00010000.US"
INDEX_CALL = "ZZX260918C00010000.US"
STOCK = "ZZQ.US"


def tx(d, q, net=0, action="BUYSELL", sym=CALL, account="margin",
       time="10:00:00", **kw):
    """One base-book row, as the audit's probes build them."""
    return dict(date=d, time=time, account=account, symbol=sym,
                action=action, quantity=q, net_amount=net,
                price=1 if net else 0, currency="USD", **kw)


def gains(acct, **row):
    """A gains document with one share sale: 100 shares, gain 200."""
    base = dict(id="sale", date="2026-02-01", symbol=STOCK, account=acct,
                qty=100, gain=200, raw_gain=200)
    base.update(row)
    return {"transactions": [base]}


def closed(gains_docs, books):
    return TS.closed_trades(gains_docs, books, lambda d: True,
                            lambda t: t["date"])


def written(rows):
    return TS.written_option_trades(rows, lambda t: t["date"])


def by_class(res):
    return {t["cls"]: t["pnl"] for t in res["trades"]}


class TestTradeStats(unittest.TestCase):

    def test_A07_credits_keyed_by_account_and_row_id(self):
        """A07: Stats assignment credits leak across accounts sharing a
        transaction id. A row id is unique within one account's book:
        the other account's sale with the same id keeps its P/L."""
        a = [tx("2026-01-01", -1, 100), tx("2026-02-01", 1, action="ASSIGN"),
             tx("2026-02-01", -100, 1000, sym=STOCK, action="ASSIGN",
                id="sale")]
        b = [tx("2026-02-01", -100, 1000, sym=STOCK, id="sale",
                account="other")]
        r = closed({"margin": gains("margin"), "other": gains("other")},
                   {"margin": a, "other": b})
        self.assertEqual([(t["account"], t["pnl"]) for t in r["trades"]
                          if t["cls"] == "long-shares"],
                         [("margin", 100.0), ("other", 200.0)])

    def test_A11_transfer_moves_contracts_without_a_trade(self):
        """A11: Stats ignores option transfer-out and calls a new purchase
        a buyback. A TRANSFER takes the written contracts off the queue;
        the later purchase opens a long."""
        rows = [tx("2026-01-01", -1, 100), tx("2026-02-01", 1, action="TRANSFER"),
                tx("2026-03-01", 1, 100)]
        r = written(rows)
        self.assertEqual((r["trades"], r["unknown"]), ([], []))
        # A short that arrives by transfer has an unknown premium: its
        # buy-back is counted, not a trade (as an OPENING_BALANCE is).
        r = written([tx("2026-01-01", -1, action="TRANSFER"),
                     tx("2026-02-01", 1, 50)])
        self.assertEqual(r["trades"], [])
        self.assertEqual(len(r["unknown"]), 1)
        # A long transferred in is sold out of, never read as a write.
        r = written([tx("2026-01-01", 1, action="TRANSFER"),
                     tx("2026-02-01", -1, 100), tx("2026-03-01", 1, 50)])
        self.assertEqual((r["trades"], r["unknown"]), ([], []))

    def test_A12_split_scales_the_credit_before_its_legs_draw(self):
        """A12: Stats allocates call-assignment premium before scaling a
        split-delayed delivery. A 2:1 split between the assignment and
        two half deliveries: half the premium each."""
        credits = [dict(account="margin", underlying=STOCK, right="C",
                        shares=100, amount=100, date="2026-02-01", strike=10,
                        delivered=100)]
        rows = [tx("2026-02-02", 2, action="SPLIT", sym=STOCK),
                tx("2026-02-03", -100, 1000, sym=STOCK, action="ASSIGN",
                   id="sale"),
                tx("2026-02-04", -100, 1000, sym=STOCK, action="ASSIGN",
                   id="sale2")]
        self.assertEqual(TS.credits_by_sale(rows, credits),
                         {"sale": 50.0, "sale2": 50.0})

    def test_A28_rename_keeps_same_day_writes_in_stamp_order(self):
        """A28: Renaming an option series loses intraday FIFO ordering of
        open lots (the trade_stats half). A 09:00 write renamed into a
        series with a 10:00 write of the same day closes first."""
        rows = [tx("2026-01-01", -1, 100, time="09:00:00"),
                tx("2026-01-01", -1, 200, sym=CALL2, time="10:00:00"),
                tx("2026-01-02", 1, action="SPLIT", symbol_new=CALL2),
                tx("2026-01-03", 1, 50, sym=CALL2)]
        tr = written(rows)["trades"]
        self.assertEqual([(t["premium"], t["pnl"]) for t in tr],
                         [(100.0, 50.0)])
        # The other way round: the destination's older write stays first.
        rows[0]["time"], rows[1]["time"] = "10:00:00", "09:00:00"
        tr = written(rows)["trades"]
        self.assertEqual([(t["premium"], t["pnl"]) for t in tr],
                         [(200.0, 150.0)])

    def test_A37_delivered_shares_covering_a_short_carry_the_premium_once(self):
        """A37: Stats counts assigned put premium twice when delivered
        shares cover a short. The cover's gain already holds the premium
        (the engine cut the shares' cost): the credit comes out of that
        short-shares trade, and the total is premium plus the cover."""
        rows = [tx("2026-01-01", -100, 1100, sym=STOCK),
                tx("2026-01-02", -1, 100, sym=PUT),
                tx("2026-02-01", 1, action="ASSIGN", sym=PUT),
                tx("2026-02-01", 100, 1000, sym=STOCK, action="ASSIGN",
                   id="cover")]
        g = {"transactions": [dict(id="cover", date="2026-02-01", symbol=STOCK,
                                   account="margin", qty=100, gain=200,
                                   raw_gain=200, direction="SHORT")]}
        r = closed({"margin": g}, {"margin": rows})
        self.assertEqual(by_class(r),
                         {"written-options": 100.0, "short-shares": 100.0})
        # A delivery covering part of a short: that part's premium in the
        # cover, the rest in the later sale of the shares kept.
        credits = written(rows)["credits"]
        rows2 = rows + [tx("2026-03-01", -40, 480, sym=STOCK, id="sale")]
        self.assertEqual(TS.credits_by_sale(rows2, credits, {"cover": 60}),
                         {"cover": 60.0, "sale": 40.0})

    def test_A44_cash_settled_assignment_makes_no_share_credit(self):
        """A44: Cash-settled option assignment subtracts its premium from
        a later unrelated stock sale. The stock trades in the account,
        but no leg is within the engine's window (3 days before to 7
        after): the premium stays with the option trade, as the engine
        leaves it undrained."""
        rows = [tx("2026-01-01", -1, 100), tx("2026-02-01", 1, 50, action="ASSIGN"),
                tx("2026-03-01", -100, 1100, sym=STOCK, id="sale")]
        r = closed({"margin": gains("margin", date="2026-03-01")},
                   {"margin": rows})
        self.assertEqual(by_class(r),
                         {"written-options": 50.0, "long-shares": 200.0})
        # An index option: its underlying never trades as stock in the
        # account, so the engine's resolver calls it cash-settled.
        rows = [tx("2026-01-01", -1, 100, sym=INDEX_CALL),
                tx("2026-02-01", 1, 50, action="ASSIGN", sym=INDEX_CALL)]
        wo = written(rows)
        self.assertEqual(wo["credits"], [])
        self.assertEqual([(t["kind"], t["cash_settled"], t["pnl"])
                          for t in wo["trades"]], [("assigned", True, 50.0)])
        # A physically settled one is marked so, and its credit names the
        # stock line the engine pairs it with.
        rows = [tx("2026-01-01", -1, 100), tx("2026-02-01", 1, action="ASSIGN"),
                tx("2026-02-01", -100, 1000, sym=STOCK, action="ASSIGN",
                   id="leg")]
        wo = written(rows)
        self.assertEqual([(t["cash_settled"], t["pnl"]) for t in wo["trades"]],
                         [(False, 100.0)])
        self.assertEqual([c["underlying"] for c in wo["credits"]], [STOCK])

    def test_A49_put_credit_opens_at_the_delivery(self):
        """A49: Stats removes put premium from shares sold before the
        assigned shares arrive. The sale of the shares held before the
        delivery keeps its P/L; the sale after it loses the premium."""
        credits = [dict(account="margin", underlying=STOCK, right="P",
                        shares=100, amount=100, date="2026-02-01", strike=10,
                        delivered=100)]
        rows = [tx("2025-06-01", 100, 900, sym=STOCK, id="old-buy"),
                tx("2026-02-02", -100, 1100, sym=STOCK, id="old-sale"),
                tx("2026-02-03", 100, 1000, sym=STOCK, action="ASSIGN",
                   id="delivery"),
                tx("2026-02-04", -100, 1100, sym=STOCK, id="new-sale")]
        self.assertEqual(TS.credits_by_sale(rows, credits), {"new-sale": 100.0})
        # The audit's rows, without the earlier purchase: the same.
        self.assertEqual(TS.credits_by_sale(rows[1:], credits),
                         {"new-sale": 100.0})
        # A delivery dated before the option row (a broker posting the
        # shares first) opens the credit there, on strong identity.
        rows = [tx("2026-01-30", 100, 1000, sym=STOCK, action="ASSIGN",
                   id="delivery"),
                tx("2026-01-31", -100, 1100, sym=STOCK, id="sale")]
        self.assertEqual(TS.credits_by_sale(rows, credits), {"sale": 100.0})


class TestSumGains(unittest.TestCase):

    def test_B34_futures_gain_and_fee_in_one_block(self):
        """B34: Gain statistics put futures gains under Stocks but their
        fees under Futures. One Futures bucket holds both."""
        r = SG.summarize_gains({"transactions": [
            {"symbol": "F:ZZH6", "currency": "CAD", "action": "BUYSELL",
             "qty": 1, "gain": 10, "proceeds": 10, "cost": 0, "fee": 1}]})
        self.assertEqual(r["returns_by_asset"], {"Futures": {"CAD": {
            "dollars": [10.0], "days": [0], "fees": [1.0],
            "fee_shares": [1.0]}}})
        # The .sum prints the block, with the fee per contract.
        text = SG.format_report(r, no_color=True)
        self.assertIn("ASSET: Futures", text)
        self.assertIn("Avg Fee Per Contract:", text)
        self.assertNotIn("ASSET: Non-option", text)

    def test_B35_open_writes_are_matched_within_the_account(self):
        """B35: Per-trade option statistics match a buyback to another
        account's grant. Account b's buy-back folds into b's write."""
        rows = [{"symbol": CALL, "currency": "CAD", "action": "BUYSELL",
                 "date": "2026-01-02", "qty": 1, "gain": 100, "grant": True,
                 "account": "a"},
                {"symbol": CALL, "currency": "CAD", "action": "BUYSELL",
                 "date": "2026-01-03", "qty": 1, "gain": 200, "grant": True,
                 "account": "b"},
                {"symbol": CALL, "currency": "CAD", "action": "BUYSELL",
                 "date": "2026-01-04", "qty": 1, "gain": -50,
                 "direction": "SHORT", "account": "b",
                 "grant_closed": {"2026": {"units": 1}}}]
        r = SG.summarize_gains({"transactions": rows})
        self.assertEqual(r["returns_by_asset"]["Options"]["CAD"]["dollars"],
                         [100.0, 150.0])


if __name__ == "__main__":
    unittest.main()
