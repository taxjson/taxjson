"""Step 6c(2): handoff reads the gains through the typed Result.

`dispositions` and `_lots_by_symbol` (lib/handoff) read gain rows and a US
run's `lots_at` lots through model/result (`GainRow`, `Lot`), the year-end
check's double-report loop too. Pinned against the code before it, kept
verbatim below: `dispositions` over every recorded gains file, and
`_lots_by_symbol` over US gains runs with `--lots-at` on the fuzzers'
books and the golden books' inputs. A lots file the old code read with a
traceback is a BooksError now: a null or wrong-shape `lots_at` the
refusal it gave a file with none, a lot that does not read (no symbol, a
null qty) one naming the lot. The row funnel checks every money value a
GainRow reads (TJ-2879), so a gain row's typed reads cannot fail."""
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from taxjson.lib import handoff
from taxjson.lib.handoff import BooksError, _work_doc
from taxjson.lib.json_input import _NUM_FIELDS
from taxjson.model import money as M
from taxjson.model.result import GainRow, Holding, Lot, Result, _Field, number
import _paths

TESTS = _paths.TESTS


def _typed(x):
    if isinstance(x, dict):
        return ("dict", [(k, _typed(v)) for k, v in x.items()])
    if isinstance(x, (list, tuple)):
        return (type(x).__name__, [_typed(v) for v in x])
    if isinstance(x, float):
        return ("float", "nan" if math.isnan(x) else struct.pack(">d", x))
    return (type(x).__name__, repr(x))


def _gains_files():
    return sorted(p for p in (TESTS / "golden").rglob("*_gains*.json")
                  if isinstance(json.loads(p.read_text(encoding="utf-8")), dict))


