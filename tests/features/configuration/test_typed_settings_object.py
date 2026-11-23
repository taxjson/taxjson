"""Step 2 of the refactoring toward the 2026-11-02 architecture review (F5): one
typed Settings object.

The object every reader gets from `cfg["settings"]`, and the mapping it keeps
up meanwhile. Synthetic data only.

Formerly: test_refactor_step2
"""
import copy
import os
import pickle
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.country import SETTING_COUNTRY
from taxjson.project.settings import KEYS, Settings
import _paths

_TABLE = {"year": 2024, "country": "canada", "base_currency": "CAD",
          "source_currencies": ["USD"], "colour": "blue"}


class TestTheObject(unittest.TestCase):
    def test_a_field_for_every_setting_in_the_table_s_order(self):
        self.assertEqual(KEYS, tuple(SETTING_COUNTRY))

    def test_the_folder_keys_project_layout_names_are_fields(self):
        """TJ-1613: `_freeze_inputs` reads them as attributes and rewrites them
        through the constants."""
        from taxjson.lib import project_layout as PL
        self.assertIn(PL.INPUTS_KEY, KEYS)
        self.assertIn(PL.TOBASE_KEY, KEYS)

    def test_what_the_file_sets_is_an_attribute_and_the_rest_is_none(self):
        s = Settings.from_table(_TABLE)
        self.assertEqual((s.year, s.country, s.base_currency), (2024, "canada", "CAD"))
        self.assertIsNone(s.province)
        self.assertEqual(s.extra, {"colour": "blue"})

    def test_it_is_frozen(self):
        s = Settings.from_table(_TABLE)
        with self.assertRaises(Exception):
            s.year = 2025
        with self.assertRaises(TypeError):
            s["year"] = 2025

    def test_it_equals_another_of_the_same_items_in_any_order(self):
        """TJ-1593: `==` on the items, in any order; unhashable as the dict
        was. A dict is another thing now (TJ-1635: the mapping base went)."""
        s = Settings.from_table(_TABLE)
        self.assertEqual(s, Settings.from_table(dict(reversed(list(_TABLE.items())))))
        self.assertNotEqual(s, Settings.from_table({**_TABLE, "year": 2025}))
        self.assertEqual(s.as_dict(), _TABLE)
        with self.assertRaises(TypeError):
            hash(s)

    def test_the_unknown_keys_can_t_be_changed_either(self):
        s = Settings.from_table(_TABLE)
        with self.assertRaises(TypeError):
            s.extra["colour"] = "red"

    def test_replace_makes_a_new_one_with_the_key_present(self):
        s = Settings.from_table({"year": 2024, "country": "usa"})
        t = s.replace(base_currency="USD")
        self.assertEqual((s.base_currency, t.base_currency), (None, "USD"))
        self.assertFalse(s.has("base_currency"))
        self.assertEqual(t.present, ("year", "country", "base_currency"))
        with self.assertRaises(TypeError):
            s.replace(colour="red")
        u = t.replace(base_currency=None)
        self.assertFalse(u.has("base_currency"))
        self.assertEqual(u.present, ("year", "country"))

    def test_a_copy_and_a_pickle_are_the_same_object_again(self):
        """TJ-1601: the dict could be deep-copied; so can this."""
        s = Settings.from_table(_TABLE)
        for t in (copy.deepcopy(s), pickle.loads(pickle.dumps(s)),
                  copy.deepcopy({"settings": s})["settings"]):
            self.assertIsInstance(t, Settings)
            self.assertEqual(t, s)
            self.assertEqual(t.present, s.present)


class TestTheTypedSpellings(unittest.TestCase):
    """`has(key)` and `empty`: the typed spellings of `"key" in settings`
    and the truth test, which go with the mapping base (TJ-1635)."""
    def test_has_and_empty(self):
        from taxjson.project.accounts import estimate_of
        from taxjson.project.settings import as_settings
        s = as_settings({"year": 2025, "tax_date": None})
        self.assertTrue(s.has("year"))
        self.assertFalse(s.has("tax_date"))         # None is absent, as replace reads it
        self.assertEqual(s.present, ("year",))
        self.assertFalse(s.has("country"))
        self.assertFalse(s.empty)
        self.assertTrue(s.replace(year=None).empty)
        self.assertTrue(as_settings(None).empty)
        self.assertTrue(as_settings({}).empty)
        self.assertTrue(estimate_of({"estimate": {"amt_carryover": {}}}).has("amt_carryover"))
        self.assertFalse(estimate_of({}).has("amt_carryover"))


