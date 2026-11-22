"""Step 5's criterion (PLAN.md, the owner's decision (a) of 2026-11-08,
TJ-2322): one copy of each shared helper. A helper that grew a second
copy fails here, naming where."""
import ast
import functools
import io
import re
import tokenize
import unittest
import _paths

SRC = _paths.REPO / "src" / "taxjson"
# The cash readers' folder (lib/cash_readers until PLAN step 16 commit 12),
# spelled in two parts so this module is not a mention of it.
_GONE = "cash" + "_readers"
_GONE_PATH = re.compile(r"lib[/.]" + _GONE + r"\b")
# When a comment or docstring naming it says the code went: the plan's
# step, a backlog id or a commit.
_DATED = re.compile(r"\bstep \d+|\bTJ-\d+|\b[0-9a-f]{8,40}\b")


@functools.lru_cache(maxsize=None)
def _modules():
    # Parsed once a run: every lock walks them.
    return tuple((p.relative_to(SRC).as_posix(),
                  ast.parse(p.read_text(encoding="utf-8")))
                 for p in sorted(SRC.rglob("*.py")))


def _parsers():
    # The parsers and their toolbox: step 5's criterion is theirs.
    return [(m, t) for m, t in _modules()
            if m.startswith(("lib/brokerages/", "parsers/"))]


def _gone_mentions(source):
    """[(line, dated)] of each mention in `source` of the cash readers'
    folder: dated None for code (an import of it, by any form, or a string
    naming its path, or a path built to it), else True when the comment
    block or docstring holding it says when the code went (`_DATED`: the
    step, a backlog id or a commit), False when not."""
    tree = ast.parse(source)
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            hit = any(_GONE in a.name.split(".") for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            hit = _GONE in (n.module or "").split(".") or any(a.name == _GONE
                                                            for a in n.names)
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            hit = isinstance(n.right, ast.Constant) and n.right.value == _GONE
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            if id(n) in docs:
                if _GONE_PATH.search(n.value):
                    out.append((n.lineno, bool(_DATED.search(" ".join(n.value.split())))))
                continue
            hit = bool(_GONE_PATH.search(n.value))
        else:
            continue
        if hit:
            out.append((n.lineno, None))
    block = []                  # consecutive comment lines: (line, text)
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    for t in tokens + [None]:
        if t is not None and t.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                                        tokenize.DEDENT):
            continue
        if t is not None and t.type == tokenize.COMMENT and (
                not block or t.start[0] == block[-1][0] + 1):
            block.append((t.start[0], t.string))
            continue
        text = " ".join(" ".join(c.lstrip("#").split()) for _l, c in block)
        out += [(line, bool(_DATED.search(text))) for line, c in block
                if _GONE_PATH.search(c)]
        block = [(t.start[0], t.string)] if t is not None and t.type == tokenize.COMMENT else []
    return sorted(out)


def _calls(node, name):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


