"""Moved from the core's tests/test_fix_l_ibparse.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

Regression tests for the low-round `ibparse` audit findings (IB
statement parser, shared broker helpers, taxjson-brokerage, broker
detection, the transaction schema) and the owner's overnight-session
item. Every fixture is synthetic: invented tickers and ISINs, fake
account ids marked pii-ok.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import unittest


_EX = _support.REPO_ROOT / 'examples'


class TestBrokerDetection(unittest.TestCase):
    """R1-57 / S029-14 / R1-90 / S059-17."""

    def setUp(self):
        self.ib = (_EX / 'ib_demo.csv').read_text(encoding='utf-8')
        self.rbc = (_EX / 'rbc_direct_demo.csv').read_text(encoding='utf-8')

    def test_fetch_accepts_what_detection_routes(self):
        from taxjson_fetch.api import looks_like_ib_statement
        self.assertTrue(looks_like_ib_statement(self.ib))
        self.assertFalse(looks_like_ib_statement(
            'Statement,Header,Field Name,Field Value\n'
            'Statement,Data,Notes,LIBOR Rate Source\n'))


if __name__ == "__main__":
    unittest.main()
