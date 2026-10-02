"""Re-audit-2 partition lists 01/02 (wave 3): Canada/USA wording and the
manual-check flags the filing views never showed.

Each test runs one synthetic book or one view under both countries and
asserts that each country's output names only its own forms and terms
(Schedule 3 / ACB / s.47 / superficial loss / T3 for Canada; Form 8949 /
basis / §1091 / wash sale / 1099-DIV for the US). Synthetic data only.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import SRC, cli, projects_both

from taxjson.lib import checklist as cl
from taxjson.lib.country import CANADA, COUNTRIES, USA

_CA_TERMS = ("Schedule 3", "s.47", "superficial", "T3 box 42", "CRA",
             "s.86.1", "ACB", "registered-account", "affiliated")
_US_TERMS = ("Form 8949", "§1091", "§355", "1099-DIV", "IRS")


def _ctx(root: Path, country: str, accounts: dict, year: int = 2025):
    cfg = {"settings": {"country": country, "year": year,
                        "base_currency": "USD" if country == USA else "CAD"},
           "accounts": accounts}
    return cl.Ctx(root=root, cfg=cfg, year=year, today=date(2026, 6, 1),
                  run_sub=lambda *a, **k: (0, "", ""))


def _py(*args, cwd=None, stdin=None):
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               NO_COLOR="1")
    return subprocess.run([sys.executable, "-m", *args], capture_output=True,
                          text=True, env=env, cwd=cwd, input=stdin,
                          timeout=180)


def _flag(sym="XYZ.US", rule_id="right_vs_share_loss"):
    return {"rule": rule_id, "loss_symbol": sym, "loss_amount": -500.0,
            "loss_date": "2025-03-03", "option_symbol": "XYZ.WS",
            "option_acquired": "2025-03-10", "held_at_window_end": True,
            "statute": "the s.54 superficial-loss rule", "option_qty": 1.0}


# ------------------------------------------------------------- A2-0413
class TestManualCheckFlagsKeepWashReviewOpen(unittest.TestCase):
    """A warrant/right (or adjusted-series / futures-option) flag denies
    nothing; the checklist said 'no superficial losses' and wash-sales
    'no losses were denied' over unresolved flags."""

    def _book(self, root: Path):
        (root / "work").mkdir(parents=True, exist_ok=True)
        doc = {"summary": {"year": 2025}, "transactions": [
            {"symbol": "XYZ.US", "date": "2025-03-03",
             "date_settle": "2025-03-04", "qty": -10, "gain": -500.0,
             "raw_gain": -500.0, "disallowed_amount": 0.0}],
               "option_replacement_warnings": [_flag()]}
        (root / "work" / "m_gains.json").write_text(json.dumps(doc))

    @rule("CA-SL-15")
    @rule("US-WASH-15")
    def test_checklist_and_wash_sales_list_the_flags(self):
        with tempfile.TemporaryDirectory() as td:
            for c in COUNTRIES:
                root = Path(td) / c
                self._book(root)
                r = cl.d_wash_reviewed(_ctx(root, c, {"m": {"type":
                                                            "taxable"}}))
                self.assertEqual(r.status, "manual", (c, r.detail))
                self.assertIn("XYZ.US 2025-03-03 [right_vs_share_loss]",
                              r.detail)
                self.assertNotIn("no superficial losses", r.detail)
                self.assertNotIn("no wash sales", r.detail)
                (root / "taxjson.toml").write_text(
                    f'[settings]\nyear = 2025\ncountry = "{c}"\n'
                    f'base_currency = "{"USD" if c == USA else "CAD"}"\n'
                    f'[accounts.m]\ntype = "taxable"\n')
                p = cli(root, "wash-sales")
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("MANUAL CHECK", p.stdout)
                self.assertIn("[right_vs_share_loss]", p.stdout)
                j = json.loads(cli(root, "wash-sales", "--json").stdout)
                self.assertEqual(len(j["manual_check_flags"]), 1)

    def test_no_flags_still_done(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "m_gains.json").write_text(json.dumps(
                {"summary": {"year": 2025}, "transactions": []}))
            for c, word in ((CANADA, "no superficial losses"),
                            (USA, "no wash sales")):
                r = cl.d_wash_reviewed(_ctx(root, c, {"m": {"type":
                                                            "taxable"}}))
                self.assertEqual((r.status, r.detail), ("done", word))


# ---------------------------------------------- A2-0738/1240/1259 ...
class TestChecklistWordingByCountry(unittest.TestCase):

    def test_estimate_never_mentions_instalments_in_a_us_project(self):
        with tempfile.TemporaryDirectory() as td:
            us = cl.d_estimate(_ctx(Path(td), USA, {}))
            ca = cl.d_estimate(_ctx(Path(td), CANADA, {}))
        self.assertNotIn("instalments", us.detail)
        self.assertIn("estimated tax", us.detail)
        self.assertIn("[instalments] absent", ca.detail)

    def test_sheltered_inputs_names_each_countrys_repurchaser(self):
        with tempfile.TemporaryDirectory() as td:
            acc = {"ira": {"type": "sheltered"}}
            us = cl.d_sheltered_inputs(_ctx(Path(td), USA, acc))
            ca = cl.d_sheltered_inputs(_ctx(Path(td), CANADA, acc))
        self.assertNotIn("affiliated", us.detail)
        self.assertIn("IRA", us.detail)
        self.assertIn("affiliated purchases unseen", ca.detail)

    def test_unblended_books_name_each_countrys_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "reports").mkdir()
            (root / "work").mkdir()
            for a in ("a1", "a2"):
                (root / "reports" / f"{a}.sum").write_text("TICKER SUMMARY\n")
            acc = {"a1": {"type": "taxable"}, "a2": {"type": "taxable"}}
            us = cl.d_run_clean(_ctx(root, USA, acc)).detail
            ca = cl.d_run_clean(_ctx(root, CANADA, acc)).detail
        self.assertIn("no cross-account wash-sale (§1091) pass", us)
        self.assertNotIn("s.47", us)
        self.assertNotIn("blended", us)
        self.assertIn("no blended (s.47) pass for a1, a2", ca)

    def test_zero_value_election_names_one_countrys_rollover(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "m_corp_spinoff_value.diag").write_text(
                "warning: spin-off booked at $0\n")
            us = cl.d_elections(_ctx(root, USA, {})).detail
            ca = cl.d_elections(_ctx(root, CANADA, {})).detail
        self.assertIn("§355", us)
        self.assertIn("allocated_acb)", us)
        self.assertNotIn("s.86.1", us)
        self.assertIn("s.86.1", ca)
        self.assertIn("allocated_acb_cad", ca)
        self.assertNotIn("§355", ca)

    def test_us_na_steps_carry_no_canadian_title_or_reason(self):
        for sid in ("t1135", "noa"):
            _id, _st, title, cmd, why = cl.step_meta(sid, USA)
            for term in ("T1135 filed", "ITA", "CRA", "[instalments]",
                         "Notice of Assessment compared"):
                self.assertNotIn(term, title + cmd, (sid, term))
                self.assertNotIn(term, why if term != "T1135 filed"
                                 else title, (sid, term))
            self.assertIn("US project", why)
        self.assertIn("T1135", cl.step_meta("t1135", CANADA)[2])


# ------------------------------------- A2-0432/0722/0745/1239/... (.sum)
class TestSumGainsWording(unittest.TestCase):

    def _report(self, country):
        from taxjson.bin.taxjson_sum_gains import format_report, \
            summarize_gains
        data = summarize_gains({"transactions": [
            {"symbol": "XYZ", "currency": "USD", "date": "2025-03-03",
             "qty": -10, "proceeds": 1500.0, "cost": 1000.0, "gain": 500.0,
             "raw_gain": 500.0}],
            "summary": {"year": 2025, "wash_solver_converged": False,
                        "wash_solver_iterations": 9}})
        return format_report(data, no_color=True, country=country)

    def test_each_country_names_its_own_form_cost_and_solver(self):
        us, ca, neutral = (self._report(USA), self._report(CANADA),
                           self._report(None))
        self.assertIn("for Form 8949 proceeds and basis", us)
        self.assertIn("Form 8949 lines", us)
        self.assertIn("the wash-sale solver did NOT converge", us)
        for t in ("Schedule 3", "ACB", "CRA", "superficial"):
            self.assertNotIn(t, us, t)
        self.assertIn("for Schedule 3 proceeds and ACB", ca)
        self.assertIn("the superficial-loss solver did NOT converge", ca)
        for t in ("Form 8949", "CRA"):
            self.assertNotIn(t, ca, t)
        for t in ("Form 8949", "Schedule 3", "ACB", "CRA"):
            self.assertNotIn(t, neutral, t)

    def test_cli_takes_country(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "g.json"
            f.write_text(json.dumps({"transactions": [], "summary": {
                "year": 2025, "wash_solver_converged": False}}))
            r = _py("taxjson.bin.taxjson_sum_gains", "--country", "us",
                    str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wash-sale solver", r.stdout)
        self.assertNotIn("CRA", r.stdout)

    def test_holdings_toml_cost_note(self):
        from taxjson.bin.taxjson_export import render_holdings_toml
        agg = {"XYZ": {"qty": 10.0, "total_cost": 100.0, "currency": "USD",
                       "position_start_date": "2025-01-02"}}
        notes = {}
        for c in (CANADA, USA, None):
            args = argparse.Namespace(account_name="m", inputs=[],
                                      dust_threshold=1e-9, country=c)
            out = "\n".join(render_holdings_toml(agg, args, base_agg=agg))
            notes[c] = next(ln for ln in out.splitlines()
                            if ln.startswith("base_cost_basis"))
        self.assertIn("s.47 blend", notes[CANADA])
        self.assertIn("filing ACB", notes[CANADA])
        self.assertIn("wash-sale basis adjustments", notes[USA])
        for t in ("s.47", "superficial", "ACB"):
            self.assertNotIn(t, notes[USA], t)
            self.assertNotIn(t, notes[None], t)


# ------------------------------------- end to end: run, audit, roc-sum ...
_BOOK = {CANADA: ("XYZ.TO", "CAD"), USA: ("XYZ", "USD")}


def _e2e_projects(td):
    ps = projects_both(td, accounts='[accounts.m]\ntype = "taxable"\n'
                       '[accounts.ira]\ntype = "sheltered"\n')
    for c, root in ps.items():
        sym, cur = _BOOK[c]
        (root / "inputs" / "m").mkdir(parents=True, exist_ok=True)
        (root / "inputs" / "ira").mkdir(parents=True, exist_ok=True)
        (root / "inputs" / "m" / "book.tt").write_text(
            f"BUYSELL 2025-01-06 10:00:00 {sym} 100 {cur} 10 -1000 0\n"
            f"ADJUST 2025-02-03 10:00:00 {sym} {cur} -50\n"
            f"BUYSELL 2025-06-02 10:00:00 {sym} -100 {cur} 15 1500 0\n")
        (root / "inputs" / "ira" / "book.tt").write_text(
            f"BUYSELL 2025-01-06 10:00:00 {sym} 5 {cur} 10 -50 0\n")
    return ps


class TestEndToEndWording(unittest.TestCase):
    """One run per country: the account .sum, `audit` (text and JSON),
    `roc-sum` and `trades-sum` name only the project country's terms."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.ps = _e2e_projects(cls._td.name)
        for c, root in cls.ps.items():
            r = cli(root, "run", "--no-input")
            assert r.returncode == 0, (c, r.stderr[-3000:])

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _out(self, c, *args):
        r = cli(self.ps[c], *args)
        self.assertEqual(r.returncode, 0, (c, args, r.stderr[-2000:]))
        return r.stdout

    def test_sum_report(self):
        us = (self.ps[USA] / "reports" / "m.sum").read_text()
        ca = (self.ps[CANADA] / "reports" / "m.sum").read_text()
        self.assertIn("for Form 8949 proceeds and basis", us)
        self.assertNotIn("Schedule 3", us)
        self.assertIn("for Schedule 3 proceeds and ACB", ca)

    def test_audit_totals_note(self):
        us = self._out(USA, "audit")
        ca = self._out(CANADA, "audit")
        self.assertIn("Form 8949 rows (form-export", us)
        self.assertNotIn("Schedule 3", us)
        self.assertIn("Schedule 3 rows (form-export", ca)
        j = json.loads(self._out(USA, "audit", "--json"))
        self.assertIn("Form 8949 rows", j["totals_note"])

    def test_roc_sum(self):
        us = self._out(USA, "roc-sum")
        ca = self._out(CANADA, "roc-sum")
        self.assertIn("1099-DIV box 3", us)
        self.assertIn("(basis reduced)", us)
        for t in ("T3", "ACB"):
            self.assertNotIn(t, us, t)
        self.assertIn("T3 box 42", ca)
        self.assertIn("(ACB reduced)", ca)
        self.assertNotIn("1099", ca)

    def test_trades_sum_names_the_sheltered_kind(self):
        us = self._out(USA, "trades-sum")
        ca = self._out(CANADA, "trades-sum")
        self.assertIn("including retirement (IRA) ira", us)
        self.assertIn("including registered ira", ca)


