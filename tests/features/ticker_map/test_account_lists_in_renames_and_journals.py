"""A comma list of accounts (`margin,beta`) to `tjs renames` and to
`tjs journals --account`: the listed accounts' renames and journals, in
any order of the names, what the single-account runs show together;
`journals --json` keeps "account" (the text given, null for every
account) and adds "account_names" (the names in the order given, null
for every account); a listed account with no books refused naming it
(exit 1), a single name as before. The libraries read None as every
account and an empty list as none.

Every fixture is SYNTHETIC (tests/_support/cli_account_list_books.py).
"""
import json
import tempfile
import tomllib
import unittest

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

    def doc(self, *args, code=0):
        return json.loads(self.tjs(*args, "--json", code=code).stdout)

    def cfg(self):
        return tomllib.loads((self.root / "taxjson.toml").read_text())


class TestRenames(_Books):
    def renames(self, *names, code=0):
        doc = self.doc("renames", *names, code=code)
        return (sorted((r["date"], r["old"], r["new"], tuple(r["accounts"]))
                       for r in doc["renames"]),
                sorted((x["account"], x["symbol"]) for x in doc["late"]))

    def test_a_list_is_its_accounts_together_in_any_order(self):
        # beta's YYR.TO trade after its rename is unresolved: exit 1.
        both = self.renames("margin,beta", code=1)
        margin = self.renames("margin")
        beta = self.renames("beta", code=1)
        self.assertEqual({a for r in both[0] for a in r[3]},
                         {"margin", "beta"})
        self.assertEqual(both, (sorted(margin[0] + beta[0]),
                                sorted(margin[1] + beta[1])))
        self.assertEqual(self.renames("beta,margin", code=1), both)
        self.assertEqual(self.renames(code=1), both)

    def test_a_listed_account_with_no_books_is_refused(self):
        r = self.tjs("renames", "margin,idle", code=1)
        self.assertIn("no books for account 'idle' in work/", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_the_library_reads_none_as_every_account(self):
        from taxjson.lib import renames
        cfg = self.cfg()
        self.assertEqual(len(renames.report(self.root, cfg, None)["renames"]),
                         2)
        self.assertEqual(
            [r["old"] for r in renames.report(self.root, cfg,
                                              ("beta",))["renames"]],
            ["YYR.TO"])

    def test_the_library_reads_an_empty_list_as_no_account(self):
        from taxjson.lib import renames
        self.assertEqual(renames.report(self.root, self.cfg(), ())["renames"],
                         [])


class TestJournals(_Books):
    def journals(self, *flags):
        doc = self.doc("journals", *flags)
        return doc, sorted((j["account"], j["from"], j["to"])
                           for j in doc["journals"])

    def test_a_list_is_its_accounts_together_in_any_order(self):
        doc, both = self.journals("--account", "margin,beta")
        _d, margin = self.journals("--account", "margin")
        _d, beta = self.journals("--account", "beta")
        self.assertEqual({j[0] for j in both}, {"margin", "beta"})
        self.assertEqual(both, sorted(margin + beta))
        self.assertEqual(self.journals("--account", "beta,margin")[1], both)
        self.assertEqual(self.journals()[1], both)

    def test_the_json_names_the_list(self):
        doc, _j = self.journals("--account", "beta,margin")
        self.assertEqual((doc["account"], doc["account_names"]),
                         ("beta,margin", ["beta", "margin"]))
        doc, _j = self.journals("--account", "margin")
        self.assertEqual((doc["account"], doc["account_names"]),
                         ("margin", ["margin"]))
        doc, _j = self.journals()
        self.assertEqual((doc["account"], doc["account_names"]), (None, None))

    def test_the_text_header_names_the_list(self):
        out = self.tjs("journals", "--account", "margin,beta").stdout
        self.assertIn("accounts margin, beta:", out.splitlines()[0])
        out = self.tjs("journals", "--account", "margin").stdout
        self.assertIn("account margin:", out.splitlines()[0])

    def test_a_listed_account_with_no_books_is_refused(self):
        r = self.tjs("journals", "--account", "margin,idle", code=1)
        self.assertIn("no books for account 'idle' in work/", r.stderr)
        # One name: as before (no journals, exit 0), and an unknown one
        # a usage error.
        self.tjs("journals", "--account", "idle")
        r = self.tjs("journals", "--account", "nope", code=2)
        self.assertIn("no [accounts.nope] in taxjson.toml", r.stderr)
        # An unknown name in a list: a list's refusal, exit 1.
        r = self.tjs("journals", "--account", "margin,nope", code=1)
        self.assertIn("no [accounts.nope] in taxjson.toml", r.stderr)

    def test_the_library_reads_none_as_every_account(self):
        from taxjson.lib import journals
        doc = journals.report(self.root, self.cfg())
        self.assertEqual(len(doc["journals"]), 2)
        self.assertEqual((doc["account"], doc["account_names"]), (None, None))

    def test_the_library_reads_an_empty_list_as_no_account(self):
        from taxjson.lib import journals
        none = journals.report(self.root, self.cfg(), ())
        self.assertEqual((none["journals"], none["account_names"]), ([], []))


if __name__ == "__main__":
    unittest.main()
