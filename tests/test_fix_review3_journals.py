"""Third pre-release review of the journals (v0.24.0):

- 2: a journal justifies an opposite-trade day near its legs only when
  that day's trades match ITS quantity in ITS direction (the FROM
  listing bought, the TO listing sold), and only one such day; a sale
  with no purchase on one listing and a buy of the other near an
  unrelated journal of another size stays missing history.
- 5: the run's mid-run short-sale note reads this run's gains, not the
  last run's cross-account wash file (rebuilt only after the note).
- 8: the cross-listing loss radar pairs a Canadian class share
  (QZT.B.TO) with its US listing written without the class letter (QZT),
  equal names still required.
- 9: the radar's still-held test scales for splits and consolidations,
  and lists at most RADAR_SHOWN pairs on the console; the .tt total
  warning quotes an ACQUIRED line as written (no fee column advised) and
  names the file as the step line does (masked).

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: 55500001).
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import projects_both

# Imported here, not inside a test: _qa_project snapshots the
# environment, and a @rule test sets the engine guard while it runs.
from _qa_project import project, tj
from test_fix_qa_f3_xlist_loss import F3_ROWS, QT_HEAD, qt_row
from test_fix_review2_journals import _USA_CAD, _short_reported


# ------------------------------------------------------------------ 2

_SHORT_A = ("BUYSELL 2025-03-03 09:00:00 QZF.U.TO -100 USD 7.00 700.00 "
            "0.00\n"
            "BUYSELL 2025-03-03 10:00:00 QZF.TO 100 CAD 10.00 1000.00 "
            "0.00\n")
_GAMBIT_A = ("BUYSELL 2025-03-04 10:00:00 QZF.TO 50 CAD 10.00 500.00 0.00\n"
             "JOURNAL 2025-03-05 QZF.TO QZF.U.TO 50\n"
             "BUYSELL 2025-03-06 10:00:00 QZF.U.TO -50 USD 7.20 360.00 "
             "0.00\n")


class TestJournalJustifiesItsOwnTradesOnly(unittest.TestCase):

    def _a(self, country):
        """A .tt journal of 50 units two days after a 100-unit sale with
        no purchase (and a buy of the other listing): still missing
        history; the journal's own gambit is not."""
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for k, body in (("short", _SHORT_A + _GAMBIT_A),
                            ("gambit", _GAMBIT_A)):
                files = {"ticker.map": "TOBASE QZF.U.TO QZF.TO\n",
                         "inputs/margin/m.tt": body}
                root = projects_both(Path(td) / k, files=files,
                                     usa=_USA_CAD)[country]
                got[k] = _short_reported(self, root, "QZF")
            self.assertEqual(got["short"], (True, True, True))
            self.assertEqual(got["gambit"], (False, False, False))

    def _b(self, country):
        """An RBC gambit of 1000 units (trades May 5, J~ legs May 6) and
        a 200-unit sale with no purchase plus a buy of the other listing
        on May 8: the May 8 sale is missing history."""
        from test_fix_dated_events import _rbc_gambit
        rbc, tmap = _rbc_gambit(True)
        with tempfile.TemporaryDirectory() as td:
            got = {}
            for k, extra in (("short", True), ("gambit", False)):
                files = {"inputs/margin/rbc.csv": rbc, "ticker.map": tmap}
                if extra:
                    files["inputs/margin/m.tt"] = (
                        "BUYSELL 2025-05-08 09:00:00 QZD.U.TO -200 USD "
                        "10.20 2040.00 0.00\n"
                        "BUYSELL 2025-05-08 10:00:00 QZD.TO 200 CAD 13.90 "
                        "2780.00 0.00\n")
                root = projects_both(Path(td) / k, files=files,
                                     usa=_USA_CAD)[country]
                got[k] = _short_reported(self, root, "QZD")
            self.assertEqual(got["short"], (True, True, True))
            self.assertEqual(got["gambit"], (False, False, False))

    @rule("CA-XLIST-04")
    def test_canada_tt_journal_of_another_size_hides_no_short(self):
        self._a("canada")

    @rule("US-XLIST-03")
    def test_usa_tt_journal_of_another_size_hides_no_short(self):
        self._a("usa")

    @rule("CA-XLIST-04")
    def test_canada_rbc_gambit_justifies_only_its_own_day(self):
        self._b("canada")

    @rule("US-XLIST-03")
    def test_usa_rbc_gambit_justifies_only_its_own_day(self):
        self._b("usa")


