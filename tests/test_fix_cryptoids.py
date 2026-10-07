"""No built-in crypto id table: a coin's Yahoo id is `<SYMBOL>-USD`
unless the project's ticker.map maps it with `CRYPTO SYMBOL YAHOO_ID`.

A coin whose default id does not resolve is named with the exact line
to add; a coin priced under its default id while the price cache holds
a numbered id of the same ticker (the project was priced under it
before), or whose Yahoo closes are far off its own trade prices, is an
ATTENTION line `taxjson run` echoes to the console.

Fictional coins only (QZT, QZX); no network: urlopen is stubbed, and
the end-to-end runs use a price cache under a temporary HOME with
TAXJSON_OFFLINE set.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tax_rules import rule

from test_fix_crypto import KR_LEDGER_H, _env, _project, _run_cli

import taxjson.bin.fill_crypto_prices as fc


def _fill(rows, tmp, *, cache=None, ticker_map=None, fetch=None):
    """Run fill-crypto in-process over `rows` with the project root
    `tmp`; returns (rows out, stderr)."""
    tmp = Path(tmp)
    inp = tmp / "in.json"
    inp.write_text(json.dumps({"transactions": rows}))
    cache_file = tmp / "cache.json"
    if cache is not None:
        cache_file.write_text(json.dumps(cache))
    if ticker_map is not None:
        (tmp / "ticker.map").write_text(ticker_map)
    saved = (fc.CACHE_FILE, fc.get_crypto_price, sys.argv, fc.time.sleep)
    fc.CACHE_FILE = str(cache_file)
    fc.get_crypto_price = fetch or (lambda s, d: 0.0)
    fc.time.sleep = lambda s: None
    sys.argv = ["fill-crypto", "--project-root", str(tmp), str(inp)]
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            fc.main()
    finally:
        (fc.CACHE_FILE, fc.get_crypto_price, sys.argv,
         fc.time.sleep) = saved
    return json.loads(out.getvalue())["transactions"], err.getvalue()


def _reward(sym="QZT", day="2026-03-02", qty=2.0):
    return {"action": "DIVIDEND", "date": day, "symbol": sym,
            "quantity": qty, "price": 0.0, "net_amount": 0.0,
            "currency": "USD"}


def _buy(sym="QZT", day="2026-03-01", qty=1.0, price=100.0, ccy="USD"):
    return {"action": "BUYSELL", "date": day, "symbol": sym,
            "quantity": qty, "price": price, "net_amount": -qty * price,
            "currency": ccy}


class TestNoBuiltinTable(unittest.TestCase):
    def test_no_table_in_the_module(self):
        self.assertFalse(hasattr(fc, "SYMBOL_OVERRIDES"))
        self.assertEqual(fc.PROJECT_CRYPTO_IDS, {})
        self.assertEqual(fc.yahoo_id("QZT"), "QZT")
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(fc.load_symbol_overrides([td]), {})

    def test_crypto_line_is_the_only_id_and_takes_the_full_pair(self):
        from taxjson.lib.ticker_map import side_rules_in
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "ticker.map").write_text(
                "CRYPTO QZT QZT55504-USD   # pasted as Yahoo shows it\n"
                "CRYPTO QZX qzx55505\n")
            self.assertEqual(side_rules_in([td]).crypto,
                             {"QZT": "QZT55504", "QZX": "qzx55505"})

    def test_init_stub_names_no_real_coin(self):
        from taxjson.lib.ticker_map_format import init_template
        crypto = [ln for ln in init_template().splitlines()
                  if ln.startswith("# CRYPTO ")]
        self.assertEqual(crypto, ["# CRYPTO ABC ABC12345"])


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDefaultIdFailureNamesTheLine(unittest.TestCase):
    def _price(self, symbol, ids=None):
        def not_found(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found",
                                         {}, None)
        err = io.StringIO()
        env = {k: v for k, v in os.environ.items()
               if k != "TAXJSON_OFFLINE"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(fc.urllib.request, "urlopen", not_found), \
                mock.patch.dict(fc.PROJECT_CRYPTO_IDS, ids or {}), \
                contextlib.redirect_stderr(err):
            p = fc.get_crypto_price(symbol, "2026-03-02")
        return p, err.getvalue()

    def test_default_id_failure_names_coin_and_line(self):
        p, err = self._price("QZX")
        self.assertEqual(p, 0.0)
        self.assertIn("QZX on 2026-03-02 (Yahoo QZX-USD)", err)
        self.assertIn("`CRYPTO QZX QZX<number>`", err)
        self.assertIn("finance.yahoo.com", err)
        self.assertIn("`QZX<number>-USD`", err)

    def test_mapped_id_failure_has_no_add_a_line_hint(self):
        p, err = self._price("QZX", {"QZX": "QZX55505"})
        self.assertEqual(p, 0.0)
        self.assertIn("(Yahoo QZX55505-USD)", " ".join(err.split()))
        self.assertNotIn("CRYPTO QZX", err)

    def test_unpriced_summary_names_the_line_per_coin(self):
        with tempfile.TemporaryDirectory() as td:
            out, err = _fill([_reward("QZX")], td)
        self.assertEqual(out[0]["price"], 0.0)
        self.assertIn("UNPRICED", err)
        self.assertIn("QZX: if Yahoo lists QZX under another id", err)
        self.assertIn("`CRYPTO QZX QZX<number>`", err)


class TestCacheEvidence(unittest.TestCase):
    """A project priced under a numbered id before (the removed table
    gave four coins one): without a CRYPTO line the coin now goes to
    `<SYMBOL>-USD`, which may be another asset — said, with the line."""

    CACHE = {"QZT55504-2026-03-02": 100.0, "QZT-2026-03-02": 0.01,
             "QZT55504-2026-02-01": 90.0}

    def test_warns_with_the_exact_line(self):
        with tempfile.TemporaryDirectory() as td:
            out, err = _fill([_reward()], td, cache=self.CACHE)
        self.assertEqual(out[0]["price"], 0.01)       # not silently fixed
        att = [ln for ln in err.splitlines()
               if ln.startswith(fc.ATTENTION_CRYPTO_ID)]
        self.assertEqual(len(att), 1, err)
        self.assertIn("QZT has no CRYPTO line", att[0])
        self.assertIn("Yahoo QZT55504-USD", att[0])
        self.assertIn("finance.yahoo.com", att[0])
        self.assertIn("\n    CRYPTO QZT QZT55504\n", err + "\n")

    def test_silent_once_the_line_is_there(self):
        with tempfile.TemporaryDirectory() as td:
            out, err = _fill([_reward()], td, cache=self.CACHE,
                             ticker_map="CRYPTO QZT QZT55504\n")
        self.assertEqual(out[0]["price"], 100.0)
        self.assertNotIn("ATTENTION", err)

    def test_a_short_number_or_other_coin_is_not_evidence(self):
        # QZT2 is another coin's ticker, not a numbered Yahoo id.
        with tempfile.TemporaryDirectory() as td:
            _, err = _fill([_reward()], td, cache={
                "QZT-2026-03-02": 5.0, "QZT2-2026-03-02": 7.0,
                "QZTX55504-2026-03-02": 9.0})
        self.assertNotIn("ATTENTION", err)


class TestCryptoSendsLookupEvidence(unittest.TestCase):
    """crypto-sends' fair-value lookup (a gift or payment the exchange
    did not price) asks the same question: its `.attention` carries the
    line, which `taxjson run` and `taxjson crypto-sends` print."""

    def _lookup(self, td, ticker_map=None):
        from taxjson.lib import crypto_sends as CS
        cache = Path(td) / "cache.json"
        cache.write_text(json.dumps(TestCacheEvidence.CACHE))
        if ticker_map:
            (Path(td) / "ticker.map").write_text(ticker_map)
        with mock.patch.object(fc, "CACHE_FILE", str(cache)), \
                mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "1"}):
            fn = CS.yahoo_usd_price(Path(td))
            got = fn("QZT", "2026-03-02"), fn("QZT", "2026-02-01")
        return fn, got

    def test_default_id_with_numbered_history_is_said_once(self):
        with tempfile.TemporaryDirectory() as td:
            fn, got = self._lookup(td)
        self.assertEqual(got[0], (0.01, "QZT"))
        self.assertEqual(len(fn.attention), 1)
        self.assertIn("\n    CRYPTO QZT QZT55504", fn.attention[0])

    def test_quiet_with_the_line(self):
        with tempfile.TemporaryDirectory() as td:
            fn, got = self._lookup(td, "CRYPTO QZT QZT55504\n")
        self.assertEqual(got, ((100.0, "QZT55504"), (90.0, "QZT55504")))
        self.assertEqual(fn.attention, [])


class TestImplausibleYahooPrice(unittest.TestCase):
    """No cache history at all: a default id that is another asset is
    caught against the coin's own broker prices near the date."""

    def test_far_off_close_is_attention(self):
        with tempfile.TemporaryDirectory() as td:
            _, err = _fill([_buy(price=100.0), _reward()], td,
                           cache={"QZT-2026-03-02": 0.02})
        att = [ln for ln in err.splitlines()
               if ln.startswith(fc.ATTENTION_CRYPTO_ID)]
        self.assertEqual(len(att), 1, err)
        self.assertIn("QZT priced from Yahoo QZT-USD at 0.02 USD on "
                      "2026-03-02", att[0])
        self.assertIn("priced at 100 USD on 2026-03-01", att[0])
        self.assertIn("`CRYPTO QZT QZT<number>`", err)

    def test_wrong_crypto_line_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            _, err = _fill([_buy(price=100.0), _reward()], td,
                           cache={"QZT9999-2026-03-02": 4000.0},
                           ticker_map="CRYPTO QZT QZT9999\n")
        self.assertIn("`CRYPTO QZT QZT9999` names the wrong Yahoo id", err)

    def test_plausible_or_unrelated_prices_are_quiet(self):
        with tempfile.TemporaryDirectory() as td:
            # CAD price of the same coin, a day apart: within range.
            _, err = _fill([_buy(price=140.0, ccy="CAD"), _reward()], td,
                           cache={"QZT-2026-03-02": 104.0})
            self.assertNotIn("ATTENTION", err)
            # A coin-quoted trade (price in BTC) is no yardstick, and a
            # trade weeks away is not near.
            _, err = _fill([_buy(price=0.001, ccy="BTC"),
                            _buy(day="2026-01-01", price=1.0), _reward()],
                           td, cache={"QZT-2026-03-02": 104.0})
            self.assertNotIn("ATTENTION", err)


