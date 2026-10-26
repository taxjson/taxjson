"""Backlog fixes, group `parsers`: the broker parsers and readers.

TJ-0026 (Kraken's UNBOOKED trades warning said the file is refused),
TJ-0040 (a whitespace-only line in a Kraken export), TJ-0164 (a Kraken
ledger's refusal named the trades file), TJ-0053 (a missing Code
column refused as a missing money column), TJ-0057 (the generic
importer's date error named no line; sanity's unreadable-books error
named taxjson.toml), TJ-0072 (the rates window missed a TRADEDATE
header), TJ-0180 (examples/ib_demo.csv lacked the demo's Cash Report),
TJ-0124 (a schema error named a masked row id, not the line), TJ-0099
(two downloads pasted into one file were booked twice), TJ-0071 (one
broker id restated in a second file was dropped by the dedup).
Synthetic data only.
"""
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.brokerages.kraken import KrakenBrokerage
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent


def setUpModule():
    # Crypto UTC stamps need a named zone outside a project.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_KT_ROWS = ("T1,O1,XBT/USD,2024-02-10 14:25:18,buy,limit,40000,4000,8,"
            "0.1,,,\n"
            "T2,O2,XBT/USD,2024-05-22 09:12:43,buy,limit,50000,2500,5,"
            "0.05,,,\n")


def _kraken(files, which):
    """(transactions, stderr) of the Kraken parser on `which`, with
    `files` alone in a folder of their own."""
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text)
        k = KrakenBrokerage()
        k.stablecoins_as_cash = True
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = k.parse_file(Path(td) / which)
        return txs, buf.getvalue()


def _brokerage(files, broker, *names):
    """taxjson-brokerage on `names` (all of `files` when none), the
    files alone in a folder of their own: the CompletedProcess."""
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text)
        paths = [str(Path(td) / n) for n in (names or files)]
        env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
        return subprocess.run(
            python_module_argv("taxjson.bin.taxjson_brokerage",
                               ["--brokerage", broker, "--account", "x",
                                *paths]),
            cwd=REPO_ROOT, capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=300)


class TestKrakenUnknownTypeWarning(unittest.TestCase):
    """TJ-0026: a trades row whose type is neither buy nor sell is left
    out and the rest of the file is booked, so the UNBOOKED warning must
    not say the file is refused."""

    def test_warning_does_not_say_the_file_is_refused(self):
        rows = _KT_ROWS.replace(",buy,limit,50000", ",settle,limit,50000")
        txs, err = _kraken({"kr_trades.csv": _KT_H + rows}, "kr_trades.csv")
        self.assertEqual(len(txs), 1)          # the buy row is booked
        self.assertIn("UNBOOKED", err)
        self.assertIn("settle x1", err)
        self.assertNotIn("refused", err)
        self.assertIn("remove the row from the export", err)


class TestKrakenWhitespaceLine(unittest.TestCase):
    """TJ-0040: a line holding only spaces is a blank line (Questrade
    and Coinbase skip it), not a truncated row."""

    def test_trailing_whitespace_line_is_skipped(self):
        clean, _ = _kraken({"kr_trades.csv": _KT_H + _KT_ROWS},
                           "kr_trades.csv")
        for tail in ("  \n", "\t\n", "  \n\n   \n"):
            with self.subTest(tail=tail):
                txs, _ = _kraken({"kr_trades.csv": _KT_H + _KT_ROWS + tail},
                                 "kr_trades.csv")
                self.assertEqual(txs, clean)

    def test_whitespace_line_between_rows_keeps_the_line_numbers(self):
        a, b = _KT_ROWS.splitlines(keepends=True)
        bad = b.replace("2500", "2,500")       # a misaligned row: line 4
        with self.assertRaises(ValueError) as cm:
            _kraken({"kr_trades.csv": _KT_H + a + "   \n" + bad},
                    "kr_trades.csv")
        self.assertIn("kr_trades.csv line 4", str(cm.exception))

    def test_a_short_row_is_still_refused(self):
        with self.assertRaises(ValueError) as cm:
            _kraken({"kr_trades.csv": _KT_H + _KT_ROWS + "T3,O3\n"},
                    "kr_trades.csv")
        self.assertIn("line 4", str(cm.exception))
        self.assertIn("truncated or misaligned", str(cm.exception))