def _us_lots_docs():
    """(name, gains document) of US runs with `--lots-at`: the fuzzers'
    books and the golden books' inputs, each on three days."""
    from taxjson.lib.tx_load import coerce_transaction_row
    from taxjson.lib.pipeline import GainsRequest, run_gains
    from _support.engine_fuzz_books import make_book
    from _support.engine_transfer_fuzz_books import make_transfer_book
    books = []
    for p in sorted((TESTS / "golden").glob("*/inputs.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        rows = {side: [coerce_transaction_row(r, i, p.parent.name) for i, r in enumerate(d[side])]
                for side in d}
        books.append((f"golden {p.parent.name}", rows.get("taxable", []), rows.get("sheltered", [])))
    for seed in range(60):
        books.append((f"make_book {seed}",) + tuple(make_book(seed)))
    for seed in range(20):
        books.append((f"make_transfer_book {seed}",) + tuple(make_transfer_book(seed)[:2]))
    out = []
    for name, txs, shel in books:
        dates = sorted(str(t.date)[:10] for t in txs if getattr(t, "date", None))
        if not dates:
            continue
        for day in (dates[0], dates[len(dates) // 2], dates[-1]):
            for per_account in (False, True):
                req = GainsRequest(country="usa", taxable=True, lots_at=day,
                                   per_account_basis=per_account)
                out.append((f"{name} {day} {per_account}",
                            run_gains(list(txs), list(shel), (), req)))
    return out


# --- the code before step 6c(2), verbatim but for its money cuts: `M.shown`
# (half up from the file's figure since step 6h(4); round() until then), as
# handoff cuts them, so a 4-place figure at a half cent (-101.925) reads
# alike in both ---------------------------------------------------------

def old_lots_by_symbol(gains: Path, names: List[str]
                    ) -> Dict[str, List[Dict[str, Any]]]:
    """{symbol: [lot]} of a gains run's `lots_at` (taxjson-gains
    --lots-at): each security's lots by account, in FIFO order within
    each account."""
    try:
        doc = json.loads(gains.read_text(encoding="utf-8"))
        rows = doc["lots_at"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise BooksError(f"the gains engine wrote no lots for "
                         f"{', '.join(names)} ({e}): the taxjson-gains "
                         f"on the PATH is not this taxjson's")
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["symbol"]), []).append({
            "account": str(r.get("account") or ""),
            "date": str(r.get("date") or "")[:10],
            "holding_start": str(r.get("holding_start")
                                 or r.get("date") or "")[:10],
            "qty": round(float(r["qty"]), 8),
            "basis": M.shown(float(r["basis"]), 2),
            "deferred": M.shown(float(r.get("deferred") or 0.0), 2)})
    for sym in out:
        # A stable sort: FIFO order kept within each account.
        out[sym].sort(key=lambda x: x["account"])
    return out


def old_dispositions(gains_files: Dict[str, Path], year: int
                 ) -> List[Dict[str, Any]]:
    out = []
    for acct, p in sorted(gains_files.items()):
        if not Path(p).exists():
            continue
        # An unreadable gains file left its sales out of the lock in
        # silence (A2-1396).
        doc = _work_doc(Path(p))
        for t in doc.get("transactions", []):
            if t.get("gain") is None or t.get("action"):
                continue
            out.append({
                "account": t.get("account") or acct,
                "symbol": t.get("symbol"), "date": t.get("date"),
                "date_settle": t.get("date_settle") or t.get("date"),
                "qty": float(t.get("qty") or 0.0),
                "proceeds": M.shown(float(t.get("proceeds") or 0.0), 2),
                "cost": M.shown(float(t.get("cost") or 0.0), 2),
                "gain": M.shown(float(t.get("gain") or 0.0), 2),
                "denied": M.shown(float(t.get("disallowed_amount") or 0.0), 2),
            })
    return out


# --- the tests ------------------------------------------------------------

def _outcome(f):
    try:
        return ("value", _typed(f()))
    except BooksError as e:
        return ("BooksError", str(e))


class TestDispositions(unittest.TestCase):
    def test_every_recorded_gains_file(self):
        files = _gains_files()
        self.assertGreater(len(files), 60)
        n = 0
        for p in files:
            with self.subTest(str(p.relative_to(TESTS))):
                for year in (2023, 2024, 2025):
                    new = handoff.dispositions({"acct": p}, year)
                    self.assertEqual(_typed(new), _typed(old_dispositions({"acct": p}, year)))
                    n += len(new)
        self.assertGreater(n, 300)

    def test_a_missing_file_and_a_damaged_one(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(handoff.dispositions({"a": Path(td) / "none_gains.json"}, 2024), [])
            bad = Path(td) / "bad_gains.json"
            bad.write_text('{"transactions": [1], "inventory": [], "summary": {}}', encoding="utf-8")
            for f in (handoff.dispositions, old_dispositions):
                with self.subTest(f.__name__), self.assertRaises(BooksError):
                    f({"a": bad}, 2024)


class TestLotsBySymbol(unittest.TestCase):
    def _both(self, doc):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "g_full_gains.json"
            p.write_text(json.dumps(doc), encoding="utf-8")
            return (_outcome(lambda: handoff._lots_by_symbol(p, ["acct"])),
                    _outcome(lambda: old_lots_by_symbol(p, ["acct"])))

    def test_us_runs_with_lots_at(self):
        docs = _us_lots_docs()
        self.assertGreater(len(docs), 400)
        lots = 0
        for name, doc in docs:
            with self.subTest(name):
                self.assertIn("lots_at", doc)
                new, old = self._both(doc)
                self.assertEqual(new, old)
                lots += len(doc["lots_at"])
                for row, raw in zip(Result.from_json(doc).lots_at, doc["lots_at"]):
                    self.assertIsInstance(row, Lot)
                    self.assertEqual(float(row.quantity), raw["qty"])
                    self.assertEqual(float(row.basis), raw["basis"])
                    self.assertEqual(float(row.deferred), raw["deferred"])
                    for attr in ("symbol", "account", "date", "holding_start"):
                        self.assertIs(getattr(row, attr), raw.get(attr))
        self.assertGreater(lots, 500)

    def test_what_the_old_code_refused_or_stopped_on(self):
        lot = {"account": "", "symbol": "SYNX", "date": "2025-01-02", "holding_start": "2025-01-02",
               "qty": 3.0, "basis": 30.5, "deferred": 0.0}
        refused = [{}, [], {"transactions": []}]
        no_lots = "the gains engine wrote no lots for acct ("
        bad_lot = "the gains engine's lot 1 for acct does not read ("
        stopped = [({"lots_at": None}, no_lots), ({"lots_at": "x"}, no_lots)] + [
                   ({"lots_at": [lot, bad]}, bad_lot + why) for bad, why in (
                       (None, "it is null, not an object"), ([1], "it is list, not an object"),
                       ("x", "it is str, not an object"),
                       ({k: v for k, v in lot.items() if k != "symbol"}, '"symbol" is missing'),
                       ({k: v for k, v in lot.items() if k != "qty"}, '"qty" is missing'),
                       (dict(lot, qty=None), '"qty" is None, not a number'),
                       (dict(lot, qty=""), '"qty" is \'\', not a number'),
                       (dict(lot, qty={}), '"qty" is {}, not a number'),
                       ({k: v for k, v in lot.items() if k != "basis"}, '"basis" is missing'),
                       (dict(lot, basis=[]), '"basis" is [], not a number'),
                       (dict(lot, basis="abc"), '"basis" is \'abc\', not a number'),
                       (dict(lot, deferred=[1]), '"deferred" is [1], not a number'))]
        for doc in refused:
            with self.subTest(doc=doc):
                new, old = self._both(doc)
                self.assertEqual(new[0], "BooksError")
                self.assertEqual(old[0], "BooksError")
        for doc, message in stopped:
            with self.subTest(doc=doc):
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "g_full_gains.json"
                    p.write_text(json.dumps(doc), encoding="utf-8")
                    with self.assertRaises((KeyError, TypeError, ValueError)):
                        old_lots_by_symbol(p, ["acct"])
                    new = _outcome(lambda: handoff._lots_by_symbol(p, ["acct"]))
                self.assertEqual(new[0], "BooksError", new)
                self.assertTrue(new[1].startswith(message), new[1])
                self.assertTrue(new[1].endswith("the taxjson-gains on the PATH is not this taxjson's"), new[1])
        for doc in ({"lots_at": [lot, dict(lot, account="B", qty=-1.0)]},
                    {"lots_at": [dict(lot, symbol=None), dict(lot, deferred=None, account=None)]}):
            with self.subTest(doc=doc):
                new, old = self._both(doc)
                self.assertEqual(new[0], "value")
                self.assertEqual(new, old)



class TestTheFieldCheck(unittest.TestCase):
    """The row funnel (json_input `check_row_types`) checks every money
    value a GainRow or Holding reads, so handoff's typed reads of a file
    `_work_doc` read never raise (TJ-2879)."""

    def test_every_typed_money_value_is_checked(self):
        for cls in (GainRow, Holding):
            keys = [f.key for f in vars(cls).values() if isinstance(f, _Field) and f.read is number]
            self.assertGreater(len(keys), 1)
            for key in keys:
                with self.subTest(cls=cls.__name__, key=key):
                    self.assertIn(key, _NUM_FIELDS)

    def test_a_text_raw_gain_is_refused_naming_the_file(self):
        row = {"symbol": "SYNX", "date": "2025-03-04", "qty": 2.0, "proceeds": 20.0,
               "cost": 10.0, "gain": 10.0}
        with tempfile.TemporaryDirectory() as td:
            for key in ("raw_gain", "permanently_disallowed", "deferred_loss_recovered"):
                with self.subTest(key=key):
                    p = Path(td) / "a_gains.json"
                    p.write_text(json.dumps({"transactions": [dict(row, **{key: "ten"})],
                                             "inventory": [], "summary": {}}), encoding="utf-8")
                    with self.assertRaises(BooksError) as cm:
                        handoff.dispositions({"a": p}, 2025)
                    self.assertIn(f"{key} is 'ten', not a number", str(cm.exception))
                    p.write_text(json.dumps({"transactions": [dict(row, **{key: 1.5})],
                                             "inventory": [], "summary": {}}), encoding="utf-8")
                    self.assertEqual(len(handoff.dispositions({"a": p}, 2025)), 1)

    def test_an_integer_too_large_for_a_float_is_refused(self):
        """TJ-2886: valid JSON a float cannot hold was an OverflowError."""
        row = {"symbol": "SYNX", "date": "2025-03-04", "qty": 2.0, "proceeds": 20.0,
               "cost": 10.0, "gain": 10.0}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            for key in ("raw_gain", "qty", "proceeds"):
                with self.subTest(key=key):
                    p.write_text(json.dumps({"transactions": [dict(row, **{key: 10 ** 400})],
                                             "inventory": [], "summary": {}}), encoding="utf-8")
                    with self.assertRaises(BooksError) as cm:
                        handoff.dispositions({"a": p}, 2025)
                    self.assertIn(f"{key} is an integer of 401 digits, too large to read",
                                  str(cm.exception))
            p.write_text(json.dumps({"transactions": [dict(row, raw_gain=10 ** 300)],
                                     "inventory": [], "summary": {}}), encoding="utf-8")
            self.assertEqual(len(handoff.dispositions({"a": p}, 2025)), 1)

if __name__ == "__main__":
    unittest.main()
