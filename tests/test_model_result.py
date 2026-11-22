"""Step 6c(1): the gains output as a typed Result (model/result.py).

(a) Every recorded gains file (the golden books' work/*_gains*.json) comes
back byte for byte through from_json and to_json; (b) run_gains's output
is what the code before it made of the engine's dict (round_floats, the
cost timeline as it was, last), over the golden books' and the fuzzers'
books; (c) a row's typed values are its JSON's numbers (float() of each the
float held), None when absent or null; (d) `number` reads as the readers'
`float(x or 0)` read; (e) the shapes refused, a null part, the order;
(f) the classes are frozen and slotted; (g) taxjson-sum-gains's
summarize_gains, reading the Result, gives what the code before it gave,
over the same documents and a table of odd rows. The one change: a row
with `"qty": null` and a fee stopped it with a TypeError (TJ-2858)."""
import copy
import dataclasses
import io
import json
import math
import random
import struct
import unittest
from decimal import Decimal
from typing import Any, Dict
from unittest import mock

from taxjson.lib.json_input import dump_filing_json
from taxjson.lib.ticker_map import get_underlying as get_base_ticker, is_option_ticker
from taxjson.model import result as R
from taxjson.model.money import from_money, round_floats, to_money
from taxjson.model.result import GainRow, Holding, Result, ResultShapeError, Row, WashRecord, number
import _paths

TESTS = _paths.TESTS


def _gains_files():
    return sorted(p for p in (TESTS / "golden").rglob("*_gains*.json")
                  if isinstance(json.loads(p.read_text(encoding="utf-8")), dict))


def _dumped(doc) -> str:
    buf = io.StringIO()
    dump_filing_json(doc, buf)
    return buf.getvalue()


def old_run_gains_end(results):
    """run_gains's last lines before step 6c(1), verbatim."""
    timeline = results.pop('cost_timeline', None)
    results = round_floats(results)
    if timeline is not None:
        results['cost_timeline'] = timeline
    return results


def _typed(x):
    """`x` with every value's type beside it, to compare two documents
    type for type (5 against 5.0, a tuple against a list)."""
    if isinstance(x, dict):
        return ("dict", [(k, _typed(v)) for k, v in x.items()])
    if isinstance(x, (list, tuple)):
        return (type(x).__name__, [_typed(v) for v in x])
    if isinstance(x, float):
        return ("float", "nan" if math.isnan(x) else struct.pack(">d", x))
    return (type(x).__name__, repr(x))


