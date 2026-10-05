"""Re-audit-2 tests-pins-01: settings the run wrapper forwards to its
tools, pinned end to end (each test fails when the forwarding is
dropped).

- [settings] corporate_distributions (income_dating_flags) through the
  gains stage, the raw books, the .sum income section, the blended pass,
  list --date, carryover, audit, wash-sales --explain, close-year /
  check-filed and handoff (A2-0532, A2-0539);
- [settings] futures_settle = "next_day" to the IB parser (A2-0531);
- the parser's --country / --foreign-roc (A2-0536, INPUTS-03), t1135's
  --sheltered context (A2-0536, S008-07) and the [estimate] deductions
  instalments builds on (A2-0536, R1-213).

Synthetic data only (a fake Questrade account number).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(root, *args):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


_QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
       'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
       'Account #,Activity Type,Account Type\n')


def _q(td, sd=None, action="Buy", desc="XYZ CORP WE ACTED AS AGENT",
       qty="0", price="0", gross="0", net="0", act="Trades"):
    dt = lambda x: f"{x} 12:00:00 AM"  # noqa: E731
    return ",".join([dt(td), dt(sd or td), action, "XYZ.TO", desc, qty,
                     price, gross, "0", net, "CAD", "55500001",  # pii-ok
                     act, "Individual margin"]) + "\n"


def _corp_csv():
    # 100 XYZ.TO bought at 10, sold Dec 30 at 8; a 300 return of capital
    # with record date Dec 20 paid Jan 15. XYZ.TO is a corporation
    # ([settings] corporate_distributions): its ROC lowers the ACB when
    # PAID (CA-INC-DATE-ROC), after the sale — 2025 loses 200 and the
    # ROC on the empty pool is a 300 gain in 2026. Without the setting
    # the record date applies (a trust's ROC): 2025 gains 100.
    return (_QH + _q("2025-03-03", qty="100", price="10", gross="-1000",
                     net="-1000")
            + _q("2025-12-30", "2025-12-31", action="Sell", qty="-100",
                 price="8", gross="800", net="800")
            + _q("2026-01-15", action="DIV",
                 desc="XYZ CORP RETURN OF CAPITAL ON 100 SHS REC 12/20/25 "
                      "PAY 01/15/26", net="300.00", act="Dividends"))


def _corp_project(root, year=2025, extra="", csv=None):
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "questrade_2025.csv").write_text(
        csv or _corp_csv())
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\n'
        f'base_currency = "CAD"\nsource_currencies = []\n'
        f'corporate_distributions = ["XYZ.TO"]\n{extra}'
        f'[accounts.margin]\ntype = "taxable"\n')


@rule("CA-INC-DATE-ROC")
class TestCorporateDistributionsReachEveryConsumer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        _corp_project(cls.root)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _sale_gains(self, name):
        d = json.loads((self.root / "work" / name).read_text())
        return [(t["date"], t["gain"]) for t in d["transactions"]
                if t.get("gain") is not None]

    def test_gains_stage(self):
        self.assertEqual(self._sale_gains("margin_gains.json")[0],
                         ("2025-12-30", -200.0))
        txt = (self.root / "reports" / "margin.sum").read_text()
        self.assertRegex(txt, r"TOTAL REALIZED GAIN:\s+-200\.00 CAD")

    def test_raw_books(self):
        for name in ("margin_raw_gains.json", "margin_raw_base_gains.json"):
            self.assertEqual(self._sale_gains(name),
                             [("2025-12-30", -200.0), ("2026-01-15", 300.0)],
                             name)

    def test_blended_pass_and_schedule_3(self):
        self.assertEqual(self._sale_gains("margin_gains_wash.json")[0],
                         ("2025-12-30", -200.0))
        r = _cli(self.root, "form-export")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("Line 13200 (gain/loss): -200.00",
                      " ".join(r.stdout.split()))

    def test_list_as_of_a_date(self):
        r = _cli(self.root, "list", "--date", "2025-12-25", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        rows = {x["symbol"]: x for x in json.loads(r.stdout)["rows"]}
        self.assertAlmostEqual(rows["XYZ.TO"]["cost"], 1000.0, places=2)

    def test_carryover(self):
        r = _cli(self.root, "carryover", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        rows = {x["year"]: x for x in json.loads(r.stdout)["rows"]}
        self.assertAlmostEqual(rows[2025]["net_gain"], -200.0, places=2)
        self.assertAlmostEqual(rows[2026]["net_gain"], 300.0, places=2)

    def test_audit(self):
        r = _cli(self.root, "audit", "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-800:])
        doc = json.loads(r.stdout)
        self.assertFalse(doc["failed"])
        self.assertAlmostEqual(doc["total_gain"], -200.0, places=2)


@rule("CA-INC-DATE-ROC")
class TestCorporateDistributionsInLocks(unittest.TestCase):
    def test_close_year_check_filed_and_handoff(self):
        # Sold Dec 10 at 8, rebought Dec 15; a 300 ROC with record date
        # Dec 1 paid Jan 15. As a corporation's ROC (paid after the
        # sale) the sale is a 200 superficial loss whose denial lands on
        # the Dec-15 shares: the lock holds 100 at ACB 1,000 (200
        # deferred) and a 0 realized total. As a trust's ROC the sale
        # gains 100 and the year ends at ACB 800 with nothing deferred.
        csv = (_QH + _q("2025-03-03", qty="100", price="10", gross="-1000",
                        net="-1000")
               + _q("2025-12-10", "2025-12-11", action="Sell", qty="-100",
                    price="8", gross="800", net="800")
               + _q("2025-12-15", "2025-12-16", qty="100", price="8",
                    gross="-800", net="-800")
               + _q("2026-01-15", action="DIV",
                    desc="XYZ CORP RETURN OF CAPITAL ON 100 SHS REC "
                         "12/01/25 PAY 01/15/26", net="300.00",
                    act="Dividends"))
        with tempfile.TemporaryDirectory() as tmp:
            p25, p26 = Path(tmp) / "p2025", Path(tmp) / "p2026"
            _corp_project(p25, csv=csv)
            r = _cli(p25, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            c = _cli(p25, "close-year")
            self.assertEqual(c.returncode, 0, c.stderr[-1500:])
            lock = json.loads((p25 / "filed" / "2025.json").read_text())
            self.assertAlmostEqual(lock["totals"]["realized"], 0.0,
                                   places=2)
            self.assertEqual(lock["year_end"]["equity"]["XYZ.TO"],
                             {"acb": 1000.0, "deferred": 200.0,
                              "qty": 100.0})
            f = _cli(p25, "check-filed")
            self.assertEqual(f.returncode, 0, f.stdout[-1500:]
                             + f.stderr[-800:])
            self.assertIn("filed 2025: OK", f.stdout + f.stderr)
            _corp_project(p26, 2026, csv=csv, extra=(
                f'prior_year_record = "{p25 / "filed" / "2025.json"}"\n'))
            r = _cli(p26, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            h = _cli(p26, "handoff", "--json")
        self.assertEqual(h.returncode, 0, h.stdout[-1500:] + h.stderr[-800:])
        doc = json.loads(h.stdout)
        self.assertEqual((doc["problems"], doc["positions"]), (0, []))


class TestCorporateDistributionIncomeAndExplain(unittest.TestCase):
    """A corporation's "DIST ON" payout is income when PAID
    (CA-INC-DATE-DIV): a December record date paid in January is not
    2025 income in the .sum. Its ROC lowers the ACB when paid
    (CA-INC-DATE-ROC), so the Dec-30 sale is a 200 loss made superficial
    by the Jan-5 rebuy, which `wash-sales --explain` traces."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        csv = (_corp_csv()
               + _q("2026-01-05", "2026-01-06", qty="100", price="8",
                    gross="-800", net="-800")
               + _q("2026-01-15", action="DIS",
                    desc="XYZ CORP DIST ON 100 SHS REC 12/20/25 "
                         "PAY 01/15/26", net="50.00", act="Dividends"))
        _corp_project(cls.root, csv=csv)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @rule("CA-INC-DATE-DIV")
    def test_sum_income_keeps_the_payout_on_its_pay_date(self):
        txt = (self.root / "reports" / "margin.sum").read_text()
        income = txt[txt.index("INCOME SUMMARY"):]
        income = income[:income.index("TOTAL NET INCOME") + 100]
        self.assertNotRegex(income, r"(?m)^XYZ\.TO\s")
        self.assertRegex(income, r"TOTAL NET INCOME\s+0\.00")

    @rule("CA-INC-DATE-ROC")
    def test_explain_traces_the_pay_date_roc(self):
        r = _cli(self.root, "wash-sales", "--explain")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        # The report layout (docs/output-style.md): "gain: ... (raw, denied)".
        self.assertIn("(raw -200.00, denied 200.00)", r.stdout)