class TestOneCopy(unittest.TestCase):

    def test_the_futures_prefixes_are_spelled_once(self):
        # taxjson's futures symbol prefixes live in lib/futures_prefixes.py
        # (FUTURES_PREFIXES, FUTURES_PREFIX_GROUP for a regex). A tuple, list
        # or set holding two or more of them, or a regex alternation of
        # them ("F:|"), anywhere else is a second copy.
        prefixes = {"F:", "/", "\\"}
        spelled = []
        for mod, tree in _modules():
            for n in ast.walk(tree):
                if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
                    vals = {e.value for e in n.elts if isinstance(e, ast.Constant)
                            and isinstance(e.value, str)}
                    if len(vals & prefixes) >= 2 and vals <= prefixes:
                        spelled.append(f"{mod}:{n.lineno}")
                elif (isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and "F:|" in n.value):
                    spelled.append(f"{mod}:{n.lineno}")
        self.assertEqual(sorted({w.split(":")[0] for w in spelled}),
                         ["lib/futures_prefixes.py"],
                         "import FUTURES_PREFIXES (or FUTURES_PREFIX_GROUP) from "
                         "taxjson.lib.futures_prefixes: " + ", ".join(sorted(spelled)))

    def test_the_prefixes_module_is_a_leaf(self):
        # Any module reads it at import: it imports nothing of taxjson.
        tree = ast.parse((SRC / "lib" / "futures_prefixes.py").read_text(encoding="utf-8"))
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertEqual([m for m in names if m.startswith("taxjson")], [])

    def test_the_core_regex_follows_the_prefixes(self):
        from taxjson.lib.option_symbols import _FUTURES_PREFIX_RE
        from taxjson.lib.futures_prefixes import FUTURES_PREFIXES
        for p in FUTURES_PREFIXES:
            self.assertEqual(_FUTURES_PREFIX_RE.sub("F:", p + "ES"), "F:ES")
        self.assertEqual(_FUTURES_PREFIX_RE.sub("F:", "ES"), "ES")


    def test_the_warrant_pairing_is_written_once(self):
        # lib/brokerages/base.py `pair_warrant_legs`: a warrant exercise's
        # share leg and its bookkeeping. A parser that hints the .tt form
        # of an unpaired leg (`warrant_exercise_tt_hint`) pairs with it,
        # and none reads a listing's root in a warrant function itself.
        pairers, spelled = set(), []
        for mod, tree in _parsers():
            for f in ast.walk(tree):
                if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if mod == "lib/brokerages/base.py":
                    continue
                if _calls(f, "warrant_exercise_tt_hint"):
                    pairers.add(mod)
                    self.assertTrue(_calls(f, "pair_warrant_legs"),
                                    f"{mod}: {f.name} pairs warrant legs itself")
                if (_calls(f, "_warrant_root")
                        or ("warrant" in f.name
                            and _calls(f, "strip_listing_suffix"))):
                    spelled.append(f"{mod}:{f.lineno} {f.name}")
        self.assertLessEqual({"lib/brokerages/ib_extractor.py",
                              "lib/brokerages/rbc_direct.py"}, pairers)
        self.assertEqual(spelled, [], "call pair_warrant_legs")

    def test_the_trade_money_identities_are_written_once(self):
        # parsers/toolbox/money.py: `money_close` (a stated amount against
        # the one its row implies) and `implied_commission_bounds` (250
        # plus 5%). A parser comparing |a| - b with a tolerance formula
        # of its own (`max(...)`, or a sum with a product), spelling the
        # notional band `max(x, y * max(a, b))` anywhere (IB's and
        # Questrade's form, inline or as a named tolerance), or the 250
        # bound, is a second copy. Other identities (a total against its
        # subtotal and fee, a fee against a fee, a quantity's) are not
        # this one and stay each parser's.
        def name(n):
            return getattr(n.func, "id", getattr(n.func, "attr", ""))

        def is_abs(n):
            return isinstance(n, ast.Call) and name(n) == "abs" and len(n.args) == 1

        def tol_formula(r):
            if isinstance(r, ast.Call) and name(r) == "max":
                return True
            return (isinstance(r, ast.BinOp) and isinstance(r.op, ast.Add)
                    and any(isinstance(x, ast.BinOp) and isinstance(x.op, ast.Mult)
                            for x in (r.left, r.right)))
        spelled = []
        for mod, tree in _parsers():
            if mod == "parsers/toolbox/money.py":
                continue
            for n in ast.walk(tree):
                if (isinstance(n, ast.Compare) and len(n.ops) == 1
                        and is_abs(n.left)
                        and isinstance(n.left.args[0], ast.BinOp)
                        and isinstance(n.left.args[0].op, ast.Sub)
                        and is_abs(n.left.args[0].left)
                        and tol_formula(n.comparators[0])):
                    spelled.append(f"{mod}:{n.lineno}")
                if isinstance(n, ast.Call) and name(n) == "max" and n.args:
                    a0, a1 = n.args[0], n.args[-1]
                    if isinstance(a0, ast.Constant) and a0.value == 250:
                        spelled.append(f"{mod}:{n.lineno}")
                    elif (len(n.args) == 2 and isinstance(a1, ast.BinOp)
                            and isinstance(a1.op, ast.Mult)
                            and any(isinstance(x, ast.Call) and name(x) == "max"
                                    and len(x.args) == 2
                                    for x in (a1.left, a1.right))):
                        spelled.append(f"{mod}:{n.lineno}")
        self.assertEqual(spelled, [], "use taxjson.parsers.toolbox.money")

    def test_money_close_is_the_forms_it_replaced(self):
        from taxjson.parsers.toolbox.money import money_close as mc
        # Questrade's and IB's form: max(abs, rel x the larger).
        self.assertTrue(mc(1000.02, 1000.0, abs_tol=0.02, rel_tol=0.0))
        self.assertFalse(mc(1000.03, 1000.0, abs_tol=0.02, rel_tol=0.0))
        self.assertTrue(mc(-1002.0, 1000.0, abs_tol=0.02, rel_tol=0.002))
        self.assertFalse(mc(1002.01, 1000.0, abs_tol=0.02, rel_tol=0.002))
        # Kraken's: the sum, a cent wider than the max form (5.03 off is
        # within 0.01 + 0.5% of 1005.03, not within 0.5% alone).
        self.assertTrue(mc(1005.03, 1000.0, abs_tol=0.01, rel_tol=0.005,
                           additive=True))
        self.assertFalse(mc(1005.03, 1000.0, abs_tol=0.01, rel_tol=0.005))
        # generic's: scaled by the expected amount, at least 1.
        self.assertTrue(mc(0.05, 0.0, abs_tol=0.05, rel_tol=0.01, scale=1.0))
        self.assertFalse(mc(0.06, 0.0, abs_tol=0.05, rel_tol=0.01, scale=1.0))
        # A NaN is never close: refused, where `diff > tol` let it through
        # (the strict cell parsers refuse one before it gets here).
        self.assertFalse(mc(float("nan"), 1.0, abs_tol=0.02, rel_tol=0.002))
        self.assertFalse(mc(1.0, float("nan"), abs_tol=0.02, rel_tol=0.002))


    def test_the_rename_judgment_is_written_once(self):
        # lib/brokerages/base.py `rename_reach` and `rename_how`: whether a
        # listing looks renamed (Questrade's and RBC's rule) and how the
        # new one goes short. Webull's rule is its own (any short after a
        # same-description predecessor), not a copy.
        detectors, spelled = [], []
        for mod, tree in _parsers():
            for f in ast.walk(tree):
                if (isinstance(f, ast.FunctionDef)
                        and f.name in ("_detect_qt_ticker_changes",
                                       "_detect_ticker_changes")):
                    detectors.append(mod)
                    self.assertTrue(_calls(f, "rename_reach"), f"{mod}: {f.name}")
            if mod == "lib/brokerages/base.py":
                continue
            for n in ast.walk(tree):
                if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and "first appears on" in n.value):
                    spelled.append(f"{mod}:{n.lineno}")
        self.assertEqual(sorted(detectors), ["lib/brokerages/questrade.py",
                                             "lib/brokerages/rbc_direct.py"])
        self.assertEqual(spelled, [], "call rename_how")


    def test_the_account_plan_check_is_written_once(self):
        # lib/brokerages/base.py `check_account_plans`: an export's account
        # types against the taxjson account, several accounts said or
        # refused. Questrade's Account Type and the generic importer's
        # account_type column call it, and its form over all of an
        # account's exports, `check_account_plans_across` (TJ-2576,
        # TJ-2584); no parser spells its
        # refusal or its registered-plan pattern again. IB's statements
        # name no account type, so its several-accounts ATTENTION ("ONE
        # account label") is its own, not a copy.
        callers, across, spelled = [], [], []
        for mod, tree in _parsers():
            if mod == "lib/brokerages/base.py":
                continue
            if _calls(tree, "check_account_plans"):
                callers.append(mod)
            if _calls(tree, "check_account_plans_across"):
                across.append(mod)
            for n in ast.walk(tree):
                if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and ("so its trades would be booked there" in n.value
                             or "RRSP|RRIF|TFSA" in n.value)):
                    spelled.append(f"{mod}:{n.lineno}")
        self.assertEqual(sorted(callers), ["lib/brokerages/generic.py",
                                           "lib/brokerages/questrade.py"])
        self.assertEqual(sorted(across), sorted(callers))
        self.assertEqual(spelled, [], "call check_account_plans")

    def test_the_cash_readers_are_gone(self):
        # Step 5 (TJ-2322) put each broker's cash-events reader in
        # lib/cash_readers, beside the fx-cash stage; PLAN step 16 commits 7
        # to 11 moved each into its broker's parse (the parser's
        # `cash_events`), and commit 12 removed the folder. Its done-when:
        # the folder is absent, and no import of it or path to it is left in
        # src/, tests/, packages/ or scripts/ but in a comment or docstring
        # that says where code was and when (it names the step).
        self.assertFalse((SRC / "lib" / _GONE).exists())
        live, undated = [], []
        for root in ("src", "tests", "packages", "scripts"):
            for p in sorted((_paths.REPO / root).rglob("*.py")):
                rel = p.relative_to(_paths.REPO).as_posix()
                found = _gone_mentions(p.read_text(encoding="utf-8"))
                live += [f"{rel}:{n}" for n, dated in found if dated is None]
                undated += [f"{rel}:{n}" for n, dated in found if dated is False]
        self.assertEqual(live, [], "an import of, or path to, the cash readers' folder")
        self.assertEqual(undated, [], "a comment naming the cash readers' folder "
                                      "that does not say when it went (step 16)")
        # Each rule on a made-up module.
        self.assertEqual(_gone_mentions(
            "import taxjson.lib." + _GONE + ".ib\n"
            "from taxjson.lib import " + _GONE + "\n"
            "from .. import " + _GONE + "\n"
            "p = 'src/taxjson/lib/" + _GONE + "/ib.py'\n"
            "q = SRC / 'lib' / '" + _GONE + "'\n"
            "# lib/" + _GONE + " held them until PLAN step 16 commit 11\n"
            "x = 1\n"
            "# lib/" + _GONE + "/ib.py reads the export\n"
            "def f():\n    'Once lib/" + _GONE + "/ib.py read them.'\n"
            "t = '" + _GONE + "'\n"),
            [(1, None), (2, None), (3, None), (4, None), (5, None), (6, True),
             (8, False), (10, False)])

    def test_the_registry_names_the_parsers_that_build_cash_events(self):
        # The registry's `builds_cash` (the ledger's brokers, lib/brokerages/
        # base CASH_EVENT_BROKERS) is the parsers whose finish builds the
        # file's cash events (`_cash_events_from`) or sets them.
        from taxjson.parsers.registry import SPECS, cash_event_brokers
        modules = dict(_modules())
        builds = set()
        for s in SPECS:
            tree = modules[s.module[len("taxjson."):].replace(".", "/") + ".py"]
            if _calls(tree, "_cash_events_from") or any(
                    isinstance(t, ast.Attribute) and t.attr == "cash_events"
                    for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets):
                builds.add(s.id)
        self.assertEqual(cash_event_brokers(), builds)
        self.assertEqual(builds, {"ib", "questrade", "rbc_direct", "coinbase", "kraken"})
        from taxjson.lib.brokerages.base import CASH_EVENT_BROKERS
        self.assertEqual(CASH_EVENT_BROKERS, builds)

if __name__ == "__main__":
    unittest.main()