@rule("CA-CRYPTO-07")
class TestRunWarnsWithoutTheLine(unittest.TestCase):
    """`taxjson run` on a project whose coin was priced under a numbered
    id before, with no CRYPTO line: an ATTENTION on the console (also
    under --fast) and in the .sum — never a silent re-price. With the
    line: the numbered id's price, no ATTENTION."""

    LEDGER = (KR_LEDGER_H
              + "LX1,RX1,2026-03-02 12:00:00,earn,reward,currency,crypto,"
                "QZT,spot / main,2.0,0,2\n")

    def _filled_price(self, root):
        rows = json.loads((root / "work" / "crypto_filled.json")
                          .read_text())["transactions"]
        return [r["price"] for r in rows if r["action"] == "DIVIDEND"][0]

    def test_run_warns_then_line_fixes(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".crypto_price_cache.json").write_text(json.dumps(
                {"QZT-2026-03-02": 0.01, "QZT55504-2026-03-02": 100.0}))
            root = _project(td)
            (root / "inputs" / "crypto" / "kr_ledgers_2026.csv").write_text(
                self.LEDGER)
            env = _env(home, TAXJSON_OFFLINE="1")

            r = _run_cli(root, "run", "--no-input", env=env)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            console = r.stdout + r.stderr
            self.assertIn("ATTENTION: crypto id: QZT has no CRYPTO line",
                          console)
            self.assertIn("    CRYPTO QZT QZT55504", console)
            self.assertEqual(self._filled_price(root), 0.01)
            sums = "".join(p.read_text() for p in
                           (root / "reports").rglob("crypto*.sum"))
            self.assertIn("crypto id: QZT", sums)

            r = _run_cli(root, "run", "--fast", "--no-input", env=env)
            self.assertIn("ATTENTION: crypto id: QZT",
                          r.stdout + r.stderr)

            (root / "ticker.map").write_text("CRYPTO QZT QZT55504\n")
            r = _run_cli(root, "run", "--fast", "--no-input", env=env)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertNotIn("crypto id:", r.stdout + r.stderr)
            self.assertEqual(self._filled_price(root), 100.0)


