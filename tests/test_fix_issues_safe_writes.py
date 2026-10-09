"""Generated state is never written through a symlink (GitHub issue #19).

`work/loss_overrides.json` was written with Path.write_text: a symlink
at that name (to an existing file, or dangling) made every run overwrite
the link's target, a file outside the project. Generated state now goes
through lib/safe_write (a temp file of its own, renamed over the entry:
a link there is replaced, its target never opened).

The sweep: every direct write in the core (`write_text`, `write_bytes`,
`open(..., "w"/"a"/"x")`, `os.fdopen(..., "w")`) is either one of the
reviewed exceptions below (a scratch file in a temporary folder, a lock
file, the safe writer's own temp, an output path the user names) or a
failure here naming the line. All data synthetic.
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "taxjson"


class TestLossOverridesStateLink(unittest.TestCase):
    """#19: work/loss_overrides.json as a symlink."""

    def _project(self, td):
        base = Path(td)
        root = base / "project"
        work = root / "work"
        work.mkdir(parents=True)
        return base, root, work

    def test_link_to_an_existing_outside_file_is_replaced(self):
        from taxjson.bin.taxjson_run import _read_loss_overrides
        with tempfile.TemporaryDirectory() as td:
            base, root, work = self._project(td)
            outside = base / "outside.txt"
            outside.write_text("synthetic content to preserve")
            state = work / "loss_overrides.json"
            state.symlink_to(outside)
            _read_loss_overrides(root, {})
            self.assertEqual(outside.read_text(),
                             "synthetic content to preserve")
            self.assertFalse(state.is_symlink())
            doc = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(doc["overrides"], [])

    def test_link_whose_target_already_holds_the_state_is_replaced(self):
        # The "unchanged, skip the write" shortcut must not keep a link.
        from taxjson.lib import loss_overrides as LO
        with tempfile.TemporaryDirectory() as td:
            base, root, work = self._project(td)
            items = [{"line": "synthetic", "account": "margin"}]
            text = json.dumps({"schema_version": LO.SCHEMA,
                               "overrides": items}, indent=2) + "\n"
            outside = base / "outside.json"
            outside.write_text(text)
            state = work / "loss_overrides.json"
            state.symlink_to(outside)
            LO.write_state(work, items)
            self.assertFalse(state.is_symlink())
            self.assertEqual(state.read_text(encoding="utf-8"), text)
            self.assertEqual(outside.read_text(), text)

    def test_dangling_link_is_replaced_never_followed(self):
        from taxjson.lib import loss_overrides as LO
        with tempfile.TemporaryDirectory() as td:
            base, root, work = self._project(td)
            outside = base / "never_created.json"
            state = work / "loss_overrides.json"
            state.symlink_to(outside)
            LO.write_state(work, [{"line": "synthetic",
                                   "account": "margin"}])
            self.assertFalse(os.path.lexists(str(outside)))
            self.assertFalse(state.is_symlink())
            doc = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(doc["overrides"][0]["line"], "synthetic")


class TestGeneratedStateLinks(unittest.TestCase):
    """The swept writers: each replaces a link at its fixed name."""

    def test_skipped_accounts_state(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cache = base / "project" / "work"
            cache.mkdir(parents=True)
            outside = base / "outside.txt"
            outside.write_text("keep")
            link = cache / R._SKIPPED_ACCOUNTS_FILE
            link.symlink_to(outside)
            R._record_skipped_accounts(cache, ["lira"])
            self.assertEqual(outside.read_text(), "keep")
            self.assertFalse(link.is_symlink())
            self.assertIn("lira", link.read_text(encoding="utf-8"))


class TestFileLock(unittest.TestCase):
    """safe_write.file_lock (the fetch plugin's locks, #13)."""

    def test_second_holder_waits_until_the_first_leaves(self):
        import threading
        from taxjson.lib.safe_write import file_lock
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "state.lock"
            waiting, entered = threading.Event(), threading.Event()

            def second():
                with file_lock(lock, on_wait=waiting.set):
                    entered.set()

            with file_lock(lock):
                t = threading.Thread(target=second)
                t.start()
                self.assertTrue(waiting.wait(30))
                self.assertFalse(entered.is_set())
            t.join(30)
            self.assertTrue(entered.is_set())
            self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600)

    def test_a_symlink_at_the_lock_name_is_replaced_never_followed(self):
        """A link at the lock's name (planted, or left over) is replaced
        by a lock file of its own — its target never created or touched
        — and the lock holds: it used to make the block run unlocked."""
        import threading
        from taxjson.lib.safe_write import file_lock
        with tempfile.TemporaryDirectory() as td:
            outside = Path(td) / "outside"
            target = Path(td) / "target"
            target.write_text("keep")
            for dest in (outside, target):
                lock = Path(td) / f"state-{dest.name}.lock"
                lock.symlink_to(dest)
                waiting, entered = threading.Event(), threading.Event()

                def second():
                    with file_lock(lock, on_wait=waiting.set):
                        entered.set()

                with file_lock(lock):
                    self.assertFalse(lock.is_symlink())
                    t = threading.Thread(target=second)
                    t.start()
                    self.assertTrue(waiting.wait(30))     # locked
                    self.assertFalse(entered.is_set())
                t.join(30)
                self.assertTrue(entered.is_set())
                self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.lexists(str(outside)))
            self.assertEqual(target.read_text(), "keep")

    def test_a_link_that_cannot_be_replaced_is_refused(self):
        from unittest import mock
        from taxjson.lib import safe_write as SW
        with tempfile.TemporaryDirectory() as td:
            lock = Path(td) / "state.lock"
            lock.symlink_to(Path(td) / "outside")
            ran = []
            with mock.patch.object(SW.os, "unlink",
                                   side_effect=PermissionError(13, "no")):
                with self.assertRaises(SW.LockLinkError) as cm:
                    with SW.file_lock(lock):
                        ran.append(1)
            self.assertEqual(ran, [])
            self.assertIn("state.lock is a symlink", str(cm.exception))


