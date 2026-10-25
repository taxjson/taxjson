"""Regression pins for the 2026-10-24 audit's holdings-export findings
(B11, B12, B13, B21, B22, B23, B44): `taxjson_export`'s transfer
evidence walk, contract multiplier, trade history and base-currency
figures. Synthetic data only; every temp file in a folder of its own.
"""
import io
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

from _style import CapturedWidth

from taxjson.bin import taxjson_export as export
from taxjson.lib.tomlcompat import tomllib


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _tx(d, q, sym="ZZQ.US", action="BUYSELL", **kw):
    return dict(date=d, date_settle=d, time="10:00:00", account="margin",
                symbol=sym, action=action, quantity=q, net_amount=0,
                currency="USD", **kw)


# A ticker map joining the two listings of one security (TOBASE).
_TMAP = SimpleNamespace(glob={}, tobase={"ZZQ.US": "ZZQ.TO"}, journal={})


def _sidecar(folder, rows, name="transfer.json"):
    p = Path(folder) / name
    p.write_text(json.dumps({"metadata": {"kind": "transfer_sidecar",
                                          "account": "margin"},
                             "transactions": rows}))
    return p


def _evidence(rows, paths_of=lambda p: [p], qty=20):
    """Apply a sidecar of `rows` to a ZZQ.US holding of `qty`; the
    quantities per listing after it, the moves and the stderr text."""
    with tempfile.TemporaryDirectory() as td:
        p = _sidecar(td, rows)
        agg = {"ZZQ.US": {"qty": qty, "total_cost": qty * 10,
                          "currency": "USD",
                          "cost_by_currency": {"USD": qty * 10}}}
        err = io.StringIO()
        with redirect_stderr(err):
            moves = export._apply_transfer_evidence(agg, paths_of(p), _TMAP)
    return {s: b["qty"] for s, b in agg.items()}, moves, err.getvalue()


def _trade_events(rows):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "book.json"
        p.write_text(json.dumps({"transactions": rows}))
        return export._load_trade_events([p])


