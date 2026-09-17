"""Conformance registrations for the six shipped brokerage parsers.

Adding a broker? Copy one of these three-line classes, add
tests/fixtures/<broker>/sample.csv (synthetic rows only), and run once
with UPDATE_GOLDEN=1 to mint the golden. See parser_conformance.py.
"""

import unittest

from parser_conformance import ParserConformance

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from taxjson.lib.brokerages.kraken import KrakenBrokerage
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from taxjson.lib.brokerages.webull import WebullBrokerage


class TestQuestradeConformance(ParserConformance, unittest.TestCase):
    parser_cls = QuestradeBrokerage
    fixture_dir = "questrade"
    expected_skips = 2          # FXT conversion (recognized non-event)
                                # + the synthetic unknown-action row;
                                # FCH is a FEE row now


class TestRbcConformance(ParserConformance, unittest.TestCase):
    parser_cls = RbcBrokerage
    fixture_dir = "rbc_direct"
    expected_skips = 1          # the synthetic unknown-activity row


class TestIbConformance(ParserConformance, unittest.TestCase):
    parser_cls = IbBrokerage
    fixture_dir = "ib"
    expected_skips = 1          # the Statement/BrokerName metadata row
                                # (recognized non-event); IB now does
                                # full row accounting


class TestWebullConformance(ParserConformance, unittest.TestCase):
    parser_cls = WebullBrokerage
    fixture_dir = "webull"
    expected_skips = None       # Webull keeps its skipped_actions summary


class TestKrakenConformance(ParserConformance, unittest.TestCase):
    parser_cls = KrakenBrokerage
    fixture_dir = "kraken"
    expected_skips = None       # Kraken keeps its ignored_types summary


class TestCoinbaseConformance(ParserConformance, unittest.TestCase):
    parser_cls = CoinbaseBrokerage
    fixture_dir = "coinbase"
    expected_skips = 0          # Receive now emits TRANSFER evidence
                                # (2026-09 custody-sidecar graduation)


if __name__ == "__main__":
    unittest.main()
