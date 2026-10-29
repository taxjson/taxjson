"""Fix round 2026-10-29 (round 4), group views: derived views and
reports.

TJ-0279  <account>.sum asked for a `.tt OPENING cost=unknown` line
         (MANUAL REPORTING REQUIRED) for a sale of units moved in from
         another of your accounts: their cost is in the blended figures
         (<account>_wash.sum, `taxjson sum`). They are a block of their
         own now, and its holdings table says "unknown", not 0.00.
TJ-0598  reports/<account>_holdings.toml: units moved in from another
         of your accounts take the cost the run has after the passes
         across accounts (`cost_source = "blended"`), where round 3
         left it unknown.
TJ-0629  `tjs stats` / `win-rates` counted a registered account's
         withdrawal in kind (a TRANSFER out, `transfers = true`, which
         the books book as a sale at its value) as a closed trade, with
         a win or a loss. It is no trade: its P/L is counted apart.
TJ-0634  The US option-replacement warning's covered_shares carried
         float noise (9.999999999999998) into the gains JSON: the loss's
         open part is a money ratio's, and quantities are saved
         unrounded. Share counts are now at the engine's share
         precision (six decimals).

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

from tax_rules import rule
from tax_rules.dual import tx
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv

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


def _flat(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------
# A withdrawal in kind is no closed trade (TJ-0629)
# --------------------------------------------------------------------

def _tfsa_project(td: str) -> Path:
    """A TFSA keeping its transfers: 100 ZZY bought at 2, 50 withdrawn
    in kind at 3 (a TRANSFER out), the other 50 sold at 1.50."""
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
        "BUYSELL 2025-01-06 10:00:00 ZZY.TO 100 CAD 2 200 0\n"
        "TRANSFER 2025-03-03 10:00:00 ZZY.TO -50 CAD 3 150\n"
        "BUYSELL 2025-04-01 10:00:00 ZZY.TO -50 CAD 1.5 75 0\n")
    return root


class TestAWithdrawalInKindIsNoTrade(unittest.TestCase):
    """TJ-0629: the withdrawal's +50 was a winning trade beside the
    sale's -25 (2 trades, a 50% win rate). It is no trade: the sale is
    the one trade, and the withdrawal's P/L is said apart."""

    @rule("CA-RPT-16")
    def test_stats_and_win_rates(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tfsa_project(td)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, "stats", "tfsa", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            shares = doc["classes"]["long-shares"]
            self.assertEqual((shares["trades"], shares["wins"],
                              shares["losses"], shares["net_pl"]),
                             (1, 0, 1, -25.0))
            self.assertEqual(doc["total"]["trades"], 1)
            self.assertEqual(doc["moved_in_kind"],
                             {"count": 1, "net_pl": 50.0})
            text = _flat(_cli(root, "stats", "tfsa").stdout)
            self.assertIn("Moved in kind, not trades (NOT in the table): "
                          "50.00 CAD of P/L over 1 move(s), at the value "
                          "they moved at.", text)
            det = _flat(_cli(root, "stats", "tfsa", "--details").stdout)
            self.assertIn("1 move(s) in kind (a registered account's "
                          "withdrawal in kind) are not trades", det)
            r = _cli(root, "win-rates", "tfsa", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            wr = json.loads(r.stdout)
            self.assertEqual([(x["key"], x["trades"], x["net_pl"])
                              for x in wr["rows"]], [("ZZY.TO", 1, -25.0)])
            self.assertEqual(wr["moved_in_kind"],
                             {"count": 1, "net_pl": 50.0})
            self.assertIn("Moved in kind, not trades",
                          _flat(_cli(root, "win-rates", "tfsa").stdout))

    @rule("CA-RPT-16")
    def test_closed_trades_sets_the_move_apart(self):
        from taxjson.lib import trade_stats as TS
        book = [{"id": "b1", "action": "BUYSELL", "symbol": "ZZY.TO",
                 "quantity": 100, "date": "2025-01-06"},
                {"id": "w1", "action": "TRANSFER", "symbol": "ZZY.TO",
                 "quantity": -50, "date": "2025-03-03"},
                {"id": "s1", "action": "BUYSELL", "symbol": "ZZY.TO",
                 "quantity": -50, "date": "2025-04-01"}]
        gains = {"transactions": [
            {"id": "w1", "symbol": "ZZY.TO", "date": "2025-03-03",
             "qty": 50.0, "gain": 50.0, "raw_gain": 50.0,
             "direction": "LONG"},
            {"id": "s1", "symbol": "ZZY.TO", "date": "2025-04-01",
             "qty": 50.0, "gain": -25.0, "raw_gain": -25.0,
             "direction": "LONG"}]}
        res = TS.closed_trades({"tfsa": gains}, {"tfsa": book},
                               lambda d: True, lambda r: r["date"])
        self.assertEqual([(t["date"], t["pnl"]) for t in res["trades"]],
                         [("2025-04-01", -25.0)])
        mv = res["moved_in_kind"]
        self.assertEqual((mv["count"], mv["net_pl"]), (1, 50.0))
        self.assertEqual(mv["moves"][0]["kind"], "moved in kind")


# --------------------------------------------------------------------
# The per-account .sum and units moved in from your own account
# (TJ-0279)
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


def _moved_project(root: Path, country: str, *qb_rows) -> Path:
    """qa buys 100 ZZQ at 2.00 and moves them out on 2024-03-01; qb
    receives them the next day (a move of your own), then `qb_rows`."""
    from tax_rules.dual import settings_for
    cur = "CAD" if country == "canada" else "USD"
    accounts = {
        "qa": _qt("99900001", cur,
                  ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0, -200.0,
                   "Trades"),
                  ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT", -100,
                   0.0, 0.0, "Transfers")),
        "qb": _qt("99900002", cur,
                  ("2024-03-02", "TF6", "ZZQ CORP TRANSFER IN", 100, 0.0,
                   0.0, "Transfers"), *qb_rows)}
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2024, source_currencies=[])
        + "".join(f'[accounts.{n}]\ntype = "taxable"\n' for n in accounts))
    for n, text in accounts.items():
        (root / "inputs" / n).mkdir(parents=True)
        (root / "inputs" / n / "questrade_2024.csv").write_text(text)
    return root


