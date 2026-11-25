"""`tjs gains-cra`'s FX and its ACCOUNT view: the rate of a sale is the
rate table's on its settlement date (bin/taxjson_convert_currency
`rate_date`, as the conversion read it), the day of the rate used beside
it; the native × FX check allows a cent on a rate-table row and what four
places allow on a weighted one; `tjs gains-cra ACCOUNT` is that account's
rows and `tjs sum ACCOUNT`'s totals (src/taxjson/cli/gains_filing.py
`_gains_cra_native`, `cmd_gains_cra`; src/taxjson/lib/gains_cra.py
`_fx_cells`, `report`). `tjs gains-base` shows each sale's same cents.

One project, a trade-date basis under close timing, its Bank of Canada
rates on weekdays only and different every day: a Questrade CSV whose
sales settle two days after the trade (one on December 31, settling in
January), a Saturday sale, a EUR sale, two sales whose CAD proceeds sit on
a half cent, an exercised put (its proceeds of two days' rates), a short
sale opened twice, written calls bought back, a written put that expired
for a net debit, a future closed at a loss; a second account with a sale
of no purchase.
"""
import datetime
import unittest
from decimal import Decimal

from _planted_rates import plant
from _support.filing_mixed_currency_project import Project, cents

from taxjson.model import money as M

TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = ["USD", "EUR"]\n'
        'tax_date = "trade"\noption_premium_timing = "close"\n'
        '[accounts.qt]\ntype = "taxable"\n[accounts.margin]\ntype = "taxable"\n'
        '[accounts.m2]\ntype = "taxable"\n')

# Two days' rates set where a sale's CAD proceeds land on a half cent.
_SET = {"2025-06-10": Decimal("1.4911"), "2025-07-10": Decimal("1.3340")}


def usd(day: str) -> Decimal:
    """The planted USD/CAD rate of a weekday: 0.0007 more each day of the year."""
    d = datetime.date.fromisoformat(day)
    return _SET.get(day, Decimal("1.3000") + d.timetuple().tm_yday * Decimal("0.0007"))


def eur(day: str) -> Decimal:
    d = datetime.date.fromisoformat(day)
    return Decimal("1.5000") + d.timetuple().tm_yday * Decimal("0.0003")


def _rates() -> str:
    day, out = datetime.date(2024, 12, 20), []
    while day <= datetime.date(2026, 1, 10):
        if day.weekday() < 5:
            out.append(f"{day.isoformat()} 12:00:00 USD CAD {usd(day.isoformat()):.4f} boc\n")
            out.append(f"{day.isoformat()} 12:00:00 EUR CAD {eur(day.isoformat()):.4f} boc\n")
        day += datetime.timedelta(days=1)
    return "".join(out)


QUESTRADE = (
    "Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,Price,Gross Amount,"
    "Commission,Net Amount,Currency,Activity Type,Account #,Account Type\n"
    "2025-01-15,2025-01-16,Buy,QZQT,QUARTZ QT INC,100,20.00,-2000.00,-1.00,-2001.00,USD,"
    "Trades,99900099,Margin\n"
    "2025-03-10,2025-03-12,Sell,QZQT,QUARTZ QT INC,-40,25.00,1000.00,-1.00,999.00,USD,"
    "Trades,99900099,Margin\n"
    "2025-12-31,2026-01-02,Sell,QZQT,QUARTZ QT INC,-10,30.00,300.00,-1.00,299.00,USD,"
    "Trades,99900099,Margin\n")

MARGIN_TT = """\
BUYSELL 2025-01-09 10:00:00 QZEU.US 10 EUR 20.00 201.00 1.00
BUYSELL 2025-04-02 10:00:00 QZEU.US -10 EUR 23.00 229.00 1.00
BUYSELL 2025-01-06 10:00:00 QZWK.US 10 USD 10.00 101.00 1.00
BUYSELL 2025-05-10 10:00:00 QZWK.US -10 USD 12.00 119.00 1.00
BUYSELL 2025-01-06 10:00:00 QZTI.US 50 USD 10.00 501.00 1.00
BUYSELL 2025-06-10 10:00:00 QZTI.US -50 USD 11.00 549.00 1.00
BUYSELL 2025-01-06 10:00:00 QZTJ.US 50 USD 10.00 500.00 0
BUYSELL 2025-07-10 10:00:00 QZTJ.US -50 USD 14.25 712.50 0
BUYSELL 2025-06-02 10:00:00 QZRX251219C00030000.US -4 USD 1.50 597.40 2.60
BUYSELL 2025-08-04 10:00:00 QZRX251219C00030000.US 1 USD 0.50 50.65 0.65
BUYSELL 2025-09-04 10:00:00 QZRX251219C00030000.US 3 USD 0.60 181.95 1.95
BUYSELL 2025-03-03 10:00:00 QZND250417P00010000.US -1 USD 0.01 -0.30 1.30
BUYSELL 2025-04-17 16:00:00 QZND250417P00010000.US 1 USD 0 0 0
BUYSELL 2025-07-01 10:00:00 QZSH.US -100 USD 30.00 2999.00 1.00
BUYSELL 2025-07-08 10:00:00 QZSH.US -200 USD 31.00 6199.00 1.00
BUYSELL 2025-09-02 10:00:00 QZSH.US 120 USD 25.00 3001.00 1.00
BUYSELL 2025-09-09 10:00:00 QZSH.US 180 USD 26.00 4681.00 1.00
BUYSELL 2025-01-13 10:00:00 QZLP.US 100 USD 4.00 401.00 1.00
BUYSELL 2025-02-04 10:00:00 QZLP250620P00005000.US 1 USD 0.70 71.00 1.00
ASSIGN 2025-06-20 16:00:00 QZLP250620P00005000.US -1 USD 0 0
ASSIGN 2025-06-20 16:00:01 QZLP.US -100 USD 5 500
BUYSELL 2025-08-11 10:00:00 F:QZM26.US 1 USD 305 30500 0 x100
BUYSELL 2025-09-03 10:00:00 F:QZM26.US -1 USD 290 29000 0 x100
"""

