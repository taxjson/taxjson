"""IB's parse state (5h (8a), TJ-2428): the methods kept after the parse
(ca_undo, cil_join, the deferred side-effect lines) read the statement's
_IbParse, which `read` sets up, so a field read through a local of its
name would split the two silently. The section methods (5h (8b)) read the
rows' shared state on `_IbRows`, which owns its containers: read's own code
reads them as `rs.<field>` too, so each has one name (TJ-2449). The end of
the parse is `_ib_finish` and its parts (5h (8c)), held to the same rule,
run from the template's finish (5h (8d)). What the account's
cross-statement pass reads is the statement's `_IbParse` too (TJ-2429)."""

import ast
import contextlib
import dataclasses
import inspect
import io
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from taxjson.lib.brokerages.ib_extractor import IbBrokerage, _IbParse, _IbRows
from taxjson.parsers.protocol import Outcome
from _support.ib_statement_books import _booked, _stmt
from _support.ib_statement_rows import CA_H, TRADES_H, _ca, _trade


class TestIbParseState(unittest.TestCase):
    def test_each_field_is_bound_once_as_an_alias(self):
        # read binds no field's name but its parameter (its one alias,
        # `transactions`, went with parse_file: the template's list is the
        # state's, `new_transactions`).
        tree = ast.parse(textwrap.dedent(inspect.getsource(IbBrokerage.read)))
        fields = {f.name for f in dataclasses.fields(_IbParse)}
        binds = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.arg)):
                name = node.id if isinstance(node, ast.Name) else node.arg
                if name in fields and (isinstance(node, ast.arg)
                                       or isinstance(node.ctx, (ast.Store, ast.Del))):
                    binds.setdefault(name, []).append(node)
        self.assertEqual([n.arg for n in binds.pop("path")], ["path"])
        for name, nodes in binds.items():
            with self.subTest(field=name):
                self.assertEqual(len(nodes), 1, f"{name} bound {len(nodes)} times")
        aliases = {node.targets[0].id for node in ast.walk(tree)
                   if isinstance(node, ast.Assign) and len(node.targets) == 1
                   and isinstance(node.targets[0], ast.Name)
                   and isinstance(node.value, ast.Attribute)
                   and isinstance(node.value.value, ast.Name) and node.value.value.id == "st"
                   and node.value.attr == node.targets[0].id}
        self.assertEqual(aliases, set(binds))
        # The other ways to split an alias from the state (TJ-2440): a
        # container field assigned on the state, the state built or `st`
        # bound twice, an exception bound to a field's name. A value field
        # (`period_start`, a default, not a factory) is set, never aliased.
        values = {f.name for f in dataclasses.fields(_IbParse)
                  if f.default is not dataclasses.MISSING}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "st" and isinstance(node.ctx, (ast.Store, ast.Del))
                    and node.attr not in values):
                self.fail(f"st.{node.attr} assigned at line {node.lineno}")
            if isinstance(node, ast.ExceptHandler):
                self.assertNotIn(node.name, fields)
        st_binds = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and n.id == "st" and isinstance(n.ctx, ast.Store)]
        ib_binds = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Attribute) and n.attr == "_ib"
                    and isinstance(n.ctx, ast.Store)]
        self.assertEqual((len(st_binds), len(ib_binds)), (1, 1))


def _tree(fn):
    return ast.parse(textwrap.dedent(inspect.getsource(fn)))


def _required(holder):
    return [f.name for f in dataclasses.fields(holder)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING]


def _state_methods():
    """The section methods, the finish (`_ib_finish`) and the class's own methods
    they call, transitively, that read the shared state (an `rs` parameter or
    `self._ib`): the helpers a section's or the tail's blocks moved to (the
    150-line ratchet)."""
    own = {name: fn for name, fn in vars(IbBrokerage).items() if inspect.isfunction(fn)}
    todo = [name for name in own if name.startswith("_ib_row_")] + ["_ib_finish"]
    seen = {}
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen[name] = own[name]
        for n in ast.walk(_tree(own[name])):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "self" and n.attr in own):
                todo.append(n.attr)
    return {name: fn for name, fn in seen.items()
            if "rs" in inspect.signature(fn).parameters
            or any(isinstance(n, ast.Attribute) and n.attr == "_ib"
                   for n in ast.walk(_tree(fn)))}