class TestKrakenSiblingNamedFirst(unittest.TestCase):
    """TJ-0164: the ledger beside a trades export is read while the
    trades file is parsed; its refusal names the ledger first."""

    LEDGER_NO_FEE = ("txid,refid,time,type,subtype,aclass,asset,amount,"
                     "balance\n"
                     "L1,T1,2024-02-10 14:25:18,trade,,currency,XXBT,0.1,"
                     "0.1\n")

    def test_ledger_refusal_starts_with_the_ledger(self):
        r = _brokerage({"kraken_trades.csv": _KT_H + _KT_ROWS,
                        "kraken_ledger.csv": self.LEDGER_NO_FEE},
                       "kraken", "kraken_trades.csv")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = next(x for x in r.stderr.splitlines() if "error:" in x)
        self.assertIn("error: kraken_ledger.csv: Kraken ledger CSV: "
                      "required column(s) missing: fee", line)
        self.assertNotIn("kraken_trades.csv", line)

    def test_own_refusal_names_the_parsed_file_once(self):
        r = _brokerage({"kraken_trades.csv": _KT_H + _KT_ROWS + "T3,O3\n"},
                       "kraken")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = next(x for x in r.stderr.splitlines() if "error:" in x)
        self.assertIn("error: kraken_trades.csv line 4: the Kraken trades "
                      "row has 2 cells", line)
        self.assertEqual(line.count("kraken_trades.csv"), 1)


class TestMissingColumnReason(unittest.TestCase):
    """TJ-0053: a section refused for a missing Code column gives a
    reason that fits it, not "a missing money column read as 0"."""

    CA = ("Corporate Actions,Header,Asset Category,Currency,Report Date,"
          "Date/Time,Description,Quantity,Proceeds,Value,Realized P/L\n"
          "Corporate Actions,Data,Stocks,USD,2024-06-03,"
          "\"2024-06-03, 20:25:00\",\"ZZP(US9990001017) Spinoff  1 for 4 "
          "(ZZS, ZZS SAMPLE CORP, US9990001025)\",25,0,500,0\n")

    def _refusal(self, text):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ib_2024.csv"
            p.write_text(text)
            with redirect_stderr(io.StringIO()), \
                    self.assertRaises(BrokerageParseError) as cm:
                IbBrokerage().parse_file(p)
        return str(cm.exception)

    def test_missing_code_column(self):
        msg = self._refusal(self.CA)
        self.assertIn("ib_2024.csv line 2 (Corporate Actions)", msg)
        self.assertIn("missing required column(s) 'Code'", msg)
        self.assertNotIn("money column", msg)
        self.assertIn("cancelled row (code Ca) reads as a new event", msg)

    def test_missing_money_column_keeps_its_reason(self):
        text = self.CA.replace(",Value,", ",Worth,") \
            .replace("Realized P/L\n", "Realized P/L,Code\n") \
            .replace(",500,0\n", ",500,0,\n")
        msg = self._refusal(text)
        self.assertIn("'Value'", msg)
        self.assertIn("a missing money column read as 0", msg)
        self.assertNotIn("Code column", msg)

    def test_reason_per_kind_of_column(self):
        from taxjson.lib.brokerages.base import (BaseBrokerage,
                                                 BrokerageParseError)
        with self.assertRaises(BrokerageParseError) as cm:
            BaseBrokerage.require_columns({'Amount': 1}, ('Currency', 'Amount'),
                                          section='Dividends')
        msg = str(cm.exception)
        self.assertIn("'Currency'", msg)
        self.assertNotIn("money column", msg)
        self.assertIn("currency or kind cannot be read without it", msg)


class TestGenericDateNamesTheLine(unittest.TestCase):
    """TJ-0057 (1): the generic importer's date refusal names the line
    and the mapping, as every other parser's does."""

    def test_bad_date_names_line_and_mapping(self):
        from taxjson.lib.brokerages.generic import GenericBrokerage
        csv_text = ("Date,Transaction type,Symbol,Quantity,Price,Amount,"
                    "Currency\n"
                    "2024-02-01,BUY,ZZQ,10,20.00,-200.00,CAD\n")
        toml_text = ('[columns]\ndate = "Date"\naction = "Transaction type"\n'
                     'symbol = "Symbol"\nquantity = "Quantity"\n'
                     'price = "Price"\namount = "Amount"\n'
                     'currency = "Currency"\n\n[formats]\n'
                     'date = "%d/%m/%Y"\n\n[actions]\n"BUY" = "buy"\n')
        with tempfile.TemporaryDirectory() as td:
            c = Path(td) / "ws.csv"
            c.write_text(csv_text)
            (Path(td) / "ws.csv.toml").write_text(toml_text)
            with redirect_stderr(io.StringIO()), \
                    self.assertRaises(ValueError) as cm:
                GenericBrokerage().parse_file(c)
        msg = str(cm.exception)
        self.assertIn("ws.csv line 2: unparseable date '2024-02-01'", msg)
        self.assertIn("[formats].date = '%d/%m/%Y' (ws.csv.toml)", msg)


