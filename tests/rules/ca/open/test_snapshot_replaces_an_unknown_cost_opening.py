"""TJ-5007: an opening snapshot replaces an earlier unknown-cost opening.

A `.tt` line `OPENING <date> <SYMBOL> <qty> cost=unknown` (units held
before the data, CA-ACB-11 / US-BASIS-04) is a position row like any
other: an opening snapshot (`OPENING <date> <SYMBOL> <qty> <CUR>
<total-cost>`) dated on or after it replaces it as it replaces earlier
purchases (CA-OPEN-03 / US-OPEN-03). Before the fix the line was opened
by the stages that compute, after the merge's cut-off had run, so its
units came back on top of the snapshot's: Canada took the sale out of
the totals (manual reporting) and kept 10 phantom units at an unknown
cost; the US realized the right gain and kept the 10 phantom units.

Now the merge holds the lines as rows while it books the renames and
cuts (lib/missing_history `hold_declared_openings`), the snapshot's row
names each line it replaced (`opening_cut_legs`), and no stage opens
that line again (lib/opening `unknown_opening_replaced`).

Every fixture is synthetic: invented tickers, amounts and dates.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, settings_for
from taxjson.rules import ids as R
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_CUR = {"canada": "CAD", "usa": "USD"}
_SFX = {"canada": ".TO", "usa": ".US"}


def _project(tmp, country, tt):
    """A one-account project of `country` whose trades.tt is `tt`
    ({s}: the listing suffix, {c}: the currency, {lot}: a US lot date)."""
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2025, source_currencies=[])
        + '[accounts.margin]\ntype = "taxable"\n')
    acct = root / "inputs" / "margin"
    acct.mkdir(parents=True)
    (acct / "trades.tt").write_text(tt.format(
        s=_SFX[country], c=_CUR[country],
        lot=" 2024-06-03" if country == "usa" else ""))
    return root


def _gains(root, name="margin_gains.json"):
    return json.loads((root / "work" / name).read_text())


def _held(doc):
    return {h["symbol"]: h["qty"] for h in doc.get("inventory") or []
            if abs(h.get("qty") or 0.0) > 1e-9}


# The entry's repro: 10 units opened at an unknown cost on December 1, an
# accurate snapshot of the same 10 units at a cost of 100 on December 31,
# all ten sold on February 3 for 120.
_REPRO = ("OPENING 2024-12-01 SYQ{s} 10 cost=unknown\n"
          "OPENING 2024-12-31 SYQ{s} 10 {c} 100{lot}\n"
          "BUYSELL 2025-02-03 10:00:00 SYQ{s} -10 {c} 12 120 0\n")
# The same, the units renamed between the line and the snapshot: the
# rename's row is left out with the line (it leads into the snapshot).
_RENAMED = ("OPENING 2024-12-01 QZOLD{s} 10 cost=unknown\n"
            "RENAME 2024-12-10 QZOLD{s} QZNEW{s}\n"
            "OPENING 2024-12-31 QZNEW{s} 10 {c} 100{lot}\n"
            "BUYSELL 2025-02-03 10:00:00 QZNEW{s} -10 {c} 12 120 0\n")
# Controls: a line dated after the snapshot, and a line of another symbol.
_AFTER = ("OPENING 2024-12-31 SYQ{s} 10 {c} 100{lot}\n"
          "OPENING 2025-01-15 SYQ{s} 5 cost=unknown\n"
          "BUYSELL 2025-02-03 10:00:00 SYQ{s} -15 {c} 12 180 0\n")
_OTHER = ("OPENING 2024-12-01 QZRX{s} 10 cost=unknown\n"
          "OPENING 2024-12-31 SYQ{s} 10 {c} 100{lot}\n"
          "BUYSELL 2025-02-03 10:00:00 SYQ{s} -10 {c} 12 120 0\n"
          "BUYSELL 2025-02-04 10:00:00 QZRX{s} -10 {c} 12 120 0\n")


class _Runs(unittest.TestCase):

    def _run(self, country, tt):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _project(td.name, country, tt)
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return root, r

    def _flat_with_a_gain_of_20(self, country, tt, sold):
        root, r = self._run(country, tt)
        # Every pass: the account's, the blended one, the native books'.
        for name in ("margin_gains.json", "margin_gains_wash.json",
                     ".blend_gains_wash.json", "margin_raw_gains.json",
                     "margin_raw_base_gains.json"):
            with self.subTest(file=name):
                doc = _gains(root, name)
                self.assertEqual(
                    [(t["symbol"], t["proceeds"], t["cost"], t["gain"])
                     for t in doc["transactions"]],
                    [(sold, 120.0, 100.0, 20.0)])
                self.assertEqual(doc.get("manual_reporting_required") or [],
                                 [])
                self.assertEqual(doc["summary"]["total_gain"], 20.0)
                self.assertEqual(_held(doc), {})
        return root, r


class TestCanada(_Runs):

    @rule(R.CA_OPEN_03)
    def test_snapshot_replaces_the_earlier_unknown_cost_opening(self):
        _root, r = self._flat_with_a_gain_of_20(
            "canada", _REPRO, "SYQ.TO")
        # Never in silence: the line is named, with what to do.
        flat = " ".join(r.stdout.split())
        self.assertIn("inputs/margin/trades.tt:1 (OPENING 2024-12-01 "
                      "SYQ.TO 10 cost=unknown) is dated on or before the "
                      "opening snapshot of SYQ.TO dated 2024-12-31", flat)
        self.assertIn("Delete the line", flat)
        # The sale is computed: nothing is routed to manual reporting.
        self.assertNotIn("sale at an unknown cost", flat)
        self.assertNotIn("never go short any more", flat)

    @rule(R.CA_OPEN_03)
    def test_a_line_renamed_into_the_snapshot_symbol_is_replaced(self):
        # The rename row that made the line the snapshot's is left out
        # with it: the snapshot's row names the line (opening_cut_legs).
        root, _r = self._flat_with_a_gain_of_20(
            "canada", _RENAMED, "QZNEW.TO")
        rows = json.loads((root / "work" / "margin_base.json").read_text())
        snap = [t for t in rows["transactions"]
                if t.get("type") == "opening"]
        self.assertEqual(len(snap), 1, rows["transactions"])
        self.assertEqual(snap[0]["opening_cut_legs"],
                         "2024-12-01 10.0 0.0 OPENING_BALANCE QZOLD.TO")
        # The books on disk never carry the line's row.
        self.assertFalse([t for t in rows["transactions"]
                          if t["action"] == "OPENING_BALANCE"])

    @rule(R.CA_ACB_11)
    def test_control_a_line_dated_after_the_snapshot_still_opens(self):
        root, r = self._run("canada", _AFTER)
        doc = _gains(root)
        self.assertEqual(doc["transactions"], [])
        self.assertEqual([(t["symbol"], t["proceeds"])
                          for t in doc["manual_reporting_required"]],
                         [("SYQ.TO", 180.0)])
        self.assertEqual(_held(doc), {})
        self.assertNotIn("is dated on or before", " ".join(r.stdout.split()))

    @rule(R.CA_ACB_11)
    def test_control_a_line_of_another_symbol_still_opens(self):
        root, r = self._run("canada", _OTHER)
        doc = _gains(root)
        self.assertEqual([(t["symbol"], t["gain"])
                          for t in doc["transactions"]], [("SYQ.TO", 20.0)])
        self.assertEqual([(t["symbol"], t["proceeds"])
                          for t in doc["manual_reporting_required"]],
                         [("QZRX.TO", 120.0)])
        self.assertEqual(_held(doc), {})
        self.assertNotIn("is dated on or before", " ".join(r.stdout.split()))


class TestUsa(_Runs):

    @rule(R.US_OPEN_03)
    def test_snapshot_replaces_the_earlier_unknown_cost_opening(self):
        _root, r = self._flat_with_a_gain_of_20("usa", _REPRO, "SYQ.US")
        self.assertIn("inputs/margin/trades.tt:1 (OPENING 2024-12-01 "
                      "SYQ.US 10 cost=unknown) is dated on or before the "
                      "opening snapshot of SYQ.US dated 2024-12-31",
                      " ".join(r.stdout.split()))

    @rule(R.US_OPEN_03)
    def test_a_line_renamed_into_the_snapshot_symbol_is_replaced(self):
        self._flat_with_a_gain_of_20("usa", _RENAMED, "QZNEW.US")


def _two_accounts(tmp, a_tt, b_tt):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(
        settings_for("canada", year=2025, source_currencies=[])
        + '[accounts.a]\ntype = "taxable"\n[accounts.b]\ntype = "taxable"\n')
    for name, tt in (("a", a_tt), ("b", b_tt)):
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "trades.tt").write_text(tt)
    return root


class TestOneLineOneReading(unittest.TestCase):
    """TJ-5241: a line whose replacement only another account's rename
    row shows. The books that pool the accounts would follow that row to
    the snapshot (the line replaced), the account's own books cannot
    (the line opened, 10 units of unknown cost carried into its
    holdings): refused, naming the line, both readings and the remedy."""

    A = ("OPENING 2024-12-01 QZOLD.TO 10 cost=unknown\n"
         "OPENING 2024-12-31 QZNEW.TO 10 CAD 100\n"
         "BUYSELL 2025-02-03 10:00:00 QZNEW.TO -10 CAD 12 120 0\n")
    B = ("BUYSELL 2024-11-20 10:00:00 QZOLD.TO 10 CAD 10 100 0\n"
         "SPLIT 2024-12-10 00:00:00 QZOLD.TO QZNEW.TO 1\n"
         "BUYSELL 2025-02-11 10:00:00 QZNEW.TO -10 CAD 12 120 0\n")

    @rule(R.CA_OPEN_03)
    def test_refused_then_one_reading_with_the_named_remedy(self):
        with tempfile.TemporaryDirectory() as td:
            root = _two_accounts(td, self.A, self.B)
            r = cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout[-2000:])
            said = " ".join((r.stdout + r.stderr).split())
            self.assertIn("inputs/a/trades.tt:1 (OPENING 2024-12-01 "
                          "QZOLD.TO 10 cost=unknown) is read two ways", said)
            self.assertIn("the rename QZOLD.TO -> QZNEW.TO of 2024-12-10 in "
                          "account b's rows", said)
            self.assertIn("the opening snapshot of QZNEW.TO dated "
                          "2024-12-31 in account a, which replaces the line",
                          said)
            self.assertIn("they open the line's 10 units at an unknown cost",
                          said)
            self.assertIn("add `RENAME 2024-12-10 QZOLD.TO QZNEW.TO` to a "
                          ".tt file of account a", said)
            self.assertIn("or delete the line", said)
            # The remedy, as named: every pass reads the line alike, and
            # the account's holdings carry no unit of unknown cost.
            with open(root / "inputs" / "a" / "trades.tt", "a") as f:
                f.write("RENAME 2024-12-10 QZOLD.TO QZNEW.TO\n")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            self.assertIn("inputs/a/trades.tt:1 (OPENING 2024-12-01 "
                          "QZOLD.TO 10 cost=unknown) is dated on or before "
                          "the opening snapshot of QZNEW.TO dated 2024-12-31",
                          " ".join(r.stdout.split()))
            for name in ("a_gains.json", "a_gains_wash.json",
                         "a_raw_gains.json", ".blend_gains_wash.json"):
                with self.subTest(file=name):
                    doc = _gains(root, name)
                    self.assertEqual(doc.get("manual_reporting_required")
                                     or [], [])
                    self.assertEqual(_held(doc), {})
            self.assertEqual(_gains(root, ".blend_gains_wash.json")
                             ["summary"]["total_gain"], 40.0)
            self.assertNotIn("QZOLD", (root / "reports" /
                                       "a_holdings.toml").read_text())

    @rule(R.CA_OPEN_03)
    def test_the_applier_refuses_and_says_an_unsaid_replacement(self):
        import contextlib
        import io
        from taxjson.model.transaction import TaxTransaction as T
        from taxjson.lib.missing_history import (TtOpeningError,
                                                 load_missing_history,
                                                 synthesize_openings)
        from taxjson.lib.opening import opening_row
        fields = T.__dataclass_fields__

        def snap(sym):
            row = opening_row(symbol=sym, quantity=10, total_cost=100,
                              day="2024-12-31", currency="CAD", account="a")
            return T(**{k: v for k, v in row.items() if k in fields})
        rename = dict(action="SPLIT", date="2024-12-10", symbol="QZOLD.TO",
                      symbol_new="QZNEW.TO", quantity=1.0)
        with tempfile.TemporaryDirectory() as td:
            root = _two_accounts(
                td, "OPENING 2024-12-01 QZOLD.TO 10 cost=unknown\n", "")
            pairs = load_missing_history(root)
            # Another account's rename row only: one line, two readings.
            with self.assertRaises(TtOpeningError) as cm:
                synthesize_openings(
                    [snap("QZNEW.TO"), T(account="b", **rename)], pairs)
            self.assertIn("is read two ways", str(cm.exception))
            # The account's own rename row, and no merge said it (no
            # leg on the snapshot's row): said here, never in silence.
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                out, log = synthesize_openings(
                    [snap("QZNEW.TO"), T(account="a", **rename)], pairs)
            self.assertFalse([t for t in out
                              if t.action == "OPENING_BALANCE"])
            self.assertIn("ATTENTION: opening: a: inputs/a/trades.tt:1 "
                          "(OPENING 2024-12-01 QZOLD.TO 10 cost=unknown) is "
                          "dated on or before the opening snapshot of "
                          "QZNEW.TO dated 2024-12-31",
                          " ".join(err.getvalue().split()))
            self.assertNotIn("said", log[0])


class TestTheViewsStateAReplacedLine(_Runs):
    """`tjs find-missing-history`, the checklist's missing-history step
    and the run's notes say what a replaced line is: it opens nothing,
    its sales are in the totals, and it covers no later short."""

    @rule(R.CA_OPEN_03)
    def test_finder_and_checklist(self):
        root, _r = self._run("canada", _REPRO)
        r = cli(root, "find-missing-history", "--details")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        said = " ".join(r.stdout.split())
        self.assertIn("REPLACED BY AN OPENING SNAPSHOT — OPENING "
                      "cost=unknown entries that open nothing: 1", said)
        self.assertIn("REPLACED in inputs/margin/trades.tt - an opening "
                      "snapshot dated on or after the line replaces it: the "
                      "run opens nothing for it and its sales are in the "
                      "totals at the snapshot's cost; delete it:", said)
        self.assertIn("SYQ.TO margin inputs/margin/trades.tt:1", said)
        self.assertIn("the opening snapshot of SYQ.TO dated 2024-12-31 "
                      "replaces this line", said)
        # Not the stale wording: the run opens nothing for it.
        self.assertNotIn("STALE in", r.stdout)
        self.assertNotIn("still opens", said)
        self.assertNotIn("stay out of the totals", said)
        r = cli(root, "checklist", "--run", timeout=600)
        said = " ".join(r.stdout.split())
        self.assertIn("1 missing-history entry is replaced by an opening "
                      "snapshot: it opens nothing and the sales are in the "
                      "totals — delete: SYQ.TO (margin, "
                      "inputs/margin/trades.tt:1)", said)
        self.assertNotIn("still opens its units", said)

    @rule(R.CA_OPEN_03)
    def test_a_replaced_line_covers_no_later_short(self):
        # 25 sold against the snapshot's 10: the sale is one with no
        # purchase, and the replaced line does not answer for it.
        root, r = self._run("canada", _REPRO.replace("-10 {c} 12 120",
                                                     "-25 {c} 12 300"))
        said = " ".join(r.stdout.split())
        self.assertIn("1 position(s) go short in margin's data (SYQ.TO)",
                      said)
        self.assertIn("1 position sold in 2025 with no purchase in your "
                      "files", said)
        r = cli(root, "find-missing-history")
        self.assertIn("AFFECTS 2025", r.stdout)
        self.assertNotIn("COVERED by", r.stdout)

    @rule(R.CA_OPEN_03)
    def test_a_declared_short_is_worded_as_one(self):
        _root, r = self._run("canada", _REPRO.replace(
            "SYQ{s} 10 cost=unknown", "SYQ{s} -10 cost=unknown"))
        said = " ".join(r.stdout.split())
        self.assertIn("(OPENING 2024-12-01 SYQ.TO -10 cost=unknown) is "
                      "dated on or before the opening snapshot of SYQ.TO "
                      "dated 2024-12-31, which replaces it as it replaces "
                      "the earlier rows: the short it declares is not "
                      "opened", said)
        self.assertIn("only for a short sold after it", said)
        self.assertNotIn("its units are not opened", said)

    @rule(R.CA_ACB_TRANSFER_BV)
    def test_a_replaced_line_answers_for_no_arrival(self):
        from taxjson.lib.opening import opening_row
        from taxjson.pipeline.stages import _opening_pairs
        snap = opening_row(symbol="SYQ.TO", quantity=10, total_cost=100,
                           day="2024-12-31", currency="CAD",
                           account="margin")
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, "canada",
                            "OPENING 2024-12-01 SYQ{s} 10 cost=unknown\n"
                            "OPENING 2024-12-02 QZRX{s} 5 cost=unknown\n")
            self.assertEqual(_opening_pairs(root, {"margin": [snap]}, []),
                             {("QZRX.TO", "margin")})
            # Control: without the snapshot both lines answer.
            self.assertEqual(_opening_pairs(root, {"margin": []}, []),
                             {("SYQ.TO", "margin"), ("QZRX.TO", "margin")})


class TestTheCutOffNamesAHeldLine(_Runs):
    """The cut-off's walk back passes the held line: a refusal that
    depends on it names it and its remedy, and a buy that reads as a
    cover only through the line's units is refused for what it is."""

    def _refused(self, tt):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _project(td.name, "canada", tt)
        r = cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0, r.stdout[-2000:])
        return " ".join((r.stdout + r.stderr).split())

    @rule(R.CA_OPEN_03)
    def test_units_that_do_not_fit_the_snapshot_are_no_short_cover(self):
        said = self._refused(
            "BUYSELL 2025-01-15 10:00:00 SYQ{s} 5 {c} 10 50 0\n"
            "OPENING 2025-02-01 SYQ{s} 10 cost=unknown\n"
            "OPENING 2025-06-30 SYQ{s} 10 {c} 100\n"
            "BUYSELL 2025-09-03 10:00:00 SYQ{s} -10 {c} 12 120 0\n")
        self.assertIn("the opening balance of SYQ.TO dated 2025-06-30 (10 "
                      "units) does not fit the rows it replaces: walked "
                      "back from the snapshot through "
                      "inputs/margin/trades.tt:2 (OPENING 2025-02-01 SYQ.TO "
                      "10 cost=unknown), the position before the 2025-01-15 "
                      "buy of 5 SYQ.TO is -5, so the line's 10 units and "
                      "the rows before it hold 5 more than the snapshot",
                      said)
        self.assertIn("Delete the OPENING cost=unknown line", said)
        self.assertNotIn("short cover(s)", said)

    @rule(R.CA_OPEN_03)
    def test_a_return_of_capital_refusal_names_the_line(self):
        said = self._refused(
            "BUYSELL 2025-01-15 10:00:00 SYQ{s} 10 {c} 10 100 0\n"
            "ADJUST 2025-02-10 10:00:00 SYQ{s} {c} -5 type=roc\n"
            "OPENING 2025-03-01 SYQ{s} 5 cost=unknown\n"
            "OPENING 2025-06-30 SYQ{s} 15 {c} 150\n"
            "BUYSELL 2025-09-03 10:00:00 SYQ{s} -15 {c} 12 180 0\n")
        self.assertIn("would leave out 1 return(s) of capital of the 2025 "
                      "tax year", said)
        self.assertIn("The walk back from the snapshot passes "
                      "inputs/margin/trades.tt:3 (OPENING 2025-03-01 SYQ.TO "
                      "5 cost=unknown), whose cost is unknown", said)
        self.assertIn("delete that line (the snapshot holds its units) or "
                      "replace it with the purchase and its cost", said)

    @rule(R.CA_OPEN_03)
    def test_control_a_real_cover_keeps_its_refusal(self):
        said = self._refused(
            "BUYSELL 2025-01-10 10:00:00 SYQ{s} -5 {c} 10 50 0\n"
            "BUYSELL 2025-01-15 10:00:00 SYQ{s} 5 {c} 10 50 0\n"
            "OPENING 2025-06-30 SYQ{s} 10 {c} 100\n"
            "BUYSELL 2025-09-03 10:00:00 SYQ{s} -10 {c} 12 120 0\n")
        self.assertIn("would leave out", said)
        self.assertNotIn("does not fit the rows it replaces", said)


