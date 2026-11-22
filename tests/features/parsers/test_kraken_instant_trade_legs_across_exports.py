"""Kraken instant trades whose legs two ledger exports split (backlog
TJ-4463): the trade is booked once, by the export that books it, and each
export moves the counts of its own rows only. A receipt two exports split
is one leg; counting both of its rows in the export holding the first
stopped the parse with "TypeError: 3 row(s) moved from consumed '', 2
counted so: a parser bug (rows another file read?)". All data synthetic.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

_H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance,"
      "amountusd,feeusd,feecurrency\n")


class TestAReceiptTwoExportsSplit(unittest.TestCase):

    def _run(self, files):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        for name, rows in files.items():
            (d / name).write_text(_H + rows, encoding="utf-8")
        r = subprocess.run(python_module_argv(
            "taxjson.bin.taxjson_brokerage",
            ["--brokerage", "kraken", *(str(d / n) for n in files), "--country", "canada",
             "--account", "crypto", "--cash-out", str(d / "a.cash")]),
            cwd=d, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600,
            env=dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                     TAXJSON_LOCAL_TZ="America/Toronto"))
        return r, d

    def test_a_conversion_whose_receipt_two_exports_split(self):
        # CAD for USD, the USD received in two rows, one in each export: a
        # currency conversion (no tax object), counted as the non-event it
        # is: two rows in the export that books it, one in the other.
        r, d = self._run({
            "kraken_ledgers_a.csv":
                "LA-1,TR-1,2025-02-18 03:45:00,spend,,currency,ZCAD,spot / main,-1300.00,0,700.00,0,0,CAD\n"
                "LA-2,TR-1,2025-02-18 03:45:00,receive,,currency,ZUSD,spot / main,600.00,0,600.00,0,0,USD\n",
            "kraken_ledgers_b.csv":
                "LB-1,TR-1,2025-02-18 03:45:00,receive,,currency,ZUSD,spot / main,400.00,0,1000.00,0,0,USD\n"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("booked once, here", r.stderr)
        self.assertIn("kraken_ledgers_a.csv: 2 recognized non-event row(s) not translated — "
                      "forex conversion CAD->USD", r.stderr)
        self.assertIn("kraken_ledgers_b.csv: 1 recognized non-event row(s) not translated — "
                      "instant-trade leg booked with its counter-leg in kraken_ledgers_a.csv: 1",
                      r.stderr)
        files = json.loads((d / "a.cash").read_text(encoding="utf-8"))["files"]
        self.assertEqual({f["name"]: [(e["from_ccy"], e["from_amt"], e["to_ccy"], e["to_amt"])
                                      for e in f["events"] if e["kind"] == "FXCONV"]
                          for f in files},
                         {"kraken_ledgers_a.csv": [("CAD", 1300.0, "USD", 1000.0)],
                          "kraken_ledgers_b.csv": [("CAD", 1300.0, "USD", 1000.0)]})

    def test_a_coin_bought_with_a_receipt_two_exports_split(self):
        # USD for a coin received in two rows, one in each export: one
        # purchase of the whole, booked by the export holding the spend.
        r, d = self._run({
            "kraken_ledgers_a.csv":
                "LA-0,R0,2025-02-01 15:00:00,deposit,,currency,ZUSD,spot / main,1000,0,1000,1000,0,USD\n"
                "LA-1,TR-2,2025-02-18 03:45:00,spend,,currency,ZUSD,spot / main,-1000.00,0,0,1000,0,USD\n"
                "LA-2,TR-2,2025-02-18 03:45:00,receive,,currency,XXBT,spot / main,0.006,0,0.006,600,0,BTC\n",
            "kraken_ledgers_b.csv":
                "LB-1,TR-2,2025-02-18 03:45:00,receive,,currency,XXBT,spot / main,0.004,0,0.01,400,0,BTC\n"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("kraken_ledgers_a.csv: 1 tax objects", r.stderr)
        self.assertIn("kraken_ledgers_b.csv: 1 recognized non-event row(s) not translated — "
                      "instant-trade leg booked with its counter-leg in kraken_ledgers_a.csv: 1",
                      r.stderr)


if __name__ == "__main__":
    unittest.main()
