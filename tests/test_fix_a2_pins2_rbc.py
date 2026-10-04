"""Mutation pins for re-audit-2 test-gap findings (tests-pins-04/05/06):
RBC and Webull parser dates and pairing, the MXN settlement cutover, the
phantom-relevance split de-duplication and the config checks.

Each test fails when the finding's surviving mutant is applied. All data
is synthetic: fake tickers and a fake broker account id."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.lib.core import TaxTransaction
from test_fix_rbcqt import rbc_parse, rrow

REPO_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ RBC
class TestRbcExpiryAndSettleDates(unittest.TestCase):

    def test_a2_0528_warrant_expiry_posted_in_january_books_dec_31(self):
        """S065-04: RBC posts a right/warrant expiry the next business
        day; the row is booked on the expiry date the description names,
        so a Dec-31 expiry's loss stays in its year."""
        txs, _, _ = rbc_parse(rrow(
            "January 3, 2028", "Reorganization", "QZW.WT", "QZW CORP WTS",
            "-100", "", "0", "CAD",
            "EXP - WTS QZW CORP AS OF 12/31/27 EXPIRED",
            settle="January 3, 2028"))
        self.assertEqual(len(txs), 1, txs)
        self.assertEqual((txs[0]['date'], txs[0]['date_settle']),
                         ('2027-12-31', '2027-12-31'))

    @rule("CA-DATE-04")
    def test_a2_0529_blank_settle_option_sale_is_t_plus_1(self):
        """R1-194: an option settles T+1 in every era — a blank-settle
        RBC option sale on 2023-12-28 (equities T+2 then) settles
        2023-12-29, not in 2024."""
        txs, _, _ = rbc_parse(rrow(
            "December 28, 2023", "Sell", "8QZQZQ2", "", "-2", "1.10",
            "218.00", "CAD",
            "CALL .QZB   06/21/24    30 QZB INC CLOSE CONTRACT", settle=""))
        self.assertEqual(len(txs), 1, txs)
        self.assertEqual(txs[0]['date'], '2023-12-28')
        self.assertEqual(txs[0]['date_settle'], '2023-12-29')


class TestRbcOneDateExportOrder(unittest.TestCase):

    @rule("CA-DATE-14")
    def test_a2_0879_single_date_export_is_read_bottom_up(self):
        """A one-day RBC export is newest-first like every RBC export:
        the rebuy listed ABOVE the sell happened after it, so the sale is
        made from the shares held before the rebuy."""
        buy0 = rrow("January 15, 2025", "Buy", "QZK", "QZK CORP", "100",
                    "10", "-1009.95", "CAD", "QZK CORP UNSOLICITED DA")
        sell = rrow("January 15, 2025", "Sell", "QZK", "QZK CORP", "-100",
                    "9", "890.05", "CAD", "QZK CORP UNSOLICITED DA")
        rebuy = rrow("January 15, 2025", "Buy", "QZK", "QZK CORP", "100",
                     "9.50", "-959.95", "CAD", "QZK CORP UNSOLICITED DA")
        # newest-first: rebuy, sell, first buy (bottom = earliest)
        txs, err, _ = rbc_parse(rebuy + sell + buy0)
        seq = [t['quantity'] for t in sorted(
            txs, key=lambda t: (t['date'], t['time']))]
        self.assertEqual(seq, [100.0, -100.0, 100.0], err)
        by_qty_time = sorted((t['time'], t['quantity'], t['price'])
                             for t in txs)
        # the 9.50 rebuy is the LAST row of the day
        self.assertEqual(by_qty_time[-1][1:], (100.0, 9.5))


class TestRbcCrossFileCashInLieu(unittest.TestCase):

    def test_a2_0898_merger_cil_in_next_years_file_pairs(self):
        """S064-14: a December MER (ROC + .947213 new per old) whose cash
        in lieu posts in January's export is one event: the stated ratio
        and the fractional sale, no UNMATCHED leg."""
        f25 = (rrow("December 15, 2025", "Reorganization", "QMRG",
                    "QMRG CORP NO PAR", "75", "", "0", "CAD",
                    "MGR - QMRG CORP NO PAR SHRS RECEIVED THRU MERGER")
               + rrow("December 15, 2025", "Reorganization", "Q099003",
                      "QMRG CORP NEW", "-80", "", "421.92", "CAD",
                      "MER - QMRG CORP NEW DEFAULT: ROC OF C$5.2740 + "
                      ".947213 NEW SHS PER 1 OLD")
               + rrow("June 9, 2025", "Buy", "QMRG", "QMRG CORP NEW", "80",
                      "168", "-13449.95", "CAD", "QMRG UNSOLICITED DA"))
        f26 = rrow("January 9, 2026", "Reorganization", "QMRG",
                   "QMRG CORP NO PAR", "", "", "41.30", "CAD",
                   "CIL - QMRG CORP NO PAR CASH IN LIEU OF FRAC SHARES")
        txs, err, _ = rbc_parse(f25, f26)
        self.assertNotIn('UNMATCHED', err)
        split = [t for t in txs if t['action'] == 'SPLIT']
        self.assertEqual(len(split), 1, txs)
        self.assertAlmostEqual(split[0]['quantity'], 0.947213, places=6)
        cil = [t for t in txs if t['action'] == 'BUYSELL'
               and t['quantity'] < 0]
        self.assertEqual(len(cil), 1, txs)
        self.assertEqual(cil[0]['date'], '2026-01-09')
        self.assertAlmostEqual(cil[0]['quantity'], -0.77704, places=5)
        self.assertAlmostEqual(cil[0]['net_amount'], 41.3, places=2)


