"""A listing read from the evidence, not the row currency alone
(lib/listing_suffix; tax-logic CA-XLIST-02 / US-XLIST-02).

Owner report (new-user run, an IB -> Questrade RRSP transfer): Questrade's
website export files interlisted shares that arrived from IB under the
TSX ticker on a USD row. The parser named the listing from the currency
(USD -> .US), so the in-leg became a listing that does not exist, and
the cross-listing join pooled the company under it. The run now reads
such a symbol as the other listing when the books show it: a transfer
journal from another ticker on the same currency's listing (two US
tickers never name one company's identical shares), or the other
listing known elsewhere under the same name.

Every fixture is SYNTHETIC: invented tickers (QZ*), names, fake account
ids (pii-ok: 55500001, U5550001).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib import listing_suffix as LS
from taxjson.lib.symbol_codes import exact_name
from tax_rules import rule

REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------ unit level

def _scan(**cands):
    """Scan of a receiving group: listing=(currency, name, arrivals)."""
    s = LS.Scan()
    for k, (cur, name, arr) in cands.items():
        lst = k.replace("_", ".")
        c = s.candidate(lst, cur)
        c.add_name(name)
        c.arrivals += list(arr)
        s.saw(lst, cur, name)
    return s


def _ev(outs=(), named=(), seen=(), renames=()):
    """Evidence: outs=[(symbol, date, qty, name)], named=[(symbol, name)]
    (a broker that names its listings), seen=[(symbol, account, broker,
    currency, name)]."""
    ev = LS.Evidence()
    for sym, d, q, name in outs:
        ev.outs.append(LS.OutLeg("ibm", "ib", sym, d, q, {exact_name(name)}))
        ev.named.setdefault(sym, set()).add(exact_name(name))
        ev.seen.setdefault(sym, []).append(
            ("ibm", "ib", "USD", frozenset([exact_name(name)])))
    for sym, name in named:
        ev.named.setdefault(sym, set()).add(exact_name(name))
        ev.seen.setdefault(sym, []).append(
            ("ibm", "ib", "USD", frozenset([exact_name(name)])))
    for sym, acct, broker, cur, name in seen:
        ev.seen.setdefault(sym, []).append(
            (acct, broker, cur, frozenset([exact_name(name)])))
    ev.renames = {frozenset(p) for p in renames}
    return ev


def _res(scan, ev, **kw):
    return LS.resolve(scan, ev, account="qt", broker="questrade", **kw)


@rule("CA-XLIST-02")
class TestResolve(unittest.TestCase):
    ARR = [("2026-09-03", 24.0)]

    def test_another_us_ticker_out_reads_as_the_tsx_listing(self):
        r = _res(_scan(QZAX_US=("USD", "QZALPHA MINES CORP TRANSFER IN",
                                self.ARR)),
                 _ev(outs=[("QZAA.US", "2026-09-01", 24,
                            "QZALPHA MINES CORP")]))
        got = r["corrected"]["QZAX.US"]
        self.assertEqual((got["symbol"], got["how"]), ("QZAX.TO", "transfer"))
        self.assertIn("transfer out of 24 QZAA.US on 2026-09-01",
                      got["evidence"])
        self.assertEqual(LS.why("QZAX.US", got),
                         "QZAX.US read as QZAX.TO: Questrade files the TSX "
                         "listing on a USD row")

    def test_the_same_tickers_other_listing_out(self):
        r = _res(_scan(QZAX_US=("USD", "QZALPHA MINES CORP", self.ARR)),
                 _ev(outs=[("QZAX.TO", "2026-09-01", 24,
                            "QZALPHA MINES CORP")]))
        self.assertEqual(r["corrected"]["QZAX.US"]["symbol"], "QZAX.TO")

    def test_a_genuine_us_listing_stays(self):
        # The same ticker out of IB (a custody move), and another ticker
        # out of the other country's listing (an ordinary journal).
        for leg in (("QZAX.US", "2026-09-01", 24, "QZALPHA MINES CORP"),
                    ("QZAY.TO", "2026-09-01", 24, "QZALPHA MINES CORP")):
            with self.subTest(leg=leg):
                r = _res(_scan(QZAX_US=("USD", "QZALPHA MINES CORP",
                                        self.ARR)), _ev(outs=[leg]))
                self.assertEqual(r, {"corrected": {}, "kept": {}})

    def test_names_must_be_equal(self):
        for name in ("QZALPHA MINES CORP CL B", "QZALPHA MINES LTD",
                     "QZBETA MINES CORP"):
            with self.subTest(name=name):
                r = _res(_scan(QZAX_US=("USD", name, self.ARR)),
                         _ev(outs=[("QZAA.US", "2026-09-01", 24,
                                    "QZALPHA MINES CORP")]))
                self.assertEqual(r["corrected"], {})

    def test_quantity_window_and_ambiguity(self):
        s = _scan(QZAX_US=("USD", "QZALPHA MINES CORP", self.ARR))
        for outs in ([("QZAA.US", "2026-08-20", 24, "QZALPHA MINES CORP")],
                     [("QZAA.US", "2026-09-01", 23, "QZALPHA MINES CORP")],
                     [("QZAA.US", "2026-09-01", 24, "QZALPHA MINES CORP"),
                      ("QZAB.US", "2026-09-02", 24, "QZALPHA MINES CORP")]):
            with self.subTest(outs=outs):
                self.assertEqual(_res(s, _ev(outs=outs))["corrected"], {})

    def test_the_map_wins(self):
        r = _res(_scan(QZAX_US=("USD", "QZALPHA MINES CORP", self.ARR)),
                 _ev(outs=[("QZAA.US", "2026-09-01", 24,
                            "QZALPHA MINES CORP")]),
                 mapped=lambda s: s == "QZAX.US")
        self.assertEqual(r["corrected"], {})
        self.assertIn("ticker.map", r["kept"]["QZAX.US"]["reason"])

    def test_a_real_us_listing_or_a_rename_stays(self):
        s = _scan(QZAX_US=("USD", "QZALPHA MINES CORP", self.ARR))
        out = [("QZAA.US", "2026-09-01", 24, "QZALPHA MINES CORP")]
        r = _res(s, _ev(outs=out, named=[("QZAX.US", "QZALPHA MINES CORP")]))
        self.assertEqual(r["corrected"], {})
        self.assertIn("real listing", r["kept"]["QZAX.US"]["reason"])
        r = _res(s, _ev(outs=out, renames=[("QZAA", "QZAX")]))
        self.assertEqual(r["corrected"], {})
        self.assertIn("rename", r["kept"]["QZAX.US"]["reason"])

    def test_one_native_pool_cannot_hold_both_currencies(self):
        s = _scan(QZAX_US=("USD", "QZALPHA MINES CORP", self.ARR))
        s.saw("QZAX.TO", "CAD", "QZALPHA MINES CORP")
        r = _res(s, _ev(outs=[("QZAA.US", "2026-09-01", 24,
                               "QZALPHA MINES CORP")]))
        self.assertEqual(r["corrected"], {})
        self.assertEqual(r["kept"]["QZAX.US"]["line"],
                         "TOBASE QZAX.US QZAX.TO")

    def test_the_other_listing_known_elsewhere(self):
        s = _scan(QZAX_US=("USD", "QZALPHA MINES CORP", ()))
        r = _res(s, _ev(seen=[("QZAX.TO", "tfsa", "questrade", "CAD",
                               "QZALPHA MINES CORP")]))
        self.assertEqual(r["corrected"]["QZAX.US"]["how"], "listing")
        r = _res(s, _ev(seen=[("QZAX.TO", "tfsa", "questrade", "CAD",
                               "QZOTHER HOLDINGS INC")]))
        self.assertEqual(r["corrected"], {})


@rule("CA-XLIST-02")
class TestScan(unittest.TestCase):
    def test_bare_tickers_are_candidates_and_dot_to_is_not(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "q.csv"
            p.write_text(QH + qt("2026-09-03", "TFI", "QZAX",
                                 "QZALPHA MINES CORP TRANSFER IN "
                                 "INTERACTIVE BROKER", "24", cur="USD")
                         + qt("2026-09-04", "Buy", "QZEE.TO",
                              "QZEPS LTD WE ACTED AS AGENT", "5",
                              act="Trades", price="10", gross="-50",
                              net="-50", cur="CAD"))
            s = LS.scan_questrade([p])
        self.assertEqual(sorted(s.cands), ["QZAX.US"])
        self.assertEqual(s.cands["QZAX.US"].arrivals, [("2026-09-03", 24.0)])
        self.assertEqual(s.currencies["QZEE.TO"], {"CAD"})


# ------------------------------------------------------------ the run

QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
      'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
      'Activity Type,Account Type\n')


def qt(td, action, sym, desc, qty, net='0', act='Transfers', price='0',
       gross='0', comm='0', cur='USD'):
    return (f"{td} 12:00:00 AM,{td} 12:00:00 AM,{action},{sym},{desc},{qty},"
            f"{price},{gross},{comm},{net},{cur},55500001,{act},"  # pii-ok
            f"Individual margin\n")


IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,BrokerName,Interactive Brokers\n'
           'Statement,Data,Title,Activity Statement\n'
           'Statement,Data,Period,"January 1, 2026 - December 31, 2026"\n'
           'Financial Instrument Information,Header,Asset Category,Symbol,'
           'Description,Conid,Security ID,Underlying,Listing Exch,'
           'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n')
IB_TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
               'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
               'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
IB_XFER_H = ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
             'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
             'Market Value,Realized P/L,Cash Amount,Code\n')
# (IB symbol, instrument name, shares) — bought in USD, all moved out.
IB_BOOK = [('QZAA', 'QZALPHA MINES CORP', 24),
           ('QZBB', 'QZBETA PIPELINES CORP', 30),
           ('QZGG', 'QZGAMMA SYSTEMS INC', 12),
           ('QZDD', 'QZDELTA GOLD CORP', 40)]


def ib_statement():
    fii = ''.join(f'Financial Instrument Information,Data,Stocks,{s},'
                  f'"{n}",99900020{i},,,NYSE,1,,,COMMON,,\n'
                  for i, (s, n, _q) in enumerate(IB_BOOK))
    trades = ''.join(f'Trades,Data,Order,Stocks,USD,U5550001,{s},'  # pii-ok
                     f'"2026-03-02, 10:00:00",{n},10,0,{-10 * n},0,0,0,0,O\n'
                     for s, _nm, n in IB_BOOK)
    xfers = ''.join(f'Transfers,Data,Stocks,USD,{s},2026-09-01,ACATS,Out,'
                    f'Other Broker,5550009,{-n},0,{-10 * n},0,0,\n'  # pii-ok
                    for s, _nm, n in IB_BOOK)
    return IB_HEAD + fii + IB_TRADES_H + trades + IB_XFER_H + xfers


def qt_export():
    """The Questrade website export: QZAA arrives as the TSX ticker QZAX
    on a USD row, QZBB as QZBX; QZGG under its own (US) ticker; QZDD as
    QZDX whose name differs (not one company). Later rows of QZAX."""
    return (QH
            + qt('2026-09-03', 'TFI', 'QZAX', 'QZALPHA MINES CORP TRANSFER '
                 'IN INTERACTIVE BROKER', '24')
            + qt('2026-09-03', 'TFI', 'QZBX', 'QZBETA PIPELINES CORP '
                 'TRANSFER IN INTERACTIVE BROKER', '30')
            + qt('2026-09-03', 'TFI', 'QZGG', 'QZGAMMA SYSTEMS INC TRANSFER '
                 'IN INTERACTIVE BROKER', '12')
            + qt('2026-09-03', 'TFI', 'QZDX', 'QZDELTA SILVER CORP TRANSFER '
                 'IN INTERACTIVE BROKER', '40')
            + qt('2026-09-29', 'DIV', 'QZAX', 'QZALPHA MINES CORP CASH DIV ON '
                 '24 SHS REC 09/15/26 PAY 09/29/26', '0', net='6.00',
                 act='Dividends')
            + qt('2026-10-05', 'Sell', 'QZAX', 'QZALPHA MINES CORP WE ACTED '
                 'AS AGENT', '-24', net='288.00', act='Trades', price='12',
                 gross='288'))


def _rates(path: Path, pair: str, rate: str):
    d, lines = date(2025, 12, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 {pair} {rate} boc")
        d += timedelta(days=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _config(country):
    base, src = ("CAD", "USD") if country == "canada" else ("USD", "CAD")
    return (f'[settings]\nyear = 2026\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{src}"]\n'
            f'local_timezone = "America/Toronto"\n'
            + ('option_grant_timing_since = 2026\n' if country == "canada"
               else '') + '\n'
            f'[accounts.ibm]\ntype = "taxable"\n\n'
            f'[accounts.qt]\ntype = "taxable"\n')


def _run(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(root: Path, country: str, ticker_map: str = "") -> None:
    (root / "taxjson.toml").write_text(_config(country))
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map)
    (root / "inputs" / "ibm").mkdir(parents=True)
    (root / "inputs" / "qt").mkdir(parents=True)
    (root / "inputs" / "ibm" / "U5550001_2026.csv").write_text(  # pii-ok
        ib_statement())
    (root / "inputs" / "qt" / "55500001.csv").write_text(  # pii-ok
        qt_export())
    if country == "canada":
        _rates(root / "work" / "to_base.csv", "USD CAD", "1.2500")
    else:
        _rates(root / "work" / "to_base.csv", "CAD USD", "0.8000")


class _RunBase:
    COUNTRY = "canada"
    MAP = ""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        _project(cls.root, cls.COUNTRY, cls.MAP)
        cls.r = _run(cls.root, "run", "--no-input")
        cls.out = " ".join((cls.r.stdout + cls.r.stderr).split())

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def symbols(self, name):
        work = self.root / "work"
        book = json.loads((work / f"{name}.json").read_text())
        return {(t['action'], t['symbol']) for t in book['transactions']}

    def check_corrected(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        books = self.symbols("qt_questrade")
        moved = self.symbols("qt_questrade_transfers")
        # Every row of QZAX: the transfer-in, the dividend, the sale.
        self.assertIn(("TRANSFER", "QZAX.TO"), moved)
        self.assertIn(("DIVIDEND", "QZAX.TO"), books)
        self.assertIn(("BUYSELL", "QZAX.TO"), books)
        self.assertFalse({s for _a, s in books | moved} & {"QZAX.US"})
        # A genuine US ticker (the same as IB's) stays .US; a different
        # name is not one company.
        self.assertIn(("TRANSFER", "QZGG.US"), moved)
        self.assertIn(("TRANSFER", "QZDX.US"), moved)
        st = LS.read_state(LS.state_path(self.root / "work", "qt",
                                         "questrade"))
        return st


@rule("CA-XLIST-02")
class TestRunCanada(_RunBase, unittest.TestCase):
    def test_symbols_are_read_as_the_tsx_listing(self):
        st = self.check_corrected()
        self.assertEqual({k: v["symbol"] for k, v in st["corrected"].items()},
                         {"QZAX.US": "QZAX.TO", "QZBX.US": "QZBX.TO"})

    def test_the_join_names_the_corrected_listing(self):
        self.assertIn("qt: joined as one security by their transfer "
                      "journal", self.out)
        self.assertIn("QZAA.US ↔ QZAX.TO (transfer 2026-09-01; QZAX.US read "
                      "as QZAX.TO: Questrade files the TSX listing on a USD "
                      "row)", self.out)
        self.assertNotIn("↔ QZAX.US", self.out)
        eff = (self.root / "work" / "ticker.map.effective").read_text()
        self.assertIn("TOBASE QZAA.US QZAX.TO", eff)

    def test_the_join_books_one_security(self):
        # Bought at IB as QZAA, sold at Questrade as QZAX: one pool.
        doc = json.loads((self.root / "work" /
                          "qt_gains_wash.json").read_text())
        self.assertAlmostEqual(doc["summary"]["total_gain"],
                               (288 - 240) * 1.25, places=2)

    def test_suggest_shows_the_explicit_lines(self):
        r = _run(self.root, "ticker-map", "--suggest")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("GLOBAL QZAX.US QZAX.TO", r.stdout)
        self.assertIn("TOBASE QZAA.US QZAX.TO", r.stdout)


@rule("US-XLIST-02")
class TestRunUsa(_RunBase, unittest.TestCase):
    COUNTRY = "usa"

    def test_symbols_are_read_as_the_tsx_listing(self):
        st = self.check_corrected()
        self.assertEqual(st["corrected"]["QZAX.US"]["symbol"], "QZAX.TO")
        # The join pools onto the base currency's listing.
        eff = (self.root / "work" / "ticker.map.effective").read_text()
        self.assertIn("TOBASE QZAX.TO QZAA.US", eff)


@rule("CA-XLIST-02")
class TestRunMapWins(_RunBase, unittest.TestCase):
    MAP = "DISTINCT QZBX.US QZBX.TO\n"

    def test_a_distinct_line_keeps_the_row_currency_listing(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        moved = self.symbols("qt_questrade_transfers")
        self.assertIn(("TRANSFER", "QZBX.US"), moved)
        self.assertIn(("TRANSFER", "QZAX.TO"), moved)


@rule("CA-XLIST-02")
class TestRunAccountOrder(_RunBase, unittest.TestCase):
    """The Questrade account comes first in taxjson.toml: its evidence
    (the IB account) is parsed after it, and the run reads it again."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        _project(cls.root, "canada")
        cfg = (cls.root / "taxjson.toml").read_text()
        head, accts = cfg.split("[accounts.ibm]")
        (cls.root / "taxjson.toml").write_text(
            head + '[accounts.qt]\ntype = "taxable"\n\n[accounts.ibm]'
            + accts.split("[accounts.qt]")[0])
        cls.r = _run(cls.root, "run", "--no-input")
        cls.out = " ".join((cls.r.stdout + cls.r.stderr).split())

    def test_order_does_not_matter(self):
        st = self.check_corrected()
        self.assertEqual(set(st["corrected"]), {"QZAX.US", "QZBX.US"})
        self.assertIn("QZAA.US ↔ QZAX.TO", self.out)


