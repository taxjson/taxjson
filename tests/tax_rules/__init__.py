"""Test markers that tie a test to the tax-logic statement it pins.

tax-logic (src/taxjson/lib/tax_logic.py) is the spec: every statement has
a stable rule id (CA-... for Canada, US-... for the United States).
A test says which statements it pins:

    from tax_rules import rule, rule_absent

    @rule("CA-SL-02")                        # Canada: still-held test applies
    def test_rebuy_sold_before_day_30_allows(self): ...

    @rule("CA-SL-02", "CA-SL-01")            # several ids of ONE country
    def test_...(self): ...

    @rule("CA-SL-02")                        # a partition (dual-country) test:
    @rule_absent("CA-SL-02", country="usa")  # the SAME book under the US
    @rule("US-WASH-06")                      # does not apply it, and the US
    def test_...(self): ...                  # rule applies instead

Both markers work on a test method or on a TestCase class (every test_*
method of the class is marked).

Runtime guard: while a marked test runs, TAXJSON_TEST_ENGINE_COUNTRY is
set to the only country its markers name, and the gains engines
(lib/country.check_engine_allowed) raise AssertionError if the other
country's engine runs — in process or in a `taxjson` subprocess that
inherits the environment. A test whose markers name both countries
(a @rule_absent, or @rule ids of both countries) may run both engines.

scripts/check_tax_rules.py reads these markers with the AST (it never
imports the tests) and fails CI on unknown ids, mixed ids in one @rule,
a Canada-tagged test that names the US engine (or the reverse), and
rules with no test that are not listed in baseline-unpinned.txt.

The dual-country harness lives in tax_rules/dual.py.
"""
from __future__ import annotations

import functools
import inspect
import os
from typing import Callable, List, Tuple

from taxjson.lib.country import (COUNTRIES, ENGINE_GUARD_ENV,
                                 canonical_country)
from taxjson.lib.tax_logic import rule_country

__all__ = ["rule", "rule_absent", "markers", "allowed_countries"]

Marker = Tuple[str, str, str]      # (kind "rule"|"absent", rule id, country)
_ATTR = "__tax_rules__"


def markers(obj) -> List[Marker]:
    """The markers on a test function or class ([] when none)."""
    return list(getattr(obj, _ATTR, []) or [])


def allowed_countries(marks: List[Marker]) -> set:
    return {c for _k, _i, c in marks}


def _guarded(fn: Callable, marks: List[Marker]) -> Callable:
    """Wrap `fn` once; later markers append to the same list, so the
    guard always sees every marker of the test."""
    if hasattr(fn, _ATTR) and getattr(fn, "__tax_rules_wrapped__", False):
        getattr(fn, _ATTR).extend(marks)
        return fn
    shared: List[Marker] = list(marks)

    @functools.wraps(fn)
    def run(*a, **k):
        allowed = allowed_countries(shared)
        prev = os.environ.get(ENGINE_GUARD_ENV)
        if len(allowed) == 1:
            os.environ[ENGINE_GUARD_ENV] = next(iter(allowed))
        else:
            os.environ.pop(ENGINE_GUARD_ENV, None)
        try:
            return fn(*a, **k)
        finally:
            if prev is None:
                os.environ.pop(ENGINE_GUARD_ENV, None)
            else:
                os.environ[ENGINE_GUARD_ENV] = prev

    setattr(run, _ATTR, shared)
    run.__tax_rules_wrapped__ = True
    return run


def _apply(target, marks: List[Marker]):
    if inspect.isclass(target):
        setattr(target, _ATTR, markers(target) + marks)
        for name, member in list(vars(target).items()):
            if name.startswith("test") and callable(member):
                setattr(target, name, _guarded(member, marks))
        return target
    return _guarded(target, marks)


def rule(*ids: str):
    """This test pins these tax-logic statements (all of ONE country)."""
    if not ids:
        raise ValueError("@rule needs at least one rule id")
    countries = {rule_country(i) for i in ids}
    if len(countries) != 1:
        raise ValueError(f"@rule{ids}: ids of both countries in one "
                         f"marker — use one @rule per country")
    c = countries.pop()
    marks = [("rule", i, c) for i in ids]
    return lambda target: _apply(target, marks)


def rule_absent(rule_id: str, *, country: str):
    """A partition test: the same synthetic book run under `country`
    (the OTHER country) must NOT apply `rule_id`. The only marker that
    lets a single-country test run the other country's engine."""
    c = canonical_country(country, what="rule_absent country")
    own = rule_country(rule_id)
    if c == own:
        raise ValueError(f"@rule_absent({rule_id!r}, country={country!r}): "
                         f"the rule is {own}'s own — name the other country")
    assert c in COUNTRIES
    marks = [("absent", rule_id, c), ("rule-owner", rule_id, own)]
    return lambda target: _apply(target, marks)
