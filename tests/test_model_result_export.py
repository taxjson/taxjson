"""Step 6c(3): the export group reads gains rows through typed views.

form_export, export, split_gains and carryover read a gains row's units
and money through model/result views over the document's own dicts
(`GainRow(e)`, `Holding(item)`); the dicts flow on to other commands as
they were. Two locks.

Here, form_export's converted functions, export's `--report` sums and
filters, split_gains' per-account split (without a base book) and
carryover's yearly nets run twice over every recorded gains file and
runs of the golden and fuzz books: with the views, and with a
raw reader in their place that returns each key's value as held. That
pins the view, not the call site: both runs share each site's own `or 0`
(where export's `.get(k, 0)` became `or 0`, the TJ-2891 fix, tested on
its own here) and read the same key.

Which key a site reads is held elsewhere: a wrong key fails the golden
books' views (form-export's Schedule 3 cost read from `fee`, split_gains'
blended share and realized gain), a feature's tests (split_gains' own
shorts: test_marked_shorts; export's blended fill:
features.reports.test_holdings_snapshot_read_back; carryover's excluded
rows: rules.us.rpt.test_us_carryover_and_crypto_year_end_records), or, here, export's count of the inputs holding
a symbol (TJ-1031).

A view reads an integer too large for a float as its Decimal, whose
float() is inf where float() of the int raised: the row funnel refuses
it (TJ-2886), and export's stdin document goes through the funnel too
(TJ-2899)."""
import argparse
import contextlib
import io
import json
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taxjson.bin import taxjson_carryover as CO
from taxjson.bin import taxjson_export as EX
from taxjson.bin import taxjson_form_export as FE
from taxjson.lib import tax_forms
from taxjson.bin import taxjson_split_gains as SG
from taxjson.lib.json_input import gains_row_kind
from taxjson.model.result import GainRow, Holding
from _support.view_lock import _docs, _manual_of, _both


class TestFormExport(unittest.TestCase):
    def test_every_gains_file(self):
        docs = _docs()
        self.assertGreater(len(docs), 60)
        n = 0
        for name, doc in docs:
            rows = [r for r in doc.get("transactions") or [] if isinstance(r, dict)]
            entries = [r for r in rows if gains_row_kind(r) == "disposition"]
            manual = [r for r in doc.get("manual_reporting_required") or [] if isinstance(r, dict)]
            manual += [r for r in rows if gains_row_kind(r) == "manual"] + _manual_of(entries)
            n += len(entries) + len(manual)
            with self.subTest(name):
                calls = [
                    lambda: tax_forms.build_schedule3(entries, None, "date"),
                    lambda: tax_forms.build_schedule3(entries, None, "date_settle"),
                    lambda: tax_forms.build_8949(entries),
                    lambda: tax_forms.build_8949(tax_forms.mark_crypto(entries)),
                    lambda: FE.manual_section(manual, "USD", country="usa"),
                    lambda: FE.manual_section(manual, "CAD", country="canada"),
                    lambda: [(tax_forms.grant_buyback_units(e, y), tax_forms.short_carried_loss(e),
                              FE.manual_figures(e)) for e in entries + manual
                             for y in (None, 2024, 2025)],
                ]
                for i, f in enumerate(calls):
                    new, old = _both((FE, tax_forms), ["GainRow"], f)
                    self.assertEqual(new, old, i)
                for form, build in (("8949", tax_forms.build_8949), ("schedule3", tax_forms.build_schedule3)):
                    def csv_text(build=build, form=form):
                        rep = dict(build(entries), form=form, manual_reporting_required=manual)
                        buf = io.StringIO()
                        FE._rows_csv(rep, buf)
                        return buf.getvalue()
                    new, old = _both((FE, tax_forms), ["GainRow"], csv_text)
                    self.assertEqual(new, old, form)
        self.assertGreater(n, 2500)


