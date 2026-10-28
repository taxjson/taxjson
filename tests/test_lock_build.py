"""A filed-year lock records the taxjson that computed it, and a drift
names it (backlog TJ-0341): `close-year` writes `taxjson` (version,
commit, release, modified) to filed/<year>.json; `check-filed` says
whether the lock was closed with this taxjson or another.

Every fixture is SYNTHETIC: one made-up account, amounts under 1,000.
"""
import json
import tempfile
import unittest
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from unittest import mock

from taxjson.bin import taxjson_filed as F
from taxjson.bin.taxjson_run import _closed_with
from taxjson.lib import channels as CH

OLD = {"version": "0.35.0", "commit": None, "release": None,
       "modified": None}


def _installed():
    """The installed version; None when taxjson runs from a source tree
    with no package metadata (PYTHONPATH=src), as `tjs --version` reads
    it."""
    try:
        return version("taxjson")
    except PackageNotFoundError:
        return None


class TestRunningBuild(unittest.TestCase):

    def test_the_installed_version_and_a_checkout_commit(self):
        b = CH.running_build()
        self.assertEqual(b["version"], _installed())
        self.assertEqual(set(b), {"version", "commit", "release",
                                  "modified"})
        if CH._package_checkout() is not None and b["commit"]:
            self.assertRegex(b["commit"], r"^[0-9a-f]{4,}$")
            self.assertIn(b["modified"], (True, False))

    def test_a_wheel_install_has_no_commit(self):
        with mock.patch.object(CH, "_package_checkout", return_value=None):
            b = CH.running_build()
        self.assertEqual((b["commit"], b["release"], b["modified"]),
                         (None, None, None))

    def test_labels(self):
        self.assertEqual(CH.build_label(OLD), "taxjson 0.35.0")
        self.assertEqual(CH.build_label(
            {"version": "0.36.1", "commit": "81c512b", "release": None,
             "modified": True}), "taxjson 0.36.1 (commit 81c512b, modified)")
        # At a release tag the version says it all.
        self.assertEqual(CH.build_label(
            {"version": "0.36.1", "commit": "81c512b", "release": "v0.36.1",
             "modified": False}), "taxjson 0.36.1")
        for bad in (None, "0.36.1", {"version": 36}, {}):
            self.assertIsNone(CH.build_label(bad))

    def test_same_build(self):
        a = {"version": "0.36.1", "commit": "81c512b", "modified": False}
        self.assertTrue(CH.same_build(a, dict(a)))
        self.assertFalse(CH.same_build(a, dict(a, commit="aed33c6")))
        self.assertFalse(CH.same_build(a, dict(a, modified=True)))
        self.assertTrue(CH.same_build({"version": "0.36.1"},
                                      {"version": "0.36.1"}))
        self.assertFalse(CH.same_build(OLD, {"version": "0.36.1"}))
        self.assertIsNone(CH.same_build(None, a))


class TestTheLockRecordsIt(unittest.TestCase):

    def test_close_year_writes_the_build(self):
        acct = {"margin": {"realized": 120.0, "disallowed": 0.0,
                           "dispositions": 1, "income": 0.0,
                           "dividend": 0.0, "pil": 0.0, "tainted": 0,
                           "proceeds": 620.0, "st_gain": 0.0,
                           "lt_gain": 0.0}}
        with tempfile.TemporaryDirectory() as td:
            p = F.write_snapshot(Path(td), 2025, "canada", "wash-adjusted",
                                 acct, force=False)
            doc = json.loads(p.read_text())
        self.assertEqual(doc["taxjson"], CH.running_build())
        # Still a lock the shape check accepts.
        self.assertIsNone(F.lock_shape_problem(doc, 2025))


class TestTheDriftNamesIt(unittest.TestCase):

    def test_closed_with_another_taxjson(self):
        (line,) = _closed_with({"taxjson": OLD})
        self.assertTrue(line.startswith(
            "It was closed with taxjson 0.35.0; this is "), line)
        self.assertIn("docs/upgrading.md", line)

    def test_closed_with_this_taxjson(self):
        if _installed() is None:
            self.skipTest("no package metadata: a lock records no version")
        (line,) = _closed_with({"taxjson": CH.running_build()})
        if CH.running_build().get("modified"):
            # A modified checkout is never "the same code".
            self.assertIn("this is", line)
        else:
            self.assertIn("not the code", line)

    def test_a_lock_from_before_says_nothing(self):
        self.assertEqual(_closed_with({}), [])
        self.assertEqual(_closed_with({"taxjson": "0.36.1"}), [])


if __name__ == "__main__":
    unittest.main()