_SALE = ("2024-06-03", "Sell", "ZZQ CORP", -40, 3.0, 120.0, "Trades")


class TestOwnSumSaysTheCostIsBlended(unittest.TestCase):
    """TJ-0279: qb sells 40 of the 100 moved in from qa. Its own .sum
    asked for a `.tt OPENING cost=unknown` line and find-missing-history
    for them: they have no purchase missing, their cost is in the
    blended figures (qb_wash.sum, `taxjson sum`)."""

    def _check(self, country, why):
        cur = "CAD" if country == "canada" else "USD"
        sym = "ZZQ.TO" if country == "canada" else "ZZQ.US"
        with tempfile.TemporaryDirectory() as td:
            root = _moved_project(Path(td) / "p", country, _SALE)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            own = (root / "reports" / "qb.sum").read_text()
            flat = _flat(own)
            self.assertIn(
                f"MOVED IN FROM YOUR OWN ACCOUNT — 1 sale(s) of units "
                f"moved in from qa, proceeds 120.00 {cur}: NOT in the rows "
                f"or totals above, and nothing to report by hand from "
                f"here. This account's own books have no cost for them; "
                f"the figures you file have it: qb_wash.sum and `taxjson "
                f"sum` ({why}).", flat)
            self.assertIn(f"{sym} 2024-06-03 40 120.00 qb", flat)
            for gone in ("MANUAL REPORTING REQUIRED", "OPENING cost=unknown",
                         "find-missing-history"):
                self.assertNotIn(gone, own)
            # Its holdings report: the 60 left at a cost unknown here,
            # never 0.00.
            self.assertIn(f"{sym} 60 unknown unknown {cur}", flat)
            self.assertIn("unknown: some of the units have no cost in "
                          "these books", flat)
            # The figures you file have the cost: 40 x 2.00.
            wash = json.loads((root / "work" / "qb_gains_wash.json")
                              .read_text())
            self.assertEqual([(t["qty"], round(t["cost"], 2))
                              for t in wash["transactions"]],
                             [(40.0, 80.0)])
            self.assertFalse(wash.get("manual_reporting_required"))

    @rule("CA-ACB-OWN-MOVE")
    def test_canada(self):
        self._check("canada", "one ACB across your taxable accounts, "
                              "CA-ACB-OWN-MOVE")

    @rule("US-BASIS-05")
    def test_usa(self):
        self._check("usa", "the lots' basis carried by the move, "
                           "US-BASIS-05")