class TestAuditCryptoLabel(unittest.TestCase):
    """A2-1267: a US crypto book (audited with --no-wash) is outside
    §1091 — the DISPOSITION label must not say 'wash sale §1091'."""

    def test_render_event_label(self):
        from taxjson.bin.taxjson_audit import render_event
        ev = {"qty": 1.0, "symbol": "BTC", "date": "2025-03-03",
              "account": "c", "id": "x" * 16, "sources": [],
              "proceeds": 100.0, "cost": 50.0, "gain": 50.0}
        def disp(**kw):
            lines = render_event(ev, 1, 1, USA, show_trace=False, **kw)
            return next(l for l in lines if "DISPOSITION" in l)
        self.assertIn("§1091", disp())
        self.assertNotIn("wash sale §1091", disp(no_wash=True))
        self.assertIn("no wash-sale rule", disp(no_wash=True))


# ------------------------------------------------------------- A2-1297
class TestHandoffDeferredWording(unittest.TestCase):

    def _why(self, country):
        from taxjson.lib import handoff
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            cfg = {"settings": {"country": country, "year": 2025},
                   "accounts": {"m": {"type": "taxable"}}}
            record = {"year": 2024, "year_end": {"equity": {"XYZ": {
                "qty": 100.0, "acb": 1000.0, "deferred": 500.0}}}}
            opening = {"equity": {"XYZ": {"qty": 100.0, "acb": 500.0,
                                          "deferred": 0.0}}}
            res = handoff.check(root, cfg, record, opening)
        return res["positions"][0]["why"]

    def test_each_countrys_deferral_name(self):
        us, ca = self._why(USA), self._why(CANADA)
        self.assertIn("deferred wash-sale losses (§1091(d))", us)
        self.assertNotIn("superficial", us)
        self.assertIn("deferred superficial losses", ca)


