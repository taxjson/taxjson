"""Tests for taxjson-convert-tt bi-directional conversion."""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.tt_format import parse_tt_line, tx_to_tt_line, tt_to_json, json_to_tt_lines


class TestParseTTLine(unittest.TestCase):
    def test_buysell(self):
        tx = parse_tt_line(
            "BUYSELL 2025-01-15 09:30:00 AAPL.US 100 USD 150.00 15000.00 5.00"
        )
        self.assertEqual(tx['action'], 'BUYSELL')
        self.assertEqual(tx['symbol'], 'AAPL.US')
        self.assertAlmostEqual(tx['quantity'], 100)
        self.assertEqual(tx['currency'], 'USD')
        self.assertAlmostEqual(tx['fee'], 5.0)

    def test_dividend(self):
        tx = parse_tt_line(
            "DIVIDEND 2025-03-15 09:30:00 X 0 USD 0.00 100.00"
        )
        self.assertEqual(tx['action'], 'DIVIDEND')
        self.assertAlmostEqual(tx['gross_amount'], 100.0)
        self.assertAlmostEqual(tx['net_amount'], 100.0)

    def test_comments_skipped(self):
        self.assertIsNone(parse_tt_line("# this is a comment"))
        self.assertIsNone(parse_tt_line(""))
        # Re-premised (crypto/tt hardening): a non-comment line whose
        # first token is not an action used to return None — silently
        # dropping `SELL`/`buysell`/`Dividend` rows with exit 0. It now
        # raises, naming the valid actions.
        with self.assertRaises(ValueError) as cm:
            parse_tt_line("GAIN 2025-01-15 09:30:00 X 1 USD 1 1")
        self.assertIn("unknown .tt action 'GAIN'", str(cm.exception))


class TestTxToTTLine(unittest.TestCase):
    def test_buysell_emits_canonical_field_order(self):
        tx = {
            'action': 'BUYSELL', 'date': '2025-01-15', 'time': '09:30:00',
            'symbol': 'AAPL.US', 'quantity': 100, 'currency': 'USD',
            'price': 150.0, 'net_amount': 15000.0, 'fee': 5.0,
        }
        line = tx_to_tt_line(tx)
        self.assertIsNotNone(line)
        parts = line.split()
        self.assertEqual(parts[0], 'BUYSELL')
        self.assertEqual(parts[3], 'AAPL.US')
        # Numeric fields formatted with explicit precision (qty/price %.8f,
        # totals %.5f) — matches the Perl scripts so old tools can re-ingest.
        self.assertIn('100.00000000', parts)
        self.assertIn('15000.00000', parts)

    def test_dividend_uses_gross_when_present(self):
        # parsers may set gross_amount as the pre-withholding figure; that's
        # the value `.tt` consumers expect on the total field.
        tx = {
            'action': 'DIVIDEND', 'date': '2025-03-15', 'time': '09:30:00',
            'symbol': 'X', 'quantity': 0, 'currency': 'USD',
            'price': 0, 'net_amount': 85.0, 'gross_amount': 100.0,
        }
        line = tx_to_tt_line(tx)
        self.assertIn('100.00000', line)
        # The withholding-NETTED value rides along as an optional 9th
        # column (2026-08 deep audit: without it a json→tt→json cycle
        # re-parsed net as gross and inflated income). Legacy consumers
        # ignore trailing columns; the total field is still the gross.
        self.assertTrue(line.rstrip().endswith('85.00000'), line)
        from taxjson.lib.tt_format import parse_tt_line
        back = parse_tt_line(line)
        self.assertAlmostEqual(back['gross_amount'], 100.0, places=2)
        self.assertAlmostEqual(back['net_amount'], 85.0, places=2)

    def test_interest(self):
        tx = {
            'action': 'INTEREST', 'date': '2025-01-02', 'time': '09:30:00',
            'currency': 'USD', 'net_amount': 385.77,
        }
        self.assertIn('385.77000', tx_to_tt_line(tx))

    def test_unknown_action_skipped(self):
        # OPENING_BALANCE etc. have no .tt representation — caller should
        # see None and skip rather than emit malformed output.
        tx = {'action': 'OPENING_BALANCE', 'date': '2025-01-01'}
        self.assertIsNone(tx_to_tt_line(tx))