class TestTheRowsHolder(unittest.TestCase):
    def test_the_holder_is_built_once_and_its_containers_have_one_name(self):
        tree = _tree(IbBrokerage.read)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "_IbRows"]
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call.args, [])
        for k in call.keywords:
            with self.subTest(field=k.arg):
                self.assertIsInstance(k.value, ast.Name)
                self.assertEqual(k.value.id, k.arg)
        passed = {k.arg for k in call.keywords}
        self.assertEqual(passed, set(_required(_IbRows)))
        # A local passed to the holder and rebound after it would split.
        late = [(n.id, n.lineno) for n in ast.walk(tree)
                if isinstance(n, ast.Name) and n.id in passed
                and isinstance(n.ctx, (ast.Store, ast.Del)) and n.lineno > call.lineno]
        self.assertEqual(late, [])
        # The containers and the values a row leaves (the defaulted fields)
        # live on the holder alone: read reads them as `rs.<field>`,
        # never through a local of the same name.
        left = {f.name for f in dataclasses.fields(_IbRows)} - passed
        self.assertEqual({n.id for n in ast.walk(tree)
                          if isinstance(n, ast.Name) and n.id in left}, set())

    def test_a_section_method_binds_no_shared_name(self):
        shared = ({f.name for f in dataclasses.fields(_IbRows)}
                  | {f.name for f in dataclasses.fields(_IbParse)} | {"rs"})
        settable = {f.name for f in dataclasses.fields(_IbRows)} - set(_required(_IbRows))
        methods = _state_methods()
        self.assertGreaterEqual(len(methods), 30)
        self.assertTrue({"_ib_finish", "_ib_finish_refunds"} <= set(methods))
        for name, fn in methods.items():
            with self.subTest(method=name):
                for n in ast.walk(_tree(fn)):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                        self.assertNotIn(n.id, shared, f"line {n.lineno}")
                    if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                            and isinstance(n.ctx, (ast.Store, ast.Del))):
                        self.assertNotEqual(n.value.id, "st", f"st.{n.attr} set")
                        if n.value.id == "rs":
                            self.assertIn(n.attr, settable, f"rs.{n.attr} set")


class TestTheFinish(unittest.TestCase):
    """The end of the parse is one call, `_ib_finish`, in the template's finish
    before its return; its parts run from it alone, each once, in its order
    (5h (8c), (8d))."""

    def test_the_tail_is_the_finish(self):
        body = _tree(IbBrokerage.finish).body[0].body
        calls = [i for i, n in enumerate(body)
                 if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Attribute)
                 and n.value.func.attr == "_ib_finish"]
        self.assertEqual(calls, [len(body) - 2])
        self.assertIsInstance(body[-1], ast.Return)

    def test_each_part_runs_from_the_finish_once(self):
        parts = sorted(n for n in vars(IbBrokerage) if n.startswith("_ib_finish_"))
        self.assertGreaterEqual(len(parts), 8)
        source = Path(inspect.getsourcefile(IbBrokerage)).read_text(encoding="utf-8")
        finish = _tree(IbBrokerage._ib_finish)
        for name in parts:
            with self.subTest(part=name):
                here = [n for n in ast.walk(finish) if isinstance(n, ast.Attribute)
                        and n.attr == name]
                self.assertEqual(len(here), 1)
                self.assertEqual(source.count(f".{name}("), 1)


class TestTheRowDispatch(unittest.TestCase):
    """The row loop finds a section's method on the parser (TJ-2457): an
    override or a test's patch is the method that runs."""

    def test_a_patched_row_method_is_the_one_called(self):
        stmt = _stmt('January 1, 2025', 'December 31, 2025', TRADES_H,
                     _trade('QZT', '2025-01-10, 10:00:00', 100, 30, -3000))
        for section in sorted(IbBrokerage._ROW_METHODS):
            with self.subTest(section=section):
                self.assertTrue(callable(getattr(IbBrokerage, IbBrokerage._ROW_METHODS[section])))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.csv"
            path.write_text(stmt, encoding="utf-8")
            with mock.patch.object(IbBrokerage, "_ib_row_trades", autospec=True,
                                   side_effect=lambda self, *a: Outcome.consumed()) as m, \
                    contextlib.redirect_stderr(io.StringIO()):
                IbBrokerage().parse_file(path)
        self.assertEqual(m.call_count, 1)


