"""Regression tests for fix round 7 (wave B), group coreus: the engines'
income and US paths.

TJ-0647 a US stock dividend's entitled sale before a split was counted
        in pre-split shares: the dividend ratio came out wrong.
TJ-0648 an entitled sale under the ticker a rename replaced was not
        found: the held shares took the whole dividend.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx


def _gains(book, sheltered=(), year=2025, country="usa", **req):
    """pipeline.run_gains (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         year=year, phantom_hint=False,
                                         **req))
    res["_stderr"] = err.getvalue()
    return res


def _sales(res, **match):
    return [t for t in res["transactions"]
            if t.get("qty") and all(t.get(k) == v for k, v in match.items())]


def _inv(res):
    return {(i["account"], i["symbol"]): (round(i["qty"], 6),
                                          round(i["total_cost"], 4),
                                          i["holding_start_date"])
            for i in res["inventory"]}


# ------------------------------------------------------- TJ-0647, TJ-0648
class TestStockDividendSaleThroughSplitOrRename(unittest.TestCase):
    """100 bought for 1000; ex-date 2025-06-02; 50 sold 2025-06-03 for
    600. With a 2:1 split on 2025-06-05 and 20 new shares paid on
    2025-06-16, the 200 entitled post-split shares become 220: the sale
    keeps 1000 / 2 / 1.1 of cost (gain 145.4545) and the 120 held cost
    545.4545, as when the split comes before the sale. With a rename
    instead and 10 new shares, the same."""

    def _book(self, split):
        book = [tx("BUYSELL", "2025-01-06", "ZQS.US", 100, 1000.0),
                tx("BUYSELL", "2025-06-03", "ZQS.US", -50, 600.0)]
        if split:
            book += [tx("SPLIT", "2025-06-05", "ZQS.US", 2),
                     tx("BUYSELL", "2025-06-16", "ZQS.US", 20, 0.0,
                        type="stock_dividend", ex_date="2025-06-02")]
        else:
            book += [tx("SPLIT", "2025-06-05", "ZQS.US", 1,
                        symbol_new="ZQN.US"),
                     tx("BUYSELL", "2025-06-16", "ZQN.US", 10, 0.0,
                        type="stock_dividend", ex_date="2025-06-02")]
        return book

    @rule("US-STKDIV-05")
    def test_split_between_the_sale_and_the_pay_date(self):
        res = _gains(self._book(split=True))
        sale = _sales(res)[0]
        self.assertAlmostEqual(sale["cost"], 454.5455, places=3)
        self.assertAlmostEqual(sale["gain"], 145.4545, places=3)
        self.assertEqual(_inv(res)[("margin", "ZQS.US")],
                         (120.0, 545.4545, "2025-01-06"))

    @rule("US-STKDIV-05")
    def test_rename_between_the_sale_and_the_pay_date(self):
        res = _gains(self._book(split=False))
        sale = _sales(res)[0]
        self.assertEqual(sale["symbol"], "ZQS.US")
        self.assertAlmostEqual(sale["cost"], 454.5455, places=3)
        self.assertAlmostEqual(sale["gain"], 145.4545, places=3)
        self.assertEqual(_inv(res)[("margin", "ZQN.US")],
                         (60.0, 545.4545, "2025-01-06"))

    @rule("US-STKDIV-05")
    def test_another_security_sold_under_the_old_ticker_later(self):
        """A sale of the old ticker AFTER the rename is not the renamed
        security's: it keeps its own cost."""
        book = [tx("BUYSELL", "2025-01-06", "ZQS.US", 100, 1000.0),
                tx("SPLIT", "2025-05-29", "ZQS.US", 1, symbol_new="ZQN.US"),
                tx("BUYSELL", "2025-05-30", "ZQS.US", 10, 50.0),
                tx("BUYSELL", "2025-06-03", "ZQS.US", -10, 60.0),
                tx("BUYSELL", "2025-06-16", "ZQN.US", 10, 0.0,
                   type="stock_dividend", ex_date="2025-06-02")]
        res = _gains(book)
        sale = _sales(res, symbol="ZQS.US")[0]
        self.assertAlmostEqual(sale["cost"], 50.0, places=4)
        self.assertEqual(_inv(res)[("margin", "ZQN.US")],
                         (110.0, 1000.0, "2025-01-06"))


