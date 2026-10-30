"""Fix round 7 (wave B), FX rates and `tjs audit`: a Yahoo rate is not
forward-filled twice (TJ-0843), a bad intraday quote does not poison
the rates file (TJ-0845), futures converted twice keep the composed
rate (TJ-0745); the audit needs no rate for a split that carries no
money (TJ-0765), traces a record-dated return of capital paid later
(TJ-0766) and finds a listing the run joined (TJ-0773).

Every fetcher is stubbed: no network. Synthetic books and rates only."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from taxjson.bin import to_base_curr as T
from taxjson.bin.taxjson_convert_currency import (
    get_rate_for_date, load_exchange_rates, reset_fallback_tally,
)
from tax_rules import rule


def _rates_file(td, rows, src="GBP", dst="USD"):
    p = Path(td) / "to_base.csv"
    p.write_text("".join(f"{r[0]} 12:00:00 {src} {dst} {r[1]} {r[2]}\n"
                         for r in rows), encoding="utf-8")
    return p


class TestYahooFillIsNotFilledAgain(unittest.TestCase):
    """TJ-0843: refresh_yahoo stores its forward-fill as calendar-date
    keys; resolve_rows used to fill again from the last fill (7 + 7 days)
    and the converter looked back 5 more: a 19-day-old rate."""

    OBS = {"2025-01-01": 1.25, "2025-01-22": 1.50}

    def setUp(self):
        reset_fallback_tally()
        self.addCleanup(reset_fallback_tally)

    def _fetch(self, _t, a, b):
        return {d: v for d, v in self.OBS.items() if a <= d <= b}

    def _rows(self, cache):
        return T.resolve_rows(cache, "GBP", "USD", "2025-01-01",
                              "2025-01-22", "2025-02-01")

    @rule("CA-FX-02")
    @rule("US-FX-02")
    def test_a_rate_is_never_older_than_fill_plus_lookback(self):
        cache = {}
        self.assertEqual(T.refresh_yahoo(
            cache, "GBPUSD", [("2025-01-01", "2025-01-22")], "2025-02-01",
            fetch=self._fetch), [])
        rows = self._rows(cache)
        dates = [d for d, _v, _s in rows]
        # 7 days of fill from January 1, nothing more, then January 22.
        self.assertEqual(dates[-2:], ["2025-01-08", "2025-01-22"])
        with tempfile.TemporaryDirectory() as td:
            hist = load_exchange_rates(_rates_file(td, rows), "USD")
        # January 13 is 12 days after the observation: still a rate.
        self.assertEqual(str(get_rate_for_date("GBP", "2025-01-13", hist,
                                               None)), "1.25")
        # January 14 and January 20 (19 days): no permissible rate.
        self.assertIsNone(get_rate_for_date("GBP", "2025-01-14", hist, None))
        self.assertIsNone(get_rate_for_date("GBP", "2025-01-20", hist, None))

    def test_a_second_fetch_does_not_fill_on_from_a_fill(self):
        # The range is asked in two parts: the second starts after the
        # first part's fill and must not seed a new fill from it.
        cache = {}
        T.refresh_yahoo(cache, "GBPUSD", [("2025-01-01", "2025-01-08")],
                        "2025-02-01", fetch=self._fetch)
        self.assertEqual(cache["GBPUSD-2025-01-08"], 1.25)
        T.refresh_yahoo(cache, "GBPUSD", [("2025-01-01", "2025-01-22")],
                        "2025-02-01", fetch=self._fetch)
        self.assertNotIn("GBPUSD-2025-01-09", cache)
        dates = [d for d, _v, _s in self._rows(cache)]
        self.assertNotIn("2025-01-09", dates)

    def test_a_weekend_after_a_genuine_observation_still_fills(self):
        # A cache holding observations only (weekdays) still carries
        # Friday's rate over the weekend, and a cached fill is used as is.
        cache = {"GBPUSD-2025-01-02": 1.2, "GBPUSD-2025-01-03": 1.3,
                 "GBPUSD-2025-01-06": 1.4,
                 "_coverage": {"yahoo:GBPUSD": [["2025-01-01",
                                                 "2025-01-10"]]}}
        got = {d: v for d, v, _s in T.resolve_rows(
            cache, "GBP", "USD", "2025-01-02", "2025-01-10", "2025-02-01")}
        self.assertEqual(got["2025-01-04"], "1.3")
        self.assertEqual(got["2025-01-05"], "1.3")
        self.assertEqual(got["2025-01-10"], "1.4")


class _Recent:
    empty = False

    def __init__(self, x):
        self.x = x

    def __getitem__(self, key):
        return SimpleNamespace(iloc=[self.x])


class TestBadIntradayQuoteIsIgnored(unittest.TestCase):
    """TJ-0845: a NaN, zero, negative or infinite intraday close was
    appended to the rates file, which the converter then refused as a
    whole."""

    def _main(self, close):
        fake = SimpleNamespace(Ticker=lambda pair: SimpleNamespace(
            history=lambda **kw: _Recent(close)))
        out, err = io.StringIO(), io.StringIO()
        built = ([("2025-06-02", "1.25", "yahoo", "2025-06-02")], [], [])
        with mock.patch.object(T, "yf", fake), \
                mock.patch.object(T, "build_rates", return_value=built), \
                mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = T.main(["GBP", "USD", "--start", "2025-06-02",
                         "--end", "2025-06-02"])
        return rc, out.getvalue()

    def test_invalid_quotes_leave_the_history_usable(self):
        for close in (float("nan"), 0.0, -1.0, float("inf")):
            with self.subTest(close=close):
                rc, text = self._main(close)
                self.assertEqual(rc, 0)
                self.assertEqual(len(text.splitlines()), 1, text)
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "to_base.csv"
                    p.write_text(text, encoding="utf-8")
                    hist = load_exchange_rates(p, "USD")
                self.assertEqual(str(get_rate_for_date(
                    "GBP", "2025-06-02", hist, None)), "1.25")

    def test_a_valid_quote_is_still_appended(self):
        rc, text = self._main(1.3)
        self.assertEqual(rc, 0)
        self.assertEqual(len(text.splitlines()), 2, text)
        self.assertIn(" GBP USD 1.3 yahoo", text.splitlines()[1])


def _fut(account, date, qty, net):
    from taxjson.lib.core import TaxTransaction
    return TaxTransaction(
        action="BUYSELL", date=date, date_settle=date, time="10:00:00",
        symbol="F:SYNZ6.US", quantity=float(qty), currency="USD",
        price=abs(net / qty), net_amount=float(net),
        gross_amount=float(abs(net)), account=account)


class TestFuturesReconversionComposesTheRate(unittest.TestCase):
    """TJ-0745: converting settled futures books again (USD to EUR at
    0.8, then EUR to CAD at 1.5) kept the first rate in settle_native,
    so the Canadian cross-account pool priced a close at 0.8."""

    DATES = ("2025-01-06", "2025-02-03", "2025-06-02", "2026-02-02")

    def _convert(self, rows, src, to, rate):
        from decimal import Decimal
        from taxjson.bin.taxjson_convert_currency import process_transactions
        reset_fallback_tally()
        self.addCleanup(reset_fallback_tally)
        hist = {src: {d: Decimal(rate) for d in self.DATES}}
        return process_transactions(rows, to, hist, None, country="canada")

    def _gain_2025(self, chain):
        from taxjson.lib.core import get_tax_rules
        books = [[_fut("acct_a", "2025-01-06", 1, 100),
                  _fut("acct_a", "2025-06-02", -1, 300)],
                 [_fut("acct_b", "2025-02-03", 1, 200),
                  _fut("acct_b", "2026-02-02", -1, 300)]]
        rows = []
        for book in books:
            for src, to, rate in chain:
                book = self._convert(book, src, to, rate)
            rows += book
        res = get_tax_rules("canada").compute_gains(rows)
        return round(sum(g["gain"] for g in res["transactions"]
                         if g.get("qty") and "gain" in g
                         and str(g.get("date", ""))[:4] == "2025"), 6), rows

    @rule("CA-FX-04")
    def test_two_step_conversion_pools_like_a_direct_one(self):
        from decimal import Decimal
        from taxjson.lib.futures import parse_settle_native
        direct, _ = self._gain_2025([("USD", "CAD", "1.2")])
        via, rows = self._gain_2025([("USD", "EUR", "0.8"),
                                     ("EUR", "CAD", "1.5")])
        # (300 - 150) x 1.2: the pooled close at the USD->CAD rate.
        self.assertEqual(direct, 180.0)
        self.assertEqual(via, direct)
        legs = [parse_settle_native(r.settle_native) for r in rows]
        self.assertTrue(all(leg[4] == Decimal("1.2") for leg in legs))

    def test_a_conversion_into_its_own_currency_keeps_the_rate(self):
        from decimal import Decimal
        from taxjson.lib.futures import parse_settle_native
        rows = self._convert([_fut("acct_a", "2025-01-06", 1, 100),
                              _fut("acct_a", "2025-06-02", -1, 300)],
                             "USD", "CAD", "1.2")
        again = self._convert(rows, "USD", "CAD", "1.2")
        self.assertEqual([parse_settle_native(r.settle_native)[4]
                          for r in again], [Decimal("1.2")] * 2)


def _audit_books(parsed, books, *, country="canada", year="2025",
                 currency="CAD", extra=()):
    """taxjson-audit in process over a parsed file, its base book and
    the gains the run would save from it: (rc, --json document, err)."""
    from test_fix_1029_r4_audit import _audit
    from taxjson.lib.core import load_transactions
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "margin_tt_trades.json").write_text(json.dumps(
            {"transactions": parsed,
             "metadata": {"source_file": "inputs/margin/trades.tt"}}))
        (p / "base.json").write_text(json.dumps({"transactions": books}))
        with contextlib.redirect_stderr(io.StringIO()):
            saved = run_gains(load_transactions(p / "base.json"), [],
                              req=GainsRequest(country=country,
                                               year=int(year),
                                               taxable=True,
                                               phantom_hint=False))
        (p / "check.json").write_text(json.dumps(
            {"transactions": saved["transactions"]}, default=str))
        return _audit(["--country", country, "--year", year,
                       "--base", str(p / "base.json"),
                       "--source", str(p / "margin_tt_trades.json"),
                       "--check", str(p / "check.json"),
                       "--base-currency", currency, "--no-trace",
                       *extra])


class TestAuditSplitNeedsNoRate(unittest.TestCase):
    """TJ-0765: a split that carries no money, labelled in a currency
    with no rate, is relabelled by the converter; the audit asked for a
    rate and failed correct books."""

    def test_moneyless_split_in_another_currency_ties(self):
        from test_fix_1029_r4_audit import _row
        from taxjson.bin.taxjson_convert_currency import process_transactions
        from taxjson.lib.core import coerce_transaction_row
        parsed = [
            _row(action="BUYSELL", date="2025-01-02", symbol="ZZQ.US",
                 quantity=10, price=10.0, net_amount=100.0, currency="CAD"),
            _row(action="SPLIT", date="2025-02-03", symbol="ZZQ.US",
                 quantity=2.0, currency="USD"),
            _row(action="BUYSELL", date="2025-03-03", symbol="ZZQ.US",
                 quantity=-20, price=6.0, net_amount=120.0, currency="CAD"),
        ]
        reset_fallback_tally()
        self.addCleanup(reset_fallback_tally)
        books = [t.to_dict() for t in process_transactions(
            [coerce_transaction_row(dict(r), i, "test")
             for i, r in enumerate(parsed)], "CAD", {}, None,
            country="canada")]
        rc, d, err = _audit_books(parsed, books)
        self.assertEqual(rc, 0, (d.get("reconciliation_failures"), err))
        self.assertFalse(d["failed"])
        [split] = [r for r in d["pool_other_rows"]["rows"]
                   if r["action"] == "SPLIT"]
        self.assertIs(split["ties"], True)

    def test_a_split_carrying_money_in_the_books_still_fails(self):
        from taxjson.bin import taxjson_audit as AUDIT
        raw = {"action": "SPLIT", "currency": "USD", "quantity": 2.0,
               "date": "2025-02-03"}
        base = dict(raw, currency="CAD", net_amount=5.0)
        _fx, _w, fails = AUDIT.money_lineage(
            "x", raw, base, base_currency="CAD", fx_history={},
            default_rate=None)
        self.assertEqual(len(fails), 1)
        self.assertIn("a split carries no money to convert", fails[0])


class TestAuditRecordDatedRoc(unittest.TestCase):
    """TJ-0766: a return of capital paid more than 31 days after the
    last audited sale, with a record date before the sale, lowers the
    sale's cost (Canada) but was left out of the pool check, so a book
    amount unlike the broker's passed."""

    def _rows(self, paid):
        from test_fix_1029_r4_audit import _row
        return [
            _row(action="BUYSELL", date="2025-12-01", symbol="ZZU.UN.TO",
                 quantity=100, price=1.0, net_amount=100.0, currency="CAD"),
            _row(action="BUYSELL", date="2025-12-30", symbol="ZZU.UN.TO",
                 quantity=-100, price=1.2, net_amount=120.0, currency="CAD"),
            _row(action="ADJUST", date=paid, symbol="ZZU.UN.TO",
                 net_amount=-50.0, currency="CAD", type="roc",
                 record_date="2025-12-29"),
        ]

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_a_late_paid_roc_is_traced_and_its_difference_fails(self):
        for paid in ("2026-01-05", "2026-03-02"):
            with self.subTest(paid=paid):
                parsed = self._rows(paid)
                rc, d, err = _audit_books(parsed, copy.deepcopy(parsed))
                self.assertEqual(rc, 0, (d.get("reconciliation_failures"),
                                         err))
                [roc] = [r for r in d["pool_other_rows"]["rows"]
                         if r["action"] == "ADJUST"]
                self.assertIs(roc["ties"], True)
                books = copy.deepcopy(parsed)
                books[2]["net_amount"] = -20.0
                rc, d, err = _audit_books(parsed, books)
                self.assertEqual(rc, 1, err)
                [m] = d["pool_other_rows"]["mismatched"]
                self.assertEqual(m["action"], "ADJUST")


