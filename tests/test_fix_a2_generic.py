"""Re-audit-2 fixes: the generic column-mapped importer.

A2-0030 (cut-off last record), A2-0103/A2-1074/A2-0634 (futures_settle
= "next_day"), A2-0107/A2-0294/A2-0627 ('/' and '\\' futures), A2-0106
(a cash-in amount under a buy action), A2-0299 (a dangling sidecar
mapping), A2-0626/A2-1079 (commission rebates), A2-0628 (an option
expiring on Dec 31), A2-0629 (an exercise/assignment the mapping cannot
express), A2-1075 (a settle date far after the trade), A2-1076 (payment
in lieu), A2-1081 (non-string [defaults]/[formats] values), A2-1083 and
A2-1080 (UTF-16 exports), A2-1085 (the broker account of each row).
Synthetic data only (synthetic account ids, marked pii-ok).
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule

_BASE_COLS = """\
[columns]
date = "Date"
action = "Type"
symbol = "Ticker"
quantity = "Qty"
price = "Price"
"""

_TOML = _BASE_COLS + """\
amount = "Amount"
fee = "Fee"
currency = "Currency"
{extra_cols}
[actions]
"BUY" = "buy"
"SELL" = "sell"
{extra_actions}
{extra}
[defaults]
currency = "CAD"
"""


def _toml(extra_cols="", extra_actions="", extra=""):
    return _TOML.format(extra_cols=extra_cols, extra_actions=extra_actions,
                        extra=extra)


def _parse(csv_text, toml_text, *, raw=None, futures_settle=None,
           name="generic_x.csv"):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / name
        if raw is not None:
            c.write_bytes(raw)
        else:
            c.write_text(csv_text, encoding="utf-8")
        c.with_name(c.name + ".toml").write_text(toml_text)
        g = GenericBrokerage()
        if futures_settle is not None:
            g.futures_settle = futures_settle
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = g.parse_file(c)
        return txs, buf.getvalue(), g


_HDR = "Date,Type,Ticker,Qty,Price,Amount,Fee,Currency\n"


class TestCutOffLastRecord(unittest.TestCase):
    """A2-0030: a record cut short is refused, never completed from
    [defaults]."""

    def test_record_with_fewer_cells_than_header_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95,USD\n"
                   "2024-11-05,BUY,MSFT,5,4", _toml())
        msg = str(cm.exception)
        self.assertIn("fewer cells than the header", msg)
        self.assertIn("line 3", msg)

    def test_missing_currency_cell_not_filled_from_defaults(self):
        # Cut right after the amount: the currency cell is gone, and
        # [defaults] currency = CAD used to book a USD trade as MSFT.TO.
        with self.assertRaises(ValueError):
            _parse(_HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95",
                   _toml())

    def test_cut_at_the_last_separator_refused(self):
        # '...,9.95,' with no line break after it: the final cell was
        # cut away, not left blank.
        with self.assertRaises(ValueError) as cm:
            _parse(_HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95,",
                   _toml())
        self.assertIn("cut", str(cm.exception))

    def test_cut_inside_the_currency_cell_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95,US",
                   _toml())
        self.assertIn("currency", str(cm.exception))

    def test_blank_last_cell_with_a_line_break_still_uses_defaults(self):
        txs, _, _ = _parse(
            _HDR + "2024-11-04,BUY,XEI,50,20.00,-1009.95,9.95,\n", _toml())
        self.assertEqual(txs[0]["currency"], "CAD")
        self.assertEqual(txs[0]["symbol"], "XEI.TO")

    def test_complete_file_without_final_line_break_parses(self):
        txs, _, _ = _parse(
            _HDR + "2024-11-04,BUY,MSFT,5,410.00,-2059.95,9.95,USD", _toml())
        self.assertEqual(txs[0]["symbol"], "MSFT.US")


_FUT = (_HDR + "2025-12-01,BUY,{s},1,6000,-300009.5,9.5,USD\n"
        "2025-12-31,SELL,{s},-1,6010,300490.5,9.5,USD\n")


class TestFuturesSettle(unittest.TestCase):
    """A2-0107/A2-0294/A2-0627 and A2-0103/A2-1074/A2-0634."""

    @rule("CA-DATE-09")
    def test_slash_and_backslash_futures_settle_on_trade_date_ca(self):
        for s in ("/ESZ5", "\\ESZ5", "F:ESZ5"):
            txs, _, _ = _parse(_FUT.format(s=s), _toml())
            self.assertEqual(txs[1]["date_settle"], "2025-12-31", s)
            # One spelling: the F: prefix every other parser uses.
            self.assertEqual(txs[1]["symbol"], "F:ESZ5.US", s)

    @rule("US-DATE-09")
    def test_slash_futures_settle_on_trade_date_us(self):
        txs, _, _ = _parse(_FUT.format(s="/ESZ5"), _toml())
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")

    @rule("CA-DATE-10")
    def test_next_day_setting_reaches_generic_ca(self):
        for s in ("F:ESZ5", "/ESZ5"):
            txs, _, _ = _parse(_FUT.format(s=s), _toml(),
                               futures_settle="next_day")
            self.assertEqual(txs[1]["date_settle"], "2026-01-02", s)
            self.assertEqual(txs[0]["date_settle"], "2025-12-02", s)

    @rule("US-DATE-12")
    def test_next_day_setting_reaches_generic_us(self):
        txs, _, _ = _parse(_FUT.format(s="F:ESZ5"), _toml(),
                           futures_settle="next_day")
        self.assertEqual(txs[1]["date_settle"], "2026-01-02")

    def test_taxjson_brokerage_passes_the_setting(self):
        from taxjson.lib.brokerages.generic import GenericBrokerage
        self.assertTrue(hasattr(GenericBrokerage(), "futures_settle"))
        self.assertEqual(GenericBrokerage().futures_settle, "trade")


class TestBuyActionCashIn(unittest.TestCase):
    """A2-0106: under one action mapped to buy, a row whose amount is
    cash IN in a file whose buys carry negative (cash-out) amounts is a
    sale by the file's own convention."""

    def test_positive_amount_buy_in_a_cash_signed_file_refused(self):
        toml = _toml().replace('"BUY" = "buy"\n"SELL" = "sell"\n',
                               '"TRADE" = "buy"\n')
        with self.assertRaises(ValueError) as cm:
            _parse(_HDR + "2025-02-03,TRADE,XYZ,100,10,-1000.00,0,CAD\n"
                   "2025-06-03,TRADE,XYZ,100,20,2000.00,0,CAD\n", toml)
        self.assertIn("buy", str(cm.exception))
        self.assertIn("line 3", str(cm.exception))

    def test_unsigned_amount_file_still_accepted(self):
        txs, _, _ = _parse(_HDR + "2025-02-03,BUY,XYZ,100,10,1000.00,0,CAD\n"
                           "2025-06-03,BUY,XYZ,100,20,2000.00,0,CAD\n",
                           _toml())
        self.assertEqual([t["net_amount"] for t in txs], [1000.0, 2000.0])


