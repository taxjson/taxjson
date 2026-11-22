"""Exact money in the gains engines (model/money, steps 6d to 6f) and the
money A/B's classes (tests/_money_oracle).

The engines read each money figure through `M.of`, the exact Decimal of
what float() reads, and run trapping a Decimal ordered against a float
(`M.engine`, `M.trapped`); what they return holds floats (`M.leave`).
Step 6f took the float mode out: exact money is the only one, and a money
site still mixing float in stops the command on one line naming it.
"""
import ast
import contextlib
import decimal
import io
import math
import os
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

import _engine
import _money_oracle as O
from taxjson.engine.ca.pools import (pool_is_flat, _short_account_part, _fold_short_futures_pools,
                                     _ca_pool_state)
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.engine.ca.taxrules import CanadaTaxRules
from taxjson.engine.by_country import get_tax_rules
from taxjson.engine.replacement import replacement_flags
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.dispatch import python_module_argv
from taxjson.model import money as M
from _support.engine_ca_pool_books import pool_of

SRC = Path(M.__file__).resolve().parents[1]


class TestTheReads(unittest.TestCase):
    def test_of_is_the_exact_decimal(self):
        """Outside an engine run nothing is trapped: the code reading what
        the engines return holds floats."""
        self.assertEqual(M.of(0.1), Decimal("0.1"))
        self.assertFalse(decimal.getcontext().traps[decimal.FloatOperation])
        self.assertTrue(Decimal("0.5") < 0.75)

    def test_the_switch_is_gone(self):
        """Step 6f: no money mode, no float fallback, and no source reads
        TAXJSON_MONEY or its strict and fallback variables."""
        for name in ("mode", "set_mode", "money_mode", "MODES", "mode_from_env", "_MODE",
                     "MoneyStop", "falls_back", "user_command", "child_stopped", "FALLBACK_ENV",
                     "STRICT_ENV", "SITE_EXIT"):
            self.assertFalse(hasattr(M, name), name)
        said = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
                if "TAXJSON_MONEY" in p.read_text(encoding="utf-8")]
        self.assertEqual(said, [])

    def test_a_mixed_comparison_stops(self):
        """In the trap a Decimal ordered against a float raises
        (FloatOperation, a TypeError) instead of answering, at the boundary
        differently (TJ-3062, TJ-3063); == stays silent."""
        with M.trapped():
            with self.assertRaises(TypeError):
                Decimal("0.005") < 0.005
            with self.assertRaises(TypeError):
                max(0.0, Decimal("1"))
            with self.assertRaises(TypeError):
                Decimal(0.1)
            self.assertFalse(Decimal("0.5") == 0.25)
            self.assertEqual(decimal.getcontext().rounding, decimal.ROUND_HALF_UP)
            # pool_is_flat reads its bound through M.of: Decimal('0.005'),
            # read from the float's repr.
            self.assertFalse(pool_is_flat("ZZQ", 1e-16, Decimal("0.005")))
        self.assertFalse(pool_is_flat("ZZQ", 1e-16, 0.005))
        self.assertFalse(decimal.getcontext().traps[decimal.FloatOperation])

    def test_the_planner_lets_a_money_type_error_through(self):
        """The US payments-in-lieu planner's silent walk plans nothing on an
        error, but exact money's TypeError is a site not yet converted: it
        is raised (TJ-3064); any other TypeError plans nothing (TJ-3100)."""
        from _support.us_reports import _paid
        from _support.us_short_rows import _short
        rows = [*_short("2025-05-01", "2025-06-12"), _paid("2025-05-15", "ZQA.US", -20.0)]

        def walk(*, run):
            raise TypeError("unsupported operand type(s) for +: 'decimal.Decimal' and 'float'")

        def other(*, run):
            raise TypeError("'NoneType' object is not subscriptable")
        with _engine.patch_engine("_us_walk_rows", walk), self.assertRaises(TypeError):
            USATaxRules()._plan_paid_compensation(rows, [], [])
        with _engine.patch_engine("_us_walk_rows", other):
            self.assertEqual(USATaxRules()._plan_paid_compensation(rows, [], []), {})

    def test_money_error(self):
        with M.trapped():
            for expr in (lambda: Decimal(1) < 0.5, lambda: Decimal(1) + 0.5, lambda: 0.5 * Decimal(1)):
                with self.assertRaises(TypeError) as cm:
                    expr()
                self.assertTrue(M.money_error(cm.exception), cm.exception)
        self.assertFalse(M.money_error(TypeError("'NoneType' object is not subscriptable")))
        with self.assertRaises(TypeError) as cm:
            Decimal(1) < None
        self.assertFalse(M.money_error(cm.exception), cm.exception)
        self.assertFalse(M.money_error(ValueError("decimal.Decimal")))

    def test_of_reads_what_float_reads(self):
        self.assertEqual(M.of(0.1), Decimal("0.1"))
        self.assertEqual(M.of(-3), Decimal(-3))
        self.assertEqual(M.of(True), Decimal(1))
        self.assertEqual(M.of(" 1.50 "), Decimal("1.50"))
        d = Decimal("2.5")
        self.assertIs(M.of(d), d)
        self.assertEqual(str(M.of(1e16 + 2)), "10000000000000002.0")
        for bad in (None, [1.0]):
            with self.assertRaises(TypeError):
                M.of(bad)
        with self.assertRaises(ValueError):
            M.of("abc")

    def test_kept_keeps_the_rows_own_value(self):
        """M.kept: the exact Decimal, an int still an int. A dividend stored
        as it is writes "10", not "10.0" (TJ-3076, TJ-3099)."""
        div = TaxTransaction(id="99900001", account="A0", date="2024-03-01", action="DIVIDEND",
                             symbol="ZZQ", quantity=0.0, gross_amount=10, net_amount=10, currency="CAD")
        res = CanadaTaxRules().compute_gains([div])
        dividend = next(e for e in res["transactions"] if e.get("action") == "DIVIDEND")["dividend"]
        self.assertIs(type(dividend), int)
        self.assertIs(type(M.kept(10)), int)
        self.assertIs(type(M.kept(True)), Decimal)
        self.assertEqual(M.kept(0.1), Decimal("0.1"))

    def test_shown_cuts_half_up_from_the_repr(self):
        """Step 6h(4): a report's money goes through M.shown, half up from
        the operand's repr, a float back, where round() cut the binary
        value half to even: a repr that is an exact half at its places
        moves one unit away from zero; any other figure is cut as round()
        cuts it. Each row: the operand, its places, round()'s cut, shown's."""
        table = (
            # Exact halves at 0, 2, 4 and 6 places, both signs.
            (0.5, 0, 0.0, 1.0), (-0.5, 0, -0.0, -1.0), (2.5, 0, 2.0, 3.0), (-2.5, 0, -2.0, -3.0),
            (0.125, 2, 0.12, 0.13), (-0.125, 2, -0.12, -0.13), (0.015, 2, 0.01, 0.02), (-0.015, 2, -0.01, -0.02),
            (0.00015, 4, 0.0001, 0.0002), (-0.00015, 4, -0.0001, -0.0002), (33.20865, 4, 33.2086, 33.2087),
            (5e-07, 6, 0.0, 1e-06), (-5e-07, 6, -0.0, -1e-06),
            (38.3569355, 6, 38.356935, 38.356936), (-38.3569355, 6, -38.356935, -38.356936),
            # A repr that is the half while the binary float sits below it:
            # round() cut these down, not half to even.
            (2.675, 2, 2.67, 2.68), (-2.675, 2, -2.67, -2.68), (1.005, 2, 1.0, 1.01),
            (2.0000025, 6, 2.000002, 2.000003),
            # Ties of 10,000 and more (14780.185 and 26907.545 are suite
            # operands at ties; TJ-3403 asked for them).
            (12345.675, 2, 12345.67, 12345.68), (-12345.675, 2, -12345.67, -12345.68),
            (1234567.125, 2, 1234567.12, 1234567.13), (-1234567.125, 2, -1234567.12, -1234567.13),
            (14780.185, 2, 14780.18, 14780.19), (7295.955, 2, 7295.95, 7295.96),
            # As round() cut them: a half round() moved up already, a repr that
            # is no half, a figure of 12 digits before the point.
            (1.5, 0, 2.0, 2.0), (0.005, 2, 0.01, 0.01), (-0.005, 2, -0.01, -0.01), (1.23455, 4, 1.2346, 1.2346),
            (5e-05, 4, 0.0001, 0.0001), (1.5e-06, 6, 2e-06, 2e-06), (99999999.995, 2, 100000000.0, 100000000.0),
            (123456789012.345, 2, 123456789012.35, 123456789012.35), (399.0, 2, 399.0, 399.0),
            # Too wide for a 28-digit context (no error: TJ-3345); the float
            # maximum; a subnormal; 17 digits at e+16 (float_decimal's case).
            (1e26, 2, 1e26, 1e26), (1.7976931348623157e308, 6, 1.7976931348623157e308, 1.7976931348623157e308),
            (5e-324, 2, 0.0, 0.0), (1e16 + 2, 2, 1e16 + 2, 1e16 + 2),
            (1.2345678901234568e+16, 2, 1.2345678901234568e+16, 1.2345678901234568e+16),
        )
        for x, places, before, after in table:
            with self.subTest(x=x, places=places):
                self.assertEqual(round(x, places), before)
                got = M.shown(x, places)
                self.assertIs(type(got), float)
                self.assertEqual((got, math.copysign(1, got)), (after, math.copysign(1, after)))

    def test_shown_passes_through_what_round_passes(self):
        """A float not finite comes back as it is (TJ-3345); an int as it
        is (a bool as round() returns it, 1); a Decimal as its half-up
        float; None, a str or a list is refused as round() refuses it."""
        for x in (math.inf, -math.inf):
            self.assertEqual(M.shown(x, 2), x)
        self.assertTrue(math.isnan(M.shown(math.nan, 2)))
        # A cut too wide even for _SHOWN's 400 digits gives the operand
        # back, as round() does, never a NaN (TJ-3418).
        big = 1.7976931348623157e308
        self.assertEqual((M.shown(big, 100), round(big, 100)), (big, big))
        for x in (3, -3, 0, 10 ** 30):
            got = M.shown(x, 2)
            self.assertEqual((got, type(got)), (round(x, 2), int))
        self.assertEqual((M.shown(True, 2), type(M.shown(True, 2))), (1, int))
        self.assertEqual(M.shown(Decimal("2.675"), 2), 2.68)
        self.assertEqual(M.shown(Decimal("-0.125"), 2), -0.13)
        self.assertEqual((M.shown(Decimal("1E+2"), 2), type(M.shown(Decimal("1E+2"), 2))), (100.0, float))
        self.assertEqual(M.shown(Decimal("-Infinity"), 2), -math.inf)
        self.assertTrue(math.isnan(M.shown(Decimal("NaN"), 2)))
        for bad in (None, "1.5", [1.5]):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    round(bad, 2)
                with self.assertRaises(TypeError):
                    M.shown(bad, 2)

    def test_shown_keeps_the_sign_of_a_zero(self):
        """-0.0 stays -0.0, and a negative figure cut to zero is -0.0, as
        round() gives them (on purpose: the `M.shown(x, 2) + 0.0` sites
        still drop the sign, and no report gains a "-0.00")."""
        for x in (-0.0, -0.001, -0.004999):
            with self.subTest(x=x):
                got = M.shown(x, 2)
                self.assertEqual(got, 0.0)
                self.assertEqual(math.copysign(1, got), -1.0)
                self.assertEqual(math.copysign(1, round(x, 2)), -1.0)
                self.assertEqual(math.copysign(1, got + 0.0), 1.0)
        self.assertEqual(math.copysign(1, M.shown(0.001, 2)), 1.0)
        self.assertEqual(math.copysign(1, M.shown(0.0, 2)), 1.0)

    def test_shown_inside_the_engines_context(self):
        """A call inside M.trapped() cuts the same and leaves that context
        as it is (precision, rounding, traps and flags); so does one under
        a precision too narrow to quantize 1e26 at 2 places (29 digits),
        which traps InvalidOperation: shown's cut has a context of its own."""
        with M.trapped():
            before = decimal.getcontext().copy()
            self.assertEqual(M.shown(2.675, 2), 2.68)
            self.assertEqual(M.shown(1e26, 2), 1e26)
            self.assertEqual(M.shown(Decimal("0.125"), 2), 0.13)
            after = decimal.getcontext()
            self.assertEqual((after.prec, after.rounding, after.traps, after.flags),
                             (before.prec, before.rounding, before.traps, before.flags))
            self.assertTrue(after.traps[decimal.FloatOperation])
        with decimal.localcontext() as ctx:
            ctx.prec = 5
            ctx.traps[decimal.InvalidOperation] = True
            ctx.rounding = decimal.ROUND_HALF_EVEN
            self.assertEqual(M.shown(1e26, 2), 1e26)
            self.assertEqual(M.shown(12345.675, 2), 12345.68)
            self.assertEqual(M.shown(0.125, 2), 0.13)

    def test_units_cut_as_round_does(self):
        self.assertIs(type(M.units(5, 9)), int)
        self.assertEqual(M.units(2.675, 2), 2.67)
        self.assertEqual(M.units(Decimal("2.675"), 2), 2.67)
        self.assertIs(type(M.units(Decimal("1.5"), 6)), float)

    def test_present_rounds_half_up(self):
        self.assertEqual(M.present(Decimal("2.675"), 2), Decimal("2.68"))
        self.assertEqual(M.present(2.675, 2), Decimal("2.68"))
        self.assertEqual(M.present(-0.125, 2), Decimal("-0.13"))
        self.assertEqual(str(M.present(3, 2)), "3.00")