class TestTransferEvidence(unittest.TestCase):

    def test_B11_repeated_sidecar_argument_moves_the_journal_once(self):
        """B11: Repeated transfer-evidence argument doubles a listing
        journal. The same sidecar named twice (and by another spelling
        of its path) is read once: 5 of 20 move, not 10."""
        legs = [_tx("2026-01-01", -5, action="TRANSFER"),
                _tx("2026-01-01", 5, sym="ZZQ.TO", action="TRANSFER")]
        qtys, moves, _ = _evidence(legs, lambda p: [p, p])
        self.assertEqual(qtys, {"ZZQ.US": 15.0, "ZZQ.TO": 5.0})
        self.assertEqual(moves, [("ZZQ.US", "ZZQ.TO", 5.0)])
        qtys, _, _ = _evidence(legs, lambda p: [str(p), Path(p).parent
                                                / "." / p.name])
        self.assertEqual(qtys, {"ZZQ.US": 15.0, "ZZQ.TO": 5.0})

    def test_B11_identical_rows_of_two_sidecars_count_once(self):
        """B11: a copy of the sidecar under another name holds the same
        rows (the same content ids): one journal, as one broker's
        overlapping exports are read."""
        legs = [_tx("2026-01-01", -5, action="TRANSFER"),
                _tx("2026-01-01", 5, sym="ZZQ.TO", action="TRANSFER")]
        with tempfile.TemporaryDirectory() as td:
            a = _sidecar(td, legs, "a_transfers.json")
            b = _sidecar(td, legs, "b_transfers.json")
            agg = {"ZZQ.US": {"qty": 20.0, "total_cost": 200.0,
                              "currency": "USD"}}
            with redirect_stderr(io.StringIO()):
                export._apply_transfer_evidence(agg, [a, b], _TMAP)
        self.assertEqual({s: v["qty"] for s, v in agg.items()},
                         {"ZZQ.US": 15.0, "ZZQ.TO": 5.0})

    def test_B12_legs_years_apart_are_not_one_journal(self):
        """B12: Transfer evidence pairs unrelated departures and
        arrivals six years apart. A 2020 out-leg and a 2026 in-leg are
        two custody moves: nothing moves, and a note names them."""
        legs = [_tx("2020-01-01", -5, action="TRANSFER"),
                _tx("2026-01-01", 5, sym="ZZQ.TO", action="TRANSFER")]
        qtys, moves, err = _evidence(legs)
        self.assertEqual(qtys, {"ZZQ.US": 20})
        self.assertEqual(moves, [])
        self.assertIn("note: transfer evidence: ZZQ.US -5 on 2020-01-01 "
                      "and ZZQ.TO +5 on 2026-01-01 are more than 5 "
                      "business days apart", err)
        # Legs a few business days apart (the broker posts the in-leg
        # after the out-leg) are one journal.
        legs[0]["date"] = "2025-12-29"
        qtys, _, _ = _evidence(legs)
        self.assertEqual(qtys, {"ZZQ.US": 15.0, "ZZQ.TO": 5.0})

    def test_B12_flip_and_flip_back_net_to_nothing(self):
        """B12 (the design kept): two journals months apart that undo
        each other move nothing, whatever the listings hold today."""
        legs = [_tx("2026-01-05", -5, action="TRANSFER"),
                _tx("2026-01-05", 5, sym="ZZQ.TO", action="TRANSFER"),
                _tx("2026-06-05", -5, sym="ZZQ.TO", action="TRANSFER"),
                _tx("2026-06-05", 5, action="TRANSFER")]
        qtys, moves, _ = _evidence(legs)
        self.assertEqual(moves, [])
        self.assertEqual(qtys, {"ZZQ.US": 20})

    def test_B13_short_position_journals_to_the_new_listing(self):
        """B13: Transfer evidence cannot journal a short position to
        its new listing. A short of 10 ZZQ.US journaled (legs +10 US,
        -10 TO) is held under ZZQ.TO, its cost with it; the base
        inventory replays the move."""
        legs = [_tx("2026-01-01", 10, action="TRANSFER"),
                _tx("2026-01-01", -10, sym="ZZQ.TO", action="TRANSFER")]
        qtys, moves, err = _evidence(legs, qty=-10)
        self.assertEqual(qtys, {"ZZQ.TO": -10.0})
        self.assertEqual(moves, [("ZZQ.US", "ZZQ.TO", -10.0)])
        self.assertIn("note: applied evidenced depot flip: -10 ZZQ.US -> "
                      "ZZQ.TO (a short position; transfer sidecar).", err)
        base = {"ZZQ.US": {"qty": -10.0, "total_cost": -130.0,
                           "currency": "CAD",
                           "cost_by_currency": {"CAD": -130.0}}}
        export._replay_moves_on_base(base, moves, _TMAP)
        self.assertEqual(set(base), {"ZZQ.TO"})
        self.assertAlmostEqual(base["ZZQ.TO"]["qty"], -10.0)
        self.assertAlmostEqual(base["ZZQ.TO"]["total_cost"], -130.0)
        self.assertEqual(base["ZZQ.TO"]["currency"], "CAD")

    def test_B13_long_journal_still_moves_the_long(self):
        """B13 (the long path kept): the out-leg's listing holds the
        long; its cost moves in proportion."""
        legs = [_tx("2026-01-01", -5, action="TRANSFER"),
                _tx("2026-01-01", 5, sym="ZZQ.TO", action="TRANSFER")]
        with tempfile.TemporaryDirectory() as td:
            p = _sidecar(td, legs)
            agg = {"ZZQ.US": {"qty": 20.0, "total_cost": 200.0,
                              "currency": "USD",
                              "cost_by_currency": {"USD": 200.0}}}
            with redirect_stderr(io.StringIO()):
                export._apply_transfer_evidence(agg, [p], _TMAP)
        self.assertAlmostEqual(agg["ZZQ.US"]["total_cost"], 150.0)
        self.assertAlmostEqual(agg["ZZQ.TO"]["total_cost"], 50.0)
        self.assertAlmostEqual(agg["ZZQ.TO"]["cost_by_currency"]["USD"],
                               50.0)


