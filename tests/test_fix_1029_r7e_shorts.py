"""Regression tests for fix round 7 (wave E), group shorts: a marked short
beside an own move's shares, a written option's premium in the holdings views
under close timing, the run's no-total note and holdings report after a split
one account reports, and edge-cases' HELD DAY 30 for a short holder.

Synthetic data only; account labels and tickers are made up.
"""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date

from taxjson.lib.core import CanadaTaxRules, LOT_MOVE_TYPE, TaxTransaction
from tax_rules import rule
from _qa_project import console, flat, gains, project, tj
from taxjson.bin.taxjson_split_gains import _split_inventory
from test_fix_1029_r7d_edgeplan import _edge, _t as _edge_row
from _style import CapturedWidth


_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _run(txs, **kw):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = CanadaTaxRules().compute_gains(txs, **kw)
    return res


def _row(account, date, qty, net, open_close='', sym='SYQ.TO', settle=None):
    return TaxTransaction(action='BUYSELL', date=date,
                          date_settle=settle or date,
                          time='10:00:00', symbol=sym, quantity=qty,
                          price=abs(net / qty), net_amount=net,
                          currency='CAD', account=account,
                          open_close=open_close)


def _move(date, units, src, dst, sym='SYQ.TO', time=''):
    desc = f'own-account move #1: {src} -> {dst} (test)'
    return [TaxTransaction(action='TRANSFER', type=LOT_MOVE_TYPE, date=date,
                           date_settle=date, time=time, symbol=sym,
                           quantity=sign * units, currency='CAD',
                           account=acct, description=desc)
            for sign, acct in ((-1, src), (1, dst))]


def _held(res):
    return {(i.get('account', ''), i['symbol']):
            (round(i['qty'], 6), round(i['total_cost'], 2))
            for i in res['inventory'] if abs(i['qty']) > 1e-9}


def _gains(res):
    return [(r['date'], round(r['gain'], 2)) for r in res['transactions']]


