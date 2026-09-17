"""Generic column-mapped CSV importer (`generic_*.csv` + TOML mapping)."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_CSV = ("Date,Transaction type,Symbol,Quantity,Price,Amount,Currency\n"
        "01/15/2025,BUY,XEI,100,10.00,-1005.00,CAD\n"
        "06/20/2025,SELL,XEI,100,15.00,1495.00,CAD\n"
        "03/01/2025,DIV,XEI,0,0,18.00,CAD\n"
        "03/01/2025,DIV,XEI,0,0,-18.00,CAD\n"
        "03-05-2025,NRT,XEI,0,0,-2.70,CAD\n"
        "04/01/2025,JNL,XEI,0,0,0,CAD\n"
        "05/01/2025,MYSTERY,XEI,0,0,1.00,CAD\n")

_TOML = """\
[columns]
date = "Date"
action = "Transaction type"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Amount"
currency = "Currency"

[formats]
date = "%m/%d/%Y"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"DIV" = "dividend"
"NRT" = "tax"
"JNL" = "skip"
"""


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
            # Editing the MAPPING must rebuild under --fast, like a
            # CSV edit would (BUY becomes skip → position vanishes).
            import time
            time.sleep(0.05)
            (root / "inputs" / "margin" / "generic.toml").write_text(
                _TOML.replace('"BUY" = "buy"', '"BUY" = "skip"'))
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(root), "run", "--fast", "--no-input"],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            base = (root / "work" / "margin_base.json").read_text()
        self.assertEqual(base.count('"BUYSELL"'), 1,
                         "the mapping edit did not invalidate the "
                         "--fast cache")




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
                 str(root), "run", "--fast", "--no-input"],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            base = (root / "work" / "margin_base.json").read_text()
        self.assertNotIn(
            '"BUYSELL"', base,
            "sidecar deletion went unnoticed under --fast — the "
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


if __name__ == "__main__":
    unittest.main()