# ---------------------------------------- A2-1242/1252/1274/... corp wording
class TestCorpActionWordingIsNeutral(unittest.TestCase):
    """The parsers take no country: their spin-off warnings and the
    shared allocator / rename rows use no country's terms (A2-1278)."""

    @staticmethod
    def _ev(action_type, src, tgt, qd, qr):
        from taxjson.lib.corp_actions import CorporateAction
        return CorporateAction(
            date="2025-03-03", time="09:30:00", action_type=action_type,
            source_symbol=src, source_isin="", target_symbol=tgt,
            target_isin="", ratio_new=1.0, ratio_old=1.0, qty_disposed=qd,
            qty_received=qr, fmv=0.0, currency="USD",
            target_currency="USD", account="m")

    def test_rename_and_allocator_rows(self):
        from taxjson.lib.corp_actions import (_emit_allocated_basis_spinoff,
                                              _emit_rename)
        desc = _emit_rename(self._ev("name_change", "OLD.US", "NEW.US",
                                     10, 10), {})[0]["description"]
        for t in ("ACB", "superficial", "wash-sale", "§1091"):
            self.assertNotIn(t, desc, t)
        rows = _emit_allocated_basis_spinoff(
            self._ev("spinoff", "PAR.US", "SPN.US", 0, 10),
            {"allocated_acb": 100.0}, description_base="spin")
        adj = next(r for r in rows if r["action"] == "ADJUST")
        self.assertIn("(parent cost reduction)", adj["description"])
        self.assertNotIn("ACB", adj["description"])

    def test_no_country_terms_in_parser_spinoff_warnings(self):
        import inspect
        import taxjson.lib.corp_actions as ca
        src = inspect.getsource(ca._ib_spinoff_events)
        self.assertNotIn("s.86.1", src.split('"""')[-1])
        from taxjson.lib.corp_actions import parse_rbc_corporate_actions
        body = inspect.getsource(parse_rbc_corporate_actions)
        self.assertNotIn("s.86.1 rollover's parent-ACB", body)


