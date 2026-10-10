"""`taxjson check-dates` (lib/check_dates): dates against each market."""
import json
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from taxjson.lib.check_dates import analyze, check_trade_time, render


class TestTradeTimes(unittest.TestCase):
    def chk(self, cls, d, t=None, sym="X.US", cur="USD"):
        r = check_trade_time(cls, date.fromisoformat(d),
                             time.fromisoformat(t) if t else None, sym, cur)
        return r[1] if r else None

    def test_crypto_any_time(self):
        self.assertIsNone(self.chk("crypto", "2025-12-25", "03:00:00"))
        self.assertIsNone(self.chk("crypto", "2025-06-07", "12:00:00"))

    def test_futures_23_5(self):
        self.assertEqual(self.chk("futures", "2025-06-07", "12:00:00"),
                         "futures-saturday")
        self.assertEqual(self.chk("futures", "2025-06-08", "10:00:00"),
                         "futures-sunday-early")
        self.assertIsNone(self.chk("futures", "2025-06-08", "19:00:00"))
        self.assertIsNone(self.chk("futures", "2025-06-10", "02:00:00"))
        self.assertEqual(self.chk("futures", "2025-12-25", "10:00:00"),
                         "futures-holiday")

    def test_us_stocks_overnight(self):
        self.assertIsNone(self.chk("us-equity", "2025-06-08", "21:00:00"))
        self.assertEqual(self.chk("us-equity", "2025-06-08", "10:00:00"),
                         "weekend-trade")
        self.assertEqual(self.chk("us-equity", "2025-06-07", "21:00:00"),
                         "weekend-trade")
        self.assertEqual(self.chk("us-equity", "2025-06-06", "21:00:00"),
                         "friday-night-trade")
        self.assertEqual(self.chk("us-equity", "2025-07-04", "10:00:00"),
                         "holiday-trade")
        self.assertIsNone(self.chk("us-equity", "2025-07-03", "21:30:00"))

    def test_options_and_canada_exchange_days(self):
        self.assertEqual(self.chk("option", "2025-06-08", "21:00:00",
                                  "X250620C00010000.US"), "weekend-trade")
        self.assertEqual(self.chk("ca-equity", "2025-08-04", "10:00:00",
                                  "X.TO", "CAD"), "holiday-trade")
        # Remembrance Day: the TSX trades.
        self.assertIsNone(self.chk("ca-equity", "2025-11-11", "10:00:00",
                                   "X.TO", "CAD"))


def _project(rows_by_file, crypto=False):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    work = root / "work"
    work.mkdir()
    lines = []
    for (kind, name), rows in rows_by_file.items():
        if kind == "tt":
            lines.append(f"tt/{name}")
            (work / f"m_tt_{Path(name).stem}.json").write_text(json.dumps(rows))
        else:
            lines.append(f"{kind}/{name}")
            (work / f"m_{kind}.json").write_text(json.dumps(rows))
    (work / "m_sources.list").write_text("\n".join(lines) + "\n")
    cfg = {"settings": {"year": 2025},
           "accounts": {"m": {"type": "taxable", "crypto": crypto}}}
    return td, root, cfg


def _r(date_, settle, sym="ABC.US", time_="10:00:00", price=10.0,
       action="BUYSELL", desc=""):
    return {"action": action, "date": date_, "date_settle": settle,
            "time": time_, "symbol": sym, "quantity": 1, "price": price,
            "currency": "USD", "description": desc}


class TestAnalyze(unittest.TestCase):
    def codes(self, doc):
        return sorted({i["code"] for i in doc["issues"]})

    def test_clean(self):
        td, root, cfg = _project({("ib", "a.csv"): [
            _r("2025-04-17", "2025-04-21")]})
        with td:
            doc = analyze(root, cfg, today=date(2026, 1, 1))
        self.assertEqual(doc["issues"], [])
        self.assertIn("Every date lands", "\n".join(render(doc)))

    def test_render_lists_note_rows(self):
        td, root, cfg = _project({("ib", "a.csv"): [
            _r("2025-05-31", "2025-05-31", action="DIVIDEND")]})
        with td:
            doc = analyze(root, cfg, today=date(2026, 1, 1))
        # (the notes are listed with --details: docs/output-style.md)
        text = "\n".join(render(doc, details=True))
        self.assertIn("ABC.US", text)
        self.assertIn("Saturday", text)

    def test_errors(self):
        td, root, cfg = _project({("ib", "a.csv"): [
            _r("2025-06-07", "2025-06-09"),               # Saturday
            _r("2025-06-10", "2025-06-09"),               # settles first
            _r("2026-06-05", "2026-06-08"),               # future
            # Far outside the year: ONE error, not also "future" (A2-1192).
            _r("2027-01-05", "2027-01-06")]})
        with td:
            doc = analyze(root, cfg, today=date(2026, 1, 1))
        self.assertEqual(self.codes(doc), ["future-date", "out-of-range",
                                           "settle-before-trade",
                                           "weekend-trade"])
        self.assertGreater(doc["errors"], 0)

    def test_tt_weekend_settle(self):
        td, root, cfg = _project({("tt", "start.tt"): [
            _r("2025-06-07", "2025-06-07")]})
        with td:
            doc = analyze(root, cfg, today=date(2026, 1, 1))
        self.assertEqual(self.codes(doc), ["settle-weekend"])

    def test_cycle_note_and_exemptions(self):
        td, root, cfg = _project({("rbc_direct", "r.csv"): [
            _r("2025-05-16", "2025-05-20"),               # RBC: Victoria Day (CA calendar, accepted)
            _r("2025-06-10", "2025-06-13"),               # neither calendar
            _r("2025-03-14", "2025-03-14",
               sym="QZD250314C00071000.US"),             # expiry-day trade
            _r("2025-08-12", "2025-08-12", price=0.0,
               desc="STK DIV ON 850 SHS"),               # event row
            _r("2025-09-15", "2025-09-15", price=4.2,
               desc="QZR REINV@C$4.17305 PAY 09/15/26")]})   # DRIP
        with td:
            doc = analyze(root, cfg, today=date(2026, 1, 1))
        self.assertEqual(self.codes(doc), ["settle-cycle"])
        self.assertEqual([i["date"] for i in doc["issues"]], ["2025-06-10"])
        self.assertEqual(doc["errors"], 0)

    def test_crypto_weekend_ok(self):
        td, root, cfg = _project({("kraken", "k.csv"): [
            _r("2025-06-07", "2025-06-07", sym="BTC", time_="03:00:00")]},
            crypto=True)
        with td:
            doc = analyze(root, cfg, today=date(2026, 1, 1))
        self.assertEqual(doc["issues"], [])


if __name__ == "__main__":
    unittest.main()
