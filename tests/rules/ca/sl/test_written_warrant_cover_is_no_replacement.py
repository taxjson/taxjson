"""Regression tests for fix round 7 (wave C), group engine: the engines
(lib/core.py, lib/corp_actions.py, lib/futures.py).

TJ-0947 an option held at an unknown cost (an OPENING cost=unknown),
        exercised: its shares were booked at the strike alone, a
        definite gain; their cost is unknown too.
TJ-0948 a taxable spin-off's fraction paid in cash was left out of the
        dividend, and the broker's $0 fraction diluted the new shares'
        cost.
TJ-0949 two broker accounts' exports in one folder: a short future
        opened in one closed the long held in the other.
TJ-0951 a US §355 spin-off allocated basis from lots bought after its
        record date.
TJ-0952 buying back a warrant written short was named as a
        superficial-loss replacement.
TJ-0953 a US short sale covered at a loss in December and delivered in
        January was the earlier year's loss.
TJ-0954 a warrant exercise with no shares per warrant stated, delivered
        in several rows: the first row took all the warrants' cost.
TJ-0956 a US return of capital beyond many small lots' basis: each
        lot's sub-cent excess was left as negative basis.

Every fixture is synthetic: invented tickers (SYN*), account labels
and amounts.

Formerly: test_fix_1029_r7c_engine
"""
import contextlib
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx
from taxjson.rules import ids as R


def _gains(book, country, year=2025):
    """pipeline.run_gains under one country (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()), \
            contextlib.redirect_stdout(io.StringIO()):
        return run_gains(list(book), [], [], req=GainsRequest(
            country=country, taxable=True, year=year,
            phantom_hint=False))


# ---------------------------------------------------------------- TJ-0952
class TestWarrantShortCoverIsNoAcquisition(unittest.TestCase):
    """SYN.TO bought for 1000 and sold 2025-06-02 for 900 (a 100 loss).
    A warrant SYN.WT.TO written short in January and bought back June 5
    acquires no right: no right_vs_share_loss flag. A warrant bought to
    open on June 5 is still named.

    Formerly: test_fix_1029_r7c_engine.TestWarrantShortCoverIsNoAcquisition"""

    def _flags(self, short_first):
        book = [tx('BUYSELL', '2025-01-06', 'SYN.TO', 100, 1000,
                   currency='CAD'),
                tx('BUYSELL', '2025-06-02', 'SYN.TO', -100, 900,
                   currency='CAD')]
        if short_first:
            book.append(tx('BUYSELL', '2025-01-10', 'SYN.WT.TO', -1, 2,
                           currency='CAD'))
        book.append(tx('BUYSELL', '2025-06-05', 'SYN.WT.TO', 1, 1,
                       currency='CAD'))
        res = _gains(book, 'canada')
        return [w for w in res.get('option_replacement_warnings') or []
                if w['rule'] == 'right_vs_share_loss']

    @rule(R.CA_SL_14)
    def test_cover_of_a_written_warrant_is_not_flagged(self):
        self.assertEqual(self._flags(True), [])

    @rule(R.CA_SL_14)
    def test_warrant_bought_to_open_is_still_flagged(self):
        flags = self._flags(False)
        self.assertEqual([(w['option_symbol'], w['option_qty'])
                          for w in flags], [('SYN.WT.TO', 1.0)])


if __name__ == '__main__':
    unittest.main()
