"""Canada / USA partition — the foundation (partition audit phase A).

One country resolver (lib/country), the ownership tables for settings,
flags and commands, base currency tied to the country, tax-logic read
through the engine's resolvers, and dual-country tests: the SAME
synthetic book (or project) run under both countries, asserting each
rule fires in its own country and not in the other.

All data is synthetic (fake account numbers only).
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib import country as C
from taxjson.lib import tax_logic as TL
from tax_rules import rule, rule_absent
from tax_rules.dual import (SRC, cli, cli_both, gains_both, projects_both,
                            settings_for, tx)

REPO_ROOT = Path(__file__).resolve().parent.parent

_GAINS = {"summary": {"year": "2025", "total_gain": 100.0},
          "transactions": [
              {"symbol": "AAA.US", "date": "2025-03-03",
               "date_settle": "2025-03-04", "date_acquired": "2024-01-02",
               "qty": -10, "currency": "USD", "proceeds": 1100.0,
               "cost": 1000.0, "gain": 100.0, "days_held": 426,
               "term": "LONG_TERM", "commission": 0.0, "fee": 0.0}],
          "inventory": [], "wash_sales": []}
_GAINS_FILE = {"work/margin_gains.json": json.dumps(_GAINS)}


def _module(mod, *args):
    import os
    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.run([sys.executable, "-m", f"taxjson.bin.{mod}",
                           *args], capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=120)


def _gain_rows(res):
    return [t for t in res["transactions"]
            if t.get("action") not in ("DIVIDEND", "DIVIDEND_IN_LIEU")]


# ------------------------------------------------------------ R1 resolver
class TestOneCountryResolver(unittest.TestCase):
    """INPUTS-07/08, COMMANDS-09/14, ENGINE-06/07, SPEC-31: every reader
    goes through lib/country and agrees — canonical, or refused with the
    same message; never a silent Canada."""

    SPELLINGS = {"canada": "canada", "CA": "canada", " Canada ": "canada",
                 "us": "usa", "USA": "usa", " Us ": "usa",
                 "United States": None, "U.S.": None, "CAN": None,
                 "germany": None, "": None}

    def _readers(self):
        from taxjson.bin import taxjson_filed
        from taxjson.bin.taxjson_audit import _norm_country
        from taxjson.lib import checklist
        from taxjson.lib.core import CanadaTaxRules, get_tax_rules
        from taxjson.lib.corp_actions import options_for
        from taxjson.lib.pipeline import GainsRequest
        return {
            "canonical_country": C.canonical_country,
            "GainsRequest": lambda v: GainsRequest(country=v).country,
            "get_tax_rules": lambda v: (
                "canada" if isinstance(get_tax_rules(v), CanadaTaxRules)
                else "usa"),
            "tax_logic": lambda v: ("usa" if TL.render(v, {}).startswith(
                "TAX LOGIC — United States") else "canada"),
            "corp_actions": lambda v: (
                "usa" if any(k == "reorg_368" for k, _ in
                             options_for(v, "merger")) else "canada"),
            "taxjson-audit": _norm_country,
            "filed": lambda v: taxjson_filed._canonical_country(
                {"country": v}),
            "checklist": lambda v: "usa" if checklist.is_us(v) else "canada",
            "settings_country": lambda v: C.settings_country({"country": v}),
        }

    @rule("CA-CTRY-01")
    @rule("US-CTRY-01")
    def test_every_reader_agrees(self):
        for spelling, want in self.SPELLINGS.items():
            for name, read in self._readers().items():
                with self.subTest(spelling=spelling, reader=name):
                    if want is None:
                        with self.assertRaises(ValueError) as cm:
                            read(spelling)
                        self.assertIn("canada, ca, usa or us",
                                      str(cm.exception)
                                      + ("" if spelling else
                                         "canada, ca, usa or us"))
                    else:
                        self.assertEqual(read(spelling), want)

    @rule("CA-CTRY-01")
    @rule("US-CTRY-01")
    def test_project_readers_agree(self):
        """load_config (every `taxjson` command) and
        taxjson-missing-history read the same toml the same way."""
        from taxjson.bin import taxjson_run as R
        from taxjson.lib.missing_history import tax_date_near
        for spelling, want in self.SPELLINGS.items():
            with self.subTest(spelling=spelling), \
                    tempfile.TemporaryDirectory() as td:
                root = Path(td)
                base = {"canada": "CAD", "usa": "USD"}.get(want, "CAD")
                (root / "taxjson.toml").write_text(
                    f'[settings]\nyear = 2025\ncountry = "{spelling}"\n'
                    f'base_currency = "{base}"\n'
                    '[accounts.margin]\ntype = "taxable"\n')
                (root / "work").mkdir()
                book = root / "work" / "margin_base.json"
                book.write_text("{}")
                if want is None:
                    with self.assertRaises(SystemExit):
                        with redirect_stderr(io.StringIO()):
                            R.load_config(root)
                    with self.assertRaises(ValueError):
                        tax_date_near(book)
                else:
                    self.assertEqual(R.load_config(root)["settings"]
                                     ["country"], want)
                    self.assertEqual(tax_date_near(book),
                                     C.default_tax_date(want))

    @rule("CA-CTRY-01")
    @rule("US-CTRY-01")
    def test_missing_country_refused_by_every_command(self):
        """`run` used to refuse while every other command read the
        project as Canada (Schedule 3, T1135, s.39(1.1) over US books)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\nbase_currency = "USD"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work").mkdir()
            (root / "work" / "margin_gains.json").write_text(
                json.dumps(_GAINS))
            for cmd in (["run", "--no-input"], ["sum"], ["form-export"],
                        ["t1135"], ["fx-cash"], ["tax-logic"],
                        ["estimate", "--other-income", "0"],
                        ["checklist", "--quick"], ["carryover"],
                        ["wash-radar"], ["instalments"],
                        ["option-boundary"], ["close-year"]):
                with self.subTest(cmd=cmd[0]):
                    r = cli(root, *cmd)
                    self.assertNotEqual(r.returncode, 0, r.stdout)
                    self.assertIn("[settings] country is missing",
                                  r.stderr)
                    self.assertNotIn("s.49", r.stderr)
                    self.assertNotIn("SCHEDULE 3", r.stdout)


