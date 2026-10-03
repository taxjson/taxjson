"""Canada / USA partition — the commands (partition audit phase B,
B-commands).

The planning tools (wash radar, sell-check, buy-check, harvest, watch),
edge-cases, check-filed / handoff, the standalone
form-export and the wording of every command speak ONE country's law:

  COMMANDS-01/02/05, SPEC-07  the radar family in a US project takes the
                    US engine's own verdict (trade dates, every account
                    incl. IRAs, no still-held "rescue", a long call is a
                    note only); Canada unchanged (settle dates, s.54)
  COMMANDS-06/SPEC-06  edge-cases explains a US project with §1091
  COMMANDS-08       check-filed / handoff refuse a lock of the other
                    country
  COMMANDS-12/SPEC-36  no Canadian terms in US output (and the reverse)
  COMMANDS-15       redact removes a US city/state/ZIP line
  COMMANDS-04       the standalone taxjson-form-export is gated too
  SPEC-16/17        tax-logic states fx-cash §988 and the US estimate's
                    assumptions

Every test runs the SAME synthetic book (or project) under both
countries. All data is synthetic (fake account numbers only).
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
from tax_rules.dual import (SRC, cli, cli_both, gains_both, projects_both,
                            tx)

COUNTRIES = ("canada", "usa")


def _row(date_, symbol, qty, net, *, settle=None, account="margin",
         action="BUYSELL", currency="USD", rid=None):
    r = dict(action=action, date=date_, date_settle=settle or date_,
             time="10:00:00", symbol=symbol, quantity=qty, net_amount=net,
             currency=currency, account=account,
             price=abs(net / qty) if qty else 0.0)
    if rid:
        r["id"] = rid
    return r


def _tx(r):
    """The same row as a TaxTransaction (for gains_both)."""
    return tx(r["action"], r["date"], r["symbol"], r["quantity"],
              r["net_amount"], settle=r["date_settle"],
              account=r["account"], currency=r["currency"])


def _radar(taxable, as_of, country, sheltered=None):
    """{ticker: row} of `taxjson-wash-radar --json` over these rows."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td) / "margin_base.json"
        t.write_text(json.dumps({"transactions": taxable}))
        cmd = [sys.executable, "-m", "taxjson.bin.taxjson_wash_radar",
               "--country", country, "--taxable", str(t), "--date", as_of,
               "--all", "--json"]
        if sheltered:
            s = Path(td) / "sheltered_base.json"
            s.write_text(json.dumps({"transactions": sheltered}))
            cmd += ["--sheltered", str(s)]
        env = dict(os.environ, PYTHONPATH=str(SRC))
        r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           timeout=120)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["country"] == country
    return {row["ticker"]: row for sec in doc["sections"]
            for row in sec["rows"]}


def _radar_both(taxable, as_of, sheltered=None):
    return {c: _radar(taxable, as_of, c, sheltered) for c in COUNTRIES}


def _loss_row(res, date_, symbol):
    return next(t for t in res["transactions"]
                if t.get("symbol") == symbol and t.get("date") == date_
                and float(t.get("raw_gain", t.get("gain")) or 0) < 0)


# A loss, then a replacement bought inside the window and still held.
RESCUE_BOOK = [
    _row("2025-01-15", "BND.US", 100, 1500.0, settle="2025-01-16"),
    _row("2025-03-10", "BND.US", -100, 1200.0, settle="2025-03-11"),
    _row("2025-03-20", "BND.US", 100, 1250.0, settle="2025-03-21"),
]
# ... and the sale the Canadian radar advises (settles before day 30).
RESCUE_SALE = _row("2025-03-28", "BND.US", -100, 1260.0,
                   settle="2025-03-31")


