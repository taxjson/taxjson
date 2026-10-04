"""Re-audit-2 partition fixes, lists partition-07 / partition-08 (area
partD), wording half: the law a message cites follows the project's
country, the standalone tools' country currency defaults, and the
spinoffs view.

Every fixture is SYNTHETIC: fake account ids (55500001 / U5550001),  # pii-ok
invented tickers (QZ*, XYZ, ZZS).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable


def _env(home=None, **extra):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"),
               TAXJSON_OFFLINE="1", NO_COLOR="1")
    if home:
        env["HOME"] = str(home)
    env.update(extra)
    return env


def _tool(module, *args, home=None):
    return subprocess.run([PY, "-m", module, *map(str, args)],
                          capture_output=True, text=True, env=_env(home),
                          stdin=subprocess.DEVNULL, cwd=REPO)


# --------------------------------------------------------------- Webull
from test_fix_l_parsers2_webull import _H25 as _WB_H, _PRE as _WB_PRE  # noqa: E402

_WB_PUT = ('USD,05-06-2025,SELL,@ZZS,PUT ZZS06/20/25 305,OPC,-1,8.00,,799.35\n'
           'USD,20-06-2025,BUY,,,,1,0.00,,\n'
           'USD,23-06-2025,BUY,ZZS,ZZS INC,SHS,100,305.00,,"(30,501.00)"\n')


def _wb(rows, country):
    from taxjson.lib.brokerages.webull import WebullBrokerage
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "wb.csv"
        f.write_text(_WB_PRE + _WB_H + rows, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            wb = WebullBrokerage()
            wb.exercise_fee = 1.00      # [accounts.X] exercise_fee (B11)
            wb.country = country
            tx = wb.parse_file(f)
    return tx, err.getvalue()


class TestWebullCitation(unittest.TestCase):
    """A2-0723 / A2-0732 / A2-0743 / A2-1243 / A2-1310 / A2-1322 /
    A2-1377 / A2-1378 (Webull part): the inferred exercise/assignment note
    cites ITA s.49(3.1) only in a Canadian project."""

    @rule("CA-OPT-06")
    @rule_absent("CA-OPT-06", country="usa")
    @rule("US-OPT-02")
    def test_put_assignment_note_by_country(self):
        _, ca = _wb(_WB_PUT, "canada")
        _, us = _wb(_WB_PUT, "usa")
        _, none = _wb(_WB_PUT, None)
        self.assertIn("s.49(3.1)", ca)
        self.assertIn("cost or proceeds", ca)
        self.assertNotIn("s.49", us)
        self.assertIn("Rev. Rul. 78-182", us)
        self.assertNotIn("s.49", none)
        self.assertNotIn("Rev. Rul.", none)
        self.assertIn("inferred an exercise/assignment", none)

    def test_driver_passes_the_country(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "webull_2025.csv"
            f.write_text(_WB_PRE + _WB_H + _WB_PUT, encoding="utf-8")
            r = _tool("taxjson.bin.taxjson_brokerage", "--brokerage",
                      "webull", "--country", "usa", "--exercise-fee",
                      "1.00", f, home=td)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("inferred an exercise/assignment", r.stderr)
        self.assertNotIn("s.49", r.stderr)


# ------------------------------------------------------------ Questrade
_QH = ('Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,'
       'Price,Gross Amount,Commission,Net Amount,Currency,Account #,'
       'Activity Type,Account Type\n')


def _q(td, action, sym, desc, qty, price, gross, comm, net, cur, act):
    d = f"{td} 12:00:00 AM"
    return ",".join([d, d, action, sym, desc, qty, price, gross, comm, net,
                     cur, "55500001", act, "Individual margin"]) + "\n"  # pii-ok


_QT_ROWS = (_q("2025-06-02", "DIV", "AAPL", "APPLE INC CASH DIV ON 100 SHS "
               "NON-RES TAX WITHHELD", "0", "0", "0", "0", "21.25", "USD",
               "Dividends")
            + _q("2025-06-03", "TF6", "QZT.TO", "QZT CORP TRANSFER IN", "100",
                 "0", "0", "0", "0", "CAD", "Transfers"))


def _qt(country):
    from taxjson.lib.brokerages.questrade import QuestradeBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "questrade_2025.csv"
        p.write_text(_QH + _QT_ROWS, encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ctx = QuestradeBrokerage.prepare_files([p])
            par = QuestradeBrokerage()
            par.account_context = ctx
            par.account_taxable = True
            par.country = country
            par.parse_file(p)
    return err.getvalue()


class TestQuestradeSlipWording(unittest.TestCase):
    """A2-1253 / A2-1308 / A2-1312 / A2-1320."""

    def test_slip_and_cost_words_by_country(self):
        ca, us, none = _qt("canada"), _qt("usa"), _qt(None)
        self.assertIn("T5/NR4 slip", ca)
        self.assertIn("sending broker's ACB", ca)
        self.assertNotIn("T5", us)
        self.assertIn("1099-DIV", us)
        self.assertIn("sending broker's cost basis", us)
        self.assertNotIn("ACB", us)
        self.assertNotIn("T5", none)
        self.assertIn("year-end tax slip", none)


# ------------------------------------------------- sum-gains / audit words
class TestReportFormWords(unittest.TestCase):
    """A2-1378: a US .sum and audit name Form 8949 and basis, never
    Schedule 3 / ACB."""

    def _sum(self, country):
        from taxjson.bin.taxjson_sum_gains import format_report, summarize_gains
        sale = {"date": "2025-05-02", "date_settle": "2025-05-02",
                "symbol": "QZX.US", "qty": -100, "proceeds": 12000.0,
                "cost": 10000.0, "gain": 2000.0, "disallowed_amount": 0.0,
                "days_held": 100, "direction": "LONG", "commission": 0.0,
                "fee": 0.0, "account": "margin"}
        data = summarize_gains({"transactions": [sale],
                                "summary": {"year": 2025}})
        return format_report(data, no_color=True, country=country)

    def test_sum_gains_words(self):
        us, ca, none = self._sum("usa"), self._sum("canada"), self._sum(None)
        self.assertNotIn("Schedule 3", us)
        self.assertNotIn("ACB", us)
        self.assertIn("Form 8949 proceeds and basis", us)
        self.assertIn("Schedule 3 proceeds and ACB", ca)
        self.assertNotIn("Schedule 3", none)

    def test_audit_totals_note(self):
        from taxjson.bin.taxjson_audit import totals_note
        self.assertIn("Form 8949 rows", totals_note("usa"))
        self.assertNotIn("Schedule 3", totals_note("usa"))
        self.assertIn("Schedule 3 rows", totals_note("canada"))


# ---------------------------------------------------------- pipeline notes
class TestPipelineBookWords(unittest.TestCase):
    """A2-1255 / A2-1323: the books' notes in the project's terms."""

    def _notes(self, country):
        from tax_rules.dual import tx
        from taxjson.lib.pipeline import prepare_books
        main = [tx("BUYSELL", "2025-01-02", "QZX.US", 10, -1000,
                   account="ira"),
                tx("TRANSFER", "2025-02-03", "QZX.US", 5, -500,
                   account="ira")]
        sh = [tx("TRANSFER", "2025-03-03", "QZY.US", 5, -500,
                 account="ira2"),
              tx("BUYSELL", "2025-05-01", "QZZ.US", -3, 300,
                 account="ira2")]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            prepare_books(main, sh, taxable=False, country=country)
        return err.getvalue()

    def test_notes_by_country(self):
        us, ca = self._notes("usa"), self._notes("canada")
        self.assertIn("wash-sale walk", us)
        self.assertIn("basis tracking", us)
        self.assertNotIn("superficial", us)
        self.assertNotIn("ACB", us)
        self.assertNotIn("registered", us)
        self.assertIn("superficial-loss walk", ca)
        self.assertIn("ACB pooling", ca)


# ------------------------------------------------------------- carryover
def _book(path, rows):
    path.write_text(json.dumps({"transactions": rows}))


def _row(date, sym, qty, net, cur="USD"):
    return {"action": "BUYSELL", "date": date, "time": "10:00:00",
            "date_settle": date, "symbol": sym, "quantity": qty,
            "price": abs(net / qty), "net_amount": net, "currency": cur,
            "account": "margin"}


class TestCarryoverWords(unittest.TestCase):
    """A2-1246 / A2-1257 / A2-1258 / A2-1293: the US ledger names the
    IRS / Schedule D, never CRA or the Canadian option settings."""

    @rule("US-RPT-08")
    @rule_absent("CA-RPT-10", country="usa")
    def test_us_ledger_words(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            _book(b, [_row("2025-01-02", "QZX.US", 10, 1000),
                      _row("2025-02-03", "QZX.US", -10, 900)])
            r = _tool("taxjson.bin.taxjson_carryover", "--country", "usa",
                      "--filed", "2025=1.00", b, home=td)
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("CRA", out)
        self.assertNotIn("option premium timing", out)
        self.assertIn("IRS Schedule D carryover", out)
        self.assertIn("prior Schedule D", out)

    @rule("CA-RPT-10")
    def test_canada_ledger_names_cra(self):
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "margin_base.json"
            _book(b, [_row("2025-01-02", "QZX.TO", 10, 1000, "CAD"),
                      _row("2025-02-03", "QZX.TO", -10, 900, "CAD")])
            r = _tool("taxjson.bin.taxjson_carryover", "--country",
                      "canada", "--filed", "2025=1.00", b, home=td)
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("CRA records", out)
        self.assertNotIn("IRS", out)
        self.assertIn("CRA's loss balance", out)


# ------------------------------------------- standalone base-currency default
class TestHomeCurrencyDefault(unittest.TestCase):
    """A2-0433 / A2-0746 / A2-1256 / A2-1264 / A2-1270 / A2-1288 /
    A2-1362 / A2-1363: --base-currency defaults to the country's."""

    def test_harvest_takes_usd_books_under_usa(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "margin_gains.json"
            g.write_text(json.dumps({"transactions": [], "inventory": [
                {"symbol": "QZX.US", "qty": 100, "total_cost": 10000.0,
                 "currency": "USD", "position_start_date": "2025-01-10"}]}))
            r = _tool("taxjson.bin.taxjson_harvest", "--country", "usa",
                      "--no-ibkr", g, home=td)
        self.assertNotIn("not the base currency CAD", r.stderr)
        self.assertNotEqual(r.returncode, 2, r.stderr)

    def test_corp_actions_default(self):
        src = (REPO / "src/taxjson/bin/taxjson_corp_actions.py").read_text(
            encoding="utf-8")
        self.assertNotIn("default='CAD'", src)
        self.assertIn("home_currency(args.country)", src)


# --------------------------------------------------------------- spinoffs
class TestSpinoffsView(unittest.TestCase):
    """A2-1275 / A2-1280 / A2-1281 (currency label), A2-0725 (another
    country's election is reported as invalid)."""

    def _proj(self, country, election, base=None):
        from test_corp_views import EV, SPIN_DESC, SUMMARY, _proj, _row
        rows = [_row("BUYSELL", "2024-05-13", "SPNC.US", 20, 600.0,
                     corp_event_id=EV, description=SPIN_DESC)]
        td, root, cfg = _proj(rows, {EV: {
            "election": election,
            "hints": {"allocated_basis": 600.0, "allocated_acb": 600.0},
            "summary": SUMMARY}})
        cfg["settings"] = {"country": country}
        if base:
            cfg["settings"]["base_currency"] = base
        return td, root, cfg

    def test_us_view_without_base_currency_is_usd(self):
        from taxjson.lib.corp_views import spinoffs
        td, root, cfg = self._proj("usa", "tax_free_355")
        with td:
            s = spinoffs(root, cfg)["spinoffs"][0]
        self.assertEqual(s["currency"], "USD")

    @rule_absent("CA-CORP-06", country="usa")
    @rule("US-CORP-07")
    def test_canadian_election_in_us_project_is_wrong_country(self):
        from taxjson.lib.corp_views import (render_spinoffs, spinoffs,
                                            wrong_country_elections)
        td, root, cfg = self._proj("usa", "rollover_s_86_1")
        with td:
            doc = spinoffs(root, cfg)
            wrong = wrong_country_elections(root, cfg)
        s = doc["spinoffs"][0]
        self.assertEqual(s["flags"], ["WRONG-COUNTRY"])
        text = "\n".join(render_spinoffs(doc))
        self.assertNotIn("CRA", text)
        self.assertNotIn("s.86.1 election", text)
        self.assertIn("tax_free_355", text)
        self.assertEqual([w["election"] for w in wrong], ["rollover_s_86_1"])

    def test_elect_pending_lists_it(self):
        from test_corp_views import EV, SUMMARY
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "usa"\n'
                'base_currency = "USD"\n[accounts.m]\ntype = "taxable"\n')
            (root / "inputs" / "m").mkdir(parents=True)
            (root / "inputs" / "m" / "manifest.json").write_text(json.dumps(
                {"elections": {EV: {"election": "rollover_s_86_1",
                                    "hints": {}, "summary": SUMMARY}}}))
            r = subprocess.run(
                [PY, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
                 "elect", "--pending"], capture_output=True, text=True,
                env=_env(td), stdin=subprocess.DEVNULL)
        self.assertNotIn("No pending elections", r.stdout)
        self.assertIn("rollover_s_86_1", r.stdout)