RBC_H = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
         '"Price","Settlement Date","Account","Value","Currency",'
         '"Description"\n')


def _rbc(d, act, sym, symdesc, qty, value, cur, desc, price=''):
    return (f'"{d} 00:00:00","{act}","{sym}","{symdesc}","{qty}",'
            f'"{price}","{d} 00:00:00","55500002","{value}","{cur}",'  # pii-ok
            f'"{desc}"\n')


@rule("CA-XLIST-02")
class TestRunRbc(unittest.TestCase):
    """RBC writes every ticker bare too: the same evidence rule."""

    def test_rbc_usd_row_of_the_tsx_ticker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root, "canada")
            (root / "inputs" / "qt" / "55500001.csv").unlink()  # pii-ok
            (root / "inputs" / "qt" / "rbc.csv").write_text(
                RBC_H
                + _rbc('2026-09-03', 'Transfers', 'QZAX',
                       'QZALPHA MINES CORP', '24', '0', 'USD',
                       'TRANSFER IN')
                + _rbc('2026-10-05', 'Sell', 'QZAX', 'QZALPHA MINES CORP',
                       '-24', '288', 'USD', 'SOLD 24 QZALPHA MINES CORP',
                       price='12'))
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            st = LS.read_state(LS.state_path(root / "work", "qt",
                                             "rbc_direct"))
            self.assertEqual(st["corrected"]["QZAX.US"]["symbol"], "QZAX.TO")
            book = json.loads((root / "work" /
                               "qt_rbc_direct.json").read_text())
            self.assertEqual({t["symbol"] for t in book["transactions"]
                              if t["action"] == "BUYSELL"}, {"QZAX.TO"})


if __name__ == "__main__":
    unittest.main()
