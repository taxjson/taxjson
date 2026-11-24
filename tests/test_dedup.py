"""Tests for `taxjson-sort --dedup`.

The dedup contract has two layers:

1. **Explicit id**: if a TaxTransaction has an `id`, that wins for the
   UID. Brokers that report per-fill txids (Kraken, Coinbase) use this
   to preserve every distinct execution.

2. **Content id**: when no `id` is set — none, null or a blank string
   (backlog TJ-5041) — the row's id is `TaxTransaction.compute_id()`,
   and taxjson_sort.generate_uid falls back to it too: action, date,
   time, symbol, quantity, money, account, type, settle date and
   description. It includes description, so a parser that calls
   `disambiguate_split_fills()` (mutates description with `[fill #N]`)
   produces distinct auto-ids and dedup keeps them. The old fallback,
   (brokerage, account, symbol, date, time), collapsed two different
   trades of one moment.

The scenarios below cover all three failure modes that cost us real
data in the past: byte-identical rows from a single split-fill order,
brokerages with no time precision (Questrade uses `12:00:00 AM` for
every trade), and explicit-id duplicates from re-importing the same
broker file twice.
"""
import unittest
from taxjson.bin.taxjson_sort import deduplicate, generate_uid
from taxjson.model.transaction import TaxTransaction


class TestDedup(unittest.TestCase):
    def test_dedup_collapses_same_explicit_id(self):
        """The original test — two rows with the same `id` collapse to one."""
        txs = [
            TaxTransaction(action="BUYSELL", date="2025-01-01", symbol="AAPL", quantity=10, id="123"),
            TaxTransaction(action="BUYSELL", date="2025-01-01", symbol="AAPL", quantity=10, id="123"),
            TaxTransaction(action="BUYSELL", date="2025-01-02", symbol="AAPL", quantity=20, id="456"),
        ]
        result = deduplicate(txs)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].id, "123")
        self.assertEqual(result[1].id, "456")

    def test_dedup_keeps_distinct_ids_with_same_other_fields(self):
        """Two trades with the same date/symbol/qty but different
        explicit ids (e.g. Kraken's per-fill txids) must NOT collapse.
        Otherwise legitimate split fills with broker-provided ids would
        vanish from the inventory pool."""
        txs = [
            TaxTransaction(action="BUYSELL", date="2025-01-01", time="10:00:00",
                           symbol="AAPL", quantity=10, price=150.0,
                           net_amount=1500.0, id="kraken-abc"),
            TaxTransaction(action="BUYSELL", date="2025-01-01", time="10:00:00",
                           symbol="AAPL", quantity=10, price=150.0,
                           net_amount=1500.0, id="kraken-def"),
        ]
        result = deduplicate(txs)
        self.assertEqual(len(result), 2,
                         "two trades with distinct broker txids must both survive dedup")
        self.assertEqual({t.id for t in result}, {"kraken-abc", "kraken-def"})

    def test_dedup_keeps_split_fills_with_fill_marker(self):
        """End-to-end regression for the split-fill bug.

        Two byte-identical Questrade rows (same date, no sub-second
        time, same symbol/qty/price) come into the engine with the
        parser-applied `[fill #2]` description marker on the second one.
        Because `compute_id()` includes description in its hash, the two
        auto-generated ids differ → dedup keeps both. Without the marker
        they'd collapse and the user would lose one real disposition."""
        txs = [
            TaxTransaction(action="BUYSELL", date="2025-10-15", time="00:00:00",
                           symbol="QZW.TO", quantity=-100, price=9.42,
                           net_amount=942.0, account="Margin"),
            TaxTransaction(action="BUYSELL", date="2025-10-15", time="00:00:00",
                           symbol="QZW.TO", quantity=-100, price=9.42,
                           net_amount=942.0, account="Margin",
                           description="[fill #2]"),
        ]
        # Sanity: auto-ids should already differ thanks to the description.
        self.assertNotEqual(txs[0].id, txs[1].id)
        result = deduplicate(txs)
        self.assertEqual(len(result), 2,
                         "[fill #2] marker must keep split fills distinct under --dedup")

    def test_dedup_collapses_unidentified_same_second_trades(self):
        """The known limitation: two trades that share brokerage,
        account, symbol, date, AND time AND have no explicit id and no
        description difference collapse to one. This is the case the
        parser-level disambiguate_split_fills() is meant to prevent
        upstream — this test documents the behavior so a future change
        that quietly improves UID granularity also updates the contract."""
        txs = [
            TaxTransaction(action="BUYSELL", date="2025-01-01", symbol="AAPL",
                           quantity=10, price=150.0, net_amount=1500.0),
            TaxTransaction(action="BUYSELL", date="2025-01-01", symbol="AAPL",
                           quantity=10, price=150.0, net_amount=1500.0),
        ]
        # The compute_id() hash is identical because every contributing
        # field is identical. So both rows produce the same id, and
        # generate_uid returns it for both.
        self.assertEqual(txs[0].id, txs[1].id)
        result = deduplicate(txs)
        self.assertEqual(len(result), 1)

    def test_synthetic_uid_separates_by_account(self):
        """Two rows that would otherwise collide (no id, same date/symbol/time)
        but belong to different accounts must NOT collapse. The synthetic
        UID includes account specifically because cross-account trades
        on the same security genuinely happen (Margin and TFSA both
        bought AAPL today)."""
        # Use plain TaxTransaction with id explicitly cleared so we test
        # the synthetic-UID path rather than the compute_id() path.
        tx1 = TaxTransaction(action="BUYSELL", date="2025-01-01",
                             symbol="AAPL", quantity=10, account="Margin")
        tx2 = TaxTransaction(action="BUYSELL", date="2025-01-01",
                             symbol="AAPL", quantity=10, account="TFSA")
        # Auto-ids differ (account is in compute_id), so dedup keeps both.
        self.assertNotEqual(tx1.id, tx2.id)
        result = deduplicate([tx1, tx2])
        self.assertEqual(len(result), 2)
        # And confirm the synthetic UID path also separates by account
        # — defensive against a future refactor that strips ids.
        tx1.id = None
        tx2.id = None
        self.assertNotEqual(generate_uid(tx1), generate_uid(tx2))