# ---------------------------------------------------------------- TJ-0649
class TestSubMicroPositionWithARealCost(unittest.TestCase):
    """0.0005 shares bought for 500 and consolidated 1:1000 are 0.0000005
    shares that still cost 500: held (inventory, cost timeline, T1135),
    and their sale for 600 a gain of 100, as when bought that small."""

    def _book(self, sold=True, consolidate=True):
        if consolidate:
            book = [tx("BUYSELL", "2025-01-06", "ZQD.US", 0.0005, 500.0),
                    tx("SPLIT", "2025-06-02", "ZQD.US", 0.001)]
        else:
            book = [tx("BUYSELL", "2025-01-06", "ZQD.US", 0.0000005, 500.0)]
        if sold:
            book.append(tx("BUYSELL", "2025-06-03", "ZQD.US", -0.0000005,
                           600.0))
        return book

    @rule("CA-ACB-01")
    def test_consolidated_into_a_fraction_then_sold(self):
        for consolidate in (True, False):
            res = _gains(self._book(consolidate=consolidate),
                         country="canada")
            self.assertAlmostEqual(res["summary"]["total_gain"], 100.0,
                                   places=4)
            self.assertNotIn("stranded", res["_stderr"])

    @rule("CA-ACB-01")
    def test_held_in_inventory_timeline_and_t1135(self):
        from taxjson.bin.taxjson_t1135 import walk_costs
        from taxjson.lib.cost_timeline import inventory_on
        for consolidate in (True, False):
            book = self._book(sold=False, consolidate=consolidate)
            res = _gains(book, country="canada", cost_timeline=True)
            inv = {i["symbol"]: i for i in res["inventory"]}
            self.assertAlmostEqual(inv["ZQD.US"]["total_cost"], 500.0)
            self.assertNotIn("stranded", res["_stderr"])
            tl = inventory_on(res["cost_timeline"], "2025-12-31")
            self.assertEqual([r["total_cost"] for r in tl], [500.0])
            f = walk_costs([t.to_dict() for t in book], 2025,
                           {"ZQD.US": "USA"})
            self.assertEqual(f["max_total_cost"], 500.0)

    @rule("CA-ACB-01")
    def test_a_sale_residue_is_still_flat(self):
        """A sale that leaves 4e-7 of 100 shares (cost 0.000004) drains
        the pool as before."""
        book = [tx("BUYSELL", "2025-01-06", "ZQD.US", 100, 1000.0),
                tx("BUYSELL", "2025-06-03", "ZQD.US", -99.9999996, 1200.0)]
        res = _gains(book, country="canada")
        self.assertEqual(res["inventory"], [])


# ---------------------------------------------------------------- TJ-0655
class TestUntypedManualRocWithARecordDate(unittest.TestCase):
    """100 trust units bought 2025-12-01 for 100 and sold 2025-12-30 for
    120; a hand-entered ADJUST of -50 paid 2026-01-05 with record date
    2025-12-29. With or without `type=roc`, it lowers the ACB on the
    record date: the 2025 gain is 70, none in 2026."""

    def _gains(self, typ):
        book = [tx("BUYSELL", "2025-12-01", "ZQT.UN.TO", 100, 100.0,
                   currency="CAD"),
                tx("BUYSELL", "2025-12-30", "ZQT.UN.TO", -100, 120.0,
                   currency="CAD"),
                tx("ADJUST", "2026-01-05", "ZQT.UN.TO", 0, -50.0,
                   currency="CAD", record_date="2025-12-29", type=typ)]
        return _gains(book, country="canada", year=None)

    @rule("CA-INC-DATE-ROC-TRUST")
    def test_untyped_row_uses_its_record_date(self):
        for typ in ("roc", ""):
            res = self._gains(typ)
            by_year = {}
            for t in res["transactions"]:
                if t.get("gain") is not None:
                    by_year[t["date"][:4]] = (by_year.get(t["date"][:4], 0)
                                              + t["gain"])
            self.assertEqual({y: round(g, 2) for y, g in by_year.items()},
                             {"2025": 70.0}, typ)

    def test_a_cost_raise_typed_otherwise_is_not_moved(self):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules("canada")
        row = TaxTransaction(action="ADJUST", date="2026-01-05",
                             date_settle="2026-01-05", symbol="ZQT.UN.TO",
                             quantity=0.0, net_amount=50.0, currency="CAD",
                             account="margin", record_date="2025-12-29",
                             type="dist")
        self.assertEqual(rules.roc_record_date(row), "")
        row.type = ""
        self.assertEqual(rules.roc_record_date(row), "2025-12-29")
        row.corp_event_id = "ev1"
        self.assertEqual(rules.roc_record_date(row), "")


