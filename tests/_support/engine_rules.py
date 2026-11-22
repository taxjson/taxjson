"""Shared test helpers: the rule each engine decision record names.

`compute_gains` returns `rules` (PLAN step 11; engine/rows `RuleTable`):
{decided row id: {record id: rule id}}, each record keyed by its own row's
id (a Canadian loss's DISALLOW is the sale's, an addition its ADJUST's
`WASH_<loss>__<trg>`; a US wash record the sale's or the cover's, a
replacement the replacement row's; a filing position's record, a
replacement closed before the loss, a US retest beside the row's own
disallowance and an in-kind contribution's sale under engine/rows
`override_record`, `closed_record`, `retest_record` and `in_kind_record`).
A test asserts a record's rule by its constant
(`assert_rule(self, res, "s1", R.CA_SL_09)`), and a characterisation's by
what its callback heard (`assert_noted`). A rule outside
tax_logic.ENGINE_RULES is refused, the asserted one and every one the
table holds, so the set check 12 reads stays the rules the engine names;
tests/test_rule_ids.py checks a test asserts each of them so.

Shared from: PLAN step 11's engine-rule tests
"""
from taxjson.lib.tax_logic import ENGINE_RULES


def rules_of(results, row_id):
    """{record id: rule id} of `row_id` in `results` ({} when none), every
    rule the table holds checked against ENGINE_RULES first."""
    table = results.get("rules") or {}
    stray = sorted({r for recs in table.values() for r in recs.values()} - ENGINE_RULES)
    if stray:
        raise AssertionError(f"the engine names rules outside tax_logic.ENGINE_RULES: {stray}")
    return dict(table.get(row_id) or {})


def assert_noted(tc, heard, rule, row=None):
    """A characterisation's `note_rule` callback heard `rule` (`heard`: the
    (row, rule id) pairs it was called with): for some row, or for `row`
    (the very dict)."""
    if rule not in ENGINE_RULES:
        raise AssertionError(f"{rule} is not an engine rule (tax_logic.ENGINE_RULES): "
                             f"no record names it")
    tc.assertTrue(any(rid == rule and (row is None or r is row) for r, rid in heard),
                  f"{rule} not heard: {[rid for _, rid in heard]}")


def assert_rule(tc, results, row_id, rule, record=None):
    """`row_id`'s records name `rule`: one of them, or the record `record`."""
    if rule not in ENGINE_RULES:
        raise AssertionError(f"{rule} is not an engine rule (tax_logic.ENGINE_RULES): "
                             f"no record names it")
    got = rules_of(results, row_id)
    if record is None:
        tc.assertIn(rule, got.values(), f"{row_id}: {got}")
    else:
        tc.assertEqual(got.get(record), rule, f"{row_id} {record}: {got}")
