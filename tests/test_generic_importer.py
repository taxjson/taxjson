"""Generic column-mapped CSV importer (`generic_*.csv` + TOML mapping)."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from _tmpfiles import isolate_layouts
import _paths
from _support.generic_toml import _TOML

REPO_ROOT = _paths.REPO


def setUpModule():
    # No user layouts folder routes a test's CSV (tests/_tmpfiles.py): for
    # the whole process on purpose, not restored, as it was at import.
    isolate_layouts()


_CSV = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
        "01/15/2025,BUY,XEI,100,10.00,-1005.00,CAD\n"
        "06/20/2025,SELL,XEI,100,15.00,1495.00,CAD\n"
        "03/01/2025,DIV,XEI,0,0,18.00,CAD\n"
        "03/01/2025,DIV,XEI,0,0,-18.00,CAD\n"
        "03-05-2025,NRT,XEI,0,0,-2.70,CAD\n"
        "04/01/2025,JNL,XEI,0,0,0,CAD\n"
        "05/01/2025,MYSTERY,XEI,0,0,1.00,CAD\n")


def _parse(csv_text, toml_text, sidecar=True):
    from taxjson.lib.brokerages.generic import GenericBrokerage
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "generic_test.csv"
        c.write_text(csv_text)
        m = (c.with_name(c.name + ".toml") if sidecar
             else Path(td) / "generic.toml")
        m.write_text(toml_text)
        buf = io.StringIO()
        with redirect_stderr(buf):
            txs = GenericBrokerage().parse_file(c)
        return txs, buf.getvalue()


class TestGenericMapping(unittest.TestCase):
    def test_full_mapping_signs_and_suffixes(self):
        # The NRT row uses a DIFFERENT date shape on purpose — but only
        # rows whose action maps get parsed, so make it valid first:
        csv = _CSV.replace("03-05-2025", "03/05/2025")
        txs, err = _parse(csv, _TOML)
        by = {}
        for t in txs:
            by.setdefault(t["action"], []).append(t)
        buy, sell = sorted(by["BUYSELL"], key=lambda t: t["date"])
        self.assertEqual(buy["symbol"], "XEI.TO")   # suffix from CAD
        self.assertEqual(buy["quantity"], 100.0)
        self.assertAlmostEqual(buy["net_amount"], 1005.0)
        self.assertEqual(sell["quantity"], -100.0)
        self.assertEqual(buy["date"], "2025-01-15")  # %m/%d/%Y honored
        # Dividend signs preserved (reversal nets to zero).
        divs = by["DIVIDEND"]
        self.assertAlmostEqual(sum(d["net_amount"] for d in divs), 0.0)
        # TAX flips to positive-withheld.
        self.assertAlmostEqual(by["TAX"][0]["net_amount"], 2.70)
        # skip-mapped and unmapped actions are counted, not silent.
        self.assertIn("JNL", err)
        self.assertIn("MYSTERY", err)
        # Unmapped rows produce no transactions.
        self.assertEqual(len(txs), 5)

    def test_shared_folder_mapping_works(self):
        csv = _CSV.replace("03-05-2025", "03/05/2025")
        txs, _ = _parse(csv, _TOML, sidecar=False)
        self.assertEqual(len(txs), 5)

    def test_missing_mapping_is_loud(self):
        from taxjson.lib.brokerages.generic import GenericBrokerage
        with tempfile.TemporaryDirectory() as td:
            c = Path(td) / "generic_x.csv"
            c.write_text(_CSV)
            with self.assertRaises(ValueError) as cm:
                GenericBrokerage().parse_file(c)
        self.assertIn("no mapping", str(cm.exception))

    def test_mapped_column_absent_is_loud(self):
        bad = _TOML.replace('symbol = "Symbol"', 'symbol = "Ticker"')
        with self.assertRaises(ValueError) as cm:
            _parse(_CSV, bad)
        self.assertIn("Ticker", str(cm.exception))

    def test_bad_date_format_is_loud(self):
        # The NRT row's 03-05-2025 doesn't match %m/%d/%Y.
        with self.assertRaises(ValueError) as cm:
            _parse(_CSV, _TOML)
        self.assertIn("unparseable date", str(cm.exception))

    def test_unknown_action_target_is_loud(self):
        bad = _TOML.replace('"BUY" = "buy"', '"BUY" = "purchase"')
        with self.assertRaises(ValueError) as cm:
            _parse(_CSV, bad)
        self.assertIn("purchase", str(cm.exception))


_SPLIT_CSV = ("Date,Transaction type,Symbol,New Symbol,Ratio,Quantity,Price,Amount,Currency\n"
              "01/15/2025,BUY,XEI,,,100,10.00,-1005.00,CAD\n"
              "02/10/2025,SPLIT,XEI,,2,100,,,CAD\n"
              "04/01/2025,RENAME,XEI,XEJ,,0,,,CAD\n"
              "06/20/2025,SELL,XEJ,,,200,7.50,1495.00,CAD\n")
_SPLIT_TOML = (_TOML.replace('symbol = "Symbol"\n',
                             'symbol = "Symbol"\nsymbol_new = "New Symbol"\nratio = "Ratio"\n')
               .replace('"JNL" = "skip"\n', '"SPLIT" = "split"\n"RENAME" = "rename"\n"JNL" = "skip"\n'))


class TestGenericSplits(unittest.TestCase):
    """The split and rename targets (step 5b): one SPLIT row each, as the
    broker parsers emit it. The ratio is the mapped `ratio` cell, never the
    quantity cell; both targets read the new-ticker cell."""

    def test_a_split_scales_and_a_rename_renames(self):
        txs, _ = _parse(_SPLIT_CSV, _SPLIT_TOML)
        splits = [t for t in txs if t["action"] == "SPLIT"]
        self.assertEqual([t["quantity"] for t in splits], [2.0, 1.0])
        self.assertEqual(splits[0]["symbol_new"], "")
        self.assertTrue(splits[1]["symbol_new"].startswith("XEJ"), splits[1])
        self.assertEqual(splits[1]["symbol"], splits[0]["symbol"])
        for t in splits:
            self.assertEqual((t["price"], t["net_amount"]), (0.0, 0.0))
            self.assertEqual(t["date_settle"], t["date"])

    def test_a_split_without_a_ratio_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_SPLIT_CSV.replace("SPLIT,XEI,,2,100,", "SPLIT,XEI,,,100,"), _SPLIT_TOML)
        self.assertIn("positive ratio", str(cm.exception))

    def test_a_rename_with_a_ratio_of_zero_is_refused(self):
        # TJ-5019: a 0 in the ratio cell read as a blank one and the
        # rename went through at 1-for-1; a negative ratio was refused.
        for cell in ("0", "0.0", "-1"):
            with self.subTest(cell=cell), self.assertRaises(ValueError) as cm:
                _parse(_SPLIT_CSV.replace("RENAME,XEI,XEJ,,", f"RENAME,XEI,XEJ,{cell},"),
                       _SPLIT_TOML)
            self.assertIn("line 4", str(cm.exception))
            self.assertIn("positive ratio", str(cm.exception))
            self.assertIn("leaves the cell blank", str(cm.exception))
            self.assertIn("unmap [columns].ratio", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            _parse(_SPLIT_CSV.replace("SPLIT,XEI,,2,100,", "SPLIT,XEI,,0,100,"), _SPLIT_TOML)
        self.assertIn("positive ratio", str(cm.exception))
        self.assertNotIn("leaves the cell blank", str(cm.exception))
        # A rename with its own ratio keeps it.
        txs, _ = _parse(_SPLIT_CSV.replace("RENAME,XEI,XEJ,,", "RENAME,XEI,XEJ,0.5,"),
                        _SPLIT_TOML)
        self.assertEqual([t["quantity"] for t in txs if t["action"] == "SPLIT"], [2.0, 0.5])

    def test_the_quantity_cell_is_never_the_ratio(self):
        # The ratio column is required for split; shares received or held
        # sit in the quantity cell and must not scale the pool (TJ-2077).
        bad = _SPLIT_TOML.replace('ratio = "Ratio"\n', '')
        with self.assertRaises(ValueError) as cm:
            _parse(_SPLIT_CSV, bad)
        self.assertIn("[columns].ratio", str(cm.exception))
        # A 50-for-1 split exists; a thousand new units per old one is a
        # share count that landed in the ratio cell.
        with self.assertRaises(ValueError) as cm:
            _parse(_SPLIT_CSV.replace("SPLIT,XEI,,2,100,", "SPLIT,XEI,,1000,100,"), _SPLIT_TOML)
        self.assertIn("share count", str(cm.exception))

    def test_money_on_a_split_row_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_SPLIT_CSV.replace("SPLIT,XEI,,2,100,,,", "SPLIT,XEI,,2,100,,12.34,"), _SPLIT_TOML)
        self.assertIn("books no money", str(cm.exception))

    def test_a_rename_without_its_column_is_refused(self):
        bad = _SPLIT_TOML.replace('symbol_new = "New Symbol"\n', '')
        with self.assertRaises(ValueError) as cm:
            _parse(_SPLIT_CSV, bad)
        self.assertIn("symbol_new", str(cm.exception))

    def test_a_rename_with_a_blank_new_ticker_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_SPLIT_CSV.replace("RENAME,XEI,XEJ,", "RENAME,XEI,,"), _SPLIT_TOML)
        self.assertIn("new-ticker", str(cm.exception))

    def test_a_split_with_a_new_ticker_renames_too(self):
        txs, _ = _parse(_SPLIT_CSV.replace("SPLIT,XEI,,2,100,", "SPLIT,XEI,XEJ,2,100,")
                        .replace("RENAME,XEI,XEJ,,0,", "JNL,XEJ,,,0,"), _SPLIT_TOML)
        splits = [t for t in txs if t["action"] == "SPLIT"]
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]["quantity"], 2.0)
        self.assertTrue(splits[0]["symbol_new"].startswith("XEJ"))

    def test_a_one_for_one_split_with_no_new_ticker_books_nothing(self):
        txs, _ = _parse(_SPLIT_CSV.replace("SPLIT,XEI,,2,100,", "SPLIT,XEI,,1,100,"), _SPLIT_TOML)
        self.assertEqual([t["quantity"] for t in txs if t["action"] == "SPLIT"], [1.0])  # the rename
        self.assertEqual([t["symbol_new"] for t in txs if t["action"] == "SPLIT"][0][:3], "XEJ")

    def test_a_fraction_is_never_a_share_count(self):
        txs, _ = _parse(_SPLIT_CSV.replace("SPLIT,XEI,,2,100,", "SPLIT,XEI,,0.001,100,"), _SPLIT_TOML)
        self.assertEqual([t["quantity"] for t in txs if t["action"] == "SPLIT"][0], 0.001)

    def test_an_unmapped_split_row_is_unbooked(self):
        toml = _SPLIT_TOML.replace('"SPLIT" = "split"\n', '')
        _, err = _parse(_SPLIT_CSV.replace("SPLIT,XEI,,2,100,", "SPLIT,XEI,,2,0,"), toml)
        self.assertIn("not in [actions]", err)
        self.assertIn("ratio", err)

    def test_a_rename_to_itself_books_nothing(self):
        txs, _ = _parse(_SPLIT_CSV.replace("RENAME,XEI,XEJ,,0,", "RENAME,XEI,XEI,,0,")
                        .replace("SELL,XEJ,", "SELL,XEI,"), _SPLIT_TOML)
        self.assertEqual([t["quantity"] for t in txs if t["action"] == "SPLIT"], [2.0])


_EVENT_CSV = ("Date,Transaction type,Symbol,New Symbol,Ratio,Quantity,Price,Amount,Currency,"
              "Book Value,Value,Acquired\n"
              "01/02/2025,OPEN,XEI,,,50,,500.00,CAD,,,05/01/2023\n"
              "01/15/2025,BUY,XEI,,,100,10.00,-1005.00,CAD,,,\n"
              "01/20/2025,TFO,XEI,,,25,12.00,,CAD,,300.00,\n"
              "03/10/2025,ROC,XEI,,,0,,25.00,CAD,,,\n"
              "03/12/2025,ADJ,XEI,,,0,,-10.00,CAD,,,\n")
_EVENT_TOML = (_SPLIT_TOML
               .replace('ratio = "Ratio"\n', 'ratio = "Ratio"\nbook_value = "Book Value"\n'
                        'market_value = "Value"\nlot_date = "Acquired"\n')
               .replace('"JNL" = "skip"\n',
                        '"OPEN" = "opening"\n"TFO" = "transfer_out"\n"TFI" = "transfer_in"\n'
                        '"ROC" = "roc"\n"ADJ" = "adjust"\n"JNL" = "skip"\n'))


class TestGenericEvents(unittest.TestCase):
    """The transfer, opening, roc and adjust targets (step 5b): the rows the
    broker parsers and the .tt lines book, the quantity and amount cells
    read as each target says, a fee on any of them refused."""

    def test_the_four_rows(self):
        txs, _ = _parse(_EVENT_CSV, _EVENT_TOML)
        by = {t["description"]: t for t in txs}
        opening = by["OPEN, acquired 2023-05-01"]
        self.assertEqual((opening["action"], opening["type"], opening["time"]),
                         ("BUYSELL", "opening", "00:00:00"))
        self.assertEqual((opening["quantity"], opening["price"], opening["net_amount"]),
                         (50.0, 10.0, 500.0))
        self.assertEqual(opening["lot_date"], "2023-05-01")
        transfer = by["TFO"]
        # The direction is the target's; the units unsigned in the export;
        # the price ignored; the value at transfer from its own column.
        self.assertEqual((transfer["action"], transfer["quantity"], transfer["net_amount"],
                          transfer["market_value"], transfer["time"]),
                         ("TRANSFER", -25.0, 0.0, 300.0, "09:30:00"))
        roc = by["ROC"]
        self.assertEqual((roc["action"], roc["net_amount"], roc["type"], roc["quantity"]),
                         ("ADJUST", -25.0, "roc", 0.0))
        self.assertEqual(transfer.get("book_value"), None)      # a transfer out
        adj = by["ADJ"]
        self.assertEqual((adj["action"], adj["net_amount"], adj.get("type"), adj["time"]),
                         ("ADJUST", -10.0, None, "09:30:00"))

    def test_a_roc_reversal_nets_out_and_a_transfer_in_states_its_book_value(self):
        csv = _EVENT_CSV.replace("TFO,XEI,,,25,12.00,,CAD,,300.00,", "TFI,XEI,,,25,,,CAD,300.00,,") + \
            "03/11/2025,ROC,XEI,,,0,,-25.00,CAD,,,\n"
        txs, _ = _parse(csv, _EVENT_TOML)
        rocs = [t["net_amount"] for t in txs if t.get("type") == "roc"]
        self.assertEqual(rocs, [-25.0, 25.0])
        tin = [t for t in txs if t["action"] == "TRANSFER"][0]
        self.assertEqual((tin["quantity"], tin["net_amount"], tin["book_value"]), (25.0, 0.0, 300.0))
        self.assertNotIn("market_value", tin)
        # A stated 0 is a stated 0 (the arrival says so); no column, no evidence.
        txs, _ = _parse(csv.replace("TFI,XEI,,,25,,,CAD,300.00,,", "TFI,XEI,,,25,,,CAD,0,,"), _EVENT_TOML)
        self.assertEqual([t for t in txs if t["action"] == "TRANSFER"][0]["book_value"], 0.0)
        txs, _ = _parse(csv.replace("TFI,XEI,,,25,,,CAD,300.00,,", "TFI,XEI,,,25,,,CAD,,,"), _EVENT_TOML)
        self.assertNotIn("book_value", [t for t in txs if t["action"] == "TRANSFER"][0])

    def test_a_transfers_direction_is_the_targets(self):
        with self.assertRaises(ValueError) as cm:
            _parse(_EVENT_CSV.replace("TFO,XEI,,,25,12.00,,CAD,,300.00,", "TFI,XEI,,,-25,,,CAD,,,"), _EVENT_TOML)
        self.assertIn("transfer_out", str(cm.exception))
        txs, _ = _parse(_EVENT_CSV.replace("TFO,XEI,,,25,12.00,,CAD,,300.00,", "TFO,XEI,,,-25,,,CAD,,,"), _EVENT_TOML)
        self.assertEqual([t["quantity"] for t in txs if t["action"] == "TRANSFER"], [-25.0])
        # An amount on a transfer row is left unread and counted in a note.
        txs, out = _parse(_EVENT_CSV.replace("TFO,XEI,,,25,12.00,,CAD,,300.00,", "TFO,XEI,,,25,,300.00,CAD,,,"), _EVENT_TOML)
        tfo = [t for t in txs if t["action"] == "TRANSFER"][0]
        self.assertEqual((tfo["net_amount"], tfo.get("market_value")), (0.0, None))
        self.assertIn("1 transfer row(s) carry an amount that was not read", out)
        self.assertIn("[columns].book_value", out)

    def test_a_transfers_value_may_be_the_amount_column_itself(self):
        # One Amount column: a trade's total on trade rows, the value at
        # transfer on transfer rows, read as the field the mapping names.
        toml = _EVENT_TOML.replace('market_value = "Value"\n', 'market_value = "Amount"\n')
        txs, out = _parse(_EVENT_CSV.replace("TFO,XEI,,,25,12.00,,CAD,,300.00,", "TFO,XEI,,,25,,275.00,CAD,,,"), toml)
        tfo = [t for t in txs if t["action"] == "TRANSFER"][0]
        self.assertEqual((tfo["net_amount"], tfo["market_value"]), (0.0, 275.0))
        self.assertNotIn("not read", out)
        buy = [t for t in txs if t["action"] == "BUYSELL" and t.get("type") != "opening"][0]
        self.assertEqual(abs(buy["net_amount"]), 1005.0)
        toml = _EVENT_TOML.replace('book_value = "Book Value"\n', 'book_value = "Amount"\n')
        txs, _ = _parse(_EVENT_CSV.replace("TFO,XEI,,,25,12.00,,CAD,,300.00,", "TFI,XEI,,,25,,275.00,CAD,,,"), toml)
        self.assertEqual([t for t in txs if t["action"] == "TRANSFER"][0]["book_value"], 275.0)
        # Two evidence fields on one header, or a fee on the amount's, stay refused.
        with self.assertRaises(ValueError) as cm:
            _parse(_EVENT_CSV, toml.replace('market_value = "Value"\n', 'market_value = "Amount"\n'))
        self.assertIn("SAME CSV header", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            _parse(_EVENT_CSV, _EVENT_TOML.replace('currency = "Currency"\n', 'currency = "Currency"\nfee = "Amount"\n'))
        self.assertIn("SAME CSV header", str(cm.exception))

    def test_an_option_opening_takes_the_ticker_maps_contract_size(self):
        from taxjson.lib import markets
        csv = _EVENT_CSV.replace("OPEN,XEI,,,50,,500.00,CAD,,,05/01/2023", "OPEN,ZQQ251219C00010000,,,2,,500.00,USD,,,")
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "ticker.map").write_text("MULT ZQQ 10\n")
            markets.use_ticker_map(Path(td) / "ticker.map")
            try:
                txs, _ = _parse(csv, _EVENT_TOML)
            finally:
                markets.use_ticker_map(None)
        opening = [t for t in txs if t.get("type") == "opening"][0]
        self.assertEqual((opening["price"], opening["multiplier"], opening["net_amount"]), (25.0, 10.0, 500.0))
        txs, _ = _parse(csv, _EVENT_TOML)              # no MULT line: the standard 100, unsaid
        opening = [t for t in txs if t.get("type") == "opening"][0]
        self.assertEqual((opening["price"], opening.get("multiplier")), (2.5, None))

    def test_an_openings_lot_date(self):
        # The mapping's date format, its date part when the trades carry a
        # clock, or ISO; anything else refused.
        txs, _ = _parse(_EVENT_CSV.replace("500.00,CAD,,,05/01/2023", "500.00,CAD,,,2023-05-01"), _EVENT_TOML)
        self.assertEqual([t for t in txs if t.get("type") == "opening"][0]["lot_date"], "2023-05-01")
        clocked = "\n".join(line if i == 0 else line.replace(",", " 09:31,", 1)
                            for i, line in enumerate(_EVENT_CSV.splitlines())) + "\n"
        txs, _ = _parse(clocked, _EVENT_TOML.replace('date = "%m/%d/%Y"', 'date = "%m/%d/%Y %H:%M"'))
        self.assertEqual([t for t in txs if t.get("type") == "opening"][0]["lot_date"], "2023-05-01")
        with self.assertRaises(ValueError) as cm:
            _parse(_EVENT_CSV.replace("500.00,CAD,,,05/01/2023", "500.00,CAD,,,2023.05.01"), _EVENT_TOML)
        self.assertIn("acquisition date", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            _parse(_EVENT_CSV.replace("500.00,CAD,,,05/01/2023", "500.00,CAD,,,05/01/2025"), _EVENT_TOML)
        self.assertIn("after the snapshot day", str(cm.exception))

    def test_the_cells_an_event_may_not_carry(self):
        cases = (("ADJ,XEI,,,0,,-10.00,", "ADJ,XEI,,,100,10.00,-1005.00,", "moves no units"),
                 ("OPEN,XEI,,,50,,500.00,", "OPEN,XEI,,,50,10.00,10.00,", "TOTAL cost"),
                 ("OPEN,XEI,,,50,,500.00,", "OPEN,/ESZ5,,,2,,50000.00,", "futures"),
                 ("ADJ,XEI,,,0,,-10.00,", "ADJ,,,,0,,-10.00,", "blank symbol"))
        for old, new, words in cases:
            with self.assertRaises(ValueError) as cm:
                _parse(_EVENT_CSV.replace(old, new), _EVENT_TOML)
            self.assertIn(words, str(cm.exception), words)
        # A price that agrees with the amount is fine.
        txs, _ = _parse(_EVENT_CSV.replace("OPEN,XEI,,,50,,500.00,", "OPEN,XEI,,,50,10.00,500.00,"), _EVENT_TOML)
        self.assertEqual([t["net_amount"] for t in txs if t.get("type") == "opening"], [500.0])

    def test_a_ratio_cell_is_read_for_the_split_targets_alone(self):
        csv = _EVENT_CSV.replace("TFO,XEI,,,25,", "TFO,XEI,,N/A,25,")
        txs, _ = _parse(csv, _EVENT_TOML)
        self.assertEqual([t["quantity"] for t in txs if t["action"] == "TRANSFER"], [-25.0])

    def test_the_refusals(self):
        cases = (("OPEN,XEI,,,50,,500.00,", "OPEN,XEI,,,-50,,500.00,", "not positive"),
                 ("OPEN,XEI,,,50,,500.00,", "OPEN,XEI,,,50,,,", "total cost"),
                 ("TFO,XEI,,,25,12.00,,", "TFO,XEI,,,0,,,", "non-zero quantity"),
                 ("ROC,XEI,,,0,,25.00,", "ROC,XEI,,,0,,0,", "non-zero amount"))
        for old, new, words in cases:
            with self.assertRaises(ValueError) as cm:
                _parse(_EVENT_CSV.replace(old, new), _EVENT_TOML)
            self.assertIn(words, str(cm.exception), words)

    def test_a_fee_on_an_event_row_is_refused(self):
        toml = _EVENT_TOML.replace('currency = "Currency"\n', 'currency = "Currency"\nfee = "Fee"\n')
        lines = _EVENT_CSV.splitlines()
        lines = [lines[0] + ",Fee"] + [line + "," for line in lines[1:]]
        csv = "\n".join(lines).replace("TFO,XEI,,,25,12.00,,CAD,,300.00,,", "TFO,XEI,,,25,12.00,,CAD,,300.00,,9.99") + "\n"
        with self.assertRaises(ValueError) as cm:
            _parse(csv, toml)
        self.assertIn("a fee is a fee row", str(cm.exception))

    def test_a_target_without_its_column_is_refused_at_load(self):
        # No income target in the mapping: the per-target check is what refuses.
        bad = (_EVENT_TOML.replace('amount = "Amount"\n', '')
               .replace('"DIV" = "dividend"\n', '"DIV" = "skip"\n')
               .replace('"NRT" = "tax"\n', '"NRT" = "skip"\n'))
        with self.assertRaises(ValueError) as cm:
            _parse(_EVENT_CSV, bad)
        self.assertIn("[actions] maps to opening but [columns].amount", str(cm.exception))
        bad = _EVENT_TOML.replace('quantity = "Quantity"\n', '').replace('"BUY" = "buy"\n', '')
        with self.assertRaises(ValueError) as cm:
            _parse(_EVENT_CSV, bad)
        self.assertIn("transfer_", str(cm.exception))


_CLASSIFY_CSV = ("Date,Transaction type,Symbol,New Symbol,Ratio,Quantity,Price,Amount,Currency,Description\n"
                 "01/15/2025,BUY,XEI,,,100,10.00,-1005.00,CAD,\n"
                 "02/01/2025,DRIP XEI,XEI,,,,,-15.00,CAD,REINVESTED 1.5 SHARES @ 10.00\n"
                 "02/10/2025,CORP ACTION,XEI,,,100,,,CAD,SPLIT 2 FOR 1\n"
                 "03/01/2025,DIVIDEND XEI,XEI,,,0,0,18.00,CAD,CASH DIVIDEND\n"
                 "03/05/2025,MISC,XEI,,,0,0,0,CAD,ACCOUNT NOTE\n")
_CLASSIFY_TOML = (_SPLIT_TOML.replace('symbol = "Symbol"\n', 'symbol = "Symbol"\ndescription = "Description"\n')
                  + '\n[[classify]]\nmatch = "^DRIP"\ntarget = "buy"\n'
                  + '[[classify]]\nmatch = "^DIVIDEND"\ntarget = "dividend"\n'
                  + '[[classify]]\nmatch = "SPLIT"\nover = "description"\ntarget = "split"\n'
                  + "\n[extract]\nquantity = '([0-9.]+) SHARES'\nprice = '@ ([0-9.]+)'\n"
                  + "ratio = 'SPLIT ([0-9]+) FOR ([0-9]+)'\n")


class TestGenericClassify(unittest.TestCase):
    """`[[classify]]` rules and `[extract]` patterns (step 5c): an action
    value [actions] does not list takes the first rule found in the cell it
    reads; a field's value comes from the description when its own column
    is unmapped or blank; the description cell is the booked row's."""

    def test_rules_and_patterns(self):
        txs, out = _parse(_CLASSIFY_CSV, _CLASSIFY_TOML)
        by = {t["description"]: t for t in txs}
        drip = by["REINVESTED 1.5 SHARES @ 10.00"]
        self.assertEqual((drip["action"], drip["quantity"], drip["price"], abs(drip["net_amount"])),
                         ("BUYSELL", 1.5, 10.0, 15.0))
        split = by["SPLIT 2 FOR 1"]
        self.assertEqual((split["action"], split["quantity"]), ("SPLIT", 2.0))
        div = by["CASH DIVIDEND"]
        self.assertEqual((div["action"], div["type"], div["net_amount"]), ("DIVIDEND", "dividend", 18.0))
        self.assertEqual(by["BUY"]["quantity"], 100.0)     # a blank cell: the action label
        self.assertNotIn("ACCOUNT NOTE", by)                 # unmapped, moves nothing: skipped quietly
        self.assertNotIn("UNBOOKED", out)

    def test_actions_win_then_the_first_rule_then_a_mapped_cell(self):
        toml = _CLASSIFY_TOML.replace('"JNL" = "skip"\n', '"JNL" = "skip"\n"DRIP XEI" = "skip"\n')
        txs, _ = _parse(_CLASSIFY_CSV, toml)
        self.assertNotIn("REINVESTED 1.5 SHARES @ 10.00", [t["description"] for t in txs])
        toml = _CLASSIFY_TOML.replace('match = "^DRIP"\ntarget = "buy"\n',
                                      'match = "^DRIP"\ntarget = "buy"\n[[classify]]\nmatch = "^DRIP"\ntarget = "sell"\n')
        txs, _ = _parse(_CLASSIFY_CSV, toml)
        drip = [t for t in txs if t["description"].startswith("REINVESTED")][0]
        self.assertEqual(drip["quantity"], 1.5)
        csv = _CLASSIFY_CSV.replace("DRIP XEI,XEI,,,,,-15.00,CAD,REINVESTED 1.5 SHARES @ 10.00",
                                    "DRIP XEI,XEI,,,1.5,,-15.00,CAD,REINVESTED 2 SHARES @ 10.00")
        txs, _ = _parse(csv, _CLASSIFY_TOML)
        drip = [t for t in txs if t["description"].startswith("REINVESTED")][0]
        self.assertEqual((drip["quantity"], drip["price"]), (1.5, 10.0))

    def test_a_ratio_of_two_groups(self):
        txs, _ = _parse(_CLASSIFY_CSV.replace("SPLIT 2 FOR 1", "SPLIT 1 FOR 10"), _CLASSIFY_TOML)
        self.assertEqual([t["quantity"] for t in txs if t["action"] == "SPLIT"], [0.1])
        with self.assertRaises(ValueError) as cm:
            _parse(_CLASSIFY_CSV.replace("SPLIT 2 FOR 1", "SPLIT 2 FOR 0"), _CLASSIFY_TOML)
        self.assertIn("[extract].ratio pattern", str(cm.exception))

    def test_a_pulled_value_that_is_not_a_number_is_refused_where_a_booked_row_reads_it(self):
        toml = _CLASSIFY_TOML.replace("quantity = '([0-9.]+) SHARES'", "quantity = '(\\S+) SHARES'")
        with self.assertRaises(ValueError) as cm:
            _parse(_CLASSIFY_CSV.replace("REINVESTED 1.5 SHARES", "REINVESTED 1.5.5 SHARES"), toml)
        self.assertIn("unparseable quantity value '1.5.5'", str(cm.exception))
        self.assertIn("[extract].quantity pattern", str(cm.exception))
        # A skipped row's text never refuses the file.
        txs, _ = _parse(_CLASSIFY_CSV.replace("MISC,XEI,,,0,0,0,CAD,ACCOUNT NOTE", "JNL,XEI,,,,,,CAD,MOVED ALL SHARES"), toml)
        self.assertEqual(len([t for t in txs if t["description"].startswith("REINVESTED")]), 1)

    def test_an_optional_group_and_the_default_action(self):
        toml = _CLASSIFY_TOML.replace("ratio = 'SPLIT ([0-9]+) FOR ([0-9]+)'", "ratio = 'SPLIT ([0-9]+)(?: FOR ([0-9]+))?'")
        txs, _ = _parse(_CLASSIFY_CSV.replace("SPLIT 2 FOR 1", "SPLIT 3"), toml)
        self.assertEqual([t["quantity"] for t in txs if t["action"] == "SPLIT"], [3.0])
        txs, _ = _parse(_CLASSIFY_CSV, toml)
        self.assertEqual([t["quantity"] for t in txs if t["action"] == "SPLIT"], [2.0])
        # A rule over the action reads [defaults].action when no column is mapped.
        toml = (_CLASSIFY_TOML.replace('action = "Transaction type"\n', '')
                .replace("[formats]", '[defaults]\naction = "DRIP XEI"\n\n[formats]'))
        csv = "\n".join(line for line in _CLASSIFY_CSV.splitlines() if line.startswith(("Date", "02/01"))) + "\n"
        txs, _ = _parse(csv, toml)
        self.assertEqual([(t["action"], t["quantity"]) for t in txs], [("BUYSELL", 1.5)])

    def test_a_pattern_fills_the_fields_the_rows_target_books(self):
        # The DRIP quantity pattern leaves a return of capital and a
        # dividend alone, though their descriptions name shares.
        toml = _CLASSIFY_TOML.replace('"JNL" = "skip"\n', '"JNL" = "skip"\n"ROC" = "roc"\n')
        csv = _CLASSIFY_CSV.replace("MISC,XEI,,,0,0,0,CAD,ACCOUNT NOTE", "ROC,XEI,,,,,25.00,CAD,RETURN OF CAPITAL ON 100 SHARES") \
                           .replace("CASH DIVIDEND", "CASH DIVIDEND ON 100 SHARES")
        txs, _ = _parse(csv, toml)
        by = {t["description"]: t for t in txs}
        self.assertEqual((by["RETURN OF CAPITAL ON 100 SHARES"]["type"], by["RETURN OF CAPITAL ON 100 SHARES"]["quantity"]), ("roc", 0.0))
        self.assertEqual(by["CASH DIVIDEND ON 100 SHARES"]["quantity"], 0.0)
        # A row nothing maps takes every pattern: it moves shares, so UNBOOKED.
        _, out = _parse(csv.replace("ROC,XEI,,,,,25.00,CAD,RETURN OF CAPITAL ON 100 SHARES",
                                    "XFER,XEI,,,,,,CAD,MOVED 5 SHARES"), toml)
        self.assertIn("UNBOOKED", out)

    def test_a_symbol_pattern_may_stand_for_the_symbol_column(self):
        toml = (_CLASSIFY_TOML.replace('symbol = "Symbol"\n', '')
                .replace("[extract]\n", "[extract]\nsymbol = 'FOR ([A-Z]+)'\n"))
        csv = _CLASSIFY_CSV.replace("CASH DIVIDEND", "CASH DIVIDEND FOR XEI")
        lines = [line for line in csv.splitlines() if line.startswith(("Date", "03/01"))]
        txs, _ = _parse("\n".join(lines) + "\n", toml)
        self.assertEqual([(t["symbol"], t["net_amount"]) for t in txs], [("XEI.TO", 18.0)])
        with self.assertRaises(ValueError) as cm:
            _parse(csv, _CLASSIFY_TOML.replace('symbol = "Symbol"\n', ''))
        self.assertIn("[extract].symbol pattern", str(cm.exception))

    def test_a_refusal_names_the_values_source(self):
        csv = _CLASSIFY_CSV.replace("DRIP XEI,XEI,,,,,-15.00,CAD,REINVESTED 1.5 SHARES @ 10.00",
                                    "DRIP XEI,XEI,,,1.5.5,,-15.00,CAD,REINVESTED 1.5 SHARES @ 10.00")
        with self.assertRaises(ValueError) as cm:
            _parse(csv, _CLASSIFY_TOML)
        self.assertIn("[columns].quantity mapping", str(cm.exception))

    def test_the_description_reaches_the_runs_checks_as_a_brokers_text(self):
        # The run's description heuristics (a dividend's "ON n SHS" states
        # a holding, positions_check) read the booked description as they
        # read a broker parser's text.
        from taxjson.lib.brokerages.base import _DIV_QTY_ON_SHS_RE
        txs, _ = _parse(_CLASSIFY_CSV.replace("CASH DIVIDEND", "CASH DIVIDEND ON 100 SHS"), _CLASSIFY_TOML)
        div = [t for t in txs if t["action"] == "DIVIDEND"][0]
        self.assertEqual(_DIV_QTY_ON_SHS_RE.search(div["description"]).group(1), "100")

    def test_the_action_and_the_description_may_be_one_column(self):
        toml = _CLASSIFY_TOML.replace('action = "Transaction type"\n', 'action = "Description"\n')
        csv = _CLASSIFY_CSV.replace("DRIP XEI,XEI,,,,,-15.00,CAD,REINVESTED 1.5 SHARES @ 10.00",
                                    "x,XEI,,,,,-15.00,CAD,DRIP 1.5 SHARES @ 10.00")
        txs, _ = _parse(csv, toml.replace('"BUY" = "buy"\n', '"BUY" = "buy"\n"CASH DIVIDEND" = "dividend"\n')
                        .replace('match = "^DIVIDEND"', 'match = "^NEVER"').replace('match = "SPLIT"', 'match = "^SPLIT"'))
        drip = [t for t in txs if t["description"].startswith("DRIP")][0]
        self.assertEqual((drip["action"], drip["quantity"]), ("BUYSELL", 1.5))

    def test_an_unmapped_row_that_moves_shares_by_its_description_is_unbooked(self):
        csv = _CLASSIFY_CSV.replace("MISC,XEI,,,0,0,0,CAD,ACCOUNT NOTE", "XFER,XEI,,,,,,CAD,MOVED 5 SHARES")
        _, out = _parse(csv, _CLASSIFY_TOML)
        self.assertIn("UNBOOKED", out)
        self.assertIn("no [[classify]] rule", out)

    def test_the_refusals_at_load(self):
        base = _CLASSIFY_TOML
        cases = (
            (base.replace('match = "^DRIP"', 'match = "("'), "not a valid regular expression"),
            (base.replace('target = "buy"', 'target = "purchase"'), "unknown target"),
            (base.replace('match = "^DRIP"\n', 'pattern = "^DRIP"\n'), "Did you mean 'match'"),
            (base.replace('match = "^DRIP"\n', ''), "must be a quoted regular expression"),
            (base.replace('target = "buy"\n', '', 1), "needs a target"),
            (base.replace('ratio = "Ratio"\n', '').replace("ratio = 'SPLIT ([0-9]+) FOR ([0-9]+)'\n", '')
             .replace('"SPLIT" = "split"\n', ''), "a [[classify]] rule maps to split but [columns].ratio is not mapped (nor an [extract].ratio pattern)"),
            (base.replace('over = "description"', 'over = "memo"'), 'reads the row\'s "action" cell'),
            (base.replace("quantity = '([0-9.]+) SHARES'", "quantity = 'SHARES'"), "capturing group"),
            (base.replace("quantity = '([0-9.]+) SHARES'", "qty = '([0-9.]+) SHARES'"), "Did you mean 'quantity'"),
            (base.replace('description = "Description"\n', ''), "[columns].description is not mapped"),
            (_SPLIT_TOML + '\n[extract]\nquantity = \'([0-9.]+) SHARES\'\n', "[columns].description is not mapped"),
            (base.replace("[extract]", "[[extract]]"), "must be a [extract] table"),
            (_SPLIT_TOML + '\n[classify]\nmatch = "^DRIP"\ntarget = "buy"\n', "written as [[classify]] tables"),
        )
        for toml, words in cases:
            with self.assertRaises(ValueError) as cm:
                _parse(_CLASSIFY_CSV, toml)
            self.assertIn(words, str(cm.exception), words)