class TestExportReport(unittest.TestCase):
    def _args(self, **kw):
        base = dict(long=False, short=False, no_cad=False, no_equities=False,
                    no_futures=False, no_options=False, no_usd=False)
        return argparse.Namespace(**dict(base, **kw))

    def test_every_inventory(self):
        n = 0
        for name, doc in _docs():
            if not isinstance(doc.get("inventory"), list):
                continue
            n += len(doc["inventory"])
            for kw in ({}, {"long": True}, {"short": True}, {"no_options": True}):
                with self.subTest(name, **kw):
                    def run(kw=kw):
                        agg = {}
                        EX.process_data_report(json.loads(json.dumps(doc)), self._args(**kw), agg)
                        return agg
                    new, old = _both(EX, ["Holding"], run)
                    self.assertEqual(new, old)
        self.assertGreater(n, 1200)

    def test_a_null_quantity_or_cost_counts_as_zero(self):
        """TJ-2891: `"qty": null` stopped `--report` with a TypeError."""
        inv = [{"symbol": "ZZQ.US", "account": "a", "qty": None, "total_cost": 10.0, "currency": "USD"},
               {"symbol": "ZZR.US", "account": "a", "qty": 2.0, "total_cost": None, "currency": "USD"},
               {"symbol": "ZZS.US", "account": "a", "qty": 3.0, "total_cost": 30.0, "currency": "USD"}]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            p.write_text(json.dumps({"transactions": [], "inventory": inv, "summary": {"year": 2025}}),
                         encoding="utf-8")
            for extra in ([], ["--long"]):
                with self.subTest(extra=extra):
                    out = io.StringIO()
                    with mock.patch.object(sys, "argv", ["taxjson-export", "--report", *extra, str(p)]), \
                            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                        try:
                            EX.main()
                        except SystemExit as e:
                            self.assertIn(e.code, (0, None))
                    self.assertIn("ZZS.US", out.getvalue())
                    self.assertIn("ZZR.US", out.getvalue())
        agg = {}
        EX.process_data_report({"inventory": inv}, self._args(), agg)
        self.assertEqual((agg["ZZQ.US"]["qty"], agg["ZZQ.US"]["total_cost"]), (0.0, 10.0))
        self.assertEqual((agg["ZZR.US"]["qty"], agg["ZZR.US"]["total_cost"]), (2.0, 0.0))


    def test_a_zero_unit_row_holds_nothing(self):
        """main counts the inputs holding a symbol by each gains row's
        units (TJ-1031): a 0-unit row with a cost is no holder, so the
        snapshot's own base figure stays; a row with units is one, and the
        figure is left out."""
        import tomllib
        snap = ('[meta]\nholdings_count = 1\n[[holding]]\nsymbol = "SYN.US"\nquantity = 10.0\n'
                'currency = "USD"\ntotal_cost = 100.0\nbase_currency = "CAD"\n'
                'base_total_cost = 135.0\n')
        with tempfile.TemporaryDirectory() as td:
            a, g = Path(td) / "a.toml", Path(td) / "g_gains.json"
            a.write_text(snap, encoding="utf-8")
            for qty, kept in ((0.0, True), (2.0, False)):
                with self.subTest(qty=qty):
                    g.write_text(json.dumps({"inventory": [{"symbol": "SYN.US", "qty": qty,
                                                            "total_cost": 5.0, "currency": "USD"}]}),
                                 encoding="utf-8")
                    out = io.StringIO()
                    argv = ["taxjson-export", "--holdings-toml", "--base-currency", "CAD", str(a), str(g)]
                    with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), \
                            contextlib.redirect_stderr(io.StringIO()):
                        try:
                            EX.main()
                        except SystemExit as e:
                            self.assertIn(e.code, (0, None))
                    (h,) = [x for x in tomllib.loads(out.getvalue())["holding"] if x["symbol"] == "SYN.US"]
                    self.assertEqual("base_total_cost" in h, kept, h)


    def _stdin(self, doc, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("taxjson.lib.cli_diag.read_stdin_utf8", return_value=json.dumps(doc)), \
                mock.patch.object(sys, "argv", ["taxjson-export", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                EX.main()
                code = 0
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), " ".join(err.getvalue().split())

    def test_the_stdin_document_goes_through_the_row_funnel(self):
        """TJ-2899: a huge integer on stdin was a holding of `inf` units at
        exit 0; TJ-2901: text in a number field a bare ResultShapeError."""
        row = {"symbol": "ZZQ.US", "account": "a", "qty": 2.0, "total_cost": 20.0, "currency": "USD"}
        for bad, why in ((10 ** 400, "qty is an integer of 401 digits, too large to read"),
                         ("abc", "qty is 'abc', not a number")):
            for mode in ("--report", "--holdings-toml"):
                with self.subTest(bad=str(bad)[:5], mode=mode):
                    code, out, err = self._stdin({"inventory": [dict(row, qty=bad)]}, mode)
                    self.assertEqual(code, 2, err)
                    self.assertIn(f'<stdin>: "inventory" row 0 (ZZQ.US): {why}', err)
                    self.assertNotIn("inf", out)
        code, out, err = self._stdin({"inventory": [row]}, "--holdings-toml")
        self.assertEqual(code, 0, err)
        self.assertIn("ZZQ.US", out)

    def test_a_null_holding_qty_reads_as_absent(self):
        """TJ-2900: `--long` compared a null holding_qty with 0."""
        inv = [{"symbol": "ZZQ.US", "qty": 2.0, "holding_qty": None, "total_cost": 20.0,
                "currency": "USD"},
               {"symbol": "ZZR.US", "qty": -1.0, "holding_qty": None, "total_cost": -10.0,
                "currency": "USD"}]
        for flag, kept, dropped in (("long", "ZZQ.US", "ZZR.US"), ("short", "ZZR.US", "ZZQ.US")):
            with self.subTest(flag=flag):
                agg = {}
                EX.process_data_report({"inventory": inv}, self._args(**{flag: True}), agg)
                self.assertIn(kept, agg)
                self.assertNotIn(dropped, agg)


class TestSplitGains(unittest.TestCase):
    def test_every_gains_file_for_each_account(self):
        n = 0
        for name, doc in _docs():
            accounts = sorted({str(r.get("account")) for part in ("transactions", "inventory")
                               for r in doc.get(part) or [] if isinstance(r, dict) and r.get("account")})
            for account in accounts:
                n += 1
                with self.subTest(name, account=account):
                    new, old = _both(SG, ["GainRow", "Holding"],
                                     lambda: SG.split_for_account(json.loads(json.dumps(doc)), account, None))
                    self.assertEqual(new, old)
        self.assertGreater(n, 150)


class TestCarryover(unittest.TestCase):
    def test_yearly_nets_over_every_gains_file(self):
        for name, doc in _docs():
            for tax_date in ("trade", "settle"):
                for s1256 in (False, True):
                    with self.subTest(name, tax_date=tax_date, s1256=s1256):
                        new, old = _both(CO, ["GainRow"],
                                         lambda: CO.yearly_nets(doc, tax_date, s1256=s1256))
                        self.assertEqual(new, old)


class TestTheViews(unittest.TestCase):
    def test_a_view_reads_the_dict_itself(self):
        e = {"qty": 2.0, "proceeds": 3.5}
        row = GainRow(e)
        self.assertIs(row.fields, e)
        e["qty"] = 4.0
        self.assertEqual(float(row.quantity), 4.0)
        self.assertEqual(float(Holding({"total_cost": 1.25}).total_cost), 1.25)

    def test_a_huge_integer_is_the_funnels(self):
        """The one value the old float() and a view read differently:
        the funnel refuses it before a reader sees it (TJ-2886)."""
        with self.assertRaises(OverflowError):
            float(10 ** 400)
        self.assertEqual(float(GainRow({"qty": 10 ** 400}).quantity), math.inf)

    def test_or_zero_reads_as_the_old_float_did(self):
        """`float(view.x or 0.0)` against `float(e.get(x) or 0.0)` for every
        value the row funnel lets through (a number, or None)."""
        values = [None, 0, 0.0, -0.0, 1, -3, 2.5, -1e-9, 5e-324, 1e16, 1.2345678901234568e+16,
                  -1e300, 0.1 + 0.2, True, False]
        for v in values:
            for default in (0.0, 0):
                with self.subTest(v=v, default=default):
                    old = float(v or default)
                    new = float(GainRow({"proceeds": v}).proceeds or default)
                    self.assertEqual(struct.pack(">d", new), struct.pack(">d", old))
                    self.assertEqual(struct.pack(">d", abs(float(GainRow({"qty": v}).quantity or default))),
                                     struct.pack(">d", abs(old)))


if __name__ == "__main__":
    unittest.main()
