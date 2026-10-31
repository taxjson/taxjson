"""Fix round 2026-10-29 (round 3), group views: the per-account holdings
export, the trading statistics' pooled put premium and edge-cases'
option expiries.

TJ-0598  reports/<account>_holdings.toml valued units that arrived by a
         move of your own from another taxable account (shares, or
         coins) at total_cost 0.0 with no marker: the account's own
         books have no cost for them (CA-ACB-OWN-MOVE), and a reader
         (portoml) applied the 0. The engines now mark a pool or lot
         whose cost the books do not have (`unknown_cost`), and the
         export writes `cost_unknown = true` with no cost figure, naming
         the move.
TJ-0624  stats / win-rates: a put's premium in an average-cost pool
         (TJ-0546) stayed whole on the shares left when shares left the
         pool by a transfer (a registered account's withdrawal in kind):
         the next sale carried the premium the departed shares' cost
         held. A transfer out of the pool takes its part; a move between
         the pool's own accounts, or a custody move's out and in rows,
         does not.
TJ-0623  edge-cases option_expiries replayed a series to the day before
         its expiry: a split or rename dated the expiry day applied
         nowhere, the old series listed as expiring whole and the
         successor's trades that day unread.
TJ-0603  a crypto account folder holding a Kraken and a Coinbase export
         wrote every coin with Kraken's .KR suffix: each coin now takes
         the suffix of the one venue whose rows hold it, bare when
         several do or the venue has none.
TJ-0607  (found fixed by TJ-0343) taxjson-split-gains apportions the
         year-end pools of a trade-date project on trade dates: pinned.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tax_rules import rule
from tax_rules.dual import settings_for
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.tomlcompat import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


# --------------------------------------------------------------------
# The holdings export of units moved in from your own account (TJ-0598)
# --------------------------------------------------------------------

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _qt(acct_no: str, cur: str, *rows) -> str:
    """A Questrade activity export: rows of (date, action, description,
    quantity, price, net amount, activity type)."""
    out = _QH
    for d, act, desc, q, px, net, kind in rows:
        out += (f"{d},{d},{act},ZZQ,{desc},{q},{px:.2f},{net:.2f},0.00,"
                f"{net:.2f},{cur},{kind},{acct_no},Margin\n")
    return out


def _moved_project(root: Path, country: str) -> Path:
    """qa buys 100 ZZQ at 2.00 and moves them out on 2024-03-01; qb
    receives them the next day (a move of your own) and holds them."""
    cur = "CAD" if country == "canada" else "USD"
    accounts = {
        "qa": _qt("99900001", cur,
                  ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0, -200.0,
                   "Trades"),
                  ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT", -100,
                   0.0, 0.0, "Transfers")),
        "qb": _qt("99900002", cur,
                  ("2024-03-02", "TF6", "ZZQ CORP TRANSFER IN", 100, 0.0,
                   0.0, "Transfers"))}
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2024, source_currencies=[])
        + "".join(f'[accounts.{n}]\ntype = "taxable"\n' for n in accounts))
    for n, text in accounts.items():
        (root / "inputs" / n).mkdir(parents=True)
        (root / "inputs" / n / "questrade_2024.csv").write_text(text)
    return root


def _comments(text: str) -> str:
    """The file's text with each comment line's "# " dropped, its
    whitespace collapsed: a comment wrapped over lines reads as one."""
    return " ".join(" ".join(ln[2:] if ln.startswith("# ") else ln
                             for ln in text.splitlines()).split())


def _holdings(root: Path, account: str):
    text = (root / "reports" / f"{account}_holdings.toml").read_text()
    return tomllib.loads(text).get("holding") or [], text


class TestMovedInUnitsHaveNoCostOfZero(unittest.TestCase):
    """TJ-0598: the receiving account's holdings file says the moved
    units' cost is not in its own books (`cost_unknown = true`, no cost
    figure), and where it is; the filing figures are unchanged."""

    def _check(self, country, where):
        with tempfile.TemporaryDirectory() as td:
            root = _moved_project(Path(td) / "p", country)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            # A full run fills the cost in from the passes across
            # accounts (round 4, test_fix_1029_r4_views); one account's
            # run has none to take.
            r = _cli(root, "run", "--no-input", "--account", "qb")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (h,), text = _holdings(root, "qb")
            self.assertEqual(h["quantity"], 100.0)
            self.assertIs(h["cost_unknown"], True)
            for key in ("total_cost", "cost_per_share", "base_total_cost",
                        "base_cost_per_share"):
                self.assertNotIn(key, h)
            flat = _comments(text)
            self.assertIn("cost_unknown: 100 moved in from qa (your own "
                          "account) on 2024-03-01: their cost is not in "
                          "this account's own books, so total_cost and "
                          "cost_per_share are left out. " + where, flat)
            # The sender holds nothing; the filing view has the cost.
            self.assertEqual(_holdings(root, "qa")[0], [])
            rows = json.loads(_cli(root, "list", "--json").stdout)["rows"]
            self.assertEqual([(x["account"], x["qty"],
                               round(x["cost"], 2)) for x in rows],
                             [("qb", 100.0, 200.0)])
            # Read back by taxjson-export: unknown, never a cost of 0.
            from taxjson.bin.taxjson_export import \
                _holdings_toml_to_inventory
            (item,) = _holdings_toml_to_inventory(
                tomllib.loads(text))["inventory"]
            self.assertIs(item["unknown_cost"], True)

    @rule("CA-ACB-OWN-MOVE")
    def test_canada(self):
        self._check("canada", "The ACB is one pool across your taxable "
                              "accounts: `tjs list` gives this "
                              "account's share.")

    @rule("US-BASIS-05")
    def test_usa(self):
        self._check("usa", "The basis moved with the lots: `tjs list` "
                           "gives it.")


class TestEnginesMarkUnknownCost(unittest.TestCase):
    """TJ-0598: an inventory row whose units include some with no cost
    in the books carries `unknown_cost`; a fully costed one does not."""

    def _inventory(self, country, rows):
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.pipeline import GainsRequest, run_gains
        txs = [TaxTransaction(**dict(
            {"currency": "CAD" if country == "canada" else "USD",
             "account": "qb", "time": "10:00:00", "price": 0.0,
             "date_settle": r["date"]}, **r)) for r in rows]
        with contextlib.redirect_stderr(io.StringIO()):
            doc = run_gains(txs, [], req=GainsRequest(
                country=country, year=2024, taxable=True,
                phantom_hint=False))
        return {i["symbol"]: i for i in doc["inventory"]}

    def _rows(self, sfx):
        return [{"action": "BUYSELL", "date": "2024-01-10",
                 "symbol": f"ZZK.{sfx}", "quantity": 10.0,
                 "net_amount": -50.0, "price": 5.0},
                {"action": "BUYSELL", "date": "2024-01-10",
                 "symbol": f"ZZQ.{sfx}", "quantity": 20.0,
                 "net_amount": -40.0, "price": 2.0},
                {"action": "TRANSFER", "type": "lot_move",
                 "date": "2024-03-01", "symbol": f"ZZQ.{sfx}",
                 "quantity": 100.0, "net_amount": 0.0,
                 "description": "own-account move #1: qa -> qb (test)"}]

    @rule("CA-ACB-OWN-MOVE")
    def test_canada(self):
        inv = self._inventory("canada", self._rows("TO"))
        self.assertIs(inv["ZZQ.TO"]["unknown_cost"], True)
        self.assertEqual(inv["ZZQ.TO"]["qty"], 120.0)
        self.assertNotIn("unknown_cost", inv["ZZK.TO"])

    @rule("US-BASIS-05")
    def test_usa(self):
        inv = self._inventory("usa", self._rows("US"))
        self.assertIs(inv["ZZQ.US"]["unknown_cost"], True)
        self.assertNotIn("unknown_cost", inv["ZZK.US"])


class TestExportWritesTheMarker(unittest.TestCase):
    """TJ-0598: render_holdings_toml for a crypto account's books (the
    coins' own moves, TJ-0281) and a holding with no move named."""

    def _args(self, **kw):
        return SimpleNamespace(**dict(
            account_name="kx", inputs=["kx_gains.json"], crypto=True,
            symbol_suffix=None, country="canada", base_currency="CAD",
            dust_threshold=1e-6), **kw)

    def test_a_moved_coin_and_an_opening(self):
        from taxjson.bin.taxjson_export import render_holdings_toml
        agg = {"ZZC": {"qty": 0.5, "total_cost": 0.0, "currency": "CAD",
                       "position_start_date": "2024-03-01",
                       "unknown_cost": True},
               "ZZD": {"qty": 2.0, "total_cost": 0.0, "currency": "CAD",
                       "position_start_date": "2024-01-02",
                       "unknown_cost": True},
               "ZZE": {"qty": 1.0, "total_cost": 30.0, "currency": "CAD",
                       "position_start_date": "2024-01-02"}}
        base = {k: dict(v) for k, v in agg.items()}
        text = "\n".join(render_holdings_toml(
            agg, self._args(), base, None,
            {"ZZC": [("2024-03-01", 0.5, "ky")],
             "ZZD": [("2023-06-01", 1.0, "ky")]}))
        doc = {h["symbol"]: h for h in tomllib.loads(text)["holding"]}
        for sym in ("ZZC", "ZZD"):
            self.assertIs(doc[sym]["cost_unknown"], True)
            self.assertNotIn("total_cost", doc[sym])
            self.assertNotIn("base_cost_per_share", doc[sym])
            self.assertEqual(doc[sym]["currency"], "CAD")
        self.assertNotIn("cost_unknown", doc["ZZE"])
        self.assertEqual(doc["ZZE"]["base_total_cost"], 30.0)
        flat = _comments(text)
        self.assertIn("cost_unknown: 0.5 moved in from ky (your own "
                      "account) on 2024-03-01", flat)
        # ZZD's move was before its current position: not named.
        self.assertIn("cost_unknown: some of these units have no cost "
                      "in this account's own books (an opening at an "
                      "unknown cost), so total_cost and cost_per_share "
                      "are left out.", flat)

    def test_a_bad_marker_is_refused_on_read_back(self):
        from taxjson.bin import taxjson_export as E
        doc = {"holding": [{"symbol": "ZZC", "quantity": 1.0,
                            "cost_unknown": "yes"}]}
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit):
            E._holdings_toml_to_inventory(doc, "h.toml")
        self.assertIn("cost_unknown is 'yes', not true or false",
                      err.getvalue())