# ------------------------------------------------------------ R2 settings
_CA_ONLY_SETTINGS = {
    "province": '"ON"',
    "option_premium_timing": '"grant"',
    "option_grant_timing_since": "2025",
    "option_buyback_loss_superficial": "true",
    "foreign_return_of_capital": '"dividend"',
    "corporate_distributions": '["XYZ.TO"]',
}
_CA_ONLY_TABLES = {
    "[instalments]": '[instalments]\nbasis = "current_year"\n',
    "[estimate] deductions": "[estimate]\ndeductions = 1000\n",
    "[estimate] carrying_charges": "[estimate]\ncarrying_charges = 100\n",
    "[estimate] amt_carryover": "[estimate]\namt_carryover = { 2024 = 10 }\n",
}


class TestSettingOwnership(unittest.TestCase):
    """ENGINE-03, INPUTS-02/05, COMMANDS-10, SPEC-08/09: a setting the
    country does not own is refused by every config reader, naming the
    key and the country — it was ignored (s.49, province) or, worse,
    honoured (foreign_return_of_capital = "dividend", ITA s.90(1))."""

    def test_table_names_every_known_key(self):
        from taxjson.bin.taxjson_run import _SETTINGS_KEYS
        self.assertEqual(set(_SETTINGS_KEYS), set(C.SETTING_COUNTRY))
        self.assertEqual(set(C.owners(C.SETTING_COUNTRY, "canada")),
                         set(_CA_ONLY_SETTINGS))
        self.assertEqual(set(C.owners(C.CONFIG_COUNTRY, "canada")),
                         set(_CA_ONLY_TABLES))
        # The one US-only key: the §852(b)(7) January-dividend list
        # (partition Phase C, D8; tests/test_fix_income.py).
        self.assertEqual(list(C.owners(C.SETTING_COUNTRY, "usa")),
                         ["ric_january_dividends"])

    @rule("US-CTRY-02")
    @rule_absent("US-CTRY-02", country="canada")
    def test_canada_only_settings_refused_in_a_us_project(self):
        from taxjson.bin.taxjson_run import validate_config
        for key, val in list(_CA_ONLY_SETTINGS.items()) + [
                (k, None) for k in _CA_ONLY_TABLES]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as td:
                extra = ({} if val is None else
                         {key: json.loads(val.replace("true", "true"))})
                tail = _CA_ONLY_TABLES.get(key, "")
                p = projects_both(td, files=_GAINS_FILE, tail=tail,
                                  canada=extra, usa=extra)
                r = cli_both(p, "sum", "--json")
                self.assertNotEqual(r["usa"].returncode, 0)
                self.assertIn(f"{key} is Canada-only", r["usa"].stderr)
                self.assertIn('this project is country = "usa"',
                              r["usa"].stderr)
                # The same config in a Canada project is fine.
                self.assertEqual(r["canada"].returncode, 0,
                                 r["canada"].stderr)
                self.assertNotIn("Canada-only", r["canada"].stderr)
                # validate_config (taxjson run) says the same.
                import tomllib
                cfg = tomllib.loads((p["usa"] / "taxjson.toml").read_text())
                with self.assertRaises(SystemExit) as cm:
                    validate_config(cfg)
                self.assertIn(key, str(cm.exception.code))

    def test_tax_date_against_the_country_default_is_said(self):
        from taxjson.bin.taxjson_run import validate_config
        for c, td, warned in (("canada", "settle", False),
                              ("canada", "trade", True),
                              ("usa", "trade", False),
                              ("usa", "settle", True)):
            with self.subTest(country=c, tax_date=td):
                w = validate_config({"settings": {
                    "year": 2025, "country": c, "tax_date": td,
                    "base_currency": C.home_currency(c)},
                    "accounts": {"m": {"type": "taxable"}}})
                self.assertEqual(any("tax_date" in m for m in w), warned, w)

    @rule("CA-ACB-08")
    @rule_absent("CA-ACB-08", country="usa")
    def test_foreign_roc_rule_is_canadian(self):
        """ITA s.90(1) (IB foreign ROC as a dividend) is Canada's: the
        resolver returns "acb" for a US project whatever the table says."""
        from taxjson.bin.taxjson_run import ib_foreign_roc_mode
        self.assertEqual(ib_foreign_roc_mode({"country": "canada"}),
                         "dividend")
        self.assertEqual(ib_foreign_roc_mode(
            {"country": "canada", "foreign_return_of_capital": "acb"}),
            "acb")
        self.assertEqual(ib_foreign_roc_mode({"country": "usa"}), "acb")
        self.assertEqual(ib_foreign_roc_mode(
            {"country": "usa", "foreign_return_of_capital": "dividend"}),
            "acb")


