"""Regression tests for fix round 8, group us.

TJ-1126 US engine: a rename stamped later in the day than a trade of its
        target booked that trade as a new position.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule, rule_absent
from tax_rules.dual import tx

SRC = Path(__file__).resolve().parent.parent / "src"


def _gains(book, year=2025):
    """pipeline.run_gains on a US book (stderr kept)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            contextlib.redirect_stdout(io.StringIO()):
        res = run_gains(list(book), [], [],
                        req=GainsRequest(country="usa", taxable=True,
                                         year=year, phantom_hint=False))
    res["_stderr"] = err.getvalue()
    return res


def _rows(res):
    return [(t["date"], t["symbol"], t["qty"], round(t["gain"], 2))
            for t in res["transactions"] if t.get("qty")]


def _trade(date, time, sym, qty, price):
    return tx("BUYSELL", date, sym, qty, abs(qty) * price, time=time,
              price=price)


def _split(time, old, new, ratio, date="2025-05-01"):
    return tx("SPLIT", date, old, ratio, 0, time=time, symbol_new=new)


# --------------------------------------------------------------- TJ-1265
class TestUsFuturesLongAndShortInTwoBrokerAccounts(unittest.TestCase):
    A, B = "U1234567", "U9990002"
    OPP = ([("2025-03-03", 1, 100, "O"), ("2026-01-05", -1, 150, "C")],
           [("2025-04-01", -1, 120, "O"), ("2026-02-02", 1, 110, "C")])

    def _run(self, rows_a, rows_b, country):
        from test_fix_1029_r7e_engine import TestIbFuturesInOneFolder as T
        with tempfile.TemporaryDirectory() as tmp:
            root = T._project(None, tmp, rows_a, rows_b, country)
            r = tj(root, "run", "--no-input")
        return " ".join((r.stdout + r.stderr).split())

    @rule("US-FUT-01")
    def test_the_run_warns_and_names_the_way_out(self):
        out = self._run(*self.OPP, "usa")
        self.assertIn("long in one broker account, short in another: give "
                      "each its own folder", out)

    @rule("US-FUT-01")
    def test_two_longs_or_one_moved_position_do_not(self):
        for rows in (
                ([("2025-03-03", 1, 100, "O"), ("2026-01-05", -1, 150, "C")],
                 [("2025-04-01", 1, 120, "O"), ("2026-02-02", -1, 110, "C")]),
                ([("2025-03-03", 1, 100, "O")],
                 [("2026-02-02", -1, 110, "C")])):
            self.assertNotIn("short in another",
                             self._run(*rows, "usa"))

    @rule_absent("US-FUT-01", country="canada")
    def test_canada_has_its_short_pool_and_no_warning(self):
        self.assertNotIn("short in another", self._run(*self.OPP, "canada"))


# --------------------------------------------------------------- TJ-1266
class TestShortCoveredAcrossTheYearEnd(unittest.TestCase):
    """A short sold 2025-02-03, covered 2025-12-31, delivered 2026-01-02,
    in the 2026 project. Keyed to the row TJ-1189's books write."""

    def _run(self, cover_net):
        from test_fix_1029_r7e_engine import _QT_HEAD, _qt, _us_project
        rows = {"margin/qt.csv": _QT_HEAD
                + _qt("2025-02-03", "2025-02-04", "Sell", "SYN", "SYN CORP",
                      -10, 100)
                + _qt("2025-12-31", "2026-01-02", "Buy", "SYN", "SYN CORP",
                      10, cover_net)}
        with tempfile.TemporaryDirectory() as tmp:
            root = _us_project(tmp, "p", 2026, rows)
            r = tj(root, "run", "--no-input")
        return " ".join((r.stdout + r.stderr).split())

    @rule("US-DATE-01")
    def test_a_loss_delivered_in_the_year_is_not_called_no_effect(self):
        out = self._run(-120)
        self.assertNotIn("no effect on 2026's numbers", out)
        self.assertIn("the cover's loss is in 2026's numbers", out)

    @rule("US-DATE-01")
    def test_a_gain_keeps_its_own_year_and_the_note(self):
        out = self._run(-80)
        self.assertIn("no effect on 2026's numbers", out)
        self.assertNotIn("the cover's loss is in", out)


