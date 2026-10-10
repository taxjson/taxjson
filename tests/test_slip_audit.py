"""`taxjson slip-audit`: T5 / T3 slips against the books' income
(tax-logic CA-SLIP-01..03; lib/slip_audit, lib/ib_dividends).

Synthetic data only: a fake IB account id (IB_ACCT), invented tickers, a
made-up holder name that must never be printed, a seeded FX cache under
a private HOME (USD/CAD 1.40 in the first half of 2025, 1.36 in the
second, so the daily and the annual-average conversions differ).
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from tax_rules import rule, rule_absent

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

IB_ACCT = "U5550001"  # pii-ok (synthetic)
HOLDER = "Zed Synthetic-Holder"

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _home(tmp: Path) -> Path:
    home = tmp / "home"
    home.mkdir(parents=True, exist_ok=True)
    obs = {}
    d = datetime.date(2024, 12, 1)
    while d <= datetime.date(2025, 12, 31):
        if d.weekday() < 5:
            obs[d.isoformat()] = "1.40" if d.month < 7 else "1.36"
        d += datetime.timedelta(days=1)
    (home / ".currency_price_cache.json").write_text(json.dumps(
        {"_boc": {"USDCAD": {"obs": obs}},
         "_coverage": {"boc:USDCAD": [["2017-01-03", "2025-12-31"]]}}))
    return home


def tj(root: Path, home: Path, *args, width=None):
    env = dict(os.environ, HOME=str(home), TAXJSON_OFFLINE="1",
               PYTHONPATH=str(SRC), NO_COLOR="1")
    if width is not None:
        env["TAXJSON_WIDTH"] = str(width)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


_TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
         'province = "ON"\noption_grant_timing_since = 2025\n')

_IB_STATEMENT = f"""Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Statement,Data,Title,Activity Statement
Statement,Data,Period,"January 1, 2025 - December 31, 2025"
Account Information,Header,Field Name,Field Value
Account Information,Data,Account,{IB_ACCT}
Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,CAD,ZZQ,"2025-01-06, 10:00:00",1000,20.00,20.00,-20000.00,-1.00,20001.00,0,0,O
Trades,Data,Order,Stocks,USD,QQZ,"2025-01-06, 10:00:00",100,50.00,50.00,-5000.00,-1.00,5001.00,0,0,O
Trades,Data,Order,Stocks,CAD,ZZT,"2025-01-06, 10:00:00",100,30.00,30.00,-3000.00,-1.00,3001.00,0,0,O
Trades,Data,Order,Stocks,USD,NQZ,"2025-01-06, 10:00:00",100,10.00,10.00,-1000.00,-1.00,1001.00,0,0,O
Financial Instrument Information,Header,Asset Category,Symbol,Description,Conid,Security ID,Listing Exch,Multiplier,Type,Code
Financial Instrument Information,Data,Stocks,ZZQ,ZZQ CORP,111,CA0000000011,TSE,1,COMMON,
Financial Instrument Information,Data,Stocks,QQZ,QQZ INC,222,US0000000022,NASDAQ,1,COMMON,
Financial Instrument Information,Data,Stocks,ZZT,ZZT INDEX ETF,333,CA0000000033,TSE,1,ETF,
Financial Instrument Information,Data,Stocks,NQZ,NQZ OYJ ADR,444,FI0000000044,NYSE,1,ADR,
Dividends,Header,Currency,Date,Description,Amount
Dividends,Data,CAD,2025-03-14,ZZQ(CA0000000011) Cash Dividend CAD 0.50 per Share (Ordinary Dividend),500.00
Dividends,Data,USD,2025-05-02,NQZ(FI0000000044) Cash Dividend USD 0.10 per Share (Ordinary Dividend),10.00
Dividends,Data,CAD,2025-06-13,ZZQ(CA0000000011) Payment in Lieu of Dividend (Ordinary Dividend),15.00
Dividends,Data,USD,2025-08-14,QQZ(US0000000022) Cash Dividend USD 0.25 per Share (Ordinary Dividend),25.00
Dividends,Data,CAD,2025-09-30,ZZT(CA0000000033) Cash Dividend CAD 0.40 per Share (Ordinary Dividend),40.00
Withholding Tax,Header,Currency,Date,Description,Amount,Code
Withholding Tax,Data,USD,2025-05-02,NQZ(FI0000000044) Cash Dividend USD 0.10 per Share - FI Tax,-2.00,
Withholding Tax,Data,USD,2025-08-14,QQZ(US0000000022) Cash Dividend USD 0.25 per Share - US Tax,-3.75,
Interest,Header,Currency,Date,Description,Amount
Interest,Data,CAD,2025-02-04,CAD Credit Interest for Jan-2025,12.34
"""

_IB_HDR = ("DataDiscriminator,Currency,Symbol,Conid,Country,ReportDate,"
           "ExDate,Shares,RevenueComponent,QualifiedIndicator,Gross,"
           "GrossInBase,GrossInUSD,Withhold,WithholdInBase,WithholdInUSD")


def _ib_report(extra: str = "") -> str:
    """IB's dividends report for the statement above: every kind of
    RevenueComponent — T5 eligible and capital gains, the T3 parts
    (eligible, foreign non-business, return of capital), a foreign
    ordinary dividend with withholding (USD payer), an NRA-exempt
    one, a withholding-only 'Other' row, a payment in lieu split
    between box 24 and box 18, and one component taxjson does not
    know."""
    d = "DividendDetail,Data"
    p = "PILDetail,Data"
    return f"""Account,Header,AccountNumber,AccountAlias,Name,BaseCurrency,
