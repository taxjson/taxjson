"""The `--json` contract (src/taxjson/lib/json_contract.py): every `--json`
output is one JSON object with a top-level integer `schema_version`, the
number the registry gives it (docs/settings.md, "`--json` outputs").

1. Every `--json` has an entry: each `tjs` command and taxjson-* tool that
   takes `--json` has a SCHEMAS id (a tool printing its command's document,
   through TOOL_OUTPUTS), and every id names a live command or tool and its
   mode's options.
2. Every `_json_out` and `versioned` call names its output, under any
   name it is imported as: `schema=` (or versioned's second argument) is
   an id SCHEMAS knows. And every json.dump / json.dumps in
   src/taxjson/cli/ and bin/ is in a function that makes such a call, or
   in NOT_JSON_OUTPUT with why (a file it writes, a stage's book, a key):
   a `--json` written straight with json.dumps fails.
3. Every recorded `--json` view is versioned: each golden book's
   `_views/*.stdout.json` is an object whose `schema_version` is the
   registry's number for the view's output (the id whose words all appear
   in its argv, `YEAR` a 4-digit year other than the view's project's
   own and `a/b` either word; the longest).
4. Every output is held: a recorded view (any exit code whose stdout
   parsed) on each country json_contract.countries lists, or a test that
   pins its number (UNHELD).
5. The docs say it: settings.md's "`--json` outputs" table lists every id
   with its number.

No output is exempt from 1, 2, 3 or 5. From 4 only: UNHELD, the outputs
a test pins instead of a view, and PENDING_HOLD, those no view holds yet
on a country, each with why. An id still in PENDING_HOLD that a view now
holds fails, so it leaves the list with the change that records the view.
"""
import ast
import copy
import json
import re
import tempfile
import tomllib
import unittest
from functools import lru_cache

import _golden
import _paths
from _support.cli_command_runs import _tjs
from _support.corp_elect_project import _project

from taxjson.lib import json_contract as J

SRC = _paths.SRC / "taxjson"
SETTINGS = _paths.REPO / "docs" / "settings.md"

# The outputs no golden view holds yet on every country, each with why.
PENDING_HOLD: dict = {
    "plot-tax": "held on Canada only: style-usa's document holds a figure the private figure "
                "list refuses (scripts/check-pii.sh), with every option; moving a price in its "
                "inputs re-records the book's other files, which C0 keeps byte-equal (TJ-4920)",
}

# Held by a test instead of a golden view: {id: (why, the test module that
# pins its number, `SCHEMAS["<id>"]`)}.
UNHELD = {
    "fetch --list": ("lists the installed fetcher plugins: CI's suite runs without taxjson-fetch",
                     "tests/test_fetch_dispatch.py"),
    "fetch": ("downloads (refused offline)", "tests/test_fetch_dispatch.py"),
    "channels": ("reads the git checkout and its remote", "tests/features/install/test_release_channels.py"),
    "watch": ("its as_of is today, and it writes its state", "tests/test_watch.py"),
    "buy-check": ("answers for today (no --date)",
                  "tests/features/planning/test_buy_check_and_sell_check_of_an_option_level_alias.py"),
    "sell-check": ("answers for today (no --date)",
                   "tests/features/planning/test_buy_check_and_sell_check_of_an_option_level_alias.py"),
    "harvest": ("today's radar and prices", "tests/test_harvest.py"),
    "technicals": ("Yahoo Finance's daily closes, or a history cache no golden book holds",
                   "tests/features/totals/test_technicals_command.py"),
    "tax-logic": ("the spec itself, hundreds of KB a country, moved by every rule's edit",
                  "tests/test_tax_logic.py"),
    "slip-audit --import-cra": ("needs pdftotext (poppler-utils), a program taxjson does not ship",
                                "tests/test_slip_audit_cra.py"),
    "taxjson-audit": ("the per-account document `tjs audit` merges into its own (held there)",
                      "tests/test_audit_command.py"),
    "clean": ("removes the files a golden book compares, and names the project's folders",
              "tests/features/run/test_clean_removes_what_a_run_wrote.py"),
}

