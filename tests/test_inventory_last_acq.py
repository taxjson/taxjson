"""Engine inventory carries `last_acq_date` — the most recent buy, the
date the 30-day superficial-loss / wash window measures from (an add to
an old position matters even when position_start_date is years old).

Both engines: Canada ACB pool tracks it directly; US FIFO derives it
from the newest surviving lot. `taxjson harvest --sheltered` consumes it
for the SH_ADD column.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _gains(country, base_doc, tmp):
    base = Path(tmp) / "acct_base.json"
    base.write_text(json.dumps(base_doc))
    r = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_gains",
         "--country", country, "--year", "2025", "--taxable", str(base)],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _buy(dt, qty, price):
    return {"action": "BUYSELL", "date": dt, "date_settle": dt,
            "time": "09:30:00", "symbol": "XYZ.TO", "quantity": qty,
            "price": price, "net_amount": abs(qty) * price,
            "currency": "CAD", "account": "acct"}


BASE = {"transactions": [_buy("2025-03-01", 10, 10.0),
                         _buy("2025-06-02", 5, 12.0)]}


class TestInventoryLastAcq(unittest.TestCase):
    def _inv_row(self, country):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = _gains(country, BASE, tmp)
        inv = out.get("inventory") or []
        self.assertEqual(len(inv), 1, inv)
        return inv[0]

    def test_canada_pool_last_acq_is_latest_buy(self):
        row = self._inv_row("canada")
        self.assertEqual(row["position_start_date"], "2025-03-01")
        self.assertEqual(row["last_acq_date"], "2025-06-02")

    def test_usa_fifo_last_acq_is_newest_surviving_lot(self):
        row = self._inv_row("usa")
        self.assertEqual(row["position_start_date"], "2025-03-01")
        self.assertEqual(row["last_acq_date"], "2025-06-02")


if __name__ == "__main__":
    unittest.main()