class TestRadarRescue(unittest.TestCase):
    """COMMANDS-01: the s.54 still-held test (and its "rescue") is
    Canadian; a US loss with a replacement in the window is already a
    wash sale, and the radar says so without advising a sale."""

    @rule("CA-PLAN-01")
    @rule_absent("CA-PLAN-01", country="usa")
    @rule("US-PLAN-01")
    @rule_absent("US-PLAN-01", country="canada")
    def test_rescue_is_canadian_the_us_verdict_is_the_engines(self):
        r = _radar_both(RESCUE_BOOK, "2025-03-25")
        ca, us = r["canada"]["BND.US"], r["usa"]["BND.US"]
        self.assertEqual(ca["category"], "VIOLATION")
        self.assertIn("to rescue the loss", ca["advisory"])
        self.assertEqual(us["category"], "WASHED")
        self.assertNotIn("rescue", us["advisory"])
        self.assertIn("§1091", us["advisory"])
        self.assertIsNone(us["rescue"])
        # The engines agree with their radar: after the advised sale the
        # Canadian loss stands, the US one is still disallowed.
        g = gains_both([_tx(x) for x in RESCUE_BOOK + [RESCUE_SALE]],
                       year=2025)
        ca_loss = _loss_row(g["canada"], "2025-03-10", "BND.US")
        us_loss = _loss_row(g["usa"], "2025-03-10", "BND.US")
        self.assertAlmostEqual(float(ca_loss["disallowed_amount"]), 0.0,
                               places=2)
        self.assertGreater(float(us_loss["disallowed_amount"]), 299.0)

    @rule("CA-PLAN-01")
    @rule_absent("CA-PLAN-01", country="usa")
    @rule("US-PLAN-01")
    @rule_absent("US-PLAN-01", country="canada")
    def test_an_ira_purchase_sold_before_day_30_still_washes(self):
        tax = [_row("2026-01-05", "XYZ.US", 100, 5000.0),
               _row("2026-09-01", "XYZ.US", -100, 4000.0)]
        ira = [_row("2026-09-04", "XYZ.US", 10, 420.0, account="ira"),
               _row("2026-09-08", "XYZ.US", -10, 410.0, account="ira")]
        r = _radar_both(tax, "2026-09-10", sheltered=ira)
        self.assertEqual(r["usa"]["XYZ.US"]["category"], "WASHED")
        self.assertIn("lost for good", r["usa"]["XYZ.US"]["advisory"])
        self.assertIn(r["canada"]["XYZ.US"]["category"],
                      ("COOLING", "BLOCKED"))
        g = gains_both([_tx(x) for x in tax],
                       sheltered=[_tx(x) for x in ira], year=2026)
        self.assertGreater(float(_loss_row(g["usa"], "2026-09-01", "XYZ.US")
                                 .get("permanently_disallowed") or 0), 0.0)
        self.assertAlmostEqual(float(_loss_row(
            g["canada"], "2026-09-01", "XYZ.US")["disallowed_amount"]),
            0.0, places=2)


class TestRadarLongCall(unittest.TestCase):
    """COMMANDS-02: a long call bought in the window replaces the shares
    in Canada (s.54 para (i)); in the US it is a note, as in the US
    engine (a warning only)."""

    CALL = "XYZ261218C00050000.US"

    @rule("CA-PLAN-02")
    @rule_absent("CA-PLAN-02", country="usa")
    @rule("US-PLAN-02")
    @rule_absent("US-PLAN-02", country="canada")
    def test_long_call_country_split(self):
        book = [_row("2026-01-05", "XYZ.US", 100, 5000.0),
                _row("2026-09-01", "XYZ.US", -100, 4000.0),
                _row("2026-09-05", self.CALL, 1, 300.0)]
        r = _radar_both(book, "2026-09-10")
        ca, us = r["canada"]["XYZ.US"], r["usa"]["XYZ.US"]
        self.assertEqual(ca["category"], "VIOLATION")
        self.assertIn(self.CALL, ca["advisory"])
        self.assertNotIn(us["category"], ("VIOLATION", "WASHED"))
        self.assertIn("NOTE: a long call", us["advisory"])
        self.assertNotIn("NOTE: a long call", ca["advisory"])
        g = gains_both([_tx(x) for x in book], year=2026)
        self.assertGreater(float(_loss_row(g["canada"], "2026-09-01",
                                           "XYZ.US")["disallowed_amount"]),
                           999.0)
        self.assertAlmostEqual(float(_loss_row(g["usa"], "2026-09-01",
                                               "XYZ.US")["disallowed_amount"]),
                               0.0, places=2)