class TestRbcMergerNote(unittest.TestCase):

    def test_a2_0899_merger_note_is_country_neutral(self):
        """S064-20: the merger note neither cites Canada nor tells the
        user to enter the shares through a .tt file (the corp-actions
        stage books them — a .tt row double-books)."""
        body = (rrow("March 3, 2025", "Buy", "QZH", "QZH CORPORATION",
                     "15", "100", "-1507.77", "USD", "QZH UNSOLICITED")
                + rrow("July 1, 2025", "Reorganization", "X000004",
                       "QZH CORPORATION", "-15", "", "0", "USD",
                       "MGR - QZH CORPORATION MERGER TO QZC CORPORATION "
                       "1 NEW = 1 OLD")
                + rrow("July 1, 2025", "Reorganization", "QZC",
                       "QZC CORPORATION", "15", "", "0", "USD",
                       "MGR - QZC CORPORATION SHRS RECEIVED THRU MERGER"))
        _, err, _ = rbc_parse(body)
        self.assertIn('merger row(s)', err)
        note = next(ln for ln in err.splitlines() if 'merger row(s)' in ln)
        self.assertNotIn('canada', note.lower())
        self.assertIn('do not also enter the shares in a .tt file', note)
        self.assertNotIn('enter them manually', note)


class TestRbcReinvestReversalDate(unittest.TestCase):

    @rule("CA-DIST-03")
    def test_a2_1583_reversal_cancels_the_drip_on_or_before_it(self):
        """Two identical DRIPs (April and June) and an April reversal:
        the reversal cancels the April purchase, so the June one is
        booked whatever the export's row order."""
        def rei(d, q, v, tail=""):
            return rrow(d, "Dividends", "QRT.UN", "QUARTZ REIT", q, "", v,
                        "CAD", "REI - QUARTZ REIT REINV@C$27.15" + tail)
        apr = rei("April 13, 2022", "3", "-81.45")
        jun = rei("June 15, 2022", "3", "-81.45")
        cxl = rei("April 20, 2022", "-3", "81.45", " CANCEL")
        for body in (apr + cxl + jun, jun + cxl + apr):
            txs, err, _ = rbc_parse(body)
            self.assertEqual([(t['date'], t['action'], t['quantity'])
                              for t in txs],
                             [('2022-06-15', 'BUYSELL', 3.0)], err)


# --------------------------------------------------------------- Webull
class TestWebullAssignmentWindow(unittest.TestCase):

    def _assign_legs(self, settle):
        from test_fix_m_parsers2_webull import _H25, _PRE, _parse
        txt = (_PRE + _H25 +
               'USD,20-03-2025,BUY,@XYZ,CALL XYZ03/21/25 50,OPC,1,1.00,,'
               '(100.99)\n'
               'USD,21-03-2025,SELL,,,,-1,0.00,,\n'
               f'USD,{settle},BUY,XYZ,XYZ CORP,SHS,100,50.00,,'
               '"(5,001.00)"\n')
        tx, err = _parse(txt)
        return sum(1 for t in tx if t['action'] == 'ASSIGN'), err

    def test_a2_0946_pairing_window_is_seven_days(self):
        """A $1.00-fee trade at the strike settling 7 days after a $0
        option close is its exercise; 8 or more days later it is an
        ordinary trade and the option an expiry."""
        n, err = self._assign_legs('28-03-2025')        # gap 7
        self.assertGreater(n, 0, err)
        for settle in ('29-03-2025', '10-04-2025', '20-04-2025'):
            n, err = self._assign_legs(settle)          # gap 8, 20, 30
            self.assertEqual(n, 0, (settle, err))


# ----------------------------------------------------------------- dates
class TestMexicoT1Cutover(unittest.TestCase):

    @rule("CA-DATE-04")
    def test_a2_1568_mxn_moves_to_t_plus_1_on_2024_05_27(self):
        """Mexico moved to T+1 with Canada on 2024-05-27 (the US a day
        later, after Memorial Day)."""
        from taxjson.lib.dates import settlement_date, settlement_lag_days
        self.assertEqual(settlement_lag_days('2024-05-24', 'MXN'), 2)
        self.assertEqual(settlement_lag_days('2024-05-27', 'MXN'), 1)
        self.assertEqual(settlement_date('2024-05-27', 'MXN'), '2024-05-28')
        self.assertEqual(settlement_lag_days('2024-05-27', 'USD'), 2)