class TestDanglingSidecar(unittest.TestCase):
    """A2-0299."""

    def test_dangling_sidecar_refused_not_shared_mapping(self):
        from taxjson.lib.brokerages.generic import GenericBrokerage
        with tempfile.TemporaryDirectory() as td:
            c = Path(td) / "generic_a.csv"
            c.write_text(_HDR + "2025-01-02,BUY,XYZ,1,10,-10,0,CAD\n")
            (Path(td) / "generic.toml").write_text(_toml())
            os.symlink(Path(td) / "gone.toml",
                       c.with_name(c.name + ".toml"))
            with self.assertRaises(ValueError) as cm:
                GenericBrokerage().parse_file(c)
            self.assertIn("generic_a.csv.toml", str(cm.exception))
            from taxjson.lib.brokerages.generic import mapping_path
            with self.assertRaises(ValueError):
                mapping_path(c)


class TestRebates(unittest.TestCase):
    """A2-0626 / A2-1079: a credited commission lowers the cost and
    raises the proceeds, and is booked as a negative fee (Questrade's
    convention), never as a charge."""

    def test_amount_column_shows_the_rebate(self):
        txs, _, _ = _parse(
            _HDR + "2025-01-02,BUY,XYZ,100,10,-999.65,-0.35,USD\n"
            "2025-02-03,SELL,XYZ,-100,11,1100.35,-0.35,USD\n", _toml())
        self.assertAlmostEqual(txs[0]["net_amount"], 999.65)
        self.assertAlmostEqual(txs[0]["fee"], -0.35)
        self.assertAlmostEqual(txs[1]["net_amount"], 1100.35)
        self.assertAlmostEqual(txs[1]["fee"], -0.35)

    def test_amount_column_shows_a_charge(self):
        txs, _, _ = _parse(
            _HDR + "2025-01-02,BUY,XYZ,100,10,-1000.35,-0.35,USD\n",
            _toml())
        self.assertAlmostEqual(txs[0]["fee"], 0.35)

    def test_no_amount_column_explicit_charged_sign(self):
        toml = (_BASE_COLS + 'fee = "Fee"\n[formats]\nfee_sign = '
                '"charged"\n[actions]\n"BUY" = "buy"\n"SELL" = "sell"\n'
                '[defaults]\ncurrency = "USD"\n')
        txs, _, _ = _parse("Date,Type,Ticker,Qty,Price,Fee\n"
                           "2025-01-02,BUY,XYZ,100,10,-0.35\n"
                           "2025-02-03,SELL,XYZ,-100,11,-0.35\n", toml)
        self.assertAlmostEqual(txs[0]["net_amount"], 999.65)
        self.assertAlmostEqual(txs[1]["net_amount"], 1100.35)
        self.assertAlmostEqual(txs[0]["fee"], -0.35)

    def test_no_amount_column_default_keeps_magnitude_as_charge(self):
        toml = (_BASE_COLS + 'fee = "Fee"\n[actions]\n"BUY" = "buy"\n'
                '[defaults]\ncurrency = "USD"\n')
        txs, _, _ = _parse("Date,Type,Ticker,Qty,Price,Fee\n"
                           "2025-01-02,BUY,XYZ,100,10,0.35\n", toml)
        self.assertAlmostEqual(txs[0]["net_amount"], 1000.35)
        self.assertAlmostEqual(txs[0]["fee"], 0.35)

    def test_futures_gross_with_explicit_sign(self):
        toml = _toml(extra='[formats]\nfee_sign = "charged"\n')
        txs, _, _ = _parse(
            _HDR + "2025-01-02,SELL,F:ESZ5,-1,10,500.50,-0.50,USD\n", toml)
        self.assertAlmostEqual(txs[0]["gross_amount"], 500.0)
        self.assertAlmostEqual(txs[0]["fee"], -0.50)