class TestTheOneApplier(unittest.TestCase):
    """lib/missing_history.synthesize_openings is the one applier every
    stage shares: a replaced line opens nothing there, whoever calls."""

    def _books(self, tt_rows):
        from taxjson.model.transaction import TaxTransaction as T
        from taxjson.lib.opening import opening_row
        fields = T.__dataclass_fields__
        snap = opening_row(symbol="SYQ.TO", quantity=10, total_cost=100,
                           day="2024-12-31", currency="CAD",
                           account="margin")
        return [T(**{k: v for k, v in snap.items() if k in fields}),
                T(action="BUYSELL", date="2025-02-03", time="10:00:00",
                  symbol="SYQ.TO", quantity=-10, price=12, net_amount=120,
                  currency="CAD", account="margin")] + tt_rows

    def _pairs(self, td, line):
        from taxjson.lib.missing_history import load_missing_history
        root = _project(td, "canada", line + "\n")
        return load_missing_history(root)

    @rule(R.CA_OPEN_03)
    def test_a_line_on_or_before_the_snapshot_opens_nothing(self):
        from taxjson.lib.missing_history import synthesize_openings
        for day in ("2024-12-01", "2024-12-31"):
            with self.subTest(day=day), \
                    tempfile.TemporaryDirectory() as td:
                pairs = self._pairs(
                    td, f"OPENING {day} SYQ.TO 10 cost=unknown")
                out, log = synthesize_openings(self._books([]), pairs,
                                               flag_stale=False)
                self.assertFalse([t for t in out
                                  if t.action == "OPENING_BALANCE"])
                self.assertEqual(len(log), 1, log)
                self.assertFalse(log[0]["inserted"])
                self.assertEqual(log[0]["replaced_by_snapshot"],
                                 {"symbol": "SYQ.TO", "date": "2024-12-31"})
                self.assertIn("replaced by the opening snapshot",
                              log[0]["note"])

    @rule(R.CA_ACB_11)
    def test_control_a_line_after_the_snapshot_is_opened(self):
        from taxjson.lib.missing_history import synthesize_openings
        with tempfile.TemporaryDirectory() as td:
            pairs = self._pairs(td,
                                "OPENING 2025-01-02 SYQ.TO 10 cost=unknown")
            out, log = synthesize_openings(self._books([]), pairs,
                                           flag_stale=False)
            opened = [t for t in out if t.action == "OPENING_BALANCE"]
            self.assertEqual([(t.symbol, t.date, t.quantity)
                              for t in opened],
                             [("SYQ.TO", "2025-01-02", 10.0)])
            self.assertTrue(log[0]["inserted"])
            self.assertNotIn("replaced_by_snapshot", log[0])