def _ib_futures_csv(cur):
    from test_fix_ibparse import HEAD
    from test_parser_audit_2026_09b import (IB_TRADES_HDR, _ib_fii_futures,
                                            _ib_trade)
    csv = (HEAD + IB_TRADES_HDR
           + _ib_trade("Futures", "QZFH6", "2025-06-02, 10:00:00", 1, 5000,
                       -500, -2, "O")
           + _ib_trade("Futures", "QZFH6", "2025-12-31, 10:00:00", -1, 5100,
                       510, -2, "C")
           + _ib_fii_futures("QZFH6", "0.1"))
    return csv.replace(",USD,", f",{cur},")


class TestFuturesSettleReachesTheParser(unittest.TestCase):
    """futures_settle = "next_day": a Dec-31 futures sale settles on the
    next settlement day (Jan 2), so on the settlement basis it is not a
    2025 disposition."""

    def _base(self, country):
        cur = "CAD" if country == "canada" else "USD"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                f'[settings]\ncountry = "{country}"\nyear = 2025\n'
                f'base_currency = "{cur}"\nsource_currencies = []\n'
                + ('option_grant_timing_since = 2025\n'
                   if country == "canada" else "")
                + 'futures_settle = "next_day"\n'
                f'[accounts.ib]\ntype = "taxable"\n')
            (root / "inputs" / "ib").mkdir(parents=True)
            (root / "inputs" / "ib" / "ib_2025.csv").write_text(
                _ib_futures_csv(cur))
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            base = json.loads((root / "work" / "ib_base.json").read_text())
            fe = _cli(root, "form-export")
        settles = {t["date"]: t["date_settle"] for t in base["transactions"]
                   if t["symbol"].startswith("F:QZFH6")}
        return settles, fe

    @rule("CA-DATE-10")
    def test_canada(self):
        settles, fe = self._base("canada")
        self.assertEqual(settles, {"2025-06-02": "2025-06-03",
                                   "2025-12-31": "2026-01-02"})
        self.assertEqual(fe.returncode, 0, fe.stderr[-1500:])
        self.assertNotIn("Line 15300 (gain/loss): 6.00",
                         " ".join(fe.stdout.split()))

    @rule("US-DATE-12")
    def test_usa(self):
        settles, _fe = self._base("usa")
        self.assertEqual(settles, {"2025-06-02": "2025-06-03",
                                   "2025-12-31": "2026-01-02"})