class TestTheClasses(unittest.TestCase):
    def test_figures(self):
        self.assertEqual(O.figures(1.5, 1.5, "gain"), "same")
        # A Decimal against a float of its value: an answer's `leave`
        # missed it (TJ-3325).
        self.assertEqual(O.figures(1.5, Decimal("1.5"), "gain"), "form")
        self.assertEqual(O.figures(0.30000000000000004, Decimal("0.3"), "gain"), "noise")
        # 1.00005 written at 4 places, half up: the float 1.0000499999999999
        # writes 1.0, the exact Decimal 1.0001.
        self.assertEqual(O.figures(1.0000499999999999, Decimal("1.00005"), "gain"), "tie")
        self.assertEqual(O.figures(0, Decimal(0), "gain"), "form")
        self.assertEqual(O.figures(1.5, Decimal("1.51"), "gain"), "bug")
        # A cent on a large figure is no noise, whatever its size (TJ-3065).
        self.assertEqual(O.figures(12345678.90, Decimal("12345678.91"), "gain"), "bug")
        # The engine's own cut at 6 places, at a half: one unit apart there.
        self.assertEqual(O.figures(60.645312, Decimal("60.645313"), "deferred_loss_recovered"), "tie")
        self.assertEqual(O.figures(1.000049, Decimal("1.00005"), "deferred_loss_recovered"), "tie")
        # Written the same at the 4th place but far above float noise: a
        # real move (TJ-3084).
        self.assertEqual(O.figures(1.2345671, Decimal("1.2345672"), "gain"), "bug")
        self.assertEqual(O.figures(60.6453, Decimal("60.6454"), "gain"), "bug")
        self.assertEqual(O.figures(78.0489, Decimal("78.049"), "rows"), "tie")
        # Written differently and more than noise apart: a real move.
        self.assertEqual(O.figures(3.00003, Decimal("3.00007"), "gain"), "bug")
        # Only a key the engine cuts at those places (TJ-3077).
        self.assertEqual(O.figures(12.345649, Decimal("12.34565"), "proceeds"), "bug")
        self.assertEqual(O.figures(12.345649, Decimal("12.34565"), "premium"), "tie")

    def test_the_cut_keys_are_the_engines(self):
        """Every key lib/core or lib/cost_timeline cuts with round( (or
        round_half_up, units, present: TJ-3332) at fixed places is in CUT_KEYS at those
        places, so a new cut is never a 'bug' nor an old list a 'tie'. lib/core is the
        engine's text, tests/_engine.py's source(): lib/core.py and the modules of the
        engine package (PLAN.md step 10)."""
        found = set()
        timeline = (SRC / "lib" / "cost_timeline.py").read_text(encoding="utf-8")
        for text in (_engine.source(), timeline):
            for n in ast.walk(ast.parse(text)):
                pairs = []
                if isinstance(n, ast.Dict):
                    pairs = [(k.value, v) for k, v in zip(n.keys, n.values)
                             if isinstance(k, ast.Constant)]
                elif (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Subscript)
                      and isinstance(n.targets[0].slice, ast.Constant)):
                    pairs = [(n.targets[0].slice.value, n.value)]
                for key, v in pairs:
                    while isinstance(v, ast.IfExp):
                        v = v.body
                    if (isinstance(v, ast.Call) and len(v.args) == 2
                            and getattr(v.func, "id", getattr(v.func, "attr", ""))
                            in ("round", "round_half_up", "units", "present")
                            and isinstance(v.args[1], ast.Constant)):
                        found.add((v.args[1].value, key))
        listed = {(k, key) for k, keys in O.CUT_KEYS.items() for key in keys}
        # The engine's text was read: 13 pairs on 2026-11-17 (lib/core.py
        # alone after step 10's moves holds 1).
        self.assertGreaterEqual(len(found), 13)
        self.assertEqual(found - listed, set())
        # A quantity is written as its float: no 4-place rounding.
        self.assertEqual(O.figures(1.0000499999999999, Decimal("1.00005"), "qty"), "noise")

    def test_texts(self):
        self.assertEqual(O.texts("loss 2.67 on ZZQ", "loss 2.67 on ZZQ"), "same")
        self.assertEqual(O.texts("loss 2.67 on ZZQ", "loss 2.68 on ZZQ"), "text")
        self.assertEqual(O.texts("cost -0.00", "cost 0.00"), "text")
        self.assertEqual(O.texts("| A |      -0.00 |", "| A |       0.00 |"), "text")
        self.assertEqual(O.texts("| A |      -0.00 |", "| B |       0.00 |"), "bug")
        # Only the spacing moved: a layout change, not a printed half (TJ-3079).
        self.assertEqual(O.texts("| A |  1.00 |", "| A |      1.00 |"), "bug")
        self.assertEqual(O.texts("loss 2.67 on ZZQ", "loss 2.69 on ZZQ"), "bug")
        self.assertEqual(O.texts("loss 2.67 on ZZQ", "loss 2.67 on ZZR"), "bug")

    def test_compare(self):
        a = {"gain": 1.0, "rows": [{"qty": 2.0, "note": "x 1.25"}], "flag": True}
        b = {"gain": 1.0, "rows": [{"qty": 2.0, "note": "x 1.26"}], "flag": True}
        self.assertEqual([(c, p) for c, p, *_ in O.compare(a, b)], [("text", ".rows[0].note")])
        # A SharedTrace is a Sequence, not a list: walked line by line.
        from taxjson.lib.trace_format import SharedTrace
        ta, tb = SharedTrace(["# x 1.2500", "# y"]), SharedTrace(["# x 1.2501", "# y"])
        self.assertEqual([(c, p) for c, p, *_ in O.compare({"t": ta}, {"t": tb})], [("text", ".t[0]")])
        self.assertEqual(O.compare({"a": 1}, {"b": 1})[0][0], "bug")
        self.assertEqual(O.compare([1, 2], [1])[0][0], "bug")
        self.assertEqual(O.compare({"f": True}, {"f": 1})[0][0], "bug")

    def test_a_derived_per_share(self):
        """A dust pool's per-share cost moves with its cost's noise: noise
        when each tree's per-share is its own cost over its quantity; a
        per-share that is not its inputs' quotient is still a bug."""
        f = {"pool_acb_after": 1.0667944311187068e-05, "pool_qty_after": 1e-08,
             "acb_per_share_after": 1066.7944311187068}
        d = {"pool_acb_after": Decimal("0.000010667944524051323266"), "pool_qty_after": 1e-08,
             "acb_per_share_after": Decimal("1066.7944524051323266")}
        self.assertEqual([c for c, *_ in O.compare(f, d)], ["noise", "noise"])
        d["acb_per_share_after"] = Decimal("1066.80")
        self.assertIn("bug", [c for c, *_ in O.compare(f, d)])
        # Alone, the per-share is a bug; a flat pool's is judged as it is
        # (TJ-3085).
        self.assertEqual(O.figures(1066.7944311187068, Decimal("1066.7944524051323266"),
                                   "acb_per_share_after"), "bug")
        flat_f = {"pool_acb_after": 0.0003, "pool_qty_after": 0.0, "acb_per_share_after": 0.0}
        flat_d = {"pool_acb_after": Decimal("0.0003"), "pool_qty_after": 0.0,
                  "acb_per_share_after": Decimal("1066.80")}
        self.assertIn("bug", [c for c, *_ in O.compare(flat_f, flat_d)])

    def test_judge(self):
        """Two trees' runs of a book, each (answer, stderr, exception as
        the dump keeps it: type, text, site, exact money's own)."""
        stop = ("MoneySiteError", "the gains engine's exact money stopped", "src/taxjson/lib/core.py:7", True)
        rec = O.judge(({"gain": 1.0}, "", None), (None, "", stop))
        self.assertEqual((rec["class"], rec["site"]), ("typeerror", "src/taxjson/lib/core.py:7"))
        rec = O.judge(({"gain": 1.0}, "", None), (None, "", ("KeyError", "'k'", "?", False)))
        self.assertEqual(rec["class"], "bug")
        same = ("ValueError", "v", "src/taxjson/lib/core.py:9", False)
        self.assertEqual(O.judge((None, "", same), (None, "", same))["class"], "same")
        # Raised at a line an edit above it moved: the same.
        moved = ("ValueError", "v", "src/taxjson/lib/core.py:18", False)
        self.assertEqual(O.judge((None, "", same), (None, "", moved))["class"], "same")
        self.assertEqual(O.judge(({"g": 1.0}, "a 1.25\n", None), ({"g": 1.0}, "a 1.26\n", None))["class"], "text")
        # A zero's sign is written: a form (TJ-3311).
        self.assertEqual(O.judge(({"g": 0.0}, "", None), ({"g": -0.0}, "", None))["class"], "form")


