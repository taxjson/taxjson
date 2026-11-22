"""Step 6c(6a): wash-radar and harvest read gains rows through views.

wash_radar's index of the gains files (`_EngineLosses.load`), its run of
the US engine (`_us_engine_losses`) and its engine-backed loss records,
and harvest's open positions (`load_positions`), holdings per account
(`load_inventory_agg`) and native inventory (`_option_quote_symbols`:
which option contract is held) read a gains row's units and money
through model/result views over the rows' own dicts (`GainRow(r)`,
`Holding(h)`). Pinned as 6c(3) pinned the export group
(test_model_result_export): each of the first four runs over every
recorded gains file and the runs of the golden and fuzz books, with the
views and with a raw reader in their place; the US engine's verdicts
over the golden and fuzz books themselves. The files go through the row
checks first (read_work_doc; harvest's `_inventory`; the native
inventory's own checks, TJ-2948, TJ-2957), so a view never sees an
integer too large for a float (TestTheFunnel); the radar's own run is
the engine's output. Which key a site reads is held by TestTheKeys, the
golden books and the radar and harvest tests."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _engine
from taxjson.bin import taxjson_harvest as HV
from taxjson.bin import taxjson_wash_radar as WR
from _support.view_lock import _books, _both, _docs, _main_year


def _with_year(doc):
    """The document with a summary.year (the radar refuses a file
    without one): its rows' own year."""
    summ = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
    if summ.get("year"):
        return doc
    return dict(doc, summary=dict(summ, year=int(_main_year(doc) or 2025)))


class TestEngineLosses(unittest.TestCase):
    def test_every_gains_file(self):
        n = 0
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            for name, doc in _docs():
                p.write_text(json.dumps(_with_year(doc)), encoding="utf-8")
                n += len(doc.get("transactions") or [])
                with self.subTest(name):
                    def run():
                        idx = WR._EngineLosses.load([p])
                        return idx.by_key, idx.cover, idx.buyback_wash
                    new, old = _both(WR, ["GainRow"], run)
                    self.assertEqual(new[0], "value", new)
                    self.assertEqual(new, old)
        self.assertGreater(n, 1500)

    def test_the_us_engine_over_the_books(self):
        listed = 0
        for name, tax, shel in _books():
            with self.subTest(name):
                new, old = _both(WR, ["GainRow"], lambda: WR._us_engine_losses(tax, shel))
                self.assertEqual(new[0], "value", new)
                self.assertEqual(new, old)
                listed += len(new[1][1])        # the typed dict's items
        self.assertGreater(listed, 100)


class TestHarvest(unittest.TestCase):
    def test_every_inventory(self):
        n = based = 0
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            for name, doc in _docs():
                if not isinstance(doc.get("inventory"), list):
                    continue
                p.write_text(json.dumps(doc), encoding="utf-8")
                n += len(doc["inventory"])
                calls = [lambda: HV.load_positions([p], include_options=True),
                         lambda: HV.load_inventory_agg([p], "SH_QTY"),
                         lambda: HV.load_inventory_agg([p], "SH_QTY", include_sold=True)]
                # A base currency the book's own (one with two stops).
                curs = {str(h.get("currency") or "").upper() for h in doc["inventory"]} - {""}
                if len(curs) <= 1:
                    base = next(iter(curs), "CAD")
                    calls.append(lambda: HV.load_positions([p], base_currency=base))
                    based += 1
                for i, f in enumerate(calls):
                    with self.subTest(name, call=i):
                        new, old = _both(HV, ["Holding"], f)
                        self.assertEqual(new[0], "value", new)
                        self.assertEqual(new, old)
        self.assertGreater(n, 1000)
        self.assertGreater(based, 150)


