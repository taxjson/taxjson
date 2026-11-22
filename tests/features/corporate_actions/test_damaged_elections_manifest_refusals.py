"""Re-audit-2 fixes: one-line errors and consistent exit codes
(errors-01 / errors-03 / errors-04 lists, errA area).

Synthetic data only.

Formerly: test_fix_a2_errA
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestManifest(unittest.TestCase):
    """A2-0160 / A2-0463 / A2-1402 / A2-0804 / A2-1399 / A2-1401 /
    A2-1449: the elections manifest is read one way, in one line.

    Formerly: test_fix_a2_errA.TestManifest"""

    def _load(self, p):
        from taxjson.lib.corp_actions import Manifest
        return Manifest.load(p)

    def test_directory_and_loop_are_manifest_errors(self):
        from taxjson.lib.corp_actions import ManifestError
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "manifest.json"
            d.mkdir()
            with self.assertRaisesRegex(ManifestError, "cannot be read"):
                self._load(d)
            loop = Path(td) / "loop.json"
            loop.symlink_to("loop.json")
            with self.assertRaisesRegex(ManifestError, "cannot be read"):
                self._load(loop)
            dangling = Path(td) / "gone.json"
            dangling.symlink_to("elsewhere.json")
            with self.assertRaises(ManifestError):
                self._load(dangling)
            self.assertEqual(self._load(Path(td) / "absent.json").records,
                             {})

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root reads a chmod-000 file")
    def test_unreadable_is_a_manifest_error(self):
        from taxjson.lib.corp_actions import ManifestError
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            p.write_text('{"elections": {}}')
            p.chmod(0)
            try:
                with self.assertRaises(ManifestError):
                    self._load(p)
            finally:
                p.chmod(0o600)

    def test_bom_is_read(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            p.write_bytes(b'\xef\xbb\xbf{"elections": {"e1": {"election": '
                          b'"ignore"}}}')
            self.assertEqual(self._load(p).records["e1"].election, "ignore")

    def test_non_string_summary_is_refused(self):
        from taxjson.lib.corp_actions import ManifestError
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            p.write_text(json.dumps({"elections": {"e1": {
                "election": "ignore", "summary": 7}}}))
            with self.assertRaisesRegex(ManifestError, "'summary'"):
                self._load(p)
            p.write_text(json.dumps({"elections": {"e1": {
                "election": "ignore", "summary": None, "notes": None}}}))
            rec = self._load(p).records["e1"]
            self.assertEqual((rec.summary, rec.notes), ("", ""))

    def test_spinoffs_view_refuses_in_one_line(self):
        from taxjson.lib.corp_views import ViewError, _manifest
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "inputs" / "margin" / "manifest.json"
            p.parent.mkdir(parents=True)
            for doc in ([], {"elections": {"e1": "rollover"}},
                        {"elections": {"e1": {"election": "x",
                                              "summary": 7}}}):
                p.write_text(json.dumps(doc))
                with self.assertRaises(ViewError):
                    _manifest(Path(td), "margin")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root writes a read-only folder")
    def test_save_to_a_read_only_folder_raises_oserror(self):
        # An OSError, which the commands report as one 'cannot write'
        # line with exit 2 (A2-1418); the old manifest is untouched.
        from taxjson.lib.corp_actions import Manifest
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "ro"
            d.mkdir()
            d.chmod(0o500)
            try:
                with self.assertRaises(OSError):
                    Manifest({}).save(d / "manifest.json")
            finally:
                d.chmod(0o700)

    def test_resolve_manifest_refuses_a_looping_link(self):
        # Never written through (A2-0160 ELOOP traceback, A2-1400).
        from taxjson.pipeline.stages import _resolve_manifest
        with tempfile.TemporaryDirectory() as td:
            acct = Path(td) / "inputs" / "margin"
            acct.mkdir(parents=True)
            (acct / "manifest.json").symlink_to("manifest.json")
            with contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                _resolve_manifest(acct, Path(td) / "work", "margin",
                                  create=True)
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
