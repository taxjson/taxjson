"""Recommendation 9: a generic export's header is the first record of the
first HEADER_WITHIN that holds every mapped column (or a [signature]'s
names), so a title, an account line or a blank line above the table is
read past, not refused. Messages name the file's own line; the account
pass and a layout's signature find the same header; `tjs redact` blanks
the lines before it whole (no parse reads them)."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from _tmpfiles import isolate_layouts
from taxjson.bin import taxjson_redact as R
from taxjson.lib.brokerages.generic import (HEADER_WITHIN, GenericBrokerage,
                                            _account_cells, effective_mapping,
                                            header_index, preamble_records)
import _paths

isolate_layouts()

TOML = """\
[columns]
date = "Trade Date"
action = "Type"
symbol = "Ticker"
quantity = "Shares"
price = "Price"
amount = "Net Amount"
currency = "Currency"
account = "Account"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"""
HEADER = "Trade Date,Type,Ticker,Shares,Price,Net Amount,Currency,Account\n"
ROWS = ("2025-01-06,BUY,ZZQ,10,5.00,-50.00,CAD,99900002\n"
        "2025-03-10,SELL,ZZQ,10,6.00,60.00,CAD,99900002\n")
# A title naming the holder, an account line with no `Label:` colon, the
# holder again and a blank line: four lines before the header. The id is
# outside the 999 range on purpose: the redactor takes 9990000N-shaped
# ids for its own placeholders, so a leak of one could pass unseen.
PREAMBLE = ("Activity export for Jane Q Sample\n"
            "Account Number,55500001\n"  # pii-ok (synthetic)
            "Account Holder,Jane Q Sample\n"
            "\n")

# A Webull statement, its account line above the header: the parser's
# fixture (synthetic).
WEBULL = (_paths.TESTS / "fixtures" / "webull"
          / "sample.csv").read_text()
WEBULL_TOML = """\
[columns]
date = "Date"
action = "Action Code"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Proceeds"
currency = "Currency"

