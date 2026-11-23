"""The short timeline's work per walked row scales with the shorts open at
that row, not with every option series ever written (backlog TJ-4977,
TJ-4978).

Since CA-OPT-12 an option series' written contracts are in the marked-short
router's table, and a covered position stays there at 0 units. v1.0.1 swept
the short timeline over every key of the table after every row, and each
label's state scanned the whole table for the symbol's shares: K x K per row
for K series ever written (a book with many written series ran 3.6 times
slower than v0.41.0). Now a label with no units of its own has no state
before any scan, the sweep names the open shorts only, and the router's
table gives a symbol's accounts without a scan.

No output may change: the tests here count the work, and compare the whole
result with the one the old sweep and the old scan give. All data is
synthetic (ZZ tickers, made-up prices).
"""
import contextlib
import copy
import io
import random
import unittest
from datetime import date, timedelta
from unittest import mock

from taxjson.engine.ca import pools as P
from taxjson.engine.ca.router import _MarkedShortRouter
from taxjson.lib import cost_timeline as CT
from taxjson.lib.pipeline import GainsRequest, run_gains
from taxjson.model.transaction import TaxTransaction

SERIES = 240          # written and closed, one account
LATER = 1500          # share rows after the last of them is closed


def tx(d, s, q, n, acct="a", **kw):
    return TaxTransaction(action=kw.pop("action", "BUYSELL"), date=d, date_settle=d,
                          time=kw.pop("time", "10:00:00"), symbol=s, quantity=q,
                          net_amount=n, currency="CAD", account=acct, **kw)


def _bdays(lo, n):
    out, d = [], lo
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _series(i):
    """A made-up contract of its own: the i-th strike of ZZA's 2026 calls."""
    return f"ZZA260116C{(10 + i) * 1000:08d}.US"