# --------------------------------------------------------------- TJ-1153
class TestAmtOneCentRow(unittest.TestCase):
    @staticmethod
    def _ca(**kw):
        from taxjson.lib import tax_estimate as TE
        args = dict(realized=0.0, eligible_div=0.0, foreign_div=60000.0,
                    pil=0.0, other_income=0.0, other_losses=0.0,
                    province="ON", year=2025, actual_withheld=9000.0,
                    amt_carryover={2022: 5000.0}, amt_carryover_source="x")
        args.update(kw)
        return TE.estimate_canada(**args)

    def _text(self, r):
        from taxjson.lib import amt_report as AR
        return "\n".join(AR.render(AR.build(r, 2025, "CAD"), details=True))

    def test_no_one_cent_row_and_the_explanation_stays(self):
        r = self._ca()
        c = r["amt"]["carryover"]
        # The stored figure does not move, only what is shown.
        self.assertEqual(c["recovered_credits_reduced"], 0.01)
        text = self._text(r)
        self.assertNotIn("credit it limits", text)
        self.assertNotIn("by 0.01", " ".join(text.split()))
        self.assertIn("tax falls by", text)
        self.assertIn("federal 0.00, ON 196.75", text)
        self.assertNotIn("by 0.01", " ".join(" ".join(r["notes"]).split()))

    def test_a_real_limit_keeps_its_row(self):
        r = self._ca(province="AB", foreign_div=0.0, actual_withheld=0.0,
                     other_income=200000.0, amt_carryover={2024: 900.5})
        self.assertNotIn("  tax falls by", self._text(r))

    def test_the_helper_keeps_two_cents(self):
        from taxjson.lib.tax_estimate import shown_credits_reduced as S
        self.assertEqual(S({"recovered_credits_reduced": 0.01}), 0.0)
        self.assertEqual(S({"recovered_credits_reduced": 0.02}), 0.02)
        self.assertEqual(S({"recovered_credits_reduced": 71.02}), 71.02)


# --------------------------------------------------------------- TJ-1294
class TestOptionReportsManualRowSymbol(unittest.TestCase):
    TOOLS = ("taxjson.bin.taxjson_leaps_gains",
             "taxjson.bin.taxjson_ccd_gains")

    def _run(self, tool, symbol, by_file):
        from taxjson.lib.dispatch import python_module_argv
        doc = json.dumps({
            "transactions": [],
            "manual_reporting_required": [
                {"symbol": symbol, "gain": 1, "date": None}],
            "summary": {"year": 2025}})
        env = dict(os.environ, PYTHONPATH=str(SRC))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.json"
            p.write_text(doc)
            return subprocess.run(
                python_module_argv(tool) + ([str(p)] if by_file else []),
                input=None if by_file else doc, capture_output=True,
                text=True, env=env, timeout=120)

    def test_a_symbol_that_is_not_text_is_one_line(self):
        for tool in self.TOOLS:
            for symbol in (["a"], {"a": 1}, 5):
                for by_file in (True, False):
                    with self.subTest(tool=tool, symbol=symbol,
                                      by_file=by_file):
                        r = self._run(tool, symbol, by_file)
                        self.assertEqual(r.returncode, 2, r.stderr)
                        self.assertNotIn("Traceback", r.stderr)
                        self.assertIn('"symbol" must be text', r.stderr)
                        self.assertEqual(
                            len(r.stderr.strip().splitlines()), 1, r.stderr)

    def test_a_text_or_absent_symbol_still_reads(self):
        for tool in self.TOOLS:
            for symbol in ("SYN.TO", "", None):
                with self.subTest(tool=tool, symbol=symbol):
                    r = self._run(tool, symbol, True)
                    self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------- TJ-1140
