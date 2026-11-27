"""A comma-separated ACCOUNT list (`tjs gains-base margin,crypto`): the
foundation every command converts onto.

lib/account_list parses and refuses a list (an empty, artifact, path or
repeated name, a name not in [accounts], two names where one is
wanted). cli/main refuses a list's shape before the command (exit 1)
and, to a command whose registration does not take one
(`p.set_defaults(account_list=True)`), "takes one account"; it sets
nothing. A lone PERIOD starting with a digit is a period, comma or not,
as before lists. cli/common parses the names on every call (`_accounts`,
`_view_scope`: a name not in [accounts] in a list, exit 1); its shared
helpers take None, a name or names, () reading none, and refuse a
listed account with no file (exit 1) unless told `missing_ok`. Every
refusal of a list exits 1; a single name keeps every path, message and
exit code it had. All data is synthetic.
"""
import argparse
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _style import CapturedWidth
from _support.cli_command_runs import _run_project, _tjs

from taxjson.cli import common as CM
from taxjson.cli import main as MAIN
from taxjson.lib import account_list as AL
from taxjson.lib import config_check


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_BETA = ("BUYSELL 2025-02-06 10:00:00 ZZR.TO 2 CAD 50 -100 0\n"
         "BUYSELL 2025-05-06 10:00:00 ZZR.TO -2 CAD 40 80 0\n"
         "DIVIDEND 2025-04-01 10:00:00 ZZR.TO 0 CAD 0 7.50 0\n")
# A second taxable account, and one declared with no inputs (no books).
_EXTRA = ('[accounts.beta]\ntype = "taxable"\n'
          '[accounts.idle]\ntype = "taxable"\n')

# The positionals a command needs besides its accounts (the sweep).
_PLACEHOLDERS = {"symbol": "ZZQ.TO", "file": "opening.csv"}
# The commands that stay one account (docs/commands.md): a partial
# build, a file written into one account's folder, a saved choice, the
# account a FILE.tt is read as.
_ONE_ACCOUNT = ("run", "opening", "elections", "check-tt")