class TestNoMappingAnyMore(unittest.TestCase):
    """TJ-1635: the mapping base went with the last dict-era reader. The
    object is its attributes, `has`, `empty`, `present`, `extra` and
    `as_dict`; a key the file leaves out is absent, not None."""
    def test_the_dict_era_spellings_are_gone(self):
        s = Settings.from_table(_TABLE)
        for spelling in (lambda: s["country"], lambda: "year" in s,
                         lambda: dict(s), lambda: list(s), lambda: len(s),
                         lambda: s.get("year"), lambda: s.keys()):
            with self.assertRaises((TypeError, AttributeError)):
                spelling()

    def test_a_dict_is_another_thing(self):
        from taxjson.project.settings import as_settings
        from taxjson.lib import requires
        from taxjson.pipeline.config import _unknown_setting_warnings
        s = Settings.from_table(_TABLE)
        self.assertNotEqual(s, _TABLE)              # never equal, never an error
        self.assertFalse(s in [_TABLE, {}])
        self.assertIn("colour", repr(s))
        self.assertTrue(as_settings(5).empty)       # no table: empty, the check names it
        self.assertEqual(requires.features_of(Settings.from_table({"tobase_map": "x"})),
                         requires.features_of({"tobase_map": "x"}))
        # a `settings` that is no table: no warning, load_config's own words
        self.assertEqual(_unknown_setting_warnings({"settings": 5}), [])
        self.assertEqual(_unknown_setting_warnings({"settings": {"colour": 1}}),
                         _unknown_setting_warnings({"settings": s}))
        # the requires check reads the object as it read the dict
        self.assertEqual(requires.problem(Settings.from_table({"requires_taxjson": ">=99.0.0"})),
                         requires.problem({"requires_taxjson": ">=99.0.0"}))

    def test_a_key_left_out_stays_absent(self):
        """`settings.tax_date` is None for an absent key and `has` tells
        the two apart: the country's default on None, the messages on
        `has`."""
        s = Settings.from_table({"year": 2024, "country": "canada"})
        self.assertIsNone(s.tax_date)
        self.assertFalse(s.has("tax_date"))
        self.assertEqual(sorted(s.present), ["country", "year"])
        self.assertEqual(s.as_dict(), {"year": 2024, "country": "canada"})
        self.assertEqual(Settings.from_table(_TABLE).extra, {"colour": "blue"})

class TestTheAccountAndEstimateTables(unittest.TestCase):
    """Step 2c: typed views beside the tables (project/accounts)."""
    _CFG = {"accounts": {"margin": {"type": "taxable", "crypto": False, "colour": "red"},
                         "rrsp": {"type": "sheltered", "plan": "rrsp"}},
            "estimate": {"other_income": 50000, "amt_carryover": {"2023": 10.0}}}

    def test_the_fields_are_the_documented_keys(self):
        """The one source of the key lists (lib/config_check re-exports them):
        a renamed field would silently change what the config check accepts."""
        from taxjson.lib import config_check as CC
        from taxjson.project.accounts import ACCOUNT_KEYS, ESTIMATE_KEYS
        self.assertEqual(ACCOUNT_KEYS, (
            "type", "crypto", "transfers", "plan", "brokerage", "account",
            "query_id", "holdings", "broker_accounts", "combined_broker_accounts",
            "exercise_fee", "year_end_posting", "custodian_country"))
        self.assertEqual(ESTIMATE_KEYS, (
            "other_income", "other_losses", "deductions", "carrying_charges",
            "long_term_losses", "amt_carryover"))
        self.assertIs(CC.ACCOUNT_KEYS, ACCOUNT_KEYS)

    def test_accounts_of_names_each_in_the_file_s_order(self):
        from taxjson.project.accounts import AccountSettings, accounts_of
        accts = accounts_of(self._CFG)
        self.assertEqual(list(accts), ["margin", "rrsp"])
        m = accts["margin"]
        self.assertIsInstance(m, AccountSettings)
        self.assertEqual((m.name, m.type, m.crypto, m.plan), ("margin", "taxable", False, None))
        self.assertTrue(m.taxable)
        self.assertFalse(m.sheltered)
        self.assertEqual(m.extra, {"colour": "red"})
        self.assertEqual(m.as_dict(), self._CFG["accounts"]["margin"])
        self.assertTrue(accts["rrsp"].sheltered)
        self.assertEqual(accounts_of({}), {})
        self.assertEqual(accounts_of(None), {})

    def test_an_entry_that_is_no_table_is_still_named(self):
        from taxjson.project.accounts import accounts_of
        accts = accounts_of({"accounts": {"m": 42}})
        self.assertEqual(list(accts), ["m"])
        self.assertIsNone(accts["m"].type)

    def test_the_tables_stay_dicts_in_the_config(self):
        from taxjson.project.accounts import accounts_of
        import json
        accounts_of(self._CFG)
        self.assertIsInstance(self._CFG["accounts"]["margin"], dict)
        json.dumps(self._CFG)

    def test_estimate_of(self):
        from taxjson.project.accounts import EstimateSettings, estimate_of
        e = estimate_of(self._CFG)
        self.assertIsInstance(e, EstimateSettings)
        self.assertEqual((e.other_income, e.other_losses, e.amt_carryover),
                         (50000, None, {"2023": 10.0}))
        self.assertEqual(e.as_dict(), self._CFG["estimate"])
        self.assertTrue(estimate_of({}).empty)
        self.assertTrue(estimate_of({"estimate": 5}).empty)

    def test_a_copy_keeps_the_name(self):
        import copy
        import pickle
        from taxjson.project.accounts import accounts_of
        m = accounts_of(self._CFG)["margin"]
        for t in (copy.deepcopy(m), pickle.loads(pickle.dumps(m)), m.replace(crypto=True)):
            self.assertEqual(t.name, "margin")
        self.assertTrue(m.replace(crypto=True).crypto)


