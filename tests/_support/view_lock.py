"""Shared test helpers: the golden and fuzz books and their gains documents, the reader before step 6c(3) (RawRow), a typed walk of a value, a document's main year, manual rows and a run with the views against one with RawRow.

Shared from: test_model_result_export
"""
import collections
import contextlib
import functools
import io
import json
import math
import struct

from taxjson.lib.json_input import gains_row_kind
import _paths
from _support.view_patch import raw_views


TESTS = _paths.TESTS


class RawRow:
    """The code before 6c(3): `e.get(key)` as held, `qty` for `quantity`."""
    __slots__ = ("fields",)

    def __init__(self, fields):
        self.fields = fields

    def __getattr__(self, name):
        return self.fields.get("qty" if name == "quantity" else name)

    def __contains__(self, key):
        return key in self.fields

    def get(self, key, default=None):
        return self.fields.get(key, default)


def _typed(x):
    if isinstance(x, dict):
        return ("dict", [(k, _typed(v)) for k, v in x.items()])
    if isinstance(x, (list, tuple)):
        return (type(x).__name__, [_typed(v) for v in x])
    if isinstance(x, set):
        return ("set", sorted(repr(v) for v in x))
    if isinstance(x, float):
        return ("float", "nan" if math.isnan(x) else struct.pack(">d", x))
    return (type(x).__name__, repr(x))


def _books():
    """(name, taxable rows, sheltered rows): the golden books' inputs and
    the fuzzers' books (make_book seeds 0 to 39, make_transfer_book 0 to
    14), built afresh at each call: one caller's runs never see another
    caller's rows (a caller running a book twice, as _docs runs it in
    each country, shares them between its runs)."""
    from taxjson.lib.tx_load import coerce_transaction_row
    from _support.engine_fuzz_books import make_book
    from _support.engine_transfer_fuzz_books import make_transfer_book
    books = []
    for p in sorted((TESTS / "golden").glob("*/inputs.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        rows = {side: [coerce_transaction_row(r, i, p.parent.name) for i, r in enumerate(d[side])]
                for side in d}
        books.append((f"golden {p.parent.name}", rows.get("taxable", []), rows.get("sheltered", [])))
    for seed in range(40):
        books.append((f"make_book {seed}",) + tuple(make_book(seed)))
    for seed in range(15):
        books.append((f"make_transfer_book {seed}",) + tuple(make_transfer_book(seed)[:2]))
    return books


@functools.lru_cache(maxsize=None)
def _docs():
    """(name, gains document): every recorded gains file, and runs of the
    golden books' inputs and the fuzzers' books (_books) in each country."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    out = []
    for p in sorted((TESTS / "golden").rglob("*_gains*.json")):
        if "_views" in p.parts:     # a recorded command's output, not a gains file
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            out.append((str(p.relative_to(TESTS)), d))
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        for name, txs, shel in _books():
            for country in ("canada", "usa"):
                req = GainsRequest(country=country, taxable=True)
                doc = run_gains(list(txs), list(shel), (), req)
                # The runs end flat: holdings shaped from their sales.
                if not doc.get("inventory"):
                    doc["inventory"] = [
                        {"symbol": r.get("symbol"), "account": r.get("account"),
                         "qty": r.get("qty"), "total_cost": r.get("cost"),
                         "currency": r.get("currency") or "CAD"}
                        for r in doc.get("transactions") or []
                        if gains_row_kind(r) == "disposition"]
                out.append((f"{name} {country}", doc))
    return out


def _main_year(doc):
    """The year most of a document's rows are dated in ("2025"; a row's
    `date`, else its `date_settle`), or None when no row is dated."""
    years = collections.Counter(str(d)[:4] for r in doc.get("transactions") or []
                                if isinstance(r, dict) and (d := r.get("date") or r.get("date_settle")))
    return years.most_common(1)[0][0] if years else None


_MANUAL_KEYS = ("symbol", "date", "date_settle", "qty", "proceeds", "direction", "account",
                "own_move_from")


def _manual_of(entries):
    """Unknown-cost rows shaped as the pipeline routes them (gain and
    cost gone), one per disposition: the recorded files hold few."""
    return [{k: e.get(k) for k in _MANUAL_KEYS if k in e} for e in entries]


def _both(module, names, f):
    """(new, old) outcomes of f(): with the views, then with RawRow
    (raw_views: no view built by the old run). `module` may be a tuple:
    code moved out of a module reads its view there (step 8f:
    form_export's builders are lib/tax_forms')."""
    modules = module if isinstance(module, tuple) else (module,)

    def run():
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                return ("value", _typed(f()), err.getvalue())
        except SystemExit as e:
            return ("exit", str(e.code), err.getvalue())
    new = run()
    with raw_views(modules, names, RawRow):
        old = run()
    return new, old
