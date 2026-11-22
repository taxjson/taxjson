"""model/evidence.py over the rows the golden project books' runs write in
work/: each parser's, merge's and stage's row files, every row read as the
loader reads it, and each packed evidence field read back to itself through
its codec (step 6b; corp_cash and settle_native since 6b(2)).

What stays of tests/test_model_events_books.py, which also round-tripped
each row through model/events.py (retired in PLAN step 14)."""
import json
import unittest
from pathlib import Path

import _golden
from taxjson.lib.tx_load import coerce_transaction_row
from taxjson.model import evidence as V

PROJECT_BOOKS = sorted(b for b in _golden.books() if _golden.meta(b)["kind"] == "project")

# Each packed field's codec (model/evidence): what a writer made reads
# back to itself.
_CODECS = {
    "broker_lots": lambda s: (None if V.parse_broker_lots(s) is None
                              else V.format_broker_lots(V.parse_broker_lots(s))),
    "commission_refunds": lambda s: V.format_commission_refunds(V.parse_commission_refunds(s)),
    "opening_cut_legs": lambda s: V.format_cut_legs(V.parse_cut_legs(s)),
    "corp_cash": lambda s: V.format_corp_cash(V.parse_corp_cash(s)),
    "settle_native": lambda s: (None if V.parse_settle_native(s) is None
                                else V.format_settle_native(V.parse_settle_native(s))),
}


def _row_lists(work: Path):
    """{file: rows} for the row files in a run's work/: a JSON list of
    rows, or a document's `transactions`, each with an action and a date
    and no gain (the gains files are the engines' output, not rows)."""
    out = {}
    for p in sorted(work.rglob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        rows = d if isinstance(d, list) else d.get("transactions") if isinstance(d, dict) else None
        if not isinstance(rows, list) or not rows:
            continue
        if not all(isinstance(r, dict) and "action" in r and "date" in r for r in rows):
            continue
        if any("gain" in r or "acb" in r for r in rows):
            continue
        out[p] = rows
    return out


def _case(book):
    def test(self):
        with _golden.project_run(book, _golden.meta(book)) as (root, _env, _r, _before):
            files = {}
            for work in sorted(root.rglob("work")):
                files.update(_row_lists(work))
            self.assertTrue(files, f"{book}: its run wrote no row file in work/")
            packed = set()
            for p, rows in files.items():
                where = p.relative_to(root)
                for i, row in enumerate(rows):
                    tx = coerce_transaction_row(row, i, str(where))
                    for field, codec in _CODECS.items():
                        text = getattr(tx, field)
                        if text and text != V.BROKER_LOTS_INVALID:
                            packed.add(field)
                            self.assertEqual(codec(text), text, f"{where} row {i}: {field}")
            if book == "evidence":
                # The book that holds the packed fields (tests/golden/README.md).
                self.assertEqual(packed, set(_CODECS))
    return test


for _book in PROJECT_BOOKS:
    _name = "TestWorkRows_" + _book.replace("-", "_")
    globals()[_name] = type(_name, (unittest.TestCase,), {"test_every_row": _case(_book)})


class TestTheBooks(unittest.TestCase):
    def test_the_project_books_are_read(self):
        self.assertGreaterEqual(len(PROJECT_BOOKS), 5, PROJECT_BOOKS)
        # The packed fields' book: renamed or gone, its check would pass
        # on nothing (tests/golden/README.md).
        self.assertIn("evidence", PROJECT_BOOKS)


if __name__ == "__main__":
    unittest.main()