class TestAuditFindsAJoinedListing(unittest.TestCase):
    """TJ-0773: a listing the run joined by its transfer journal is in
    the run's effective map only; `tjs audit QZD.U.TO` read ticker.map
    and matched nothing."""

    @classmethod
    def setUpClass(cls):
        import test_fix_qtjournal as F
        cls.F = F
        cls._td = tempfile.TemporaryDirectory()
        cls.root = F._project(cls._td.name)
        cls.r = F._run(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _audit(self, *sym):
        r = self.F._run(self.root, "audit", *sym, "--json", "--no-trace")
        return r.returncode, json.loads(r.stdout or "{}"), r.stderr

    @rule("CA-XLIST-03")
    def test_the_joined_listing_filters_to_the_same_sale(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-2000:])
        self.assertIn("TOBASE QZD.U.TO QZD.TO", (
            self.root / "work" / self.F.XL.EFFECTIVE_MAP).read_text())
        rc, whole, err = self._audit("QZD.TO")
        self.assertEqual(rc, 0, err)
        rc, d, err = self._audit("QZD.U.TO")
        self.assertEqual(rc, 0, err)
        ids = sorted(e["id"] for e in d["events"])
        self.assertTrue(ids)
        self.assertEqual(ids, sorted(e["id"] for e in whole["events"]))


if __name__ == "__main__":
    unittest.main()
