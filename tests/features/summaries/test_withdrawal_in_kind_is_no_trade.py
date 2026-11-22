"""Fix round 2026-10-29 (round 4), group views: derived views and
reports.

TJ-0279  <account>.sum asked for a `.tt OPENING cost=unknown` line
         (MANUAL REPORTING REQUIRED) for a sale of units moved in from
         another of your accounts: their cost is in the blended figures
         (<account>_wash.sum, `tjs sum`). They are a block of their
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

Formerly: test_fix_1029_r4_views
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
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
    the one trade, and the withdrawal's P/L is said apart.

    Formerly: test_fix_1029_r4_views.TestAWithdrawalInKindIsNoTrade"""

    @rule(R.CA_RPT_16)
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

    @rule(R.CA_RPT_16)
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

    @rule(R.US_BASIS_05)
    def test_a_moves_cover_of_a_short_is_a_trade(self):
        # A row with a move leg's id that closed a short there (the US
        # engine's `closed_by_delivery`, backlog TJ-3424) is a trade,
        # not a move in kind (TJ-3454).
        from taxjson.lib import trade_stats as TS
        book = [{"id": "s1", "action": "BUYSELL", "symbol": "ZZY.US",
                 "quantity": -5, "date": "2025-01-03"},
                {"id": "m1", "action": "TRANSFER", "symbol": "ZZY.US",
                 "quantity": 10, "date": "2025-03-03"}]
        gains = {"transactions": [
            {"id": "m1", "symbol": "ZZY.US", "date": "2025-03-03",
             "qty": 5.0, "gain": 10.0, "raw_gain": 10.0,
             "direction": "SHORT", "closed_by_delivery": True}]}
        res = TS.closed_trades({"c": gains}, {"c": book},
                               lambda d: True, lambda r: r["date"])
        self.assertEqual([(t["date"], t["pnl"]) for t in res["trades"]],
                         [("2025-03-03", 10.0)])
        self.assertEqual(res["moved_in_kind"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
