"""Shared test helpers: both Webull Trading Summary layouts, a 2024 statement's top and a parse giving the rows.

Shared from: test_webull_formats
"""
import tempfile
from pathlib import Path

from taxjson.lib.brokerages.webull import WebullBrokerage
from taxjson.parsers.settings import ParseSettings


def _wb():
    """Webull with a $1.00 exercise/assignment charge configured
    (`[accounts.X] exercise_fee = 1.00`): these fixtures carry that
    charge, and nothing is inferred without the setting (B11)."""
    p = WebullBrokerage()
    p.configure(ParseSettings(exercise_fee=1.00))
    return p


_PRE = (",,,,,,,,\n"
        "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
        "Year / Année:,,,,,,,2024,\n"
        "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,\n")
_H24 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')
_H25 = _H24.replace('"Price\nPrix",', '"Price\nPrix",,')


def _parse(text):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "wb.csv"
        f.write_text(text, encoding="utf-8")
        return _wb().parse_file(f)
