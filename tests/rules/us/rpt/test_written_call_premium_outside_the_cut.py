"""Fix round 8, group stats: the performance views and an option's premium
around deliveries, returns of capital and snapshots. Every fixture is
SYNTHETIC: made-up tickers, ids and amounts.

Formerly: test_fix_r8_stats
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from _qa_project import tj


def _project(td, books, *, country="canada", year=2025):
    root = Path(td) / "p"
    for acct, text in books.items():
        (root / "inputs" / acct).mkdir(parents=True)
        (root / "inputs" / acct / "trades.tt").write_text(text)
    (root / "ticker.map").write_text("")
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{"USD" if country == "usa" else "CAD"}"\n'
        'source_currencies = []\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in books))
    tj(root, "run", "--no-input")
    return root


def _stats(rows, *, country="canada", year=2025):
    with tempfile.TemporaryDirectory() as td:
        root = _project(td, {"m": rows}, country=country, year=year)
        return json.loads(tj(root, "stats", str(year), "--json").stdout)


def _pl(doc, cls=None):
    if cls is None:
        return doc["total"]["net_pl"]
    return (doc["classes"].get(cls) or {"net_pl": 0.0})["net_pl"]


class TestACutDoesNotCoverAWrittenCallsPremium(unittest.TestCase):
    """TJ-1272: a written call's premium is in the sale's proceeds, in no
    basis, so a return of capital's cut does not cancel it. Canada had that
    from TJ-1309; the US engine now says it too.

    Formerly: test_fix_r8_stats.TestACutDoesNotCoverAWrittenCallsPremium"""

    def _book(self, x, c, roc, *, put=True, allow=False):
        sym = f"SYN.{x}"
        opt = f"SYN250117P00001000.{x}"
        call = f"SYN250516C00001000.{x}"
        rows = (f"BUYSELL 2025-01-06 10:00:00 {sym} 100 {c} 2 200 0\n"
                f"BUYSELL 2025-01-10 10:00:00 {sym} -100 {c} .8 80 0\n")
        if put:
            rows += (f"BUYSELL 2025-01-02 10:00:00 {opt} -1 {c} .2 20 0\n"
                     f"ASSIGN 2025-01-17 09:30:00 {opt} 1 {c} 0 0 0\n"
                     f"ASSIGN 2025-01-17 10:00:00 {sym} 100 {c} 1 100 0\n")
        else:
            rows += f"BUYSELL 2025-01-17 10:00:00 {sym} 100 {c} 1 100 0\n"
        rows += (f"ADJUST 2025-03-03 10:00:00 {sym} {c} -{roc} type=roc\n"
                 f"BUYSELL 2025-04-01 10:00:00 {call} -1 {c} .3 30 0\n"
                 f"ASSIGN 2025-05-16 09:30:00 {call} 1 {c} 0 0 0\n"
                 f"ASSIGN 2025-05-16 10:00:00 {sym} -100 {c} 1 100 0\n")
        if allow:
            rows += f'ALLOWLOSS 2025-01-10 {sym} reason="control"\n'
        return rows

    @rule(R.US_RPT_12)
    def test_us_a_call_and_a_put_in_one_sale(self):
        for roc in (150, 200):
            with self.subTest(roc=roc):
                doc = _stats(self._book("US", "USD", roc), country="usa")
                self.assertEqual(_pl(doc), 30.0)
                self.assertEqual(
                    _pl(doc), _pl(_stats(self._book("US", "USD", roc, allow=True),
                                         country="usa")))

    @rule(R.US_RPT_12)
    def test_us_only_a_call(self):
        doc = _stats(self._book("US", "USD", 150, put=False), country="usa")
        self.assertEqual(
            _pl(doc), _pl(_stats(self._book("US", "USD", 150, put=False,
                                            allow=True), country="usa")))

    @rule(R.US_RPT_12)
    def test_us_the_sale_across_two_lots_only_one_of_them_cut(self):
        def book(allow):
            rows = ("BUYSELL 2025-01-06 10:00:00 SYN.US 100 USD 2 200 0\n"
                    "BUYSELL 2025-01-10 10:00:00 SYN.US -100 USD .8 80 0\n"
                    "BUYSELL 2025-01-13 10:00:00 SYN.US 100 USD 1 100 0\n"
                    "BUYSELL 2025-03-01 10:00:00 SYN.US 100 USD 1 100 0\n"
                    "ADJUST 2025-03-03 10:00:00 SYN.US USD -300 type=roc\n"
                    "BUYSELL 2025-04-01 10:00:00 SYN250516C00001000.US -2 USD "
                    ".3 60 0\n"
                    "ASSIGN 2025-05-16 09:30:00 SYN250516C00001000.US 2 USD 0 "
                    "0 0\n"
                    "ASSIGN 2025-05-16 10:00:00 SYN.US -200 USD 1 200 0\n")
            return rows + ('ALLOWLOSS 2025-01-10 SYN.US reason="control"\n'
                           if allow else "")
        self.assertEqual(_pl(_stats(book(False), country="usa")),
                         _pl(_stats(book(True), country="usa")))

    @rule(R.US_RPT_12)
    def test_control_a_put_alone_is_still_covered(self):
        rows = ("BUYSELL 2025-01-02 10:00:00 SYN250117P00001000.US -1 USD .2 "
                "20 0\n"
                "BUYSELL 2025-01-06 10:00:00 SYN.US 100 USD 2 200 0\n"
                "BUYSELL 2025-01-10 10:00:00 SYN.US -100 USD .8 80 0\n"
                "ASSIGN 2025-01-17 09:30:00 SYN250117P00001000.US 1 USD 0 0 0\n"
                "ASSIGN 2025-01-17 10:00:00 SYN.US 100 USD 1 100 0\n"
                "ADJUST 2025-03-03 10:00:00 SYN.US USD -150 type=roc\n")
        sale = "BUYSELL 2025-05-16 10:00:00 SYN.US -100 USD 1 100 0\n"
        allow = 'ALLOWLOSS 2025-01-10 SYN.US reason="control"\n'
        self.assertEqual(_pl(_stats(rows + sale, country="usa")),
                         _pl(_stats(rows + sale + allow, country="usa")))

    @rule(R.CA_RPT_16)
    def test_canada_a_call_and_a_put_in_one_sale(self):
        for roc in (150, 200):
            with self.subTest(roc=roc):
                doc = _stats(self._book("TO", "CAD", roc))
                self.assertEqual(_pl(doc), 30.0)


if __name__ == "__main__":
    unittest.main()
