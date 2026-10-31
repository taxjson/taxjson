"""Issue #76: futures settled per account before the taxable accounts are
pooled.

Each account's books put its plain futures on the settlement basis on
their own (lib/futures.settle_futures, inside the conversion), so the
Canadian engine's cross-account pool (s.47) only ever saw zero-cost
openings and account-local P/L: identical contracts split across two
taxable accounts gave a different gain than in one account. A long in
one account and a short in another were also netted by the engine's
one signed pool per symbol, which moved a realized close into the year
the other account's position closed.

All books are synthetic (fake accounts "acct_a" / "acct_b")."""
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from taxjson.lib.core import TaxTransaction, get_tax_rules
from tax_rules import rule, rule_absent
from tax_rules.dual import cli

RATES = {"USD": {"2025-01-06": Decimal("1.40"),
                 "2025-02-03": Decimal("1.42"),
                 "2025-06-02": Decimal("1.37"),
                 "2026-02-02": Decimal("1.44")}}


def _fut(account, date, qty, net, symbol="F:SYNZ6.US", cur="USD",
         time="10:00:00"):
    return TaxTransaction(
        action="BUYSELL", date=date, date_settle=date, time=time,
        symbol=symbol, quantity=float(qty), currency=cur,
        price=abs(net / qty), net_amount=float(net),
        gross_amount=float(abs(net)), account=account)


def _convert(rows, country="canada", to="CAD"):
    """One account's books, settled and converted on their own — what
    each account's merge2 stage writes to <account>_base.json."""
    from taxjson.bin.taxjson_convert_currency import (
        process_transactions, reset_fallback_tally)
    reset_fallback_tally()
    return process_transactions(rows, to, RATES, Decimal("1.35"),
                                country=country)


def _gains(books, country="canada"):
    """The combined taxable pass over every account's converted books."""
    rows = []
    for b in books:
        rows += _convert(b, country=country,
                         to="CAD" if country == "canada" else "USD")
    res = get_tax_rules(country).compute_gains(rows)
    return [g for g in res["transactions"] if g.get("qty") and "gain" in g]


def _by_year(gains):
    out = {}
    for g in gains:
        y = str(g["date"])[:4]
        out[y] = round(out.get(y, 0.0) + g["gain"], 6)
    return out


# Two identical contracts: 10,000 USD in January, 20,000 USD in
# February; one sold in June 2025 for 30,000, the other in February
# 2026 for 30,000 (net amounts in USD, commission 0).
BUY_A = ("2025-01-06", 1, 10000)
SELL_A = ("2025-06-02", -1, 30000)
BUY_B = ("2025-02-03", 1, 20000)
SELL_B = ("2026-02-02", -1, 30000)