class TestFlagOwnership(unittest.TestCase):
    """ENGINE-03: the engine CLIs refuse a flag the --country does not
    own (it used to be dropped without a word)."""

    CA_FLAGS = (["--option-premium-timing", "grant"],
                ["--option-grant-since", "2025"],
                ["--option-buyback-wash"])

    def _book(self, td):
        p = Path(td) / "b.json"
        p.write_text(json.dumps({"transactions": []}))
        return p

    @rule("US-CTRY-02")
    @rule_absent("US-CTRY-02", country="canada")
    def test_canada_flags_refused_with_country_usa(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._book(td)
            for mod in ("taxjson_gains", "taxjson_explain",
                        "taxjson_carryover"):
                for flag in self.CA_FLAGS:
                    with self.subTest(mod=mod, flag=flag[0]):
                        us = _module(mod, "--country", "usa", *flag, str(b))
                        self.assertEqual(us.returncode, 2, us.stderr)
                        self.assertIn(f"{flag[0]} is Canada-only",
                                      us.stderr)
                        ca = _module(mod, "--country", "canada", *flag,
                                     str(b))
                        self.assertNotIn("Canada-only", ca.stderr)
                        self.assertNotEqual(ca.returncode, 2, ca.stderr)
            audit = _module("taxjson_audit", "--country", "us", "--base",
                            str(b), "--option-buyback-wash")
            self.assertEqual(audit.returncode, 2)
            self.assertIn("--option-buyback-wash is Canada-only",
                          audit.stderr)

    @rule("US-CTRY-02")
    @rule_absent("US-CTRY-02", country="canada")
    def test_canadian_estimate_flags_refused_in_a_us_project(self):
        """`estimate --province XX` in a US project was silently ignored
        (COMMANDS-10 / SPEC-09); --deductions was refused only by the
        estimate itself."""
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, files=_GAINS_FILE, canada={"province": "ON"})
            for flag in (["--province", "BC"], ["--deductions", "1000"],
                         ["--carrying-charges", "50"]):
                with self.subTest(flag=flag[0]):
                    r = cli_both(p, "estimate", "--other-income", "0", *flag)
                    self.assertNotEqual(r["usa"].returncode, 0)
                    self.assertIn(f"{flag[0]} is Canada-only",
                                  r["usa"].stderr)
                    self.assertEqual(r["canada"].returncode, 0,
                                     r["canada"].stderr)

    @rule("CA-CTRY-02")
    @rule_absent("CA-CTRY-02", country="usa")
    def test_per_account_basis_refused_with_country_canada(self):
        with tempfile.TemporaryDirectory() as td:
            b = self._book(td)
            ca = _module("taxjson_gains", "--country", "canada",
                         "--per-account-basis", str(b))
            self.assertEqual(ca.returncode, 2)
            self.assertIn("--per-account-basis is United States-only",
                          ca.stderr)
            us = _module("taxjson_gains", "--country", "usa",
                         "--per-account-basis", str(b))
            self.assertEqual(us.returncode, 0, us.stderr)


