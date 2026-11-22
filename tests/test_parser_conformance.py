"""Conformance registrations: one class per fixture folder (the seven
parsers, IB and the generic importer included, and Kraken's ledgers
export beside its trades). The checks are the harness's
(tests/parser_conformance.py): the schema, the golden, determinism, row
accounting, detection through `tjs run`'s routing and the countries.

Adding a broker? Copy one of these three-line classes, add
tests/fixtures/<broker>/sample.csv (synthetic rows only), and run once
with UPDATE_GOLDEN=1 to mint the golden. See parser_conformance.py.
"""

import os
import unittest

from parser_conformance import ParserConformance

from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
from taxjson.lib.brokerages.ib_extractor import IbBrokerage
from taxjson.lib.brokerages.kraken import KrakenBrokerage
from taxjson.lib.brokerages.generic import GenericBrokerage
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
from taxjson.lib.brokerages.webull import WebullBrokerage
from _tmpfiles import isolate_layouts

isolate_layouts()



def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestGenericConformance(ParserConformance, unittest.TestCase):
    # The generic importer (step 5b: the tier-1 layout in the making): its
    # sidecar mapping names every target, the fixture one row of each but
    # interest and fee.
    parser_cls = GenericBrokerage
    fixture_dir = "generic"


class TestWealthsimpleLayoutConformance(ParserConformance, unittest.TestCase):
    # A broker shipped as a layout (src/taxjson/data/layouts/wealthsimple.toml,
    # step 5): the generic importer, no sidecar, the signature routes it.
    parser_cls = GenericBrokerage
    fixture_dir = "wealthsimple"


class TestQuestradeConformance(ParserConformance, unittest.TestCase):
    parser_cls = QuestradeBrokerage
    fixture_dir = "questrade"


class TestRbcConformance(ParserConformance, unittest.TestCase):
    parser_cls = RbcBrokerage
    fixture_dir = "rbc_direct"


class TestIbConformance(ParserConformance, unittest.TestCase):
    parser_cls = IbBrokerage
    fixture_dir = "ib"


class TestWebullConformance(ParserConformance, unittest.TestCase):
    parser_cls = WebullBrokerage
    fixture_dir = "webull"


class TestKrakenConformance(ParserConformance, unittest.TestCase):
    parser_cls = KrakenBrokerage
    fixture_dir = "kraken"


class TestKrakenLedgersConformance(ParserConformance, unittest.TestCase):
    parser_cls = KrakenBrokerage
    fixture_dir = "kraken_ledgers"


class TestCoinbaseConformance(ParserConformance, unittest.TestCase):
    parser_cls = CoinbaseBrokerage
    fixture_dir = "coinbase"


if __name__ == "__main__":
    unittest.main()
