"""A correct ticker.map TOBASE line keeps a custody-transfer pair together
(tax-logic CA-XLIST-01/02, US-XLIST-01/02).

Owner report (a new-user project): IB moves a US listing out; Questrade's
website export books the arrival under the company's TSX root on a USD
row. With the right map line `TOBASE <US listing> <TSX listing>` the
out-leg became the TSX listing while the in-leg stayed ROOT.US (the
listing-suffix stage kept the row currency's listing because the account
also holds ROOT.TO in CAD, and the cross-listing join refused the pair
because the map names the out-leg): a phantom disposition at fair value
and a phantom ROOT.US position, nothing on the console. The map's
renames now apply to the out-leg before both checks: an out-leg the map
books as the in-leg's other listing reads the in-leg as that listing and
the two legs pair. A pair the map refuses that leaves a transfer leg
unpaired is a console Warning naming both legs and the line that
settles it (`--strict` stops).

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

from _style import CapturedWidth
from tax_rules import rule
from taxjson.lib import cross_listings as XL
from taxjson.lib import listing_suffix as LS
from taxjson.lib.symbol_codes import exact_name

REPO = Path(__file__).resolve().parent.parent

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


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
NAME = "QZALPHA MINES CORP"


def ib_statement():
    """IB holds the company's TSX listing QZAA in CAD and its NYSE
    listing QZAB in USD (another ticker), and moves the 24 QZAB out."""
    fii = (f'Financial Instrument Information,Data,Stocks,QZAA,"{NAME}",'
           f'999000201,,,TSE,1,,,COMMON,,\n'
           f'Financial Instrument Information,Data,Stocks,QZAB,"{NAME}",'
           f'999000202,,,NYSE,1,,,COMMON,,\n')
    trades = ('Trades,Data,Order,Stocks,CAD,U5550001,QZAA,'  # pii-ok
              '"2026-04-01, 10:00:00",10,20,0,-200,0,0,0,0,O\n'
              'Trades,Data,Order,Stocks,USD,U5550001,QZAB,'  # pii-ok
              '"2026-03-02, 10:00:00",24,12.5,0,-300,0,0,0,0,O\n')
    xfers = ('Transfers,Data,Stocks,USD,QZAB,2026-09-01,ACATS,Out,'
             'Other Broker,5550009,-24,0,-300,0,0,\n')  # pii-ok
    return IB_HEAD + fii + IB_TRADES_H + trades + IB_XFER_H + xfers


def qt_export(sale=True):
    """Questrade's website export: the 24 QZAB arrive as the TSX root
    QZAA on a USD row, and are sold there later."""
    return (QH
            + qt('2026-09-03', 'TFI', 'QZAA', f'{NAME} TRANSFER IN '
                 'INTERACTIVE BROKER', '24')
            + (qt('2026-10-05', 'Sell', 'QZAA', f'{NAME} WE ACTED AS '
                  'AGENT', '-24', net='450.00', act='Trades', price='18.75',
                  gross='450') if sale else ''))


def _rates(path: Path, pair: str, rate: str):
    d, lines = date(2025, 12, 1), []
    while d <= date(2026, 12, 31):
        lines.append(f"{d.isoformat()} 12:00:00 {pair} {rate} boc")
        d += timedelta(days=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _config(country, kind):
    base, src = ("CAD", "USD") if country == "canada" else ("USD", "CAD")
    return (f'[settings]\nyear = 2026\ncountry = "{country}"\n'
            f'base_currency = "{base}"\nsource_currencies = ["{src}"]\n'
            f'local_timezone = "America/Toronto"\n'
            + ('option_grant_timing_since = 2026\n' if country == "canada"
               else '') + '\n'
            f'[accounts.acct]\ntype = "{kind}"\n')


def _run(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(root: Path, country: str, kind: str, ticker_map: str,
             sale: bool = True) -> None:
    (root / "taxjson.toml").write_text(_config(country, kind))
    if ticker_map:
        (root / "ticker.map").write_text(ticker_map)
    acct = root / "inputs" / "acct"
    acct.mkdir(parents=True)
    (acct / "U5550001_2026.csv").write_text(ib_statement())  # pii-ok
    (acct / "55500001.csv").write_text(qt_export(sale))  # pii-ok
    if country == "canada":
        _rates(root / "work" / "to_base.csv", "USD CAD", "1.2500")
    else:
        _rates(root / "work" / "to_base.csv", "CAD USD", "0.8000")


# ------------------------------------------------------------ unit level

class TestResolveThroughTheMap(unittest.TestCase):
    """lib/listing_suffix.resolve reads the out-leg through the map."""
    ARR = [("2026-09-03", 24.0)]

    def _scan(self, mixed=True):
        s = LS.Scan()
        c = s.candidate("QZAA.US", "USD")
        c.add_name(NAME)
        c.arrivals += self.ARR
        s.saw("QZAA.US", "USD", NAME)
        ev = LS.Evidence()
        ev.outs.append(LS.OutLeg("acct", "ib", "QZAB.US", "2026-09-01",
                                 24.0, {exact_name(NAME)}))
        for sym, cur in (("QZAB.US", "USD"),) + (
                (("QZAA.TO", "CAD"),) if mixed else ()):
            ev.named.setdefault(sym, set()).add(exact_name(NAME))
            ev.seen.setdefault(sym, []).append(
                ("acct", "ib", cur, frozenset([exact_name(NAME)])))
        return s, ev

    def _res(self, mixed, renames):
        s, ev = self._scan(mixed)
        named = set(renames) | set(renames.values())
        return LS.resolve(s, ev, account="acct", broker="questrade",
                          mapped=lambda x: x in named, renames=renames)

    @rule("CA-XLIST-02")
    def test_the_map_books_the_out_leg_as_the_other_listing(self):
        # The account holds QZAA.TO in CAD: the native rows keep QZAA.US
        # and the base-currency books join it (no conditional line).
        r = self._res(True, {"QZAB.US": "QZAA.TO"})
        self.assertEqual(r["corrected"], {})
        k = r["kept"]["QZAA.US"]
        self.assertEqual((k["line"], k["map"]),
                         ("TOBASE QZAA.US QZAA.TO", "QZAA.TO"))
        self.assertIn("which ticker.map books as QZAA.TO", k["reason"])
        # Without the other listing in the account: corrected as before.
        r = self._res(False, {"QZAB.US": "QZAA.TO"})
        got = r["corrected"]["QZAA.US"]
        self.assertEqual((got["symbol"], got["pair"]["map"]),
                         ("QZAA.TO", "QZAA.TO"))

    @rule("CA-XLIST-02")
    def test_the_map_books_the_out_leg_as_the_in_legs_own_listing(self):
        # `GLOBAL QZAB.US QZAA.US`: a custody move of QZAA.US — never
        # read as the TSX listing.
        r = self._res(False, {"QZAB.US": "QZAA.US"})
        self.assertEqual(r, {"corrected": {}, "kept": {}})

    @rule("CA-XLIST-02")
    def test_an_unrelated_target_reads_as_without_the_map(self):
        # The map books the out-leg as neither listing: the out-leg's
        # own symbol is the evidence, as with no map line (the pair is
        # then a Warning of the cross-listing stage, map_split).
        r = self._res(True, {"QZAB.US": "QZZZ.TO"})
        self.assertEqual(r, self._res(True, {}))
        self.assertNotIn("map", r["kept"]["QZAA.US"])

    @rule("CA-XLIST-02")
    def test_a_ticker_change_of_the_other_listing(self):
        # The TSX listing itself moves out, renamed by the map: the
        # in-leg is the TSX listing (the map renames it on).
        s, ev = self._scan(False)
        ev.outs[0].symbol = "QZAA.TO"
        r = LS.resolve(s, ev, account="acct", broker="questrade",
                       mapped=lambda x: x in ("QZAA.TO", "QZNEW.TO"),
                       renames={"QZAA.TO": "QZNEW.TO"})
        got = r["corrected"]["QZAA.US"]
        self.assertEqual((got["symbol"], "map" in got["pair"]),
                         ("QZAA.TO", False))


def _legs():
    o = XL.Leg("acct", "ib", "QZAB.US", "2026-09-01", -24.0,
               currency="USD", name=exact_name(NAME), raw_name=NAME)
    i = XL.Leg("acct", "questrade", "QZAA.US", "2026-09-03", 24.0,
               currency="USD", name=exact_name(NAME), raw_name=NAME)
    names = {"QZAB.US": {exact_name(NAME)}, "QZAA.US": {exact_name(NAME)},
             "QZAA.TO": {exact_name(NAME)}}
    return [o, i], names, {exact_name(NAME): NAME}


def _analyze(renames, distinct=()):
    legs, names, shown = _legs()
    refused = []
    named = set(renames) | set(renames.values())
    r = XL.analyze(legs, names, shown, map_named=named,
                   map_distinct=distinct, base_currency="CAD",
                   refused=refused, map_renames=renames)
    return r, refused, XL.map_split(refused, named, renames)


class TestAnalyzeThroughTheMap(unittest.TestCase):
    """lib/cross_listings.analyze reads a pair the map decides through
    the map's renames."""

    @rule("CA-XLIST-01")
    def test_the_in_leg_joins_its_other_listing(self):
        r, refused, split = _analyze({"QZAB.US": "QZAA.TO"})
        self.assertEqual([(p.frm, p.to, p.extra.get("map"))
                          for p in r["joined"]],
                         [("QZAA.US", "QZAA.TO", "QZAA.TO")])
        self.assertEqual((refused, split), ([], []))
        self.assertEqual(r["joined"][0].record()["map"], "QZAA.TO")
        # The other direction (a US project's base): the map books the
        # TSX listing as the out-leg's symbol.
        r, refused, split = _analyze({"QZAA.TO": "QZAB.US"})
        self.assertEqual([(p.frm, p.to) for p in r["joined"]],
                         [("QZAA.US", "QZAA.TO")])

    @rule("CA-XLIST-01")
    def test_distinct_keeps_them_apart(self):
        r, refused, split = _analyze({"QZAB.US": "QZAA.TO"},
                                     [("QZAA.US", "QZAA.TO")])
        self.assertEqual(r["joined"], [])

    @rule("CA-XLIST-01")
    def test_an_unrelated_listing_is_refused_and_named(self):
        r, refused, split = _analyze({"QZAB.US": "QZZZ.TO"})
        self.assertEqual(r["joined"], [])
        self.assertEqual([p.extra["refused"] for p in refused], ["map"])
        self.assertEqual([(eo, ei, line) for _p, eo, ei, line in split],
                         [("QZZZ.TO", "QZAA.US", "TOBASE QZAA.US QZZZ.TO")])
        head, details = XL.map_split_note("acct", split)
        self.assertIn("24 QZAB.US out 2026-09-01 (booked as QZZZ.TO) / "
                      "QZAA.US in 2026-09-03", head)
        self.assertTrue(any("`TOBASE QZAA.US QZZZ.TO`" in d
                            for d in details))
        self.assertIsNone(XL.map_split_note("other", split))

    @rule("CA-XLIST-01")
    def test_a_map_that_books_both_as_one_says_nothing(self):
        # Both legs named and booked as one symbol: the map pairs them.
        r, refused, split = _analyze({"QZAB.US": "QZAA.TO",
                                      "QZAA.US": "QZAA.TO"})
        self.assertEqual(r["joined"], [])
        self.assertEqual(split, [])


