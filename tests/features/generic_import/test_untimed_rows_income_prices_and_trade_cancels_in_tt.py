"""Fixes of 2026-10-29, round 7 (wave C), group taxtext: the standalone
pipeline tools. The validator survives an impossible trade date beside
a settlement date (backlog TJ-0987), reads the qty alias (TJ-0977) and
checks multiplier and market_value (TJ-0979); the .tt conversion reads
the qty alias too (TJ-0977), writes an untimed row's clock (TJ-0983),
reads an income line's per-share price (TJ-0984) and keeps a broker
trade cancellation (TJ-0985); currency conversion prices income on its
pay date (TJ-0974) and names a selected rate's own source (TJ-0986);
sort and conversion keep the per-file broker accounts (TJ-0976). One
test class per backlog item, each named for it.

Every fixture is SYNTHETIC: invented tickers (SYN*), invented account
ids, small amounts.

Formerly: test_fix_1029_r7c_taxtext
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO


def _cli(module, args, cwd=None, stdin=None):
    """Run a taxjson module as the pipeline does: (rc, stdout, stderr)."""
    env = dict(os.environ, TAXJSON_WIDTH="0", TAXJSON_OFFLINE="1",
               PYTHONPATH=str(REPO_ROOT / "src"))
    c = subprocess.run(python_module_argv(f"taxjson.bin.{module}",
                                          [str(a) for a in args]),
                       capture_output=True, text=True, env=env, cwd=cwd,
                       input=stdin)
    return c.returncode, c.stdout, c.stderr


def _buy(**kw):
    row = {"action": "BUYSELL", "date": "2025-02-28",
           "date_settle": "2025-03-03", "time": "10:00:00",
           "symbol": "SYN.TO", "quantity": 10.0, "price": 10.0,
           "net_amount": 100.0, "fee": 0.0, "currency": "CAD",
           "account": "margin"}
    row.update(kw)
    return row


# =============================================== TJ-0983 an untimed row
class TestTJ0983UntimedRowsWriteAReadableClock(unittest.TestCase):
    """TJ-0983: a JSON row with time "" or null converted to .tt with a
    missing column or the literal None, a line the reader refused.

    Formerly: test_fix_1029_r7c_taxtext.TestTJ0983UntimedRowsWriteAReadableClock"""

    def test_empty_and_null_time_write_the_unknown_clock(self):
        from taxjson.lib import tt_format as CT
        for t in ("", None, "  "):
            line = CT.tx_to_tt_line(_buy(time=t), "settle")
            self.assertEqual(line.split()[2], "09:30:00", line)
            row = CT.parse_tt_line(line, "margin")
            self.assertEqual(row["symbol"], "SYN.TO")
            self.assertEqual(row["time"], "09:30:00")

    def test_an_unpadded_clock_is_padded(self):
        from taxjson.lib import tt_format as CT
        self.assertEqual(CT.tt_time({"time": "9:30"}), "09:30:00")
        self.assertEqual(CT.tt_time({"time": "14:05:07"}), "14:05:07")
        self.assertEqual(CT.tt_time({}), "09:30:00")

    def test_round_trip_through_the_cli(self):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            tt = Path(d) / "book.tt"
            book.write_text(json.dumps({"transactions": [
                _buy(time=""), _buy(time=None, date="2025-03-03")]}))
            rc, out, err = _cli("taxjson_convert_tt",
                                [book, "--date-basis", "settle"])
            self.assertEqual(rc, 0, err)
            tt.write_text(out)
            rc, out, err = _cli("taxjson_convert_tt",
                                [tt, "--account", "margin"])
            self.assertEqual(rc, 0, err)
            rows = json.loads(out)["transactions"]
        self.assertEqual([r["time"] for r in rows], ["09:30:00"] * 2)


# =========================================== TJ-0984 the per-share price
class TestTJ0984IncomeLineKeepsItsPrice(unittest.TestCase):
    """TJ-0984: the reader of a DIVIDEND / DIVIDEND_IN_LIEU / TAX line
    never read its PRICE column: the row booked price 0 and `tjs divs`
    showed a per-share amount of 0.

    Formerly: test_fix_1029_r7c_taxtext.TestTJ0984IncomeLineKeepsItsPrice"""

    DIV = {"action": "DIVIDEND", "date": "2025-06-02",
           "date_settle": "2025-06-02", "time": "10:00:00",
           "symbol": "SYN.TO", "quantity": 10.0, "price": 2.0,
           "gross_amount": 20.0, "net_amount": 20.0, "currency": "CAD",
           "account": "margin", "type": "dividend"}

    def test_every_income_action_reads_the_price(self):
        from taxjson.lib import tt_format as CT
        for action in ("DIVIDEND", "DIVIDEND_IN_LIEU", "TAX"):
            row = CT.parse_tt_line(
                f"{action} 2025-06-02 10:00:00 SYN.TO 10 CAD 2 20.00",
                "margin")
            self.assertEqual(row["price"], 2.0, action)
            self.assertEqual(row["gross_amount"], 20.0)
            self.assertEqual(row["quantity"], 10.0)

    def test_round_trip_keeps_the_price_and_the_id(self):
        from taxjson.lib import tt_format as CT
        from taxjson.model.transaction import TaxTransaction
        line = CT.tx_to_tt_line(self.DIV)
        row = CT.parse_tt_line(line, "margin")
        self.assertEqual(row["price"], 2.0)
        native = TaxTransaction(**{k: v for k, v in row.items()
                                   if k != "id"})
        self.assertEqual(row["id"], native.compute_id())

    def test_a_non_numeric_price_is_refused(self):
        from taxjson.lib import tt_format as CT
        with self.assertRaises(ValueError):
            CT.parse_tt_line(
                "DIVIDEND 2025-06-02 10:00:00 SYN.TO 10 CAD x 20.00",
                "margin")


# =========================================== TJ-0985 a trade cancellation
class TestTJ0985TradeCancelSurvivesTaxtext(unittest.TestCase):
    """TJ-0985: an IB cancellation (`Ca`, type=trade_cancel) of a sale
    in a correction statement was written to .tt as an ordinary
    purchase: the cancelled sale stayed booked (proceeds 200, gain 100)
    where the broker files book nothing.

    Formerly: test_fix_1029_r7c_taxtext.TestTJ0985TradeCancelSurvivesTaxtext"""

    BUY = _buy(date="2025-01-02", date_settle="2025-01-03",
               description="SYN")
    SALE = _buy(date="2025-06-02", date_settle="2025-06-03",
                quantity=-10.0, price=20.0, net_amount=200.0,
                description="SYN")
    CANCEL = dict(SALE, quantity=10.0, type="trade_cancel")

    def test_the_token_is_written_and_read_back(self):
        from taxjson.lib import tt_format as CT
        line = CT.tx_to_tt_line(self.CANCEL)
        # (then the trade date its line's settlement date is not:
        # backlog TJ-5213)
        self.assertTrue(line.endswith(" type=trade_cancel traded=2025-06-02"),
                        line)
        row = CT.parse_tt_line(line, "margin")
        self.assertEqual(row["type"], "trade_cancel")
        self.assertNotIn("type=", CT.tx_to_tt_line(self.SALE))

    def test_the_merge_drops_the_pair_after_a_round_trip(self):
        from taxjson.lib import tt_format as CT
        from taxjson.lib.trade_cancel import pair_cancellations
        rows = [CT.parse_tt_line(CT.tx_to_tt_line(r), "margin")
                for r in (self.BUY, self.SALE, self.CANCEL)]
        kept, pairs, unmatched = pair_cancellations(rows)
        self.assertEqual([r["quantity"] for r in kept], [10.0])
        self.assertEqual((len(pairs), unmatched), (1, []))

    def test_the_token_is_checked(self):
        from taxjson.lib import tt_format as CT
        for bad, why in (
                ("BUYSELL 2025-06-03 10:00:00 SYN.TO 0 CAD 20 0 "
                 "type=trade_cancel", "not 0"),
                ("BUYSELL 2025-06-03 10:00:00 SYN.TO 10 CAD 20 200 "
                 "type=trade_cancel record=2025-06-01", "unknown .tt token"),
                ("BUYSELL 2025-06-03 10:00:00 SYN.TO 10 CAD 20 200 "
                 "type=split", "trade_cancel")):
            with self.assertRaises(ValueError) as cm:
                CT.parse_tt_line(bad, "margin")
            self.assertIn(why, str(cm.exception))

    def test_a_project_of_the_tt_book_has_no_disposition(self):
        from _qa_project import tj, toml
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            book.write_text(json.dumps({"transactions": [
                self.BUY, self.SALE, self.CANCEL]}))
            rc, out, err = _cli("taxjson_convert_tt",
                                [book, "--date-basis", "settle"])
            self.assertEqual(rc, 0, err)
            root = Path(d) / "proj"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(toml(year=2025))
            (root / "inputs" / "margin" / "book.tt").write_text(out)
            tj(root, "run", "--strict", "--no-input")
            totals = json.loads(tj(root, "sum", "--json").stdout)[
                "filing"]["totals"]
        self.assertEqual((totals["proceeds"], totals["acb"],
                          totals["gain"]), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