class TestJournalDayUnit(unittest.TestCase):
    """walk_journal_symbols' day choice, on its inputs."""

    def test_quantity_direction_and_one_day(self):
        from taxjson.lib.missing_history import _journal_trade_day
        legs = ["2025-05-06"]
        trades = {
            ("m", "2025-05-05"): {"QZD.TO": [1000.0, 0.0],
                                  "QZD.U.TO": [0.0, 1000.0]},
            # Same size, a day later: the journal already has its day.
            ("m", "2025-05-07"): {"QZD.TO": [1000.0, 0.0],
                                  "QZD.U.TO": [0.0, 1000.0]},
            # Another size.
            ("m", "2025-05-08"): {"QZD.TO": [200.0, 0.0],
                                  "QZD.U.TO": [0.0, 200.0]},
        }
        day = _journal_trade_day(trades, "m", legs, "QZD.TO", "QZD.U.TO",
                                 1000.0)
        # The nearest; a tie goes to the earlier day (trades settle
        # after they are made).
        self.assertEqual(day, "2025-05-05")
        self.assertIsNone(_journal_trade_day(
            trades, "m", legs, "QZD.TO", "QZD.U.TO", 200.0 + 1))
        # The wrong direction (TO bought, FROM sold) is no gambit.
        self.assertIsNone(_journal_trade_day(
            {("m", "2025-05-05"): {"QZD.TO": [0.0, 1000.0],
                                   "QZD.U.TO": [1000.0, 0.0]}},
            "m", legs, "QZD.TO", "QZD.U.TO", 1000.0))
        # Trades on the legs' own day: that day, no other.
        self.assertEqual("2025-05-06", _journal_trade_day(
            {("m", "2025-05-06"): {"QZD.TO": [1000.0, 0.0],
                                   "QZD.U.TO": [0.0, 1000.0]},
             ("m", "2025-05-05"): {"QZD.TO": [1000.0, 0.0],
                                   "QZD.U.TO": [0.0, 1000.0]}},
            "m", legs, "QZD.TO", "QZD.U.TO", 1000.0))


# ------------------------------------------------------------------ 5

_SALE = "BUYSELL 2024-03-01 10:00:00 QZS.TO -100 CAD 10.00 1000.00 0.00\n"
_COVER = "BUYSELL 2024-05-01 10:00:00 QZS.TO 100 CAD 8.00 800.00 0.00\n"
_COVERED = ("1 position(s) go short in margin's data (QZS.TO): booked as "
            "short sales closed by a later purchase")
_OPEN = "their gain is in no total"


class TestShortNoteReadsThisRun(unittest.TestCase):
    """Run 1 with one book, run 2 with the other, in one project: the
    mid-run note says what run 2's engine booked (as the closing summary
    and `taxjson sum` do)."""

    def _two_runs(self, country, first, second):
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/book.tt": first},
                           country=country)
            tj(root, "run", "--no-input")
            (root / "inputs" / "margin" / "book.tt").write_text(second)
            # --details: the note's detail line (_OPEN) is not on the
            # default one-line console (Essentials first).
            r = tj(root, "run", "--no-input", "--details")
            s = tj(root, "sum", "--json").stdout
            mid = " ".join(r.stdout.split(
                "Checking for missing purchase history", 1)[-1]
                .split("==>", 1)[0].split())
            return mid, json.loads(s)

    def _check(self, country):
        mid, s = self._two_runs(country, _SALE, _SALE + _COVER)
        self.assertIn(_COVERED, mid)
        self.assertNotIn(_OPEN, mid)
        self.assertEqual([x["booked"] for x in s["no_purchase_in_totals"]],
                         ["short_cover"])
        mid, s = self._two_runs(country, _SALE + _COVER, _SALE)
        self.assertIn(_OPEN, mid)
        self.assertNotIn("booked as short sales closed", mid)
        self.assertEqual([x["symbol"] for x in s["no_purchase_uncovered"]],
                         ["QZS.TO"])

    def test_canada_note_follows_this_run(self):
        self._check("canada")

    def test_usa_note_follows_this_run(self):
        self._check("usa")


