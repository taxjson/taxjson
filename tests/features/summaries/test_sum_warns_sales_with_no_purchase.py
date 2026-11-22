"""New-user guidance (owner-approved gaps from the getting-started study).

1. A closing summary at the end of `tjs run`: sales with no purchase
   not covered by missing history, $0-cost positions (sold or still
   held), transfer-ins kept out with no cost, accounts with open
   positions and no holdings check, income on a security the books do
   not hold — each with its command; silent when clean; the counts in
   reports/run_summary.json. The short-position NOTE of a taxable account
   is on the console.
2. `tjs sum` warns about uncovered sales with no purchase; "tainted"
   is "unknown cost" in every message (JSON keys kept, aliases added).
3. A transfer-in from outside the books takes the broker's STATED book
   value as its cost (never a market value), with an ATTENTION that a
   covering .tt line silences (CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).
4. Messages: the crypto time-zone refusal, the sanity mismatch hint, the
   stage-failure line, a stale missing-history line, the inputs/
   README per broker.
5. A US scaffold fetches no CAD rates.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_newuser_guidance
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import cli, settings_for
from _style import CapturedWidth
import _paths


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _qrow(td, action, sym, desc, qty, price, gross, net, cur="CAD",
          act="Trades", acct="55500001"):                      # pii-ok
    return (f"{td},{td},{action},{sym},{desc},{qty},{price},{gross},0.00,"
            f"{net},{cur},{act},{acct},Margin\n")


def _project(tmp, *, country="canada", year=2025, accounts=None,
             files=None, extra_settings=""):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, source_currencies=[])
        + extra_settings
        + (accounts or '[accounts.margin]\ntype = "taxable"\n'))
    for rel, text in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


# A Canadian margin account with one of each first-run finding.
_CA_QT = _QH + (
    _qrow("2025-02-03", "Buy", "ABC.TO", "ABC CORP", 10, "20.00",
          "-200.00", "-200.00")
    + _qrow("2025-03-03", "TF6", "XYZ.TO",
            "XYZ CORP TRANSFER BOOK VALUE 5000.00", 100, "0.00", "0.00",
            "0.00", act="Transfers")
    + _qrow("2025-03-04", "TF6", "QRS.TO", "QRS CORP TRANSFER", 50, "0.00",
            "0.00", "0.00", act="Transfers")
    + _qrow("2025-04-01", "Sell", "XYZ.TO", "XYZ CORP", -100, "60.00",
            "6000.00", "6000.00")
    + _qrow("2025-05-01", "Sell", "SMA.TO", "SMA CORP", -20, "40.00",
            "800.00", "800.00"))


class TestSumWarnsUncoveredSales(unittest.TestCase):
    """Item 2: a Questrade / RBC / Webull sale with no purchase was
    silent in `sum`.

    Formerly: test_fix_newuser_guidance.TestSumWarnsUncoveredSales"""

    def test_sum_names_the_sale_and_the_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, files={
                "inputs/margin/questrade.csv": _CA_QT})
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            r = cli(root, "sum")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("tjs sum: warning: 1 position(s) sold in "
                          "2025 with no purchase in your files: their gain "
                          "is NOT in these totals", r.stderr)
            self.assertIn("No .tt OPENING cost=unknown line: SMA.TO "
                          "(margin)", r.stderr)
            self.assertIn("tjs find-missing-history", r.stderr)
            j = json.loads(cli(root, "sum", "--json").stdout)
            self.assertEqual(j["no_purchase_uncovered"],
                             [{"symbol": "SMA.TO", "account": "margin",
                               "sales": 1, "proceeds": 800.0}])

    def test_no_user_facing_tainted_left(self):
        # Every message string of the package says "unknown cost".
        import re
        import tokenize
        root = _paths.REPO / "src" / "taxjson"
        msg = re.compile(r"\btainted (disposition|row|sale)|"
                         r"pool TAINTED|tainted \(unknown", re.I)
        hits = []
        for p in root.rglob("*.py"):
            with open(p, encoding="utf-8") as fh:
                for t in tokenize.generate_tokens(fh.readline):
                    if t.type == tokenize.STRING or t.type == getattr(
                            tokenize, "FSTRING_MIDDLE", -1):
                        if (msg.search(t.string)
                                and not t.string.lstrip("rbuf").startswith(
                                    ('"""', "'''"))):
                            hits.append(f"{p.name}:{t.start[0]}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
