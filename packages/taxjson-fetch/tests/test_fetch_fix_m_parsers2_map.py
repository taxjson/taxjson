"""Moved from the core's tests/test_fix_m_parsers2_map.py with `taxjson fetch` (now the
taxjson-fetch plugin). Original module docstring:

Medium-round fixes: ticker.map, taxjson-merge and taxjson fetch
(parsers2-map). Synthetic data only; fetch tests never touch the
network beyond a 127.0.0.1 stub and never read credentials.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import tempfile
import unittest
from pathlib import Path
from tax_rules import rule


class TestFetchRedirectRedacted(unittest.TestCase):
    """S031-14: the credentialed-redirect refusal printed the redirect
    URL with its query — the Flex token / Questrade refresh token."""

    def test_refusal_message_carries_no_query(self):
        import http.server
        import threading
        from unittest import mock
        from taxjson_fetch import api as F

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location",
                                 "https://new-host.invalid" + self.path)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with mock.patch.object(F, "_FLEX_BASE", base + "/Flex"):
                with self.assertRaises(RuntimeError) as cm:
                    F.flex_fetch("SYNTHFLEXTOKEN123", "123456",
                                 F.default_http_get, sleep=lambda s: None)
            self.assertNotIn("SYNTHFLEXTOKEN123", str(cm.exception))
            self.assertIn("redirect", str(cm.exception))
            with mock.patch.object(F, "_QT_LOGIN", base + "/oauth2/token"):
                with self.assertRaises(RuntimeError) as cm:
                    F.qt_refresh("SYNTHREFRESH987", F.default_http_get)
            self.assertNotIn("SYNTHREFRESH987", str(cm.exception))
        finally:
            srv.shutdown()


class TestQtCsvBlankMoney(unittest.TestCase):
    """R1-348: a missing/null netAmount was written as 0, so the strict
    parser dropped a dividend as an informational zero-net row."""

    def _act(self, **kw):
        a = {"tradeDate": "2025-05-15T00:00:00.000000-04:00",
             "settlementDate": "2025-05-15T00:00:00.000000-04:00",
             "action": "DIV", "symbol": "ABC.TO",
             "description": "ABC HOLDINGS INC CASH DIV ON 200 SHS",
             "quantity": 0, "price": 0, "grossAmount": 0,
             "commission": 0, "netAmount": 42.50, "currency": "CAD",
             "type": "Dividends"}
        a.update(kw)
        return a

    def _parse(self, act):
        from taxjson_fetch.api import qt_to_csv
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "questrade_2025.csv"
            p.write_text(qt_to_csv([act], "99000000"))
            return QuestradeBrokerage().parse_file(p)

    def test_missing_or_null_net_is_refused(self):
        a = self._act()
        a.pop("netAmount")
        with self.assertRaisesRegex(Exception, "Net Amount"):
            self._parse(a)
        with self.assertRaisesRegex(Exception, "Net Amount"):
            self._parse(self._act(netAmount=None))

    def test_explicit_zero_and_value_still_parse(self):
        from taxjson_fetch.api import qt_to_csv
        self.assertIn(",0,", qt_to_csv([self._act(netAmount=0.0)], "9"))
        txs = self._parse(self._act())
        self.assertEqual([t["net_amount"] for t in txs
                          if t["action"] == "DIVIDEND"], [42.5])


@rule("CA-SL-01")
class TestQtWindowCoversSuperficialLoss(unittest.TestCase):
    """S002-05 / S031-13: the default window ended Jan 15 of the next
    year and started Dec 15 — a Jan 16-30 (or Dec 1-14) repurchase in a
    fetch-only account was never seen, so a denied loss was allowed."""

    def test_year_window_is_dec1_to_jan31(self):
        from datetime import date
        from taxjson_fetch.api import qt_window
        for today in (date(2026, 2, 10), date(2026, 3, 1),
                      date(2026, 9, 29)):
            s, e = qt_window(None, None, today=today, year=2025)
            self.assertEqual(s, date(2024, 12, 1))
            self.assertEqual(e, min(today, date(2026, 1, 31)))


class TestLiveOptionSuffix(unittest.TestCase):
    """S031-12: a CDR (AMZN.TO) in the payload made the account's US
    option on AMZN Montreal-listed (.TO) while the books say .US."""

    def test_books_decide_the_option_suffix(self):
        from taxjson_fetch.api import positions_to_holdings_toml
        pos = [{"symbol": "AMZN.TO", "openQuantity": 10},
               {"symbol": "AMZN17Jan27C200.00", "openQuantity": -1}]
        txt = positions_to_holdings_toml(
            pos, "rrsp", "9", "2026-09-29 00:00:00",
            book_symbols={"AMZN270117C00200000.US", "AMZN.TO"})
        self.assertIn('"AMZN270117C00200000.US"', txt)
        # a .TO option in the books stays .TO even with no equity leg
        txt = positions_to_holdings_toml(
            [{"symbol": "BMO20Jan26C88.00", "openQuantity": -1}],
            "rrsp", "9", "2026-09-29 00:00:00",
            book_symbols={"BMO260120C00088000.TO"})
        self.assertIn('"BMO260120C00088000.TO"', txt)

    def test_book_equity_cdr_does_not_make_options_montreal(self):
        from taxjson_fetch.api import positions_to_holdings_toml
        txt = positions_to_holdings_toml(
            [{"symbol": "DLR16Jan27C150.00", "openQuantity": -1}],
            "rrsp", "9", "2026-09-29 00:00:00",
            book_symbols={"DLR.TO"})
        self.assertIn('"DLR270116C00150000.US"', txt)
        # a Montreal root learned from a .TO OPTION in the books still
        # suffixes another contract on that root .TO
        txt = positions_to_holdings_toml(
            [{"symbol": "BMO16Jan27C90.00", "openQuantity": -1}],
            "rrsp", "9", "2026-09-29 00:00:00",
            book_symbols={"BMO260120C00088000.TO"})
        self.assertIn('"BMO270116C00090000.TO"', txt)


if __name__ == "__main__":
    unittest.main()