# --------------------------------------------------------------------
# The pooled put premium and transfers out of the pool (TJ-0624)
# --------------------------------------------------------------------

PUT = "ZZQ250321P00001000.TO"


def _r(acct, d, sym, q, net, action="BUYSELL", rid=None, **kw):
    mult = 100 if sym == PUT else 1
    return dict(dict(date=d, time="10:00:00", account=acct, symbol=sym,
                     action=action, quantity=q, net_amount=net,
                     price=abs(net) / abs(q) / mult if net else 0,
                     currency="CAD", multiplier=mult,
                     **({"id": rid} if rid else {})), **kw)


def _put_book(acct="tfsa"):
    """100 held at 1; a put written for 10, assigned: 100 more at 1, the
    pool's cost 190 for 200 shares."""
    return [_r(acct, "2025-01-06", "ZZQ.TO", 100, 100, rid="buy"),
            _r(acct, "2025-02-03", PUT, -1, 10, rid="write"),
            _r(acct, "2025-03-21", PUT, 1, 0, "ASSIGN", rid="asg"),
            _r(acct, "2025-03-21", "ZZQ.TO", 100, 100, rid="dlv",
               time="10:01:00")]


def _credits(pool):
    from taxjson.lib import trade_stats as TS
    puts = [c for rows in pool.values()
            for c in TS.written_option_trades(rows, lambda t: t["date"])[
                "credits"] if c.get("right") != "C"]
    return {k: round(v, 6)
            for k, v in TS.pooled_put_credits(pool, puts).items()}


