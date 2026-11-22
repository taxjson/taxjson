"""Backlog fixes 2026-10-29, group `parsers`: the broker parsers.

TJ-0376 (one account's Webull downloads pasted into one file lost each
download's row order), TJ-0237 (two accounts' Webull downloads pasted
into one file were booked under the first account), TJ-0378 (a Kraken
fiat reward with its fee in another currency booked the USD net as its
own currency), TJ-0379 (a US Kraken stablecoin reward's separately paid
fee came off the coins' cost), TJ-0383 (a negative Coinbase reward was
booked as positive income), TJ-0391 (Coinbase Send/Receive rows lost
their IDs, and two exports' distinct sends folded into one), TJ-0382
(generic [actions] keys alike but for case silently replaced one
another).
Synthetic data only; account ids are fake (pii-ok).

Formerly: test_fix_1029_parsers
"""
import contextlib
import dataclasses
import io
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.webull import WebullBrokerage
from tax_rules import rule
from taxjson.rules import ids as R


def setUpModule():
    # Crypto UTC stamps need a named zone outside a project.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_WB_PRE = (",,,,,,,,\n"
           "Account Number / Numéro de compte:,,,,,,,55500001,\n"  # pii-ok: synthetic id
           "Year / Année:,,,,,,,2025,\n"
           "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES "
           "TRANSACTIONS,\n")
_WB_H = ('"Currency\nDevise",Date,"Action Code\nCode d\'action",'
         '"Symbol\nSymbole","Security Description\nDescription des titres",'
         'Type Code of Securities Code de genre de titres,'
         '"Quantity of Securities Quantité\nde titres","Price\nPrix",'
         '"Proceeds of\nDisposition or Settlement Amount Produits de '
         'disposition"\n')
# Two newest-first downloads of one account, no row in common: March
# (a sale and a same-day rebuy after a buy) and June.
_WB_MARCH = (_WB_PRE + _WB_H
             + "CAD,05-03-2025,BUY,ZZW,ZZW CORP,SHS,10,20,(200)\n"
             "CAD,05-03-2025,SELL,ZZW,ZZW CORP,SHS,-10,30,300\n"
             "CAD,03-03-2025,BUY,ZZW,ZZW CORP,SHS,10,10,(100)\n")
_WB_JUNE = (_WB_PRE + _WB_H
            + "CAD,09-06-2025,SELL,ZZW,ZZW CORP,SHS,-10,40,400\n"
            "CAD,06-06-2025,BUY,ZZW,ZZW CORP,SHS,10,30,(300)\n")


def _webull(text, name="webull_2025.csv"):
    """The Webull parser's rows for `text`, alone in a folder of its
    own."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            return WebullBrokerage().parse_file(p)


def _ca_gain(rows):
    from taxjson.engine.ca.taxrules import CanadaTaxRules
    from taxjson.model.transaction import TaxTransaction
    allowed = {f.name for f in dataclasses.fields(TaxTransaction)}
    txs = [TaxTransaction(**{k: v for k, v in r.items() if k in allowed})
           for r in rows]
    return CanadaTaxRules().compute_gains(
        txs, detect_wash_sales=False)["summary"]["total_gain"]


class TestWebullPastedDownloads(unittest.TestCase):
    """TJ-0376: one account's newest-first downloads pasted into one
    file were read as one block: the dates go both ways across the
    blocks, so neither was read bottom-up and a same-day sale and rebuy
    replayed rebuy first. Each download keeps its own order now.

    Formerly: test_fix_1029_parsers.TestWebullPastedDownloads"""

    @rule(R.CA_DATE_14)
    @rule(R.US_DATE_13)
    def test_each_download_keeps_its_own_order(self):
        split = _webull(_WB_MARCH) + _webull(_WB_JUNE)
        together = _webull(_WB_MARCH + _WB_JUNE)
        self.assertEqual(
            [(t["date_settle"], t["quantity"]) for t in together],
            [(t["date_settle"], t["quantity"]) for t in split])
        march5 = [t["quantity"] for t in together
                  if t["date_settle"] == "2025-03-05"]
        self.assertEqual(march5, [-10.0, 10.0])     # the sale first
        self.assertAlmostEqual(_ca_gain(together), 350.0)
        self.assertAlmostEqual(_ca_gain(split), 350.0)

    @rule(R.CA_DATE_14)
    def test_one_date_download_follows_the_others(self):
        # A download whose rows are all at one date cannot show its
        # direction: it follows the file's other downloads.
        one_day = (_WB_PRE + _WB_H
                   + "CAD,12-06-2025,BUY,ZZW,ZZW CORP,SHS,5,30,(150)\n"
                   "CAD,12-06-2025,SELL,ZZW,ZZW CORP,SHS,-5,31,155\n")
        rows = _webull(_WB_MARCH + one_day)
        june12 = [t["quantity"] for t in rows
                  if t["date_settle"] == "2025-06-12"]
        self.assertEqual(june12, [-5.0, 5.0])


if __name__ == "__main__":
    unittest.main()