class TestSanityNamesTheDamagedBook(unittest.TestCase):
    """TJ-0057 (2): sanity's unreadable-books error starts with the
    damaged file, not with taxjson.toml."""

    def test_nan_quantity(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                '[accounts.margin]\ntype = "taxable"\n'
                'holdings = "pos.toml"\n')
            (root / "pos.toml").write_text(
                '[meta]\nas_of = "2025-12-31"\n\n[positions]\n')
            (root / "work").mkdir()
            (root / "work" / "margin_gains_wash.json").write_text(
                json.dumps({"inventory": [{"symbol": "ZZQ.US",
                                           "qty": float("nan")}]}))
            env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
            p = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_run",
                                   ["-C", str(root), "sanity"]),
                cwd=REPO_ROOT, capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL, timeout=300)
        self.assertNotEqual(p.returncode, 0)
        err = [x.split("error:", 1)[1].strip()
               for x in p.stderr.splitlines() if "error:" in x]
        self.assertTrue(err, p.stderr)
        self.assertTrue(err[0].startswith(
            "work/margin_gains_wash.json: ZZQ.US: inventory qty"), err[0])
        self.assertIn("the books of 'margin' cannot be read", p.stderr)
        self.assertNotIn("taxjson.toml:", p.stderr)


class TestRatesWindowCapsHeader(unittest.TestCase):
    """TJ-0072: a header written all in capitals with its words run
    together (TRADEDATE, SETTLEDATE) names a date column, as TradeDate
    and TRADE_DATE do; a data row never becomes a header."""

    def test_caps_run_together_header(self):
        from datetime import date
        from taxjson.lib.rates_window import (_cells, _date_heads,
                                              earliest_in_text)
        rows = (b"20190315,ZZQ,bought\n"
                b"20240601,ZZQ,report 2024-06-01\n")
        for h in (b"TRADEDATE", b"SETTLEDATE", b"TradeDate",
                  b"TRADE_DATE"):
            with self.subTest(h=h):
                self.assertEqual(_date_heads(_cells(h + b",SYMBOL,NOTE",
                                                    b",")), [0])
                self.assertEqual(earliest_in_text(h + b",SYMBOL,NOTE\n"
                                                  + rows, 2027),
                                 date(2019, 3, 15))
        # Rows that are data stay data: a capitals cell beside a cell
        # that is no capitals name, a number or a date.
        for line in (b"BUY,ZZQ.US,UPDATE", b"Action,UPDATE",
                     b"LIQUIDATE,20190315", b"CONSOLIDATED,2024-06-01"):
            with self.subTest(line=line):
                self.assertEqual(_date_heads(_cells(line, b",")), [])


class TestExamplesMatchTheDemo(unittest.TestCase):
    """TJ-0180: every examples/*_demo.csv is the demo project's copy
    (`tjs init --demo`), byte for byte: examples/ib_demo.csv lacked the
    Cash Report, so a project built from examples/ warned."""

    def test_examples_are_the_demo_files(self):
        demo = REPO_ROOT / "src" / "taxjson" / "data" / "demo"
        examples = sorted((REPO_ROOT / "examples").glob("*_demo.csv"))
        self.assertTrue(examples)
        for ex in examples:
            with self.subTest(example=ex.name):
                copies = sorted(demo.glob(f"*/{ex.name}"))
                self.assertEqual(len(copies), 1, copies)
                self.assertEqual(ex.read_bytes(), copies[0].read_bytes())


_QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Activity Type,Account #,Account Type\n")
_QT_ROWS = ("2024-01-16,2024-01-18,Buy,ZZQ,ZZQ CORP,100,18.00,-1800.00,"
            "-4.95,-1804.95,USD,Trades,99900090,Margin\n"
            "2024-03-20,2024-03-22,Buy,ZZQ,ZZQ CORP,50,17.00,-850.00,"
            "-4.95,-854.95,USD,Trades,99900090,Margin\n")