class TestTheAB(unittest.TestCase):
    def test_a_tree_against_itself(self):
        """The A/B's dump and diff, a few books of each kind: a tree is
        `same` as itself, everywhere."""
        with tempfile.TemporaryDirectory() as td:
            out = io.StringIO()
            dump = str(Path(td) / "a.pkl")
            with contextlib.redirect_stdout(out):
                self.assertEqual(O.main(["dump", dump, "--books", "2"]), 0, out.getvalue())
                self.assertEqual(O.main(["diff", dump, dump, "--strict"]), 0, out.getvalue())
        self.assertRegex(out.getvalue(), r"books: (\d+)  same: \1  noise: 0")

    def _diff(self, base, new):
        import pickle
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for i, recs in enumerate((base, new)):
                paths.append(str(Path(td) / f"{i}.pkl"))
                with open(paths[-1], "wb") as f:
                    pickle.dump({"src": "x", "records": recs}, f)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = O.main(["diff", *paths, "--strict"])
        return rc, out.getvalue() + err.getvalue()

    def test_nothing_compared_is_refused(self):
        """An empty dump, or a name twice, is refused; the books that
        raised in both trees are counted (TJ-3323, TJ-3326)."""
        ok = ({"g": 5.0}, "", None)
        self.assertEqual(self._diff([], [])[0], 2)
        self.assertEqual(self._diff([("b1", ok), ("b1", ok)], [("b1", ok)])[0], 2)
        raised = (None, "", ("ValueError", "refused", "x.py:1", False))
        rc, said = self._diff([("b1", raised), ("b2", ok)], [("b1", raised), ("b2", ok)])
        self.assertEqual(rc, 0)
        self.assertIn("raised in both trees: 1", said)
        # Every book only its exception, or a money stop in both trees,
        # fails --strict (TJ-3331).
        self.assertEqual(self._diff([("b1", raised)], [("b1", raised)])[0], 1)
        stop = (None, "", ("MoneySiteError", "stopped", "core.py:9", True))
        rc, said = self._diff([("b1", stop), ("b2", ok)], [("b1", stop), ("b2", ok)])
        self.assertEqual(rc, 1)
        self.assertIn("typeerror: 1", said)

    def test_a_decimal_left_in_an_answer(self):
        """A Decimal against a float of the same value is a form
        difference: one tree's `leave` missed it (TJ-3325)."""
        self.assertEqual(O.figures(Decimal("5"), 5.0, "gain"), "form")
        self.assertEqual(O.figures(5.0, 5.0, "gain"), "same")
        self.assertEqual(self._diff([("b1", ({"g": 5.0}, "", None))],
                                    [("b1", ({"g": Decimal("5")}, "", None))])[0], 1)

    def test_a_suite_that_did_not_run(self):
        """`dump --suite` refuses a run with a module that did not load
        or that made no compute_gains call (TJ-3324); a test that fails is
        said and its calls kept (TJ-3328)."""
        with tempfile.TemporaryDirectory() as td, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                O.main(["dump", str(Path(td) / "a.pkl"), "--books", "0",
                        "--suite", "no_such_module_zzq"])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("no dump", err.getvalue())
        with tempfile.TemporaryDirectory() as td, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            out = str(Path(td) / "a.pkl")
            failing = "test_model_money_switch._FailsAfterACall.test_it"
            with mock.patch.dict(os.environ, {"TJ_AB_FAILING": "1"}):
                self.assertEqual(O.main(["dump", out, "--books", "0", "--suite", failing]), 0)
            import pickle
            with open(out, "rb") as f:
                suite = [n for n, _r in pickle.load(f)["records"] if n.startswith("suite/")]
            self.assertEqual(len(suite), 1, suite)
        self.assertIn("1 failure(s)", err.getvalue())