# ------------------------------------------------------------------- IB
_IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Statement,Data,Title,Activity Statement\n')
_IB_CA_H = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
            'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
            'Code\n')


def _ib_ca(desc, qty, value=0, cur='USD'):
    return (f'Corporate Actions,Data,Stocks,{cur},2025-03-02,'
            f'"2025-03-02, 20:25:00","{desc}",{qty},0,{value},0,\n')


_IB_TENDER = (
    _ib_ca('QZTG(US9990000011) Tendered to 99999999 1 FOR 1 (QZTG.TEN, '
           'QZTG CORP - TENDER, US9990000011)', -1000)
    + _ib_ca('QZTG.TEN(US9990000011) Tendered to 99999999 1 FOR 1 '
             '(QZTG.TEN, QZTG CORP - TENDER, US9990000011)', 1000)
    + _ib_ca('QZTG.TEN(99999999) Merged(Voluntary Offer Allocation) WITH '
             'US8880000001 1 for 2 (QZAQ, ACQUIRER INC, US8880000001)', 500,
             value=25000)
    + _ib_ca('QZTG.TEN(99999999) Merged(Voluntary Offer Allocation) WITH '
             'US8880000001 1 for 2 (QZTG.TEN, QZTG CORP - TENDER, '
             'US9990000011)', -1000))