def _own_view(country, rows):
    """run_gains on one account's own book (qb): rows of (id, action,
    date, quantity, net amount[, description])."""
    from taxjson.lib.core import TaxTransaction as T
    from taxjson.lib.pipeline import GainsRequest, run_gains
    sfx = "TO" if country == "canada" else "US"
    cur = "CAD" if country == "canada" else "USD"
    txs = []
    for i, a, d, q, n, *desc in rows:
        txs.append(T(id=i, action=a, date=d, date_settle=d, time="10:00:00",
                     symbol=f"ZZQ.{sfx}", quantity=q, net_amount=n,
                     price=abs(n / q) if q and n else 0.0, currency=cur,
                     account="qb",
                     **({"type": "lot_move", "description": desc[0]}
                        if desc else {})))
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(txs, [], req=GainsRequest(
            country=country, year=2024, taxable=True, phantom_hint=False))


_MOVE_IN = ("mv", "TRANSFER", "2024-03-01", 100, 0.0,
            "own-account move #1: qa -> qb (test)")


class TestTheEnginesNameTheMove(unittest.TestCase):
    """TJ-0279: a sale of units whose unknown cost an own move alone
    brought carries `own_move_from`; one whose pool (Canada) or lot
    (US) also holds an opening at an unknown cost does not."""

    def _froms(self, country, rows):
        doc = _own_view(country, rows)
        return [(m["qty"], m.get("own_move_from"))
                for m in doc.get("manual_reporting_required") or []]

    def _a_move_alone(self, country):
        self.assertEqual(
            self._froms(country, [_MOVE_IN, ("s", "BUYSELL", "2024-06-03",
                                             -40, 120.0)]),
            [(40.0, ["qa"])])

    @rule("CA-ACB-OWN-MOVE")
    def test_canada_a_move_alone(self):
        self._a_move_alone("canada")

    @rule("US-BASIS-05")
    def test_usa_a_move_alone(self):
        self._a_move_alone("usa")

    @rule("CA-ACB-OWN-MOVE")
    def test_canada_a_move_and_an_opening(self):
        # One pool: the sale's unknown cost is not the move's alone.
        self.assertEqual(self._froms("canada", [
            ("ob", "OPENING_BALANCE", "2024-01-02", 10, 0.0), _MOVE_IN,
            ("s", "BUYSELL", "2024-06-03", -40, 120.0)]), [(40.0, None)])

    @rule("CA-ACB-OWN-MOVE")
    def test_canada_the_pool_drains_between(self):
        # The moved units all sold (the pool flat), then an opening at
        # an unknown cost: its sale is no own move's.
        self.assertEqual(self._froms("canada", [
            _MOVE_IN, ("s1", "BUYSELL", "2024-06-03", -100, 300.0),
            ("ob", "OPENING_BALANCE", "2024-07-02", 10, 0.0),
            ("s2", "BUYSELL", "2024-08-01", -10, 30.0)]),
            [(100.0, ["qa"]), (10.0, None)])

    @rule("US-BASIS-05")
    def test_usa_each_lot_its_own(self):
        # FIFO: the opening's lot first, then the moved lot.
        self.assertEqual(self._froms("usa", [
            ("ob", "OPENING_BALANCE", "2024-01-02", 10, 0.0), _MOVE_IN,
            ("s", "BUYSELL", "2024-06-03", -40, 120.0)]),
            [(10.0, None), (30.0, ["qa"])])