class TestParserCountryWiring(unittest.TestCase):
    @rule("CA-ACB-08")
    def test_canada_run_books_a_foreign_roc_as_a_dividend(self):
        # The run passes --country canada --foreign-roc dividend to the
        # parser; the parser's own neutral default is an ACB reduction.
        csv = ("Statement,Header,Field Name,Field Value\n"
               "Statement,Data,BrokerName,Interactive Brokers\n"
               "Statement,Data,Title,Activity Statement\n"
               "Trades,Header,DataDiscriminator,Asset Category,Currency,"
               "Account,Symbol,Date/Time,Quantity,T. Price,C. Price,"
               "Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n"
               "Trades,Data,Order,Stocks,CAD,U5550001,QZRX,"  # pii-ok
               "\"2025-03-03, 10:00:00\",1000,3,0,-3000,-1,0,0,0,O\n"
               "Dividends,Header,Currency,Date,Description,Amount\n"
               "Dividends,Data,CAD,2025-06-19,QZRX(US9990000701) Cash "
               "Dividend USD 0.30 per Share (Return of Capital),300\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                'option_grant_timing_since = 2025\n'
                '[accounts.ib]\ntype = "taxable"\n')
            (root / "inputs" / "ib").mkdir(parents=True)
            (root / "inputs" / "ib" / "ib_2025.csv").write_text(csv)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            base = json.loads((root / "work" / "ib_base.json").read_text())
        other = [(t["action"], t["net_amount"]) for t in base["transactions"]
                 if t["action"] != "BUYSELL"]
        self.assertEqual(other, [("DIVIDEND", 300.0)])


