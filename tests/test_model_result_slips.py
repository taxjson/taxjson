"""Step 6c(5): the slip readers read gains rows through typed views.

slip_symbols `load_computed` (`tjs reconcile-slips`, and `tjs
slip-audit`'s per-account books through it) and t1135
`join_income_gains` (`tjs t1135`'s income and gain columns) read a gains
row's units and money through `GainRow(e)`, a view over the document's
own dict. Pinned as 6c(3) pinned the export group
(test_model_result_export): each runs over every recorded gains file and
the runs of the golden and fuzz books, with the views and with a raw
reader in their place, so a typed read gives what the raw read gave;
which key a site reads is held by the golden books' slip-audit and
t1135 views and the feature tests. Both read a file through
json_input `require_gains_doc`, the row checks, so a view never sees an
integer too large for a float, and its lists of rows as lists of
objects (TJ-2929, TJ-2939). A wrong key that passed every other test
(a one-line mutant of each converted read, run against the slip and
t1135 tests and the goldens) is pinned in TestTheKeys. slip_audit reads
base and native rows only: nothing of it changes."""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_form_export as FE
from taxjson.bin import taxjson_reconcile_slips as RS
from taxjson.lib import slip_symbols, tax_forms
from taxjson.bin import taxjson_t1135 as T1135
from taxjson.lib.json_input import InputFileError, gains_row_kind
from _support.view_lock import _both, _docs, _main_year, _manual_of


def _year(doc):
    y = _main_year(doc)
    return int(y) if y else None


def _variants(doc):
    """The document, and the same with its dispositions routed as
    unknown-cost rows too (the recorded files hold few)."""
    entries = [r for r in doc.get("transactions") or [] if gains_row_kind(r) == "disposition"]
    yield "as written", doc
    if entries:
        yield "manual", dict(doc, manual_reporting_required=(doc.get("manual_reporting_required") or [])
                             + _manual_of(entries))


class TestLoadComputed(unittest.TestCase):
    def test_every_gains_file(self):
        n = 0
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            for name, doc in _docs():
                y = _year(doc)
                for kind, d in _variants(doc):
                    p.write_text(json.dumps(d), encoding="utf-8")
                    n += len(d.get("transactions") or []) + len(d.get("manual_reporting_required") or [])
                    for year in dict.fromkeys((y, None)):
                        for basis in ("settle", "trade"):
                            for country in ("canada", "usa"):
                                with self.subTest(name, kind=kind, year=year, basis=basis, country=country):
                                    new, old = _both((slip_symbols, tax_forms), ["GainRow"], lambda: slip_symbols.load_computed(
                                        [p], year, basis, country))
                                    self.assertEqual(new[0], "value", new)
                                    self.assertEqual(new, old)
        self.assertGreater(n, 4000)


def _paid(doc):
    """Each payment in lieu made negative: the compensation a short
    seller pays, which `short_paid` takes out (the recorded files hold
    none)."""
    def flip(r):
        if not isinstance(r, dict) or r.get("action") != "DIVIDEND_IN_LIEU":
            return r
        return dict(r, pil=-(abs(r.get("pil") or 0) + abs(r.get("dividend") or 0) + 1.5))
    return dict(doc, transactions=[flip(r) for r in doc.get("transactions") or []])


class TestJoinIncomeGains(unittest.TestCase):
    def test_every_gains_file(self):
        n = 0
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            for name, doc in _docs():
                y = _year(doc)
                if y is None:
                    continue
                for kind, d in list(_variants(doc)) + [("paid on a short", _paid(doc))]:
                    p.write_text(json.dumps(d), encoding="utf-8")
                    n += len(d.get("transactions") or [])
                    for basis in ("settle", "trade"):
                        for short_paid in (None, lambda *a: True):
                            with self.subTest(name, kind=kind, basis=basis, paid=short_paid is not None):
                                new, old = _both(T1135, ["GainRow"], lambda: T1135.join_income_gains(
                                    [p], y, tax_date=basis, short_paid=short_paid))
                                self.assertEqual(new[0], "value", new)
                                self.assertEqual(new, old)
        self.assertGreater(n, 2500)


