"""The radar's SIMILAR statuses when a listing is booked in lower case.

A ticker.map EXTRACT line's symbol is booked as typed (`EXTRACT old
sample holdings | CAD | zyab.TO` books `zyab.TO`), while the SIMILAR
groups are upper case. `RadarReport.status` keys each row by its ticker
upper-cased and skips a row with no status, so a lower-case listing
reads its group, and a closed lower-case twin that `--all` lists without
a status never blanks the status of the listing it spells.

Synthetic data only (ZY tickers, made-up prices).
"""
import json
import unittest

from tax_rules import rule
from taxjson.rules import ids as R
from _support.planning_radar import _radar_main, _row

_GROUPS = ('--similar', 'ZYAB.TO ZYCD.TO', '--similar', 'ZYEF.TO ZYGH.TO')


def _run(taxable, *extra):
    return _radar_main('canada', taxable, None, '2024-03-15',
                       ('--all', *_GROUPS, *extra))


def _doc(taxable):
    doc = json.loads(_run(taxable, '--json'))
    rows = {r['ticker']: r['category'] for s in doc['sections']
            for r in s.get('rows') or []}
    return doc, rows


class TestLowerCaseListingSimilarStatus(unittest.TestCase):

    @rule(R.CA_SL_20)
    def test_a_closed_lower_case_twin_keeps_the_status(self):
        """ZYAB.TO is held (CLEAR); zyab.TO was bought and sold in 2023,
        so `--all` lists it with no status. The group still reads
        ZYAB.TO CLEAR: the row with no status is skipped, not written
        over the CLEAR one."""
        books = [_row('2023-06-01', 'zyab.TO', 5, 10.0),
                 _row('2023-08-01', 'zyab.TO', -5, 12.0),
                 _row('2024-01-10', 'ZYAB.TO', 10, 10.0)]
        doc, rows = _doc(books)
        self.assertEqual(rows, {'ZYAB.TO': 'CLEAR', 'zyab.TO': ''})
        self.assertEqual(doc['similar'], [
            {'members': ['ZYAB.TO', 'ZYCD.TO'],
             'status': {'ZYAB.TO': 'CLEAR'}}])
        self.assertIn('ZYAB.TO CLEAR, ZYCD.TO —', _run(books))

    @rule(R.CA_SL_20)
    def test_a_lower_case_listing_reads_its_group(self):
        """zyef.TO is held (CLEAR): the group SIMILAR ZYEF.TO ZYGH.TO
        reads its status under the upper-case spelling."""
        books = [_row('2024-01-10', 'zyef.TO', 20, 10.0)]
        doc, rows = _doc(books)
        self.assertEqual(rows, {'zyef.TO': 'CLEAR'})
        self.assertEqual(doc['similar'], [
            {'members': ['ZYEF.TO', 'ZYGH.TO'],
             'status': {'ZYEF.TO': 'CLEAR'}}])
        self.assertIn('ZYEF.TO CLEAR, ZYGH.TO —', _run(books))


if __name__ == '__main__':
    unittest.main()
