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

Formerly: test_fix_qt_listing_suffix
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import listing_suffix as LS
from taxjson.lib.symbol_codes import exact_name
from _support.symbols_listing_suffix import (
    _scan, _ev, _res, QH, qt, _run, _project, _RunBase, RBC_H, _rbc,
)
from tax_rules import rule
from taxjson.rules import ids as R


# ------------------------------------------------------------ unit level


@rule(R.CA_XLIST_02)
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
        # Arrived by a transfer nothing in the books pairs with.
        s = _scan(QZAX_US=("USD", "QZALPHA MINES CORP", self.ARR))
        r = _res(s, _ev(seen=[("QZAX.TO", "tfsa", "questrade", "CAD",
                               "QZALPHA MINES CORP")]))
        self.assertEqual(r["corrected"]["QZAX.US"]["how"], "listing")
        r = _res(s, _ev(seen=[("QZAX.TO", "tfsa", "questrade", "CAD",
                               "QZOTHER HOLDINGS INC")]))
        self.assertEqual(r["corrected"], {})

    def test_the_same_broker_proved_it_in_another_account(self):
        # Another Questrade account read QZAX.US as QZAX.TO on its own
        # evidence, under the same name (GitHub issue #3): shares bought
        # here on a USD row are the TSX listing too.
        s = _scan(QZAX_US=("USD", "QZALPHA MINES CORP", ()))
        nm = frozenset([exact_name("QZALPHA MINES CORP")])
        ev = _ev()
        ev.proved["QZAX.US"] = [("tfsa", "questrade", "QZAX.TO", nm)]
        got = _res(s, ev)["corrected"]["QZAX.US"]
        self.assertEqual((got["symbol"], got["how"]), ("QZAX.TO", "broker"))
        self.assertIn("account tfsa's QZAX.US was read as QZAX.TO",
                      got["evidence"])
        # Another broker's proof, or another name, is not evidence here.
        for proof in (("tfsa", "rbc_direct", "QZAX.TO", nm),
                      ("tfsa", "questrade", "QZAX.TO",
                       frozenset([exact_name("QZOTHER HOLDINGS INC")]))):
            ev.proved["QZAX.US"] = [proof]
            self.assertEqual(_res(s, ev)["corrected"], {}, proof)

    def test_shares_bought_on_a_usd_row_keep_the_us_listing(self):
        # The same ticker on the NYSE and the TSX (an interlisted
        # company): a USD trade of the bare ticker is the US listing.
        s = _scan(QZAX_US=("USD", "QZALPHA MINES CORP", ()))
        r = _res(s, _ev(seen=[("QZAX.TO", "tfsa", "questrade", "CAD",
                               "QZALPHA MINES CORP")]))
        self.assertEqual(r, {"corrected": {}, "kept": {}})


@rule(R.CA_XLIST_02)
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


@rule(R.CA_XLIST_02)
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


@rule(R.US_XLIST_02)
class TestRunUsa(_RunBase, unittest.TestCase):
    COUNTRY = "usa"

    def test_symbols_are_read_as_the_tsx_listing(self):
        st = self.check_corrected()
        self.assertEqual(st["corrected"]["QZAX.US"]["symbol"], "QZAX.TO")
        # The join pools onto the base currency's listing.
        eff = (self.root / "work" / "ticker.map.effective").read_text()
        self.assertIn("TOBASE QZAX.TO QZAA.US", eff)