class TestDamagedFiles(unittest.TestCase):
    """TJ-2929, TJ-2930: load_computed refuses a damaged gains file
    naming it, where it stopped with an AttributeError or a ValueError
    naming none (tjs prints an InputFileError in one line, exit 2)."""

    def _computed(self, **parts):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            p.write_text(json.dumps(dict({"transactions": [], "summary": {}}, **parts)), encoding="utf-8")
            return slip_symbols.load_computed([p], 2025, "settle", "canada")

    def test_the_row_lists_are_lists_of_objects(self):
        # In form_export's reader (json_input require_gains_doc), so
        # form-export and both reconcile readers refuse them (TJ-2937 to
        # TJ-2939); a damaged inventory too, though load_computed reads
        # none (TJ-2942: a damaged file is refused whole).
        for key in ("manual_reporting_required", "grant_nil_closes", "inventory"):
            for bad, why in ((["x"], " (row 0 is a string)"), ({"a": 1}, ""), (5, ""), (True, "")):
                with self.subTest(key=key, bad=bad):
                    with tempfile.TemporaryDirectory() as td:
                        p = Path(td) / "a_gains.json"
                        p.write_text(json.dumps({"transactions": [], "summary": {}, key: bad}),
                                     encoding="utf-8")
                        for read in (lambda: slip_symbols.load_computed([p], 2025, "settle", "canada"),
                                     lambda: RS.check_computed_currency([p], "CAD"),
                                     lambda: FE.load_manual_rows([p], 2025, "date_settle"),
                                     lambda: FE.load_dispositions([p], 2025, "date_settle")):
                            with self.assertRaises(InputFileError) as cm:
                                read()
                            self.assertIn(f'a_gains.json: "{key}" must be a list of JSON objects{why}',
                                          str(cm.exception))
        self.assertEqual(self._computed(manual_reporting_required=None, grant_nil_closes=None), {})

    def test_a_grant_closed_not_an_object_reads_as_none(self):
        # TJ-2940: as form-export's grant_buyback_units reads it.
        row = {"symbol": "ZZA250718P00018500.US", "date": "2025-05-02", "date_settle": "2025-05-02",
               "qty": 5, "proceeds": -133.15, "cost": 0.0, "gain": -133.15, "direction": "SHORT"}
        for bad in ("x", ["x"], 5):
            with self.subTest(bad=bad):
                self.assertEqual(self._computed(transactions=[dict(row, grant_closed=bad)]),
                                 self._computed(transactions=[row]))
                self.assertEqual(self._computed(grant_nil_closes=[dict(row, grant_closed=bad)]),
                                 self._computed(grant_nil_closes=[dict(row, grant_closed={})]))

    def test_a_capitalized_263h_is_a_number(self):
        row = {"symbol": "ZZQ.US", "date": "2025-05-02", "date_settle": "2025-05-02", "qty": -1,
               "proceeds": 10.0, "cost": 5.0, "gain": 5.0}
        for bad, why in (("abc", "capitalized_263h is 'abc', not a number"),
                         (10 ** 400, "capitalized_263h is an integer of 401 digits")):
            with self.subTest(bad=str(bad)[:5]), self.assertRaises(InputFileError) as cm:
                self._computed(transactions=[dict(row, capitalized_263h=bad)])
            self.assertIn(f'a_gains.json: "transactions" row 0 (ZZQ.US): {why}', str(cm.exception))
        self.assertEqual(self._computed(transactions=[dict(row, capitalized_263h=2.5)])["ZZQ"]
                         ["capitalized_263h"], 2.5)


class TestTheKeys(unittest.TestCase):
    """Sites a wrong key passed every other test of (6c(5)'s mutation
    run: the raw stand-in reads the attribute the view reads, so the
    lock holds the read, not the key)."""

    def _computed(self, rows=(), manual=()):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            p.write_text(json.dumps({"transactions": list(rows), "manual_reporting_required": list(manual),
                                     "summary": {}}), encoding="utf-8")
            return slip_symbols.load_computed([p], 2025, "settle", "canada")

    def _row(self, **kw):
        return dict({"date": "2025-09-03", "date_settle": "2025-09-03", "disallowed_amount": 0.0,
                     "commission": 0.0, "fee": 0.0, "direction": "LONG", "account": "margin"}, **kw)

    def test_a_canadian_futures_loss_is_its_raw_gain(self):
        # A loss partly denied: the slip shows it before the denial.
        e = self._row(symbol="F:SXFH6.TO", qty=-1, proceeds=-2010.0, cost=0.0, gain=-1500.0,
                      raw_gain=-2010.0, disallowed_amount=510.0, commission=10.0)
        rec = self._computed([e])["F:SXFH6"]
        self.assertEqual((rec["proceeds_net"], rec["cost"], rec["future_loss_rows"]), (0.0, 2010.0, 1))

    def test_a_grant_row_is_a_write_by_its_cost(self):
        # A grant row whose cost is a net debit beyond its commission is
        # no write: its commission is not added to the gross.
        e = self._row(symbol="ZZA250718P00018500.US", qty=5, proceeds=0.0, cost=512.40, gain=-512.40,
                      commission=10.0, direction="SHORT", grant=True)
        rec = self._computed([e])["ZZA250718P00018500"]
        self.assertEqual((rec["proceeds_net"], rec["proceeds_gross"]), (-512.4, -512.4))

    def test_an_unknown_cost_row_is_kept_by_its_units(self):
        manual = [self._row(symbol="ZZQ.US", qty=-5, proceeds=0.0),
                  self._row(symbol="ZZR.US", qty=0, proceeds=100.0)]
        out = self._computed(manual=manual)
        self.assertEqual({k: (v["qty"], v["rows"], v["tainted_rows"]) for k, v in out.items()},
                         {"ZZQ": (5.0, 1, 1)})


if __name__ == "__main__":
    unittest.main()