# ------------------------------------------------------------------ 8

def _qt_trades(rows):
    from test_fix_qa_f3_xlist_loss import QT_HEAD, qt_row
    return QT_HEAD + "".join(qt_row(*r) for r in rows)


_CL_B = "QZT HOLDINGS INC CL B SUBORDINATE VOTING"


class TestRadarClassShareListedWithoutItsLetter(unittest.TestCase):

    def _run(self, country, us_name=_CL_B):
        rows = [("2024-01-02", "2024-01-04", "Buy", "QZT.B.TO", 100, 70,
                 "CAD", _CL_B),
                ("2024-03-01", "2024-03-05", "Sell", "QZT.B.TO", -100, 60,
                 "CAD", _CL_B),
                ("2024-03-11", "2024-03-13", "Buy", "QZT", 100, 45, "USD",
                 us_name)]
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/q.csv": _qt_trades(rows)},
                           country=country)
            return tj(root, "run", "--no-input").stdout

    def _check(self, country, kind):
        out = self._run(country)
        self.assertIn(f"Warning: possible {kind} across listings: QZT.B.TO "
                      f"sold at a loss, QZT.US bought within 30 days", out)
        # A US line named without the class is not the class share.
        out = self._run(country, "QZT HOLDINGS INC")
        self.assertNotIn("across listings", out)

    @rule("CA-XLIST-05")
    def test_canada_class_share_and_its_us_line(self):
        self._check("canada", "superficial loss")

    @rule("US-XLIST-04")
    def test_usa_class_share_and_its_us_line(self):
        self._check("usa", "wash sale")

    def test_roots(self):
        from taxjson.lib.xlist_loss_radar import _roots
        self.assertEqual(_roots("QZT.B.TO"), {"QZT.B", "QZT"})
        self.assertEqual(_roots("QZT.US"), {"QZT"})
        self.assertEqual(_roots("QZT.U.TO"), {"QZT"})
        # A unit or warrant designator is not a class letter.
        self.assertEqual(_roots("QZT.UN.TO"), {"QZT.UN"})
        self.assertEqual(_roots("QZT.WS"), {"QZT.WS"})


# ------------------------------------------------------------------ 9

class TestRadarHeldAtScalesSplits(unittest.TestCase):

    def test_held_at(self):
        from datetime import date
        from taxjson.lib.xlist_loss_radar import _held_at

        def r(action, day, q, acct="m", **kw):
            return dict(action=action, date=day, date_settle=day,
                        quantity=q, account=acct, symbol="QZX.US", **kw)
        rows = [r("BUYSELL", "2024-03-11", 100),
                r("SPLIT", "2024-03-15", 2, symbol_new="QZX.US"),
                r("BUYSELL", "2024-03-20", -150)]
        self.assertAlmostEqual(_held_at(rows, date(2024, 3, 31),
                                        "canada"), 50.0)
        # A one-for-ten consolidation, then all ten sold.
        rows = [r("BUYSELL", "2024-03-11", 100),
                r("SPLIT", "2024-03-15", 0.1),
                r("BUYSELL", "2024-03-20", -10)]
        self.assertAlmostEqual(_held_at(rows, date(2024, 3, 31),
                                        "canada"), 0.0)
        # Each account's split scales its own units.
        rows = [r("BUYSELL", "2024-03-11", 100),
                r("BUYSELL", "2024-03-11", 10, acct="t"),
                r("SPLIT", "2024-03-15", 2)]
        self.assertAlmostEqual(_held_at(rows, date(2024, 3, 31),
                                        "canada"), 210.0)
        # A split renaming the listing away moves its units off it.
        rows = [r("BUYSELL", "2024-03-11", 100),
                r("SPLIT", "2024-03-15", 1, symbol_new="QZY.US")]
        self.assertAlmostEqual(_held_at(rows, date(2024, 3, 31),
                                        "canada"), 0.0)

    @rule("CA-XLIST-05")
    def test_canada_split_keeps_the_listing_held(self):
        """Bought 100 in the window, split two-for-one, 150 sold before
        day 30: 50 still held, so the loss is flagged."""
        rows = F3_ROWS + qt_row("2024-03-20", "2024-03-21", "Sell", "ZZX",
                                -150, 46, "USD")
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {
                "margin/q.csv": QT_HEAD + rows,
                "margin/split.tt": "SPLIT 2024-03-15 09:30:00 ZZX.US "
                                   "ZZX.US 2\n"})
            out = tj(root, "run", "--no-input").stdout
        self.assertIn("Warning: possible superficial loss across listings: "
                      "ZZX.TO sold at a loss, ZZX.US bought within 30 days",
                      out)