class TestTheManualSectionSplits(unittest.TestCase):
    """TJ-0279: a .sum's unknown-cost rows: the ones an own move brought
    in their own block, the rest MANUAL REPORTING REQUIRED as before."""

    def test_both_blocks(self):
        from taxjson.bin.taxjson_form_export import manual_section
        rows = [{"symbol": "ZZQ.TO", "date": "2024-06-03", "qty": 40.0,
                 "proceeds": 120.0, "account": "qb", "currency": "CAD",
                 "own_move_from": ["qa"]},
                {"symbol": "ZZK.TO", "date": "2024-06-04", "qty": 5.0,
                 "proceeds": 50.0, "account": "qb", "currency": "CAD"}]
        text = _flat("\n".join(manual_section(rows, "", "canada")))
        self.assertIn("MANUAL REPORTING REQUIRED — 1 sale(s) with no "
                      "purchase in your files (unknown cost, .tt OPENING "
                      "cost=unknown), proceeds 50.00 CAD", text)
        self.assertIn("ZZK.TO 2024-06-04 5 50.00 qb", text)
        self.assertIn("MOVED IN FROM YOUR OWN ACCOUNT — 1 sale(s) of units "
                      "moved in from qa, proceeds 120.00 CAD", text)
        # Without own-move rows: the block as before.
        plain = manual_section(rows[1:], "CAD")
        self.assertTrue(plain[0].startswith("MANUAL REPORTING REQUIRED"))
        self.assertNotIn("MOVED IN", "\n".join(plain))


# --------------------------------------------------------------------
# The holdings file: moved-in units at the cost the run has (TJ-0598)
# --------------------------------------------------------------------

def _holdings(root: Path, account: str):
    from taxjson.lib.tomlcompat import tomllib
    text = (root / "reports" / f"{account}_holdings.toml").read_text()
    return tomllib.loads(text).get("holding") or [], text


def _comments(text: str) -> str:
    """The comment lines' text, "# " dropped, whitespace collapsed."""
    return " ".join(" ".join(ln[2:] for ln in text.splitlines()
                             if ln.startswith("# ")).split())


class TestMovedInUnitsTakeTheBlendedCost(unittest.TestCase):
    """TJ-0598: round 3 wrote `cost_unknown = true` for units qb got by
    a move of its own from qa. The run has their cost after the passes
    across accounts (qb_gains_wash.json, `taxjson list`): the holding
    now carries it, `cost_source = "blended"`."""

    def _check(self, country, qb_rows, qty, cost, fig):
        cur = "CAD" if country == "canada" else "USD"
        with tempfile.TemporaryDirectory() as td:
            root = _moved_project(Path(td) / "p", country, *qb_rows)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (h,), text = _holdings(root, "qb")
            self.assertEqual(
                {k: h.get(k) for k in (
                    "quantity", "currency", "total_cost", "cost_per_share",
                    "base_currency", "base_total_cost",
                    "base_cost_per_share", "cost_source", "cost_unknown")},
                {"quantity": qty, "currency": cur, "total_cost": cost,
                 "cost_per_share": 2.0, "base_currency": cur,
                 "base_total_cost": cost, "base_cost_per_share": 2.0,
                 "cost_source": "blended", "cost_unknown": None})
            self.assertIn(
                "cost_source = blended: 100 moved in from qa (your own "
                "account) on 2024-03-01, a cost this account's own books "
                "do not have. total_cost and base_total_cost are " + fig
                + " after the passes across accounts, as `taxjson list` "
                  "gives it, not this account's own books.",
                _comments(text))
            # The figure is `taxjson list`'s.
            rows = json.loads(_cli(root, "list", "--json").stdout)["rows"]
            self.assertEqual([(x["account"], x["qty"], round(x["cost"], 2))
                              for x in rows], [("qb", qty, cost)])
            # Read back by taxjson-export: the cost, not unknown.
            from taxjson.bin.taxjson_export import \
                _holdings_toml_to_inventory
            from taxjson.lib.tomlcompat import tomllib
            (item,) = _holdings_toml_to_inventory(
                tomllib.loads(text))["inventory"]
            self.assertEqual(item["total_cost"], cost)
            self.assertFalse(item.get("unknown_cost"))

    @rule("CA-ACB-OWN-MOVE")
    def test_canada(self):
        self._check("canada", (), 100.0, 200.0,
                    "this account's share of the ACB pool across your "
                    "taxable accounts (s.47)")

    @rule("CA-ACB-OWN-MOVE")
    def test_canada_after_a_sale(self):
        self._check("canada", (_SALE,), 60.0, 120.0,
                    "this account's share of the ACB pool across your "
                    "taxable accounts (s.47)")

    @rule("US-BASIS-05")
    def test_usa(self):
        self._check("usa", (), 100.0, 200.0,
                    "the basis of this account's lots (the moved lots' "
                    "basis carried with them)")

    @rule("CA-ACB-OWN-MOVE")
    def test_one_account_run_keeps_it_unknown(self):
        # `run --account qb` runs no pass across accounts: no figure
        # to take, the holding says so as before.
        with tempfile.TemporaryDirectory() as td:
            root = _moved_project(Path(td) / "p", "canada")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, "run", "--no-input", "--account", "qb")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (h,), text = _holdings(root, "qb")
            self.assertIs(h["cost_unknown"], True)
            self.assertNotIn("total_cost", h)
            self.assertNotIn("cost_source", h)
            # The listing's currency (a key a reader requires), from the
            # move's own rows: these books' pool has none.
            self.assertEqual(h["currency"], "CAD")

    @rule("CA-ACB-OWN-MOVE")
    def test_canada_crypto(self):
        from test_fix_1029_transfers import _crypto_move
        with tempfile.TemporaryDirectory() as td:
            root = _crypto_move(Path(td) / "p", "canada")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (h,), _text = _holdings(root, "receiver")
            # 0.5 ETH left of the 1 bought for 100 (one pool).
            self.assertEqual((h["quantity"], h["total_cost"],
                              h["base_total_cost"], h["cost_source"]),
                             (0.5, 50.0, 50.0, "blended"))
            self.assertNotIn("cost_unknown", h)