class TestHelpersTakeEitherShape(unittest.TestCase):
    """The helpers with callers of both kinds (the run's typed view, a
    query's raw table) type theirs at entry and read attributes."""
    TABLES = {"margin": {"type": "taxable"}, "tfsa": {"type": "sheltered"},
              "coin": {"type": "taxable", "crypto": True},
              "moves": {"type": "taxable", "transfers": True}}

    def test_a_view_passes_through_and_a_dict_is_wrapped(self):
        from taxjson.project.accounts import (
            AccountSettings, accounts_of, typed_account, typed_accounts)
        view = accounts_of({"accounts": self.TABLES})
        self.assertIs(typed_accounts(view), view)
        self.assertIs(typed_account(view["tfsa"], "tfsa"), view["tfsa"])
        self.assertIs(typed_account(view["tfsa"]), view["tfsa"])
        self.assertEqual(typed_account({"type": "taxable"}).name, "")
        renamed = typed_account(view["tfsa"], "other")
        self.assertEqual((renamed.name, renamed.type, renamed.present),
                         ("other", "sheltered", view["tfsa"].present))
        typed = typed_accounts(self.TABLES)
        self.assertIsInstance(typed["coin"], AccountSettings)
        self.assertTrue(typed["coin"].crypto)
        self.assertEqual(typed_accounts(5), {})
        self.assertEqual(typed_account(5, "x").name, "x")

    def test_the_records_folder_is_no_run_s_files(self):
        from taxjson.lib.checklist import Ctx, has_books
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "work"
            (cache / ".deps").mkdir(parents=True)
            ctx = Ctx(root=Path(td), cfg={}, year=2025, today=None, run_sub=None)
            self.assertFalse(has_books(ctx))
            (cache / "m_base.json").write_text("{}")
            # A book is a named account's (lib/clean holds_books).
            (Path(td) / "taxjson.toml").write_text("[accounts.m]\n")
            self.assertTrue(has_books(ctx))

    def test_configured_accounts_leaves_an_entry_that_is_no_table_out(self):
        from taxjson.lib.checklist import Ctx
        from taxjson.project.accounts import (
            accounts_of, configured_accounts, sheltered_by_name)
        tables = dict(self.TABLES, bad=42)
        self.assertEqual(sorted(accounts_of({"accounts": tables})), ["bad", "coin", "margin", "moves", "tfsa"])
        self.assertEqual(sorted(configured_accounts(tables)), ["coin", "margin", "moves", "tfsa"])
        view = configured_accounts(tables)
        self.assertIs(configured_accounts(view), view)
        self.assertEqual(configured_accounts(5), {})
        self.assertEqual(sheltered_by_name(view),
                         {"margin": False, "tfsa": True, "coin": False, "moves": False})
        self.assertEqual(sheltered_by_name(configured_accounts({"x": {}})), {})
        ctx = Ctx(root=Path("."), cfg={"accounts": 5}, year=2025, today=None,
                  run_sub=None)
        self.assertEqual(ctx.accounts, {})

    def test_the_helpers_give_one_answer_for_both_shapes(self):
        from taxjson.pipeline import stages
        from taxjson.project.accounts import accounts_of
        view = accounts_of({"accounts": self.TABLES})
        plans = stages._in_kind_plans(self.TABLES, "canada")
        self.assertEqual(plans, stages._in_kind_plans(view, "canada"))
        self.assertEqual(plans, {"margin": "", "tfsa": "tfsa"})
        self.assertEqual(stages._account_plan("tfsa", {"type": "sheltered"}, "canada"),
                         stages._account_plan("tfsa", view["tfsa"], "canada"))

    def test_the_run_helpers_give_one_answer_for_both_shapes(self):
        from taxjson.pipeline import run as R, sanity as SA, stages
        from taxjson.project.accounts import accounts_of
        from taxjson.project.settings import as_settings
        tables = dict(self.TABLES, margin={"type": "taxable", "holdings": "h.csv"})
        view = accounts_of({"accounts": tables})
        settings = as_settings({"year": 2025, "country": "canada", "base_currency": "CAD",
                                "option_premium_timing": "grant"})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "margin_base.json").write_text('{"transactions": []}')
            # the books helper reads the book: no written option in it
            self.assertIs(R._books_write_options(root, view), False)
            for a, b in ((tables, view), (view, tables)):
                self.assertEqual(R._grant_since_warning(settings, root, a),
                                 R._grant_since_warning(settings, root, b))
                self.assertEqual(SA._sanity_items_from_config(a, root, 2025),
                                 SA._sanity_items_from_config(b, root, 2025))
                self.assertEqual(stages._paired_transfers(root, root / "work", a),
                                 stages._paired_transfers(root, root / "work", b))

    def test_the_tt_and_rename_helpers_give_one_answer_for_both_shapes(self):
        from taxjson.lib import check_tt as CT, dated_events as DE
        from taxjson.project.accounts import accounts_of
        view = accounts_of({"accounts": self.TABLES})
        for n, table in self.TABLES.items():
            self.assertEqual(CT._reads_inkind(table), CT._reads_inkind(view[n]))
            self.assertEqual(CT._is_crypto(table), CT._is_crypto(view[n]))
            self.assertEqual(DE.account_kind(table), DE.account_kind(view[n]))
        self.assertTrue(CT._reads_inkind(view["margin"]))
        self.assertFalse(CT._reads_inkind(view["moves"]))
        self.assertFalse(CT._is_crypto(None))

    def test_the_estimate_table_is_read_typed(self):
        from taxjson.lib import carryforward as CF
        from taxjson.project.accounts import estimate_of
        with tempfile.TemporaryDirectory() as td:
            cfg = {"estimate": {"amt_carryover": {"2024": 10.0}}}
            got = CF.resolve_amt(Path(td), {"year": 2025}, cfg["estimate"])
            self.assertEqual(got["by_year"], {2024: 10.0})
            self.assertEqual(CF.resolve_amt(Path(td), {"year": 2025}, estimate_of(cfg)),
                             got)
            self.assertEqual(estimate_of({"estimate": 5}).other_income, None)
            self.assertEqual(CF.resolve_amt(Path(td), {"year": 2025}, 5)["source"], None)