Account,Data,{IB_ACCT},,{HOLDER},CAD,
DividendDetail,Header,{_IB_HDR}
{d},Summary,CAD,ZZQ,111,CA,20250314,20250228,1000,,,500,500,360,0,0,0
{d},RevenueComponent,CAD,ZZQ,111,CA,20250314,20250228,,T5: Eligible Dividend Income,Not Qualified,480,480,345.6,0,0,0
{d},RevenueComponent,CAD,ZZQ,111,CA,20250314,20250228,,T5: Capital Gains,Not Qualified,20,20,14.4,0,0,0
{d},Summary,USD,NQZ,444,FI,20250502,20250420,100,,,10,14.05,10,-2,-2.81,-2
{d},RevenueComponent,USD,NQZ,444,FI,20250502,20250420,,Ordinary Div - NRA Withholding Exempt,Qualified - Meets Holding Period,10,14.05,10,-2,-2.81,-2
{d},Summary,USD,QQZ,222,US,20250814,20250801,100,,,25,34.10,25,-3.75,-5.12,-3.75
{d},RevenueComponent,USD,QQZ,222,US,20250814,20250801,,Ordinary Dividend,Qualified - Meets Holding Period,25,34.10,25,-3.75,-5.12,-3.75
{d},Summary,USD,QQZ,222,US,20251021,20251003,0,,,0,0,0,-0.01,-0.01,-0.01
{d},RevenueComponent,USD,QQZ,222,US,20251021,20251003,,Other,Other,0,0,0,-0.01,-0.01,-0.01
{d},Summary,CAD,ZZT,333,CA,20250930,20250924,100,,,40,40,28.8,0,0,0
{d},RevenueComponent,CAD,ZZT,333,CA,20250930,20250924,,T3: Eligible Dividend Income,Qualified - Meets Holding Period,30,30,21.6,0,0,0
{d},RevenueComponent,CAD,ZZT,333,CA,20250930,20250924,,T3: Foreign Non-Business Income,Not Qualified,6.55,6.55,4.7,0,0,0
{d},RevenueComponent,CAD,ZZT,333,CA,20250930,20250924,,T3: Return of Capital,Not Qualified,3.45,3.45,2.5,0,0,0
{d},RevenueComponent,CAD,ZZT,333,CA,20250930,20250924,,T5: Mystery Income,Not Qualified,0,0,0,0,0,0
DividendDetail,Total,,CAD,,,,,,,,,540,540,388.8,0,0,0
PILDetail,Header,DataDiscriminator,Currency,Symbol,Conid,Country,ReportDate,ExDate,Shares,RevenueComponent,Gross,GrossInBase,GrossInUSD,Withhold,WithholdInBase,WithholdInUSD
{p},Summary,CAD,ZZQ,111,CA,20250613,20250530,30,,15,15,10.8,0,0,0
{p},RevenueComponent,CAD,ZZQ,111,CA,20250613,20250530,,T5: Eligible Dividend Income,14.5,14.5,10.44,0,0,0
{p},RevenueComponent,CAD,ZZQ,111,CA,20250613,20250530,,T5: Capital Gains,0.5,0.5,0.36,0,0,0
{extra}"""


def _ib_project(tmp: Path, report: str = None) -> Path:
    root = tmp / "ib"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "slips").mkdir(parents=True)
    (root / "inputs" / "margin" / "ib.csv").write_text(_IB_STATEMENT)
    (root / "inputs" / "slips" / f"{IB_ACCT}.2025.dividends.csv"
     ).write_text(report if report is not None else _ib_report())
    (root / "taxjson.toml").write_text(
        _TOML + '[accounts.margin]\ntype = "taxable"\n')
    return root


def _apply(root: Path, rep: dict) -> None:
    sg = rep["suggestions"]
    with (root / "taxjson.toml").open("a") as f:
        for e in sg["capital_gains_dividends"]:
            f.write("\n" + e["toml"] + "\n")
    for e in sg["tt_lines"]:
        p = root / e["file"]
        with p.open("a") as f:
            f.write("\n".join(e["lines"]) + "\n")


class TestIBReport(unittest.TestCase):
    """lib/ib_dividends: every RevenueComponent kind, the holder's name
    never kept, the account hashed as the books carry it."""

    def test_components_and_privacy(self):
        from taxjson.bin.taxjson_brokerage import hash_broker_account
        from taxjson.lib import ib_dividends as IB
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / f"{IB_ACCT}.2025.dividends.csv"
            p.write_text(_ib_report())
            self.assertTrue(IB.is_dividends_report(p))
            q = Path(td) / "t5008.csv"
            q.write_text("symbol,quantity,proceeds\nZZQ,1,10\n")
            self.assertFalse(IB.is_dividends_report(q))
            rep = IB.read_report(p)
        self.assertEqual(rep.account_hash, hash_broker_account(IB_ACCT))
        self.assertEqual(rep.account_masked, "U5***")
        self.assertNotIn(HOLDER, repr(rep))
        self.assertNotIn(IB_ACCT, repr(rep).replace(str(p), ""))
        tot = {}
        for pay in rep.payments:
            for c in pay.components:
                k = (c.slip, c.category)
                tot[k] = round(tot.get(k, 0.0) + c.gross_base, 2)
        self.assertEqual(tot[("T5", "eligible")], 494.5)
        self.assertEqual(tot[("T5", "cg_div")], 20.5)
        self.assertEqual(tot[("T5", "foreign")], 48.15)
        self.assertEqual(tot[("T3", "eligible")], 30.0)
        self.assertEqual(tot[("T3", "foreign")], 6.55)
        self.assertEqual(tot[("T3", "roc")], 3.45)
        self.assertEqual(rep.unknown, [("T5: Mystery Income", 0.0)])
        pil = [x for x in rep.payments if x.pil]
        self.assertEqual(len(pil), 1)
        self.assertAlmostEqual(sum(x.withheld_base for x in rep.payments),
                               7.94)

    def test_component_table(self):
        from taxjson.lib.ib_dividends import component_category as cc
        for label, country, want in (
                ("T5: Eligible Dividend Income", "CA", ("T5", "eligible")),
                ("T5: Other Than Eligible Dividend Income", "CA",
                 ("T5", "non_eligible")),
                ("T5: Capital Gains", "CA", ("T5", "cg_div")),
                ("T3: Return of Capital", "CA", ("T3", "roc")),
                ("T3: Foreign Non-Business Income", "CA", ("T3", "foreign")),
                ("T3: Capital Gains", "CA", ("T3", "trust_cg")),
                ("T3: Other Income", "CA", ("T3", "other")),
                ("Ordinary Dividend", "CA", ("T5", "eligible")),
                ("Ordinary Dividend", "US", ("T5", "foreign")),
                ("Ordinary Div - NRA Withholding Exempt", "TW",
                 ("T5", "foreign")),
                ("T5: Something New", "CA", ("T5", None))):
            with self.subTest(label=label):
                self.assertEqual(cc(label, country), want)


class TestSlipsFile(unittest.TestCase):
    """slips.toml is checked loudly, naming the entry."""

    CFG = {"settings": {"year": 2025, "country": "canada"},
           "accounts": {"margin": {"type": "taxable"},
                        "tfsa": {"type": "sheltered"}}}

    def _load(self, text):
        from taxjson.lib import slip_audit as SA
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "slips.toml"
            p.write_text(text)
            return SA.load_slips_file(p, self.CFG, 2025)

    def test_good_file(self):
        slips, _r, avg = self._load(
            'year = 2025\n[annual_average]\nUSD = 1.3\n'
            '[[slip]]\ntype = "t5"\naccount = "margin"\ncurrency = "USD"\n'
            'broker_account = "68-12345"\n'  # pii-ok (synthetic)
            'boxes = { 24 = "1,234.50", 15 = 10, 25 = 1703.61 }\n'
            '[[slip.line]]\nsymbol = "zzq.to"\ndate = 2025-03-14\n'
            'box = "18"\namount = 5.5\n')
        (s,) = slips
        self.assertEqual((s.type, s.currency, s.broker_masked),
                         ("T5", "USD", "68***"))
        self.assertEqual(s.amounts, {"ca_div": 1234.5, "foreign": 10.0})
        self.assertEqual(len(s.broker_hashes), 2)   # as typed, and bare
        self.assertEqual(s.lines[0].root, "ZZQ")
        self.assertEqual(avg, {"USD": 1.3})

    def test_refusals_name_the_entry(self):
        from taxjson.lib.slip_audit import SlipsError
        head = '[[slip]]\ntype = "T5"\naccount = "margin"\n'
        for text, frag in (
                ('year = 2024\n', "last year's slips"),
                ('[[slip]]\ntype = "NR4"\naccount = "margin"\n', "NR4"),
                ('[[slip]]\ntype = "T4"\naccount = "margin"\n', "T5"),
                ('[[slip]]\ntype = "T5"\n', "account is required"),
                ('[[slip]]\ntype = "T5"\naccount = "nope"\n',
                 "not an [accounts.*]"),
                ('[[slip]]\ntype = "T3"\naccount = "tfsa"\n',
                 "registered"),
                (head + 'boxes = { 29 = 1 }\n', "an identifier"),
                (head + 'boxes = { 99 = 1 }\n', "not a T5 amount box"),
                (head + 'boxes = { 49 = "405 54" }\n', "not a T5"),
                ('[[slip]]\ntype = "T3"\naccount = "margin"\n'
                 'boxes = { 49 = "405 54" }\n', "decimal comma or a space"),
                (head + 'name = "x"\n', "unknown key"),
                ('owner = "x"\n', "unknown key")):
            with self.subTest(text=text):
                with self.assertRaises(SlipsError) as cm:
                    self._load(text)
                self.assertIn(frag, str(cm.exception))
                if "[[slip]]" in text:
                    self.assertIn("[[slip]] #1", str(cm.exception))

    def test_annual_average_from_the_cache(self):
        from taxjson.lib.slip_audit import annual_average
        cache = {"_boc": {"USDCAD": {"obs": {
            "2025-01-02": "1.40", "2025-07-02": "1.36", "2024-12-31": "9",
            "2025-07-03": "bad"}}}}
        a = annual_average("USD", 2025, cache)
        self.assertEqual(a["rate"], 1.38)
        self.assertEqual(a["observations"], 2)
        self.assertIn("Bank of Canada", a["source"])
        self.assertIsNone(annual_average("EUR", 2025, cache))


class TestIBEndToEnd(unittest.TestCase):
    """IB's dividends report: the T5's box 18 and the T3's return of
    capital are found, the suggestions apply cleanly (divs-sum shows
    box 18 apart, roc-sum the ROC) and the audit then passes; the
    checklist's t5-t3 step asks per account."""

    @rule("CA-SLIP-01", "CA-SLIP-03", "CA-INC-06")
    def test_find_apply_pass(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _ib_project(tmp)
            r = tj(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
            r = tj(root, home, "slip-audit", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)
            rep = json.loads(r.stdout)
            for out in (r.stdout, r.stderr,
                        tj(root, home, "slip-audit").stdout):
                self.assertNotIn(HOLDER, out)
                self.assertNotIn(IB_ACCT, out)
            (acc,) = rep["accounts"]
            (g,) = acc["groups"]
            self.assertEqual(g["broker_account"], "U5***")
            lines = {ln["category"]: ln for ln in g["buckets"][0]["lines"]}
            self.assertEqual(lines["cg_div"]["slip"], 20.5)
            self.assertEqual(lines["cg_div"]["books"], 0.0)
            self.assertEqual(lines["cg_div"]["status"], "differs")
            self.assertEqual(lines["roc"]["slip"], 3.45)
            self.assertEqual(lines["roc"]["status"], "differs")
            # Interest: IB's report has none — not compared, a note.
            self.assertNotIn("interest", lines)
            self.assertTrue(any("box 13" in n for n in
                                rep["suggestions"]["notes"]))
            # The payment in lieu's box-18 part cannot be named.
            self.assertTrue(any("payment in lieu" in n and "box-18" in n
                                for n in rep["suggestions"]["notes"]))
            # The unknown component is said, not dropped.
            self.assertTrue(any("Mystery" in p for p in rep["problems"]))
            self.assertEqual(g["payments"]["matched"], 5)
            self.assertEqual(g["payments"]["missing_from_books"], [])
            cgd = rep["suggestions"]["capital_gains_dividends"]
            self.assertEqual([(e["symbol"], e["date"], e["amount"])
                              for e in cgd],
                             [("ZZQ.TO", "2025-03-14", 20.0)])
            (tt,) = rep["suggestions"]["tt_lines"]
            self.assertEqual(tt["file"], "inputs/margin/slip-audit.tt")
            self.assertEqual(tt["lines"], [
                "ADJUST 2025-09-30 09:30:00 ZZT.TO CAD -3.45 type=roc",
                "DIVIDEND 2025-09-30 09:30:00 ZZT.TO 0 CAD 0 -3.45"])
            # The checklist asks, per account.
            r = tj(root, home, "checklist", "--json")
            st = {s["id"]: s for s in json.loads(r.stdout)["steps"]}
            self.assertEqual(st["t5-t3"]["status"], "attention")
            self.assertIn("margin: ", st["t5-t3"]["detail"])
            self.assertEqual(st["t5-t3"]["command"], "taxjson slip-audit")

            _apply(root, rep)
            r = tj(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
            d = json.loads(tj(root, home, "divs-sum", "--json").stdout)
            self.assertEqual(d["capital_gains_dividends"]["totals"],
                             {"CAD": 20.0})
            ro = json.loads(tj(root, home, "roc-sum", "--json").stdout)
            self.assertEqual(ro["totals_taxable"], {"CAD": 3.45})
            r = tj(root, home, "slip-audit", "--json")
            rep2 = json.loads(r.stdout)
            self.assertEqual(rep2["issues"], [], rep2["issues"])
            self.assertEqual(r.returncode, 0)
            self.assertEqual(rep2["suggestions"]["capital_gains_dividends"],
                             [])
            self.assertEqual(rep2["suggestions"]["tt_lines"], [])
            txt = tj(root, home, "slip-audit").stdout
            self.assertIn("Every slip agrees with the books", txt)
            from _style import assert_styled
            assert_styled(self, tj(root, home, "slip-audit",
                                   width=120).stdout)
            r = tj(root, home, "checklist", "--json")
            st = {s["id"]: s for s in json.loads(r.stdout)["steps"]}
            self.assertEqual(st["t5-t3"]["status"], "done")

    @rule("CA-SLIP-01")
    def test_missing_payments_both_ways_and_answers(self):
        extra = ("DividendDetail,Data,Summary,CAD,ZZQ,111,CA,20251212,"
                 "20251128,1000,,,500,500,360,0,0,0\n"
                 "DividendDetail,Data,RevenueComponent,CAD,ZZQ,111,CA,"
                 "20251212,20251128,,T5: Eligible Dividend Income,Not "
                 "Qualified,500,500,360,0,0,0\n")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            report = _ib_report(extra)
            # The statement's QQZ dividend is not in IB's report.
            report = "\n".join(ln for ln in report.splitlines()
                               if ",QQZ,222,US,20250814," not in ln) + "\n"
            root = _ib_project(tmp, report)
            self.assertEqual(tj(root, home, "run", "--no-input").returncode,
                             0)
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            pay = rep["accounts"][0]["groups"][0]["payments"]
            self.assertEqual([(e["symbol"], e["date"], e["amount"])
                              for e in pay["missing_from_books"]],
                             [("ZZQ", "2025-12-12", 500.0)])
            self.assertEqual([(e["symbol"], e["date"], e["amount"])
                              for e in pay["missing_from_slip"]],
                             [("QQZ.US", "2025-08-14", 25.0)])
            kinds = {i["kind"] for i in rep["issues"]}
            self.assertTrue({"missing-from-books",
                             "missing-from-slip"} <= kinds)
            # A DONE mark answers this account's findings; a new
            # finding is a new question.
            r = tj(root, home, "checklist", "--done", "t5-t3")
            self.assertEqual(r.returncode, 0, r.stderr)
            st = {s["id"]: s for s in json.loads(
                tj(root, home, "checklist", "--json").stdout)["steps"]}
            self.assertEqual(st["t5-t3"]["effective"], "done")
            (root / "inputs" / "margin" / "more.tt").write_text(
                "DIVIDEND 2025-11-14 09:30:00 ZZQ.TO 0 CAD 0 7.00\n")
            self.assertEqual(tj(root, home, "run", "--no-input").returncode,
                             0)
            st = {s["id"]: s for s in json.loads(
                tj(root, home, "checklist", "--json").stdout)["steps"]}
            self.assertEqual(st["t5-t3"]["effective"], "attention")

    def test_reconcile_slips_and_checklist_skip_the_report(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _ib_project(tmp)
            self.assertEqual(cl.slip_files(root), [])
            (root / "inputs" / "slips" / "slips.toml").write_text("")
            self.assertEqual(cl._unread_slip_files(root), [])
            r = tj(root, _home(tmp), "reconcile-slips",
                   str(root / "inputs" / "slips"
                       / f"{IB_ACCT}.2025.dividends.csv"))
            self.assertEqual(r.returncode, 2)
            self.assertIn("slip-audit", r.stderr)
            self.assertNotIn(IB_ACCT, r.stderr)
            from taxjson.lib import slip_audit as SA
            with self.assertRaises(SA.SlipsError) as cm:
                SA.audit(root, {"settings": {"year": 2025,
                                             "country": "canada"},
                                "accounts": {"margin": {"type": "taxable"},
                                             "tfsa": {"type": "sheltered"}}},
                         account="tfsa")
            self.assertIn("gets no T5 or T3", str(cm.exception))


_RBC_TT = """BUYSELL 2025-01-06 10:00:00 QRS.US 100 USD 50 5000 0
DIVIDEND 2025-03-14 09:30:00 QRS.US 100 USD 0.30 30.00
TAX 2025-03-14 09:30:00 QRS.US 100 USD 0.045 4.50
DIVIDEND 2025-09-12 09:30:00 QRS.US 100 USD 0.30 30.00
TAX 2025-09-12 09:30:00 QRS.US 100 USD 0.045 4.50
BUYSELL 2025-01-06 10:00:00 ZZF.TO 100 CAD 20 2000 0
DIVIDEND 2025-06-30 09:30:00 ZZF.TO 100 CAD 0.50 50.00 record=2025-06-27 label=distribution
DIVIDEND 2026-01-07 09:30:00 ZZF.TO 100 CAD 0.50 50.00 record=2025-12-30 label=distribution
"""

_SLIPS = """year = 2025

[[slip]]
type = "T5"
issuer = "RBC"
account = "rbc"
currency = "USD"
boxes = { 15 = 60.00, 16 = 9.00 }

[[slip]]
type = "T3"
issuer = "RBC"
account = "rbc"
currency = "CAD"
security = "ZZF.TO"
boxes = { 49 = 70.00, 50 = 96.60, 21 = 20.00, 42 = 10.00 }

[[slip]]
type = "T5"
issuer = "XB"
account = "ghost"
boxes = { 24 = 25.00 }

[[slip]]
type = "T5"
issuer = "XB"
account = "rbc"
currency = "CAD"

[[slip]]
type = "T5008"
issuer = "XB"
account = "ghost"
code = "SHS"
boxes = { 20 = 100.00, 21 = 120.00 }
"""


def _typed_project(tmp: Path) -> Path:
    root = tmp / "typed"
    for a, text in (("rbc", _RBC_TT),
                    ("spare", "BUYSELL 2025-01-06 10:00:00 ZZY.TO 10 CAD "
                              "10 100 0\nDIVIDEND 2025-04-01 09:30:00 "
                              "ZZY.TO 10 CAD 4 40.00\n"),
                    ("tiny", "INTEREST 2025-12-31 16:00:00 CAD 12.00\n"),
                    ("ghost", "BUYSELL 2025-01-06 10:00:00 ZZG.TO 10 CAD "
                              "10 100 0\nBUYSELL 2025-02-03 10:00:00 "
                              "ZZG.TO -10 CAD 12 120 0\n")):
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / f"{a}.tt").write_text(text)
    (root / "inputs" / "slips").mkdir(parents=True)
    (root / "inputs" / "slips" / "slips.toml").write_text(_SLIPS)
    (root / "taxjson.toml").write_text(
        _TOML + "".join(f'[accounts.{a}]\ntype = "taxable"\n'
                        for a in ("rbc", "spare", "tiny", "ghost")))
    return root


class TestTypedSlips(unittest.TestCase):
    """slips.toml: an RBC-like USD T5 (compared in USD, both CAD
    conversions shown), a T3 with a return of capital and a capital
    gain, coverage gaps both ways, and an empty slip."""

    @rule("CA-SLIP-01", "CA-SLIP-02", "CA-SLIP-03", "CA-INC-DATE-TRUST")
    def test_usd_t5_t3_roc_and_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _typed_project(tmp)
            r = tj(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
            r = tj(root, home, "slip-audit", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)
            rep = json.loads(r.stdout)
            avg = rep["annual_average"]["USD"]
            self.assertTrue(1.36 < avg["rate"] < 1.40)
            acc = {a["account"]: a for a in rep["accounts"]}
            (g,) = acc["rbc"]["groups"]
            b = {x["currency"]: x for x in g["buckets"]}
            usd = {ln["category"]: ln for ln in b["USD"]["lines"]}
            self.assertEqual((usd["foreign"]["slip"], usd["foreign"]["books"],
                              usd["foreign"]["status"]),
                             (60.0, 60.0, "ok"))
            # Daily: 30 x 1.40 + 30 x 1.36; average: 60 x the mean.
            self.assertEqual(usd["foreign"]["books_cad_daily"], 82.8)
            self.assertEqual(usd["foreign"]["slip_cad_annual_average"],
                             round(60 * avg["rate"], 2))
            self.assertEqual(usd["foreign_tax"]["books"], 9.0)
            cad = {ln["category"]: ln for ln in b["CAD"]["lines"]}
            # The T3 split: the books' 100 of ZZF (both distributions
            # are 2025's by their record dates) in the T3's proportions.
            self.assertEqual(b["CAD"]["split_by_t3"], ["ZZF"])
            self.assertEqual(cad["ca_div"]["slip"], 70.0)
            self.assertEqual(cad["trust_cg"]["slip"], 20.0)
            self.assertEqual(cad["roc"]["books"], 0.0)
            self.assertEqual(cad["roc"]["status"], "differs")
            self.assertEqual(g["record_year"], [
                {"symbol": "ZZF.TO", "paid": "2026-01-07",
                 "counted_in": "2025"}])
            (tt,) = rep["suggestions"]["tt_lines"]
            self.assertEqual(tt["account"], "rbc")
            self.assertEqual(tt["lines"], [
                "ADJUST 2026-01-07 16:00:00 ZZF.TO CAD -10.00 type=roc "
                "record=2025-12-30",
                "DIVIDEND 2026-01-07 16:00:00 ZZF.TO 0 CAD 0 -10.00 "
                "record=2025-12-30 label=distribution"])
            # Coverage: income with no slip; a slip with no income; an
            # empty slip; a small interest with no slip is not a gap.
            kinds = {(i["account"], i["kind"]) for i in rep["issues"]}
            self.assertIn(("spare", "no-slip"), kinds)
            self.assertIn(("ghost", "no-books"), kinds)
            self.assertIn(("rbc", "empty-slip"), kinds)
            self.assertNotIn("tiny", {i["account"] for i in rep["issues"]})
            # An aggregated T5008 beside the books' dispositions
            # (information; reconcile-slips reconciles per security).
            self.assertEqual(rep["t5008"], [{
                "account": "ghost", "class": "securities", "codes": ["SHS"],
                "slip_proceeds": 120.0, "books_proceeds": 120.0,
                "slip_cost": 100.0, "books_cost": 100.0,
                "at_annual_average": False}])
            tiny = [e for e in rep["coverage"]["no_slip"]
                    if e["account"] == "tiny"]
            self.assertTrue(tiny and tiny[0]["small_interest_only"])
            # Shown to a person (width 120): the house style.
            from _style import assert_labelled, assert_styled
            shown = tj(root, home, "slip-audit", width=120)
            assert_styled(self, shown.stdout)
            assert_labelled(self, shown.stdout + shown.stderr)
            txt = tj(root, home, "slip-audit").stdout
            for frag in ("SLIP AUDIT — tax year 2025", "BOOKS CAD DAILY",
                         "SLIP CAD AVERAGE",
                         "spare: Canadian dividends 40.00 CAD is on no "
                         "slip", "below the T5 minimum"):
                self.assertIn(frag, txt)
            # The record-date notes and each coverage line's sources moved
            # behind --details (docs/output-style.md, Essentials first).
            txt = tj(root, home, "slip-audit", "--details").stdout
            for frag in ("Counted in 2025: ZZF.TO",
                         "spare: Canadian dividends 40.00 CAD from "
                         "spare.tt is on no slip",
                         "no T5 is issued below 50"):
                self.assertIn(frag, txt)

            _apply(root, rep)
            self.assertEqual(tj(root, home, "run", "--no-input").returncode,
                             0)
            ro = json.loads(tj(root, home, "roc-sum", "--json").stdout)
            self.assertEqual(ro["totals_taxable"], {"CAD": 10.0})
            rep2 = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            (g2,) = next(a for a in rep2["accounts"]
                         if a["account"] == "rbc")["groups"]
            cad2 = {ln["category"]: ln for ln in
                    {x["currency"]: x for x in g2["buckets"]}["CAD"]["lines"]}
            for c in ("ca_div", "trust_cg", "roc"):
                self.assertEqual(cad2[c]["status"], "ok", cad2[c])
            self.assertEqual(rep2["suggestions"]["tt_lines"], [])

    def test_template(self):
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _typed_project(tmp)
            (root / "inputs" / "slips" / "slips.toml").unlink()
            self.assertEqual(tj(root, home, "run", "--no-input").returncode,
                             0)
            r = tj(root, home, "slip-audit", "--template")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = tomllib.loads(r.stdout)
            got = {(s["account"], s["currency"]) for s in doc["slip"]}
            self.assertEqual(got, {("rbc", "CAD"), ("rbc", "USD"),
                                   ("spare", "CAD"), ("tiny", "CAD")})
            self.assertIn("# 15 = 0.00   # foreign income  (the books "
                          "have some)", r.stdout)
            # Saved as is, every slip is still to be typed.
            (root / "inputs" / "slips" / "slips.toml").write_text(r.stdout)
            rep = json.loads(tj(root, home, "slip-audit", "--json").stdout)
            self.assertEqual({i["kind"] for i in rep["issues"]},
                             {"empty-slip", "no-slip"})
            r = tj(root, home, "checklist", "--json")
            st = {s["id"]: s for s in json.loads(r.stdout)["steps"]}
            self.assertEqual(st["t5-t3"]["status"], "attention")

    def test_no_slips_is_todo(self):
        from taxjson.lib import checklist as cl
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            home = _home(tmp)
            root = _typed_project(tmp)
            (root / "inputs" / "slips" / "slips.toml").unlink()
            self.assertEqual(tj(root, home, "run", "--no-input").returncode,
                             0)
            cfg = tomllib.loads((root / "taxjson.toml").read_text())
            ctx = cl.Ctx(root=root, cfg=cfg, year=2025,
                         today=datetime.date(2026, 3, 1),
                         run_sub=lambda *a, **k: (1, "", ""))
            r = cl.d_t5_t3(ctx)
            self.assertEqual(r.status, "todo")
            self.assertIn("slips.toml", r.detail)
            r = tj(root, home, "slip-audit")
            self.assertEqual(r.returncode, 1)
            self.assertIn("No slips in inputs/slips/", r.stdout)


class TestCountry(unittest.TestCase):
    """T5 / T3 slips are Canadian: a US project refuses the command and
    its checklist step stays a comparison by hand."""

    @rule("CA-SLIP-01")
    @rule_absent("CA-SLIP-01", country="usa")
    def test_us_refuses(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "usa"\n'
                'base_currency = "USD"\n[accounts.margin]\n'
                'type = "taxable"\n')
            r = tj(root, _home(root), "slip-audit")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("slip-audit", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("TAXABLE", cl.step_meta("t5-t3", "usa")[3])
            self.assertEqual(cl.step_meta("t5-t3", "canada")[3],
                             "taxjson slip-audit")


if __name__ == "__main__":
    unittest.main()
