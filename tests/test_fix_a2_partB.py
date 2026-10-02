"""Re-audit-2 partition fixes, part B (lists partition-03 / partition-04):
Canada/USA wording and country gates in `taxjson run`'s commands,
edge-cases, currency conversion and the country ownership tables."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, cli_both, projects_both


def _book(rows):
    return json.dumps({"transactions": rows})


def _row(action, date, symbol, qty, net, *, currency, account="c1",
         price=None, **kw):
    d = {"action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": symbol, "quantity": qty,
         "price": abs(net / qty) if (price is None and qty) else (price or 0),
         "net_amount": net, "currency": currency, "account": account}
    d.update(kw)
    return d


class TestCarryoverCryptoOnly(unittest.TestCase):
    """A2-0146 / A2-0411 / A2-0412: a crypto-only US project fed its
    books positionally, so the ledger washed crypto losses (§1091 does
    not reach digital assets, US-WASH-13)."""

    def _projects(self, td):
        files = {}
        for c, cur in (("canada", "CAD"), ("usa", "USD")):
            files[c] = _book([
                _row("BUYSELL", "2025-02-03", "BTC", 1.0, -50000.0,
                     currency=cur),
                _row("BUYSELL", "2025-08-01", "BTC", -1.0, 40000.0,
                     currency=cur),
                _row("BUYSELL", "2025-08-10", "BTC", 1.0, -41000.0,
                     currency=cur)])
        p = projects_both(
            td, accounts='[accounts.c1]\ntype = "taxable"\ncrypto = true\n')
        for c, root in p.items():
            (root / "work").mkdir(exist_ok=True)
            (root / "work" / "c1_base.json").write_text(files[c])
        return p

    @rule("US-WASH-13")
    @rule_absent("US-WASH-13", country="canada")
    @rule("CA-SL-13")
    def test_crypto_only_project_keeps_the_us_loss(self):
        with tempfile.TemporaryDirectory() as td:
            res = cli_both(self._projects(td), "carryover", "--json")
        for c, r in res.items():
            self.assertEqual(r.returncode, 0, (c, r.stderr))
        nets = {}
        for c, r in res.items():
            doc = json.loads(r.stdout)
            row = next(x for x in doc["rows"] if int(x["year"]) == 2025)
            nets[c] = row
        us = nets["usa"]
        self.assertAlmostEqual(
            float(us.get("net_st", 0.0)) + float(us.get("net_lt", 0.0)),
            -10000.0, places=2)
        # Canada: the coin is identical property, rebought and still
        # held — superficial (CA-SL-13), the year's net is 0.
        self.assertAlmostEqual(float(nets["canada"]["net_gain"]), 0.0, places=2)


TWO_TAXABLE = ('[accounts.acctA]\ntype = "taxable"\n'
               '[accounts.acctB]\ntype = "taxable"\n')


def _two_account_projects(td, a_rows, b_rows):
    return projects_both(td, accounts=TWO_TAXABLE, files={
        "inputs/acctA/acctA.tt": a_rows, "inputs/acctB/acctB.tt": b_rows})


class TestListDateBasisWording(unittest.TestCase):
    """A2-0154, A2-0410, A2-0720, A2-0734, A2-1244, A2-1266, A2-1318,
    A2-1330: `list --date` told a US project its per-account cost was an
    ACB and that the return blends it (s.47)."""

    @rule("CA-ACB-01")
    @rule_absent("CA-ACB-01", country="usa")
    @rule("US-BASIS-01")
    def test_s47_note_is_canada_only(self):
        a = "BUYSELL 2025-03-03 10:00:00 XYZ.US 100 USD 30.00 -3000.00 0.00\n"
        b = "BUYSELL 2025-03-03 10:00:00 XYZ.US 100 USD 10.00 -1000.00 0.00\n"
        with tempfile.TemporaryDirectory() as td:
            p = _two_account_projects(td, a, b)
            for c, root in p.items():
                r = cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, (c, r.stderr[-2000:]))
            t = cli_both(p, "list", "--date", "2025-06-30")
            j = cli_both(p, "list", "--date", "2025-06-30", "--json")
        for c in p:
            self.assertEqual(t[c].returncode, 0, (c, t[c].stderr))
        self.assertIn("s.47", t["canada"].stderr)
        self.assertIn("per-account ACB", json.loads(j["canada"].stdout)["basis"])
        self.assertNotIn("s.47", t["usa"].stderr)
        self.assertNotIn("ACB", t["usa"].stderr + t["usa"].stdout)
        self.assertIn("per-account FIFO basis",
                      json.loads(j["usa"].stdout)["basis"])


if __name__ == "__main__":
    unittest.main()