_OPT = "ZZQ270115C00010000.US"


def _args(**kw):
    a = dict(account_name=None, inputs=[], base_currency=None, country=None,
             dust_threshold=1e-9, no_equities=False, no_options=False,
             no_futures=False, no_cad=False, no_usd=False, short=False,
             long=False)
    a.update(kw)
    return Namespace(**a)


class TestContractMultiplier(unittest.TestCase):

    def test_B21_conflicting_multipliers_are_unknown(self):
        """B21: Conflicting declared option multipliers revert to an
        invented size of 100. A bucket flagged multiplier_conflict has
        no contract size (None), not the equity 100."""
        self.assertIsNone(export.contract_multiplier_of(
            _OPT, {"multiplier": 25, "multiplier_conflict": True}))
        self.assertEqual(export.contract_multiplier_of(
            _OPT, {"multiplier": 25}), 25.0)
        self.assertEqual(export.contract_multiplier_of(_OPT, {}), 100.0)

    def test_B21_conflict_is_warned_and_written_as_unknown(self):
        """B21: two inputs declaring 25 and 50 warn once, naming the
        sizes; the snapshot writes `contract_multiplier_unknown = true`
        and no `contract_multiplier`, and reads that back as unknown."""
        agg = {}
        err = io.StringIO()
        with redirect_stderr(err):
            for m in (25, 50, 25):
                export.process_data_report(
                    {"inventory": [{"symbol": _OPT, "qty": 1.0,
                                    "total_cost": 50.0, "currency": "USD",
                                    "multiplier": m}]},
                    _args(), agg)
        self.assertTrue(agg[_OPT]["multiplier_conflict"])
        self.assertEqual(err.getvalue().count("warning:"), 1)
        self.assertIn(f"warning: {_OPT}: its inputs declare different "
                      f"contract sizes (25, 50)", err.getvalue())
        with redirect_stderr(io.StringIO()):
            text = "\n".join(export.render_holdings_toml(agg, _args()))
        h = tomllib.loads(text)["holding"][0]
        self.assertNotIn("contract_multiplier", h)
        self.assertIs(h["contract_multiplier_unknown"], True)
        self.assertIn("# contract_multiplier unknown: the inputs declare "
                      "different contract sizes (25, 50)", text)
        # Read back: still unknown, never 100.
        inv = export._holdings_toml_to_inventory(tomllib.loads(text))
        again = {}
        with redirect_stderr(io.StringIO()):
            export.process_data_report(inv, _args(), again)
        self.assertIsNone(export.contract_multiplier_of(_OPT, again[_OPT]))
        # A declared size agreed on is written as before.
        agreed = {_OPT: {"qty": 1.0, "total_cost": 50.0, "currency": "USD",
                         "multiplier": 25.0, "position_start_date": None}}
        h = tomllib.loads("\n".join(export.render_holdings_toml(
            agreed, _args())))["holding"][0]
        self.assertEqual(h["contract_multiplier"], 25)
        self.assertNotIn("contract_multiplier_unknown", h)


