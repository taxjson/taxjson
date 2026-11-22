"""Shared test helpers: crypto sends.

Shared from: test_fix_sends
"""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import _paths


REPO_ROOT = _paths.REPO

# Kraken ledger (UTC). America/Toronto is UTC-5 in winter, UTC-4 in
# summer — the local stamps the ids and .tt lines carry are noted.
KR_LEDGER = (
    "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
    # CAD -> USDC instant conversion: the USD pool's first 1000 units
    # cost exactly 1370 CAD.
    "LA1AAA,RA1,2025-01-15 15:00:00,spend,,currency,ZCAD,spot,-1370,0,0\n"
    "LA2AAA,RA1,2025-01-15 15:00:00,receive,,currency,USDC,spot,1000,0,"
    "1000\n"
    # USDC reward, 10 units at the 2025-02-01 rate 1.40 -> cost 14.
    "LB1BBB,RB1,2025-02-01 12:00:00,earn,reward,currency,USDC,spot,10,0,"
    "1010\n"
    # Gift of 100 USDC (+1 USDC withdrawal fee). 07:00 local.
    "LC1CCC,RC1,2025-03-01 12:00:00,withdrawal,,currency,USDC,spot,-100,1,"
    "909\n"
    # ATOM arriving from Coinbase (matched: a self-custody move).
    "LD1DDD,RD1,2025-06-22 14:08:31,deposit,,currency,ATOM,spot,80,0,80\n"
    # USDC reward 9 days after the Coinbase USDC gift below -> that
    # gift's small FX loss is (likely) superficial.
    "LE1EEE,RE1,2025-07-20 12:00:00,earn,reward,currency,USDC,spot,1,0,"
    "910\n"
    # QZL reward so the book holds QZL before the payment.
    "LF1FFF,RF1,2026-01-12 12:00:00,earn,reward,currency,QZL,spot,3,0,3\n"
    # Payment in QZL: 2.5 QZL + a 0.05 QZL network fee the parser
    # books by itself. 17:30:00 local.
    "LG1GGG,RG1,2026-04-13 21:30:00,withdrawal,,currency,QZL,spot,-2.5,"
    "0.05,0.45\n"
    # A Hybrid Earn sweep nobody has classified yet.
    "LH1HHH,RH1,2026-06-01 12:00:00,hybridearnwithdrawal,,currency,USDC,"
    "spot,-5,0,905\n")

# A Coinbase ID as long as a real one (24 hex digits): the listing shows
# its first 2 characters, never the rest.
C6_ID = "c6e0a1b2c3d4e5f6a7b8c9d0"
CB_CSV = (
    "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
    "Price Currency,Price at Transaction,Subtotal,"
    "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n"
    "c1,2025-05-01 12:00:00 UTC,Buy,USDC,20,CAD,1.38,27.60,27.60,0,"
    "Bought 20 USDC\n"
    "c2,2025-06-01 12:00:00 UTC,Buy,BTC,0.002,CAD,140000,280,280,0,"
    "Bought 0.002 BTC\n"
    "c3,2025-06-10 12:00:00 UTC,Buy,ATOM,80.002,CAD,9,720.02,"
    "720.02,0,Bought ATOM\n"
    "c4,2025-07-11 11:20:39 UTC,Send,USDC,10,CAD,1.35,13.50,13.50,0,"
    "Sent 10 USDC to an address\n"
    "c5,2025-06-22 14:05:10 UTC,Send,ATOM,80.002,CAD,9.85,788.02,"
    "788.02,0,Sent ATOM to an address\n"
    f"{C6_ID},2025-08-01 12:00:00 UTC,Send,BTC,0.001,CAD,150000,150,150,0,"
    "Sent BTC to an address\n")

PAY_ID = "kr-20260413T173000-QZL-2.5"
# QZL is a fictional coin. Yahoo lists it under a numbered id, which
# only the project's ticker.map CRYPTO line knows (there is no built-in
# coin id table): every project here carries the line.
CRYPTO_MAP_LINE = "CRYPTO QZL QZL55501\n"
KR_USDC_ID = "kr-20250301T070000-USDC-100"
CB_USDC_ID = "cb-20250711T072039-USDC-10"
HYBRID_ID = "kr-20260601T080000-USDC-5"