# The json.dump / json.dumps calls in src/taxjson/cli/ and bin/ that write
# no `--json` document: {(file, the function holding them): what they
# write}. Every other one is in a function that names its output.
NOT_JSON_OUTPUT = {
    ("src/taxjson/cli/file.py", "cmd_t1135"): "the cash events handed to taxjson-t1135, in a temp file",
    ("src/taxjson/cli/file.py", "_thaw_partial_freeze"): "a close-year lock, filed/<year>.json",
    ("src/taxjson/cli/migrate.py", "_record_migrate_edits"): "a close-year lock, filed/<year>.json",
    ("src/taxjson/cli/unclose.py", "_apply"): "the record of a reopening, unclosed.json in the backup folder",
    ("src/taxjson/bin/fill_crypto_prices.py", "_fill"): "a stage's book on stdout (the run writes it to work/)",
    ("src/taxjson/bin/taxjson_apply_distributions.py", "main"): "the books file it rewrites",
    ("src/taxjson/bin/taxjson_brokerage.py", "_dedup_evidence"): "a row's identity key, never written",
    ("src/taxjson/bin/taxjson_brokerage.py", "_write_cash_out"): "the cash events, work/<account>_<broker>.cash",
    ("src/taxjson/bin/taxjson_brokerage.py", "_brokerage_write"):
        "a stage's book on stdout, the rows it kept aside and its override log",
    ("src/taxjson/bin/taxjson_convert_currency.py", "main"): "a stage's book on stdout",
    ("src/taxjson/bin/taxjson_convert_tt.py", "_main"): "a .tt file's book, to --output or stdout",
    ("src/taxjson/bin/taxjson_corp_actions.py", "_log_saved"): "the saved elections' log, a line each",
    ("src/taxjson/bin/taxjson_corp_actions.py", "main"): "a stage's book on stdout",
    ("src/taxjson/bin/taxjson_corp_actions.py", "_corp_actions_refuse"):
        "the pending elections file (--pending-json), work/<account>_pending_elections.json",
    ("src/taxjson/bin/taxjson_diff.py", "make_key"): "a row's comparison key, never written",
    ("src/taxjson/bin/taxjson_diff.py", "main.sort_key"): "a row's sort key, never written",
    ("src/taxjson/bin/taxjson_filed.py", "write_snapshot"): "a close-year lock, filed/<year>.json",
    ("src/taxjson/bin/taxjson_filed.py", "_recompute_blended"): "the books handed to the engine, in a temp file",
    ("src/taxjson/bin/taxjson_gains.py", "_main"): "the cost timeline (--cost-timeline)",
    ("src/taxjson/bin/taxjson_merge.py", "main"): "a stage's book on stdout",
    ("src/taxjson/bin/taxjson_merge2.py", "main"): "a stage's book on stdout",
    ("src/taxjson/bin/taxjson_redact.py", "redact_record_paths"): "a redacted value in the copy of a JSON file",
    ("src/taxjson/bin/taxjson_sort.py", "main"): "a stage's book on stdout",
    ("src/taxjson/bin/taxjson_ticker_map.py", "main"): "a stage's book on stdout",
    ("src/taxjson/bin/taxjson_wash_radar.py", "_output"):
        "the --json-out file, reports/wash_radar_<account>.json (its bytes kept); the --json beside it prints "
        "_payload's document, which versioned() numbers",
    ("src/taxjson/bin/taxjson_watch.py", "save_state"): "watch's state file",
    ("src/taxjson/bin/to_base_curr.py", "save_cache"): "the rate cache's text, kept to tell a change",
    ("src/taxjson/bin/to_base_curr.py", "build_rates"): "the rate cache's text, compared before and after",
}

_YEAR = re.compile(r"\d{4}\Z")


@lru_cache(maxsize=None)
def _json_commands() -> dict:
    """{command: its option strings} of each `tjs` command taking --json."""
    import argparse
    from taxjson.cli import parser as P
    _p, sub = P._build_parser()
    out = {}

    def walk(name, sp):
        opts = {s for a in sp._actions for s in a.option_strings}
        if "--json" in opts:
            out[name] = opts
        for a in sp._actions:
            if isinstance(a, argparse._SubParsersAction):
                for n, child in a.choices.items():
                    walk(f"{name} {n}", child)
    for name, sp in sub.choices.items():
        walk(name, sp)
    return out


