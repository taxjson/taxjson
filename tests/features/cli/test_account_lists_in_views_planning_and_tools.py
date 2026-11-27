"""A comma ACCOUNT list (`tjs stats margin,beta`) to the views, the
planning tools and the report tools that take one: list, stats,
win-rates, option-yield, div-estimate, slip-audit, wash-radar,
crypto-sends, find-missing-history and fetch.

For each: the list gives the union of the single-account runs (the
figures add where the command adds), `a,b` and `b,a` give the same
figures, a listed name that breaks the command's own rule (a sheltered
account to the radar, a non-crypto account to crypto-sends) is refused
naming it, a listed account with no books is refused naming it (exit
1), and a mode that acts on one account refuses a list. One name keeps
its output (the A/B against the release is in the round's notes). The
`--json` documents that echo the accounts add `account_names` (the
names as given, null for every account); stats and win-rates add
`sheltered_accounts`. All data is synthetic.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from _style import CapturedWidth
from _support.cb_send_projects import CB_HEADER, _proj
from _support.cli_command_runs import _run_project
from _support.crypto_sends import _cli

from taxjson.cli import main as MAIN
from taxjson.cli import plan as PLAN
from taxjson.lib import account_list as AL
from taxjson.lib import crypto_sends as CS
from taxjson.lib import fetchers as FP
from taxjson.lib.dispatch import python_module_argv


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# margin: a closed win (ZZQ.TO +50), ZZP.TO held, ZZU.TO sold with no
# purchase; beta: a closed loss (ZZR.TO -20), ZZS.TO held, ZZV.TO sold
# with no purchase; rrsp (sheltered): a closed win (ZZT.TO +10);
# margin.old (a name with a period): ZZW.TO held; idle: declared, no
# inputs (no books).
_MARGIN = ("BUYSELL 2025-01-06 10:00:00 ZZQ.TO 10 CAD 10 -100 0\n"
           "BUYSELL 2025-03-06 10:00:00 ZZQ.TO -10 CAD 15 150 0\n"
           "BUYSELL 2025-02-03 10:00:00 ZZP.TO 5 CAD 20 -100 0\n"
           "BUYSELL 2025-04-07 10:00:00 ZZU.TO -4 CAD 30 120 0\n"
           "DIVIDEND 2025-03-03 10:00:00 ZZP.TO 0 CAD 0 2.50 0\n")
_BETA = ("BUYSELL 2025-02-06 10:00:00 ZZR.TO 2 CAD 50 -100 0\n"
         "BUYSELL 2025-05-06 10:00:00 ZZR.TO -2 CAD 40 80 0\n"
         "DIVIDEND 2025-04-01 10:00:00 ZZR.TO 0 CAD 0 7.50 0\n"
         "BUYSELL 2025-02-10 10:00:00 ZZS.TO 3 CAD 20 -60 0\n"
         "BUYSELL 2025-06-02 10:00:00 ZZV.TO -3 CAD 10 30 0\n")
_RRSP = ("BUYSELL 2025-01-08 10:00:00 ZZT.TO 1 CAD 40 -40 0\n"
         "BUYSELL 2025-02-08 10:00:00 ZZT.TO -1 CAD 50 50 0\n")
_OLD = "BUYSELL 2025-01-10 10:00:00 ZZW.TO 2 CAD 10 -20 0\n"
_EXTRA = ('[accounts.beta]\ntype = "taxable"\n'
          '[accounts.rrsp]\ntype = "sheltered"\n'
          '[accounts.idle]\ntype = "taxable"\n'
          '[accounts."margin.old"]\ntype = "taxable"\n')


def _inproc(root, *argv):
    """`tjs -C ROOT ARGV` in this process: (exit code, stdout, stderr).
    Exit 1 carries its text in SystemExit (lib/out.fail), shown here as
    the interpreter would print it."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", ["tjs", "-C", str(root), *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            MAIN.main()
        except SystemExit as e:
            if isinstance(e.code, str):
                err.write(e.code + "\n")
                code = 1
            else:
                code = e.code or 0
    return code, out.getvalue(), err.getvalue()


class _Books(unittest.TestCase):
    """The built Canada project above, shared by the class."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="taxjson-acct-lists-"))
        cls.root = _run_project(cls._tmp, trades=_MARGIN, extra=_EXTRA,
                                more_inputs={"beta": _BETA, "rrsp": _RRSP,
                                             "margin.old": _OLD})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def ok(self, *argv):
        code, out, err = _inproc(self.root, *argv)
        self.assertEqual(code, 0, err)
        self.assertNotIn("Traceback", err)
        return out

    def json(self, *argv):
        return json.loads(self.ok(*argv, "--json"))

    def refused(self, argv, code, *texts):
        got, _out, err = _inproc(self.root, *argv)
        self.assertEqual(got, code, err)
        self.assertNotIn("Traceback", err)
        for t in texts:
            self.assertIn(t, err)
        return err


class TestPositions(_Books):
    """`tjs list margin,beta`: both accounts' open positions."""

    def _rows(self, *argv):
        return sorted((r["account"], r["symbol"], r["qty"], r["cost"])
                      for r in self.json("list", *argv)["rows"])

    def test_the_union_in_either_order(self):
        margin, beta = self._rows("margin"), self._rows("beta")
        self.assertEqual({a for a, *_ in margin}, {"margin"})
        self.assertEqual({a for a, *_ in beta}, {"beta"})
        self.assertEqual(self._rows("margin,beta"), sorted(margin + beta))
        self.assertEqual(self._rows("beta,margin"), sorted(margin + beta))
        self.assertEqual(self._rows("margin,beta", "2025-12-31"),
                         sorted(margin + beta))
        self.assertEqual(self._rows("2025-12-31", "beta,margin"),
                         sorted(margin + beta))

    def test_a_listed_account_with_no_books(self):
        self.refused(["list", "margin,idle"], 1,
                     "no gains file for account 'idle' in work/")
        self.refused(["list", "margin,idle", "--date", "2025-12-31"], 1,
                     "no gains file for account 'idle' in work/")

    def test_one_name_reads_as_before(self):
        self.refused(["list", "idle"], 2,
                     "no gains for account 'idle' in work/")


class TestClosedTradeViews(_Books):
    """stats and win-rates: the listed accounts' closed trades."""

    def test_stats_adds_the_accounts(self):
        one = {n: self.json("stats", n)["total"] for n in ("margin", "beta")}
        both = self.json("stats", "margin,beta")
        for key in ("trades", "wins", "losses", "net_pl", "gross_wins",
                    "gross_losses"):
            self.assertAlmostEqual(both["total"][key],
                                   one["margin"][key] + one["beta"][key])
        self.assertEqual(both["account_names"], ["margin", "beta"])
        self.assertEqual(both["sheltered_accounts"], [])
        self.assertFalse(both["sheltered"])
        swapped = self.json("stats", "beta,margin")
        self.assertEqual(swapped["total"], both["total"])
        self.assertEqual(swapped["account_names"], ["beta", "margin"])

    def test_stats_names_a_sheltered_account_in_the_list(self):
        doc = self.json("stats", "margin,rrsp")
        self.assertFalse(doc["sheltered"])           # not every one
        self.assertEqual(doc["sheltered_accounts"], ["rrsp"])
        self.assertEqual(doc["total"]["trades"],
                         self.json("stats", "margin")["total"]["trades"]
                         + self.json("stats", "rrsp")["total"]["trades"])
        head = self.ok("stats", "margin,rrsp").splitlines()[0]
        self.assertIn("accounts margin, rrsp — rrsp registered, not "
                      "taxable events", head)
        alone = self.json("stats", "rrsp")
        self.assertTrue(alone["sheltered"])
        self.assertEqual(alone["sheltered_accounts"], ["rrsp"])
        self.assertEqual(alone["account_names"], ["rrsp"])

    def test_every_taxable_account_is_null(self):
        doc = self.json("stats")
        self.assertIsNone(doc["account_names"])
        self.assertEqual(doc["sheltered_accounts"], [])

    def test_win_rates_a_comma_token_is_never_a_symbol(self):
        # Before lists `margin,beta` was read as a symbol: no closed trade
        # on it, exit 0 (a silent wrong result).
        both = self.json("win-rates", "margin,beta")
        self.assertEqual(both["symbols"], [])
        self.assertEqual(both["unmatched_symbols"], [])
        keys = sorted(r["key"] for r in both["rows"])
        single = sorted(r["key"] for n in ("margin", "beta")
                        for r in self.json("win-rates", n)["rows"])
        self.assertEqual(keys, single)
        self.assertEqual(self.json("win-rates", "beta,margin")["total"],
                         both["total"])
        self.assertEqual(both["account_names"], ["margin", "beta"])
        doc = self.json("win-rates", "ZZQ.TO", "margin,beta", "2025")
        self.assertEqual([r["key"] for r in doc["rows"]], ["ZZQ.TO"])

    def test_win_rates_one_account_argument(self):
        self.refused(["win-rates", "margin", "beta,rrsp"], 1,
                     "more than one account argument: 'margin', "
                     "'beta,rrsp'",
                     "'margin,beta,rrsp'")

    def test_a_listed_account_with_no_books(self):
        for cmd in ("stats", "win-rates"):
            with self.subTest(cmd=cmd):
                self.refused([cmd, "margin,idle"], 1,
                             "no gains file for account 'idle' in work/")


class TestPlanningViews(_Books):
    """option-yield and div-estimate."""

    def test_option_yield_reads_a_list_as_accounts(self):
        # Before lists: an underlying 'MARGIN,BETA' with no contract.
        doc = self.json("option-yield", "margin,beta")
        self.assertEqual(doc["unmatched_underlyings"], [])
        self.refused(["option-yield", "margin,idle"], 1,
                     "no native transaction file for account 'idle'")

    def test_div_estimate_the_union(self):
        def held(*argv):
            doc = self.json("div-estimate", *argv, "--date", "2025-07-01")
            return sorted((r["account"], r["symbol"])
                          for r in doc["omitted_no_history"])
        margin, beta = held("margin"), held("beta")
        self.assertTrue(margin and beta)
        self.assertEqual(held("margin,beta"), sorted(margin + beta))
        self.assertEqual(held("beta,margin"), sorted(margin + beta))
        self.assertEqual(held("6m", "beta,margin"), sorted(margin + beta))
        self.refused(["div-estimate", "margin,idle", "--date",
                      "2025-07-01"], 1,
                     "no native books for account 'idle' in work/")


class TestSlipAudit(_Books):
    """slip-audit: the listed accounts' income against the slips."""

    def _no_slip(self, *argv):
        code, out, err = _inproc(self.root, "slip-audit", *argv, "--json")
        self.assertEqual(code, 1, err)              # findings: no slips
        return sorted(r["account"]
                      for r in json.loads(out)["coverage"]["no_slip"])

    def test_the_union(self):
        self.assertEqual(self._no_slip("margin,beta"), ["beta", "margin"])
        self.assertEqual(self._no_slip("beta,margin"), ["beta", "margin"])
        self.assertEqual(self._no_slip("margin"), ["margin"])

    def test_a_listed_account_with_no_slips_or_books(self):
        self.refused(["slip-audit", "margin,rrsp"], 1,
                     "account 'rrsp' gets no T5 or T3",
                     "Leave it out of the list: 'margin'.")
        self.refused(["slip-audit", "margin,idle"], 1,
                     "no books for account 'idle' in work/")
        self.refused(["slip-audit", "rrsp"], 2)      # one name: as before

    def test_import_cra_takes_one_account(self):
        self.refused(["slip-audit", "margin,beta", "--import-cra",
                      str(self.root / "no-such-folder")], 1,
                     "`tjs slip-audit --import-cra` takes one account: "
                     "'margin,beta' names 2")


class TestWashRadar(_Books):
    """wash-radar: the rows of the listed accounts; every taxable book
    still decides each status."""

    def _rows(self, *argv):
        doc = self.json("wash-radar", *argv, "--date", "2025-06-03")
        rows = {(s["category"], r["ticker"]): r
                for s in doc["sections"] for r in s["rows"]}
        return doc, rows

    def test_the_union_in_either_order(self):
        _d, margin = self._rows("margin")
        _d, beta = self._rows("beta")
        doc, both = self._rows("margin,beta")
        self.assertTrue(margin and beta)
        self.assertEqual(both, {**margin, **beta})
        self.assertEqual(self._rows("beta,margin")[1], both)
        self.assertEqual(doc["account"], "margin,beta")
        self.assertEqual(doc["account_names"], ["margin", "beta"])
        self.assertTrue(doc["rows_scoped"])

    def test_the_names_key(self):
        one, _r = self._rows("margin")
        self.assertEqual((one["account"], one["account_names"]),
                         ("margin", ["margin"]))
        every, _r = self._rows()
        self.assertEqual((every["account"], every["account_names"],
                          every["rows_scoped"]), ("", None, False))

    def test_a_listed_sheltered_account_or_one_with_no_books(self):
        self.refused(["wash-radar", "margin,rrsp"], 1,
                     "rrsp is a sheltered account",
                     "Leave it out of the list: 'margin'.")
        self.refused(["wash-radar", "margin,idle"], 1,
                     "no books for account 'idle' in work/")
        self.refused(["wash-radar", "rrsp"], 2,       # one name: as before
                     "rrsp is a sheltered account")


class TestFindMissingHistory(_Books):
    """find-missing-history and its child's repeatable --account."""

    def test_the_union_in_either_order(self):
        both = self.ok("find-missing-history", "margin,beta")
        self.assertIn("accounts margin, beta", both.splitlines()[0])
        for sym, acct in (("ZZU.TO", "margin"), ("ZZV.TO", "beta")):
            self.assertRegex(both, rf"{sym}\s+{acct}\s")
        margin = self.ok("find-missing-history", "margin")
        self.assertIn("ZZU.TO", margin)
        self.assertNotIn("ZZV.TO", margin)
        swapped = self.ok("find-missing-history", "beta,margin")
        self.assertEqual(swapped.splitlines()[2:], both.splitlines()[2:])

    def test_refusals(self):
        self.refused(["find-missing-history", "margin,idle"], 1,
                     "no books for account 'idle' in work/")
        self.refused(["find-missing-history", "margin,beta",
                      "--write-purchases", "p.tt"], 1,
                     "`tjs find-missing-history --write-purchases FILE` "
                     "takes one account: 'margin,beta' names 2")

    def test_the_child_takes_several_accounts(self):
        books = [str(self.root / "work" / f"{n}_base.json")
                 for n in ("margin", "beta")]

        def child(*accts):
            argv = python_module_argv("taxjson.bin.taxjson_missing_history")
            for a in accts:
                argv += ["--account", a]
            return subprocess.run(argv + books, capture_output=True,
                                  text=True, stdin=subprocess.DEVNULL,
                                  env=dict(os.environ, TAXJSON_WIDTH="0"),
                                  timeout=300)
        r = child("margin", "beta")
        self.assertIn("ZZU.TO", r.stdout)
        self.assertIn("ZZV.TO", r.stdout)
        r = child("beta")
        self.assertNotIn("ZZU.TO", r.stdout)
        self.assertIn("ZZV.TO", r.stdout)
        r = child("margin", "nope")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("no row carries account 'nope'", r.stderr)
        r = child("nope")
        self.assertEqual(r.returncode, 2, r.stderr)   # one name: as before


class TestRefusalCodes(_Books):
    """Every refusal of a list exits 1; one name keeps its code, and a
    lone token starting with a digit reads as it did before lists."""

    def test_an_unknown_name_in_a_list(self):
        for argv in (["list", "margin,nope"], ["stats", "margin,nope"],
                     ["win-rates", "margin,nope"],
                     ["option-yield", "margin,nope"],
                     ["div-estimate", "margin,nope", "--date", "2025-07-01"],
                     ["slip-audit", "margin,nope"],
                     ["wash-radar", "margin,nope"],
                     ["find-missing-history", "margin,nope"],
                     ["list", "2025-12-31,margin"]):
            with self.subTest(argv=argv):
                self.refused(argv, 1, "in taxjson.toml")
        for argv, words in (
                (["list", "nope"], "no gains for account 'nope' in work/"),
                (["wash-radar", "nope"], "no [accounts.nope] in taxjson.toml"),
                (["div-estimate", "nope", "--date", "2025-07-01"],
                 "no [accounts.nope] in taxjson.toml")):
            with self.subTest(argv=argv):
                self.refused(argv, 2, words)            # one name: as before

    def test_a_lone_token_starting_with_a_digit_is_read_whole(self):
        self.refused(["div-estimate", "1y,margin", "--date", "2025-07-01"],
                     2, "no [accounts.1y,margin] in taxjson.toml",
                     "Check the name.")
        self.refused(["stats", "1y,margin", "--all-history"], 1,
                     "no books for account '1y,margin' in work/ — run "
                     "`tjs run` first")
        for cmd in ("stats", "win-rates", "option-yield"):
            with self.subTest(cmd=cmd):
                self.refused([cmd, "1y,margin"], 2,
                             "invalid time period '1y,margin'")

    def test_a_list_before_its_period(self):
        # Refused as before (exit 1), naming the period-first form.
        for argv, fix in (
                (["stats", "margin,beta", "2025"],
                 "The period goes first: `tjs stats 2025 margin,beta`."),
                (["div-estimate", "margin,beta", "6m"],
                 "The period goes first: `tjs div-estimate 6m "
                 "margin,beta`.")):
            with self.subTest(argv=argv):
                self.refused(argv, 1, "'margin,beta' is not an account "
                             "name", fix)
        err = self.refused(["stats", "./x", "2025"], 1,
                           "'./x' is not an account name")
        self.assertNotIn("goes first", err)              # no list: as before

    def test_write_missing_history_reads_each_name(self):
        # `margin.old` has a suffix, yet is an account: a list of two.
        out = self.ok("find-missing-history", "margin.old,margin",
                      "--write-missing-history")
        self.assertNotIn("takes no FILE", out)
        # A FILE in a list: exit 2, the whole token's code before lists.
        for tok in ("x.json,margin", "margin,x.json"):
            with self.subTest(tok=tok):
                self.refused(["find-missing-history", tok,
                              "--write-missing-history"], 2,
                             "--write-missing-history takes no FILE "
                             "(x.json)")
        self.refused(["find-missing-history", "x.json",
                      "--write-missing-history"], 2,       # one: as before
                     "--write-missing-history takes no FILE (x.json)")


class TestUsCryptoInTheRadar(unittest.TestCase):
    """wash-radar's US crypto rule on each listed name: some crypto,
    refused naming them (exit 1); one crypto account alone: nothing to
    check (exit 0, as before)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "usa"\n'
            'base_currency = "USD"\nsource_currencies = []\n'
            'local_timezone = "America/New_York"\n'
            '[accounts.coins]\ntype = "taxable"\ncrypto = true\n'
            '[accounts.stock]\ntype = "taxable"\n')
        (self.root / "work").mkdir()
        for n in ("coins", "stock"):
            (self.root / "work" / f"{n}_base.json").write_text(
                '{"transactions": []}')

    def _scoped(self, names):
        err = io.StringIO()
        with contextlib.redirect_stdout(err), \
                contextlib.redirect_stderr(err):
            try:
                PLAN._radar_scoped_bases(self.root, self.root / "work",
                                         names, None)
            except SystemExit as e:
                if isinstance(e.code, str):
                    return 1, err.getvalue() + e.code
                return e.code, err.getvalue()
        return None, err.getvalue()

    def test_names(self):
        code, text = self._scoped(("stock", "coins"))
        self.assertEqual(code, 1, text)
        self.assertIn("coins is a crypto account of a US project", text)
        self.assertIn("Leave it out of the list: 'stock'.", text)
        code, text = self._scoped(("coins",))
        self.assertEqual(code, 0, text)
        self.assertIn("nothing to check", text)


class TestWantedAccounts(unittest.TestCase):
    """lib/crypto_sends reads `want` exactly as lib/account_list's
    names_of (its own copy: the import would close a cycle)."""

    def test_as_names_of(self):
        for want in (None, "", "a", "a,b", [], [""], ("b", "a"),
                     {"b", "a"}, frozenset(), ["a", "a"], ["a", ""],
                     ("crypto",)):
            with self.subTest(want=want):
                self.assertEqual(CS.wanted_accounts(want),
                                 AL.names_of(want))
        self.assertEqual(CS.wanted_accounts("a,b"), ("a,b",))   # one name
        self.assertEqual(CS.wanted_accounts([]), ())            # none


# Two Coinbase crypto accounts, each with a send that arrives nowhere.
def _cb_send(tag, day):
    return (CB_HEADER
            + f"{tag}1,2025-07-01 12:00:00 UTC,Buy,BTC,2,CAD,100000,200000,"
              "200000,0,Bought 2 BTC\n"
            + f"{tag}2,2025-07-{day} 10:00:00 UTC,Send,BTC,1,CAD,110000,"
              "110000,110000,0,Sent 1 BTC\n")


class TestCryptoSends(unittest.TestCase):
    """crypto-sends: the listing takes a list; --set/--unset/--write one."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="taxjson-acct-lists-cb-")
        cls.root, cls.home = _proj(cls._tmp, {
            "ca": {"cb.csv": _cb_send("a", "10")},
            "cb": {"cb.csv": _cb_send("b", "12")}})
        with open(cls.root / "taxjson.toml", "a") as f:
            f.write('[accounts.margin]\ntype = "taxable"\n')
        (cls.root / "inputs" / "margin").mkdir()
        (cls.root / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 10 -10 0\n")
        r = _cli(cls.root, cls.home, "run", "--no-input")
        if r.returncode != 0:
            raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _sends(self, *argv):
        r = _cli(self.root, self.home, "crypto-sends", *argv, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        return {a: v for a, v in doc["accounts"].items()}

    def test_the_union_in_either_order(self):
        ca, cb = self._sends("ca"), self._sends("cb")
        self.assertEqual(list(ca), ["ca"])
        self.assertEqual(list(cb), ["cb"])
        self.assertEqual(self._sends("ca,cb"), {**ca, **cb})
        self.assertEqual(self._sends("cb,ca"), {**ca, **cb})

    def test_the_report_reads_the_names_given(self):
        # A string is one name, an empty collection none (names_of).
        from taxjson.pipeline.config import load_config
        cfg = load_config(self.root)
        for want, shown in (((), []), ("ca", ["ca"]), (("cb", "ca"),
                                                      ["ca", "cb"]),
                            (None, ["ca", "cb"])):
            with self.subTest(want=want):
                rep = CS.build_report(self.root, cfg, None, None, want=want,
                                      with_pool=False)
                self.assertEqual(sorted(rep["accounts"]), shown)

    def test_refusals(self):
        for argv, words in (
                (["ca,margin"], "'margin' is not a crypto account"),
                (["ca,cb", "--write"], "`tjs crypto-sends --write` takes "
                                       "one account: 'ca,cb' names 2"),
                (["ca,cb", "--unset", "x"], "`tjs crypto-sends --unset` "
                                            "takes one account")):
            with self.subTest(argv=argv):
                r = _cli(self.root, self.home, "crypto-sends", *argv)
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(words, r.stderr)
                self.assertNotIn("Traceback", r.stderr)


class _FakeFetcher:
    """A fetcher plugin that records the accounts it is asked for."""
    brokerages = ("fakebroker",)
    description = "Fake test fetcher"
    account_keys = ()
    setup_hint = ""
    calls: list = []

    def add_arguments(self, p):
        pass

    def fetch(self, request):
        _FakeFetcher.calls.append(list(request.accounts))
        return {a: {"dry": request.dry_run} for a in request.accounts}


class TestFetch(unittest.TestCase):
    """fetch: a comma list and space-separated names mean the same."""

    def setUp(self):
        _FakeFetcher.calls = []
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("TAXJSON_OFFLINE", None)     # the fake: no network
        ep = types.SimpleNamespace(name="fake", dist=None,
                                   load=lambda: _FakeFetcher)
        p = mock.patch.object(FP, "_entry_points", return_value=[ep])
        p.start()
        self.addCleanup(p.stop)
        FP.discover(refresh=True)
        self.addCleanup(FP.discover, True)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n'
            '[accounts.b]\ntype = "taxable"\nbrokerage = "fakebroker"\n'
            '[accounts.a]\ntype = "taxable"\nbrokerage = "fakebroker"\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')

    def fetch(self, *argv):
        return _inproc(self.root, "fetch", *argv, "--dry-run")

    def test_a_list_is_the_names_in_order(self):
        for argv in (["b,a"], ["b", "a"]):
            with self.subTest(argv=argv):
                _FakeFetcher.calls = []
                code, _out, err = self.fetch(*argv)
                self.assertEqual(code, 0, err)
                self.assertEqual(_FakeFetcher.calls, [["b", "a"]])
        _FakeFetcher.calls = []
        code, _out, err = self.fetch()
        self.assertEqual(code, 0, err)
        self.assertEqual(_FakeFetcher.calls, [["a", "b"]])   # sorted

    def test_each_listed_name_declares_a_brokerage(self):
        code, _out, err = self.fetch("a,rrsp")
        self.assertEqual(code, 1, err)
        self.assertIn("[accounts.rrsp] declares no `brokerage`", err)
        code, _out, err = self.fetch("a,a")
        self.assertEqual(code, 1, err)
        self.assertEqual(_FakeFetcher.calls, [])


if __name__ == "__main__":
    unittest.main()
