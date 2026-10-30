"""Fix round 2026-10-29 (round 7, wave D), group fetchexport: the
holdings snapshot (`taxjson-export --holdings-toml`) read back.

TJ-1031  A base-currency cost is written only when every input of the
         holding gives one: never one input's figure as the whole
         holding's.
TJ-1071  A cost_source = "blended" figure another input also holds is
         left out for real: the holding's cost is written as unknown.
TJ-1070  --transfer-evidence finds a move through the map's GLOBAL
         renames of a snapshot's symbols.
TJ-1072  The trade history takes a trade whose settle lag straddles a
         split in post-split units, as the Canadian engine books it.
TJ-1109  A cost in cost_currency keeps that label.
TJ-1110  Lot tables (`acquired`) are refused, not merged and undated;
         cost_kind is kept.
TJ-1111  [meta] account and broker_account are carried when every input
         names the same one.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth

from taxjson.bin import taxjson_export as export
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.positions_reports import read_positions
from taxjson.lib.tomlcompat import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _export(files, *args):
    """taxjson-export with `files` ({name: text}) in a folder of their
    own; `args` name them as {name}. (CompletedProcess, parsed snapshot
    or None)."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, text in files.items():
            (root / name).write_text(text)
        argv = [str(root / a[1:-1]) if a.startswith("{") else a
                for a in args]
        env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_WIDTH="0")
        r = subprocess.run(
            python_module_argv("taxjson.bin.taxjson_export", argv),
            capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL, timeout=120)
        doc = (tomllib.loads(r.stdout)
               if r.returncode == 0 and "--holdings-toml" in args else None)
        return r, doc


def _holding(doc, sym):
    return next(h for h in doc.get("holding", []) if h["symbol"] == sym)


def _toml(*holdings, meta=""):
    head = f"[meta]\n{meta}holdings_count = {len(holdings)}\n"
    return head + "".join(f"[[holding]]\n{h}\n" for h in holdings)


def _side(*legs):
    return json.dumps({
        "metadata": {"kind": "transfer_sidecar", "account": "acct1"},
        "transactions": [
            {"action": "TRANSFER", "symbol": s, "date": "2025-06-02",
             "quantity": q, "currency": c} for s, q, c in legs]})


_WITH_BASE = ('symbol = "SYN.US"\nquantity = 10.0\ncurrency = "USD"\n'
              'total_cost = 100.0\nbase_currency = "CAD"\n'
              'base_total_cost = 135.0\n')
_NO_BASE = ('symbol = "SYN.US"\nquantity = 10.0\ncurrency = "USD"\n'
            'average_entry_price = 20.0\n')
_BLENDED = ('symbol = "ZZQ.TO"\nquantity = 4.0\ncurrency = "CAD"\n'
            'total_cost = 80.0\ncost_per_share = 20.0\n'
            'cost_source = "blended"\nbase_currency = "CAD"\n'
            'base_total_cost = 80.0\n')
_OTHER = ('symbol = "ZZQ.TO"\nquantity = 6.0\ncurrency = "CAD"\n'
          'total_cost = 60.0\nbase_currency = "CAD"\n'
          'base_total_cost = 60.0\n')
_NATIVE_JSON = json.dumps({"inventory": [
    {"symbol": "SYN.US", "qty": 10.0, "total_cost": 100.0,
     "currency": "USD"}]})
_BASE_JSON = json.dumps({"inventory": [
    {"symbol": "SYN.US", "qty": 10.0, "total_cost": 135.0,
     "currency": "CAD"}]})


