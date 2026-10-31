"""Fix round 8, the `trades` list of reports/<account>_holdings.toml:
TJ-1342 (a move in kind wiped the position's history), TJ-1344 (a position
that arrived by transfer looked bought or empty), TJ-1345 (same-clock SELL
and BUY of a flat symbol listed one reduced BUY).

Every fixture is synthetic: invented tickers and amounts.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import settings_for

from taxjson.bin import taxjson_export as export
from taxjson.lib.core import LOT_MOVE_TYPE
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.tomlcompat import tomllib

SRC = Path(__file__).resolve().parent.parent / "src"


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _events(rows, country=None, **kw):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "raw.json"
        p.write_text(json.dumps({"transactions": rows}))
        return export._load_trade_events([p], country=country, **kw)


def _row(d, q, price=0.0, t="10:00:00", action="BUYSELL", sym="SYN.TO", **kw):
    return dict(date=d, date_settle=d, time=t, action=action, symbol=sym,
                quantity=q, price=price, currency="CAD", account="rrsp",
                **kw)


def _brief(evs):
    return [(e["date"], e["action"], e["qty"]) for e in evs]


class TestTiedSellAndBuy(unittest.TestCase):
    """TJ-1345."""

    def test_same_clock_sell_then_buy_of_a_flat_symbol_lists_both(self):
        for country in ("canada", "us"):
            ev = _events([_row("2025-03-03", -10, 11),
                          _row("2025-03-03", 30, 10)], country)
            self.assertEqual(
                [(e["action"], e["qty"], e["price"]) for e in ev["SYN.TO"]],
                [("BUY", 30.0, 10.0), ("SELL", 10.0, 11.0)], country)

    def test_listed_as_at_different_clocks(self):
        tied = _events([_row("2025-03-03", -10, 11),
                        _row("2025-03-03", 30, 10)])
        apart = _events([_row("2025-03-03", 30, 10, t="10:00:00"),
                         _row("2025-03-03", -10, 11, t="11:00:00")])
        self.assertEqual(tied, apart)

    def test_a_further_tied_row_keeps_the_net(self):
        ev = _events([_row("2025-03-03", 30, 10), _row("2025-03-03", -40, 11),
                      _row("2025-03-03", 20, 12)])
        self.assertEqual(
            sorted((e["action"], e["qty"]) for e in ev["SYN.TO"]),
            [("BUY", 20.0), ("BUY", 30.0), ("SELL", 40.0)])

    def test_a_tied_row_after_a_real_crossing_nets_too(self):
        # 10 held from an earlier day: the sale's first 10 close that
        # round (B22), the tied buy crosses what is left of the sale.
        ev = _events([_row("2025-03-01", 10, 9), _row("2025-03-03", -20, 11),
                      _row("2025-03-03", 30, 10)])
        self.assertEqual(_brief(ev["SYN.TO"]),
                         [("2025-03-03", "BUY", 30.0),
                          ("2025-03-03", "SELL", 10.0)])

    def test_control_crossing_a_position_opened_earlier_stays_b22(self):
        ev = _events([_row("2025-03-01", -10, 10), _row("2025-03-03", 30, 11)])
        self.assertEqual(_brief(ev["SYN.TO"]), [("2025-03-03", "BUY", 20.0)])

    def test_control_same_day_at_another_clock_stays_b22(self):
        ev = _events([_row("2025-03-03", -10, 10, t="10:00:00"),
                      _row("2025-03-03", 30, 11, t="11:00:00")])
        self.assertEqual(_brief(ev["SYN.TO"]), [("2025-03-03", "BUY", 20.0)])

    def test_control_tied_rows_that_close_the_old_round_drop_it(self):
        ev = _events([_row("2025-03-01", 10, 9), _row("2025-03-03", -10, 11),
                      _row("2025-03-03", 30, 10)])
        self.assertEqual(_brief(ev["SYN.TO"]), [("2025-03-03", "BUY", 30.0)])

    def test_control_a_position_opened_by_a_tied_transfer_stays_b22(self):
        ev = _events([_row("2025-03-03", 10, action="TRANSFER"),
                      _row("2025-03-03", -30, 11)])
        self.assertEqual(_brief(ev["SYN.TO"]), [("2025-03-03", "SELL", 20.0)])


def _run_project(td, tt_lines, *, name="rrsp", kind="sheltered"):
    """`tjs run` of one account's .tt rows: the holdings file as read back
    and its text."""
    root = Path(td) / "p"
    root.mkdir()
    (root / "taxjson.toml").write_text(
        settings_for("canada", year=2025, source_currencies=[])
        + f'[accounts.{name}]\ntype = "{kind}"\ntransfers = true\n')
    (root / "inputs" / name).mkdir(parents=True)
    (root / "inputs" / name / "a.tt").write_text("\n".join(tt_lines) + "\n")
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), "run"]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)
    if r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    text = (root / "reports" / f"{name}_holdings.toml").read_text()
    return {h["symbol"]: h for h in tomllib.loads(text)["holding"]}, text


def _tt(act, d, sym, q, p=0):
    return f"{act} {d} 09:30:00 {sym} {q} CAD {p} {p * abs(q)}"


def _listed(h):
    return [(str(e["date"]), e["action"], e["qty"]) for e in h["trades"]]


class TestMoveInKindKeepsTheHistory(unittest.TestCase):
    """TJ-1342."""

    MOVE = [{"date": "2025-07-02", "q": 60, "p": 100},
            {"date": "2025-08-01", "q": -10, "p": 110},
            {"date": "2025-09-02", "q": -50, "p": 120, "a": "TRANSFER"},
            {"date": "2025-09-03", "q": 50, "p": 0, "a": "TRANSFER"},
            {"date": "2025-09-15", "q": 5, "p": 130}]

    def _rows(self):
        return [_row(r["date"], r["q"], r["p"],
                     action=r.get("a", "BUYSELL")) for r in self.MOVE]

    def test_a_start_before_the_move_keeps_the_purchases(self):
        ev = _events(self._rows(),
                     position_starts={"SYN.TO": "2025-07-02"})
        self.assertEqual(_brief(ev["SYN.TO"]),
                         [("2025-07-02", "BUY", 60.0),
                          ("2025-08-01", "SELL", 10.0),
                          ("2025-09-15", "BUY", 5.0)])

    def test_control_no_start_known_ends_the_position_as_before(self):
        ev = _events(self._rows())
        self.assertEqual(_brief(ev["SYN.TO"]), [("2025-09-15", "BUY", 5.0)])

    def test_control_a_later_start_is_a_later_round(self):
        # The engine did not net them: its position starts at the arrival.
        ev = _events(self._rows(),
                     position_starts={"SYN.TO": "2025-09-03"})
        self.assertEqual(_brief(ev["SYN.TO"]), [("2025-09-15", "BUY", 5.0)])

    def test_control_a_rebuy_after_an_out_with_no_arrival_is_a_new_round(self):
        ev = _events([_row("2025-02-03", 50, 100),
                      _row("2025-03-03", -50, 120, action="TRANSFER"),
                      _row("2025-06-02", 10, 90)],
                     position_starts={"SYN.TO": "2025-06-02"})
        self.assertEqual(_brief(ev["SYN.TO"]), [("2025-06-02", "BUY", 10.0)])

    def test_control_a_sale_to_flat_still_ends_the_round(self):
        # The walk's start only speaks for transfers, and only the flat
        # that is one: the closing sale of a round is the boundary.
        ev = _events([_row("2025-02-03", 50, 100), _row("2025-03-03", -50, 120),
                      _row("2025-06-02", 10, 90)],
                     position_starts={"SYN.TO": "2025-02-03"})
        self.assertEqual(_brief(ev["SYN.TO"]), [("2025-06-02", "BUY", 10.0)])

    def test_a_tjs_run_lists_the_position_with_its_start(self):
        with tempfile.TemporaryDirectory() as td:
            held, _ = _run_project(td, [
                _tt("BUYSELL", "2025-07-02", "QZA.TO", 60, 100),
                _tt("BUYSELL", "2025-08-01", "QZA.TO", -10, 110),
                _tt("TRANSFER", "2025-09-02", "QZA.TO", -50, 120),
                _tt("TRANSFER", "2025-09-03", "QZA.TO", 50),
                _tt("BUYSELL", "2025-09-15", "QZA.TO", 5, 130),
                _tt("BUYSELL", "2025-07-02", "QZC.TO", 50, 100),
                _tt("TRANSFER", "2025-09-02", "QZC.TO", -50, 120),
                _tt("TRANSFER", "2025-09-03", "QZC.TO", 50),
                # Not a move: out, and back six months on.
                _tt("BUYSELL", "2025-01-06", "QZE.TO", 20, 100),
                _tt("TRANSFER", "2025-02-03", "QZE.TO", -20, 120),
                _tt("TRANSFER", "2025-09-03", "QZE.TO", 20, 150),
            ])
        self.assertEqual(_listed(held["QZA.TO"]),
                         [("2025-07-02", "BUY", 60.0),
                          ("2025-08-01", "SELL", 10.0),
                          ("2025-09-15", "BUY", 5.0)])
        self.assertEqual(held["QZA.TO"]["quantity"], 55.0)
        self.assertEqual(str(held["QZA.TO"]["position_start_date"]),
                         "2025-07-02")
        self.assertEqual(_listed(held["QZC.TO"]),
                         [("2025-07-02", "BUY", 50.0)])
        self.assertEqual(str(held["QZE.TO"]["position_start_date"]),
                         "2025-09-03")
        self.assertEqual(held["QZE.TO"]["trades"], [])


def _marks(rows, **kw):
    out = {}
    _events(rows, arrivals=out, **kw)
    return out


class TestArrivalsMarked(unittest.TestCase):
    """TJ-1344: a holding's `arrivals`, and nothing about `trades` changes
    for a reader that knows only them."""

    def test_a_contribution_in_kind_is_marked(self):
        rows = [_row("2025-01-06", 100, action="TRANSFER"),
                _row("2025-03-05", -10, 80)]
        self.assertEqual(_marks(rows)["SYN.TO"],
                         [{"date": "2025-01-06", "qty": 100.0,
                           "kind": "transfer"}])
        # The list of trades stays what it was.
        self.assertEqual(_brief(_events(rows)["SYN.TO"]),
                         [("2025-03-05", "SELL", 10.0)])

    def test_a_move_of_your_own_is_marked_as_one(self):
        rows = [_row("2025-01-06", 40, action="TRANSFER", type=LOT_MOVE_TYPE)]
        self.assertEqual(_marks(rows)["SYN.TO"],
                         [{"date": "2025-01-06", "qty": 40.0,
                           "kind": "own_move"}])

    def test_a_netted_move_marks_nothing(self):
        rows = [_row("2025-07-02", 50, 100),
                _row("2025-09-02", -50, action="TRANSFER"),
                _row("2025-09-03", 50, action="TRANSFER")]
        self.assertEqual(
            _marks(rows, position_starts={"SYN.TO": "2025-07-02"}), {})

    def test_a_partial_move_marks_nothing(self):
        rows = [_row("2025-07-02", 50, 100),
                _row("2025-09-02", -20, action="TRANSFER"),
                _row("2025-09-03", 20, action="TRANSFER")]
        self.assertEqual(_marks(rows), {})

    def test_an_out_leg_dated_after_its_arrival_marks_nothing(self):
        rows = [_row("2025-07-02", 50, 100),
                _row("2025-09-02", 50, action="TRANSFER"),
                _row("2025-09-03", -50, action="TRANSFER")]
        self.assertEqual(_marks(rows), {})

    def test_a_later_withdrawal_leaves_what_is_still_here(self):
        rows = [_row("2025-01-06", 100, action="TRANSFER"),
                _row("2025-06-02", -30, action="TRANSFER")]
        self.assertEqual(_marks(rows)["SYN.TO"],
                         [{"date": "2025-01-06", "qty": 70.0,
                           "kind": "transfer"}])

    def test_a_closed_round_leaves_no_mark(self):
        rows = [_row("2025-01-06", 100, action="TRANSFER"),
                _row("2025-03-05", -100, 80),
                _row("2025-06-02", 10, 70)]
        self.assertEqual(_marks(rows), {})

    def test_an_arrival_after_an_unmatched_out_is_marked(self):
        rows = [_row("2025-01-06", 20, 100),
                _row("2025-02-03", -20, action="TRANSFER"),
                _row("2025-09-03", 20, action="TRANSFER")]
        self.assertEqual(_marks(rows, position_starts={"SYN.TO": "2025-09-03"})
                         ["SYN.TO"],
                         [{"date": "2025-09-03", "qty": 20.0,
                           "kind": "transfer"}])

    def test_a_rename_carries_the_mark(self):
        rows = [_row("2025-01-06", 100, action="TRANSFER", sym="OLD.TO"),
                _row("2025-03-05", 1, action="SPLIT", sym="OLD.TO",
                     symbol_new="SYN.TO")]
        self.assertEqual(list(_marks(rows)), ["SYN.TO"])

    def test_control_bought_positions_have_no_mark(self):
        rows = [_row("2025-01-06", 100, 10), _row("2025-03-05", -10, 12)]
        self.assertEqual(_marks(rows), {})

    def test_a_tjs_run_writes_it_on_the_arrived_holding_alone(self):
        with tempfile.TemporaryDirectory() as td:
            held, text = _run_project(td, [
                _tt("TRANSFER", "2025-01-06", "QZD.TO", 100),
                _tt("BUYSELL", "2025-03-05", "QZD.TO", -10, 80),
                _tt("BUYSELL", "2025-07-02", "QZF.TO", 50, 100),
            ])
        qzd, qzf = held["QZD.TO"], held["QZF.TO"]
        self.assertEqual(
            [(str(m["date"]), m["qty"], m["kind"]) for m in qzd["arrivals"]],
            [("2025-01-06", 100.0, "transfer")])
        self.assertEqual(_listed(qzd), [("2025-03-05", "SELL", 10.0)])
        self.assertEqual(
            sum(m["qty"] for m in qzd["arrivals"])
            - sum(e["qty"] for e in qzd["trades"]), qzd["quantity"])
        self.assertNotIn("arrivals", qzf)
        self.assertEqual(text.count("arrivals ="), 1)
        # Read back as any snapshot: the key is ignored.
        inv = export._holdings_toml_to_inventory(tomllib.loads(text))
        self.assertEqual(
            {r["symbol"]: r["qty"] for r in inv["inventory"]},
            {"QZD.TO": 90.0, "QZF.TO": 50.0})

    def test_a_tjs_run_marks_a_move_between_taxable_accounts(self):
        from test_decisions_2026_10_26_ownmove import _cli, _project
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", "loss")
            r = _cli(root, "run")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = tomllib.loads(
                (root / "reports" / "margin2_holdings.toml").read_text())
        h = next(h for h in doc["holding"] if h["symbol"] == "ZZB.TO")
        self.assertEqual(
            [(str(m["date"]), m["qty"], m["kind"]) for m in h["arrivals"]],
            [("2024-05-01", 50.0, "own_move")])
        self.assertEqual(h["trades"], [])


if __name__ == "__main__":
    unittest.main()