class TestRunCryptoSendsWithoutTheLine(unittest.TestCase):
    """The sends fixture (fictional QZL, priced under a numbered id)
    with its CRYPTO line removed: both the reward fill-crypto prices and
    the payment crypto-sends values are said, with the line — a
    console warning and a .sum line, not a --strict stop of its own."""

    def test_both_paths_say_it(self):
        from test_fix_sends import _cli, _project
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td)
            (root / "ticker.map").unlink()
            cache = home / ".crypto_price_cache.json"
            c = json.loads(cache.read_text())
            c.update({"QZL-2026-04-13": 0.5, "QZL-2026-01-12": 0.4})
            cache.write_text(json.dumps(c))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            console = r.stdout + r.stderr
            self.assertIn("ATTENTION: crypto id: QZL has no CRYPTO line",
                          console)
            self.assertIn("crypto sends: crypto id: QZL has no CRYPTO "
                          "line", console)
            self.assertIn("CRYPTO QZL QZL55501", console)
            diag = (root / "work" / "crypto_crypto_sends.diag").read_text()
            self.assertIn("crypto id: QZL", diag)
            r = _cli(root, home, "crypto-sends", "crypto")
            self.assertIn("crypto id: QZL", r.stderr)


if __name__ == "__main__":
    unittest.main()