class TestATransferOutOfThePoolTakesItsPremium(unittest.TestCase):
    """TJ-0624: 100 of the pool's 200 shares leave by a transfer, then
    the other 100 are sold: each half carries half the premium."""

    @rule("CA-RPT-16")
    def test_a_withdrawal_in_kind(self):
        book = _put_book() + [
            _r("tfsa", "2025-03-22", "ZZQ.TO", -100, 100, "TRANSFER",
               rid="out"),
            _r("tfsa", "2025-03-24", "ZZQ.TO", -100, 100, rid="sale")]
        self.assertEqual(_credits({"tfsa": book}),
                         {("tfsa", "out"): 5.0, ("tfsa", "sale"): 5.0})

    @rule("CA-RPT-16")
    def test_a_custody_move_is_no_transfer_out(self):
        # The out row and the in row 10 days later net to zero: the
        # shares never left; the sale of all 200 carries all of it.
        book = _put_book() + [
            _r("tfsa", "2025-03-22", "ZZQ.TO", -200, 0, "TRANSFER",
               rid="out"),
            _r("tfsa", "2025-04-01", "ZZQ.TO", 200, 0, "TRANSFER",
               rid="in"),
            _r("tfsa", "2025-04-03", "ZZQ.TO", -200, 200, rid="sale")]
        self.assertEqual(_credits({"tfsa": book}),
                         {("tfsa", "sale"): 10.0})
        # The in row 40 days on: two moves, the first a transfer out of
        # every share, the later arrival none of the premium.
        far = [dict(r, date="2025-05-02") if r.get("id") == "in" else
               dict(r, date="2025-05-04") if r.get("id") == "sale" else r
               for r in book]
        self.assertEqual(_credits({"tfsa": far}),
                         {("tfsa", "out"): 10.0})

    @rule("CA-RPT-16", "CA-ACB-OWN-MOVE")
    def test_a_move_between_the_pools_accounts(self):
        # other's 100 move to other2 (a move of your own: the run's two
        # lot-move legs), which sells them: half the premium, as before.
        desc = "own-account move #1: other -> other2 (test)"
        margin = [dict(r, account="margin") for r in _put_book()[1:]]
        pool = {
            "margin": margin,
            "other": [_r("other", "2025-01-06", "ZZQ.TO", 100, 100,
                         rid="buy"),
                      _r("other", "2025-03-22", "ZZQ.TO", -100, 0,
                         "TRANSFER", rid="mv", type="lot_move",
                         description=desc, time="00:00:00")],
            "other2": [_r("other2", "2025-03-22", "ZZQ.TO", 100, 0,
                          "TRANSFER", rid="mv", type="lot_move",
                          description=desc, time="00:00:00"),
                       _r("other2", "2025-03-24", "ZZQ.TO", -100, 100,
                          rid="sale")]}
        self.assertEqual(_credits(pool), {("other2", "sale"): 5.0})

    @rule("CA-RPT-16")
    def test_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            for a in ("margin", "tfsa"):
                (root / "inputs" / a).mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.tfsa]\ntype = "sheltered"\ntransfers = true\n')
            (root / "inputs" / "margin" / "m.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZK.TO 10 CAD 1 10 0\n")
            (root / "inputs" / "tfsa" / "t.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 100 CAD 1 100 0\n"
                f"BUYSELL 2025-02-03 10:00:00 {PUT} -1 CAD 0.1 10 0\n"
                f"ASSIGN 2025-03-21 10:00:00 {PUT} 1 CAD 0 0 0\n"
                "BUYSELL 2025-03-21 10:01:00 ZZQ.TO 100 CAD 1 100 0\n"
                "TRANSFER 2025-03-22 10:00:00 ZZQ.TO -100 CAD 1 100\n"
                "BUYSELL 2025-03-24 10:00:00 ZZQ.TO -100 CAD 1 100 0\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, "stats", "tfsa", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            # The premium is the put's; the withdrawal and the sale each
            # break even (a 5 win and a 5 loss before). The withdrawal
            # in kind is no trade: apart (backlog TJ-0629).
            self.assertEqual(doc["classes"]["written-options"]["net_pl"],
                             10.0)
            shares = doc["classes"]["long-shares"]
            self.assertEqual((shares["trades"], shares["breakeven"],
                              shares["net_pl"]), (1, 1, 0.0))
            self.assertEqual(doc["moved_in_kind"],
                             {"count": 1, "net_pl": 0.0})


# --------------------------------------------------------------------
# edge-cases: a series split on its own expiry day (TJ-0623)
# --------------------------------------------------------------------

CALL = "ZZQ260102C00010000.US"          # expires 2026-01-02
CALL5 = "ZZQ260102C00005000.US"


def _expiry_project(td: str, tt: str) -> Path:
    root = Path(td) / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "inputs" / "margin" / "trades.tt").write_text(tt)
    return root


