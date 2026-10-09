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

from tax_rules import rule, rule_absent  # noqa: E402

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


def _suggest(root):
    """`taxjson ticker-map --suggest` (scan's MAP-GAP pairs moved
    there, to verify)."""
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "ticker-map", "--suggest"], cwd=REPO_ROOT, capture_output=True,
        text=True)


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
         "tips", *args], cwd=REPO_ROOT, capture_output=True, text=True,
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

    def test_tips_online_asks_yahoo_for_its_spelling(self):
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
            gap = _suggest(root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("US-LISTING", r.stdout)
        self.assertIn("hold ZZQ.V instead", r.stdout)
        self.assertIn("TOBASE ZZQ.US ZZQ.V   or   DISTINCT ZZQ.US ZZQ.V",
                      gap.stdout)                     # MAP-GAP

    @rule("CA-SCAN-02")
    def test_mapped_cse_twin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(
                tmp, accounts=[("margin", "taxable")],
                holdings={"margin": _holdings_toml("ZZQ.US")},
                raws={"margin": _raw_json("ZZQ.US")},
                ticker_map="TOBASE ZZQ.US ZZQ.CN\n")
            r = _scan(root)
            gap = _suggest(root)
        self.assertIn("hold ZZQ.CN instead", r.stdout)
        self.assertNotIn("To verify", gap.stdout)

    def test_distinct_venue_twin_is_not_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _scan_project(
                tmp, accounts=[("margin", "taxable"), ("rrsp", "sheltered")],
                holdings={"margin": _holdings_toml("ZZQ.US"),
                          "rrsp": _holdings_toml("ZZQ.NE")},
                raws={"margin": _raw_json("ZZQ.US")},
                ticker_map="DISTINCT ZZQ.US ZZQ.NE\n")
            r = _scan(root)
            gap = _suggest(root)
        self.assertNotIn("US-LISTING", r.stdout)
        self.assertNotIn("To verify", gap.stdout)


# ---------------------------------------------------------------- B18

class TestQuestradeKeyWithoutDealerNames(unittest.TestCase):
    """B18: the dividend <-> trade matching key keeps no list of dealer
    names; a transfer row naming any dealer still teaches the map."""

    def _parse(self, *bodies):
        from test_fix_rbcqt import qt_parse
        return qt_parse(*bodies)

    def test_key_has_no_dealer_list(self):
        from taxjson.lib.brokerages import questrade as Q
        import inspect
        src = inspect.getsource(Q).upper()
        for name in ("SCOTIA", "CIBC", "DOMINION SECURITIES",
                     "NATIONAL BANK", "INTERACTIVE"):
            self.assertNotIn(name, src.split("_DESC_NOISE_RES")[1]
                             .split("_SPINOFF_PARENT_RE")[0])

    def test_any_dealer_on_a_transfer_teaches_the_dividend(self):
        from test_fix_rbcqt import q, qdiv, of
        for dealer in ("ZZDEALER SECURITIES INC 41.75 TRANSFER",
                       "QQ WEALTH LTD",
                       "TRANSFER IN SOME OTHER BROKER"):
            tfi = q(action='TF6', sym='ZZQ', desc=f'ZZQ MINES LTD {dealer}',
                    qty='10', price='0', gross='0', comm='0', net='0',
                    act='Transfers')
            div = qdiv('A012345', 'ZZQ MINES LTD CASH DIV ON 10 SHS REC '
                       '06/01/25 PAY 06/15/25', '4.00')
            txs, err, _ = self._parse(tfi + div)
            self.assertEqual(of(txs, action='DIVIDEND')[0]['symbol'],
                             'ZZQ.US', (dealer, err))

    def test_transfer_with_internal_code_resolves_via_trade(self):
        from test_fix_rbcqt import q, of
        trade = q(sym='ZZQ', desc='ZZQ MINES LTD WE ACTED AS AGENT')
        tfi = q(action='TF6', sym='R777301', act='Transfers', qty='7',
                price='0', gross='0', comm='0', net='0',
                desc='ZZQ MINES LTD ZZDEALER SECURITIES 41.75 TRANSFER '
                     'BOOK VALUE 371.21')
        txs, err, _ = self._parse(trade + tfi)
        self.assertEqual(of(txs, action='TRANSFER')[0]['symbol'], 'ZZQ.US',
                         err)

    def test_class_letters_are_symmetric(self):
        from taxjson.lib.brokerages.questrade import _get_desc_key
        self.assertEqual(_get_desc_key("ZZR HOLDINGS INC CLASS A SUB VTG "
                                       "WE ACTED AS AGENT"),
                         "ZZR HOLDINGS INC CL A")
        self.assertEqual(_get_desc_key("ZZR HOLDINGS INC CLASS B SUB VTG"),
                         "ZZR HOLDINGS INC CL B")
        self.assertEqual(_get_desc_key("ZZR HOLDINGS INC CL B CASH DIV ON "
                                       "10 SHS"), "ZZR HOLDINGS INC CL B")

    def test_class_dividend_without_the_class_word_finds_the_one_class(self):
        from test_fix_rbcqt import q, qdiv, of
        trade = q(sym='ZZR', desc='ZZR HOLDINGS INC CLASS B WE ACTED AS '
                  'AGENT')
        div = qdiv('A012345', 'ZZR HOLDINGS INC CASH DIV ON 10 SHS', '4.00')
        txs, err, _ = self._parse(trade + div)
        self.assertEqual(of(txs, action='DIVIDEND')[0]['symbol'], 'ZZR.US',
                         err)

    def test_name_with_transfer_inside_is_kept(self):
        from taxjson.lib.brokerages.questrade import _get_desc_key
        self.assertEqual(_get_desc_key("ZZ TRANSFER LP WE ACTED AS AGENT"),
                         "ZZ TRANSFER LP")


# ------------------------------------------------- partition: IB ROC home

_IB_STMT = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Dividends,Header,Currency,Account,Date,Description,Amount\n'
            'Dividends,Data,USD,U5550001,2026-06-30,'  # pii-ok
            'QZRX(US0000000017) Return of Capital USD 0.12 per Share,'
            '24.00\n'
            'Dividends,Data,CAD,U5550001,2026-06-30,'  # pii-ok
            'QZRT(CA0000000011) Return of Capital CAD 0.12 per Share,'
            '12.00\n')


