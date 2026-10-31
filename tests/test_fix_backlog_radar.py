"""Backlog group `radar` (wash-radar, rebuy, buy-check, sell-check,
watch): regressions for TJ-0023, TJ-0076, TJ-0079, TJ-0104, TJ-0121,
TJ-0122, TJ-0137, TJ-0141, TJ-0156, TJ-0184 and TJ-0186. Synthetic books
only (made-up ZZ tickers); every radar run is in process with a fixed
--date, and buy-check / sell-check read a radar built that way."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from tax_rules import rule


def _row(d, sym, qty, px, acct="margin", settle=None, currency="CAD"):
    return {"action": "BUYSELL", "date": d, "date_settle": settle or d,
            "time": "10:00:00", "symbol": sym, "quantity": qty,
            "price": px, "net_amount": -qty * px, "currency": currency,
            "account": acct, "commission": 0.0,
            "id": f"{acct}-{sym}-{d}-{qty}"}


def _radar_main(country, taxable, sheltered=None, as_of="2026-10-25",
                extra=()):
    """The radar run in process: (stdout, the --json document or None)."""
    from taxjson.bin import taxjson_wash_radar as R
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d) / "margin_base.json"
        tp.write_text(json.dumps({"transactions": taxable}))
        argv = ["taxjson-wash-radar", "--country", country, "--date",
                as_of, "--taxable", str(tp), *extra]
        if sheltered:
            sp = Path(d) / "sheltered_base.json"
            sp.write_text(json.dumps({"transactions": sheltered}))
            argv += ["--sheltered", str(sp)]
        out, old = io.StringIO(), sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                R.main()
        finally:
            sys.argv = old
    return out.getvalue()


def _radar(country, taxable, sheltered=None, as_of="2026-10-25",
           cleared=7):
    """The radar's --json document, flattened per ticker."""
    from taxjson.bin.taxjson_watch import flatten_radar
    return flatten_radar(json.loads(_radar_main(
        country, taxable, sheltered, as_of,
        ("--all", "--json", "--cleared-days", str(cleared)))))


def _check(cmd_name, radar, symbols, usa=False, last_loss=None,
           details=False):
    """buy-check / sell-check against a radar built above."""
    import taxjson.bin.taxjson_run as R
    from taxjson.lib.core import is_option_symbol, parse_option_underlying

    def canon(t):
        t = t.strip().upper()
        if is_option_symbol(t):
            return parse_option_underlying(t) or t
        return t
    buf = io.StringIO()
    code = 0
    with patch.object(R, "_wash_class_context",
                      return_value=(radar, canon, last_loss or {})), \
            patch.object(R, "_radar_country_is_usa", return_value=usa):
        try:
            with contextlib.redirect_stdout(buf):
                getattr(R, cmd_name)(Namespace(
                    dir=".", symbol=list(symbols), json=False,
                    details=details))
        except SystemExit as e:
            code = e.code or 0
    return buf.getvalue(), code


class TestNoteContinuation(unittest.TestCase):
    """TJ-0184: a `- ` item's Info note wraps under the note, never with
    a second `-` bullet."""

    def test_note_continuation_has_no_bullet(self):
        from taxjson.lib.wash_scope import advisory_lines
        adv = ("ZZB.TO: CAUTION: a loss sale today is NOT superficial. "
               "NOTE: SHELTERED 'tfsa' bought 2026-10-15 but holds none "
               "of it now — that leg re-arms only if an affiliated "
               "account re-buys within 30 days AFTER your sale.")
        lines = advisory_lines(adv, 70, "- ", "  ")
        note = [i for i, ln in enumerate(lines) if "SHELTERED" in ln][0]
        self.assertTrue(lines[note].startswith("- "))
        self.assertGreater(len(lines), note + 1)
        for ln in lines[note + 1:]:
            self.assertFalse(ln.startswith("-"), lines)
            self.assertTrue(ln.startswith("    "), lines)