def _ends(stmts) -> bool:
    """A block that cannot fall off its end: a value returned or a raise last,
    through if/else and try."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, ast.Return):
        return last.value is not None
    if isinstance(last, ast.Raise):
        return True
    if isinstance(last, ast.If):
        return _ends(last.body) and _ends(last.orelse)
    if isinstance(last, ast.Try):
        return ((_ends(last.body) or _ends(last.orelse))
                and all(_ends(h.body) for h in last.handlers))
    return False


class TestTheRowsAnswer(unittest.TestCase):
    """5h (8d): a row's handling answers what the row is (parsers.protocol
    Outcome) and the parse counts the answer, as the template does; a held
    `Ca` row is consumed into the state at its row, and only the moves a
    later pass makes (`recount`) touch the counts after it."""
    ANSWERING = ("_ib_trade_book", "_ib_ca_event", "_ib_ca_tender", "_ib_ca_cash_merger",
                 "_ib_ca_stock_dividend", "_ib_trade_other_category", "_ib_trade_level")
    RECOUNTS = {"resolve_unmatched_ca", "_ib_ca_apply_undo", "_ib_finish_unmatched_ca"}

    def _methods(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(IbBrokerage)))
        return {f.name: f for f in tree.body[0].body if isinstance(f, ast.FunctionDef)}

    def test_every_path_of_a_row_method_answers(self):
        fns = self._methods()
        for name in sorted(set(IbBrokerage._ROW_METHODS.values()) | set(self.ANSWERING)):
            with self.subTest(name=name):
                f = fns[name]
                self.assertTrue(_ends(f.body), "a path falls off the end")
                self.assertEqual([n.lineno for n in ast.walk(f)
                                  if isinstance(n, ast.Return) and n.value is None], [])

    def test_no_row_counts_itself(self):
        fns = self._methods()
        hits = sorted((name, n.func.attr) for name, f in fns.items() for n in ast.walk(f)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                      and n.func.attr in ("count_skip", "count_nonevent", "note_row_consumed",
                                          "recount")
                      and not (n.func.attr == "recount" and name in self.RECOUNTS))
        self.assertEqual(hits, [])


class TestTheRowRecords(unittest.TestCase):
    """TJ-2448 / TJ-2461: the Trades and Corporate Actions helpers read one
    record of the row (`_IbTrade`, `_IbCa`): no wide keyword lists, no
    closures handed down as arguments, no tuples a mis-ordered unpack would
    swap silently (5h (8d))."""

    def test_the_helpers_take_the_record(self):
        fns = {f.name: f for f in ast.parse(textwrap.dedent(inspect.getsource(IbBrokerage)))
               .body[0].body if isinstance(f, ast.FunctionDef)}
        helpers = sorted(n for n in fns if n.startswith(("_ib_trade_", "_ib_ca_"))
                         and n not in ("_ib_ca_record", "_ib_ca_apply_undo", "_ib_ca_settle"))
        self.assertGreaterEqual(len(helpers), 13)
        for name in helpers:
            with self.subTest(helper=name):
                f = fns[name]
                self.assertEqual(f.args.kwonlyargs, [])
                self.assertLessEqual(len(f.args.args), 3)
                self.assertEqual([n.lineno for n in ast.walk(f)
                                  if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)], [])
        for name in ("_ib_row_trades", "_ib_row_corporate_actions"):
            with self.subTest(row=name):
                nested = [n.name for n in ast.walk(fns[name])
                          if isinstance(n, ast.FunctionDef) and n is not fns[name]]
                self.assertTrue(set(nested) <= {"_num"}, nested)


SD = ('QZT(US9990000501) Stock Dividend US9990000501 1 for 10 '
      '(QZT, QZT CORP, US9990000501)')
CT = 'QZT(US9990000501) Merged(Acquisition) FOR USD 30.00 PER SHARE'


class TestTheDeferredFlushIsItsStatements(unittest.TestCase):
    """The side-effect lines a deferring parse leaves print its own
    statement's lines, whatever the parser parses after it (TJ-2438). The
    kept methods' cross-statement work (a Ca undoing another statement's
    event) is held by features.parsers.test_undone_corporate_event_notes_silenced's A2-1091 and features.parsers.test_ib_statement_sections's
    S059-04 tests, through taxjson-brokerage."""

    def test_a_flush_taken_before_a_second_parse_prints_its_own_lines(self):
        buy = TRADES_H + _trade('QZT', '2025-01-10, 10:00:00', 100, 30, -3000)
        a = _stmt('January 1, 2025', 'December 31, 2025', buy, CA_H,
                  _ca(SD, 10, value=300, when='2025-06-30, 20:25:00'))
        b = _stmt('January 1, 2026', 'March 31, 2026', CA_H,
                  _ca(CT, -100, proceeds=3000, when='2026-01-05, 20:25:00'))
        parser = IbBrokerage()
        parser.defer_ca_messages = True
        with tempfile.TemporaryDirectory() as td:
            pa, pb = Path(td) / "a.csv", Path(td) / "b.csv"
            pa.write_text(a, encoding="utf-8")
            pb.write_text(b, encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_file(pa)
                flush_a = parser.ca_side_effects
                parser.parse_file(pb)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                flush_a()
        self.assertIn("stock dividend of 10", err.getvalue())
        self.assertNotIn("cash takeover booked as a sale", err.getvalue())


class TestTheSkipSummaryWaitsForTheAccountsPass(unittest.TestCase):
    """Under an account context the statement's skip summary (the template's,
    `emit_skip_summary`) waits for the cross-statement pass: a `Ca` row whose
    original another statement holds is paired there, no skip (5h (8d))."""

    def test_a_ca_another_statement_pairs_is_not_a_skip(self):
        buy = TRADES_H + _trade('QZT', '2025-01-10, 10:00:00', 100, 30, -3000)
        a = _stmt('January 1, 2025', 'December 31, 2025', buy, CA_H,
                  _ca(SD, 10, value=300, when='2025-06-30, 20:25:00'))
        b = _stmt('January 1, 2026', 'March 31, 2026', CA_H,
                  _ca(SD, -10, value=-300, when='2025-06-30, 20:25:00', code='Ca'))
        _rows, err = _booked({'a.csv': a, 'b.csv': b})
        self.assertIn("its original in a.csv is undone", err)
        self.assertNotIn("Ca row whose original is not in this statement", err)
        self.assertIn("note: b.csv: 3 recognized non-event row(s)", err)

    def test_the_summary_keeps_the_templates_signature(self):
        # TJ-2484: the deferral is IB's own (`_ib_flush_skip_summary`), not a
        # keyword the template's result member does not have.
        from taxjson.lib.brokerages.base import BaseBrokerage
        self.assertEqual(inspect.signature(IbBrokerage.emit_skip_summary),
                         inspect.signature(BaseBrokerage.emit_skip_summary))


# What `_reconcile_files` reads of each statement (TJ-2429, TJ-2464).
CROSS = ("ca_seen", "ca_pairs", "unmatched_ca", "cil_seen", "unmatched_cil",
         "trade_ca_keys", "trade_pairs", "held_trade_cas", "xfer_ca_keys",
         "xfer_pairs", "held_xfer_cas", "refund_folds", "refund_seen",
         "unmatched_refunds", "trade_meta", "period_start")


class TestWhatCrossesStatements(unittest.TestCase):
    """What the account's cross-statement pass reads lives on each
    statement's `_IbParse` (TJ-2429): one holder, never a parser attribute
    beside it that the parse's end sets."""

    def test_each_is_a_field_of_the_parse_state(self):
        names = {f.name for f in dataclasses.fields(_IbParse)}
        self.assertEqual(sorted(set(CROSS) - names), [])

    def test_the_parser_holds_none_of_its_own(self):
        # Every use is on a statement's state (`st.<field>`, `<parser>._ib.<field>`),
        # and a container field is filled in place, never assigned (TJ-2465).
        tree = ast.parse(Path(inspect.getsourcefile(IbBrokerage)).read_text(encoding="utf-8"))
        values = {f.name for f in dataclasses.fields(_IbParse)
                  if f.default is not dataclasses.MISSING}

        def on_state(v):
            return ((isinstance(v, ast.Name) and v.id == "st")
                    or (isinstance(v, ast.Attribute) and v.attr == "_ib"))
        bad = [(n.lineno, n.attr) for n in ast.walk(tree)
               if isinstance(n, ast.Attribute) and n.attr in CROSS
               and (not on_state(n.value)
                    or (isinstance(n.ctx, (ast.Store, ast.Del)) and n.attr not in values))]
        # A value field is set by read alone, on its own state (TJ-2479).
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "IbBrokerage")
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn is not next(
                    f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name == "read"):
                bad += [(n.lineno, n.attr) for n in ast.walk(fn)
                        if isinstance(n, ast.Attribute) and n.attr in values
                        and isinstance(n.ctx, (ast.Store, ast.Del))]
        bad += [(n.lineno, n.args[1].value) for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "getattr" and len(n.args) > 1
                and isinstance(n.args[1], ast.Constant) and n.args[1].value in CROSS]
        self.assertEqual(bad, [])

    def test_no_other_module_reaches_a_statements_state(self):
        # The state is the IB parser's: another module reaching `._ib.` or
        # `_IbParse` would set what the pass reads from outside (TJ-2479).
        root = Path(inspect.getsourcefile(IbBrokerage)).parents[2]
        here = Path(inspect.getsourcefile(IbBrokerage)).resolve()
        hits = [str(p.relative_to(root)) for p in sorted(root.rglob("*.py"))
                if p.resolve() != here
                and ("._ib." in (text := p.read_text(encoding="utf-8")) or "_IbParse" in text)]
        self.assertEqual(hits, [])

    def test_an_unmatched_cancellation_stays_on_its_statements_state(self):
        text = _stmt('January 1, 2025', 'December 31, 2025', CA_H,
                     _ca(SD, -10, value=-300, when='2025-06-30, 20:25:00', code='Ca'))
        parser = IbBrokerage()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.csv"
            path.write_text(text, encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_file(path)
        self.assertEqual([c["qty"] for c in parser._ib.unmatched_ca], [-10])
        self.assertEqual(len(parser._ib.ca_seen), 1)
        for name in CROSS:
            with self.subTest(name=name):
                self.assertNotIn(name, vars(parser))


if __name__ == "__main__":
    unittest.main()
