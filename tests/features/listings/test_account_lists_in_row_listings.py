"""The row listings take an ACCOUNT list (`tjs divs alpha,beta`,
lib/account_list): a list shows the rows of the accounts it names, the
same rows whatever their order, each a single-account run's; a listed
account with no books, or one a listing reads nothing for (a sheltered
account in `tjs cash`, a crypto one in `tjs gains`), is refused, never
left out, every bad name in one refusal; one name is as it always was,
and an empty ACCOUNT (`tjs cash all ""`) reads as none, as before lists.

Synthetic .tt books and the Coinbase demo export only (made-up tickers)."""
import os
import tempfile
import unittest
from pathlib import Path

from _support.view_account_lists import Views, added, canon, crypto_project, tt_project

# (command, the rows' key, the keys of its totals) on the .tt project.
_UNIONS = (
    ("events", "rows", ("totals",)),
    ("divs", "rows", ("totals",)),
    ("dil", "rows", ("totals",)),
    ("trades", "rows", ("totals",)),
    ("roc", "rows", ("totals",)),
    ("gains", "rows", ("totals",)),
    ("fees", "rows", ("totals",)),
    ("leaps", "rows", ("total_gain", "taxable_gain", "sheltered_gain")),
    ("cash", "rows", ("totals",)),
)


_BUILT = {}


def setUpModule():
    """The two projects, built once for the module's tests."""
    _BUILT["tmp"] = tempfile.TemporaryDirectory()
    tmp = Path(_BUILT["tmp"].name)
    _BUILT["tt"] = Views(tt_project(tmp))
    _BUILT["coins"] = Views(crypto_project(tmp))


def tearDownModule():
    _BUILT.pop("tmp").cleanup()


class _Project(unittest.TestCase):
    @property
    def tt(self):
        return _BUILT["tt"]

    @property
    def coins(self):
        return _BUILT["coins"]

    def assertFiguresEqual(self, got, want, msg=None):
        self.assertEqual(sorted(got), sorted(want), msg)
        for k in want:
            self.assertAlmostEqual(got[k], want[k], places=6, msg=f"{msg}: {k}")

    def assertUnion(self, views, cmd, rows, totals, a, b):
        both = views.doc(cmd, f"{a},{b}")
        one, other = views.doc(cmd, a), views.doc(cmd, b)
        self.assertTrue(one[rows] and other[rows], (cmd, "each account has rows"))
        self.assertEqual(canon(both[rows]), canon(one[rows] + other[rows]), cmd)
        for key in totals:
            self.assertFiguresEqual(added({key: both[key]}),
                                    added({key: one[key]}, {key: other[key]}), f"{cmd} {key}")
        flipped = views.doc(cmd, f"{b},{a}")
        self.assertEqual(canon(flipped[rows]), canon(both[rows]), cmd)
        for key in totals:
            self.assertEqual(flipped[key], both[key], (cmd, key))


class TestAListIsTheUnionOfItsAccounts(_Project):
    def test_each_listing_shows_the_rows_of_the_accounts_named(self):
        for cmd, rows, totals in _UNIONS:
            with self.subTest(cmd=cmd):
                self.assertUnion(self.tt, cmd, rows, totals, "alpha", "beta")

    def test_a_ticker_of_both_accounts_shows_each_accounts_rows(self):
        both = self.tt.doc("divs", "alpha,beta")["rows"]
        self.assertEqual(sorted(r["account"] for r in both
                                if r["symbol"] == "ZZQ.TO" and r["action"] == "DIVIDEND"),
                         ["alpha", "beta"])

    def test_transfers_of_two_accounts(self):
        self.assertUnion(self.tt, "transfers", "transfers", ("count",), "rrsp", "tfsa")

    def test_staking_of_two_crypto_accounts(self):
        both = self.coins.doc("staking", "coins,wallet")
        one, other = self.coins.doc("staking", "coins"), self.coins.doc("staking", "wallet")
        self.assertEqual(canon(both["rewards"]), canon(one["rewards"] + other["rewards"]))
        income = [sum(g["income_amount"]["total"] for g in d["groups"]) for d in (both, one, other)]
        self.assertAlmostEqual(income[0], income[1] + income[2], places=6)
        self.assertGreater(income[2], income[1])  # wallet's first reward is doubled
        flipped = self.coins.doc("staking", "wallet,coins")
        self.assertEqual(canon(flipped["rewards"]), canon(both["rewards"]))

    def test_a_list_prefixes_each_line_with_its_account_one_name_does_not(self):
        _, both, _ = self.tt.run("divs", "alpha,beta")
        _, one, _ = self.tt.run("divs", "alpha")
        rows = [ln for ln in both.splitlines() if ln and not ln.startswith("TOTAL ")]
        self.assertEqual(len(rows), 5, both)          # 3 dividends, 2 payments in lieu
        self.assertTrue(all(ln.split()[0] in ("alpha", "beta") for ln in rows), both)
        self.assertTrue(one.startswith("DIVIDEND "), one)


