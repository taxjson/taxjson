"""The 2026-10-29 fix round 3, group wash: the superficial-loss views
and the planning tools (backlog TJ-0600, TJ-0620, TJ-0599, TJ-0615).
Synthetic data only: made-up tickers (ZZR, OTH, SYN, ZZC, ZZQ) and
account numbers in throwaway folders.

Formerly: test_fix_1029_r3_wash"""
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_toml import _toml


def _flat(text: str) -> str:
    """Whitespace-collapsed: where a line breaks differs with the temp
    path's length (backlog TJ-0625)."""
    return " ".join(text.split())


class TestShelteredAccountExplain(unittest.TestCase):
    """TJ-0600: the RRSP buys ZZR.TO 10 at 10, sells them at 5 and buys
    10 back 7 days later. `wash-sales rrsp` says no superficial losses;
    `wash-sales rrsp --explain` traced the RRSP sale as a superficial
    loss, each window row twice (the book merged as taxable and again
    as the sheltered context). A registered account's own sale is never
    one: the trace now says what the table says, and why.

    Formerly: test_fix_1029_r3_wash.TestShelteredAccountExplain"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.roots = {}
        for country, acct, sym, cur in (("canada", "rrsp", "ZZR.TO", "CAD"),
                                        ("usa", "ira", "ZZR.US", "USD")):
            root = Path(cls._td.name) / country
            root.mkdir()
            (root / "taxjson.toml").write_text(_toml(
                country, year=2026,
                extra=f'[accounts.{acct}]\ntype = "sheltered"\n'))
            books = {
                acct: (f"BUYSELL 2026-01-05 10:00:00 {sym} 10 {cur} 10 100 0\n"
                       f"BUYSELL 2026-02-02 10:00:00 {sym} -10 {cur} 5 50 0\n"
                       f"BUYSELL 2026-02-09 10:00:00 {sym} 10 {cur} 5 50 0\n"),
                "margin": (f"BUYSELL 2026-01-05 10:00:00 OTH{sym[3:]} 10 "
                           f"{cur} 10 100 0\n"),
            }
            for a, text in books.items():
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / "m.tt").write_text(text)
            tj(root, "run", "--no-input")
            cls.roots[country] = (root, acct, sym)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _check_none(self, country, term):
        root, acct, sym = self.roots[country]
        table = json.loads(tj(root, "wash-sales", acct, "--json").stdout)
        self.assertEqual(table["rows"], [])
        r = tj(root, "wash-sales", acct, "--explain", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = _flat(r.stdout)
        self.assertIn(f"No {term} for account '{acct}' — it is a "
                      f"sheltered account.", out)
        self.assertIn("Its purchases can deny a taxable account's loss",
                      out)
        self.assertNotIn(sym, r.stdout + r.stderr)
        self.assertNotIn("repeat an earlier row", r.stderr)

    @rule(R.CA_ACB_05)
    def test_tj0600_explain_says_none_for_an_rrsp(self):
        self._check_none("canada", "superficial losses")

    @rule(R.US_BASIS_07)
    def test_tj0600_explain_says_none_for_an_ira(self):
        self._check_none("usa", "wash sales")

    @rule(R.CA_ACB_05)
    def test_tj0600_the_project_trace_has_no_registered_sale(self):
        root, _acct, sym = self.roots["canada"]
        r = tj(root, "wash-sales", "--explain", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(f"{sym} — 2026-02-02", r.stdout)


if __name__ == "__main__":
    unittest.main()
