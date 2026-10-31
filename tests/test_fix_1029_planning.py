"""The 2026-10-29 fix round, planning commands: buy-check, sell-check,
rebuy, wash-radar, harvest and wash-sales (backlog TJ-0350, TJ-0351,
TJ-0352, TJ-0243, TJ-0245, TJ-0247, TJ-0244, TJ-0246, TJ-0258,
TJ-0259, TJ-0413, TJ-0157). Synthetic data only: made-up tickers
(SYN.TO, ONE.TO, TWO.TO, ZZU.US) in throwaway projects."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from test_fix_backlog_radar import _radar, _radar_main, _rebuy, _row


def _toml(country="canada", *, sheltered=False, extra="", year=None):
    zone = "America/New_York" if country == "usa" else "America/Toronto"
    cur = "USD" if country == "usa" else "CAD"
    text = (f'[settings]\nlocal_timezone = "{zone}"\nyear = '
            f'{year or date.today().year}\ncountry = "{country}"\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n')
    if sheltered:
        text += '[accounts.rrsp]\ntype = "sheltered"\n'
    return text + extra


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


class TestMissingShelteredBookRefused(unittest.TestCase):
    """TJ-0350 (the audit's case TJ-0394): an RRSP bought SYN.TO 7 days
    ago, so a loss sale of the taxable SYN.TO is UNSAFE. With
    work/sheltered_base.json gone, sell-check said SAFE, exit 0, after
    an Info line. Decided 2026-10-29: sell-check, buy-check and rebuy
    refuse, exit 2, run `tjs run` first."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(_toml(sheltered=True))
        for acct, days in (("margin", 60), ("rrsp", 7)):
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "m.tt").write_text(
                f"BUYSELL {_ago(days)} 10:00:00 SYN.TO 10 CAD 10 100 0\n")
        tj(root, "run", "--no-input")
        cls.shelter = root / "work" / "sheltered_base.json"

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj0350_with_the_book_sell_check_is_unsafe(self):
        r = tj(self.root, "sell-check", "SYN.TO", "--json", check=False)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(json.loads(r.stdout)["results"][0]["verdict"],
                         "UNSAFE")

    def test_tj0350_without_it_every_check_refuses(self):
        saved = self.shelter.with_suffix(".saved")
        self.shelter.rename(saved)
        self.addCleanup(saved.rename, self.shelter)
        for args in (("sell-check", "SYN.TO", "--json"),
                     ("sell-check", "SYN.TO"),
                     ("buy-check", "SYN.TO", "--json"),
                     ("rebuy", "--json"),
                     ("rebuy", "SYN.TO")):
            with self.subTest(args=args):
                r = tj(self.root, *args, check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertEqual(r.stdout, "")
                err = " ".join(r.stderr.split())
                self.assertIn("Error: no sheltered_base.json in work/", err)
                self.assertIn("Run `tjs run` first", err)

    def test_tj0394_wash_sales_explain_refuses_too(self):
        # The explain replay lost the registered account's denial the
        # saved table still holds.
        saved = self.shelter.with_suffix(".saved")
        self.shelter.rename(saved)
        self.addCleanup(saved.rename, self.shelter)
        r = tj(self.root, "wash-sales", "--explain", check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no sheltered_base.json in work/", r.stderr)


class TestUnreadableMapExit2(unittest.TestCase):
    """TJ-0351 (the audit's case TJ-0405): a ticker.map that is not
    UTF-8 exited 1, the UNSAFE verdict's status; a problem line exits
    2. Both are an input to fix: exit 2."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(_toml())
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "m.tt").write_text(
            f"BUYSELL {_ago(60)} 10:00:00 SYN.TO 10 CAD 10 100 0\n")
        tj(root, "run", "--no-input")
        (root / "ticker.map").write_bytes(b"GLOBAL SYNOLD.US SYN.TO \xff\xfe\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj0351_unreadable_map_exits_2(self):
        for args in (("buy-check", "SYN.TO"), ("sell-check", "SYN.TO"),
                     ("rebuy", "--json")):
            with self.subTest(args=args):
                r = tj(self.root, *args, check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("ticker.map cannot be read", r.stderr)


class TestHarvestStrictMap(unittest.TestCase):
    """TJ-0352 (the audit's case TJ-0399): the books hold
    NEW270115C00010000.TO, built from the OLD contract held through
    `GLOBAL OLD.TO NEW.TO`. With the line broken (`GLOBAL OLD.TO`) the
    lenient loader dropped it, harvest quoted the NEW contract (5.00)
    instead of the OLD one held (0.50): a loss of 50 read as a gain of
    400, exit 0."""

    OLD, NEW = "OLD270115C00010000.TO", "NEW270115C00010000.TO"

    def _harvest(self, mapping):
        import contextlib
        import io
        import os
        from unittest import mock
        from taxjson.bin.taxjson_harvest import main as harvest
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)

            def book(sym):
                return {"summary": {"year": 2026}, "transactions": [],
                        "inventory": [{"symbol": sym, "qty": 1,
                                       "total_cost": 100,
                                       "currency": "CAD",
                                       "position_start_date":
                                           "2025-01-02"}]}
            (p / "margin_gains.json").write_text(json.dumps(book(self.NEW)))
            (p / "margin_raw_gains.json").write_text(
                json.dumps(book(self.OLD)))
            (p / "ticker.map").write_text(mapping)
            asked = []

            def fetch(rem):
                asked.extend(rem)
                return {s: (0.5 if s.startswith("OLD") else 5.0,
                            "synthetic", "CAD") for s in rem}
            out, err = io.StringIO(), io.StringIO()
            env = {k: v for k, v in os.environ.items()
                   if k != "TAXJSON_OFFLINE"}
            with mock.patch.dict(os.environ, env, clear=True), \
                    contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                rc = harvest([str(p / "margin_gains.json"), "--ticker-map",
                              str(p / "ticker.map"), "--options",
                              "--country", "canada", "--json", "--no-ibkr",
                              "--price-cache", str(p / "prices.json")],
                             fetchers=[lambda rem: {}],
                             option_fetchers=[fetch])
        return rc, asked, out.getvalue(), err.getvalue()

    def test_tj0352_valid_map_quotes_the_contract_held(self):
        rc, asked, out, _err = self._harvest("GLOBAL OLD.TO NEW.TO\n")
        self.assertEqual(rc, 0)
        self.assertEqual(asked, [self.OLD])

    def test_tj0352_malformed_map_quotes_no_other_contract(self):
        rc, asked, out, err = self._harvest("GLOBAL OLD.TO\n")
        self.assertEqual(rc, 0, err)
        self.assertEqual(asked, [])
        self.assertNotIn("400", out)
        self.assertIn("option position(s) omitted", " ".join(err.split()))


class TestCommandsRefuseMalformedMap(unittest.TestCase):
    """TJ-0352: `tjs harvest` and `tjs wash-radar` (whose SIMILAR groups
    lib/similar read past a dropped line) refuse a map with a problem
    line, exit 2, as `tjs run` and buy-check do."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(_toml())
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "inputs" / "margin" / "m.tt").write_text(
            f"BUYSELL {_ago(60)} 10:00:00 SYN.TO 10 CAD 10 100 0\n"
            f"BUYSELL {_ago(7)} 10:00:00 SYN.TO -5 CAD 5 25 0\n")
        (root / "ticker.map").write_text("SIMILAR SYN.TO SYNB.TO\n")
        tj(root, "run", "--no-input")
        (root / "ticker.map").write_text("SIMILAR SYN.TO\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj0352_each_command_refuses(self):
        for args in (("harvest", "--no-ibkr"), ("wash-radar",),
                     ("wash-radar", "--json"), ("wash-radar", "margin")):
            with self.subTest(args=args):
                r = tj(self.root, *args, check=False)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("Error: 1 ticker.map problem(s)", r.stderr)
                self.assertIn("ticker.map:1:", r.stderr)

    def test_tj0352_open_findings_kept_while_the_map_is_broken(self):
        from taxjson.lib import similar as SIM
        state = {"format": SIM.FORMAT, "country": "canada",
                 "findings": [{"loss_symbol": "SYN.TO",
                               "other_symbol": "SYNB.TO"}]}
        work = self.root / "work"
        (work / SIM.STATE).write_text(json.dumps(state))
        self.addCleanup((work / SIM.STATE).unlink)
        self.assertEqual(len(SIM.open_findings(self.root)), 1)
        self.assertTrue(SIM.map_problems(self.root))


class TestForwardViewSettlesTheSale(unittest.TestCase):
    """TJ-0243 (the audit's case TJ-0416): an RRSP bought SYN.TO on
    Wednesday 2026-10-14, settled Thursday 10-15. On Friday 11-13 a
    taxable loss sale settles Monday 11-16, 32 days after the buy: the
    loss stands (the engine allows it). The radar measured the sale from
    the Friday, said LOCKED, "selling before 2026-11-15" (a Sunday:
    10-15 + 31 on the calendar)."""

    _BOOK = [_row("2026-01-05", "SYN.TO", 10, 10)]
    _RRSP = [_row("2026-10-14", "SYN.TO", 10, 5, acct="rrsp",
                  settle="2026-10-15")]

    @rule("CA-PLAN-01")
    def test_tj0243_locked_clears_on_the_first_trading_day(self):
        row = _radar("canada", self._BOOK, self._RRSP,
                     as_of="2026-11-12")["SYN.TO"]
        self.assertEqual(row["category"], "LOCKED", row["advisory"])
        # Friday's sale settles Monday 11-16, past 10-15 + 30.
        self.assertEqual(row["clears_at"], "2026-11-13")
        self.assertIn("at a loss before 2026-11-13", row["advisory"])

    @rule("CA-PLAN-01")
    def test_tj0243_a_friday_sale_settles_monday(self):
        for d in ("2026-11-13", "2026-11-14", "2026-11-16"):
            with self.subTest(as_of=d):
                row = _radar("canada", self._BOOK, self._RRSP,
                             as_of=d)["SYN.TO"]
                self.assertNotEqual(row["category"], "LOCKED",
                                    row["advisory"])

    @rule("CA-PLAN-01")
    def test_tj0243_exitable_and_a_weekend_end(self):
        # A taxable buy settled 10-15: EXITABLE until Thursday 11-12.
        tax = self._BOOK + [_row("2026-10-14", "SYN.TO", 5, 5,
                                 settle="2026-10-15")]
        row = _radar("canada", tax, as_of="2026-11-12")["SYN.TO"]
        self.assertEqual(row["category"], "EXITABLE", row["advisory"])
        self.assertEqual(row["clears_at"], "2026-11-13")
        row = _radar("canada", tax, as_of="2026-11-13")["SYN.TO"]
        self.assertEqual(row["category"], "CLEAR", row["advisory"])
        # A buy settled Wednesday 10-21: + 30 is Friday 11-20, the
        # trade that settles then is Thursday's; Friday is clear (it
        # was Saturday 11-21).
        rrsp = [_row("2026-10-20", "SYN.TO", 10, 10, acct="rrsp",
                     settle="2026-10-21")]
        row = _radar("canada", self._BOOK, rrsp,
                     as_of="2026-10-20")["SYN.TO"]
        self.assertEqual((row["category"], row["clears_at"]),
                         ("LOCKED", "2026-11-20"))

    @rule("US-PLAN-01")
    def test_tj0243_us_clears_on_a_trading_day(self):
        # An IRA bought SYN.US on Thursday 2026-10-15: + 31 is Sunday
        # 11-15; the first trading day from it is Monday 11-16.
        book = [_row("2026-01-05", "SYN.US", 10, 10, currency="USD")]
        ira = [_row("2026-10-15", "SYN.US", 10, 5, acct="ira",
                    currency="USD")]
        row = _radar("usa", book, ira, as_of="2026-11-13")["SYN.US"]
        self.assertEqual((row["category"], row["clears_at"]),
                         ("LOCKED", "2026-11-16"))
        row = _radar("usa", book, ira, as_of="2026-11-16")["SYN.US"]
        self.assertNotEqual(row["category"], "LOCKED", row["advisory"])


class TestShelteredBuyMadeToday(unittest.TestCase):
    """TJ-0245 (the audit's case TJ-0415): as of 2026-10-20 an RRSP
    bought SYN.TO today, settling 10-21, and no taxable account holds
    it. The row said only "CLEAR: Held only in SHELTERED accounts",
    `sheltered_buy` null, while the settled-day case names the buy."""

    @rule("CA-PLAN-01")
    def test_tj0245_the_buy_made_today_is_named(self):
        rrsp = [_row("2026-10-20", "SYN.TO", 10, 10, acct="rrsp",
                     settle="2026-10-21")]
        other = [_row("2026-01-05", "OTHER.TO", 10, 10)]
        row = _radar("canada", other, rrsp, as_of="2026-10-20")["SYN.TO"]
        self.assertEqual(row["category"], "CLEAR", row["advisory"])
        self.assertEqual(row["sheltered_buy"]["account"], "rrsp")
        self.assertIn("that purchase can make a taxable loss",
                      row["advisory"])


class TestBuyCheckMoney(unittest.TestCase):
    """TJ-0244: buy-check's per-unit share of a recent loss printed
    "about $10.00 of it per unit" while the radar's advisory beside it
    said "10.00 CAD of it for each unit" (TJ-0121)."""

    def _per_unit(self, usa):
        from test_fix_backlog_radar import _check
        cur, sym = ("USD", "ZZA.US") if usa else ("CAD", "ZZA.TO")
        book = [_row("2026-06-01", sym, 200, 20.0, currency=cur),
                _row("2026-10-14", sym, -100, 10.0, currency=cur)]
        radar = _radar("usa" if usa else "canada", book, as_of="2026-10-25")
        self.assertEqual(radar[sym]["category"], "BLOCKED",
                         radar[sym]["advisory"])
        out, code = _check("cmd_buy_check", radar, [sym], usa=usa)
        self.assertEqual(code, 1, out)
        return " ".join(out.split())

    @rule("CA-PLAN-01")
    def test_tj0244_canada(self):
        out = self._per_unit(False)
        self.assertIn("about 10.00 CAD of it per unit", out)
        self.assertNotIn("$", out)

    @rule("US-PLAN-01")
    def test_tj0244_usa(self):
        out = self._per_unit(True)
        self.assertIn("about 10.00 USD of it per unit", out)
        self.assertNotIn("$", out)


class TestClaimedWashedTitle(unittest.TestCase):
    """TJ-0246: the US project of TJ-0023 (a ZZG.US loss on 2026-10-20,
    30 bought back 10-22, the sale claimed by a .tt ALLOWLOSS line).
    `wash-radar --details` titled its section "WASHED — wash sale
    (§1091): loss disallowed; no sale undoes it" over a row saying the
    books allow the loss."""

    _BOOK = [_row("2026-08-03", "ZZG.US", 60, 50.0, currency="USD"),
             _row("2026-10-20", "ZZG.US", -60, 45.0, currency="USD"),
             _row("2026-10-22", "ZZG.US", 30, 45.0, currency="USD")]

    def _run(self, claimed, *extra):
        with tempfile.TemporaryDirectory() as d:
            args = list(extra)
            if claimed:
                lo = Path(d) / "loss_overrides.json"
                lo.write_text(json.dumps([{
                    "account": "margin", "date": "2026-10-20",
                    "symbol": "ZZG.US", "reason": "synthetic test",
                    "where": "inputs/margin/al.tt:1"}]))
                args += ["--loss-overrides", str(lo)]
            return _radar_main("usa", self._BOOK, as_of="2026-10-25",
                               extra=args)

    @rule("US-WASH-25")
    def test_tj0246_a_claimed_section_says_so(self):
        text = self._run(True)
        self.assertIn("WASHED — wash sale (§1091) you claim as your filing "
                      "position (ALLOWLOSS): the books allow the loss (1)",
                      text)
        self.assertNotIn("loss disallowed", text)
        doc = json.loads(self._run(True, "--json"))
        title = {s["category"]: s["title"] for s in doc["sections"]}
        self.assertIn("you claim as your filing position", title["WASHED"])

    @rule("US-WASH-25")
    def test_tj0246_a_washed_section_keeps_its_title(self):
        text = self._run(False)
        self.assertIn("WASHED — wash sale (§1091): loss disallowed; no sale "
                      "undoes it (1)", text)


class TestRebuyKeepsAClosedFinalDenial(unittest.TestCase):
    """TJ-0247: the TJ-0079 book (ZZJ.TO losses on 06-01 and 06-09, 30
    bought back 06-12) as of Friday 2026-07-03: the 06-01 loss is denied
    for good (its rescue date 06-30 passed) and its re-entry date 07-02
    has passed too. rebuy showed only the 06-09 loss, and "sell it by
    07-09 to keep the loss", the final denial gone without a word."""

    _BOOK = [_row("2026-01-08", "ZZJ.TO", 100, 20.0),
             _row("2026-06-01", "ZZJ.TO", -50, 15.0, settle="2026-06-02"),
             _row("2026-06-09", "ZZJ.TO", -50, 14.0, settle="2026-06-10"),
             _row("2026-06-12", "ZZJ.TO", 30, 14.0, settle="2026-06-15")]

    @rule("CA-PLAN-05")
    def test_tj0247_the_final_denial_is_named(self):
        from taxjson.lib import rebuy as RB
        radar = _radar("canada", self._BOOK, as_of="2026-07-03")
        doc = _rebuy(radar, "2026-07-03")
        row, = doc["rows"]
        self.assertEqual((row["status"], row["sold_on"]),
                         ("rebought", "2026-06-09"))
        self.assertEqual(row["final"], ["2026-06-01"])
        self.assertEqual([l["trade_date"] for l in row["denied_closed"]],
                         ["2026-06-01"])
        acts = " ".join(RB._acts(doc, "canada"))
        self.assertIn("sell it by 2026-07-09 to keep the 2026-06-09 loss",
                      acts)
        self.assertIn("1 loss(es) already denied (ZZJ.TO)", acts)
        text = " ".join(RB._row_detail(row, False).split())
        self.assertIn("Also 2026-06-01 margin 50 units, loss 250.00, 30 "
                      "units denied for good (the last trade date to "
                      "rescue it, 2026-06-30, has passed); past its "
                      "re-entry date, 2026-07-02, nothing changes it.",
                      text)


class TestExplainWindowCountsSettlement(unittest.TestCase):
    """TJ-0258: a Canadian loss sale on Monday 2024-08-05 (settled the
    6th) and a buy 14 days later (settled the 20th). The window counted
    each row's TRADE date from the loss's SETTLEMENT date: the loss sale
    read T-1 and the buy T+13."""

    def _gain(self):
        from taxjson.lib.core import CanadaTaxRules, TaxTransaction

        def tx(d, settle, q, px):
            return TaxTransaction(
                action='BUYSELL', date=d, date_settle=settle,
                symbol='ZZI.US', quantity=q, price=px,
                net_amount=-q * px, currency='CAD', account='margin')
        res = CanadaTaxRules().compute_gains([
            tx('2024-02-01', '2024-02-05', 10, 10.0),
            tx('2024-08-05', '2024-08-06', -10, 5.0),
            tx('2024-08-19', '2024-08-20', 10, 5.0)])
        return next(g for g in res['transactions'] if g.get('is_wash_sale'))

    @rule("CA-SL-01")
    def test_tj0258_day_counts_settlement_dates(self):
        g = self._gain()
        ww = g['wash_window']
        self.assertEqual((ww['loss_date'], ww['loss_settle']),
                         ('2024-08-05', '2024-08-06'))
        self.assertEqual([(t['days_from_loss'], t['role'])
                          for t in ww['transactions']],
                         [(0, 'loss_sale'), (14, 'trigger')])

    @rule("CA-SL-01")
    def test_tj0258_the_views_name_the_settlement_date(self):
        from taxjson.lib import trace_format as TF
        g = self._gain()
        text = "\n".join(TF._report_window_lines(g, 120, "  "))
        self.assertIn("(loss 2024-08-05, settled 2024-08-06)", text)
        self.assertIn("DAY: days from the loss sale's settlement", text)
        rows = [" ".join(ln.split()) for ln in text.splitlines()]
        self.assertTrue(any(r.startswith("T+0 2024-08-05 2024-08-06 margin "
                                         "SELL") for r in rows), text)
        self.assertTrue(any(r.startswith("T+14 2024-08-19 2024-08-20 "
                                         "margin BUY") for r in rows), text)
        trace = "\n".join(TF._render_wash_window(g))
        self.assertIn("LOSS  = 2024-08-05, settled 2024-08-06", trace)
        self.assertNotIn("T-1 ", trace)


class TestExplainAccountAndTriggers(unittest.TestCase):
    """A Canadian 2024 project: margin sells 100 ZZU.TO at a loss on
    2024-08-01, the RRSP buys 60 on 08-15 and margin2 buys 20 on 08-20;
    margin2 has a denied TWO.TO loss of its own.
    TJ-0259: --explain named only the RRSP buy as the trigger.
    TJ-0413: `wash-sales margin --explain` also printed margin2's TWO.TO
    denial, which `wash-sales margin` does not list."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls._td.name)
        (root / "taxjson.toml").write_text(
            _toml(sheltered=True, year=2024,
                  extra='[accounts.margin2]\ntype = "taxable"\n'))
        books = {
            "margin": ("BUYSELL 2024-03-01 10:00:00 ZZU.TO 100 CAD 10 1000 0\n"
                       "BUYSELL 2024-08-01 10:00:00 ZZU.TO -100 CAD 8 800 0\n"),
            "margin2": ("BUYSELL 2024-08-20 10:00:00 ZZU.TO 20 CAD 8 160 0\n"
                        "BUYSELL 2024-02-01 10:00:00 TWO.TO 10 CAD 10 100 0\n"
                        "BUYSELL 2024-03-04 10:00:00 TWO.TO -10 CAD 5 50 0\n"
                        "BUYSELL 2024-03-11 10:00:00 TWO.TO 10 CAD 5 50 0\n"),
            "rrsp": "BUYSELL 2024-08-15 10:00:00 ZZU.TO 60 CAD 8 480 0\n",
        }
        for acct, text in books.items():
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "m.tt").write_text(text)
        tj(root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _explain(self, *account):
        r = tj(self.root, "wash-sales", *account, "--explain", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_tj0259_every_trigger_is_named(self):
        out = " ".join(self._explain().split())
        self.assertIn("triggered by 2 acquisitions: - 2024-08-15 BUY "
                      "+60.0000 @ 8.0000 account=rrsp [sheltered] - "
                      "2024-08-20 BUY +20.0000 @ 8.0000 account=margin2",
                      out)
        # One trigger keeps its one line.
        self.assertIn("triggered by 2024-03-11 BUY +10.0000 @ 5.0000 "
                      "account=margin2", out)

    def test_tj0413_an_account_shows_its_own_dispositions(self):
        out = self._explain("margin")
        self.assertIn("ZZU.TO — 2024-08-01 — margin", out)
        self.assertNotIn("TWO.TO", out)
        # Still the pooled computation: margin2's buy backs the denial.
        self.assertIn("account=margin2", out)
        out2 = self._explain("margin2")
        self.assertIn("TWO.TO — 2024-03-04 — margin2", out2)
        self.assertNotIn("ZZU.TO — 2024-08-01", out2)
        table = json.loads(tj(self.root, "wash-sales", "margin",
                              "--json").stdout)
        self.assertNotIn("TWO.TO", json.dumps(table))


if __name__ == "__main__":
    unittest.main()
