"""The 2026-10-29 fix round, planning commands, second part: wash-radar,
rebuy, buy-check, sell-check, harvest and wash-sales (backlog TJ-0446,
TJ-0447, TJ-0448, TJ-0449, TJ-0450, TJ-0451, TJ-0452, TJ-0453,
TJ-0559, TJ-0560, TJ-0428, TJ-0474). Synthetic data only: made-up
tickers (ZZX, ZZY, ZZP, ZZC, ZZGA, ZZQ, ZZR) in throwaway folders."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from test_fix_1029_planning import _toml
from test_fix_backlog_radar import _check, _radar_main, _rebuy, _row

from taxjson.bin.taxjson_watch import flatten_radar


def _radar_map(country, taxable, map_text, as_of, sheltered=None):
    """The radar's --json document, flattened, with a ticker.map of
    `map_text` (None: no map) in a folder of its own."""
    with tempfile.TemporaryDirectory() as d:
        extra = ["--all", "--json", "--cleared-days", "7"]
        if map_text is not None:
            mp = Path(d) / "ticker.map"
            mp.write_text(map_text)
            extra += ["--ticker-map", str(mp)]
        return flatten_radar(json.loads(_radar_main(
            country, taxable, sheltered, as_of, tuple(extra))))


_PAIR = "TOBASE ZZY.US ZZY.TO\n"


class TestTwoListingsTwoCalendars(unittest.TestCase):
    """TJ-0446 / TJ-0447: tobase.map books ZZY.US as ZZY.TO. A sale or a
    buy of the US listing settles on US trading days: the radar read
    only the TSX calendar, so its sell-by date was a day late (a US sale
    that day settled after day 30) and its re-entry date a day early (a
    US buy that day still settled inside the window)."""

    _VIOL = [_row("2026-01-05", "ZZY.TO", 100, 20.0, settle="2026-01-06"),
             _row("2026-05-19", "ZZY.TO", -100, 15.0, settle="2026-05-20"),
             _row("2026-05-26", "ZZY.TO", 50, 15.0, settle="2026-05-27")]

    @rule("CA-PLAN-01")
    def test_tj0446_sell_by_is_the_earlier_of_the_two(self):
        # Window ends 2026-06-19 (Juneteenth: NYSE closed). A US sale
        # on 06-18 settles 06-22, after day 30; on 06-17 it settles
        # 06-18. The TSX alone gave 06-18.
        r = _radar_map("canada", self._VIOL, _PAIR, "2026-06-01")["ZZY.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["clears_at"], "2026-06-17")
        self.assertIn("on either listing, ZZY.TO or ZZY.US",
                      " ".join(r["advisory"].split()))
        alone = _radar_map("canada", self._VIOL, None, "2026-06-01")
        self.assertEqual(alone["ZZY.TO"]["clears_at"], "2026-06-18")
        self.assertNotIn("either listing", alone["ZZY.TO"]["advisory"])

    @rule("CA-PLAN-01")
    def test_tj0446_rebuy_says_the_same_sell_by(self):
        radar = _radar_map("canada", self._VIOL, _PAIR, "2026-06-01")
        doc = _rebuy(radar, "2026-06-01")
        text = " ".join(json.dumps(doc).split())
        self.assertIn("2026-06-17", text)
        self.assertNotIn("2026-06-18", text)

    @rule("CA-PLAN-01")
    def test_tj0446_thanksgiving(self):
        # Loss settles 10-30: day 30 is Sunday 11-29. The TSX trades
        # Thursday 11-26 (settles Friday); the NYSE is closed for
        # Thanksgiving, and its Wednesday trade is the last that settles
        # by Friday.
        rows = [_row("2026-01-05", "ZZY.TO", 100, 20.0, settle="2026-01-06"),
                _row("2026-10-29", "ZZY.TO", -100, 15.0, settle="2026-10-30"),
                _row("2026-10-29", "ZZY.TO", 50, 15.0, settle="2026-10-30")]
        self.assertEqual(
            _radar_map("canada", rows, _PAIR, "2026-10-29")["ZZY.TO"]
            ["clears_at"], "2026-11-25")
        self.assertEqual(
            _radar_map("canada", rows, None, "2026-10-29")["ZZY.TO"]
            ["clears_at"], "2026-11-26")

    _COOL = [_row("2026-01-05", "ZZX.TO", 100, 20.0, settle="2026-01-06"),
             _row("2026-05-29", "ZZX.TO", -100, 15.0, settle="2026-06-01")]

    @rule("CA-PLAN-05")
    def test_tj0447_rebuy_is_the_later_of_the_two(self):
        # Window ends 2026-07-01 (Canada Day: the TSX is closed). A TSX
        # buy on 06-30 settles 07-02, after the window; a US buy on
        # 06-30 settles 07-01, inside it.
        pair = "TOBASE ZZX.US ZZX.TO\n"
        r = _radar_map("canada", self._COOL, pair, "2026-06-02")["ZZX.TO"]
        self.assertEqual(r["category"], "COOLING", r["advisory"])
        self.assertEqual(r["rebuy_at"], "2026-07-01")
        self.assertEqual(r["clears_at"], "2026-07-01")
        alone = _radar_map("canada", self._COOL, None, "2026-06-02")
        self.assertEqual(alone["ZZX.TO"]["rebuy_at"], "2026-06-30")
        radar = _radar_map("canada", self._COOL, pair, "2026-06-02")
        out, _code = _check("cmd_buy_check", radar, ["ZZX.TO"])
        self.assertIn("2026-07-01", out)
        self.assertNotIn("2026-06-30", out)

    @rule("CA-PLAN-05")
    def test_tj0447_a_us_booked_pair(self):
        # Booked under the US listing (TOBASE ZZC.TO ZZCU.US): the loss
        # settles 10-27, the window ends 11-26 (US Thanksgiving). A US
        # buy on 11-25 settles 11-27; a TSX buy that day settles 11-26,
        # inside the window.
        rows = [_row("2026-04-01", "ZZCU.US", 100, 20.0, settle="2026-04-02",
                     currency="USD"),
                _row("2026-10-26", "ZZCU.US", -100, 15.0, settle="2026-10-27",
                     currency="USD")]
        r = _radar_map("canada", rows, "TOBASE ZZC.TO ZZCU.US\n",
                       "2026-10-28")["ZZCU.US"]
        self.assertEqual(r["rebuy_at"], "2026-11-26")
        alone = _radar_map("canada", rows, None, "2026-10-28")["ZZCU.US"]
        self.assertEqual(alone["rebuy_at"], "2026-11-25")


class TestTheRunPassesTheMap(unittest.TestCase):
    """TJ-0446 / TJ-0447 end to end: the project's ticker.map reaches
    the radar every planning command runs."""

    @rule("CA-PLAN-05")
    def test_tj0447_rebuy_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(_toml(year=2026))
            (root / "ticker.map").write_text("TOBASE ZZX.US ZZX.TO\n")
            # A .tt line's one date is its settlement date (tax_date
            # settle): the sale settles 06-01.
            (root / "inputs" / "margin" / "m.tt").write_text(
                "BUYSELL 2026-01-06 10:00:00 ZZX.TO 100 CAD 20 2000 0\n"
                "BUYSELL 2026-06-01 10:00:00 ZZX.TO -100 CAD 15 1500 0\n")
            tj(root, "run", "--no-input")
            r = tj(root, "rebuy", "ZZX.US", "--date", "2026-06-02", "--json")
        doc = json.loads(r.stdout)
        self.assertIn("2026-07-01", json.dumps(doc), doc)
        self.assertNotIn("2026-06-30", json.dumps(doc), doc)


class TestFullExitNamesEveryUnit(unittest.TestCase):
    """TJ-0448: EXITABLE said "Selling the FULL position at a loss is
    fine now" over a position split across two listings or accounts;
    selling one of them is a partial sale, and its loss is denied."""

    @rule("CA-PLAN-01")
    def test_tj0448_two_listings(self):
        rows = [_row("2026-01-05", "ZZX.TO", 100, 20.0, settle="2026-01-06"),
                _row("2026-10-22", "ZZX.TO", 50, 11.0, settle="2026-10-23")]
        r = _radar_map("canada", rows, "TOBASE ZZX.US ZZX.TO\n",
                       "2026-10-29")["ZZX.TO"]
        self.assertEqual(r["category"], "EXITABLE", r["advisory"])
        self.assertIn("all 150 taxable units of ZZX.TO and ZZX.US, one "
                      "security", " ".join(r["advisory"].split()))

    @rule("CA-PLAN-01")
    def test_tj0448_two_accounts(self):
        rows = [_row("2026-02-02", "ZZP.TO", 100, 20.0, settle="2026-02-03"),
                _row("2026-03-03", "ZZP.TO", 100, 20.0, acct="cash",
                     settle="2026-03-04"),
                _row("2026-10-21", "ZZP.TO", 50, 11.0, acct="cash",
                     settle="2026-10-22")]
        r = _radar_map("canada", rows, None, "2026-10-29")["ZZP.TO"]
        self.assertEqual(r["category"], "EXITABLE", r["advisory"])
        self.assertIn("all 250 taxable units: cash 150, margin 100",
                      " ".join(r["advisory"].split()))

    @rule("CA-PLAN-01")
    def test_tj0448_one_account_one_listing_unchanged(self):
        rows = [_row("2026-01-05", "ZZX.TO", 100, 20.0, settle="2026-01-06"),
                _row("2026-10-22", "ZZX.TO", 50, 11.0, settle="2026-10-23")]
        r = _radar_map("canada", rows, None, "2026-10-29")["ZZX.TO"]
        self.assertIn("Selling the FULL position at a loss is fine now",
                      " ".join(r["advisory"].split()))


class _Canon:
    """buy-check's canonicaliser with a SIMILAR group (ticker.map)."""
    similar = {"ZZGA.TO": {"ZZGB.TO"}, "ZZGB.TO": {"ZZGA.TO"}}
    bare_listings = ()

    def __call__(self, t):
        return t.strip().upper()