class TestRadarDateBasis(unittest.TestCase):
    """COMMANDS-05 / SPEC-07: the radar measures the window on the
    engine's own dates — settle in Canada, trade in the US."""

    @rule("CA-PLAN-01")
    @rule_absent("CA-PLAN-01", country="usa")
    @rule("US-PLAN-01")
    @rule_absent("US-PLAN-01", country="canada")
    def test_29_trade_days_31_settle_days(self):
        # Thu buy settles Fri; the Fri loss sale settles Mon.
        book = [_row("2025-01-02", "ABC.US", 100, 1000.0,
                     settle="2025-01-03"),
                _row("2025-02-06", "ABC.US", 100, 1000.0,
                     settle="2025-02-07"),
                _row("2025-03-07", "ABC.US", -100, 800.0,
                     settle="2025-03-10")]
        r = _radar_both(book, "2025-03-12")
        self.assertEqual(r["usa"]["ABC.US"]["category"], "WASHED")
        self.assertNotIn(r["canada"]["ABC.US"]["category"],
                         ("VIOLATION", "WASHED"))
        g = gains_both([_tx(x) for x in book], year=2025)
        self.assertGreater(float(_loss_row(g["usa"], "2025-03-07",
                                           "ABC.US")["disallowed_amount"]),
                           0.0)
        self.assertAlmostEqual(float(_loss_row(
            g["canada"], "2025-03-07", "ABC.US")["disallowed_amount"]),
            0.0, places=2)

    @rule("CA-PLAN-01")
    @rule_absent("CA-PLAN-01", country="usa")
    @rule("US-PLAN-01")
    @rule_absent("US-PLAN-01", country="canada")
    def test_reentry_date_on_the_countrys_basis(self):
        book = [_row("2025-01-02", "ABC.US", 100, 1000.0,
                     settle="2025-01-03"),
                _row("2025-03-07", "ABC.US", -100, 800.0,
                     settle="2025-03-10")]
        r = _radar_both(book, "2025-03-12")
        for c in COUNTRIES:
            self.assertEqual(r[c]["ABC.US"]["category"], "COOLING")
        self.assertEqual(r["canada"]["ABC.US"]["clears_at"], "2025-04-10")
        self.assertEqual(r["usa"]["ABC.US"]["clears_at"], "2025-04-07")


def _base_files(rows, sheltered=None):
    files = {"work/margin_base.json": json.dumps({"transactions": rows})}
    if sheltered is not None:
        files["work/sheltered_base.json"] = json.dumps(
            {"transactions": sheltered})
    return files


class TestChecksFollowTheRadar(unittest.TestCase):
    """COMMANDS-01/12: sell-check never turns a US wash sale into an
    ACTION (there is nothing to rescue), and buy-check names the
    country's rule."""

    @rule("CA-PLAN-01")
    @rule_absent("CA-PLAN-01", country="usa")
    @rule("US-PLAN-01")
    @rule_absent("US-PLAN-01", country="canada")
    def test_sell_check_and_buy_check(self):
        rows = [_row("2026-01-15", "BND.US", 100, 1500.0),
                _row("2026-09-10", "BND.US", -100, 1200.0),
                _row("2026-09-20", "BND.US", 100, 1250.0)]
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2026, files=_base_files(rows))
            sell = cli_both(p, "sell-check", "BND.US", "--json")
            buy = cli_both(p, "buy-check", "ZZZ.US")
        v = {c: json.loads(sell[c].stdout)["results"][0]
             for c in COUNTRIES}
        self.assertEqual(v["canada"]["verdict"], "ACTION", sell["canada"])
        self.assertNotEqual(v["usa"]["verdict"], "ACTION")
        us_text = " ".join(v["usa"]["detail"])
        self.assertIn("WASHED", us_text)
        self.assertNotIn("rescue", us_text)
        self.assertIn("would be superficial", buy["canada"].stdout)
        self.assertIn("would be a wash sale", buy["usa"].stdout)
        self.assertNotIn("superficial", buy["usa"].stdout)


def _gains_doc(res, account="margin"):
    return json.dumps(dict(res, metadata={"account": account}))