class TestCashEchoesItsAccounts(_Project):
    def test_the_json_names_the_list_in_its_order(self):
        for argv, account, names in ((("alpha,beta",), "alpha,beta", ["alpha", "beta"]),
                                     (("beta,alpha",), "beta,alpha", ["beta", "alpha"]),
                                     (("alpha",), "alpha", ["alpha"]),
                                     ((), None, None),
                                     # an empty ACCOUNT: the text given, as before
                                     (("all", ""), "", None),
                                     (("",), None, None)):
            with self.subTest(argv=argv):
                d = self.tt.doc("cash", *argv)
                self.assertEqual((d["account"], d["account_names"]), (account, names))

    def test_a_listed_account_with_no_books_is_refused(self):
        code, out, err = self.tt.run("cash", "alpha,idle")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no books for account 'idle'", err)

    def test_a_sheltered_account_in_the_list_is_refused(self):
        code, out, err = self.tt.run("cash", "alpha,rrsp")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("account 'rrsp' is not a taxable account", err)
        code, out, err = self.tt.run("cash", "tfsa,alpha,rrsp")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("accounts 'tfsa', 'rrsp' are not taxable accounts", err)

    def test_one_sheltered_name_is_refused_as_before(self):
        code, out, err = self.tt.run("cash", "rrsp")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("account 'rrsp' is not a taxable account", err)


class TestAListedAccountWithNoBooksIsRefused(_Project):
    def test_each_listing_names_it(self):
        for cmd in ("events", "divs", "dil", "trades", "roc", "gains", "leaps", "transfers",
                    "staking", "fees"):
            with self.subTest(cmd=cmd):
                code, out, err = self.tt.run(cmd, "alpha,idle")
                self.assertEqual(code, 1, err)
                self.assertEqual(out, "")
                self.assertIn("for account 'idle'", err)
                self.assertNotIn("'alpha'", err)

    def test_one_such_name_keeps_its_words_and_exit(self):
        code, out, err = self.tt.run("divs", "idle")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no native transaction file for account 'idle' in work/", err)
        self.assertIn("check the name\n", err)
        code, out, err = self.tt.run("transfers", "idle")       # an empty state
        self.assertEqual((code, err), (0, ""))
        self.assertIn("No transfer rows for account 'idle' in the books.", out)

    def test_transfers_names_a_list_with_no_rows(self):
        code, out, err = self.tt.run("transfers", "alpha,beta")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("No transfer rows for accounts 'alpha', 'beta' in the books.", out)


class TestGainsRefusesACryptoAccountInTheList(_Project):
    def test_a_crypto_account_is_named(self):
        code, out, err = self.coins.run("gains", "margin,coins")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no native gains for account 'coins'", err)

    def test_one_crypto_name_as_before(self):
        code, out, err = self.coins.run("gains", "coins")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no native gains for account 'coins'", err)

    def test_every_bad_name_in_one_refusal(self):
        code, out, err = self.coins.run("gains", "coins,spare,margin")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no native gains for accounts 'coins', 'spare'", err)
        self.assertIn("'coins': books, no native gains.", err)
        self.assertIn("'spare': no books in work/", err)

    def test_one_name_with_no_books_as_before(self):
        code, out, err = self.coins.run("gains", "spare")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no native gains for account 'spare' in work/\n", err)

    def test_the_equity_account_alone_lists_its_gains(self):
        d = self.coins.doc("gains", "margin")
        self.assertEqual({r["account"] for r in d["rows"]}, {"margin"})


class TestAnEmptyAccountReadsAsNone(_Project):
    """`tjs X all ""`: an empty ACCOUNT is every account, as before lists
    (the shell passes "" from an empty variable)."""

    def test_each_listing_as_with_no_account(self):
        for argv in (("events", "all"), ("divs", "all"), ("gains", "all"), ("fees", "all"),
                     ("leaps", "all"), ("cash", "all"), ("trades", "2025"), ("transfers",)):
            with self.subTest(argv=argv):
                self.assertEqual(self.tt.run(*argv, ""), self.tt.run(*argv))
        self.assertEqual(self.coins.run("staking", "all", ""), self.coins.run("staking", "all"))


class TestARefusedListBuildsNothing(_Project):
    """A listed account with no gains file is refused before the
    full-history note and before any full-history file is built (a
    window outside the tax year)."""

    def test_the_windowed_listing(self):
        work = self.tt.root / "work"
        before = _files_of(work)
        code, out, err = self.tt.run("leaps", "all", "alpha,idle")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("for account 'idle'", err)
        self.assertNotIn("note:", err)
        self.assertEqual(_files_of(work), before)


def _files_of(folder):
    """Each file of `folder`: its name, size and change time (a file
    built or rebuilt shows)."""
    return sorted((e.name, e.stat().st_size, e.stat().st_mtime_ns)
                  for e in os.scandir(folder) if e.is_file())


if __name__ == "__main__":
    unittest.main()
