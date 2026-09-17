"""Unit tests for pure helpers in taxjson-generate-parser.

The end-to-end CLI requires an API key and external service call, so
this file pins the deterministic helpers only: code-fence stripping,
class-name derivation, and sample-CSV reading.
"""
import os
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_generate_parser import (
    _default_class_name,
    _read_sample,
    _strip_code_fences,
)


class TestStripCodeFences(unittest.TestCase):
    def test_no_fences_passthrough(self):
        code = "import csv\n\nclass Foo:\n    pass\n"
        out = _strip_code_fences(code)
        # Trailing newline guaranteed.
        self.assertTrue(out.endswith('\n'))
        self.assertIn('class Foo', out)

    def test_python_fence_stripped(self):
        wrapped = "```python\nimport csv\n```"
        out = _strip_code_fences(wrapped)
        self.assertEqual(out.strip(), 'import csv')
        self.assertFalse('```' in out)

    def test_bare_fence_stripped(self):
        wrapped = "```\nimport csv\n```"
        out = _strip_code_fences(wrapped)
        self.assertEqual(out.strip(), 'import csv')

    def test_py_alias_fence_stripped(self):
        wrapped = "```py\nimport csv\n```"
        out = _strip_code_fences(wrapped)
        self.assertEqual(out.strip(), 'import csv')

    def test_whitespace_around_fences_tolerated(self):
        wrapped = "\n\n```python\nimport csv\n```\n\n"
        out = _strip_code_fences(wrapped)
        self.assertEqual(out.strip(), 'import csv')


class TestDefaultClassName(unittest.TestCase):
    def test_single_word(self):
        self.assertEqual(_default_class_name('Schwab'), 'SchwabBrokerage')

    def test_multiword_camelcased(self):
        self.assertEqual(
            _default_class_name('Charles Schwab'),
            'CharlesSchwabBrokerage',
        )

    def test_punctuation_stripped(self):
        self.assertEqual(
            _default_class_name('TD Direct (Canada)'),
            'TdDirectCanadaBrokerage',
        )

    def test_digits_preserved(self):
        self.assertEqual(_default_class_name('IB 2024'), 'Ib2024Brokerage')

    def test_empty_returns_default(self):
        self.assertEqual(_default_class_name(''), 'NewBrokerage')
        self.assertEqual(_default_class_name('---'), 'NewBrokerage')


class TestReadSample(unittest.TestCase):
    def _write(self, content):
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_reads_up_to_n_lines(self):
        path = self._write("a\nb\nc\nd\ne\n")
        try:
            out = _read_sample(path, n=3)
            self.assertEqual(out, "a\nb\nc")
        finally:
            os.remove(path)

    def test_short_file_returns_all_lines(self):
        path = self._write("a\nb\n")
        try:
            out = _read_sample(path, n=10)
            self.assertEqual(out, "a\nb")
        finally:
            os.remove(path)

    def test_empty_file_returns_empty(self):
        path = self._write("")
        try:
            self.assertEqual(_read_sample(path, n=10), "")
        finally:
            os.remove(path)


if __name__ == '__main__':
    unittest.main()