class TestRoundTrip(unittest.TestCase):
    """tt → json → tt should preserve all tx-shaped data. Round-tripping is
    the contract that protects users who use the script as a translator
    between the legacy Perl pipeline and the Python flow."""

    SAMPLE_TT = """\
BUYSELL 2025-01-15 09:30:00 AAPL.US 100.00000000 USD 150.00000000 15000.00000 5.00000
BUYSELL 2025-03-15 14:00:00 AAPL.US -50.00000000 USD 175.00000000 8750.00000 5.00000
DIVIDEND 2025-04-01 09:30:00 AAPL.US 0.00000000 USD 0.00000000 100.00000
INTEREST 2025-01-02 09:30:00 USD 385.77000
"""

    def test_tt_to_json_to_tt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tt_path = Path(tmpdir) / "in.tt"
            tt_path.write_text(self.SAMPLE_TT)
            data = tt_to_json(tt_path, 'TestAcct')
            self.assertEqual(len(data['transactions']), 4)

            # Round-trip back to tt.
            json_path = Path(tmpdir) / "mid.json"
            json_path.write_text(json.dumps(data))
            lines = list(json_to_tt_lines(json_path))
            self.assertEqual(len(lines), 4)

            # Re-parse the emitted tt and check key fields survive.
            roundtrip_path = Path(tmpdir) / "out.tt"
            roundtrip_path.write_text("\n".join(lines) + "\n")
            data2 = tt_to_json(roundtrip_path, 'TestAcct')

            for orig, rt in zip(data['transactions'], data2['transactions']):
                self.assertEqual(orig['action'], rt['action'])
                self.assertEqual(orig['date'], rt['date'])
                self.assertEqual(orig.get('symbol'), rt.get('symbol'))
                self.assertAlmostEqual(
                    float(orig.get('quantity', 0)),
                    float(rt.get('quantity', 0)),
                    places=6,
                )
                self.assertAlmostEqual(
                    float(orig.get('net_amount', 0)),
                    float(rt.get('net_amount', 0)),
                    places=4,
                )


