"""Issue #93: a Coinbase export WITH an ID column whose cells are blank.

Rows without a usable ID took neither path: no broker id, and the
split-fill guard ran only when the header had no ID column at all — so
two genuine identical same-second fills hashed to one content id and the
dedup stage kept one (gain understated, a phantom short). A row without
an ID now gets the no-ID-column treatment whatever the header says,
while explicit IDs and the cross-file overlap rule keep working.

All rows are synthetic.
"""
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_HDR = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of Fees and/or Spread),Fees and/or Spread,Notes\n")
_BUY = ",2025-01-06 16:00:00 UTC,Buy,ETH,1,CAD,1000,1000,1000,0,\n"
_SELL = ",2025-04-01 16:00:00 UTC,Sell,ETH,2,CAD,1500,3000,3000,0,\n"


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _parse(td, name, content):
    from taxjson.lib.brokerages.coinbase import CoinbaseBrokerage
    p = Path(td) / name
    p.write_text(content)
    return CoinbaseBrokerage().parse_file(p)


def _books(files, parser=None):
    """{file name: csv text} -> the rows the dedup stage keeps."""
    from taxjson.bin.taxjson_sort import deduplicate
    from taxjson.lib.core import TaxTransaction
    names = {f.name for f in dataclasses.fields(TaxTransaction)}
    txs = []
    with tempfile.TemporaryDirectory() as td:
        for name, content in files.items():
            for d in (parser or _parse)(td, name, content):
                d = {k: v for k, v in d.items() if k in names}
                d["source"] = name
                txs.append(TaxTransaction(**d))
    return deduplicate(txs)


class TestBlankCoinbaseIds(unittest.TestCase):

    def test_all_blank_ids_keep_identical_fills(self):
        kept = _books({"coinbase.csv": _HDR + _BUY + _BUY + _SELL})
        buys = [t for t in kept if t.quantity > 0]
        self.assertEqual(len(buys), 2,
                         "two identical same-second purchases are two "
                         "purchases (issue #93)")
        self.assertEqual(len(kept), 3)

    def test_mixed_populated_and_blank_ids(self):
        kept = _books({"coinbase.csv": _HDR + "CB-T-1" + _BUY + _BUY
                       + _BUY + _SELL})
        buys = [t for t in kept if t.quantity > 0]
        self.assertEqual(len(buys), 3)
        self.assertIn("CB-T-1", [t.id for t in buys],
                      "an explicit ID is kept as the row's id")
        self.assertEqual(len({t.id for t in buys}), 3)

    def test_explicit_ids_are_not_marked(self):
        with tempfile.TemporaryDirectory() as td:
            txs = _parse(td, "c.csv", _HDR + "CB-T-1" + _BUY + "CB-T-2"
                         + _BUY)
        self.assertEqual([t["id"] for t in txs], ["CB-T-1", "CB-T-2"])
        self.assertFalse(any("fill #" in (t.get("description") or "")
                             for t in txs))

    def test_overlapping_blank_id_exports_count_once(self):
        body = _HDR + _BUY + _BUY + _SELL
        kept = _books({"coinbase_2025.csv": body,
                       "coinbase_ytd.csv": body})
        self.assertEqual(len(kept), 3,
                         "the same blank-ID rows in two overlapping "
                         "exports are one set of trades")
        self.assertEqual(sum(t.quantity for t in kept if t.quantity > 0),
                         2)

    def test_overlap_matches_the_no_id_column_layout(self):
        # The blank-ID rows take the no-ID-column path: same ids.
        with tempfile.TemporaryDirectory() as td:
            a = _parse(td, "a.csv", _HDR + _BUY + _BUY)
            no_col = _HDR.split(",", 1)[1]
            b = _parse(td, "b.csv", no_col + _BUY[1:] + _BUY[1:])
        self.assertEqual([t.get("description") for t in a],
                         [t.get("description") for t in b])

    def test_blank_id_convert_legs_share_one_stem(self):
        row = (",2025-05-01 10:00:00 UTC,Convert,BTC,0.05,CAD,40000,"
               "2000,2010,10,Converted 0.05 BTC to 1.2 ETH\n")
        with tempfile.TemporaryDirectory() as td:
            txs = _parse(td, "c.csv", _HDR + row + row)
        ids = [t.get("id") for t in txs]
        self.assertEqual(len(ids), 4)
        self.assertTrue(all(ids), "both legs carry one stem id (A2-0998)")
        self.assertEqual(len(set(ids)), 4,
                         "two identical converts stay two")
        self.assertEqual(ids[0].rsplit("-", 1)[0], ids[1].rsplit("-", 1)[0])

    def test_blank_id_crypto_pair_legs_share_one_stem(self):
        row = (",2025-05-01 10:00:00 UTC,Buy,ETH,1,CAD,3000,3000,3000,0,"
               "Bought 1 ETH for 0.05 BTC on ETH-BTC\n")
        with tempfile.TemporaryDirectory() as td:
            txs = _parse(td, "c.csv", _HDR + row + row)
        ids = [t.get("id") for t in txs]
        self.assertTrue(all(ids), ids)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids[1], ids[0] + "-quote")

    def test_issue_93_repro_books_both_purchases(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "coins").mkdir(parents=True)
            (root / "inputs" / "coins" / "coinbase.csv").write_text(
                _HDR + _BUY + _BUY + _SELL)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear=2025\ncountry="canada"\n'
                'base_currency="CAD"\nsource_currencies=[]\n'
                'local_timezone="America/Toronto"\n'
                '[accounts.coins]\ntype="taxable"\ncrypto=true\n')
            env = dict(os.environ, TAXJSON_OFFLINE="1")

            def cli(*a):
                return subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run",
                     "-C", str(root), *a], cwd=REPO_ROOT, env=env,
                    capture_output=True, text=True,
                    stdin=subprocess.DEVNULL)
            r = cli("run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("short", (r.stdout + r.stderr).lower())
            s = cli("sum", "--json")
            self.assertEqual(s.returncode, 0, s.stderr[-2000:])
            acct = json.loads(s.stdout)["filing"]["accounts"][0]
            self.assertAlmostEqual(acct["gain"], 1000.0, places=2)
            self.assertAlmostEqual(acct["proceeds"], 3000.0, places=2)
            self.assertAlmostEqual(acct["acb"], 2000.0, places=2)


# The same gap on Kraken: the txid column is required, but a blank cell
# took no id and no split-fill marks.
_KT = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,margin,"
       "misc,ledgers\n")