@lru_cache(maxsize=None)
def _json_tools() -> frozenset:
    """The taxjson-* tools whose parser takes --json (an AST read of bin/)."""
    from taxjson.bin._entry import console_prog
    out = set()
    for p in sorted((SRC / "bin").glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "add_argument"
               and any(isinstance(a, ast.Constant) and a.value == "--json" for a in n.args)
               for n in ast.walk(tree)):
            out.add(console_prog(p.stem))
    return frozenset(out)


# The two calls that name an output, and the argument that names it.
_NAMERS = {"_json_out": "schema", "versioned": "output"}


def _aliases(tree) -> dict:
    """{a name a module calls: "_json_out" or "versioned"}: the two names,
    and each name an import binds one of them to (`import ... as v`)."""
    out = {n: n for n in _NAMERS}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            out.update({a.asname: a.name for a in n.names if a.name in _NAMERS and a.asname})
    return out


def _namer(n, aliases):
    """("_json_out" or "versioned", the node naming its output or None) of
    a call to either under any name (`J.versioned` too); else None."""
    if not isinstance(n, ast.Call):
        return None
    f = n.func
    fn = aliases.get(f.id) if isinstance(f, ast.Name) else (
        f.attr if isinstance(f, ast.Attribute) and f.attr in _NAMERS else None)
    if fn is None:
        return None
    return fn, next((k.value for k in n.keywords if k.arg == _NAMERS[fn]), n.args[1] if len(n.args) > 1 else None)


def _calls():
    """(file, line, function name, the output it names or None, named at
    all) of every _json_out and versioned call in src/taxjson, under any
    name it is imported as; the call in _json_out itself (it passes its
    own argument on) left out."""
    out = []
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(_paths.REPO).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"))
        aliases = _aliases(tree)
        inside = {id(c) for f in ast.walk(tree)
                  if isinstance(f, ast.FunctionDef) and f.name == "_json_out" for c in ast.walk(f)}
        for n in ast.walk(tree):
            hit = None if id(n) in inside else _namer(n, aliases)
            if hit is None:
                continue
            fn, arg = hit
            named = arg.value if isinstance(arg, ast.Constant) else None
            out.append((rel, n.lineno, fn, named, arg is not None))
    return out


def _dump_sites():
    """({(file, function): [line, ...]} of the json.dump / json.dumps calls
    in src/taxjson/cli/ and bin/, under any name json or the function is
    imported as, by the innermost function holding each (`Class.method`,
    `outer.inner`, `<module>`); the set of those functions that name an
    output: a `_json_out` / `versioned` call there with a literal id)."""
    sites, naming = {}, set()
    for d in ("cli", "bin"):
        for p in sorted((SRC / d).rglob("*.py")):
            rel = p.relative_to(_paths.REPO).as_posix()
            tree = ast.parse(p.read_text(encoding="utf-8"))
            aliases = _aliases(tree)
            mods = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                    for a in n.names if a.name == "json"}
            funcs = {a.asname or a.name for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom) and n.module == "json"
                     for a in n.names if a.name in ("dump", "dumps")}

            def visit(node, where):
                for ch in ast.iter_child_nodes(node):
                    inner = where
                    if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        inner = ch.name if where == "<module>" else f"{where}.{ch.name}"
                    elif isinstance(ch, ast.Call):
                        f = ch.func
                        if (isinstance(f, ast.Attribute) and f.attr in ("dump", "dumps")
                                and isinstance(f.value, ast.Name) and f.value.id in mods) or (
                                isinstance(f, ast.Name) and f.id in funcs):
                            sites.setdefault((rel, where), []).append(ch.lineno)
                        hit = _namer(ch, aliases)
                        if hit is not None and isinstance(hit[1], ast.Constant):
                            naming.add((rel, where))
                    visit(ch, inner)
            visit(tree, "<module>")
    return sites, naming


def _words_match(output: str, words: list, year=None) -> bool:
    """Whether every word of the id `output` is among `words` (the first
    one first): `YEAR` a 4-digit year other than `year` (the project's
    own: `tjs amt 2024` in the 2024 project prints `amt`'s document),
    `a/b` either word."""
    want = output.split()
    if not words or want[0] != words[0]:
        return False
    rest = words[1:]
    other = [x for x in rest if _YEAR.match(x) and x != str(year)]
    for w in want[1:]:
        alts = w.split("/") if w.startswith("--") and "/" in w else [w]
        if not any((a == "YEAR" and other) or a in rest for a in alts):
            return False
    return True