def _ib(text, country):
    from taxjson.lib.brokerages.ib_extractor import IbBrokerage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ib.csv"
        p.write_text(text, encoding="utf-8")
        par = IbBrokerage()
        par.country = country
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            txs = par.parse_file(p)
    return txs, err.getvalue()


class TestIbWording(unittest.TestCase):
    """A2-0726 / A2-1251 / A2-1301 / A2-1304 / A2-1305 / A2-1307 /
    A2-1317 (tender), A2-1303 (multi-account)."""

    def test_tender_names_the_countrys_rollover(self):
        _, ca = _ib(_IB_HEAD + _IB_CA_H + _IB_TENDER, "canada")
        _, us = _ib(_IB_HEAD + _IB_CA_H + _IB_TENDER, "usa")
        for err in (ca, us):
            self.assertIn("warning: UNBOOKED:", err)
        self.assertIn("s.85.1", ca)
        self.assertNotIn("s.85.1", us)
        self.assertIn("§368", us)

    def test_multi_account_warning_is_neutral(self):
        src = (REPO / "src/taxjson/lib/brokerages/ib_extractor.py").read_text(
            encoding="utf-8")
        self.assertNotIn("(TFSA/RRSP) its own folder", src)


class TestRbcRightsWording(unittest.TestCase):
    """A2-1378 sibling (rbc_direct s.15(1)(c)): the rights note cites the
    project's law."""

    def test_rights_note_by_country(self):
        from test_rbc_parse_audit_2026_09 import RTS_EXP, parse, row
        body = row("September 8, 2023", "Reorganization", "CSX.RT", "", "1",
                   "", "0", "CAD", f"DIS - RTS CONSTELLO SOFTWARE INC "
                   f"{RTS_EXP} {RTS_EXP} RTS DIST  ON       1 SHS REC "
                   f"09/01/23 PAY 09/08/23")
        _, ca, _ = parse(body, country="canada")
        _, us, _ = parse(body, country="usa")
        self.assertIn("15(1)(c)", ca)
        self.assertNotIn("15(1)(c)", us)
        self.assertNotIn("ACB", us)
        self.assertIn("§305(a)", us)


class TestCryptoSendsRunNote(unittest.TestCase):
    """A2-1378 (run note): a US run never offers `gift` or calls it a
    disposition (US-SEND-02)."""

    @rule("US-SEND-02")
    def test_us_run_note_offers_self_or_payment(self):
        from test_fix_sends import _cad_usd_rates_file, _cli, _project
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td, country="usa")
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = ["CAD"]\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            _cad_usd_rates_file(root / "work" / "to_base.csv")
            (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
            r = _cli(root, home, "run", "--no-input")
        err = r.stderr + r.stdout
        self.assertEqual(r.returncode, 0, err[-2000:])
        self.assertIn("not yet classified as self / payment", err)
        self.assertNotIn("gift or payment is a disposition", err)


if __name__ == "__main__":
    unittest.main()
