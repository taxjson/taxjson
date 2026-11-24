"""One broker's id with two contents across exports is refused (the
outside audit of 2026-11-23, backlog TJ-5034).

Overlapping Coinbase exports held one deposit ID with 100 in the older
copy and 120 in the newer (an export edited, a row restated). The cash
events' cross-file dedup read the ID together with the content, so the
two copies were two events and the ledger booked 220 for one ID. Within
one file the parser already refuses a reused ID with other content.

Now the copies of one ID (one book, one kind of event) agree in their
date, currency and amount, or the cash events are refused: both rows,
the ID (masked), the field that differs, and what to do. Identical
copies are still one event. All data synthetic.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib import cash_events as CE
from taxjson.lib import cash_lines as CL
from taxjson.lib import timezones as TZ

from tax_rules import rule
from taxjson.rules import ids as R
from _support.fx_cash_run import record_run
from _support.qt_spinoff import _cli

CB_H = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,"
        "Price at Transaction,Subtotal,Total (inclusive of Fees and/or Spread),"
        "Fees and/or Spread,Notes\n")


def _deposit(amount, row_id="CB-SYN-0001"):
    return (f"{row_id},2026-03-02 16:24:11 UTC,Deposit,USD,{amount},USD,1.00,{amount},"
            f"{amount},0.00,Deposit from bank\n")


def _move(file, amount, bid="X-1", date="2026-03-02", cur="USD", **more):
    e = CL.move(date, cur, amount, where=file, desc="deposit")
    e.update(file=file, book="coins/coinbase", label="coins", line_where=f"{file}:2",
             _broker_id=bid, **more)
    return e


class TestOneIdTwoContents(unittest.TestCase):
    def _coinbase(self, files):
        from _support.parser_cash import parsed_cash_events
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        out = []
        with patch.object(TZ, "local_tz_name", return_value="America/Toronto"):
            for name, rows in sorted(files.items()):
                p = Path(td.name) / name
                p.write_text(CB_H + rows, encoding="utf-8")
                out += [dict(e, label="coins", broker="coinbase", file=name,
                             book="coins/coinbase")
                        for e in parsed_cash_events("coinbase", p)]
        return out

    @rule(R.CA_FX_07)
    def test_a_coinbase_id_with_100_and_120_is_refused(self):
        evs = self._coinbase({"coinbase_old.csv": _deposit("100.00"),
                              "coinbase_new.csv": _deposit("120.00")})
        self.assertEqual(sorted(e["amount"] for e in evs), [100.0, 120.0])
        with self.assertRaises(CE.CashIdConflict) as cm:
            CE.dedup_across_files(evs)
        msg = str(cm.exception)
        for part in ("coins/coinbase: the broker's id CB*** of one CASHMOVE is in two "
                     "exports with different content", "coinbase_new.csv:2",
                     "the copies differ in the amount: 120.00 in coinbase_new.csv:2 (Coinbase "
                     "deposit USD) against 100.00 in coinbase_old.csv:2 (Coinbase deposit USD)",
                     "keep only the export with the right copy of the row",
                     "then `tjs run`"):
            self.assertIn(part, msg)
        self.assertNotIn("CB-SYN-0001", msg)       # the id is masked
        self.assertIsInstance(cm.exception, ValueError)

    @rule(R.CA_FX_07)
    def test_identical_copies_are_one_event(self):
        kept = CE.dedup_across_files(self._coinbase({
            "coinbase_old.csv": _deposit("100.00"), "coinbase_new.csv": _deposit("100.00")}))
        self.assertEqual([e["amount"] for e in kept], [100.0])

    def test_the_field_that_differs_is_named(self):
        for other, said in ((_move("b.csv", 100.0, date="2026-03-03"),
                             "differ in the date: 2026-03-02 in a.csv:2 (deposit) against "
                             "2026-03-03 in b.csv:2"),
                            (_move("b.csv", 100.0, cur="EUR"),
                             "differ in the currency: USD in a.csv:2 (deposit) against EUR in"),
                            (_move("b.csv", -100.0),
                             "differ in the amount: 100.00 in a.csv:2 (deposit) against "
                             "-100.00 in")):
            with self.subTest(said), self.assertRaises(CE.CashIdConflict) as cm:
                CE.dedup_across_files([_move("a.csv", 100.0), other])
            self.assertIn(said, str(cm.exception))
        conv = [dict(CL.conv("2026-03-02", "USD", 100, "CAD", amt, where=f), file=f,
                     book="coins/coinbase", _broker_id="X-2")
                for f, amt in (("a.csv", 140), ("b.csv", 150))]
        with self.assertRaises(CE.CashIdConflict) as cm:
            CE.dedup_across_files(conv)
        self.assertIn("differ in the amount bought: 140.00 in a.csv (FXCONV) against 150.00 in "
                      "b.csv", str(cm.exception))

    def test_several_conflicts_are_one_line_each(self):
        with self.assertRaises(CE.CashIdConflict) as cm:
            CE.dedup_across_files([_move("a.csv", 100.0), _move("b.csv", 120.0),
                                   _move("a.csv", 5.0, bid="X-2"), _move("b.csv", 6.0, bid="X-2")])
        self.assertEqual(len(cm.exception.conflicts), 2)
        self.assertEqual(str(cm.exception).split("\n"), cm.exception.conflicts)
        self.assertIn("5.00 in a.csv:2", cm.exception.conflicts[1])

    def test_an_amount_the_parse_derives_is_said_as_that(self):
        # (review P2-4) A Kraken Hybrid Earn excess is derived from the
        # order of the account's rows: two exports that derive two
        # amounts list those rows differently. Never "different content".
        a = _move("kraken_a.csv", 500.0, desc="Kraken hybridearndeposit USD, over what was parked",
                  _derived=True)
        b = _move("kraken_b.csv", 1500.0, desc=a["desc"], _derived=True)
        with self.assertRaises(CE.CashIdConflict) as cm:
            CE.dedup_across_files([a, b])
        msg = str(cm.exception)
        for part in ("kraken_a.csv:2 and kraken_b.csv:2 are one row of the broker (id X-***",
                     "its amount is derived as 500.00 from kraken_a.csv and as 1,500.00 from "
                     "kraken_b.csv", "the two exports list those rows differently"):
            self.assertIn(part, msg)
        self.assertNotIn("different content", msg)

    def test_what_is_no_conflict(self):
        a = _move("a.csv", 100.0)
        # Under half a cent apart (two exports' precision): one event.
        self.assertEqual(len(CE.dedup_across_files([a, _move("b.csv", 100.004)])), 1)
        # Another description of the same content: one event.
        self.assertEqual(len(CE.dedup_across_files(
            [a, dict(_move("b.csv", 100.0), desc="Deposit")])), 1)
        # Another book, another kind, another id: events of their own.
        self.assertEqual(len(CE.dedup_across_files(
            [a, dict(_move("b.csv", 120.0), book="other/coinbase")])), 2)
        self.assertEqual(len(CE.dedup_across_files([a, _move("b.csv", 120.0, bid="X-9")])), 2)
        flow = dict(CL.flow("2026-03-02", "USD", -1.0, where="b.csv", desc="fee"),
                    file="b.csv", book="coins/coinbase", _broker_id="X-1")
        self.assertEqual(len(CE.dedup_across_files([a, flow])), 2)
        # One file holds an event more of the id than the other (a fee
        # the older export did not state): no copy disagrees.
        fee_a = dict(flow, file="a.csv", where="a.csv")
        self.assertEqual(len(CE.dedup_across_files([a, fee_a, _move("b.csv", 100.0)])), 2)
        # An event more of the id in the newer file alone, alike in
        # amount with another description (a fee and a spread of one
        # row): kept, as before the ids were compared (review P3).
        spread = dict(flow, desc="spread")
        kept = CE.dedup_across_files([fee_a, flow, spread])
        self.assertEqual([(e["file"], e["desc"]) for e in kept],
                         [("a.csv", "fee"), ("b.csv", "spread")])
        self.assertEqual([e["desc"] for e in CE.dedup_across_files([fee_a, spread, flow])],
                         ["fee", "spread"])
        # Two contents of one id in ONE file are the parser's to refuse
        # (two events of one row there): kept, as before.
        self.assertEqual(len(CE.dedup_across_files([a, _move("a.csv", 120.0)])), 2)

    def test_the_copy_kept_names_the_other_files_row(self):
        kept = CE.dedup_across_files([_move("a.csv", 100.0), _move("b.csv", 100.0)])
        self.assertEqual(CE._rows_of(kept[0]), ["a.csv:2", "b.csv:2"])


class TestTheCommandsRefuseIt(unittest.TestCase):
    """The refusal is an input refused: exit 2, one `error:` line."""

    def _project(self, files):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "work").mkdir()
        (root / "filed").mkdir()
        (root / "inputs" / "coins").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\nfx_cash_ledger = "v2"\n'
            'local_timezone = "America/Toronto"\n[accounts.coins]\ntype = "taxable"\n')
        (root / "work" / "coins_sorted.json").write_text(json.dumps({"transactions": []}))
        for name, rows in files.items():
            (root / "inputs" / "coins" / name).write_text(CB_H + rows, encoding="utf-8")
        with patch.object(TZ, "local_tz_name", return_value="America/Toronto"):
            record_run(root)
        return root

    @rule(R.CA_FX_07)
    def test_fx_cash_and_cash_exit_2_naming_both_rows(self):
        root = self._project({"coinbase_old.csv": _deposit("100.00"),
                              "coinbase_new.csv": _deposit("120.00")})
        for args in (("fx-cash", "--ledger", "v2"), ("cash",)):
            with self.subTest(args):
                r = _cli(root, *args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                for part in ("error:", "the broker's id CB***", "120.00 in coinbase_new.csv:2",
                             "against 100.00 in coinbase_old.csv:2"):
                    self.assertIn(part, r.stderr)

    def test_two_conflicts_are_listed_one_below_the_other(self):
        root = self._project({
            "coinbase_old.csv": _deposit("100.00") + _deposit("5.00", "CB-SYN-0002"),
            "coinbase_new.csv": _deposit("120.00") + _deposit("6.00", "CB-SYN-0002")})
        r = _cli(root, "cash")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        lines = [ln for ln in r.stderr.splitlines() if "the broker's id CB***" in ln]
        self.assertEqual(len(lines), 2, r.stderr)
        self.assertIn("error: 2 broker's ids are in two exports with different content",
                      r.stderr)
        from taxjson.pipeline.config import load_config
        self.assertEqual(len(CE.id_conflicts(root, load_config(root), "canada")), 2)

    def test_the_run_says_it_and_strict_stops(self):
        # (review P2-5, TJ-5221) `tjs run` reads no cash event, and said
        # nothing of the conflict unless fx_cash_gains was set: an
        # ATTENTION names it now, whatever the ledger settings, --strict
        # stops, and the checklist's run step lists it.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        root.chmod(0o700)
        (root / "inputs" / "coins").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'local_timezone = "America/Toronto"\n[accounts.coins]\ntype = "taxable"\n'
            'crypto = true\n')
        for name, amount in (("coinbase_old.csv", "100.00"), ("coinbase_new.csv", "120.00")):
            (root / "inputs" / "coins" / name).write_text(CB_H + _deposit(amount),
                                                          encoding="utf-8")
        r = _cli(root, "run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        said = [ln for ln in (r.stdout + r.stderr).splitlines() if "ATTENTION" in ln]
        self.assertEqual(len(said), 1, r.stdout + r.stderr)
        for part in ("the broker's id CB*** of one CASHMOVE is in two exports with different "
                     "content", "120.00 in coinbase_new.csv:2", "100.00 in coinbase_old.csv:2",
                     "keep only the export with the right copy of the row"):
            self.assertIn(part, r.stdout + r.stderr)
        from taxjson.cli.common import _run_state_problems
        from taxjson.pipeline.config import load_config
        with patch.object(TZ, "local_tz_name", return_value="America/Toronto"):
            problems = _run_state_problems(root, load_config(root))
        self.assertTrue(any("the broker's id CB***" in x for x in problems), problems)
        r = _cli(root, "run", "--strict")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("--strict: 1 broker's id(s) on cash events are in two exports with "
                      "different content", r.stderr)

    @rule(R.CA_FX_07)
    def test_identical_copies_are_booked_once(self):
        root = self._project({"coinbase_old.csv": _deposit("100.00"),
                              "coinbase_new.csv": _deposit("100.00")})
        r = _cli(root, "cash", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("CASHMOVE 2026-03-02 USD +100.00"), 1, r.stdout)


if __name__ == "__main__":
    unittest.main()