class TestSameContractsAcrossAccounts(unittest.TestCase):
    @rule("CA-FX-04")
    def test_split_across_accounts_matches_one_account(self):
        one = _gains([[_fut("acct_a", *BUY_A), _fut("acct_a", *BUY_B),
                       _fut("acct_a", *SELL_A), _fut("acct_a", *SELL_B)]])
        two = _gains([[_fut("acct_a", *BUY_A), _fut("acct_a", *SELL_A)],
                      [_fut("acct_b", *BUY_B), _fut("acct_b", *SELL_B)]])
        # Average cost of the two open contracts: 15,000 USD. The June
        # close: 30,000 - 15,000 = 15,000 USD at the closing rate.
        self.assertEqual(_by_year(one)["2025"], round(15000 * 1.37, 6))
        self.assertEqual(_by_year(two), _by_year(one))
        self.assertEqual(_by_year(two)["2026"], round(15000 * 1.44, 6))

    @rule("CA-FX-04")
    def test_one_account_unchanged(self):
        # A book whose futures sit in one account keeps its numbers:
        # the per-account settlement already is the taxpayer's pool.
        book = [_fut("acct_a", *BUY_A), _fut("acct_a", *BUY_B),
                _fut("acct_a", *SELL_A), _fut("acct_a", *SELL_B)]
        conv = _convert(book)
        res = get_tax_rules("canada").compute_gains(conv)
        closes = [r for r in conv if r.net_amount]
        got = sorted(g["proceeds"] - g["cost"] for g in res["transactions"]
                     if g.get("qty") and "gain" in g)
        self.assertEqual(got, sorted(r.net_amount for r in closes))

    @rule("CA-FX-04")
    def test_other_contract_in_other_account_not_pooled(self):
        # Pooling is per contract: another delivery month is another
        # property (its own symbol), so nothing moves.
        two = _gains([[_fut("acct_a", *BUY_A), _fut("acct_a", *SELL_A)],
                      [_fut("acct_b", *BUY_B, symbol="F:SYNH6.US"),
                       _fut("acct_b", *SELL_B, symbol="F:SYNH6.US")]])
        self.assertEqual(_by_year(two)["2025"], round(20000 * 1.37, 6))
        self.assertEqual(_by_year(two)["2026"], round(10000 * 1.44, 6))

    @rule("CA-FX-04")
    def test_short_positions_pool_their_opening_proceeds(self):
        # Two shorts opened in two accounts (30,000 and 20,000 USD) and
        # covered separately: each cover is against the average 25,000.
        one = _gains([[_fut("acct_a", "2025-01-06", -1, 30000),
                       _fut("acct_a", "2025-02-03", -1, 20000),
                       _fut("acct_a", "2025-06-02", 1, 21000),
                       _fut("acct_a", "2026-02-02", 1, 22000)]])
        two = _gains([[_fut("acct_a", "2025-01-06", -1, 30000),
                       _fut("acct_a", "2025-06-02", 1, 21000)],
                      [_fut("acct_b", "2025-02-03", -1, 20000),
                       _fut("acct_b", "2026-02-02", 1, 22000)]])
        self.assertEqual(_by_year(one)["2025"], round(4000 * 1.37, 6))
        self.assertEqual(_by_year(two), _by_year(one))


class TestOpposedPositionsAcrossAccounts(unittest.TestCase):
    """A long in one account and a short of the same contract in another
    are two positions: neither closes the other, and each close's P/L is
    in the year that account closed it."""

    @rule("CA-FX-04")
    def test_long_close_is_not_deferred_by_a_short_elsewhere(self):
        two = _gains([
            [_fut("acct_a", "2025-01-06", 1, 10000),
             _fut("acct_a", "2025-06-02", -1, 30000)],      # +20,000
            [_fut("acct_b", "2025-02-03", -1, 20000),
             _fut("acct_b", "2026-02-02", 1, 25000)]])      # -5,000
        y = _by_year(two)
        self.assertEqual(y["2025"], round(20000 * 1.37, 6))
        self.assertEqual(y["2026"], round(-5000 * 1.44, 6))

    @rule("CA-FX-04")
    def test_short_close_is_not_deferred_by_a_long_elsewhere(self):
        two = _gains([
            [_fut("acct_a", "2025-01-06", -1, 30000),
             _fut("acct_a", "2025-06-02", 1, 26000)],       # +4,000
            [_fut("acct_b", "2025-02-03", 1, 20000),
             _fut("acct_b", "2026-02-02", -1, 25000)]])     # +5,000
        y = _by_year(two)
        self.assertEqual(y["2025"], round(4000 * 1.37, 6))
        self.assertEqual(y["2026"], round(5000 * 1.44, 6))