class TestAFillThatFailsKeepsTheFile(unittest.TestCase):
    """TJ-0598: a blended file the export cannot read leaves the
    holdings file stage_account wrote, said in one warning."""

    def test_damaged_blended_file(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            work, reports = Path(td) / "work", Path(td) / "reports"
            work.mkdir()
            reports.mkdir()
            gains = work / "qb_raw_gains.json"
            gains.write_text(json.dumps({"inventory": [
                {"symbol": "ZZQ.TO", "qty": 100.0, "total_cost": 0.0,
                 "currency": "CAD", "unknown_cost": True}],
                "transactions": []}))
            (work / "qb_gains_wash.json").write_text("{not json")
            held = reports / "qb_holdings.toml"
            held.write_text("cost_unknown = true\n")
            cmd = R._cmd("taxjson-export") + [
                "--holdings-toml", "--account-name", "qb", str(gains)]
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(err):
                R.fill_moved_holdings(["qb"], {"qb": {"holdings_cmd": cmd}},
                                      work, reports)
            self.assertEqual(held.read_text(), "cost_unknown = true\n")
            self.assertIn("qb: the holdings file keeps units moved in from "
                          "another of your accounts at an unknown cost",
                          _flat(err.getvalue()))


class TestTheBlendedFillHoldsBack(unittest.TestCase):
    """TJ-0598: the figure after the passes is taken only where it is
    the holding's alone; a listing in another currency keeps its own
    cost unknown and takes the base-currency figure."""

    def _args(self, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(**dict(dict(
            account_name="qb", inputs=["qb_raw_gains.json"], crypto=False,
            symbol_suffix=None, country="canada", base_currency="CAD",
            dust_threshold=1e-3), **kw))

    def _fill(self, agg, inv, tobase=None):
        from taxjson.bin.taxjson_export import _blended_fill
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "qb_gains_wash.json"
            p.write_text(json.dumps({"inventory": inv,
                                     "transactions": []}))
            m = tobase or {}
            return _blended_fill(agg, [str(p)], "qb",
                                 lambda s: m.get(s, s), "CAD", 1e-3, {})

    _MOVED = {"qty": 100.0, "total_cost": 0.0, "currency": "",
              "position_start_date": "2024-03-01", "unknown_cost": True}

    def test_refusals(self):
        row = {"symbol": "ZZQ.TO", "qty": 100.0, "total_cost": 200.0,
               "currency": "CAD", "account": "qb"}
        self.assertEqual(self._fill({"ZZQ.TO": dict(self._MOVED)}, [row]),
                         {"ZZQ.TO": {"total_cost": 200.0,
                                     "currency": "CAD", "native": ""}})
        for why, agg, inv, tb in (
                ("another quantity", {"ZZQ.TO": dict(self._MOVED)},
                 [dict(row, qty=90.0)], None),
                ("no cost there either", {"ZZQ.TO": dict(self._MOVED)},
                 [dict(row, unknown_cost=True)], None),
                ("another account's row", {"ZZQ.TO": dict(self._MOVED)},
                 [dict(row, account="qa")], None),
                ("not the base currency", {"ZZQ.TO": dict(self._MOVED)},
                 [dict(row, currency="USD")], None),
                ("two listings, one pool",
                 {"ZZQ.TO": dict(self._MOVED),
                  "ZZQ.US": dict(self._MOVED, qty=5.0)},
                 [row], {"ZZQ.US": "ZZQ.TO"}),
                ("a known cost", {"ZZQ.TO": dict(self._MOVED,
                                                 unknown_cost=False)},
                 [row], None)):
            with self.subTest(why):
                self.assertEqual(self._fill(agg, inv, tb), {})

    @rule("CA-ACB-OWN-MOVE")
    def test_a_us_listing_in_a_canadian_project(self):
        from taxjson.bin.taxjson_export import render_holdings_toml
        from taxjson.lib.tomlcompat import tomllib
        agg = {"ZZQ.US": dict(self._MOVED, currency="USD")}
        fill = self._fill(agg, [{"symbol": "ZZQ.US", "qty": 100.0,
                                 "total_cost": 270.0, "currency": "CAD",
                                 "account": "qb"}])
        self.assertEqual(fill["ZZQ.US"]["native"], "USD")
        text = "\n".join(render_holdings_toml(
            agg, self._args(), {k: dict(v) for k, v in agg.items()},
            None, {"ZZQ.US": [("2024-03-01", 100.0, "qa")]}, fill))
        (h,) = tomllib.loads(text)["holding"]
        self.assertEqual(
            {k: h.get(k) for k in ("currency", "cost_unknown",
                                   "total_cost", "base_currency",
                                   "base_total_cost", "cost_source")},
            {"currency": "USD", "cost_unknown": True, "total_cost": None,
             "base_currency": "CAD", "base_total_cost": 270.0,
             "cost_source": "blended"})
        self.assertIn("total_cost and cost_per_share (USD) are left out "
                      "(cost_unknown); base_total_cost is this account's "
                      "share of the ACB pool", _comments(text))


# --------------------------------------------------------------------
# covered_shares at the engine's share precision (TJ-0634)
# --------------------------------------------------------------------

def _us_gains(book):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(list(book), [], [], req=GainsRequest(
            country="usa", taxable=True, year=2025))


class TestCoveredSharesHaveNoFloatNoise(unittest.TestCase):
    """TJ-0634: 100 shares sold at a loss, 90 bought back, then one
    call: its 10 shares against the loss are 10, in memory and in the
    gains JSON, not 9.999999999999998."""

    CALL = "ZZQ250620C00050000.US"

    @rule("US-WASH-12")
    def test_ten_not_nine_point_nine_nine(self):
        from taxjson.lib.numeric import round_floats
        doc = _us_gains([
            tx("BUYSELL", "2025-01-02", "ZZQ.US", 100, -500),
            tx("BUYSELL", "2025-03-03", "ZZQ.US", -100, 400),
            tx("BUYSELL", "2025-03-10", "ZZQ.US", 90, -369),
            tx("BUYSELL", "2025-03-12", self.CALL, 1, -200)])
        (w,) = doc["option_replacement_warnings"]
        self.assertEqual(repr(w["covered_shares"]), "10.0")
        self.assertEqual(repr(w["replaced_qty"]), "90.0")
        # As the gains JSON saves it (quantities are not rounded there).
        saved = json.loads(json.dumps(round_floats(w)))
        self.assertEqual(repr(saved["covered_shares"]), "10.0")

    @rule("US-WASH-12")
    def test_a_fractional_share_keeps_its_six_decimals(self):
        from taxjson.lib.core import share_qty
        self.assertEqual(share_qty(9.999999999999998), 10.0)
        self.assertEqual(share_qty(0.123456), 0.123456)
        self.assertEqual(repr(share_qty(-1e-12)), "0.0")


if __name__ == "__main__":
    unittest.main()