def output_of(words: list, year=None):
    """The SCHEMAS id a view's words print in a project of `year` (the
    longest that matches), or None."""
    hits = [o for o in J.SCHEMAS if _words_match(o, words, year)]
    return max(hits, key=lambda o: len(o.split()), default=None)


def _view_year(book: str, cwd: str):
    """The year of the project a view of `book` runs in: its taxjson.toml's
    `year`, or the folder's name for a year folder a view makes (`tjs
    new-year`); None when neither tells."""
    m = _golden.meta(book)
    folder = (_golden.GOLDEN / book / "project" / m.get("run_dir", ".") / cwd).resolve()
    toml = folder / "taxjson.toml"
    if toml.is_file():
        return tomllib.loads(toml.read_text(encoding="utf-8")).get("settings", {}).get("year")
    return int(folder.name) if _YEAR.match(folder.name) else None


def _book_country(book: str) -> str:
    m = _golden.meta(book)
    toml = _golden.GOLDEN / book / "project" / m.get("run_dir", ".") / "taxjson.toml"
    c = tomllib.loads(toml.read_text(encoding="utf-8"))["settings"]["country"].lower()
    return {"ca": "canada", "us": "usa"}.get(c, c)


@lru_cache(maxsize=None)
def recorded_views() -> tuple:
    """(book, country, view name, words, exit, its JSON or None, the
    output it prints) of every recorded --json view of the golden books:
    None when its stdout did not parse (recorded as .stdout.txt)."""
    out = []
    for book in _golden.books():
        index = _golden.GOLDEN / book / "expected" / "_views" / "index.json"
        if _golden.meta(book)["kind"] != "project" or not index.is_file():
            continue
        country = _book_country(book)
        for i in json.loads(index.read_text(encoding="utf-8")):
            argv = i["view"].split()
            if "--json" not in argv:
                continue
            view = {"argv": i["view"], "cwd": i["cwd"], "width": i.get("width", 0), "tool": i.get("tool")}
            name = _golden.view_name(view)
            words = _golden.view_argv(view)
            js = _golden.GOLDEN / book / "expected" / "_views" / f"{name}.stdout.json"
            doc = json.loads(js.read_text(encoding="utf-8")) if js.is_file() else None
            out.append((book, country, name, words, i["exit"], doc, output_of(words, _view_year(book, i["cwd"]))))
    return tuple(out)


def _held() -> dict:
    """{id: the countries a recorded view holds it on}."""
    out: dict = {}
    for _book, country, _name, _words, _exit, doc, o in recorded_views():
        if o is not None and doc is not None:
            out.setdefault(o, set()).add(country)
    return out


class TestEveryJsonHasAnEntry(unittest.TestCase):
    def test_each_command_and_tool(self):
        firsts = {o.split()[0] for o in J.SCHEMAS}
        missing = sorted(c for c in _json_commands() if c not in firsts)
        self.assertEqual(missing, [], "a --json without a json_contract.SCHEMAS entry")
        for tool in sorted(_json_tools()):
            with self.subTest(tool=tool):
                own, prints = tool in firsts, J.TOOL_OUTPUTS.get(tool)
                self.assertTrue(own != (prints is not None),
                                f"{tool}: an id of its own in SCHEMAS, or the command's it prints in TOOL_OUTPUTS")
                if prints is not None:
                    self.assertIn(prints, J.SCHEMAS)

    def test_each_id_names_a_live_command_or_tool(self):
        cmds, tools = _json_commands(), _json_tools()
        for o in J.SCHEMAS:
            first, *rest = o.split()
            with self.subTest(output=o):
                self.assertTrue(first in cmds or first in tools, f"{o}: no command or tool {first!r} takes --json")
                for w in rest:
                    if w.startswith("--") and first in cmds:
                        for alt in w.split("/"):
                            self.assertIn(alt, cmds[first], f"{o}: `tjs {first}` has no {alt}")
        self.assertEqual(sorted(set(J.TOOL_OUTPUTS) - tools), [], "TOOL_OUTPUTS names a tool without --json")
        self.assertEqual(sorted(set(J.COUNTRIES) - set(J.SCHEMAS)), [])

    def test_the_numbers(self):
        for o, n in J.SCHEMAS.items():
            self.assertIs(type(n), int, o)
            self.assertGreaterEqual(n, 1, o)