class TestRecordedFacts(unittest.TestCase):
    @rule("CA-FX-04")
    def test_parts_record_leg_money_pl_and_rate(self):
        from taxjson.lib.futures import parse_settle_native
        conv = _convert([_fut("acct_a", *BUY_A), _fut("acct_a", *SELL_A)])
        legs = [parse_settle_native(r.settle_native) for r in conv]
        self.assertEqual([l[0] for l in legs], ["LO", "LC"])
        self.assertEqual(legs[1][2], Decimal("20000.0"))
        self.assertEqual(legs[1][3], "USD")
        self.assertEqual(legs[1][4], Decimal("1.37"))
        # Survives the JSON round trip of work/<account>_base.json.
        back = TaxTransaction(**json.loads(json.dumps(conv[1].to_dict())))
        self.assertEqual(back.settle_native, conv[1].settle_native)

    @rule("CA-FX-04")
    def test_books_without_the_facts_keep_their_accounts_average(self):
        from taxjson.lib.futures import pool_across_accounts
        rows = (_convert([_fut("acct_a", *BUY_A), _fut("acct_a", *SELL_A)])
                + _convert([_fut("acct_b", *BUY_B), _fut("acct_b", *SELL_B)]))
        for r in rows:
            r.settle_native = ""            # books of an older taxjson
        out, notes = pool_across_accounts(rows)
        self.assertEqual([r.net_amount for r in out],
                         [r.net_amount for r in rows])
        self.assertEqual(len(notes), 1)
        self.assertIn("held in several taxable accounts", notes[0])

    @rule("CA-FX-04")
    def test_opposed_positions_open_at_year_end(self):
        rows = (_convert([_fut("acct_a", "2025-01-06", 1, 10000)])
                + _convert([_fut("acct_b", "2025-02-03", -1, 20000)]))
        res = get_tax_rules("canada").compute_gains(rows)
        self.assertEqual(res["summary"]["total_gain"], 0)
        # The long and the other account's short are two positions, never
        # their net (backlog TJ-1103).
        self.assertEqual(sorted((i.get("account", ""), i["qty"])
                                for i in res["inventory"]
                                if i["symbol"] == "F:SYNZ6.US"),
                         [("", 1.0), ("acct_b", -1.0)])


class TestUsaKeepsPerAccountFifo(unittest.TestCase):
    """US (US-FUT-01): a futures close is matched first in, first out
    within its own account, as the US engine keeps basis per account —
    the Canadian cross-account average never applies."""

    @rule("CA-FX-04")
    @rule_absent("CA-FX-04", country="usa")
    @rule("US-FUT-01")
    def test_dual_country_two_accounts(self):
        # acct_a holds the 20,000 contract and sells it in June; acct_b
        # holds the 10,000 one. Canada: the June close is against the
        # average 15,000 (s.47). US: against acct_a's own 20,000.
        books = [[_fut("acct_a", *BUY_B), _fut("acct_a", *SELL_A)],
                 [_fut("acct_b", *BUY_A), _fut("acct_b", *SELL_B)]]
        ca = _by_year(_gains(books, country="canada"))
        us = _by_year(_gains(books, country="usa"))
        self.assertEqual(ca["2025"], round(15000 * 1.37, 6))
        self.assertEqual(us["2025"], 10000.0)
        self.assertEqual(us["2026"], 20000.0)


def _tt(rows):
    return "".join(
        f"BUYSELL {d} 10:00:00 F:SYNZ26.TO {q} CAD {abs(n / q)} {n} 0 x1\n"
        for d, q, n in rows)


@rule("CA-FX-04")
class TestIssue76Pipeline(unittest.TestCase):
    """The issue's reproduction through `tjs run` + `form-export`."""

    def _project(self, root, books):
        cfg = ('[settings]\nyear = 2025\ncountry = "canada"\n'
               'base_currency = "CAD"\nsource_currencies = []\n'
               'local_timezone = "America/Toronto"\n')
        for a, rows in books.items():
            cfg += f'[accounts.{a}]\ntype = "taxable"\ncrypto = false\n'
            d = root / "inputs" / a
            d.mkdir(parents=True, exist_ok=True)
            (d / "trades.tt").write_text(_tt(rows))
        (root / "taxjson.toml").write_text(cfg)
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        f = cli(root, "form-export", "--json")
        self.assertEqual(f.returncode, 0, f.stderr[-2000:])
        return json.loads(f.stdout)["totals"]["gain_all"]

    def test_schedule3_independent_of_the_account(self):
        a = [("2025-01-06", 1, 10000), ("2025-06-02", -1, 30000)]
        b = [("2025-02-03", 1, 20000), ("2026-02-02", -1, 30000)]
        opp_b = [("2025-02-03", -1, 20000), ("2026-02-02", 1, 25000)]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            one = self._project(td / "one", {"acct_a": a + b})
            two = self._project(td / "two", {"acct_a": a, "acct_b": b})
            opp = self._project(td / "opp", {"acct_a": a, "acct_b": opp_b})
        self.assertEqual(one, 15000.0)
        self.assertEqual(two, 15000.0)
        # The June close realized 20,000 in acct_a in 2025; acct_b's
        # short is closed in 2026 and does not defer it.
        self.assertEqual(opp, 20000.0)


if __name__ == "__main__":
    unittest.main()