# ---------------------------------------------------------------- TJ-0729
class TestPaymentInLieuOfAReturnOfCapital(unittest.TestCase):
    """IB Canada pays 100 in lieu of a Canadian corporation's return of
    capital ("(Return of Capital)"): the payment it replaces was no
    taxable dividend, so s.260(5.1) deems no dividend: ordinary income.
    The same row without the label stays a deemed dividend."""

    HEAD = ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,Interactive Brokers Canada Inc\n"
            "Dividends,Header,Currency,Account,Date,Description,Amount\n")

    def _parse(self, marker):
        import tempfile
        from pathlib import Path
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.csv"
            p.write_text(self.HEAD + "Dividends,Data,CAD,U1234567,"
                         "2025-06-02,ZQR(CA9990000001) Payment in Lieu of "
                         f"Dividend CAD 1 per Share{marker},100\n")
            with contextlib.redirect_stderr(io.StringIO()):
                txs = IbBrokerage().parse_file(p)
        return [t for t in txs if t["action"] == "DIVIDEND_IN_LIEU"]

    @rule("CA-INC-03", "CA-INC-07")
    def test_roc_pil_is_ordinary_income(self):
        from taxjson.lib.core import coerce_transaction_row
        from taxjson.lib.income_dating import IncomeRules
        rules = IncomeRules("canada")
        roc = self._parse(" (Return of Capital)")
        plain = self._parse("")
        self.assertEqual(roc[0].get("income_label"), "return_of_capital")
        self.assertFalse(plain[0].get("income_label"))
        roc_row = coerce_transaction_row(roc[0], 0, "test")
        plain_row = coerce_transaction_row(plain[0], 0, "test")
        self.assertEqual(roc_row.income_label, "return_of_capital")
        self.assertFalse(rules.pil_is_dividend(roc_row))
        self.assertTrue(rules.pil_is_dividend(plain_row))

    def test_label_round_trips_through_tt(self):
        from taxjson.bin import taxjson_convert_tt as ct
        row = self._parse(" (Return of Capital)")[0]
        tail = ct.tt_tail(row, "DIVIDEND_IN_LIEU")
        self.assertIn("label=return_of_capital", tail)
        self.assertEqual(
            ct._check_facts("DIVIDEND_IN_LIEU",
                            {"label": "return_of_capital"}, tail,
                            "book.tt")["income_label"],
            "return_of_capital")


# ---------------------------------------------------------------- TJ-0723
class TestZeroBasisSpinOffKeepsTheParentsDates(unittest.TestCase):
    """10 parent shares bought 2024-01-02 for 100; a return of capital of
    100 on 2025-06-02 leaves them at zero basis; a §355 spin-off on
    2025-06-03 pays 2 shares with allocated basis 0; they are sold
    2025-08-01 for 10. The shares carry the parent's January 2024 date:
    a long-term gain of 10."""

    def _book(self, roc, alloc):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.corp_actions import CorporateAction, resolve_event
        ev = CorporateAction(
            date="2025-06-03", time="09:30:00", action_type="spinoff",
            source_symbol="ZQP.US", source_isin="", target_symbol="ZQC.US",
            target_isin="", ratio_new=1, ratio_old=5, qty_disposed=0,
            qty_received=2, fmv=0, currency="USD", target_currency="USD",
            account="margin", event_id="ev-zq-1")
        with contextlib.redirect_stderr(io.StringIO()):
            rows = resolve_event(ev, "tax_free_355", country="usa",
                                 hints={"allocated_acb": alloc})
        return ([tx("BUYSELL", "2024-01-02", "ZQP.US", 10, 100.0),
                 tx("ADJUST", "2025-06-02", "ZQP.US", 0, -roc, type="roc")]
                + [TaxTransaction(**r) for r in rows]
                + [tx("BUYSELL", "2025-08-01", "ZQC.US", -2, 10.0)])

    @rule("US-CORP-07")
    def test_zero_basis_parent(self):
        res = _gains(self._book(100.0, 0))
        sale = _sales(res, symbol="ZQC.US")[0]
        self.assertEqual(sale["acquired_date"], "2024-01-02")
        self.assertEqual(sale["term"], "LONG_TERM")
        self.assertAlmostEqual(sale["gain"], 10.0)
        self.assertNotIn("unapplied basis adjustment", res["_stderr"])

    @rule("US-CORP-07")
    def test_positive_basis_control(self):
        res = _gains(self._book(99.0, 0.2))
        sale = _sales(res, symbol="ZQC.US")[0]
        self.assertEqual(sale["acquired_date"], "2024-01-02")
        self.assertAlmostEqual(sale["gain"], 9.8)

    @rule("US-CORP-07")
    def test_spin_off_with_no_parent_lots_is_unchanged(self):
        """No parent shares in the books: the shares are a lot of the
        spin date, with the warning, and the $0 parent row adds none."""
        book = [r for r in self._book(100.0, 0)
                if not (r.symbol == "ZQP.US" and r.action != "ADJUST")
                and r.type != "roc"]
        res = _gains(book)
        sale = _sales(res, symbol="ZQC.US")[0]
        self.assertEqual(sale["acquired_date"], "2025-06-03")
        self.assertIn("finds no long ZQP.US lots", res["_stderr"])
        self.assertNotIn("unapplied basis adjustment", res["_stderr"])


