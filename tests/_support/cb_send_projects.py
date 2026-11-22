"""Shared test helpers: Coinbase rows and projects for crypto sends.

Shared from: test_fix_a2_crypto_sends
"""
import json
from pathlib import Path

from _support.crypto_sends import _cad_usd_rates_file, _rates_file


CB_HEADER = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
             "Price Currency,Price at Transaction,Subtotal,"
             "Total (inclusive of fees and/or spread),Fees and/or Spread,"
             "Notes\n")


def _proj(td, accounts, *, country="canada"):
    """A project with `accounts` {name: {file name: text}}; every
    account is a taxable crypto account (in this order)."""
    root = Path(td) / "proj"
    base = "USD" if country == "usa" else "CAD"
    src = "CAD" if country == "usa" else "USD"
    toml = (f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{src}"]\n')
    for name, files in accounts.items():
        toml += f'[accounts.{name}]\ntype = "taxable"\ncrypto = true\n'
        d = root / "inputs" / name
        d.mkdir(parents=True)
        for fn, text in files.items():
            (d / fn).write_text(text)
    (root / "taxjson.toml").write_text(toml)
    (root / "work").mkdir()
    if country == "usa":
        _cad_usd_rates_file(root / "work" / "to_base.csv")
    else:
        _rates_file(root / "work" / "to_base.csv")
    home = Path(td) / "home"
    home.mkdir()
    (home / ".crypto_price_cache.json").write_text(json.dumps(
        {"SOL-2025-06-01": 150.0}))
    return root, home


# US: two Coinbase accounts; 1 BTC bought in `bkr`, sent to `acoin`,
# sold there.
US_BKR = (CB_HEADER
          + "u1,2025-05-01 12:00:00 UTC,Buy,BTC,1,USD,100000,100000,100000,"
            "0,Bought 1 BTC\n"
          + "u2,2025-07-01 12:00:00 UTC,Send,BTC,1,USD,105000,105000,"
            "105000,0,Sent 1 BTC\n")
US_ACOIN = (CB_HEADER
            + "v1,2025-07-01 13:00:00 UTC,Receive,BTC,1,USD,105000,105000,"
              "105000,0,Received 1 BTC\n"
            + "v2,2025-07-10 12:00:00 UTC,Sell,BTC,1,USD,110000,110000,"
              "110000,0,Sold 1 BTC\n")