class TestTheTypedTablesReachEveryGuard(unittest.TestCase):
    """A helper that guarded an account table with `isinstance(x, dict)` let
    the typed view past it (TJ-1709: a sheltered account's ALLOWLOSS line was
    no longer refused). The guards take any Mapping, and this holds them to
    it (TJ-1710)."""
    def test_a_sheltered_account_s_allowloss_is_refused_with_the_typed_view(self):
        from taxjson.lib import loss_overrides as LO
        from taxjson.project.accounts import accounts_of
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "plan").mkdir(parents=True)
            (root / "inputs" / "plan" / "x.tt").write_text(
                'ALLOWLOSS 2025-12-19 QZA.TO reason="r"\n')
            for accounts in ({"plan": {"type": "sheltered"}},
                             accounts_of({"accounts": {"plan": {"type": "sheltered"}}})):
                with self.assertRaises(LO.LossOverrideError) as cm:
                    LO.read_project(root, accounts)
                self.assertIn("sheltered", str(cm.exception))

    def test_no_guard_on_an_account_table_asks_for_a_dict(self):
        import re
        src = _paths.REPO / "src" / "taxjson"
        # `type` is an account key when the statement compares it with an
        # account type: transactions and slips carry a `type` of their own.
        keys = r'"(crypto|transfers|holdings|plan|brokerage)"'
        hits = []
        for p in sorted(src.rglob("*.py")):
            lines = p.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                for m in re.finditer(r"isinstance\((\w+), dict\)", line):
                    var = m.group(1)
                    stmt = "\n".join(lines[i:i + 3])
                    if re.search(rf"\b{var}\.get\({keys}", stmt) \
                            or re.search(rf"{keys} (not )?in {var}\b", stmt) \
                            or (re.search(rf"\b{var}\.get\(\"type\"", stmt)
                                and re.search(r'"(taxable|sheltered)"', stmt)):
                        hits.append(f"{p.relative_to(src.parent.parent)}:{i + 1}: {line.strip()[:80]}")
        self.assertEqual(hits, [])


