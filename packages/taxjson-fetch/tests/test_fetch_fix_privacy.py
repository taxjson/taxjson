"""GitHub issue #81: an HTTP error's reason and body reached the error
message unsanitised, so a server or proxy that echoed the request URL
showed the refresh token, and an account id in a JSON message bypassed
the masked request path. Every token, id and value below is synthetic;
no request leaves the machine (http_get is injected)."""
import _support  # noqa: F401
import io
import unittest
import urllib.error

from taxjson_fetch import api

_REFRESH = "SyntheticRefreshTok3n_abc123"
_ACCESS = "SyntheticAccessTok3n_def456"
_FLEX = "123456789012345678901234"          # synthetic Flex token (digits)
_ACCT = "99900001"                           # synthetic Questrade number
_IB = "U9990001"                             # synthetic IB account  # pii-ok


def _raiser(code, reason, body):
    def get(url):
        raise urllib.error.HTTPError(url, code, reason, {},
                                     io.BytesIO(body(url).encode()))
    return get


class HttpErrorDetailScrubbed(unittest.TestCase):

    def _err(self, fn):
        with self.assertRaises(RuntimeError) as cm:
            fn()
        return str(cm.exception)

    def test_refresh_echoing_its_url_hides_the_token(self):
        msg = self._err(lambda: api.qt_refresh(
            _REFRESH, _raiser(400, "Bad request",
                              lambda u: "invalid request " + u)))
        self.assertNotIn(_REFRESH, msg)
        self.assertNotIn("refresh_token=Synth", msg)
        self.assertIn("HTTP 400", msg)          # still says what failed

    def test_token_echoed_in_json_and_reason_is_hidden(self):
        body = ('{"error":"invalid_grant","refresh_token":"%s",'
                '"detail":"token %s expired"}' % (_REFRESH, _REFRESH))
        msg = self._err(lambda: api.qt_refresh(
            _REFRESH, _raiser(400, "Bad token " + _REFRESH,
                              lambda u: body)))
        self.assertNotIn(_REFRESH, msg)
        self.assertIn("invalid_grant", msg)

    def test_account_id_in_body_is_masked(self):
        get = _raiser(403, "Forbidden", lambda u: (
            '{"message":"access denied for account %s"}' % _ACCT))
        msg = self._err(lambda: api._qt_get(
            "https://api01.iq.questrade.com/", _ACCESS,
            f"/v1/accounts/{_ACCT}/activities", get))
        self.assertNotIn(_ACCT, msg)
        self.assertIn("99***", msg)
        self.assertIn("access denied", msg)

    def test_access_token_and_bearer_echo_hidden(self):
        get = _raiser(401, "Unauthorized", lambda u: (
            "Authorization: Bearer %s rejected (%s)" % (_ACCESS, _ACCESS)))
        msg = self._err(lambda: api._qt_get(
            "https://api01.iq.questrade.com/", _ACCESS,
            f"/v1/accounts/{_ACCT}/positions", get))
        self.assertNotIn(_ACCESS, msg)

    def test_ib_account_in_body_is_masked(self):
        get = _raiser(500, "Server Error", lambda u: (
            "statement for %s unavailable" % _IB))
        msg = self._err(lambda: api.flex_fetch(_FLEX, "123456", get,
                                               sleep=lambda s: None))
        self.assertNotIn(_IB, msg)
        self.assertIn("U9***", msg)

    def test_flex_http_error_echoing_url_hides_token(self):
        msg = self._err(lambda: api.flex_fetch(
            _FLEX, "123456", _raiser(400, "Bad", lambda u: "bad " + u),
            sleep=lambda s: None))
        self.assertNotIn(_FLEX, msg)

    def test_flex_error_message_and_raw_fallback_hide_token(self):
        # SendRequest answering with an XML ErrorMessage, or with a body
        # that is not XML at all (the first 200 characters were shown).
        for body in (
                "<FlexStatementResponse><Status>Fail</Status><ErrorMessage>"
                "Token %s is invalid for account %s</ErrorMessage>"
                "</FlexStatementResponse>" % (_FLEX, _IB),
                "proxy says: GET /SendRequest?t=%s&q=1 denied" % _FLEX):
            msg = self._err(lambda: api.flex_fetch(
                _FLEX, "123456", lambda url: body.encode(),
                sleep=lambda s: None))
            self.assertNotIn(_FLEX, msg)
            self.assertNotIn(_IB, msg)

    def test_body_longer_than_the_limit_never_shows_a_cut_token(self):
        # Scrubbing after the cut left the token's head when the cut
        # fell inside it.
        pad = "x" * 290
        msg = self._err(lambda: api.qt_refresh(
            _REFRESH, _raiser(400, "Bad", lambda u: pad + " " + _REFRESH)))
        self.assertNotIn(_REFRESH[:6], msg)

    def test_urlerror_text_is_scrubbed(self):
        def get(url):
            raise urllib.error.URLError("proxy refused " + url)
        msg = self._err(lambda: api.qt_refresh(_REFRESH, get))
        self.assertNotIn(_REFRESH, msg)

    def test_scrub_keeps_ordinary_detail(self):
        s = api.scrub_detail('{"code":1017,"message":"Access token is '
                             'invalid"}', ())
        self.assertIn("1017", s)
        self.assertIn("Access token is invalid", s)


if __name__ == "__main__":
    unittest.main()
