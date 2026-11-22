"""Pre-release review of the dated renames (v0.24 dated events).

- H2: `tjs format-map --write` never changes the books: it simulates
  the rename events the run books after the move (the project's .tt lines
  plus the moved map lines) and refuses, writing nothing, unless they are
  the ones it books now; a moved line that repeats a .tt line written
  differently is reported. Every declaration of one change is one event
  whatever order the files are read in (earliest date; late= per account).
- H3: dated renames apply in date order, and an account holds OLD when an
  earlier rename moved a position into it (a chain A -> B -> C).
- H5 / M8: a .tt RENAME applies only to accounts of its declaring
  account's kind (securities or crypto).
- M3: contradictory declarations stop the run naming the lines (a swap or
  cycle, one change on two dates).
- M4: late= describes one account's rows: its own account's late rows,
  and those of accounts without a line of their own.
- Lows: --check, symlinked inputs folder, idempotent .tt append, the
  dated_events.state of a failed run, read_state guards, an OLD row on
  the event date, the bounded hint regexes, map rules beside a .tt
  RENAME, home_account and crypto accounts, the legacy JOURNAL wording.

Every fixture is SYNTHETIC: invented QZ* tickers, fake account names.

Formerly: test_fix_rename_review
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import dated_events as DE
from taxjson.lib import renames as RN
from taxjson.lib.rename_decl import DatedRename
from tax_rules import rule
from tax_rules.dual import cli, projects_both
from taxjson.rules import ids as R

_HOME = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _sum(root):
    r = cli(root, "sum", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def _run(tc, root, *extra):
    r = cli(root, "run", "--no-input", *extra)
    tc.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


def _bt(day, sym, qty, cur, price):
    return (f"BUYSELL {day} 10:00:00 {sym} {qty} {cur} {price:.2f} "
            f"{abs(qty) * price:.2f} 0.00\n")


# ------------------------------------------------------------ H5 / M8

_KR_HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
           "margin,misc,ledgers\n")


def _kraken(pair):
    return (_KR_HDR
            + f"TXA1,OA1,{pair},2025-01-15 10:00:00.1234,buy,limit,50000,"
              f"50000,0,1.0,,,\n"
            + f"TXA2,OA2,{pair},2025-06-02 14:30:00.5678,sell,limit,60000,"
              f"60000,0,1.0,,,\n")


class TestRenameKindPartition(unittest.TestCase):
    """Formerly: test_fix_rename_review.TestRenameKindPartition"""

    def _check(self, country):
        x, cur = _HOME[country]
        accounts = ('[accounts.margin]\ntype = "taxable"\n\n'
                    '[accounts.kx]\ntype = "taxable"\ncrypto = true\n')
        files = {
            # A securities account declares a change of a symbol a coin
            # also uses: it must not move the coin.
            "inputs/margin/m.tt": (
                _bt("2024-01-10", f"QZS.{x}", 10, cur, 10.0)
                + "RENAME 2025-04-01 BTC QZBTC\n"
                + _bt("2025-06-03", f"QZS.{x}", -10, cur, 11.0)),
            "inputs/kx/kr_trades.csv": _kraken(f"BTC/{cur}")}
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, accounts=accounts, files=files)[country]
            _run(self, root)
            base = json.loads((root / "work" / "kx_base.json").read_text())
            self.assertFalse([t for t in base["transactions"]
                              if t["action"] == "SPLIT"])
            self.assertFalse([t for t in base["transactions"]
                              if t["symbol"] == "QZBTC"])
            self.assertAlmostEqual(_sum(root)["totals"]["total"],
                                   10000.0 + 10.0, delta=0.011)

    @rule(R.CA_CRYPTO_RENAME)
    def test_canada_security_rename_never_moves_a_coin(self):
        self._check("canada")

    @rule(R.US_CRYPTO_RENAME)
    def test_usa_security_rename_never_moves_a_coin(self):
        self._check("usa")

    def _apply(self, kind_decl, kind_books):
        from taxjson.model.transaction import TaxTransaction
        rows = [TaxTransaction(action="BUYSELL", date="2024-01-10",
                               time="10:00:00", date_settle="2024-01-10",
                               symbol="QZC", quantity=1.0, price=10.0,
                               net_amount=-10.0, currency="CAD",
                               account="acct")]
        dr = DatedRename("QZC", "QZD", "2025-04-01", "", "x.tt:1",
                         source="tt", account="decl", kind=kind_decl)
        import io
        out = RN.apply_dated_renames(rows, [dr], stream=io.StringIO(),
                                     kind=kind_books)
        return [t.action for t in out]

    @rule(R.CA_CRYPTO_RENAME)
    def test_canada_a_coin_rename_books_in_crypto_accounts_only(self):
        self.assertEqual(self._apply("crypto", "securities"), ["BUYSELL"])
        self.assertEqual(self._apply("crypto", "crypto"),
                         ["BUYSELL", "SPLIT"])
        self.assertEqual(self._apply("securities", "crypto"), ["BUYSELL"])
        # A legacy map line (no kind) books everywhere.
        self.assertEqual(self._apply("", "crypto"), ["BUYSELL", "SPLIT"])

    @rule(R.US_CRYPTO_RENAME)
    def test_usa_a_coin_rename_books_in_crypto_accounts_only(self):
        self.assertEqual(self._apply("crypto", "securities"), ["BUYSELL"])
        self.assertEqual(self._apply("securities", "securities"),
                         ["BUYSELL", "SPLIT"])

    def test_home_account_is_never_a_crypto_account_for_a_security(self):
        with tempfile.TemporaryDirectory() as td:
            accounts = {"cx": {"type": "taxable", "crypto": True},
                        "margin": {"type": "taxable"}}
            self.assertEqual(DE.home_account(Path(td), accounts, "QZA.TO",
                                             "QZB.TO", "2025-04-01")[0],
                             "margin")
            # Its books name the coin: the crypto account.
            (Path(td) / "work").mkdir()
            (Path(td) / "work" / "cx_base.json").write_text(json.dumps(
                {"transactions": [{"action": "BUYSELL", "symbol": "QZA",
                                   "date": "2024-01-01"}]}))
            self.assertEqual(DE.home_accounts(Path(td), accounts, "QZA",
                                              "QZB", "2025-04-01"),
                             [("cx", "its books hold QZA or QZB")])


if __name__ == "__main__":
    unittest.main()
