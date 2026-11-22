"""Shared test helpers: T5 and T3 slips against the books' income.

Shared from: test_slip_audit
"""
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import _paths


REPO = _paths.REPO
SRC = REPO / "src"

IB_ACCT = "U5550001"  # pii-ok (synthetic)
HOLDER = "Zed Synthetic-Holder"


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
