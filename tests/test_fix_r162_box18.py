"""R1-62: T5 box 18 capital-gains dividends (split-share and mutual-fund
corporations) are booked as ordinary dividends — no export labels them.
A Canada-owned taxjson.toml table, [[capital_gains_dividends]], names them;
divs-sum shows them apart and the Canadian estimate taxes them as a
capital gain (50% inclusion, no gross-up or credit). A US project
refuses the table (tax-logic CA-INC-06, CA-CTRY-02/US-CTRY-02).

All data is synthetic (fake account ids, invented tickers).
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import country as C
from taxjson.lib.cg_dividends import (CgDividendMapError, allocate,
                                      parse_map)
from taxjson.lib.tax_estimate import estimate_canada
from tax_rules import rule, rule_absent
from tax_rules.dual import cli

ACCT = "55500001"  # pii-ok (synthetic)


def _div(sym, date, amt, cur="CAD", rid=None):
    return {"action": "DIVIDEND", "symbol": sym, "date": date,
            "gross_amount": amt, "net_amount": amt, "currency": cur,
            "id": rid or f"{sym}-{date}"}


def _date(t):
    return t["date"]


class TestMapParsing(unittest.TestCase):

    def test_entries(self):
        e = parse_map("# comment\nzzs.to 2025 all\n"
                      "ZZX.TO 2025-09-10 5.50 margin  # trailing\n")
        self.assertEqual([(x.line, x.symbol, x.when, x.amount, x.account)
                          for x in e],
                         [(2, "ZZS.TO", "2025", None, ""),
                          (3, "ZZX.TO", "2025-09-10", 5.5, "margin")])

    def test_bad_lines_name_the_line(self):
        for text, frag in (("ZZS.TO 2025\n", "line 1"),
                           ("\nZZS.TO 25 all\n", "line 2"),
                           ("ZZS.TO 2025 -3\n", "positive"),
                           ("ZZS.TO 2025 abc\n", "AMOUNT")):
            with self.subTest(text=text):
                with self.assertRaises(CgDividendMapError) as cm:
                    parse_map(text)
                self.assertIn(frag, str(cm.exception))
                self.assertIn("capital_gains_dividends.map", str(cm.exception))


    def test_config_entries_read_like_the_old_map(self):
        import datetime as dt
        from taxjson.lib.cg_dividends import entries_from_config
        cfg = {"accounts": {"margin": {"type": "taxable"}},
               "capital_gains_dividends": [
                   {"symbol": "zzs.to", "year": 2025, "amount": "all"},
                   {"symbol": "ZZX.TO", "date": dt.date(2025, 9, 10),
                    "amount": 5.5, "account": "margin"}]}
        self.assertEqual(
            [(x.line, x.symbol, x.when, x.amount, x.account)
             for x in entries_from_config(cfg)],
            [(1, "ZZS.TO", "2025", None, ""),
             (2, "ZZX.TO", "2025-09-10", 5.5, "margin")])
        for bad, frag in (
                ({"symbol": "ZZS.TO", "amount": "all"}, "exactly one"),
                ({"symbol": "ZZS.TO", "year": 2025, "amount": -3},
                 "amount"),
                ({"symbol": "ZZS.TO", "year": 2025, "amount": "all",
                  "account": "tfsa"}, "not an [accounts.*]"),
                ({"symbol": "ZZS.TO", "year": 2025, "amount": "all",
                  "when": 1}, "unknown key")):
            with self.assertRaises(CgDividendMapError) as cm:
                entries_from_config(dict(cfg, capital_gains_dividends=[bad]))
            self.assertIn(frag, str(cm.exception))
            self.assertIn("[[capital_gains_dividends]] #1",
                          str(cm.exception))
        with self.assertRaises(CgDividendMapError) as cm:
            entries_from_config(dict(cfg, capital_gains_dividends=[
                cfg["capital_gains_dividends"][0]] * 2))
        self.assertIn("repeats entry #1", str(cm.exception))


class TestAllocate(unittest.TestCase):
    rows = [("margin", _div("ZZS.TO", "2025-01-10", 100.0)),
            ("margin", _div("ZZS.TO", "2025-02-10", 300.0)),
            ("margin", _div("ZZS.TO", "2024-12-10", 50.0)),
            ("tfsa", _div("ZZS.TO", "2025-01-10", 70.0, rid="t1"))]

    def test_year_all_covers_taxable_accounts_only(self):
        f = allocate(parse_map("ZZS.TO 2025 all\n"), self.rows,
                     date_of=_date, default_accounts={"margin"})
        self.assertEqual(f, {("margin", "ZZS.TO-2025-01-10"): 1.0,
                             ("margin", "ZZS.TO-2025-02-10"): 1.0})

    def test_amount_is_shared_pro_rata(self):
        f = allocate(parse_map("ZZS 2025 100\n"), self.rows,
                     date_of=_date, default_accounts={"margin"})
        self.assertAlmostEqual(f[("margin", "ZZS.TO-2025-01-10")], 0.25)
        self.assertAlmostEqual(f[("margin", "ZZS.TO-2025-02-10")], 0.25)

    def test_date_and_account(self):
        f = allocate(parse_map("ZZS.TO 2025-01-10 35 tfsa\n"), self.rows,
                     date_of=_date, default_accounts={"margin"})
        self.assertEqual(f, {("tfsa", "t1"): 0.5})

    def test_loud_errors(self):
        cases = (("ZZQ.TO 2025 all\n", "matches no dividend"),
                 ("ZZS.TO 2023 all\n", "matches no dividend"),
                 ("ZZS.TO 2025 401\n", "more than"),
                 ("ZZS.TO 2025 all\nZZS.TO 2025-01-10 all\n",
                  "already named"))
        for text, frag in cases:
            with self.subTest(text=text):
                with self.assertRaises(CgDividendMapError) as cm:
                    allocate(parse_map(text), self.rows, date_of=_date,
                             default_accounts={"margin"})
                self.assertIn(frag, str(cm.exception))
        mixed = self.rows + [("margin", _div("ZZS.TO", "2025-03-10", 5.0,
                                             cur="USD"))]
        with self.assertRaises(CgDividendMapError) as cm:
            allocate(parse_map("ZZS.TO 2025 10\n"), mixed, date_of=_date,
                     default_accounts={"margin"})
        self.assertIn("ONE currency", str(cm.exception))


class TestEstimateCanada(unittest.TestCase):

    @rule("CA-INC-06")
    def test_cg_dividend_is_a_half_included_gain(self):
        kw = dict(realized=10000.0, foreign_div=0.0, pil=0.0,
                  other_income=60000.0, other_losses=0.0, province="ON",
                  year=2025)
        as_div = estimate_canada(eligible_div=1000.0, **kw)
        as_cg = estimate_canada(eligible_div=0.0,
                                capital_gains_dividends=1000.0, **kw)
        self.assertAlmostEqual(as_cg["taxable_gain"], 5500.0)
        self.assertAlmostEqual(as_cg["grossed_eligible"], 0.0)
        self.assertAlmostEqual(as_cg["capital_gains_dividends"], 1000.0)
        self.assertAlmostEqual(as_cg["investment_income"],
                               as_div["investment_income"])
        self.assertNotAlmostEqual(as_cg["estimated_tax"],
                                  as_div["estimated_tax"], places=1)
        # The AMT base takes the gain at 100% (like any capital gain).
        self.assertAlmostEqual(as_cg["amt"]["adjusted_income"],
                               as_div["amt"]["adjusted_income"])


_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
       "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
       "Activity Type,Account Type\n")


def _project(td, country, *, with_map):
    cur = C.HOME_CURRENCY[country]
    root = Path(td) / country
    (root / "inputs" / "margin").mkdir(parents=True)
    rows = [
        f"2025-01-03 09:30:00 AM,2025-01-06 12:00:00 AM,Buy,ZZS.TO,"
        f"ZZS SPLIT CORP CL A WE ACTED AS AGENT,1000,10.00,-10000.00,0.00,"
        f"-10000.00,{cur},{ACCT},Trades,Individual margin",
        f"2025-03-10 12:00:00 AM,2025-03-10 12:00:00 AM,DIV,ZZS.TO,"
        f"ZZS SPLIT CORP CL A DIV ON 1000 SHS REC 02/28/25 PAY 03/10/25,0,"
        f"0.00,0.00,0.00,100.00,{cur},{ACCT},Dividends,Individual margin",
        f"2025-04-10 12:00:00 AM,2025-04-10 12:00:00 AM,DIV,ZZS.TO,"
        f"ZZS SPLIT CORP CL A DIV ON 1000 SHS REC 03/31/25 PAY 04/10/25,0,"
        f"0.00,0.00,0.00,100.00,{cur},{ACCT},Dividends,Individual margin",
        f"2025-06-02 09:30:00 AM,2025-06-03 12:00:00 AM,Sell,ZZS.TO,"
        f"ZZS SPLIT CORP CL A WE ACTED AS AGENT,-1000,11.00,11000.00,0.00,"
        f"11000.00,{cur},{ACCT},Trades,Individual margin",
    ]
    (root / "inputs" / "margin" / "questrade.csv").write_text(
        _QT + "\n".join(rows) + "\n")
    prov = 'province = "ON"\n' if country == C.CANADA else ""
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2025\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n{prov}'
        f'[accounts.margin]\ntype = "taxable"\n')
    r = cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr[-2000:]
    if with_map:
        # Added after the run: only the income views read the table
        # (and a US project's config refuses it, `run` included).
        _add_cgd(root, ("ZZS.TO", "date = 2025-03-10", '"all"'),
                 ("ZZS.TO", "date = 2025-04-10", "40.00"))
    return root


def _add_cgd(root, *entries):
    with (root / "taxjson.toml").open("a") as f:
        for sym, when, amt in entries:
            f.write(f'\n[[capital_gains_dividends]]\nsymbol = "{sym}"\n'
                    f'{when}\namount = {amt}\n')


class TestEndToEnd(unittest.TestCase):

    @rule("CA-INC-06")
    @rule_absent("CA-INC-06", country="usa")
    @rule("US-CTRY-02")
    def test_map_in_both_countries(self):
        with tempfile.TemporaryDirectory() as td:
            ca = _project(Path(td) / "a", C.CANADA, with_map=True)
            ca0 = _project(Path(td) / "b", C.CANADA, with_map=False)
            us = _project(Path(td) / "c", C.USA, with_map=True)

            d = json.loads(cli(ca, "divs-sum", "--json").stdout)
            d0 = json.loads(cli(ca0, "divs-sum", "--json").stdout)
            self.assertEqual(d0["totals"], {"CAD": 200.0})
            self.assertEqual(d0.get("capital_gains_dividends"), None)
            # 100 (all of the Mar-10 payment) + 40 of the Apr-10 one.
            self.assertEqual(d["totals"], {"CAD": 60.0})
            self.assertEqual(d["totals_taxable"], {"CAD": 60.0})
            cg = d["capital_gains_dividends"]
            self.assertEqual(cg["totals"], {"CAD": 140.0})
            self.assertEqual(cg["totals_taxable"], {"CAD": 140.0})
            self.assertEqual(cg["rows"], [{"symbol": "ZZS.TO",
                                           "currency": "CAD",
                                           "amount": 140.0}])
            txt = cli(ca, "divs-sum").stdout
            self.assertIn("CAPITAL-GAINS DIVIDENDS", txt)
            self.assertIn("17400", txt)

            e = json.loads(cli(ca, "estimate", "--json").stdout)["estimate"]
            e0 = json.loads(cli(ca0, "estimate", "--json").stdout)[
                "estimate"]
            self.assertAlmostEqual(e["capital_gains_dividends"], 140.0)
            self.assertAlmostEqual(e0["capital_gains_dividends"], 0.0)
            # Sale gain 1,000 + 140 box 18, at 50%.
            self.assertAlmostEqual(e0["taxable_gain"], 500.0)
            self.assertAlmostEqual(e["taxable_gain"], 570.0)
            self.assertAlmostEqual(e0["grossed_eligible"], 276.0)
            self.assertAlmostEqual(e["grossed_eligible"], 82.8)
            etxt = cli(ca, "estimate").stdout
            self.assertIn("Capital-gains dividends", etxt)

            # The ledger is untouched: same gains file either way.
            g = json.loads((ca / "work" / "margin_gains.json").read_text())
            g0 = json.loads((ca0 / "work" / "margin_gains.json").read_text())
            self.assertEqual(g["summary"]["total_gain"],
                             g0["summary"]["total_gain"])

            # A US project refuses the Canada-only table.
            for args in (("divs-sum", "--json"), ("estimate", "--json")):
                r = cli(us, *args)
                self.assertNotEqual(r.returncode, 0, args)
                self.assertIn("[capital_gains_dividends]", r.stderr)
                self.assertIn("Canada-only", r.stderr)

    @rule("CA-INC-06")
    def test_bad_entry_is_an_error_naming_the_line(self):
        with tempfile.TemporaryDirectory() as td:
            ca = _project(td, C.CANADA, with_map=False)
            _add_cgd(ca, ("ZZS.TO", "date = 2025-03-10", '"all"'),
                     ("ZZQ.TO", "year = 2025", '"all"'))
            for args in (("divs-sum",), ("estimate",)):
                r = cli(ca, *args)
                self.assertNotEqual(r.returncode, 0, args)
                self.assertIn("[[capital_gains_dividends]] #2", r.stderr)


if __name__ == "__main__":
    unittest.main()