class TestBaseCoversTheHolding(unittest.TestCase):
    """TJ-1031."""

    def test_one_input_without_a_base_figure(self):
        r, doc = _export(
            {"a.toml": _toml(_WITH_BASE), "b.toml": _toml(_NO_BASE)},
            "--holdings-toml", "--base-currency", "CAD", "{a.toml}",
            "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "SYN.US")
        self.assertEqual((h["quantity"], h["total_cost"]), (20.0, 300.0))
        for key in ("base_currency", "base_total_cost",
                    "base_cost_per_share"):
            self.assertNotIn(key, h)
        self.assertIn("SYN.US", r.stderr)
        self.assertIn("base_total_cost is omitted", r.stderr)
        self.assertIn("# base_total_cost omitted", r.stdout)

    def test_every_input_with_a_base_figure_is_summed(self):
        r, doc = _export(
            {"a.toml": _toml(_WITH_BASE), "b.toml": _toml(_WITH_BASE)},
            "--holdings-toml", "--base-currency", "CAD", "{a.toml}",
            "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "SYN.US")
        self.assertEqual((h["base_total_cost"], h["base_cost_per_share"]),
                         (270.0, 13.5))
        self.assertNotIn("base_total_cost", r.stderr)

    def test_a_gains_file_without_base_figures(self):
        r, doc = _export(
            {"a.toml": _toml(_WITH_BASE), "g.json": _NATIVE_JSON},
            "--holdings-toml", "{a.toml}", "{g.json}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("base_total_cost", _holding(doc, "SYN.US"))

    def test_base_gains_alone_stay_one_to_one(self):
        # The --base-gains contract: the same positions as the inputs.
        r, doc = _export({"n.json": _NATIVE_JSON, "b.json": _BASE_JSON},
                         "--holdings-toml", "--base-gains", "{b.json}",
                         "{n.json}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_holding(doc, "SYN.US")["base_total_cost"], 135.0)
        self.assertEqual(r.stderr.strip(), "")

    def test_a_filtered_out_input_does_not_count(self):
        short = ('symbol = "SYN.US"\nquantity = -3.0\ncurrency = "USD"\n'
                 'total_cost = -30.0\n')
        r, doc = _export(
            {"a.toml": _toml(_WITH_BASE), "b.toml": _toml(short)},
            "--holdings-toml", "--long", "{a.toml}", "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_holding(doc, "SYN.US")["base_total_cost"], 135.0)

    def test_a_moved_partial_bucket_stays_omitted(self):
        r, doc = _export(
            {"a.toml": _toml(_WITH_BASE), "b.toml": _toml(_NO_BASE),
             "t.map": "TOBASE SYN.US SYN.TO\n",
             "s.json": _side(("SYN.US", -20, "USD"), ("SYN.TO", 20, "CAD"))},
            "--holdings-toml", "--map", "{t.map}", "--transfer-evidence",
            "{s.json}", "{a.toml}", "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "SYN.TO")
        self.assertEqual(h["quantity"], 20.0)
        self.assertNotIn("base_total_cost", h)

    def test_a_moved_whole_bucket_keeps_its_base(self):
        r, doc = _export(
            {"a.toml": _toml(_WITH_BASE),
             "t.map": "TOBASE SYN.US SYN.TO\n",
             "s.json": _side(("SYN.US", -10, "USD"), ("SYN.TO", 10, "CAD"))},
            "--holdings-toml", "--map", "{t.map}", "--transfer-evidence",
            "{s.json}", "{a.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_holding(doc, "SYN.TO")["base_total_cost"], 135.0)

    def test_base_gains_for_the_gains_files_part_add_up(self):
        r, doc = _export({"a.toml": _toml(_WITH_BASE),
                          "n.json": _NATIVE_JSON, "b.json": _BASE_JSON},
                         "--holdings-toml", "--base-gains", "{b.json}",
                         "{a.toml}", "{n.json}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_holding(doc, "SYN.US")["base_total_cost"], 270.0)

    def test_a_snapshot_and_base_gains_of_one_position_is_omitted(self):
        # The same 10 units' base cost twice: 20 base units for 10 held.
        r, doc = _export({"a.toml": _toml(_WITH_BASE), "b.json": _BASE_JSON},
                         "--holdings-toml", "--base-gains", "{b.json}",
                         "{a.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("base_total_cost", _holding(doc, "SYN.US"))
        self.assertIn("base_total_cost is omitted", r.stderr)


class TestBlendedWithAnotherHolder(unittest.TestCase):
    """TJ-1071, with TJ-1031's blended case."""

    def test_the_figure_is_left_out_as_the_warning_says(self):
        r, doc = _export(
            {"a.toml": _toml(_BLENDED), "b.toml": _toml(_OTHER)},
            "--holdings-toml", "--base-currency", "CAD", "{a.toml}",
            "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "ZZQ.TO")
        self.assertEqual(h["quantity"], 10.0)
        self.assertTrue(h["cost_unknown"])
        for key in ("total_cost", "cost_per_share", "cost_source",
                    "base_total_cost"):
            self.assertNotIn(key, h)
        self.assertIn("cost_unknown", r.stderr)

    def test_a_lone_blended_holding_is_kept(self):
        r, doc = _export({"a.toml": _toml(_BLENDED)}, "--holdings-toml",
                         "--base-currency", "CAD", "{a.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "ZZQ.TO")
        self.assertEqual((h["total_cost"], h["cost_source"],
                          h["base_total_cost"]), (80.0, "blended", 80.0))
        self.assertEqual(r.stderr.strip(), "")


class TestEvidenceThroughGlobalRenames(unittest.TestCase):
    """TJ-1070."""

    SNAP = _toml('symbol = "OLDN.US"\nquantity = 10.0\ncurrency = "USD"\n'
                 'total_cost = 50.0\n', meta="as_of = 2025-06-30\n")

    def test_the_move_applies_to_the_renamed_holding(self):
        r, doc = _export(
            {"s.toml": self.SNAP,
             "t.map": "GLOBAL OLDN.US SYN.US\nTOBASE SYN.US SYN.TO\n",
             "e.json": _side(("OLDN.US", -10, "USD"), ("SYN.TO", 10, "CAD"))},
            "--holdings-toml", "--map", "{t.map}", "--transfer-evidence",
            "{e.json}", "{s.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("applied evidenced depot flip", r.stderr)
        self.assertEqual([h["symbol"] for h in doc["holding"]], ["SYN.TO"])
        self.assertEqual(doc["holding"][0]["quantity"], 10.0)
        self.assertEqual(doc["holding"][0]["total_cost"], 50.0)

    def test_a_held_raw_spelling_still_moves(self):
        # A gains file keeps the spelling its books have: the leg that
        # names a key of the holdings is taken as it is.
        gains = json.dumps({"inventory": [
            {"symbol": "OLDN.US", "qty": 10.0, "total_cost": 50.0,
             "currency": "USD"}]})
        r, doc = _export(
            {"g.json": gains, "t.map": "GLOBAL OLDN.US SYN.US\n",
             "e.json": _side(("OLDN.US", -10, "USD"), ("SYN.US", 10, "USD"))},
            "--holdings-toml", "--map", "{t.map}", "--transfer-evidence",
            "{e.json}", "{g.json}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([h["symbol"] for h in doc["holding"]], ["SYN.US"])

    def test_a_renamed_in_leg_lands_on_the_renamed_holding(self):
        snap = _toml(
            'symbol = "SYN.US"\nquantity = 10.0\ncurrency = "USD"\n'
            'total_cost = 50.0\n',
            'symbol = "OLDT.TO"\nquantity = 5.0\ncurrency = "CAD"\n'
            'total_cost = 40.0\n')
        r, doc = _export(
            {"s.toml": snap,
             "e.json": _side(("SYN.US", -10, "USD"), ("OLDT.TO", 10, "CAD")),
             "t.map": "GLOBAL OLDT.TO SYN.TO\nTOBASE SYN.US SYN.TO\n"},
            "--holdings-toml", "--map", "{t.map}", "--transfer-evidence",
            "{e.json}", "{s.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(h["symbol"], h["quantity"])
                          for h in doc["holding"]], [("SYN.TO", 15.0)])

    def test_a_journal_a_rename_explains_moves_nothing(self):
        # Both legs are one symbol after the rename the snapshot took.
        r, doc = _export(
            {"s.toml": self.SNAP, "t.map": "GLOBAL OLDN.US SYN.US\n",
             "e.json": _side(("OLDN.US", -10, "USD"), ("SYN.US", 10, "USD"))},
            "--holdings-toml", "--map", "{t.map}", "--transfer-evidence",
            "{e.json}", "{s.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([(h["symbol"], h["quantity"])
                          for h in doc["holding"]], [("SYN.US", 10.0)])


def _row(day, qty, price=0.0, t="10:00:00", settle=None, action="BUYSELL",
         **kw):
    return {"date": day, "date_settle": settle or day, "time": t,
            "action": action, "symbol": "SYN.TO", "quantity": qty,
            "price": price, "currency": "CAD", "account": "acct1", **kw}


def _events(rows, country="canada"):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "raw.json"
        p.write_text(json.dumps({"transactions": rows}))
        return export._load_trade_events([p], country=country)


class TestSplitInsideTheSettleLag(unittest.TestCase):
    """TJ-1072."""

    def _book(self):
        return [
            _row("2025-01-10", 10, 10.0, settle="2025-01-13"),
            _row("2025-06-02", 10, 10.0, t="15:00:00",
                 settle="2025-06-03"),
            _row("2025-06-02", 2, t="20:25:00", action="SPLIT"),
            _row("2025-06-05", -40, 6.0),
            _row("2025-06-09", 1, 6.0)]

    def test_the_closed_round_goes(self):
        self.assertEqual(_events(self._book())["SYN.TO"],
                         [{"date": "2025-06-09", "action": "BUY",
                           "qty": 1.0, "price": 6.0}])

    def test_the_trade_is_charted_as_executed(self):
        # Not closed: the row keeps the quantity and price it traded at.
        rows = self._book()[:3]
        self.assertEqual(
            _events(rows)["SYN.TO"],
            [{"date": "2025-01-10", "action": "BUY", "qty": 10.0,
              "price": 10.0},
             {"date": "2025-06-02", "action": "BUY", "qty": 10.0,
              "price": 10.0}])

    def test_a_crossing_row_is_kept_in_its_own_units(self):
        # 10 held, a x2 split, 15 sold as executed: 20 held less 30
        # post-split shares, so the 10 left short are 5 as executed.
        rows = [_row("2025-01-10", 10, 10.0),
                _row("2025-06-02", -15, 9.0, t="15:00:00",
                     settle="2025-06-03"),
                _row("2025-06-02", 2, t="20:25:00", action="SPLIT")]
        self.assertEqual(_events(rows)["SYN.TO"],
                         [{"date": "2025-06-02", "action": "SELL",
                           "qty": 5.0, "price": 9.0}])

    def test_a_morning_split_is_no_straddle(self):
        rows = [_row("2025-01-10", 10, 10.0),
                _row("2025-06-02", 2, t="00:00:01", action="SPLIT"),
                _row("2025-06-02", 10, 5.0, t="15:00:00",
                     settle="2025-06-03"),
                _row("2025-06-05", -30, 6.0),
                _row("2025-06-09", 1, 6.0)]
        self.assertEqual(_events(rows)["SYN.TO"],
                         [{"date": "2025-06-09", "action": "BUY",
                           "qty": 1.0, "price": 6.0}])

    def test_the_split_row_of_two_accounts_scales_once(self):
        rows = self._book()
        rows.insert(3, dict(rows[2], account="acct2"))
        self.assertEqual(_events(rows)["SYN.TO"],
                         [{"date": "2025-06-09", "action": "BUY",
                           "qty": 1.0, "price": 6.0}])

    def test_a_us_book_takes_the_clock_order(self):
        # The US engine orders by trade date and clock: the trade comes
        # before the evening split and takes it with the pool.
        self.assertEqual(_events(self._book(), "usa")["SYN.TO"],
                         [{"date": "2025-06-09", "action": "BUY",
                           "qty": 1.0, "price": 6.0}])

    def test_it_matches_the_engine(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "raw.json"
            rows = [dict(t, id=f"t{i}",
                         net_amount=-t["quantity"] * t["price"],
                         gross_amount=-t["quantity"] * t["price"],
                         fees=0.0)
                    for i, t in enumerate(self._book())]
            raw.write_text(json.dumps({"transactions": rows}))
            env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_WIDTH="0")
            g = subprocess.run(python_module_argv(
                "taxjson.bin.taxjson_gains",
                ["--country", "canada", "--year", "2025", str(raw)]),
                capture_output=True, text=True, env=env, timeout=120,
                stdin=subprocess.DEVNULL)
            self.assertEqual(g.returncode, 0, g.stderr)
            eng = Path(td) / "engine.json"
            eng.write_text(g.stdout)
            r = subprocess.run(python_module_argv(
                "taxjson.bin.taxjson_export",
                ["--holdings-toml", "--country", "canada", "--trades",
                 str(raw), str(eng)]),
                capture_output=True, text=True, env=env, timeout=120,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            h = _holding(tomllib.loads(r.stdout), "SYN.TO")
        self.assertEqual(h["quantity"], 1.0)
        self.assertEqual([str(t["date"]) for t in h["trades"]],
                         [str(h["position_start_date"])])


class TestCostCurrency(unittest.TestCase):
    """TJ-1109."""

    LINE = ('symbol = "ZZU.US"\nquantity = 10.0\ncurrency = "USD"\n'
            'cost_currency = "CAD"\ntotal_cost = 130.0\n')

    def _read_back(self, text):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "h.toml"
            p.write_text(text)
            return read_positions(p).rows[0]

    def test_the_cost_keeps_its_currency(self):
        r, doc = _export({"h.toml": _toml(self.LINE)}, "--holdings-toml",
                         "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "ZZU.US")
        self.assertEqual((h["currency"], h["cost_currency"],
                          h["total_cost"]), ("USD", "CAD", 130.0))
        row = self._read_back(r.stdout)
        self.assertEqual((row.currency, row.cost_currency, row.cost),
                         ("USD", "CAD", 130.0))
        # A second pass writes the same holding.
        r2, doc2 = _export({"h.toml": r.stdout}, "--holdings-toml",
                           "{h.toml}")
        self.assertEqual(doc2["holding"], doc["holding"])

    def test_the_same_currency_changes_nothing(self):
        line = self.LINE.replace('cost_currency = "CAD"',
                                 'cost_currency = "usd"')
        r, doc = _export({"h.toml": _toml(line)}, "--holdings-toml",
                         "{h.toml}")
        h = _holding(doc, "ZZU.US")
        self.assertEqual((h["currency"], h["total_cost"]), ("USD", 130.0))
        self.assertNotIn("cost_currency", h)

    def test_summed_with_a_usd_cost_is_mixed(self):
        other = ('symbol = "ZZU.US"\nquantity = 5.0\ncurrency = "USD"\n'
                 'total_cost = 50.0\n')
        r, doc = _export({"a.toml": _toml(self.LINE),
                          "b.toml": _toml(other)},
                         "--holdings-toml", "{a.toml}", "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "ZZU.US")
        self.assertTrue(h["mixed_currency"])
        self.assertEqual((h["total_cost_cad"], h["total_cost_usd"]),
                         (130.0, 50.0))
        self.assertNotIn("total_cost", h)

    def test_two_inputs_of_one_cost_currency_sum(self):
        r, doc = _export({"a.toml": _toml(self.LINE),
                          "b.toml": _toml(self.LINE)},
                         "--holdings-toml", "{a.toml}", "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        h = _holding(doc, "ZZU.US")
        self.assertEqual((h["currency"], h["cost_currency"],
                          h["total_cost"]), ("USD", "CAD", 260.0))

    def test_no_cost_keeps_the_listing_currency(self):
        line = ('symbol = "ZZU.US"\nquantity = 10.0\ncurrency = "USD"\n'
                'cost_currency = "CAD"\n')
        r, doc = _export({"h.toml": _toml(line)}, "--holdings-toml",
                         "{h.toml}")
        h = _holding(doc, "ZZU.US")
        self.assertEqual(h["currency"], "USD")
        self.assertTrue(h["cost_unknown"])
        self.assertNotIn("cost_currency", h)


class TestLotTables(unittest.TestCase):
    """TJ-1110."""

    A = ('symbol = "ZZQ.US"\nquantity = 2.0\ncurrency = "USD"\n'
         'total_cost = 20.0\nacquired = "2021-03-04"\ncost_kind = "lots"\n')
    B = ('symbol = "ZZQ.US"\nquantity = 3.0\ncurrency = "USD"\n'
         'total_cost = 33.0\nacquired = "2022-03-04"\ncost_kind = "lots"\n')

    def test_the_rewrite_is_refused(self):
        r, _doc = _export({"h.toml": _toml(self.A, self.B)},
                          "--holdings-toml", "{h.toml}")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("ZZQ.US", r.stderr)
        self.assertIn("acquired", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_the_report_table_still_sums_them(self):
        r, _doc = _export({"h.toml": _toml(self.A, self.B)}, "--report",
                          "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ZZQ.US", r.stdout)

    def test_cost_kind_is_kept(self):
        line = ('symbol = "ZZQ.US"\nquantity = 2.0\ncurrency = "USD"\n'
                'total_cost = 20.0\ncost_kind = "lots"\n')
        r, doc = _export({"h.toml": _toml(line)}, "--holdings-toml",
                         "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_holding(doc, "ZZQ.US")["cost_kind"], "lots")

    def test_two_kinds_of_cost_are_said(self):
        one = ('symbol = "ZZQ.US"\nquantity = 2.0\ncurrency = "USD"\n'
               'total_cost = 20.0\ncost_kind = "lots"\n')
        two = one.replace('"lots"', '"average"')
        r, doc = _export({"a.toml": _toml(one), "b.toml": _toml(two)},
                         "--holdings-toml", "{a.toml}", "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("cost_kind", _holding(doc, "ZZQ.US"))
        self.assertIn("cost_kind", r.stderr)

    def test_a_bad_cost_kind_is_refused(self):
        line = ('symbol = "ZZQ.US"\nquantity = 2.0\ncurrency = "USD"\n'
                'total_cost = 20.0\ncost_kind = "fifo"\n')
        r, _doc = _export({"h.toml": _toml(line)}, "--holdings-toml",
                          "{h.toml}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("cost_kind", r.stderr)


class TestMetaAccount(unittest.TestCase):
    """TJ-1111."""

    LINE = ('symbol = "SYN.TO"\nquantity = 10.0\ncurrency = "CAD"\n'
            'total_cost = 120.0\n')
    META = 'account = "margin"\nbroker_account = "99900001"\n'

    def test_carried(self):
        r, doc = _export({"h.toml": _toml(self.LINE, meta=self.META)},
                         "--holdings-toml", "{h.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((doc["meta"]["account"],
                          doc["meta"]["broker_account"]),
                         ("margin", "99900001"))

    def test_the_account_name_option_wins(self):
        r, doc = _export({"h.toml": _toml(self.LINE, meta=self.META)},
                         "--holdings-toml", "--account-name", "other",
                         "{h.toml}")
        self.assertEqual(doc["meta"]["account"], "other")
        self.assertEqual(doc["meta"]["broker_account"], "99900001")

    def test_two_accounts_carry_none(self):
        r, doc = _export(
            {"a.toml": _toml(self.LINE, meta=self.META),
             "b.toml": _toml(self.LINE, meta='account = "cash"\n'
                                             'broker_account = "99900002"\n')},
            "--holdings-toml", "{a.toml}", "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("account", doc["meta"])
        self.assertNotIn("broker_account", doc["meta"])

    def test_an_input_naming_none_carries_none(self):
        r, doc = _export(
            {"a.toml": _toml(self.LINE, meta=self.META),
             "b.toml": _toml(self.LINE)},
            "--holdings-toml", "{a.toml}", "{b.toml}")
        self.assertNotIn("account", doc["meta"])

    def test_a_gains_input_carries_none(self):
        gains = json.dumps({"inventory": [
            {"symbol": "SYN.TO", "qty": 1.0, "total_cost": 1.0,
             "currency": "CAD"}]})
        r, doc = _export(
            {"a.toml": _toml(self.LINE, meta=self.META), "g.json": gains},
            "--holdings-toml", "{a.toml}", "{g.json}")
        self.assertNotIn("account", doc["meta"])


if __name__ == "__main__":
    unittest.main()
