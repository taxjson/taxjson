"""The 2026-10-25 audit, the statistics views: `tjs stats` / `tjs
win-rates` (lib/trade_stats) count each closed trade's economic P/L
before any denial, so a denied loss counts once — in the loss sale's
P/L — and the replacement's sale counts without the denied loss the
rule added to its cost. Findings planning-us#1 and brokers-inputs#2.

The engines record that addition per disposition
(`deferred_loss_recovered`); the views add it back. Every fixture is
synthetic: made-up tickers (ZZSL.TO, ZZB.US), account names (margin,
margin2, tfsa, roth), no ids or amounts of 1,000 or more with cents.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib import trade_stats as TS
from taxjson.lib.core import CanadaTaxRules, TaxTransaction, USATaxRules
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`taxjson -C root args...` from this tree's sources
    (python_module_argv, never `-m` from the cwd)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _project(tmp: str, country: str, year: int, books: dict,
             types: dict) -> Path:
    """A project with one .tt book per account ({account: lines})."""
    root = Path(tmp) / "p"
    base = "USD" if country == "usa" else "CAD"
    toml = (f'[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = []\n')
    for acct, typ in types.items():
        toml += f'[accounts.{acct}]\ntype = "{typ}"\n'
    (root / "inputs").mkdir(parents=True)
    (root / "taxjson.toml").write_text(toml)
    for acct, lines in books.items():
        d = root / "inputs" / acct
        d.mkdir()
        (d / f"{acct}.tt").write_text("\n".join(lines) + "\n")
    return root


def _run_json(test, root: Path, *args: str) -> dict:
    r = _cli(root, *args, "--json")
    test.assertEqual(r.returncode, 0, r.stderr[-3000:])
    return json.loads(r.stdout)


def _tx(d, q, net, sym, cur, account="margin"):
    return TaxTransaction(action="BUYSELL", date=d, date_settle=d,
                          time="09:30:00", symbol=sym, quantity=q,
                          net_amount=net, currency=cur, account=account)


def _pnls(rows):
    """Each disposition's P/L as `stats` counts it (rounded)."""
    doc = {"transactions": [dict(r, account=r.get("account") or "margin")
                            for r in rows]}
    res = TS.closed_trades({"margin": doc}, {"margin": []},
                           lambda d: True, lambda t: t["date"])
    return [round(t["pnl"], 4) for t in res["trades"]]


def _sales(rows):
    return [r for r in rows if r.get("qty")]