class _FailsAfterACall(unittest.TestCase):
    """A test that makes one engine call and then fails, for TJ-3328's
    dump to keep its call. It skips unless that test runs it."""

    def test_it(self):
        if os.environ.get("TJ_AB_FAILING") != "1":
            self.skipTest("run by TestTheAB.test_a_suite_that_did_not_run only")
        _run("canada", [_row("99900001", "2024-01-10", "BUYSELL", "ZZQ", 1.0, 5.0, -5.0)])
        self.fail("fails after its call, by design")


def _row(rid, date, action, symbol, qty, price, net, **kw):
    return TaxTransaction(id=rid, account="A0", date=date, action=action, symbol=symbol,
                          quantity=qty, price=price, net_amount=net, currency="CAD", **kw)


def _run(country, rows, **kw):
    with contextlib.redirect_stderr(io.StringIO()):
        return get_tax_rules(country).compute_gains(list(rows), **kw)


def _gains(res):
    return [(g["date"], g["symbol"], g["gain"]) for g in res["transactions"] if "gain" in g]


class TestBooksNoFuzzerWrites(unittest.TestCase):
    """Books no fuzzer writes, run to the end in exact money with their
    figures (the second review of step 6d(2): TJ-3092, TJ-3093)."""

    def test_a_return_of_capital_after_the_sale(self):
        rows = [_row("99900001", "2024-01-10", "BUYSELL", "ZZQ", 100.0, 50.0, -5000.0),
                _row("99900002", "2024-02-10", "BUYSELL", "ZZQ", -100.0, 52.0, 5200.0),
                _row("99900003", "2024-02-20", "ADJUST", "ZZQ", 0.0, 0.0, -50.0, type="roc")]
        for kw in ({}, {"trace": True}, {"cost_timeline": "settle"}):
            with self.subTest(**kw):
                res = _run("canada", rows, **kw)
                # The return of capital on an empty pool is a gain.
                self.assertEqual(_gains(res), [("2024-02-10", "ZZQ", 200.0), ("2024-02-20", "ZZQ", 50.0)])
                self.assertEqual(res["inventory"], [])

    def test_a_covered_call(self):
        call = "ZZQ240315C00055000"
        rows = [_row("99900001", "2024-01-10", "BUYSELL", "ZZQ", 100.0, 50.0, -5001.0),
                _row("99900002", "2024-02-10", "BUYSELL", call, -1.0, 2.0, 199.0),
                _row("99900003", "2024-03-01", "BUYSELL", call, 1.0, 1.0, -101.0)]
        res = _run("canada", rows, option_premium_timing="grant")
        self.assertEqual(_gains(res), [("2024-02-10", call, 199.0), ("2024-03-01", call, -101.0)])
        res = _run("canada", rows, trace=True)
        self.assertEqual(_gains(res), [("2024-03-01", call, 98.0)])
        self.assertEqual([(p["symbol"], p["qty"], p["total_cost"]) for p in res["inventory"]],
                         [("ZZQ", 100.0, 5001.0)])

    def test_an_override_on_a_loss_with_a_sold_replacement(self):
        """A US ALLOWLOSS on a loss whose replacement another account bought
        and sold before it: what the denial would have been is recorded
        (TJ-3082)."""
        from tax_rules.dual import tx
        a, b, z = "U1234567", "99900002", "ZZQ.US"
        rows = [tx("BUYSELL", "2024-06-03", z, 100, -6000.0, account=a),
                tx("BUYSELL", "2025-01-02", z, 100, -5000.0, account=b),
                tx("BUYSELL", "2025-01-04", z, -100, 4000.0, account=b),
                tx("BUYSELL", "2025-01-20", z, -40, 1200.0, account=a)]
        ov = {"account": a, "date": "2025-01-20", "symbol": z, "qty": 40, "reason": "r",
              "where": "inputs/a/x.tt:1", "line": 'ALLOWLOSS 2025-01-20 ZZQ.US 40 reason="r"'}
        res = _run("usa", rows, loss_overrides=(ov,), per_account_basis=True)
        lo = res["loss_overrides"][0]
        self.assertEqual(lo["status"], "applied")
        self.assertAlmostEqual(lo["sales"][0]["would_disallow"], 1200.0)