# ------------------------------------------------------------ the run

class _Run:
    COUNTRY = "canada"
    KIND = "taxable"
    MAP = "TOBASE QZAB.US QZAA.TO\n"
    SALE = True

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        _project(cls.root, cls.COUNTRY, cls.KIND, cls.MAP, cls.SALE)
        cls.r = _run(cls.root, "run", "--no-input")
        cls.out = " ".join((cls.r.stdout + cls.r.stderr).split())

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def book_symbols(self, name):
        doc = json.loads((self.root / "work" / f"{name}.json").read_text())
        return {t["symbol"] for t in doc["transactions"]}

    def summary(self):
        r = _run(self.root, "sum", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout)


class _MapBooked(_Run):
    """The repro: the map books IB's QZAB.US as QZAA.TO; Questrade's
    arrival on a USD row joins QZAA.TO (the base-currency books)."""

    def test_the_pair_is_one_security(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        self.assertIn("QZAB.US ↔ QZAA.US (transfer 2026-09-01; ticker.map "
                      "books QZAB.US as QZAA.TO, so QZAA.US joins "
                      "QZAA.TO)", self.out)
        # The base currency's listing is the pool (tobase_direction): the
        # TSX listing in Canada, the US one in a US project (the map's
        # own line chained on).
        keep, other = (("QZAA.TO", "QZAA.US") if self.COUNTRY == "canada"
                       else ("QZAA.US", "QZAA.TO"))
        eff = (self.root / "work" / "ticker.map.effective").read_text()
        self.assertIn(f"TOBASE {other} {keep}", eff)
        self.assertNotIn("Short position", self.out)
        self.assertNotIn("no purchase", self.out)
        self.assertNotIn("two securities", self.out)
        # No phantom second listing anywhere in the base-currency books.
        self.assertEqual(self.book_symbols("acct_gains"), {keep})
        self.assertNotIn(other, (self.root / "reports" /
                                 "acct.sum").read_text())
        # The raw holdings (one native pool per listing) are written.
        self.assertTrue((self.root / "reports" /
                         "acct_holdings.toml").is_file())
        self.assertNotIn("would pool mixed currencies", self.out)


@rule("CA-XLIST-01")
@rule("CA-XLIST-02")
class TestCanadaTaxable(_MapBooked, unittest.TestCase):
    def test_the_sale_is_in_the_total(self):
        # One ACB pool: (10 x 20 + 24 x 12.5 x 1.25) CAD for 34 units.
        tot = self.summary()["filing"]["totals"]
        self.assertAlmostEqual(tot["proceeds"], 450 * 1.25, places=2)
        self.assertAlmostEqual(tot["gain"],
                               450 * 1.25 - 24 * (200 + 375) / 34,
                               places=2)


@rule("CA-XLIST-01")
@rule("CA-XLIST-02")
class TestCanadaSheltered(_MapBooked, unittest.TestCase):
    KIND = "sheltered"

    def test_ten_units_left(self):
        held = (self.root / "reports" / "acct.sum").read_text()
        self.assertRegex(held, r"QZAA\.TO\s+10\s")


@rule("US-XLIST-01")
@rule("US-XLIST-02")
class TestUsaTaxable(_MapBooked, unittest.TestCase):
    COUNTRY = "usa"

    def test_the_sale_is_in_the_total(self):
        # FIFO: the 24 units moved (bought first, USD 300) are sold.
        tot = self.summary()["filing"]["totals"]
        self.assertAlmostEqual(tot["proceeds"], 450, places=2)
        self.assertAlmostEqual(tot["gain"], 150, places=2)


@rule("US-XLIST-01")
@rule("US-XLIST-02")
class TestUsaSheltered(_MapBooked, unittest.TestCase):
    COUNTRY = "usa"
    KIND = "sheltered"


@rule("CA-XLIST-01")
class TestNoMapLineStillJoins(_Run, unittest.TestCase):
    MAP = ""

    def test_the_join_pairs_the_legs(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        self.assertIn("joined as one security by their transfer journal: "
                      "QZAB.US ↔ QZAA.US (transfer 2026-09-01)", self.out)
        self.assertNotIn("two securities", self.out)
        tot = self.summary()["filing"]["totals"]
        self.assertAlmostEqual(tot["gain"], (450 - 300) * 1.25, places=2)


class _Unrelated(_Run):
    """A map line naming an unrelated listing: the pair stays refused,
    and the split is a console Warning (--strict stops)."""
    MAP = "TOBASE QZAB.US QZZZ.TO\n"

    def test_refused_with_a_warning(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        self.assertIn("warning: acct: ticker.map books the two legs of a "
                      "transfer as two securities: 24 QZAB.US out "
                      "2026-09-01 (booked as QZZZ.TO) / QZAA.US in "
                      "2026-09-03", self.out)
        self.assertIn("add `TOBASE QZAA.US QZZZ.TO` to ticker.map (or "
                      "correct the line naming QZAB.US)", self.out)
        st = XL.read_state(self.root / "work" / XL.STATE)
        self.assertEqual([r["refused"] for r in st["refused"]], ["map"])
        self.assertEqual(st["joined"], [])

    def test_strict_stops(self):
        r = _run(self.root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--strict: acct: ticker.map books the two legs of a "
                      "transfer as two securities",
                      " ".join(r.stderr.split()))


@rule("CA-XLIST-01")
class TestUnrelatedCanada(_Unrelated, unittest.TestCase):
    pass


@rule("US-XLIST-01")
class TestUnrelatedUsa(_Unrelated, unittest.TestCase):
    COUNTRY = "usa"


# ------------------------------------------------------------ IB names

def ib_two_companies(first_tsx=True):
    """One IB statement listing two companies under the bare symbol QZE:
    QZENERGY LTD on the TSX (a CA ISIN) and QZEQUITY INC on the NYSE (a
    US ISIN), each traded in its own currency."""
    tsx = ('Financial Instrument Information,Data,Stocks,QZE,'
           '"QZENERGY LTD",999000301,CA9990003011,,TSE,1,,,COMMON,,\n')
    nyse = ('Financial Instrument Information,Data,Stocks,QZE,'
            '"QZEQUITY INC",999000302,US9990003021,,NYSE,1,,,COMMON,,\n')
    trades = ('Trades,Data,Order,Stocks,CAD,U5550001,QZE,'  # pii-ok
              '"2026-03-02, 10:00:00",10,20,0,-200,0,0,0,0,O\n'
              'Trades,Data,Order,Stocks,USD,U5550001,QZE,'  # pii-ok
              '"2026-03-03, 10:00:00",5,40,0,-200,0,0,0,0,O\n')
    return (IB_HEAD + (tsx + nyse if first_tsx else nyse + tsx)
            + IB_TRADES_H + trades)


class TestIbNamesFollowTheListing(unittest.TestCase):
    """Two companies under one bare IB symbol: each row's name is the
    instrument of the listing it is booked as, never the first listed
    (owner report: a TSX and an NYSE company sharing a ticker read as
    one listing with two names, a MAP-GAP in `taxjson scan`, now
    `tips`)."""

    def _names(self, first_tsx):
        from taxjson.lib.brokerages.ib_extractor import IbBrokerage
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "U5550001_2026.csv"  # pii-ok
            p.write_text(ib_two_companies(first_tsx))
            rows = IbBrokerage().parse_file(p)
        return {(t["symbol"], t.get("security_name")) for t in rows
                if t.get("action") == "BUYSELL"}

    def test_each_listing_carries_its_own_company(self):
        for first_tsx in (True, False):
            with self.subTest(first_tsx=first_tsx):
                self.assertEqual(self._names(first_tsx),
                                 {("QZE.TO", "QZENERGY LTD"),
                                  ("QZE.US", "QZEQUITY INC")})

    def test_shown_apart_and_no_map_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(_config("canada", "taxable"))
            (root / "inputs" / "acct").mkdir(parents=True)
            (root / "inputs" / "acct" / "U5550001_2026.csv").write_text(  # pii-ok
                ib_two_companies(False))
            _rates(root / "work" / "to_base.csv", "USD CAD", "1.2500")
            r = _run(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            _legs, names, _shown = XL.gather(root / "work", ["acct"])
            self.assertEqual(names["QZE.TO"], {exact_name("QZENERGY LTD")})
            self.assertEqual(names["QZE.US"], {exact_name("QZEQUITY INC")})
            self.assertEqual(XL.shown_apart("QZE.US", "QZE.TO", names),
                             XL.DIFFERENT)
            r = _run(root, "tips")
            self.assertNotIn("MAP-GAP", r.stdout, r.stdout)


if __name__ == "__main__":
    unittest.main()