class TestTheFunnel(unittest.TestCase):
    """A number too large for a float stops each reader with one line
    before a view reads it as an infinity."""

    def _file(self, td, doc):
        p = Path(td) / "a_gains.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def _stops(self, f, code):
        """The message f() stops with (the radar's refusal is exit 1, its
        text the exception's; harvest's input error exit 2, printed)."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            f()
        if code == 1:
            self.assertIsInstance(cm.exception.code, str)
            return cm.exception.code
        self.assertEqual(cm.exception.code, code)
        return err.getvalue()

    def test_the_radar(self):
        for key in ("qty", "raw_gain", "gain"):
            row = {"id": "t1", "symbol": "XYZ", "date": "2025-03-03", "qty": 10,
                   "gain": -50.0, "raw_gain": -50.0, key: 10 ** 400}
            with self.subTest(key), tempfile.TemporaryDirectory() as td:
                p = self._file(td, {"summary": {"year": 2025}, "transactions": [row]})
                msg = self._stops(lambda: WR._EngineLosses.load([p]), 1)
                self.assertIn(f"{key} is an integer of 401 digits", msg)

    def test_harvest(self):
        for key in ("qty", "total_cost", "deferred_wash", "recognised_premium"):
            row = {"symbol": "XYZ", "account": "U1234567", "qty": 10, "total_cost": 500.0,
                   "currency": "CAD", key: 10 ** 400}
            with self.subTest(key), tempfile.TemporaryDirectory() as td:
                p = self._file(td, {"inventory": [row]})
                for f in (lambda: HV.load_positions([p]),
                          lambda: HV.load_inventory_agg([p], "SH_QTY")):
                    msg = self._stops(f, 2)
                    self.assertIn(f"{key} is an integer of 401 digits", msg)

    def test_a_text_premium(self):
        """TJ-2947: a written option's recognised premium as text was a
        ValueError traceback from `tjs harvest`."""
        row = {"symbol": "XYZ", "qty": -1, "total_cost": -50.0, "currency": "CAD",
               "recognised_premium": "abc"}
        with tempfile.TemporaryDirectory() as td:
            p = self._file(td, {"inventory": [row]})
            msg = self._stops(lambda: HV.load_positions([p]), 2)
        self.assertIn("recognised_premium is 'abc', not a number", msg)

    def test_a_damaged_native_inventory(self):
        """TJ-2948: harvest's read of the native `<acct>_raw_gains.json`
        (which option contract is held) stopped with an AttributeError or
        a ValueError on a damaged inventory. It is not read, with a
        warning naming the file (TJ-2958): the renamed contract is
        omitted, never quoted from another. Damage in a part harvest does
        not read is not its to refuse (TJ-2957)."""
        booked, held = "ZZB261218C00012000.TO", "ZZA261218C00012000.US"
        good = [{"symbol": held, "qty": 1}]
        damaged = [{"inventory": ["x"]}, {"inventory": {"a": 1}},
                   {"inventory": [{"symbol": held, "qty": "abc"}]},
                   {"inventory": [{"symbol": held, "qty": 10 ** 400}]}, "not json", "[1]"]
        intact = [{"inventory": good},
                  {"inventory": good, "transactions": [{"symbol": held, "price": "n/a"}]},
                  {"inventory": good, "manual_reporting_required": "x", "summary": 3}]
        for raw in intact + damaged:
            with self.subTest(raw=str(raw)[:60]), tempfile.TemporaryDirectory() as td:
                g = self._file(td, {"inventory": [{"symbol": booked, "qty": 1}]})
                native = Path(td) / "a_raw_gains.json"
                native.write_text(raw if isinstance(raw, str) else json.dumps(raw), encoding="utf-8")
                tmap = Path(td) / "ticker.map"
                tmap.write_text("TOBASE ZZA.US ZZB.TO\n", encoding="utf-8")
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    out = HV._option_quote_symbols([booked], [g], tmap)
                self.assertEqual(out, {booked: None if raw in damaged else held})
                if raw in damaged:
                    self.assertIn(f"warning: the native inventory is not read: {native}", err.getvalue())
                    self.assertIn("(no matching row)", " ".join(err.getvalue().split()))
                else:
                    self.assertEqual(err.getvalue(), "")


class TestTheKeys(unittest.TestCase):
    """The keys the views read, where the lock above cannot tell (it
    swaps the reader, not the key) and no other test did: the loss
    before any denial (`raw_gain`, else `gain`), and the denied loss a
    holding carries (`deferred_wash`)."""

    def _gains(self, td, rows):
        p = Path(td) / "margin_gains_wash.json"
        p.write_text(json.dumps({"summary": {"year": 2026}, "metadata": {"account": "margin"},
                                 "transactions": rows}), encoding="utf-8")
        return p

    def test_the_engine_index(self):
        tx = SimpleNamespace(account="margin", id="s1", date="2026-05-01",
                             date_settle="2026-05-04")
        cases = [({"raw_gain": -300.0, "gain": -120.0}, [-300.0]),
                 ({"gain": -300.0}, [-300.0]),       # a file before raw_gain
                 ({"raw_gain": None, "gain": -300.0}, []),
                 ({"raw_gain": -300.0, "gain": None}, [-300.0])]
        for extra, want in cases:
            row = dict({"id": "s1", "qty": 100, "account": "margin", "direction": "LONG"}, **extra)
            with self.subTest(extra), tempfile.TemporaryDirectory() as td:
                rows = WR._EngineLosses.load([self._gains(td, [row])]).rows_for(tx)
                self.assertEqual([r["raw_gain"] for r in rows], want)

    def test_the_us_engine(self):
        res = {"transactions": [{"id": "s1", "qty": 10, "gain": -50.0},
                                {"id": "s2", "qty": 5, "gain": -30.0, "raw_gain": -80.0,
                                 "disallowed_amount": 50.0, "permanently_disallowed": 20.0}]}
        fake = SimpleNamespace(compute_gains=lambda *a, **kw: res)
        with _engine.patch_engine("get_tax_rules", lambda country: fake):
            out = WR._us_engine_losses([], [])
        self.assertEqual({k: (v["qty"], v["loss"], v["disallowed"], v["permanent"])
                          for k, v in out.items()},
                         {"s1": (10.0, 50.0, 0.0, 0.0), "s2": (5.0, 80.0, 50.0, 20.0)})

    def test_the_radar_reads_the_engines_loss(self):
        """A loss the engine already denied in part: the radar's record is
        the engine's loss before the denial (310: its own pool says 300),
        not the gain after it."""
        from _support.planning_radar_row import _row
        book = [_row("2026-04-01", "ZZQ.TO", 100, 50.0, currency="CAD", settle="2026-04-02", rid="b1"),
                _row("2026-05-01", "ZZQ.TO", -100, 47.0, currency="CAD", settle="2026-05-04", rid="s1"),
                _row("2026-05-10", "ZZQ.TO", 40, 47.0, currency="CAD", settle="2026-05-11", rid="b2")]
        sold = {"id": "s1", "symbol": "ZZQ.TO", "qty": 100, "account": "margin",
                "date": "2026-05-01", "date_settle": "2026-05-04", "direction": "LONG",
                "gain": -186.0, "raw_gain": -310.0}
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td) / "margin_base.json"
            tp.write_text(json.dumps({"transactions": book}), encoding="utf-8")
            argv = ["taxjson-wash-radar", "--country", "canada", "--date", "2026-05-20", "--all",
                    "--taxable", str(tp), "--gains", str(self._gains(td, [sold])), "--json"]
            out = io.StringIO()
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                WR.main()
        rows = [r for sec in json.loads(out.getvalue())["sections"] for r in sec["rows"]]
        self.assertEqual([(r["ticker"], [(x["loss"], x["qty"]) for x in r["losses"]]) for r in rows],
                         [("ZZQ.TO", [(310.0, 100.0)])])

    def test_harvest_positions(self):
        row = {"symbol": "XYZ", "qty": 10, "total_cost": 500.0, "deferred_wash": 20.0,
               "recognised_premium": 7.0, "currency": "CAD", "last_acq_date": "2026-01-05"}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_gains.json"
            p.write_text(json.dumps({"inventory": [row]}), encoding="utf-8")
            pos = HV.load_positions([p])
            agg = HV.load_inventory_agg([p], "SH_QTY")
        self.assertEqual([(x["qty"], x["cost"], x["deferred_wash"], x["recognised_premium"]) for x in pos],
                         [(10.0, 500.0, 20.0, 7.0)])
        self.assertEqual(agg["XYZ"]["qty"], 10.0)


if __name__ == "__main__":
    unittest.main()