class TestRadarCap(unittest.TestCase):

    def test_list_capped_with_a_summary(self):
        import contextlib
        import io
        from taxjson.bin import taxjson_run as TR
        from taxjson.lib import xlist_loss_radar as XR

        def finding(k):
            return XR.Finding(f"QZ{k:02d}.TO", f"QZ{k:02d}.US", "QZ CO",
                              f"TOBASE QZ{k:02d}.US QZ{k:02d}.TO",
                              f"DISTINCT QZ{k:02d}.TO QZ{k:02d}.US",
                              [{"account": "m", "date": "2024-03-01"}],
                              [{"account": "m", "date": "2024-03-11"}])
        n = XR.RADAR_SHOWN + 3
        orig = XR.analyze
        XR.analyze = lambda root, cfg: [finding(k) for k in range(n)]
        TR._SHOWN_THIS_RUN.clear()
        buf = io.StringIO()
        try:
            with tempfile.TemporaryDirectory() as td, \
                    contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(buf):
                TR._say_xlist_losses(Path(td), {"settings": {
                    "country": "canada"}}, Path(td))
        finally:
            XR.analyze = orig
        text = " ".join(buf.getvalue().split())
        self.assertEqual(text.count("possible superficial loss across "
                                    "listings: QZ"), XR.RADAR_SHOWN)
        self.assertIn("3 more possible superficial losses across listings",
                      text)
        self.assertIn("taxjson ticker-map --suggest", text)


_ACQ = ("ACQUIRED 2024-01-10 09:30:00 QZA.TO 40 CAD 12.00 530.00 "
        "ARRIVED 2024-06-03\n")


class TestTtTotalWarning(unittest.TestCase):
    """An ACQUIRED line whose total is not qty x price, and a .tt file
    whose name holds an account-number-like part."""

    def _run(self, country, name, body, *args, acct="margin"):
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {f"{acct}/{name}": body},
                           country=country, extra_accounts=(
                               '[accounts.plan]\ntype = "sheltered"\n'))
            return tj(root, "run", "--no-input", *args, check=False)

    def _check(self, country):
        # (a sheltered account: the arrival leg needs no broker row)
        # The line and the price that agrees are the warning's detail:
        # --details (the default console shows its headline).
        r0 = self._run(country, "lots.tt", _ACQ, acct="plan")
        self.assertIn("inputs/plan/lots.tt:1", r0.stdout + r0.stderr)
        r = self._run(country, "lots.tt", _ACQ, "--details", acct="plan")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
        text = " ".join(r.stdout.split() + r.stderr.split())
        self.assertIn("ACQUIRED 2024-01-10 09:30:00 QZA.TO 40 CAD 12.00 "
                      "530.00 ARRIVED 2024-06-03", text)
        self.assertNotIn("BUYSELL 2024-01-10", text)
        self.assertNotIn("put the difference in the line's fee column", text)
        self.assertIn("13.25", text)        # the price that agrees

    def test_canada_acquired_line_quoted_as_written(self):
        self._check("canada")

    def test_usa_acquired_line_quoted_as_written(self):
        self._check("usa")

    def test_masked_name_everywhere(self):
        body = "BUYSELL 2024-02-01 10:00:00 QZA.TO 10 CAD 10.00 500.00 0\n"
        name = "acct55500001.tt"                                # pii-ok
        r = self._run("canada", name, body, "--strict")
        text = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("55500001", text)                      # pii-ok
        self.assertIn("Reading acct55***.tt", text)
        self.assertIn("inputs/margin/acct55***.tt:1:", text)
        self.assertIn("--strict: margin: inputs/margin/acct55***.tt has",
                      " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
