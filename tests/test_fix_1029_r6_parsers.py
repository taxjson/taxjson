"""Broker parser fixes of 2026-10-29, round 6 (group parsers): new cases
of two earlier fixes of the day.

TJ-0661 (a Kraken instant trade whose first export holds both sides but
not every leg booked only that export's legs, the later leg suppressed
as "booked with its counter-leg"; a case of TJ-0525's cross-export
join) and TJ-0666 (a Questrade reversal cancelled only one of two
overlapping downloads' copies of its original when they spell the same
numbers differently, 10 and 10.0000; a case of TJ-0517's reversal plan).
Every fixture is SYNTHETIC: fake ledger and account ids, invented
tickers. Each test's files live in a folder of their own.
"""
import json
import os
import unittest

from test_fix_1029_r2_brokerparse import qrei, qt_parse, _buys
from test_fix_1029_r2_cryptoparse import _KL_H2, _brokerage


def setUpModule():
    # Crypto UTC stamps need a named zone outside a project.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _flat(text):
    """Whitespace-collapsed: where a message wraps depends on the width."""
    return " ".join(text.split())


def _trades(r):
    return sorted((t["symbol"], t["quantity"], t["net_amount"], t.get("id"))
                  for t in json.loads(r.stdout)["transactions"])


# One instant trade (refid R1): 2 SOL spent for 1 ETH, received in two
# settlements of 0.5 ETH, the second two seconds after midnight UTC.
_SPEND = "L1,R1,2025-12-31 23:59:58,spend,,currency,SOL,spot,-2,0,0,20,0,SOL\n"
_RECV1 = ("L2,R1,2025-12-31 23:59:59,receive,,currency,ETH,spot,0.5,0,0.5,"
          "10,0,ETH\n")
_RECV2 = ("L3,R1,2026-01-01 00:00:01,receive,,currency,ETH,spot,0.5,0,1,"
          "10,0,ETH\n")


class TestKrakenSplitSettlementAcrossExports(unittest.TestCase):
    """TJ-0661: the 2025 ledger export holds the spend and the first
    0.5 ETH receipt, the 2026 one the second 0.5 ETH. The 2025 export
    holds both sides, so it booked its own legs alone (2 SOL for 0.5
    ETH, proceeds 10), and the 2026 export's receipt was dropped as
    "booked with its counter-leg" at exit 0 under --strict. Every
    export holding a leg of the trade now books it from all its legs,
    once: the same 2 SOL for 1 ETH, proceeds 20, as one export."""

    _BOOKED = [("ETH", 1.0, 20.0, "R1-buy"), ("SOL", -2.0, 20.0, "R1-sell")]

    def test_one_export_control(self):
        r, _side = _brokerage({"kr_all.csv": _KL_H2 + _SPEND + _RECV1
                               + _RECV2}, "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_trades(r), self._BOOKED)

    def test_the_later_leg_is_joined(self):
        r, _side = _brokerage({"kr_2025.csv": _KL_H2 + _SPEND + _RECV1,
                               "kr_2026.csv": _KL_H2 + _RECV2},
                              "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_trades(r), self._BOOKED)
        err = _flat(r.stderr)
        self.assertIn("refid R1***: an instant trade whose legs are in "
                      "kr_2025.csv and kr_2026.csv — booked once, here.", err)
        self.assertIn("instant-trade leg booked with its counter-leg in "
                      "kr_2025.csv: 1", err)

    def test_file_order_does_not_matter(self):
        # The export holding the later leg comes first: it books the
        # trade, and the one holding both sides is not booked twice.
        r, _side = _brokerage({"kr_a_2026.csv": _KL_H2 + _RECV2,
                               "kr_b_2025.csv": _KL_H2 + _SPEND + _RECV1},
                              "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_trades(r), self._BOOKED)

    def test_an_export_holding_every_leg_books_it(self):
        # An all-history export beside the partial one: it books the
        # trade from its own rows; the partial one's legs are counted
        # as booked there.
        r, _side = _brokerage({"kr_a_2025.csv": _KL_H2 + _SPEND + _RECV1,
                               "kr_b_all.csv": (_KL_H2 + _SPEND + _RECV1
                                                + _RECV2)},
                              "kraken", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_trades(r), self._BOOKED)
        self.assertIn("instant-trade leg booked with its counter-leg in "
                      "kr_b_all.csv: 2", _flat(r.stderr))

    def test_a_restated_leg_is_still_refused(self):
        restated = _RECV1.replace(",0.5,0,0.5,10,", ",0.6,0,0.6,12,")
        r, _side = _brokerage({"kr_2025.csv": _KL_H2 + _SPEND + _RECV1,
                               "kr_2026.csv": _KL_H2 + restated + _RECV2},
                              "kraken")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("kr_2025.csv and kr_2026.csv both carry ledger txid "
                      "L2*** with DIFFERENT content", _flat(r.stderr))


class TestQuestradeReversalAcrossNumberSpellings(unittest.TestCase):
    """TJ-0666: a DRIP of 10 SYN for 100 is in two overlapping
    downloads, one writing Quantity 10 and Net Amount -100, the other
    10.0000 and -100.00, and the later one also holds its reversal. The
    copies were told apart by the cells as written, so the reversal
    cancelled one copy and the other stayed booked: 10 phantom shares
    costing 100, said nowhere. The copies are matched by value now."""

    def test_both_spellings_of_the_original_drop_out(self):
        txs, _err = qt_parse({
            'a.csv': qrei('2025-02-03', 'CAD', '10', '-100'),
            'b.csv': (qrei('2025-02-03', 'CAD', '10.0000', '-100.00')
                      + qrei('2025-03-03', 'CAD', '-10', '100'))})
        self.assertEqual(_buys(txs), [])

    def test_one_export_control(self):
        txs, _err = qt_parse({
            'a.csv': (qrei('2025-02-03', 'CAD', '10', '-100')
                      + qrei('2025-03-03', 'CAD', '-10', '100'))})
        self.assertEqual(_buys(txs), [])

    def test_two_reinvestments_in_one_export_stay_two(self):
        # Two rows of one file are two events however they are written:
        # the reversal cancels one of them.
        txs, _err = qt_parse({
            'a.csv': (qrei('2025-02-03', 'CAD', '10', '-100')
                      + qrei('2025-02-03', 'CAD', '10.00', '-100.00')
                      + qrei('2025-03-03', 'CAD', '-10', '100'))})
        self.assertEqual(_buys(txs), [('2025-02-03', 'SYN.TO', 'CAD', 10.0)])


if __name__ == "__main__":
    unittest.main()