class TestTheLeftoverCuts(unittest.TestCase):
    """The engine's last float reads and binary cuts, exact since step
    6h(3) (TJ-3306 to TJ-3309, TJ-3311). Each half below is one whose
    binary float lies under it: round() of the float cut it a unit down,
    and its exact value cuts half up, as the gains file writes."""

    def test_a_recovered_loss_and_a_deferred_wash_cut_half_up(self):
        # TJ-3306: 2 units bought for 200 and sold at a loss (ZQA's
        # 37.175625, ZQB's 2.0021) are bought back in the window, and 1 is
        # sold: half the denied loss comes back (18.5878125 at 6 places),
        # half stays in the pool (1.00105 at 4).
        rows = []
        for i, (sym, net) in enumerate((("ZQA", 162.824375), ("ZQB", 197.9979))):
            rows += [_row(f"9990000{i}1", "2025-03-03", "BUYSELL", sym, 2.0, 100.0, -200.0),
                     _row(f"9990000{i}2", "2025-03-10", "BUYSELL", sym, -2.0, 80.0, net),
                     _row(f"9990000{i}3", "2025-03-17", "BUYSELL", sym, 2.0, 75.0, -150.0),
                     _row(f"9990000{i}4", "2025-06-02", "BUYSELL", sym, -1.0, 90.0, 90.0)]
        res = _run("canada", rows, year_end="2025-12-31")
        self.assertEqual([(g["symbol"], g["deferred_loss_recovered"]) for g in res["transactions"]
                          if g.get("deferred_loss_recovered")],
                         [("ZQA", 18.587813), ("ZQB", 1.00105)])     # round(): 18.587812
        for key in ("inventory", "inventory_year_end"):
            self.assertEqual([(p["symbol"], p["deferred_wash"]) for p in res[key]],
                             [("ZQA", 18.5878), ("ZQB", 1.0011)], key)  # round(): 1.001

    def test_the_recognised_premium_is_an_exact_sum(self):
        # TJ-3307: a put written for 400.00005 under grant timing, open at
        # the year end.
        put = "ZQZ260116P00020000.TO"
        rows = [_row("99900001", "2025-02-03", "BUYSELL", put, -1.0, 4.0, 400.00005)]
        res = _run("canada", rows, option_premium_timing="grant", option_grant_since=2025)
        self.assertEqual([(p["symbol"], p["recognised_premium"]) for p in res["inventory"]],
                         [(put, 400.0001)])                         # round(): 400.0

    def test_the_spin_off_warning_and_the_356_trace_print_the_decimal(self):
        # TJ-3308: 2.675 allocated from a parent a return of capital left
        # at no basis (round(): "2.67"); a §356 boot of 1.00105 in the
        # trace (round(): "1.0010").
        from tax_rules.dual import tx
        from _support.us_corp_engine import _boot_rows, _engine, _spin_rows
        res = _engine([tx("BUYSELL", "2023-01-10", "QZRX", 10, 500.0, account="b"),
                       tx("ADJUST", "2024-06-03", "QZRX", 0, -500.0, account="b", type="roc")]
                      + _spin_rows(2.675, 5.0))
        self.assertIn("allocates 2.68 but the parent's basis held is 0.00", res["_stderr"])
        rows = ([tx("BUYSELL", "2023-01-10", "QZRX", 50, 2000.0, account="b")]
                + _boot_rows(50, 25, 1.00105, 92.0)
                + [tx("BUYSELL", "2025-09-04", "QZRX", 5, 300.0, account="b"),
                   tx("BUYSELL", "2025-09-05", "QZRX", -5, 310.0, account="b")])
        res = _run("usa", rows, per_account_basis=True, trace=True)
        lines = [line for g in res["transactions"] for line in g.get("trace") or []
                 if "§356 EXCHANGE" in line]
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].endswith("| 1 block(s), boot 1.0011"), lines)

    def test_the_pool_views_hold_the_exact_deferred_wash(self):
        # TJ-3309: an account's part of a short (its share of the units) and
        # a merged pool hold the exact Decimal, not a float product or sum
        # (0.7 * 0.1 is 0.06999999999999999 in float; 0.1 + 0.2,
        # 0.30000000000000004).
        from taxjson.lib.futures import SHORT_POOL_SUFFIX
        short = pool_of(-10.0, 100, denied="0.7")
        with M.trapped():
            part = _short_account_part(short, "U1234567", 0.1)
            folded = _fold_short_futures_pools({
                "ZQF": pool_of(-1.0, 5, denied="0.1"),
                "ZQF" + SHORT_POOL_SUFFIX: pool_of(-10.0, 100, denied="0.2")})
            state = _ca_pool_state({"ZQF": short})(("", "ZQF"))
        self.assertEqual(repr(part["deferred_wash"]), "Decimal('0.07')")
        self.assertEqual(repr(folded["ZQF"].deferred_wash), "Decimal('0.3')")
        self.assertEqual(repr(state[2]), "Decimal('0.7')")

    def test_a_short_pools_zero_cost_is_unsigned(self):
        # TJ-3311: a short opened for no proceeds: its cost is 0.0, not
        # -0.0, in the inventory and at the year end.
        res = _run("canada", [_row("99900001", "2025-03-03", "BUYSELL", "ZQX", -10.0, 0.0, 0.0)],
                   year_end="2025-12-31")
        for key in ("inventory", "inventory_year_end"):
            [p] = res[key]
            self.assertEqual((p["qty"], repr(p["total_cost"])), (-10.0, "0.0"), key)


