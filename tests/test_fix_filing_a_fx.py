"""fx-cash (ITA s.39(1.1)) ledger fixes, medium audit round, filing-a:
R1-147 (ASSIGN cash legs), R1-148 (either-direction caveat, year-end
pool), S003-05 (non-cash legs are not currency flows), S033-08
(futures move only their settled P/L)."""
import unittest

from taxjson.bin.taxjson_fx_cash import (apply_jurisdiction, build_ledger,
                                         render_report)
from taxjson.lib.core import TaxTransaction
from tax_rules import rule


def _tx(action, date, cur, net, qty=0.0, **kw):
    return dict(action=action, date=date, date_settle=date,
                time=kw.pop("time", "10:00:00"),
                symbol=kw.pop("symbol", "AAA.US"),
                quantity=qty, currency=cur, net_amount=net,
                account=kw.pop("account", "margin"), **kw)


def _rates(table):
    return lambda cur, d: table.get(d)


class TestAssignMovesCash(unittest.TestCase):
    """R1-147: Webull books an assignment's stock leg as ASSIGN with the
    strike cash; it spends USD exactly like a BUYSELL purchase."""

    def test_assign_stock_leg_spends_currency(self):
        r = _rates({"2025-01-10": 1.30, "2025-02-10": 1.36,
                    "2025-03-10": 1.40, "2025-04-10": 1.44})
        txs = [
            _tx("BUYSELL", "2025-01-10", "USD", 10000.0, qty=-100),
            _tx("ASSIGN", "2025-02-10", "USD", 10000.0, qty=100,
                symbol="DELL.US"),
            _tx("BUYSELL", "2025-03-10", "USD", 10000.0, qty=-100,
                symbol="DELL.US"),
            _tx("BUYSELL", "2025-04-10", "USD", 10000.0, qty=100),
        ]
        doc = build_ledger(txs, "CAD", {}, 2025, rate_of=r)
        # 10k @1.30 spent @1.36 (+600), 10k @1.40 spent @1.44 (+400).
        self.assertAlmostEqual(doc["net_gain"], 1000.0, places=2)
        self.assertEqual(doc["pools"], {})
        self.assertEqual(doc["overdrafts"], {})

    def test_zero_cash_option_assign_leg_is_ignored(self):
        r = _rates({"2025-01-10": 1.30})
        txs = [_tx("ASSIGN", "2025-01-10", "USD", 0.0, qty=1,
                   symbol="DELL250117C00100000.US")]
        doc = build_ledger(txs, "CAD", {}, 2025, rate_of=r)
        self.assertEqual(doc["per_currency"], {})


@rule("CA-FX-07")
@rule("US-FX-03")
class TestNonCashLegs(unittest.TestCase):
    """S003-05: swaps, in-kind rewards and stock-for-stock corporate
    actions move no foreign cash."""

    def _pool(self):
        return [_tx("BUYSELL", "2025-02-03", "USD", 60000.0, qty=-20,
                    symbol="ETH", account="kraken")]

    R = staticmethod(_rates({"2025-02-03": 1.47, "2026-06-01": 1.38}))

    def _net(self, extra):
        doc = build_ledger(self._pool() + extra, "CAD", {}, 2026,
                           rate_of=self.R)
        return doc

    def test_kraken_crypto_to_crypto_legs(self):
        doc = self._net([
            _tx("BUYSELL", "2026-06-01", "USD", 99000.0, qty=-30,
                symbol="ETH", account="kraken",
                description="Instant Trade (sell leg of crypto-to-crypto swap)"),
            _tx("BUYSELL", "2026-06-01", "USD", 99000.0, qty=1,
                symbol="BTC", account="kraken",
                description="Instant Trade (buy leg of crypto-to-crypto swap)"),
            _tx("BUYSELL", "2026-06-01", "USD", 500.0, qty=-1,
                symbol="SOL", account="kraken",
                description="Trade SOLETH (sell leg of crypto-to-crypto)"),
            _tx("BUYSELL", "2026-06-01", "USD", 500.0, qty=0.1,
                symbol="ETH", account="kraken",
                description="Trade SOLETH (counter leg of crypto-to-crypto)"),
        ])
        self.assertAlmostEqual(doc["net_gain"], 0.0, places=2)
        self.assertEqual(doc["per_currency"].get("USD", {}).get(
            "disposed", 0.0), 0.0)

    def test_coinbase_convert_legs(self):
        doc = self._net([
            _tx("BUYSELL", "2026-06-01", "USD", 5000.0, qty=-2,
                symbol="ETH", account="coinbase",
                description="Convert (sell leg): ETH to BTC"),
            _tx("BUYSELL", "2026-06-01", "USD", 5000.0, qty=0.05,
                symbol="BTC", account="coinbase",
                description="Convert (buy leg): ETH to BTC"),
        ])
        self.assertAlmostEqual(doc["net_gain"], 0.0, places=2)

    def test_coin_staking_reward_is_not_cash(self):
        doc = self._net([
            _tx("DIVIDEND", "2026-06-01", "USD", 8000.0, qty=50,
                symbol="SOL", account="kraken", gross_amount=8000.0,
                description="Staking Reward"),
            _tx("BUYSELL", "2026-06-01", "USD", 8000.0, qty=50,
                symbol="SOL", account="kraken",
                description="Staking Reward"),
        ])
        self.assertAlmostEqual(doc["net_gain"], 0.0, places=2)

    @rule("CA-CRYPTO-02")
    def test_fiat_and_stablecoin_rewards_stay_cash(self):
        doc = build_ledger(
            [_tx("DIVIDEND", "2026-06-01", "USD", 12.0, qty=12,
                 symbol="USDC", account="kraken", gross_amount=12.0,
                 description="Staking Reward")],
            "CAD", {}, 2026, rate_of=self.R)
        self.assertAlmostEqual(
            doc["per_currency"]["USD"]["acquired"], 12.0)

    def test_stock_for_stock_merger_and_spinoff_are_not_cash(self):
        doc = self._net([
            _tx("BUYSELL", "2026-06-01", "CAD", 50000.0, qty=-1000,
                symbol="SSL.TO", corp_event_id="ev1",
                description="Merger SSL.TO→RGLD.US (taxable)"),
            _tx("BUYSELL", "2026-06-01", "USD", 50000.0, qty=300,
                symbol="RGLD.US", corp_event_id="ev1",
                description="Merger SSL.TO→RGLD.US (taxable)"),
            _tx("BUYSELL", "2026-06-01", "USD", 20000.0, qty=100,
                symbol="SPIN.US", corp_event_id="ev2",
                description="Spin-off SPIN.US (s.86.1)"),
        ])
        self.assertAlmostEqual(doc["net_gain"], 0.0, places=2)

    def test_standalone_cash_in_lieu_leg_is_cash(self):
        doc = build_ledger(
            [_tx("BUYSELL", "2026-06-01", "USD", 42.5, qty=-0.5,
                 symbol="NEW.US", corp_event_id="ev3",
                 description="Merger OLD.US→NEW.US: cash-in-lieu for "
                             "0.5 fractional share(s) (x)")],
            "CAD", {}, 2026, rate_of=self.R)
        self.assertAlmostEqual(
            doc["per_currency"]["USD"]["acquired"], 42.5)


