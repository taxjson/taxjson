"""Batch C regression pin (finding 82): a token or API response that is
valid JSON but not an object (an array, a string) is the one-line
RuntimeError `taxjson fetch` catches, never an AttributeError traceback.
Synthetic tokens and account numbers only; nothing touches the network.
"""
# First: puts the plugin, the core and its test helpers on sys.path.
import _support  # noqa: F401
import unittest

from taxjson_fetch import api


SESSION = {"api_server": "https://api01.iq.questrade.com/",
           "access_token": "synthetic-access"}


class TestNonObjectJson(unittest.TestCase):

    def test_token_exchange_array_or_scalar(self):
        for body in (b"[]", b'"x"', b"7", b"null"):
            with self.subTest(body=body):
                with self.assertRaises(RuntimeError) as cm:
                    api.qt_refresh("synthetic-token", lambda url: body)
                self.assertIn("not an object", str(cm.exception))
                self.assertNotIn("synthetic-token", str(cm.exception))

    def test_api_get_array(self):
        with self.assertRaises(RuntimeError) as cm:
            api.qt_positions(SESSION, "99900001", lambda url: b"[1, 2]")
        msg = str(cm.exception)
        self.assertIn("not an object", msg)
        self.assertNotIn("99900001", msg)          # the path stays masked

    def test_object_still_read(self):
        doc = b'{"positions": [{"symbol": "ZZQ.TO", "openQuantity": 1}]}'
        self.assertEqual(
            api.qt_positions(SESSION, "99900001", lambda url: doc),
            [{"symbol": "ZZQ.TO", "openQuantity": 1}])


if __name__ == "__main__":
    unittest.main()