def _project(td, rows, country="canada", year=2026):
    """A project folder with only work/margin_base.json (the radar's
    input) and its taxjson.toml."""
    root = Path(td) / "proj"
    (root / "work").mkdir(parents=True)
    cur = "USD" if country == "usa" else "CAD"
    (root / "taxjson.toml").write_text(
        f'[settings]\ncountry = "{country}"\nyear = {year}\n'
        f'base_currency = "{cur}"\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": rows}))
    return root


class TestWashRadarFlags(unittest.TestCase):
    """TJ-0141: --help says what --all adds (the OTHER rows); TJ-0156:
    --verbose is the full report, as --details."""

    def _parser(self):
        import taxjson.bin.taxjson_run as R
        _p, sub = R._build_parser("taxjson")
        return sub.choices["wash-radar"]

    def test_all_help_names_the_other_rows(self):
        text = " ".join(self._parser().format_help().split())
        self.assertNotIn("Include CLEAR (no-risk) positions too", text)
        self.assertIn("Also the OTHER rows", text)
        self.assertIn("the default view already has the CLEAR rows", text)

    def test_verbose_is_the_details_view(self):
        import taxjson.bin.taxjson_run as R
        rows = [_row("2026-08-03", "ZZB.TO", 100, 20.0)]
        seen = {}
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, rows)
            for flags in ((), ("--verbose",), ("--details",)):
                args = self._parser().parse_args(list(flags))
                args.dir = str(root)
                with patch.object(R, "_exec_tool",
                                  side_effect=lambda c, **k: c) as ex:
                    R.cmd_wash_radar(args)
                seen[flags] = ex.call_args[0][0]
        self.assertIn("--brief", seen[()])
        self.assertNotIn("--brief", seen[("--verbose",)])
        self.assertNotIn("--brief", seen[("--details",)])


class TestAdvisoryMoney(unittest.TestCase):
    """TJ-0121: an advisory's amount is printed as the tables print money
    (thousands separators, no '$'), the base currency after it."""

    def test_canada_blocked_amounts(self):
        book = [_row("2026-06-01", "ZZA.TO", 200, 20.0),
                _row("2026-10-14", "ZZA.TO", -100, 10.0)]
        r = _radar("canada", book, as_of="2026-10-25")["ZZA.TO"]
        self.assertEqual(r["category"], "BLOCKED", r["advisory"])
        self.assertIn("Recent loss of 1,000.00 CAD on 2026-10-14",
                      r["advisory"])
        self.assertIn("10.00 CAD of it for each unit", r["advisory"])
        self.assertNotIn("$", r["advisory"])

    def test_us_washed_amounts(self):
        book = [_row("2026-08-03", "ZZG.US", 60, 50.0, currency="USD"),
                _row("2026-10-20", "ZZG.US", -60, 30.0, currency="USD"),
                _row("2026-10-22", "ZZG.US", 30, 30.0, currency="USD")]
        r = _radar("usa", book, as_of="2026-10-25")["ZZG.US"]
        self.assertEqual(r["category"], "WASHED", r["advisory"])
        self.assertIn("the loss of 1,200.00 USD on 2026-10-20",
                      r["advisory"])
        self.assertIn("600.00 USD disallowed", r["advisory"])
        self.assertNotIn("$", r["advisory"])


