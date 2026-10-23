"""2026-10 audit #59: a long option bought before the data (a .tt
`OPENING <date> <OPTION> <qty> cost=unknown` line) and sold to close is
not a write in `stats`, `win-rates` and `option-yield`: the views add
the opening the gains stage synthesizes in memory (the books on disk
never carry it). Synthetic books only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib import trade_stats as TS

REPO = Path(__file__).resolve().parent.parent
OPT = "XYZ260619C00050000.TO"
ENV = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
           NO_COLOR="1", TAXJSON_WIDTH="0")


def _tj(root, *args):
    r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                        "-C", str(root), *args], capture_output=True,
                       text=True, env=ENV, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, (args, r.stdout[-2000:], r.stderr[-2000:])
    return r


def _row(action, date, q, net, sym=OPT):
    return {"action": action, "date": date, "time": "09:30:00",
            "symbol": sym, "quantity": q, "net_amount": net,
            "price": abs(net / q / 100) if q else 0.0, "account": "margin"}


class TestWrittenOptionTrades(unittest.TestCase):
    def test_long_opening_is_sold_out_of(self):
        rows = [_row("OPENING_BALANCE", "2025-03-01", 1, 0.0),
                _row("BUYSELL", "2025-03-02", -1, 300.0),
                _row("BUYSELL", "2025-04-02", 1, -100.0)]
        wo = TS.written_option_trades(rows, lambda r: r["date"])
        self.assertEqual(wo["trades"], [])
        # Without the opening the sale read as a write bought back.
        wo = TS.written_option_trades(rows[1:], lambda r: r["date"])
        self.assertEqual([t["kind"] for t in wo["trades"]], ["bought back"])

    def test_short_opening_close_is_counted_not_a_trade(self):
        rows = [_row("OPENING_BALANCE", "2025-03-01", -1, 0.0),
                _row("BUYSELL", "2025-04-02", 1, -100.0)]
        wo = TS.written_option_trades(rows, lambda r: r["date"])
        self.assertEqual(wo["trades"], [])
        self.assertEqual(len(wo["unknown"]), 1)
        res = TS.closed_trades({}, {"margin": rows}, lambda d: True,
                               lambda r: r["date"])
        self.assertEqual(res["trades"], [])
        self.assertEqual(res["tainted_skipped"], 1)


@rule("CA-ACB-11")
class TestViews(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\noption_grant_timing_since = 2025\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "inputs" / "margin" / "missing_history.tt").write_text(
            f"OPENING 2025-03-01 {OPT} 1 cost=unknown\n")
        (root / "inputs" / "margin" / "opts.tt").write_text(
            f"BUYSELL 2025-03-02 09:30:00 {OPT} -1 CAD 3.00 300.00 0.0\n"
            f"BUYSELL 2025-04-02 09:30:00 {OPT} 1 CAD 1.00 -100.00 0.0\n")
        _tj(root, "run", "--no-input")
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_stats_and_win_rates_count_no_write(self):
        d = json.loads(_tj(self.root, "stats", "--json").stdout)
        self.assertEqual(d["classes"]["written-options"]["trades"], 0)
        w = json.loads(_tj(self.root, "win-rates", "--json").stdout)
        self.assertEqual(w["total"]["trades"], 0)

    def test_option_yield_closes_the_opening(self):
        d = json.loads(_tj(self.root, "option-yield", "--json").stdout)
        closed = d["closed"]
        self.assertEqual(len(closed), 1)
        self.assertEqual((closed[0]["side"], closed[0]["how"]),
                         ("bought", "sold"))
        self.assertIsNone(closed[0]["pnl"])
        self.assertEqual([o["side"] for o in d["open"]], ["bought"])


if __name__ == "__main__":
    unittest.main()


class TestTinyPosition(unittest.TestCase):
    """2026-10 audit #101 (the 2026-10 critique's lead example): a
    share position under the 1e-6 tolerance that carries real cost is
    closed by its sale, at its cost — not read as flat, its sale opening
    a short and its cost stranded out of every total."""

    @rule("CA-ACB-01")
    def test_sale_of_a_sub_tolerance_position_is_booked(self):
        with tempfile.TemporaryDirectory() as td:
            totals = {}
            for name, qty, price in (("tiny", "0.000001", 100000000),
                                     ("control", "0.0001", 1000000)):
                root = Path(td) / name
                (root / "inputs" / "margin").mkdir(parents=True)
                (root / "taxjson.toml").write_text(
                    '[settings]\nlocal_timezone = "America/Toronto"\n'
                    'year = 2025\ncountry = "canada"\n'
                    'base_currency = "CAD"\nsource_currencies = []\n'
                    '[accounts.margin]\ntype = "taxable"\n')
                (root / "inputs" / "margin" / "t.tt").write_text(
                    f"BUYSELL 2025-01-02 09:30:00 SAMP.US {qty} CAD "
                    f"{price} 100 0\n"
                    f"BUYSELL 2025-04-01 09:30:00 SAMP.US -{qty} CAD "
                    f"{2 * price} 200 0\n")
                _tj(root, "run", "--no-input")
                d = json.loads(_tj(root, "sum", "--json").stdout)
                f = d["filing"]["totals"]
                totals[name] = (f["proceeds"], f["acb"], f["gain"])
            self.assertEqual(totals["tiny"], (200.0, 100.0, 100.0))
            self.assertEqual(totals["tiny"], totals["control"])
