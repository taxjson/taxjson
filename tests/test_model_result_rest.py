"""Step 6c(6c3): the last gains readers outside the engine read rows
through model/result views.

lib/trade_stats (`tjs stats`: `_deemed_gains`, `economic_pnl`, the
covers, sales and denied tallies of `closed_trades`), lib/in_kind
`contribution_results` and pipeline's inventory reads (sanity's
holdings, stages `_blend_conservation_gaps`, inputs
`_warn_expired_open_options`) read a row's units and money through
`GainRow(e)` / `Holding(h)` views over the rows' own dicts. The rows come
through the row checks first, on the fields the views read: stats'
history and cover documents (cli/summaries `_stats_cover_doc`: their
short covers' and deemed rows' units and gain; a refused figure stops
the view, backlog TJ-3043, TJ-3054), the in-kind results' gains files
(stages `_checked_gains_docs`: a refused one is left out, said with the
in-kind message, TJ-3044, TJ-3061) and the blended pass's files
(`_blend_gap_messages`: the inventory quantities, TJ-3046, TJ-3053), all
through json_input `read_rows_doc`; sanity and the expired options check
each figure is finite themselves. lib/missing_history's warned losses stay engine reads (in
memory, inside the engine: TJ-3048). Pinned as the earlier
6c locks: `tjs stats` and `tjs sanity` run in-process in every golden
project after its `tjs run`, text and `--json`, with the views and with
a raw reader, and the two print the same; the raw side must have read
rows. TestTheKeys pins which key each figure reads on made-up rows where
the keys differ (the comparison cannot see it). lib/checklist's denial
total stays a raw read: its row check would add an import edge inside
the cycle step 8 shrinks (backlog)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _golden
from _support.view_lock import RawRow
from taxjson.cli import summaries as SU
from taxjson.cli.main import main as tjs_main
from taxjson.lib import in_kind as IK
from taxjson.lib import trade_stats as TS
from taxjson.model.result import GainRow
from taxjson.pipeline import sanity as SA
from taxjson.pipeline.stages import _blend_conservation_gaps, _blend_gap_messages

BOOKS = ("demo", "style-canada", "style-usa", "options", "crypto")
ARGVS = (["stats"], ["stats", "--all-history"], ["sanity"])


class _Counted(RawRow):
    """RawRow, counting the rows it was made over."""
    __slots__ = ()
    made = 0

    def __init__(self, fields):
        _Counted.made += 1
        super().__init__(fields)


def _tjs(root, argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", ["tjs", "-C", str(root), *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            tjs_main()
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class TestTheCommands(unittest.TestCase):
    def test_with_and_without_the_view(self):
        for book in BOOKS:
            m = _golden.meta(book)
            with _golden.project_run(book, m) as (root, env, _r, _b), \
                    mock.patch.dict(os.environ, env, clear=True):
                root = root / m.get("run_dir", ".")
                read = 0
                for base in ARGVS:
                    for argv in (base, base + ["--json"]):
                        with self.subTest(book=book, argv=argv):
                            new = _tjs(root, argv)
                            _Counted.made = 0
                            with mock.patch.object(TS, "GainRow", _Counted), \
                                    mock.patch.object(SA, "Holding", _Counted):
                                old = _tjs(root, argv)
                            read += _Counted.made
                            self.assertEqual(new, old)
                            self.assertIn(new[0], (0, 1), new[2])
                with self.subTest(book=book):
                    self.assertGreater(read, 0, f"stats and sanity read no rows in {book}")


def _row(**kw):
    """A sale whose figures all differ."""
    row = {"symbol": "ZZQ", "date": "2024-03-01", "date_settle": "2024-03-04", "account": "margin",
           "id": "99900001", "qty": 3.0, "proceeds": 30.0, "cost": 70.0, "gain": -10.0,
           "raw_gain": -40.0, "disallowed_amount": 30.0, "permanently_disallowed": 12.0,
           "deferred_loss_recovered": 5.0, "denied_contribution": 7.0}
    row.update(kw)
    return {k: v for k, v in row.items() if v is not None}


class TestTheKeys(unittest.TestCase):
    """Which key each figure reads, on rows where the keys differ."""

    def test_economic_pnl(self):
        """The raw gain (the gain when the row has none, or a null one),
        plus the deferred loss the sale released."""
        self.assertEqual(TS.economic_pnl(GainRow(_row())), -35.0)
        self.assertEqual(TS.economic_pnl(GainRow(_row(raw_gain=None))), -5.0)
        # A null raw gain is read as model/result gain_before_denial reads
        # it, the one rule: none, not the gain (backlog TJ-3047).
        self.assertEqual(TS.economic_pnl(GainRow({**_row(), "raw_gain": None})), 5.0)

    def test_deemed_gains(self):
        """A deemed row of no units (its proceeds are not its units)
        gives its gain."""
        doc = {"transactions": [_row(deemed=True, qty=0.0, proceeds=3.0, gain=7.0, raw_gain=9.0,
                                     acquired_date="2023-05-01")]}
        self.assertEqual(TS._deemed_gains({"margin": doc}), {("margin", "99900001"): [("2023-05-01", 7.0)]})
        # A gain beside no raw gain: still the gain that decides.
        doc["transactions"][0].pop("raw_gain")
        self.assertEqual(TS._deemed_gains({"margin": doc}), {("margin", "99900001"): [("2023-05-01", 7.0)]})

    def test_contribution_results(self):
        doc = {"transactions": [_row(in_kind=IK.CONTRIBUTION)]}
        self.assertEqual(IK.contribution_results([doc]),
                         {("margin", "ZZQ", "2024-03-01"): {"gain": -10.0, "denied": 7.0}})

    def test_an_expired_option(self):
        """The units the books still hold, not the cost."""
        from taxjson.pipeline.inputs import _warn_expired_open_options
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            g = d / "margin_gains.json"
            g.write_text(json.dumps({"transactions": [], "inventory": [
                {"symbol": "ZZQ240119C00010000", "qty": 2.0, "total_cost": 5.0},
                # A cost that is not a number, a 400-digit one: warned
                # without the cost, never a traceback (TJ-3045, TJ-3052);
                # a written option's cost is not read at all.
                {"symbol": "ZZR240119C00010000", "qty": 1.0, "total_cost": "abc"},
                {"symbol": "ZZS240119C00010000", "qty": 1.0, "total_cost": 10 ** 400},
                {"symbol": "ZZU240119C00010000", "qty": -1.0, "total_cost": "abc"},
                # A 400-digit quantity: left out (TJ-3046).
                {"symbol": "ZZT240119C00010000", "qty": 10 ** 400, "total_cost": 5.0}]}),
                encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                _warn_expired_open_options("margin", g, d, 2024)
            diag = (d / "margin_expired_options.diag").read_text(encoding="utf-8")
        self.assertIn("ZZQ240119C00010000 expired 2024-01-19 but the books still hold 2 (long)", diag)
        self.assertIn("the 5.00 paid", diag)
        self.assertEqual(diag.count(" paid "), 1)
        for sym, held in (("ZZR", "1 (long)"), ("ZZS", "1 (long)"), ("ZZU", "-1 (written)")):
            self.assertIn(f"{sym}240119C00010000 expired 2024-01-19 but the books still hold {held}", diag)
        self.assertNotIn("ZZT", diag)

    def test_a_too_large_quantity_in_sanity(self):
        """Said as the row checks say it, not in 400 digits (TJ-3056)."""
        from _support.filing_sanity_books import _sanity, _sanity_project
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _sanity_project(root, inventory_qty=10 ** 400, snapshots=(("2026-02-15", 0),))
            code, _doc, err = _sanity(root)
        self.assertEqual(code, 2)
        self.assertIn("ZZQ.US: inventory qty is an integer of 401 digits, too large to read", err)
        self.assertNotIn("9" * 50, err)

    def test_the_conservation_check(self):
        """The units, not the cost."""
        msg = _blend_conservation_gaps(
            {"inventory": [{"symbol": "ZZQ", "qty": 10.0, "total_cost": 99.0}]},
            [{"inventory": [{"symbol": "ZZQ", "qty": 4.0, "total_cost": 40.0, "blended_pool": True}]}])
        self.assertIn("blended ZZQ holds 10 but the per-account split accounts for only 4 — 6 short", msg[0])


class TestTheFunnel(unittest.TestCase):
    """The rows come through the row checks before a view reads them, on
    the fields the views read: a damaged field nothing reads leaves the
    document in (backlog TJ-3043, TJ-3044)."""

    def test_the_in_kind_results_documents(self):
        """The in-kind results check their contribution rows' date, gain
        and denied_contribution: a refused one is left out, its refusal
        returned for the in-kind message to say (TJ-2984, TJ-3044,
        TJ-3061). The configured accounts' books only (TJ-4301)."""
        from taxjson.pipeline.stages import _checked_gains_docs
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "work"
            d.mkdir()
            (Path(tmp) / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                + "".join(f'[accounts.{a}]\ntype = "{k}"\n' for a, k in
                          (("margin", "taxable"), ("cash", "taxable"), ("tfsa", "taxable"))),
                encoding="utf-8")
            (d / "margin_gains.json").write_text(json.dumps({"transactions": [_row()]}), encoding="utf-8")
            # A field no view reads here, and a sale that is no in-kind
            # contribution: both kept.
            (d / "cash_gains.json").write_text(json.dumps({"transactions": [
                _row(qty="abc", in_kind=IK.CONTRIBUTION), _row(gain="abc")]}), encoding="utf-8")
            (d / "tfsa_gains.json").write_text(json.dumps({"transactions": [
                _row(gain="abc", in_kind=IK.CONTRIBUTION)]}), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
                docs, refused = _checked_gains_docs(d)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(sorted(len(doc["transactions"]) for doc in docs), [1, 2])
        self.assertEqual(len(refused), 1)
        self.assertIn('work/tfsa_gains.json: "transactions" row 0 (ZZQ): gain is \'abc\'', refused[0])
        self.assertNotIn(tmp, refused[0])

    def test_a_damaged_history_document(self):
        """A figure stats reads (a short cover's or a deemed row's units
        or gain) that is not a number stops the view (exit 2, the file
        named); a field or a row it never reads leaves the document in
        (TJ-3043, TJ-3054)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "margin_gains_all.json"
            fifo = {"ib": [{"action": "ASSIGN", "symbol": "ZZQ250117C00010000", "date": "2024-01-17"}]}
            books = {"margin": [{"action": "ADJUST", "symbol": "ZZQ", "date": "2023-06-01", "net_amount": -5.0}]}
            with mock.patch.object(SU, "_gains_all_file", lambda *a: p):
                p.write_text(json.dumps({"transactions": [_row(qty="abc", direction="SHORT")]}),
                             encoding="utf-8")
                err = io.StringIO()
                with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                    SU._fifo_history_docs(root, root, fifo, books, "2024", False)
                self.assertEqual(cm.exception.code, 2)
                self.assertIn("margin_gains_all.json", err.getvalue())
                self.assertIn("qty is 'abc', not a number", err.getvalue())
                for kept in (_row(price="abc", direction="SHORT"), _row(raw_gain="abc", deemed=True),
                             _row(qty="abc"), _row(qty="abc", direction="SHORT", symbol="ZZQ250117C00010000")):
                    p.write_text(json.dumps({"transactions": [kept]}), encoding="utf-8")
                    self.assertEqual(list(SU._fifo_history_docs(root, root, fifo, books, "2024", False)),
                                     ["margin"], kept)
                p.write_text(json.dumps({"transactions": [_row(gain="abc", deemed=True)]}), encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    SU._fifo_history_docs(root, root, fifo, books, "2024", False)
                p.write_text(json.dumps({"transactions": [_row()]}), encoding="utf-8")
                self.assertEqual(list(SU._fifo_history_docs(root, root, fifo, books, "2024", False)), ["margin"])

    def test_the_blended_pass_files(self):
        """Their inventory quantities read through the row checks: a
        400-digit one is refused, no "holds inf" message (TJ-3046); a
        damaged field the check never reads leaves it on (TJ-3053)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            blend = d / ".blend_gains_wash.json"
            (d / "margin_gains_wash.json").write_text(json.dumps({"inventory": [
                {"symbol": "ZZQ", "qty": 4.0, "blended_pool": True}]}), encoding="utf-8")
            blend.write_text(json.dumps({"inventory": [{"symbol": "ZZQ", "qty": 10.0}]}), encoding="utf-8")
            self.assertEqual(len(_blend_gap_messages(blend, d, ["margin"])), 1)
            (d / "margin_gains_wash.json").write_text(json.dumps({
                "transactions": [{"symbol": "ZZQ", "price": "abc", "date_settle": 5}],
                "inventory": [{"symbol": "ZZQ", "qty": 4.0, "blended_pool": True}]}), encoding="utf-8")
            self.assertEqual(len(_blend_gap_messages(blend, d, ["margin"])), 1)
            blend.write_text('{"inventory": [{"symbol": "ZZQ", "qty": ' + "9" * 400 + "}]}", encoding="utf-8")
            self.assertEqual(_blend_gap_messages(blend, d, ["margin"]), [])


if __name__ == "__main__":
    unittest.main()