# ---------------------------------------------------------------- TJ-0730
class TestSecurityFutureIsNotSection1256(unittest.TestCase):
    """A single-stock future (root ZQF) opened 2026-08-03 at 100 and
    closed 2026-08-10 at 101 (x100): with `SECFUT ZQF` in the ticker.map
    it is no §1256 contract: a short-term gain of 100 on Form 8949, out
    of the Form 6781 list and inside the wash-sale rule. Without the
    line every future stays §1256."""

    def _with_map(self, text):
        import tempfile
        from pathlib import Path
        from taxjson.lib import markets
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tm = Path(td.name) / "ticker.map"
        tm.write_text(text)
        markets.use_ticker_map(tm)
        self.addCleanup(markets.use_ticker_map, None)

    def _form(self, legs):
        from taxjson.bin.taxjson_form_export import build_8949
        from taxjson.lib.futures import settle_futures
        book = [tx("BUYSELL", d, "F:ZQFZ6.US", q, p * 100, price=p,
                   multiplier=100)
                for d, (q, p) in zip(("2026-08-03", "2026-08-10"), legs)]
        book = settle_futures(book, "fifo")[0]
        res = _gains(book, year=2026)
        ents = [t for t in res["transactions"] if t.get("qty")]
        return ents, build_8949(ents, 2026)

    @rule("US-FUT-02")
    def test_declared_security_future_goes_on_form_8949(self):
        from taxjson.lib.core import outside_1091
        from taxjson.lib.futures import section_1256_kind
        self._with_map("SECFUT F:ZQF\n")
        self.assertEqual(section_1256_kind("F:ZQFZ6.US"), "")
        self.assertFalse(outside_1091("F:ZQFZ6.US"))
        self.assertEqual(section_1256_kind("F:ZQGZ6.US"), "future")
        ents, f = self._form([(1, 100), (-1, 101)])
        self.assertEqual(ents[0]["term"], "SHORT_TERM")
        self.assertEqual(f["section_1256"], [])
        row = f["part_I"][0]
        self.assertEqual((row["proceeds"], row["cost"], row["gain"]),
                         (100.0, 0.0, 100.0))
        _ents, f = self._form([(1, 100), (-1, 99)])
        row = f["part_I"][0]
        self.assertEqual((row["proceeds"], row["cost"], row["gain"]),
                         (0.0, 100.0, -100.0))
        _ents, f = self._form([(-1, 100), (1, 99)])
        row = f["part_I"][0]
        self.assertEqual((row["proceeds"], row["cost"], row["gain"]),
                         (100.0, 0.0, 100.0))

    @rule("US-FUT-02")
    def test_without_the_line_every_future_is_section_1256(self):
        from taxjson.lib.core import outside_1091
        self._with_map("")
        self.assertTrue(outside_1091("F:ZQFZ6.US"))
        _ents, f = self._form([(1, 100), (-1, 101)])
        self.assertEqual(f["part_I"], [])
        self.assertEqual([r["kind"] for r in f["section_1256"]],
                         ["future"])

    def test_ticker_map_line(self):
        from taxjson.lib.ticker_map import parse_side_line
        self.assertEqual(parse_side_line("SECFUT", "SECFUT /zqf"),
                         ("secfut", "ZQF", True))
        self.assertEqual(parse_side_line("SECFUT", "SECFUT ZQF NO"),
                         ("secfut", "ZQF", False))
        with self.assertRaises(ValueError):
            parse_side_line("SECFUT", "SECFUT ZQF MAYBE")


