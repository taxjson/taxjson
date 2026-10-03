"""Same-timestamp rows keep the export's row order (audit R1-30, owner
decision D7, partition Phase C).

Webull prints no clock time (every trade is stamped 09:30:00) and
Questrade stamps 00:00:00, so rows of one day tie. The Canada engine
used to put a BUY before a SELL at the same moment, which turned a
write + same-day buy-back (listed SELL first) into a long round trip.
Now plain trades at one moment follow the export's row order in both
engines, through every stage (parse, merge, sort, convert); the fixed
rungs (opening balance, split, assignment legs, denied-loss rows,
adjustments) keep their places.

All data synthetic (fake account ids, invented tickers).
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import country as C
from taxjson.lib.core import TaxTransaction
from taxjson.lib.corporate_timeline import (CaPriority, RadarPriority,
                                            UsPriority, event_sort_key,
                                            radar_priority)
from tax_rules import rule
from tax_rules.dual import gains_both, projects_both, tx

OPT = "XYZ260116C00050000.US"
GRANT = dict(option_premium_timing="grant", option_grant_since=2025)


def _t(qty, net, *, date="2025-10-01", sym=OPT, settle="2025-10-02",
       time="09:30:00"):
    return tx("BUYSELL", date, sym, qty, net, settle=settle, time=time)


def _write_buyback(sell_first):
    """A write (-1 @1.00) and its same-day buy-back (+1 @3.00) at one
    timestamp, in the given export order, then the same contract bought
    again five days later and held past day 30."""
    s, b = _t(-1, 100), _t(1, 300)
    again = _t(1, 250, date="2025-10-06", settle="2025-10-07")
    return ([s, b] if sell_first else [b, s]) + [again]


def _shape(res):
    return [(t["date"], t.get("direction"), round(t["gain"], 2))
            for t in res["transactions"]]


class TestTradesFollowExportOrder(unittest.TestCase):
    """The same book under both countries: the tie follows the order the
    rows arrive in, in each engine."""

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_write_then_buyback_is_a_short_round_trip(self):
        r = gains_both(_write_buyback(sell_first=True), year=2025,
                       canada=GRANT)
        ca, us = r[C.CANADA], r[C.USA]
        # Canada (grant timing): the premium on the write, the cost on
        # the buy-back; not superficial (option_buyback_loss_superficial
        # = false), so nothing is pushed into 2026.
        self.assertEqual(_shape(ca), [("2025-10-01", "SHORT", 100.0),
                                      ("2025-10-01", "SHORT", -300.0)])
        self.assertAlmostEqual(ca["summary"]["total_gain"], -200.0, places=2)
        self.assertAlmostEqual(ca["summary"]["total_disallowed"], 0.0,
                               places=2)
        # US (§1234 close timing): one short closed by the buy-back; a
        # later purchase is not a short sale, so §1091(e) does not wash it.
        self.assertEqual([(d, dr) for d, dr, _ in _shape(us)],
                         [("2025-10-01", "SHORT")])
        self.assertAlmostEqual(us["summary"]["total_gain"], -200.0, places=2)
        self.assertAlmostEqual(us["summary"]["total_disallowed"], 0.0,
                               places=2)

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_buy_then_sell_is_a_long_round_trip(self):
        r = gains_both(_write_buyback(sell_first=False), year=2025,
                       canada=GRANT)
        for c in C.COUNTRIES:
            with self.subTest(country=c):
                self.assertEqual([dr for _, dr, _ in _shape(r[c])],
                                 ["LONG"])
                # The re-buy within 30 days, still held: denied.
                self.assertAlmostEqual(r[c]["summary"]["total_disallowed"],
                                       200.0, places=2)

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_sell_then_rebuy_of_held_shares(self):
        # 100 held at $10; at one moment the export lists a sale of 100
        # at $12, then a purchase of 100 at $11. Canada: the sale is
        # against the old ACB (gain 200) and the new pool is $1,100;
        # buy-first averaged the purchase in first (gain 150). The US
        # FIFO lot is the old one either way.
        head = [tx("BUYSELL", "2025-03-03", "QZS.US", 100, 1000)]
        s = tx("BUYSELL", "2025-06-02", "QZS.US", -100, 1200,
               time="09:30:00")
        b = tx("BUYSELL", "2025-06-02", "QZS.US", 100, 1100,
               time="09:30:00")
        got = {}
        for label, book in (("sell_first", head + [s, b]),
                            ("buy_first", head + [b, s])):
            r = gains_both(book, year=2025)
            for c in C.COUNTRIES:
                got[(c, label)] = round(r[c]["summary"]["total_gain"], 2)
                if c == C.CANADA and label == "sell_first":
                    inv = {i["symbol"]: i for i in r[c]["inventory"]}
                    self.assertAlmostEqual(inv["QZS.US"]["total_cost"],
                                           1100.0, places=2)
        self.assertEqual(got[(C.CANADA, "sell_first")], 200.0)
        self.assertEqual(got[(C.CANADA, "buy_first")], 150.0)
        self.assertEqual(got[(C.USA, "sell_first")], 200.0)
        self.assertEqual(got[(C.USA, "buy_first")], 200.0)


def _ev(action, qty, symbol="ZZQ.US", time="10:00:00"):
    return TaxTransaction(action=action, date="2025-06-10", time=time,
                          quantity=qty, symbol=symbol, currency="CAD",
                          price=float(abs(qty)) + 1.0)


class TestFixedRungsStay(unittest.TestCase):
    """Only the plain-trade rung yields to the input order; every other
    tie-break keeps its place in both ladders, whatever the input order."""

    OPT = "ZZQ250620C00010000.US"

    def _orders(self, rows, profile):
        out = set()
        for perm in (rows, rows[::-1], rows[1:] + rows[:1]):
            out.add(tuple((t.action, t.quantity) for t in sorted(
                perm, key=lambda t: event_sort_key(t, profile=profile))))
        return out

    @rule("CA-DATE-14")
    def test_canada_ladder(self):
        # Every rung but the trades is fixed; trades are one rung.
        self.assertEqual(CaPriority.TRADE, 3)
        fixed = [_ev("OPENING_BALANCE", 100), _ev("DISALLOW", 0),
                 _ev("ASSIGN", -1, self.OPT), _ev("ASSIGN", 100),
                 _ev("ADJUST", 5)]
        self.assertEqual(self._orders(fixed, "ca_main"), {(
            ("OPENING_BALANCE", 100), ("DISALLOW", 0), ("ASSIGN", -1),
            ("ASSIGN", 100), ("ADJUST", 5))})
        # A split before the day's trades (at any clock time), trades
        # between the assignment legs and the adjustments.
        rows = [_ev("ADJUST", 5), _ev("BUYSELL", -100),
                _ev("SPLIT", 2.0, time="12:00:00"), _ev("ASSIGN", 100)]
        got = [(t.action, t.quantity) for t in sorted(
            rows, key=lambda t: event_sort_key(t, profile="ca_main"))]
        self.assertEqual(got, [("SPLIT", 2.0), ("ASSIGN", 100),
                               ("BUYSELL", -100), ("ADJUST", 5)])

    @rule("US-DATE-13")
    def test_us_ladder(self):
        fixed = [_ev("OPENING_BALANCE", 100), _ev("ASSIGN", -1, self.OPT),
                 _ev("ASSIGN", 100), _ev("SPLIT", 2.0), _ev("ADJUST", 5)]
        self.assertEqual(self._orders(fixed, "us_main"), {(
            ("OPENING_BALANCE", 100), ("ASSIGN", -1), ("ASSIGN", 100),
            ("SPLIT", 2.0), ("ADJUST", 5))})
        self.assertEqual(UsPriority.OTHER, 3)

    def test_trades_keep_input_order_in_every_engine_profile(self):
        for profile in ("ca_main", "ca_balance", "us_main"):
            for rows in ([_ev("BUYSELL", -100), _ev("BUYSELL", 100)],
                         [_ev("BUYSELL", 100), _ev("BUYSELL", -100)],
                         [_ev("BUYSELL", -1, self.OPT),
                          _ev("BUYSELL", 1, self.OPT),
                          _ev("BUYSELL", -1, self.OPT)]):
                with self.subTest(profile=profile, rows=len(rows)):
                    got = sorted(rows, key=lambda t: event_sort_key(
                        t, profile=profile))
                    self.assertEqual([t.id for t in got],
                                     [t.id for t in rows])

    def test_radar_ladder(self):
        # The wash radar replays the pool the engine's way: ADJUST first
        # (its deliberate divergence), then corporate events, then the
        # trades in input order.
        self.assertEqual(radar_priority(_ev("BUYSELL", 100)),
                         radar_priority(_ev("BUYSELL", -100)))
        self.assertEqual(radar_priority(_ev("BUYSELL", 100)),
                         RadarPriority.TRADE)
        self.assertLess(radar_priority(_ev("SPLIT", 2.0)),
                        RadarPriority.TRADE)
        self.assertLess(radar_priority(_ev("ADJUST", 0)),
                        radar_priority(_ev("SPLIT", 2.0)))

    def test_phantom_walk_stays_buy_first(self):
        # The missing-history walks are diagnostics: a same-moment pair
        # is not evidence of missing history, so they read it buys first
        # whatever the order (audit S075-12 / S076-04) — see phantom_walk.
        s, b = _ev("BUYSELL", -100), _ev("BUYSELL", 100)
        for rows in ([s, b], [b, s]):
            got = sorted(rows, key=lambda t: event_sort_key(
                t, profile="phantom_walk"))
            self.assertEqual([t.quantity for t in got], [100, -100])


class TestStagesPreserveRowOrder(unittest.TestCase):
    """sort/dedup, merge2 and convert-currency keep tied rows in input
    order (stable sorts, first-wins dedup)."""

    def _rows(self):
        return [TaxTransaction(action="BUYSELL", date="2025-10-01",
                               time="09:30:00", date_settle="2025-10-02",
                               symbol=OPT, quantity=q, price=p,
                               net_amount=abs(q) * p * 100, currency="USD",
                               account="margin")
                for q, p in ((-1, 1.0), (1, 3.0), (-1, 2.0), (1, 2.5))]

    def test_sort_and_dedup(self):
        from taxjson.bin.taxjson_sort import deduplicate, sort_transactions
        rows = self._rows()
        for inp in (rows, rows[::-1]):
            out = deduplicate(sort_transactions(inp))
            self.assertEqual([t.id for t in out], [t.id for t in inp])

    def test_convert_currency(self):
        from decimal import Decimal
        from taxjson.bin.taxjson_convert_currency import (
            process_transactions, reset_fallback_tally)
        reset_fallback_tally()
        rows = self._rows()
        with contextlib.redirect_stderr(io.StringIO()):
            out = process_transactions(list(rows), "CAD", {},
                                       Decimal("1.35"), country="canada")
        self.assertEqual([(t.quantity, round(t.price / 1.35, 6))
                          for t in out],
                         [(t.quantity, t.price) for t in rows])


# ------------------------------------------------------------ parsers
QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')
ACCT = '55500001'  # pii-ok (synthetic)


def _qrow(td, action, qty, price):
    gross = float(qty) * float(price)
    net = -gross
    return (f"{td} 12:00:00 AM,{td} 12:00:00 AM,{action},QZA,"
            f"QZA CORP WE ACTED AS AGENT,{qty},{price},{-gross:.2f},0,"
            f"{net:.2f},USD,{ACCT},Trades,Individual margin\n")


def _qt(body):
    from taxjson.lib.brokerages.questrade import QuestradeBrokerage
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "questrade_2025.csv"
        p.write_text(QH + body, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            ctx = QuestradeBrokerage.prepare_files([p])
            par = QuestradeBrokerage()
            par.account_context = ctx
            return par.parse_file(p)


def _generic(body):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_t.csv"
        c.write_text("Date,Type,Ticker,Shares,Price,Amount,Currency\n"
                     + body)
        c.with_name(c.name + ".toml").write_text(
            '[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
            'quantity="Shares"\nprice="Price"\namount="Amount"\n'
            'currency="Currency"\n[actions]\n"BUY"="buy"\n"SELL"="sell"\n')
        with contextlib.redirect_stderr(io.StringIO()):
            return GenericBrokerage().parse_file(c)


class TestParsersEmitChronologicalOrder(unittest.TestCase):
    """A parser emits one day's rows in the order they happened: file
    order for an oldest-first export, bottom-up for a newest-first one
    (RBC always did this; Questrade and the generic importer now too)."""

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_questrade_oldest_first_keeps_file_order(self):
        body = (_qrow("2025-03-03", "Buy", 10, 50)
                + _qrow("2025-06-02", "Sell", -10, 55)
                + _qrow("2025-06-02", "Buy", 10, 54))
        got = [(t["date"], t["quantity"]) for t in _qt(body)
               if t["action"] == "BUYSELL"]
        self.assertEqual(got, [("2025-03-03", 10), ("2025-06-02", -10),
                               ("2025-06-02", 10)])

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_questrade_newest_first_is_read_bottom_up(self):
        # The same activity exported newest first.
        body = (_qrow("2025-06-02", "Buy", 10, 54)
                + _qrow("2025-06-02", "Sell", -10, 55)
                + _qrow("2025-03-03", "Buy", 10, 50))
        got = [(t["date"], t["quantity"]) for t in _qt(body)
               if t["action"] == "BUYSELL"]
        self.assertEqual(got, [("2025-03-03", 10), ("2025-06-02", -10),
                               ("2025-06-02", 10)])

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_generic_both_directions(self):
        old_first = ("2025-03-03,BUY,QZG,10,50,500,USD\n"
                     "2025-06-02,SELL,QZG,10,55,550,USD\n"
                     "2025-06-02,BUY,QZG,10,54,540,USD\n")
        lines = old_first.splitlines(keepends=True)
        for body in (old_first, "".join(lines[::-1])):
            got = [(t["date"], t["quantity"]) for t in _generic(body)]
            self.assertEqual(got, [("2025-03-03", 10), ("2025-06-02", -10),
                                   ("2025-06-02", 10)])

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_one_date_file_keeps_file_order(self):
        # No second date to tell the direction: file order.
        body = ("2025-06-02,SELL,QZG,10,55,550,USD\n"
                "2025-06-02,BUY,QZG,10,54,540,USD\n")
        self.assertEqual([t["quantity"] for t in _generic(body)], [-10, 10])


# ------------------------------------------------ end to end (Webull)
_WB_PRE = (',,,,,,,,,\nAccount Number / Numéro de compte:,,,,,,,,55500001,\n'  # pii-ok
           'Year / Année:,,,,,,,,2025,\nReport / Rapport:,,,,,,,,'
           'TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,\n"DOE, JANE",,,,,,,,,\n')
_WB_HDR = ('"Currency\nDevise",Date,"Action Code\nCode d\'action",'
           '"Symbol\nSymbole","Security Description\nDescription des '
           'titres",Type Code of Securities Code de genre de titres,'
           '"Quantity of Securities Quantité\nde titres","Price\nPrix",,'
           'Proceeds of Disposition or Settlement Amount Produits de '
           'disposition\n')


def _wb_csv(sell_first):
    s = 'USD,02-10-2025,SELL,@XYZ,CALL XYZ01/16/26 50,OPC,-1,1.00,,100.00\n'
    b = 'USD,02-10-2025,BUY,,,,1,3.00,,(300.00)\n'
    if not sell_first:
        s, b = (b.replace(',,,,1', ',@XYZ,CALL XYZ01/16/26 50,OPC,1'),
                s.replace('@XYZ,CALL XYZ01/16/26 50,OPC', ',,'))
    return (_WB_PRE + _WB_HDR + ',,,,,,,,,\n' + s + b
            + 'USD,07-10-2025,BUY,,,,1,2.50,,(250.00)\n')


class TestWebullThroughRun(unittest.TestCase):
    """The owner's case end to end: a Webull export listing a write before
    its same-day buy-back, through `taxjson run` (parse, merge, sort,
    currency, engine) in a project of each country."""

    @rule("CA-DATE-14")
    @rule("US-DATE-13")
    def test_export_order_reaches_both_engines(self):
        from test_partition_engine_inputs import _run_offline, _usd_rates
        got = {}
        for sell_first in (True, False):
            with tempfile.TemporaryDirectory() as td:
                ps = projects_both(
                    Path(td), year=2025,
                    files={"inputs/margin/wb_2025.csv": _wb_csv(sell_first),
                           "ticker.map": ""},
                    canada={"source_currencies": ["USD"],
                            "option_premium_timing": "grant",
                            "option_grant_timing_since": 2025})
                _usd_rates(ps[C.CANADA] / "work" / "to_base.csv")
                for c, root in ps.items():
                    r = _run_offline(root, td, "run", "--no-input")
                    self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                    g = json.loads((root / "work" / "margin_gains.json")
                                   .read_text())
                    got[(c, sell_first)] = [
                        t.get("direction") for t in g["transactions"]]
        self.assertEqual(got[(C.CANADA, True)], ["SHORT", "SHORT"])
        self.assertEqual(got[(C.USA, True)], ["SHORT"])
        for c in C.COUNTRIES:
            self.assertEqual(got[(c, False)], ["LONG"], c)


if __name__ == "__main__":
    unittest.main()