SPECIAL_RATES = {"2025-02-01": "1.4000", "2025-03-01": "1.4500",
                 "2026-04-13": "1.3611"}


def _rates_file(path: Path):
    d = date(2025, 1, 1)
    lines = []
    while d <= date.today():
        iso = d.isoformat()
        lines.append(f"{iso} 12:00:00 USD CAD "
                     f"{SPECIAL_RATES.get(iso, '1.3500')} boc")
        d += timedelta(days=1)
    path.write_text("\n".join(lines) + "\n")


def _cad_usd_rates_file(path: Path):
    """CAD->USD rates for a USD (US) project: the fixture ledger buys
    USDC with CAD, which a US project books as a purchase of property
    (stablecoins are property there, tax-logic US-CRYPTO-02)."""
    d = date(2025, 1, 1)
    lines = []
    while d <= date.today():
        lines.append(f"{d.isoformat()} 12:00:00 CAD USD 0.7300 yahoo")
        d += timedelta(days=1)
    path.write_text("\n".join(lines) + "\n")


def _project(td, *, country="canada"):
    root = Path(td) / "proj"
    acct = root / "inputs" / "crypto"
    acct.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "{country}"\n'
        f'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
        f'[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
    (acct / "kr_ledgers.csv").write_text(KR_LEDGER)
    (acct / "cb_2025.csv").write_text(CB_CSV)
    (root / "ticker.map").write_text(CRYPTO_MAP_LINE)
    (root / "work").mkdir()
    _rates_file(root / "work" / "to_base.csv")
    home = Path(td) / "home"
    home.mkdir()
    # The planted rates in the HOME too, for the modules that run this
    # project with a _cli of their own (backlog TJ-0635).
    _home_rates(home)
    (home / ".crypto_price_cache.json").write_text(json.dumps({
        "QZL55501-2026-04-13": 1.84,
        "QZL55501-2026-01-12": 2.1,
    }))
    return root, home


def _home_rates(home: Path) -> None:
    """The rates _rates_file and _cad_usd_rates_file plant, also in the
    test HOME's currency cache (~/.currency_price_cache.json, read by an
    offline rebuild of work/to_base.csv): a Bank of Canada USD->CAD
    series with SPECIAL_RATES, a Yahoo CAD->USD one, every day from
    2025-01-01 to a week past today (made-up rates). `tjs run`
    rebuilds to_base.csv when a taxjson source file is newer than it
    (stage_currency_rates, needs_rebuild); a file edited while the suite
    ran made the rebuild read an empty cache, replace the planted rates
    with none, and stop the run on "no exchange rate" (backlog TJ-0606).
    Written once; a cache the test wrote itself is kept."""
    p = Path(home) / ".currency_price_cache.json"
    if not Path(home).is_dir() or p.exists():
        return
    d, hi = date(2025, 1, 1), date.today() + timedelta(days=7)
    boc, cache = {}, {}
    while d <= hi:
        iso = d.isoformat()
        boc[iso] = float(SPECIAL_RATES.get(iso, "1.3500"))
        cache[f"CADUSD-{iso}"] = 0.73
        d += timedelta(days=1)
    span = [["2025-01-01", hi.isoformat()]]
    cache.update({"_boc": {"USDCAD": {"obs": boc}},
                  "_coverage": {"boc:USDCAD": span, "yahoo:CADUSD": span}})
    p.write_text(json.dumps(cache))


def _env(home):
    _home_rates(home)
    return {**os.environ, "HOME": str(home), "TAXJSON_OFFLINE": "1",
            "TAXJSON_LOCAL_TZ": "America/Toronto", "NO_COLOR": "1",
            "PYTHONPATH": str(REPO_ROOT / "src")}


def _cli(root, home, *a):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=_env(home))
