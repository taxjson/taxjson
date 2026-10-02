"""Re-audit-2 partition fixes, part B (lists partition-03 / partition-04):
Canada/USA wording and country gates in `taxjson run`'s commands,
edge-cases, currency conversion and the country ownership tables."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, cli_both, projects_both


def _book(rows):
    return json.dumps({"transactions": rows})


def _row(action, date, symbol, qty, net, *, currency, account="c1",
         price=None, **kw):
    d = {"action": action, "date": date, "date_settle": date,
         "time": "10:00:00", "symbol": symbol, "quantity": qty,
         "price": abs(net / qty) if (price is None and qty) else (price or 0),
         "net_amount": net, "currency": currency, "account": account}
    d.update(kw)
    return d


class TestCarryoverCryptoOnly(unittest.TestCase):
    """A2-0146 / A2-0411 / A2-0412: a crypto-only US project fed its
    books positionally, so the ledger washed crypto losses (§1091 does
    not reach digital assets, US-WASH-13)."""

    def _projects(self, td):
        files = {}
        for c, cur in (("canada", "CAD"), ("usa", "USD")):
            files[c] = _book([
                _row("BUYSELL", "2025-02-03", "BTC", 1.0, -50000.0,
                     currency=cur),
                _row("BUYSELL", "2025-08-01", "BTC", -1.0, 40000.0,
                     currency=cur),
                _row("BUYSELL", "2025-08-10", "BTC", 1.0, -41000.0,
                     currency=cur)])
        p = projects_both(
            td, accounts='[accounts.c1]\ntype = "taxable"\ncrypto = true\n')
        for c, root in p.items():
            (root / "work").mkdir(exist_ok=True)
            (root / "work" / "c1_base.json").write_text(files[c])
        return p

    @rule("US-WASH-13")
    @rule_absent("US-WASH-13", country="canada")
    @rule("CA-SL-13")
    def test_crypto_only_project_keeps_the_us_loss(self):
        with tempfile.TemporaryDirectory() as td:
            res = cli_both(self._projects(td), "carryover", "--json")
        for c, r in res.items():
            self.assertEqual(r.returncode, 0, (c, r.stderr))
        nets = {}
        for c, r in res.items():
            doc = json.loads(r.stdout)
            row = next(x for x in doc["rows"] if int(x["year"]) == 2025)
            nets[c] = row
        us = nets["usa"]
        self.assertAlmostEqual(
            float(us.get("net_st", 0.0)) + float(us.get("net_lt", 0.0)),
            -10000.0, places=2)
        # Canada: the coin is identical property, rebought and still
        # held — superficial (CA-SL-13), the year's net is 0.
        self.assertAlmostEqual(float(nets["canada"]["net_gain"]), 0.0, places=2)

    @rule("CA-RPT-10")
    @rule("US-RPT-08")
    def test_ledger_notes_name_one_country(self):
        # A2-1333: the notes said "CRA/IRS" and "Schedule 3 / Schedule D"
        # in either country.
        import contextlib
        import io
        from taxjson.bin.taxjson_carryover import main
        for c, cur, bad in (("usa", "USD", ("CRA", "Schedule 3")),
                            ("canada", "CAD", ("IRS", "Schedule D"))):
            with tempfile.TemporaryDirectory() as td:
                b = Path(td) / "m_base.json"
                b.write_text(_book([
                    _row("BUYSELL", "2023-02-03", "ZZC.US", 10, -1000.0,
                         currency=cur, account="m"),
                    _row("BUYSELL", "2023-08-01", "ZZC.US", -10, 900.0,
                         currency=cur, account="m")]))
                out = io.StringIO()
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc = main([str(b), "--country", c,
                               "--project-year", "2025"])
            self.assertEqual(rc, 0)
            txt = out.getvalue()
            self.assertIn("Verify each against the filed return", txt)
            for x in bad:
                self.assertNotIn(x, txt, (c, x))

TWO_TAXABLE = ('[accounts.acctA]\ntype = "taxable"\n'
               '[accounts.acctB]\ntype = "taxable"\n')


def _two_account_projects(td, a_rows, b_rows):
    return projects_both(td, accounts=TWO_TAXABLE, files={
        "inputs/acctA/acctA.tt": a_rows, "inputs/acctB/acctB.tt": b_rows})


class TestListDateBasisWording(unittest.TestCase):
    """A2-0154, A2-0410, A2-0720, A2-0734, A2-1244, A2-1266, A2-1318,
    A2-1330: `list --date` told a US project its per-account cost was an
    ACB and that the return blends it (s.47)."""

    @rule("CA-ACB-01")
    @rule_absent("CA-ACB-01", country="usa")
    @rule("US-BASIS-01")
    def test_s47_note_is_canada_only(self):
        a = "BUYSELL 2025-03-03 10:00:00 XYZ.US 100 USD 30.00 -3000.00 0.00\n"
        b = "BUYSELL 2025-03-03 10:00:00 XYZ.US 100 USD 10.00 -1000.00 0.00\n"
        with tempfile.TemporaryDirectory() as td:
            p = _two_account_projects(td, a, b)
            for c, root in p.items():
                r = cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, (c, r.stderr[-2000:]))
            t = cli_both(p, "list", "--date", "2025-06-30")
            j = cli_both(p, "list", "--date", "2025-06-30", "--json")
        for c in p:
            self.assertEqual(t[c].returncode, 0, (c, t[c].stderr))
        self.assertIn("s.47", t["canada"].stderr)
        self.assertIn("per-account ACB", json.loads(j["canada"].stdout)["basis"])
        self.assertNotIn("s.47", t["usa"].stderr)
        self.assertNotIn("ACB", t["usa"].stderr + t["usa"].stdout)
        self.assertIn("per-account FIFO basis",
                      json.loads(j["usa"].stdout)["basis"])



class TestStaleSendsFileWithUnreadableDecisions(unittest.TestCase):
    """A2-0415: sends.json became unreadable after crypto_sends.tt was
    generated from it; the run booked the old file (a decision since
    changed) with a warning, even under --strict in the US case."""

    def _check(self, country):
        from test_fix_sends import (_project, _cli, TAO_ID,
                                    _cad_usd_rates_file)
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td, country=country)
            if country == "usa":
                (root / "taxjson.toml").write_text(
                    '[settings]\nyear = 2026\ncountry = "usa"\n'
                    'base_currency = "USD"\nsource_currencies = ["CAD"]\n'
                    '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
                _cad_usd_rates_file(root / "work" / "to_base.csv")
                (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
            man = root / "inputs" / "crypto" / "sends.json"
            man.write_text(json.dumps({"sends": {
                TAO_ID: {"decision": "payment"}}}))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            tt = root / "inputs" / "crypto" / "crypto_sends.tt"
            self.assertIn("TAO", tt.read_text())
            # The owner reclassifies the payment and mistypes the value.
            man.write_text(json.dumps({"sends": {
                TAO_ID: {"decision": "slef"}}}))
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0, country)
            self.assertIn("not booked on a guess", r.stderr)
            self.assertIn("'slef'", r.stderr)

    @rule("CA-CRYPTO-07")
    def test_canada_run_stops_instead_of_booking_the_old_file(self):
        self._check("canada")

    @rule("US-SEND-01")
    def test_usa_run_stops_instead_of_booking_the_old_file(self):
        self._check("usa")



def _sends_project(td, country):
    from test_fix_sends import _project, _cad_usd_rates_file
    root, home = _project(td, country=country)
    if country == "usa":
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "usa"\n'
            'base_currency = "USD"\nsource_currencies = ["CAD"]\n'
            '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
        _cad_usd_rates_file(root / "work" / "to_base.csv")
        (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
    return root, home


class TestCryptoSendsGiftWording(unittest.TestCase):
    """A2-0721, A2-0740, A2-1286, A2-1283, A2-1285, A2-1329: a US project
    was told a gift is a disposition at fair value and offered `gift`
    in hints that the same command refuses (US-SEND-02)."""

    @rule("US-SEND-02")
    @rule_absent("US-SEND-02", country="canada")
    @rule("CA-CRYPTO-07")
    def test_us_wording_offers_payment_only(self):
        from test_fix_sends import _cli
        out = {}
        for country in ("canada", "usa"):
            with tempfile.TemporaryDirectory() as td:
                root, home = _sends_project(td, country)
                r = _cli(root, home, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                lst = _cli(root, home, "crypto-sends", "crypto")
                bad = _cli(root, home, "crypto-sends", "crypto", "--set",
                           "kr-20260504T185014-TAO-0.1=donate")
                summ = (root / "reports" / "crypto.sum").read_text()
                out[country] = (r.stderr, lst.stdout, bad.stderr, summ)
        run_err, listing, err, summ = out["usa"]
        self.assertIn("a gift is not a sale for a US donor", run_err)
        self.assertNotIn("gift or payment is a disposition", run_err)
        self.assertIn(".tt if payment:", listing)
        self.assertNotIn("gift/payment", listing)
        self.assertIn("expected one of self, payment.", err)
        self.assertNotIn("(gift or payment), each is a taxable", summ)
        run_err, listing, err, summ = out["canada"]
        self.assertIn("a gift or payment is a disposition at fair value",
                      run_err)
        self.assertIn(".tt if gift/payment:", listing)
        self.assertIn("expected one of self, gift, payment.", err)
        self.assertIn("(gift or payment), each is a taxable", summ)



def _recent(days):
    from datetime import date, timedelta
    return (date.today() - timedelta(days=days)).isoformat()


class TestBuySellCheck(unittest.TestCase):
    """buy-check / sell-check country gates (A2-0408, A2-0749, A2-0750,
    A2-0752, A2-1340)."""

    def _run_both(self, td, accounts, files):
        p = projects_both(td, year=int(_recent(0)[:4]), accounts=accounts,
                          files=files)
        for c, root in p.items():
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, (c, r.stderr[-2000:]))
        return p

    @rule("CA-SL-05", "CA-PLAN-02")
    def test_canada_call_denial_is_sized_per_contract(self):
        # A2-0752: one contract replaces 100 shares; the text priced the
        # denial per unit as if per contract.
        rows = (f"BUYSELL {_recent(120)} 10:00:00 XYZ.TO 100 CAD 50.00 "
                f"-5000.00 0.00\n"
                f"BUYSELL {_recent(11)} 10:00:00 XYZ.TO -100 CAD 40.00 "
                f"4000.00 0.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, year=int(_recent(0)[:4]), files={
                "inputs/margin/m.tt": rows})["canada"]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            b = cli(root, "buy-check", "XYZ261218C00040000.TO")
        self.assertEqual(b.returncode, 1, b.stdout + b.stderr)
        self.assertIn("$10.00 of it per share", b.stdout)
        self.assertIn("$1,000.00 per standard 100-share contract", b.stdout)

    @rule("CA-PLAN-01")
    @rule("US-PLAN-01")
    def test_locked_buy_check_does_not_promise_a_full_exit(self):
        # A2-0408: a registered/IRA buy in the window denies a taxable
        # loss sale even as a full exit; buy-check said "a full exit is
        # not" a wash/superficial sale.
        accounts = ('[accounts.margin]\ntype = "taxable"\n'
                    '[accounts.reg]\ntype = "sheltered"\n')
        with tempfile.TemporaryDirectory() as td:
            p = self._run_both(td, accounts, {
                "inputs/margin/m.tt":
                    f"BUYSELL {_recent(200)} 10:00:00 XYZ.US 100 USD "
                    f"50.00 -5000.00 0.00\n",
                "inputs/reg/r.tt":
                    f"BUYSELL {_recent(5)} 10:00:00 XYZ.US 50 USD 40.00 "
                    f"-2000.00 0.00\n"})
            b = cli_both(p, "buy-check", "XYZ.US")
        for c, r in b.items():
            self.assertIn("LOCKED", r.stdout, c)
            self.assertNotIn("a full exit is not", r.stdout, c)
            self.assertNotIn("selling the full position is not", r.stdout,
                             c)
            self.assertIn("does not escape the rule", r.stdout, c)

    @rule("US-PLAN-05")
    @rule_absent("US-PLAN-05", country="canada")
    @rule("CA-SL-13")
    def test_us_coin_query_is_outside_the_wash_rule(self):
        # A2-0749 / A2-0750 / A2-1340: the coin is in no US radar row;
        # buy-check ETH took ETH.US's COOLING verdict, sell-check BTC said
        # "no tracked taxable position".
        accounts = ('[accounts.margin]\ntype = "taxable"\n'
                    '[accounts.kr]\ntype = "taxable"\ncrypto = true\n')
        with tempfile.TemporaryDirectory() as td:
            p = self._run_both(td, accounts, {
                "inputs/margin/m.tt":
                    f"BUYSELL {_recent(120)} 10:00:00 ETH.US 100 USD 50.00 "
                    f"-5000.00 0.00\n"
                    f"BUYSELL {_recent(8)} 10:00:00 ETH.US -100 USD 40.00 "
                    f"4000.00 0.00\n",
                "inputs/kr/k.tt":
                    f"BUYSELL {_recent(100)} 10:00:00 ETH 1 USD 3000.00 "
                    f"-3000.00 0.00\n"})
            b = cli_both(p, "buy-check", "ETH")
            s = cli_both(p, "sell-check", "ETH")
            e = cli(p["usa"], "buy-check", "ETH.US")
        us_b, us_s = b["usa"], s["usa"]
        self.assertEqual(us_b.returncode, 0, us_b.stdout + us_b.stderr)
        self.assertIn("crypto is not subject to the wash-sale rule",
                      us_b.stdout)
        self.assertNotIn("COOLING", us_b.stdout)
        self.assertIn("ETH.US is a separate listing", us_b.stdout)
        self.assertEqual(us_s.returncode, 0, us_s.stdout + us_s.stderr)
        self.assertIn("crypto is not subject to the wash-sale rule",
                      us_s.stdout)
        self.assertNotIn("no tracked taxable position", us_s.stdout)
        # The ETF keeps its own verdict under its own name.
        self.assertIn("COOLING", e.stdout)
        # Canada radars the coin (CA-SL-13): no US wording.
        self.assertNotIn("wash-sale rule", b["canada"].stdout)
        self.assertIn("this is the ETH listing", b["canada"].stdout)



class TestWashSalesNamesTheRule(unittest.TestCase):
    """A2-0748, A2-1249, A2-1265, A2-1326, A2-1333, A2-1352, A2-1360,
    A2-1371, A2-1372: Canada's wash-sales report, its --explain trace
    and the single-account run note said 'wash sale' and 'cost basis'."""

    BOOK = ("BUYSELL 2025-03-03 10:00:00 ZZW.US 100 USD 20.00 -2000.00 0.00\n"
            "BUYSELL 2025-04-03 10:00:00 ZZW.US -100 USD 15.00 1500.00 0.00\n"
            "BUYSELL 2025-04-10 10:00:00 ZZW.US 100 USD 16.00 -1600.00 0.00\n")

    @rule("CA-SL-01")
    @rule_absent("CA-SL-01", country="usa")
    @rule("US-WASH-01")
    def test_each_country_names_its_rule(self):
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, files={"inputs/margin/m.tt": self.BOOK})
            for c, root in p.items():
                r = cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, (c, r.stderr[-2000:]))
            w = cli_both(p, "wash-sales")
            x = cli_both(p, "wash-sales", "--explain")
            one = cli_both(p, "run", "--no-input", "--account", "margin")
            empty = projects_both(Path(td) / "e", files={
                "inputs/margin/m.tt": self.BOOK.splitlines(True)[0]})
            for c, root in empty.items():
                cli(root, "run", "--no-input")
            n = cli_both(empty, "wash-sales")
        ca, us = w["canada"].stdout, w["usa"].stdout
        self.assertIn("SUPERFICIAL LOSSES", ca)
        self.assertIn("1 superficial loss(es)", ca)
        self.assertIn("ACB of the substituted property", ca)
        self.assertNotIn("wash sale", ca.lower())
        self.assertIn("WASH SALES", us)
        self.assertIn("1 wash sale(s)", us)
        self.assertIn("cost basis of the repurchased shares", us)
        self.assertNotIn("superficial", us.lower())
        self.assertNotIn("WASH SALE", x["canada"].stdout)
        self.assertIn("cross-account superficial-loss detection",
                      one["canada"].stderr)
        self.assertIn("cross-account wash-sale detection",
                      one["usa"].stderr)
        self.assertIn("No superficial losses", n["canada"].stdout)
        self.assertIn("No wash sales", n["usa"].stdout)



class TestIncomeViewsWording(unittest.TestCase):
    """A2-0439, A2-0741, A2-1269, A2-1271, A2-1324, A2-1354, A2-1358,
    A2-1359, A2-1265: roc-sum / divs-sum / trades-sum / audit printed
    T3 box 42, T5/T3 slips, 'ACB', 'registered' and 'Schedule 3 rows'
    in US projects."""

    ACCOUNTS = ('[accounts.margin]\ntype = "taxable"\n'
                '[accounts.ira]\ntype = "sheltered"\n')
    MARGIN = ("BUYSELL 2025-01-10 10:00:00 ZZF.US 100 USD 10.00 -1000.00 0.00\n"
              "DIVIDEND 2025-03-15 09:30:00 ZZF.US 0 USD 0.00 25.00\n"
              "ADJUST 2025-06-30 09:30:00 ZZF.US USD -50\n"
              "BUYSELL 2025-08-10 10:00:00 ZZF.US -50 USD 12.00 600.00 0.00\n")
    IRA = ("BUYSELL 2025-01-10 10:00:00 ZZF.US 10 USD 10.00 -100.00 0.00\n"
           "DIVIDEND 2025-03-15 09:30:00 ZZF.US 0 USD 0.00 2.50\n"
           "ADJUST 2025-06-30 09:30:00 ZZF.US USD -5\n")

    @rule("CA-ACB-06")
    @rule("US-ROC-01")
    def test_us_views_use_us_terms(self):
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, accounts=self.ACCOUNTS, files={
                "inputs/margin/m.tt": self.MARGIN,
                "inputs/ira/i.tt": self.IRA})
            for c, root in p.items():
                r = cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, (c, r.stderr[-2000:]))
            roc = cli_both(p, "roc-sum")
            divs = cli_both(p, "divs-sum")
            trades = cli_both(p, "trades-sum")
            aud = cli_both(p, "audit")
            empty = cli_both(p, "roc-sum", "2024")
        us = roc["usa"].stdout + divs["usa"].stdout + trades["usa"].stdout
        for bad in ("T3", "T5", "ACB", "registered"):
            self.assertNotIn(bad, us, bad)
        self.assertIn("Form 1099-DIV box 3", roc["usa"].stdout)
        self.assertIn("Form 1099-DIV", divs["usa"].stdout)
        self.assertIn("tax-advantaged (IRA)", trades["usa"].stdout)
        self.assertIn("Form 8949 rows", aud["usa"].stdout)
        self.assertNotIn("Schedule 3", aud["usa"].stdout)
        self.assertIn("basis adjustments", empty["usa"].stdout)
        # Canada keeps its own terms.
        self.assertIn("T3 box 42", roc["canada"].stdout)
        self.assertIn("T5/T3 slips", divs["canada"].stdout)
        self.assertIn("registered", trades["canada"].stdout)
        self.assertIn("Schedule 3 rows", aud["canada"].stdout)
        self.assertIn("No ACB adjustments", empty["canada"].stdout)



class TestPlanKindsByCountry(unittest.TestCase):
    """A2-0739, A2-1272, A2-1332: the other country's plan kinds were
    accepted silently; a US HSA was unknown (and offered Canada's
    'fhsa'); a registered plan on a taxable account skipped the scan."""

    def _projects(self, td, ca_plan, us_plan, ca_type="sheltered",
                  us_type="sheltered"):
        p = projects_both(td, accounts="")
        for c, plan, typ in (("canada", ca_plan, ca_type),
                             ("usa", us_plan, us_type)):
            t = p[c] / "taxjson.toml"
            t.write_text(t.read_text()
                         + '[accounts.margin]\ntype = "taxable"\n'
                         + f'[accounts.reg]\ntype = "{typ}"\n'
                         + f'plan = "{plan}"\n')
            (p[c] / "inputs" / "margin").mkdir(parents=True)
            (p[c] / "inputs" / "margin" / "m.tt").write_text(
                "BUYSELL 2025-03-03 10:00:00 ZZQ.US 1 USD 10.00 -10.00 "
                "0.00\n")
        return p

    @rule("CA-CTRY-02")
    @rule("US-CTRY-02")
    def test_other_countrys_plan_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            r = cli_both(self._projects(td, "roth", "tfsa"), "run",
                         "--no-input")
        self.assertNotEqual(r["canada"].returncode, 0)
        self.assertIn('plan = "roth" is United States-only',
                      r["canada"].stderr)
        self.assertNotEqual(r["usa"].returncode, 0)
        self.assertIn('plan = "tfsa" is Canada-only', r["usa"].stderr)

    @rule("CA-CTRY-02")
    @rule("US-CTRY-02")
    def test_own_plans_accepted_and_conflicts_warned(self):
        with tempfile.TemporaryDirectory() as td:
            r = cli_both(self._projects(td, "rdsp", "hsa"), "run",
                         "--no-input")
            w = cli_both(self._projects(Path(td) / "w", "rrsp", "ira",
                                        ca_type="taxable",
                                        us_type="taxable"),
                         "run", "--no-input")
        for c in ("canada", "usa"):
            self.assertEqual(r[c].returncode, 0, (c, r[c].stderr[-1500:]))
            self.assertNotIn("not a known plan kind", r[c].stderr)
            self.assertIn("contradicts type = 'taxable'", w[c].stderr, c)

    def test_unknown_plan_suggests_only_this_countrys(self):
        from taxjson.bin.taxjson_run import validate_config
        import contextlib
        import io
        cfg = {"settings": {"country": "usa", "base_currency": "USD"},
               "accounts": {"h": {"type": "sheltered", "plan": "hsaa"}}}
        with contextlib.redirect_stderr(io.StringIO()):
            ws = validate_config(cfg)
        msg = "\n".join(ws)
        self.assertIn("did you mean 'hsa'", msg)
        self.assertNotIn("tfsa", msg)



class TestOneCountryHelpAndWarnings(unittest.TestCase):
    """A2-0718 (elect example), A2-1241 / A2-1273 (retired cross_asset
    warning), A2-0724 (taxjson-gains help)."""

    @rule("CA-SL-05")
    @rule("US-WASH-12")
    def test_cross_asset_warning_states_each_countrys_rule(self):
        from taxjson.bin.taxjson_run import validate_config
        import contextlib
        import io
        out = {}
        for c, cur in (("canada", "CAD"), ("usa", "USD")):
            with contextlib.redirect_stderr(io.StringIO()):
                w = validate_config({"settings": {
                    "year": 2025, "country": c, "base_currency": cur,
                    "cross_asset": True},
                    "accounts": {"m": {"type": "taxable"}}})
            out[c] = "\n".join(x for x in w if "cross_asset" in x)
        self.assertIn("s.54", out["canada"])
        self.assertNotIn("s.54", out["usa"])
        self.assertIn("§1091", out["usa"])
        self.assertIn("does not deny", out["usa"])

    @rule("US-CORP-04")
    @rule("CA-CORP-04")
    def test_elect_example_names_this_countrys_key(self):
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td)
            for root in p.values():
                (root / "inputs" / "margin").mkdir(parents=True)
            r = cli_both(p, "elect", "margin", "--set", "abc")
        self.assertIn("reorg_368", r["usa"].stderr)
        self.assertNotIn("rollover_s_85_1_5", r["usa"].stderr)
        self.assertIn("rollover_s_85_1_5", r["canada"].stderr)

    @rule("CA-CTRY-02")
    @rule("US-CTRY-02")
    def test_gains_help_says_refused(self):
        import subprocess
        import sys
        from tax_rules.dual import SRC
        import os
        h = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_gains", "--help"],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=str(SRC))).stdout
        flat = " ".join(h.split())
        self.assertNotIn("Ignored for the US engine", flat)
        self.assertNotIn("No effect for Canada", flat)
        self.assertIn("Refused with --country usa", flat)
        self.assertIn("Refused with --country canada", flat)



class TestFxFallbackDirection(unittest.TestCase):
    """A2-0148: the implicit 1.35 fallback (a USD->CAD rate) multiplied a
    pre-coverage CAD row in a USD book by 1.35 (real ~0.74)."""

    @rule("CA-FX-02")
    @rule("US-FX-02")
    def test_placeholder_rate_follows_the_direction(self):
        from taxjson.bin import taxjson_convert_currency as CC
        from taxjson.lib.core import TaxTransaction
        CC.reset_fallback_tally()

        def conv(cur, tgt):
            t = TaxTransaction(action="BUYSELL", date="1995-06-01",
                               time="09:30:00", date_settle="1995-06-01",
                               symbol="XYZ.TO", quantity=100, price=10.0,
                               net_amount=-1000.0, currency=cur)
            return CC.convert_transaction(t, tgt, {}, None).net_amount

        self.assertAlmostEqual(conv("USD", "CAD"), -1350.0, places=2)
        self.assertAlmostEqual(conv("CAD", "USD"), -740.74, places=2)
        # An explicit --default-rate is the user's own, applied as given.
        t = TaxTransaction(action="BUYSELL", date="1995-06-01",
                           time="09:30:00", date_settle="1995-06-01",
                           symbol="XYZ.TO", quantity=100, price=10.0,
                           net_amount=-1000.0, currency="CAD")
        self.assertAlmostEqual(CC.convert_transaction(
            t, "USD", {}, CC.resolve_default_rate(0.8)).net_amount,
            -800.0, places=2)
        rows = CC.fallback_rows()
        self.assertTrue(rows)
        msg = "\n".join(m for v in CC.fallback_validation_issues(
            "USD", None).values() for m in v)
        self.assertIn("0.740741", msg)
        CC.reset_fallback_tally()



class TestRawMixedCurrencyActions(unittest.TestCase):
    """A2-0440: the raw-pass mixed-currency detector counted a fixed set
    of actions — it missed an OPENING_BALANCE in another currency (the
    native gains pass then stopped the run) and flagged a TRANSFER the
    engines never check."""

    def _syms(self, rows):
        from taxjson.bin.taxjson_run import _raw_mixed_currency_symbols
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m_raw.json"
            p.write_text(_book(rows))
            return _raw_mixed_currency_symbols(p)

    def test_detector_follows_the_engines_guard(self):
        buy = _row("BUYSELL", "2025-02-03", "QZE.TO", 10, -100.0,
                   currency="CAD")
        self.assertEqual(self._syms([
            buy, _row("OPENING_BALANCE", "2025-01-02", "QZE.TO", 5, -50.0,
                      currency="USD")]), ["QZE.TO"])
        self.assertEqual(self._syms([
            buy, _row("TRANSFER", "2025-03-03", "QZE.TO", 5, 0.0,
                      currency="USD")]), [])
        self.assertEqual(self._syms([
            buy, _row("DIVIDEND", "2025-03-03", "QZE.TO", 0, 5.0,
                      currency="USD")]), [])



class TestOwnershipTablesComplete(unittest.TestCase):
    """A2-0719: --foreign-roc dividend and --slip-gains were refused by
    ad-hoc checks outside lib/country.FLAG_COUNTRY, and nothing checked
    the tables as country.py claimed."""

    @rule("US-CTRY-02")
    @rule("CA-CTRY-02")
    def test_value_and_flag_entries_are_owned(self):
        from taxjson.lib import country as C
        from taxjson.lib import tax_logic as T
        self.assertTrue(C.flag_country_problems(
            "usa", {"--foreign-roc": "dividend"}))
        self.assertFalse(C.flag_country_problems(
            "usa", {"--foreign-roc": "acb"}))
        self.assertFalse(C.flag_country_problems(
            "canada", {"--foreign-roc": "dividend"}))
        self.assertTrue(C.flag_country_problems(
            "usa", {"--slip-gains": ["2025=10"]}))
        us = next(r.text for _s, rules in T.rule_sections("usa", {})
                  for r in rules if r.id == "US-CTRY-02")
        self.assertIn("--foreign-roc dividend", us)
        self.assertIn("--slip-gains", us)

    @rule("US-CTRY-02")
    def test_cli_refusals_come_from_the_table(self):
        import contextlib
        import io
        from taxjson.bin.taxjson_carryover import main
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "m_base.json"
            b.write_text(_book([]))
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                main([str(b), "--country", "usa",
                      "--slip-gains", "2025=10"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--slip-gains is Canada-only", err.getvalue())

    def test_check_tax_rules_checks_the_tables(self):
        import importlib.util
        from tax_rules.dual import REPO_ROOT
        spec = importlib.util.spec_from_file_location(
            "ctr_partB", REPO_ROOT / "scripts" / "check_tax_rules.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertEqual(m.ownership_problems(), [])
        self.assertEqual([x.group(1) for x in m._GATE_MSG_RE.finditer(
            'print("x: --foo dividend is ITA "\n'
            '      "s.90; it does not apply with --country usa")')],
            ["--foo"])



class TestCountrySwitchRefusesOldBooks(unittest.TestCase):
    """A2-0147: books built as one country were served by every report
    under the other country's labels and law citations at exit 0."""

    BOOK = ("BUYSELL 2025-03-03 10:00:00 ZZW.US 100 USD 20.00 -2000.00 0.00\n"
            "BUYSELL 2025-04-03 10:00:00 ZZW.US -100 USD 15.00 1500.00 0.00\n"
            "BUYSELL 2025-04-10 10:00:00 ZZW.US 100 USD 16.00 -1600.00 0.00\n")

    @rule("CA-CTRY-01")
    @rule("US-CTRY-01")
    def test_reports_refuse_books_of_the_other_country(self):
        from tax_rules.dual import settings_for
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, files={"inputs/margin/m.tt": self.BOOK})
            for c, root in p.items():
                r = cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, (c, r.stderr[-2000:]))
            # Swap the two projects' countries without re-running.
            for c, other in (("canada", "usa"), ("usa", "canada")):
                t = p[c] / "taxjson.toml"
                t.write_text(settings_for(other, year=2025)
                             + '[accounts.margin]\ntype = "taxable"\n')
            for cmd in (("wash-sales",), ("list",), ("sum",),
                        ("divs-sum",), ("check-dates",)):
                r = cli_both(p, *cmd)
                for c in p:
                    self.assertNotEqual(r[c].returncode, 0, (c, cmd))
                    self.assertIn("built by the last full run for country",
                                  r[c].stderr, (c, cmd))
            # A fresh run rebuilds them; the reports work again.
            for c, root in p.items():
                self.assertEqual(cli(root, "run", "--no-input").returncode,
                                 0, c)
            w = cli_both(p, "wash-sales")
        self.assertEqual(w["canada"].returncode, 0, w["canada"].stderr)
        self.assertIn("WASH SALES", w["canada"].stdout)  # now a US project
        self.assertIn("SUPERFICIAL LOSSES", w["usa"].stdout)


if __name__ == "__main__":
    unittest.main()