class TestOneConfigRead(unittest.TestCase):
    """load_config with and without missing_ok is one body (TJ-1640): the same
    refusals on a file that is there."""
    _OK = '[settings]\nyear = 2024\ncountry = "canada"\n[accounts.m]\ntype = "taxable"\n'

    def _exit(self, root, soft):
        import contextlib
        import io
        from taxjson.pipeline.config import load_config
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                load_config(root, missing_ok=soft)
        except SystemExit as e:
            # _die hands the message to the top level as the exit code.
            return " ".join((str(e.code) + " " + err.getvalue()).split())
        return None

    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_a_newer_project_is_the_full_read_s_refusal_only(self):
        """The dispatcher refuses it once per command, with its exemptions
        (`tjs tax-logic` renders a project it cannot run): the soft read reads."""
        from taxjson.pipeline.config import load_config
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(self._OK.replace(
                "[accounts.m]", 'requires_taxjson = ">=99.0.0"\n[accounts.m]'))
            self.assertIn("needs taxjson 99.0.0", self._exit(root, False) or "")
            self.assertEqual(load_config(root, missing_ok=True)["settings"].year, 2024)

    def test_a_bad_account_name_is_the_headline_for_both_reads(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # A second account without a type: the name is still the headline,
            # not "invalid account types:" over both (TJ-1686).
            (root / "taxjson.toml").write_text(self._OK.replace(
                '[accounts.m]\ntype = "taxable"\n',
                '[accounts."../x"]\ntype = "taxable"\n[accounts.n]\ncrypto = false\n'))
            for soft in (True, False):
                self.assertIn("is not a valid account name", (self._exit(root, soft) or "").split("—")[0], soft)

    def test_a_scalar_accounts_table_is_refused_by_both_reads(self):
        """TJ-1688: `accounts = 5` was a traceback in the soft read."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text('accounts = 5\n[settings]\nyear = 2024\ncountry = "canada"\n')
            for soft in (True, False):
                self.assertIn("[accounts] must be a table", self._exit(root, soft) or "", soft)

    def test_a_dangling_symlink_is_the_same_refusal_for_both(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").symlink_to(root / "nowhere.toml")
            for soft in (True, False):
                self.assertIn("symlink", self._exit(root, soft) or "", soft)

    def test_no_file_is_empty_softly_and_a_stop_otherwise(self):
        from taxjson.pipeline.config import load_config
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_config(Path(td), missing_ok=True), {})
            self.assertIn("taxjson.toml", self._exit(Path(td), False) or "")

    def test_only_the_full_read_names_the_project_in_the_environment(self):
        from taxjson.lib import project_layout as PL
        from taxjson.pipeline.config import load_config
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(self._OK)
            os.environ.pop(PL.ENV_PROJECT_ROOT, None)
            load_config(root, missing_ok=True)
            self.assertNotIn(PL.ENV_PROJECT_ROOT, os.environ)
            load_config(root)
            self.assertEqual(os.environ.get(PL.ENV_PROJECT_ROOT), str(root.resolve()))


class TestTheTomlParser(unittest.TestCase):
    """project/toml_io: the one parser."""
    def test_read_toml_drops_a_bom(self):
        from taxjson.project.toml_io import read_toml
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.toml"
            p.write_bytes(b"\xef\xbb\xbf[settings]\nyear = 2024\n")
            self.assertEqual(read_toml(p), {"settings": {"year": 2024}})


class TestOneBomPolicy(unittest.TestCase):
    """TJ-1661: one parser, one BOM policy: a `.toml` saved with a byte-order
    mark reads the same from text and from a path. TJ-1655: the layout's
    wrapper is `parse_config_text`."""
    def test_a_bom_reads_everywhere(self):
        from taxjson.lib.project_layout import LayoutError, parse_config_text
        from taxjson.project.toml_io import parse_toml, read_toml
        self.assertEqual(parse_toml("\ufeff[settings]\nyear = 2025\n"), {"settings": {"year": 2025}})
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.toml"
            p.write_bytes(b"\xef\xbb\xbf[settings]\nyear = 2025\n")
            self.assertEqual(read_toml(p), {"settings": {"year": 2025}})
        self.assertEqual(parse_config_text("\ufeffyear = 1\n"), {"year": 1})
        with self.assertRaises(LayoutError):
            parse_config_text("= 1")

    def test_the_decode_policy_is_one(self):
        from taxjson.project.toml_io import decode_toml_bytes
        self.assertEqual(decode_toml_bytes(b"\xef\xbb\xbfa = 1\r\nb = 2\rc = 3\n"),
                         "a = 1\nb = 2\nc = 3\n")
        with self.assertRaises(UnicodeDecodeError):
            decode_toml_bytes(b"a = \xff")

    def test_a_report_saved_with_a_bom_or_bare_cr_still_reads(self):
        from taxjson.lib import holdings_dir, positions_reports
        from taxjson.project.toml_io import read_toml
        body = (b'[meta]\nkind = "holdings"\naccount = "margin"\nas_of = "2025-12-31"\n'
                b'[[holding]]\nsymbol = "QZA.TO"\nquantity = 10\n')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "margin_holdings.toml"
            p.write_bytes(b"\xef\xbb\xbf" + body)
            self.assertEqual(read_toml(p)["meta"]["account"], "margin")
            self.assertEqual(holdings_dir._meta(p).get("account"), "margin")
            self.assertTrue(positions_reports._is_holdings_toml(p))
            p.write_bytes(body.replace(b"\n", b"\r"))       # classic-Mac endings
            self.assertEqual(read_toml(p)["meta"]["account"], "margin")
            self.assertEqual(holdings_dir._meta(p).get("account"), "margin")


class TestTheReaders(unittest.TestCase):
    """Both readers hand it out, after today's checks canonicalised the table."""
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _project(self, top):
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "taxjson.toml").write_text(
            '[settings]\nyear = 2024\ncountry = "CA"\nbase_currency = " cad "\n'
            'tax_date = "settle"\nsource_currencies = ["usd"]\nlocal_timezone = "America/Toronto"\n'
            'colour = "blue"\n\n[accounts.margin]\ntype = "taxable"\n')

    def test_load_config_and_the_soft_reader(self):
        from taxjson.pipeline.config import load_config, load_settings
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            self._project(top)
            for s in (load_config(top)["settings"], load_settings(top),
                      load_config(top, missing_ok=True)["settings"],
                      load_settings(top, missing_ok=True)):
                self.assertIsInstance(s, Settings)
                self.assertEqual((s.country, s.base_currency, s.source_currencies),
                                 ("canada", "CAD", ["USD"]))
                self.assertEqual(s.extra, {"colour": "blue"})
            self.assertEqual(load_config(top / "nowhere", missing_ok=True), {})
            empty = load_settings(top / "nowhere", missing_ok=True)
            self.assertIsInstance(empty, Settings)
            self.assertTrue(empty.empty)
            self.assertEqual(empty.as_dict(), {})

    def test_the_check_after_loading_canonicalises_the_object_too(self):
        """TJ-1600: a raw table handed in typed is still canonicalised."""
        from taxjson.pipeline.config import validate_config
        cfg = {"settings": Settings.from_table(
            {"year": 2024, "country": "CA", "base_currency": " cad "}),
            "accounts": {"margin": {"type": "taxable"}}}
        validate_config(cfg)
        self.assertEqual((cfg["settings"].country, cfg["settings"].base_currency),
                         ("canada", "CAD"))

    def test_the_unknown_key_is_still_warned_about(self):
        from taxjson.pipeline.config import load_config, validate_config
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            self._project(top)
            cfg = load_config(top)
            warnings = validate_config(cfg) if "settings" in cfg else []
            self.assertTrue(any("colour" in str(w) for w in warnings), warnings)


if __name__ == "__main__":
    unittest.main()