# ------------------------------------------------------------- A2-1234
class TestApplyDistributionsNeutralWithoutCountry(unittest.TestCase):

    def _run(self, *extra):
        with tempfile.TemporaryDirectory() as td:
            book = Path(td) / "b.json"
            book.write_text(json.dumps({"metadata": {"target_currency":
                                                     "CAD"},
                                        "transactions": [
                {"action": "BUYSELL", "date": "2025-01-06",
                 "date_settle": "2025-01-07", "time": "10:00:00",
                 "symbol": "XAW.TO", "quantity": 100, "currency": "CAD",
                 "price": 10, "net_amount": -1000, "account": "m"}]}))
            m = Path(td) / "d.map"
            m.write_text("XAW.TO 2025-06-30 0.5\n")
            r = _py("taxjson.bin.taxjson_apply_distributions", str(book),
                    "--map", str(m), "--account", "m", *extra)
            doc = json.loads(book.read_text()) if r.returncode == 0 \
                else {}
        self.assertEqual(r.returncode, 0, r.stderr)
        row = next(t for t in doc["transactions"]
                   if t["action"] == "ADJUST")
        return row["description"], r.stderr

    def test_book_row_words(self):
        desc, err = self._run()
        self.assertIn("(cost up)", desc)
        self.assertNotIn("ACB", desc + err)
        desc, err = self._run("--country", "canada")
        self.assertIn("(ACB up)", desc)
        desc, err = self._run("--country", "usa")
        self.assertIn("(basis up)", desc)
        self.assertNotIn("ACB", desc + err)