def _inproc(root, *argv, parser=None):
    """`tjs -C ROOT ARGV` in this process: (exit code, stdout, stderr).
    Exit 1 carries its text in SystemExit (lib/out.fail), shown here as
    the interpreter would print it."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    patch = (mock.patch.object(MAIN, "_build_parser", parser) if parser
             else contextlib.nullcontext())
    with mock.patch.object(sys, "argv", ["tjs", "-C", str(root), *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            patch:
        try:
            MAIN.main()
        except SystemExit as e:
            if isinstance(e.code, str):
                err.write(e.code + "\n")
                code = 1
            else:
                code = e.code or 0
    return code, out.getvalue(), err.getvalue()


def _marking(*cmds, record=None):
    """A `_build_parser` whose commands CMDS take a list, their handler
    replaced by one recording the Namespace into RECORD (a list)."""
    real = MAIN._build_parser

    def build(prog):
        p, sub = real(prog)
        for c in cmds:
            sub.choices[c].set_defaults(account_list=True)
            if record is not None:
                sub.choices[c].set_defaults(func=record.append)
        return p, sub
    return build


def _account_commands():
    """{command: (its account argument, the parser)} for every command
    that takes an account (an `account` destination)."""
    from taxjson.cli.parser import _build_parser
    _p, sub = _build_parser("tjs")
    out = {}
    for name, sp in sub.choices.items():
        acts = {a.dest: a for a in sp._actions}
        if "account" in acts and name not in out:
            out[name] = (acts["account"], sp)
    return out


def _list_argv(cmd, act, sp, names):
    """The argvs giving `names` (one comma token) to `cmd`: in its
    ACCOUNT slot, and in the lone PERIOD slot when it has one."""
    rest = []
    for a in sp._actions:
        if (not a.option_strings and a.dest not in ("account", "period",
                                                    "items")
                and a.nargs in (None, 1)):
            rest.append(_PLACEHOLDERS[a.dest])
    if act.option_strings:
        return [[cmd, *rest, act.option_strings[-1], names]]
    out = [[cmd, names, *rest]]
    if any(a.dest == "period" for a in sp._actions):
        out.append([cmd, "all", names, *rest])
    return out


class TestParse(unittest.TestCase):
    """lib/account_list: pure, the names in the order given."""

    def test_every_account_one_name_and_a_list(self):
        self.assertIsNone(AL.parse_accounts(None))
        self.assertIsNone(AL.parse_accounts(""))
        self.assertIsNone(AL.parse_accounts([]))
        self.assertEqual(AL.parse_accounts("margin"), ("margin",))
        self.assertEqual(AL.parse_accounts("rrsp,margin"), ("rrsp", "margin"))
        self.assertEqual(AL.parse_accounts(["a,b", "c"]), ("a", "b", "c"))
        self.assertEqual(AL.parse_accounts("a,b", ["b", "a", "x"]),
                         ("a", "b"))

    def _refused(self, text, configured=None, one=False):
        with self.assertRaises(AL.AccountListError) as cm:
            AL.parse_accounts(text, configured, one=one)
        self.assertIsInstance(cm.exception, ValueError)
        return cm.exception

    def test_an_empty_name(self):
        for text in ("a,,b", ",a", "a,", ","):
            with self.subTest(text=text):
                e = self._refused(text)
                self.assertEqual((e.kind, e.text), ("empty", text))

    def test_a_name_twice(self):
        e = self._refused("a,b,a,b,a")
        self.assertEqual((e.kind, e.names), ("duplicate", ("a", "b")))
        self.assertEqual(self._refused(["a", "b,a"]).kind, "duplicate")

    def test_no_account_name(self):
        for text, bad in (("./x,b", "./x"), ("a,*", "*"), ("a,-b", "-b")):
            with self.subTest(text=text):
                e = self._refused(text)
                self.assertEqual((e.kind, e.names), ("name", (bad,)))

    def test_an_artifact_name(self):
        for text, bad in (("a_raw,b", ("a_raw",)), ("a,B_Gains", ("B_Gains",)),
                          ("../x_raw,b", ("../x_raw",)), ("a_base", ("a_base",))):
            with self.subTest(text=text):
                e = self._refused(text)
                self.assertEqual((e.kind, e.names), ("artifact", bad))

    def test_a_list_is_marked(self):
        self.assertTrue(self._refused("a,nope", ["a"]).listed)
        self.assertTrue(self._refused(["a", "nope"], ["a"]).listed)
        self.assertFalse(self._refused("nope", ["a"]).listed)
        self.assertFalse(self._refused("x_raw").listed)

    def test_not_configured(self):
        e = self._refused("a,nope,zip", ["b", "a"])
        self.assertEqual((e.kind, e.names), ("unknown", ("nope", "zip")))
        self.assertEqual(e.configured, ("a", "b"))
        self.assertEqual(self._refused("nope", ["a"]).kind, "unknown")

    def test_one_wanted(self):
        e = self._refused("a,b", one=True)
        self.assertEqual((e.kind, e.count), ("one", 2))
        # Counted without the empty ones; one name and an empty one is
        # the empty name.
        self.assertEqual(self._refused("a,,b", one=True).count, 2)
        self.assertEqual(self._refused("a,", one=True).kind, "empty")
        self.assertEqual(AL.parse_accounts("a", one=True), ("a",))

    def test_a_comma_is_a_list(self):
        self.assertTrue(AL.is_account_list("a,b"))
        self.assertTrue(AL.is_account_list(","))
        for tok in ("margin", "", None, ["a,b"], "2025-06-30"):
            self.assertFalse(AL.is_account_list(tok), tok)

    def test_names_of_normalizes(self):
        self.assertIsNone(AL.names_of(None))
        self.assertIsNone(AL.names_of(""))
        # No account (a subset rule that left none), never every one.
        for empty in ([], (), set(), [""], ("", "")):
            self.assertEqual(AL.names_of(empty), (), empty)
        self.assertEqual(AL.names_of("margin"), ("margin",))
        # A string is one name, never split: an unconverted command's
        # text reads as it always did (`stats 1y,margin --all-history`).
        self.assertEqual(AL.names_of("1y,margin"), ("1y,margin",))
        self.assertEqual(AL.names_of(["b", "a", "b"]), ("b", "a"))
        self.assertEqual(AL.names_of({"b", "a"}), ("a", "b"))
        self.assertEqual(AL.names_of(["b", "a"]), ("b", "a"))
        self.assertEqual(AL.names_of({"b": 1, "a": 2}), ("b", "a"))

    def test_the_name_rule_is_the_configs(self):
        self.assertIs(AL.ACCOUNT_NAME_RE, config_check.ACCOUNT_NAME_RE)
        self.assertIs(MAIN.ACCOUNT_NAME_RE, config_check.ACCOUNT_NAME_RE)


class TestRefusalWords(unittest.TestCase):
    """What each refusal says, and its exit code."""

    def _say(self, text, configured=None, one=False, **kw):
        try:
            AL.parse_accounts(text, configured, one=one)
        except AL.AccountListError as e:
            return AL.refusal(e, "gains-base", **kw)
        self.fail(f"{text!r} was not refused")

    def test_one_account(self):
        head, details, code = self._say("margin,crypto", one=True)
        self.assertEqual(head, "`tjs gains-base` takes one account: "
                               "'margin,crypto' names 2")
        self.assertEqual(details, ("Run it once for each account.",))
        self.assertEqual(code, 1)
        head, _d, _c = self._say("a,b", one=True, mode="--set")
        self.assertTrue(head.startswith("`tjs gains-base --set` takes one"),
                        head)

    def test_empty_and_twice_name_the_fix(self):
        head, details, code = self._say("margin,,crypto")
        self.assertEqual(head, "'margin,,crypto' has an empty account name")
        self.assertIn("'margin,crypto'", details[0])
        self.assertEqual(code, 1)
        _h, details, _c = self._say("margin,", takes_list=False)
        self.assertEqual(details, ("Give the name alone ('margin').",))
        head, details, code = self._say("margin,crypto,margin")
        self.assertEqual(head, "'margin,crypto,margin' names margin more "
                               "than once")
        self.assertEqual(details, ("Name each account once: "
                                   "'margin,crypto'.",))
        self.assertEqual(code, 1)

    def test_unknown_keeps_the_single_name_words(self):
        head, details, code = self._say("margin,nope", ["margin", "rrsp"])
        self.assertEqual(head, "no [accounts.nope] in taxjson.toml — check "
                               "the name (configured: margin, rrsp).")
        # A list's refusal exits 1, the code any comma token had; one
        # name keeps the single-name check's 2.
        self.assertEqual((details, code), ((), 1))
        self.assertEqual(self._say("nope", ["margin"])[2], 2)
        head, _d, _c = self._say("x,y", ["margin"])
        self.assertEqual(head, "no [accounts.x], [accounts.y] in "
                               "taxjson.toml — check the names (configured: "
                               "margin).")

    def test_a_path_keeps_its_words(self):
        head, details, code = self._say("./x,margin")
        self.assertEqual(head, AL.not_an_account_name("./x"))
        self.assertIn("is not an account name", head)
        self.assertEqual((details, code), ((), 1))

    def test_an_artifact_keeps_main_s_words(self):
        head, details, code = self._say("margin,beta_raw")
        self.assertEqual(head, "'beta_raw' is not an account: names ending "
                               "in '_raw' are pipeline work files (an "
                               "account's native or intermediate books), "
                               "never accounts — use an account name from "
                               "[accounts] in taxjson.toml.")
        self.assertEqual((details, code), ((), 1))

    def test_the_json_convention_is_stated(self):
        doc = " ".join(AL.__doc__.split())
        self.assertIn('adds "account_names": the names in the order given, '
                      'or null for every account', doc)
        self.assertIn('keeps "account" byte-for-byte as today', doc)


class _Project(unittest.TestCase):
    """A built Canada project: margin and beta (taxable, with books), idle
    (declared, no inputs)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp(prefix="taxjson-acct-list-"))
        cls.root = _run_project(cls._tmp, extra=_EXTRA,
                                more_inputs={"beta": _BETA})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _stops(self, fn, *a, **kw):
        """(exit code, its text) of `fn(*a, **kw)`, which must stop: exit
        1 carries its text in SystemExit (lib/out.fail)."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit) as cm:
            fn(*a, **kw)
        code = cm.exception.code
        if isinstance(code, str):
            return 1, code
        return code, err.getvalue()


class TestThroughTjs(_Project):
    """The real program (a child process) refuses before the command."""

    def _one_line(self, r, code, *texts):
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        for t in texts:
            self.assertIn(t, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_list_to_a_command_of_one_account(self):
        for argv in (["elections", "margin,beta"],
                     ["run", "--account", "margin,beta"]):
            with self.subTest(argv=argv):
                self._one_line(_tjs(self.root, *argv), 1,
                               f"`tjs {argv[0]}` takes one account: "
                               f"'margin,beta' names 2",
                               "Run it once for each account.")

    def test_a_path_item_keeps_its_message(self):
        self._one_line(_tjs(self.root, "sum", "./x,beta"), 1,
                       "'./x' is not an account name",
                       "not a path or a pattern")
        self._one_line(_tjs(self.root, "winners", "beta,margin_raw"), 1,
                       "'margin_raw' is not an account")

    def test_an_empty_name_to_a_command_of_one_account(self):
        self._one_line(_tjs(self.root, "elections", "margin,"), 1,
                       "'margin,' has an empty account name",
                       "Give the name alone ('margin').")

    def test_a_lone_positional_starting_with_a_digit_is_a_period(self):
        """Comma or not, as before lists: its command reads it (a list
        whose first name starts with a digit goes after a period)."""
        r = _tjs(self.root, "divs", "1y,margin")
        self._one_line(r, 2, "invalid time period '1y,margin'")
        self.assertNotIn("account", r.stderr)

    def test_a_command_s_own_reading_of_a_digit_first_token(self):
        """stats reads its lone positional itself: the token whole, as
        before lists (review P1: a split changed this text)."""
        self._one_line(_tjs(self.root, "stats", "1y,margin",
                            "--all-history"), 1,
                       "no books for account '1y,margin' in work/ — run "
                       "`tjs run` first")

    def test_a_period_word_first_says_no_period_has_a_comma(self):
        self._one_line(_tjs(self.root, "elections", "ytd,margin"), 1,
                       "`tjs elections` takes one account: 'ytd,margin' names 2",
                       "A period never has a comma.")

    def test_list_s_second_positional(self):
        """`tjs list [ACCOUNT] [YYYY-MM-DD]` takes the date in either
        slot: a list in the second is checked as in the first."""
        # (list takes a list: the accounts' positions that day.)
        r = _tjs(self.root, "list", "2025-06-01", "margin,beta")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OPEN POSITIONS", r.stdout)
        self.assertIn("margin", r.stdout)
        self._one_line(_tjs(self.root, "list", "2025-06-01", "beta,x_raw"),
                       1, "'x_raw' is not an account")
        r = _tjs(self.root, "list", "2025-06-01")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OPEN POSITIONS", r.stdout)

    def test_one_name_is_as_it_was(self):
        """The controls: one good name, one unknown name, three families
        (a listing, the filing sum, the planning radar)."""
        for argv in (["divs", "margin"], ["sum", "margin"],
                     ["wash-radar", "margin"]):
            with self.subTest(argv=argv):
                r = _tjs(self.root, *argv)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("account list", r.stderr)
                self.assertNotIn("takes one account", r.stderr)
        for argv, words in (
                (["divs", "nope"], "no native transaction file for account "
                                   "'nope' in work/ — run `tjs run` first, "
                                   "or check the name"),
                (["sum", "nope"], "no gains for account 'nope' in work/"),
                (["wash-radar", "nope"], "no [accounts.nope] in taxjson.toml"
                                         " — check the name (configured: "
                                         "beta, idle, margin).")):
            with self.subTest(argv=argv):
                self._one_line(_tjs(self.root, *argv), 2, words)


class TestEveryCommand(_Project):
    """A list to every command that takes an account and has no marker
    is refused, naming it; the one-account commands carry none."""

    def test_the_sweep_finds_the_commands(self):
        found = _account_commands()
        for c in ("sum", "gains-base", "divs", "wash-radar", "audit",
                  "journals", "fetch") + _ONE_ACCOUNT:
            self.assertIn(c, found)
        self.assertGreaterEqual(len(found), 40)

    def test_the_one_account_commands_take_no_list(self):
        found = _account_commands()
        for c in _ONE_ACCOUNT:
            self.assertFalse(found[c][1].get_default("account_list"), c)

    def test_the_codes_a_comma_had_before_lists(self):
        """As on the base tree: a lone PERIOD starting with a digit is a
        period (its command refuses it, exit 2), checked whole as before
        (an artifact name at its end, exit 1); a PERIOD beside an
        ACCOUNT is read whole; any other comma token exits 1."""
        for argv, code in ((["divs", "1y,x/y"], 2),
                           (["divs", "1y_raw,margin"], 2),
                           (["divs", "1y,margin_raw"], 1),
                           (["divs", "1y,margin_raw,"], 2),
                           (["divs", "margin_raw,beta"], 1),
                           (["divs", "x/y,beta"], 1),
                           (["winners", "a,b", "margin"], 1),
                           (["winners", "1y,2y", "margin"], 2)):
            with self.subTest(argv=argv):
                got, _out, err = _inproc(self.root, *argv)
                self.assertEqual(got, code, err)
                self.assertNotIn("Traceback", err)

    def test_a_list_to_a_command_without_the_marker(self):
        from taxjson.lib.fetchers import installed
        for cmd, (act, sp) in sorted(_account_commands().items()):
            if sp.get_default("account_list"):
                continue                # converted: its own tests
            if cmd == "fetch" and not installed():
                continue                # (no fetcher: its install line)
            for argv in _list_argv(cmd, act, sp, "margin,beta"):
                with self.subTest(argv=argv):
                    code, _out, err = _inproc(self.root, *argv)
                    self.assertEqual(code, 1, err)
                    self.assertIn(f"`tjs {cmd}` takes one account: "
                                  f"'margin,beta' names 2", err)


class TestMarked(_Project):
    """A command whose registration takes a list (one marked here, in
    this process only)."""

    def _run(self, *argv):
        seen = []
        code, out, err = _inproc(self.root, *argv, parser=_marking(
            "spinoffs", "winners", record=seen))
        return code, err, (seen[0] if seen else None)

    def test_main_sets_nothing(self):
        code, err, ns = self._run("spinoffs", "beta,margin")
        self.assertEqual(code, 0, err)
        self.assertFalse(hasattr(ns, "accounts"))
        self.assertEqual(ns.account, "beta,margin")
        self.assertEqual(CM._accounts(ns), ("beta", "margin"))
        # Parsed on every call: what the command made of its arguments.
        ns.account = "margin"
        self.assertEqual(CM._accounts(ns), ("margin",))

    def test_the_lone_period_slot(self):
        code, err, ns = self._run("winners", "margin,beta")
        self.assertEqual(code, 0, err)
        self.assertEqual((ns.account, ns.period), (None, "margin,beta"))
        keep, scope, names = CM._view_scope(ns, self.root)
        self.assertEqual((scope, names), ("tax year 2025", ("margin", "beta")))
        self.assertEqual(CM._accounts(ns), ("margin", "beta"))
        code, err, ns = self._run("winners", "all", "beta,margin")
        self.assertEqual(CM._view_scope(ns, self.root)[1:],
                         ("all history", ("beta", "margin")))

    def test_one_name_and_none(self):
        _c, _e, ns = self._run("spinoffs", "margin")
        self.assertEqual(CM._accounts(ns), ("margin",))
        _c, _e, ns = self._run("spinoffs")
        self.assertIsNone(CM._accounts(ns))
        # A lone word is the period or the account by the config, as
        # _lone_positional reads it.
        _c, _e, ns = self._run("winners", "margin")
        self.assertEqual(CM._accounts(ns), ("margin",))
        _c, _e, ns = self._run("winners", "1y")
        self.assertIsNone(CM._accounts(ns))

    def test_refused_before_the_command(self):
        for argv, words in (
                (["spinoffs", "margin,,beta"],
                 "'margin,,beta' has an empty account name"),
                (["spinoffs", "margin,beta,margin"],
                 "'margin,beta,margin' names margin more than once"),
                (["spinoffs", "margin,beta_gains"],
                 "'beta_gains' is not an account"),
                (["winners", "all", "./x,beta"],
                 "'./x' is not an account name")):
            with self.subTest(argv=argv):
                got, err, ns = self._run(*argv)
                self.assertEqual(got, 1, err)
                self.assertIn(words, err)
                self.assertIsNone(ns)

    def test_an_unknown_name_is_the_command_s_exit_1(self):
        code, err, ns = self._run("winners", "margin,nope")
        self.assertEqual(code, 0, err)          # main passes it on
        code, err = self._stops(CM._view_scope, ns, self.root)
        self.assertEqual(code, 1)
        self.assertIn("no [accounts.nope] in taxjson.toml — check the name "
                      "(configured: beta, idle, margin).", err)
        _c, _e, ns = self._run("winners", "ytd,margin")
        code, err = self._stops(CM._view_scope, ns, self.root)
        self.assertEqual(code, 1)
        self.assertIn("no [accounts.ytd] in taxjson.toml", err)
        self.assertIn("A period never has a comma.", err)

    def test_a_digit_first_is_the_period_marked_or_not(self):
        code, err, ns = self._run("winners", "1y,,margin")
        self.assertEqual(code, 0, err)          # main: the command's
        code, err = self._stops(CM._view_scope, ns, self.root)
        self.assertEqual(code, 2)
        self.assertIn("invalid time period '1y,,margin'", err)
        _c, _e, ns = self._run("winners", "ytd", "2fa,margin")
        code, err = self._stops(CM._view_scope, ns, self.root)
        self.assertEqual(code, 1)
        self.assertIn("no [accounts.2fa] in taxjson.toml", err)


class TestCommonHelpers(_Project):
    """cli/common, called directly (as tests call cmd_* functions)."""

    def _ns(self, **kw):
        base = {"dir": str(self.root), "cmd": "spinoffs", "account": None,
                "period": None}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_accounts_parses_without_main(self):
        self.assertEqual(CM._accounts(self._ns(account="beta,margin")),
                         ("beta", "margin"))
        # One name is the command's own to check, as before lists.
        self.assertEqual(CM._accounts(self._ns(account="nope")), ("nope",))
        self.assertEqual(CM._accounts(self._ns(period="margin")),
                         ("margin",))
        self.assertIsNone(CM._accounts(self._ns(period="all")))
        self.assertIsNone(CM._accounts(self._ns(account="")))
        for text, words in (("margin,nope", "no [accounts.nope] in "
                                            "taxjson.toml"),
                            ("margin,,beta", "has an empty account name"),
                            ("margin,beta_raw", "'beta_raw' is not an "
                                                "account"),
                            ("margin,../x", "'../x' is not an account name")):
            with self.subTest(text=text):
                code, err = self._stops(CM._accounts, self._ns(account=text))
                self.assertEqual(code, 1)
                self.assertIn(words, err)

    def test_accounts_reads_the_text_a_command_decided(self):
        """div-estimate reads `ytd` as the account (its window takes
        only <n>[dwmy]); list moves a date out of the ACCOUNT slot."""
        ns = self._ns(period="ytd")
        self.assertIsNone(CM._accounts(ns))
        self.assertEqual(CM._accounts(ns, text="ytd"), ("ytd",))
        ns = self._ns(account="2025-06-01")
        self.assertEqual(CM._accounts(ns, text="beta,margin"),
                         ("beta", "margin"))
        self.assertIsNone(CM._accounts(ns, text=""))
        self.assertEqual(CM._accounts(ns, text=["margin", "beta"]),
                         ("margin", "beta"))
        code, err = self._stops(CM._accounts, ns, text=["margin", "beta,x"])
        self.assertEqual(code, 1)
        self.assertIn("no [accounts.x] in taxjson.toml", err)

    def test_one_names_the_mode(self):
        ns = self._ns(account="margin,beta", cmd="crypto-sends")
        code, err = self._stops(CM._accounts, ns, one="--set")
        self.assertEqual(code, 1)
        self.assertIn("`tjs crypto-sends --set` takes one account: "
                      "'margin,beta' names 2", err)
        self.assertIn("Run it once for each account.", err)
        self.assertEqual(CM._accounts(self._ns(account="margin"), one=True),
                         ("margin",))
        code, err = self._stops(CM._accounts, self._ns(account=["a", "b"]),
                                one=True)
        self.assertEqual(code, 1)
        self.assertIn("takes one account: 'a b' names 2", err)

    def test_require_listed_names_each_one_missing(self):
        CM._require_listed(None, set(), "gains file")
        CM._require_listed((), set(), "gains file")
        CM._require_listed(("margin",), {"margin": 1}, "gains file")
        code, err = self._stops(CM._require_listed, ("margin", "idle", "x"),
                                {"margin"}, "gains file")
        self.assertEqual(code, 1)           # a list: exit 1, whatever code=
        self.assertIn("no gains file for accounts 'idle', 'x' in work/", err)
        self.assertEqual(self._stops(CM._require_listed, ("margin", "idle"),
                                     {"margin"}, "books", code=2)[0], 1)
        # One name: `code` (2 by default).
        self.assertEqual(self._stops(CM._require_listed, "idle", (),
                                     "books")[0], 2)
        self.assertEqual(self._stops(CM._require_listed, "idle", (), "books",
                                     code=1)[0], 1)

    def test_require_books_takes_names(self):
        CM._require_books(self.root, ("margin", "beta"))
        CM._require_books(self.root, ())             # none named: nothing
        code, err = self._stops(CM._require_books, self.root,
                                ["margin", "idle"])
        self.assertEqual(code, 1)
        self.assertIn("no books for account 'idle' in work/", err)
        code, err = self._stops(CM._require_books, self.root, "idle")
        self.assertIn("no books for account 'idle' in work/ — run `tjs run` "
                      "first (an account with no inputs has no books).", err)

    def test_gains_files_of(self):
        from taxjson.lib.report_model import resolve_gains_files
        cache = self.root / "work"
        self.assertEqual(CM._gains_files_of(cache, "margin"),
                         resolve_gains_files(cache, "margin"))
        self.assertEqual(CM._gains_files_of(cache, None),
                         resolve_gains_files(cache, None))
        self.assertEqual(CM._gains_files_of(cache, ()), {})
        self.assertEqual(CM._gains_files_of(cache, []), {})
        both = CM._gains_files_of(cache, ("margin", "beta"))
        self.assertEqual(sorted(both), ["beta", "margin"])
        # A listed account with no gains file: refused, unless the
        # command unions over accounts some of which may have none.
        code, err = self._stops(CM._gains_files_of, cache, ["margin", "idle"])
        self.assertEqual(code, 1)
        self.assertIn("no gains file for account 'idle' in work/", err)
        self.assertEqual(sorted(CM._gains_files_of(cache, ["margin", "idle"],
                                                   missing_ok=True)),
                         ["margin"])
        # Text is one name, as an unconverted command passes it.
        self.assertEqual(CM._gains_files_of(cache, "margin,beta"),
                         resolve_gains_files(cache, "margin,beta"))

    def test_the_gains_views_refuse_a_listed_account_with_no_file(self):
        cache = self.root / "work"
        ns = self._ns(cmd="winners")
        for fn, a in ((CM._window_gains_files, (self.root, cache,
                                                 ("beta", "idle"), ns)),
                      (CM._leaps_scope_guard, (self.root, ("beta", "idle"),
                                               ns)),
                      (CM._leaps_contracts, (self.root, ("beta", "idle"),
                                             "leaps")),
                      (CM._leaps_empty_doc, (self.root, ("beta", "idle"))),
                      (CM._leaps_closed, (self.root, ("beta", "idle"), set(),
                                          lambda d: True))):
            with self.subTest(fn=fn.__name__):
                code, err = self._stops(fn, *a)
                self.assertEqual(code, 1)
                self.assertIn("'idle'", err)
                fn(*a, missing_ok=True)

    def test_no_account_reads_nothing(self):
        cache = self.root / "work"
        ns = self._ns(cmd="winners")
        self.assertEqual(CM._window_gains_files(self.root, cache, (), ns),
                         ({}, False))
        self.assertEqual(CM._leaps_scope_guard(self.root, [], ns), {})
        self.assertEqual(CM._leaps_contracts(self.root, (), "leaps"), {})
        self.assertEqual(CM._leaps_closed(self.root, (), set(),
                                          lambda d: True)[:2], ([], False))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            CM._warn_accounts_without_books(self.root, set(), "x", "books",
                                            among=())
        self.assertEqual(err.getvalue(), "")

    def test_collect_period_txs_reads_the_listed_accounts(self):
        rows, scope, _bad, _keep = CM._collect_period_txs(
            self._ns(cmd="divs-sum", account="beta,margin"), "divs-sum",
            ("DIVIDEND",))
        self.assertEqual(scope, "tax year 2025")
        self.assertEqual({a for a, _tx in rows}, {"beta"})
        one, _s, _b, _k = CM._collect_period_txs(
            self._ns(cmd="divs-sum", account="beta"), "divs-sum",
            ("DIVIDEND",))
        self.assertEqual(one, rows)
        code, err = self._stops(CM._collect_period_txs,
                                self._ns(cmd="divs-sum", account="beta,idle"),
                                "divs-sum", ("DIVIDEND",))
        self.assertEqual(code, 1)
        self.assertIn("no native transaction file for account 'idle' in "
                      "work/", err)
        some, _s, _b, _k = CM._collect_period_txs(
            self._ns(cmd="divs-sum", account="beta,idle"), "divs-sum",
            ("DIVIDEND",), missing_ok=True)
        self.assertEqual(some, rows)

    def test_staking_rows_refuse_a_list_with_exit_1(self):
        code, err = self._stops(CM._staking_rows, self._ns(
            cmd="staking", account="margin,idle", symbol=None, broker=None))
        self.assertEqual(code, 1)
        self.assertIn("'idle'", err)

    def test_lone_positional(self):
        self.assertEqual(CM._lone_positional(self.root, "margin,beta", None),
                         (None, "margin,beta"))
        self.assertEqual(CM._lone_positional(self.root, "margin", None),
                         (None, "margin"))
        self.assertEqual(CM._lone_positional(self.root, "1y", None),
                         ("1y", None))
        # A digit first: the period it was before lists (refused, 2).
        code, err = self._stops(CM._lone_positional, self.root, "1y,margin",
                                None)
        self.assertEqual(code, 2)
        self.assertIn("invalid time period '1y,margin'", err)

    def test_the_comma_text_is_a_programming_error(self):
        """Helpers only a converted command hands a list: the raw comma
        text (`args.account`) would match no account, silently."""
        from taxjson.pipeline.config import load_config
        cfg = load_config(self.root)
        text = "margin,beta"
        for fn, a, kw in (
                (CM._uncovered_sales, (self.root, cfg, text), {}),
                (CM._unanswered_covers, (self.root, cfg, text), {}),
                (CM._leaps_empty_doc, (self.root, text), {}),
                (CM._refuse_us_crypto_account,
                 (self.root, text, "tjs wash-radar"), {}),
                (CM._warn_accounts_without_books,
                 (self.root, set(), "divs", "books"), {"among": text})):
            with self.subTest(fn=fn.__name__):
                with self.assertRaises(ValueError) as cm:
                    fn(*a, **kw)
                self.assertEqual(str(cm.exception), "internal: pass the names "
                                 "from _accounts, not the comma text")
                kw = {k: ("margin", "beta") for k in kw}
                a = tuple(("margin", "beta") if x == text else x for x in a)
                with contextlib.redirect_stderr(io.StringIO()):
                    fn(*a, **kw)                # the names: no error

    def test_the_views_read_a_string_as_one_name(self):
        """What an unconverted command reaches with its text whole
        (`stats 1y,margin --all-history`): read as before lists."""
        cache = self.root / "work"
        self.assertEqual(CM._gains_files_of(cache, "1y,margin"), {})
        self.assertEqual(CM._window_gains_files(self.root, cache, "1y,margin",
                                                self._ns(cmd="stats")),
                         ({}, False))
        code, err = self._stops(CM._require_books, self.root, "1y,margin")
        self.assertEqual(code, 1)
        self.assertIn("no books for account '1y,margin' in work/ — run `tjs "
                      "run` first", err)

    def test_filters_take_names(self):
        from taxjson.pipeline.config import load_config
        cfg = load_config(self.root)
        self.assertEqual(CM._uncovered_sales(self.root, cfg, ("margin",)), [])
        self.assertEqual(CM._unanswered_covers(self.root, cfg,
                                               ("beta", "margin")), [])
        self.assertEqual(CM._uncovered_sales(self.root, cfg, ()), [])
        self.assertEqual(CM._unanswered_covers(self.root, cfg, []), [])
        for fn in (CM._uncovered_sales, CM._unanswered_covers):
            with self.subTest(fn=fn.__name__):
                code, err = self._stops(fn, self.root, cfg, ("margin", "idle"))
                self.assertEqual(code, 1)
                self.assertIn("no books for account 'idle' in work/", err)
                self.assertEqual(fn(self.root, cfg, ("margin", "idle"),
                                    missing_ok=True), [])


class TestUsCrypto(unittest.TestCase):
    """_refuse_us_crypto_account with names: every one a US crypto
    account is nothing to check (exit 0, as one); some of them, refused
    naming them (exit 1, as every refusal of a list)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "usa"\n'
            'base_currency = "USD"\nsource_currencies = []\n'
            'local_timezone = "America/New_York"\n'
            '[accounts.coins]\ntype = "taxable"\ncrypto = true\n'
            '[accounts.stock]\ntype = "taxable"\n')

    def tearDown(self):
        self._tmp.cleanup()

    def _code(self, names):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                CM._refuse_us_crypto_account(self.root, names, "tjs wash-radar")
            except SystemExit as e:
                if isinstance(e.code, str):         # exit 1, its text
                    return 1, out.getvalue() + err.getvalue() + e.code
                return e.code, out.getvalue() + err.getvalue()
        return None, out.getvalue() + err.getvalue()

    def test_names(self):
        self.assertIsNone(self._code("stock")[0])
        code, text = self._code("coins")
        self.assertEqual(code, 0)
        self.assertIn("coins is a crypto account of a US project — the "
                      "wash-sale rule (§1091) does not apply to it, so "
                      "there is nothing to check.", text)
        self.assertIsNone(self._code(())[0])
        with self.assertRaises(ValueError):
            self._code("stock,coins")                 # the raw text
        code, text = self._code(("stock", "coins"))
        self.assertEqual(code, 1)
        self.assertIn("coins is a crypto account of a US project", text)
        self.assertIn("Leave it out of the list: 'stock'.", text)


if __name__ == "__main__":
    unittest.main()