# -------------------------------------------------------------- phantoms
class TestPhantomRelevanceSplitDedup(unittest.TestCase):

    def test_a2_0933_split_booked_by_two_brokers_counts_once(self):
        """S033-14: one account fed by two broker files that both book
        the same 2:1 split. Counted twice, the walk saw a 4:1 split, the
        2026 cover of the carried short vanished and --write-missing-history
        wrote nothing."""
        from taxjson.lib.missing_history import (MissingHistoryCandidate,
                                                  assess_tax_year_relevance)

        def tx(action, d, tm, qty, net=0.0, price=0.0):
            return TaxTransaction(action=action, date=d, time=tm,
                                  symbol='XYZ.US', quantity=qty,
                                  currency='USD', price=price,
                                  net_amount=net, account='margin',
                                  date_settle=d)
        rows = [tx('BUYSELL', '2025-01-06', '10:00:00', 100, -1000, 10),
                tx('SPLIT', '2025-06-11', '00:00:00', 2.0),
                tx('SPLIT', '2025-06-11', '20:25:00', 2.0),
                tx('BUYSELL', '2025-08-04', '10:00:00', -200, 1200, 6),
                tx('BUYSELL', '2025-08-05', '10:00:00', -200, 1200, 6),
                tx('BUYSELL', '2026-03-02', '10:00:00', 50, -350, 7)]
        cand = MissingHistoryCandidate(symbol='XYZ.US', account='margin',
                                currency='USD',
                                first_negative_date='2025-08-05',
                                peak_short=-200.0, end_position=-150.0,
                                disposition_count=1, registered=False)
        r = assess_tax_year_relevance(rows, [cand], 2026)
        self.assertTrue(r[0].affects_year)


# ---------------------------------------------------------- config_check
_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"
"""
_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n"
       "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,ISHARES "
       "COMP,100,10.00,1000.00,9.95,-1009.95,CAD,55500001,Trades,"
       "Individual\n")  # pii-ok (synthetic)


def _project(tmp, config, accounts=("margin",)):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(config)
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "questrade_2025.csv").write_text(_QT)
    return root


def _cli(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _settings(**kw):
    from taxjson.lib.config_check import settings_problems
    cfg = {"settings": dict(country="canada", **kw)}
    return settings_problems(cfg), cfg["settings"]


class TestConfigChecks(unittest.TestCase):

    # S038-07 and siblings: every reserved suffix, written out here so a
    # suffix dropped from config_check fails this test.
    RESERVED = ("_raw", "_base", "_gains", "_wash", "_merged", "_sorted",
                "_filled", "_mapped", "_report", "_tt", "_manifest",
                "_sources")

    def test_a2_1599_every_reserved_suffix_is_refused(self):
        from taxjson.lib.config_check import account_name_problem
        for suf in self.RESERVED:
            with self.subTest(suffix=suf):
                msg = account_name_problem("m" + suf)
                self.assertIn(repr(suf), msg)
                self.assertIn(repr(suf), account_name_problem(
                    "M" + suf.upper()))

    def test_a2_0927_account_named_wash_is_refused_by_run(self):
        """S038-07: an account `m_wash` overwrote account m's wash report
        (reports/m_wash.sum) with rc 0."""
        cfg = _CONFIG + '\n[accounts.m_wash]\ntype = "taxable"\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg, accounts=("margin", "m_wash"))
            r = _cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("m_wash", r.stderr)
        self.assertIn("'_wash'", r.stderr)

    def test_a2_0926_base_currency_is_upper_cased(self):
        """S030-20: base_currency = "cad" converted every CAD fee at the
        default FX rate; the reader canonicalises the spelling."""
        for raw in ("cad", " Cad ", "CAD"):
            probs, s = _settings(base_currency=raw)
            self.assertEqual(probs, [], raw)
            self.assertEqual(s["base_currency"], "CAD", raw)

    def test_a2_0928_settings_must_be_a_table(self):
        """S038-12: `settings = "x"` is a one-line error, not an
        AttributeError traceback."""
        from taxjson.lib.config_check import settings_problems
        self.assertEqual(settings_problems({"settings": "x"}),
                         ["[settings] must be a table"])
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, 'settings = "x"\n\n[accounts.margin]\n'
                                 'type = "taxable"\n')
            r = _cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[settings] must be a table", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a2_1598_base_currency_must_be_three_letters(self):
        for bad in (5, "CADX", "C$", ""):
            with self.subTest(base=bad):
                probs, _ = _settings(base_currency=bad)
                self.assertTrue(any("3-letter" in p for p in probs), probs)
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CONFIG.replace('"CAD"', '5'))
            r = _cli(root, "run", "--no-input")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("3-letter", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