class TestSchemaErrorNamesTheLine(unittest.TestCase):
    """TJ-0124: a row the schema check refuses is named by its file and
    line (a masked content id cannot be searched for), without a double
    space where the symbol is missing, and with the fix."""

    def test_validator_prefers_the_line(self):
        from taxjson.lib.brokerages.schema import validate_transactions
        tx = {"action": "BUYSELL", "date": "2024-03-20", "symbol": "",
              "quantity": 50.0, "currency": "USD", "price": 17.0,
              "net_amount": 854.95, "source": "qt.csv", "id": "b2c3d4"}
        errors, _ = validate_transactions([{**tx, "line": 3}])
        self.assertEqual(len(errors), 1, errors)
        e = errors[0]
        self.assertTrue(e.startswith("qt.csv line 3: BUYSELL (no symbol) "
                                     "2024-03-20: missing required field "
                                     "'symbol'"), e)
        self.assertNotIn("  ", e)
        self.assertNotIn("row id", e)
        self.assertIn(".tt line", e)
        # No line known: the masked row id, as before.
        errors, _ = validate_transactions([tx])
        self.assertIn("qt.csv (row id b2***): BUYSELL (no symbol)",
                      errors[0])

    def _error_line(self, r):
        self.assertEqual(r.returncode, 1, r.stderr)
        return next(x for x in r.stderr.splitlines()
                    if "missing required field" in x)

    def test_questrade_row_without_symbol(self):
        rows = _QT_ROWS.replace(",Buy,ZZQ,ZZQ CORP,50,", ",Buy,,ZZQ CORP,50,")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "qt_activity.csv"
            p.write_text(_QT_HEAD + rows)
            env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
            r = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_brokerage",
                                   ["--brokerage", "questrade", "--account",
                                    "x", "--strict", str(p)]),
                cwd=REPO_ROOT, capture_output=True, text=True, env=env,
                stdin=subprocess.DEVNULL, timeout=300)
        line = self._error_line(r)
        self.assertIn("qt_activity.csv line 3: BUYSELL (no symbol) "
                      "2024-03-20", line)

    def test_generic_row_without_symbol(self):
        csv_text = ("Date,Transaction type,Symbol,Quantity,Price,Amount,"
                    "Currency\n"
                    "2024-02-01,BUY,ZZQ,10,20.00,-200.00,CAD\n"
                    "2024-02-02,BUY,,10,20.00,-200.00,CAD\n")
        toml_text = ('[columns]\ndate = "Date"\naction = "Transaction type"\n'
                     'symbol = "Symbol"\nquantity = "Quantity"\n'
                     'price = "Price"\namount = "Amount"\n'
                     'currency = "Currency"\n\n[actions]\n"BUY" = "buy"\n')
        r = _brokerage({"ws.csv": csv_text, "ws.csv.toml": toml_text},
                       "generic", "ws.csv")
        self.assertEqual(r.returncode, 0, r.stderr)    # not --strict
        line = next(x for x in r.stderr.splitlines()
                    if "missing required field" in x)
        self.assertIn("ws.csv line 3: BUYSELL (no symbol) 2024-02-02", line)


