"""The year hand-off's Dec-31 rows under US rules (the outside audit of
2026-11-23, TJ-5065 and its review): a Canadian-dollar dividend in a US
project (base currency USD) is compared as the broker stated it, in CAD,
to the cent: a rate refreshed since is no correction, a cent is, in
CAD; a USD row is compared in USD.

Synthetic data only (made-up tickers and accounts, amounts under a
thousand)."""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

_SETTINGS = {"country": "usa", "year": 2026, "base_currency": "USD"}
_CFG = {"settings": _SETTINGS, "accounts": {"aa": {"type": "taxable"}}}
_CFG25 = {"settings": dict(_SETTINGS, year=2025),
          "accounts": {"aa": {"type": "taxable"}}}


def _work(test, base, native) -> Path:
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    work = Path(td.name) / "work"
    work.mkdir()
    (work / "aa_base.json").write_text(json.dumps({"transactions": base}))
    (work / "aa_raw.json").write_text(json.dumps({"transactions": native}))
    return work


def _div(net, gross, **extra):
    r = {"id": "d1", "action": "DIVIDEND", "date": "2025-12-31",
         "date_settle": "2025-12-31", "symbol": "ZZC.TO", "quantity": 100,
         "net_amount": net, "gross_amount": gross, "account": "aa"}
    r.update(extra)
    return r


def _cad(rate, gross=500.0, net=425.0):
    """A CAD dividend read at CADUSD `rate`: the base books' row (USD,
    `trade_currency` CAD) and the native books' (CAD)."""
    base = _div(round(net * rate, 2), round(gross * rate, 2),
                currency="USD", trade_currency="CAD")
    return [base], [_div(net, gross, currency="CAD")]


class TestACanadianDividendInAUsProject(unittest.TestCase):

    def _issues(self, rec, base, native):
        from taxjson.lib import handoff as H
        return H._boundary_issues(_work(self, base, native), _CFG, rec,
                                  "trade", set())

    def _record(self, base, native):
        from taxjson.lib import handoff as H
        return {"year": 2025, "boundary_rows": H.boundary_rows(
            _work(self, base, native), _CFG25, 2025)}

    @rule(R.US_RPT_06)
    def test_a_rate_refreshed_since_is_no_correction(self):
        rec = self._record(*_cad(0.7407))
        row = rec["boundary_rows"][0]
        self.assertEqual((row["native_currency"], row["native_income"],
                          row["native_net"]), ("CAD", 500.0, 425.0))
        self.assertEqual(self._issues(rec, *_cad(0.7300)), [])

    @rule(R.US_RPT_06)
    def test_a_cent_is_reported_in_cad(self):
        rec = self._record(*_cad(0.7407))
        out = self._issues(rec, *_cad(0.7407, gross=500.01, net=425.01))
        self.assertEqual(len(out), 1, out)
        it = out[0]
        self.assertEqual((it["currency"], it["native_income_difference"],
                          it["native_difference"]), ("CAD", 0.01, 0.01))
        why = " ".join(it["why"].split())
        self.assertIn("reported 0.01 CAD less", why)

    @rule(R.US_RPT_06)
    def test_a_usd_dividend_is_compared_in_usd(self):
        usd = [_div(42.5, 50.0, symbol="ZZU.US", currency="USD")]
        rec = self._record(usd, usd)
        self.assertEqual(self._issues(rec, usd, usd), [])
        now = [_div(42.49, 50.0, symbol="ZZU.US", currency="USD")]
        out = self._issues(rec, now, now)
        self.assertEqual([i["withheld_difference"] for i in out],
                         [{"USD": 0.01}])
        self.assertNotIn("currency", out[0])


if __name__ == "__main__":
    unittest.main()