class TestAHalfAQuotientLeftBelow(unittest.TestCase):
    """TJ-3357: a 28-digit quotient can leave an exact half a residue below
    it; `present` cuts it as the half, and a figure really below a half
    as it is."""

    def test_the_residue_is_the_halfs(self):
        for v, p, want in (("650.2102499999999999999999999", 4, "650.2103"),
                           ("-650.2102499999999999999999999", 4, "-650.2103"),
                           ("650.2102500000000000000000001", 4, "650.2103"),
                           ("70.88031249999999999999999999", 6, "70.880313")):
            with self.subTest(v=v):
                self.assertEqual(str(M.present(Decimal(v), p)), want)

    def test_below_a_half_is_below(self):
        # A real sub-half sits 1E-17 or more under it (0.1151874999...).
        for v, p, want in (("0.1151874999999999769625", 6, "0.115187"),
                           ("2.67499999999999998", 2, "2.67"),
                           ("-2.67499999999999998", 2, "-2.67")):
            with self.subTest(v=v):
                self.assertEqual(str(M.present(Decimal(v), p)), want)

    def test_what_has_no_residue(self):
        self.assertEqual(str(M.present(Decimal("0"), 2)), "0.00")
        self.assertEqual(str(M.present(Decimal("-0"), 2)), "-0.00")
        self.assertTrue(M.present(Decimal("NaN"), 2).is_nan())
        self.assertEqual(str(M.present(2.675, 2)), "2.68")
        self.assertEqual(str(M.present(1, 2)), "1.00")
        # More than 10 places before the point: no 18th place to move.
        self.assertEqual(str(M.present(Decimal("123456789012.1234499999999999"), 4)),
                         "123456789012.1234")
        with M.trapped():
            self.assertEqual(str(M.present(Decimal("650.2102499999999999999999999"), 4)),
                             "650.2103")


