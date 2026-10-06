"""Pre-release review of the Questrade internal-code inference
(lib/symbol_codes, tax-logic CA-ACB-CODES / US-BASIS-CODES) and of the
console file names / message labels added since v0.19.0.

M1  a name-only match keeps the share designators (class letter, voting
    rights, ADR / ordinary, NEW ...) and must be EXACT and unique; a near
    name is only suggested (the ATTENTION line names the GLOBAL line).
    Transfer pairing refuses a leg whose name is another class.
M2  any ticker.map rule naming the code (DELETE, DISTINCT, dated RENAME,
    GLOBAL ...) wins over the inference, in the run and in the parser.
L1  the record and `taxjson transfers` say "ticker.map rule" then.
L2  a transfer-in that pairs with nothing is not identified by name.
L3  pairing on a subset of words needs two strong words in common.
L4  a damaged record is ignored with one warning, never a traceback.
L5  a file name's control characters are escaped on the console and in
    the .diag.
L6  out.labelled() escapes control characters shown to a person.

Every fixture is SYNTHETIC: invented tickers (QZ*), codes and names,
fake account ids (pii-ok: 55500001, U5550001).
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taxjson.lib import symbol_codes as SC
from taxjson.lib.brokerages.questrade import QuestradeBrokerage
from tax_rules import rule
from test_fix_qtcodes import (CONFIG, QH, SELL_M, TFI_M, DIV_M, DIV_NAME,
                              _run, ib_statement, q)


def ok_listing(listing, cur):
    return QuestradeBrokerage().apply_currency_suffix(listing, cur) == listing


def use(code, name, arrivals=(), cur='USD'):
    return SC.CodeUse(code=code, name=name, currencies=[cur],
                      arrivals=list(arrivals), rows=1)


def entry(sym, name):
    return SC.NameEntry(sym, SC.name_tokens(name), name, 'ibm', 'ib')


def leg(sym, date, qty, name, cur='USD'):
    return SC.OutLeg('ibm', 'ib', sym, date, qty, cur,
                     [SC.name_tokens(name)])


def res(uses, outs=(), names=(), mapped=lambda c: False):
    return SC.resolve(uses, list(outs), list(names), listing_ok=ok_listing,
                      mapped=mapped)


# ------------------------------------------------------------ L5 / L6

class TestFileNamesStayOneLine(unittest.TestCase):
    def test_shown_name_and_one_line_escape_controls(self):
        from taxjson.lib.brokerages.base import shown_name
        from taxjson.lib.out import one_line
        self.assertEqual(shown_name('/x/qz\nfake\r.csv'),
                         'qz\\nfake\\r.csv')
        self.assertEqual(one_line('a\tb\x1bc'), 'a\\tb\\x1bc')
        self.assertEqual(one_line(one_line('a\nb')), 'a\\nb')

    def test_run_console_and_diag_keep_a_newline_name_on_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(CONFIG.replace(
                "[accounts.ibm]\ntype = \"taxable\"\n\n", ""))
            d = root / "inputs" / "qt"
            d.mkdir(parents=True)
            buy = q('2026-03-02', 'Buy', 'QZM', 'QZM MINING CORP', '10',
                    net='-100.00', act='Trades', price='10', gross='-100')
            (d / "qz\nnote: forged.csv").write_text(QH + buy)
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("inputs/qt/qz\\nnote: forged.csv → Questrade",
                          r.stdout)
            for out in (r.stdout, r.stderr):
                self.assertFalse([ln for ln in out.splitlines()
                                  if ln.lstrip().startswith('note: forged')])
            for diag in (root / "work").glob("*.diag"):
                with self.subTest(diag=diag.name):
                    self.assertFalse(
                        [ln for ln in diag.read_text().splitlines()
                         if ln.lstrip().startswith('note: forged')])


class TestLabelledEscapesControls(unittest.TestCase):
    def test_shown_to_a_person(self):
        from taxjson.lib.out import labelled
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "100"}):
            got = labelled("warning: QZ\x1b[2Jdesc\x07")
        self.assertEqual(got, "Warning: QZ\\x1b[2Jdesc\\x07")

    def test_captured_keeps_its_bytes(self):
        from taxjson.lib.out import labelled
        with mock.patch.dict(os.environ, {"TAXJSON_WIDTH": "0"}):
            self.assertEqual(labelled("warning: a\x1bb"), "warning: a\x1bb")


if __name__ == "__main__":
    unittest.main()