class TestBlankId(unittest.TestCase):
    """TJ-5041: a JSON row's `"id": ""` (or spaces) is no id. It was kept,
    so two different purchases of one moment shared the id "" and
    `taxjson-sort --dedup` kept one: 20 shares and their cost of 200 gone
    with no word. A null or absent id already gave each its content id."""

    _ROWS = [{"action": "BUYSELL", "date": "2025-02-03", "time": "10:00:00",
              "date_settle": "2025-02-03", "symbol": "QZRX.TO",
              "quantity": q, "price": 10, "net_amount": 10 * q,
              "currency": "CAD", "account": "margin"} for q in (10, 20)]

    def _book(self, blank):
        import json
        from _tmpfiles import private_tmpfile
        rows = [dict(r, id=blank) if blank is not ... else dict(r)
                for r in self._ROWS]
        with private_tmpfile("w", suffix=".json", delete=False) as f:
            f.write(json.dumps({"transactions": rows}))
        return f.name

    def test_a_blank_id_is_the_content_id(self):
        from pathlib import Path
        from taxjson.lib.tx_load import load_transactions
        absent = [t.id for t in load_transactions(Path(self._book(...)))]
        self.assertEqual(len(set(absent)), 2)
        for blank in ("", "   ", None):
            with self.subTest(id=blank):
                rows = load_transactions(Path(self._book(blank)))
                self.assertEqual([t.id for t in rows], absent)
                self.assertEqual(len(deduplicate(rows)), 2)
        self.assertEqual(TaxTransaction(action="BUYSELL", date="2025-01-01",
                                        symbol="QZA.TO", id=" ").id,
                         TaxTransaction(action="BUYSELL", date="2025-01-01",
                                        symbol="QZA.TO").id)

    def test_generate_uid_falls_back_to_the_content_id(self):
        a, b = (TaxTransaction(**{k: v for k, v in r.items()})
                for r in self._ROWS)
        a.id = b.id = ""
        self.assertNotEqual(generate_uid(a), generate_uid(b))
        self.assertEqual(generate_uid(a), a.compute_id())
        self.assertEqual(len(deduplicate([a, b])), 2)

    def test_sort_dedup_keeps_both_purchases(self):
        import contextlib
        import io
        import json
        from unittest import mock
        from taxjson.bin import taxjson_sort
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.argv", ["taxjson-sort", self._book(""),
                                     "--dedup"]), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            taxjson_sort.main()
        rows = json.loads(out.getvalue())["transactions"]
        self.assertEqual(sorted((r["quantity"], r["net_amount"])
                                for r in rows), [(10.0, 100.0), (20.0, 200.0)])
        self.assertTrue(all(r["id"] for r in rows), rows)


if __name__ == '__main__':
    unittest.main()
