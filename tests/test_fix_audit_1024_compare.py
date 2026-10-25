"""Audit 2026-10-24, group "compare": `taxjson compare` snapshots
(lib/compare), the stand-alone `taxjson-diff` (bin/taxjson_diff) and
the backup helper every user-file rewrite goes through
(lib/safe_write.backup_copy). One test per finding, each named after
it. Synthetic data only; every temp file in a folder of its own.
"""
import json
import os
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_checklist import _ctx, _project

from taxjson.bin.taxjson_diff import (extract_records, field_diffs,
                                      values_equal)
from taxjson.lib import compare as cmp
from taxjson.lib import safe_write as sw
from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parents[1]


def _snapshot_project(td):
    """A project with reports/ and a cfg, as the checklist tests build
    it: (root, cfg)."""
    root = Path(td)
    _project(root)
    return root, _ctx(root, {}).cfg


def _entries(root):
    return sorted(p.name for p in cmp.snapshots_dir(root).iterdir())


class TestSnapshots(unittest.TestCase):
    def test_a08_saving_a_snapshot_keeps_the_one_named_name_part(self):
        """A08: Saving a snapshot deletes an independent snapshot named
        NAME.part. `baseline.part` is a valid snapshot name; staging
        `baseline` there removed it."""
        with tempfile.TemporaryDirectory() as td:
            root, cfg = _snapshot_project(td)
            self.assertEqual(cmp.check_name("baseline.part", saving=True),
                             "baseline.part")
            cmp.save(root, cfg, "baseline.part")
            self.assertTrue(cmp.snap_dir(root, "baseline.part").is_dir())
            cmp.save(root, cfg, "baseline")
            self.assertTrue(cmp.snap_dir(root, "baseline.part").is_dir())
            self.assertTrue((cmp.snap_dir(root, "baseline.part")
                             / cmp.SNAPSHOT_FILE).is_file())
            self.assertTrue(cmp.snap_dir(root, "baseline").is_dir())
            # No staging folder is left behind, and --list names both.
            self.assertEqual(_entries(root), ["baseline", "baseline.part"])
            self.assertEqual(
                [i["name"] for i in cmp.list_snapshots(root, cfg)],
                ["baseline", "baseline.part"])
            # A staging or holding folder a crash leaves is never listed
            # (its name starts with a dot, which no snapshot name does).
            stale = cmp.snapshots_dir(root) / ".baseline.x1y2z3.part"
            (stale / "reports").mkdir(parents=True)
            (stale / cmp.SNAPSHOT_FILE).write_text(
                (cmp.snap_dir(root, "baseline") / cmp.SNAPSHOT_FILE)
                .read_text())
            self.assertEqual(
                [i["name"] for i in cmp.list_snapshots(root, cfg)],
                ["baseline", "baseline.part"])

    def test_a41_failed_publication_keeps_the_previous_snapshot(self):
        """A41: Failed snapshot publication deletes the previous saved
        snapshot. The earlier snapshot is moved aside and moved back
        when the rename of the new one fails."""
        with tempfile.TemporaryDirectory() as td:
            root, cfg = _snapshot_project(td)
            first = cmp.save(root, cfg, "saved")
            dest = cmp.snap_dir(root, "saved")
            # The next save would hold one more report file.
            (root / "reports" / "extra.sum").write_text("x\n")
            real = Path.rename

            def fake(self, target):
                # The publication rename (staging folder -> the name)
                # fails; the earlier snapshot's moves aside and back
                # work.
                if Path(target) == dest and self.name.startswith("."):
                    raise OSError("synthetic publication failure")
                return real(self, target)

            with patch.object(Path, "rename", autospec=True,
                              side_effect=fake):
                with self.assertRaises(cmp.CompareError) as c:
                    cmp.save(root, cfg, "saved")
            msg = str(c.exception)
            self.assertIn("work/compare/saved/ cannot be replaced", msg)
            self.assertIn("synthetic publication failure", msg)
            self.assertIn("the earlier snapshot is kept", msg)
            # The earlier snapshot is where it was, unchanged, and no
            # staging or holding folder is left behind.
            self.assertTrue(dest.is_dir())
            kept = json.loads((dest / cmp.SNAPSHOT_FILE).read_text())
            self.assertEqual(kept["reports"], first["reports"])
            self.assertEqual(_entries(root), ["saved"])
            # Without the failure the replacement goes through.
            doc = cmp.save(root, cfg, "saved")
            self.assertEqual(len(doc["reports"]), len(first["reports"]) + 1)
            self.assertEqual(_entries(root), ["saved"])

    def test_a41_probe_rename_always_failing_keeps_the_snapshot(self):
        """A41, the audit's probe as written: every rename fails; the
        earlier snapshot is still there and the error says so."""
        with tempfile.TemporaryDirectory() as td:
            root, cfg = _snapshot_project(td)
            cmp.save(root, cfg, "saved")
            with patch.object(Path, "rename",
                              side_effect=OSError("synthetic failure")):
                with self.assertRaises(cmp.CompareError):
                    cmp.save(root, cfg, "saved")
            self.assertTrue(cmp.snap_dir(root, "saved").is_dir())
            self.assertEqual(_entries(root), ["saved"])

    def test_a34_distinct_invalid_bytes_are_a_difference(self):
        """A34: Report comparison collapses distinct invalid-UTF8 or
        binary bytes. Each undecodable byte stays its own character."""
        self.assertNotEqual(cmp.normalized(bytes([255])),
                            cmp.normalized(bytes([254])))
        self.assertEqual(cmp.normalized(bytes([255])),
                         cmp.normalized(bytes([255])))
        self.assertEqual(cmp.normalized("ok\n".encode()), "ok\n")
        self.assertEqual(cmp.normalized("Generated: 2026\nok\n".encode()),
                         "ok\n")
        # The display form is printable text and keeps them apart too.
        self.assertEqual(cmp.normalized(bytes([255]), display=True), "\\xff")
        self.assertNotEqual(cmp.normalized(bytes([255]), display=True),
                            cmp.normalized(bytes([254]), display=True))
        with tempfile.TemporaryDirectory() as td:
            root, cfg = _snapshot_project(td)
            (root / "reports" / "margin.sum").write_bytes(b"x \xff\n")
            cmp.save(root, cfg, "saved")
            (root / "reports" / "margin.sum").write_bytes(b"x \xfe\n")
            doc = cmp.compare(root, cfg, "saved", details=True)
            self.assertTrue(doc["different"])
            self.assertEqual(doc["reports"]["changed"], ["margin.sum"])
            diff = doc["diffs"]["margin.sum"]
            self.assertIn("-x \\xff", diff)
            self.assertIn("+x \\xfe", diff)
            # The rendered view encodes as UTF-8 (no lone surrogates).
            text = "\n".join(cmp.render(doc, details=True))
            text.encode("utf-8")
            self.assertIn("\\xfe", text)

    def test_a48_malformed_snapshot_is_a_compare_error(self):
        """A48: Compare accepts a malformed snapshot then crashes on its
        account table. The loader names the field; --list skips it."""
        with tempfile.TemporaryDirectory() as td:
            root, cfg = _snapshot_project(td)
            cmp.save(root, cfg, "saved")
            p = cmp.snap_dir(root, "saved") / cmp.SNAPSHOT_FILE
            good = json.loads(p.read_text())

            def broken(**fields):
                doc = dict(good)
                doc.update(fields)
                p.write_text(json.dumps(doc))
                with self.assertRaises(cmp.CompareError) as c:
                    cmp.compare(root, cfg, "saved")
                msg = str(c.exception)
                self.assertIn("work/compare/saved/snapshot.json is not a "
                              "snapshot", msg)
                self.assertIn("`taxjson compare --delete saved`", msg)
                # --list leaves it out instead of crashing.
                self.assertEqual(cmp.list_snapshots(root, cfg), [])
                return msg

            msg = broken(accounts=["margin"])
            self.assertIn("accounts is a list, not a table of accounts", msg)
            msg = broken(accounts={"margin": [1, 2]})
            self.assertIn("accounts.margin is a list, not a table of "
                          "figures", msg)
            msg = broken(reports="margin.sum")
            self.assertIn("reports is text, not a list of file names", msg)
            msg = broken(files=[])
            self.assertIn("files is a list, not a table of inputs", msg)
            msg = broken(taken=5)
            self.assertIn("taken is a number, not text", msg)
            # A snapshot taxjson wrote still loads and compares.
            p.write_text(json.dumps(good))
            doc = cmp.compare(root, cfg, "saved")
            self.assertFalse(doc["different"])
            self.assertEqual(
                [i["name"] for i in cmp.list_snapshots(root, cfg)], ["saved"])