def _expiries(root: Path):
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr[-2000:]
    r = _cli(root, "edge-cases", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["year_boundary"]["option_expiries"]


class TestExpiryDaySplit(unittest.TestCase):
    """TJ-0623: a series split (or renamed) on its expiry day itself is
    held as its successor that day, as a split earlier in the window
    is (TJ-0564): the day's rows under the new symbol are its own."""

    @rule("CA-RPT-07")
    def test_split_on_the_expiry_day(self):
        with tempfile.TemporaryDirectory() as td:
            root = _expiry_project(td, (
                f"BUYSELL 2025-12-01 10:00:00 {CALL} 1 CAD 1 100 0\n"
                f"SPLIT 2026-01-02 00:00:00 {CALL} {CALL5} 2\n"
                f"BUYSELL 2026-01-02 10:00:00 {CALL5} -1 CAD 2 200 0\n"
                f"BUYSELL 2026-01-02 16:00:00 {CALL5} -1 CAD 0 0 0\n"))
            rows = _expiries(root)
        self.assertEqual([(r["symbol"], r["held"], r["traded"], r["units"],
                           r["outcome"]) for r in rows],
                         [(CALL5, 2.0, 1.0, 1.0, "1 sold, 1 expire")])

    @rule("CA-RPT-07")
    def test_renamed_on_the_expiry_day(self):
        new = "ZZR260102C00010000.US"
        with tempfile.TemporaryDirectory() as td:
            root = _expiry_project(td, (
                f"BUYSELL 2025-12-01 10:00:00 {CALL} 1 CAD 1 100 0\n"
                f"SPLIT 2026-01-02 00:00:00 {CALL} {new} 1\n"
                f"BUYSELL 2026-01-02 16:00:00 {new} -1 CAD 0 0 0\n"))
            rows = _expiries(root)
        self.assertEqual([(r["symbol"], r["held"], r["units"],
                           r["outcome"]) for r in rows],
                         [(new, 1.0, 1.0, "1 expire")])


# --------------------------------------------------------------------
# A crypto account of two exchanges: each coin its venue's suffix
# (TJ-0603)
# --------------------------------------------------------------------

KR_HDR = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
          "margin,misc,ledgers\n")
