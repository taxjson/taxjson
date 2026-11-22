"""Generic importer and text-tool fixes of 2026-10-29, round 7 wave D
(group generic).

TJ-1034 (a ticker.map MULT line read as the premium multiplier of an
adjusted series), TJ-1035 (a Dec-31 $0 expiry whose commission is only
in the net amount), TJ-1077 (a buy-to-close $0 expiry refused as a
swapped fee/amount), TJ-1121 (a fee-only zero-price close needing
allow_large_fees), TJ-1100 (a tab table's footer name no longer blanked),
TJ-1101 (a folder path right after a colon in a settings comment).

Every fixture is SYNTHETIC: made-up tickers (SYN, QZX), invented amounts.
Each test's files live in a folder of their own.

Formerly: test_fix_1029_r7d_generic
"""
import unittest

from tax_rules import rule
from taxjson.rules import ids as RID

from _support.generic_gains import _gains, _mapping, _parse

_AMOUNT = 'amount = "Amount"'
_AMOUNT_FEE = 'amount = "Amount"\nfee = "Fee"'
_CHARGED = 'fee_sign = "charged"'
_LARGE = "[options]\nallow_large_fees = true"


class TestMultIsNotThePremiumSize(unittest.TestCase):
    """TJ-1034: MULT sizes the deliverable. The cash is qty x price x 100,
    or the size the row's own amount shows. TJ-0863's mini (MULT 10) keeps
    its tests in features.generic_import.test_generic_rows_and_small_import_tools.

    Formerly: test_fix_1029_r7d_generic.TestMultIsNotThePremiumSize"""

    SYM = "QZX1251219C00005000.TO"
    TM = "MULT QZX1 50\n"

    def test_adjusted_series_with_its_real_cash_is_accepted(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,2,200,CAD\n"
                    f"2025-02-06,S,{self.SYM},-1,3,300,CAD\n")
        rows, _err = _parse(csv_text, _mapping(_AMOUNT), ticker_map=self.TM)
        self.assertEqual([r["net_amount"] for r in rows], [200.0, 300.0])
        self.assertEqual([r["gross_amount"] for r in rows], [200.0, 300.0])
        self.assertEqual([r.get("fee") for r in rows], [0.0, 0.0])
        # Its cash is not at 50: the row states no size against the MULT
        # line, which still sizes the deliverable.
        self.assertNotIn("multiplier", rows[0])
        g = _gains(rows, 2025, ticker_map=self.TM)
        self.assertAlmostEqual(g["summary"]["total_gain"], 100.0)

    def test_adjusted_series_price_only_is_at_100(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,2,CAD\n"
                    f"2025-02-06,S,{self.SYM},-1,3,CAD\n")
        rows, _err = _parse(csv_text, _mapping(), ticker_map=self.TM)
        self.assertEqual([r["net_amount"] for r in rows], [200.0, 300.0])
        g = _gains(rows, 2025, ticker_map=self.TM)
        self.assertAlmostEqual(g["summary"]["total_gain"], 100.0)

    def test_a_row_whose_amount_is_at_the_declared_size_is_taken_at_it(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,2,100,CAD\n")
        rows, _err = _parse(csv_text, _mapping(_AMOUNT), ticker_map=self.TM)
        self.assertEqual(rows[0]["net_amount"], 100.0)
        self.assertEqual(rows[0]["gross_amount"], 100.0)
        # The row check compares its money at that size.
        self.assertEqual(rows[0]["multiplier"], 50.0)
        self.assertEqual(rows[0]["contract_size_basis"], "declared")

    def test_an_amount_fitting_no_size_is_still_refused(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,2,77,CAD\n")
        with self.assertRaisesRegex(ValueError, "differs from qty x price"):
            _parse(csv_text, _mapping(_AMOUNT), ticker_map=self.TM)
        # Not read as an undeclared mini (x10 fits): MULT says otherwise.
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,2,20,CAD\n")
        with self.assertRaisesRegex(ValueError, "differs from qty x price"):
            _parse(csv_text, _mapping(_AMOUNT), ticker_map=self.TM)

    def test_a_penny_price_prefers_the_premium_100(self):
        # 0.05 x 100 = 5 exactly; x 50 would read 2.50 of it as a fee.
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,0.05,5,CAD\n")
        rows, _err = _parse(csv_text, _mapping(_AMOUNT), ticker_map=self.TM)
        self.assertEqual(rows[0]["fee"], 0.0)

    def test_a_declared_mini_whose_row_shows_100_follows_the_row(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,2,200,CAD\n")
        rows, _err = _parse(csv_text, _mapping(_AMOUNT),
                            ticker_map=f"MULT {self.SYM} 10\n")
        self.assertEqual(rows[0]["net_amount"], 200.0)
        self.assertEqual(rows[0]["gross_amount"], 200.0)

    def test_a_declared_mini_with_a_commission_stays_a_mini(self):
        # 5 fits x 100 exactly and x 10 with a commission: MULT says x 10.
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,0.05,5,CAD\n")
        rows, _err = _parse(
            csv_text, _mapping(_AMOUNT, options=_LARGE),
            ticker_map=f"MULT {self.SYM} 10\n")
        self.assertEqual(rows[0]["gross_amount"], 0.5)
        self.assertEqual(rows[0]["fee"], 4.5)


class TestExpiryCommissionInTheNet(unittest.TestCase):
    """TJ-1035: no fee column, a zero-price close on the option's expiry
    day whose amount is a charge: dated and settled the expiry day, and
    the charge is a cost, not proceeds.

    Formerly: test_fix_1029_r7d_generic.TestExpiryCommissionInTheNet"""

    SYM = "SYN251231P00100000.TO"

    def _rows(self, amount="-1", day="2025-12-31", options=_LARGE):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,1,100,CAD\n"
                    f"{day},S,{self.SYM},-1,0,{amount},CAD\n")
        return _parse(csv_text, _mapping(_AMOUNT, options=options))[0]

    @rule(RID.CA_DATE_08)
    def test_the_loss_is_in_the_expiry_year(self):
        rows = self._rows()
        self.assertEqual(rows[1]["date"], "2025-12-31")
        self.assertEqual(rows[1]["date_settle"], "2025-12-31")
        self.assertEqual(rows[1]["net_amount"], -1.0)
        self.assertEqual(rows[1]["fee"], 1.0)
        self.assertAlmostEqual(_gains(rows, 2025)["summary"]["total_gain"],
                               -101.0)
        self.assertAlmostEqual(_gains(rows, 2026)["summary"]["total_gain"],
                               0.0)

    def test_needs_no_large_fee_opt_in(self):
        rows = self._rows(options="")
        self.assertEqual(rows[1]["net_amount"], -1.0)

    def test_posted_just_after_the_expiry_is_clamped_to_it(self):
        rows = self._rows(day="2026-01-02")
        self.assertEqual(rows[1]["date"], "2025-12-31")
        self.assertEqual(rows[1]["date_settle"], "2025-12-31")
        self.assertEqual(rows[1]["net_amount"], -1.0)

    def test_agrees_with_the_fee_column_form(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,1,100,0,CAD\n"
                    f"2025-12-31,S,{self.SYM},-1,0,-1,1,CAD\n")
        with_fee = _parse(csv_text, _mapping(_AMOUNT_FEE, _CHARGED))[0]
        self.assertEqual(
            {k: with_fee[1][k] for k in ("date", "date_settle",
                                         "net_amount", "fee")},
            {k: self._rows()[1][k] for k in ("date", "date_settle",
                                             "net_amount", "fee")})

    def test_a_written_options_buy_to_close(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,S,{self.SYM},-1,1,100,CAD\n"
                    f"2025-12-31,B,{self.SYM},1,0,-1,CAD\n")
        rows = _parse(csv_text, _mapping(_AMOUNT))[0]
        self.assertEqual(rows[1]["date_settle"], "2025-12-31")
        self.assertEqual(rows[1]["net_amount"], 1.0)

    def test_cash_received_on_a_sale_stays_proceeds(self):
        # A positive amount on a zero-price sale may be cash received (a
        # cash settlement): nothing shows it is a charge.
        rows = self._rows(amount="1")
        self.assertEqual(rows[1]["net_amount"], 1.0)
        self.assertEqual(rows[1]["fee"], 0.0)

    def test_more_than_a_commission_is_not_read_as_one(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,S,{self.SYM},-1,5,500,CAD\n"
                    f"2025-12-31,B,{self.SYM},1,0,-400,CAD\n")
        rows = _parse(csv_text, _mapping(_AMOUNT))[0]
        self.assertEqual(rows[1]["fee"], 0.0)
        self.assertEqual(rows[1]["net_amount"], 400.0)

    def test_before_the_expiry_is_an_ordinary_row(self):
        rows = self._rows(day="2025-12-15")
        self.assertEqual(rows[1]["fee"], 0.0)
        self.assertNotEqual(rows[1]["date_settle"], "2025-12-15")

    def test_a_stated_fee_of_zero_is_believed(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,1,100,0,CAD\n"
                    f"2025-12-31,S,{self.SYM},-1,0,-1,0,CAD\n")
        rows = _parse(csv_text, _mapping(_AMOUNT_FEE, _CHARGED))[0]
        self.assertEqual(rows[1]["fee"], 0.0)

    def test_an_unsigned_quantity_paying_its_charge_is_accepted(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,1,100,CAD\n"
                    f"2025-12-31,S,{self.SYM},1,0,-1,CAD\n")
        rows = _parse(csv_text, _mapping(_AMOUNT))[0]
        self.assertEqual(rows[1]["net_amount"], -1.0)
        self.assertEqual(rows[1]["quantity"], -1.0)

    def test_a_fee_sign_cash_file_with_no_fee_column(self):
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                    f"2025-01-06,B,{self.SYM},1,1,100,CAD\n"
                    f"2025-12-31,S,{self.SYM},-1,0,-1,CAD\n")
        rows = _parse(csv_text, _mapping(_AMOUNT, 'fee_sign = "cash"'))[0]
        self.assertEqual(rows[1]["fee"], 1.0)
        self.assertEqual(rows[1]["net_amount"], -1.0)


class TestStatedFeeOnlyCloseAgreesWithBlank(unittest.TestCase):
    """TJ-1077, TJ-1121: at a stated zero price, an amount equal to the
    fee says what a blank amount says.

    Formerly: test_fix_1029_r7d_generic.TestStatedFeeOnlyCloseAgreesWithBlank"""

    SYM = "SYN251231P00100000.TO"

    def _close(self, target, amount, opening="S", options=_LARGE,
               sym=None, fee="1", price="0"):
        oprice = "100" if sym else "1"
        sym = sym or self.SYM
        qty, oqty = ("1", "-1") if target == "B" else ("-1", "1")
        csv_text = ("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                    f"2025-01-06,{opening},{sym},{oqty},{oprice},100,0,CAD\n"
                    f"2025-12-31,{target},{sym},{qty},{price},{amount},{fee},"
                    "CAD\n")
        return _parse(csv_text, _mapping(_AMOUNT_FEE, _CHARGED, options))[0]

    def test_buy_to_close_any_amount_form(self):
        blank = self._close("B", "")[1]
        self.assertEqual(blank["net_amount"], 1.0)
        for amount in ("1", "-1"):
            with self.subTest(amount=amount):
                row = self._close("B", amount)[1]
                self.assertEqual(row["net_amount"], 1.0)
                self.assertEqual(row["date_settle"], "2025-12-31")
                self.assertEqual(row["fee"], 1.0)
                self.assertEqual(row["date_settle"], blank["date_settle"])

    def test_no_large_fee_opt_in_needed(self):
        row = self._close("B", "1", options="")[1]
        self.assertEqual(row["net_amount"], 1.0)
        row = self._close("S", "-1", opening="B", options="")[1]
        self.assertEqual(row["net_amount"], -1.0)
        self.assertEqual(row["date_settle"], "2025-12-31")

    def test_a_worthless_share_sale_needs_no_opt_in(self):
        rows = self._close("S", "-1", opening="B", options="", sym="SYN.TO")
        self.assertEqual(rows[1]["net_amount"], -1.0)
        # (A share's sale settles T+1 whatever its price: in 2026.)
        self.assertAlmostEqual(_gains(rows, 2026)["summary"]["total_gain"],
                               -101.0)

    def test_the_swapped_mapping_is_still_refused(self):
        # A fee column above the amount: the commission booked as the cost.
        with self.assertRaisesRegex(ValueError, "not less than the buy"):
            self._close("B", "10", fee="1000")
        # The price not stated (blank): nothing says the fee is the cash.
        with self.assertRaisesRegex(ValueError, "not less than the buy"):
            self._close("B", "1", price="")

    def test_a_large_fee_under_a_real_amount_still_needs_the_opt_in(self):
        with self.assertRaisesRegex(ValueError, "allow_large_fees"):
            self._close("S", "10", opening="B", options="")


if __name__ == "__main__":
    unittest.main()
