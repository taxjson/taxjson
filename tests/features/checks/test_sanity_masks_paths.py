"""Re-audit-2 fixes: id masking in diagnostics (fix list privacy-02).

A2-0756 / A2-0757 / A2-1381 (every Kraken refid / txid in a note, an
error or a skip summary is masked to its first 2 characters + ***),
A2-0461 (the file-name account-id mask is pinned at the IB and generic
broker diagnostics). Synthetic data only.

Formerly: test_fix_a2_privacy_mask
"""
import os
import tempfile
import unittest


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


# ------------------------------------------------- A2-1380 / A2-1392
class TestSanityMasksPaths(unittest.TestCase):
    """A2-1380: sanity --json and the duplicate-file note/error name a
    holdings file masked, like the text listing. A2-1392: a symlink
    loop given as a file is one clean line, not a traceback.

    Formerly: test_fix_a2_privacy_mask.TestSanityMasksPaths"""

    _HOLD = ('[[holding]]\nsymbol = "XEI.TO"\nquantity = 100\n')

    def _proj(self, tmp):
        from _support.tt_holdings_projects import _held_project
        root = _held_project(tmp, "")
        f = root / "hold" / "U5550001_holdings.toml"  # pii-ok
        f.write_text(self._HOLD)
        return root, str(f)

    def test_json_paths_are_masked(self):
        import json
        from _support.qt_margin_views import _run_cli
        with tempfile.TemporaryDirectory() as tmp:
            root, f = self._proj(tmp)
            r = _run_cli(root, "sanity", "margin", f, "--json")
            self.assertNotIn("5550001", r.stdout + r.stderr)
            j = json.loads(r.stdout)
            self.assertTrue(j["files"][0]["file"].endswith(
                "U5***_holdings.toml"), j["files"][0]["file"])

    def test_file_given_twice_note_is_masked(self):
        from _support.qt_margin_views import _run_cli
        with tempfile.TemporaryDirectory() as tmp:
            root, f = self._proj(tmp)
            r = _run_cli(root, "sanity", "margin", f, f)
            self.assertIn("U5***_holdings.toml", r.stderr)
            self.assertNotIn("5550001", r.stdout + r.stderr)
            r = _run_cli(root, "sanity", f"margin={f}", "tfsa", f)
            self.assertIn("U5***_holdings.toml", r.stderr + r.stdout)
            self.assertNotIn("5550001", r.stdout + r.stderr)

    def test_symlink_loop_is_one_line(self):
        import os
        from _support.qt_margin_views import _run_cli
        with tempfile.TemporaryDirectory() as tmp:
            root, f = self._proj(tmp)
            l1, l2 = root / "hold" / "l1.toml", root / "hold" / "l2.toml"
            os.symlink(l2, l1)
            os.symlink(l1, l2)
            for args in (("margin", str(l1)), (f"margin={l1}",)):
                with self.subTest(args=args):
                    r = _run_cli(root, "sanity", *args)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertIn("l1.toml", r.stderr)


if __name__ == "__main__":
    unittest.main()