M2_TT = """\
BUYSELL 2025-01-06 10:00:00 QZTT.US 50 USD 11.00 551.00 1.00
BUYSELL 2025-03-12 10:00:00 QZTT.US -50 USD 13.00 649.00 1.00
BUYSELL 2025-11-03 10:00:00 QZNP.US -5 USD 13.00 64.00 1.00
"""

CELLS = ("proceeds", "acb", "outlays", "gain")
MISMATCH = "not the Proceeds"


def _one(doc, symbol, date=None):
    rows = [r for r in doc["rows"] if r["symbol"] == symbol and date in (None, r["date"])]
    assert len(rows) == 1, (symbol, date, rows)
    return rows[0]


def _within(test, r, allow):
    """native × FX is the CAD proceeds within `allow`."""
    gap = abs(M.of(r["native_proceeds"]) * M.of(r["fx_rate"]) - M.of(r["proceeds"]))
    test.assertLessEqual(gap, allow, r["symbol"])


class TestTheRateDates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project(files={"margin": MARGIN_TT, "m2": M2_TT}, toml=TOML, planted=False)
        (cls.p.root / "inputs" / "qt").mkdir(parents=True)
        (cls.p.root / "inputs" / "qt" / "questrade.csv").write_text(QUESTRADE, encoding="utf-8")
        plant(cls.p.root / "work" / "to_base.csv", _rates(), cls.p.home)
        cls.p.run()
        cls.doc = cls.p.json("gains-cra")
        cls.sum = cls.p.json("sum")

    def test_a_broker_sale_converts_on_its_settlement_date(self):
        r = _one(self.doc, "QZQT.US", "2025-03-10")
        self.assertEqual(r["date_settle"], "2025-03-12")
        self.assertNotEqual(usd("2025-03-10"), usd("2025-03-12"))
        self.assertEqual(M.of(r["fx_rate"]), usd("2025-03-12"))
        self.assertEqual((r["fx_source"], r["fx_date"], r["fx_rate_date"]),
                         ("rate_table", "2025-03-12", "2025-03-12"))
        self.assertEqual(cents(r["native_proceeds"]), Decimal("1000.00"))
        self.assertEqual(cents(r["proceeds"]), M.present(Decimal(1000) * usd("2025-03-12"), 2))
        self.assertEqual(r["notes"], [])

    def test_a_year_end_sale_on_a_trade_date_basis(self):
        r = _one(self.doc, "QZQT.US", "2025-12-31")
        self.assertEqual((r["date_settle"], r["fx_rate_date"], r["fx_date"]),
                         ("2026-01-02", "2026-01-02", "2026-01-02"))
        self.assertEqual(M.of(r["fx_rate"]), usd("2026-01-02"))
        self.assertEqual(cents(r["proceeds"]), M.present(Decimal(300) * usd("2026-01-02"), 2))

    def test_a_weekend_sale_takes_the_rate_before_it(self):
        r = _one(self.doc, "QZWK.US")
        self.assertEqual(datetime.date.fromisoformat(r["date"]).weekday(), 5)
        self.assertEqual((r["fx_rate_date"], r["fx_date"]), ("2025-05-10", "2025-05-09"))
        self.assertEqual(M.of(r["fx_rate"]), usd("2025-05-09"))
        _within(self, r, Decimal("0.01"))

    def test_a_second_currency(self):
        self.assertEqual([t["currency"] for t in self.doc["tables"]], ["EUR", "USD"])
        r = _one(self.doc, "QZEU.US")
        self.assertEqual((r["currency"], r["fx_source"]), ("EUR", "rate_table"))
        self.assertEqual(M.of(r["fx_rate"]), eur("2025-04-02"))
        _within(self, r, Decimal("0.01"))
        self.assertEqual(r["notes"], [])

    def test_a_half_cent_is_no_mismatch(self):
        for sym in ("QZTI.US", "QZTJ.US"):
            r = _one(self.doc, sym)
            with self.subTest(symbol=sym):
                self.assertEqual(M.of(r["fx_rate"]), _SET[r["date"]])
                _within(self, r, Decimal("0.01"))
                self.assertFalse([n for n in r["notes"] if MISMATCH in n], r["notes"])

    def test_weighted_rows_are_checked_at_four_places(self):
        weighted = [r for r in self.doc["rows"]
                    if r["fx_source"] == "weighted" and r["native_proceeds"]]
        self.assertEqual({r["symbol"] for r in weighted},
                         {"QZRX251219C00030000.US", "QZSH.US"})
        for r in weighted:
            with self.subTest(symbol=r["symbol"], date=r["date"]):
                self.assertEqual((r["fx_date"], r["fx_rate_date"]), (None, None))
                _within(self, r, abs(M.of(r["native_proceeds"])) * Decimal("0.00005")
                        + Decimal("0.01"))
                self.assertFalse([n for n in r["notes"] if MISMATCH in n], r["notes"])
                self.assertTrue(any("proceeds-weighted" in n for n in r["notes"]))

    def test_a_sale_of_two_days_rates_keeps_its_note(self):
        r = _one(self.doc, "QZLP.US")
        self.assertTrue([n for n in r["notes"] if MISMATCH in n], r["notes"])

    def test_a_future_s_loss_and_a_net_debit_write(self):
        fut = _one(self.doc, "F:QZM26.US")
        self.assertEqual((cents(fut["proceeds"]), cents(fut["native_proceeds"])),
                         (Decimal("0.00"), Decimal("0.00")))
        self.assertEqual(cents(fut["acb"]), -cents(fut["gain"]))
        self.assertEqual(fut["fx_source"], "weighted")
        debit = _one(self.doc, "QZND250417P00010000.US")
        self.assertEqual((cents(debit["proceeds"]), debit["fx_rate"]), (Decimal("0.00"), None))
        self.assertGreater(debit["outlays"], 0)
        self.assertEqual(cents(debit["gain"]), -cents(debit["outlays"]))
        self.assertTrue(any(n.startswith("no FX: nothing was received") for n in debit["notes"]))

    def test_the_totals_are_sum_s(self):
        self.assertIsNone(self.doc["scope"])
        for cell in CELLS + ("denied",):
            self.assertEqual(cents(self.doc["totals"][cell]),
                             cents(self.sum["filing"]["totals"][cell]), cell)

    def test_one_account(self):
        for acct in ("qt", "m2"):
            with self.subTest(account=acct):
                doc, sm = self.p.json("gains-cra", acct), self.p.json("sum", acct)
                self.assertEqual(doc["scope"], {"account": acct})
                self.assertEqual(doc["rows"], [r for r in self.doc["rows"]
                                               if r["account"] == acct])
                for cell in CELLS + ("denied",):
                    self.assertEqual(cents(doc["totals"][cell]),
                                     cents(sm["filing"]["totals"][cell]), cell)
                text = self.p.tjs("gains-cra", acct).stdout
                self.assertIn(f"tax year 2025, account {acct}, Schedule 3's cells", text)
                self.assertIn(f"FOR ACCOUNT {acct} — Schedule 3, as tjs sum {acct} shows it "
                              f"(not the whole return)", text)
                self.assertNotIn("FOR THE RETURN", text)

    def test_one_account_counts_its_own_sales_of_no_purchase(self):
        def kinds(doc):
            return [i["kind"] for i in doc["readiness"]["issues"]]
        self.assertIn("no-purchase", kinds(self.doc))
        self.assertIn("no-purchase", kinds(self.p.json("gains-cra", "m2")))
        self.assertNotIn("no-purchase", kinds(self.p.json("gains-cra", "qt")))
        self.assertIn("sale with no purchase", self.p.tjs("gains-cra", "m2").stdout)
        self.assertNotIn("no purchase", self.p.tjs("gains-cra", "qt").stdout)

    def test_gains_base_shows_each_sale_s_same_cents(self):
        gb = self.p.json("gains-base")
        by_id = {r["id"]: r for r in gb["rows"]}
        for r in self.doc["rows"]:
            b = by_id[r["id"]]
            with self.subTest(symbol=r["symbol"], date=r["date"]):
                self.assertEqual(cents(b["proceeds"]), cents(r["proceeds"]))
                self.assertEqual(cents(b["outlays"]), cents(r["outlays"]))
                self.assertEqual(cents(b["allowed"]), cents(r["gain"]))
                self.assertEqual(cents(b["cost"]), cents(r["acb"]) + cents(r["denied"]))


if __name__ == "__main__":
    unittest.main()