def _books():
    """(name, txs, sheltered, country) for run_gains: the engine golden
    books' inputs and the fuzzers' books."""
    from taxjson.lib.tx_load import coerce_transaction_row
    out = []
    for p in sorted((TESTS / "golden").glob("*/inputs.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        rows = {side: [coerce_transaction_row(r, i, p.parent.name) for i, r in enumerate(d[side])]
                for side in d}
        for country in ("canada", "usa"):
            out.append((f"golden {p.parent.name} {country}", rows.get("taxable", []),
                        rows.get("sheltered", []), country))
    from _support.engine_fuzz_books import make_book
    from _support.engine_settle_fuzz_books import build_book
    from _support.engine_transfer_fuzz_books import make_transfer_book
    for seed in range(200):
        txs, shel = make_book(seed)
        out.append((f"make_book {seed}", txs, shel, ("canada", "usa")[seed % 2]))
    for seed in range(40):
        txs, shel = make_transfer_book(seed)[:2]
        out.append((f"make_transfer_book {seed}", txs, shel, ("canada", "usa")[seed % 2]))
        txs, shel, _meta = build_book(seed)
        out.append((f"build_book {seed}", txs, shel, ("usa", "canada")[seed % 2]))
    return out


def _run(txs, shel, country):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    return run_gains(list(txs), list(shel), (), GainsRequest(country=country, taxable=True))


# --- the documents --------------------------------------------------------

class TestTheRecordedFiles(unittest.TestCase):
    def test_every_recorded_gains_file_comes_back_byte_for_byte(self):
        """A `*_gains.json` is what run_gains wrote: it comes back byte
        for byte. A `*_gains_wash.json` is the wash stage's, money not
        rounded: to_json writes it as run_gains would have."""
        files = _gains_files()
        written = [p for p in files if p.name.endswith("_gains.json")]
        self.assertGreater(len(written), 50)
        self.assertGreater(len(files) - len(written), 10)
        for p in files:
            with self.subTest(str(p.relative_to(TESTS))):
                text = p.read_text(encoding="utf-8")
                doc = json.loads(text)
                out = Result.from_json(doc).to_json()
                self.assertEqual(_typed(out), _typed(old_run_gains_end(copy.deepcopy(doc))))
                if p in written:
                    self.assertEqual(_dumped(out), text)
                    self.assertEqual(_typed(out), _typed(doc))

    def test_the_document_is_not_changed_and_not_shared(self):
        p = _gains_files()[0]
        doc = json.loads(p.read_text(encoding="utf-8"))
        before = copy.deepcopy(doc)
        res = Result.from_json(doc)
        out = res.to_json()
        self.assertEqual(doc, before)
        self.assertIsNot(out["transactions"], doc["transactions"])
        self.assertIsNot(out["transactions"][0], doc["transactions"][0])
        doc["transactions"][0]["gain"] = 123.0
        doc["transactions"].append({"qty": 1.0})
        doc["extra"] = 1
        self.assertNotEqual(res.transactions[0].gain, Decimal("123.0"))
        self.assertEqual(len(res.transactions), len(before["transactions"]))
        self.assertEqual(_typed(res.to_json()), _typed(out))


class TestRunGains(unittest.TestCase):
    """run_gains writes through the Result: what the code before it
    wrote, value for value and type for type."""

    def test_run_gains_is_what_it_was(self):
        from taxjson.lib import pipeline
        real = R.gains_json
        seen = []

        def spy(doc):
            seen.append(copy.deepcopy(doc))
            out = real(doc)
            self.assertEqual(_typed(doc), _typed(seen[-1]))  # not changed
            return out

        books = _books()
        self.assertGreater(len(books), 250)
        with mock.patch.object(pipeline, "gains_json", side_effect=spy):
            for name, txs, shel, country in books:
                with self.subTest(name):
                    seen.clear()
                    out = _run(txs, shel, country)
                    self.assertEqual(len(seen), 1)
                    self.assertEqual(_typed(out), _typed(old_run_gains_end(seen[0])))

    def test_the_cost_timeline_is_not_rounded_and_goes_last(self):
        doc = {"cost_timeline": {"X": [[0.123456789, 1e-9]]}, "summary": {"gain": 0.123456789},
               "transactions": [{"qty": 0.123456789, "cost": 0.123456789}]}
        out = Result.from_json(doc).to_json()
        self.assertEqual(list(out), ["summary", "transactions", "cost_timeline"])
        self.assertEqual(out["cost_timeline"], doc["cost_timeline"])
        self.assertEqual(out["summary"], {"gain": 0.1235})
        self.assertEqual(out["transactions"], [{"qty": 0.123456789, "cost": 0.1235}])
        self.assertEqual(_typed(out), _typed(old_run_gains_end(copy.deepcopy(doc))))
        self.assertNotIn("cost_timeline", Result.from_json({"cost_timeline": None}).to_json())


# --- the rows -------------------------------------------------------------

_MONEY = ("proceeds", "cost", "gain", "raw_gain", "commission", "fee", "disallowed_amount",
          "permanently_disallowed", "deferred_loss_recovered", "dividend", "pil")
_TEXT = ("symbol", "account", "currency", "date", "date_settle", "action", "id", "time",
         "term", "direction", "note", "acquired_date")
_FLAGS = ("is_option", "is_wash_sale", "grant", "deemed", "tainted", "paid_on_short")


class TestTheRows(unittest.TestCase):
    def test_the_accessors_name_their_keys(self):
        self.assertEqual(GainRow.quantity.key, "qty")
        self.assertEqual(Holding.quantity.key, "qty")
        self.assertEqual(Holding.total_cost.key, "total_cost")
        for cls in (Row, WashRecord):
            self.assertFalse(hasattr(cls, "quantity"), cls)
            self.assertFalse(hasattr(cls, "symbol"), cls)
        for attr in _MONEY + _TEXT + _FLAGS + ("days_held",):
            self.assertEqual(getattr(GainRow, attr).key, attr)

    def test_a_rows_values_are_its_jsons(self):
        n = 0
        for p in _gains_files():
            doc = json.loads(p.read_text(encoding="utf-8"))
            res = Result.from_json(doc)
            for part in ("transactions", "manual_reporting_required"):
                for row, raw in zip(getattr(res, part), doc.get(part) or []):
                    n += 1
                    self.assertIsInstance(row, GainRow)
                    for attr, key in [("quantity", "qty")] + [(m, m) for m in _MONEY]:
                        v = getattr(row, attr)
                        if raw.get(key) is None:
                            self.assertIsNone(v, (p.name, key))
                        else:
                            self.assertIsInstance(v, Decimal)
                            self.assertEqual(_typed(type(raw[key])(v)), _typed(raw[key]), (p.name, key))
                    for attr in _TEXT + ("days_held",):
                        self.assertIs(getattr(row, attr), raw.get(attr))
                    for attr in _FLAGS:
                        self.assertIs(getattr(row, attr), bool(raw.get(attr)))
                    self.assertEqual(dict(row.fields), raw)
                    self.assertEqual(list(row.fields), list(raw))
            for row, raw in zip(res.inventory, doc.get("inventory") or []):
                self.assertIsInstance(row, Holding)
                self.assertEqual(float(row.quantity), raw["qty"])
                self.assertEqual(float(row.total_cost), raw["total_cost"])
                for attr in ("symbol", "account", "currency"):
                    self.assertIs(getattr(row, attr), raw.get(attr))
                self.assertEqual(dict(row.fields), raw)
            for row, raw in zip(res.wash_sales, doc.get("wash_sales") or []):
                self.assertIsInstance(row, WashRecord)
                self.assertEqual(dict(row.fields), raw)
        self.assertGreater(n, 300)

    def test_get_and_in(self):
        row = Result.from_json({"transactions": [{"qty": 0, "x": None}]}).transactions[0]
        self.assertEqual(row.quantity, Decimal(0))
        self.assertIn("x", row)
        self.assertNotIn("y", row)
        self.assertIsNone(row.get("x", 5))
        self.assertEqual(row.get("y", 5), 5)
        self.assertIsNone(row.cost)
        self.assertFalse(row.tainted)


class TestNumber(unittest.TestCase):
    """number(v) or 0 reads as the readers' v or 0 did: float() of the
    one is float() of the other, or both raise the same error."""

    EDGES = [None, "", 0, 0.0, -0.0, 5e-324, -5e-324, 1e308, -1.7976931348623157e308, 0.1, 1e16,
             1.2345678901234568e+16, 7, -3, 2 ** 80, True, False, float("nan"), float("inf"),
             float("-inf"), "12.5", " 1.5\n", "-0", "1e999999999999999999999", "-1e-999999999999999999999",
             "nan", "inf", "-Infinity", "1_000", "abc", "sNaN", "0x10", [], {}, (), [1], {"a": 1},
             Decimal("2.50"), Decimal("-0"), object()]

    @staticmethod
    def _result_of(f):
        try:
            v = f()
        except (ArithmeticError, ValueError, TypeError) as e:
            return ("raises", type(e) if not isinstance(e, ArithmeticError) else ValueError)
        return ("nan",) if math.isnan(v) else ("value", struct.pack(">d", v))

    def test_float_of_number_is_float_of_the_value(self):
        """A string spelling a negative zero ("-0") is a zero Decimal,
        so `or 0` gives 0.0 where float("-0") was -0.0: the same in any
        sum that starts at 0.0, as the readers' sums do (and a file's
        string amount is refused before a reader reads it)."""
        for v in self.EDGES:
            with self.subTest(v=v):
                old = self._result_of(lambda: float(v or 0) + (0.0 if isinstance(v, str) else -0.0))
                new = self._result_of(lambda: float(number(v) or 0) + (0.0 if isinstance(v, str) else -0.0))
                self.assertEqual(new, old)

    def test_a_float_is_its_repr(self):
        rng = random.Random(6031)
        for _ in range(100000):
            x = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
            if not math.isfinite(x):
                continue
            d = number(x)
            self.assertEqual(d.as_tuple(), to_money(x).as_tuple())
            self.assertEqual(struct.pack(">d", float(d)), struct.pack(">d", x))
        # A 17-digit repr at e+16 keeps a place: from_money reads it as
        # the float it was, not an int (TJ-2869).
        for x in (1.2345678901234568e+16, -9.876543210987654e+16, 1e16):
            with self.subTest(x=x):
                self.assertEqual(number(x).as_tuple(), to_money(x).as_tuple())
                self.assertIs(type(from_money(number(x))), float)
                self.assertEqual(from_money(number(x)), x)

    def test_its_types(self):
        self.assertIsNone(number(None))
        self.assertIsNone(number(""))
        self.assertEqual(number(7), Decimal(7))
        self.assertEqual(str(number(0.5)), "0.5")
        d = Decimal("2.50")
        self.assertIs(number(d), d)
        with self.assertRaises(TypeError):
            number([1])


# --- the shapes -----------------------------------------------------------

class TestTheShapes(unittest.TestCase):
    def test_a_part_of_the_wrong_shape_is_refused_when_it_is_read(self):
        bad = [("transactions", [1]), ("transactions", "rows"), ("transactions", {"a": 1}),
               ("inventory", [[1]]), ("wash_sales", {"a": 1}), ("summary", [1]),
               ("by_ticker", "x"), ("manual_reporting_required", [None]),
               ("lots_at", [None]), ("lots_at", "x"), ("lots_at", {"a": 1})]
        for key, value in bad:
            with self.subTest(key=key, value=value):
                res = Result.from_json({key: value, "x": 1})
                self.assertIn(key, res)
                with self.assertRaises(ResultShapeError):
                    getattr(res, key)
                self.assertEqual(res.to_json()[key], value)
        for doc in ([], "x", None):
            with self.subTest(doc=doc), self.assertRaises(ResultShapeError):
                Result.from_json(doc)
        self.assertTrue(issubclass(ResultShapeError, ValueError))

    def test_a_null_or_empty_part_reads_empty_and_is_written_as_held(self):
        """The readers' `or []`: a null, {} or [] in any part (TJ-2859:
        `{"transactions": {}}` on stdin printed the report)."""
        for empty in (None, {}, [], "", 0):
            doc = {k: empty for k in ("transactions", "manual_reporting_required", "wash_sales",
                                      "inventory", "lots_at", "summary", "by_ticker")}
            doc["extra"] = 1.23456
            with self.subTest(empty=empty):
                res = Result.from_json(doc)
                for k in ("transactions", "manual_reporting_required", "wash_sales", "inventory",
                          "lots_at"):
                    self.assertEqual(getattr(res, k), ())
                self.assertEqual(dict(res.summary), {})
                self.assertEqual(dict(res.by_ticker), {})
                self.assertEqual(res.to_json(), dict(doc, extra=1.2346))

    def test_the_order_and_the_rest(self):
        doc = {"metadata": {"v": 1}, "transactions": [], "zeta": [1.00005], "summary": {}, "alpha": "a"}
        res = Result.from_json(doc)
        self.assertEqual(list(res.doc), list(doc))
        self.assertEqual(res.get("metadata"), {"v": 1})
        self.assertIn("zeta", res)
        self.assertNotIn("nope", res)
        self.assertEqual(list(res.to_json()), list(doc))
        self.assertEqual(res.to_json()["zeta"], [1.0001])
        self.assertEqual(Result.from_json({}).to_json(), {})
        self.assertNotIn("summary", Result.from_json({"transactions": []}).to_json())

    def test_the_parts_cannot_be_changed(self):
        """TJ-2870: a part read through get() or doc is read-only, so a
        typed part and to_json cannot disagree."""
        res = Result.from_json({"transactions": [{"gain": 1.0}], "summary": {"a": 1}, "x": [1]})
        self.assertEqual(len(res.transactions), 1)
        for part in ("transactions", "x"):
            self.assertIsInstance(res.get(part), tuple)
        with self.assertRaises(TypeError):
            res.get("transactions")[0]["gain"] = 5.0
        with self.assertRaises(TypeError):
            res.get("summary")["a"] = 2
        with self.assertRaises(TypeError):
            res.doc["x"] = 1
        self.assertEqual(res.to_json(), {"transactions": [{"gain": 1.0}], "summary": {"a": 1}, "x": [1]})

    def test_a_tuple_is_read_as_a_list(self):
        """TJ-2875: an in-memory caller's tuple of rows is read, and
        written as a list."""
        res = Result.from_json({"transactions": ({"qty": 1.0},), "inventory": ({"qty": 2.0},)})
        self.assertEqual(res.transactions[0].quantity, Decimal("1.0"))
        self.assertEqual(res.inventory[0].quantity, Decimal("2.0"))
        self.assertEqual(res.to_json(), {"transactions": [{"qty": 1.0}], "inventory": [{"qty": 2.0}]})

    def test_a_typed_value_that_is_no_number_is_refused_by_name(self):
        """TJ-2876: a wrongly shaped amount is a ResultShapeError naming
        its key, as a wrong part is."""
        row = Result.from_json({"transactions": [{"fee": [1], "qty": {"a": 1}, "cost": "abc"}]}).transactions[0]
        for attr, key in (("fee", "fee"), ("quantity", "qty"), ("cost", "cost")):
            with self.subTest(attr), self.assertRaises(ResultShapeError) as cm:
                getattr(row, attr)
            self.assertIn(f'"{key}"', str(cm.exception))

    def test_gains_json_does_not_change_the_document(self):
        doc = {"transactions": [{"cost": 1.23456}], "cost_timeline": {"X": [[1.23456]]}}
        before = copy.deepcopy(doc)
        out = R.gains_json(doc)
        self.assertEqual(doc, before)
        self.assertEqual(out, {"transactions": [{"cost": 1.2346}], "cost_timeline": {"X": [[1.23456]]}})

    def test_a_part_is_read_once(self):
        res = Result.from_json({"transactions": [{"qty": 1}], "summary": {"a": 1}})
        self.assertIs(res.transactions, res.transactions)
        self.assertIs(res.summary, res.summary)
        same = {"transactions": [{"qty": 1}], "summary": {"a": 1}}
        self.assertEqual(Result.from_json(same), res)


class TestTheClasses(unittest.TestCase):
    def test_frozen_and_slotted(self):
        res = Result.from_json({"transactions": [{"qty": 1}], "inventory": [{"qty": 2}],
                                "wash_sales": [{"loss_tx": {}}]})
        for obj in (res, res.transactions[0], res.inventory[0], res.wash_sales[0]):
            with self.subTest(type(obj).__name__):
                self.assertFalse(hasattr(obj, "__dict__"))
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(obj, "fields" if isinstance(obj, Row) else "doc", {})
                with self.assertRaises(TypeError):
                    hash(obj)
        with self.assertRaises(TypeError):
            res.doc["x"] = 1
        with self.assertRaises(TypeError):
            res.transactions[0].fields["qty"] = 5
        with self.assertRaises(TypeError):
            res.summary["x"] = 1

    def test_numeric_re_exports_the_model_s_rounding(self):
        from taxjson.lib import numeric
        from taxjson.model import money
        for name in ("D", "round_half_up", "round_floats", "_QTY_FIELDS"):
            self.assertIs(getattr(numeric, name), getattr(money, name))
        self.assertIs(R.round_floats, money.round_floats)

    def test_one_reader_of_a_number_written_out(self):
        """model/money `text_decimal` reads the tokens of model/evidence
        and model/result (TJ-2862)."""
        from taxjson.model import evidence, money
        self.assertIs(evidence.text_decimal, money.text_decimal)
        self.assertIs(evidence.exact_decimal, money.exact_decimal)
        self.assertIs(R.text_decimal, money.text_decimal)
        for token in ("12.5", "1e999999999999999999999", "-1e-999999999999999999999", "nan", " 7 "):
            with self.subTest(token=token):
                self.assertEqual(str(number(token)), str(evidence._number(token)))


# --- the first reader: taxjson-sum-gains ----------------------------------

def _odd_docs():
    """Gains documents with odd rows: what the old summarize_gains read
    (a missing qty, a string or bool amount, an empty one, a 0, an
    income row's fee, a grant write and its buy-back, a tainted row, a
    routed manual row, a null part)."""
    base = {"account": "A1", "currency": "CAD", "date": "2024-03-01", "date_settle": "2024-03-04"}
    rows = [
        dict(base, symbol="SYNA.TO", qty=10.0, cost=100.0, proceeds=120.5, gain=20.5, days_held=40,
             fee=1.0, commission=0.5, term="LONG_TERM"),
        dict(base, symbol="SYNA.TO", cost="12.5", proceeds=True, gain="", days_held=None, fee=0),
        dict(base, symbol="SYNA.TO", gain=3.0, fee=2.0),
        dict(base, symbol="SYNA.TO", action="DIVIDEND", dividend=5.25, qty=0, fee=0.25),
        dict(base, symbol="SYNA.TO", action="DIVIDEND_IN_LIEU", pil=-2.0, paid_on_short=True,
             dividend=1.0, qty=3),
        dict(base, symbol="SYNA250620C00010000", qty=1.0, gain=50.0, grant=True, direction="SHORT"),
        dict(base, symbol="SYNA250620C00010000", qty=1.0, gain=-20.0, direction="SHORT",
             days_held=12, grant_closed={"2024": {"units": 1.0}}),
        dict(base, symbol="SYNA250620C00010000", qty=2.0, gain=-5.0, direction="SHORT", days_held=3),
        dict(base, symbol="SYNB.TO", qty=0.0, gain=1.0, tainted=True, term="SHORT_TERM"),
        dict(base, symbol="", qty=1.0, gain=9.0),
        dict(base, symbol="F:SYNMH6.US", qty=1.0, gain=4.05, fee=1.1, currency=None),
    ]
    manual = [dict(base, symbol="SYNC.TO", qty=2.0, proceeds=10.0, direction="SHORT", own_move_from="A2")]
    return [
        {"transactions": rows},
        {"transactions": rows, "manual_reporting_required": manual, "inventory": [{"symbol": "SYNA.TO", "qty": 3}],
         "summary": {"year": "2024", "wash_solver_converged": False, "wash_solver_iterations": 7,
                     "total_fees_by_currency": {"CAD": {"total": 9.0, "options": 1.0}}}},
        {"transactions": rows[:1], "manual_reporting_required": None, "summary": {}},
        {"transactions": []},
        {},
    ]


class TestSumGains(unittest.TestCase):
    """summarize_gains reads the Result: the same aggregates as before."""

    def _same(self, doc, where):
        from taxjson.lib.gain_summary import summarize_gains
        old = old_summarize_gains(copy.deepcopy(doc))
        self.assertEqual(_typed(summarize_gains(doc)), _typed(old), where)
        self.assertEqual(_typed(summarize_gains(Result.from_json(doc))), _typed(old), where)

    def test_the_recorded_files(self):
        for p in _gains_files():
            with self.subTest(str(p.relative_to(TESTS))):
                self._same(json.loads(p.read_text(encoding="utf-8")), p.name)

    def test_the_books(self):
        for name, txs, shel, country in _books():
            with self.subTest(name):
                self._same(json.loads(json.dumps(_run(txs, shel, country))), name)

    def test_odd_rows(self):
        for i, doc in enumerate(_odd_docs()):
            with self.subTest(i):
                self._same(doc, i)

    def test_the_command_on_odd_documents(self):
        """TJ-2859: parts the report does not read, and empty ones, give
        the report as before; a row that is no object is one line and
        exit 2, not a traceback."""
        import subprocess

        from taxjson.lib.dispatch import python_module_argv

        def run(text):
            r = subprocess.run(python_module_argv("taxjson.bin.taxjson_sum_gains", ["--json"]),
                               input=text, capture_output=True, text=True)
            r.stderr = " ".join(r.stderr.split())
            return r
        for doc in ({"transactions": {}}, {"transactions": [], "inventory": {}},
                    {"transactions": [], "wash_sales": {"a": 1}}, {"transactions": [], "by_ticker": [1]}):
            with self.subTest(doc=doc):
                r = run(json.dumps(doc))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(json.loads(r.stdout)["ticker_stats"], {})
        for text, said in (('{"transactions": [null]}', '<stdin>: "transactions" row 0 is an object, not NoneType'),
                           ('{"transactions": [], "summary": [1]}', '<stdin>: "summary" is an object, not list'),
                           ('{"transactions": [[1, 2]]}', '<stdin>: "transactions" row 0 is an object, not list')):
            with self.subTest(text=text):
                r = run(text)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn(said, r.stderr)
                self.assertIn("re-run `tjs run`", r.stderr)

    def test_a_null_qty_beside_a_fee_is_read_as_0(self):
        """TJ-2858: `"qty": null` on a row with a fee stopped the old
        code with a TypeError (`float(None)`); the units count 0."""
        from taxjson.lib.gain_summary import summarize_gains
        doc = {"transactions": [{"symbol": "SYNX.TO", "action": "DIVIDEND", "dividend": 5.0, "qty": None,
                                 "fee": 1.0, "currency": "CAD", "date": "2024-03-01"}]}
        with self.assertRaises(TypeError):
            old_summarize_gains(copy.deepcopy(doc))
        out = summarize_gains(doc)
        self.assertEqual(out["returns_by_asset"]["Stocks"]["CAD"]["fee_shares"], [0.0])
        self.assertEqual(out["total_fees"], {"CAD": 1.0})


# --- the code before step 6c(1), verbatim but for four one-line ifs split ---

def old_summarize_gains(data: Dict[str, Any]) -> Dict[str, Any]:
    ticker_stats: Dict[str, Dict] = {}
    returns_by_asset: Dict[str, Dict] = {} # asset_type -> currency -> {dollars: [], days: [], fees: [], fee_shares: []}
    total_fees: Dict[str, float] = {}
    option_fees: Dict[str, float] = {}
    
    # We rebuild the summary from the transaction list to ensure year-filtering and currency are correct.
    transactions = data.get('transactions', [])
    target_year = data.get('summary', {}).get('year') or 'all'
    # An option whose root names no share listing but one class share
    # (SAMPLD...TO with SAMPLD.B.TO shares) groups under that share (S040-11).
    from taxjson.lib.ticker_map import class_share_aliases
    # Held shares (inventory) too (re-audit A2-0715).
    _aliases = class_share_aliases(
        t.get('symbol') for t in list(transactions)
        + list(data.get('inventory') or []) if isinstance(t, dict))
    # Grant-timing writes not yet paired with a buy-back:
    # (account, symbol, currency) -> [[bucket, dollars index, open
    # units, tick_stats, hold_days index], ...] (A2-1114). Per account:
    # a buy-back closes its own account's write, never another's
    # (2026-10-24 audit B35).
    open_writes: Dict[Any, list] = {}
    from taxjson.lib.futures import is_plain_future

    for tx in transactions:
        symbol = tx.get('symbol')
        if not symbol:
            continue

        is_opt = is_option_ticker(symbol)
        # One classifier for the returns and the fees: a futures row's
        # gain and fee land in the same Futures block (audit B34).
        asset_type = ('Options' if is_opt else
                      'Futures' if is_plain_future(symbol) else 'Stocks')
        base_ticker = get_base_ticker(symbol) if is_opt else symbol
        if base_ticker is None:
            base_ticker = symbol
        if is_opt:
            base_ticker = _aliases.get(base_ticker.upper(), base_ticker)
        
        # If the gain entry is missing currency, bucket it under '?' rather
        # than silently mis-attributing to CAD. The engine populates this
        # field for every Canada/USA gain, so missing means upstream parser
        # didn't set it on the source transaction.
        currency = tx.get('currency') or '?'
        
        if base_ticker not in ticker_stats:
            ticker_stats[base_ticker] = {}
        if currency not in ticker_stats[base_ticker]:
            ticker_stats[base_ticker][currency] = {
                'cap': 0.0, 'opt': 0.0, 'div': 0.0, 'pil': 0.0, 'cost': 0.0, 'proceeds': 0.0,
                'hold_days': [], 'trade_count': 0
            }
        
        tick_stats = ticker_stats[base_ticker][currency]
        
        if tx.get('action') == 'DIVIDEND':
            div = float(tx.get('dividend', 0) or 0)
            tick_stats['div'] += div
        elif tx.get('action') == 'DIVIDEND_IN_LIEU':
            # PIL: ticker-associated but tax-bucketed as ordinary income, not
            # eligible dividend. Separate column so it contributes to total
            # profit without inflating the T5/Schedule B dividend total.
            pil = float(tx.get('pil', 0) or 0)
            tick_stats['pil'] = tick_stats.get('pil', 0.0) + pil
            # Compensation paid on a short (US-INC-04), whatever the
            # security also received: no income reduction (TJ-1060).
            if tx.get('paid_on_short') and pil < 0:
                tick_stats['pil_paid'] = tick_stats.get('pil_paid', 0.0) - pil
            # A Canadian s.260 payment in lieu (a Canadian dealer, a
            # Canadian issuer) is a taxable dividend: the gains run
            # carries its amount as 'dividend' (lib/income_dating).
            tick_stats['div'] += float(tx.get('dividend', 0) or 0)
        else:
            cost = float(tx.get('cost', 0) or 0)
            proceeds = float(tx.get('proceeds', 0) or 0)
            gain = float(tx.get('gain', 0) or 0)
            days = int(tx.get('days_held', 0) or 0)

            tick_stats['cost'] += cost
            tick_stats['proceeds'] += proceeds
            if is_opt:
                tick_stats['opt'] += gain
            else:
                tick_stats['cap'] += gain

            # Aggregate for statistics
            if asset_type not in returns_by_asset:
                returns_by_asset[asset_type] = {}
            if currency not in returns_by_asset[asset_type]:
                returns_by_asset[asset_type][currency] = {'dollars': [], 'days': [], 'fees': [], 'fee_shares': []}
            bucket = returns_by_asset[asset_type][currency]
            qty = abs(float(tx.get('qty', 0) or 0))

            def _trade(dollars, held):
                bucket['dollars'].append(dollars)
                bucket['days'].append(held)
                tick_stats['hold_days'].append(held)
                tick_stats['trade_count'] += 1
                return (len(bucket['dollars']) - 1,
                        len(tick_stats['hold_days']) - 1)

            if tx.get('grant'):
                # A grant-timing WRITE record (s.49(1)) and its buy-back
                # are ONE trade (S040-12: counting both doubled TRADES):
                # the write is the trade, its premium the result until a
                # same-year buy-back folds its cost in below. Leaving the
                # write out (as S040-12 did) dropped every premium from
                # the per-asset block — an expired write vanished and a
                # bought-back one showed only its loss (A2-1114).
                _bi, _hi = _trade(gain, 0)
                open_writes.setdefault(
                    (tx.get('account') or '', symbol, currency), []).append(
                    [bucket, _bi, qty, tick_stats, _hi])
            else:
                # Units of this close that buy back a grant write of the
                # SAME year (the engine's grant_closed; a file written
                # before it: any open write of the series) fold into
                # that write's trade, FIFO. A buy-back of an earlier
                # year's write is a trade of its own.
                fold = 0.0
                if is_opt and tx.get('direction') == 'SHORT':
                    gc = tx.get('grant_closed')
                    if isinstance(gc, dict):
                        _y = str(tx.get('date_settle') or tx.get('date') or '')[:4]
                        fold = sum(float((v or {}).get('units') or 0.0)
                                   for y, v in gc.items() if str(y) == _y)
                    else:
                        fold = qty
                folded = 0.0
                for w in open_writes.get(
                        (tx.get('account') or '', symbol, currency), []):
                    if fold - folded <= 1e-9:
                        break
                    take = min(w[2], fold - folded)
                    if take <= 1e-9:
                        continue
                    w[0]['dollars'][w[1]] += gain * take / qty if qty else gain
                    w[0]['days'][w[1]] = days
                    w[3]['hold_days'][w[4]] = days
                    w[2] -= take
                    folded += take
                rest = qty - folded
                if rest > 1e-9 or qty <= 1e-9:
                    _trade(gain * rest / qty if qty > 1e-9 else gain, days)

            term = tx.get('term')
            if term == 'SHORT_TERM':
                tick_stats['st_gain'] = tick_stats.get('st_gain', 0.0) + gain
            elif term == 'LONG_TERM':
                tick_stats['lt_gain'] = tick_stats.get('lt_gain', 0.0) + gain

        # Track fees if present in the gain entry
        fee = float(tx.get('fee', 0) or 0) + float(tx.get('commission', 0) or 0)
        # != 0, not > 0: fee rebates/corrections arrive negative and
        # must net out of TOTAL TRADING FEES.
        if fee != 0:
            total_fees[currency] = total_fees.get(currency, 0.0) + fee
            if asset_type not in returns_by_asset:
                returns_by_asset[asset_type] = {}
            if currency not in returns_by_asset[asset_type]: 
                returns_by_asset[asset_type][currency] = {'dollars': [], 'days': [], 'fees': [], 'fee_shares': []}
            
            returns_by_asset[asset_type][currency]['fees'].append(fee)
            returns_by_asset[asset_type][currency]['fee_shares'].append(abs(float(tx.get('qty', 0))))
            
            if is_opt:
                option_fees[currency] = option_fees.get(currency, 0.0) + fee

    # If the engine already aggregated total fees across the RAW transaction
    # list (both buy and sell sides), prefer that — gain entries only carry
    # sell-side fees because buy fees are baked into cost basis. The summary
    # total is the right number for trading-efficiency analysis.
    summary_fees = (data.get('summary') or {}).get('total_fees_by_currency') or {}
    if summary_fees:
        total_fees = {curr: bucket.get('total', 0.0) for curr, bucket in summary_fees.items()}
        option_fees = {curr: bucket.get('options', 0.0) for curr, bucket in summary_fees.items()}

    return {
        'ticker_stats': ticker_stats,
        'returns_by_asset': returns_by_asset,
        # Dispositions carrying a missing-history OPENING_BALANCE basis.
        # PIPELINE-written gains files carry them in a separate
        # manual_reporting_required section (run_gains strips the rows
        # and pops the flag), so the aggregates above EXCLUDE them;
        # only raw engine output still carries in-line tainted rows.
        # Count both homes so consumers can warn either way
        # (round-five audit: the in-line count was always 0 for
        # pipeline files, silencing `tjs sum`'s warning).
        'tainted_count': sum(1 for t in transactions
                             if t.get('tainted') and 'gain' in t),
        # Pipeline files carry tainted rows OUT-of-line: excluded from
        # every aggregate above, so they get their own count (and the
        # consumer a different warning).
        'tainted_routed': len(
            data.get('manual_reporting_required') or []),
        # The routed rows themselves, for the .sum's MANUAL REPORTING
        # section (R1-320: the .sum you file from dropped them without
        # a word while `tjs sum` pointed at a section nobody wrote).
        'manual_rows': [
            # `direction`: a short covered with its short sale before
            # the data lists its cover cost, never as proceeds
            # (form_export.manual_figures, backlog TJ-0497).
            # `own_move_from`: units moved in from another of your
            # accounts, their cost in the blended figures (TJ-0279).
            {k: r.get(k) for k in ('symbol', 'date', 'date_settle',
                                   'qty', 'proceeds', 'account',
                                   'direction', 'own_move_from',
                                   'currency')}
            for r in (data.get('manual_reporting_required') or [])],
        'total_fees': total_fees,
        'option_fees': option_fees,
        'total_year': target_year,
        # Carry the wash-sale solver's convergence flag through so the
        # report can warn when the numbers may be unreliable. Default True
        # so engines/old files that don't emit it never spuriously warn.
        'wash_solver_converged': (data.get('summary') or {}).get('wash_solver_converged', True),
        'wash_solver_iterations': (data.get('summary') or {}).get('wash_solver_iterations'),
    }


if __name__ == "__main__":
    unittest.main()
