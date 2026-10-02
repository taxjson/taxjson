"""Re-audit-2 tests-pins (G6a): taxjson_run.py wrapper wiring and the
taxjson.toml account order (CA-DATE-14).

Each test pins a line the full suite let a mutant change: a wrapper that
forwards a flag or file to its tool, or a recompute that merges the
taxable books. Synthetic data only: `.tt` books in CAD (no FX fetch),
made-up accounts and symbols.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(root, *args, env_extra=None):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e.setdefault("PYTHONPATH", str(REPO_ROOT / "src"))
    if env_extra:
        e.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(root, accounts, books, settings="", extra_files=None):
    """A Canada 2025 project: `accounts` in taxjson.toml order, each a
    taxable account with a `.tt` book (books[name])."""
    root = Path(root)
    toml = ('[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n' + settings)
    for name in accounts:
        toml += f'\n[accounts.{name}]\ntype = "taxable"\n'
        d = root / "inputs" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "book.tt").write_text(books[name])
    (root / "taxjson.toml").write_text(toml)
    for rel, text in (extra_files or {}).items():
        (root / rel).write_text(text)
    return root


# ----------------------------------------------- CA-DATE-14 account order
# zeta sells its 100 XYZ.US at the same moment alpha buys 100. With zeta
# listed first the sale comes first (gain 200, no denial, year-end cost
# 2,000); with alpha first the sale is made from the blended pool of 200
# (cost 1,500: a 300 superficial loss, added to the 100 still held ->
# year-end cost 1,800).
_ORDER_BOOKS = {
    "zeta": ("BUYSELL 2025-01-06 10:00:00 XYZ.US 100 CAD 10 1000 0\n"
             "BUYSELL 2025-03-03 10:00:00 XYZ.US -100 CAD 12 1200 0\n"),
    "alpha": "BUYSELL 2025-03-03 10:00:00 XYZ.US 100 CAD 20 2000 0\n",
}


class _OrderProjects:
    """Both toml orders, built and run once for the class."""
    tmp = None
    roots = {}

    @classmethod
    def build(cls):
        if cls.tmp is not None:
            return
        cls.tmp = tempfile.mkdtemp(prefix="tj_g6a_order_")
        for key, order in (("zeta_first", ["zeta", "alpha"]),
                           ("alpha_first", ["alpha", "zeta"])):
            root = _project(Path(cls.tmp) / key, order, _ORDER_BOOKS)
            r = _cli(root, "run", "--no-input")
            assert r.returncode == 0, r.stderr[-2000:]
            cls.roots[key] = root

    @classmethod
    def cleanup(cls):
        if cls.tmp:
            shutil.rmtree(cls.tmp, ignore_errors=True)
        cls.tmp, cls.roots = None, {}


def tearDownModule():
    _OrderProjects.cleanup()


@rule("CA-DATE-14")
class TestAccountOrderFollowsToml(unittest.TestCase):
    """Rows of different accounts at one moment follow the accounts'
    order in taxjson.toml — in the run's blend (A2-0937) and in every
    tool that recomputes the blended book: check-filed / the run's
    filed-year drift check (A2-0512), t1135 (A2-1592), and its twins
    carryover, audit and wash-sales --explain."""

    @classmethod
    def setUpClass(cls):
        _OrderProjects.build()
        cls.roots = _OrderProjects.roots

    def _summary(self, key):
        doc = json.loads((self.roots[key] / "work" /
                          "zeta_gains_wash.json").read_text())
        return doc["summary"]

    def test_run_blend_follows_toml_order(self):
        """A2-0937: stage_blended_wash_pass merges in toml order
        (taxjson_run.py, `for n in names` in the taxjson-merge call)."""
        s = self._summary("zeta_first")
        self.assertAlmostEqual(s["total_gain"], 200.0, 2)
        self.assertAlmostEqual(s["total_disallowed"], 0.0, 2)
        s = self._summary("alpha_first")
        self.assertAlmostEqual(s["total_gain"], 0.0, 2)
        self.assertAlmostEqual(s["total_disallowed"], 300.0, 2)

    def test_t1135_cost_matches_the_run(self):
        """A2-1592: cmd_t1135 fed the bases in sorted() order."""
        for key, cost in (("zeta_first", 2000.0), ("alpha_first", 1800.0)):
            r = _cli(self.roots[key], "t1135", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (p,) = [x for x in json.loads(r.stdout)["properties"]
                    if x["symbol"] == "XYZ.US"]
            self.assertAlmostEqual(p["year_end_cost"], cost, 2, key)

    def test_carryover_net_gain_matches_the_run(self):
        """A2-1592 twin: cmd_carryover fed the bases in sorted() order."""
        for key, gain in (("zeta_first", 200.0), ("alpha_first", 0.0)):
            r = _cli(self.roots[key], "carryover", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (row,) = [x for x in json.loads(r.stdout)["rows"]
                      if x["year"] == 2025]
            self.assertAlmostEqual(row["net_gain"], gain, 2, key)

    def test_audit_ties_out_in_either_order(self):
        """A2-1592 twin: cmd_audit merged the equity books sorted()."""
        for key in ("zeta_first", "alpha_first"):
            r = _cli(self.roots[key], "audit")
            self.assertEqual(r.returncode, 0,
                             key + "\n" + (r.stdout + r.stderr)[-2500:])
            self.assertIn("1 tied, 0 MISMATCHED", r.stdout + r.stderr)

    def test_wash_sales_explain_traces_the_run_book(self):
        """A2-1592 twin: _explain_wash_sales merged sorted(_by_name)."""
        r = _cli(self.roots["zeta_first"], "wash-sales", "--explain")
        self.assertNotIn("LOSS SALE", r.stdout + r.stderr)
        r = _cli(self.roots["alpha_first"], "wash-sales", "--explain")
        self.assertIn("LOSS SALE", r.stdout + r.stderr)

    def test_check_filed_ok_right_after_close_year(self):
        """A2-0512: the drift check recomputed in the lock's alphabetical
        key order and reported a false DRIFT right after close-year."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            shutil.copytree(self.roots["zeta_first"], root)
            r = _cli(root, "close-year")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, "check-filed")
            self.assertEqual(r.returncode, 0,
                             (r.stdout + r.stderr)[-2500:])
            self.assertNotIn("DRIFT", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
