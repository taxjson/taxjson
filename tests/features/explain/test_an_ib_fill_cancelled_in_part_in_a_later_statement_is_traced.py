"""Backlog TJ-5270 with IB's statements: a fill in one statement and IB's
cancellation of part of it (a `Ca` row) in the next. `tjs audit` names the
row the run netted from the two (a buy reduced, a sale reduced) by what
made it and by each statement it came from, checks its amount against the
two netted, and fails a book that holds another; in Canada and in the US.

On v2.1.0 the reduced buy read "a row of ib_a.csv that no parsed file holds
now — stale work/ files: re-run `tjs run`" under "With no parsed row", on
fresh books, its amount never compared, and the reduced sale was "SOURCE
(none found)". A broker export's row is named by its file: no parser
records the line a row was read from (only a `.tt` file's converter does).

Synthetic account (U1234567) and tickers (QZCX, QZCY), the test HOME's
made-up rates (USD/CAD 1.35), amounts under 1,000."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _paths
from _support.ib_statement_rows import HEAD, TRADES_H, _period, _trade
from taxjson.lib.dispatch import python_module_argv

ACCT = "U1234567"
_HEAD = "a fill less the part the broker cancelled (type=trade_cancel), booked by the run"
_BOTH = ": the fill inputs/margin/ib_a.csv, the cancellation inputs/margin/ib_b.csv"
STALE = "no parsed file holds now"


def _tr(sym, when, qty, price, proceeds, comm, code):
    return _trade(sym, when, qty, price, proceeds, comm, code, acct=ACCT)


def _statement(period, *rows):
    return (HEAD + _period(*period) + "Account Information,Header,Field Name,Field Value\n"
            f"Account Information,Data,Account,{ACCT}\n" + TRADES_H + "".join(rows))


# The first statement: QZCY bought and sold (37 of it), QZCX bought (23).
FIRST = _statement(("January 1, 2025", "October 15, 2025"),
                   _tr("QZCY", "2025-10-01, 10:00:00", 37, 11.43, -422.91, -1.07, "O"),
                   _tr("QZCX", "2025-10-06, 10:00:00", 23, 21.47, -493.81, -1.04, "O"),
                   _tr("QZCY", "2025-10-08, 10:00:00", -37, 13.29, 491.73, -1.13, "C"))
# The next: IB cancels 6 of the QZCX buy and 9 of the QZCY sale (a part of
# each commission back), then the rest of each is sold.
CANCELS = (_tr("QZCX", "2025-10-06, 10:00:00", -6, 21.47, 128.82, 0.28, "Ca"),
           _tr("QZCY", "2025-10-08, 10:00:00", 9, 13.29, -119.61, 0.27, "Ca"))
LATER = (_tr("QZCX", "2025-11-03, 10:00:00", -17, 26.83, 456.11, -1.02, "C"),
         _tr("QZCY", "2025-11-10, 10:00:00", -9, 14.17, 127.53, -1.09, "C"))
PERIOD_2 = ("October 16, 2025", "December 31, 2025")


def _tjs(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`tjs -C root args...` from this tree's sources (width 0)."""
    env = dict(os.environ, PYTHONPATH=str(_paths.SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run")
                          + ["-C", str(root), *args],
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(tmp: Path, name: str, country: str, second: str) -> Path:
    root = tmp / name
    (root / "inputs" / "margin").mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        f'country = "{country}"\nbase_currency = "{base}"\nsource_currencies = ["USD"]\n'
        '\n[accounts.margin]\ntype = "taxable"\n', encoding="utf-8")
    (root / "inputs" / "margin" / "ib_a.csv").write_text(FIRST, encoding="utf-8")
    (root / "inputs" / "margin" / "ib_b.csv").write_text(second, encoding="utf-8")
    r = _tjs(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return root


class _IbStatementsCases:
    """The two statements' audit, per country (COUNTRY; MISMATCH: what the
    audit says of the reduced buy's book row moved by 1.00)."""

    COUNTRY = ""
    MISMATCH = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.root = _project(tmp, "p", cls.COUNTRY,
                            _statement(PERIOD_2, *CANCELS, *LATER))
        cls.text = _tjs(cls.root, "audit", "--details")
        cls.json = _tjs(cls.root, "audit", "--json", "--no-trace")
        # The control: no cancellation, the QZCX buy sold in part.
        cls.plain = _project(tmp, "plain", cls.COUNTRY, _statement(PERIOD_2, LATER[0]))
        cls.plain_text = _tjs(cls.plain, "audit", "--details")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_reduced_buy_is_named_with_both_statements(self):
        r = self.text
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(STALE, r.stdout)
        self.assertNotIn("With no parsed row", r.stdout)
        self.assertIn("  pool trades              2 tied, 0 mismatched, 0 with no "
                      "parsed row  ✓", r.stdout)
        self.assertIn("  Netted from a parsed fill and a broker cancellation (checked "
                      "against the two):\n    2025-10-06 BUY 17 QZCX.US — " + _HEAD
                      + _BOTH + "  ✓", r.stdout)

    def test_the_json_lists_the_reduced_buy_tied(self):
        r = self.json
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertFalse(doc["failed"])
        pool = doc["pool_trades"]
        self.assertEqual((pool["checked"], pool["tied"], pool["untraced"]), (2, 2, 0))
        self.assertEqual(pool["untraced_rows"], [])
        (red,) = pool["reduced_rows"]
        self.assertEqual((red["symbol"], red["date"], red["quantity"], red["ties"]),
                         ("QZCX.US", "2025-10-06", 17.0, True))
        self.assertEqual(red["origin"], _HEAD + _BOTH)
        self.assertEqual([p["role"] for p in red["parts"]], ["fill", "cancellation"])

    def test_the_reduced_sale_is_traced_to_both_statements(self):
        r = self.text
        self.assertNotIn("none found", r.stdout)
        self.assertNotIn("stale work/ artifacts", r.stdout)
        self.assertIn("  SOURCE       " + _HEAD + "\n"
                      "               the fill: inputs/margin/ib_a.csv\n"
                      "               2025-10-08 SELL 37 QZCY.US @ 13.29 — net 490.60 USD, "
                      "fee 1.13\n"
                      "               the cancellation: inputs/margin/ib_b.csv\n"
                      "               2025-10-08 BUY 9 QZCY.US @ 13.29 — net 119.34 USD, "
                      "fee -0.27\n",
                      r.stdout)
        self.assertIn("  source rows              3/3 traced to a parsed broker row  ✓",
                      r.stdout)
        (ev,) = [e for e in json.loads(self.json.stdout)["events"]
                 if e["symbol"] == "QZCY.US" and e["date"] == "2025-10-08"]
        self.assertEqual(ev["origin"], _HEAD + _BOTH)
        self.assertEqual([(s["role"], s["row"]["source"]) for s in ev["sources"]],
                         [("fill", "ib_a.csv"), ("cancellation", "ib_b.csv")])
        self.assertEqual(ev["warnings"], [])

    def test_a_reduced_buy_the_book_holds_otherwise_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            shutil.copytree(self.root, root)
            p = root / "work" / "margin_base.json"
            doc = json.loads(p.read_text(encoding="utf-8"))
            (row,) = [t for t in doc["transactions"]
                      if t.get("symbol") == "QZCX.US" and t.get("quantity") == 17.0]
            row["net_amount"] = row["net_amount"] + 1.0
            p.write_text(json.dumps(doc), encoding="utf-8")
            r = _tjs(root, "audit", "--details", "--no-trace")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("  pool trades              1 tied, 1 mismatched, 0 with no "
                      "parsed row  ✗", r.stdout)
        self.assertIn(self.MISMATCH, r.stdout)

    def test_control_statements_with_no_cancellation_audit_as_before(self):
        r = self.plain_text
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("  pool trades              2 tied, 0 mismatched, 0 with no "
                      "parsed row  ✓", r.stdout)
        self.assertIn("  source rows              2/2 traced to a parsed broker row  ✓",
                      r.stdout)
        self.assertNotIn("Netted from", r.stdout)
        self.assertNotIn(STALE, r.stdout)


class TheStatementsInCanada(_IbStatementsCases, unittest.TestCase):
    COUNTRY = "canada"
    # The netted USD (493.81 + 1.04 less 128.82 and 0.28) at the day's rate.
    MISMATCH = ("FX cross-check FAILED: 365.75 USD x 1.35 = 493.76 but the base "
                "book row carries 494.76 CAD")


class TheStatementsInTheUs(_IbStatementsCases, unittest.TestCase):
    COUNTRY = "usa"
    MISMATCH = ("AMOUNT cross-check FAILED: the netting of the parsed fill and its "
                "broker cancellation carries 365.75 USD but the base book row "
                "carries 366.75 USD")


if __name__ == "__main__":
    unittest.main()