CB_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
          "Price Currency,Price at Transaction,Subtotal,Total (inclusive "
          "of Fees and/or Spread),Fees and/or Spread,Notes\n")


def _crypto_project(td: str, files: dict) -> Path:
    root = Path(td) / "p"
    (root / "inputs" / "kx").mkdir(parents=True)
    for n, text in files.items():
        (root / "inputs" / "kx" / n).write_text(text)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.kx]\ntype = "taxable"\ncrypto = true\n')
    return root


# Kraken: ZZD and ZZC bought; Coinbase: ZZE and ZZC bought (in CAD).
_KR = (KR_HDR
       + "TXA1,OA1,ZZD/CAD,2025-02-15 10:00:00.1234,buy,limit,10,25,0,"
         "2.5,,,\n"
       + "TXA2,OA2,ZZC/CAD,2025-02-16 10:00:00.1234,buy,limit,40,40,0,"
         "1.0,,,\n")
_CB = (CB_HDR
       + "CB-ZZ-0001,2025-03-05 09:48:33 UTC,Buy,ZZE,4.0,CAD,5.00,20.00,"
         "20.00,0.00,Bought 4 ZZE\n"
       + "CB-ZZ-0002,2025-03-06 09:48:33 UTC,Buy,ZZC,1.0,CAD,40.00,40.00,"
         "40.00,0.00,Bought 1 ZZC\n")