class TestTheBoundary(unittest.TestCase):
    """An engine's money is Decimal from its reads to its return; what it
    returns holds floats (step 6e)."""

    def test_leave(self):
        shared = {"cost": Decimal("1.50")}
        Pair = __import__("collections").namedtuple("Pair", "a b")
        doc = {"rows": [shared, shared, {"q": 2.0, "n": Decimal("5")}],
               "t": (Decimal("0.1"), "x"), "p": Pair(Decimal("2.5"), 1), "s": "1.25", "i": 3, "f": None}
        out = M.leave(doc)
        self.assertIs(out, doc)
        self.assertEqual(doc["rows"][0], {"cost": 1.5})
        self.assertIs(doc["rows"][0], doc["rows"][1])
        self.assertIs(type(doc["rows"][2]["n"]), float)
        self.assertEqual(doc["t"], (0.1, "x"))
        self.assertEqual(doc["p"], Pair(2.5, 1))
        self.assertIs(type(doc["p"]), Pair)
        self.assertEqual((doc["s"], doc["i"], doc["f"]), ("1.25", 3, None))
        self.assertEqual(str(M.leave(Decimal("-0.0"))), "0.0")
        self.assertEqual(str(M.leave(Decimal("-0E-12"))), "0.0")
        # What 28-digit division leaves of a zero (TJ-3098); a small
        # figure stays.
        self.assertEqual(str(M.leave(Decimal("-1E-25"))), "0.0")
        self.assertEqual(M.leave(Decimal("1E-12")), 1e-12)

        class Pairs(tuple):
            pass
        more = {"set": {Decimal("1.5"), "a"}, "frozen": frozenset({Decimal("2.5")}),
                Decimal("3.5"): "key", "sub": Pairs((Decimal("4.5"),))}
        M.leave(more)
        self.assertEqual(more["set"], {1.5, "a"})
        self.assertTrue(all(type(v) is not Decimal for v in more["set"]))
        self.assertEqual(more["frozen"], frozenset({2.5}))
        self.assertIs(type(next(iter(more["frozen"]))), float)
        self.assertEqual([type(k) for k in more], [str, str, float, str])
        self.assertIs(type(more["sub"]), Pairs)
        self.assertIs(type(more["sub"][0]), float)

    def test_a_missed_site_names_itself(self):
        """A money site still mixing float in stops on an error naming the
        site and what to do; any other TypeError passes as it is (TJ-3104)."""
        @M.engine
        def mixed():
            return M.of(1.0) + 0.5

        @M.engine
        def other():
            return None + 1
        with self.assertRaises(M.MoneySiteError) as cm:
            mixed()
        e = cm.exception
        self.assertIn("test_model_money_switch.py:", e.site)
        self.assertIn("decimal.Decimal", e.cause)
        self.assertEqual(str(e), M.stopped_text(e.site, e.cause))
        self.assertIn("please report it with this line.", str(e))
        self.assertIsInstance(e, TypeError)
        self.assertTrue(M.money_error(e))
        with self.assertRaises(TypeError) as cm:
            other()
        self.assertNotIsInstance(cm.exception, M.MoneySiteError)

    def test_the_radars_flags(self):
        """The radar's flags, called outside an engine run, come back as
        floats (TJ-3101)."""
        loss = {"symbol": "ZZQ", "date": "2025-02-03", "amount": -12.345, "direction": "LONG", "id": "99900001"}
        wt = TaxTransaction(id="99900002", account="A0", date="2025-02-10", action="BUYSELL", symbol="ZZQ.WT",
                            quantity=10.0, price=1.0, net_amount=-10.0, currency="CAD")
        found = replacement_flags([loss], [wt], date_of=lambda t: t.date)
        self.assertEqual([w["rule"] for w in found], ["right_vs_share_loss"])
        self.assertIs(type(found[0]["loss_amount"]), float)
        self.assertEqual(found[0]["loss_amount"], -12.35)

    def test_engine(self):
        seen = []

        @M.engine
        def run(x):
            seen.append(decimal.getcontext().traps[decimal.FloatOperation])
            # Half up, as the gains file writes and a trace line shows.
            self.assertEqual(f"{Decimal('91.22625'):.4f}", "91.2263")
            return {"x": M.of(x)}
        self.assertIs(type(run(0.1)["x"]), float)
        self.assertEqual(seen, [True])
        self.assertFalse(decimal.getcontext().traps[decimal.FloatOperation])

    def test_a_book_returns_floats(self):
        rows = [TaxTransaction(id="99900001", account="A0", date="2024-01-02", action="BUYSELL", symbol="ZZQ",
                               quantity=3.0, price=0.1, net_amount=-0.30000000000000004, currency="CAD"),
                TaxTransaction(id="99900002", account="A0", date="2024-02-02", action="BUYSELL", symbol="ZZQ",
                               quantity=-3.0, price=0.2, net_amount=0.6, currency="CAD")]
        found = []
        seen = set()

        def scan(x):
            # Into keys, sets and objects too: `leave` walks no object, so
            # money in one would reach the readers as a Decimal.
            if isinstance(x, Decimal):
                found.append(x)
                return
            if isinstance(x, (str, bytes, int, float, type(None), type)) or callable(x) or id(x) in seen:
                return
            seen.add(id(x))
            if isinstance(x, dict):
                for k, v in x.items():
                    scan(k)
                    scan(v)
            elif isinstance(x, (list, tuple, set, frozenset)):
                for v in x:
                    scan(v)
            else:
                for v in getattr(x, "__dict__", {}).values():
                    scan(v)
                for s in getattr(type(x), "__slots__", ()):
                    scan(getattr(x, s, None))
        for country in ("canada", "usa"):
            with contextlib.redirect_stderr(io.StringIO()):
                res = get_tax_rules(country).compute_gains(list(rows), trace=True, cost_timeline="settle")
            scan(res)
        self.assertEqual(found, [])
        self.assertFalse(decimal.getcontext().traps[decimal.FloatOperation])

    def test_a_cost_timeline_writes(self):
        """A written call in each of two accounts with a cost timeline:
        its account premiums come back floats, and the gains file can be
        written (TJ-3097)."""
        import json
        from taxjson.model.result import gains_json
        call = "ZZQ240315C00055000"

        def row(rid, acct, d, sym, q, p, n):
            return TaxTransaction(id=rid, account=acct, date=d, action="BUYSELL", symbol=sym,
                                  quantity=q, price=p, net_amount=n, currency="CAD")
        rows = [row("99900001", "A1", "2024-01-10", "ZZQ", 100.0, 50.0, -5001.0),
                row("99900002", "A1", "2024-02-10", call, -1.0, 2.0, 199.0),
                row("99900003", "A2", "2024-02-12", call, -1.0, 2.5, 249.0),
                row("99900004", "A1", "2024-03-01", call, 1.0, 1.0, -101.0)]
        with contextlib.redirect_stderr(io.StringIO()):
            res = get_tax_rules("canada").compute_gains(rows, cost_timeline="settle")
        premiums = [e["premium"] for day in res["cost_timeline"]["account_premiums"].values()
                    for e in day.values()]
        self.assertTrue(premiums)
        self.assertTrue(all(type(p) is float for p in premiums), premiums)
        json.dumps(gains_json(res))


