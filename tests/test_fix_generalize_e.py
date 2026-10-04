"""No hard-coded data in the flow (owner, 2026-10-04), part E: no
built-in FX rate (B13), Yahoo spellings and the scan probe (B14),
Canadian twins from every venue (B15), the Questrade dividend matching
key (B18), and IB return of capital judged against the project's
country (partition)."""
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from tax_rules import rule  # noqa: E402

from taxjson.bin import taxjson_convert_currency as CC  # noqa: E402
from taxjson.lib.core import TaxTransaction  # noqa: E402


def _usd_row(date, tid="T1"):
    return TaxTransaction(id=tid, date=date, symbol="ZZQ.US",
                          action="BUYSELL", quantity=1.0, price=10.0,
                          net_amount=-10.0, proceeds=0.0, currency="USD",
                          account="A")


class TestNoBuiltInFxRate(unittest.TestCase):
    """B13: a date with no rate stops the conversion, naming the date
    and the pair; no 1.35 placeholder."""

    def setUp(self):
        CC.reset_fallback_tally()

    def test_no_placeholder_constant(self):
        self.assertFalse(hasattr(CC, "DEFAULT_RATE"))
        self.assertFalse(hasattr(CC, "IMPLICIT_PAIR_RATES"))
        self.assertIsNone(CC.default_rate_for("USD", "CAD"))
        self.assertEqual(CC.default_rate_for("USD", "CAD", 1.5),
                         Decimal("1.5"))

    def test_prior_rate_within_lookback_is_used(self):
        hist = {"USD": {"2025-03-03": Decimal("1.44")}}
        self.assertEqual(CC.get_rate_for_date("USD", "2025-03-08", hist,
                                              None), Decimal("1.44"))
        self.assertIsNone(CC.get_rate_for_date("USD", "2025-03-09", hist,
                                               None))

    def test_missing_rate_stops_naming_date_and_pair(self):
        hist = {"USD": {"2025-03-03": Decimal("1.44")}}
        with self.assertRaises(CC.MissingRateError) as cm:
            CC.process_transactions([_usd_row("2025-03-04", "T0"),
                                     _usd_row("2025-03-20")], "CAD",
                                    hist, None, country="canada")
        msg = str(cm.exception)
        self.assertIn("USD->CAD on 2025-03-20", msg)
        self.assertNotIn("2025-03-04", msg)
        self.assertNotIn("1.35", msg)

    def test_currency_with_no_rates_stops(self):
        with self.assertRaises(CC.MissingRateError) as cm:
            CC.process_transactions([_usd_row("2025-03-04")], "CAD", {},
                                    None, country="canada")
        self.assertIn("USD->CAD on 2025-03-04", str(cm.exception))

    def test_explicit_default_rate_is_an_opt_in(self):
        out = CC.process_transactions([_usd_row("2025-03-20")], "CAD",
                                      {}, Decimal("2"), country="canada")
        self.assertEqual(out[0].currency, "CAD")
        self.assertAlmostEqual(out[0].net_amount, -20.0)

    def test_audit_twin_reports_missing(self):
        from taxjson.bin.taxjson_audit import rate_with_provenance
        self.assertEqual(rate_with_provenance("USD", "2025-03-09",
                                              {"USD": {}}, None),
                         (None, "missing", None))

    def test_fees_report_stops_on_a_missing_rate(self):
        from taxjson.bin import taxjson_fees as F
        with self.assertRaises(CC.MissingRateError):
            self._fees_one(F)

    def _fees_one(self, F):
        import json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ib.json"
            p.write_text(json.dumps({
                "metadata": {"source_brokerage": "ib"},
                "transactions": [{
                    "id": "F1", "date": "2025-03-20", "symbol": "ZZQ.US",
                    "action": "BUYSELL", "quantity": 1, "price": 10,
                    "net_amount": -11, "commission": -1, "fee": 0,
                    "currency": "USD", "account": "A"}]}))
            F.aggregate([p], year=None, since=None, to_curr="CAD",
                        history={}, default_rate=None, by_account=False)

    @rule("CA-FX-02")
    def test_canada_rule_states_no_built_in_rate(self):
        from taxjson.lib import tax_logic
        text = tax_logic.catalog()["CA-FX-02"].text
        self.assertNotIn("1.35", text)
        self.assertIn("no built-in rate", text)

    @rule("US-FX-02")
    def test_usa_rule_states_no_built_in_rate(self):
        from taxjson.lib import tax_logic
        text = tax_logic.catalog()["US-FX-02"].text
        self.assertNotIn("1.35", text)
        self.assertIn("no built-in rate", text)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- B14

import json  # noqa: E402
import subprocess  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

_FAKE_YF = '''
import os
class _H:
    empty = True
class Ticker:
    def __init__(self, s):
        with open(os.environ["FAKE_YF_LOG"], "a") as f:
            f.write(s + "\\n")
        self.info = {}
        self.history_metadata = {}
    def history(self, **kw):
        return _H()
'''


def _holdings_toml(*symbols):
    out = ['schema_version = "1.2"']
    for sym in symbols:
        out.append(f'[[holding]]\nsymbol = "{sym}"\nquantity = 100.0\n'
                   f'currency = "USD"\ntotal_cost = 1000.0\n'
                   f'cost_per_share = 10.0')
    return "\n".join(out) + "\n"