[actions]
"BUY" = "buy"
"""


class _Folder(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def export(self, text, toml=TOML, name="activity.csv"):
        csv = self.dir / name
        csv.write_text(text)
        csv.with_name(csv.name + ".toml").write_text(toml)
        return csv

    def parse(self, csv):
        with contextlib.redirect_stderr(io.StringIO()):
            return GenericBrokerage().parse_file(csv)

    def refused(self, text):
        with self.assertRaises(ValueError) as cm:
            self.parse(self.export(text))
        return cm.exception


class TestHeaderWithin(_Folder):

    def test_the_finder(self):
        rows = [["Title"], ["Account Number", "99900001"], [],
                ["Date", "Type", "Amount"], ["2025-01-06", "BUY", "1"]]
        self.assertEqual(header_index(rows, ["date", " TYPE "]), 3)
        # Nothing wanted, or nothing fits: none (a reader takes the first
        # record, as before, and names what is missing).
        self.assertIsNone(header_index(rows, []))
        self.assertIsNone(header_index(rows, ["Date", "Fee"]))
        self.assertEqual(header_index(rows[3:], ["Date"]), 0)
        # Past HEADER_WITHIN records: not looked at.
        far = [["x"]] * HEADER_WITHIN + [["Date", "Type"]]
        self.assertIsNone(header_index(far, ["Date", "Type"]))

    def test_a_preamble_is_read_past(self):
        plain = self.parse(self.export(HEADER + ROWS, name="plain.csv"))
        led = self.parse(self.export(PREAMBLE + HEADER + ROWS))
        self.assertEqual(len(led), 2)
        self.assertEqual([(t["date"], t["action"], t["quantity"],
                           t["net_amount"]) for t in led],
                         [(t["date"], t["action"], t["quantity"],
                           t["net_amount"]) for t in plain])

    def test_a_message_names_the_files_own_line(self):
        # The second data row is cut short: the file's line 7 (four
        # preamble lines, the header, one good row), not the table's 3.
        bad = ROWS.replace("60.00,CAD,99900002", "60.00")
        with self.assertRaises(ValueError) as cm:
            self.parse(self.export(PREAMBLE + HEADER + bad))
        self.assertIn("line 7:", str(cm.exception))

    def test_no_line_holds_the_columns(self):
        with self.assertRaises(ValueError) as cm:
            self.parse(self.export(PREAMBLE + HEADER.replace("Ticker", "Sym")
                                   + ROWS))
        msg = str(cm.exception)
        # The first record stands as the header: every column missing.
        self.assertIn("symbol -> 'Ticker'", msg)
        self.assertIn(f"no row of the first {HEADER_WITHIN} holds every "
                      f"mapped column", msg)

    def test_the_account_pass_reads_past_the_preamble(self):
        csv = self.export(PREAMBLE + HEADER + ROWS)
        self.assertEqual(_account_cells(csv), {"99900002": {""}})

    def test_a_layout_signature_fits_a_header_below_a_title(self):
        layouts = self.dir / "layouts"
        layouts.mkdir()
        sig = '\n[signature]\nheaders = ["Trade Date", "Ticker", "Net Amount"]\n'
        (layouts / "zz.toml").write_text(TOML + sig)
        csv = self.dir / "export.csv"
        csv.write_text(PREAMBLE + HEADER + ROWS)
        old = os.environ.get("TAXJSON_LAYOUTS")
        os.environ["TAXJSON_LAYOUTS"] = str(layouts)
        try:
            self.assertEqual(effective_mapping(csv).name, "zz")
            self.assertEqual(len(self.parse(csv)), 2)
        finally:
            if old is None:
                os.environ.pop("TAXJSON_LAYOUTS", None)
            else:
                os.environ["TAXJSON_LAYOUTS"] = old

    def test_a_record_the_reader_refuses_keeps_the_header_found(self):
        # A lone carriage return in a cell below the header: the header is
        # still the one found, and the parse names the bad record, not the
        # title as a header missing every column.
        bad = ROWS.replace("ZZQ,10,6.00", "ZZ\rQ,10,6.00")
        csv = self.export(PREAMBLE + HEADER + bad)
        self.assertEqual(preamble_records(csv, csv.read_bytes().decode()), 4)
        with self.assertRaises(Exception) as cm:
            self.parse(csv)
        self.assertNotIn("not in the CSV header", str(cm.exception))

    def test_rows_of_data_above_the_header_are_refused(self):
        # An older export pasted above a newer one whose header adds a
        # mapped column: its rows would be read past with no word.
        old = HEADER.replace(",Account", "")
        rows = "".join(r.rsplit(",", 1)[0] + "\n" for r in ROWS.splitlines())
        msg = str(self.refused(old + rows + HEADER + ROWS))
        self.assertIn("activity.csv line 2: a row above the header (line 4) "
                      "reads as data", msg)
        # A title or an account line (fewer than three cells, no date) is
        # no row of data.
        self.assertEqual(len(self.parse(self.export(PREAMBLE + HEADER + ROWS))), 2)

    def test_a_second_exports_title_is_named(self):
        # Two such exports pasted into one file: the second's title is
        # read as a row, and the refusal says what it may be.
        msg = str(self.refused(PREAMBLE + HEADER + ROWS + PREAMBLE + HEADER + ROWS))
        self.assertIn("a title or account line of a second export", msg)

    def test_a_signature_naming_a_brokers_columns_is_ambiguous(self):
        # The documented rule, wherever the header sits: Webull's header
        # is below its account line, and a signature naming its columns
        # fits it (docs/upgrading.md).
        from taxjson.lib.brokerages.detect import AmbiguousBroker, detect
        csv = self.dir / "activity.csv"
        csv.write_text(WEBULL)
        (self.dir / "generic.toml").write_text(
            WEBULL_TOML + '\n[signature]\nheaders = ["Action Code", "Type Code", "Proceeds"]\n')
        with self.assertRaises(AmbiguousBroker):
            detect(csv)



class TestRedactBlanksThePreamble(_Folder):

    def test_the_lines_before_the_header(self):
        csv = self.export(PREAMBLE + HEADER + ROWS)
        self.assertEqual(preamble_records(csv, csv.read_text()), 4)
        out = self.dir / "out"
        out.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            dst, rep = R.redact_file(csv, out, [], False)
        copy = dst.read_text()
        for leak in ("Jane", "Sample", "55500001"):  # pii-ok
            self.assertNotIn(leak, copy)
        lines = copy.splitlines()
        # The line count stays (the parse's line numbers do too); the
        # table is the redactor's as before.
        self.assertEqual(len(lines), len((PREAMBLE + HEADER + ROWS).splitlines()))
        self.assertEqual(lines[:4], ["REDACTED", "REDACTED,REDACTED",
                                     "REDACTED,REDACTED", ""])
        self.assertEqual(lines[4], HEADER.rstrip("\n"))
        self.assertEqual(rep.identity_rows, 3)

    def test_a_quoted_title_over_two_lines(self):
        csv = self.export('"Activity export\nfor Jane Q Sample"\n' + HEADER + ROWS)
        self.assertEqual(preamble_records(csv, csv.read_text()), 1)
        text, _rep = R.redact_text(csv.read_text(), preamble=1)
        self.assertNotIn("Jane", text)
        self.assertTrue(text.startswith('"REDACTED"\n' + HEADER), text)

    def test_a_lone_carriage_return_in_a_quoted_title(self):
        # The redactor splits lines at a lone carriage return, the csv
        # reader does not: counted in records, both blank the same lines.
        body = ('"Activity export\rJane Q Sample"\nAccount Number,55500001\n'  # pii-ok
                + HEADER + ROWS)
        csv = self.dir / "activity.csv"
        csv.write_bytes(body.encode())
        csv.with_name(csv.name + ".toml").write_text(TOML)
        self.assertEqual(preamble_records(csv, body), 2)
        text, _rep = R.redact_text(body, preamble=2)
        for leak in ("Jane", "55500001"):  # pii-ok
            self.assertNotIn(leak, text)
        self.assertIn(HEADER, text)

    def test_a_brokers_export_beside_a_generic_mapping(self):
        # A folder's generic.toml naming Webull's columns (no signature,
        # or one that makes the file ambiguous): the run reads the file
        # with Webull's parser, which reads its account line, so redact
        # leaves those lines to its own rules (the account a placeholder).
        csv = self.dir / "activity.csv"
        csv.write_text(WEBULL)
        (self.dir / "generic.toml").write_text(WEBULL_TOML)
        self.assertEqual(preamble_records(csv, WEBULL), 0)    # no signature: no mapping
        self.assertEqual(R._preamble(csv, WEBULL, R._detected(csv)), 0)
        (self.dir / "generic.toml").write_text(
            WEBULL_TOML + '\n[signature]\nheaders = ["Action Code", "Type Code", "Proceeds"]\n')
        self.assertEqual(R._preamble(csv, WEBULL, R._detected(csv)), 0)      # ambiguous: the run refuses it
        text, rep = R.redact_text(WEBULL, preamble=R._preamble(csv, WEBULL, R._detected(csv)))
        # The line kept, its id the placeholder (redact's own rule), not
        # REDACTED: the Webull parser reads the account from it.
        self.assertEqual(len(rep.accounts), 1)
        self.assertEqual(text.splitlines()[0],
                         "Account Number: " + next(iter(rep.accounts.values())))

    def test_a_broker_export_has_none(self):
        # No mapping describes it: nothing is blanked by this pass.
        csv = self.dir / "activity.csv"
        csv.write_text(PREAMBLE + HEADER + ROWS)
        self.assertEqual(preamble_records(csv, csv.read_text()), 0)

    def test_the_project_copy(self):
        root = self.dir / "proj"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
        csv = root / "inputs" / "margin" / "activity.csv"
        csv.write_text(PREAMBLE + HEADER + ROWS)
        csv.with_name(csv.name + ".toml").write_text(TOML)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(R.redact_tree(root, None, [], False, False), 0)
        copy = (root / "inputs_redact" / "margin" / "activity.csv").read_text()
        for leak in ("Jane", "Sample", "55500001"):  # pii-ok
            self.assertNotIn(leak, copy)
        self.assertIn(HEADER, copy)


if __name__ == "__main__":
    unittest.main()