# ---------------------------------------------------------------- TJ-0728
class TestDividendCompensationPaidOnAShort(unittest.TestCase):
    """100 shares shorted 2025-01-06 for 1000; 20 of dividend
    compensation paid 2025-01-15; covered for 900. Covered 2025-01-21
    (within 45 days) the payment is the cover's cost: gain 80, no paid
    compensation left. Covered 2025-03-03 it stays paid compensation
    (gain 100), and the estimate on the standard deduction does not
    subtract it."""

    def _book(self, cover):
        return [tx("BUYSELL", "2025-01-06", "ZQK.US", -100, 1000.0),
                tx("DIVIDEND_IN_LIEU", "2025-01-15", "ZQK.US", 100, -20.0,
                   type="dividend_in_lieu", gross_amount=-20.0),
                tx("BUYSELL", cover, "ZQK.US", 100, 900.0)]

    @rule("US-INC-04")
    def test_short_closed_within_45_days_capitalizes(self):
        res = _gains(self._book("2025-01-21"))
        cover = _sales(res, direction="SHORT")[0]
        self.assertAlmostEqual(cover["gain"], 80.0)
        pil = [t for t in res["transactions"]
               if t.get("action") == "DIVIDEND_IN_LIEU"][0]
        self.assertAlmostEqual(pil["pil"], 0.0)
        self.assertAlmostEqual(pil["capitalized_263h"], 20.0)
        from taxjson.bin.taxjson_form_export import build_8949
        row = build_8949(_sales(res, direction="SHORT"),
                         2025)["part_I"][0]
        self.assertEqual((row["proceeds"], row["cost"], row["gain"]),
                         (1000.0, 920.0, 80.0))

    @rule("US-INC-04")
    def test_short_held_longer_keeps_the_payment(self):
        res = _gains(self._book("2025-03-03"))
        cover = _sales(res, direction="SHORT")[0]
        self.assertAlmostEqual(cover["gain"], 100.0)
        pil = [t for t in res["transactions"]
               if t.get("action") == "DIVIDEND_IN_LIEU"][0]
        self.assertAlmostEqual(pil["pil"], -20.0)
        self.assertNotIn("capitalized_263h", pil)

    def test_a_negative_row_with_no_short_is_left_alone(self):
        """A reversal of a payment received on long shares."""
        book = [tx("BUYSELL", "2025-01-06", "ZQK.US", 100, 1000.0),
                tx("DIVIDEND_IN_LIEU", "2025-01-15", "ZQK.US", 100, -20.0,
                   type="dividend_in_lieu", gross_amount=-20.0)]
        res = _gains(book)
        pil = [t for t in res["transactions"]
               if t.get("action") == "DIVIDEND_IN_LIEU"][0]
        self.assertAlmostEqual(pil["pil"], -20.0)

    @rule("US-INC-04")
    def test_estimate_does_not_subtract_paid_compensation(self):
        import json
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        from taxjson.lib.dispatch import python_module_argv
        src = Path(__file__).resolve().parent.parent / "src"

        def estimate(paid):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "taxjson.toml").write_text(
                    "[settings]\ncountry = \"usa\"\nyear = 2025\n"
                    "base_currency = \"USD\"\nsource_currencies = []\n"
                    "local_timezone = \"America/New_York\"\n\n"
                    "[accounts.a]\ntype = \"taxable\"\n\n"
                    "[estimate]\nother_income = 100000\n")
                (root / "ticker.map").write_text("")
                d = root / "inputs" / "a"
                d.mkdir(parents=True)
                (d / "book.tt").write_text(
                    "BUYSELL 2025-01-06 10:00:00 ZQK.US -100 USD 10 1000 0\n"
                    "BUYSELL 2025-03-03 10:00:00 ZQK.US 100 USD 9 900 0\n")
                if paid:
                    (d / "book.csv").write_text(
                        "Statement,Header,Field Name,Field Value\n"
                        "Statement,Data,BrokerName,Interactive Brokers LLC\n"
                        "Dividends,Header,Currency,Account,Date,"
                        "Description,Amount\n"
                        "Dividends,Data,USD,U1234567,2025-01-15,"
                        "ZQK(US9990000001) Payment in Lieu of Dividend "
                        "USD 0.2 per Share,-20\n")
                env = dict(os.environ, PYTHONPATH=str(src),
                           TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
                argv = python_module_argv("taxjson.bin.taxjson_run")
                for args in (("run", "--no-input"),
                             ("estimate", "--json")):
                    r = subprocess.run(argv + ["-C", str(root), *args],
                                       capture_output=True, text=True,
                                       env=env, stdin=subprocess.DEVNULL,
                                       timeout=600)
                    self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                return json.loads(r.stdout)["estimate"]
        with_paid, without = estimate(True), estimate(False)
        self.assertEqual(with_paid["estimated_tax"],
                         without["estimated_tax"])
        self.assertEqual(with_paid["pil_paid_not_deducted"], 20.0)
        self.assertTrue(any("Schedule A" in n for n in with_paid["notes"]))


if __name__ == "__main__":
    unittest.main()