class TestOwnMoveCountsAsTheAccountsLong(unittest.TestCase):
    """TJ-1180: a sale coded C;O closes the account's own long, moved-in
    shares included, and opens a short only past it; moved-out shares are
    no longer the sender's long."""

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_moved_in_units_are_closed_first(self):
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', -15, 225, 'C;O'),
                      _row('b', '2025-06-02', 5, -60, 'C')])
        self.assertEqual(_gains(res), [('2025-03-03', 50.0),
                                       ('2025-06-02', 15.0)])
        self.assertEqual(_held(res), {})

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_same_as_the_control_with_no_move(self):
        control = _run([_row('b', '2025-01-02', 10, -100),
                        _row('b', '2025-03-03', -15, 225, 'C;O'),
                        _row('b', '2025-06-02', 5, -60, 'C')])
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', -15, 225, 'C;O'),
                      _row('b', '2025-06-02', 5, -60, 'C')])
        self.assertEqual(res['summary']['total_gain'],
                         control['summary']['total_gain'])

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_part_held_part_moved_in(self):
        res = _run([_row('a', '2025-01-02', 10, -100),
                    _row('b', '2025-01-02', 5, -50)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', -20, 300, 'C;O')])
        self.assertEqual(_gains(res), [('2025-03-03', 75.0)])
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (-5.0, -75.0)})

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_moved_out_units_are_no_long_of_the_sender(self):
        res = _run([_row('a', '2025-01-02', 15, -150)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('a', '2025-03-03', -10, 150, 'C;O')])
        self.assertEqual(_gains(res), [('2025-03-03', 25.0)])
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 100.0),
                                      ('a', 'SYQ.TO'): (-5.0, -75.0)})

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_a_sale_the_day_the_units_arrive(self):
        """Whatever clock the leg has, and a sale settling that day."""
        for time in ('', '23:00:00'):
            res = _run([_row('a', '2025-01-02', 10, -100)]
                       + _move('2025-02-03', 10, 'a', 'b', time=time)
                       + [_row('b', '2025-01-30', -10, 150, 'C;O',
                               settle='2025-02-03')])
            self.assertEqual(_gains(res), [('2025-01-30', 50.0)], time)
            self.assertEqual(_held(res), {}, time)

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_a_split_after_the_move_scales_the_moved_units(self):
        split = TaxTransaction(action='SPLIT', date='2025-02-10',
                               date_settle='2025-02-10', time='00:00:01',
                               symbol='SYQ.TO', quantity=2.0,
                               currency='CAD', account='a')
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b') + [split]
                   + [_row('b', '2025-03-03', -20, 300, 'C;O')])
        self.assertEqual(_gains(res), [('2025-03-03', 200.0)])
        self.assertEqual(_held(res), {})

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_a_rename_after_the_move_keeps_the_moved_units(self):
        ren = TaxTransaction(action='SPLIT', date='2025-02-10',
                             date_settle='2025-02-10', time='00:00:01',
                             symbol='SYQ.TO', symbol_new='SYR.TO',
                             quantity=1.0, currency='CAD', account='a')
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b') + [ren]
                   + [_row('b', '2025-03-03', -10, 150, 'C;O', sym='SYR.TO')])
        self.assertEqual(_gains(res), [('2025-03-03', 50.0)])
        self.assertEqual(_held(res), {})

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_a_move_out_of_units_the_books_never_had(self):
        """The sender's later buy is its own long: never a short because
        of an earlier move the router could not back."""
        res = _run([_row('c', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('a', '2025-03-03', 10, -120),
                      _row('a', '2025-04-03', -10, 150, 'C;O')])
        self.assertEqual(len(_gains(res)), 1)
        self.assertEqual(sum(q for q, _ in _held(res).values()), 10.0)
        self.assertNotIn(('a', 'SYQ.TO'), _held(res))

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_a_coded_open_past_a_move_stays_short(self):
        """Coded O alone: a short, whatever long the account received."""
        res = _run([_row('a', '2025-01-02', 10, -100)]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', -5, 75, 'O')])
        self.assertEqual(_gains(res), [])
        self.assertEqual(_held(res), {('', 'SYQ.TO'): (10.0, 100.0),
                                      ('b', 'SYQ.TO'): (-5.0, -75.0)})

    @rule("CA-DISP-04", "CA-ACB-OWN-MOVE")
    def test_an_unmarked_buy_goes_long_beside_moved_in_units(self):
        """b short 5 then 10 moved in: its long is held, so an unmarked buy
        is a long, not a cover (the router's rule for any long)."""
        res = _run([_row('a', '2025-01-02', 10, -100),
                    _row('b', '2025-01-10', -5, 75, 'O')]
                   + _move('2025-02-03', 10, 'a', 'b')
                   + [_row('b', '2025-03-03', 5, -60)])
        self.assertEqual(_gains(res), [])
        self.assertEqual(_held(res)[('b', 'SYQ.TO')], (-5.0, -75.0))

    @rule("CA-ACB-OWN-MOVE")
    def test_a_book_with_no_marked_short_is_untouched(self):
        plain = _run([_row('a', '2025-01-02', 10, -100),
                      _row('b', '2025-03-03', -10, 150)])
        moved = _run([_row('a', '2025-01-02', 10, -100)]
                     + _move('2025-02-03', 10, 'a', 'b')
                     + [_row('b', '2025-03-03', -10, 150)])
        self.assertEqual(_gains(plain), _gains(moved))
        self.assertEqual(_held(plain), _held(moved))


class TestHeldDayThirtyOfAShortHolder(unittest.TestCase):
    """TJ-1260: a holder that is short on day 30 holds no units. Keeps
    TJ-1113 (per holder)."""
    S = "ZZS.TO"

    def _books(self, *extra):
        return {"margin": [
            _edge_row("margin", "BUYSELL", "2024-02-10", self.S, 100, -1000.0),
            _edge_row("margin", "BUYSELL", "2024-03-10", self.S, -120, 960.0),
            *extra]}

    @rule("CA-RPT-07", "CA-SL-08")
    def test_a_short_holder_shows_none(self):
        _doc, edge, text = _edge(self._books(), year=2024)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 0.0)
        self.assertRegex(text, r"2024-03-10\s+100\s+0\n")

    @rule("CA-RPT-07", "CA-SL-08")
    def test_a_short_never_hides_an_in_window_holders_units(self):
        tfsa = [_edge_row("tfsa", "BUYSELL", "2024-03-20", self.S, 20, -180.0)]
        _doc, edge, _text = _edge(self._books(), {"tfsa": tfsa}, year=2024)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 20.0)

    @rule("CA-RPT-07", "CA-SL-08")
    def test_an_old_holder_stays_apart(self):
        tfsa = [_edge_row("tfsa", "BUYSELL", "2024-01-10", self.S, 50, -450.0)]
        _doc, edge, _text = _edge(self._books(), {"tfsa": tfsa}, year=2024)
        (w,) = edge["window_edges"]
        self.assertEqual(w["held_at_day30"], 0.0)

    def _year_end(self, sheltered=None):
        books = {"margin": [
            _edge_row("margin", "BUYSELL", "2024-12-02", self.S, 100, -1000.0),
            _edge_row("margin", "BUYSELL", "2024-12-20", self.S, -120, 960.0),
            _edge_row("margin", "BUYSELL", "2025-01-06", self.S, 5, -40.0)]}
        _doc, edge, _text = _edge(books, sheltered, year=2024)
        (r,) = edge["year_boundary"]["loss_windows"]
        return r

    @rule("CA-RPT-07", "CA-SL-02")
    def test_allowed_reason_of_a_short_holder(self):
        r = self._year_end()
        self.assertEqual(r["allowed_by"], "not_held_day30")
        self.assertIn("no units were held at the end of day 30", r["why"])
        self.assertNotIn("-15", r["why"])
        self.assertNotIn("another holder", r["why"])

    @rule("CA-RPT-07", "CA-SL-02")
    def test_another_holders_units_are_named_in_full(self):
        tfsa = [_edge_row("tfsa", "BUYSELL", "2024-01-10", self.S, 50, -450.0)]
        r = self._year_end({"tfsa": tfsa})
        self.assertEqual(r["allowed_by"], "not_held_day30")
        self.assertIn("50 held by another holder", r["why"])


