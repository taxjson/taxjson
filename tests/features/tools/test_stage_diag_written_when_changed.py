"""A stage's captured stderr (work/<out>.diag, pipeline/tools
`run_to_file`) is written only when it changed (backlog TJ-4962): a
re-run that captured the same text keeps the file as it is (no temp, no
fsync, no rename), as every other stamp of the run; new text replaces it,
no text removes it, and a symlink at its name is replaced, never written
through. The stage's output itself is still written on every run.

All data is synthetic.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from _tmpfiles import private_dir


def _stage(text: str) -> list:
    """A child that prints `{}` and writes `text` to its stderr."""
    return [sys.executable, "-I", "-c",
            f"import sys; print('{{}}'); sys.stderr.write({text!r})"]


def _ident(p: Path) -> tuple:
    st = os.lstat(p)
    return (st.st_ino, st.st_mtime_ns)


class TestStageDiagWrittenWhenChanged(unittest.TestCase):

    def setUp(self):
        self.work = Path(private_dir())
        self.out = self.work / "acct_broker.json"
        self.diag = self.work / "acct_broker.json.diag"

    def _run(self, text: str) -> int:
        """run_to_file with a stage that says `text`; the fsyncs it made."""
        from taxjson.pipeline.tools import run_to_file
        with mock.patch.object(os, "fsync", wraps=os.fsync) as sync:
            run_to_file(_stage(text), self.out)
        return sync.call_count

    def test_same_text_keeps_the_diag(self):
        said = "  acct.csv: 3 tax objects\nnote: synthetic line\n"
        self.assertEqual(self._run(said), 2)        # the output, the .diag
        diag, out = _ident(self.diag), _ident(self.out)
        self.assertEqual(self._run(said), 1)        # the output alone
        self.assertEqual(_ident(self.diag), diag)
        self.assertEqual(self.diag.read_text(encoding="utf-8"), said)
        self.assertNotEqual(_ident(self.out), out)  # rebuilt every run
        self.assertEqual(self.out.read_text(encoding="utf-8"), "{}\n")

    def test_new_text_replaces_the_diag(self):
        self._run("  acct.csv: 3 tax objects\n")
        before = _ident(self.diag)
        self.assertEqual(self._run("  acct.csv: 4 tax objects\n"), 2)
        self.assertNotEqual(_ident(self.diag), before)
        self.assertEqual(self.diag.read_text(encoding="utf-8"),
                         "  acct.csv: 4 tax objects\n")

    def test_no_text_removes_the_diag(self):
        self._run("  acct.csv: 3 tax objects\n")
        self.assertEqual(self._run(""), 1)
        self.assertFalse(self.diag.exists())

    def test_link_at_its_name_is_replaced_not_followed(self):
        said = "  acct.csv: 3 tax objects\n"
        target = self.work / "elsewhere.txt"
        target.write_text(said, encoding="utf-8")
        held = _ident(target)
        self.diag.symlink_to(target)
        self.assertEqual(self._run(said), 2)
        self.assertFalse(self.diag.is_symlink())
        self.assertEqual(self.diag.read_text(encoding="utf-8"), said)
        self.assertEqual(_ident(target), held)

    def test_holds(self):
        from taxjson.lib.safe_write import holds
        p = self.work / "stamp.txt"
        self.assertFalse(holds(p, "x\n"))                    # missing
        p.write_bytes("é\n".encode("utf-8"))
        self.assertTrue(holds(p, "é\n"))
        self.assertTrue(holds(p, "é\n".encode("utf-8")))
        self.assertFalse(holds(p, "é"))
        self.assertFalse(holds(p, "é\n", encoding="latin-1"))
        link = self.work / "link.txt"
        link.symlink_to(p)
        self.assertFalse(holds(link, "é\n"))                 # a link: never


if __name__ == "__main__":
    unittest.main()