@rule(R.CA_XLIST_02)
class TestRunMapWins(_RunBase, unittest.TestCase):
    MAP = "DISTINCT QZBX.US QZBX.TO\n"

    def test_a_distinct_line_keeps_the_row_currency_listing(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        moved = self.symbols("qt_questrade_transfers")
        self.assertIn(("TRANSFER", "QZBX.US"), moved)
        self.assertIn(("TRANSFER", "QZAX.TO"), moved)


@rule(R.CA_XLIST_02)
class TestRunAccountOrder(_RunBase, unittest.TestCase):
    """The Questrade account comes first in taxjson.toml: its evidence
    (the IB account) is parsed after it, and the run reads it again."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        _project(cls.root, "canada", cleanup=cls.addClassCleanup)
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


@rule(R.CA_XLIST_02)
class TestRunProvedByTheSameBroker(_RunBase, unittest.TestCase):
    """A second Questrade account with no transfer of its own reinvests a
    dividend in QZAX on a USD row (REI) and sells the unit there: the
    proof the qt account's transfer gave (Questrade files QZAX's TSX
    listing on USD rows) reads its rows as QZAX.TO too (GitHub
    issue #3). A purchase on a USD trade row is not re-read
    (TestRunBoughtKeepsTheUsListing)."""
    LIRA = (qt("2026-09-29", "REI", "QZAX", "QZALPHA MINES CORP "
               "REINV@U$10.00 REC 09/15/26 PAY 09/29/26", "1",
               net="-10.00", act="Dividend reinvestment")
            + qt("2026-10-05", "Sell", "QZAX", "QZALPHA MINES CORP WE "
                 "ACTED AS AGENT", "-1", net="11.00", act="Trades",
                 price="11", gross="11"))

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        _project(cls.root, "canada", cleanup=cls.addClassCleanup)
        # The second account comes FIRST: its evidence is parsed later.
        cfg = (cls.root / "taxjson.toml").read_text()
        head, accts = cfg.split("[accounts.ibm]")
        (cls.root / "taxjson.toml").write_text(
            head + '[accounts.lira]\ntype = "sheltered"\n\n[accounts.ibm]'
            + accts)
        (cls.root / "inputs" / "lira").mkdir(parents=True)
        (cls.root / "inputs" / "lira" / "q.csv").write_text(QH + cls.LIRA)
        cls.r = _run(cls.root, "run", "--no-input")
        cls.out = " ".join((cls.r.stdout + cls.r.stderr).split())

    def test_rows_read_as_the_tsx_listing(self):
        self.check_corrected()
        st = LS.read_state(LS.state_path(self.root / "work", "lira",
                                         "questrade"))
        got = st["corrected"]["QZAX.US"]
        self.assertEqual((got["symbol"], got["how"]), ("QZAX.TO", "broker"))
        book = self.symbols("lira_questrade")
        self.assertEqual({s for a, s in book if a == "BUYSELL"}, {"QZAX.TO"})
        self.assertNotIn("Short position", self.out)


@rule(R.CA_XLIST_02)
class TestRunBoughtKeepsTheUsListing(TestRunProvedByTheSameBroker):
    """The second account BUYS QZAX on a USD trade row: the US listing
    (a USD purchase of the bare ticker; many Canadian companies trade
    under the same ticker on the NYSE) — the other account's proof does
    not re-read it (v0.24.1 review L8)."""
    LIRA = (qt("2026-09-10", "Buy", "QZAX", "QZALPHA MINES CORP WE "
               "ACTED AS AGENT", "10", net="-100.00", act="Trades",
               price="10", gross="-100")
            + qt("2026-10-05", "Sell", "QZAX", "QZALPHA MINES CORP WE "
                 "ACTED AS AGENT", "-10", net="110.00", act="Trades",
                 price="11", gross="110"))
    test_rows_read_as_the_tsx_listing = None    # the parent's verdict

    def test_a_purchase_keeps_the_us_listing(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        st = LS.read_state(LS.state_path(self.root / "work", "lira",
                                         "questrade"))
        self.assertNotIn("QZAX.US", st["corrected"])
        book = self.symbols("lira_questrade")
        self.assertEqual({s for a, s in book if a == "BUYSELL"}, {"QZAX.US"})


@rule(R.CA_XLIST_02)
class TestRunRbc(unittest.TestCase):
    """RBC writes every ticker bare too: the same evidence rule."""

    def test_rbc_usd_row_of_the_tsx_ticker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root, "canada", cleanup=self.addCleanup)
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