_MARGIN = ("BUYSELL 2024-01-10 10:00:00 ZZS.TO 100 CAD 10.00 1000.00\n"
           "SPLIT 2024-03-01 09:30:00 ZZS.TO ZZS.TO 2\n")
_QT_ROWS = ("BUYSELL 2024-02-10 10:00:00 ZZS.TO 100 CAD 8.00 800.00\n"
            "BUYSELL 2024-04-10 10:00:00 ZZS.TO -150 CAD 12.00 1800.00\n"
            "BUYSELL 2024-06-10 10:00:00 ZZS.TO 80 CAD 11.00 880.00\n")
_QT_ACCT = '[accounts.qt]\ntype = "taxable"\n'


class TestRunNoteAfterAnotherAccountsSplit(unittest.TestCase):
    """TJ-1258: the run's short note reads the split another taxable
    account reports, as the engine and find-missing-history do."""

    def _note(self, margin=_MARGIN, qt=_QT_ROWS, extra=_QT_ACCT, more=None):
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/f.tt": margin, "qt/f.tt": qt,
                                     **(more or {})}, extra_accounts=extra)
            return flat(console(tj(root, "run", "--no-input")))

    def test_no_note_when_the_split_covers_the_sale(self):
        text = self._note()
        self.assertNotIn("go short in qt's data", text)
        self.assertNotIn("in no total", text)

    def test_a_real_short_past_the_split_keeps_its_note(self):
        qt = ("BUYSELL 2024-02-10 10:00:00 ZZS.TO 10 CAD 8.00 80.00\n"
              "BUYSELL 2024-04-10 10:00:00 ZZS.TO -50 CAD 12.00 600.00\n"
              "BUYSELL 2024-06-10 10:00:00 ZZS.TO 80 CAD 11.00 880.00\n")
        self.assertIn("go short in qt's data", self._note(qt=qt))

    def test_a_split_of_a_registered_account_is_not_spread(self):
        """TJ-1022: the books apply it to that book only."""
        tfsa = _QT_ACCT + '[accounts.tfsa]\ntype = "sheltered"\n'
        text = self._note(
            margin="BUYSELL 2024-01-10 10:00:00 ZZS.TO 100 CAD 10.00 1000.00\n",
            extra=tfsa,
            more={"tfsa/f.tt": _MARGIN})
        self.assertIn("go short in qt's data", text)