class TestClaimedLossBriefLine(unittest.TestCase):
    """TJ-0023: a WASHED row whose loss is a .tt ALLOWLOSS filing position
    says so in the default view, as --details and --json do."""

    _BOOK = [_row("2026-08-03", "ZZG.US", 60, 50.0, currency="USD"),
             _row("2026-10-20", "ZZG.US", -60, 45.0, currency="USD"),
             _row("2026-10-22", "ZZG.US", 30, 45.0, currency="USD")]

    def _brief(self, overrides):
        with tempfile.TemporaryDirectory() as d:
            extra = ["--brief"]
            if overrides:
                lo = Path(d) / "loss_overrides.json"
                lo.write_text(json.dumps([{
                    "account": "margin", "date": "2026-10-20",
                    "symbol": "ZZG.US", "reason": "synthetic test",
                    "where": "inputs/margin/al.tt:1"}]))
                extra += ["--loss-overrides", str(lo)]
            return _radar_main("usa", self._BOOK, as_of="2026-10-25",
                               extra=extra)

    @rule("US-WASH-25")
    def test_claimed_position_is_not_called_disallowed(self):
        text = self._brief(True)
        self.assertIn("WASHED: ZZG.US: the loss is claimed as your filing "
                      "position (ALLOWLOSS)", text)
        self.assertNotIn("the loss is disallowed", text)
        for ln in text.splitlines():
            self.assertLessEqual(len(ln), 100, ln)

    @rule("US-WASH-25")
    def test_a_washed_loss_keeps_its_line(self):
        text = self._brief(False)
        self.assertIn("WASHED: ZZG.US: the loss is disallowed; no sale "
                      "undoes it", text)


def _rebuy(radar, as_of, country="canada"):
    from taxjson.lib import rebuy as RB
    from taxjson.lib.ticker_map import underlying_of
    return RB.build(radar, as_of, country,
                    underlying=lambda s: underlying_of(s, {}))


class TestTradeMadeTodayCounts(unittest.TestCase):
    """TJ-0076: in a settle-date project a buy made today (Friday) that
    settles Monday is acquired and held from its trade date, as the
    TAXABLE/SHELTERED columns already count it: a registered account's
    buy makes the taxable position LOCKED, a taxable rebuy after a loss
    is a VIOLATION, and rebuy lists the loss as bought back."""

    _TAX = [_row("2026-06-01", "ZZA.TO", 100, 50.0),
            _row("2026-10-13", "ZZA.TO", -100, 40.0, settle="2026-10-14"),
            _row("2026-10-23", "ZZA.TO", 40, 41.0, settle="2026-10-26"),
            _row("2026-01-07", "ZZH.TO", 100, 60.0)]
    _SHL = [_row("2026-10-23", "ZZH.TO", 25, 50.0, acct="tfsa",
                 settle="2026-10-26")]

    @rule("CA-PLAN-01")
    def test_radar_rebuy_and_sell_check_on_the_trade_date(self):
        radar = _radar("canada", self._TAX, self._SHL, as_of="2026-10-23")
        a, h = radar["ZZA.TO"], radar["ZZH.TO"]
        self.assertEqual(a["category"], "VIOLATION", a["advisory"])
        # 2026-10-14 + 30 settles on Fri 11-13: the last trade is Thu.
        self.assertEqual(a["clears_at"], "2026-11-12")
        self.assertEqual(a["denied_qty"], 40.0)
        self.assertEqual(h["category"], "LOCKED", h["advisory"])
        self.assertEqual(h["at_risk_qty"], 25.0)
        row = {r["ticker"]: r for r in _rebuy(radar, "2026-10-23")["rows"]}
        self.assertEqual(row["ZZA.TO"]["status"], "rebought")
        self.assertEqual(row["ZZA.TO"]["rescue_by"], "2026-11-12")
        out, code = _check("cmd_sell_check", radar, ["ZZH.TO"])
        self.assertIn("ZZH.TO: PARTIAL", out)
        self.assertEqual(code, 1)
        out, code = _check("cmd_sell_check", radar, ["ZZA.TO"])
        self.assertIn("ZZA.TO: ACTION", out)

    @rule("CA-PLAN-01")
    def test_a_sale_made_today_ends_the_backing(self):
        # The tfsa sells its 25 today (settling Monday): it no longer
        # holds them, as its SHELTERED column already shows.
        shl = self._SHL[:1] + [_row("2026-10-26", "ZZH.TO", -25, 50.0,
                                    acct="tfsa", settle="2026-10-27")]
        radar = _radar("canada", self._TAX, shl, as_of="2026-10-26")
        self.assertNotEqual(radar["ZZH.TO"]["category"], "LOCKED",
                            radar["ZZH.TO"]["advisory"])


