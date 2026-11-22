"""Shared test helpers: the Webull 2025 Trading Summary preamble and header, an option buy row and a parse.

Shared from: test_fix_l_parsers2_webull
"""
import contextlib
import io
import tempfile
from pathlib import Path

from taxjson.lib.brokerages.webull import WebullBrokerage
from taxjson.lib.country import parse_basis
from taxjson.parsers.settings import ParseSettings


_PRE = (",,,,,,,,\n"
        "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
        "Year / Année:,,,,,,,2025,\n"
        "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES TRANSACTIONS,\n")
_H25 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",,'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')


def _parse(rows, country="canada"):
    # The ITA citation is the Canadian project's wording; the parser
    # cites the law of the country taxjson-brokerage passes (A2-0723).
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "wb.csv"
        f.write_text(_PRE + _H25 + rows, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            wb = WebullBrokerage()
            wb.configure(ParseSettings(exercise_fee=1.00, country=country, **parse_basis(country)))
            tx = wb.parse_file(f)
        return tx, err.getvalue()


# Two KQZ calls (2025-03-21, strike 61) bought.
_OPTION_BUY = 'USD,19-03-2025,BUY,@KQZ,CALL KQZ03/21/25 61,OPC,2,3.15,,(631.98)\n'