class TestAToolStoppedAtAMoneySite(unittest.TestCase):
    """A stand-alone tool whose engine stops at a money site still mixing
    float in says it on one `error:` line naming the site, exit 1, not a
    traceback (TJ-3104, step 6f). The site is planted at startup by a
    sitecustomize on PYTHONPATH."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        hook = td / "hook"
        hook.mkdir()
        (hook / "sitecustomize.py").write_text(
            _engine.loader() +
            "from taxjson.model import money as M\n"
            "real = _engine.binding('_disambiguate_duplicate_ids')\n"
            "def missed(*a, **k):\n"
            "    real(*a, **k)\n"
            "    M.of(1.0) + 0.5\n"
            "_engine.store_engine('_disambiguate_duplicate_ids', missed)\n", encoding="utf-8")
        cls.env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1", PYTHONPATH=os.pathsep.join(
            [str(hook), str(SRC.parent)]))
        rows = [_row("99900001", "2024-01-10", "BUYSELL", "ZZQ", 10.0, 5.0, -50.0),
                _row("99900002", "2024-02-10", "BUYSELL", "ZZQ", -10.0, 6.0, 60.0)]
        cls.books = td / "books.json"
        import json
        cls.books.write_text(json.dumps({"transactions": [r.to_dict() for r in rows]}), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _gains(self, width):
        return subprocess.run(python_module_argv("taxjson.bin._entry", [
            "taxjson_gains", str(self.books), "--country", "canada"]),
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300,
            env=dict(self.env, TAXJSON_WIDTH=str(width)))

    def test_one_error_line(self):
        r = self._gains(0)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        line, = [x for x in r.stderr.splitlines() if "error:" in x]
        self.assertEqual(line, r.stderr.splitlines()[-1])
        self.assertRegex(line, r"^taxjson-gains: error: the gains engine's exact money stopped at "
                               + _engine.site_pattern() +
                               r":\d+ \(.+\): a figure there is still a binary float\. "
                               r"This is a bug in taxjson: please report it with this line\.$")

    def test_shown_to_a_person(self):
        r = self._gains(100)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        said = r.stderr[r.stderr.index("Error: "):]
        self.assertRegex(said, r"^Error: the gains engine's exact money stopped at "
                               + _engine.site_pattern() + ":", r.stderr)
        self.assertTrue(" ".join(said.split()).endswith("please report it with this line."), said)



class TestTheRadarSaysAStop(unittest.TestCase):
    """The wash radar's replacement flags are advice it leaves out where
    they fail, but exact money's stop in them is a bug, said on the
    tool's one error line, exit 1 (TJ-3314); a filed year's recompute
    that stopped so is not an input to fix (TJ-3316)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        hook = td / "hook"
        hook.mkdir()
        (hook / "sitecustomize.py").write_text(
            _engine.loader() +
            "from taxjson.model import money as M\n"
            "real = _engine.binding('detect_right_replacement_matches')\n"
            "def missed(*a, **k):\n"
            "    M.of(1.0) + 0.5\n"
            "    return real(*a, **k)\n"
            "_engine.store_engine('detect_right_replacement_matches', missed)\n", encoding="utf-8")
        cls.hook = str(hook)
        rows = [_row("99900001", "2024-03-01", "BUYSELL", "ZZQ.TO", 100.0, 50.0, -5000.0),
                _row("99900002", "2024-06-03", "BUYSELL", "ZZQ.TO", -100.0, 40.0, 4000.0),
                _row("99900003", "2024-06-10", "BUYSELL", "ZZQ.WT.TO", 10.0, 1.0, -10.0)]
        cls.books = td / "books.json"
        import json
        cls.books.write_text(json.dumps({"transactions": [r.to_dict() for r in rows]}), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _radar(self, *path):
        env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1", TAXJSON_WIDTH="0",
                   PYTHONPATH=os.pathsep.join([*path, str(SRC.parent)]))
        return subprocess.run(python_module_argv("taxjson.bin._entry", [
            "taxjson_wash_radar", "--taxable", str(self.books), "--country", "canada",
            "--date", "2024-06-20", "--all"]),
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300, env=env)

    def test_the_flag_is_there_without_a_stop(self):
        r = self._radar()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ZZQ.WT.TO", r.stdout)

    def test_a_stop_in_the_flags_is_said(self):
        r = self._radar(self.hook)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stderr.splitlines()[-1][:len("taxjson-wash-radar: error: " + M.STOPPED)],
                         "taxjson-wash-radar: error: " + M.STOPPED, r.stderr)

    def test_a_relayed_stop(self):
        """A tool relaying the radar's stop shows its line without the
        child's label (TJ-3330), and harvest stops on it, as buy-check
        does, instead of going on without the radar (TJ-3329)."""
        from taxjson.cli import plan
        from taxjson.cli.common import _child_error
        line = "taxjson-wash-radar: error: " + M.stopped_text("taxjson/lib/core.py:1", "x")
        self.assertTrue(_child_error(line).startswith(M.STOPPED), _child_error(line))

        def ran(rc, err):
            return subprocess.CompletedProcess([], rc, "", err)
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()) as said:
            plan._live_radar_error(ran(1, line))
        self.assertIn(M.STOPPED, f"{cm.exception.code}{said.getvalue()}")
        self.assertEqual(plan._live_radar_error(ran(1, "taxjson-wash-radar: error: no books")),
                         ["no books"])
        self.assertEqual(plan._live_radar_error(ran(0, "")), [])

    def test_a_filed_years_recompute(self):
        from taxjson.pipeline.run import _recompute_hint
        self.assertIn("bug in taxjson", _recompute_hint(M.stopped_text("taxjson/lib/core.py:1", "x")))
        self.assertIn("Fix that input", _recompute_hint("taxjson-gains: error: cannot read books.json"))


if __name__ == "__main__":
    unittest.main()