class TestReentryIsATradingDay(unittest.TestCase):
    """TJ-0186: REBUY ON (and the radar's re-entry dates) is the first
    trading day a buy no longer replaces the loss — never a weekend, and
    in Canada the first day whose buy settles after the window."""

    @rule("CA-PLAN-05")
    def test_canada_first_trade_that_settles_after_the_window(self):
        from datetime import date
        from taxjson.lib.market_calendar import is_trading_day
        book = [_row("2026-04-01", "ZZD.TO", 100, 30.0),
                # settles Thu 10-01: the window closes Sat 10-31
                _row("2026-09-30", "ZZD.TO", -100, 20.0,
                     settle="2026-10-01"),
                _row("2026-04-01", "ZZE.TO", 100, 30.0),
                # settles Mon 10-05: the window closes Wed 11-04
                _row("2026-10-02", "ZZE.TO", -100, 20.0,
                     settle="2026-10-05")]
        radar = _radar("canada", book, as_of="2026-10-25")
        self.assertEqual(radar["ZZD.TO"]["category"], "COOLING")
        # Fri 10-30 settles Mon 11-02, after the window (it was Sun 11-01).
        self.assertEqual(radar["ZZD.TO"]["rebuy_at"], "2026-10-30")
        self.assertEqual(radar["ZZD.TO"]["clears_at"], "2026-10-30")
        # The window's last day settles Thu 11-05 (it was 11-05).
        self.assertEqual(radar["ZZE.TO"]["rebuy_at"], "2026-11-04")
        for t in ("ZZD.TO", "ZZE.TO"):
            d = radar[t]["rebuy_at"]
            self.assertTrue(is_trading_day(d, "CAD"), d)
            self.assertLess(date.fromisoformat(d).weekday(), 5)
        rows = {r["ticker"]: r for r in _rebuy(radar, "2026-10-25")["rows"]}
        self.assertEqual(rows["ZZD.TO"]["rebuy_at"], "2026-10-30")
        self.assertEqual(rows["ZZD.TO"]["days_left"], 5)
        # On that day the loss blocks nothing: no COOLING row, and rebuy
        # lists it as cleared.
        radar = _radar("canada", book, as_of="2026-10-30")
        self.assertNotEqual(radar["ZZD.TO"]["category"], "COOLING")
        doc = _rebuy(radar, "2026-10-30")
        self.assertNotIn("ZZD.TO", [r["ticker"] for r in doc["rows"]])
        self.assertEqual([c["ticker"] for c in doc["cleared"]], ["ZZD.TO"])

    @rule("US-PLAN-06")
    def test_us_first_trading_day_from_trade_date_plus_31(self):
        book = [_row("2026-04-01", "ZZD.US", 100, 30.0, currency="USD"),
                _row("2026-10-01", "ZZD.US", -100, 20.0, currency="USD")]
        radar = _radar("usa", book, as_of="2026-10-25")
        r = radar["ZZD.US"]
        self.assertEqual(r["category"], "COOLING", r["advisory"])
        # 10-01 + 31 is Sun 11-01: the first trading day is Mon 11-02.
        self.assertEqual(r["rebuy_at"], "2026-11-02")
        self.assertEqual(r["clears_at"], "2026-11-02")
        # Still open on Sat 10-31 / Sun 11-01 (nothing can be bought).
        doc = _rebuy(_radar("usa", book, as_of="2026-11-01"), "2026-11-01",
                     "usa")
        self.assertEqual([x["rebuy_at"] for x in doc["rows"]],
                         ["2026-11-02"])


