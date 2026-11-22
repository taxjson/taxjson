"""v0.17.0 pre-release security review pins (core side).

H1  install hints never name a PyPI package (taxjson / taxjson-fetch are
    not published there: a name-squatter's package would be installed).
M1  write-then-rename writers never write through a symlink planted at
    their temp name (`<out>.part`, `.migrate.part`) or at the final name.
L5  fill-crypto quotes the symbol into the Yahoo URL and closes the
    response.
I1  the [fx] extra's comment dates the Yahoo fallback like the code.

(L6, TAXJSON_OFFLINE stopping `tjs fetch`: test_fetch_dispatch.py.)
Synthetic data only; nothing here touches the network.

Formerly: test_fix_prerelease_security
"""
import io
import os
import sys
import unittest
from unittest import mock
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))


# ----------------------------------------------------------------- L5

class _Resp(io.BytesIO):
    closed_by_with = False

    def __exit__(self, *a):
        type(self).closed_by_with = True
        return super().__exit__(*a)


class TestFillCryptoUrl(unittest.TestCase):
    """Formerly: test_fix_prerelease_security.TestFillCryptoUrl"""
    def test_symbol_is_quoted_and_the_response_closed(self):
        from taxjson.lib import crypto_prices
        seen = []
        body = (b'{"chart": {"result": [{"indicators": {"quote": '
                b'[{"close": [123.5]}]}}]}}')

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            return _Resp(body)

        env = {k: v for k, v in os.environ.items() if k != "TAXJSON_OFFLINE"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(crypto_prices.urllib.request, "urlopen", fake_urlopen), \
                mock.patch.dict(crypto_prices.PROJECT_CRYPTO_IDS, {"ZZQ": "A/B?x=1#y"}):
            v = crypto_prices.get_crypto_price("ZZQ", "2025-01-02")
        self.assertEqual(v, 123.5)
        self.assertEqual(len(seen), 1)
        path = seen[0].split("?", 1)[0]
        self.assertTrue(path.endswith("/chart/A%2FB%3Fx%3D1%23y-USD"),
                        seen[0])
        self.assertTrue(_Resp.closed_by_with)


if __name__ == "__main__":
    unittest.main()
