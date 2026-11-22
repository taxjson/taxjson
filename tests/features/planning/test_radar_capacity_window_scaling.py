"""Backlog group `perf` (2026-10-29): run time of large books.

TJ-0238: for every loss, the US engine walked every replacement record
of the class (and parsed each one's date), and every lot it sold or
split walked them again, so a one-symbol book cost losses x rows (4,000
rows 4.5 s, 8,000 rows 19 s). The records are now indexed by trade-date
window, by row id and by the lot they point at; the scans read the same
records in the same order, so no result changes.

TJ-0239: the wash radar's per-loss capacity test walked every
acquisition of the class; it now reads the window by bisect over the
acquisitions sorted by epoch.

TJ-0240: `strip_json_comments` ran a regex pass with a Python callback
over every string literal of every work/ JSON read (a tenth of a large
run); a text with no `#`, or one that is valid JSON as it stands, is now
returned as is.

The pin tests hold the v0.37.0 results on generated books, captured
before the change; the scaling tests count the per-row work the old
scans did per loss. All data is synthetic (ZZ tickers, made-up prices).

Formerly: test_fix_1029_perf
"""
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from tax_rules import rule
from taxjson.rules import ids as RID


def _bdays(lo, hi):
    out, d = [], lo
    while d <= hi:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class _CountingEpoch(float):
    """An epoch that counts its comparisons (the radar's window tests)."""
    n = 0

    def __lt__(self, o):
        _CountingEpoch.n += 1
        return float.__lt__(self, o)

    def __le__(self, o):
        _CountingEpoch.n += 1
        return float.__le__(self, o)

    def __gt__(self, o):
        _CountingEpoch.n += 1
        return float.__gt__(self, o)

    def __ge__(self, o):
        _CountingEpoch.n += 1
        return float.__ge__(self, o)


def _radar_row(d, qty, px, rid):
    return {"action": "BUYSELL", "date": d, "date_settle": d,
            "time": "10:00:00", "symbol": "ZZQ.TO", "quantity": qty,
            "price": px, "net_amount": round(-qty * px, 2),
            "currency": "CAD", "account": "margin", "commission": 0.0,
            "id": rid}


def _radar_json(rows, as_of):
    from taxjson.bin import taxjson_wash_radar as R
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d) / "margin_base.json"
        tp.write_text(json.dumps({"transactions": rows}))
        argv = ["taxjson-wash-radar", "--country", "canada", "--date",
                as_of, "--taxable", str(tp), "--all", "--json"]
        out, old = io.StringIO(), sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                R.main()
        finally:
            sys.argv = old
    return out.getvalue()


def _radar_book(pairs, span_days):
    days = _bdays(date(2022, 1, 3), date(2022, 1, 3)
                  + timedelta(days=span_days))
    rows = []
    for i in range(pairs):
        d = days[i * len(days) // pairs].isoformat()
        p = 20.0 + (i % 97) * 0.01
        q = round(p + (1 if i % 2 == 0 else -1), 2)
        rows.append(_radar_row(d, 10.0, round(p, 2), f"b{i}"))
        rows.append(_radar_row(d, -10.0, q, f"s{i}"))
    return rows


class TestRadarCapacityWindow(unittest.TestCase):
    """TJ-0239: the radar's per-loss capacity test reads the loss's
    window, not every acquisition of the class.

    Formerly: test_fix_1029_perf.TestRadarCapacityWindow"""

    @rule(RID.CA_SL_08)
    def test_window_comparisons_grow_with_rows_not_losses_x_rows(self):
        from taxjson.bin import taxjson_wash_radar as R
        rows = _radar_book(600, 1100)         # 1,200 rows over 3 years
        real = R.date_to_epoch
        _CountingEpoch.n = 0
        with mock.patch.object(R, 'date_to_epoch',
                               lambda s: _CountingEpoch(real(s))):
            doc = json.loads(_radar_json(rows, "2025-06-30"))
        self.assertIsInstance(doc, dict)
        self.assertLess(_CountingEpoch.n, 40 * len(rows))

    @rule(RID.CA_SL_08)
    def test_dense_book_report_unchanged(self):
        """A dense one-symbol book: the radar's document as v0.37.0
        wrote it (the run date aside)."""
        rows = _radar_book(150, 60)
        doc = json.loads(_radar_json(rows, "2022-02-20"))
        doc.pop('generated_at', None)
        # The keys buy-check's per-unit denial added since (a5c3bb4,
        # TJ-0559), a settled VIOLATION's forward view (TJ-0775) and the
        # registered units bought in the window and the last such buy
        # (TJ-1039, TJ-1177): every figure v0.37.0 wrote is unchanged.
        added = ('recent_buy', 'recent_loss_per_unit',
                 'recent_loss_per_unit_qty', 'forward',
                 'registered_window_qty', 'registered_window_last_add')

        def _v037(o):
            if isinstance(o, dict):
                return {k: _v037(v) for k, v in o.items() if k not in added}
            return [_v037(x) for x in o] if isinstance(o, list) else o
        doc = _v037(doc)
        self.assertEqual(
            hashlib.sha256(json.dumps(doc, sort_keys=True).encode())
            .hexdigest(), '6be9608d23f528ebbba6ec9bf93db8debff93a14'
                          'bffcec4272a8a7263699cc5c')


if __name__ == '__main__':
    unittest.main()
