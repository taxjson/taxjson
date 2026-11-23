"""`tjs div-estimate` lists its rows by the next pay date, the latest first,
so the payment that comes next is the bottom row (the owner's request of
2026-11-22); a row with no projected payment sits above them, by ticker.
`--json` keeps the document's order. Synthetic payers only."""
import unittest

from taxjson.lib import div_estimate as DE

from _support.view_div_estimate import _div, _est, _pos, _trade

# Quarterly payers on different months, and one with a single payment
# and a quarterly date since, passed with the shares held (no schedule:
# no next date).
_EARLY = ["2025-07-10", "2025-10-10", "2026-01-10", "2026-04-10"]
_MID = ["2025-08-20", "2025-11-20", "2026-02-20", "2026-05-20"]
_LATE = ["2025-09-15", "2025-12-15", "2026-03-15", "2026-06-15"]


def _doc():
    rows = [("margin", _div(d, "ZZA.TO", 100, 0.5)) for d in _LATE]
    rows += [("margin", _div(d, "ZZM.TO", 100, 0.5)) for d in _EARLY]
    rows += [("margin", _div(d, "ZZZ.TO", 100, 0.5)) for d in _MID]
    rows.append(_trade("2025-12-01", "ZZB.TO", 10))
    rows.append(("margin", _div("2026-02-01", "ZZB.TO", 10, 2.0)))
    return _est(rows, [_pos("ZZA.TO", 100), _pos("ZZM.TO", 100),
                       _pos("ZZZ.TO", 100), _pos("ZZB.TO", 10)])


def _table_tickers(text):
    lines = text.splitlines()
    k = next(i for i, ln in enumerate(lines) if ln.startswith("---"))
    out = []
    for ln in lines[k + 1:]:
        if not ln.strip():
            break
        out.append(ln.split()[0])
    return out


class TestTheNextPaymentIsTheBottomRow(unittest.TestCase):

    def test_the_document_stays_by_ticker(self):
        doc = _doc()
        self.assertEqual([r["symbol"] for r in doc["rows"]],
                         ["ZZA.TO", "ZZB.TO", "ZZM.TO", "ZZZ.TO"])
        nxt = {r["symbol"]: r["next_pay_date"] for r in doc["rows"]}
        self.assertIsNone(nxt["ZZB.TO"])
        self.assertLess(nxt["ZZM.TO"], nxt["ZZZ.TO"])
        self.assertLess(nxt["ZZZ.TO"], nxt["ZZA.TO"])

    def test_the_table_is_by_next_date_latest_first(self):
        doc = _doc()
        before = [dict(r) for r in doc["rows"]]
        want = ["ZZB.TO", "ZZA.TO", "ZZZ.TO", "ZZM.TO"]
        self.assertEqual(_table_tickers(DE.render(doc).text()), want)
        full = DE.render(doc, details=True).text()
        self.assertEqual(_table_tickers(full), want)
        sched = full.split("SCHEDULES AND PROJECTED PAYMENTS")[1]
        self.assertEqual(
            sorted(want, key=lambda s: sched.index(f"- {s} (")), want)
        self.assertEqual(doc["rows"], before)      # the document: untouched

    def test_the_same_date_is_by_ticker_then_account(self):
        def r(sym, acct, d):
            return {"symbol": sym, "account": acct, "next_pay_date": d}
        rows = [r("ZZB", "tfsa", "2026-07-01"), r("ZZB", "margin", "2026-07-01"),
                r("ZZA", "margin", "2026-07-01"), r("ZZN", "margin", None),
                r("ZZC", "margin", "2026-08-01"), r("ZZD", "margin", None)]
        self.assertEqual(
            [(x["symbol"], x["account"]) for x in DE.next_last(rows)],
            [("ZZN", "margin"), ("ZZD", "margin"), ("ZZC", "margin"),
             ("ZZA", "margin"), ("ZZB", "margin"), ("ZZB", "tfsa")])


if __name__ == "__main__":
    unittest.main()
