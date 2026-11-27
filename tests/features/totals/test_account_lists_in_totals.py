"""The totals by type take an ACCOUNT list (`tjs divs-sum alpha,beta`,
lib/account_list): a list's totals add the accounts it names and no
other, the same whatever their order, each a single-account run's
figures (a ticker of two listed accounts adds up as one row); a
total line names the listed accounts (winners, losers, and the
"(all accounts)" of divs-sum, dil-sum and roc-sum); fees-sum reads each
file once; a listed account with no books is refused, never left out,
before any note or full-history file; one name is as it always was, and
an empty ACCOUNT reads as none, as before lists.

Synthetic .tt books and the Coinbase demo export only (made-up tickers)."""
import os
import tempfile
import unittest
from pathlib import Path

from _support.view_account_lists import (Views, added, canon, crypto_project, rows_added,
                                         tt_project)


# (command, the rows' key, the keys of its figures) on the .tt project.
_SUMS = (
    ("divs-sum", "rows", ("totals", "totals_taxable", "totals_slips", "totals_sheltered")),
    ("dil-sum", "rows", ("totals", "totals_dividend", "totals_ordinary")),
    ("trades-sum", "rows", ("totals", "sold_taxable")),
    ("roc-sum", "rows", ("totals", "totals_taxable", "totals_sheltered")),
    ("ccd-sum", "rows", ("total_gain", "taxable_gain", "sheltered_gain")),
    ("leaps-sum", "rows", ("total_gain", "taxable_gain", "sheltered_gain")),
    ("winners", "rows", ("side_total_gain",)),
)

# The row field naming ZZQ.TO, the ticker alpha and beta both hold.
_SHARED = {"divs-sum": "symbol", "trades-sum": "symbol", "roc-sum": "symbol",
           "ccd-sum": "underlying", "winners": "ticker"}

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


class TestAListAddsTheAccountsItNames(_Project):
    def test_each_total_is_the_sum_of_the_accounts(self):
        for cmd, rows, figures in _SUMS:
            with self.subTest(cmd=cmd):
                both = self.tt.doc(cmd, "alpha,beta")
                one, other = self.tt.doc(cmd, "alpha"), self.tt.doc(cmd, "beta")
                self.assertTrue(one[rows] and other[rows], cmd)
                # each row's figures add, a ticker of both accounts as one row
                self.assertFiguresEqual(rows_added(both[rows]),
                                        rows_added(one[rows], other[rows]), cmd)
                for key in figures:
                    self.assertFiguresEqual(added({key: both[key]}),
                                            added({key: one[key]}, {key: other[key]}),
                                            f"{cmd} {key}")

    def test_a_ticker_of_both_accounts_is_one_row(self):
        for cmd, field in sorted(_SHARED.items()):
            with self.subTest(cmd=cmd):
                for argv in ("alpha", "beta"):
                    self.assertIn("ZZQ.TO", [r[field] for r in self.tt.doc(cmd, argv)["rows"]],
                                  argv)
                self.assertEqual([r[field] for r in self.tt.doc(cmd, "alpha,beta")["rows"]
                                  ].count("ZZQ.TO"), 1)

    def test_the_order_of_the_names_changes_no_figure(self):
        for cmd, rows, figures in _SUMS:
            with self.subTest(cmd=cmd):
                both, flipped = self.tt.doc(cmd, "alpha,beta"), self.tt.doc(cmd, "beta,alpha")
                self.assertEqual(canon(flipped[rows]), canon(both[rows]))
                for key in figures:
                    self.assertEqual(flipped[key], both[key], key)

    def test_an_account_left_out_of_the_list_is_left_out_of_the_totals(self):
        every = self.tt.doc("divs-sum")
        both = self.tt.doc("divs-sum", "alpha,beta")
        self.assertAlmostEqual(every["totals_taxable"]["CAD"] - both["totals_taxable"]["CAD"],
                               self.tt.doc("divs-sum", "alpha_us")["totals_taxable"]["CAD"],
                               places=6)

    def test_staking_sum_of_two_crypto_accounts(self):
        both = self.coins.doc("staking-sum", "coins,wallet")
        one, other = (self.coins.doc("staking-sum", "coins"),
                      self.coins.doc("staking-sum", "wallet"))
        income = [sum(g["income_amount"]["total"] for g in d["groups"]) for d in (both, one, other)]
        count = [sum(g["reward_count"] for g in d["groups"]) for d in (both, one, other)]
        self.assertAlmostEqual(income[0], income[1] + income[2], places=6)
        self.assertEqual(count[0], count[1] + count[2])
        self.assertEqual(self.coins.doc("staking-sum", "wallet,coins")["groups"], both["groups"])