class TestEveryCallNamesItsOutput(unittest.TestCase):
    def test_a_named_output_is_one_the_registry_knows(self):
        bad = [f"{f}:{line}: {fn}(..., {named!r})" for f, line, fn, named, given in _calls()
               if given and named not in J.SCHEMAS]
        self.assertEqual(bad, [], "a schema= that is no json_contract.SCHEMAS id (a string literal)")

    def test_every_call_names_its_output(self):
        calls = _calls()
        self.assertTrue(any(fn == "_json_out" for _f, _l, fn, _n, _g in calls), "no _json_out call found")
        bad = [f"{f}:{line}: {fn}(doc) names no output" for f, line, fn, _named, given in calls if not given]
        self.assertEqual(bad, [], "name the output: _json_out(doc, schema=ID), versioned(doc, ID)")

    def test_an_imported_name_is_counted(self):
        tree = ast.parse("from taxjson.lib.json_contract import versioned as v\n"
                         "from taxjson.pipeline.sanity import _json_out as out\n"
                         "v(d, 'x')\nout(d)\nJ.versioned(d)\n")
        hits = [_namer(n, _aliases(tree)) for n in ast.walk(tree)]
        self.assertEqual([(fn, getattr(a, "value", a)) for fn, a in filter(None, hits)],
                         [("versioned", "x"), ("_json_out", None), ("versioned", None)])

    def test_each_json_dump_names_its_output_or_says_what_it_writes(self):
        sites, naming = _dump_sites()
        self.assertIn(("src/taxjson/cli/tips.py", "cmd_tips"), naming)
        bad = sorted(f"{f}:{lines[0]}: {fn}" for (f, fn), lines in sites.items()
                     if (f, fn) not in naming and (f, fn) not in NOT_JSON_OUTPUT)
        self.assertEqual(bad, [], "a json.dump(s) in a function that names no output: print a --json document "
                                  "through versioned(doc, ID) or _json_out(doc, schema=ID); a file it writes "
                                  "goes in NOT_JSON_OUTPUT with what it is")

    def test_not_json_output_is_true(self):
        sites, naming = _dump_sites()
        for key, why in sorted(NOT_JSON_OUTPUT.items()):
            with self.subTest(site=key):
                self.assertTrue(isinstance(why, str) and why)
                self.assertIn(key, sites, f"{key}: no json.dump(s) there now: remove it from NOT_JSON_OUTPUT")
                self.assertNotIn(key, naming, f"{key} names its output: remove it from NOT_JSON_OUTPUT")


class TestEveryRecordedViewIsVersioned(unittest.TestCase):
    def test_each_view(self):
        self.assertTrue(recorded_views())
        for book, _c, name, _words, code, doc, o in recorded_views():
            with self.subTest(book=book, view=name):
                self.assertIsNotNone(o, f"{book} {name}: no json_contract.SCHEMAS id prints it")
                if doc is None:
                    # Nothing parsed: a refusal prints nothing on stdout.
                    self.assertNotEqual(code, 0)
                    text = (_golden.GOLDEN / book / "expected" / "_views" / f"{name}.stdout.txt").read_text(
                        encoding="utf-8")
                    self.assertEqual(text, "", f"{book} {name}: --json printed text that is not JSON")
                    continue
                self.assertIsInstance(doc, dict, f"{book} {name}: a JSON object at the top")
                self.assertIs(type(doc.get("schema_version")), int,
                              f"{book} {name}: no integer schema_version at the top")
                self.assertEqual(doc["schema_version"], J.SCHEMAS[o], f"{book} {name}")

    def test_how_a_view_finds_its_output(self):
        self.assertEqual(output_of(["fx-cash", "--ledger", "v2", "--events", "--json"]), "fx-cash --ledger v2")
        self.assertEqual(output_of(["fx-cash", "--json"]), "fx-cash")
        self.assertEqual(output_of(["amt", "2024", "--json"]), "amt YEAR")
        self.assertEqual(output_of(["amt", "2024", "--json"], year=2025), "amt YEAR")
        self.assertEqual(output_of(["amt", "2024", "--json"], year=2024), "amt")
        self.assertEqual(_view_year("demo", "../2025"), 2025)
        self.assertEqual(_view_year("demo", "."), 2024)
        self.assertEqual(output_of(["amt", "--json"]), "amt")
        self.assertEqual(output_of(["compare", "--list", "--json"]), "compare --list")
        self.assertEqual(output_of(["checklist", "--reset", "--json"]), "checklist --done/--undo/--reset")
        self.assertEqual(output_of(["years", "--diff", "2024", "2025", "--json"]), "years --diff")
        self.assertEqual(output_of(["taxjson-sum-gains", "--json", "x.json"]), "taxjson-sum-gains")
        self.assertIsNone(output_of(["no-such", "--json"]))