_OPT = "SYQ261218P00010000.TO"
_CLOSE_TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
               'country = "canada"\nbase_currency = "CAD"\n'
               'source_currencies = ["USD"]\n'
               'option_premium_timing = "close"\n'
               '[accounts.a]\ntype = "taxable"\n'
               '[accounts.b]\ntype = "taxable"\n')
_WRITE_A = f"BUYSELL 2025-01-02 10:00:00 {_OPT} -1 CAD 1 100\n"
_WRITE_B = f"BUYSELL 2025-01-03 10:00:00 {_OPT} -2 CAD 3 600\n"


def _close_project(td, name, a=_WRITE_A, b=_WRITE_B, toml=_CLOSE_TOML):
    root = project(td, name, {"a/t.tt": a, "b/t.tt": b})
    (root / "taxjson.toml").write_text(toml)
    tj(root, "run", "--no-input")
    return root


def _option_rows(root, account):
    doc = json.loads((root / "work" / f"{account}_gains_wash.json").read_text())
    return {i["symbol"]: (round(i["qty"], 6), round(i["total_cost"], 2))
            for i in doc["inventory"]}


class TestCloseTimingViewsKeepEachAccountsPremium(unittest.TestCase):
    """TJ-1251: an account's open written contracts show the premium a
    close takes (CA-OPT-06), in list and harvest, as TJ-1105's close does."""

    @rule("CA-OPT-06")
    def test_each_account_shows_its_own_premium(self):
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(td, "p")
            self.assertEqual(_option_rows(root, "a"), {_OPT: (-1.0, -100.0)})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-2.0, -600.0)})
            text = tj(root, "list").stdout
            self.assertRegex(text, r"a\s+\S+\s+-1\s+-100\.00")
            self.assertRegex(text, r"b\s+\S+\s+-2\s+-600\.00")

    @rule("CA-OPT-06")
    def test_a_close_realizes_what_the_view_showed_and_b_stays(self):
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(
                td, "p", a=_WRITE_A
                + f"BUYSELL 2025-06-02 10:00:00 {_OPT} 1 CAD 0.5 50\n")
            self.assertEqual(_option_rows(root, "a"), {})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-2.0, -600.0)})
            self.assertEqual(sum(round(g["gain"], 2)
                                 for g in gains(root, "a")), 50.0)

    @rule("CA-OPT-06")
    def test_a_partial_close_keeps_the_accounts_average(self):
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(
                td, "p",
                a=f"BUYSELL 2025-01-02 10:00:00 {_OPT} -2 CAD 1.5 300\n"
                  f"BUYSELL 2025-06-02 10:00:00 {_OPT} 1 CAD 0.5 50\n")
            self.assertEqual(_option_rows(root, "a"), {_OPT: (-1.0, -150.0)})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-2.0, -600.0)})

    @rule("CA-OPT-06")
    def test_the_harvest_figure_is_what_a_buy_back_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(td, "p")
            (root / "work" / ".price_cache.json").write_text(json.dumps(
                {_OPT: {"price": 0.5, "source": "yfinance", "currency": "CAD",
                        "asof": date.today().isoformat(),
                        "quote_symbol": _OPT}}))
            rows = json.loads(tj(root, "harvest", "--options", "--json",
                                 check=False).stdout)["rows"]
            got = {r["account"]: round(r["unrealized"], 2) for r in rows}
            self.assertEqual(got, {"a": 50.0, "b": 500.0})

    @rule("CA-OPT-06")
    def test_grant_timing_is_as_before(self):
        toml = _CLOSE_TOML.replace('"close"', '"grant"')
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(td, "p", toml=toml)
            a_rows, b_rows = _option_rows(root, "a"), _option_rows(root, "b")
            self.assertEqual(a_rows[_OPT][0], -1.0)
            self.assertEqual(round(a_rows[_OPT][1] + b_rows[_OPT][1], 2), -700.0)

    @rule("CA-OPT-06")
    def test_units_that_are_not_the_accounts_lots_go_by_share(self):
        rows = [{"symbol": _OPT, "qty": -3.0, "total_cost": -700.0,
                 "account_premiums": {
                     "a": {"units": 2.0, "premium": 100.0},
                     "b": {"units": 1.0, "premium": 600.0}}}]
        book = [{"action": "BUYSELL", "symbol": _OPT, "quantity": -1.0,
                 "date": "2025-01-02", "date_settle": "2025-01-02",
                 "account": "a"}]
        (row,) = _split_inventory(rows, "a", book, {}, "9999-12-31")
        self.assertEqual(row["qty"], -1.0)
        self.assertEqual(row["total_cost"], round(-700.0 / 3, 6))
        self.assertNotIn("account_premiums", row)

    @rule("CA-OPT-06")
    def test_a_parked_deferral_goes_by_units_on_top(self):
        rows = [{"symbol": _OPT, "qty": -3.0, "total_cost": -730.0,
                 "account_premiums": {
                     "a": {"units": 1.0, "premium": 100.0},
                     "b": {"units": 2.0, "premium": 600.0}}}]
        book = [{"action": "BUYSELL", "symbol": _OPT, "quantity": -1.0,
                 "date": "2025-01-02", "date_settle": "2025-01-02",
                 "account": "a"}]
        (row,) = _split_inventory(rows, "a", book, {}, "9999-12-31")
        self.assertEqual(row["total_cost"], -110.0)

    @rule("CA-OPT-06")
    def test_a_table_of_the_wrong_shape_goes_by_share(self):
        book = [{"action": "BUYSELL", "symbol": _OPT, "quantity": -1.0,
                 "date": "2025-01-02", "date_settle": "2025-01-02",
                 "account": "a"}]
        for bad in ("x", [], {"a": 1}, {"a": {"units": "?", "premium": 1}},
                    {"a": {"units": 1.0}}, None):
            rows = [{"symbol": _OPT, "qty": -3.0, "total_cost": -600.0,
                     "account_premiums": bad}]
            (row,) = _split_inventory(rows, "a", book, {}, "9999-12-31")
            self.assertEqual(row["total_cost"], -200.0, bad)

    @rule("CA-OPT-06")
    def test_a_split_scales_both_accounts_lots(self):
        split = f"SPLIT 2025-03-03 09:30:00 {_OPT} {_OPT} 2\n"
        with tempfile.TemporaryDirectory() as td:
            root = _close_project(td, "p", a=_WRITE_A + split, b=_WRITE_B)
            self.assertEqual(_option_rows(root, "a"), {_OPT: (-2.0, -100.0)})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-4.0, -600.0)})

    @rule("CA-OPT-06")
    def test_a_registered_accounts_write_is_apart(self):
        toml = _CLOSE_TOML + '[accounts.tfsa]\ntype = "sheltered"\n'
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {
                "a/t.tt": _WRITE_A, "b/t.tt": _WRITE_B,
                "tfsa/t.tt": f"BUYSELL 2025-01-03 10:00:00 {_OPT} -1 CAD 9 900\n"})
            (root / "taxjson.toml").write_text(toml)
            tj(root, "run", "--no-input")
            self.assertEqual(_option_rows(root, "a"), {_OPT: (-1.0, -100.0)})
            self.assertEqual(_option_rows(root, "b"), {_OPT: (-2.0, -600.0)})

    @rule("CA-OPT-06")
    def test_one_account_has_no_premium_table(self):
        res = _run([_row("a", "2025-01-02", -1, 100, sym=_OPT),
                    _row("a", "2025-01-03", -2, 600, sym=_OPT)])
        self.assertNotIn("account_premiums", res["inventory"][0])

    @rule("CA-OPT-06")
    def test_two_accounts_have_it_and_it_sums_to_the_pool(self):
        res = _run([_row("a", "2025-01-02", -1, 100, sym=_OPT),
                    _row("b", "2025-01-03", -2, 600, sym=_OPT)])
        row = res["inventory"][0]
        self.assertEqual(row["account_premiums"],
                         {"a": {"units": 1.0, "premium": 100.0},
                          "b": {"units": 2.0, "premium": 600.0}})
        self.assertEqual(row["total_cost"], -700.0)


if __name__ == '__main__':
    unittest.main()