class TestTradeHistory(unittest.TestCase):

    def test_B22_crossing_from_long_to_short_starts_a_new_round(self):
        """B22: Holdings trade history does not reset when a trade
        crosses from long to short. Buy 10, sell 20, buy 5: the current
        (short) position's events start at the sale, kept as the 10
        that opened the short."""
        evs = _trade_events([_tx("2026-01-01", 10, price=10),
                             _tx("2026-01-02", -20, price=11),
                             _tx("2026-01-03", 5, price=12)])
        self.assertEqual([(e["date"], e["action"], e["qty"], e["price"])
                          for e in evs["ZZQ.US"]],
                         [("2026-01-02", "SELL", 10.0, 11.0),
                          ("2026-01-03", "BUY", 5.0, 12.0)])
        # Short to long crosses the same way; a transfer crossing zero
        # leaves no trade to chart for the new position.
        evs = _trade_events([_tx("2026-01-01", -10, price=10),
                             _tx("2026-01-02", 30, price=11)])
        self.assertEqual([(e["action"], e["qty"]) for e in evs["ZZQ.US"]],
                         [("BUY", 20.0)])
        evs = _trade_events([_tx("2026-01-01", 10, price=10),
                             _tx("2026-01-02", -30, action="TRANSFER")])
        self.assertNotIn("ZZQ.US", evs)

    def test_B22_flat_and_uncrossed_rounds_unchanged(self):
        """B22 (kept): a round closed to zero drops; a sale that leaves
        a long keeps every event."""
        evs = _trade_events([_tx("2026-01-01", 10), _tx("2026-01-02", -10),
                             _tx("2026-01-03", 5)])
        self.assertEqual([e["date"] for e in evs["ZZQ.US"]], ["2026-01-03"])
        evs = _trade_events([_tx("2026-01-01", 10), _tx("2026-01-02", -4)])
        self.assertEqual([e["qty"] for e in evs["ZZQ.US"]], [10.0, 4.0])

    def test_B44_rename_merges_events_in_replay_order(self):
        """B44: Holdings rename appends older trades after newer trades
        in chart history. ZZQ.US bought 01-01, ZZR.US bought 01-03,
        ZZQ.US renamed to ZZR.US on 01-04: the merged history is dated
        in order."""
        evs = _trade_events([_tx("2026-01-01", 10),
                             _tx("2026-01-03", 5, sym="ZZR.US"),
                             _tx("2026-01-04", 1, action="SPLIT",
                                 symbol_new="ZZR.US")])
        self.assertEqual([e["date"] for e in evs["ZZR.US"]],
                         ["2026-01-01", "2026-01-03"])
        self.assertNotIn("ZZQ.US", evs)


class TestBaseCurrencyFigures(unittest.TestCase):

    def test_B23_mixed_currency_base_bucket_has_no_base_total(self):
        """B23: Holdings export labels a mixed-currency base bucket as
        entirely CAD. A base bucket holding CAD 100 + USD 100 gets no
        base_total_cost; a comment and a warning name the currencies."""
        args = _args(account_name="margin", base_currency="CAD",
                     country="canada")
        native = {"ZZQ.US": {"qty": 10, "total_cost": 100,
                             "currency": "USD"}}
        base = {"ZZQ.US": {"qty": 10, "total_cost": 200, "currency": "CAD",
                           "mixed_currency": True,
                           "cost_by_currency": {"CAD": 100, "USD": 100}}}
        err = io.StringIO()
        with redirect_stderr(err):
            lines = export.render_holdings_toml(native, args, base)
        self.assertEqual([l for l in lines
                          if l.startswith("base_total_cost")], [])
        h = tomllib.loads("\n".join(lines))["holding"][0]
        self.assertNotIn("base_currency", h)
        self.assertNotIn("base_cost_per_share", h)
        self.assertEqual(h["total_cost"], 100)
        self.assertIn("# base_total_cost omitted: this holding's "
                      "base-currency figures are in CAD, USD", "\n".join(lines))
        self.assertIn("warning: ZZQ.US: its base-currency figures are in "
                      "CAD, USD: base_total_cost is omitted", err.getvalue())
        # A converted bucket keeps its base figures.
        base["ZZQ.US"] = {"qty": 10, "total_cost": 200, "currency": "CAD",
                          "cost_by_currency": {"CAD": 200}}
        h = tomllib.loads("\n".join(export.render_holdings_toml(
            native, args, base)))["holding"][0]
        self.assertEqual(h["base_total_cost"], 200)


if __name__ == "__main__":
    unittest.main()
