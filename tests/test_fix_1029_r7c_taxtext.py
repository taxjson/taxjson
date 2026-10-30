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
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.bin import taxjson_validate as V
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent


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


# ================================ TJ-0987 an impossible trade date
class TestTJ0987ImpossibleTradeDateBesideSettlement(unittest.TestCase):
    """TJ-0987: a purchase dated 2025-02-30 with settlement 2025-03-03
    crashed taxjson-validate with a ValueError traceback (the
    settlement-lag check parsed the date again): no report, and the
    next file was never validated."""

    def test_reported_with_its_row_and_no_traceback(self):
        issues, _w = V.validate_transactions(
            [_buy(date="2025-02-30")], "book.json")
        found = [m for msgs in issues.values() for m in msgs]
        self.assertTrue(any("Impossible date: '2025-02-30'" in m
                            for m in found), found)

    def test_the_next_file_is_still_validated(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "a.json"
            good = Path(d) / "b.json"
            bad.write_text(json.dumps([_buy(date="2025-02-30")]))
            good.write_text(json.dumps([_buy()]))
            rc, out, err = _cli("taxjson_validate", [bad, good])
        self.assertEqual(rc, 1, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("Impossible date: '2025-02-30'", out)
        self.assertIn("OK: " + str(good), out)

    def test_a_real_day_keeps_the_settlement_checks(self):
        issues, _w = V.validate_transactions([_buy()], "book.json")
        self.assertEqual(dict(issues), {})
        issues, _w = V.validate_transactions(
            [_buy(date_settle="2025-02-27")], "book.json")
        self.assertTrue(any("before the trade date" in m
                            for msgs in issues.values() for m in msgs))


# ===================================================== TJ-0977 qty alias
def _qty_rows():
    buy = _buy(date="2025-01-02", date_settle="2025-01-03")
    sell = _buy(date="2025-06-02", date_settle="2025-06-03", price=20.0,
                net_amount=200.0)
    buy["qty"] = buy.pop("quantity")
    sell["qty"] = -sell.pop("quantity")
    return [buy, sell]


class TestTJ0977QtyAlias(unittest.TestCase):
    """TJ-0977: `qty`, the loaders' alias of `quantity`, read as 0 in
    taxjson-validate ("Quantity is 0") and taxjson-convert-tt (every
    line written with quantity 0)."""

    def test_validate_accepts_the_alias(self):
        issues, _w = V.validate_transactions(_qty_rows(), "book.json")
        self.assertEqual(dict(issues), {})

    def test_validate_still_refuses_a_zero_or_missing_quantity(self):
        rows = _qty_rows()
        rows[0]["qty"] = 0
        del rows[1]["qty"]
        issues, _w = V.validate_transactions(rows, "book.json")
        found = [m for msgs in issues.values() for m in msgs]
        self.assertTrue(any("Quantity is 0" in m for m in found), found)
        self.assertTrue(any("Missing 'quantity'" in m for m in found),
                        found)

    def test_convert_tt_writes_the_quantity(self):
        from taxjson.bin import taxjson_convert_tt as CT
        lines = [CT.tx_to_tt_line(r) for r in _qty_rows()]
        self.assertEqual(lines[0].split()[4], "10.00000000")
        self.assertEqual(lines[1].split()[4], "-10.00000000")
        for line in lines:
            row = CT.parse_tt_line(line, "margin")
            self.assertEqual(abs(row["quantity"]), 10.0)


# ============================ TJ-0979 multiplier and market_value types
class TestTJ0979ValidatorChecksEveryNumericField(unittest.TestCase):
    """TJ-0979: taxjson-validate said "Validation passed" for a row with
    multiplier="not-a-number" or market_value=true, which the shared
    loader (taxjson-sort --strict) refuses."""

    def _errors(self, **kw):
        issues, _w = V.validate_transactions([_buy(**kw)], "book.json")
        return [m for msgs in issues.values() for m in msgs]

    def test_malformed_values_are_errors(self):
        self.assertTrue(any("Non-numeric multiplier" in m for m in
                            self._errors(multiplier="not-a-number")))
        self.assertTrue(any("Non-numeric market_value" in m for m in
                            self._errors(market_value=True)))
        self.assertTrue(any("Null multiplier" in m for m in
                            self._errors(multiplier=None)))

    def test_the_loader_refuses_the_same_rows(self):
        from taxjson.lib.core import coerce_transaction_row
        for kw in ({"multiplier": "not-a-number"}, {"market_value": True}):
            with self.assertRaises(ValueError):
                coerce_transaction_row(_buy(**kw), 0, "book.json")

    def test_numbers_and_absence_pass(self):
        self.assertEqual(self._errors(multiplier=100, market_value=95.5),
                         [])
        self.assertEqual(self._errors(), [])


# =============================================== TJ-0983 an untimed row
class TestTJ0983UntimedRowsWriteAReadableClock(unittest.TestCase):
    """TJ-0983: a JSON row with time "" or null converted to .tt with a
    missing column or the literal None, a line the reader refused."""

    def test_empty_and_null_time_write_the_unknown_clock(self):
        from taxjson.bin import taxjson_convert_tt as CT
        for t in ("", None, "  "):
            line = CT.tx_to_tt_line(_buy(time=t), "settle")
            self.assertEqual(line.split()[2], "09:30:00", line)
            row = CT.parse_tt_line(line, "margin")
            self.assertEqual(row["symbol"], "SYN.TO")
            self.assertEqual(row["time"], "09:30:00")

    def test_an_unpadded_clock_is_padded(self):
        from taxjson.bin import taxjson_convert_tt as CT
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
    showed a per-share amount of 0."""

    DIV = {"action": "DIVIDEND", "date": "2025-06-02",
           "date_settle": "2025-06-02", "time": "10:00:00",
           "symbol": "SYN.TO", "quantity": 10.0, "price": 2.0,
           "gross_amount": 20.0, "net_amount": 20.0, "currency": "CAD",
           "account": "margin", "type": "dividend"}

    def test_every_income_action_reads_the_price(self):
        from taxjson.bin import taxjson_convert_tt as CT
        for action in ("DIVIDEND", "DIVIDEND_IN_LIEU", "TAX"):
            row = CT.parse_tt_line(
                f"{action} 2025-06-02 10:00:00 SYN.TO 10 CAD 2 20.00",
                "margin")
            self.assertEqual(row["price"], 2.0, action)
            self.assertEqual(row["gross_amount"], 20.0)
            self.assertEqual(row["quantity"], 10.0)

    def test_round_trip_keeps_the_price_and_the_id(self):
        from taxjson.bin import taxjson_convert_tt as CT
        from taxjson.lib.core import TaxTransaction
        line = CT.tx_to_tt_line(self.DIV)
        row = CT.parse_tt_line(line, "margin")
        self.assertEqual(row["price"], 2.0)
        native = TaxTransaction(**{k: v for k, v in row.items()
                                   if k != "id"})
        self.assertEqual(row["id"], native.compute_id())

    def test_a_non_numeric_price_is_refused(self):
        from taxjson.bin import taxjson_convert_tt as CT
        with self.assertRaises(ValueError):
            CT.parse_tt_line(
                "DIVIDEND 2025-06-02 10:00:00 SYN.TO 10 CAD x 20.00",
                "margin")


# =========================================== TJ-0985 a trade cancellation
class TestTJ0985TradeCancelSurvivesTaxtext(unittest.TestCase):
    """TJ-0985: an IB cancellation (`Ca`, type=trade_cancel) of a sale
    in a correction statement was written to .tt as an ordinary
    purchase: the cancelled sale stayed booked (proceeds 200, gain 100)
    where the broker files book nothing."""

    BUY = _buy(date="2025-01-02", date_settle="2025-01-03",
               description="SYN")
    SALE = _buy(date="2025-06-02", date_settle="2025-06-03",
                quantity=-10.0, price=20.0, net_amount=200.0,
                description="SYN")
    CANCEL = dict(SALE, quantity=10.0, type="trade_cancel")

    def test_the_token_is_written_and_read_back(self):
        from taxjson.bin import taxjson_convert_tt as CT
        line = CT.tx_to_tt_line(self.CANCEL)
        self.assertTrue(line.endswith(" type=trade_cancel"), line)
        row = CT.parse_tt_line(line, "margin")
        self.assertEqual(row["type"], "trade_cancel")
        self.assertNotIn("type=", CT.tx_to_tt_line(self.SALE))

    def test_the_merge_drops_the_pair_after_a_round_trip(self):
        from taxjson.bin import taxjson_convert_tt as CT
        from taxjson.lib.trade_cancel import pair_cancellations
        rows = [CT.parse_tt_line(CT.tx_to_tt_line(r), "margin")
                for r in (self.BUY, self.SALE, self.CANCEL)]
        kept, pairs, unmatched = pair_cancellations(rows)
        self.assertEqual([r["quantity"] for r in kept], [10.0])
        self.assertEqual((len(pairs), unmatched), (1, []))

    def test_the_token_is_checked(self):
        from taxjson.bin import taxjson_convert_tt as CT
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


# ======================================= TJ-0974 income on its pay date
class TestTJ0974IncomeConvertedOnItsPayDate(unittest.TestCase):
    """TJ-0974: a USD 100 dividend paid June 2 with date_settle June 3
    was converted at June 3's rate (1.50: CAD 150) instead of the pay
    date's (1.25: CAD 125)."""

    RATES = ("2025-06-02 12:00:00 USD CAD 1.25\n"
             "2025-06-03 12:00:00 USD CAD 1.50\n")

    @staticmethod
    def _div(action="DIVIDEND", **kw):
        row = {"action": action, "date": "2025-06-02",
               "date_settle": "2025-06-03", "time": "10:00:00",
               "symbol": "SYN.US", "currency": "USD", "net_amount": 100.0,
               "gross_amount": 100.0, "account": "margin"}
        row.update(kw)
        return row

    def _convert(self, rows):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            rates = Path(d) / "rates.txt"
            book.write_text(json.dumps({"transactions": rows}))
            rates.write_text(self.RATES)
            rc, out, err = _cli("taxjson_convert_currency",
                                [book, "--to", "CAD", "--rates", rates])
        self.assertEqual(rc, 0, err)
        return json.loads(out)["transactions"]

    @rule("CA-FX-01")
    def test_income_withholding_and_fees_take_the_pay_dates_rate(self):
        rows = self._convert([
            self._div(),
            self._div("DIVIDEND_IN_LIEU"),
            self._div("TAX", net_amount=-15.0, gross_amount=-15.0),
            self._div("INTEREST", symbol="CASH"),
            self._div("FEE", symbol="CASH", net_amount=8.0,
                      gross_amount=0.0)])
        self.assertEqual([r["net_amount"] for r in rows],
                         [125.0, 125.0, -18.75, 125.0, 10.0])

    @rule("CA-FX-01")
    def test_a_trade_keeps_its_settlement_dates_rate(self):
        rows = self._convert([_buy(date="2025-06-02",
                                   date_settle="2025-06-03",
                                   currency="USD")])
        self.assertEqual(rows[0]["net_amount"], 150.0)

    @rule("US-FX-02")
    def test_rate_date_is_the_one_rule(self):
        from taxjson.bin.taxjson_convert_currency import rate_date
        from taxjson.lib.core import TaxTransaction
        self.assertEqual(rate_date(self._div()), "2025-06-02")
        self.assertEqual(rate_date(self._div(date_settle="")),
                         "2025-06-02")
        self.assertEqual(rate_date(_buy()), "2025-03-03")
        self.assertEqual(rate_date(_buy(date_settle="")), "2025-02-28")
        self.assertEqual(rate_date(TaxTransaction(**self._div())),
                         "2025-06-02")


# =============================== TJ-0986 the source of the selected rate
class TestTJ0986SourceOfTheSelectedRate(unittest.TestCase):
    """TJ-0986: a five-column USD/CAD 1.25 row followed by a 1.50 quote
    of the same day tagged `boc`: the conversion applied 1.25 (the first
    row) but its summary named the Bank of Canada, the ignored row's
    source."""

    def _sources(self, text):
        from taxjson.bin import taxjson_convert_currency as CC
        with tempfile.TemporaryDirectory() as d:
            rates = Path(d) / "rates.txt"
            rates.write_text(text)
            with contextlib.redirect_stderr(io.StringIO()):
                history = CC.load_exchange_rates(rates, "CAD")
            return history, CC.load_rate_sources(rates, "CAD")

    def test_an_untagged_selected_rate_has_no_source(self):
        history, sources = self._sources(
            "2025-06-02 12:00:00 USD CAD 1.25\n"
            "2025-06-02 16:00:00 USD CAD 1.50 boc\n")
        self.assertEqual(str(history["USD"]["2025-06-02"]), "1.25")
        self.assertEqual(sources.get("USD", {}), {})

    def test_the_summary_says_no_source_column(self):
        from taxjson.bin import taxjson_convert_currency as CC
        _h, sources = self._sources(
            "2025-06-02 12:00:00 USD CAD 1.25\n"
            "2025-06-02 16:00:00 USD CAD 1.50 boc\n")
        CC.reset_fallback_tally()
        CC._RATES_USED.add(("USD", "2025-06-02"))
        try:
            text = CC.rate_source_summary(sources)
        finally:
            CC.reset_fallback_tally()
        self.assertIn("Bank of Canada Valet for 0 dates", text)
        self.assertIn("1 from a rates file without a source column", text)

    def test_a_skipped_row_does_not_reserve_the_day(self):
        _h, sources = self._sources(
            "2025-06-02 12:00:00 USD CAD 1_25 yahoo\n"
            "2025-06-02 12:00:00 USD EUR 1.10 yahoo\n"
            "2025-06-02 16:00:00 USD CAD 1.50 boc\n"
            "2025-06-03 16:00:00 USD CAD 1.40 boc\n"
            "2025-06-03 17:00:00 USD CAD 1.45 yahoo\n")
        self.assertEqual(sources["USD"], {"2025-06-02": "boc",
                                          "2025-06-03": "boc"})


# =========================== TJ-0976 per-file accounts through a stage
class TestTJ0976PassThroughKeepsSourceAccounts(unittest.TestCase):
    """TJ-0976: two consolidated statements of disjoint broker accounts,
    each with one identical CAD 100 purchase: `sort --dedup` keeps both,
    but a plain sort (or an identity CAD-to-CAD conversion) first wrote
    no metadata.source_accounts, and the next dedup kept one."""

    def _doc(self):
        rows = []
        for src in ("one.csv", "two.csv"):
            row = _buy(date="2025-01-02", date_settle="2025-01-03")
            row["source"] = src
            rows.append(row)
        return {"transactions": rows, "metadata": {"source_accounts": {
            "one.csv": ["aaaa000001", "aaaa000002"],
            "two.csv": ["bbbb000001", "bbbb000002"]}}}

    def _dedup_after(self, module, extra):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            mid = Path(d) / "mid.json"
            book.write_text(json.dumps(self._doc()))
            rc, out, err = _cli(module, [book, *extra])
            self.assertEqual(rc, 0, err)
            meta = json.loads(out)["metadata"]
            self.assertEqual(meta["source_accounts"],
                             self._doc()["metadata"]["source_accounts"])
            mid.write_text(out)
            rc, out, err = _cli("taxjson_sort", [mid, "--dedup", "--strict"])
            self.assertEqual(rc, 0, err)
        return json.loads(out)["transactions"]

    def test_direct_dedup_keeps_both(self):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            book.write_text(json.dumps(self._doc()))
            rc, out, err = _cli("taxjson_sort", [book, "--dedup", "--strict"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(json.loads(out)["transactions"]), 2)

    def test_a_plain_sort_keeps_the_accounts(self):
        self.assertEqual(len(self._dedup_after("taxjson_sort",
                                               ["--strict"])), 2)

    def test_an_identity_conversion_keeps_the_accounts(self):
        self.assertEqual(len(self._dedup_after("taxjson_convert_currency",
                                               ["--to", "CAD"])), 2)

    def test_stdin_keeps_them_too(self):
        rc, out, err = _cli("taxjson_sort", [],
                            stdin=json.dumps(self._doc()))
        self.assertEqual(rc, 0, err)
        self.assertIn("one.csv", json.loads(out)["metadata"][
            "source_accounts"])
        rc, out, err = _cli("taxjson_convert_currency", ["--to", "CAD"],
                            stdin=json.dumps(self._doc()))
        self.assertEqual(rc, 0, err)
        self.assertIn("two.csv", json.loads(out)["metadata"][
            "source_accounts"])

    def test_no_accounts_no_metadata_block(self):
        with tempfile.TemporaryDirectory() as d:
            book = Path(d) / "book.json"
            book.write_text(json.dumps([_buy()]))
            rc, out, err = _cli("taxjson_sort", [book])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("metadata", json.loads(out))


if __name__ == "__main__":
    unittest.main()