class TestEveryCommandReadsTheProjectsMap(unittest.TestCase):
    """tjs tax-logic soft-reads the project: it left out the roots the
    project's ticker.map adds, while it says the map extends its lists."""

    def _tjs(self, td, *args, **env):
        from taxjson.lib.dispatch import python_module_argv
        run_env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                       TAXJSON_WIDTH="0")
        run_env.pop("TAXJSON_TICKER_MAP", None)
        run_env.update(env)
        return subprocess.run(
            python_module_argv("taxjson.bin.taxjson_run")
            + ["-C", td, *args], capture_output=True, text=True,
            env=run_env, stdin=subprocess.DEVNULL, timeout=300)

    def _project(self, td, ticker_map=b"INDEXOPT ZZI\n"):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\ncountry = "usa"\nyear = 2025\n'
            'base_currency = "USD"\nsource_currencies = []\n'
            'local_timezone = "America/New_York"\n')
        if ticker_map is not None:
            (root / "ticker.map").write_bytes(ticker_map)

    def test_tax_logic_names_the_projects_root(self):
        with tempfile.TemporaryDirectory() as td:
            self._project(td)
            r = self._tjs(td, "tax-logic")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("XSP, ZZI", r.stdout)

    def test_a_stray_outer_setting_does_not_apply(self):
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as other:
            self._project(td, ticker_map=None)
            stray = Path(other) / "ticker.map"
            stray.write_text("INDEXOPT ZZI\n")
            r = self._tjs(td, "tax-logic", TAXJSON_TICKER_MAP=str(stray))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("ZZI", r.stdout)

    def test_outside_a_project_the_variable_still_counts(self):
        with tempfile.TemporaryDirectory() as td, \
                tempfile.TemporaryDirectory() as other:
            mine = Path(other) / "ticker.map"
            mine.write_text("INDEXOPT ZZI\n")
            r = self._tjs(td, "tax-logic", "--country", "usa",
                          TAXJSON_TICKER_MAP=str(mine))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("XSP, ZZI", r.stdout)

    def test_a_map_it_cannot_read_gives_no_traceback(self):
        for raw in (b"NOT A KEYWORD\n", b"\xff\xfe\x00bad\n"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as td:
                self._project(td, ticker_map=raw)
                r = self._tjs(td, "tax-logic")
                self.assertNotIn("Traceback", r.stderr)


# --------------------------------------------------------------- TJ-0928
class TestUsGiftAdvice(unittest.TestCase):
    """No message tells a US donor that `self` books a gift; Canada's
    decision and its words stay."""

    @rule("US-SEND-02")
    def test_the_refusal_says_it_cannot_be_booked(self):
        from taxjson.lib.country import command_country_problem
        said = command_country_problem("crypto-sends", "usa", "gift")
        self.assertIn("cannot book it yet", said)
        self.assertNotIn("record it as", said)
        self.assertIsNone(command_country_problem("crypto-sends", "canada",
                                                  "gift"))

    @rule("US-SEND-02")
    def test_the_prompt_says_self_keeps_the_coins(self):
        from taxjson.lib import crypto_sends as cs
        said = []
        with tempfile.TemporaryDirectory() as tmp:
            man = Path(tmp) / "sends.json"
            answers = iter(["g", "s"])
            cs.prompt_undecided(
                [{"id": "k1", "summary": "1 SYN", "decision": None,
                  "fair_value": None}], man,
                ask=lambda q: next(answers), say=said.append,
                allow_gift=False)
        text = " ".join(" ".join(said).split())
        self.assertIn("cannot book it yet", text)
        self.assertIn("self keeps the coins and their basis", text)
        self.assertNotIn("answer self", text)

    @rule("US-SEND-02")
    def test_the_documents_stopped_advising_it(self):
        root = Path(__file__).resolve().parent.parent
        for doc in ("docs/settings.md", "docs/tax-rules.md"):
            text = (root / doc).read_text()
            self.assertNotIn("record it as `self`", text)
            self.assertNotIn("record a gift as `self`", text)
            self.assertIn("cannot book a US gift yet", text)
        self.assertIn("a crypto gift you gave cannot be booked",
                      (root / "docs/limits.md").read_text())

    def _keys(self, country, decided):
        from taxjson.lib.checklist import _us_self_sends
        rep = {"accounts": {"coins": {"sends": [
            {"id": "k2", "decision": decided},
            {"id": "k1", "decision": "payment"}]}}}
        return _us_self_sends(rep, country)

    @rule("US-SEND-02")
    def test_the_checklist_asks_about_self_in_a_us_project(self):
        self.assertEqual(self._keys("usa", "self"), ["k2"])

    @rule_absent("US-SEND-02", country="canada")
    def test_the_checklist_does_not_ask_in_canada_or_for_a_payment(self):
        self.assertEqual(self._keys("canada", "self"), [])
        self.assertEqual(self._keys("usa", "payment"), [])


class TestUsGiftInAProject(unittest.TestCase):
    @rule("US-SEND-02")
    def test_refused_says_so_and_self_is_asked_until_answered(self):
        from test_fix_sends import (KR_USDC_ID, PAY_ID, _cad_usd_rates_file, _cli,
                                    _project)
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td, country="usa")
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2026\ncountry = "usa"\nbase_currency = "USD"\n'
                'source_currencies = ["CAD"]\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            _cad_usd_rates_file(root / "work" / "to_base.csv")
            (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            g = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{PAY_ID}=gift")
            self.assertNotEqual(g.returncode, 0)
            err = " ".join(g.stderr.split())
            self.assertIn("cannot book a US gift yet", err)
            self.assertNotIn("record it as", err)
            s = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{PAY_ID}=self", "--set", f"{KR_USDC_ID}=self")
            self.assertEqual(s.returncode, 0, s.stderr[-2000:])
            r = _cli(root, home, "checklist", "--run")
            out = " ".join((r.stdout + r.stderr).split())
            self.assertIn("recorded as self", out)
            self.assertIn("cannot book a US gift yet", out)
            d = _cli(root, home, "checklist", "--done", "crypto-sends")
            self.assertEqual(d.returncode, 0, d.stderr[-2000:])
            r = _cli(root, home, "checklist")
            line = next(ln for ln in r.stdout.splitlines()
                        if "Crypto sends classified" in ln)
            self.assertIn("[x]", line)


# --------------------------------------------------------------- TJ-1126
OLD_PUT = "SYN251219P00001000.US"
NEW_PUT = "SYN251219P00000500.US"


class TestRenameBeforeItsTargetsTrade(unittest.TestCase):
    def _put_book(self, stamp):
        return [_trade("2025-01-02", "10:00:00", OLD_PUT, -1, 10),
                _split(stamp, OLD_PUT, NEW_PUT, 2),
                _trade("2025-05-01", "10:00:00", NEW_PUT, 1, 2),
                _trade("2025-12-19", "16:00:00", NEW_PUT, 1, 0)]

    @rule("US-DATE-13")
    def test_the_stamp_does_not_change_the_gain(self):
        want = [("2025-05-01", NEW_PUT, 1.0, 3.0),
                ("2025-12-19", NEW_PUT, 1.0, 5.0)]
        for stamp in ("09:30:00", "12:00:00", "20:25:00"):
            with self.subTest(stamp=stamp):
                self.assertEqual(_rows(_gains(self._put_book(stamp))), want)

    @rule("US-DATE-13")
    def test_a_sale_of_the_old_ticker_before_the_stamp_stays_before(self):
        # An evening stamp (IB 20:25) on a day the old ticker traded: the
        # sale closes the old ticker's shares, as it always has.
        book = [_trade("2025-01-02", "10:00:00", "OLDX.US", 10, 10),
                _trade("2025-05-01", "10:00:00", "OLDX.US", -5, 12),
                _split("20:25:00", "OLDX.US", "NEWX.US", 2),
                _trade("2025-08-01", "10:00:00", "NEWX.US", -1, 7)]
        self.assertEqual(_rows(_gains(book)),
                         [("2025-05-01", "OLDX.US", 5.0, 10.0),
                          ("2025-08-01", "NEWX.US", 1.0, 2.0)])

    @rule("US-DATE-13")
    def test_both_tickers_earlier_keeps_the_stamp_and_says_so(self):
        book = [_trade("2025-01-02", "10:00:00", "OLDX.US", 10, 10),
                _trade("2025-05-01", "09:40:00", "OLDX.US", -5, 12),
                _trade("2025-05-01", "10:00:00", "NEWX.US", 1, 6),
                _split("20:25:00", "OLDX.US", "NEWX.US", 2),
                _trade("2025-08-01", "10:00:00", "NEWX.US", -1, 7)]
        res = _gains(book)
        self.assertEqual(_rows(res),
                         [("2025-05-01", "OLDX.US", 5.0, 10.0),
                          ("2025-08-01", "NEWX.US", 1.0, 2.0)])
        self.assertEqual(
            res["_stderr"].count("the rename OLDX.US -> NEWX.US"), 1)

    @rule("US-DATE-13")
    def test_a_plain_split_keeps_the_clock(self):
        # A 09:30 purchase stays out of a noon split (TJ-1094's order),
        # with symbol_new spelled either way.
        for new in ("", "SYN.US"):
            with self.subTest(symbol_new=new):
                book = [_trade("2025-01-02", "10:00:00", "SYN.US", 10, 10),
                        _trade("2025-05-01", "09:30:00", "SYN.US", 5, 6),
                        _split("12:00:00", "SYN.US", new, 2),
                        _trade("2025-08-01", "10:00:00", "SYN.US", -25, 7)]
                # 10 at 10 and 5 at 6 both double: 20 at 5, then 5 of the
                # 10 at 3. (Split first, the 5 would stay at 6.)
                self.assertEqual(_rows(_gains(book)),
                                 [("2025-08-01", "SYN.US", 20.0, 40.0),
                                  ("2025-08-01", "SYN.US", 5.0, 20.0)])

    def test_a_row_stays_as_it_was_given(self):
        from taxjson.lib.corporate_timeline import renames_at_open
        book = self._put_book("12:00:00")
        rows, = renames_at_open((book,))
        self.assertEqual(rows[1].time, "00:00:00")
        self.assertEqual(rows[1].id, book[1].id)
        self.assertEqual(book[1].time, "12:00:00")

    def test_canada_books_do_not_pass_through_it(self):
        # Canada's order is the phase ladder (CA-DATE-14): same gain at
        # any stamp, with no row changed.
        from taxjson.lib.pipeline import GainsRequest, run_gains
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            res = run_gains(self._put_book("12:00:00"), [], [],
                            req=GainsRequest(country="canada", taxable=True,
                                             year=2025, phantom_hint=False))
        self.assertEqual(round(sum(t["gain"] for t in res["transactions"]
                                   if t.get("qty")), 2), 8.0)


if __name__ == "__main__":
    unittest.main()