class TestDec31Expiry(unittest.TestCase):
    """A2-0628: an option closed at $0 on its expiry day is dated and
    settled that day (no T+1); a $0 close posted after the expiry is
    clamped to it."""

    _CSV = (_HDR + "2025-12-15,BUY,SPY251231C00700000,1,4.10,-414.23,"
            "4.23,USD\n{d},SELL,SPY251231C00700000,-1,0,0,0,USD\n")

    @rule("CA-DATE-08")
    def test_expiry_day_close_settles_that_day_ca(self):
        txs, _, _ = _parse(self._CSV.format(d="2025-12-31"), _toml())
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")

    @rule("US-DATE-08")
    def test_expiry_day_close_settles_that_day_us(self):
        txs, _, _ = _parse(self._CSV.format(d="2025-12-31"), _toml())
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")

    @rule("CA-DATE-08")
    def test_expiry_posted_next_business_day_clamped(self):
        txs, err, _ = _parse(self._CSV.format(d="2026-01-02"), _toml())
        self.assertEqual(txs[1]["date"], "2025-12-31")
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")
        self.assertIn("expiry", err)

    def test_priced_close_before_expiry_keeps_t1(self):
        txs, _, _ = _parse(
            _HDR + "2025-12-15,BUY,SPY251231C00700000,1,4.10,-414.23,4.23,"
            "USD\n2025-12-30,SELL,SPY251231C00700000,-1,1.00,95.77,4.23,"
            "USD\n", _toml())
        self.assertEqual(txs[1]["date_settle"], "2025-12-31")


