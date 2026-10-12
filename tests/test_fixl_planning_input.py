"""Planning low round: the shared JSON input contract for bin tools
(lib/json_input). All data is synthetic."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from taxjson.lib.json_input import InputFileError, read_json_doc  # noqa: E402


class TestReadJsonDoc(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def _f(self, name, content):
        p = Path(self.d.name) / name
        p.write_bytes(content if isinstance(content, bytes)
                      else content.encode())
        return p

    def test_bad_inputs_name_the_file(self):
        for name, content in [("trunc.json", '{"transactions": ['),
                              ("latin1.json", b'{"a": "caf\xe9"}'),
                              ("int.json", "5"),
                              ("shape.json", '{"transactions": 5}')]:
            p = self._f(name, content)
            with self.assertRaises(InputFileError) as cm:
                read_json_doc(p)
            self.assertIn(name, str(cm.exception))
            self.assertNotIn("\n", str(cm.exception))
        with self.assertRaises(InputFileError):
            read_json_doc(Path(self.d.name) / "missing.json")

    def test_bare_array_is_a_book(self):
        p = self._f("bare.json", '[{"date": "2025-01-02"}]  # note')
        self.assertEqual(read_json_doc(p),
                         {"transactions": [{"date": "2025-01-02"}]})
        with self.assertRaises(InputFileError):
            read_json_doc(p, list_key=None)

    def test_require_key(self):
        p = self._f("g.json", '{"transactions": []}')
        with self.assertRaises(InputFileError) as cm:
            read_json_doc(p, require_key="inventory")
        self.assertIn("inventory", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