_KBUY = ",,BTC/CAD,2025-01-15 10:00:00.1234,buy,limit,500,500,0,1.0,,,\n"
_KL = "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
_KSTAKE = ",,2025-06-01 12:00:00,staking,,currency,SOL.S,spot,0.1,0,0.1\n"


def _kparse(td, name, content):
    from taxjson.lib.brokerages.kraken import KrakenBrokerage
    p = Path(td) / name
    p.write_text(content)
    import io
    from contextlib import redirect_stderr, redirect_stdout
    with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
        return KrakenBrokerage().parse_file(p)


class TestBlankKrakenTxids(unittest.TestCase):

    def test_blank_txid_identical_fills_are_two(self):
        kept = _books({"kr_trades.csv": _KT + _KBUY + _KBUY}, _kparse)
        self.assertEqual(len(kept), 2)

    def test_explicit_txids_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            txs = _kparse(td, "kr_trades.csv",
                          _KT + "TQ1" + _KBUY + "TQ2" + _KBUY + _KBUY)
        self.assertEqual([t.get("id") for t in txs][:2], ["TQ1", "TQ2"])
        self.assertFalse(any("fill #" in (t.get("description") or "")
                             for t in txs[:2]))

    def test_overlapping_blank_txid_exports_count_once(self):
        body = _KT + _KBUY + _KBUY
        kept = _books({"kr_trades_a.csv": body, "kr_trades_b.csv": body},
                      _kparse)
        self.assertEqual(len(kept), 2)

    def test_blank_txid_pair_legs_share_one_stem(self):
        row = ",,ETH/XBT,2025-01-15 10:00:00.1234,buy,limit,0.05,0.05,0,1.0,,,\n"
        with tempfile.TemporaryDirectory() as td:
            txs = _kparse(td, "kr_trades.csv", _KT + row + row)
        ids = [t.get("id") for t in txs]
        self.assertEqual(len(ids), 4)
        self.assertTrue(all(ids), ids)
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(ids[0].rsplit("-", 1)[0], ids[1].rsplit("-", 1)[0])

    def test_blank_txid_ledger_rewards_are_two(self):
        kept = _books({"kr_ledgers.csv": _KL + _KSTAKE + _KSTAKE}, _kparse)
        self.assertEqual(len(kept), 4, "two rewards: two incomes, two buys")


if __name__ == "__main__":
    unittest.main()
