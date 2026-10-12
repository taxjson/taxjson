"""Broker positions reports (lib/positions_reports): one common row per
position from IB's Activity Statement Open Positions section, RBC's
Holdings Export and a [[holding]] TOML — symbols spelled as each
broker's trade parser spells them, account ids masked, the cost and its
KIND (average cost vs lot basis), market value never read as a cost.
Detection by content: a positions-only file is never parsed as trades
(and `taxjson run` skips one dropped into inputs/), and no trade export
is ever read as positions. All data here is synthetic."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EX = REPO_ROOT / "examples"
FIX = REPO_ROOT / "tests" / "fixtures"

from taxjson.lib import positions_reports as P  # noqa: E402
from taxjson.lib.brokerages import detect as D  # noqa: E402
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

_IB_HEAD = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers Canada Inc.\n"
    "Statement,Data,Title,Activity Statement\n"
    'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U5550001\n"  # pii-ok
)
_OP_H = ("Open Positions,Header,DataDiscriminator,Asset Category,Currency,"
         "Symbol,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,"
         "Unrealized P/L,Code\n")
_IB_POS = (
    _OP_H
    + "Open Positions,Data,Summary,Stocks,CAD,ZZA,100,1,10.5,1050,12,"
      "1200,150,\n"
    + "Open Positions,Data,Summary,Stocks,USD,ZZB,-20,1,30,-600,25,"
      "-500,100,\n"
    + "Open Positions,Total,,Stocks,CAD,,,,,1050,,1200,150,\n"
    + _OP_H
    + "Open Positions,Data,Summary,Equity and Index Options,USD,"
      '"ZZB 16JAN26 30 C",2,100,1.5,300,1,200,-100,\n'
    + "Open Positions,Data,Lot,Stocks,CAD,ZZA,100,1,10.5,1050,12,1200,"
      "150,\n"
    + "Open Positions,Total,,Equity and Index Options,USD,,,,,300,,200,"
      "-100,\n"
)
_IB_STATEMENT = _IB_HEAD + _IB_POS + (
    "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
    "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
    "Realized P/L,MTM P/L,Code\n"
    'Trades,Data,Order,Stocks,CAD,ZZA,"2025-02-12, 09:35:14",100,10.50,'
    "10.50,-1050.00,0,1050.00,0,0,O\n")
_IB_CONSOLIDATED = _IB_STATEMENT.replace(
    "Account Information,Data,Account,U5550001\n",  # pii-ok
    "Account Information,Data,Account,U5550001\n"  # pii-ok
    'Account Information,Data,Accounts Included,"U5550001, U5550002"\n'  # pii-ok
)
_RBC_HOLD = (
    "Holdings Export as of Jan 5, 2026 at 9:00:28 am ET\n\n"
    '"Account","Product","Symbol","Name","Quantity","Currency",'
    '"Last Price","Total Book Cost","Total Market Value"\n'
    '"55500001","Equities","ZZR","ZZR CORP","10","CAD","6.00",'  # pii-ok
    '"$50.00","60.00"\n'
    '"55500001","Equities","ZZU","ZZU INC","5","USD","20.00",'  # pii-ok
    '"80.00","100.00"\n'
    '"55500001","Cash","","CASH","","CAD","","",""\n'  # pii-ok
)
_RBC_HOLD_CAD_COST = _RBC_HOLD.replace('"Total Book Cost"',
                                       '"Book Value (CAD)"')
_RBC_HOLD_NO_COST = (
    "Holdings Export as of Jun 20, 2025\n"
    '"Symbol","Quantity","Currency","Market Value"\n'
    '"ZZR","10","CAD","100.00"\n')
_TOML = (
    '[meta]\naccount = "U5550001"\n'  # pii-ok
    'generated_at = 2025-12-31T21:00:00Z\n\n'
    '[[holding]]\nsymbol = "ZZA.TO"\nquantity = 100\ncurrency = "CAD"\n'
    'total_cost = 1050.0\nasset_type = "equity"\n\n'
    '[[holding]]\nsymbol = "ZZQ.US"\nquantity = 4\ncurrency = "USD"\n'
    'average_entry_price = 12.5\nmarket_value = 99.0\n\n'
    '[[holding]]\nsymbol = "ZZL.US"\nquantity = 3\ncurrency = "USD"\n'
    'total_cost = 30.0\nacquired = "2019-03-04"\n\n'
    '[[holding]]\nsymbol = "ZZL.US"\nquantity = 2\ncurrency = "USD"\n'
    'total_cost = 40.0\nacquired = 2021-06-01\n\n'
    '[[holding]]\nsymbol = "CAD"\nquantity = 500\nasset_type = "cash"\n')


def _write(td, name, text, encoding="utf-8"):
    p = Path(td) / name
    p.write_bytes(text.encode(encoding))
    return p


class TestIbOpenPositions(unittest.TestCase):
    def test_rows_symbols_cost_kind_and_market_value(self):
        with tempfile.TemporaryDirectory() as td:
            rep = P.read_positions(_write(td, "U5550001.csv",  # pii-ok
                                          _IB_STATEMENT))
        self.assertEqual(rep.broker, "ib")
        self.assertEqual(rep.as_of, "2025-12-31")
        self.assertEqual(rep.accounts, ["U5***"])
        by = {r.symbol: r for r in rep.rows}
        self.assertEqual(sorted(by), ["ZZA.TO", "ZZB.US",
                                      "ZZB260116C00030000.US"])
        a = by["ZZA.TO"]
        self.assertEqual((a.quantity, a.cost, a.cost_currency, a.cost_kind,
                          a.market_value, a.asset_type, a.account),
                         (100.0, 1050.0, "CAD", P.COST_LOTS, 1200.0,
                          "stock", "U5***"))
        self.assertEqual(a.source, "U5***.csv")
        b = by["ZZB.US"]
        self.assertEqual((b.quantity, b.cost, b.currency),
                         (-20.0, -600.0, "USD"))
        o = by["ZZB260116C00030000.US"]
        self.assertEqual((o.asset_type, o.multiplier, o.quantity, o.cost),
                         ("option", 100.0, 2.0, 300.0))
        self.assertTrue(any("Lot row" in n for n in rep.notes))

    def test_consolidated_statement_says_combined(self):
        with tempfile.TemporaryDirectory() as td:
            rep = P.read_positions(_write(td, "s.csv", _IB_CONSOLIDATED))
        self.assertEqual(rep.accounts, ["U5***"])
        self.assertTrue(any("consolidated" in n and "combined" in n
                            for n in rep.notes))
        self.assertNotIn("5550002", repr(rep))  # pii-ok

    def test_statement_without_open_positions_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "s.csv", (EX / "ib_demo.csv").read_text())
            self.assertIsNone(P.detect_positions(p))
            with self.assertRaises(P.PositionsReportError) as cm:
                P.read_positions(p)
        self.assertIn("no Open Positions section", str(cm.exception))

    def test_bad_quantity_is_refused_naming_the_row(self):
        bad = _IB_STATEMENT.replace("ZZA,100,1,", "ZZA,1x0,1,", 1)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(P.PositionsReportError) as cm:
                P.read_positions(_write(td, "s.csv", bad))
        self.assertIn("Quantity", str(cm.exception))


class TestRbcHoldings(unittest.TestCase):
    def test_book_cost_is_average_cost_market_value_kept_apart(self):
        with tempfile.TemporaryDirectory() as td:
            rep = P.read_positions(_write(td, "h.csv", _RBC_HOLD))
        self.assertEqual((rep.broker, rep.as_of), ("rbc_direct",
                                                   "2026-01-05"))
        by = {r.symbol: r for r in rep.rows}
        self.assertEqual(sorted(by), ["ZZR.TO", "ZZU.US"])
        r = by["ZZR.TO"]
        self.assertEqual((r.cost, r.cost_kind, r.market_value, r.account),
                         (50.0, P.COST_AVERAGE, 60.0, "55***"))
        self.assertEqual((by["ZZU.US"].cost, by["ZZU.US"].cost_currency),
                         (80.0, "USD"))
        self.assertEqual(rep.accounts, ["55***"])

    def test_a_cad_labelled_book_value_is_cad(self):
        # A broker's CAD book value for a USD position is already
        # converted: the label decides the cost's currency.
        with tempfile.TemporaryDirectory() as td:
            rep = P.read_positions(_write(td, "h.csv", _RBC_HOLD_CAD_COST))
        u = {r.symbol: r for r in rep.rows}["ZZU.US"]
        self.assertEqual((u.currency, u.cost_currency), ("USD", "CAD"))

    def test_market_value_only_is_no_cost(self):
        with tempfile.TemporaryDirectory() as td:
            rep = P.read_positions(_write(td, "h.csv", _RBC_HOLD_NO_COST))
        (r,) = rep.rows
        self.assertIsNone(r.cost)
        self.assertEqual((r.cost_kind, r.market_value), (P.COST_NONE,
                                                         100.0))
        self.assertTrue(any("no book-cost column" in n for n in rep.notes))

    def test_missing_quantity_label_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "h.csv", "Holdings Export as of Jan 5, 2026\n"
                                    "Symbol,Units\nZZR,10\n")
            with self.assertRaises(P.PositionsReportError):
                P.read_positions(p)


class TestHoldingsToml(unittest.TestCase):
    def test_costs_lots_and_as_of(self):
        with tempfile.TemporaryDirectory() as td:
            rep = P.read_positions(_write(td, "h.toml", _TOML))
        self.assertEqual((rep.broker, rep.as_of), ("toml", "2025-12-31"))
        self.assertEqual(rep.accounts, ["U5***"])
        rows = rep.rows
        self.assertEqual([r.symbol for r in rows],
                         ["ZZA.TO", "ZZQ.US", "ZZL.US", "ZZL.US"])
        self.assertEqual((rows[0].cost, rows[0].cost_kind),
                         (1050.0, P.COST_AVERAGE))
        self.assertEqual((rows[1].cost, rows[1].market_value),
                         (50.0, 99.0))
        self.assertEqual([(r.lot_date, r.cost_kind) for r in rows[2:]],
                         [("2019-03-04", P.COST_LOTS),
                          ("2021-06-01", P.COST_LOTS)])

    def test_bool_blank_and_bad_dates_refused(self):
        for bad in ('quantity = true', 'quantity = ""',
                    'quantity = 1\nacquired = "March 2019"',
                    'quantity = 1\ncost_kind = "fifo"'):
            with tempfile.TemporaryDirectory() as td:
                p = _write(td, "h.toml",
                           f'[[holding]]\nsymbol = "ZZA.TO"\n{bad}\n')
                with self.subTest(bad), \
                        self.assertRaises(P.PositionsReportError):
                    P.read_positions(p)


# ------------------------------------------------------------ detection

def _trade_samples():
    """(broker, label, text) for every trade-export demo and fixture."""
    out = []
    for broker, demo in (("ib", "ib_demo.csv"),
                         ("questrade", "questrade_demo.csv"),
                         ("webull", "webull_demo.csv"),
                         ("rbc_direct", "rbc_direct_demo.csv"),
                         ("coinbase", "coinbase_demo.csv"),
                         ("kraken", "kraken_demo.csv")):
        out.append((broker, demo, (EX / demo).read_text(encoding="utf-8")))
        out.append((broker, f"fixtures/{broker}",
                    (FIX / broker / "sample.csv").read_text(
                        encoding="utf-8")))
    return out


_POSITIONS_ONLY = [("rbc_holdings", "rbc holdings", _RBC_HOLD),
                   ("rbc_holdings", "rbc holdings, no cost",
                    _RBC_HOLD_NO_COST),
                   ("rbc_holdings", "rbc holdings, bare",
                    "Holdings Export as of Jan 5, 2026\n\nSymbol,Quantity\n"
                    "ZZR,10\n")]


class TestDetectionMatrix(unittest.TestCase):
    def test_positions_only_samples_match_no_trade_detector(self):
        for kind, label, text in _POSITIONS_ONLY:
            rows = D.csv_rows(text)
            for name, fn in D.DETECTORS.items():
                with self.subTest(sample=label, detector=name):
                    self.assertIsNone(fn(rows, rows[:D.SCAN_ROWS])[0])
            with tempfile.TemporaryDirectory() as td:
                p = _write(td, "export.csv", text)
                det = D.detect(p)
                with self.subTest(sample=label):
                    self.assertIsNone(det.broker)
                    self.assertEqual(det.positions, kind)
                    self.assertEqual(P.positions_only(p), kind)
                    self.assertEqual(P.detect_positions(p), kind)

    def test_trade_exports_are_never_positions_only(self):
        for broker, label, text in _trade_samples():
            with tempfile.TemporaryDirectory() as td:
                p = _write(td, "export.csv", text)
                with self.subTest(sample=label):
                    self.assertIsNone(P.positions_only(p))
                    self.assertIsNone(P.detect_positions(p))
                    self.assertEqual(D.detect(p).broker, broker)
                    self.assertEqual(D.detect(p).positions, "")

    def test_ib_statement_with_positions_is_activity_and_positions(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "s.csv", _IB_STATEMENT)
            self.assertEqual(D.detect(p).broker, "ib")
            self.assertEqual(P.detect_positions(p), "ib_open_positions")
            self.assertIsNone(P.positions_only(p))

    def test_toml(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "h.toml", _TOML)
            q = _write(td, "taxjson.toml", "[settings]\nyear = 2025\n")
            self.assertEqual(P.detect_positions(p), "holdings_toml")
            self.assertEqual(P.positions_only(p), "holdings_toml")
            self.assertIsNone(P.detect_positions(q))

    def test_utf16_holdings_export(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "h.csv", _RBC_HOLD, "utf-16")
            self.assertEqual(D.detect(p).positions, "rbc_holdings")
            self.assertEqual(len(P.read_positions(p).rows), 2)

    def test_unsupported_brokers_say_why(self):
        self.assertEqual(set(P.UNSUPPORTED),
                         {"questrade", "webull", "coinbase", "kraken"})
        self.assertIn("[[holding]] TOML", P.UNSUPPORTED["questrade"])
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "q.csv",
                       (EX / "questrade_demo.csv").read_text())
            with self.assertRaises(P.PositionsReportError) as cm:
                P.read_positions(p)
        self.assertIn("not a positions report", str(cm.exception))


# ------------------------------------------------------------ run

_CONFIG = """[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"
"""


def _env():
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = (str(REPO_ROOT / "src") + os.pathsep
                       + e.get("PYTHONPATH", ""))
    return e


def _cli(*args, root=None):
    cmd = [sys.executable, "-m"]
    cmd += (["taxjson.bin.taxjson_run", "-C", str(root), *args] if root
            else list(args))
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, env=_env(), stdin=subprocess.DEVNULL)


class TestRunSkipsPositionsReports(unittest.TestCase):
    _TT = "BUYSELL 2025-02-03 09:30:00 ZZR.TO 10 CAD 5 50 0\n"

    def test_run_completes_and_says_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_CONFIG)
            d = root / "inputs" / "margin"
            d.mkdir(parents=True)
            (d / "a.tt").write_text(self._TT)
            (d / "holdings_55500001.csv").write_text(_RBC_HOLD)  # pii-ok
            for args in (("run", "--no-input"),
                         ("run", "--fast", "--no-input")):
                r = _cli(*args, root=root)
                with self.subTest(args=args):
                    self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                    # The console names the file as it is on disk; the
                    # saved .diag masks it.
                    self.assertIn(
                        "  inputs/margin/holdings_55500001.csv → "  # pii-ok
                        "positions report (RBC Holdings Export, as of "
                        "2026-01-05) — not activity; skipped (read it with "
                        "`taxjson sanity` or `taxjson opening`)", r.stdout)
                    diag = (root / "work" /
                            "margin_detect.diag").read_text()
                    self.assertIn("inputs/margin/holdings_55***.csv → "
                                  "positions report", diag)
                    self.assertNotIn("55500001", diag)  # pii-ok

    def test_folder_with_only_a_positions_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_CONFIG)
            d = root / "inputs" / "margin"
            d.mkdir(parents=True)
            (d / "h.csv").write_text(_RBC_HOLD)
            r = _cli("run", "--no-input", root=root)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("h.csv → positions report", r.stdout)

    def test_detect_brokerage_tool(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "h.csv", _RBC_HOLD)
            r = _cli("taxjson.bin.taxjson_detect_brokerage", str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "positions:rbc_holdings\n")
        self.assertIn("positions report (RBC Holdings Export", r.stderr)


if __name__ == "__main__":
    unittest.main()