class TestBuyCheckViolationDate(unittest.TestCase):
    """TJ-0137: buy-check gives a VIOLATION's re-entry date (rebuy's
    REBUY ON), not a pointer to wash-radar, whose CLEARS for it is the
    sell-by date."""

    _BOOK = [_row("2026-04-01", "ZZD.TO", 100, 30.0),
             _row("2026-09-30", "ZZD.TO", -100, 20.0, settle="2026-10-01"),
             _row("2026-10-15", "ZZD.TO", 50, 20.0, settle="2026-10-16")]

    @rule("CA-PLAN-05")
    def test_violation_names_the_reentry_date(self):
        radar = _radar("canada", self._BOOK, as_of="2026-10-25")
        r = radar["ZZD.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        self.assertEqual(r["clears_at"], "2026-10-29")       # sell by
        self.assertEqual(r["rebuy_at"], "2026-10-30")
        out, code = _check("cmd_buy_check", radar, ["ZZD.TO"])
        self.assertEqual(code, 1)
        self.assertIn("ZZD.TO: UNSAFE", out)
        self.assertIn("Safe to buy from 2026-10-30.", " ".join(out.split()))
        self.assertNotIn("2026-10-29", out)
        self.assertNotIn("see `tjs wash-radar`", out)

    @rule("CA-PLAN-05")
    def test_radar_legend_says_violation_clears_is_a_sell_by(self):
        text = _radar_main("canada", self._BOOK, as_of="2026-10-25",
                           extra=["--brief"])
        self.assertIn("STATUS: what a loss sale or a buy does today; "
                      "CLEARS: window closes (VIOLATION: last day to "
                      "sell).\n", text)


class TestRescueIsPerLoss(unittest.TestCase):
    """TJ-0079: a ticker with one loss denied for good (its last rescue
    trade date has passed) and one still rescuable: rebuy says sell to
    keep only the rescuable one, and calls the other's denial final."""

    # As of Wed 2026-07-01 (Canada Day). The 06-02 loss's window closes
    # Thu 07-02: its last rescue trade date was Tue 06-30 (settling
    # 07-02), and a buy first settles after the window on Thu 07-02.
    _BOOK = [_row("2026-01-08", "ZZJ.TO", 100, 20.0),
             _row("2026-06-01", "ZZJ.TO", -50, 15.0, settle="2026-06-02"),
             _row("2026-06-09", "ZZJ.TO", -50, 14.0, settle="2026-06-10"),
             _row("2026-06-12", "ZZJ.TO", 30, 14.0, settle="2026-06-15")]

    @rule("CA-PLAN-05")
    def test_rebuy_keeps_only_the_rescuable_loss(self):
        from taxjson.lib import rebuy as RB
        radar = _radar("canada", self._BOOK, as_of="2026-07-01")
        r = radar["ZZJ.TO"]
        self.assertEqual(r["category"], "VIOLATION", r["advisory"])
        by_date = {l["trade_date"]: l for l in r["losses"]}
        self.assertEqual(by_date["2026-06-01"]["rescue_by"], "2026-06-30")
        self.assertTrue(by_date["2026-06-01"]["rescue_passed"])
        self.assertEqual(by_date["2026-06-09"]["rescue_by"], "2026-07-09")
        self.assertFalse(by_date["2026-06-09"]["rescue_passed"])
        doc = _rebuy(radar, "2026-07-01")
        row, = doc["rows"]
        self.assertEqual(row["status"], "rebought")
        self.assertEqual(row["rescue_by"], "2026-07-09")
        self.assertEqual(row["rescues"], ["2026-06-09"])
        self.assertEqual(row["final"], ["2026-06-01"])
        acts = " ".join(RB._acts(doc, "canada"))
        self.assertIn("ZZJ.TO: already bought back; sell it by 2026-07-09 "
                      "to keep the 2026-06-09 loss", acts)
        self.assertIn("1 loss(es) already denied (ZZJ.TO)", acts)
        text = RB._row_detail(row, False)
        self.assertIn("2026-06-01 margin 50 units, loss 250.00, 30 units "
                      "denied for good (the last trade date to rescue it, "
                      "2026-06-30, has passed)", text)
        self.assertIn("2026-06-09 margin 50 units, loss 300.00, 30 units "
                      "denied as things stand", text)
        self.assertIn("sell the replacement by 2026-07-09 (last trade "
                      "date) to keep the 2026-06-09 loss.", text)

    @rule("CA-PLAN-05")
    def test_a_single_rescuable_loss_reads_as_before(self):
        from taxjson.lib import rebuy as RB
        radar = _radar("canada", self._BOOK, as_of="2026-06-29")
        doc = _rebuy(radar, "2026-06-29")
        row, = doc["rows"]
        self.assertEqual((row["status"], row["final"]), ("rebought", []))
        self.assertEqual(row["rescue_by"], "2026-06-30")
        acts = " ".join(RB._acts(doc, "canada"))
        self.assertIn("sell it by 2026-06-30 to keep the loss", acts)
        self.assertNotIn("already denied", acts)


def _cmd_rebuy(radar, symbols, as_of="2026-10-25", as_json=False):
    """`tjs rebuy SYMBOL...` against a radar built above: stdout."""
    import taxjson.bin.taxjson_run as R

    def canon(t):
        return t.strip().upper()
    buf = io.StringIO()
    with patch.object(R, "_wash_class_context",
                      return_value=(radar, canon, {})), \
            patch.object(R, "_radar_country_is_usa", return_value=False), \
            patch.object(R, "_base_currency", return_value="CAD"), \
            contextlib.redirect_stdout(buf):
        R.cmd_rebuy(Namespace(dir=".", symbol=list(symbols), date=as_of,
                              json=as_json, details=False))
    return buf.getvalue()


class TestRebuyUnmatchedSymbol(unittest.TestCase):
    """TJ-0104: `rebuy SYMBOL` that matches no row says so, with the
    count of the other tickers that have an open window — never the
    all-clear sentence."""

    _BOOK = [_row("2026-04-01", "ZZD.TO", 100, 30.0),
             _row("2026-09-30", "ZZD.TO", -100, 20.0, settle="2026-10-01"),
             _row("2026-04-01", "ZZQ.TO", 100, 30.0),
             _row("2026-04-01", "ZZR.TO", 100, 30.0),
             _row("2026-05-01", "ZZR.TO", -100, 20.0, settle="2026-05-04")]

    def setUp(self):
        self.radar = _radar("canada", self._BOOK, as_of="2026-10-25")

    def test_unknown_symbol(self):
        out = _cmd_rebuy(self.radar, ["NOSUCH"])
        self.assertNotIn("No ticker sold at a loss", out)
        self.assertIn("No loss of NOSUCH has an open repurchase window.",
                      out)
        self.assertIn("! NOSUCH: not in the books (1 other ticker(s) have "
                      "an open window) — tjs rebuy", out)

    def test_command_name_as_symbol(self):
        out = _cmd_rebuy(self.radar, ["wash-radar"])
        self.assertIn("! WASH-RADAR: not in the books (1 other ticker(s) "
                      "have an open window) — tjs rebuy", out)

    def test_symbol_in_the_books_without_a_window(self):
        out = _cmd_rebuy(self.radar, ["ZZQ.TO", "ZZR.TO"])
        self.assertIn("! ZZQ.TO: no loss with an open window (1 other "
                      "ticker(s) have one) — tjs rebuy", out)
        self.assertIn("! ZZR.TO: no loss with an open window", out)

    def test_a_match_beside_an_unmatched_one(self):
        out = _cmd_rebuy(self.radar, ["ZZD.TO", "NOSUCH"])
        self.assertIn("ZZD.TO  2026-09-30", out)
        self.assertIn("! NOSUCH: not in the books", out)
        self.assertNotIn("other ticker", out)
        doc = json.loads(_cmd_rebuy(self.radar, ["ZZD.TO", "NOSUCH"],
                                    as_json=True))
        self.assertEqual(doc["unmatched"],
                         [{"symbol": "NOSUCH", "in_books": False}])
        self.assertEqual(doc["others_open"], 0)

    def test_without_symbols_unchanged(self):
        out = _cmd_rebuy(_radar("canada", self._BOOK[2:3]), [])
        self.assertIn("No ticker sold at a loss has an open repurchase "
                      "window.", out)
        self.assertNotIn("! ", out)


class TestShortCoverWordingCanada(unittest.TestCase):
    """TJ-0122: buy-check / sell-check word a loss on covering a short
    as one, not as a loss sold."""

    _BOOK = [_row("2026-08-03", "ZZK.TO", -100, 10.0, settle="2026-08-04"),
             _row("2026-10-07", "ZZK.TO", 100, 12.0, settle="2026-10-08")]

    def setUp(self):
        self.radar = _radar("canada", self._BOOK, as_of="2026-10-25")
        self.assertEqual(self.radar["ZZK.TO"]["category"], "COOLING")

    def test_buy_check(self):
        out, code = _check("cmd_buy_check", self.radar, ["ZZK.TO"])
        flat = " ".join(out.split())
        self.assertIn("ZZK.TO: COOLING — a loss on covering a short "
                      "(2026-10-07) within the past 30 days; buying now "
                      "cancels it", flat)
        self.assertIn("30 days after that cover", flat)
        self.assertNotIn("a loss sold", flat)

    def test_sell_check(self):
        out, code = _check("cmd_sell_check", self.radar, ["ZZK.TO"])
        flat = " ".join(out.split())
        self.assertIn("ZZK.TO: COOLING — a recent loss on covering a short "
                      "(2026-10-07) is already in its cooling window", flat)
        self.assertNotIn("a recent loss sale", flat)

    def test_last_loss_line(self):
        import taxjson.bin.taxjson_run as R
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "margin_gains.json"
            p.write_text(json.dumps({"transactions": [
                {"symbol": "ZZK.TO", "qty": 100, "gain": -200.0,
                 "raw_gain": -200.0, "date": "2026-10-07",
                 "date_settle": "2026-10-08", "direction": "SHORT",
                 "account": "margin"},
                {"symbol": "ZZL.TO", "qty": 10, "gain": -20.0,
                 "raw_gain": -20.0, "date": "2026-10-07",
                 "date_settle": "2026-10-08", "account": "margin"}]}))
            ll = R._last_loss_by_class({"margin": str(p)},
                                       lambda t: t.strip().upper(), None,
                                       usa=False)
        self.assertEqual(ll["ZZK.TO"]["direction"], "SHORT")
        self.assertTrue(R._last_loss_line(ll["ZZK.TO"]).startswith(
            "last loss this tax year (covering a short): ZZK.TO settled "
            "2026-10-08 ("))
        self.assertTrue(R._last_loss_line(ll["ZZL.TO"]).startswith(
            "last loss sale this tax year: ZZL.TO settled 2026-10-08 ("))


class TestShortCoverRebuyLineUS(unittest.TestCase):
    """TJ-0122: the US rebuy line for a short-cover loss gives the date
    a new short sale stops replacing it, as buy-check does."""

    _BOOK = [_row("2026-08-03", "ZZP.US", -100, 10.0, currency="USD"),
             _row("2026-10-05", "ZZP.US", 100, 12.0, currency="USD"),
             _row("2026-10-12", "ZZP.US", 40, 12.0, currency="USD")]

    @rule("US-WASH-05")
    def test_rebuy_names_the_reshort_date(self):
        from taxjson.lib import rebuy as RB
        radar = _radar("usa", self._BOOK, as_of="2026-10-25")
        self.assertEqual(radar["ZZP.US"]["short_cover_loss"]
                         ["reshort_ok_from"], "2026-11-05")
        doc = _rebuy(radar, "2026-10-25", "usa")
        row, = doc["rows"]
        self.assertEqual(row["status"], "short-cover")
        self.assertEqual(row["reshort_ok_from"], "2026-11-05")
        acts = " ".join(RB._acts(doc, "usa"))
        self.assertIn("! ZZP.US: a short-cover loss; only a new short sale "
                      "before 2026-11-05 replaces it — tjs buy-check", acts)
        self.assertNotIn("31 days", acts)
        self.assertIn("A buy does not replace it; a new short sale before "
                      "2026-11-05 does.", RB._row_detail(row, True))


if __name__ == "__main__":
    unittest.main()