class TestAssignmentShape(unittest.TestCase):
    """A2-0629: the mapping has no exercise/assignment target; a $0
    option close beside a stock trade at the strike is named on the
    console (ATTENTION) instead of booked silently as an expiry plus an
    unrelated trade."""

    def test_assignment_shape_is_attention(self):
        txs, err, _ = _parse(
            _HDR + "2025-01-10,SELL,XYZ250620P00050000.TO,-1,2.00,200.00,"
            "0,CAD\n2025-06-20,BUY,XYZ250620P00050000.TO,1,0,0,0,CAD\n"
            "2025-06-20,BUY,XYZ.TO,100,50,-5000.00,0,CAD\n", _toml())
        self.assertEqual(len(txs), 3)
        self.assertIn("warning: ATTENTION:", err)
        self.assertIn("exercise/assignment", err)
        self.assertIn("XYZ250620P00050000.TO", err)

    def test_no_attention_for_plain_expiry(self):
        _, err, _ = _parse(
            _HDR + "2025-01-10,SELL,XYZ250620P00050000.TO,-1,2.00,200.00,"
            "0,CAD\n2025-06-20,BUY,XYZ250620P00050000.TO,1,0,0,0,CAD\n",
            _toml())
        self.assertNotIn("ATTENTION", err)


class TestLateSettle(unittest.TestCase):
    """A2-1075: a mapped settle date far after the trade."""

    _TOML = _toml(extra_cols='settle = "Settle"\n')
    _H = "Date,Settle,Type,Ticker,Qty,Price,Amount,Fee,Currency\n"

    @rule("CA-DATE-06")
    @rule("US-DATE-06")
    def test_settle_a_year_late_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(self._H + "2025-12-15,2026-12-16,BUY,XYZ,1,10,-10,0,"
                   "CAD\n", self._TOML)
        self.assertIn("2026-12-16", str(cm.exception))

    @rule("CA-DATE-06")
    @rule("US-DATE-06")
    def test_settle_a_week_and_more_late_is_attention(self):
        txs, err, _ = _parse(self._H + "2025-03-03,2025-03-14,BUY,XYZ,1,10,"
                             "-10,0,CAD\n", self._TOML)
        self.assertEqual(txs[0]["date_settle"], "2025-03-14")
        self.assertIn("warning: ATTENTION:", err)

    def test_normal_settle_silent(self):
        _, err, _ = _parse(self._H + "2025-03-03,2025-03-04,BUY,XYZ,1,10,"
                           "-10,0,CAD\n", self._TOML)
        self.assertNotIn("ATTENTION", err)


class TestPaymentInLieu(unittest.TestCase):
    """A2-1076."""

    def test_dividend_in_lieu_target(self):
        txs, _, _ = _parse(
            _HDR + "2025-03-03,PIL,XYZ,0,0,12.50,0,USD\n",
            _toml(extra_actions='"PIL" = "dividend_in_lieu"\n'))
        self.assertEqual(txs[0]["action"], "DIVIDEND_IN_LIEU")
        self.assertEqual(txs[0]["type"], "dividend_in_lieu")
        self.assertEqual(txs[0]["net_amount"], 12.5)
        self.assertEqual(txs[0]["description"], "PIL")


class TestValueTypes(unittest.TestCase):
    """A2-1081: [defaults] and [formats] values must be strings."""

    def _refused(self, toml):
        with self.assertRaises(ValueError) as cm:
            _parse("Date,Type,Qty,Price,Amount\n2025-01-02,BUY,1,10,-10\n",
                   toml)
        return str(cm.exception)

    def test_non_string_default_currency_refused(self):
        msg = self._refused(
            '[columns]\ndate="Date"\naction="Type"\nquantity="Qty"\n'
            'price="Price"\namount="Amount"\n[actions]\n"BUY"="buy"\n'
            '[defaults]\ncurrency=true\nsymbol="AAPL"\n')
        self.assertIn("[defaults].currency", msg)

    def test_non_string_default_symbol_refused(self):
        msg = self._refused(
            '[columns]\ndate="Date"\naction="Type"\nquantity="Qty"\n'
            'price="Price"\namount="Amount"\n[actions]\n"BUY"="buy"\n'
            '[defaults]\ncurrency="USD"\nsymbol=["AAPL"]\n')
        self.assertIn("[defaults].symbol", msg)

    def test_non_string_format_refused(self):
        msg = self._refused(
            '[columns]\ndate="Date"\naction="Type"\nquantity="Qty"\n'
            'price="Price"\namount="Amount"\n[formats]\nsettle=5\n'
            '[actions]\n"BUY"="buy"\n[defaults]\ncurrency="USD"\n'
            'symbol="AAPL"\n')
        self.assertIn("[formats].settle", msg)