class TestGenericEndToEnd(unittest.TestCase):
    def test_full_run_and_mapping_edit_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            csv = root / "inputs" / "margin" / "generic_mybk.csv"
            csv.write_text(
                "Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
                "01/15/2025,BUY,XEI,100,10.00,-1000.00,CAD\n"
                "06/20/2025,SELL,XEI,100,15.00,1500.00,CAD\n")
            (root / "inputs" / "margin" / "generic.toml").write_text(_TOML)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "run", "--no-input"],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            gains = json.loads(
                (root / "work" / "margin_gains.json").read_text())
            self.assertAlmostEqual(gains["summary"]["total_gain"],
                                   500.0, places=2)
            # Editing the MAPPING rebuilds, like a
            # CSV edit would (BUY becomes skip → position vanishes).
            import time
            time.sleep(0.05)
            (root / "inputs" / "margin" / "generic.toml").write_text(
                _TOML.replace('"BUY" = "buy"', '"BUY" = "skip"'))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "run", "--no-input"],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            base = (root / "work" / "margin_base.json").read_text()
        self.assertEqual(base.count('"BUYSELL"'), 1,
                         "the mapping edit did not reach the books")




class TestReleaseAuditRegressions(unittest.TestCase):
    """Fixes from the pre-release adversarial pass."""

    def test_currency_prefixed_numerics_parse(self):
        # 'C$10.00' silently became price=0 (a $0-basis buy) before —
        # common currency prefixes now strip and parse.
        csv = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
               "01/15/2025,BUY,XEI,100,C$10.00,,CAD\n")
        txs, _ = _parse(csv, _TOML)
        self.assertAlmostEqual(txs[0]["price"], 10.0, places=2)
        self.assertAlmostEqual(txs[0]["net_amount"], 1000.0, places=2)

    def test_true_garbage_numeric_refused_loudly(self):
        csv = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
               "01/15/2025,BUY,XEI,100,ten dollars,-1005.00,CAD\n")
        with self.assertRaises(ValueError) as cm:
            _parse(csv, _TOML)
        self.assertIn("unparseable price", str(cm.exception))

    def test_duplicate_mapped_header_refused(self):
        csv = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Amount,Currency\n"
               "01/15/2025,BUY,XEI,100,10.00,-1005.00,-999.00,CAD\n")
        with self.assertRaises(ValueError) as cm:
            _parse(csv, _TOML)
        self.assertIn("MORE THAN ONCE", str(cm.exception))

    def test_sidecar_deletion_invalidates_fast_cache(self):
        # FUZZ #J class: deleting a sidecar switches the file to the
        # shared mapping — the manifest must notice.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            csv = root / "inputs" / "margin" / "generic_a.csv"
            csv.write_text(
                "Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
                "01/15/2025,BUY,AAA,100,10.00,-1000.00,CAD\n")
            sidecar = csv.with_name(csv.name + ".toml")
            sidecar.write_text(_TOML)                      # BUY -> buy
            (root / "inputs" / "margin" / "generic.toml").write_text(
                _TOML.replace('"BUY" = "buy"', '"BUY" = "skip"'))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "run", "--no-input"],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('"BUYSELL"',
                          (root / "work" / "margin_base.json").read_text())
            sidecar.unlink()          # shared mapping (skip) now governs
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "run", "--no-input"],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            base = (root / "work" / "margin_base.json").read_text()
        self.assertNotIn(
            '"BUYSELL"', base,
            "sidecar deletion went unnoticed — the "
            "deleted mapping's rows survived")


