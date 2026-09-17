"""RuleSpec.auto_default + the name_change rule: events with exactly one
sane treatment are elected without prompting, with a full audit-trail
manifest record that `taxjson elect --redo` can override."""

import unittest

from taxjson.lib.corp_actions import (
    CorporateAction,
    ElectionRecord,
    Manifest,
    RULES_BY_COUNTRY,
    apply_auto_defaults,
    resolve_event,
)


def _name_change(source='FB.US', target='META.US'):
    return CorporateAction(
        date='2025-06-09', time='20:25:00', action_type='name_change',
        source_symbol=source, source_isin='US30303M1027',
        target_symbol=target, target_isin='US30303M1027',
        ratio_new=1, ratio_old=1,
        qty_disposed=0.0, qty_received=0.0,
        fmv=0.0, currency='USD', target_currency='USD',
        account='Margin',
    )


def _merger():
    return CorporateAction(
        date='2025-10-22', time='20:25:00', action_type='merger',
        source_symbol='SSL.TO', source_isin='CA0000000001',
        target_symbol='RGLD.US', target_isin='US0000000002',
        ratio_new=1, ratio_old=16,
        qty_disposed=1600.0, qty_received=100.0,
        fmv=25920.67, currency='CAD', target_currency='USD',
        account='Margin',
    )


class TestNameChangeRule(unittest.TestCase):
    def test_registered_for_both_countries_with_auto_default(self):
        for country in ('canada', 'usa'):
            rule = RULES_BY_COUNTRY[country]['name_change']
            self.assertEqual(rule.auto_default, 'rename')
            self.assertEqual([k for k, _ in rule.options], ['rename'])

    def test_rename_emits_ratio1_split(self):
        rows = resolve_event(_name_change(), 'rename', 'canada', {})
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r['action'], 'SPLIT')
        self.assertEqual(r['quantity'], 1.0)
        self.assertEqual(r['symbol'], 'FB.US')
        self.assertEqual(r['symbol_new'], 'META.US')


class TestApplyAutoDefaults(unittest.TestCase):
    def test_name_change_auto_elected_with_audit_record(self):
        man = Manifest()
        ev = _name_change()
        applied = apply_auto_defaults([ev, _merger()], man, 'canada')
        self.assertEqual([a.event_id for a in applied], [ev.event_id])
        rec = man.get(ev.event_id)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.election, 'rename')
        self.assertIn('auto-elected', rec.notes)
        # The merger still needs a human decision.
        self.assertIsNone(man.get(_merger().event_id))

    def test_existing_election_never_overwritten(self):
        man = Manifest()
        ev = _name_change()
        man.set(ElectionRecord(event_id=ev.event_id, summary=ev.summary(),
                               election='ignore', notes='user said skip'))
        applied = apply_auto_defaults([ev], man, 'canada')
        self.assertEqual(applied, [])
        self.assertEqual(man.get(ev.event_id).election, 'ignore')


if __name__ == '__main__':
    unittest.main()