class TestTheCutOffNamesTheLine(unittest.TestCase):
    """lib/opening: the cut-off leaves the line's row out and names it on
    the snapshot's row; the premium ledger never reads that leg."""

    def _rows(self):
        from taxjson.model.transaction import TaxTransaction as T
        from taxjson.lib.opening import opening_row
        fields = T.__dataclass_fields__
        snap = opening_row(symbol="SYQ.TO", quantity=10, total_cost=100,
                           day="2024-12-31", currency="CAD",
                           account="margin")
        return [T(action="OPENING_BALANCE", date="2024-12-01",
                  time="00:00:00", symbol="SYQ.TO", quantity=10, price=0.0,
                  net_amount=0.0, currency="CAD", account="margin"),
                T(**{k: v for k, v in snap.items() if k in fields})]

    @rule(R.CA_OPEN_03)
    def test_leg_written_and_read_back(self):
        import io
        from taxjson.lib.opening import (apply_opening_cutoff,
                                         unknown_opening_replaced)
        cut = []
        kept = apply_opening_cutoff(self._rows(), report=io.StringIO(),
                                    cut=cut)
        self.assertEqual([t.action for t in cut], ["OPENING_BALANCE"])
        self.assertEqual([t.opening_cut_legs for t in kept],
                         ["2024-12-01 10.0 0.0 OPENING_BALANCE"])
        self.assertEqual(
            unknown_opening_replaced(kept, "margin", ["SYQ.TO"],
                                     "2024-12-01"),
            ("SYQ.TO", "2024-12-31"))
        # Another account, another symbol, a later date: not replaced.
        self.assertIsNone(unknown_opening_replaced(
            kept, "other", ["SYQ.TO"], "2024-12-01"))
        self.assertIsNone(unknown_opening_replaced(
            kept, "margin", ["QZRX.TO"], "2024-12-01"))
        self.assertIsNone(unknown_opening_replaced(
            kept, "margin", ["SYQ.TO"], "2025-01-02"))

    @rule(R.CA_OPEN_03)
    def test_the_premium_ledger_does_not_read_the_leg(self):
        from taxjson.model.evidence import (UNKNOWN_OPENING_LEG,
                                            cut_leg_action)
        self.assertEqual(cut_leg_action(
            "2024-12-01 10.0 0.0 OPENING_BALANCE QZOLD.TO"),
            UNKNOWN_OPENING_LEG)
        self.assertEqual(cut_leg_action("2024-12-01 10.0 5.0 BUYSELL"),
                         "BUYSELL")
        self.assertEqual(cut_leg_action("damaged"), "")


if __name__ == "__main__":
    unittest.main()
