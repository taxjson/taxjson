"""2026-10 workflow audit: the fixes the audit's confirmed findings asked
for (CHANGELOG "Fixed"): interest paid shown apart, a negative futures
price in a generic CSV, the audit over a pending election, the
checklist's hints, init's timezone, the single-folder year hints,
sanity with no holdings file, the headless election message, no empty
elections.json, compare's exit codes (tests/test_compare.py).

Formerly: test_workflow_audit_fixes"""
import os
import unittest
from unittest import mock


class TestSystemTimezone(unittest.TestCase):
    """Formerly: test_workflow_audit_fixes.TestSystemTimezone"""
    def test_a_real_localtime_link_wins_over_a_utc_timezone_file(self):
        from taxjson.lib import timezones as TZ
        env = {k: v for k, v in os.environ.items() if k != "TZ"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("builtins.open",
                           mock.mock_open(read_data="Etc/UTC\n")), \
                mock.patch("os.readlink",
                           return_value="/usr/share/zoneinfo/America/Toronto"):
            self.assertEqual(TZ.system_timezone(), "America/Toronto")
        with mock.patch.dict(os.environ, dict(env, TZ="UTC"), clear=True), \
                mock.patch("os.readlink",
                           return_value="/usr/share/zoneinfo/America/Toronto"):
            self.assertIsNone(TZ.system_timezone())


if __name__ == "__main__":
    unittest.main()
