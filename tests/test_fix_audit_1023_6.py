"""Workflow audit 2026-10-23, item 6 (~/workflow-audit-2026-10-23.md is
the maintainer's report): `wash-radar ACCOUNT` read only that account's
books, so a sibling taxable account's rebuy or loss was invisible. In
Canada a loss rebought in the other account read COOLING in the seller's
view and EXITABLE ("a FULL exit is fine") in the buyer's; in the US a
loss the engine already washed read COOLING / EXITABLE. The account now
scopes the rows shown; every book still decides each status, as the
engine pools them. The run's reports/wash_radar_<account>.* are the same
view.

Every fixture is SYNTHETIC: made-up tickers ZZ*.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]

TOML = """[settings]
year = 2026
country = "{country}"
base_currency = "{cur}"
source_currencies = []

[accounts.margin]
type = "taxable"

[accounts.ib]
type = "taxable"

[accounts.roth]
type = "sheltered"
"""


def _tjs(root, *args):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        check=False, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


def _project(tmp, country):
    """margin buys 50 ZZY and sells them at a loss on 2026-10-01; ib
    buys 50 ZZY on 2026-10-15 (still held) and holds ZZQ, which margin
    never traded; roth holds ZZR."""
    cur, sfx = (("CAD", "TO") if country == "canada" else ("USD", "US"))
    root = Path(tmp) / country
    for a in ("margin", "ib", "roth"):
        (root / "inputs" / a).mkdir(parents=True)
    (root / "taxjson.toml").write_text(TOML.format(country=country,
                                                   cur=cur))
    (root / "inputs" / "margin" / "margin.tt").write_text(
        f"BUYSELL 2026-03-02 10:00:00 ZZY.{sfx} 50 {cur} 20.00 1000.00 0\n"
        f"BUYSELL 2026-10-01 10:00:00 ZZY.{sfx} -50 {cur} 15.00 750.00 0\n")
    (root / "inputs" / "ib" / "ib.tt").write_text(
        f"BUYSELL 2026-10-15 10:00:00 ZZY.{sfx} 50 {cur} 14.00 700.00 0\n"
        f"BUYSELL 2026-02-02 10:00:00 ZZQ.{sfx} 10 {cur} 10.00 100.00 0\n")
    (root / "inputs" / "roth" / "roth.tt").write_text(
        f"BUYSELL 2026-01-12 10:00:00 ZZR.{sfx} 10 {cur} 10.00 100.00 0\n")
    r = _tjs(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr[-3000:]
    return root, sfx


def _rows(doc):
    return {r["ticker"]: sec["category"]
            for sec in doc.get("sections") or []
            for r in sec.get("rows") or []}


class _Base:
    country = ""
    want = ""      # ZZY's status in every view

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root, cls.sfx = _project(cls._tmp.name, cls.country)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _radar(self, *acct):
        r = _tjs(self.root, "wash-radar", *acct, "--date", "2026-10-16",
                 "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_every_scope_agrees_with_the_pooled_radar(self):
        zzy = f"ZZY.{self.sfx}"
        pooled = _rows(self._radar())
        self.assertEqual(pooled.get(zzy), self.want, pooled)
        for acct in ("margin", "ib"):
            doc = self._radar(acct)
            rows = _rows(doc)
            # Before: margin COOLING (it saw no rebuy), ib EXITABLE.
            self.assertEqual(rows.get(zzy), self.want, (acct, rows))
            self.assertEqual(doc["account"], acct)
            self.assertTrue(doc["rows_scoped"])
        # The rows are the account's own listings only.
        self.assertNotIn(f"ZZQ.{self.sfx}", _rows(self._radar("margin")))
        self.assertIn(f"ZZQ.{self.sfx}", _rows(self._radar("ib")))
        self.assertNotIn(f"ZZR.{self.sfx}", _rows(self._radar("ib")))

    def test_text_view_says_the_rows_are_scoped(self):
        r = _tjs(self.root, "wash-radar", "margin", "--date", "2026-10-16")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ROWS: the listings of margin; every account's "
                      "books decide each status", r.stdout)
        self.assertIn(self.want, r.stdout)
        r = _tjs(self.root, "wash-radar", "--date", "2026-10-16")
        self.assertNotIn("ROWS:", r.stdout)

    def test_run_sidecar_is_the_same_view(self):
        doc = json.loads((self.root / "reports"
                          / "wash_radar_margin.json").read_text())
        self.assertTrue(doc["rows_scoped"])
        self.assertEqual(set(_rows(doc)), {f"ZZY.{self.sfx}"})


class TestCanada(_Base, unittest.TestCase):
    country = "canada"
    # ib's rebuy, inside the window and still held, backs a denial of
    # margin's loss (one taxable pool): rescue it by selling the rebuy.
    want = "VIOLATION"

    @rule("CA-SL-03")
    def test_every_scope_agrees_with_the_pooled_radar(self):
        super().test_every_scope_agrees_with_the_pooled_radar()

    def test_engine_denies_the_loss(self):
        r = _tjs(self.root, "wash-sales")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"margin\s+2026-10-01\s+ZZY\.TO\s+50\s"
                                   r".*-250\.00\s+250\.00")


class TestUSA(_Base, unittest.TestCase):
    country = "usa"
    # ib's purchase within 30 days washes margin's loss (any account).
    want = "WASHED"

    @rule("US-WASH-04")
    def test_every_scope_agrees_with_the_pooled_radar(self):
        super().test_every_scope_agrees_with_the_pooled_radar()


if __name__ == "__main__":
    unittest.main()