# Reviewed direct writes: (file under src/taxjson, the stripped source
# line) -> why it is not generated project state.
_ALLOWED = {
    ("lib/json_cache.py",
     'with open(str(path) + ".lock", "a") as lock:'):
        "the cache's lock file (appends nothing)",
    ("lib/json_cache.py",
     'with os.fdopen(fd, "w", encoding="utf-8") as f:'):
        "its own mkstemp temp file, renamed into place",
    ("bin/taxjson_redact.py",
     'with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:'):
        "redact_file's new copy, os.open O_CREAT|O_EXCL|O_NOFOLLOW",
    ("lib/corp_actions.py",
     "with os.fdopen(fd, 'w', encoding='utf-8') as f:"):
        "Manifest.save's own mkstemp temp file, renamed into place",
    ("bin/taxjson_run.py",
     '_probe.write_text(text, encoding="utf-8")'):
        "scratch copy in a TemporaryDirectory",
    ("bin/taxjson_run.py",
     'tp.write_text(new_text, encoding="utf-8")'):
        "scratch copy in a TemporaryDirectory",
    ("bin/taxjson_run.py",
     'tmp.write_text(res.stdout, encoding="utf-8")'):
        "its own mkstemp scratch file, removed after",
    ("bin/taxjson_run.py",
     'cleanup.write_text(res.stdout, encoding="utf-8")'):
        "its own mkstemp scratch file, removed after",
    ("bin/taxjson_filed.py",
     'src.write_text(json.dumps(combined), encoding="utf-8")'):
        "scratch copy in a TemporaryDirectory",
    ("lib/handoff.py",
     'full.write_text(json.dumps({"transactions": rows}))'):
        "scratch copy in a TemporaryDirectory",
    ("lib/handoff.py",
     'trunc.write_text(json.dumps({"transactions": kept + adjust_rows}))'):
        "scratch copy in a TemporaryDirectory",
    ("lib/migrate.py",
     "tp.write_text(_joined(before, pl.map_append),"):
        "scratch copy in a TemporaryDirectory",
    ("bin/taxjson_generate_parser.py",
     "bad_path.write_text(code, encoding='utf-8')"):
        "the generated parser the user names with --output",
    ("bin/taxjson_generate_parser.py",
     "output_path.write_text(code, encoding='utf-8')"):
        "the generated parser the user names with --output",
}

_WRITE_RE = re.compile(
    r"\.write_text\(|\.write_bytes\("
    r"|\b(?:fd)?open\(.*,\s*['\"][rwax]*[wax][bt+]*['\"]")


class TestNoDirectWritesInCore(unittest.TestCase):
    """#19 sweep: a new direct write fails here until it goes through
    lib/safe_write (write_atomic / atomic_open) or is reviewed above."""

    def test_every_direct_write_is_reviewed(self):
        found = set()
        unexpected = []
        for p in sorted(SRC.rglob("*.py")):
            rel = p.relative_to(SRC).as_posix()
            if rel == "lib/safe_write.py":
                continue
            for n, line in enumerate(
                    p.read_text(encoding="utf-8").splitlines(), 1):
                if not _WRITE_RE.search(line):
                    continue
                key = (rel, line.strip())
                if key in _ALLOWED:
                    found.add(key)
                else:
                    unexpected.append(f"src/taxjson/{rel}:{n}: "
                                      f"{line.strip()}")
        self.assertEqual(unexpected, [], (
            "direct file write(s) in the core — write generated state "
            "with taxjson.lib.safe_write.write_atomic / atomic_open (a "
            "link at the name is replaced, never written through), or "
            "add a reviewed exception to _ALLOWED"))
        self.assertEqual(sorted(set(_ALLOWED) - found), [],
                         "stale _ALLOWED entries: the line is gone")


if __name__ == "__main__":
    unittest.main()