class TestSimilarViolationCaveat(unittest.TestCase):
    """TJ-0449: a fund ticker.map calls SIMILAR was bought back inside
    a loss's window (a VIOLATION): sell-check of its look-alike said
    plain SAFE, though the purchase is inside a loss sale's window."""

    @rule("CA-SL-20")
    def test_tj0449_sell_check_names_the_buy(self):
        import taxjson.bin.taxjson_run as R
        rows = [_row("2026-04-01", "ZZGA.TO", 200, 20.0, settle="2026-04-02"),
                _row("2026-04-01", "ZZGB.TO", 100, 20.0, settle="2026-04-02"),
                _row("2026-10-05", "ZZGB.TO", -100, 15.0, settle="2026-10-06"),
                _row("2026-10-20", "ZZGB.TO", 50, 15.0, settle="2026-10-21")]
        radar = _radar_map("canada", rows, None, "2026-10-29")
        self.assertEqual(radar["ZZGB.TO"]["category"], "VIOLATION")
        self.assertEqual(radar["ZZGB.TO"]["recent_buy"],
                         {"account": "margin", "date": "2026-10-20"})
        lines = R._similar_lines(radar, _Canon(), "ZZGA.TO", "ZZGA.TO",
                                 "sell", False)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("ZZGB.TO: VIOLATION — bought within the past 30 days "
                      "('margin', 2026-10-20)", lines[0])