def _raw_json(*div_symbols):
    return json.dumps({"transactions": [
        {"action": "DIVIDEND", "date": "2026-03-01", "symbol": s,
         "quantity": 0, "gross_amount": 10.0, "net_amount": 10.0,
         "currency": "USD"} for s in div_symbols]})


def _scan_project(tmp, *, accounts, holdings, raws, ticker_map=None):
    root = Path(tmp) / "proj"
    (root / "work").mkdir(parents=True)
    (root / "reports").mkdir()
    acct_toml = "".join(
        f'[accounts.{n}]\ntype = "{t}"\n' for n, t in accounts)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        'base_currency = "CAD"\n' + acct_toml)
    for name, text in holdings.items():
        (root / "reports" / f"{name}_holdings.toml").write_text(text)
    for name, text in raws.items():
        (root / "work" / f"{name}_raw.json").write_text(text)
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map)
    return root


def _scan(root, *args, fake_yf_dir=None, log=None):
    env = dict(os.environ)
    env.pop("TAXJSON_OFFLINE", None)
    src = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        ([str(fake_yf_dir)] if fake_yf_dir else []) + [src])
    if log:
        env["FAKE_YF_LOG"] = str(log)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "scan", *args], cwd=REPO_ROOT, capture_output=True, text=True,
        env=env)


class TestYahooSpelling(unittest.TestCase):
    """B14: no named-security special case; any class letter."""

    def test_class_shares_any_letter(self):
        from taxjson.lib.price_chain import yf_symbol_for as y
        self.assertEqual(y("ZZQ.B.US"), "ZZQ-B")
        self.assertEqual(y("ZZQ.C.TO"), "ZZQ-C.TO")
        self.assertEqual(y("ZZQ.B.TO"), "ZZQ-B.TO")
        self.assertEqual(y("ZZQ.PR.A.TO"), "ZZQ-PA.TO")
        self.assertEqual(y("ZZQ.PR.G.TO"), "ZZQ-PG.TO")
        self.assertEqual(y("ZZQ.UN.TO"), "ZZQ-UN.TO")
        self.assertEqual(y("ZZQ.TO"), "ZZQ.TO")

    def test_no_named_security_in_the_converter(self):
        import inspect
        from taxjson.lib import price_chain
        src = inspect.getsource(price_chain.yf_symbol_for)
        self.assertNotIn("BRK", src)

    def test_scan_online_asks_yahoo_for_its_spelling(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fakeyf"
            fake.mkdir()
            (fake / "yfinance.py").write_text(_FAKE_YF)
            log = Path(tmp) / "yf.log"
            root = _scan_project(
                tmp, accounts=[("margin", "taxable")],
                holdings={"margin": _holdings_toml("ZZQ.B.US", "ZZR.US")},
                raws={"margin": _raw_json("ZZQ.B.US")},
                ticker_map="QUOTE ZZR.US ZZRX\n")
            _scan(root, "--online", fake_yf_dir=fake, log=log)
            asked = set(log.read_text().split()) if log.exists() else set()
        self.assertIn("ZZQ-B", asked)            # not ZZQ.B.US
        self.assertIn("ZZRX", asked)             # the QUOTE line
        self.assertNotIn("ZZQ.B.US", asked)
        self.assertNotIn("ZZR.US", asked)


# ---------------------------------------------------------------- B15

class TestCanadianTwinOnEveryVenue(unittest.TestCase):
    """B15: a US listing's Canadian twin is found on every Canadian
    venue, not only as `{root}.TO`."""

    @rule("CA-SCAN-02")
    def test_venture_twin_seen_in_the_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(
                tmp, accounts=[("margin", "taxable"), ("rrsp", "sheltered")],
                holdings={"margin": _holdings_toml("ZZQ.US"),
                          "rrsp": _holdings_toml("ZZQ.V")},
                raws={"margin": _raw_json("ZZQ.US")})
            r = _scan(root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("US-LISTING", r.stdout)
        self.assertIn("hold ZZQ.V instead", r.stdout)
        self.assertIn("ZZQ.V/ZZQ.US", r.stdout)       # MAP-GAP

    @rule("CA-SCAN-02")
    def test_mapped_cse_twin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(
                tmp, accounts=[("margin", "taxable")],
                holdings={"margin": _holdings_toml("ZZQ.US")},
                raws={"margin": _raw_json("ZZQ.US")},
                ticker_map="TOBASE ZZQ.US ZZQ.CN\n")
            r = _scan(root)
        self.assertIn("hold ZZQ.CN instead", r.stdout)
        self.assertNotIn("MAP-GAP", r.stdout)

    def test_distinct_venue_twin_is_not_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(
                tmp, accounts=[("margin", "taxable"), ("rrsp", "sheltered")],
                holdings={"margin": _holdings_toml("ZZQ.US"),
                          "rrsp": _holdings_toml("ZZQ.NE")},
                raws={"margin": _raw_json("ZZQ.US")},
                ticker_map="DISTINCT ZZQ.US ZZQ.NE\n")
            r = _scan(root)
        self.assertNotIn("US-LISTING", r.stdout)
        self.assertNotIn("MAP-GAP", r.stdout)
