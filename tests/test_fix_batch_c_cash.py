"""Batch C (cash books, the FX-on-cash ledger v2, the generic importer,
ALLOWLOSS): 2026-10 audit findings 51, 52, 53, 70, 87. All data
synthetic."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib import loss_overrides as LO


def _line(text, book="margin/tt"):
    e = CE.parse_line(text)
    e.update(book=book, label="margin", where="c.tt:1")
    return e


def _build(lines, rate_of=lambda c, d: 1.5):
    return V2.build([], {"lines": lines, "events": [], "problems": []},
                    "CAD", 2025, rate_of, country="canada")


class TestCashBookNames(unittest.TestCase):
    """#51: two accounts whose hashes share the first 4 hex were one
    book (`margin/ib:3135`) that no `at=` could name."""

    A, B, C = "3135769a44", "31355d479e", "7f20aa0b11"   # synthetic

    def test_colliding_prefixes_get_longer_names(self):
        bk = CE.Books()
        bk.add("margin", "ib", self.A)
        bk.add("margin", "ib", self.B)
        names = bk.all_names("margin")
        self.assertEqual(names, ["margin/ib:31355", "margin/ib:31357"])
        self.assertEqual(bk.name("margin", "ib", self.A), "margin/ib:31357")
        self.assertEqual(bk.for_at("margin", "ib:31357"),
                         ("margin/ib:31357", ""))
        b, why = bk.for_at("margin", "ib:3135")
        self.assertIsNone(b)
        self.assertIn("margin/ib:31355", why)

    def test_distinct_prefixes_keep_four(self):
        # A project without a collision keeps its book names (and so its
        # at= lines and the prior year's recorded close).
        bk = CE.Books()
        bk.add("margin", "ib", self.A)
        bk.add("margin", "ib", self.C)
        self.assertEqual(bk.all_names("margin"),
                         ["margin/ib:3135", "margin/ib:7f20"])
        one = CE.Books()
        one.add("margin", "ib", self.A)
        self.assertEqual(one.all_names("margin"), ["margin/ib"])


class TestCashOpenNoBook(unittest.TestCase):
    """#52: a CASHOPEN whose book could not be told was dropped without
    a word."""

    def test_reported(self):
        ln = _line("CASHOPEN 2025-01-01 USD 100 140", None)
        ln["book_problem"] = ("its account folder holds several books "
                              "(margin/ib:3135, margin/ib:7f20) — add "
                              "at=<book>, or a CASHBOOK <book> line to the "
                              "file")
        r = _build([ln])
        self.assertEqual(r["status"], "not_computed")
        texts = [p["text"] for p in r["problems"]]
        self.assertTrue(any(t.startswith("c.tt:1: its account folder "
                                         "holds several books")
                            for t in texts), texts)

    def test_base_currency_open_still_ignored(self):
        r = _build([_line("CASHOPEN 2025-01-01 CAD 100 100", None)])
        self.assertEqual(r["status"], "computed")
        self.assertEqual(r["problems"], [])


class TestExplicitValueNeedsNoRate(unittest.TestCase):
    """#53: FXCONV USD -> EUR with value= was refused for want of a USD
    rate it does not use."""

    def _lines(self):
        return [_line("CASHOPEN 2025-01-01 USD 100 140"),
                _line("CASHOPEN 2025-01-01 EUR 0 0"),
                _line("FXCONV 2025-02-03 USD 100 EUR 90 value=150"),
                _line("CASHBAL 2025-12-31 USD 0"),
                _line("CASHBAL 2025-12-31 EUR 90")]

    def test_no_rate_on_file(self):
        r = _build(self._lines(), rate_of=lambda c, d: None)
        self.assertEqual(r["status"], "computed", r["problems"])
        self.assertAlmostEqual(r["net_gain"], 10.0)     # 150 - 140
        self.assertEqual(r["close"]["books"]["margin/tt"]["EUR"],
                         {"units": 90.0, "cost": 150.0})

    def test_same_figures_with_a_rate(self):
        r = _build(self._lines(), rate_of=lambda c, d: 1.4)
        self.assertEqual(r["status"], "computed", r["problems"])
        self.assertAlmostEqual(r["net_gain"], 10.0)
        self.assertEqual(r["events"][0]["rate"], 1.4)

    def test_without_value_a_rate_is_still_needed(self):
        lines = self._lines()
        lines[2] = _line("FXCONV 2025-02-03 USD 100 EUR 90")
        r = _build(lines, rate_of=lambda c, d: None)
        self.assertEqual(r["status"], "not_computed")


_HDR = "Date,Type,Ticker,Shares,Price,Amount,Commission,Currency\n"
_TOML = ('[columns]\ndate="Date"\naction="Type"\nsymbol="Ticker"\n'
         'quantity="Shares"\nprice="Price"\namount="Amount"\n'
         'fee="Commission"\ncurrency="Currency"\n'
         '[actions]\n"BUY"="buy"\n"SELL"="sell"\n')


def _generic(csv_text):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_t.csv"
        c.write_text(csv_text)
        c.with_name(c.name + ".toml").write_text(_TOML)
        with redirect_stderr(io.StringIO()):
            return GenericBrokerage().parse_file(c)


class TestGenericNegativeFuturesSale(unittest.TestCase):
    """#70: an unsigned-quantity export's futures sale at a negative
    price (it pays cash) was refused as 'a buy by its own signs'."""

    BUY = "2025-04-01,BUY,/ZQX,1,10.00,-10000.00,0,USD\n"

    def test_unsigned_sale_booked_like_signed(self):
        unsigned = _generic(_HDR + self.BUY +
                            "2025-04-20,SELL,/ZQX,1,-10.00,-10000.00,0,USD\n")
        signed = _generic(_HDR + self.BUY +
                          "2025-04-20,SELL,/ZQX,-1,-10.00,-10000.00,0,USD\n")
        self.assertEqual([(t["quantity"], t["net_amount"]) for t in unsigned],
                         [(t["quantity"], t["net_amount"]) for t in signed])
        self.assertEqual(unsigned[1]["quantity"], -1.0)
        self.assertAlmostEqual(unsigned[1]["net_amount"], -10000.0)

    def test_negative_price_buy_does_not_flag_the_file(self):
        txs = _generic(_HDR + self.BUY +
                       "2025-04-15,BUY,/ZQX,1,-5.00,5000.00,0,USD\n"
                       "2025-04-20,SELL,/ZQX,2,-10.00,-20000.00,0,USD\n")
        self.assertEqual([t["quantity"] for t in txs], [1.0, 1.0, -2.0])

    def test_positive_price_contradiction_still_refused(self):
        with self.assertRaises(ValueError):
            _generic(_HDR + self.BUY +
                     "2025-04-20,SELL,/ZQX,1,10.00,-10000.00,0,USD\n")


class TestAllowLossQuantity(unittest.TestCase):
    """#87: `1,5` was read as 15 units."""

    def _qty(self, tok):
        return LO.parse_line(f'ALLOWLOSS 2025-02-03 SAMP.US {tok} '
                             f'reason="synthetic"')["qty"]

    def test_decimal_comma_refused(self):
        for tok in ("1,5", "1,2345", "12,34", "$5", "(3)"):
            with self.assertRaisesRegex(ValueError,
                                        "is not a number of units sold"):
                self._qty(tok)

    def test_plain_and_grouped_accepted(self):
        self.assertEqual(self._qty("15"), 15.0)
        self.assertEqual(self._qty("2.5"), 2.5)
        self.assertEqual(self._qty("1,000"), 1000.0)
        self.assertEqual(self._qty("-3"), 3.0)


if __name__ == "__main__":
    unittest.main()