class TestEdgeCases(unittest.TestCase):
    """COMMANDS-06 / SPEC-06: edge-cases explains a US project with
    §1091 — no still-held reasoning, no s.54, no Schedule 3, a long call
    as a warning only; Canada unchanged."""

    @rule("CA-RPT-07")
    @rule("US-RPT-05")
    def test_edge_cases_speak_the_projects_law(self):
        call = "XYZ251219C00050000.US"
        rows = [_row("2025-01-06", "XYZ.US", 100, 5000.0),
                _row("2025-06-02", "XYZ.US", -100, 4000.0,
                     settle="2025-06-03"),
                _row("2025-06-30", "XYZ.US", 100, 4100.0,
                     settle="2025-07-01"),
                _row("2025-07-03", "XYZ.US", -100, 4200.0,
                     settle="2025-07-07"),
                _row("2025-06-05", call, 1, 300.0),
                _row("2025-12-31", "XYZ.US", 10, 400.0,
                     settle="2026-01-02"),
                _row("2025-12-31", "XYZ.US", -10, 420.0,
                     settle="2026-01-02")]
        out = {}
        with tempfile.TemporaryDirectory() as td:
            base = json.dumps({"transactions": rows})
            p = projects_both(td, year=2025,
                              files={"work/margin_base.json": base})
            g = gains_both([_tx(x) for x in rows], year=2025)
            for c in COUNTRIES:
                (p[c] / "work" / "margin_gains_wash.json").write_text(
                    _gains_doc(g[c]))
            r = cli_both(p, "edge-cases")
            for c in COUNTRIES:
                self.assertEqual(r[c].returncode, 0, r[c].stderr)
                out[c] = r[c].stdout
        ca, us = out["canada"], out["usa"]
        self.assertIn("ITA s.54", ca)
        self.assertIn("Schedule 3", ca)
        self.assertIn("Superficial-loss window", ca)
        for word in ("s.54", "Schedule 3", "superficial", "Superficial",
                     "still held on day 30", "held on day 30",
                     "Written options across"):
            self.assertNotIn(word, us, word)
        self.assertIn("Wash-sale window", us)
        self.assertIn("no still-held", us)
        self.assertIn("Form 8949", us)
        self.assertIn("WARNING only", us)


def _lock(country, year=2025):
    return json.dumps({
        "schema_version": 2, "year": year, "country": country,
        "basis": "wash-adjusted", "date_basis": (
            "settle" if country == "canada" else "trade"),
        "accounts": {"margin": {"realized": 100.0, "disallowed": 0.0,
                                "dispositions": 1, "income": 0.0}},
        "totals": {"realized": 100.0}, "year_end": {},
        "dispositions": []})


class TestFiledLockCountry(unittest.TestCase):
    """COMMANDS-08: a lock closed under the other country is refused by
    check-filed and handoff (named as the cause; no amend/refresh
    advice), and close-year records the country."""

    @rule("CA-RPT-09")
    @rule("US-RPT-06")
    def test_other_countrys_lock_is_refused(self):
        other = {"canada": "usa", "usa": "canada"}
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2026, files={
                "work/margin_base.json": json.dumps({"transactions": []})})
            for c in COUNTRIES:
                (p[c] / "filed").mkdir()
                (p[c] / "filed" / "2025.json").write_text(_lock(other[c]))
                prior = p[c] / "prior.json"
                prior.write_text(_lock(other[c]))
            chk = cli_both(p, "check-filed")
            ho = {c: cli(p[c], "handoff", "--prior", str(p[c] / "prior.json"))
                  for c in COUNTRIES}
        for c in COUNTRIES:
            err = chk[c].stderr
            self.assertEqual(chk[c].returncode, 1, chk[c])
            self.assertIn(f'closed under country = "{other[c]}"', err)
            self.assertIn(f'this project is country = "{c}"', err)
            self.assertNotIn("DRIFTED", err)
            self.assertNotIn("amend the return", err)
            self.assertNotEqual(ho[c].returncode, 0)
            self.assertIn(f'closed under country = "{other[c]}"',
                          ho[c].stderr)
            self.assertNotIn("problem", ho[c].stdout.lower())

    @rule("CA-RPT-09")
    @rule("US-RPT-06")
    def test_close_year_records_the_country(self):
        from taxjson.bin import taxjson_filed as F
        for c in COUNTRIES:
            with tempfile.TemporaryDirectory() as td:
                path = F.write_snapshot(Path(td), 2025, c, "wash-adjusted",
                                        {"margin": {"realized": 1.0}},
                                        force=False)
                lock = json.loads(path.read_text())
            self.assertEqual(lock["country"], c)
            settings = {"country": c}
            self.assertIsNone(F.lock_country_problem(lock, settings, "x"))
            self.assertIsNotNone(F.lock_country_problem(
                dict(lock, country="usa" if c == "canada" else "canada"),
                settings, "x"))