class TestEveryOutputIsHeld(unittest.TestCase):
    def test_a_view_or_a_test(self):
        held = _held()
        for o in J.SCHEMAS:
            want = set(J.countries(o))
            with self.subTest(output=o):
                if o in UNHELD:
                    continue
                if o in PENDING_HOLD:
                    self.assertFalse(want <= held.get(o, set()), f"{o} is held now: remove it from PENDING_HOLD")
                    continue
                self.assertEqual(sorted(want - held.get(o, set())), [],
                                 f"{o}: no golden view holds it on these countries (tests/golden/*/book.json "
                                 f"`views`)")

    def test_the_lists(self):
        for name, ids in (("PENDING_HOLD", PENDING_HOLD), ("UNHELD", UNHELD)):
            self.assertEqual(sorted(set(ids) - set(J.SCHEMAS)), [], f"{name} names an id SCHEMAS does not")
        self.assertEqual(sorted(set(UNHELD) & set(PENDING_HOLD)), [])
        for o, why in PENDING_HOLD.items():
            self.assertTrue(isinstance(why, str) and why, o)

    def test_a_test_pins_each_unheld_output(self):
        for o, (why, module) in sorted(UNHELD.items()):
            with self.subTest(output=o):
                self.assertTrue(why)
                path = _paths.REPO / module
                self.assertTrue(path.is_file(), f"{o}: no {module}")
                text = path.read_text(encoding="utf-8")
                first = o.split()[0]
                self.assertTrue(first in text or first.replace("-", "_") in text, f"{module} never names {first}")
                pin = f'SCHEMAS["{o}"]'
                self.assertTrue("schema_version" in text and pin in text,
                                f"{module}: assert the document's schema_version is json_contract.{pin}")


class TestElectPendingFromItsFile(unittest.TestCase):
    """`tjs elect --pending --json` beside a work/pending_elections.json
    (the golden view holds the branch without one): the file's content
    under the registry's number, whatever number the file holds for its
    own format, or none. An account's entry is the file's as it is."""

    PENDING = {"accounts": {"margin": {"schema_version": 1, "country": "canada", "pending": [
        {"event_id": "20251022-zza-zzb-51d7", "summary": "2025-10-22 merger: ZZA.TO to ZZB.US",
         "options": [{"election": "taxable_disposition", "description": "sale now", "hints": []}]}]}}}

    def test_the_file_under_the_registry_s_number(self):
        for own in (None, 7):
            with self.subTest(file_schema_version=own), tempfile.TemporaryDirectory() as d:
                pending = copy.deepcopy(self.PENDING)
                r = _tjs(_project(d, pending=pending if own is None else {"schema_version": own, **pending}),
                         "elect", "--pending", "--json")
                self.assertEqual(r.returncode, 0, r.stderr)
                doc = json.loads(r.stdout)
                self.assertEqual(doc.pop("schema_version"), J.SCHEMAS["elect --pending"])
                self.assertEqual(doc, self.PENDING)


class TestTheDocsSayIt(unittest.TestCase):
    def test_settings_md_lists_every_output(self):
        text = SETTINGS.read_text(encoding="utf-8")
        i = text.find("\n## `--json` outputs\n")
        self.assertGreaterEqual(i, 0, "docs/settings.md: no `## `--json` outputs` section")
        section = text[i + 1:]
        stop = re.search(r"^## ", section[3:], re.M)
        section = section[:stop.start() + 3] if stop else section
        rows = re.findall(r"^\| `([^`]+)` \| (\d+) \|", section, re.M)
        listed = {o: int(n) for o, n in rows}
        self.assertEqual(len(rows), len(listed), "an output listed twice")
        self.assertEqual(listed, J.SCHEMAS)


if __name__ == "__main__":
    unittest.main()
