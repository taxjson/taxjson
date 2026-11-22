"""model/money.py: money's float round trip, bit for bit (step 6a); and the
model package imports only itself and the standard library.

Moved from tests/test_model_events.py when model/events.py was retired
(PLAN step 14)."""
import ast
import math
import random
import struct
import sys
import unittest
from decimal import Decimal, localcontext

from taxjson.model.money import from_money, to_money
import _paths

SRC = _paths.SRC / "taxjson"


class TestMoneyRoundTrip(unittest.TestCase):
    def test_random_floats_bit_for_bit(self):
        rng = random.Random(6)
        n = 0
        while n < 100_000:
            x = struct.unpack("<d", rng.getrandbits(64).to_bytes(8, "little"))[0]
            if not math.isfinite(x):
                continue
            y = from_money(to_money(x))
            self.assertIs(type(y), float, repr(x))
            self.assertEqual(struct.pack("<d", y), struct.pack("<d", x), repr(x))
            n += 1

    def test_the_amounts_a_row_carries(self):
        # Random bits are rarely an amount: draw cents and up to eight
        # places from 1e-8 to 1e15, as a parser's float() makes them.
        rng = random.Random(7)
        for _ in range(100_000):
            places = rng.randrange(0, 9)
            x = float(f"{rng.randrange(10 ** 15)}e-{places}")
            if rng.random() < 0.5:
                x = -x
            y = from_money(to_money(x))
            self.assertIs(type(y), float, repr(x))
            self.assertEqual(struct.pack("<d", y), struct.pack("<d", x), repr(x))
            self.assertEqual(to_money(x), Decimal(repr(x)), repr(x))

    def test_the_decimal_context_does_not_matter(self):
        with localcontext() as ctx:
            ctx.prec = 5
            for x in (1.2345678901234568e+16, 0.1, 123456.789, -0.0):
                self.assertEqual(struct.pack("<d", from_money(to_money(x))), struct.pack("<d", x))

    def test_ints_stay_ints_and_the_edges(self):
        for x in (0, 1, -7, 10 ** 30):
            self.assertIs(type(from_money(to_money(x))), int)
            self.assertEqual(from_money(to_money(x)), x)
        for x in (-0.0, 5e-324, 0.1, 1e16, 1.2345678901234568e+16, -1.7976931348623157e308):
            self.assertEqual(struct.pack("<d", from_money(to_money(x))), struct.pack("<d", x))
        self.assertEqual(to_money(1.2345678901234568e+16).as_tuple().exponent, -1)

    def test_what_is_no_money(self):
        for x in (True, Decimal("1"), "1.5", None):
            with self.assertRaises(TypeError):
                to_money(x)
        for x in (math.inf, math.nan):
            with self.assertRaises(ValueError):
                to_money(x)
        for d in (Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity")):
            with self.assertRaises(ValueError):
                from_money(d)
        with self.assertRaises(TypeError):
            from_money(1.5)


class TestTheModelsImports(unittest.TestCase):
    def test_model_imports_model_and_the_standard_library(self):
        for p in sorted((SRC / "model").glob("*.py")):
            for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0, p.name)
                    mods = [node.module]
                for m in mods:
                    top = m.split(".")[0]
                    self.assertTrue(m.startswith("taxjson.model") or top in sys.stdlib_module_names,
                                    f"{p.name} imports {m}")


if __name__ == "__main__":
    unittest.main()