class TestFeesSumReadsEachFileOnce(_Project):
    def test_an_account_and_its_prefixed_sibling(self):
        both = self.tt.doc("fees-sum", "alpha,alpha_us")
        one, other = self.tt.doc("fees-sum", "alpha"), self.tt.doc("fees-sum", "alpha_us")
        self.assertEqual(both["meta"]["files_read"],
                         one["meta"]["files_read"] + other["meta"]["files_read"])
        self.assertEqual(both["meta"]["fee_rows"], one["meta"]["fee_rows"] + other["meta"]["fee_rows"])
        self.assertAlmostEqual(both["total"]["total"],
                               one["total"]["total"] + other["total"]["total"], places=6)
        self.assertEqual(both["total"]["trades"], one["total"]["trades"] + other["total"]["trades"])
        self.assertEqual(sorted(both["brokerages"]),
                         sorted(list(one["brokerages"]) + list(other["brokerages"])))
        flipped = self.tt.doc("fees-sum", "alpha_us,alpha")
        self.assertEqual(flipped["total"], both["total"])

    def test_one_name_still_leaves_its_sibling_out(self):
        one = self.tt.doc("fees-sum", "alpha")
        self.assertEqual(list(one["brokerages"]), ["manual (.tt)/alpha"])


class TestWinnersAndLosersNameTheList(_Project):
    def test_the_title_and_the_totals_name_each_account(self):
        code, out, err = self.tt.run("winners", "alpha,beta")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("accounts alpha,beta, top 10", out.splitlines()[0])
        totals = [ln for ln in out.splitlines() if "TOTAL" in ln]
        self.assertEqual(len(totals), 2, out)
        self.assertTrue(all("alpha,beta" in ln for ln in totals), totals)

    def test_one_name_as_before(self):
        code, out, err = self.tt.run("winners", "alpha")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("account alpha, top 10", out.splitlines()[0])
        self.assertNotIn("accounts", out.splitlines()[0])

    def test_losers_with_none_names_the_list(self):
        code, out, err = self.tt.run("losers", "alpha,beta")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("No realized losers", out)
        self.assertIn("accounts alpha,beta", out)


class TestATotalLineNamesTheList(_Project):
    """A list's total line says which accounts it adds; one name and none
    keep "(all accounts)", the words they always had."""

    def test_the_sheltered_totals(self):
        for cmd, label in (("divs-sum", "TOTAL DIVIDEND"), ("dil-sum", "TOTAL DIVIDEND IN LIEU"),
                           ("roc-sum", "TOTAL CAPITAL RETURNED")):
            with self.subTest(cmd=cmd):
                for argv, words in ((("alpha,rrsp",), "(accounts alpha,rrsp)"),
                                    (("rrsp,alpha",), "(accounts rrsp,alpha)"),
                                    (("rrsp",), "(all accounts)"), ((), "(all accounts)")):
                    code, out, err = self.tt.run(cmd, *argv)
                    self.assertEqual((code, err), (0, ""), argv)
                    totals = [ln for ln in out.splitlines() if ln.startswith(label + " (")]
                    self.assertEqual(len(totals), 1, out)
                    self.assertTrue(totals[0].startswith(f"{label} {words}:"), totals)


class TestAnEmptyAccountReadsAsNone(_Project):
    """`tjs X all ""`: an empty ACCOUNT is every account, as before lists."""

    def test_each_total_as_with_no_account(self):
        for cmd in ("divs-sum", "dil-sum", "trades-sum", "roc-sum", "ccd-sum", "leaps-sum",
                    "winners", "losers", "fees-sum"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.tt.run(cmd, "all", ""), self.tt.run(cmd, "all"))
        self.assertEqual(self.coins.run("staking-sum", "all", ""),
                         self.coins.run("staking-sum", "all"))


class TestARefusedListBuildsNothing(_Project):
    """A listed account with no gains file is refused before the
    full-history note and before any full-history file is built (a
    window outside the tax year)."""

    def test_the_windowed_totals(self):
        work = self.tt.root / "work"
        for cmd in ("winners", "losers", "ccd-sum", "leaps-sum"):
            with self.subTest(cmd=cmd):
                before = _files_of(work)
                code, out, err = self.tt.run(cmd, "all", "alpha,idle")
                self.assertEqual((code, out), (1, ""))
                self.assertIn("for account 'idle'", err)
                self.assertNotIn("note:", err)
                self.assertEqual(_files_of(work), before)


def _files_of(folder):
    """Each file of `folder`: its name, size and change time (a file
    built or rebuilt shows)."""
    return sorted((e.name, e.stat().st_size, e.stat().st_mtime_ns)
                  for e in os.scandir(folder) if e.is_file())


class TestAListedAccountWithNoBooksIsRefused(_Project):
    def test_each_total_names_it(self):
        for cmd in ("divs-sum", "dil-sum", "trades-sum", "roc-sum", "ccd-sum", "leaps-sum",
                    "winners", "losers", "fees-sum", "staking-sum"):
            with self.subTest(cmd=cmd):
                code, out, err = self.tt.run(cmd, "alpha,idle")
                self.assertEqual(code, 1, err)
                self.assertEqual(out, "")
                self.assertIn("for account 'idle'", err)
                self.assertNotIn("'alpha'", err)

    def test_one_such_name_keeps_its_words_and_exit(self):
        code, out, err = self.tt.run("ccd-sum", "idle")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no gains for account 'idle' in work/ — run `tjs run` first, "
                      "or check the name", err)
        code, out, err = self.tt.run("fees-sum", "idle")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no files for account 'idle' in work/", err)


if __name__ == "__main__":
    unittest.main()