class TestFuturesNotional(unittest.TestCase):
    """S033-08: a futures round trip moves only its P/L and commissions,
    never the notional (fixed on main by the R1-204 settlement model)."""

    def test_notional_is_not_spent(self):
        def fut(date, qty, net, price):
            return TaxTransaction(
                action="BUYSELL", date=date, date_settle=date,
                time="10:00:00", symbol="F:CLZ5.US", quantity=qty,
                currency="USD", price=price, fee=2.0, net_amount=net,
                gross_amount=abs(qty) * price * 1000,
                account="IB").to_dict()
        r = _rates({"2025-01-10": 1.30, "2025-02-10": 1.40,
                    "2025-03-10": 1.35})
        txs = [_tx("BUYSELL", "2025-01-10", "USD", 100000.0, qty=-1000,
                   account="IB"),
               fut("2025-02-10", 1.0, 60002.0, 60.0),
               fut("2025-03-10", -1.0, 60998.0, 61.0)]
        doc = build_ledger(txs, "CAD", {}, 2025, rate_of=r,
                           country="canada")
        self.assertEqual(doc["overdrafts"], {})
        usd = doc["per_currency"]["USD"]
        self.assertLess(usd["disposed"], 100.0)
        self.assertLess(abs(doc["net_gain"]), 10.0)


class TestCaveats(unittest.TestCase):
    """R1-148: unseen conversions can move the figure either way; the
    report must say so and show the ledger's year-end balance."""

    def test_report_says_either_direction_and_shows_pool(self):
        r = _rates({"2025-01-10": 1.30, "2025-02-10": 1.40,
                    "2025-03-10": 1.35})
        txs = [_tx("BUYSELL", "2025-01-10", "USD", 10000.0, qty=-100),
               _tx("BUYSELL", "2025-02-10", "USD", 4000.0, qty=10),
               _tx("BUYSELL", "2025-03-10", "USD", 9000.0, qty=10)]
        doc = build_ledger(txs, "CAD", {}, 2025, rate_of=r)
        self.assertEqual(doc["overdrafts"], {"USD": 1})
        self.assertEqual(doc["pools_year_end"], {})   # overdrawn
        text = render_report(doc, "CAD", 2025, "canada",
                             apply_jurisdiction(doc["net_gain"], "canada"))
        self.assertNotIn("understates", text)
        self.assertIn("either direction", text)

    def test_clean_ledger_still_carries_the_conversion_caveat(self):
        r = _rates({"2025-01-10": 1.30, "2025-02-10": 1.40})
        txs = [_tx("BUYSELL", "2025-01-10", "USD", 10000.0, qty=-100),
               _tx("BUYSELL", "2025-02-10", "USD", 4000.0, qty=10)]
        doc = build_ledger(txs, "CAD", {}, 2025, rate_of=r)
        text = render_report(doc, "CAD", 2025, "canada",
                             apply_jurisdiction(doc["net_gain"], "canada"))
        self.assertIn("either direction", text)
        self.assertIn("6,000.00", text)     # year-end USD balance shown

    def test_pool_at_year_end_not_end_of_history(self):
        r = _rates({"2025-01-10": 1.30, "2026-02-10": 1.40})
        txs = [_tx("BUYSELL", "2025-01-10", "USD", 10000.0, qty=-100),
               _tx("BUYSELL", "2026-02-10", "USD", 10000.0, qty=100)]
        doc = build_ledger(txs, "CAD", {}, 2025, rate_of=r)
        self.assertAlmostEqual(
            doc["pools_year_end"]["USD"]["units"], 10000.0)


if __name__ == "__main__":
    unittest.main()
