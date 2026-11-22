"""Fix round 2026-10-29 (round 7), group optyield: option-yield, the
LEAPS views, ccd-sum and the report filters.

TJ-0692  `SYN.B` (a class share's bare ticker) matched no option or
         trade of SYN.B.US: the first dot came off, with the class.
TJ-0694  a futures option's x1000 stated on its close alone priced the
         open at the default 100 contract size.
TJ-0774  shares held partly at an unknown cost (an OPENING cost=unknown)
         gave a yield on the known part of their cost; list and shares
         showed that part as the whole.
TJ-0777  a closed unknown-cost long (an OPENING cost=unknown) made the
         LEAPS views read its sale as a write and a later real entry as
         its buy-back: the known LEAPS gain disappeared.
TJ-0778  ccd-sum filtered a grant-timing write's expiry by the write's
         date: an expiry inside a narrow window was not counted.
TJ-0784  option-yield of a tax year not yet begun reported today's
         closes under its label.
TJ-0785  win-rates refused a digit-leading listing (1234.HK) as an
         invalid period.
TJ-0790  an expiry carrying a fee (IB `C;Ep` with a commission) read
         as a buy-back.
TJ-0903  a written call renamed by a SPLIT and bought back in the new
         series counted an extra (inferred) expiry in ccd-sum.
TJ-0907  a series split reported by one blended Canadian account left
         another account's contracts in the old series: leaps /
         leaps-sum ACCOUNT lost the LEAPS gain, option-yield invented
         open contracts.
TJ-0909  an account named `123abc` was refused as an invalid period;
         one named `2025` was silently read as the year.
TJ-0910  option-yield did not find the class shares (SYN.B.TO) behind
         the option root SYNB.

Every fixture is synthetic: invented tickers (SYN*), account names and
amounts.

Formerly: test_fix_1029_r7_optyield
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _project(td: str, files, *, country="canada", year=2025,
             accounts=None, settings="") -> Path:
    """A project with each account's `.tt` book; `run` done."""
    root = Path(td) / "p"
    accounts = accounts or {"margin": "taxable"}
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
    for a, text in files.items():
        name, text = text if isinstance(text, tuple) else ("trades.tt", text)
        (root / "inputs" / a / name).write_text(text)
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        f'source_currencies = []\n{settings}\n'
        + "".join(f'[accounts."{a}"]\ntype = "{t}"\n'
                  for a, t in accounts.items()))
    (root / "ticker.map").write_text("")
    r = _cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return root


def _json(root: Path, *args: str) -> dict:
    r = _cli(root, *args, "--json")
    assert r.returncode == 0, (args, r.stdout[-2000:], r.stderr[-2000:])
    return json.loads(r.stdout)


# --------------------------------------------------------------------
# Digit-leading symbols and account names (TJ-0785, TJ-0909)
# --------------------------------------------------------------------

class TestDigitLeadingPositionals(unittest.TestCase):
    """TJ-0785: `win-rates 1234.HK` filters on that listing. TJ-0909:
    an account named `123abc` is selectable alone; one named `2025` is
    the account beside a period (or --all-history), and alone the
    period, said in a warning.

    Formerly: test_fix_1029_r7_optyield.TestDigitLeadingPositionals"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _project(cls._td.name, {
            "123abc": "BUYSELL 2025-02-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                      "BUYSELL 2025-03-03 10:00:00 SYN.TO -10 CAD 12 120 0\n",
            "2025": "BUYSELL 2025-02-03 10:00:00 SYM.TO 10 CAD 10 100 0\n"
                    "BUYSELL 2025-03-03 10:00:00 SYM.TO -10 CAD 11 110 0\n",
            "margin": "BUYSELL 2025-02-03 10:00:00 1234.HK 10 CAD 10 100 0\n"
                      "BUYSELL 2025-03-03 10:00:00 1234.HK -10 CAD 14 140 0\n"},
            accounts={"123abc": "taxable", "2025": "taxable",
                      "margin": "taxable"})

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _net(self, *args):
        return _json(self.root, *args)["total"]["net_pl"]

    def test_digit_leading_listing(self):
        for args in (("1234.HK",), ("1234.HK", "2025")):
            doc = _json(self.root, "win-rates", *args)
            self.assertEqual([r["key"] for r in doc["rows"]], ["1234.HK"],
                             args)
            self.assertEqual(doc["unmatched_symbols"], [])
        # A malformed period is still refused.
        r = _cli(self.root, "win-rates", "30x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid time period", r.stderr)

    def test_digit_leading_account(self):
        for cmd in ("stats", "trades", "fees", "leaps"):
            r = _cli(self.root, cmd, "123abc")
            self.assertEqual(r.returncode, 0, (cmd, r.stderr))
        self.assertEqual(_json(self.root, "stats", "123abc")["accounts"],
                         ["123abc"])
        self.assertEqual(self._net("win-rates", "123abc"), 20.0)
        doc = _json(self.root, "option-yield", "123abc")
        self.assertEqual(doc["rows"], [])

    def test_year_shaped_account(self):
        self.assertEqual(self._net("win-rates", "all", "2025"), 10.0)
        self.assertEqual(self._net("win-rates", "2025", "--all-history"),
                         10.0)
        self.assertEqual(self._net("win-rates", "2025", "2025"), 10.0)
        # Alone it is the period: every account, with a warning.
        r = _cli(self.root, "win-rates", "2025", "--json")
        self.assertEqual(json.loads(r.stdout)["total"]["net_pl"], 70.0)
        self.assertIn("both a period and an account name", r.stderr)
        r = _cli(self.root, "stats", "2025")
        self.assertIn("both a period and an account name", r.stderr)


if __name__ == "__main__":
    unittest.main()
