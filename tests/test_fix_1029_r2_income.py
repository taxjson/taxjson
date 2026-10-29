"""Backlog fix round 2026-10-29 (round 2), group `income`: dividend
forecasts (`tjs div-estimate`), dividend corrections and refunded
withholding, tips, slip-audit and roc-sum (T3 return of capital) and
the final-gains required-field contract.

TJ-0547  div-estimate: a dated rename carries the dividend history to
         the new symbol.
TJ-0548  div-estimate: withholding posted in another currency than its
         dividend is converted at the pay date's rate before the rate is
         read.
TJ-0549  div-estimate: a dividend paid after a split on shares counted
         on a record date before it is read in today's shares.
TJ-0552  div-estimate: a correction's refunded withholding moves with
         the gross it reverses.
TJ-0558  div-estimate: one broker account's short charge in a combined
         account is an expense, not a reversal of its own dividend,
         beside another broker account's larger long.
TJ-0429  slip-audit: the suggested .tt lines name the folder the year
         reads (../inputs/<account>/ in the shared-exports layout).
TJ-0420  slip-audit: a typed T3's box 42 is spread over the year's
         distributions when units were sold between them, not all put
         after the sale.
TJ-0427  roc-sum: a hand-typed .tt ADJUST type=roc line is a manual row.
TJ-0562  tips: TFSA-US-DIV follows the issuer's domicile, not the US
         listing alone.
TJ-0495  a gains document's income row without its amount or date is
         refused, an explicit 0 kept.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN.*, ZZ*), account names (margin), broker account
ids (99900001), amounts under 1,000. Planning numbers (CA-PLAN-06 /
US-PLAN-07) unless a test says otherwise.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import div_estimate as DE
from taxjson.lib.dispatch import python_module_argv
from tax_rules import rule

REPO = Path(__file__).resolve().parents[1]


def _tjs(root, *args):
    """The CLI in a child process (stdin closed, unwrapped output)."""
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
               NO_COLOR="1")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "packages" / "taxjson-fetch" / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    argv = python_module_argv("taxjson.bin.taxjson_run")
    if root is not None:
        argv += ["-C", str(root)]
    return subprocess.run(argv + list(args),
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(root, rows, settings="", country="canada", year=2025,
             name="trades.tt"):
    """A one-account project whose books are `rows` (a .tt file, or
    another export named `name`)."""
    root = Path(root)
    (root / "inputs" / "margin").mkdir(parents=True)
    base = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\n'
        f'country = "{country}"\nbase_currency = "{base}"\n'
        f'source_currencies = []\n{settings}\n'
        f'[accounts.margin]\ntype = "taxable"\n')
    (root / "ticker.map").write_text("")
    (root / "inputs" / "margin" / name).write_text(rows)
    return root


def _row(sym, d, act, q, amount=0.0, cur="CAD", acct="margin", **kw):
    return (acct, dict(date=d, account=acct, symbol=sym, quantity=q,
                       net_amount=amount, action=act, currency=cur, **kw))


def _one(rows, sym, qty, as_of, months=3, base="CAD", fx=None,
         fx_on=None):
    doc = DE.estimate([dict(account="margin", symbol=sym, qty=qty,
                            group="taxable")], DE.payments(rows),
                      as_of=as_of, months=months, tax_year=2025,
                      base=base, fx=fx or (lambda c: 1.0), fx_on=fx_on)
    return doc


_MONTHS = ("2025-04-02", "2025-05-02", "2025-06-02", "2025-07-02")


class TestDivEstimateRename(unittest.TestCase):
    """TJ-0547: 100 SYN.TO paid 10 a month April to July, then RENAME
    July 3 SYN.TO NEW.TO. As of July 4, NEW.TO was omitted with no
    history and the forecast was 0; the four payments are the same
    security's, so it is 10 a month."""

    def _rows(self):
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        rows += [_row("SYN.TO", d, "DIVIDEND", 100, 10, gross_amount=10)
                 for d in _MONTHS]
        rows.append(_row("SYN.TO", "2025-07-03", "SPLIT", 1.0,
                         symbol_new="NEW.TO"))
        return rows

    def test_the_history_follows_the_rename(self):
        doc = _one(self._rows(), "NEW.TO", 100, "2025-07-04")
        self.assertEqual(doc["omitted_no_history"], [])
        r = doc["rows"][0]
        self.assertEqual(r["frequency"], "monthly")
        self.assertEqual(r["payments"], 4)
        self.assertAlmostEqual(r["last_per_share"], 0.10)
        self.assertEqual(r["next_pay_date"], "2025-08-02")
        self.assertAlmostEqual(r["window_amount"], 30.0)
        # As of a date before the rename the shares held now are still
        # NEW.TO's: the same history.
        r = _one(self._rows(), "NEW.TO", 100, "2025-07-02")["rows"][0]
        self.assertAlmostEqual(r["window_amount"], 30.0)

    def test_a_later_payment_under_the_old_ticker_stays_its_own(self):
        """A row of the old ticker paid and entitled after the rename is
        another security's (the ticker reused): it is not NEW.TO's."""
        rows = self._rows() + [_row("SYN.TO", "2025-09-02", "DIVIDEND",
                                    50, 99, gross_amount=99)]
        pays = DE.payments(rows)
        self.assertEqual([p.pay_date for p in pays["NEW.TO"]], list(_MONTHS))
        self.assertEqual([p.pay_date for p in pays["SYN.TO"]],
                         ["2025-09-02"])

    def test_a_rename_with_a_ratio_and_a_later_split(self):
        """A rename that consolidates 2-for-1 (ratio 0.5), then a 3-for-1
        split of the new ticker: the old payments are read in today's
        shares (100 old = 50 new = 150 now; 10 / 150 a share)."""
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        rows += [_row("SYN.TO", d, "DIVIDEND", 100, 10, gross_amount=10)
                 for d in _MONTHS]
        rows.append(_row("SYN.TO", "2025-07-03", "SPLIT", 0.5,
                         symbol_new="NEW.TO"))
        rows.append(_row("NEW.TO", "2025-07-10", "SPLIT", 3.0,
                         symbol_new="NEW.TO"))
        r = _one(rows, "NEW.TO", 150, "2025-07-12")["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 10 / 150, places=5)
        self.assertAlmostEqual(r["window_amount"], 30.0)

    def test_the_command_after_a_run(self):
        rows = ("BUYSELL 2025-01-06 10:00:00 SYN.TO 100 CAD 1 100 0\n"
                + "".join(f"DIVIDEND {d} 10:00:00 SYN.TO 100 CAD 0.1 10\n"
                          for d in _MONTHS)
                + "RENAME 2025-07-03 SYN.TO NEW.TO\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", rows)
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs(root, "div-estimate", "--date", "2025-07-04",
                     "--months", "3", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["omitted_no_history"], [])
            self.assertEqual([x["symbol"] for x in doc["rows"]], ["NEW.TO"])
            self.assertAlmostEqual(doc["totals"]["window"]["taxable"], 30.0)


class TestDivEstimateWithholdingCurrency(unittest.TestCase):
    """TJ-0548: a USD 10 dividend whose same-day TAX row is CAD 2.70.
    The rate read 2.70 / 10 = 27% and CAD 10.94 over three payments; at
    USD/CAD 1.35, CAD 2.70 is USD 2.00, 20%, and the three payments'
    withholding is CAD 8.10."""

    def _rows(self):
        rows = [_row("SYN.US", "2025-01-06", "BUYSELL", 100, 100,
                     cur="USD")]
        for d in _MONTHS:
            rows.append(_row("SYN.US", d, "DIVIDEND", 100, 10, cur="USD",
                             gross_amount=10))
            rows.append(_row("SYN.US", d, "TAX", 0, 2.70, type="tax"))
        return rows

    def test_converted_at_the_pay_dates_rate(self):
        doc = _one(self._rows(), "SYN.US", 100, "2025-07-04",
                   fx=lambda c: 1.35, fx_on=lambda c, d: 1.35)
        r = doc["rows"][0]
        self.assertAlmostEqual(r["withholding_rate"], 0.20)
        self.assertEqual(r["flags"], [])
        self.assertAlmostEqual(doc["totals"]["withholding_window"]
                               ["taxable"], 8.10)
        # The pay date's rate, not the latest: 1.35 then, 1.50 now.
        doc = _one(self._rows(), "SYN.US", 100, "2025-07-04",
                   fx=lambda c: 1.50, fx_on=lambda c, d: 1.35)
        self.assertAlmostEqual(doc["rows"][0]["withholding_rate"], 0.20)

    def test_no_rate_no_withholding_rate(self):
        doc = _one(self._rows(), "SYN.US", 100, "2025-07-04",
                   fx=lambda c: None, fx_on=lambda c, d: None)
        r = doc["rows"][0]
        self.assertIsNone(r["withholding_rate"])
        self.assertTrue(any("withholding in CAD on USD payments" in f
                            for f in r["flags"]), r["flags"])

    def test_the_command_after_a_run(self):
        rows = "BUYSELL 2025-01-06 10:00:00 SYN.US 100 USD 1 100 0\n"
        for d in _MONTHS:
            rows += (f"DIVIDEND {d} 10:00:00 SYN.US 100 USD 0.1 10\n"
                     f"TAX {d} 10:00:00 SYN.US 0 CAD 0 2.7\n")
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", rows)
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "source_currencies = []", 'source_currencies = ["USD"]'))
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs(root, "div-estimate", "--date", "2025-07-04",
                     "--months", "3", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            row = json.loads(r.stdout)["rows"][0]
            rate = row["fx_rate"]
            self.assertTrue(rate and rate > 1, row)
            # CAD 2.70 over USD 10 at the books' rate on the pay date.
            self.assertAlmostEqual(row["withholding_rate"],
                                   2.70 / (10 * rate), places=3)


class TestDivEstimateRecordDateBeforeSplit(unittest.TestCase):
    """TJ-0549: four monthly CAD 10 dividends on 100 shares; the July 2
    payment's record date is June 27, before the June 30 2-for-1 split.
    It names the 100 pre-split shares: 0.05 a share now, 10 a payment on
    200 shares, not 20."""

    def test_the_record_date_decides_the_denomination(self):
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        for d in _MONTHS:
            kw = {"record_date": "2025-06-27"} if d == "2025-07-02" else {}
            rows.append(_row("SYN.TO", d, "DIVIDEND", 100, 10,
                             gross_amount=10, **kw))
        rows.append(_row("SYN.TO", "2025-06-30", "SPLIT", 2.0,
                         symbol_new="SYN.TO"))
        r = _one(rows, "SYN.TO", 200, "2025-07-04")["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 0.05)
        self.assertAlmostEqual(r["next_amount"], 10.0)
        self.assertAlmostEqual(r["window_amount"], 30.0)

    def test_a_record_date_after_the_split_is_not_scaled(self):
        """The control: a July 2 payment of record June 30 or later is on
        the post-split shares the row names (200): 0.05 a share."""
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        for d in _MONTHS[:3]:
            rows.append(_row("SYN.TO", d, "DIVIDEND", 100, 10,
                             gross_amount=10))
        rows.append(_row("SYN.TO", "2025-07-02", "DIVIDEND", 200, 10,
                         gross_amount=10, record_date="2025-06-30"))
        rows.append(_row("SYN.TO", "2025-06-30", "SPLIT", 2.0,
                         symbol_new="SYN.TO"))
        r = _one(rows, "SYN.TO", 200, "2025-07-04")["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 0.05)
        self.assertAlmostEqual(r["window_amount"], 30.0)


class TestDivEstimateCorrectionRefund(unittest.TestCase):
    """TJ-0552: a monthly dividend of 10 with TAX 1.50; the next day a
    correction of -5 and a TAX refund of -0.75. The gross was netted to
    5, but the refund stayed on the correction's date: 1.50 over 5, 30%,
    4.50 over three payments. Net: 0.75 over 5, 15%, 2.25."""

    def test_the_refund_moves_with_the_gross(self):
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        for d in _MONTHS:
            rows.append(_row("SYN.TO", d, "DIVIDEND", 100, 8.5,
                             gross_amount=10))
            rows.append(_row("SYN.TO", d, "TAX", 100, 1.5, type="tax"))
        rows.append(_row("SYN.TO", "2025-07-03", "DIVIDEND", 100, -5,
                         gross_amount=-5))
        rows.append(_row("SYN.TO", "2025-07-03", "TAX", 0, -0.75,
                         type="tax"))
        doc = _one(rows, "SYN.TO", 100, "2025-07-04")
        r = doc["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 0.05)
        self.assertAlmostEqual(r["withholding_rate"], 0.15)
        self.assertAlmostEqual(doc["totals"]["withholding_window"]
                               ["taxable"], 2.25)

    def test_a_refund_across_two_postings(self):
        """A reversal of 15 cancels the latest 10 and 5 of an earlier
        posting; its refund of -2.25 moves in the same proportion."""
        rows = [_row("SYN.TO", "2025-01-06", "BUYSELL", 100, 100)]
        for d in _MONTHS:
            rows.append(_row("SYN.TO", d, "DIVIDEND", 100, 8.5,
                             gross_amount=10))
            rows.append(_row("SYN.TO", d, "TAX", 100, 1.5, type="tax"))
        rows.append(_row("SYN.TO", "2025-07-03", "DIVIDEND", 100, -15,
                         gross_amount=-15))
        rows.append(_row("SYN.TO", "2025-07-03", "TAX", 0, -2.25,
                         type="tax"))
        pays = DE.payments(rows)
        by = {p.pay_date: p for p in pays["SYN.TO"]}
        self.assertNotIn("2025-07-02", by)
        self.assertAlmostEqual(by["2025-06-02"].gross, 5.0)
        self.assertAlmostEqual(by["2025-06-02"].tax, 0.75)


class TestDivEstimateCombinedShort(unittest.TestCase):
    """TJ-0558: one taxjson account combines broker accounts A and B.
    B holds 100, is paid 10, then sells 200 (short 100) while A buys
    1000; B is charged 10 on its short. The combined balance is long
    900, so the charge read as a reversal and erased B's payment: 900
    shares with no history. B's own balance is short: an expense."""

    def _rows(self, opening_without_source=False):
        b, a = "99900002", "99900001"
        rows = [_row("SYN.TO", "2025-06-02", "BUYSELL", 100, 100,
                     source_account=b),
                _row("SYN.TO", "2025-07-02", "DIVIDEND", 100, 10,
                     gross_amount=10, record_date="2025-07-02",
                     source_account=b),
                _row("SYN.TO", "2025-07-03", "BUYSELL", 1000, 1000,
                     source_account=a),
                _row("SYN.TO", "2025-07-03", "BUYSELL", -200, 200,
                     source_account=b),
                _row("SYN.TO", "2025-07-04", "DIVIDEND", 100, -10,
                     gross_amount=-10, record_date="2025-07-04",
                     source_account=b)]
        if opening_without_source:
            rows.insert(0, _row("SYN.TO", "2025-01-02", "OPENING_BALANCE",
                                100, 0))
        return rows

    def test_the_broker_accounts_own_short_is_an_expense(self):
        doc = _one(self._rows(), "SYN.TO", 900, "2025-07-05")
        self.assertEqual(doc["omitted_no_history"], [])
        r = doc["rows"][0]
        self.assertAlmostEqual(r["last_per_share"], 0.10)
        self.assertAlmostEqual(r["window_amount"], 22.50)

    def test_a_row_naming_no_broker_account_falls_back_to_the_account(
            self):
        """Units opened by a row naming no broker account (a .tt OPENING
        line) leave each broker account's own balance incomplete: the
        account's decides, as before (long 1000 here: a reversal)."""
        pays = DE.payments(self._rows(opening_without_source=True))
        self.assertNotIn("SYN.TO", pays)


_Q_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
             "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
             "Activity Type,Account #,Account Type\n")


class TestSlipAuditSuggestedFile(unittest.TestCase):
    """TJ-0429: in an open year of the default layout (one folder of
    exports for every year, `inputs_dir = "../inputs"`), slip-audit said
    to add the ROC line to inputs/margin/slip-audit.tt, a folder that
    year never reads: followed literally it changed nothing. It names
    ../inputs/margin/slip-audit.tt, the folder the year reads, and says
    every year reading it reads the line."""

    def test_the_shared_folder_is_named_and_the_line_applies(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "fd"
            r = _tjs(None, "init", "--country", "canada", "--year",
                     "2025", str(top))
            self.assertEqual(r.returncode, 0, r.stderr)
            (top / "inputs" / "margin" / "q.csv").write_text(
                _Q_HEADER
                + "2025-01-10,2025-01-13,Buy,ZZE.TO,ZZE SAMPLE TRUST UNITS,"
                  "100,10.00,-1000.00,0.00,-1000.00,CAD,Trades,99900001,"
                  "Margin\n"
                + "2025-06-27,2025-06-27,DIS,ZZE.TO,ZZE SAMPLE TRUST UNITS "
                  "DIST ON 100 SHS,0,0,20.00,0,20.00,CAD,Dividends,"
                  "99900001,Margin\n")
            year = top / "2025"
            (year / "inputs" / "slips").mkdir(parents=True)
            (year / "inputs" / "slips" / "slips.toml").write_text(
                'year = 2025\n\n[[slip]]\ntype = "T3"\n'
                'issuer = "ZZE SAMPLE TRUST"\naccount = "margin"\n'
                'security = "ZZE.TO"\nboxes = { 42 = 20.00 }\n')
            r = _tjs(year, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs(year, "slip-audit", "--json")
            (tt,) = json.loads(r.stdout)["suggestions"]["tt_lines"]
            self.assertEqual(tt["file"], "../inputs/margin/slip-audit.tt")
            self.assertTrue(tt["shared"])
            r = _tjs(year, "slip-audit")
            self.assertIn("Add to ../inputs/margin/slip-audit.tt", r.stdout)
            self.assertIn("each year reading it reads the line", r.stdout)
            # Followed literally, from the year folder, it brings the
            # books to the slip.
            (year / tt["file"]).write_text("\n".join(tt["lines"]) + "\n")
            r = _tjs(year, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs(year, "slip-audit", "--json")
            doc = json.loads(r.stdout)
            self.assertEqual(doc["suggestions"]["tt_lines"], [])
            line = [ln for a in doc["accounts"] for g in a["groups"]
                    for b in g["buckets"] for ln in b["lines"]
                    if ln["category"] == "roc"]
            self.assertEqual([ln["status"] for ln in line], ["ok"], line)

    def test_a_project_of_its_own_names_its_own_folder(self):
        from taxjson.lib.slip_audit import suggest_tt_file
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", "")
            self.assertEqual(suggest_tt_file(root, "margin"),
                             {"file": "inputs/margin/slip-audit.tt",
                              "shared": False})


_T3R = (_Q_HEADER
        + "2024-01-10,2024-01-12,Buy,ZZE.TO,ZZE SAMPLE ETF UNITS,200,25.00,"
          "-5000.00,-4.95,-5004.95,CAD,Trades,99900090,Margin\n"
        + "".join(
            f"{p},{p},DIV,ZZE.TO,ZZE SAMPLE ETF DIST ON {n} SHS REC {r} PAY "
            f"{p[5:7]}/{p[8:]}/24,0,0,{a},0,{a},CAD,Dividends,99900090,"
            f"Margin\n"
            for p, r, n, a in (("2024-03-28", "03/22/24", 200, "60.00"),
                               ("2024-06-27", "06/21/24", 200, "60.00"),
                               ("2024-09-27", "09/20/24", 200, "60.00")))
        + "2024-11-15,2024-11-18,Sell,ZZE.TO,ZZE SAMPLE ETF UNITS,-100,27.00,"
          "2700.00,-4.95,2695.05,CAD,Trades,99900090,Margin\n"
        + "2024-12-30,2024-12-30,DIV,ZZE.TO,ZZE SAMPLE ETF DIST ON 100 SHS "
          "REC 12/27/24 PAY 12/30/24,0,0,90.00,0,90.00,CAD,Dividends,"
          "99900090,Margin\n")
_T3R_SLIPS = ('year = 2024\n\n[[slip]]\ntype = "T3"\n'
              'issuer = "ZZE Sample Funds"\naccount = "margin"\n'
              'currency = "CAD"\nsecurity = "ZZE.TO"\n'
              'boxes = { 26 = 150.00, 21 = 40.00, 42 = 80.00 }\n')


class TestTypedT3ReturnOfCapitalAfterASale(unittest.TestCase):
    """TJ-0420: 200 ZZE.TO units, distributions of 60 in March, June and
    September, 100 units sold November 15, a distribution of 90 on
    December 30; the typed T3 returns 80 of capital (box 42). All 80 was
    suggested on December 30, after the sale: the units sold kept an ACB
    the return of capital should have lowered (gain 192.58). Spread in
    proportion to the distributions (17.78, 17.78, 17.78, 26.66), 53.34
    lowers the ACB before the sale: the gain is 2700.00 - 2475.80 -
    4.95 = 219.25 and the 100 units kept end at 2449.14. TJ-0427: the
    applied .tt lines are manual rows in roc-sum, not the broker's."""

    def _run(self, root, *args):
        r = _tjs(root, *args)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r

    @rule("CA-SLIP-03", "CA-INC-DATE-ROC-TRUST")
    def test_spread_over_the_distributions_and_manual_in_roc_sum(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", _T3R, year=2024,
                            name="questrade_2024.csv")
            (root / "inputs" / "slips").mkdir()
            (root / "inputs" / "slips" / "slips.toml").write_text(_T3R_SLIPS)
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(self._run(root, "slip-audit", "--json").stdout)
            (tt,) = doc["suggestions"]["tt_lines"]
            adj = [ln for ln in tt["lines"] if ln.startswith("ADJUST")]
            self.assertEqual(adj, [
                "ADJUST 2024-03-28 16:00:00 ZZE.TO CAD -17.78 type=roc "
                "record=2024-03-22",
                "ADJUST 2024-06-27 16:00:00 ZZE.TO CAD -17.78 type=roc "
                "record=2024-06-21",
                "ADJUST 2024-09-27 16:00:00 ZZE.TO CAD -17.78 type=roc "
                "record=2024-09-20",
                "ADJUST 2024-12-30 16:00:00 ZZE.TO CAD -26.66 type=roc "
                "record=2024-12-27"])
            self.assertTrue(any("sold on 2024-11-15" in n
                                and "fund's breakdown" in n
                                for n in doc["suggestions"]["notes"]),
                            doc["suggestions"]["notes"])
            (root / tt["file"]).write_text("\n".join(tt["lines"]) + "\n")
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(self._run(root, "slip-audit", "--json").stdout)
            self.assertEqual(doc["suggestions"]["tt_lines"], [])
            self.assertEqual(doc["issues"], [])
            fe = json.loads(self._run(root, "form-export", "--json").stdout)
            (row,) = [x for x in fe["rows"] if x["symbol"] == "ZZE.TO"]
            self.assertAlmostEqual(row["acb"], 2475.80, places=2)
            self.assertAlmostEqual(row["gain"], 219.25, places=2)
            # TJ-0427: the four typed lines are manual, not the broker's.
            rs = json.loads(self._run(root, "roc-sum", "--json").stdout)
            (z,) = rs["rows"]
            self.assertEqual((z["roc_rows"], z["manual_rows"],
                              z["dist_rows"]), (0, 4, 0))
            self.assertAlmostEqual(z["capital_returned"], 80.0)

    @rule("CA-SLIP-03")
    def test_no_sale_between_keeps_one_line_on_the_last(self):
        """The control: with the sale after the last distribution's
        record date, one line on the last distribution, as before."""
        csv = _T3R.replace("2024-11-15,2024-11-18,Sell", "SKIP").splitlines()
        csv = "\n".join(ln for ln in csv if not ln.startswith("SKIP")) + "\n"
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", csv, year=2024,
                            name="questrade_2024.csv")
            (root / "inputs" / "slips").mkdir()
            (root / "inputs" / "slips" / "slips.toml").write_text(_T3R_SLIPS)
            r = _tjs(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(self._run(root, "slip-audit", "--json").stdout)
            (tt,) = doc["suggestions"]["tt_lines"]
            self.assertEqual(
                [ln for ln in tt["lines"] if ln.startswith("ADJUST")],
                ["ADJUST 2024-12-30 16:00:00 ZZE.TO CAD -80.00 type=roc "
                 "record=2024-12-27"])


class TestTipsIssuerDomicile(unittest.TestCase):
    """TJ-0562: a TFSA holds SYN.US, a Canadian issuer listed only in the
    US (no Canadian listing in the books), declared `T1135 SYN.US CAN`.
    tips said "US dividend payer inside a TFSA: the 15% US withholding
    is unrecoverable here". US withholding follows the issuer's
    domicile, not its listing."""

    def _tips(self, td, ticker_map="", issuer_country=""):
        root = Path(td) / "p"
        (root / "work").mkdir(parents=True)
        (root / "reports").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\n[accounts.tfsa]\ntype = "sheltered"\n'
            'plan = "tfsa"\n')
        (root / "reports" / "tfsa_holdings.toml").write_text(
            'schema_version = "1.2"\n[[holding]]\nsymbol = "SYN.US"\n'
            'quantity = 100.0\ncurrency = "USD"\ntotal_cost = 100.0\n'
            'cost_per_share = 1.0\n')
        row = {"action": "DIVIDEND", "date": "2025-03-01",
               "symbol": "SYN.US", "quantity": 100, "gross_amount": 10.0,
               "net_amount": 10.0, "currency": "USD"}
        if issuer_country:
            row["issuer_country"] = issuer_country
        (root / "work" / "tfsa_raw.json").write_text(
            json.dumps({"transactions": [row]}))
        (root / "ticker.map").write_text(ticker_map)
        r = _tjs(root, "tips", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return [f for f in json.loads(r.stdout)["findings"]
                if f["check"] == "TFSA-US-DIV"]

    @rule("CA-SCAN-01")
    def test_a_declared_canadian_issuer_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._tips(td, "T1135 SYN.US CAN\n"), [])
        with tempfile.TemporaryDirectory() as td:
            # The export's ISIN says Canada (IB).
            self.assertEqual(self._tips(td, issuer_country="CA"), [])
        with tempfile.TemporaryDirectory() as td:
            # Another foreign country: no US withholding claimed either.
            self.assertEqual(self._tips(td, "T1135 SYN.US IRL\n"), [])

    @rule("CA-SCAN-01")
    def test_a_declared_us_issuer_is_flagged_outright(self):
        with tempfile.TemporaryDirectory() as td:
            (f,) = self._tips(td, "T1135 SYN.US USA\n")
        self.assertIn("US dividend payer inside a TFSA: the 15% US "
                      "withholding is unrecoverable", f["message"])
        self.assertNotIn("if its issuer", f["message"])

    @rule("CA-SCAN-01")
    def test_no_domicile_on_record_is_a_condition(self):
        with tempfile.TemporaryDirectory() as td:
            (f,) = self._tips(td)
        self.assertIn("if its issuer is US-domiciled", f["message"])
        self.assertIn("T1135 SYN.US COUNTRY", f["message"])


class TestGainsIncomeRowContract(unittest.TestCase):
    """TJ-0495: a final gains document's DIVIDEND without its amount, or
    with no date, passed require_gains_doc; T1135 joined the first as
    income 0 and dropped the second, with no warning."""

    def _doc(self, row):
        return {"transactions": [dict(row, symbol="SYN.US",
                                      currency="CAD", gain=0.0)]}

    def test_an_income_row_without_its_amount_or_date_is_refused(self):
        from taxjson.lib.json_input import InputFileError, require_gains_doc
        for row, field in (
                ({"action": "DIVIDEND", "date": "2025-06-01"}, "dividend"),
                ({"action": "DIVIDEND", "dividend": 100.0}, "date"),
                ({"action": "DIVIDEND", "dividend": None,
                  "date": "2025-06-01"}, "dividend"),
                ({"action": "DIVIDEND_IN_LIEU", "date": "2025-06-01"},
                 "pil or dividend"),
                ({"action": "DIVIDEND_IN_LIEU", "pil": 5.0, "date": ""},
                 "date")):
            with self.subTest(row=row):
                with self.assertRaises(InputFileError) as cm:
                    require_gains_doc(self._doc(row), "g.json")
                msg = str(cm.exception)
                self.assertIn('g.json: "transactions" row 0 (SYN.US', msg)
                self.assertIn(f"an income row ({row['action']}) without "
                              f"{field}", msg)

    def test_an_explicit_zero_and_settle_date_are_kept(self):
        from taxjson.lib.json_input import require_gains_doc
        for row in ({"action": "DIVIDEND", "dividend": 0,
                     "date": "2025-06-01"},
                    {"action": "DIVIDEND", "dividend": 10.0,
                     "date_settle": "2025-06-02"},
                    {"action": "DIVIDEND_IN_LIEU", "pil": 5.0,
                     "date": "2025-06-01"},
                    # a s.260 deemed dividend: the run moves pil there.
                    {"action": "DIVIDEND_IN_LIEU", "dividend": 5.0,
                     "pil": 0.0, "date": "2025-06-01"}):
            with self.subTest(row=row):
                doc = self._doc(row)
                self.assertIs(require_gains_doc(doc, "g.json"), doc)

    def test_t1135_refuses_rather_than_joining_zero(self):
        from taxjson.bin.taxjson_t1135 import (UnreadableGains,
                                               join_income_gains)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_gains.json"
            p.write_text(json.dumps(self._doc(
                {"action": "DIVIDEND", "date": "2025-06-01"})))
            with self.assertRaises(UnreadableGains) as cm:
                join_income_gains([p], 2025, {})
            self.assertIn("without dividend", str(cm.exception))
            p.write_text(json.dumps(self._doc(
                {"action": "DIVIDEND", "dividend": 0.0,
                 "date": "2025-06-01"})))
            self.assertEqual(join_income_gains([p], 2025, {}),
                             {"SYN.US": {"income": 0.0, "gain": 0.0}})


if __name__ == "__main__":
    unittest.main()