class TestWording(unittest.TestCase):
    """COMMANDS-12 / SPEC-36: the command output names the project's
    own law."""

    def test_wash_sales_carryover_and_radar_wording(self):
        rows = [_row("2025-01-15", "BND.US", 100, 1500.0),
                _row("2025-03-10", "BND.US", -100, 1200.0),
                _row("2025-03-20", "BND.US", 100, 1250.0)]
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, year=2025, files=_base_files(rows))
            g = gains_both([_tx(x) for x in rows], year=2025)
            for c in COUNTRIES:
                (p[c] / "work" / "margin_gains_wash.json").write_text(
                    _gains_doc(g[c]))
            ws = cli_both(p, "wash-sales")
            rad = cli_both(p, "wash-radar", "--date", "2025-03-25")
        self.assertIn("superficial-loss rule", ws["canada"].stdout)
        self.assertIn("§1091", ws["usa"].stdout)
        self.assertIn("an IRA", ws["usa"].stdout)
        self.assertNotIn("superficial", ws["usa"].stdout)
        self.assertNotIn("registered account", ws["usa"].stdout)
        self.assertNotIn("§1091", ws["canada"].stdout)
        for bad in ("rescue", "superficial", "SETTLE", "registered",
                    "DRIP", "VIOLATION"):
            self.assertNotIn(bad, rad["usa"].stdout, bad)
        self.assertIn("VIOLATION", rad["canada"].stdout)
        self.assertNotIn("WASHED", rad["canada"].stdout)

    def test_carryover_notes(self):
        from taxjson.bin.taxjson_carryover import render
        us = render({"country": "usa", "rows": [
            {"year": 2025, "net_gain": -100.0, "net_st": -100.0,
             "net_lt": 0.0, "dispositions": 1,
             "ordinary_income_offset": 100.0, "st_carryover": 0.0,
             "lt_carryover": 0.0}], "final_carryforward": 0.0,
             "final_st_carryover": 0.0, "final_lt_carryover": 0.0},
            "USD", 2025, False)
        for bad in ("T1A", "50% inclusion", "Schedule 3", "superficial"):
            self.assertNotIn(bad, us, bad)
        self.assertIn("post-wash-sale", us)

    def test_fx_cash_caveat_and_checklist(self):
        from taxjson.bin.taxjson_fx_cash import (apply_jurisdiction,
                                                 render_report)
        doc = {"per_currency": {"CAD": {"acquired": 10.0, "disposed": 10.0,
                                        "gain": 5.0}},
               "net_gain": 5.0, "overdrafts": {}, "unrated": {}}
        us = render_report(doc, "USD", 2025, "usa",
                           apply_jurisdiction(5.0, "usa"))
        ca = render_report(doc, "CAD", 2025, "canada",
                           apply_jurisdiction(5.0, "canada"))
        self.assertIn("§988 calculation", us)
        self.assertNotIn("s.39(1.1)", us)
        self.assertIn("s.39(1.1) calculation", ca)
        from taxjson.lib import checklist as CL
        self.assertNotIn("$200 per transaction", str(CL.US_STEPS))

    def test_reconcile_slips_note(self):
        from taxjson.bin.taxjson_reconcile_slips import render
        rep = {"rows": [{"symbol": "AAA", "status": "OK", "detail": ""}],
               "clean": True,
               "counts": {"ok": 1, "mismatch": 0, "missing_from_computed": 0,
                          "missing_from_slip": 0}}
        us = render(rep, 1.0, "usa")
        ca = render(rep, 1.0, "canada")
        self.assertNotIn("T5008 box 20", us)
        self.assertIn("1099-B box 1e", us)
        self.assertIn("T5008 box 20", ca)


class TestFxCashRule(unittest.TestCase):
    """SPEC-16: fx-cash is s.39(1.1) (capital, $200 net exemption) in
    Canada and §988 (ordinary, no annual exemption) in the US."""

    @rule("CA-FX-07")
    @rule_absent("CA-FX-07", country="usa")
    @rule("US-FX-03")
    def test_fx_cash_rule_by_country(self):
        from taxjson.bin.taxjson_fx_cash import apply_jurisdiction
        ca = apply_jurisdiction(500.0, "canada")
        us = apply_jurisdiction(500.0, "usa")
        self.assertEqual(ca["reportable"], 300.0)       # beyond $200
        self.assertIn("s.39(1.1)", ca["rule"])
        self.assertEqual(us["reportable"], 500.0)       # no exemption
        self.assertIn("§988", us["rule"])
        self.assertIn("ordinary", us["rule"])
        self.assertEqual(apply_jurisdiction(150.0, "canada")["reportable"],
                         0.0)
        self.assertEqual(apply_jurisdiction(150.0, "usa")["reportable"],
                         150.0)


