"""Crypto sends: classify every unmatched outgoing crypto transfer as
self / gift / payment, price the dispositions, and generate the .tt
BUYSELL lines (`tjs crypto-sends`, the `tjs run` prompt, the
checklist step). Stablecoin gifts/payments get the FX-gain calculation
instead of a sale line.

Synthetic ledgers only. Rates: a hand-written work/to_base.csv, and the
same rates in the temporary HOME's currency cache (_home_rates: a run
may rebuild the file); coin prices: a fill-crypto cache under that HOME
with TAXJSON_OFFLINE set, so nothing here touches the network.

Formerly: test_fix_sends
"""
import json
import tempfile
import unittest
from tax_rules import rule
from taxjson.rules import ids as R
from _support.crypto_sends import PAY_ID, _cad_usd_rates_file, _project, _cli


class TestUsGift(unittest.TestCase):
    """Formerly: test_fix_sends.TestUsGift"""
    @rule(R.US_SEND_02)
    def test_us_project_refuses_gift(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td, country="usa")
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = ["CAD"]\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            _cad_usd_rates_file(root / "work" / "to_base.csv")
            # Coinbase rows are CAD-priced; keep the US fixture to Kraken.
            (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            g = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{PAY_ID}=gift")
            self.assertNotEqual(g.returncode, 0)
            self.assertIn("not a sale", g.stderr)
            p = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{PAY_ID}=payment")
            self.assertEqual(p.returncode, 0, p.stderr)

    @rule(R.US_SEND_01)
    def test_us_payment_is_written_as_a_sale_at_fair_value(self):
        # A2-0851: a payment in a US project is a sale at fair value in
        # crypto_sends.tt, booked by the next run (US-SEND-01).
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td, country="usa")
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = ["CAD"]\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            _cad_usd_rates_file(root / "work" / "to_base.csv")
            (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            p = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{PAY_ID}=payment")
            self.assertEqual(p.returncode, 0, p.stderr)
            w = _cli(root, home, "crypto-sends", "crypto", "--write")
            self.assertEqual(w.returncode, 0, w.stderr)
            body = (root / "inputs" / "crypto" / "crypto_sends.tt"
                    ).read_text()
            lines = [ln for ln in body.splitlines()
                     if ln.startswith("BUYSELL") and " QZL " in ln]
            self.assertEqual(len(lines), 1, body)
            self.assertTrue(lines[0].startswith(
                "BUYSELL 2026-04-13 17:30:00 QZL -2.5 "), lines[0])
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            base = json.loads((root / "work" / "crypto_base.json")
                              .read_text())["transactions"]
            sale = [t for t in base if t["symbol"] == "QZL"
                    and t["action"] == "BUYSELL"
                    and abs(float(t["quantity"]) + 2.5) < 1e-12]
            self.assertEqual(len(sale), 1)
            self.assertGreater(float(sale[0]["net_amount"]), 0.0)


if __name__ == "__main__":
    unittest.main()
