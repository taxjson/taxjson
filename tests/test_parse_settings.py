"""`ParseSettings` (step 5h): what a parse is told about the project,
built from the tool's arguments and applied by the base class; a parser
run bare keeps its class defaults."""
import dataclasses
import unittest
from types import SimpleNamespace

from taxjson.bin.taxjson_brokerage import _parse_settings
from taxjson.parsers import registry
from taxjson.lib.country import parse_basis
from taxjson.parsers.settings import ParseSettings
from tax_rules import rule
from taxjson.rules import ids as R


def _args(**kw):
    base = dict(country=None, account_type=None, combined_broker_accounts=False,
                transfers_out=None, transfers=False, foreign_roc=None,
                futures_settle="trade", exercise_fee=None)
    base.update(kw)
    return SimpleNamespace(**base)


class TestParseSettings(unittest.TestCase):
    def test_the_tool_builds_it_from_its_flags(self):
        s = _parse_settings(_args(), [])
        self.assertEqual((s.country, s.account_taxable, s.combined_broker_accounts,
                          s.transfer_costs_checked_downstream,
                          s.stablecoins_as_cash, s.plans_taxed, s.exercise_fee),
                         (None, None, False, False, False, frozenset(), None))
        s = _parse_settings(_args(country="canada", account_type="taxable",
                                  combined_broker_accounts=True, transfers_out="x.json",
                                  exercise_fee=1.5), [("ABC", "*", "ABC.TO")])
        self.assertEqual((s.account_taxable, s.combined_broker_accounts,
                          s.transfer_costs_checked_downstream,
                          s.stablecoins_as_cash, s.security_overrides, s.exercise_fee),
                         (True, True, True, True, (("ABC", "*", "ABC.TO"),), 1.5))
        self.assertEqual(s.plans_taxed, frozenset())        # Canada taxes no plan as ordinary
        s = _parse_settings(_args(country="usa", account_type="sheltered",
                                  transfers_out="x.json", transfers=True, foreign_roc="acb"), [])
        self.assertEqual((s.account_taxable, s.transfer_costs_checked_downstream,
                          s.stablecoins_as_cash),
                         (False, False, False))
        from taxjson.lib.country import plans_taxed_as_ordinary
        self.assertEqual(s.plans_taxed, plans_taxed_as_ordinary("usa"))
        self.assertIn("tfsa", {p.lower() for p in s.plans_taxed})

    @rule(R.CA_CRYPTO_02)
    @rule(R.US_CRYPTO_02)
    def test_the_basis_is_given_never_derived(self):
        # Recommendation 8's second slice: the country decides the basis
        # (lib/country parse_basis), the object takes it as given.
        self.assertFalse(ParseSettings(country=None).stablecoins_as_cash)
        self.assertEqual(ParseSettings(country=None).plans_taxed, frozenset())
        self.assertTrue(ParseSettings(country="canada", **parse_basis("canada")).stablecoins_as_cash)
        self.assertFalse(ParseSettings(country="usa", **parse_basis("usa")).stablecoins_as_cash)
        self.assertEqual((parse_basis(None), parse_basis(" ")),
                         ({"stablecoins_as_cash": False, "plans_taxed": frozenset()},) * 2)
        # A country's parse is never booked on the neutral basis by omission.
        for country in ("canada", "usa"):
            with self.assertRaises(TypeError):
                ParseSettings(country=country)
        # The basis without a country, or against it, is the caller's to choose.
        self.assertTrue(ParseSettings(stablecoins_as_cash=True).stablecoins_as_cash)
        self.assertFalse(ParseSettings(country="canada", stablecoins_as_cash=False,
                                       plans_taxed=frozenset()).stablecoins_as_cash)
        # A foreign issuer's return of capital is the characterize
        # step's, not the parse's (recommendation 8): no field here.
        self.assertNotIn("foreign_return_of_capital", ParseSettings.names())
        # What the tool and the conformance check build: the country's
        # own basis, whatever the spelling (none: the neutral one).
        for country in ("canada", "CA", "usa", None, " "):
            s = ParseSettings.for_country(country, exercise_fee=1.5)
            b = parse_basis(country)
            self.assertEqual((s.stablecoins_as_cash, s.plans_taxed, s.exercise_fee),
                             (b["stablecoins_as_cash"], b["plans_taxed"], 1.5))
        # A spelling is canonical once; a blank is no country.
        s = ParseSettings(country="CA", **parse_basis("CA"))
        self.assertEqual((s.country, s.stablecoins_as_cash), ("canada", True))
        self.assertEqual(ParseSettings(country="").country, None)
        self.assertEqual(ParseSettings(country=" ").stablecoins_as_cash, False)

    def test_configure_sets_every_field_and_derives_the_deferral(self):
        s = ParseSettings(country="canada", **parse_basis("canada"), account_taxable=True,
                          combined_broker_accounts=True,
                          transfer_costs_checked_downstream=True,
                          futures_settle="next_day", exercise_fee=1.5)
        for spec in registry.SPECS:
            p = registry.parser_class(spec)()
            p.configure(s, account_context="ctx")
            for name in s.names():
                self.assertEqual(getattr(p, name), getattr(s, name), (spec.id, name))
            self.assertEqual(p.account_context, "ctx")
        # IB decides its deferral in its own configure: with a context, wait.
        ib = registry.parser_class("ib")()
        ib.configure(s, account_context="ctx")
        self.assertTrue(ib.defer_ca_messages)
        ib = registry.parser_class("ib")()
        ib.configure(s)                              # no context: nothing to wait for
        self.assertFalse(ib.defer_ca_messages)
        self.assertFalse(hasattr(registry.parser_class("questrade")(), "defer_ca_messages"))

    def test_a_bare_parser_keeps_its_class_defaults(self):
        kr = registry.parser_class("kraken")()
        self.assertTrue(kr.stablecoins_as_cash)
        ib = registry.parser_class("ib")()
        self.assertEqual((ib.futures_settle, ib.defer_ca_messages), ("trade", False))
        self.assertFalse(hasattr(ib, "foreign_return_of_capital"))
        self.assertIsNone(registry.parser_class("webull")().exercise_fee)

    def test_the_crypto_parsers_are_named(self):
        # The registry's flag and the class's own stablecoin model agree.
        for spec in registry.SPECS:
            models = getattr(registry.parser_class(spec), "stablecoins_as_cash", None) is True
            self.assertEqual(spec.crypto, models, spec.id)

    def test_the_object_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ParseSettings().country = "usa"       # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
