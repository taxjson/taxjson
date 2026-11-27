"""A comma list of accounts (`margin,beta`) to `tjs wash-sales` (its
table and --explain) and to `tjs audit --account`: the listed accounts'
rows, in any order of the names, the rows the single-account runs give
together (and their totals added), a denial whose purchase is in the
other listed account included; a listed account with no books, an
unknown one, and a list that breaks audit's rule (a sheltered account),
refused naming the account, exit 1. wash-sales leaves a sheltered
account, and a US project's crypto account, out of a list in both modes
alike, an Info: line naming it (exit 0); a list of only such accounts
is what one of them gives. audit names a listed account no disposition
matched (Info:). A single name as before.

Every fixture is SYNTHETIC (tests/_support/cli_account_list_books.py).
"""
import json
import tempfile
import unittest
from pathlib import Path

from _support.cli_account_list_books import account_list_books
from _support.cli_command_runs import _tjs


class _Books(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = account_list_books(cls._td.name)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def tjs(self, *args, code=0):
        r = _tjs(self.root, *args)
        self.assertEqual(r.returncode, code,
                         f"{args}: {r.stdout[-1500:]}{r.stderr[-1500:]}")
        return r


class TestWashSalesTable(_Books):
    def rows(self, *names):
        doc = json.loads(self.tjs("wash-sales", *names, "--json").stdout)
        return (sorted((r["account"], r["symbol"], r["date"],
                        r["disallowed_amount"]) for r in doc["rows"]),
                doc["totals"])

    def test_a_list_is_its_accounts_together_in_any_order(self):
        both, total = self.rows("margin,beta")
        margin, t_m = self.rows("margin")
        beta, t_b = self.rows("beta")
        self.assertEqual({r[0] for r in both}, {"margin", "beta"})
        self.assertEqual(both, sorted(margin + beta))
        for key in ("denied", "embedded_in_open", "permanently_denied"):
            self.assertAlmostEqual(total[key], t_m[key] + t_b[key], places=6)
        self.assertEqual(self.rows("beta,margin"), (both, total))

    def test_one_name_is_that_account_of_the_whole(self):
        every, _t = self.rows()
        self.assertEqual(self.rows("margin")[0],
                         [r for r in every if r[0] == "margin"])

    def test_a_denial_by_the_other_listed_accounts_purchase(self):
        # margin's XXC.TO loss, denied by beta's purchase: margin's row,
        # in the list as alone; never beta's.
        def xxc(*names):
            doc = json.loads(self.tjs("wash-sales", *names, "--json").stdout)
            return [(r["account"], r["wash_trigger"]["trigger_account"],
                     r["disallowed_amount"])
                    for r in doc["rows"] if r["symbol"] == "XXC.TO"]
        self.assertEqual(xxc("margin,beta"), [("margin", "beta", 100.0)])
        self.assertEqual(xxc("beta,margin"), xxc("margin"))
        self.assertEqual(xxc("beta"), [])

    def test_a_sheltered_account_in_a_list_is_left_out_and_said(self):
        r = self.tjs("wash-sales", "margin,rrsp", "--json")
        self.assertEqual(json.loads(r.stdout),
                         json.loads(self.tjs("wash-sales", "margin",
                                             "--json").stdout))
        self.assertIn("note: rrsp left out: superficial losses don't apply "
                      "in a sheltered account.", r.stderr)
        # Only sheltered accounts: what one of them gives.
        r = self.tjs("wash-sales", "rrsp,tfsa")
        self.assertIn("No superficial losses for accounts 'rrsp', 'tfsa' — "
                      "no losses were denied.", r.stdout)
        self.assertNotIn("left out", r.stderr)

    def test_a_listed_account_with_no_books_or_unknown_is_refused(self):
        r = self.tjs("wash-sales", "margin,idle", code=1)
        self.assertIn("no gains file for account 'idle' in work/", r.stderr)
        self.assertEqual(r.stdout, "")
        r = self.tjs("wash-sales", "margin,nope", code=1)
        self.assertIn("no [accounts.nope] in taxjson.toml", r.stderr)
        # One name: today's message and code.
        r = self.tjs("wash-sales", "idle", code=2)
        self.assertIn("no gains for account 'idle' in work/ — run `tjs run`"
                      " first, or check the name.", r.stderr)


class TestWashSalesExplain(_Books):
    def test_a_list_traces_its_accounts_in_any_order(self):
        both = self.tjs("wash-sales", "margin,beta", "--explain").stdout
        self.assertIn("ZZQ.TO — 2025-03-03 — margin", both)
        self.assertIn("YYR.TO — 2025-05-06 — beta", both)
        self.assertEqual(self.tjs("wash-sales", "beta,margin",
                                  "--explain").stdout, both)
        one = self.tjs("wash-sales", "margin", "--explain").stdout
        self.assertIn("ZZQ.TO — 2025-03-03 — margin", one)
        self.assertNotIn("— beta", one)

    def test_a_denial_by_the_other_listed_accounts_purchase(self):
        both = self.tjs("wash-sales", "margin,beta", "--explain").stdout
        block = both[both.index("XXC.TO — 2025-05-05 — margin"):
                     both.index("YYR.TO — 2025-05-06 — beta")]
        self.assertIn("- triggered by 2025-05-12 BUY +10.0000 @ 41.0000 "
                      "account=beta", block)
        self.assertEqual(self.tjs("wash-sales", "beta,margin",
                                  "--explain").stdout, both)

    def test_a_sheltered_account_in_a_list_is_left_out_as_the_table_does(self):
        r = self.tjs("wash-sales", "margin,rrsp", "--explain")
        self.assertEqual(r.stdout,
                         self.tjs("wash-sales", "margin", "--explain").stdout)
        self.assertIn("note: rrsp left out: superficial losses don't apply "
                      "in a sheltered account.", r.stderr)

    def test_a_list_of_sheltered_accounts_has_nothing_to_trace(self):
        r = self.tjs("wash-sales", "rrsp,tfsa", "--explain")
        self.assertIn("No superficial losses for accounts 'rrsp', 'tfsa'",
                      r.stdout)
        # One sheltered name: its own note, as before.
        r = self.tjs("wash-sales", "rrsp", "--explain")
        self.assertNotIn("accounts 'rrsp'", r.stdout)

    def test_a_listed_account_with_no_base_book_is_refused(self):
        r = self.tjs("wash-sales", "margin,idle", "--explain", code=1)
        self.assertIn("no base book for account 'idle' in work/", r.stderr)


class TestAuditAccountList(_Books):
    def events(self, *names):
        flags = [x for n in names for x in ("--account", n)]
        doc = json.loads(self.tjs("audit", *flags, "--json").stdout)
        self.assertFalse(doc["failed"])
        return (sorted((e["account"], e["symbol"], e["date"], e["gain"])
                       for e in doc["events"]), doc["total_gain"],
                doc["total_disallowed"])

    def test_a_list_audits_its_accounts_in_any_order(self):
        # An equity account and a crypto account: two computations, the
        # crypto one the blend the run made (coins1 with coins2).
        both = self.events("margin,coins1")
        margin, coins = self.events("margin"), self.events("coins1")
        self.assertEqual({e[0] for e in both[0]}, {"margin", "coins1"})
        self.assertEqual(both[0], sorted(margin[0] + coins[0]))
        self.assertAlmostEqual(both[1], margin[1] + coins[1], places=6)
        self.assertAlmostEqual(both[2], margin[2] + coins[2], places=6)
        self.assertEqual(self.events("coins1,margin"), both)
        self.assertEqual(self.events("beta,margin")[0],
                         sorted(self.events("beta")[0] + margin[0]))

    def test_a_listed_account_that_is_not_taxable_is_refused(self):
        r = self.tjs("audit", "--account", "margin,rrsp", "--summary",
                     code=1)
        self.assertIn("--account 'rrsp' is not a taxable account in "
                      "taxjson.toml (audit covers: ", r.stderr)
        r = self.tjs("audit", "--account", "rrsp,margin,tfsa", "--summary",
                     code=1)
        self.assertIn("--account 'rrsp', 'tfsa' are not taxable accounts",
                      r.stderr)
        # One name: today's words and code.
        r = self.tjs("audit", "--account", "rrsp", "--summary", code=1)
        self.assertIn("--account 'rrsp' is not a taxable account", r.stderr)

    def test_a_listed_account_with_no_disposition_is_said(self):
        r = self.tjs("audit", "--account", "margin,gamma", "--json")
        self.assertEqual(
            sorted(e["account"] for e in json.loads(r.stdout)["events"]),
            [e[0] for e in self.events("margin")[0]])
        self.assertIn("note: no disposition matches --account gamma in "
                      "2025: the other listed accounts' dispositions are "
                      "audited.", r.stderr)
        r = self.tjs("audit", "--account", "beta,margin", "--summary")
        self.assertNotIn("no disposition matches", r.stderr)

    def test_every_taxable_account_is_the_whole_audit(self):
        # idle and spare have no books: not listed (refused, below).
        listed = self.tjs("audit", "--account",
                          "margin,beta,gamma,coins1,coins2", "--json")
        self.assertEqual(listed.stdout, self.tjs("audit", "--json").stdout)

    def test_a_listed_account_with_no_books_is_refused(self):
        r = self.tjs("audit", "--account", "margin,idle", "--summary",
                     code=1)
        self.assertIn("no books for account 'idle' in work/", r.stderr)
        self.assertEqual(r.stdout, "")
        # One name: today's message (nothing matches the filter).
        r = self.tjs("audit", "--account", "idle", "--summary", code=1)
        self.assertIn("no disposition matches --account idle", r.stderr)


_US_TOML = """[settings]
year = 2025
country = "usa"
base_currency = "USD"
source_currencies = []
local_timezone = "America/New_York"

[accounts.margin]
type = "taxable"

[accounts.ira]
type = "sheltered"

[accounts.coins]
type = "taxable"
crypto = true
"""

_US_BOOKS = {
    "margin": ("BUYSELL 2025-01-06 10:00:00 ZZQ.US 10 USD 100 -1000 0\n"
               "BUYSELL 2025-03-03 10:00:00 ZZQ.US -10 USD 80 800 0\n"
               "BUYSELL 2025-03-10 10:00:00 ZZQ.US 10 USD 82 -820 0\n"),
    "ira": "BUYSELL 2025-02-10 10:00:00 XXS.US 5 USD 20 -100 0\n",
    "coins": ("BUYSELL 2025-01-06 10:00:00 SYN 1 USD 100 -100 0\n"
              "BUYSELL 2025-02-06 10:00:00 SYN -1 USD 80 80 0\n"),
}


class TestUsCryptoInAList(_Books):
    """A US project: §1091 does not reach a crypto account (US-WASH)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name) / "p"
        cls.root.mkdir()
        (cls.root / "taxjson.toml").write_text(_US_TOML, encoding="utf-8")
        for name, text in _US_BOOKS.items():
            (cls.root / "inputs" / name).mkdir(parents=True)
            (cls.root / "inputs" / name / "trades.tt").write_text(
                text, encoding="utf-8")
        r = _tjs(cls.root, "run", "--no-input")
        if r.returncode != 0:
            raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])

    def test_a_crypto_account_in_a_list_is_left_out_in_both_modes(self):
        for mode in ((), ("--explain",)):
            with self.subTest(mode=mode):
                r = self.tjs("wash-sales", "margin,coins", *mode)
                self.assertEqual(r.stdout, self.tjs("wash-sales", "margin",
                                                    *mode).stdout)
                self.assertIn("note: coins left out: wash sales don't apply "
                              "to a US project's crypto account (§1091).",
                              r.stderr)

    def test_a_list_the_rule_reaches_none_of_is_nothing_to_check(self):
        r = self.tjs("wash-sales", "coins,ira", "--explain")
        self.assertIn("nothing to check", r.stdout)
        self.assertEqual(self.tjs("wash-sales", "coins", "--explain").stdout,
                         "tjs wash-sales: coins is a crypto account of a US "
                         "project — the wash-sale rule (§1091) does not "
                         "apply to it, so there is nothing to check.\n")


if __name__ == "__main__":
    unittest.main()