# ------------------------------------------------------------- A2-1373
class TestSettingsYearIsShared(unittest.TestCase):
    """The web context (settings_problems) refuses the [settings] year
    values every CLI command refuses."""

    def test_settings_problems_checks_year(self):
        from taxjson.lib.config_check import settings_problems
        for bad in (1850, 2204, 0, 2024.0, True, "2024"):
            cfg = {"settings": {"country": "canada", "year": bad}}
            probs = settings_problems(cfg)
            self.assertTrue(any("year" in p for p in probs), (bad, probs))
        self.assertEqual(settings_problems(
            {"settings": {"country": "usa", "year": 2025}}), [])

    def test_web_context_refuses(self):
        from taxjson.web.context import ProjectContext
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 1850\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.m]\ntype = "taxable"\n')
            with self.assertRaises(ValueError) as e:
                ProjectContext.load(root)
        self.assertIn("plausible tax year", str(e.exception))


# ----------------------------------------- A2-1319 / A2-1287 / A2-1311
class TestStandaloneToolWording(unittest.TestCase):

    def test_missing_history_sheltered_heading(self):
        from taxjson.bin.taxjson_missing_history import _sheltered_title
        us, ca = _sheltered_title(2025, USA), _sheltered_title(2025, CANADA)
        self.assertIn("retirement-account (IRA) positions", us)
        self.assertNotIn("registered", us)
        self.assertIn("registered-account positions", ca)

    def test_printed_usage_lines_carry_country(self):
        import taxjson.bin.taxjson_missing_history as mh
        import taxjson.bin.taxjson_corp_actions as cax
        for doc in (mh.__doc__, cax.__doc__):
            for ln in doc.splitlines():
                if ln.strip().startswith(("taxjson-gains",
                                          "taxjson-corp-actions",
                                          "# review/prune")):
                    nxt = doc.split(ln, 1)[1].splitlines()[1:2]
                    self.assertTrue("--country" in ln
                                    or any("--country" in n for n in nxt),
                                    ln)

    def test_convert_tt_unknown_suffix_is_neutral(self):
        with tempfile.TemporaryDirectory() as td:
            tt = Path(td) / "a.tt"
            tt.write_text("BUYSELL 2025-01-06 10:00:00 XYZ.TSX 10 CAD 10 "
                          "-100 0\n")
            r = _py("taxjson.bin.taxjson_convert_tt", "--account-name", "m",
                    str(tt), str(Path(td) / "a.json"))
        self.assertIn("its own cost-basis pool", r.stderr)
        self.assertNotIn("ACB", r.stderr)


# ------------------------------------------- A2-0427 (already fixed; pin)
class TestTtSplitRoundTripKeepsNoCurrency(unittest.TestCase):

    def test_json_tt_json(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.json"
            src.write_text(json.dumps({"transactions": [
                {"action": "SPLIT", "date": "2026-06-11", "time": "20:25:00",
                 "date_settle": "2026-06-11", "account": "m",
                 "symbol": "XYZ.US", "symbol_new": "", "quantity": 2,
                 "currency": "USD", "price": 0, "net_amount": 0}]}))
            tt, back = Path(td) / "rt.tt", Path(td) / "rt.json"
            self.assertEqual(_py("taxjson.bin.taxjson_convert_tt",
                                 str(src), str(tt)).returncode, 0)
            self.assertEqual(_py("taxjson.bin.taxjson_convert_tt",
                                 "--account-name", "m", str(tt),
                                 str(back)).returncode, 0)
            row = json.loads(back.read_text())["transactions"][0]
        self.assertEqual(row["action"], "SPLIT")
        self.assertNotEqual(row["currency"], "CAD")


if __name__ == "__main__":
    unittest.main()