class TestBriefNoteCount(unittest.TestCase):
    """TJ-0451: the radar's brief counted every note as "warn-only
    note(s) to check by hand", the plain informational ones included."""

    @rule("CA-PLAN-01")
    def test_tj0451_no_by_hand_line_for_plain_notes(self):
        taxable = [_row("2026-01-07", "ZZC.TO", 100, 20.0, settle="2026-01-08")]
        sheltered = [_row("2026-10-15", "ZZC.TO", 20, 20.0, acct="tfsa",
                          settle="2026-10-16"),
                     _row("2026-10-22", "ZZC.TO", -20, 19.0, acct="tfsa",
                          settle="2026-10-23")]
        out = _radar_main("canada", taxable, sheltered, "2026-10-29",
                          ("--brief",))
        self.assertIn("CAUTION", out)
        self.assertNotIn("warn-only note", out)

    @rule("US-PLAN-02")
    def test_tj0451_a_by_hand_note_still_counts(self):
        # A long call bought in the last 30 days: the US engine only
        # flags it (call_vs_share_loss), so it is checked by hand.
        taxable = [_row("2026-07-01", "ZZK.US", 100, 50.0, currency="USD"),
                   _row("2026-10-22", "ZZK261218C00045000.US", 1, 2.0,
                        currency="USD")]
        out = _radar_main("usa", taxable, None, "2026-10-29",
                          ("--brief", "--all"))
        self.assertIn("1 warn-only note(s) to check by hand", out)