def _ib_parse(country, foreign_roc):
    from taxjson.lib.brokerages.ib_extractor import IbBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ib.csv"
        p.write_text(_IB_STMT)
        ib = IbBrokerage()
        ib.country = country
        ib.foreign_return_of_capital = foreign_roc
        txs = ib.parse_file(p)
    return {t['symbol']: t['action'] for t in txs
            if t['action'] in ('ADJUST', 'DIVIDEND')}


def _brokerage_cli(country):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ib.csv"
        p.write_text(_IB_STMT)
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_brokerage", str(p),
             "--brokerage", "ib", "--country", country], cwd=REPO_ROOT,
            capture_output=True,
            text=True, env=dict(os.environ,
                                PYTHONPATH=str(REPO_ROOT / "src")))
    doc = json.loads(r.stdout)
    return {t['symbol']: t['action'] for t in doc['transactions']
            if t['action'] in ('ADJUST', 'DIVIDEND')}


class TestIbRocHomeCountry(unittest.TestCase):
    """Partition: the IB parser's s.90(1) "foreign issuer" test is
    Canadian law; in a US project no issuer's return of capital becomes
    a dividend, a Canadian one included."""

    @rule("CA-ACB-08")
    @rule_absent("CA-ACB-08", country="usa")
    @rule("US-ROC-01")
    def test_same_statement_under_both_countries(self):
        ca = _brokerage_cli("canada")
        us = _brokerage_cli("usa")
        self.assertEqual(ca, {"QZRX.US": "DIVIDEND", "QZRT.TO": "ADJUST"})
        self.assertEqual(us, {"QZRX.US": "ADJUST", "QZRT.TO": "ADJUST"})

    @rule("CA-ACB-08")
    @rule_absent("CA-ACB-08", country="usa")
    def test_us_parser_never_applies_s90_even_if_asked(self):
        self.assertEqual(_ib_parse("canada", "dividend"),
                         {"QZRX.US": "DIVIDEND", "QZRT.TO": "ADJUST"})
        self.assertEqual(_ib_parse("usa", "dividend"),
                         {"QZRX.US": "ADJUST", "QZRT.TO": "ADJUST"})