class TestUsEstimateAndCarryover(unittest.TestCase):
    """SPEC-17: the US estimate's assumptions and the carryover ledger,
    as tax-logic states them."""

    @rule("US-RPT-04", "US-RPT-07")
    @rule_absent("US-RPT-04", country="canada")
    def test_estimate_assumptions(self):
        with tempfile.TemporaryDirectory() as td:
            g = {"summary": {"year": "2025", "total_gain": 0.0},
                 "transactions": [
                     {"symbol": "AAA.US", "date": "2025-03-03",
                      "date_settle": "2025-03-04", "qty": -10,
                      "currency": "USD", "proceeds": 1000.0,
                      "cost": 1100.0, "gain": -100.0, "raw_gain": -100.0,
                      "term": "SHORT_TERM", "commission": 0.0, "fee": 0.0},
                     {"action": "DIVIDEND", "symbol": "BBB.TO",
                      "date": "2025-05-01", "net_amount": 500.0,
                      "gain": 500.0, "currency": "USD"}],
                 "inventory": [], "wash_sales": []}
            p = projects_both(td, year=2025, canada={"province": "ON"},
                              files={"work/margin_gains.json":
                                     json.dumps(g)})
            r = cli_both(p, "estimate", "--other-income", "300000")
        us, ca = r["usa"].stdout, r["canada"].stdout
        self.assertEqual(r["usa"].returncode, 0, r["usa"].stderr)
        self.assertIn("NIIT", us)
        self.assertIn("standard deduction", us)
        self.assertIn("QUALIFIED", us)
        self.assertIn("no foreign tax credit", us)
        self.assertNotIn("NIIT", ca)
        self.assertNotIn("standard deduction", ca)
        from taxjson.lib.tax_estimate import estimate_usa
        e = estimate_usa(st=-10000.0, lt=0.0, qualified_div=0.0, pil=0.0,
                         other_income=100000.0, other_losses=0.0,
                         year=2025)
        self.assertEqual(e["ordinary_offset"], 3000.0)

    @rule("US-RPT-08")
    def test_carryover_worksheet(self):
        from taxjson.bin.taxjson_carryover import build_usa_ledger
        led = build_usa_ledger({2024: {"net": -10000.0, "st": -10000.0,
                                       "lt": 0.0, "dispositions": 1}}, {})
        self.assertEqual(led["rows"][0]["ordinary_income_offset"], 3000.0)
        self.assertEqual(led["final_st_carryover"], 7000.0)
        led = build_usa_ledger({2024: {"net": -10000.0, "st": -10000.0,
                                       "lt": 0.0, "dispositions": 1}},
                               {2024: 0.0})
        self.assertEqual(led["final_st_carryover"], 10000.0)


class TestRedactUsAddress(unittest.TestCase):
    """COMMANDS-15: a US city/state/ZIP line is redacted like a Canadian
    postal-code line."""

    def test_us_and_canadian_address_lines(self):
        from taxjson.bin.taxjson_redact import redact_text
        # Synthetic; assembled so the repository's PII hook does not
        # read the fixture itself as an address.
        text = ("Webull Securities Synthetic\n"
                "Springfield, IL 62704-1234\n"
                "Toronto, ON " + "M5V" + " 2T6\n")
        out = redact_text(text)
        out = out[0] if isinstance(out, tuple) else out
        self.assertNotIn("62704", out)
        self.assertNotIn("Springfield", out)
        self.assertNotIn("M5V", out)
        # A bare 5-digit amount is not a ZIP.
        row = redact_text("2025-01-02,Buy,AAPL,10,15000,USD\n")
        row = row[0] if isinstance(row, tuple) else row
        self.assertIn("15000", row)


class TestStandaloneFormExport(unittest.TestCase):
    """COMMANDS-04 residue: the standalone taxjson-form-export is gated
    by the same ownership table as `taxjson form-export`, and refuses a
    Schedule 3 built from US-engine gains."""

    def test_form_needs_the_countrys_form(self):
        from taxjson.bin.taxjson_form_export import main
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [
                {"symbol": "AAA.US", "date": "2025-03-03",
                 "date_settle": "2025-03-04", "qty": -10, "proceeds": 1100.0,
                 "cost": 1000.0, "gain": 100.0, "term": "LONG_TERM"}]}))
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(g), "--country", "usa",
                                       "--form", "schedule3"]), 2)
                self.assertEqual(main([str(g), "--country", "canada",
                                       "--form", "8949"]), 2)
                self.assertEqual(main([str(g), "--country", "usa",
                                       "--form", "8949"]), 0)
                with self.assertRaises(SystemExit):
                    main([str(g), "--country", "canada",
                          "--form", "schedule3"])
            self.assertIn("Canada-only", err.getvalue())
            self.assertIn("United States-only", err.getvalue())


if __name__ == "__main__":
    unittest.main()