# ------------------------------------------------------------ R3 commands
class TestCommandOwnership(unittest.TestCase):
    """ENGINE-01, SPEC-03/04/05, COMMANDS-03/04: dispatch refuses the
    other country's commands and forms before they run."""

    def _projects(self, td):
        base = {"transactions": [
            {"action": "BUYSELL", "date": "2025-02-03",
             "date_settle": "2025-02-04", "time": "10:00:00",
             "symbol": "XYZ.US", "quantity": 1000, "price": 150.0,
             # A converted base book is in the base currency
             # (taxjson-t1135 refuses native-currency rows, S051-15).
             "net_amount": 150000.0, "currency": "CAD",
             "account": "55500001"}]}   # pii-ok
        p = projects_both(td, files=dict(
            _GAINS_FILE, **{"work/margin_base.json": json.dumps(base)}))
        # Each project's converted books are in its own base currency
        # (taxjson-t1135 and form-export refuse other-currency rows,
        # S051-15 / S032-13).
        gains = dict(_GAINS, transactions=[
            dict(t, currency="CAD") for t in _GAINS["transactions"]])
        (p["canada"] / "work" / "margin_gains.json").write_text(
            json.dumps(gains))
        return p

    @rule("US-CTRY-02")
    @rule_absent("US-CTRY-02", country="canada")
    @rule("CA-RPT-01")
    @rule_absent("CA-RPT-01", country="usa")
    def test_canadian_commands_refused_in_a_us_project(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._projects(td)
            for cmd, shown in ((["t1135"], "`taxjson t1135`"),
                               (["instalments"], "`taxjson instalments`"),
                               (["option-boundary"],
                                "`taxjson option-boundary`"),
                               (["form-export", "--form", "schedule3"],
                                "`taxjson form-export --form schedule3`")):
                with self.subTest(cmd=cmd[0]):
                    r = cli_both(p, *cmd)
                    self.assertNotEqual(r["usa"].returncode, 0)
                    self.assertIn(f"{shown} is Canada-only",
                                  r["usa"].stderr)
                    self.assertEqual(r["usa"].stdout, "")
                    self.assertNotIn("Canada-only", r["canada"].stderr)
            # T1135 still works in Canada: the same books need the form.
            ca = cli(p["canada"], "t1135")
            self.assertEqual(ca.returncode, 0, ca.stderr)
            self.assertIn("FILING REQUIRED", ca.stdout)

    @rule("CA-CTRY-02")
    @rule_absent("CA-CTRY-02", country="usa")
    @rule("US-RPT-01")
    @rule_absent("US-RPT-01", country="canada")
    def test_us_forms_refused_in_a_canada_project(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._projects(td)
            for form in ("8949", "txf"):
                with self.subTest(form=form):
                    r = cli_both(p, "form-export", "--form", form)
                    self.assertNotEqual(r["canada"].returncode, 0)
                    self.assertIn(f"--form {form}` is United States-only",
                                  r["canada"].stderr)
                    # Refused by country, not by the old data accident
                    # (Canada rows carry no ST/LT term).
                    self.assertNotIn("term", r["canada"].stderr)
                    self.assertEqual(r["usa"].returncode, 0,
                                     r["usa"].stderr)
            us = cli(p["usa"], "form-export", "--form", "8949")
            self.assertIn("8949", us.stdout)
            self.assertIn("AAA.US", us.stdout)

    def test_every_owned_command_is_a_real_command(self):
        from taxjson.bin import taxjson_run as R
        import argparse
        seen = set()
        orig = argparse.ArgumentParser.parse_args

        def grab(self, args=None, namespace=None):
            for a in self._actions:
                if isinstance(a, argparse._SubParsersAction):
                    seen.update(a.choices)
            raise SystemExit(0)
        argparse.ArgumentParser.parse_args = grab
        try:
            argv = sys.argv
            sys.argv = ["taxjson", "tax-logic"]
            with self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    R.main()
        finally:
            argparse.ArgumentParser.parse_args = orig
            sys.argv = argv
        for key in C.COMMAND_COUNTRY:
            self.assertIn(key.partition(":")[0], seen, key)


# ------------------------------------------------------------ R4 base currency
class TestBaseCurrencyFollowsCountry(unittest.TestCase):
    """SPEC-02, INPUTS-06, COMMANDS-11: a return is filed in the
    country's currency — US + CAD ran silently (Form 8949 in CAD at Bank
    of Canada rates); Canada + USD only warned."""

    @rule("US-CTRY-03")
    @rule_absent("US-CTRY-03", country="canada")
    @rule("CA-CTRY-03")
    @rule_absent("CA-CTRY-03", country="usa")
    @rule("US-FX-01")
    def test_base_currency_must_be_the_countrys(self):
        from taxjson.bin.taxjson_run import validate_config
        for country, base, ok in (("usa", "USD", True), ("usa", "CAD", False),
                                  ("canada", "CAD", True),
                                  ("canada", "USD", False),
                                  ("usa", "usd", True)):
            with self.subTest(country=country, base=base):
                cfg = {"settings": {"year": 2025, "country": country,
                                    "base_currency": base},
                       "accounts": {"m": {"type": "taxable"}}}
                if ok:
                    self.assertEqual(validate_config(cfg), [])
                else:
                    with self.assertRaises(SystemExit) as cm:
                        validate_config(cfg)
                    self.assertIn(
                        f"filed in {C.home_currency(country)}",
                        str(cm.exception.code))

    @rule("US-FX-01")
    @rule("CA-FX-01")
    def test_unset_base_currency_is_the_countrys(self):
        from taxjson.bin.taxjson_run import _base_currency
        with tempfile.TemporaryDirectory() as td:
            for c in C.COUNTRIES:
                root = Path(td) / c
                root.mkdir()
                (root / "taxjson.toml").write_text(
                    f'[settings]\nyear = 2025\ncountry = "{c}"\n')
                self.assertEqual(_base_currency(root), C.home_currency(c))


# ------------------------------------------------------------ R9 tax-logic
class TestTaxLogicIsTheSpec(unittest.TestCase):
    """SPEC-12/13/31, R9: tax-logic reads settings through the engine's
    own resolvers, never falls back to Canada, and every statement has a
    stable id."""

    def _ids(self, country, settings):
        return [r.id for _t, rs in TL.rule_sections(country, settings)
                for r in rs]

    def test_every_variant_renders_what_the_engine_reads(self):
        from taxjson.lib.pipeline import option_timing_from_settings
        for c in C.COUNTRIES:
            for st in TL.variants(c):
                ids = set(self._ids(c, st))
                basis = C.resolve_tax_date(c, st.get("tax_date"))
                if c == "canada":
                    self.assertIn("CA-DATE-01" if basis == "settle"
                                  else "CA-DATE-02", ids)
                    kw = option_timing_from_settings(st)
                    self.assertIn("CA-OPT-01" if kw["option_premium_timing"]
                                  == "grant" else "CA-OPT-05", ids)
                    self.assertIn("CA-SL-12" if kw[
                        "option_buyback_loss_superficial"] else "CA-SL-11",
                        ids)
                    self.assertIn("CA-ACB-08" if C.foreign_roc_mode(st)
                                  == "dividend" else "CA-ACB-09", ids)
                    self.assertIn("CA-DATE-10" if C.futures_settle_mode(st)
                                  == "next_day" else "CA-DATE-09", ids)
                else:
                    self.assertIn("US-DATE-01" if basis == "trade"
                                  else "US-DATE-02", ids)
                self.assertTrue(all(i.startswith("CA-" if c == "canada"
                                                 else "US-") for i in ids))

    def test_messy_spellings_follow_the_engine(self):
        # " Grant " is what the engine reads as grant timing: the text
        # used to render close timing for it.
        ids = self._ids("canada", {"country": "canada",
                                   "option_premium_timing": " Grant ",
                                   "year": 2025})
        self.assertIn("CA-OPT-01", ids)
        # Values the engine refuses are refused here too.
        for bad in ({"futures_settle": "nextday"},
                    {"foreign_return_of_capital": "ACB"},
                    {"tax_date": "Settled"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                TL.render("canada", dict(bad, country="canada"))

    def test_unknown_country_is_refused_not_canada(self):
        for bad in ("United States", "U.S.", None, ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                TL.render(bad, {})

    def test_cli_ids_country_spelling_and_readable_default(self):
        with tempfile.TemporaryDirectory() as td:
            out = _module("taxjson_run", "-C", td, "tax-logic", "--country",
                          "USA", "--ids")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("[US-WASH-01]", out.stdout)
            plain = _module("taxjson_run", "-C", td, "tax-logic",
                            "--country", "canada")
            self.assertNotIn("[CA-", plain.stdout)
            self.assertIn("SUPERFICIAL LOSS (S.54)", plain.stdout)
            none = _module("taxjson_run", "-C", td, "tax-logic")
            self.assertNotEqual(none.returncode, 0)
            self.assertIn("--country", none.stderr)
            js = json.loads(_module("taxjson_run", "-C", td, "tax-logic",
                                    "--country", "us", "--json").stdout)
            self.assertEqual(js["country"], "usa")
            self.assertIn("US-OPT-01", [r["id"] for s in js["rule_ids"]
                                        for r in s["rules"]])

    def test_ids_are_stable_and_each_statement_has_one(self):
        cat = TL.catalog()
        for rid in cat:
            self.assertRegex(rid, r"^(CA|US)-[A-Z0-9]+(-[A-Z0-9]+)+$")
        # The ids tests cite for the owner's dating rules exist.
        for rid in ("CA-INC-DATE-DIV", "CA-INC-DATE-PIL", "CA-INC-DATE-ROC",
                    "CA-INC-DATE-ROC-TRUST", "CA-INC-DATE-TRUST",
                    "US-INC-DATE-DIV", "US-INC-DATE-ROC", "US-INC-DATE-RIC"):
            self.assertIn(rid, cat)

    def test_checker_passes(self):
        r = subprocess.run([sys.executable,
                            str(REPO_ROOT / "scripts" / "check_tax_rules.py")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestRuleMarkers(unittest.TestCase):
    """The markers themselves: mixed ids refused, and the runtime guard
    stops a one-country test from running the other engine."""

    def test_mixed_and_self_absent_are_refused(self):
        with self.assertRaises(ValueError):
            rule("CA-SL-01", "US-WASH-01")
        with self.assertRaises(ValueError):
            rule_absent("CA-SL-01", country="canada")

    def test_guard_blocks_the_other_engine(self):
        from taxjson.lib.core import CanadaTaxRules, USATaxRules

        @rule("CA-SL-01")
        def ca_only():
            USATaxRules().compute_gains([])

        @rule("CA-SL-01")
        def ca_ok():
            CanadaTaxRules().compute_gains([])

        @rule("CA-SL-01")
        @rule_absent("CA-SL-01", country="usa")
        def dual():
            CanadaTaxRules().compute_gains([])
            USATaxRules().compute_gains([])

        with self.assertRaises(AssertionError) as cm:
            ca_only()
        self.assertIn("ran the United States engine", str(cm.exception))
        ca_ok()
        dual()
        # The guard is lifted afterwards.
        USATaxRules().compute_gains([])


# ------------------------------------------------------------ income dating
class TestIncomeDating(unittest.TestCase):
    """Owner request (2026-09-30): the record-date / pay-date rules stated
    in tax-logic with their own ids, each pinned under both countries.
    A corporation's dividend, a payment in lieu and a corporate or
    foreign return of capital are dated by the day they are PAID; a
    row's date_settle is set to the record / payable date below to show
    the pay date wins under either country's tax_date basis. The
    record-date rules (Phase C: Canadian trusts, US January fund
    dividends) read the parsers' record_date / income_label facts and
    are pinned here and in tests/test_fix_income.py."""

    @staticmethod
    def _years(res):
        return {(t.get("action"), t["date"][:4]) for t in res["transactions"]
                if t.get("action") in ("DIVIDEND", "DIVIDEND_IN_LIEU")}

    def _book(self, sym, action, pay, record, amount=50.0):
        return [tx("BUYSELL", "2025-06-02", sym, 100, 1000),
                tx(action, pay, sym, 0, amount, settle=record,
                   description=f"record {record}")]

    @rule("CA-INC-DATE-DIV")
    @rule("US-INC-DATE-DIV")
    def test_dividend_is_the_pay_years(self):
        book = self._book("XYZ.US", "DIVIDEND", "2026-01-15", "2025-12-31")
        for year, want in ((2025, set()), (2026, {("DIVIDEND", "2026")})):
            r = gains_both(book, year=year)
            for c in C.COUNTRIES:
                with self.subTest(year=year, country=c):
                    self.assertEqual(self._years(r[c]), want)

    @rule("CA-INC-DATE-PIL")
    @rule("US-INC-DATE-DIV")
    def test_payment_in_lieu_is_the_pay_years(self):
        book = self._book("XYZ.US", "DIVIDEND_IN_LIEU", "2026-01-10",
                          "2025-12-30")
        for year, want in ((2025, set()),
                           (2026, {("DIVIDEND_IN_LIEU", "2026")})):
            r = gains_both(book, year=year)
            for c in C.COUNTRIES:
                with self.subTest(year=year, country=c):
                    self.assertEqual(self._years(r[c]), want)

    def _roc_book(self, sym):
        # 100 units at 10; a 1.00/unit return of capital with record /
        # payable date 2025-12-31, PAID 2026-01-15; half sold on
        # 2026-01-08 (between the two dates), half on 2026-02-02. The
        # parsers stamp a ROC row with its posting (pay) date, trade and
        # settle alike (test_roc pins the parser side); the record date
        # is only in the description.
        return [tx("BUYSELL", "2025-06-02", sym, 100, 1000),
                tx("BUYSELL", "2026-01-08", sym, -50, 600),
                tx("ADJUST", "2026-01-15", sym, 0, -100, type="roc",
                   description="RETURN OF CAPITAL record 2025-12-31"),
                tx("BUYSELL", "2026-02-02", sym, -50, 600)]

    def _sale_gains(self, res):
        return [round(t["gain"], 2) for t in _gain_rows(res)]

    @rule("CA-INC-DATE-ROC")
    @rule("US-INC-DATE-ROC")
    def test_return_of_capital_lowers_cost_on_its_pay_date(self):
        r = gains_both(self._roc_book("XYZ.US"), year=2026)
        for c in C.COUNTRIES:
            with self.subTest(country=c):
                # Sale before the pay date: full cost 500 (gain 100);
                # after: 500 - 100 of ROC (gain 200). On the record date
                # it would be 150 / 150.
                self.assertEqual(self._sale_gains(r[c]), [100.0, 200.0])

    @rule("CA-INC-DATE-ROC")
    @rule("US-INC-DATE-ROC")
    def test_parsers_stamp_roc_with_its_posting_date(self):
        """The date the engines place a ROC on comes from the parser: the
        broker's posting (pay) date, for trade and settle alike."""
        import os
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        hdr = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
               "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
               "Account #,Activity Type,Account Type\n")
        row = ("2026-01-15 09:30:00 AM,2026-01-15 12:00:00 AM,DIV,XEI.TO,"
               "ISHARES S&P/TSX RETURN OF CAPITAL ON 500 SHS REC 12/31/25,"
               "0,0.00,0.00,0.00,42.50,CAD\n")
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False) as f:
            f.write(hdr + row)
        try:
            (t,) = QuestradeBrokerage().parse_file(Path(f.name))
        finally:
            os.remove(f.name)
        self.assertEqual((t["action"], t["type"]), ("ADJUST", "roc"))
        self.assertEqual(t["date"][:10], "2026-01-15")
        self.assertEqual(t["date_settle"][:10], "2026-01-15")

    @rule("CA-INC-DATE-ROC-TRUST")
    @rule_absent("CA-INC-DATE-ROC-TRUST", country="usa")
    @rule("US-INC-DATE-ROC")
    def test_canadian_trust_roc_lowers_the_acb_when_payable(self):
        """A Canadian trust's ROC (T3 box 42) lowers the ACB on its record
        date in a Canada project (s.53(2)(h)); a US project keeps the pay
        date. With no record date (the IB case) both use the pay date."""
        book = self._roc_book("XYZ.UN.TO")
        r = gains_both(book, year=2026)
        for c in C.COUNTRIES:
            with self.subTest(country=c, record_date=False):
                self.assertEqual(self._sale_gains(r[c]), [100.0, 200.0])
        book[2].record_date = "2025-12-31"
        r = gains_both(book, year=2026)
        self.assertEqual(self._sale_gains(r["canada"]), [150.0, 150.0])
        self.assertEqual(self._sale_gains(r["usa"]), [100.0, 200.0])

    @rule("CA-INC-DATE-TRUST")
    @rule_absent("CA-INC-DATE-TRUST", country="usa")
    @rule("US-INC-DATE-RIC")
    @rule_absent("US-INC-DATE-RIC", country="canada")
    def test_trust_or_fund_distribution_year(self):
        """A Canadian trust distribution payable in December and paid in
        January is the December year's income in Canada (s.104(13)); a
        US fund dividend listed in ric_january_dividends is Dec 31's in
        the US (§852(b)(7)). Each only in its own country; with neither
        the facts nor the list, the pay year everywhere."""
        trust = self._book("XYZ.UN.TO", "DIVIDEND", "2026-01-20",
                           "2025-12-31")
        fund = self._book("VNQ.US", "DIVIDEND", "2026-01-20", "2025-12-31")
        for book in (trust, fund):
            for year, want in ((2025, set()),
                               (2026, {("DIVIDEND", "2026")})):
                r = gains_both(book, year=year)
                for c in C.COUNTRIES:
                    with self.subTest(sym=book[0].symbol, year=year,
                                      country=c):
                        self.assertEqual(self._years(r[c]), want)
        trust[1].record_date = "2025-12-31"
        trust[1].income_label = "distribution"
        got = {c: gains_both(trust, year=2025)[c] for c in C.COUNTRIES}
        self.assertEqual(self._years(got["canada"]), {("DIVIDEND", "2026")})
        self.assertEqual(self._years(got["usa"]), set())
        got = gains_both(fund, year=2025,
                         usa={"ric_january_dividends": ("VNQ.US",)})
        self.assertEqual(self._years(got["usa"]), {("DIVIDEND", "2026")})
        self.assertEqual(self._years(got["canada"]), set())
        # The list given to Canada is refused, or never applied there
        # (A2-0830: the Canada side was never handed it).
        try:
            ca = gains_both(fund, year=2025,
                            canada={"ric_january_dividends": ("VNQ.US",)})
        except ValueError:
            pass
        else:
            self.assertEqual(self._years(ca["canada"]), set())

    @rule("CA-DATE-11")
    @rule("US-DATE-03")
    def test_interest_is_the_pay_years(self):
        from taxjson.bin.taxjson_sum_income import summarize_income
        rows = [{"action": "INTEREST", "date": "2026-01-02",
                 "date_settle": "2025-12-31", "net_amount": 5.0,
                 "currency": "USD"}]
        self.assertEqual(summarize_income(rows, 2025)["interest_totals"], {})
        self.assertEqual(summarize_income(rows, 2026)["interest_totals"],
                         {"USD": 5.0})


# ------------------------------------------------------------ engine partition
class TestEnginePartition(unittest.TestCase):
    """The owner-named partition rules, each run on ONE book under both
    countries (tax_rules.dual.gains_both): the rule fires in its country
    and not in the other (partition ENGINE-11, COMMANDS-16, INPUTS-15,
    SPEC-19)."""

    @rule("CA-SL-02")
    @rule_absent("CA-SL-02", country="usa")
    @rule("US-WASH-06")
    @rule_absent("US-WASH-06", country="canada")
    def test_still_held_at_day_30(self):
        book = [tx("BUYSELL", "2025-01-02", "AAA.US", 100, 1000,
                   settle="2025-01-03"),
                tx("BUYSELL", "2025-02-03", "AAA.US", -100, 800,
                   settle="2025-02-04"),
                tx("BUYSELL", "2025-02-10", "AAA.US", 100, 800,
                   settle="2025-02-11"),
                tx("BUYSELL", "2025-02-20", "AAA.US", -100, 850,
                   settle="2025-02-21")]
        r = gains_both(book)
        # Canada: the rebuy was sold before day 30 -> loss allowed.
        self.assertEqual(r["canada"]["summary"]["total_disallowed"], 0)
        # US: no still-held test -> 200 disallowed (moved to the rebuy).
        self.assertAlmostEqual(r["usa"]["summary"]["total_disallowed"],
                               200.0, places=2)

    @rule("CA-SL-01")
    @rule_absent("CA-SL-01", country="usa")
    @rule("US-WASH-01")
    @rule_absent("US-WASH-01", country="canada")
    def test_window_settle_dates_vs_trade_dates(self):
        # Settle gap 29 / trade gap 32: Canada denies, US allows.
        a = [tx("BUYSELL", "2025-01-02", "BBB.US", 100, 1000,
                settle="2025-01-03"),
             tx("BUYSELL", "2025-01-30", "BBB.US", -100, 800,
                settle="2025-02-03"),
             tx("BUYSELL", "2025-03-03", "BBB.US", 100, 800,
                settle="2025-03-04")]
        r = gains_both(a)
        self.assertAlmostEqual(r["canada"]["summary"]["total_disallowed"],
                               200.0, places=2)
        self.assertEqual(r["usa"]["summary"]["total_disallowed"], 0)
        # Trade gap 30 / settle gap 34: US denies, Canada allows.
        b = [tx("BUYSELL", "2025-05-01", "BBC.US", 100, 1000,
                settle="2025-05-02"),
             tx("BUYSELL", "2025-06-02", "BBC.US", -100, 800,
                settle="2025-06-03"),
             tx("BUYSELL", "2025-07-02", "BBC.US", 100, 800,
                settle="2025-07-07")]
        r = gains_both(b)
        self.assertEqual(r["canada"]["summary"]["total_disallowed"], 0)
        self.assertAlmostEqual(r["usa"]["summary"]["total_disallowed"],
                               200.0, places=2)

    @rule("CA-ACB-01")
    @rule_absent("CA-ACB-01", country="usa")
    @rule("US-BASIS-01")
    @rule_absent("US-BASIS-01", country="canada")
    def test_average_cost_vs_fifo(self):
        book = [tx("BUYSELL", "2025-01-02", "CCC.US", 100, 1000),
                tx("BUYSELL", "2025-01-03", "CCC.US", 100, 2000),
                tx("BUYSELL", "2025-06-02", "CCC.US", -100, 1500)]
        r = gains_both(book)
        self.assertAlmostEqual(r["canada"]["summary"]["total_gain"], 0.0,
                               places=2)
        self.assertAlmostEqual(r["usa"]["summary"]["total_gain"], 500.0,
                               places=2)

    @rule("US-HOLD-01")
    @rule_absent("US-HOLD-01", country="canada")
    def test_short_and_long_term(self):
        book = [tx("BUYSELL", "2023-01-03", "III.US", 100, 1000),
                tx("BUYSELL", "2024-01-04", "III.US", 100, 1000),
                tx("BUYSELL", "2024-01-05", "III.US", -200, 3000)]
        r = gains_both(book)
        self.assertEqual(sorted(t.get("term") for t in
                                _gain_rows(r["usa"])),
                         ["LONG_TERM", "SHORT_TERM"])
        self.assertTrue(all(t.get("term") is None
                            for t in _gain_rows(r["canada"])))

    @rule("CA-OPT-01")
    @rule_absent("CA-OPT-01", country="usa")
    @rule("US-OPT-01")
    @rule_absent("US-OPT-01", country="canada")
    def test_grant_timing_vs_close_timing(self):
        book = [tx("BUYSELL", "2025-11-03", "EEE251219C00050000.US", -1,
                   300),
                tx("BUYSELL", "2026-01-05", "EEE251219C00050000.US", 1,
                   100)]
        by_year = {}
        for year in (2025, 2026):
            r = gains_both(book, year=year,
                           canada=dict(option_premium_timing="grant",
                                       option_grant_since=2025))
            for c in C.COUNTRIES:
                by_year[(c, year)] = round(r[c]["summary"]["total_gain"], 2)
        self.assertEqual(by_year[("canada", 2025)], 300.0)
        self.assertEqual(by_year[("canada", 2026)], -100.0)
        self.assertEqual(by_year[("usa", 2025)], 0.0)
        self.assertEqual(by_year[("usa", 2026)], 200.0)

    @rule("CA-SL-05")
    @rule_absent("CA-SL-05", country="usa")
    @rule("US-WASH-12")
    @rule_absent("US-WASH-12", country="canada")
    def test_long_call_replacement(self):
        book = [tx("BUYSELL", "2025-01-02", "FFF.US", 100, 1000),
                tx("BUYSELL", "2025-02-03", "FFF.US", -100, 800),
                tx("BUYSELL", "2025-02-10", "FFF251219C00010000.US", 1, 50)]
        r = gains_both(book)
        self.assertAlmostEqual(r["canada"]["summary"]["total_disallowed"],
                               200.0, places=2)
        self.assertEqual(r["usa"]["summary"]["total_disallowed"], 0)
        self.assertTrue(r["usa"].get("option_replacement_warnings"))

    @rule("CA-ACB-07")
    @rule_absent("CA-ACB-07", country="usa")
    @rule("US-ROC-02", "US-ROC-03")
    @rule_absent("US-ROC-03", country="canada")
    def test_roc_beyond_cost_and_after_exit(self):
        book = [tx("BUYSELL", "2025-01-02", "DDD.US", 100, 1000),
                tx("ADJUST", "2025-03-01", "DDD.US", 0, -1500, type="roc"),
                tx("BUYSELL", "2025-04-01", "DDD.US", -100, 1200)]
        r = gains_both(book)
        # Beyond the cost: a deemed gain in both countries — Canada
        # s.40(3), the US §301(c)(3) (booked since partition phase B) —
        # then 1200 on the sale at a nil/zero basis.
        for c in C.COUNTRIES:
            self.assertAlmostEqual(r[c]["summary"]["total_gain"], 1700.0,
                                   places=2)
            self.assertEqual([round(t["gain"], 2) for t in
                              r[c]["transactions"] if t.get("deemed")],
                             [500.0])
        # Received with no shares held: Canada books it (s.40(3)); the
        # US does not apply it (a warning: report by hand).
        after = [tx("BUYSELL", "2025-01-02", "DDE.US", 100, 1000),
                 tx("BUYSELL", "2025-03-01", "DDE.US", -100, 1200),
                 tx("ADJUST", "2025-04-01", "DDE.US", 0, -300, type="roc")]
        r = gains_both(after)
        self.assertAlmostEqual(r["canada"]["summary"]["total_gain"], 500.0,
                               places=2)
        self.assertAlmostEqual(r["usa"]["summary"]["total_gain"], 200.0,
                               places=2)
        self.assertIn("no open long lots", r["usa"]["_stderr"])

    @rule("US-WASH-05")
    @rule_absent("US-WASH-05", country="canada")
    @rule("CA-SL-07")
    @rule_absent("CA-SL-07", country="usa")
    def test_reshort_after_short_cover_loss(self):
        book = [tx("BUYSELL", "2025-01-02", "JJJ.US", -100, 1000),
                tx("BUYSELL", "2025-02-03", "JJJ.US", 100, 1200),
                tx("BUYSELL", "2025-02-10", "JJJ.US", -100, 1200),
                tx("BUYSELL", "2025-04-01", "JJJ.US", 100, 1100)]
        r = gains_both(book)
        # Canada: shorting again acquires nothing -> the -200 stands.
        self.assertEqual(r["canada"]["summary"]["total_disallowed"], 0)
        # US (Reg. 1.1091-1(g)): the re-short is a replacement.
        self.assertAlmostEqual(r["usa"]["summary"]["total_disallowed"],
                               200.0, places=2)

    @rule("US-WASH-11")
    @rule_absent("US-WASH-11", country="canada")
    @rule("CA-SL-02")
    def test_sheltered_rebuy_sold_before_day_30(self):
        book = [tx("BUYSELL", "2025-01-02", "HHH.US", 100, 1000),
                tx("BUYSELL", "2025-02-03", "HHH.US", -100, 800)]
        ira = [tx("BUYSELL", "2025-02-05", "HHH.US", 100, 800,
                  account="55500002"),  # pii-ok
               tx("BUYSELL", "2025-02-12", "HHH.US", -100, 820,
                  account="55500002")]  # pii-ok
        r = gains_both(book, sheltered=ira)
        perm = {c: round(sum(t.get("permanently_disallowed", 0.0) or 0.0
                             for t in r[c]["transactions"]), 2)
                for c in C.COUNTRIES}
        # Canada: the registered rebuy is gone by day 30 -> allowed.
        self.assertEqual(r["canada"]["summary"]["total_disallowed"], 0)
        self.assertEqual(perm["canada"], 0)
        # US: an IRA replacement makes the loss permanent (no held test).
        self.assertAlmostEqual(perm["usa"], 200.0, places=2)

    @rule("CA-DATE-02")
    @rule("US-DATE-02")
    def test_explicit_tax_date_overrides_the_country_default(self):
        book = [tx("BUYSELL", "2025-06-02", "GGG.US", 100, 1000),
                tx("BUYSELL", "2025-12-31", "GGG.US", -100, 1500,
                   settle="2026-01-02")]
        for basis, want in (("trade", "2025"), ("settle", "2026")):
            r = gains_both(book, year=int(want), tax_date=basis)
            for c in C.COUNTRIES:
                with self.subTest(basis=basis, country=c):
                    self.assertEqual(len(_gain_rows(r[c])), 1)
                    self.assertEqual(r[c]["summary"]["tax_date_basis"],
                                     basis)

    @rule("CA-DATE-01")
    @rule("US-DATE-01")
    def test_us_ladder_defaults_to_trade_dates(self):
        """ENGINE-13: event_sort_key's default basis follows the ladder's
        country (the US one used Canada's settle-first date)."""
        from taxjson.lib.corporate_timeline import event_sort_key
        a = tx("BUYSELL", "2025-12-31", "X.US", 1, 1, settle="2026-01-02")
        b = tx("BUYSELL", "2026-01-01", "X.US", 1, 1, settle="2026-01-01")
        us = sorted([b, a], key=lambda t: event_sort_key(t, profile="us_main"))
        ca = sorted([a, b], key=lambda t: event_sort_key(t, profile="ca_main"))
        self.assertEqual([t.date for t in us], ["2025-12-31", "2026-01-01"])
        self.assertEqual([t.date for t in ca], ["2026-01-01", "2025-12-31"])

    @rule("CA-DATE-01")
    @rule_absent("CA-DATE-01", country="usa")
    @rule("US-DATE-01")
    @rule_absent("US-DATE-01", country="canada")
    def test_year_end_sale_lands_in_the_countrys_year(self):
        book = [tx("BUYSELL", "2025-06-02", "GGG.US", 100, 1000),
                tx("BUYSELL", "2025-12-31", "GGG.US", -100, 1500,
                   settle="2026-01-02")]
        years = {}
        for year in (2025, 2026):
            r = gains_both(book, year=year)
            for c in C.COUNTRIES:
                years[(c, year)] = len(_gain_rows(r[c]))
        self.assertEqual((years[("canada", 2025)], years[("canada", 2026)]),
                         (0, 1))
        self.assertEqual((years[("usa", 2025)], years[("usa", 2026)]),
                         (1, 0))


if __name__ == "__main__":
    unittest.main()
