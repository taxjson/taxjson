"""Regression tests for the 2026-08 audit fixes.

One class per finding; each test pins the exact divergence the audit
reproduced, so a reintroduction fails with the original wrong number.

Formerly: test_audit_2026_08_fixes
"""
import os
import tempfile
import unittest
from pathlib import Path


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


class TestCrossReportsWashBasis(unittest.TestCase):
    """Audit 2026-08 #15: reports/ccd.rpt and leaps.rpt were built from
    pre-wash gains while their query twins (ccd-sum/leaps-sum) resolve
    the wash-adjusted files — `_wash_preferred_gains` now picks the
    same basis, freshness-guarded.

    Formerly: test_audit_2026_08_fixes.TestCrossReportsWashBasis"""

    def test_selection(self):
        import os as _os
        from taxjson.pipeline.stages import _wash_preferred_gains
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "margin_gains.json"
            wash = Path(tmp) / "margin_gains_wash.json"
            plain.write_text("{}")
            # No wash twin → plain.
            self.assertEqual(_wash_preferred_gains(plain), plain)
            # Fresh wash twin → wash.
            wash.write_text("{}")
            _os.utime(plain, (1000, 1000))
            _os.utime(wash, (2000, 2000))
            self.assertEqual(_wash_preferred_gains(plain), wash)
            # STALE wash twin (older run) must not shadow fresh gains.
            _os.utime(wash, (500, 500))
            self.assertEqual(_wash_preferred_gains(plain), plain)


if __name__ == '__main__':
    unittest.main()