class TestPerUnitDenial(unittest.TestCase):
    """TJ-0559 / TJ-0560: buy-check's "about X of it per unit" divided
    every in-window loss by every unit sold, and in the US counted a
    short-cover loss a buy cannot replace."""

    @rule("US-PLAN-06")
    def test_tj0559_us_short_cover_loss_left_out(self):
        rows = [_row("2026-07-30", "ZZQ.US", 100, 1.0, currency="USD"),
                _row("2026-10-07", "ZZQ.US", -100, 0.8, currency="USD"),
                _row("2026-10-14", "ZZQ.US", -100, 1.0, currency="USD"),
                _row("2026-10-21", "ZZQ.US", 100, 1.6, currency="USD")]
        radar = _radar_map("usa", rows, None, "2026-10-28")
        r = radar["ZZQ.US"]
        self.assertAlmostEqual(r["recent_loss"], 20.0, 2)
        self.assertAlmostEqual(r["recent_loss_qty"], 100.0, 2)
        self.assertAlmostEqual(r["recent_loss_per_unit"], 0.20, 4)
        # The long loss's re-entry (10-07 + 30 = 11-06, a Friday: the
        # first trading day after it), not the short cover's.
        self.assertEqual(r["clears_at"], "2026-11-09")
        self.assertTrue(r["short_cover_loss"])
        out, code = _check("cmd_buy_check", radar, ["ZZQ.US"], usa=True)
        self.assertIn("UNSAFE", out)
        self.assertIn("about 0.20 USD of it per unit",
                      " ".join(out.split()))

    @rule("CA-PLAN-01")
    def test_tj0560_two_sales_one_unit_replaces_a_unit_of_each(self):
        rows = [_row("2026-07-30", "ZZR.TO", 100, 1.0, settle="2026-07-31"),
                _row("2026-10-07", "ZZR.TO", -100, 0.8, settle="2026-10-08"),
                _row("2026-10-09", "ZZR.TO", 100, 0.8, settle="2026-10-13"),
                _row("2026-10-21", "ZZR.TO", -100, 0.6, settle="2026-10-22")]
        radar = _radar_map("canada", rows, None, "2026-10-28")
        r = radar["ZZR.TO"]
        self.assertAlmostEqual(r["recent_loss"], 40.0, 2)
        self.assertAlmostEqual(r["recent_loss_qty"], 200.0, 2)
        self.assertAlmostEqual(r["recent_loss_per_unit"], 0.40, 4)
        self.assertIsNone(r["recent_loss_per_unit_qty"])
        out, _code = _check("cmd_buy_check", radar, ["ZZR.TO"])
        self.assertIn("about 0.40 CAD of it per unit",
                      " ".join(out.split()))


class TestHarvestOtherListing(unittest.TestCase):
    """TJ-0450: `harvest ZZX.US` said "No open positions for ZZX.US"
    while tobase.map books it as ZZX.TO; buy-check / sell-check resolve
    it."""

    @rule("CA-PLAN-01")
    def test_tj0450_the_booked_row_is_shown(self):
        from taxjson.bin.taxjson_harvest import main as harvest_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            g = root / "margin_gains_wash.json"
            g.write_text(json.dumps({
                "summary": {"year": 2026}, "transactions": [],
                "inventory": [{"symbol": "ZZX.TO", "qty": 50,
                               "total_cost": 765.49,
                               "position_start_date": "2026-04-01",
                               "last_acq_date": "2026-04-01"}]}))
            mp = root / "ticker.map"
            mp.write_text("TOBASE ZZX.US ZZX.TO\n")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = harvest_main(
                    [str(g), "--no-ibkr", "--country", "canada",
                     "--symbol", "ZZX.US", "--ticker-map", str(mp)],
                    fetchers=[lambda rem: {s: (12.0, "fake") for s in rem}])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("ZZX.TO", out.getvalue())
        self.assertNotIn("No open positions", out.getvalue())
        self.assertIn("ZZX.US is booked as ZZX.TO",
                      " ".join(err.getvalue().split()))


class TestRebuyDetailsWording(unittest.TestCase):
    """TJ-0453: rebuy --details said REBUY ON is "wash-radar's CLEARS";
    for a VIOLATION the radar's CLEARS is the last day to sell."""

    @rule("CA-PLAN-05")
    def test_tj0453_violation_clears_is_the_sell_by(self):
        from taxjson.lib import rebuy as RB
        rows = [_row("2026-01-05", "ZZX.TO", 100, 20.0, settle="2026-01-06"),
                _row("2026-05-29", "ZZX.TO", -100, 15.0, settle="2026-06-01")]
        radar = _radar_map("canada", rows, None, "2026-06-02")
        text = " ".join(RB.render(_rebuy(radar, "2026-06-02"), "CAD",
                                  details=True).text().split())
        self.assertNotIn("(wash-radar's CLEARS, buy-check's safe-from "
                         "date)", text)
        self.assertIn("wash-radar's CLEARS for a COOLING or BLOCKED row; "
                      "a VIOLATION's CLEARS is its last day to sell", text)