class TestJsonReadAsEveryToolReadsIt(unittest.TestCase):
    """TJ-5035: the JSON-to-.tt writer read the book with its own json.load:
    a BUYSELL with no net_amount became a .tt line with a total of
    0.00000 (read back as a purchase at no cost, with only a total
    mismatch warning), and a BOM or a `#` comment, which every other
    tool reads, was a JSONDecodeError."""

    _ROW = {"action": "BUYSELL", "date": "2025-02-03", "time": "10:00:00",
            "date_settle": "2025-02-03", "symbol": "QZRX.TO",
            "quantity": 10, "price": 10, "net_amount": 100,
            "currency": "CAD", "account": "margin"}

    def _file(self, text, bom=False):
        from _tmpfiles import private_tmpfile
        with private_tmpfile("wb", suffix=".json", delete=False) as f:
            f.write((b"\xef\xbb\xbf" if bom else b"") + text.encode())
        return Path(f.name)

    def test_a_trade_with_no_amount_is_refused(self):
        from taxjson.lib.cli_diag import InputContentError
        # What its line carried: a quantity of 0 beside the row's total,
        # or a total of 0.
        for drop, wrote in (("net_amount", "a total of 0, read as a cost"),
                            ("quantity", "a quantity of 0 beside the "
                                         "row's total")):
            with self.subTest(missing=drop):
                row = {k: v for k, v in self._ROW.items() if k != drop}
                p = self._file(json.dumps({"transactions": [row]}))
                with self.assertRaises(InputContentError) as cm:
                    list(json_to_tt_lines(p))
                msg = str(cm.exception)
                self.assertIn(f"required field(s) {drop} missing", msg)
                self.assertIn(f"would carry {wrote}", msg)
                self.assertIn(str(p), msg)
                self.assertIn("nothing was written", msg)
        # An explicit 0 is an amount: written as it is.
        p = self._file(json.dumps({"transactions": [dict(self._ROW,
                                                         net_amount=0)]}))
        line, = json_to_tt_lines(p)
        self.assertIn(" 0.00000 ", line)

    def test_bom_and_comments_read_as_the_loader_reads_them(self):
        plain = list(json_to_tt_lines(self._file(json.dumps(
            {"transactions": [self._ROW]}))))
        self.assertEqual(len(plain), 1)
        text = ("# a note\n" + json.dumps({"transactions": [self._ROW]})
                + "  # trailing\n")
        for bom in (False, True):
            with self.subTest(bom=bom):
                self.assertEqual(list(json_to_tt_lines(
                    self._file(text, bom=bom))), plain)

    def test_the_tool_refuses_in_one_line(self):
        import contextlib
        import io
        from unittest import mock
        from taxjson.bin import taxjson_convert_tt
        row = {k: v for k, v in self._ROW.items() if k != "net_amount"}
        p = self._file(json.dumps({"transactions": [row]}))
        out = p.with_name("out.tt")
        err = io.StringIO()
        with mock.patch("sys.argv", ["taxjson-convert-tt", str(p),
                                     str(out), "--date-basis", "settle"]), \
                contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as cm:
            taxjson_convert_tt.main()
        # The tool's refusal of an input it cannot convert (a malformed
        # .tt line's too).
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("taxjson-convert-tt: error:", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertFalse(out.exists())

    def test_a_book_it_cannot_read_exits_2(self):
        # TJ-5154: a book that is not valid JSON (and, since TJ-5035, one
        # that is not UTF-8) is an input that cannot be read: exit 2, as
        # docs/commands.md's exit table gives it, naming the file; a row
        # it cannot convert stays exit 1 (test_the_tool_refuses_in_one_line).
        import contextlib
        import io
        from unittest import mock
        from taxjson.bin import taxjson_convert_tt
        for raw, says in ((b'{"transactions": [', "Expecting value"),
                          (b'{"transactions": [{"symbol": "QZ\xe9.TO"}]}',
                           "not UTF-8")):
            for basis in ([], ["--date-basis", "settle"]):
                with self.subTest(says=says, basis=basis):
                    from _tmpfiles import private_tmpfile
                    with private_tmpfile("wb", suffix=".json",
                                         delete=False) as f:
                        f.write(raw)
                    p = Path(f.name)
                    out = p.with_name("out.tt")
                    err = io.StringIO()
                    with mock.patch("sys.argv", ["taxjson-convert-tt",
                                                 str(p), str(out), *basis]), \
                            contextlib.redirect_stderr(err), \
                            self.assertRaises(SystemExit) as cm:
                        taxjson_convert_tt.main()
                    self.assertEqual(cm.exception.code, 2, err.getvalue())
                    self.assertIn("taxjson-convert-tt: error:", err.getvalue())
                    self.assertIn(says, err.getvalue())
                    self.assertIn(p.name, err.getvalue())
                    self.assertNotIn("Traceback", err.getvalue())
                    self.assertFalse(out.exists())

    def test_the_tool_with_no_date_basis_reads_it_alike(self):
        # TJ-5151: outside a project and with no --date-basis, the tool
        # read the book itself to see whether one is needed: a BOM or a
        # `#` comment was "Unexpected UTF-8 BOM" / "Expecting value".
        import contextlib
        import io
        from unittest import mock
        from taxjson.bin import taxjson_convert_tt
        plain = list(json_to_tt_lines(self._file(json.dumps(
            {"transactions": [self._ROW]}))))
        two = dict(self._ROW, date_settle="2025-02-05")
        for rows, bom, code in (([self._ROW], True, None),
                                ([self._ROW], False, None),
                                ([two], True, 2)):
            with self.subTest(bom=bom, two_dates=code is not None):
                p = self._file("# a note\n" + json.dumps(
                    {"transactions": rows}) + "\n", bom=bom)
                self.assertFalse((p.parent / "taxjson.toml").exists())
                out = p.with_name("out.tt")
                err = io.StringIO()
                with mock.patch("sys.argv", ["taxjson-convert-tt", str(p),
                                             str(out)]), \
                        contextlib.redirect_stderr(err):
                    try:
                        taxjson_convert_tt.main()
                        got = None
                    except SystemExit as e:
                        got = e.code
                self.assertEqual(got, code, err.getvalue())
                self.assertNotIn("BOM", err.getvalue())
                self.assertNotIn("Expecting value", err.getvalue())
                if code is None:
                    self.assertEqual(out.read_text().splitlines(), plain)
                else:
                    self.assertIn("pass --date-basis", err.getvalue())
                    self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