class TestPastedExports(unittest.TestCase):
    """TJ-0099: two downloads pasted into one file (the header or the
    statement preamble repeated) were booked twice when they overlap:
    Webull silently, Questrade with a false "2 Questrade accounts"
    warning. Overlapping downloads in one file are refused (saved as two
    files, the cross-file rule reads the shared rows once); downloads
    that share no row are still read; RBC names the repeated header."""

    def test_questrade_overlap_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from test_fix_rbcqt import QH, q, qt_parse
        body = q(td="2025-02-03") + q(td="2025-03-03", qty="5",
                                      gross="-250", net="-254.95")
        with self.assertRaises(BrokerageParseError) as cm:
            qt_parse(body + QH + body)
        msg = str(cm.exception)
        self.assertIn("questrade_2025.csv line 4: the header repeats here",
                      msg)
        self.assertIn("2 row(s) are in both (line 5 repeats line 2)", msg)
        self.assertIn("Save each download as its own file", msg)

    def test_questrade_header_line_is_no_second_account(self):
        from test_fix_rbcqt import QH, q, qt_parse
        a = q(td="2025-02-03")
        b = q(td="2025-03-03", qty="5", gross="-250", net="-254.95")
        plain, _, _ = qt_parse(a + b)
        txs, err, _ = qt_parse(a + QH + b)
        self.assertNotIn("Questrade accounts", err)
        self.assertEqual([(t["date"], t["quantity"]) for t in txs],
                         [(t["date"], t["quantity"]) for t in plain])

    def _webull(self, text):
        from taxjson.lib.brokerages.webull import WebullBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "webull_test.csv"
            p.write_text(text)
            with redirect_stderr(io.StringIO()):
                return WebullBrokerage().parse_file(p)

    def test_webull_overlap_is_refused_and_disjoint_downloads_read(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        demo = (REPO_ROOT / "src" / "taxjson" / "data" / "demo" / "webull"
                / "webull_demo.csv").read_text()
        with self.assertRaises(BrokerageParseError) as cm:
            self._webull(demo + demo)
        self.assertIn("the statement preamble (Account Number) repeats "
                      "here", str(cm.exception))
        self.assertIn("booked twice", str(cm.exception))
        # Two downloads with no row in common: each row booked once.
        head, _, rows = demo.partition('"Proceeds"\n')
        rows = rows.splitlines(keepends=True)
        first = head + '"Proceeds"\n' + "".join(rows[:3])
        second = head + '"Proceeds"\n' + "".join(rows[3:])
        self.assertEqual(len(self._webull(first + second)),
                         len(self._webull(demo)))

    def test_rbc_names_the_repeated_header(self):
        demo = (REPO_ROOT / "src" / "taxjson" / "data" / "demo" / "tfsa"
                / "rbc_direct_demo.csv").read_text()
        from test_fix_rbcqt import rbc_parse
        with self.assertRaises(ValueError) as cm:
            rbc_parse(demo + demo, raw=True)
        msg = str(cm.exception)
        self.assertIn("the header repeats here — two downloads pasted", msg)
        self.assertNotIn("is not a number", msg)

    def test_generic_overlap_is_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.brokerages.generic import GenericBrokerage
        head = ("Date,Transaction type,Symbol,Quantity,Price,Amount,"
                "Currency\n")
        body = "2024-02-01,BUY,ZZQ,10,20.00,-200.00,CAD\n"
        toml_text = ('[columns]\ndate = "Date"\naction = "Transaction type"\n'
                     'symbol = "Symbol"\nquantity = "Quantity"\n'
                     'price = "Price"\namount = "Amount"\n'
                     'currency = "Currency"\n\n[actions]\n"BUY" = "buy"\n')
        with tempfile.TemporaryDirectory() as td:
            c = Path(td) / "ws.csv"
            c.write_text(head + body + head + body)
            (Path(td) / "ws.csv.toml").write_text(toml_text)
            with redirect_stderr(io.StringIO()), \
                    self.assertRaises(BrokerageParseError) as cm:
                GenericBrokerage().parse_file(c)
        self.assertIn("ws.csv line 3: the header repeats here",
                      str(cm.exception))


class TestRestatedIdAcrossFiles(unittest.TestCase):
    """TJ-0071: one broker id (a Coinbase ID, a Kraken txid) in two
    DIFFERENT files with different content — a restated row — is
    refused, as one file's twin is; the dedup kept one version by file
    name with no message. Identical overlapping downloads still pass."""

    def _coinbase(self):
        return (REPO_ROOT / "src" / "taxjson" / "data" / "demo" / "crypto"
                / "coinbase_demo.csv").read_text()

    def test_coinbase_restated_row_is_refused(self):
        a = self._coinbase()
        self.assertIn("62000.00,3100.00,3075.20", a)
        b = a.replace("62000.00,3100.00,3075.20", "63000.00,3150.00,3125.20")
        r = _brokerage({"cb_a.csv": a, "cb_b.csv": b}, "coinbase")
        self.assertEqual(r.returncode, 1, r.stderr)
        line = next(x for x in r.stderr.splitlines() if "error:" in x)
        self.assertIn("cb_a.csv and cb_b.csv: row id CB*** is in both with "
                      "DIFFERENT content", line)
        self.assertIn("3075.20", line)
        self.assertIn("3125.20", line)
        self.assertNotIn("CB-DEMO", line)

    def test_identical_overlap_passes(self):
        a = self._coinbase()
        r = _brokerage({"cb_a.csv": a, "cb_b.csv": a}, "coinbase")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_kraken_restated_fill_is_refused(self):
        b = _KT_ROWS.replace("50000,2500,5,", "50000,2500,6,")
        r = _brokerage({"kr_a.csv": _KT_H + _KT_ROWS, "kr_b.csv": _KT_H + b},
                       "kraken")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("row id T2*** is in both with DIFFERENT content",
                      r.stderr)


if __name__ == "__main__":
    unittest.main()
