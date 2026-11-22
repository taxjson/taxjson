"""Regression tests for the low-round filing-b fixes (fixl/filing-b):
reconcile-slips, t1135, audit and form-export.

Synthetic data only; account labels are fake.

Formerly: test_fix_l_filing_b
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.rules import ids as R
from _support.filing_bin_reports import _run, _gains, _g


if __name__ == "__main__":
    unittest.main()


from tax_rules import rule, rule  # noqa: E402


def _tx(action="BUYSELL", date="2025-01-15", symbol="AAPL.US", qty=0.0,
        net=0.0, settle=None, time="09:30:00", currency="CAD", **extra):
    d = {"action": action, "date": date, "date_settle": settle or date,
         "time": time, "symbol": symbol, "quantity": qty,
         "net_amount": net, "symbol_new": "", "currency": currency}
    d.update(extra)
    return d


def _base(td, rows, name="margin_base.json", **meta):
    p = Path(td) / name
    doc = {"transactions": rows}
    if meta:
        doc["metadata"] = meta
    p.write_text(json.dumps(doc))
    return p


# ---------------------------------------------------------------------------
# form-export, sum FOR THE RETURN, stand-alone layout checks
# ---------------------------------------------------------------------------

from taxjson.bin import taxjson_form_export as FE  # noqa: E402


class TestDateKeysPinned(unittest.TestCase):
    """R1-303: the settle-vs-trade date key of form-export, carryover and
    fx-cash was unpinned (t1135: TestT1135Walk).

    Formerly: test_fix_l_filing_b.TestDateKeysPinned"""

    @rule(R.CA_DATE_01)
    def test_form_export_year_by_settle_date(self):
        ents = [_g(date="2025-12-31", settle="2026-01-02"),
                _g(symbol="BBB.TO", date="2025-06-02")]
        with tempfile.TemporaryDirectory() as td:
            g = _gains(td, ents)
            rc, out, err = _run(FE.main, [
                str(g), "--form", "schedule3", "--country", "canada",
                "--year", "2026", "--date-basis", "settle", "--json"])
            self.assertEqual(rc, 0, err)
            self.assertEqual([r["symbol"] for r in json.loads(out)["rows"]],
                             ["AAA.TO"])
            rc, out, err = _run(FE.main, [
                str(g), "--form", "schedule3", "--country", "canada",
                "--year", "2025", "--date-basis", "settle", "--json"])
            self.assertEqual([r["symbol"] for r in json.loads(out)["rows"]],
                             ["BBB.TO"])

    @rule(R.CA_DATE_01)
    def test_carryover_buckets_by_settle_date(self):
        from taxjson.bin.taxjson_carryover import yearly_nets
        res = {"transactions": [_g(date="2025-12-31", settle="2026-01-02",
                                   proceeds=900.0, cost=1000.0)]}
        self.assertEqual(list(yearly_nets(res, "settle")), [2026])
        self.assertEqual(list(yearly_nets(res, "trade")), [2025])

    @rule(R.CA_SL_13)
    def test_carryover_blends_canadian_crypto_into_the_wash_pass(self):
        from taxjson.bin import taxjson_carryover as CO
        with tempfile.TemporaryDirectory() as td:
            eq = _base(td, [_tx(qty=10, net=-1000.0, symbol="AAA.TO")],
                       "margin_base.json")
            cr = _base(td, [
                _tx(date="2025-02-03", qty=1.0, net=-50000.0, symbol="BTC"),
                _tx(date="2025-03-03", qty=-1.0, net=40000.0, symbol="BTC"),
                _tx(date="2025-03-10", qty=1.0, net=-41000.0,
                    symbol="BTC")], "crypto_base.json")
            rc, out, err = _run(CO.main, [str(eq), "--crypto", str(cr),
                                          "--country", "canada", "--json"])
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        rows = doc.get("ledger") or doc.get("years") or doc.get("rows")
        row = next(r for r in rows if int(r.get("year")) == 2025)
        # The BTC loss is superficial (repurchased within 30 days, still
        # held): the year's net is 0, not -10,000.
        net = next(row[k] for k in ("net_gain", "net", "realized")
                   if k in row)
        self.assertAlmostEqual(float(net), 0.0, places=2)

    @rule(R.CA_FX_07)
    def test_fx_cash_walks_in_settlement_order(self):
        from taxjson.bin.taxjson_fx_cash import build_ledger
        rates = {("USD", "2026-02-11"): 1.30, ("USD", "2026-02-12"): 1.40}
        txs = [dict(action="BUYSELL", date="2026-02-09",
                    date_settle="2026-02-12", time="10:00:00",
                    symbol="AAA.US", quantity=10, currency="USD",
                    net_amount=1000.0, account="margin"),
               dict(action="BUYSELL", date="2026-02-10",
                    date_settle="2026-02-11", time="10:00:00",
                    symbol="BBB.US", quantity=-10, currency="USD",
                    net_amount=1000.0, account="margin")]
        doc = build_ledger(txs, "CAD", {}, 2026,
                           rate_of=lambda c, d: rates.get((c, d)))
        self.assertEqual(doc["overdrafts"], {})
        self.assertAlmostEqual(doc["net_gain"], 100.0, places=2)
