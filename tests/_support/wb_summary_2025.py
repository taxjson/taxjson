"""Shared test helpers: the Webull Trading Summary headers, a 2025 statement's top and a parse giving the rows and stderr.

Shared from: test_fix_m_parsers2_webull
"""
import contextlib
import io
import tempfile
from pathlib import Path

from taxjson.lib.brokerages.webull import WebullBrokerage
from taxjson.parsers.settings import ParseSettings
from _siblings import with_siblings


def _wb():
    """Webull with a $1.00 exercise/assignment charge configured
    (`[accounts.X] exercise_fee = 1.00`): these fixtures carry that
    charge, and nothing is inferred without the setting (B11)."""
    p = with_siblings(WebullBrokerage())
    p.configure(ParseSettings(exercise_fee=1.00))
    return p


_PRE = (",,,,,,,,\n"
        "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
        "Year / Année:,,,,,,,2025,\n"
        "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,\n")
_H24 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')
_H25 = _H24.replace('"Price\nPrix",', '"Price\nPrix",,')


def _parse(text, name="wb.csv"):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / name
        f.write_text(text, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tx = _wb().parse_file(f)
        return tx, err.getvalue()
