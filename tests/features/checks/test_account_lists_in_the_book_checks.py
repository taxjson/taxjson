"""A comma list of accounts (`margin,beta`) to the checks of the books:
`tjs edge-cases`, `check-dates`, `splits` and `spinoffs` read the listed
accounts' books, in any order of the names, what the single-account runs
read together; a listed account with no books is refused naming it
(exit 1), a single name as before. `tjs sanity` and `tjs check-tt` take
a comma list as one of their items, a bare name item only: a FILE item
(an existing file) is never split, an empty or repeated name is refused
as the item is today (exit 2).

Every fixture is SYNTHETIC (tests/_support/cli_account_list_books.py).
"""
import json
import shutil
import tempfile
import unittest

from _support.cli_account_list_books import HOLDINGS, account_list_books
from _support.cli_command_runs import _tjs


class _Books(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = account_list_books(cls._td.name)
        (cls.root / "h.toml").write_text(HOLDINGS, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def tjs(self, *args, code=0):
        r = _tjs(self.root, *args)
        self.assertEqual(r.returncode, code,
                         f"{args}: {r.stdout[-1500:]}{r.stderr[-1500:]}")
        return r

    def doc(self, *args, code=0):
        return json.loads(self.tjs(*args, "--json", code=code).stdout)

    def refused_for_idle(self, *args, what="books"):
        r = self.tjs(*args, code=1)
        self.assertIn(f"no {what} for account 'idle' in work/", r.stderr)
        self.assertEqual(r.stdout, "")


def _edge_rows(doc):
    rows = [(part, r.get("account"), r.get("symbol"), r.get("date"))
            for part, v in doc["year_boundary"].items() for r in v]
    for part in ("window_edges", "calls_in_windows", "renamed_late"):
        rows += [(part, r.get("account"), r.get("symbol"), r.get("date"))
                 for r in doc[part]]
    return sorted(rows, key=repr)


class TestEdgeCases(_Books):
    def test_a_list_is_its_accounts_together_in_any_order(self):
        both = _edge_rows(self.doc("edge-cases", "margin,beta"))
        margin = _edge_rows(self.doc("edge-cases", "margin"))
        beta = _edge_rows(self.doc("edge-cases", "beta"))
        self.assertEqual({r[1] for r in both}, {"margin", "beta"})
        self.assertEqual(both, sorted(margin + beta, key=repr))
        self.assertEqual(_edge_rows(self.doc("edge-cases", "beta,margin")),
                         both)

    def test_a_listed_account_with_no_books_is_refused(self):
        self.refused_for_idle("edge-cases", "margin,idle")


class TestCheckDates(_Books):
    def test_a_list_checks_its_accounts_sources(self):
        both = self.doc("check-dates", "margin,beta")
        margin = self.doc("check-dates", "margin")
        beta = self.doc("check-dates", "beta")
        self.assertEqual(both["sources"],
                         {**margin["sources"], **beta["sources"]})
        self.assertEqual(both["checked"], margin["checked"] + beta["checked"])
        self.assertEqual(self.doc("check-dates", "beta,margin"), both)

    def test_a_listed_account_with_no_books_is_refused(self):
        self.refused_for_idle("check-dates", "margin,idle",
                              what="parsed sources")
        # No listed account has one: each named, not "no parsed sources".
        r = self.tjs("check-dates", "idle,spare", code=1)
        self.assertIn("no parsed sources for accounts 'idle', 'spare' in "
                      "work/", r.stderr)
        # One name: today's message.
        r = self.tjs("check-dates", "idle", code=1)
        self.assertIn("no parsed sources in work/", r.stderr)


class TestSplitsAndSpinoffs(_Books):
    def test_a_list_is_its_accounts_together_in_any_order(self):
        def rows(names):
            return sorted((s["account"], s["symbol"], s["date"], s["ratio"])
                          for s in self.doc("splits", names)["splits"])
        both = rows("margin,beta")
        self.assertEqual({r[0] for r in both}, {"margin", "beta"})
        self.assertEqual(both, sorted(rows("margin") + rows("beta")))
        self.assertEqual(rows("beta,margin"), both)
        self.assertEqual(self.doc("spinoffs", "margin,beta"),
                         self.doc("spinoffs", "beta,margin"))

    def test_a_listed_account_with_no_books_is_refused(self):
        self.refused_for_idle("splits", "beta,idle")
        self.refused_for_idle("spinoffs", "margin,idle")


class TestSanityItems(_Books):
    def test_a_comma_item_is_its_accounts(self):
        apart = self.doc("sanity", "margin", "beta", "h.toml", code=1)
        self.assertEqual(self.doc("sanity", "margin,beta", "h.toml", code=1),
                         apart)
        self.assertEqual(self.doc("sanity", "beta,margin", "h.toml", code=1),
                         apart)
        self.assertEqual(apart["groups"][0]["accounts"], ["beta", "margin"])

    def test_a_file_item_with_a_comma_is_a_file(self):
        shutil.copy(self.root / "h.toml", self.root / "h,2.toml")
        self.assertEqual(
            self.doc("sanity", "margin", "beta", "h,2.toml", code=1)
            ["discrepancies"],
            self.doc("sanity", "margin", "beta", "h.toml", code=1)
            ["discrepancies"])

    def test_an_account_and_a_file_run_together_is_no_list(self):
        # The words of before lists: no account part read as a name.
        r = self.tjs("sanity", "margin,h.toml", code=2)
        self.assertIn("'margin,h.toml' is neither an account", r.stderr)
        self.assertIn("nor an existing .toml file", r.stderr)

    def test_an_empty_or_unknown_name_in_the_item_is_refused(self):
        r = self.tjs("sanity", "margin,", "h.toml", code=2)
        self.assertIn("'margin,' has an empty account name", r.stderr)
        r = self.tjs("sanity", "margin,nope", "h.toml", code=2)
        self.assertIn("'nope' is not an account", r.stderr)
        r = self.tjs("sanity", "margin,idle", "h.toml", code=2)
        self.assertIn("account 'idle' has no inputs", r.stderr)


class TestCheckTtItems(_Books):
    def test_a_comma_item_is_its_accounts(self):
        apart = self.tjs("check-tt", "margin", "beta")
        self.assertIn("inputs/margin/trades.tt", apart.stdout)
        self.assertIn("inputs/beta/trades.tt", apart.stdout)
        self.assertEqual(self.tjs("check-tt", "margin,beta").stdout,
                         apart.stdout)
        self.assertEqual(self.tjs("check-tt", "beta,margin").stdout,
                         apart.stdout)

    def test_a_file_item_with_a_comma_is_a_file(self):
        f = self.root / "x,y.tt"
        f.write_text("BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 10 -10 0\n",
                     encoding="utf-8")
        r = self.tjs("check-tt", str(f))
        self.assertIn("x,y.tt", r.stdout)
        self.assertIn("Every line parses.", r.stdout)

    def test_an_empty_repeated_or_unknown_name_is_refused(self):
        r = self.tjs("check-tt", "margin,,beta", code=2)
        self.assertIn("'margin,,beta' has an empty account name", r.stderr)
        r = self.tjs("check-tt", "margin,margin", code=2)
        self.assertIn("names margin more than once", r.stderr)
        r = self.tjs("check-tt", "margin,nope", code=2)
        self.assertIn("no account 'nope' in taxjson.toml", r.stderr)

    def test_its_account_flag_stays_one_account(self):
        r = self.tjs("check-tt", "--account", "margin,beta",
                     "inputs/margin/trades.tt", code=1)
        self.assertIn("takes one account", r.stderr)


if __name__ == "__main__":
    unittest.main()