class TestUtf16(unittest.TestCase):
    """A2-1083 (the importer) and A2-1080 (taxjson-generate-parser)."""

    _CSV = _HDR + "2025-01-02,BUY,XYZ,10,50.00,-500.00,0,CAD\n"

    def test_utf16_export_parses_like_utf8(self):
        a, _, _ = _parse(self._CSV, _toml())
        b, _, _ = _parse(None, _toml(), raw=self._CSV.encode("utf-16"))
        self.assertEqual(a, b)

    def test_cp1252_still_refused_naming_the_file(self):
        with self.assertRaises(ValueError) as cm:
            _parse(None, _toml(), raw=(self._CSV.replace("XYZ", "XÉZ")
                                       .encode("cp1252")))
        self.assertIn("generic_x.csv", str(cm.exception))

    def test_generate_parser_reads_a_utf16_sample(self):
        from taxjson.bin.taxjson_generate_parser import _read_sample
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.csv"
            p.write_bytes(self._CSV.encode("utf-16"))
            self.assertEqual(_read_sample(p, 1),
                             "Date,Type,Ticker,Qty,Price,Amount,Fee,Currency")

    def test_generate_parser_names_a_legacy_file(self):
        env = dict(os.environ)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sample_x.csv"
            p.write_bytes("Date,Ticker\n2025-01-02,X\xc9Z\n".encode("cp1252"))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_generate_parser",
                 str(p), "-o", str(Path(td) / "out.py")],
                capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sample_x.csv", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestBrokerAccount(unittest.TestCase):
    """A2-1085 (parser side): the broker account of each row, from a
    mapped column or the mapping's [broker].account."""

    def test_account_column(self):
        toml = _toml(extra_cols='account = "Account"\n')
        txs, _, g = _parse(
            "Date,Type,Ticker,Qty,Price,Amount,Fee,Currency,Account\n"
            "2025-01-02,BUY,XYZ,10,5,-50,0,CAD,55500001\n"  # pii-ok
            "2025-01-02,BUY,XYZ,10,5,-50,0,CAD,55500002\n",  # pii-ok
            toml)
        self.assertEqual([t["broker_account"] for t in txs],
                         ["55500001", "55500002"])  # pii-ok
        self.assertEqual(g.statement_accounts(),
                         {"55500001", "55500002"})  # pii-ok

    def test_broker_account_key(self):
        toml = _toml() + '[broker]\naccount = "55500001"\n'  # pii-ok
        txs, _, g = _parse(_HDR + "2025-01-02,BUY,XYZ,10,5,-50,0,CAD\n",
                           toml)
        self.assertEqual(txs[0]["broker_account"], "55500001")  # pii-ok
        self.assertEqual(g.statement_accounts(), {"55500001"})  # pii-ok

    def test_no_account_declared(self):
        txs, _, g = _parse(_HDR + "2025-01-02,BUY,XYZ,10,5,-50,0,CAD\n",
                           _toml())
        self.assertNotIn("broker_account", txs[0])
        self.assertEqual(g.statement_accounts(), set())

    def test_non_string_broker_account_refused(self):
        toml = _toml() + '[broker]\naccount = 55500001\n'  # pii-ok
        with self.assertRaises(ValueError):
            _parse(_HDR + "2025-01-02,BUY,XYZ,10,5,-50,0,CAD\n", toml)


if __name__ == "__main__":
    unittest.main()