_GAINS_TWO_DENIALS = {
    "summary": {"year": 2026},
    "inventory": [],
    "transactions": [
        {"date": "2026-08-05", "symbol": "ZZQ.TO", "qty": 20,
         "proceeds": 564.17, "cost": 966.954, "raw_gain": -402.784,
         "gain": 0.0, "disallowed_amount": 402.784,
         "is_wash_sale": True},
        {"date": "2026-11-20", "symbol": "ZZR.TO", "qty": 10,
         "proceeds": 1953.0, "cost": 2017.714, "raw_gain": -64.714,
         "gain": 0.0, "disallowed_amount": 64.714,
         "is_wash_sale": True}]}


class TestWashSalesTotalIsTheRows(unittest.TestCase):
    """TJ-0428: wash-sales printed DENIED 467.50 under rows that add to
    467.49 (sum's RETURN row denies 467.49): the total was the engine's
    unrounded figure rounded once."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name) / "p"
        (self.root / "work").mkdir(parents=True)
        (self.root / "inputs" / "margin").mkdir(parents=True)
        (self.root / "taxjson.toml").write_text(_toml(year=2026))
        (self.root / "work" / "margin_gains_wash.json").write_text(
            json.dumps(_GAINS_TWO_DENIALS))

    def test_tj0428_the_total_adds_the_rows(self):
        r = tj(self.root, "wash-sales", check=False)
        text = " ".join(r.stdout.split())
        self.assertIn("402.78", text, r.stderr)
        self.assertIn("64.71", text)
        self.assertIn("DENIED: 467.49 CAD over 2 superficial loss(es)",
                      text)
        self.assertNotIn("467.50", text)
        j = json.loads(tj(self.root, "wash-sales", "--json",
                          check=False).stdout)
        self.assertEqual(j["totals"]["denied"], 467.49)
        self.assertEqual(j["totals"]["denied_unrounded"], 467.5)
        d = " ".join(tj(self.root, "wash-sales", "--details",
                        check=False).stdout.split())
        self.assertIn("the gains files' unrounded total denied is 467.50 "
                      "(-0.01 on the DENIED total)", d)


class TestOptionReplacementOneLine(unittest.TestCase):
    """TJ-0474: carryover, audit and wash-sales --explain rebuild the
    books and print the engine's warn-only flags; the one-line view cut
    them at their first colon: "Warning: option-replacement (warn-only,
    numbers unchanged)", naming no loss."""

    LINE = ("warning: option-replacement (warn-only, numbers unchanged): "
            "ZZA.US loss -1,000.00 on 2026-06-01 has ZZA261218C00045000.US "
            "acquired 2026-06-08 in the ±30d window; under IRS §1091 "
            "('option to acquire') up to 100 of the 100 shares' loss "
            "(-1,000.00) would be denied (1 contract(s) x 100 shares, each "
            "used once) [call_vs_share_loss]")
    LOT = ("warning: opening-balance lot (warn-only, numbers unchanged): "
           "ZZA.US loss -1,000.00 on 2026-06-01: the opening balance dates "
           "1 ZZA261218C00045000.US as bought 2026-06-08, inside the ±30d "
           "window; an opening balance is never a replacement in the books, "
           "but that purchase was real, so under IRS §1091 the loss may be "
           "a wash sale — review it by hand [opening_lot_vs_loss]")

    def test_tj0474_names_the_loss_and_the_command(self):
        from taxjson.lib.stage_msg import console_lines
        self.assertEqual(
            console_lines(self.LINE, width_=120, concise=True),
            ["Warning: ZZA.US loss on 2026-06-01: ZZA261218C00045000.US "
             "may replace it (warn-only) — `tjs wash-sales --details`"])
        self.assertEqual(
            console_lines(self.LOT, width_=120, concise=True),
            ["Warning: ZZA.US loss on 2026-06-01: ZZA261218C00045000.US, "
             "an opening lot, may replace it — `tjs wash-sales --details`"])
        # --details and captured output keep the whole line.
        self.assertEqual(console_lines(self.LINE, width_=0), [self.LINE])


if __name__ == "__main__":
    unittest.main()