def book(series=SERIES, later=LATER):
    """`series` option series written and bought back in account a, one
    after the other; one series written in accounts a and b and left open;
    then `later` share rows."""
    days = _bdays(date(2024, 1, 2), series + 2 + later // 4 + 2)
    rows = [tx(days[0], "ZZA260116P00005000.US", -3.0, 150.0, id="open-a"),
            tx(days[0], "ZZA260116P00005000.US", -1.0, 50.0, acct="b", id="open-b",
               time="10:01:00")]
    for i in range(series):
        rows.append(tx(days[i + 1], _series(i), -1.0, 40.0 + i % 7, id=f"w{i}"))
        rows.append(tx(days[i + 2], _series(i), 1.0, -(25.0 + i % 11), id=f"c{i}",
                       time="09:45:00"))
    for j in range(later // 2):
        d = days[series + 2 + j // 2]
        rows.append(tx(d, "ZZB.US", 10.0, -(200.0 + j % 5), id=f"b{j}",
                       time=f"11:{j % 2:02d}:00"))
        rows.append(tx(d, "ZZB.US", -10.0, 201.0 + j % 3, id=f"s{j}",
                       time=f"12:{j % 2:02d}:00"))
    return rows


def engine(rows):
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(list(rows), [], [], GainsRequest(
            country="canada", taxable=True, year=2024, cost_timeline=True,
            phantom_hint=False))


class _Counted:
    """The run's short-timeline work: the labels each sweep of the shorts
    recorder was given, and the calls of `_short_shares`."""

    def __init__(self):
        self.labels = []          # one count per sweep that names labels
        self.shares = 0

    def run(self, rows):
        real_sweep, real_shares = CT.Recorder.sweep, P._short_shares

        def sweep(rec, seq, labels=(), **kw):
            labels = list(labels)
            if labels:
                self.labels.append(len(labels))
            return real_sweep(rec, seq, labels, **kw)

        def shares(sym, short_units):
            self.shares += 1
            return real_shares(sym, short_units)

        with mock.patch.object(CT.Recorder, "sweep", sweep), \
                mock.patch.object(P, "_short_shares", shares):
            return engine(rows)


class TestWorkScalesWithOpenShorts(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.counted = _Counted()
        cls.result = cls.counted.run(book())

    def test_a_sweep_names_the_open_shorts_only(self):
        """At most three shorts are ever open at once here (the series left
        open in two accounts, and the one series written and not yet bought
        back): no sweep names more, whatever the table holds. v1.0.1 named
        every key: up to SERIES + 2."""
        c = self.counted
        self.assertTrue(c.labels, "the book has shorts: the timeline is swept")
        self.assertLessEqual(max(c.labels), 3)

    def test_the_shares_are_read_for_open_shorts_only(self):
        """`_short_shares` runs for a label with units of its own, and once
        a sweep: a short the recorder holds is checked as a held label and
        not again as a named one (one opened by the row is checked as a
        named one, and the final pools' rows read the shares a few times).
        So at most once for each of the three shorts open at once. In
        v1.0.1 it ran for every key of the table at every sweep, and twice
        for an open short."""
        c = self.counted
        self.assertGreater(len(c.labels), LATER, "one sweep per walked row at least")
        self.assertLessEqual(c.shares, 3 * len(c.labels) + 2 * (SERIES + 2))

    def test_the_short_timeline_still_has_every_series(self):
        """Each series' written row and its flat row are in the timeline's
        shorts part; the open one is each account's."""
        shorts = self.result["cost_timeline"]["shorts"]
        syms = [r[3] for r in shorts["rows"]]
        for i in (0, SERIES // 2, SERIES - 1):
            self.assertEqual(syms.count(_series(i)), 2, _series(i))
        self.assertEqual(sorted({r[2] for r in shorts["rows"]
                                 if r[3] == "ZZA260116P00005000.US"}), ["a", "b"])


class _EveryLabel(dict):
    """`Recorder.loose` as a set that holds every label: the sweep then
    names every key of the router's table, as v1.0.1 did."""

    def __contains__(self, _lab):
        return True

    def __bool__(self):
        return True


def _sweep_of_every_label(rec, seq, labels=(), **_kw):
    """v1.0.1's `Recorder.sweep`: every held label, then every named one."""
    for lab in list(rec._held) + list(labels):
        rec._check(seq, lab, "")


def _scanned_shares(sym, short_units):
    """v1.0.1's `_short_shares`: the symbol's keys out of the whole table."""
    who = {a: u for (y, a), u in dict(short_units).items()
           if y == sym and u > P.pool_qty_eps(sym, u)}
    total = sum(who.values())
    return {a: u / total for a, u in who.items()}


class TestTheResultIsTheOldOnes(unittest.TestCase):
    """The whole result, against the old sweep (every key of the table) and
    the old scan for the shares, on a smaller book of the same shape with a
    series in three accounts, a marked share short in two accounts that a
    split and a rename carry and a purchase covers, and a short futures
    position opened and closed."""

    def _rows(self):
        rows = book(series=40, later=120)
        days = _bdays(date(2024, 1, 2), 60)
        s = "ZZC260116C00020000.US"
        rows += [tx(days[3], s, -1.0, 30.0, acct="a", id="t1"),
                 tx(days[4], s, -3.0, 95.0, acct="b", id="t2"),
                 tx(days[5], s, -7.0, 230.0, acct="c", id="t3"),
                 tx(days[20], s, 3.0, -60.0, acct="b", id="t4"),
                 tx(days[30], s, -2.0, 70.0, acct="b", id="t5"),
                 tx(days[50], s, 1.0, -10.0, acct="a", id="t6")]
        # A marked share short (the broker's O and C codes), split 2 for 1,
        # renamed, and covered in one of its two accounts.
        rows += [tx(days[6], "ZZD.US", -30.0, 600.0, acct="a", id="m1", open_close="O"),
                 tx(days[7], "ZZD.US", -10.0, 210.0, acct="c", id="m2", open_close="O"),
                 tx(days[15], "ZZD.US", 2.0, 0.0, acct="a", id="m3", action="SPLIT",
                    time="00:00:00"),
                 tx(days[25], "ZZD.US", 1.0, 0.0, acct="a", id="m4", action="SPLIT",
                    time="00:00:00", symbol_new="ZZE.US"),
                 tx(days[40], "ZZE.US", 60.0, -500.0, acct="a", id="m5", open_close="C")]
        # A short futures position: its settlement parts (legs SO and SC).
        rows += [tx(days[8], "F:ZZFZ6.US", -1.0, 0.0, acct="b", id="f1",
                    type="futures_settlement", settle_native="SO 100 0 CAD"),
                 tx(days[35], "F:ZZFZ6.US", 1.0, 40.0, acct="b", id="f2",
                    type="futures_settlement", settle_native="SC 96 0 CAD")]
        return rows

    def test_same_result_as_the_sweep_of_every_key_and_the_scan(self):
        rows = self._rows()
        new = engine(copy.deepcopy(rows))
        with mock.patch.object(CT.Recorder, "loose", lambda self: _EveryLabel()), \
                mock.patch.object(CT.Recorder, "sweep", _sweep_of_every_label), \
                mock.patch.object(P, "_short_shares", _scanned_shares):
            old = engine(copy.deepcopy(rows))
        shorts = {r[3] for r in new["cost_timeline"]["shorts"]["rows"]}
        self.assertLessEqual({"ZZC260116C00020000.US", "ZZD.US", "ZZE.US", "F:ZZFZ6.US"},
                             shorts)
        self.assertEqual(repr(new), repr(old))


class TestTheEngineSweepsALooseShort(unittest.TestCase):
    """The engine's own sweep names a key at 0 units that the recorder's
    last state is not flat for (`_ca_tl_after`'s `loose` clause). A marked
    short of half a millionth of a share is under the pool's zero; a 3 for
    1 split takes the router's units over it while the pool still reads
    flat, so the label's state has no units and no cost but a currency and
    a date: not held. When a purchase covers it, its units are 0 and only
    the `loose` clause names it: its flat row is v1.0.1's last row."""

    def _rows(self):
        rows = [tx("2024-01-10", "ZZA.US", -5e-7, 0.0, id="sh", open_close="O"),
                tx("2024-02-01", "ZZA.US", 3.0, 0.0, id="sp", action="SPLIT",
                   time="00:00:00")]
        for j in range(12):
            rows.append(tx(f"2024-03-{j + 1:02d}", "ZZB.US", 10.0, -200.0, id=f"b{j}"))
            rows.append(tx(f"2024-03-{j + 1:02d}", "ZZB.US", -10.0, 201.0, id=f"s{j}",
                           time="11:00:00"))
        rows += [tx("2024-06-01", "ZZA.US", 1.5e-6, 0.0, id="cv", open_close="C"),
                 tx("2024-07-02", "ZZB.US", 5.0, -100.0, id="z")]
        return rows

    def test_the_covered_shorts_flat_row_is_recorded_as_by_the_full_sweep(self):
        new = engine(self._rows())
        with mock.patch.object(CT.Recorder, "loose", lambda self: _EveryLabel()), \
                mock.patch.object(CT.Recorder, "sweep", _sweep_of_every_label), \
                mock.patch.object(P, "_short_shares", _scanned_shares):
            old = engine(self._rows())
        rows = old["cost_timeline"]["shorts"]["rows"]
        # (seq, date, account, symbol, qty, cost, deferred, unknown cost, currency, ...)
        self.assertEqual([r[1:9] for r in rows],
                         [["2024-02-01", "a", "ZZA.US", 0.0, 0.0, 0.0, False, "CAD"],
                          ["2024-06-01", "a", "ZZA.US", 0.0, 0.0, 0.0, False, ""]])
        self.assertEqual(new["cost_timeline"]["shorts"]["rows"], rows)
        self.assertEqual(repr(new), repr(old))


class TestALooseLabelIsStillSwept(unittest.TestCase):
    """A label whose last state is not the flat one, but holds no units and
    no cost (so the recorder does not hold it), is in `loose`: the engine's
    sweep names it at 0 units too, and its flat row is recorded."""

    def test_loose_until_flat_and_after_a_restore(self):
        state = {}
        rec = CT.Recorder(state.get)
        lab = ("a", "ZZA.US")
        state[lab] = (0.0, 0.0, 0.0, True, "CAD", None, None)
        rec.sweep(0, [lab])
        self.assertEqual(list(rec.loose()), [lab])
        self.assertNotIn(lab, rec._held)
        kept = rec.fold_state()
        del state[lab]
        rec.sweep(1, [lab])
        self.assertEqual(list(rec.loose()), [])
        self.assertEqual([r[0] for r in rec._rows], [0, 1])
        rec.restore(kept)
        self.assertEqual(list(rec.loose()), [lab])

    def test_a_checkpoint_of_another_shape_is_refused(self):
        """`restore` takes `fold_state`'s six parts: a tuple without the
        last two (`_loose`, `_unequal`) raises, and restores nothing in
        part."""
        rec = CT.Recorder({}.get)
        kept = rec.fold_state()
        self.assertEqual(len(kept), 6)
        with self.assertRaises(ValueError):
            rec.restore(kept[:4])
        rec.restore(kept)

    def test_a_held_label_is_not_loose(self):
        state = {("a", "ZZA.US"): (-1.0, 5.0, 0.0, False, "CAD", None, None)}
        rec = CT.Recorder(state.get)
        rec.sweep(0, list(state))
        self.assertEqual((list(rec._held), list(rec.loose())), ([("a", "ZZA.US")], []))


class TestASteadySweepAsksOnce(unittest.TestCase):
    """`sweep(..., steady=True)` asks a held label's state once, and
    records what the sweep of every label records: also for a state that
    holds a NaN, whose second check records it again."""

    def _both(self, states):
        """The rows two recorders keep over `states` (one {label: state}
        a sweep; a state is built again at each call, as an engine's is),
        every label named at each sweep: (steady, v1.0.1's), and the steady
        one's state_of calls."""
        now, asked = {}, []

        def fresh(lab):
            st = now.get(lab)
            return None if st is None else tuple(float(repr(x)) if isinstance(x, float)
                                                 and not isinstance(x, bool) else x for x in st)

        def state_of(lab):
            asked.append(lab)
            return fresh(lab)
        new, old = CT.Recorder(state_of), CT.Recorder(fresh)
        labels = sorted({lab for st in states for lab in st})
        for seq, st in enumerate(states):
            now.clear()
            now.update(st)
            new.sweep(seq, labels, steady=True)
            _sweep_of_every_label(old, seq, labels)
        return new._rows, old._rows, asked

    def test_a_held_label_is_asked_once_a_sweep(self):
        a, b = ("a", "ZZA.US"), ("b", "ZZA.US")
        st = (-1.0, 5.0, 0.0, False, "CAD", None, None)
        new, old, asked = self._both([{a: st}, {a: st, b: st}, {b: st}, {}])
        self.assertEqual(new, old)
        self.assertEqual([r[:2] for r in new],
                         [(0, a), (1, b), (2, a), (3, b)])
        # sweep 0: a and b named; 1: a held, b named; 2: both held; 3: b held, a named
        self.assertEqual(asked, [a, b, a, b, a, b, b, a])

    def test_a_state_unequal_to_itself_is_checked_again(self):
        a = ("a", "ZZA.US")
        states = [{a: (-1.0, 5.0, 0.0, False, "CAD", None, None)},
                  {a: (-2.0, float("nan"), 0.0, False, "CAD", None, None)},
                  {a: (-2.0, float("nan"), 0.0, False, "CAD", None, None)},
                  {a: (-1.0, 5.0, 0.0, False, "CAD", None, None)}, {}]
        new, old, _asked = self._both(states)
        self.assertEqual(repr(new), repr(old))
        self.assertEqual([r[0] for r in new], [0, 1, 1, 2, 2, 3, 4])


class TestTheTablesIndexKeepsItsOrder(unittest.TestCase):
    """`_ShortUnits.of(sym)` is the scan of the table for `sym`, in the
    table's order, after any of the router's writes (so the shares add the
    same floats in the same order)."""

    def _scan(self, table, sym):
        return [(a, u) for (y, a), u in table.items() if y == sym]

    def test_of_is_the_scan_after_random_writes(self):
        rng = random.Random(4978)
        r = _MarkedShortRouter()
        syms, accts = ["ZZA.US", "ZZB.US", "ZZC.US", "ZZD.US"], ["a", "b", "c", "d"]
        kept = None
        for step in range(3000):
            op = rng.random()
            s, a = rng.choice(syms), rng.choice(accts)
            if op < 0.55:
                r.short[(s, a)] = r.short.get((s, a), 0.0) + rng.choice([-1.0, 0.25, 2.0])
            elif op < 0.70:
                r.move_in("written", s, a, rng.random())
            elif op < 0.80:
                r.move_out("written", s, a, rng.random())
            elif op < 0.90:
                r.split(s, rng.choice(syms), rng.choice([0.5, 2.0, 1.0]))
            elif op < 0.95:
                kept = copy.deepcopy(r.fold_state())
            elif kept is not None:
                r.restore(copy.deepcopy(kept))
            for y in syms:
                self.assertEqual(list(r.short.of(y).items()), self._scan(r.short, y),
                                 (step, y))
                self.assertEqual(P._short_shares(y, r.short),
                                 _scanned_shares(y, r.short), (step, y))
        self.assertIsInstance(r.short, P._ShortUnits)
        self.assertIsInstance(copy.deepcopy(r.fold_state())[0], P._ShortUnits)

    def test_a_write_that_would_pass_the_index_by_is_refused(self):
        t = P._ShortUnits({("ZZA.US", "a"): 1.0})
        for write in (lambda: t.pop(("ZZA.US", "a")), t.popitem,
                      lambda: t.setdefault(("ZZB.US", "a"), 1.0),
                      lambda: t.update({("ZZB.US", "a"): 1.0})):
            with self.assertRaises(TypeError):
                write()
        del t[("ZZA.US", "a")]
        self.assertEqual((dict(t), dict(t.of("ZZA.US"))), ({}, {}))


if __name__ == "__main__":
    unittest.main()