@unittest.skipIf(tomllib is None, "reading TOML needs Python 3.11+ or tomli")
class TestEachCoinItsVenue(unittest.TestCase):
    """TJ-0603: an account folder holding a Kraken and a Coinbase export
    wrote .KR on every coin, Coinbase's too (the one venue with a suffix
    went to the whole account). Each coin now carries the suffix of the
    one venue whose rows hold it; one both hold, or a venue with no
    suffix (Coinbase), is written bare."""

    def _held(self, files):
        with tempfile.TemporaryDirectory() as td:
            root = _crypto_project(td, files)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            doc, _text = _holdings(root, "kx")
        return {h["symbol"]: h["quantity"] for h in doc}

    def test_two_exchanges(self):
        self.assertEqual(self._held({"kr_trades.csv": _KR,
                                     "cb_2025.csv": _CB}),
                         {"ZZD.KR": 2.5, "ZZC": 2.0, "ZZE": 4.0})

    def test_a_renamed_coin_keeps_its_venue(self):
        # A ticker.map line renames at parse; a dated .tt RENAME in the
        # account's books (a SPLIT row naming the new coin).
        for extra in ({"renames.tt": "RENAME 2025-04-01 ZZD ZZF\n"},
                      {"../../ticker.map": "GLOBAL ZZD ZZF\n"}):
            with self.subTest(extra=extra):
                self.assertEqual(
                    self._held({"kr_trades.csv": _KR, "cb_2025.csv": _CB,
                                **extra}),
                    {"ZZF.KR": 2.5, "ZZC": 2.0, "ZZE": 4.0})

    def test_one_exchange_alone_is_unchanged(self):
        self.assertEqual(self._held({"kr_trades.csv": _KR}),
                         {"ZZD.KR": 2.5, "ZZC.KR": 1.0})
        self.assertEqual(self._held({"cb_2025.csv": _CB}),
                         {"ZZE": 4.0, "ZZC": 1.0})


# --------------------------------------------------------------------
# taxjson-split-gains on a trade-date project (TJ-0607: already right)
# --------------------------------------------------------------------

class TestSplitYearEndOnTradeDates(unittest.TestCase):
    """TJ-0607 (found fixed: backlog TJ-0343 passes the year end's date
    basis to the split): m2's buy traded Dec 30 that settles Jan 2 is
    in its year-end share on a trade-date project, not on a
    settlement-date one."""

    @rule("CA-RPT-07")
    def test_a_december_30_buy_settling_in_january(self):
        from taxjson.bin.taxjson_split_gains import split_for_account
        from taxjson.lib.core import TaxTransaction
        from taxjson.lib.pipeline import GainsRequest, run_gains

        def t(acct, day, settle, qty):
            return TaxTransaction(
                account=acct, action="BUYSELL", date=day,
                date_settle=settle, time="10:00:00", symbol="ZZY.TO",
                quantity=qty, net_amount=-10.0 * qty, currency="CAD",
                id=f"{acct}-{day}")
        books = {"m1": [t("m1", "2025-03-03", "2025-03-04", 10)],
                 "m2": [t("m2", "2025-06-02", "2025-06-03", 10),
                        t("m2", "2025-12-30", "2026-01-02", 10)]}
        got = {}
        for basis in ("trade", "settle"):
            with contextlib.redirect_stderr(io.StringIO()):
                doc = run_gains([x for r in books.values() for x in r], [],
                                req=GainsRequest(country="canada",
                                                 year=2025, taxable=True,
                                                 tax_date=basis))
            got[basis] = {
                a: [(r["qty"], r["total_cost"]) for r in split_for_account(
                    doc, a, [x.to_dict() for x in rows])[
                        "inventory_year_end"]]
                for a, rows in books.items()}
        self.assertEqual(got["trade"], {"m1": [(10.0, 100.0)],
                                        "m2": [(20.0, 200.0)]})
        self.assertEqual(got["settle"], {"m1": [(10.0, 100.0)],
                                         "m2": [(10.0, 100.0)]})



if __name__ == "__main__":
    unittest.main()