class TestDeniedLossCountsOnce(unittest.TestCase):

    @rule("CA-RPT-16")
    def test_planning_us_1_canada_superficial_loss_counts_once(self):
        """planning-us#1: stats / win-rates count a superficial-loss
        denied loss twice once the replacement is sold. Buy 100 @10,
        sell @9 (-100), rebuy 100 @9 inside 30 days (ACB 900 + the
        denied 100), sell @10: -100 and +100, one win and one loss, net
        0 — the denied 100 only on its own line."""
        sym = "ZZSL.TO"
        with self.subTest("the engine records the ACB addition"):
            res = CanadaTaxRules().compute_gains([
                _tx("2024-02-01", 100, 1000, sym, "CAD"),
                _tx("2024-03-01", -100, 900, sym, "CAD"),
                _tx("2024-03-11", 100, 900, sym, "CAD"),
                _tx("2024-06-03", -100, 1000, sym, "CAD")])
            rows = _sales(res["transactions"])
            self.assertEqual([(r["cost"], r["raw_gain"]) for r in rows],
                             [(1000.0, -100.0), (1000.0, 0.0)])
            self.assertNotIn("deferred_loss_recovered", rows[0])
            self.assertAlmostEqual(rows[1]["deferred_loss_recovered"], 100.0)
            self.assertEqual(_pnls(rows), [-100.0, 100.0])
        with self.subTest("a pool sold in parts releases it pro rata"):
            # 200 shares (1,950 of ACB, 100 of it the denied loss): 50 sold
            # carry 25, the other 150 carry 75. Bought 2,850, sold 2,600.
            res = CanadaTaxRules().compute_gains([
                _tx("2024-02-01", 100, 1000, sym, "CAD"),
                _tx("2024-03-01", -100, 900, sym, "CAD"),
                _tx("2024-03-11", 100, 900, sym, "CAD"),
                _tx("2024-05-01", 100, 950, sym, "CAD"),
                _tx("2024-06-03", -50, 500, sym, "CAD"),
                _tx("2024-07-03", -150, 1200, sym, "CAD")])
            rows = _sales(res["transactions"])
            self.assertEqual([r.get("deferred_loss_recovered")
                              for r in rows], [None, 25.0, 75.0])
            self.assertEqual(_pnls(rows), [-100.0, 37.5, -187.5])
        with self.subTest("stats and win-rates"):
            with tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp, "canada", 2024, {"sl": [
                    "BUYSELL 2024-02-01 09:30:00 ZZSL.TO 100 CAD 10.00 1000.00 0",  # pii-ok
                    "BUYSELL 2024-03-01 09:30:00 ZZSL.TO -100 CAD 9.00 900.00 0",
                    "BUYSELL 2024-03-11 09:30:00 ZZSL.TO 100 CAD 9.00 900.00 0",
                    "BUYSELL 2024-06-03 09:30:00 ZZSL.TO -100 CAD 10.00 1000.00 0",  # pii-ok
                ]}, {"sl": "taxable"})
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                st = _run_json(self, root, "stats", "sl")
                ls = st["classes"]["long-shares"]
                self.assertEqual((ls["trades"], ls["wins"], ls["losses"],
                                  ls["net_pl"], ls["largest_win"]),
                                 (2, 1, 1, 0.0, 100.0))
                self.assertEqual(st["denied_total"], 100.0)
                wr = _run_json(self, root, "win-rates", "ZZSL")
                self.assertEqual(wr["total"], st["total"])

    @rule("US-RPT-12")
    def test_planning_us_1_usa_wash_sale_counts_once(self):
        """planning-us#1: stats / win-rates count a wash-sale denied loss
        twice once the replacement is sold. Buy 100 @50, sell @40
        (-1,000), buy 100 @41 inside the window (basis 4,100 + the 1,000
        disallowed), sell @45: -1,000 and +400, net -600, one win."""
        sym = "ZZB.US"
        with self.subTest("long: the lot's basis add"):
            res = USATaxRules().compute_gains([
                _tx("2024-02-01", 100, 5000, sym, "USD"),
                _tx("2024-06-03", -100, 4000, sym, "USD"),
                _tx("2024-06-17", 100, 4100, sym, "USD"),
                _tx("2024-07-15", -100, 4500, sym, "USD")])
            rows = _sales(res["transactions"])
            self.assertEqual([(r["cost"], r["raw_gain"]) for r in rows],
                             [(5000.0, -1000.0), (5100.0, -600.0)])
            self.assertAlmostEqual(rows[1]["deferred_loss_recovered"], 1000.0)
            self.assertEqual(_pnls(rows), [-1000.0, 400.0])
        with self.subTest("short: the cut to the re-short's proceeds"):
            # Short 100 @50, cover @55 (-500), re-short @55 in the window
            # (proceeds 5,500 - 500), cover 60 then 40 @52: +180, +120.
            res = USATaxRules().compute_gains([
                _tx("2026-02-02", -100, 5000, "ZZS.US", "USD"),
                _tx("2026-03-02", 100, 5500, "ZZS.US", "USD"),
                _tx("2026-03-10", -100, 5500, "ZZS.US", "USD"),
                _tx("2026-05-04", 60, 3120, "ZZS.US", "USD"),
                _tx("2026-05-05", 40, 2080, "ZZS.US", "USD")])
            rows = _sales(res["transactions"])
            self.assertEqual([r.get("deferred_loss_recovered")
                              for r in rows], [None, 300.0, 200.0])
            self.assertEqual(_pnls(rows), [-500.0, 180.0, 120.0])
        with self.subTest("a replacement sold before the loss"):
            # US-WASH-22: its row's cost carries the add.
            rows = [dict(id="r", date="2026-02-10", symbol="ZZR.US", qty=100,
                         cost=5500, proceeds=4600, raw_gain=-900, gain=-900,
                         wash_basis_added=[{"amount": 1000.0, "qty": 100}]),
                    dict(id="l", date="2026-02-20", symbol="ZZR.US", qty=100,
                         cost=5000, proceeds=4000, raw_gain=-1000, gain=0,
                         disallowed_amount=1000)]
            self.assertEqual(_pnls(rows), [100.0, -1000.0])
        with self.subTest("win-rates, an IRA buy inside the window"):
            # 30 shares bought in the IRA (that part denied for good), 40
            # in the account (400 added to their basis): -1,000 and +160.
            with tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp, "usa", 2026, {
                    "margin": [
                        "BUYSELL 2026-02-02 09:30:00 ZZB.US 100 USD 50.00 5000.00 0",  # pii-ok
                        "BUYSELL 2026-06-01 09:30:00 ZZB.US -100 USD 40.00 4000.00 0",  # pii-ok
                        "BUYSELL 2026-06-15 09:30:00 ZZB.US 40 USD 41.00 1640.00 0",  # pii-ok
                        "BUYSELL 2026-07-15 09:30:00 ZZB.US -40 USD 45.00 1800.00 0",  # pii-ok
                    ],
                    "roth": [
                        "BUYSELL 2026-06-10 09:30:00 ZZB.US 30 USD 40.50 1215.00 0",  # pii-ok
                    ]}, {"margin": "taxable", "roth": "sheltered"})
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                wr = _run_json(self, root, "win-rates", "ZZB")
                t = wr["total"]
                self.assertEqual((t["trades"], t["wins"], t["losses"],
                                  t["net_pl"], t["gross_wins"]),
                                 (2, 1, 1, -840.0, 160.0))
                # 700 denied: the IRA's 300 and the account's 400.
                self.assertEqual(wr["denied_total"], 700.0)
                st = _run_json(self, root, "stats")
                self.assertEqual(st["total"], t)

    @rule("CA-RPT-16")
    def test_brokers_inputs_2_pooled_partial_denial_counts_once(self):
        """brokers-inputs#2: stats / win-rates 'P/L before any
        superficial-loss denial' counts part of a denied loss twice.
        150 shares (800 of ACB) in margin; 80 sold at a loss of 106.67;
        50 bought inside the window — 20 in margin2 (their 26.67 added
        to the pooled ACB), 30 in the TFSA (40.00 denied for good). The
        margin's last 70, sold, carry 70/90 of the 26.67 (20.74): its
        P/L before the denial is -39.15, not -59.89."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, "canada", 2024, {
                "margin": [
                    "BUYSELL 2024-01-10 09:30:00 ZZA.TO 100 CAD 5.00 500.00 0",
                    "BUYSELL 2024-02-12 09:30:00 ZZA.TO 50 CAD 6.00 300.00 0",
                    "BUYSELL 2024-06-03 09:30:00 ZZA.TO -80 CAD 4.00 320.00 0",
                    "BUYSELL 2024-09-10 09:30:00 ZZA.TO -70 CAD 4.50 315.00 0",
                ],
                "margin2": [
                    "BUYSELL 2024-06-10 09:30:00 ZZA.TO 20 CAD 4.10 82.00 0",
                ],
                "tfsa": [
                    "BUYSELL 2024-06-20 09:30:00 ZZA.TO 30 CAD 4.05 121.50 0",
                ]}, {"margin": "taxable", "margin2": "taxable",
                     "tfsa": "sheltered"})
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            doc = json.loads((root / "work" / "margin_gains_wash.json")
                             .read_text())
            rows = _sales(doc["transactions"])
            self.assertEqual([round(r["raw_gain"], 2) for r in rows],
                             [-106.67, -59.89])
            self.assertAlmostEqual(rows[1]["deferred_loss_recovered"],
                                   20.7407, places=3)
            st = _run_json(self, root, "stats")
            ls = st["classes"]["long-shares"]
            self.assertEqual((ls["trades"], ls["losses"], ls["net_pl"],
                              ls["largest_loss"]), (2, 2, -145.82, -106.67))
            self.assertEqual(st["denied_total"], 66.67)
            wr = _run_json(self, root, "win-rates", "ZZA", "margin")
            self.assertEqual(wr["total"]["net_pl"], -145.82)


if __name__ == "__main__":
    unittest.main()