class TestT1135SeesTheShelteredAccounts(unittest.TestCase):
    @rule("CA-RPT-12")
    def test_year_end_cost_matches_the_books(self):
        # A 50,000 loss; the RRSP buys 1,000 units first (the whole loss
        # is denied for good), the taxable account rebuys 500. The
        # taxable 500 carry no deferred loss: cost 50,000, as `list`
        # shows. Without the RRSP context t1135's full-history pass put
        # 25,000 of the denial on them.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                'option_grant_timing_since = 2025\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n')
            for acct, tt in (
                    ("margin",
                     "BUYSELL 2025-02-03 10:00:00 ZZZ.US 1000 CAD 150.00 "
                     "150000.00 0\n"
                     "BUYSELL 2025-06-03 10:00:00 ZZZ.US -1000 CAD 100.00 "
                     "100000.00 0\n"
                     "BUYSELL 2025-06-12 10:00:00 ZZZ.US 500 CAD 100.00 "
                     "50000.00 0\n"),
                    ("rrsp",
                     "BUYSELL 2025-06-10 10:00:00 ZZZ.US 1000 CAD 100.00 "
                     "100000.00 0\n")):
                (root / "inputs" / acct).mkdir(parents=True)
                (root / "inputs" / acct / "x.tt").write_text(tt)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            t = _cli(root, "t1135", "--json")
            ls = _cli(root, "list", "--json")
        self.assertEqual(t.returncode, 0, t.stderr[-1500:])
        prop = {p["symbol"]: p for p in json.loads(t.stdout)["properties"]}
        self.assertAlmostEqual(prop["ZZZ.US"]["year_end_cost"], 50000.0,
                               places=2)
        held = [x for x in json.loads(ls.stdout)["rows"]
                if x["account"] == "margin" and x["symbol"] == "ZZZ.US"]
        self.assertAlmostEqual(held[0]["cost"], 50000.0, places=2)


class TestInstalmentsUseTheEstimateDeductions(unittest.TestCase):
    def test_estimate_table_deductions_lower_the_instalment_base(self):
        # instalments builds on `taxjson estimate`: [estimate] deductions
        # and carrying_charges lower the current-year net tax, so the
        # schedule equals the estimate's (R1-213).
        cfg = ('[settings]\nyear = 2025\ncountry = "canada"\n'
               'province = "ON"\nbase_currency = "CAD"\n'
               'source_currencies = []\noption_grant_timing_since = 2025\n'
               '[accounts.margin]\ntype = "taxable"\n'
               '[estimate]\nother_income = 100000\nother_losses = 0\n'
               'deductions = {d}\ncarrying_charges = {c}\n'
               '[instalments]\nbasis = "current_year"\nwithheld = 0\n')
        tax = {}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "m.tt").write_text(
                "BUYSELL 2025-02-03 10:00:00 ZZZ.TO 1000 CAD 10.00 "
                "10000.00 0\n"
                "BUYSELL 2025-06-03 10:00:00 ZZZ.TO -1000 CAD 110.00 "
                "110000.00 0\n")
            for d, c in ((0, 0), (20000, 5000)):
                (root / "taxjson.toml").write_text(cfg.format(d=d, c=c))
                if not tax:
                    r = _cli(root, "run", "--no-input")
                    self.assertEqual(r.returncode, 0, r.stderr[-1500:])
                i = _cli(root, "instalments", "--json")
                self.assertEqual(i.returncode, 0, i.stderr[-1500:])
                tax[d] = json.loads(i.stdout)["current_net_tax"]
        self.assertLess(tax[20000], tax[0] - 1000)


if __name__ == "__main__":
    unittest.main()