def _diff(*args):
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"), NO_COLOR="1")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_diff", list(args)),
        check=False, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=120)


class TestJsonDiff(unittest.TestCase):
    def test_b10_missing_number_is_not_zero(self):
        """B10: JSON diff treats a missing numeric field as an explicit
        zero. An unknown cost beside a cost of 0.0 is a change."""
        self.assertEqual(field_diffs({"cost": None}, {"cost": 0.0}, set()),
                         [("cost", None, 0.0)])
        self.assertEqual(field_diffs({}, {"cost": 0.0}, set()),
                         [("cost", None, 0.0)])
        self.assertFalse(values_equal(None, 0.0))
        self.assertFalse(values_equal(0.0, None))
        self.assertFalse(values_equal("", 0.0))
        self.assertFalse(values_equal(0.0, ""))
        # What stays equal: two missing values, an int and its float,
        # floats within the tolerance; and a real move is still seen.
        self.assertTrue(values_equal(None, ""))
        self.assertTrue(values_equal(None, None))
        self.assertTrue(values_equal(0, 0.0))
        self.assertTrue(values_equal(1.0, 1.0000001))
        self.assertFalse(values_equal(1.0, 1.001))
        self.assertFalse(values_equal(9.4e-7, 5.6e-7))
        with tempfile.TemporaryDirectory() as td:
            row = {"action": "BUYSELL", "date": "2026-01-02",
                   "time": "10:00:00", "symbol": "ZZQ.US", "quantity": -5,
                   "price": 10.0, "account": "margin", "proceeds": 50.0}
            old = Path(td) / "old.json"
            new = Path(td) / "new.json"
            old.write_text(json.dumps({"transactions": [dict(row, cost=None)]}))
            new.write_text(json.dumps({"transactions": [dict(row, cost=0.0)]}))
            r = _diff(str(old), str(new))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("1 modified", r.stdout)
            self.assertIn("- cost: ", r.stdout)
            self.assertIn("+ cost: 0.0000", r.stdout)

    def test_b48_malformed_manual_entries_are_refused(self):
        """B48: JSON diff silently drops malformed manual-reporting
        entries and reports no changes. A section that is not a list,
        or an entry that is not an object, is refused with exit 2."""
        with self.assertRaises(ValueError) as c:
            extract_records({"manual_reporting_required": [7]})
        self.assertIn('"manual_reporting_required"[0] is a number, not an '
                      'object', str(c.exception))
        with self.assertRaises(ValueError) as c:
            extract_records({"transactions": [], "manual_reporting_required":
                             {"a": 1}})
        self.assertIn('"manual_reporting_required" is an object, not a list',
                      str(c.exception))
        with self.assertRaises(ValueError):
            extract_records(["x"])
        # The shapes taxjson writes still read, the section marked.
        recs = extract_records({"transactions": [{"a": 1}],
                                "manual_reporting_required": [{"b": 2}],
                                "other": None})
        self.assertEqual(recs, [{"a": 1}, {"b": 2, "_section":
                                           "manual_reporting_required"}])
        self.assertEqual(extract_records({"transactions": None}), [])
        with tempfile.TemporaryDirectory() as td:
            old = Path(td) / "old.json"
            new = Path(td) / "new.json"
            old.write_text(json.dumps({"manual_reporting_required": [7]}))
            new.write_text(json.dumps({"manual_reporting_required": []}))
            r = _diff(str(old), str(new))
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("taxjson-diff: cannot read input", r.stderr)
            self.assertIn(f'{old}: "manual_reporting_required"[0] is a '
                          f'number, not an object (a record)', r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            # The same files with a well-formed entry: a removal, exit 0.
            old.write_text(json.dumps({"manual_reporting_required": [
                {"action": "BUYSELL", "date": "2026-01-02",
                 "symbol": "ZZQ.US", "quantity": -5, "account": "margin"}]}))
            r = _diff(str(old), str(new))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("1 removed", r.stdout)


class TestBackupCopy(unittest.TestCase):
    def test_d06_concurrent_backups_keep_both_versions(self):
        """D06: Concurrent backup allocation overwrites one of two
        original versions. The name is reserved by an exclusive create,
        so the only window left is between the reservation and the
        write (sw._fill): the second caller then finds the name taken
        and goes on to the next one."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "synthetic.txt"
            src.write_text("version one")
            barrier = threading.Barrier(2)
            ready = threading.Event()
            original = sw._fill

            def fill(fd, data):
                ready.set()
                barrier.wait(timeout=5)
                original(fd, data)

            with patch.object(sw, "_fill", side_effect=fill):
                with ThreadPoolExecutor(2) as pool:
                    a = pool.submit(sw.backup_copy, src)
                    self.assertTrue(ready.wait(timeout=5))
                    src.write_text("version two")
                    b = pool.submit(sw.backup_copy, src)
                    paths = [a.result(timeout=10), b.result(timeout=10)]
            self.assertNotEqual(paths[0], paths[1])
            baks = sorted(root.glob("synthetic.txt.bak*"))
            self.assertEqual([p.name for p in baks],
                             ["synthetic.txt.bak", "synthetic.txt.bak1"])
            self.assertEqual(sorted(p.read_text() for p in baks),
                             ["version one", "version two"])
            # The helper's other promises hold: an identical backup is
            # reused, a link at a .bak name is never written through,
            # and the copy is owner-only.
            self.assertEqual(sw.backup_copy(src), paths[1])
            victim = root / "victim.txt"
            victim.write_text("keep me")
            other = root / "other.txt"
            other.write_text("o")
            (root / "other.txt.bak").symlink_to(victim)
            bak = sw.backup_copy(other)
            self.assertEqual(bak.name, "other.txt.bak1")
            self.assertEqual(victim.read_text(), "keep me")
            self.assertEqual(bak.read_text(), "o")
            self.assertEqual(bak.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