class TestParenthesizedNegatives(unittest.TestCase):
    """2026-09 audit #3: num() stripped '(x)' as a magnitude, losing
    the accounting-negative sign on the sign-preserving branches — a
    parenthesized DIVIDEND reversal booked as MORE income and a
    parenthesized TAX withholding flipped into a refund."""

    def test_paren_dividend_reversal_stays_negative(self):
        csv = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
               "03/01/2025,DIV,XEI,0,0,18.00,CAD\n"
               "03/05/2025,DIV,XEI,0,0,(18.00),CAD\n")
        txs, _ = _parse(csv, _TOML)
        divs = [t for t in txs if t["action"] == "DIVIDEND"]
        self.assertEqual(sorted(t["net_amount"] for t in divs),
                         [-18.0, 18.0])
        self.assertAlmostEqual(sum(t["net_amount"] for t in divs), 0.0,
                               places=2,
                               msg="'(18.00)' parsed as +18 — a fully "
                                   "reversed dividend reported income")

    def test_paren_withholding_counts_as_tax_withheld(self):
        # tax_sign default 'cash': negative cash = withheld → +2.70.
        csv = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
               "03/05/2025,NRT,XEI,0,0,(2.70),CAD\n")
        txs, _ = _parse(csv, _TOML)
        self.assertEqual(txs[0]["action"], "TAX")
        self.assertAlmostEqual(txs[0]["net_amount"], 2.70, places=2,
                               msg="'(2.70)' lost its sign and booked "
                                   "a REFUND instead of withholding")

    def test_paren_currency_prefix_combination(self):
        csv = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
               "03/01/2025,DIV,XEI,0,0,(C$18.00),CAD\n")
        txs, _ = _parse(csv, _TOML)
        self.assertAlmostEqual(txs[0]["net_amount"], -18.0, places=2)

    def test_trade_branches_unaffected_by_paren_amounts(self):
        # Trades re-sign via signed_quantity/abs(): a parenthesized
        # (broker-negative) buy amount still yields the same positive
        # fee-inclusive net and signed quantity as the bare-minus form.
        csv = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
               "01/15/2025,BUY,XEI,100,10.00,(1005.00),CAD\n"
               "06/20/2025,SELL,XEI,100,15.00,1495.00,CAD\n")
        txs, _ = _parse(csv, _TOML)
        buy = next(t for t in txs if t["quantity"] > 0)
        sell = next(t for t in txs if t["quantity"] < 0)
        self.assertAlmostEqual(buy["net_amount"], 1005.0, places=2)
        self.assertAlmostEqual(buy["quantity"], 100.0, places=2)
        self.assertAlmostEqual(sell["net_amount"], 1495.0, places=2)


class TestGenericRowOrder(unittest.TestCase):
    """The rows are read lazily (step 5h (3)): each row's shape check comes
    in file order with the classification and build of the rows before
    it, as the one loop had it, so the first bad row names the refusal."""
    _HEAD = "Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
    _BAD_DATE = "03-05-2025,BUY,XEI,100,10.00,-1005.00,CAD\n"
    _SHORT = "06/20/2025,SELL,XEI,100\n"

    def test_the_first_bad_row_is_the_refusal(self):
        with self.assertRaises(ValueError) as cm:
            _parse(self._HEAD + self._BAD_DATE + self._SHORT, _TOML)
        self.assertIn("unparseable date", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            _parse(self._HEAD + self._SHORT + self._BAD_DATE, _TOML)
        self.assertIn("fewer cells than the header", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
